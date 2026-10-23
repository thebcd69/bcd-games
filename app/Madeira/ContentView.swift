import SwiftUI
import UIKit
import QuartzCore
import Metal
import os.log

// 2026-07-03 window-hosted Metal layer.
//
// The presenting CAMetalLayer must NOT be a SwiftUI-hosted view's backing
// layer: on iOS 26/27, SwiftUI's hosting intermittently routes such layers
// through an indirect/snapshot path where direct Metal presentations are
// silently dropped — presented drawables complete with presentedTime==0
// (measured), the screen freezes on stale content, and only full-tree
// re-renders (screenshots) reveal new frames. Which path a given run gets
// appeared random — the "sometimes rendering starts at present #9,
// sometimes never" lottery.
//
// So the layer now lives in MetalHostView, a raw UIView added directly to
// the UIWindow (classic game setup, no SwiftUI management). The SwiftUI-
// hosted MetalBackedView remains as a transparent layout placeholder that
// tracks geometry and handles touch input. The host view sits on top of
// the window but has interaction disabled, so touches fall through to the
// SwiftUI hierarchy (and thus to the placeholder's touch handlers).

/// Raw window-level host for the presenting CAMetalLayer.
final class MetalHostView: UIView {
    // Process-lifetime singleton. The CAMetalLayer is registered with DXMT's
    // swapchain exactly once; if the host were recreated on view teardown
    // (rotation, re-attach) DXMT would keep presenting to the DEAD layer —
    // black surface both ways (2026-07-05 landscape regression). One host,
    // one layer, forever; only its FRAME is re-parented/resized.
    static let shared = MetalHostView(frame: CGRect(x: 0, y: 0, width: 800, height: 600))

    override class var layerClass: AnyClass { return CAMetalLayer.self }
    var metalLayer: CAMetalLayer { return layer as! CAMetalLayer }
    override init(frame: CGRect) {
        super.init(frame: frame)
        GamepadEventClaim.install(on: self)
        isUserInteractionEnabled = false   // touches fall through to SwiftUI
        backgroundColor = .black
        contentScaleFactor = UIScreen.main.scale
        metalLayer.device = MTLCreateSystemDefaultDevice()
        metalLayer.pixelFormat = .bgra8Unorm
        metalLayer.framebufferOnly = true
        // 2026-07-03 MeloNX trick: displaySyncEnabled is macOS-public but
        // exists as PRIVATE API on iOS. Disabling it takes our presents out
        // of the display-sync scheduling machinery — the thing that has been
        // silently dropping them (presentedTime==0 on all but occasional
        // frames) at our sub-1Hz game present cadence. MeloNX (shipping
        // Switch emulator) sets exactly this pair on its layer.
        let syncSel = NSSelectorFromString("setDisplaySyncEnabled:")
        if metalLayer.responds(to: syncSel) {
            metalLayer.perform(syncSel, with: NSNumber(value: false))
            LogStore.shared.log("MetalLayer: displaySyncEnabled=false (private API, MeloNX pattern)")
        }
        /* ml651: was hardcoded 60, which contradicted everything around it —
         * ProMotionIntent asks the display link for CAFrameRateRange(preferred: 120)
         * while this declared the surface a 60Hz one. Track the screen instead.
         *
         * ⚠️ HYPOTHESIS, NOT A DIAGNOSIS. displaySyncEnabled=false directly above
         * takes our presents out of display-sync scheduling, so this nominal
         * value may well be inert. It is one line and it removes a genuine
         * contradiction; if the A/B shows nothing, the cap is elsewhere and we
         * have eliminated it rather than argued about it. */
        let fpsSel = NSSelectorFromString("setNominalFramesPerSecond:")
        if metalLayer.responds(to: fpsSel) {
            let hz = UIScreen.main.maximumFramesPerSecond
            metalLayer.perform(fpsSel, with: hz as NSNumber)
            LogStore.shared.log("MetalLayer: ml651 nominalFPS=\(hz) (was hardcoded 60; "
                                + "display link asks preferred=120)")
        }
        UIApplication.shared.isIdleTimerDisabled = true
        // Set once so DXMT's swapchain setup never blocks on a zero-sized
        // layer. After this, DXMT's setProps is the ONLY drawableSize
        // writer — per-layout rewrites from the app were a second writer
        // fighting it (pool churn on every SwiftUI layout pass).
        metalLayer.drawableSize = CGSize(width: 800, height: 600)
    }
    required init?(coder: NSCoder) { fatalError() }
}

/// Touches on the game view outside the on-screen controls reach the program as
/// a mouse. Some games (Dark Souls Remastered) switch to keyboard and mouse
/// prompts on any mouse event and then ignore the controller, so a stray tap
/// next to a touch button costs the player the controller. MADEIRA_TOUCH_MOUSE
/// (bcd.cfg `env.NAME`, else the process environment): "1" always sends
/// them (the previous behaviour), "0" never does, and by default they are not
/// sent while the landscape overlay shows at least one controller mapping,
/// unless touch is set to work as a trackpad. A hardware mouse or trackpad is
/// not affected.
///
/// madeira-bcd: the game's own file (Game details › This game's config) is
/// read first, as for the pad mode, so a per-game line applies. And by
/// default a desktop (Dock) session keeps touches as the mouse: there the
/// pointer is how windows are used. Build 433, Grand Theft Auto V through the
/// Rockstar Games Launcher: with the Xbox layout shown every tap on the game
/// was dropped, so its window never got the click that would also have
/// brought it to the front (log 2026-10-07 19:27).
@MainActor enum TouchMouseGate {
    enum Mode: String { case auto, on, off }
    static let mode: Mode = {
        let v = MadeiraConfig.gameValue("env.MADEIRA_TOUCH_MOUSE")
            ?? MadeiraConfig.get("env.MADEIRA_TOUCH_MOUSE")  // 1: touches always reach the program as a mouse, 0: never; default: not while touch controller mappings are shown
            ?? ProcessInfo.processInfo.environment["MADEIRA_TOUCH_MOUSE"]
        let m: Mode = v == "1" ? .on : (v == "0" ? .off : .auto)
        LogStore.shared.log("[touch-mouse] mode=\(m.rawValue)")
        return m
    }()
    /// Set by TouchControlsOverlay.configureGamepad: the landscape overlay is
    /// visible, not editing, and has at least one controller mapping.
    static var padOverlay = false
    static func suppressing(touchpad: Bool, desktop: Bool) -> Bool {
        switch mode {
        case .on: return false
        case .off: return true
        case .auto: return padOverlay && !touchpad && !desktop
        }
    }
}

// SwiftUI-hosted placeholder: geometry + touch input only.
final class MetalBackedView: UIView {
    private static var layerRegistered = false

    // Hardware keyboard bridge: the view becomes first responder so the iOS
    // software keyboard appears, and each typed character is forwarded to
    // Wine as a virtual-key sequence (winios_post_key → send_hardware_message
    // → WM_KEYDOWN/WM_CHAR). Lets the user type into Windows dialogs (e.g.
    // Run) directly instead of relying on the browse list.
    static weak var keyboardTarget: MetalBackedView?

    /// Re-place the window-level surface after a Display fit change.
    static func relayoutLive() {
        keyboardTarget?.setNeedsLayout()
        keyboardTarget?.layoutIfNeeded()
    }
    override var canBecomeFirstResponder: Bool { true }
    static func toggleKeyboard() {
        guard let v = keyboardTarget else { return }
        if v.isFirstResponder { v.resignFirstResponder() }
        else { v.becomeFirstResponder() }
    }

    override init(frame: CGRect) {
        super.init(frame: frame)
        GamepadEventClaim.install(on: self)
        // Multi-touch REQUIRED: with it off, a fast double-tap's second
        // touch (landing before the first lift is processed) is silently
        // swallowed — drag-arm never fired (2026-07-06). Two-finger
        // scroll/right-click need it too.
        self.isMultipleTouchEnabled = true
        self.isUserInteractionEnabled = true
        self.backgroundColor = .clear
        // Hardware mouse/trackpad: hide the system pointer over the surface and
        // carry indirect-pointer motion (HardwareInput.swift). No-op with
        // MADEIRA_HWINPUT=0.
        PointerFallback.install(on: self)
    }
    required init?(coder: NSCoder) {
        super.init(coder: coder)
        GamepadEventClaim.install(on: self)
        PointerFallback.install(on: self)
    }

    // Visibility-stall postmortem (2026-07-03): the intermittent "presents
    // count but the screen stays black until a bg/fg or screenshot" state
    // was probed exhaustively — drawable leaks, present pacing, panel idle,
    // SwiftUI hosting, display-sync, CADisplayLink, transaction nudges and
    // view re-attach kicks were all eliminated (none changed it; only true
    // scene-level lifecycle events land pending frames, ~1-2 each). The one
    // robust correlate is present cadence: 60 FPS content always displays,
    // ~1 FPS content mostly doesn't. Resolution path: raise game FPS (perf
    // work), with a steady-rate re-present in DXMT as fallback insurance.

    /// The guest surface's size in guest pixels: the virtual monitor win32u
    /// reports right now (GuestDisplay.swift). A game's ChangeDisplaySettings
    /// really resizes it, so it is read back on every use rather than taken
    /// from MADEIRA_SCREEN_W/H once; a mode change re-lays-out through
    /// `observeModeChanges`. It is 1024x768 unless a session chose otherwise.
    private func guestSize() -> CGSize {
        Self.observeModeChanges()
        var w: Int32 = 0, h: Int32 = 0
        winios_screen_size(&w, &h)
        guard w > 0, h > 0 else { return CGSize(width: 1024, height: 768) }
        return CGSize(width: CGFloat(w), height: CGFloat(h))
    }

    /// The layout used for the presented layer and for touch mapping: a
    /// library session's Aspect & scaling choice (LibraryModel.displayMode);
    /// Fit everywhere else, which is what the developer interface always did.
    private func effectiveDisplayMode() -> DisplayMode {
        let library = LibraryModel.shared
        return library.current != nil ? library.displayMode : .fit
    }

    /// The presented drawable's size, or `.zero` until this session has
    /// presented into it (before that it is the 800x600 seed or the last
    /// session's size, and Aspect would letterbox against the wrong shape).
    private func drawableAspect() -> CGSize {
        guard madeira_get_present_count() != Self.presentCountAtLaunch else { return .zero }
        let d = MetalHostView.shared.metalLayer.drawableSize
        return (d.width > 0 && d.height > 0) ? d : .zero
    }

    /// The present counter when the current library session started (see
    /// drawableAspect); LibraryModel.begin sets it.
    static var presentCountAtLaunch: UInt64 = 0

    /// The rect (view-local points) the guest surface occupies. The
    /// window-level host view gets THIS frame, not our full bounds, and touch
    /// mapping uses the same rect, so letterboxing, cropping and stretching
    /// never skew input.
    private func gameRect() -> CGRect {
        let r = GameSurfaceLayout.rect(guest: guestSize(), aspect: drawableAspect(),
                                       bounds: bounds, mode: effectiveDisplayMode())
        return CGRect(x: r.minX, y: r.minY, width: max(r.width, 1), height: max(r.height, 1))
    }

    private static var drawableObservation: NSKeyValueObservation?
    private static var pendingSettle: DispatchWorkItem?
    private static var lastApplied = ""

    /// Sizes the presented layer's host view to gameRect(). `[display] apply`
    /// is logged when the result changes.
    private func applyDisplayMode(reason: String) {
        guard let w = window else { return }
        let r = gameRect()
        MetalHostView.shared.frame = convert(r, to: w)
        // The desktop compositor lays the guest display out in the same rect,
        // so Aspect / Fill / Stretch / Fit apply to desktop sessions as well.
        winios_set_desktop_rect(r.minX - bounds.minX, r.minY - bounds.minY, r.width, r.height, 1)
        let guest = guestSize(), mode = effectiveDisplayMode()
        let line = String(format: "mode=%@ guest=%.0fx%.0f bounds=%.0fx%.0f -> rect=(%.0f,%.0f %.0fx%.0f)",
                          mode.rawValue, guest.width, guest.height, bounds.width, bounds.height,
                          r.minX, r.minY, r.width, r.height)
        if line != Self.lastApplied {
            Self.lastApplied = line
            fputs("[display] apply reason=\(reason) \(line)\n", stderr)
        }
    }

