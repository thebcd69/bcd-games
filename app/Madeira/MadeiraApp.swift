import Foundation
import SwiftUI
import UIKit

@main
struct MadeiraApp: App {
    /// madeira-bcd: the screen orientation lock for a game's session (BCDOrientation.swift). The delegate answers which
    /// orientations a window may take: the three Info.plist declares until a session turns the lock on.
    @UIApplicationDelegateAdaptor(BCDOrientationDelegate.self) private var orientationDelegate

    init() {
        ExternalGameStorage.shared.restore()
        // madeira-bcd: the Metal Performance HUD's performance insights (the
        // notes that stack up left of its panel: shader compiles, similar render
        // passes, blit encoders) cover the game. Off unless bcd.cfg says
        // env.MTL_HUD_INSIGHTS_ENABLED = 1, which is exported later and wins.
        setenv("MTL_HUD_INSIGHTS_ENABLED", "0", 0)
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
    }

    var body: some Scene {
        WindowGroup {
            // BCD's interface (BCDLiveFrontEnd, shown by ContentView) is the app's only interface.
            ContentView()
                .tint(BCDStyle.lavender)
                .modifier(ClaimGamepadEvents())
                .onAppear {
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
