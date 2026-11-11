#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wineserver-object-open-trace.py: the wineserver names the object a failed open asked for (night round 2026-11-11, A1 S4;
Horizon Zero Dawn in BCD Dock: "SteamAPI_Init() failed; create pipe failed." and no log says which object the library could not open).

Checks: CI runs this check and then the patch before the server is built; the iOS wineserver compiles wine/server/object.c (so the patch
reaches it); the patch applies to a copy of the pinned wine/server/object.c once, then says "already patched"; an anchor that moved fails by name
and leaves the file alone; the open itself is unchanged (the hook sits on the failure path, after the objects are released, WINE_IOS only, and
nothing else changed); the real helper, cut from the patched file and compiled on the host against a fake object tree, prints nothing with the key
unset or 0, one documented line per failed open with it set (type, directory path, ASCII name, status, attributes), `-` for a missing type or
directory, `?` for a non-ASCII character, and stops at 200 lines; with the iOS SDK (the Mac) the patched file passes `clang -fsyntax-only` with
the wineserver build's own flags. SKIP of the file part (exit 0) when no wine source is available.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check and then the patch, before the wineserver is built",
      "python3 tests/host/check-wineserver-object-open-trace.py\n          python3 tools/patch-wineserver-object-open-trace.py wine" in wf
      and wf.index("python3 tools/patch-wineserver-object-open-trace.py wine") < wf.index("bash build/wineserver/build.sh"))
build = (root / "build/wineserver/build.sh").read_text()
check("the iOS wineserver compiles wine/server/object.c (so the patch reaches it)", '"object:$WINE_SRC/server/object.c:object.o"' in build)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
i = cat.find('"env.MADEIRA_OBJ_TRACE"')
check("the key is in the settings catalog (default off)", i > 0 and '"default": "0"' in cat[i:i + 700])

wine = None
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine"):
    if (cand / "server/object.c").is_file():
        wine = cand
        break
if not wine:
    print("SKIP: no wine source (check out the submodule or set WINE_SRC)")
    print("PASS: wineserver object open trace (static part)" if ok else "FAIL: wineserver object open trace")
    sys.exit(0 if ok else 1)

