#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Auto-tune (docs/AUTO-TUNE.md): the switch, the hooks, the rules, the fixtures, the engine.

Auto-tune reads a tuned game's session log after the session (never during it), recognises
the failure and tries one known config line at the next start. On by default (Settings ›
Auto-tune, and each game's own switch); turned off it must be as if it did not exist. These checks hold it to that:

Structure (every host):
  - every line outside AutoTune*.swift that names auto-tune is one of the registered hooks,
    and each hook is behind `AutoTune.enabled` (the Settings row, which is the switch itself,
    and the offer's alert binding are the registered exceptions);
  - the switch is a UserDefaults bool read once per run, on when absent; every entry point
    of AutoTune.swift starts with `guard enabled`;
  - nothing of it runs in the Wine process: no native file names it, and AutoTune*.swift
    keeps no timer, observer or display link and reads no environment switch (no catalog
    churn: the settings catalog's scanner finds nothing in them);
  - the Xcode project builds the four files; CI runs this check;
  - the rule table is current (build/tools/gen-autotune-rules.py --check) and sound: unique
    ids, "key = value" lines, keys the settings catalog knows, none of the never-list, no
    key only Swift or only bcd.cfg reads, valid fields, a reason and evidence each; a
    rule for a crash fixed in code (fixedIn) has no lines and names its build;
  - every rule is proved by at least one fixture; every fixture has its .expect and is
    scrubbed (no container path, user name or e-mail address).

Behaviour (swiftc; --require-swift makes its absence a failure):
  - every fixture replayed whole and in 4093- and 7-byte chunks gives its .expect's outcome,
    stage, rules, the game's error boxes and next line; a failure classified from the log
    alone (a killed run) stays a failure with the same rules when the session ended normally;
  - the engine checks (tests/host/autotune_engine_checks.swift): keep or revert, the one
    unchanged retry, stuck, the attempt limit, a new baseline after the user's own change,
    the user's keys, the override file, the scanner across chunk sizes, the outcomes;
  - the app side (tests/host/autotune_glue_checks.swift, AutoTune.swift with stubs): off,
    with a tuned game, a leftover marker and a state file on disk, every entry point leaves
    files, MADEIRA_CFG_GAME, the log and the offer untouched; on, the launch file, the
    marker, the analysis, a killed run's recovery and the question work.

  check-autotune.py [--require-swift] [--bless NAME...]
--bless writes NAME.expect from the current result (review it before committing).
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
FIX = R / "tests/fixtures/autotune"
CORE, GLUE, VIEWS, RULES_SWIFT = (APP / "AutoTuneCore.swift", APP / "AutoTune.swift",
                                  APP / "AutoTuneViews.swift", APP / "AutoTuneRules.generated.swift")
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--bless", nargs="*", default=None)
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + detail) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def code_lines(text):
    """Lines that are not comments."""
    return [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("//")]


# ---- hooks ----------------------------------------------------------------------------
# Every non-comment line outside AutoTune*.swift that names AutoTune, exactly.
HOOKS = {
    # BCD's Settings › Logs: the switch itself, like the old Settings row (one line, so the
    # whole of BCD's settings code names auto-tune nowhere else).
    "BCDSettingsLive.swift": [
        "private let tune = BCDLiveSwitch(read: { AutoTune.preferred }, write: { AutoTune.preferred = $0 }, running: AutoTune.enabled)   // madeira-bcd: auto-tune's switch in BCD's Settings, on by default",
    ],
    # BCD's interface (BCDLiveStore.swift, 2026-10-18): a tuned game's badge, read only while auto-tune is on.
    "BCDLiveStore.swift": [
        'AutoTune.enabled && AutoTune.isOn(windowsPath) && AutoTune.state(windowsPath).status == "found"   // madeira-bcd: BCD\'s interface shows a tuned game; off, nothing is read',
    ],
    "LibraryBCD.swift": [
        "if AutoTune.enabled { AutoTune.exportLayer(for: entry) }",
    ],
    "Library.swift": [
        "if AutoTune.enabled { AutoTune.sessionBegan(entry, remember: remember, dockWithoutEntry: dock != nil && !remember) }",
        "if AutoTune.enabled { autoTuneSessionEnded() }",
        "guard AutoTune.enabled else { return }",
        "AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: sawProcess, quitRequested: quitRequested,",
        "if AutoTune.enabled { AutoTune.recoverUnfinishedSession() }",
        # Wave 3 (2026-10-19): the classic Settings row (AutoTuneSettingsSection) and Game details section
        # (AutoTuneGameSection) are deleted with their screens; BCDSettingsLive's switch and
        # BCDGamePageLive's section (both listed below) replaced them.
    ],
    "ContentView.swift": [
        # One modifier: ContentView's chain of alerts is at the type checker's limit (build of
        # 2026-10-16: "unable to type-check this expression in reasonable time").
        ".modifier(AutoTuneAlert())   // madeira-bcd: auto-tune's result (AutoTuneViews.swift); never shown while it is off",
    ],
    # BCD's game page (BCDGamePage.swift): the game's switch, where its search stands, what it
    # tried, its report, keeping the found lines and Undo, each only while Settings › Auto-tune
    # is on (and the switch only for a game auto-tune can tune).
    "BCDGamePageLive.swift": [
        "let tuned = AutoTune.enabled && AutoTune.eligible(entry) ? (on: AutoTune.isOn(path), state: AutoTune.state(path), most: AutoTuneEngine.maxAttempts) : nil",
        "private static func said(_ outcome: String) -> String { AutoTune.enabled ? AutoTuneOffer.outcomeText(outcome) : outcome }",
        "if AutoTune.enabled { AutoTune.setOn(path, on) }",
        "if AutoTune.enabled { AutoTune.reset(path) }",
        "let kept = AutoTune.enabled ? AutoTune.state(path).kept : []",
        "if AutoTune.enabled { AutoTune.reset(path) }",
        "guard let state = AutoTune.enabled ? AutoTune.state(path) : nil else { return nil }",
    ],
}
for swift in sorted(APP.glob("*.swift")):
    if swift.name.startswith("AutoTune"):
        continue
    found = [l for l in code_lines(swift.read_text()) if "AutoTune" in l]
    want = HOOKS.get(swift.name, [])
    check(f"{swift.name}: only the registered auto-tune hooks ({len(want)})", sorted(found) == sorted(want),
          "unexpected: %s\n     missing: %s" % ([l for l in found if l not in want], [l for l in want if l not in found]))
lib = (APP / "Library.swift").read_text()
fn = lib[lib.index("private func autoTuneSessionEnded() {"):]
fn = fn[:fn.index("\n    }\n")]
check("Library.autoTuneSessionEnded starts with the gate and reads the session facts only behind it",
      code_lines(fn)[1] == "guard AutoTune.enabled else { return }")
views = VIEWS.read_text()
alert = views[views.index("struct AutoTuneAlert: ViewModifier {"):]
check("the offer's alert shows only while auto-tune is on and after the exit report and any restart notice",
      "Binding(get: { AutoTune.enabled && offers.offer != nil && library.error == nil && library.restartNotice == nil\n"
      "                       && gate.mayShow(offers.offer != nil) }" in alert)
# 2026-10-24 (the owner): the alert hangs on ContentView, under every page and sheet; shown while a game's page was open, iOS took
# the page down and the library's first screen came back whatever was pressed. It now waits while anything is presented over the
# root screen, comes up once nothing is, and stays up until answered.
gate = views[views.index("final class AutoTuneAlertGate: ObservableObject {"):]
check("the alert waits while a page, sheet or other alert is presented over the root screen, and stays up once shown",
      "guard pending else { showing = false; return false }" in gate and "if showing { return true }" in gate
      and "return root.presentedViewController == nil" in gate and "if Self.nothingPresented() {" in gate)
check("nothing runs while it waits: it looks again when one of BCD's pages or sheets closes",
      "BCDPresentation.closed = { AutoTuneAlertGate.shared.lookAgain() }" in gate
      and "guard AutoTuneOffers.shared.offer != nil, !showing else { return }" in gate)
comp = (APP / "BCDComponents.swift").read_text()
check("BCDPresentation.didClose looks again a moment after a close, only when someone waits",
      "guard let closed else { return }" in comp and "DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { closed() }" in comp)
hooks = {"BCDGamePage.swift": ["content.fullScreenCover(item: $game, onDismiss: BCDPresentation.didClose)"],
         "BCDFrontEnd.swift": ["onDismiss: { installButtons(); BCDPresentation.didClose() }"],
         "BCDLiveFrontEnd.swift": [".fullScreenCover(isPresented: $onboarding.presented, onDismiss: BCDPresentation.didClose)",
                                   ".sheet(item: sheetShown, onDismiss: BCDPresentation.didClose)",
                                   ".fullScreenCover(item: pageShown, onDismiss: BCDPresentation.didClose)"]}
for name, wants in hooks.items():
    text = (APP / name).read_text()
    check(f"{name}: its pages and sheets call BCDPresentation.didClose when they close", all(w in text for w in wants))
check("its Close BCD is the only way an attempt restarts the app (no relaunch by itself)",
      alert.count("exit(0)") == 1 and "Button(\"Close BCD\")" in alert and "exit(" not in GLUE.read_text()
      and views.count("exit(") == 1)

glue = GLUE.read_text()
check("the switch is a UserDefaults bool read once per run, on when absent",
      "static let enabled: Bool = storedOn(globalKey)" in glue
      and "UserDefaults.standard.object(forKey: key) == nil || UserDefaults.standard.bool(forKey: key)" in glue)
check("the game's switch is on when absent and a stored off is kept",
      "[String: Bool])?[windowsPath.lowercased()] ?? true" in glue and "d[windowsPath.lowercased()] = on\n" in glue)
for name in ("exportLayer", "sessionBegan", "sessionEnded", "recoverUnfinishedSession", "answer"):
    i = glue.index(f"static func {name}(")
    body = code_lines(glue[glue.index("{", i) + 1:])
    check(f"AutoTune.{name} starts with `guard enabled`", body[0].startswith("guard enabled"), body[0])
check("the offer is published only by the analysis", glue.count("AutoTuneOffers.shared.offer =") == 2)
# Round 624 (Ghost of Tsushima JKJA9Q/VQ9SBA): a memory kill with nothing left to try names the game's own graphics settings, before the export hint.
stuck = glue[glue.index('case "stuck", "gave-up":'):glue.index('case "known":', glue.index('case "stuck", "gave-up":'))]
check("a stuck/gave-up memory kill tells the player to lower the game's own graphics (round 624)",
      'if report.outcome == "killed" {' in stuck and "iOS closed the game at its memory limit. Set the game's own graphics to the lowest preset" in stuck
      and stuck.index("lowest preset") < stuck.index("Export the auto-tune report"))
bridge = (APP / "WineProcessBridge.m").read_text()
check("WineProcessBridge.m still echoes the game's lines the way AutoTune.echo expects them (an attempt whose "
      "lines are missing from its log counts for nothing)",
      'fprintf(stderr, "[game-cfg]   %s\\n", t.UTF8String);' in bridge and
      'fprintf(stderr, "[madeira-env] game %s=%s\\n", k.UTF8String, v.UTF8String);' in bridge)

# ---- nothing in the Wine process, nothing resident, no catalog churn --------------------
# Our own native code, and the patches we apply to Wine, FEX and DXMT (third-party headers are
# left out: Wine's DirectShow strmif.h has a tuner's own "AutoTune").
native = []
NATIVE_DIRS = ("app/Madeira", "build/ntdll-unix", "build/wineios-drv", "build/win32u-unix", "build/wineserver",
               "build/crypto-unix", "build/madeira-d3d12", "build/d3d12core", "build/dxmt-ios", "build/dxmt-d3d11",
               "build/dxmt-dxgi", "build/dxmt-nvapi", "build/fex-ios", "build/fex-arm64ec", "build/fex-wow64",
               "build/hidpad", "build/madsync", "build/tftrace", "build/faultrep", "build/stub-dlls",
               "build/rppairing-ios", "build/madeira_cfg.h", "madeira-d3d12/src", "madeira-dock/src")
for base in NATIVE_DIRS:
    d = R / base
    if not d.exists():
        continue
    for p in ([d] if d.is_file() else d.rglob("*")):
        if p.suffix in (".m", ".mm", ".c", ".h", ".cpp", ".hpp") and p.is_file():
            try:
                if re.search(r"(?i)auto-?tune", p.read_text(errors="ignore")):
                    native.append(str(p.relative_to(R)))
            except OSError:
                pass
for p in sorted((R / "tools").glob("patch-*.py")):
    if re.search(r"(?i)auto-?tune", p.read_text(errors="ignore")):
        native.append(str(p.relative_to(R)))
check("no native source or native patch names auto-tune (nothing of it runs in the Wine process)", not native, str(native[:5]))
own = "\n".join(p.read_text() for p in (CORE, GLUE, VIEWS))
resident = [w for w in ("Timer", "NotificationCenter", "addObserver", "DispatchSource", "CADisplayLink", "publish(every", "LogTail")
            if w in own]
check("AutoTune*.swift keeps nothing running during a session (no timer, observer, display link)", not resident, str(resident))
spec = importlib.util.spec_from_file_location("catalog", R / "build/tools/gen-config-catalog.py")
cat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cat)
churn = [m.group(0) for p in (CORE, GLUE, VIEWS) for pat in (cat.CFG, cat.ENV) for m in pat.finditer(p.read_text())]
check("the settings catalog's scanner finds no option in AutoTune*.swift", not churn, str(churn))
check("AutoTune*.swift reads no environment variable", "getenv(" not in own)

pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
for f in ("AutoTune.swift", "AutoTuneCore.swift", "AutoTuneViews.swift", "AutoTuneRules.generated.swift"):
    check(f"the Xcode project builds {f}", pbx.count(f"/* {f} in Sources */") == 2 and pbx.count(f"path = {f};") == 1)
CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "run: python3 tests/host/check-autotune.py --require-swift" in CI.read_text())

