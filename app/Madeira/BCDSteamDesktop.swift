// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › BCD Dock › Steam for Windows (experimental; branch steam-client, milestone M1): the switch, the install of Valve's own
// Steam client and the button that opens it. The card names no engine type: it reads and asks through BCDSteamDesktopBackend, which
// BCDSteamDesktopLive.swift carries out in the app and the preview app on sample data (docs/BCD-UI-ARCHITECTURE.md). With the switch
// off the card is the switch and one line; nothing else of the feature shows anywhere.

// MARK: - What the card shows and asks for

struct BCDSteamDesktopState: Equatable {
    /// A download under way.
    struct Work: Equatable {
        /// What is being done, in words.
        var step: String
        var received: Int64 = 0
        /// 0: not known yet.
        var total: Int64 = 0
        var fraction: Double { total > 0 ? min(1, Double(received) / Double(total)) : 0 }
    }

    /// The switch: off unless the player turned it on.
    var on = false
    /// steam.exe is in the client's own folder.
    var installed = false
    /// The client's version once it has updated itself; nil before its first start.
    var version: String?
    /// What the client's folder holds on this device; nil until it is measured.
    var diskBytes: Int64?
    /// BCD downloads the client's files itself before the first start (else Steam does, in the session).
    var fetchesPackages = true
    /// BCD Dock's games are in the client's library too (SteamDesktopLibraries); on unless the player turned it off.
    var sharesDock = true
    var work: Work?
    /// Why the last Install or Open could not go on.
    var error: String?
    /// The session is being started.
    var starting = false
}

@MainActor protocol BCDSteamDesktopBackend: ObservableObject {
    var state: BCDSteamDesktopState { get }
    func refresh()
    func setOn(_ on: Bool)
    func setFetchesPackages(_ on: Bool)
    func setSharesDock(_ on: Bool)
    /// Gets steam.exe (and the client's files, when chosen) from Valve's update servers; no session runs.
    func install()
    /// Stops the download; what arrived stays for the next Install.
    func stopInstall()
    /// Starts the client in a Windows desktop, as the library tile does.
    func open()
}

// MARK: - The card

/// The words, apart from the view, so the host check reads them (tests/host/check-steam-desktop.py).
enum BCDSteamDesktopWords {
    static let title = "Steam for Windows (experimental)"
    static let switchTitle = "Steam for Windows"
    static let switchDetail = "Valve's own Steam client, with its window, store and library, in a Windows desktop inside BCD. "
        + "Experimental: it needs far more memory than BCD Dock, and an update from Valve can break it. BCD Dock stays the way "
        + "your Steam games start."
    /// Owner, 2026-11-22: Steam for Windows is for looking around, not for playing (docs/STEAM_DESKTOP.md, "Scope").
    static let notForPlaying = "Not for playing games. Use it for the store, your library, Friends, the Workshop and Steam's "
        + "settings. A game started from it runs inside Steam's session, without the settings BCD gives that game, and heavy games "
        + "don't fit beside Steam's window. Play your games through BCD Dock."
    static let devices = "Made for devices with a large address space and a memory limit of 6 GB or more, such as an 8 GB iPhone "
        + "signed with the larger memory limit or an M-series iPad. On others Steam's window may not come up."
    static let install = "BCD gets steam.exe from Valve's own update servers (about 3 MB) and checks it against Valve's list of "
        + "client files. Steam then brings itself up to date at its first start."
    static let packagesTitle = "Download the client's files with BCD first"
    static let packagesDetail = "About 260 MB more, from the same servers, each file checked. Steam then only unpacks them at its "
        + "first start. Off: Steam downloads them itself inside the session, which is slower and has to stay on screen."
    static let shareTitle = "Share games with BCD Dock"
    static let shareDetail = "Games BCD Dock installed, on this device and on your external drives, show as installed in Steam "
        + "for Windows. Games you install in Steam for Windows show in your library and start through BCD Dock. There is one copy "
        + "of each game, used by both. Steam downloads no DLC you didn't choose in BCD, and BCD keeps the DLC you add or remove in "
        + "Steam. Steam may update or check the games. Takes effect at the next start."
    static let signIn = "You sign in in Steam's own window; BCD hands it no sign-in. While it runs, BCD's own Steam connection and "
        + "downloads wait. It is also in the Library, as \(title)."
    static let afterOpen = "Games you start from Steam share this one session and its settings. To start another game through "
        + "BCD Dock afterwards, restart BCD."

