// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The host check for the game setup sections' words, rows and tables (app/Madeira/BCDGameSetupPage.swift, BCDGameSetupTables.swift),
// compiled with the page's rules (BCDGamePage.swift up to its views), BCDStore.swift and BCDGameSetupRules.swift by
// tests/host/check-game-setup.py. Prints "ok" / "FAIL" lines.

import Foundation

var failures = 0
var checks = 0
func check(_ ok: Bool, _ what: String, _ detail: String = "") {
    checks += 1
    if !ok { failures += 1 }
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
}

// ------------------------------------------------------------------------------ DLL components
do {
    let ids = BCDDllComponents.all.map(\.id)
    check(Set(ids).count == ids.count, "component ids are unique")
    let all = BCDDllComponents.all.flatMap(\.names)
    check(Set(all).count == all.count, "no DLL name is in two components", "\(Dictionary(grouping: all, by: { $0 }).filter { $1.count > 1 }.keys)")
    check(all.allSatisfy { $0 == $0.lowercased() && BCDDllComponents.isDllName($0) }, "names are lower case DLL names")
    check(all.contains("d3dx9_43.dll") && all.contains("xinput1_3.dll") && all.contains("vcruntime140.dll") && all.contains("d3dcompiler_47.dll") && all.contains("xaudio2_7.dll"), "the common ones are there")
    check(!all.contains("kernel32.dll") && !all.contains("ntdll.dll") && !all.contains("ucrtbase.dll") && !all.contains("user32.dll"), "no system library is in a component")

    check(BCDDllComponents.names("A.dll; b.dll,a.dll  c.dll") == ["a.dll", "b.dll", "c.dll"], "list: separators, case, duplicates")
    let vc = BCDDllComponents.all.first { $0.id == "vcrt" }!
    check(BCDDllComponents.shadow(vc, wine: true, shipped: ["VCRUNTIME140.dll", "other.dll"], current: ["my.dll"]) == ["my.dll", "vcruntime140.dll"],
          "Wine's own adds the shipped names of the component, keeps the player's own")
    check(BCDDllComponents.shadow(vc, wine: false, shipped: [], current: ["my.dll", "vcruntime140.dll", "msvcp140.dll"]) == ["my.dll"], "as the game has it takes the component's names out")
    check(BCDDllComponents.isWines(vc, shadow: ["vcruntime140.dll"]) && !BCDDllComponents.isWines(vc, shadow: ["my.dll"]), "a component is Wine's when a name of it is listed")
    check(BCDDllComponents.shipped(files: ["Game.dll", "D3DX9_43.dll", "xinput1_3.dll", "d3dx9_43.DLL"]) == ["D3DX9_43.dll", "xinput1_3.dll"], "shipped: components' DLLs only, once each")

    check(BCDDllComponents.switchedOff("video64=;comdlg32=n,b;dsound,mfplat=") == ["video64", "dsound", "mfplat"], "switched off: entries with no mode")
    check(BCDDllComponents.settingOff("video64=;comdlg32=n,b", to: ["dsound.dll"]) == "comdlg32=n,b;dsound.dll=", "other entries stay, the off ones are replaced")
    check(BCDDllComponents.settingOff("video64=", to: []) == "", "nothing off, nothing left: the line goes")
    check(BCDDllComponents.settingOff(nil, to: ["a.dll", "a.dll", "b.dll"]) == "a.dll,b.dll=", "an off entry for several DLLs")
    check(BCDDllComponents.isDllName("d3d9.dll") && !BCDDllComponents.isDllName("d3d9") && !BCDDllComponents.isDllName("a b.dll") && !BCDDllComponents.isDllName("a;b.dll") && !BCDDllComponents.isDllName("é.dll"),
          "DLL names")
    check(BCDGameSetupPage.parseOff("dsound.dll; MFPLAT.dll bad name,x") == (["dsound.dll", "mfplat.dll"], ["bad", "name", "x"]), "the Switched off field", "\(BCDGameSetupPage.parseOff("dsound.dll; MFPLAT.dll bad name,x"))")

    // 2026-11-20: the game's own copy first (WINEDLLOVERRIDES n,b), for mod loaders
    check(BCDDllComponents.nativeFirst("video64=;comdlg32=n,b;dinput8,version=native,builtin;x=b,n;y= N , B ") == ["comdlg32", "dinput8", "version", "y"],
          "own copy first: n,b and native,builtin entries (spaces and case allowed), not b,n and not off", "\(BCDDllComponents.nativeFirst("video64=;comdlg32=n,b;dinput8,version=native,builtin;x=b,n;y= N , B "))")
    check(BCDDllComponents.switchedOff("video64=;dinput8=n,b") == ["video64"], "an n,b entry is not switched off")
    check(BCDDllComponents.settingNativeFirst("video64=;comdlg32=n,b;x=b", to: ["dinput8.dll", "dinput8.dll", "version.dll"]) == "video64=;x=b;dinput8.dll,version.dll=n,b",
          "own copy first: the other entries stay as written, the n,b ones are replaced by one entry", BCDDllComponents.settingNativeFirst("video64=;comdlg32=n,b;x=b", to: ["dinput8.dll", "dinput8.dll", "version.dll"]))
    check(BCDDllComponents.settingNativeFirst("dinput8=n,b", to: []) == "", "nothing left: the line goes")
    check(BCDDllComponents.settingOff("dinput8.dll=n,b", to: ["dsound.dll"]) == "dinput8.dll=n,b;dsound.dll=", "switching a DLL off keeps the n,b entry")
    check(BCDDllComponents.bare("DInput8.DLL") == "dinput8" && BCDDllComponents.bare("dinput8") == "dinput8", "dsound and dsound.dll are the same DLL")
}

