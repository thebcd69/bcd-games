// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit
import Combine

// Settings' live backends: the one part of Settings that touches the engine (it is listed in
// tools/ui-preview.py's ENGINE_BOUND, so the preview app never compiles it). Every read and
// write goes through the engine's own settings and functions, with the same keys, the same
// defaults and the same log lines the previous settings screen used; nothing here changes
// what the engine does with them.
//
// Config keys are passed to MadeiraConfig as values, never as literals in a read call, so the
// settings catalog's scanner (build/tools/gen-config-catalog.py), which catalogues the engine's
// own reads, does not count this screen as one more reader.

extension BCDSettingsBackends {
    static func make() -> BCDSettingsBackends {
        BCDSettingsBackends(memory: BCDMemoryLive(), graphics: BCDGraphicsLive(), controls: BCDControlsLive(),
                            storage: BCDStorageLive(), dotNet: BCDDotNetLive(), logs: BCDLogsLive(), feedback: BCDFeedbackLive(),
                            allSettings: BCDAllSettingsLive(), about: BCDAboutLive(), developer: BCDDeveloperLive(),
                            vcRuntime: BCDVCRuntimeLive(),
                            // Accounts, JIT and BCD Dock (BCDAccountsLive, BCDJITLive, BCDDockLive): each
                            // draws its whole page; B asks its own pages first (BCDSettingsSectionBack).
                            custom: [
                                .accounts: { AnyView(BCDAccountsLiveSection(backHook: BCDSettingsSectionBack.shared.hook)) },
                                .jit: { AnyView(BCDJITLiveSection(enableJIT: { BCDLiveStore.shared.enableJIT() },
                                                                  backHook: BCDSettingsSectionBack.shared.hook)) },
                                .dock: { AnyView(BCDDockLiveSection(startDock: { BCDLiveStore.shared.startDock($0, $1) },
                                                                    backHook: BCDSettingsSectionBack.shared.hook)) },
                            ])
    }
}

/// The engine options Settings shows, by their real names.
private enum BCDLiveKey {
    static let pool = "pool"
    static let videoMemory = "vram-mb"
    static let swap = "swap-mb"
    static let memoryPool = "mempool-mb"
    static let swapCoverage = "env.MADEIRA_SWAP_COVERAGE"
    static let eco = "eco"
    static let desktopSize = "desktop-size"
    static let frameCap = "env.MADEIRA_FPS_CAP"
    static let promote = "env.MADEIRA_PROMOTE"
    static let metalFX = "metalfx-upscale"
    static let direct3D9 = "d3d9"
    static let xinput = "MADEIRA_XINPUT"                      // MadeiraConfig.flag name, written as env.MADEIRA_XINPUT
    static let padMode = "env.MADEIRA_PAD_MODE"
    static let padOutput = "env.MADEIRA_PAD_OUTPUT"
    static let diagnostics: [BCDDiagnostic: String] = [       // MadeiraConfig.flag names, off unless set
        .deviceLoad: "MADEIRA_DEVICE_STATS", .power: "MADEIRA_POWER_PROBE",
        .lean: "MADEIRA_LEAN_DIAG", .sessionTools: "MADEIRA_SESSION_DIAGNOSTICS",
    ]
}

// MARK: - Memory & performance

@MainActor final class BCDMemoryLive: BCDMemoryBackend {
    @Published private(set) var status = BCDMemoryLive.measure()
    @Published private(set) var eco = false
    @Published private(set) var restartNeeded = false
    @Published private(set) var extensionTestRunning = false
    @Published private(set) var extensionTestLine: String?
    @Published private var values: [BCDMemoryKey: String] = [:]
    let switchesShown = RuntimeMemorySyncSettings.shown
    var extensionTestAvailable: Bool { true }

    init() { read() }

