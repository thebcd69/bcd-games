#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX cache measurement (owner's go, 2026-11-02): log-only counters that show what a disk cache of translated code
would save, before one is built. Read from the FEX sources at the pinned commit in a full tree:
  1. Core.cpp times every CompileCode() call (total and largest) from the virtual counter and prints [CB_COST]
     beside [CB_SUMMARY], with the publish and block-link icache flushes counted in JIT.cpp.
  2. [cb-sample] logs 1 in 128 compiled blocks, picked by the low 16 bits of the guest address (stable across
     launches for file-backed code), at most 3000 lines per process; MADEIRA_FEX_CB_SAMPLE=0 turns it off.
  3. The sample asks the image tracker only after the block is published (past the unpublished-compile return,
     next to the upstream code-map writer) and reads only the file id, never the file name.
A tree without the FEX submodule says so and passes (the full tree runs the checks)."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
fails = []
def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)

core_p = root / "FEX/FEXCore/Source/Interface/Core/Core.cpp"
jit_p = root / "FEX/FEXCore/Source/Interface/Core/JIT/JIT.cpp"
if not (core_p.exists() and jit_p.exists()):
    print("skip: no FEX submodule in this tree (the full tree runs these checks)")
    print("PASS: FEX cache measurement (skipped here)")
    sys.exit(0)

core, jit = core_p.read_text(), jit_p.read_text()

check("Core.cpp: CNTVCT_EL0 tick reader, 0 on non-arm64 hosts",
      'static inline uint64_t MadeiraCbTicks()' in core and 'mrs %0, CNTVCT_EL0' in core)
call = "auto [CompiledCode, DebugData, StartAddr, Length, NeedsAddGuestCodeRanges] = CompileCode(Thread, GuestRIP, MaxInst);"
check("Core.cpp: CompileCode() timed on both sides",
      f"const uint64_t MadeiraCbT0 = MadeiraCbTicks();\n  {call}\n  MadeiraCbAddCompileTicks(MadeiraCbTicks() - MadeiraCbT0);" in core)
check("Core.cpp: [CB_COST] line with compile and flush cost, printed after [CB_SUMMARY]",
      '"[CB_COST] compiles={} compile_ms={} avg_us={} max_us={} flush_ms={} flush_calls={} "' in core
      and 0 < core.find('[CB_SUMMARY] total={}') < core.find('[CB_COST] compiles={}'))

i_pub = core.find("    Thread->LookupCache->AddBlockMapping(Thread, GuestAddr, CodePages, HostAddr);")
i_samp = core.find('LogMan::Msg::EFmt("[cb-sample] f {:x} {:x}"')
i_map = core.find("  if (CodeMapWriter) {\n    auto Region = SyscallHandler->LookupExecutableFileSection(Thread, GuestRIP);")
i_unpub = core.find("  if (IosUnpublished) {\n    return (uintptr_t)CodePtr;")
check("Core.cpp: sample taken after the block is published, before the code-map writer, past the unpublished return",
      0 < i_unpub < i_pub < i_samp < i_map)
block = core[i_pub:i_map]
check("Core.cpp: 1 in 128 by the low 16 bits of the guest address",
      "const uint64_t Low = GuestRIP & 0xFFFFull;" in block and "((Low * 0x9E3779B97F4A7C15ull) >> 57) == 0" in block)
check("Core.cpp: at most 3000 sample lines per process",
      "MadeiraSampleLines.fetch_add(1, std::memory_order_relaxed) < 3000" in block)
check("Core.cpp: MADEIRA_FEX_CB_SAMPLE=0 turns the samples off (on otherwise)",
      'getenv("MADEIRA_FEX_CB_SAMPLE")' in block and "MadeiraSampleOn = (Env && Env[0] == '0') ? 0 : 1;" in block)
check("Core.cpp: file-backed samples by file id + offset, anonymous by address; no file name read",
      "Region->FileInfo.FileId, GuestRIP - Region->FileStartVA" in block and '"[cb-sample] a {:x}", GuestRIP' in block
      and "Filename" not in block)

check("JIT.cpp: counters declared (with <cstdint>) before the iOS flush helper",
      "#include <cstdint>\nnamespace FEXCore::MadeiraCbCost {\nextern volatile uint64_t PublishFlushTicks;" in jit
      and jit.find("namespace FEXCore::MadeiraCbCost {") < jit.find("inline void IOSFlushOneInstr("))
check("JIT.cpp: block-link flushes counted",
      "__sync_add_and_fetch(&FEXCore::MadeiraCbCost::LinkFlushCalls, 1);\n  NtFlushInstructionCache(reinterpret_cast<void*>(~0ull), RXAddr, 4);" in jit)
check("JIT.cpp: publish flush timed around NtFlushInstructionCache",
      "NtFlushInstructionCache(reinterpret_cast<void*>(~0ull), CodeBegin, CodeOnlySize);\n    __asm volatile(\"mrs %0, CNTVCT_EL0\" : \"=r\"(MadeiraT1));\n"
      "    __sync_add_and_fetch(&FEXCore::MadeiraCbCost::PublishFlushTicks, MadeiraT1 - MadeiraT0);" in jit)

if fails:
    print("FAILED: FEX cache measurement")
    sys.exit(1)
print("PASS: FEX cache measurement ([CB_COST], [cb-sample])")
