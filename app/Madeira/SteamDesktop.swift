// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(CryptoKit)
import CryptoKit
#endif

// Steam for Windows (experimental; branch steam-client, milestone M1 "bootstrap"; docs/STEAM_DESKTOP.md): Valve's own Steam client,
// steam.exe with its window, store and library, in a Wine desktop. A separate choice beside BCD Dock, off unless Settings › BCD Dock ›
// Steam for Windows turns it on.
//
// What is here, Foundation only (tests/host/check-steam-desktop.py compiles this file with SteamRuntime.swift and runs it):
//   - SteamDesktop: the folder (never the Dock's), the library entry, the switch, the command line;
//   - SteamDesktopManifest: Valve's public client manifest, read into packages with their sizes and SHA-256 sums;
//   - SteamDesktopInstaller: steam.exe fetched and unpacked with the app's own ZIP reader, and Valve's other packages put into
//     the client's own package folder, each file checked against the manifest;
//   - SteamDesktopCDN: the one HTTPS host those files come from, every redirect checked, a download that goes on where it stopped;
//   - SteamDesktopProfile: the session's built-in settings and the file a session reads them from;
//   - SteamDesktopRegistry: the registry values written before a session;
//   - SteamDesktopWatch: what a session's log says about Steam's processes, windows, memory and the JIT pool.
// The engine's side (the library entry, the session, the Steam connection) is BCDSteamDesktopLive.swift.
//
// No Valve file is in BCD. The client is Valve's unchanged program, signed in by the player in Valve's own window; BCD hands it no
// sign-in, no licence and no ticket.

// MARK: - Names

enum SteamDesktop {
    /// The client's own folder. BCD Dock's client is in `SteamRuntimeFiles.windowsRoot` ("...\Steam"), which nothing here writes to:
    /// Valve's client updates itself and would replace the pinned build the Dock host checks.
    static let windowsRoot = SteamRuntimeFiles.desktopRoot
    static let relativeRoot = "Program Files (x86)/Steam Desktop"
    static let program = "steam.exe"
    static var relativeProgram: String { relativeRoot + "/" + program }
    static var windowsProgram: String { windowsRoot + "\\" + program }
    /// Where Valve's own updater keeps the packages it downloaded, and where BCD puts the ones it fetched for it.
    static let packageFolder = "package"

    /// The name on the library tile and in Settings.
    static let title = "Steam for Windows (experimental)"
    /// The library entry BCD makes for the client (a fixed id, as a launcher's entry has).
    static let entryID = UUID(uuidString: "5B7D3C1E-9A44-4E0B-8F52-6C1D0E7A2B93")!

    /// Settings › BCD Dock › Steam for Windows (UserDefaults): off unless the player turned it on.
    static let enabledKey = "bcd.steamDesktop.enabled"
    /// "Download the client's files with BCD first": on unless the player turned it off.
    static let packagesKey = "bcd.steamDesktop.packages"
    static var isEnabled: Bool { UserDefaults.standard.bool(forKey: enabledKey) }
    static var fetchesPackages: Bool { UserDefaults.standard.object(forKey: packagesKey) as? Bool ?? true }
    /// Posted when the switch or the installed state changed (the library shows or hides the tile).
    static let didChange = Notification.Name("BCDSteamDesktopDidChange")

    /// The app sets this to 1 for a Steam for Windows session and unsets it for every other launch (ContentView.runWineFullSequence);
    /// WineProcessBridge.m then publishes no fixed Steam game identity.
    static let sessionVariable = "MADEIRA_STEAM_DESKTOP_SESSION"

    /// Steam's own options for the session (plan §4.3; each is in the current client's option table): no friends list at start, no
    /// file check after an unclean exit, web views drawn without the GPU, no crash uploader, no hang timeouts under emulation, no shader
    /// work, no VR. The entry's Launch arguments hold them; a player who changes that text keeps his own.
    static let defaultArguments = "-nofriendsui -noverifyfiles -cef-disable-gpu -cef-disable-gpu-compositing -cef-disable-breakpad "
        + "-cef-disable-hang-timeouts -noshaders -vrdisable"
    /// Arguments BCD wrote as its default in an earlier build: an entry that still has one of them takes the current default.
    static let earlierArguments: [String] = []

    /// The arguments a start uses: BCD's when the entry has none or an earlier default of BCD's, else the player's own text.
    static func arguments(entry current: String) -> String {
        let own = current.trimmingCharacters(in: .whitespaces)
        return own.isEmpty || earlierArguments.contains(own) ? defaultArguments : own
    }

    /// Steam's own options a log may name: BCD's defaults and the ones the plan's measurement tries.
    static let knownArguments: Set<String> = Set(defaultArguments.split(separator: " ").map(String.init)).union(
        ["-no-dwrite", "-silent", "-bigpicture", "-gamepadui", "-nobigpicture", "-cef-disable-sandbox", "-nojoy", "-console", "-dev"])

    /// The arguments for the log: "default", or the known options among them and how many other words there are. What the player
    /// typed is never written out (a log must not carry a word that could be an account's).
    static func argumentsForLog(_ arguments: String) -> String {
        if arguments == defaultArguments { return "default" }
        let words = arguments.split(whereSeparator: { $0 == " " || $0 == "\t" }).map { $0.lowercased() }
        let known = words.filter(knownArguments.contains)
        return "own(" + (known.isEmpty ? "-" : known.joined(separator: " ")) + "; \(words.count - known.count) other)"
    }

    /// Whether these arguments keep web views off the GPU (the registry values of SteamDesktopRegistry follow it).
    static func softwareWebViews(_ arguments: String) -> Bool {
        arguments.split(whereSeparator: { $0 == " " || $0 == "\t" }).contains { $0.lowercased() == "-cef-disable-gpu" }
    }

    /// A program path that is the client's steam.exe, in either spelling of the separators and any case.
    static func isClient(windowsPath: String) -> Bool {
        windowsPath.replacingOccurrences(of: "/", with: "\\").caseInsensitiveCompare(windowsProgram) == .orderedSame
    }

    /// A path inside the Dock's client folder (or the folder itself). Nothing of Steam for Windows may be written there.
    static func isDockPath(relative: String) -> Bool {
        let path = relative.lowercased(), dock = SteamRuntimeFiles.relativeRoot.lowercased()
        return path == dock || path.hasPrefix(dock + "/")
    }

    /// A 64-bit Windows program by its headers: "MZ", the PE signature and machine AMD64.
    static func isWindowsProgram64(_ data: Data) -> Bool {
        guard data.count >= 0x100, data[data.startIndex] == 0x4d, data[data.startIndex + 1] == 0x5a else { return false }
        func u32(_ p: Int) -> Int { (0..<4).reduce(0) { $0 | Int(data[data.startIndex + p + $1]) << ($1 * 8) } }
        let pe = u32(60)
        guard pe >= 64, pe + 6 <= data.count, u32(pe) == 0x0000_4550 else { return false }
        return Int(data[data.startIndex + pe + 4]) | Int(data[data.startIndex + pe + 5]) << 8 == 0x8664
    }
}

// MARK: - Valve's client manifest

/// Valve's public manifest of the Windows client (`steam_client_win64` on the update host): the packages of the current client, each
/// with its file name, size and SHA-256. The format is Valve's KeyValues text:
///
///     "win64"
///     {
///         "version"   "1794536281"
///         "bins_win64"
///         {
///             "file"    "bins_win64.zip.<sha1>"      "size"  "64658652"   "sha2"   "<sha256>"
///             "zipvz"   "bins_win64.zip.vz.<sha1>_<size>"                 "sha2vz" "<sha256>"
///         }
///         "steam_win64"
///         {
///             "steamrow" { "file" "steam_win64_steamrow.zip.<sha1>" ... "IsBootstrapperPackage" "1" }
///             ...
///         }
///     }
///
/// Unlike the Dock's client this is not pinned: Valve's client updates itself at its first start anyway, so a pinned copy would only
/// be older. What is checked instead: the manifest comes from Valve's host over HTTPS, and every file BCD keeps has the size and the
/// SHA-256 this manifest names. A manifest BCD cannot read in full is refused; nothing is guessed.
enum SteamDesktopManifest {
    static let fileName = "steam_client_win64"
    static let platform = "win64"
    /// The variant of the bootstrapper package for Steam outside China, the one Valve's updater takes there.
    static let realm = "steamrow"
    static let maxBytes = 256 * 1024
    static let maxPackageBytes: Int64 = 1 << 30
    static let maxPackages = 64

    struct File: Equatable, Sendable {
        let name: String
        let bytes: Int64
        let sha256: String
    }

    struct Package: Equatable, Sendable {
        /// The manifest's own name for it: "bins_win64".
        let name: String
        /// The ZIP archive.
        let zip: File
        /// The same archive in Valve's compressed form, when the manifest has one.
        let compressed: File?
        /// The package that holds steam.exe.
        let bootstrapper: Bool
        /// What Valve's own updater downloads and looks for in its package folder: the compressed form when there is one.
        var download: File { compressed ?? zip }
    }

    struct Manifest: Equatable, Sendable {
        let version: String
        let packages: [Package]
        var bootstrapper: Package { packages.first { $0.bootstrapper }! }
        var downloadBytes: Int64 { packages.reduce(0) { $0 + $1.download.bytes } }
    }

    enum Failure: Error, Equatable {
        case malformed(String)
    }

    private indirect enum Node {
        case value(String)
        case block([(key: String, node: Node)])
    }

    private struct Reader {
        let bytes: [UInt8]
        var at = 0
        var nodes = 0