    /// What iOS lets BCD use: what the app uses now plus what it may still take.
    static func measure() -> BCDMemoryStatus {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count) }
        }
        let available = jit_available_memory()
        let limit: Int64? = result == KERN_SUCCESS && available > 0 ? Int64(info.phys_footprint) + Int64(available) : nil
        var low: UInt64 = 0, high: UInt64 = 0
        let space: Int64? = jit_task_map_range(&low, &high) ? Int64(high) : nil
        let entitlements = EntitlementStatus.check()
        return BCDMemoryStatus(limitBytes: limit, deviceBytes: Int64(ProcessInfo.processInfo.physicalMemory),
                               increasedLimit: entitlements.increasedMemory, extendedAddressing: entitlements.extendedVA,
                               addressSpaceBytes: space)
    }

    private static func key(_ key: BCDMemoryKey) -> String? {
        switch key {
        case .pool: return BCDLiveKey.pool
        case .videoMemory: return BCDLiveKey.videoMemory
        case .swap: return BCDLiveKey.swap
        case .memoryPool: return BCDLiveKey.memoryPool
        case .swapCoverage, .syncEngine: return nil
        }
    }

    private static func megabytes(_ key: BCDMemoryKey) -> [Int] {
        switch key {
        case .pool: return RuntimeMemorySyncSettings.poolChoices
        case .videoMemory: return RuntimeMemorySyncSettings.vramChoices
        case .swap: return RuntimeMemorySyncSettings.swapChoices
        case .memoryPool: return RuntimeMemorySyncSettings.memPoolChoices
        case .swapCoverage, .syncEngine: return []
        }
    }

    /// The values as the engine reads them from bcd.cfg.
    private func read() {
        var out: [BCDMemoryKey: String] = [:]
        for key in BCDMemoryKey.allCases {
            if let name = Self.key(key) {
                let mb = Int(MadeiraConfig.get(name) ?? "") ?? 0
                out[key] = mb > 0 ? String(mb) : ""
            }
        }
        out[.swapCoverage] = RuntimeMemorySyncSettings.currentCoverage()
        out[.syncEngine] = SyncEngine.current.rawValue
        values = out
        eco = MadeiraConfig.bool(BCDLiveKey.eco, default: false)
    }

    func choices(_ key: BCDMemoryKey) -> [String] {
        var list: [String]
        switch key {
        case .swapCoverage: list = RuntimeMemorySyncSettings.coverageChoices.map(\.0)
        case .syncEngine: list = [SyncEngine.fastsync.rawValue] + SyncEngine.allCases.filter { $0 != .fastsync }.map(\.rawValue)
        default: list = Self.megabytes(key).map { $0 > 0 ? String($0) : "" }
        }
        let current = value(key)
        if !list.contains(current) { list.append(current) }      // a value set by hand stays reachable
        return list
    }

    func value(_ key: BCDMemoryKey) -> String { values[key] ?? "" }

    func set(_ key: BCDMemoryKey, _ value: String) {
        switch key {
        case .swapCoverage:
            // With swap-mode = 2 no key means broad, so the standard rules are written out.
            MadeiraConfig.set(BCDLiveKey.swapCoverage, value.isEmpty ? (RuntimeMemorySyncSettings.swapModeBroad ? "classic" : nil) : value)
            LogStore.shared.log("[runtime-settings] swap-coverage=\(value.isEmpty ? "classic" : value)")
        case .syncEngine:
            guard let engine = SyncEngine(rawValue: value) else { return }
            SyncEngine.apply(engine)
            LogStore.shared.log("[runtime-settings] sync-engine=\(engine.rawValue)")
        default:
            guard let name = Self.key(key) else { return }
            let mb = Int(value) ?? 0
            MadeiraConfig.set(name, mb > 0 ? String(mb) : nil)
            LogStore.shared.log("[runtime-settings] \(name)=\(mb)")
        }
        restartNeeded = true
        read()
    }

    func setEco(_ on: Bool) {
        MadeiraConfig.set(BCDLiveKey.eco, on ? "1" : nil)
        LogStore.shared.log("[runtime-settings] eco=\(on ? 1 : 0)")
        restartNeeded = true
        read()
    }

    /// The helper extension's memory experiment (MemoryHostTest.m); results in the log.
    func runExtensionTest() {
        guard !extensionTestRunning else { return }
        extensionTestRunning = true
        extensionTestLine = "Starting…"
        MadeiraMemoryHostTest({ [weak self] line in
            LogStore.shared.log("[memhost] \(line)")
            DispatchQueue.main.async { self?.extensionTestLine = line }
        }, { [weak self] in
            DispatchQueue.main.async { self?.extensionTestRunning = false }
        })
    }

    func refresh() {
        status = Self.measure()
        read()
    }
}

// MARK: - Graphics

@MainActor final class BCDGraphicsLive: BCDGraphicsBackend {
    @Published private(set) var resolution = ""
    @Published private(set) var frameCap: Int?
    @Published private(set) var holdMaximumRate = false
    @Published private(set) var metalFX = ""
    @Published private(set) var direct3D9 = ""
    let resolutions: [BCDResolution]
    let defaultResolution = ResolutionChoices.defaultValue
    let displayRate = ProMotionIntent.panelMaxFPS
    let displaySwitchesShown = RuntimeMemorySyncSettings.shown
    var restartNeeded: Bool { false }

    init() {
        let screen = ResolutionChoices.screen
        let native = (Int(screen.pixels.width), Int(screen.pixels.height))
        resolutions = ResolutionChoices.groups().flatMap(\.choices).map { choice in
            let shape: BCDResolution.Shape
            if choice.w == native.0 && choice.h == native.1 { shape = .native }
            else if ResolutionChoices.widescreen.contains(where: { $0 == (choice.w, choice.h) }) && !ResolutionChoices.fills(choice.w, choice.h) { shape = .wide }
            else if ResolutionChoices.classic.contains(where: { $0 == (choice.w, choice.h) }) && !ResolutionChoices.fills(choice.w, choice.h) { shape = .classic }
            else { shape = .screen }
            return BCDResolution(width: choice.w, height: choice.h, shape: shape)
        }
        read()
    }

    private func read() {
        let values = MadeiraConfig.all()
        // The desktop's size as the session reads it: WxH within 640x360 and 3840x2160.
        let size = (values[BCDLiveKey.desktopSize] ?? "").lowercased().split(separator: "x")
            .compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
        resolution = size.count == 2 && (640...3840).contains(size[0]) && (360...2160).contains(size[1]) ? "\(size[0])x\(size[1])" : ""
        frameCap = Int(values[BCDLiveKey.frameCap] ?? "").flatMap { (10...120).contains($0) ? $0 : nil }
        holdMaximumRate = ProMotionIntent.holdMaximum
        metalFX = values[BCDLiveKey.metalFX] ?? ""
        direct3D9 = values[BCDLiveKey.direct3D9] ?? ""
    }

    private func write(_ key: String, _ value: String?) {
        MadeiraConfig.set(key, (value?.isEmpty ?? true) ? nil : value)
        LogStore.shared.log("[bcd-settings] \(key) = \(value.flatMap { $0.isEmpty ? nil : $0 } ?? "(default)")")
        read()
    }

    func setResolution(_ value: String) { write(BCDLiveKey.desktopSize, value) }
    func setFrameCap(_ fps: Int?) { write(BCDLiveKey.frameCap, fps.map(String.init)) }
    func setMetalFX(_ value: String) { write(BCDLiveKey.metalFX, value) }
    func setDirect3D9(_ value: String) { write(BCDLiveKey.direct3D9, value) }

