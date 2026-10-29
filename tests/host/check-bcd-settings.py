#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's Settings (app/Madeira/BCDSettings*.swift, docs/BCD-UI-ARCHITECTURE.md).

  1. Every Settings file carries BCD's three licence lines.
  2. The views name no engine type; only BCDSettingsLive.swift does, and tools/ui-preview.py's
     ENGINE_BOUND keeps it out of the preview build. The Xcode project builds every Settings
     file under IDs of Settings' own range (A1000B80-A1000B9F, A2000B80-A2000B9F).
     Feedback (BCDSettingsFeedback.swift, with its live backend in BCDSettingsLive.swift) is
     covered further by tests/host/check-feedback.py.
  3. The preview build compiles (tools/ui-preview.py --build-only; --no-preview skips it).
  4. The controller (BCDSettingsCore.swift compiled and driven on the Mac): down walks every
     section in order, each shown beside the list, then Classic settings; up comes back to
     Done; right or A enters a section at its first control; up and down walk its rows; left
     and right step a value row; left and B come back to the list; B on the list closes
     Settings; upright, the list pushes each section and B pops it.
  5. Words: no string a view draws says "Madeira"; the catalog's words go through
     BCDSettingsText, run here over the whole catalog (keys, titles, notes, choices) with
     none of the old name left, while search still finds the real keys.
  6. About carries the credits word for word: BCDCredit() first, then every MadeiraCredit(...)
     of Library.swift's Settings › Credits, in order, and its notice (NO WARRANTY, the
     source); the preview's sample has the same words.
  7. All settings covers every catalog key: the whole catalog minus Memory & performance's
     keys (RuntimeMemorySyncSettings.featuredKeys, as before), each of which Memory writes.
  8. Calm: no animation that runs by itself (repeatForever, TimelineView, a spinning
     ProgressView), none longer than 0.25 s, no material behind content; every control a
     controller can reach is .bcdFocusable or .bcdSettingsControl.
  9. Nothing of the classic settings screen is left (wave 3, 2026-10-19: it is deleted): no
     notification, no Settings fallback, no list row, no placeholder button; CI runs this check.
 10. Updates (BCDUpdates.swift, run on the Mac with fake answers): tags compare by their numbers
     (v0.1.580 is newer than 0.1.570, 0.1.9 is older than 0.1.10); offline, 404 and unreadable
     answers are one quiet line, never an alert; a release page must be on github.com; Check
     automatically is on unless turned off and checks once per start; the home screen's chip
     shows only while it is on and a newer release exists, and opens Settings › Updates; no
     address is ever drawn as text, and nothing is downloaded.
 11. Nothing stays reachable only through the classic screens (owner, 2026-10-18: they are
     deleted in the cleanup wave): every function of the classic settings form and the
     developer screen's settings has its place here (listed below with the engine call each
     uses), Accounts, JIT and BCD Dock aside (agent C1's sections); no interface picker; the
     classic fallback is gone (it was marked CLEANUP WAVE).

    tests/host/check-bcd-settings.py [--no-preview]
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app" / "Madeira"
PREVIEW = R / "app" / "BCDPreview"
VIEWS = ["BCDSettings.swift", "BCDSettingsCore.swift", "BCDSettingsSections.swift", "BCDSettingsAll.swift",
         "BCDSettingsAbout.swift", "BCDUpdates.swift", "BCDSettingsFeedback.swift"]
LIVE = "BCDSettingsLive.swift"
SAMPLE = PREVIEW / "BCDSettingsPreviewData.swift"
HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
ENGINE_TYPES = ["LibraryModel", "LibraryEntry", "LibraryView", "SteamSignInModel", "MadeiraDockModel", "MadeiraDock",
                "JITCoordinator", "WineProcessBridge", "MadeiraConfig", "LogStore", "LogUpload", "WineMonoModel",
                "WineMonoPin", "InputSettings", "TouchControlsModel", "TouchControlsOverlay", "ControlPresetsModel",
                "ControlPresetStore", "StorageModel", "ConfigCatalog", "ConfigOption", "AutoTune", "EntitlementStatus",
                "GameSession", "ResolutionChoices", "GuestDisplay", "SyncEngine", "RuntimeMemorySyncSettings",
                "BCDCredit", "MadeiraCredit", "BuildStamp", "GamepadInput", "HardwareInput", "PadStickMouse",
                "PadBindings", "ControlAction", "ProMotionIntent", "OnboardingModel", "StikJITHelper",
                "MadeiraMemoryHostTest", "wine_process_is_running", "jit_available_memory", "jit_task_map_range",
                "VCRuntime", "VCRuntimeInstaller", "SaveBackup", "BCDLibraryImport", "FeedbackUpload", "FeedbackCore"]
failures = []


def check(cond, what, detail=""):
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(what)


def read(path):
    return Path(path).read_text()


def code(text):
    """The text without // comments (a // inside a string, like a URL's, cuts the line short:
    harmless for the searches below)."""
    out = []
    for line in text.split("\n"):
        i = line.find("//")
        out.append(line if i < 0 else line[:i])
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


def strings(text):
    return re.findall(r'"((?:[^"\\]|\\.)*)"', text)


views = {name: read(APP / name) for name in VIEWS}
live = read(APP / LIVE)
sample = read(SAMPLE)
lib = read(APP / "Library.swift")
preview_tool = read(R / "tools" / "ui-preview.py")
pbx = read(R / "app" / "Madeira.xcodeproj" / "project.pbxproj")

# ---- 1. headers -------------------------------------------------------------------------
for name in VIEWS + [LIVE]:
    check(read(APP / name).startswith(HEADER), f"{name}: BCD's licence header")
check(sample.startswith(HEADER), "BCDSettingsPreviewData.swift: BCD's licence header")
check(read(R / "tests" / "host" / "check-bcd-settings.py").split("\n")[1:4] == HEADER.replace("// ", "# ").strip().split("\n"),
      "check-bcd-settings.py: BCD's licence header")

# ---- 2. no engine types in the views; ENGINE_BOUND; the Xcode project --------------------
for name, text in list(views.items()) + [("BCDSettingsPreviewData.swift", sample)]:
    found = sorted({t for t in ENGINE_TYPES if re.search(r"\b" + re.escape(t) + r"\b", bare(text))})
    check(not found, f"{name}: names no engine type", "found: " + ", ".join(found))
bound = re.search(r"ENGINE_BOUND = \{([^}]*)\}", preview_tool)
bound = set(re.findall(r'"([^"]+)"', bound.group(1))) if bound else set()
check(LIVE in bound, "ui-preview.py: ENGINE_BOUND keeps BCDSettingsLive.swift out of the preview")
check(not (set(VIEWS) & bound), "ui-preview.py: the Settings views are in the preview build")
check(any(re.search(r"\b" + t + r"\b", bare(live)) for t in ENGINE_TYPES), "BCDSettingsLive.swift is where the engine is")
for name in VIEWS + [LIVE]:
    ref = re.search(r"(A2000B[89A-F][0-9A-F]) /\* " + re.escape(name) + r" \*/ = \{isa = PBXFileReference", pbx)
    build = re.search(r"(A1000B[89A-F][0-9A-F]) /\* " + re.escape(name) + r" in Sources \*/ = \{isa = PBXBuildFile; fileRef = "
                      + (ref.group(1) if ref else "X"), pbx)
    ok = bool(ref and build) and ref.group(1)[-2:] == build.group(1)[-2:] and "A1000B" in build.group(1)
    ok = ok and pbx.count(build.group(1) + " /* " + name + " in Sources */,") == 1 and pbx.count(ref.group(1) + " /* " + name + " */,") == 1
    check(ok and int(ref.group(1)[-2:], 16) <= 0x9F, f"Xcode project builds {name} with Settings' own IDs")

# ---- 3. the preview build ---------------------------------------------------------------
if "--no-preview" in sys.argv:
    print("note: preview build skipped (--no-preview)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-settings-preview-") as out:
        r = subprocess.run([sys.executable, str(R / "tools" / "ui-preview.py"), "--build-only", "--out", out],
                           capture_output=True, text=True)
        check(r.returncode == 0 and (Path(out) / "BCDPreview.app" / "BCDPreview").exists(),
              "the preview app builds with Settings in it (no engine type reached a view)", (r.stdout + r.stderr)[-2000:])
preview_app = read(PREVIEW / "BCDPreviewApp.swift")
check('"BCDSection"' in preview_app and 'BCDSettingsSection(name: String($0.split(separator: "+").first' in preview_app,
      "preview app: -BCDSection opens a section (section+state starts a sample in that state)")
check('screen.partition(".")' in preview_tool and '"-BCDSection"' in preview_tool,
      "ui-preview.py: --screens settings.<section> photographs a section")

# ---- 4 and 5. the controller and the words, driven on the Mac ----------------------------
catalog = read(APP / "ConfigCatalog.generated.swift")
shown = []
for m in re.finditer(r'ConfigOption\(key: "((?:[^"\\]|\\.)*)", title: "((?:[^"\\]|\\.)*)".*?note: "((?:[^"\\]|\\.)*)", '
                     r'choices: \[(.*?)\], sources:', catalog):
    key, title, note, choices = m.groups()
    shown.append({"key": key, "title": json.loads('"' + title + '"'), "note": json.loads('"' + note + '"'),
                  "labels": [json.loads('"' + l + '"') for l in re.findall(r'\("(?:[^"\\]|\\.)*", "((?:[^"\\]|\\.)*)"\)', choices)]})
check(len(shown) == catalog.count("ConfigOption(key:") and len(shown) > 100, f"the catalog's {len(shown)} options read for the word check")

DRIVER = r'''
import Foundation
enum BCDMove { case up, down, left, right }
extension Result { var failureValue: Failure? { if case .failure(let f) = self { return f } else { return nil } } }
var failed = false
func check(_ cond: Bool, _ what: String) {
    print((cond ? "ok   " : "FAIL ") + what)
    if !cond { failed = true }
}
typealias N = BCDSettingsNavigator
let all = BCDSettingsSection.allCases
let list = BCDSettingsID.list
check(all.count == 17 && all.last == .about && all[all.count - 2] == .updates && all.contains(.developer)
      && all.firstIndex(of: .gecko) == all.firstIndex(of: .vcruntime)! + 1,
      "sixteen sections (Developer among them, the HTML engine for installers right after Visual C++), Updates then About last")
check(all.firstIndex(of: .feedback) == all.firstIndex(of: .logs)! + 1 && BCDSettingsSection.feedback.title == "Feedback"
      && BCDSettingsSection(name: "feedback") == .feedback && !BCDSettingsSection.feedback.placeholder,
      "Feedback is a section of its own, right after Logs")

// Side by side: down shows every section in turn, then Classic settings; up returns.
var nav = N(mode: .split, selected: all[0])
var at: String? = list(all[0])
var seen = [all[0]]
for _ in 0..<(all.count + 3) {
    switch nav.move(.down, from: at) {
    case .select(let s): nav.selected = s; at = list(s); seen.append(s)
    case .focus(let id): at = id
    default: break
    }
}
check(seen == all, "split: down walks every section in order and shows each beside the list")
// 2026-10-19: no Classic settings row below the list any more (nothing classic on screen).
check(at == list(all[all.count - 1]), "split: down stops on the last section")
check(nav.back(from: at) == .close, "split: B on the list closes Settings")
var upSeen: [BCDSettingsSection] = []
for _ in 0..<(all.count + 3) {
    switch nav.move(.up, from: at) {
    case .select(let s): nav.selected = s; at = list(s); upSeen.append(s)
    case .focus(let id): at = id
    default: break
    }
}
check(upSeen == Array(all.reversed().dropFirst()), "split: up walks back through every section")
check(at == BCDSettingsID.done, "split: up from the first section reaches Done")
check(nav.move(.down, from: BCDSettingsID.done) == .focus(list(all[0])), "split: down from Done is the list again")
check(nav.back(from: BCDSettingsID.done) == .close, "split: B on Done closes Settings")

// Inside each section.
for s in all {
    let a = BCDSettingsID.control(s, "a"), b = BCDSettingsID.control(s, "b"), c = BCDSettingsID.control(s, "c")
    let n = N(mode: .split, selected: s, order: [a, b, c], adjustable: [b])
    check(n.move(.right, from: list(s)) == .enter(s, first: a), "split: right enters \(s.title) at its first control")
    check(n.move(.down, from: a) == .focus(b) && n.move(.down, from: b) == .focus(c) && n.move(.down, from: c) == .stay,
          "split: down walks \(s.title)'s rows and stops at the last")
    check(n.move(.up, from: c) == .focus(b) && n.move(.up, from: a) == .stay, "split: up walks back and stops at the first")
    check(n.move(.right, from: b) == .step(b, 1) && n.move(.left, from: b) == .step(b, -1), "split: left and right step a value row")
    check(n.move(.left, from: a) == .leave(s) && n.move(.right, from: a) == .stay, "split: left from a row is the list")
    check(n.back(from: c) == .leave(s) && n.back(from: list(s)) == .close, "split: B leaves \(s.title), then closes Settings")
    check(BCDSettingsID.section(a) == s && BCDSettingsID.section(list(s)) == s, "ids: \(s.title)'s controls are its own")
}
// A section with nothing listed yet (wired in later) is left to the nearest-control rule.
check(N(mode: .split, selected: .accounts).move(.right, from: list(.accounts)) == .pass, "split: an unlisted section's controls use the usual rule")

// Upright: the list pushes each section; B pops, then closes.
var up = N(mode: .stacked, selected: all[0])
at = list(all[0])
var walked = [all[0]]
for _ in 0..<(all.count + 3) {
    if case .focus(let id) = up.move(.down, from: at) { at = id; if let s = BCDSettingsID.listSection(id) { walked.append(s) } }
}
check(walked == all && at == list(all[all.count - 1]), "upright: down walks the whole list and stops on the last section")
for s in all {
    let first = BCDSettingsID.control(s, "first")
    var page = N(mode: .stacked, selected: s)
    check(page.move(.right, from: list(s)) == .enter(s, first: nil), "upright: right opens \(s.title)")
    page.pushed = true
    page.order = [first]
    check(page.move(.up, from: first) == .focus(BCDSettingsID.back), "upright: up from \(s.title)'s first row is Back")
    check(page.move(.down, from: BCDSettingsID.back) == .focus(first), "upright: down from Back is the first row")
    check(page.back(from: first) == .leave(s) && page.move(.left, from: first) == .leave(s), "upright: B and left pop \(s.title)")
    check(page.move(.down, from: list(s)) == .focus(first), "upright: a ring left on the list comes to the page")
    page.pushed = false
    check(page.back(from: list(s)) == .close, "upright: B on the list closes Settings")
}

// The words.
check(BCDSettingsText.key("env.MADEIRA_ONBOARDING") == "env.ONBOARDING", "a key is shown without the old name")
check(BCDSettingsText.realKey("env.ONBOARDING", known: ["env.MADEIRA_ONBOARDING"]) == "env.MADEIRA_ONBOARDING",
      "a key typed as shown is written as the real key")
check(BCDSettingsText.realKey("env.FEX_TSOENABLED", known: ["env.MADEIRA_ONBOARDING"]) == "env.FEX_TSOENABLED", "any other key stays as typed")
check(BCDSettingsText.matches("MADEIRA_PROMOTE", key: "env.MADEIRA_PROMOTE", title: "", note: "", category: "")
      && BCDSettingsText.matches("env.PROMOTE", key: "env.MADEIRA_PROMOTE", title: "", note: "", category: ""),
      "search finds an option by its real key and by its shown one")
check(BCDSettingsText.logName("bcd-log.txt") == "BCD's log, this run" && BCDSettingsText.logName("bcd-log.prev.txt") == "BCD's log, the run before",
      "BCD's own logs (bcd-log.txt, bcd-log.prev.txt) are named by what they are")
let data = try! Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let options = try! JSONSerialization.jsonObject(with: data) as! [[String: Any]]
var left: [String] = []
for o in options {
    let words = [o["key"] as! String, o["title"] as! String, o["note"] as! String] + (o["labels"] as! [String])
    for w in words where BCDSettingsText.scrub(w).range(of: "madeira", options: .caseInsensitive) != nil { left.append(w) }
    let key = o["key"] as! String
    if !BCDSettingsText.matches(key, key: key, title: "", note: "", category: "") { left.append("search misses " + key) }
}
check(left.isEmpty, "the whole catalog, as All settings shows it, never says the old name (\(options.count) options)" + (left.isEmpty ? "" : ": \(left.prefix(3))"))

// Updates.
typealias U = BCDUpdateRules
check(U.isNewer("v0.1.580", version: "0.1.570", build: "570"), "updates: v0.1.580 is newer than 0.1.570")
check(!U.isNewer("v0.1.570", version: "0.1.570", build: "570"), "updates: the same release is not newer")
check(!U.isNewer("0.1.9", version: "0.1.10", build: "10") && U.isNewer("0.1.10", version: "0.1.9", build: "9"),
      "updates: numbers compare as numbers (0.1.9 is older than 0.1.10)")
check(U.isNewer("v0.2", version: "0.1.570", build: "570") && !U.isNewer("v0.1", version: "0.1.570", build: "570"),
      "updates: a shorter tag compares with zeros")
check(!U.isNewer("nightly", version: "0.1.570", build: "570"), "updates: a tag without numbers is never newer")
func answer(_ tag: String, _ page: String = "https://github.com/thebcd69/bcd-games/releases/tag/v0.1.580") -> Data {
    Data("{\"tag_name\": \"\(tag)\", \"html_url\": \"\(page)\"}".utf8)
}
if case .success(let r) = U.release(from: answer("v0.1.580"), status: 200) {
    check(r.version == "0.1.580" && r.page.host == "github.com", "updates: the release's version without its v, and its page")
} else { check(false, "updates: a release is read") }
check(U.release(from: Data(), status: 404).failureValue == .noRelease, "updates: 404 (nothing published yet) is a quiet line")
check(U.release(from: Data("<html>".utf8), status: 200).failureValue == .unreadable, "updates: an unreadable answer is a quiet line")
check(U.release(from: answer("v9", "https://example.com/x"), status: 200).failureValue == .unreadable,
      "updates: a release page anywhere but github.com is refused")
check(U.release(from: Data(), status: 500).failureValue == .refused(500), "updates: another status is a quiet line")
let defaults = UserDefaults(suiteName: "bcd-settings-check-\(UUID().uuidString)")!
let checker = BCDUpdateChecker(defaults: defaults)
check(checker.autoCheck, "updates: Check automatically is on unless turned off")
checker.version = "0.1.570"
checker.build = "570"
var asked = 0
checker.fetch = { _ in asked += 1; throw URLError(.notConnectedToInternet) }
await checker.check()
check(checker.state == .failed(BCDUpdateFailure.offline.line) && !checker.showsChip, "updates: offline is one quiet line, no chip")
checker.fetch = { _ in asked += 1; return (Data(), 404) }
await checker.check()
check(checker.state == .failed(BCDUpdateFailure.noRelease.line), "updates: no release yet is one quiet line")
checker.fetch = { _ in asked += 1; return (answer("v0.1.570"), 200) }
await checker.check()
check(checker.state == .upToDate && !checker.showsChip, "updates: the same release is up to date, no chip")
checker.fetch = { _ in asked += 1; return (answer("v0.1.580"), 200) }
await checker.check()
check(checker.state == .updateAvailable && checker.latest?.version == "0.1.580" && checker.showsChip,
      "updates: a newer release shows the chip while checking automatically")
checker.autoCheck = false
check(!checker.showsChip && defaults.object(forKey: BCDUpdateChecker.autoCheckKey) as? Bool == false
      && BCDUpdateChecker.autoCheckKey == "bcd.updates.autoCheck", "updates: the chip only while Check automatically is on; the switch is kept")
asked = 0
checker.checkOnStart()
try? await Task.sleep(nanoseconds: 200_000_000)
check(asked == 0, "updates: no check at start while Check automatically is off")
checker.autoCheck = true
checker.checkOnStart()
checker.checkOnStart()
try? await Task.sleep(nanoseconds: 300_000_000)
check(asked == 1, "updates: one check per start at most")
exit(failed ? 1 : 0)
'''
swiftc = shutil.which("swiftc") or shutil.which("xcrun")
if not swiftc:
    check(sys.platform != "darwin", "swiftc for the controller and word checks")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-settings-core-") as work:
        work = Path(work)
        (work / "main.swift").write_text(DRIVER)
        (work / "catalog.json").write_text(json.dumps(shown))
        updates_src = read(APP / "BCDUpdates.swift")
        start, end = updates_src.index("// MARK: - Rules and checker"), updates_src.index("// MARK: - On screen")
        (work / "updates.swift").write_text("import Foundation\nimport Combine\n" + updates_src[start:end])
        cmd = ([swiftc] if swiftc.endswith("swiftc") else [swiftc, "swiftc"]) + [
            "-o", str(work / "drive"), str(APP / "BCDSettingsCore.swift"), str(work / "updates.swift"), str(work / "main.swift")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        check(r.returncode == 0, "BCDSettingsCore.swift and BCDUpdates.swift's rules compile on their own (Foundation, Combine)", r.stderr[-2000:])
        if r.returncode == 0:
            r = subprocess.run([str(work / "drive"), str(work / "catalog.json")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                print("     " + line if line.startswith("ok") else line)
            check(r.returncode == 0, "the controller walks Settings as it should, and the words hold", r.stdout[-1500:] + r.stderr[-500:])

# ---- 5. no old name in what the views draw -----------------------------------------------
core = views["BCDSettingsCore.swift"]
text_rules = block(core, "enum BCDSettingsText {")
for name, text in views.items():
    body = code(text.replace(text_rules, "") if name == "BCDSettingsCore.swift" else text)
    bad = [s for s in strings(body) if "madeira" in s.lower()]
    check(not bad, f"{name}: no string says the old name", str(bad[:3]))
check("BCDSettingsText.scrub(entry.note)" in views["BCDSettingsAll.swift"] and "BCDSettingsText.key(entry.key)" in views["BCDSettingsAll.swift"]
      and "BCDSettingsText.scrub($0.label)" in views["BCDSettingsAll.swift"],
      "All settings shows the catalog's notes, keys and choices through BCDSettingsText")
check("BCDSettingsText.matches(query, key: entry.key" in views["BCDSettingsAll.swift"]
      and "BCDSettingsText.realKey(newKey" in views["BCDSettingsAll.swift"],
      "All settings searches by shown words and real keys, and adds a shown key as the real one")

# ---- 6. the credits, word for word --------------------------------------------------------
# Wave 3 (2026-10-19): the classic Settings form is deleted, so the reference is the frozen copy of its Credits
# section (tests/fixtures/classic-credits.swift.txt, verbatim from Library.swift at 0b287c31). About must still show
# every credit and the licence notice, word for word.
last = (R / "tests/fixtures/classic-credits.swift.txt").read_text()
credits = re.findall(r'MadeiraCredit\(name: "[^"]*", handle: "[^"]*", role: "[^"]*"\)', last)
notice = re.search(r'Text\("(BCD and Madeira are built on[^"]*)"\)', last)
check(len(credits) >= 8 and notice is not None and "NO WARRANTY" in notice.group(1) and "github.com/thebcd69/bcd-games" in notice.group(1),
      f"the classic Credits (frozen) read: BCDCredit, {len(credits)} contributors, the notice")
about = block(live, "struct BCDAboutCreditsLive: View")
positions = [about.find(c) for c in credits]
check("BCDCredit()" in about and all(p >= 0 for p in positions) and positions == sorted(positions)
      and about.find("BCDCredit()") < min(positions or [0]),
      "About: BCDCredit first, then every contributor of Settings › Credits in the same order, word for word",
      "missing: " + str([c for c, p in zip(credits, positions) if p < 0]))
check(len(re.findall(r"MadeiraCredit\(name:", about)) == len(credits), "About: no contributor added or dropped")
check(notice is not None and f'Text("{notice.group(1)}")' in about, "About: the licence notice word for word (NO WARRANTY, the source)")
check("backend.credits()" in views["BCDSettingsAbout.swift"] and "AnyView(BCDAboutCreditsLive())" in live,
      "About shows the live credits view")
people = re.findall(r'\("([^"]*)", "([^"]*)", "([^"]*)"\)', block(sample, "private struct BCDPreviewCredits: View"))
wanted = [tuple(re.match(r'MadeiraCredit\(name: "([^"]*)", handle: "([^"]*)", role: "([^"]*)"\)', c).groups()) for c in credits]
attribution = re.search(r'static let attribution = "([^"]*)"', block(lib, "struct BCDCredit: View"))
check(people == wanted and notice is not None and notice.group(1) in sample and attribution is not None
      and attribution.group(1) in sample and "A fork of Madeira by Will Faust and its contributors, below." in sample,
      "the preview's credits carry the same words")

# ---- 7. All settings covers every catalog key ---------------------------------------------
featured = re.search(r"static let featuredKeys: Set<String> = \[([^\]]*)\]", lib)
featured = re.findall(r'"([^"]+)"', featured.group(1)) if featured else []
all_live = block(live, "final class BCDAllSettingsLive")
check(re.search(r"let entries: \[BCDConfigEntry\] = ConfigCatalog\.generated\s*\.filter \{ !RuntimeMemorySyncSettings\.featuredKeys\.contains\(\$0\.key\) \}\s*\.map\(BCDAllSettingsLive\.entry\)", all_live) is not None
      and all_live.count(".filter") == 1,
      "All settings lists the whole catalog but Memory's keys, exactly as before")
check("let catalogKeys = ConfigCatalog.keys" in all_live and "let categories = ConfigCatalog.categories" in all_live,
      "All settings knows every catalog key (the file's other lines are listed apart) and every category")
check("MadeiraConfig.set(key, stored)" in all_live and '"[all-settings] \\(key) = \\(stored ?? "(default)")"' in all_live,
      "All settings writes as before: trimmed, empty is out of the file, logged as [all-settings]")
all_view = views["BCDSettingsAll.swift"]
check("backend.entries.filter { matches($0) }" in all_view and "for category in backend.categories" in all_view,
      "All settings shows every entry the search matches, under every category")
mem_live = block(live, "final class BCDMemoryLive")
keys_block = block(live, "private enum BCDLiveKey")
missing = []
for key in featured:
    if key in ("inproc-sync", "env.MADEIRA_FASTSYNC"):
        if "SyncEngine.apply(engine)" not in mem_live:
            missing.append(key)
    elif f'"{key}"' not in keys_block:
        missing.append(key)
check(len(featured) >= 8 and not missing, f"Memory & performance writes each key All settings leaves to it ({len(featured)})",
      "missing: " + ", ".join(missing))

# ---- 8. calm, and every control reachable -------------------------------------------------
for name, text in list(views.items()) + [(LIVE, live)]:
    c = code(text)
    check(not re.search(r"repeatForever|TimelineView|ProgressView\(\s*\)|Material\b|\.material", c),
          f"{name}: nothing animates by itself, no material behind content")
    durations = [float(d) for d in re.findall(r"duration:\s*([0-9.]+)", c)]
    bare = re.findall(r"withAnimation\s*\{|withAnimation\(\s*\)|\.animation\(\.(?:default|spring|easeInOut|easeOut|easeIn|linear)\s*,", c)
    check(all(d <= 0.25 for d in durations) and not bare, f"{name}: every animation is short (at most 0.25 s) and timed",
          f"durations {durations}, untimed {bare}")
    lines = c.split("\n")
    loose = []
    for i, line in enumerate(lines):
        if re.search(r"\b(Button|Menu|TextField)\s*[({]", line):
            ahead = "\n".join(lines[i:i + 45])
            if ".bcdFocusable(" not in ahead and ".bcdSettingsControl(" not in ahead:
                loose.append(f"{i + 1}: {line.strip()[:70]}")
    check(not loose, f"{name}: every control a controller needs is focusable", "; ".join(loose[:4]))

# ---- 9. Classic settings and CI ------------------------------------------------------------
root = views["BCDSettings.swift"]
# Wave 3 (2026-10-19): the classic settings screen is deleted, so the notification that opened it
# (declared in Settings, posted by the app, printed by the preview) is retired with it.
check("OpenClassicSettings" not in root and "openClassicSettings" not in root and "openClassicSettings" not in live
      and "openClassicSettings" not in sample and "func openClassic" not in root,
      "no classic settings notification: not declared, not posted, not printed by the preview")
check('Text("Classic settings")' not in root and "classicRow" not in root,
      "no Classic settings row in BCD's Settings (2026-10-19: nothing classic on screen)")
placeholder = block(views["BCDSettingsSections.swift"], "struct BCDSettingsPlaceholder: View")
check("openClassic" not in placeholder and "classic" not in code(placeholder).lower()
      and "var placeholder: Bool { [.accounts, .jit, .dock].contains(self) }" in views["BCDSettingsCore.swift"]
      and "case .accounts, .jit, .dock: BCDSettingsPlaceholder(section: section)" in root,
      "only Accounts, JIT and BCD Dock are placeholders (the preview's; the app wires them in), and they open nothing classic")
vc_view = block(views["BCDSettingsSections.swift"], "struct BCDVCRuntimeSettings<Backend: BCDVCRuntimeBackend>: View")
check('URL(string: "https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/")' in vc_view
      and '"Accept and download"' in vc_view and "accept Microsoft's license terms" in vc_view
      and all(f'"{t}"' in vc_view for t in ["Included in this build", "Not installed", "Cancel the download",
                                            "Download it again", "Remove the downloaded copy"])
      and "backend.lastError" in vc_view and "BCDProgressBar(" in vc_view and "model.ask(" in vc_view,
      "Visual C++ runtime: what is on the device, Accept and download beside Microsoft's license, progress, "
      "cancel, download again, remove after a confirm, the last error")
controls_view = block(views["BCDSettingsSections.swift"], "struct BCDControlsSettings<Backend: BCDControlsBackend>: View")
check("if backend.pointerLockAvailable" in controls_view and "backend.togglePointerLock()" in controls_view
      and "if backend.mouseNeedsAssistiveTouch" in controls_view and "backend.dismissMouseHint()" in controls_view
      and "AssistiveTouch" in controls_view and "Ctrl+Alt+P" in controls_view,
      "Controls: Capture the pointer (iPad) and the iPhone AssistiveTouch note, from HardwareInput")
screen = read(APP / "BCDControllerScreen.swift")
check('BCDSettingsActionRow(id: id("controller-screen"), title: "Controllers"' in controls_view
      and "model.showControllers()" in controls_view,
      "Controls: a Controllers row opens BCD's controller screen (BCDControllerScreen)")
check("BCDControllerScreen(focus: focus, close: { model.hideControllers() })" in root and "if model.controllersShown {" in root
      and "fullScreenCover" not in block(root, "func showControllers()"),
      "the controller screen shows inside Settings, handed Settings' focus, with Done")
check("cover({ [weak self] in self?.hideControllers() }, layer: BCDSettingsModel.controllersLayer)" in root
      and 'static let controllersLayer = "controllers."' in root
      and '"controllers.mapping"' in screen and '"controllers.watch"' in screen and '"controllers.done"' in screen,
      "a controller can walk the screen's switches: it takes the focus layer \"controllers.\" (the ids of its switches and Done)")
check("if covered != nil { return coveredLayer == nil }" in root and "if let layer = coveredLayer { focus.closeLayer(layer)" in root
      and "if let layer = coveredLayer { focus?.openLayer(layer) }" in root,
      "a page over Settings with its own focus layer gets the D-pad (one without still swallows it); the layer is let go with Settings")
check('BCDHint(button: .a, title: "Select")' in root and 'BCDHint(button: .b, title: "Done"), covered)' in root,
      "the screen's buttons: A selects, B closes it")
updates = read(APP / "BCDUpdates.swift")
updates_view = block(views["BCDSettingsSections.swift"], "struct BCDUpdatesSettings: View")
front = read(APP / "BCDFrontEnd.swift")
chip = block(updates, "struct BCDUpdateChip: View")
check('static let endpoint = URL(string: "https://api.github.com/repos/thebcd69/bcd-games/releases/latest")!' in updates
      and "Authorization" not in updates and "token" not in code(updates).lower(),
      "updates: GitHub's public API for thebcd69/bcd-games, no token")
check(not re.search(r"\.alert\(|\.sheet\(|fullScreenCover|download\(|downloadTask", code(updates) + code(updates_view)),
      "updates: no alert or sheet of its own, nothing downloaded")
shown_text = re.findall(r"(?:Text|Label|Link)\(([^\n]*)", code(chip) + code(updates_view))
check(not [t for t in shown_text if re.search(r"page|absoluteString|html_url|http", t)] and "UIApplication.shared.open(page)" in updates,
      "updates: no address is drawn as text; Go to update opens the page")
check("if checker.showsChip" in chip and "var showsChip: Bool { autoCheck && state == .updateAvailable }" in updates
      and 'Text("Update available")' in chip, "updates: the chip shows only while checking automatically and a newer release exists")
chips = block(front, "private var statusChips: some View")
check("BCDUpdateChip { settingsSection = .updates; showSettings = true }" in chips
      and "section: settingsSection" in front, "updates: the chip sits with the JIT chip and opens Settings at Updates")
check('Label("Go to update"' in updates_view and "checker.checkNow()" in updates_view and "checker.autoCheck = $0" in updates_view
      and "model.go(to: .updates)" in views["BCDSettingsAbout.swift"],
      "updates: Go to update, Check for updates and Check automatically in Settings, reachable from About")
# ---- 11. every classic function has a place ---------------------------------------------
COVERED = [  # (the classic function, where it is now, the code that does it)
    ("Extended logging", "Logs", "input.diagnostics = on"),
    ("Auto-tune", "Logs", "AutoTune.preferred = $0"),
    ("Send Log", "Logs", "LogUpload.send(url"),
    ("Share Log", "Logs", "LogStore.shared.exportableLogs"),
    ("Clear Log", "Developer", "LogStore.shared.clear()"),
    ("the log console", "Developer", "LogStore.shared.logFileURL"),
    ("Pointer mode and speeds", "Controls", "input.mode = .relative"),
    ("Hold the display at its maximum rate", "Graphics", '"[runtime-settings] promote='),
    ("the desktop's Resolution", "Graphics", "write(BCDLiveKey.desktopSize, value)"),
    ("Memory & sync", "Memory & performance", "SyncEngine.apply(engine)"),
    ("Test extension memory", "Memory & performance", "MadeiraMemoryHostTest("),
    ("Memory+ and the entitlements", "Memory & performance", "EntitlementStatus.check()"),
    (".NET Framework", ".NET Framework", "mono.install()"),
    ("Saves: back up", "Storage", "SaveBackup.backup()"),
    ("Saves: restore", "Storage", "SaveBackup.restore(from: url)"),
    ("Add every game in drive_c", "Storage", "BCDLibraryImport.run"),
    ("Storage's caches", "Storage", "model.clear(item)"),
    ("All settings and its search", "All settings", "ConfigCatalog.generated"),
    ("the controller sheet's key mapping", "Controls", "pads.setMapping(on)"),
    ("the controller sheet's input tester", "Developer", "pads.tester.reading"),
    ("Wine Virtual Desktop", "Developer", "BCDLiveID.local(LibraryEntry.desktopID)"),
    ("Credits", "About", "BCDCredit()"),
    ("Visual C++ runtime: download", "Visual C++ runtime", "runtime.install()"),
    ("Visual C++ runtime: cancel", "Visual C++ runtime", "runtime.cancel()"),
    ("Visual C++ runtime: remove", "Visual C++ runtime", "runtime.remove()"),
    ("Visual C++ runtime: state", "Visual C++ runtime", "VCRuntime.shared"),
    ("the pointer-lock button", "Controls", "mouse.togglePointerLock(why:"),
    ("the iPhone AssistiveTouch mouse note", "Controls", "mouse.dismissAssistiveTouchHint()"),
    ("the right stick as a mouse", "Controls", "input.padRightStickMouse = on"),
    ("the mouse's tracking speed", "Controls", "input.sensMouse ="),
]
for what, where, call in COVERED:
    check(call in live, f"classic function covered: {what} -> {where}", call)
mine = "\n".join(code(t) for t in views.values()) + code(live) + code(sample)
check(not re.search(r"\b(BCDInterfacePicker|FrontendChoice|LiquidMetalSetting)\b", mine),
      "no interface picker (only BCD's interface) and no classic chrome switch")
check(not any("CLEANUP WAVE" in t or "BCDSettingsID.classic" in t for t in [*views.values(), live, sample]),
      "the classic fallback is gone: nothing is marked CLEANUP WAVE any more, and the list has no classic focus id")
check("BCDSettingsToggleRow(id: BCDSettingsID.control(.about, \"developer\"), title: \"Developer options\"" in views["BCDSettingsAbout.swift"]
      and "$0 != .developer || developerShown" in root, "Developer shows in the list behind About's Developer options")

workflow = read(R / ".github" / "workflows" / "build-ipa.yml")
check("python3 tests/host/check-bcd-settings.py" in workflow, "CI runs this check")

print("check-bcd-settings:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
