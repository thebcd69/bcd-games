// Portions copyright 2026 alantangok and NevesHenry: code taken from https://github.com/willfaust/Madeira (pull request #413; commits abca30a, 91e1e57, 9806664 and facd2cc)
// under GPL-3.0-or-later and modified for BCD in October 2026. See THIRD-PARTY-NOTICES.md.
// Portions copyright 2026 Paulo Eduardo Correa (pauloec): the native iPhone Duo interface (Liquid Glass, system tabs, controller navigation, Windows desktop on Home), from thebcd69/bcd-games pull request #43; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
import Foundation
import QuartzCore
import UIKit
import UIKit.UIGestureRecognizerSubclass
import SwiftUI
import Combine
import GameController
import ObjectiveC

// ============================================================================
// Hardware keyboard and mouse/trackpad input.
//
// Until now every event the app posted into Wine started as a finger. A
// Bluetooth or USB keyboard and mouse already produce the events Windows
// expects; this file's job is to not lose them on the way, to show the
// program's cursor, and to give the program input only while it has focus.
//
// KEYBOARD: GCKeyboard, not UIKit presses. UIKit's press pipeline is a text
// pipeline. Modifiers arrive only as `modifierFlags` on the next key, holding a
// key is re-delivered as repeated downs with no ups, and UIKeyCommand matches
// whole chords. GCKeyboard is the raw HID path: one callback per physical
// transition, modifiers as ordinary keys, no repeat.
//
// `GCKeyCode.rawValue` IS the USB HID keyboard usage ID (0x04 = 'a', 0xE1 =
// left shift). The virtual-key map below is keyed on that integer rather than
// on GCKeyCode's named constants, which are an SDK-dependent subset (F13-F24
// and most international keys have none).
//
// Scan codes and the extended-key bit are not chosen here: driver_ios.c derives
// them from the virtual key with MAPVK_VK_TO_VSC_EX and marks the navigation
// keys extended, and the wineserver synthesises the generic VK_SHIFT/
// VK_CONTROL/VK_MENU from the left/right ones. Posting VK_LSHIFT is therefore
// both more precise than VK_SHIFT and fully compatible.
// tests/host/check-hardware-input.py checks the combined HID -> VK ->
// scan code result against Wine's US layout.
//
// FOCUS: GCKeyboard and GCMouse report to the app whatever the user is doing
// in it. Input reaches the program only while it has focus:
//   * the app is active and in the foreground, the game view is on screen and
//     nothing (a sheet, an alert, a picker) is presented over it;
//   * keyboard: no other text input (a text field) is first responder. The
//     game view's own text bridge counts as the program's;
//   * mouse: the pointer is over the game view (iPad, where UIKit reports the
//     pointer's position), or the last click or tap landed on it (iPhone
//     AssistiveTouch, which reports no position), or pointer lock is on, or a
//     button that went down on the game view is still held.
// Losing focus releases everything the program was told is held (key-ups and
// button-ups are posted); a key or button pressed while the program did not
// have focus stays away from it until it is released.
//
// MOUSE: GCMouse delivers raw HID deltas (iPad, and iPhone with
// AssistiveTouch). Where GCMouse enumerates a mouse but never reports, the same
// input arrives as UIKit indirect-pointer events (hover, pointer drag, scroll,
// and button "touches"; needs UIApplicationSupportsIndirectInputEvents in
// Info.plist); `PointerFallback` feeds it into the same path. How motion
// reaches the program depends on its cursor:
//   * The program SHOWS a cursor and UIKit reports the pointer's position
//     (iPad, unlocked): the program's cursor is put where the pointer is
//     (ABSOLUTE), so the drawn cursor sits exactly under the hidden iOS
//     pointer and never drifts away from it.
//   * Otherwise (the program hides its cursor, as mouse-look does; pointer
//     lock; iPhone): device motion is posted RELATIVE. The wineserver adds it
//     to its own cursor and hands raw input exactly the delta before any
//     ClipCursor clamping, so aiming never stalls at a screen edge.
// On iPad, while the program hides its cursor and the pointer is over the game
// view, the pointer is locked (hidden and pinned, deltas keep coming at the
// screen edges); the lock is released when the program shows a cursor again.
//
// CURSOR: with no virtual desktop (a program on the game view) nothing drew a
// Windows cursor: the finger is the pointer there. While the mouse is in use,
// DirectCursorOverlay draws the program's own cursor image at Wine's cursor
// position over the game view, hidden whenever the program hides its cursor.
// driver_ios.c reports image, visibility and position through
// Winios/WiniosCursor.c. A finger on the game view hides it until the mouse
// moves again. The desktop session keeps its compositor-drawn cursor.
//
// Gamepads are NOT handled here: GamepadInput.swift owns every GCController
// and its XInput slots. The one exception is opt-in and read-only: "Right
// stick controls mouse" (`PadStickMouse`) turns a controller's right stick
// into velocity mouse motion for programs without controller support.
//
// Switches (Documents/bcd.cfg `env.NAME = value`, or the process
// environment): MADEIRA_HWINPUT=0 turns all of this off (upstream behaviour);
// MADEIRA_INPUT_FOCUS=0 gives the program input regardless of focus;
// MADEIRA_DIRECT_CURSOR=0 draws no cursor on the game view and keeps motion
// relative there; MADEIRA_POINTER_ABSOLUTE=0 keeps motion relative
// everywhere; MADEIRA_POINTER_LOCK=0 never locks the pointer;
// MADEIRA_POINTER_AUTOLOCK=0 locks it only on request (Ctrl+Alt+P or the lock
// button). Documents/bcd-input.json holds the settings: `sensMouse` (mouse
// gain, default 1.0), `padRightStickMouse` (default false) and
// `ignoreTouchesWithMouse` (default true; false disables the AssistiveTouch
// click filter). The driver side has MADEIRA_NAV_KEYS_E0. Log tag:
// `[hwinput]`. See docs/KEYBOARD_MOUSE.md.
//
// Modified by BCD (thebcd69), 2026-10-18 (a tester's Warcraft II on an iPad:
// "the iPad mouse and the game mouse aren't perfectly synchronized ... the iPad
// mouse pops out at the sides and the game mouse doesn't move"):
//   * The absolute route mapped the pointer onto a fixed 1024x768 screen fitted
//     in the view, and the drawn cursor used the same scale, while a session's
//     virtual monitor is its own size (1112x832 there) laid out by the
//     session's Fit/Fill/Stretch/Aspect. Now both go through the presented game
//     rect and the live monitor size, exactly as a finger's touch does, and the
//     drawn cursor honours the program's ClipCursor and SetCursorPos.
//   * The lock was assumed to hold once asked for. iPadOS grants it only to a
//     full-screen scene; the app now reads UIPointerLockState and logs it.
//   * Where the pointer is not locked (iPadOS refused, or the mouse arrives
//     through UIKit), a program that hides its cursor but draws its own where
//     Wine's is (MouseCapture) gets the pointer's position instead of relative
//     moves that drift away from the pointer (env.MADEIRA_POINTER_FOLLOW).
//   * While the program holds the mouse, BCD's overlay window lets the pointer
//     through, so it neither shows over BCD's buttons nor takes the mouse away.
//   * Opt-in: Windows' key repeat for a held key (env.MADEIRA_KEY_REPEAT = 1).
// ============================================================================

// MARK: - Pure input mapping
//
// Foundation only, no UIKit or Wine symbols: the host test compiles this
// section on its own, up to the "Device glue" marker below.

/// A clipboard scalar is one queue entry. ASCII uses physical keys for games
/// that read scan codes; other text uses Windows' Unicode packet input.
struct PasteStroke {
    let scalar: UInt32
    let vk: Int32
    let shift: Bool
}

struct PasteText {
    private var scalars: String.UnicodeScalarView.Iterator
    private(set) var next: PasteStroke?

    static func interval(milliseconds: String?) -> Double {
        guard let raw = milliseconds, let value = Double(raw), value.isFinite else { return 0.01 }
        return min(100, max(5, value)) / 1000
    }

    init(_ text: String) {
        scalars = text.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n").unicodeScalars.makeIterator()
        advance()
    }

    mutating func advance() {
        next = nil
        while let scalar = scalars.next() {
            let value = scalar.value
            // NUL, Backspace, Escape and other clipboard controls must not
            // become game commands. Tab and newline intentionally type keys.
            guard value == 9 || value == 10 || (value >= 32 && value != 127) else { continue }
            let key = Self.key(for: Character(String(scalar)))
            next = PasteStroke(scalar: value, vk: key?.0 ?? 0, shift: key?.1 ?? false)
            return
        }
    }

    static func isCommandV(_ vk: Int32, held: Set<Int32>) -> Bool {
        let commands: Set<Int32> = [0x5B, 0x5C]
        return vk == 0x56 && !held.isDisjoint(with: commands)
            && held.isSubset(of: commands.union([0x56]))
    }

    // Shared with the software keyboard. Restrict letter folding to ASCII:
    // uppercasing a non-ASCII character (e.g. ß) can produce multiple letters.
    static func key(for ch: Character) -> (Int32, Bool)? {
        guard let a = ch.asciiValue else { return nil }
        switch a {
        case 10, 13: return (0x0D, false)
        case 9: return (0x09, false)
        case 32: return (0x20, false)
        case 65...90: return (Int32(a), true)
        case 97...122: return (Int32(a - 32), false)
        case 48...57: return (Int32(a), false)
        default: break
        }
        let plain = "-=[]\\;',./`"
        let shifted = "_+{}|:\"<>?~"
        let keys: [Int32] = [0xBD, 0xBB, 0xDB, 0xDD, 0xDC, 0xBA, 0xDE, 0xBC, 0xBE, 0xBF, 0xC0]
        if let i = Array(plain).firstIndex(of: ch) { return (keys[i], false) }
        if let i = Array(shifted).firstIndex(of: ch) { return (keys[i], true) }
        if let i = Array(")!@#$%^&*(").firstIndex(of: ch) { return (Int32(0x30 + i), true) }
        return nil
    }
}

/// Each explicit paste is a complete request, even when the user taps Paste
/// again before the previous request has finished reaching the game.
struct PasteQueue {
    private var active: PasteText?
    private var pending: [PasteText] = []
    var next: PasteStroke? { active?.next }

    mutating func append(_ text: String) {
        let request = PasteText(text)
        guard request.next != nil else { return }
        if active == nil { active = request } else { pending.append(request) }
    }

    mutating func advance() {
        active?.advance()
        if active?.next == nil { active = pending.isEmpty ? nil : pending.removeFirst() }
    }

    mutating func cancel() { active = nil; pending.removeAll() }
}

enum HardwareKeyMap {
    /// Windows virtual-key code for a USB HID keyboard/keypad usage (page
    /// 0x07), or nil for a key Windows has no virtual key for. Ordered exactly
    /// as the HID table is, so a gap is visible as a gap.
    static func vk(forHIDUsage u: Int) -> Int32? {
        switch u {
        // 0x04-0x1D: a-z. HID is alphabetical, VK_A..VK_Z is 0x41..0x5A.
        case 0x04...0x1D: return Int32(0x41 + (u - 0x04))
        // 0x1E-0x26: 1-9, 0x27: 0. VK_0..VK_9 are the ASCII digits.
        case 0x1E...0x26: return Int32(0x31 + (u - 0x1E))
        case 0x27: return 0x30                       // VK_0

        case 0x28: return 0x0D                       // VK_RETURN
        case 0x29: return 0x1B                       // VK_ESCAPE
        case 0x2A: return 0x08                       // VK_BACK
        case 0x2B: return 0x09                       // VK_TAB
        case 0x2C: return 0x20                       // VK_SPACE
        case 0x2D: return 0xBD                       // VK_OEM_MINUS   -
        case 0x2E: return 0xBB                       // VK_OEM_PLUS    =
        case 0x2F: return 0xDB                       // VK_OEM_4       [
        case 0x30: return 0xDD                       // VK_OEM_6       ]
        case 0x31: return 0xDC                       // VK_OEM_5       backslash
        case 0x32: return 0xDC                       // non-US # / ~ (ISO): same VK
        case 0x33: return 0xBA                       // VK_OEM_1       ;
        case 0x34: return 0xDE                       // VK_OEM_7       '
        case 0x35: return 0xC0                       // VK_OEM_3       `
        case 0x36: return 0xBC                       // VK_OEM_COMMA   ,
        case 0x37: return 0xBE                       // VK_OEM_PERIOD  .
        case 0x38: return 0xBF                       // VK_OEM_2       /
        case 0x39: return 0x14                       // VK_CAPITAL

        // 0x3A-0x45: F1-F12 -> VK_F1 (0x70) upward.
        case 0x3A...0x45: return Int32(0x70 + (u - 0x3A))

        case 0x46: return 0x2C                       // VK_SNAPSHOT (PrintScreen)
        case 0x47: return 0x91                       // VK_SCROLL
        case 0x48: return 0x13                       // VK_PAUSE
        case 0x49: return 0x2D                       // VK_INSERT
        case 0x4A: return 0x24                       // VK_HOME
        case 0x4B: return 0x21                       // VK_PRIOR (PageUp)
        case 0x4C: return 0x2E                       // VK_DELETE (forward delete)
        case 0x4D: return 0x23                       // VK_END
        case 0x4E: return 0x22                       // VK_NEXT (PageDown)
        case 0x4F: return 0x27                       // VK_RIGHT
        case 0x50: return 0x25                       // VK_LEFT
        case 0x51: return 0x28                       // VK_DOWN
        case 0x52: return 0x26                       // VK_UP

        case 0x53: return 0x90                       // VK_NUMLOCK
        case 0x54: return 0x6F                       // VK_DIVIDE
        case 0x55: return 0x6A                       // VK_MULTIPLY
        case 0x56: return 0x6D                       // VK_SUBTRACT
        case 0x57: return 0x6B                       // VK_ADD
        // Numpad Enter. Windows tells it from the main Enter only by the E0 bit
        // on its scan code; the VK is the same. The plain VK_RETURN is what
        // every program that does not read scan codes sees anyway.
        case 0x58: return 0x0D                       // VK_RETURN (numpad)
        // 0x59-0x61: keypad 1-9 -> VK_NUMPAD1 (0x61) upward. 0x62: keypad 0.
        case 0x59...0x61: return Int32(0x61 + (u - 0x59))
        case 0x62: return 0x60                       // VK_NUMPAD0
        case 0x63: return 0x6E                       // VK_DECIMAL

        case 0x64: return 0xE2                       // VK_OEM_102 (ISO < > key)
        case 0x65: return 0x5D                       // VK_APPS (menu key)
        case 0x67: return 0x92                       // VK_OEM_NEC_EQUAL (keypad =)

        // 0x68-0x73: F13-F24 -> VK_F13 (0x7C) upward.
        case 0x68...0x73: return Int32(0x7C + (u - 0x68))

        case 0x75: return 0x2F                       // VK_HELP
        case 0x77: return 0x29                       // VK_SELECT
        // 0x74/0x76/0x78-0x7E (Execute, Menu, Stop, Again, Undo, Cut, Copy,
        // Paste, Find) are deliberately unmapped: Windows has no virtual key for
        // them, and the VKs that look close (VK_OEM_AUTO, VK_OEM_ENLW) are IME
        // keys. Inventing a mapping would send an IME key to a program.
        case 0x7F: return 0xAD                       // VK_VOLUME_MUTE
        case 0x80: return 0xAF                       // VK_VOLUME_UP
        case 0x81: return 0xAE                       // VK_VOLUME_DOWN
        case 0x85: return 0x6C                       // VK_SEPARATOR (keypad ,)

        // International / IME keys. Japanese and Korean keyboards send these
        // and a Windows program's text entry reads them.
        case 0x87: return 0xC1                       // VK_ABNT_C1 / JIS ro
        case 0x88: return 0xF2                       // VK_OEM_COPY (katakana/hiragana)
        case 0x89: return 0xDC                       // JIS yen: VK_OEM_5
        case 0x8A: return 0x1C                       // VK_CONVERT
        case 0x8B: return 0x1D                       // VK_NONCONVERT
        case 0x90: return 0x15                       // VK_HANGUL
        case 0x91: return 0x19                       // VK_HANJA

        // 0xE0-0xE7: the modifiers, as ordinary keys with LEFT/RIGHT identity.
        case 0xE0: return 0xA2                       // VK_LCONTROL
        case 0xE1: return 0xA0                       // VK_LSHIFT
        case 0xE2: return 0xA4                       // VK_LMENU
        case 0xE3: return 0x5B                       // VK_LWIN
        case 0xE4: return 0xA3                       // VK_RCONTROL
        case 0xE5: return 0xA1                       // VK_RSHIFT
        case 0xE6: return 0xA5                       // VK_RMENU
        case 0xE7: return 0x5C                       // VK_RWIN

        default: return nil
        }
    }

