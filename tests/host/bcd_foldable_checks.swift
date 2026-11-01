// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The foldable-iPhone gate, the inner display's shape and the scene-size tracker, compiled on the host by
// tests/host/check-bcd-foldable.py with app/Madeira/BCDFoldable.swift and app/Madeira/GuestDisplay.swift.

import Foundation
import CoreGraphics

/// GuestDisplay.configureSessionDefault publishes the virtual monitor through this C function in the app.
func winios_display_mode_changed(_ w: Int32, _ h: Int32) {}

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: String = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
    if !ok { failures += 1 }
}

@main enum Run {
    static func main() {
        // ---------------------------------------------------------------- the gate: model AND switch
        check("the foldable model's identifier is iPhone19,4 and no other", BCDFoldableDevice.identifiers == ["iPhone19,4"])
        check("the model is recognised", BCDFoldableDevice.isFoldable(machine: "iPhone19,4"))
        check("the switch is off when nothing is stored (the default)", !BCDFoldableSwitch.defaultOn && !BCDFoldableSwitch.resolve(nil))
        check("only 1 turns it on and only 0 turns it off", BCDFoldableSwitch.resolve("1") && BCDFoldableSwitch.resolve(" 1 ")
              && !BCDFoldableSwitch.resolve("0") && !BCDFoldableSwitch.resolve(" 0\n"))
        check("any other answer is the default (off)", ["", "true", "yes", "on", "2", "-1", "01", "1.0", "\u{FF11}"].allSatisfy { !BCDFoldableSwitch.resolve($0) })
        check("the foldable model with its switch on opens the gate", BCDFoldableSwitch.gate(machine: "iPhone19,4", stored: "1"))
        check("the foldable model with its switch off, stored 0, unset or garbled keeps it shut",
              ["0", nil, "", "maybe", "true"].allSatisfy { !BCDFoldableSwitch.gate(machine: "iPhone19,4", stored: $0) })

        // A different model identifier: nothing new runs, whatever the switch holds.
        var others: [String] = []
        for n in 1...5 { others.append("iPhone18,\(n)") }                                  // iPhone 17 family and Air
        for n in 1...9 where n != 4 { others.append("iPhone19,\(n)") }                      // the rest of the iPhone 18 generation
        for n in 1...16 { others.append("iPhone17,\(n)") }
        others += ["iPhone19,40", "iPhone19,", "iPhone19", "iPhone19,4 ", " iPhone19,4", "iphone19,4", "iPhone20,4", "iPhone119,4", "iPad19,4",
                   "iPad16,3", "iPad16,5", "iPad17,1", "iPad17,3", "iPad13,8", "iPhone11,2", "iPhone13,1", "iPhone14,2", "iPhone15,2", "iPhone16,2",
                   "arm64", "arm64e", "x86_64", "iPhone", "", "Watch7,1", "RealityDevice14,1", "Mac16,1"]
        var leaked: [String] = []
        for m in others {
            for stored in ["1", "0", nil, "true", ""] as [String?] where BCDFoldableSwitch.gate(machine: m, stored: stored) { leaked.append("\(m)/\(stored ?? "nil")") }
            if BCDFoldableDevice.isFoldable(machine: m) { leaked.append("\(m)/model") }
        }
        check("with any other model identifier the gate is shut for every switch value (\(others.count) identifiers, five values each)", leaked.isEmpty, leaked.joined(separator: ", "))
        check("the identifier list holds exactly one entry, so nothing but iPhone19,4 can ever match", BCDFoldableDevice.identifiers.count == 1)
        check("this host is not the foldable model, so its gate is shut", !BCDFoldableDevice.isThisDevice && !BCDFoldable.active)

        // ---------------------------------------------------------------- the display shape
        check("a shut gate returns no screen, so the existing reading of the screen stays", BCDFoldable.screen(active: false) == nil && BCDFoldable.innerScreen == nil)
        let open = BCDFoldable.screen(active: true)
        check("an open gate returns the inner display, 2670x1878 px landscape", open?.pixels == CGSize(width: 2670, height: 1878)
              && open.map { $0.points.width > $0.points.height } == true)
        let inner = ResolutionChoices.Screen(points: open!.points, pixels: open!.pixels)
        let outer = ResolutionChoices.Screen(points: CGSize(width: 678, height: 466), pixels: CGSize(width: 2034, height: 1398))
        check("the two displays have nearly one shape (1.422 and 1.455: 2.3% apart)", abs(ResolutionChoices.aspect(inner) / ResolutionChoices.aspect(outer) - 1) < 0.025,
              "\(ResolutionChoices.aspect(inner)) \(ResolutionChoices.aspect(outer))")
        let d = ResolutionChoices.defaultSize(for: inner)
        check("a new entry's default Resolution is 1152x808 (1280x720's pixel count in the inner display's shape)", d == (1152, 808), "\(d)")
        check("... it fills the inner display and is within 2.5% of the outer's shape", ResolutionChoices.fills(d.w, d.h, screen: inner)
              && abs(Double(d.w) / Double(d.h) / ResolutionChoices.aspect(outer) - 1) < 0.025)
        let groups = ResolutionChoices.groups(for: inner)
        let own = groups[0].choices.map(\.value)
        check("the screen-shaped choices are light, default, about 1080p and native", own == ["936x656", "1152x808", "1720x1208", "2670x1878"], "\(own)")
        check("the 16:9 sizes say bars above and below, the 4:3 sizes bars at the sides", groups.map(\.title).contains { $0.contains("16:9") && $0.contains("bars above and below") }
              && groups.map(\.title).contains { $0.contains("4:3") && $0.contains("bars at the sides") }, groups.map(\.title).joined(separator: " | "))
        check("no 720-lines special case: the default already has more than 720 lines", ResolutionChoices.screen720(for: inner) == nil)
        // The screens the app has known until now are not touched by any of this.
        let ipad = ResolutionChoices.Screen(points: CGSize(width: 1180, height: 820), pixels: CGSize(width: 2360, height: 1640))
        let phone = ResolutionChoices.Screen(points: CGSize(width: 956, height: 440), pixels: CGSize(width: 2868, height: 1320))
        check("an 11-inch iPad and a 19.5:9 iPhone keep their defaults", ResolutionChoices.defaultSize(for: ipad) == (1152, 800)
              && ResolutionChoices.defaultSize(for: phone) == (1408, 648))

        // ---------------------------------------------------------------- the iPad layout's minimum width
        check("the iPad layout needs 700 pt", BCDFoldableLayout.padMinimumWidth == 700)
        check("a window without a width never reads as compact", !BCDFoldableLayout.compact(forWidth: 0) && !BCDFoldableLayout.compact(forWidth: -5))
        check("the inner display in portrait (626, or 669 by the other reading), the outer display's 466 and 678 and a Split View half read as compact",
              [626.0, 669, 466, 678, 445, 313, 699.9].allSatisfy { BCDFoldableLayout.compact(forWidth: $0) })
        check("700 pt and wider do not: the inner display in landscape (890), an iPad mini (744 and 1133), an 11-inch iPad (820, 1180)",
              [700.0, 744, 820, 890, 1133, 1180, 1376].allSatisfy { !BCDFoldableLayout.compact(forWidth: $0) })

        // ---------------------------------------------------------------- the tracker
        func snap(_ w: Double, _ h: Double, _ t: Double = 0, _ l: Double = 0, _ b: Double = 0, _ r: Double = 0) -> BCDFoldableSnapshot {
            BCDFoldableSnapshot(width: w, height: h, top: t, left: l, bottom: b, right: r)
        }
        var t = BCDFoldableTracker()
        check("the first pass of a session is .first and changes nothing", t.note(snap(678, 466)) == .first && t.changes == 0 && t.previous == nil)
        check("the same size again is .unchanged", t.note(snap(678, 466)) == .unchanged && t.changes == 0)
        check("sub-point noise (0.4) is .unchanged", t.note(snap(678.4, 465.6)) == .unchanged && t.changes == 0)
        check("an unfold (678x466 to 890x626) is .resized and remembers where it came from",
              t.note(snap(890, 626)) == .resized && t.changes == 1 && t.previous == snap(678, 466) && t.last == snap(890, 626))
        check("a rotation (890x626 to 626x890) is .resized", t.note(snap(626, 890)) == .resized && t.changes == 2)
        check("a move of the safe area alone is .insets", t.note(snap(626, 890, 0, 0, 0, 60)) == .insets && t.changes == 3)
        check("each side of the safe area counts by itself", [snap(626, 890, 10, 0, 0, 60), snap(626, 890, 10, 5, 0, 60), snap(626, 890, 10, 5, 7, 60)]
              .allSatisfy { t.note($0) == .insets })
        check("a move of the window's size and the insets together is .resized", t.note(snap(466, 678, 0, 0, 0, 0)) == .resized)
        t.reset()
        check("a new session starts from .first again", t.note(snap(466, 678)) == .first && t.changes == 0)
        check("aspect: 890x626 and 626x890 are both 1.42, a zero size is 0", abs(snap(890, 626).aspect - 1.4217) < 0.001
              && abs(snap(626, 890).aspect - 1.4217) < 0.001 && snap(0, 0).aspect == 0)
        let line = BCDFoldableTracker.line(.resized, now: snap(890, 626), was: snap(678, 466), changes: 1, extra: "scene=890x626")
        check("the log line starts with [foldable], names both sizes, the aspect and the extra facts",
              line.hasPrefix("[foldable] resized from 678x466 pt") && line.contains("now 890x626 pt") && line.contains("aspect 1.42")
              && line.contains("change=1") && line.hasSuffix("scene=890x626"), line)
        check("the first line says session start", BCDFoldableTracker.line(.first, now: snap(1, 1), was: nil, changes: 0, extra: "").hasPrefix("[foldable] session start:"))

        exit(failures == 0 ? 0 : 1)
    }
}
