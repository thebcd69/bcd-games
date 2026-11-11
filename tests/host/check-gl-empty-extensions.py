#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""env.MADEIRA_GL_EMPTY_EXTENSIONS = 1: a 64-bit glGetString(GL_EXTENSIONS) gets "" instead of NULL (virtual_ios.c, OpenGL stub table).

Far Cry 4's hardware detection (Ubisoft's systemdetection64.dll) ignores that its OpenGL context could not be created, calls
glGetString(GL_EXTENSIONS) and passes the result to its own strstr, whose ASSERT(string) ends in an int3: the game exits with
0x80000003 right after "DetectionOpenGlWindow".

Checks:
  1. the stub tables cut from virtual_ios.c, compiled with -Wall -Wextra -Werror and ASan/UBSan, the switch set per call in one
     process: unset, "", "0", "11" and "true" leave code 130 NOT_SUPPORTED with ret untouched; "1" answers GL_EXTENSIONS with
     SUCCESS and an empty string and logs once per thread, leaves GL_VENDOR and GL_VERSION as before, and gives a 32-bit caller
     (WowTebOffset set) no answer; every call still marks the calling thread's process once; the other codes fail as before;
  2. code 130 is glGetString: read from the shipped app/Madeira/arm64ec-windows/opengl32.dll (its glGetString export's x64
     thunk, then the `mov w1, #code` before the unix call), and from wine/dlls/opengl32/unixlib.h when the submodule is present;
  3. the wiring: both bitnesses still get the stub table (the binder is unchanged); the catalog documents the key, off by
     default; a list may set it, 0 or 1; the session end clears it; CI runs this check.
