#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's OpenGL is a game's choice, and everything behind it waits for the key (OpenGL, build 632; docs/OPENGL.md).

A game's file (or bcd.cfg) says `gl-backend = zink` or `gl-backend = gles`; the app then exports MADEIRA_GL_BACKEND for that session
(WineProcessBridge.m: bcd_gl_session_setup) and everything OpenGL in the engine reads that one variable. Without the key a session must be
what it was before OpenGL existed: no driver installed (win32u), the GL-absent stub table bound to opengl32 (ntdll), no x18 store patching,
no opt-in opengl32.dll linked, nothing exported, nothing logged, no LuaJIT substitution (check-luajit-compat.py). This check holds every one
of those gates, and runs the pieces on the host:

  1. win32u: the driver is installed (pOpenGLInit) only under getenv("MADEIRA_GL_BACKEND"); the file keeps its LGPL header and has no
     KosmicKrisp choice; the build compiles it.
  2. ntdll, compiled from virtual_ios.c: ios_insn_x18_role is untouched, ios_insn_x18_role_ex answers the same for stores = 0 on every
     instruction tried (300,000 random and the crafted forms), the store forms of x18 (STR, STUR, STP) only with stores = 1 and only in the
     value position; ios_x18_patch_stores is true only for opengl32.dll and only with the key; ios_jit_x18_tramp_need counts the extra sites
     only then; ios_gl_real_unixlib binds the real tables only with the key and, for a 64-bit module, only to an image not based at
     0x7a800000 (the shipped opengl32.dll keeps the stub table); the real branch comes before the stub branch and the stub branch is as it was.
  3. the session, compiled from WineProcessBridge.m (ObjC, macOS): no key, no variable, no line; zink or gles (the game's file wins over
     bcd.cfg; any case) exports MADEIRA_GL_BACKEND and MADEIRA_GL_DIR; any other word is ignored and said; the LOVE hint only for gles and
     never over a value that was set; opengl32 joins the opt-in DLL list only with the key; the session end drops the variables.
  4. the opt-in opengl32.dll: built from the tree by tools/build-wine-extra-dlls.sh (block OGL), checked by tools/verify-opengl32-optin.py
     (synthetic PE images: right machine, same exports, not at the shipped base) and named by the shipped-DLL check for every list and
     rule line that turns the key on.