    func setHoldMaximumRate(_ on: Bool) {
        MadeiraConfig.set(BCDLiveKey.promote, on ? "1" : nil)
        LogStore.shared.log("[runtime-settings] promote=\(on ? 1 : 0)")
        read()
    }
}

// MARK: - Controls

@MainActor final class BCDControlsLive: BCDControlsBackend {
    private let controls = TouchControlsModel.shared
    private let presets = ControlPresetsModel.shared
    private let input = InputSettings.shared
    private var bag = Set<AnyCancellable>()
    @Published private var values: [String: String] = MadeiraConfig.all()
    @Published private(set) var restartNeeded = false

    init() {
        for publisher in [controls.objectWillChange, presets.objectWillChange, input.objectWillChange,
                          HardwareInput.shared.objectWillChange] {
            publisher.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &bag)
        }
    }

    var touchControls: Bool { controls.visible }
    func setTouchControls(_ on: Bool) { controls.visible = on }
    var opacity: Double { controls.opacity }
    func setOpacity(_ value: Double) { controls.opacity = min(max(value, 0.2), 1) }

    var layoutsShown: Bool { ControlPresetsModel.enabled }
    var layouts: [BCDControlLayout] {
        presets.available.map { BCDControlLayout(id: $0.id, name: $0.name, builtIn: ControlPresetStore.isBuiltIn($0.id)) }
    }
    var activeLayout: String? { controls.layoutID }
    var unsavedControls: Bool { presets.currentIsUnsaved }
    var canSaveLayouts: Bool { !presets.readOnly }
    var nextLayoutName: String { presets.store.nextCustomName() }

    func useLayout(_ id: String, keepCurrentFirst: Bool) {
        if keepCurrentFirst { presets.keepCurrent() }
        presets.load(id, screen: ControlPresetsModel.currentScreen())
    }

    /// The controller hub's keys-and-mouse mapping (BCDControllerHub; its screen has the same switch).
    private let pads = BCDControllerHub.shared
    var controllerKeys: Bool { pads.mapping }
    func setControllerKeys(_ on: Bool) {
        pads.setMapping(on)
        LogStore.shared.log("[bcd-settings] controller keys=\(on ? 1 : 0)")
        objectWillChange.send()
    }

    // A mouse or trackpad: HardwareInput's own state and lock (its developer-only lock button
    // and AssistiveTouch note). Its published values change on connect, lock and path only.
    private let mouse = HardwareInput.shared
    var mouseConnected: Bool { HardwareInput.enabled && mouse.mouseConnected }
    var pointerLockAvailable: Bool { HardwareInput.enabled && HardwareInput.pointerLockAvailable }
    var pointerLocked: Bool { mouse.pointerLocked }
    var pointerLockUsable: Bool { mouse.pointerLocked || mouse.mousePath != .uikit }
    func togglePointerLock() { mouse.togglePointerLock(why: "BCD settings") }
    var mouseNeedsAssistiveTouch: Bool { HardwareInput.enabled && mouse.assistiveTouchHint }
    func dismissMouseHint() { mouse.dismissAssistiveTouchHint() }

    func newLayout() -> Bool { presets.createLayout() }
    func deleteLayout(_ id: String) { presets.delete(id) }
    func editor(close: @escaping () -> Void) -> AnyView? { AnyView(BCDTouchEditorHost(close: close)) }

    var pointer: BCDPointerMode {
        switch input.mode {
        case .absolute: return .absolute
        case .touch: return .touch
        case .relative: return .relative
        }
    }

    func setPointer(_ mode: BCDPointerMode) {
        switch mode {
        case .absolute: input.mode = .absolute
        case .touch: input.mode = .touch
        case .relative: input.mode = .relative
        }
        LogStore.shared.log("[bcd-settings] pointer=\(mode.rawValue)")
    }

    var touchSpeed: Double { input.sensAbs }
    func setTouchSpeed(_ value: Double) { input.sensAbs = min(max(value, 0.1), 8) }
    var lookSpeed: Double { input.sensRel }
    func setLookSpeed(_ value: Double) { input.sensRel = min(max(value, 0.1), 8) }
    var mouseSpeed: Double { input.sensMouse }
    func setMouseSpeed(_ value: Double) { input.sensMouse = min(max(value, 0.1), 8) }
    var rightStickMouse: Bool { input.padRightStickMouse }
    func setRightStickMouse(_ on: Bool) { input.padRightStickMouse = on }

    /// Physical controllers: on unless bcd.cfg says 0 (read once per app run).
    var controllers: Bool { values["env." + BCDLiveKey.xinput].map { $0 != "0" } ?? MadeiraConfig.flag(BCDLiveKey.xinput) }
    func setControllers(_ on: Bool) {
        write("env." + BCDLiveKey.xinput, on ? nil : "0")
        restartNeeded = true
    }

    var controllerAPI: String { values[BCDLiveKey.padMode] ?? "" }
    var controllerAPIChoices: [BCDSettingsChoice] { Self.choices(BCDLiveKey.padMode) }
    func setControllerAPI(_ value: String) { write(BCDLiveKey.padMode, value.isEmpty ? nil : value) }
    var controllerOutput: String { values[BCDLiveKey.padOutput] ?? "" }
    var controllerOutputChoices: [BCDSettingsChoice] { Self.choices(BCDLiveKey.padOutput) }
    func setControllerOutput(_ value: String) { write(BCDLiveKey.padOutput, value.isEmpty ? nil : value) }

    private func write(_ key: String, _ value: String?) {
        MadeiraConfig.set(key, value)
        LogStore.shared.log("[bcd-settings] \(key) = \(value ?? "(default)")")
        values = MadeiraConfig.all()
    }

    /// An option's choices as the settings catalog lists them.
    private static func choices(_ key: String) -> [BCDSettingsChoice] {
        (ConfigCatalog.generated.first { $0.key == key }?.choices ?? [])
            .map { BCDSettingsChoice(value: $0.value, label: BCDSettingsText.scrub($0.label)) }
    }

    /// What PadBindings' built-in template has each input do.
    var keyboardMouseTemplate: [BCDSettingsBinding] {
        let template = PadBindings.template
        var out = [BCDSettingsBinding(input: "LS", action: Self.words(template.leftStick, stick: true)),
                   BCDSettingsBinding(input: "RS", action: Self.words(template.rightStick, stick: true))]
        let dpad = ["D↑", "D↓", "D←", "D→"].map { template.buttons[$0] }
        for name in ["RT", "LT", "A", "B", "X", "Y", "LB", "RB", "L3", "R3"] {
            out.append(BCDSettingsBinding(input: name, action: Self.words(template.buttons[name] ?? .none, stick: false)))
        }
        if dpad == [.key(0x26), .key(0x28), .key(0x25), .key(0x27)] {
            out.append(BCDSettingsBinding(input: "D-pad", action: "Arrow keys"))
        } else {
            for name in ["D↑", "D↓", "D←", "D→"] {
                out.append(BCDSettingsBinding(input: name, action: Self.words(template.buttons[name] ?? .none, stick: false)))
            }
        }
        out.append(BCDSettingsBinding(input: "Start", action: Self.words(template.buttons["Menu"] ?? .none, stick: false)))
        out.append(BCDSettingsBinding(input: "Select", action: Self.words(template.buttons["View"] ?? .none, stick: false)))
        return out
    }

    private static func words(_ action: ControlAction, stick: Bool) -> String {
        switch action {
        case .none: return stick ? "Mouse" : "Nothing"
        case .mouseLeft: return "Left click"
        case .mouseRight: return "Right click"
        case .joystickWASD: return "W A S D"
        case .joystickArrows: return "Arrow keys"
        case .keyboardToggle: return "Keyboard"
        case .pad(let name): return name
        case .key(let vk):
            switch vk {
            case 0x20: return "Space"
            case 0x11: return "Ctrl"
            case 0x10: return "Shift"
            case 0x12: return "Alt"
            case 0x1B: return "Esc"
            case 0x09: return "Tab"
            case 0x0D: return "Enter"
            case 0x30...0x39, 0x41...0x5A: return String(UnicodeScalar(UInt8(vk)))
            default: return ControlAction.keyLabel(vk)
            }
        }
    }
}

