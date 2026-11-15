#!/usr/bin/env python3
# Copyright 2026 NevesHenry and intraducine. This file was taken from https://github.com/willfaust/Madeira
# (pull request #604) under GPL-3.0-or-later with the Madeira Converter Exception, and modified for BCD in November 2026.
# See THIRD-PARTY-NOTICES.md.
"""Malformed Steam protobuf data throws instead of stopping the app.

Compiles app/Madeira/SwiftSteam/Proto/SteamProtoMessages.swift with
SteamLANPeer.swift and DepotManifest.swift and feeds them bytes no Steam
client sends: a tag whose field number does not fit in 32 bits, a 4-7 byte
manifest blob, and uint32 manifest fields above 2^32. Their callers use try?
(LAN discovery datagrams from any sender on UDP 27036, a cached manifest, the
plain-manifest attempt before decryption), so each must throw or parse; a Swift
trap there stops the app. Valid manifests must still parse as before.
Never contacts Steam. Needs swiftc (macOS or Linux) and python3.

BCD's own instances of the same pattern (U1, upstream merge of 2026-11-14), checked here too: a chunk offset that does not fit a
signed 64-bit file position throws in DepotManifest (the downloader writes at off_t(offset) and adds the chunk's size); the int32
eresult of the logon and depot-key replies is read with its bit pattern, as the legacy logon body is (UInt32() of a negative value
traps); Steam Cloud's upload blocks (SteamCloudTransfer.blockRange, compiled on its own) refuse an offset + length past 2^64 or a
length past Int.max instead of trapping, and every other block gets the bytes it got before; and no Steam decoder converts a
varint with a trapping initializer.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
steam = root / 'app/Madeira/SwiftSteam'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failures = 0
trap = ''


def require(cond, what):
    global failures
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        failures += 1
        if trap:
            print('     ' + trap)


def varint(v):
    out = bytearray()
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def field(num, wire, payload):
    tag = varint(num << 3 | wire)
    if wire == 0:
        return tag + varint(payload)
    if wire == 5:
        return tag + struct.pack('<I', payload)
    return tag + varint(len(payload)) + payload


MAGIC = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x21, 0x4C, 0x5F, 0xA0])  # LAN discovery packets
MANIFEST_MAGIC = bytes([0xD0, 0x17, 0xF6, 0x71])                  # 8-byte header before the payload
MAX_FIELD = (1 << 32) - 1                                          # the largest field number readTag returns
HUGE_TAG = varint(((1 << 37) - 1) << 3)                            # field 2^37 - 1, wire type varint


def chunk(original, compressed, offset=0):
    return field(1, 2, bytes(range(20))) + field(2, 5, 0x01020304) + field(3, 0, offset) + field(4, 0, original) + field(5, 0, compressed)


def mapping(name, size, flags, *chunks):
    return field(1, 2, name) + field(2, 0, size) + field(3, 0, flags) + b''.join(field(6, 2, c) for c in chunks)


def framed(payload):
    return MANIFEST_MAGIC + struct.pack('<I', len(payload)) + payload


main_swift = r'''
import Foundation

func unhex(_ s: String) -> Data {
    var d = Data(); var i = s.startIndex
    while i < s.endIndex { let j = s.index(i, offsetBy: 2); d.append(UInt8(s[i..<j], radix: 16)!); i = j }
    return d
}

func manifest(_ data: Data) -> String {
    do {
        let m = try DepotManifest.parse(depotID: 1, manifestGID: 1, data: data)
        return "parsed " + m.files.map { f in
            "\(f.filename):\(f.size):\(f.flags):" + f.chunks.map { "\($0.uncompressedSize)/\($0.compressedSize)" }.joined(separator: ";")
        }.joined(separator: ",")
    } catch { return "threw" }
}

@main struct Check {
    static func main() {
        let args = CommandLine.arguments
        // BCD: only the commands that take bytes get hex; "range" and "noise" take numbers
        let input = args.count > 2 && ["tag", "type", "status", "manifest", "mutate"].contains(args[1]) ? unhex(args[2]) : Data()
        switch args[1] {
        case "tag":
            var decoder = ProtobufDecoder(input)
            do {
                if let t = try decoder.readTag() { print("\(t.fieldNumber) \(t.wireType.rawValue)") } else { print("end") }
            } catch { print("threw") }
        case "type":
            print(SteamLANPeer.packetType([UInt8](input)).map(String.init) ?? "nil")
        case "status":
            print(SteamLANPeer.parseStatus(input).map { String($0.clientID) } ?? "nil")
        case "manifest":
            print(manifest(input))
        case "range":
            // BCD: SteamCloudTransfer.blockRange(offset:length:fileCount:)
            let r = SteamCloudTransfer.blockRange(offset: UInt64(args[2])!, length: UInt64(args[3])!, fileCount: Int(args[4])!)
            print(r.map { "\($0.lowerBound)..<\($0.upperBound)" } ?? "nil")
        case "noise":
            // Fixed pseudo-random 4 KiB blobs; a trap ends the process before the summary.
            var s: UInt64 = 1
            var parsed = 0, threw = 0
            for _ in 0..<Int(args[2])! {
                let blob = Data((0..<4096).map { _ -> UInt8 in
                    s = s &* 6364136223846793005 &+ 1442695040888963407
                    return UInt8(truncatingIfNeeded: s >> 33)
                })
                if (try? DepotManifest.parse(depotID: 1, manifestGID: 1, data: blob)) != nil { parsed += 1 } else { threw += 1 }
            }
            print("\(parsed) \(threw)")
        case "mutate":
            // BCD: deterministic mutations of a VALID message (a bit flipped, a byte replaced, the end cut off, a slice repeated), so the
            // nested decoders (a manifest's FileMapping and ChunkData, a LAN status's header and user records) see malformed input;
            // random blobs end at their first tag. A trap ends the process before the summary.
            var s: UInt64 = 7
            func next(_ bound: Int) -> Int {
                s = s &* 6364136223846793005 &+ 1442695040888963407
                return bound > 0 ? Int((s >> 33) % UInt64(bound)) : 0
            }
            var parsed = 0, threw = 0, nested = 0
            let base = [UInt8](input)
            for i in 0..<Int(args[3])! {
                var m = base
                switch i % 4 {
                case 0: let k = next(m.count); m[k] ^= UInt8(1 << next(8))
                case 1: let k = next(m.count); m[k] = UInt8(truncatingIfNeeded: next(256))
                case 2: m = Array(m.prefix(next(m.count)))
                default: let a = next(m.count); let b = a + next(m.count - a); m.insert(contentsOf: m[a..<b], at: b)
                }
                if args[4] == "status" {
                    if SteamLANPeer.parseStatus(Data(m)) != nil { parsed += 1 } else { threw += 1 }
                    _ = SteamLANPeer.packetType(m)
                } else if let parsedManifest = try? DepotManifest.parse(depotID: 1, manifestGID: 1, data: Data(m)) {
                    parsed += 1
                    if parsedManifest.files.contains(where: { !$0.chunks.isEmpty }) { nested += 1 }
                } else { threw += 1 }
            }
            print("\(parsed) \(threw) \(nested)")
        default: print("?")
        }
    }
}
'''

stub_swift = '''
import Foundation
enum SteamLog { static func trace(_ s: String) {} }
enum ContentDecryptor {
    static let maximumChunkBytes = 64 * 1024 * 1024
    static func decryptChunk(encryptedData: Data, depotKey: Data) throws -> Data { throw SteamError.protobufError("no key") }
}
'''

if not Path(SWIFTC).exists() and not shutil.which(SWIFTC):
    print('SKIP: no swiftc (set SWIFTC)')
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'main.swift').write_text(main_swift)
    (tmp / 'stub.swift').write_text(stub_swift)
    # BCD: the upload block range of SteamCloud.swift, on its own (the rest of that file needs the app).
    cloud = (root / 'app/Madeira/SteamCloud.swift').read_text()
    head = '    static func blockRange(offset: UInt64, length: UInt64, fileCount: Int) -> Range<Int>? {'
    require(head in cloud, 'BCD: SteamCloud.swift has SteamCloudTransfer.blockRange')
    if head in cloud:
        start = cloud.index(head)
        body = cloud[start:cloud.index('\n    }\n', start) + 7]
    else:   # sources from before it: a stand-in that is never right, so the grid below fails instead of the build
        body = '    static func blockRange(offset: UInt64, length: UInt64, fileCount: Int) -> Range<Int>? { 0..<0 }\n'
    (tmp / 'cloud.swift').write_text('import Foundation\nenum SteamCloudTransfer {\n' + body + '}\n')
    exe = tmp / 'check'
    build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', '-o', str(exe),
                            str(steam / 'Proto/SteamProtoMessages.swift'), str(steam / 'Core/SteamError.swift'),
                            str(steam / 'Content/SteamLANPeer.swift'), str(steam / 'Content/DepotManifest.swift'),
                            str(tmp / 'stub.swift'), str(tmp / 'cloud.swift'), str(tmp / 'main.swift')],
                           capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stderr[-4000:])
        require(False, 'the Steam protobuf sources compile')
        sys.exit(1)

    def run(*args):
        global trap
        r = subprocess.run([str(exe)] + [str(a) for a in args], capture_output=True, text=True, timeout=120)
        trap = ''
        if r.returncode != 0:
            fatal = next((l for l in r.stderr.splitlines() if 'Fatal error' in l), r.stderr.strip()[-200:])
            trap = f'exit {r.returncode}: {fatal}'
            return None
        return r.stdout.strip()

    # The decoder: field numbers up to 2^32 - 1 decode as before, larger ones throw.
    require(run('tag', varint(MAX_FIELD << 3 | 2).hex()) == f'{MAX_FIELD} 2', 'field number 2^32 - 1 decodes')
    require(run('tag', varint((MAX_FIELD + 1) << 3).hex()) == 'threw', 'field number 2^32 throws')
    require(run('tag', HUGE_TAG.hex()) == 'threw', 'field number 2^37 - 1 throws')

    # A LAN datagram whose header holds that tag (22 bytes, from any sender on UDP 27036).
    datagram = MAGIC + struct.pack('<I', len(HUGE_TAG)) + HUGE_TAG + struct.pack('<I', 0)
    require(run('type', datagram.hex()) is not None, 'packetType of a datagram with an oversized tag returns')
    require(run('status', datagram.hex()) == 'nil', 'that datagram is not taken for a PC\'s status')

    # Depot manifests: valid ones parse, bare and behind the 8-byte header.
    good = field(1, 2, mapping(b'game.exe', 5, 0, chunk(5, 5)))
    require(run('manifest', good.hex()) == 'parsed game.exe:5:0:5/5', 'a valid manifest parses')
    require(run('manifest', framed(good).hex()) == 'parsed game.exe:5:0:5/5', 'a valid manifest behind the 8-byte header parses')

    require(run('manifest', b'PK\x03\x04\x00'.hex()) == 'threw', 'a 5-byte blob throws')
    require(run('manifest', framed(HUGE_TAG + b'\x00').hex()) == 'threw', 'a manifest with an oversized tag throws')
    large = field(1, 2, mapping(b'a', 1 << 63, 0))
    overflow = field(1, 2, mapping(b'a', (1 << 64) - 1, 0)) + field(1, 2, mapping(b'b', 1, 0))
    require(run('manifest', large.hex()) == 'threw', 'a file larger than Int64.max throws')
    require(run('manifest', overflow.hex()) == 'threw', 'file sizes that overflow the total throw')
    limit = field(1, 2, mapping(b'a', (1 << 63) - 1, 0))
    require(run('manifest', limit.hex()) == 'parsed a:9223372036854775807:0:', 'Int64.max remains valid')
    over_limit = limit + field(1, 2, mapping(b'b', 1, 0))
    require(run('manifest', over_limit.hex()) == 'threw', 'a total above Int64.max throws')
    # uint32 fields above 2^32 keep their low 32 bits, as protobuf's uint32 and the other Steam decoders do.
    require(run('manifest', field(1, 2, mapping(b'a', 1, 1 << 32 | 0x40)).hex()) == 'parsed a:1:64:',
            'flags above 2^32 keep their low 32 bits')
    require(run('manifest', field(1, 2, mapping(b'a', 5, 0, chunk(1 << 32 | 5, 1 << 33 | 3))).hex()) == 'parsed a:5:0:5/3',
            'cb_original and cb_compressed above 2^32 keep their low 32 bits')

    # BCD: a chunk offset beyond a signed 64-bit file position (with the chunk's size) throws; the largest that fits parses.
    I64 = (1 << 63) - 1
    require(run('manifest', field(1, 2, mapping(b'a', 5, 0, chunk(5, 5, I64 - 5))).hex()) == 'parsed a:5:0:5/5',
            'BCD: a chunk ending exactly at Int64.max parses')
    require(run('manifest', field(1, 2, mapping(b'a', 5, 0, chunk(5, 5, I64 - 4))).hex()) == 'threw',
            'BCD: a chunk ending past Int64.max throws')
    require(run('manifest', field(1, 2, mapping(b'a', 5, 0, chunk(5, 5, (1 << 64) - 1))).hex()) == 'threw',
            'BCD: a chunk offset of 2^64 - 1 throws')

    # BCD: Steam Cloud upload blocks. The old code: start = min(offset, n), end = min(offset + length, n) (a trap when the sum passes
    # 2^64), then Int(length) (a trap past Int.max), and the block was refused unless end - start == length.
    def old_block(offset, length, n):
        if offset + length >= 1 << 64:
            return 'trap'
        start, end = min(offset, n), min(offset + length, n)
        if length > I64:
            return 'trap'
        return f'{start}..<{end}' if end - start == length else 'nil'
    values = [0, 1, 4, 5, 6, 9, 10, 11, I64 - 1, I64, I64 + 1, (1 << 64) - 2, (1 << 64) - 1]
    same, traps, bad = 0, 0, []
    for n in (0, 10):
        for offset in values:
            for length in values:
                want = old_block(offset, length, n)
                got = run('range', offset, length, n)
                if want == 'trap':
                    traps += 1
                    if got != 'nil':
                        bad.append((offset, length, n, got))
                elif got == want:
                    same += 1
                else:
                    bad.append((offset, length, n, want, got))
    require(not bad and traps > 0, f'BCD: Steam Cloud blocks: {same} get what they got before, {traps} that trapped are refused' +
            (f' (wrong: {bad[:4]})' if bad else ''))

    # BCD: mutations of valid messages reach the nested decoders (F5 of the 2026-11-14 review).
    rich = b''.join(field(1, 2, mapping(n, 5 * k, k, *[chunk(5, 4, 5 * j) for j in range(k)]))
                    for k, n in ((1, b'game.exe'), (2, b'data/pak0.pak'), (3, b'Binaries\\Win64\\x.dll')))
    require(run('manifest', rich.hex()) is not None and run('manifest', rich.hex()).startswith('parsed game.exe:5:1:'), 'BCD: the mutation base manifest parses')
    for label, base, kind in (('manifest', rich, 'manifest'), ('framed manifest', framed(rich), 'manifest')):
        out = run('mutate', base.hex(), 4000, kind)
        parts = out.split() if out else []
        require(len(parts) == 3 and int(parts[0]) + int(parts[1]) == 4000 and int(parts[0]) > 0 and int(parts[1]) > 0 and int(parts[2]) > 0,
                f'BCD: 4000 mutations of a valid {label} each parse or throw, and some parse with chunks' +
                (f' ({parts[0]} parsed, {parts[1]} threw, {parts[2]} with chunks)' if len(parts) == 3 else ''))
    header = field(1, 0, 0x1122334455) + field(2, 0, 1)
    user = b'\x09' + struct.pack('<Q', 76561198000000000)   # CMsgRemoteClientBroadcastStatus.User: steamid, fixed64 field 1
    body = field(4, 2, b'PC') + field(9, 2, user) + field(20, 2, b'192.168.1.20')
    status = MAGIC + struct.pack('<I', len(header)) + header + struct.pack('<I', len(body)) + body
    require(run('status', status.hex()) == str(0x1122334455), 'BCD: the mutation base LAN status parses')
    out = run('mutate', status.hex(), 4000, 'status')
    parts = out.split() if out else []
    require(len(parts) == 3 and int(parts[0]) + int(parts[1]) == 4000 and int(parts[0]) > 0 and int(parts[1]) > 0,
            'BCD: 4000 mutations of a valid LAN status each parse or are refused' + (f' ({parts[0]} parsed, {parts[1]} refused)' if len(parts) == 3 else ''))

    noise = run('noise', 1000)
    counts = ' (%s parsed, %s threw)' % tuple(noise.split()) if noise else ''
    require(noise is not None and sum(map(int, noise.split())) == 1000, '1000 random 4 KiB blobs each parse or throw' + counts)

# BCD: the int32 eresult fields are read with their bit pattern, and no Steam decoder converts a varint with a trapping initializer.
import re
app = root / 'app/Madeira'
session = (steam / 'Core/SteamSession.swift').read_text()
downloader = (steam / 'Content/DepotDownloader.swift').read_text()
require('eResultCode = UInt32(bitPattern: logonResponse.eresult)' in session and
        'EResult(rawValue: UInt32(bitPattern: keyResponse.eresult))' in downloader, 'BCD: the eresult of the logon and depot-key replies is read by its bit pattern')
trapping = []
for swift in sorted(app.rglob('*.swift')):
    text = swift.read_text(encoding='utf-8', errors='replace')
    trapping += [f'{swift.relative_to(root)}: {m.group(0)}' for m in re.finditer(r'\bU?Int(?:8|16|32|64)?\(\s*(?:try\s+)?[A-Za-z_.]+\.readVarint\(\)\s*\)', text)]
    trapping += [f'{swift.relative_to(root)}: {m.group(0)}' for m in re.finditer(r'\bUInt32\(\s*[A-Za-z_.]+\.eresult\s*\)', text)]
require(not trapping, 'BCD: no varint or eresult converted with a trapping initializer' + (f' ({trapping[:4]})' if trapping else ''))
wf = (root / '.github/workflows/build-ipa.yml').read_text()
require('python3 tests/host/check-steam-malformed-protobuf.py' in wf, 'CI runs this check')

if failures:
    print(f'check-steam-malformed-protobuf: {failures} FAILED')
    sys.exit(1)
print('PASS: malformed Steam protobuf data throws instead of stopping the app')