    /// Ctrl+Alt+P toggles pointer lock. It is the way out of the lock while
    /// the pointer cannot reach the app's own buttons, so P is swallowed
    /// (never posted to Windows); Ctrl and Alt are posted normally, because
    /// they are keys the user is genuinely holding.
    static func isPointerLockChord(_ vk: Int32, held: Set<Int32>) -> Bool {
        vk == 0x50                                              // VK_P
            && (held.contains(0xA2) || held.contains(0xA3))     // L/R control
            && (held.contains(0xA4) || held.contains(0xA5))     // L/R alt
    }
}

/// The five buttons a mouse has, and their MOUSEEVENTF_* edges. X1 and X2 share
/// one flag pair; mouseData (XBUTTON1/XBUTTON2) tells them apart.
enum MouseButton: Int, CaseIterable, Comparable {
    case left, right, middle, x1, x2

    func event(down: Bool) -> (flags: UInt32, data: UInt32) {
        switch self {
        case .left:   return (down ? 0x0002 : 0x0004, 0)
        case .right:  return (down ? 0x0008 : 0x0010, 0)
        case .middle: return (down ? 0x0020 : 0x0040, 0)
        case .x1:     return (down ? 0x0080 : 0x0100, 1)
        case .x2:     return (down ? 0x0080 : 0x0100, 2)
        }
    }

    static func < (a: MouseButton, b: MouseButton) -> Bool { a.rawValue < b.rawValue }
}

/// What has been posted DOWN and not yet UP. Callers declare the complete set
/// they want held and post the returned edges (ups first), so a lost callback
/// converges on the next one instead of leaving a key held.
struct HeldEdges<T: Hashable & Comparable> {
    private(set) var down: Set<T> = []

    mutating func update(_ want: Set<T>) -> (up: [T], down: [T]) {
        let ups = down.subtracting(want).sorted()
        let downs = want.subtracting(down).sorted()
        down = want
        return (ups, downs)
    }
}

/// Relative motion with the truncation remainder carried. The integer handed
/// to Wine loses a fraction on every event, and at a gain below 1.0 (or with
/// AssistiveTouch's already-scaled fractional deltas) that fraction is the
/// whole signal.
struct MotionCarry {
    private(set) var x = 0.0
    private(set) var y = 0.0

    mutating func add(_ dx: Double, _ dy: Double, gain: Double) -> (dx: Int32, dy: Int32) {
        guard dx.isFinite, dy.isFinite, gain.isFinite else { return (0, 0) }
        x += dx * gain
        y += dy * gain
        let ix = Int32(max(-30000, min(30000, x)))     // truncates toward zero
        let iy = Int32(max(-30000, min(30000, y)))
        x -= Double(ix)
        y -= Double(iy)
        return (ix, iy)
    }

    mutating func reset() { x = 0; y = 0 }
}

/// Continuous scroll (a precision wheel, a trackpad) becomes whole Windows
/// wheel notches of 120. Positive y is away from the user (MOUSEEVENTF_WHEEL
/// +120), positive x is right (MOUSEEVENTF_HWHEEL +120).
struct WheelAccumulator {
    static let wheel: UInt32 = 0x0800
    static let hwheel: UInt32 = 0x1000
    /// A burst larger than this is clamped rather than looped over.
    static let maxNotchesPerEvent = 32

    let notch: Double
    private(set) var x = 0.0
    private(set) var y = 0.0

    init(notch: Double = 1.0) { self.notch = notch }

    mutating func add(_ dx: Double, _ dy: Double) -> [(flags: UInt32, delta: Int32)] {
        guard dx.isFinite, dy.isFinite, notch > 0 else { return [] }
        var out: [(flags: UInt32, delta: Int32)] = []
        y += dy
        x += dx
        while y >= notch, out.count < Self.maxNotchesPerEvent { y -= notch; out.append((Self.wheel, 120)) }
        while y <= -notch, out.count < Self.maxNotchesPerEvent { y += notch; out.append((Self.wheel, -120)) }
        while x >= notch, out.count < Self.maxNotchesPerEvent { x -= notch; out.append((Self.hwheel, 120)) }
        while x <= -notch, out.count < Self.maxNotchesPerEvent { x += notch; out.append((Self.hwheel, -120)) }
        if out.count >= Self.maxNotchesPerEvent { x = 0; y = 0 }
        return out
    }

    mutating func reset() { x = 0; y = 0 }
}

/// GCMouse's scroll pad reports the vertical wheel in its first value,
/// negative away from the user, and the horizontal wheel in its second,
/// positive to the right (SDL reads it the same way: vertical = -x,
/// horizontal = y, in SDL_uikitevents.m). Passed through unchanged, every
/// wheel turn reached Windows as MOUSEEVENTF_HWHEEL. Returns
/// WheelAccumulator's (dx, dy).
enum GCMouseWheel {
    static func axes(_ first: Double, _ second: Double) -> (dx: Double, dy: Double) {
        (second, -first)
    }
}

/// AssistiveTouch (the only pointer path iPhone has) turns a mouse CLICK into
/// a synthesised `.direct` touch at its cursor. That touch has no contact patch
/// (`majorRadius` at or near zero, where a fingertip measures roughly 10-30
/// points) and lands within milliseconds of the GCMouse button transition for
/// the same click. Either signal is enough.
enum SynthesizedTouch {
    static let fingerRadiusFloor = 1.0
    static let buttonCoincidence = 0.050

    static func looksSynthesized(direct: Bool, majorRadius: Double, secondsSinceButton: Double) -> Bool {
        direct && (majorRadius <= fingerRadiusFloor || secondsSinceButton < buttonCoincidence)
    }
}

/// The drawn cursor of the desktop session follows relative motion, clamped
/// the way the wineserver clamps its own cursor.
enum DesktopCursor {
    static func advance(x: Double, y: Double, dx: Int32, dy: Int32,
                        width: Int, height: Int) -> (x: Double, y: Double) {
        let w = Double(max(width, 1)), h = Double(max(height, 1))
        return (min(max(x + Double(dx), 0), w - 1), min(max(y + Double(dy), 0), h - 1))
    }
}

/// A controller's right stick as a velocity mouse (opt-in, see `PadStickMouse`).
enum StickVelocity {
    /// Mouse counts per second at full deflection with a gain of 1.0.
    static let fullRate = 360.0
    static let deadzone = 0.15

    /// Deflection rescaled from the deadzone edge, so the first countable
    /// movement is a crawl and not a jump; unit length at most.
    static func deflect(_ x: Double, _ y: Double, deadzone: Double = deadzone) -> (x: Double, y: Double) {
        guard x.isFinite, y.isFinite else { return (0, 0) }
        let d = (x * x + y * y).squareRoot()
        guard d > deadzone else { return (0, 0) }
        let m = min((d - deadzone) / (1 - deadzone), 1.0)
        return (x / d * m, y / d * m)
    }

    /// Screen-sense motion for one frame: stick y is positive UP, the mouse's
    /// is positive DOWN. `dt` is clamped so a stalled frame cannot fling.
    static func frame(x: Double, y: Double, gain: Double, dt: Double) -> (dx: Double, dy: Double) {
        let v = deflect(x, y)
        let k = gain * fullRate * max(min(dt, 1.0 / 15.0), 1.0 / 240.0)
        return (v.x * k, -v.y * k)
    }
}


/// Where a point on the game view lands on the Windows screen. The screen is
/// drawn aspect-fit and centred in the view: MetalBackedView.gameRect for a
/// program on the game view (its touch mapping, mapTouch, is the same
/// arithmetic) and the desktop compositor (Winios.m) for the desktop
/// session. A point in the letterbox clamps to the nearest edge.
enum ScreenMap {
    static func fit(viewW: Double, viewH: Double, screenW: Int, screenH: Int)
        -> (originX: Double, originY: Double, scale: Double) {
        let sw = Double(max(screenW, 1)), sh = Double(max(screenH, 1))
        let s = max(min(viewW / sw, viewH / sh), .leastNormalMagnitude)
        return ((viewW - sw * s) / 2, (viewH - sh * s) / 2, s)
    }

    static func toScreen(x: Double, y: Double, viewW: Double, viewH: Double,
                         screenW: Int, screenH: Int) -> (x: Int32, y: Int32) {
        guard x.isFinite, y.isFinite, viewW > 0, viewH > 0 else { return (0, 0) }
        let f = fit(viewW: viewW, viewH: viewH, screenW: screenW, screenH: screenH)
        let sx = min(max((x - f.originX) / f.scale, 0), Double(max(screenW, 1) - 1))
        let sy = min(max((y - f.originY) / f.scale, 0), Double(max(screenH, 1) - 1))
        return (Int32(sx), Int32(sy))
    }

    /// madeira-bcd (2026-10-18): a point in the presented game rect's own
    /// coordinates in guest pixels. The rect is MetalHostView's frame, which
    /// MetalBackedView sizes to GameSurfaceLayout's rect for the session's Fit,
    /// Fill, Stretch or Aspect, so x and y scale separately; `guestW`/`guestH`
    /// is the virtual monitor win32u reports now (a library session's is rarely
    /// 1024x768: 1112x832 in the Warcraft II tester's run). A point outside the
    /// rect (letterbox, Fill's cropped margin) clamps to the nearest edge. The
    /// same arithmetic as GameSurfaceLayout.map, which places a finger's touch.
    static func toGuest(x: Double, y: Double, rectW: Double, rectH: Double,
                        guestW: Int, guestH: Int) -> (x: Int32, y: Int32) {
        guard x.isFinite, y.isFinite, rectW > 0, rectH > 0 else { return (0, 0) }
        let gw = Double(max(guestW, 1)), gh = Double(max(guestH, 1))
        return (Int32(min(max(x * gw / rectW, 0), gw - 1)), Int32(min(max(y * gh / rectH, 0), gh - 1)))
    }

    /// Where Wine puts a cursor posted at (x, y) while the program clips it
    /// (ClipCursor; right and bottom exclusive): the wineserver's own clamp
    /// (update_desktop_cursor_pos). An empty rectangle (none reported) clamps
    /// nothing.
    static func clamp(x: Int32, y: Int32, clipLeft l: Int32, top t: Int32, right r: Int32, bottom b: Int32)
        -> (x: Int32, y: Int32) {
        guard r > l, b > t else { return (x, y) }
        return (max(min(x, r - 1), l), max(min(y, b - 1), t))
    }
}

/// madeira-bcd (2026-10-18): what a program that HIDES its cursor reads the
/// mouse with. A position reader draws its own cursor where Wine's cursor is
/// (Warcraft II, most strategy and adventure games): on an iPad whose pointer is
/// not locked, Wine's cursor must sit under the iPad pointer, or the two drift
/// apart. A motion reader (mouse-look) turns by the mouse's motion: it recentres
/// the cursor (SetCursorPos), confines it to a small area (ClipCursor), or reads
/// raw input without legacy messages or a low-level hook (DirectInput, which
/// the wineserver reports); it needs relative moves, which keep coming past the
/// screen edge. Unknown until the mouse has moved for `settle` seconds with the
/// cursor hidden and no sign of motion reading; a motion reader stays one until
/// the program shows its cursor again (`reset`).
struct MouseCapture {
    enum Reading: String { case unknown, position, motion }

    /// Mouse-look recentres every frame: a game at 30 FPS warps ten times in
    /// this long. A program that only positions its cursor does not move it at all.
    static let settle = 0.35
    static let warpWindow = 2.0
    static let warpsForMotion = 3
    /// A clip narrower or lower than this share of the screen confines mouse-look;
    /// a program confining its cursor to its window or viewport clips wider.
    static let smallClip = 0.5
    /// While the cursor follows the pointer, a warp shorter than this share of the
    /// screen width is a clamp to the program's own viewport, not a recentring.
    static let farWarp = 0.04

    private(set) var reading: Reading = .unknown
    private(set) var why = ""
    private(set) var firstMotion = -Double.infinity
    private var warps: [Double] = []

    mutating func reset() {
        reading = .unknown
        why = ""
        firstMotion = -.infinity
        warps = []
    }

    private mutating func motionReader(_ reason: String) {
        guard reading != .motion else { return }
        reading = .motion
        why = reason
    }

    /// The mouse moved at `t` while the cursor was hidden.
    mutating func motion(at t: Double) {
        guard t.isFinite, reading == .unknown else { return }
        if !firstMotion.isFinite { firstMotion = t; return }
        if t - firstMotion >= Self.settle {
            reading = .position
            why = "no recentring, confining or raw motion reading in \(Int(Self.settle * 1000)) ms of motion"
        }
    }

    /// The program moved its cursor itself (`count` warps at `t`). `distance`:
    /// how far the last one moved it from where the pointer had put it, in
    /// screen widths; nil when the cursor does not follow the pointer (then every
    /// warp counts).
    mutating func warp(count: Int, at t: Double, distance: Double?) {
        guard count > 0, t.isFinite else { return }
        if let distance, distance < Self.farWarp { return }
        for _ in 0..<min(count, Self.warpsForMotion) { warps.append(t) }
        warps.removeAll { t - $0 > Self.warpWindow }
        if warps.count >= Self.warpsForMotion {
            motionReader("recentred its cursor \(warps.count) times in \(Int(Self.warpWindow)) s")
        }
    }

    /// The server's clip rectangle (right/bottom exclusive; empty: none).
    mutating func clip(left: Int, top: Int, right: Int, bottom: Int, screenW: Int, screenH: Int) {
        let w = right - left, h = bottom - top
        guard w > 0, h > 0, screenW > 0, screenH > 0 else { return }
        if Double(w) < Self.smallClip * Double(screenW) || Double(h) < Self.smallClip * Double(screenH) {
            motionReader("confined its cursor to \(w)x\(h) of \(screenW)x\(screenH)")
        }
    }

    /// The wineserver's WINIOS_READER_* bits for the foreground program.
    mutating func reader(_ flags: UInt32) {
        if flags & 1 != 0 { motionReader("reads raw mouse input without legacy messages") }
        else if flags & 2 != 0 { motionReader("reads the mouse through a low-level hook") }
    }
}

/// madeira-bcd (2026-10-18): Windows' typematic repeat for a held key of a
/// hardware keyboard (opt-in, env.MADEIRA_KEY_REPEAT = 1). GCKeyboard reports
/// one transition per key and never repeats, and the wineserver's own
/// repeat is off (no driver enables it), so a held key went down once: Backspace
/// in a game's chat deleted one character. As on Windows, only the key pressed
/// last repeats, after `delay`, every `period` (KeyboardDelay 1 and
/// KeyboardSpeed 31, the defaults), and releasing it ends the repeat until the
/// next press. The repeats are key-downs of a key that is already down, so the
/// program sees the previous-state bit set, as it would on Windows.
struct KeyRepeat {
    static let delay = 0.5
    static let period = 1.0 / 30.0

    private(set) var key: Int32?
    private(set) var next = Double.infinity

    mutating func pressed(_ vk: Int32, at t: Double) {
        guard t.isFinite else { return }
        key = vk
        next = t + Self.delay
    }

    mutating func released(_ vk: Int32) {
        if key == vk { cancel() }
    }

    mutating func cancel() {
        key = nil
        next = .infinity
    }

