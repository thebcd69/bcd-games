// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Writes the install record of a fixture game with AppManifestWriter and prints it (tests/host/check-steam-branches.py compiles this with the
// production writer and compares the text with the record the writer wrote before version selection existed). Argument 1: a folder;
// argument 2 (optional): the beta branch the record names.

import Foundation

@main struct Harness {
    static func main() {
        let dir = URL(fileURLWithPath: CommandLine.arguments[1])
        let beta = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : nil
        let depots = [AppManifestWriter.InstalledDepot(depotID: 9001, manifestGID: 111, size: 1000),
                      AppManifestWriter.InstalledDepot(depotID: 9002, manifestGID: 112, size: 234)]
        if let beta {
            try! AppManifestWriter.writeManifest(appID: 9000, name: "Fixture Game", installDir: "Fixture Game", buildID: 1000, steamID: 76561190000000000,
                                                 sizeOnDisk: 1234, steamAppsPath: dir.path, installedDepots: depots,
                                                 sharedDepots: [(depotID: 9003, ownerAppID: 9100)], customExecutables: ["tool\\custom.bin"],
                                                 language: "german", betaKey: beta)
        } else {
            try! AppManifestWriter.writeManifest(appID: 9000, name: "Fixture Game", installDir: "Fixture Game", buildID: 1000, steamID: 76561190000000000,
                                                 sizeOnDisk: 1234, steamAppsPath: dir.path, installedDepots: depots,
                                                 sharedDepots: [(depotID: 9003, ownerAppID: 9100)], customExecutables: ["tool\\custom.bin"],
                                                 language: "german")
        }
        let text = try! String(contentsOf: dir.appendingPathComponent("appmanifest_9000.acf"), encoding: .utf8)
        print(text.components(separatedBy: "\n").map { $0.contains("\"LastUpdated\"") ? "\t\"LastUpdated\"" : $0 }.joined(separator: "\n"), terminator: "")
    }
}
