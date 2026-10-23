//
//  GameLibrary.swift
//  Finding and launching guest executables without a rebuild.
//
//  Every title so far needed its own hard-coded button, and the escape hatch
//  was bcd-exe.txt -- a file the user has to create in the Files app, spell
//  a Windows path into by hand, and get exactly right. The first time it was
//  used in anger the answer was "ERR No documents/madeire-exe.txt".
//
//  The prefix already knows what is installed. C:\ is <Documents>/wine/drive_c,
//  so scanning it answers the question directly: these are the executables you
//  could run. Wine's own tree is excluded, because a list where cmd.exe and
//  winecfg.exe outnumber the games is not a list anyone reads.
//
//  Arguments stay per-title and persist, so "Stray needs -dx11 -windowed" is
//  remembered rather than retyped, and the hard-coded buttons keep working --
//  this is an addition, not a replacement.
//

import Foundation
import SwiftUI

// MARK: - Model

struct GuestExecutable: Identifiable, Hashable {
    /// Windows path as the guest sees it, e.g. C:\Program Files\Stray\...\Stray-Win64-Shipping.exe
    let windowsPath: String
    /// Location on the device, for size and mtime.
    let url: URL
    /// Folder immediately under Program Files, or the drive root -- what a person calls the game.
    let title: String
    let sizeBytes: Int64

    var id: String { windowsPath }

    var fileName: String { url.lastPathComponent }

    /// The folder holding the exe, as the guest names it without the drive:
    /// "Crysis\Bin64". What tells two same-named exes apart on a card.
    var folderDescription: String {
        var parts = windowsPath.split(separator: "\\").map(String.init)
        if parts.first?.hasSuffix(":") == true { parts.removeFirst() }
        if !parts.isEmpty { parts.removeLast() }
        return parts.isEmpty ? "C:\\" : parts.joined(separator: "\\")
    }

    var sizeDescription: String {
        let mb = Double(sizeBytes) / (1024 * 1024)
        if mb < 1 { return String(format: "%.0f KB", Double(sizeBytes) / 1024) }
        return mb < 1024 ? String(format: "%.1f MB", mb) : String(format: "%.2f GB", mb / 1024)
    }
}

// MARK: - Scanner

enum GameLibrary {

    /// Directories under drive_c that are Wine's, not the user's. users is
    /// walked (people copy games to the Desktop or Public), minus its caches.
    private static let skippedRoots: Set<String> = [
        "windows", "proc", "programdata",
    ]

    /// Never games at any depth: per-user caches and temp trees are large and
    /// full of helper exes.
    private static let skippedAnywhere: Set<String> = [
        "appdata", "temp", "$recycle.bin", "_commonredist", "commonredist", "redist",
        "directx", "__installer", "support",
    ]

    /// Folders that hold a game's binaries rather than a game. A container
    /// folder whose exes all sit under different subfolders is split into one
    /// title per subfolder -- unless those subfolders are one of these.
    private static let binaryFolders: Set<String> = [
        "bin", "bin32", "bin64", "bin_x64", "bin_x86", "binaries", "x64", "x86", "win64", "win32",
        "game", "engine", "system", "program", "exe", "launcher", "retail", "shipping",
    ]

    /// Launchers and tooling that ship beside a game and are never the thing you want.
    private static let noiseNames: Set<String> = [
        "unins000.exe", "uninstall.exe", "uninstaller.exe",
        "vcredist_x64.exe", "vcredist_x86.exe", "dxwebsetup.exe",
        "dotnetfx.exe", "oalinst.exe", "crashreporter.exe",
        "crashhandler.exe", "ueprereqsetup_x64.exe",
        "vc_redist.x64.exe", "vc_redist.x86.exe", "unitycrashhandler64.exe",
        "unitycrashhandler32.exe", "crashreportclient.exe", "crashpad_handler.exe",
        "dxsetup.exe", "installermessage.exe", "easyanticheat_setup.exe",
        "easyanticheat_eos_setup.exe", "cleanup.exe", "touchup.exe",
    ]

    static var driveC: URL? {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first?
            .appendingPathComponent("wine")
            .appendingPathComponent("drive_c")
    }