    /// The key to post again at `now`, if its time has come. A late timer posts
    /// one repeat, not a burst.
    mutating func due(at now: Double) -> Int32? {
        guard let k = key, now.isFinite, now >= next else { return nil }
        next = now + Self.period
        return k
    }
}

/// Keys (or buttons) the user holds, and which of them the program may see.
/// Losing focus hides everything held; a key pressed while the program does
/// not have focus stays hidden from it until it is released, even if focus
/// comes back while it is still down (the program never saw it go down).
struct FocusGate<T: Hashable> {
    private(set) var physical: Set<T> = []
    private(set) var blocked: Set<T> = []

    mutating func press(_ k: T, focused: Bool) {
        physical.insert(k)
        if !focused { blocked.insert(k) }
    }

    mutating func release(_ k: T) {
        physical.remove(k)
        blocked.remove(k)
    }

    /// Hide a held key from the program until it is released (a swallowed chord).
    mutating func block(_ k: T) {
        if physical.contains(k) { blocked.insert(k) }
    }

    mutating func focusLost() { blocked = physical }

    mutating func reset() { physical = []; blocked = [] }

    func wanted(focused: Bool) -> Set<T> { focused ? physical.subtracting(blocked) : [] }
}

/// Click to focus, for a pointer whose position the system does not report
/// (iPhone AssistiveTouch). Every tap or click in the app's window says where
/// the user is working: on the game view or elsewhere. A mouse button is routed
/// by the click that carries it; AssistiveTouch turns a click into a tap at
/// its cursor that lands within milliseconds of the button report, before or
/// after it, so a button with no tap nearby waits up to `window` for one.
struct ClickFocus {
    static let window = 0.060

    private(set) var onGame = true
    private(set) var lastTouchAt = -Double.infinity
    private(set) var lastTouchOnGame = true

    mutating func touchBegan(onGame: Bool, at t: Double) {
        guard t.isFinite else { return }
        self.onGame = onGame
        lastTouchAt = t
        lastTouchOnGame = onGame
    }

    /// Whether a button that went down at `t` belongs to the program; nil
    /// while a tap for it could still arrive (ask again after `window`).
    func route(buttonAt t: Double, now: Double) -> Bool? {
        guard t.isFinite, now.isFinite else { return onGame }
        if abs(lastTouchAt - t) <= Self.window { return lastTouchOnGame }
        if now - t < Self.window { return nil }
        return onGame
    }
}

/// How pointer motion reaches the program.
enum PointerRoute: String {
    /// The program does not have the mouse's focus: nothing is posted.
    case blocked
    /// Device motion, posted as relative moves.
    case relative
    /// The iOS pointer's position on the game view, posted as absolute moves.
    case absolute
}

enum PointerPolicy {
    /// - focused: the mouse belongs to the program right now;
    /// - hover: the system reports where the pointer is (iPad, not AssistiveTouch);
    /// - locked: pointer lock is on (motion only, no position);
    /// - cursorShown: the program shows a cursor (always, in the desktop session);
    /// - absoluteAllowed: MADEIRA_POINTER_ABSOLUTE;
    /// - follow (madeira-bcd, 2026-10-18): the program hides its cursor but draws
    ///   its own where Wine's is, and the pointer is not locked (MouseCapture,
    ///   env.MADEIRA_POINTER_FOLLOW): Wine's cursor goes where the pointer is.
    static func route(focused: Bool, hover: Bool, locked: Bool, cursorShown: Bool,
                      absoluteAllowed: Bool, follow: Bool = false) -> PointerRoute {
        guard focused else { return .blocked }
        return hover && !locked && (cursorShown || follow) && absoluteAllowed ? .absolute : .relative
    }
}

/// Pointer lock that follows the program's cursor (iPad): lock while it hides
/// its cursor and the pointer is over the game view, release when it shows one
/// or loses focus. Only a program that is live locks the pointer: its driver
/// reported within `liveWindow`. A lock whose program stopped reporting while
/// the mouse keeps moving (the program exited) is released after `staleAfter`.
enum AutoLock {
    static let hiddenDelay = 0.5
    static let liveWindow = 1.0
    static let staleAfter = 2.0
    static let moving = 0.5

    enum Action: Equatable { case lock, unlock, none }

    /// `wantsLock` (madeira-bcd, 2026-10-18): false while env.MADEIRA_POINTER_FOLLOW
    /// = 1 asks for the pointer's position instead of a lock for the game's
    /// hidden cursor.
    static func decide(locked: Bool, lockedByUs: Bool, cursorShown: Bool, hiddenFor: Double,
                       pointerOver: Bool, focused: Bool, sinceReport: Double, sinceMotion: Double,
                       wantsLock: Bool = true) -> Action {
        if locked {
            guard lockedByUs else { return .none }
            if cursorShown || !focused || !wantsLock { return .unlock }
            if sinceMotion < moving && sinceReport > staleAfter { return .unlock }
            return .none
        }
        if wantsLock && !cursorShown && hiddenFor >= hiddenDelay && pointerOver && focused
            && sinceReport <= liveWindow && sinceMotion < moving {
            return .lock
        }
        return .none
    }
}

// MARK: - Device glue

final class HardwareInput: ObservableObject {
    static let shared = HardwareInput()

    // MARK: switches

    /// MADEIRA_HWINPUT=0: no keyboard/mouse bridging, no pointer recognisers,
    /// no touch filter, no drawn cursor; the app behaves as it did before this
    /// file existed.
    static let enabled = flag("MADEIRA_HWINPUT", defaultOn: true)
    /// MADEIRA_INPUT_FOCUS=0: keyboard and mouse reach the program whatever
    /// has focus (the program still gets nothing while the app is inactive:
    /// held input is released then).
    static let focusEnabled = enabled && flag("MADEIRA_INPUT_FOCUS", defaultOn: true)
    /// MADEIRA_DIRECT_CURSOR=0: no cursor is drawn on the game view, motion
    /// stays relative there and the pointer never locks by itself; the driver
    /// reports nothing.
    static let directCursorEnabled = enabled && flag("MADEIRA_DIRECT_CURSOR", defaultOn: true)
    /// MADEIRA_POINTER_ABSOLUTE=0: the program's cursor never follows the iOS
    /// pointer's position; all motion is relative.
    static let absoluteEnabled = enabled && flag("MADEIRA_POINTER_ABSOLUTE", defaultOn: true)
    /// MADEIRA_POINTER_LOCK=0: the pointer is never locked and no lock button
    /// or chord is offered.
    static let lockEnabled = enabled && flag("MADEIRA_POINTER_LOCK", defaultOn: true)
    /// MADEIRA_POINTER_AUTOLOCK=0: the pointer locks only on request, not
    /// while the program hides its cursor (see `AutoLock`).
    static let autoLockEnabled = lockEnabled && flag("MADEIRA_POINTER_AUTOLOCK", defaultOn: true)

    /// Only `0` disables a default-on switch; only `1` enables a default-off one.
    private static func flag(_ name: String, defaultOn: Bool) -> Bool {
        guard let value = MadeiraConfig.get("env.\(name)") ?? ProcessInfo.processInfo.environment[name]
        else { return defaultOn }
        return defaultOn ? value != "0" : value == "1"
    }

    /// madeira-bcd (2026-10-18): a switch the running game's own lines may set
    /// (Game details › This game's config), else bcd.cfg, else the process
    /// environment. Read again for each library session; main thread.
    private static var sessionSwitches: [String: (key: String, value: String?)] = [:]
    private static func sessionValue(_ name: String) -> String? {
        let game = getenv("MADEIRA_CFG_GAME").map { String(cString: $0) } ?? ""
        let key = "\(LibraryModel.sessionsThisRun)|\(game)"
        if let cached = sessionSwitches[name], cached.key == key { return cached.value }
        let value = MadeiraConfig.gameValue("env.\(name)") ?? MadeiraConfig.get("env.\(name)")
            ?? ProcessInfo.processInfo.environment[name]
        sessionSwitches[name] = (key, value)
        return value
    }

    /// env.MADEIRA_POINTER_FOLLOW (madeira-bcd, 2026-10-18): what an iPad does
    /// for a program that hides its cursor but draws its own where Wine's cursor
    /// is (MouseCapture). Unset: its cursor follows the pointer's position while
    /// the pointer is not locked (iPadOS refused the lock: not full screen; or
    /// the mouse arrives through UIKit), the one state where relative moves
    /// drift away from the pointer; with the lock it is unchanged. 1 (a game's
    /// own line): this game's hidden cursor always follows the pointer, without
    /// the classification and instead of the automatic lock. 0: never
    /// (relative, as before).
    enum FollowMode: String { case off, auto, always }
    var followMode: FollowMode {
        switch Self.sessionValue("MADEIRA_POINTER_FOLLOW") {
        case "0": return .off
        case "1": return .always
        default: return .auto
        }
    }

    /// env.MADEIRA_KEY_REPEAT = 1 (madeira-bcd, 2026-10-18, opt-in): Windows'
    /// repeat for a held hardware-keyboard key (KeyRepeat).
    var keyRepeatEnabled: Bool { Self.sessionValue("MADEIRA_KEY_REPEAT") == "1" }

    /// Whether pointer lock can do anything on this device. `prefersPointerLocked`
    /// is an iPad mechanism; iPhone's on-screen cursor belongs to AssistiveTouch
    /// and ignores it. No API reports this, so the idiom is the detection.
    static var pointerLockAvailable: Bool { lockEnabled && UIDevice.current.userInterfaceIdiom != .phone }

    /// The desktop session (explorer on a virtual desktop). Read per use: the
    /// app sets MADEIRA_DESKTOP for each launch.
    static var desktopMode: Bool {
        guard let v = getenv("MADEIRA_DESKTOP") else { return false }
        return v.pointee == 49  // '1'
    }

    /// The guest screen's live size in pixels (IOSDisplayShim): the session's
    /// virtual monitor, the desktop in a desktop session, until a program
    /// changes the display mode, which resizes it.
    static func screenSize() -> (w: Int, h: Int) {
        var w: Int32 = 0, h: Int32 = 0
        winios_screen_size(&w, &h)
        return (w > 0 ? Int(w) : 1024, h > 0 ? Int(h) : 768)
    }

    /// A program on the game view, with its cursor drawn by this file.
    private var directCursorLive: Bool { Self.directCursorEnabled && !Self.desktopMode }

    // MARK: published state

    @Published private(set) var keyboardConnected = false
    @Published private(set) var mouseConnected = false
    /// The iOS system pointer is hidden and pinned, and GCMouse deltas keep
    /// arriving at the screen edges. See `PointerLock`.
    @Published private(set) var pointerLocked = false
    /// iPhone: a mouse enumerated but nothing came out of it in 10 s. Raised
    /// once per session, dismissable, cleared the instant a delta lands.
    @Published private(set) var assistiveTouchHint = false

    /// Which path is carrying the mouse. Pointer lock is correct for `.gcmouse`
    /// and fatal for `.uikit` (`prefersPointerLocked` is exactly the switch
    /// that tells UIKit to stop delivering pointer events).
    enum MousePath: String { case none, gcmouse, uikit }
    @Published private(set) var mousePath: MousePath = .none

    /// True while a hardware keyboard delivers raw key events. The text bridge
    /// (MetalBackedView's UIKeyInput) receives the same presses from UIKit, so
    /// it stands aside while this is true instead of typing every key twice.
    var handlesTyping: Bool { Self.enabled && keyboardConnected }

    // MARK: focus (main thread)

    private var appActive = true
    /// The app is active, the game view is on screen and nothing is presented
    /// over it. Read by PadStickMouse.
    private(set) var baseFocused = true
    private var keyboardFocused = true
    private var mouseFocused = true
    /// UIKit reports the pointer's position (hover): on iPad from the start,
    /// so the mouse belongs to the program only once the pointer is over the
    /// game view; on iPhone (AssistiveTouch) only if a hover ever arrives.
    /// Without it the mouse is routed by click focus.
    private var hoverSeen = false
    private var pointerOver = false
    private var clickFocus = ClickFocus()
    private var focusTimer: Timer?
    private weak var observedWindow: UIWindow?
    private var refreshing = false
    private var refreshAgain = false

    // MARK: held state (main thread)

    private var keys = FocusGate<Int32>()
    private var keysPosted = HeldEdges<Int32>()
    // Defer Command until its partner is known: Command-V must not open the
    // guest Start menu. A Command tap or another chord still reaches Windows.
    private var deferredCommands: Set<Int32> = []
    private var pasteText = PasteQueue()
    private var pasteTimer: Timer?
    private var pasteInterval = 0.01
    /// Buttons whose press went to the program and are still down.
    private var gameButtons: Set<MouseButton> = []
    private var buttonsPosted = HeldEdges<MouseButton>()
    /// iPhone: presses waiting for the tap that says where they belong.
    private var pendingButtons: [MouseButton: (at: CFTimeInterval, released: Bool)] = [:]
    private var gcButtonSeen = false

    // MARK: cursor and lock state (main thread)

    private var cursorState = winios_direct_cursor_state()
    private var cursorHiddenSince: CFTimeInterval = 0
    private var lastReportAt: CFTimeInterval = 0
    /// The mouse (or the right-stick mouse) is being used; a finger on the game
    /// view clears it. The drawn cursor shows only while this is true.
    private var mouseInUse = false
    private var currentRoute: PointerRoute = .relative
    /// The last absolute position posted: the drawn cursor's position on the
    /// absolute route (the driver's report would trail it).
    private var lastAbsolute: (x: Int32, y: Int32)?
    private var lockedByUs = false
    /// The user released an automatic lock: leave it off until the program's
    /// cursor next changes visibility.
    private var autoLockSuppressed = false
    /// madeira-bcd (2026-10-18): iPadOS's own answer (UIPointerLockState): a
    /// lock is only a request, and a scene that is not full screen never gets
    /// it. Before this the app acted as if every request held.
    private var lockGranted = false
    private var lockRequestedAt: CFTimeInterval = 0
    private var lockRefusalLogged = false
    /// How long a fresh request counts as granted while iPadOS answers.
    private static let lockAnswerWindow: CFTimeInterval = 0.5
    /// What the program that hides its cursor reads the mouse with.
    private var capture = MouseCapture()
    private var captureLogged = MouseCapture.Reading.unknown
    /// The user released the pointer (Ctrl+Alt+P or the lock button) while the
    /// program held it: BCD's buttons take the pointer again until the
    /// program's cursor next changes visibility.
    private var userReleased = false
    /// Hardware-keyboard repeat (env.MADEIRA_KEY_REPEAT = 1).
    private var keyRepeat = KeyRepeat()
    private var keyRepeatTimer: Timer?
    // The once-a-minute activity line (main thread; relative moves under motionLock).
    private var minuteStart: CFTimeInterval = 0
    private var minuteAbsolute = 0, minuteButtons = 0, minuteKeys = 0, minuteRepeats = 0, minuteWarps = 0
    private var routeLogged = 0

    // MARK: motion state (motionLock)

    /// GCMouse's delivery queue. Serial (deltas stay ordered) and
    /// `.userInteractive`, so a mouse sample never waits behind a SwiftUI
    /// re-render on the main queue. Motion is posted from here directly:
    /// `winios_pointer` pushes into a mutex-guarded ring. Buttons hop to main.
    private let mouseQueue = DispatchQueue(label: "madeira.hwinput.mouse", qos: .userInteractive)
    private let motionLock = NSLock()
    private var carry = MotionCarry()
    private var wheel = WheelAccumulator()
    /// `currentRoute`, for the mouse queue.
    private var route: PointerRoute = .relative
    private var mouseInUseMirror = false
    /// A GCMouse delta inside this many seconds means the hand is on the mouse
    /// and a `.direct` touch is AssistiveTouch's, not a finger's.
    private static let mouseActiveWindow: CFTimeInterval = 2.0
    private var lastGCDeltaAt: CFTimeInterval = 0
    private var lastGCButtonAt: CFTimeInterval = 0
    private var gcDeltaLive = false
    private var rawSeq = 0
    private var minuteRelative = 0, minuteWheel = 0
    private var tickDX = 0.0, tickDY = 0.0, tickWheel = 0
    private var tickerArmed = false
    private var desktopCursorSynced = false
    // Delivery statistics, reported every 10 s while diagnostics are on.
    private static let deliveryWindow: CFTimeInterval = 10.0
    private var devCount = 0
    private var devLastAt: CFTimeInterval = 0
    private var devGapSum = 0.0
    private var devGapMax = 0.0
    private var devWindowStart: CFTimeInterval = 0

