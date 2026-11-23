#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Settings > Recommended settings: the catalog of the recommended list the app is using (round 707, the owner's request).

Behaviour (every host with swiftc; --require-swift makes a missing swiftc a failure):
  - app/Madeira/BCDRecommendedCatalog.swift (the pure model: stores, search, filter, groups, which build has a line) and
    app/Madeira/RecommendedCatalogAdapter.swift (the list's own types into the model, and which file is the list in use) compiled with the
    app's list code (GameRecommendationsList.swift, GameRecommendations.swift, GameRecommendationsSource.swift) and run by
    tests/host/recommended_catalog_checks.swift over the committed recommended/recommendations.json. Python works out the stores and the switches
    of every rule by itself and hands them over, so the Swift side is held to a second reading of the same rules; the lines this build
    writes are held to what the app itself writes (RecommendationRule.recommendation) for every rule, ten builds and both device sizes.

Source (every host):
  1. Licence headers; the model and the adapter are Foundation only and name no UI or engine type; the page names no engine type and writes
     nothing (no setting, no default, no log line); the live side is the only one that touches the list in use.
  2. Wiring: the Settings section (after Feedback and before All settings), the backend member with its default, the live backend in make(),
     the page's rows reachable by a controller, B leaves a game for the list, the list is lazy and its order is given whole.
  3. The model is built once per list in use, off the main thread; the preview build leaves the live and adapter files out.
  4. The files are in the Xcode project; CI runs this check (told, not failed, while the workflow does not yet).

  check-recommended-catalog.py [--require-swift] [--preview]
"""
import argparse
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
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--preview", action="store_true", help="also build the preview app (tools/ui-preview.py --build-only)")
args = ap.parse_args()
failures = 0
notes = []


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    """The text without comments, so a word in a comment counts for nothing."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("///") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


def bare(text):
    """Code without comments or string contents: what names a type."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', code(text))


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
PY_HEADER = "# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)\n# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n"
CORE, ADAPTER, VIEW, LIVE = "BCDRecommendedCatalog.swift", "RecommendedCatalogAdapter.swift", "BCDRecommendedCatalogView.swift", "BCDRecommendedCatalogLive.swift"
src = {n: (APP / n).read_text() for n in (CORE, ADAPTER, VIEW, LIVE)}
settings = (APP / "BCDSettings.swift").read_text()
settings_core = (APP / "BCDSettingsCore.swift").read_text()
settings_live = (APP / "BCDSettingsLive.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
checks_dir = R / "tests/host"

# ---- 1. licence, layering --------------------------------------------------------------------------------------
for name in (CORE, ADAPTER, VIEW, LIVE):
    check(src[name].startswith(HEADER), f"{name}: BCD's licence header")
for path in ("tests/host/recommended_catalog_checks.swift",):
    check((R / path).read_text().startswith(HEADER), f"{path}: BCD's licence header")
check((R / "tests/host/check-recommended-catalog.py").read_text().splitlines()[1:4] == PY_HEADER.splitlines(), "this check: licence header")
core, adapter, view, live = (code(src[n]) for n in (CORE, ADAPTER, VIEW, LIVE))
check(re.findall(r"^import (\w+)", core, re.M) == ["Foundation"], "the model imports Foundation only", re.findall(r"^import (\w+)", core, re.M))
check(re.findall(r"^import (\w+)", adapter, re.M) == ["Foundation"], "the adapter imports Foundation only", re.findall(r"^import (\w+)", adapter, re.M))
ENGINE = ["LibraryModel", "LibraryEntry", "MadeiraConfig", "LogStore", "GameRecommendations", "RecommendationsStore", "RecommendationsFile",
          "GameRecommendationList", "LogUpload", "UIDevice", "UIApplication", "UIPasteboard", "FileManager", "URLSession", "ConfigCatalog"]
for name in (CORE, VIEW):
    named = sorted({w for w in ENGINE if re.search(r"\b" + w + r"\b", bare(src[name]))})
    check(not named, f"{name} names no engine type", ", ".join(named))
check("SwiftUI" not in core and "UIKit" not in core, "the model knows no SwiftUI or UIKit")
check(re.search(r"(?i)\bmadeira\b", "\n".join(re.findall(r'"((?:[^"\\]|\\.)*)"', code(src[CORE]) + code(src[VIEW])))) is None,
      "no string the page draws says Madeira")
for name, body in ((VIEW, bare(src[VIEW])), (LIVE, bare(src[LIVE]))):
    bad = [w for w in ("MadeiraConfig", ".set(", "setValue(", "LogStore", "removeObject", "write(to", "createFile", "removeItem") if w in body]
    check(not bad, f"{name} writes nothing: no setting, default, file or log line", ", ".join(bad))
check("UIPasteboard.general.string = text" in live and live.count("UIPasteboard") == 1, "the live side's only act is the clipboard (a line or a note, when asked)")
check(re.search(r"\.animation\(|withAnimation\(|repeatForever|TimelineView|\.blur\(|Material", view) is None, "the page animates nothing and draws no blur or material")

# ---- 2. wiring -------------------------------------------------------------------------------------------------
cases = re.search(r"enum BCDSettingsSection: String, CaseIterable, Identifiable \{\s*case (.*?)\n", settings_core).group(1).split(", ")
check(cases.index("recommended") == cases.index("feedback") + 1 and cases.index("all") == cases.index("recommended") + 1,
      "Settings: Recommended settings sits between Feedback and All settings", str(cases))
check('case .recommended: return "Recommended settings"' in settings_core and 'case .recommended: return "Every game BCD has a list for"' in settings_core
      and 'case .recommended: return "checklist"' in settings_core, "Settings: a title, a summary and a symbol (literals: BCDSettingsCore.swift compiles on its own)")
check('static let title = "Recommended settings"' in src[CORE] and 'static let summary = "Every game BCD has a list for"' in src[CORE],
      "the section is called Recommended settings")
check("var recommended: (any BCDRecommendedBackend)? = nil" in settings and "case .recommended: BCDRecommendedSettings(backend: model.backends.recommendedBackend)" in settings,
      "Settings: the backend member (nil by default, so the preview's make() is untouched) and the page")
check("recommended: BCDRecommendedLive()" in settings_live, "the live make() passes the live backend")
check("#if BCD_PREVIEW" in src[VIEW] and "return BCDRecommendedSample.backend" in src[VIEW]
      and src[VIEW].index("#if BCD_PREVIEW") > src[VIEW].index("var recommendedBackend"), "the preview build gets a sample, the app none")
ids = set(re.findall(r'control\("([^"]+)"\)', src[VIEW]))
check({"search", "filter", "back", "notes"} <= ids and "static func ruleID(_ id: String) -> String { BCDSettingsID.control(.recommended, \"rule.\" + id) }" in src[VIEW]
      and 'control("line.' not in src[VIEW] and 'id: "line.\\(index)"' in src[VIEW],
      "every control has a focus id (search, filter, a game, back, a line, the notes)", str(sorted(ids)))
controls = len(re.findall(r"\.bcdSettingsControl\(", view))
check(controls >= 5 and "BCDSettingsActionRow(id: control(\"notes\")" in view,
      f"the search, the filter, a game, back and a line are controller controls ({controls}), and so is the notes row")
check("BCDSettingsSectionBack.shared.inner" in view and "{ openRule = nil; return true }" in view and "BCDSettingsSectionBack.shared.inner = nil" in view,
      "B leaves a game for the list (BCDSettingsSectionBack), and the list for Settings' own list")
check("LazyVStack" in view and ".transformPreference(BCDSettingsOrderKey.self)" in view, "the list is lazy and hands the controller its whole order")
check("focus.focused = new == nil ? old.map(Self.ruleID) : Self.control(\"back\")" in view and "guard focus.controllerActive else { return }" in view,
      "a controller's ring follows: into a game's first control when it opens, back to the game's row when it closes")
check("proxy.scrollTo(Self.topID" in view and "proxy.scrollTo(Self.ruleID(old)" in view, "opening a game shows its top, going back shows where the list was")
check("model.read(" in view and "rule.notes.joined" in view, "the notes open in Settings' reader (a controller scrolls them)")
check("BCDSettingsText.scrub(line.text)" in view and "backend?.copy(text)" in view and "copy(line.text, id: id)" in view,
      "lines show without the old name and copy as they are")

# ---- 3. built once, off the main thread; preview ------------------------------------------------------------------
check("Task.detached(priority: .userInitiated) { BCDRecommendedCatalogSource.load(active: active) }" in live
      and "if let built, built.key == key { return built.catalog }" in live and 'let key = "\\(active.origin.rawValue)/\\(active.version)/\\(active.rules.count)"' in live,
      "the live backend builds the catalog off the main thread, once per list in use (a newer list has another key)")
check("running" in live and "runningKey == key" in live, "two visits at once share one build")
bound = set(re.findall(r'"([^"]+\.swift)"', tool))
check(LIVE in bound, "ui-preview.py: the live file stays out of the preview build")
check(not ADAPTER.startswith("BCD"), "the adapter is not a BCD*.swift file, which the preview build would take in")
for name in (CORE, VIEW):
    check(name.startswith("BCD") and name not in bound, f"{name} is built into the preview")

# ---- 4. the project, CI ------------------------------------------------------------------------------------------------
for name in (CORE, ADAPTER, VIEW, LIVE):
    check(project.count(f"/* {name} in Sources */") == 2 and project.count(f"path = {name};") == 1, f"{name} is built by the Xcode project")
if "python3 tests/host/check-recommended-catalog.py" in workflow:
    check(True, "CI runs this check")
else:
    notes.append(".github/workflows/build-ipa.yml does not run this check yet (the step is in the hand-back)")

# ---- behaviour -------------------------------------------------------------------------------------------------------------
data = json.loads((R / "recommended/recommendations.json").read_text())
EPIC_NOTE = re.compile(r"\bEpic (?:Games )?(?:Store|copy|copies|version)\b|\bfrom (?:the )?Epic\b|\bEpic'?s? (?:Store|copy|copies)\b|\bEpic/", re.I)


def stores(rule):
    match = rule.get("match") or {}
    programs = match.get("programs", [])
    out = set()
    if match.get("steamAppIDs") or match.get("steamFolders") or any(p.get("steam") is True for p in programs):
        out.add("steam")
    if any(p.get("steam") is not True for p in programs):
        out.add("drmFree")
    head = rule["id"].replace("-", " ") + " " + rule["title"]
    notes_ = rule.get("notes", [])
    if re.search(r"\bepic\b", head, re.I) or any(EPIC_NOTE.search(n) for n in notes_):
        out.add("epic")
    if re.search(r"\bgog\b", head, re.I) or any(re.search(r"\bgog\b", n, re.I) for n in notes_):
        out.add("gog")
    return out


def switches(rule):
    s = rule.get("switches", {})
    out = []

    def onoff(v):
        return "On" if v else "Off"
    for key, label in (("avx", "AVX"), ("nvidia", "Report an NVIDIA GPU"), ("wineVCRT", "Wine's C++ runtime"), ("fastSync", "Fast synchronization"),
                       ("semaphoreFastPath", "Fast semaphore waits")):
        if key in s:
            out.append(f"{label}={onoff(s[key])}")
    if "fpsMode" in s:
        out.append("FPS limit=" + {1: "60", 3: "30", 4: "40", 0: "Display maximum", 2: "Uncapped"}.get(s["fpsMode"], str(s["fpsMode"])))
    if "resolution" in s:
        out.append("Resolution=" + ("This screen's shape at 720 lines" if s["resolution"] == "screen@720" else s["resolution"].replace("x", " × ")))
    if "display" in s:
        out.append("Aspect & scaling=" + {"fit": "Fit", "fill": "Fill", "stretch": "Stretch", "aspect": "Aspect"}.get(s["display"], s["display"]))
    return out


expected = {"rules": len(data["rules"]), "version": data["version"], "generated": data["generated"],
            "stores": {k: [r["id"] for r in data["rules"] if k in stores(r)] for k in ("steam", "epic", "gog", "drmFree")},
            "switches": {r["id"]: switches(r) for r in data["rules"] if switches(r)}}
check(all(len(expected["stores"][k]) > 0 for k in expected["stores"]) and len(expected["stores"]["gog"]) >= 3 and len(expected["stores"]["epic"]) >= 10,
      "the list has rules in every store (Steam %d, Epic %d, GOG %d, DRM-free / any copy %d)" % tuple(len(expected["stores"][k]) for k in ("steam", "epic", "gog", "drmFree")))
swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-catalog-") as t:
        tmp = Path(t)
        (tmp / "expected.json").write_text(json.dumps(expected))
        (tmp / "stubs.swift").write_text('''
import Foundation
final class LogStore {
    static let shared = LogStore()
    enum Level { case info, error }
    func log(_ message: String, level: Level = .info) {}
}
struct ConfigOption { let key: String; let category: String }
enum ConfigCatalog { static let generated: [ConfigOption] = [] }
''')
        shutil.copy(R / "tests/host/recommended_catalog_checks.swift", tmp / "main.swift")
        exe = tmp / "run"
        b = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(exe)]
                           + [str(APP / n) for n in ("GameRecommendations.swift", "GameRecommendationsList.swift", "GameRecommendationsSource.swift", CORE, ADAPTER)]
                           + [str(tmp / "stubs.swift"), str(tmp / "main.swift")], capture_output=True, text=True)
        check(b.returncode == 0, "the model, the adapter, the app's list code and the checks compile on the host", b.stderr[-2500:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600,
                               env=dict(os.environ, BCD_RECS_JSON=str(R / "recommended/recommendations.json"), BCD_RECS_EXPECTED=str(tmp / "expected.json")))
            lines = r.stdout.splitlines()
            bad = [l for l in lines if l.startswith("FAIL") or l.startswith("     ")]
            summary = [l for l in lines if l.startswith("recommended catalog:")]
            for l in lines:
                if l.strip().startswith("build "):
                    print("      " + l.strip())
            check(r.returncode == 0 and summary, summary[0] if summary else "the catalog's rules", "\n      ".join(bad[:14]) + r.stderr[-400:])

if args.preview or os.environ.get("BCD_CHECK_PREVIEW"):
    out = Path(tempfile.mkdtemp(prefix="bcd-catalog-preview-"))
    b = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", str(out)], capture_output=True, text=True)
    check(b.returncode == 0, "the preview app builds without the engine, with the catalog page and its sample", (b.stdout + b.stderr)[-1500:])
    shutil.rmtree(out, ignore_errors=True)

for n in notes:
    print("NOTE: " + n)
print("check-recommended-catalog: " + ("PASS" if failures == 0 else f"{failures} FAILED"))
sys.exit(0 if failures == 0 else 1)