    /// Re-applies the layout to the live view now and once more ~0.3 s later:
    /// UIKit reports pre-rotation bounds while a rotation is still animating,
    /// and DXMT may not have published the new drawable size yet. A burst of
    /// triggers collapses into one trailing re-apply.
    static func refreshDisplayMode(reason: String = "refresh") {
        keyboardTarget?.applyDisplayMode(reason: reason)
        pendingSettle?.cancel()
        let item = DispatchWorkItem { keyboardTarget?.applyDisplayMode(reason: "settle:\(reason)") }
        pendingSettle = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: item)
    }

    /// Guest mode changes (IOSDisplayShim posts them on the main queue) and
    /// device rotation re-lay-out the surface. Idempotent, app lifetime.
    private static let observers: [NSObjectProtocol] = [
        NotificationCenter.default.addObserver(forName: .MadeiraDisplayModeChanged, object: nil, queue: .main) { _ in
            MetalBackedView.refreshDisplayMode(reason: "mode-changed")
        },
        NotificationCenter.default.addObserver(forName: UIDevice.orientationDidChangeNotification, object: nil, queue: .main) { _ in
            MetalBackedView.refreshDisplayMode(reason: "orientation")
        },
    ]
    static func observeModeChanges() { _ = observers }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard let w = window else { return }   // detach: leave the host be
        MetalBackedView.keyboardTarget = self  // keyboard button targets the live view
        // SwiftUI ancestors attach gesture recognizers that can delay or
        // cancel raw touch delivery (double-tap timing is exactly what
        // they punish). Defuse them for our subtree.
        var v: UIView? = self
        while let s = v {
            s.gestureRecognizers?.forEach {
                $0.cancelsTouchesInView = false
                $0.delaysTouchesBegan = false
                $0.delaysTouchesEnded = false
            }
            v = s.superview
        }
        let host = MetalHostView.shared
        if host.superview !== w {
            host.removeFromSuperview()
            w.addSubview(host)
        }
        applyDisplayMode(reason: "attach")
        // S2 desktop mode: the winios compositor renders the wine virtual
        // desktop aspect-fit inside THIS placeholder's area, exactly like
        // the games' Metal layer — never over the whole phone screen.
        let full = convert(bounds, to: w)
        winios_set_compositor_frame(full.minX, full.minY, full.width, full.height)
        if !Self.layerRegistered {
            Self.layerRegistered = true
            madeira_display_set_layer(host.metalLayer)
            // DXMT writes drawableSize off the main thread; Aspect and Fill
            // height follow it, so a change re-lays-out on the main queue.
            Self.drawableObservation = host.metalLayer.observe(\.drawableSize, options: [.new]) { _, _ in
                DispatchQueue.main.async { MetalBackedView.refreshDisplayMode(reason: "drawable") }
            }
            LogStore.shared.log("MetalLayer registered with DXMT shim (window-hosted singleton)", level: .success)
        }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        if let w = window {
            applyDisplayMode(reason: "layout")
            let full = convert(bounds, to: w)
            winios_set_compositor_frame(full.minX, full.minY, full.width, full.height)
        }
    }

    // Map a touch in view-local points to guest pixels through the same
    // GameSurfaceLayout math that sizes the presented layer, then post to
    // winios.drv. Off-surface touches (letterbox, Fill's cropped margin)
    // clamp to the nearest edge.
    private func mapTouch(_ touch: UITouch) -> (Int32, Int32) {
        mapPoint(touch.location(in: self))
    }

    /// Same mapping for any point (a multi-finger midpoint, a touch's down
    /// point). In a desktop session the compositor letterboxes the desktop in
    /// its own frame, so it does the mapping.
    private func mapPoint(_ p: CGPoint) -> (Int32, Int32) {
        if desktopMode {
            let w = convert(p, to: nil)
            var px: Int32 = 0, py: Int32 = 0
            _ = winios_desktop_point_from_window(Double(w.x), Double(w.y), &px, &py)
            Self.cursor = CGPoint(x: CGFloat(px), y: CGFloat(py))   // keep the trackpad cursor in step
            return (px, py)
        }
        let g = GameSurfaceLayout.map(point: p, guest: guestSize(), aspect: drawableAspect(),
                                      bounds: bounds, mode: effectiveDisplayMode())
        return (Int32(g.x), Int32(g.y))
    }

    // ==================================================================
    // Touch pointer mode (InputSettings.touchMode), in direct and desktop
    // sessions alike:
    //   one-finger tap        — left click where the finger is
    //   hold (0.25 s) or move — left button down at the touch point, drag, lift
    //   two / three-finger tap — right / middle click at the fingers' midpoint
    //   two-finger drag       — scroll wheel
    // The drawn cursor jumps to the finger at once; nothing is posted until
    // the gesture resolves, so a multi-finger tap never leaves a stray click.
    // ==================================================================
    private var tmDownPoints: [ObjectIdentifier: CGPoint] = [:]
    private var tmGestureDownPoint = CGPoint.zero
    private var tmPeak = 0
    private var tmResolved = false
    private var tmDragTouch: UITouch?
    private var tmSlopBroken = false
    private var tmGeneration = 0
    private var tmTwoFingerLastY: CGFloat = 0
    private var tmScrollAccum: CGFloat = 0
    private static let tmHoldDelay: TimeInterval = 0.25
    private static let tmSlop: CGFloat = 10
    private let F_MDOWN: UInt32 = 0x20, F_MUP: UInt32 = 0x40

    private var touchPointerMode: Bool { InputSettings.shared.touchMode }

    private func tmResetGesture() {
        tmDownPoints.removeAll()
        tmGestureDownPoint = .zero
        tmPeak = 0
        tmResolved = false
        tmDragTouch = nil
        tmSlopBroken = false
        tmGeneration += 1
        tmTwoFingerLastY = 0
        tmScrollAccum = 0
    }

    /// Midpoint of every finger's down point this gesture.
    private func tmMidpoint() -> CGPoint {
        guard !tmDownPoints.isEmpty else { return tmGestureDownPoint }
        let pts = Array(tmDownPoints.values)
        let n = CGFloat(pts.count)
        return CGPoint(x: pts.reduce(0) { $0 + $1.x } / n, y: pts.reduce(0) { $0 + $1.y } / n)
    }

    private func tmCommitDrag(_ t: UITouch) {
        guard !tmResolved else { return }
        tmResolved = true
        tmDragTouch = t
        let (x, y) = mapPoint(tmGestureDownPoint)
        winios_post_touch_down(x, y)
    }

    /// Right/middle click at a guest position: winios_post_touch_* are
    /// left-button only, so these go through winios_pointer with ABSOLUTE.
    private func tmAbsoluteClick(down: UInt32, up: UInt32, at p: CGPoint) {
        let (x, y) = mapPoint(p)
        winios_pointer(x, y, down | F_ABS, 0)
        winios_pointer(x, y, up | F_ABS, 0)
    }

    private func touchModeBegan(_ touches: Set<UITouch>) {
        guard !tmResolved else { return }   // a finger joining mid-drag changes nothing
        for t in touches where tmDownPoints[ObjectIdentifier(t)] == nil {
            tmDownPoints[ObjectIdentifier(t)] = t.location(in: self)
        }
        tmPeak = max(tmPeak, tmDownPoints.count)
        if tmDownPoints.count == 1, let t = touches.first {
            tmGestureDownPoint = t.location(in: self)
            let (x, y) = mapPoint(tmGestureDownPoint)
            winios_cursor_move(x, y)
            tmGeneration += 1
            let gen = tmGeneration
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.tmHoldDelay) { [weak self, weak t] in
                guard let self, let t, self.tmGeneration == gen, !self.tmResolved,
                      self.tmDownPoints.count == 1 else { return }
                self.tmCommitDrag(t)
            }
        } else {
            // A second or third finger: only a multi-finger tap is possible now.
            tmGeneration += 1
        }
    }

    private func touchModeMoved(_ touches: Set<UITouch>, _ event: UIEvent?) {
        let active = activeTouches(event)
        if !tmResolved, tmDownPoints.count == 2, active.count == 2 {
            let avg = avgPoint(active)
            if tmTwoFingerLastY == 0 { tmTwoFingerLastY = avg.y }
            let dy = avg.y - tmTwoFingerLastY
            if abs(dy) > 2 { tmSlopBroken = true }
            tmTwoFingerLastY = avg.y
            tmScrollAccum += dy
            let (mx, my) = mapPoint(avg)
            while tmScrollAccum <= -14 { tmScrollAccum += 14
                winios_pointer(mx, my, F_WHEEL, UInt32(bitPattern: Int32(-120))) }
            while tmScrollAccum >= 14 { tmScrollAccum -= 14
                winios_pointer(mx, my, F_WHEEL, UInt32(bitPattern: Int32(120))) }
            return
        }
        for t in touches {
            guard let down = tmDownPoints[ObjectIdentifier(t)] else { continue }
            let p = t.location(in: self)
            if tmResolved {
                guard t === tmDragTouch else { continue }
                let (x, y) = mapPoint(p)
                winios_post_touch_move(x, y)
                continue
            }
            guard tmDownPoints.count == 1 else {
                if hypot(p.x - down.x, p.y - down.y) > Self.tmSlop { tmSlopBroken = true }
                continue
            }
            if hypot(p.x - down.x, p.y - down.y) > Self.tmSlop {
                tmSlopBroken = true
                tmCommitDrag(t)                  // button down at the ORIGINAL point
                let (x, y) = mapPoint(p)
                winios_post_touch_move(x, y)
            }
        }
    }

    private func touchModeEnded(_ touches: Set<UITouch>, _ event: UIEvent?) {
        if tmResolved, let d = tmDragTouch, touches.contains(d) {
            let (x, y) = mapPoint(d.location(in: self))
            winios_post_touch_up(x, y)
            tmResetGesture()
            return
        }
        guard !tmResolved else { return }
        guard activeTouches(event).isEmpty else { return }   // wait for every finger
        let peak = tmPeak, mid = tmMidpoint(), brokeSlop = tmSlopBroken
        tmResetGesture()
        guard !brokeSlop else { return }
        switch peak {
        case 1:
            let (x, y) = mapPoint(mid)
            winios_post_touch_down(x, y)
            winios_post_touch_up(x, y)
        case 2: tmAbsoluteClick(down: F_RDOWN, up: F_RUP, at: mid)
        case 3: tmAbsoluteClick(down: F_MDOWN, up: F_MUP, at: mid)
        default: break
        }
    }

    private func touchModeCancelled(_ touches: Set<UITouch>) {
        if tmResolved, let d = tmDragTouch, touches.contains(d) {
            let (x, y) = mapPoint(d.location(in: self))
            winios_post_touch_up(x, y)   // never leave the button held
            tmResetGesture()
            return
        }
        if !tmResolved { tmResetGesture() }
    }

    // ==================================================================
    // S2 desktop mode: trackpad-style pointer.
    //   one finger move       — cursor moves relative (like a laptop pad)
    //   single tap            — left click
    //   double tap            — double click (two rapid clicks)
    //   double tap + hold     — drag (button held while moving), lift = drop
    //   two-finger drag       — scroll wheel
    //   two-finger tap        — right click
    // Cursor position lives here (desktop px); wine + the rendered arrow
    // follow via winios_pointer / winios_cursor_move.
    // ==================================================================
    // Internal, not private: a hardware mouse moves the same desktop cursor
    // (HardwareInput.followDesktopCursor). ml1157: it starts at the centre of
    // whatever desktop size was launched (static, so it is first read at the
    // first use -- after MADEIRA_SCREEN_* are set).
    static var cursor = CGPoint(
        x: CGFloat(getenv("MADEIRA_SCREEN_W").flatMap { Int(String(cString: $0)) } ?? 960) / 2,
        y: CGFloat(getenv("MADEIRA_SCREEN_H").flatMap { Int(String(cString: $0)) } ?? 540) / 2)
    private var lastPanPoint = CGPoint.zero
    private var touchStartPoint = CGPoint.zero
    private var touchStartTime: TimeInterval = 0
    private var movedBeyondSlop = false
    private var dragActive = false
    private var dragTouch: UITouch?          // the finger that owns the drag
    private var touchGeneration = 0          // invalidates pending long-press timers
    private var twoFingerActive = false
    private var twoFingerMoved = false
    private var twoFingerStartTime: TimeInterval = 0
    private var lastTwoFingerY: CGFloat = 0
    private var scrollAccum: CGFloat = 0
    // ml641: relative motion is scaled by a float sensitivity, so the integer
    // delta we hand to wine loses a fraction every event. At low sensitivity
    // that truncation is the whole signal — carry the remainder or slow drags
    // simply do nothing.
    private var relCarryX: CGFloat = 0
    private var relCarryY: CGFloat = 0

    private let F_MOVE: UInt32 = 0x1, F_LDOWN: UInt32 = 0x2, F_LUP: UInt32 = 0x4
    private let F_RDOWN: UInt32 = 0x8, F_RUP: UInt32 = 0x10
    private let F_WHEEL: UInt32 = 0x800, F_ABS: UInt32 = 0x8000

    private var desktopMode: Bool {
        guard let v = getenv("MADEIRA_DESKTOP") else { return false }
        return v.pointee == 49  // '1'
    }
    /// The trackpad handling below also drives a direct launch unless the
    /// player picked Touch (pointer jumps to the finger).
    /// Largest guest pixel the pointer may reach. A direct launch follows the
    /// game's live display mode; the desktop keeps its session size.
    private func pointerMax() -> CGPoint {
        if desktopMode {
            return CGPoint(x: envInt("MADEIRA_SCREEN_W", 1024) - 1, y: envInt("MADEIRA_SCREEN_H", 768) - 1)
        }
        var w: Int32 = 0, h: Int32 = 0
        winios_screen_size(&w, &h)
        return CGPoint(x: Int(w > 0 ? w : 1024) - 1, y: Int(h > 0 ? h : 768) - 1)
    }
    private func envInt(_ name: String, _ def: Int) -> Int {
        guard let v = getenv(name), let i = Int(String(cString: v)) else { return def }
        return i
    }
    private func postPointer(_ flags: UInt32, data: Int32 = 0) {
        winios_pointer(Int32(Self.cursor.x), Int32(Self.cursor.y), flags, UInt32(bitPattern: data))
    }
    private func avgPoint(_ touches: [UITouch]) -> CGPoint {
        var x: CGFloat = 0, y: CGFloat = 0
        for t in touches { let p = t.location(in: self); x += p.x; y += p.y }
        let n = CGFloat(max(touches.count, 1))
        return CGPoint(x: x / n, y: y / n)
    }
    private func activeTouches(_ event: UIEvent?) -> [UITouch] {
        (event?.allTouches ?? []).filter { $0.phase != .ended && $0.phase != .cancelled }
    }

    // Direct touches that began while TouchMouseGate was suppressing stay
    // swallowed until they lift, so a change of the gate never leaves a button held.
    private var tmgSwallowed: Set<ObjectIdentifier> = []
    /// Returns the touches that should still be handled (nil = nothing left).
    private func tmgFilter(_ touches: Set<UITouch>, _ phase: UITouch.Phase) -> Set<UITouch>? {
        if phase == .began {
            _ = TouchMouseGate.mode   // logs the mode once
            guard TouchMouseGate.suppressing(touchpad: touchPointerMode, desktop: desktopMode) else { return touches }
            let direct = touches.filter { $0.type == .direct }
            guard !direct.isEmpty else { return touches }
            for t in direct { tmgSwallowed.insert(ObjectIdentifier(t)) }
            let rest = touches.subtracting(direct)
            return rest.isEmpty ? nil : rest
        }
        guard !tmgSwallowed.isEmpty else { return touches }
        let mine = touches.filter { tmgSwallowed.contains(ObjectIdentifier($0)) }
        guard !mine.isEmpty else { return touches }
        if phase == .ended || phase == .cancelled {
            for t in mine { tmgSwallowed.remove(ObjectIdentifier(t)) }
        }
        let rest = touches.subtracting(mine)
        return rest.isEmpty ? nil : rest
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        if HardwareInput.shared.interceptTouches(touches, event, .began) { return }
        guard let touches = tmgFilter(touches, .began) else { return }
        if touchPointerMode { touchModeBegan(touches); return }
        guard desktopMode else {
            guard let t = touches.first else { return }
            let (x, y) = mapTouch(t)
            winios_post_touch_down(x, y)
            return
        }
        let now = Date().timeIntervalSinceReferenceDate
        let active = activeTouches(event)
        touchGeneration += 1
        if active.count >= 2 {
            twoFingerActive = true
            twoFingerMoved = false
            twoFingerStartTime = now
            lastTwoFingerY = avgPoint(active).y
            scrollAccum = 0
            // a drag started by the first finger stays active; harmless
            return
        }
        guard let t = touches.first else { return }
        let p = t.location(in: self)
        touchStartPoint = p
        lastPanPoint = p
        touchStartTime = now
        movedBeyondSlop = false
        relCarryX = 0; relCarryY = 0   // ml641: never carry motion across a lift
        // long-press → drag: hold still for 0.5s, haptic confirms, then move
        // the window; release drops. (Replaced double-tap-hold — it raced
        // Windows' double-click detection: wine saw WM_LBUTTONDBLCLK.)
        let gen = touchGeneration
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self, self.touchGeneration == gen, !self.dragActive,
                  !self.movedBeyondSlop, !self.twoFingerActive,
                  // ml643: in mouse-look the finger is the CAMERA, not a pointer.
                  // Holding still to line up a shot must not press the mouse.
                  !InputSettings.shared.relative else { return }
            self.dragActive = true
            self.dragTouch = t
            self.postPointer(self.F_LDOWN)
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            fputs("[trackpad] long-press drag armed\n", stderr)
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        if HardwareInput.shared.interceptTouches(touches, event, .moved) { return }
        guard let touches = tmgFilter(touches, .moved) else { return }
        if touchPointerMode { touchModeMoved(touches, event); return }
        guard desktopMode else {
            guard let t = touches.first else { return }
            let (x, y) = mapTouch(t)
            winios_post_touch_move(x, y)
            return
        }
        let active = activeTouches(event)
        if twoFingerActive {
            guard active.count >= 2 else { return }
            let avg = avgPoint(active)
            let dy = avg.y - lastTwoFingerY
            lastTwoFingerY = avg.y
            if abs(dy) > 2 { twoFingerMoved = true }
            scrollAccum += dy
            // 14pt of finger travel = one wheel notch. ml641 flipped the sign:
            // on a touchscreen the content follows the finger, so dragging UP
            // scrolls DOWN through the document. It was mouse-wheel sense before.
            while scrollAccum <= -14 { scrollAccum += 14; postPointer(F_WHEEL, data: -120) }
            while scrollAccum >= 14 { scrollAccum -= 14; postPointer(F_WHEEL, data: 120) }
            return
        }
        let t: UITouch
        if dragActive, let d = dragTouch {
            guard touches.contains(d) else { return }  // only the old tap finger moved
            t = d
        } else {
            guard let f = touches.first else { return }
            t = f
        }
        let p = t.location(in: self)
        let dx = p.x - lastPanPoint.x, dy = p.y - lastPanPoint.y
        lastPanPoint = p
        if hypot(p.x - touchStartPoint.x, p.y - touchStartPoint.y) > 10 { movedBeyondSlop = true }

        /* ml641 RELATIVE (mouse-look) MODE.
         *
         * Absolute input is what made the camera spin. We post a POSITION; wine
         * turns it into the delta the game reads as
         *     x - desktop_shm->cursor.x            (queue_ios.c:2290)
         * A game that locks the cursor calls ClipCursor, and update_desktop_cursor_pos
         * then CLAMPS desktop_shm->cursor into that rect, pinning it. Our own
         * Self.cursor keeps wandering across the full 1024x768, so the subtraction
         * yields (wandering - pinned): a huge delta that never converges and is
         * re-sent on every event. Spin rate depends on WHERE the finger is, not how
         * fast it moves.
         *
         * Posting device motion instead makes that impossible to reproduce: wine
         * computes cursor.x + dx, so the delta is exactly dx no matter what the
         * game does to the cursor. No F_ABS, and Self.cursor is deliberately not
         * touched — in this mode it has no meaning.
         *
         * Sign follows PUBG/Fortnite: drag right -> view turns right -> the world
         * slides left, so a target to the RIGHT of the crosshair is pulled onto it
         * by dragging RIGHT. That is the same sign as a mouse. Negate both terms
         * for content-drag (finger-follows-world) feel. */
        if InputSettings.shared.relative {
            let sens = CGFloat(InputSettings.shared.sensRel)
            relCarryX += dx * sens
            relCarryY += dy * sens
            let ix = Int32(max(-30000, min(30000, relCarryX)))
            let iy = Int32(max(-30000, min(30000, relCarryY)))
            relCarryX -= CGFloat(ix)
            relCarryY -= CGFloat(iy)
            if ix != 0 || iy != 0 { winios_pointer(ix, iy, F_MOVE, 0) }
            return
        }

        let sens = CGFloat(InputSettings.shared.sensAbs)   // desktop px per view pt
        let lim = pointerMax()
        Self.cursor.x = min(max(Self.cursor.x + dx * sens, 0), lim.x)
        Self.cursor.y = min(max(Self.cursor.y + dy * sens, 0), lim.y)
        postPointer(F_MOVE | F_ABS)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        if HardwareInput.shared.interceptTouches(touches, event, .ended) { return }
        guard let touches = tmgFilter(touches, .ended) else { return }
        if touchPointerMode { touchModeEnded(touches, event); return }
        guard desktopMode else {
            guard let t = touches.first else { return }
            let (x, y) = mapTouch(t)
            winios_post_touch_up(x, y)
            return
        }
        let now = Date().timeIntervalSinceReferenceDate
        if twoFingerActive {
            if activeTouches(event).isEmpty {
                if !twoFingerMoved && now - twoFingerStartTime < 0.40
                    && !InputSettings.shared.relative {   // ml643: see touchesBegan
                    postPointer(F_RDOWN)
                    postPointer(F_RUP)
                }
                twoFingerActive = false
            }
            return
        }
        touchGeneration += 1   // cancel any pending long-press
        if dragActive {
            if let d = dragTouch, !touches.contains(d) {
                fputs("[trackpad] ended: non-drag finger up (drag continues)\n", stderr)
                return
            }
            fputs("[trackpad] ended: drag drop\n", stderr)
            postPointer(F_LUP)
            dragActive = false
            dragTouch = nil
            return
        }
        // stationary release before the 0.5s drag threshold = click.
        // ml643: NOT in relative mode — every small aim adjustment would fire the
        // weapon. Left/right click are on-screen buttons there instead.
        if !movedBeyondSlop && now - touchStartTime < 0.5 && !InputSettings.shared.relative {
            fputs("[trackpad] ended: click\n", stderr)
            postPointer(F_LDOWN)
            postPointer(F_LUP)
        }
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        if HardwareInput.shared.interceptTouches(touches, event, .cancelled) { return }
        guard let touches = tmgFilter(touches, .cancelled) else { return }
        if touchPointerMode { touchModeCancelled(touches); return }
        guard desktopMode else {
            guard let t = touches.first else { return }
            let (x, y) = mapTouch(t)
            winios_post_touch_up(x, y)
            return
        }
        fputs("[trackpad] CANCELLED (dragActive=\(dragActive))\n", stderr)
        touchGeneration += 1
        if dragActive { postPointer(F_LUP); dragActive = false }
        dragTouch = nil
        twoFingerActive = false
    }
}