    // MARK: bookkeeping (main thread)

    private var started = false
    /// GameController hands the same GCMouse back from `mice()`, `current` and
    /// the notifications; the identity set decides what is news.
    private var attachedMice = Set<ObjectIdentifier>()
    private var gcDeltaSeen = false
    private var uikitSeen = false
    private var touchClassLogged = 0
    private var phoneLockNoted = false
    private var hintArmed = false
    private var ticker: Timer?
    private var tickKeys = 0

    private static let moveFlag: UInt32 = 0x0001
    private static let absoluteFlag: UInt32 = 0x8000

    private var diagnostics: Bool { InputSettings.shared.diagnostics }
    /// Either GameController stream has spoken: the UIKit fallback stands aside.
    private var gcLive: Bool { gcDeltaSeen || gcButtonSeen }

    // MARK: - lifecycle

    /// Idempotent. Called once from MadeiraApp's onAppear.
    func start() {
        guard !started else { return }
        started = true
        log("enabled=\(Self.enabled ? 1 : 0) focus=\(Self.focusEnabled ? 1 : 0) "
            + "cursor=\(Self.directCursorEnabled ? 1 : 0) absolute=\(Self.absoluteEnabled ? 1 : 0) "
            + "lock=\(Self.lockEnabled ? 1 : 0) autolock=\(Self.autoLockEnabled ? 1 : 0)")
        guard Self.enabled else { return }
        appActive = UIApplication.shared.applicationState == .active
        hoverSeen = UIDevice.current.userInterfaceIdiom != .phone
        if Self.directCursorEnabled {
            // Called on a Wine thread, at most once per main-queue turn.
            winios_direct_cursor_enable {
                DispatchQueue.main.async { HardwareInput.shared.directCursorChanged() }
            }
        }
        PadStickMouse.shared.start()

        let nc = NotificationCenter.default
        nc.addObserver(forName: .GCKeyboardDidConnect, object: nil, queue: .main) { [weak self] n in
            self?.attachKeyboard(n.object as? GCKeyboard)
        }
        nc.addObserver(forName: .GCKeyboardDidDisconnect, object: nil, queue: .main) { [weak self] _ in
            self?.detachKeyboard()
        }
        nc.addObserver(forName: .GCMouseDidConnect, object: nil, queue: .main) { [weak self] n in
            self?.attachMouse(n.object as? GCMouse, why: "connect")
        }
        nc.addObserver(forName: .GCMouseDidDisconnect, object: nil, queue: .main) { [weak self] n in
            self?.detachMouse(n.object as? GCMouse)
        }
        // A connected mouse that is not CURRENT is not routed to this app; the
        // moment it becomes current is the moment its handlers are worth
        // (re-)installing even though the device object did not change.
        nc.addObserver(forName: .GCMouseDidBecomeCurrent, object: nil, queue: .main) { [weak self] n in
            self?.attachMouse(n.object as? GCMouse, why: "became-current")
        }
        // The events that end a press are the ones you cannot count on: a key
        // held when the app resigns active never gets its up.
        for name in [UIApplication.willResignActiveNotification,
                     UIApplication.didEnterBackgroundNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.appActive = false
                self?.refreshFocus("app inactive")
            }
        }
        nc.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            self?.appActive = true
            self?.refreshFocus("app active")
        }
        // Not on memory warnings: games here run close to the memory limit and
        // get them mid-play, and a key released while still physically held
        // stays up until it is pressed again.
        // Another text input taking or giving back the keyboard, or a window
        // changing key status, changes focus at once rather than at the next poll.
        for name in [UITextField.textDidBeginEditingNotification, UITextField.textDidEndEditingNotification,
                     UITextView.textDidBeginEditingNotification, UITextView.textDidEndEditingNotification,
                     UIWindow.didBecomeKeyNotification, UIWindow.didResignKeyNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.refreshFocus("responder")
            }
        }
        // madeira-bcd (2026-10-18): iPadOS granting or ending the lock (a scene
        // going full screen or leaving it); refreshFocus reads the state.
        nc.addObserver(forName: UIPointerLockState.didChangeNotification, object: nil, queue: .main) { [weak self] _ in
            self?.refreshFocus("pointer lock state")
        }

        // Devices attached before launch get no notification.
        inventory("startup")
        refreshFocus("start")
        // stderr becomes the session log only when Wine starts, seconds after
        // this runs; re-emit the (cheap, idempotent) inventory once it is.
        for delay in [10.0, 30.0, 90.0] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                self?.inventory("t+\(Int(delay))s")
            }
        }
    }

    /// Enumerate and (re-)wire every pointing device by BOTH routes, `mice()`
    /// and `current`: they are not the same list and either can be empty.
    private func inventory(_ why: String) {
        let mice = GCMouse.mice()
        let cur = GCMouse.current
        log("inventory(\(why)): mice=\(mice.count) current=\(cur != nil) "
            + "keyboard=\(GCKeyboard.coalesced != nil) path=\(mousePath.rawValue) "
            + "gcDelta=\(gcDeltaSeen) uikit=\(uikitSeen)")
        for (i, m) in mice.enumerated() { attachMouse(m, why: "\(why)/mice[\(i)]") }
        if let cur, !mice.contains(where: { $0 === cur }) { attachMouse(cur, why: "\(why)/current") }
        attachKeyboard(GCKeyboard.coalesced)
    }

    private func log(_ s: String) {
        fputs("[hwinput] \(s)\n", stderr)
    }

    /// The only place `mousePath` moves. `.gcmouse` is absorbing: once a raw
    /// HID delta has arrived, the UIKit path is redundant at best and a
    /// double count at worst.
    private func announcePath(_ p: MousePath) {
        guard p != mousePath, mousePath != .gcmouse else { return }
        mousePath = p
        log("mouse path: \(p.rawValue)")
    }

    /// Raw delta trace while diagnostics are on: all of the first 20, then
    /// 1 in 100. The first twenty tell a dead stream from an all-zero one.
    private func logRaw(_ src: String, _ dx: Double, _ dy: Double) {
        motionLock.lock(); rawSeq += 1; let n = rawSeq; motionLock.unlock()
        guard n <= 20 || n % 100 == 0 else { return }
        guard InputSettings.shared.diagnostics else { return }
        log(String(format: "raw %@ #%d dx=%.3f dy=%.3f", src, n, dx, dy))
    }

    /// Post an up for everything the program was told is held.
    private func releaseAll(_ why: String) {
        cancelPaste()
        deferredCommands.removeAll()
        let had = keysPosted.down.count + buttonsPosted.down.count
        keys.focusLost()
        pendingButtons.removeAll()
        gameButtons.removeAll()
        syncKeys()
        syncButtons()
        motionLock.lock(); carry.reset(); wheel.reset(); motionLock.unlock()
        if had > 0 { log("released \(had) held input(s) (\(why))") }
    }

    // MARK: - focus

    /// Everything that decides where input goes, re-evaluated from scratch.
    /// Cheap (a few property reads and one responder-chain walk); called on
    /// every focus-relevant event and four times a second while an input
    /// device is attached. Re-entrant calls (a lock change inside) coalesce.
    private func refreshFocus(_ why: String) {
        guard Self.enabled else { return }
        if refreshing { refreshAgain = true; return }
        refreshing = true
        defer { refreshing = false }
        repeat {
            refreshAgain = false
            evaluateFocus(why)
        } while refreshAgain
    }

    private func evaluateFocus(_ why: String) {
        installTouchObserver()
        updateLockState()
        noteHiddenMotion(at: nil)
        let base = computeBaseFocus()
        let keyboard = base && !typingElsewhere()
        let over = hoverSeen ? pointerOver : clickFocus.onGame
        // madeira-bcd (2026-10-18): `routeLocked`, not the bare request: a lock
        // iPadOS refused holds the mouse only for a program that reads motion.
        let mouse = base && (!Self.focusEnabled || routeLocked || over || !gameButtons.isEmpty)
        baseFocused = base
        if keyboard != keyboardFocused {
            keyboardFocused = keyboard
            if !keyboard { keys.focusLost(); keyRepeat.cancel(); scheduleKeyRepeat(); deferredCommands.removeAll(); cancelPaste() }
            syncKeys()
            log("keyboard focus: \(keyboard ? "program" : "elsewhere") (\(why))")
        }
        if mouse != mouseFocused {
            mouseFocused = mouse
            if !mouse {
                pendingButtons.removeAll()
                gameButtons.removeAll()
                syncButtons()
                motionLock.lock(); carry.reset(); wheel.reset(); motionLock.unlock()
            }
            if diagnostics || !base { log("mouse focus: \(mouse ? "program" : "elsewhere") (\(why))") }
        }
        updateRoute()
        updateAutoLock()
        renderCursor()
        updateFocusTimer()
        minuteLine()
    }

    // MARK: - lock state and the program's mouse reading (madeira-bcd, 2026-10-18)

    /// The pointer is locked, or was asked to be moments ago and iPadOS may
    /// still grant it.
    private var lockEffective: Bool {
        pointerLocked && (lockGranted || CACurrentMediaTime() - lockRequestedAt < Self.lockAnswerWindow)
    }

    /// The program hides its cursor, draws its own where Wine's cursor is, and
    /// the pointer is not locked: Wine's cursor goes where the iPad pointer is.
    private var followsPointer: Bool {
        guard directCursorLive, hoverSeen, Self.absoluteEnabled, cursorState.reports > 0,
              cursorState.shown == 0, !lockEffective else { return false }
        switch followMode {
        case .off: return false
        // Automatic: a program MouseCapture found reading the position, and only
        // where a lock exists to be refused (iPad); an iPhone's AssistiveTouch
        // pointer keeps relative moves unless the line asks.
        case .auto: return capture.reading == .position && Self.pointerLockAvailable
        // The line says this game draws its own cursor: no classification.
        case .always: return true
        }
    }

    /// Motion only, no position, and the mouse with the program wherever the
    /// pointer is: a lock iPadOS holds (or may still grant), or one it refused
    /// while the program hides its cursor and does not follow the pointer (it
    /// reads motion, or it is still being classified): relative moves, as
    /// before. A refused lock over a shown cursor or a following one is no lock.
    private var routeLocked: Bool {
        pointerLocked && (lockEffective || (!cursorShownForRoute && !followsPointer))
    }

    /// The program holds the mouse: ContentView's ControlsWindow (BCD's buttons
    /// and touch controls over the game) lets pointer events through to the game
    /// view, so the pointer neither shows over them nor takes the mouse away.
    /// Fingers are not affected. Ctrl+Alt+P or the lock button gives the pointer
    /// back to BCD until the program's cursor next changes visibility.
    var pointerHeldByProgram: Bool {
        Self.enabled && mouseInUse && mouseFocused && !userReleased && (pointerLocked || followsPointer)
    }

    /// A pointer event (hover, a mouse or trackpad click, scrolling, a trackpad
    /// pinch) rather than a finger. Used by ControlsWindow's hit test.
    static func isPointerEvent(_ event: UIEvent?) -> Bool {
        guard let event else { return false }
        switch event.type {
        case .hover, .scroll, .transform:
            return true
        case .touches:
            return !event.buttonMask.isEmpty
                || (event.allTouches?.contains { $0.type == .indirectPointer } ?? false)
        default:
            return false
        }
    }

    /// iPadOS's answer to the lock request (UIPointerLockState), logged when it
    /// changes, and once per request when it never came.
    private func updateLockState() {
        let scene = MetalBackedView.keyboardTarget?.window?.windowScene
        let granted = scene?.pointerLockState?.isLocked ?? false
        if granted != lockGranted {
            lockGranted = granted
            if granted { lockRefusalLogged = false }
            log("pointer lock \(granted ? "granted" : "ended") by iPadOS (requested=\(pointerLocked ? 1 : 0); "
                + "\(Self.sceneShape(scene)))")
        }
        if pointerLocked, !granted, !lockRefusalLogged,
           CACurrentMediaTime() - lockRequestedAt >= Self.lockAnswerWindow {
            lockRefusalLogged = true
            log("pointer lock requested but not granted by iPadOS (\(Self.sceneShape(scene))): iPadOS locks only "
                + "a full-screen scene. A program that hides its cursor and draws its own follows the pointer "
                + "instead (env.MADEIRA_POINTER_FOLLOW=\(followMode.rawValue)); one that reads motion keeps "
                + "relative moves")
        }
    }

    private static func sceneShape(_ scene: UIWindowScene?) -> String {
        guard let scene else { return "no scene" }
        let s = scene.coordinateSpace.bounds.size, full = scene.screen.bounds.size
        let fills = (abs(s.width - full.width) < 1 && abs(s.height - full.height) < 1)
            || (abs(s.width - full.height) < 1 && abs(s.height - full.width) < 1)
        return String(format: "scene %.0fx%.0f pt, screen %.0fx%.0f pt: %@", s.width, s.height,
                      full.width, full.height, fills ? "full size" : "a window")
    }

    /// The mouse moved while the program hides its cursor (MouseCapture settles
    /// on "position" after enough of it). Hover and UIKit motion pass their time;
    /// the focus poll passes nil and looks at the GCMouse stream's last delta.
    private func noteHiddenMotion(at t: CFTimeInterval?) {
        guard directCursorLive, hoverSeen, cursorState.reports > 0, cursorState.shown == 0,
              capture.reading == .unknown, cursorHiddenSince > 0 else { return }
        var when = t
        if when == nil {
            motionLock.lock(); let last = lastGCDeltaAt; motionLock.unlock()
            if last > cursorHiddenSince { when = last }
        }
        guard let when else { return }
        capture.motion(at: when)
        noteCaptureChange()
    }

    /// The program's warps, clip and reader for the current hidden period.
    private func classifyHiddenCursor(warps: Int, distance: Double?, now: CFTimeInterval) {
        guard directCursorLive, cursorState.reports > 0, cursorState.shown == 0 else { return }
        let g = Self.screenSize()
        capture.warp(count: warps, at: now, distance: distance)
        capture.clip(left: Int(cursorState.clip_left), top: Int(cursorState.clip_top),
                     right: Int(cursorState.clip_right), bottom: Int(cursorState.clip_bottom),
                     screenW: g.w, screenH: g.h)
        capture.reader(cursorState.reader)
        noteCaptureChange()
    }

    private var captureLogCount = 0
    private func noteCaptureChange() {
        guard capture.reading != captureLogged else { return }
        captureLogged = capture.reading
        guard capture.reading != .unknown else { return }
        captureLogCount += 1
        if captureLogCount <= 20 {
            log("the program hides its cursor and reads the mouse's "
                + (capture.reading == .position ? "position" : "motion") + " (\(capture.why)); "
                + (capture.reading == .position
                   ? "its cursor follows the pointer while the pointer is not locked "
                     + "(env.MADEIRA_POINTER_FOLLOW=\(followMode.rawValue), lock now: \(lockEffective ? "on" : "off"))"
                   : "relative moves"))
        }
        refreshFocus("mouse reading")
    }

    /// Once a minute while input moves: what reached the program, by which
    /// route, and the lock and reading behind it. Main thread (the focus poll).
    private func minuteLine() {
        let now = CACurrentMediaTime()
        if minuteStart == 0 { minuteStart = now; return }
        guard now - minuteStart >= 60 else { return }
        motionLock.lock()
        let relative = minuteRelative, wheelNotches = minuteWheel
        minuteRelative = 0; minuteWheel = 0
        motionLock.unlock()
        let total = relative + wheelNotches + minuteAbsolute + minuteButtons + minuteKeys + minuteRepeats + minuteWarps
        if total > 0 {
            let g = Self.screenSize()
            let lock = !pointerLocked ? "off" : lockGranted ? "granted" : lockEffective ? "asked" : "refused"
            log("minute: mouse relative=\(relative) absolute=\(minuteAbsolute) buttons=\(minuteButtons) "
                + "wheel=\(wheelNotches) keys=\(minuteKeys) repeats=\(minuteRepeats) warps=\(minuteWarps) "
                + "route=\(currentRoute.rawValue) lock=\(lock) cursor=\(cursorState.shown != 0 ? "shown" : "hidden") "
                + "reading=\(capture.reading.rawValue) reader=\(cursorState.reader) "
                + "clip=\(cursorState.clip_left),\(cursorState.clip_top),\(cursorState.clip_right),\(cursorState.clip_bottom) "
                + "screen=\(g.w)x\(g.h) path=\(mousePath.rawValue)")
        }
        minuteStart = now
        minuteAbsolute = 0; minuteButtons = 0; minuteKeys = 0; minuteRepeats = 0; minuteWarps = 0
    }

    /// The app is active, the game view is on screen in the foreground scene,
    /// and no sheet, alert, picker or menu controller is presented over any of
    /// the scene's windows.
    private func computeBaseFocus() -> Bool {
        guard !LibraryModel.shared.desktopHome else { return false }
        guard appActive, UIApplication.shared.applicationState == .active else { return false }
        guard Self.focusEnabled else { return true }
        guard let v = MetalBackedView.keyboardTarget, let w = v.window,
              !v.isHidden, !w.isHidden, v.alpha > 0.01 else { return false }
        if let scene = w.windowScene {
            if scene.activationState != .foregroundActive { return false }
            if scene.windows.contains(where: { $0.rootViewController?.presentedViewController != nil }) {
                return false
            }
        }
        return true
    }

    /// Another text input (a text field, a text view) is first responder. The
    /// game view's own text bridge is the program's, not "elsewhere".
    private func typingElsewhere() -> Bool {
        guard Self.focusEnabled, let fr = FirstResponder.current else { return false }
        return fr !== MetalBackedView.keyboardTarget && fr is UIKeyInput
    }

    /// The same question for a paste: the key window's own input (LibraryKeyInput, the keyboard the key bar sits on) is the program's.
    private func typingElsewhereForPaste() -> Bool {
        guard Self.focusEnabled, let fr = FirstResponder.current else { return false }
        return fr !== MetalBackedView.keyboardTarget && !(fr is LibraryKeyInput) && fr is UIKeyInput
    }

    /// Four times a second while a keyboard, mouse or controller is attached:
    /// a sheet or text field can take focus without any event reaching here.
    private func updateFocusTimer() {
        let want = Self.enabled && (keyboardConnected || mouseConnected || PadStickMouse.shared.controllerConnected)
        if want, focusTimer == nil {
            let t = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in self?.refreshFocus("poll") }
            RunLoop.main.add(t, forMode: .common)
            focusTimer = t
        } else if !want, let t = focusTimer {
            t.invalidate()
            focusTimer = nil
        }
    }

    /// Controllers come and go through PadStickMouse; the poll follows them.
    func controllersChanged() { refreshFocus("controllers") }

    /// Every tap and click in the game view's window, for click focus.
    private func installTouchObserver() {
        guard let w = MetalBackedView.keyboardTarget?.window, w !== observedWindow else { return }
        observedWindow = w
        w.addGestureRecognizer(TouchFocusObserver())
    }

    /// TouchFocusObserver: a touch began somewhere in the game view's window.
    func touchBeganInWindow(_ t: UITouch) {
        guard Self.enabled, let game = MetalBackedView.keyboardTarget else { return }
        let onGame = t.view.map { $0.isDescendant(of: game) } ?? false
        clickFocus.touchBegan(onGame: onGame, at: CACurrentMediaTime())
        if !hoverSeen { refreshFocus(onGame ? "tap on the game view" : "tap elsewhere") }
    }

    // MARK: - keyboard

    private func attachKeyboard(_ kb: GCKeyboard?) {
        guard let kb else { return }
        guard let input = kb.keyboardInput else {
            if !keyboardConnected {
                log("keyboard connected: \(kb.vendorName ?? "keyboard") keyboardInput=NIL (no key stream)")
            }
            return
        }
        kb.handlerQueue = .main
        input.keyChangedHandler = { [weak self] _, _, code, pressed in
            self?.key(code, pressed)
        }
        if !keyboardConnected {
            keyboardConnected = true
            log("keyboard connected: \(kb.vendorName ?? "keyboard")")
            refreshFocus("keyboard connected")
        }
    }

    private func detachKeyboard() {
        cancelPaste()
        deferredCommands.removeAll()
        // A key held while the keyboard's battery dies never sends its up.
        keys.reset()
        syncKeys()
        keyRepeat.cancel(); scheduleKeyRepeat()
        keyboardConnected = GCKeyboard.coalesced?.keyboardInput != nil
        log("keyboard disconnected (coalesced still present=\(keyboardConnected))")
        refreshFocus("keyboard disconnected")
    }

    private func key(_ code: GCKeyCode, _ pressed: Bool) {
        tickKeys += 1
        guard let vk = HardwareKeyMap.vk(forHIDUsage: code.rawValue) else {
            // An unmapped key is a key the program did not receive; the usage
            // number names it exactly.
            if pressed { log("unmapped HID usage 0x\(String(code.rawValue, radix: 16))") }
            return
        }
        refreshFocus("key")
        let isCommand = vk == 0x5B || vk == 0x5C
        if !pressed, deferredCommands.remove(vk) != nil,
           keys.wanted(focused: keyboardFocused).contains(vk) {
            cancelPaste()
            winios_post_key(vk, 1); winios_post_key(vk, 0)
        }
        if pressed { keys.press(vk, focused: keyboardFocused) } else { keys.release(vk) }
        if Self.pasteShortcut, pressed, keyboardFocused, isCommand, keys.physical.isSubset(of: [0x5B, 0x5C]) {
            deferredCommands.insert(vk)
        }
        if Self.pasteShortcut, pressed, keyboardFocused, PasteText.isCommandV(vk, held: keys.physical) {
            // A first-responder text bridge owns UIKit's paste command. Do
            // not discard it merely because GCKeyboard is connected: UIKit
            // can consume the shortcut before the raw V callback arrives.
            for key in keys.physical { keys.block(key) }
            deferredCommands.removeAll()
            syncKeys()
            let responder = FirstResponder.current
            if responder !== MetalBackedView.keyboardTarget && !(responder is LibraryKeyInput) {
                pasteClipboard()
            }
            return
        }
        if pressed, !isCommand { cancelPaste() }
        if pressed, !isCommand, !deferredCommands.isEmpty {
            // Post the modifier BEFORE the other key, even though Windows VK
            // sort order places e.g. A before LWIN.
            let wanted = keysPosted.down.union(deferredCommands.intersection(keys.wanted(focused: keyboardFocused)))
            for command in keysPosted.update(wanted).down { winios_post_key(command, 1) }
            deferredCommands.removeAll()
        }
        if pressed, keyboardFocused, Self.pointerLockAvailable,
           HardwareKeyMap.isPointerLockChord(vk, held: keys.physical) {
            keys.block(vk)
            syncKeys()
            keyRepeat.cancel(); scheduleKeyRepeat()
            togglePointerLock(why: "Ctrl+Alt+P")
            return
        }
        syncKeys()
        updateKeyRepeat(vk, pressed)
        startTicker()
    }

    private func syncKeys() {
        let edges = keysPosted.update(keys.wanted(focused: keyboardFocused).subtracting(deferredCommands))
        for vk in edges.up { winios_post_key(vk, 0) }
        for vk in edges.down { winios_post_key(vk, 1) }
        minuteKeys += edges.down.count
    }

    // MARK: - key repeat (madeira-bcd, 2026-10-18; env.MADEIRA_KEY_REPEAT = 1)

    /// A press the program saw starts its repeat (and ends the previous key's,
    /// as on Windows); any other press, or the repeating key's release, ends it.
    private func updateKeyRepeat(_ vk: Int32, _ pressed: Bool) {
        if !pressed {
            keyRepeat.released(vk)
        } else if keysPosted.down.contains(vk), keyRepeatEnabled {
            keyRepeat.pressed(vk, at: CACurrentMediaTime())
        } else {
            keyRepeat.cancel()
        }
        scheduleKeyRepeat()
    }

    /// A timer runs only while a key is repeating.
    private func scheduleKeyRepeat() {
        if keyRepeat.key == nil {
            keyRepeatTimer?.invalidate()
            keyRepeatTimer = nil
            return
        }
        guard keyRepeatTimer == nil else { return }
        let t = Timer(timeInterval: KeyRepeat.period / 2, repeats: true) { [weak self] _ in self?.keyRepeatTick() }
        RunLoop.main.add(t, forMode: .common)
        keyRepeatTimer = t
    }

    private func keyRepeatTick() {
        guard let vk = keyRepeat.key, keyboardFocused, keysPosted.down.contains(vk) else {
            keyRepeat.cancel()
            scheduleKeyRepeat()
            return
        }
        guard let due = keyRepeat.due(at: CACurrentMediaTime()) else { return }
        winios_post_key(due, 1)
        minuteRepeats += 1
    }

    /// env.MADEIRA_PASTE_SHORTCUT = 1 (the game's own file; OFF unless set): Command-V on a hardware keyboard pastes the clipboard as
    /// typed text instead of reaching the program as the Windows key and V. It defers every Command press until its partner is
    /// known (a Command tap or another chord still reaches Windows, after the delay), which changes the timing of Command chords
    /// for the program, so a working game's keyboard stays as it was. The key bar's Paste button does not depend on it.
    static var pasteShortcut: Bool { getenv("MADEIRA_PASTE_SHORTCUT").map { String(cString: $0) == "1" } ?? false }

    /// Read only in response to an explicit paste gesture, never on a timer.
    func pasteClipboard() {
        guard canPaste, let text = UIPasteboard.general.string else { return }
        paste(text)
    }

    var canPaste: Bool { !LibraryModel.shared.menu && computeBaseFocus() && !typingElsewhereForPaste() }

    func paste(_ text: String) {
        guard canPaste else { return }
        deferredCommands.removeAll()
        // Held Command/Ctrl/Shift must not turn the inserted text into hotkeys.
        // Suppress held keys until release, rather than inventing another down.
        for vk in keys.physical { keys.block(vk) }
        syncKeys()
        LibraryKeyboard.input?.releaseModifiers()
        pasteText.append(text)
        guard pasteText.next != nil, pasteTimer == nil else { return }
        let interval = PasteText.interval(milliseconds: getenv("MADEIRA_PASTE_INTERVAL_MS").map { String(cString: $0) })
        pasteInterval = interval
        let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
            self?.advancePaste()
        }
        pasteTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func advancePaste() {
        guard canPaste, let stroke = pasteText.next else { cancelPaste(); return }
        // Retry the SAME scalar under queue pressure. A whole character's
        // down/up (and Shift, or a UTF-16 surrogate pair) is drained together.
        if winios_try_post_text(stroke.scalar, stroke.vk, stroke.shift ? 1 : 0, Int32(pasteInterval * 1000)) != 0 {
            pasteText.advance()
            if pasteText.next == nil {
                pasteTimer?.invalidate(); pasteTimer = nil
            }
        }
    }

    func cancelPaste() {
        pasteTimer?.invalidate(); pasteTimer = nil; pasteText.cancel()
        winios_cancel_text()
    }

    // MARK: - mouse

    // HANDLER SIGNATURES, since a wrong one compiles and then never fires:
    //   mouseInput.mouseMovedHandler  : (GCMouseInput, Float, Float) -> Void
    //   button.pressedChangedHandler  : (GCControllerButtonInput, Float, Bool) -> Void
    //   scroll.valueChangedHandler    : (GCControllerDirectionPad, Float, Float) -> Void
    // `pressedChangedHandler` and `valueChangedHandler` on a button share one
    // type, so assigning to the wrong one type-checks and changes semantics.
    private func attachMouse(_ mouse: GCMouse?, why: String) {
        guard let mouse else { return }
        let fresh = attachedMice.insert(ObjectIdentifier(mouse)).inserted
        guard let m = mouse.mouseInput else {
            if fresh {
                log("mouse connected: \(mouse.vendorName ?? "mouse") via=\(why) mouseInput=NIL "
                    + "(no GC delta stream; UIKit indirect-pointer path only)")
            }
            noteMousePresent()
            return
        }
        mouse.handlerQueue = mouseQueue
        m.mouseMovedHandler = { [weak self] _, dx, dy in
            self?.moved(Double(dx), Double(dy))
        }
        m.leftButton.pressedChangedHandler = { [weak self] _, _, pressed in
            self?.gcButton(.left, pressed)
        }
        m.rightButton?.pressedChangedHandler = { [weak self] _, _, pressed in
            self?.gcButton(.right, pressed)
        }
        m.middleButton?.pressedChangedHandler = { [weak self] _, _, pressed in
            self?.gcButton(.middle, pressed)
        }
        // Side buttons in the order the device reports them. Windows has
        // exactly two; anything beyond is dropped rather than invented.
        for (i, aux) in (m.auxiliaryButtons ?? []).enumerated() where i < 2 {
            let b: MouseButton = i == 0 ? .x1 : .x2
            aux.pressedChangedHandler = { [weak self] _, _, pressed in
                self?.gcButton(b, pressed)
            }
        }
        m.scroll.valueChangedHandler = { [weak self] _, x, y in
            let wheel = GCMouseWheel.axes(Double(x), Double(y))
            self?.scrolled(wheel.dx, wheel.dy)
        }
        noteMousePresent()
        if fresh {
            log("mouse connected: \(mouse.vendorName ?? "mouse") via=\(why) "
                + "current=\(GCMouse.current === mouse) right=\(m.rightButton != nil) "
                + "middle=\(m.middleButton != nil) aux=\(m.auxiliaryButtons?.count ?? 0)")
        }
    }

    /// Both attach routes: a pointing device exists. Says nothing about
    /// whether it REPORTS.
    private func noteMousePresent() {
        if !mouseConnected {
            mouseConnected = true
            refreshFocus("mouse connected")
        }
        armAssistiveTouchHint()
    }

    private func detachMouse(_ mouse: GCMouse?) {
        if let mouse { attachedMice.remove(ObjectIdentifier(mouse)) }
        pendingButtons.removeAll()
        gameButtons.removeAll()
        syncButtons()
        let remaining = GCMouse.mice()
        // The UIKit path never depended on a GCMouse object; only a GC-less AND
        // UIKit-less state is "no mouse".
        mouseConnected = !remaining.isEmpty || uikitSeen
        if remaining.isEmpty {
            gcDeltaSeen = false
            gcButtonSeen = false
            attachedMice.removeAll()
            motionLock.lock(); gcDeltaLive = false; motionLock.unlock()
            if mousePath == .gcmouse { mousePath = .none; log("mouse path: none") }
            setPointerLocked(false, byUs: false, why: "mouse disconnected")
            setMouseInUse(false)
        }
        log("mouse disconnected: \(mouse?.vendorName ?? "?") (remaining=\(remaining.count) "
            + "path=\(mousePath.rawValue))")
        refreshFocus("mouse disconnected")
    }

    /// GameController reports y pointing UP, like a desk; Windows reports y
    /// pointing DOWN, like a screen. The negation is that difference. Runs on
    /// `mouseQueue`. On the absolute route the program's cursor follows the
    /// pointer's position instead, and the delta only says the mouse is in use.
    private func moved(_ dx: Double, _ dy: Double) {
        let now = CACurrentMediaTime()
        logRaw("gcmouse", dx, dy)
        noteDelivery(now)
        var first = false, wake = false
        motionLock.lock()
        let r = route
        if dx != 0 || dy != 0 {
            lastGCDeltaAt = now
            if !gcDeltaLive { gcDeltaLive = true; first = true }
            if !mouseInUseMirror { mouseInUseMirror = true; wake = true }
        }
        motionLock.unlock()
        // Post before the main hop: the delta is the thing with a deadline.
        if r == .relative { postMotion(dx, -dy) }
        if first || wake {
            let isFirst = first
            DispatchQueue.main.async { [weak self] in
                if isFirst { self?.firstGCDelta() }
                self?.setMouseInUse(true)
            }
        }
    }

    private func firstGCDelta() {
        gcDeltaSeen = true
        announcePath(.gcmouse)
        if assistiveTouchHint { assistiveTouchHint = false }
        refreshFocus("first GCMouse delta")
    }

    /// The single place a screen-down delta becomes Windows motion, shared by
    /// the GCMouse handler and the UIKit fallback so both use the same gain
    /// and the same carry. Callable from either queue.
    private func postMotion(_ dx: Double, _ dy: Double) {
        // One aligned Double read of a value only the slider writes.
        let gain = InputSettings.shared.sensMouse
        motionLock.lock()
        let d = carry.add(dx, dy, gain: gain)
        motionLock.unlock()
        postRelative(d.dx, d.dy)
    }

    /// Post one integer relative move (RELATIVE: see the header comment).
    /// Also the controller right-stick mouse's exit (`PadStickMouse`), which
    /// keeps its own carry and checks focus itself. Callable from either queue.
    func postRelative(_ dx: Int32, _ dy: Int32) {
        guard Self.enabled, dx != 0 || dy != 0 else { return }
        let desktop = Self.desktopMode
        motionLock.lock()
        tickDX += Double(dx); tickDY += Double(dy)
        minuteRelative += 1
        var syncCursor = false
        if desktop, !desktopCursorSynced { desktopCursorSynced = true; syncCursor = true }
        motionLock.unlock()
        winios_pointer(dx, dy, Self.moveFlag, 0)
        if desktop && cursorShownForRoute { followDesktopCursor(dx, dy, sync: syncCursor) }
        bumpTicker()
    }

    /// Desktop session only: relative motion moves nothing that the drawn
    /// arrow follows (winios_pointer draws only absolute positions). Keep
    /// MetalBackedView's trackpad cursor, which is also the touch trackpad's
    /// position, in step and draw it there. The first time, one absolute move
    /// to the new position puts Wine's cursor at the same place, so the two
    /// agree from then on (until a program moves or clips its cursor itself;
    /// the next touch or absolute move re-syncs them).
    private func followDesktopCursor(_ dx: Int32, _ dy: Int32, sync: Bool) {
        DispatchQueue.main.async {
            let cur = MetalBackedView.cursor
            let desk = Self.screenSize()
            let p = DesktopCursor.advance(x: Double(cur.x), y: Double(cur.y), dx: dx, dy: dy,
                                          width: desk.w, height: desk.h)
            MetalBackedView.cursor = CGPoint(x: p.x, y: p.y)
            if sync {
                // Also draws the arrow (winios_pointer draws absolute moves).
                winios_pointer(Int32(p.x), Int32(p.y), Self.moveFlag | Self.absoluteFlag, 0)
            } else {
                winios_cursor_move(Int32(p.x), Int32(p.y))
            }
        }
    }

    /// Absolute route: put the program's cursor where the iOS pointer is on
    /// the game view. Main thread.
    private func postAbsolute(_ p: CGPoint, in view: UIView) {
        let desktop = Self.desktopMode
        let s = guestPoint(p, in: view)
        setMouseInUse(true)
        if let last = lastAbsolute, last.x == s.x, last.y == s.y { return }
        lastAbsolute = s
        if desktop {
            // The touch trackpad continues from here; the compositor's arrow
            // follows the absolute move.
            MetalBackedView.cursor = CGPoint(x: Int(s.x), y: Int(s.y))
            motionLock.lock(); desktopCursorSynced = true; motionLock.unlock()
        }
        winios_pointer(s.x, s.y, Self.moveFlag | Self.absoluteFlag, 0)
        minuteAbsolute += 1
        updateTracking()
        renderCursor()
    }

    /// madeira-bcd (2026-10-18): where a point on the game view lands on the
    /// Windows screen, the same mapping a finger's touch takes
    /// (MetalBackedView.mapPoint): the desktop compositor's own mapping in the
    /// desktop session; otherwise the presented game rect (MetalHostView, laid
    /// out by GameSurfaceLayout for the session's Fit/Fill/Stretch/Aspect) at
    /// the virtual monitor's live size. It was a fixed 1024x768 surface fitted
    /// in the view, so the program's cursor lagged the pointer by the ratio of
    /// the two sizes and never reached the far edges.
    private func guestPoint(_ p: CGPoint, in view: UIView) -> (x: Int32, y: Int32) {
        if Self.desktopMode {
            let w = view.convert(p, to: nil)
            var px: Int32 = 0, py: Int32 = 0
            if winios_desktop_point_from_window(Double(w.x), Double(w.y), &px, &py) != 0 { return (px, py) }
        }
        let g = Self.screenSize()
        let host = MetalHostView.shared
        if let hw = host.window, hw === view.window, host.bounds.width > 0, host.bounds.height > 0 {
            let q = view.convert(p, to: host)
            return ScreenMap.toGuest(x: Double(q.x), y: Double(q.y), rectW: Double(host.bounds.width),
                                     rectH: Double(host.bounds.height), guestW: g.w, guestH: g.h)
        }
        return ScreenMap.toScreen(x: Double(p.x), y: Double(p.y), viewW: Double(view.bounds.width),
                                  viewH: Double(view.bounds.height), screenW: g.w, screenH: g.h)
    }

    /// Delivery cadence while diagnostics are on, once every 10 s from the
    /// mouse queue. `rate` near 60/s means the accessibility layer coalesces to
    /// the display; a `gap max` far above the mean is a sample that waited.
    private func noteDelivery(_ now: CFTimeInterval) {
        var line: String?
        motionLock.lock()
        if devWindowStart == 0 { devWindowStart = now }
        if devLastAt != 0 {
            let gap = now - devLastAt
            devGapSum += gap
            if gap > devGapMax { devGapMax = gap }
        }
        devLastAt = now
        devCount += 1
        let span = now - devWindowStart
        if span >= Self.deliveryWindow {
            let gaps = max(devCount - 1, 1)
            line = String(format: "delivery %.1fs: events=%d rate=%.1f/s gap mean=%.1fms max=%.1fms",
                          span, devCount, Double(devCount) / span,
                          devGapSum / Double(gaps) * 1000, devGapMax * 1000)
            devWindowStart = now; devCount = 0; devGapSum = 0; devGapMax = 0
        }
        motionLock.unlock()
        if let line, InputSettings.shared.diagnostics { log(line) }
    }

    /// GCMouse button handlers run on `mouseQueue`. The timestamp is taken
    /// here, before the hop: it is what `shouldIgnore` and click focus compare
    /// a tap against.
    private func gcButton(_ b: MouseButton, _ pressed: Bool) {
        let t = CACurrentMediaTime()
        motionLock.lock(); lastGCButtonAt = t; motionLock.unlock()
        DispatchQueue.main.async { [weak self] in
            self?.buttonChanged(b, pressed, at: t)
        }
    }

    /// A press goes to the program only if it has the mouse's focus when the
    /// press happens; a release always follows its press. Main thread.
    private func buttonChanged(_ b: MouseButton, _ pressed: Bool, at t: CFTimeInterval) {
        gcButtonSeen = true
        if pressed {
            setMouseInUse(true)
            refreshFocus("button")
            if !baseFocused {
                // nothing: the program does not have focus
            } else if !Self.focusEnabled || pointerLocked || hoverSeen {
                if mouseFocused { gameButtons.insert(b) }
            } else if let toGame = clickFocus.route(buttonAt: t, now: CACurrentMediaTime()) {
                if toGame { gameButtons.insert(b) }
            } else {
                pendingButtons[b] = (t, false)
                DispatchQueue.main.asyncAfter(deadline: .now() + ClickFocus.window) { [weak self] in
                    self?.resolvePendingButton(b)
                }
            }
        } else {
            if let p = pendingButtons[b] { pendingButtons[b] = (p.at, true) }
            gameButtons.remove(b)
        }
        syncButtons()
        // A drag that left the game view keeps the mouse only while held.
        if !pressed && gameButtons.isEmpty { refreshFocus("button up") }
        startTicker()
    }

    /// iPhone: the tap that carries this press has had its chance to arrive.
    private func resolvePendingButton(_ b: MouseButton) {
        guard let p = pendingButtons.removeValue(forKey: b) else { return }
        let toGame = baseFocused
            && (clickFocus.route(buttonAt: p.at, now: CACurrentMediaTime()) ?? clickFocus.onGame)
        guard toGame else { return }
        gameButtons.insert(b)
        syncButtons()
        if p.released {
            gameButtons.remove(b)
            syncButtons()
        }
    }

    private func syncButtons() {
        let edges = buttonsPosted.update(gameButtons)
        for b in edges.up { let e = b.event(down: false); winios_pointer(0, 0, e.flags, e.data) }
        for b in edges.down { let e = b.event(down: true); winios_pointer(0, 0, e.flags, e.data) }
        minuteButtons += edges.down.count
    }

    /// Reached from the mouse queue (GCMouse scroll) and from main (UIKit,
    /// which only scrolls a view the pointer is over).
    private func scrolled(_ x: Double, _ y: Double) {
        motionLock.lock()
        let blocked = route == .blocked
        let notches = blocked ? [] : wheel.add(x, y)
        tickWheel += notches.count
        minuteWheel += notches.count
        motionLock.unlock()
        for n in notches { winios_pointer(0, 0, n.flags, UInt32(bitPattern: n.delta)) }
        if !notches.isEmpty { bumpTicker() }
    }

    // MARK: - route, drawn cursor and automatic lock

    private var cursorShownForRoute: Bool {
        if InputSettings.shared.relative { return false }
        if Self.desktopMode { return cursorState.reports == 0 || cursorState.shown != 0 }
        return directCursorLive && cursorState.shown != 0
    }

    private func updateRoute() {
        // madeira-bcd (2026-10-18): `routeLocked` and `follow` (see their notes);
        // with neither in play this is the route it always was.
        let follow = followsPointer
        let r = PointerPolicy.route(focused: mouseFocused, hover: hoverSeen, locked: routeLocked,
                                    cursorShown: cursorShownForRoute, absoluteAllowed: Self.absoluteEnabled,
                                    follow: follow)
        if r != currentRoute {
            currentRoute = r
            if r != .absolute { lastAbsolute = nil }
            motionLock.lock(); route = r; carry.reset(); motionLock.unlock()
            if diagnostics || (follow && routeLogged < 20) {
                routeLogged += 1
                log("pointer route: \(r.rawValue)" + (follow ? " (the hidden cursor follows the pointer)" : ""))
            }
        }
        updateTracking()
    }

    /// The driver's position reports are wanted while the drawn cursor follows
    /// Wine's cursor, and as the automatic lock's sign of a live program; and
    /// (madeira-bcd) whenever the program hides its cursor, so that sign stays
    /// on while a following cursor takes absolute moves.
    private func updateTracking() {
        guard directCursorLive else { return }
        let on = mouseInUse && (currentRoute == .relative || lastAbsolute == nil || cursorState.shown == 0)
        winios_direct_cursor_track(on ? 1 : 0)
    }

    private func setMouseInUse(_ on: Bool) {
        if !on { touchCursorActive = false }
        guard on != mouseInUse else {
            if !on { renderCursor() }
            return
        }
        mouseInUse = on
        motionLock.lock(); mouseInUseMirror = on; motionLock.unlock()
        updateTracking()
        renderCursor()
    }

    private var touchCursorActive = false

    /// Trackpad or tap-to-click touch moved the pointer in a direct game session:
    /// draw the program's cursor at that guest position.
    func noteTouchCursor(x: Int32, y: Int32) {
        guard directCursorLive, !InputSettings.shared.relative else { return }
        touchCursorActive = true
        lastAbsolute = (x, y)
        renderCursor()
    }

    /// PadStickMouse moved the program's cursor relatively: the drawn cursor
    /// follows Wine's position again.
    func stickMoved() {
        lastAbsolute = nil
        setMouseInUse(true)
        updateTracking()
    }

    /// WiniosCursor.c: the program changed its cursor, or Wine's cursor moved
    /// while tracked; (madeira-bcd) or it warped or clipped its cursor, or the
    /// foreground program's mouse reader changed. Main thread.
    func directCursorChanged() {
        let before = cursorState
        winios_direct_cursor_get(&cursorState)
        let now = CACurrentMediaTime()
        lastReportAt = now
        if cursorState.image_serial != before.image_serial { DirectCursorOverlay.shared.loadImage() }
        // A warp (SetCursorPos) holds until the pointer moves again: the drawn
        // cursor shows Wine's position, and the next absolute move is posted
        // even where the pointer had been. Measured against where the pointer
        // had put the cursor, for MouseCapture.
        let warps = Int(cursorState.warps &- before.warps)
        var warpDistance: Double?
        if warps > 0 {
            minuteWarps += warps
            if let last = lastAbsolute {
                let w = Double(max(Self.screenSize().w, 1))
                var wx = cursorState.x, wy = cursorState.y
                _ = winios_pointer_map_invert(&wx, &wy)   // build 646, [ptr-map]: in the picture's space, as lastAbsolute is
                let dx = Double(wx) - Double(last.x), dy = Double(wy) - Double(last.y)
                warpDistance = (dx * dx + dy * dy).squareRoot() / w
                lastAbsolute = nil
                updateTracking()
            }
        }
        if before.reports == 0 || cursorState.shown != before.shown {
            cursorHiddenSince = cursorState.shown != 0 ? 0 : now
            capture.reset()
            captureLogged = .unknown
            if cursorState.shown != before.shown {
                autoLockSuppressed = false
                userReleased = false
                cursorToggles += 1
                // A program can toggle every frame: the first 40, then a count.
                if cursorToggles <= 40 {
                    log("program cursor \(cursorState.shown != 0 ? "shown" : "hidden")")
                } else if cursorToggles % 1000 == 0 {
                    log("program cursor shown or hidden \(cursorToggles) times (one line each for the first 40)")
                }
            }
            classifyHiddenCursor(warps: warps, distance: warpDistance, now: now)
            refreshFocus("program cursor")
        } else {
            classifyHiddenCursor(warps: warps, distance: warpDistance, now: now)
            updateAutoLock()
            renderCursor()
        }
    }
    private var cursorToggles = 0

    /// The program's cursor, drawn where Wine's is: where the last absolute
    /// move put it, clamped as the wineserver clamps it to the program's clip
    /// rectangle (madeira-bcd), or where the driver reported it.
    private func renderCursor() {
        guard directCursorLive else { return }
        let shown = cursorState.shown != 0 || (touchCursorActive && cursorState.reports == 0)
        let visible = (mouseInUse || touchCursorActive) && mouseFocused && shown && !InputSettings.shared.relative
        var pos = (x: cursorState.x, y: cursorState.y)   // screen pixels, where Windows has its cursor
        if let last = lastAbsolute {
            // madeira-bcd (build 646, [ptr-map]): lastAbsolute is in the picture's space; Wine clips in screen pixels. Unchanged
            // (both calls do nothing) unless a windowed game's picture fills the view (Winios.h, winios_pointer_map_apply).
            var sx = last.x, sy = last.y
            _ = winios_pointer_map_apply(&sx, &sy)
            pos = ScreenMap.clamp(x: sx, y: sy, clipLeft: cursorState.clip_left, top: cursorState.clip_top,
                                  right: cursorState.clip_right, bottom: cursorState.clip_bottom)
        }
        var px = pos.x, py = pos.y
        _ = winios_pointer_map_invert(&px, &py)   // build 646: back to the picture's space, where the overlay draws
        let g = Self.screenSize()
        DirectCursorOverlay.shared.render(visible: visible, x: px, y: py, guestW: g.w, guestH: g.h)
    }

    private func updateAutoLock() {
        guard Self.autoLockEnabled, Self.directCursorEnabled, Self.pointerLockAvailable, mousePath == .gcmouse else {
            if pointerLocked && lockedByUs { setPointerLocked(false, byUs: true, why: "automatic lock unavailable") }
            return
        }
        let now = CACurrentMediaTime()
        motionLock.lock(); let lastDelta = lastGCDeltaAt; motionLock.unlock()
        // madeira-bcd: env.MADEIRA_POINTER_FOLLOW = 1 gives the game's hidden
        // cursor the pointer's position instead of a lock.
        let wantsLock = followMode != .always
        let relOptIn = InputSettings.shared.relative
        let shown = !relOptIn && cursorState.shown != 0
        let hiddenFor = relOptIn ? AutoLock.hiddenDelay : (cursorState.reports == 0 || cursorHiddenSince == 0 ? 0 : now - cursorHiddenSince)
        let sinceReport = (Self.desktopMode || relOptIn) ? 0 : (lastReportAt == 0 ? .infinity : now - lastReportAt)
        let action = AutoLock.decide(
            locked: pointerLocked, lockedByUs: lockedByUs, cursorShown: shown,
            hiddenFor: hiddenFor,
            pointerOver: pointerOver, focused: baseFocused,
            sinceReport: sinceReport,
            sinceMotion: lastDelta == 0 ? .infinity : now - lastDelta,
            wantsLock: wantsLock)
        switch action {
        case .lock where !autoLockSuppressed:
            setPointerLocked(true, byUs: true, why: "the program hides its cursor")
        case .unlock:
            setPointerLocked(false, byUs: true, why: shown ? "the program shows its cursor"
                             : !wantsLock ? "the program draws its own cursor (env.MADEIRA_POINTER_FOLLOW=1)"
                             : baseFocused ? "the program stopped reporting" : "focus lost")
        default:
            break
        }
    }

    // MARK: - AssistiveTouch clicks that arrive as fingers

    /// True while a real mouse is in the user's hand: the GCMouse path won and
    /// a delta arrived inside the last `mouseActiveWindow` seconds.
    var mouseActive: Bool {
        guard mousePath == .gcmouse else { return false }
        motionLock.lock(); let t = lastGCDeltaAt; motionLock.unlock()
        return t != 0 && CACurrentMediaTime() - t < Self.mouseActiveWindow
    }

    /// Classify one touch and say whether the caller must drop it. Without
    /// this, AssistiveTouch's synthesised click runs the finger path: an
    /// absolute move to its cursor that snaps the program's cursor there,
    /// after which relative deltas resume from the new place (click, jump,
    /// drift back). The click itself is not lost: GCMouse already reported it.
    /// The first 30 classifications are logged with both signals.
    @discardableResult
    func shouldIgnore(_ t: UITouch, logging: Bool) -> Bool {
        guard Self.enabled else { return false }
        let radius = Double(t.majorRadius)
        motionLock.lock(); let btnAt = lastGCButtonAt; motionLock.unlock()
        let dt = btnAt == 0 ? Double.infinity : CACurrentMediaTime() - btnAt
        let synthesised = SynthesizedTouch.looksSynthesized(direct: t.type == .direct,
                                                            majorRadius: radius,
                                                            secondsSinceButton: dt)
        if logging, touchClassLogged < 30, mouseConnected {
            touchClassLogged += 1
            log(String(format: "touch classified %@ radius=%.2f dt=%@ active=%@",
                       synthesised ? "synthesized" : "finger", radius,
                       dt.isFinite ? String(format: "%.0fms", dt * 1000) : "never",
                       mouseActive ? "yes" : "no"))
        }
        return synthesised && mouseActive && InputSettings.shared.ignoreTouchesWithMouse
    }

    /// True when EVERY touch in the event is synthesised, the only case where
    /// dropping the whole callback is safe.
    func shouldIgnore(_ touches: Set<UITouch>, logging: Bool) -> Bool {
        guard !touches.isEmpty else { return false }
        var all = true
        for t in touches where !shouldIgnore(t, logging: logging) { all = false }
        return all
    }

    enum TouchPhase { case began, moved, ended, cancelled }

    /// Called first by each of MetalBackedView's touch overrides. Returns true
    /// when the touches belong to the mouse and must not reach the finger
    /// paths: an indirect-pointer touch is a mouse BUTTON (read off
    /// `UIEvent.buttonMask`), and its motion is carried by `PointerFallback`'s
    /// recognisers; a synthesised AssistiveTouch click is dropped. A real
    /// finger on the game view hides the drawn cursor: the finger is the
    /// pointer until the mouse moves again.
    func interceptTouches(_ touches: Set<UITouch>, _ event: UIEvent?, _ phase: TouchPhase) -> Bool {
        guard Self.enabled else { return false }
        if touches.contains(where: { $0.type == .indirectPointer }) {
            switch phase {
            case .moved: break
            case .cancelled: uikitButtons([])       // no mask worth reading: drop all
            case .began, .ended: pointerButtons(event, ending: phase == .ended)
            }
            return true
        }
        let drop = shouldIgnore(touches, logging: phase == .began)
        if !drop, phase == .began { setMouseInUse(false) }
        return drop
    }

    private func pointerButtons(_ event: UIEvent?, ending: Bool) {
        var mask = event?.buttonMask ?? []
        if ending {
            // A mask that still lists the button being released: if nothing
            // indirect is still down, nothing is held.
            let live = (event?.allTouches ?? []).filter {
                $0.type == .indirectPointer && $0.phase != .ended && $0.phase != .cancelled
            }
            if live.isEmpty { mask = [] }
        }
        var want: Set<MouseButton> = []
        if mask.contains(.primary) { want.insert(.left) }
        if mask.contains(.secondary) { want.insert(.right) }
        if mask.contains(UIEvent.ButtonMask.button(3)) { want.insert(.middle) }
        if mask.contains(UIEvent.ButtonMask.button(4)) { want.insert(.x1) }
        if mask.contains(UIEvent.ButtonMask.button(5)) { want.insert(.x2) }
        // UIKit's pointer-compatibility mode reports a click as a touch with an
        // empty mask. It is a left click.
        if !ending && want.isEmpty { want.insert(.left) }
        uikitButtons(want)
    }

    private func armAssistiveTouchHint() {
        guard !hintArmed, UIDevice.current.userInterfaceIdiom == .phone else { return }
        hintArmed = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 10.0) { [weak self] in
            guard let self, !self.gcDeltaSeen, !self.uikitSeen, self.mouseConnected else { return }
            self.assistiveTouchHint = true
            self.log("assistive-touch hint shown: mouse enumerated, no delta in 10s")
        }
    }

    func dismissAssistiveTouchHint() {
        guard assistiveTouchHint else { return }
        assistiveTouchHint = false
        log("assistive-touch hint dismissed")
    }

    // MARK: - UIKit pointer: position (every path) and the indirect-pointer fallback
    //
    // Hover and pointer-drag positions drive the absolute route and pointer
    // focus on every path. Their deltas carry motion only on the fallback
    // path. Deltas are screen-down view points and BEST-EFFORT: an unlocked
    // iOS pointer is clamped to the screen, so at an edge the hover deltas
    // stop, and iOS has no API to warp or re-centre it. The GCMouse + pointer
    // lock path is the complete solution where the OS provides it.

    /// Hover over the game view (`inside` false: the pointer left it).
    func pointerHovered(at p: CGPoint, in view: UIView, inside: Bool) {
        guard Self.enabled else { return }
        guard inside else {
            if pointerOver { pointerOver = false; refreshFocus("pointer left the game view") }
            return
        }
        if !hoverSeen || !pointerOver {
            hoverSeen = true
            pointerOver = true
            refreshFocus("pointer over the game view")
        }
        noteHiddenMotion(at: CACurrentMediaTime())
        if currentRoute == .absolute { postAbsolute(p, in: view) }
    }

    /// A pointer drag (a button held) on the game view: hover is silent then.
    func pointerDragged(at p: CGPoint, in view: UIView) {
        guard Self.enabled else { return }
        noteHiddenMotion(at: CACurrentMediaTime())
        guard currentRoute == .absolute else { return }
        postAbsolute(p, in: view)
    }

    func uikitMoved(_ dx: CGFloat, _ dy: CGFloat, src: String) {
        guard Self.enabled, !gcLive else { return }     // the HID stream owns it
        guard dx != 0 || dy != 0 else { return }
        logRaw(src, Double(dx), Double(dy))
        noteUIKitPointer()
        setMouseInUse(true)
        guard currentRoute == .relative else { return }
        postMotion(Double(dx), Double(dy))
    }

    /// The complete set of buttons UIKit says are down, declared, not edged.
    /// These touches reach the game view only while the pointer is over it.
    func uikitButtons(_ want: Set<MouseButton>) {
        guard Self.enabled, !gcLive else { return }
        let allowed = baseFocused ? want : []
        if !want.isEmpty { noteUIKitPointer(); setMouseInUse(true) }
        guard allowed != gameButtons else { return }
        gameButtons = allowed
        syncButtons()
        startTicker()
    }

    /// 14 points per notch, the ratio the touch trackpad's two-finger scroll uses.
    func uikitScroll(_ dxPoints: CGFloat, _ dyPoints: CGFloat) {
        guard Self.enabled, !gcLive else { return }
        guard dxPoints != 0 || dyPoints != 0 else { return }
        noteUIKitPointer()
        scrolled(Double(dxPoints) / 14.0, Double(dyPoints) / 14.0)
    }

    private func noteUIKitPointer() {
        guard !uikitSeen else { return }
        uikitSeen = true
        if assistiveTouchHint { assistiveTouchHint = false }
        noteMousePresent()
        announcePath(.uikit)
    }

    // MARK: - pointer lock

    /// The lock button and Ctrl+Alt+P. Releasing an automatic lock keeps it
    /// off until the program's cursor next changes visibility.
    func togglePointerLock(why: String = "toggle") {
        // madeira-bcd (2026-10-18): also gives back a pointer the program holds
        // without a lock (a cursor that follows it), so the mouse can reach BCD's
        // buttons; pressed again, it captures the pointer again.
        if pointerLocked || pointerHeldByProgram {
            if lockedByUs || followsPointer { autoLockSuppressed = true }
            userReleased = true
            if pointerLocked {
                setPointerLocked(false, byUs: false, why: why)
            } else {
                log("pointer given back to BCD (\(why)); the program still gets it over the game view")
                refreshFocus("pointer given back")
            }
        } else {
            userReleased = false
            setPointerLocked(true, byUs: false, why: why)
        }
    }

    private func setPointerLocked(_ on: Bool, byUs: Bool, why: String) {
        var want = on && mouseConnected && Self.lockEnabled
        // iPhone has no system pointer to lock (see `pointerLockAvailable`).
        // Nothing is lost: GCMouse deltas are raw HID reports and keep arriving
        // while the AssistiveTouch cursor sits against a screen edge.
        if want, !Self.pointerLockAvailable {
            if !phoneLockNoted {
                phoneLockNoted = true
                log("pointer lock: unavailable on iPhone (AssistiveTouch pointer)")
            }
            want = false
        }
        // Locking while UIKit carries the mouse would END it: the lock stops
        // UIKit pointer delivery, and this device has no GCMouse stream.
        if want, mousePath == .uikit {
            log("pointer lock refused (\(why)): path=uikit")
            want = false
        }
        guard want != pointerLocked else { return }
        pointerLocked = want
        lockedByUs = want && byUs
        if want {
            // madeira-bcd: a request is an ask; updateLockState reads iPadOS's
            // answer, and asks again once the answer window has passed.
            lockRequestedAt = CACurrentMediaTime()
            lockRefusalLogged = false
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.lockAnswerWindow + 0.05) { [weak self] in
                self?.refreshFocus("pointer lock answer")
            }
        }
        PointerLock.refresh()
        log("pointer lock \(want ? "ON" : "OFF") (\(why)) path=\(mousePath.rawValue)")
        refreshFocus("pointer lock")
    }

    // MARK: - 1 Hz activity line (diagnostics only)

    /// `Timer` and `RunLoop.main` are main-thread objects; `tickerArmed` is
    /// the lock-guarded mirror the mouse queue reads.
    private func bumpTicker() {
        guard InputSettings.shared.diagnostics else { return }
        if Thread.isMainThread { startTicker(); return }
        motionLock.lock(); let armed = tickerArmed; motionLock.unlock()
        guard !armed else { return }
        DispatchQueue.main.async { [weak self] in self?.startTicker() }
    }

    private func startTicker() {
        guard ticker == nil, diagnostics else { return }
        let t = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(t, forMode: .common)
        ticker = t
        motionLock.lock(); tickerArmed = true; motionLock.unlock()
    }

    private func tick() {
        motionLock.lock()
        let dx = tickDX, dy = tickDY, wheelN = tickWheel
        tickDX = 0; tickDY = 0; tickWheel = 0
        motionLock.unlock()
        let idle = tickKeys == 0 && wheelN == 0 && dx == 0 && dy == 0
            && keys.physical.isEmpty && gameButtons.isEmpty
        if idle || !diagnostics {
            // Nothing moved and nothing is held: stand down.
            ticker?.invalidate(); ticker = nil
            motionLock.lock(); tickerArmed = false; motionLock.unlock()
            return
        }
        let keyList = keysPosted.down.sorted().map { String(format: "%02x", $0) }.joined(separator: ",")
        log("keys_down=[\(keyList)] mouse_dx=\(Int(dx)) mouse_dy=\(Int(dy)) "
            + "buttons=\(gameButtons.sorted().map(\.rawValue)) wheel=\(wheelN) "
            + "lock=\(pointerLocked ? "on" : "off") path=\(mousePath.rawValue) route=\(currentRoute.rawValue) "
            + "focus=kb:\(keyboardFocused ? 1 : 0),mouse:\(mouseFocused ? 1 : 0) events=\(tickKeys)")
        tickKeys = 0
    }
}

