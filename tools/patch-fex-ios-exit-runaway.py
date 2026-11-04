#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX ARM64EC: a process whose exit re-enters the torn-down emulator is ended instead of spinning for ever (round 630, agent A2 S18).

Enshrouded (PG716Q = F8WYGG, 0CAY0F, 2TKD32; build 626) ends itself after its Steamworks stop: RtlExitUserProcess ->
NtTerminateProcess(handle=0x0) -> LdrShutdownProcess, whose DLL detaches run xtajit64.dll's TLS callbacks last; FEX frees its code
buffer and its dispatcher ("[jit-pool] tail FREE"), and then the same thread enters the freed dispatcher again with a "guest RIP" in
FEX's own heap ("[iOS-bogusrip] band=host-heap(0x7c-0x7f)") and compiles one decommitted page for ever ("[fexlock] UNPUB-COMPILE
rip=0x7c...", 16,000 compiles a second, 270-280 % CPU) until iOS kills the app. The loader's second NtTerminateProcess(-1), the one
that ends the process, is never reached. A process that exits normally frees the same dispatcher and never comes back (Darksiders
01XE9K).

With this patch ContextImpl::CompileBlock's host-address gate (a guest RIP >= 0x7400000000 or inside FEX's code buffer; nothing there
can be emulated) ends the process the way the loader was about to, once the loader is shutting the process down
(RtlDllShutdownInProgress()) and the gate was reached 64 times: "[exit-runaway] ..." and NtTerminateProcess(-1,
STATUS_PROCESS_IS_TERMINATING). Anywhere else the gate does what it did (a log line); a running game never has the loader in its
shutdown. The threshold lets a few transient hits pass and is reached within milliseconds in the spin.

Applied by tools/build-xtajit64.sh (the 64-bit module; Core.cpp is restored after the build); idempotent; a moved anchor refuses
the patch and the build goes on without it (a warning).
Usage: patch-fex-ios-exit-runaway.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "exit-runaway-v1 madeira-bcd"
CORE = "FEXCore/Source/Interface/Core/Core.cpp"

HEAD = "uintptr_t ContextImpl::CompileBlock(FEXCore::Core::CpuStateFrame* Frame, uint64_t GuestRIP, uint64_t MaxInst) {\n"
DECL = ("#if defined(FEX_IOS_HOST) && defined(_WIN32)\n"
        "// " + MARKER + " (tools/patch-fex-ios-exit-runaway.py, round 630): ntdll's, as JIT.cpp declares NtTerminateProcess\n"
        "extern \"C\" unsigned char RtlDllShutdownInProgress(void);\n"
        "extern \"C\" int32_t NtTerminateProcess(void* ProcessHandle, int32_t ExitStatus);\n"
        "#endif\n")
GATE = ("  if (GuestRIP >= 0x7400000000ULL || RIPInFEXCodeBuffer) {\n"
        "    static uint32_t BogusRIPCount = 0;\n")
STOP = ("  if (GuestRIP >= 0x7400000000ULL || RIPInFEXCodeBuffer) {\n"
        "#if defined(FEX_IOS_HOST) && defined(_WIN32)\n"
        "    /* " + MARKER + " (round 630, agent A2 S18): the loader is shutting this process down and the emulator, torn down by its\n"
        "     * DLL detach, was asked for a host address: nothing that runs now can be emulated. End the process the way the loader was\n"
        "     * about to (its second NtTerminateProcess) instead of compiling the same page for ever (Enshrouded PG716Q, 0CAY0F, 2TKD32). */\n"
        "    if (RtlDllShutdownInProgress()) {\n"
        "      static std::atomic<uint32_t> ExitRunaway {0};\n"
        "      if (ExitRunaway.fetch_add(1, std::memory_order_relaxed) + 1 == 64) {\n"
        "        LogMan::Msg::EFmt(\"[exit-runaway] madeira-bcd: the process is shutting down and the emulator was asked for host address {:#x} \"\n"
        "                          \"64 times: ending the process (STATUS_PROCESS_IS_TERMINATING)\", GuestRIP);\n"
        "        NtTerminateProcess(reinterpret_cast<void*>(-1), static_cast<int32_t>(0xC000010A)); // STATUS_PROCESS_IS_TERMINATING\n"
        "      }\n"
        "    }\n"
        "#endif\n"
        "    static uint32_t BogusRIPCount = 0;\n")


def patch(src):
    if MARKER in src:
        if src.count(DECL + HEAD) != 1 or src.count(STOP) != 1:
            raise ValueError("partial or changed exit-runaway")
        return src
    if src.count(HEAD) != 1:
        raise ValueError("ContextImpl::CompileBlock not found once (FEX changed)")
    if src.count(GATE) != 1:
        raise ValueError("the host-address gate of CompileBlock not found once (FEX changed)")
    if not src.index(HEAD) < src.index(GATE):
        raise ValueError("the host-address gate is not inside CompileBlock")
    return src.replace(HEAD, DECL + HEAD, 1).replace(GATE, STOP, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / CORE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-exit-runaway: " + str(error))
    if result == source:
        print("patch-fex-ios-exit-runaway: already in")
    else:
        path.write_text(result)
        print("FEX ARM64EC: a process whose exit keeps asking the torn-down emulator for host addresses is ended ([exit-runaway])")
