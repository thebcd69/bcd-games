// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 ft7t8drgwf-wq
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Modified by BCD (thebcd69), 2026-11-05: split out of Madeira's ItchLibrary.swift (everything above its ItchLibrary class, Foundation only, so
// tests/host/check-itch.py compiles it on the Mac); BCD's words in the messages; the installer folder is .bcd-setup; the installed record
// also keeps the library entry's ID and the game's cover address (BCD's records name their entries, as BCDGOGInstallRecord does).

import Foundation

// The itch.io account's installs (docs/ITCH.md): the record of an installed game, the progress and error types, the installer kinds
// (ItchSetup), the program finder (ItchProgram) and one file download with pause (ItchTransfer).

/// A game BCD installed: where, which upload and how it starts.
struct ItchInstalledGame: Codable, Sendable {
    var gameID: Int
    var title: String
    /// The install folder, relative to drive_c ("itch/Celeste").
    var folder: String
    var uploadID: Int
    var buildID: Int
    var version: String
    /// The program, relative to the install folder, with "/" separators.
    var launchExe: String
    var arguments: String
    var installBytes: Int64
    /// Set while the download is an installer that has not installed the game yet:
    /// `launchExe` is then ItchSetup's batch file, and Play runs the installer.
    var setup: ItchSetup.Kind? = nil
    /// When the installer was downloaded: a folder it made elsewhere is newer.
    var setupSince: Date? = nil
    /// madeira-bcd: the library entry this install made (nil until it has one), and the game's cover address, kept so that an installed
    /// game keeps its picture after the account's list is gone (a sign-out).
    var entryID: UUID? = nil
    var cover: URL? = nil
    var programPath: String { folder + "/" + launchExe }
}

struct ItchDownloadProgress: Equatable, Sendable {
    enum Phase: String, Sendable { case preparing, downloading, unpacking }
    var phase: Phase = .preparing
    var totalBytes: UInt64 = 0
    var doneBytes: UInt64 = 0
    var bytesPerSecond: Double = 0
    var fraction: Double { totalBytes > 0 ? min(1, Double(doneBytes) / Double(totalBytes)) : 0 }
}

enum ItchInstallError: LocalizedError {
    case diskSpace(needed: Int64)
    case unsupportedFile(String)
    case innerRar
    case noProgram

    var errorDescription: String? {
        switch self {
        case .diskSpace(let needed):
            return "Not enough free space on this device: this download needs about \(ByteCountFormatter.string(fromByteCount: needed, countStyle: .file)) more. Free some space, then try again."
        case .unsupportedFile(let name):
            return "This game comes as \(name), which BCD cannot unpack yet. Only .zip and .7z archives, .exe files and .msi installers install from itch.io for now."
        case .innerRar:
            return "This game's download holds a .rar archive, which BCD cannot unpack. Unpack it in the game's folder in drive_c\\itch with another app, then add the game's .exe with Add a game in the Library."
        case .noProgram:
            return "BCD could not tell which program starts this game. Open its folder in drive_c\\itch and add the game's .exe with Add a game in the Library."
        }
    }

    var reason: String {
        switch self {
        case .diskSpace: return "disk-space"
        case .unsupportedFile: return "unsupported-file"
        case .innerRar: return "inner-rar"
        case .noProgram: return "no-program"
        }
    }
}

/// Installer downloads, the kinds the itch app's installers handle (hush: Inno Setup,
/// NSIS, MSI), found the way it finds them, by the file's own markers. Each runs once,
/// through a batch file in the game's hidden `.bcd-setup` folder, in the Wine
/// desktop with Windows services first, with its unattended switches and the game's
/// own folder as the destination; another installer runs as it is, so the player
/// answers its questions. After the session (or the next start of BCD), the
/// folder's program, or a new folder of the installer's own, becomes the game.
enum ItchSetup {
    enum Kind: String, Codable, Sendable { case inno, nsis, msi, other }
    static let folderName = ".bcd-setup"
    static let scriptName = "install.bat"
    /// Written by the batch file once the installer has ended.
    static let ranName = "ran"

