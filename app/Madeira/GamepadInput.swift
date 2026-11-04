import Foundation
// GameController supports background handler queues but lacks Sendable annotations.
@preconcurrency import GameController
import UIKit
import SwiftUI

/// ml1930: physical and touch controller snapshots share the same serial publisher.
/// Slot/profile/timer state belongs exclusively to `queue`. The app lifecycle
/// and observer registration belong to the main actor. Guest readers use the
/// C snapshot lock; no Swift objects cross into Wine.
final class GamepadInput: @unchecked Sendable {
    static let shared = GamepadInput()
    @MainActor static let enabled: Bool = {
        let value = MadeiraConfig.get("env.MADEIRA_XINPUT")
            ?? ProcessInfo.processInfo.environment["MADEIRA_XINPUT"]
        return value != "0"
    }()

    @MainActor static let touchEnabled: Bool = {
        let value = MadeiraConfig.get("env.MADEIRA_TOUCH_XINPUT")
            ?? ProcessInfo.processInfo.environment["MADEIRA_TOUCH_XINPUT"]
        return enabled && value != "0"
    }()

    @MainActor func configureTouch(controls: Set<UUID>, acceptingInput: Bool = true) {
        let allowed = Self.touchEnabled ? controls : []
        queue.async { [self] in
            let wasConnected = touchState.connected, wasAccepting = touchState.acceptingInput
            touchState.configure(allowed, acceptingInput: acceptingInput)
            if wasConnected != touchState.connected || wasAccepting != touchState.acceptingInput {
                LogStore.shared.log("[touch-xinput] connected=\(touchState.connected ? 1 : 0) accepting=\(acceptingInput ? 1 : 0) controls=\(allowed.count)")
            }
            sample()
        }
    }

    /// Publish player 1 before the game looks (MADEIRA_PAD_EARLY_SLOT=1; default OFF).
    ///
    /// Some input layers enumerate XInput once at startup and only rescan on a
    /// device-arrival broadcast, which this port does not deliver. Touch slot 0
    /// connects only once the landscape overlay shows its controller mappings,
    /// and a paired controller may not have reported an extended profile yet,
    /// so such a game never sees a pad. When the session will have a controller
    /// source (touch controller mappings shown, or a controller paired), slot 0
    /// is connected at rest from the start; live input takes it over. The
    /// reservation lasts until the process exits (one Wine session per run),
    /// so player 1 then shows as connected for the whole session even while no
    /// controller is in use. That is why it is opt-in: without the switch,
    /// slot 0 connects only when a real source appears, as before.
    @MainActor func reserveSessionSlot(touchControls: Bool) {
        // Round 630 (agent T1 S1; Dokimon Quest N9PJHQ, DQKXK3: the game loads xinput1_3 and looks for pads once at start, the touch pad
        // connected 0-5 s later): the switch is also read from the game's own file, so a list row can set it for one game, as
        // MADEIRA_PAD_MODE is (configuredPadMode). optIn itself stays bcd.cfg and the environment for its other callers.
        let gameSet = MadeiraConfig.gameValue("env.MADEIRA_PAD_EARLY_SLOT") == "1"
        guard Self.enabled, gameSet || Self.optIn("MADEIRA_PAD_EARLY_SLOT") else { return }
        let touch = touchControls && Self.touchEnabled
        let paired = !GCController.controllers().isEmpty
        guard touch || paired else {
            LogStore.shared.log("[xinput] ml1990 early-slot switch set (\(gameSet ? "game" : "cfg")), slot=0 not reserved: no touch controller "
                                + "mappings shown and no paired controller at the session start")
            return
        }
        queue.async { [self] in touchState.reserved = true; sample() }
        LogStore.shared.log("[xinput] ml1990 slot=0 reserved for the session touch=\(touch ? 1 : 0) paired=\(paired ? 1 : 0)")
    }