// SwiftUI wrapper around the placeholder view.
// iOS software-keyboard → Wine key events. Each character is mapped to a
// US-layout virtual-key (+ shift where needed) and posted as a down/up pair;
// the message queue's ToUnicode then produces the right WM_CHAR. Paths need
// the full symbol set (":" "\" "-" "." "_"), so the table is comprehensive.
extension MetalBackedView: UIKeyInput {
    var hasText: Bool { false }

    // US-keyboard VK + shift for a character. Returns nil for chars we can't map.
    static func vkForChar(_ ch: Character) -> (Int32, Bool)? {
        if ch == "\n" || ch == "\r" { return (0x0D, false) }   // VK_RETURN
        if ch == "\t" { return (0x09, false) }                 // VK_TAB
        if ch == " " { return (0x20, false) }                  // VK_SPACE
        if ch.isLetter, let up = ch.uppercased().first?.asciiValue, up >= 0x41, up <= 0x5A {
            return (Int32(up), ch.isUppercase)                 // VK_A..VK_Z
        }
        if let a = ch.asciiValue, a >= 0x30, a <= 0x39 {
            return (Int32(a), false)                           // VK_0..VK_9 (unshifted)
        }
        let table: [Character: (Int32, Bool)] = [
            "!": (0x31, true), "@": (0x32, true), "#": (0x33, true), "$": (0x34, true),
            "%": (0x35, true), "^": (0x36, true), "&": (0x37, true), "*": (0x38, true),
            "(": (0x39, true), ")": (0x30, true),
            "-": (0xBD, false), "_": (0xBD, true),
            "=": (0xBB, false), "+": (0xBB, true),
            "[": (0xDB, false), "{": (0xDB, true),
            "]": (0xDD, false), "}": (0xDD, true),
            "\\": (0xDC, false), "|": (0xDC, true),
            ";": (0xBA, false), ":": (0xBA, true),
            "'": (0xDE, false), "\"": (0xDE, true),
            ",": (0xBC, false), "<": (0xBC, true),
            ".": (0xBE, false), ">": (0xBE, true),
            "/": (0xBF, false), "?": (0xBF, true),
            "`": (0xC0, false), "~": (0xC0, true),
        ]
        return table[ch]
    }

    func insertText(_ text: String) {
        // A hardware keyboard's presses reach Wine raw (HardwareInput); UIKit
        // also delivers them here as text, which would type every key twice.
        if HardwareInput.shared.handlesTyping { return }
        for ch in text {
            guard let (vk, shift) = MetalBackedView.vkForChar(ch) else { continue }
            if shift { winios_post_key(0x10, 1) }   // VK_SHIFT down
            winios_post_key(vk, 1)
            winios_post_key(vk, 0)
            if shift { winios_post_key(0x10, 0) }    // VK_SHIFT up
        }
    }

    func deleteBackward() {
        if HardwareInput.shared.handlesTyping { return }
        winios_post_key(0x08, 1)   // VK_BACK down
        winios_post_key(0x08, 0)
    }

    // Traits: keep iOS from rewriting path characters.
    var keyboardType: UIKeyboardType { get { .asciiCapable } set {} }
    var autocorrectionType: UITextAutocorrectionType { get { .no } set {} }
    var autocapitalizationType: UITextAutocapitalizationType { get { .none } set {} }
    var smartQuotesType: UITextSmartQuotesType { get { .no } set {} }
    var smartDashesType: UITextSmartDashesType { get { .no } set {} }
    var spellCheckingType: UITextSpellCheckingType { get { .no } set {} }
}

/// Pointer settings, persisted to the app container.
///
/// ml641. Two independent sensitivities, because the two modes mean different
/// things and a single slider would fight itself:
///   • absolute  — trackpad gain, desktop px per view pt. This IS the old
///     hardcoded `sens = 2.0`, so the default reproduces today's desktop feel
///     exactly.
///   • relative  — mouse counts per view pt for mouse-look. What the right value
///     is depends on the GAME's own sensitivity and FOV, which we cannot see, so
///     it has to be calibrated by hand once. See the comment in touchesMoved.
///
/// Stored as JSON in Documents/ rather than UserDefaults: that is the container
/// we already know survives reinstall (verified), and it can be pulled and
/// edited with the same devicectl command we use for the log.
final class InputSettings: ObservableObject {
    static let shared = InputSettings()

    @Published var relative: Bool  = false { didSet { save() } }
    /// Direct launch only: the pointer jumps to the finger and a touch is a
    /// click there (the behaviour before trackpad mode reached direct launches).
    @Published var directTouch: Bool = false { didSet { save() } }

    /// madeira-bcd: the one choice the Session panel's pointer picker shows,
    /// mapped onto upstream's flags: absolute (the finger is the pointer),
    /// touch (upstream's touch pointer mode: tap to click, hold to drag) and
    /// relative (mouse-look).
    enum PointerMode: Int, CaseIterable { case absolute, touch, relative }
    var mode: PointerMode {
        get { touchMode ? .touch : (relative ? .relative : .absolute) }
        set { touchMode = newValue == .touch; relative = newValue == .relative }
    }
    @Published var sensAbs:  Double = 2.0  { didSet { save() } }
    @Published var sensRel:  Double = 2.0  { didSet { save() } }
    /// Touch pointer mode (MetalBackedView.touchModeBegan): tap to click where
    /// the finger is, hold or move to drag. Checked before `relative`; the
    /// library's pointer picker keeps the two mutually exclusive.
    @Published var touchMode = false { didSet { save() } }
    /// ml649: heavy diagnostics. Default OFF so the shipped default is the fast
    /// path; flip it on only when a run needs to be explainable.
    @Published var diagnostics = false { didSet { madeira_set_diag_enabled(diagnostics ? 1 : 0); save() } }
    /// Hardware mouse gain (HardwareInput): counts per reported unit. 1.0 passes
    /// the device's deltas through unchanged.
    @Published var sensMouse: Double = 1.0 { didSet { save() } }
    /// Drop AssistiveTouch's synthesised clicks while a hardware mouse is live
    /// (HardwareInput.shouldIgnore). `"ignoreTouchesWithMouse": false` in
    /// bcd-input.json turns the filter off.
    @Published var ignoreTouchesWithMouse = true { didSet { save() } }
    /// "Move the mouse with the right stick" (HardwareInput.swift, PadStickMouse). Off by
    /// default: a program that reads the controller already gets the stick.
    @Published var padRightStickMouse = false { didSet { save() } }

    /// didSet fires for assignments made in init() because the properties are
    /// already initialised by then; without this the first launch would write
    /// the defaults back over a file it had only half-read.
    private var loading = false

    private static var url: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("bcd-input.json")
    }

    private init() {
        loading = true
        if let d = try? Data(contentsOf: Self.url),
           let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] {
            relative = j["relative"] as? Bool   ?? false
            directTouch = j["directTouch"] as? Bool ?? false
            sensAbs  = j["sensAbs"]  as? Double ?? 2.0
            sensRel  = j["sensRel"]  as? Double ?? 2.0
            touchMode = j["touchMode"] as? Bool ?? false
            diagnostics = j["diagnostics"] as? Bool ?? false
            sensMouse = j["sensMouse"] as? Double ?? 1.0
            ignoreTouchesWithMouse = j["ignoreTouchesWithMouse"] as? Bool ?? true
            padRightStickMouse = j["padRightStickMouse"] as? Bool ?? false
        }
        loading = false
        madeira_set_diag_enabled(diagnostics ? 1 : 0)   // push the restored value down
    }

    private func save() {
        guard !loading else { return }
        let j: [String: Any] = ["relative": relative, "sensAbs": sensAbs, "sensRel": sensRel, "diagnostics": diagnostics,
                                "sensMouse": sensMouse, "ignoreTouchesWithMouse": ignoreTouchesWithMouse,
                                "padRightStickMouse": padRightStickMouse,
                                "touchMode": touchMode]
        guard let d = try? JSONSerialization.data(withJSONObject: j) else { return }
        try? d.write(to: Self.url, options: .atomic)
    }
}

struct MadeiraMetalView: UIViewRepresentable {
    func makeUIView(context: Context) -> MetalBackedView {
        return MetalBackedView(frame: CGRect(x: 0, y: 0, width: 800, height: 600))
    }
    func updateUIView(_ uiView: MetalBackedView, context: Context) {}
}

struct ContentView: View {
    /// iOS 26 and later blur content under the navigation bar themselves.
    private static var systemScrollEdge: Bool {
        if #available(iOS 26.0, *) { return true }
        return false
    }
    @StateObject private var logStore = LogStore.shared
    @StateObject private var jitCoordinator = JITCoordinator.shared
    @State private var jitStatus: JITStatus = .unknown
    /// Play without JIT: the start that waits for Enable JIT (jitReadyForLaunch).
    @State private var launchAfterJIT: (() -> Void)?
    @State private var entitlements: EntitlementStatus?

    /// Parses a stored value into a size, falling back to the default rather
    /// than trusting a string that survived an app update.
    static func desktopSize(_ value: String) -> (Int, Int) {
        if value == "native" {
            let b = UIScreen.main.bounds
            return (max(640, Int(b.width)), max(480, Int(b.height)))
        }
        let parts = value.split(separator: "x")
        if parts.count == 2, let w = Int(parts[0]), let h = Int(parts[1]), w >= 640, h >= 480 {
            return (w, h)
        }
        return (960, 540)
    }
    /// The library engine (Library.swift): BCD's interface (BCDLiveFrontEnd) is shown while no
    /// game runs, and a running library session gets the full-screen `sessionBody`.
    @ObservedObject private var library = LibraryModel.shared

    enum JITStatus {
        case unknown
        case testing
        case available
        case unavailable
    }