    /// nil: not an installer (the game itself, or a file BCD does not know).
    static func kind(fileName: String, head: Data) -> Kind? {
        let name = fileName.lowercased()
        if name.hasSuffix(".msi") || head.starts(with: [0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) { return .msi }
        guard head.starts(with: [0x4D, 0x5A]) else { return nil }
        func has(_ marker: String) -> Bool { head.range(of: Data(marker.utf8)) != nil }
        if has("Inno Setup Setup Data") || has("JR.Inno.Setup") || has("InnoSetupLdrWindow") { return .inno }
        if has("NullsoftInst") || has("Nullsoft.NSIS") { return .nsis }
        let key = name.filter { $0.isLetter || $0.isNumber }
        if (key.contains("setup") || key.contains("install")) && !key.contains("uninst") { return .other }
        return nil
    }

    /// The file's first 8 MB, where installers keep their markers.
    static func kind(of url: URL, fileName: String) -> Kind? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        return kind(fileName: fileName, head: (try? handle.read(upToCount: 8 << 20)) ?? Data())
    }

    /// C:\-style path of a folder or file relative to drive_c.
    static func windowsPath(_ relative: String) -> String { "C:\\" + relative.replacingOccurrences(of: "/", with: "\\") }

    /// The batch file: the installer, into `target`, waiting until it ends.
    static func script(kind: Kind, installer rawInstaller: String, target rawTarget: String, title: String) -> String {
        // A batch file doubles %; NSIS's unquoted /D= also needs cmd's other specials escaped.
        let installer = rawInstaller.replacingOccurrences(of: "%", with: "%%")
        let target = rawTarget.replacingOccurrences(of: "%", with: "%%")
        let bare = ["^", "&", "|", "<", ">", "(", ")"].reduce(target) { $0.replacingOccurrences(of: $1, with: "^" + $1) }
        let command: String
        switch kind {
        case .inno:
            command = "start \"\" /wait \"\(installer)\" /SP- /SILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /DIR=\"\(target)\""
        case .nsis:
            // NSIS takes /D= last and unquoted, spaces and all.
            command = "start \"\" /wait \"\(installer)\" /S /D=\(bare)"
        case .msi:
            command = "start \"\" /wait msiexec /i \"\(installer)\" /qb /norestart TARGETDIR=\"\(target)\" INSTALLDIR=\"\(target)\" INSTALLFOLDER=\"\(target)\""
        case .other:
            command = "start \"\" /wait \"\(installer)\""
        }
        let safeTitle = title.filter { $0 != "%" && $0 != "\r" && $0 != "\n" && $0 != "^" && $0 != "&" && $0 != "|" && $0 != "<" && $0 != ">" }
        return ["@echo off", "rem BCD: the itch.io installer of \(safeTitle), into its own folder (ItchLibrary.swift).", command,
                "echo ran>\"%~dp0\(ranName)\"", ""]
            .joined(separator: "\r\n")
    }

