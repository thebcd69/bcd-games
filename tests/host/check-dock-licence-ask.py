#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-licence-ask.py: ownership is asked 500 ms after the Steam sign-in, not after a fixed 5 s (round 632, upstream v0.2.0 C17).

BCD logs: in 1,939 Dock sessions the gap between session-authenticated-online and session-requested-app-entitled is 5.0 s (98.6% between 4.5
and 6.5 s). The Dock's session loop (madeira-dock/src/session.c, upstream submodule) is shared by EVERY Steam start, so the check is about what
must not change: a start that works today still works, a start that fails today still fails with the same code at the same moment, and nothing
fails earlier than it does today.

Checks (the Dock source is madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; the parts that need it are skipped without it):
  - CI runs this check and applies the patch after the licence-wait patch, warning only on failure; the catalog lists
    env.MADEIRA_DOCK_LICENCE_WAIT_MS (500); the app logs session-licence-answer-ms;
  - on a copy of session.c after the workflow's other patches the patch applies once, a second run changes nothing, a moved anchor refuses it
    and leaves the file alone, every changed line lies inside the licence loop of sh_session, and the whole patched host compiles with
    build/madeira-dock/build.sh's flags (warnings are errors);
  - the production licence loop, original (without the patch) and patched, cut out of session.c and built on the host over a scripted Valve
    client with a fake clock, in 16 scenarios: where today's start works, the patched one works and never later (500 ms after the sign-in when
    the licence is there; the moment it arrives before 5 s; the old moment after that); where today's start fails (entitled but not listed:
    35; an unreadable list: 36; never licensed: 34 after the deadline), the patched one fails with the same code at the same moment with the
    identical events; from 5 s after the sign-in the two loops give identical events; MADEIRA_DOCK_LICENCE_WAIT_MS=5000 gives the old timing
    exactly, 0 asks at once, a bad value means 500, 20000 waits longer on request.