/// UIKit has no public "current first responder". An action sent to nil goes
/// to the first responder and walks up the chain from there, so the first
/// object that receives it is the first responder (or, with none, whatever
/// UIKit starts from, which is never a text input).
private enum FirstResponder {
    static weak var found: UIResponder?

    static var current: UIResponder? {
        found = nil
        UIApplication.shared.sendAction(#selector(UIResponder.madeiraReportFirstResponder),
                                        to: nil, from: nil, for: nil)
        return found
    }
}

extension UIResponder {
    @objc fileprivate func madeiraReportFirstResponder() {
        FirstResponder.found = self
    }
}


// ============================================================================
// POINTER LOCK.
//
// `prefersPointerLocked` hides and pins the iPad system pointer (containment:
// an unlocked pointer stops at the screen edge and starts hitting the app's
// own chrome). UIKit asks the KEY WINDOW's root view controller, and this
// app's root is SwiftUI's own UIHostingController, which the app never
// constructs and cannot subclass. So the override is ADDED to that concrete
// class at runtime. Swift generic classes get one ObjC class per
// specialisation, so the root's class has exactly one instance; the overlay
// windows' hosting controllers are different specialisations and untouched.
// iPadOS honours the preference only while the scene is full screen.
// ============================================================================

enum PointerLock {
    private static var installedClasses = Set<ObjectIdentifier>()

