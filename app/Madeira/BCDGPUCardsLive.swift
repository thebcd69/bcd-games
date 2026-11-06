// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (commit adec653)
// under GPL-3.0-or-later and modified for BCD in October 2026. See THIRD-PARTY-NOTICES.md.

import Foundation
import Metal

// "Look like this GPU" at a launch (BCDGPUCards.swift has the cards and the rules). The game's line
// `gpu-card` (else bcd.cfg's, so one line can serve every game) picks a card; this puts it into the
// launch the way every other per-game choice gets in: DXMT's options for DXGI and Direct3D 9
// (ContentView composes DXMT_CONFIG and calls this just before it sets it) and the environment
// win32u reads for Windows' own adapter (build/win32u-unix/sysparams_ios.c). It runs once per
// launch, on the launch worker, and does nothing at all when no card is chosen.
// Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).

enum BCDGPUCardsLive {
    /// The card the launch asks for, nil when none is chosen (or the line names a card this build does not know).
    static func chosen() -> (card: BCDGPUCard?, word: String) {
        let word = (MadeiraConfig.gameValue("gpu-card") ?? MadeiraConfig.get("gpu-card") ?? "").trimmingCharacters(in: .whitespaces)
        return (BCDGPUCards.card(word), word)
    }

    /// Puts the launch's card into `options` (the DXMT options so far) and the environment.
    static func apply(to options: inout [String]) {
        let (card, word) = chosen()
        guard let card else {
            if !word.isEmpty { LogStore.shared.log("[gpu-card] \"\(word)\" is not a card this build knows: the game sees what it always saw") }
            return
        }
        let merged = BCDGPUCards.merged(options, with: card)
        options = merged.options
        let env = BCDGPUCards.environment(card, nativeName: card.isApple ? MTLCreateSystemDefaultDevice()?.name : nil)
        for (name, value) in env.set { setenv(name, value, 1) }
        for name in env.unset { unsetenv(name) }
        LogStore.shared.log("[gpu-card] \(card.id): \(env.set["MADEIRA_GPU_NAME"] ?? "BCD's own Apple GPU") \(card.vendorHex):\(card.deviceHex)"
                            + " in DXGI, Direct3D 9 and Windows; nvapi=\(card.isNVIDIA ? 1 : 0)"
                            + (merged.replaced > 0 ? "; \(merged.replaced) adapter option\(merged.replaced == 1 ? "" : "s") of the game's DXMT lines replaced" : "")
                            + (merged.namesDropped ? "; the names left out, DXMT's \(BCDGPUCards.dxmtConfigLimit)-character limit" : ""))
    }

    /// Before a launch of a game that keeps the card's name in its prefs (Fallout 3 / New Vegas, BCDGPUCards.prefsFile):
    /// the name is put on the card the game will see, so it does not reopen its launcher. Nothing for any other game.
    static func syncGamePrefs(appID: Int?, program: String?) {
        guard let relative = BCDGPUCards.prefsFile(appID: appID, program: program) else { return }
        let card = chosen().card
        let metalName = MTLCreateSystemDefaultDevice()?.name
        let cardName = card.flatMap { $0.isApple ? nil : $0.name }
        let users = LibraryModel.drive.appendingPathComponent("users", isDirectory: true)
        for user in (try? FileManager.default.contentsOfDirectory(atPath: users.path)) ?? [] {
            let file = users.appendingPathComponent(user).appendingPathComponent("Documents/My Games/" + relative)
            guard let data = try? Data(contentsOf: file) else { continue }
            let utf8 = String(data: data, encoding: .utf8)
            guard let text = utf8 ?? String(data: data, encoding: .isoLatin1),
                  let updated = BCDGPUCards.prefsText(text, cardName: cardName, metalName: metalName),
                  let out = updated.data(using: utf8 != nil ? .utf8 : .isoLatin1) else { continue }
            do {
                try out.write(to: file, options: .atomic)
                LogStore.shared.log("[gpu-card] \(relative): sD3DDevice set to the card the game sees")
            } catch {
                LogStore.shared.log("[gpu-card] \(relative): not written: \(error.localizedDescription)")
            }
        }
    }
}