    /// ml2100: how player 1 reaches Windows, `env.MADEIRA_PAD_MODE` from the
    /// game's own file (GameProfiles.swift), else bcd.cfg, else the process
    /// environment. Unset or "xinput": the XInput path above, unchanged (the
    /// default). "hid": player 1 becomes a HID game controller served by the
    /// wineserver (build/wineserver/hidpad_ios.c) -- a DualSense when the pad
    /// is a PlayStation one, a generic HID gamepad otherwise -- and leaves
    /// XInput. "dualsense" / "generic" force that identity. Read once, at
    /// session start (beginPadSession): the device exists before the game
    /// enumerates, so a change applies at the game's next start.
    static let padModeKey = "env.MADEIRA_PAD_MODE"
    static func configuredPadMode() -> (value: String, source: String) {
        if let v = MadeiraConfig.gameValue(padModeKey) { return (v.lowercased(), "game") }
        if let v = MadeiraConfig.get(padModeKey), !v.isEmpty { return (v.lowercased(), "cfg") }
        if let v = ProcessInfo.processInfo.environment["MADEIRA_PAD_MODE"], !v.isEmpty { return (v.lowercased(), "env") }
        return ("xinput", "default")
    }

    /// ml2106: what of a game's controller output reaches the physical pad
    /// (app/Madeira/PadOutput.m, docs/CONTROLLERS.md): XInputSetState rumble on
    /// any XInput pad, and the virtual DualSense's output reports (rumble,
    /// adaptive triggers, lightbar, player LEDs) on player 1's pad.
    /// `env.MADEIRA_PAD_OUTPUT`, the game's file first: unset or 1 both, 0
    /// none, hid only the DualSense's, xinput only XInput rumble.
    static let padOutputKey = "env.MADEIRA_PAD_OUTPUT"
    static func configuredPadOutput() -> (xinput: Bool, hid: Bool, value: String) {
        let value = (MadeiraConfig.gameValue(padOutputKey)
            ?? MadeiraConfig.get("env.MADEIRA_PAD_OUTPUT")
            ?? ProcessInfo.processInfo.environment["MADEIRA_PAD_OUTPUT"]
            ?? "1").lowercased()
        switch value {
        case "0", "off", "none": return (false, false, value)
        case "hid", "dualsense": return (false, true, value)
        case "xinput": return (true, false, value)
        default: return (true, true, value)
        }
    }

    /// The identity this session's HID controller was created with (nil: XInput mode).
    @MainActor private(set) static var sessionHIDKind: String?

    /// ml2100: decide this session's controller path. Called once, before the
    /// wineserver starts (ContentView.runWineFullSequence): the wineserver and
    /// the first Wine process read MADEIRA_HIDPAD to create the device and its
    /// registry entries. In XInput mode it only unsets that and logs.
    @MainActor func beginPadSession() {
        let mode = Self.configuredPadMode()
        let output = Self.configuredPadOutput()
        SystemGestures.beginSession(enabled: Self.enabled)   // madeira-bcd: env.MADEIRA_PAD_SYSTEM_GESTURES (opt-in)
        unsetenv("MADEIRA_HIDPAD"); unsetenv("MADEIRA_HIDPAD_NAME")
        // An earlier session in this app run may have taken player 1 off XInput.
        queue.async { [self] in hidActive = false; hidKeepsXInput = false }
        Self.sessionHIDKind = nil
        guard ["hid", "dualsense", "generic"].contains(mode.value) else {
            LogStore.shared.log("[hid-pad] ml2100 session mode=xinput source=\(mode.source)")
            beginPadOutput(xinput: output.xinput && Self.enabled, hid: false, value: output.value)
            return
        }
        guard Self.enabled else {
            LogStore.shared.log("[hid-pad] ml2100 session mode=\(mode.value) ignored: MADEIRA_XINPUT=0 turns every controller off")
            beginPadOutput(xinput: false, hid: false, value: output.value)
            return
        }
        // Player 1 is the first paired extended gamepad, as refreshControllers assigns slots.
        let first = GCController.controllers().first { $0.extendedGamepad != nil }
        let sony = first?.extendedGamepad is GCDualSenseGamepad || first?.extendedGamepad is GCDualShockGamepad
        let kind: String
        switch mode.value {
        case "dualsense", "generic": kind = mode.value
        // No pad yet: a DualSense, the identity this mode exists for.
        default: kind = first == nil || sony ? "dualsense" : "generic"
        }
        setenv("MADEIRA_HIDPAD", kind, 1)
        if kind == "generic", let name = first?.vendorName { setenv("MADEIRA_HIDPAD_NAME", name, 1) }
        Self.sessionHIDKind = kind
        let keepXInput = Self.optIn("MADEIRA_HIDPAD_XINPUT")
        queue.async { [self] in hidActive = true; hidKeepsXInput = keepXInput; sample() }
        LogStore.shared.log("[hid-pad] ml2100 session mode=\(mode.value) source=\(mode.source) kind=\(kind) "
                            + "pad=\(first?.productCategory ?? "none") xinput-slot0=\(keepXInput ? "kept" : "off")")
        // Only the DualSense identity has output reports (the generic pad has none).
        beginPadOutput(xinput: output.xinput, hid: output.hid && kind == "dualsense", value: output.value)
    }

