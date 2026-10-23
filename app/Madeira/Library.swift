import SwiftUI
import UniformTypeIdentifiers
import UIKit
import Darwin
import ImageIO
import Combine

// ============================================================================
// Library front end.
//
// A game library in front of the existing launch path: entries are Windows
// executables inside the prefix's drive_c, each with its own launch profile
// (arguments, resolution and scaling, frame limit, x87 precision, on-screen
// controls). Play starts ContentView's runWineFullSequence; the session runs
// full screen with BCD's in-game menu. Wave 3 (2026-10-19): this file is the
// library engine only; the classic library screens, the developer interface and
// the interface choice are gone (BCD's interface, BCDLiveFrontEnd, is the only one).
// Every switch below is read with MadeiraConfig.flag, so `env.NAME = 0` in
// bcd.cfg turns it off.
// ============================================================================

/// Thermal state, Low Power Mode, screen capture, battery and brightness every
/// 10 s while a session runs: the device conditions that explain a slow run in
/// a log (447: the CPU was held at 1.31/1.70 GHz from the start of play with
/// thermal=nominal; charging and screen recording also draw from the power
/// budget). MADEIRA_DEVICE_STATS=0 turns the line off.
enum DeviceLoadDiagnostics {
    private static var lastReport = 0.0
    private static var timer: Timer?
    /// Opt-in (env.MADEIRA_DEVICE_STATS = 1): a line every 10 s during every
    /// session is a diagnostic, so it is off by default.
    static func start() {
        guard timer == nil, MadeiraConfig.flag("MADEIRA_DEVICE_STATS", fallback: false) else { return }
        UIDevice.current.isBatteryMonitoringEnabled = true
        let value = Timer(timeInterval: 10, repeats: true) { _ in report() }
        timer = value
        RunLoop.main.add(value, forMode: .common)
    }
    static func report() {
        guard wine_process_is_running() != 0 else { return }
        let now = CACurrentMediaTime()
        guard now - lastReport >= 10 else { return }
        lastReport = now
        let process = ProcessInfo.processInfo
        let thermal = thermalName(process.thermalState)
        let device = UIDevice.current
        if !device.isBatteryMonitoringEnabled { device.isBatteryMonitoringEnabled = true }   // the HUD turns it off when it closes
        let level = device.batteryLevel < 0 ? "?" : String(Int((device.batteryLevel * 100).rounded()))
        let battery: String
        switch device.batteryState {
        case .charging: battery = "charging"
        case .full: battery = "full"
        case .unplugged: battery = "unplugged"
        default: battery = "unknown"
        }
        fputs("[device-load] thermal=\(thermal) low-power=\(process.isLowPowerModeEnabled ? 1 : 0) capture=\(UIScreen.main.isCaptured ? 1 : 0) "
              + "battery=\(level)% \(battery) brightness=\(String(format: "%.2f", UIScreen.main.brightness))\n", stderr)
    }
    static func thermalName(_ state: ProcessInfo.ThermalState) -> String {
        switch state {
        case .nominal: return "nominal"
        case .fair: return "fair"
        case .serious: return "serious"
        case .critical: return "critical"
        @unknown default: return "unknown"
        }
    }
}

/// Which build is installed, logged once at start ([build]). A build that writes
/// `MadeiraBuild` into Info.plist (a round tag and build time) shows that; other
/// builds show the bundle version.
enum BuildStamp {
    static let text: String = {
        let info = Bundle.main.infoDictionary ?? [:]
        if let stamp = info["MadeiraBuild"] as? String, !stamp.isEmpty { return stamp }
        let version = info["CFBundleShortVersionString"] as? String ?? "?"
        let build = info["CFBundleVersion"] as? String ?? "?"
        return "v\(version) (\(build))"
    }()
}

/// Controller navigation of the library, fed by GamepadInput's sampler (player
/// 1's physical pad) so there is no second timer. While the library owns input
/// (no session, or the in-game menu is open) GamepadInput publishes a neutral
/// pad to Windows. D-pad or left stick moves the focus, A opens, B goes back,
/// Y adds a game, a shoulder switches tabs; in a session Back+Start opens the
/// menu. MADEIRA_FRONTEND_CONTROLLER=0 turns navigation off.
final class LibraryController: ObservableObject, @unchecked Sendable {
    static let shared = LibraryController()
    @Published var connected = false
    let commands = PassthroughSubject<String, Never>()
    private let lock = NSLock()
    private var enabled = false
    private var owns = false
    private var last: UInt16 = 0
    private var announced = false
    private let allowed = MadeiraConfig.flag("MADEIRA_FRONTEND_CONTROLLER")
    /// BCD (2026-10-18): holding the Home button opens and closes the in-game menu (BCDSessionLive.swift).
    static let homeHold = MadeiraConfig.flag("MADEIRA_MENU_HOME_HOLD", fallback: false)   // 1: holding a controller's Home (PS) button for half a second opens and closes BCD's in-game menu; iOS hands Home over at once only with env.MADEIRA_PAD_SYSTEM_GESTURES = 1. Off (default): Back + Start and the menu button only
    private let holdHome = LibraryController.homeHold   // read once, on the main thread
    private var homeSince: TimeInterval?
    private var homeSent = false
    var ownsInput: Bool { lock.lock(); defer { lock.unlock() }; return enabled && owns }
    func configure(enabled: Bool, ownsInput: Bool) {
        lock.lock(); self.enabled = enabled && allowed; owns = ownsInput; lock.unlock()
    }
    /// One sample of player 1's pad, in XInput button bits and stick units.
    func sample(buttons raw: UInt16, lx: Int16, ly: Int16) {
        lock.lock()
        guard enabled else { lock.unlock(); return }
        var buttons = raw
        if owns {
            if lx < -16000 { buttons |= 4 }; if lx > 16000 { buttons |= 8 }
            if ly > 16000 { buttons |= 1 }; if ly < -16000 { buttons |= 2 }
        }
        let pressed = buttons & ~last; last = buttons
        let own = owns, announce = !announced; announced = true
        var homeHeld = false
        if holdHome { homeHeld = holdingHome(buttons) }   // BCD: env.MADEIRA_MENU_HOME_HOLD (opt-in); off, nothing is timed
        lock.unlock()
        if announce { DispatchQueue.main.async { self.connected = true; fputs("[frontend-controller] navigation active\n", stderr) } }
        var command: String?
        // Reserve the Back+Start chord in gameplay, leaving ordinary Start intact.
        if !own, buttons & 0x30 == 0x30, pressed & 0x30 != 0 { command = "menu" }
        if own {
            // BCD (2026-10-18): the shoulders say which way (BCD's interface has three tabs);
            // X and View reach BCD's screens too. The classic library takes either shoulder as "tab".
            for (mask, name): (UInt16, String) in [(1, "up"), (2, "down"), (4, "left"), (8, "right"), (0x1000, "accept"), (0x2000, "back"), (0x8000, "add"), (0x10, "menu"), (0x100, "tab-previous"), (0x200, "tab-next"), (0x4000, "x"), (0x20, "options")] {
                if pressed & mask != 0 { command = name; break }
            }
        }
        if homeHeld { command = "home" }
        if let command { DispatchQueue.main.async { self.commands.send(command) } }
    }

    /// The Home button (0x0400) held for half a second, once per hold. Called with the lock held.
    private func holdingHome(_ buttons: UInt16) -> Bool {
        guard buttons & 0x0400 != 0 else { homeSince = nil; homeSent = false; return false }
        let now = ProcessInfo.processInfo.systemUptime
        guard let since = homeSince else { homeSince = now; return false }
        guard !homeSent, now - since >= 0.5 else { return false }
        homeSent = true
        return true
    }
}

/// One game in the library and its launch profile, stored in
/// Documents/bcd-library.json (version 1). New fields must be optional so
/// older files keep decoding; unknown keys are ignored.
struct LibraryEntry: Codable, Identifiable {
    var id = UUID()
    var title: String
    /// The executable, relative to drive_c ("Games/Foo/foo.exe").
    var relativePath: String
    /// 32 or 64 from the PE header, 0 when unknown.
    var bits: Int
    /// A user-chosen cover in Documents/bcd-art/.
    var coverFile: String?
    /// The Steam store app this game was matched to (Game details › Find on
    /// Steam). Only its public artwork is used: the library cover and the
    /// starting screen's background, when no cover file is chosen.
    var steamID: Int?
    var arguments = ""
    /// The virtual monitor's size ("WxH"): the session default a game renders
    /// for (GuestDisplay.configureSessionDefault), and the Desktop entry's
    /// desktop size. ml1172: new entries default to this screen's shape at
    /// 1280x720's pixel count (ResolutionChoices: 1408x648 on a 19.5:9 iPhone,
    /// as before, and 1152x800 on an 11-inch iPad, where a fixed 1408x648 left bars).
    var resolution = ResolutionChoices.defaultValue
    /// How the monitor is scaled to the screen (DisplayMode raw value; nil = Fit).
    var display: String?
    /// FPS limit: 1 = 60, 3 = 30, 4 = 40, 0 = display maximum, 2 = uncapped (madeira_set_vsync_locked).
    var fpsMode = 1
    /// FEX's X87ReducedPrecision for this game. Off by default, as in FEX; only
    /// an explicit choice exports FEX_X87REDUCEDPRECISION=1.
    var reducedX87 = false
    var liveLogs = false
    var performance = false
    var touchControls = false
    var controls: [TouchControl]?
    /// The named layout (TouchControlPresets.swift) `controls` came from, so the
    /// Session menu shows it and an edit is written back to it; nil for controls
    /// no layout holds. Older files have none.
    var controlLayout: String?
    var lastPlayed: Date?
    var graphicsAPI: String?
    var folderBytes: Int64?
    var metadataChecked: Date?
    var metadataRevision: Int?
    var overlayFields: [String]?
    /// The Wine desktop (explorer and services in a virtual desktop).
    var desktop: Bool?
    /// Touch controls' opacity (0.15...1, nil = 0.7) and overall size (0.5...2,
    /// nil = 1) in this game's sessions.
    var controlOpacity: Double?
    var controlSize: Double?
    // ml1163: how a game starts. All optional, so older library files decode and
    // entries that never set them start as before.
    /// "desktop": the program runs inside the Wine desktop, as explorer.exe's
    /// first child (explorer.exe /desktop=shell,<resolution> "<exe>" args), so
    /// every window shows. nil or "direct": the program is Wine's first process,
    /// with no desktop; only its small windows (launchers, message boxes) are
    /// drawn over the game.
    var launchMode: String?
    /// The working directory, a C:\ path (exported as MADEIRA_WORKDIR); nil or
    /// empty = the program's own folder (Steam's for "The game").
    var workingDirectory: String?
    /// Start services.exe (the SCM, and through it rpcss: out-of-process COM,
    /// which Steam-style launchers need) before the program, from a generated
    /// C:\bcd-games\<id>.bat (servicesScript).
    var startServices: Bool?
    /// How a physical controller reaches this game: nil, the game's own support
    /// (XInput, as before); "dinput", XInput and a DirectInput joystick of the same
    /// pad (for games older than XInput; a game reading both APIs sees two
    /// controllers); "keys", keyboard and mouse (PadKeyboardMouse: the pad
    /// presses keys and moves the mouse, the game sees no controller). For games
    /// without controller support. Optional, so older files decode.
    var controllerMode: String?
    /// Keyboard-and-mouse mode: what each controller input does in this game
    /// (PadBindings.buttonNames plus "LS"/"RS" → key, mouse button, key stick or
    /// .none), on top of the layout's bindings and the built-in template. Only
    /// inputs the player changed are stored. Optional, so older files decode.
    var controllerBinds: [String: ControlAction]?
    /// Keyboard-and-mouse mode: vertical speed of the right-stick mouse relative
    /// to horizontal (PadBindings.mouseVertical); nil = 1.
    var padMouseVertical: Double?
    /// Processors reported to Windows code in this game's sessions
    /// (MADEIRA_CPU_COUNT, ntdll); nil = automatic.
    var cpuCount: Int?
    /// D3D9 anisotropic filtering limit (DXMT_D9_ANISO_LIMIT: 1, 2, 4 or 8);
    /// nil = the application's own choice.
    var anisotropyLimit: Int?
    /// "Report an NVIDIA GPU": DXGI names NVIDIA as the vendor (GeForce RTX
    /// 3060, 10DE:2544), DXMT's NVAPI answers, and win32u registers the same
    /// display adapter, for games that refuse a GPU without a known vendor's
    /// driver. nil = off.
    var reportNVIDIA: Bool?
    /// A Steam game (SteamGames.swift): Madeira Dock starts it by this App ID
    /// through Valve's client, with Steam's default launch option.
    /// `relativePath` is then its install folder, relative to drive_c.
    var steamAppID: Int?
    /// How a Steam game starts (Game details › Steam › Start with): nil is Madeira
    /// Dock, the default; "game" is the game's own program in Wine, without Steam
    /// (SteamDirectStart).
    var steamStart: String?
    /// "The game": the program, relative to the install folder ("bin/game.exe"), its
    /// arguments, and its working folder (relative to the install folder; nil: the
    /// program's own folder, "": the install folder), from Steam's launch configuration
    /// for the app ("steam"), the Program picker ("choice") or the folder's only
    /// program ("only").
    var steamProgram: String?
    var steamProgramArguments: String?
    var steamProgramFolder: String?
    var steamProgramSource: String?
    /// The install folder, build and picked program a Steam game's `bits` and
    /// `graphicsAPI` were read for, and whether Steam's launch configuration was
    /// cached (LibraryModel.refreshSteamMetadata); any change reads them again.
    var steamMetadataInstall: String?
    /// Fastsync's per-game switches, used only while Settings › Sync engine is
    /// Fastsync: "Fast synchronization" (nil = on; off gives this game Wine's
    /// standard sync) and "Fast semaphore waits" (nil = off). Optional, so older
    /// library files decode; the fork's files carry the same keys.
    var fastSync: Bool?
    var semaphoreFastPath: Bool?
    /// This game's own lines in bcd.cfg's syntax (Game details › This game's
    /// config). At launch a key set here wins over bcd.cfg, env.NAME lines are
    /// exported after bcd.cfg's and dxmt options are added to its own
    /// (MadeiraConfig.applyGame). nil: none.
    var config: String?
    /// AVX and AVX2 for this game (FEX's 128-bit AVX emulation, MADEIRA_FEX_AVX);
    /// nil = off, FEX's iOS default.
    var avx: Bool?
    /// Experimental MetalFX frame interpolation between the game's frames
    /// (DXMT's present path, MADEIRA_FRAMEGEN); nil = off.
    var frameGeneration: Bool?
    /// MetalFX spatial upscaling factor (Display › MetalFX upscaling: 1.5 or 2;
    /// nil = off), passed on as this game's `metalfx-upscale` line.
    var metalFXUpscale: Double?

