// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315:
// app/Tests/Licence/main.swift) under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

// The Swift half of tests/host/check-lsfg.py: app/Madeira/BCDLossless.swift (the licence receipt, the sealed file, reading the frame
// generation's shaders out of a Lossless.dll, the control file, the menu's rows) compiled as it is with small stand-ins for the menu's
// row types, and run on the host. No file of Lossless Scaling is here or needed: the DLL in this test is made up below.
import Foundation
import CryptoKit

var failures = 0
func require(_ ok: @autoclosure () -> Bool, _ what: String) {
    if ok() { print("PASS: " + what) } else { print("FAIL: " + what); failures += 1 }
}

// ---- the receipt ---------------------------------------------------------------------------------------------------------------
let key = SymmetricKey(size: .bits256)
let other = SymmetricKey(size: .bits256)
let t0 = Date(timeIntervalSince1970: 1_800_000_000)
let day: TimeInterval = 86_400
let me = BCDLosslessLicence.account(name: "SomeAccount")
let someoneElse = BCDLosslessLicence.account(name: "another")

require(me == BCDLosslessLicence.account(name: "someaccount") && me.count == 64 && !me.contains("some"), "the account is a SHA-256 of its name, whatever the case; the name is not in it")
let r = BCDLosslessLicence.issue(account: me, now: t0, key: key)
require(BCDLosslessLicence.status(of: r, now: t0, signedInAccount: me, key: key) == .valid(daysLeft: 7), "a fresh receipt is valid for the whole week")
require(BCDLosslessLicence.status(of: r, now: t0.addingTimeInterval(day + 60), signedInAccount: me, key: key) == .valid(daysLeft: 6), "... for 6 more days after a day")
require({ if case .valid(let d) = BCDLosslessLicence.status(of: r, now: t0.addingTimeInterval(6.5 * day), signedInAccount: me, key: key) { return d <= 1 } else { return false } }(),
        "... and on its last day")
require(BCDLosslessLicence.status(of: r, now: t0.addingTimeInterval(7 * day + 60), signedInAccount: me, key: key) == .expired, "it runs out after the week")
require(BCDLosslessLicence.status(of: r, now: t0, signedInAccount: someoneElse, key: key) == .otherAccount, "a receipt for another account does not count for this one")
require(BCDLosslessLicence.status(of: r, now: t0, signedInAccount: nil, key: key) == .otherAccount, "nobody signed in: it does not count")

var edited = r; edited.verifiedAt = t0.addingTimeInterval(5 * day)
require(BCDLosslessLicence.status(of: edited, now: t0.addingTimeInterval(5 * day), signedInAccount: me, key: key) == .tampered, "a changed date does not verify")
edited = r; edited.account = someoneElse
require(BCDLosslessLicence.status(of: edited, now: t0, signedInAccount: someoneElse, key: key) == .tampered, "a changed account does not verify")
require(BCDLosslessLicence.status(of: r, now: t0, signedInAccount: me, key: other) == .tampered, "a receipt made with another device's key does not verify")
var wrongApp = r; wrongApp.appID = 440
require(BCDLosslessLicence.status(of: wrongApp, now: t0, signedInAccount: me, key: key) == .tampered, "a receipt for another app does not verify")
require(BCDLosslessLicence.appID == 993090 && BCDLosslessLicence.graceDays == 7, "the app is Lossless Scaling (993090) and the week is seven days")

let used = BCDLosslessLicence.seen(r, now: t0.addingTimeInterval(5 * day), key: key)
require(used.lastSeen == t0.addingTimeInterval(5 * day)
        && BCDLosslessLicence.status(of: used, now: t0.addingTimeInterval(5 * day), signedInAccount: me, key: key) == .valid(daysLeft: 2), "using it moves lastSeen forward and stays valid")