    var body: some View {
        /* ml658: was NavigationView, which is deprecated and — the reason this
         * matters — defaults to a SPLIT VIEW on iPad. TARGETED_DEVICE_FAMILY is
         * "1,2", so iPad is a shipping target, and the whole UI was being forced
         * into a sidebar/detail arrangement it was never laid out for.
         * NavigationStack is single-column on every device. Safe here: there are
         * no NavigationLinks anywhere in the app, so nothing depended on the
         * two-column selection behaviour. */
        NavigationStack {
            Group {
                if library.current != nil {
                    sessionBody
                        .navigationBarHidden(true)
                } else {
                    // BCD's own interface (BCDLiveFrontEnd.swift), with Play, Enable JIT and the
                    // Dock start (BCD, 2026-10-18; the only interface since wave 3, 2026-10-19).
                    BCDLiveFrontEnd(play: launchLibraryEntry, enableJIT: enableJIT,
                                    startDock: { startDock($0, compactPool: $1) })
                }
            }
            .navigationTitle("BCD")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.regularMaterial, for: .navigationBar)
            // The library keeps a material bar only before iOS 26. From iOS 26 the
            // system draws its soft scroll edge effect instead: content scrolls
            // under the title, the buttons and the search field behind a
            // progressive blur (as in the App Store), with no hard edge.
            .toolbarBackground(!Self.systemScrollEdge ? .visible : .automatic, for: .navigationBar)
            // A second session cannot start in this process; offer to close Madeira.
            .alert("BCD needs a restart", isPresented: Binding(get: { library.restartNotice != nil },
                                                               set: { if !$0 { library.restartNotice = nil } })) {
                Button("Close BCD") {
                    LogStore.shared.log("[session-once] closed by the user for a restart")
                    exit(0)
                }
                Button("Later", role: .cancel) { library.restartNotice = nil }
            } message: { Text(library.restartNotice ?? "") }
            .modifier(AutoTuneAlert())   // madeira-bcd: auto-tune's result (AutoTuneViews.swift); never shown while it is off
            // CS_DEBUGGED without a debugger (JIT enabled outside Madeira): offer Madeira's own request.
            .alert("Enable JIT", isPresented: Binding(get: { library.jitNotice != nil },
                                                      set: { if !$0 { library.jitNotice = nil } })) {
                Button("Enable JIT") { library.jitNotice = nil; enableJIT() }
                Button("Later", role: .cancel) { library.jitNotice = nil }
            } message: { Text(library.jitNotice ?? "") }
            .sheet(isPresented: $jitCoordinator.showSetup) { BCDJITSetupSheet(enableJIT: { enableJIT() }) }   // madeira-bcd 2026-10-18: BCD's JIT setup (BCDJITLive.swift)
            // A Steam game's saves may not be the latest (cloudClear).
            .alert(library.cloudNotice?.title ?? "Steam Cloud", isPresented: Binding(get: { library.cloudNotice != nil },
                                                                                     set: { if !$0 { library.cloudNotice = nil } })) {
                if let notice = library.cloudNotice {
                    switch notice.kind {
                    case .syncing: Button("Sync first") { library.cloudNotice = nil; cloudWait(notice.appID) }
                    case .unchecked: Button("Try again") { library.cloudNotice = nil; cloudWait(notice.appID) }
                    case .conflict:
                        Button("Choose saves") {
                            library.cloudNotice = nil; library.cloudRetry = nil
                            library.showDetail = library.entries.first { $0.steamAppID == notice.appID }?.id
                        }
                    }
                    Button("Play anyway") {
                        LogStore.shared.log("[steam-cloud] app=\(notice.appID) before-play: launched anyway")
                        library.cloudNotice = nil; library.cloudBypass = notice.appID
                        let retry = library.cloudRetry; library.cloudRetry = nil; retry?()
                    }
                    Button("Cancel", role: .cancel) { library.cloudNotice = nil; library.cloudRetry = nil }
                }
            } message: { Text(library.cloudNotice?.message ?? "") }
            .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
                library.refreshFlag()
                if library.current == nil { MetalHostView.shared.isHidden = true }
            }
            .onAppear {
                StikJITHelper.installTrapHandler()   // madeira-bcd ios18-jit: also when started already debugged
                entitlements = EntitlementStatus.check()
                logEntitlementStatus()
                logStore.log("[build] \(BuildStamp.text)")
                DeviceDiagnostics.logStartup()
                DeviceCapability.logStartup()   // madeira-bcd: the GPU and CPU line; an A13's one-time notice
                DockOffline.begin()
                DeviceLoadDiagnostics.start()
                // Madeira Dock: an unconsumed sign-in transfer from an earlier run goes.
                if wine_process_is_running() == 0 { MadeiraDock.cleanup() }
                // madeira-bcd: the Dock's Reset sets the VC++ markers back to a new prefix's
                // values, read from the bundled prefix template (DockReset.swift).
                DockReset.baselineProvider = { DockReset.templateRegistry { madeira_extract_prefix_tgz($0, $1) } }
            }
            .onReceive(NotificationCenter.default.publisher(for: SteamSignIn.didChange)) { _ in
                if !SteamSignIn.isSignedIn { MadeiraDock.cleanup() }
            }
            // A Home Screen shortcut (madeira://play?exe=...) starts its library entry,
            // now or, from a cold start, once the library is up.
            .onReceive(ShortcutRouter.shared.$pendingExe) { _ in launchPendingShortcut() }
        }
    }

    /// A library session: the game full screen in either orientation, with the
    /// library's menu button, starting screen and in-game menu drawn above it by
    /// TouchControlsOverlay (BCDSessionLiveLayer), in the window above the game surface.
    private var sessionBody: some View {
        ZStack {
            Color.black
            MadeiraMetalView()
                .onAppear { TouchControlsHost.attach() }
                .onReceive(NotificationCenter.default.publisher(
                    for: UIDevice.orientationDidChangeNotification)) { _ in
                    TouchControlsHost.attach()   // re-frame to the new bounds
                }
        }
        .ignoresSafeArea()
        .background(Color.black)
        .statusBarHidden(true)
        .modifier(DeferSystemEdges(active: true))   // madeira-bcd: env.MADEIRA_DEFER_EDGES (opt-in); without it the view is unchanged
    }

    private func logEntitlementStatus() {
        guard let ents = entitlements else { return }
        logStore.log("Checking entitlements...")
        // allow-jit is a macOS entitlement and is never granted on iOS, so
        // false is the expected reading -- logging it as an error made a
        // healthy run look broken. What JIT actually rides on is
        // get-task-allow -> CS_DEBUGGED, which the JIT badge now reports.
        logStore.log("  allow-jit: \(ents.jitAllowed) (macOS-only, inert on iOS)", level: .debug)
        logStore.log("  increased-memory-limit: \(ents.increasedMemory)", level: ents.increasedMemory ? .success : .debug)
        logStore.log("  extended-virtual-addressing: \(ents.extendedVA)", level: ents.extendedVA ? .success : .debug)
        logStore.log("  increased-debugging-memory-limit: \(ents.increasedDebugMemory)", level: ents.increasedDebugMemory ? .success : .debug)
        // The memory limit is the entitlement a session needs; the address map may
        // be the standard 63 GB one.
        if !ents.increasedMemory {
            logStore.log("  Tip: Use GetMoreRam to add increased-memory-limit", level: .info)
        }
    }

    private func enableJIT() {
        // Explains why JIT cannot be enabled on a copy signed without get-task-allow; 0 opens StikDebug regardless.
        // A debugger can attach only to a process whose signature carries
        // get-task-allow (a development signature). A copy signed with a
        // distribution or enterprise certificate lacks it, StikDebug can never
        // attach, and CS_DEBUGGED never appears however often this is tapped.
        if !SigningStatus.current.debuggable, MadeiraConfig.flag("MADEIRA_JIT_SIGNING_CHECK") {
            jitStatus = .unavailable
            logStore.log(String(format: "[jit-signing] get-task-allow is missing (cs-flags=0x%x): no debugger can attach to this copy, "
                                + "so JIT cannot be enabled. Reinstall BCD with a development certificate.",
                                SigningStatus.current.flags), level: .error)
            library.error = SigningStatus.notDebuggableMessage
            launchAfterJITEnded(started: false)
            return
        }
        jitStatus = .testing
        logStore.log("Requesting JIT with \(jitCoordinator.resolvedMethod.title)...")

        jitCoordinator.enable { result in
            switch result {
            case .success:
                jitStatus = .available
                // madeira-bcd ios18-jit: where BCD builds its pool without a debugger, JIT is on with none attached.
                logStore.log(isDebuggerTraced() || !JITNoDebuggerPool.usable ? "JIT enabled! Debugger attached."
                             : "JIT enabled: no debugger is attached; BCD builds its JIT memory without one.", level: .success)
                launchAfterJITEnded(started: true)
            case .failure(let failure):
                launchAfterJITEnded(started: false)
                if let coordinatorError = failure as? JITCoordinator.CoordinatorError,
                   case .setupRequired = coordinatorError {
                    jitStatus = .unknown
                    return
                }
                jitStatus = .unavailable
                logStore.log("Failed to enable JIT: \(failure.localizedDescription)", level: .error)
                library.error = failure.localizedDescription
            }
        }
    }

    /// Enable JIT finished: a Play that waited for it starts its game, only when the
    /// debugger is attached (so the start cannot ask for JIT again) and nothing else
    /// started meanwhile. A failure drops it: a later Enable JIT starts no game.
    private func launchAfterJITEnded(started: Bool) {
        guard let launch = launchAfterJIT else { return }
        launchAfterJIT = nil
        library.startingJIT = nil
        guard started, StikJITHelper.ready, library.current == nil, wine_process_is_running() == 0 else {
            logStore.log("[jit-on-play] JIT did not come on: the game was not started")
            // A failure has its own error; this one closes the details page as well.
            if started, library.current == nil { library.error = "JIT is now enabled, but the game didn't start. Tap Play once more." }
            return
        }
        logStore.log("[jit-on-play] JIT is on: starting the game")
        launch()
    }

    /// Whether a launch may ask for the JIT pool. `then` makes Play
    /// enable JIT itself (the same flow as Enable JIT, LocalDevVPN and the Madeira JIT
    /// shortcut included) and start the game once the debugger is attached; that also
    /// covers CS_DEBUGGED set with no debugger attached (JIT enabled from StikDebug's
    /// own list, which attaches and leaves). Without `then`, the library offers
    /// Madeira's Enable JIT instead of starting a launch that cannot get its pool.
    private func jitReadyForLaunch(entry: UUID? = nil, then launch: (() -> Void)? = nil) -> Bool {
        if StikJITHelper.ready { return true }
        if let launch {
            logStore.log("[jit-on-play] JIT is not on: enabling it, then starting the game")
            launchAfterJIT = launch
            library.startingJIT = entry
            if jitStatus != .testing { enableJIT() }   // a second Play while it runs only replaces the game
            return false
        }
        if StikJITHelper.flaggedWithoutDebugger {
            logStore.log("[jit-debugger] launch held: CS_DEBUGGED is set but no debugger is attached; "
                         + "JIT has to be enabled again from BCD", level: .error)
            library.jitNotice = StikJITHelper.noDebuggerMessage
        } else {
            logStore.log("JIT not enabled. Press 'Enable JIT' first.", level: .error)
            library.error = "JIT has to be enabled before a game can start."
        }
        return false
    }

    /// Whether a Steam game may start as far as its Steam Cloud saves go. If a sync
    /// is running, the last check failed or never ran, or saves wait for a choice,
    /// an alert asks first; `retry` starts the game again from there. A check that
    /// is only old is repeated first, without asking.
    private func cloudClear(_ appID: Int, name: String, retry: @escaping () -> Void) -> Bool {
        if library.cloudBypass == appID { library.cloudBypass = nil; return true }
        guard let hold = SteamOwnedLibrary.shared.cloudHold(appID) else { return true }
        library.cloudRetry = retry
        if hold == .stale { cloudWait(appID); return false }
        LogStore.shared.log("[steam-cloud] app=\(appID) before-play: held \(hold)")
        library.cloudNotice = cloudNotice(appID, name: name, hold: hold)
        return false
    }

    private func cloudNotice(_ appID: Int, name: String, hold: SteamOwnedLibrary.CloudHold) -> LibraryModel.CloudNotice {
        switch hold {
        case .syncing, .stale:
            return .init(appID: appID, kind: .syncing, title: "Steam Cloud sync is still running",
                         message: "Steam Cloud is still checking or downloading \(name)'s saves. If you start now, you may end up playing older saves.")
        case .unchecked(let why):
            return .init(appID: appID, kind: .unchecked, title: "Couldn't check Steam Cloud",
                         message: "BCD can't tell whether the saves of \(name) on this device are the newest."
                            + (why.map { " (\($0))" } ?? "") + " If another device has newer saves and you start now, you'll have to choose between them later.")
        case .conflict(let count):
            return .init(appID: appID, kind: .conflict, title: "Steam Cloud has different saves",
                         message: "This device and Steam Cloud disagree on \(count) of \(name)'s saves. Pick the ones to keep on the game's page, or play with the saves on this device.")
        }
    }

    /// Syncs the game's saves, then starts it; if the saves are still not settled, asks again.
    private func cloudWait(_ appID: Int) {
        guard SteamOwnedLibrary.shared.cloudWaitingFor == nil else { return }
        let name = MadeiraDock.games(drive: MadeiraDock.drive).first { $0.id == appID }?.name ?? "This game"
        Task { @MainActor in
            let hold = await SteamOwnedLibrary.shared.settleCloud(appID)
            if let hold {
                library.cloudNotice = cloudNotice(appID, name: name, hold: hold)
            } else {
                let retry = library.cloudRetry; library.cloudRetry = nil; retry?()
            }
        }
    }

    /// A Home Screen shortcut waiting for the library (a link opened at a cold start
    /// arrives before the library is up).
    private func launchPendingShortcut() {
        guard library.current == nil, let exe = ShortcutRouter.shared.pendingExe else { return }
        ShortcutRouter.shared.pendingExe = nil
        launchShortcut(exe)
    }

    /// A Home Screen shortcut starts a library game by its Windows path. Only a
    /// game already in the library starts (owner's decision 2026-10-07, as
    /// upstream): a game outside it is added by hand first.
    private func launchShortcut(_ exe: String) {
        let key = exe.lowercased()
        if let entry = library.entries.first(where: { $0.desktop != true && $0.windowsPath.lowercased() == key }) {
            launchLibraryEntry(entry); return
        }
        LogStore.shared.log("[shortcut] \(exe) is not in the library: not started", level: .error)
        library.error = "The game this shortcut opens isn't in your library. Add it there first, then try the shortcut again."
    }

    /// Play in the library (Library.swift): checks that a session can start,
    /// applies the entry's launch profile and runs the same full sequence as the
    /// developer interface's buttons.
    private func launchLibraryEntry(_ entry: LibraryEntry) {
        if entry.relativePath.hasPrefix("External/"), (try? LibraryModel.executable(entry.launchRelativePath)) == nil {
            library.error = "This external game is unavailable. Connect the SSD, then choose its games folder in Settings › Storage."
            return
        }
        // madeira-bcd: what this device's GPU or CPU cannot run is refused here, before JIT is
        // asked for (DeviceCapability.swift); A14 and later devices always pass.
        if let refusal = DeviceCapability.libraryRefusal(entry) { library.error = refusal; return }
        // madeira-bcd: a game seen for the first time starts with Madeira's recommended
        // settings (GameRecommendationsApply.swift).
        var entry = entry
        let isNew = !library.entries.contains { $0.id == entry.id }
        if GameRecommendations.prepare(&entry, new: isNew) { library.save(entry) }
        // A Steam game starts through Madeira Dock with its own launch profile (SteamGames.swift),
        // unless its Game details page chose "The game": then its own program starts below, like
        // any library game (SteamDirectStart).
        if let appID = entry.steamAppID, !entry.startsSteamGameDirectly {
            // A second Play while this game's Dock start is on its way changes nothing: not the
            // launch options applied below, and not the first start's sign-in (DockLaunchGate).
            guard !DockStartScreen.launchGate.busy(now: ProcessInfo.processInfo.systemUptime) else {
                LogStore.shared.log("[madeira-dock] a start is already on its way: this Play is ignored app=\(appID)")
                return
            }
            guard let game = MadeiraDock.games(drive: MadeiraDock.drive).first(where: { $0.id == appID }) else {
                library.error = "Steam doesn't show this game as installed anymore. Refresh the library, then try again."; return
            }
            LogStore.shared.log("[steam-games] play app=\(appID)")
            // madeira-bcd: the game's own options (MetalFX, AVX, NVIDIA, game config) --
            // this path never reached BCDLaunch, so a Steam game ran without them.
            BCDLaunch.applyLibrary(entry, sessionLog: false)
            startDock(game, compactPool: MadeiraDockModel.shared.compactPool, profile: entry)
            return
        }
        if let appID = entry.steamAppID {
            guard cloudClear(appID, name: entry.title, retry: { launchLibraryEntry(entry) }) else { return }
            guard entry.steamProgram?.isEmpty == false else {
                library.error = "No program is set for this game. Pick one in Game details › Steam › Program."; return
            }
            LogStore.shared.log("[steam-start] app=\(appID) direct source=\(entry.steamProgramSource ?? "-") " +
                                "args=\(entry.launchArguments.isEmpty ? 0 : 1) folder=\(entry.steamWorkingWindowsPath == nil ? "program" : "steam")")
        }
        guard wine_process_is_running() == 0, wineserver_is_running() == 0, library.current == nil else {
            library.error = "Another session is still running."; return
        }
        // One Wine session per app run (see LibraryModel.sessionsThisRun).
        if LibraryModel.sessionsThisRun > 0, MadeiraConfig.flag("MADEIRA_ONE_SESSION_PER_RUN") {
            LogStore.shared.log("[session-once] launch held: \(LibraryModel.sessionsThisRun) session(s) already ran in this app run")
            library.restartNotice = LibraryModel.restartMessage; return
        }
        // The same precondition runWineFullSequence checks: the JIT pool is
        // taken at launch, through the debugger. Without it, Play enables JIT and
        // continues from here once it is on.
        guard jitReadyForLaunch(entry: entry.id, then: { startLibraryEntry(entry) }) else { return }
        startLibraryEntry(entry)
    }

    /// The rest of Play, with JIT on: checks the entry's launch profile and starts it.
    private func startLibraryEntry(_ entry: LibraryEntry) {
        do { if entry.desktop != true { _ = try LibraryModel.executable(entry.launchRelativePath) }; try entry.validate() }
        catch {
            library.error = error.localizedDescription
            logStore.log("[launch-preflight] profile validation failed: \(error.localizedDescription)", level: .error)
            return
        }
        // launchArguments carries the whole ml1163 command (explorer's /desktop=, the quoted
        // program, its arguments); validate() and the bridge's tokenizer take 4 KB.
        guard entry.launchWindowsPath.utf8.count < 1024, entry.launchArguments.utf8.count < 4096 else {
            library.error = "The program path or its launch arguments are longer than BCD allows."; return
        }
        entry.configureLaunch()
        // madeira-bcd: the game's own options (LibraryBCD.swift); this run's
        // log is named below, by upstream's rule (the program a launch starts).
        BCDLaunch.applyLibrary(entry, sessionLog: false)
        exportBCDDesktopProfile(entry)
        if let line = DesktopGDISection.apply(entry) { logStore.log(line) }
        // This run's log under the program's name too (Documents/logs). A Steam game started
        // through Madeira Dock above gets its own from ntdll, once Valve's client starts it.
        let program = entry.desktop == true ? "explorer.exe"
            : entry.launchWindowsPath.split(separator: "\\").last.map(String.init) ?? entry.launchWindowsPath
        LogStore.shared.startSessionLog(program: program)
        library.begin(entry)
        runWineFullSequence(profile: entry)
    }

    /// madeira-bcd: LibraryPrefs' AVX, NVIDIA and Wine C++ runtime switches for a
    /// library launch, set again after LibraryEntry.applyEnvironment (which unsets
    /// what its own fields do not choose). For the Desktop entry too: Game details
    /// writes them for every entry, and BCDLaunch.applyLibrary clears them there.
    static func reassertBCDSwitches(_ entry: LibraryEntry) {
        let path = entry.windowsPath
        var on: [String] = []
        if LibraryPrefs.avx(path) { setenv("MADEIRA_FEX_AVX", "1", 1); on.append("avx") }
        if LibraryPrefs.wineVCRT(path) { setenv("MADEIRA_WINE_VCRT", "1", 1); on.append("wine-vcrt") }
        if LibraryPrefs.nvidia(path) {
            setenv("DXMT_ENABLE_NVEXT", "1", 1)
            setenv("DXMT_WSI_MONITOR_IDENTITY", "1", 1)
            setenv("DXMT_WSI_MODE_TABLE", "1", 1)
            if entry.desktop == true, getenv("MADEIRA_DXMT_EXTRA") == nil { setenv("MADEIRA_DXMT_EXTRA", "dxgi.customDeviceId=2544", 1) }
            on.append("nvidia")
        }
        if !on.isEmpty { LogStore.shared.log("[bcd] game switches: \(on.joined(separator: " "))") }
    }

    /// madeira-bcd: the Desktop entry's own file (Game details writes every entry's
    /// options to GameProfiles.swift files); BCDLaunch.applyLibrary leaves the
    /// Desktop without one, so it is exported here, before the session starts.
    private func exportBCDDesktopProfile(_ entry: LibraryEntry) {
        guard entry.desktop == true else { return }
        let file = GameProfile(windowsPath: entry.windowsPath)
        guard file.hasSettings, let url = file.url else { return }
        setenv("MADEIRA_CFG_GAME", url.path, 1)
        logStore.log("[bcd] Desktop: its own settings file \(url.lastPathComponent)")
    }

    /// Full sequence: allocate JIT pool, start wineserver, start Wine.
    /// Debugger stays attached during PE loading so mprotect_exec can use BRK
    /// to prepare code pages. Detach happens after Wine finishes + recovery.
    // The JIT pool can only be placed once per launch. StikJITHelper's own
    // ml596 note says so: the guest window the second pool needs is already
    // held by the first, the kernel hands back the same address every time,
    // so "the three retries are three identical attempts" and allocatePool
    // gives up -- then kills the app ten seconds later.
    //
    // Ten seconds is long enough that the death lands nowhere near the button
    // that caused it, so starting a second game after the desktop reads as
    // "the app crashed at random" rather than "one Wine session per launch".
    // It cost a debugging session to find that out from a log. Refuse at the
    // button, immediately, and say what to do instead.
    private static var wineSequenceStarted = false

    /// `profile` is a library entry whose launch profile applies to this run.
    private func runWineFullSequence(profile: LibraryEntry? = nil) {
        guard jit_check_debugged() else {
            logStore.log("JIT not enabled. Press 'Enable JIT' first.", level: .error)
            if profile != nil { LibraryModel.shared.launchFailed() }
            return
        }
        // madeira-bcd: every launch, the buttons and BCD Dock included (DeviceCapability.swift):
        // a GPU or CPU this build cannot run gets a message instead of a crash; A14 and later pass.
        if let refusal = DeviceCapability.launchRefusal(desktopEntry: profile?.desktop == true, dock: MadeiraDock.launchPending) {
            _ = MadeiraDock.takeLaunchRequest()   // a refused Dock launch leaves nothing for the next one
            LibraryModel.shared.launchFailed(refusal)
            BCDStartingHost.hide()
            return
        }
        if Self.wineSequenceStarted {
            logStore.log("A Wine session already ran in this launch, and the JIT pool can only "
                       + "be placed once -- a second one would abort and kill the app 10s later. "
                       + "Quit BCD, relaunch it, enable JIT, and start this one first.",
                         level: .error)
            // madeira-bcd ios18-jit: a failed pool request no longer ends the app, so a second Play can
            // land here; say so instead of leaving the library on its starting screen (no-op without one).
            LibraryModel.shared.launchFailed("A game already started, or tried to start, in this run of BCD, and BCD "
                + "runs one per run. Quit BCD (swipe it away in the app switcher), open it again, and start the game.")
            return
        }
        Self.wineSequenceStarted = true

        // Steam downloads wait for the session, and the app's own Steam connection closes
        // before Valve's client signs in with the same account (SteamOwnedLibrary).
        SteamOwnedLibrary.shared.sessionChanged(active: true)
        /* ml2100: XInput (default) or the HID controller; before the wineserver starts. */
        GamepadInput.shared.beginPadSession()
        /* ml1990: player 1 exists before the game enumerates XInput. */
        GamepadInput.shared.reserveSessionSlot(touchControls: TouchControlsModel.shared.offersControllerInput)
        /* ml1095: the one config file, Documents/bcd.cfg. */
        if MadeiraConfig.present {
            let cfg = MadeiraConfig.all().sorted { $0.key < $1.key }
            logStore.log("bcd.cfg: " + (cfg.isEmpty ? "(empty)" : cfg.map { "\($0.key)=\($0.value)" }.joined(separator: " ")))
        } else {
            logStore.log("bcd.cfg: absent")
        }

        logStore.log("Running full Wine sequence...")
        DeviceDiagnostics.logLaunch()

        // Start a main thread heartbeat to diagnose hang
        var heartbeatCount = 0
        let heartbeat = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
            heartbeatCount += 1
            os_log("[HEARTBEAT] main thread alive #%d", heartbeatCount)
        }

        // Pause UI flushing — prevents ALL SwiftUI re-renders during Wine execution,
        // so zero main thread hang time accumulates while debugger is attached
        logStore.uiPaused = true

        // Suppress os_log from wineserver — hundreds of messages/sec cause os_log buffer
        // contention that blocks the main thread RunLoop, triggering iOS hang detection
        ws_log_quiet = 1

        DispatchQueue.global(qos: .userInitiated).async {
            // A library entry's launch profile (executable, arguments, x87
            // precision, frame limit).
            if let profile {
                profile.applyEnvironment()
                // madeira-bcd: applyEnvironment sets or unsets AVX and "Report an NVIDIA
                // GPU" from the entry's own fields; BCDLaunch.applyExtras exported the
                // game's LibraryPrefs switches before, on the main thread (Game details
                // writes those). Either one turns a switch on.
                Self.reassertBCDSwitches(profile)
                logStore.log("[launch-route] library profile applied")
                // madeira-bcd: a Dock session of one-time installs runs without the game's
                // address-space holds, which leave its 32-bit setups no room.
                let dropped = DockInstallers.applyInstallSessionConfig()
                if !dropped.isEmpty {
                    logStore.log("[dock-installers] this session of one-time installs leaves out the game's " +
                                 dropped.sorted().joined(separator: ", ") + " (MADEIRA_DOCK_INSTALL_LAYOUT=0 keeps them)")
                }
            } else {
                // No library entry (a BCD Dock start started from Settings): no entry lines
                // (MadeiraConfig.applyGame).
                _ = try? MadeiraConfig.applyGame(nil)
            }

            // Step 1: Allocate JIT pool (BRK suspends entire process)
            // 128 MB was enough for cube but Thumper exhausts it (more PE
            // copies + larger FEX block cache). Desktop mode holds the
            // session's aarch64 image set AND every child's x64 set AND the
            // FEX code buffers in ONE pool: Thumper-under-desktop hit 199MB
            // of image copies alone (2026-07-06), leaving the FEX tail carve
            // colliding with the head. 384 MB fits both plus slack; the pool
            // is dual-map + NO_FOOTPRINT so unwritten pages cost nothing.
            //
            // 2026-07-10 (Steam S3): 384 MB is VIRTUAL-exhausted by Steam's
            // pseudo-process fan-out — steam.exe + services + rpcss + cmd +
            // conhost + steamerrorreporter64 each copy their whole DLL set
            // (owner-keyed, no .text sharing yet) → 138 image copies hit
            // ~365 MB and the crash reporter's ntdll can't fit → the load
            // fails and execution BUS-faults on the un-committed image. Since
            // the pool is jetsam-exempt + demand-committed (unwritten pages
            // cost nothing), raising the VIRTUAL cap is a cheap, safe unblock.
            // 640 MB clears the current fan-out with headroom to reach the
            // ole32 delay-load (FEX riprel probe) and beyond. The real fix for
            // the PHYSICAL duplication is .text sharing (deferred project).
            //
            // 2026-07-10 pm (task #34 / CEF): 896 MB — libcef.dll's 212MB
            // pool copy EXHAUSTED 640 (bump 412MB + no contiguous 212MB →
            // libcef load degraded → init CHECK). Pure-x64 skip-copy was
            // trialed and reverted (broke x18-trampoline layout, ml68);
            // until skip-copy or .text sharing lands, buy headroom. Virtual
            // is jetsam-exempt; the copy itself is ~212MB real RSS when
            // written.
            // 2026-08-01 (ml364): 1152 MB — ml363 died at MSM depth on pool
            // EXHAUSTION (bump 858MB, freelist 0, tail-reserve 64MB) when
            // Chrome's in-proc GPU thread requested a doubled 32MB EC code
            // buffer; the fallback landed non-executable in the guest band and
            // FEX scribbled through a garbage CodeBuffer. NOTE the jetsam
            // ledger note above is STALE: the pool was never exempt and
            // arrives FULLY DIRTY from StikDebug's TXM blessing writes, so
            // this +256MB costs +256MB of the 4096MB budget up front. The
            // ml362/ml363 footprint work (peak 3804→3190) is what pays for
            // it. The real fix for both sides is still .text sharing.
            // 2026-08-01 (ml367): back to 896 MB. ml364 needed 1152 because the
            // shipped PE DLLs carried DWARF debug sections (llvm-mingw links
            // -Wl,-debug:dwarf) and the pool copies the ENTIRE image, so 42% of
            // every copy was debug info with no runtime purpose. Stripping them
            // (llvm-strip --strip-debug over the bundle) drops projected peak
            // pool use 894 -> ~653 MB, so 896 restores the ml364-equivalent
            // headroom (~243 MB) while returning 256 MB of footprint — the pool
            // is dirty from birth, so its SIZE is what costs, not its usage.
            // KEEP ios_usable_va_floor PAIRED: 896MB -> 0x7038000000.
            // 2026-08-02 (ml421): 1024 MB. ml420 (post-#69-fix, deepest run yet:
            // cycle 41) refilled the stripped 896 pool anyway — head 768MB of
            // copies + 176MB tail of EC code buffers collided; the doubled 32MB
            // GPU-thread buffer was refused and the ml361/ml363 ClearCache
            // wild-write returned (now also honestly REFUSED unix-side,
            // rev=ml421). +128MB is the depth lever that fits under jetsam:
            // ml420 peaked 3837 phys; 3837+128=3965 < 4096. Tight — if jetsam
            // returns, the durable fix is .text sharing, not more pool.
            // 2026-08-02 (ml423): BACK to 896. Jetsam DID return — ml422 died a
            // silent EXC_RESOURCE kill at 2.5min (peak 3904, log stops mid-line),
            // exactly the predicted cost of the +128MB dirty-at-birth pool.
            // ml421's honest EC_CODE refusal makes pool exhaustion GRACEFUL now
            // (ctor halving, worst case one thread's 0xdead fault) while jetsam
            // kills the whole app — 896 + graceful degradation strictly beats
            // 1024 + jetsam roulette. Durable fix remains .text sharing.
            // KEEP ios_usable_va_floor PAIRED: 896MB -> 0x7038000000.
            // 2026-08-03 (ml458): STAY at 896 — growth is closed for good.
            // jetsam killed 1024 twice (ml422 peak 3904) and the no-footprint
            // exemption is unreachable: all four (entry-flags, owner) variants
            // return kr=4, and the plain ones expose why — the named entry
            // covers 16KB of the 896MB object, i.e. the kernel wants an entry
            // naming the WHOLE object, which we can never build over memory
            // whose object StikDebug created. Pool stays dirty-from-birth and
            // jetsam-counted, so SIZE is the cost and 896 is the ceiling.
            // ⛔ ml457 re-trialed pure-x64 skip-copy (already dead per ml68
            // above) and it failed again for a different reason: x64 guest
            // RIPs ARE pool-copy aliases, so the copy is the execution
            // substrate — steam.exe died in seconds. Do not try a third time.
            // The remaining levers are USE-side: the 276MB of duplicate copies
            // (.text sharing) and the 214MB tail of EC code buffers.
            // ml668: RUNTIME-SELECTABLE. 896 stays the default and the only
            // value proven for Steam/CEF. 384 is the direct-game experiment:
            // the last good Book of the Dead run used ~139MB of head + ~48MB
            // of tail, so 384 leaves ~197MB of observed slack while returning
            // ~512MB of footprint -- and the pool is dirty from birth, so its
            // SIZE is the cost, not its usage. The VA floor is no longer a
            // hand-paired constant (ml668 derives it from the pool actually
            // allocated), so changing this is now a one-line change.
            // Override: bcd.cfg pool = a bare number of MB, so it can be
            // swapped between runs without a rebuild, and removing the line
            // reverts to the proven default. Clamped to sane values --
            // a typo here would otherwise move the VA floor with it.
            // Madeira Dock: a Dock launch may opt in to a compact pool (only that
            // launch; off by default). bcd.cfg pool below still wins.
            let dockLaunch = MadeiraDock.takeLaunchRequest()
            // A Dock session publishes no fixed Steam game identity to its guests
            // (WineProcessBridge.m): Valve's client runs in the host and gives each
            // game its own. Every other launch clears the flag and is unchanged.
            // env.MADEIRA_DOCK_CLEAR_STEAM_ID = 0 keeps the fixed identity (A/B).
            if dockLaunch.dock && SteamSignIn.flag("MADEIRA_DOCK_CLEAR_STEAM_ID", default: true) {
                setenv("MADEIRA_DOCK_SESSION", "1", 1)
            } else {
                unsetenv("MADEIRA_DOCK_SESSION")
            }
            // A Dock session runs Valve's client headless, with no Chromium, so
            // nothing claims the 8 GB V8 cage holdback (virtual_ios.c). Let ntdll
            // hand it to the allocator when the guest band runs out. bcd.cfg
            // env.MADEIRA_CAGE_RELEASE, exported later, wins.
            if dockLaunch.dock {
                setenv("MADEIRA_CAGE_RELEASE", "1", 1)
            } else {
                unsetenv("MADEIRA_CAGE_RELEASE")
            }
            var poolSizeMB = DockPerformancePolicy.sessionPoolMB(standard: 896, dock: dockLaunch.dock, compact: dockLaunch.compact)
            if poolSizeMB != 896 { logStore.log("[dock-pool] compact JIT pool \(poolSizeMB)MB for this Dock launch") }
            // madeira-bcd ios18-jit: env.MADEIRA_POOL_FROM_LIMIT (off: 896, no change) sizes it from the memory limit, never larger.
            poolSizeMB = min(poolSizeMB, StikJITHelper.launchPoolMB(standard: 896))
            // bcd.cfg pool: the JIT pool size in MB (256 to 1152) for every launch; wins over the size above.
            if let txt = MadeiraConfig.get("pool"),
               let mb = Int(txt.trimmingCharacters(in: .whitespacesAndNewlines)),
               mb >= 256, mb <= 1152 {
                poolSizeMB = mb
                logStore.log("JIT pool overridden to \(mb)MB via bcd.cfg pool")
            }
            // ml694: W^X A/B switch. bcd.cfg wx = 0
            // disables page demotion for the SAME binary, so the on/off
            // comparison needs one rebuild, not two. The previous gate read
            // container paths that can never exist, so it silently forced
            // ENABLED and no A/B was actually possible.
            if let txt = MadeiraConfig.get("wx") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                setenv("MADEIRA_WX", v, 1)
                logStore.log("W^X override: MADEIRA_WX=\(v) via bcd.cfg wx")
            }

            // ml727: wine-mono backpatcher bridge A/B. bcd.cfg mono-bridge = 1
            // sets MADEIRA_WINEMONO_BRIDGE, which arms FEX's Mono code-patching
            // optimisation for wine-mono (recognised since ml712 but activation left
            // opt-in because the bridge reclassifies an XCHG from a true atomic exchange
            // into an alias-directed plain write).
            //
            // Worth arming here: the dominant fault site emits SWPAL, which is exactly
            // what FEX generates for a guest XCHG, and the patching XCHGs sit inside
            // libmono -- so the bridge's "RIP must lie inside Mono" test should pass.
            if let txt = MadeiraConfig.get("mono-bridge") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_WINEMONO_BRIDGE", v, 1)
                    logStore.log("Mono bridge: MADEIRA_WINEMONO_BRIDGE=\(v) via bcd.cfg mono-bridge")
                }
            }

            // ml716: syscall-frame context A/B. bcd.cfg ctx-frame = 1
            // makes ios_fill_thread_context() report a thread parked inside a syscall
            // using its saved Wine syscall frame (TEB+0x378) instead of the Mach-O
            // registers it happens to be executing. Off by default; native code reads
            // only the environment variable.
            if let txt = MadeiraConfig.get("ctx-frame") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_CTX_FRAME", v, 1)
                    logStore.log("Context source: MADEIRA_CTX_FRAME=\(v) via bcd.cfg ctx-frame")
                }
            }

            // ml744: DXMT options passthrough. bcd.cfg dxmt is copied
            // verbatim into DXMT_CONFIG, which the renderer's config parser reads as
            // inline "key=value" lines, so options can be tried without a rebuild.
            // d3d11.mipClampBC=N is the one that matters for memory: this GPU cannot
            // sample BC, so those textures are expanded to uncompressed and cost 2-8x
            // their shipped size.
            // DXMT splits DXMT_CONFIG on ";" only and a newline is not whitespace to
            // its line parser, so the options are joined with ";" (ml1095: "a=b;c=d"
            // on one line). A library game's own dxmt options come after bcd.cfg's.
            // ml1255: "#" pieces (comments) are dropped; DXMT skips them anyway, but
            // they would count against its length limit below.
            // (madeira-bcd, build 296: options joined with "\n" became one option and
            // left Ghost of Tsushima's DXGI with Device 0.)
            var dxmtOptions: [String] = []
            for (source, txt) in [("bcd.cfg dxmt", MadeiraConfig.get("dxmt")), ("the game's config", MadeiraConfig.gameValue("dxmt"))] {
                let parts = (txt ?? "").split(whereSeparator: { $0 == ";" || $0.isNewline })
                    .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty && !$0.hasPrefix("#") }
                if !parts.isEmpty {
                    dxmtOptions += parts
                    logStore.log("DXMT config: \(parts.joined(separator: ";")) via \(source) (\(parts.count) option\(parts.count == 1 ? "" : "s"))")
                }
            }

            // MetalFX spatial upscaling (metalfx-upscale; Game details › MetalFX
            // upscaling writes it to the game's own lines). The D3D12 runtime reads
            // the key itself; D3D11 games get DXMT's MetalFX swapchain at the same
            // factor (DXMT takes 1 to 2).
            if let txt = MadeiraConfig.gameValue("metalfx-upscale") ?? MadeiraConfig.get("metalfx-upscale"),
               let factor = Double(txt), factor >= 1.1 {
                setenv("DXMT_METALFX_SPATIAL_SWAPCHAIN", "1", 1)
                dxmtOptions.append("d3d11.metalSpatialUpscaleFactor=\(min(factor, 2))")
                logStore.log("MetalFX upscaling: \(txt)x via metalfx-upscale")
            } else {
                unsetenv("DXMT_METALFX_SPATIAL_SWAPCHAIN")
            }
            // A library entry's "Report an NVIDIA GPU" (LibraryEntry.applyEnvironment):
            // DXGI's device id is the GeForce RTX 3060 win32u registers as the
            // display adapter (sysparams_ios.c), so every API names one GPU.
            if profile?.reportNVIDIA == true {
                dxmtOptions.append("dxgi.customDeviceId=2544")
                logStore.log("DXMT config: dxgi.customDeviceId=2544 via Report an NVIDIA GPU")
            }
            // madeira-bcd: the game's options from BCDLaunch.applyExtras (LibraryPrefs'
            // NVIDIA device id, MetalFX's D3D11 factor up to 3x from the game's own file),
            // last, as before the merge. An option whose key is already set above is
            // replaced, not repeated: DXMT reads at most 259 characters.
            if let c = getenv("MADEIRA_DXMT_EXTRA") {
                let extra = String(cString: c).split(whereSeparator: { $0 == ";" || $0.isNewline })
                    .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty && !$0.hasPrefix("#") }
                for option in extra {
                    let key = option.split(separator: "=", maxSplits: 1).first.map(String.init) ?? option
                    dxmtOptions.removeAll { ($0.split(separator: "=", maxSplits: 1).first.map(String.init) ?? $0) == key }
                    dxmtOptions.append(option)
                }
                if !extra.isEmpty { logStore.log("DXMT config: \(extra.joined(separator: ";")) via the game's settings") }
            }
            // Unset otherwise, so a previous session's options in this app process do not apply.
            if !dxmtOptions.isEmpty { setenv("DXMT_CONFIG", dxmtOptions.joined(separator: ";"), 1) } else { unsetenv("DXMT_CONFIG") }
            // ml1255: DXMT reads the variable into a MAX_PATH buffer (util_env.cpp
            // getEnvVar); from a longer value it gets nothing, and every option is lost.
            let dxmtLength = dxmtOptions.joined(separator: ";").utf16.count
            if dxmtLength > 259 {
                logStore.log("DXMT config is \(dxmtLength) characters; DXMT reads at most 259 and drops ALL of it -- shorten the dxmt lines of bcd.cfg and the game's config", level: .error)
            }

            // D3D9 frontend for 32-bit programs. The i386 d3d9.dll is DXMT's thin
            // shim; unset (the default) or "emulated", it forwards every export to
            // d3d9-emulated.dll, DXMT's D3D9 frontend built for i386 and translated
            // by FEX like the program. "native" makes the shim bind its unix side
            // and run the frontend as native ARM64 code in libdxmt_combined.a.
            // Only the i386 shim reads MADEIRA_D3D9; 64-bit programs are unaffected.
            if let txt = MadeiraConfig.get("d3d9") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_D3D9", v, 1)
                    logStore.log("D3D9 frontend: MADEIRA_D3D9=\(v) via bcd.cfg d3d9")
                }
            }

            // ml734: Theorafile call tracer. bcd.cfg tf-trace = 1
            // redirects libtheorafile's tf_* exports through wrappers in
            // tftrace-x64.dll that call the original and report the RETURN
            // value. The intro decodes and plays, the stream reaches a clean
            // end of file, the decoder stops reading -- and the game never
            // leaves VideoContext. File EOF is not decoder EOS, and a call
            // count cannot tell "tf_eos returns false forever" from "it returns
            // true and the managed side ignores it". Only the return value can.
            if let txt = MadeiraConfig.get("tf-trace") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_TF_TRACE", v, 1)
                    logStore.log("Theorafile tracer: MADEIRA_TF_TRACE=\(v) via bcd.cfg tf-trace")
                }
            }

            // ml731: Windows shared-data clock A/B. bcd.cfg usd-time = 1
            // makes wineserver update KUSER_SHARED_DATA's SystemTime, InterruptTime
            // and TickCount again. Without it those stay frozen at their init values,
            // so GetTickCount/Environment.TickCount/DateTime.UtcNow never advance and
            // every time-gated transition in a managed game waits forever while the
            // renderer keeps drawing. Opt-in only because the old code claimed the
            // write faulted; this should become unconditional once proven.
            if let txt = MadeiraConfig.get("usd-time") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_USD_TIME", v, 1)
                    logStore.log("Shared-data clock: MADEIRA_USD_TIME=\(v) via bcd.cfg usd-time")
                }
            }

            // ml730: REAL thread suspension A/B. bcd.cfg real-suspend = 1
            // makes a Wine suspend actually stop the Mach thread and keep it stopped
            // until the matching resume, instead of only snapshotting its registers
            // and bumping a counter while the target keeps running.
            //
            // Off by default and reversible on purpose: wineserver is a thread inside
            // this same Mach process and shares the allocator with the guest, so truly
            // freezing a thread that holds the malloc lock or FEX's CodeInvalidationMutex
            // can deadlock whoever suspended it. Windows apps tolerate preemptive suspend
            // because the suspender does not share their heap; here it does.
            if let txt = MadeiraConfig.get("real-suspend") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_REAL_SUSPEND", v, 1)
                    logStore.log("Thread suspension: MADEIRA_REAL_SUSPEND=\(v) via bcd.cfg real-suspend")
                }
            }

            // ml713: Mono suspend-policy A/B. bcd.cfg mono-suspend =
            // preemptive (or coop/hybrid) sets MONO_THREADS_SUSPEND
            // for wine-mono, so the comparison needs no rebuild.
            //
            // EXPERIMENT, NOT A FIX, and deliberately not a default. Marvel Cosmic
            // Invasion deadlocks with one thread owning a Mono critical section while
            // looping on mono_lls_find/usleep waiting for a thread-info record, and six
            // threads queued behind that section. Preemptive suspend would sidestep the
            // handshake -- but it needs SuspendThread + GetThreadContext to yield a
            // coherent x86-64 context for a guest thread stopped anywhere, including
            // mid-JIT-block, and that path has never been exercised under FEX. It may
            // trade a deadlock for a worse failure. If it does get in-game, that is NOT
            // evidence for any particular theory of the deadlock.
            if let txt = MadeiraConfig.get("mono-suspend") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MONO_THREADS_SUSPEND", v, 1)
                    logStore.log("Mono suspend policy: MONO_THREADS_SUSPEND=\(v) via bcd.cfg mono-suspend")
                }
            }

            winios_phase("pool-alloc-begin")
            logStore.log("Allocating \(poolSizeMB)MB JIT pool (BRK will suspend process)...")
            let t0 = CFAbsoluteTimeGetCurrent()
            let pool = StikJITHelper.allocatePool(poolSize: poolSizeMB * 1024 * 1024)
            let elapsed = CFAbsoluteTimeGetCurrent() - t0
            winios_phase("pool-ready")
            logStore.log("BRK suspension lasted \(String(format: "%.2f", elapsed))s")

            // Arena carver self-test. bcd.cfg arena-test holds
            // "churn:N", "ramp:N" or "random:N". Deliberately a SEPARATE key
            // from arena: a test that only runs when the feature is
            // enabled cannot be used to decide whether to enable it.
            if let txt = MadeiraConfig.get("arena-test") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_ARENA_TEST", v, 1)
                    logStore.log("arena carver self-test: \(v)", level: .success)
                }
            }

            // ml787: deterministic call-ret allocation failure injection.
            // bcd.cfg fexfail holds "reserve:N" or "commit:N".
            // The containment path it exercises only occurs naturally when a
            // title exhausts the emulator's address band, and only the reserve
            // half occurs at all -- an untested cleanup path is an assumption,
            // so this makes both reproducible on demand. Without the key
            // nothing is injected.
            if let txt = MadeiraConfig.get("fexfail") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                if !v.isEmpty {
                    setenv("MADEIRA_FEX_FAIL_CALLRET", v, 1)
                    logStore.log("call-ret failure injection: \(v) via bcd.cfg fexfail", level: .error)
                }
            }

            // ml762: remote Metal backend. bcd.cfg remote holds
            // "<host-ip> <token>" and routes winemetal to a Metal daemon on that
            // host instead of the local device. The mode is decided ONCE per
            // process: flipping it later would leave handles from two address
            // spaces alive at the same time, which is precisely what the handle
            // tag exists to make impossible.
            if let txt = MadeiraConfig.get("remote") {
                let parts = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                                .split(separator: " ", maxSplits: 1).map(String.init)
                if parts.count == 2 {
                    setenv("DXMT_REMOTE_METAL", parts[0], 1)
                    setenv("RMETAL_TOKEN", parts[1], 1)
                    logStore.log("remote Metal: host=\(parts[0]) via bcd.cfg remote", level: .success)
                } else if !parts.isEmpty {
                    logStore.log("bcd.cfg remote needs '<host-ip> <token>'", level: .error)
                }
            }

            // madeira-d3d12: M1 shader-converter gate, in-app.
            // bcd.cfg d3d12 = 1 runs the same canary that
            // passes standalone on macOS and over SSH on this device, but from
            // inside Madeira -- which is the only way to test bundling, signing
            // and dlopen under the app's own sandbox. Results go to the log.
            // Reports its decision either way. A gate that stays silent when it
            // declines to run is indistinguishable from one that never executed,
            // which cost a device run to work out.
            if let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first {
                let raw = MadeiraConfig.get("d3d12")   /* ml1095 */
                let val = raw ?? ""
                if val == "1" {
                    let dir = Bundle.main.bundlePath + "/d3d12"
                    let dylib = dir + "/libmetalirconverter.dylib"
                    let haveDylib = FileManager.default.fileExists(atPath: dylib)
                    let transcript = d.appendingPathComponent("bcd-d3d12-canary.log").path
                    logStore.log("madeira-d3d12: running the M1 canary in-app (dylib present: \(haveDylib))", level: .info)
                    let fails = madeira_d3d12_canary_run_log(
                        dir, dylib, nil, transcript,
                        (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "?")
                    if fails == 0 {
                        logStore.log("madeira-d3d12: M1 canary PASSED in-app (transcript: bcd-d3d12-canary.log)", level: .success)
                    } else {
                        logStore.log("madeira-d3d12: M1 canary FAILED (\(fails) checks)", level: .error)
                    }
                } else {
                    logStore.log("madeira-d3d12: gate off (bcd.cfg d3d12 \(raw == nil ? "unset" : "= '\(val)'"))", level: .debug)
                }
            }

            // ml821: coalesced remote messages. bcd.cfg remote-batch = 1
            // makes the pre-submission flush send many buffer ranges per
            // round trip and drains autorelease pools in one call. It is OPT-IN
            // because the measurement it is meant to improve needs a matched
            // baseline: without the key the process behaves exactly as
            // ml820 did. Round-trip COUNT is the cost being attacked -- one
            // gameplay frame spent 369 ms of 524 ms on 2,197 serialized calls.
            if let txt = MadeiraConfig.get("remote-batch"),
               txt.trimmingCharacters(in: .whitespacesAndNewlines) == "1" {
                setenv("DXMT_REMOTE_BATCH", "1", 1)
                logStore.log("remote Metal: message coalescing ON via bcd.cfg remote-batch", level: .success)
            }

            // ml761: top-level API census. bcd.cfg apicensus = 1
            // counts every call across the PE->unix winemetal boundary and
            // classifies each as producer, consumer, lifetime, query, sync,
            // presentation or bulk-memory. Needed because a packed command
            // batch carries GUEST handles -- raw pointer casts, meaningless on
            // another machine -- so every handle producer and consumer has to
            // be redirected together.
            if let txt = MadeiraConfig.get("apicensus") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                setenv("DXMT_API_CENSUS", v, 1)
                logStore.log("API census: DXMT_API_CENSUS=\(v) via bcd.cfg apicensus")
            }

            // ml760: shadow-pack mode. bcd.cfg shadow = 1 packs
            // and validates every real render batch into the remote wire format,
            // then discards it and renders locally as normal. Exercises the
            // packer against live traffic where being wrong costs nothing. The
            // check that matters is packed counts equalling census counts: a
            // silently skipped command would otherwise surface as a subtly wrong
            // frame on another machine.
            if let txt = MadeiraConfig.get("shadow") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                setenv("DXMT_SHADOW_PACK", v, 1)
                logStore.log("shadow pack: DXMT_SHADOW_PACK=\(v) via bcd.cfg shadow")
            }

            // ml758: wmtcmd census. bcd.cfg census = 1 counts
            // which of the 59 render/compute/blit command types a workload
            // actually emits, and how large their sidecar data gets. Needed
            // before serialising wmtcmd_* for the remote Metal transport --
            // building a schema for all 59 on speculation would be weeks of
            // work for commands no title may ever issue.
            if let txt = MadeiraConfig.get("census") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                setenv("DXMT_CMD_CENSUS", v, 1)
                logStore.log("wmtcmd census: DXMT_CMD_CENSUS=\(v) via bcd.cfg census")
            }

            // ml757: FEX arena placeholder. bcd.cfg arena = 1
            // makes Wine reserve FEX's host arena before any PE loads. OFF by
            // default: FEX still selects its own band, and on hardware that
            // band IS the reservation, so enabling it starves FEX and kills
            // x64 before the first window. Proven correct on the research VM
            // (8GB held, 0 of 123 guest images inside it) -- turn on only once
            // FEX consumes WINE_IOS_FEX_ARENA_BASE/SIZE instead of choosing.
            if let txt = MadeiraConfig.get("arena") {
                let v = txt.trimmingCharacters(in: .whitespacesAndNewlines)
                setenv("MADEIRA_FEX_ARENA", v, 1)
                logStore.log("FEX arena placeholder: MADEIRA_FEX_ARENA=\(v) via bcd.cfg arena")
            }

            // ml748: W^X A/B probe. bcd.cfg wxprobe = 1 runs it.
            // Loading xtajit64.dll faults writing its .rdata on the jailbroken
            // research VM and not on this phone, with CS_DEBUGGED live in both,
            // so attachment is not the variable. Either the VM is stricter than
            // real hardware (its patchVmMapProtect() was removed, and that is
            // what forces W to stick on file-backed pages), or hardware masks a
            // genuine bug and the loader must stop holding RWX over image pages.
            // Reasoning cannot separate those; the SAME build reporting on both
            // machines can. Runs here because it needs the real container, the
            // real sandbox and a live cs_wx_enabled map -- a standalone binary
            // over SSH already answered this wrongly once.
            if let txt = MadeiraConfig.get("wxprobe"),
               txt.trimmingCharacters(in: .whitespacesAndNewlines) == "1" {
                logStore.log("W^X probe armed via bcd.cfg wxprobe", level: .success)
                jit_wx_probe()
            }

            if let pool = pool {
                logStore.log("JIT pool: RX=\(String(format: "%p", Int(bitPattern: pool.rx))), RW=\(String(format: "%p", Int(bitPattern: pool.rw))), size=\(pool.size / 1024 / 1024)MB", level: .success)
                setenv("WINE_IOS_JIT_RX", String(format: "%lx", Int(bitPattern: pool.rx)), 1)
                setenv("WINE_IOS_JIT_RW", String(format: "%lx", Int(bitPattern: pool.rw)), 1)
                setenv("WINE_IOS_JIT_SIZE", String(format: "%lx", pool.size), 1)
                // madeira-bcd split pool (pool-split = 1): the pool offsets between its two
                // debugger regions, which ntdll never hands out (StikJITHelper.poolHole).
                if let hole = StikJITHelper.poolHole {
                    setenv("WINE_IOS_JIT_HOLE", String(format: "%lx:%lx", hole.off, hole.end), 1)
                } else {
                    unsetenv("WINE_IOS_JIT_HOLE")
                }
                // madeira-bcd pool-mid (pool-mid = 1): the second hole, when a free run
                // between the split regions became a third region (StikJITHelper.poolHole2).
                if let hole2 = StikJITHelper.poolHole2 {
                    setenv("WINE_IOS_JIT_HOLE2", String(format: "%lx:%lx", hole2.off, hole2.end), 1)
                } else {
                    unsetenv("WINE_IOS_JIT_HOLE2")
                }
                // madeira-bcd pool-low (pool-low = 1): region C below the executable window,
                // RW alias at the pool's distance; ntdll carves FEX's code buffers from it
                // first (StikJITHelper.poolLow).
                if let low = StikJITHelper.poolLow {
                    setenv("WINE_IOS_JIT_TAIL_REGION", String(format: "%lx:%lx", low.rx, low.size), 1)
                    logStore.log(String(format: "[pool-low] WINE_IOS_JIT_TAIL_REGION=%lx:%lx (%ldMB for FEX code buffers)",
                                        low.rx, low.size, low.size >> 20), level: .success)
                } else {
                    unsetenv("WINE_IOS_JIT_TAIL_REGION")
                }
                // ml1330: the pool is allocated once per app run and reused, so this is
                // the actual size, which the unix side records if a session runs it dry.
                setenv("MADEIRA_POOL_MB", String(pool.size / 1024 / 1024), 1)
            } else {
                // ml596: ABORT. "Continuing without it" produced ml595 — a run that
                // looked like an ARM64EC/optimizer regression but was only Wine
                // executing with no JIT pool, and it cost a diagnostic cycle plus a
                // wrong conclusion I wrote into the source. A run without the pool can
                // only manufacture misleading secondary crashes, so refuse to start one.
                logStore.log("JIT pool allocation FAILED — not starting Wine.", level: .error)
                // The reason allocatePool recorded (no debugger, placement, alias);
                // the lines above this one in the log carry the detail.
                let reason = StikJITHelper.poolFailure
                logStore.log("  " + (reason ?? "No reason was recorded; see the pool lines above."), level: .info)
                logStore.uiPaused = false
                // madeira-bcd ios18-jit: BCD now stays up after this (no exit(0)): end the session's quiet log mode
                // and its heartbeat as a finished session does.
                DispatchQueue.main.async { ws_log_quiet = 0; heartbeat.invalidate() }
                // A library session that never started returns to the library.
                let offerJIT = reason == StikJITHelper.noDebuggerMessage
                DispatchQueue.main.async { LibraryModel.shared.launchFailed(reason, offerJIT: offerJIT) }
                return
            }

            // Step 1b (ml524, #67): DETACH THE DEBUGGER NOW, while the VM map is small.
            //
            // Every ~54s whole-app stall coincides with StikDebug DEPARTING — clean
            // exit(0) and jetsam-kill alike (12:07:43 exit(0) -> GAP 54.0s at 12:07:49;
            // 12:13:58 cpulimit kill -> GAP 53.8s starting 64ms BEFORE the kill log).
            // Departure is the trigger; the manner of death is irrelevant. StikDebug
            // burns its 48s-CPU-per-60s budget in ~52s every single run, so an
            // UNCONTROLLED departure mid-game is guaranteed. Detaching here pays the
            // cost ONCE, at a moment we choose, before anything is on screen.
            //
            // Why it may also be CHEAPER here: on attach the kernel unnests the DYLD
            // shared region in OUR map ("increases system memory footprint until the
            // target exits"), so teardown plausibly scales with VM-map complexity —
            // and right now the map is a fraction of what it becomes under Steam
            // (91 threads / 2512MB). The [early-detach] timing below tests exactly that.
            //
            // Safe NOW and not before: ml522/ml523 made US the task-level Mach handler
            // for bad-access + bad-instruction + breakpoint, so the fault backstop that
            // used to require a live debugger (madeira-jit.js: "NEVER detach here ... every
            // later escalated fault parks its thread forever", the ml345 wedge) is ours.
            // And all executable memory already comes from the pool granted above —
            // virtual_ios.c copies every PE .text into it rather than mprotecting,
            // because iOS/TXM blocks mprotect(PROT_EXEC) outright.
            //
            // ORDERING MATTERS: our task-port claim installs at wine's first thread
            // setup, which is AFTER this point, so this BRK still reaches StikDebug.
            // Flip to false to A/B against the old attached-for-the-whole-run behaviour.
            let earlyDetach = true
            if earlyDetach, pool != nil {
                let dt0 = CFAbsoluteTimeGetCurrent()
                StikJITHelper.detachDebugger()
                let dms = (CFAbsoluteTimeGetCurrent() - dt0) * 1000.0
                logStore.log(String(format: "[early-detach] rev=ml524 took %.0f ms", dms),
                             level: dms > 5000 ? .error : .success)
            } else if !earlyDetach {
                logStore.log("[early-detach] rev=ml524 DISABLED — debugger stays attached all run")
            }

            winios_phase("detach-done")

            // The Madeira JIT shortcut turned Cellular Data off or connected LocalDevVPN
            // for this JIT (JITNetwork.swift). The pool is mapped and the debugger is
            // gone, so put them back now: running the shortcut leaves Madeira for a
            // moment, which is safe only before Wine starts drawing.
            JITNetworkShortcut.restoreBlocking()

            // Step 2: Start wineserver
            self.startWineserver()
            winios_phase("wineserver-up")

            // Step 3: Start Wine.

            // Wine starts as soon as the wineserver has finished starting up (its registry
            // is loaded), normally within tens of milliseconds, instead of after a fixed
            // 2 s pause. 0 restores the fixed pause.
            if MadeiraConfig.flag("MADEIRA_FAST_SERVER_START") {
                let waitStart = CFAbsoluteTimeGetCurrent()
                while wineserver_is_ready() == 0, wineserver_is_running() != 0, CFAbsoluteTimeGetCurrent() - waitStart < 2.0 {
                    Thread.sleep(forTimeInterval: 0.01)
                }
                logStore.log(String(format: "[launch] wineserver ready after %.0f ms", (CFAbsoluteTimeGetCurrent() - waitStart) * 1000))
            } else {
                Thread.sleep(forTimeInterval: 2.0)
            }
            winios_phase("wine-start")
            self.startWineProcess()

            // Step 4: Wait for Wine to finish instead of fixed timer
            // Poll wine_process_is_running() — it clears when __wine_main returns
            // For real games this never returns (message loop runs forever), so
            // the cap is what matters. After detach, the dual-mapped JIT pool
            // keeps existing blocks executable; only NEW BRK-based compiles
            // fail.
            //
            // 2026-05-13 first-frame: Thumper splash renders at ~50s but JIT is
            // STILL compiling new FMOD blocks 3M log lines later — audio init
            // is huge (~14k unique RIPs in fmod64.dll alone). Bumped to 300s
            // to let FMOD finish init before debugger detach; otherwise main
            // game loop never engages because Present is gated on audio ready.
            logStore.log("Waiting for Wine to finish PE loading...")
            // 2026-07-03 early detach: attached-mode runs the whole guest
            // ~2x slower (measured 1.2s → 0.74s per present at detach) and
            // on iOS 27 presented frames only reliably reach glass after
            // detach. Post-detach is safe now: trap-mode JIT writes go via
            // the Mach emulator (no debugger), pool pages are pre-executable
            // (dual map), page0 runs once on the first thread, and a
            // post-detach compile was observed working (real_compiles
            // 7093→7094, no faults). So: detach once the game is actually
            // presenting (present #2 = first post-splash frame) plus a
            // settle window, instead of waiting out the full 1200s cap.
            let maxWait = 1200.0  // hard safety cap (unchanged)
            // 2026-07-03 second iteration: detach on present #1 (splash shown)
            // instead of #2. The 3-minute splash-hold is the game loading —
            // running it detached should roughly halve it. Riskier than #2
            // (thousands of load-time compiles + worker-thread spawns happen
            // post-detach) but all known dependencies are covered: trap-mode
            // writes, pre-executable pool, page0 once-guard.
            let settleAfterFirstPresent = 20.0
            var presentingSince: CFAbsoluteTime? = nil
            let pollStart = CFAbsoluteTimeGetCurrent()
            var lastHeartbeat = CFAbsoluteTimeGetCurrent()
            while wine_process_is_running() != 0 {
                Thread.sleep(forTimeInterval: 0.25)
                let now = CFAbsoluteTimeGetCurrent()
                // Diagnostic heartbeat: 2026-07-03's detach-at-#1 run never
                // triggered despite presents visibly counting — log what this
                // loop actually observes so that can't happen silently again.
                if now - lastHeartbeat > 30 {
                    lastHeartbeat = now
                    logStore.log("detach-wait: presents=\(madeira_get_present_count()) running=\(wine_process_is_running()) elapsed=\(Int(now - pollStart))s")
                }
                // Task #25: the present heuristic is meaningless in desktop
                // mode — ANY child presenting (cube, a game window) trips it
                // mid-session, and later program launches still need the
                // attached-debugger facilities. Desktop sessions stay
                // attached until the desktop exits (or the safety cap).
                let isDesktopSession = getenv("MADEIRA_DESKTOP").map { $0.pointee == 49 } ?? false
                if !isDesktopSession {
                    if presentingSince == nil && madeira_get_present_count() >= 1 {
                        presentingSince = now
                        logStore.log("Game is presenting (#1, splash) — early detach in \(Int(settleAfterFirstPresent))s")
                    }
                    if let t = presentingSince, now - t > settleAfterFirstPresent {
                        logStore.log("Early detach: game presenting and settled", level: .success)
                        break
                    }
                }
                if now - pollStart > maxWait {
                    logStore.log("Wine still running after \(Int(maxWait))s, proceeding with detach", level: .error)
                    break
                }
            }
            let wineElapsed = CFAbsoluteTimeGetCurrent() - pollStart
            logStore.log("Wine finished after \(String(format: "%.1f", wineElapsed))s")

            // Step 5: Resume UI + os_log, give main thread time to recover before detach
            DispatchQueue.main.async {
                ws_log_quiet = 0
                logStore.uiPaused = false
            }
            Thread.sleep(forTimeInterval: 2.0)

            // Step 6: Detach debugger — main thread should have zero accumulated hang time
            logStore.log("Detaching debugger...")
            StikJITHelper.detachDebugger()

            DispatchQueue.main.async {
                heartbeat.invalidate()
                if wine_process_is_running() == 0 { LibraryModel.shared.launchFailed() }
            }
        }
    }

    /// Madeira Dock: hand the stored sign-in to the host once, point it at the game and
    /// start the normal session with explorer's virtual desktop running dockhost.exe.
    /// Nothing is handed over unless JIT is ready and no session runs.
    /// From the library (Settings › Steam › Madeira Dock) the start is a library
    /// session: the library's one-session-per-run rule applies first, failures
    /// show in the library, and the session gets the full-screen game view.
    /// `profile` is a Steam game's library entry (its Game details page): the
    /// session then takes that entry's display, performance and on-screen settings.
    private func startDock(_ game: DockGame, compactPool: Bool, profile: LibraryEntry? = nil) {
        // A second Play while a Dock start is on its way (a double tap) is ignored: its failure
        // would clean up the first start's sign-in transfer and give the account back while the
        // host signs in (Panzer Paladin 9BAP3Q: Dock code 30). DockLaunchGate.
        guard !DockStartScreen.launchGate.busy(now: ProcessInfo.processInfo.systemUptime) else {
            logStore.log("[madeira-dock] a start is already on its way: this Play is ignored app=\(game.id)")
            return
        }
        guard jitReadyForLaunch(entry: profile?.id,
                                then: { startDock(game, compactPool: compactPool, profile: profile) }) else { return }
        guard cloudClear(game.id, name: game.name, retry: { startDock(game, compactPool: compactPool, profile: profile) }) else { return }
        guard wine_process_is_running() == 0, wineserver_is_running() == 0, library.current == nil else {
            logStore.log("[madeira-dock] a session already ran in this app run; restart BCD first", level: .error)
            library.error = "Another session is still running."
            return
        }
        if LibraryModel.sessionsThisRun > 0, MadeiraConfig.flag("MADEIRA_ONE_SESSION_PER_RUN") {
            LogStore.shared.log("[session-once] Dock launch held: \(LibraryModel.sessionsThisRun) session(s) already ran in this app run")
            library.restartNotice = LibraryModel.restartMessage
            return
        }
        // This start is the one on its way until its session begins or it fails (below).
        DockStartScreen.launchGate.begin(now: ProcessInfo.processInfo.systemUptime)
        func fail(_ error: Error) {
            DockStartScreen.launchGate.end()
            MadeiraDock.cleanup()
            SteamOwnedLibrary.shared.dockEnded()
            MadeiraDockModel.shared.status = error.localizedDescription
            logStore.log("[madeira-dock] not started: \(error.localizedDescription)", level: .error)
            library.error = error.localizedDescription
        }
        do {
            try MadeiraDock.validate(game, drive: MadeiraDock.drive)
            try profile?.validate()
            guard SteamSignIn.isSignedIn else { throw DockError.message("BCD Dock needs a Steam sign-in. Sign in to Steam in BCD first.") }
        } catch { fail(error); return }
        // Only one sign-in of the account may be online: the app's own Steam connection
        // (library, playtime, downloads) logs off and its socket closes before the sign-in
        // is handed to Valve's client, and it stays off until the Dock session has ended
        // (SteamOwnedLibrary.prepareDock / dockEnded, SteamConnectionGate).
        Task { @MainActor in
            // Resolve the original config.launch key before closing the native connection.
            // A filtered launch array can start at 1 (or have gaps); its offset is not the key.
            // Without a choice (no configuration to be had, or no entry whose .exe is on disk:
            // a launcher started through a .bat, say) it is key 0, which every Dock start
            // used before; Dock stops at once if Steam names that entry missing.
            let options = await SteamOwnedLibrary.shared.launchOptions(appID: game.id)
            let installFolder = MadeiraDock.drive.appendingPathComponent(game.library + "/common/" + game.installDir)
            let chosen = options.flatMap { SteamDirectStart.choose($0, installFolder: installFolder)?.launchIndex }
            let launchOption = chosen ?? 0
            LogStore.shared.log("[madeira-dock] launch option \(launchOption)\(chosen == nil ? " (none chosen: the default)" : "")")
            await SteamOwnedLibrary.shared.prepareDock()
            do {
                // The launch state may have changed while the connection closed.
                guard StikJITHelper.ready, wine_process_is_running() == 0, wineserver_is_running() == 0,
                      library.current == nil else {
                    throw DockError.message("Something changed while the launch was getting ready. Enable JIT, then try again.")
                }
                guard let signIn = SteamSignIn.credentialsForDock() else {
                    throw DockError.message("BCD Dock needs a Steam sign-in. Sign in to Steam in BCD first.")
                }
                try MadeiraDock.writeHandoff(account: signIn.accountName, token: signIn.refreshToken, appID: game.id)
            } catch { fail(error); return }
            MadeiraDock.configure(game, launchOption: launchOption)
            // The game's one-time installs (its Steam install script) run first, in the same
            // session. No session runs yet, so the registry files can be read and written.
            // madeira-bcd: they run in a session of their own (DockInstallers.installsOnly), and
            // a Reset takes out what they installed; the other games share those installs.
            DockInstallers.prepare(game, drive: MadeiraDock.drive, prefix: MadeiraDock.prefix,
                                   others: MadeiraDock.games(drive: MadeiraDock.drive))
            // madeira-bcd (round 578): a start that has Valve's client prepare per-user executables (CEG)
            // finds Valve's own client service installed, as on Windows; the Dock host's own installer
            // step failed in 3 of 5 logged sessions (DockCEGService). No session runs yet.
            if !DockInstallers.installsOnly {
                DockCEGService.prepare(game, drive: MadeiraDock.drive, prefix: MadeiraDock.prefix)
                // madeira-bcd (2026-10-21): Valve's client added Counter-Strike 2's dependency on the
                // redistributables app 228980, which BCD never installs, and every later start waited
                // with error 17 (SteamSharedRedist). Only while 228980 is not fully installed.
                if SteamSignIn.flag("MADEIRA_DOCK_SHARED_REDIST", default: true),
                   let line = SteamSharedRedist.prepare(appID: game.id,
                                                        steamApps: MadeiraDock.drive.appendingPathComponent(game.library, isDirectory: true)) {
                    logStore.log(line)
                }
            }
            if DockInstallers.installsOnly {
                // No host runs in this session: nothing will take the sign-in transfer.
                MadeiraDock.cleanup()
                logStore.log("[madeira-dock] app \(game.id): this session runs only the one-time installs; the host starts after a restart")
                // The launch leaves out the game's address-space holds (DockInstallers.applyInstallSessionConfig).
                setenv("MADEIRA_DOCK_INSTALLS_SESSION", "1", 1)
            } else {
                unsetenv("MADEIRA_DOCK_INSTALLS_SESSION")
            }
            // Only a start that runs installers turns madsync off, for its own session
            // (build/madsync/madsync.c reads MADEIRA_MADSYNC_SESSION once, when the server starts).
            if DockInstallers.serverSync {
                setenv("MADEIRA_MADSYNC_SESSION", "0", 1)
                logStore.log("[dock-installers] this session runs one-time installs: madsync off for this session only (MADEIRA_MADSYNC_SESSION=0)")
            } else {
                unsetenv("MADEIRA_MADSYNC_SESSION")
            }
            // A Dock session starts 64-bit (explorer, then the host), but the programs it starts
            // later are often 32-bit: one-time installers and the 32-bit games Valve's client
            // launches. win32u decides once, when the session's first program initialises it,
            // whether the GDI handle table is a section that every 32-bit program can map inside
            // its own guest window (wine dlls/win32u/gdiobj.c, gdi_shared_use_section). Left to
            // that default, a Dock session's table is private host memory, and 32-bit gdi32
            // truncates its address and faults on its first GDI handle. The regular launch path
            // is unchanged; env.MADEIRA_GDI_SHARED_SECTION = 0 in bcd.cfg, exported after
            // this, keeps the default for Dock sessions too.
            setenv("MADEIRA_GDI_SHARED_SECTION", "1", 1)
            // ml1185: without desktop-size or a game's Resolution, this screen's shape
            // (ml1172's default: 1408x648 on a 19.5:9 iPhone, 1152x800 on an 11-inch
            // iPad), as every library entry defaults to; a fixed 1280x720 left bars
            // above and below on an iPad.
            var (width, height) = ResolutionChoices.defaultSize()
            if let txt = MadeiraConfig.get("desktop-size") {
                let p = txt.lowercased().split(separator: "x").compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
                if p.count == 2, p[0] >= 640, p[1] >= 360, p[0] <= 3840, p[1] <= 2160 { width = p[0]; height = p[1] }
            }
            // A Steam game's own Resolution (validated above) sizes its Dock desktop.
            if let size = profile?.resolution.split(separator: "x").compactMap({ Int($0) }), size.count == 2 {
                width = size[0]; height = size[1]
            }
            setenv("MADEIRA_EXE", "explorer.exe", 1)
            setenv("MADEIRA_ARGS", MadeiraDock.launchArguments(width: width, height: height, installers: DockInstallers.script,
                                                               host: !DockInstallers.installsOnly), 1)
            setenv("MADEIRA_DESKTOP", "1", 1)
            setenv("MADEIRA_SCREEN_W", String(width), 1)
            setenv("MADEIRA_SCREEN_H", String(height), 1)
            // The compositor and touch mapping read the published size
            // (winios_screen_size), which a program's display-mode change moves;
            // start this session from its own desktop size, not a previous one.
            winios_display_mode_changed(Int32(width), Int32(height))
            MadeiraDock.requestLaunch(compactPool: compactPool)
            if !DockInstallers.installsOnly {
                logStore.log("[madeira-dock] starting the host for app \(game.id); Valve's client authenticates and authorizes the launch")
            }
            MadeiraDockModel.shared.watchReport()
            if let profile { library.begin(profile, dock: game) }
            else { library.begin(.dockSession(title: game.name, width: width, height: height), remember: false, dock: game) }
            DockStartScreen.launchGate.end()   // the library session exists now: its own guards answer a later Play
            runWineFullSequence(profile: profile)
        }
    }

    private func startWineserver() {
        logStore.log("Starting wineserver...")

        let documentsPath = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let winePrefixPath = documentsPath.appendingPathComponent("wine").path

        logStore.log("Wine prefix: \(winePrefixPath)")

        let result = wineserver_start(winePrefixPath)
        if result == 0 {
            logStore.log("Wineserver thread launched successfully", level: .success)
        } else {
            logStore.log("Failed to start wineserver (error: \(result))", level: .error)
        }
    }

    private func startWineProcess() {
        logStore.log("Starting Wine process...")

        if wineserver_is_running() == 0 {
            logStore.log("Wineserver not running! Start it first.", level: .error)
            return
        }

        let documentsPath = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let winePrefixPath = documentsPath.appendingPathComponent("wine").path

        // Call synchronously — caller already waited for wineserver to be ready
        let result = wine_process_start(winePrefixPath)
        if result == 0 {
            logStore.log("Wine process thread launched", level: .success)
        } else {
            logStore.log("Failed to start Wine process (error: \(result))", level: .error)
        }
    }
}


