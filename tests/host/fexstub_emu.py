"""Helpers for the FEX overlay checks that run Module.S under emulation: assemble
the patched ARM64EC Module.S as an AArch64 ELF object with the host clang, link
its .text in memory, and run a stub with Python's unicorn module, or, where it is
not installed, with tests/host/a64mini.py (round 670). The clang is optional;
`missing()` names what is absent so a check can skip only its emulation part.
"""
from pathlib import Path
import os
import shutil
import struct
import subprocess
import tempfile

try:
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn import arm64_const as A64
except ImportError:
    Uc = None

# Fixed addresses of the emulated process.
TEXT, STUBS, DATA, FRAME, CPUAREA, TEB, TSD, STACK, JIT, NATIVE = (
    0x10000000, 0x10100000, 0x20000000, 0x30000000, 0x31000000, 0x32000000, 0x33000000, 0x40000000, 0x50000000,
    0x60000000)
REGIONS = ((TEXT, 0x100000), (STUBS, 0x1000), (DATA, 0x100000), (FRAME, 0x2000), (CPUAREA, 0x1000), (TEB, 0x2000),
           (TSD, 0x1000), (STACK, 0x10000), (JIT, 0x400000), (NATIVE, 0x10000))
SIZES = {'IosXpFex': 4496 * 8, 'IosAliasEntries': 256 * 32}


# Round 670: without unicorn the stubs run on tests/host/a64mini.py (checked by tests/host/check-a64mini.py), so the checks
# no longer skip their emulation on the macOS runner; FEXSTUB_ENGINE=mini uses it even where unicorn is installed.
ENGINE = os.environ.get('FEXSTUB_ENGINE', '')


def missing():
    """None when the emulation can run, else what is missing."""
    if not shutil.which(os.environ.get('CLANG', 'clang')):
        return 'an AArch64 clang'
    return None


def assemble(asm):
    """The FEX_IOS_HOST Module.S text as a relocatable AArch64 ELF object."""
    with tempfile.TemporaryDirectory() as tmp:
        src, obj = Path(tmp) / 'Module.S', Path(tmp) / 'Module.o'
        src.write_text(asm)
        subprocess.run([shutil.which(os.environ.get('CLANG', 'clang')), '--target=aarch64-linux-gnu', '-DFEX_IOS_HOST=1',
                        '-c', str(src), '-o', str(obj)], check=True)
        return obj.read_bytes()


def elf_sections(data):
    assert data[:4] == b'\x7fELF' and data[4] == 2 and data[5] == 1 and struct.unpack_from('<H', data, 18)[0] == 183
    shoff, = struct.unpack_from('<Q', data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x3a)
    secs = [struct.unpack_from('<IIQQQQIIQQ', data, shoff + i * shentsize) for i in range(shnum)]
    strs = lambda sec, off: data[secs[sec][4] + off:data.index(b'\0', secs[sec][4] + off)].decode()
    return secs, [strs(shstrndx, s[0]) for s in secs], strs


