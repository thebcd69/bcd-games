#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315,
# patches/0143-fex-lean-callret.patch: its changes to JIT.cpp and JITClass.h) under GPL-3.0-or-later and modified for BCD in
# November 2026. See THIRD-PARTY-NOTICES.md.
"""FEX (ARM64EC module): no suspend check at the point a guest CALL returns to (MADEIRA_FEX_CALLRET_LEAN; an A/B experiment).

Every entry point of the JIT's code starts with a header: the block header's address is stored, the trap flag is tested when
asked, room for spill slots is made, and the thread's suspend doorbell is tested (`ldr w; cbz; brk #0xcafe`: a thread that another
one wants suspended stops there, with a state the emulator can hand out). The instruction after a guest CALL is such an entry
point: the callee's RET comes back to it through the L1 cache.

With MADEIRA_FEX_CALLRET_LEAN=1 the doorbell test is left out of an entry point that directly follows the CALL it belongs to (the
return block of the call that was just emitted, with no branch into it pending). Two instructions fewer are run on every return
from a guest call, and twelve bytes less code are emitted per call site. Every other entry point keeps its test, and so does every
unconditional backward branch.

Why a thread still stops soon after its doorbell rings: the code between a guest CALL and the instruction after it always passes
a doorbell test of its own. An x64 callee is entered at its own entry point, which has the test. An ARM64EC callee is entered
through ExitFunctionEC (or ExitFunctionECJit), which reads the doorbell after it has left the simulation. So the test that is
left out was the second one in a row. The one call whose callee would be the return point itself, `call` to the next instruction
(the position-independent-code idiom), is not emitted as a call at all (OpDispatchBuilder::CALLOp pushes the address and ends the
block), so the point after it is an ordinary entry point and keeps its test. A return point that is reached some other way (a jump from another compiled unit, a context
set by an exception) no longer tests at that point and does at the next call, backward jump or entry point, as straight-line code
always has.

This is the third change of Porthole's patch 0143. Its other two (a call-ret stack guard in two instructions, and the stack's base
on an 8 MiB boundary for it) are not taken: BCD's JIT does not keep the call-ret stack at all (tools/patch-fex-ios-callret-elide.py),
so there is no guard to shorten, and with MADEIRA_FEX_CALLRET_ELIDE=0 the guard stays as it is.

Unless MADEIRA_FEX_CALLRET_LEAN is exactly 1 when the program starts (default off) every entry point is emitted as before, byte
for byte: EmitEntryPoint's new argument is false. The module says "FEX: callret-lean-v1 madeira-bcd enabled=1" in the log when it
is on, and nothing when it is off. Off iOS, and in the WOW64 module (which this script is not applied to), nothing changes.

Applied by tools/build-xtajit64.sh after its other overlays (JIT.cpp, JITClass.h and Module.cpp are restored after the build);
idempotent; a moved anchor or a partial overlay refuses the patch and the build goes on without it (a warning).
Usage: patch-fex-ios-callret-lean.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "callret-lean-v1 madeira-bcd"
JIT = "FEXCore/Source/Interface/Core/JIT/JIT.cpp"
JIT_H = "FEXCore/Source/Interface/Core/JIT/JITClass.h"
MODULE = "Source/Windows/ARM64EC/Module.cpp"

# (file, what, anchor, replacement)
HUNKS = (
    (JIT_H, "EmitEntryPoint's declaration",
     "  void EmitEntryPoint(ARMEmitter::BackwardLabel& HeaderLabel, bool CheckTF);\n",
     "  // callret-lean-v1 madeira-bcd (tools/patch-fex-ios-callret-lean.py): SkipSuspendCheck leaves the suspend doorbell test out of\n"
     "  // the entry point; only the point a CALL falls through into asks for it, and only with MADEIRA_FEX_CALLRET_LEAN=1.\n"
     "  void EmitEntryPoint(ARMEmitter::BackwardLabel& HeaderLabel, bool CheckTF, bool SkipSuspendCheck = false);\n"),
    (JIT, "the includes",
     "#include <cstdio>\n#include <cstring>\n",
     "#include <cstdio>\n#include <cstdlib>\n#include <cstring>\n"),
    (JIT, "EmitEntryPoint's definition",
     "void Arm64JITCore::EmitEntryPoint(ARMEmitter::BackwardLabel& HeaderLabel, bool CheckTF) {\n",
     "// callret-lean-test:begin\n"
     "/* callret-lean-v1 madeira-bcd (tools/patch-fex-ios-callret-lean.py; from Porthole's patch 0143): whether the suspend doorbell test\n"
     " * is left out at the point a guest CALL returns to. Only exactly \"1\" is on (default off); read once, when the process's emulator\n"
     " * first emits such a point. Only the iOS ARM64EC module has the switch. */\n"
     "static bool CallRetLeanEnabled() {\n"
     "#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)\n"
     "  static const bool On = [] {\n"
     "    const char* Env = getenv(\"MADEIRA_FEX_CALLRET_LEAN\");\n"
     "    return Env && Env[0] == '1' && !Env[1];\n"
     "  }();\n"
     "  return On;\n"
     "#else\n"
     "  return false;\n"
     "#endif\n"
     "}\n"
     "// callret-lean-test:end\n"
     "\n"
     "void Arm64JITCore::EmitEntryPoint(ARMEmitter::BackwardLabel& HeaderLabel, bool CheckTF, bool SkipSuspendCheck) {\n"),
    (JIT, "the end of EmitEntryPoint",
     "  EmitSuspendInterruptCheck();\n}\n\nCPUBackend::CompiledCode Arm64JITCore::CompileCode(",
     "  /* callret-lean-v1 madeira-bcd: the doorbell test is left out at the point a CALL returns to (the callee's own entry point, or\n"
     "   * ExitFunctionEC for an ARM64EC callee, tested it on the way). Every other entry point and every backward jump keeps its test. */\n"
     "  if (!SkipSuspendCheck) {\n"
     "    EmitSuspendInterruptCheck();\n"
     "  }\n}\n\nCPUBackend::CompiledCode Arm64JITCore::CompileCode("),
    (JIT, "the entry point of a block (the pending call return)",
     "        PendingCallReturnTargetLabel = nullptr;\n\n        BindOrRestart(&IsReturnTarget->second);\n",
     "        // callret-lean-v1 madeira-bcd: this entry point directly follows the CALL it is the return block of, and nothing branches into it here\n"
     "        const bool IsCallReturnFallthrough = PendingCallReturnTargetLabel == &IsReturnTarget->second && !PendingTargetLabel;\n"
     "        PendingCallReturnTargetLabel = nullptr;\n\n        BindOrRestart(&IsReturnTarget->second);\n"),
    (JIT, "the entry point of a block (EmitEntryPoint's call)",
     "        EmitEntryPoint(JITCodeHeaderLabel, CheckTF);\n",
     "        EmitEntryPoint(JITCodeHeaderLabel, CheckTF, IsCallReturnFallthrough && CallRetLeanEnabled());\n"),
    (MODULE, "the module's start (after the call-ret elision's line)",
     "    LogMan::Msg::IFmt(\"FEX: callret-elide-v1 madeira-bcd enabled={}\", IosCallRetStackElided != 0);\n  }\n#endif\n",
     "    LogMan::Msg::IFmt(\"FEX: callret-elide-v1 madeira-bcd enabled={}\", IosCallRetStackElided != 0);\n  }\n#endif\n"
     "#ifdef FEX_IOS_HOST\n"
     "  {\n"
     "    /* callret-lean-v1 madeira-bcd (tools/patch-fex-ios-callret-lean.py): the JIT reads the same variable itself, the same way\n"
     "     * (CallRetLeanEnabled in JIT.cpp); here it is only said in the log, and only when it is on. */\n"
     "    const char* LeanOpt = getenv(\"MADEIRA_FEX_CALLRET_LEAN\");\n"
     "    if (LeanOpt && LeanOpt[0] == '1' && !LeanOpt[1]) {\n"
     "      LogMan::Msg::IFmt(\"FEX: callret-lean-v1 madeira-bcd enabled=1 (no suspend check at the point a guest CALL returns to)\");\n"
     "    }\n"
     "  }\n"
     "#endif\n"),
)
FILES = (JIT_H, JIT, MODULE)


def patch(texts):
    """texts: {relative path: source}. Returns the patched dict (the same object's values when everything is in already)."""
    present = [MARKER in texts[f] for f in FILES]
    if all(present):
        for f, what, old, new in HUNKS:
            if texts[f].count(new) != 1:
                raise ValueError("partial or changed callret-lean (" + what + ")")
        return texts
    if any(present):
        raise ValueError("partial callret-lean: " + ", ".join(f for f, p in zip(FILES, present) if p) + " patched, the rest not")
    out = dict(texts)
    for f, what, old, new in HUNKS:
        if out[f].count(old) != 1:
            raise ValueError(what + " not found once in " + f + " (FEX changed)")
        out[f] = out[f].replace(old, new, 1)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    root = Path(sys.argv[1])
    try:
        sources = {f: (root / f).read_text() for f in FILES}
        result = patch(sources)
    except (OSError, ValueError) as error:
        sys.exit("patch-fex-ios-callret-lean: " + str(error))
    if result == sources:
        print("patch-fex-ios-callret-lean: already in")
    else:
        for f in FILES:
            (root / f).write_text(result[f])
        print("FEX: no suspend check at the point a guest CALL returns to (MADEIRA_FEX_CALLRET_LEAN=1; without it every entry point is as before)")