// ============================================================================
// ml643 — LANDSCAPE TOUCH CONTROLS (pass 1: overlay, editor, persistence)
//
// This is the L2 layer from reference_swiftui_liquid_glass_ux_layers.md: glass
// elements composited over the game canvas, repositionable.
//
// 🔑 Everything here MUST live in its own UIWindow. MetalHostView is a raw
// window-level UIView above the whole SwiftUI hierarchy, so a control drawn in
// the normal content tree gets sliced off wherever it overlaps the game surface
// — and zIndex cannot fix that, because zIndex only orders siblings *within*
// SwiftUI.
// ============================================================================

/// What a control does when pressed. Codable with associated values so the
/// whole layout round-trips through JSON.
enum ControlAction: Codable, Equatable, Hashable {
    case none
    case key(Int32)          // Windows virtual-key code
    case mouseLeft
    case mouseRight
    case joystickWASD        // renders as a stick, posts W/A/S/D
    case joystickArrows      // renders as a stick, posts the arrow keys
    case keyboardToggle      // raises the iOS keyboard, as in portrait
    case pad(String)         // ml1930: touch gamepad action, preserving saved layout names.

    /// The four keys a stick drives, up/right/down/left. nil for non-sticks.
    var stickKeys: [Int32]? {
        switch self {
        case .joystickWASD:   return [0x57, 0x44, 0x53, 0x41]   // W D S A
        case .joystickArrows: return [0x26, 0x27, 0x28, 0x25]   // up right down left
        default: return nil
        }
    }
    /// SF Symbol drawn in the middle of a key stick's face. WASD and the arrow
    /// keys share one control (`.dirStick`) and would draw the same ring, so a
    /// user could not tell them apart without pressing one. nil for every other
    /// control, including the controller sticks, which carry their own label.
    var stickGlyph: String? {
        switch self {
        case .joystickWASD:   return "keyboard"
        case .joystickArrows: return "arrow.up.and.down.and.arrow.left.and.right"
        default:              return nil
        }
    }
    var isPad: Bool { if case .pad = self { return true }; return false }
    var padName: String? { if case .pad(let name) = self { return name }; return nil }
    var isPadStick: Bool { padName == "LS" || padName == "RS" }

