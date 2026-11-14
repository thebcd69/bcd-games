// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import Combine
import SwiftUI
import UIKit

// Updates: whether a newer BCD has been released. BCD asks GitHub's public API which release of
// thebcd69/bcd-games is the newest (no account, no token), once per app start while "Check
// automatically" is on and whenever someone taps Check for updates, and compares its tag with
// this copy's version. It never downloads anything: "Go to update" opens the release's page.
// A failed check (offline, no release published yet, an answer it cannot read) is one quiet
// line in Settings › Updates, never an alert. Engine-free: the preview app builds it too.

// MARK: - Rules and checker (Foundation and Combine only; tests/host/check-bcd-settings.py compiles this part)

/// Why a check did not give an answer, in one short line.
enum BCDUpdateFailure: Error, Equatable {
    case offline, noRelease, refused(Int), unreadable

    var line: String {
        switch self {
        case .offline: return "BCD couldn't reach GitHub. Try again later."
        case .noRelease: return "No release is published yet."
        case .refused(let status): return "GitHub didn't answer this time (\(status))."
        case .unreadable: return "GitHub's answer couldn't be read."
        }
    }
}

enum BCDUpdateRules {
    /// The numbers in a version or a tag: "v0.1.580" is [0, 1, 580].
    static func numbers(_ text: String) -> [Int] {
        text.split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
    }

    /// Whether `tag` names a newer release than this copy: its numbers against the version's,
    /// one by one; a tag with a number more than the version compares that one with the build.
    static func isNewer(_ tag: String, version: String, build: String) -> Bool {
        let theirs = numbers(tag)
        guard !theirs.isEmpty else { return false }
        var ours = numbers(version)
        if theirs.count > ours.count, let built = Int(build) { ours.append(built) }
        for index in 0..<max(theirs.count, ours.count) {
            let a = index < theirs.count ? theirs[index] : 0
            let b = index < ours.count ? ours[index] : 0
            if a != b { return a > b }
        }
        return false
    }

    /// The release GitHub's answer names (its version without a leading "v", and its page on
    /// github.com), or why there is none.
    static func release(from data: Data, status: Int) -> Result<(version: String, page: URL), BCDUpdateFailure> {
        if status == 404 { return .failure(.noRelease) }
        guard status == 200 else { return .failure(.refused(status)) }
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let tag = object["tag_name"] as? String, !numbers(tag).isEmpty,
              let link = object["html_url"] as? String, let page = URL(string: link),
              page.scheme == "https", page.host == "github.com"
        else { return .failure(.unreadable) }
        let version = tag.first == "v" || tag.first == "V" ? String(tag.dropFirst()) : tag
        return .success((version, page))
    }
}

@MainActor final class BCDUpdateChecker: ObservableObject {
    enum State: Equatable {
        case idle, checking, upToDate, updateAvailable
        case failed(String)
    }

    /// The app's checker. The first use starts the once-per-start check (when it is on).
    static let shared: BCDUpdateChecker = {
        let checker = BCDUpdateChecker()
        checker.checkOnStart()
        return checker
    }()
    static let autoCheckKey = "bcd.updates.autoCheck"
    static let endpoint = URL(string: "https://api.github.com/repos/thebcd69/bcd-games/releases/latest")!

    @Published private(set) var state = State.idle
    /// The newest release GitHub named, once a check succeeded.
    @Published private(set) var latest: (version: String, page: URL)?
    /// Check automatically: once each time BCD starts. On unless turned off.
    @Published var autoCheck: Bool {
        didSet { defaults.set(autoCheck, forKey: Self.autoCheckKey) }
    }
    /// This copy's version and build (the preview app and the host check give their own).
    var version: String
    var build: String
    /// Asks a URL and returns the answer's body and HTTP status (the preview app and the host
    /// check give their own answers).
    var fetch: (URL) async throws -> (Data, Int)
    private let defaults: UserDefaults
    private var checkedThisRun = false

    init(defaults: UserDefaults = .standard, bundle: Bundle = .main) {
        self.defaults = defaults
        autoCheck = defaults.object(forKey: Self.autoCheckKey) as? Bool ?? true
        version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        fetch = { url in
            var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
            request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
            let (data, response) = try await URLSession.shared.data(for: request)
            return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
        }
    }

    /// The home screen's chip shows only while checking automatically and a newer release exists.
    var showsChip: Bool { autoCheck && state == .updateAvailable }

    /// At most once per app start, and only while Check automatically is on.
    func checkOnStart() {
        guard autoCheck, !checkedThisRun else { return }
        checkedThisRun = true
        Task { @MainActor [weak self] in await self?.check() }
    }

    /// Check for updates, now.
    func checkNow() {
        Task { @MainActor [weak self] in await self?.check() }
    }

    func check() async {
        guard state != .checking else { return }
        state = .checking
        do {
            let (data, status) = try await fetch(Self.endpoint)
            switch BCDUpdateRules.release(from: data, status: status) {
            case .success(let release):
                latest = release
                state = BCDUpdateRules.isNewer(release.version, version: version, build: build) ? .updateAvailable : .upToDate
            case .failure(let failure):
                state = .failed(failure.line)
            }
        } catch {
            state = .failed(BCDUpdateFailure.offline.line)
        }
    }
}

// MARK: - On screen

extension BCDUpdateChecker {
    /// The newest release's page, in the browser. Nothing is downloaded by BCD.
    func openReleasePage() {
        guard let page = latest?.page else { return }
        UIApplication.shared.open(page)
    }
}

/// "Update available", beside the JIT chip on the home screen: only while Check automatically is
/// on and a newer release exists. A tap (or A) opens Settings › Updates; it never opens by itself.
struct BCDUpdateChip: View {
    @ObservedObject private var checker = BCDUpdateChecker.shared
    /// "Update" instead of "Update available": a phone held upright that also shows Home's "JIT off" chip has room for
    /// the title, both chips and Settings only with the short word (build 655).
    var compact = false
    let open: () -> Void

    var body: some View {
        if checker.showsChip {
            Button(action: open) {
                HStack(spacing: 6) {
                    Image(systemName: "arrow.down.circle.fill").font(.system(size: 12, weight: .bold))
                        .foregroundStyle(BCDStyle.glow)
                    Text(compact ? "Update" : "Update available").font(BCDStyle.label(12)).lineLimit(1)
                }
                .fixedSize()                       // whole, even in a crowded bar
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(BCDStyle.surface, in: Capsule())
                .overlay(Capsule().strokeBorder(BCDStyle.glow, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .layoutPriority(1)
            .bcdFocusable("shell.update", cornerRadius: 16, action: open)
            .accessibilityLabel("Update available")
            .accessibilityHint("Opens Settings at Updates")
        }
    }
}
