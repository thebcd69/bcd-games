// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  VCRuntimeDownload.swift
//
//  Microsoft's Visual C++ runtime for x64 games (the twelve C++ runtime DLLs and MFC's
//  two), downloaded from Microsoft on the device.
//
//  BCD's public builds carry no Microsoft binaries: Microsoft's licence does not let this
//  project redistribute them, and upstream Madeira ships none either. The owner's builds
//  still bundle them (x86_64-vcruntime, fetched by build-ipa.yml). Every other build gets
//  them here: vc_redist.x64.exe, straight from Microsoft over HTTPS; its fourteen x64 DLLs
//  taken out on the device (BCDCab.c reads the cabinets), checked, and kept, byte for byte
//  as Microsoft shipped them, in Library/Application Support/VCRuntime/x86_64 (kept out of
//  backups). Nothing else is downloaded and nothing is run. At every session start
//  WineProcessBridge.m (madeira_vcruntime_dir) links them into the game's system32 exactly
//  as it links the bundled ones: the bundle's copy first, then this one.
//
//  API (the onboarding and Settings screens call it; this file draws no UI):
//
//    VCRuntime.shared                    @MainActor ObservableObject; every member below
//                                        is published and read on the main thread
//      status: VCRuntime.Status          the downloaded copy:
//                                          .notInstalled
//                                          .downloading(received:expected:)  bytes; expected 0 = not known yet
//                                          .installing(Double)               0...1, unpacking and checking
//                                          .installed(version:)              e.g. "14.44.35211.0" ("" if unreadable)
//      source: VCRuntime.Source          what the next session links: .bundled, .downloaded or
//                                        .none (the bridge's order: the bundle wins)
//      isAvailable: Bool                 source != .none
//      sourceDescription: String         one line for the UI ("Included in this build",
//                                        "Downloaded from Microsoft (version …)", "Not installed")
//      isBusy: Bool                      a download or install is running
//      lastError: String?                why the last install or removal failed; nil after a
//                                        success or a cancel
//      install(progress:completion:)     downloads, unpacks, checks and installs. Also a
//                                        reinstall: an installed copy is replaced only by one
//                                        that passed every check. progress gets 0...1 for
//                                        the whole job; completion gets the version, or the
//                                        error (VCRuntimeError.cancelled after cancel()).
//                                        Works on any build (a bundled copy still wins).
//      install(fromInstaller:progress:completion:)
//                                        the same from a vc_redist.x64.exe already on the
//                                        device (the host check uses it; or a file the user chose)
//      cancel()                          stops a download in progress
//      remove() -> Bool                  deletes the downloaded copy; false while a game runs
//      refresh()                         reads the disk again (status and source)
//    VCRuntime.didChangeNotification     posted on the main thread after each change above
//
//  Log tag: [vcruntime].
//
//  Round 625 (the owner's item 26): VCRuntime.x86 is the same for Microsoft's 32-bit package, for its four MFC DLLs only
//  (mfc140.dll, mfc140u.dll, mfcm140.dll, mfcm140u.dll) in Library/Application Support/VCRuntime/x86; a 32-bit game gets
//  them in its syswow64 when its config says env.MADEIRA_MFC32 = 1 (WineProcessBridge.m madeira_mfc32). VCRuntimePackage
//  says which package; every function takes the x64 one unless told otherwise, so the 64-bit path is as before.

import Foundation
import CryptoKit
#if canImport(UIKit)
import UIKit
#endif

// MARK: - Source, checks and installer (Foundation and CryptoKit only, plus WineMonoInstaller.sha256 from WineMono.swift; tests/host/check-vcruntime-download.py compiles this part with the installer part of WineMono.swift)

enum VCRuntimeError: LocalizedError, Equatable {
    case busy
    case sessionRunning
    case notEnoughSpace
    case network(String)
    case redirected(String)
    case http(Int)
    case tooLarge
    case checksumMismatch
    case extraction(String)
    case cancelled

    var errorDescription: String? {
        switch self {
        case .busy: return "The Visual C++ runtime is already being installed."
        case .sessionRunning: return "Close the running game first: it may be using the Visual C++ runtime."
        case .notEnoughSpace:
            return "Not enough free space: installing the Visual C++ runtime needs about 100 MB (about 14 MB once installed)."
        case .network(let detail): return "The download failed: \(detail)"
        case .redirected(let address):
            return "The download was redirected to \(address); only Microsoft's download server over HTTPS is accepted. Nothing was installed."
        case .http(let code): return "Microsoft's download server answered \(code)."
        case .tooLarge: return "The download was far larger than Microsoft's Visual C++ installer, so it was stopped."
        case .checksumMismatch: return "The download does not match the checksum Microsoft's server gave for it."
        case .extraction(let detail): return detail
        case .cancelled: return "Cancelled."
        }
    }
}

/// Where the installer comes from.
enum VCRuntimeSource {
    /// Microsoft's permanent link to the latest Visual C++ 2015-2022 x64 redistributable,
    /// the one build-ipa.yml downloads.
    static let url = URL(string: "https://aka.ms/vs/17/release/vc_redist.x64.exe")!