        mutating func skipBlank() {
            while at < bytes.count {
                let c = bytes[at]
                if c == 0x20 || c == 0x09 || c == 0x0d || c == 0x0a { at += 1 }
                else if c == 0x2f, at + 1 < bytes.count, bytes[at + 1] == 0x2f { while at < bytes.count, bytes[at] != 0x0a { at += 1 } }
                else { break }
            }
        }

        /// A quoted string; the reader stands on its opening quote.
        mutating func string() throws -> String {
            guard at < bytes.count, bytes[at] == 0x22 else { throw Failure.malformed("a name or value without quotes") }
            at += 1
            var out: [UInt8] = []
            while at < bytes.count, bytes[at] != 0x22 {
                var c = bytes[at]
                if c == 0x5c {
                    at += 1
                    guard at < bytes.count else { break }
                    c = bytes[at] == 0x6e ? 0x0a : bytes[at] == 0x74 ? 0x09 : bytes[at]
                }
                out.append(c)
                at += 1
                guard out.count <= 512 else { throw Failure.malformed("a name or value longer than 512 bytes") }
            }
            guard at < bytes.count else { throw Failure.malformed("a quote that never closes") }
            at += 1
            guard let text = String(bytes: out, encoding: .utf8) else { throw Failure.malformed("text that is not UTF-8") }
            return text
        }

        /// The pairs up to the closing brace (`top`: up to the end of the text).
        mutating func pairs(depth: Int, top: Bool) throws -> [(key: String, node: Node)] {
            guard depth <= 6 else { throw Failure.malformed("blocks nested too deep") }
            var out: [(key: String, node: Node)] = []
            while true {
                skipBlank()
                if at >= bytes.count {
                    guard top else { throw Failure.malformed("a block that never closes") }
                    return out
                }
                if bytes[at] == 0x7d {
                    guard !top else { throw Failure.malformed("a closing brace without a block") }
                    at += 1
                    return out
                }
                let key = try string()
                skipBlank()
                guard at < bytes.count else { throw Failure.malformed("a name without a value") }
                nodes += 1
                guard nodes <= 4096 else { throw Failure.malformed("too many entries") }
                if bytes[at] == 0x7b {
                    at += 1
                    out.append((key, .block(try pairs(depth: depth + 1, top: false))))
                } else {
                    out.append((key, .value(try string())))
                }
            }
        }
    }

    private static func isHex(_ text: Substring, _ count: Int) -> Bool {
        text.utf8.count == count && text.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x61 && $0 <= 0x66) }
    }

    private static func isDigits(_ text: Substring, max: Int) -> Bool {
        !text.isEmpty && text.utf8.count <= max && text.utf8.allSatisfy { $0 >= 0x30 && $0 <= 0x39 }
    }

    private static func isName(_ text: String) -> Bool {
        !text.isEmpty && text.utf8.count <= 64
            && text.utf8.allSatisfy { ($0 >= 0x61 && $0 <= 0x7a) || ($0 >= 0x30 && $0 <= 0x39) || $0 == 0x5f }
    }

    /// The plain values of a block, by name; a name written twice makes the block unreadable.
    private static func values(_ pairs: [(key: String, node: Node)], of package: String) throws -> [String: String] {
        var out: [String: String] = [:]
        for (key, node) in pairs {
            guard case .value(let text) = node else { continue }
            guard out.updateValue(text, forKey: key) == nil else { throw Failure.malformed("\(package): \(key) is there twice") }
        }
        return out
    }

    /// One package from its values: nil for a block that names no file (not a package); an error for one that names a file BCD
    /// cannot take as it is written.
    private static func package(_ name: String, stem: String, _ fields: [String: String]) throws -> Package? {
        guard let file = fields["file"] else { return nil }
        let zipPrefix = stem + ".zip."
        guard file.hasPrefix(zipPrefix), isHex(file.dropFirst(zipPrefix.count), 40) else {
            throw Failure.malformed("\(name): the file name is not \(stem).zip.<40 hex digits>")
        }
        guard let sizeText = fields["size"], isDigits(Substring(sizeText), max: 12), let size = Int64(sizeText),
              size > 0, size <= maxPackageBytes else { throw Failure.malformed("\(name): no usable size") }
        guard let sha = fields["sha2"], isHex(Substring(sha), 64) else { throw Failure.malformed("\(name): no SHA-256") }
        var compressed: File?
        if let vz = fields["zipvz"] {
            // "<stem>.zip.vz.<sha1>_<size>": Valve's updater reads the size out of the name.
            let vzPrefix = stem + ".zip.vz."
            let rest = vz.dropFirst(vzPrefix.count)
            guard vz.hasPrefix(vzPrefix), let bar = rest.firstIndex(of: "_"), isHex(rest[..<bar], 40),
                  isDigits(rest[rest.index(after: bar)...], max: 12), let vzSize = Int64(rest[rest.index(after: bar)...]),
                  vzSize > 0, vzSize <= maxPackageBytes else {
                throw Failure.malformed("\(name): the compressed file name is not \(stem).zip.vz.<40 hex digits>_<size>")
            }
            guard let vzSha = fields["sha2vz"], isHex(Substring(vzSha), 64) else {
                throw Failure.malformed("\(name): a compressed file without its SHA-256")
            }
            compressed = File(name: vz, bytes: vzSize, sha256: vzSha)
        }
        return Package(name: name, zip: File(name: file, bytes: size, sha256: sha), compressed: compressed,
                       bootstrapper: fields["IsBootstrapperPackage"] == "1")
    }

    /// The manifest's packages, or the reason it cannot be used.
    static func parse(_ data: Data) throws -> Manifest {
        guard !data.isEmpty, data.count <= maxBytes else { throw Failure.malformed("not the size of a manifest") }
        var reader = Reader(bytes: [UInt8](data))
        let top = try reader.pairs(depth: 0, top: true)
        let found = top.filter { $0.key == platform }
        guard found.count == 1, case .block(let entries) = found[0].node else {
            throw Failure.malformed("no \"\(platform)\" block")
        }
        guard let version = try values(entries, of: platform)["version"], isDigits(Substring(version), max: 20) else {
            throw Failure.malformed("no version")
        }
        var packages: [Package] = []
        for (name, node) in entries {
            guard case .block(let own) = node else { continue }
            guard isName(name) else { throw Failure.malformed("a package name BCD does not take: \(name.prefix(40))") }
            // The bootstrapper comes in one variant per realm, each a block of its own inside the package's.
            let variant = own.filter { $0.key == realm }
            var read: Package?
            if variant.count == 1, case .block(let inner) = variant[0].node {
                read = try package(name, stem: name + "_" + realm, values(inner, of: name))
            } else if variant.isEmpty {
                read = try package(name, stem: name, values(own, of: name))
            } else {
                throw Failure.malformed("\(name): \(realm) is not one block")
            }
            if let read { packages.append(read) }
        }
        guard !packages.isEmpty, packages.count <= maxPackages else { throw Failure.malformed("no packages, or too many") }
        guard packages.filter(\.bootstrapper).count == 1 else { throw Failure.malformed("not exactly one package with steam.exe") }
        let names = packages.flatMap { [$0.zip.name.lowercased()] + ($0.compressed.map { [$0.name.lowercased()] } ?? []) }
        guard Set(names).count == names.count, Set(packages.map(\.name)).count == packages.count else {
            throw Failure.malformed("two packages with one name")
        }
        guard packages.reduce(Int64(0), { $0 + $1.zip.bytes }) <= 4 * maxPackageBytes else { throw Failure.malformed("too large in all") }
        return Manifest(version: version, packages: packages)
    }

    /// The version of a manifest file on disk (Valve's updater keeps the installed one in the package folder), or nil.
    static func version(ofFile url: URL) -> String? {
        guard let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize, size > 0, size <= maxBytes,
              let data = try? Data(contentsOf: url) else { return nil }
        return (try? parse(data))?.version
    }
}

#if canImport(CryptoKit)
// MARK: - Where the files come from

/// What the installer asks of the network: Valve's update host in the app (SteamDesktopCDN), a folder of files in the host check.
protocol SteamDesktopTransport: Sendable {
    /// A small file, whole; an error when it is longer than `limit`.
    func data(_ name: String, limit: Int) async throws -> Data
    /// The file `name` of `expected` bytes into `file`, which already holds its first `offset` bytes; `progress` gets the bytes the
    /// file holds so far. A download that stops early leaves what it got in `file`.
    func download(_ name: String, to file: URL, from offset: Int64, expected: Int64,
                  progress: @escaping @Sendable (Int64) -> Void) async throws
}

// MARK: - The install

/// Puts Valve's client into `SteamDesktop.relativeRoot` while no session runs:
///  1. the manifest, read from Valve's host;
///  2. the bootstrapper package (the one with steam.exe, about 3 MB): its size and SHA-256 checked, unpacked with the app's own ZIP
///     reader (SteamRuntimeFiles.unpack), steam.exe looked at;
///  3. with `packages`, every package of the manifest into the client's package folder, under the name the manifest gives it and in
///     the compressed form where the manifest has one (the form Valve's updater downloads), each checked the same way. An
///     interrupted download goes on where it stopped (the unfinished file is `<name>.bcd-part` there); a file already there with the
///     right size and sum is kept;
///  4. steam.exe into the folder, last, so a folder with steam.exe is a finished install.
/// steam.exe then brings the client up to date by itself at its first start, in the session. With the packages in place it should
/// have nothing left to download, only to check and unpack; without them it downloads them itself, inside the emulator.
///
/// Why BCD fetches the packages (the default): Valve's updater would download the same files, about 260 MB, from the same host in
/// the session, where every byte is checked by emulated code, the app has to stay in front, and an interruption starts a file over.
/// Here the download is native, can be cancelled and goes on later. That the updater takes the files as its own is read from its
/// strings (it keeps packages there under these names, checks size and SHA-256, has an option to force a download, deletes package
/// files the manifest no longer names) and is NOT yet seen on a device: the session's "[steam-desktop] updater:" lines say. A file
/// it does not want costs a second download, never a wrong install.
/// Nothing in the Dock's folder is read or written.
enum SteamDesktopInstaller {
    enum Step: String, Sendable { case manifest, bootstrapper, packages, installing }

