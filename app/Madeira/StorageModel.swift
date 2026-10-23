//
//  StorageModel.swift
//  Madeira
//
//  madeira-bcd: Settings > Storage -- what the runtime keeps on the phone and a
//  way to take back the space. Frame captures (the overlay's CAP), session logs
//  and the shader caches all grow without the user ever seeing them; a phone
//  that fills up kills the game mid-session in ways that look like our bugs.
//

import SwiftUI

/// A user-authorized SSD folder, exposed to Wine through a link in the local C:.
/// The bookmark's scope stays open for native Wine/FEX file I/O, not just the picker.
final class ExternalGameStorage {
    static let shared = ExternalGameStorage()
    private let key = "bcd.external-games.bookmark"
    private let lock = NSRecursiveLock()
    private var folder: URL?
    private(set) var message = "Choose a folder on a connected SSD. Games there appear in C:\\External."

    private var link: URL? { GameLibrary.driveC?.appendingPathComponent("External", isDirectory: true) }
    var configured: Bool { UserDefaults.standard.data(forKey: key) != nil }

    var freeBytes: Int64? {
        lock.lock(); defer { lock.unlock() }
        guard let folder, FileManager.default.fileExists(atPath: folder.path) else { return nil }
        return (try? folder.resourceValues(forKeys: [.volumeAvailableCapacityKey]))?.volumeAvailableCapacity.map(Int64.init)
    }

    func restore() {
        guard let data = UserDefaults.standard.data(forKey: key) else { return }
        do {
            var stale = false
            let url = try URL(resolvingBookmarkData: data, options: [.withoutUI], relativeTo: nil, bookmarkDataIsStale: &stale)
            try connect(url)
        } catch {
            message = "External games unavailable. Reconnect the SSD and choose its folder again."
        }
    }

    func connect(_ url: URL) throws {
        lock.lock(); defer { lock.unlock() }
        let opened = url.startAccessingSecurityScopedResource()
        var retained = false
        defer { if opened && !retained { url.stopAccessingSecurityScopedResource() } }
        guard opened, let link, let drive = GameLibrary.driveC else {
            throw failure("BCD couldn't access this folder. Choose it again in Files.")
        }
        let target = url.resolvingSymlinksInPath().standardizedFileURL
        let local = drive.resolvingSymlinksInPath().standardizedFileURL.path
        guard target.path != local, !target.path.hasPrefix(local + "/"), !local.hasPrefix(target.path + "/"),
              (try target.resourceValues(forKeys: [.isDirectoryKey])).isDirectory == true else {
            throw failure("Choose a separate games folder on the SSD, outside BCD's Windows drive.")
        }
        // Probe write access before changing the mount. Never overwrite a user's folder.
        let probe = target.appendingPathComponent(".bcd-write-test-" + UUID().uuidString)
        try Data().write(to: probe, options: .withoutOverwriting)
        try FileManager.default.removeItem(at: probe)
        let bookmark = try url.bookmarkData(options: [.minimalBookmark], includingResourceValuesForKeys: nil, relativeTo: nil)
        try FileManager.default.createDirectory(at: drive, withIntermediateDirectories: true)
        let previous = try? FileManager.default.destinationOfSymbolicLink(atPath: link.path)
        if FileManager.default.fileExists(atPath: link.path) || previous != nil {
            guard let previous, let folder,
                  URL(fileURLWithPath: previous).standardizedFileURL.path == folder.resolvingSymlinksInPath().standardizedFileURL.path else {
                throw failure("C:\\External already exists. Rename that folder in Files before connecting the SSD.")
            }
            try FileManager.default.removeItem(at: link)
            do { try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target) }
            catch {
                try? FileManager.default.createSymbolicLink(atPath: link.path, withDestinationPath: previous)
                throw error
            }
        } else {
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        }
        if let folder { folder.stopAccessingSecurityScopedResource() }
        folder = url
        retained = true
        UserDefaults.standard.set(bookmark, forKey: key)
        message = "\(url.lastPathComponent) is connected as C:\\External. Keep the SSD connected while playing."
    }

    /// Accept only the authorized mount; links that escape either root stay rejected.
    func relativePath(for url: URL, drive: URL) -> String? {
        lock.lock(); defer { lock.unlock() }
        let path = url.resolvingSymlinksInPath().standardizedFileURL.path
        let root = drive.resolvingSymlinksInPath().standardizedFileURL.path
        if path.hasPrefix(root + "/") { return String(path.dropFirst(root.count + 1)) }
        guard let folder, let link,
              link.resolvingSymlinksInPath().standardizedFileURL.path == folder.resolvingSymlinksInPath().standardizedFileURL.path else { return nil }
        let external = folder.resolvingSymlinksInPath().standardizedFileURL.path
        if path == external { return "External" }
        guard path.hasPrefix(external + "/") else { return nil }
        return "External/" + path.dropFirst(external.count + 1)
    }

    private func failure(_ text: String) -> NSError {
        NSError(domain: "BCD.ExternalStorage", code: 1, userInfo: [NSLocalizedDescriptionKey: text])
    }
}

