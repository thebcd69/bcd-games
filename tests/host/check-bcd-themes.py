#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 creeper4004
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Exercise the real palette and profile store on the host without the game engine."""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIVER = r'''
import Foundation
import Observation
func require(_ value: @autoclosure () -> Bool, _ message: String) { if !value() { fatalError(message) } }
let data = try Data(contentsOf: URL(fileURLWithPath: "app/Madeira/BCDThemes.json"))
let presets = try JSONDecoder().decode([BCDThemeProfile].self, from: data)
for p in presets { let decoded = try BCDThemeProfile.decode(p.encoded()); require(decoded == p, "round trip \(p.name)") }
let suite = "bcd-theme-test-" + UUID().uuidString
let defaults = UserDefaults(suiteName: suite)!
defer { defaults.removePersistentDomain(forName: suite) }
let store = BCDThemeStore(defaults: defaults, presets: presets)
require(store.current == .original, "existing look on first start")
var updates = 0
withObservationTracking { _ = store.current.rgb("accent").hex } onChange: { updates += 1 }
store.select("ocean")
require(updates == 1, "nested palette reads notify SwiftUI")
require(BCDThemeStore(defaults: defaults, presets: presets).current.id == "ocean", "selection persisted")
for r in [0.0, 0.25, 0.5, 0.75, 1.0] {
 for g in [0.0, 0.25, 0.5, 0.75, 1.0] {
  for b in [0.0, 0.25, 0.5, 0.75, 1.0] {
   _ = try BCDThemeProfile.generated(main: BCDThemeRGB(red:r,green:g,blue:b)).validated()
  }
 }
}
store.setMainColor(BCDThemeRGB(hex: "#FFFF00")!)
RunLoop.current.run(until: Date().addingTimeInterval(0.3))
require(BCDThemeStore(defaults: defaults, presets: presets).current.seed == "#FFFF00", "custom draft persisted")
try store.saveAs("My yellow")
let savedID = store.current.id
store.select("forest"); store.select(savedID)
require(store.current.name == "My yellow", "saved custom profile selectable")
let temp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString+".json")
defer { try? FileManager.default.removeItem(at: temp) }
try presets[0].encoded().write(to: temp)
try store.importProfile(temp)
require(store.current.id != presets[0].id && store.current.name == presets[0].name, "import does not overwrite bundled ID")
store.deleteCurrent()
require(store.current == .original && store.saved.count == 1, "delete preserves other profiles")
let old = store.current
for invalid in [Data("{}".utf8), Data(repeating: 32,count:65_537), Data("not json".utf8)] {
 do { _ = try BCDThemeProfile.decode(invalid); fatalError("bad input accepted") } catch {}
}
for field in ["accent", "surface", "text"] {
 var p = presets[0]; p.colors[field] = "#GG0000"
 do { _ = try p.validated(); fatalError("bad hex accepted") } catch {}
}
var unsupported = old; unsupported.version = 2
do { _ = try unsupported.validated(); fatalError("future format accepted") } catch {}
var invisible = old; invisible.colors["text"] = invisible.colors["surface"]
do { _ = try invisible.validated(); fatalError("invisible text accepted") } catch {}
try Data("{}".utf8).write(to:temp)
do { try store.importProfile(temp); fatalError("invalid import accepted") } catch {}
require(store.current == old, "failed import leaves current palette intact")
defaults.set(Data("corrupted".utf8),forKey:"bcd.appearance.v1")
require(BCDThemeStore(defaults:defaults,presets:presets).current == .original, "corrupt preferences recover")
print("PASS: presets, 125 generated palettes, observation, persistence, import isolation and invalid input")
'''
with tempfile.TemporaryDirectory(prefix="bcd-themes-") as directory:
    path = Path(directory)
    (path / "main.swift").write_text(DRIVER)
    subprocess.run(["xcrun", "swiftc", str(ROOT / "app/Madeira/BCDThemeCore.swift"),
                    str(ROOT / "app/Madeira/BCDTheme.swift"), str(path / "main.swift"),
                    "-o", str(path / "check")], check=True, cwd=ROOT)
    subprocess.run([str(path / "check")], check=True, cwd=ROOT)