    /// What MadeiraConfig.applyGame writes for this game: the lines its pickers
    /// stand for, then its own config, which wins where both set a key.
    var gameConfigText: String? {
        var lines: [String] = []
        if let metalFXUpscale { lines.append("metalfx-upscale = \(metalFXUpscale)") }
        if let config, !config.isEmpty { lines.append(config) }
        return lines.isEmpty ? nil : lines.joined(separator: "\n")
    }

    var displayMode: DisplayMode { display.flatMap(DisplayMode.init(rawValue:)) ?? .fit }

    var launchArguments: String {
        if desktop == true { return "/desktop=shell,\(resolution) C:\\windows\\system32\\services.exe" }
        return launchCommand.args
    }

    /// ml1163: a .bat or .cmd target runs through cmd.exe. For "The game" (a Steam
    /// game started as its own program) the target is that program.
    var isBatch: Bool {
        let path = launchRelativePath.lowercased()
        return path.hasSuffix(".bat") || path.hasSuffix(".cmd")
    }
    /// ml1163's launch options apply to what the library starts itself: a game you
    /// added, or a Steam game's "The game". A Steam game started through Madeira
    /// Dock has Dock's own desktop and command (ContentView.startDock).
    var usesLaunchOptions: Bool { desktop != true && (steamAppID == nil || startsSteamGameDirectly) }
    /// ml1163: a game (not the Desktop entry) started inside the Wine desktop.
    var runsInDesktop: Bool { usesLaunchOptions && launchMode == "desktop" }
    /// The program's own arguments: Steam's launch configuration for "The game",
    /// else the entry's Launch arguments.
    var programArguments: String { startsSteamGameDirectly ? (steamProgramArguments ?? "") : arguments }
    /// ml1163: the directory the program starts in (MADEIRA_WORKDIR): the chosen
    /// working folder, else Steam's for "The game", else the program's own folder.
    /// A desktop-mode game inherits explorer's directory and a .bat runs as
    /// cmd.exe, whose own folder is system32, so configureLaunch exports it
    /// whenever what starts lives elsewhere.
    var launchDirectory: String {
        let chosen = (workingDirectory ?? "").trimmingCharacters(in: .whitespaces)
        if !chosen.isEmpty { return chosen }
        return steamWorkingWindowsPath ?? Self.folder(of: launchWindowsPath)
    }
    /// C:\a\b\x.exe -> C:\a\b (C:\ for a file in the root).
    static func folder(of windowsPath: String) -> String {
        guard let i = windowsPath.lastIndex(of: "\\") else { return "C:\\" }
        let dir = String(windowsPath[..<i])
        return dir.count <= 2 ? dir + "\\" : dir
    }
    /// drive_c on the host. LibraryModel.drive is the same folder; this struct
    /// stands alone so the host tests can compile it.
    static var hostDrive: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("wine/drive_c", isDirectory: true)
    }
    /// C:\a\b -> <prefix>/drive_c/a/b; nil for another drive or a ".." element.
    static func hostURL(ofWindowsPath path: String) -> URL? {
        let p = path.trimmingCharacters(in: .whitespaces)
        guard p.count >= 3, p.prefix(3).uppercased() == "C:\\" else { return nil }
        let parts = p.dropFirst(3).split(separator: "\\").map(String.init)
        guard !parts.contains("..") else { return nil }
        return parts.reduce(hostDrive) { $0.appendingPathComponent($1) }
    }
    /// ml1163: where startServices writes its batch file, one per entry.
    var servicesScriptPath: String { "C:\\bcd-games\\\(id.uuidString).bat" }
    /// ml1163: the services batch, CRLF. services.exe is STARTed; then a .bat is
    /// CALLed so it runs in this console, and an exe is STARTed so cmd exits and
    /// its console closes straight away (the pattern of Steam's batch). nil
    /// unless startServices.
    var servicesScript: String? {
        guard usesLaunchOptions, startServices == true else { return nil }
        func quoted(_ s: String) -> String { "\"\(s)\"" }
        // Text put into the batch keeps one line each (a line break in the arguments
        // or the title would start another command), and its '%' is doubled once per
        // expansion: cmd expands '%' in a batch file, so C:\Games\100% Juice became
        // C:\Games\100 Juice and "%~" in a title was a syntax error that stopped the
        // whole batch; CALL expands its line a second time.
        func batchText(_ s: String, expansions: Int = 1) -> String {
            var text = s.replacingOccurrences(of: "\r", with: " ").replacingOccurrences(of: "\n", with: " ")
            for _ in 0..<expansions { text = text.replacingOccurrences(of: "%", with: "%%") }
            return text
        }
        let expansions = isBatch ? 2 : 1   // the program's line: CALL for a .bat
        let extra = batchText(programArguments.trimmingCharacters(in: .whitespaces), expansions: expansions)
        let target = quoted(batchText(launchWindowsPath, expansions: expansions))
        let run = isBatch ? "call \(target)" : "start \"\" \(target)"
        return [
            "@echo off",
            "rem Generated by BCD (ml1163) for \(batchText(title)); rewritten at every launch.",
            "start \"\" \"C:\\windows\\system32\\services.exe\"",
            "cd /d \(quoted(batchText(launchDirectory)))",
            extra.isEmpty ? run : run + " " + extra,
        ].joined(separator: "\r\n") + "\r\n"
    }

    /// ml1163: what a game starts (the Desktop entry keeps its own command
    /// above). MADEIRA_ARGS goes through
    /// WineProcessBridge's quote-aware tokenizer; Wine re-quotes an argument
    /// with spaces.
    ///   Wine desktop: explorer.exe /desktop=shell,WxH "<exe>" args, or cmd /c "x.bat" args
    ///   direct:       the exe with its arguments, or cmd.exe /c "x.bat" args
    /// With startServices the target is the services batch, which carries the
    /// arguments itself. The exe is launchWindowsPath: "The game"'s program for
    /// a Steam game started as its own program.
    private var launchCommand: (exe: String, args: String) {
        func quoted(_ s: String) -> String { "\"\(s)\"" }
        var path = launchWindowsPath
        var extra = programArguments.trimmingCharacters(in: .whitespaces)
        var batch = isBatch
        if servicesScript != nil { path = servicesScriptPath; extra = ""; batch = true }
        func withExtra(_ s: String) -> String { extra.isEmpty ? s : s + " " + extra }
        if runsInDesktop {
            return ("explorer.exe", "/desktop=shell,\(resolution) " + withExtra(batch ? "cmd /c \(quoted(path))" : quoted(path)))
        }
        if batch { return ("C:\\windows\\system32\\cmd.exe", withExtra("/c \(quoted(path))")) }
        return (path, programArguments)   // upstream's direct launch, arguments verbatim
    }
    /// The details page's "command that runs" line: the program's file name and
    /// its arguments, as upstream shows a direct start; ml1163: what starts it
    /// otherwise (explorer.exe or cmd.exe with its whole command), and with the
    /// services batch also what that batch starts.
    var commandPreview: String {
        func name(_ path: String) -> String { path.split(separator: "\\").last.map(String.init) ?? path }
        let command = launchCommand
        let program = ([name(launchWindowsPath)] + (programArguments.isEmpty ? [] : [programArguments])).joined(separator: " ")
        if command.exe == launchWindowsPath { return program }
        let line = name(command.exe) + " " + command.args
        return servicesScript == nil ? line : line + "\n(the batch runs services.exe first, then " + program + ")"
    }

    /// A Steam game that starts as its own program ("Start with: The game").
    var startsSteamGameDirectly: Bool { steamAppID != nil && steamStart == "game" }
    /// What a launch starts, relative to drive_c: "The game"'s program inside the
    /// install folder, else `relativePath`.
    var launchRelativePath: String {
        guard startsSteamGameDirectly, let program = steamProgram, !program.isEmpty else { return relativePath }
        return relativePath + "/" + program
    }
    var launchWindowsPath: String { "C:\\" + launchRelativePath.replacingOccurrences(of: "/", with: "\\") }
    /// "The game"'s working folder as a Windows path, or nil for the program's own folder.
    var steamWorkingWindowsPath: String? {
        guard startsSteamGameDirectly, let folder = steamProgramFolder else { return nil }
        return "C:\\" + (folder.isEmpty ? relativePath : relativePath + "/" + folder).replacingOccurrences(of: "/", with: "\\")
    }

    static let desktopID = UUID(uuidString: "AF046C35-C32A-497B-92BC-0BBD14F8CB61")!
    static var desktopEntry: LibraryEntry {
        var entry = LibraryEntry(title: "Desktop", relativePath: "windows/system32/explorer.exe", bits: 64)
        entry.id = desktopID; entry.desktop = true; entry.graphicsAPI = "Wine desktop"
        return entry
    }

    var windowsPath: String { "C:\\" + relativePath.replacingOccurrences(of: "/", with: "\\") }

    /// The vsync mode to apply: a saved 30 or 40 FPS limit runs as 60 when it is
    /// not offered (DXMT without that cap would present uncapped).
    var effectiveFPSMode: Int32 { ProMotionIntent.supportedMode(fpsMode) }

    func validate() throws {
        let size = resolution.split(separator: "x").compactMap { Int($0) }
        guard size.count == 2, (320...4096).contains(size[0]), (240...4096).contains(size[1]),
              (0...4).contains(fpsMode), !arguments.contains("\0"), !windowsPath.contains("\0"),
              !launchArguments.contains("\0"), !launchWindowsPath.contains("\0"),
              launchWindowsPath.utf8.count < 1024, (steamWorkingWindowsPath?.utf8.count ?? 0) < 512 else {
            throw LibraryError.message("A display or argument value saved for this game is not valid.")
        }
        var quoted = false, inToken = false, tokens = 0
        for character in launchArguments {
            if character == "\"" { quoted.toggle() }
            if !quoted && (character == " " || character == "\t") { inToken = false }
            else if !inToken { tokens += 1; inToken = true }
        }
        // WineProcessBridge takes at most 64 arguments in 4 KB.
        guard launchArguments.utf8.count < 4096 else { throw LibraryError.message("The full launch command is longer than BCD can pass on.") }
        guard !quoted, tokens <= 64 else { throw LibraryError.message("Close every double quote you open, and keep the launch to 64 arguments or fewer.") }
        // build/madeira_cfg.h reads at most 64 KB of a file.
        guard (config?.utf8.count ?? 0) < 60_000, config?.contains("\0") != true else {
            throw LibraryError.message("The config for this game is over the size limit.")
        }
        // ml1163: the launch options.
        guard launchMode == nil || launchMode == "desktop" || launchMode == "direct" else {
            throw LibraryError.message("The launch mode saved for this game is not valid.")
        }
        if usesLaunchOptions, let chosen = workingDirectory?.trimmingCharacters(in: .whitespaces), !chosen.isEmpty {
            // WineProcessBridge maps MADEIRA_WORKDIR onto drive_c (C:\ only), and the
            // services batch quotes it, so only an existing C:\ folder without
            // quotes is accepted.
            var isFolder: ObjCBool = false
            guard !chosen.contains("\""), !chosen.contains("\0"), chosen.utf8.count < 500,
                  let url = Self.hostURL(ofWindowsPath: chosen),
                  FileManager.default.fileExists(atPath: url.path, isDirectory: &isFolder), isFolder.boolValue else {
                throw LibraryError.message("Pick a working folder that exists on drive C:, for example C:\\Games\\Some Game.")
            }
        }
        // validate() is the last step of a launch that can refuse it
        // (ContentView.launchLibraryEntry), so the services batch is written
        // here: a batch that cannot be written stops the launch with a message.
        if let script = servicesScript {
            guard let url = Self.hostURL(ofWindowsPath: servicesScriptPath) else { return }
            do {
                try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
                try script.write(to: url, atomically: true, encoding: .utf8)
            } catch {
                throw LibraryError.message("\(servicesScriptPath) could not be saved: \(error.localizedDescription)")
            }
        }
    }

    /// Runs on the launch worker, before the JIT pool is taken.
    func applyEnvironment() {
        configureLaunch()
        // Unset unless chosen: FEX's own default then applies, as for any other launch.
        if reducedX87 { setenv("FEX_X87REDUCEDPRECISION", "1", 1) } else { unsetenv("FEX_X87REDUCEDPRECISION") }
        // Exported only when chosen: unset keeps the engine's own default (and any
        // bcd.cfg setting), as before these choices existed.
        // Unset otherwise, so a previous game's choice (a launch that died before the
        // session-end unset) never beats bcd.cfg for this one.
        if let cpuCount, (1..<64).contains(cpuCount) { setenv("MADEIRA_CPU_COUNT", String(cpuCount), 1) }
        else { unsetenv("MADEIRA_CPU_COUNT") }
        // "dinput": the host pad also as a DirectInput joystick (wine/dlls/dinput/joystick_ios.c,
        // off by default because a game reading both APIs would see two controllers).
        // Exported only for that choice, and it wins over bcd.cfg's MADEIRA_DINPUT_PAD
        // (ml1240: in ml1184's per-launch list, unset when the session ends); the cfg's
        // own value still applies to the other choices.
        if GamepadInput.keyboardMouseAvailable, controllerMode == "dinput" { setenv("MADEIRA_DINPUT_PAD", "1", 1) }
        else if MadeiraConfig.get("env.MADEIRA_DINPUT_PAD") == nil { unsetenv("MADEIRA_DINPUT_PAD") }
        if let anisotropyLimit, [1, 2, 4, 8].contains(anisotropyLimit) { setenv("DXMT_D9_ANISO_LIMIT", String(anisotropyLimit), 1) }
        else { unsetenv("DXMT_D9_ANISO_LIMIT") }
        // Set or unset, so a previous session's choice never stays.
        if avx == true { setenv("MADEIRA_FEX_AVX", "1", 1) } else { unsetenv("MADEIRA_FEX_AVX") }
        if frameGeneration == true { setenv("MADEIRA_FRAMEGEN", "1", 1) } else { unsetenv("MADEIRA_FRAMEGEN") }
        // "Report an NVIDIA GPU": DXMT's vendor extension (DXGI vendor 10DE,
        // NVAPI), with DXGI's output carrying user32's monitor and mode list so
        // the game finds its monitor on the adapter. The device id goes into
        // DXMT_CONFIG (ContentView); win32u reads DXMT_ENABLE_NVEXT for the
        // registry adapter. Set or unset, so one game's choice never leaks into
        // the next session; when set, it wins over bcd.cfg's lines for the
        // same keys (ml1184's per-launch list in WineProcessBridge.m).
        if reportNVIDIA == true {
            setenv("DXMT_ENABLE_NVEXT", "1", 1)
            setenv("DXMT_WSI_MONITOR_IDENTITY", "1", 1)
            setenv("DXMT_WSI_MODE_TABLE", "1", 1)
        } else {
            unsetenv("DXMT_ENABLE_NVEXT")
            unsetenv("DXMT_WSI_MONITOR_IDENTITY")
            unsetenv("DXMT_WSI_MODE_TABLE")
        }
        // Fastsync's per-game switches, only when Settings chose Fastsync; with Madsync
        // (the default) or Wine's standard sync nothing is exported here.
        if SyncEngine.current == .fastsync {
            let mode = MadeiraConfig.get("env.MADEIRA_FASTSYNC") ?? "auto"
            setenv("MADEIRA_FASTSYNC", fastSync == false ? "0" : mode, 1)
            // Only a game's own choice; otherwise bcd.cfg's env.MADEIRA_FASTSYNC_SEM applies.
            if let sem = semaphoreFastPath { setenv("MADEIRA_FASTSYNC_SEM", sem ? "1" : "0", 1) }
            else { unsetenv("MADEIRA_FASTSYNC_SEM") }
        }
        madeira_set_vsync_locked(effectiveFPSMode)
        // This game's own lines; a launch without any unsets the previous game's.
        do {
            let pairs = try MadeiraConfig.applyGame(gameConfigText)
            if !pairs.isEmpty {
                LogStore.shared.log("[game-cfg] " + pairs.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: " "))
            }
        } catch {
            LogStore.shared.log("[game-cfg] this game's config could not be written: \(error.localizedDescription)")
        }
        fputs("[frontend] launch profile applied\n", stderr)
        if usesLaunchOptions {
            fputs("[library] ml1163 start=\(runsInDesktop ? "desktop" : "direct") batch=\(isBatch ? 1 : 0) services=\(servicesScript != nil ? 1 : 0) cwd=\(launchDirectory)\n", stderr)
        }
        LogStore.shared.log("[display-shape] resolution=\(resolution) mode=\(displayMode.rawValue)")
    }

    /// What the bridge starts. Set on the main thread before the session begins.
    func configureLaunch() {
        // "The game"'s identity and the launch's working folder, for this launch only
        // (the bridge reads and clears them); every other launch starts without them.
        unsetenv("MADEIRA_STEAM_APPID"); unsetenv("MADEIRA_STEAM_APPPATH"); unsetenv("MADEIRA_WORKDIR")
        if steamAppID != nil {
            // A Steam game through Madeira Dock: Dock has set what starts (ContentView.startDock);
            // the virtual monitor follows this entry's Resolution, as below. "The game" starts
            // its own program below, like any library game.
            if !startsSteamGameDirectly {
                GuestDisplay.configureSessionDefault(view: CGSize(width: 1280, height: 720), knob: resolution)
                return
            }
        }
        let command = desktop == true ? (exe: "explorer.exe", args: launchArguments) : launchCommand
        setenv("MADEIRA_EXE", command.exe, 1)
        setenv("MADEIRA_ARGS", command.args, 1)
        if desktop == true || runsInDesktop { setenv("MADEIRA_DESKTOP", "1", 1) } else { unsetenv("MADEIRA_DESKTOP") }
        if startsSteamGameDirectly, let steamAppID {
            // The game's own Steam identity (SteamAppId, SteamGameId, SteamAppPath = its install
            // folder) instead of the bridge's fixed one.
            setenv("MADEIRA_STEAM_APPID", String(steamAppID), 1)
            setenv("MADEIRA_STEAM_APPPATH", windowsPath, 1)
        }
        // ml1163: a game starts in launchDirectory (a chosen folder, Steam's for "The game",
        // else the program's own). It is exported when the bridge's default, the folder of
        // what it starts, differs: explorer.exe (desktop mode) and cmd.exe (a .bat, the
        // services batch) live elsewhere. The Desktop entry keeps explorer's default.
        if desktop != true, launchDirectory != Self.folder(of: command.exe) {
            setenv("MADEIRA_WORKDIR", launchDirectory, 1)
        }
        // Every session's virtual monitor takes this entry's Resolution
        // (MADEIRA_SCREEN_W/H, source "knob"); for the Desktop entry it is the
        // same size as its /desktop= argument.
        GuestDisplay.configureSessionDefault(view: CGSize(width: 1280, height: 720), knob: resolution)
    }
}

