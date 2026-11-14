// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  LogUploadCore.swift
//  Madeira
//
//  madeira-bcd FORK-ONLY: never in a pull request to upstream (owner, 2026-10-17).
//
//  "Send logs to BCD" (LogUpload.swift): a session log is scrubbed and gzip'ed on the
//  phone before it leaves it. Foundation and zlib only, so tests/host/check-log-upload.py
//  compiles it alone and holds the scrub to tools/autotune-fixture.py's, line for line.
//
//  One pass, 1 MB at a time, with fixed-size state: a 150 MB log (Warcraft II, 2.2 M
//  lines) costs no more memory than a 3 MB one. A line is handed to the regular
//  expressions only when a byte scan finds something they could change; most lines
//  ("Handled self-modifying code: pc: ... fault: ...") go through untouched.
//

import Foundation
import zlib

enum LogUploadCore {
    /// The same rules, in the same order, as tools/autotune-fixture.py's SCRUB.
    static let rules: [(NSRegularExpression, String)] = [
        (#"/(?:private/)?var/mobile/Containers/(?:Data|Shared/AppGroup)/(?:Application/)?[0-9A-Fa-f-]{36}"#, "<container>"),
        (#"(?i)(\\\\?users\\\\?)(?!public\b|mobile\b|<user>)[^\\/"' ]+"#, "$1<user>"),
        (#"/Users/[^/"' ]+"#, "/Users/<user>"),
        (#"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#, "<email>"),
        (#"\b(?:\d{1,3}\.){3}\d{1,3}\b"#, "<ip>"),
        (#"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"#, "<uuid>"),
        (#"\b[0-9a-f]{40}\b"#, "<id>"),
        // 2026-10-17, before the Send Log alpha: identifiers a long or networked session can show.
        (#"(?i)(userdata[\\/]+)\d+"#, "$1<steamid>"),
        (#"\b7656119\d{10}\b"#, "<steamid>"),
        (#"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"#, "<mac>"),
        (#"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"#, "<ip6>"),
        (#"(?i)\b(?:fe80|f[cd][0-9a-f]{2}|[23][0-9a-f]{3})(?::[0-9a-f]{1,4})*::(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})*)?"#, "<ip6>"),
        (#"[^\s'’"\\/]{1,40}['’]s (?=(?:[A-Za-z0-9-]+ )?(?:iPhone|iPad|Mac|MacBook|AirPods|Keyboard|Mouse|Trackpad|Controller|DualSense|DualShock|Xbox|Joy-Con)\b)"#, "<name>'s "),
        // 2026-10-22, Epic Games sign-in (BCDEpic): Epic's two token families and the sign-in page's code.
        (#"\beg1~[A-Za-z0-9._~+/=-]+"#, "eg1~<token>"),
        (#"\begoc1~[A-Za-z0-9._~+/=-]+"#, "egoc1~<token>"),
        (#"("authorizationCode"\s*:\s*")[0-9A-Za-z]{16,64}"#, "$1<code>"),
        // 2026-10-22, Epic Games milestone 2 (BCDEpicStart): a game started signed in has Epic's one-time code, the account's ID and its
        // name on its command line, and a sign-in page may have a code in an address. The runtime masks the first three ("*") before they
        // are logged; these rules are the second line (a value is replaced, a mask or placeholder is left as it is).
        (#"(?i)(-AUTH_PASSWORD=)(?![*<])[^\s"'\\]+"#, "$1<code>"),
        (#"(?i)(-epicuserid=)(?![*<])[0-9A-Za-z]{8,64}"#, "$1<id>"),
        (#"(?i)(-epicusername=)(?![*<])(?:\\?"[^"]*?\\?"|[^\s"'\\]+)"#, "$1<name>"),
        (#"(?i)((?:exchangeCode|userCode)=|[?&]code=)(?![*<])[^&\s"'\\]+"#, "$1<code>"),
    ].map { (try! NSRegularExpression(pattern: $0.0), $0.1) }

    static func scrub(_ line: String) -> String {
        var s = line
        for (re, template) in rules {
            let range = NSRange(s.startIndex..., in: s)
            s = re.stringByReplacingMatches(in: s, range: range, withTemplate: template)
        }
        return s
    }

    /// Could any rule change this line? False only when none can: no "/Containers/" or
    /// "/Users/", no "users" or "userdata" in any case, no "@", fewer than three digit.digit
    /// dots (an IP address has three), fewer than four "-" (a UUID or a MAC has them), no run
    /// of 40 hex digits or of 17 decimal ones (a SteamID64), fewer than five ":" and no "::"
    /// (MAC and IPv6 addresses), no "'s " (a person's device name), no "eg1~" or "egoc1~" (Epic's tokens),
    /// no `authorizationCode":` (the Epic sign-in page's JSON) and no "=" after -AUTH_PASSWORD, -epicuserid,
    /// -epicusername, exchangeCode, userCode or ?code / &code (Epic's start arguments and a code in an address).
    static func mayNeedScrub(_ b: UnsafeRawBufferPointer) -> Bool {
        let n = b.count
        if n < 7 { return false }
        var dots = 0, dashes = 0, colons = 0, hexRun = 0, hexRunMax = 0, digitRun = 0, digitRunMax = 0
        for i in 0..<n {
            let c = b[i]
            switch c {
            case 0x40: return true                                   // @
            case 0x3A:                                               // : and ::, and authorizationCode":
                colons += 1
                if i + 1 < n, b[i + 1] == 0x3A { return true }
                if i >= 18, matches(b, i - 18, "authorizationCode\"") { return true }
            case 0x3D:                                               // = after one of Epic's start arguments or a code in an address
                if endsLower(b, i, "-auth_password") || endsLower(b, i, "-epicuserid") || endsLower(b, i, "-epicusername")
                    || endsLower(b, i, "exchangecode") || endsLower(b, i, "usercode") || endsLower(b, i, "?code") || endsLower(b, i, "&code") { return true }
            case 0x7E:                                               // ~ after eg1 or egoc1
                if (i >= 3 && matches(b, i - 3, "eg1~")) || (i >= 5 && matches(b, i - 5, "egoc1~")) { return true }
            case 0x27:                                               // 's
                if i + 2 < n, b[i + 1] == 0x73, b[i + 2] == 0x20 { return true }
            case 0xE2:                                               // ’s (U+2019)
                if i + 4 < n, b[i + 1] == 0x80, b[i + 2] == 0x99, b[i + 3] == 0x73, b[i + 4] == 0x20 { return true }
            case 0x2D: dashes += 1                                   // -
            case 0x2E:                                               // .
                if i > 0, i + 1 < n, isDigit(b[i - 1]), isDigit(b[i + 1]) { dots += 1 }
            case 0x2F:                                               // / then Containers/ or Users/
                if matches(b, i + 1, "Containers/") || matches(b, i + 1, "Users/") { return true }
            case 0x55, 0x75:                                         // U, u then sers or serdata in any case
                if i + 4 < n, lower(b[i + 1]) == 0x73, lower(b[i + 2]) == 0x65, lower(b[i + 3]) == 0x72,
                   lower(b[i + 4]) == 0x73 || lower(b[i + 4]) == 0x64 { return true }
            default: break
            }
            if (c >= 0x30 && c <= 0x39) || (c >= 0x61 && c <= 0x66) { hexRun += 1; hexRunMax = max(hexRunMax, hexRun) }
            else { hexRun = 0 }
            if c >= 0x30 && c <= 0x39 { digitRun += 1; digitRunMax = max(digitRunMax, digitRun) } else { digitRun = 0 }
        }
        return dots >= 3 || dashes >= 4 || hexRunMax >= 40 || digitRunMax >= 17 || colons >= 5
    }

    /// The bytes just before `end` are `word` (lower case), in any letter case.
    private static func endsLower(_ b: UnsafeRawBufferPointer, _ end: Int, _ word: StaticString) -> Bool {
        let n = word.utf8CodeUnitCount
        guard end >= n else { return false }
        for j in 0..<n where lower(b[end - n + j]) != word.utf8Start[j] { return false }
        return true
    }

    private static func isDigit(_ c: UInt8) -> Bool { c >= 0x30 && c <= 0x39 }
    private static func lower(_ c: UInt8) -> UInt8 { c >= 0x41 && c <= 0x5A ? c + 32 : c }
    private static func matches(_ b: UnsafeRawBufferPointer, _ at: Int, _ word: StaticString) -> Bool {
        let w = UnsafeRawBufferPointer(start: word.utf8Start, count: word.utf8CodeUnitCount)
        guard at + w.count <= b.count else { return false }
        for j in 0..<w.count where b[at + j] != w[j] { return false }
        return true
    }

    enum PackError: Error { case read, write, zlib(Int32) }

    /// Scrubs `source` line by line into a gzip file at `dest`. Returns the bytes read and
    /// written. `progress` gets the fraction read, now and then.
    /// The sender's own words (Send Log's comment box, 2026-10-24): what the log cannot show (low FPS, where it stutters,
    /// what was on screen). At most 2000 characters, one "[user-comment] " line per line written, control characters out;
    /// scrubbed like every other line. Empty: nothing.
    static func commentLines(_ comment: String?) -> [String] {
        let text = String((comment ?? "").trimmingCharacters(in: .whitespacesAndNewlines).prefix(2000))
        guard !text.isEmpty else { return [] }
        return text.split(whereSeparator: \.isNewline).map { line in
            "[user-comment] " + String(line.unicodeScalars.filter { $0.value >= 0x20 && $0.value != 0x7F }.map(Character.init))
        }
    }

    /// `trailer`: lines that follow the log's last line (the performance trace, BCDPerfTraceCore.swift): after it, so no line number of the
    /// log moves. Scrubbed like every other line. Empty: nothing is added.
    /// madeira-bcd (Porthole port, item 11): `head`, lines after the comment and before the log (the thinned log's note), and `keep`, asked
    /// once for every line of the log in order: false leaves the line out (BCDLogThin.swift). By default nothing is added and every line goes.
    static func pack(_ source: URL, to dest: URL, comment: String? = nil, trailer: [String] = [],
                     head: [String] = [], keep: ((UnsafeRawBufferPointer) -> Bool)? = nil,
                     progress: ((Double) -> Void)? = nil) throws -> (raw: Int, packed: Int) {
        guard let input = try? FileHandle(forReadingFrom: source) else { throw PackError.read }
        defer { try? input.close() }
        let total = (try? FileManager.default.attributesOfItem(atPath: source.path)[.size] as? NSNumber)?.intValue ?? 0
        FileManager.default.createFile(atPath: dest.path, contents: nil)
        guard let output = try? FileHandle(forWritingTo: dest) else { throw PackError.write }
        defer { try? output.close() }

        var z = z_stream()
        // windowBits 15 + 16: a gzip header and trailer, which the intake checks for.
        let rc = deflateInit2_(&z, Z_DEFAULT_COMPRESSION, Z_DEFLATED, MAX_WBITS + 16, 8, Z_DEFAULT_STRATEGY,
                               ZLIB_VERSION, Int32(MemoryLayout<z_stream>.size))
        guard rc == Z_OK else { throw PackError.zlib(rc) }
        defer { deflateEnd(&z) }

        var out = [UInt8](repeating: 0, count: 1 << 18)
        var packed = 0
        func deflateChunk(_ bytes: UnsafeRawBufferPointer, flush: Int32) throws {
            z.next_in = UnsafeMutablePointer(mutating: bytes.baseAddress?.assumingMemoryBound(to: Bytef.self))
            z.avail_in = uInt(bytes.count)
            repeat {
                let produced: Int = try out.withUnsafeMutableBytes { o in
                    z.next_out = o.baseAddress!.assumingMemoryBound(to: Bytef.self)
                    z.avail_out = uInt(o.count)
                    let r = deflate(&z, flush)
                    guard r == Z_OK || r == Z_STREAM_END || r == Z_BUF_ERROR else { throw PackError.zlib(r) }
                    return o.count - Int(z.avail_out)
                }
                if produced > 0 {
                    output.write(Data(out[0..<produced]))
                    packed += produced
                }
            } while z.avail_out == 0
        }

        var pending = Data()             // scrubbed lines waiting for deflate
        var carry = Data()               // a line cut by the chunk boundary
        var read = 0, lastReport = 0
        func emit(_ line: UnsafeRawBufferPointer) {
            if mayNeedScrub(line) {
                pending.append(Data(scrub(String(decoding: line, as: UTF8.self)).utf8))
            } else {
                pending.append(line.bindMemory(to: UInt8.self))
            }
            pending.append(0x0A)
        }
        // The sender's comment goes first, so whoever opens the log reads it before anything else.
        for line in commentLines(comment) { Data(line.utf8).withUnsafeBytes { emit($0) } }
        for line in head { Data(line.utf8).withUnsafeBytes { emit($0) } }
        /// A line of the log itself (not the comment, the head or the trailer).
        func emitLine(_ line: UnsafeRawBufferPointer) {
            if let keep, !keep(line) { return }
            emit(line)
        }
        while true {
            let chunk = (try? input.read(upToCount: 1 << 20)) ?? nil
            guard let chunk, !chunk.isEmpty else { break }
            read += chunk.count
            chunk.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                var start = 0
                while start < raw.count {
                    guard let nl = memchr(raw.baseAddress! + start, 0x0A, raw.count - start) else {
                        carry.append(contentsOf: UnsafeRawBufferPointer(rebasing: raw[start...]))
                        break
                    }
                    let end = UnsafeRawPointer(nl) - raw.baseAddress!
                    if carry.isEmpty {
                        emitLine(UnsafeRawBufferPointer(rebasing: raw[start..<end]))
                    } else {
                        carry.append(contentsOf: UnsafeRawBufferPointer(rebasing: raw[start..<end]))
                        carry.withUnsafeBytes { emitLine($0) }
                        carry.removeAll(keepingCapacity: true)
                    }
                    start = end + 1
                }
            }
            try pending.withUnsafeBytes { try deflateChunk($0, flush: Z_NO_FLUSH) }
            pending.removeAll(keepingCapacity: true)
            if let progress, total > 0, read - lastReport >= 8 << 20 {
                lastReport = read
                progress(min(1, Double(read) / Double(total)))
            }
        }
        if !carry.isEmpty {
            let before = pending.count
            carry.withUnsafeBytes { emitLine($0) }
            if trailer.isEmpty, pending.count > before { pending.removeLast() }   // the source did not end with a newline (with a trailer, that line is ended here and the trailer follows)
        }
        for line in trailer { Data(line.utf8).withUnsafeBytes { emit($0) } }
        try pending.withUnsafeBytes { try deflateChunk($0, flush: Z_FINISH) }
        progress?(1)
        return (read, packed)
    }
}