def link(obj):
    """Link .text at TEXT and .data at the start of DATA; undefined data symbols get DATA slots and
    undefined branch targets `brk` stubs. Returns (text bytes, data bytes, {symbol: address}, {data symbol: address})."""
    secs, names, strs = elf_sections(obj)
    text = names.index('.text')
    data_sec = names.index('.data') if '.data' in names else None
    symtab = next(i for i, s in enumerate(secs) if s[1] == 2)
    syms = []
    for i in range(secs[symtab][5] // 24):
        name, info, _, shndx, value, _ = struct.unpack_from('<IBBHQQ', obj, secs[symtab][4] + i * 24)
        syms.append((strs(secs[symtab][6], name) if name else '', shndx, value))
    body = bytearray(obj[secs[text][4]:secs[text][4] + secs[text][5]])
    data = bytearray(obj[secs[data_sec][4]:secs[data_sec][4] + secs[data_sec][5]]) if data_sec is not None else bytearray()
    base = {text: TEXT}
    if data_sec is not None:
        base[data_sec] = DATA
    data_at, stub_at, cursor = {}, {}, (DATA + len(data) + 63) & ~63
    for s in secs:
        if s[1] != 4 or s[7] != text:
            continue
        for k in range(s[5] // 24):
            off, info, addend = struct.unpack_from('<QQq', obj, s[4] + k * 24)
            name, shndx, value = syms[info >> 32]
            rtype = info & 0xffffffff
            if shndx in base:
                S = base[shndx] + value
            elif shndx != 0:
                raise AssertionError(f'relocation against section {names[shndx]}')
            elif rtype in (282, 283):
                S = stub_at.setdefault(name, STUBS + 4 * len(stub_at))
            else:
                if name not in data_at:
                    data_at[name] = cursor
                    cursor = (cursor + SIZES.get(name, 8) + 63) & ~63
                S = data_at[name]
            P, X = TEXT + off, S + addend
            insn = struct.unpack_from('<I', body, off)[0]
            if rtype == 275:  # ADR_PREL_PG_HI21
                imm = ((X & ~0xfff) - (P & ~0xfff)) >> 12
                insn = (insn & ~((3 << 29) | (0x7ffff << 5))) | ((imm & 3) << 29) | (((imm >> 2) & 0x7ffff) << 5)
            elif rtype in (277, 278, 284, 285, 286, 299):  # ADD/LDST{8,16,32,64,128}_ABS_LO12_NC
                shift = {277: 0, 278: 0, 284: 1, 285: 2, 286: 3, 299: 4}[rtype]
                assert rtype == 277 or X % (1 << shift) == 0, f'misaligned {name}'
                insn = (insn & ~(0xfff << 10)) | ((((X & 0xfff) >> shift) & 0xfff) << 10)
            elif rtype in (282, 283):  # JUMP26/CALL26
                insn = (insn & ~0x3ffffff) | (((X - P) >> 2) & 0x3ffffff)
            elif rtype == 280:  # CONDBR19
                insn = (insn & ~(0x7ffff << 5)) | ((((X - P) >> 2) & 0x7ffff) << 5)
            elif rtype == 279:  # TSTBR14
                insn = (insn & ~(0x3fff << 5)) | ((((X - P) >> 2) & 0x3fff) << 5)
            else:
                raise AssertionError(f'relocation type {rtype}')
            struct.pack_into('<I', body, off, insn)
    symbols = {name: base[shndx] + value for name, shndx, value in syms if name and shndx in base}
    data_at.update({name: addr for name, addr in symbols.items() if DATA <= addr < DATA + 0x100000})
    return bytes(body), bytes(data), symbols, data_at


def blank_memory():
    return {base: bytearray(size) for base, size in REGIONS if base not in (TEXT, STUBS)}


def run_mini(code, data, entry, state, stops, count=2000):
    """run() on tests/host/a64mini.py: the same regions, initial state and results."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('a64mini', Path(__file__).resolve().parent / 'a64mini.py')
    mini = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mini)
    mem = {base: bytearray(size) for base, size in REGIONS}
    mem[TEXT][:len(code)] = code
    mem[STUBS][:] = struct.pack('<I', 0xd4200020) * (len(mem[STUBS]) // 4)  # brk #1
    for base, blob in state['mem'].items():
        mem[base][:len(blob)] = blob
    if data and not any(base == DATA for base in state['mem']):
        mem[DATA][:len(data)] = data
    pc, regs, nzcv, _, steps = mini.run(list(mem.items()), entry, state['regs'], state['sp'], stops,
                                        nzcv=state.get('nzcv', 0x20000000), fpcr=0, tpidrro=state.get('tpidrro', TSD), count=count)
    out = {base: bytearray(mem[base][:len(blob)]) for base, blob in state['mem'].items()}
    return pc, regs, nzcv, out, steps


def run(code, data, entry, state, stops, count=2000, engine=None):
    """Run from `entry` with state {'mem': {base: bytearray}, 'regs': [x0..x30], 'sp': sp, 'tpidrro': ..., 'nzcv': ...}
    until a stop address; returns (stop, regs + [sp], nzcv, memory, steps)."""
    if (engine or ENGINE) == 'mini' or Uc is None:
        return run_mini(code, data, entry, state, stops, count)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.ctl_set_cpu_model(A64.UC_CPU_ARM64_MAX)  # LSE
    for base, size in REGIONS:
        uc.mem_map(base, size)
    uc.mem_write(TEXT, code)
    uc.mem_write(STUBS, struct.pack('<I', 0xd4200020) * 1024)  # brk #1
    for base, blob in state['mem'].items():
        uc.mem_write(base, bytes(blob))
    if data and not any(base == DATA for base in state['mem']):
        uc.mem_write(DATA, data)
    for r in range(29):
        uc.reg_write(A64.UC_ARM64_REG_X0 + r, state['regs'][r])
    uc.reg_write(A64.UC_ARM64_REG_X29, state['regs'][29])
    uc.reg_write(A64.UC_ARM64_REG_X30, state['regs'][30])
    uc.reg_write(A64.UC_ARM64_REG_SP, state['sp'])
    uc.reg_write(A64.UC_ARM64_REG_NZCV, state.get('nzcv', 0x20000000))
    uc.reg_write(A64.UC_ARM64_REG_FPCR, 0)
    uc.reg_write(A64.UC_ARM64_REG_TPIDRRO_EL0, state.get('tpidrro', TSD))
    steps = [0]

    def hook(uc, address, size, user):
        if address in stops:
            uc.emu_stop()
        else:
            steps[0] += 1

    uc.hook_add(UC_HOOK_CODE, hook)
    uc.emu_start(entry, 0, count=count)
    pc = uc.reg_read(A64.UC_ARM64_REG_PC)
    assert pc in stops, f'stopped at {pc:#x}'
    regs = [uc.reg_read(A64.UC_ARM64_REG_X0 + r) for r in range(29)] + \
           [uc.reg_read(A64.UC_ARM64_REG_X29), uc.reg_read(A64.UC_ARM64_REG_X30), uc.reg_read(A64.UC_ARM64_REG_SP)]
    mem = {base: bytearray(uc.mem_read(base, len(blob))) for base, blob in state['mem'].items()}
    return pc, regs, uc.reg_read(A64.UC_ARM64_REG_NZCV), mem, steps[0]
