// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. The measurement in this file (BCDDriveSpeed, BCDDriveWriteMode, BCDDriveSpeedTest) was taken
// from https://github.com/tylerkaska112/Porthole (commit 99ec315, app/Sources/Core/DriveSpeedTest.swift) under GPL-3.0-or-later with
// the Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: BCD's names and folder, the file system's name read
// here, and BCDDriveGentle (BCD's own: when a download is written gently). See THIRD-PARTY-NOTICES.md.

import Foundation

// How fast a drive is as BCD uses it (Settings > Storage > External drives > Test speed), and whether a Steam download to a slow
// drive is written with fewer writers (bcd.cfg drive-gentle-writes = 1; off by default). Plain: nothing here knows the app's drives
// or its settings, so the preview build and tests/host/bcd_drive_speed_checks.swift use this file as it is.

/// A short, honest measurement of a drive as BCD uses it: a sequential write (with the drive made to finish it), a sequential read
/// that skips the system's cache, and how many small files it can create per second (what preparing a download does thousands of
/// times). Everything is made in a folder of its own inside the given folder and removed again.
struct BCDDriveSpeed: Equatable {
    var writeMBps: Double
    var readMBps: Double
    var smallFilesPerSecond: Double
    var fileSystem: String?

    var summary: String {
        String(format: "Write %.0f MB/s \u{00B7} read %.0f MB/s \u{00B7} %.0f small files/s", writeMBps, readMBps, smallFilesPerSecond)
            + (fileSystem.map { " \u{00B7} \($0)" } ?? "")
    }
}

/// How a download is written to a drive. A slow drive (a cheap thumb drive) gets few writers, in order; anything faster is written
/// like the device's own storage.
enum BCDDriveWriteMode: String {
    case gentle, normal
    /// Below this a download is written gently. A thumb drive that manages 5 MB/s of one long write does far less when many writers
    /// jump between files.
    static let gentleBelowMBps = 40.0
    static func mode(forWriteMBps mbps: Double) -> BCDDriveWriteMode { mbps < gentleBelowMBps ? .gentle : .normal }
}

enum BCDDriveSpeedTest {
    static let folderName = ".bcd-speed-test"
    private static let megabytes = 64
    private static let smallFiles = 60

    /// The quick one a download starts with: 8 MB written in order and made to reach the drive. Blocking, a second or two on a slow
    /// thumb drive.
    static func quickWriteMBps(in root: URL) throws -> Double {
        let fm = FileManager.default
        let folder = root.appendingPathComponent(folderName, isDirectory: true)
        try? fm.removeItem(at: folder)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: folder) }
        let block = [UInt8](repeating: 0xA5, count: 1 << 20)
        let fd = open(folder.appendingPathComponent("quick.bin").path, O_RDWR | O_CREAT | O_TRUNC, 0o644)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        let began = DispatchTime.now()
        let size = 8
        for i in 0..<size {
            let written = block.withUnsafeBytes { pwrite(fd, $0.baseAddress, block.count, off_t(i) << 20) }
            guard written == block.count else { let code = errno; close(fd); throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO) }
        }
        _ = fcntl(fd, F_FULLFSYNC)
        close(fd)
        return Double(size) / max(seconds(since: began), 0.001)
    }

    /// Blocking: run it off the main thread, with the drive's access held.
    static func run(in root: URL) throws -> BCDDriveSpeed {
        let fm = FileManager.default
        let folder = root.appendingPathComponent(folderName, isDirectory: true)
        try? fm.removeItem(at: folder)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: folder) }

        let block = [UInt8](repeating: 0xA5, count: 1 << 20)
        let big = folder.appendingPathComponent("big.bin").path

        // Write.
        var began = DispatchTime.now()
        let fd = open(big, O_RDWR | O_CREAT | O_TRUNC, 0o644)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        for i in 0..<megabytes {
            let written = block.withUnsafeBytes { pwrite(fd, $0.baseAddress, block.count, off_t(i) << 20) }
            guard written == block.count else { let code = errno; close(fd); throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO) }
        }
        _ = fcntl(fd, F_FULLFSYNC)
        close(fd)
        let writeSeconds = seconds(since: began)

        // Read, without the cache.
        began = DispatchTime.now()
        let rd = open(big, O_RDONLY)
        guard rd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        _ = fcntl(rd, F_NOCACHE, 1)
        var buffer = [UInt8](repeating: 0, count: 1 << 20)
        for i in 0..<megabytes {
            let got = buffer.withUnsafeMutableBytes { pread(rd, $0.baseAddress, 1 << 20, off_t(i) << 20) }
            if got <= 0 { break }
        }
        close(rd)
        let readSeconds = seconds(since: began)

        // Small files: create, write 4 KB, close.
        let small = folder.appendingPathComponent("small", isDirectory: true)
        try fm.createDirectory(at: small, withIntermediateDirectories: true)
        let payload = Data(repeating: 0x5A, count: 4096)
        began = DispatchTime.now()
        for i in 0..<smallFiles {
            try payload.write(to: small.appendingPathComponent("f\(i)"))
        }
        let smallSeconds = seconds(since: began)

        return BCDDriveSpeed(writeMBps: Double(megabytes) / max(writeSeconds, 0.001), readMBps: Double(megabytes) / max(readSeconds, 0.001),
                             smallFilesPerSecond: Double(smallFiles) / max(smallSeconds, 0.001), fileSystem: fileSystemName(at: root))
    }

    private static func seconds(since start: DispatchTime) -> Double {
        Double(DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000_000
    }

    /// The file system's name as the system has it ("apfs", "exfat", "msdos"), or nil.
    static func fileSystemName(at url: URL) -> String? {
        var info = statfs()
        guard statfs(url.path, &info) == 0 else { return nil }
        let name = withUnsafeBytes(of: &info.f_fstypename) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
        return name.isEmpty ? nil : name
    }

    /// The volume a path is on, as two numbers; nil when it cannot be read. The nearest folder that exists is asked.
    static func volume(of url: URL) -> [Int32]? {
        var probe = url.resolvingSymlinksInPath()
        while !FileManager.default.fileExists(atPath: probe.path), probe.pathComponents.count > 1 { probe.deleteLastPathComponent() }
        var info = statfs()
        guard statfs(probe.path, &info) == 0 else { return nil }
        return [info.f_fsid.val.0, info.f_fsid.val.1]
    }
}