    struct Progress: Equatable, Sendable {
        var step: Step
        var received: Int64 = 0
        var total: Int64 = 0
    }

    struct Outcome: Equatable, Sendable {
        /// The manifest's version.
        let version: String
        let programBytes: Int64
        /// Packages now in the package folder, the ones among them that were already there, and the bytes fetched for the others.
        let packages: Int
        let kept: Int
        let fetchedBytes: Int64
    }

    enum Failure: LocalizedError, Equatable {
        case activeSession
        case alreadyInstalled
        case manifest(String)
        case download(String)
        case verification(String)
        case notSteam
        case conflict(String)

        /// A short word for the log.
        var logReason: String {
            switch self {
            case .activeSession: return "session-running"
            case .alreadyInstalled: return "already-installed"
            case .manifest(let why): return "manifest (\(why))"
            case .download(let what): return "download (\(what))"
            case .verification(let what): return "verification (\(what))"
            case .notSteam: return "not-steam"
            case .conflict(let what): return "conflict (\(what))"
            }
        }

        var errorDescription: String? {
            switch self {
            case .activeSession: return "A session is still running. Close it, then install Steam for Windows."
            case .alreadyInstalled: return "Steam for Windows is already installed."
            case .manifest: return "BCD could not read Valve's list of client files, so nothing was installed. Try again in a little while."
            case .download: return "The download from Valve's update server did not finish. Check the connection and try again: BCD goes on where it stopped."
            case .verification: return "A downloaded file did not pass verification, so it was not kept. Try again."
            case .notSteam: return "What came back is not Valve's steam.exe, so it was not kept. Try again in a little while."
            case .conflict: return "Files already in the Steam for Windows folder conflict with the install, so BCD left them alone."
            }
        }
    }

    /// The end of BCD's own unfinished files in the package folder.
    static let partSuffix = ".bcd-part"

