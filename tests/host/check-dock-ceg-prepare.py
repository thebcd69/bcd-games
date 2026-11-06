#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Steam CEG in a Dock session: service start, retry on 55, "already prepared", the empty wine.inf folders (round 632, upstream v0.2.0 #519).

Games whose depots flag custom executables (CheckGuid) need Valve's client to prepare the per-account executables before LaunchApp. 24
BCD logs of 12 games (Call of Duty: Black Ops, Spec Ops: The Line, Saints Row, Mafia II, ...; builds 574-630) ended "ceg-job-result=55,
ceg-result=55, host result 49", one (FRKBN1, Borderlands: The Pre-Sequel) "ceg-request=0, ceg-result=0". Three build-time patches of the Dock
(upstream Madeira build/dock-patches 0001-0003 as BCD's tools/patch-dock-ceg-*.py) and the empty folders stock Wine makes (system32\\mui,
catroot, spool\\printers, syswow64\\mui; WineProcessBridge.m) address them. Nothing here generates, patches or fakes an executable, a ticket or
a server answer: Valve's registered service is started through the service manager, the client's own request is repeated, and the game is
started only when the real client answers OK.

Checks (the Dock source is madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; the parts that need it are skipped without it):
  1. Wiring: the Dock build step runs this check and the three scripts after the other launch.c/session.c patches' predecessors, each
     warning only on failure; the retired ceg-zero-jobs patch is gone from the workflow; the catalog lists MADEIRA_DOCK_CEG_SERVICE_START;
     the app logs the four new numeric fields; auto-tune's CEG rules are fixedIn 632.
  2. The scripts on a copy of the Dock: each applies once after the workflow's earlier patches, a second run changes nothing, a moved anchor
     refuses it and leaves the file alone, 0003 refuses without 0002 and with the retired zero-jobs patch, and applies to both files or none.
     Every changed line lies inside the CEG code (the CEG defines and prepare_custom_binaries, scm.c's CEG service code, sh_ceg_request_step).
  3. The CEG gate: sh_ceg_scm_prepare, prepare_custom_binaries and sh_ceg_request_step have no call site outside the block that
     MADEIRA_STEAM_HOST_CEG=1 opens, and the app sets that variable only for a game with custom executables (not CEG: no code changes).
  4. prepare_custom_binaries, original and patched, built on the host over the same scripted client: every start that works today (jobs that
     all succeed, a refusal, a busy client, a job that fails with another code, the 5-minute wait) gives the identical result and events; only a
     request whose job failed with 55 is retried (3 times, 1.5 s apart, then fails as before) and only a FIRST request that answers OK with no
     job starts the game; after a retried request the same answer still fails closed.
  5. start_client_service built on the host over a scripted service manager: a service that already runs is not touched (no start, no wait);
     a start that needs time waits for running plus the 2 s settle; a start that times out (1053) gets the updater's 20 s; a service that never
     runs ends the wait at 30 s; one that stops again is started once more; the result never exceeds those bounds.
  6. madeira_ensure_wine_inf_dirs, built on the host from WineProcessBridge.m: it creates only the four empty folders, leaves existing ones
     (and everything inside them) alone, adds nothing to a prefix that has them, and touches syswow64 only for a WoW64 session.
  7. The whole patched host compiles with build/madeira-dock/build.sh's flags where llvm-mingw is at hand.
"""
from pathlib import Path
import difflib, importlib.util, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, root / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SVC = load("cegsvc", "tools/patch-dock-ceg-service-start.py")
RETRY = load("cegretry", "tools/patch-dock-ceg-retry.py")
PREP = load("cegprep", "tools/patch-dock-ceg-already-prepared.py")

# ------------------------------------------------------------------------------------------------------------------ 1. wiring
wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
order = ["tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py",
         "tools/patch-dock-install-dir-events.py", "tools/patch-dock-registry-events.py", "tools/patch-dock-ceg-service-start.py", "tools/patch-dock-ceg-retry.py", "tools/patch-dock-ceg-already-prepared.py"]
pos = [step.find(p) for p in order]
check("the Dock build step runs this check, then the three CEG scripts in order, after the other launch.c patches and before build.sh",
      "python3 tests/host/check-dock-ceg-prepare.py\n" in step and all(p >= 0 for p in pos) and pos == sorted(pos)
      and pos[-1] < step.find("bash build/madeira-dock/build.sh") and step.find("tests/host/check-dock-ceg-prepare.py") < pos[5])
for script, files in (("patch-dock-ceg-service-start.py", "madeira-dock/src/scm.c"), ("patch-dock-ceg-retry.py", "madeira-dock/src/launch.c"),
                      ("patch-dock-ceg-already-prepared.py", "madeira-dock/src/launch.c madeira-dock/src/validation.c")):
    check("%s warns and goes on when it cannot apply" % script,
          ("python3 tools/%s %s || echo \"::warning::" % (script, files)) in step)
check("the retired ceg-zero-jobs patch is not in the workflow, and its script, check and key are gone",
      "ceg-zero-jobs" not in wf and not (root / "tools/patch-dock-ceg-zero-jobs.py").exists()
      and not (root / "tests/host/check-dock-ceg-zero-jobs.py").exists()
      and "MADEIRA_DOCK_CEG_ZERO_JOBS" not in (root / "build/tools/gen-config-catalog.py").read_text())
check("the workflow runs this check as its own step too", "python3 tests/host/check-dock-ceg-prepare.py" in wf)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_CEG_SERVICE_START, default on, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_CEG_SERVICE_START": \{[^}]*"default": "1"[^}]*"sources": \["tools/patch-dock-ceg-service-start\.py"\]', cat, re.S)
      is not None)
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app logs the four new numeric fields", all('"%s"' % k in allowed for k in
      ("ceg-service-start", "ceg-service-start-retry", "ceg-request-retry", "ceg-already-prepared")))
rules = (root / "build/tools/autotune_rules.py").read_text()
for rid in ("dock-ceg-result-55", "dock-ceg-no-job"):
    body = rules[rules.index('"id": "%s"' % rid):]
    body = body[:body.index("\n    },\n")]
    check("auto-tune rule %s is fixedIn 632 with no line" % rid, '"fixedIn": 632' in body and '"lines": []' in body and "noFix" not in body, body[:300])

# -------------------------------------------------------------------------------------------------------- 3. the CEG gate (text)
dock_launch = None
dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
mp = (root / "app/Madeira/MadeiraDock.swift").read_text()
check("the app exports MADEIRA_STEAM_HOST_CEG=1 only for a game with custom executables (and unsets it otherwise)",
      re.search(r'if game\.customExecutables && SteamSignIn\.flag\("MADEIRA_DOCK_CEG", default: true\) \{\s*setenv\("MADEIRA_STEAM_HOST_CEG", "1", 1\)\s*\} else \{\s*unsetenv\("MADEIRA_STEAM_HOST_CEG"\)', mp)
      is not None)

if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = {f: (dock / "src" / f).read_text() for f in ("launch.c", "scm.c", "validation.c", "session.c")}
    if "madeira-bcd: ceg-" in orig["launch.c"] + orig["scm.c"] + orig["validation.c"]:
        print("note: the Dock tree is already patched; the checks below need the pristine pin, so they are skipped")
    else:
        with tempfile.TemporaryDirectory(prefix="madeira-dock-ceg-prepare-") as td:
            t = Path(td)
            earlier = ("tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py",
                       "tools/patch-dock-install-dir-events.py", "tools/patch-dock-registry-events.py")

            def fresh(name):
                d = t / name
                shutil.copytree(dock / "src", d)
                return d

            def run(script, *files):
                r = subprocess.run([sys.executable, str(root / script)] + [str(f) for f in files], capture_output=True, text=True)
                return r.returncode, r.stdout + r.stderr

            # ---------------------------------------------------------------------------------------- 2. the scripts on a copy
            base = fresh("base")
            for p in earlier:
                rc, out = run(p, base / "launch.c")
                check("the workflow's earlier patch %s applies to the copy" % p, rc == 0, out)
            rc, out = run("tools/patch-dock-licence-wait.py", base / "session.c")
            check("the workflow's session.c patch applies to the copy", rc == 0, out)
            before = {f: (base / f).read_text() for f in ("launch.c", "scm.c", "validation.c")}
            rc1, o1 = run("tools/patch-dock-ceg-service-start.py", base / "scm.c")
            rc2, o2 = run("tools/patch-dock-ceg-retry.py", base / "launch.c")
            rc3, o3 = run("tools/patch-dock-ceg-already-prepared.py", base / "launch.c", base / "validation.c")
            check("the three scripts apply once, in order, after the earlier patches", rc1 == rc2 == rc3 == 0, o1 + o2 + o3)
            once = {f: (base / f).read_text() for f in ("launch.c", "scm.c", "validation.c")}
            check("each file carries its marker", SVC.MARKER in once["scm.c"] and RETRY.MARKER in once["launch.c"]
                  and PREP.MARKER in once["launch.c"] and PREP.MARKER in once["validation.c"])
            again = [run("tools/patch-dock-ceg-service-start.py", base / "scm.c"), run("tools/patch-dock-ceg-retry.py", base / "launch.c"),
                     run("tools/patch-dock-ceg-already-prepared.py", base / "launch.c", base / "validation.c")]
            check("a second run says so and changes nothing",
                  all(rc == 0 and "already in" in out for rc, out in again) and all((base / f).read_text() == once[f] for f in once), again)

            # every changed line is inside the CEG code
            def changed_lines(a, b):
                return [i for op in difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).get_opcodes()
                        if op[0] != "equal" for i in range(op[3], max(op[4], op[3] + 1))]

            lc = once["launch.c"].splitlines()
            lo = next(i for i, l in enumerate(lc) if l.startswith("#define SH_CEG_WAIT_MS"))
            fn = next(i for i, l in enumerate(lc) if l.startswith("static int prepare_custom_binaries("))
            hi = next(i for i in range(fn, len(lc)) if lc[i] == "}")
            doc = max(i for i in range(fn) if lc[i].startswith("/* Returns 0 once every started job"))
            moved = changed_lines(before["launch.c"], once["launch.c"])
            check("launch.c changes only in the CEG defines and prepare_custom_binaries (with its doc comment)",
                  moved and all(lo <= i <= hi or doc <= i <= fn for i in moved), (lo, doc, fn, hi, moved[:10], moved[-5:]))
            sc = once["scm.c"].splitlines()
            s_lo = next(i for i, l in enumerate(sc) if l.startswith("int32_t sh_ceg_scm_prepare("))
            s_end = next(i for i in range(s_lo, len(sc)) if sc[i] == "}")
            s_def = next(i for i, l in enumerate(sc) if l.startswith("#define SH_SERVICE_INSTALL_MS"))
            s_head = next(i for i, l in enumerate(sc) if "Dock itself never creates" in l)
            s_ok = [i for i in changed_lines(before["scm.c"], once["scm.c"])
                    if not (i <= s_head + 2 or s_def < i <= s_def + 3 or s_lo - 130 < i <= s_end)]
            check("scm.c changes only in the header comment, three defines, start_client_service and sh_ceg_scm_prepare", not s_ok, s_ok)
            vc = once["validation.c"].splitlines()
            v_lo = next(i for i, l in enumerate(vc) if l.startswith("enum sh_ceg_step sh_ceg_request_step("))
            v_end = next(i for i in range(v_lo, len(vc)) if vc[i] == "}")
            check("validation.c changes only in sh_ceg_request_step",
                  all(v_lo <= i <= v_end for i in changed_lines(before["validation.c"], once["validation.c"])))

            # a moved anchor refuses and leaves the file alone
            for script, fname, bad_from, bad_to, word in (
                    ("tools/patch-dock-ceg-service-start.py", "scm.c", "#define SH_SERVICE_INSTALL_MS 60000\n", "#define SH_SERVICE_INSTALL_MS 61000\n", "anchor"),
                    ("tools/patch-dock-ceg-retry.py", "launch.c", "#define SH_CEG_BUSY_RETRY_MS 10000\n", "#define SH_CEG_BUSY_RETRY_MS 9000\n", "anchor")):
                fresh_dir = fresh("moved-" + fname)
                for p in earlier:
                    run(p, fresh_dir / "launch.c")
                text = (fresh_dir / fname).read_text().replace(bad_from, bad_to)
                (fresh_dir / fname).write_text(text)
                rc, out = run(script, fresh_dir / fname)
                check("%s: a moved anchor refuses and leaves the file alone" % script, rc != 0 and word in out and (fresh_dir / fname).read_text() == text, out)
            m = fresh("moved-validation")
            for p in earlier:
                run(p, m / "launch.c")
            run("tools/patch-dock-ceg-service-start.py", m / "scm.c")
            run("tools/patch-dock-ceg-retry.py", m / "launch.c")
            vtext = (m / "validation.c").read_text().replace("progress->failure = result == 1 ? 0 : result;", "progress->failure = result;")
            (m / "validation.c").write_text(vtext)
            ltext = (m / "launch.c").read_text()
            rc, out = run("tools/patch-dock-ceg-already-prepared.py", m / "launch.c", m / "validation.c")
            check("already-prepared: a moved validation.c anchor refuses and neither file changes",
                  rc != 0 and (m / "validation.c").read_text() == vtext and (m / "launch.c").read_text() == ltext, out)
            n = fresh("no-retry")
            for p in earlier:
                run(p, n / "launch.c")
            rc, out = run("tools/patch-dock-ceg-already-prepared.py", n / "launch.c", n / "validation.c")
            check("already-prepared refuses without the retry patch (remote_retries) and changes nothing",
                  rc != 0 and "retry" in out and "madeira-bcd: ceg-already-prepared" not in (n / "launch.c").read_text()
                  and (n / "validation.c").read_text() == orig["validation.c"], out)
            z = fresh("zero-jobs")
            z_script = root / "tools/patch-dock-ceg-zero-jobs.py"
            zl = (z / "launch.c").read_text()
            zl = zl.replace(
                '                if (step != SH_CEG_WAIT) {\n                    o->event("ceg-result", progress.failure);',
                '                if (step != SH_CEG_WAIT) {\n                    /* madeira-bcd: ceg-zero-jobs-v1 (retired) */\n                    o->event("ceg-result", progress.failure);')
            (z / "launch.c").write_text(zl)
            run("tools/patch-dock-ceg-retry.py", z / "launch.c")
            rc, out = run("tools/patch-dock-ceg-already-prepared.py", z / "launch.c", z / "validation.c")
            check("already-prepared refuses next to the retired zero-jobs patch", rc != 0 and "zero-jobs" in out, out)
            half = fresh("half")
            for p in earlier:
                run(p, half / "launch.c")
            run("tools/patch-dock-ceg-retry.py", half / "launch.c")
            run("tools/patch-dock-ceg-already-prepared.py", half / "launch.c", half / "validation.c")
            (half / "validation.c").write_text(orig["validation.c"])
            rc, out = run("tools/patch-dock-ceg-already-prepared.py", half / "launch.c", half / "validation.c")
            check("already-prepared: only one of the two files patched is refused", rc != 0 and "only one" in out, out)

            # the order does not matter for the anchors: the CEG scripts first, then the other launch.c scripts
            top = fresh("top")
            good = True
            good &= run("tools/patch-dock-ceg-service-start.py", top / "scm.c")[0] == 0
            good &= run("tools/patch-dock-ceg-retry.py", top / "launch.c")[0] == 0
            good &= run("tools/patch-dock-ceg-already-prepared.py", top / "launch.c", top / "validation.c")[0] == 0
            for p in earlier:
                rc, out = run(p, top / "launch.c")
                good &= rc == 0
            good &= run("tools/patch-dock-licence-wait.py", top / "session.c")[0] == 0
            check("the other launch.c and session.c scripts still apply after the CEG scripts (the anchors do not overlap)", good)
            check("both orders give the same launch.c, scm.c and validation.c",
                  all((top / f).read_text() == once[f] for f in ("launch.c", "scm.c", "validation.c")) or
                  all(sorted((top / f).read_text().splitlines()) == sorted(once[f].splitlines()) for f in ("launch.c", "scm.c", "validation.c")))

            # ------------------------------------------------------------------------------------ 3. the gate, in the real source
            lc_text = once["launch.c"]
            gate_open = lc_text.index('if (wide_flag(L"MADEIRA_STEAM_HOST_CEG", L\'1\')) {')
            depth, gate_end = 0, lc_text.index("{", gate_open)
            while True:
                depth += (lc_text[gate_end] == "{") - (lc_text[gate_end] == "}")
                gate_end += 1
                if depth == 0:
                    break
            outside = lc_text[:gate_open] + lc_text[gate_end:]
            fn_text = lc_text[lc_text.index("static int prepare_custom_binaries("):]
            fn_text = fn_text[:fn_text.index("\n}\n") + 3]
            outside_no_fn = outside.replace(fn_text, "")
            check("sh_ceg_scm_prepare and prepare_custom_binaries are called only inside the MADEIRA_STEAM_HOST_CEG=1 block",
                  "sh_ceg_scm_prepare(" not in outside_no_fn and "prepare_custom_binaries(" not in outside_no_fn)
            others = "".join((dock / "src" / f).read_text() for f in ("main.c", "session.c", "probe.c", "auth.c"))
            check("sh_ceg_request_step, sh_ceg_scm_prepare and start_client_service have no other caller in the Dock",
                  "sh_ceg_request_step(" not in others and "sh_ceg_scm_prepare(" not in others and "start_client_service" not in others)

            # ------------------------------------------------------------------------------------ 4. prepare_custom_binaries on the host
            cc = shutil.which("cc") or shutil.which("clang")

            def region(src):
                a = src.index("#define SH_CEG_WAIT_MS")
                b = src.index("typedef int32_t (__thiscall *ceg_request_fn)")
                b = src.index("\n", b) + 1
                f = src.index("static int prepare_custom_binaries(")
                e = src.index("\n}\n", f) + 3
                return src[a:b], src[f:e]

            harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <wchar.h>
#include "validation.h"
#include "probe.h"
#include "client_layout.h"
#define __thiscall
typedef void *HMODULE;
typedef long LONG;
static volatile LONG interrupted = 0;
static LONG InterlockedCompareExchange(volatile LONG *d, LONG e, LONG c) { LONG o = *d; if (o == c) *d = e; return o; }
static bool method_is(HMODULE m, void *obj, unsigned slot, uintptr_t rva) { (void)m; (void)obj; (void)slot; (void)rva; return true; }
@@DEFS@@
@@FUNC@@
/* the scripted client and clock */
static uint64_t clock_ms = 1000;
static char events[2048];
static int requests;
static uint64_t request_times[16];
static void ev(const char *name, int32_t v) { char b[64]; snprintf(b, sizeof b, "%s=%d ", name, (int)v); strncat(events, b, sizeof events - strlen(events) - 1); }
static uint64_t now_ms(void) { return clock_ms; }
static void sleep_ms(uint32_t ms) { clock_ms += ms; }
struct resp { int32_t status; uint32_t jobs; int njobs; int32_t jobres[4]; };
struct scenario { const char *name; int n; struct resp r[6]; };
static struct scenario sc;
struct cbq { int id; unsigned char data[12]; int size; };
static struct cbq queue[16]; static int qhead, qtail;
static void push(int id, int32_t result, uint32_t app, int size) {
    struct cbq *c = &queue[qtail++]; c->id = id; c->size = size; memset(c->data, 0, sizeof c->data);
    memcpy(c->data, &result, 4); memcpy(c->data + 4, &app, 4);
}
static int32_t fake_request(void *self, uint32_t app, bool force, bool fast, uint32_t *jobs)
{
    (void)self; (void)force; (void)fast;
    if (requests < 16) request_times[requests] = clock_ms;
    struct resp *r = requests < sc.n ? &sc.r[requests] : &sc.r[sc.n - 1];
    requests++;
    *jobs = r->jobs;
    for (int i = 0; i < r->njobs; i++) push(SH_CEG_JOB_FINISHED, r->jobres[i], app, 8);
    return r->status;
}
static bool get_callback(int32_t pipe, struct sh_callback *cb)
{
    (void)pipe;
    if (qhead >= qtail) return false;
    cb->user = 1; cb->id = queue[qhead].id; cb->data = queue[qhead].data; cb->size = queue[qhead].size; qhead++;
    return true;
}
static void free_callback(int32_t pipe) { (void)pipe; }
#define U UINT32_MAX
static struct scenario scenarios[] = {
    {"ok-1-job",            1, {{1, 1, 1, {1}}}},
    {"ok-3-jobs",           1, {{1, 3, 3, {1, 1, 1}}}},
    {"job-fails-5",         1, {{1, 1, 1, {5}}}},
    {"job-fails-2-of-3",    1, {{1, 3, 2, {1, 22}}}},
    {"refused-2",           1, {{2, 0, 0, {0}}}},
    {"request-fails-55",    1, {{55, 0, 0, {0}}}},
    {"busy-then-ok",        2, {{10, 0, 0, {0}}, {1, 1, 1, {1}}}},
    {"unset-count",         1, {{1, U, 0, {0}}}},
    {"too-many-jobs",       1, {{1, 5000, 0, {0}}}},
    {"never-finishes",      1, {{1, 1, 0, {0}}}},
    {"zero-jobs-first",     1, {{1, 0, 0, {0}}}},
    {"job-55-then-ok",      2, {{1, 1, 1, {55}}, {1, 1, 1, {1}}}},
    {"job-55-of-2-then-ok", 2, {{1, 2, 2, {1, 55}}, {1, 1, 1, {1}}}},
    {"job-55-five-times",   1, {{1, 1, 1, {55}}}},
    {"job-55-then-zero",    2, {{1, 1, 1, {55}}, {1, 0, 0, {0}}}},
    {"job-55-then-refused", 2, {{1, 1, 1, {55}}, {2, 0, 0, {0}}}},
};
int main(void)
{
    struct sh_observer obs = { now_ms, sleep_ms, ev };
    struct sh_api api = {0}; api.get_callback = get_callback; api.free_callback = free_callback;
    struct dock_client_layout layout = {0}; layout.ceg_request = 1;
    void *vt[100] = {0}; vt[71] = (void *)fake_request; struct { void **vt; } object = { vt };
    for (unsigned i = 0; i < sizeof scenarios / sizeof scenarios[0]; i++) {
        sc = scenarios[i]; clock_ms = 1000; events[0] = 0; requests = 0; qhead = qtail = 0;
        int r = prepare_custom_binaries(NULL, &object, &api, &obs, 1, 42700, &layout);
        printf("%s|%d|%s|requests=%d gaps=", sc.name, r, events, requests);
        for (int k = 1; k < requests && k < 16; k++) printf("%d,", (int)(request_times[k] - request_times[k - 1]));
        printf("\n");
    }
    return 0;
}
'''
            outs = {}
            if cc:
                for label, files in (("original", before), ("patched", once)):
                    d = t / ("h-" + label)
                    shutil.copytree(base, d)
                    for f in ("launch.c", "scm.c", "validation.c"):
                        (d / f).write_text(files[f])
                    if label == "original":
                        (d / "validation.c").write_text(orig["validation.c"])
                        launch_src = before["launch.c"]
                    else:
                        launch_src = once["launch.c"]
                    defs, func = region(launch_src)
                    (d / "h.c").write_text(harness.replace("@@DEFS@@", defs).replace("@@FUNC@@", func))
                    b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter",
                                        "-I", str(d), str(d / "h.c"), str(d / "validation.c"), "-o", str(d / "h")], capture_output=True, text=True)
                    check("the %s prepare_custom_binaries builds on the host with the Dock's validation.c (C11, warnings are errors)" % label,
                          b.returncode == 0, b.stderr[-1800:])
                    if b.returncode == 0:
                        outs[label] = {l.split("|")[0]: l for l in subprocess.run([str(d / "h")], capture_output=True, text=True).stdout.splitlines()}
            else:
                print("skip the host build: no C compiler")
            if len(outs) == 2:
                o, p = outs["original"], outs["patched"]
                same = ("ok-1-job", "ok-3-jobs", "job-fails-5", "job-fails-2-of-3", "refused-2", "request-fails-55", "busy-then-ok",
                        "unset-count", "too-many-jobs", "never-finishes")
                check("every start that works today and every failure that is not 55 or zero jobs gives the identical result and events",
                      all(o[k] == p[k] for k in same) and len(o) == 16, [(o[k], p[k]) for k in same if o[k] != p[k]])
                check("sanity: the original returns 0 for jobs that succeed, 49 for the failures",
                      o["ok-1-job"].split("|")[1] == "0" and o["ok-3-jobs"].split("|")[1] == "0" and o["job-fails-5"].split("|")[1] == "49"
                      and "ceg-result=-1" in o["never-finishes"] and "ceg-result=5 " in o["job-fails-5"], o["never-finishes"])
                check("original: a first request that answers OK with no job fails (49, ceg-result=0); job 55 fails (49, ceg-result=55)",
                      o["zero-jobs-first"].split("|")[1] == "49" and "ceg-request=0 ceg-result=0" in o["zero-jobs-first"]
                      and o["job-55-then-ok"].split("|")[1] == "49" and "ceg-job-result=55" in o["job-55-then-ok"] and "ceg-result=55" in o["job-55-then-ok"])
                z = p["zero-jobs-first"].split("|")
                check("patched: a FIRST request that answers OK with no job starts the game (0, ceg-already-prepared=1, ceg-result=1) and asks once",
                      z[1] == "0" and z[2] == "ceg-request-result=1 ceg-request=0 ceg-already-prepared=1 ceg-result=1 " and "requests=1" in z[3], z)
                r1 = p["job-55-then-ok"].split("|")
                check("patched: a job that failed with 55 is asked again 1.5 s later and the second request's success starts the game",
                      r1[1] == "0" and "ceg-job-result=55 ceg-request-retry=1 ceg-request-result=1 ceg-request=1 " in r1[2] and r1[2].endswith("ceg-result=1 ")
                      and "requests=2 gaps=" in r1[3] and r1[3].split("gaps=")[1].startswith("1"), r1)
                r2 = p["job-55-of-2-then-ok"].split("|")
                check("patched: 55 from one of two jobs retries the same way", r2[1] == "0" and "ceg-request-retry=1" in r2[2] and "requests=2" in r2[3], r2)
                r3 = p["job-55-five-times"].split("|")
                check("patched: 55 four times in a row retries three times (1.5 s apart), then fails exactly as before (49, ceg-result=55)",
                      r3[1] == "49" and r3[2].count("ceg-request-retry=") == 3 and r3[2].endswith("ceg-finished-jobs=0 ceg-result=55 ") and "requests=4" in r3[3]
                      and r3[3].split("gaps=")[1].startswith("15"), r3)
                r4 = p["job-55-then-zero"].split("|")
                check("patched: after a retried request an OK with no job still fails closed (49, ceg-result=0, no ceg-already-prepared)",
                      r4[1] == "49" and "ceg-already-prepared" not in r4[2] and r4[2].endswith("ceg-request=0 ceg-result=0 "), r4)
                r5 = p["job-55-then-refused"].split("|")
                check("patched: after a retried request a refusal fails with its own code (49, ceg-result=2)", r5[1] == "49" and r5[2].endswith("ceg-result=2 "), r5)
                check("original and patched differ in exactly the intended scenarios",
                      sorted(k for k in o if o[k] != p[k]) == sorted(["zero-jobs-first", "job-55-then-ok", "job-55-of-2-then-ok", "job-55-five-times",
                                                                       "job-55-then-zero", "job-55-then-refused"]),
                      sorted(k for k in o if o[k] != p[k]))

            # ------------------------------------------------------------------------------------ 5. start_client_service on the host
            sh = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include "validation.h"
#include "probe.h"
typedef void *SC_HANDLE;
typedef unsigned long DWORD;
typedef int BOOL;
typedef unsigned char BYTE;
typedef struct { DWORD dwCurrentState; } SERVICE_STATUS_PROCESS;
#define SERVICE_START 0x10
#define SERVICE_QUERY_STATUS 4
#define SERVICE_RUNNING 4
#define SERVICE_STOPPED 1
#define SERVICE_START_PENDING 2
#define SC_STATUS_PROCESS_INFO 0
#define ERROR_SERVICE_REQUEST_TIMEOUT 1053
#define ERROR_SERVICE_ALREADY_RUNNING 1056
#define ERROR_TIMEOUT 1460
#define TRUE 1
#define FALSE 0
#define NULL_HANDLE ((SC_HANDLE)0)
static const wchar_t steam_service[] = L"Steam Client Service";
@@DEFS@@
/* scripted service manager and clock */
static uint64_t clock_ms = 100000;
static char events[512];
static int starts, queries, opened, closed; static uint64_t slept;
static DWORD last_error;
static int mode;              /* the scenario */
static uint64_t started_at;   /* clock of the last successful StartServiceW */
static int state_now(void)
{
    uint64_t since = clock_ms - started_at;
    switch (mode) {
    case 0: return SERVICE_RUNNING;                                               /* already running */
    case 1: return starts ? (since >= 3000 ? SERVICE_RUNNING : SERVICE_START_PENDING) : SERVICE_STOPPED;   /* up after 3 s */
    case 2: return clock_ms >= started_at && starts && since >= 12000 ? SERVICE_RUNNING : SERVICE_STOPPED; /* 1053, then the updater restarts it */
    case 3: return starts ? SERVICE_START_PENDING : SERVICE_STOPPED;               /* never runs */
    case 4: return SERVICE_STOPPED;                                                /* stops again, always */
    case 5: return starts ? (since >= 500 ? SERVICE_RUNNING : SERVICE_STOPPED) : SERVICE_STOPPED;  /* STOPPED for 0.5 s, then up */
    case 8: return starts ? SERVICE_RUNNING : SERVICE_STOPPED;                     /* the start says "already running" */
    default: return SERVICE_STOPPED;
    }
}
static SC_HANDLE OpenServiceW(SC_HANDLE m, const wchar_t *n, DWORD a) { (void)m; (void)n; (void)a; opened++; if (mode == 7) { last_error = 1060; return NULL_HANDLE; } return (SC_HANDLE)1; }
static BOOL StartServiceW(SC_HANDLE s, DWORD n, const wchar_t **a) {
    (void)s; (void)n; (void)a;
    if (mode == 6) { last_error = 5; return FALSE; }
    if (mode == 2 && starts == 0) { starts++; started_at = clock_ms; last_error = ERROR_SERVICE_REQUEST_TIMEOUT; return FALSE; }
    if (mode == 8) { starts++; last_error = ERROR_SERVICE_ALREADY_RUNNING; started_at = clock_ms; return FALSE; }
    starts++; started_at = clock_ms; return TRUE;
}
static BOOL QueryServiceStatusEx(SC_HANDLE s, int level, BYTE *buf, DWORD size, DWORD *needed) {
    (void)s; (void)level; (void)size; (void)needed; queries++;
    SERVICE_STATUS_PROCESS st; st.dwCurrentState = (DWORD)state_now(); memcpy(buf, &st, sizeof st); return TRUE;
}
static BOOL CloseServiceHandle(SC_HANDLE s) { (void)s; closed++; return TRUE; }
static DWORD GetLastError(void) { return last_error; }
static void ev(const char *name, int32_t v) { char b[64]; snprintf(b, sizeof b, "%s=%d ", name, (int)v); strncat(events, b, sizeof events - strlen(events) - 1); }
static uint64_t now_ms(void) { return clock_ms; }
static void sleep_ms(uint32_t ms) { clock_ms += ms; slept += ms; }
@@QUERY@@
@@FUNC@@
int main(void)
{
    struct sh_observer obs = { now_ms, sleep_ms, ev };
    for (mode = 0; mode <= 8; mode++) {
        clock_ms = 100000; events[0] = 0; starts = queries = opened = closed = 0; slept = 0; started_at = 0; last_error = 0;
        int32_t r = start_client_service(&obs, (SC_HANDLE)2);
        printf("%d|%d|%s|starts=%d slept=%llu opened=%d closed=%d\n", mode, (int)r, events, starts, (unsigned long long)slept, opened, closed);
    }
    return 0;
}
'''
            sc_text = once["scm.c"]
            defs = "".join(l + "\n" for l in sc_text.splitlines() if l.startswith("#define SH_SERVICE_") and "_MS" in l)
            q0 = sc_text.index("static bool query_state(SC_HANDLE service, SERVICE_STATUS_PROCESS *status)\n{")
            query = sc_text[q0:sc_text.index("\n}\n", q0) + 3]
            f0 = sc_text.index("static int32_t start_client_service(")
            func = sc_text[f0:sc_text.index("\n}\n", f0) + 3]
            if cc:
                d = t / "h-scm"
                d.mkdir()
                for f in ("validation.h", "validation.c", "probe.h"):
                    shutil.copy(base / f, d / f)
                (d / "h.c").write_text(sh.replace("@@DEFS@@", defs).replace("@@QUERY@@", "static bool query_state(SC_HANDLE service, SERVICE_STATUS_PROCESS *status);\n").replace("@@FUNC@@", func + "\n" + query))
                b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter", "-I", str(d), str(d / "h.c"),
                                    str(d / "validation.c"), "-o", str(d / "h")], capture_output=True, text=True)
                check("start_client_service builds on the host over a scripted service manager (C11, warnings are errors)", b.returncode == 0, b.stderr[-1800:])
                if b.returncode == 0:
                    res = {int(l.split("|")[0]): l.split("|") for l in subprocess.run([str(d / "h")], capture_output=True, text=True).stdout.splitlines()}
                    check("a service that already runs is not touched: result 0, no start, no wait, no event",
                          res[0][1] == "0" and res[0][2] == "" and "starts=0 slept=0" in res[0][3], res[0])
                    check("a start that needs 3 s: result 0 after waiting for running (100 ms steps) plus the 2 s settle",
                          res[1][1] == "0" and "starts=1" in res[1][3] and 4900 <= int(re.search(r"slept=(\d+)", res[1][3]).group(1)) <= 5300, res[1])
                    check("a start that times out (1053) gets the updater's time: event ceg-service-start-retry=1053, result 0 once it runs",
                          res[2][1] == "0" and res[2][2] == "ceg-service-start-retry=1053 " and 14000 <= int(re.search(r"slept=(\d+)", res[2][3]).group(1)) <= 14300, res[2])
                    check("a service that never runs ends the wait at 30 s with ERROR_TIMEOUT (1460), no settle",
                          res[3][1] == "1460" and 30000 <= int(re.search(r"slept=(\d+)", res[3][3]).group(1)) <= 30200, res[3])
                    check("a service that stops again is started once more, then ends with -1 (about 2 s + 2 s of grace, never 30 s)",
                          res[4][1] == "-1" and "starts=2" in res[4][3] and int(re.search(r"slept=(\d+)", res[4][3]).group(1)) <= 4300, res[4])
                    check("a service that is STOPPED for half a second after the start is waited for, not restarted",
                          res[5][1] == "0" and "starts=1" in res[5][3], res[5])
                    check("access denied on the start (5) is the result at once (no wait); a missing service (1060) too",
                          res[6][1] == "5" and "slept=0" in res[6][3] and res[7][1] == "1060" and "starts=0" in res[7][3], (res[6], res[7]))
                    check("'already running' from the start is no error: the wait finds it running, then only the 2 s settle",
                          res[8][1] == "0" and res[8][2] == "" and "slept=2000" in res[8][3], res[8])
                    check("the service handle is closed on every path that opened one (all but the missing service)",
                          all("opened=1 closed=1" in r[3] for m, r in res.items() if m != 7) and "opened=1 closed=0" in res[7][3], [r[3] for r in res.values()])
            sc_call = once["scm.c"][once["scm.c"].index("int32_t sh_ceg_scm_prepare("):]
            sc_call = sc_call[:sc_call.index("\n}\n")]
            check("sh_ceg_scm_prepare starts the service only for a registered service and MADEIRA_DOCK_CEG_SERVICE_START not 0, after the install step; "
                  "its return value is still only the registration result",
                  'if (registered && !wide_flag(L"MADEIRA_DOCK_CEG_SERVICE_START", L\'0\'))' in sc_call
                  and sc_call.index("ceg-service-install") < sc_call.index("ceg-service-start")
                  and sc_call.rstrip().endswith("return sh_ceg_scm_result(true, registered);"), sc_call[-400:])

            # ------------------------------------------------------------------------------------ 7. the whole host
            mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                      Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                          if (m / "x86_64-w64-mingw32-clang").exists()), None)
            if mingw:
                b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                    "-static", "-Wl,--strip-all", "-Wl,--no-insert-timestamp", "-o", str(t / "dockhost.exe")]
                                   + sorted(str(f) for f in base.glob("*.c")) + ["-ladvapi32"], capture_output=True, text=True)
                check("the patched host compiles with build/madeira-dock/build.sh's flags, with every Dock patch of the workflow",
                      b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1800:])
            else:
                print("skip the Dock compile: no llvm-mingw at hand")

# ------------------------------------------------------------------------------------------------------------ 6. the wine.inf folders
wpb = (root / "app/Madeira/WineProcessBridge.m").read_text()
fa = wpb.index("static void madeira_ensure_wine_inf_dirs(")
fn_m = wpb[fa:wpb.index("\n}\n", fa) + 3]
call = wpb[wpb.index("if (has_i386_set) {\n                madeira_link_syswow64(fm, prefix, bundlePath);"):]
call = call[:call.index("madeira_link_wine_mono(fm, prefix, bundlePath);")]
check("the folders are made once per session start, after the i386 farm and before Wine Mono, with the WoW64 flag",
      "madeira_ensure_wine_inf_dirs(fm, prefix, has_i386_set);" in call and wpb.count("madeira_ensure_wine_inf_dirs(fm") == 1)
cc = shutil.which("cc") or shutil.which("clang")
if cc and sys.platform == "darwin":
    with tempfile.TemporaryDirectory(prefix="madeira-wine-inf-") as td:
        t = Path(td)
        (t / "h.m").write_text('''#import <Foundation/Foundation.h>
#include <unistd.h>
''' + fn_m + '''
int main(void)
{
    @autoreleasepool {
        NSFileManager *fm = [NSFileManager defaultManager];
        NSString *root = [NSString stringWithUTF8String:getenv("PREFIXES")];
        NSArray *names = @[@"fresh64", @"fresh32", @"have", @"filemui"];
        for (NSString *n in names) [fm createDirectoryAtPath:[root stringByAppendingPathComponent:n] withIntermediateDirectories:YES attributes:nil error:nil];
        NSString *have = [root stringByAppendingPathComponent:@"have/drive_c/windows/system32/mui"];
        [fm createDirectoryAtPath:have withIntermediateDirectories:YES attributes:nil error:nil];
        [@"keep" writeToFile:[have stringByAppendingPathComponent:@"x.mui"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSString *sysf = [root stringByAppendingPathComponent:@"filemui/drive_c/windows/system32"];
        [fm createDirectoryAtPath:sysf withIntermediateDirectories:YES attributes:nil error:nil];
        [@"file" writeToFile:[sysf stringByAppendingPathComponent:@"mui"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
        madeira_ensure_wine_inf_dirs(fm, [root stringByAppendingPathComponent:@"fresh64"], NO);
        madeira_ensure_wine_inf_dirs(fm, [root stringByAppendingPathComponent:@"fresh32"], YES);
        madeira_ensure_wine_inf_dirs(fm, [root stringByAppendingPathComponent:@"fresh32"], YES);
        madeira_ensure_wine_inf_dirs(fm, [root stringByAppendingPathComponent:@"have"], YES);
        madeira_ensure_wine_inf_dirs(fm, [root stringByAppendingPathComponent:@"filemui"], NO);
    }
    return 0;
}
''')
        b = subprocess.run([cc, "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-framework", "Foundation", str(t / "h.m"), "-o", str(t / "h")], capture_output=True, text=True)
        check("madeira_ensure_wine_inf_dirs builds on the host (warnings are errors)", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            (t / "p").mkdir()
            r = subprocess.run([str(t / "h")], capture_output=True, text=True, env=dict(os.environ, PREFIXES=str(t / "p")))
            tree = lambda n: sorted(str(p.relative_to(t / "p" / n)) + ("/" if p.is_dir() else "") for p in (t / "p" / n).rglob("*"))
            want64 = ["drive_c/", "drive_c/windows/", "drive_c/windows/system32/", "drive_c/windows/system32/catroot/", "drive_c/windows/system32/mui/",
                      "drive_c/windows/system32/spool/", "drive_c/windows/system32/spool/printers/"]
            check("a 64-bit-only session gets system32\\mui, catroot and spool\\printers and nothing in syswow64", tree("fresh64") == want64, tree("fresh64"))
            check("a WoW64 session also gets syswow64\\mui, and a second call adds nothing",
                  tree("fresh32") == sorted(want64 + ["drive_c/windows/syswow64/", "drive_c/windows/syswow64/mui/"]), tree("fresh32"))
            check("an existing mui folder keeps its contents; only the missing folders are made",
                  (t / "p/have/drive_c/windows/system32/mui/x.mui").read_text() == "keep" and (t / "p/have/drive_c/windows/system32/catroot").is_dir()
                  and (t / "p/have/drive_c/windows/syswow64/mui").is_dir())
            check("a file named mui is not replaced (the failure is only counted)",
                  (t / "p/filemui/drive_c/windows/system32/mui").is_file() and (t / "p/filemui/drive_c/windows/system32/mui").read_text() == "file")
            lines = r.stderr.splitlines()
            check("one log line per call: 7 made / 0 / 0, 8 made, 0 made 8 there, 3 made 1 there, a file in the way counts failed",
                  lines == ["[WineProc] wine.inf system folders: 3 made, 0 already there, 0 failed",
                            "[WineProc] wine.inf system folders: 4 made, 0 already there, 0 failed",
                            "[WineProc] wine.inf system folders: 0 made, 4 already there, 0 failed",
                            "[WineProc] wine.inf system folders: 3 made, 1 already there, 0 failed",
                            "[WineProc] wine.inf system folders: 2 made, 0 already there, 1 failed"], lines)
else:
    print("skip the Objective-C build: no compiler or not macOS")

print("PASS: Dock CEG prepare (service start, retry on 55, already prepared, wine.inf folders)" if ok else "FAILED: Dock CEG prepare")
sys.exit(0 if ok else 1)
