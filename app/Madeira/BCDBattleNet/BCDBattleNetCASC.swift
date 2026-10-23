// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

/// CASC V7 local indexes and data envelopes, from the published TACT format.
/// lookup3 is Bob Jenkins' public-domain hash; all arithmetic wraps at 32 bits.
enum BCDBnetCASC {
    private final class Discard: BCDHTTPConsumer {
        var received: Int64 = 0
        func begin(at offset: Int64) throws { received = offset }
        func receive(_ data: Data) throws { received += Int64(data.count) }
    }
    private static func receipt(_ build: BCDBnetBuild) -> Data {
        Data((build.product + "\n" + build.build + "\n").utf8)
    }
    /// Recover a completed atomic move if saving the library record was interrupted.
    static func committed(_ build: BCDBnetBuild, at folder: URL) -> Bool {
        (try? Data(contentsOf: folder.appendingPathComponent(".bcd-battlenet-build"))) == receipt(build)
            && FileManager.default.fileExists(atPath: folder.appendingPathComponent(BCDBnetNGDP.program(build.product)).path)
    }
    static func integer(_ value: UInt64, _ count: Int, little: Bool = true) -> Data {
        Data((0..<count).map { UInt8(truncatingIfNeeded: value >> ((little ? $0 : count - 1 - $0) * 8)) })
    }
    static func hash(_ data: Data, _ pc: UInt32 = 0, _ pb: UInt32 = 0) -> (UInt32, UInt32) {
        let bytes = [UInt8](data)
        var a = UInt32(0xdeadbeef) &+ UInt32(bytes.count) &+ pc, b = a, c = a &+ pb
        func rot(_ v: UInt32, _ n: UInt32) -> UInt32 { (v << n) | (v >> (32 - n)) }
        func word(_ at: Int) -> UInt32 { (0..<4).reduce(0) { $0 | (UInt32(bytes[at + $1]) << ($1 * 8)) } }
        var at = 0
        while bytes.count - at > 12 {
            a &+= word(at); b &+= word(at + 4); c &+= word(at + 8)
            a &-= c; a ^= rot(c, 4); c &+= b
            b &-= a; b ^= rot(a, 6); a &+= c
            c &-= b; c ^= rot(b, 8); b &+= a
            a &-= c; a ^= rot(c, 16); c &+= b
            b &-= a; b ^= rot(a, 19); a &+= c
            c &-= b; c ^= rot(b, 4); b &+= a
            at += 12
        }
        guard at < bytes.count else { return (c, b) }
        for i in 0..<bytes.count - at {
            let value = UInt32(bytes[at + i]) << ((i % 4) * 8)
            if i < 4 { a &+= value } else if i < 8 { b &+= value } else { c &+= value }
        }
        c ^= b; c &-= rot(b, 14); a ^= c; a &-= rot(c, 11); b ^= a; b &-= rot(a, 25)
        c ^= b; c &-= rot(b, 16); a ^= c; a &-= rot(c, 4); b ^= a; b &-= rot(a, 14); c ^= b; c &-= rot(b, 24)
        return (c, b)
    }
    static func envelope(key: String, size: Int64, offset: UInt64) throws -> Data {
        var data = Data(try BCDBnetNGDP.keyBytes(key).reversed())
        guard size > 0, size + 30 < 1 << 30 else { throw BCDBattleNetError.format("casc-size") }
        data.append(integer(UInt64(size + 30), 4)); data.append(contentsOf: [0, 0])
        data.append(integer(UInt64(hash(data, 0x3d6be971).0), 4))
        let table: [UInt32] = [0x049396b8, 0x72a82a9b, 0xee626cca, 0x9917754f, 0x15de40b1, 0xf5a8a9b6, 0x421eac7e, 0xa9d55c9a,
                                0x317fd40c, 0x04faf80d, 0x3d6be971, 0x52933cfd, 0x27f64b7d, 0xc6f5c11b, 0xd5757e3a, 0x6c388745]
        var accum = [UInt8](repeating: 0, count: 4)
        for (i, byte) in data.enumerated() { accum[Int((offset + UInt64(i)) & 3)] ^= byte }
        let next = UInt32(truncatingIfNeeded: offset + 30)
        let mask = integer(UInt64(table[Int(next & 15)] ^ next), 4)
        for i in 0..<4 { let j = Int((offset + 26 + UInt64(i)) & 3); data.append(accum[j] ^ mask[j]) }
        return data
    }
    struct Entry { var key: Data; var archive: Int; var offset: Int64; var size: Int64
        var bucket: Int { let x = key.prefix(9).reduce(UInt8(0), ^); return Int((x ^ (x >> 4)) & 15) }
        var bytes: Data {
            var out = Data(key.prefix(9))
            out.append(integer((UInt64(archive) << 30) | UInt64(offset), 5, little: false))
            out.append(integer(UInt64(size), 4))
            return out
        }
    }
    static func index(bucket: Int, entries: [Entry]) -> Data {
        var header = Data([7, 0, UInt8(bucket), 0, 4, 5, 9, 30])
        header.append(integer(1 << 38, 8))
        var result = integer(UInt64(header.count), 4)
        result.append(integer(UInt64(hash(header).0), 4)); result.append(header)
        result.append(Data(repeating: 0, count: 8))
        let sorted = entries.sorted { $0.key.lexicographicallyPrecedes($1.key) }
        var bytes = Data(), pc: UInt32 = 0, pb: UInt32 = 0
        for entry in sorted { let row = entry.bytes; (pc, pb) = hash(row, pc, pb); bytes.append(row) }
        result.append(integer(UInt64(bytes.count), 4)); result.append(integer(UInt64(pc), 4)); result.append(bytes)
        let length = max(0x30000, ((result.count + 4095) / 4096) * 4096 + 0xc000)
        result.append(Data(repeating: 0, count: length - result.count))
        return result
    }