    static func install(drive: URL, transport: SteamDesktopTransport, packages: Bool,
                        sessionRunning: @escaping @Sendable () -> Bool,
                        log: @escaping @Sendable (String) -> Void,
                        progress: @escaping @Sendable (Progress) -> Void) async throws -> Outcome {
        guard !sessionRunning() else { throw Failure.activeSession }
        guard !SteamDesktop.isDockPath(relative: SteamDesktop.relativeRoot) else { throw Failure.conflict("folder") }
        let fm = FileManager.default
        /// A place inside the client's folder: no link on the way, no second spelling of a folder (SteamRuntimeFiles.destination).
        func place(_ relative: String) throws -> URL {
            do { return try SteamRuntimeFiles.destination(SteamDesktop.relativeRoot + "/" + relative, under: drive) }
            catch { throw Failure.conflict(relative) }
        }
        func size(_ url: URL) -> Int64? {
            ((try? fm.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.int64Value
        }
        let installed = try place(SteamDesktop.program)
        guard !fm.fileExists(atPath: installed.path) else { throw Failure.alreadyInstalled }

        progress(Progress(step: .manifest))
        let listing = try await transport.data(SteamDesktopManifest.fileName, limit: SteamDesktopManifest.maxBytes)
        let manifest: SteamDesktopManifest.Manifest
        do {
            manifest = try SteamDesktopManifest.parse(listing)
        } catch SteamDesktopManifest.Failure.malformed(let why) {
            throw Failure.manifest(why)
        }
        let boot = manifest.bootstrapper.zip
        log("[steam-desktop] install: manifest version=\(manifest.version) packages=\(manifest.packages.count) "
            + "download=\(manifest.downloadBytes >> 20)MB bootstrapper=\(manifest.bootstrapper.name) bytes=\(boot.bytes)")
        // The app's ZIP reader takes an archive of at most 64 MiB; the bootstrapper is about 3 MB.
        guard boot.bytes <= 64 * 1024 * 1024 else { throw Failure.manifest("the package with steam.exe is larger than the reader takes") }

        let stage = fm.temporaryDirectory.appendingPathComponent("bcd-steam-desktop-" + UUID().uuidString, isDirectory: true)
        try fm.createDirectory(at: stage, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: stage) }

        // The package with steam.exe.
        try Task.checkCancellation()
        progress(Progress(step: .bootstrapper, received: 0, total: boot.bytes))
        let archiveURL = stage.appendingPathComponent("bootstrapper.zip")
        try await transport.download(boot.name, to: archiveURL, from: 0, expected: boot.bytes) { got in
            progress(Progress(step: .bootstrapper, received: got, total: boot.bytes))
        }
        guard size(archiveURL) == boot.bytes else { throw Failure.download(boot.name) }
        let archive = try Data(contentsOf: archiveURL, options: .mappedIfSafe)
        guard hash(archive) == boot.sha256 else { throw Failure.verification(boot.name) }
        let unpacked = stage.appendingPathComponent("files", isDirectory: true)
        var names: [String] = []
        do {
            try SteamRuntimeFiles.unpack(archive) { name, bytes in
                let file = unpacked.appendingPathComponent(name)
                try fm.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
                try bytes.write(to: file, options: .atomic)
                names.append(name)
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw Failure.verification(boot.name)
        }
        guard let program = names.first(where: { $0.caseInsensitiveCompare(SteamDesktop.program) == .orderedSame }),
              let programData = try? Data(contentsOf: unpacked.appendingPathComponent(program), options: .mappedIfSafe),
              SteamDesktop.isWindowsProgram64(programData) else { throw Failure.notSteam }
        let programBytes = Int64(programData.count)

        // Valve's packages, for its own updater.
        var kept = 0, fetched: Int64 = 0, inPlace = 0
        if packages {
            let folder = try place(SteamDesktop.packageFolder)
            try fm.createDirectory(at: folder, withIntermediateDirectories: true)
            // BCD's own unfinished files of an older manifest go; nothing else in the folder is touched.
            let wanted = Set(manifest.packages.map { $0.download.name + partSuffix })
            for name in (try? fm.contentsOfDirectory(atPath: folder.path)) ?? [] where name.hasSuffix(partSuffix) && !wanted.contains(name) {
                try? fm.removeItem(at: folder.appendingPathComponent(name))
            }
            let total = manifest.downloadBytes
            var done: Int64 = 0
            for package in manifest.packages {
                try Task.checkCancellation()
                guard !sessionRunning() else { throw Failure.activeSession }
                let file = package.download
                let target = try place(SteamDesktop.packageFolder + "/" + file.name)
                let part = try place(SteamDesktop.packageFolder + "/" + file.name + partSuffix)
                if size(target) == file.bytes, SteamRuntimeFiles.sha256(ofFile: target) == file.sha256 {
                    try? fm.removeItem(at: part)
                    kept += 1; inPlace += 1; done += file.bytes
                    progress(Progress(step: .packages, received: done, total: total))
                    continue
                }
                // A file of this name that is not the manifest's file is of no use to Valve's updater either.
                if fm.fileExists(atPath: target.path) { try fm.removeItem(at: target) }
                var have = size(part) ?? 0
                if have > file.bytes { try? fm.removeItem(at: part); have = 0 }
                if have < file.bytes {
                    let before = done
                    try await transport.download(file.name, to: part, from: have, expected: file.bytes) { got in
                        progress(Progress(step: .packages, received: before + got, total: total))
                    }
                }
                guard let now = size(part), now == file.bytes else {
                    if (size(part) ?? 0) > file.bytes { try? fm.removeItem(at: part) }
                    throw Failure.download(file.name)
                }
                guard SteamRuntimeFiles.sha256(ofFile: part) == file.sha256 else {
                    try? fm.removeItem(at: part)
                    throw Failure.verification(file.name)
                }
                try fm.moveItem(at: part, to: target)
                fetched += file.bytes - have
                inPlace += 1; done += file.bytes
                log("[steam-desktop] install: package \(package.name) fetched bytes=\(file.bytes)" + (have > 0 ? " resumed-at=\(have)" : ""))
                progress(Progress(step: .packages, received: done, total: total))
            }
        }

        // steam.exe last: a folder with steam.exe is a finished install. No suspension from here on.
        try Task.checkCancellation()
        guard !sessionRunning() else { throw Failure.activeSession }
        progress(Progress(step: .installing))
        for name in names.sorted(by: { a, b in a == program ? false : (b == program ? true : a < b) }) {
            let target = try place(name)
            let staged = unpacked.appendingPathComponent(name)
            if fm.fileExists(atPath: target.path) {
                guard fm.contentsEqual(atPath: target.path, andPath: staged.path) else { throw Failure.conflict(name) }
                continue
            }
            try fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try fm.moveItem(at: staged, to: target)
        }
        log("[steam-desktop] install: steam.exe in place bytes=\(programBytes) packages=\(inPlace) kept=\(kept) fetched=\(fetched >> 20)MB "
            + "(the client updates itself at its first start)")
        return Outcome(version: manifest.version, programBytes: programBytes, packages: inPlace, kept: kept, fetchedBytes: fetched)
    }

    private static func hash(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - Valve's update host

/// The files' one source: Valve's client update host over HTTPS, the host BCD Dock's packages come from (SteamRuntimeFiles.origin).
/// A redirect is followed only to the same host over HTTPS; an answer from anywhere else is refused. A package is written to its file
/// as it arrives, and a download that goes on asks for the rest only (a Range request): the server's answer has to start at exactly
/// that byte and name the size the manifest names, or the file starts again from nothing.
final class SteamDesktopCDN: NSObject, SteamDesktopTransport, URLSessionDataDelegate, @unchecked Sendable {
    struct Policy: Equatable, Sendable {
        let scheme: String
        let host: String
        /// nil: the scheme's own port.
        let port: Int?
        /// The address every file name is appended to.
        let base: String

        /// Valve's host. The app uses no other policy; the host check serves its fixtures from this Mac.
        static let valve = Policy(scheme: "https", host: "client-update.akamai.steamstatic.com", port: nil, base: SteamRuntimeFiles.origin)

        func allows(_ url: URL?) -> Bool {
            guard let url, url.scheme == scheme, url.host == host, url.user == nil, url.password == nil else { return false }
            if let port { return url.port == port }
            return url.port == nil || url.port == (scheme == "https" ? 443 : 80)
        }
    }

    private final class Job {
        var memory = Data()
        var limit = 0
        var handle: FileHandle?
        var offset: Int64 = 0
        var expected: Int64 = 0
        var written: Int64 = 0
        var failure: Error?
        var progress: (@Sendable (Int64) -> Void)?
        var continuation: CheckedContinuation<Void, Error>?
    }

    let policy: Policy
    private let lock = NSLock()
    private var jobs: [Int: Job] = [:]
    private var session: URLSession!

    init(policy: Policy = .valve) {
        self.policy = policy
        super.init()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 3600
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }

    /// The session holds this object until it is closed.
    func close() { session.invalidateAndCancel() }

    private func makeRequest(_ name: String) throws -> URLRequest {
        // A file name of the manifest, or the manifest's own: letters, digits, "." "_" "-" only.
        guard !name.isEmpty, name.utf8.count <= 200,
              name.utf8.allSatisfy({ ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x5a) || ($0 >= 0x61 && $0 <= 0x7a) || $0 == 0x2e || $0 == 0x5f || $0 == 0x2d }),
              !name.hasPrefix("."), let url = URL(string: policy.base + name), policy.allows(url) else {
            throw SteamDesktopInstaller.Failure.download(name)
        }
        var request = URLRequest(url: url)
        request.httpShouldHandleCookies = false
        // The bytes as they are on the host: a size, a range and a SHA-256 mean nothing for a re-coded body.
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        return request
    }

    /// The job a task's callbacks belong to, with the continuation its end resumes; then the task starts.
    private func start(_ task: URLSessionTask, _ job: Job, _ continuation: CheckedContinuation<Void, Error>) {
        lock.lock()
        job.continuation = continuation
        jobs[task.taskIdentifier] = job
        lock.unlock()
        task.resume()
    }

    private func run(_ job: Job, _ request: URLRequest) async throws {
        try Task.checkCancellation()
        let task = session.dataTask(with: request)
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                start(task, job, continuation)
            }
        } onCancel: {
            task.cancel()
        }
    }

    func data(_ name: String, limit: Int) async throws -> Data {
        let job = Job()
        job.limit = limit
        try await run(job, try makeRequest(name))
        return job.memory
    }

    func download(_ name: String, to file: URL, from offset: Int64, expected: Int64,
                  progress: @escaping @Sendable (Int64) -> Void) async throws {
        let fm = FileManager.default
        if !fm.fileExists(atPath: file.path) { guard fm.createFile(atPath: file.path, contents: nil) else { throw SteamDesktopInstaller.Failure.download(name) } }
        let handle = try FileHandle(forWritingTo: file)
        defer { try? handle.close() }
        // The file holds exactly its first `offset` bytes before anything is asked for (never more than it really has).
        let length = try handle.seekToEnd()
        let start = min(UInt64(max(0, offset)), length)
        if length != start { try handle.truncate(atOffset: start); try handle.seekToEnd() }
        let job = Job()
        job.handle = handle
        job.offset = Int64(start)
        job.expected = expected
        job.progress = progress
        var request = try makeRequest(name)
        if job.offset > 0 { request.setValue("bytes=\(job.offset)-", forHTTPHeaderField: "Range") }
        try await run(job, request)
        try handle.synchronize()
    }

    private func job(_ task: URLSessionTask) -> Job? {
        lock.lock(); defer { lock.unlock() }
        return jobs[task.taskIdentifier]
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(policy.allows(request.url) ? request : nil)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let job = job(dataTask) else { completionHandler(.cancel); return }
        let name = dataTask.originalRequest?.url?.lastPathComponent ?? "?"
        func refuse() { job.failure = SteamDesktopInstaller.Failure.download(name); completionHandler(.cancel) }
        guard let http = response as? HTTPURLResponse, policy.allows(http.url) else { refuse(); return }
        guard let handle = job.handle else {
            guard http.statusCode == 200, http.expectedContentLength <= Int64(job.limit) else { refuse(); return }
            completionHandler(.allow)
            return
        }
        switch http.statusCode {
        case 200:
            // The whole file: what the file held of it is of no use (the server did not take the range).
            guard http.expectedContentLength < 0 || http.expectedContentLength == job.expected else { refuse(); return }
            if job.offset > 0 {
                do { try handle.truncate(atOffset: 0); try handle.seekToEnd() } catch { refuse(); return }
                job.offset = 0
            }
            completionHandler(.allow)
        case 206:
            // "bytes <first>-<last>/<size>": exactly the rest of the file the manifest names.
            let range = (http.value(forHTTPHeaderField: "Content-Range") ?? "").lowercased()
            guard job.offset > 0, range == "bytes \(job.offset)-\(job.expected - 1)/\(job.expected)" else {
                try? handle.truncate(atOffset: 0)
                refuse(); return
            }
            completionHandler(.allow)
        default:
            // 416 and the like: this file's next try starts from nothing.
            if http.statusCode == 416 { try? handle.truncate(atOffset: 0) }
            refuse()
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let job = job(dataTask), job.failure == nil else { return }
        let name = dataTask.originalRequest?.url?.lastPathComponent ?? "?"
        guard let handle = job.handle else {
            job.memory.append(data)
            if job.memory.count > job.limit { job.failure = SteamDesktopInstaller.Failure.download(name); dataTask.cancel() }
            return
        }
        guard job.offset + job.written + Int64(data.count) <= job.expected else {
            job.failure = SteamDesktopInstaller.Failure.download(name)
            dataTask.cancel()
            return
        }
        do { try handle.write(contentsOf: data) } catch {
            job.failure = SteamDesktopInstaller.Failure.download(name)
            dataTask.cancel()
            return
        }
        job.written += Int64(data.count)
        job.progress?(job.offset + job.written)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        let job = jobs.removeValue(forKey: task.taskIdentifier)
        let continuation = job?.continuation
        job?.continuation = nil
        lock.unlock()
        guard let job, let continuation else { return }
        let name = task.originalRequest?.url?.lastPathComponent ?? "?"
        if let failure = job.failure { continuation.resume(throwing: failure) }
        else if let error = error as? URLError, error.code == .cancelled { continuation.resume(throwing: CancellationError()) }
        else if error != nil { continuation.resume(throwing: SteamDesktopInstaller.Failure.download(name)) }
        else { continuation.resume() }
    }
}

#endif

// MARK: - The session's settings

/// The settings of a Steam for Windows session (plan §6 "T0: session" and §11's measurement list), set for that session alone:
///
///   pool = 1152, pool-split = 1, pool-low = 1
///       The JIT pool at its largest, split round the main thread's stack, with the run below the executable window for the
///       emulator's code buffers. Steam's processes filled a 592 MB pool and its web helper then restarted in a loop (77VQJK).
///       `pool` in a game's own lines counts only for a game that sets pool-above-window; in this session it counts by itself
///       (ContentView.runWineFullSequence), and pool-above-window keeps its default.
///   swap-mb = 2048, swap-mode = 2, swap-images = 1
///       The swap file, broad coverage, and the sections of pure-x64 images in it.
///   avail-phys = 1, avail-phys-cef-mb = 900
///       Windows programs are told the memory left before the app's limit, and Steam's web helper at most 900 MB of it: Chromium's
///       moderate memory pressure, which trims its caches every 10 s.
///   env.MADEIRA_X64_IMAGE_NOCOPY = 1
///       Pure-x64 images (libcef.dll is about 240 MB) run without a copy in the JIT pool.
///   env.MADEIRA_SC_PA_POOLS = 2, env.MADEIRA_CEF_POOL_IMAGES = steamwebhelper.exe
///       The 64-bit web helper's four PartitionAlloc pools get the address band's four slots. For steamwebhelper.exe the list adds
///       nothing to its command line that the engine's own rule for it (process_ios.c, "task #34") has not added already: that rule
///       runs first, refuses every --type= child and appends --single-process, so the list's rewrite finds the switch there and
///       only writes PartitionAllocBackupRefPtr into the --disable-features list a second time.
///   env.MADEIRA_BG_QOS_PROCS = steam.exe;steamwebhelper.exe;steamservice.exe
///       Steam's processes run at utility priority while a game presents.
///   env.MADEIRA_GDI_SHARED_SECTION = 1
///       Not in the plan's list; added when the keys were checked against the engine. The session starts 64-bit (explorer, then the
///       64-bit steam.exe), and win32u decides once, when the session's first program starts, whether the GDI handle table is a
///       section a 32-bit program can map (wine dlls/win32u/gdiobj.c gdi_shared_use_section: only in a session armed for 32-bit code,
///       or with this line). Steam then starts 32-bit programs, its own service and every 32-bit game, and without the section their
///       gdi32 faults on its first GDI handle. BCD Dock's sessions and the Windows desktop set the same for the same reason
///       (ContentView.startDock, DesktopGDISection).
///   env.MADEIRA_WOW_MIN_FREE_GB = 2
///       Build 690, 2026-11-21 21:57 (iPhone18,2, a 512 GB map): Steam started Breakneck, a 32-bit game, three minutes into the
///       session. The map then had 6797 MB of free address space, and a 32-bit window needs 4096 MB plus 8 GB left over: "refused:
///       map too small", "BOOT FAILED at stage 'guest-window-reserve'", twice. The 32-bit SteamService.exe was refused the same way
///       in build 689 (6533 MB free). With this line a window leaves 2 GB free instead of 8, so 6144 MB are enough. It is the EA
///       app's line (BCDLaunchers), and the EA app runs the same 64-bit Chromium layout (MADEIRA_SC_PA_POOLS = 2) with 32-bit
///       children.
///
///       Build 692 showed the floor alone is not enough (23:05). The map rule passed, but no 4 GB-aligned slot was free:
///       0x7100000000 and 0x7200000000 had 64-bit views at their base, 0x7300000000 ends in the web helper's second pool, and
///       0x74-0x7a00000000 is the helper's pools. Nothing but a slot held from the start can give a game its window here.
///   env.MADEIRA_WOW_PLACEHOLDER_SLOTS = 1, env.MADEIRA_WOW_HOLD_FOR = C:\Program Files (x86)\Steam Desktop\steamapps\common\,
///   env.MADEIRA_WOW_HIGH_SIDE_CAP = 1
///       One 4 GB window held from the session start for the client's own games (the 32-bit SteamService.exe cannot take it),
///       with the engine's cap that build 691 lacked (virtual_ios.c ios_wow_high_side_cap):
///       - **691 without the cap (22:32, two runs), Steam never opened.** Layout 2's floor is the held slot's own base, so the
///         band below it was empty: every furniture view first failed a full scan there. The band above reached the top of the
///         map, and steam.exe's top-down RWX page landed at 0x7bffff0000, the start of the web helper's fourth 16 GB pool. The
///         pool could not be reserved ("[cef-one] second BackupRefPtr pool ... could not be reserved"), the helper stopped on
///         a breakpoint (0x80000003), and Steam restarted it every 10 s.
///       - **With the cap,** furniture above the held slot keeps the furniture ceiling (0x73ffff0000), so the 64-bit programs
///         have [0x7200004000, 0x73ffff0000), and that band is searched first when the band below has no room.
///       The three lines only go together: check-steam-desktop fails on a placeholder line without the cap.
///
/// How they reach the session: BCD writes them, as one block, above the game's own lines in the file this session's settings are read
/// from (`sessionFile`, exported as MADEIRA_CFG_GAME for the session only). A game file is read after bcd.cfg and its last line for a
/// key wins, so a line of the player's own for the same key, in the entry's own settings, wins over BCD's, and BCD's wins over
/// bcd.cfg's. Nothing is written to bcd.cfg or to the entry: no other game's start sees any of it.
enum SteamDesktopProfile {
    static let lines: [String] = [
        "pool = 1152",
        "pool-split = 1",
        "pool-low = 1",
        "swap-mb = 2048",
        "swap-mode = 2",
        "swap-images = 1",
        "avail-phys = 1",
        "avail-phys-cef-mb = 900",
        "env.MADEIRA_X64_IMAGE_NOCOPY = 1",
        "env.MADEIRA_SC_PA_POOLS = 2",
        "env.MADEIRA_CEF_POOL_IMAGES = steamwebhelper.exe",
        "env.MADEIRA_BG_QOS_PROCS = steam.exe;steamwebhelper.exe;steamservice.exe",
        "env.MADEIRA_GDI_SHARED_SECTION = 1",
        "env.MADEIRA_WOW_MIN_FREE_GB = 2",
        "env.MADEIRA_WOW_PLACEHOLDER_SLOTS = 1",
        "env.MADEIRA_WOW_HOLD_FOR = " + SteamDesktop.windowsRoot + "\\steamapps\\common\\",
        "env.MADEIRA_WOW_HIGH_SIDE_CAP = 1",
    ]

    static let fileName = "bcd-steam-desktop.cfg"
    static let opening = "# BCD's settings for a Steam for Windows session (docs/STEAM_DESKTOP.md); written again at every start"
    static let ownOpening = "# this entry's own settings, after BCD's so that they win"

    /// "key = value" lines as pairs, comments and blank lines left out; the last line of a key wins where they are looked up.
    static func pairs(_ text: String) -> [(key: String, value: String)] {
        let text = text.hasPrefix("\u{FEFF}") ? String(text.dropFirst()) : text
        return text.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" }).compactMap { raw in
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { return nil }
            let key = line[..<eq].trimmingCharacters(in: .whitespaces)
            if key.isEmpty { return nil }
            return (key: key, value: line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces))
        }
    }