/// The on-screen layout editor (TouchControlsOverlay, the same editor a game's menu opens),
/// full screen over Settings. It edits in landscape only, as it always has; upright, it asks
/// for the device to be turned. Ending the edit closes it.
struct BCDTouchEditorHost: View {
    @ObservedObject private var controls = TouchControlsModel.shared
    let close: () -> Void

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color.black.ignoresSafeArea()
                if proxy.size.width > proxy.size.height {
                    TouchControlsOverlay()
                } else {
                    VStack(spacing: 16) {
                        Image(systemName: "rotate.right").font(.system(size: 40, weight: .semibold)).foregroundStyle(BCDStyle.glow)
                        Text("Turn your device on its side").font(BCDStyle.heading(20))
                        Text("The on-screen controls are laid out for a device on its side.")
                            .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft).multilineTextAlignment(.center)
                        Button("Close") { finish() }.buttonStyle(BCDOutlineButtonStyle()).frame(maxWidth: 220)
                            .bcdFocusable("settings.editor.close", cornerRadius: 25) { finish() }
                    }
                    .padding(24)
                    .foregroundStyle(BCDStyle.text)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .preferredColorScheme(.dark)
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
        .onAppear {
            controls.visible = true
            if !controls.editing { controls.editing = true }
        }
        .onChange(of: controls.editing) { _, editing in if !editing { close() } }
        .onDisappear { finish() }
    }

    private func finish() {
        guard controls.editing else { return }
        controls.selected = nil
        controls.editing = false
    }
}

// MARK: - Storage

@MainActor final class BCDStorageLive: BCDStorageBackend {
    let supportsExternalGames = true
    @Published private(set) var externalGamesMessage: String? = ExternalGameStorage.shared.message
    var externalGamesFree: Int64? { ExternalGameStorage.shared.freeBytes }
    var canConnectExternalGames: Bool {
        wine_process_is_running() == 0 && LibraryModel.shared.current == nil && !savesBusy && !addingGames && !model.busy
    }

    func connectExternalGames(_ url: URL) {
        guard canConnectExternalGames else {
            externalGamesMessage = "Quit the game and wait for storage tasks to finish before connecting a folder."
            return
        }
        do {
            try ExternalGameStorage.shared.connect(url)
            externalGamesMessage = ExternalGameStorage.shared.message
            refresh()
        } catch { externalGamesMessage = error.localizedDescription }
    }

    private let model = StorageModel()
    private var bag = Set<AnyCancellable>()
    @Published private(set) var deviceFree: Int64?
    @Published private(set) var deviceTotal: Int64?
    @Published private var monoBytes: Int64?
    @Published private(set) var savesBusy = false
    @Published private(set) var savesMessage: String?
    @Published private(set) var addingGames = false
    @Published private(set) var addGamesMessage: String?

