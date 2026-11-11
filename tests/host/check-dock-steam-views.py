#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-steam-views.py: the Dock mirrors its Steam discovery entries into the registry view the game reads, and replays steam_api's five
calls (night round 2026-11-11, X2 S1 and S4b; K7QKB6, 25C9NT, 9HEY99, NR2WGS, KQMTVT).

Checks:
  - CI runs this check and applies the patch after tools/patch-dock-steam-readback.py and before the Dock build, without failing the step; the app keeps
    the six report fields; the catalog lists MADEIRA_DOCK_STEAM_VIEWS (default on);
  - Dock source (madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; skipped without it): on a copy of src/launch.c after the earlier launch.c patches,
    the patch applies once and only ADDS lines; a second run changes nothing; a moved anchor and a missing readback patch refuse it;
  - the helpers, built on the host over a toy registry that models Wine's views (a 32-bit view lands in HKCU Software\\Wow6432Node when that node exists,
    else in the plain key): without the node the mirror writes nothing new and the probe says 63 before and after; with the node and no Valve tree the probe
    says 0 before and 63 after, and the restore takes the two values out again; with a stale tree (a dead client's pid) the probe says 7 before and 63 after and
    the restore puts the stale values back; a second publish changes nothing (the previous values are recorded once);
  - the patched host compiles with build/madeira-dock/build.sh's flags together with every Dock patch of the workflow, where llvm-mingw is at hand.
"""
from pathlib import Path
import difflib, importlib.util, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("steamviews", root / "tools/patch-dock-steam-views.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the readback patch, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-steam-views.py\n" in step
      and 'python3 tools/patch-dock-steam-views.py madeira-dock/src/launch.c || echo "::warning::' in step
      and step.index("patch-dock-steam-readback.py") < step.index("patch-dock-steam-views.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the seven report fields",
      all('"%s"' % k in allowed for k in ("launch-view-mirror", "launch-view-error", "launch-host-pid", "launch-steamapi-probe-pre", "launch-steamapi-probe",
                                           "launch-steamapi-probe-t", "launch-steamapi-probe-running")))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_STEAM_VIEWS, default on, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_STEAM_VIEWS": \{[^}]*"default": "1"[^}]*"sources": \["tools/patch-dock-steam-views\.py"\]', cat, re.S) is not None)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/launch.c").read_text()
    if P.MARKER in orig or "madeira-bcd: launch-discovery-readback" in orig:
        print("note: the Dock tree is already patched; the source checks are skipped")
    else:
        with tempfile.TemporaryDirectory(prefix="madeira-dock-steam-views-") as td:
            t = Path(td)
            shutil.copytree(dock / "src", t / "src")
            lc = t / "src/launch.c"
            script = str(root / "tools/patch-dock-steam-views.py")
            r0 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            check("without the readback patch it refuses and leaves the file alone",
                  r0.returncode != 0 and "readback" in r0.stderr and lc.read_text() == orig, r0.stdout + r0.stderr)
            for patch in ("tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py",
                          "tools/patch-dock-install-dir-events.py", "tools/patch-dock-registry-events.py", "tools/patch-dock-registry-create.py"):
                r = subprocess.run([sys.executable, str(root / patch), str(lc)], capture_output=True, text=True)
                check("the workflow's earlier patch %s applies to the copy" % patch, r.returncode == 0, r.stdout + r.stderr)
            before = lc.read_text()
            r1 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            once = lc.read_text()
            r2 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            check("it applies once after them; a second run says so and changes nothing",
                  r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and lc.read_text() == once and P.MARKER in once,
                  r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), once.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
            check("nothing is removed or changed: the patch only adds lines", ops and all(o[0] == "insert" for o in ops), ops)
            bad = t / "bad.c"
            bad.write_text(before.replace('    SetConsoleCtrlHandler(on_control, FALSE);\n', '    SetConsoleCtrlHandler(on_control, FALSE); /* moved */\n'))
            r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
            check("a moved anchor refuses the patch and leaves the file alone",
                  r3.returncode != 0 and "end of the launch" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)

            helpers = once[once.index("struct view_dword {"):once.index("static volatile LONG interrupted;")]
            harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
typedef void *HKEY; typedef long LSTATUS; typedef unsigned int DWORD; typedef unsigned int REGSAM; typedef unsigned char BYTE; typedef int BOOL; typedef void *HANDLE;
#define ERROR_SUCCESS 0L
#define ERROR_FILE_NOT_FOUND 2L
#define REG_SZ 1
#define REG_DWORD 4
#define KEY_QUERY_VALUE 1
#define KEY_SET_VALUE 2
#define KEY_WOW64_32KEY 0x200
#define FALSE 0
#define INVALID_FILE_ATTRIBUTES 0xffffffffu
static HKEY HKEY_CURRENT_USER = (HKEY)1;
/* the toy registry: tree 0 is the plain key, tree 1 is Software\Wow6432Node; key 0 is ...\Valve\Steam\ActiveProcess, key 1 is ...\Valve\Steam */
struct val { int tree, key, used; wchar_t name[32]; DWORD type, size; unsigned char data[1100]; };
static struct val vals[64];
static int node, exists[2][2], alive_pid;
static int tree_of(REGSAM sam) { return (sam & KEY_WOW64_32KEY) && node ? 1 : 0; }
static int key_of(const wchar_t *p) { return !wcscmp(p, L"Software\\Valve\\Steam\\ActiveProcess") ? 0 : !wcscmp(p, L"Software\\Valve\\Steam") ? 1 : -1; }
static HKEY mk(int tree, int key) { return (HKEY)(uintptr_t)(0x100 + tree * 2 + key); }
static struct val *find(HKEY h, const wchar_t *name)
{
    int tree = (int)(((uintptr_t)h - 0x100) / 2), key = (int)(((uintptr_t)h - 0x100) % 2);
    for (int i = 0; i < 64; ++i) if (vals[i].used && vals[i].tree == tree && vals[i].key == key && !wcscmp(vals[i].name, name)) return &vals[i];
    return NULL;
}
static LSTATUS RegOpenKeyExW(HKEY root, const wchar_t *path, DWORD o, REGSAM sam, HKEY *out)
{
    (void)root; (void)o;
    int tree = tree_of(sam), key = key_of(path);
    if (key < 0 || !exists[tree][key]) { *out = NULL; return ERROR_FILE_NOT_FOUND; }
    *out = mk(tree, key); return ERROR_SUCCESS;
}
static LSTATUS RegCreateKeyExW(HKEY root, const wchar_t *path, DWORD r, wchar_t *cls, DWORD opt, REGSAM sam, void *sa, HKEY *out, DWORD *disp)
{
    (void)root; (void)r; (void)cls; (void)opt; (void)sa; (void)disp;
    int tree = tree_of(sam), key = key_of(path);
    exists[tree][key] = 1; *out = mk(tree, key); return ERROR_SUCCESS;
}
static LSTATUS RegQueryValueExW(HKEY h, const wchar_t *name, DWORD *res, DWORD *type, BYTE *data, DWORD *size)
{
    (void)res;
    struct val *v = find(h, name);
    if (!v) return ERROR_FILE_NOT_FOUND;
    *type = v->type; if (data && *size >= v->size) memcpy(data, v->data, v->size); *size = v->size; return ERROR_SUCCESS;
}
static LSTATUS RegSetValueExW(HKEY h, const wchar_t *name, DWORD r, DWORD type, const BYTE *data, DWORD size)
{
    (void)r;
    struct val *v = find(h, name);
    if (!v) { for (int i = 0; i < 64 && !v; ++i) if (!vals[i].used) v = &vals[i]; v->used = 1; v->tree = (int)(((uintptr_t)h - 0x100) / 2); v->key = (int)(((uintptr_t)h - 0x100) % 2); wcscpy(v->name, name); }
    v->type = type; v->size = size; memcpy(v->data, data, size); return ERROR_SUCCESS;
}
static LSTATUS RegDeleteValueW(HKEY h, const wchar_t *name) { struct val *v = find(h, name); if (v) v->used = 0; return ERROR_SUCCESS; }
static LSTATUS RegCloseKey(HKEY h) { (void)h; return ERROR_SUCCESS; }
static DWORD GetFileAttributesW(const wchar_t *p) { return wcsstr(p, L"gone") ? INVALID_FILE_ATTRIBUTES : 0x20; }
static DWORD GetEnvironmentVariableW(const wchar_t *n, wchar_t *b, DWORD s) { (void)n; (void)b; (void)s; return 0; }
static HANDLE OpenProcess(DWORD acc, BOOL inh, DWORD pid) { (void)acc; (void)inh; return (int)pid == alive_pid ? (HANDLE)0x77 : NULL; }
static BOOL GetExitCodeProcess(HANDLE h, DWORD *code) { (void)h; *code = 259; return 1; }
static BOOL CloseHandle(HANDLE h) { (void)h; return 1; }
''' + helpers + r'''
static void put_dword(int tree, int key, const wchar_t *n, DWORD v) { exists[tree][key] = 1; RegSetValueExW(mk(tree, key), n, 0, REG_DWORD, (const BYTE *)&v, 4); }
static void put_sz(int tree, int key, const wchar_t *n, const wchar_t *s) { exists[tree][key] = 1; RegSetValueExW(mk(tree, key), n, 0, REG_SZ, (const BYTE *)s, (DWORD)((wcslen(s) + 1) * 2)); }
static DWORD get_dword(int tree, int key, const wchar_t *n) { struct val *v = find(mk(tree, key), n); return v ? *(DWORD *)v->data : 0xdeadbeef; }
static void run(const char *label, int has_node, int stale)
{
    memset(vals, 0, sizeof vals); memset(exists, 0, sizeof exists); node = has_node; alive_pid = 42;
    /* the Dock's own publish into the plain key, and the seeded strings (BCD's prefix seeding) */
    put_dword(0, 0, L"pid", 42); put_dword(0, 0, L"ActiveUser", 7);
    put_sz(0, 0, L"SteamClientDll64", L"C:\\Steam\\steamclient64.dll"); put_sz(0, 0, L"SteamClientDll", L"C:\\Steam\\steamclient.dll");
    put_sz(0, 1, L"SteamPath", L"C:\\Steam"); put_sz(0, 1, L"SteamExe", L"C:\\Steam\\steam.exe");
    if (has_node && stale) {   /* a dead real client's tree: pid 260, ActiveUser 0, a path that is gone */
        put_dword(1, 0, L"pid", 260); put_dword(1, 0, L"ActiveUser", 0); put_sz(1, 0, L"SteamClientDll64", L"C:\\gone\\steamclient64.dll");
        put_sz(1, 1, L"SteamPath", L"c:/gone");
    }
    HKEY active = mk(0, 0);
    struct view_mirror m = {0};
    LSTATUS err = 0;
    int pre = dock_steamapi_probe(42);
    int bits = view_mirror_publish(&m, active, 42, 7, &err);
    int post = dock_steamapi_probe(42);
    int again = view_mirror_publish(&m, active, 42, 7, &err);
    int view = has_node ? 1 : 0;   /* the tree a 32-bit reader lands in */
    unsigned p32 = get_dword(view, 0, L"pid"), u32 = get_dword(view, 0, L"ActiveUser");
    int created32 = exists[1][0] || exists[1][1];
    view_mirror_restore(&m);
    printf("%s|pre=%d bits=%d post=%d again=%d|p32=%u u32=%u made32=%d|after pid32=%u user32=%u plain=%u\n", label, pre, bits, post, again, p32, u32, created32,
           get_dword(view, 0, L"pid"), get_dword(view, 0, L"ActiveUser"), get_dword(0, 0, L"pid"));
}
int main(void)
{
    run("A-nonode", 0, 0);
    run("B-node", 1, 0);
    run("C-stale", 1, 1);
    return 0;
}
'''
            cc = shutil.which("cc") or shutil.which("clang")
            if cc:
                (t / "h.c").write_text(harness)
                b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-Wno-unused-function", "-Wno-unused-parameter",
                                    str(t / "h.c"), "-o", str(t / "h")], capture_output=True, text=True)
                check("the mirror and the probe build on the host over a toy registry (C11, warnings are errors)", b.returncode == 0, b.stderr[-1500:])
                if b.returncode == 0:
                    out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()
                    check("no Wow6432Node: probe 63 before and after, the 32-bit view is the plain key (pid 42 there), no second tree is made, the values stay",
                          out[0].startswith("A-nonode|pre=63 bits=29 post=63 again=29|p32=42 u32=7 made32=0|after pid32=42 user32=7 plain=42"), out)
                    check("Wow6432Node without a Valve tree (state C): probe 0 before, 63 after, the 32-bit tree gets pid 42 and ActiveUser 7 and the restore takes them out",
                          out[1].startswith("B-node|pre=0 bits=31 post=63 again=29|p32=42 u32=7 made32=1|after pid32=3735928559 user32=3735928559 plain=42"), out)
                    check("a stale 32-bit tree (a dead client's pid): probe 7 before, 63 after, the stale values come back at the end",
                          out[2].startswith("C-stale|pre=7 bits=31 post=63 again=29|p32=42 u32=7 made32=1|after pid32=260 user32=0 plain=42"), out)
            else:
                print("skip the host build: no C compiler")

            mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                      Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                          if (m / "x86_64-w64-mingw32-clang").exists()), None)
            if mingw:
                for patch, target in (("tools/patch-dock-licence-wait.py", "session.c"), ("tools/patch-dock-licence-ask.py", "session.c"),
                                      ("tools/patch-dock-ceg-service-start.py", "scm.c"), ("tools/patch-dock-ceg-retry.py", "launch.c"),
                                      ("tools/patch-dock-ceg-already-prepared.py", None)):
                    files = [str(t / "src" / target)] if target else [str(t / "src/launch.c"), str(t / "src/validation.c")]
                    r = subprocess.run([sys.executable, str(root / patch)] + files, capture_output=True, text=True)
                    check("the workflow's later patch %s applies to the copy too" % patch, r.returncode == 0, r.stdout + r.stderr)
                b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                    "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"],
                                   capture_output=True, text=True)
                check("the patched host compiles with build/madeira-dock/build.sh's flags, with every Dock patch of the workflow",
                      b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
            else:
                print("skip the Dock compile: no llvm-mingw at hand")

print("PASS: Dock steam views" if ok else "FAILED: Dock steam views")
sys.exit(0 if ok else 1)