struct StorageItem: Identifiable {
    let id: String
    let title: String
    let detail: String
    let deletable: Bool
    let urls: () -> [URL]
}

final class StorageModel: ObservableObject {
    @Published private(set) var sizes: [String: Int64] = [:]
    @Published private(set) var busy = false

    static var documents: URL? { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first }

    /// C:\users\<name>\AppData\Local\BCD\ShaderCache for every profile.
    static func d3d12ShaderCaches() -> [URL] {
        guard let users = GameLibrary.driveC?.appendingPathComponent("users"),
              let names = try? FileManager.default.contentsOfDirectory(atPath: users.path) else { return [] }
        return names.map {
            users.appendingPathComponent($0).appendingPathComponent("AppData/Local/BCD/ShaderCache", isDirectory: true)
        }.filter { FileManager.default.fileExists(atPath: $0.path) }
    }

    static let items: [StorageItem] = [
        StorageItem(id: "capture", title: "Frame captures",
                    detail: "Written by the overlay's CAP button", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("capture", isDirectory: true)].compactMap { $0 } }),
        StorageItem(id: "logs", title: "Session logs",
                    detail: "Earlier games' logs (the newest 40 are kept)", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("logs", isDirectory: true),
                             StorageModel.documents?.appendingPathComponent("bcd-log.prev.txt")].compactMap { $0 } }),
        StorageItem(id: "sc12", title: "D3D12 shader cache",
                    detail: "Converted shaders; deleting it makes the next start convert them again", deletable: true,
                    urls: { StorageModel.d3d12ShaderCaches() }),
        StorageItem(id: "sc11", title: "D3D11 shader cache",
                    detail: "DXMT's compiled shaders", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("shadercache", isDirectory: true)].compactMap { $0 } }),
        StorageItem(id: "drive", title: "Windows drive (C:)",
                    detail: "Games, saves and the Windows files", deletable: false,
                    urls: { [GameLibrary.driveC].compactMap { $0 } }),
    ]

    func refresh() {
        guard !busy else { return }
        busy = true
        DispatchQueue.global(qos: .utility).async {
            var out: [String: Int64] = [:]
            for item in StorageModel.items {
                out[item.id] = item.urls().reduce(Int64(0)) { $0 + StorageModel.size(of: $1) }
            }
            DispatchQueue.main.async {
                self.sizes = out
                self.busy = false
            }
        }
    }

    func clear(_ item: StorageItem) {
        guard item.deletable else { return }
        DispatchQueue.global(qos: .utility).async {
            for u in item.urls() { try? FileManager.default.removeItem(at: u) }
            DispatchQueue.main.async { self.refresh() }
        }
    }

    /// Allocated bytes under `url` (a file or a directory; symlinks are not followed).
    static func size(of url: URL) -> Int64 {
        let fm = FileManager.default
        let keys: [URLResourceKey] = [.isRegularFileKey, .totalFileAllocatedSizeKey, .fileAllocatedSizeKey]
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else { return 0 }
        if !isDir.boolValue {
            let v = try? url.resourceValues(forKeys: Set(keys))
            return Int64(v?.totalFileAllocatedSize ?? v?.fileAllocatedSize ?? 0)
        }
        var total: Int64 = 0
        if let e = fm.enumerator(at: url, includingPropertiesForKeys: keys, options: [], errorHandler: { _, _ in true }) {
            for case let f as URL in e {
                guard let v = try? f.resourceValues(forKeys: Set(keys)), v.isRegularFile == true else { continue }
                total += Int64(v.totalFileAllocatedSize ?? v.fileAllocatedSize ?? 0)
            }
        }
        return total
    }
}
