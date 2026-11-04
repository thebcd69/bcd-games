#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-registry-events.py: the Steam client entries step (host result 42) says which part failed; a failed open is tried
once more after a second (round 630, agent A1 S11).

BN3SXW (PlateUp!, build 625) and 7 more archive logs ended with host result 42 and no event; BN3SXW's next start worked.

Checks:
  - CI runs this check and applies the patch in the Dock build step after the other launch.c patches and before build.sh, without
    failing the step; the app keeps the three report fields; the catalog lists MADEIRA_DOCK_REGISTRY_RETRY (default on);
  - Dock source (madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; skipped without it): on a copy of src/launch.c after every
    earlier launch.c patch of the workflow, the patch applies once and changes only the open and publish lines; a second run changes
    nothing; a moved anchor refuses it and leaves the file alone;
  - the patched opens, cut from the source and built on the host with stand-ins: both opens fine: nothing reported, no wait; the first
    open fails once: one retry after 1 s, then it goes on; it fails twice: step 1 and its error, after one retry; the second open fails
    once: only that open is tried again (the first key is not opened twice); MADEIRA_DOCK_REGISTRY_RETRY=0: no retry; a failed value
    reports its step (3, 4 or 5) and nothing after it is published;
  - the patched host compiles with build/madeira-dock/build.sh's flags together with every Dock patch of the workflow, where llvm-mingw
    is at hand.
