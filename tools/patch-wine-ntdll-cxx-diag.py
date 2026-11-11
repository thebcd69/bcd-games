#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The PE ntdll's `[exc] ml811` block names the type of an MSVC C++ throw (night round 2026-11-11, G8 S9; log only).

Rise of the Tomb Raider (Steam 391220, BCD Dock, 64-bit ARM64EC; Send Log 2Q4BH0, build 630): 47 s into the run the game's own crash reporter prints
`[Crash] Unhandled exception at address 0x...8110 (code: 0xe06d7363): "Unknown exception"` and the log cannot say WHICH C++ type was thrown. 106 logs of
2026-11-03..11-10 contain `[exc] ml811 code=e06d7363`. The decoder that exists (`[cxx-type]`, build/ntdll-unix/thread_ios.c ios_cxx_throw_diag: thrown type,
catchable bases, std::exception's what(), the HRESULT of an ATL or COM exception) sits in the UNIX NtRaiseException, which a 64-bit throw that the program's own
handler takes never reaches (the PE ntdll dispatches it first): `grep -c '[cxx-type]'` is 0 in 2Q4BH0, while a 32-bit WoW64 log has two.

This adds, to dlls/ntdll/signal_arm64ec.c right after the 0x20474343 branch of the ml811 block (inside its recursion guard, at most 4 hits per code), a branch for
0xE06D7363 with four parameters (magic, object, ThrowInfo, image base) that prints
  [exc] ml811   MSVC C++ throw: thrown .?AVruntime_error@std@@; also catchable as: .?AVexception@std@@ (2 catchable type(s), ThrowInfo 0x..., attributes 0x0)
  [exc] ml811   MSVC C++ throw what="..."          (a std:: type: the message of std::exception)
  [exc] ml811   MSVC C++ throw ATL::CAtlException hr=0x80004005 | _com_error hr=0x...
with the same safe reads as every other branch of the block (NtReadVirtualMemory; no allocation, no lock, no fault) and the layouts of ios_cxx_throw_diag.

WHERE THIS REACHES: the 64-bit ntdll.dll BCD ships is a committed binary that no CI step rebuilds, so a source patch reaches a phone only through a variant built by
tools/build-wine-extra-dlls.sh (ntdll-sync.dll and ntdll-xsec.dll are such variants: opt-in, built only when this tree, unpatched, rebuilds the shipped file apart from
time stamps) or when the owner rebuilds the committed ntdll.dll. NOTHING applies this patch yet; see night-round note W-WINE.md (G8-S9).
Idempotent; every anchor is checked before the file is written; fails by name when a place moved.
Usage: patch-wine-ntdll-cxx-diag.py WINE-DIR | path/to/wine/dlls/ntdll/signal_arm64ec.c
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: msvc-throw-diag"
FILE = "dlls/ntdll/signal_arm64ec.c"

HELPER_ANCHOR = "static int ios_exc_dump_str( const char *what, ULONG_PTR addr, SIZE_T len, int wide )\n{\n"
HELPER = r'''/* ''' + MARKER + r''' (tools/patch-wine-ntdll-cxx-diag.py, night round 2026-11-11, Rise of the Tomb Raider 2Q4BH0): the decorated name of an MSVC type_info
 * (64-bit layout: vtable, runtime data, name at +16), read fault-safe. */
static void ios_msvc_type_name( ULONG_PTR type_info, char *out, size_t outsz )
{
    char raw[72];
    SIZE_T got = 0, i;

    out[0] = '?'; out[1] = 0;
    memset( raw, 0, sizeof(raw) );
    if (NtReadVirtualMemory( GetCurrentProcess(), (void *)(type_info + 16), raw, sizeof(raw) - 1, &got ) || !got) return;
    for (i = 0; i < sizeof(raw) - 1 && raw[i] && i < outsz - 1; i++)
        out[i] = (raw[i] >= 0x20 && raw[i] < 0x7f) ? raw[i] : '?';
    out[i] = 0;
    if (!i) { out[0] = '?'; out[1] = 0; }
}

'''
BRANCH_ANCHOR = ('                    else\n'
                 '                        ERR( "[exc] ml811   param[0]=%p is not readable as an unwind header\\n",\n'
                 '                             (void *)ue );\n'
                 '                }\n'
                 '            }\n'
                 '            InterlockedExchange( &in_probe, 0 );\n')
BRANCH_NEW = (
    '                    else\n'
    '                        ERR( "[exc] ml811   param[0]=%p is not readable as an unwind header\\n",\n'
    '                             (void *)ue );\n'
    '                }\n'
    '\n'
    '                /* ' + MARKER + ' (night round 2026-11-11, Rise of the Tomb Raider 2Q4BH0): an MSVC C++ throw carries { magic, object, ThrowInfo,\n'
    '                 * image base }; ThrowInfo { attributes, pmfnUnwind, pForwardCompat, pCatchableTypeArray } and everything below are 32-bit RVAs from the\n'
    '                 * image base. Same reads and layouts as ios_cxx_throw_diag (build/ntdll-unix/thread_ios.c), which the unix side runs for throws that\n'
    '                 * reach NtRaiseException; this one is for the throws the program handles itself. */\n'
    '                if (rec->ExceptionCode == 0xe06d7363 && rec->NumberParameters >= 4)\n'
    '                {\n'
    '                    ULONG_PTR obj = rec->ExceptionInformation[1], tinfo = rec->ExceptionInformation[2], base = rec->ExceptionInformation[3];\n'
    '                    ULONG ti[4], count = 0, refs[8], ct[7], attrs = 0, k, want;\n'
    '                    char first[72], list[160], name[72];\n'
    '                    SIZE_T got = 0, len = 0;\n'
    '\n'
    '                    strcpy( first, "? (ThrowInfo or its type list not readable)" );\n'
    '                    list[0] = 0;\n'
    '                    if (tinfo >= 0x10000 && !NtReadVirtualMemory( GetCurrentProcess(), (void *)tinfo, ti, sizeof(ti), &got ) && got == sizeof(ti))\n'
    '                    {\n'
    '                        ULONG_PTR cta = base + ti[3];\n'
    '\n'
    '                        attrs = ti[0];\n'
    '                        if (ti[3] && !NtReadVirtualMemory( GetCurrentProcess(), (void *)cta, &count, sizeof(count), &got ) && got == sizeof(count))\n'
    '                        {\n'
    '                            want = count > 8 ? 8 : count;\n'
    '                            if (want && !NtReadVirtualMemory( GetCurrentProcess(), (void *)(cta + 4), refs, want * 4, &got ) && got == want * 4)\n'
    '                                for (k = 0; k < want; k++)\n'
    '                                {\n'
    '                                    if (!refs[k] || NtReadVirtualMemory( GetCurrentProcess(), (void *)(base + refs[k]), ct, sizeof(ct), &got )\n'
    '                                        || got != sizeof(ct)) continue;   /* { properties, pType, PMD x3, size, copy } */\n'
    '                                    ios_msvc_type_name( base + ct[1], name, sizeof(name) );\n'
    '                                    if (!k) strcpy( first, name );\n'
    '                                    else if (len < sizeof(list) - 80) len += snprintf( list + len, sizeof(list) - len, " %s", name );\n'
    '                                }\n'
    '                        }\n'
    '                    }\n'
    '                    ERR( "[exc] ml811   MSVC C++ throw: thrown %s; also catchable as:%s (%u catchable type(s), ThrowInfo %p, attributes %#x, object %p)\\n",\n'
    '                         first, list[0] ? list : " nothing", (unsigned)count, (void *)tinfo, (unsigned)attrs, (void *)obj );\n'
    '                    {\n'
    '                        ULONG hr = 0;\n'
    '                        ULONG_PTR what = 0;\n'
    '                        char text[81];\n'
    '\n'
    '                        if (strstr( first, "CAtlException@ATL" ) && !NtReadVirtualMemory( GetCurrentProcess(), (void *)obj, &hr, 4, &got ) && got == 4)\n'
    '                            ERR( "[exc] ml811   MSVC C++ throw ATL::CAtlException hr=%#x\\n", (unsigned)hr );\n'
    '                        else if (strstr( first, "_com_error@@" ) && !NtReadVirtualMemory( GetCurrentProcess(), (void *)(obj + 8), &hr, 4, &got ) && got == 4)\n'
    '                            ERR( "[exc] ml811   MSVC C++ throw _com_error hr=%#x\\n", (unsigned)hr );\n'
    '                        else if (strstr( first, "@std@@" ) && !NtReadVirtualMemory( GetCurrentProcess(), (void *)(obj + 8), &what, sizeof(what), &got )\n'
    '                                 && got == sizeof(what) && what >= 0x10000)\n'
    '                        {\n'
    '                            memset( text, 0, sizeof(text) );\n'
    '                            if (!NtReadVirtualMemory( GetCurrentProcess(), (void *)what, text, 80, &got ) ||\n'
    '                                !NtReadVirtualMemory( GetCurrentProcess(), (void *)what, text, 16, &got ))\n'
    '                            {\n'
    '                                for (k = 0; k < 80 && text[k]; k++) if (text[k] < 0x20 || text[k] > 0x7e) text[k] = \'?\';\n'
    '                                text[k] = 0;\n'
    '                                ERR( "[exc] ml811   MSVC C++ throw what=\\"%s\\"\\n", text );\n'
    '                            }\n'
    '                        }\n'
    '                    }\n'
    '                }\n'
    '            }\n'
    '            InterlockedExchange( &in_probe, 0 );\n')


def patch(src):
    if MARKER in src:
        if src.count(HELPER) != 1 or src.count(BRANCH_NEW) != 1:
            raise ValueError("partial or changed msvc throw diagnostic patch")
        return src
    if src.count(HELPER_ANCHOR) != 1:
        raise ValueError("ios_exc_dump_str of signal_arm64ec.c not found once (Wine changed)")
    if src.count(BRANCH_ANCHOR) != 1:
        raise ValueError("the end of the 0x20474343 branch of the ml811 block not found once (Wine changed)")
    if not src.index(HELPER_ANCHOR) < src.index(BRANCH_ANCHOR):
        raise ValueError("ml811 block and ios_exc_dump_str are in another order (Wine changed)")
    return src.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1).replace(BRANCH_ANCHOR, BRANCH_NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-ntdll-cxx-diag.py WINE-DIR | path/to/wine/dlls/ntdll/signal_arm64ec.c")
    arg = Path(sys.argv[1])
    path = arg if arg.suffix == ".c" else arg / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-ntdll-cxx-diag: " + str(error))
    if result == source:
        print("patch-wine-ntdll-cxx-diag: already patched")
    else:
        path.write_text(result)
        print("patch-wine-ntdll-cxx-diag: patched")


if __name__ == "__main__":
    main()