    /// Every host the download may touch. Checked 2026-10-18 (curl -sI): aka.ms answers with
    /// one 301 to https://download.visualstudio.microsoft.com/download/pr/<id>/<SHA-256>/VC_redist.x64.exe.
    static let hosts: Set<String> = ["aka.ms", "download.visualstudio.microsoft.com"]

    /// vc_redist.x64.exe is about 25 MB; a download past this is not it.
    static let maxBytes: Int64 = 128 << 20

    /// HTTPS on the default port to one of `hosts`, with no user name or password.
    static func isAllowed(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "https", let host = url.host?.lowercased(), hosts.contains(host) else { return false }
        return (url.port == nil || url.port == 443) && url.user == nil && url.password == nil
    }

    /// scheme://host[:port] of `url`, for messages and the log.
    static func origin(_ url: URL) -> String {
        "\(url.scheme ?? "?")://\(url.host ?? "?")" + (url.port.map { ":\($0)" } ?? "")
    }

    /// Microsoft's download server names each file by its SHA-256 (the path component before
    /// the file name); nil when the URL has no 64-hex-digit component.
    static func expectedSHA256(in url: URL) -> String? {
        url.pathComponents.first { $0.count == 64 && $0.allSatisfy(\.isHexDigit) }?.lowercased()
    }
}

/// Which of Microsoft's two packages (round 625, the owner's item 26: the 32-bit one, for MFC only).
struct VCRuntimePackage: Equatable, Sendable {
    /// For the log and the messages.
    let name: String
    /// Microsoft's permanent link.
    let url: URL
    /// Its folder in Library/Application Support/VCRuntime, and the download's file name there while it is unpacked.
    let folderName: String
    let downloadName: String
    /// What is taken out of it, and the file that marks a folder holding them.
    let dllNames: [String]
    let marker: String
    /// The suffix the package's cabinets give these DLLs ("vcruntime140.dll_amd64", "mfc140u.dll_x86").
    let entrySuffix: String
    /// IMAGE_FILE_HEADER.Machine, and whether the optional header is PE32+.
    let machine: Int
    let pe32Plus: Bool

    static let x64 = VCRuntimePackage(name: "x64", url: VCRuntimeSource.url, folderName: "x86_64",
                                      downloadName: "vc_redist.x64.exe.download", dllNames: VCRuntimeInstaller.dllNames,
                                      marker: VCRuntimeInstaller.marker, entrySuffix: "_amd64", machine: 0x8664, pe32Plus: true)
    /// Round 625: the 32-bit package, for its four MFC DLLs (a 32-bit game keeps Wine's own C++ runtime). Checked 2026-10-31 on
    /// Microsoft's 14.44.35211 package (13,953,392 bytes, SHA-256 0c09f261...5c55ed64): its cabinet a11 holds mfc140.dll_x86,
    /// mfc140u.dll_x86, mfcm140.dll_x86 and mfcm140u.dll_x86, each i386, PE32, a DLL, signed (and ten language DLLs, not taken).
    static let x86 = VCRuntimePackage(name: "x86", url: URL(string: "https://aka.ms/vs/17/release/vc_redist.x86.exe")!, folderName: "x86",
                                      downloadName: "vc_redist.x86.exe.download",
                                      dllNames: ["mfc140.dll", "mfc140u.dll", "mfcm140.dll", "mfcm140u.dll"], marker: "mfc140u.dll",
                                      entrySuffix: "_x86", machine: 0x14C, pe32Plus: false)
}

/// The parts of a PE image the checks need, read with every offset checked.
struct PEImage {
    /// IMAGE_FILE_HEADER.Machine: 0x8664 for x64, 0xAA64 for ARM64.
    let machine: Int
    /// The optional header is PE32+ (64-bit).
    let isPE32Plus: Bool
    /// IMAGE_FILE_DLL is set.
    let isDLL: Bool
    /// The certificate table (Authenticode) lies inside the file and starts with a PKCS#7
    /// WIN_CERTIFICATE: Microsoft's signature is still attached.
    let hasCertificate: Bool
    /// VS_FIXEDFILEINFO's file version, "14.44.35211.0"; nil when there is none.
    let fileVersion: String?

    init?(_ data: Data) {
        guard let image = data.withUnsafeBytes({ PEImage.parse(Reader(raw: $0)) }) else { return nil }
        self = image
    }

    private init(machine: Int, isPE32Plus: Bool, isDLL: Bool, hasCertificate: Bool, fileVersion: String?) {
        self.machine = machine
        self.isPE32Plus = isPE32Plus
        self.isDLL = isDLL
        self.hasCertificate = hasCertificate
        self.fileVersion = fileVersion
    }

