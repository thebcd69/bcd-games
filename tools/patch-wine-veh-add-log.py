#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[veh] add: which of a program's own modules registers an exception handler (round 624, agent A S13; log only).

osu! (SV6C4C), Deadpool and Diablo II end after an exception that one of the program's handlers took and did not resolve, and no log could say
whose handler it was: Wine's existing "[veh]" lines print a handler only while it runs, under arm64ec, for access violations. This logs the
registration itself, where the owning module is known:

  ntdll  RtlAddVectoredExceptionHandler / RtlAddVectoredContinueHandler   "[veh] add exception|continue first=N handler=P = module+0xRVA"
  kernelbase  SetUnhandledExceptionFilter                                 "[veh] add unhandled-filter first=0 handler=P = module+0xRVA"

A handler inside Windows' or Wine's own files (a path under \\windows\\) is left out, so the lines are the program's (its engine, Mono, a crash
reporter, a protection layer); a handler in no module (JIT code) is logged as such. 16 lines a process at most, per file; the registration
itself is unchanged.

Applied for the i386 farm only (32-bit processes: osu!, Diablo II and Deadpool are 32-bit) and put back right after it
(.github/workflows/build-ipa.yml): the 64-bit ntdll.dll and kernelbase.dll BCD ships are committed binaries, and the arm64ec builds of the
opt-in ntdll/kernelbase variants (tools/build-wine-extra-dlls.sh) first rebuild an unpatched ntdll/kernelbase and compare it byte for byte
with them.

Usage: patch-wine-veh-add-log.py <wine source root> (idempotent: a second run says "already applied").
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: [veh] add"

HELPER = r'''
/* madeira-bcd: [veh] add (tools/patch-wine-veh-add-log.py, round 624, agent A S13: osu! SV6C4C, Deadpool, Diablo II): which of the
 * program's own modules registers an exception handler, so a log can say whose handler took an exception. Windows' and Wine's own files
 * (a path under \windows\) are left out; a handler in no module (JIT code) says so. 16 lines a process at most; log only. */
static BOOL bcd_veh_system_path( const UNICODE_STRING *path )
{
    static const WCHAR win[] = L"\\windows\\";
    unsigned int i, j, n = path->Buffer ? path->Length / sizeof(WCHAR) : 0;

    for (i = 0; i + 9 <= n; i++)
    {
        for (j = 0; j < 9; j++)
        {
            WCHAR c = path->Buffer[i + j];
            if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
            if (c != win[j]) break;
        }
        if (j == 9) return TRUE;
    }
    return FALSE;
}

static void bcd_veh_log_owner( const char *what, ULONG first, const void *func )
{
    static LONG told;
    LDR_DATA_TABLE_ENTRY *mod;

    if (!func || told >= 16) return;
    if (LdrFindEntryForAddress( func, &mod ))
    {
        if (InterlockedIncrement( &told ) <= 16)
            ERR( "[veh] add %s first=%u handler=%p in no module (generated code)\n", what, (unsigned int)first, func );
        return;
    }
    if (bcd_veh_system_path( &mod->FullDllName )) return;
    if (InterlockedIncrement( &told ) > 16) return;
    ERR( "[veh] add %s first=%u handler=%p = %s+%#lx\n", what, (unsigned int)first, func, debugstr_w( mod->BaseDllName.Buffer ),
         (unsigned long)((const char *)func - (const char *)mod->DllBase) );
}
'''

NT_ANCHOR = "static VECTORED_HANDLER *add_vectored_handler( LIST_ENTRY *handler_list, ULONG first,\n"
NT_CONT_OLD = """PVOID WINAPI RtlAddVectoredContinueHandler( ULONG first, PVECTORED_EXCEPTION_HANDLER func )
{
    return add_vectored_handler( &vectored_continue_handlers, first, func );
}"""
NT_CONT_NEW = """PVOID WINAPI RtlAddVectoredContinueHandler( ULONG first, PVECTORED_EXCEPTION_HANDLER func )
{
    bcd_veh_log_owner( "continue", first, func );   /* madeira-bcd: [veh] add */
    return add_vectored_handler( &vectored_continue_handlers, first, func );
}"""
NT_EXC_OLD = """PVOID WINAPI DECLSPEC_HOTPATCH RtlAddVectoredExceptionHandler( ULONG first, PVECTORED_EXCEPTION_HANDLER func )
{
    return add_vectored_handler( &vectored_exception_handlers, first, func );
}"""
NT_EXC_NEW = """PVOID WINAPI DECLSPEC_HOTPATCH RtlAddVectoredExceptionHandler( ULONG first, PVECTORED_EXCEPTION_HANDLER func )
{
    bcd_veh_log_owner( "exception", first, func );   /* madeira-bcd: [veh] add */
    return add_vectored_handler( &vectored_exception_handlers, first, func );
}"""

KB_ANCHOR = "static PTOP_LEVEL_EXCEPTION_FILTER top_filter;\n"
KB_OLD = """LPTOP_LEVEL_EXCEPTION_FILTER WINAPI DECLSPEC_HOTPATCH SetUnhandledExceptionFilter(
    LPTOP_LEVEL_EXCEPTION_FILTER filter )
{
    return InterlockedExchangePointer( (void **)&top_filter, filter );
}"""
KB_NEW = """LPTOP_LEVEL_EXCEPTION_FILTER WINAPI DECLSPEC_HOTPATCH SetUnhandledExceptionFilter(
    LPTOP_LEVEL_EXCEPTION_FILTER filter )
{
    bcd_veh_log_owner( "unhandled-filter", 0, filter );   /* madeira-bcd: [veh] add */
    return InterlockedExchangePointer( (void **)&top_filter, filter );
}"""


def once(text, old, new, what, path):
    if text.count(old) != 1:
        sys.exit("patch-wine-veh-add-log: anchor '%s' not found exactly once in %s" % (what, path))
    return text.replace(old, new)


def patch(path, edits):
    text = path.read_text()
    if MARKER in text:
        print("%s: [veh] add already applied" % path.name)
        return
    for old, new, what in edits:
        text = once(text, old, new, what, path)
    path.write_text(text)
    print("%s: [veh] add applied" % path.name)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-veh-add-log.py <wine source root>")
    wine = Path(sys.argv[1])
    patch(wine / "dlls/ntdll/exception.c", [
        (NT_ANCHOR, HELPER + "\n" + NT_ANCHOR, "add_vectored_handler"),
        (NT_CONT_OLD, NT_CONT_NEW, "RtlAddVectoredContinueHandler"),
        (NT_EXC_OLD, NT_EXC_NEW, "RtlAddVectoredExceptionHandler"),
    ])
    patch(wine / "dlls/kernelbase/debug.c", [
        (KB_ANCHOR, KB_ANCHOR + HELPER, "top_filter"),
        (KB_OLD, KB_NEW, "SetUnhandledExceptionFilter"),
    ])


if __name__ == "__main__":
    main()
