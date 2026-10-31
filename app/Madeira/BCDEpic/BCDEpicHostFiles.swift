// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The files of an Epic game's signed-in start (docs/research/open-fork-2026-10-18/epic.md §4.4, §4.5, §4.9):
// the request BCD Host reads once (bcd-host/src/request.h is the format's other half), the ownership-token
// file the game reads while it runs, the host's report, and the clean-up of all three. Foundation only (the
// host checks compile it). What is written here is a secret for a few minutes (a one-time sign-in code in the
// request's arguments, an ownership token in the token file): both files are created exclusively (never
// following a link), readable by the app alone, never backed up, and removed by the host when it has read the
// request, by the app when the session ends, at the next start of the app and at sign-out. No value of either
// file is ever logged; the report holds numbers only.

/// What BCD Host is asked to start: a program, its folder and arguments, and the one file to delete at the end.
struct BCDEpicHostRequest: Equatable {
    static let magic = "BCDHOST1"
    static let version: UInt32 = 1
    static let maxSize = 32768

    struct Flags: OptionSet, Equatable {
        let rawValue: UInt32
        /// Wait until every process the program started has ended, not only the program.
        static let tree = Flags(rawValue: 1)
        /// Start services.exe first (outside the job: it lives as long as the session).
        static let services = Flags(rawValue: 2)
    }

    enum Tag: UInt16 {
        case program = 1
        case cwd = 2
        case args = 3
        case ovt = 4
        case root = 5
    }

    enum Problem: Error, Equatable {
        case tooBig
        case badValue(String)
    }

    var program: String
    var workingFolder: String?
    /// The command line after the program, already quoted where it needs it.
    var arguments: String
    /// The ownership-token file (C:\ProgramData\BCD\...) the host deletes when the game's tree has ended.
    var ovt: String?
    /// The folder the program must be under (the install root).
    var root: String?
    var flags: Flags = [.tree]

    func encoded() throws -> Data {
        var data = Data(Self.magic.utf8)
        func put32(_ value: UInt32) { for shift in stride(from: 0, to: 32, by: 8) { data.append(UInt8(truncatingIfNeeded: value >> UInt32(shift))) } }
        func field(_ tag: Tag, _ text: String) throws {
            let bytes = Array(text.utf8)
            guard !text.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) else { throw Problem.badValue("\(tag)") }
            data.append(UInt8(tag.rawValue & 0xFF)); data.append(UInt8(tag.rawValue >> 8))
            put32(UInt32(bytes.count))
            data.append(contentsOf: bytes)
        }
        put32(Self.version)
        put32(flags.rawValue)
        try field(.program, program)
        if let workingFolder { try field(.cwd, workingFolder) }
        if !arguments.isEmpty { try field(.args, arguments) }
        if let ovt { try field(.ovt, ovt) }
        if let root { try field(.root, root) }
        guard data.count <= Self.maxSize else { throw Problem.tooBig }
        return data
    }
}

/// What BCD Host's report says (numbers only; the host's report.h lists the keys).
struct BCDEpicHostReport: Equatable {
    static let keys: Set<String> = ["v", "stage", "error", "last-error", "flags", "open", "services", "job", "wait",
                                    "child-pid", "child-exit", "settle", "ovt", "seconds", "lived", "late", "left", "helpers", "names"]
    var values: [String: UInt32] = [:]

    subscript(_ key: String) -> UInt32? { values[key] }

    /// Reads a report: only the listed keys, only decimal numbers of at most ten digits; anything else is skipped.
    static func parse(_ text: String) -> BCDEpicHostReport? {
        guard text.utf8.count <= 2048 else { return nil }
        var report = BCDEpicHostReport()
        for line in text.split(whereSeparator: { $0 == "\n" || $0 == "\r" }) {
            let parts = line.split(separator: "=", maxSplits: 1)
            guard parts.count == 2, keys.contains(String(parts[0])), parts[1].utf8.count <= 10,
                  parts[1].utf8.allSatisfy({ $0 >= 0x30 && $0 <= 0x39 }), let value = UInt32(parts[1]) else { continue }
            report.values[String(parts[0])] = value
        }
        return report.values["stage"] == nil ? nil : report
    }

    /// The report as a log line: the keys in a fixed order, numbers only.
    var logText: String {
        ["stage", "error", "last-error", "open", "services", "job", "wait", "settle", "child-exit", "ovt", "seconds", "lived", "late", "left", "helpers", "names"]
            .compactMap { key in values[key].map { "\(key)=\($0)" } }.joined(separator: " ")
    }

    /// The host stopped before it started the game (a request it could not read, a program it could not start).
    var failedBeforeStart: Bool { (values["error"] ?? 0) != 0 && (values["stage"] ?? 0) < 6 }
}

extension BCDEpicHostFiles {
    /// madeira-bcd (round 624, Discord forum item 7): what the player reads when BCD Host stopped before the game started, or did not
    /// run at all. Epic had already given this start its sign-in (the exchange code's request succeeded, or no session would have run),
    /// so it is not about Epic: the old words ("BCD couldn't start this game signed in to Epic") had players sign out and in again.
    static let notStartedWords = "BCD Host stopped before the game started; your Epic sign-in is fine. Press Play to try again, and if it "
        + "happens again, send the log from Settings › Logs."
}