    /// Little-endian reads that return nil outside the buffer.
    private struct Reader {
        let raw: UnsafeRawBufferPointer
        var count: Int { raw.count }
        func u16(_ at: Int) -> Int? {
            guard at >= 0, at <= raw.count - 2 else { return nil }
            return Int(raw[at]) | Int(raw[at + 1]) << 8
        }
        func u32(_ at: Int) -> Int? {
            guard at >= 0, at <= raw.count - 4 else { return nil }
            return Int(raw[at]) | Int(raw[at + 1]) << 8 | Int(raw[at + 2]) << 16 | Int(raw[at + 3]) << 24
        }
    }

    private static func parse(_ b: Reader) -> PEImage? {
        guard b.u16(0) == 0x5A4D, let pe = b.u32(0x3C), pe >= 0x40, b.u32(pe) == 0x0000_4550 else { return nil }   // "MZ", "PE\0\0"
        guard let machine = b.u16(pe + 4), let sections = b.u16(pe + 6), let optionalSize = b.u16(pe + 20),
              let characteristics = b.u16(pe + 22), let magic = b.u16(pe + 24) else { return nil }
        let optional = pe + 24
        let directories: Int
        switch magic {
        case 0x20B: directories = optional + 112
        case 0x10B: directories = optional + 96
        default: return nil
        }
        guard let directoryCount = b.u32(directories - 4), optional + optionalSize <= b.count else { return nil }
        func directory(_ index: Int) -> (Int, Int)? {
            let at = directories + 8 * index
            guard index < directoryCount, at + 8 <= optional + optionalSize,
                  let address = b.u32(at), let size = b.u32(at + 4), size > 0 else { return nil }
            return (address, size)
        }

        // The certificate table (directory 4) is the one that holds a file offset, not an RVA.
        var signed = false
        if case let (offset, size)? = directory(4), size >= 8, offset >= optional + optionalSize, offset <= b.count - size,
           let length = b.u32(offset), length >= 8, length <= size,
           b.u16(offset + 4) == 0x0200, b.u16(offset + 6) == 0x0002 {        // WIN_CERT_REVISION_2_0, PKCS_SIGNED_DATA
            signed = true
        }

        // RVA -> file offset through the section table, for `length` bytes.
        let table = optional + optionalSize
        func fileOffset(_ rva: Int, _ length: Int) -> Int? {
            for s in 0..<sections {
                let h = table + 40 * s
                guard let virtualSize = b.u32(h + 8), let address = b.u32(h + 12),
                      let rawSize = b.u32(h + 16), let rawOffset = b.u32(h + 20) else { return nil }
                guard rva >= address, rva - address < max(virtualSize, rawSize) else { continue }
                let delta = rva - address
                guard delta + length <= rawSize, rawOffset + delta + length <= b.count else { return nil }
                return rawOffset + delta
            }
            return nil
        }

        // RT_VERSION in the resource directory (2): type 16, its first name, its first language.
        func fileVersion() -> String? {
            guard case let (rva, size)? = directory(2), let root = fileOffset(rva, size) else { return nil }
            func entries(_ dir: Int) -> [(id: Int, target: Int)]? {
                guard dir >= 0, dir + 16 <= size, let named = b.u16(root + dir + 12), let ids = b.u16(root + dir + 14),
                      dir + 16 + 8 * (named + ids) <= size else { return nil }
                return (0..<(named + ids)).compactMap { k in
                    guard let id = b.u32(root + dir + 16 + 8 * k), let target = b.u32(root + dir + 20 + 8 * k) else { return nil }
                    return (id, target)
                }
            }
            let subdirectory = 0x8000_0000
            guard let types = entries(0), let type = types.first(where: { $0.id == 16 }), type.target & subdirectory != 0,
                  let names = entries(type.target & ~subdirectory), let name = names.first, name.target & subdirectory != 0,
                  let languages = entries(name.target & ~subdirectory), let language = languages.first,
                  language.target & subdirectory == 0, language.target + 16 <= size,
                  let dataRVA = b.u32(root + language.target), let dataSize = b.u32(root + language.target + 4),
                  dataSize >= 92, let info = fileOffset(dataRVA, dataSize) else { return nil }
            // VS_VERSIONINFO: wLength, wValueLength, wType, L"VS_VERSION_INFO", padding to 4,
            // then VS_FIXEDFILEINFO (signature 0xFEEF04BD, struct version, file version MS, LS).
            for (k, unit) in "VS_VERSION_INFO\0".utf16.enumerated() where b.u16(info + 6 + 2 * k) != Int(unit) { return nil }
            let fixed = info + 40
            guard b.u32(fixed) == 0xFEEF_04BD, let ms = b.u32(fixed + 8), let ls = b.u32(fixed + 12) else { return nil }
            return "\(ms >> 16).\(ms & 0xFFFF).\(ls >> 16).\(ls & 0xFFFF)"
        }

        return PEImage(machine: machine, isPE32Plus: magic == 0x20B, isDLL: characteristics & 0x2000 != 0,
                       hasCertificate: signed, fileVersion: fileVersion())
    }
}

