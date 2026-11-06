#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Wine fork carries opengl32's winios change (OpenGL, build 632).

BCD's OpenGL needs this tree's dlls/opengl32 as the Wine fork patches it (the change of Madeira's wine-opengl-winios.patch, as a fork
commit): the unix side's OpenGL ES contexts, the 32-bit (wow64) thunks that turn every embedded guest pointer into a host one with
ios_wow_host_ptr(), the GL_EXTENSIONS cap for 32-bit callers, query results as buffer offsets, and Makefile.in's fixed 0x7a800000 image base for
the x86 builds only. build/ntdll-unix compiles unix_wgl.c and unix_thunks.c into libntdll_unix.a; tools/build-wine-extra-dlls.sh builds
opengl32-optin.dll from the same tree. Run against the checked-out submodule (SKIP without it).
"""
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
W = R / "wine/dlls/opengl32"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-800:]) if detail and not cond else ""))
    ok = ok and bool(cond)


if not (W / "unix_wgl.c").is_file():
    print("SKIP: no Wine sources here (check out the wine submodule)")
    sys.exit(0)

mk = (W / "Makefile.in").read_text()
check("Makefile.in: the sub-4 GB image base is for the x86 builds only (ARM64EC and ARM64 get the default base)",
      re.search(r"^i386_EXTRADLLFLAGS\s*=\s*-Wl,--image-base,0x7a800000", mk, re.M) and re.search(r"^x86_64_EXTRADLLFLAGS\s*=\s*-Wl,--image-base,0x7a800000", mk, re.M)
      and not re.search(r"^EXTRADLLFLAGS\s*=", mk, re.M), mk[:600])
up = (W / "unix_private.h").read_text()
check("unix_private.h: embedded guest pointers go through ios_wow_host_ptr, with the call counters the driver's perf line reads",
      "ios_wow_host_ptr" in up and "madeira_gl_wow64_calls" in up and "madeira_gl_count_call" in up and "wow64_buffer_ptr" in up)
th = (W / "unix_thunks.c").read_text()
check("unix_thunks.c: the generated wow64 thunks convert with ios_wow_host_ptr (thousands of sites), not ULongToPtr alone",
      th.count("ios_wow_host_ptr(") > 1000, th.count("ios_wow_host_ptr("))
wg = (W / "unix_wgl.c").read_text()
check("unix_wgl.c: OpenGL ES contexts, the GL_EXTENSIONS cap (MADEIRA_GL_EXTENSIONS_MAX) and the query buffer offsets (MADEIRA_GL_QUERY_BUFFER_OFFSETS)",
      "is_gles" in wg and "MADEIRA_GL_EXTENSIONS_MAX" in wg and "MADEIRA_GL_QUERY_BUFFER_OFFSETS" in wg and "wow64_query_buffer_ptr" in wg)
mo = (W / "make_opengl").read_text()
check("make_opengl: the generator carries the conversions (so a regeneration keeps them)", "get_wow64_ptr_arg" in mo and "ios_wow_host_ptr" in mo)
lic = (R / "wine/LICENSE-MADEIRA.md").read_text()
check("LICENSE-MADEIRA.md names the opengl32 change and its author (the credit stays with the code)", "opengl32: OpenGL ES contexts" in lic and "Connor Gow" in lic)
check("the ntdll build compiles both unix files and archives them",
      all(x in (R / "build/ntdll-unix/build.sh").read_text() for x in ('"$WINE_SRC/dlls/opengl32/unix_wgl.c" "opengl32_wgl" "opengl32"',
                                                                         '"$WINE_SRC/dlls/opengl32/unix_thunks.c" "opengl32_thunks" "opengl32"',
                                                                         '"$OBJ_DIR/opengl32_wgl.o" "$OBJ_DIR/opengl32_thunks.o"')))
print("PASS: OpenGL Wine pin" if ok else "FAIL: OpenGL Wine pin")
sys.exit(0 if ok else 1)