require(BCDLosslessLicence.status(of: used, now: t0.addingTimeInterval(2 * day), signedInAccount: me, key: key) == .tampered, "a clock set back by days after a use is noticed")
require(BCDLosslessLicence.status(of: used, now: t0.addingTimeInterval(5 * day - 3600), signedInAccount: me, key: key) == .valid(daysLeft: 3), "a small step back (time zone, correction) is not")
require(BCDLosslessLicence.seen(used, now: t0, key: key).lastSeen == used.lastSeen, "seen never moves lastSeen back")
var future = BCDLosslessLicence.issue(account: me, now: t0.addingTimeInterval(30 * day), key: key)
require(BCDLosslessLicence.status(of: future, now: t0, signedInAccount: me, key: key) == .tampered, "a licence 'verified' in the future is refused")
require({ let d = try! JSONEncoder().encode(r); let back = try! JSONDecoder().decode(BCDLosslessLicence.Receipt.self, from: d)
          return back == r && BCDLosslessLicence.status(of: back, now: t0, signedInAccount: me, key: key) == .valid(daysLeft: 7) }(), "a receipt survives being saved and read")

// ---- the sealed file -------------------------------------------------------------------------------------------------------------
let plain = Data((0..<4096).map { UInt8($0 & 0xff) })
let secret = SymmetricKey(size: .bits256)
let sealed = try! BCDLosslessLicence.seal(plain, deviceSecret: secret, account: me)
require(sealed.count > plain.count && sealed.range(of: plain.prefix(64)) == nil, "the sealed file does not contain the file")
require((try? BCDLosslessLicence.open(sealed, deviceSecret: secret, account: me)) == plain, "it opens for the same device and account")
require((try? BCDLosslessLicence.open(sealed, deviceSecret: secret, account: someoneElse)) == nil, "it does not open for another account")
require((try? BCDLosslessLicence.open(sealed, deviceSecret: SymmetricKey(size: .bits256), account: me)) == nil, "it does not open on another device")
var broken = sealed; broken[broken.count / 2] ^= 0x01
require((try? BCDLosslessLicence.open(broken, deviceSecret: secret, account: me)) == nil, "a changed byte is noticed")
require((try! BCDLosslessLicence.seal(plain, deviceSecret: secret, account: me)) != sealed, "sealing twice gives different bytes (a new nonce each time)")
require(BCDLosslessLicence.sha256(Data("abc".utf8)) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "sha256 is the usual one")

// ---- the shaders in a DLL (a made-up one: a PE file whose .rsrc section holds RCDATA resources) ----------------------------------
/// A PE file with one section, .rsrc, holding the given RCDATA resources (and one of another type), laid out as a linker does.
func makeDLL(_ resources: [Int: Data], sectionVA: Int = 0x3000) -> Data {
    var rsrc = Data()
    func u16(_ v: Int) -> Data { var le = UInt16(v).littleEndian; return withUnsafeBytes(of: &le) { Data($0) } }
    func u32(_ v: Int) -> Data { var le = UInt32(truncatingIfNeeded: v).littleEndian; return withUnsafeBytes(of: &le) { Data($0) } }
    func dirHeader(ids: Int) -> Data { u32(0) + u32(0) + u16(0) + u16(0) + u16(0) + u16(ids) }
    let ids = resources.keys.sorted()
    // root: two types (3 = an icon, to be passed over; 10 = RCDATA)
    let rootSize = 16 + 2 * 8
    let iconDirAt = rootSize, iconDirSize = 16 + 8                    // one icon, id 1
    let iconLangAt = iconDirAt + iconDirSize, iconLangSize = 16 + 8
    let typeDirAt = iconLangAt + iconLangSize, typeDirSize = 16 + ids.count * 8
    let langDirsAt = typeDirAt + typeDirSize, langDirSize = 16 + 8
    let dataEntriesAt = langDirsAt + ids.count * langDirSize
    let iconEntryAt = dataEntriesAt + ids.count * 16
    var blobAt = iconEntryAt + 16 + 64                                  // the icon's 64 bytes sit before the blobs
    rsrc += dirHeader(ids: 2) + u32(3) + u32(0x8000_0000 | iconDirAt) + u32(10) + u32(0x8000_0000 | typeDirAt)
    rsrc += dirHeader(ids: 1) + u32(1) + u32(0x8000_0000 | iconLangAt)
    rsrc += dirHeader(ids: 1) + u32(1033) + u32(iconEntryAt)
    rsrc += dirHeader(ids: ids.count)
    for (i, id) in ids.enumerated() { rsrc += u32(id) + u32(0x8000_0000 | (langDirsAt + i * langDirSize)) }
    for i in ids.indices { rsrc += dirHeader(ids: 1) + u32(1033) + u32(dataEntriesAt + i * 16) }
    var blobs = Data()
    for id in ids {
        let blob = resources[id]!
        rsrc += u32(sectionVA + blobAt) + u32(blob.count) + u32(0) + u32(0)
        blobs += blob
        blobAt += blob.count
    }
    rsrc += u32(sectionVA + iconEntryAt + 16) + u32(64) + u32(0) + u32(0)
    rsrc += Data("DXBC".utf8) + Data(repeating: 0x49, count: 60)        // an icon that happens to start like a shader: not RCDATA, never taken
    rsrc += blobs
    var file = Data([0x4d, 0x5a]) + Data(repeating: 0, count: 0x3a) + u32(0x80) + Data(repeating: 0, count: 0x40)
    let optSize = 0xf0
    file += u32(0x4550) + u16(0x8664) + u16(1) + u32(0) + u32(0) + u32(0) + u16(optSize) + u16(0x2022)
    file += Data(repeating: 0, count: optSize)
    let rawAt = 0x400
    file += Data(".rsrc\0\0\0".utf8) + u32(rsrc.count) + u32(sectionVA) + u32(rsrc.count) + u32(rawAt) + Data(repeating: 0, count: 16)
    file += Data(repeating: 0, count: rawAt - file.count)
    return file + rsrc
}
func shader(_ id: Int, size: Int = 96) -> Data { Data("DXBC".utf8) + Data(repeating: UInt8(id & 0xff), count: size - 4) }