    /// ml2106: arm PadOutput.m for this session. Before the wineserver starts,
    /// so XInputGetCapabilities already reports motors when rumble is on.
    @MainActor private func beginPadOutput(xinput: Bool, hid: Bool, value: String) {
        madeira_pad_output_configure(xinput ? 1 : 0, hid ? 1 : 0)
        LogStore.shared.log("[hidpad-out] ml2106 session env.MADEIRA_PAD_OUTPUT=\(value) "
                            + "xinput-rumble=\(xinput ? 1 : 0) dualsense-output=\(hid ? 1 : 0)")
    }

    /// Documents/bcd.cfg `env.NAME`, else the process environment; only "0" disables.
    static func flag(_ name: String) -> Bool {
        (MadeiraConfig.get("env.\(name)") ?? ProcessInfo.processInfo.environment[name]) != "0"
    }

    /// The same sources, for behaviour that is off by default: only "1" enables.
    static func optIn(_ name: String) -> Bool {
        (MadeiraConfig.get("env.\(name)") ?? ProcessInfo.processInfo.environment[name]) == "1"
    }

    /// madeira-bcd (2026-10-25, round 603): the gyro's right stick changed (BCDGyroLive.swift); player 1 is published again.
    func gyroChanged() { queue.async { [self] in sample() } }

    /// madeira-bcd (2026-10-25, round 604): turbo, macros or a press BCD makes itself changed (BCDTurboLive.swift); player 1 is published again.
    func inputExtrasChanged() { queue.async { [self] in sample() } }

    @MainActor func touch(owner: UUID, control: UUID, value: GamepadSample?) {
        guard Self.touchEnabled else { return }
        queue.async { [self] in
            guard active || value == nil else { return }
            touchState.update(owner: owner, control: control, value: value)
            sample()
        }
    }

    /// Keyboard-and-mouse mode (PadKeyboardMouse): nil keeps XInput. Set on the
    /// main actor by the library for its session; read on `queue`.
    private var keyboardMouse: PadBindings?

    /// Availability of the mode. 0 removes the per-game choice; the physical pad then always feeds XInput.
    static let keyboardMouseAvailable: Bool = flag("MADEIRA_XINPUT") && flag("MADEIRA_PAD_KBM")

    /// Whether keyboard-and-mouse mode is on. Main thread: PadStickMouse ("Right
    /// stick controls mouse") stands down while it is, since this mode moves the
    /// mouse (or drives keys) with the same stick.
    private(set) var keyboardMouseOn = false