final class LibraryModel: ObservableObject {
    static let shared = LibraryModel()
    static var documents: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0] }
    static var drive: URL { documents.appendingPathComponent("wine/drive_c", isDirectory: true).resolvingSymlinksInPath() }
    @Published var entries: [LibraryEntry] = []
    @Published var current: UUID?
    @Published var activeEntry: LibraryEntry?
    @Published var menu = false
    @Published var performance = false
    @Published var liveLogs = false
    @Published var fpsMode = 1
    /// The session's controller mode (LibraryEntry.controllerMode): "keys" or nil.
    @Published var controllerMode: String? {
        didSet { if oldValue != controllerMode { applyControllerMode() } }
    }
    /// The session's binds table (LibraryEntry.controllerBinds); a change rebuilds
    /// the driver's bindings at once, so the binds page is live.
    @Published var controllerBinds: [String: ControlAction] = [:] {
        didSet { if oldValue != controllerBinds { applyControllerMode() } }
    }
    /// The session's right-stick mouse vertical speed (LibraryEntry.padMouseVertical).
    @Published var padMouseVertical: Double = 1 {
        didSet { if oldValue != padMouseVertical { applyControllerMode() } }
    }
    /// The session's touch-control opacity (the entry's Control opacity).
    @Published var opacity = 0.7
    /// The session's Aspect & scaling; MetalBackedView lays the game out with it.
    @Published var displayMode = DisplayMode.fit {
        didSet { if displayMode != oldValue { MetalBackedView.refreshDisplayMode(reason: "mode-toggle") } }
    }
    @Published var error: String?
    @Published var sessionMessage = ""
    @Published var launching = false
    @Published var overlayFields = ["FPS", "Frame time", "RAM", "Battery"]
    private var launchPresent: UInt64 = 0
    private var launchSurface: UInt64 = 0
    private var launchStarted = Date()
    /// Read by the starting screen for its elapsed-time line.
    var launchStartedAt: Date { launchStarted }
    @Published var launchSlow = false
    @Published var launchLogs = false
    private var launchDismissLogged = false
    var menuButtonRect = CGRect.zero
    var performanceRect = CGRect.zero
    /// The in-game menu and the starting screen take every touch.
    var blocksGameplayTouch: Bool { current != nil && (menu || launching) }
    private var timer: Timer?
    private var sawProcess = false
    // Why a session ended by itself (not Quit): the program the app launched
    // exited with a Windows error (wine_crash_exit_status, WineProcessBridge.m).
    // MADEIRA_EXIT_REPORT=0 returns to the library without a message.
    private var quitRequested = false
    /// madeira-bcd (round 577, GTA IV freeze): the program's exit code when the library itself ended the session
    /// because the engine never reported the program's end (watchProgramEnd); nil otherwise.
    private var endedByWatch: Int32?
    /// What watchProgramEnd logged last, so each state is logged once.
    private var endWatchNote = ""
    /// madeira-bcd (round 579, BCD Dock): the library itself ended the session because the game's last process ended
    /// with an error status and nothing of it was left (watchDockGameEnd); nothing is asked again.
    private var endedByDockGame = false
    /// madeira-bcd (round 580, BCD Dock: the launch failed and no game ran): when watchDockFailedLaunch first saw the host's failed
    /// launch with nothing of the game running; nil while that does not hold.
    private var dockFailedAt: Date?
    /// The host's failure in words, once the library itself ended the session for it (exitReport shows it).
    private var endedByDockFailure: String?
    /// What watchDockGameEnd logged last, so each state is logged once.
    private var dockEndNote = ""
    private func exitReport() -> String? {
        guard !quitRequested, MadeiraConfig.flag("MADEIRA_EXIT_REPORT") else { return nil }
        // madeira-bcd (round 580): the Dock host reported the launch failed and nothing ran; the library ended the session (watchDockFailedLaunch).
        if let words = endedByDockFailure {
            LogStore.shared.log("[exit-report] the Dock host reported the launch failed (the session-end watch)")
            return words
        }
        var status: UInt32 = 0
        guard wine_crash_exit_status(&status) != 0 else {
            // madeira-bcd (round 577): a plain nonzero exit code of a session the library ended itself
            guard let code = endedByWatch, let words = SessionEndRules.message(exitCode: code) else { return nil }
            LogStore.shared.log("[exit-report] code=\(code) (the session-end watch)")
            return words
        }
        LogStore.shared.log("[exit-report] status=0x\(String(status, radix: 16))")
        // madeira-bcd (round 580, Stellar Blade demo DBKP8Z): the game's file asked for a wider executable window, this run
        // could not make it (StikJITHelper.exeWindowWidenFailedMB), and the program ended with c0000018: it did not fit.
        // iOS lays the app's memory out anew at every start, so the same game starts after BCD is closed and opened again.
        // Text only; nothing is held back.
        if status == 0xC0000018, let mb = StikJITHelper.exeWindowWidenFailedMB {
            LogStore.shared.log("[exit-report] the executable window could not be widened to \(mb) MB in this run")
            return "The game quit with Windows error 0xC0000018 (conflicting addresses). This run of BCD could not widen the memory its program starts in to the \(mb) MB the game's settings ask for, because iOS had put something else there. Quit BCD (swipe it away in the app switcher), open it again and start the game."
        }
        let kind = status == 0xC0000005 ? " (access violation)" : status == 0xC0000017 ? " (not enough memory)" : ""
        return "The game quit with Windows error 0x\(String(status, radix: 16, uppercase: true))\(kind). To report the problem, export the diagnostic log."
    }
    private var readOnly = false
    private var metadataInFlight = Set<UUID>()
    private var steamMetadataInFlight = Set<Int>()
    private var savedControls: [TouchControl] = []
    private var savedLayout: String?
    private var savedVisible = true
    private var savedSize = 1.0
    /// The session's first frame makes the drawable's shape known (Aspect).
    private var laidOutAfterFirstPresent = false
    private struct Document: Codable { var version: Int; var entries: [LibraryEntry] }
    private var file: URL { Self.documents.appendingPathComponent("bcd-library.json") }

    private init() {
        refreshFlag()
        if FileManager.default.fileExists(atPath: file.path) {
            do {
                let doc = try JSONDecoder().decode(Document.self, from: Data(contentsOf: file))
                guard doc.version == 1 else { throw LibraryError.message("This library file uses a format this version of BCD does not read.") }
                entries = doc.entries
            } catch {
                readOnly = true
                self.error = "BCD could not open your library. The file itself was left untouched. " + error.localizedDescription
            }
        }
        resetPhoneResolution()
        applyRecommendations()
        RecommendationsSource.start(idle: { [weak self] in self?.current == nil && wine_process_is_running() == 0 }, onChange: { [weak self] in self?.applyRecommendations() })   // BCD: the online recommended settings (GameRecommendationsSource.swift): at most one check in 6 hours; a newer list reaches the library's games at once, never a running game
        // madeira-bcd: a tuned session that never came back (AutoTune.swift); off, nothing.
        if AutoTune.enabled { AutoTune.recoverUnfinishedSession() }
    }

    /// madeira-bcd: games already in the library get Madeira's recommended settings
    /// when they are due (GameRecommendationsApply.swift); the entries' own fields
    /// are saved here.
    private func applyRecommendations() {
        guard !readOnly else { return }
        var next = entries
        var changed = false
        for i in next.indices {
            if GameRecommendations.prepare(&next[i]) { changed = true }
        }
        if changed { persist(next) }
    }

    /// ml1172: every new entry used to get 1408x648, a 19.5:9 phone's shape.
    /// On a screen of another shape (an iPad: a third of it black in Fit)
    /// those entries are reset once to this device's default. Nothing tells a
    /// deliberate 1408x648 from the old default, so a game set to it on purpose
    /// is reset too; on a 19.5:9 iPhone nothing changes.
    private func resetPhoneResolution() {
        let key = "madeira.ml1172.resolution-reset"
        guard !readOnly, !UserDefaults.standard.bool(forKey: key) else { return }
        let phone = "1408x648"
        let reset = ResolutionChoices.fills(1408, 648) ? 0 : entries.filter { $0.resolution == phone }.count
        if reset > 0 {
            var next = entries
            for i in next.indices where next[i].resolution == phone { next[i].resolution = ResolutionChoices.defaultValue }
            guard persist(next) else { return }   // try again at the next start
        }
        UserDefaults.standard.set(true, forKey: key)
        LogStore.shared.log("[library] ml1172 resolution default \(ResolutionChoices.defaultValue); \(reset) entries reset from \(phone)")
    }

    private var flagAnnounced = false
    func refreshFlag() {
        guard current == nil, wine_process_is_running() == 0 else { return }
        // BCD's interface is the only interface (wave 3, 2026-10-19): the library engine is always on
        // and always owns the controller's navigation (it was the default choice until then).
        if !flagAnnounced { flagAnnounced = true; fputs("[frontend] library enabled=1\n", stderr) }
        LibraryController.shared.configure(enabled: true, ownsInput: true)
    }

    func save(_ entry: LibraryEntry) {
        guard !readOnly else { error = "BCD could not read the library file. Keep a copy or fix it before you change anything."; return }
        var next = entries
        var entry = entry
        // madeira-bcd: a game seen for the first time gets Madeira's recommended settings
        // (GameRecommendationsApply.swift): a finished Steam download, a new or saved entry.
        let isNew = !next.contains { $0.id == entry.id || (entry.steamAppID != nil && $0.steamAppID == entry.steamAppID) }
        GameRecommendations.prepare(&entry, new: isNew)
        // A Steam game has one entry: a details page opened before its card first saved
        // one (refreshSteamMetadata) updates that entry.
        if let i = next.firstIndex(where: { $0.id == entry.id }) ??
            next.firstIndex(where: { entry.steamAppID != nil && $0.steamAppID == entry.steamAppID }) {
            // A details sheet may predate an asynchronous metadata refresh.
            if (next[i].metadataChecked ?? .distantPast) > (entry.metadataChecked ?? .distantPast) {
                entry.folderBytes = next[i].folderBytes; entry.graphicsAPI = next[i].graphicsAPI
                entry.bits = next[i].bits; entry.steamMetadataInstall = next[i].steamMetadataInstall
                entry.metadataChecked = next[i].metadataChecked
                entry.metadataRevision = next[i].metadataRevision
            }
            next[i] = entry
        } else { next.append(entry) }
        persist(next)
    }
    func remove(_ id: UUID) {
        persist(entries.filter { $0.id != id })
    }
    /// A Steam game's library entry, which holds its per-game settings
    /// (SteamGames.swift). A game without one gets a new entry made from what
    /// Steam installed; it is saved when its details page closes or it is
    /// played. An existing entry follows the install folder Steam records.
    func steamEntry(_ game: DockGame, title: String? = nil) -> LibraryEntry {
        let folder = game.library + "/common/" + game.installDir
        if var entry = entries.first(where: { $0.steamAppID == game.id }) {
            entry.relativePath = folder
            return entry
        }
        var entry = LibraryEntry(title: title ?? game.name, relativePath: folder, bits: 0)
        entry.steamAppID = game.id
        entry.folderBytes = SteamInstallFiles.sizeOnDisk(appID: game.id, steamApps: Self.drive.appendingPathComponent(game.library, isDirectory: true))
        return entry
    }
    /// A Steam download finished (SteamOwnedLibrary): the game gets its library
    /// entry, or an existing one keeps its title, artwork and settings.
    func upsertSteam(_ game: DockGame, title: String) {
        guard !readOnly else { return }
        var entry = steamEntry(game, title: title)
        entry.folderBytes = SteamInstallFiles.sizeOnDisk(appID: game.id, steamApps: Self.drive.appendingPathComponent(game.library, isDirectory: true)) ?? entry.folderBytes
        save(entry)
    }
    /// A Steam game was uninstalled: its entry goes with its files.
    func removeSteam(appID: Int) {
        guard entries.contains(where: { $0.steamAppID == appID }) else { return }
        persist(entries.filter { $0.steamAppID != appID })
    }
    @discardableResult
    private func persist(_ next: [LibraryEntry]) -> Bool {
        guard !readOnly else { return false }
        do {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(Document(version: 1, entries: next)).write(to: file, options: .atomic)
            entries = next
            return true
        } catch { self.error = "Saving the library failed: " + error.localizedDescription; return false }
    }

    /// Install size and graphics API, at most once a day per entry.
    @MainActor
    func refreshMetadata(_ id: UUID) async {
        let revision = 12
        guard !metadataInFlight.contains(id), let entry = entries.first(where: { $0.id == id }), entry.desktop != true,
              entry.metadataRevision != revision || Date().timeIntervalSince(entry.metadataChecked ?? .distantPast) > 86400,
              let url = try? Self.executable(entry.relativePath) else { return }
        metadataInFlight.insert(id)
        defer { metadataInFlight.remove(id) }
        let result = await LibraryMetadataScanner.shared.scan(url, drive: Self.drive)
        guard !Task.isCancelled, var updated = entries.first(where: { $0.id == id }) else { return }
        updated.folderBytes = result.bytes
        if let api = result.api { updated.graphicsAPI = api }
        updated.metadataChecked = Date(); updated.metadataRevision = revision; save(updated)
        fputs("[library-metadata] install scan api=\(updated.graphicsAPI ?? "unknown") bytes=\(result.bytes ?? -1)\n", stderr)
    }

    /// An installed Steam game's library pills (SteamGames.swift): 32-bit or 64-bit and
    /// the graphics API of the program "Start with: The game" would start
    /// (SteamDirectStart.program), and the install size from Steam's install record.
    /// Read off the main thread once per install folder, build and picked program
    /// (again when Steam's launch configuration arrives, or after a day while no
    /// program is known) and kept on the game's entry.
    @MainActor
    func refreshSteamMetadata(_ game: DockGame, title: String) async {
        let revision = 1
        guard !readOnly, game.installed, !steamMetadataInFlight.contains(game.id) else { return }
        steamMetadataInFlight.insert(game.id)
        defer { steamMetadataInFlight.remove(game.id) }
        let drive = Self.drive
        let folder = game.library + "/common/" + game.installDir
        let root = drive.appendingPathComponent(folder, isDirectory: true)
        let steamApps = drive.appendingPathComponent(game.library, isDirectory: true)
        let record = await Task.detached(priority: .utility) {
            (build: SteamInstallFiles.buildID(appID: game.id, steamApps: steamApps),
             size: SteamInstallFiles.sizeOnDisk(appID: game.id, steamApps: steamApps))
        }.value
        let stored = entries.first { $0.steamAppID == game.id }
        let picked = stored?.steamProgramSource == "choice" ? stored?.steamProgram : nil
        let known = SteamOwnedLibrary.shared.game(game.id)?.launches != nil
        let install = "\(folder)#\(record.build ?? 0)#\(picked ?? "")#\(known ? 1 : 0)"
        if let stored, stored.steamMetadataInstall == install, stored.metadataRevision == revision,
           stored.bits != 0 || Date().timeIntervalSince(stored.metadataChecked ?? .distantPast) < 86400 { return }
        // Steam's launch configuration is asked for only when no picked program is installed.
        let kept = await Task.detached(priority: .utility) { picked.flatMap { SteamDirectStart.onDisk($0, in: root, directory: false) } }.value
        let options = kept == nil ? await SteamOwnedLibrary.shared.launchOptions(appID: game.id) : nil
        let program = await Task.detached(priority: .utility) { () -> (url: URL, bits: Int, api: String?)? in
            guard let path = SteamDirectStart.program(picked: kept, options: options, installFolder: root),
                  let inspected = try? LibraryModel.inspect(root.appendingPathComponent(path)) else { return nil }
            return (root.appendingPathComponent(path), inspected.bits, inspected.graphicsAPI)
        }.value
        var api = program?.api
        if let program, let scanned = await LibraryMetadataScanner.shared.scan(program.url, drive: drive, countBytes: false).api { api = scanned }
        guard !Task.isCancelled else { return }
        var updated = entries.first { $0.steamAppID == game.id } ?? LibraryEntry(title: title, relativePath: folder, bits: 0)
        updated.steamAppID = game.id
        updated.relativePath = folder
        updated.bits = program?.bits ?? 0
        updated.graphicsAPI = api
        updated.folderBytes = record.size ?? updated.folderBytes
        updated.steamMetadataInstall = install
        updated.metadataChecked = Date(); updated.metadataRevision = revision
        save(updated)
        LogStore.shared.log("[steam-games] metadata app=\(game.id) bits=\(updated.bits) api=\(api ?? "unknown")")
    }

    /// What an entry can start: an executable, or (ml1163) a batch file run through cmd.exe.
    static let programExtensions: Set<String> = ["exe", "bat", "cmd"]
    static func executable(_ relative: String) throws -> URL {
        let url = drive.appendingPathComponent(relative).resolvingSymlinksInPath().standardizedFileURL
        guard ExternalGameStorage.shared.relativePath(for: url, drive: drive) != nil, programExtensions.contains(url.pathExtension.lowercased()),
              FileManager.default.fileExists(atPath: url.path) else {
            throw LibraryError.message("Pick a program in drive_c: an .exe, .bat or .cmd file.")
        }
        return url
    }
    static func inspect(_ url: URL) throws -> LibraryEntry {
        guard let relative = ExternalGameStorage.shared.relativePath(for: url, drive: drive) else {
            throw LibraryError.message("Only programs inside drive_c can be added.")
        }
        if ["bat", "cmd"].contains(url.pathExtension.lowercased()) {
            // ml1163: a batch file has no PE header. Like every entry it starts
            // directly by default, as upstream starts every game; the details page
            // warns that Wine then stops when cmd.exe, its first process, exits.
            return LibraryEntry(title: url.deletingPathExtension().lastPathComponent.replacingOccurrences(of: "_", with: " "),
                                relativePath: relative, bits: 0)
        }
        let h = try FileHandle(forReadingFrom: url); defer { try? h.close() }
        let dos = try h.read(upToCount: 64) ?? Data()
        guard dos.count == 64, dos[0] == 0x4d, dos[1] == 0x5a else { throw LibraryError.message("This file is not a Windows program.") }
        let offset = (0..<4).reduce(UInt64(0)) { $0 | (UInt64(dos[60 + $1]) << ($1 * 8)) }
        guard offset >= 64, offset < 16 * 1024 * 1024 else { throw LibraryError.message("The program's header is not valid.") }
        try h.seek(toOffset: offset)
        let pe = try h.read(upToCount: 6) ?? Data()
        guard pe.count == 6, Array(pe.prefix(4)) == [0x50, 0x45, 0, 0] else { throw LibraryError.message("The file has no PE header.") }
        let machine = Int(pe[4]) | Int(pe[5]) << 8
        guard machine == 0x14c || machine == 0x8664 else { throw LibraryError.message("BCD runs x86 and x64 programs only.") }
        let name = url.deletingPathExtension().lastPathComponent.replacingOccurrences(of: "_", with: " ")
        var entry = LibraryEntry(title: name, relativePath: relative, bits: machine == 0x14c ? 32 : 64)
        entry.graphicsAPI = graphicsImports(url)
        return entry
    }

    // Read the PE import directory, rather than guessing from the executable's name.
    static func apiNames(_ imports: [String]) -> Set<String> {
        var levels = Set<String>()
        for name in imports {
            switch name {
            case "ddraw.dll": levels.insert("DirectDraw")
            case "d3d8.dll": levels.insert("D3D8")
            case "d3d9.dll": levels.insert("D3D9")
            case "d3d10.dll", "d3d10_1.dll": levels.insert("D3D10")
            case "d3d11.dll": levels.insert("D3D11")
            case "d3d12.dll": levels.insert("D3D12")
            case "opengl32.dll": levels.insert("OpenGL")
            case "vulkan-1.dll": levels.insert("Vulkan")
            default: break
            }
        }
        return levels
    }
    static func graphicsImports(_ url: URL) -> String? {
        let levels = apiNames(importNames(url))
        return levels.isEmpty ? nil : levels.sorted().joined(separator: " / ")
    }
    static func importNames(_ url: URL) -> [String] {
        guard let h = try? FileHandle(forReadingFrom: url) else { return [] }
        defer { try? h.close() }
        func read(_ offset: UInt64, _ count: Int) -> Data {
            do { try h.seek(toOffset: offset); return try h.read(upToCount: count) ?? Data() } catch { return Data() }
        }
        func u32(_ data: Data, _ offset: Int) -> UInt32 {
            guard offset >= 0, offset + 4 <= data.count else { return 0 }
            return (0..<4).reduce(0) { $0 | UInt32(data[offset + $1]) << ($1 * 8) }
        }
        let dos = read(0, 64); guard dos.count == 64, dos[0] == 0x4d, dos[1] == 0x5a else { return [] }
        let base = UInt64(u32(dos, 60)); guard base < 16 * 1024 * 1024 else { return [] }
        let header = read(base, 264); guard header.count == 264, u32(header, 0) == 0x4550 else { return [] }
        let sections = Int(header[6]) | Int(header[7]) << 8
        let optSize = Int(header[20]) | Int(header[21]) << 8
        guard sections <= 96, optSize >= 120 else { return [] }
        let pe64 = header[24] == 0x0b && header[25] == 2
        guard header[24] == 0x0b, header[25] == 1 || pe64 else { return [] }
        let imports = u32(header, pe64 ? 144 : 128)
        let delayed = optSize >= (pe64 ? 224 : 208) ? u32(header, pe64 ? 240 : 224) : 0
        let table = read(base + 24 + UInt64(optSize), sections * 40)
        func fileOffset(_ rva: UInt32) -> UInt64? {
            guard table.count == sections * 40 else { return nil }
            for index in 0..<sections {
                let i = index * 40, va = u32(table, index * 40 + 12), size = u32(table, index * 40 + 16)
                if rva >= va, rva - va < size { return UInt64(u32(table, i + 20)) + UInt64(rva - va) }
            }
            return nil
        }
        var names: [String] = []
        for (rva, stride, nameField) in [(imports, 20, 12), (delayed, 32, 4)] {
            guard rva != 0, let start = fileOffset(rva) else { continue }
            for i in 0..<256 {
                let descriptor = read(start + UInt64(i * stride), stride)
                guard descriptor.count == stride else { break }
                var nameRVA = u32(descriptor, nameField); if nameRVA == 0 { break }
                if stride == 32 && u32(descriptor, 0) & 1 == 0 {
                    let imageBase = u32(header, 52)
                    guard !pe64, nameRVA >= imageBase else { continue }
                    nameRVA -= imageBase
                }
                guard let offset = fileOffset(nameRVA) else { continue }
                let data = read(offset, 128)
                let name = String(decoding: data.prefix(while: { $0 != 0 }), as: UTF8.self).lowercased()
                names.append(name)
            }
        }
        return names
    }

    // MARK: session

    /// Wine sessions started in this app run. A second one cannot start in the
    /// same process: the wineserver's permanent objects from the first session
    /// are still there and init_registry aborts on "\Registry". Madeira asks for
    /// a restart instead. MADEIRA_ONE_SESSION_PER_RUN=0 lets the launch go ahead.
    static var sessionsThisRun = 0
    static let restartMessage = "BCD can start one game each time it opens. To play another, swipe BCD away in the app switcher and open it again."
    @Published var restartNotice: String?
    /// CS_DEBUGGED is set but no debugger is attached (JIT was enabled outside
    /// Madeira): the text of the alert that offers Madeira's own Enable JIT.
    @Published var jitNotice: String?
    /// Play is enabling JIT before starting this entry (ContentView.jitReadyForLaunch):
    /// its Play button reads Starting JIT, with a spinner, until JIT is on or fails.
    @Published var startingJIT: UUID?
    /// A Steam game's saves may not be the latest (SteamOwnedLibrary.cloudHold):
    /// the alert Play shows before starting it.
    struct CloudNotice: Equatable {
        enum Kind { case syncing, unchecked, conflict }
        var appID: Int
        var kind: Kind
        var title: String
        var message: String
    }
    @Published var cloudNotice: CloudNotice?
    /// Starts the game the notice is about again.
    var cloudRetry: (() -> Void)?
    /// The game (App ID) allowed to start once without the check ("Launch anyway").
    var cloudBypass: Int?
    /// The entry whose details page the library should open (the notice's "Choose").
    @Published var showDetail: UUID?

    /// `remember: false` runs a session that is not a library entry (a Madeira
    /// Dock start): it is neither added to the library nor stamped as played.
    /// `dock`: the game a Madeira Dock start launches (DockStartScreen).
    func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil) {
        wine_exit_status_reset()
        quitRequested = false
        endedByWatch = nil; endWatchNote = ""
        endedByDockGame = false; dockEndNote = ""; dockFailedAt = nil; endedByDockFailure = nil
        // madeira-bcd: auto-tune's session marker (AutoTune.swift); off, nothing.
        if AutoTune.enabled { AutoTune.sessionBegan(entry, remember: remember, dockWithoutEntry: dock != nil && !remember) }
        LibraryController.shared.configure(enabled: true, ownsInput: false)
        Self.sessionsThisRun += 1
        launchPresent = madeira_get_present_count(); launchStarted = Date(); launchSlow = false; launchLogs = entry.liveLogs
        launchSurface = winios_surface_present_count()
        MetalBackedView.presentCountAtLaunch = launchPresent; laidOutAfterFirstPresent = false
        launching = true; overlayFields = entry.overlayFields ?? ["FPS", "Frame time", "RAM", "Battery"]
        displayMode = entry.displayMode
        activeEntry = entry; current = entry.id; menu = false; performance = entry.performance; liveLogs = entry.liveLogs
        LogStore.shared.setDisplayActive(entry.liveLogs)
        fpsMode = entry.fpsMode; sessionMessage = "Starting…"
        opacity = min(max(entry.controlOpacity ?? 0.7, 0.15), 1)
        let controls = TouchControlsModel.shared
        savedControls = controls.controls; savedVisible = controls.visible; savedSize = controls.sizeScale
        savedLayout = controls.layoutID
        if let profile = entry.controls {
            controls.controls = profile
            // The game's controls come with the layout they were loaded from (if that
            // layout still exists), so a later edit is not written to another game's.
            if ControlPresetsModel.enabled {
                controls.layoutID = ControlPresetsModel.shared.store.resolvedID(entry.controlLayout)
            }
        }
        controls.visible = entry.touchControls
        controls.sizeScale = min(max(entry.controlSize ?? 1, 0.5), 2)
        // madeira-bcd 578 (lead): the built-in default is NOT put on screen at session start: a player may keep touch
        // controls on with an empty overlay and use the touches as the mouse. It comes only when the player turns touch
        // controls on in the in-game menu (BCDTouchLive.setVisible), an explicit request for controls.
        ControlPresetsModel.shared.sessionBegan()
        controllerBinds = GamepadInput.keyboardMouseAvailable ? (entry.controllerBinds ?? [:]) : [:]
        padMouseVertical = GamepadInput.keyboardMouseAvailable ? (entry.padMouseVertical ?? 1) : 1
        controllerMode = GamepadInput.keyboardMouseAvailable ? entry.controllerMode : nil
        applyControllerMode()
        MetalHostView.shared.isHidden = false
        ProMotionIntent.apply(mode: entry.effectiveFPSMode)
        if remember { var played = entry; played.lastPlayed = Date(); save(played) }
        launchDismissLogged = false
        DockStartScreen.shared.begin(dock, at: launchStarted)
        sawProcess = false
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.poll() }
    }
    private func poll() {
        let dockStart = DockStartScreen.shared
        if dockStart.active {
            // A Dock start: the desktop's own frames (explorer, the host's console window)
            // do not end this starting screen; the game's window does (DockStartScreen).
            dockStart.poll(self, rendered: madeira_get_present_count() >= launchPresent + 3)
        }
        if dockStart.holding {
            if launching && !launchSlow && Date().timeIntervalSince(launchStarted) > 30 { launchSlow = true }
        } else if launching {
            if madeira_get_present_count() >= launchPresent + 3 {
                showGameView(reason: "present")
            } else if winios_surface_present_count() > launchSurface {
                showGameView(reason: "surface")
            } else if Date().timeIntervalSince(launchStarted) > 30 { launchSlow = true }
        }
        if wine_process_is_running() != 0 {
            sawProcess = true
            if sessionMessage == "Starting…" { sessionMessage = "" }
            watchProgramEnd()
            watchDockGameEnd()
        } else if sawProcess && wineserver_is_running() == 0 { finish() }
        // The first frame gives Aspect and Fill height the drawable's shape.
        if current != nil, !laidOutAfterFirstPresent, madeira_get_present_count() != MetalBackedView.presentCountAtLaunch {
            laidOutAfterFirstPresent = true
            MetalBackedView.refreshDisplayMode(reason: "first-present")
        }
    }
    /// madeira-bcd (round 577, GTA IV freeze): the program the app launched ended on a thread other than the engine's
    /// boot thread, so the engine never reports the end and the last frame would stay for good (the session ends when
    /// __wine_main returns, WineProcessBridge.m "Session end"). The library leaves it when none of the program's own
    /// processes is left (SessionEndRules). Only a session the engine already knows is over and cannot end by itself:
    /// a program that is running, or ended on the boot thread (the normal path), is never touched here.
    private func watchProgramEnd() {
        guard endedByWatch == nil else { return }
        var code: Int32 = 0
        var seconds = 0.0
        guard wine_launched_exit_stuck(&code, &seconds) != 0 else { return }
        let dock = DockStartScreen.shared.active
        var names = [CChar](repeating: 0, count: 256)
        let running = Int(madeira_live_user_processes(&names, Int32(names.count)))
        switch SessionEndRules.verdict(secondsSinceExit: seconds, userProcesses: running, dockSession: dock) {
        case .wait:
            if dock, endWatchNote != "dock" {
                endWatchNote = "dock"
                LogStore.shared.log("[session-end] main program exited code=\(code); a BCD Dock session is left as it is")
            }
        case .keep(let count):
            let note = "keep \(count)"
            if endWatchNote != note {
                endWatchNote = note
                let list = String(decoding: names.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
                LogStore.shared.log("[session-end] main program exited code=\(code); \(count) process(es) of its own still run (\(list)); the session goes on")
            }
        case .leave:
            endedByWatch = code
            LogStore.shared.log("[session-end] main program exited code=\(code); no game process left; returning")
            // The engine's own end work: it joins the wineserver thread, which is not for the main queue.
            DispatchQueue.global(qos: .userInitiated).async { wine_session_abandon() }
        }
    }
    /// madeira-bcd (round 579, BCD Dock: the game died and the session stayed open): a Dock session's initial program is
    /// Wine's desktop, which never ends by itself on iOS, so a game that crashed left its dead desktop on the screen for
    /// good (Dying Light 32FEAT, The Binding of Isaac VV5NHZ). Only a Dock session, only after the game was seen
    /// to run, and only when DockGameEndRules says it is over: the game's last process ended with an error status
    /// (c0000005 ...), nothing of it runs, the grace time has passed and the host finished. The session then ends as any
    /// other does: finish() shows the library with the exit report (the game's status) and hands the status to
    /// auto-tune. Off: env.MADEIRA_DOCK_GAME_END = 0; env.MADEIRA_DOCK_GAME_END_GRACE = seconds (default 10).
    private func watchDockGameEnd() {
        guard !endedByDockGame, DockStartScreen.shared.active, MadeiraConfig.flag("MADEIRA_DOCK_GAME_END") else { return }
        var text = [CChar](repeating: 0, count: 32768)
        guard madeira_child_report(&text, Int32(text.count)) > 0 else { return }
        let table = DockGameEndRules.parse(String(decoding: text.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self))
        let grace = DockGameEndRules.grace(MadeiraConfig.get("env.MADEIRA_DOCK_GAME_END_GRACE"))
        let verdict = DockGameEndRules.verdict(table, places: .standard, grace: grace) {
            let report = MainActor.assumeIsolated { MadeiraDock.pollReport() }
            return DockHostState(gameRan: report.fields["launch-game-running"] == "1",
                                 gameOver: report.fields["launch-game-ended"] == "1", finished: report.result != nil)
        }
        switch verdict {
        case .none:
            watchDockFailedLaunch(table)
        case .wait(let status, let file, let why):
            let note = "\(file) \(status) \(why)"
            if dockEndNote != note {
                dockEndNote = note
                LogStore.shared.log("[dock-game-end] \(file) ended with status 0x\(String(status, radix: 16)); the session goes on for now (\(why))")
            }
            watchDockFailedLaunch(table)
        case .end(let status, let file, let lived):
            endedByDockGame = true
            LogStore.shared.log("[dock-game-end] \(file) ended with status 0x\(String(status, radix: 16)) after \(Int(lived)) s, the host finished and nothing of the game runs: ending the session")
            DispatchQueue.global(qos: .userInitiated).async { _ = wine_session_dock_game_ended(Int32(bitPattern: status)) }
        }
    }
    /// madeira-bcd (round 580, BCD Dock: the launch failed and no game ran): the host finished with a failure and Valve's
    /// client never ran the game (Spec Ops: The Line and Risen 2, Steam's per-account executable, `probe-result=49`), so no
    /// program of the game ever exists for watchDockGameEnd's rule above, and the session sat on its desktop until the user
    /// closed BCD (271 s in the longest log). When nothing of the game runs and the failure has stood for
    /// `DockGameEndRules.failedLaunchGrace` seconds, the session ends and the library shows the host's reason in words (the
    /// starting screen showed it already). Only a failed launch: a host that finished well, a game that ran, and a
    /// program of the game that runs are not touched. Off with env.MADEIRA_DOCK_GAME_END = 0 (the guard of the caller).
    private func watchDockFailedLaunch(_ table: DockProcessTable) {
        var words: String?
        let failed = DockGameEndRules.failedLaunch(table, places: .standard) {
            let report = MainActor.assumeIsolated { MadeiraDock.pollReport() }
            words = report.failure
            return DockGameEndRules.DockHostResult(result: report.result, gameRan: report.fields["launch-game-running"] == "1")
        }
        guard let failed, failed.blocked == nil else {
            dockFailedAt = nil
            if let failed, let why = failed.blocked {
                let note = "launch-failed \(failed.result) \(why)"
                if dockEndNote != note {
                    dockEndNote = note
                    LogStore.shared.log("[dock-game-end] the host reported the launch failed (code \(failed.result)); the session goes on for now (\(why))")
                }
            }
            return
        }
        let since = dockFailedAt ?? Date()
        if dockFailedAt == nil {
            dockFailedAt = since
            LogStore.shared.log("[dock-game-end] the host reported the launch failed (code \(failed.result)) and no game process runs; the session ends in \(Int(DockGameEndRules.failedLaunchGrace)) s unless one starts")
        }
        guard Date().timeIntervalSince(since) >= DockGameEndRules.failedLaunchGrace else { return }
        endedByDockGame = true
        endedByDockFailure = words ?? "BCD Dock ended before any game window showed up (code \(failed.result)). Export the diagnostic log to see why."
        LogStore.shared.log("[dock-game-end] the host reported the launch failed (code \(failed.result)) and no game process ran: ending the session")
        // The engine's end work records the number as the session's exit status (auto-tune reads a failed session from it).
        DispatchQueue.global(qos: .userInitiated).async { _ = wine_session_dock_game_ended(Int32(failed.result)) }
    }
    /// `reason`: what stopped the launch, when the caller knows (the JIT pool's failure).
    /// `offerJIT`: the launch failed because no debugger is attached, so the alert
    /// offers Enable JIT instead of only reporting.
    func launchFailed(_ reason: String? = nil, offerJIT: Bool = false) {
        guard current != nil && !sawProcess else { return }
        finish()
        if offerJIT, let reason { jitNotice = reason }
        else { error = reason ?? "The game did not start. Look in the diagnostic log, and make sure JIT is on." }
    }
    /// Both flags change in one transaction without animation: the animated
    /// removal of a scrolling view with live content could leave the starting
    /// screen up (and unresponsive) while the game was already presenting.
    func showGameView(reason: String = "button") {
        if launching && !launchDismissLogged {
            launchDismissLogged = true
            fputs("[launch-view] dismissed reason=\(reason) logs=\(launchLogs ? 1 : 0)\n", stderr)
        }
        LogStore.shared.setDisplayActive(liveLogs)
        var transaction = Transaction(); transaction.disablesAnimations = true
        withTransaction(transaction) { launchLogs = false; launching = false }
    }
    func toggleLaunchLogs() {
        launchLogs.toggle()
        LogStore.shared.setDisplayActive(launching ? launchLogs : liveLogs)
        fputs("[startup-log] visible=\(launchLogs ? 1 : 0)\n", stderr)
    }

    /// Hand the session's mode to the pad sampler. In keyboard-and-mouse mode the
    /// bindings follow the touch layout on screen, so they are rebuilt when the
    /// controls change (TouchControlsModel.controls, observed below).
    func applyControllerMode() {
        guard GamepadInput.keyboardMouseAvailable else { return }
        if current != nil, controllerMode == "keys" {
            GamepadInput.shared.setKeyboardMouse(PadBindings.build(controls: TouchControlsModel.shared.controls, binds: controllerBinds, mouseVertical: padMouseVertical))
            if controlsSink == nil {
                controlsSink = TouchControlsModel.shared.$controls.sink { [weak self] controls in
                    guard let self, self.controllerMode == "keys", self.current != nil else { return }
                    GamepadInput.shared.setKeyboardMouse(PadBindings.build(controls: controls, binds: self.controllerBinds, mouseVertical: self.padMouseVertical))
                }
            }
        } else {
            controlsSink = nil
            GamepadInput.shared.setKeyboardMouse(nil)
        }
    }
    private var controlsSink: AnyCancellable?

    func setFPS(_ mode: Int) {
        fpsMode = mode
        let applied = ProMotionIntent.supportedMode(mode)
        madeira_set_vsync_locked(applied)
        ProMotionIntent.apply(mode: applied)
        saveCurrentProfile()
    }
    func showMenu() {
        LibraryKeyboard.hide()
        LibraryController.shared.configure(enabled: true, ownsInput: true)
        menu = true
    }
    func requestQuit() {
        LibraryKeyboard.hide()
        quitRequested = true
        // Ask the application to close (Alt+F4) through the normal input queue,
        // so it can save; the surface stays up until the native session ends.
        winios_post_key(0x12, 1); winios_post_key(0x73, 1)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) {
            winios_post_key(0x73, 0); winios_post_key(0x12, 0)
        }
        sessionMessage = "The game was asked to close. Confirm in the game if it asks."
        menu = false
        fputs("[frontend] graceful close requested\n", stderr)
    }
    func saveCurrentProfile() {
        let controls = TouchControlsModel.shared
        if let id = current, var entry = entries.first(where: { $0.id == id }) {
            entry.controls = controls.controls
            if ControlPresetsModel.enabled { entry.controlLayout = controls.layoutID }
            entry.touchControls = controls.visible
            entry.fpsMode = fpsMode; entry.performance = performance
            entry.overlayFields = overlayFields
            entry.controlOpacity = opacity; entry.controlSize = controls.sizeScale
            if GamepadInput.keyboardMouseAvailable { entry.controllerMode = controllerMode }
            if GamepadInput.keyboardMouseAvailable { entry.controllerBinds = controllerBinds.isEmpty ? nil : controllerBinds }
            if GamepadInput.keyboardMouseAvailable { entry.padMouseVertical = padMouseVertical == 1 ? nil : padMouseVertical }
            // The in-game Aspect & scaling choice sticks to the game. MADEIRA_SESSION_TOOLS=0
            // hides that picker and leaves the stored choice alone.
            if MadeiraConfig.flag("MADEIRA_SESSION_TOOLS") { entry.display = displayMode.rawValue }
            save(entry)
        }
    }
    /// madeira-bcd (round 579): reads the log on a utility queue and, if the game said Steam was missing, shows the hint in
    /// place of the generic report (or alone), unless something else has taken the alert meanwhile.
    private func sayNoSteam(replacing report: String?) {
        let log = LogStore.shared.logFileURL
        DispatchQueue.global(qos: .utility).async {
            guard SteamStartHint.logSaysNoSteam(log) else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self, self.error == report else { return }
                LogStore.shared.log("[steam-start] the game said it found no Steam and closed; it was started without BCD Dock")
                self.error = SteamStartHint.text
            }
        }
    }
    private func finish() {
        // madeira-bcd: auto-tune reads this session's log afterwards (AutoTune.swift); off, nothing.
        if AutoTune.enabled { autoTuneSessionEnded() }
        let report = sawProcess ? exitReport() : nil
        if let report { error = report }
        // madeira-bcd (round 579): a Steam game started without Dock that said it found no Steam: text only, read once from
        // the log after a short session that ended by itself (SteamStartHint). Every other session is not touched.
        if sawProcess, SteamStartHint.applies(directSteamStart: activeEntry?.startsSteamGameDirectly == true,
                                              quitRequested: quitRequested,
                                              seconds: Date().timeIntervalSince(launchStarted)) {
            sayNoSteam(replacing: report)
        }
        timer?.invalidate(); timer = nil
        saveCurrentProfile()
        controllerMode = nil
        controllerBinds = [:]
        padMouseVertical = 1
        let controls = TouchControlsModel.shared
        controls.editing = false; controls.selected = nil
        controls.controls = savedControls; controls.visible = savedVisible; controls.sizeScale = savedSize
        if ControlPresetsModel.enabled { controls.layoutID = savedLayout }
        current = nil; activeEntry = nil; menu = false; sessionMessage = ""
        displayMode = .fit
        LogStore.shared.setDisplayActive(true)
        launching = false; launchLogs = false; LibraryKeyboard.hide()
        DockStartScreen.shared.finish()
        LibraryController.shared.configure(enabled: true, ownsInput: true)
        MetalHostView.shared.isHidden = true
        ProMotionIntent.shared.setActive(false)
        SystemGestures.endSession()   // madeira-bcd: env.MADEIRA_PAD_SYSTEM_GESTURES; without it nothing to undo
        fputs("[frontend] returned to library\n", stderr)
    }
    /// madeira-bcd: what auto-tune needs from a session that ended (AutoTune.swift).
    private func autoTuneSessionEnded() {
        guard AutoTune.enabled else { return }
        var status: UInt32 = 0
        let crashed = wine_crash_exit_status(&status) != 0
        let now = madeira_get_present_count()
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: sawProcess, quitRequested: quitRequested,
                                                 crashStatus: crashed ? status : nil,
                                                 presents: now >= launchPresent ? Int(now - launchPresent) : 0,
                                                 seconds: Date().timeIntervalSince(launchStarted)))
    }
}