    init() {
        model.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &bag)
    }

    var items: [BCDStorageItem] {
        var list = StorageModel.items.map { BCDStorageItem(id: $0.id, name: $0.title, bytes: model.sizes[$0.id], clearable: $0.deletable) }
        if WineMonoModel.downloaded {
            list.insert(BCDStorageItem(id: "mono", name: "Wine Mono", bytes: monoBytes, clearable: false), at: max(0, list.count - 1))
        }
        return list
    }

    var measuring: Bool { model.busy }

    func refresh() {
        model.refresh()
        let keys: Set<URLResourceKey> = [.volumeAvailableCapacityForImportantUsageKey, .volumeTotalCapacityKey]
        if let values = try? URL(fileURLWithPath: NSHomeDirectory()).resourceValues(forKeys: keys) {
            deviceFree = values.volumeAvailableCapacityForImportantUsage
            deviceTotal = values.volumeTotalCapacity.map { Int64($0) }
        }
        guard WineMonoModel.downloaded else { monoBytes = nil; return }
        let runtime = WineMonoModel.runtime
        DispatchQueue.global(qos: .utility).async {
            let bytes = StorageModel.size(of: runtime)
            DispatchQueue.main.async { [weak self] in self?.monoBytes = bytes }
        }
    }

    func clear(_ id: String) {
        guard let item = StorageModel.items.first(where: { $0.id == id }), item.deletable else { return }
        LogStore.shared.log("[bcd-settings] storage clear \(id)")
        model.clear(item)
    }

    /// SaveBackup's zip of every Windows user's saves (the classic Saves section's function).
    func backupSaves(_ done: @escaping (URL?) -> Void) {
        guard !savesBusy else { return }
        savesBusy = true
        savesMessage = nil
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Swift.Result { try SaveBackup.backup() }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                savesBusy = false
                switch result {
                case .success(let backup):
                    savesMessage = "\(backup.files) files backed up, \(BCDFormat.bytes(Int64(backup.bytes)))"
                        + (backup.leftOut > 0 ? "; \(backup.leftOut) very large files left out." : ".")
                    done(backup.url)
                case .failure(let error):
                    savesMessage = error.localizedDescription
                    done(nil)
                }
            }
        }
    }

    func discardBackup(_ url: URL) { try? FileManager.default.removeItem(at: url) }

    func restoreSaves(from url: URL) {
        guard !savesBusy else { return }
        savesBusy = true
        savesMessage = nil
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Swift.Result { try SaveBackup.restore(from: url) }
            DispatchQueue.main.async { [weak self] in
                self?.savesBusy = false
                switch result {
                case .success(let count): self?.savesMessage = "\(count) files put back."
                case .failure(let error): self?.savesMessage = error.localizedDescription
                }
            }
        }
    }

    /// BCDLibraryImport: every game found on drive C:, added with its main program.
    func addEveryGame() {
        guard !addingGames else { return }
        addingGames = true
        addGamesMessage = nil
        BCDLibraryImport.run { [weak self] added in
            self?.addingGames = false
            self?.addGamesMessage = added == 0 ? "No new games found." : "\(added) game\(added == 1 ? "" : "s") added to your library."
        }
    }
}

// MARK: - .NET Framework

@MainActor final class BCDDotNetLive: BCDDotNetBackend {
    private let mono = WineMonoModel.shared
    private var bag = Set<AnyCancellable>()
    @Published private(set) var installedBytes: Int64?

    init() {
        mono.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &bag)
    }

    var state: BCDMonoState {
        switch mono.phase {
        case .downloading(let fraction): return .downloading(fraction)
        case .installing(let fraction): return .installing(fraction)
        case .idle:
            if WineMonoModel.bundled { return .builtIn }
            return mono.installed ? .installed(version: WineMonoPin.version) : .missing
        }
    }

    var error: String? { mono.error }
    var version: String { WineMonoPin.version }
    var downloadBytes: Int64 { WineMonoPin.tarBytes }

    func install() { mono.install() }

    func remove() {
        mono.remove()
        measure()
    }

    func refresh() {
        mono.refresh()
        measure()
    }

    private func measure() {
        guard WineMonoModel.downloaded else { installedBytes = nil; return }
        let runtime = WineMonoModel.runtime
        DispatchQueue.global(qos: .utility).async {
            let bytes = StorageModel.size(of: runtime)
            DispatchQueue.main.async { [weak self] in self?.installedBytes = bytes }
        }
    }
}

// MARK: - Visual C++ runtime

/// VCRuntime.shared (VCRuntimeDownload.swift): the download from Microsoft, its checks and the
/// copy on the device.
@MainActor final class BCDVCRuntimeLive: BCDVCRuntimeBackend {
    private let runtime = VCRuntime.shared
    private var bag = Set<AnyCancellable>()

    init() {
        runtime.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &bag)
    }

    var state: BCDVCRuntimeState {
        switch runtime.status {
        case .notInstalled: return .missing
        case .downloading(let received, let expected): return .downloading(received: received, expected: expected)
        case .installing(let fraction): return .installing(fraction)
        case .installed(let version): return .installed(version: version)
        }
    }

    var source: BCDVCRuntimeSource {
        switch runtime.source {
        case .bundled: return .bundled
        case .downloaded: return .downloaded
        case .none: return .none
        }
    }

    var lastError: String? { runtime.lastError }

    func install() {
        LogStore.shared.log("[bcd-settings] VC++ runtime download")
        runtime.install()
    }

    func cancel() { runtime.cancel() }

    func remove() {
        LogStore.shared.log("[bcd-settings] VC++ runtime remove")
        runtime.remove()
    }

    func refresh() { if !runtime.isBusy { runtime.refresh() } }
}

// MARK: - Logs

/// Auto-tune's switch for BCD's Settings (check-autotune.py registers the one line that names it).
struct BCDLiveSwitch {
    let read: () -> Bool
    let write: (Bool) -> Void
    /// The value this app run started with: the switch applies from the next start.
    let running: Bool
}

