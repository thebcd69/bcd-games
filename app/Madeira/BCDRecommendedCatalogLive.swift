// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// Settings › Recommended settings on the engine (the page: BCDRecommendedCatalogView.swift): the list the app is using, as a catalog.
// This file is in tools/ui-preview.py's ENGINE_BOUND, so the preview app never compiles it; the preview shows a sample.
//
// The catalog is built once per list in use: the page asks, a background task reads the list's file again and builds it (the
// 373-odd rules are parsed once, off the main thread), and every later visit gets the same value. A newer download that goes into
// use while the app runs has another version, so the next visit builds again; nothing else changes it.

@MainActor final class BCDRecommendedLive: BCDRecommendedBackend {
    private var built: (key: String, catalog: BCDRecommendedCatalog)?
    private var running: Task<BCDRecommendedCatalog, Never>?
    private var runningKey = ""

    func load() async -> BCDRecommendedCatalog? {
        let active = GameRecommendations.active
        let key = "\(active.origin.rawValue)/\(active.version)/\(active.rules.count)"
        if let built, built.key == key { return built.catalog }
        guard !active.rules.isEmpty else { return nil }
        let task: Task<BCDRecommendedCatalog, Never>
        if let running, runningKey == key {
            task = running
        } else {
            task = Task.detached(priority: .userInitiated) { BCDRecommendedCatalogSource.load(active: active) }
            running = task
            runningKey = key
        }
        let catalog = await task.value
        built = (key, catalog)
        if runningKey == key { running = nil }
        return catalog
    }

    func copy(_ text: String) { UIPasteboard.general.string = text }
}
