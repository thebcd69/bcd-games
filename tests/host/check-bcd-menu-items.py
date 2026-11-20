#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The in-game menu items of round 604: Pause, Windows, Task Manager, magnifier, picture filter and flip, gestures help, live-log tools with
Wine debug-channel presets, vibration and touch tick, touch controls that hide and take a colour, "a touch that misses the controls", the
controller's way of opening the menu, "Save these settings for this game", frame generation and the cooling switch.

Every one of them is a menu choice or a menu action, and a game whose player does none of them runs exactly as before. Checks:
  1. BCD's licence header on every new file, this check and its harnesses.
  2. Layers: the plain files (values, rules, words) import Foundation, CoreGraphics or SwiftUI only and name no engine type; the files that
     drive the engine are in ui-preview.py's ENGINE_BOUND; the Xcode project builds every file once.
  3. Off means nothing: no timer, observer, window or call exists until the player turns the item on; the hooks in older files are single
     lines that read one flag or one stored value, and the defaults equal what BCD always did.
  4. Wiring: every row the menu draws has its handler; the session start and end hooks, the quit, the touch drawing, the window of the touch
     controls, the game view's layout and touch mapping, the controller sampler, the pad's output pass, the bridge's debug-channel hook, the
     audio endpoint's mute, the present path's gate and the Windows list in the window driver are where the design says.
  5. Behaviour (swiftc on the host; --require-swift makes its absence a failure): tests/host/bcd_menu_items_checks.swift: every default, a
     game's saved set and its file, the touch tint, the controller chord's timing, the screen flip, the
     magnifier's geometry (and that a finger maps to the pixel drawn under it at every zoom), the Windows page's order and words, the process
     list, the gestures page, the debug presets and, with the Wine source tree (WINE_SRC, or ~/madeira-bcd/wine), that every channel exists.
  6. The Pause gate in C (BCDPauseGate.c, called by BCDFrameRing.c) under the address, undefined-behaviour and thread sanitizers: open, it costs the present path
     nothing; shut, the thread that presents waits and goes on when the gate opens; the pause is not a stall.
  7. Compilation: the C and Objective-C files compile for the iPhone SDK; the Wine-side files (the audio endpoint and the window driver)
     compile with the build's flags when the Wine tree is there (--require-wine makes its absence a failure); the preview build compiles.

  check-bcd-menu-items.py [--require-swift] [--require-wine] [--skip-preview]
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
ap.add_argument("--require-wine", action="store_true")
ap.add_argument("--skip-preview", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[-2500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    out = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


def func(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
PLAIN = ["BCDSessionFeel", "BCDDebugChannels", "BCDGestureHelp", "BCDMagnifier", "BCDWindows", "BCDTouchFeelModel", "BCDMagnifierBar"]
LIVE = ["BCDMenuMore", "BCDSessionFeelLive", "BCDMagnifierLive", "BCDWindowsLive", "BCDDebugLive", "BCDPauseLive"]
read = lambda p: Path(p).read_text()
src = {n: read(APP / f"{n}.swift") for n in PLAIN + LIVE}
rumble_m, rumble_h = read(APP / "BCDPhoneRumble.m"), read(APP / "BCDPhoneRumble.h")
ring_c, ring_h = read(APP / "BCDFrameRing.c"), read(APP / "BCDFrameRing.h")
gate_c, gate_h = read(APP / "BCDPauseGate.c"), read(APP / "BCDPauseGate.h")
session = read(APP / "BCDSessionLive.swift")
library = read(APP / "Library.swift")
content = read(APP / "ContentView.swift")
touch_layouts = read(APP / "BCDTouchLayouts.swift")
touch_controls = read(APP / "BCDTouchControls.swift")
touch_live = read(APP / "BCDTouchLive.swift")
in_game_menu = read(APP / "BCDInGameMenu.swift")
pad_output = read(APP / "PadOutput.m")
bridge = read(APP / "WineProcessBridge.m")
bridging = read(APP / "Madeira-Bridging-Header.h")
gestures = read(APP / "SystemGestures.swift")
winios_h = read(APP / "Winios/Winios.h")
winios_m = read(APP / "Winios/Winios.m")
driver = read(R / "build/win32u-unix/driver_ios.c")
audio = read(R / "build/ntdll-unix/audio_null_ios.c")
project = read(R / "app/Madeira.xcodeproj/project.pbxproj")
tool = read(R / "tools/ui-preview.py")
harness = read(R / "tests/host/bcd_menu_items_checks.swift")
gate_test = read(R / "tests/host/bcd_pause_gate_test.c")
workflow = read(R / ".github/workflows/build-ipa.yml")

# ---- 1. licence -----------------------------------------------------------------------------------------------------------------------
for name in PLAIN + LIVE:
    check(src[name].startswith(HEADER), f"{name}.swift: BCD's licence header")
for name, text in (("BCDPhoneRumble.m", rumble_m), ("BCDPhoneRumble.h", rumble_h), ("bcd_menu_items_checks.swift", harness), ("bcd_pause_gate_test.c", gate_test)):
    check(text.startswith(HEADER), f"{name}: BCD's licence header")
check(Path(__file__).read_text().startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)\n"),
      "this check: BCD's licence header")

# ---- 2. layers ----------------------------------------------------------------------------------------------------------------------------
ENGINE = ("LibraryModel", "MadeiraConfig", "LogStore", "GameProfile", "winios_", "madeira_", "InputSettings", "TouchControlsModel", "GamepadInput", "HardwareInput",
          "LibraryEntry", "bcd_pause", "bcd_phone")
allowed = {"BCDSessionFeel": {"Foundation"}, "BCDDebugChannels": {"Foundation"}, "BCDGestureHelp": {"Foundation"},
           "BCDMagnifier": {"CoreGraphics", "Foundation"}, "BCDWindows": {"Foundation"}, "BCDTouchFeelModel": {"Combine", "Foundation", "UIKit"},
           "BCDMagnifierBar": {"SwiftUI"}}
for name in PLAIN:
    imports = set(re.findall(r"^import (\w+)", code(src[name]), re.M))
    check(imports == allowed[name], f"{name}.swift imports {sorted(allowed[name])} only", imports)
    named = sorted(w for w in ENGINE if w in code(src[name]))
    check(not named, f"{name}.swift names no engine type", ", ".join(named))
check("UserDefaults" in code(src["BCDSessionFeel"]) and code(src["BCDSessionFeel"]).count("UserDefaults") == 1,
      "BCDSessionFeel.swift reads the app's preferences in one place only (the controller chord's choice, read once before any session)")
for name in LIVE:
    check(re.search(r'ENGINE_BOUND \|= \{[^}]*"%s\.swift"' % name, tool) is not None, f"{name}.swift is in ui-preview.py's ENGINE_BOUND")
    check(f"{name}.swift in Sources" in project and project.count(f"path = {name}.swift;") == 1, f"{name}.swift is built by the Xcode project")
for name in PLAIN:
    check(not re.search(r'ENGINE_BOUND[^\n]*"%s\.swift"' % name, tool) and project.count(f"path = {name}.swift;") == 1 and f"{name}.swift in Sources" in project,
          f"{name}.swift stays in the preview build and is built by the Xcode project")
check("BCDPhoneRumble.m in Sources" in project and "path = BCDPhoneRumble.h;" in project, "the Xcode project builds BCDPhoneRumble.m and lists its header")
check('#import "BCDPhoneRumble.h"' in bridging and "void madeira_audio_set_paused(int paused);" in bridging, "the bridging header names the phone rumble and the audio mute")

# ---- 3. off means nothing --------------------------------------------------------------------------------------------------------------
feel_model = code(src["BCDTouchFeelModel"])
check(feel_model.count("Timer(") == 1 and "private func arm()" in feel_model
      and "guard seconds > 0, !hidden, !suspended, held == 0 else { return }" in func(feel_model, "private func arm()"),
      "auto-hide: the only timer is made by arm(), and only with a time chosen, no finger down and the controls shown")
check("guard seconds > 0 else { return }" in func(feel_model, "func controlsTouched(") and "guard seconds > 0, hidden else { return }" in func(feel_model, "func gameTouched()"),
      "auto-hide: a touch is looked at only with a time chosen (and the game's touch only while the controls are hidden)")
check("if event.type == .touches, BCDTouchFeelModel.shared.seconds > 0 {" in content, "the controls' window looks at an event only when auto-hide is on")
check("guard visible, !BCDTouchFeel.controlsHidden else { return false }" in content and "if !feel.hidden {" in content,
      "hidden controls build no view and take no touch")
check("guard BCDTouchFeel.haptics else { return }" in touch_layouts, "the touch tick reads one flag")
check("BCDTouchFeel.tint.map" in touch_controls and ".id(feel.revision)" in touch_live, "the controls' colour is the palette's, drawn again when it changes")
check("if (!g_enabled) return;" in func(rumble_m, "void bcd_phone_rumble_push(") and "if (bcd_phone_rumble_enabled()) {" in pad_output
      and "!g_rumble[0] && g_xinput" in pad_output, "phone vibration: nothing unless the player switched it on, and then only where no paired controller takes the rumble")
check("bcd_phone_rumble_stop();   // madeira-bcd (round 604)" in pad_output, "the app resigning active stops the phone's rumble too")
check("static OSStatus ios_audio_render_cb" in audio and "if (atomic_load_explicit(&g_ios_audio_paused, memory_order_relaxed)) {" in audio
      and "void madeira_audio_set_paused(int paused)" in audio, "the audio endpoint plays silence only while the game is paused (one relaxed load otherwise)")
check("bcd_pause_gate_pass();" in ring_c and "pthread" not in ring_c and "if (atomic_load_explicit(&g_pause, memory_order_relaxed)) pause_wait();" in gate_c,
      "the ring calls the gate once a frame (it takes no lock itself); the gate costs one relaxed load while it is open")
check("void bcd_pause_set(int paused);" in gate_h and "int bcd_pause_waiting(void);" in gate_h and "void bcd_frame_forget_last(void);" in ring_h
      and "bcd_frame_forget_last();" in gate_c, "the gate is in its own header and tells the statistics to forget the wait")
check("BCDPauseGate.c in Sources" in project and "path = BCDPauseGate.h;" in project, "the Xcode project builds the gate")
check('#import "BCDPauseGate.h"' in bridging, "the bridging header names the gate")
poll = func(driver, "static void ios_window_menu_poll(void)")
check("if (!winios_windows_wanted || !winios_windows_wanted()) return;" in poll.split("{", 1)[1][:200], "the window driver's list work is behind one relaxed flag")
check("ios_window_menu_poll();" in func(driver, "static BOOL winios_drv_process_events( DWORD mask )"), "the message poll calls it once")
check("static _Atomic int g_winlist_wanted;" in winios_m and "void winios_windows_set_wanted(int on)" in winios_m, "the list is wanted only while the app says so")
pro = read(APP / "ProMotionIntent.swift")
menu_text = src["BCDMenuMore"]
check("func setHeat(_ on: Bool)" in menu_text and "ThermalPacing.shared.arm()" in menu_text and "ThermalPacing.shared.disarm()" in menu_text
      and 'GameProfile(windowsPath: entry.windowsPath).set("env.MADEIRA_THERMAL_CAP"' in menu_text,
      "the cooling row drives the thermal cap that is already in the tree (ThermalPacing): the game's own line, the session's environment, begin and disarm")
check("var heldMode: Int32?" in pro and "func disarm()" in pro and pro.count("madeira-bcd (round 604") == 3,
      "ProMotionIntent.swift: ThermalPacing gets the three small members the menu needs (arm, what it holds, and a switch-off that puts the old cap back)")
check("guard isOn else { return base }" in code(src["BCDMagnifier"]) and "BCDMagnifierCurrent.state.rect(base: base)" in content
      and "if BCDMagnifierCurrent.state.isOn {" in content, "the magnifier changes nothing at 1x")
check("guard window == nil," in code(src["BCDMagnifierLive"]) and "func reset()" in src["BCDMagnifierLive"], "the magnifier's bar exists only while the magnifier is on")
check("TouchMouseGate.override = Self.gateMode(" in code(src["BCDSessionFeelLive"]) and "switch override ?? mode {" in content,
      "the touch-mouse choice overrides the old rule only when chosen")
check("holdHome || BCDChordSetting.mode == .holdGuide" in library and "if BCDChordSetting.mode == .holdStart {" in library,
      "the controller sampler times Home or Start only for the way the player chose")
check("BCDChordSetting.mode == .holdGuide ||" in gestures, "Hold Home asks iOS to hand the Home button over, at the next start")
once = func(bridge, "{   /* madeira-bcd (round 604): the in-game menu's Wine debug channels")
check("[[NSFileManager defaultManager] removeItemAtPath:once error:nil];" in once and "chan.length <= 400" in once and "characterSetWithCharactersInString" in once
      and 'setenv("WINEDEBUG", chan.UTF8String, 1);' in once, "the bridge applies the debug channels once, removes the file, and takes plain channel text only")
check(bridge.index("bcd-debug-once.txt") > bridge.index('fprintf(stderr, "[madeira-env] game %s=%s\\n"'),
      "... after the game's own env lines, so the player's request is the last word")
check("BCDPauseLive.resume()" in func(library, "func requestQuit()"), "a quit asks the game to close only after Pause has let it go")

# ---- 4. wiring ------------------------------------------------------------------------------------------------------------------------------
check("BCDFeelLive.sessionBegan(entry)" in library and "BCDFeelLive.sessionEnded()" in library, "Library.swift: the session start and end call the feel settings, one line each")
check(library.index("BCDFeelLive.sessionBegan(entry)") < library.index("BCDSessionRecorder.sessionBegan(entry)") and library.count("BCDFeelLive.") == 2, "... and no more")
for hook in ("return BCDMenuMore.arrange(own, tab: tab)", "if let own = BCDMenuMore.page(id) { return own }", "case \"log\": return BCDMenuMore.logPage()",
             "func logLines() -> [BCDLogLine] { BCDMenuMore.logLines() }", "BCDMenuMore.pageShown(id) {", "BCDMenuMore.menuClosed()"):
    check(hook in session, f"BCDSessionLive.swift: {hook}")
for verb in ("act", "toggle", "choose", "answer"):
    check(f"if BCDMenuMore.handles(row) {{ return BCDMenuMore.{verb}(row" in session, f"BCDSessionLive.swift: the library menu's {verb} hands our rows over")
check("ForEach(page.sections) { section($0) }" in in_game_menu, "the log page draws the rows above its lines")
menu_more = code(src["BCDMenuMore"])
ids = re.findall(r'id: "(mm\.[A-Za-z0-9.]+)"', menu_more)
kinds = {}
for m in re.finditer(r'BCDMenuRow\(id: "(mm\.[A-Za-z0-9.]+)".*?kind: (\.[a-z]+)', menu_more, re.S):
    kinds.setdefault(m.group(1), m.group(2))
handled = {
    ".toggle": func(menu_more, "static func toggle("),
    ".choice": func(menu_more, "static func choose("),
    ".action": func(menu_more, "static func act("),
}
for row, kind in sorted(kinds.items()):
    if kind in handled:
        check(f'"{row}"' in handled[kind] or f'"{row}"' in func(menu_more, "static func answer(") or row.startswith("mm.win.front") or row.startswith("mm.dbg.p."),
              f"row {row} ({kind[1:]}) has a handler")
for page in ("mm.windows", "mm.tasks", "mm.gestures", "mm.debug"):
    check(f'case "{page}":' in func(menu_more, "static func page(_ id: String)"), f"the page {page} is built")
    check(f'.page("{page}"' in menu_more, f"... and a row opens it")
check(len(set(ids)) >= 30, f"the menu has its rows ({len(set(ids))} ids)")
check("BCDMenuMore.handles" in session and 'row.hasPrefix("mm.")' in menu_more, "every id of ours starts with mm.")
check(not any(i.startswith("mm.") for i in re.findall(r'BCDMenuRow\(id: "([a-z.]+)"', session)), "no older row uses our prefix")
check(content.count("BCDTouchFeelModel.shared.gameTouched()") == 1 and content.count("BCDMagnifierCurrent.state") == 3, "ContentView.swift: the game view's three hooks")
check("bcd_pause_set(1)" in code(src["BCDPauseLive"]) and "madeira_audio_set_paused(1)" in code(src["BCDPauseLive"]), "Pause shuts the gate and silences the audio")
check("func clear()" in src["BCDPauseLive"] and "BCDPauseLive.clear()" in code(src["BCDSessionFeelLive"]), "a session starts and ends with the gate open")
check("NtSuspendProcess" in src["BCDPauseLive"] and "does not work here" in src["BCDPauseLive"], "the reason for the gate (Wine's suspend does not work on iOS) is written down where the code is")
check("winios_windows_command" in code(src["BCDWindowsLive"]) and "winios_windows_set_wanted(0)" in code(src["BCDWindowsLive"]), "the Windows page switches its list off when it closes")
check("WINIOS_WINLIST_MAX" in winios_h and "struct winios_listed_window" in winios_h and "#include \"../../app/Madeira/Winios/Winios.h\"" in driver,
      "the window list's C interface is declared once, in Winios.h, and the driver includes it")
check(re.search(r"^\s*- name: .*BCD in-game menu items", workflow, re.M) is not None and "check-bcd-menu-items.py" in workflow, "CI runs this check")

# ---- 5. behaviour in Swift --------------------------------------------------------------------------------------------------------------------------
swift = shutil.which("xcrun")
wine_src = Path(os.environ.get("WINE_SRC", R / "wine"))
if not (wine_src / "dlls").is_dir():
    wine_src = Path.home() / "madeira-bcd/wine"
have_wine = (wine_src / "dlls").is_dir() and (wine_src / "build-macos/include/config.h").exists()
if not swift:
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        guest = read(APP / "GuestDisplay.swift")
        layout = guest[guest.index("/// How the guest surface is mapped into the view"):guest.index("/// The session default of the guest")]
        (t / "GuestLayout.swift").write_text("import Foundation\nimport CoreGraphics\n\n" + layout)
        (t / "main.swift").write_text(harness)
        files = [str(t / "main.swift"), str(t / "GuestLayout.swift")] + [str(APP / f"{n}.swift") for n in
                 ("BCDSessionFeel", "BCDOrientation", "BCDGestureHelp", "BCDMagnifier", "BCDWindows", "BCDDebugChannels")]
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run")] + files, capture_output=True, text=True)
        check(r.returncode == 0, "the plain Swift files and the harness compile on the host", r.stdout + r.stderr)
        if r.returncode == 0:
            run_args = [str(t / "run")] + ([str(wine_src)] if (wine_src / "dlls").is_dir() else [])
            r = subprocess.run(run_args, capture_output=True, text=True)
            out = r.stdout
            for line in out.splitlines():
                if line.startswith("FAIL"):
                    check(False, line[6:])
            n_pass = sum(1 for line in out.splitlines() if line.startswith("PASS"))
            check(r.returncode == 0 and "ALL PASS" in out and n_pass >= 100, f"the behaviour harness: {n_pass} checks, none failing", out[-1500:] + r.stderr[-800:])
            check((wine_src / "dlls").is_dir() or not args.require_wine, "the Wine source was there for the channel names", str(wine_src))

# ---- 6. the gate in C ---------------------------------------------------------------------------------------------------------------------------------
clang = shutil.which("clang")
if not clang:
    check("clang is here for the gate test", not args.require_swift)
else:
    for san in ("address,undefined", "thread"):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "gate"
            r = subprocess.run([clang, "-std=c11", "-g", "-O1", f"-fsanitize={san}", f"-I{APP}", str(R / "tests/host/bcd_pause_gate_test.c"),
                                str(APP / "BCDFrameRing.c"), str(APP / "BCDPauseGate.c"), "-o", str(exe), "-lpthread"], capture_output=True, text=True)
            check(r.returncode == 0, f"the gate test compiles under {san}", r.stderr)
            if r.returncode == 0:
                r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
                check(r.returncode == 0 and "ALL PASS" in r.stdout, f"the gate holds the presenting thread and lets it go ({san})", r.stdout + r.stderr)

# ---- 7. compilation for the phone -------------------------------------------------------------------------------------------------------------------
if swift:
    sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
    base = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0"]
    for name, extra in (("BCDFrameRing.c", []), ("BCDPauseGate.c", []), ("BCDPhoneRumble.m", ["-fobjc-arc"]), ("PadOutput.m", ["-fobjc-arc"]),
                        ("Winios/Winios.m", ["-fobjc-arc"]), ("WineProcessBridge.m", ["-fobjc-arc"])):
        r = subprocess.run(base + extra + [f"-I{APP}", f"-I{APP}/Winios", "-Wall", str(APP / name)], capture_output=True, text=True)
        errors = [l for l in r.stderr.splitlines() if "error:" in l]
        check(not errors, f"{name} compiles for the iPhone", "\n".join(errors[:5]))
    if have_wine:
        wine_build = wine_src / "build-macos"
        ntdll = ["-O2", "-fPIC", "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing",
                 "-Wimplicit-function-declaration", "-Wint-conversion", f"-include{wine_build}/include/config.h",
                 f"-include{R}/build/ntdll-unix/shims/wine_ios_exit.h", f"-I{R}/build/ntdll-unix/shims", f"-I{R}/build/madsync", "-DHAVE_LINUX_NTSYNC_H=1",
                 f"-I{wine_build}/dlls/ntdll", f"-I{wine_src}/dlls/ntdll", f"-I{wine_src}/dlls/ntdll/unix", f"-I{wine_build}/include", f"-I{wine_src}/include",
                 "-D__WINESRC__", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1",
                 "-Dget_thread_context=ntdll_get_thread_context", "-Dset_thread_context=ntdll_set_thread_context"]
        # The build's own flags for this file (it calls Nt functions that its headers leave undeclared, as it always did), so only real errors count.
        ntdll_flags = [f for f in ntdll if f not in ("-Wimplicit-function-declaration", "-Wint-conversion")] + ["-Wno-implicit-function-declaration", "-Wno-int-conversion"]
        r = subprocess.run(base + ntdll_flags + [str(R / "build/ntdll-unix/audio_null_ios.c")], capture_output=True, text=True)
        errors = [l for l in r.stderr.splitlines() if "error:" in l]
        check(not errors, "audio_null_ios.c (the audio endpoint) compiles with the build's flags", "\n".join(errors[:5]))
        win32u = ["-O2", "-fPIC", "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing", "-Wimplicit-function-declaration", "-Wint-conversion",
                  f"-include{R}/build/win32u-unix/config_ios.h", f"-include{R}/build/ntdll-unix/shims/wine_ios_exit.h", f"-I{R}/build/win32u-unix",
                  f"-I{wine_build}/include", f"-I{R}/build/ntdll-unix/shims", f"-I{wine_build}/dlls/win32u", f"-I{wine_src}/dlls/win32u", f"-I{wine_src}/include",
                  "-D__WINESRC__", "-D_WIN32U_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1",
                  "-D__wine_unix_lib_init=win32u_unix_lib_init", "-USONAME_LIBFREETYPE", "-USONAME_LIBFONTCONFIG", "-USONAME_LIBEGL", "-USONAME_LIBVULKAN",
                  "-USONAME_LIBGNUTLS", "-UHAVE_FT2BUILD_H"]
        r = subprocess.run(base + win32u + [str(R / "build/win32u-unix/driver_ios.c")], capture_output=True, text=True)
        errors = [l for l in r.stderr.splitlines() if "error:" in l or "implicit declaration" in l]
        check(not errors, "driver_ios.c (the window driver) compiles with the build's flags, no implicit declaration", "\n".join(errors[:5]))
    else:
        check(not args.require_wine, "the Wine tree (WINE_SRC or ~/madeira-bcd/wine, built) was there for the two Wine-side files")
    if not args.skip_preview:
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp], capture_output=True, text=True, timeout=1500)
            check(r.returncode == 0, "the preview build compiles with the plain files and without the engine ones", (r.stdout + r.stderr)[-1500:])

print("ALL PASS" if failures == 0 else f"{failures} FAILED")
sys.exit(1 if failures else 0)