/// Takes the DLLs out of vc_redist.x64.exe and installs them; no network, UI or app state,
/// so the host check runs it on the real installer.
enum VCRuntimeInstaller {
    /// What a game's system32 gets: the list build-ipa.yml fetches, in its order.
    static let dllNames = ["concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                           "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "vcamp140.dll",
                           "vccorlib140.dll", "vcomp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
                           "vcruntime140_threads.dll", "mfc140.dll", "mfc140u.dll"]
    /// The file that marks a folder holding them, here and in WineProcessBridge.m.
    static let marker = "vcruntime140.dll"
    /// The largest payload taken into memory: vc_redist's biggest (the ARM64 cabinet) is 11 MB.
    static let maxPayload = 64 << 20
    /// The most an outer cabinet's files may add up to; vc_redist's hold 25 MB.
    static let maxUnpacked = 512 << 20

    /// Library/Application Support/VCRuntime; WineProcessBridge.m reads VCRuntime/x86_64 in it.
    static var defaultRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("VCRuntime", isDirectory: true)
    }
    static func folder(in root: URL, package: VCRuntimePackage = .x64) -> URL {
        root.appendingPathComponent(package.folderName, isDirectory: true)
    }
    private static func staging(in root: URL, package: VCRuntimePackage) -> URL {
        root.appendingPathComponent(package.folderName + ".partial", isDirectory: true)
    }
    private static func retired(in root: URL, package: VCRuntimePackage) -> URL {
        root.appendingPathComponent(package.folderName + ".old", isDirectory: true)
    }
    private static func removed(in root: URL, package: VCRuntimePackage) -> URL {
        root.appendingPathComponent(package.folderName + ".removed", isDirectory: true)
    }
    static func downloadFile(in root: URL, package: VCRuntimePackage = .x64) -> URL { root.appendingPathComponent(package.downloadName) }

    /// The file version of the marker DLL in `folder`, or nil when the folder does not hold it.
    static func installedVersion(in folder: URL, package: VCRuntimePackage = .x64) -> String? {
        let marker = folder.appendingPathComponent(package.marker)
        guard FileManager.default.isReadableFile(atPath: marker.path) else { return nil }
        guard let data = try? Data(contentsOf: marker, options: .alwaysMapped) else { return "" }
        return PEImage(data)?.fileVersion ?? ""
    }

    /// An x64 DLL as Microsoft ships it: PE32+, AMD64, a DLL, its signature attached (the 32-bit package's: PE32, i386).
    static func check(_ data: Data, name: String, package: VCRuntimePackage = .x64) throws {
        guard let image = PEImage(data) else { throw VCRuntimeError.extraction("\(name) in the installer is not a Windows DLL.") }
        guard image.machine == package.machine, image.isPE32Plus == package.pe32Plus, image.isDLL else {
            throw VCRuntimeError.extraction(package.pe32Plus ? "\(name) in the installer is not an x64 DLL."
                                                             : "\(name) in the installer is not a 32-bit (x86) DLL.")
        }
        guard image.hasCertificate else {
            throw VCRuntimeError.extraction("\(name) in the installer has lost Microsoft's signature.")
        }
    }

    /// Writes the fourteen DLLs of vc_redist.x64.exe (`installer`) into `folder`, which must
    /// not hold any of them yet. The installer is a WiX Burn bundle: a setup program with
    /// cabinets attached. Each cabinet in it is opened; each of its files that is itself a
    /// cabinet is opened in turn, and its `<name>.dll_amd64` entries are the x64 DLLs (the
    /// `_arm64` ones, and the ARM64 cabinet's x64 managed MFC, are never taken). Each DLL
    /// passes `check` before it is written. `progress` gets 0...1. Throws unless all
    /// fourteen were found.
    static func extract(installer: URL, into folder: URL, package: VCRuntimePackage = .x64, progress: (Double) -> Void = { _ in }) throws {
        let exe = try Data(contentsOf: installer, options: .alwaysMapped)
        guard exe.count > 64, exe.starts(with: [0x4D, 0x5A]) else {
            throw VCRuntimeError.extraction("The download is not Microsoft's Visual C++ installer.")
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var found = Set<String>()
        try exe.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let base = raw.baseAddress else { return }
            var from = 0
            while found.count < package.dllNames.count, from < raw.count {
                let at = Int(bcd_cab_find(base, raw.count, from))
                if at < 0 { break }
                var handle: OpaquePointer?
                guard bcd_cab_open(base + at, raw.count - at, &handle) == BCD_CAB_OK, let outer = handle else {
                    from = at + 1                                       // "MSCF" that is not a cabinet
                    continue
                }
                defer { bcd_cab_close(outer) }
                let span = Int(bcd_cab_size(outer))
                try peel(outer, into: folder, package: package, found: &found) { part in
                    progress(min(1, (Double(at) + part * Double(span)) / Double(raw.count)))
                }
                from = at + span
            }
        }
        let missing = package.dllNames.filter { !found.contains($0) }
        guard missing.isEmpty else {
            throw VCRuntimeError.extraction("Microsoft's installer did not hold \(missing.joined(separator: ", ")).")
        }
        progress(1)
    }

    /// The files of an outer cabinet, in order (one decompression pass); the ones that are
    /// cabinets give up the DLLs still missing.
    private static func peel(_ outer: OpaquePointer, into folder: URL, package: VCRuntimePackage, found: inout Set<String>,
                             progress: (Double) -> Void) throws {
        let count = bcd_cab_count(outer)
        let total = (0..<count).reduce(0.0) { $0 + Double(bcd_cab_file_size(outer, $1)) }
        guard total <= Double(maxUnpacked) else { return }             // not one of the installer's
        var done = 0.0
        for index in 0..<count {
            if found.count == package.dllNames.count { return }
            let size = Int(bcd_cab_file_size(outer, index))
            defer {
                done += Double(size)
                progress(total > 0 ? done / total : 1)
            }
            guard size >= 36, size <= maxPayload else { continue }    // too small for a cabinet, or not one we need
            var payload = Data(count: size)
            let rc = payload.withUnsafeMutableBytes { bcd_cab_extract(outer, index, $0.baseAddress, $0.count) }
            guard rc == BCD_CAB_OK else {
                throw VCRuntimeError.extraction("The installer is damaged (\(String(cString: bcd_cab_error(rc)))).")
            }
            guard payload.starts(with: [0x4D, 0x53, 0x43, 0x46]) else { continue }   // "MSCF"
            try payload.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                var handle: OpaquePointer?
                guard bcd_cab_open(raw.baseAddress, raw.count, &handle) == BCD_CAB_OK, let inner = handle else { return }
                defer { bcd_cab_close(inner) }
                try take(from: inner, into: folder, package: package, found: &found)
            }
        }
    }

    /// The wanted `<name>.dll_amd64` (32-bit package: `_x86`) entries of an inner cabinet; the first copy of each wins.
    private static func take(from inner: OpaquePointer, into folder: URL, package: VCRuntimePackage, found: inout Set<String>) throws {
        for index in 0..<bcd_cab_count(inner) {
            guard let stored = bcd_cab_name(inner, index) else { continue }
            let name = String(cString: stored).lowercased()
            guard name.hasSuffix(".dll" + package.entrySuffix) else { continue }
            let dll = String(name.dropLast(package.entrySuffix.count))
            guard package.dllNames.contains(dll), !found.contains(dll) else { continue }
            let size = Int(bcd_cab_file_size(inner, index))
            guard size > 0, size <= maxPayload else {
                throw VCRuntimeError.extraction("\(dll) in the installer has an impossible size.")
            }
            var bytes = Data(count: size)
            let rc = bytes.withUnsafeMutableBytes { bcd_cab_extract(inner, index, $0.baseAddress, $0.count) }
            guard rc == BCD_CAB_OK else {
                throw VCRuntimeError.extraction("\(dll) could not be unpacked (\(String(cString: bcd_cab_error(rc)))).")
            }
            try check(bytes, name: dll, package: package)
            try bytes.write(to: folder.appendingPathComponent(dll), options: .withoutOverwriting)
            found.insert(dll)
        }
    }

    /// Installs from a downloaded (or given) vc_redist.x64.exe: the SHA-256 the download URL
    /// names, when it names one; the DLLs unpacked and checked into a staging folder; then the
    /// staging folder put in place of the installed copy in one step. `sessionRunning` is
    /// asked just before that step: an installed copy is not replaced under a running game.
    /// Returns vcruntime140.dll's file version ("" if unreadable). On any error the installed
    /// copy is as it was.
    static func install(installer: URL, downloadedFrom url: URL?, root: URL, package: VCRuntimePackage = .x64,
                        sessionRunning: () -> Bool = { false },
                        progress: (Double) -> Void = { _ in }) throws -> String {
        let fm = FileManager.default
        let staging = Self.staging(in: root, package: package), folder = Self.folder(in: root, package: package)
        if let url, let expected = VCRuntimeSource.expectedSHA256(in: url) {
            guard try WineMonoInstaller.sha256(of: installer) == expected else { throw VCRuntimeError.checksumMismatch }
        }
        try? fm.removeItem(at: staging)
        defer { try? fm.removeItem(at: staging) }
        try extract(installer: installer, into: staging, package: package, progress: progress)
        let version = installedVersion(in: staging, package: package) ?? ""
        if fm.fileExists(atPath: folder.path), sessionRunning() { throw VCRuntimeError.sessionRunning }
        try replace(folder, with: staging, retired: Self.retired(in: root, package: package))
        return version
    }

    /// Puts `staging` in place of `folder`. With a copy already there, the two directories
    /// trade names in one step (APFS's RENAME_SWAP), so a session starting meanwhile links the
    /// old copy or the new one, never neither; the old one is then deleted.
    private static func replace(_ folder: URL, with staging: URL, retired: URL) throws {
        let fm = FileManager.default
        try? fm.removeItem(at: retired)
        guard fm.fileExists(atPath: folder.path) else {
            try fm.moveItem(at: staging, to: folder)
            return
        }
        if renamex_np(staging.path, folder.path, UInt32(RENAME_SWAP)) == 0 {
            try? fm.removeItem(at: staging)                              // the old copy, now
            return
        }
        try fm.moveItem(at: folder, to: retired)                         // no RENAME_SWAP here: two steps
        do {
            try fm.moveItem(at: staging, to: folder)
        } catch {
            try? fm.moveItem(at: retired, to: folder)
            throw error
        }
        try? fm.removeItem(at: retired)
    }

    /// Deletes the installed copy: first renamed away in one step, so a session starting
    /// meanwhile finds the whole copy or none.
    static func remove(root: URL, package: VCRuntimePackage = .x64) throws {
        let fm = FileManager.default
        let folder = Self.folder(in: root, package: package), removed = Self.removed(in: root, package: package)
        guard fm.fileExists(atPath: folder.path) else { return }
        try? fm.removeItem(at: removed)
        try fm.moveItem(at: folder, to: removed)
        try? fm.removeItem(at: removed)
    }

    /// What an install or removal the app was killed in left behind. Killed between the
    /// two steps of a swap without RENAME_SWAP, the old copy is put back.
    static func removeLeftovers(root: URL, package: VCRuntimePackage = .x64) {
        let fm = FileManager.default
        let folder = Self.folder(in: root, package: package), retired = Self.retired(in: root, package: package)
        if !fm.fileExists(atPath: folder.path), fm.fileExists(atPath: retired.appendingPathComponent(package.marker).path) {
            try? fm.moveItem(at: retired, to: folder)
        }
        for url in [Self.staging(in: root, package: package), retired, Self.removed(in: root, package: package),
                    Self.downloadFile(in: root, package: package)] {
            try? fm.removeItem(at: url)
        }
    }
}

