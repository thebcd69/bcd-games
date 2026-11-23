#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""glIsSync(0) and glDeleteSync(0) on BCD's OpenGL no longer dereference NULL (round 707, agent M3 S2; DOOM 3: BFG Edition 705H3D).

Wine's opengl32 unix side turns a client sync handle into the host's with get_unix_sync (dlls/opengl32/unix_private.h), which reads
sync->unix_handle with no NULL check; the 32-bit client lets handle 0 through on purpose (wgl.c get_sync_from_handle returns TRUE for it),
and GL defines glIsSync(0) as GL_FALSE and glDeleteSync(0) as a no-op. DOOM 3: BFG Edition (32-bit, gl-backend = zink) asks glIsSync(0)
after its first swap and died in wow64_ext_glIsSync. A NULL sync now maps to NULL; every other handle is read as before. The generated
unix_thunks.c calls get_unix_sync for both the 64-bit and the wow64 entry points, and ios_wow_host_ptr(0) is already NULL, so nothing else
changes. Only a session whose game uses BCD's OpenGL (gl-backend = zink or gles) reaches these thunks.

Applied in CI before build/ntdll-unix/build.sh compiles opengl32's unix side. Idempotent; fails by name if the anchor moved, the file
unchanged. Usage: patch-wine-gl-null-sync.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd (round 707, M3 S2)"
REL = "dlls/opengl32/unix_private.h"
OLD = """static inline GLsync get_unix_sync( GLsync sync )
{
    return (GLsync)(UINT_PTR)sync->unix_handle;
}
"""
NEW = """static inline GLsync get_unix_sync( GLsync sync )
{
    /* """ + MARKER + """: glIsSync(0) is GL_FALSE and glDeleteSync(0) a no-op; the 32-bit client passes handle 0 through on purpose
     * (get_sync_from_handle). DOOM 3: BFG Edition asks glIsSync(0) after its first swap (tools/patch-wine-gl-null-sync.py). */
    return sync ? (GLsync)(UINT_PTR)sync->unix_handle : NULL;
}
"""


def main():
    wine = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = wine / REL
    s = path.read_text()
    if MARKER in s:
        print("opengl32 NULL sync: already applied")
        return 0
    if s.count(OLD) != 1:
        print(f"::error::patch-wine-gl-null-sync: get_unix_sync: anchor found {s.count(OLD)} times (want 1) in {path}")
        return 1
    path.write_text(s.replace(OLD, NEW))
    print("opengl32 NULL sync: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
