// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 creeper4004
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import Observation

/// Observation follows reads through BCDStyle's computed properties, including independently
/// hosted in-game panels. A palette switch never replaces a screen or resets navigation state.
@Observable final class BCDThemeStore {
    static let shared = BCDThemeStore()
    private(set) var presets: [BCDThemeProfile]
    private(set) var saved: [BCDThemeProfile] = []
    private(set) var current = BCDThemeProfile.original
    private(set) var draft: BCDThemeProfile?
    var storageError: String?
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var pendingSave: DispatchWorkItem?
    private static let storageKey = "bcd.appearance.v1"
    private struct State: Codable { var version = 1; var selected: String; var saved: [BCDThemeProfile]; var draft: BCDThemeProfile? }

    init(defaults: UserDefaults = .standard, presets: [BCDThemeProfile]? = nil) {
        self.defaults = defaults
        let bundled = Bundle.main.url(forResource: "BCDThemes", withExtension: "json")
            .flatMap { try? Data(contentsOf: $0) }.flatMap { try? JSONDecoder().decode([BCDThemeProfile].self, from: $0) }
        self.presets = (presets ?? bundled ?? [.original]).compactMap { try? $0.validated() }
        if self.presets.isEmpty { self.presets = [.original] }
        if let data = defaults.data(forKey: Self.storageKey), let state = try? JSONDecoder().decode(State.self, from: data), state.version == 1 {
            var ids = Set(self.presets.map(\.id) + ["custom"])
            saved = state.saved.compactMap { profile in
                guard let valid = try? profile.validated(), ids.insert(valid.id).inserted else { return nil }
                return valid
            }
            draft = state.draft.flatMap { try? $0.validated() }
            current = profiles.first { $0.id == state.selected } ?? self.presets[0]
        }
    }

    var profiles: [BCDThemeProfile] { presets + saved + (draft.map { [$0] } ?? []) }
    var canDelete: Bool { saved.contains { $0.id == current.id } || current.id == "custom" }
    func select(_ id: String) {
        guard let profile = profiles.first(where: { $0.id == id }) else { return }
        current = profile
        persist()
    }

    func setMainColor(_ main: BCDThemeRGB) {
        if (current.seed ?? current.colors["accent"]) == main.hex { return }
        let profile = BCDThemeProfile.generated(main: main)
        draft = profile
        current = profile
        // A color wheel sends many changes; apply every one, coalesce writes to disk.
        pendingSave?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.persist() }
        pendingSave = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2, execute: work)
    }

    func saveAs(_ name: String) throws {
        var profile = current
        profile.id = UUID().uuidString.lowercased()
        profile.name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        saved.append(try profile.validated())
        current = profile
        persist()
    }

    func importProfile(_ url: URL) throws {
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        // Limit reads before decoding, including providers that do not supply a file size.
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let data = try handle.read(upToCount: 65_537) ?? Data()
        var profile = try BCDThemeProfile.decode(data)
        // An imported file can never overwrite a built-in, saved profile or the working draft.
        profile.id = UUID().uuidString.lowercased()
        saved.append(profile)
        current = profile
        persist()
    }

    func deleteCurrent() {
        guard canDelete else { return }
        if current.id == "custom" { draft = nil } else { saved.removeAll { $0.id == current.id } }
        current = presets.first { $0.id == "bcd-violet" } ?? presets[0]
        persist()
    }

    private func persist() {
        pendingSave?.cancel()
        do {
            let state = State(selected: current.id, saved: saved, draft: draft)
            defaults.set(try JSONEncoder().encode(state), forKey: Self.storageKey)
            storageError = nil
        } catch { storageError = "Your colors could not be saved. Try selecting the profile again." }
    }
}

#if canImport(UIKit)
import SwiftUI
import UIKit

extension BCDThemeStore {
    func color(_ role: String) -> Color { current.rgb(role).color }
    func setMainColor(_ color: Color) {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        guard UIColor(color).getRed(&r, green: &g, blue: &b, alpha: &a) else { return }
        setMainColor(BCDThemeRGB(red: Double(r), green: Double(g), blue: Double(b)))
    }
}

extension BCDThemeRGB {
    var color: Color { Color(.sRGB, red: red, green: green, blue: blue, opacity: 1) }
}
#endif