var resources: [Int: Data] = [:]
for id in 101...302 { resources[id] = shader(id, size: 64 + id) }     // as the real file: 202 compute shaders, the frame generation's among them
resources[400] = Data("not a shader, but long enough to be one".utf8) + Data(repeating: 0, count: 40)
let dll = makeDLL(resources)

require(Set(BCDLosslessShaders.required) == Set(255...302) && BCDLosslessShaders.required.count == 48, "the frame generation's shaders are the 48 ids 255 to 302")
let got = BCDLosslessShaders.extract(from: dll)
require(Set(got.keys) == Set(255...302), "from a DLL with 202 shaders only the frame generation's 48 are read: none of ids 101 to 254 (the upscalers' among them)")
require((255...302).allSatisfy { got[$0] == resources[$0] }, "each one byte for byte")
require(BCDLosslessShaders.extract(from: dll, wanted: [147, 400, 9999]).keys.sorted() == [147], "a resource is taken only when asked for by id and only when it is a DXBC blob")
require(BCDLosslessShaders.extract(from: Data(repeating: 0x41, count: 4096)).isEmpty && BCDLosslessShaders.extract(from: Data()).isEmpty, "garbage gives no shaders")
require(BCDLosslessShaders.extract(from: Data([0x4d, 0x5a] + [UInt8](repeating: 0, count: 200))).isEmpty, "a PE header with no resources gives no shaders")
require(BCDLosslessShaders.extract(from: dll.prefix(dll.count - 2000)).count < 48, "a file cut short gives fewer shaders, and nothing is read past its end")
for cut in stride(from: 0, to: dll.count, by: 37) { _ = BCDLosslessShaders.extract(from: dll.prefix(cut)) }
var noise = dll
for i in stride(from: 0x400, to: min(noise.count, 0x400 + 4000), by: 3) { noise[i] = noise[i] &+ 0x91 }
_ = BCDLosslessShaders.extract(from: noise)
require(true, "cut-off and damaged files are read without a crash")
let embedded = Data(repeating: 7, count: 13) + dll
require(Set(BCDLosslessShaders.extract(from: embedded.suffix(from: 13)).keys) == Set(255...302), "a slice of a larger buffer reads the same")

