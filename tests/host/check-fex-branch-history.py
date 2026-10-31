#!/usr/bin/env python3
"""Run the production recorder's ARM64 words under Unicorn; no Wine/game runs.

Requires a C++20 compiler, the pinned FEX fmt headers and Python unicorn.
The fixture supplies operands to the added emitter block and uses FEX's actual
ARM64 emitter and patched frame layout. Unrelated registers, flags and frame
bytes must remain unchanged, including after ring wrap and concurrent frames.
"""
from pathlib import Path
import importlib.util
import json
import os
import struct
import subprocess
import tempfile

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X28, UC_ARM64_REG_NZCV, UC_ARM64_REG_SP

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('overlay', root / 'tools/patch-fex-ios-branch-history.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)
sources = {name: (root / 'FEX' / name).read_text() for name in overlay.EDITS}
# Since the 2026-10-15 merge the FEX pin already carries this overlay inside its combined files (the later ec-bitmap-cache overlay
# adds a static_assert to the same block), where the script's own check ("partial or changed overlay") stops; build-xtajit64.sh does
# not run the script on such a pin. Then the pin's files are the patched files and the script's behaviour is not under test here.
already = all(overlay.MARKER in source for source in sources.values())
patched = sources if already else overlay.patch(sources)
if not already:
    assert overlay.patch(patched) == patched
    for name, edits in overlay.EDITS.items():
        changed = dict(sources)
        changed[name] = changed[name].replace(edits[0][0], 'changed-anchor', 1)
        try:
            overlay.patch(changed)
        except ValueError:
            pass
        else:
            raise AssertionError('drift accepted: ' + name)
        changed = dict(patched)
        changed[name] = changed[name].replace(edits[0][1], overlay.MARKER, 1)
        try:
            overlay.patch(changed)
        except ValueError:
            pass
        else:
            raise AssertionError('partial overlay accepted: ' + name)
else:
    # What the compiled fixture below uses must be what the pin ships: the emitter block and the module block verbatim.
    assert overlay.BRANCH_ADD in patched[overlay.BRANCH], 'BranchOps.cpp no longer carries the recorder block the fixture compiles'
    assert overlay.MODULE_ADD in patched[overlay.MODULE], 'Module.cpp no longer carries the layout export the fixture compiles'
    assert overlay.FRAME_ADD in patched[overlay.FRAME], 'CoreState.h no longer carries the history struct the fixture compiles'

script = (root / 'tools/build-xtajit64.sh').read_text()
assert script.index('patch-fex-ios-rpmalloc-span8.py') < script.index('patch-fex-ios-branch-history.py')
assert all(name in script for name in overlay.EDITS)
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
assert "b'branch-history-v1 madeira-bcd' not in module" in workflow
assert "b'IosGuestBranchTraceLayout' not in module" in workflow

prefix = r'''
#include <FEXCore/Core/CoreState.h>
#include <CodeEmitter/Emitter.h>
#include <cstdio>
#include <cstdlib>
#include <vector>
using Frame = FEXCore::Core::CpuStateFrame;
namespace FEXCore::Core { bool IosGuestBranchTraceEnabled = true; }
enum class BranchHint { None, Call, Return, CheckTF };
struct Operand { unsigned kind; uint64_t value; };
struct OpType { Operand NewRIP; BranchHint Hint; };
struct Debug { struct Mapping { uint64_t GuestEntryOffset; }; std::vector<Mapping> GuestOpcodes; };
struct Reg { ARMEmitter::XRegister X() const { return ARMEmitter::XReg::x4; } };
struct Fixture : ARMEmitter::Emitter {
  uint64_t Entry = 0x125d90000ULL;
  Debug debug {{{0x258cf5}}};
  Debug* DebugData = &debug;
  static constexpr auto STATE = ARMEmitter::XReg::x28;
  static constexpr auto TMP1 = ARMEmitter::XReg::x10;
  static constexpr auto TMP2 = ARMEmitter::XReg::x11;
  static constexpr auto TMP3 = ARMEmitter::XReg::x12;
  Fixture(uint8_t* p, size_t n) : Emitter(p,n) {}
  bool IsInlineConstant(Operand v, uint64_t* target) { *target=v.value; return v.kind==0; }
  bool IsInlineEntrypointOffset(Operand v, uint64_t* target) { *target=Entry+v.value; return v.kind==1; }
  Reg GetReg(Operand) { return {}; }
  void InsertGuestRIPMove(ARMEmitter::Register reg, uint64_t value) {
    movz(ARMEmitter::Size::i64Bit, reg, value & 0xffff);
    for (unsigned shift=16;shift<64;shift+=16)
      movk(ARMEmitter::Size::i64Bit, reg, (value>>shift)&0xffff, shift);
  }
  void record(OpType* Op) {
'''
suffix = r'''
  }
};
int main(int argc,char** argv) {
  if (argc!=4) return 1;
  static_assert(offsetof(Frame, State.gregs[FEXCore::X86State::REG_RSP]) == 0x40);
  Frame initial {};
  if (initial.IosGuestBranchHistory.Magic != 0x314744454742444dULL || initial.IosGuestBranchHistory.Serial) return 2;
  if (IosGuestBranchTraceLayout[0] != 0x314744454742444dULL ||
      static_cast<uint32_t>(IosGuestBranchTraceLayout[1]) != offsetof(Frame,IosGuestBranchHistory) ||
      (IosGuestBranchTraceLayout[1]>>32) != sizeof(Frame) ||
      IosGuestBranchTraceLayout[2] != (272 | (8ULL<<32))) return 3;
  unsigned kind=std::strtoul(argv[1],nullptr,0);
  FEXCore::Core::IosGuestBranchTraceEnabled = kind!=3;
  uint8_t code[4096] {};
  Fixture fixture(code,sizeof(code));
  OpType op {{kind, kind==1 ? 0x258cf6ULL : 0x125fe8cf6ULL}, static_cast<BranchHint>(std::strtoul(argv[3],nullptr,0))};
  fixture.record(&op);
  FILE* file=fopen(argv[2],"wb");
  fwrite(code,1,fixture.GetCursorOffset(),file); fclose(file);
  printf("{\"history\":%zu,\"size\":%zu,\"state\":%zu,\"callback\":%zu,\"bytes\":%zu}\n",
    offsetof(Frame,IosGuestBranchHistory),sizeof(Frame),sizeof(FEXCore::Core::CPUState),
    offsetof(Frame,IosLastCallbackLR),fixture.GetCursorOffset());
}
'''
prefix = prefix.replace('namespace FEXCore::Core { bool IosGuestBranchTraceEnabled = true; }', overlay.MODULE_ADD)


def verify(code, layout, kind, hint):
    address, base = 0x100000, 0x200000
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(address, 0x10000)
    uc.mem_map(base, 0x20000)
    uc.mem_write(address, code)
    history = layout['history']
    expected = []
    for turn in range(19):
        owner_base = base + (turn % 2) * 0x10000
        if turn < 2:
            frame = bytearray([0xA5] * layout['size'])
            frame[history:history+272] = struct.pack('<Q',0x314744454742444d)+bytes(272-8)
            uc.mem_write(owner_base, bytes(frame))
        previous = bytes(uc.mem_read(owner_base, layout['size']))
        serial = struct.unpack_from('<Q', previous, history+8)[0]
        block = 0x1383d15e0 + turn * 0x100
        uc.mem_write(owner_base, struct.pack('<Q', block))
        for reg in range(29):
            uc.reg_write(UC_ARM64_REG_X0 + reg, 0xABC000000 + reg)
        uc.reg_write(UC_ARM64_REG_X28, owner_base)
        target = 0x125fe8cf6 + turn
        uc.reg_write(UC_ARM64_REG_X0 + 4, target)
        uc.reg_write(UC_ARM64_REG_SP, 0x300000)
        uc.reg_write(UC_ARM64_REG_NZCV, 0xB0000000)
        registers = [uc.reg_read(UC_ARM64_REG_X0+i) for i in range(29)]
        uc.emu_start(address, address+len(code))
        result = bytes(uc.mem_read(owner_base, layout['size']))
        assert result[:history] == struct.pack('<Q', block)+previous[8:history]
        assert result[history+272:] == previous[history+272:]
        assert uc.reg_read(UC_ARM64_REG_NZCV) == 0xB0000000
        assert uc.reg_read(UC_ARM64_REG_SP) == 0x300000
        for reg in range(29):
            if reg not in (10,11,12):
                assert uc.reg_read(UC_ARM64_REG_X0+reg) == registers[reg]
        assert struct.unpack_from('<Q',result,history+8)[0] == serial+1
        source, dest, owner_block, actual_hint = struct.unpack_from('<4Q',result,history+16+(serial&7)*32)
        assert (source,dest,owner_block) == (0x125fe8cf5, target if kind==2 else 0x125fe8cf6, block)
        assert actual_hint == hint
        expected.append((owner_base, serial, dest))
    assert expected[-1][1] == 9  # ring wrapped independently in each thread frame


with tempfile.TemporaryDirectory(prefix='fex-branch-history-') as directory:
    folder = Path(directory)
    header = folder / 'FEXCore/Core/CoreState.h'
    header.parent.mkdir(parents=True)
    header.write_text(patched[overlay.FRAME])
    unit = folder / 'fixture.cpp'
    binary = folder / 'fixture'
    unit.write_text(prefix + overlay.BRANCH_ADD + suffix)
    cxx = os.environ.get('CXX','c++')
    command = [cxx,'-std=c++20','-O1','-g','-DARCHITECTURE_arm64ec','-DFEX_IOS_HOST=1',
               '-fsanitize=address,undefined','-fno-sanitize-recover=all',
               '-I',str(folder),'-I',str(root/'FEX/FEXCore/include'),
               '-I',str(root/'FEX/CodeEmitter'),'-I',str(root/'FEX/FEXHeaderUtils'),
               '-I',str(root/'FEX/External/fmt/include'),str(unit),'-o',str(binary)]
    subprocess.run(command,check=True)
    for kind in range(4):
        for hint in range(4):
            path = folder / 'words.bin'
            layout = json.loads(subprocess.check_output([str(binary),str(kind),str(path),str(hint)],text=True))
            assert layout['state']==1472 and layout['callback']==3792  # pinned old ABI stays fixed
            assert layout['history']==3800 and layout['size']>=3800+272 and layout['size']%8==0   # the frame has grown (later overlays append fields); the recorder only needs its own offset
            code = path.read_bytes()
            if kind==3:
                assert not code
            else:
                verify(code,layout,kind,hint)
    # A wrong ring stride must fail the independent memory/slot invariants.
    unit.write_text((prefix + overlay.BRANCH_ADD + suffix).replace('ShiftType::LSL, 5','ShiftType::LSL, 4'))
    subprocess.run(command,check=True)
    path = folder / 'mutant.bin'
    layout = json.loads(subprocess.check_output([str(binary),'2',str(path),'2'],text=True))
    try:
        verify(path.read_bytes(),layout,2,2)
    except AssertionError:
        pass
    else:
        raise AssertionError('wrong-stride recorder was accepted')
print('PASS: actual ARM64 recorder targets/returns, NZCV/register/old-ABI preservation, per-frame wrap, disabled emission, drift refusal and negative stride control')
