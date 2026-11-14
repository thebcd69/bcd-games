// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. See LICENSE.
// Taken from https://github.com/tylerkaska112/Porthole (commit 99ec315, app/Sources/Amazon/AmazonManifest.swift) under GPL-3.0-or-later
// with the Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: none in the code; this header. See THIRD-PARTY-NOTICES.md.
//
// The manifest of an Amazon Games build ("manifest.proto" next to the build's files). It comes from the same download system Twitch's Fuel launcher used:
// a 4-byte big-endian header length, a protobuf header (how the rest is compressed, a hash, an RSA signature), then the protobuf manifest, LZMA-compressed.
// The signature is checked against Amazon's public key before a single field is trusted: the file list decides what is written to the device.
//
// The protobuf messages are tiny and fixed (tv.twitch.fuel.sds), so they are read by hand instead of pulling in a protobuf library:
//   ManifestHeader { 1 compression { 1 algorithm }, 2 hash { 1 algorithm, 2 value }, 3 signature { 1 algorithm, 2 value } }
//   Manifest { 1 repeated Package { 1 name, 2 repeated File, 3 repeated Dir } }
//   File { 1 path, 2 mode, 3 size, 4 created, 5 hash { 1 algorithm, 2 value }, 6 hidden, 7 system }     Dir { 1 path, 2 mode }

import Foundation
import Security
import Compression

enum AmazonManifestError: Error, LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case .invalid(let message) = self { return message }; return nil }
}

struct AmazonManifestFile: Equatable, Sendable {
    /// Windows-style path inside the game folder ("Bin\\game.exe").
    let path: String
    let size: Int64
    /// "sha256" or "shake128" (the second is not used by any build seen; such a file is refused).
    let hashAlgorithm: String
    let hash: Data
    var hashHex: String { hash.map { String(format: "%02x", $0) }.joined() }
}

struct AmazonManifest: Equatable, Sendable {
    var files: [AmazonManifestFile] = []
    var directories: [String] = []

    /// The key Amazon signs manifests with (the same one every open-source client for this system carries), as the base64 of its X.509 SubjectPublicKeyInfo.
    static let amazonPublicKey = Data(base64Encoded: """
        MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA6fSRMUi3VpTtv9P4+KvMAcAIP4SYbTQfB1ns7vyUjsj8nrF2lGNtQTtGLnrNmM2ElZ2R7VmQtNiRtPMxToIW\
        Rajin0H0OyzGrHA8P6w96Mj4q1JeORCzJeVFgLOBClCCMmB+5bJBWnJcq/sEMwu9gGynCeiYNLt7ZMVpL1GOsNjl+yLk7OMMGpMj1JWCVFfgYE9Lud1QZJllFAWhRBoT\
        wTctAUZTikObFUoBm+KEiCsKIcay4WOybvJwxTNBUl2GL8c+ihrT2ntLPpb9aIJE/gXU3Ihl5oXe/0P/QN0CRu/ybXWLiGzIYqKIok4nepkdo8V3gWR55K801pOuck0B\
        awIDAQAB
        """.replacingOccurrences(of: "\n", with: ""))!

    /// Reads, decompresses and verifies a manifest file. `publicKey` is only different in tests.
    static func parse(_ content: Data, publicKey: Data = amazonPublicKey) throws -> AmazonManifest {
        let bytes = [UInt8](content)
        guard bytes.count > 8 else { throw AmazonManifestError.invalid("Amazon's manifest is too short.") }
        let headerSize = Int(bytes[0]) << 24 | Int(bytes[1]) << 16 | Int(bytes[2]) << 8 | Int(bytes[3])
        guard headerSize > 0, 4 + headerSize <= bytes.count else { throw AmazonManifestError.invalid("Amazon's manifest header is damaged.") }
        let header = try Header(Array(bytes[4 ..< 4 + headerSize]))
        let body = Array(bytes[(4 + headerSize)...])
        let raw: [UInt8]
        switch header.compression {
        case 0: raw = body
        case 1: raw = try decompress(body)
        default: throw AmazonManifestError.invalid("Amazon's manifest uses a compression this version does not know.")
        }
        guard header.signatureAlgorithm == 0 else { throw AmazonManifestError.invalid("Amazon's manifest uses a signature this version does not know.") }
        guard verify(Data(raw), signature: header.signature, publicKey: publicKey) else {
            throw AmazonManifestError.invalid("Amazon's manifest failed its signature check, so nothing was downloaded.")
        }
        return try AmazonManifest(raw)
    }

    // MARK: Protobuf

    private struct Reader {
        let bytes: [UInt8]
        var position = 0
        init(_ bytes: [UInt8]) { self.bytes = bytes }
        var atEnd: Bool { position >= bytes.count }

