#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX: the code-invalidation walk skips a registered thread that has no decoder or lookup cache (round 630, agent O2 S7).

Outlast (Y09766, build 623; the same class as GTA: Vice City D938HM and round 624's S4): to drop the cached code of a freed memory
range, FEX walks every registered thread with its CodeInvalidationMutex and the process-wide thread mutex held. One thread's state
had a NULL FrontendDecoder; ContextImpl::InvalidateThreadCachedCodeRange faulted on it (`stp xzr,xzr,[x8,#0x68]`), the exception left
the native frames without running a destructor, both locks stayed owned and every game thread then waited for ever ("[fexlock] STUCK").

A thread without a decoder or a lookup cache has nothing cached to invalidate (both objects ARE the per-thread caches this function
clears), so it is skipped, with one "[inval-null]" line (at most 8 a run) that names the thread state and its first members, which
the log maps to a tid through its "[wow64-tls] ... ThreadState" line. For every thread of a working session both pointers are set
(the walk dereferenced both on every free before, and a NULL would have faulted at once), so only a thread that would have faulted
takes the new branch.

Applied by tools/build-xtajit-wow64.sh and tools/build-xtajit64.sh (FEXCore is built into both modules; each restores Core.cpp after
its build); idempotent; a moved anchor refuses the patch and the build goes on without it (a warning).
Usage: patch-fex-ios-inval-null-thread.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "inval-null-thread-v1 madeira-bcd"
CORE = "FEXCore/Source/Interface/Core/Core.cpp"

ANCHOR = ("void ContextImpl::InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {\n"
          "  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, \"CodeInvalidationMutex needs to be unique_locked here\");\n")
GUARD = ("#ifdef FEX_IOS_HOST\n"
         "  /* " + MARKER + " (tools/patch-fex-ios-inval-null-thread.py, round 630, Outlast Y09766): a thread without a decoder or a\n"
         "   * lookup cache has nothing cached to drop; walking into it faulted with this mutex and the thread mutex held, and every game\n"
         "   * thread then waited for ever. Skip it, and say so (at most 8 times a run). */\n"
         "  if (!Thread || !Thread->FrontendDecoder || !Thread->LookupCache) {\n"
         "    static std::atomic<unsigned> NullN {0};\n"
         "    if (NullN.fetch_add(1, std::memory_order_relaxed) < 8) {\n"
         "      LogMan::Msg::EFmt(\"[inval-null] Thread={} CurrentFrame={} Decoder={} Lookup={} range={:#x}+{:#x}: a thread state without its \"\n"
         "                        \"caches is skipped rev=r630\",\n"
         "                        static_cast<void*>(Thread), Thread ? static_cast<void*>(Thread->CurrentFrame) : nullptr,\n"
         "                        Thread ? static_cast<void*>(Thread->FrontendDecoder.get()) : nullptr,\n"
         "                        Thread ? static_cast<void*>(Thread->LookupCache.get()) : nullptr, Start, Length);\n"
         "    }\n"
         "    return;\n"
         "  }\n"
         "#endif\n")


def patch(src):
    if MARKER in src:
        if src.count(ANCHOR + GUARD) != 1:
            raise ValueError("partial or changed inval-null-thread")
        return src
    if src.count(ANCHOR) != 1:
        raise ValueError("ContextImpl::InvalidateThreadCachedCodeRange's first lines not found once (FEX changed)")
    return src.replace(ANCHOR, ANCHOR + GUARD, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / CORE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-inval-null-thread: " + str(error))
    if result == source:
        print("patch-fex-ios-inval-null-thread: already in")
    else:
        path.write_text(result)
        print("FEX: the code-invalidation walk skips a thread state without its caches ([inval-null])")