    /// Everything under drive_c that could plausibly be launched, deepest-first by size.
    ///
    /// Depth is bounded because a game tree can be arbitrarily deep and a scan that
    /// walks all of it on the main thread would stall the UI on first open.
    static func scan(maxDepth: Int = 12) -> [GuestExecutable] {
        guard let root = driveC else { return [] }
        var found: [GuestExecutable] = []
        let fm = FileManager.default

        func walk(_ dir: URL, depth: Int, title: String?) {
            guard depth <= maxDepth else { return }
            guard let entries = try? fm.contentsOfDirectory(
                at: dir,
                includingPropertiesForKeys: [.isDirectoryKey, .fileSizeKey],
                options: [.skipsHiddenFiles]
            ) else { return }

            for entry in entries {
                guard ExternalGameStorage.shared.relativePath(for: entry, drive: root) != nil else { continue }
                let values = try? entry.resolvingSymlinksInPath().resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey])
                if values?.isDirectory == true {
                    let lower = entry.lastPathComponent.lowercased()
                    if depth == 0 && skippedRoots.contains(lower) { continue }
                    if skippedAnywhere.contains(lower) { continue }
                    // The first directory below drive_c (or below Program Files) names the title.
                    let externalRoot = depth == 0 && entry.lastPathComponent == "External"
                    walk(entry, depth: depth + 1, title: externalRoot ? nil : title ?? titleFor(entry))
                } else if entry.pathExtension.lowercased() == "exe" {
                    if noiseNames.contains(entry.lastPathComponent.lowercased()) { continue }
                    guard let windows = windowsPath(for: entry, root: root) else { continue }
                    found.append(GuestExecutable(
                        windowsPath: windows,
                        url: entry,
                        title: title ?? entry.deletingPathExtension().lastPathComponent,
                        sizeBytes: Int64(values?.fileSize ?? 0)
                    ))
                }
            }
        }

        walk(root, depth: 0, title: nil)
        found = splitContainers(found, root: root)

        // Biggest first: a game's shipping binary dwarfs the launchers next to it,
        // so this puts the thing you actually want at the top without guessing.
        return found.sorted {
            $0.title == $1.title ? $0.sizeBytes > $1.sizeBytes
                                 : $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
        }
    }

    /// "C:\\Games\\GTA\\gta.exe" and "C:\\Games\\Crysis\\Bin64\\Crysis.exe" are two games,
    /// not one called Games. A title folder with no exe of its own whose exes
    /// sit in two or more subfolders is a container: each subfolder becomes a
    /// title. Repeats so a container inside a container splits too. Subfolders
    /// named like binary directories (Bin32/Bin64, Engine) keep the title whole.
    private static func splitContainers(_ exes: [GuestExecutable], root: URL) -> [GuestExecutable] {
        let rootPath = root.standardizedFileURL.path
        // Components of each exe's directory, from the title folder down.
        func below(_ exe: GuestExecutable, _ title: String) -> [String]? {
            let rel = exe.url.deletingLastPathComponent().standardizedFileURL.path
            guard rel.hasPrefix(rootPath + "/") else { return nil }
            let comps = rel.dropFirst(rootPath.count + 1).split(separator: "/").map(String.init)
            guard let i = comps.firstIndex(of: title) else { return nil }
            return Array(comps[(i + 1)...])
        }
        var current = exes
        for _ in 0..<5 {
            var changed = false
            let byTitle = Dictionary(grouping: current, by: { $0.title })
            var next: [GuestExecutable] = []
            for (title, group) in byTitle {
                let subs = group.map { below($0, title) }
                let firsts = Set(subs.compactMap { $0?.first?.lowercased() })
                let isContainer = !subs.contains { $0 == nil || $0!.isEmpty }
                    && firsts.count >= 2
                    && firsts.isDisjoint(with: binaryFolders)
                if !isContainer { next += group; continue }
                changed = true
                for (exe, sub) in zip(group, subs) {
                    next.append(GuestExecutable(windowsPath: exe.windowsPath, url: exe.url,
                                                title: sub?.first ?? title, sizeBytes: exe.sizeBytes))
                }
            }
            current = next
            if !changed { break }
        }
        return current
    }

    /// The folder a person would name: the one under Program Files, else the one
    /// under drive_c. Returns nil for the Program Files containers themselves so
    /// the level below them supplies the title instead.
    private static func titleFor(_ dir: URL) -> String? {
        let name = dir.lastPathComponent
        return name.lowercased().hasPrefix("program files") ? nil : name
    }

    /// Translate a device URL under drive_c into the guest's C:\ path.
    /// Returns nil for anything outside the prefix, which the guest could not open anyway.
    static func windowsPath(for url: URL, root: URL? = nil) -> String? {
        guard let root = root ?? driveC else { return nil }
        let rootPath = root.standardizedFileURL.path
        let filePath = url.standardizedFileURL.path
        guard filePath.hasPrefix(rootPath + "/") else { return nil }
        let relative = String(filePath.dropFirst(rootPath.count + 1))
        return "C:\\" + relative.replacingOccurrences(of: "/", with: "\\")
    }
}

