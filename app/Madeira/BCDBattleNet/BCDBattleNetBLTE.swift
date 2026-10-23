// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import CryptoKit

enum BCDBnetBLTE {
    struct Frame { var encoded: Int; var decoded: Int; var md5: String }
    static func frames(_ header: Data) throws -> [Frame] {
        var r = BCDBnetReader(data: header)
        guard try r.bytes(4) == Data("BLTE".utf8), try r.number(4) == UInt64(header.count),
              try r.number(1) == 15 else { throw BCDBattleNetError.format("blte-header") }
        let count = try Int(r.number(3))
        guard count > 0, count <= 100_000, r.remaining == count * 24 else { throw BCDBattleNetError.format("blte-frames") }
        return try (0..<count).map { _ in
            let encoded = try Int(r.number(4)), decoded = try Int(r.number(4)), hash = try r.bytes(16)
            guard encoded > 0, encoded <= 1 << 30, decoded <= 1 << 30 else { throw BCDBattleNetError.format("blte-size") }
            return Frame(encoded: encoded, decoded: decoded, md5: BCDBnetNGDP.hex(hash))
        }
    }
    /// Small manifests and loose client files only. CASC payloads remain encoded on disk.
    static func decode(_ data: Data, key: String, limit: Int) throws -> Data {
        guard data.count >= 9, data.prefix(4) == Data("BLTE".utf8) else { throw BCDBattleNetError.format("blte") }
        var r = BCDBnetReader(data: data); _ = try r.bytes(4)
        let header = try Int(r.number(4))
        guard header == 0 || (header >= 12 && header <= 4 << 20 && header <= data.count) else { throw BCDBattleNetError.format("blte-header") }
        guard BCDBnetNGDP.md5(header == 0 ? data : data.prefix(header)) == key else { throw BCDContentError.checkFailed }
        let frames = header == 0 ? [Frame(encoded: data.count - 8, decoded: -1, md5: "")] : try frames(data.prefix(header))
        r.offset = max(8, header)
        var result = Data()
        for frame in frames {
            try Task.checkCancellation()
            let block = try r.bytes(frame.encoded)
            if !frame.md5.isEmpty, BCDBnetNGDP.md5(block) != frame.md5 { throw BCDContentError.checkFailed }
            let body = Data(block.dropFirst()), decoded: Data
            switch block.first {
            case 78: decoded = body
            case 90: decoded = try BCDZlib.inflate(body, limit: limit - result.count)
            default: throw BCDBattleNetError.unsupported("BLTE codec")
            }
            guard decoded.count <= limit - result.count, frame.decoded < 0 || decoded.count == frame.decoded else {
                throw BCDContentError.checkFailed
            }
            result.append(decoded)
        }
        guard r.remaining == 0 else { throw BCDContentError.checkFailed }
        return result
    }
}

/// Validates the EKey (header MD5) and every encoded frame while forwarding bytes to the
/// content engine. Retains only the header and a hash state, never a complete game payload.
final class BCDBnetVerifiedConsumer: BCDHTTPConsumer {
    let target: BCDHTTPConsumer
    let key: String
    let size: Int64
    private var header = Data()
    private var headerSize: Int?
    private var frames: [BCDBnetBLTE.Frame]?
    private var frame = 0
    private var frameBytes = 0
    private var hasher = Insecure.MD5()
    private(set) var received: Int64 = 0
    init(target: BCDHTTPConsumer, key: String, size: Int64) { self.target = target; self.key = key; self.size = size }
    func begin(at offset: Int64) throws {
        if offset == 0 { header = Data(); headerSize = nil; frames = nil; frame = 0; frameBytes = 0; hasher = Insecure.MD5(); received = 0 }
        guard offset == received else { throw BCDContentError.checkFailed }
        try target.begin(at: offset)
    }
    func receive(_ data: Data) throws {
        guard Int64(data.count) <= size - received else { throw BCDContentError.tooLarge }
        try verify(data)
        try target.receive(data)
        received += Int64(data.count)
    }
    private func verify(_ data: Data) throws {
        var position = 0
        if frames == nil {
            let needed = (headerSize ?? 8) - header.count
            let n = min(needed, data.count)
            header.append(data.prefix(n)); position += n
            if header.count == 8, headerSize == nil {
                guard header.prefix(4) == Data("BLTE".utf8) else { throw BCDContentError.checkFailed }
                var r = BCDBnetReader(data: header); r.offset = 4
                let length = try Int(r.number(4))
                guard length == 0 || (length >= 12 && length <= 4 << 20 && Int64(length) < size) else { throw BCDContentError.checkFailed }
                headerSize = length
                if length == 0 { frames = []; hasher.update(data: header) }
            }
            if let length = headerSize, length > 0 {
                let n = min(length - header.count, data.count - position)
                header.append(data.subdata(in: position..<position + n)); position += n
                if header.count == length {
                    guard BCDBnetNGDP.md5(header) == key else { throw BCDContentError.checkFailed }
                    let made = try BCDBnetBLTE.frames(header)
                    guard made.reduce(Int64(length), { $0 + Int64($1.encoded) }) == size else { throw BCDContentError.checkFailed }
                    frames = made; header = Data()
                }
            }
        }
        guard let frames else { return }
        if frames.isEmpty { hasher.update(data: data.dropFirst(position)); return }
        while position < data.count {
            guard frame < frames.count else { throw BCDContentError.checkFailed }
            let n = min(frames[frame].encoded - frameBytes, data.count - position)
            hasher.update(data: data.subdata(in: position..<position + n)); position += n; frameBytes += n
            if frameBytes == frames[frame].encoded {
                guard BCDBnetNGDP.hex(Data(hasher.finalize())) == frames[frame].md5 else { throw BCDContentError.checkFailed }
                frame += 1; frameBytes = 0; hasher = Insecure.MD5()
            }
        }
    }
    func finish() throws {
        guard received == size, let frames else { throw BCDContentError.checkFailed }
        if frames.isEmpty {
            guard BCDBnetNGDP.hex(Data(hasher.finalize())) == key else { throw BCDContentError.checkFailed }
        } else if frame != frames.count { throw BCDContentError.checkFailed }
    }
}