    /// The folder's one installer, relative to it: the hidden setup folder's, else the
    /// only program within two levels that is an installer.
    static func installer(in folder: URL) -> (ItchSetup.Kind, String)? {
        let fm = FileManager.default
        let hidden = folder.appendingPathComponent(ItchSetup.folderName, isDirectory: true)
        var found: [(ItchSetup.Kind, String)] = []
        for name in (try? fm.contentsOfDirectory(atPath: hidden.path)) ?? [] where name.lowercased() != scriptName && name != ranName {
            if let kind = ItchSetup.kind(of: hidden.appendingPathComponent(name), fileName: name) { found.append((kind, ItchSetup.folderName + "/" + name)) }
        }
        if found.isEmpty, let walker = fm.enumerator(at: folder, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]) {
            let base = folder.resolvingSymlinksInPath().standardizedFileURL.path
            for case let url as URL in walker {
                let relative = String(url.resolvingSymlinksInPath().standardizedFileURL.path.dropFirst(base.count + 1))
                if relative.split(separator: "/").count > 2 { walker.skipDescendants(); continue }
                let ext = url.pathExtension.lowercased()
                guard ext == "exe" || ext == "msi" else { continue }
                if let kind = ItchSetup.kind(of: url, fileName: url.lastPathComponent) { found.append((kind, relative)) }
            }
        }
        return found.count == 1 ? found[0] : nil
    }

    /// A folder the installer made somewhere else (its own choice of Program Files,
    /// AppData\Local\Programs or C:\): the one named like the game, else the only new
    /// one. Relative to drive_c.
    static func installedElsewhere(drive: URL, title: String, since: Date) -> String? {
        let fm = FileManager.default
        func key(_ s: String) -> String { s.lowercased().filter { $0.isLetter || $0.isNumber } }
        let skip: Set<String> = ["windows", "users", "programdata", "program files", "program files (x86)", "itch", "madeira downloads",
                                 "madeira-games", "common files", "windows nt", "internet explorer", "windows media player",
                                 "windowspowershell", "microsoft", "steam", "gog games", "epic games",
                                 "bcd-games", "bcd-launchers", "external", "steamapps", "bcd"]   // madeira-bcd: BCD's own folders in drive_c
        var roots = ["Program Files", "Program Files (x86)", ""]
        for user in (try? fm.contentsOfDirectory(atPath: drive.appendingPathComponent("users").path)) ?? [] where !user.hasPrefix(".") {
            roots.append("users/\(user)/AppData/Local/Programs")
        }
        var fresh: [String] = []
        for root in roots {
            let url = root.isEmpty ? drive : drive.appendingPathComponent(root, isDirectory: true)
            guard let names = try? fm.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isDirectoryKey, .creationDateKey]) else { continue }
            for child in names where !child.lastPathComponent.hasPrefix(".") && !skip.contains(child.lastPathComponent.lowercased())
                && !child.lastPathComponent.lowercased().hasPrefix("madeira") && !child.lastPathComponent.lowercased().hasPrefix("bcd") {
                let values = try? child.resourceValues(forKeys: [.isDirectoryKey, .creationDateKey])
                guard values?.isDirectory == true, let made = values?.creationDate, made >= since.addingTimeInterval(-60) else { continue }
                fresh.append(root.isEmpty ? child.lastPathComponent : root + "/" + child.lastPathComponent)
            }
        }
        let wanted = key(title)
        let named = fresh.filter { let k = key(($0 as NSString).lastPathComponent); return !wanted.isEmpty && (k == wanted || k.contains(wanted) || wanted.contains(k)) && !k.isEmpty }
        if named.count == 1 { return named[0] }
        return fresh.count == 1 ? fresh[0] : nil
    }
}

/// Which program starts an unpacked game.
enum ItchProgram {
    /// Programs that are never the game: uninstallers, crash reporters, redistributables.
    static func helper(_ name: String) -> Bool {
        let n = name.lowercased()
        return n.hasPrefix("unins") || n.contains("crashhandler") || n.contains("crashreport") || n.contains("redist")
            || n.hasPrefix("vcredist") || n.hasPrefix("dxsetup") || n.hasPrefix("dxwebsetup") || n.hasPrefix("oalinst")
            || n.contains("setup") || n.hasPrefix("notification_helper") || n == "zsync.exe" || n.hasPrefix("ue4prereq")
            || n.hasPrefix("dotnet") || n.hasPrefix("ndp")
    }

