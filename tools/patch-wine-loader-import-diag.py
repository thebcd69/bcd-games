#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[import-stub] and [delay-fail] detail: what the exporting module looks like when an import finds no export (round 660, M024 (d) and M093; log only).

The Witcher 3 (DirectX 12 build; Send Logs EHMN4F, ZQ37NG, build 653) ends with "Call from ... to unimplemented function steam_api64.dll.SteamAPI_Init,
aborting": the game's Steam plug-in imports SteamAPI_Init from a steam_api64.dll that is loaded, and the loader finds no such export. Whether the DLL's
export table is damaged, unsorted (the loader looks a name up by binary search), or the name is simply absent cannot be told from the log. A Chromium-type
program (DJFYCR) ends on a winmm delay-load ("[delay-fail] ... target=winmm.dll"), and the line names neither the file that was loaded nor its export table.

dlls/ntdll/loader.c gets one helper, ios_import_probe, and three calls:
  - import_dll: where an import finds no export and gets a stub (the four branches: by name / by ordinal, with and without an export directory), one
    "[import-stub]" line per (importer, exporter) pair, 8 pairs at most per process:
      [import-stub] IMPORTER imports EXPORTER.NAME (file PATH): importer base=P exporter base=P size=S machine=M export-dir=BYTES names=N funcs=F
      base-ordinal=B | bsearch=I walk=J rva=R | nearest: LO .. HI
    bsearch is the loader's own lookup (-1 = not found), walk a plain scan of every name (an export table that is not sorted fails the first and
    passes the second), rva 0 = the slot is empty, LO and HI the names on either side of the missing one. The probe reads inside __TRY: a module
    whose tables cannot be read prints one line saying so.
  - LdrResolveDelayLoadedAPI's failure path: after the existing "[delay-fail] rev=ml334 ..." line (kept as it is: the log reader's rules read it), when the
    target module is loaded, a "[delay-fail] detail ..." line with the same probe and the file the module was loaded from.
  - ios_import_stub_caller (new, called by dispatch_exception's EXCEPTION_WINE_STUB case before its "wine: Call from ..." message, which is kept
    word for word: the known-stop rule witcher3-steam-plugin-import-stub reads it): "[import-stub] call from ADDR is in MODULE (base, +offset) to a stub
    for DLL" for the first 8 stub calls of a process, taking the loader lock only when it is free.
Nothing else changes: no import is bound differently, no call is made when every import resolves. Keyless (a diagnostic that prints only where a stub
is already being made, or a delay-load has already failed).

WHERE THIS REACHES: the 32-bit ntdll through the i386 farm (build-ipa.yml puts the files back after it, as for the other ntdll scripts), and the
64-bit ntdll through the rebuilt set (tools/build-wine-fresh-dlls.sh, env.MADEIRA_WIN64_FRESH = 1): the committed 64-bit ntdll.dll is a frozen binary.
Idempotent; fails by name when an anchor moved.
Usage: patch-wine-loader-import-diag.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: [import-stub]"
LOADER = "dlls/ntdll/loader.c"
EXCEPTION = "dlls/ntdll/exception.c"