        mutating func varint() throws -> UInt64 {
            var result: UInt64 = 0, shift: UInt64 = 0
            while true {
                guard position < bytes.count, shift < 64 else { throw AmazonManifestError.invalid("Amazon's manifest is damaged.") }
                let byte = bytes[position]; position += 1
                result |= UInt64(byte & 0x7F) << shift
                if byte & 0x80 == 0 { return result }
                shift += 7
            }
        }
        /// The next field: its number and either a number (varint / fixed) or its bytes (length-delimited).
        mutating func field() throws -> (number: Int, value: UInt64, data: [UInt8]?) {
            let key = try varint()
            let number = Int(key >> 3)
            switch key & 7 {
            case 0: return (number, try varint(), nil)
            case 1:
                guard position + 8 <= bytes.count else { throw AmazonManifestError.invalid("Amazon's manifest is damaged.") }
                position += 8; return (number, 0, nil)
            case 2:
                let length = Int(try varint())
                guard length >= 0, position + length <= bytes.count else { throw AmazonManifestError.invalid("Amazon's manifest is damaged.") }
                defer { position += length }
                return (number, 0, Array(bytes[position ..< position + length]))
            case 5:
                guard position + 4 <= bytes.count else { throw AmazonManifestError.invalid("Amazon's manifest is damaged.") }
                position += 4; return (number, 0, nil)
            default: throw AmazonManifestError.invalid("Amazon's manifest is damaged.")
            }
        }
    }

    private struct Header {
        var compression = 0
        var signatureAlgorithm = 0
        var signature = Data()
        init(_ bytes: [UInt8]) throws {
            var reader = Reader(bytes)
            while !reader.atEnd {
                let f = try reader.field()
                switch (f.number, f.data) {
                case (1, let data?):
                    var inner = Reader(data)
                    while !inner.atEnd { let g = try inner.field(); if g.number == 1, g.data == nil { compression = Int(g.value) } }
                case (3, let data?):
                    var inner = Reader(data)
                    while !inner.atEnd {
                        let g = try inner.field()
                        if g.number == 1, g.data == nil { signatureAlgorithm = Int(g.value) }
                        if g.number == 2, let d = g.data { signature = Data(d) }
                    }
                default: break
                }
            }
        }
    }

    private init(_ bytes: [UInt8]) throws {
        var reader = Reader(bytes)
        var firstPackage = true
        while !reader.atEnd {
            let f = try reader.field()
            guard f.number == 1, let package = f.data else { continue }
            // Only the first package is the game (Nile and the Twitch client read only that one too).
            guard firstPackage else { continue }
            firstPackage = false
            var inner = Reader(package)
            while !inner.atEnd {
                let g = try inner.field()
                guard let data = g.data else { continue }
                if g.number == 2 { files.append(try Self.file(data)) }
                else if g.number == 3 {
                    var dir = Reader(data)
                    while !dir.atEnd { let h = try dir.field(); if h.number == 1, let p = h.data, let s = String(bytes: p, encoding: .utf8) { directories.append(s) } }
                }
            }
        }
        guard !files.isEmpty else { throw AmazonManifestError.invalid("Amazon's manifest lists no files.") }
    }

    private static func file(_ bytes: [UInt8]) throws -> AmazonManifestFile {
        var reader = Reader(bytes)
        var path = "", size: Int64 = 0, algorithm = 0, hash = Data()
        while !reader.atEnd {
            let f = try reader.field()
            switch (f.number, f.data) {
            case (1, let data?): path = String(bytes: data, encoding: .utf8) ?? ""
            case (3, nil): size = Int64(bitPattern: f.value)
            case (5, let data?):
                var inner = Reader(data)
                while !inner.atEnd {
                    let g = try inner.field()
                    if g.number == 1, g.data == nil { algorithm = Int(g.value) }
                    if g.number == 2, let d = g.data { hash = Data(d) }
                }
            default: break
            }
        }
        guard !path.isEmpty, size >= 0, !hash.isEmpty else { throw AmazonManifestError.invalid("Amazon's manifest has a damaged file entry.") }
        return AmazonManifestFile(path: path, size: size, hashAlgorithm: algorithm == 0 ? "sha256" : "shake128", hash: hash)
    }

    // MARK: Compression and signature

    /// LZMA as the manifest is wrapped: an .xz file (what Python's lzma.compress writes, and what the Twitch tools read). The older bare .lzma format is not supported:
    /// it often does not state its size, which the decoder needs, and no build seen uses it.
    static func decompress(_ input: [UInt8]) throws -> [UInt8] {
        guard input.starts(with: [0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00]) else {
            throw AmazonManifestError.invalid("Amazon's manifest is packed in a way this version does not read.")
        }
        // The system's decoder reads .xz. The output size is not stored up front, so the buffer grows until it fits.
        var capacity = max(input.count * 8, 1 << 20)
        while capacity <= 1 << 30 {
            var out = [UInt8](repeating: 0, count: capacity)
            let n = input.withUnsafeBufferPointer { src in
                out.withUnsafeMutableBufferPointer { dst in
                    compression_decode_buffer(dst.baseAddress!, capacity, src.baseAddress!, input.count, nil, COMPRESSION_LZMA)
                }
            }
            if n > 0 && n < capacity { return Array(out[0 ..< n]) }
            capacity *= 4
        }
        throw AmazonManifestError.invalid("Amazon's manifest would not unpack.")
    }

