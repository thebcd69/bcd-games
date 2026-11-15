#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The 64-bit kernel32's GetTickCount and GetTickCount64 read KUSER_SHARED_DATA where it really is (build 663).

Wine's dlls/kernel32/sync.c reads the tick count through the constant 0x7ffe0000. On iOS nothing can be mapped there (the
4 GB floor), so in a 64-bit (ARM64EC) session every call took a Mach exception that BCD's sub-floor service answered from the
real page ("[subfloor] ml956 serviced": RDR2 XBB632, 10,313,729 reads in 516 s, about 20,000 a second, all at 0x7ffe0320 from
kernel32's `ldr w0, [x8]`; SurrounDead J7VKSE 52.6 million in an hour). A fault per call costs tens of microseconds of a
performance core; a game that asks for the time tens of thousands of times a second loses a large part of a core to it.

The patched kernel32 asks BCD once per process where the page really is (NtQueryInformationProcess with BCD's private class
1011, answered by build/ntdll-unix/process_ios.c) and reads the same bytes from there: the values are the ones the fault
service returned, without the fault. Any answer but success with an address keeps 0x7ffe0000, as before (a success with 0 is
asked again at the next call). ARM64EC and ARM64 builds only; the i386 kernel32 is the same code as before (its guest window
holds the page at 0x7ffe0000).

Applied by tools/build-wine-extra-dlls.sh to its own build of kernel32 (kernel32-usd.dll), then undone; the shipped
kernel32.dll stays as it is, and app/Madeira/WineProcessBridge.m links kernel32-usd.dll as kernel32.dll unless
env.MADEIRA_USD_DIRECT = 0.

Usage: patch-wine-kernel32-usd-direct.py [path/to/wine/dlls/kernel32/sync.c]
Idempotent; exits non-zero, writing nothing, when the line it replaces moved.
"""
import sys

MARKER = "madeira-bcd kernel32 usd-direct"

OLD = "static const struct _KUSER_SHARED_DATA *user_shared_data = (struct _KUSER_SHARED_DATA *)0x7ffe0000;\n"
NEW = """#if defined(__aarch64__) || defined(__arm64ec__)
/* """ + MARKER + """ (build 663; tools/patch-wine-kernel32-usd-direct.py): on iOS nothing can be mapped at 0x7ffe0000, so
 * every tick-count read there took a fault that BCD answered from the real page (about 20,000 a second in RDR2). BCD says once
 * per process where that page is (private class 1011); the same bytes are read from there. A failed question keeps 0x7ffe0000. */
static const struct _KUSER_SHARED_DATA *bcd_usd;

static const struct _KUSER_SHARED_DATA *bcd_user_shared_data(void)
{
    const struct _KUSER_SHARED_DATA *usd = __atomic_load_n( &bcd_usd, __ATOMIC_RELAXED );

    if (!usd)
    {
        ULONG_PTR real = 0;

        if (NtQueryInformationProcess( GetCurrentProcess(), (PROCESSINFOCLASS)1011, &real, sizeof(real), NULL ))
            real = 0x7ffe0000;   /* not BCD's answer: the old address, for good */
        else if (!real)
            return (const struct _KUSER_SHARED_DATA *)0x7ffe0000;   /* not known yet: asked again next time */
        usd = (const struct _KUSER_SHARED_DATA *)real;
        __atomic_store_n( &bcd_usd, usd, __ATOMIC_RELAXED );
    }
    return usd;
}
#define user_shared_data (bcd_user_shared_data())
#else
static const struct _KUSER_SHARED_DATA *user_shared_data = (struct _KUSER_SHARED_DATA *)0x7ffe0000;
#endif
"""


def patch(source):
    if MARKER in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError("the constant user_shared_data line was not found exactly once (found %d): sync.c moved" % source.count(OLD))
    return source.replace(OLD, NEW)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/kernel32/sync.c"
    source = open(path).read()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-kernel32-usd-direct: %s (%s)" % (error, path))
    if result == source:
        print("patch-wine-kernel32-usd-direct: already patched")
    else:
        open(path, "w").write(result)
        print("patch-wine-kernel32-usd-direct: applied to " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
