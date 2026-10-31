#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[msgbox-origin] and [pf-xmmi64]: find which CPU check Burnout Paradise fails (madeira-bcd; logging only).

Burnout Paradise: The Ultimate Box (Steam app 24740, 32-bit BurnoutParadise.exe; build 574 log AQY7NM) shows its
own "CPU Error" box, "This machine does not support the SSE2 Command Set ...", and exits. FEX's CPUID leaf 1
reports SSE2 (EDX bit 26, FEXCore CPUID.cpp Function_01h), and PF_XMMI64_INSTRUCTIONS_AVAILABLE is true on both
paths read in source: the Wine KUSER_SHARED_DATA table and, for a 32-bit process, wow64_NtWow64IsProcessorFeaturePresent
-> BTCpuIsProcessorFeaturePresent -> FEX CPUFeatures::IsFeaturePresent. Nothing in the log or the sources says
which check the game does, so this adds two log lines that say it on the next run:

1. user32 MessageBoxIndirectW (every MessageBox/MessageBoxEx/MessageBoxTimeout funnels here), i386 build only,
   first 4 boxes of a process: the return addresses on the stack that point into the main exe right after a call
   instruction (exe-relative RVA), and 192 bytes of the code in front of the first such return address, as hex.
   That is the function around the check (a cpuid, a call to IsProcessorFeaturePresent, an SSE2 probe under SEH),
   readable from the log without having the exe. The scan is wrapped in __TRY/__EXCEPT_PAGE_FAULT.
2. wow64 wow64_NtWow64IsProcessorFeaturePresent: ONE line per process, only when the answer for feature 10
   (PF_XMMI64) is FALSE, with whether the BTCpu hook is resolved. The return value is untouched.

Both run only when a box is shown / a feature answer is already FALSE; nothing else changes.

