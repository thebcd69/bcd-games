#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-licence-wait.py: a late Steam sign-in still gets time for the licence list (round 630, agent U S3).

444TS8: the sign-in took 64 s of the Dock's 90 s, the licence list had 26 s and the start ended with code 34.

Checks:
  - CI runs this check and applies the patch before build/madeira-dock/build.sh, without failing the step;
  - Dock source (madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; skipped without it): on a copy of src/session.c the patch
    replaces the loop's 90 s condition by a deadline that starts at 90 s, moves it only at the first sign-in, only later, to 45 s after
    it and never past 180 s from the logon start, with one report field; the tick bound covers 180 s at 20 ms a tick; the switch
    MADEIRA_DOCK_LICENCE_WAIT=0 keeps the 90 s; a second run changes nothing; a moved anchor refuses it and leaves the file alone;
  - the patched host compiles with build/madeira-dock/build.sh's flags (warnings are errors) together with the launch.c patches of the
    workflow, where llvm-mingw is at hand;
  - the deadline's arithmetic, taken from the patched source and compiled on the host: a sign-in at 1 s or 45 s keeps 90 s, 64 s
    (444TS8) gets 109 s, 150 s gets the 180 s cap, a second sign-in moves nothing, the switch 0 keeps 90 s;
  - the catalog lists the switch, default on.