    /// Re-ask UIKit for the preference on every window's root view controller.
    static func refresh() {
        DispatchQueue.main.async {
            for w in allWindows() {
                if let root = w.rootViewController {
                    install(on: root)
                    root.setNeedsUpdateOfPrefersPointerLocked()
                }
            }
        }
    }

    private static func allWindows() -> [UIWindow] {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
    }

    private static func install(on root: UIViewController) {
        guard let cls: AnyClass = object_getClass(root) else { return }
        let id = ObjectIdentifier(cls)
        guard !installedClasses.contains(id) else { return }
        let sel = NSSelectorFromString("prefersPointerLocked")
        let body: @convention(block) (AnyObject) -> Bool = { _ in
            HardwareInput.shared.pointerLocked
        }
        let imp = imp_implementationWithBlock(body)
        // "B@:": returns BOOL, takes self and _cmd. Add first; replace only if
        // this exact class already had one.
        if !class_addMethod(cls, sel, imp, "B@:") {
            _ = class_replaceMethod(cls, sel, imp, "B@:")
        }
        installedClasses.insert(id)
        fputs("[hwinput] pointer lock installed on \(NSStringFromClass(cls))\n", stderr)
    }
}

/// Hides the iOS system pointer over the game surface, whether or not the lock
/// took: the program draws its own cursor at its own position.
final class PointerHider: NSObject, UIPointerInteractionDelegate {
    static let shared = PointerHider()