enum BCDEpicHostFiles {
    enum Problem: Error, Equatable {
        case folder
        case file
    }

    /// Where the request lives: a folder of its own under Application Support (0700, complete protection, not backed up).
    static func transferFolder(_ base: URL) -> URL { base.appendingPathComponent("transfer", isDirectory: true) }
    static func requestFile(_ base: URL) -> URL { transferFolder(base).appendingPathComponent("launch.req") }

    /// Wine's Unix namespace path of a file on the device (the prefix has no guaranteed Z: drive): what BCD_HOST_REQUEST holds,
    /// spelled as BCD Dock's transfer is (MadeiraDock.handoffGuestPath).
    static func guestPath(_ url: URL) -> String { "\\\\?\\unix" + url.path.replacingOccurrences(of: "/", with: "\\") }

    // MARK: The ownership-token file

    static let windowsFolder = "C:\\ProgramData\\BCD\\Epic"

    /// An identifier that is safe in a file name: letters, digits, '-' and '_'.
    static func plain(_ text: String, max: Int = 80) -> Bool {
        !text.isEmpty && text.utf8.count <= max && text.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x5A) || ($0 >= 0x61 && $0 <= 0x7A) || $0 == 0x2D || $0 == 0x5F }
    }

    /// The token file's name: the namespace and the catalog item, as Epic's launcher names it by them.
    static func ovtName(namespace: String, catalogItemId: String) -> String? {
        plain(namespace) && plain(catalogItemId) ? namespace + catalogItemId + ".ovt" : nil
    }

    static func ovtFolder(drive: URL) -> URL { drive.appendingPathComponent("ProgramData/BCD/Epic", isDirectory: true) }
    static func ovtWindowsPath(_ name: String) -> String { windowsFolder + "\\" + name }
    static func reportFile(drive: URL) -> URL { drive.appendingPathComponent("ProgramData/BCD/host.txt") }

    // MARK: Writing

    /// Creates a file that did not exist (never following a link), readable by the app alone, and writes `data` into it.
    private static func create(_ url: URL, _ data: Data) throws {
        let descriptor = open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw Problem.file }
        let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        do {
            #if os(iOS)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: url.path)
            #endif
            try file.write(contentsOf: data)
            try file.close()
        } catch {
            try? file.close()
            try? FileManager.default.removeItem(at: url)
            throw error
        }
    }

    private static func plainFolder(_ url: URL, owned owner: Bool) throws {
        let fm = FileManager.default
        var attributes: [FileAttributeKey: Any] = [.posixPermissions: 0o700]
        #if os(iOS)
        if owner { attributes[.protectionKey] = FileProtectionType.complete }
        #endif
        try fm.createDirectory(at: url, withIntermediateDirectories: true, attributes: attributes)
        guard try url.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else { throw Problem.folder }
        if owner {
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            var folder = url
            try folder.setResourceValues(values)
        }
    }

    /// The request, written for one start: any earlier one goes first.
    static func writeRequest(_ data: Data, base: URL) throws -> URL {
        let file = requestFile(base)
        try? FileManager.default.removeItem(at: file)
        try plainFolder(transferFolder(base), owned: true)
        try create(file, data)
        return file
    }

    /// The ownership token's file, holding Epic's whole reply (a bare token is refused by some games' checks). Returns the
    /// file and its Windows path. Written under the prefix's drive, where the game reads it.
    static func writeOwnership(_ reply: Data, name: String, drive: URL) throws -> (file: URL, windows: String) {
        guard name.hasSuffix(".ovt"), plain(String(name.dropLast(4)), max: 160) else { throw Problem.file }
        let folder = ovtFolder(drive: drive)
        try plainFolder(folder, owned: false)
        let file = folder.appendingPathComponent(name)
        try? FileManager.default.removeItem(at: file)
        try create(file, reply)
        return (file, ovtWindowsPath(name))
    }

    // MARK: Clean-up

    /// Removes what a start left: the request, every token file, and (when `report` is true) the host's report. Returns how many
    /// files went. Safe to run at any time (also with nothing there).
    @discardableResult
    static func cleanup(base: URL, drive: URL?, report: Bool = false) -> Int {
        let fm = FileManager.default
        var removed = 0
        func remove(_ url: URL) { if (try? fm.removeItem(at: url)) != nil { removed += 1 } }
        remove(requestFile(base))
        if let drive {
            let folder = ovtFolder(drive: drive)
            for name in (try? fm.contentsOfDirectory(atPath: folder.path)) ?? [] where name.hasSuffix(".ovt") {
                remove(folder.appendingPathComponent(name))
            }
            if report { remove(reportFile(drive: drive)) }
        }
        return removed
    }

    /// The host's report, if it wrote one.
    static func readReport(drive: URL) -> BCDEpicHostReport? {
        guard let data = try? Data(contentsOf: reportFile(drive: drive)), data.count <= 2048, let text = String(data: data, encoding: .utf8) else { return nil }
        return BCDEpicHostReport.parse(text)
    }
}
