#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""LOVE's GC64 LuaJIT in place of one that needs memory below 2 GB (OpenGL, build 632; build/wineserver/luajit_compat.c).

LOVE games (Balatro) ship an x64 LuaJIT built with 32-bit GC references, whose allocator wants memory below 2 GB through
NtAllocateVirtualMemory; iOS never gives it, luaL_newstate() returns NULL and the game dies at its first Lua call. The wineserver
(mapping_ios.c, create_mapping) asks luajit_compat.c about every image section; for such a DLL it hands back the file descriptor of the
bundled GC64 build (compat/love/lua51.dll, build/luajit-x64/build.sh) and the section is made from that file. The game's file is never touched.

BCD runs it only in a session whose game has the gl-backend key on (the app exports MADEIRA_GL_BACKEND then): a LOVE game cannot draw without
OpenGL, and without the key the function answers -1 at its first statement, so no image mapping of any other program reads anything more.

Checks:
  1. the hook in mapping_ios.c sits at the SEC_IMAGE branch, before get_image_params, and both build lists of the wineserver name the new object;
  2. compiled on the host with synthetic PE images: without the key every image gets -1 and nothing is printed; with it only an x64 DLL that
     exports luaJIT_setmode and contains the string NtAllocateVirtualMemory gets the bundled file (an fd), a GC64 build is left as it is,
     a DLL without the export, a 32-bit DLL and a file that is not a PE are never touched, and a missing bundled file is said and answers -1;
  3. build/luajit-x64/build.sh pins the source commit, builds interpreter-only GC64 with internal unwinding, and refuses a DLL that still
     holds the low-memory allocator; CI builds it after llvm-mingw, caches it and does not fail the build when it cannot.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_synth import build_pe

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    ok = ok and bool(cond)


src = (R / "build/wineserver/luajit_compat.c").read_text()
mapping = (R / "build/wineserver/mapping_ios.c").read_text()
wsb = (R / "build/wineserver/build.sh").read_text()
ljb = (R / "build/luajit-x64/build.sh").read_text()
wf = (R / ".github/workflows/build-ipa.yml").read_text()

# 1. wiring
img = mapping[mapping.index("if (flags & SEC_IMAGE)\n        {\n            unsigned int err;"):]
img = img[:img.index("goto error;")]
check("mapping_ios.c asks luajit_compat.c in the SEC_IMAGE branch, before get_image_params",
      "int compat_fd = madeira_luajit_compat_fd( unix_fd, st.st_size );" in img
      and img.index("madeira_luajit_compat_fd(") < img.index("err = get_image_params( mapping, st.st_size, unix_fd );"))
check("... and only swaps the section's file when an fd came back (-1 leaves everything as it was)", "if (compat_fd != -1)" in img)
check("both lists of the wineserver build name the object (compiled, and swapped into the archive)",
      '"luajit_compat:$BUILD_DIR/luajit_compat.c:luajit_compat.o"' in wsb and '"luajit_compat.o:luajit_compat.o"' in wsb)
body = src[src.index("int madeira_luajit_compat_fd( int unix_fd, off_t file_size )"):]
first = [l.strip() for l in body.split("{", 1)[1].splitlines() if l.strip() and not l.strip().startswith(("/*", "*", "const", "char ", "int fd"))]
check("the key gate is the function's first statement: nothing is read without it",
      first[0] == "int c_comments = comments && !strcmp( comments, \"1\" );" and first[1] == "if (!c_comments && (!gl || !*gl)) return -1;" and 'getenv( "MADEIRA_GL_BACKEND" )' in body, first[:2])
check("the file keeps Connor Gow's LGPL header (the credit stays)", src.startswith("/*\n * Madeira: substitute a GC64 LuaJIT") and "Copyright 2026 Connor Gow" in src
      and "GNU Lesser General Public" in src)