    // ------------------------------------------------------------------
    // HOW A VIRTUAL CONTROLLER BUTTON IS DRAWN. The shape is part of the
    // button's identity: a wide rounded rectangle says "shoulder", a capsule
    // says "system", a coloured circle says "face", so a layout reads without
    // its labels. Saved layouts keep their names ("Menu", "View", "D↑"); only
    // the drawing changes.
    // ------------------------------------------------------------------
    enum PadFace { case round, wide, capsule, small }

    var padFace: PadFace? {
        guard let n = padName else { return nil }
        switch n {
        case "LB", "RB", "LT", "RT": return .wide
        case "Menu", "View":         return .capsule
        case "L3", "R3":             return .small
        default:                     return .round
        }
    }
    /// SF Symbol drawn instead of a label: the four D-pad directions.
    var padGlyph: String? {
        switch padName {
        case "D↑": return "arrowtriangle.up.fill"
        case "D↓": return "arrowtriangle.down.fill"
        case "D←": return "arrowtriangle.left.fill"
        case "D→": return "arrowtriangle.right.fill"
        default:   return nil
        }
    }
    /// The text drawn on the control. Menu/View are the XInput names the
    /// layout stores; the buttons themselves read START/SELECT.
    var padFaceLabel: String {
        switch padName {
        case "Menu": return "START"
        case "View": return "SELECT"
        default:     return label
        }
    }
    /// Drawn size, given the layout's diameter for a round button. Shoulders
    /// are wide, Start/Select are small pills, stick clicks are small circles.
    func controlSize(diameter d: CGFloat) -> CGSize {
        switch padFace {
        case .wide:    return CGSize(width: d * 1.6, height: d * 0.74)
        case .capsule: return CGSize(width: d * 1.2, height: d * 0.5)
        case .small:   return CGSize(width: d * 0.8, height: d * 0.8)
        default:       return CGSize(width: d, height: d)
        }
    }

