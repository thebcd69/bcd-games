// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Add a game's engine side (BCDAddGame.swift): drive_c's folders (LibraryModel.drive), a program
// read as the library reads one (LibraryModel.inspect, LibraryModel.programExtensions), saved to
// the library (LibraryModel.save, which gives a game seen for the first time its recommended
// settings), and every game of drive_c added at once (BCDLibraryImport). Engine-bound: the
// preview build leaves it out (tools/ui-preview.py, ENGINE_BOUND). BCDAddGameSource finds it by
// its Objective-C name.

@objc(BCDAddGameLive)
@MainActor final class BCDAddGameLive: NSObject, BCDAddGameBackend {
    nonisolated override init() { super.init() }

    private var drive: URL { LibraryModel.drive }

    private func url(_ path: String) -> URL {
        path.isEmpty ? drive : drive.appendingPathComponent(path)
    }

    /// Only what lies inside drive_c, links followed: a link out of it is not listed. The links that lead out and are
    /// listed are the external drives added in Settings › Storage (C:\External\<name>, while connected; ExternalDrives.swift).
    private func inside(_ url: URL) -> Bool { LibraryModel.relativeToDrive(url) != nil }

    func list(_ folder: String) throws -> [BCDDriveItem] {
        let base = url(folder)
        guard folder.isEmpty || inside(base) else { throw BCDGameError("This folder is outside drive_c.") }
        let names: [URL]
        if folder.lowercased() == "external" || folder.lowercased().hasPrefix("external/") {
            // C:\External and the drives in it, listed by path: the path-based call follows a drive's link, and the names
            // keep `base` as their parent, so they stay under drive_c (the URL listing fails on a link as the last component).
            guard let found = try? FileManager.default.contentsOfDirectory(atPath: base.path) else {
                throw BCDGameError("BCD couldn't open this folder. If it is on an external drive, check that the drive is connected.")
            }
            names = found.filter { !$0.hasPrefix(".") }.map { base.appendingPathComponent($0) }
        } else {
            do {
                names = try FileManager.default.contentsOfDirectory(at: base, includingPropertiesForKeys: [.isDirectoryKey],
                                                                    options: .skipsHiddenFiles)
            } catch {
                throw BCDGameError("BCD couldn't open this folder.")
            }
        }
        var items: [BCDDriveItem] = []
        for file in names where inside(file) {
            let isFolder = (try? file.resolvingSymlinksInPath().resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory ?? false
            guard isFolder || LibraryModel.programExtensions.contains(file.pathExtension.lowercased()) else { continue }
            let path = folder.isEmpty ? file.lastPathComponent : folder + "/" + file.lastPathComponent
            items.append(BCDDriveItem(name: file.lastPathComponent, path: path, folder: isFolder))
        }
        return items.sorted {
            $0.folder != $1.folder ? $0.folder : $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
    }

    func inspect(_ path: String) throws -> BCDProgramInfo {
        let entry = try LibraryModel.inspect(url(path))
        return BCDProgramInfo(title: entry.title, windowsPath: entry.windowsPath, bits: entry.bits, api: entry.graphicsAPI)
    }

    /// The program, read again and saved with its name; a program the library already holds is
    /// not added twice: its game is the answer.
    func add(_ path: String, title: String) throws -> String {
        var entry = try LibraryModel.inspect(url(path))
        let library = LibraryModel.shared
        if let known = library.entries.first(where: {
            $0.desktop != true && $0.steamAppID == nil && $0.relativePath.lowercased() == entry.relativePath.lowercased()
        }) {
            return BCDLiveID.local(known.id)
        }
        let name = title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !name.isEmpty { entry.title = name }
        library.save(entry)
        guard library.entries.contains(where: { $0.id == entry.id }) else {
            throw BCDGameError("BCD couldn't add the game to the library.")
        }
        LogStore.shared.log("[bcd-ui] added a game: \(entry.bits)-bit \(entry.graphicsAPI ?? "-")")
        return BCDLiveID.local(entry.id)
    }

    func addEveryGame() async -> Int {
        await withCheckedContinuation { done in
            BCDLibraryImport.run { added in done.resume(returning: added) }
        }
    }
}