    func pointerInteraction(_ interaction: UIPointerInteraction,
                            styleFor region: UIPointerRegion) -> UIPointerStyle? {
        .hidden()
    }

    /// One region, the whole surface, so the hidden style never lapses back to
    /// the system arrow between per-location regions.
    func pointerInteraction(_ interaction: UIPointerInteraction,
                            regionFor request: UIPointerRegionRequest,
                            defaultRegion: UIPointerRegion) -> UIPointerRegion? {
        guard let v = interaction.view else { return defaultRegion }
        return UIPointerRegion(rect: v.bounds)
    }
}

/// The pointer recognisers must never win an arbitration: they recognise
/// simultaneously with everything, and `cancelsTouchesInView = false` keeps raw
/// touch delivery intact, so a mouse can never steal a finger.
final class PointerGestureDelegate: NSObject, UIGestureRecognizerDelegate {
    static let shared = PointerGestureDelegate()

    func gestureRecognizer(_ g: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
    func gestureRecognizer(_ g: UIGestureRecognizer,
                           shouldRequireFailureOf other: UIGestureRecognizer) -> Bool { false }
    func gestureRecognizer(_ g: UIGestureRecognizer,
                           shouldBeRequiredToFailBy other: UIGestureRecognizer) -> Bool { false }
}

/// Sees every touch that begins in the game view's window (fingers, pointer
/// clicks and AssistiveTouch's synthesised taps) and reports where it landed,
/// for click focus. It never recognises and never delays or cancels a touch.
final class TouchFocusObserver: UIGestureRecognizer {
    init() {
        super.init(target: nil, action: nil)
        cancelsTouchesInView = false
        delaysTouchesBegan = false
        delaysTouchesEnded = false
        delegate = PointerGestureDelegate.shared
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        for t in touches { HardwareInput.shared.touchBeganInWindow(t) }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) { finishIfIdle(event) }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) { finishIfIdle(event) }

