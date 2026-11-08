#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""An executable heap hands out blocks the translator flushes (build 632; the "Terraria set", i386 farm only).

Terraria crashed right after making a character. A heap created with HEAP_CREATE_ENABLE_EXECUTE hands freed bytes out again, and the
program writes new code into them without flushing anything: x86 needs no instruction cache flush. Wine Mono's dynamic methods live in such a
heap. FEX has turned off its write trap for Mono's process by then, so it ran what it had translated from the old code.

On i386, RtlAllocateHeap calls NtFlushInstructionCache for each block an executable heap hands out, and RtlReAllocateHeap does the same for
a block grown in place (a moved one went through RtlAllocateHeap). Heaps without the flag, and every other architecture, are untouched.
MADEIRA_HEAP_EXEC_FLUSH=0 turns it off (read with the other MADEIRA_HEAP_* switches of heap_init_madeira_policy).

The code is upstream Madeira's wine commit e7685ffad ("ntdll: flush blocks handed out by an executable heap from the translator", by
ft7t8drgwf-wq, Wine fork madeira-lgpl, LGPL-2.1-or-later like the file it goes into); BCD applies it as this script instead of a commit in
its Wine fork, and for the i386 farm only (.github/workflows/build-ipa.yml puts heap.c back right after the farm, like the x64 machine
answer): the 64-bit ntdll.dll BCD ships is a committed binary and tools/build-wine-extra-dlls.sh builds ntdll-sync.dll and ntdll-xsec.dll
only when an unpatched rebuild of the pinned tree equals it byte for byte. Every added line of this change shifts the line number an
assert() in unsafe_heap_from_handle() carries into the ARM64EC object (849 -> 868 when measured), so as a commit in the fork it would stop
both opt-in DLLs from being built. Everything it adds is under "#ifdef __i386__" anyway.

Usage: patch-wine-heap-exec-flush.py [path/to/wine/dlls/ntdll/heap.c]
Idempotent ("already patched"); exits non-zero, naming the anchor, when heap.c has moved.
"""
import sys

MARKER = "madeira-bcd: executable heap flush"

# 1. the switch and the helper, after the policy bits
POLICY_OLD = "#define MADEIRA_HEAP_STATS 8\n\nvoid heap_init_madeira_policy(void)\n"
POLICY_NEW = """#define MADEIRA_HEAP_STATS 8

/* """ + MARKER + """ (tools/patch-wine-heap-exec-flush.py; upstream Madeira's wine commit e7685ffad).
 * A heap created with HEAP_CREATE_ENABLE_EXECUTE hands freed bytes out again, and the
 * program writes new code into them without telling anyone: x86 needs no instruction
 * cache flush. Wine Mono's dynamic methods live in such a heap. FEX has turned off its
 * write trap for Mono's process by then (FEX's ml1280), so it would run what it translated
 * from the old code. Each block handed out from such a heap is flushed from the
 * translator's cache. MADEIRA_HEAP_EXEC_FLUSH=0 turns this off. */
static BOOL madeira_heap_exec_flush = TRUE;

static inline void madeira_heap_flush_exec( const struct heap *heap, void *ptr, SIZE_T size )
{
    if (madeira_heap_exec_flush && ptr && (heap->flags & HEAP_CREATE_ENABLE_EXECUTE))
        NtFlushInstructionCache( NtCurrentProcess(), ptr, size );
}

void heap_init_madeira_policy(void)
"""

# 2. the switch, read where the other MADEIRA_HEAP_* switches are
READ_OLD = """            value.Length == sizeof(WCHAR) && buffer[0] == '1') madeira_heap_policy |= 1u << i;
    }
    if (madeira_heap_policy)
"""
READ_NEW = """            value.Length == sizeof(WCHAR) && buffer[0] == '1') madeira_heap_policy |= 1u << i;
    }
    RtlInitUnicodeString( &name, L"MADEIRA_HEAP_EXEC_FLUSH" );
    value.Buffer = buffer;
    value.MaximumLength = sizeof(buffer);
    if (!RtlQueryEnvironmentVariable_U( NULL, &name, &value ) &&
        value.Length == sizeof(WCHAR) && buffer[0] == '0') madeira_heap_exec_flush = FALSE;
    if (madeira_heap_policy)
"""

# 3. RtlAllocateHeap: after the reclaim retry, still inside its "#ifdef __i386__"
ALLOC_OLD = """        heap_unlock( heap, heap_flags );
        if (released) goto retry;
    }
#endif
    if (!status) valgrind_notify_alloc( ptr, size, flags & HEAP_ZERO_MEMORY );
"""
ALLOC_NEW = """        heap_unlock( heap, heap_flags );
        if (released) goto retry;
    }
    if (!status) madeira_heap_flush_exec( heap, ptr, size );
#endif
    if (!status) valgrind_notify_alloc( ptr, size, flags & HEAP_ZERO_MEMORY );
"""

# 4. RtlReAllocateHeap: a block grown in place takes in freed bytes too
REALLOC_OLD = """            RtlFreeHeap( heap, flags, ptr );
            status = STATUS_SUCCESS;
        }
    }

    TRACE( "handle %p, flags %#lx, ptr %p, size %#Ix, return %p, status %#lx.\\n", handle, flags, ptr, size, ret, status );
"""
REALLOC_NEW = """            RtlFreeHeap( heap, flags, ptr );
            status = STATUS_SUCCESS;
        }
    }
#ifdef __i386__
    /* A block grown in place takes in freed bytes too; a moved one was flushed by RtlAllocateHeap. */
    else madeira_heap_flush_exec( heap, ret, size );
#endif

    TRACE( "handle %p, flags %#lx, ptr %p, size %#Ix, return %p, status %#lx.\\n", handle, flags, ptr, size, ret, status );
"""

EDITS = (("the policy switch and helper", POLICY_OLD, POLICY_NEW),
         ("the switch's read in heap_init_madeira_policy", READ_OLD, READ_NEW),
         ("RtlAllocateHeap's end", ALLOC_OLD, ALLOC_NEW),
         ("RtlReAllocateHeap's end", REALLOC_OLD, REALLOC_NEW))


def apply(src):
    for what, old, new in EDITS:
        if src.count(old) != 1:
            raise SystemExit("patch-wine-heap-exec-flush: anchor %s not found exactly once (heap.c moved)" % what)
        src = src.replace(old, new)
    return src


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/heap.c"
    src = open(path).read()
    if MARKER in src:
        print("patch-wine-heap-exec-flush: already patched")
        return
    out = apply(src)   # before the file is opened for writing: a refusal must leave it as it was
    open(path, "w").write(out)
    print("patch-wine-heap-exec-flush: applied to " + path)


if __name__ == "__main__":
    main()
