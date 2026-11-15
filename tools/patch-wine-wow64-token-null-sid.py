#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wow64.dll: a NULL SID from NtQueryInformationToken is answered, not dereferenced (source form of tools/patch-wow64-token-null-sid.py; round 624, Garry's Mod).

dlls/wow64/security.c, wow64_NtQueryInformationToken, case TokenOwner / TokenPrimaryGroup / TokenAppContainerSid:
    sid = owner->Owner;
    sid_len = offsetof( SID, SubAuthority[sid->SubAuthorityCount] );        <- reads byte 1 of a NULL SID
ntdll answers TokenAppContainerSid with success and a NULL SID, as Windows does for a token that is not an AppContainer's, and the 32-bit program's
call (Garry's Mod's CEF launcher thread: 8 of 8 logs on 6 devices, builds 574-622) faulted at address 1 in wow64.dll.

The committed aarch64-windows/wow64.dll carries the fix as a 49-byte patch of the binary (tools/patch-wow64-token-null-sid.py: the build of the pinned
source plus that patch is what ships). A rebuilt wow64.dll (tools/build-wine-fresh-dlls.sh, env.MADEIRA_WIN64_FRESH = 1) must carry it too, or the key
would take the fix away from Garry's Mod: this script is the same change in the source. For a NULL SID the thunk stores owner32->Owner = 0 and returns
the status of the query with *retlen = sizeof(*owner32) = 4; len < 4 gives STATUS_BUFFER_TOO_SMALL, exactly as the binary patch does. A real SID runs the
code as before. Keyless (the old code reads address 1).

Idempotent; fails by name when the case moved.
Usage: patch-wine-wow64-token-null-sid.py WINE-DIR | path/to/wine/dlls/wow64/security.c
"""
from pathlib import Path
import sys

FILE = "dlls/wow64/security.c"
MARKER = "madeira-bcd: NULL SID"
OLD = """        status = NtQueryInformationToken( handle, class, &buffer, sizeof(buffer), &ret_size );
        if (status) return status;
        sid = owner->Owner;
        sid_len = offsetof( SID, SubAuthority[sid->SubAuthorityCount] );
        if (len >= sizeof(*owner32) + sid_len)
        {
            owner32->Owner = host_ptr32( owner32 + 1 );
"""
NEW = """        status = NtQueryInformationToken( handle, class, &buffer, sizeof(buffer), &ret_size );
        if (status) return status;
        sid = owner->Owner;
        if (!sid)   /* madeira-bcd: NULL SID (tools/patch-wine-wow64-token-null-sid.py; round 624, Garry's Mod): TokenAppContainerSid of a token that is not an AppContainer's */
        {
            if (len >= sizeof(*owner32)) owner32->Owner = 0;
            else status = STATUS_BUFFER_TOO_SMALL;
            if (retlen) *retlen = sizeof(*owner32);
            return status;
        }
        sid_len = offsetof( SID, SubAuthority[sid->SubAuthorityCount] );
        if (len >= sizeof(*owner32) + sid_len)
        {
            owner32->Owner = host_ptr32( owner32 + 1 );
"""


def main():
    arg = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = arg if arg.suffix == ".c" else arg / FILE
    text = path.read_text()
    if MARKER in text:
        print("wow64 NULL SID: already patched")
        return
    if text.count(OLD) != 1:
        sys.exit("patch-wine-wow64-token-null-sid: the TokenOwner / TokenPrimaryGroup / TokenAppContainerSid case was not found exactly once in " + str(path))
    path.write_text(text.replace(OLD, NEW))
    print("wow64 NULL SID: patched")


if __name__ == "__main__":
    main()