# ---- rules ----------------------------------------------------------------------------
gen = subprocess.run([sys.executable, str(R / "build/tools/gen-autotune-rules.py"), "--check"], capture_output=True, text=True)
check("AutoTuneRules.generated.swift is current", gen.returncode == 0, gen.stdout + gen.stderr)
spec = importlib.util.spec_from_file_location("autotune_rules", R / "build/tools/autotune_rules.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rules = mod.RULES
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
known = set(re.findall(r'ConfigOption\(key: "([^"]+)"', catalog)) | set(cat.OVERLAY)
# (cat.OVERLAY: build/tools/gen-config-catalog.py always emits these, so a key added there is in the catalog the full tree
# regenerates; the committed copy may lag until then, as it does for every new key, 2026-10-21.)
# Read by Swift from the game's own file only, or only from bcd.cfg: a layer line would do nothing.
NOT_FROM_LAYER = {"metalfx-upscale", "fps-limit", "env.MADEIRA_PAD_MODE", "pool", "wx", "usd-time", "d3d9", "metal-validation"}
LINE = re.compile(r"^([A-Za-z0-9._-]+) = (\S.*)$")
FIELDS = {"id", "lines", "priority", "all", "any", "none", "outcomes", "session", "api", "ramMinMB", "ramMaxMB",
          "minStage", "maxStage", "imageMaxMB", "exeMaxMB", "exeMinMB", "memoryMinPct", "upgrade", "listUpgrade", "listUpgradeFailure", "noRetry", "reason",
          "evidence", "fixedIn", "noFix", "evaluateWhenRan", "ranOnly"}
ids = [r["id"] for r in rules]
check("rule ids are unique", len(ids) == len(set(ids)))
for r in rules:
    bad = []
    if set(r) - FIELDS:
        bad.append("unknown fields %s" % sorted(set(r) - FIELDS))
    if "fixedIn" in r:
        # A crash only a change in Madeira fixes: no lines, the build that fixed it.
        if r.get("lines") != [] or not isinstance(r["fixedIn"], int) or r["fixedIn"] < 1 or r.get("upgrade") or r.get("noFix"):
            bad.append("a fixedIn rule has \"lines\": [], a build number and no upgrade")
    elif r.get("noFix"):
        # A failure no setting and no known build changes: no lines, no build, no upgrade.
        if r.get("lines") != [] or r["noFix"] is not True or r.get("upgrade") or r.get("listUpgrade") or r.get("noRetry"):
            bad.append("a noFix rule has \"lines\": [], noFix true and no upgrade or retry flag")
    elif not r.get("lines"):
        bad.append("no lines")
    if "evaluateWhenRan" in r and (r["evaluateWhenRan"] is not True or not r.get("noFix")):
        # Round 624 (RETRO-2 RQ1): only a noFix rule may speak for a session scored ran/quit (it changes the text, never a setting).
        bad.append("evaluateWhenRan is true and only on a noFix rule")
    if "ranOnly" in r and (r["ranOnly"] is not True or r.get("evaluateWhenRan") is not True):
        # Round 624 (Fallout 4 voices): ranOnly narrows an evaluateWhenRan rule to sessions that played.
        bad.append("ranOnly is true and only on an evaluateWhenRan rule")
    if r.get("exeMinMB") is not None and r.get("exeMaxMB") is not None and r["exeMinMB"] > r["exeMaxMB"]:
        bad.append("exeMinMB above exeMaxMB")
    for k in ("memoryMinPct",):
        if r.get(k) is not None and not (isinstance(r[k], int) and 1 <= r[k] <= 100):
            bad.append("%s is a percentage" % k)
    for line in r.get("lines", []):
        m = LINE.match(line)
        if not m:
            bad.append("not 'key = value': %r" % line)
            continue
        k = m.group(1)
        if k not in known:
            bad.append("%s is not in the settings catalog" % k)
        if k in mod.NEVER:
            bad.append("%s is on the never-list (%s)" % (k, mod.NEVER[k]))
        if k in NOT_FROM_LAYER:
            bad.append("%s is not read from the game's layer" % k)
    if not r.get("all") or any(not s.get("has") or any(not h for h in s["has"]) for s in r.get("all", []) + r.get("any", []) + r.get("none", [])):
        bad.append("an empty signal")
    if set(r.get("outcomes", [])) - {"crashed", "killed", "hung", "exited-early"}:
        bad.append("unknown outcome")
    if r.get("session") not in (None, "library", "dock") or r.get("api") not in (None, "d3d11", "d3d12"):
        bad.append("bad session/api")
    if not r.get("reason") or not r.get("evidence"):
        bad.append("no reason or evidence")
    if not isinstance(r.get("priority"), int):
        bad.append("priority")
    check(f"rule {r['id']} is sound", not bad, "; ".join(bad))

# ---- fixtures -------------------------------------------------------------------------
logs = sorted(FIX.glob("*.log"))
expects = {p.stem for p in FIX.glob("*.expect")}
check(f"{len(logs)} fixtures, each with its .expect", logs and {p.stem for p in logs} == expects,
      "without: %s; orphaned: %s" % (sorted({p.stem for p in logs} - expects), sorted(expects - {p.stem for p in logs})))
# users\NAME, also in Wine's own spelling of a path (doubled backslashes: L"C:\\users\\<user>\\AppData"): the name is the part that
# may not stay; tools/autotune-fixture.py's scrub reads both spellings the same way.
SCRUB = re.compile(r"/var/mobile/Containers/(?:Data|Shared)/[^ ]*[0-9A-F]{8}-|(?i:\\users\\{1,2}(?!public\b|mobile\b|<user>)[^\\/ ]+)|"
                   r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|/Users/(?!<user>)")
dirty = [p.name for p in logs if SCRUB.search(p.read_text(errors="ignore"))]
check("fixtures are scrubbed (no container path, user name or e-mail address)", not dirty, str(dirty))
# A player's install folder named like a web site (a download site's name) never reaches a public fixture; tools/autotune-fixture.py
# writes C:\Games\... instead (round 624). Same pattern as the tool's SITE_FOLDER.
SITE_FOLDER = re.compile(r"(?i)(?:(?<=\\)|(?<=[^:/]/))[^\\/\s\"'<>|:*?]+\.(?:com|org|to|ru|me|io|cc|xyz|info|site|games|vip|club)(?=[\\/])")
site = [p.name for p in logs if SITE_FOLDER.search(p.read_text(errors="ignore"))]
check("fixtures carry no install folder named like a web site", not site, str(site))
proved = set()
for p in FIX.glob("*.expect"):
    proved.update(json.loads(p.read_text()).get("matched", [])[:1])
    proved.update(json.loads(p.read_text()).get("matched", []))
check("every rule is matched by at least one fixture", set(ids) <= proved, "unproved: %s" % sorted(set(ids) - proved))

# ---- behaviour --------------------------------------------------------------------------
swiftc = shutil.which("swiftc")
if not swiftc:
    if args.require_swift:
        check("Swift compiler available", False)
    else:
        print("SKIP: Swift compiler unavailable locally; macOS CI must run this check with --require-swift")
    print("PASS: auto-tune" if ok else "FAILED: auto-tune")
    sys.exit(0 if ok else 1)


def build(tmp, name, sources):
    exe = Path(tmp) / name
    b = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-o", str(exe)] + [str(s) for s in sources],
                       capture_output=True, text=True)
    check(f"{name} compiles", b.returncode == 0, b.stderr[-3000:])
    return exe if b.returncode == 0 else None


KEYS = ("outcome", "stage", "matched", "dialogs", "status", "next")
with tempfile.TemporaryDirectory(prefix="madeira-autotune-") as tmp:
    replay = build(tmp, "replay", [CORE, RULES_SWIFT, R / "tests/host/autotune_replay.swift"])
    if replay:
        if args.bless is not None:
            for name in args.bless:
                exp_path = FIX / (name + ".expect")
                prior = json.loads(exp_path.read_text()) if exp_path.exists() else {"end": {}, "note": ""}
                out = json.loads(subprocess.run([str(replay), "--end", json.dumps(prior["end"]), str(FIX / (name + ".log"))],
                                                capture_output=True, text=True).stdout)
                prior.update({k: out[k] for k in KEYS})
                exp_path.write_text(json.dumps(prior, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
                print(f"blessed {name}: {out['outcome']}, stage {out['stage']}, matched {out['matched']}, next {out['next']}")
        for p in logs:
            exp = json.loads((FIX / (p.stem + ".expect")).read_text())
            runs = {}
            for chunk in (0, 4093, 7):
                r = subprocess.run([str(replay), "--chunk", str(chunk), "--end", json.dumps(exp["end"]), str(p)],
                                   capture_output=True, text=True)
                runs[chunk] = json.loads(r.stdout) if r.returncode == 0 else {"error": r.stderr}
            got = runs[0]
            same = all(runs[c].get(k) == got.get(k) for c in (4093, 7) for k in KEYS + ("facts", "monoMissing"))
            diff = {k: (exp.get(k), got.get(k)) for k in KEYS if exp.get(k) != got.get(k)}
            # Wine Mono missing (the game page's hint): only a fixture whose .expect says so may show it.
            if bool(exp.get("monoMissing")) != bool(got.get("monoMissing")):
                diff["monoMissing"] = (bool(exp.get("monoMissing")), bool(got.get("monoMissing")))
            check(f"{p.stem}: {got.get('outcome')}, stage {got.get('stage')}, next {got.get('next') or '-'}",
                  not diff and same, "expected/got: %s; chunk sizes agree: %s" % (diff, same))
            # A crash read from the log alone (the app never came back) reads the same when the
            # session ended normally. A memory kill or a hang exists only on the first path.
            if exp.get("end", {}).get("killed") and exp["outcome"] in ("crashed", "exited-early") and exp["matched"]:
                r = subprocess.run([str(replay), "--end", "{}", str(p)], capture_output=True, text=True)
                alt = json.loads(r.stdout)
                check(f"{p.stem}: a normal end gives the same rules ({alt['outcome']})",
                      alt["outcome"] in ("crashed", "hung", "exited-early") and alt["matched"] == exp["matched"])
    engine = build(tmp, "engine", [CORE, RULES_SWIFT, R / "tests/host/autotune_engine_checks.swift"])
    if engine:
        r = subprocess.run([str(engine)], capture_output=True, text=True)
        check("the engine checks pass", r.returncode == 0, "\n".join(l for l in r.stdout.splitlines() if not l.startswith("ok"))[-3000:])
    glue_exe = build(tmp, "madeira-autotune-glue-check",
                     [GLUE, CORE, RULES_SWIFT, R / "tests/host/autotune_glue_stubs.swift", R / "tests/host/autotune_glue_checks.swift"])
    if glue_exe:
        for mode in ("off", "on", "default"):
            t = Path(tmp) / mode
            for d in ("home", "configs", "docs"):
                (t / d).mkdir(parents=True)
            env = dict(os.environ, HOME=str(t / "home"), CFFIXED_USER_HOME=str(t / "home"),
                       AUTOTUNE_TEST_CONFIGS=str(t / "configs"), AUTOTUNE_TEST_DOCS=str(t / "docs"),
                       AUTOTUNE_TEST_LOG=str(t / "docs/bcd-log.txt"),
                       AUTOTUNE_TEST_CRASH=str(FIX / "rift-apart-2026-10-15-23-02-21.log"),
                       AUTOTUNE_TEST_SILENT=str(FIX / "synthetic-silent-end.log"),
                       AUTOTUNE_TEST_INSTALLS=str(FIX / "gta-v-legacy-installs-only-2026-10-21.log"),
                       AUTOTUNE_TEST_INSTALLS_FAILED=str(FIX / "saints-row-the-third-installs-only-2026-10-21.log"))
            extra = {"on": ["-madeira.autotune", "YES"], "off": ["-madeira.autotune", "NO"]}.get(mode, [])
            r = subprocess.run([str(glue_exe), mode] + extra, capture_output=True, text=True, env=env, timeout=300)
            check(f"the app side with auto-tune {mode}", r.returncode == 0,
                  "\n".join(l for l in r.stdout.splitlines() if not l.startswith("ok"))[-3000:] + r.stderr[-1000:])

print("PASS: auto-tune" if ok else "FAILED: auto-tune")
sys.exit(0 if ok else 1)