    var label: String {
        switch self {
        case .none:            return "—"
        case .mouseLeft:       return "L"
        case .mouseRight:      return "R"
        case .keyboardToggle:  return "⌨"
        case .joystickWASD:    return "WASD"
        case .joystickArrows:  return "↕"
        case .pad(let n):      return n
        case .key(let vk):     return ControlAction.keyLabel(vk)
        }
    }

    /// Minimal for pass 1 — the full VK table arrives with the mapping panel.
    static func keyLabel(_ vk: Int32) -> String {
        switch vk {
        case 0x0D: return "⏎"
        case 0x20: return "␣"
        case 0x1B: return "Esc"
        case 0x09: return "⇥"
        case 0x10: return "⇧"
        case 0x11: return "Ctl"
        case 0x12: return "Alt"
        case 0x25: return "←"
        case 0x26: return "↑"
        case 0x27: return "→"
        case 0x28: return "↓"
        default:
            if vk >= 0x30, vk <= 0x5A, let u = UnicodeScalar(UInt32(vk)) {
                return String(Character(u))
            }
            return String(format: "%02X", vk)
        }
    }
}

/// One on-screen control.
///
/// Position is NORMALISED (0–1 of the screen), never points: the device gets
/// rotated and the logical surface can change size, and a layout stored in
/// absolute coordinates scatters the first time either happens.
struct TouchControl: Codable, Identifiable, Equatable {
    var id = UUID()
    var nx: Double = 0.5
    var ny: Double = 0.5
    var scale: Double = 1.0
    var action: ControlAction = .mouseLeft   // usable the moment it is created
    /// A physical controller input that also performs this control's key or
    /// mouse action when the game runs in keyboard-and-mouse controller mode
    /// (PadKeyboardMouse): "A", "RT", "D↑", ...; "LS"/"RS" for a key stick.
    /// Optional, so layouts saved before it existed still decode.
    var padBinding: String?
}

