#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Hide the iOS host's own version file from Windows programs (round 630, agent O1 S14).

Ren'Py 6.99 games (Doki Doki Literature Club 45E18B and 86QH9P; 6AHQMM, 48B1X4, NTR3Y8, TS8ME1; Coming Out on Top NQZKVF) stopped
at start-up with exit -1 right after pyexpat.pyd. Ren'Py calls Python's platform.mac_ver(); Wine's rule that an absolute "/path" is a
Unix path (ntdll path.c) lets it find /System/Library/CoreServices/SystemVersion.plist, which exists on iOS; Python then decides it
runs on a Mac and calls os.uname(), which Python for Windows does not have, and the AttributeError ends the import of renpy.

get_nt_and_unix_names (the \\??\\unix\\ branch every NT file call goes through for such a path) now answers "not found" for exactly
that file, so mac_ver() returns its empty default, as on Windows. Every other host path is untouched; Wine's own code reads host
files on the Unix side, not through this NT path.

Applied to the wine checkout after the other dlls/ntdll/unix/file.c patches, before "Build ntdll-unix"; the submodule is not
committed to.

Usage: patch-wine-hide-host-version-plist.py [path/to/wine/dlls/ntdll/unix/file.c]
Idempotent; exits non-zero when an anchor is missing (wine moved).
"""
import sys

MARKER = "madeira-bcd: hide host version plist"
HELPER_ANCHOR = "NTSTATUS get_nt_and_unix_names( OBJECT_ATTRIBUTES *attr, UNICODE_STRING *nt_name,\n"
HELPER = r"""#ifdef WINE_IOS
/* """ + MARKER + r""" (tools/patch-wine-hide-host-version-plist.py, round 630): the iOS host's own version file. A Windows
 * Python's platform.mac_ver() (Ren'Py 6.99 calls it at import) found it through Wine's "absolute /path is a Unix path" rule,
 * decided it runs on a Mac and called os.uname(), which a Windows build does not have. Exactly this one file is reported as not
 * found; every other host path is untouched. */
static BOOL ios_hidden_host_file( const char *unix_name, ULONG len )
{
    static const char hidden[] = "/System/Library/CoreServices/SystemVersion.plist";
    return len == sizeof(hidden) - 1 && !strncasecmp( unix_name, hidden, len );
}
#endif

"""
CALL_ANCHOR = ("        for (ULONG i = 0; i < lenA; i++) if (unix_name[i] == '\\\\') unix_name[i] = '/';\n\n"
               "        status = find_drive_nt_root( unix_name, lenA, &buffer, disposition );\n")
CALL = ("        for (ULONG i = 0; i < lenA; i++) if (unix_name[i] == '\\\\') unix_name[i] = '/';\n\n"
        "#ifdef WINE_IOS\n"
        "        /* " + MARKER + ": *unix_name_ret is set, the caller frees it */\n"
        "        if (ios_hidden_host_file( unix_name, lenA )) return STATUS_OBJECT_NAME_NOT_FOUND;\n"
        "#endif\n"
        "        status = find_drive_nt_root( unix_name, lenA, &buffer, disposition );\n")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
    src = open(path).read()
    if MARKER in src:
        print("patch-wine-hide-host-version-plist: already patched")
        return
    if src.count(HELPER_ANCHOR) != 1:
        sys.exit("patch-wine-hide-host-version-plist: get_nt_and_unix_names not found exactly once in %s" % path)
    if src.count(CALL_ANCHOR) != 1:
        sys.exit("patch-wine-hide-host-version-plist: the \\??\\unix\\ branch was not found exactly once in %s" % path)
    out = src.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR).replace(CALL_ANCHOR, CALL)
    open(path, "w").write(out)
    print("patch-wine-hide-host-version-plist: applied to " + path)


if __name__ == "__main__":
    main()
