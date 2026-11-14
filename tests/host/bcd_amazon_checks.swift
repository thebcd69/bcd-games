// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-amazon.py, compiled with app/Madeira/SwiftAmazon/AmazonManifest.swift, AmazonAuth.swift and AmazonAPI.swift
// and a stand-in for the log. The first argument is the folder where the check wrote its signed manifests and keys.

import Foundation
import CryptoKit

@main enum Checks {
    static var failures = 0
    static func check(_ what: String, _ ok: Bool) {
        print((ok ? "PASS: " : "FAIL: ") + what)
        if !ok { failures += 1 }
    }

    static func main() {
        let dir = URL(fileURLWithPath: CommandLine.arguments[1])
        func read(_ name: String) -> Data { (try? Data(contentsOf: dir.appendingPathComponent(name))) ?? Data() }
        let key = read("key.spki"), other = read("other.spki")

        // ---- the manifest
        let parsed = try? AmazonManifest.parse(read("good.proto"), publicKey: key)
        check("a manifest signed with the key is read: its files, sizes and SHA-256 sums, and its folders (first package only)",
              parsed?.files.map(\.path) == ["Bin\\Game.exe", "fuel.json"] && parsed?.files.map(\.size) == [12, 3]
              && parsed?.files.first?.hashHex == SHA256.hash(data: Data("hello amazon".utf8)).map { String(format: "%02x", $0) }.joined()
              && parsed?.directories == ["Bin", "Data"])
        check("the same manifest with another key is refused", (try? AmazonManifest.parse(read("good.proto"), publicKey: other)) == nil)
        check("a changed byte is refused", (try? AmazonManifest.parse(read("bad.proto"), publicKey: key)) == nil)
        check("a list that does not match its signature is refused", (try? AmazonManifest.parse(read("evil.proto"), publicKey: key)) == nil)
        check("a file too short to be a manifest is refused", (try? AmazonManifest.parse(Data([0, 0, 0]), publicKey: key)) == nil)
        check("Amazon's own key is a usable RSA key", AmazonManifest.rsaKeyBody(fromSPKI: [UInt8](AmazonManifest.amazonPublicKey)) != nil)

        // ---- paths and names
        check("a manifest path becomes a relative path", AmazonInstall.safeRelativePath("Bin\\Sub\\game.exe") == "Bin/Sub/game.exe")
        check("a path that could leave the game folder is refused",
              ["..\\x.exe", "\\abs.exe", "/abs", "C:\\x.exe", "a\\..\\..\\b", ".\\x", ""].allSatisfy { AmazonInstall.safeRelativePath($0) == nil })
        let game = AmazonGame(id: "amzn1.adg.product.1234", entitlementID: "e1", title: "Some Game: Deluxe (2026)!", sku: "", artURL: nil, logoURL: nil, developer: nil)
        let folder = AmazonInstall.folderName(for: game)
        check("the folder name is the title in plain letters, with a tag from the product id",
              folder.hasPrefix("Some_Game_Deluxe_2026_") && folder.count == "Some_Game_Deluxe_2026_".count + 6
              && folder.unicodeScalars.allSatisfy { CharacterSet.alphanumerics.contains($0) || $0 == "_" })
        let twin = AmazonGame(id: "amzn1.adg.product.5678", entitlementID: "e2", title: game.title, sku: "", artURL: nil, logoURL: nil, developer: nil)
        check("two games of one name never share a folder", AmazonInstall.folderName(for: twin) != folder)

        // ---- the entitlements
        let entitlements: [[String: Any]] = [
            ["id": "e-b", "product": ["id": "p-b", "title": "Beta", "productLine": "Twitch:FuelGame", "sku": "s",
                                      "productDetail": ["details": ["backgroundUrl1": "https://x/b.jpg", "logoUrl": "https://x/l.png"]]]],
            ["id": "e-a", "product": ["id": "p-a", "title": " Alpha ", "productLine": ""]],
            ["id": "e-c", "product": ["id": "p-c", "title": "Sword skin", "productLine": "Twitch:FuelEntitlement"]],
            ["id": "e-d", "product": ["id": "p-d", "title": ""]],
        ]
        let (games, summary) = AmazonAPI.games(from: entitlements)
        check("games are kept, add-ons and nameless products are not, sorted by name",
              games.map(\.title) == ["Alpha", "Beta"] && games[1].entitlementID == "e-b" && games[1].art?.absoluteString == "https://x/b.jpg"
              && games[1].logo?.absoluteString == "https://x/l.png" && summary.hasPrefix("4 entitlement(s), 2 game(s)"))

        // ---- fuel.json
        let fuel = """
        {
          // the start
          "SchemaVersion": 2,
          "Main": { "Command": "Bin\\\\Game.exe", "Args": ["-windowed", "a b",], /* old */ "WorkingSubdirOverride": "Bin", },
        }
        """
        let launch = AmazonLaunch.parse(Data(("\u{FEFF}" + fuel).utf8))
        check("fuel.json with comments, trailing commas and a BOM names the program, its arguments and its folder",
              launch == AmazonLaunch(command: "Bin\\Game.exe", arguments: ["-windowed", "a b"], workingSubdirectory: "Bin"))
        check("a fuel.json without a program is nothing", AmazonLaunch.parse(Data("{\"Main\":{}}".utf8)) == nil)
        let root = dir.appendingPathComponent("drive/Amazon/" + folder, isDirectory: true)
        try? FileManager.default.createDirectory(at: root.appendingPathComponent("bin"), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: root.appendingPathComponent("bin/game.exe").path, contents: Data())
        FileManager.default.createFile(atPath: root.appendingPathComponent("fuel.json").path, contents: Data(fuel.utf8))
        let record = try? AmazonInstall.finish(game, folder: folder, root: root, version: "v1", installBytes: 15)
        check("the install record: the program spelled as on disk, the arguments quoted where needed, the working folder",
              record?.programPath == "Amazon/\(folder)/bin/game.exe" && record?.arguments == "-windowed \"a b\""
              && record?.workDir == "C:\\Amazon\\\(folder)\\bin" && record?.windowsInstallPath == "C:\\Amazon\\\(folder)")
        try? FileManager.default.removeItem(at: root.appendingPathComponent("fuel.json"))
        try? FileManager.default.removeItem(at: root.appendingPathComponent("bin/game.exe"))
        check("no fuel.json and no single program: the player is told where the folder is",
              (try? AmazonInstall.finish(game, folder: folder, root: root, version: "v1", installBytes: 0)) == nil)

        // ---- the sign-in's address
        let back = URL(string: "https://www.amazon.com/?openid.oa2.authorization_code=ANabc123&openid.mode=id_res")!
        check("the page Amazon's sign-in ends on is recognised and its code read",
              AmazonAuth.isLoginResult(back) && AmazonAuth.extractCode(from: back.absoluteString) == "ANabc123")
        check("any other address is not", !AmazonAuth.isLoginResult(URL(string: "https://www.amazon.com/ap/signin?x=1")!)
              && !AmazonAuth.isLoginResult(URL(string: "https://evil.example/?openid.oa2.authorization_code=1")!)
              && AmazonAuth.extractCode(from: "no code here") == nil)

        print(failures == 0 ? "ALL PASS" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
