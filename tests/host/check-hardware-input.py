#!/usr/bin/env python3
# Portions copyright 2026 NevesHenry: code taken from https://github.com/willfaust/Madeira (pull request #413)
# under GPL-3.0-or-later and modified for BCD in October 2026. See THIRD-PARTY-NOTICES.md.
"""Hardware keyboard and mouse: compile the production mapping on a POSIX host.

Four parts:
  1. The pure section of app/Madeira/HardwareInput.swift (key map, mouse button
     edges, held-set diffing, motion carry, wheel notches, the AssistiveTouch
     classifier, the desktop cursor clamp, the right-stick velocity, the
     view-to-screen mapping, focus gating of held keys, click focus, the
     pointer route and the automatic pointer lock) is compiled with swiftc and
     exercised.
  2. Every HID keyboard usage is taken through the production chain
     HID usage -> virtual key (HardwareInput.swift) -> scan code (Wine's
     NtUserMapVirtualKeyEx(MAPVK_VK_TO_VSC_EX) on its built-in US layout, which
     is what the iOS driver uses) -> extended flag (winios_key_extended_flag,
     compiled from build/win32u-unix/driver_ios.c) and compared with the scan
     code a real PC keyboard sends for that key (USB HID to PS/2 set 1).
     check-nav-keys.py covers the navigation-key flag itself.
  3. app/Madeira/Winios/WiniosCursor.c, the direct-mode cursor state the driver
     reports into, is compiled with cc and exercised, including from threads.
  4. Source checks for the driver's reports, the app wiring and the switches.

madeira-bcd (2026-10-18; a tester's Warcraft II on an iPad drifted away from
the iPad pointer and grew in memory): part 1 also checks that the mouse's
absolute mapping equals GameSurfaceLayout.map (where a finger's touch lands) for
every display mode, monitor size and view, the clip clamp, MouseCapture (a
hidden cursor that follows the pointer vs mouse-look), the follow route, the
automatic lock's `wantsLock` and KeyRepeat; part 3 the clip, warp and reader
reports; part 3b compiles the wineserver's reader report (queue_ios.c) against
stub structures; part 4 the driver, server, overlay-window and log-rate wiring.

Needs python3, swiftc (SWIFTC to override), a C compiler (CC, default cc) and
the Wine source: the `wine` submodule, or WINE_SRC=<path to a Wine tree>.
No iOS SDK, device, keyboard or mouse is needed.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
app = root / 'app/Madeira'
source = (app / 'HardwareInput.swift').read_text()
pure = source.split('// MARK: - Pure input mapping', 1)[1].split('// MARK: - Device glue', 1)[0]
for banned in ('UIKit', 'UITouch', 'winios_', 'GameController'):
    assert banned not in pure.replace('UIKit or Wine', '').replace('through UIKit', ''), f'pure section uses {banned}'
# The production layout a finger's touch goes through (GuestDisplay.swift), compiled with it.
guest_display = (app / 'GuestDisplay.swift').read_text()
layout = guest_display[guest_display.index('/// How the guest surface is mapped into the view'):
                       guest_display.index('/// The session default of the guest')]

failures = []


def check(cond, what):
    if not cond:
        failures.append(what)


# ---------------------------------------------------------------- 1. Swift ---
swift_tests = r'''
// Key map: every usage the map knows, dumped for the scan-code comparison.
for u in 0..<256 {
    if let vk = HardwareKeyMap.vk(forHIDUsage: u) { print("MAP \(u) \(vk)") }
}
assert(HardwareKeyMap.vk(forHIDUsage: 0x04) == 0x41)        // a -> VK_A
assert(HardwareKeyMap.vk(forHIDUsage: 0x27) == 0x30)        // 0 -> VK_0
assert(HardwareKeyMap.vk(forHIDUsage: 0x45) == 0x7B)        // F12
assert(HardwareKeyMap.vk(forHIDUsage: 0x73) == 0x87)        // F24
assert(HardwareKeyMap.vk(forHIDUsage: 0xE1) == 0xA0)        // left shift is VK_LSHIFT
for u in [0x00, 0x01, 0x02, 0x03, 0x74, 0x76, 0x78, 0x7E, 0xE8, 0xFF] {
    assert(HardwareKeyMap.vk(forHIDUsage: u) == nil, "usage \(u) must stay unmapped")
}

// Ctrl+Alt+P, either side of each modifier; never P alone or with one modifier.
assert(HardwareKeyMap.isPointerLockChord(0x50, held: [0xA2, 0xA4, 0x50]))
assert(HardwareKeyMap.isPointerLockChord(0x50, held: [0xA3, 0xA5, 0x50]))
assert(!HardwareKeyMap.isPointerLockChord(0x50, held: [0xA2, 0x50]))
assert(!HardwareKeyMap.isPointerLockChord(0x50, held: [0xA4, 0x50]))
assert(!HardwareKeyMap.isPointerLockChord(0x51, held: [0xA2, 0xA4, 0x51]))

// Mouse buttons: MOUSEEVENTF_* pairs, XBUTTON1/2 in mouseData.
assert(MouseButton.left.event(down: true) == (0x0002, 0) && MouseButton.left.event(down: false) == (0x0004, 0))
assert(MouseButton.right.event(down: true) == (0x0008, 0) && MouseButton.right.event(down: false) == (0x0010, 0))
assert(MouseButton.middle.event(down: true) == (0x0020, 0) && MouseButton.middle.event(down: false) == (0x0040, 0))
assert(MouseButton.x1.event(down: true) == (0x0080, 1) && MouseButton.x1.event(down: false) == (0x0100, 1))
assert(MouseButton.x2.event(down: true) == (0x0080, 2) && MouseButton.x2.event(down: false) == (0x0100, 2))

// Held-set diffing: declared sets converge, ups before downs, no duplicates.
var keys = HeldEdges<Int32>()
var e = keys.update([0x57, 0xA0])
assert(e.up.isEmpty && e.down == [0x57, 0xA0])
e = keys.update([0x57, 0xA0])
assert(e.up.isEmpty && e.down.isEmpty)                      // a repeat posts nothing
e = keys.update([0x41])
assert(e.up == [0x57, 0xA0] && e.down == [0x41])
e = keys.update([])
assert(e.up == [0x41] && e.down.isEmpty && keys.down.isEmpty)
var buttons = HeldEdges<MouseButton>()
_ = buttons.update([.left, .x2])
let b = buttons.update([.right])
assert(b.up == [.left, .x2] && b.down == [.right])

// Motion carry: fractions accumulate, truncation toward zero, clamp, NaN.
var carry = MotionCarry()
var sum: Int32 = 0
for _ in 0..<10 { sum += carry.add(0.25, 0, gain: 1).dx }
assert(sum == 2)                                            // 2.5 -> 2, 0.5 carried
assert(carry.add(0.5, 0, gain: 1).dx == 1)
var neg = MotionCarry()
assert(neg.add(-0.6, -1.4, gain: 1) == (0, -1))
assert(neg.add(-0.6, 0, gain: 1).dx == -1)
var big = MotionCarry()
assert(big.add(1e9, -1e9, gain: 1) == (30000, -30000))
var bad = MotionCarry()
assert(bad.add(.nan, 1, gain: 1) == (0, 0) && bad.add(1, 1, gain: .infinity) == (0, 0))
var gained = MotionCarry()
assert(gained.add(3, -3, gain: 2) == (6, -6))

// Wheel: whole notches of 120, remainder kept, bounded bursts, NaN ignored.
var wheel = WheelAccumulator()
assert(wheel.add(0, 0.6).isEmpty)
var n = wheel.add(0, 0.6)
assert(n.count == 1 && n[0].flags == 0x0800 && n[0].delta == 120)
n = wheel.add(0, -2.2)
assert(n.count == 2 && n.allSatisfy { $0.flags == 0x0800 && $0.delta == -120 })
n = wheel.add(1.0, 0)
assert(n.count == 1 && n[0].flags == 0x1000 && n[0].delta == 120)
n = wheel.add(0, 1e12)
assert(n.count == WheelAccumulator.maxNotchesPerEvent)
assert(wheel.add(0, 0.5).isEmpty)                           // the burst did not linger
assert(wheel.add(.nan, .infinity).isEmpty)

// GCMouse's scroll pad: vertical first (negative away from the user),
// horizontal second (positive right). A turn away from the user is WHEEL +120,
// one to the right HWHEEL +120; never the other axis.
var gcWheel = WheelAccumulator()
var w = GCMouseWheel.axes(-1, 0)
n = gcWheel.add(w.dx, w.dy)
assert(n.count == 1 && n[0].flags == 0x0800 && n[0].delta == 120)
w = GCMouseWheel.axes(3, 0)
n = gcWheel.add(w.dx, w.dy)
assert(n.count == 3 && n.allSatisfy { $0.flags == 0x0800 && $0.delta == -120 })
w = GCMouseWheel.axes(0, 1)
n = gcWheel.add(w.dx, w.dy)
assert(n.count == 1 && n[0].flags == 0x1000 && n[0].delta == 120)
w = GCMouseWheel.axes(0, -2)
n = gcWheel.add(w.dx, w.dy)
assert(n.count == 2 && n.allSatisfy { $0.flags == 0x1000 && $0.delta == -120 })

// AssistiveTouch classifier: no contact patch, or coincident with a GC button.
assert(SynthesizedTouch.looksSynthesized(direct: true, majorRadius: 0, secondsSinceButton: .infinity))
assert(SynthesizedTouch.looksSynthesized(direct: true, majorRadius: 20, secondsSinceButton: 0.010))
assert(!SynthesizedTouch.looksSynthesized(direct: true, majorRadius: 20, secondsSinceButton: 0.5))
assert(!SynthesizedTouch.looksSynthesized(direct: false, majorRadius: 0, secondsSinceButton: 0))

// Desktop cursor follows relative motion inside the desktop.
assert(DesktopCursor.advance(x: 10, y: 10, dx: -50, dy: 5, width: 1024, height: 768) == (0, 15))
assert(DesktopCursor.advance(x: 1000, y: 760, dx: 100, dy: 100, width: 1024, height: 768) == (1023, 767))
assert(DesktopCursor.advance(x: 5, y: 5, dx: 1, dy: 1, width: 0, height: 0) == (0, 0))

// Right stick as a velocity mouse.
assert(StickVelocity.deflect(0.1, 0.1) == (0, 0))           // inside the deadzone
let full = StickVelocity.deflect(1, 0)
assert(abs(full.x - 1) < 1e-9 && full.y == 0)
let f = StickVelocity.frame(x: 1, y: 1, gain: 1, dt: 1.0 / 60)
assert(f.dx > 0 && f.dy < 0)                                // stick up = mouse up
assert(abs((f.dx * f.dx + f.dy * f.dy).squareRoot() - 6.0) < 1e-9)   // 360/s at 60 Hz
let stalled = StickVelocity.frame(x: 1, y: 0, gain: 1, dt: 5)
assert(abs(stalled.dx - 24.0) < 1e-9)                       // dt clamped to 1/15 s
assert(StickVelocity.frame(x: .nan, y: 0, gain: 1, dt: 0.016) == (0, 0))

// View to screen: the same aspect fit as MetalBackedView.gameRect/mapTouch.
func mapTouch(_ x: Double, _ y: Double, _ bw: Double, _ bh: Double) -> (Int32, Int32) {
    let scale = min(bw / 1024, bh / 768), w = 1024 * scale, h = 768 * scale
    let rx = (bw - w) / 2, ry = (bh - h) / 2
    return (Int32(min(max((x - rx) * 1024 / w, 0), 1023)), Int32(min(max((y - ry) * 768 / h, 0), 767)))
}
for (bw, bh) in [(1024.0, 768.0), (834.0, 1194.0), (1366.0, 1024.0), (393.0, 852.0), (852.0, 393.0)] {
    for (px, py) in [(0.0, 0.0), (bw / 2, bh / 2), (bw - 0.5, bh - 0.5), (bw * 0.3, bh * 0.7), (-5.0, bh + 9)] {
        let s = ScreenMap.toScreen(x: px, y: py, viewW: bw, viewH: bh, screenW: 1024, screenH: 768)
        let m = mapTouch(px, py, bw, bh)
        assert(s.x == m.0 && s.y == m.1, "ScreenMap differs from mapTouch at \(px),\(py) in \(bw)x\(bh)")
    }
}
// Desktop session: a 1280x720 desktop letterboxed in a 4:3 view; centre and corners.
assert(ScreenMap.toScreen(x: 512, y: 384, viewW: 1024, viewH: 768, screenW: 1280, screenH: 720) == (640, 360))
assert(ScreenMap.toScreen(x: 0, y: 0, viewW: 1024, viewH: 768, screenW: 1280, screenH: 720) == (0, 0))
assert(ScreenMap.toScreen(x: 1024, y: 768, viewW: 1024, viewH: 768, screenW: 1280, screenH: 720) == (1279, 719))
assert(ScreenMap.toScreen(x: .nan, y: 1, viewW: 1024, viewH: 768, screenW: 1024, screenH: 768) == (0, 0))
assert(ScreenMap.toScreen(x: 5, y: 5, viewW: 0, viewH: 0, screenW: 1024, screenH: 768) == (0, 0))

// Focus gate: losing focus hides held keys; a key pressed without focus stays
// hidden until released, even after focus returns.
var gate = FocusGate<Int32>()
gate.press(0x57, focused: true)
assert(gate.wanted(focused: true) == [0x57])
gate.focusLost()
assert(gate.wanted(focused: false).isEmpty && gate.wanted(focused: true).isEmpty)
gate.press(0x41, focused: false)
assert(gate.wanted(focused: true).isEmpty)                  // W and A were not seen going down
gate.release(0x57)
gate.press(0x57, focused: true)
assert(gate.wanted(focused: true) == [0x57])                 // a fresh press counts again
gate.release(0x41)
gate.press(0x50, focused: true); gate.block(0x50)            // the swallowed P of Ctrl+Alt+P
assert(gate.wanted(focused: true) == [0x57])
gate.release(0x50)
gate.press(0x50, focused: true)
assert(gate.wanted(focused: true) == [0x57, 0x50])
gate.reset()
assert(gate.physical.isEmpty && gate.wanted(focused: true).isEmpty)

// Click focus: a press is routed by the tap that carries it, before or after.
var click = ClickFocus()
assert(click.onGame && click.route(buttonAt: 10, now: 10) == nil)   // no tap yet: wait
assert(click.route(buttonAt: 10, now: 10.061) == true)              // none came: current focus
click.touchBegan(onGame: false, at: 20.00)
assert(!click.onGame && click.route(buttonAt: 20.02, now: 20.02) == false)   // tap just before
click.touchBegan(onGame: true, at: 30.03)
assert(click.route(buttonAt: 30.00, now: 30.04) == true)                     // tap just after
assert(click.route(buttonAt: 40.00, now: 40.00) == nil)
assert(click.route(buttonAt: 40.00, now: 40.07) == true)
click.touchBegan(onGame: false, at: 50)
assert(click.route(buttonAt: 60, now: 60.1) == false)
assert(click.route(buttonAt: .nan, now: 1) == false)

// Pointer route: absolute only where the pointer's position is known, the
// program shows a cursor and nothing is locked.
assert(PointerPolicy.route(focused: false, hover: true, locked: false, cursorShown: true, absoluteAllowed: true) == .blocked)
assert(PointerPolicy.route(focused: true, hover: true, locked: false, cursorShown: true, absoluteAllowed: true) == .absolute)
assert(PointerPolicy.route(focused: true, hover: true, locked: true, cursorShown: true, absoluteAllowed: true) == .relative)
assert(PointerPolicy.route(focused: true, hover: true, locked: false, cursorShown: false, absoluteAllowed: true) == .relative)
assert(PointerPolicy.route(focused: true, hover: false, locked: false, cursorShown: true, absoluteAllowed: true) == .relative)
assert(PointerPolicy.route(focused: true, hover: true, locked: false, cursorShown: true, absoluteAllowed: false) == .relative)

// Automatic lock: only for a live program that hides its cursor under the pointer.
func lockAction(locked: Bool = false, byUs: Bool = false, shown: Bool = false, hiddenFor: Double = 1,
                over: Bool = true, focused: Bool = true, sinceReport: Double = 0.1, sinceMotion: Double = 0.1) -> AutoLock.Action {
    AutoLock.decide(locked: locked, lockedByUs: byUs, cursorShown: shown, hiddenFor: hiddenFor, pointerOver: over,
                    focused: focused, sinceReport: sinceReport, sinceMotion: sinceMotion)
}
assert(lockAction() == .lock)
assert(lockAction(shown: true) == .none)
assert(lockAction(hiddenFor: 0.2) == .none)                  // the program may be about to show one
assert(lockAction(over: false) == .none)
assert(lockAction(focused: false) == .none)
assert(lockAction(sinceReport: 5) == .none)                  // no program is draining the mouse
assert(lockAction(sinceMotion: 3) == .none)                  // the mouse is not in use
assert(lockAction(locked: true, byUs: true, shown: true) == .unlock)
assert(lockAction(locked: true, byUs: true, focused: false) == .unlock)
assert(lockAction(locked: true, byUs: true, sinceReport: 3) == .unlock)      // the program went away
assert(lockAction(locked: true, byUs: true, sinceReport: 3, sinceMotion: 3) == .none)
assert(lockAction(locked: true, byUs: true, over: false) == .none)           // locked pointers do not hover
assert(lockAction(locked: true, byUs: false, shown: true) == .none)          // the user's own lock stays

// madeira-bcd 2026-10-18 -------------------------------------------------------
// The mouse's absolute mapping is the touch mapping: a point on the game view
// relative to the presented rect (MetalHostView's frame, GameSurfaceLayout.rect)
// lands on the same guest pixel GameSurfaceLayout.map gives a finger, for every
// display mode, live monitor size, drawable shape and view.
var mapped = 0
for mode in DisplayMode.allCases {
    for (gw, gh) in [(1024, 768), (1112, 832), (1280, 720), (640, 480), (1920, 1080), (1152, 864)] {
        for (bw, bh) in [(1600.0, 1200.0), (1366.0, 1024.0), (1194.0, 834.0), (852.0, 393.0), (2752.0 / 1.72, 2064.0 / 1.72)] {
            for aspect in [CGSize.zero, CGSize(width: 800, height: 600), CGSize(width: 1920, height: 1080)] {
                let bounds = CGRect(x: 0, y: 0, width: bw, height: bh)
                let guest = CGSize(width: gw, height: gh)
                let r = GameSurfaceLayout.rect(guest: guest, aspect: aspect, bounds: bounds, mode: mode)
                for (px, py) in [(0.0, 0.0), (bw / 2, bh / 2), (bw - 0.5, bh - 0.5), (bw * 0.3, bh * 0.7),
                                 (-5.0, bh + 9), (r.minX + 0.25, r.maxY - 0.25), (r.maxX - 0.01, r.minY)] {
                    let touch = GameSurfaceLayout.map(point: CGPoint(x: px, y: py), guest: guest, aspect: aspect,
                                                      bounds: bounds, mode: mode)
                    let mouse = ScreenMap.toGuest(x: px - r.minX, y: py - r.minY, rectW: r.width, rectH: r.height,
                                                  guestW: gw, guestH: gh)
                    assert(mouse.x == Int32(touch.x) && mouse.y == Int32(touch.y),
                           "mouse \(mouse) != touch \(touch) at \(px),\(py) \(mode) \(gw)x\(gh) in \(bw)x\(bh)")
                    mapped += 1
                }
            }
        }
    }
}
// A 1024x768 Fit session maps exactly as before the change...
for (bw, bh) in [(1024.0, 768.0), (1366.0, 1024.0), (1600.0, 1200.0), (852.0, 393.0)] {
    let r = GameSurfaceLayout.rect(guest: CGSize(width: 1024, height: 768), bounds: CGRect(x: 0, y: 0, width: bw, height: bh), mode: .fit)
    for (px, py) in [(0.0, 0.0), (bw / 2, bh / 3), (bw - 0.5, bh - 0.5), (bw * 0.9, bh * 0.1)] {
        let old = ScreenMap.toScreen(x: px, y: py, viewW: bw, viewH: bh, screenW: 1024, screenH: 768)
        let new = ScreenMap.toGuest(x: px - r.minX, y: py - r.minY, rectW: r.width, rectH: r.height, guestW: 1024, guestH: 768)
        assert(old == new, "a 1024x768 Fit session must map as before")
    }
}
// ...while the tester's 1112x832 monitor on a 1600x1200 iPad view did not: the
// pointer at the right edge put Wine's cursor at x=1023, 89 pixels short.
let oldEdge = ScreenMap.toScreen(x: 1599.9, y: 600, viewW: 1600, viewH: 1200, screenW: 1024, screenH: 768)
let wc2 = GameSurfaceLayout.rect(guest: CGSize(width: 1112, height: 832), bounds: CGRect(x: 0, y: 0, width: 1600, height: 1200), mode: .fit)
let newEdge = ScreenMap.toGuest(x: 1599.9 - wc2.minX, y: 600 - wc2.minY, rectW: wc2.width, rectH: wc2.height, guestW: 1112, guestH: 832)
assert(oldEdge.x == 1023 && newEdge.x == 1111)
assert(ScreenMap.toGuest(x: .nan, y: 1, rectW: 10, rectH: 10, guestW: 100, guestH: 100) == (0, 0))
assert(ScreenMap.toGuest(x: 5, y: 5, rectW: 0, rectH: 10, guestW: 100, guestH: 100) == (0, 0))
print("MAPPED \(mapped)")

// The clip clamp is the wineserver's (right/bottom exclusive); none reported: unchanged.
assert(ScreenMap.clamp(x: 1200, y: -5, clipLeft: 0, top: 0, right: 1112, bottom: 832) == (1111, 0))
assert(ScreenMap.clamp(x: 50, y: 60, clipLeft: 100, top: 100, right: 200, bottom: 300) == (100, 100))
assert(ScreenMap.clamp(x: 150, y: 150, clipLeft: 100, top: 100, right: 200, bottom: 300) == (150, 150))
assert(ScreenMap.clamp(x: 5000, y: 5000, clipLeft: 0, top: 0, right: 0, bottom: 0) == (5000, 5000))

// MouseCapture: position once the mouse moved long enough with no sign of mouse-look.
var cap = MouseCapture()
cap.motion(at: 10.0); cap.motion(at: 10.2)
assert(cap.reading == .unknown)
cap.motion(at: 10.0 + MouseCapture.settle + 0.001)
assert(cap.reading == .position)
cap.motion(at: 20)                                           // settled: motion changes nothing
assert(cap.reading == .position)
// A program that starts recentring later becomes a motion reader, and stays one.
cap.warp(count: 1, at: 30.0, distance: 0.3)
cap.warp(count: 1, at: 30.5, distance: 0.2)
assert(cap.reading == .position)
cap.warp(count: 1, at: 31.0, distance: 0.25)
assert(cap.reading == .motion && cap.why.contains("recentred"))
cap.motion(at: 40)
assert(cap.reading == .motion)
cap.reset()
assert(cap.reading == .unknown && cap.why.isEmpty)
// Relative route (no distance): every warp counts; three in two seconds is mouse-look.
var look = MouseCapture()
look.warp(count: 3, at: 1.0, distance: nil)
assert(look.reading == .motion)
// Following the pointer: short warps are clamps to the program's viewport, not recentring.
var clampy = MouseCapture()
for i in 0..<10 { clampy.warp(count: 1, at: 1.0 + Double(i) * 0.05, distance: 0.01) }
assert(clampy.reading == .unknown)
// Warps spread out are a program positioning its cursor now and then.
var spread = MouseCapture()
for i in 0..<5 { spread.warp(count: 1, at: Double(i) * 3.0, distance: nil) }
assert(spread.reading == .unknown)
// Confinement: a small clip is mouse-look; the window or viewport is not.
var conf = MouseCapture()
conf.clip(left: 0, top: 0, right: 1112, bottom: 832, screenW: 1112, screenH: 832)
conf.clip(left: 2, top: 0, right: 1110, bottom: 832, screenW: 1112, screenH: 832)   // cnc-ddraw style viewport
conf.clip(left: 0, top: 0, right: 0, bottom: 0, screenW: 1112, screenH: 832)        // none reported
assert(conf.reading == .unknown)
conf.clip(left: 555, top: 415, right: 557, bottom: 417, screenW: 1112, screenH: 832)
assert(conf.reading == .motion && conf.why.contains("confined"))
// The wineserver's reader bits: raw input without legacy messages, or a low-level hook.
var raw = MouseCapture(); raw.reader(0); assert(raw.reading == .unknown)
raw.reader(1); assert(raw.reading == .motion && raw.why.contains("raw"))
var hook = MouseCapture(); hook.reader(2); assert(hook.reading == .motion && hook.why.contains("hook"))
var nan = MouseCapture(); nan.motion(at: .nan); nan.warp(count: 5, at: .infinity, distance: nil)
assert(nan.reading == .unknown)

// The follow route: absolute only with hover, no lock, absolute allowed.
assert(PointerPolicy.route(focused: true, hover: true, locked: false, cursorShown: false, absoluteAllowed: true, follow: true) == .absolute)
assert(PointerPolicy.route(focused: true, hover: true, locked: true, cursorShown: false, absoluteAllowed: true, follow: true) == .relative)
assert(PointerPolicy.route(focused: true, hover: false, locked: false, cursorShown: false, absoluteAllowed: true, follow: true) == .relative)
assert(PointerPolicy.route(focused: true, hover: true, locked: false, cursorShown: false, absoluteAllowed: false, follow: true) == .relative)
assert(PointerPolicy.route(focused: false, hover: true, locked: false, cursorShown: false, absoluteAllowed: true, follow: true) == .blocked)

// The automatic lock gives way to a following cursor only when asked (MADEIRA_POINTER_FOLLOW=1).
assert(AutoLock.decide(locked: false, lockedByUs: false, cursorShown: false, hiddenFor: 1, pointerOver: true,
                       focused: true, sinceReport: 0.1, sinceMotion: 0.1, wantsLock: false) == .none)
assert(AutoLock.decide(locked: true, lockedByUs: true, cursorShown: false, hiddenFor: 1, pointerOver: true,
                       focused: true, sinceReport: 0.1, sinceMotion: 0.1, wantsLock: false) == .unlock)
assert(AutoLock.decide(locked: true, lockedByUs: false, cursorShown: false, hiddenFor: 1, pointerOver: true,
                       focused: true, sinceReport: 0.1, sinceMotion: 0.1, wantsLock: false) == .none)

// Key repeat: the key pressed last, after the delay, at the period, no bursts.
var rep = KeyRepeat()
assert(rep.due(at: 0) == nil)
rep.pressed(0x41, at: 0)
assert(rep.due(at: KeyRepeat.delay - 0.01) == nil)
assert(rep.due(at: KeyRepeat.delay) == 0x41)
assert(rep.due(at: KeyRepeat.delay + 0.01) == nil)
assert(rep.due(at: KeyRepeat.delay + KeyRepeat.period) == 0x41)
assert(rep.due(at: 10) == 0x41 && rep.due(at: 10.001) == nil)   // a stalled timer: one repeat
rep.pressed(0x42, at: 20)                                       // B takes over from A
rep.released(0x41)
assert(rep.key == 0x42 && rep.due(at: 20 + KeyRepeat.delay) == 0x42)
rep.released(0x42)
assert(rep.key == nil && rep.due(at: 100) == nil)
rep.pressed(0x20, at: 0); rep.cancel()
assert(rep.key == nil && rep.due(at: 5) == nil)
rep.pressed(0x20, at: .nan)
assert(rep.key == nil)
assert(abs(KeyRepeat.delay - 0.5) < 1e-9 && abs(1 / KeyRepeat.period - 30) < 1e-9)   // Windows' defaults
print("SWIFT-OK")
'''

with tempfile.TemporaryDirectory(prefix='madeira-hwinput-') as tmp:
    tmp = Path(tmp)
    src, exe = tmp / 'main.swift', tmp / 'check'
    src.write_text('import Foundation\n#if canImport(CoreGraphics)\nimport CoreGraphics\n#endif\n'
                   + pure + layout + swift_tests)
    subprocess.run([os.environ.get('SWIFTC', 'swiftc'), str(src), '-o', str(exe)], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
check('SWIFT-OK' in out, 'Swift pure-section checks')
m = re.search(r'^MAPPED (\d+)$', out, re.M)
check(m is not None and int(m.group(1)) >= 2000, 'the mouse/touch mapping comparison ran over every case')
hid_to_vk = {int(u): int(v) for u, v in re.findall(r'^MAP (\d+) (-?\d+)$', out, re.M)}
print(f'PASS: pure mapping ({len(hid_to_vk)} HID usages mapped), button edges, held sets, '
      'carry, wheel, classifier, desktop cursor, stick velocity; mouse mapping = touch mapping '
      f'({m.group(1) if m else "?"} cases), clip clamp, MouseCapture, follow route, lock, key repeat')

# ---------------------------------------------------- 2. Wine scan codes ---
input_c = wine / 'dlls/win32u/input.c'
if not input_c.exists():
    sys.exit(f'FAIL: Wine source not found at {wine}; check out the wine submodule or set WINE_SRC')

vk_values = {}
for header in ('include/winuser.rh', 'include/winuser.h', 'include/kbd.h'):
    for name, val in re.findall(r'#define\s+VK_(\w+)\s+(?:\(\w+\))?(0x[0-9A-Fa-f]+|\d+)\b',
                                (wine / header).read_text(errors='replace')):
        vk_values.setdefault(name, int(val, 0))
kbd = (wine / 'include/kbd.h').read_text(errors='replace')
kbd_type = 4                                            # kbd.h's default KBD_TYPE
macros = dict(re.findall(r'#define\s+([TXY][0-9A-F]{2})\s+(.+)', kbd))


def macro_vk(token):
    body = macros[token].strip()
    if re.fullmatch(r"'.'", body):
        return ord(body[1])
    m = re.fullmatch(r'_EQ\((\w+)\)', body)
    if m:
        return vk_values.get(m.group(1), -1)
    m = re.fullmatch(r'_NE\(([^)]*)\)', body)
    if m:
        return vk_values.get(m.group(1).split(',')[kbd_type - 1].strip(), -1)
    raise ValueError(f'unparsed kbd.h macro {token} = {body}')


text = input_c.read_text(errors='replace')
main_table = re.search(r'static const USHORT vsc_to_vk\[\] =\s*\{(.*?)\};', text, re.S).group(1)
vsc2vk = {}
for vsc, entry in enumerate(t.strip() for t in main_table.split(',') if t.strip()):
    vk = macro_vk(entry.split('|')[0].strip())
    if vk != vk_values['_none_']:
        vsc2vk[vsc] = vk
for table, base in (('vsc_to_vk_e0', 0x100), ('vsc_to_vk_e1', 0x200)):
    body = re.search(r'static const VSC_VK %s\[\] =\s*\{(.*?)\};' % table, text, re.S).group(1)
    for vsc, entry in re.findall(r'\{\s*(0x[0-9a-fA-F]+)\s*,\s*([TXY][0-9A-F]{2})[^}]*\}', body):
        vk = macro_vk(entry)
        if vk != vk_values['_none_']:
            vsc2vk[base + int(vsc, 16)] = vk

# NtUserMapVirtualKeyEx(code, MAPVK_VK_TO_VSC_EX): the generic modifiers and the
# numpad virtual keys are first replaced, then the lowest scan-table index whose
# virtual key matches wins; an E0/E1 position is returned as 0xe0xx/0xe1xx.
assert 'case VK_NUMPAD8: code = VK_UP; break;' in text, "Wine's VK_TO_VSC remapping changed"
remap = {'SHIFT': 'LSHIFT', 'CONTROL': 'LCONTROL', 'MENU': 'LMENU', 'NUMPAD0': 'INSERT', 'NUMPAD1': 'END',
         'NUMPAD2': 'DOWN', 'NUMPAD3': 'NEXT', 'NUMPAD4': 'LEFT', 'NUMPAD5': 'CLEAR', 'NUMPAD6': 'RIGHT',
         'NUMPAD7': 'HOME', 'NUMPAD8': 'UP', 'NUMPAD9': 'PRIOR', 'DECIMAL': 'DELETE'}
remap = {vk_values[a]: vk_values[b] for a, b in remap.items()}


def vk_to_vsc_ex(vk):
    vk = remap.get(vk, vk)
    for idx in range(0x300):
        if idx in vsc2vk and (vsc2vk[idx] & 0xff) == vk:
            return idx + 0xdf00 if idx >= 0x100 else idx
    return 0


# The driver's extended-flag decision, compiled from the production source.
driver = (root / 'build/win32u-unix/driver_ios.c').read_text()
start = driver.index('static UINT winios_key_extended_flag(')
helper = driver[start:driver.index('/* end winios_key_extended_flag */', start)]
names = ('PRIOR', 'NEXT', 'END', 'HOME', 'LEFT', 'UP', 'RIGHT', 'DOWN', 'INSERT', 'DELETE')
c_src = '#include <stdio.h>\ntypedef unsigned int UINT;\n#define KEYEVENTF_EXTENDEDKEY 0x0001\n'
c_src += ''.join(f'#define VK_{nm} 0x{vk_values[nm]:02x}\n' for nm in names)
c_src += helper + r'''
int main(void)
{
    UINT vk, scan;
    while (scanf("%x %x", &vk, &scan) == 2)
        printf("%x %u %u\n", vk, winios_key_extended_flag(vk, scan, 1), winios_key_extended_flag(vk, scan, 0));
    return 0;
}
'''
queries = {vk: vk_to_vsc_ex(vk) for vk in sorted(set(hid_to_vk.values()))}
with tempfile.TemporaryDirectory(prefix='madeira-hwinput-c-') as tmp:
    csrc, cexe = Path(tmp) / 'flag.c', Path(tmp) / 'flag'
    csrc.write_text(c_src)
    subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Werror', str(csrc), '-o', str(cexe)], check=True)
    res = subprocess.run([str(cexe)], input=''.join(f'{vk:x} {sc:x}\n' for vk, sc in queries.items()),
                         check=True, capture_output=True, text=True).stdout
ext_on, ext_off = {}, {}
for line in res.split('\n'):
    if line.strip():
        vk, on, off = line.split()
        ext_on[int(vk, 16)], ext_off[int(vk, 16)] = int(on), int(off)


def sent(usage, nav_e0=True):
    """(scan byte, extended) the driver sends for this HID usage."""
    vk = hid_to_vk[usage]
    ext = (ext_on if nav_e0 else ext_off)[vk]
    return (queries[vk] & 0xff, bool(ext))


# USB HID usage -> PS/2 scan code set 1 (make code, E0/E1 prefix as "extended").
pc = {}
for i, sc in enumerate([0x1E, 0x30, 0x2E, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17, 0x24, 0x25, 0x26, 0x32,
                        0x31, 0x18, 0x19, 0x10, 0x13, 0x1F, 0x14, 0x16, 0x2F, 0x11, 0x2D, 0x15, 0x2C]):
    pc[0x04 + i] = (sc, False)                                             # a-z
for i in range(10):
    pc[0x1E + i] = (0x02 + i, False)                                       # 1-9, 0
pc.update({0x28: (0x1C, False), 0x29: (0x01, False), 0x2A: (0x0E, False), 0x2B: (0x0F, False),
           0x2C: (0x39, False), 0x2D: (0x0C, False), 0x2E: (0x0D, False), 0x2F: (0x1A, False),
           0x30: (0x1B, False), 0x31: (0x2B, False), 0x32: (0x2B, False), 0x33: (0x27, False),
           0x34: (0x28, False), 0x35: (0x29, False), 0x36: (0x33, False), 0x37: (0x34, False),
           0x38: (0x35, False), 0x39: (0x3A, False)})
for i in range(10):
    pc[0x3A + i] = (0x3B + i, False)                                       # F1-F10
pc.update({0x44: (0x57, False), 0x45: (0x58, False),                      # F11, F12
           0x47: (0x46, False), 0x48: (0x1D, True),                        # Scroll Lock, Pause (E1 1D)
           0x49: (0x52, True), 0x4A: (0x47, True), 0x4B: (0x49, True), 0x4C: (0x53, True),
           0x4D: (0x4F, True), 0x4E: (0x51, True), 0x4F: (0x4D, True), 0x50: (0x4B, True),
           0x51: (0x50, True), 0x52: (0x48, True),                         # nav cluster, arrows
           0x53: (0x45, False), 0x54: (0x35, True), 0x55: (0x37, False), 0x56: (0x4A, False),
           0x57: (0x4E, False),
           0x59: (0x4F, False), 0x5A: (0x50, False), 0x5B: (0x51, False), 0x5C: (0x4B, False),
           0x5D: (0x4C, False), 0x5E: (0x4D, False), 0x5F: (0x47, False), 0x60: (0x48, False),
           0x61: (0x49, False), 0x62: (0x52, False), 0x63: (0x53, False),  # numpad
           0x64: (0x56, False), 0x65: (0x5D, True),                        # ISO key, Application
           0x7F: (0x20, True), 0x80: (0x30, True), 0x81: (0x2E, True),     # volume
           0x87: (0x73, False),                                            # JIS ro
           0xE0: (0x1D, False), 0xE1: (0x2A, False), 0xE2: (0x38, False), 0xE3: (0x5B, True),
           0xE4: (0x1D, True), 0xE5: (0x36, False), 0xE6: (0x38, True), 0xE7: (0x5C, True)})
for i, sc in enumerate([0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x76]):
    pc[0x68 + i] = (sc, False)                                             # F13-F24

# Known, documented differences (docs/KEYBOARD_MOUSE.md). Each is pinned to what
# is sent today, so a change in either direction is noticed.
known = {
    0x46: ((0x54, False), 'Print Screen: VK_SNAPSHOT maps to the SysRq position 0x54'),
    0x58: ((0x1C, False), 'numpad Enter: posted as VK_RETURN, so no E0'),
    0x67: ((0x00, False), 'keypad = (VK_OEM_NEC_EQUAL): not in the US layout'),
    0x75: ((0x63, False), 'Help: no PC make code; VK_HELP maps to 0x63'),
    0x77: ((0x00, False), 'Select: not in the US layout'),
    0x85: ((0x00, False), 'keypad comma (VK_SEPARATOR): not in the US layout'),
    0x88: ((0x00, False), 'katakana/hiragana (VK_OEM_COPY): not in the US layout'),
    0x89: ((0x2B, False), 'JIS yen: shares VK_OEM_5 with backslash'),
    0x8A: ((0x00, False), 'convert: not in the US layout'),
    0x8B: ((0x00, False), 'non-convert: not in the US layout'),
    0x90: ((0x00, False), 'Hangul: not in the US layout'),
    0x91: ((0x00, False), 'Hanja: not in the US layout'),
}
check(set(hid_to_vk) == set(pc) | set(known),
      f'mapped usages differ from the reference: extra={sorted(set(hid_to_vk) - set(pc) - set(known))} '
      f'missing={sorted((set(pc) | set(known)) - set(hid_to_vk))}')
for usage, want in sorted(pc.items()):
    if usage in hid_to_vk:
        check(sent(usage) == want, f'HID 0x{usage:02x} vk=0x{hid_to_vk[usage]:02x}: '
                                   f'sent {sent(usage)} want {want}')
for usage, (want, why) in sorted(known.items()):
    if usage in hid_to_vk:
        check(sent(usage) == want, f'known difference changed, HID 0x{usage:02x} ({why}): sent {sent(usage)}')
print(f'PASS: {len(pc)} keys send the PC scan code and E0 flag through Wine\'s US layout '
      f'({len(known)} documented differences pinned)')

# ------------------------------------------------ 3. Direct-mode cursor (C) ---
cursor_test = r'''
#include "WiniosCursor.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>

static int notified;
static void notify(void) { notified++; }

static void *hammer(void *arg)
{
    int i, base = (int)(long)arg;
    unsigned char px[4 * 4 * 4];
    for (i = 0; i < 20000; i++)
    {
        winios_direct_cursor_pos(base + i % 100, i % 50);
        if (i % 1000 == 0)
        {
            memset(px, i & 0xff, sizeof(px));
            winios_direct_cursor_set(1, 4, 4, 1, 2, px);
            winios_direct_cursor_show(i % 2000 == 0);
        }
    }
    return NULL;
}

int main(void)
{
    struct winios_direct_cursor_state s;
    unsigned char img[3 * 2 * 4], out[WINIOS_CURSOR_MAX * WINIOS_CURSOR_MAX * 4];
    pthread_t th[4];
    int i;

    /* Inert until the app enables it: the driver asks first and does nothing. */
    assert(!winios_direct_cursor_wanted());
    winios_direct_cursor_pos(5, 6);
    winios_direct_cursor_show(1);
    winios_direct_cursor_clip(1, 2, 3, 4);
    winios_direct_cursor_warp(7, 8);
    winios_direct_cursor_reader(3);
    winios_direct_cursor_get(&s);
    assert(s.x == 0 && s.y == 0 && !s.shown && s.reports == 0);
    assert(s.clip_right == 0 && s.warps == 0 && s.reader == 0);

    winios_direct_cursor_enable(notify);
    assert(winios_direct_cursor_wanted());

    /* The first report notifies even untracked; later positions only while tracking. */
    winios_direct_cursor_pos(10, 20);
    assert(notified == 1);
    winios_direct_cursor_pos(11, 21);                 /* not tracking, and a notification is pending */
    winios_direct_cursor_get(&s);
    assert(s.x == 11 && s.y == 21 && s.reports == 1 && !s.shown);
    winios_direct_cursor_pos(12, 22);
    assert(notified == 1);                            /* untracked: stored, no notification */
    winios_direct_cursor_track(1);
    winios_direct_cursor_pos(12, 22);
    assert(notified == 2);                            /* tracked: every report counts, moved or not */
    winios_direct_cursor_pos(13, 23);
    assert(notified == 2);                            /* coalesced until the app takes the state */
    winios_direct_cursor_get(&s);
    assert(s.x == 13 && s.y == 23);
    winios_direct_cursor_track(0);

    /* Visibility notifies on change only. */
    winios_direct_cursor_show(1);
    assert(notified == 3);
    winios_direct_cursor_get(&s);
    assert(s.shown == 1);
    winios_direct_cursor_show(1);
    assert(notified == 3);
    winios_direct_cursor_show(0);
    assert(notified == 4);
    winios_direct_cursor_get(&s);
    assert(s.shown == 0);

    /* Images: copied, serial bumps, oversized or empty ones are refused. */
    for (i = 0; i < (int)sizeof(img); i++) img[i] = (unsigned char)i;
    winios_direct_cursor_set(7, 3, 2, 1, 1, img);
    assert(notified == 5);
    memset(img, 0, sizeof(img));                      /* the caller's buffer is not kept */
    winios_direct_cursor_get(&s);
    assert(s.w == 3 && s.h == 2 && s.hot_x == 1 && s.hot_y == 1 && s.image_serial == 1);
    memset(&s, 0, sizeof(s));
    assert(winios_direct_cursor_copy_image(out, sizeof(out), &s) == 1);
    assert(s.w == 3 && s.h == 2 && s.image_serial == 1);
    for (i = 0; i < 3 * 2 * 4; i++) assert(out[i] == (unsigned char)i);
    assert(winios_direct_cursor_copy_image(out, 3 * 2 * 4 - 1, NULL) == 0);   /* does not fit */
    winios_direct_cursor_set(8, WINIOS_CURSOR_MAX + 1, 1, 0, 0, out);
    winios_direct_cursor_set(8, 0, 1, 0, 0, out);
    winios_direct_cursor_set(8, 1, 1, 0, 0, NULL);
    winios_direct_cursor_get(&s);
    assert(s.image_serial == 1 && s.w == 3);

    /* Reports from several threads while the app reads. */
    winios_direct_cursor_track(1);
    for (i = 0; i < 4; i++) pthread_create(&th[i], NULL, hammer, (void *)(long)(i * 1000));
    for (i = 0; i < 2000; i++)
    {
        winios_direct_cursor_get(&s);
        if (s.image_serial > 1) assert(s.w == 4 && s.h == 4 && s.hot_x == 1 && s.hot_y == 2);
        winios_direct_cursor_copy_image(out, sizeof(out), NULL);
    }
    for (i = 0; i < 4; i++) pthread_join(th[i], NULL);
    winios_direct_cursor_get(&s);
    assert(s.w == 4 && s.image_serial > 1);

    /* madeira-bcd 2026-10-18: clip, warp and reader reports. */
    {
        int before;
        winios_direct_cursor_track(0);
        winios_direct_cursor_get(&s);                     /* re-arm */
        before = notified;
        winios_direct_cursor_clip(0, 0, 1112, 832);       /* a change notifies */
        assert(notified == before + 1);
        winios_direct_cursor_get(&s);
        assert(s.clip_left == 0 && s.clip_top == 0 && s.clip_right == 1112 && s.clip_bottom == 832);
        winios_direct_cursor_clip(0, 0, 1112, 832);       /* the same clip: nothing */
        assert(notified == before + 1);
        winios_direct_cursor_clip(2, 0, 1110, 832);
        assert(notified == before + 2);
        winios_direct_cursor_get(&s);
        assert(s.clip_left == 2 && s.clip_right == 1110);
        /* A warp always notifies, untracked too, and is counted. */
        before = notified;
        winios_direct_cursor_warp(556, 416);
        assert(notified == before + 1);
        winios_direct_cursor_get(&s);
        assert(s.x == 556 && s.y == 416 && s.warps == 1);
        winios_direct_cursor_warp(556, 416);
        winios_direct_cursor_warp(10, 10);                /* coalesced: one notification, two warps */
        assert(notified == before + 2);
        winios_direct_cursor_get(&s);
        assert(s.warps == 3 && s.x == 10);
        /* The reader bits notify on a change only. */
        before = notified;
        winios_direct_cursor_reader(0);
        assert(notified == before);
        winios_direct_cursor_reader(WINIOS_READER_RAW_CAPTURE);
        assert(notified == before + 1);
        winios_direct_cursor_get(&s);
        assert(s.reader == WINIOS_READER_RAW_CAPTURE);
        winios_direct_cursor_reader(WINIOS_READER_RAW_CAPTURE);
        assert(notified == before + 1);
        winios_direct_cursor_reader(0);
        winios_direct_cursor_get(&s);
        assert(s.reader == 0);
    }
    printf("CURSOR-OK %d notifications\n", notified);
    return 0;
}
'''
winios = app / 'Winios'
with tempfile.TemporaryDirectory(prefix='madeira-hwinput-cursor-') as tmp:
    csrc, cexe = Path(tmp) / 'cursor_test.c', Path(tmp) / 'cursor_test'
    csrc.write_text(cursor_test)
    subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Wextra', '-Werror', '-pthread', f'-I{winios}',
                    str(csrc), str(winios / 'WiniosCursor.c'), '-o', str(cexe)], check=True)
    out = subprocess.run([str(cexe)], check=True, capture_output=True, text=True).stdout
check('CURSOR-OK' in out, 'WiniosCursor.c checks')
print('PASS: direct-mode cursor state: inert until enabled, first-report and tracked notifications, '
      'coalescing, visibility, image copy and bounds, concurrent reports; clip, warp and reader reports')

# ---------------------------------------- 3b. The wineserver's reader report ---
# madeira-bcd 2026-10-18: madeira_report_mouse_reader, compiled from the production
# queue_ios.c against stub structures: raw input without legacy messages or capturing
# the mouse (DirectInput 8 exclusive) and a low-level hook of the foreground process
# are reported; plain raw input, another process's hook and nothing are not.
queue = (root / 'build/wineserver/queue_ios.c').read_text()
start = queue.index('static void madeira_report_mouse_reader(')
reader_fn = queue[start:queue.index('\n}\n', start) + 3]
reader_harness = r'''
#include <assert.h>
#include <stdio.h>
#define RIDEV_INPUTSINK     0x00000100
#define RIDEV_NOLEGACY      0x00000030
#define RIDEV_CAPTUREMOUSE  0x00000200
#define WH_MOUSE_LL 14
struct rawinput_device { unsigned int usage, flags; };
struct process { const struct rawinput_device *rawinput_mouse; };
struct thread { struct process *process; };
struct desktop { struct thread *ll_hook; };
static struct thread *get_first_global_hook( struct desktop *d, int id ) { return id == WH_MOUSE_LL ? d->ll_hook : NULL; }
static unsigned int reported = 99, calls;
__attribute__((weak)) void winios_direct_cursor_reader( unsigned int flags ) { reported = flags; calls++; }
''' + reader_fn + r'''
int main(void)
{
    struct rawinput_device plain = { 0, 0 }, sink = { 0, RIDEV_INPUTSINK }, nolegacy = { 0, RIDEV_NOLEGACY | RIDEV_INPUTSINK },
                           capture = { 0, RIDEV_CAPTUREMOUSE }, exclude = { 0, 0x10 }, pageonly = { 0, 0x20 };
    struct process game = { NULL }, other = { NULL };
    struct thread fg = { &game }, hooker = { &game }, stranger = { &other };
    struct desktop d = { NULL };
    madeira_report_mouse_reader( &d, &fg ); assert(reported == 0);
    game.rawinput_mouse = &plain; madeira_report_mouse_reader( &d, &fg ); assert(reported == 0);
    game.rawinput_mouse = &sink; madeira_report_mouse_reader( &d, &fg ); assert(reported == 0);
    game.rawinput_mouse = &exclude; madeira_report_mouse_reader( &d, &fg ); assert(reported == 0);   /* half of NOLEGACY */
    game.rawinput_mouse = &pageonly; madeira_report_mouse_reader( &d, &fg ); assert(reported == 0);
    game.rawinput_mouse = &nolegacy; madeira_report_mouse_reader( &d, &fg ); assert(reported == 1);
    game.rawinput_mouse = &capture; madeira_report_mouse_reader( &d, &fg ); assert(reported == 1);
    game.rawinput_mouse = NULL; d.ll_hook = &stranger; madeira_report_mouse_reader( &d, &fg ); assert(reported == 0);
    d.ll_hook = &hooker; madeira_report_mouse_reader( &d, &fg ); assert(reported == 2);
    game.rawinput_mouse = &nolegacy; madeira_report_mouse_reader( &d, &fg ); assert(reported == 3);
    printf("READER-OK %u\n", calls);
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-hwinput-reader-') as tmp:
    csrc, cexe = Path(tmp) / 'reader.c', Path(tmp) / 'reader'
    csrc.write_text(reader_harness)
    subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Werror', str(csrc), '-o', str(cexe)], check=True)
    out = subprocess.run([str(cexe)], check=True, capture_output=True, text=True).stdout
check('READER-OK' in out, "the wineserver's mouse reader report")
check('madeira_report_mouse_reader( desktop, foreground );   /* madeira-bcd' in queue
      and queue.index('madeira_report_mouse_reader( desktop, foreground );') < queue.index('release_object( foreground );', queue.index('dispatch_rawinput_message( desktop, &raw_msg );')),
      'queue_mouse_message reports the reader while it holds the foreground thread')
check(re.search(r'extern void winios_direct_cursor_reader\( unsigned int flags \) __attribute__\(\(weak\)\);', queue) is not None
      and 'if (!winios_direct_cursor_reader) return;' in reader_fn,
      'the server reports through a weak symbol and does nothing without it')
print("PASS: the wineserver reports raw-input capture and low-level hooks of the foreground program, nothing else")

# ------------------------------------------------------- 4. Source wiring ---
# The driver: reports only in direct mode and only once the app asked; the
# desktop compositor path is unchanged; what the program sees is unchanged.
check('if (!winios_desktop_mode() || !winios_cursor_set) return;' not in driver
      and 'cursor_set = winios_direct_cursor_set;' in driver
      and 'if (!winios_direct_cursor_on()) return;' in driver,
      'winios_drv_set_cursor sends direct-mode cursors to WiniosCursor.c, only when wanted')
check('if (winios_desktop_mode())       winios_user_driver.pSetCursor           = winios_drv_set_cursor;' in driver
      and 'else if (winios_direct_cursor_set) winios_user_driver.pSetCursor         = winios_drv_set_cursor;' in driver,
      'pSetCursor: compositor in the desktop session, direct-mode report otherwise')
check('winios_user_driver.pSetCursorPos = winios_drv_set_cursor_pos;' in driver
      and re.search(r'static BOOL winios_drv_set_cursor_pos\( INT x, INT y \)\s*\{\s*'
                    r'if \(!winios_direct_cursor_on\(\)\) return TRUE;\s*'
                    r'if \(winios_direct_cursor_warp\) winios_direct_cursor_warp\( x, y \);\s*'
                    r'else winios_direct_cursor_pos\( x, y \);\s*return TRUE;\s*\}',
                    driver) is not None,
      'pSetCursorPos reports a warp (the position without the app hook) and succeeds, as nulldrv does')
report = driver[driver.index('static void winios_report_cursor_pos(void)'):driver.index('static BOOL winios_drv_set_cursor_pos(')]
check('clip = desktop_shm->cursor.clip;' in report
      and 'if (winios_direct_cursor_clip) winios_direct_cursor_clip( clip.left, clip.top, clip.right, clip.bottom );' in report
      and report.index('winios_direct_cursor_clip( clip.left') < report.index('winios_direct_cursor_pos( x, y );'),
      'each position report carries the clip rectangle read in the same snapshot, clip first')
check('if ((flags & MOUSEEVENTF_MOVE) && winios_direct_cursor_on()) winios_report_cursor_pos();' in driver,
      'every posted move reports where the server put the cursor')
check(re.search(r'static int winios_direct_cursor_on\(void\)\s*\{\s*return !winios_desktop_mode\(\) && '
                r'winios_direct_cursor_wanted && winios_direct_cursor_wanted\(\);', driver) is not None,
      'the reports are gated on direct mode and the app')
for sym in ('winios_direct_cursor_wanted', 'winios_direct_cursor_set', 'winios_direct_cursor_show',
            'winios_direct_cursor_pos', 'winios_direct_cursor_clip', 'winios_direct_cursor_warp'):
    check(re.search(sym + r'\([^;]*\)\s*__attribute__\(\(weak\)\);', driver) is not None, f'{sym} is a weak import')
bridging = (app / 'Madeira-Bridging-Header.h').read_text()
check('#import "Winios/WiniosCursor.h"' in bridging, 'the bridging header imports WiniosCursor.h')

cv = (app / 'ContentView.swift').read_text()
for phase in ('began', 'moved', 'ended', 'cancelled'):
    check(f'HardwareInput.shared.interceptTouches(touches, event, .{phase})' in cv,
          f'MetalBackedView touches{phase.capitalize()} must ask HardwareInput first')
check(cv.count('PointerFallback.install(on: self)') == 2, 'both MetalBackedView initialisers install the pointer fallback')
check(cv.count('if HardwareInput.shared.handlesTyping { return }') == 2, 'text bridge stands aside for a hardware keyboard')
# Wave 3 (2026-10-19): the developer interface's pointer panel and its HardwareInputSettings row are deleted; mouse speed, the
# right stick as a mouse, the pointer-lock button and the AssistiveTouch note are BCD's Settings › Controls (the same
# HardwareInput calls; check-bcd-settings.py lists each).
bcd_live = (app / 'BCDSettingsLive.swift').read_text()
check('HardwareInputSettings' not in cv + (app / 'HardwareInput.swift').read_text()
      and 'mouse.togglePointerLock(why:' in bcd_live and 'mouse.dismissAssistiveTouchHint()' in bcd_live
      and 'input.sensMouse =' in bcd_live and 'input.padRightStickMouse = on' in bcd_live,
      "settings for the mouse (speed, right stick, pointer lock, AssistiveTouch note) are in BCD's Settings › Controls")
for key, default in (('sensMouse', '1.0'), ('ignoreTouchesWithMouse', 'true'), ('padRightStickMouse', 'false')):
    check(f'"{key}": {key}' in cv and f'{key} = j["{key}"]' in cv and f'?? {default}' in cv,
          f'InputSettings persists {key}')
check('HardwareInput.shared.start()' in (app / 'MadeiraApp.swift').read_text(), 'MadeiraApp starts HardwareInput')
plist = (app / 'Info.plist').read_text()
check(re.search(r'<key>UIApplicationSupportsIndirectInputEvents</key>\s*<true/>', plist) is not None,
      'Info.plist opts into indirect input events')
pbx = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
check('HardwareInput.swift in Sources */,' in pbx and 'path = HardwareInput.swift;' in pbx, 'HardwareInput.swift is built')
check('WiniosCursor.c in Sources */,' in pbx and re.search(r'path = "?Winios/WiniosCursor\.c"?;', pbx) is not None,
      'WiniosCursor.c is built')
switches = ('MADEIRA_HWINPUT', 'MADEIRA_INPUT_FOCUS', 'MADEIRA_DIRECT_CURSOR', 'MADEIRA_POINTER_ABSOLUTE',
            'MADEIRA_POINTER_LOCK', 'MADEIRA_POINTER_AUTOLOCK')
for switch in switches:
    check(f'flag("{switch}", defaultOn: true)' in source, f'{switch} switch, default on')
glue = source.split('// MARK: - Device glue', 1)[1]
# Every path to the program goes through the focus decision.
check('keys.wanted(focused: keyboardFocused)' in glue and 'keys.press(vk, focused: keyboardFocused)' in glue,
      'keys reach the program only with keyboard focus')
check('if r == .relative { postMotion(dx, -dy) }' in glue, 'GCMouse motion is posted only on the relative route')
check('let blocked = route == .blocked' in glue, 'the wheel is gated on focus')
check('let allowed = baseFocused ? want : []' in glue, 'UIKit pointer buttons are gated on focus')
check('guard let p = profile, HardwareInput.shared.baseFocused, !GamepadInput.shared.keyboardMouseOn else { return }' in glue,
      'the right-stick mouse is gated on focus, and stands down in keyboard-and-mouse controller mode')
check('presentedViewController != nil' in glue and 'fr is UIKeyInput' in glue
      and 'UIApplication.shared.applicationState == .active' in glue,
      'focus accounts for presented controllers, other text inputs and the app state')
check('GCController' not in source.split('final class PadStickMouse', 1)[0].split('// MARK: - Device glue', 1)[1]
      .replace('GCControllerButtonInput', '').replace('GCControllerDirectionPad', ''),
      'only PadStickMouse may touch controllers; GamepadInput owns them')
check(re.search(r'm\.scroll\.valueChangedHandler = \{ \[weak self\] _, x, y in\s*'
                r'let wheel = GCMouseWheel\.axes\(Double\(x\), Double\(y\)\)\s*'
                r'self\?\.scrolled\(wheel\.dx, wheel\.dy\)', glue) is not None,
      'the GCMouse wheel goes through GCMouseWheel.axes')
docs_path = root / 'docs/KEYBOARD_MOUSE.md'
if docs_path.is_file():
    docs = docs_path.read_text()
    for name in switches + ('MADEIRA_NAV_KEYS_E0', 'sensMouse', 'padRightStickMouse', 'ignoreTouchesWithMouse',
                            'Ctrl+Alt+P', 'MADEIRA_POINTER_FOLLOW', 'MADEIRA_KEY_REPEAT', 'UIPointerLockState',
                            'UIRequiresFullScreen'):
        check(name in docs, f'docs/KEYBOARD_MOUSE.md mentions {name}')
else:
    print('SKIP: docs/KEYBOARD_MOUSE.md is absent (the public export has no docs/)')

# madeira-bcd 2026-10-18: the absolute route maps like a touch, the drawn cursor scales
# by the live monitor, the lock is checked with iPadOS, the overlay window lets the
# held pointer through, and the per-event logs are rate-limited.
check('let s = guestPoint(p, in: view)' in glue and 'DirectCursorOverlay.screenW' not in source
      and 'static let screenW = 1024' not in source,
      'the absolute route maps through the presented rect and the live monitor, never a fixed 1024x768')
check('winios_desktop_point_from_window(Double(w.x), Double(w.y), &px, &py)' in glue
      and 'ScreenMap.toGuest(x: Double(q.x), y: Double(q.y)' in glue,
      'desktop sessions use the compositor mapping, game sessions the presented rect')
check('let kx = host.width / CGFloat(max(guestW, 1)), ky = host.height / CGFloat(max(guestH, 1))' in source,
      'the drawn cursor scales by the live monitor, x and y separately')
check('pos = ScreenMap.clamp(x: last.x, y: last.y, clipLeft: cursorState.clip_left' in glue,
      'the drawn cursor on the absolute route honours ClipCursor')
check('scene?.pointerLockState?.isLocked' in glue and 'UIPointerLockState.didChangeNotification' in glue,
      "the lock is read back from iPadOS, and its changes observed")
check('let mouse = base && (!Self.focusEnabled || routeLocked || over || !gameButtons.isEmpty)' in glue,
      'a refused lock holds the mouse only for a program that reads motion')
check('wantsLock: wantsLock)' in glue and 'let wantsLock = followMode != .always' in glue,
      'MADEIRA_POINTER_FOLLOW=1 keeps the automatic lock off the game\'s hidden cursor')
check('case .auto: return capture.reading == .position && Self.pointerLockAvailable' in glue
      and 'case .always: return true' in glue and 'case .off: return false' in glue,
      'automatic following needs a position reader on an iPad; 1 always follows; 0 never')
check('Self.sessionValue("MADEIRA_POINTER_FOLLOW")' in glue and 'Self.sessionValue("MADEIRA_KEY_REPEAT") == "1"' in glue,
      'the two switches are read per session, the game\'s own lines first; key repeat is opt-in')
check('else if keysPosted.down.contains(vk), keyRepeatEnabled {' in glue
      and 'guard let vk = keyRepeat.key, keyboardFocused, keysPosted.down.contains(vk) else {' in glue,
      'key repeat only repeats a key the program holds, with keyboard focus')
cv_controls = cv[cv.index('final class ControlsWindow: UIWindow {'):cv.index('enum TouchControlsHost {')]
check('if HardwareInput.isPointerEvent(event), HardwareInput.shared.pointerHeldByProgram,' in cv_controls
      and cv_controls.index('HardwareInput.isPointerEvent(event)') < cv_controls.index('library.menuButtonRect.contains(point)')
      and '!(library.current != nil && (library.menu || library.launching)) { return nil }' in cv_controls,
      "the overlay window lets the held pointer through, never with the menu or starting screen up")
winios_m = (app / 'Winios/Winios.m').read_text()
check('if (total <= 40) {' in winios_m and '} else if (total % 5000 == 0) {' in winios_m
      and 'fprintf(stderr, "[winios] drain type=%u x=%d y=%d flags=0x%x\\n", e.type, e.x, e.y, e.flags); fflush(stderr);\n        if (e.type' not in winios_m,
      'the drain logs its first 40 events, then a count, not a flushed line per mouse move')
check('if (n <= 60) {' in winios_m and '} else if (n % 1000 == 0) {' in winios_m,
      'post_key logs its first 60 keys, then a count')
# 2026-10-28 (privacy; upstream 448391e logged none): a key that types text is logged as "char", never its code, in the three places
# that log keys (the first keys of a session may be a sign-in name or password, and logs are sent).
drv = (root / 'build/win32u-unix/driver_ios.c').read_text()
check('static int winios_vk_types_text(int vk) {' in winios_m
      and 'return vk == 0x20 || (vk >= 0x30 && vk <= 0x5A) || (vk >= 0x60 && vk <= 0x6F) || (vk >= 0xBA && vk <= 0xE2);' in winios_m
      and 'if (winios_vk_types_text(vk)) fprintf(stderr, "[winios] post_key vk=char down=%d\\n", down);' in winios_m
      and 'if (e.type == WINIOS_EV_KEY && (e.x == 0x20 || (e.x >= 0x30 && e.x <= 0x5A) || (e.x >= 0x60 && e.x <= 0x6F) || (e.x >= 0xBA && e.x <= 0xE2)))' in winios_m
      and 'int text = vk == 0x20 || (vk >= 0x30 && vk <= 0x5A) || (vk >= 0x60 && vk <= 0x6F) || (vk >= 0xBA && vk <= 0xE2);' in drv
      and 'drv_post_key #%u vk=char flags=0x%x' in drv,
      'a text key is logged as "char" (no code) by post_key, the drain and the driver')
print('PASS: driver reports, touch interception, focus gating, text-bridge guard, settings, Info.plist, '
      'project, switches and docs wired')

if failures:
    for f in failures:
        print('FAIL:', f)
    sys.exit(1)
