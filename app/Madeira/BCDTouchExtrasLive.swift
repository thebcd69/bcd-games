// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import UIKit

// What a finger or a Pencil on the game does beyond the pointer setting (round 604): aim mode (a finger turns the camera), touch
// gestures (taps and swipes with several fingers run an action), and the Apple Pencil as a precise mouse. MetalBackedView hands each
// batch of touches to `BCDTouchExtras.filter` first; it returns the touches the older code should still see (nil: none).
//
// Cost. Off, MetalBackedView's hooks read one static flag. On, a batch is looked at for the few things it can mean; the aim finger's
// counts go through the controller right-stick mouse's exit; nothing polls or animates. The Pencil's hover and squeeze recognisers are
// added to the game view when the feature turns on and taken off when it turns off.
// Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).

enum BCDTouchStage { case began, moved, ended, cancelled }

@MainActor enum BCDTouchExtras {
    /// Any of the three is on: MetalBackedView asks `filter`.
    nonisolated(unsafe) static var active = false
    /// The gestures that have an action of their own (the older code skips the same gesture then).
    nonisolated(unsafe) static var bound: Set<BCDGesture> = []

    static let aim = BCDAimLive()
    static let gestures = BCDGestureLive()
    static let pencil = BCDPencilLive()

    static func configure(aim: BCDAimSettings, gestures: BCDGestureSettings, pencil: BCDPencilSettings, title: String) {
        self.aim.configure(aim, title: title)
        self.gestures.configure(gestures, title: title)
        self.pencil.configure(pencil, title: title)
        bound = Set(gestures.bindings.keys)
        active = aim.isOn || gestures.isOn || pencil.isOn
    }

    /// Whether a gesture has an action of its own, so the older code does not do its own thing for it as well.
    nonisolated static func replaces(_ gesture: BCDGesture) -> Bool { bound.contains(gesture) }

    /// The touches the older handling still sees.
    static func filter(_ view: MetalBackedView, _ touches: Set<UITouch>, _ stage: BCDTouchStage) -> Set<UITouch>? {
        guard active else { return touches }
        var rest = touches
        if pencil.isOn { rest = pencil.handle(view, rest, stage) }
        if gestures.isOn { gestures.observe(view, rest, stage) }
        if aim.isOn { rest = aim.handle(view, rest, stage) }
        return rest.isEmpty ? nil : rest
    }
}

// MARK: - Aim mode

/// Night round 2026-11-11 (A5 S13): the right stick of player 1 while an aiming finger drives it (aim-as = stick). GamepadInput.sample reads it on its own
/// queue the way it reads BCDGyroLive's stick: one static flag while it is off.
final class BCDAimStick: @unchecked Sendable {
    static let shared = BCDAimStick()
    /// The aim drives the right stick of player 1 right now (set on the main thread, read on GamepadInput's queue).
    nonisolated(unsafe) static var active = false
    private let lock = NSLock()
    private var stick: (rx: Int16, ry: Int16) = (0, 0)

    func set(_ value: (rx: Int16, ry: Int16)) { lock.lock(); stick = value; lock.unlock() }

    /// Player 1's controller as the game is about to see it: the aim's stick goes on the right stick unless the player pushes the pad's own past its dead zone.
    func padSample(_ state: inout winios_gamepad) {
        lock.lock(); let aim = stick; lock.unlock()
        guard aim.rx != 0 || aim.ry != 0 else { return }
        let merged = BCDGyroMath.merged(physical: (state.rx, state.ry), gyro: aim)
        state.rx = merged.rx
        state.ry = merged.ry
    }
}

@MainActor final class BCDAimLive {
    private(set) var settings = BCDAimSettings()
    private var touch = BCDAimTouch()
    private var lastNote = 0.0
    private var logged = ""
    private var stickHeld = false
    private var decayPending = false
    private var lastStickMove = 0.0

    var isOn: Bool { settings.isOn }

    func configure(_ new: BCDAimSettings, title: String) {
        if new != settings { touch.reset(); stickRest() }
        settings = new
        let wasStick = BCDAimStick.active
        BCDAimStick.active = new.isOn && new.output == .stick
        if wasStick != BCDAimStick.active { GamepadInput.shared.aimStickChanged() }
        let note = new.isOn ? "\(title): aim mode \(new.zone.rawValue) sensitivity \(new.sensitivity) vertical \(new.vertical)%"
            + (new.invertY ? " inverted" : "") + (new.tapClicks ? " tap clicks" : "") + (new.output == .stick ? " output stick" : "") : ""
        if note != logged {
            logged = note
            if !note.isEmpty { LogStore.shared.log("[aim] " + note) }
        }
    }

