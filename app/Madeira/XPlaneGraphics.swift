// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// X-Plane 12 at its lowest graphics settings (`xplane-graphics = lowest` in the game's settings; 2026-11-22).
//
// X-Plane 12 reached its flight on the owner's iPhone (build 699) and stopped twice there: once with "X-Plane is totally out of
// memory", once with "Failed to allocate a GPU buffer". Both times the address map was full ([mmap-place] NO GAP FITS), and
// X-Plane's own box said its rendering options were too high. The settings were X-Plane's defaults: it saves its preferences
// (Output/preferences/X-Plane.prf) only when it quits cleanly, and it never had, so every start took the defaults again.
// It could not be lowered by hand either: without preferences X-Plane opens straight into its first-time flight, with no main
// menu.
//
// Those defaults come from X-Plane's own Resources/settings.txt: one `SETTING <type> <ui> <name> <max>` block per setting, with
// `SETTING_DEFAULT <config> <value>` lines (configs all, vkmtl, vkmtl_async, m1 and safe). Before X-Plane starts, BCD sets
// the default of every graphics setting there to its lowest value, for every config but "safe" (X-Plane's own safe mode keeps
// its values). Nothing else in the file changes, byte for byte. A setting the player changes in X-Plane and saves by quitting
// is kept in X-Plane.prf, which X-Plane reads over these defaults, so the player's own choice still wins.
//
// The file as X-Plane shipped it is kept beside it (settings.txt.bcd-original) and is put back when the line is taken out.
// A new settings.txt from an X-Plane update is kept as the new original before it is lowered.

import Foundation

enum XPlaneGraphics {
    /// The game-settings key and its one value.
    static let key = "xplane-graphics"
    static let lowestValue = "lowest"

    /// Every graphics setting of X-Plane 12's settings.txt and its lowest value (from the file's own menus and tables). The
    /// G-load dimming is not graphics and is left alone. Anisotropic filtering's lowest is 1 (its table has no 0).
    static let lowest: [String: Int] = [
        "renopt_draw_3d_04": 0,              // 3-d drawing detail: minimal
        "renopt_static_acf": 0,              // parked aircraft at airports: off
        "renopt_boats": 0,                   // boats in the water: off
        "renopt_vegetation_3d": 0,           // 3D trees: 2d only
        "renopt_vegetation_quality_04": 0,   // tree quality: very low
        "renopt_draw_distance04": 0,         // drawing range: minimal
        "renopt_clouds_02": 0,               // cloud quality: low
        "renopt_FSR_04": 0,                  // FSR: off
        "renopt_SSAO_04": 0,                 // SSAO: off
        "renopt_shadow_quality_04": 0,       // 3-d shadows: the lowest level
        "renopt_MSAA": 0,                    // HDR anti-aliasing: none
        "renopt_FXAA": 0,                    // FXAA: off
        "renopt_tex_res_04": 0,              // texture resolution: the lowest
        "renopt_ANISO": 1,                   // anisotropic filtering: none
    ]

    static let settingsRelative = "Resources/settings.txt"
    static let originalSuffix = ".bcd-original"

    /// Whether `text` is X-Plane 12's rendering settings file: its header (`A`, a version, `SETTINGS`) and at least one of the
    /// settings above.
    static func isSettingsFile(_ text: String) -> Bool {
        let head = text.split(whereSeparator: \.isNewline).prefix(3).map { $0.trimmingCharacters(in: .whitespaces) }
        guard head.count == 3, head[0] == "A" || head[0] == "I", Int(head[1]) != nil, head[2] == "SETTINGS" else { return false }
        return lowest.keys.contains { text.contains("SETTING ") && text.contains(" \($0) ") }
    }

    /// The file with every graphics setting's defaults at their lowest (all configs but "safe"), every other byte kept;
    /// nil when it is not X-Plane 12's settings file. A file already at its lowest comes back unchanged.
    static func lowered(_ text: String) -> String? {
        lowering(text)?.text
    }

