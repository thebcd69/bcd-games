#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-registry-create.py: the Steam client entries step creates a key that is missing instead of ending the start with
host result 42 (night round 2026-11-11; ZB8E97, RAC3KD, GNWWRT, GWX5H2, DP9TVR: launch-registry-step=2, launch-registry-error=2 after the
1 s retry, every game of those prefixes).

Checks:
  - CI runs this check and applies the patch in the Dock build step right after tools/patch-dock-registry-events.py and before build.sh,
    without failing the step; the app keeps the three new report fields; the catalog lists MADEIRA_DOCK_REGISTRY_CREATE (default on);
  - Dock source (madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; skipped without it): on a copy of src/launch.c after every
    earlier launch.c patch of the workflow, the patch applies once and changes only the failure exit of the registry step; a second run
    changes nothing; a moved anchor and a missing earlier patch refuse it and leave the file alone;
  - the patched step, cut from the source and built on the host with stand-ins for the registry: both keys there: nothing reported (the old
    path); the machine key missing: it is created after the retry and the three values are published; the ActiveProcess key missing: both
    are created; a create that fails: its error, the probe and the old step/error events, and the start ends in 42; MADEIRA_DOCK_REGISTRY_CREATE=0:
    no create, the probe and the old events; an error other than "not found": no create;
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


spec = importlib.util.spec_from_file_location("registrycreate", root / "tools/patch-dock-registry-create.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the registry events patch, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-registry-create.py\n" in step
      and 'python3 tools/patch-dock-registry-create.py madeira-dock/src/launch.c || echo "::warning::' in step
      and step.index("patch-dock-registry-events.py") < step.index("patch-dock-registry-create.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the three new report fields",
      all('"%s"' % k in allowed for k in ("launch-registry-created", "launch-registry-create-error", "launch-registry-probe")))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_REGISTRY_CREATE, default on, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_REGISTRY_CREATE": \{[^}]*"default": "1"[^}]*"sources": \["tools/patch-dock-registry-create\.py"\]', cat, re.S) is not None)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/launch.c").read_text()
    if P.MARKER in orig:
        print("note: the Dock tree is already patched; the original cannot be rebuilt from it, skipping the source checks")
        orig = None
    if orig is not None:
        with tempfile.TemporaryDirectory(prefix="madeira-dock-registry-create-") as td:
            t = Path(td)
            shutil.copytree(dock / "src", t / "src")
            lc = t / "src/launch.c"
            lc.write_text(orig)
            if "madeira-bcd: registry-events-v1" in orig:
                print("note: the Dock tree is already patched by the earlier launch.c patches; they are not applied again")
            else:
                for patch in ("tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py",
                              "tools/patch-dock-install-dir-events.py"):
                    r = subprocess.run([sys.executable, str(root / patch), str(lc)], capture_output=True, text=True)
                    check("the workflow's earlier patch %s applies to the copy" % patch, r.returncode == 0, r.stdout + r.stderr)
            script = str(root / "tools/patch-dock-registry-create.py")
            skipped = lc.read_text()
            r0 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            check("without the registry events patch it refuses and leaves the file alone",
                  "madeira-bcd: registry-events-v1" in skipped or (r0.returncode != 0 and "registry-events" in r0.stderr and lc.read_text() == skipped),
                  r0.stdout + r0.stderr)
            lc.write_text(skipped)   # in a build the tree already has the registry events patch, so r0 applied this one: start again from before it
            if "madeira-bcd: registry-events-v1" not in lc.read_text():
                r = subprocess.run([sys.executable, str(root / "tools/patch-dock-registry-events.py"), str(lc)], capture_output=True, text=True)
                check("the registry events patch applies to the copy", r.returncode == 0, r.stdout + r.stderr)
            before = lc.read_text()
            r1 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            once = lc.read_text()
            r2 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            check("it applies once after them; a second run says so and changes nothing",
                  r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and lc.read_text() == once and P.MARKER in once,
                  r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), once.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
            check("nothing else changes: only lines are added around the registry step's failure exit (no line is removed or replaced)",
                  len(ops) == 2 and all(o[0] == "insert" for o in ops) and [o[4] - o[3] for o in ops] == [22, 18], ops)
            bad = t / "bad.c"
            bad.write_text(before.replace(P.OLD_FAIL, P.OLD_FAIL.replace("goto done;", "goto done; /* moved */")))
            r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
            check("a moved anchor refuses the patch and leaves the file alone",
                  r3.returncode != 0 and "failure exit" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)

            cut = once[once.index("    int registry_step = 0;\n"):]
            cut = cut[:cut.index("    o->event(\"launch-client-discovery-published\", 1);\n")]
            harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