@MainActor final class BCDLogsLive: BCDLogsBackend {
    /// An install without the increased memory limit: what fixes it, said before a log goes (MemoryRights.swift).
    var sendLogAdvice: String? { MemoryRights.lacking ? MemoryRights.sendLogAdvice : nil }
    private let input = InputSettings.shared
    private let tune = BCDLiveSwitch(read: { AutoTune.preferred }, write: { AutoTune.preferred = $0 }, running: AutoTune.enabled)   // madeira-bcd: auto-tune's switch in BCD's Settings, on by default
    private var bag = Set<AnyCancellable>()
    private var files: [String: URL] = [:]
    @Published private(set) var recent: [BCDLogFile] = []
    @Published private(set) var sent: [BCDSentLog] = []
    @Published private(set) var sending = false
    @Published private(set) var sendStatus: String?
    @Published private var configTick = 0
    var restartNeeded: Bool { false }

    init() {
        input.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &bag)
    }

    func refresh() {
        LogUpload.forgetAutomatic()
        let urls = LogUpload.recentLogs()
        files = Dictionary(urls.map { ($0.path, $0) }, uniquingKeysWith: { first, _ in first })
        recent = urls.map { BCDLogFile(id: $0.path, name: $0.lastPathComponent, date: LogUpload.modified($0), bytes: Int64(LogUpload.size($0))) }
        sent = LogUpload.history.map { BCDSentLog(code: $0.code, name: $0.name, date: $0.date) }
    }

    func send(_ log: BCDLogFile) {
        guard !sending, let url = files[log.id] else { return }
        sending = true
        sendStatus = "Getting it ready…"
        LogUpload.send(url, game: LogUpload.game(of: url), progress: { [weak self] text in self?.sendStatus = text }) { [weak self] result in
            guard let self else { return }
            sending = false
            switch result {
            case .success(let code): sendStatus = "Sent. Your code: \(code)"
            case .failure(let error): sendStatus = "Not sent: \(error.localizedDescription)"
            }
            refresh()
        }
    }

    func copy(_ code: String) { UIPasteboard.general.string = code }

    /// BCD's own logs for the share sheet, linked under BCD's names (no copy of a large file).
    func exportable() -> [URL] {
        let fm = FileManager.default
        let folder = fm.temporaryDirectory.appendingPathComponent("BCD logs", isDirectory: true)
        try? fm.removeItem(at: folder)
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true)
        return LogStore.shared.exportableLogs.map { url in
            let name: String
            switch url.lastPathComponent {
            case "bcd-log.txt": name = "BCD log.txt"
            case "bcd-log.prev.txt": name = "BCD log (the run before).txt"
            default: name = BCDSettingsText.scrub(url.lastPathComponent)
            }
            let link = folder.appendingPathComponent(name)
            if (try? fm.linkItem(at: url, to: link)) != nil || (try? fm.copyItem(at: url, to: link)) != nil { return link }
            return url
        }
    }

    var extendedLogging: Bool { input.diagnostics }
    func setExtendedLogging(_ on: Bool) { input.diagnostics = on }

    func diagnostic(_ item: BCDDiagnostic) -> Bool {
        _ = configTick
        guard let name = BCDLiveKey.diagnostics[item] else { return false }
        return MadeiraConfig.flag(name, fallback: false)
    }

    func setDiagnostic(_ item: BCDDiagnostic, _ on: Bool) {
        guard let name = BCDLiveKey.diagnostics[item] else { return }
        MadeiraConfig.set("env." + name, on ? "1" : nil)
        LogStore.shared.log("[bcd-settings] env.\(name) = \(on ? "1" : "(default)")")
        configTick += 1
    }

    var crashTuning: Bool {
        _ = configTick
        return tune.read()
    }
    var crashTuningRunning: Bool { tune.running }
    func setCrashTuning(_ on: Bool) {
        tune.write(on)
        configTick += 1
    }
}

// MARK: - Feedback

/// Settings › Feedback on the engine: FeedbackUpload.swift sends the message, LogUpload.swift the log
/// that goes with it ("Attach my newest log"). The log is sent first and its code goes into the
/// message; a log that was sent for this message is not sent again when the message is retried.
@MainActor final class BCDFeedbackLive: BCDFeedbackBackend {
    let draft = BCDFeedbackDraft()
    @Published private(set) var newestLog: BCDFeedbackLog?
    @Published private(set) var sent: [BCDSentFeedback] = []
    @Published private(set) var state = BCDFeedbackState.idle
    private var newest: URL?
    /// The log already sent for the message being retried: its file as it was, and the code.
    private var attached: (path: String, date: Date, bytes: Int, code: String)?

    func refresh() {
        let urls = LogUpload.recentLogs()
        if let first = urls.first {
            // A game's session log is a second name for BCD's log of the same run: the game's name is the better label.
            let url = urls.first { LogUpload.modified($0) == LogUpload.modified(first) && $0.deletingLastPathComponent().lastPathComponent == "logs" } ?? first
            newest = url
            newestLog = BCDFeedbackLog(name: url.lastPathComponent, date: LogUpload.modified(url), bytes: Int64(LogUpload.size(url)))
        } else {
            newest = nil
            newestLog = nil
            draft.attachLog = false
        }
        sent = FeedbackUpload.history.map {
            BCDSentFeedback(code: $0.code, kind: BCDFeedbackKind(rawValue: $0.kind) ?? .other, date: $0.date, line: $0.line)
        }
    }