    static var keys: [String] { pairs(lines.joined(separator: "\n")).map(\.key) }

    /// The file a session reads: BCD's block, then `own` (the text of the file the launch had chosen for the game: the entry's own
    /// settings, with auto-tune's lines when it adds any), so every line of `own` comes later and wins.
    static func sessionFile(own: String?) -> String {
        var text = opening + "\n" + lines.joined(separator: "\n") + "\n"
        if let own, !own.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            text += ownOpening + "\n" + (own.hasSuffix("\n") ? own : own + "\n")
        }
        return text
    }

    /// What a session file sets for BCD's keys, and which of them the entry's own lines changed: the log's
    /// "[steam-desktop] settings in effect" line.
    static func effective(sessionFile text: String) -> (values: [(key: String, value: String)], changed: [String]) {
        var last: [String: String] = [:]
        for (key, value) in pairs(text) { last[key] = value }
        let defaults = pairs(lines.joined(separator: "\n"))
        let values = defaults.map { (key: $0.key, value: last[$0.key] ?? "") }
        let changed = zip(defaults, values).filter { $0.0.value != $0.1.value }.map(\.0.key)
        return (values, changed)
    }

    static func effectiveLine(sessionFile text: String) -> String {
        let now = effective(sessionFile: text)
        return "[steam-desktop] settings in effect: " + now.values.map { "\($0.key)=\($0.value)" }.joined(separator: " ")
            + (now.changed.isEmpty ? " (all BCD's)" : " (the entry's own lines set: \(now.changed.joined(separator: ", ")))")
    }
}

// MARK: - BCD Dock's games in the client's library

/// Branch steam-client (2026-11-22; owner: "BCD Dock ile oyun paylaşımını aç xplane 12 zaten yüklü onu görsün"). Valve's client lists
/// only the library folders in its own `steamapps\libraryfolders.vdf`, so a game BCD Dock installed (X-Plane 12 in
/// `C:\Program Files (x86)\Steam\steamapps`) showed as not installed. Before each session BCD adds to that list BCD Dock's library
/// and the Steam libraries on BCD's external drives (the folders BCD Dock's own list has, ExternalDrives.listInSteamClient). Steam
/// then reads their `appmanifest_*.acf` files itself and shows those games installed: one copy of each game on the device, for
/// both. Plan §7.5.
///
/// What it changes, and what it does not:
///  - Only the client's own list is written (ExternalSteamLibraries: Valve's entries and fields kept; a path already listed is not
///    added twice). Nothing in BCD Dock's folders is written by BCD.
///  - The client itself may write into those libraries: its own library marker and the games' appmanifest files (it can update or
///    verify a game there). BCD keeps one copy of BCD Dock's appmanifest and library files from before the first shared session
///    (`backupFolder`), to put back by hand.
///  - The switch (`shareKey`, on unless cleared) off: the next session's list has those folders taken out again.
enum SteamDesktopLibraries {
    static let shareKey = "bcd.steamDesktop.shareDock"
    static var sharesDock: Bool { UserDefaults.standard.object(forKey: shareKey) as? Bool ?? true }
    /// The client's list, relative to drive_c.
    static var listRelative: String { SteamDesktop.relativeRoot + "/steamapps/libraryfolders.vdf" }
    /// Where the copy of BCD Dock's library files from before the first shared session goes, relative to drive_c.
    static var backupFolder: String { SteamDesktop.relativeRoot + "/bcd-backup/dock-library" }

    /// BCD Dock's libraries, drive_c relative: its own folder and the drives' (`drives`: "External/<name>/SteamLibrary").
    static func dockLibraries(drives: [String]) -> [String] {
        [SteamRuntimeFiles.relativeRoot] + drives.filter { !$0.isEmpty && $0.lowercased() != SteamRuntimeFiles.relativeRoot.lowercased() }
    }

    /// The client's list for the next session: with BCD Dock's libraries (`share`), or without them. The client's own folder is
    /// always listed first when BCD makes the file. nil: the file needs no change.
    static func list(_ data: Data?, share: Bool, drives: [String]) throws -> Data? {
        let dock = dockLibraries(drives: drives)
        let listed = try listedPaths(data)
        if share {
            let wanted = [SteamDesktop.relativeRoot] + dock
            // Steam's own file is rewritten only when a folder is missing from it, not to reformat it.
            guard wanted.contains(where: { !listed.contains(windowsPath($0).lowercased()) }) else { return nil }
            return try ExternalSteamLibraries.add(paths: wanted, to: data)
        }
        guard let data, dock.contains(where: { listed.contains(windowsPath($0).lowercased()) }) else { return nil }
        return try ExternalSteamLibraries.remove(paths: dock, from: data)
    }