// MARK: - Download

/// One download of vc_redist.x64.exe: redirects only to VCRuntimeSource.hosts, progress,
/// and the finished file moved out of URLSession's temporary folder. `finished` gets the
/// final URL. Its delegate calls arrive one at a time on the session's own queue.
final class VCRuntimeDownloader: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let destination: URL
    private let progress: (Int64, Int64) -> Void
    private let finished: (Result<URL, Error>) -> Void
    private var session: URLSession?
    private var task: URLSessionDownloadTask?
    private var outcome: Result<URL, Error>?
    private var refusedHost: String?
    private var tooLarge = false
    private var reported: Int64 = -1

    /// No cookies or cache kept.
    static func configuration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 60 * 60
        configuration.waitsForConnectivity = false
        return configuration
    }

    /// `url` and `configuration` are for tests/host/check-vcruntime-download.py, which serves
    /// the redirects from a URLProtocol; the app uses the defaults.
    init(url: URL = VCRuntimeSource.url, configuration: URLSessionConfiguration = VCRuntimeDownloader.configuration(),
         destination: URL, progress: @escaping (Int64, Int64) -> Void,
         finished: @escaping (Result<URL, Error>) -> Void) {
        self.destination = destination
        self.progress = progress
        self.finished = finished
        super.init()
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        self.session = session
        task = session.downloadTask(with: url)
    }

    func start() { task?.resume() }
    func cancel() { task?.cancel() }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        if let url = request.url, VCRuntimeSource.isAllowed(url) {
            completionHandler(request)
        } else {
            refusedHost = request.url.map(VCRuntimeSource.origin) ?? "an unknown address"
            completionHandler(nil)
            task.cancel()
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesWritten > VCRuntimeSource.maxBytes || totalBytesExpectedToWrite > VCRuntimeSource.maxBytes {
            tooLarge = true
            downloadTask.cancel()
            return
        }
        // At most one report per 256 KB.
        guard reported < 0 || totalBytesWritten - reported >= 256 << 10 || totalBytesWritten == totalBytesExpectedToWrite else { return }
        reported = totalBytesWritten
        progress(totalBytesWritten, max(totalBytesExpectedToWrite, 0))
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // URLSession deletes `location` when this returns: check the answer and move the file now.
        guard let response = downloadTask.response as? HTTPURLResponse, let url = response.url else {
            outcome = .failure(VCRuntimeError.network("no answer from the server"))
            return
        }
        guard VCRuntimeSource.isAllowed(url) else {
            outcome = .failure(VCRuntimeError.redirected(VCRuntimeSource.origin(url)))
            return
        }
        guard response.statusCode == 200 else {
            outcome = .failure(VCRuntimeError.http(response.statusCode))
            return
        }
        do {
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: location, to: destination)
            outcome = .success(url)
        } catch {
            outcome = .failure(VCRuntimeError.network(error.localizedDescription))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let result: Result<URL, Error>
        if let host = refusedHost {
            result = .failure(VCRuntimeError.redirected(host))
        } else if tooLarge {
            result = .failure(VCRuntimeError.tooLarge)
        } else if let error {
            let cancelled = (error as? URLError)?.code == .cancelled
            result = .failure(cancelled ? VCRuntimeError.cancelled : VCRuntimeError.network(error.localizedDescription))
        } else {
            result = outcome ?? .failure(VCRuntimeError.network("the download ended without a file"))
        }
        if case .failure = result { try? FileManager.default.removeItem(at: destination) }
        session.finishTasksAndInvalidate()                             // releases this delegate
        self.session = nil
        finished(result)
    }
}