    func send() {
        if case .sending = state { return }
        let kind = draft.kind.rawValue, text = draft.text
        guard !FeedbackCore.clean(text).isEmpty else { state = .failed(FeedbackUpload.UploadError.problem(.empty).localizedDescription); return }
        guard draft.attachLog, let url = newest else { return post(kind, text, log: nil) }
        let file = (path: url.path, date: LogUpload.modified(url), bytes: LogUpload.size(url))
        if let done = attached, done.path == file.path, done.date == file.date, done.bytes == file.bytes {
            return post(kind, text, log: done.code)
        }
        state = .sending("Getting your log ready…")
        LogUpload.send(url, game: LogUpload.game(of: url), progress: { [weak self] line in self?.state = .sending(line) }) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let code):
                attached = (file.path, file.date, file.bytes, code)
                post(kind, text, log: code)
            case .failure(let error):
                state = .failed("Your log was not sent: \(error.localizedDescription) Your message is still here. Turn off \u{201C}Attach my newest log\u{201D} to send it alone.")
            }
        }
    }

    private func post(_ kind: String, _ text: String, log: String?) {
        state = .sending("Sending your message…")
        FeedbackUpload.send(kind: kind, text: text, log: log) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let code):
                state = .sent(code)
                draft.text = ""
                attached = nil
            case .failure(let error):
                state = .failed("Not sent: \(error.localizedDescription) Your message is still here.")
            }
            refresh()
        }
    }

    func copy(_ code: String) { UIPasteboard.general.string = code }
}

// MARK: - Developer

@MainActor final class BCDDeveloperLive: BCDDeveloperBackend {
    private let pads = BCDControllerHub.shared
    private var bag = Set<AnyCancellable>()

    init() {
        // The controller check follows the pads coming and going, and the hub's tester (it reads
        // the sticks only while its screen watches it), a few times a second at most.
        pads.objectWillChange.merge(with: pads.tester.objectWillChange)
            .throttle(for: .milliseconds(150), scheduler: RunLoop.main, latest: true)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &bag)
    }

    /// The library's Windows desktop entry, started through BCD's own Play (BCDLiveStore), as
    /// the library's "Windows desktop" game starts.
    func startDesktop() {
        let live = BCDLiveStore.shared
        let id = BCDLiveID.local(LibraryEntry.desktopID)
        let game = live.store.games.first { $0.id == id } ?? BCDGame(id: id, title: "Windows desktop", store: .local, state: .ready)
        live.play(game)
    }

    /// The last 64 KB of this run's log, from the start of a line.
    func logTail() -> String {
        guard let handle = try? FileHandle(forReadingFrom: LogStore.shared.logFileURL) else { return "There is no log yet." }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        let start = size > 64_000 ? size - 64_000 : 0
        try? handle.seek(toOffset: start)
        var text = String(decoding: (try? handle.readToEnd()) ?? Data(), as: UTF8.self)
        if start > 0, let newline = text.firstIndex(of: "\n") { text = String(text[text.index(after: newline)...]) }
        return text.isEmpty ? "The log is empty." : text
    }

    func clearLog() {
        LogStore.shared.clear()
        LogStore.shared.log("[bcd-settings] log cleared")
    }

    var controllerName: String? { pads.leadName }

    var controllerInput: [String] {
        guard pads.connected else { return [] }
        let r = pads.tester.reading
        return ["Last: \(r.last ?? "none")",
                String(format: "Left stick   x %+.2f  y %+.2f", r.leftX, r.leftY),
                String(format: "Right stick  x %+.2f  y %+.2f", r.rightX, r.rightY),
                String(format: "Triggers     L %.2f  R %.2f", r.leftTrigger, r.rightTrigger)]
    }
}

// MARK: - All settings

/// The settings catalog (ConfigCatalog.generated.swift) and bcd.cfg, with the same
/// writes as before: trimmed, empty means out of the file, logged as [all-settings].
@MainActor final class BCDAllSettingsLive: BCDAllSettingsBackend {
    let entries: [BCDConfigEntry] = ConfigCatalog.generated
        .filter { !RuntimeMemorySyncSettings.featuredKeys.contains($0.key) }
        .map(BCDAllSettingsLive.entry)
    let categories = ConfigCatalog.categories
    let catalogKeys = ConfigCatalog.keys
    @Published private(set) var values = MadeiraConfig.all()
    @Published private(set) var changed = false

    private static func entry(_ option: ConfigOption) -> BCDConfigEntry {
        let kind: BCDConfigEntry.Kind
        switch option.kind {
        case .bool: kind = .toggle
        case .int: kind = .number
        case .text: kind = .text
        case .choice: kind = .choice
        }
        return BCDConfigEntry(key: option.key, title: option.title, kind: kind, defaultValue: option.defaultValue,
                              category: option.category, note: option.note,
                              choices: option.choices.map { BCDSettingsChoice(value: $0.value, label: $0.label) })
    }

    func set(_ key: String, _ value: String?) {
        let trimmed = value?.trimmingCharacters(in: .whitespaces)
        let stored = (trimmed?.isEmpty ?? true) ? nil : trimmed
        guard stored != values[key] else { return }
        MadeiraConfig.set(key, stored)
        values = MadeiraConfig.all()
        changed = true
        LogStore.shared.log("[all-settings] \(key) = \(stored ?? "(default)")")
    }

    func refresh() { values = MadeiraConfig.all() }
}

// MARK: - About

@MainActor final class BCDAboutLive: BCDAboutBackend {
    let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
    let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
    /// The stamp a build writes into Info.plist (its round and time), as BuildStamp reads it.
    let stamp = (Bundle.main.infoDictionary?["MadeiraBuild"] as? String).flatMap { $0.isEmpty ? nil : BCDSettingsText.scrub($0) }
    let licences = BCDAboutLive.bundledLicences()

    func credits() -> AnyView { AnyView(BCDAboutCreditsLive()) }

    func text(of licence: BCDLicenceText) -> String {
        (try? String(contentsOfFile: licence.id, encoding: .utf8)) ?? "BCD could not read this text."
    }