    /// Fingers only (a Pencil is the pencil's). The aiming finger's touches are taken; the others go on.
    func handle(_ view: UIView, _ touches: Set<UITouch>, _ stage: BCDTouchStage) -> Set<UITouch> {
        var taken: [UITouch] = []
        for t in touches where t.type == .direct {
            let id = ObjectIdentifier(t).hashValue
            let point = t.location(in: view)
            switch stage {
            case .began:
                if touch.began(id: id, at: point, time: t.timestamp, bounds: view.bounds.size, settings) { taken.append(t) }
            case .moved:
                if settings.output == .stick {
                    // The finger's travel in this event is the push of the right stick (BCDAimMath.stickPush); the counts of the mouse path are not used.
                    if let travel = touch.movedPoints(id: id, to: point) {
                        stickPush(BCDAimMath.stickPush(dx: travel.dx, dy: travel.dy, settings), now: t.timestamp)
                        taken.append(t)
                    }
                } else if let counts = touch.moved(id: id, to: point, settings) {
                    post(counts, now: t.timestamp)
                    taken.append(t)
                }
            case .ended:
                if let tap = touch.ended(id: id, time: t.timestamp) {
                    if tap && settings.tapClicks { BCDInputActionRunner.run(.click(.left)) }
                    stickRest()
                    taken.append(t)
                }
            case .cancelled:
                if touch.cancelled(id: id) { stickRest(); taken.append(t) }
            }
        }
        return taken.isEmpty ? touches : touches.subtracting(taken)
    }

    /// Night round 2026-11-11 (A5 S13): the push goes to the right stick; one delayed check at a time (no timer of its own) lets it go back to rest
    /// once no move has come for BCDAimMath.stickHoldSeconds.
    private func stickPush(_ push: (x: Double, y: Double), now: Double) {
        BCDAimStick.shared.set((BCDGyroMath.axis(push.x), BCDGyroMath.axis(push.y)))
        stickHeld = true
        GamepadInput.shared.aimStickChanged()
        lastStickMove = ProcessInfo.processInfo.systemUptime
        scheduleStickDecay(after: BCDAimMath.stickHoldSeconds)
    }

    private func scheduleStickDecay(after delay: Double) {
        guard !decayPending else { return }
        decayPending = true
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.decayPending = false
                let age = ProcessInfo.processInfo.systemUptime - self.lastStickMove
                if age >= BCDAimMath.stickHoldSeconds { self.stickRest() } else { self.scheduleStickDecay(after: BCDAimMath.stickHoldSeconds - age + 0.005) }
            }
        }
    }

    /// The stick back to rest (the finger lifted, the touch was taken away, the settings changed, or no move came for the hold time).
    private func stickRest() {
        guard stickHeld else { return }
        stickHeld = false
        BCDAimStick.shared.set((0, 0))
        GamepadInput.shared.aimStickChanged()
    }

    /// Whole counts through the exit the controller's right-stick mouse uses, only while the game view has the pointer.
    private func post(_ counts: (dx: Int32, dy: Int32), now: Double) {
        guard counts.dx != 0 || counts.dy != 0, HardwareInput.shared.baseFocused else { return }
        HardwareInput.shared.postRelative(counts.dx, counts.dy)
        if now - lastNote > 0.1 {
            lastNote = now
            HardwareInput.shared.stickMoved()   // the drawn cursor follows the program's again
        }
    }
}

// MARK: - Touch gestures

@MainActor final class BCDGestureLive {
    private(set) var settings = BCDGestureSettings()
    private var tracker = BCDGestureTracker()
    private var logged = ""

    var isOn: Bool { settings.isOn }

    func configure(_ new: BCDGestureSettings, title: String) {
        if new != settings { tracker.cancel() }
        settings = new
        let note = new.isOn ? "\(title): \(new.bindings.count) touch gesture(s): "
            + new.bindings.sorted { $0.key.rawValue < $1.key.rawValue }.map { "\($0.key.rawValue)=\($0.value.id)" }.joined(separator: " ") : ""
        if note != logged {
            logged = note
            if !note.isEmpty { LogStore.shared.log("[gesture] " + note) }
        }
    }

    /// Watches the fingers; takes nothing from them.
    func observe(_ view: UIView, _ touches: Set<UITouch>, _ stage: BCDTouchStage) {
        for t in touches where t.type == .direct {
            let id = ObjectIdentifier(t).hashValue
            let point = t.location(in: view)
            switch stage {
            case .began: tracker.down(id: id, at: point, time: t.timestamp)
            case .moved: tracker.move(id: id, to: point)
            case .ended:
                if let gesture = tracker.up(id: id, at: point, time: t.timestamp), let action = settings.bindings[gesture] {
                    LogStore.shared.log("[gesture] \(gesture.rawValue) -> \(action.id)")
                    BCDInputActionRunner.run(action)
                }
            case .cancelled: tracker.cancel()
            }
        }
    }
}