typedef void *HKEY; typedef long LSTATUS; typedef unsigned int DWORD; typedef unsigned int REGSAM;
#define ERROR_SUCCESS 0L
#define ERROR_FILE_NOT_FOUND 2L
#define ERROR_PATH_NOT_FOUND 3L
#define ERROR_ACCESS_DENIED 5L
#define KEY_QUERY_VALUE 1
#define KEY_SET_VALUE 2
#define REG_CREATED_NEW_KEY 1
#define REG_OPENED_EXISTING_KEY 2
#define KEY_WOW64_64KEY 0x100
#define KEY_WOW64_32KEY 0x200
static HKEY HKEY_CURRENT_USER = (HKEY)1, HKEY_LOCAL_MACHINE = (HKEY)2;
struct saved_value { HKEY key; const wchar_t *name; };
struct obs { void (*event)(const char *, int32_t); void (*sleep_ms)(uint32_t); };
static char log_[768];
static void ev(const char *n, int32_t v) { char b[96]; snprintf(b, sizeof b, "%s=%d ", n, v); strncat(log_, b, sizeof log_ - strlen(log_) - 1); }
static void sl(uint32_t ms) { char b[32]; snprintf(b, sizeof b, "sleep=%u ", ms); strncat(log_, b, sizeof log_ - strlen(log_) - 1); }
/* the registry: which keys exist, the error an open of a missing key answers, whether a create works, the probes' keys */
static int user_there, machine_there, create_fails, creates, probe_seen[5]; static LSTATUS missing_error; static const wchar_t *flag;
static LSTATUS RegOpenKeyExW(HKEY root, const wchar_t *path, DWORD o, REGSAM sam, HKEY *out)
{
    (void)o;
    *out = (HKEY)0xbad;
    if (root == HKEY_CURRENT_USER && !wcscmp(path, L"Software\\Valve\\Steam\\ActiveProcess")) {
        if (user_there) { *out = (HKEY)0x10; return ERROR_SUCCESS; }
        return missing_error;
    }
    if (root == HKEY_LOCAL_MACHINE && !wcscmp(path, L"Software\\Valve\\Steam") && (sam & KEY_WOW64_32KEY)) {
        if (machine_there) { *out = (HKEY)0x20; return ERROR_SUCCESS; }
        return missing_error;
    }
    /* the probes (no view flag or the 64-bit view) */
    if (root == HKEY_LOCAL_MACHINE && !wcscmp(path, L"Software\\Valve\\Steam") && !(sam & 0x300)) { if (probe_seen[0]) { *out = (HKEY)0x30; return ERROR_SUCCESS; } return ERROR_FILE_NOT_FOUND; }
    if (root == HKEY_LOCAL_MACHINE && !wcscmp(path, L"Software\\Wow6432Node\\Valve\\Steam") && (sam & KEY_WOW64_64KEY)) { if (probe_seen[1]) { *out = (HKEY)0x30; return ERROR_SUCCESS; } return ERROR_FILE_NOT_FOUND; }
    if (root == HKEY_LOCAL_MACHINE && !wcscmp(path, L"Software") && !(sam & 0x300)) { if (probe_seen[2]) { *out = (HKEY)0x30; return ERROR_SUCCESS; } return ERROR_FILE_NOT_FOUND; }
    if (root == HKEY_LOCAL_MACHINE && !wcscmp(path, L"Software\\Wow6432Node") && (sam & KEY_WOW64_64KEY)) { if (probe_seen[3]) { *out = (HKEY)0x30; return ERROR_SUCCESS; } return ERROR_FILE_NOT_FOUND; }
    if (root == HKEY_CURRENT_USER && !wcscmp(path, L"Software\\Valve\\Steam\\ActiveProcess")) { if (probe_seen[4]) { *out = (HKEY)0x30; return ERROR_SUCCESS; } return ERROR_FILE_NOT_FOUND; }
    return ERROR_FILE_NOT_FOUND;
}
static LSTATUS RegCreateKeyExW(HKEY root, const wchar_t *path, DWORD r, wchar_t *cls, DWORD opt, REGSAM sam, void *sa, HKEY *out, DWORD *disp)
{
    (void)r; (void)cls; (void)opt; (void)sa;
    ++creates;
    if (create_fails) { *out = (HKEY)0xbad; return ERROR_ACCESS_DENIED; }
    if (root == HKEY_CURRENT_USER && !wcscmp(path, L"Software\\Valve\\Steam\\ActiveProcess") && !(sam & KEY_WOW64_32KEY)) {
        *disp = user_there ? REG_OPENED_EXISTING_KEY : REG_CREATED_NEW_KEY; user_there = 1; *out = (HKEY)0x10; return ERROR_SUCCESS;
    }
    if (root == HKEY_LOCAL_MACHINE && !wcscmp(path, L"Software\\Valve\\Steam") && (sam & KEY_WOW64_32KEY)) {
        *disp = machine_there ? REG_OPENED_EXISTING_KEY : REG_CREATED_NEW_KEY; machine_there = 1; *out = (HKEY)0x20; return ERROR_SUCCESS;
    }
    *out = (HKEY)0xbad;
    return ERROR_FILE_NOT_FOUND;
}
static LSTATUS RegCloseKey(HKEY key) { (void)key; return ERROR_SUCCESS; }
static bool wide_flag(const wchar_t *name, wchar_t expected) { (void)name; return flag && wcslen(flag) == 1 && flag[0] == expected && !wcscmp(name, L"MADEIRA_DOCK_REGISTRY_CREATE"); }
static int published;
static bool publish(struct saved_value *v, DWORD value) { (void)v; (void)value; ++published; return true; }
static DWORD GetCurrentProcessId(void) { return 42; }
static int run(int has_user, int has_machine, LSTATUS miss, int cfails, const wchar_t *f, int p0, int p1, int p2, int p3, int p4)
{
    struct obs obs = { ev, sl }, *o = &obs;
    HKEY active = NULL, machine = NULL;
    struct saved_value values[3] = {{0}};
    uint64_t steamid = 7;
    user_there = has_user; machine_there = has_machine; missing_error = miss; create_fails = cfails; flag = f; creates = 0; published = 0; log_[0] = 0;
    probe_seen[0] = p0; probe_seen[1] = p1; probe_seen[2] = p2; probe_seen[3] = p3; probe_seen[4] = p4;
''' + cut + r'''
    (void)values; (void)active; (void)machine;
    printf("ok|%s|u%d m%d c%d p%d\n", log_, user_there, machine_there, creates, published);
    return 0;
done:
    printf("42|%s|u%d m%d c%d p%d\n", log_, user_there, machine_there, creates, published);
    return 42;
}
int main(void)
{
    run(1, 1, ERROR_FILE_NOT_FOUND, 0, NULL, 0, 0, 0, 0, 0);       /* both keys there */
    run(1, 0, ERROR_FILE_NOT_FOUND, 0, NULL, 0, 0, 0, 0, 0);       /* the machine key missing: created */
    run(0, 0, ERROR_FILE_NOT_FOUND, 0, NULL, 0, 0, 0, 0, 0);       /* both missing: both created */
    run(0, 1, ERROR_PATH_NOT_FOUND, 0, NULL, 0, 0, 0, 0, 0);       /* the ActiveProcess key missing, path error */
    run(1, 0, ERROR_FILE_NOT_FOUND, 1, NULL, 0, 1, 1, 1, 0);      /* a create that fails: the probe, the old events */
    run(1, 0, ERROR_FILE_NOT_FOUND, 0, L"0", 1, 0, 1, 0, 1);      /* MADEIRA_DOCK_REGISTRY_CREATE=0 */
    run(1, 0, ERROR_ACCESS_DENIED, 0, NULL, 0, 0, 0, 0, 0);        /* another error: no create */
    run(0, 0, ERROR_FILE_NOT_FOUND, 0, L"1", 0, 0, 0, 0, 0);       /* the flag set to 1 is not 0: created */
    return 0;
}
'''
            cc = shutil.which("cc") or shutil.which("clang")
            if cc:
                (t / "h.c").write_text(harness)
                b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-Wno-unused-function",
                                    "-Wno-unused-label", "-Wno-unused-parameter", str(t / "h.c"), "-o", str(t / "h")], capture_output=True, text=True)
                check("the patched step builds on the host (C11, warnings are errors)", b.returncode == 0, b.stderr[-1500:])
                if b.returncode == 0:
                    out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()
                    check("both keys there: nothing reported, nothing created, three values", out[0] == "ok||u1 m1 c0 p3", out)
                    check("the machine key missing: one retry, then it is created and the values go out (step 2 never reported)",
                          out[1] == "ok|launch-registry-retry=2 sleep=1000 launch-registry-created=2 |u1 m1 c1 p3", out)
                    check("both keys missing: the ActiveProcess key first, then the machine key, one retry in between",
                          out[2] == "ok|launch-registry-retry=1 sleep=1000 launch-registry-created=1 launch-registry-created=2 |u1 m1 c2 p3", out)
                    check("a missing ActiveProcess key (path error) is created too; the machine key that exists is opened by its create and not reported",
                          out[3] == "ok|launch-registry-retry=1 sleep=1000 launch-registry-created=1 |u1 m1 c2 p3", out)
                    check("a create that fails: its error, the probe (bits 1, 2, 3 seen, and 4: the ActiveProcess key opened = 30), the old step and error; the start ends in 42",
                          out[4] == "42|launch-registry-retry=2 sleep=1000 launch-registry-create-error=5 launch-registry-probe=30 "
                                    "launch-registry-step=2 launch-registry-error=2 |u1 m0 c1 p0", out)
                    check("MADEIRA_DOCK_REGISTRY_CREATE=0: nothing created, the probe (bits 0, 2, 4 = 21) and the old events",
                          out[5] == "42|launch-registry-retry=2 sleep=1000 launch-registry-probe=21 launch-registry-step=2 launch-registry-error=2 |u1 m0 c0 p0", out)
                    check("an error other than not found is not repaired: no create (the probe sees only the ActiveProcess key: 16)",
                          out[6] == "42|launch-registry-retry=2 sleep=1000 launch-registry-probe=16 launch-registry-step=2 launch-registry-error=5 |u1 m0 c0 p0", out)
                    check("the flag at 1 is not off: created", out[7].startswith("ok|launch-registry-retry=1 sleep=1000 launch-registry-created=1 launch-registry-created=2 "), out)
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

print("PASS: Dock registry create" if ok else "FAILED: Dock registry create")
sys.exit(0 if ok else 1)