// MARK: - Fixed-base images

/// Gives a relocation-stripped 64-bit exe that wants a base below 4 GB a
/// relocation table, so Wine can load it where iOS lets it.
///
/// iOS reserves the low 4 GB of every process (__PAGEZERO), so an image linked
/// /FIXED at, say, 0x37000000 (Crysis's Bin64\Crysis64.exe) cannot be placed
/// at its base, and with IMAGE_FILE_RELOCS_STRIPPED Wine's loader refuses to
/// move it: "failed to create main module ... c0000018".
///
/// An x64 image's absolute pointers into itself live in its data sections as
/// aligned 64-bit values (vtables, CRT init tables, TLS and load-config
/// directories); code reaches the image RIP-relatively. Every such value that
/// falls inside the image is taken as a DIR64 fixup. Checked against the real
/// .reloc of 41 MSVC and mingw x64 binaries: no fixup missed outside code, no
/// false one at the binaries' own bases; a handful of data constants do alias
/// a base as low as 0x37000000 in images of several MB. Images with a writable
/// executable section (packers) are left alone.
///
/// The file gets a ".mreloc" section and loses the stripped flag; the original
/// is kept next to it with `backupSuffix` appended.
enum FixedBaseImage {
    static let backupSuffix = ".bcd-orig"

    /// A line for the log when the file was changed or deliberately left
    /// alone, nil when it is not a candidate.
    static func prepare(_ url: URL) -> String? {
        // Most exes are not candidates: decide from the headers alone.
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        let head = [UInt8]((try? handle.read(upToCount: 4096)) ?? Data())
        try? handle.close()
        guard isCandidate(head) else { return nil }

        guard let data = try? Data(contentsOf: url) else { return nil }
        let name = url.lastPathComponent
        switch rebuild([UInt8](data)) {
        case .failure(let why):
            return "\(name): fixed-base image below 4 GB, not relocatable here (\(why))"
        case .success(let (patched, count, base)):
            let backup = url.appendingPathExtension(String(backupSuffix.dropFirst()))
            do {
                if !FileManager.default.fileExists(atPath: backup.path) {
                    try FileManager.default.copyItem(at: url, to: backup)
                }
                try Data(patched).write(to: url, options: .atomic)
            } catch {
                return "\(name): could not write the relocated copy (\(error.localizedDescription))"
            }
            return String(format: "%@: fixed base 0x%llx is below the iOS floor -- added %ld rebuilt relocations "
                          + "so Wine can load it elsewhere (original kept as %@)",
                          name, base, count, backup.lastPathComponent)
        }
    }

    private enum Outcome { case success(([UInt8], Int, UInt64)), failure(String) }

    private static func u16(_ b: [UInt8], _ o: Int) -> Int { Int(b[o]) | Int(b[o + 1]) << 8 }
    private static func u32(_ b: [UInt8], _ o: Int) -> Int { u16(b, o) | u16(b, o + 2) << 16 }
    private static func u64(_ b: [UInt8], _ o: Int) -> UInt64 { UInt64(u32(b, o)) | UInt64(u32(b, o + 4)) << 32 }
    private static func put16(_ b: inout [UInt8], _ o: Int, _ v: Int) { b[o] = UInt8(v & 0xff); b[o + 1] = UInt8(v >> 8 & 0xff) }
    private static func put32(_ b: inout [UInt8], _ o: Int, _ v: Int) { put16(&b, o, v & 0xffff); put16(&b, o + 2, v >> 16 & 0xffff) }
    private static func alignUp(_ x: Int, _ a: Int) -> Int { a > 0 ? (x + a - 1) / a * a : x }

    /// PE32+, relocations stripped, preferred base below 4 GB.
    private static func isCandidate(_ b: [UInt8]) -> Bool {
        guard b.count >= 0x40, b[0] == 0x4d, b[1] == 0x5a else { return false }
        let pe = u32(b, 0x3c)
        guard pe + 24 + 112 + 6 * 8 <= b.count, b[pe] == 0x50, b[pe + 1] == 0x45,
              b[pe + 2] == 0, b[pe + 3] == 0 else { return false }
        let opt = pe + 24
        guard u16(b, opt) == 0x20b, u16(b, pe + 22) & 0x0001 != 0 else { return false }
        return u64(b, opt + 24) < 0x1_0000_0000 && u32(b, opt + 108) > 5
    }