/// When a Steam download is written with fewer writers (BCD's own). Only with the key, only for a folder that is not on the device's
/// own volume, and only when 8 MB written to it in one go come in under 40 MB/s. The measurement is kept per volume for this run of
/// the app.
enum BCDDriveGentle {
    /// bcd.cfg: 1 = a slow external drive is written gently. No line, the default: every download is written as before.
    static let key = "drive-gentle-writes"
    /// Chunks in flight for a slow drive (the usual number is 8, or 16 from a PC on the network).
    static let chunks = 4

    struct Decision: Equatable {
        /// The chunks in flight to use, or nil for the usual number.
        var limit: Int?
        /// A line for the log, or nil when there is nothing to say (the key is off, or the folder is on the device itself).
        var log: String?
    }

    /// `keyOn`: the key is 1. `folderVolume` / `homeVolume`: where the download goes and where the app's own files are (nil: not
    /// known). `measure`: the quick write test, called at most once and only when everything else says it matters.
    static func decide(keyOn: Bool, folderVolume: [Int32]?, homeVolume: [Int32]?, measure: () throws -> Double) -> Decision {
        guard keyOn, let folderVolume, let homeVolume, folderVolume != homeVolume else { return Decision(limit: nil, log: nil) }
        do {
            let mbps = try measure()
            let mode = BCDDriveWriteMode.mode(forWriteMBps: mbps)
            return Decision(limit: mode == .gentle ? chunks : nil,
                            log: String(format: "[drive] write test before the download: %.0f MB/s, writing %@", mbps,
                                        mode == .gentle ? "gently (\(chunks) chunks at a time, in order)" : "as usual"))
        } catch {
            return Decision(limit: nil, log: "[drive] the write test before the download failed (\(error.localizedDescription)): writing as usual")
        }
    }

    private static let lock = NSLock()
    private static var known: [[Int32]: Decision] = [:]

    /// The decision for a download into `folder`. Blocking for a second or two the first time a slow drive is asked about: call it
    /// off the main thread.
    static func decision(for folder: URL, keyOn: Bool) -> Decision {
        guard keyOn else { return Decision(limit: nil, log: nil) }
        let volume = BCDDriveSpeedTest.volume(of: folder), home = BCDDriveSpeedTest.volume(of: URL(fileURLWithPath: NSHomeDirectory()))
        lock.lock()
        if let volume, let kept = known[volume] { lock.unlock(); return Decision(limit: kept.limit, log: nil) }
        lock.unlock()
        var root = folder.resolvingSymlinksInPath()
        while !FileManager.default.fileExists(atPath: root.path), root.pathComponents.count > 1 { root.deleteLastPathComponent() }
        let made = decide(keyOn: true, folderVolume: volume, homeVolume: home) { try BCDDriveSpeedTest.quickWriteMBps(in: root) }
        if let volume, made.log != nil { lock.lock(); known[volume] = made; lock.unlock() }
        return made
    }
}