    /// Fail once every touch of the sequence is over, so UIKit resets it for
    /// the next one; failing earlier would hide a second finger's touch.
    private func finishIfIdle(_ event: UIEvent) {
        let live = (event.allTouches ?? []).contains { $0.phase != .ended && $0.phase != .cancelled }
        if !live { state = .failed }
    }
}

/// The mouse as UIKit sees it on the game surface. iOS splits one pointer into
/// three shapes:
///   * hover:  movement with no button down (absolute positions);
///   * drag:   movement WITH a button down (hover stops during a drag), a pan
///             restricted to `.indirectPointer`;
///   * scroll: the wheel, a pan that accepts no touch type at all.
/// Positions drive pointer focus and the absolute route on every path; the
/// deltas carry motion when GameController cannot see the mouse. Buttons are
/// indirect-pointer touches, handled by `HardwareInput.interceptTouches`. None
/// of this can touch a finger.
final class PointerFallback: NSObject {
    private static let key = UnsafeMutableRawPointer.allocate(byteCount: 1, alignment: 1)

    private var hoverLast = CGPoint.zero
    private var hoverHasLast = false
    private var panLast = CGPoint.zero
    private var scrollLast = CGPoint.zero

    /// Idempotent per view. Does nothing with MADEIRA_HWINPUT=0.
    static func install(on view: UIView) {
        guard HardwareInput.enabled else { return }
        guard objc_getAssociatedObject(view, key) == nil else { return }
        let f = PointerFallback()
        objc_setAssociatedObject(view, key, f, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
        view.addInteraction(UIPointerInteraction(delegate: PointerHider.shared))

        let indirect = [NSNumber(value: UITouch.TouchType.indirectPointer.rawValue)]
        let hover = UIHoverGestureRecognizer(target: f, action: #selector(onHover(_:)))
        hover.cancelsTouchesInView = false
        hover.delegate = PointerGestureDelegate.shared
        view.addGestureRecognizer(hover)

        let drag = UIPanGestureRecognizer(target: f, action: #selector(onDrag(_:)))
        drag.allowedTouchTypes = indirect
        drag.allowedScrollTypesMask = []            // motion only; the wheel is separate
        drag.maximumNumberOfTouches = 1
        drag.cancelsTouchesInView = false
        drag.delaysTouchesBegan = false
        drag.delaysTouchesEnded = false
        drag.delegate = PointerGestureDelegate.shared
        view.addGestureRecognizer(drag)

        let scroll = UIPanGestureRecognizer(target: f, action: #selector(onScroll(_:)))
        scroll.allowedTouchTypes = []               // never a finger: scroll events only
        scroll.allowedScrollTypesMask = [.continuous, .discrete]
        scroll.cancelsTouchesInView = false
        scroll.delaysTouchesBegan = false
        scroll.delaysTouchesEnded = false
        scroll.delegate = PointerGestureDelegate.shared
        view.addGestureRecognizer(scroll)
    }

    @objc private func onHover(_ g: UIHoverGestureRecognizer) {
        guard let view = g.view else { return }
        // A hovering Apple Pencil is not a pointer.
        if #available(iOS 16.1, *), g.zOffset > 0 { return }
        let p = g.location(in: view)
        switch g.state {
        case .began:
            hoverLast = p; hoverHasLast = true
            HardwareInput.shared.pointerHovered(at: p, in: view, inside: true)
        case .changed:
            HardwareInput.shared.pointerHovered(at: p, in: view, inside: true)
            // No previous sample (re-entry, or a drag just ended): re-seed
            // instead of posting a jump.
            guard hoverHasLast else { hoverLast = p; hoverHasLast = true; return }
            let d = CGPoint(x: p.x - hoverLast.x, y: p.y - hoverLast.y)
            hoverLast = p
            HardwareInput.shared.uikitMoved(d.x, d.y, src: "hover")
        default:
            hoverHasLast = false
            HardwareInput.shared.pointerHovered(at: p, in: view, inside: false)
        }
    }

    @objc private func onDrag(_ g: UIPanGestureRecognizer) {
        guard let view = g.view else { return }
        // Translation, not location, for motion: it keeps accumulating past
        // the point where the clamped system pointer stops.
        let t = g.translation(in: view)
        switch g.state {
        case .began:
            panLast = .zero
            hoverHasLast = false
            HardwareInput.shared.pointerDragged(at: g.location(in: view), in: view)
        case .changed:
            HardwareInput.shared.pointerDragged(at: g.location(in: view), in: view)
            let d = CGPoint(x: t.x - panLast.x, y: t.y - panLast.y)
            panLast = t
            HardwareInput.shared.uikitMoved(d.x, d.y, src: "pan")
        default:
            panLast = .zero
            hoverHasLast = false
        }
    }

    @objc private func onScroll(_ g: UIPanGestureRecognizer) {
        let t = g.translation(in: g.view)
        switch g.state {
        case .changed:
            let d = CGPoint(x: t.x - scrollLast.x, y: t.y - scrollLast.y)
            scrollLast = t
            HardwareInput.shared.uikitScroll(d.x, d.y)
        default:
            scrollLast = .zero
        }
    }
}

/// The program's own cursor, drawn over the game view when it runs without a
/// virtual desktop (in the desktop session the compositor draws it). One
/// CALayer on MetalHostView, the window-level view that presents the game, so
/// it moves and scales with it; created the first time a mouse is used there.
/// The image and hotspot are the program's (driver_ios.c extracts them exactly
/// as for the desktop compositor); positions are Wine screen pixels on the
/// session's virtual monitor, which MetalHostView shows scaled to its bounds
/// (madeira-bcd, 2026-10-18: it assumed a 1024x768 monitor, so on any other
/// size the drawn cursor sat away from Wine's cursor). Main thread.
final class DirectCursorOverlay {
    static let shared = DirectCursorOverlay()

    private var layer: CALayer?
    private var image = winios_direct_cursor_state()
    private var serial: UInt32 = 0
    private var buffer = [UInt8](repeating: 0, count: Int(WINIOS_CURSOR_MAX) * Int(WINIOS_CURSOR_MAX) * 4)

    /// A new cursor image from the program (straight-alpha BGRA, as the
    /// compositor's winios_cursor_set takes it).
    func loadImage() {
        let s = buffer.withUnsafeMutableBytes { raw in
            winios_direct_cursor_copy_image(raw.baseAddress, raw.count, &image)
        }
        guard s != 0, s != serial else { return }
        serial = s
        let w = Int(image.w), h = Int(image.h)
        let info = CGBitmapInfo(rawValue: CGBitmapInfo.byteOrder32Little.rawValue
                                | CGImageAlphaInfo.first.rawValue)
        guard let provider = CGDataProvider(data: Data(buffer[0..<(w * h * 4)]) as CFData),
              let cg = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: w * 4,
                               space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: info, provider: provider,
                               decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        ensureLayer().contents = cg
        CATransaction.commit()
    }

    /// `guestW`/`guestH`: the virtual monitor's live size. x and y scale
    /// separately (Stretch), as the presented layer does.
    func render(visible: Bool, x: Int32, y: Int32, guestW: Int, guestH: Int) {
        guard visible || layer != nil else { return }
        let l = ensureLayer()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        l.isHidden = !visible
        if visible {
            let host = MetalHostView.shared.bounds
            let origin = MetalHostView.shared.superview != nil ? MetalHostView.shared.frame.origin : .zero
            let kx = host.width / CGFloat(max(guestW, 1)), ky = host.height / CGFloat(max(guestH, 1))
            let w = serial != 0 ? Int(image.w) : Self.arrowSize.w
            let h = serial != 0 ? Int(image.h) : Self.arrowSize.h
            let hx = serial != 0 ? Int(image.hot_x) : 0
            let hy = serial != 0 ? Int(image.hot_y) : 0
            l.bounds = CGRect(x: 0, y: 0, width: CGFloat(w) * kx, height: CGFloat(h) * ky)
            l.position = CGPoint(x: origin.x + CGFloat(Int(x) - hx) * kx, y: origin.y + CGFloat(Int(y) - hy) * ky)
        }
        CATransaction.commit()
    }

    private func ensureLayer() -> CALayer {
        let hostLayer = (MetalHostView.shared.superview ?? MetalHostView.shared).layer
        if let l = layer {
            if l.superlayer !== hostLayer { hostLayer.addSublayer(l) }
            return l
        }
        let l = CALayer()
        l.anchorPoint = .zero
        l.zPosition = 10000
        l.magnificationFilter = .nearest
        l.isHidden = true
        l.contents = Self.arrow()
        hostLayer.addSublayer(l)
        layer = l
        return l
    }

    /// Until the program's image arrives (or if it cannot be read): the same
    /// plain arrow the desktop compositor falls back to.
    private static let arrowSize = (w: 14, h: 21)
    private static func arrow() -> CGImage? {
        let r = UIGraphicsImageRenderer(size: CGSize(width: arrowSize.w, height: arrowSize.h))
        return r.image { _ in
            let p = UIBezierPath()
            p.move(to: CGPoint(x: 0.5, y: 0.5))
            p.addLine(to: CGPoint(x: 0.5, y: 15.5))
            p.addLine(to: CGPoint(x: 4.2, y: 12.2))
            p.addLine(to: CGPoint(x: 7.0, y: 19.0))
            p.addLine(to: CGPoint(x: 9.6, y: 17.8))
            p.addLine(to: CGPoint(x: 6.8, y: 11.1))
            p.addLine(to: CGPoint(x: 11.8, y: 10.7))
            p.close()
            UIColor.white.setFill()
            p.fill()
            UIColor.black.setStroke()
            p.lineWidth = 1.0
            p.stroke()
        }.cgImage
    }
}

/// "Move the mouse with the right stick" (InputSettings.padRightStickMouse, default
/// OFF): a paired controller's right stick moves the mouse at a velocity, for
/// programs with no controller support of their own. A program that reads the
/// controller itself (XInput or DirectInput) already gets the right stick, and
/// feeding it to the mouse as well turns the camera twice and drags the
/// program's own cursor, hence opt-in. GamepadInput.swift keeps publishing the
/// stick to XInput either way; this only reads it. Gain: the Relative pointer
/// slider (`sensRel`), as for the fork's on-screen aim stick. It moves the
/// program's cursor only while the app is active with the game view on screen
/// and nothing presented over it.
final class PadStickMouse: ObservableObject {
    static let shared = PadStickMouse()

    /// A controller with an extended profile is attached (drives the toggle's
    /// visibility).
    @Published private(set) var controllerConnected = false

    private var profile: GCExtendedGamepad?
    private var link: CADisplayLink?
    private var carry = MotionCarry()
    private var appActive = true
    private var wanted = false
    private var started = false
    private var toggleSink: AnyCancellable?

    /// Idempotent; main thread. Nothing with MADEIRA_HWINPUT=0.
    func start() {
        guard !started, HardwareInput.enabled else { return }
        started = true
        let nc = NotificationCenter.default
        for name in [Notification.Name.GCControllerDidConnect, .GCControllerDidDisconnect,
                     .GCControllerDidBecomeCurrent] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.refreshControllers()
            }
        }
        for name in [UIApplication.willResignActiveNotification, UIApplication.didEnterBackgroundNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.appActive = false
                self?.update()
            }
        }
        nc.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            self?.appActive = true
            self?.refreshControllers()
        }
        // @Published publishes in willSet: take the new value from the sink.
        toggleSink = InputSettings.shared.$padRightStickMouse.sink { [weak self] on in
            DispatchQueue.main.async {
                self?.wanted = on
                self?.update()
            }
        }
        wanted = InputSettings.shared.padRightStickMouse
        refreshControllers()
    }

    /// The live profile is captured on main (re-fetching it elsewhere yielded
    /// stale axes on devices tested in the fork, see GamepadInput).
    private func refreshControllers() {
        let pads = GCController.controllers().filter { $0.extendedGamepad != nil }
        let current = GCController.current.flatMap { c in pads.contains { $0 === c } ? c : nil }
        profile = (current ?? pads.first)?.extendedGamepad
        let connected = profile != nil
        if connected != controllerConnected {
            controllerConnected = connected
            HardwareInput.shared.controllersChanged()
        }
        update()
    }

    private func update() {
        let want = appActive && wanted && profile != nil
        if want, link == nil {
            carry.reset()
            let l = CADisplayLink(target: self, selector: #selector(tick(_:)))
            l.add(to: .main, forMode: .common)
            link = l
            fputs("[hwinput] right stick controls mouse: on\n", stderr)
        } else if !want, let l = link {
            l.invalidate()
            link = nil
            fputs("[hwinput] right stick controls mouse: off\n", stderr)
        }
    }

    @objc private func tick(_ l: CADisplayLink) {
        // Keyboard-and-mouse controller mode (PadKeyboardMouse) owns the stick:
        // moving the mouse here as well would double it.
        guard let p = profile, HardwareInput.shared.baseFocused, !GamepadInput.shared.keyboardMouseOn else { return }
        let f = StickVelocity.frame(x: Double(p.rightThumbstick.xAxis.value),
                                    y: Double(p.rightThumbstick.yAxis.value),
                                    gain: InputSettings.shared.sensRel,
                                    dt: l.targetTimestamp - l.timestamp)
        guard f.dx != 0 || f.dy != 0 else { return }
        let d = carry.add(f.dx, f.dy, gain: 1)
        guard d.dx != 0 || d.dy != 0 else { return }
        HardwareInput.shared.stickMoved()
        HardwareInput.shared.postRelative(d.dx, d.dy)
    }
}