// MARK: - Model

@MainActor final class VCRuntime: ObservableObject {
    static let shared = VCRuntime(root: VCRuntimeInstaller.defaultRoot,
                                  bundledFolder: Bundle.main.bundleURL.appendingPathComponent("x86_64-vcruntime", isDirectory: true))
    /// Round 625 (the owner's item 26): Microsoft's 32-bit MFC, downloaded the same way. No build carries it (no bundle folder).
    static let x86 = VCRuntime(root: VCRuntimeInstaller.defaultRoot,
                               bundledFolder: Bundle.main.bundleURL.appendingPathComponent("i386-mfc", isDirectory: true), package: .x86)

    enum Status: Equatable {
        case notInstalled
        case downloading(received: Int64, expected: Int64)
        case installing(Double)
        case installed(version: String)
    }

    enum Source: String {
        case bundled, downloaded, none
    }

    static let didChangeNotification = Notification.Name("BCDVCRuntimeDidChange")

    /// Free space an install needs: the 25 MB download, the new copy (14 MB) and the old one.
    static let spaceNeeded: Int64 = 100_000_000

    @Published private(set) var status: Status = .notInstalled
    @Published private(set) var source: Source = .none
    @Published private(set) var lastError: String?

    var isAvailable: Bool { source != .none }

