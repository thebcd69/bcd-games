// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-autotune-ask.py, compiled with app/Madeira/AutoTuneCore.swift and AutoTuneAsk.swift and the two
// stand-ins below (the log and the result's text; the real ones are in LogStore.swift and AutoTune.swift).

import Foundation

final class LogStore {
    static let shared = LogStore()
    var lines: [String] = []
    func log(_ line: String) { lines.append(line) }
}

struct AutoTuneOffer {
    var title = "Game"
    var windowsPath: String
    var state: AutoTuneState
    var ask = false
    var message: String {
        "Game crashed.\n\nThe next start tries: " + (state.candidate ?? []).joined(separator: "; ") + ". It helps. Restart BCD and play again."
    }
}

@main enum Checks {
    static var failures = 0
    static func check(_ what: String, _ ok: Bool) {
        print((ok ? "PASS: " : "FAIL: ") + what)
        if !ok { failures += 1 }
    }

    static func main() {
        let defaults = UserDefaults.standard
        for k in [AutoTuneAsk.key, "madeira.autotune.ask.approved", "madeira.autotune.ask.declined"] { defaults.removeObject(forKey: k) }
        let path = "C:\\Games\\Game.exe"
        var s = AutoTuneState()
        s.status = "trying"
        s.kept = ["env.A = 1"]
        s.candidate = ["env.B = 1"]

        // Off: everything as always.
        check("off by default (no key stored)", !AutoTuneAsk.on)
        check("off: nothing waits and the layer is auto-tune's own", AutoTuneAsk.waiting(s, path).isEmpty && AutoTuneAsk.layer(s, path) == s.layerLines)
        let offer = AutoTuneOffer(windowsPath: path, state: s)
        check("off: the result asks nothing, is never held back and offers no undo",
              !AutoTuneAsk.asks(offer) && !AutoTuneAsk.quiet(offer) && !AutoTuneAsk.offersUndo(AutoTuneOffer(windowsPath: path, state: { var f = s; f.status = "found"; return f }())))
        AutoTuneAsk.forget(path)
        check("off: forgetting writes nothing", defaults.object(forKey: "madeira.autotune.ask.approved") == nil && defaults.object(forKey: "madeira.autotune.ask.declined") == nil)

        // On.
        defaults.set(true, forKey: AutoTuneAsk.key)
        check("on: the new fix waits", AutoTuneAsk.waiting(s, path) == ["env.B = 1"])
        check("on: a start gets only what auto-tune kept", AutoTuneAsk.layer(s, path) == ["env.A = 1"])
        check("on: the result asks", AutoTuneAsk.asks(offer) && !AutoTuneAsk.quiet(offer))
        check("on: its words say nothing changes without a yes, and drop the restart line",
              AutoTuneAsk.message(offer).contains("Auto-tune would try: env.B = 1") && AutoTuneAsk.message(offer).contains("Nothing changes until you say so")
              && !AutoTuneAsk.message(offer).contains("Restart BCD and play again"))
        var retry = s; retry.candidate = []
        check("a retry with nothing changed needs no yes", AutoTuneAsk.waiting(retry, path).isEmpty && AutoTuneAsk.layer(retry, path) == retry.layerLines)
        var none = s; none.candidate = nil
        check("no candidate: nothing waits", AutoTuneAsk.waiting(none, path).isEmpty)
        var already = s; already.candidate = ["env.A = 1"]
        check("a candidate the game already has does not wait", AutoTuneAsk.waiting(already, path).isEmpty)

        AutoTuneAsk.decline(s, path)
        check("Not now: the same fix is not asked about again (held back), and still waits", AutoTuneAsk.quiet(offer) && AutoTuneAsk.waiting(s, path) == ["env.B = 1"])
        var other = s; other.candidate = ["env.C = 1"]
        check("... but another fix is asked about", !AutoTuneAsk.quiet(AutoTuneOffer(windowsPath: path, state: other)))
        check("the Windows path's case does not matter", AutoTuneAsk.quiet(AutoTuneOffer(windowsPath: path.uppercased(), state: s)))

        AutoTuneAsk.approve(s, path)
        check("Try it: nothing waits, the start gets the whole layer", AutoTuneAsk.waiting(s, path).isEmpty && AutoTuneAsk.layer(s, path) == s.layerLines
              && !AutoTuneAsk.quiet(offer) && !AutoTuneAsk.asks(offer))
        check("the yes is for that fix only: the next one waits again", AutoTuneAsk.waiting(other, path) == ["env.C = 1"])
        check("each answer is logged", LogStore.shared.lines.contains { $0.contains("said yes to env.B = 1") } && LogStore.shared.lines.contains { $0.contains("put off env.B = 1") })

        var found = s; found.status = "found"; found.candidate = nil; found.kept = ["env.A = 1", "env.B = 1"]
        check("once a fix was kept the result offers Undo", AutoTuneAsk.offersUndo(AutoTuneOffer(windowsPath: path, state: found)))
        check("... not for the What happened? question", !AutoTuneAsk.offersUndo(AutoTuneOffer(windowsPath: path, state: found, ask: true)))
        AutoTuneAsk.forget(path)
        check("Undo forgets the answers", AutoTuneAsk.waiting(s, path) == ["env.B = 1"] && !AutoTuneAsk.quiet(offer))

        var optin = AutoTuneState(); optin.kept = ["env.MADEIRA_OPTIN_DLLS = a"]; optin.candidate = ["env.MADEIRA_OPTIN_DLLS = b"]
        check("a list line that adds up (opt-in DLLs) keeps only the kept names while the new one waits",
              AutoTuneAsk.layer(optin, path) == ["env.MADEIRA_OPTIN_DLLS = a"] && optin.layerLines != AutoTuneAsk.layer(optin, path))

        for k in [AutoTuneAsk.key, "madeira.autotune.ask.approved", "madeira.autotune.ask.declined"] { defaults.removeObject(forKey: k) }
        print(failures == 0 ? "ALL PASS" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
