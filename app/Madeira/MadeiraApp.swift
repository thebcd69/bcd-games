// Portions copyright 2026 Connor Gow: code taken from https://github.com/willfaust/Madeira (pull request #464)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
// Portions copyright 2026 creeper4004: colour profiles and Settings > Appearance, from thebcd69/bcd-games pull request #27; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
import Foundation
import SwiftUI
import UIKit

@main
struct MadeiraApp: App {
    /// madeira-bcd: the screen orientation lock for a game's session (BCDOrientation.swift). The delegate answers which
    /// orientations a window may take: the three Info.plist declares until a session turns the lock on.
    @UIApplicationDelegateAdaptor(BCDOrientationDelegate.self) private var orientationDelegate

    init() {
        // madeira-bcd: the Metal Performance HUD's performance insights (the
        // notes that stack up left of its panel: shader compiles, similar render
        // passes, blit encoders) cover the game. Off unless bcd.cfg says
        // env.MTL_HUD_INSIGHTS_ENABLED = 1, which is exported later and wins.
        setenv("MTL_HUD_INSIGHTS_ENABLED", "0", 0)
        // madeira-bcd (Porthole port, item 6): a Steam download to a slow external drive is written with fewer writers when bcd.cfg
        // says drive-gentle-writes = 1 (BCDDriveSpeed.swift). Without the line the answer is nil at once and nothing is measured.
        DepotDownloader.gentleChunks = { folder in
            guard MadeiraConfig.get(BCDDriveGentle.key) == "1" else { return nil }
            let decision = await Task.detached(priority: .utility) { BCDDriveGentle.decision(for: folder, keyOn: true) }.value
            if let line = decision.log { SteamLog.event(line) }
            return decision.limit
        }
        // ml1172: read the screen on the main thread; library entries, whose
        // default Resolution comes from it, are also made on other threads.
        _ = ResolutionChoices.screen
        // madeira-bcd: update packs are gone (owner's decision 2026-10-09, as
        // upstream); a pack an earlier build installed is deleted.
        if let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
            let packs = base.appendingPathComponent("MadeiraPacks", isDirectory: true)
            if FileManager.default.fileExists(atPath: packs.path) { try? FileManager.default.removeItem(at: packs) }
        }
        UserDefaults.standard.removeObject(forKey: "madeira.packs.enabled")
        // madeira-bcd (from Madeira #498): games and downloads keep going when BCD leaves the screen, if Settings >
        // Memory & performance > In the background says so (BackgroundRun.swift). Off: it only halts the audio
        // endpoint when BCD enters the background, which the "audio" background mode would otherwise keep alive.
        BackgroundRun.shared.start()
        LibraryController.duoNavigation = BCDDuoFeatures.active   // the native Duo interface's direction repeat; false everywhere else
    }

    var body: some Scene {
        WindowGroup {
            // BCD's interface (BCDLiveFrontEnd, shown by ContentView) is the app's only interface.
            ContentView()
                .modifier(BCDThemeTint())   // BCD Violet (the default) leaves the app's tint as it was; another profile tints the app
                .modifier(ClaimGamepadEvents())
                .onAppear {
                    // External drives (ExternalDrives.swift, from Madeira #464): each saved drive is opened again and linked
                    // as C:\External\<name> and its letter before Wine starts.
                    ExternalDrives.shared.mountAll()
                    GamepadInput.shared.start()
                    // BCD (2026-10-18): controllers, their tester and the key mapping (BCDControllerHub.swift).
                    BCDControllerHub.shared.start(.live)
                    HardwareInput.shared.start()
                    JITNetworkShortcut.shared.restoreLeftover()   // also starts its network path monitor
                }
                // madeira://jit-network/... (the Madeira JIT shortcut returning, JITNetwork.swift),
                // else madeira://play?exe=... (Home Screen shortcuts, SavesAndShortcuts.swift).
                .onOpenURL { url in if !JITNetworkShortcut.shared.handle(url) { ShortcutRouter.shared.handle(url) } }
        }
    }
}