// ------------------------------------------------------------------------------ launch chips
do {
    let quick = BCDLaunchFlags.quick
    check(Set(quick.map(\.value)).count == quick.count, "quick picks are unique")
    for flag in ["-force-gfx-direct", "-force-d3d11-singlethreaded", "-force-dx9", "-force-d3d9", "-force-d3d11", "/d3d9"] {
        check(quick.contains { $0.value == flag }, "the Winlator pick \(flag) is offered")
    }
    check(BCDLaunchFlags.flags.allSatisfy { f in quick.contains { $0.value == f } }, "every flag that has a switch is also a quick pick")
    check(quick.allSatisfy { !$0.note.isEmpty && !$0.value.contains(" ") }, "every pick has a note and is one word")
    check(BCDLaunchFlags.toggled("-force-d3d9", "-force-d3d11") == "-force-d3d11" && BCDLaunchFlags.toggled("-force-dx9 -windowed", "-force-d3d11") == "-windowed -force-d3d11",
          "one Unity Direct3D flag at a time")
    check(BCDLaunchFlags.toggled("-force-gfx-direct", "-force-d3d11") == "-force-gfx-direct -force-d3d11" && BCDLaunchFlags.toggled("-dx9", "-force-d3d9") == "-dx9 -force-d3d9",
          "the others are independent")
    check(BCDLaunchFlags.toggled("-force-d3d11-singlethreaded", "-force-d3d11") == "-force-d3d11-singlethreaded -force-d3d11", "a flag that starts with another's name is another flag")
    check(BCDLaunchFlags.has("-force-d3d11-singlethreaded", "-force-d3d11") == false, "and is not found as it")
    check(BCDLaunchFlags.toggled("/d3d9 -windowed", "/d3d9") == "-windowed", "a second tap takes /d3d9 out")
    check(BCDLaunchFlags.toggled("-dx11 -windowed", "-dx12") == "-windowed -dx12" && BCDLaunchFlags.toggled("-windowed", "-fullscreen") == "-fullscreen", "the old exclusions still hold")
    check(quick.contains { $0.value == "-vulkan" } && quick.contains { $0.value == "-force-vulkan" }, "the Vulkan picks are offered (2026-11-20)")
    check(BCDLaunchFlags.toggled("-dx11 -windowed", "-vulkan") == "-windowed -vulkan" && BCDLaunchFlags.toggled("-vulkan", "-dx12") == "-dx12",
          "-vulkan and a Direct3D flag take each other out")
    check(BCDLaunchFlags.toggled("-force-d3d11", "-force-vulkan") == "-force-vulkan", "Unity: one graphics API at a time, Vulkan included")
}

