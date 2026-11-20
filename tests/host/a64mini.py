# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A small AArch64 interpreter for the FEX exit stubs (round 670), used by tests/host/fexstub_emu.py where Python's unicorn
module is missing (the macOS build runner, this Mac): the stubs' checks then still run them instead of skipping.

It runs exactly the instructions the ARM64EC exit and return stubs of Module.S use, each decoded from its machine word:
ADR/ADRP, ADD/SUB (immediate, shifted register; with flags), AND/ORR/EOR/ANDS (immediate, shifted register), MOVZ/MOVN/MOVK,
UBFM/SBFM, LDR/STR/LDRB/STRB/LDRH/STRH and the signed loads (unsigned offset, unscaled, pre- and post-index, register
offset), LDADD/STADD and the other LSE fetch-and-op forms, B, BL, B.cond, CBZ/CBNZ, TBZ/TBNZ, BR/BLR/RET, MRS/MSR of FPCR,
NZCV and TPIDRRO_EL0, NOP. Any other word raises Unsupported: the interpreter never guesses. Its own correctness is checked by
tests/host/check-a64mini.py (every encoding against the assembler's own words and hand-computed results) and by the lean
exit's check, whose two stubs it runs to the same end (they are proved on devices).
"""
import struct


class Unsupported(Exception):
    pass


MASK64 = (1 << 64) - 1
SYSREGS = {(3, 3, 4, 4, 0): 'fpcr', (3, 3, 13, 0, 3): 'tpidrro', (3, 3, 4, 2, 0): 'nzcv', (3, 3, 13, 0, 2): 'tpidr'}


def sext(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def decode_bit_masks(n, imms, immr, size):
    combined = (n << 6) | (~imms & 0x3f)
    length = combined.bit_length() - 1
    if length < 1:
        raise Unsupported('reserved logical immediate')
    levels = (1 << length) - 1
    s, r = imms & levels, immr & levels
    if s == levels:
        raise Unsupported('reserved logical immediate')
    esize = 1 << length
    welem = (1 << (s + 1)) - 1
    elem = ((welem >> r) | (welem << (esize - r))) & ((1 << esize) - 1)
    mask = 0
    for i in range(size // esize):
        mask |= elem << (i * esize)
    return mask


class Machine:
    def __init__(self, regions, regs, sp, nzcv, fpcr=0, tpidrro=0):
        self.regions = regions            # [(base, bytearray)]
        self.x = list(regs[:31])
        self.sp = sp
        self.nzcv = nzcv
        self.sys = {'fpcr': fpcr, 'tpidrro': tpidrro, 'tpidr': 0}

    # memory
    def _where(self, addr, size):
        for base, blob in self.regions:
            if base <= addr and addr + size <= base + len(blob):
                return blob, addr - base
        raise Unsupported(f'memory access at {addr:#x} ({size} bytes) outside every region')

    def load(self, addr, size):
        blob, off = self._where(addr, size)
        return int.from_bytes(blob[off:off + size], 'little')

    def store(self, addr, size, value):
        blob, off = self._where(addr, size)
        blob[off:off + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little')

    # registers: 31 is SP or ZR depending on the operand
    def get(self, r, sf, sp=False):
        if r == 31:
            v = self.sp if sp else 0
        else:
            v = self.x[r]
        return v if sf else v & 0xffffffff

    def put(self, r, value, sf, sp=False):
        value &= MASK64 if sf else 0xffffffff   # a W write zero-extends
        if r == 31:
            if sp:
                self.sp = value
        else:
            self.x[r] = value

    def flags(self, n, z, c, v):
        self.nzcv = (n << 31) | (z << 30) | (c << 29) | (v << 28)

    def cond(self, c):
        n, z, cf, v = (self.nzcv >> 31) & 1, (self.nzcv >> 30) & 1, (self.nzcv >> 29) & 1, (self.nzcv >> 28) & 1
        base = c >> 1
        result = (z == 1, cf == 1, n == 1, v == 1, cf == 1 and z == 0, n == v, n == v and z == 0, True)[base]
        if c & 1 and c != 15:
            result = not result
        return result

    def addsub(self, a, b, sub, sf, setflags):
        bits = 64 if sf else 32
        mask = (1 << bits) - 1
        if sub:
            b = (~b) & mask
            carry_in = 1
        else:
            carry_in = 0
        full = a + b + carry_in
        result = full & mask
        if setflags:
            n = result >> (bits - 1)
            z = int(result == 0)
            c = int(full >> bits)
            sa, sb, sr = a >> (bits - 1), b >> (bits - 1), result >> (bits - 1)
            v = int(sa == sb and sr != sa)
            self.flags(n, z, c, v)
        return result

    def shift(self, value, kind, amount, sf):
        bits = 64 if sf else 32
        mask = (1 << bits) - 1
        value &= mask
        if kind == 0:
            return (value << amount) & mask
        if kind == 1:
            return value >> amount
        if kind == 2:
            return (sext(value, bits) >> amount) & mask
        return ((value >> amount) | (value << (bits - amount))) & mask if amount else value

    def step(self, pc):
        """Execute the word at pc; returns the next pc."""
        insn = self.load(pc, 4)
        nxt = pc + 4
        # PC-relative addressing
        if insn & 0x1f000000 == 0x10000000:
            imm = sext(((insn >> 5) & 0x7ffff) << 2 | (insn >> 29) & 3, 21)
            rd = insn & 31
            if insn >> 31:
                self.put(rd, ((pc & ~0xfff) + (imm << 12)) & MASK64, 1)
            else:
                self.put(rd, (pc + imm) & MASK64, 1)
            return nxt
        # Add/subtract (immediate)
        if insn & 0x1f800000 == 0x11000000:
            sf, sub, s = insn >> 31, (insn >> 30) & 1, (insn >> 29) & 1
            imm = ((insn >> 10) & 0xfff) << (12 if (insn >> 22) & 1 else 0)
            rn, rd = (insn >> 5) & 31, insn & 31
            result = self.addsub(self.get(rn, sf, sp=True), imm, sub, sf, s)
            self.put(rd, result, sf, sp=not s)
            return nxt
        # Logical (immediate)
        if insn & 0x1f800000 == 0x12000000:
            sf, opc, n = insn >> 31, (insn >> 29) & 3, (insn >> 22) & 1
            if not sf and n:
                raise Unsupported(f'{insn:#010x}')
            imm = decode_bit_masks(n, (insn >> 10) & 0x3f, (insn >> 16) & 0x3f, 64 if sf else 32)
            rn, rd = (insn >> 5) & 31, insn & 31
            a = self.get(rn, sf)
            result = (a & imm, a | imm, a ^ imm, a & imm)[opc]
            if opc == 3:
                bits = 64 if sf else 32
                self.flags(result >> (bits - 1), int(result == 0), 0, 0)
            self.put(rd, result, sf, sp=opc != 3)
            return nxt
        # Move wide (immediate)
        if insn & 0x1f800000 == 0x12800000:
            sf, opc, hw = insn >> 31, (insn >> 29) & 3, (insn >> 21) & 3
            imm, rd = (insn >> 5) & 0xffff, insn & 31
            if opc == 1 or (not sf and hw > 1):
                raise Unsupported(f'{insn:#010x}')
            if opc == 0:
                value = ~(imm << (16 * hw))
            elif opc == 2:
                value = imm << (16 * hw)
            else:
                value = (self.get(rd, sf) & ~(0xffff << (16 * hw))) | (imm << (16 * hw))
            self.put(rd, value, sf)
            return nxt
        # Bitfield
        if insn & 0x1f800000 == 0x13000000:
            sf, opc = insn >> 31, (insn >> 29) & 3
            immr, imms = (insn >> 16) & 0x3f, (insn >> 10) & 0x3f
            rn, rd = (insn >> 5) & 31, insn & 31
            bits = 64 if sf else 32
            if opc not in (0, 2) or ((insn >> 22) & 1) != sf:
                raise Unsupported(f'{insn:#010x}')
            src = self.get(rn, sf)
            if imms >= immr:
                width = imms - immr + 1
                field = (src >> immr) & ((1 << width) - 1)
                if opc == 0:
                    field = sext(field, width) & ((1 << bits) - 1)
            else:
                width = imms + 1
                field = (src & ((1 << width) - 1)) << (bits - immr)
                if opc == 0:
                    field = (sext(src & ((1 << width) - 1), width) << (bits - immr)) & ((1 << bits) - 1)
            self.put(rd, field, sf)
            return nxt
        # Add/subtract (shifted register)
        if insn & 0x1f200000 == 0x0b000000:
            sf, sub, s = insn >> 31, (insn >> 30) & 1, (insn >> 29) & 1
            kind, amount = (insn >> 22) & 3, (insn >> 10) & 0x3f
            rm, rn, rd = (insn >> 16) & 31, (insn >> 5) & 31, insn & 31
            if kind == 3:
                raise Unsupported(f'{insn:#010x}')
            b = self.shift(self.get(rm, sf), kind, amount, sf)
            self.put(rd, self.addsub(self.get(rn, sf), b, sub, sf, s), sf)
            return nxt
        # Logical (shifted register)
        if insn & 0x1f000000 == 0x0a000000:
            sf, opc, neg = insn >> 31, (insn >> 29) & 3, (insn >> 21) & 1
            kind, amount = (insn >> 22) & 3, (insn >> 10) & 0x3f
            rm, rn, rd = (insn >> 16) & 31, (insn >> 5) & 31, insn & 31
            bits = 64 if sf else 32
            b = self.shift(self.get(rm, sf), kind, amount, sf)
            if neg:
                b = ~b & ((1 << bits) - 1)
            a = self.get(rn, sf)
            result = (a & b, a | b, a ^ b, a & b)[opc]
            if opc == 3:
                self.flags(result >> (bits - 1), int(result == 0), 0, 0)
            self.put(rd, result, sf)
            return nxt
        # Data processing (2 source): LSLV, LSRV, ASRV, RORV
        if insn & 0x7fe0f000 == 0x1ac02000:
            sf, kind = insn >> 31, (insn >> 10) & 3
            rm, rn, rd = (insn >> 16) & 31, (insn >> 5) & 31, insn & 31
            bits = 64 if sf else 32
            self.put(rd, self.shift(self.get(rn, sf), kind, self.get(rm, sf) % bits, sf), sf)
            return nxt
        # Load-acquire / store-release register (LDAR, STLR and their byte/halfword forms)
        if insn & 0x3fbffc00 == 0x089ffc00:
            size, load, rn, rt = insn >> 30, (insn >> 22) & 1, (insn >> 5) & 31, insn & 31
            addr = self.get(rn, 1, sp=True)
            if load:
                self.put(rt, self.load(addr, 1 << size), 1)
            else:
                self.store(addr, 1 << size, self.get(rt, 1))
            return nxt
        # LSE atomic memory operations (LDADD, LDCLR, LDEOR, LDSET; STADD... are these with Rt = ZR)
        if insn & 0x3f208c00 == 0x38200000 and (insn >> 26) & 1 == 0:
            size, rs, opc, rn, rt = insn >> 30, (insn >> 16) & 31, (insn >> 12) & 7, (insn >> 5) & 31, insn & 31
            if opc > 3:
                raise Unsupported(f'{insn:#010x}')
            nbytes = 1 << size
            addr = self.get(rn, 1, sp=True)
            old = self.load(addr, nbytes)
            operand = self.get(rs, 1) & ((1 << (8 * nbytes)) - 1)
            new = (old + operand, old & ~operand, old ^ operand, old | operand)[opc]
            self.store(addr, nbytes, new)
            self.put(rt, old, size == 3)
            return nxt
        # Loads and stores (integer registers)
        if insn & 0x3a000000 == 0x38000000 and (insn >> 26) & 1 == 0:
            size, opc = insn >> 30, (insn >> 22) & 3
            rn, rt = (insn >> 5) & 31, insn & 31
            base = self.get(rn, 1, sp=True)
            writeback = None
            if insn & 0x01000000:                       # unsigned offset
                addr = base + (((insn >> 10) & 0xfff) << size)
            elif insn & 0x00200000:                     # register offset
                if (insn >> 10) & 3 != 2:
                    raise Unsupported(f'{insn:#010x}')
                option, scaled, rm = (insn >> 13) & 7, (insn >> 12) & 1, (insn >> 16) & 31
                off = self.get(rm, 1)
                if option == 2:
                    off &= 0xffffffff
                elif option == 6:
                    off = sext(off, 32)
                elif option not in (3, 7):
                    raise Unsupported(f'{insn:#010x}')
                addr = base + ((off << size) if scaled else off)
            else:
                imm = sext((insn >> 12) & 0x1ff, 9)
                mode = (insn >> 10) & 3
                if mode == 0:
                    addr = base + imm
                elif mode == 1:
                    addr, writeback = base, base + imm
                elif mode == 3:
                    addr = writeback = base + imm
                else:
                    raise Unsupported(f'{insn:#010x}')
            addr &= MASK64
            nbytes = 1 << size
            if opc == 0:
                self.store(addr, nbytes, self.get(rt, 1))
            elif opc == 1:
                self.put(rt, self.load(addr, nbytes), 1)
            else:
                if size == 3 or (size == 2 and opc == 3):
                    raise Unsupported(f'{insn:#010x}')
                value = sext(self.load(addr, nbytes), 8 * nbytes)
                self.put(rt, value, opc == 2)
            if writeback is not None:
                self.put(rn, writeback, 1, sp=True)
            return nxt
        # Branches
        if insn & 0x7c000000 == 0x14000000:
            if insn >> 31:
                self.x[30] = nxt
            return (pc + (sext(insn & 0x3ffffff, 26) << 2)) & MASK64
        if insn & 0xff000010 == 0x54000000:
            return (pc + (sext((insn >> 5) & 0x7ffff, 19) << 2)) & MASK64 if self.cond(insn & 15) else nxt
        if insn & 0x7e000000 == 0x34000000:
            sf, nz, rt = insn >> 31, (insn >> 24) & 1, insn & 31
            zero = self.get(rt, sf) == 0
            return (pc + (sext((insn >> 5) & 0x7ffff, 19) << 2)) & MASK64 if zero != bool(nz) else nxt
        if insn & 0x7e000000 == 0x36000000:
            bit = ((insn >> 31) << 5) | ((insn >> 19) & 31)
            nz, rt = (insn >> 24) & 1, insn & 31
            set_ = (self.get(rt, 1) >> bit) & 1
            return (pc + (sext((insn >> 5) & 0x3fff, 14) << 2)) & MASK64 if set_ == nz else nxt
        if insn & 0xff9ffc1f == 0xd61f0000:          # BR, BLR
            target = self.get((insn >> 5) & 31, 1)
            if (insn >> 21) & 3 == 1:
                self.x[30] = nxt
            return target
        if insn & 0xfffffc1f == 0xd65f0000:          # RET
            return self.get((insn >> 5) & 31, 1)
        # System registers
        if insn & 0xffd00000 == 0xd5100000:
            read = (insn >> 21) & 1
            key = (2 + ((insn >> 19) & 1), (insn >> 16) & 7, (insn >> 12) & 15, (insn >> 8) & 15, (insn >> 5) & 7)
            if key not in SYSREGS:
                raise Unsupported(f'system register {key}')
            name, rt = SYSREGS[key], insn & 31
            if read:
                self.put(rt, self.nzcv if name == 'nzcv' else self.sys[name], 1)
            elif name == 'nzcv':
                self.nzcv = self.get(rt, 1) & 0xf0000000
            else:
                self.sys[name] = self.get(rt, 1)
            return nxt
        if insn & 0xfffff01f == 0xd503201f:          # hints (NOP, YIELD ...): nothing to do here
            return nxt
        if insn & 0xfffff09f == 0xd503309f:          # DSB, DMB, ISB: one interpreter thread, already in order
            return nxt
        raise Unsupported(f'{insn:#010x} at {pc:#x}')


def run(regions, entry, regs, sp, stops, nzcv=0x20000000, fpcr=0, tpidrro=0, count=2000):
    """Run from `entry` until a stop address (not executed); regions [(base, bytearray)] are changed in place.
    Returns (stop pc, x0..x30 + [sp], nzcv, fpcr, steps)."""
    m = Machine(regions, regs, sp, nzcv, fpcr, tpidrro)
    pc, steps = entry, 0
    while pc not in stops:
        if steps >= count:
            raise Unsupported(f'no stop after {count} instructions (pc {pc:#x})')
        pc = m.step(pc)
        steps += 1
    return pc, m.x + [m.sp], m.nzcv, m.sys['fpcr'], steps