HELPER_ANCHOR = "static BOOL import_dll( WINE_MODREF *wm, const IMAGE_IMPORT_DESCRIPTOR *descr, LPCWSTR load_path, WINE_MODREF **pwm )\n{\n"
HELPER = r'''/* madeira-bcd: [import-stub] and [delay-fail] detail (tools/patch-wine-loader-import-diag.py, round 660 M024 and M093; log only).
 * What the exporting module looks like when an import found no export. NAME is the imported name, or NULL for an import by ordinal. */
static void ios_import_probe( const char *tag, const char *what, WINE_MODREF *importer, WINE_MODREF *exporter,
                              const char *name, ULONG ordinal )
{
    HMODULE mod = exporter->ldr.DllBase;
    const IMAGE_EXPORT_DIRECTORY *exports = NULL;
    const IMAGE_NT_HEADERS *nt;
    DWORD exp_size = 0, i, rva = 0;
    const DWORD *names, *functions;
    const WORD *ordinals;
    const char *lo = NULL, *hi = NULL;
    int bsearch_ord = -1, walk_ord = -1;

    __TRY
    {
        nt = RtlImageNtHeader( mod );
        exports = RtlImageDirectoryEntryToData( mod, TRUE, IMAGE_DIRECTORY_ENTRY_EXPORT, &exp_size );
        if (exports && exp_size >= sizeof(*exports))
        {
            names = get_rva( mod, exports->AddressOfNames );
            ordinals = get_rva( mod, exports->AddressOfNameOrdinals );
            functions = get_rva( mod, exports->AddressOfFunctions );
            if (name)
            {
                bsearch_ord = find_name_in_exports( mod, exports, name );
                for (i = 0; i < exports->NumberOfNames; i++)
                {
                    const char *n = get_rva( mod, names[i] );
                    int c = strcmp( n, name );
                    if (!c) walk_ord = ordinals[i];
                    else if (c < 0) { if (!lo || strcmp( n, lo ) > 0) lo = n; }
                    else if (!hi || strcmp( n, hi ) < 0) hi = n;
                }
                if (walk_ord >= 0 && (DWORD)walk_ord < exports->NumberOfFunctions) rva = functions[walk_ord];
            }
            else if (ordinal >= exports->Base && ordinal - exports->Base < exports->NumberOfFunctions)
                rva = functions[ordinal - exports->Base];
        }
        ERR( "%s %s (file %s): importer base=%p exporter base=%p size=%#x machine=%#x export-dir=%u names=%u funcs=%u base-ordinal=%u"
             " | bsearch=%d walk=%d rva=%#x | nearest: %s .. %s\n",
             tag, what, debugstr_w( exporter->ldr.FullDllName.Buffer ), importer ? importer->ldr.DllBase : NULL, mod,
             (unsigned int)exporter->ldr.SizeOfImage, nt ? nt->FileHeader.Machine : 0, (unsigned int)exp_size,
             exports && exp_size >= sizeof(*exports) ? (unsigned int)exports->NumberOfNames : 0,
             exports && exp_size >= sizeof(*exports) ? (unsigned int)exports->NumberOfFunctions : 0,
             exports && exp_size >= sizeof(*exports) ? (unsigned int)exports->Base : 0,
             bsearch_ord, walk_ord, (unsigned int)rva, debugstr_a( lo ), debugstr_a( hi ));
    }
    __EXCEPT_PAGE_FAULT
    {
        ERR( "%s %s (file %s): the export directory of %p could not be read\n", tag, what,
             debugstr_w( exporter->ldr.FullDllName.Buffer ), mod );
    }
    __ENDTRY
}

/* one "[import-stub]" line per (importer, exporter) pair, 8 pairs a process; called with the loader lock held, from import_dll */
static void ios_import_stub_diag( WINE_MODREF *importer, WINE_MODREF *exporter, const char *dll, const char *name, ULONG ordinal )
{
    static const void *seen[8][2];
    static unsigned int nseen;
    const char *what;
    unsigned int i;

    for (i = 0; i < nseen; i++) if (seen[i][0] == importer && seen[i][1] == exporter) return;
    if (nseen >= ARRAY_SIZE(seen)) return;
    seen[nseen][0] = importer;
    seen[nseen][1] = exporter;
    nseen++;
    if (name) what = wine_dbg_sprintf( "%s imports %s.%s", debugstr_w( importer->ldr.BaseDllName.Buffer ), dll, name );
    else what = wine_dbg_sprintf( "%s imports %s.#%u", debugstr_w( importer->ldr.BaseDllName.Buffer ), dll, (unsigned int)ordinal );
    ios_import_probe( "[import-stub]", what, importer, exporter, name, ordinal );
}

/* the module that called a stub (dispatch_exception's EXCEPTION_WINE_STUB): the first 8 calls of a process; the loader lock only when it is free */
void ios_import_stub_caller( void *pc, const char *dll )
{
    static LONG said;
    LDR_DATA_TABLE_ENTRY *mod;

    if (InterlockedIncrement( &said ) > 8) return;
    if (!RtlTryEnterCriticalSection( &loader_section ))
    {
        ERR( "[import-stub] call from %p to a stub for %s (the loader lock is busy: module not looked up)\n", pc, debugstr_a( dll ) );
        return;
    }
    if (!LdrFindEntryForAddress( pc, &mod ))
        ERR( "[import-stub] call from %p is in %s (base %p, +%#x) to a stub for %s\n", pc, debugstr_w( mod->FullDllName.Buffer ),
             mod->DllBase, (unsigned int)((char *)pc - (char *)mod->DllBase), debugstr_a( dll ) );
    else
        ERR( "[import-stub] call from %p is in no module, to a stub for %s\n", pc, debugstr_a( dll ) );
    RtlLeaveCriticalSection( &loader_section );
}

'''