    /// BCD Dock's own list (`<Dock>/steamapps/libraryfolders.vdf`, which BCD already writes for the drives) with the client's
    /// library (`share`), or without it, so games and DLC installed in Steam for Windows show in BCD's library and start through
    /// BCD Dock. BCD Dock's own folder is listed first when BCD makes the file. nil: the file needs no change.
    static func dockList(_ data: Data?, share: Bool) throws -> Data? {
        let listed = try listedPaths(data)
        let client = windowsPath(SteamDesktop.relativeRoot).lowercased()
        if share {
            let wanted = [SteamRuntimeFiles.relativeRoot, SteamDesktop.relativeRoot]
            guard wanted.contains(where: { !listed.contains(windowsPath($0).lowercased()) }) else { return nil }
            return try ExternalSteamLibraries.add(paths: wanted, to: data)
        }
        guard let data, listed.contains(client) else { return nil }
        return try ExternalSteamLibraries.remove(paths: [SteamDesktop.relativeRoot], from: data)
    }

    /// BCD Dock's own list, relative to drive_c.
    static var dockListRelative: String { SteamRuntimeFiles.relativeRoot + "/steamapps/libraryfolders.vdf" }

    /// The paths a list names, lower case; none without the file.
    static func listedPaths(_ data: Data?) throws -> Set<String> {
        guard let data else { return [] }
        var parser = try SteamKeyValues(data)
        let folders = try parser.read()["libraryfolders"]?.fields ?? [:]
        return Set(folders.values.compactMap { ($0["path"]?.string ?? $0.string)?.lowercased() })
    }

    /// "Program Files (x86)/Steam" -> "C:\Program Files (x86)\Steam", as the list writes it.
    static func windowsPath(_ relative: String) -> String { "C:\\" + relative.replacingOccurrences(of: "/", with: "\\") }

    /// The small files of BCD Dock's main library the copy keeps: its list, its marker and the games' appmanifests.
    static func keepsInBackup(_ name: String) -> Bool {
        let lower = name.lowercased()
        return lower == "libraryfolders.vdf" || lower == "libraryfolder.vdf" || (lower.hasPrefix("appmanifest_") && lower.hasSuffix(".acf"))
    }
}

// MARK: - BCD Dock's DLC choices in the client (2026-11-22)

/// Valve's client installs every DLC the account owns by itself. BCD installs only the DLC the player chose (none unless chosen), so
/// a BCD Dock game in the client's list looked unfinished, and the client began to download its DLC at its first start: X-Plane 12's
/// six Global Scenery packs, build 694 (2026-11-22). The owner: "BCD doc'un DLC tercihlerini görsün ve onlara sadık kalsın".
///
/// Steam keeps a DLC the player unticked (Properties › DLC) in the game's record as `"DisabledDLC" "id,id,..."`, under `UserConfig`
/// and under `MountedConfig`. Before a session BCD writes that list into each BCD Dock game's record. It holds:
/// - every DLC the account owns that has files to download and that BCD neither installed nor chose;
/// - the list's own entries (one the player unticked in the client stays unticked);
/// - but never a DLC the record states as installed, nor one BCD's choice wants.
/// After the session BCD's choice takes what the player did in the client (`adopted`): ticked or installed there joins it,
/// unticked or taken out there leaves it. So the two agree both ways, and BCD's next update keeps a DLC the client installed.
///
/// A DLC without files (its licence alone unlocks it) is never listed: disabled, the game would not see it. Only the `DisabledDLC`
/// value changes; every other byte of the record stays as Valve's client or BCD wrote it.
enum SteamDesktopDLC {
    /// How long the owned-DLC lookups before a session may take in all; a game not looked up by then keeps its record's list.
    static let lookupSeconds: Double = 30

    /// The list a record should hold before a session: the owned DLC with files and the list's own entries, without what is
    /// installed and without what BCD's choice wants (`wanted`, SteamDLCChoices: a DLC chosen in BCD and not downloaded yet may come
    /// down in the client too; nil: never chosen, which is what is installed).
    static func disabled(owned: Set<UInt32>, installed: Set<UInt32>, current: Set<UInt32>, wanted: Set<UInt32>?) -> Set<UInt32> {
        current.union(owned).subtracting(installed).subtracting(wanted ?? [])
    }

    /// BCD's choice after a session, from what the player did in the client: a DLC ticked there (it left the list BCD wrote) or
    /// installed there joins it; one unticked there (it joined the list) or taken out there leaves it. nil: nothing changed, so
    /// nothing is written (with no choice before, what is installed stays the choice).
    static func adopted(wanted: Set<UInt32>?, installedBefore: Set<UInt32>, written: Set<UInt32>, installedAfter: Set<UInt32>,
                        listedAfter: Set<UInt32>) -> Set<UInt32>? {
        let before = wanted ?? installedBefore
        let ticked = written.subtracting(listedAfter), unticked = listedAfter.subtracting(written)
        let added = installedAfter.subtracting(installedBefore), removed = installedBefore.subtracting(installedAfter)
        let after = before.union(ticked).union(added).subtracting(unticked).subtracting(removed)
        return after == before ? nil : after
    }

    static func text(_ ids: Set<UInt32>) -> String { ids.sorted().map(String.init).joined(separator: ",") }

    /// The DLC the record's `UserConfig` keeps disabled.
    static func listed(_ data: Data) -> Set<UInt32> {
        guard var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let value = root["AppState"]?["UserConfig"]?["DisabledDLC"]?.string else { return [] }
        return Set(value.split(separator: ",").compactMap { UInt32($0.trimmingCharacters(in: .whitespaces)) })
    }

    /// The DLC the record states as installed (an `InstalledDepots` entry with its `dlcappid`).
    static func installed(_ data: Data) -> Set<UInt32> {
        guard var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let depots = root["AppState"]?["InstalledDepots"]?.fields else { return [] }
        return Set(depots.values.compactMap { $0["dlcappid"]?.string.flatMap { UInt32($0) } })
    }

    private enum Kind { case word, open, close }
    private struct Token { let kind: Kind; let range: Range<Int>; let word: String }

    /// The record's tokens, with their byte ranges: quoted and bare words, braces; `//` comments and blanks are skipped.
    private static func tokens(_ b: [UInt8]) -> [Token]? {
        var out: [Token] = [], i = b.starts(with: [0xef, 0xbb, 0xbf]) ? 3 : 0
        while i < b.count {
            let c = b[i]
            if c <= 32 { i += 1; continue }
            if c == 47, i + 1 < b.count, b[i + 1] == 47 { while i < b.count, b[i] != 10 { i += 1 }; continue }
            if c == 123 { out.append(Token(kind: .open, range: i..<i + 1, word: "")); i += 1; continue }
            if c == 125 { out.append(Token(kind: .close, range: i..<i + 1, word: "")); i += 1; continue }
            let start = i
            var value: [UInt8] = []
            if c == 34 {
                i += 1
                var closed = false
                while i < b.count {
                    if b[i] == 34 { i += 1; closed = true; break }
                    if b[i] == 92, i + 1 < b.count, b[i + 1] == 34 || b[i + 1] == 92 { value.append(b[i + 1]); i += 2; continue }
                    value.append(b[i]); i += 1
                }
                if !closed { return nil }
            } else {
                while i < b.count, b[i] > 32, b[i] != 123, b[i] != 125 { value.append(b[i]); i += 1 }
            }
            out.append(Token(kind: .word, range: start..<i, word: String(decoding: value, as: UTF8.self)))
            guard out.count <= 100_000 else { return nil }
        }
        return out
    }

    /// The index of the brace that closes the one at `open`.
    private static func closing(_ t: [Token], _ open: Int) -> Int? {
        var depth = 0
        for k in open..<t.count {
            if t[k].kind == .open { depth += 1 } else if t[k].kind == .close { depth -= 1; if depth == 0 { return k } }
        }
        return nil
    }

    /// The direct children of the block between `open` and `close`: a key and its text value, or a key and its block.
    private static func children(_ t: [Token], _ open: Int, _ close: Int) -> [(key: Int, value: Int, end: Int)]? {
        var out: [(key: Int, value: Int, end: Int)] = [], k = open + 1
        while k < close {
            guard t[k].kind == .word, k + 1 < close else { return nil }
            if t[k + 1].kind == .word { out.append((k, k + 1, k + 1)); k += 2; continue }
            guard t[k + 1].kind == .open, let end = closing(t, k + 1), end < close else { return nil }
            out.append((k, k + 1, end)); k = end + 1
        }
        return out
    }