// MARK: - The Apple Pencil

@MainActor final class BCDPencilLive: NSObject, UIPencilInteractionDelegate {
    private(set) var settings = BCDPencilSettings()
    private var pointer = BCDPencilPointer()
    private weak var installedOn: MetalBackedView?
    private var hover: UIHoverGestureRecognizer?
    private var interaction: UIPencilInteraction?
    private var logged = false

    var isOn: Bool { settings.isOn }

    func configure(_ new: BCDPencilSettings, title: String) {
        let was = settings.isOn
        settings = new
        if was && !new.isOn { post(pointer.reset()) }
        if new.isOn { install() } else { uninstall() }
        if new.isOn && !logged { logged = true; LogStore.shared.log("[pencil] \(title): the Apple Pencil is a mouse (squeeze: \(new.tap.title))") }
        if !new.isOn { logged = false }
    }

    /// A Pencil's touches are the mouse; fingers go on to the rest.
    func handle(_ view: MetalBackedView, _ touches: Set<UITouch>, _ stage: BCDTouchStage) -> Set<UITouch> {
        if installedOn == nil { install() }
        var rest = touches
        for t in touches where t.type == .pencil {
            rest.remove(t)
            let (x, y) = view.mapPoint(t.preciseLocation(in: view))
            switch stage {
            case .began: post(pointer.touchBegan(x: x, y: y))
            case .moved: post(pointer.touchMoved(x: x, y: y))
            case .ended: post(pointer.touchEnded(x: x, y: y))
            case .cancelled: post(pointer.touchCancelled())
            }
        }
        return rest
    }

    // MARK: Hover, squeeze and double tap

    private func install() {
        guard let view = MetalBackedView.keyboardTarget, installedOn !== view else { return }
        uninstall()
        let hover = UIHoverGestureRecognizer(target: self, action: #selector(hovered(_:)))
        hover.cancelsTouchesInView = false
        view.addGestureRecognizer(hover)
        let interaction = UIPencilInteraction()
        interaction.delegate = self
        view.addInteraction(interaction)
        self.hover = hover
        self.interaction = interaction
        installedOn = view
    }

    private func uninstall() {
        if let view = installedOn {
            if let hover { view.removeGestureRecognizer(hover) }
            if let interaction { view.removeInteraction(interaction) }
        }
        hover = nil
        interaction = nil
        installedOn = nil
    }

    /// The Pencil hovering reports how far it is above the glass; a mouse pointer reports none (the older code has that).
    @objc private func hovered(_ recognizer: UIHoverGestureRecognizer) {
        guard settings.isOn, let view = installedOn, recognizer.state == .began || recognizer.state == .changed else { return }
        guard #available(iOS 16.1, *), recognizer.zOffset > 0 else { return }
        let (x, y) = view.mapPoint(recognizer.location(in: view))
        post(pointer.hovered(x: x, y: y))
    }

    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveTap tap: UIPencilInteraction.Tap) {
        tapped(at: tap.hoverPose?.location)
    }

    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveSqueeze squeeze: UIPencilInteraction.Squeeze) {
        guard squeeze.phase == .ended else { return }
        tapped(at: squeeze.hoverPose?.location)
    }

    private func tapped(at location: CGPoint?) {
        guard settings.isOn else { return }
        var point: (x: Int32, y: Int32)?
        if let location, let view = installedOn { let p = view.mapPoint(location); point = (p.0, p.1) }
        post(pointer.tapped(settings.tap, at: point))
    }

    // MARK: Posting

    private static let move: UInt32 = 0x1, leftDown: UInt32 = 0x2, leftUp: UInt32 = 0x4, rightDown: UInt32 = 0x8, rightUp: UInt32 = 0x10
    private static let middleDown: UInt32 = 0x20, middleUp: UInt32 = 0x40, absolute: UInt32 = 0x8000

    private func post(_ ops: [BCDPencilPointer.Op]) {
        for op in ops {
            switch op {
            case .move(let x, let y):
                winios_pointer(x, y, Self.move | Self.absolute, 0)
                winios_cursor_move(x, y)
            case .leftDown(let x, let y): winios_pointer(x, y, Self.leftDown | Self.absolute, 0)
            case .leftUp(let x, let y): winios_pointer(x, y, Self.leftUp | Self.absolute, 0)
            case .click(let button, let x, let y):
                let down = button == .right ? Self.rightDown : Self.middleDown
                let up = button == .right ? Self.rightUp : Self.middleUp
                winios_pointer(x, y, Self.move | Self.absolute, 0)
                winios_pointer(x, y, down | Self.absolute, 0)
                DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(BCDExtraKeys.tapHold)) { winios_pointer(x, y, up | Self.absolute, 0) }
            }
        }
    }
}