    /// RSA PKCS#1 v1.5 over SHA-256, with the key given as X.509 SubjectPublicKeyInfo.
    static func verify(_ message: Data, signature: Data, publicKey spki: Data) -> Bool {
        guard let pkcs1 = rsaKeyBody(fromSPKI: [UInt8](spki)) else { return false }
        let attributes: [CFString: Any] = [kSecAttrKeyType: kSecAttrKeyTypeRSA, kSecAttrKeyClass: kSecAttrKeyClassPublic]
        guard let key = SecKeyCreateWithData(Data(pkcs1) as CFData, attributes as CFDictionary, nil) else { return false }
        return SecKeyVerifySignature(key, .rsaSignatureMessagePKCS1v15SHA256, message as CFData, signature as CFData, nil)
    }

    /// The RSAPublicKey inside a SubjectPublicKeyInfo: SEQUENCE { SEQUENCE { algorithm }, BIT STRING { 0, RSAPublicKey } }.
    static func rsaKeyBody(fromSPKI bytes: [UInt8]) -> [UInt8]? {
        func element(at i: Int) -> (tag: UInt8, body: Range<Int>)? {
            guard i + 2 <= bytes.count else { return nil }
            let tag = bytes[i]
            var length = Int(bytes[i + 1]), start = i + 2
            if length & 0x80 != 0 {
                let n = length & 0x7F
                guard n > 0, n <= 4, start + n <= bytes.count else { return nil }
                length = 0
                for k in 0..<n { length = length << 8 | Int(bytes[start + k]) }
                start += n
            }
            guard start + length <= bytes.count else { return nil }
            return (tag, start ..< start + length)
        }
        guard let outer = element(at: 0), outer.tag == 0x30, let algorithm = element(at: outer.body.lowerBound), algorithm.tag == 0x30,
              let bits = element(at: algorithm.body.upperBound), bits.tag == 0x03, bits.body.count > 1, bytes[bits.body.lowerBound] == 0 else { return nil }
        return Array(bytes[(bits.body.lowerBound + 1) ..< bits.body.upperBound])
    }
}

// MARK: - fuel.json

/// What a game's own fuel.json says about starting it: { "SchemaVersion": 2, "Main": { "Command": "Bin\\game.exe", "Args": [...], "WorkingSubdirOverride": "Bin" } }.
/// The file is written by hand by the game's makers, so comments and trailing commas (which Amazon's own launcher accepts) are tolerated.
struct AmazonLaunch: Equatable {
    var command: String
    var arguments: [String]
    var workingSubdirectory: String?

    static func parse(_ data: Data) -> AmazonLaunch? {
        var text = String(decoding: data, as: UTF8.self)
        if text.hasPrefix("\u{FEFF}") { text.removeFirst() }
        let cleaned = Self.strip(text)
        guard let json = try? JSONSerialization.jsonObject(with: Data(cleaned.utf8)) as? [String: Any],
              let main = json["Main"] as? [String: Any], let command = main["Command"] as? String, !command.isEmpty else { return nil }
        let sub = (main["WorkingSubdirOverride"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        return AmazonLaunch(command: command, arguments: (main["Args"] as? [String]) ?? [], workingSubdirectory: sub)
    }

    /// Takes out // and /* */ comments and commas before a closing bracket, leaving text inside strings alone.
    static func strip(_ text: String) -> String {
        let c = Array(text.unicodeScalars)
        var out = String.UnicodeScalarView()
        var i = 0, inString = false
        while i < c.count {
            let ch = c[i]
            if inString {
                out.append(ch)
                if ch == "\\", i + 1 < c.count { out.append(c[i + 1]); i += 2; continue }
                if ch == "\"" { inString = false }
                i += 1; continue
            }
            if ch == "\"" { inString = true; out.append(ch); i += 1; continue }
            if ch == "/", i + 1 < c.count, c[i + 1] == "/" { while i < c.count, c[i] != "\n" { i += 1 }; continue }
            if ch == "/", i + 1 < c.count, c[i + 1] == "*" {
                i += 2
                while i + 1 < c.count, !(c[i] == "*" && c[i + 1] == "/") { i += 1 }
                i += 2; continue
            }
            if ch == "," {
                var j = i + 1
                while j < c.count, c[j] == " " || c[j] == "\n" || c[j] == "\r" || c[j] == "\t" { j += 1 }
                if j < c.count, c[j] == "}" || c[j] == "]" { i += 1; continue }
            }
            out.append(ch); i += 1
        }
        return String(out)
    }
}
