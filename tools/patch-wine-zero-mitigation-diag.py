#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""kernelbase-optin.dll: say what GetProcessMitigationPolicy did, and export SetProcessValidCallTargets (night round 2026-11-11, G5 S7).

Glyphica: Typing Survival (Steam 2400160, neosphere.exe with ChakraCore.dll; Send Log 8YHNJ0, build 633, iPad16,3 under LiveContainer) is the first
log with BOTH the key env.MADEIRA_ZERO_MITIGATION_POLICY = 1 and the fork's kernelbase (kernelbase-optin.dll, `[WineProc] MADEIRA_OPTIN_DLLS: kernelbase.dll
-> kernelbase-optin.dll`) and ChakraCore still fails fast (c0000602, `ChakraCore.dll+0x5045a7`, 20 s after the start). The disassembled optin DLL reads
the key and zeroes the buffer, so the round-624 premise ("the key makes ChakraCore take its non-CFG path") did not end the stop; two candidates remain and
no log can tell them apart: (1) the variable is not in the game's Windows environment (a Dock-started program gets its environment block from Valve's
client), so nothing was zeroed; (2) the fail-fast does not depend on the CFG bit: ChakraCore looks up SetProcessValidCallTargets (api-ms-win-core-memory-
l1-1-3.dll, which this Wine routes to kernelbase) and kernelbase.spec has it only as a commented stub.

Two changes to the SAME file that kernelbase-optin.dll is built from, applied by tools/build-wine-extra-dlls.sh around the build of that one DLL and put
back after it (the shipped kernelbase.dll is a committed binary, and the i386 farm and every other build see the unpatched file):
  1. dlls/kernelbase/process.c, GetProcessMitigationPolicy: after the answer, `fixme:process:GetProcessMitigationPolicy madeira-bcd: ZERO_MITIGATION_POLICY
     enabled=<0|1> zeroed=<0|1> policy=<n> length=<n> first_dword=<hex>` (8 lines a process at most). enabled=0: the key is not in the game's Windows
     environment (candidate 1); enabled=1 zeroed=1 first_dword=00000000: the buffer was empty and ChakraCore fails for another reason.
  2. dlls/kernelbase/kernelbase.spec + process.c: SetProcessValidCallTargets is exported, a stub that answers TRUE (as on a Windows without CFG
     enforcement). Only the opt-in kernelbase has it (a game that lists kernelbase in env.MADEIRA_OPTIN_DLLS: Glyphica, Football Manager 27); the shipped
     kernelbase.dll keeps not exporting it. It covers candidate 1 whatever the answer and the lookup part of candidate 2; it does nothing for a fail-fast
     that does not involve CFG.
Idempotent; every anchor is checked before a file is written; fails by name when a place moved.
Usage: patch-wine-zero-mitigation-diag.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: zero-mitigation-diag"
PROCESS = "dlls/kernelbase/process.c"
SPEC = "dlls/kernelbase/kernelbase.spec"

FN_OLD = ('BOOL WINAPI /* DECLSPEC_HOTPATCH */ GetProcessMitigationPolicy( HANDLE process, PROCESS_MITIGATION_POLICY policy,\n'
          '                                                          void *buffer, SIZE_T length )\n'
          '{\n'
          '    FIXME( "(%p, %u, %p, %Iu): stub\\n", process, policy, buffer, length );\n'
          '    if (buffer && length && length <= 16 && zero_mitigation_policy_enabled()) memset( buffer, 0, length );\n'
          '    return TRUE;\n'
          '}\n')
FN_NEW = ('BOOL WINAPI /* DECLSPEC_HOTPATCH */ GetProcessMitigationPolicy( HANDLE process, PROCESS_MITIGATION_POLICY policy,\n'
          '                                                          void *buffer, SIZE_T length )\n'
          '{\n'
          '    static LONG said;   /* ' + MARKER + ' (tools/patch-wine-zero-mitigation-diag.py, night round 2026-11-11, Glyphica 8YHNJ0) */\n'
          '    BOOL zeroed = FALSE;\n'
          '\n'
          '    FIXME( "(%p, %u, %p, %Iu): stub\\n", process, policy, buffer, length );\n'
          '    if (buffer && length && length <= 16 && zero_mitigation_policy_enabled())\n'
          '    {\n'
          '        memset( buffer, 0, length );\n'
          '        zeroed = TRUE;\n'
          '    }\n'
          '    if (InterlockedIncrement( &said ) <= 8)\n'
          '        FIXME( "madeira-bcd: ZERO_MITIGATION_POLICY enabled=%d zeroed=%d policy=%u length=%Iu first_dword=%08lx\\n",\n'
          '               zero_mitigation_policy_enabled(), zeroed, policy, length, (buffer && length >= 4) ? *(DWORD *)buffer : 0 );\n'
          '    return TRUE;\n'
          '}\n'
          '\n'
          '/* ' + MARKER + ': exported by kernelbase-optin.dll only (the shipped kernelbase.dll is a committed binary without it). ChakraCore\n'
          ' * (api-ms-win-core-memory-l1-1-3.dll, routed to kernelbase) looks it up when its control-flow-guard probe says the bit is set and calls\n'
          ' * RaiseFailFastException when it is missing (Glyphica). Control flow guard is not enforced here: every call succeeds. */\n'
          'BOOL WINAPI SetProcessValidCallTargets( HANDLE process, void *address, SIZE_T size, ULONG count, void *info )\n'
          '{\n'
          '    FIXME( "(%p, %p, %Iu, %lu, %p): stub\\n", process, address, size, count, info );\n'
          '    return TRUE;\n'
          '}\n')
SPEC_OLD = "# @ stub SetProcessValidCallTargets\n"
SPEC_NEW = "@ stdcall SetProcessValidCallTargets(ptr ptr ptr long ptr)\n"


def patch(process_src, spec_src):
    if MARKER in process_src:
        if process_src.count(FN_NEW) != 1 or spec_src.count(SPEC_NEW) != 1 or SPEC_OLD in spec_src:
            raise ValueError("partial or changed zero-mitigation diagnostic patch")
        return process_src, spec_src
    if process_src.count(FN_OLD) != 1:
        raise ValueError("GetProcessMitigationPolicy of kernelbase/process.c not found once as the fork has it (Wine changed)")
    if "WINAPI SetProcessValidCallTargets(" in process_src:
        raise ValueError("kernelbase/process.c already has a SetProcessValidCallTargets (Wine changed)")
    if spec_src.count(SPEC_OLD) != 1:
        raise ValueError("the commented SetProcessValidCallTargets stub of kernelbase.spec not found once (Wine changed)")
    return process_src.replace(FN_OLD, FN_NEW, 1), spec_src.replace(SPEC_OLD, SPEC_NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-zero-mitigation-diag.py WINE-DIR")
    wine = Path(sys.argv[1])
    process_src = (wine / PROCESS).read_text()
    spec_src = (wine / SPEC).read_text()
    try:
        new_process, new_spec = patch(process_src, spec_src)
    except ValueError as error:
        sys.exit("patch-wine-zero-mitigation-diag: " + str(error))
    if (new_process, new_spec) == (process_src, spec_src):
        print("patch-wine-zero-mitigation-diag: already patched")
        return
    (wine / PROCESS).write_text(new_process)
    (wine / SPEC).write_text(new_spec)
    print("patch-wine-zero-mitigation-diag: patched")


if __name__ == "__main__":
    main()