    /// The first [[actions]] path of an .itch.toml manifest (the author's own launch
    /// action), relative to the folder, or nil.
    static func manifestAction(_ text: String) -> String? {
        var inAction = false
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("[[") { inAction = line == "[[actions]]"; continue }
            guard inAction, line.hasPrefix("path"), let eq = line.firstIndex(of: "=") else { continue }
            var value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            // A basic string ("bin\\Game.exe" in the file) has TOML's escapes; a literal one ('...') none.
            if value.hasPrefix("\""), let end = value.dropFirst().firstIndex(of: "\"") {
                value = String(value[value.index(after: value.startIndex)..<end]).replacingOccurrences(of: "\\\\", with: "\\")
            } else if value.hasPrefix("'"), let end = value.dropFirst().firstIndex(of: "'") { value = String(value[value.index(after: value.startIndex)..<end]) }
            let path = value.replacingOccurrences(of: "\\", with: "/").trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            guard path.lowercased().hasSuffix(".exe"), ItchArchive.safeName(path) == path else { continue }
            return path
        }
        return nil
    }

    /// The program, relative to `folder`: the manifest's action when it names one there,
    /// else the only program (helpers aside) within three folder levels, else the one
    /// named like the game, else the one nearest the top. nil when there is none or
    /// several equally likely.
    static func find(in folder: URL, title: String) -> String? {
        let fm = FileManager.default
        if let text = try? String(contentsOf: folder.appendingPathComponent(".itch.toml"), encoding: .utf8),
           let path = manifestAction(text), fm.fileExists(atPath: folder.appendingPathComponent(path).path) {
            return path
        }
        var programs: [String] = []
        let base = folder.resolvingSymlinksInPath().standardizedFileURL.path
        if let walker = fm.enumerator(at: folder, includingPropertiesForKeys: [.isRegularFileKey], options: [.skipsHiddenFiles]) {
            for case let url as URL in walker {
                let relative = String(url.resolvingSymlinksInPath().standardizedFileURL.path.dropFirst(base.count + 1))
                if relative.split(separator: "/").count > 4 { walker.skipDescendants(); continue }
                if url.pathExtension.lowercased() == "exe", !helper(url.lastPathComponent) { programs.append(relative) }
            }
        }
        return choose(programs, title: title)
    }

    static func choose(_ programs: [String], title: String) -> String? {
        if programs.count == 1 { return programs[0] }
        guard !programs.isEmpty else { return nil }
        func key(_ s: String) -> String { s.lowercased().filter { $0.isLetter || $0.isNumber } }
        let wanted = key(title)
        let named = programs.filter { key(($0 as NSString).lastPathComponent.replacingOccurrences(of: ".exe", with: "", options: .caseInsensitive)) == wanted }
        if named.count == 1 { return named[0] }
        let depth = { (p: String) in p.split(separator: "/").count }
        let top = programs.map(depth).min()!
        let shallow = programs.filter { depth($0) == top }
        return shallow.count == 1 ? shallow[0] : nil
    }
}

/// One file download with pause (resume data) and progress.
final class ItchTransfer: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private var continuation: CheckedContinuation<Void, Error>?
    private var task: URLSessionDownloadTask?
    private var session: URLSession?
    private let destination: URL
    private let resumeFile: URL
    private let progress: @Sendable (UInt64, UInt64) -> Void
    private var moveError: Error?

    init(destination: URL, resumeFile: URL, progress: @escaping @Sendable (UInt64, UInt64) -> Void) {
        self.destination = destination; self.resumeFile = resumeFile; self.progress = progress
    }

    func run(_ request: URLRequest) async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                self.continuation = continuation
                let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
                self.session = session
                let task: URLSessionDownloadTask
                if let data = try? Data(contentsOf: resumeFile), !data.isEmpty {
                    task = session.downloadTask(withResumeData: data)
                } else {
                    task = session.downloadTask(with: request)
                }
                self.task = task
                // Paused before the task existed: stop at once (the handler below found no task).
                if Task.isCancelled { task.cancel() }
                task.resume()
            }
        } onCancel: {
            // Pause: keep what came so far for the next try.
            task?.cancel(byProducingResumeData: { [resumeFile] data in
                if let data { try? data.write(to: resumeFile, options: .atomic) }
            })
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        progress(UInt64(max(0, totalBytesWritten)), UInt64(max(0, totalBytesExpectedToWrite)))
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { moveError = ItchAPIError.http(status); return }
        do {
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: location, to: destination)
        } catch { moveError = error }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        session.finishTasksAndInvalidate()
        let resume = (error as? URLError)?.downloadTaskResumeData
        if let resume { try? resume.write(to: resumeFile, options: .atomic) }
        else if error == nil { try? FileManager.default.removeItem(at: resumeFile) }
        let result = error ?? moveError
        continuation?.resume(with: result.map { .failure($0) } ?? .success(()))
        continuation = nil
    }
}
