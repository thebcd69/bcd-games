#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX ARM64EC: a thread with no CPU area is not an emulated thread (night round 2026-11-11, W-FEX: R1-S3 and X1 S1c).

A native ARM64 program (services.exe, cmd.exe, conhost.exe, wineboot.exe ...) that an x64 program of an ARM64EC session starts runs on the
session's ARM64EC ntdll, which loads FEX into it (Wine's loader_init calls load_arm64ec_module() for every process of that ntdll), but
build/ntdll-unix/thread_ios.c init_thread_stack deliberately gives such a thread NO CHPE CPU area ("NOT setting cpu_area (owner
machine=0xaa64 session is_arm64ec=1)": TEB->ChpeV2CpuAreaInfo == NULL). Every FEX hook that asks the area for its emulator thread state
(`GetCPUArea().ThreadState()` = `Area->EmulatorData[1]`) then reads NULL+0x38 and the program dies at its first memory call:
`[fex-fault] pc=libarm64ecfex.dll+0x113c14 (NotifyMemoryAlloc+0x54) fault-addr=0x38`. Seven archive sessions (Epic GTA V Enhanced
QG7MT4, KT9K9S, SCHZWC, DABCAC, 4DRZ3H: BCD's host starts services.exe; The Escapists 4TXJW2 and Psych Engine 3012R0: `cmd.exe /c start`)
fault identically, no session that works takes this path.

A thread without an area is not emulated, so none of these hooks has anything to do for it (the hooks already return for an EC thread whose
`ThreadState` is NULL). The patch makes the reads null-safe:
  * HasCPUArea() / HasEmulatorThreadState() (new, next to GetCPUArea): the area pointer is read through the TEB first;
  * NotifyMemoryAlloc, NotifyMemoryFree, NotifyMemoryProtect, NotifyUnmapViewOfSection, FlushInstructionCacheHeavy,
    BTCpu64FlushInstructionCache and BTCpu64NotifyMemoryDirty test HasEmulatorThreadState() instead of GetCPUArea().ThreadState();
  * BTCpu64NotifyReadFile reads the thread state only when there is an area (a NULL one then takes its existing "no thread state" return);
  * ResetToConsistentState (called by the ARM64EC ntdll for EVERY exception) returns at once for a thread without an area, so Wine goes on
    with the exception as it is instead of the emulator reading a NULL area (ScopedCallbackDisable and the CPUArea reads below it);
  * ThreadInit returns STATUS_SUCCESS for such a thread (nothing to initialise: its first line would write the area's emulator stack
    limit through NULL; arm64ec_process_init runs it for the native main thread right after ProcessInit).
Every changed line sits on a path that dereferences NULL today; a thread WITH an area runs exactly the old code (HasCPUArea() is true,
the old condition follows). The Wine side of the same defect (the ntdll's own `[rtcs] pre` log argument) is R1-S3b (W-FEX .md).

Applied by tools/build-xtajit64.sh to every pin (Module.cpp is restored after the build); idempotent, all-or-nothing, fails by name when
an anchor moves. Usage: patch-fex-ios-notify-nocpuarea.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "ec-no-cpu-area-v1 madeira-bcd"

HELPER_ANCHOR = (
    "ThreadCPUArea GetCPUArea() {\n"
    "#ifdef FEX_IOS_HOST\n"
    "  return ThreadCPUArea(IOSLoadTEB());\n"
    "#else\n"
    "  return ThreadCPUArea(NtCurrentTeb());\n"
    "#endif\n"
    "}\n"
)
HELPER = HELPER_ANCHOR + (
    "\n"
    "// " + MARKER + " (round 2026-11-11, R1-S3 / X1 S1c): a native ARM64 thread of an ARM64EC session (services.exe, cmd.exe, conhost.exe)\n"
    "// has TEB->ChpeV2CpuAreaInfo == NULL (build/ntdll-unix/thread_ios.c, \"NOT setting cpu_area\") although the ARM64EC ntdll still calls\n"
    "// these hooks for it. Such a thread is not emulated: there is no thread state to ask for. A TEB that cannot be read keeps the old path.\n"
    "inline bool HasCPUArea() {\n"
    "#ifdef FEX_IOS_HOST\n"
    "  const _TEB* Teb = IOSLoadTEB();\n"
    "#else\n"
    "  const _TEB* Teb = NtCurrentTeb();\n"
    "#endif\n"
    "  if (!Teb) {\n"
    "    return true;\n"
    "  }\n"
    "  return *reinterpret_cast<void* const*>(reinterpret_cast<uintptr_t>(Teb) + ThreadCPUArea::TEBCPUAreaOffset) != nullptr;\n"
    "}\n"
    "\n"
    "inline bool HasEmulatorThreadState() {\n"
    "  return HasCPUArea() && GetCPUArea().ThreadState();\n"
    "}\n"
)

OLD_COND = "  if (!InvalidationTracker || !GetCPUArea().ThreadState()) {\n"
NEW_COND = "  if (!InvalidationTracker || !HasEmulatorThreadState()) {   // " + MARKER + "\n"

# the hooks whose first statement asks the CPU area for the thread state (signature lines of the pinned Module.cpp)
HOOKS = [
    "void NotifyMemoryAlloc(void* Address, SIZE_T Size, ULONG Type, ULONG Prot, BOOL After, NTSTATUS Status) {\n",
    "void NotifyMemoryFree(void* Address, SIZE_T Size, ULONG FreeType, BOOL After, NTSTATUS Status) {\n",
    "void NotifyMemoryProtect(void* Address, SIZE_T Size, ULONG NewProt, BOOL After, NTSTATUS Status) {\n",
    "void NotifyUnmapViewOfSection(void* Address, BOOL After, NTSTATUS Status) {\n",
    "void FlushInstructionCacheHeavy(const void* Address, SIZE_T Size) {\n",
    "void BTCpu64FlushInstructionCache(const void* Address, SIZE_T Size) {\n",
    "void BTCpu64NotifyMemoryDirty(void* Address, SIZE_T Size) {\n",
]

READFILE_HEAD = "void BTCpu64NotifyReadFile(HANDLE Handle, void* Address, SIZE_T Size, BOOL After, NTSTATUS Status) {\n"
READFILE_OLD = "  auto* ThreadState = GetCPUArea().ThreadState();\n"
READFILE_NEW = "  auto* ThreadState = HasCPUArea() ? GetCPUArea().ThreadState() : nullptr;   // " + MARKER + "\n"

RTCS_HEAD = "NTSTATUS ResetToConsistentState(EXCEPTION_RECORD* Exception, CONTEXT* GuestContext, ARM64_NT_CONTEXT* NativeContext) {\n"
RTCS_GUARD = (
    "  if (!HasCPUArea()) {   // " + MARKER + ": a native ARM64 thread of an ARM64EC session is not emulated; Wine goes on with the exception\n"
    "    return STATUS_SUCCESS;\n"
    "  }\n"
)

TI_HEAD = "NTSTATUS ThreadInit() {\n"
TI_GUARD = (
    "  if (!HasCPUArea()) {   // " + MARKER + ": a native ARM64 thread of an ARM64EC session has no emulator state to build\n"
    "    return STATUS_SUCCESS;\n"
    "  }\n"
)


def function_span(text, head, what):
    """(start, end) of the function whose signature line is `head` (once in the file); end = the next line that is a lone }."""
    n = text.count(head)
    if n != 1:
        sys.exit("patch-fex-ios-notify-nocpuarea: %s not found once (%d times; FEX changed)" % (what, n))
    start = text.index(head)
    end = text.find("\n}\n", start)
    if end < 0:
        sys.exit("patch-fex-ios-notify-nocpuarea: the end of %s not found (FEX changed)" % what)
    return start, end


def replace_in_function(text, head, old, new, what):
    start, end = function_span(text, head, what)
    body = text[start:end]
    if body.count(old) != 1:
        sys.exit("patch-fex-ios-notify-nocpuarea: %s: the thread-state line is not there once (FEX changed)" % what)
    return text[:start] + body.replace(old, new) + text[end:]


def insert_after_signature(text, head, guard, what):
    function_span(text, head, what)
    return text.replace(head, head + guard)


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        sys.exit("patch-fex-ios-notify-nocpuarea: %s not found once (%d times; FEX changed)" % (what, n))
    return text.replace(old, new)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / "Source/Windows/ARM64EC/Module.cpp"
    src = path.read_text()
    if MARKER in src:
        print("ARM64EC/Module.cpp: the no-CPU-area guards are already in; nothing to do")
        return
    out = replace_once(src, HELPER_ANCHOR, HELPER, "GetCPUArea()")
    for head in HOOKS:
        out = replace_in_function(out, head, OLD_COND, NEW_COND, head.split("(")[0].split()[-1])
    out = replace_in_function(out, READFILE_HEAD, READFILE_OLD, READFILE_NEW, "BTCpu64NotifyReadFile")
    out = insert_after_signature(out, RTCS_HEAD, RTCS_GUARD, "ResetToConsistentState")
    out = insert_after_signature(out, TI_HEAD, TI_GUARD, "ThreadInit")
    path.write_text(out)
    print("ARM64EC/Module.cpp: a thread without a CPU area (a native ARM64 program of an ARM64EC session) no longer faults in the emulator's hooks")


main()