final class TouchControlsModel: ObservableObject {
    static let shared = TouchControlsModel()
    static let baseDiameter: CGFloat = 64

    @Published var controls: [TouchControl] = [] { didSet { save() } }
    @Published var visible = true               { didSet { save() } }
    /// Session panel "Opacity" for the play-mode controls (edit mode stays opaque).
    @Published var opacity: Double = 1.0        { didSet { save() } }
    @Published var editing = false {            // transient, never persisted
        didSet {
            // ml1970: an ended edit is written back to the custom layout it came from.
            if !oldValue && editing { editBaseline = controls }
            if oldValue && !editing { ControlPresetsModel.shared.editingEnded(baseline: editBaseline) }
        }
    }
    /// The Session panel card is open on this window: it takes every touch.
    @Published var sessionPanel = false         // transient
    @Published var selected: UUID?              // transient
    /// ml1970: the named layout (TouchControlPresets.swift) these controls were
    /// loaded from; nil for controls no layout holds.
    @Published var layoutID: String?            { didSet { save() } }
    /// ml1970: no controls file existed at launch, so the built-in controller
    /// layout may be applied once (ControlPresetsModel.applyDefaultIfNeeded).
    var needsDefaultLayout = false
    private var editBaseline: [TouchControl] = []

    /// One size for the whole layout (0.5...2), multiplying each control's own
    /// pinch `scale`. A library entry keeps its own (Control size) and sets it
    /// for its session; transient, so the developer interface stays at 1.
    @Published var sizeScale: Double = 1.0

    /// A control's drawn diameter. The view, the hit test and the mapping
    /// panel's placement all use it, so the touch region and the pixels agree.
    static func diameter(_ c: TouchControl) -> CGFloat {
        baseDiameter * CGFloat(c.scale) * CGFloat(shared.sizeScale)
    }
    /// The drawn frame: the diameter for round controls, the pad button's own
    /// shape otherwise (ControlAction.controlSize).
    static func size(_ c: TouchControl) -> CGSize { c.action.controlSize(diameter: diameter(c)) }

    private var loading = false
    private static var url: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("bcd-controls.json")
    }

    /// `opacity` and `layout` are optional so files written before them still decode.
    private struct Saved: Codable { var controls: [TouchControl]; var visible: Bool; var opacity: Double?; var layout: String? }

    private init() {
        loading = true
        if let d = try? Data(contentsOf: Self.url),
           let s = try? JSONDecoder().decode(Saved.self, from: d) {
            controls = s.controls
            visible  = s.visible
            opacity  = s.opacity ?? 1.0
            layoutID = s.layout
        }
        needsDefaultLayout = !FileManager.default.fileExists(atPath: Self.url.path)
        loading = false
    }

    private func save() {
        guard !loading else { return }
        guard let d = try? JSONEncoder().encode(Saved(controls: controls, visible: visible, opacity: opacity, layout: layoutID))
        else { return }
        try? d.write(to: Self.url, options: .atomic)
    }

    /// ml1990: touch controls will feed player 1 this session (visible
    /// controller mappings, or the built-in a user without controls is about to get with MADEIRA_CONTROLS_XBOX_DEFAULT=1).
    var offersControllerInput: Bool {
        visible && (controls.contains { $0.action.padName.map(TouchPadAction.supported) ?? false }
                    || ControlPresetsModel.shared.defaultPending)
    }

    func index(of id: UUID?) -> Int? {
        guard let id else { return nil }
        return controls.firstIndex { $0.id == id }
    }

    /// ml644: does this WINDOW point land on something interactive?
    ///
    /// Hit-test geometrically, never by walking the UIView hierarchy. SwiftUI
    /// does not back each Button with its own UIView — the entire overlay is one
    /// _UIHostingView and taps are routed by SwiftUI's own gesture machinery. So
    /// `super.hitTest` returns that same hosting view for EVERY point, buttons
    /// included, and ml643's "is it the root view?" test therefore rejected every
    /// touch in the window. Nothing responded, and edit mode — whose branch
    /// captured everything — could never be entered to mask it.
    /// `topBar: false` in a library session, where BCD's in-game layer replaces the bar.
    func hitsInteractive(_ p: CGPoint, in bounds: CGRect, topBar: Bool = true) -> Bool {
        // Top bar: three 44pt buttons 10pt apart in play mode (controls, edit,
        // session: madeira-bcd), centred, 10pt down, plus the layout menu while
        // touch controls are on (ml1970), plus the keyboard button.
        // Padded generously; a few points of slop costs nothing and a missed tap
        // costs a build.
        let buttons: CGFloat = (TouchControlsOverlay.showsLayoutMenu(self) ? 4 : 3) + 1
        let barW: CGFloat = buttons * 44 + (buttons - 1) * 10
        if topBar, CGRect(x: bounds.midX - barW / 2 - 10, y: 0,
                          width: barW + 20, height: 68).contains(p) { return true }
        guard visible else { return false }
        for c in controls {
            let r = Self.diameter(c) / 2
            let cx = CGFloat(c.nx) * bounds.width
            let cy = CGFloat(c.ny) * bounds.height
            if hypot(p.x - cx, p.y - cy) <= r { return true }
        }
        return false
    }
}

/// Click-through EXCEPT where a control actually is.
///
/// A window that only draws would return nil unconditionally. This one has to
/// take input, so it discriminates: a hit that
/// lands on the hosting root view means empty space, and empty space belongs to
/// the game underneath — mouse-look must keep working between the buttons.
final class ControlsWindow: UIWindow {
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        let m = TouchControlsModel.shared
        // Edit mode owns the whole screen: drags and the scale pinch must not
        // leak through and swing the camera while you are arranging buttons.
        if m.editing || m.sessionPanel { return super.hitTest(point, with: event) }
        // ml1970: the layout menu and its dialogs are UIKit presentations
        // outside the hosting view (an alert's dimming view covers the screen).
        // While one is up it takes the touches it covers; otherwise its buttons
        // would be dead wherever they are not over the top bar or a control.
        if ControlPresetsModel.enabled, let root = rootViewController?.view,
           let hit = super.hitTest(point, with: event), hit !== self, !hit.isDescendant(of: root) { return hit }
        let library = LibraryModel.shared
        // madeira-bcd (2026-10-18): while the program holds the mouse, the
        // pointer's hover, clicks and scrolling go to the game view, never to the
        // buttons here: the pointer showed over a side button and the program
        // lost the mouse there ("the iPad mouse pops out at the sides").
        // Fingers, the open menu and the starting screen are unchanged; Ctrl+Alt+P
        // gives the pointer back (HardwareInput.pointerHeldByProgram).
        if HardwareInput.isPointerEvent(event), HardwareInput.shared.pointerHeldByProgram,
           !(library.current != nil && (library.menu || library.launching)) { return nil }
        // A library session (Library.swift), in either orientation: its in-game
        // menu and starting screen take every touch; otherwise only its menu
        // button, its performance overlay and the touch controls do.
        if library.current != nil {
            if library.menu || library.launching || library.menuButtonRect.contains(point) ||
                (library.performance && library.performanceRect.contains(point)) {
                return super.hitTest(point, with: event)
            }
            guard m.hitsInteractive(point, in: bounds, topBar: false) else { return nil }
            return super.hitTest(point, with: event)
        }
        // Portrait draws nothing here, so it must consume nothing.
        guard bounds.width > bounds.height else { return nil }
        guard m.hitsInteractive(point, in: bounds) else { return nil }
        return super.hitTest(point, with: event)
    }
}

enum TouchControlsHost {
    private static var window: ControlsWindow?

    static func attach() {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive })
                        ?? scenes.first else { return }
        if window == nil {
            // ml644: orientationDidChangeNotification is NOT posted unless
            // generation has been switched on, so without this the overlay would
            // keep a portrait-sized frame after the first rotation.
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
            let w = ControlsWindow(windowScene: scene)
            // Above the joystick pad's +100. A higher windowLevel is the only
            // ordering nothing inside the app window can undo.
            w.windowLevel = .normal + 101
            w.backgroundColor = .clear
            w.isHidden = false        // deliberately never made key
            let host = UIHostingController(rootView: TouchControlsOverlay())
            host.view.backgroundColor = .clear
            w.rootViewController = host
            window = w
        }
        window?.frame = scene.coordinateSpace.bounds
        fputs("[controls] ml644 overlay attached frame=\(window?.frame ?? .zero) " +
              "controls=\(TouchControlsModel.shared.controls.count)\n", stderr)
    }
}

struct TouchControlsOverlay: View {
    @ObservedObject private var m = TouchControlsModel.shared
    @ObservedObject private var library = LibraryModel.shared
    @State private var pinchBase: Double?

    var body: some View {
        GeometryReader { geo in
            // Landscape only; portrait keeps the existing key row and joystick.
            // A library session is full screen in either orientation, and its
            // HUD (menu button, starting screen, in-game menu) replaces the top
            // bar; the controls hide while its menu or starting screen is up.
            let session = library.current != nil
            let landscape = geo.size.width > geo.size.height || session
            ZStack(alignment: .top) {
                if landscape {
                    if (m.visible || m.editing) && !library.blocksGameplayTouch {
                        controls(geo.size, session: session)
                            .opacity(m.editing ? 1.0 : m.opacity)
                    }
                    if session && !m.editing { BCDSessionLiveLayer() } else { topBar }   // BCD's in-game layer (BCDSessionLive.swift)
                    // BCD (2026-10-18): the selected control's panel is part of BCD's editor, in `topBar`.
                    if m.sessionPanel {
                        BCDDeskSessionMenu(onClose: { m.sessionPanel = false })   // BCD's in-game menu (BCDSessionLive.swift)
                    }
                }
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .top)
            .contentShape(Rectangle())
            .gesture(scalePinch, including: m.editing ? .all : .subviews)
            .onAppear { applyDefaultLayout(geo); configureGamepad(landscape: landscape) }
            .onChange(of: geo.size) { _, _ in applyDefaultLayout(geo); configureGamepad(landscape: landscape) }
            .onChange(of: m.controls) { _, _ in configureGamepad(landscape: landscape) }
            .onChange(of: m.visible) { _, _ in configureGamepad(landscape: landscape) }
            .onChange(of: m.editing) { _, _ in configureGamepad(landscape: landscape) }
            .onChange(of: library.blocksGameplayTouch) { _, _ in configureGamepad(landscape: landscape) }
            .onDisappear { GamepadInput.shared.configureTouch(controls: []); TouchMouseGate.padOverlay = false }
        }
        .ignoresSafeArea()
    }

    /// BCD's controls (BCDTouchLive.swift).
    private func controls(_ screen: CGSize, session: Bool) -> some View { BCDTouchControlsLive(screen: screen, session: session) }

    private func configureGamepad(landscape: Bool) {
        let ids = landscape && m.visible
            ? m.controls.filter { $0.action.padName.map(TouchPadAction.supported) ?? false }.map(\.id) : []
        // Menus and the control editor keep the pad connected but take no input (#204);
        // a touch that misses the controls is no mouse only while they do (#150).
        let accepting = !m.editing && !library.blocksGameplayTouch
        GamepadInput.shared.configureTouch(controls: Set(ids), acceptingInput: accepting)
        TouchMouseGate.padOverlay = accepting && !ids.isEmpty
        // The `[touch] layout=... kind=... keys-path=... pad=...` line: what the touch layer does with the layout
        // on screen, while a game runs (a library session or a program Wine runs by itself).
        if library.current != nil || wine_process_is_running() != 0 {
            ControlPresetsModel.shared.logPlan(shown: landscape && m.visible, accepting: accepting)
        }
    }

    /// ml1970: with MADEIRA_CONTROLS_XBOX_DEFAULT=1, a user with no controls file gets the built-in controller
    /// layout on the first landscape overlay, laid out for this screen (never over an existing controls file).
    private func applyDefaultLayout(_ geo: GeometryProxy) {
        guard m.needsDefaultLayout, geo.size.width > geo.size.height else { return }
        let i = geo.safeAreaInsets
        ControlPresetsModel.shared.applyDefaultIfNeeded(screen: ControlPresetScreen(
            width: Double(geo.size.width), height: Double(geo.size.height),
            left: Double(i.leading), right: Double(i.trailing),
            top: Double(i.top), bottom: Double(i.bottom)))
    }

    /// ml1970: the layout menu, offered only while touch controls are shown.
    static func showsLayoutMenu(_ m: TouchControlsModel) -> Bool {
        ControlPresetsModel.enabled && m.visible && !m.editing
    }

    /// BCD (2026-10-18): BCD's bar and editor (BCDTouchLive.swift).
    private var topBar: some View { BCDTouchChromeLive() }

    /// Pinch anywhere scales the SELECTED control. With nothing selected it does
    /// nothing rather than guessing which one you meant.
    private var scalePinch: some Gesture {
        MagnificationGesture()
            .onChanged { v in
                guard m.editing, let i = m.index(of: m.selected) else { return }
                if pinchBase == nil { pinchBase = m.controls[i].scale }
                m.controls[i].scale = min(max((pinchBase ?? 1) * Double(v), 0.5), 3.0)
            }
            .onEnded { _ in pinchBase = nil }
    }
}

extension ControlAction {
    /// Xbox face colours. Everything else is uncoloured: a wash of tint on every
    /// button would make the four that mean something unreadable.
    var padTint: Color? {
        switch padName {
        case "A": return Color(red: 0.36, green: 0.76, blue: 0.30)
        case "B": return Color(red: 0.88, green: 0.28, blue: 0.24)
        case "X": return Color(red: 0.24, green: 0.53, blue: 0.92)
        case "Y": return Color(red: 0.96, green: 0.76, blue: 0.16)
        default:  return nil
        }
    }
}

/// madeira-bcd: the library's Desktop gets the section-backed GDI handle table (owner, 2026-10-21).
/// The Desktop is where programs are installed and started by hand, and those are often 32-bit
/// (Battle.net-Setup.exe, build 579). Its session starts 64-bit (explorer), and win32u decides once,
/// when the first program initialises it, whether the GDI handle table is a section a 32-bit program
/// can map inside its own guest window (wine dlls/win32u/gdiobj.c, gdi_shared_use_section). Left to
/// that default the table is private host memory, and 32-bit gdi32 truncates its address and faults
/// on its first GDI handle. The Dock asks for the section the same way (startDock). Only the
/// library's Desktop: a game started "In a Wine desktop" keeps the default.
/// env.MADEIRA_GDI_SHARED_SECTION = 0 in the Desktop's Game config or in bcd.cfg, which the bridge
/// exports after this, turns it off.
enum DesktopGDISection {
    /// This app run's Desktop set the variable; another library start clears it again.
    private static var setByDesktop = false

    /// For every library start, before the session starts: the log line to write, or nil.
    static func apply(_ entry: LibraryEntry) -> String? {
        if entry.desktop == true {
            setenv("MADEIRA_GDI_SHARED_SECTION", "1", 1)
            setByDesktop = true
            return "[bcd] Desktop: section-backed GDI table for the 32-bit programs started here "
                + "(MADEIRA_GDI_SHARED_SECTION=1; env.MADEIRA_GDI_SHARED_SECTION = 0 in its Game config turns it off)"
        }
        if setByDesktop {
            unsetenv("MADEIRA_GDI_SHARED_SECTION")
            setByDesktop = false
        }
        return nil
    }
}