    private static func rebuild(_ input: [UInt8]) -> Outcome {
        var b = input
        let pe = u32(b, 0x3c), nsec = u16(b, pe + 6), optsz = u16(b, pe + 20), chars = u16(b, pe + 22)
        let opt = pe + 24
        let base = u64(b, opt + 24)
        let salign = u32(b, opt + 32), falign = u32(b, opt + 36)
        let sizeImage = u32(b, opt + 56), sizeHeaders = u32(b, opt + 60)
        let dd = opt + 112
        let table = opt + optsz
        let tableEnd = table + 40 * nsec
        guard tableEnd + 40 <= b.count else { return .failure("truncated headers") }

        struct Section { let name: String; let vsize, va, rsize, rptr, flags: Int }
        var sections: [Section] = []
        for i in 0..<nsec {
            let o = table + 40 * i
            let name = String(decoding: b[o..<o + 8].prefix { $0 != 0 }, as: UTF8.self)
            sections.append(Section(name: name, vsize: u32(b, o + 8), va: u32(b, o + 12),
                                    rsize: u32(b, o + 16), rptr: u32(b, o + 20), flags: u32(b, o + 36)))
        }
        let execute = 0x2000_0000, write = 0x8000_0000, discardable = 0x0200_0000
        if sections.contains(where: { $0.flags & execute != 0 && $0.flags & write != 0 }) {
            return .failure("a section is both writable and executable, so it is probably packed")
        }

        // Aligned 64-bit values inside the image, in data sections only.
        var rvas: [Int] = []
        let lo = base, hi = base + UInt64(sizeImage)
        for s in sections where s.flags & (execute | discardable) == 0 && s.name != ".reloc" {
            var n = s.vsize > 0 ? min(s.vsize, s.rsize) : s.rsize
            n = min(n, b.count - s.rptr)
            var i = 0
            while i + 8 <= n {
                let v = u64(b, s.rptr + i)
                if v >= lo && v < hi { rvas.append(s.va + i) }
                i += 8
            }
        }

        // IMAGE_BASE_RELOCATION blocks of IMAGE_REL_BASED_DIR64 entries.
        var rel: [UInt8] = []
        var page = -1
        var entries: [Int] = []
        func flush() {
            guard page >= 0 else { return }
            if entries.count % 2 == 1 { entries.append(0) }
            var block = [UInt8](repeating: 0, count: 8 + 2 * entries.count)
            put32(&block, 0, page)
            put32(&block, 4, block.count)
            for (k, e) in entries.enumerated() { put16(&block, 8 + 2 * k, e) }
            rel += block
        }
        for r in rvas.sorted() {
            if r & ~0xfff != page { flush(); page = r & ~0xfff; entries = [] }
            entries.append(0xA000 | (r & 0xfff))
        }
        flush()

        // Room for one more section header, which must be unused.
        let firstRaw = sections.filter { $0.rsize > 0 }.map(\.rptr).min() ?? sizeHeaders
        guard tableEnd + 40 <= min(sizeHeaders, firstRaw),
              b[tableEnd..<tableEnd + 40].allSatisfy({ $0 == 0 }) else {
            return .failure("no room for another section header")
        }

        let newVA = sections.map { alignUp($0.va + max($0.vsize, $0.rsize), salign) }.max() ?? alignUp(sizeHeaders, salign)
        let newRaw = alignUp(b.count, falign)
        let rawSize = alignUp(rel.count, falign)
        b += [UInt8](repeating: 0, count: newRaw - b.count)
        b += rel
        b += [UInt8](repeating: 0, count: rawSize - rel.count)

        let h = tableEnd
        for (k, c) in Array(".mreloc".utf8).enumerated() { b[h + k] = c }
        put32(&b, h + 8, rel.count)
        put32(&b, h + 12, newVA)
        put32(&b, h + 16, rawSize)
        put32(&b, h + 20, newRaw)
        put32(&b, h + 36, 0x4200_0040)          // initialized data, discardable, read
        put16(&b, pe + 6, nsec + 1)
        put16(&b, pe + 22, chars & ~0x0001)      // no longer RELOCS_STRIPPED
        put32(&b, opt + 56, alignUp(newVA + rel.count, salign))
        put32(&b, opt + 64, 0)                   // checksum: not verified for executables
        put32(&b, dd + 5 * 8, newVA)
        put32(&b, dd + 5 * 8 + 4, rel.count)
        return .success((b, rvas.count, base))
    }
}