// MARK: - Rules (session end)

/// madeira-bcd (round 577, GTA IV freeze): when the library leaves a session whose program has ended but whose engine never
/// reported it (LibraryModel.watchProgramEnd). Pure: what the engine says goes in, a verdict comes out
/// (tests/host/check-session-end.py).
enum SessionEndRules {
    /// Seconds the program must have been gone: the boot thread coming back by itself, a process it started a
    /// moment before it ended, its last log lines: all settle in less.
    static let grace = 4.0

    enum Verdict: Equatable {
        /// Nothing to do yet: inside the grace time, or a BCD Dock session (left as it is).
        case wait
        /// Processes the program started still run: the session goes on until they end.
        case keep(Int)
        /// Nothing of the program's own is left.
        case leave
    }

    /// `userProcesses`: the program's own processes that still run (not Wine's own, not helpers; a process the
    /// engine could not track counts). A BCD Dock session has its own start and end, and its processes are
    /// Valve's client's: never ended from here.
    static func verdict(secondsSinceExit: Double, userProcesses: Int, dockSession: Bool) -> Verdict {
        guard !dockSession, secondsSinceExit >= grace else { return .wait }
        return userProcesses > 0 ? .keep(userProcesses) : .leave
    }

    /// What the user reads: a plain exit code, in a few words (an NTSTATUS error has its own report). Nil for 0.
    static func message(exitCode: Int32) -> String? {
        guard exitCode != 0 else { return nil }
        let shown = exitCode > 0 && exitCode < 65536 ? String(exitCode) : "0x" + String(UInt32(bitPattern: exitCode), radix: 16, uppercase: true)
        return "The game closed (exit code \(shown)). If it keeps happening, open its page and send its log."
    }
}