    /// Translate player 1's physical pad into keys and mouse (`bindings`) instead
    /// of publishing it to XInput; nil restores XInput. Touch controls keep
    /// feeding XInput either way. Main thread.
    func setKeyboardMouse(_ bindings: PadBindings?) {
        guard Self.keyboardMouseAvailable else { return }
        keyboardMouseOn = bindings != nil
        queue.async { [self] in
            let was = keyboardMouse != nil
            if keyboardMouse != nil && bindings == nil { PadKeyboardMouse.shared.releaseAll("mode off") }
            keyboardMouse = bindings
            if was != (bindings != nil) {
                LogStore.shared.log("[pad-kbm] physical controller as keyboard and mouse: \(bindings != nil ? "on" : "off")")
            }
            sample()
        }
    }

    private let queue = DispatchQueue(label: "madeira.gamepad", qos: .userInteractive)
    var controllers = [GCController?](repeating: nil, count: 4)   // madeira-bcd (round 604): not private, BCDPlayersLive.swift changes players' places
    var profiles = [GCExtendedGamepad?](repeating: nil, count: 4)
    private var timer: DispatchSourceTimer?
    private var active = false
    /// ml2100: player 1 goes to the HID controller (beginPadSession); queue-owned.
    private var hidActive = false
    /// MADEIRA_HIDPAD_XINPUT=1: player 1 stays an XInput pad as well.
    private var hidKeepsXInput = false
    private var hidBattery: (level: UInt8, charging: UInt8) = (UInt8(WINIOS_HIDPAD_BATTERY_UNKNOWN), 0)
    private var hidBatteryCountdown = 0
    private var touchState = TouchGamepadState()
    @MainActor private var observers: [NSObjectProtocol] = []
    @MainActor private var started = false