"""
from pathlib import Path
import importlib.util, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("licencewait", root / "tools/patch-dock-licence-wait.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch before the Dock build, without failing the step",
      'python3 tests/host/check-dock-licence-wait.py\n' in step
      and 'python3 tools/patch-dock-licence-wait.py madeira-dock/src/session.c || echo "::warning::' in step
      and step.index("patch-dock-licence-wait.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the report field session-licence-wait-ms (fields outside reportAllowed never reach the log)",
      '"session-licence-wait-ms"' in allowed)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_LICENCE_WAIT, default on, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_LICENCE_WAIT": \{[^}]*"default": "1"[^}]*"sources": \["tools/patch-dock-licence-wait\.py"\]', cat, re.S) is not None)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/session.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/session.c").read_text()
    if P.MARKER in orig:
        print("note: the Dock tree is already patched; the original is rebuilt from it")
        orig = orig.replace(P.LOOP_NEW, P.LOOP_OLD, 1).replace(P.ONLINE_NEW, P.ONLINE_OLD, 1)
    with tempfile.TemporaryDirectory(prefix="madeira-dock-licence-") as td:
        t = Path(td)
        shutil.copytree(dock / "src", t / "src")
        sc = t / "src/session.c"
        sc.write_text(orig)
        script = str(root / "tools/patch-dock-licence-wait.py")
        r1 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
        once = sc.read_text()
        r2 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and sc.read_text() == once and P.MARKER in once,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        loop = once[once.index("    uint64_t deadline = begin + 90000;\n"):]
        loop = loop[:loop.index("\n    if (blips) o->event(")]
        check("the loop runs to a deadline that starts at 90 s, with a tick bound for 180 s at 20 ms",
              "for (unsigned tick = 0; tick < 9000 && o->now_ms() < deadline; ++tick) {" in loop and "o->sleep_ms(20);" in loop
              and "o->now_ms() - begin < 90000" not in once)
        moved = loop[loop.index("if (!deadline_moved) {"):]
        moved = moved[:moved.index("\n                }\n") + 18]
        check("the deadline moves only at the first sign-in, only later, to 45 s after it, capped at 180 s from the logon start, with one report field",
              loop.count("deadline = want < cap ? want : cap;") == 1 and "uint64_t want = now + 45000, cap = begin + 180000;" in moved
              and "deadline_moved = true;" in moved and "if (want > deadline) {" in moved
              and 'o->event("session-licence-wait-ms", (int32_t)(deadline - begin));' in moved
              and loop.index("online_at = now;") < loop.index("if (!deadline_moved) {"))
        check("MADEIRA_DOCK_LICENCE_WAIT=0 keeps the 90 s (the deadline counts as moved from the start)",
              'const char *licence_wait = getenv("MADEIRA_DOCK_LICENCE_WAIT");' in once
              and 'bool deadline_moved = licence_wait && !strcmp(licence_wait, "0");' in once)
        a, b = orig.splitlines(), once.splitlines()
        import difflib
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("nothing else changes: the loop line replaced, the sign-in branch extended", len(ops) == 2, ops)
        bad = t / "bad.c"
        bad.write_text(orig.replace("tick < 4500 && o->now_ms() - begin < 90000", "tick < 4500 && o->now_ms() - begin < 95000"))
        r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves the file alone",
              r3.returncode != 0 and "licence loop" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)

        # the deadline's arithmetic, cut from the patched source
        init = once[once.index("    const char *licence_wait = getenv(\"MADEIRA_DOCK_LICENCE_WAIT\");\n"):once.index("    for (unsigned tick = 0; tick < 9000")]
        branch = moved
        harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct obs { void (*event)(const char *, int32_t); };
static int32_t last_event = -1;
static void ev(const char *name, int32_t v) { if (!strcmp(name, "session-licence-wait-ms")) last_event = v; }
static uint64_t deadline_after(uint64_t begin, int64_t first, int64_t second)
{
    struct obs obs = { ev }, *o = &obs;
''' + init + r'''
    uint64_t times[2] = { (uint64_t)first, (uint64_t)second };
    for (int i = 0; i < 2; i++) {
        if (times[i] == (uint64_t)-1) continue;
        uint64_t now = begin + times[i];
        ''' + branch.replace("\n", "\n    ") + r'''
    }
    return deadline - begin;
}
int main(void)
{
    printf("%llu %llu %llu %llu %llu", (unsigned long long)deadline_after(1000, 1000, -1), (unsigned long long)deadline_after(1000, 45000, -1),
           (unsigned long long)deadline_after(1000, 64300, -1), (unsigned long long)deadline_after(1000, 150000, -1),
           (unsigned long long)deadline_after(1000, 64300, 120000));
    setenv("MADEIRA_DOCK_LICENCE_WAIT", "0", 1);
    printf(" %llu\n", (unsigned long long)deadline_after(1000, 64300, -1));
    return 0;
}
'''
        cc = shutil.which("cc") or shutil.which("clang")
        if cc:
            (t / "h.c").write_text(harness)
            b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", str(t / "h.c"), "-o", str(t / "h")],
                               capture_output=True, text=True)
            check("the deadline code compiles on the host (C11, warnings are errors)", b.returncode == 0, b.stderr[-1200:])
            if b.returncode == 0:
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_DOCK_LICENCE_WAIT"}
                out = subprocess.run([str(t / "h")], capture_output=True, text=True, env=env).stdout.split()
                check("sign-in at 1 s and 45 s: 90 s; at 64 s (444TS8): 109 s; at 150 s: the 180 s cap; a second sign-in moves nothing; the "
                      "switch 0: 90 s", out == ["90000", "90000", "109300", "180000", "109300", "90000"], out)
        else:
            print("skip the host compile: no C compiler")

        mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                  Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                      if (m / "x86_64-w64-mingw32-clang").exists()), None)
        if mingw:
            lc = t / "src/launch.c"
            for patch in ("tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py"):
                subprocess.run([sys.executable, str(root / patch), str(lc)], capture_output=True, text=True)
            b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"],
                               capture_output=True, text=True)
            check("the patched host compiles with build/madeira-dock/build.sh's flags, with the workflow's launch.c patches",
                  b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
        else:
            print("skip the Dock compile: no llvm-mingw at hand")

print("PASS: a late sign-in gets time for the licence list" if ok else "FAILED: Dock licence wait")
sys.exit(0 if ok else 1)