    /// The record with `ids` as its `DisabledDLC` under `UserConfig` and `MountedConfig` (a missing block is added, an empty list
    /// takes the line out), every other byte kept. nil when the record already says so, or is not a readable `AppState`.
    static func record(_ data: Data, disabled ids: Set<UInt32>) -> Data? {
        let b = [UInt8](data)
        guard b.count <= 1 << 20, let t = tokens(b), t.count >= 3, t[0].kind == .word,
              t[0].word.caseInsensitiveCompare("AppState") == .orderedSame, t[1].kind == .open,
              let rootClose = closing(t, 1), let top = children(t, 1, rootClose) else { return nil }
        let value = text(ids)
        func quoted(_ s: String) -> String { "\"" + s + "\"" }
        var edits: [(range: Range<Int>, text: String)] = []
        for name in ["UserConfig", "MountedConfig"] {
            if let block = top.first(where: { t[$0.key].word.caseInsensitiveCompare(name) == .orderedSame && t[$0.value].kind == .open }) {
                guard let inner = children(t, block.value, block.end) else { return nil }
                if let line = inner.first(where: { t[$0.key].word.caseInsensitiveCompare("DisabledDLC") == .orderedSame
                                                   && t[$0.value].kind == .word }) {
                    if ids.isEmpty {
                        var from = t[line.key].range.lowerBound
                        while from > 0, b[from - 1] != 10, b[from - 1] <= 32 { from -= 1 }
                        if from > 0, b[from - 1] == 10 { from -= 1; if from > 0, b[from - 1] == 13 { from -= 1 } }
                        edits.append((from..<t[line.value].range.upperBound, ""))
                    } else if t[line.value].word != value {
                        edits.append((t[line.value].range, quoted(value)))
                    }
                } else if !ids.isEmpty {
                    let at = t[block.value].range.upperBound
                    edits.append((at..<at, "\n\t\t" + quoted("DisabledDLC") + "\t\t" + quoted(value)))
                }
            } else if !ids.isEmpty {
                let at = t[rootClose].range.lowerBound
                edits.append((at..<at, "\t" + quoted(name) + "\n\t{\n\t\t" + quoted("DisabledDLC") + "\t\t" + quoted(value) + "\n\t}\n"))
            }
        }
        guard !edits.isEmpty else { return nil }
        var out = b
        for edit in edits.sorted(by: { $0.range.lowerBound > $1.range.lowerBound }) {
            out.replaceSubrange(edit.range, with: Array(edit.text.utf8))
        }
        return Data(out)
    }
}

// MARK: - The registry before a session

/// What BCD writes into the prefix's registry files before a Steam for Windows session, while no session runs (plan §4.4, §7.2):
///
///  - the discovery values, pointed at the client's own folder: HKCU Software\Valve\Steam SteamPath and SteamExe,
///    ...\ActiveProcess SteamClientDll and SteamClientDll64, and HKLM Software\Valve\Steam InstallPath in both registry views.
///    Steam writes the HKCU ones again itself when it runs; a game the client starts must not be handed the Dock's client meanwhile.
///    BCD Dock's own check puts every one of them back to the Dock's folder before the next Dock start
///    (SteamRuntimeFiles.repairRegistry, namesDesktopClient).
///  - with `softwareWebViews` (the session's arguments keep web views off the GPU): GPUAccelWebViewsV3, H264HWAccel and
///    SmoothScrollWebViews = 0 in HKCU Software\Valve\Steam, the values Steam's own Interface settings write for "GPU accelerated
///    rendering in web views", hardware video decoding and smooth scrolling. Valve documents none of them; a client that does not
///    know one ignores it. Without `softwareWebViews` they are left as they are: the player's choice in Steam's settings stands.
///
/// A value that already says the same is not written again; a hive that changes gets a copy of itself first, once
/// (`<name>.steam-desktop-bak`, the hive as it was before BCD's first change for this feature). Never an account, a licence or a
/// sign-in: only folders and three display settings.
enum SteamDesktopRegistry {
    static let backupExtension = "steam-desktop-bak"
    static let webViewValues = ["GPUAccelWebViewsV3", "H264HWAccel", "SmoothScrollWebViews"]

    enum Failure: Error, Equatable { case notARegistryFile, conflict(String) }

    /// The lines of one section: (the value's name, the whole line it has to be).
    private static func sections(machine: Bool, softwareWebViews: Bool) -> [(key: String, values: [(name: String, line: String)])] {
        let root = SteamDesktop.windowsRoot.replacingOccurrences(of: "\\", with: "\\\\")
        func text(_ name: String, _ value: String) -> (name: String, line: String) { (name, "\"\(name)\"=\"\(value)\"") }
        if machine {
            return [("Software\\\\Valve\\\\Steam", [text("InstallPath", root)]),
                    ("Software\\\\Wow6432Node\\\\Valve\\\\Steam", [text("InstallPath", root)])]
        }
        var steam = [text("SteamPath", root), text("SteamExe", root + "\\\\" + SteamDesktop.program)]
        if softwareWebViews { steam += webViewValues.map { (name: $0, line: "\"\($0)\"=dword:00000000") } }
        return [("Software\\\\Valve\\\\Steam", steam),
                ("Software\\\\Valve\\\\Steam\\\\ActiveProcess",
                 [text("SteamClientDll", root + "\\\\steamclient.dll"), text("SteamClientDll64", root + "\\\\steamclient64.dll")])]
    }

    /// `text` (a Wine registry file) with the values above; `changed` counts the lines written or added.
    static func seeded(_ text: String, machine: Bool, softwareWebViews: Bool) throws -> (text: String, changed: Int) {
        guard text.hasPrefix("WINE REGISTRY Version 2") else { throw Failure.notARegistryFile }
        var lines = text.components(separatedBy: "\n")
        var changed = 0
        for (key, values) in sections(machine: machine, softwareWebViews: softwareWebViews) {
            let header = "[\(key)]".lowercased()
            let starts = lines.indices.filter { lines[$0].lowercased().hasPrefix(header) }
            guard starts.count <= 1 else { throw Failure.conflict(key) }
            guard let start = starts.first else {
                if lines.last == "" { lines.removeLast() }
                lines += ["", "[\(key)]"] + values.map(\.line) + [""]
                changed += values.count
                continue
            }
            var end = lines[(start + 1)...].firstIndex { $0.hasPrefix("[") } ?? lines.count
            for (name, line) in values {
                let prefix = "\"\(name)\"=".lowercased()
                let found = lines[(start + 1)..<end].indices.filter { lines[$0].lowercased().hasPrefix(prefix) }
                guard found.count <= 1 else { throw Failure.conflict(key + "\\" + name) }
                if let at = found.first {
                    if lines[at] != line { lines[at] = line; changed += 1 }
                } else {
                    // After the section's last line that says something (a blank line closes it in Wine's own files).
                    var at = end
                    while at > start + 1, lines[at - 1].trimmingCharacters(in: .whitespaces).isEmpty { at -= 1 }
                    lines.insert(line, at: at)
                    end += 1
                    changed += 1
                }
            }
        }
        return (lines.joined(separator: "\n"), changed)
    }

    /// Writes both hives of `prefix`. Returns one word per hive and the log line.
    static func seed(prefix: URL, softwareWebViews: Bool, sessionRunning: Bool) -> (state: [String], line: String) {
        guard !sessionRunning else { return (["skipped"], "[steam-desktop] registry: skipped, a session is running") }
        let fm = FileManager.default
        var state: [String] = []
        for (file, machine) in [("system.reg", true), ("user.reg", false)] {
            let url = prefix.appendingPathComponent(file)
            guard url.standardizedFileURL.path == url.resolvingSymlinksInPath().standardizedFileURL.path,
                  let text = try? String(contentsOf: url, encoding: .utf8) else { state.append("\(file)=unreadable"); continue }
            do {
                let next = try seeded(text, machine: machine, softwareWebViews: softwareWebViews)
                if next.text == text { state.append("\(file)=ok"); continue }
                let backup = url.appendingPathExtension(backupExtension)
                if !fm.fileExists(atPath: backup.path) { try fm.copyItem(at: url, to: backup) }
                try Data(next.text.utf8).write(to: url, options: .atomic)
                state.append("\(file)=written(values \(next.changed))")
            } catch Failure.notARegistryFile {
                state.append("\(file)=not-a-registry-file")
            } catch Failure.conflict(let what) {
                state.append("\(file)=left-alone(\(what))")
            } catch {
                state.append("\(file)=not-written")
            }
        }
        return (state, "[steam-desktop] registry: " + state.joined(separator: " ") + " web-views=\(softwareWebViews ? "software" : "as-set-in-steam")")
    }
}

// MARK: - What a session's log says

/// A Steam for Windows session, read while it runs (BCDSteamDesktopLive: every few seconds, off the main thread): the engine's table
/// of processes and the new lines of the session's log go in, "[steam-desktop]" lines come out, so that one Send Log answers what the
/// plan's measurement asks (§11 M0): the memory at the sign-in window and at the main window, how often the web helper started and
/// how it ended, how often libcef.dll was loaded, how full the JIT pool's two ends are, and whether a code buffer was refused.
/// Pure: text in, text out (tests/host/check-steam-desktop.py runs it on a real log). Only file names are written, never a path.
struct SteamDesktopWatch {
    struct Memory: Equatable {
        /// What iOS counts against the app's limit, and what is left before it, in MB.
        var footprintMB: Int
        var leftMB: Int
    }

    /// The words for the session line the first time a program of the session is refused its last, smallest code buffer (FEX's
    /// "EXEC ALLOC FAILED": the JIT pool is full). The program dies with it; when it is Steam's web helper, Steam starts it again
    /// and it dies again, which only a restart of BCD ends (the pool cannot grow while a session runs). Refusing that restart in the
    /// engine is milestone M2's; M1 says what happened.
    static let noCodeMemoryWords = "This session ran out of code memory, so Steam's window may keep restarting. "
        + "Close Steam, or restart BCD and open Steam again."

    private(set) var helperStarts = 0
    private(set) var helperEnds = 0
    private(set) var helperCrashes = 0
    private(set) var cefLoads = 0
    private(set) var execFailed = 0
    private(set) var tailRefused = 0
    private(set) var poolExhausted = 0
    private(set) var helperChildrenRefused = 0
    private(set) var gameStarts = 0
    /// The JIT pool as the log last said: the image copies' end (head) and the pool's size, the code buffers' share (tail), in bytes.
    private(set) var poolHead: UInt64 = 0
    private(set) var poolSize: UInt64 = 0
    private(set) var poolTail: UInt64 = 0
    private(set) var milestones: [String] = []
    /// A sentence for the session line, to be shown once; the caller takes it.
    var notice: String?

