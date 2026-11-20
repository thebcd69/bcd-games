#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[import-miss] and, opt-in, [import-appdir]: a DLL a module imports that the loader does not find (round 669, agent B S7).

Doki Doki Literature Club (Send Log N8E7GN, build 665): Python's pygame_sdl2 modules (font.pyd, image.pyd, _renpy.pyd) import SDL2_ttf.dll,
SDL2_image.dll, libjpeg-62.dll and libpng-3.dll, which lie in the program's own folder, and the loader reported "Library SDL2_ttf.dll (which is
needed by ...font.pyd) not found"; the program's own SDL2.dll loads from the same folder. Ren'Py then ends. What the loader searched is not in the log.

dlls/ntdll/loader.c gets one helper, ios_import_miss, called from import_dll when load_dll answered STATUS_DLL_NOT_FOUND:
  - "[import-miss] NAME needed by IMPORTER: load path PATH; application dir DIR; MADEIRA_IMPORT_APPDIR=0|1", 8 lines a process (log only);
  - with env.MADEIRA_IMPORT_APPDIR = 1 (default off), one more load_dll with the program's own folder first in the search path, and on success
    "[import-appdir] NAME (needed by IMPORTER) found through the application directory DIR".
Without the key nothing but the log line changes, and the line prints only where an import already failed.
WHERE THIS REACHES: the 32-bit ntdll through the i386 farm (build-ipa.yml, right after patch-wine-loader-import-diag.py; put back with it), and the
64-bit ntdll through the rebuilt set (tools/build-wine-fresh-dlls.sh). Idempotent; fails by name when an anchor moved.
Usage: patch-wine-loader-import-appdir.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: [import-miss]"
LOADER = "dlls/ntdll/loader.c"
HELPER_ANCHOR = "static BOOL import_dll( WINE_MODREF *wm, const IMAGE_IMPORT_DESCRIPTOR *descr, LPCWSTR load_path, WINE_MODREF **pwm )\n{\n"
HELPER = r'''/* madeira-bcd: [import-miss] and [import-appdir] (tools/patch-wine-loader-import-appdir.py, round 669 agent B S7; Doki Doki Literature Club
 * N8E7GN). An import load_dll did not find: what was searched (log only, 8 lines a process) and, with MADEIRA_IMPORT_APPDIR=1, one more search
 * with the program's own folder first. */
static NTSTATUS ios_import_miss( WINE_MODREF *wm, const WCHAR *libname, const char *name, LPCWSTR load_path, WINE_MODREF **pwm, BOOL system )
{
    static LONG said;
    static int on = -1;
    const UNICODE_STRING *image = &NtCurrentTeb()->Peb->ProcessParameters->ImagePathName;
    const WCHAR *rest = load_path ? load_path : L"";
    unsigned int dir_len = 0, i;
    NTSTATUS status = STATUS_DLL_NOT_FOUND;
    WCHAR *alt;

    if (on < 0)
    {
        UNICODE_STRING nm, val;
        WCHAR buf[8];
        RtlInitUnicodeString( &nm, L"MADEIRA_IMPORT_APPDIR" );
        val.Buffer = buf; val.Length = 0; val.MaximumLength = sizeof(buf);
        on = (!RtlQueryEnvironmentVariable_U( NULL, &nm, &val ) && val.Length == sizeof(WCHAR) && buf[0] == '1');
    }
    for (i = 0; i < image->Length / sizeof(WCHAR); i++) if (image->Buffer[i] == '\\') dir_len = i;   /* up to the last backslash */
    if (InterlockedIncrement( &said ) <= 8)
        ERR( "[import-miss] %s needed by %s: load path %s; application dir %s; MADEIRA_IMPORT_APPDIR=%d\n", debugstr_a( name ),
             debugstr_w( wm->ldr.FullDllName.Buffer ), debugstr_w( rest ), debugstr_wn( image->Buffer, dir_len ), on );
    if (!on || !dir_len) return status;
    if (!(alt = RtlAllocateHeap( GetProcessHeap(), 0, (dir_len + 1 + wcslen( rest ) + 1) * sizeof(WCHAR) ))) return status;
    memcpy( alt, image->Buffer, dir_len * sizeof(WCHAR) );
    alt[dir_len] = ';';
    wcscpy( alt + dir_len + 1, rest );
    status = load_dll( alt, libname, 0, pwm, system );
    if (!status)
        ERR( "[import-appdir] %s (needed by %s) found through the application directory %s\n", debugstr_a( name ),
             debugstr_w( wm->ldr.FullDllName.Buffer ), debugstr_wn( image->Buffer, dir_len ) );
    RtlFreeHeap( GetProcessHeap(), 0, alt );
    return status;
}

'''
SITE_OLD = "    if (!status) status = load_dll( load_path, buffer, 0, &wmImp, system );\n"
SITE_NEW = SITE_OLD + ("    if (status == STATUS_DLL_NOT_FOUND)   /* madeira-bcd: [import-miss] (tools/patch-wine-loader-import-appdir.py) */\n"
                       "        status = ios_import_miss( wm, buffer, name, load_path, &wmImp, system );\n")


def main():
    loader = Path(sys.argv[1] if len(sys.argv) > 1 else "wine") / LOADER
    text = loader.read_text()
    if MARKER in text:
        print(f"import-appdir: already applied ({loader})")
        return
    for what, anchor in (("import_dll", HELPER_ANCHOR), ("the load_dll call of import_dll", SITE_OLD)):
        if text.count(anchor) != 1:
            sys.exit(f"patch-wine-loader-import-appdir: the anchor of {what} is not in {loader} exactly once")
    text = text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR).replace(SITE_OLD, SITE_NEW)
    loader.write_text(text)
    print(f"import-appdir: [import-miss] / [import-appdir] applied ({loader})")


if __name__ == "__main__":
    main()