    /// `lowered` and how many defaults it changed. It works on the UTF-8 bytes: in a Swift String "\r\n" is one Character,
    /// so a file with Windows line ends would not split into lines by "\n" (the host check's CRLF block). Only ASCII digits
    /// are replaced, so the bytes stay valid UTF-8.
    static func lowering(_ text: String) -> (text: String, changed: Int)? {
        guard isSettingsFile(text) else { return nil }
        let bytes = Array(text.utf8)
        var out: [UInt8] = []
        out.reserveCapacity(bytes.count)
        var current: [UInt8]?
        var changed = 0
        var start = 0
        let blank: Set<UInt8> = [0x20, 0x09, 0x0D, 0x0A]   // space, tab, CR, LF
        while start < bytes.count {
            // One line with its own ending ("\n" or "\r\n"), so the ends stay as they were.
            var end = start
            while end < bytes.count, bytes[end] != 0x0A { end += 1 }
            if end < bytes.count { end += 1 }
            // The line's words as byte ranges.
            var words: [Range<Int>] = []
            var i = start
            while i < end {
                while i < end, blank.contains(bytes[i]) { i += 1 }
                let w = i
                while i < end, !blank.contains(bytes[i]) { i += 1 }
                if i > w { words.append(w..<i) }
            }
            let word = { (k: Int) -> [UInt8] in Array(bytes[words[k]]) }
            if words.count >= 4, word(0) == Array("SETTING".utf8) {
                current = word(3)
            } else if words.count >= 3, word(0) == Array("SETTING_DEFAULT".utf8), word(1) != Array("safe".utf8),
                      let name = current, let value = lowest[String(decoding: name, as: UTF8.self)],
                      Int(String(decoding: word(2), as: UTF8.self)) != nil, word(2) != Array(String(value).utf8) {
                // Only the value word changes: everything before it and after it stays.
                out += bytes[start..<words[2].lowerBound]
                out += Array(String(value).utf8)
                out += bytes[words[2].upperBound..<end]
                changed += 1
                start = end
                continue
            }
            out += bytes[start..<end]
            start = end
        }
        return (String(decoding: out, as: UTF8.self), changed)
    }

    /// What the game's settings ask for: true for `xplane-graphics = lowest`, the last line winning.
    static func wanted(settings text: String) -> Bool {
        var value: String?
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            if line[..<eq].trimmingCharacters(in: .whitespaces) == key {
                value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            }
        }
        return value?.lowercased() == lowestValue
    }

    /// Before a start of the game installed in `folder`: lowest wanted, settings.txt is lowered (its original kept first);
    /// not wanted, a file BCD lowered is put back. Returns the log line, or nil when nothing applies (no X-Plane 12 here, or
    /// nothing to do and nothing done before).
    static func prepare(folder: URL, wanted: Bool) -> String? {
        let fm = FileManager.default
        let file = folder.appendingPathComponent(settingsRelative)
        let original = folder.appendingPathComponent(settingsRelative + originalSuffix)
        guard let data = try? Data(contentsOf: file), data.count <= 1 << 20, let text = String(data: data, encoding: .utf8),
              let (low, changed) = lowering(text) else { return nil }
        if wanted {
            if changed == 0 {
                return "[xplane-graphics] lowest: the defaults are already at their lowest"
            }
            do {
                // The file as it is now is X-Plane's own (first time, or a new one from an update): it is the original.
                try data.write(to: original, options: .atomic)
                try Data(low.utf8).write(to: file, options: .atomic)
                return "[xplane-graphics] lowest: \(changed) defaults lowered in Resources/settings.txt (X-Plane's own kept as settings.txt\(originalSuffix))"
            } catch {
                return "[xplane-graphics] lowest: settings.txt could not be written (\(error.localizedDescription)); X-Plane keeps its defaults"
            }
        }
        guard let kept = try? Data(contentsOf: original), let keptText = String(data: kept, encoding: .utf8) else { return nil }
        // Put back only a file that is exactly BCD's lowering of the kept original; anything else is X-Plane's own now.
        guard lowered(keptText) == text else {
            try? fm.removeItem(at: original)
            return "[xplane-graphics] off: settings.txt is X-Plane's own (an update replaced it); the old copy was removed"
        }
        do {
            try kept.write(to: file, options: .atomic)
            try fm.removeItem(at: original)
            return "[xplane-graphics] off: X-Plane's own settings.txt is back"
        } catch {
            return "[xplane-graphics] off: X-Plane's own settings.txt could not be put back (\(error.localizedDescription))"
        }
    }
}