// ------------------------------------------------------------------------------ FEX presets
do {
    var v: [String: String] = [:]
    check(BCDFEXPresets.current(v) == "", "no lines: BCD's own choice")
    for preset in BCDFEXPresets.all {
        check(Set(preset.lines.keys) == Set(BCDFEXPresets.keys), "\(preset.id) sets exactly the five keys")
        v = preset.lines
        check(BCDFEXPresets.current(v) == preset.id, "\(preset.id): the lines read back as the preset")
        let applied = BCDFEXPresets.edits(for: preset.id)
        check(applied == preset.lines, "\(preset.id): the edits are its lines")
    }
    check(BCDFEXPresets.edits(for: "").values.allSatisfy { $0.isEmpty } && BCDFEXPresets.edits(for: "").count == 5, "BCD's own choice removes the five lines")
    v = ["env.FEX_VECTORTSOENABLED": "0"]
    check(BCDFEXPresets.current(v) == "custom", "one line of a list is a custom set (a game's tested list is not a preset)")
    let presets = BCDFEXPresets.all
    check(Set(presets.map { $0.lines }.map { "\($0.sorted { $0.key < $1.key })" }).count == presets.count, "the presets differ")
    check(presets.allSatisfy { $0.lines[BCDFEXPresets.tso] == "1" }, "no preset switches TSO off (the option that breaks multithreaded programs)")
}

// ------------------------------------------------------------------------------ Wine desktop
do {
    check(BCDWineDesktop.isDPI("144") && !BCDWineDesktop.isDPI("20") && !BCDWineDesktop.isDPI("abc") && !BCDWineDesktop.isDPI("9999"), "DPI values")
    check(BCDWineDesktop.dpiChoices.dropFirst().allSatisfy { BCDWineDesktop.isDPI($0.value) }, "every DPI choice is valid")
    check(BCDWineDesktop.darkColors.count >= 25 && Set(BCDWineDesktop.darkColors.map(\.name)).count == BCDWineDesktop.darkColors.count, "dark colours: each name once")
    check(BCDWineDesktop.darkColors.allSatisfy { $0.rgb.split(separator: " ").count == 3 && $0.rgb.split(separator: " ").allSatisfy { Int($0).map { (0...255).contains($0) } ?? false } }, "every colour is three numbers 0 to 255")
    let text = Dictionary(uniqueKeysWithValues: BCDWineDesktop.darkColors.map { ($0.name, $0.rgb) })
    func lum(_ rgb: String) -> Int { rgb.split(separator: " ").compactMap { Int($0) }.reduce(0, +) }
    check(lum(text["Window"]!) < 150 && lum(text["WindowText"]!) > 500 && lum(text["ButtonFace"]!) < 200 && lum(text["ButtonText"]!) > 500 && lum(text["MenuText"]!) > 500, "text is light on dark")
    check(BCDWineDesktop.isWallpaperPath("C:\\bcd-wallpaper.bmp") && BCDWineDesktop.isWallpaperPath("c:/users/x/p.BMP") && !BCDWineDesktop.isWallpaperPath("D:\\a.bmp")
          && !BCDWineDesktop.isWallpaperPath("C:\\a.png") && !BCDWineDesktop.isWallpaperPath("C:\\..\\a.bmp") && !BCDWineDesktop.isWallpaperPath("a.bmp"), "wallpaper paths")
}