    static func installed(version: String?, diskBytes: Int64?) -> String {
        var text = version.map { "Installed (Valve's client \($0))" } ?? "Installed (Steam updates itself at its first start)"
        if let diskBytes, diskBytes > 0 { text += ", \(BCDFormat.bytes(diskBytes)) on this device" }
        return text + "."
    }

    static func progress(_ work: BCDSteamDesktopState.Work) -> String {
        guard work.total > 0 else { return work.step }
        return "\(work.step) \(BCDFormat.bytes(work.received)) of \(BCDFormat.bytes(work.total))"
    }
}

/// The card under Settings › BCD Dock. Its controls' ids start with the Dock section's prefix, so the section's D-pad order and its
/// B button take them in.
struct BCDSteamDesktopCard<Backend: BCDSteamDesktopBackend>: View {
    @ObservedObject var backend: Backend

    static var prefix: String { "settings.dock.steam-desktop." }
    private var state: BCDSteamDesktopState { backend.state }

    var body: some View {
        BCDSectionKit.Card(eyebrow: BCDSteamDesktopWords.title, symbol: "macwindow") {
            BCDSectionKit.SwitchRow(title: BCDSteamDesktopWords.switchTitle, detail: BCDSteamDesktopWords.switchDetail,
                                    isOn: state.on, id: Self.prefix + "switch", enabled: state.work == nil) { backend.setOn($0) }
            BCDSectionKit.Note(text: BCDSteamDesktopWords.notForPlaying, symbol: "exclamationmark.circle", tone: .warn)
            if state.on {
                Divider().overlay(BCDStyle.stroke)
                if let work = state.work {
                    BCDProgressBar(fraction: work.fraction)
                    BCDSectionKit.Note(text: BCDSteamDesktopWords.progress(work), symbol: "arrow.down.circle", tone: .info)
                    Text("You can leave this page; the download carries on. Cancel keeps what is already here for the next try.")
                        .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                    BCDSectionKit.ActionButton(title: "Cancel", symbol: "xmark", kind: .outline, fill: true,
                                               id: Self.prefix + "cancel") { backend.stopInstall() }
                } else if state.installed {
                    BCDSectionKit.Note(text: BCDSteamDesktopWords.installed(version: state.version, diskBytes: state.diskBytes),
                                       symbol: "checkmark.circle.fill", tone: .good)
                    Text(BCDSteamDesktopWords.signIn).bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                    BCDSectionKit.SwitchRow(title: BCDSteamDesktopWords.shareTitle, detail: BCDSteamDesktopWords.shareDetail,
                                            isOn: state.sharesDock, id: Self.prefix + "share-dock") { backend.setSharesDock($0) }
                    if let error = state.error {
                        BCDSectionKit.Note(text: error, symbol: "exclamationmark.triangle.fill", tone: .bad)
                    }
                    BCDSectionKit.ActionButton(title: state.starting ? "Starting…" : "Open Steam for Windows", symbol: "play.fill",
                                               kind: .primary, fill: true, id: Self.prefix + "open",
                                               enabled: !state.starting) { backend.open() }
                    Text(BCDSteamDesktopWords.afterOpen).bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(BCDSteamDesktopWords.install).bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                    BCDSectionKit.SwitchRow(title: BCDSteamDesktopWords.packagesTitle, detail: BCDSteamDesktopWords.packagesDetail,
                                            isOn: state.fetchesPackages, id: Self.prefix + "packages") { backend.setFetchesPackages($0) }
                    if let error = state.error {
                        BCDSectionKit.Note(text: error, symbol: "exclamationmark.triangle.fill", tone: .bad)
                    }
                    BCDSectionKit.ActionButton(title: state.error == nil ? "Install Steam for Windows" : "Try again",
                                               symbol: "arrow.down.circle.fill", kind: .primary, fill: true,
                                               id: Self.prefix + "install") { backend.install() }
                }
                Text(BCDSteamDesktopWords.devices).bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .onAppear { backend.refresh() }
    }
}