"""
from pathlib import Path
import difflib, importlib.util, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("licenceask", root / "tools/patch-dock-licence-ask.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the licence-wait patch, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-licence-ask.py\n" in step
      and 'python3 tools/patch-dock-licence-ask.py madeira-dock/src/session.c || echo "::warning::' in step
      and step.index("patch-dock-licence-wait.py") < step.index("patch-dock-licence-ask.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app logs session-licence-answer-ms", '"session-licence-answer-ms"' in allowed)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_LICENCE_WAIT_MS (int, 500) from the patch",
      re.search(r'"env\.MADEIRA_DOCK_LICENCE_WAIT_MS": \{[^}]*"kind": "int"[^}]*"default": "500"[^}]*"sources": \["tools/patch-dock-licence-ask\.py"\]', cat, re.S) is not None)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/session.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/session.c").read_text()
    if P.MARKER in orig:
        print("note: the Dock tree is already patched; the checks below need the pristine pin, so they are skipped")
    else:
        with tempfile.TemporaryDirectory(prefix="madeira-dock-licence-ask-") as td:
            t = Path(td)
            shutil.copytree(dock / "src", t / "src")

            def run(script, *files):
                r = subprocess.run([sys.executable, str(root / script)] + [str(f) for f in files], capture_output=True, text=True)
                return r.returncode, r.stdout + r.stderr

            for patch, files in (("tools/patch-dock-game-scm.py", ["launch.c"]), ("tools/patch-dock-keep-alive.py", ["launch.c"]),
                                 ("tools/patch-dock-steam-readback.py", ["launch.c"]), ("tools/patch-dock-licence-wait.py", ["session.c"]),
                                 ("tools/patch-dock-install-dir-events.py", ["launch.c"]), ("tools/patch-dock-registry-events.py", ["launch.c"]),
                                 ("tools/patch-dock-ceg-service-start.py", ["scm.c"]), ("tools/patch-dock-ceg-retry.py", ["launch.c"])):
                rc, out = run(patch, *[t / "src" / f for f in files])
                check("the workflow's earlier patch %s applies to the copy" % patch, rc == 0, out)
            rc, out = run("tools/patch-dock-ceg-already-prepared.py", t / "src/launch.c", t / "src/validation.c")
            check("the workflow's CEG already-prepared patch applies to the copy", rc == 0, out)
            sc = t / "src/session.c"
            before = sc.read_text()
            rc1, o1 = run("tools/patch-dock-licence-ask.py", sc)
            once = sc.read_text()
            rc2, o2 = run("tools/patch-dock-licence-ask.py", sc)
            check("it applies once after them; a second run says so and changes nothing",
                  rc1 == 0 and rc2 == 0 and "already in" in o2 and sc.read_text() == once and P.MARKER in once, o1 + o2)
            lines = once.splitlines()
            lo = next(i for i, l in enumerate(lines) if l.startswith("int sh_session(HMODULE module, void *engine, const struct sh_api *api,"))
            changed = [i for op in difflib.SequenceMatcher(None, before.splitlines(), lines, autojunk=False).get_opcodes() if op[0] != "equal"
                       for i in range(op[3], max(op[4], op[3] + 1))]
            check("every changed line lies inside sh_session", changed and all(i > lo for i in changed), (lo, changed[:6]))
            bad = t / "bad.c"
            bad.write_text(before.replace("if (entitled || (use_list && now - last_list >= 1000)) {", "if (entitled || (use_list && now - last_list >= 999)) {"))
            r3 = run("tools/patch-dock-licence-ask.py", bad)
            check("a moved anchor refuses the patch and leaves the file alone", r3[0] != 0 and "anchor" in r3[1] and P.MARKER not in bad.read_text(), r3)

            mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                      Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                          if (m / "x86_64-w64-mingw32-clang").exists()), None)
            if mingw:
                b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                    "-static", "-Wl,--strip-all", "-Wl,--no-insert-timestamp", "-o", str(t / "dockhost.exe")]
                                   + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"], capture_output=True, text=True)
                check("the patched host compiles with build/madeira-dock/build.sh's flags, with every Dock patch of the workflow",
                      b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1800:])
            else:
                print("skip the Dock compile: no llvm-mingw at hand")

            # ------------------------------------------------------------------------------ the licence loop on the host
            cc = shutil.which("cc") or shutil.which("clang")

            def cut(text):
                a = text.index("    uint64_t begin = o->now_ms(), online_at = 0, last_probe = 0, last_list = 0, offline_since = 0;\n")
                b = text.index("    if (blips) o->event(\"session-online-blips\"")
                head = text[:text.index("int sh_session(")]
                typedefs = "\n".join(l for l in head.splitlines() if l.startswith("typedef") and "__thiscall" in l)
                enabled = head[head.index("static bool enabled(const char *name)"):]
                enabled = enabled[:enabled.index("\n}\n") + 3]
                return typedefs, enabled, text[a:b]

            harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "validation.h"
#include "probe.h"
#include "client_layout.h"
#define __thiscall
typedef void *HMODULE;
@@TYPEDEFS@@
@@ENABLED@@
/* the scripted Valve client and clock; times are ms from the loop's start */
static uint64_t clock_ms = 100000, t0;
static long signin_at, entitled_at, listed_at, bad_list_until;   /* ms from the start; -1 never */
static uint32_t app_id = 42700;
static char events[8192];
static int launches; static long launch_rel = -1;
static void ev(const char *name, int32_t value) { char b[96]; snprintf(b, sizeof b, "%s=%d@%ld ", name, (int)value, (long)(clock_ms - t0)); strncat(events, b, sizeof events - strlen(events) - 1); }
static uint64_t now_ms(void) { return clock_ms; }
static void sleep_ms(uint32_t ms) { clock_ms += ms; }
static bool logged_on(int32_t user, int32_t pipe) { (void)user; (void)pipe; return signin_at >= 0 && (long)(clock_ms - t0) >= signin_at; }
static bool get_callback(int32_t pipe, struct sh_callback *cb) { (void)pipe; (void)cb; return false; }
static void free_callback(int32_t pipe) { (void)pipe; }
static bool fn_true(void *self) { (void)self; return true; }
static bool fn_subscribed(void *self, uint32_t app) {
    (void)self;
    if (app == 0) return true;
    if (app != app_id) return false;
    long rel = (long)(clock_ms - t0) - (signin_at < 0 ? 0 : signin_at);
    return entitled_at >= 0 && rel >= entitled_at;
}
static int32_t fn_subscriptions(void *self, uint32_t *apps, int32_t cap, bool flag) {
    (void)self; (void)flag; (void)cap;
    long rel = (long)(clock_ms - t0) - (signin_at < 0 ? 0 : signin_at);
    if (bad_list_until == -2 || (bad_list_until >= 0 && rel < bad_list_until)) return -1;
    int32_t n = 0; apps[n++] = 7; apps[n++] = 8;
    if (listed_at >= 0 && rel >= listed_at) apps[n++] = app_id;
    return n;
}
static int sh_launch(HMODULE module, void *engine, void *client_user, const struct sh_api *api, const struct sh_observer *o,
                     int32_t pipe, int32_t user, uint64_t steamid, uint32_t appid, const struct dock_client_layout *layout)
{
    (void)module; (void)engine; (void)client_user; (void)api; (void)o; (void)pipe; (void)user; (void)steamid; (void)appid; (void)layout;
    launches++; launch_rel = (long)(clock_ms - t0);
    return 0;
}
static int offline_session(HMODULE module, void *engine, void *client_user, const struct sh_api *api, const struct sh_observer *o,
                           int32_t pipe, int32_t user, uint64_t id, uint32_t app, const struct dock_client_layout *layout, bool use_list)
{
    (void)module; (void)engine; (void)client_user; (void)api; (void)o; (void)pipe; (void)user; (void)id; (void)app; (void)layout; (void)use_list;
    return 50;
}
static int run_loop(const struct sh_observer *o, const struct sh_api *api, void **v, void *client_user, bool use_list)
{
    HMODULE module = NULL; void *engine = NULL; const struct dock_client_layout *layout = NULL;
    int32_t pipe = 1, user = 1; uint64_t id = 76561197960265729ULL; unsigned long app = app_id;
    bool offline_abi = false; int result = 0;
    (void)module; (void)engine; (void)layout; (void)id; (void)pipe; (void)user; (void)offline_abi;
@@LOOP@@
done:
    return result;
}
int main(int argc, char **argv)
{
    /* argv: signin entitled listed bad_list_until use_list */
    if (argc < 6) return 2;
    signin_at = atol(argv[1]); entitled_at = atol(argv[2]); listed_at = atol(argv[3]); bad_list_until = atol(argv[4]);
    bool use_list = atoi(argv[5]) != 0;
    void *vt[300] = {0};
    vt[4] = (void *)fn_true; vt[6] = (void *)fn_true; vt[181] = (void *)fn_subscribed; vt[182] = (void *)fn_subscriptions;
    void **v = vt;
    struct sh_observer obs = { now_ms, sleep_ms, ev };
    struct sh_api api = {0}; api.logged_on = logged_on; api.get_callback = get_callback; api.free_callback = free_callback;
    t0 = clock_ms;
    int r = run_loop(&obs, &api, v, &v, use_list);
    printf("%d|%d|%ld|%ld|%s\n", r, launches, launch_rel, (long)(clock_ms - t0), events);
    return 0;
}
'''
            outs = {}
            if cc:
                for label, text in (("original", before), ("patched", once)):
                    typedefs, enabled_fn, loop = cut(text)
                    d = t / ("h-" + label)
                    d.mkdir()
                    for f in ("validation.h", "validation.c", "probe.h", "client_layout.h"):
                        shutil.copy(t / "src" / f, d / f)
                    (d / "h.c").write_text(harness.replace("@@TYPEDEFS@@", typedefs).replace("@@ENABLED@@", enabled_fn).replace("@@LOOP@@", loop))
                    b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter",
                                        "-Wno-unused-but-set-variable", "-Wno-unused-variable", "-I", str(d), str(d / "h.c"), str(d / "validation.c"),
                                        "-o", str(d / "h")], capture_output=True, text=True)
                    check("the %s licence loop builds on the host over the scripted client (C11, warnings are errors)" % label, b.returncode == 0, b.stderr[-2500:])
                    if b.returncode == 0:
                        outs[label] = d / "h"
            else:
                print("skip the host build: no C compiler")

            # name: (sign-in ms, entitled ms after it, listed ms after it, list unreadable until, use_list, extra env)
            S = {
                "ready-at-once":            (2000, 0, 0, -1, 1, {}),
                "ready-at-3s":              (2000, 3000, 3000, -1, 1, {}),
                "ready-at-7s":              (2000, 7000, 7000, -1, 1, {}),
                "entitled-never-listed":    (2000, 0, -1, -1, 1, {}),
                "entitled-listed-at-2s":    (2000, 0, 2000, -1, 1, {}),
                "entitled-listed-at-6s":    (2000, 0, 6000, -1, 1, {}),
                "list-unreadable-to-3s":    (2000, 0, 0, 3000, 1, {}),
                "list-unreadable-always":   (2000, 0, 0, -2, 1, {}),
                "never-licensed":           (2000, -1, -1, -1, 1, {}),
                "listed-only-no-list-use":  (2000, -1, 0, -1, 0, {}),
                "entitled-at-2s-no-list":   (2000, 2000, 2000, -1, 0, {}),
                "signin-late-at-50s":       (50000, 0, 0, -1, 1, {}),
                "wait-5000-ready":          (2000, 0, 0, -1, 1, {"MADEIRA_DOCK_LICENCE_WAIT_MS": "5000"}),
                "wait-0-ready":             (2000, 0, 0, -1, 1, {"MADEIRA_DOCK_LICENCE_WAIT_MS": "0"}),
                "wait-bad-values":          (2000, 0, 0, -1, 1, {"MADEIRA_DOCK_LICENCE_WAIT_MS": "abc"}),
                "wait-20000-ready":         (2000, 0, 0, -1, 1, {"MADEIRA_DOCK_LICENCE_WAIT_MS": "20000"}),
            }

            def play(label, name):
                s = S[name]
                import os
                env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_DOCK_")}
                env["MADEIRA_STEAM_HOST_LAUNCH"] = "1"   # the loop goes on to sh_launch (the scripted stub) when ownership is confirmed
                env.update(s[5])
                if not s[4]:
                    env["MADEIRA_DOCK_LIST_ENTITLEMENT"] = "0"
                r = subprocess.run([str(outs[label]), *map(str, s[:4]), str(s[4])], capture_output=True, text=True, env=env, timeout=120)
                parts = r.stdout.strip().split("|", 4)
                return dict(result=int(parts[0]), launches=int(parts[1]), launch=int(parts[2]), end=int(parts[3]), events=parts[4]) if len(parts) == 5 else {"error": r.stdout + r.stderr}

            if len(outs) == 2:
                res = {name: (play("original", name), play("patched", name)) for name in S}
                check("every scenario ran", all("error" not in a and "error" not in b for a, b in res.values()), [n for n, (a, b) in res.items() if "error" in a or "error" in b])
                o, p = {n: a for n, (a, b) in res.items()}, {n: b for n, (a, b) in res.items()}
                signin = lambda n: S[n][0]
                # where today's start works: the patched one works too and never later
                for name in ("ready-at-once", "ready-at-3s", "ready-at-7s", "entitled-listed-at-2s", "list-unreadable-to-3s", "entitled-at-2s-no-list", "signin-late-at-50s"):
                    check("%s: today's start works (result 0, one launch); the patched one works too and never later (%d ms vs %d ms after the sign-in)"
                          % (name, p[name]["launch"] - signin(name), o[name]["launch"] - signin(name)),
                          o[name]["result"] == 0 and o[name]["launches"] == 1 and p[name]["result"] == 0 and p[name]["launches"] == 1
                          and p[name]["launch"] <= o[name]["launch"], (o[name], p[name]))
                check("ready at once: the patched start launches 500 ms after the sign-in (one 20 ms tick), today's 5 s after",
                      500 <= p["ready-at-once"]["launch"] - 2000 < 540 and 5000 <= o["ready-at-once"]["launch"] - 2000 < 5040, (o["ready-at-once"], p["ready-at-once"]))
                check("a licence that arrives at 3 s starts at 3 s (not 5 s); one that arrives at 7 s starts exactly when today's does",
                      3000 <= p["ready-at-3s"]["launch"] - 2000 < 3540 and p["ready-at-7s"]["launch"] == o["ready-at-7s"]["launch"]
                      and p["ready-at-7s"]["events"] == o["ready-at-7s"]["events"], (p["ready-at-3s"], p["ready-at-7s"], o["ready-at-7s"]))
                check("listed only 2 s after being entitled: the patched start sees it at 2 s, today's at 5 s; an unreadable list is waited out",
                      2000 <= p["entitled-listed-at-2s"]["launch"] - 2000 < 2300 and 3000 <= p["list-unreadable-to-3s"]["launch"] - 2000 < 3300, (p["entitled-listed-at-2s"], p["list-unreadable-to-3s"]))
                check("a late sign-in (at 50 s) gets the same early start", 500 <= p["signin-late-at-50s"]["launch"] - 50000 < 540, p["signin-late-at-50s"])
                # where today's start fails: the same code at the same moment, identical events
                for name, code in (("entitled-never-listed", 35), ("entitled-listed-at-6s", 35), ("list-unreadable-always", 36), ("never-licensed", 34),
                                   ("listed-only-no-list-use", 34)):
                    check("%s: today's start fails with %d; the patched one fails with the same code at the same moment, with the identical events"
                          % (name, code), o[name]["result"] == code and p[name]["result"] == code and p[name]["end"] == o[name]["end"]
                          and p[name]["events"] == o[name]["events"] and o[name]["launches"] == 0 and p[name]["launches"] == 0, (o[name], p[name]))
                check("entitled but not listed: 35 comes 5 s after the sign-in, not earlier, in both", 5000 <= o["entitled-never-listed"]["end"] - 2000 < 5100
                      and p["entitled-never-listed"]["end"] == o["entitled-never-listed"]["end"], (o["entitled-never-listed"], p["entitled-never-listed"]))
                check("an unreadable list: 36 comes 5 s after the sign-in, in both", 5000 <= o["list-unreadable-always"]["end"] - 2000 < 5100
                      and p["list-unreadable-always"]["end"] == o["list-unreadable-always"]["end"])
                check("never licensed: both loops run to the same deadline and print the same probe lines (the first at 5 s)",
                      "session-online-subscription-count" in o["never-licensed"]["events"] and p["never-licensed"]["events"] == o["never-licensed"]["events"])
                check("the early answer carries the new report field, only it; an answer from 5 s on has none (identical events)",
                      "session-licence-answer-ms=" in p["ready-at-once"]["events"] and "session-licence-answer-ms" not in o["ready-at-once"]["events"]
                      and "session-licence-answer-ms" not in p["ready-at-7s"]["events"], p["ready-at-once"])
                # the setting
                check("MADEIRA_DOCK_LICENCE_WAIT_MS=5000: exactly today's timing and events (apart from the report field, absent after 5 s)",
                      p["wait-5000-ready"]["launch"] == o["wait-5000-ready"]["launch"] and p["wait-5000-ready"]["events"] == o["wait-5000-ready"]["events"],
                      (o["wait-5000-ready"], p["wait-5000-ready"]))
                check("0 asks at once", p["wait-0-ready"]["launch"] - 2000 < 40, p["wait-0-ready"])
                check("a value that is no number in 0..30000 means 500 ms", 500 <= p["wait-bad-values"]["launch"] - 2000 < 540, p["wait-bad-values"])
                check("20000 waits that long when asked (the player's own choice)", 20000 <= p["wait-20000-ready"]["launch"] - 2000 < 20100, p["wait-20000-ready"])

print("PASS: Dock licence ask (500 ms, nothing fails earlier)" if ok else "FAILED: Dock licence ask")
sys.exit(0 if ok else 1)