    @MainActor func start() {
        guard !started else { return }
        started = true
        LogStore.shared.log("[xinput] ml1920 physical controllers enabled=\(Self.enabled ? 1 : 0)")
        LogStore.shared.log("[touch-xinput] ml1930 enabled=\(Self.touchEnabled ? 1 : 0)")
        guard Self.enabled else { return }
        let center = NotificationCenter.default
        for name in [Notification.Name.GCControllerDidConnect, .GCControllerDidDisconnect] {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.refreshControllers() }
            })
        }
        for name in [UIApplication.willResignActiveNotification, UIApplication.didEnterBackgroundNotification] {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.setActive(false)
            })
        }
        observers.append(center.addObserver(forName: UIApplication.didBecomeActiveNotification,
                                            object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.refreshControllers() }
            self?.setActive(true)
        })
        refreshControllers()
        setActive(UIApplication.shared.applicationState == .active)
    }

    @MainActor private func refreshControllers() {
        // Capture the live profile on main. Re-fetching extendedGamepad on the
        // polling queue yielded stale axes on devices tested in the fork.
        let live = GCController.controllers().compactMap { controller -> (GCController, GCExtendedGamepad)? in
            guard let profile = controller.extendedGamepad else { return nil }
            controller.handlerQueue = queue
            return (controller, profile)
        }
        SystemGestures.apply(live.map { $0.0 })   // madeira-bcd: env.MADEIRA_PAD_SYSTEM_GESTURES; without it nothing is touched
        let playerKeys = BCDPlayersLive.keys(for: live.map { $0.0 })   // madeira-bcd (round 604): the key each controller is remembered by for its player
        queue.async { [self] in
            for i in controllers.indices {
                guard let old = controllers[i], !live.contains(where: { $0.0 === old }) else { continue }
                profiles[i]?.valueChangedHandler = nil
                controllers[i] = nil
                profiles[i] = nil
                // ml2106: a game's "motors off" for a pad that left is dropped (the
                // slot reads disconnected), so the slot's motors end here.
                winios_gamepad_set_vibration(Int32(i), 0, 0)
                fputs("[xinput] ml1920 slot=\(i) disconnected\n", stderr)
            }
            for (controller, profile) in live {
                // madeira-bcd (round 604): the slot the player chose for this controller when it is free, else the first free one as always.
                guard !controllers.contains(where: { $0 === controller }),
                      let i = BCDPlayersLive.slot(forKey: playerKeys[ObjectIdentifier(controller)], occupied: controllers.map { $0 != nil }) else { continue }
                controllers[i] = controller
                profiles[i] = profile
                // A pad joining a slot starts still, before sample() publishes it,
                // rather than replaying the level the slot had before.
                winios_gamepad_set_vibration(Int32(i), 0, 0)
                profile.valueChangedHandler = { [weak self] _, _ in
                    // Explicit queue hop also serializes callbacks already in flight
                    // when a controller is disconnected or the app resigns active.
                    self?.queue.async { [weak self] in self?.sample() }
                }
                fputs("[xinput] ml1920 slot=\(i) connected\n", stderr)
            }
            // ml2106: the pad in each slot is where that slot's rumble plays
            // (and slot 0's the DualSense output); unchanged slots are no-ops.
            for i in controllers.indices { madeira_pad_output_set_slot(Int32(i), controllers[i]) }
            updateTimer()
            sample()
            BCDPlayersLive.report(controllers)   // madeira-bcd (round 604): the players' card shows who is in which slot
        }
    }

    private func setActive(_ value: Bool) {
        // ml2106: motors stop and adaptive triggers go off while the app is
        // inactive; the game's last state comes back with it.
        madeira_pad_output_set_active(value ? 1 : 0)
        queue.async { [self] in
            active = value
            if !value {
                touchState.clear()
                if keyboardMouse != nil { PadKeyboardMouse.shared.releaseAll("inactive") }
            }
            updateTimer()
            sample()
        }
    }

    func updateTimer() {
        let needed = active && profiles.contains(where: { $0 != nil })
        if !needed { timer?.cancel(); timer = nil; return }
        guard timer == nil else { return }
        let source = DispatchSource.makeTimerSource(queue: queue)
        source.schedule(deadline: .now(), repeating: .milliseconds(4), leeway: .milliseconds(1))
        source.setEventHandler { [weak self] in self?.sample() }
        timer = source
        source.resume()
    }

    // XInput leaves dead zones to the game. Preserve the complete signed range.
    static func axis(_ value: Float) -> Int16 {
        guard value.isFinite else { return 0 }
        let clamped = max(-1, min(1, value))
        return Int16((clamped * (clamped < 0 ? 32768 : 32767)).rounded())
    }
    static func trigger(_ value: Float) -> UInt8 {
        guard value.isFinite else { return 0 }
        return UInt8((max(0, min(1, value)) * 255).rounded())
    }

    private func sample() {
        // madeira-bcd (round 604): "Player N: press a button on the controller" (BCDPlayersLive.swift); one static flag, -1, while nobody is choosing.
        if BCDPlayersLive.listening >= 0, let hit = BCDPlayersLive.pressed(in: profiles) { assignListened(hit) }
        for i in profiles.indices {
            let pad = profiles[i]
            let touchConnected = i == BCDPlayersLive.touchSlot && touchState.connected   // madeira-bcd (round 604): the touch controls play as the player chosen, Player 1 unless changed
            let gyroConnected = i == 0 && BCDGyroLive.stickActive   // madeira-bcd (2026-10-25, round 603): gyro aim on the right stick (opt-in per game)
            // Player 1's pad went away mid-press: nothing feeds the driver now, so
            // release the keys and buttons it holds.
            if i == 0, pad == nil, keyboardMouse != nil, PadKeyboardMouse.shared.holding {
                PadKeyboardMouse.shared.releaseAll("controller disconnected")
            }
            let hid = i == 0 && hidActive
            guard pad != nil || touchConnected || gyroConnected else {
                winios_gamepad_set_state(Int32(i), nil)
                if hid { winios_hidpad_set_state(nil) }
                continue
            }
            var state = winios_gamepad()
            state.connected = 1
            // ml2100: the pad whose HID-only inputs (touchpad click) this sample
            // carries; nil while the app is inactive or the library owns input.
            var hidLive: GCExtendedGamepad?
            // Keep the connected identity, but release all controls while the
            // app is inactive. A delayed callback cannot republish a held key.
            if active, let pad {
                hidLive = hid ? pad : nil
                let buttons: [(GCControllerButtonInput?, UInt16)] = [
                    (pad.dpad.up, 0x0001), (pad.dpad.down, 0x0002),
                    (pad.dpad.left, 0x0004), (pad.dpad.right, 0x0008),
                    (pad.buttonMenu, 0x0010), (pad.buttonOptions, 0x0020),
                    (pad.leftThumbstickButton, 0x0040), (pad.rightThumbstickButton, 0x0080),
                    (pad.leftShoulder, 0x0100), (pad.rightShoulder, 0x0200),
                    (pad.buttonHome, 0x0400), (pad.buttonA, 0x1000),
                    (pad.buttonB, 0x2000), (pad.buttonX, 0x4000), (pad.buttonY, 0x8000)
                ]
                for (button, mask) in buttons where button?.isPressed == true { state.buttons |= mask }
                state.left_trigger = Self.trigger(pad.leftTrigger.value)
                state.right_trigger = Self.trigger(pad.rightTrigger.value)
                state.lx = Self.axis(pad.leftThumbstick.xAxis.value)
                state.ly = Self.axis(pad.leftThumbstick.yAxis.value)
                state.rx = Self.axis(pad.rightThumbstick.xAxis.value)
                state.ry = Self.axis(pad.rightThumbstick.yAxis.value)
                // The library front end navigates with player 1's pad (Library.swift).
                // While it owns input (no session, or its menu is open) the game
                // sees a connected pad at rest.
                if i == 0 {
                    let library = LibraryController.shared
                    library.sample(buttons: state.buttons, lx: state.lx, ly: state.ly)
                    if library.ownsInput {
                        state = winios_gamepad()
                        state.connected = 1
                        hidLive = nil
                        if keyboardMouse != nil { PadKeyboardMouse.shared.releaseAll("library menu") }
                    } else if let kbm = keyboardMouse {
                        // Keyboard-and-mouse mode: the pad becomes keys and mouse
                        // motion; XInput sees no physical player 1 (touch may still
                        // connect it below). The HID controller, when on, likewise
                        // stays but gets none of the physical pad's input.
                        PadKeyboardMouse.shared.feed(buttons: state.buttons, lt: state.left_trigger, rt: state.right_trigger,
                                                     lx: state.lx, ly: state.ly, rx: state.rx, ry: state.ry,
                                                     bindings: kbm, focused: HardwareInput.shared.baseFocused)
                        hidLive = nil
                        guard touchConnected else {
                            winios_gamepad_set_state(Int32(i), nil)
                            if hid { winios_hidpad_set_state(nil) }
                            continue
                        }
                        state = winios_gamepad()
                        state.connected = 1
                    }
                }
            }
            if active && touchConnected {
                let physical = GamepadSample(buttons: state.buttons,
                    lt: state.left_trigger, rt: state.right_trigger,
                    lx: state.lx, ly: state.ly, rx: state.rx, ry: state.ry)
                let merged = GamepadSample.merge(physical: physical, touch: touchState.sample)
                state.buttons = merged.buttons
                state.left_trigger = merged.lt; state.right_trigger = merged.rt
                state.lx = merged.lx; state.ly = merged.ly; state.rx = merged.rx; state.ry = merged.ry
            }
            // madeira-bcd (2026-10-25, round 603): gyro aim (BCDGyroLive.swift): notes the button the gyro waits for and puts the gyro on the right
            // stick. One static flag when it is off; not while the library's menu owns the controller or the pad is keys and mouse.
            if i == 0, BCDGyroLive.padNeeded, active, keyboardMouse == nil, !LibraryController.shared.ownsInput {
                BCDGyroLive.shared.padSample(&state)
            }
            // madeira-bcd (2026-10-25, round 604): turbo and macros (BCDTurboLive.swift) change what player 1's merged state presses. One
            // static flag when they are off; not while the library's menu owns the controller or the pad is keys and mouse.
            if i == 0, BCDTurboLive.padNeeded, active, keyboardMouse == nil, !LibraryController.shared.ownsInput {
                BCDTurboLive.shared.padSample(&state)
            }
            if hid {
                publishHID(state, live: hidLive, pad: pad)
                // One controller, one API: player 1 is not also an XInput pad,
                // as a DualSense on Windows is not (MADEIRA_HIDPAD_XINPUT=1 keeps both).
                if !hidKeepsXInput {
                    winios_gamepad_set_state(Int32(i), nil)
                    continue
                }
            }
            winios_gamepad_set_state(Int32(i), &state)
        }
    }

    /// ml2100: player 1 as the HID controller reports it: the XInput sample above
    /// (touch merge and library ownership included) plus what only a HID report
    /// carries. L2/R2's digital bits follow the merged analogue value, so touch
    /// triggers set them too. The battery is read once a second.
    private func publishHID(_ state: winios_gamepad, live: GCExtendedGamepad?, pad: GCExtendedGamepad?) {
        var hid = winios_hidpad()
        hid.connected = 1
        hid.buttons = UInt32(state.buttons)
        hid.lx = state.lx; hid.ly = state.ly; hid.rx = state.rx; hid.ry = state.ry
        hid.left_trigger = state.left_trigger; hid.right_trigger = state.right_trigger
        if state.left_trigger > 25 { hid.buttons |= WINIOS_HIDPAD_L2 }
        if state.right_trigger > 25 { hid.buttons |= WINIOS_HIDPAD_R2 }
        if let live {
            let touchpad = (live as? GCDualSenseGamepad)?.touchpadButton ?? (live as? GCDualShockGamepad)?.touchpadButton
            if touchpad?.isPressed == true { hid.buttons |= WINIOS_HIDPAD_TOUCHPAD }
        }
        if hidBatteryCountdown <= 0 {
            hidBatteryCountdown = 250
            if let battery = pad?.controller?.battery {
                let fraction = battery.batteryLevel.isFinite ? battery.batteryLevel : 0
                let level = UInt8(max(0, min(100, (fraction * 100).rounded())))
                let charging: UInt8 = battery.batteryState == .charging ? 1 : battery.batteryState == .full ? 2 : 0
                hidBattery = battery.batteryState == .unknown ? (UInt8(WINIOS_HIDPAD_BATTERY_UNKNOWN), 0) : (level, charging)
            } else {
                hidBattery = (UInt8(WINIOS_HIDPAD_BATTERY_UNKNOWN), 0)
            }
        }
        hidBatteryCountdown -= 1
        hid.battery = hidBattery.level
        hid.charging = hidBattery.charging
        winios_hidpad_set_state(&hid)
    }
}

/// iOS 18 otherwise routes stick input into UIKit/SwiftUI focus navigation.
struct ClaimGamepadEvents: ViewModifier {
    func body(content: Content) -> some View {
        if #available(iOS 18.0, *), GamepadInput.enabled {
            content.handlesGameControllerEvents(matching: .gamepad)
        } else { content }
    }
}

enum GamepadEventClaim {
    @MainActor static func install(on view: UIView) {
        guard GamepadInput.enabled else { return }
        if #available(iOS 18.0, *) {
            guard !view.interactions.contains(where: { $0 is GCEventInteraction }) else { return }
            let interaction = GCEventInteraction()
            interaction.handledEventTypes = .gamepad
            view.addInteraction(interaction)
        }
    }
}
