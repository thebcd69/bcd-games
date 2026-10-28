#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The performance HUD (round 603; docs/BCD-UI-ARCHITECTURE.md; design: the Winlator-family study, section 3): Off, four finished layouts
(1 Pulse, 2 Strip, 3 Tune, 4 Report) and a Custom one, the frame-time ring that feeds it and the session summary, and where it sits.

Behaviour (every host with swiftc / cc; --require-swift makes a missing swiftc a failure):
  - app/Madeira/BCDHUDCore.swift compiled with tests/host/bcd_hud_checks.swift: the layouts and their words, an old overlay becoming
    the same readings (nothing changes on a working game), what is stored, the frame arithmetic (60, 19 and 1 fps, the lows, the graph),
    the colours, the rows of every layout against the design, and the placement on a phone held sideways, upright, a 4:3 window, a
    near-square one and a resized window (only the window's size and safe area decide; nothing names a device);
  - app/Madeira/BCDFrameRing.c compiled with tests/host/bcd_frame_ring_test.c under the address and undefined-behaviour sanitizers and
    the thread sanitizer: the ring, the histogram, the stalls, many presenting threads at once, and what one present costs.

Source (every host):
  1. Licence headers on every new file; the core names no UI or engine type, the views and the menu no engine type.
  2. The present path (tools/patch-winemetal-frame-ring.py): applies to the pinned DXMT once and again as "already patched", sits
     after the frame-cap patch in the workflow, is a weak import, and the inserted block compiles; the ring itself has no lock, no
     allocation and no log line.
  3. Nothing runs while the HUD is off (its sampler exists only while a layout is on; what a layout does not show is not read; the GPU
     meter and the battery monitor go off with it); it takes no touch except while the player moves it; the window's hit test reads only the
     HUD's frame, which is zero otherwise.
  4. Placement and layout read the window's size and safe area as they are now and nothing else: no device model, idiom or fixed
     screen size, and they are read again when the window changes.
  5. The hooks in older files are the ones listed (Library, ContentView, the session layer and its menu, Settings, the game page),
     the old overlay's setting carries over, and the developer interface's overlay is untouched.
  6. The files are in the Xcode project, the live files are left out of the preview build, CI runs this check and the patch step.

  check-bcd-hud.py [--require-swift] [--require-preview]
"""
import argparse
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
ap.add_argument("--require-preview", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    """The text without comments (whole-line and trailing //), so a word in a comment counts for nothing."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("///") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


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
CORE, VIEW, MENU, LIVE = "BCDHUDCore.swift", "BCDHUDView.swift", "BCDHUDMenu.swift", "BCDHUDLive.swift"
RING_C, RING_H = "BCDFrameRing.c", "BCDFrameRing.h"
src = {n: (APP / n).read_text() for n in (CORE, VIEW, MENU, LIVE, RING_C, RING_H)}
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()
library = (APP / "Library.swift").read_text()
content = (APP / "ContentView.swift").read_text()
session = (APP / "BCDSession.swift").read_text()
sessionlive = (APP / "BCDSessionLive.swift").read_text()
patch_text = (R / "tools/patch-winemetal-frame-ring.py").read_text()

# ---- 1. licence, layering --------------------------------------------------------------------------------------
for name in (CORE, VIEW, MENU, LIVE, RING_C, RING_H):
    check(src[name].startswith(HEADER), f"{name}: BCD's licence header")
for path in ("tools/patch-winemetal-frame-ring.py", "tests/host/bcd_hud_checks.swift", "tests/host/bcd_frame_ring_test.c", "tests/host/check-bcd-hud.py"):
    check(HEADER.replace("// ", "# ").split("\n")[0] in (R / path).read_text() or HEADER.split("\n")[0] in (R / path).read_text(), f"{path}: BCD's licence header")
core = code(src[CORE])
check(re.findall(r"^import (\w+)", core, re.M) == ["Foundation", "CoreGraphics"], "the core imports Foundation and CoreGraphics only", re.findall(r"^import (\w+)", core, re.M))
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryController", "TouchControlsModel", "GameProfile", "MadeiraConfig", "LogStore", "MetalHostView",
          "DockStartScreen", "GamepadInput", "ProMotionIntent", "FrameLimit", "MetalBackedView", "CPUMeter", "madeira_", "winios_", "wine_", "bcd_frame_",
          "UIDevice", "UIScreen", "UIApplication"]
for name in (CORE, VIEW, MENU):
    body = code(src[name])
    named = sorted({w for w in ENGINE if re.search(r"\b" + re.escape(w) if not w.endswith("_") else re.escape(w), body)})
    check(not named, f"{name} names no engine type", ", ".join(named))
check("UIKit" not in core and "SwiftUI" not in core, "the core knows no UIKit or SwiftUI")
for name in (VIEW, MENU, CORE):
    body = code(src[name])
    bad = [w for w in (".blur(", "Material", "repeatForever", "TimelineView", "ProgressView(", "Timer(", "Timer.", ".glassEffect") if w in body]
    check(not bad, f"{name}: no blur, material, spinner, timer or animation that runs on its own", ", ".join(bad))
check(not re.search(r'"[^"\n]*\bMadeira\b[^"\n]*"', code(src[VIEW]) + code(src[MENU]) + code(src[LIVE])), "no Madeira in what the HUD shows")
check(not re.search(r"(?i)auto-?tune", "\n".join(src[n] for n in (RING_C, RING_H)) + patch_text),
      "the ring and its patch name no auto-tune (tests/host/check-autotune.py keeps native files free of it)")

# ---- 2. the present path ----------------------------------------------------------------------------------------
ring = code(src[RING_C])
check(not [w for w in ("malloc", "calloc", "pthread_mutex", "pthread_cond", "printf", "fprintf", "dprintf", "syslog", "os_log", "NSLog", "usleep", "sleep(")
           if w in ring],
      "the ring allocates, locks, sleeps and logs nothing")
push = block(ring, "void bcd_frame_ring_push_at(uint64_t t) {")
check(push.count("atomic_fetch_add_explicit") == 3 + 0 or push.count("atomic_fetch_add_explicit") >= 2, "a present takes a handful of atomics")
check("memory_order_seq_cst" not in ring and "atomic_thread_fence" not in ring, "no sequentially consistent operation or fence on the present path")
check("weak_import" in patch_text and "if (bcd_frame_ring_push)" in patch_text, "the call is a weak import: a link without the app's ring still links")
check("python3 tools/patch-winemetal-frame-ring.py" in workflow, "CI runs the frame ring patch")
steps = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", workflow)
check("tools/patch-winemetal-frame-ring.py" in steps and steps.index("tools/patch-winemetal-frame-ring.py") > steps.index("tools/patch-winemetal-fps-cap.py"),
      "... after the frame-cap patch, which also edits the present call")
check("python3 tests/host/check-bcd-hud.py" in workflow, "CI runs this check")
check("#include \"BCDFrameRing.h\"" in (APP / "Madeira-Bridging-Header.h").read_text(), "Swift reads the ring through the bridging header")

dxmt_src = None
pin = subprocess.run(["git", "ls-tree", "HEAD", "dxmt"], cwd=R, capture_output=True, text=True).stdout.split()
bare = Path.home() / "madeira-local/kaynak/dxmt.git"
tmp = tempfile.TemporaryDirectory(prefix="bcd-hud-dxmt-")
base = Path(tmp.name)
if (R / "dxmt/src/winemetal/unix/winemetal_unix.c").is_file():
    # Check the pin, including in a checkout where the build patches ran.
    (base / "dxmt").mkdir()
    a = subprocess.Popen(["git", "-C", str(R / "dxmt"), "archive", "HEAD", "src"], stdout=subprocess.PIPE)
    t = subprocess.run(["tar", "-x", "-C", str(base / "dxmt")], stdin=a.stdout)
    a.wait()
    if a.returncode == 0 and t.returncode == 0:
        dxmt_src = base / "dxmt"
elif len(pin) >= 3 and bare.is_dir():
    (base / "dxmt").mkdir()
    a = subprocess.Popen(["git", "-C", str(bare), "archive", pin[2], "src"], stdout=subprocess.PIPE)
    t = subprocess.run(["tar", "-x", "-C", str(base / "dxmt")], stdin=a.stdout)
    a.wait()
    if a.returncode == 0 and t.returncode == 0:
        dxmt_src = base / "dxmt"
if dxmt_src is None:
    print("SKIP: no dxmt sources (the submodule is not checked out and the local bare repo is missing): the patch is not applied")
else:
    cwd = base
    first = subprocess.run([sys.executable, str(R / "tools/patch-winemetal-frame-ring.py")], cwd=cwd, capture_output=True, text=True)
    second = subprocess.run([sys.executable, str(R / "tools/patch-winemetal-frame-ring.py")], cwd=cwd, capture_output=True, text=True)
    check(first.returncode == 0 and "patched" in first.stdout and "already" not in first.stdout, "the patch applies to the pinned DXMT", first.stdout + first.stderr)
    check(second.returncode == 0 and "already patched" in second.stdout, "... and says 'already patched' the second time", second.stdout + second.stderr)
    wm = (cwd / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()
    fn = block(wm, "static inline void madeira_log_present_cadence(const char *path, double after) {")
    check(fn.count("bcd_frame_ring_push") == 3 and fn.index("atomic_fetch_add_explicit(&g_madeira_present_count") < fn.index("bcd_frame_ring_push"),
          "the call is inside madeira_log_present_cadence, after the present is counted, so every present of every API passes it")
    check(wm.count("bcd_frame_ring_push();") == 1, "and nowhere else")
    snippet = fn[fn.index("{   /* madeira-bcd frame ring"):]
    snippet = snippet[:snippet.index("\n  }") + 4]
    cfile = base / "snippet.c"
    cfile.write_text("void f(void) {\n" + snippet + "\n}\n")
    cc = subprocess.run(["cc", "-fsyntax-only", "-Wall", "-Werror", str(cfile)], capture_output=True, text=True)
    check(cc.returncode == 0, "the inserted block compiles as C (a weak import in a block)", cc.stderr)
    # The whole chain, as the workflow runs it, twice
    env = dict(os.environ, DXMT_SRC_DIR=str(dxmt_src))
    chain = subprocess.run([sys.executable, str(R / "tests/host/check-dxmt-patch-chain.py")], cwd=R, env=env, capture_output=True, text=True)
    check(chain.returncode == 0, "the DXMT patch chain still applies in the workflow's order, twice", (chain.stdout + chain.stderr)[-800:])
tmp.cleanup()

# ---- 3. nothing while off, no touch ---------------------------------------------------------------------------
live = code(src[LIVE])
sampler = block(live, "@MainActor final class BCDHUDLive {")
start = block(sampler, "func start() {")
stop = block(sampler, "func stop() {")
check("Timer(timeInterval: interval" in sampler and "timer.tolerance = interval * 0.2" in sampler and sampler.count("RunLoop.main.add(") == 1,
      "the sampler's one timer is scheduled by `schedule`, with tolerance, on the main run loop")
check("guard timer == nil else { return }" in start and "schedule()" in start, "start() makes the timer once")
check("timer?.invalidate()" in stop and "timer = nil" in stop and "madeira_gpu_meter_enable(0)" in stop and "isBatteryMonitoringEnabled = false" in stop
      and "feed.plan = nil" in stop and "frames.removeAll()" in stop, "stop() ends the timer, the GPU meter and the battery monitor and drops the frames")
check("if needs.contains(.cpu) {" in sampler and "if gpuOn { sampleGPU" in sampler and "if needs.contains(.jit) {" in sampler
      and "if needs.contains(.swap) {" in sampler and "if needs.contains(.gpuMemory) {" in sampler and "if needs.contains(.lows) {" in sampler
      and "if needs.contains(.graph) {" in sampler, "the CPU, GPU, JIT, swap, GPU-memory, lows and graph are read only for a layout that shows them")
check("let wantGPU = needs.contains(.gpu)" in sampler and "madeira_gpu_meter_enable(wantGPU ? 1 : 0)" in sampler,
      "the GPU meter (a completion handler per command buffer) is on only while a GPU reading is shown")
check("interval = needs.contains(.graph) ? 0.25 : 0.5" in sampler, "text twice a second, the graph four times, nothing faster")
check("if plan != lastPlan || force {" in sampler and "feed.plan = plan" in sampler, "the view is told only when the plan changed")
live_session = block(sessionlive, "@MainActor final class BCDLiveSession: ObservableObject, BCDSessionHUDActions {")
check("if running && !model.launching && BCDHUDState.shared.setup.layout.shown { startOverlay() } else { stopOverlay() }" in live_session
      and "hudLive?.stop()" in block(live_session, "private func stopOverlay() {") and "hud.performance = nil" in block(live_session, "private func stopOverlay() {"),
      "the HUD exists only while a layout is on; Off drops it")
check("let live = BCDHUDLive()" in live_session and "stopOverlay()" in block(live_session, "func disappeared() {"), "and it goes with the session's layer")
host = block(code(src[VIEW]), "struct BCDHUDHost: View {")
check(".allowsHitTesting(feed.editing)" in host, "the HUD takes no touch unless the player is moving it")
check("guard feed.editing else { return .zero }" in host and ".onDisappear { report(.zero) }" in host,
      "the frame the window's hit test reads is zero except while it is moved, and zero when the HUD goes")
check("library.performanceRect.contains(point)" in content and "library.performance && library.performanceRect" not in content,
      "ControlsWindow lets a touch through to the game everywhere but the HUD's frame (zero unless moved)")
check("BCDDraggable(key: \"bcdPerformanceOverlay\"" not in session, "the layer no longer holds the old draggable overlay plate")
check("if hud.starting == nil, let performance = hud.performance {" in block(session, "struct BCDSessionLayer: View {"),
      "the layer draws the HUD only after the starting screen, and only when there is one")
check("sampler.start()" not in live_session and "BCDOverlaySampler(kind" not in sessionlive,
      "a library session no longer starts the old sampler; the developer interface's overlay (BCDOverlaySampler()) is the one left")

# ---- 4. size and safe area, nothing else ----------------------------------------------------------------------------
for name in (CORE, VIEW, MENU, LIVE):
    body = code(src[name])
    bad = [w for w in ("userInterfaceIdiom", "UIDevice.current.model", "UIDevice.current.name", "utsname", "UIScreen.main.bounds", "UIScreen.main.nativeBounds",
                       "UIScreen.main.scale", "iPhone", "iPad", "Dynamic Island", "hasNotch") if w in body]
    check(not bad, f"{name} decides nothing from a device model, idiom or the screen's fixed size", ", ".join(bad))
check("let viewport: CGSize" in host and "let insets: EdgeInsets" in host and "BCDHUDScreenClass.of(viewport)" in host,
      "the host takes the window's size and its safe area from the layer, and classes the window by its size alone")
check("BCDSessionInsets.of(geo)" in block(session, "struct BCDSessionLayer: View {"), "the layer reads the window's insets as they are (the live window, not a table)")
check(".onChange(of: viewport) { _, _ in stable = .zero }" in host and ".onChange(of: available)" in host and "feed.relayout()" in host,
      "a window of another size forgets the placement it was chosen with and plans the rows again, with nothing restarted")
check("feed.layoutRevision &+= 1" in sampler and "picture != lastPicture" in sampler,
      "the picture moving (a fold, a resize, a turn) draws the HUD again, so it keeps clear of it")
check("static func windowSize() -> CGSize" in sampler and "scene?.windows.first(where: \\.isKeyWindow)" in sampler and "BCDHUDLive.windowSize()" in sessionlive,
      "the menu asks the foreground window for its size, not the device's screen")
check("static func roomy(_ viewport: CGSize) -> Bool { min(viewport.width, viewport.height) >= 600 }" in src[CORE], "'roomy' is a rule on the window's short side")
check("UIScreen.main.maximumFramesPerSecond" in src[LIVE] and src[LIVE].count("UIScreen.main") == 1,
      "the only screen property the live side reads is the refresh rate it prints (not a size)")
check("static func place(for" not in src[CORE] and "phoneLandscape" not in src[CORE] and "padLandscape" not in src[CORE], "no phone or pad class is left in the placement")

# ---- 5. hooks in older files ------------------------------------------------------------------------------------
check(library.count("BCDHUDState.shared.sessionBegan(entry)") == 1 and library.count("BCDHUDState.shared.save(into: &entry)") == 1
      and library.count("var hud: BCDHUDSetup?") == 1, "Library.swift: the entry keeps an optional HUD, read when a session starts and written when the player changed it")
check("func save(into entry: inout LibraryEntry) {\n        if dirty || entry.hud != nil { entry.hud = setup }" in code(src[LIVE]),
      "an entry that never had a HUD and was never changed is not rewritten")
check("func change(_ next: BCDHUDSetup, why: String) {" in live and "LibraryModel.shared.performance = next.layout.shown" in live,
      "a change in the menu keeps the old overlay switch in step, so an older reading of the entry agrees")
for fn_name in ("toggle(_ row: String, _ on: Bool)", "choose(_ row: String, _ value: String)", "slide(_ row: String, _ value: Double)", "answer(_ row: String, _ answer: String)"):
    body = block(sessionlive, "    func " + fn_name + " {")
    check("if BCDHUDMenu.owns(row) { return applyHUD(" in body, f"the in-game menu's {fn_name.split('(')[0]} hands the HUD's rows to BCDHUDMenu")
check("out += BCDHUDMenu.sections(BCDHUDState.shared.setup, screen: hudScreen)" in sessionlive and "case BCDHUDMenu.customPage: return BCDHUDMenu.page(id, BCDHUDState.shared.setup)" in sessionlive,
      "the Display tab has the HUD's rows and the Custom page opens from them")
old = code(sessionlive)
check('"overlay.fields"' not in old and "model.overlayFields" not in old, "the old 'Shows' multi-select is gone from the menu")
desk = block(old, "@MainActor final class BCDOverlaySampler {")
check("ProMotionIntent.apply(mode: madeira_get_vsync_locked())" in desk and '"[present] count=' in desk and "madeira_set_vsync_locked(next)" in desk and "libraryReadings" not in desk,
      "the developer interface's overlay keeps its switches and its [present] line; the library branch of the sampler is gone")
check("moved: @MainActor (BCDHUDPlace, BCDHUDScreenClass) -> Void" in code(src[VIEW]), "a drop says which kind of window it was dropped on")
check("everyGame" in (APP / "BCDSettingsLive.swift").read_text() or "hudEveryGame" in (APP / "BCDSettingsLive.swift").read_text(), "Settings › Graphics has the choice for every game")
check("hudEveryGame" in (APP / "BCDSettingsSections.swift").read_text() and 'BCDSettingsID.control(.graphics, "hud")' in (APP / "BCDSettingsSections.swift").read_text(),
      "... its row is in the Graphics page")
check("BCDHUDMigration.resolve(stored: entry.hud, performance: entry.performance, fields: entry.overlayFields," in code(src[LIVE]),
      "a game's own HUD, else its old overlay switch and fields, else the choice for every game")

# ---- 6. the project and the preview ---------------------------------------------------------------------------------
for name in (CORE, VIEW, MENU, LIVE, "BCDSessionCore.swift", "BCDSessionRecorderLive.swift", "BCDScreenshotLive.swift", RING_C):
    check(project.count(f"/* {name} in Sources */") == 2 and project.count(f"path = {name};") == 1, f"{name} is built by the Xcode project")
check(project.count(f"path = {RING_H};") == 1, f"{RING_H} is in the Xcode project")
check(re.search(r'ENGINE_BOUND\s*\|=\s*\{[^}]*"BCDHUDLive\.swift"[^}]*"BCDSessionRecorderLive\.swift"[^}]*"BCDScreenshotLive\.swift"', tool) is not None,
      "the live files are left out of the preview build")
check(all(f'"overlay-{v}"' not in tool for v in ("1",)) and '"overlay"' in tool, "the preview tool photographs the overlay screens (overlay-1 ... overlay-edit are -BCDTab variants)")
preview = (R / "app/BCDPreview/BCDPreviewSession.swift").read_text()
check("BCDPreviewSessionSamples.hudFeed(spec)" in preview and "ingame-menu-hud-custom" in preview, "the preview app draws every layout, the Custom one, an old overlay carried over, the move state and the menu's HUD rows")

# ---- behaviour --------------------------------------------------------------------------------------------------
swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-hud-") as t:
        exe = Path(t) / "run"
        b = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(exe), str(APP / CORE), str(R / "tests/host/bcd_hud_checks.swift")],
                           capture_output=True, text=True)
        check(b.returncode == 0, "the core and its checks compile on the host", b.stderr[-1500:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            lines = [l for l in r.stdout.splitlines() if l.startswith(("ok   ", "FAIL"))]
            bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL") or l.startswith("     ")]
            check(r.returncode == 0 and lines, f"the HUD's rules: {sum(l.startswith('ok') for l in lines)} checks", "\n      ".join(bad[:12]) + r.stderr[-300:])

cc = shutil.which("cc")
if not cc:
    check(not args.require_swift, "cc on the host (the ring)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-ring-") as t:
        for label, flags in (("address + undefined-behaviour sanitizers", ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]),
                             ("thread sanitizer", ["-fsanitize=thread"])):
            exe = Path(t) / ("ring-" + label.split()[0])
            b = subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I" + str(APP)] + flags +
                               ["-o", str(exe), str(APP / RING_C), str(APP / "BCDPauseGate.c"), str(R / "tests/host/bcd_frame_ring_test.c"), "-lpthread"], capture_output=True, text=True)
            if b.returncode != 0 and "sanitize" in b.stderr:
                print(f"SKIP: the {label} is not available here")
                continue
            check(b.returncode == 0, f"the ring compiles with {label}", b.stderr[-800:])
            if b.returncode == 0:
                r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
                bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
                note = [l for l in r.stdout.splitlines() if l.startswith("note:")]
                check(r.returncode == 0 and not bad, f"the ring's tests pass under the {label}", "\n      ".join(bad) + r.stderr[-600:])
                for l in note:
                    print("      " + l)

# ---- the preview build (--require-preview) --------------------------------------------------------------------------
if args.require_preview or os.environ.get("BCD_CHECK_PREVIEW"):
    out = Path(tempfile.mkdtemp(prefix="bcd-hud-preview-"))
    b = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", str(out)], capture_output=True, text=True)
    check(b.returncode == 0, "the preview app builds without the engine, with the HUD's views", (b.stdout + b.stderr)[-1500:])
    shutil.rmtree(out, ignore_errors=True)

print("check-bcd-hud: " + ("PASS" if failures == 0 else f"{failures} FAILED"))
sys.exit(0 if failures == 0 else 1)
