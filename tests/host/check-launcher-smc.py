#!/usr/bin/env python3
"""Exercise the Launcher policy and actual FEX ARM64 code-byte validator.

Requires C++20, pinned fmt headers and Python unicorn. This does not run the
full JIT or a game. The production validator body uses the real FEX emitter;
the fixture supplies operands, constant loads and short-range label bindings.
"""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import tempfile

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_NZCV, UC_ARM64_REG_SP

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('smc', root / 'tools/patch-fex-ios-launcher-smc.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)
module = (root / 'FEX/Source/Windows/ARM64EC/Module.cpp').read_text()
patched = overlay.patch(module)
assert overlay.patch(patched) == patched
history_spec = importlib.util.spec_from_file_location('history', root / 'tools/patch-fex-ios-branch-history.py')
history = importlib.util.module_from_spec(history_spec)
history_spec.loader.exec_module(history)
history_sources = {name: (root/'FEX'/name).read_text() for name in history.EDITS}
# Since the 2026-10-15 merge the FEX pin carries the branch-history overlay inside its combined files, where the overlay's own
# check ("partial or changed overlay") stops; build-xtajit64.sh does not run it on such a pin. Use the pin's files as they are.
pin_has_history = all(history.MARKER in source for source in history_sources.values())
if not pin_has_history:
    history_sources = history.patch(history_sources)
history_sources[history.MODULE] = overlay.patch(history_sources[history.MODULE])
if not pin_has_history:
    assert history.patch(history_sources) == history_sources
assert overlay.patch(history_sources[history.MODULE]) == history_sources[history.MODULE]
for source in (module.replace(overlay.ANCHOR, 'changed-anchor'),
               patched.replace(overlay.ADD, overlay.MARKER)):
    try:
        overlay.patch(source)
    except ValueError:
        pass
    else:
        raise AssertionError('changed or partial policy accepted')
assert patched.index(overlay.ADD) < patched.index('Context::CreateNewContext(')
build = (root / 'tools/build-xtajit64.sh').read_text()
# build-xtajit64.sh names the launcher-smc overlay first in its branch for a pin that already carries the others; the order that matters is the
# one of the older-pin sequence, which is the LAST place both appear.
assert build.rindex('patch-fex-ios-branch-history.py') < build.rindex('patch-fex-ios-launcher-smc.py')
assert "b'launcher-smc-v1 madeira-bcd' not in module" in (root / '.github/workflows/build-ipa.yml').read_text()
config = (root / 'FEX/FEXCore/include/FEXCore/Config/Config.h').read_text()
start = config.index('enum ConfigSMCChecks {')
smc_enum = config[start:config.index('\n};', start)+3]
branch = (root / 'FEX/FEXCore/Source/Interface/Core/JIT/BranchOps.cpp').read_text()
start = branch.index('DEF_OP(ValidateCode) {')
validator = branch[start:branch.index('\n}\n', start)+2].split('{', 1)[1].rsplit('}', 1)[0]
core = (root / 'FEX/FEXCore/Source/Interface/Core/Core.cpp').read_text()
assert 'Config.SMCChecks == FEXCore::Config::CONFIG_SMC_FULL' in core
assert 'Thread->OpDispatcher->_ThreadRemoveCodeEntry();' in core

prefix = r'''
#include <CodeEmitter/Emitter.h>
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <string_view>
#include <strings.h>
namespace fextl {
using string = std::string;
[[noreturn]] void ReportAllocationFailure(std::size_t, std::size_t) { std::abort(); }
namespace fmt { string format(const char*, unsigned value) { return std::to_string(value); } }
}
namespace FEXCore::Allocator {
void* aligned_alloc(size_t alignment,size_t size) {
  void* result=nullptr;
  if (alignment<sizeof(void*)) alignment=sizeof(void*);
  return ::posix_memalign(&result,alignment,size) ? nullptr : result;
}
void aligned_free(void* value) { std::free(value); }
}
namespace LogMan::Msg { template<class... T> void IFmt(const char*, T&&...) {} }
#define _stricmp strcasecmp
namespace FEXCore::Config {
''' + smc_enum + r'''
enum ConfigOption { CONFIG_SMCCHECKS };
static unsigned Current = CONFIG_SMC_MTRACK, Calls;
void Set(ConfigOption option, std::string_view value) {
  assert(option == CONFIG_SMCCHECKS);
  Current = std::strtoul(std::string(value).c_str(),nullptr,10); ++Calls;
}
struct Value { unsigned operator()() const { return Current; } };
}
#define FEX_CONFIG_OPT(Name, Option) FEXCore::Config::Value Name
void policy(const char* ExecutableName) {
''' + overlay.ADD + r'''
}
struct Operand { int unused; };
struct OpType {
  struct { Operand Args[1]; } Header;
  alignas(8) std::array<uint8_t,16> CodeOriginal;
  unsigned CodeLength;
};
namespace IR { using IROp_ValidateCode = OpType; }
struct Wrap { OpType* Op; template<class T> T* C() { return Op; } };
struct Reg { ARMEmitter::XRegister X() const { return ARMEmitter::XReg::x4; } };
struct Fixture : ARMEmitter::Emitter {
  static constexpr auto TMP1 = ARMEmitter::XReg::x10, TMP2 = ARMEmitter::XReg::x11;
  Fixture(uint8_t* data, size_t size) : Emitter(data,size) {}
  Reg GetGuestMemReg(Operand) { return {}; }
  ARMEmitter::XRegister GetReg(unsigned) { return ARMEmitter::XReg::x5; }
  void LoadConstant(ARMEmitter::Size size, ARMEmitter::Register reg, uint64_t value) {
    const unsigned bits = size==ARMEmitter::Size::i64Bit ? 64 : 32;
    movz(size,reg,value & 0xffff);
    for (unsigned shift=16;shift<bits;shift+=16) movk(size,reg,(value>>shift)&0xffff,shift);
  }
  void cbnz_OrRestart(ARMEmitter::Size size, ARMEmitter::Register reg, ARMEmitter::ForwardLabel* label) { assert(cbnz(size,reg,label)==ARMEmitter::BranchEncodeSucceeded::Success); }
  void b_OrRestart(ARMEmitter::ForwardLabel* label) { assert(b(label)==ARMEmitter::BranchEncodeSucceeded::Success); }
  void BindOrRestart(ARMEmitter::ForwardLabel* label) { assert(Bind(label)); }
  void validate(OpType* op) { Wrap wrap {op}; auto IROp = &wrap; unsigned Node=0;
''' + validator + r'''
  }
};
'''
suffix = r'''
int main(int argc,char** argv) {
  if (argc==2 && !std::strcmp(argv[1],"policy")) {
    using namespace FEXCore::Config;
    unsetenv("FEX_SMCCHECKS"); unsetenv("MADEIRA_LAUNCHER_FULL_SMC");
    for (auto name : {"Launcher.exe","LAUNCHER.EXE","launcher.exe"}) {
      for (unsigned mode=0;mode<=2;mode++) { Current=mode; Calls=0; policy(name); assert(Current==mode && !Calls); }
    }
    for (auto value : {"0","0-disabled","","1-invalid","true","2"}) {
      setenv("MADEIRA_LAUNCHER_FULL_SMC",value,1);
      for (unsigned mode=0;mode<=2;mode++) { Current=mode; Calls=0; policy("Launcher.exe"); assert(Current==mode && !Calls); }
    }
    setenv("MADEIRA_LAUNCHER_FULL_SMC","1",1);
    for (auto name : {"Launcher.exe","LAUNCHER.EXE","launcher.exe"}) {
      for (unsigned mode=0;mode<=2;mode++) { Current=mode; Calls=0; policy(name); assert(Current==CONFIG_SMC_FULL && Calls==1); }
    }
    for (auto value : {"none","mtrack","full","0","1","2"}) {
      setenv("FEX_SMCCHECKS",value,1);
      for (unsigned mode=0;mode<=2;mode++) { Current=mode; Calls=0; policy("Launcher.exe"); assert(Current==mode && !Calls); }
    }
    setenv("FEX_SMCCHECKS","",1);
    Current=CONFIG_SMC_MTRACK; Calls=0; policy("Launcher.exe"); assert(Current==CONFIG_SMC_FULL && Calls==1);
    for (auto name : {"PlayGTAV.exe","GTA5_Enhanced.exe","SocialClubHelper.exe","RockstarService.exe","dockhost.exe","OtherLauncher.exe"}) {
      for (unsigned mode=0;mode<=2;mode++) { Current=mode; Calls=0; policy(name); assert(Current==mode && !Calls); }
    }
    unsetenv("MADEIRA_LAUNCHER_FULL_SMC");
    Current=CONFIG_SMC_MTRACK; Calls=0; policy("Launcher.exe"); assert(Current==CONFIG_SMC_MTRACK && !Calls);
    puts("PASS: configured SMC policy preserved by default, exact Launcher-only opt-in and explicit configuration priority"); return 0;
  }
  if (argc!=3) return 1;
  OpType op {}; op.CodeLength=std::strtoul(argv[1],nullptr,10);
  if (!op.CodeLength || op.CodeLength>15) return 2;
  for (unsigned i=0;i<16;i++) op.CodeOriginal[i]=0x13+i*11;
  uint8_t code[4096] {}; Fixture fixture(code,sizeof(code)); fixture.validate(&op);
  FILE* file=std::fopen(argv[2],"wb"); assert(file);
  std::fwrite(code,1,fixture.GetCursorOffset(),file); std::fclose(file);
  std::printf("{\"bytes\":%zu}\n",fixture.GetCursorOffset()); return 0;
}
'''


def verify(code, length):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    address, source = 0x100000, 0x200005
    uc.mem_map(address,0x10000)
    uc.mem_map(0x200000,0x10000)
    uc.mem_write(address,code)
    original = bytes(0x13+i*11 for i in range(16))
    for change in [None, *range(16)]:
        current = bytearray(original)
        if change is not None:
            current[change] ^= 0x40
        uc.mem_write(source,bytes(current))
        for reg in range(29):
            uc.reg_write(UC_ARM64_REG_X0+reg,0xABC000000+reg)
        uc.reg_write(UC_ARM64_REG_X0+4,source)
        uc.reg_write(UC_ARM64_REG_NZCV,0xB0000000)
        uc.reg_write(UC_ARM64_REG_SP,0x300000)
        before = [uc.reg_read(UC_ARM64_REG_X0+reg) for reg in range(29)]
        uc.emu_start(address,address+len(code))
        assert uc.reg_read(UC_ARM64_REG_X0+5)==int(change is not None and change<length)
        assert uc.reg_read(UC_ARM64_REG_NZCV)==0xB0000000
        assert uc.reg_read(UC_ARM64_REG_SP)==0x300000
        assert bytes(uc.mem_read(source,16))==bytes(current)
        for reg in range(29):
            if reg not in (5,10,11): assert uc.reg_read(UC_ARM64_REG_X0+reg)==before[reg]


with tempfile.TemporaryDirectory(prefix='launcher-smc-') as directory:
    folder = Path(directory)
    unit, binary, words = folder/'fixture.cpp',folder/'fixture',folder/'words.bin'
    unit.write_text(prefix+suffix)
    command = [os.environ.get('CXX','c++'),'-std=c++20','-O1','-g','-DFEX_IOS_HOST=1',
               '-fsanitize=address,undefined','-fno-sanitize-recover=all',
               '-I',str(root/'FEX/FEXCore/include'),'-I',str(root/'FEX/CodeEmitter'),'-I',str(root/'FEX/FEXHeaderUtils'),
               '-I',str(root/'FEX/External/fmt/include'),str(unit),'-o',str(binary)]
    subprocess.run(command,check=True)
    subprocess.run([str(binary),'policy'],check=True)
    for length in range(1,16):
        result=json.loads(subprocess.check_output([str(binary),str(length),str(words)],text=True))
        assert result['bytes']==words.stat().st_size
        verify(words.read_bytes(),length)
    # If comparison is accidentally suppressed, changed bytes must catch it.
    original = 'sub(ARMEmitter::Size::i64Bit, TMP1, TMP1, TMP2);'
    assert prefix.count(original)==1
    unit.write_text(prefix.replace(original,'sub(ARMEmitter::Size::i64Bit, TMP1, TMP1, TMP1);')+suffix)
    subprocess.run(command,check=True)
    subprocess.check_output([str(binary),'15',str(words)],text=True)
    try:
        verify(words.read_bytes(),15)
    except AssertionError:
        pass
    else:
        raise AssertionError('disabled code validator accepted')
print('PASS: production ARM64 ValidateCode detects each changed byte for lengths1..15, preserves NZCV/registers, excludes adjacent bytes and catches disabled-comparison control')