/// madeira-bcd (round 579): a Steam game started with "Start with: The game's own program" that says Steam is missing
/// and ends by itself (Trails in the Sky 1st Chapter W3D1CP and 5JMRWZ: "SteamAPI_Init() failed.", exit code 1 after 1 s;
/// Katana ZERO DVTCGN and X7YKSJ: "Steam must be running to play this game", then an access violation in its own failure
/// branch). Without Valve's client such a game cannot start; BCD Dock can. Only text: nothing about how the session starts or
/// ends changes (LibraryModel.finish asks, once, after the session ended). Pure and Foundation-only
/// (tests/host/check-session-end.py).
enum SteamStartHint {
    /// A start that failed at once ends in seconds; a longer session is a game that ran (and printed a Steam failure on
    /// the way, which many DRM-free games do).
    static let maxSeconds = 120.0
    /// What the user reads.
    static let text = "The game could not find Steam and closed. It needs Steam: open its page, set Start with to "
        + "BCD Dock (Steam's client), then play it again."
    /// What a log line says when the game, Valve's steam_api or BCD's own diagnostic found no Steam client.
    static let needles = ["SteamAPI_Init() failed", "did not locate a running instance of Steam",
                          "steam_api found no running Steam", "Steam must be running to play this game"]
    /// The line the library writes when a Steam game starts without Dock (ContentView.launchLibraryEntry).
    static let startMarker = "[steam-start] app="
    static let startMode = " direct source="