patch = root / "tools/patch-wineserver-object-open-trace.py"
pristine = (wine / "server/object.c").read_text()
if "obj-open-trace-v1" in pristine:
    print("SKIP: the wine tree is already patched; the checks need the pristine file")
    print("PASS: wineserver object open trace (static part)" if ok else "FAIL: wineserver object open trace")
    sys.exit(0 if ok else 1)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "server").mkdir()
    (t / "server/object.c").write_text(pristine)
    p1 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
    p2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
    check("applies once, then says already patched", p1.returncode == 0 and "line added" in p1.stdout
          and p2.returncode == 0 and "already patched" in p2.stdout, p1.stdout + p1.stderr + p2.stdout)
    after = (t / "server/object.c").read_text()
    fn = after[after.index("void *open_named_object("):]
    fn = fn[:fn.index("\n}\n")]
    check("the hook is on the failure path only (after the object is released), WINE_IOS only; the successful return is unchanged",
          fn.index("            return obj;") < fn.index("        release_object( obj );") < fn.index("#ifdef WINE_IOS")
          < fn.index("madeira_obj_open_fail( parent, ops, name, attributes );") < fn.index("#endif") < fn.index("    return NULL;"))
    check("nothing else changed", re.sub(r"#ifdef WINE_IOS\n/\* madeira-bcd obj-open-trace-v1.*?#endif\n\n", "", after, flags=re.S).replace(
              "#ifdef WINE_IOS\n    madeira_obj_open_fail( parent, ops, name, attributes );  /* madeira-bcd obj-open-trace-v1 */\n#endif\n", "") == pristine)
    moved = pristine.replace("        else if (ops && obj->ops != ops)\n", "        else if (ops && obj->ops != ops )\n", 1)
    (t / "server/object.c").write_text(moved)
    p3 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
    check("a moved anchor fails by name, the file unchanged", p3.returncode != 0 and "open_named_object in server/object.c" in p3.stdout
          and (t / "server/object.c").read_text() == moved, p3.stdout + p3.stderr)

    helper = after[after.index("#ifdef WINE_IOS\n/* madeira-bcd obj-open-trace-v1"):]
    helper = helper[:helper.index("#endif\n") + len("#endif\n")]
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned short WCHAR;
typedef unsigned int data_size_t;
#define WINE_IOS 1
struct unicode_str { const WCHAR *str; data_size_t len; };
struct type_descr { struct unicode_str name; };
struct object_ops { struct type_descr *type; };
struct object;
struct object_name { struct object *parent; data_size_t len; WCHAR name[1]; };
struct object { struct object_name *name; };
struct process { unsigned int id; };
struct thread { unsigned int id; struct process *process; unsigned int error; };
static struct thread *current;
static unsigned int get_error(void) { return current ? current->error : 0; }
@HELPER@
static struct object *dir(struct object *parent, const char *s)
{
    size_t n = strlen(s), i;
    struct object_name *nm = calloc(1, sizeof(*nm) + n * 2);
    struct object *o = calloc(1, sizeof(*o));
    nm->parent = parent; nm->len = n * 2;
    for (i = 0; i < n; i++) nm->name[i] = (unsigned char)s[i];
    o->name = nm;
    return o;
}
static struct unicode_str us(const char *s, WCHAR *buf)
{
    struct unicode_str u; size_t i, n = strlen(s);
    for (i = 0; i < n; i++) buf[i] = (unsigned char)s[i];
    u.str = buf; u.len = n * 2; return u;
}
int main(int argc, char **argv)
{
    WCHAR b[256];
    static WCHAR ev[] = { 'E', 'v', 'e', 'n', 't' };
    struct type_descr tev = { { ev, sizeof(ev) } };
    struct object_ops ops = { &tev };
    struct process proc = { 0x50 };
    struct thread th = { 0x54, &proc, 0xc0000034 };
    struct object *bno = dir(dir(NULL, "BaseNamedObjects"), "Session1");
    struct unicode_str n1 = us("Global\\SteamClient_X", b), n2;
    int mode = atoi(argv[1]), i;
    current = &th;
    switch (mode)
    {
    case 0: madeira_obj_open_fail(bno, &ops, &n1, 0x80); break;
    case 1: /* no type, no directory, a non-ASCII character, no thread */
        b[0] = 'a'; b[1] = 0x20ac; b[2] = 'z'; n2.str = b; n2.len = 6;
        current = NULL;
        madeira_obj_open_fail(NULL, NULL, &n2, 0x40); break;
    case 2: for (i = 0; i < 300; i++) madeira_obj_open_fail(bno, &ops, &n1, 0x80); break;
    case 3: madeira_obj_open_fail(bno, &ops, &n1, 0x80); setenv("MADEIRA_OBJ_TRACE", "1", 1); madeira_obj_open_fail(bno, &ops, &n1, 0x80); break;
    }
    return 0;
}
'''
    (t / "h.c").write_text(harness.replace("@HELPER@", helper))
    cc = shutil.which("cc")
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the inserted helper compiles on the host", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        def run(mode, key):
            env = dict(os.environ)
            env.pop("MADEIRA_OBJ_TRACE", None)
            if key is not None:
                env["MADEIRA_OBJ_TRACE"] = key
            return subprocess.run([str(t / "h"), str(mode)], capture_output=True, text=True, env=env).stderr.splitlines()
        check("key unset: nothing is printed", run(0, None) == [], run(0, None))
        check("key 0: nothing is printed", run(0, "0") == [], run(0, "0"))
        l = run(0, "1")
        check("key 1: one documented line (opener, type, directory path, name, status, attributes, counter)",
              l == ["[obj-open-fail] pid=0050 tid=0054 type=Event parent=\\BaseNamedObjects\\Session1 name=Global\\SteamClient_X status=c0000034 attrs=0x80 #1 rev=r652"], l)
        l = run(1, "1")
        check("no type and no directory print `-`, a non-ASCII character `?`, no thread pid/tid 0",
              l == ["[obj-open-fail] pid=0000 tid=0000 type=- parent=- name=a?z status=00000000 attrs=0x40 #1 rev=r652"], l)
        l = run(2, "1")
        check("200 lines at most", len(l) == 200 and l[-1].endswith("#200 rev=r652"), len(l))
        l = run(3, None)
        check("the key is read at every failed open: a value exported after the first failure counts from then on",
              len(l) == 1 and l[0].endswith("#1 rev=r652"), l)

    sdk = shutil.which("xcrun")
    if not sdk or not (wine / "build-macos/include").is_dir():
        print("SKIP: the iOS-SDK syntax check needs Xcode and the full tree's wine/build-macos/include (the Mac)")
    else:
        (t / "server/object.c").write_text(after)
        tree = t / "tree"
        (tree / "server").mkdir(parents=True, exist_ok=True)
        for f in (wine / "server").glob("*.h"):
            shutil.copy(f, tree / "server" / f.name)
        shutil.copy(t / "server/object.c", tree / "server/object.c")
        sdkpath = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        bdir = root / "build/wineserver"
        cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdkpath, "-miphoneos-version-min=17.0", "-O2",
               "-I" + str(wine / "include"), "-I" + str(wine / "include/wine"), "-I" + str(wine / "build-macos/include"), "-I" + str(bdir),
               "-I" + str(tree / "server"), "-I" + str(root / "build/ntdll-unix/shims"), "-I" + str(root / "build/madsync"), "-DHAVE_LINUX_NTSYNC_H=1",
               "-include", str(bdir / "config_ios.h"), "-include", "stdarg.h", "-include", str(bdir / "unicode_fix.h"),
               "-include", str(bdir / "wineserver_ios_kill.h"), '-DBINDIR="/usr/local/bin"', '-DDATADIR="/usr/local/share"', "-D__WINESRC__",
               "-DWINE_IOS=1", "-Dmain=wineserver_main", "-Wno-implicit-function-declaration", str(tree / "server/object.c")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        check("the patched object.c passes clang -fsyntax-only with the iOS SDK and the wineserver build's flags (no error)",
              r.returncode == 0 and "error" not in r.stderr, r.stderr[-1200:])

print("PASS: wineserver object open trace" if ok else "FAIL: wineserver object open trace")
sys.exit(0 if ok else 1)