let box = BCDLosslessShaders.container(from: dll)
require(box != nil, "the container is made when every needed shader is there")
if let box {
    func u32(_ o: Int) -> Int { (0..<4).reduce(0) { $0 | Int(box[box.startIndex + o + $1]) << (8 * $1) } }
    require(box.prefix(4) == Data("LSFG".utf8) && u32(4) == 1 && u32(8) == 48, "its head: LSFG, version 1, 48 shaders")
    var o = 12, ids: [Int] = [], ok = true
    while o + 8 <= box.count {
        let id = u32(o), n = u32(o + 4)
        ok = ok && box.subdata(in: (o + 8)..<(o + 8 + n)) == resources[id]
        ids.append(id); o += 8 + n
    }
    require(ok && ids == Array(255...302) && o == box.count, "then (id, size, bytes) for 255 to 302 in order, and nothing after")
    require(box.range(of: resources[147]!) == nil && box.range(of: resources[101]!) == nil && box.count < dll.count / 2, "no other resource of the DLL is in it")
}
var missing = resources; missing[280] = nil
require(BCDLosslessShaders.container(from: makeDLL(missing)) == nil, "a DLL without one of the needed shaders (another version) gives no container")
var notShader = resources; notShader[300] = Data(repeating: 0x20, count: 200)
require(BCDLosslessShaders.container(from: makeDLL(notShader)) == nil, "... and so does one where a needed resource is not a shader")

// ---- a game's line and the control file -----------------------------------------------------------------------------------------
require(BCDLossless.generated(nil) == nil && BCDLossless.generated("") == nil && BCDLossless.generated("0") == nil && BCDLossless.generated("4") == nil
        && BCDLossless.generated("x") == nil, "no line, 0, 4 or a word: off")
require(BCDLossless.generated("1") == 1 && BCDLossless.generated(" 3 ") == 3, "1 to 3: that many frames added")
require(BCDLossless.flow(nil) == 1 && BCDLossless.flow("2") == 2 && BCDLossless.flow("9") == 4 && BCDLossless.flow("0.2") == 1 && BCDLossless.flow("x") == 1, "the flow divisor: 1 to 4, 1 without a line")
require(BCDLossless.Control() == BCDLossless.Control(enabled: false, generated: 1, flow: 1, force: false, measure: false, performance: true), "a control starts off, one frame, the lighter passes")
require(BCDLossless.Control(enabled: true, generated: 2, flow: 2, force: false, measure: true, performance: false).text(hz: 120)
        == "enabled=1\ngenerated=2\nflow=2.0\nforce=0\nperformance=0\nmeasure=1\nhz=120\n", "the control file's text, as the engine reads it")
require(BCDLossless.Control(enabled: true, generated: 9, flow: 40).text(hz: 60).contains("generated=3\nflow=4.0\n"), "numbers out of range are brought in")
require(BCDLossless.windowsControlPath == #"C:\ProgramData\BCD\lsfg.ctl"# && BCDLossless.windowsShaderPath == #"C:\ProgramData\BCD\lsfg.bin"# && BCDLossless.folder == "ProgramData/BCD",
        "the files are in BCD's own folder of the prefix")
let st = BCDLossless.parse(status: "state=generating\ngame=41.2\ngenerated=40.9\ncost=1.90\nsize=1408x648\nflow=704x324\nscreen=120\n")
require(st["state"] == "generating" && st["flow"] == "704x324", "the status file's lines")
let on = BCDLossless.Control(enabled: true)
require(BCDLossless.words(control: on, status: st).hasPrefix("Making 40.9 extra frames a second. The game draws 41.2 FPS"), "generating, in words")
require(BCDLossless.words(control: BCDLossless.Control(), status: st) == "Off.", "off is off whatever the engine said last")
require(BCDLossless.words(control: on, status: ["state": "error", "message": "the shader file is not there"]) == "Not working: the shader file is not there.", "an error says why")
require(BCDLossless.words(control: on, status: [:]).hasPrefix("Waiting for the game to draw"), "nothing from the engine yet")
require(BCDLossless.words(control: on, status: ["state": "running", "game": "118", "screen": "120"]).contains("cannot show more"), "a game as fast as the screen")