    /// Whether the session's log is worth reading: a direct Steam start that ended by itself, soon.
    static func applies(directSteamStart: Bool, quitRequested: Bool, seconds: Double) -> Bool {
        directSteamStart && !quitRequested && seconds >= 0 && seconds <= maxSeconds
    }

    /// The log read line by line, in chunks of any size with fixed state: `said` is true when a needle line comes after
    /// the LAST direct-start line (an earlier session of the same app run does not count).
    struct LogScan {
        private(set) var started = false
        private(set) var said = false
        private var carry: [UInt8] = []
        private static let maxLine = 16384
        private static let start = Array(SteamStartHint.startMarker.utf8)
        private static let mode = Array(SteamStartHint.startMode.utf8)
        private static let words = SteamStartHint.needles.map { Array($0.utf8) }

        mutating func feed(_ data: [UInt8]) {
            data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                guard let base = raw.baseAddress else { return }
                var from = 0
                while from < raw.count {
                    if let nl = memchr(base + from, 0x0A, raw.count - from) {
                        let end = from + (UnsafeRawPointer(nl) - (base + from))
                        let piece = UnsafeRawBufferPointer(start: base + from, count: end - from)
                        if carry.isEmpty { line(piece) }
                        else {
                            append(piece)
                            let whole = carry
                            carry.removeAll(keepingCapacity: true)
                            whole.withUnsafeBytes { line($0) }
                        }
                        from = end + 1
                    } else {
                        append(UnsafeRawBufferPointer(start: base + from, count: raw.count - from))
                        break
                    }
                }
            }
        }
        mutating func finish() {
            guard !carry.isEmpty else { return }
            let whole = carry
            carry.removeAll()
            whole.withUnsafeBytes { line($0) }
        }
        private mutating func append(_ piece: UnsafeRawBufferPointer) {
            let room = Self.maxLine - carry.count
            if room > 0 { carry.append(contentsOf: piece.prefix(room)) }
        }
        private static func has(_ hay: UnsafeRawBufferPointer, _ needle: [UInt8]) -> Bool {
            guard let base = hay.baseAddress, hay.count >= needle.count else { return false }
            return needle.withUnsafeBytes { memmem(base, hay.count, $0.baseAddress, $0.count) != nil }
        }
        private mutating func line(_ text: UnsafeRawBufferPointer) {
            if Self.has(text, Self.start) && Self.has(text, Self.mode) { started = true; said = false; return }
            if started && !said && Self.words.contains(where: { Self.has(text, $0) }) { said = true }
        }
    }

    /// Reads the whole log once, 64 KB at a time. Not for the main thread.
    static func logSaysNoSteam(_ url: URL) -> Bool {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? handle.close() }
        var scan = LogScan()
        while let data = try? handle.read(upToCount: 65536), !data.isEmpty { scan.feed([UInt8](data)) }
        scan.finish()
        return scan.said
    }
}

