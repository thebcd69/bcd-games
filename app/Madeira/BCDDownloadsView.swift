// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// Downloads: the queue, the device's free room, and what finished today. Each download has
/// its own buttons under its progress, for touch, VoiceOver and the controller alike: Pause,
/// Resume or Try again, and Cancel, which asks first, in the row itself, so a controller can
/// answer too (the classic download sheet's Pause, Resume, Try again and Cancel download).
struct BCDDownloadsView: View {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.bcdLayout) private var layout
    var nativeControllerNavigation = false
    /// The download whose Cancel waits for an answer.
    @State private var stopping: String?

    var body: some View {
        ScrollViewReader { reader in
        ScrollView(.vertical, showsIndicators: false) {
            if layout == .phonePortrait {
                VStack(spacing: 14) { queue; roomCard; finishedCard }
                    .padding(.horizontal, layout.gutter).padding(.vertical, 8)
            } else {
                HStack(alignment: .top, spacing: 16) {
                    queue.frame(maxWidth: .infinity)
                    VStack(spacing: 14) { roomCard; finishedCard }.frame(width: layout == .pad ? 360 : 300)
                }
                .padding(.horizontal, layout.gutter).padding(.vertical, 8)
            }
        }
        .onScrollPhaseChange { _, phase in if nativeControllerNavigation && phase == .interacting { focus.touched() } }
        .onChange(of: focus.focused) { _, _ in reveal(reader) }
        .onChange(of: focus.controllerActive) { _, _ in reveal(reader) }
        .onGeometryChange(for: CGSize.self) { $0.size } action: { _ in reveal(reader) }
        }
    }

    private func reveal(_ reader: ScrollViewProxy) {
        guard nativeControllerNavigation, focus.controllerActive, let id = focus.focused, id.hasPrefix("downloads.") else { return }
        DispatchQueue.main.async {
            guard focus.controllerActive, focus.focused == id else { return }
            withAnimation(.easeOut(duration: 0.18)) { reader.scrollTo(id, anchor: .center) }
        }
    }

    @ViewBuilder private var queue: some View {
        if store.downloads.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                BCDEyebrow(text: "Queue")
                Text("Nothing is downloading").font(BCDStyle.heading(20))
                Text("Pick a game in your library and choose Install. It downloads straight from its store onto this device.")
                    .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
            }
            .bcdCard(padding: 20)
        } else {
            VStack(spacing: 12) {
                ForEach(store.downloads) { download in row(download) }
            }
        }
    }

    private func row(_ download: BCDDownload) -> some View {
        HStack(alignment: .top, spacing: 16) {
            if let game = store.games.first(where: { $0.id == download.id }) {
                BCDGameArt(game: game, kind: .cover, showTitle: false)
                    .frame(width: layout == .pad ? 64 : 52, height: layout == .pad ? 96 : 78)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline) {
                    Text(download.title).font(BCDStyle.heading(18)).lineLimit(1)
                    Spacer(minLength: 8)
                    Text("\(Int(download.fraction * 100))%").font(BCDStyle.label(15)).monospacedDigit()
                }
                BCDProgressBar(fraction: download.fraction)
                HStack(spacing: 8) {
                    Text("\(BCDFormat.bytes(download.doneBytes)) of \(BCDFormat.bytes(download.totalBytes))")
                    Spacer(minLength: 4)
                    Text(download.paused ? "Paused" : progressDetail(download))
                }
                .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft).monospacedDigit().lineLimit(1)
                if let failed = download.failed {
                    Label(failed, systemImage: "exclamationmark.triangle.fill").font(BCDStyle.body(13)).foregroundStyle(BCDStyle.alert)
                }
                controls(download)
            }
        }
        .bcdCard(padding: 16)
    }

    // MARK: A download's buttons

    /// Pause, Resume or Try again, and Cancel; after Cancel, the question in their place.
    @ViewBuilder private func controls(_ download: BCDDownload) -> some View {
        if stopping == download.id {
            VStack(alignment: .leading, spacing: 8) {
                Text("Stop this download? A game that isn't installed yet loses what has come down so far. An update just stops, and the game stays as it is.")
                    .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                pair {
                    chip("Stop download", symbol: "xmark", id: "downloads.\(download.id).stop", tint: BCDStyle.alert) {
                        stopping = nil
                        store.actions?.cancelDownload(download.id)
                    }
                    chip("Keep downloading", symbol: "arrow.uturn.backward", id: "downloads.\(download.id).keep") {
                        answered(download)
                    }
                }
            }
        } else {
            let main = BCDDownloadButton(paused: download.paused, failed: download.failed != nil)
            pair {
                // The row's main control keeps the row's focus id (A: "Pause or resume").
                chip(main.title, symbol: main.symbol, id: "downloads.\(download.id)") { primary(download) }
                chip("Cancel", symbol: "xmark", id: "downloads.\(download.id).cancel") { ask(download) }
            }
        }
    }

    /// Two buttons side by side, or one above the other where the row is too narrow for both
    /// at full size (a phone on its side beside the storage column, an iPhone SE).
    private func pair<Content: View>(@ViewBuilder _ buttons: () -> Content) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { buttons() }
            VStack(alignment: .leading, spacing: 8) { buttons() }
        }
    }

    /// A paused download resumes and a failed one starts again where it stopped (nothing
    /// downloaded is lost); a running or waiting one pauses.
    private func primary(_ download: BCDDownload) {
        if BCDDownloadButton(paused: download.paused, failed: download.failed != nil).resumes {
            store.actions?.resumeDownload(download.id)
        } else {
            store.actions?.pauseDownload(download.id)
        }
    }

    private func ask(_ download: BCDDownload) {
        withAnimation(.easeOut(duration: 0.15)) { stopping = download.id }
        if focus.controllerActive { focus.focused = "downloads.\(download.id).keep" }
    }

    private func answered(_ download: BCDDownload) {
        withAnimation(.easeOut(duration: 0.15)) { stopping = nil }
        if focus.controllerActive { focus.focused = "downloads.\(download.id)" }
    }

    private func chip(_ title: String, symbol: String, id: String, tint: Color? = nil,
                      action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .font(BCDStyle.label(14)).lineLimit(1)
                .foregroundStyle(tint ?? BCDStyle.text)
                .padding(.horizontal, 14).frame(minHeight: 44)
                .background(BCDStyle.surfaceRaised, in: Capsule())
                .overlay(Capsule().strokeBorder(tint ?? BCDStyle.stroke, lineWidth: 1))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 22, action: action)
        .id(id)
    }

    /// Speed and time left on one line, the time first when the row is narrow.
    private func progressDetail(_ download: BCDDownload) -> String {
        var parts: [String] = []
        if download.bytesPerSecond > 0 && layout != .phonePortrait { parts.append("\(BCDFormat.bytes(Int64(download.bytesPerSecond)))/s") }
        if let eta = BCDFormat.eta(download) { parts.append(eta) }
        return parts.joined(separator: " · ")
    }

    private var roomCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            BCDEyebrow(text: "Storage")
            if let free = store.system.freeDeviceBytes, let total = store.system.totalDeviceBytes, total > 0 {
                Text("\(BCDFormat.bytes(free)) free of \(BCDFormat.bytes(total))").font(BCDStyle.label(15))
                BCDProgressBar(fraction: 1 - Double(free) / Double(total))
            }
            if let external = store.system.freeExternalBytes {
                Text("External drive: \(BCDFormat.bytes(external)) free").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.lavender)
            }
            Text(Self.backgroundNote)
                .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .bcdCard(padding: 16)
    }

    /// From iOS 26 a Steam download goes on in the background (SteamDownloadBackground.swift).
    private static var backgroundNote: String {
        if #available(iOS 26.0, *) { return BCDDownloadNotes.background(continues: true) }
        return BCDDownloadNotes.background(continues: false)
    }

    private var finishedCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            BCDEyebrow(text: "Finished today")
            if store.finishedToday.isEmpty {
                Text("Nothing yet").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
            } else {
                ForEach(store.finishedToday) { item in
                    HStack {
                        Text(item.title).lineLimit(1)
                        Spacer()
                        Text(BCDFormat.bytes(item.bytes)).foregroundStyle(BCDStyle.textSoft)
                    }
                    .font(BCDStyle.body(14))
                }
            }
        }
        .bcdCard(padding: 16)
    }
}

