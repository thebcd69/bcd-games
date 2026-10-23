#!/usr/bin/env python3
r"""Resolve GLOBALROOT-wrapped DOS file paths in the iOS Wine file layer.

Minecraft Dungeons II opens its instruction recovery data through
\??\GLOBALROOT\??\C:\...\Splitmap-Shipping.dat. The old client resolver
looks for dosdevices/globalroot, then falls back to the object manager.
That fallback does not translate the DOS drive path and returns path not found.

GLOBALROOT targets the object manager root. Skip each complete wrapper, then
recognise the inner DOS namespace. Keep offsets relative to the original name
so the existing drive, component and reparse-point code receives the same input.
Native device paths still use the object manager. No guest code is changed.

Applied before ntdll-unix is compiled. The submodule pin stays unchanged.
Usage: patch-wine-globalroot.py [wine/dlls/ntdll/unix/file.c]
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: GLOBALROOT DOS paths"
OLD = r'''static inline int get_dos_prefix_len( const UNICODE_STRING *name )
{
    static const WCHAR dosdev_prefixW[] = {'\\','D','o','s','D','e','v','i','c','e','s','\\'};

    if (name->Length >= sizeof(nt_prefixW) &&
        !memcmp( name->Buffer, nt_prefixW, sizeof(nt_prefixW) ))
        return ARRAY_SIZE( nt_prefixW );

    if (name->Length >= sizeof(dosdev_prefixW) &&
        !wcsnicmp( name->Buffer, dosdev_prefixW, ARRAY_SIZE( dosdev_prefixW )))
        return ARRAY_SIZE( dosdev_prefixW );

    return 0;
}'''
NEW = r'''static inline int get_dos_prefix_len( const UNICODE_STRING *name )
{
    static const WCHAR dosdev_prefixW[] = {'\\','D','o','s','D','e','v','i','c','e','s','\\'};
#ifdef WINE_IOS
    /* madeira-bcd: GLOBALROOT DOS paths (tools/patch-wine-globalroot.py). */
    static const WCHAR globalrootW[] = {'G','L','O','B','A','L','R','O','O','T','\\'};
#endif
    const WCHAR *buffer = name->Buffer;
    unsigned int len = name->Length / sizeof(WCHAR), pos = 0, prefix_len;

    for (;;)
    {
        if (len >= ARRAY_SIZE(nt_prefixW) && !memcmp( buffer, nt_prefixW, sizeof(nt_prefixW) ))
            prefix_len = ARRAY_SIZE(nt_prefixW);
        else if (len >= ARRAY_SIZE(dosdev_prefixW) &&
                 !wcsnicmp( buffer, dosdev_prefixW, ARRAY_SIZE(dosdev_prefixW) ))
            prefix_len = ARRAY_SIZE(dosdev_prefixW);
        else
            return 0;

#ifdef WINE_IOS
        if (len >= prefix_len + ARRAY_SIZE(globalrootW) &&
            !wcsnicmp( buffer + prefix_len, globalrootW, ARRAY_SIZE(globalrootW) ))
        {
            /* Keep the backslash: GLOBALROOT targets the object manager root. */
            prefix_len += ARRAY_SIZE(globalrootW) - 1;
            buffer += prefix_len;
            len -= prefix_len;
            pos += prefix_len;
            continue;
        }
#endif
        return pos + prefix_len;
    }
}'''


def patch(source):
    if MARKER in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError("GLOBALROOT patch anchor missing or ambiguous")
    return source.replace(OLD, NEW)


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c")
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit(str(error))
    if result == source:
        print("GLOBALROOT DOS paths already patched")
    else:
        path.write_text(result)
        print("patched GLOBALROOT DOS paths")


if __name__ == "__main__":
    main()
