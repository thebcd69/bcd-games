#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-ceg-zero-jobs.py: an accepted prepare request with zero jobs may go on to Valve's launch, keyed (round 630, agent A1 S14).

FRKBN1 (Borderlands: The Pre-Sequel, build 626): Valve's client accepted the request to prepare the game's custom executables
(ceg-request-result=1) and started no job (ceg-request=0); the Dock ended with host result 49 (ceg-result=0) and the game never launched.

Checks:
  - CI runs this check and applies the patch in the Dock build step, after the other launch.c patches and before build.sh, without
    failing the step; the catalog lists env.MADEIRA_DOCK_CEG_ZERO_JOBS (default off); the app keeps the report field
    ceg-zero-jobs-launch and words ceg-result 0 as "started no job";
  - Dock source (madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; skipped without it): on a copy of src/launch.c, after the
    workflow's earlier launch.c patches, the patch applies once and only extends the request's failure branch; a second run changes
    nothing; a moved anchor refuses it and leaves the file alone;
  - the patched branch, cut from the source and built on the host with the Dock's own validation.c (sh_ceg_request_step): with the key
    exactly 1 an accepted request with zero jobs returns 0 with ceg-zero-jobs-launch=1 and ceg-result=1; without the key, with 0, 11 or
    an empty value it fails as before (49, ceg-result=0); a refusal (2), too many jobs and the unknown count fail as before even with the
    key; a request with jobs still waits;
  - the patched host compiles with build/madeira-dock/build.sh's flags together with every launch.c and session.c patch of the
    workflow, where llvm-mingw is at hand.