enum LibraryError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(text) = self { return text }; return nil }
}

// Serialized off the main actor. Cancellation follows the card's SwiftUI task,
// so entering a session stops directory work instead of competing with it.
private actor LibraryMetadataScanner {
    static let shared = LibraryMetadataScanner()
    // Dynamic imports do not appear in the PE import table. Look only for
    // terminated DLL names in bounded reads; these indicate supported APIs,
    // not which backend an application selects at runtime.
    private func dynamicAPIs(_ file: URL, budget: inout Int) -> Set<String> {
        guard budget > 0, let handle = try? FileHandle(forReadingFrom: file) else { return [] }
        defer { try? handle.close() }
        guard let signature = try? handle.read(upToCount: 2), signature == Data([0x4d, 0x5a]) else { return [] }
        let length = (try? handle.seekToEnd()) ?? 0
        let window = min(budget, 4 * 1024 * 1024)
        var result = Set<String>()
        let names = ["ddraw.dll", "d3d8.dll", "d3d9.dll", "d3d10.dll", "d3d10_1.dll", "d3d11.dll", "d3d12.dll", "opengl32.dll", "vulkan-1.dll"]
        for offset in [UInt64(0), length > UInt64(window) ? length - UInt64(window) : 0] {
            guard budget > 0, !Task.isCancelled else { break }
            try? handle.seek(toOffset: offset)
            guard let bytes = try? handle.read(upToCount: min(window, budget)) else { break }
            budget -= bytes.count
            let folded = Data(bytes.map { $0 >= 65 && $0 <= 90 ? $0 + 32 : $0 })
            for name in names {
                let ascii = Data((name + "\0").utf8)
                let wide = Data((name + "\0").utf16.flatMap { [UInt8($0 & 255), UInt8($0 >> 8)] })
                if folded.range(of: ascii) != nil || folded.range(of: wide) != nil {
                    result.formUnion(LibraryModel.apiNames([name]))
                }
            }
            if length <= UInt64(window) { break }
        }
        return result
    }
    /// `countBytes: false` skips the size walk (a Steam game's size is its install record's).
    func scan(_ executable: URL, drive: URL, countBytes: Bool = true) -> (bytes: Int64?, api: String?) {
        let folder = executable.deletingLastPathComponent()
        guard ExternalGameStorage.shared.relativePath(for: folder, drive: drive) != nil, !Task.isCancelled else { return (nil, nil) }
        let manager = FileManager.default
        // Executables commonly live below the installation root. Only ascend
        // conventional binary directories, never an arbitrary library parent.
        var installation = folder
        let binaryFolders: Set<String> = ["bin", "binaries", "win32", "win64", "x86", "x64", "release"]
        for _ in 0..<4 {
            guard binaryFolders.contains(installation.lastPathComponent.lowercased()) else { break }
            let parent = installation.deletingLastPathComponent()
            guard ExternalGameStorage.shared.relativePath(for: parent, drive: drive) != nil,
                  !["program files", "program files (x86)", "games", "common", "steamapps", "external"].contains(parent.lastPathComponent.lowercased()) else { break }
            installation = parent
        }
        var complete = true
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
        let walker = countBytes ? manager.enumerator(at: installation, includingPropertiesForKeys: Array(keys), options: [], errorHandler: { _, _ in complete = false; return true }) : nil
        var bytes: Int64 = 0
        var files = 0
        while let file = walker?.nextObject() as? URL {
            if Task.isCancelled { return (nil, nil) }
            files += 1
            if files > 200_000 { complete = false; break }
            guard let values = try? file.resourceValues(forKeys: keys) else { complete = false; continue }
            if values.isSymbolicLink == true { walker?.skipDescendants(); continue }
            if values.isRegularFile == true { bytes += Int64(values.fileSize ?? 0) }
        }
        // Engines often import graphics through a local DLL. Follow only their
        // actual import graph, case-insensitively, never every DLL in drive_c.
        let siblings = (try? manager.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        var local: [String: URL] = [:]
        for file in siblings where file.pathExtension.lowercased() == "dll" {
            if ExternalGameStorage.shared.relativePath(for: file, drive: drive) != nil { local[file.lastPathComponent.lowercased()] = file }
        }
        var budget = 32 * 1024 * 1024
        var pending = [executable], visited = Set<String>(), apis = Set<String>()
        // A launcher may start a sibling executable rather than import its engine.
        // Restrict fallback to the same installation directory and a small count.
        if LibraryModel.graphicsImports(executable) == nil {
            pending.insert(contentsOf: siblings.filter {
                $0.pathExtension.lowercased() == "exe" && $0 != executable &&
                ExternalGameStorage.shared.relativePath(for: $0, drive: drive) != nil
            }.sorted { $0.path < $1.path }.prefix(8), at: 0)
        }
        while let file = pending.popLast(), visited.count < 64, !Task.isCancelled {
            if !visited.insert(file.path).inserted { continue }
            let imports = LibraryModel.importNames(file)
            apis.formUnion(LibraryModel.apiNames(imports))
            apis.formUnion(dynamicAPIs(file, budget: &budget))
            for name in imports { if let dependency = local[name], !visited.contains(dependency.path) { pending.append(dependency) } }
        }
        return (complete && walker != nil ? bytes : nil, apis.isEmpty ? nil : apis.sorted().joined(separator: "/"))
    }
}

/// Whether JIT is available (the debugger is attached), checked every 2 s.
/// Shared by BCD's JIT chip and the Enable JIT buttons, which are disabled once it is.
final class LibraryJITState: ObservableObject {
    static let shared = LibraryJITState()
    @Published private(set) var enabled = StikJITHelper.ready
    private var timer: Timer?
    private init() {
        let timer = Timer(timeInterval: 2, repeats: true) { [weak self] _ in self?.refresh() }
        RunLoop.main.add(timer, forMode: .common)   // keeps ticking while a list scrolls
        self.timer = timer
    }
    func refresh() {
        let now = StikJITHelper.ready
        guard now != enabled else { return }
        withAnimation(UIAccessibility.isReduceMotionEnabled ? nil : .default) { enabled = now }
    }
}

struct SteamMatch: Decodable, Identifiable {
    let id: Int
    let name: String
    let tiny_image: String?
}

/// The public Steam store: title search and store artwork for an app ID. No
/// account, credentials, or private library access.
enum SteamCatalog {
    static func search(_ text: String) async throws -> [SteamMatch] {
        var url = URLComponents(string: "https://store.steampowered.com/api/storesearch/")!
        url.queryItems = [URLQueryItem(name: "term", value: text), URLQueryItem(name: "l", value: "english"), URLQueryItem(name: "cc", value: "US")]
        var request = URLRequest(url: url.url!); request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200, data.count < 2_000_000 else {
            throw LibraryError.message("Steam search is not available right now. You can still set the title and artwork yourself.")
        }
        struct Results: Decodable { var items: [SteamMatch] }
        return Array(try JSONDecoder().decode(Results.self, from: data).items.prefix(30))
    }
    static func cover(_ id: Int) -> URL? { URL(string: "https://cdn.cloudflare.steamstatic.com/steam/apps/\(id)/library_600x900.jpg") }
    static func hero(_ id: Int) -> URL? { URL(string: "https://cdn.cloudflare.steamstatic.com/steam/apps/\(id)/library_hero.jpg") }
}

/// Settings › Credits, first row: BCD, a fork of Madeira, with the attribution line of
/// LICENSE-BCD.md section 4(b) exactly as written there.
struct BCDCredit: View {
    static let attribution = "Includes work from BCD by thebcd69 (https://github.com/thebcd69/bcd-games)"
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("BCD").font(.body.weight(.semibold))
            Text("A fork of Madeira by Will Faust and its contributors, below.")
                .font(.subheadline).foregroundStyle(.secondary)
            if let url = URL(string: "https://github.com/thebcd69/bcd-games") {
                Link(Self.attribution, destination: url).font(.subheadline)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

/// One row of Settings › Credits: a person, their GitHub account and what they did.
struct MadeiraCredit: View {
    let name: String
    let handle: String
    let role: String
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(name).font(.body.weight(.semibold))
                if let url = URL(string: "https://github.com/\(handle)") {
                    Link("@\(handle)", destination: url).font(.subheadline)
                }
            }
            Text(role).font(.subheadline).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

/// The in-process synchronisation engine, one per session. Fastsync is the default:
/// bcd.cfg with neither inproc-sync nor env.MADEIRA_FASTSYNC, for which the app
/// exports MADEIRA_FASTSYNC=auto (WineProcessBridge.m). Madsync is inproc-sync = 1;
/// Wine standard sync is inproc-sync = 0 without a fastsync value, as it was written
/// while madsync was the default. Mirrors madeira_cfg_sync_engine (build/madeira_cfg.h).
/// Wine reads both once per app run, and never runs fastsync while madsync is on.
enum SyncEngine: String, CaseIterable, Identifiable {
    case madsync, fastsync, wine
    var id: String { rawValue }
    var label: String {
        switch self {
        case .madsync: return "Madsync"
        case .fastsync: return "Fastsync (default)"
        case .wine: return "Wine standard sync"
        }
    }
    /// The MADEIRA_FASTSYNC values Wine treats as "fastsync on" (sync.c, event.c).
    static let fastsyncValues: Set<String> = ["1", "on", "yes", "auto", "cells"]
    static var current: SyncEngine {
        let inproc = MadeiraConfig.get("inproc-sync")
        if inproc != nil && MadeiraConfig.bool("inproc-sync", default: false) { return .madsync }
        if let fast = MadeiraConfig.get("env.MADEIRA_FASTSYNC") { return fastsyncValues.contains(fast) ? .fastsync : .wine }
        return inproc == nil ? .fastsync : .wine
    }
    static func apply(_ engine: SyncEngine) {
        switch engine {
        case .madsync: MadeiraConfig.set("inproc-sync", "1"); MadeiraConfig.set("env.MADEIRA_FASTSYNC", nil)
        case .fastsync: MadeiraConfig.set("inproc-sync", nil); MadeiraConfig.set("env.MADEIRA_FASTSYNC", nil)
        case .wine: MadeiraConfig.set("inproc-sync", "0"); MadeiraConfig.set("env.MADEIRA_FASTSYNC", nil)
        }
    }
}

/// The memory and sync options BCD's Settings › Memory & performance offers (BCDSettingsLive.swift):
/// the JIT pool (bcd.cfg pool), the video memory budget (vram-mb), the file-backed swap tier (swap-mb,
/// off by default, and env.MADEIRA_SWAP_COVERAGE, which allocations it backs), the memory pool and the
/// in-process sync engine (SyncEngine). All are read when Madeira starts, so changes apply after a restart.
/// MADEIRA_RUNTIME_SETTINGS=0 hides them. Wave 3 (2026-10-19): the classic form's rows are gone; these are
/// the tables and readers BCD's Settings uses.
enum RuntimeMemorySyncSettings {
    /// env.MADEIRA_RUNTIME_SETTINGS = 0 (bcd.cfg or the environment) hides the memory and display switches:
    /// a build can ship without them. Read here, with a literal key, so that All settings keeps listing it.
    static var shown: Bool { MadeiraConfig.flag("MADEIRA_RUNTIME_SETTINGS") }
    /// The keys Memory owns; All settings leaves them out.
    static let featuredKeys: Set<String> = ["pool", "vram-mb", "swap-mb", "env.MADEIRA_SWAP_COVERAGE", "mempool-mb",
                                            "inproc-sync", "env.MADEIRA_FASTSYNC", "eco"]
    // ml1241: 256 (the launch's lower bound) for the pool, 512 and 1024 for video memory
    // (winemetal accepts vram-mb >= 256), at the user's request.
    static let poolChoices = [0, 256, 512, 640, 768, 1024, 1152]          // 0 = the standard 896 MB
    static let vramChoices = [0, 512, 1024, 1536, 2048, 3072, 4096, 4352, 4608, 5120, 6144]   // 0 = automatic
    static let swapChoices = [0, 1024, 2048, 3072, 4096]
    static let memPoolChoices = [0, 2048, 4096, 6144]
    /// The stored value "" (no key) and "classic" are the same rules, unless
    /// bcd.cfg has swap-mode = 2 (then no key means broad, ml1257).
    static let coverageChoices: [(String, String)] = [
        ("", "Large allocations (8 MB+)"), ("blocks", "All allocations of 1 MB+"), ("wide", "1 MB+ and overflow"),
        ("broad", "Whole reservations 4 MB+ (broad)"),
    ]

    static func currentCoverage() -> String {
        let v = (MadeiraConfig.get("env.MADEIRA_SWAP_COVERAGE") ?? "").lowercased()
        if v.isEmpty { return swapModeBroad ? "broad" : "" }
        return v == "classic" ? "" : v
    }
    /// ml1257: bcd.cfg swap-mode = 2 selects broad coverage when
    /// env.MADEIRA_SWAP_COVERAGE is unset (virtual_ios.c ios_swap_config).
    static var swapModeBroad: Bool { (Int(MadeiraConfig.get("swap-mode") ?? "") ?? 1) >= 2 }
}

/// ml1174: CPU load for the performance overlay, from the process's thread
/// times. `total` is the CPU time since the previous sample as a share of all
/// cores; `top` is the busiest thread's share of one core, which shows a game
/// held back by one thread while the total stays low.
final class CPUMeter {
    private var last: [UInt64: Double] = [:]
    private var lastWall = 0.0
    private let cores = Double(max(1, ProcessInfo.processInfo.activeProcessorCount))

    /// nil on the first call, and when the threads cannot be read.
    func sample() -> (total: Double, top: Double)? {
        var list: thread_act_array_t?
        var count: mach_msg_type_number_t = 0
        guard task_threads(mach_task_self_, &list, &count) == KERN_SUCCESS, let list else { return nil }
        defer {
            vm_deallocate(mach_task_self_, vm_address_t(UInt(bitPattern: list)),
                          vm_size_t(Int(count) * MemoryLayout<thread_t>.stride))
        }
        let wall = CACurrentMediaTime()
        var now: [UInt64: Double] = [:]
        var sum = 0.0, top = 0.0
        for i in 0..<Int(count) {
            let thread = list[i]
            defer { mach_port_deallocate(mach_task_self_, thread) }
            var ident = thread_identifier_info_data_t()
            var identCount = mach_msg_type_number_t(MemoryLayout<thread_identifier_info_data_t>.size / MemoryLayout<natural_t>.size)
            var basic = thread_basic_info_data_t()
            var basicCount = mach_msg_type_number_t(MemoryLayout<thread_basic_info_data_t>.size / MemoryLayout<natural_t>.size)
            let identResult = withUnsafeMutablePointer(to: &ident) {
                $0.withMemoryRebound(to: integer_t.self, capacity: Int(identCount)) {
                    thread_info(thread, thread_flavor_t(THREAD_IDENTIFIER_INFO), $0, &identCount)
                }
            }
            let basicResult = withUnsafeMutablePointer(to: &basic) {
                $0.withMemoryRebound(to: integer_t.self, capacity: Int(basicCount)) {
                    thread_info(thread, thread_flavor_t(THREAD_BASIC_INFO), $0, &basicCount)
                }
            }
            guard identResult == KERN_SUCCESS, basicResult == KERN_SUCCESS else { continue }
            let seconds = Double(basic.user_time.seconds + basic.system_time.seconds)
                + Double(basic.user_time.microseconds + basic.system_time.microseconds) / 1e6
            now[ident.thread_id] = seconds
            if let before = last[ident.thread_id] {
                let used = max(0, seconds - before)
                sum += used; top = max(top, used)
            }
        }
        let interval = wall - lastWall
        let first = last.isEmpty
        last = now; lastWall = wall
        guard !first, interval > 0 else { return nil }
        return (min(100, sum / interval / cores * 100), min(100, top / interval * 100))
    }
}

// A key window is required for UIKit text input; the rendering placeholder lives
// beneath separate presentation and control windows and cannot reliably own it.
enum LibraryKeyboard {
    static var window: UIWindow?
    static weak var previous: UIWindow?
    static var input: LibraryKeyInput?
    static func show() {
        // MADEIRA_FRONTEND_KEYBOARD=0: the game view's own keyboard instead.
        if !MadeiraConfig.flag("MADEIRA_FRONTEND_KEYBOARD") { MetalBackedView.toggleKeyboard(); return }
        guard window == nil, let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first(where: { $0.activationState == .foregroundActive }) else { return }
        previous = scene.windows.first(where: { $0.isKeyWindow })
        let w = LibraryKeyboardWindow(windowScene: scene)
        w.windowLevel = .normal + 102; w.backgroundColor = .clear
        let controller = UIViewController(); controller.view.backgroundColor = .clear
        w.rootViewController = controller
        let v = LibraryKeyInput(frame: CGRect(x: 0, y: 0, width: 1, height: 1))
        controller.view.addSubview(v); input = v; window = w
        w.makeKeyAndVisible(); v.becomeFirstResponder()
        fputs("[frontend-keyboard] key-window input activated\n", stderr)
    }
    static func hide() {
        if window != nil { fputs("[frontend-keyboard] key-window input deactivated\n", stderr) }
        input?.releaseModifiers(); input?.resignFirstResponder(); window?.isHidden = true
        window = nil; input = nil; previous?.makeKey(); previous = nil
    }
}
final class LibraryKeyboardWindow: UIWindow {
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? { nil }
}
final class LibraryKeyInput: UIView, UIKeyInput {
    var hasText: Bool { true }
    override var canBecomeFirstResponder: Bool { true }
    /// Ctrl, Shift and Alt held down by the key bar (BCDKeyboardAccessory).
    var held = Set<Int32>()
    var keyboardType: UIKeyboardType { get { .asciiCapable } set {} }
    var autocorrectionType: UITextAutocorrectionType { get { .no } set {} }
    var autocapitalizationType: UITextAutocapitalizationType { get { .none } set {} }
    /// BCD's key bar over the keyboard (BCDSessionLive.swift): Esc, Ctrl, Shift, Alt, Tab, Enter,
    /// the arrows and Done, posted as before.
    private lazy var keyBar: UIView = BCDKeyboardAccessory.view(for: self)
    override var inputAccessoryView: UIView? { keyBar }
    func press(_ vk: Int32) { winios_post_key(vk, 1); winios_post_key(vk, 0) }
    func insertText(_ text: String) {
        for ch in text {
            guard let (vk, shift) = MetalBackedView.vkForChar(ch) else { continue }
            let temporary = shift && !held.contains(0x10)
            if temporary { winios_post_key(0x10, 1) }; press(vk); if temporary { winios_post_key(0x10, 0) }
        }
    }
    func deleteBackward() { press(0x08) }
    func releaseModifiers() { for key in held { winios_post_key(key, 0) }; held.removeAll() }
}

/// A desktop session (the Desktop entry) draws through Winios's compositor view,
/// a plain UIView the native side adds straight onto the app window, above the
/// SwiftUI root view, and never hides. The game view (MetalHostView) is hidden
/// when a session ends; the compositor was not, so the ended session's frozen
/// desktop covered the library. The library hides that view when the session
/// ends and shows it again when the next one begins. [library-surface] logs
/// both. MADEIRA_LIBRARY_HIDE_ENDED_DESKTOP=0 leaves it alone.
enum EndedSessionSurface {
    private static var watch: AnyCancellable?
    private static var hiddenByUs = false

    @MainActor static func install() {
        guard watch == nil else { return }
        watch = LibraryModel.shared.$current.receive(on: DispatchQueue.main).sink { current in
            MainActor.assumeIsolated {
                if current == nil { hide(reason: "session-ended") } else { show() }
            }
        }
    }

    @MainActor static func hide(reason: String) {
        guard MadeiraConfig.flag("MADEIRA_LIBRARY_HIDE_ENDED_DESKTOP"),
              LibraryModel.shared.current == nil, wine_process_is_running() == 0 else { return }
        guard winios_compositor_set_hidden(1) != 0 else { return }
        hiddenByUs = true
        LogStore.shared.log("[library-surface] hid ended desktop reason=\(reason)")
    }

    @MainActor static func show() {
        guard hiddenByUs else { return }
        hiddenByUs = false
        _ = winios_compositor_set_hidden(0)
        LogStore.shared.log("[library-surface] desktop shown for the new session")
    }
}