// ---- Settings' words --------------------------------------------------------------------------------------------------------------
require(BCDLossless.Setup().words.hasPrefix("Sign in to Steam first"), "signed out")
require(BCDLossless.Setup(signedIn: true).words.hasPrefix("Not set up"), "signed in, nothing stored")
require(BCDLossless.Setup(signedIn: true, owned: false).words == "This Steam account does not own Lossless Scaling.", "Steam said no")
require(BCDLossless.Setup(signedIn: true, owned: true, installed: true, status: .valid(daysLeft: 1)).words == "Ready. The Steam purchase is checked again within 1 day.", "ready, one day")
require(BCDLossless.Setup(signedIn: true, installed: true, status: .valid(daysLeft: 5)).usable && !BCDLossless.Setup(signedIn: true, installed: true, status: .expired).usable
        && !BCDLossless.Setup(signedIn: true, installed: false, status: .valid(daysLeft: 5)).usable, "usable only with the file and a valid receipt")
require(BCDLossless.Setup(signedIn: true, installed: true, status: .otherAccount).words.contains("another Steam account"), "another account")
require(BCDLossless.Setup(signedIn: true, busy: "Downloading from Steam\u{2026}").words == "Downloading from Steam\u{2026}", "busy shows what it is doing")

// ---- the in-game menu -------------------------------------------------------------------------------------------------------------
require(BCDLosslessMenu.section(live: false, ready: false, wanted: nil, control: BCDLossless.Control(), status: [:]) == nil,
        "not set up, not asked for, not running: no section at all (the menu is as before)")
let later = BCDLosslessMenu.section(live: false, ready: true, wanted: 2, control: BCDLossless.Control(), status: [:])!
require(later.rows.map(\.id) == ["mm.lsfg", "mm.lsfg.note"] && later.rows[1].title == "It applies the next time this game starts.", "set up and asked for, session without it: the choice and 'next start'")
require(BCDLosslessMenu.section(live: false, ready: false, wanted: 1, control: BCDLossless.Control(), status: [:])!.rows[1].title.hasPrefix("Set Lossless Scaling up first"), "asked for but not set up says where to set it up")
let liveSection = BCDLosslessMenu.section(live: true, ready: true, wanted: 1, control: BCDLossless.Control(enabled: true, generated: 2, flow: 2), status: st)!
require(liveSection.rows.map(\.id) == ["mm.lsfg", "mm.lsfg.note", "mm.lsfg.light", "mm.lsfg.flow", "mm.lsfg.force", "mm.lsfg.measure"], "a session with it: every switch")
if case .choice(let choices, let selected) = liveSection.rows[0].kind {
    require(choices.map(\.id) == ["", "1", "2", "3"] && choices.map(\.title) == ["Off", "2×", "3×", "4×"] && selected == "2", "Off, 2×, 3×, 4×; what runs is chosen")
} else { require(false, "the first row is a choice") }
require(liveSection.rows[1].kind == .note(.good) && liveSection.rows[2].kind == .toggle(true), "the status in the good tone while generating; lighter passes on")
if case .choice(_, let selected) = liveSection.rows[3].kind { require(selected == "2", "the flow choice shows half") } else { require(false, "flow is a choice") }
require(BCDLosslessMenu.section(live: true, ready: true, wanted: 1, control: BCDLossless.Control(), status: [:])!.rows.count == 2, "switched off in a session with it: the choice and the status only")
require(BCDLosslessMenu.flowChoice(1) == "1" && BCDLosslessMenu.flowChoice(1.33) == "1.33" && BCDLosslessMenu.flowChoice(2) == "2" && BCDLosslessMenu.flowChoice(4) == "4", "the four flow choices")
require(BCDLossless.choices.map(\.0) == ["", "1", "2", "3"] && BCDLossless.title.contains("experimental"), "the game page's choices, and the title says experimental")

print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
exit(failures == 0 ? 0 : 1)