"""
from pathlib import Path
import importlib.util, re, shutil, subprocess, sys, tempfile, difflib

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("cegzero", root / "tools/patch-dock-ceg-zero-jobs.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the other launch.c patches, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-ceg-zero-jobs.py\n" in step
      and 'python3 tools/patch-dock-ceg-zero-jobs.py madeira-dock/src/launch.c || echo "::warning::' in step
      and step.index("patch-dock-steam-readback.py") < step.index("patch-dock-ceg-zero-jobs.py") < step.index("bash build/madeira-dock/build.sh"))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_CEG_ZERO_JOBS, default off, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_CEG_ZERO_JOBS": \{[^}]*"default": "0"[^}]*"sources": \["tools/patch-dock-ceg-zero-jobs\.py"\]', cat, re.S) is not None)
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the report field ceg-zero-jobs-launch and words ceg-result 0 as a request that started no job",
      '"ceg-zero-jobs-launch"' in allowed and 'case 0: return "Steam started no job to prepare this game\'s executable' in dk)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/launch.c").read_text()
    if P.MARKER in orig:
        print("note: the Dock tree is already patched; the original is rebuilt from it")
        orig = orig.replace(P.NEW, P.OLD, 1)
    with tempfile.TemporaryDirectory(prefix="madeira-dock-ceg-zero-") as td:
        t = Path(td)
        shutil.copytree(dock / "src", t / "src")
        lc = t / "src/launch.c"
        lc.write_text(orig)
        earlier = ("tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py")
        for patch in earlier:
            r = subprocess.run([sys.executable, str(root / patch), str(lc)], capture_output=True, text=True)
            check("the workflow's earlier patch %s applies to the copy" % patch, r.returncode == 0, r.stdout + r.stderr)
        before = lc.read_text()
        script = str(root / "tools/patch-dock-ceg-zero-jobs.py")
        r1 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
        once = lc.read_text()
        r2 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
        check("it applies once after them; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and lc.read_text() == once and P.MARKER in once,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), once.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
        added = once.splitlines()[ops[0][3]:ops[0][4]] if len(ops) == 1 else []
        check("nothing else changes: one insertion into the request's failure branch, before its ceg-result event",
              len(ops) == 1 and ops[0][0] == "insert" and any("ceg-zero-jobs-launch" in l for l in added)
              and once.index("if (step != SH_CEG_WAIT) {") < once.index("ceg-zero-jobs-launch") < once.index('o->event("ceg-result", progress.failure);\n                    return 49;'),
              ops)
        bad = t / "bad.c"
        bad.write_text(before.replace('o->event("ceg-request", jobs == UINT32_MAX ? -1 : (int32_t)jobs);',
                                      'o->event("ceg-request", (int32_t)jobs);'))
        r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves the file alone",
              r3.returncode != 0 and "failure branch" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)

        # the patched branch, cut from the source, with the Dock's own validation.c
        branch = once[once.index('                o->event("ceg-request-result", status);\n'):]
        branch = branch[:branch.index("                waiting = true;\n")]
        harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include "validation.h"
struct obs { void (*event)(const char *, int32_t); };
static char events[512];
static void ev(const char *name, int32_t v) { char b[96]; snprintf(b, sizeof b, "%s=%d ", name, v); strncat(events, b, sizeof events - strlen(events) - 1); }
static const wchar_t *flag_value;   /* NULL: the variable is not set */
static bool wide_flag(const wchar_t *name, wchar_t expected)
{
    (void)name;
    return flag_value && wcslen(flag_value) == 1 && flag_value[0] == expected;   /* GetEnvironmentVariableW(...) == 1 && value[0] == expected */
}
static int request(int32_t status, uint32_t jobs, const wchar_t *flag)
{
    struct obs obs = { ev }, *o = &obs;
    struct sh_ceg_progress progress = {0};
    bool waiting = false;
    flag_value = flag; events[0] = 0;
    enum sh_ceg_step step = sh_ceg_request_step(status, jobs, &progress);
''' + branch + r'''
    waiting = true;
    return waiting ? -1 : -2;
}
int main(void)
{
    struct { int32_t status; uint32_t jobs; const wchar_t *flag; } cases[] = {
        {1, 0, L"1"}, {1, 0, NULL}, {1, 0, L"0"}, {1, 0, L"11"}, {1, 0, L""}, {2, 0, L"1"}, {1, 99999, L"1"}, {1, UINT32_MAX, L"1"},
        {1, 2, L"1"}, {1, 2, NULL},
    };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        int r = request(cases[i].status, cases[i].jobs, cases[i].flag);
        printf("%d|%s\n", r, events);
    }
    return 0;
}
'''
        cc = shutil.which("cc") or shutil.which("clang")
        if cc:
            (t / "h.c").write_text(harness)
            b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-Wno-unused-but-set-variable",
                                "-I", str(t / "src"), str(t / "h.c"), str(t / "src/validation.c"), "-o", str(t / "h")],
                               capture_output=True, text=True)
            check("the patched branch builds on the host with the Dock's validation.c (C11, warnings are errors)", b.returncode == 0, b.stderr[-1500:])
            if b.returncode == 0:
                out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()
                launch = "0|ceg-request-result=1 ceg-request=0 ceg-zero-jobs-launch=1 ceg-result=1 "
                fail0 = "49|ceg-request-result=1 ceg-request=0 ceg-result=0 "
                check("key 1: an accepted request with zero jobs goes on to the launch (0, ceg-zero-jobs-launch=1, ceg-result=1)",
                      len(out) == 10 and out[0] == launch, out)
                check("no key, 0, 11 or an empty value: it fails as before (49, ceg-result=0)", out[1:5] == [fail0] * 4, out)
                check("a refusal, too many jobs and an unknown count fail as before even with the key",
                      out[5] == "49|ceg-request-result=2 ceg-request=0 ceg-result=2 "
                      and out[6].startswith("49|ceg-request-result=1 ceg-request=99999 ") and "ceg-zero-jobs-launch" not in out[6]
                      and out[7] == "49|ceg-request-result=1 ceg-request=-1 ceg-result=0 ", out)
                check("a request with jobs still waits for them, with or without the key",
                      out[8] == "-1|ceg-request-result=1 ceg-request=2 " and out[9] == out[8], out)
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

print("PASS: Dock CEG zero jobs (keyed)" if ok else "FAILED: Dock CEG zero jobs")
sys.exit(0 if ok else 1)