# 2. compiled
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    harness = t / "h.c"
    harness.write_text(r'''
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
int madeira_luajit_compat_fd( int unix_fd, off_t file_size );
int main( int argc, char **argv )
{
    int fd = open( argv[1], O_RDONLY ), ret;
    struct stat st;
    fstat( fd, &st );
    ret = madeira_luajit_compat_fd( fd, st.st_size );
    printf( "%s\n", ret == -1 ? "-1" : "fd" );
    if (ret != -1)
    {
        char buf[16] = {0};
        pread( ret, buf, 10, 0 );
        printf( "bundled:%s\n", buf );
    }
    return 0;
}
''')
    exe = t / "h"
    cc = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-o", str(exe), str(harness), str(R / "build/wineserver/luajit_compat.c")],
                        capture_output=True, text=True)
    check("luajit_compat.c compiles on the host without warnings", cc.returncode == 0 and not cc.stderr.strip(), cc.stderr)
    if cc.returncode == 0:
        bundle = t / "bundle"
        (bundle / "compat/love").mkdir(parents=True)
        (bundle / "compat/love/lua51.dll").write_bytes(b"GC64-BUNDLED-DLL")
        (bundle / "compat/lua-c-comments").mkdir()
        (bundle / "compat/lua-c-comments/lua51.dll").write_bytes(b"C-COMMENTS-DLL")
        names = ["lua_newstate", "luaL_newstate", "luaJIT_setmode", "lua_pcall", "luaopen_jit"]
        images = {
            "low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)": build_pe(exports=names, blob=b"xx NtAllocateVirtualMemory yy"),
            "GC64 x64 LuaJIT (no allocator string)": build_pe(exports=names, blob=b"nothing about memory here"),
            "x64 DLL without luaJIT_setmode": build_pe(exports=["a", "b"], blob=b"NtAllocateVirtualMemory"),
            "a longer export name with the same prefix": build_pe(exports=["luaJIT_setmodeX"], blob=b"NtAllocateVirtualMemory"),
            "32-bit LuaJIT": build_pe(machine=0x14C, pe32=True, image_base=0x10000000, exports=names, blob=b"NtAllocateVirtualMemory"),
            "not a PE": b"hello, this is not a program",
        }
        paths = {}
        for i, (label, data) in enumerate(images.items()):
            p = t / f"img{i}.dll"
            p.write_bytes(data)
            paths[label] = p

        def run(path, key, bundle_dir=bundle, comments=None):
            env = {"PATH": os.environ.get("PATH", "")}
            if key:
                env["MADEIRA_GL_BACKEND"] = key
            if comments is not None:
                env["MADEIRA_LUAJIT_C_COMMENTS"] = comments
            if bundle_dir:
                env["WINEDLLPATH"] = str(bundle_dir)
            r = subprocess.run([str(exe), str(path)], capture_output=True, text=True, env=env)
            return r.stdout.split(), r.stderr

        quiet = True
        for label, p in paths.items():
            out, err = run(p, None)
            if out != ["-1"] or err:
                quiet = False
                check(f"without the key: {label} gets -1 and nothing is printed", False, (out, err))
        check("without the key every image gets -1 and nothing is printed (6 images)", quiet)
        quiet = True
        for key in ("", ):
            for label, p in paths.items():
                out, err = run(p, key)
                if out != ["-1"] or err:
                    quiet = False
        check("an empty key counts as no key", quiet)

        for backend in (None, "zink"):
            out, err = run(paths["low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)"], backend, comments="1")
            check("C-style comments select the dedicated DLL regardless of OpenGL",
                  out == ["fd", "bundled:C-COMMENTS"] and "with C-style comments" in err, (out, err))
        for label in ("GC64 x64 LuaJIT (no allocator string)", "32-bit LuaJIT", "not a PE"):
            out, err = run(paths[label], None, comments="1")
            check(f"C-style comments do not replace {label}", out == ["-1"], (out, err))
        out, err = run(paths["low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)"], None, comments="0")
        check("C-style comments require an explicit 1", out == ["-1"] and not err, (out, err))
        (bundle / "compat/lua-c-comments/lua51.dll").unlink()
        out, err = run(paths["low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)"], "zink", comments="1")
        check("a missing comment DLL leaves the original in place",
              out == ["-1"] and "/lua-c-comments/lua51.dll is missing" in err, (out, err))

        out, err = run(paths["low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)"], "zink")
        check("with the key a low-memory x64 LuaJIT gets the bundled GC64 DLL's fd", out[:1] == ["fd"] and out[1:] == ["bundled:GC64-BUNDL"]
              and "mapping the bundled GC64 build instead" in err, (out, err))
        out, err = run(paths["low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)"], "gles")
        check("... with either backend word", out[:1] == ["fd"], (out, err))
        out, err = run(paths["GC64 x64 LuaJIT (no allocator string)"], "zink")
        check("a GC64 LuaJIT is left as it is, and the log says so", out == ["-1"] and "x64 LuaJIT with GC64, left as is" in err, (out, err))
        for label in ("x64 DLL without luaJIT_setmode", "a longer export name with the same prefix", "32-bit LuaJIT", "not a PE"):
            out, err = run(paths[label], "zink")
            check(f"{label}: -1, nothing printed", out == ["-1"] and not err, (out, err))
        out, err = run(paths["low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)"], "zink", bundle_dir=t / "no-bundle")
        check("a missing bundled DLL is said and answers -1 (the game keeps its own)", out == ["-1"] and "lua51.dll is missing" in err, (out, err))
        out, err = run(paths["low-memory x64 LuaJIT (NtAllocateVirtualMemory inside)"], "zink", bundle_dir=None)
        check("no WINEDLLPATH: -1", out == ["-1"], (out, err))

