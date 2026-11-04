#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit thread that gets no emulator thread state ends by itself instead of taking its process along (round 630, agent A2 S3).

The 32-bit Steam client (steam.exe and steamwebhelper.exe; R883EG, W2J15H, 9PB1B5: one iPad mini on a 63 GB address map, builds
625 and 626) filled its emulator window a few seconds into Big Picture: FEXCore's CreateThread returned NULL for the next thread
("[nt-fail] ... NtAllocateVirtualMemoryEx status=c0000017 ... ext={addr[low=0xc00000000 high=0xeffffffff ...]}") and
BTCpuThreadInit used it at once (`Thread->CurrentFrame`, "[fex-fault] pc=libwow64fex.dll+0x102af4 ... fault-addr=0x0"): the whole
client died, and in 9PB1B5 the app with it. With this patch that thread logs "[wow64-tls] ... CreateThread returned null" and ends
itself with STATUS_NO_MEMORY; the creation lock is released first, because no other thread can start or end while it is held. A
thread whose state was created runs exactly as before (the lock is a std::unique_lock instead of a std::scoped_lock on the same
mutex, held for the same span). build/ntdll-unix/virtual_ios.c gives the emulator its whole window in the first place (the
"[wow-band] r630" retry); this is the safety net for when the room runs out anyway.

Applied to the FEX tree before the WOW64 module is built (tools/build-xtajit-wow64.sh, the CI step that ships xtajit.dll, and
build/fex-wow64/build.sh), which restore Module.cpp afterwards. Anchors are the pinned FEX's; a changed anchor refuses the patch and
the build goes on without it (a warning).

Usage: patch-fex-ios-thread-init-nomem.py <FEX tree>
Idempotent: a second run says "already in" and changes nothing.
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: thread-init-nomem v1"
MODULE = "Source/Windows/WOW64/Module.cpp"

ANCHOR = ("void BTCpuThreadInit() {\n"
          "  static constexpr size_t DefaultWow64CS {4};\n"
          "  std::scoped_lock Lock(ThreadCreationMutex);\n"
          "  FEX::Windows::InitCRTThread();\n"
          "  auto* Thread = CTX->CreateThread(0, 0);\n")
AFTER = ("#ifdef FEX_IOS_HOST\n"
         "// " + MARKER + ": not declared by FEX's winternl.h; ntdll exports it (Source/Windows/Defs/ntdll.def)\n"
         "extern \"C\" NTSTATUS WINAPI NtTerminateThread(HANDLE, LONG);\n"
         "#endif\n"
         "void BTCpuThreadInit() {\n"
         "  static constexpr size_t DefaultWow64CS {4};\n"
         "  std::unique_lock Lock(ThreadCreationMutex); // " + MARKER + ": released before a thread without a state ends\n"
         "  FEX::Windows::InitCRTThread();\n"
         "  auto* Thread = CTX->CreateThread(0, 0);\n"
         "#ifdef FEX_IOS_HOST\n"
         "  /* " + MARKER + " (tools/patch-fex-ios-thread-init-nomem.py, round 630): CreateThread returns NULL when the emulator's\n"
         "   * window has no room for another thread state (the 32-bit Steam client on a 63 GB address map: 9PB1B5, W2J15H, R883EG).\n"
         "   * The lines below used it at once and the NULL read ended the whole process. This thread ends alone instead; the lock is\n"
         "   * released first, since the thread never returns from NtTerminateThread. */\n"
         "  if (!Thread) {\n"
         "    LogMan::Msg::EFmt(\"[wow64-tls] tid={:#x} CreateThread returned null: no room for another thread state; this thread ends \"\n"
         "                      \"with STATUS_NO_MEMORY (madeira-bcd thread-init-nomem)\", GetCurrentThreadId());\n"
         "    Lock.unlock();\n"
         "    NtTerminateThread(NtCurrentThread(), STATUS_NO_MEMORY);\n"
         "    return;\n"
         "  }\n"
         "#endif\n")


def patch(module):
    if MARKER in module:
        if module.count(AFTER) != 1:
            raise ValueError("partial or changed thread-init-nomem")
        return module
    if module.count(ANCHOR) != 1:
        raise ValueError("WOW64/Module.cpp anchor changed: BTCpuThreadInit's first lines")
    return module.replace(ANCHOR, AFTER, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-2])
    path = Path(sys.argv[1]) / MODULE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-thread-init-nomem: " + str(error))
    if result == source:
        print("patch-fex-ios-thread-init-nomem: already in")
    else:
        path.write_text(result)
        print("FEX WOW64: a thread that gets no emulator state ends alone with STATUS_NO_MEMORY ([wow64-tls] ... returned null)")
