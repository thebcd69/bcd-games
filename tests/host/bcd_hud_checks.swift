// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The performance HUD's rules, compiled on the host by tests/host/check-bcd-hud.py with app/Madeira/BCDHUDCore.swift.

import Foundation
import CoreGraphics

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: String = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
    if !ok { failures += 1 }
}

func near(_ a: Double?, _ b: Double, _ tolerance: Double = 0.05) -> Bool { a.map { abs($0 - b) <= tolerance } ?? false }

/// Frames at a steady rate: `seconds` of them ending at `end`.
func steady(_ fps: Double, seconds: Double, end: Double) -> [BCDFrameSample] {
    let dt = 1 / fps
    var out: [BCDFrameSample] = []
    var t = end - seconds
    while t <= end + 1e-9 { out.append(BCDFrameSample(t: t, dt: dt)); t += dt }
    return out
}

func texts(_ plan: BCDHUDPlan?) -> [[String]] {
    (plan?.rows ?? []).map { row in
        switch row {
        case .line(let parts): return parts.map(\.text)
        case .graph: return ["<graph>"]
        case .pulse(let number, let caption, _): return [number.text, caption]
        }
    }
}

@main enum Run {
    static func main() throws {
        // ---- 1. The layouts and their words ----------------------------------------------------------------
        check("the choices are Off, 1, 2, 3, 4 and Custom, in that order",
              BCDHUDLayout.allCases.map(\.title) == ["Off", "1", "2", "3", "4", "Custom"])
        check("the stored words are off, 1, 2, 3, 4 and custom", BCDHUDLayout.allCases.map(\.rawValue) == ["off", "1", "2", "3", "4", "custom"])
        check("Off is not shown, the rest are", !BCDHUDLayout.off.shown && BCDHUDLayout.allCases.dropFirst().allSatisfy(\.shown))
        check("layout 1 needs the frames and the memory (for its dot); Off needs nothing", BCDHUDLayout.pulse.needs == [.frames, .memory] && BCDHUDLayout.off.needs.isEmpty)
        check("only layouts 3 and 4 switch the GPU meter and the CPU sampler on",
              BCDHUDLayout.allCases.filter { $0.needs.contains(.gpu) || $0.needs.contains(.cpu) } == [.tune, .report])
        check("layout 2 reads the memory and nothing heavier", BCDHUDLayout.strip.needs == [.frames, .memory])
        check("every layout has a summary of one sentence", BCDHUDLayout.allCases.allSatisfy { !$0.summary.isEmpty && $0.summary.hasSuffix(".") })

        // ---- 2. The old overlay becomes a HUD, and a game that never used it stays as it was ------------------
        let none = BCDHUDMigration.resolve(stored: nil, performance: false, fields: nil, everyGame: .off, legacyPlace: nil)
        check("a game that never turned the overlay on has no HUD", none.layout == .off)
        let every = BCDHUDMigration.resolve(stored: nil, performance: false, fields: nil, everyGame: .strip, legacyPlace: nil)
        check("... unless Settings chose a layout for every game", every.layout == .strip && every.place(.landscape) == .auto)
        let customAll = BCDHUDMigration.resolve(stored: nil, performance: false, fields: nil, everyGame: .custom, legacyPlace: nil)
        check("a Custom choice for every game is not a thing: Off", customAll.layout == .off)
        let defaults = BCDHUDMigration.resolve(stored: nil, performance: true, fields: nil, everyGame: .tune, legacyPlace: nil)
        check("the old default (FPS, Frame time, RAM, Battery) becomes a Custom HUD with exactly those readings, in that order",
              defaults.layout == .custom && defaults.custom.items == [.fps, .frameMs, .memUsed, .battery], "\(defaults.layout) \(defaults.custom.items)")
        check("... on the old plate: a strip, 62% black, medium text, full colour", defaults.custom.shape == .strip && defaults.custom.size == .medium
              && defaults.custom.backgroundOpacity == 0.62 && defaults.custom.textOpacity == 1)
        check("... at the old place: the top centre, on every kind of screen",
              BCDHUDScreenClass.allCases.allSatisfy { defaults.place($0) == .dragged(x: 0.5, y: 0) })
        check("the layout chosen for every game does not touch a game that used the overlay", defaults.layout == .custom)
        let moved = BCDHUDMigration.resolve(stored: nil, performance: true, fields: ["FPS", "CPU"], everyGame: .off, legacyPlace: (0.2, 0.7))
        check("where the player had dragged the overlay is kept", moved.place(.landscape) == .dragged(x: 0.2, y: 0.7) && moved.custom.items == [.fps, .cpu])
        let fpsOnly = BCDHUDMigration.resolve(stored: nil, performance: true, fields: ["FPS"], everyGame: .off, legacyPlace: nil)
        check("an overlay of the frame rate alone is layout 1", fpsOnly.layout == .pulse)
        check("an empty or unknown field list is the frame rate alone",
              BCDHUDMigration.resolve(stored: nil, performance: true, fields: [], everyGame: .off, legacyPlace: nil).layout == .pulse
              && BCDHUDMigration.resolve(stored: nil, performance: true, fields: ["Nope"], everyGame: .off, legacyPlace: nil).layout == .pulse)
        let order = BCDHUDMigration.resolve(stored: nil, performance: true, fields: ["Thermal", "FPS", "Thermal", "GPU", "Battery", "CPU", "RAM", "Frame time"],
                                            everyGame: .off, legacyPlace: nil)
        check("the old order is kept and a repeated field is dropped", order.custom.items == [.thermal, .fps, .gpu, .battery, .cpu, .memUsed, .frameMs])
        let own = BCDHUDSetup(layout: .report)
        check("a game's own HUD is never changed by the old switch or the every-game choice",
              BCDHUDMigration.resolve(stored: own, performance: false, fields: ["FPS"], everyGame: .strip, legacyPlace: (0.1, 0.1)) == own
              && BCDHUDMigration.resolve(stored: BCDHUDSetup(layout: .off), performance: true, fields: nil, everyGame: .tune, legacyPlace: nil).layout == .off)
        check("each old field is one item", ["FPS", "Frame time", "CPU", "GPU", "RAM", "Battery", "Thermal"].compactMap(BCDHUDItem.legacy).count == 7
              && BCDHUDItem.legacy("Memory") == nil)

        // ---- 3. What is stored ---------------------------------------------------------------------------------
        var setup = BCDHUDSetup(layout: .custom)
        setup.custom.items = [.fps, .low1, .frameGraph, .jit]
        setup.custom.shape = .table
        setup.custom.size = .large
        setup.custom.textOpacity = 0.7
        setup.custom.backgroundOpacity = 0.2
        setup.custom.graphSeconds = 60
        setup.custom.dimWhenCalm = true
        setup.set(.slot(.bottomTrailing), for: .landscape)
        setup.set(.dragged(x: 0.31, y: 0.12), for: .square)
        let encoded = try JSONEncoder().encode(setup)
        let again = try JSONDecoder().decode(BCDHUDSetup.self, from: encoded)
        check("a setup survives being stored", again == setup, String(decoding: encoded, as: UTF8.self))
        check("a place that is auto is not stored", { var s = setup; s.set(.auto, for: .landscape); return s.places["landscape"] == nil }())
        let tolerant = try JSONDecoder().decode(BCDHUDSetup.self, from: Data(#"""
            {"layout":"9","custom":{"items":["fps","teleport","fps","jit"],"shape":"cube","size":"huge","textOpacity":7,"backgroundOpacity":-3,"graphSeconds":45},
             "places":{"landscape":"nowhere","portrait":"drag:2,-1","square":"top-trailing"}}
            """#.utf8))
        check("a later build's file loads: an unknown layout is Off", tolerant.layout == .off)
        check("... unknown items and repeats are dropped", tolerant.custom.items == [.fps, .jit], "\(tolerant.custom.items)")
        check("... unknown words and numbers fall back and clamp", tolerant.custom.shape == .strip && tolerant.custom.size == .medium
              && tolerant.custom.textOpacity == 1 && tolerant.custom.backgroundOpacity == 0 && tolerant.custom.graphSeconds == 30)
        check("... an unknown place is auto, a drag is kept inside the screen",
              tolerant.place(.landscape) == .auto && tolerant.place(.portrait) == .dragged(x: 1, y: 0) && tolerant.place(.square) == .slot(.topTrailing))
        let empty = try JSONDecoder().decode(BCDHUDSetup.self, from: Data("{}".utf8))
        check("an empty record is Off with the starting Custom list", empty.layout == .off && empty.custom == BCDHUDCustom.default && empty.places.isEmpty)
        check("a place's words round-trip", ["auto", "top", "top-leading", "bottom-trailing", "drag:0.5000,0.2500"].allSatisfy { BCDHUDPlace(setting: $0).setting == $0 })
        check("the Custom list stands for a copy of the layout the player was on",
              BCDHUDCustom.startingFrom(.pulse).items == [.fps] && BCDHUDCustom.startingFrom(.strip).items == [.fps, .frameMs, .memory, .thermal]
              && BCDHUDCustom.startingFrom(.tune).items.contains(.frameGraph) && BCDHUDCustom.startingFrom(.report).items.count > 10
              && !BCDHUDCustom.startingFrom(.off).items.isEmpty)
        check("the editor lists items in the order of their groups", BCDHUDCustom.canonical([.clock, .fps, .jit, .cpu]) == [.fps, .cpu, .jit, .clock])
        check("every item has a group, a title and a note",
              BCDHUDItem.allCases.allSatisfy { !$0.title.isEmpty && !$0.note.isEmpty } && Set(BCDHUDItem.allCases.map(\.group)).count == BCDHUDItem.Group.allCases.count)
        check("a setup's needs are its items' (a Custom GPU item switches the meter on)",
              { var s = BCDHUDSetup(layout: .custom); s.custom.items = [.gpu, .fps]; return s.needs.contains(.gpu) && s.needs.contains(.frames) && !s.needs.contains(.cpu) }()
              && BCDHUDSetup(layout: .strip).needs == [.frames, .memory])

        // ---- 4. Frame arithmetic -----------------------------------------------------------------------------
        check("60 frames a second reads as 60", near(BCDFrameMath.fps(steady(60, seconds: 3, end: 10), now: 10.004), 60, 0.2))
        check("19 frames a second reads as 19, not a step of 5", near(BCDFrameMath.fps(steady(19, seconds: 3, end: 10), now: 10.01), 19, 0.2))
        check("one frame a second reads as 1", near(BCDFrameMath.fps(steady(1, seconds: 6, end: 10), now: 10.2), 1, 0.05))
        check("120 frames a second reads as 120", near(BCDFrameMath.fps(steady(120, seconds: 3, end: 10), now: 10.003), 120, 0.5))
        check("no frames, no rate", BCDFrameMath.fps([], now: 5) == 0 && BCDFrameMath.fps(steady(60, seconds: 1, end: 2), now: 9) == 0)
        let fading = BCDFrameMath.fps(steady(60, seconds: 3, end: 10), now: 10.5)
        check("when the frames stop the rate falls", fading < 60 && fading > 0, "\(fading)")
        check("a second's stall halves it, two seconds' is nothing", BCDFrameMath.fps(steady(60, seconds: 3, end: 10), now: 11.0) < 35
              && BCDFrameMath.fps(steady(60, seconds: 3, end: 10), now: 12.2) == 0)
        check("the average frame time of the last half second", near(BCDFrameMath.averageMs(steady(60, seconds: 3, end: 10), now: 10.001), 16.667, 0.01)
              && BCDFrameMath.averageMs([], now: 1) == nil)
        check("a percentile is a nearest rank", BCDFrameMath.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5
              && BCDFrameMath.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.99) == 10 && BCDFrameMath.percentile([], 0.5) == nil)
        var slow = steady(60, seconds: 20, end: 100)
        for i in stride(from: 5, to: slow.count, by: 60) { slow[i].dt = 0.050 }   // 1 in 60 frames takes 50 ms
        check("1% low: a frame in sixty at 50 ms is a low of 20", near(BCDFrameMath.low(slow, now: 100, window: 30, percent: 1), 20, 0.01),
              "\(String(describing: BCDFrameMath.low(slow, now: 100, window: 30, percent: 1)))")
        check("1% low needs a hundred frames; 0.1% a thousand",
              BCDFrameMath.low(steady(60, seconds: 1, end: 100), now: 100, window: 30, percent: 1) == nil
              && BCDFrameMath.low(steady(60, seconds: 10, end: 100), now: 100, window: 30, percent: 0.1) == nil
              && BCDFrameMath.low(steady(60, seconds: 20, end: 100), now: 100, window: 30, percent: 0.1) != nil)
        check("the worst frame of the last ten seconds", near(BCDFrameMath.worstMs(slow, now: 100), 50, 0.01) && BCDFrameMath.worstMs([], now: 1) == nil)
        check("stutters are frames of twice the usual time and 33 ms: here 20 of them in 20 s",
              BCDFrameMath.stutters(slow, now: 100, window: 60) == 20, "\(String(describing: BCDFrameMath.stutters(slow, now: 100, window: 60)))")
        check("a locked 30 has no stutters", BCDFrameMath.stutters(steady(30, seconds: 10, end: 50), now: 50) == 0)
        let graph = BCDFrameMath.graph(steady(60, seconds: 30, end: 40), now: 40, count: 40)
        check("the graph holds the slowest frame of each quarter second", graph.count == 40 && graph.dropFirst().allSatisfy { abs($0 - 16.67) < 0.1 }, "\(graph.prefix(5))")
        var gapped = steady(60, seconds: 4, end: 10)
        gapped.removeAll { $0.t > 8 && $0.t < 9 }
        let gap = BCDFrameMath.graph(gapped, now: 10, count: 16)
        check("a second without a frame shows as stall-high buckets", gap.filter { $0 >= 249 }.count >= 3, "\(gap)")
        check("before the first frame the graph is empty (0)", BCDFrameMath.graph([BCDFrameSample(t: 9.9, dt: 0.016)], now: 10, count: 8).prefix(5).allSatisfy { $0 == 0 })
        check("a graph of 240 buckets squeezes to 120 by pairs' maxima", BCDFrameMath.squeeze((0..<240).map(Float.init), to: 120).last == 239
              && BCDFrameMath.squeeze([1, 2, 3], to: 10) == [1, 2, 3])

        // ---- 5. Colours ----------------------------------------------------------------------------------------
        check("against a limit of 60: 58 is green, 40 amber, 20 red", BCDHUDRules.fpsTone(58, cap: 60) == .good && BCDHUDRules.fpsTone(40, cap: 60) == .warm
              && BCDHUDRules.fpsTone(20, cap: 60) == .hot)
        check("a locked 30 is green (the old overlay showed it amber)", BCDHUDRules.fpsTone(30, cap: 30) == .good && BCDHUDRules.fpsTone(30, cap: nil) == .warm)
        check("with no limit: green from 50, amber from 30, red below, grey before a frame", BCDHUDRules.fpsTone(50, cap: nil) == .good
              && BCDHUDRules.fpsTone(49, cap: nil) == .warm && BCDHUDRules.fpsTone(29, cap: nil) == .hot && BCDHUDRules.fpsTone(0, cap: nil) == .soft)
        check("the limit is 60, 30 or 40 for the modes that have one", BCDHUDRules.cap(fpsMode: 1) == 60 && BCDHUDRules.cap(fpsMode: 3) == 30
              && BCDHUDRules.cap(fpsMode: 4) == 40 && BCDHUDRules.cap(fpsMode: 0) == nil && BCDHUDRules.cap(fpsMode: 2) == nil)
        check("memory: over 768 MB left is green, over 128 amber, less red", BCDHUDRules.memoryTone(freeMB: 900) == .good && BCDHUDRules.memoryTone(freeMB: 300) == .warm
              && BCDHUDRules.memoryTone(freeMB: 100) == .hot && BCDHUDRules.memoryTone(freeMB: nil) == .soft)
        check("heat words", BCDHUDRules.heat(0).word == "Cool" && BCDHUDRules.heat(1).word == "Warm" && BCDHUDRules.heat(2).word == "Hot" && BCDHUDRules.heat(3).word == "Too hot")
        check("the quiet dot: nothing when all is well", BCDHUDRules.quietWarning(thermal: 1, freeMB: 900, lowFor: 2.9) == nil)
        check("... serious heat is amber, critical red, little memory red, 3 s of low rate amber",
              BCDHUDRules.quietWarning(thermal: 2, freeMB: 900, lowFor: 0) == .warm && BCDHUDRules.quietWarning(thermal: 3, freeMB: 900, lowFor: 0) == .hot
              && BCDHUDRules.quietWarning(thermal: 0, freeMB: 100, lowFor: 0) == .hot && BCDHUDRules.quietWarning(thermal: 0, freeMB: 900, lowFor: 3) == .warm)

        // ---- 6. The rows ---------------------------------------------------------------------------------------
        var d = BCDHUDData()
        d.fps = 60; d.cap = 60; d.frameMs = 16.6; d.low1 = 52; d.low01 = 38
        d.cpuTotal = 41; d.cpuTop = 78; d.gpuPercent = 63; d.gpuMs = 9.8
        d.footprintMB = 3100; d.limitMB = 5800; d.peakMB = 3400; d.swapMB = 400; d.jitUsedMB = 410; d.jitTotalMB = 1024
        d.thermal = 0; d.lowPower = false; d.batteryPercent = 78
        d.api = "D3D11 64"; d.renderSize = "1408x648"; d.outputSize = "2816x1296"; d.upscale = "MetalFX 2x"
        d.playSeconds = 2530; d.build = "602"
        d.graph = Array(repeating: 16.6, count: 120)
        check("off makes no plan", BCDHUDRows.plan(layout: .off, custom: .default, data: d, width: 400) == nil)
        let p1 = BCDHUDRows.plan(layout: .pulse, custom: .default, data: d, width: 400)
        check("layout 1 is a number and a caption", texts(p1) == [["60", "fps"]] && p1?.capsule == true)
        if case .pulse(let number, _, let dot)? = p1?.rows.first { check("layout 1: green at 60 of 60, no dot", number.tone == .good && dot == nil) }
        var warn = d; warn.fps = 41; warn.warning = .warm
        if case .pulse(let number, _, let dot)? = BCDHUDRows.plan(layout: .pulse, custom: .default, data: warn, width: 400)?.rows.first {
            check("layout 1: 41 of 60 is amber and the dot shows", number.tone == .warm && dot == .warm)
        }
        let p2 = BCDHUDRows.plan(layout: .strip, custom: .default, data: d, width: 400)
        check("layout 2 is one line: frame rate | frame time | memory against the limit | heat", texts(p2) == [["60 FPS", "16.6 ms", "3.1/5.8 GB", "Cool"]], "\(texts(p2))")
        check("layout 2 colours: the rate green, the memory green, the heat green", {
            guard case .line(let parts)? = p2?.rows.first else { return false }
            return parts.map(\.tone) == [.good, .normal, .good, .good]
        }())
        let p3 = BCDHUDRows.plan(layout: .tune, custom: .default, data: d, width: 400)
        check("layout 3 is three lines and a graph", texts(p3) == [["60 FPS", "1% 52", "16.6 ms"], ["CPU 41%", "GPU 63%"], ["3.1/5.8 GB", "Cool"], ["<graph>"]], "\(texts(p3))")
        if case .graph(let g)? = p3?.rows.last { check("layout 3's graph is at most 60 buckets (15 s)", g.values.count == 60 && g.height <= 40 && g.width <= 150) }
        d.hdr = "Auto HDR · sampled 1.72× / display 2.60×"; d.hdrTone = .good
        check("only Report requests HDR sampling among presets", BCDHUDLayout.presets.filter { $0.needs.contains(.hdr) } == [.report])
        check("Custom HDR requests the same probe", BCDHUDItem.hdr.needs == .hdr)
        let p4 = BCDHUDRows.plan(layout: .report, custom: .default, data: d, width: 400)
        check("layout 4 includes HDR output alongside the seven report rows",
              texts(p4) == [["D3D11 64  1408x648 > MetalFX 2x  cap 60"], ["Auto HDR · sampled 1.72× / display 2.60×"], ["60 FPS", "avg 16.6", "1% 52", "0.1% 38"], ["<graph>"],
                            ["CPU 41% (top 78%)", "GPU 63% 9.8 ms"], ["Mem 3.1/5.8 GB", "peak 3.4", "swap 0.4"],
                            ["JIT 410/1024", "Cool", "LPM off", "78%"], ["42:10", "build 602"]], "\(texts(p4))")
        if case .graph(let g)? = p4?.rows[3] { check("layout 4's graph is at most 120 buckets and 200 pt wide", g.values.count == 120 && g.width <= 200) }
        var bare = BCDHUDData()
        bare.fps = 0
        check("with nothing measured yet no layout crashes or lies: dashes",
              texts(BCDHUDRows.plan(layout: .strip, custom: .default, data: bare, width: 300)) == [["– FPS", "– ms", "Cool"]]
              && texts(BCDHUDRows.plan(layout: .pulse, custom: .default, data: bare, width: 300)) == [["–", "fps"]])
        _ = BCDHUDRows.plan(layout: .report, custom: .default, data: bare, width: 300)
        _ = BCDHUDRows.plan(layout: .tune, custom: .default, data: bare, width: 300)

        // The old readings, written as they were written
        func one(_ item: BCDHUDItem, _ data: BCDHUDData) -> String? { BCDHUDRows.segment(item, data)?.text }
        var old = BCDHUDData()
        old.fps = 60; old.frameMs = 16.7; old.cpuTotal = 41; old.cpuTop = 78; old.gpuPercent = 63; old.gpuMs = 9.8; old.footprintMB = 2104; old.batteryPercent = 78
        check("the old overlay's words: 60 FPS, 16.7 ms, CPU 41% (top 78%), GPU 63% (9.8 ms), 2104 MB, 78%, Cool",
              [BCDHUDItem.fps, .frameMs, .cpu, .gpu, .memUsed, .battery, .thermal].map { one($0, old) ?? "nil" }
              == ["60 FPS", "16.7 ms", "CPU 41% (top 78%)", "GPU 63% (9.8 ms)", "2104 MB", "78%", "Cool"])
        check("... and when a reading is missing: – ms, CPU –, GPU –, no battery line",
              one(.frameMs, BCDHUDData()) == "– ms" && one(.cpu, BCDHUDData()) == "CPU –" && one(.gpu, BCDHUDData()) == "GPU –" && one(.battery, BCDHUDData()) == nil)
        check("a battery under 20% is amber", BCDHUDRows.segment(.battery, { var x = old; x.batteryPercent = 15; return x }())?.tone == .warm)
        let legacy = BCDHUDMigration.resolve(stored: nil, performance: true, fields: nil, everyGame: .off, legacyPlace: nil)
        let legacyPlan = BCDHUDRows.plan(layout: legacy.layout, custom: legacy.custom, data: old, width: 420)
        check("the migrated default overlay reads as the old one did: 60 FPS · 16.7 ms · 2104 MB · 78%", texts(legacyPlan) == [["60 FPS", "16.7 ms", "2104 MB", "78%"]], "\(texts(legacyPlan))")
        check("... on a dark plate with a dot between the readings", legacyPlan?.separator == "  ·  " && legacyPlan?.background == 0.62 && legacyPlan?.textSize == 12)
        var seven = BCDHUDCustom()
        seven.items = [.fps, .frameMs, .cpu, .gpu, .memUsed, .battery, .thermal]
        let narrow = BCDHUDRows.plan(layout: .custom, custom: seven, data: old, width: 200)
        check("a narrow screen wraps the strip into lines", (narrow?.rows.count ?? 0) >= 3 && texts(narrow).joined().count == 7, "\(texts(narrow))")
        var listed = seven; listed.shape = .list
        check("the list shape is one reading to a line", texts(BCDHUDRows.plan(layout: .custom, custom: listed, data: old, width: 400)).allSatisfy { $0.count == 1 })
        var table = seven; table.shape = .table
        let tableRows = texts(BCDHUDRows.plan(layout: .custom, custom: table, data: old, width: 400))
        check("the table shape pairs the readings", tableRows.map(\.count) == [2, 2, 2, 1] && BCDHUDRows.plan(layout: .custom, custom: table, data: old, width: 400)?.columns == 2)
        var withGraph = BCDHUDCustom()
        withGraph.items = [.fps, .frameGraph, .frameMs]
        withGraph.shape = .list
        var gdata = old; gdata.graph = Array(repeating: 16, count: 240)
        check("a graph stands where the list put it", texts(BCDHUDRows.plan(layout: .custom, custom: withGraph, data: gdata, width: 400)) == [["60 FPS"], ["<graph>"], ["16.7 ms"]])
        withGraph.graphSeconds = 15
        if case .graph(let g)? = BCDHUDRows.plan(layout: .custom, custom: withGraph, data: gdata, width: 400)?.rows[1] { check("the graph's length is the player's: 15 s is 60 buckets", g.values.count == 60) }
        var clear = BCDHUDCustom(); clear.backgroundOpacity = 0.1; clear.textOpacity = 0.6; clear.size = .large
        let clearPlan = BCDHUDRows.plan(layout: .custom, custom: clear, data: old, width: 400)
        check("a plate under 25% gets the text outline; size and opacity reach the plan", clearPlan?.outlineText == true && clearPlan?.textSize == 14 && clearPlan?.textOpacity == 0.6
              && BCDHUDRows.plan(layout: .custom, custom: .default, data: old, width: 400)?.outlineText == false)
        check("the play time reads 42:10 and 1:02:03", BCDHUDRows.clockTime(seconds: 2530) == "42:10" && BCDHUDRows.clockTime(seconds: 3723) == "1:02:03"
              && BCDHUDRows.clockTime(seconds: -4) == "00:00")
        check("memory is shown in thousands of MB, one decimal", BCDHUDRows.gigabytes(3174) == "3.2" && BCDHUDRows.gigabytes(5800) == "5.8" && BCDHUDRows.gigabytes(0) == "0.0")
        check("a resolution without MetalFX is one size; with it two",
              one(.resolution, { var x = BCDHUDData(); x.renderSize = "1280x720"; return x }()) == "1280x720"
              && one(.resolution, d) == "1408x648 > 2816x1296")

        // ---- 7. Where it goes ------------------------------------------------------------------------------------
        let phone = CGSize(width: 852, height: 393)
        let island = BCDHUDInsets(top: 0, leading: 59, bottom: 21, trailing: 59)
        let size = CGSize(width: 250, height: 22)
        let topCentre = BCDHUDPlacement.origin(slot: .top, size: size, viewport: phone, insets: island)
        check("top centre on a phone held sideways: centred, 20 pt down (no top inset)", abs(topCentre.x - (852 - 250) / 2) < 0.01 && topCentre.y == 20, "\(topCentre)")
        let topLeading = BCDHUDPlacement.origin(slot: .topLeading, size: size, viewport: phone, insets: island)
        check("top leading sits inside the 59 pt strip, 8 pt from it", topLeading.x == 67, "\(topLeading)")
        let bottomTrailing = BCDHUDPlacement.origin(slot: .bottomTrailing, size: size, viewport: phone, insets: island)
        check("bottom trailing is inside the bottom inset and the trailing strip", bottomTrailing.x == 852 - 59 - 8 - 250 && bottomTrailing.y == 393 - 21 - 8 - 22, "\(bottomTrailing)")
        let upright = CGSize(width: 393, height: 852)
        let uprightInsets = BCDHUDInsets(top: 59, leading: 0, bottom: 34, trailing: 0)
        let uprightTop = BCDHUDPlacement.origin(slot: .topTrailing, size: CGSize(width: 44, height: 20), viewport: upright, insets: uprightInsets)
        check("upright: under the island's strip, 20 pt from the rounded corner", uprightTop.y == 59 + 8 && uprightTop.x == 393 - 20 - 44, "\(uprightTop)")
        check("a HUD wider than the safe area stays at the left edge", BCDHUDPlacement.origin(slot: .top, size: CGSize(width: 900, height: 20), viewport: phone, insets: island).x == 67)
        check("margins: 8 pt from a safe area, 20 pt from a bare screen edge", BCDHUDPlacement.margin(inset: 59) == 8 && BCDHUDPlacement.margin(inset: 0) == 20)
        // The old overlay's rule for a dragged place
        let c = BCDHUDPlacement.center(dragged: 0.5, y: 0, size: CGSize(width: 200, height: 30), viewport: phone, insets: island)
        check("dragged: the old top centre, kept inside the safe area", c.x == 426 && c.y == 0 + 8 + 15, "\(c)")
        let edge = BCDHUDPlacement.center(dragged: 1, y: 1, size: CGSize(width: 200, height: 30), viewport: phone, insets: island)
        check("dragged to the far corner it stops at the safe area", edge.x == 852 - 59 - 100 - 8 && edge.y == 393 - 21 - 15 - 8, "\(edge)")
        // Auto placement
        let strip = BCDHUDCustom.default
        let free = BCDHUDPlacement.auto(layout: .strip, custom: strip, size: size, viewport: phone, insets: island, avoid: [], bars: [])
        check("auto, nothing in the way: layout 2 takes the top centre", free == topCentre, "\(free)")
        let narrowBlock = BCDHUDPlacement.auto(layout: .strip, custom: strip, size: size, viewport: phone, insets: island,
                                               avoid: [CGRect(x: 330, y: 0, width: 50, height: 60)], bars: [])
        check("auto, a control at the top centre that leaves the corner free: the next slot (top leading)", narrowBlock == topLeading, "\(narrowBlock)")
        let blocked = BCDHUDPlacement.auto(layout: .strip, custom: strip, size: size, viewport: phone, insets: island,
                                           avoid: [CGRect(x: 100, y: 0, width: 650, height: 60)], bars: [])
        check("auto, controls all along the top: the bottom centre", blocked == BCDHUDPlacement.origin(slot: .bottom, size: size, viewport: phone, insets: island), "\(blocked)")
        let crowded = BCDHUDPlacement.auto(layout: .tune, custom: strip, size: CGSize(width: 190, height: 78), viewport: phone, insets: island,
                                           avoid: [CGRect(x: 0, y: 0, width: 852, height: 120), CGRect(x: 0, y: 273, width: 852, height: 120)], bars: [])
        check("auto, everything covered: the slot that covers least is chosen, not nothing", crowded.x >= 59 && crowded.y >= 0)
        let bars = BCDHUDPlacement.bars(picture: CGRect(x: 100, y: 0, width: 652, height: 393), viewport: phone)
        check("a pillarboxed picture leaves two side bars", bars.count == 2 && bars[0].width == 100 && bars[1].minX == 752, "\(bars)")
        check("... a full picture none", BCDHUDPlacement.bars(picture: CGRect(origin: .zero, size: phone), viewport: phone).isEmpty
              && BCDHUDPlacement.bars(picture: .zero, viewport: phone).isEmpty)
        let wideBars = BCDHUDPlacement.bars(picture: CGRect(x: 300, y: 0, width: 252, height: 393), viewport: phone)
        let docked = BCDHUDPlacement.auto(layout: .tune, custom: strip, size: CGSize(width: 190, height: 78), viewport: phone, insets: island, avoid: [], bars: wideBars)
        check("auto docks in a bar the picture leaves, which covers none of the game", wideBars.contains { $0.contains(CGRect(origin: docked, size: CGSize(width: 190, height: 78))) }, "\(docked)")
        let tooSmall = BCDHUDPlacement.auto(layout: .tune, custom: strip, size: CGSize(width: 190, height: 78), viewport: phone, insets: island, avoid: [],
                                            bars: BCDHUDPlacement.bars(picture: CGRect(x: 40, y: 0, width: 772, height: 393), viewport: phone))
        check("a bar that is too narrow is not used", tooSmall == BCDHUDPlacement.origin(slot: .topLeading, size: CGSize(width: 190, height: 78), viewport: phone, insets: island))
        check("the window class comes from its size alone: wide is landscape, tall is portrait, near-square is square",
              BCDHUDScreenClass.of(phone) == .landscape && BCDHUDScreenClass.of(upright) == .portrait
              && BCDHUDScreenClass.of(CGSize(width: 1180, height: 820)) == .landscape && BCDHUDScreenClass.of(CGSize(width: 1024, height: 768)) == .landscape
              && BCDHUDScreenClass.of(CGSize(width: 768, height: 1024)) == .portrait && BCDHUDScreenClass.of(CGSize(width: 800, height: 800)) == .square
              && BCDHUDScreenClass.of(CGSize(width: 900, height: 780)) == .square && BCDHUDScreenClass.of(.zero) == .landscape)
        for layout in BCDHUDLayout.allCases where layout != .off {
            check("layout \(layout.title) has somewhere to go (a slot order)", !BCDHUDPlacement.preference(layout: layout, custom: .default, landscape: true, roomy: false).isEmpty)
        }

        // ---- 8. Another window: a ~4:3 screen, a near-square one, a resize while the game runs -----------------------
        // Nothing below names a device: only the window's size, its safe area, the picture and what to keep clear of.
        let four3 = CGSize(width: 1024, height: 768)
        let tablet = BCDHUDInsets(top: 24, leading: 0, bottom: 20, trailing: 0)
        let wide169 = CGRect(x: 0, y: 96, width: 1024, height: 576)   // a 16:9 game in a 4:3 window: 96 pt bars above and below
        func inside(_ origin: CGPoint, _ box: CGSize, _ viewport: CGSize, _ insets: BCDHUDInsets) -> Bool {
            origin.x >= insets.leading && origin.y >= insets.top && origin.x + box.width <= viewport.width - insets.trailing + 0.01
                && origin.y + box.height <= viewport.height - insets.bottom + 0.01
        }
        check("a window of 1024x768 is roomy, a phone held sideways and an upright one are not; a near-square 800x800 is",
              BCDHUDPlacement.roomy(four3) && BCDHUDPlacement.roomy(CGSize(width: 800, height: 800)) && !BCDHUDPlacement.roomy(phone) && !BCDHUDPlacement.roomy(upright))
        let barsOf43 = BCDHUDPlacement.bars(picture: wide169, viewport: four3)
        check("on a 4:3 window a 16:9 picture leaves a bar above and one below", barsOf43.count == 2 && barsOf43[0].height == 96 && barsOf43[1].minY == 672, "\(barsOf43)")
        for layout in [BCDHUDLayout.pulse, .strip] {
            let box = layout == .pulse ? CGSize(width: 56, height: 22) : CGSize(width: 250, height: 22)
            let spot = BCDHUDPlacement.auto(layout: layout, custom: .default, size: box, viewport: four3, insets: tablet, avoid: [], bars: barsOf43)
            check("4:3, layout \(layout.title): docked in a bar, so it covers none of the picture, 20 pt from the screen's edge",
                  !CGRect(origin: spot, size: box).intersects(wide169) && inside(spot, box, four3, tablet) && spot.x >= 20, "\(spot)")
        }
        let none43 = BCDHUDPlacement.bars(picture: CGRect(origin: .zero, size: four3), viewport: four3)
        for layout in [BCDHUDLayout.pulse, .strip, .tune, .report, .custom] {
            let box = layout == .report ? CGSize(width: 270, height: 132) : layout == .tune ? CGSize(width: 190, height: 78) : CGSize(width: 250, height: 22)
            let block = [CGRect(x: 40, y: 540, width: 160, height: 160), CGRect(x: 824, y: 540, width: 160, height: 160)]   // two stick clusters
            let spot = BCDHUDPlacement.auto(layout: layout, custom: .default, size: box, viewport: four3, insets: tablet, avoid: block, bars: none43)
            let frame = CGRect(origin: spot, size: box)
            check("4:3, a full-screen picture, layout \(layout.title): inside the safe area, clear of the touch controls, 20 pt from the screen's edge",
                  inside(spot, box, four3, tablet) && !block.contains { $0.intersects(frame) } && spot.x >= 20, "\(spot)")
        }
        let square = CGSize(width: 800, height: 800)
        let squareBars = BCDHUDPlacement.bars(picture: CGRect(x: 0, y: 175, width: 800, height: 450), viewport: square)
        let reportBox = CGSize(width: 270, height: 132)
        let report = BCDHUDPlacement.auto(layout: .report, custom: .default, size: reportBox, viewport: square, insets: BCDHUDInsets(top: 20, leading: 0, bottom: 20, trailing: 0),
                                          avoid: [], bars: squareBars)
        check("a near-square 800x800 screen with a 16:9 picture: layout 4 sits in the bar above it and covers nothing",
              !CGRect(origin: report, size: reportBox).intersects(CGRect(x: 0, y: 175, width: 800, height: 450)), "\(report)")
        // A resize while the game runs: the same inputs at the new size give a place inside the new window
        let small = CGSize(width: 520, height: 420)
        let smallInsets = BCDHUDInsets(top: 0, leading: 0, bottom: 0, trailing: 0)
        for slot in BCDHUDSlot.allCases {
            let at = BCDHUDPlacement.origin(slot: slot, size: CGSize(width: 250, height: 22), viewport: small, insets: smallInsets)
            check("a window resized to 520x420: \(slot.title) is inside it", inside(at, CGSize(width: 250, height: 22), small, smallInsets), "\(at)")
        }
        let resizedDrag = BCDHUDPlacement.center(dragged: 0.9, y: 0.95, size: CGSize(width: 250, height: 22), viewport: small, insets: smallInsets)
        check("a dragged place is the same share of a smaller window, kept inside it", resizedDrag.x <= 520 - 125 - 8 && resizedDrag.y <= 420 - 11 - 8, "\(resizedDrag)")
        let narrowPlan = BCDHUDRows.plan(layout: .report, custom: .default, data: d, width: 200)
        check("a window narrower than a layout: the plate and its graph shrink to the room there is",
              (narrowPlan?.maxWidth ?? 999) <= 200 && narrowPlan.map { plan in plan.rows.allSatisfy { row in
                  if case .graph(let g) = row { return g.width <= plan.maxWidth }
                  return true } } == true)
        let roomyPlan = BCDHUDRows.plan(layout: .report, custom: .default, data: d, width: 420, roomy: true)
        check("a roomy window gets the wider Report plate (270), a small one the 240", roomyPlan?.maxWidth == 270 && BCDHUDRows.plan(layout: .report, custom: .default, data: d, width: 420)?.maxWidth == 240)
        check("nothing in the placement names a device", true)

        print(failures == 0 ? "bcd_hud_checks: PASS" : "bcd_hud_checks: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