"""
from pathlib import Path
import difflib, importlib.util, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("registryevents", root / "tools/patch-dock-registry-events.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the other launch.c patches, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-registry-events.py\n" in step
      and 'python3 tools/patch-dock-registry-events.py madeira-dock/src/launch.c || echo "::warning::' in step
      and step.index("patch-dock-install-dir-events.py") < step.index("patch-dock-registry-events.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the three report fields",
      all('"%s"' % k in allowed for k in ("launch-registry-step", "launch-registry-error", "launch-registry-retry")))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_REGISTRY_RETRY, default on, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_REGISTRY_RETRY": \{[^}]*"default": "1"[^}]*"sources": \["tools/patch-dock-registry-events\.py"\]', cat, re.S) is not None)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/launch.c").read_text()
    if P.MARKER in orig:
        print("note: the Dock tree is already patched; the original is rebuilt from it")
        orig = orig.replace(P.NEW_OPEN, P.OLD_OPEN, 1).replace(P.NEW_PUBLISH, P.OLD_PUBLISH, 1)
    with tempfile.TemporaryDirectory(prefix="madeira-dock-registry-") as td:
        t = Path(td)
        shutil.copytree(dock / "src", t / "src")
        lc = t / "src/launch.c"
        lc.write_text(orig)
        for patch in ("tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py",
                      "tools/patch-dock-ceg-zero-jobs.py", "tools/patch-dock-install-dir-events.py"):
            r = subprocess.run([sys.executable, str(root / patch), str(lc)], capture_output=True, text=True)
            check("the workflow's earlier patch %s applies to the copy" % patch, r.returncode == 0, r.stdout + r.stderr)
        before = lc.read_text()
        script = str(root / "tools/patch-dock-registry-events.py")
        r1 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
        once = lc.read_text()
        r2 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
        check("it applies once after them; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and lc.read_text() == once and P.MARKER in once,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), once.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
        check("nothing else changes: the open lines and the publish lines replaced",
              len(ops) == 2 and all(o[0] == "replace" for o in ops) and [o[2] - o[1] for o in ops] == [4, 2], ops)
        bad = t / "bad.c"
        bad.write_text(before.replace(P.OLD_PUBLISH, P.OLD_PUBLISH.replace("(DWORD)steamid", "(DWORD)(steamid & 0xffffffff)")))
        r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves the file alone",
              r3.returncode != 0 and "published values" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)

        cut = once[once.index("    int registry_step = 0;\n"):]
        cut = cut[:cut.index("    o->event(\"launch-client-discovery-published\", 1);\n")]
        harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
typedef void *HKEY; typedef long LSTATUS; typedef unsigned int DWORD;
#define ERROR_SUCCESS 0L
#define ERROR_FILE_NOT_FOUND 2L
#define ERROR_ACCESS_DENIED 5L
#define KEY_QUERY_VALUE 1
#define KEY_SET_VALUE 2
#define KEY_WOW64_32KEY 0x200
static HKEY HKEY_CURRENT_USER = (HKEY)1, HKEY_LOCAL_MACHINE = (HKEY)2;
struct saved_value { HKEY key; const wchar_t *name; };
struct obs { void (*event)(const char *, int32_t); void (*sleep_ms)(uint32_t); };
static char log_[512];
static void ev(const char *n, int32_t v) { char b[96]; snprintf(b, sizeof b, "%s=%d ", n, v); strncat(log_, b, sizeof log_ - strlen(log_) - 1); }
static void sl(uint32_t ms) { char b[32]; snprintf(b, sizeof b, "sleep=%u ", ms); strncat(log_, b, sizeof log_ - strlen(log_) - 1); }
static int fails_user, fails_machine, opens_user, opens_machine, fail_value; static const wchar_t *flag;
static LSTATUS RegOpenKeyExW(HKEY root, const wchar_t *path, DWORD o, DWORD sam, HKEY *out)
{
    (void)path; (void)o; (void)sam;
    if (root == HKEY_CURRENT_USER) { opens_user++; if (fails_user-- > 0) { *out = (HKEY)0xbad; return ERROR_FILE_NOT_FOUND; } *out = (HKEY)0x10; }
    else { opens_machine++; if (fails_machine-- > 0) { *out = (HKEY)0xbad; return ERROR_ACCESS_DENIED; } *out = (HKEY)0x20; }
    return ERROR_SUCCESS;
}
static bool wide_flag(const wchar_t *name, wchar_t expected) { (void)name; return flag && wcslen(flag) == 1 && flag[0] == expected; }
static int published;
static bool publish(struct saved_value *v, DWORD value) { (void)v; (void)value; if (++published == fail_value) return false; return true; }
static DWORD GetCurrentProcessId(void) { return 42; }
static int run(int fu, int fm, int fv, const wchar_t *f)
{
    struct obs obs = { ev, sl }, *o = &obs;
    HKEY active = NULL, machine = NULL;
    struct saved_value values[3] = {{0}};
    uint64_t steamid = 7;
    fails_user = fu; fails_machine = fm; fail_value = fv; flag = f; opens_user = opens_machine = 0; published = 0; log_[0] = 0;
''' + cut + r'''
    (void)values; (void)active; (void)machine;
    printf("ok|%s|u%d m%d p%d\n", log_, opens_user, opens_machine, published);
    return 0;
done:
    printf("42|%s|u%d m%d p%d\n", log_, opens_user, opens_machine, published);
    return 42;
}
int main(void)
{
    run(0, 0, 0, NULL); run(1, 0, 0, NULL); run(2, 0, 0, NULL); run(0, 1, 0, NULL); run(1, 0, 0, L"0"); run(0, 0, 2, NULL); run(0, 0, 1, NULL);
    run(0, 0, 3, NULL);
    return 0;
}
'''
        cc = shutil.which("cc") or shutil.which("clang")
        if cc:
            (t / "h.c").write_text(harness)
            b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-Wno-unused-function",
                                "-Wno-unused-label", str(t / "h.c"), "-o", str(t / "h")], capture_output=True, text=True)
            check("the patched opens build on the host (C11, warnings are errors)", b.returncode == 0, b.stderr[-1500:])
            if b.returncode == 0:
                out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()
                check("both opens fine: nothing reported, no wait, three values", out[0] == "ok||u1 m1 p3", out)
                check("the first open fails once: one retry after 1 s, then on", out[1] == "ok|launch-registry-retry=1 sleep=1000 |u2 m1 p3", out)
                check("it fails twice: step 1 and its error after one retry, nothing published",
                      out[2] == "42|launch-registry-retry=1 sleep=1000 launch-registry-step=1 launch-registry-error=2 |u2 m0 p0", out)
                check("the second open fails once: only that open again (the first key is not opened twice)",
                      out[3] == "ok|launch-registry-retry=2 sleep=1000 |u1 m2 p3", out)
                check("MADEIRA_DOCK_REGISTRY_RETRY=0: no retry", out[4] == "42|launch-registry-step=1 launch-registry-error=2 |u1 m0 p0", out)
                check("a failed value reports its step and nothing after it is published",
                      out[5] == "42|launch-registry-step=4 |u1 m1 p2" and out[6] == "42|launch-registry-step=3 |u1 m1 p1"
                      and out[7] == "42|launch-registry-step=5 |u1 m1 p3", out)
        else:
            print("skip the host build: no C compiler")

        mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                  Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                      if (m / "x86_64-w64-mingw32-clang").exists()), None)
        if mingw:
            r = subprocess.run([sys.executable, str(root / "tools/patch-dock-licence-wait.py"), str(t / "src/session.c")], capture_output=True, text=True)
            check("the workflow's session.c patch applies to the copy too", r.returncode == 0, r.stdout + r.stderr)
            b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"],
                               capture_output=True, text=True)
            check("the patched host compiles with build/madeira-dock/build.sh's flags, with every Dock patch of the workflow",
                  b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
        else:
            print("skip the Dock compile: no llvm-mingw at hand")

print("PASS: Dock registry events" if ok else "FAILED: Dock registry events")
sys.exit(0 if ok else 1)