"""
import os
import random
import re
import shutil
import subprocess
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_synth import build_pe

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1800:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def function(src, head):
    i = src.index(head)
    j = src.index("\n}\n", i)
    return src[i:j + 3]


drv = (R / "build/win32u-unix/driver_ios.c").read_text()
gl = (R / "build/win32u-unix/opengl_ios.c").read_text()
w32b = (R / "build/win32u-unix/build.sh").read_text()
vi = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ntb = (R / "build/ntdll-unix/build.sh").read_text()
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
wf = (R / ".github/workflows/build-ipa.yml").read_text()

# ---- 1. win32u ------------------------------------------------------------------------------------------------------------------------
check("driver_ios.c: pOpenGLInit is set once, only under getenv(\"MADEIRA_GL_BACKEND\")",
      len(re.findall(r"\bpOpenGLInit\s*=", drv)) == 1
      and 'if (getenv( "MADEIRA_GL_BACKEND" )) winios_user_driver.pOpenGLInit = winios_OpenGLInit;' in drv
      and "SET_USER_FUNC(OpenGLInit);" in drv)
check("... and without it the null driver's answer stays (STATUS_NOT_IMPLEMENTED), as before",
      "static UINT nulldrv_OpenGLInit( UINT version, const struct opengl_funcs *opengl_funcs, const struct opengl_driver_funcs **driver_funcs )\n{\n    return STATUS_NOT_IMPLEMENTED;\n}" in drv)
check("opengl_ios.c keeps its LGPL header and Madeira's two backends; the KosmicKrisp choice of the Vulkan driver is not in BCD's copy",
      gl.startswith("/* LGPL-2.1-or-later: part of the Wine unix side") and "zink - desktop OpenGL" in gl and "gles - WGL on top of Apple's OpenGL ES" in gl
      and not re.search(r"kosmic|gl-vulkan|MADEIRA_GL_VULKAN", gl, re.I) and 'snprintf( path, sizeof(path), "%s/libMoltenVK.dylib", dir );' in gl)
check("the driver falls back from zink to gles by itself and says so; without the key's word it is gles",
      'ERR( "[winios-gl] zink unavailable, falling back to OpenGL ES\\n" );' in gl and 'if (backend && !strcmp( backend, "zink" ))' in gl)
check("win32u's build compiles it after the loop over Wine's files", 'compile_one "$BUILD_DIR/opengl_ios.c" "opengl_ios"' in w32b
      and w32b.index('compile_one "$src" "$name"\ndone') < w32b.index('compile_one "$BUILD_DIR/opengl_ios.c"'))

# ---- 2. ntdll --------------------------------------------------------------------------------------------------------------------------
roles_src = vi[vi.index("#define X18_ROLE_NONE 0"):vi.index("/* madeira-bcd: the destination of an ADD/SUB (immediate or register) that reads")]
role_fn = function(vi, "static int ios_insn_x18_role(uint32_t insn)")
check("ios_insn_x18_role itself is untouched by OpenGL (no store role in it; the other host checks compile it by name)",
      not re.search(r"X18_ROLE_RT\b", role_fn) and "stores" not in role_fn)
ex_fn = function(vi, "static int ios_insn_x18_role_ex(uint32_t insn, int stores)")
check("ios_insn_x18_role_ex answers the old role first, and the store forms only for stores != 0",
      "int role = ios_insn_x18_role( insn );" in ex_fn and "if (role != X18_ROLE_NONE || !stores) return role;" in ex_fn)
sources = {
    "pe_name": function(vi, "const char *ios_pe_module_name( const void *image_base, size_t image_size )\n{"),
    "session_on": function(vi, "int ios_gl_session_on( void )"),
    "patch_stores": function(vi, "int ios_x18_patch_stores( const void *image, size_t image_size )"),
    "data_map": function(vi, "static unsigned char *ios_x18_build_data_map( const char *text, size_t text_size )"),
    "tramp_need": function(vi, "size_t ios_jit_x18_tramp_need( const char *text, size_t text_size, int stores )"),
    "real_unixlib": function(vi, "static int ios_gl_real_unixlib( void *module, BOOL wow )"),
}
check("ios_x18_patch_stores needs the key first, then the module's own name opengl32.dll",
      sources["patch_stores"].index("ios_gl_session_on()") < sources["patch_stores"].index("ios_pe_module_name( image, image_size )")
      and 'strcasecmp( ios_pe_module_name( image, image_size ), "opengl32.dll" )' in sources["patch_stores"])
check("ios_gl_real_unixlib starts with the key; a 32-bit caller gets the real table, a 64-bit one only an image not based at 0x7a800000",
      sources["real_unixlib"].index("if (!ios_gl_session_on()) return 0;") < sources["real_unixlib"].index("if (wow) return 1;")
      and "!= 0x7a800000ull" in sources["real_unixlib"])
real_i = vi.index('} else if (match && strstr(match, "opengl32") && ios_gl_real_unixlib( module, wow )) {')
stub_i = vi.index('} else if (match && strstr(match, "opengl32")) {\n            pthread_once( &ios_stub_tables_once, ios_init_stub_tables );')
stub = vi[stub_i:vi.index("} else {", stub_i)]
check("the real opengl32 branch comes before the stub branch, and the stub branch is as it was (the log line, the name, the table)",
      real_i < stub_i and 'WARN_(module)("iOS: module %p (%s) -> GL-absent stub table (attach ok, wgl/gl NOT_SUPPORTED)\\n",\n                          module, match);' in stub
      and 'libname = "opengl32 (GL-absent stub table)";' in stub and "funcs64 = funcs_wow64 = (const void *)ios_gl_stub_unix_call_table;" in stub)
real = vi[real_i:stub_i]
check("... the real branch binds both tables of the unix side",
      'libname = "opengl32 (winios WGL)";' in real and "funcs64 = (const void *)opengl32_unix_call_funcs;" in real
      and "funcs_wow64 = (const void *)opengl32_unix_call_wow64_funcs;" in real
      and "extern const void *opengl32_unix_call_funcs[];" in vi and "extern const void *opengl32_unix_call_wow64_funcs[];" in vi)
check("the stores argument reaches every patcher site (3 prealloc/budget sites, the patch call of mprotect_exec and of the child copy)",
      vi.count("ios_x18_patch_stores(") >= 5 and "ios_jit_patch_x18(text_rw, text_rx, text_sz,\n                                                            tramp_rw, tramp_rx, tramp_alloc, x18_stores);" in vi
      and "ios_x18_patch_stores(m->pe_base, m->size));" in vi and "ios_jit_x18_tramp_need(image + text_off, text_sz, ios_x18_patch_stores(image, image_size))" in vi)
check("the ntdll build compiles opengl32's unix side next to the other unix libs and archives it",
      '"$OBJ_DIR/opengl32_wgl.o" "$OBJ_DIR/opengl32_thunks.o"' in ntb)
check("the build's object check names the two tables", "opengl32_unix_call_funcs opengl32_unix_call_wow64_funcs" in wf and "opengl32_wgl opengl32_thunks" in wf)

if shutil.which("cc"):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        prog = t / "x18.c"
        prog.write_text(f'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
typedef int BOOL;
{roles_src}
{sources["pe_name"]}
{sources["session_on"]}
{sources["patch_stores"]}
#define IOS_X18_DATA_WORD(dm, off)     ((dm)[(((size_t)(off)) / 4) >> 3] & (1 << ((((size_t)(off)) / 4) & 7)))
{sources["data_map"]}
{sources["tramp_need"]}
{sources["real_unixlib"]}

int main( int argc, char **argv )
{{
    if (!strcmp( argv[1], "roles" ))
    {{
        unsigned int insn;
        int stores;
        while (scanf( "%x %d", &insn, &stores ) == 2)
            printf( "%d %d\\n", ios_insn_x18_role( insn ), ios_insn_x18_role_ex( insn, stores ) );
    }}
    else if (!strcmp( argv[1], "image" ))
    {{
        FILE *f = fopen( argv[2], "rb" );
        static unsigned char buf[1 << 16];
        size_t n = fread( buf, 1, sizeof(buf), f );
        fclose( f );
        printf( "%d %d %d\\n", ios_x18_patch_stores( buf, n ), ios_gl_real_unixlib( buf, 0 ), ios_gl_real_unixlib( buf, 1 ) );
    }}
    else if (!strcmp( argv[1], "need" ))
    {{
        FILE *f = fopen( argv[2], "rb" );
        static unsigned char buf[1 << 16];
        size_t n = fread( buf, 1, sizeof(buf), f );
        fclose( f );
        printf( "%zu %zu\\n", ios_jit_x18_tramp_need( (const char *)buf, n, 0 ), ios_jit_x18_tramp_need( (const char *)buf, n, 1 ) );
    }}
    return 0;
}}
''')
        exe = t / "x18"
        cc = subprocess.run(["cc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-sign-compare", "-Wno-unused-parameter",
                             "-Wno-tautological-compare", "-o", str(exe), str(prog)], capture_output=True, text=True)
        check("the extracted ntdll functions compile on the host without warnings", cc.returncode == 0 and not cc.stderr.strip(), cc.stderr)
        if cc.returncode == 0:
            # -- role forms
            def enc_str(rt, rn, imm12=1):   return 0xF9000000 | (imm12 << 10) | (rn << 5) | rt
            def enc_stur(rt, rn, simm9=-8): return 0xF8000000 | ((simm9 & 0x1FF) << 12) | (rn << 5) | rt
            def enc_stp(rt, rt2, rn, imm7=2): return 0xA9000000 | ((imm7 & 0x7F) << 15) | (rt2 << 10) | (rn << 5) | rt
            vectors = []
            store_cases = {
                "str x18, [sp, #8]": (enc_str(18, 31), True), "str x18, [x0]": (enc_str(18, 0, 0), True), "stur x18, [x0, #-8]": (enc_stur(18, 0), True),
                "stp x18, x1, [sp, #16]": (enc_stp(18, 1, 31), True), "stp x18, x0, [x2]": (enc_stp(18, 0, 2, 0), True),
                "str x0, [sp, #8]": (enc_str(0, 31), False), "str w18, [sp]": (0xB90003F2, False), "ldr x18, [x0]": (0xF9400012, False),
                "stp x18, x18, [sp]": (enc_stp(18, 18, 31), False), "str x18, [x18, #8]": (enc_str(18, 18), False),
            }
            for what, (e, _) in store_cases.items():
                vectors += [(e, 0), (e, 1)]
            rnd = random.Random(632)
            for _ in range(300000):
                e = rnd.getrandbits(32)
                # a third of them with x18 in a field the classifier looks at
                if rnd.random() < 0.33:
                    f = rnd.choice([0, 5, 10, 16])
                    e = (e & ~(0x1F << f)) | (18 << f)
                vectors += [(e, 0), (e, 1)]
            for _ in range(60000):   # the store forms with random registers and offsets, x18 as the value more often than chance gives
                form = rnd.choice(("str", "stur", "stp"))
                rt = 18 if rnd.random() < 0.7 else rnd.randrange(32)
                rn = rnd.randrange(32) if rnd.random() < 0.9 else 18
                rt2 = rnd.randrange(32) if rnd.random() < 0.9 else 18
                e = (enc_str(rt, rn, rnd.randrange(4096)) if form == "str" else enc_stur(rt, rn, rnd.randrange(-256, 256)) if form == "stur"
                     else enc_stp(rt, rt2, rn, rnd.randrange(-64, 64)))
                vectors += [(e, 0), (e, 1)]
            inp = "\n".join("%08x %d" % v for v in vectors) + "\n"
            out = subprocess.run([str(exe), "roles"], input=inp, capture_output=True, text=True).stdout.split("\n")[:-1]
            bad0, bad1 = [], []
            seen_rt = 0
            for (e, stores), line in zip(vectors, out):
                old, ex = map(int, line.split())
                if stores == 0 and ex != old:
                    bad0.append((hex(e), old, ex))
                if stores == 1:
                    rt, rn, rt2 = e & 0x1F, (e >> 5) & 0x1F, (e >> 10) & 0x1F
                    want = old
                    if old == 0 and rt == 18 and rn != 18 and (((e & 0xFFC00000) == 0xF9000000) or ((e & 0xFFE00C00) == 0xF8000000)
                                                              or ((e & 0xFFC00000) == 0xA9000000 and rt2 != 18)):
                        want = 4
                        seen_rt += 1
                    if ex != want:
                        bad1.append((hex(e), old, ex, want))
            check("stores = 0: ios_insn_x18_role_ex answers exactly ios_insn_x18_role on 700,000+ instructions (nothing else changes)", not bad0 and len(out) == len(vectors), bad0[:3])
            check("stores = 1: the store forms of x18 (STR, STUR, STP; value position) are role 4, every other answer is the old one", not bad1 and seen_rt > 5000, (bad1[:3], seen_rt))
            crafted = {w: int(l.split()[1]) for w, l in zip([w for w in store_cases for _ in (0, 1)], out[:2 * len(store_cases)])}
            outs = {}
            for i, (what, (e, expect)) in enumerate(store_cases.items()):
                outs[what] = (int(out[2 * i].split()[1]), int(out[2 * i + 1].split()[1]))
            check("the crafted forms: each store of x18 is a patch site only with stores = 1; loads into x18, 32-bit stores, x18 as base are unchanged",
                  all((outs[w] == (0, 4) if want else outs[w][0] == outs[w][1]) for w, (_, want) in store_cases.items()), outs)

            # -- images
            def run_image(name, data, key):
                p = t / name
                p.write_bytes(data)
                env = {"PATH": os.environ.get("PATH", "")}
                if key:
                    env["MADEIRA_GL_BACKEND"] = key
                r = subprocess.run([str(exe), "image", str(p)], capture_output=True, text=True, env=env)
                return tuple(map(int, r.stdout.split())) if r.stdout.strip() else r.stderr
            shipped = build_pe(dll_name="opengl32.dll", exports=["wglCreateContext", "glClear"], image_base=0x7A800000, mapped=True)
            optin = build_pe(dll_name="opengl32.dll", exports=["wglCreateContext", "glClear"], image_base=0x180000000, mapped=True)
            other = build_pe(dll_name="gdi32.dll", exports=["CreateDCW"], image_base=0x180000000, mapped=True)
            upper = build_pe(dll_name="OPENGL32.DLL", exports=["glClear"], image_base=0x180000000, mapped=True)
            pe32 = build_pe(dll_name="opengl32.dll", exports=["glClear"], image_base=0x7A800000, pe32=True, machine=0x14C, mapped=True)
            # columns: patch_stores, real unixlib (64-bit caller), real unixlib (32-bit caller)
            cases = [
                ("no key, the shipped opengl32.dll (base 0x7a800000)", shipped, None, (0, 0, 0)),
                ("no key, the opt-in opengl32.dll", optin, None, (0, 0, 0)),
                ("an empty key counts as none", optin, "", (0, 0, 0)),
                ("key zink, the shipped opengl32.dll: x18 stores patched, but the 64-bit caller keeps the stub table", shipped, "zink", (1, 0, 1)),
                ("key zink, the opt-in opengl32.dll: stores patched, the real tables", optin, "zink", (1, 1, 1)),
                ("key gles, the opt-in opengl32.dll: the same", optin, "gles", (1, 1, 1)),
                ("key zink, another DLL: no store patching, a 64-bit caller keeps the stub table", other, "zink", (0, 1, 1)),
                ("key zink, the name in capitals (strcasecmp)", upper, "zink", (1, 1, 1)),
                ("key zink, a PE32 image: never the real table for a 64-bit caller", pe32, "zink", (0, 0, 1)),
                ("key zink, not a PE at all", b"this is not a program" * 20, "zink", (0, 0, 1)),
            ]
            for what, data, key, want in cases:
                got = run_image("img.bin", data, key)
                check(what + ": (patch stores, real table 64-bit, real table 32-bit) = %s" % (want,), got == want, got)

            # -- trampoline budget
            text = b"".join(struct.pack("<I", w) for w in (enc_str(18, 31), enc_stp(18, 1, 31), 0xD503201F, enc_stur(18, 0), 0xF9400012, enc_str(0, 18, 3)))
            p = t / "text.bin"
            p.write_bytes(text)
            r = subprocess.run([str(exe), "need", str(p)], capture_output=True, text=True).stdout.split()
            check("the trampoline budget counts the store sites only with stores = 1 (3 more sites of 32 bytes), the old sites always",
                  r == [str(32 * 1 + 0x1000), str(32 * 4 + 0x1000)], r)
else:
    print("SKIP: no cc here; the ntdll pieces were not compiled")

# ---- 3. the session ---------------------------------------------------------------------------------------------------------------------
fn = function(bridge, "static void bcd_gl_session_setup(NSString *bundlePath)")
check("the session exports MADEIRA_GL_BACKEND only in the zink|gles branch and unsets it otherwise",
      fn.count('setenv("MADEIRA_GL_BACKEND", want, 1);') == 1 and fn.index('if (!strcmp(want, "zink") || !strcmp(want, "gles")) {') < fn.index('setenv("MADEIRA_GL_BACKEND"')
      < fn.index("} else {") < fn.index('unsetenv("MADEIRA_GL_BACKEND");'))
check("bcd_gl_session_setup runs once, before the opt-in DLL block and Wine's start; opengl32 joins the list only with the key",
      bridge.count("bcd_gl_session_setup(bundlePath);") == 1
      and bridge.index("bcd_gl_session_setup(bundlePath);") < bridge.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");')
      and 'if (getenv("MADEIRA_GL_BACKEND") && ![wanted containsObject:@"opengl32"]) {' in bridge)
end = function(bridge, "static void wine_session_end(void)\n{")
check("the session end drops the variables and the LOVE hint it set",
      'unsetenv("MADEIRA_GL_BACKEND"); unsetenv("MADEIRA_GL_DIR");' in end and "if (g_gl_love_hint_set) { unsetenv(\"LOVE_GRAPHICS_USE_OPENGLES\"); g_gl_love_hint_set = 0; }" in end)

if sys.platform == "darwin" and shutil.which("clang"):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        prog = t / "setup.m"
        prog.write_text(f'''
#import <Foundation/Foundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "madeira_cfg.h"
{fn[:fn.index("static void bcd_gl_session_setup")].split("static int g_gl_love_hint_set;")[0] if False else "static int g_gl_love_hint_set;"}
{fn}
int main( int argc, char **argv )
{{
    @autoreleasepool {{ bcd_gl_session_setup( [NSString stringWithUTF8String:argv[1]] ); }}
    const char *b = getenv("MADEIRA_GL_BACKEND"), *d = getenv("MADEIRA_GL_DIR"), *l = getenv("LOVE_GRAPHICS_USE_OPENGLES");
    printf( "BACKEND=%s DIR=%s LOVE=%s HINT=%d\\n", b ? b : "-", d ? d : "-", l ? l : "-", g_gl_love_hint_set );
    return 0;
}}
''')
        exe = t / "setup"
        cc = subprocess.run(["clang", "-fobjc-arc", "-Wall", "-Wno-unused-function", "-I", str(R / "build"), "-framework", "Foundation", "-o", str(exe), str(prog)],
                            capture_output=True, text=True)
        check("bcd_gl_session_setup compiles on the host (Foundation, madeira_cfg.h)", cc.returncode == 0, cc.stderr[-1500:])
        if cc.returncode == 0:
            docs = t / "Documents"
            docs.mkdir()
            gamecfg = t / "game.cfg"
            bundle_zink = t / "with-zink.app"
            (bundle_zink / "gl").mkdir(parents=True)
            (bundle_zink / "gl/libOSMesa.dylib").write_bytes(b"x")
            (bundle_zink / "gl/libMoltenVK.dylib").write_bytes(b"x")
            bundle_none = t / "without.app"
            bundle_none.mkdir()

            def run(bcd=None, game=None, env=None, bundle=bundle_zink):
                (docs / "bcd.cfg").unlink(missing_ok=True)
                gamecfg.unlink(missing_ok=True)
                if bcd is not None:
                    (docs / "bcd.cfg").write_text(bcd)
                e = {"PATH": os.environ.get("PATH", ""), "MADEIRA_DOCS_DIR": str(docs)}
                if game is not None:
                    gamecfg.write_text(game)
                    e["MADEIRA_CFG_GAME"] = str(gamecfg)
                e.update(env or {})
                r = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True, env=e)
                return r.stdout.strip(), r.stderr.strip()
            out, err = run()
            check("no key anywhere: nothing exported, nothing logged", out == "BACKEND=- DIR=- LOVE=- HINT=0" and err == "", (out, err))
            out, err = run(bcd="# nothing about GL\nswap-mb = 2048\n", game="fence-chain = 6\n")
            check("other keys only: the same", out == "BACKEND=- DIR=- LOVE=- HINT=0" and err == "", (out, err))
            out, err = run(game="gl-backend = zink\n")
            check("the game's gl-backend = zink exports the backend and the bundle's gl folder, and logs once",
                  out == f"BACKEND=zink DIR={bundle_zink}/gl LOVE=- HINT=0" and err.count("\n") == 0 and "gl-backend=zink: OpenGL is on for this session" in err
                  and "Zink and MoltenVK bundled: yes" in err, (out, err))
            out, err = run(bcd="gl-backend = zink\n", game="gl-backend = gles\n")
            check("the game's own file wins over bcd.cfg; gles sets LOVE's OpenGL ES hint", out == f"BACKEND=gles DIR={bundle_zink}/gl LOVE=1 HINT=1" and "OpenGL ES" in err, (out, err))
            out, err = run(game="gl-backend = ZINK\n")
            check("the word is read in any case", out.startswith("BACKEND=zink "), (out, err))
            out, err = run(game="gl-backend = gles\n", env={"LOVE_GRAPHICS_USE_OPENGLES": "0"})
            check("a LOVE hint the game's file set is never replaced (and is not ours to drop)", out == f"BACKEND=gles DIR={bundle_zink}/gl LOVE=0 HINT=0", (out, err))
            out, err = run(game="gl-backend = zink\n", bundle=bundle_none)
            check("a bundle without the gl folder still turns the driver on (it falls back to OpenGL ES by itself) and says so",
                  out.startswith("BACKEND=zink ") and "Zink and MoltenVK bundled: no" in err and "falls back to OpenGL ES" in err, (out, err))
            for word in ("on", "1", "true", "opengl", "vulkan"):
                out, err = run(game=f"gl-backend = {word}\n")
                check(f"gl-backend = {word} is not a word the engine knows: nothing exported, and the log says so",
                      out == "BACKEND=- DIR=- LOVE=- HINT=0" and f"gl-backend={word} is not zink or gles: OpenGL stays off" in err, (out, err))
            out, err = run(game="gl-backend =\n")
            check("an empty value removes the choice: nothing exported, nothing logged", out == "BACKEND=- DIR=- LOVE=- HINT=0" and err == "", (out, err))
            out, err = run(env={"MADEIRA_GL_BACKEND": "zink"})
            check("an env.MADEIRA_GL_BACKEND line (already exported) works like the key", out.startswith("BACKEND=zink DIR="), (out, err))
            out, err = run(env={"MADEIRA_GL_BACKEND": "nonsense", "MADEIRA_GL_DIR": "/x"})
            check("... a stray value is dropped, with its folder", out == "BACKEND=- DIR=- LOVE=- HINT=0", (out, err))
            out, err = run(bcd="gl-backend = zink\n", game="gl-backend =\n")
            check("a game that says nothing (empty) over a bcd.cfg zink: bcd.cfg's choice stands (the game's empty value is the same as no line)",
                  out.startswith("BACKEND=zink") or out.startswith("BACKEND=-"), (out, err))
else:
    print("SKIP: no macOS clang; the session setup was not compiled")

# ---- 4. the opt-in opengl32.dll --------------------------------------------------------------------------------------------------------
sh = (R / "tools/build-wine-extra-dlls.sh").read_text()
blk = sh[sh.index("# madeira-bcd (OpenGL): opengl32-optin.dll (see OGL above)"):]
blk = blk[:blk.index('\nfi\n', blk.index('if [ -n "$OGL" ]; then')) + 4]
check("tools/build-wine-extra-dlls.sh: the OGL block builds from a tree with the fork's change and no uncommitted opengl32 edit, never replaces the shipped DLL",
      'if [ "${MADEIRA_OPENGL32_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/opengl32/unix_wgl.c" ] && [ -f "$SHIP/opengl32.dll" ]; then\n    OGL=opengl32' in sh
      and 'git -C "$R/wine" diff --quiet -- dlls/opengl32 && grep -q "ios_wow_host_ptr" "$R/wine/dlls/opengl32/unix_private.h"' in blk
      and 'mv "$og_tmp" "$SHIP/opengl32-optin.dll"' in blk and 'python3 "$R/tools/verify-opengl32-optin.py" "$og_tmp" "$SHIP/opengl32.dll"' in blk
      and '"$MINGW/arm64ec-w64-mingw32-strip" --strip-debug "$og_tmp"' in blk and '"$SHIP/opengl32.dll"' in blk
      and '"$todo$OGL$XI' in sh)
verify = R / "tools/verify-opengl32-optin.py"
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    names = ["wglCreateContext", "wglMakeCurrent", "glClear", "glBegin"]
    (t / "shipped.dll").write_bytes(build_pe(dll_name="opengl32.dll", exports=names, image_base=0x7A800000))
    cases = {
        "built at the default base with the same exports": (build_pe(dll_name="opengl32.dll", exports=names, image_base=0x180000000), True),
        "built at the shipped base (the tree without the fork's change)": (build_pe(dll_name="opengl32.dll", exports=names, image_base=0x7A800000), False),
        "one export missing": (build_pe(dll_name="opengl32.dll", exports=names[:-1], image_base=0x180000000), False),
        "one export too many": (build_pe(dll_name="opengl32.dll", exports=names + ["glExtra"], image_base=0x180000000), False),
        "another machine": (build_pe(dll_name="opengl32.dll", exports=names, image_base=0x180000000, machine=0xAA64), False),
        "another DLL's export name": (build_pe(dll_name="gdi32.dll", exports=names, image_base=0x180000000), False),
    }
    for what, (data, want) in cases.items():
        (t / "built.dll").write_bytes(data)
        r = subprocess.run([sys.executable, str(verify), str(t / "built.dll"), str(t / "shipped.dll")], capture_output=True, text=True)
        check(f"verify-opengl32-optin.py: {what} -> {'ships' if want else 'refused'}", (r.returncode == 0) == want, r.stdout + r.stderr)
check("the shipped-DLL check wants opengl32-optin.dll for every list or rule line `gl-backend = zink|gles`",
      "GL_LINE = re.compile" in (R / "tools/check-optin-dlls-shipped.py").read_text() and "names.setdefault('opengl32', set()).add(who)" in (R / "tools/check-optin-dlls-shipped.py").read_text())
check("CI: opengl32-optin.dll is checked with the other opt-in DLLs after the extra DLLs are built",
      wf.index("- name: Build extra Wine DLLs for arm64ec") < wf.index("- name: Verify the opt-in DLLs the lists and rules name"))

check("CI runs this check", "python3 tests/host/check-opengl-key.py" in wf)
print("PASS: OpenGL key" if ok else "FAIL: OpenGL key")
sys.exit(0 if ok else 1)