// ------------------------------------------------------------------------------ the sections
func find(_ g: BCDGameSettingGroup, _ id: String) -> BCDGameSetting? { g.settings.first { $0.id == id } }
do {
    var s = BCDGameSetupState()
    var sections = BCDGameSetupPage.sections(s)
    check(sections.map(\.id) == ["windows", "components", "fex", "fixes"], "four sections for a game that starts on its own", "\(sections.map(\.id))")
    check(sections[1].parent == "windows" && sections[0].parent == nil && sections[2].parent == nil && sections[3].parent == nil, "Components is reached from Windows, the others are Options' own rows")
    check(sections.allSatisfy { $0.settings.allSatisfy { !$0.changed } } || sections[3].settings.allSatisfy { !$0.changed }, "nothing set: nothing marked changed")
    let windows = sections[0]
    check(windows.headline == "Windows 10" && find(windows, "setup.version")?.value == "" && find(windows, "setup.version")?.choices.first?.label.contains("default") == true, "Windows 10 is the default and says so")
    check(find(windows, "setup.version")!.choices.map(\.value).contains("winxp") && find(windows, "setup.version")!.choices.map(\.value).contains("win7"), "XP and 7 are offered")
    if case .opens(.section(let to)) = find(windows, "setup.components")!.kind { check(to == "components", "the Components row opens the Components section") } else { check(false, "the Components row opens a section") }
    s.windowsVersion = "win7"
    check(BCDGameSetupPage.sections(s)[0].headline == "Windows 7" && BCDGameSetupPage.sections(s)[0].settings[0].changed, "Windows 7: the headline and the dot")
    s.windowsVersion = "win98x"
    check(find(BCDGameSetupPage.sections(s)[0], "setup.version")!.choices.contains { $0.value == "win98x" }, "a version typed into the file by hand stays listed")

    // components
    s = BCDGameSetupState()
    s.is64Bit = true
    s.dll.shipped = ["d3dx9_43.dll", "VCRUNTIME140.dll"]
    s.dll.shadow = ["vcruntime140.dll"]
    var comp = BCDGameSetupPage.componentsSection(s)
    let vcrt = find(comp, "setup.dll.vcrt")!
    check(vcrt.value == "wine" && vcrt.locked == nil && vcrt.choices.map(\.value) == ["", "wine"] && vcrt.choices[0].label == "Game's own", "a 64-bit game's component is Wine's when listed")
    check(find(comp, "setup.dll.d3dx")!.value == "" && find(comp, "setup.dll.d3dx")!.note?.contains("d3dx9_43.dll") == true, "and says what the game ships")
    check(find(comp, "setup.dll.xinput")!.note?.contains("none of these") == true, "or that it ships none")
    check(comp.headline == "1 set to Wine's own", "headline", comp.headline ?? "")
    s.is64Bit = false
    comp = BCDGameSetupPage.componentsSection(s)
    check(find(comp, "setup.dll.d3dx") == nil && find(comp, "setup.dll-32bit")?.kind == .info && find(comp, "setup.dll-32bit")?.note?.contains("d3dx9_43.dll") == true
          && find(comp, "setup.dll-off") != nil, "a 32-bit game has one line saying why there is no per-component choice (and what it ships), not a locked row each")
    s.dll.off = ["dsound.dll"]
    s.dll.runtimes = ["OpenAL", "XNA Framework 4.0"]
    comp = BCDGameSetupPage.componentsSection(s)
    check(find(comp, "setup.dll-off")!.value == "dsound.dll" && comp.headline == "1 set to Wine's own · 1 switched off", "the off list", comp.headline ?? "")
    check(find(comp, "setup.dll-native-first")?.value == "" && find(comp, "setup.dll-native-first")?.heading == "Mod loaders", "the own-copy-first field, empty")
    s.dll.nativeFirst = ["dinput8.dll", "version.dll"]
    comp = BCDGameSetupPage.componentsSection(s)
    check(find(comp, "setup.dll-native-first")!.value == "dinput8.dll;version.dll" && comp.headline == "1 set to Wine's own · 1 switched off · 2 game's own first",
          "the own-copy-first list and the headline", comp.headline ?? "")
    s.dll.nativeFirst = []
    check(find(comp, "setup.runtimes")!.value == "OpenAL, XNA Framework 4.0" && find(comp, "setup.runtimes")!.kind == .info, "installed runtimes")
    s.dll.runtimes = nil
    check(find(BCDGameSetupPage.componentsSection(s), "setup.runtimes")!.value == "None installed yet", "runtimes: the installer never ran")

    // fex
    s = BCDGameSetupState()
    var fex = BCDGameSetupPage.fexSection(s)
    check(fex.headline == "BCD's own choice" && find(fex, "setup.fex")!.choices.map(\.value) == ["", "stability", "compatibility", "performance"], "FEX: BCD's own choice, three presets")
    s.fexPreset = "performance"; s.smcFull = true
    fex = BCDGameSetupPage.fexSection(s)
    check(fex.headline == "Performance · self-modifying code" && find(fex, "setup.fex-smc")!.isOn, "a preset and the SMC switch in the headline")
    s.fexPreset = "custom"
    fex = BCDGameSetupPage.fexSection(s)
    check(find(fex, "setup.fex")!.choices.map(\.value).last == "custom", "custom is shown when the file sets its own lines")

    // fixes
    s = BCDGameSetupState()
    var fixes = BCDGameSetupPage.fixesSection(s)
    check(fixes.headline == "None" && find(fixes, "setup.fix-none") != nil && find(fixes, "setup.fix-add") != nil, "no fixes: None, and a way to add one")
    s.fixes = [BCDSetupFixItem(number: 1, enabled: true, summary: "In foo.ini, [Display] FullScreen = 1", problem: nil),
               BCDSetupFixItem(number: 3, enabled: false, summary: "Move Cache aside", problem: nil),
               BCDSetupFixItem(number: 4, enabled: true, summary: "Write x.dll", problem: "BCD does not write that place or a program")]
    s.fixProblems = ["fix.9: \"wibble\" is not a fix this build knows"]
    fixes = BCDGameSetupPage.fixesSection(s)
    check(fixes.headline == "2 of 3 on", "headline", fixes.headline ?? "")
    let f1 = find(fixes, "setup.fix.1")!, f3 = find(fixes, "setup.fix.3")!, f4 = find(fixes, "setup.fix.4")!
    check(f1.value == "on" && f3.value == "off" && f1.choices.map(\.value) == ["on", "off", "remove"], "each fix: on, off or remove")
    check(f1.choices[2].question?.destructive == true && f1.choices[0].question == nil && f1.choices[1].question == nil, "only removing asks")
    check(f4.note?.hasPrefix("Cannot run:") == true, "a fix that cannot run says why", f4.note ?? "")
    check(BCDGameSetupPage.fixNumber("setup.fix.12") == 12 && BCDGameSetupPage.fixNumber("setup.fix-add") == nil && BCDGameSetupPage.fixNumber("setup.fix-problem.0") == nil && BCDGameSetupPage.fixNumber("setup.dll.vcrt") == nil,
          "row ids and fix numbers")
    check(fixes.settings.contains { $0.id == "setup.fix-problem.0" && $0.kind == .info }, "a line BCD cannot read is listed")
    if case .text = find(fixes, "setup.fix-add")!.kind { check(true, "the add row is a text row") } else { check(false, "the add row is a text row") }
    check(BCDGameSetupPage.addQuestionNote.contains("never changes a program"), "the help says no program is changed")

    // saves outside the prefix
    s = BCDGameSetupState()
    check(BCDGameSetupPage.savesSection(s) == nil && !BCDGameSetupPage.sections(s).contains { $0.id == "saveplace" }, "no known save folder, no section")
    s.saves = [BCDSetupSaveItem(path: "users/u/Documents/My Games/Foo", label: "Documents › My Games › Foo", place: .prefix),
               BCDSetupSaveItem(path: "users/u/AppData/Roaming/Vendor/Foo", label: "AppData › Roaming › Vendor › Foo", place: .files),
               BCDSetupSaveItem(path: "users/u/AppData/Local/Foo", label: "AppData › Local › Foo", place: .needsLink),
               BCDSetupSaveItem(path: "users/u/Saved Games/Foo", label: "Saved Games › Foo", place: .missing)]
    let places = BCDGameSetupPage.savesSection(s)!
    check(BCDGameSetupPage.sections(s).map(\.id) == ["windows", "components", "fex", "saveplace", "fixes"], "the section follows CPU translation")
    check(places.headline == "2 of 4 outside the prefix", "headline", places.headline ?? "")
    let doc = places.settings[0], roam = places.settings[1], loc = places.settings[2], missing = places.settings[3]
    check(doc.value == "prefix" && roam.value == "files" && loc.value == "files" && doc.choices.map(\.value) == ["prefix", "files"] && doc.choices.map(\.label) == ["Prefix", "Files"], "each folder: in the prefix or in Files")
    check(doc.choices[1].question?.confirm == "Move out" && doc.choices[0].question == nil && roam.choices[0].question?.confirm == "Move back" && roam.choices[1].question == nil,
          "moving out asks, moving back asks, staying asks nothing")
    check(doc.choices[1].question?.message.contains("Nothing is deleted") == true && roam.choices[0].question?.message.contains("nothing is moved") == true, "the questions say nothing is deleted, and what blocks the way back")
    check(loc.note?.contains("lost its link") == true && missing.kind == .info && missing.value == "Not in the prefix yet", "a lost link says it will be made again; a missing folder is a fact")
    check(!places.settings.contains { $0.title.contains("users") || $0.title.contains("/u/") }, "no Windows user or raw path in a label")
    s.savesBlocked = "Quit the game first."
    check(BCDGameSetupPage.savesSection(s)!.settings[0].note == "Quit the game first.", "a running game blocks with its reason")
    check(BCDGameSetupPage.exeIconRow([:]).kind == .action && BCDGameSetupPage.exeIconRow(["setup.exeicon": "Done."]).note == "Done.", "the icon row is an action that reports what it did")

    // desktop
    s = BCDGameSetupState()
    check(BCDGameSetupPage.desktopSection(s) == nil, "no desktop section for a game that does not start in one")
    s.desktop = BCDSetupDesktopState(dpi: "144", theme: "dark", wallpaper: "C:\\w.bmp")
    let desk = BCDGameSetupPage.desktopSection(s)!
    check(desk.headline == "144 dpi · dark · wallpaper" && find(desk, "setup.desktop-dpi")!.value == "144" && find(desk, "setup.desktop-theme")!.value == "dark", "desktop headline and rows")
    s.desktop = BCDSetupDesktopState(dpi: "130")
    check(find(BCDGameSetupPage.desktopSection(s)!, "setup.desktop-dpi")!.choices.contains { $0.value == "130" }, "a DPI typed by hand stays listed")
    check(BCDGameSetupPage.sections(s).map(\.id).last == "desktop", "the desktop section follows the others")

    // words
    let words = (BCDGameSetupPage.sections(BCDGameSetupState()) + [BCDGameSetupPage.desktopSection({ var d = BCDGameSetupState(); d.desktop = BCDSetupDesktopState(); return d }())!])
        .flatMap { g in [g.title, g.note ?? ""] + g.settings.flatMap { [$0.title, $0.note ?? ""] } }
    check(words.allSatisfy { !$0.lowercased().contains("madeira") }, "no word of the old name is shown")
}