# (old, new) pairs inside import_dll, each exactly once: the call goes right after the allocate_stub line of its branch
SITES = [
    ("""                WARN("No implementation for %s.%d", name, ordinal );
                thunk_list->u1.Function = allocate_stub( name, IntToPtr(ordinal) );
""", """                WARN("No implementation for %s.%d", name, ordinal );
                thunk_list->u1.Function = allocate_stub( name, IntToPtr(ordinal) );
                ios_import_stub_diag( wm, wmImp, name, NULL, ordinal );   /* madeira-bcd: [import-stub] */
"""),
    ("""                WARN("No implementation for %s.%s", name, pe_name->Name );
                thunk_list->u1.Function = allocate_stub( name, (const char*)pe_name->Name );
""", """                WARN("No implementation for %s.%s", name, pe_name->Name );
                thunk_list->u1.Function = allocate_stub( name, (const char*)pe_name->Name );
                ios_import_stub_diag( wm, wmImp, name, (const char *)pe_name->Name, 0 );   /* madeira-bcd: [import-stub] */
"""),
    ("""                thunk_list->u1.Function = allocate_stub( name, IntToPtr(ordinal) );
                WARN("No implementation for %s.%d imported from %s, setting to %p\\n",
""", """                thunk_list->u1.Function = allocate_stub( name, IntToPtr(ordinal) );
                ios_import_stub_diag( wm, wmImp, name, NULL, ordinal );   /* madeira-bcd: [import-stub] */
                WARN("No implementation for %s.%d imported from %s, setting to %p\\n",
"""),
    ("""                thunk_list->u1.Function = allocate_stub( name, (const char*)pe_name->Name );
                WARN("No implementation for %s.%s imported from %s, setting to %p\\n",
""", """                thunk_list->u1.Function = allocate_stub( name, (const char*)pe_name->Name );
                ios_import_stub_diag( wm, wmImp, name, (const char *)pe_name->Name, 0 );   /* madeira-bcd: [import-stub] */
                WARN("No implementation for %s.%s imported from %s, setting to %p\\n",
"""),
]

DELAY_OLD = """        ERR( "[delay-fail] rev=ml334 importer=%p target=%s import=%s status=%08x hmod=%p"
             " -- Chromium's DelayLoad-ModuleName hook kills the process on this\\n",
             base, debugstr_a(name), debugstr_a(imp), nts, *phmod );
"""
DELAY_NEW = DELAY_OLD + """        /* madeira-bcd: [import-stub] (tools/patch-wine-loader-import-diag.py, round 660 M093): the file the target was loaded from and its export table */
        if (*phmod)
        {
            WINE_MODREF *target = get_modref( *phmod );

            if (target)
            {
                const char *what = wine_dbg_sprintf( "detail: %s.%s", debugstr_a(name), debugstr_a(imp) );

                ios_import_probe( "[delay-fail]", what, get_modref( base ),
                                  target, IMAGE_SNAP_BY_ORDINAL(pINT[id].u1.Ordinal) ? NULL : imp,
                                  LOWORD(pINT[id].u1.Ordinal) );
            }
        }
"""

EXC_OLD = """    case EXCEPTION_WINE_STUB:
        if (unconverted_guest_ptr( rec->ExceptionInformation[0] )) break;
"""
EXC_NEW = EXC_OLD + """        {   /* madeira-bcd: [import-stub] (tools/patch-wine-loader-import-diag.py, round 660 M024): which module called the stub */
            extern void ios_import_stub_caller( void *pc, const char *dll );
            ios_import_stub_caller( rec->ExceptionAddress, (const char *)rec->ExceptionInformation[0] );
        }
"""


def main():
    wine = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    loader = wine / LOADER
    exc = wine / EXCEPTION
    text = loader.read_text()
    etext = exc.read_text()
    if MARKER in text and MARKER in etext:
        print("loader import diagnostics: already applied")
        return
    if MARKER in text or MARKER in etext:
        sys.exit("patch-wine-loader-import-diag: only one of loader.c and exception.c carries the patch")
    if text.count(HELPER_ANCHOR) != 1:
        sys.exit("patch-wine-loader-import-diag: the head of import_dll was not found exactly once in " + str(loader))
    for old, _ in SITES:
        if text.count(old) != 1:
            sys.exit("patch-wine-loader-import-diag: a stub branch of import_dll was not found exactly once (%r) in %s" % (old.splitlines()[-1].strip(), loader))
    if text.count(DELAY_OLD) != 1:
        sys.exit("patch-wine-loader-import-diag: the [delay-fail] ERR was not found exactly once in " + str(loader))
    if etext.count(EXC_OLD) != 1:
        sys.exit("patch-wine-loader-import-diag: the EXCEPTION_WINE_STUB case was not found exactly once in " + str(exc))
    text = text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR)
    for old, new in SITES:
        text = text.replace(old, new)
    text = text.replace(DELAY_OLD, DELAY_NEW)
    loader.write_text(text)
    exc.write_text(etext.replace(EXC_OLD, EXC_NEW))
    print("loader import diagnostics: applied")


if __name__ == "__main__":
    main()
