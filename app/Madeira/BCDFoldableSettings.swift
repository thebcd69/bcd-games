// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › Graphics › Foldable iPhone › Duo enhancements (thebcd69/bcd-games#43 by Paulo Eduardo Correa / pauloec; modified by BCD,
// 2026-11-11): the switch of the native interface (BCDDuoFeatures in BCDFoldable.swift). Off until a person turns it on; the answer
// applies from the next start of BCD, which the row says once it differs from what is running.
struct BCDDuoEnhancementsSettingsRow: View {
    @State private var on = BCDDuoFeatures.enabled(machine: BCDFoldableDevice.machine,
        stored: UserDefaults.standard.string(forKey: BCDDuoFeatures.key))

    var body: some View {
        BCDSettingsToggleRow(id: BCDSettingsID.control(.graphics, "duo-interface"),
            title: "Duo enhancements (experimental)", note: note, isOn: on) { value in
            BCDDuoFeatures.store(value)
            on = value
            fputs("[foldable] duo interface switch \(value ? "on" : "off") (applies from the next start of BCD; running now: \(BCDDuoFeatures.active ? "on" : "off"))\n", stderr)
        }
    }

    private var note: String {
        let base = "Liquid Glass, system tabs and a sidebar, and controller navigation for iPhone Duo. Off: the interface BCD had before."
        return on == BCDDuoFeatures.active ? base : base + " Applies the next time BCD starts."
    }
}

// Settings › Graphics › Foldable iPhone: the experimental switch (BCDFoldable.swift). Shown on the foldable model only
// (BCDSettingsSections.swift tests BCDFoldableDevice.isThisDevice before it adds the group), so no other device's Settings
// has the row. It keeps its answer in UserDefaults, not in bcd.cfg, and the answer applies from the next start of BCD
// (the gate is read once per run), which the row says once it differs from what is running.

struct BCDFoldableSettingsRow: View {
    @State private var on = BCDFoldableSwitch.resolve(BCDFoldableSwitch.stored)

    var body: some View {
        BCDSettingsToggleRow(id: BCDSettingsID.control(.graphics, "foldable"), title: "Foldable iPhone support (experimental)",
                             note: note, isOn: on) { value in
            BCDFoldableSwitch.store(value)
            on = value
            fputs("[foldable] switch \(value ? "on" : "off") (applies from the next start of BCD; running now: \(BCDFoldable.active ? "on" : "off"))\n", stderr)
        }
    }

    private var note: String {
        let base = "While a game runs, the touch controls and the picture follow the screen when the phone is folded or "
            + "unfolded, and new games start with the inner screen's shape. It has not been tried on a real device yet."
        return on == BCDFoldable.active ? base : base + " Applies the next time BCD starts."
    }
}