// ------------------------------------------------------------------------------ how a game starts
do {
    // the plan
    let none = BCDStartPlan.read(["unrelated": "x"])
    check(none.isEmpty && !none.needsSupervisor && none.problems.isEmpty && none.stopSecond, "a file with no start line asks for nothing")
    let plan = BCDStartPlan.read(["start.delay": "5", "start.second": "C:\\Tools\\mapper.exe", "start.second-args": "/min", "window.2": "opaque title Launcher 250", "window.1": "raise title \"Final Fantasy IX\" 1000"])
    check(plan.delay == 5 && plan.second == "C:\\Tools\\mapper.exe" && plan.secondArguments == "/min" && plan.stopSecond && plan.needsSupervisor && !plan.isEmpty, "delay, second program, arguments, stop on by default")
    check(plan.rules.map(\.number) == [1, 2] && plan.rules[0].text == "Final Fantasy IX" && plan.rules[0].delayMS == 1000 && plan.rules[1].action == .opaque, "rules in number order")
    check(!BCDStartPlan.read(["start.second": "C:\\Tools\\mapper.exe", "start.second-stop": "0"]).needsSupervisor && BCDStartPlan.read(["window.1": "raise title x"]).needsSupervisor, "a second program that is left running needs no supervisor; rules do")
    check(BCDStartPlan.read(["start.delay": "301"]).problems.count == 1 && BCDStartPlan.read(["start.delay": "x"]).delay == 0, "a delay over 300 or not a number is a problem")
    for bad in ["C:\\Tools\\readme.txt", "..\\x.exe", "C:\\a\\..\\b.exe", "tools\\x.exe", "D:\\x.exe", "C:\\EasyAntiCheat\\EasyAntiCheat_Setup.exe", "BEService.exe", "C:\\x\"y.exe", ""] {
        check(!BCDStartPlan.isProgram(bad), "not a second program: \(bad)")
    }
    for good in ["C:\\Tools\\mapper.exe", "notepad.exe", "c:\\Program Files\\JoyToKey\\JoyToKey.exe", "C:\\Tools\\startup.cmd", "C:\\Tools\\helper.bat"] { check(BCDStartPlan.isProgram(good), "a second program: \(good)") }
    check(BCDStartPlan.read(["start.second": "C:\\x\\EasyAntiCheat.exe"]).second == "" && BCDStartPlan.read(["start.second": "C:\\x\\EasyAntiCheat.exe"]).problems.count == 1, "a protection program is no second program")

    // rules
    check(BCDWindowRule.parse("raise title \"Final Fantasy IX\" 1000", number: 1).rule?.line == "raise title \"Final Fantasy IX\" 1000", "a rule's line reads back")
    check(BCDWindowRule.parse("minimize-restore class UnityWndClass", number: 2).rule?.delayMS == 0, "the delay is optional")
    for bad in ["raise title", "close title x", "raise name x", "raise title x -1", "raise title x 60001", "raise title x notanumber", "raise title \"a|b\"", "raise title \"Easy Anti-Cheat Launch Error\" 1000",
                "opaque class BattlEye", "raise title VANGUARD 0", "raise title \"\" 0"] {
        check(BCDWindowRule.parse(bad, number: 1).rule == nil, "refused rule: \(bad)")
    }
    var many: [String: String] = [:]
    for i in 1...20 { many["window.\(i)"] = "raise title x\(i) 0" }
    check(BCDStartPlan.read(many).rules.count == 16 && BCDStartPlan.read(many).problems.count == 1, "at most 16 rules")
    check(BCDStartPlan.nextRuleNumber(["window.1": "x", "window.3": "x"]) == 2 && BCDStartPlan.nextRuleNumber([:]) == 1, "next free rule number")

    // swap
    check(BCDStartPlan.swapProblem("launcher.exe=game.exe") == nil && BCDStartPlan.swapProblem("a.exe=b.exe;c d.exe=e.exe") == nil, "swaps of program names")
    for bad in ["launcher.exe", "launcher.exe=C:\\x\\game.exe", "launcher.dll=game.exe", "a.exe=b.exe;", "../x.exe=y.exe", "x.exe=y.exe;z", "EasyAntiCheat_Launcher.exe=Game.exe", "Game.exe=BEClient_x64.exe"] {
        check(BCDStartPlan.swapProblem(bad) != nil, "refused swap: \(bad)")
    }

    // the script
    var p = BCDStartPlan(); p.delay = 5; p.second = "C:\\Tools\\mapper.exe"; p.secondArguments = "/min 100%"; p.rules = plan.rules
    let script = BCDStartScript.batch(title: "Foo 100%", plan: p, main: .program(path: "C:\\Games\\Foo\\foo.exe", arguments: "-windowed 50%", isBatch: false), workdir: "C:\\Games\\Foo",
                                      configPath: "C:\\bcd-games\\ID-start.txt", secondExists: true, helperPresent: true)
    let expected = ["@echo off", "rem Generated by BCD (round 604) for Foo 100%%; rewritten at every launch.", "cd /d \"C:\\Games\\Foo\"",
                    "start \"\" \"C:\\Tools\\mapper.exe\" /min 100%%", "start \"\" \"C:\\bcd-games\\bcd-helper-x64.exe\" supervise \"C:\\bcd-games\\ID-start.txt\"",
                    "\"C:\\bcd-games\\bcd-helper-x64.exe\" wait 5", "start \"\" \"C:\\Games\\Foo\\foo.exe\" -windowed 50%%"].joined(separator: "\r\n") + "\r\n"
    check(script == expected, "the start script: second program, supervisor, delay, then the game; '%' doubled", script.debugDescription)
    let svc = BCDStartScript.batch(title: "Foo", plan: p, main: .servicesBatch(path: "C:\\bcd-games\\ID.bat"), workdir: "C:\\Games\\Foo", configPath: "C:\\c.txt", secondExists: false, helperPresent: false)
    check(svc == ["@echo off", "rem Generated by BCD (round 604) for Foo; rewritten at every launch.", "cd /d \"C:\\Games\\Foo\"", "call \"C:\\bcd-games\\ID.bat\""].joined(separator: "\r\n") + "\r\n",
          "with the services batch as the game's start, and no helper and no second program: only the call", svc.debugDescription)
    let bat = BCDStartScript.batch(title: "Foo", plan: BCDStartPlan(), main: .program(path: "C:\\Games\\run 100%.bat", arguments: "x%", isBatch: true), workdir: "C:\\Games", configPath: "", secondExists: false, helperPresent: true)
    check(bat.hasSuffix("call \"C:\\Games\\run 100%%%%.bat\" x%%%%\r\n"), "a .bat game is called, '%' doubled twice (CALL expands it again)", bat.debugDescription)
    check(!script.contains("\n\n") && script.components(separatedBy: "\r\n").allSatisfy { !$0.contains("\n") && !$0.contains("\r") }, "every line of the script is one line")
    var evil = BCDStartPlan(); evil.second = "C:\\a\\b.exe"; evil.secondArguments = "x\ny"
    check(!BCDStartScript.batch(title: "t\nrm", plan: evil, main: .program(path: "C:\\g.exe", arguments: "a\nb", isBatch: false), workdir: "C:\\", configPath: "", secondExists: true, helperPresent: false).contains("\nb\r\n"),
          "a line break in a title or arguments never starts another command")
    // the supervisor's file, for the C reader (check-bcd-helper.py)
    let file = BCDStartScript.supervisorFile(plan: p, game: "C:\\Games\\Foo", program: "C:\\Games\\Foo\\bin\\Foo.EXE", log: "C:\\bcd-games\\ID-start.log")
    if CommandLine.arguments.count > 1 { try? file.write(toFile: CommandLine.arguments[1], atomically: true, encoding: .utf8) }
    check(file.contains("program=Foo.EXE\n") && file.contains("second=mapper.exe\n") && file.contains("rule=raise|title|Final Fantasy IX|1000\n") && file.contains("wait=5\n") && file.contains("stop=1\n") && file.contains("log=C:\\bcd-games\\ID-start.log\n"), "the supervisor's file", file)

    // the section
    var st = BCDGameStartState()
    var g = BCDGameStartPage.group(st)
    check(g.headline == "As BCD starts it" && g.settings.map(\.id) == ["setup.start-delay", "setup.start-second", "setup.start-rule-add", "setup.start-swap"] && g.settings.allSatisfy { !$0.changed || $0.kind == .info }, "nothing set: four rows, none marked")
    st.plan = plan; st.swap = "launcher.exe=game.exe"
    g = BCDGameStartPage.group(st)
    check(g.headline == "wait 5 s · second program · 2 window rules · swap", "headline", g.headline ?? "")
    check(g.settings.map(\.id).contains("setup.start-second-args") && g.settings.map(\.id).contains("setup.start-second-stop") && g.settings.contains { $0.id == "setup.start-rule.1" && $0.choices.last?.question?.destructive == true },
          "a second program shows its arguments and its stop switch; a rule asks before it is removed")
    st.ownStart = false
    g = BCDGameStartPage.group(st)
    check(g.settings.map(\.id) == ["setup.start-swap"], "a game BCD Dock starts through Steam has only the swap row")
    check(BCDGameStartPage.ruleNumber("setup.start-rule.12") == 12 && BCDGameStartPage.ruleNumber("setup.start-rule-add") == nil, "rule ids")
    check(BCDProtection.names("Easy Anti-Cheat Launch Error") && BCDProtection.names("BEClient_x64.exe") && !BCDProtection.names("Foo Game"), "protection names")
    print("PROTECTION " + BCDProtection.list.joined(separator: "|"))
}

print("\(checks) checks, \(failures) failed")
exit(failures == 0 ? 0 : 1)