    /// The licence texts in the app's licenses and legal folders, one entry per licence.
    private static func bundledLicences() -> [BCDLicenceText] {
        let fm = FileManager.default
        var out: [BCDLicenceText] = []
        var titles = Set<String>()
        func add(_ url: URL) {
            let name = url.lastPathComponent
            let title = Self.title(name)
            guard !name.hasPrefix("."), titles.insert(title).inserted else { return }
            out.append(BCDLicenceText(id: url.path, title: title))
        }
        for folder in ["licenses", "legal"] {
            guard let base = Bundle.main.resourceURL?.appendingPathComponent(folder, isDirectory: true),
                  let names = try? fm.contentsOfDirectory(atPath: base.path) else { continue }
            for name in names.sorted() {
                let url = base.appendingPathComponent(name)
                var directory: ObjCBool = false
                guard fm.fileExists(atPath: url.path, isDirectory: &directory) else { continue }
                if directory.boolValue {
                    for inner in ((try? fm.contentsOfDirectory(atPath: url.path)) ?? []).sorted() { add(url.appendingPathComponent(inner)) }
                } else {
                    add(url)
                }
            }
        }
        return out
    }

    /// A licence file's title: by what it is (the GPL's and the exception's files are named
    /// after the original project), else from the list below, else its own name.
    private static func title(_ name: String) -> String {
        let upper = name.uppercased()
        if upper == "LICENSE" || (upper.contains("GPL-3.0") && !upper.contains("LGPL")) { return "GNU General Public License, version 3" }
        if upper.contains("EXCEPTION") && !upper.contains("APACHE") { return "The Metal Shader Converter exception" }
        if upper.hasPrefix("LICENSE-BCD") { return "BCD's additional terms (GPL-3.0 section 7)" }
        return titles[name] ?? BCDSettingsText.scrub(name)
    }

    private static let titles: [String: String] = [
        "THIRD-PARTY-NOTICES.txt": "Third-party notices",
        "THIRD-PARTY-NOTICES.md": "Third-party components",
        "DXMT-MIT.txt": "DXMT · MIT license", "MIT-DXMT.txt": "DXMT · MIT license",
        "FEX-MIT.txt": "FEX-Emu · MIT license", "MIT-FEX.txt": "FEX-Emu · MIT license",
        "LGPL-2.1.txt": "GNU Lesser General Public License 2.1",
        "LGPL-3.0.txt": "GNU Lesser General Public License 3.0",
        "LLVM-Apache-2.0-with-exception.txt": "LLVM · Apache 2.0 with the LLVM exception",
        "ZSTD-BSD.txt": "Zstandard · BSD license",
        "rpmalloc-0BSD.txt": "rpmalloc · 0BSD license",
        "LICENSE-StikJIT-MPL-2.0.txt": "StikJIT · Mozilla Public License 2.0",
        "LICENSE-idevice-MIT.txt": "idevice · MIT license",
        "LICENSES-rppairing-crates.txt": "Pairing libraries (Rust crates)",
        "BSD-2-xxHash.txt": "xxHash · BSD 2-clause license",
        "BSD-3-SoftFloat-3e.txt": "Berkeley SoftFloat 3e · BSD 3-clause license",
        "Cephes.txt": "Cephes",
        "MIT-NVAPI.txt": "NVAPI headers · MIT license",
        "MIT-fmt.txt": "fmt · MIT license",
        "MIT-libxml2.txt": "libxml2 · MIT license",
        "MIT-libxslt.txt": "libxslt · MIT license",
    ]
}

/// About's credits: the app's legal notices exactly as Settings › Credits shows them
/// (Library.swift), through the same views (BCDCredit, MadeiraCredit) and the same notice.
/// tests/host/check-bcd-settings.py holds every line below to Library.swift's, word for word:
/// change them there first, never only here.
struct BCDAboutCreditsLive: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            BCDCreditLink(id: "credit.bcd", link: URL(string: "https://github.com/thebcd69/bcd-games")) {
                BCDCredit()
            }
            row(MadeiraCredit(name: "Will Faust", handle: "willfaust", role: "Created Madeira"))
            row(MadeiraCredit(name: "Nick", handle: "125hz", role: "32-bit game support, the game library and Madeira Dock (BCD Dock in this app)"))
            row(MadeiraCredit(name: "Jfishin", handle: "Jfishin", role: "The original native Steam sign-in, library and downloads"))
            row(MadeiraCredit(name: "Jesse", handle: "JesseLovelace", role: "Steam Cloud saves, faster game launches, and fixes that let more games run"))
            row(MadeiraCredit(name: "Dan Perks", handle: "danperks", role: "In-app JIT without StikDebug, and pairing without a computer"))
            row(MadeiraCredit(name: "spitefulowl", handle: "spitefulowl", role: "Wine and FEX runtime fixes, DXMT texture and memory fixes, audio, the swap tier, and library launch options"))
            row(MadeiraCredit(name: "meshoklv", handle: "meshoklv", role: "Controller fixes for games that ship their own XInput or need focus, touch taps that stay off the mouse, and a crash-guard fix"))
            row(MadeiraCredit(name: "TheHadesc", handle: "TheHadesc", role: "Madeira Dock starts for games whose Steam launch entries do not start at zero, and a touch gamepad that survives the in-game keyboard"))
            Text("BCD and Madeira are built on Wine, FEX-Emu, DXMT by Feifan He (3Shain) with the Direct3D 9 frontend by David Acevedo (dacevedo12), rpmalloc by Mattias Jansson, StikDebug, StikJIT and idevice. Thank you to everyone who contributes to these projects.\n\nBCD is free software under the GNU General Public License, version 3, with additional terms for BCD's own files (LICENSE-BCD.md), and comes with NO WARRANTY. The licence: gnu.org/licenses/gpl-3.0. The source: github.com/thebcd69/bcd-games.")
                .font(.footnote).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func row(_ credit: MadeiraCredit) -> some View {
        BCDCreditLink(id: "credit.\(credit.handle)", link: URL(string: "https://github.com/\(credit.handle)")) { credit }
    }
}