    var isBusy: Bool {
        switch status {
        case .downloading, .installing: return true
        case .notInstalled, .installed: return false
        }
    }

    var sourceDescription: String {
        switch source {
        case .bundled: return "Included in this build"
        case .downloaded:
            if case .installed(let version) = installedState, !version.isEmpty { return "Downloaded from Microsoft (version \(version))" }
            return "Downloaded from Microsoft"
        case .none: return "Not installed"
        }
    }

    /// Library/Application Support/VCRuntime, or a test's folder.
    let root: URL
    /// The bundle's x86_64-vcruntime, or a test's folder.
    let bundledFolder: URL
    /// Which of Microsoft's packages (round 625: x64, or x86 for MFC).
    let package: VCRuntimePackage
    var folder: URL { VCRuntimeInstaller.folder(in: root, package: package) }
    /// The download's first URL and session settings. tests/host/check-vcruntime-download.py
    /// serves Microsoft's answers from a URLProtocol through them; the app keeps these.
    var downloadURL: URL
    var downloadConfiguration = VCRuntimeDownloader.configuration()

    private var installedState: Status = .notInstalled              // the disk's state, while busy too
    private var downloader: VCRuntimeDownloader?
    private var progressHandler: ((Double) -> Void)?
    private var completionHandler: ((Result<String, Error>) -> Void)?
    #if canImport(UIKit)
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    #endif

    init(root: URL, bundledFolder: URL, package: VCRuntimePackage = .x64) {
        self.root = root
        self.bundledFolder = bundledFolder
        self.package = package
        downloadURL = package.url
        VCRuntimeInstaller.removeLeftovers(root: root, package: package)
        refresh()
    }

    func refresh() {
        let version = VCRuntimeInstaller.installedVersion(in: folder, package: package)
        installedState = version.map { .installed(version: $0) } ?? .notInstalled
        if !isBusy { status = installedState }
        let bundled = FileManager.default.isReadableFile(atPath: bundledFolder.appendingPathComponent(package.marker).path)
        source = bundled ? .bundled : version != nil ? .downloaded : .none
        changed()
    }

    func install(progress: ((Double) -> Void)? = nil, completion: ((Result<String, Error>) -> Void)? = nil) {
        guard let file = begin(progress: progress, completion: completion) else { return }
        log("download \(downloadURL.absoluteString)")
        status = .downloading(received: 0, expected: 0)
        changed()
        let downloader = VCRuntimeDownloader(url: downloadURL, configuration: downloadConfiguration,
                                             destination: file, progress: { received, expected in
            Task { @MainActor in self.downloadProgress(received, expected) }
        }, finished: { result in
            Task { @MainActor in self.downloaded(result, file: file) }
        })
        self.downloader = downloader
        downloader.start()
    }

    /// Installs from a vc_redist.x64.exe already on the device (tests/host/check-vcruntime-download.py;
    /// also a file the user chose). The file is not deleted.
    func install(fromInstaller installer: URL, progress: ((Double) -> Void)? = nil,
                 completion: ((Result<String, Error>) -> Void)? = nil) {
        guard begin(progress: progress, completion: completion) != nil else { return }
        log("install from \(installer.lastPathComponent)")
        unpack(installer, downloadedFrom: nil, deleteAfterwards: false)
    }

    func cancel() {
        downloader?.cancel()
    }

    @discardableResult
    func remove() -> Bool {
        guard !isBusy else { lastError = VCRuntimeError.busy.errorDescription; changed(); return false }
        guard wine_process_is_running() == 0 else {
            lastError = VCRuntimeError.sessionRunning.errorDescription
            changed()
            return false
        }
        do {
            try VCRuntimeInstaller.remove(root: root, package: package)
            lastError = nil
            log("removed")
        } catch {
            lastError = "The Visual C++ runtime could not be removed: \(error.localizedDescription)"
            log("remove failed: \(error.localizedDescription)")
        }
        refresh()
        return lastError == nil
    }