/// A download's main button (pure: tests/host/check-parity.py compiles it): Try again for a
/// failed download, Resume for a paused one, else Pause. Resume and Try again both ask the
/// store for the download again, which goes on where it stopped.
enum BCDDownloadButton: Equatable {
    case pause, resume, tryAgain

    init(paused: Bool, failed: Bool) { self = failed ? .tryAgain : paused ? .resume : .pause }

    var title: String {
        switch self {
        case .pause: return "Pause"
        case .resume: return "Resume"
        case .tryAgain: return "Try again"
        }
    }

    var symbol: String {
        switch self {
        case .pause: return "pause.fill"
        case .resume: return "play.fill"
        case .tryAgain: return "arrow.clockwise"
        }
    }

    /// Resume and Try again ask for the download again; Pause pauses it.
    var resumes: Bool { self != .pause }
}

/// What the Downloads tab says about leaving BCD (pure: tests/host/check-parity.py compiles it).
enum BCDDownloadNotes {
    /// `continues`: iOS 26 or later, where a Steam download goes on in the background as a task
    /// iOS shows with its progress. Before, iOS pauses it soon after BCD leaves the screen, and
    /// it picks up again when BCD is back. Either way a download waits while a game runs.
    static func background(continues: Bool) -> String {
        if continues {
            return "You can leave BCD while a game downloads: it goes on in the background, and iOS shows its progress. "
                + "Downloads wait while a game runs and carry on when it closes. Plug in the charger for big ones."
        }
        return "If you leave BCD, a download pauses soon after and picks up again when you come back. "
            + "Downloads also wait while a game runs. Plug in the charger for big ones."
    }
}