    /// The queue downloaded and verified every BLTE payload. Assemble into a separate folder,
    /// then atomically commit it. Payloads stay intact until the commit, allowing a cancelled
    /// assembly to restart without re-downloading; reserve space for both copies up front.
    static func assemble(_ build: BCDBnetBuild, stage: URL) throws -> URL {
        let fm = FileManager.default, output = stage.appendingPathComponent("installed", isDirectory: true)
        if fm.fileExists(atPath: output.path) { try fm.removeItem(at: output) }
        let dataFolder = output.appendingPathComponent("Data/data", isDirectory: true)
        try fm.createDirectory(at: dataFolder, withIntermediateDirectories: true)
        var entries: [Entry] = [], seen = Set<Data>(), archive = -1, position: Int64 = 1 << 30
        var file: FileHandle?
        defer { try? file?.close() }
        for payload in build.payloads {
            try Task.checkCancellation()
            guard BCDBnetNGDP.key(payload.key), payload.size >= 8, payload.size + 30 < 1 << 30 else { throw BCDBattleNetError.format("payload") }
            let key = try BCDBnetNGDP.keyBytes(payload.key), short = Data(key.prefix(9))
            guard seen.insert(short).inserted else { throw BCDBattleNetError.unsupported("colliding CASC keys") }
            if position + payload.size + 30 >= 1 << 30 {
                try file?.synchronize(); try file?.close(); archive += 1; position = 0
                guard archive < 256 else { throw BCDBattleNetError.unsupported("install over 256 GiB") }
                let url = dataFolder.appendingPathComponent(String(format: "data.%03d", archive))
                guard fm.createFile(atPath: url.path, contents: nil) else { throw BCDContentError.diskFull }
                file = try FileHandle(forWritingTo: url)
            }
            let header = try envelope(key: payload.key, size: payload.size, offset: (UInt64(archive) << 30) | UInt64(position))
            try file!.write(contentsOf: header)
            let raw = stage.appendingPathComponent("payload/" + payload.key.prefix(2) + "/" + payload.key)
            let input = try FileHandle(forReadingFrom: raw)
            do {
                let verifier = BCDBnetVerifiedConsumer(target: Discard(), key: payload.key, size: payload.size)
                try verifier.begin(at: 0)
                var copied: Int64 = 0
                while let bytes = try input.read(upToCount: 1 << 20), !bytes.isEmpty {
                    try Task.checkCancellation()
                    copied += Int64(bytes.count)
                    guard copied <= payload.size else { throw BCDContentError.checkFailed }
                    try verifier.receive(bytes)
                    try file!.write(contentsOf: bytes)
                }
                guard copied == payload.size else { throw BCDContentError.checkFailed }
                try verifier.finish()
                try input.close()
            } catch { try? input.close(); throw error }
            entries.append(Entry(key: key, archive: archive, offset: position, size: payload.size + 30))
            position += payload.size + 30
        }
        try file?.synchronize(); try file?.close(); file = nil
        for bucket in 0..<16 {
            let bytes = index(bucket: bucket, entries: entries.filter { $0.bucket == bucket })
            try bytes.write(to: dataFolder.appendingPathComponent(String(format: "%02x00000001.idx", bucket)), options: .atomic)
        }
        for (key, data) in [(build.build, build.buildConfig), (build.cdn, build.cdnConfig)] {
            _ = try BCDBnetNGDP.config(data, key: key)
            let url = output.appendingPathComponent("Data/config/" + key.prefix(2) + "/" + key.dropFirst(2).prefix(2) + "/" + key)
            try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url, options: .atomic)
        }
        var paths = Set<String>(), folding = BCDPathFolding()
        for entry in build.install {
            try Task.checkCancellation()
            let prefix = BCDBnetNGDP.client(build.product) + "/"
            let relative = try folding.file(BCDContentPaths.components(entry.path.hasPrefix(prefix) ? entry.path : prefix + entry.path))
            guard paths.insert(BCDContentPaths.fold(relative)).inserted, BCDBnetNGDP.key(entry.encoded), entry.size <= 256 << 20 else {
                throw BCDBattleNetError.format("install-path")
            }
            let raw = stage.appendingPathComponent("payload/" + entry.encoded.prefix(2) + "/" + entry.encoded)
            let data = try BCDBnetBLTE.decode(Data(contentsOf: raw, options: .mappedIfSafe), key: entry.encoded, limit: Int(entry.size))
            guard data.count == entry.size, BCDBnetNGDP.md5(data) == entry.content else { throw BCDContentError.checkFailed }
            let target = output.appendingPathComponent(relative)
            try fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: target, options: .atomic)
        }
        let info = "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|CDN Path!STRING:0|CDN Hosts!STRING:0|Tags!STRING:0|Version!STRING:0|Product!STRING:0\n" +
            "\(build.region)|1|\(build.build)|\(build.cdn)|\(build.path)|\(build.hosts.joined(separator: " "))|Windows x86_64 \(build.locale) text? \(build.locale) speech?|\(build.version)|\(build.product)\n"
        try Data(info.utf8).write(to: output.appendingPathComponent(".build.info"), options: .atomic)
        guard fm.fileExists(atPath: output.appendingPathComponent(BCDBnetNGDP.program(build.product)).path) else {
            throw BCDBattleNetError.format("missing-program")
        }
        try receipt(build).write(to: output.appendingPathComponent(".bcd-battlenet-build"), options: .atomic)
        return output
    }
}