    // MARK: Steps

    /// Checks and setup shared by both installs; nil (completion already called) when it cannot start.
    private func begin(progress: ((Double) -> Void)?, completion: ((Result<String, Error>) -> Void)?) -> URL? {
        guard !isBusy else { completion?(.failure(VCRuntimeError.busy)); return nil }
        if case .installed = installedState, wine_process_is_running() != 0 {
            lastError = VCRuntimeError.sessionRunning.errorDescription
            changed()
            completion?(.failure(VCRuntimeError.sessionRunning))
            return nil
        }
        do {
            let fm = FileManager.default
            try fm.createDirectory(at: root, withIntermediateDirectories: true)
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            var url = root
            try url.setResourceValues(values)
            VCRuntimeInstaller.removeLeftovers(root: root, package: package)
            let free = try root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
                .volumeAvailableCapacityForImportantUsage
            if let free, free < Self.spaceNeeded { throw VCRuntimeError.notEnoughSpace }
        } catch {
            let failure = error as? VCRuntimeError ?? .extraction("The Visual C++ runtime folder could not be prepared: \(error.localizedDescription)")
            lastError = failure.errorDescription
            log("failed: \(lastError ?? "")")
            changed()
            completion?(.failure(failure))
            return nil
        }
        lastError = nil
        progressHandler = progress
        completionHandler = completion
        beginBackgroundTask()
        return VCRuntimeInstaller.downloadFile(in: root, package: package)
    }

    /// Reports arrive through separate tasks: an older one that comes late is dropped.
    private func downloadProgress(_ received: Int64, _ expected: Int64) {
        guard case .downloading(let before, _) = status, received >= before else { return }
        status = .downloading(received: received, expected: expected)
        if expected > 0 { progressHandler?(0.9 * Double(received) / Double(expected)) }
        changed()
    }

    private func downloaded(_ result: Result<URL, Error>, file: URL) {
        downloader = nil
        switch result {
        case .failure(let error):
            finish(.failure(error))
        case .success(let url):
            let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.int64Value ?? 0
            log("downloaded \(size) bytes from \(url.host ?? "?")")
            unpack(file, downloadedFrom: url, deleteAfterwards: true)
        }
    }

    private func unpack(_ installer: URL, downloadedFrom url: URL?, deleteAfterwards: Bool) {
        status = .installing(0)
        changed()
        let root = self.root, package = self.package
        Task.detached(priority: .userInitiated) {
            var last = -1
            let outcome: Result<String, Error>
            do {
                let version = try VCRuntimeInstaller.install(installer: installer, downloadedFrom: url, root: root, package: package,
                                                             sessionRunning: { wine_process_is_running() != 0 }) { fraction in
                    let percent = Int(fraction * 100)
                    guard percent != last else { return }
                    last = percent
                    Task { @MainActor in self.installProgress(Double(percent) / 100) }
                }
                outcome = .success(version)
            } catch {
                outcome = .failure(error)
            }
            if deleteAfterwards { try? FileManager.default.removeItem(at: installer) }
            await MainActor.run { self.finish(outcome) }
        }
    }

    private func installProgress(_ fraction: Double) {
        guard case .installing(let before) = status, fraction >= before else { return }
        status = .installing(fraction)
        progressHandler?(0.9 + 0.1 * fraction)
        changed()
    }

    private func finish(_ outcome: Result<String, Error>) {
        let completion = completionHandler
        if case .success = outcome { progressHandler?(1) }
        progressHandler = nil
        completionHandler = nil
        endBackgroundTask()
        switch outcome {
        case .success(let version):
            lastError = nil
            log("installed \(version.isEmpty ? "(version unreadable)" : version) in Application Support/VCRuntime/\(package.folderName)")
        case .failure(let error):
            if (error as? VCRuntimeError) == .cancelled {
                lastError = nil
                log("cancelled")
            } else {
                lastError = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                log("failed: \(lastError ?? "")")
            }
        }
        status = .notInstalled                                         // not busy: refresh reads the disk
        refresh()
        completion?(outcome)
    }

    private func changed() {
        NotificationCenter.default.post(name: Self.didChangeNotification, object: self)
    }

    private func log(_ line: String) {
        LogStore.shared.log((package == .x64 ? "[vcruntime] " : "[vcruntime-\(package.name)] ") + line)
    }

    // MARK: Background time

    /// Lets a download or install that is under way finish when the app goes to the background.
    private func beginBackgroundTask() {
        #if canImport(UIKit)
        guard backgroundTask == .invalid else { return }
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "VC++ runtime") { [weak self] in
            MainActor.assumeIsolated { self?.endBackgroundTask() }
        }
        #endif
    }

    private func endBackgroundTask() {
        #if canImport(UIKit)
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
        #endif
    }
}