Usage: patch-wine-cpu-error-diag.py <wine source dir>. Idempotent; fails by name if an anchor moves.
"""
import pathlib
import sys

wine = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
MSGBOX = wine / "dlls/user32/msgbox.c"
WOW64 = wine / "dlls/wow64/syscall.c"
MARKER = "madeira-bcd: [msgbox-origin]"
PF_MARKER = "madeira-bcd: [pf-xmmi64]"

INC_OLD = '#include "wine/debug.h"\n'
INC_NEW = '#include "wine/debug.h"\n#include "wine/exception.h"\n'

CH_OLD = "WINE_DECLARE_DEBUG_CHANNEL(msgbox);\n"
CH_NEW = CH_OLD + r'''
#ifdef __i386__
/* madeira-bcd: [msgbox-origin] (tools/patch-wine-cpu-error-diag.py): who showed this box? */
static void madeira_msgbox_origin( const MSGBOXPARAMSW *mb )
{
    static LONG told;
    char *base = (char *)GetModuleHandleW( NULL );
    IMAGE_NT_HEADERS *nt;
    ULONG_PTR *sp, *top;
    const unsigned char *first = NULL;
    unsigned int n, hits = 0;
    SIZE_T size;

    if (InterlockedIncrement( &told ) > 4) return;
    if (!base || !(nt = RtlImageNtHeader( (HMODULE)base ))) return;
    size = nt->OptionalHeader.SizeOfImage;
    sp = (ULONG_PTR *)__builtin_frame_address( 0 );
    top = (ULONG_PTR *)NtCurrentTeb()->Tib.StackBase;
    ERR( "[msgbox-origin] box %s / %s: main exe base %p size %#lx\n", debugstr_w( mb->lpszCaption ),
         debugstr_w( mb->lpszText ), base, (unsigned long)size );
    __TRY
    {
        for (n = 0; sp < top && n < 2048 && hits < 8; sp++, n++)
        {
            ULONG_PTR v = *sp;
            const unsigned char *p = (const unsigned char *)v;

            if (v < (ULONG_PTR)base + 0x1000 + 6 || v >= (ULONG_PTR)base + size) continue;
            /* a return address: preceded by call rel32, call [mem32] or call reg */
            if (!(p[-5] == 0xe8 || (p[-6] == 0xff && p[-5] == 0x15) || (p[-2] == 0xff && (p[-1] & 0xf8) == 0xd0))) continue;
            ERR( "[msgbox-origin] stack+%#x: return into exe at rva %#lx\n", n * 4, (unsigned long)(v - (ULONG_PTR)base) );
            if (!first) first = p;
            hits++;
        }
        if (first && first - 0xc0 >= (const unsigned char *)base)
        {
            const unsigned char *c = first - 0xc0;
            unsigned int i, j;
            for (i = 0; i < 0xd0; i += 16)
            {
                char line[16 * 3 + 1];
                for (j = 0; j < 16; j++) snprintf( line + j * 3, 4, "%02x ", c[i + j] );
                ERR( "[msgbox-origin] code rva %#lx: %s\n", (unsigned long)(c + i - (const unsigned char *)base), line );
            }
        }
    }
    __EXCEPT_PAGE_FAULT
    {
        ERR( "[msgbox-origin] scan faulted\n" );
    }
    __ENDTRY
}
#else
/* madeira-bcd: no scan outside i386. The 64-bit user32.dll BCD ships (app/Madeira/arm64ec-windows/user32.dll) is a committed binary that
 * no step builds from this source, so a 64-bit scan here would never run (round 624: Khazan's box is a 64-bit process's; its evidence is
 * the unix side's [sub-floor-image] line and the box's own [win-name] line). */
static void madeira_msgbox_origin( const MSGBOXPARAMSW *mb ) { }
#endif
'''

CALL_OLD = """    if (!(hRes = FindResourceExW(user32_module, (LPWSTR)RT_DIALOG, L"MSGBOX", msgbox->dwLanguageId)))
    {
        if (!msgbox->dwLanguageId ||"""
CALL_NEW = """    madeira_msgbox_origin( msgbox );

""" + CALL_OLD

PF_OLD = """    UINT feature = get_ulong( &args );

    return pBTCpuIsProcessorFeaturePresent && pBTCpuIsProcessorFeaturePresent( feature );
}"""
PF_NEW = """    UINT feature = get_ulong( &args );
    BOOLEAN ret = pBTCpuIsProcessorFeaturePresent && pBTCpuIsProcessorFeaturePresent( feature );

    if (!ret && feature == PF_XMMI64_INSTRUCTIONS_AVAILABLE)
    {
        static LONG told;   /* madeira-bcd: [pf-xmmi64] (tools/patch-wine-cpu-error-diag.py) */
        if (!InterlockedExchange( &told, 1 ))
            ERR( "[pf-xmmi64] IsProcessorFeaturePresent(PF_XMMI64) answered FALSE: BTCpu hook %s\\n",
                 pBTCpuIsProcessorFeaturePresent ? "resolved (the backend said no)" : "NOT resolved" );
    }
    return ret;
}"""


def once(text, old, new, what):
    if text.count(old) != 1:
        sys.exit("patch-wine-cpu-error-diag: anchor '%s' not found exactly once" % what)
    return text.replace(old, new)


def main():
    t = MSGBOX.read_text()
    if MARKER in t:
        print("msgbox-origin diagnostics: already applied")
    else:
        t = once(t, INC_OLD, INC_NEW, "wine/debug.h include")
        t = once(t, CH_OLD, CH_NEW, "msgbox channel")
        t = once(t, CALL_OLD, CALL_NEW, "MessageBoxIndirectW body")
        MSGBOX.write_text(t)
        print("msgbox-origin diagnostics: applied")
    w = WOW64.read_text()
    if PF_MARKER in w:
        print("pf-xmmi64 diagnostics: already applied")
    else:
        WOW64.write_text(once(w, PF_OLD, PF_NEW, "wow64_NtWow64IsProcessorFeaturePresent"))
        print("pf-xmmi64 diagnostics: applied")


if __name__ == "__main__":
    main()