NOT covered: what Far Cry 4 does after the detection (device only); the session's gl-backend key, which binds the real OpenGL
unix side instead of this table.
"""
from pathlib import Path
import os, re, shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/virtual_ios.c").read_text()
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
ok = True


def check(what, cond, detail=""):
    global ok
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:2000]) if detail and not cond else ""))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- 1. the tables, per value of the switch
stubs = src[src.index("static NTSTATUS ios_stub_unix_call(void *args) {"):src.index("/* DXMT's unix call table, statically linked")]
harness = r"""
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int NTSTATUS;
typedef NTSTATUS (*unixlib_entry_t)(void *);
#define STATUS_SUCCESS ((NTSTATUS)0)
#define STATUS_NOT_SUPPORTED ((NTSTATUS)0xC00000BB)
typedef struct { int WowTebOffset; } TEB;
static _Thread_local TEB teb;
static TEB *NtCurrentTeb(void) { return &teb; }
static int marks;
void *ios_jit_current_peb(void) { return (void *)0x1000; }
void ios_child_slot_gl_call(void *peb) { (void)peb; marks++; }
""" + stubs + r"""
struct p64 { void *teb; unsigned int name; const unsigned char *ret; };
static const unsigned char sentinel[] = "untouched";
static const char *call( unsigned int name )   /* "ns" (NOT_SUPPORTED, ret untouched), "empty" (SUCCESS, ""), or "bad" */
{
    struct p64 p = { 0, name, sentinel };
    NTSTATUS s = ios_gl_stub_unix_call_table[130]( &p );
    if (s == STATUS_NOT_SUPPORTED && p.ret == sentinel) return "ns";
    if (s == STATUS_SUCCESS && p.ret && !p.ret[0]) return "empty";
    return "bad";
}
static void *thread( void *arg )
{
    (void)arg;
    printf( "thread ext=%s\n", call( 0x1F03 ) );
    return NULL;
}
int main( int argc, char **argv )
{
    pthread_t t;
    int i;
    pthread_once( &ios_stub_tables_once, ios_init_stub_tables );
    for (i = 1; i < argc; i++)   /* each argument: a value of the switch, or "-" for unset; "wow:" first for a 32-bit caller */
    {
        const char *v = argv[i];
        teb.WowTebOffset = !strncmp( v, "wow:", 4 ) ? (v += 4, 0x2000) : 0;
        if (!strcmp( v, "-" )) unsetenv( "MADEIRA_GL_EMPTY_EXTENSIONS" );
        else setenv( "MADEIRA_GL_EMPTY_EXTENSIONS", v, 1 );
        printf( "%s ext=%s vendor=%s version=%s\n", argv[i], call( 0x1F03 ), call( 0x1F00 ), call( 0x1F02 ) );
    }
    teb.WowTebOffset = 0;
    pthread_create( &t, NULL, thread, NULL );
    pthread_join( t, NULL );
    printf( "marks=%d other=%d,%d attach=%d\n", marks, ios_gl_stub_unix_call_table[3]( NULL ) == STATUS_NOT_SUPPORTED,
            ios_gl_stub_unix_call_table[131]( NULL ) == STATUS_NOT_SUPPORTED, ios_gl_stub_unix_call_table[0]( NULL ) == STATUS_SUCCESS );
    return 0;
}
"""
if not cc:
    check("a C compiler for the host test", False, "no cc/clang/gcc")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "h.c").write_text(harness)
        exe = t / "h"
        flags = ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter", "-g", "-O1", "-pthread",
                 "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        r = subprocess.run([cc, *flags, str(t / "h.c"), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode:   # a compiler without the sanitizer runtimes
            r = subprocess.run([cc, *[f for f in flags if "sanitize" not in f], str(t / "h.c"), "-o", str(exe)],
                               capture_output=True, text=True)
        check("the stub tables compile with -Wall -Wextra -Werror", r.returncode == 0, r.stderr)
        if r.returncode == 0:
            values = ["-", "", "0", "11", "true", "1", "wow:1", "1", "0", "-", "1"]
            env = {k: v for k, v in os.environ.items() if k != "MADEIRA_GL_EMPTY_EXTENSIONS"}
            p = subprocess.run([str(exe), *values], capture_output=True, text=True, env=env)
            got = p.stdout.splitlines()
            ns, ans = "ext=ns vendor=ns version=ns", "ext=empty vendor=ns version=ns"
            want = ["%s %s" % (v, ans if v == "1" else ns) for v in values]
            check("only exactly 1 answers GL_EXTENSIONS, with SUCCESS and \"\"; GL_VENDOR and GL_VERSION fail as before; a 32-bit "
                  "caller gets no answer; the switch is read at each call", p.returncode == 0 and got[:len(values)] == want,
                  (p.returncode, got, p.stderr[-1500:]))
            check("another thread with the switch on gets the answer too", got[len(values):len(values) + 1] == ["thread ext=empty"], got)
            check("every call still marks its thread's process once (two threads: two marks); other codes fail; attach succeeds",
                  got[len(values) + 1:] == ["marks=2 other=1,1 attach=1"], got)
            check("one [gl-empty-ext] line per thread that got the answer", p.stderr.count("[gl-empty-ext]") == 2, p.stderr[-1500:])


# ---------------------------------------------------------------- 2. code 130 is glGetString
def dll_code(path):
    d = path.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    n = struct.unpack_from("<H", d, pe + 6)[0]
    osz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    secs = []
    for i in range(n):
        s = opt + osz + i * 40
        vs, va, rs, ro = struct.unpack_from("<IIII", d, s + 8)
        secs.append((va, max(vs, rs), ro))

    def off(rva):
        for va, size, ro in secs:
            if va <= rva < va + size:
                return ro + rva - va
        raise ValueError("rva %#x outside every section" % rva)

    e = off(struct.unpack_from("<I", d, opt + 0x70)[0])
    nn, af, an, ao = struct.unpack_from("<IIII", d, e + 24)
    for i in range(nn):
        name_off = off(struct.unpack_from("<I", d, off(an) + 4 * i)[0])
        if d[name_off:d.index(b"\0", name_off)] != b"glGetString":
            continue
        rva = struct.unpack_from("<I", d, off(af) + 4 * struct.unpack_from("<H", d, off(ao) + 2 * i)[0])[0]
        at = off(rva)
        j = d.find(b"\xe9", at, at + 16)                              # the x64 thunk's jmp rel32 to the ARM64EC body
        if j < 0:
            raise ValueError("no jmp in glGetString's x64 thunk")
        body = rva + (j - at) + 5 + struct.unpack_from("<i", d, j + 1)[0]
        code = None
        for k in range(64):
            w = struct.unpack_from("<I", d, off(body) + 4 * k)[0]
            if (w & 0xffe0001f) == 0x52800001:                         # movz w1, #imm16
                code = (w >> 5) & 0xffff
            if (w & 0xfc000000) == 0x94000000 and code is not None:   # bl __wine_unix_call
                return code
        raise ValueError("no `mov w1, #code; bl` in glGetString")
    raise ValueError("no glGetString export")


try:
    shipped = dll_code(root / "app/Madeira/arm64ec-windows/opengl32.dll")
except (OSError, ValueError, struct.error) as error:
    shipped = error
check("the shipped arm64ec-windows/opengl32.dll's glGetString passes unix code 130", shipped == 130, shipped)
check("the source answers that code", "#define IOS_GL_UNIX_GLGETSTRING 130" in src and
      "ios_gl_stub_unix_call_table[IOS_GL_UNIX_GLGETSTRING] = ios_gl_stub_get_string;" in src)
hdr = Path(os.environ.get("WINE_SRC") or (root / "wine")) / "dlls/opengl32/unixlib.h"
if hdr.is_file():
    h = hdr.read_text(errors="replace")
    enum = h[h.index("enum unix_funcs"):]
    names = re.findall(r"^\s*(unix_\w+),", enum[:enum.index("};")], re.M)
    check("wine/dlls/opengl32/unixlib.h: unix_glGetString is entry 130", "unix_glGetString" in names and names.index("unix_glGetString") == 130)
    check("its 64-bit arguments are { TEB *teb; GLenum name; const GLubyte *ret; }",
          re.search(r"struct glGetString_params\s*\{\s*TEB \*teb;\s*GLenum name;\s*const GLubyte \*ret;\s*\};", h))
else:
    print("note: wine/dlls/opengl32/unixlib.h is not checked out; the shipped DLL above is the authority")

# ---------------------------------------------------------------- 3. the wiring
gl_branch = src[src.index('} else if (match && strstr(match, "opengl32")) {'):]
gl_branch = gl_branch[:gl_branch.index("} else {")]
check("opengl32's stub branch still hands the one table to both bitnesses",
      "funcs64 = funcs_wow64 = (const void *)ios_gl_stub_unix_call_table;" in gl_branch)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the catalog documents the key, off by default; a list may set it, 0 or 1; the session end clears it; CI runs this check",
      re.search(r'"env\.MADEIRA_GL_EMPTY_EXTENSIONS": \{[^}]*"default": "0"', gen, re.S) is not None
      and 'key: "env.MADEIRA_GL_EMPTY_EXTENSIONS"' in cat and '"MADEIRA_GL_EMPTY_EXTENSIONS",' in pol
      and 'case "MADEIRA_GL_EMPTY_EXTENSIONS":' in pol and 'unsetenv("MADEIRA_GL_EMPTY_EXTENSIONS");' in bridge
      and "python3 tests/host/check-gl-empty-extensions.py" in wf)
print("PASS: gl-empty-extensions" if ok else "FAILED: gl-empty-extensions")
sys.exit(0 if ok else 1)