# 3. the build script and CI
check("the build pins LuaJIT's commit, checks it out when another is there, and clones the v2.1 branch only when nothing is",
      re.search(r"LUAJIT_COMMIT=[0-9a-f]{40}\n", ljb) and 'git clone -q -b v2.1 https://github.com/LuaJIT/LuaJIT "$SRC"' in ljb
      and 'git -C "$SRC" checkout -q "$LUAJIT_COMMIT"' in ljb)
check("interpreter only, GC64 (the default), internal unwinding; the DLL is stripped into app/Madeira/compat/love",
      'XCFLAGS="-DLUAJIT_DISABLE_JIT -DLUAJIT_NO_UNWIND"' in ljb and "DISABLE_GC64" not in ljb and 'x86_64-w64-mingw32-strip -o "$destination/lua51.dll"' in ljb
      and 'OUT="$R/app/Madeira/compat/love"' in ljb)
check("a DLL that still holds the low-memory allocator is refused", "grep -aq NtAllocateVirtualMemory" in ljb and 'rm -f "$destination/lua51.dll"' in ljb)
i_restore = wf.index("- name: Restore GC64 LuaJIT (LOVE games)")
check("CI: after llvm-mingw is saved, restored from a cache keyed on the script, built only on a miss, never failing the build, then saved",
      wf.index("- name: Save llvm-mingw") < i_restore < wf.index("- name: Build the GC64 LuaJIT (LOVE games)") < wf.index("- name: Save GC64 LuaJIT (LOVE games)")
      and "key: luajit-gc64-v1-${{ hashFiles('build/luajit-x64/build.sh', 'build/luajit-x64/c-style-comments.patch') }}" in wf
      and "continue-on-error: true\n        run: bash build/luajit-x64/build.sh" in wf)
check("CI runs this check", "python3 tests/host/check-luajit-compat.py" in wf)
gi = (R / ".gitignore").read_text()
check("the DLL, its version and the licence copy are not committed",
      "app/Madeira/compat/love/lua51.dll" in gi and "app/Madeira/compat/lua-c-comments/lua51.dll" in gi and "app/Madeira/licenses/LuaJIT-MIT.txt" in gi)
proj = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the Xcode project bundles the compat folder as a resource", "A1000704 /* compat in Resources */" in proj and "A2000704 /* compat */" in proj)

print("PASS: LuaJIT compat" if ok else "FAIL: LuaJIT compat")
sys.exit(0 if ok else 1)