    private var running: [String: [Double]] = [:]
    private var processLines = 0
    private var pending = ""
    static let maxProcessLines = 120

    /// A process the engine's table lists as running: its file name (lower case), and whether its folder is the client's or a
    /// Steam library's game folder.
    private struct Listed {
        var name: String
        var client: Bool
        var game: Bool
    }

    private static func listed(_ path: String) -> Listed {
        let lower = path.lowercased().replacingOccurrences(of: "/", with: "\\")
        let name = lower.split(separator: "\\").last.map(String.init) ?? lower
        let root = SteamDesktop.windowsRoot.lowercased()
        var folder = lower
        if let nt = folder.range(of: "\\??\\"), nt.lowerBound == folder.startIndex { folder.removeSubrange(nt) }
        return Listed(name: name, client: folder.hasPrefix(root + "\\") && !folder.contains("\\steamapps\\"),
                      game: folder.contains("\\steamapps\\common\\"))
    }

    private func suffix(_ seconds: Double, _ memory: Memory) -> String {
        var text = "t=+\(Int(seconds))s footprint=\(memory.footprintMB)MB left=\(memory.leftMB)MB helper-starts=\(helperStarts) "
            + "helper-ends=\(helperEnds) libcef-loads=\(cefLoads)"
        if poolSize > 0 { text += " pool-head=\(poolHead >> 20)/\(poolSize >> 20)MB" }
        text += " pool-tail=\(poolTail >> 20)MB exec-failed=\(execFailed) tail-refused=\(tailRefused) pool-exhausted=\(poolExhausted)"
        return text
    }

    /// The periodic line.
    func status(at seconds: Double, memory: Memory) -> String {
        "[steam-desktop] status " + suffix(seconds, memory) + " games-started=\(gameStarts)"
    }

    private mutating func milestone(_ name: String, _ seconds: Double, _ memory: Memory, once: Bool = true) -> String? {
        if once, milestones.contains(name) { return nil }
        milestones.append(name)
        return "[steam-desktop] milestone=\(name) " + suffix(seconds, memory)
    }

    /// The engine's process table (madeira_child_report: "R <helper> <seconds since start> <path>" for a running process) and the
    /// ends since the last look (madeira_child_ends_since: "<number> <status hex> <seconds lived> <gl> <helper> <path>").
    mutating func read(processes report: String, ends: String, at seconds: Double, memory: Memory) -> [String] {
        var out: [String] = []
        var seen: [String: [Double]] = [:]
        for line in report.split(separator: "\n") {
            let f = line.split(separator: "\t", maxSplits: 3, omittingEmptySubsequences: false).map(String.init)
            guard f.count == 4, f[0] == "R", let age = Double(f[2]), age.isFinite else { continue }
            let process = Self.listed(f[3])
            guard process.client || process.game else { continue }
            let started = seconds - age
            let key = process.name
            // The same process as at the last look: a start within a second of one already known under this name.
            let known = (running[key] ?? []).contains { abs($0 - started) < 1.0 } || (seen[key] ?? []).contains { abs($0 - started) < 1.0 }
            seen[key, default: []].append(started)
            guard !known else { continue }
            if process.name == "steamwebhelper.exe" {
                helperStarts += 1
                if let said = milestone(helperStarts == 1 ? "helper-start" : "helper-restart-\(helperStarts - 1)", seconds, memory) { out.append(said) }
            } else if process.name == "steam.exe", process.client {
                if let said = milestone("steam-start", seconds, memory, once: false) { out.append(said) }
            } else if process.game {
                gameStarts += 1
                if let said = milestone("game-start", seconds, memory, once: false) { out.append(said + " exe=\(process.name)") }
            } else if processLines < Self.maxProcessLines {
                processLines += 1
                out.append("[steam-desktop] process \(process.name) started t=+\(Int(seconds))s")
            }
        }
        running = seen
        for line in ends.split(separator: "\n") {
            let f = line.split(separator: "\t", maxSplits: 5, omittingEmptySubsequences: false).map(String.init)
            guard f.count == 6, let status = UInt32(f[1], radix: 16), let lived = Double(f[2]) else { continue }
            let process = Self.listed(f[5])
            guard process.client || process.game else { continue }
            let crash = status & 0xC000_0000 == 0xC000_0000 || status == 0x8000_0003
            if process.name == "steamwebhelper.exe" {
                helperEnds += 1
                if crash { helperCrashes += 1 }
            }
            guard processLines < Self.maxProcessLines else { continue }
            processLines += 1
            out.append("[steam-desktop] process \(process.name) ended status=0x\(String(status, radix: 16)) lived=\(String(format: "%.0f", lived))s"
                       + (process.game ? " (a game's)" : "") + " t=+\(Int(seconds))s footprint=\(memory.footprintMB)MB")
        }
        return out
    }

    /// Titles of Steam's own windows that are not its sign-in window, in every language: the web helper's window before its page
    /// names it, SDL's placeholder, the context menu, the toast window.
    static let otherWindowTitles: Set<String> = ["", "Steam", "SteamWebhelper", "SDL_app", "Menu", "Steam Big Picture Mode"]

    /// A `[win-name]` line of one of Steam's own windows (SDL_app) whose title is not one of the known others. Before the main window
    /// that is the sign-in window, whatever language Steam took from the system: build 689's first device run titled it "Steam'e Giriş
    /// Yap" (the log writes "Giri? Yap"), and the English rule never fired.
    static func namesOwnWindow(_ line: Substring) -> Bool {
        guard let at = line.range(of: "class='SDL_app' text=\"") else { return false }
        let rest = line[at.upperBound...]
        guard let end = rest.range(of: "\" rev=") ?? rest.lastIndex(of: "\"").map({ $0..<rest.index(after: $0) }) else { return false }
        let title = String(rest[..<end.lowerBound])
        return !otherWindowTitles.contains(title) && !title.hasPrefix("notificationtoasts_")
    }

    private static func hex(after marker: String, in line: Substring) -> UInt64? {
        guard let at = line.range(of: marker) else { return nil }
        let digits = line[at.upperBound...].prefix { $0.isHexDigit }
        return UInt64(digits, radix: 16)
    }

    /// New text of the session's log, as it was read from the file (a last line without its end waits for the next call).
    mutating func read(log chunk: String, at seconds: Double, memory: Memory) -> [String] {
        var out: [String] = []
        let text = pending + chunk
        var lines = text.split(separator: "\n", omittingEmptySubsequences: false)
        pending = text.hasSuffix("\n") ? "" : String(lines.popLast() ?? "")
        if pending.utf8.count > 16_384 { pending = "" }
        for line in lines {
            // Lines BCD's own watch wrote, and the previous run's tail a new session copies in, say nothing about this session.
            if line.contains("[steam-desktop]") || line.contains("[prev-tail]") || line.contains("[prev-end]") { continue }
            if line.contains("[win-name]") {
                var name: String?
                if line.contains("class='BootstrapUpdateUIClass'") { name = "updater-window" }
                else if line.contains("class='SDL_app' text=\"Sign in to Steam\"") { name = "sign-in-window" }
                else if line.contains("class='SDL_app' text=\"Steam\"") { name = "main-window" }
                else if line.contains("class='SDL_app' text=\"Steam Big Picture Mode\"") { name = "big-picture-window" }
                else if !milestones.contains("main-window"), Self.namesOwnWindow(line) { name = "sign-in-window" }
                if let name, let said = milestone(name, seconds, memory) { out.append(said) }
                continue
            }
            if line.contains("[jit-pool]") {
                if line.contains("[jit-pool] image "), let used = Self.hex(after: " used=0x", in: line) {
                    poolHead = used
                    if let size = Self.hex(after: "/0x", in: line[(line.range(of: " used=0x")?.upperBound ?? line.startIndex)...]) { poolSize = size }
                    if line.lowercased().contains("(libcef.dll)") { cefLoads += 1 }
                }
                if let head = Self.hex(after: " head_used=0x", in: line) {
                    poolHead = head
                    if let size = Self.hex(after: "/0x", in: line[(line.range(of: " head_used=0x")?.upperBound ?? line.startIndex)...]) { poolSize = size }
                }
                if let tail = Self.hex(after: " tail_resv=0x", in: line) { poolTail = tail }
                if line.contains("[jit-pool] TAIL REFUSED") { tailRefused += 1 }
                if line.contains("[jit-pool] EXHAUSTED") {
                    poolExhausted += 1
                    if poolExhausted == 1, let said = milestone("pool-exhausted", seconds, memory) { out.append(said) }
                }
                continue
            }
            if line.contains("[x64-image]"), line.lowercased().contains("libcef.dll\""), line.contains("mapped without a pool copy") {
                cefLoads += 1
                continue
            }
            if line.contains("EXEC ALLOC FAILED") {
                execFailed += 1
                if execFailed == 1 {
                    if let said = milestone("exec-alloc-failed", seconds, memory) { out.append(said) }
                    notice = Self.noCodeMemoryWords
                }
                continue
            }
            if line.contains("[proc-gate] REFUSING steamwebhelper --type=") { helperChildrenRefused += 1 }
        }
        return out
    }

    /// The session's last line.
    func summary(at seconds: Double, memory: Memory) -> String {
        "[steam-desktop] session end " + suffix(seconds, memory) + " helper-crashes=\(helperCrashes) helper-children-refused=\(helperChildrenRefused) "
            + "games-started=\(gameStarts) milestones=\(milestones.isEmpty ? "-" : milestones.joined(separator: ","))"
    }
}
