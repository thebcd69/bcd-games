#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Steam Cloud downloads (build 669, after Porthole 1.3.1, 6d4d1aa): SteamCloudTransfer.checked keeps a served file only when its SHA-1
is the one Steam listed; as before the bytes as served or the zip's first entry, and only when that fails a zlib stream, gzip or bare
deflate. A failure names what arrived (served=, head=, steam-served=, raw=) for the log, where reason() said only "SteamFileError";
fetch gets a cut-short file once more.

The functions are cut out of app/Madeira/SteamCloud.swift and compiled with swiftc on this Mac against real zlib/gzip/deflate data."""
import gzip, hashlib, os, shutil, subprocess, sys, tempfile, zlib, zipfile, io
from pathlib import Path

R = Path(__file__).resolve().parents[2]
src = (R / "app/Madeira/SteamCloud.swift").read_text()
lib = (R / "app/Madeira/SteamOwnedLibrary.swift").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[-1500:]))
    ok = ok and bool(cond)


def cut(start, end_marker="\n    }\n"):
    i = src.index(start)
    return src[i:src.index(end_marker, i) + len(end_marker)]


fetch = cut("    static func fetch(_ info: SteamCloudDownloadInfo, expectedSHA: Data) async throws -> Data {")
check("fetch: a cut-short file (fewer bytes than Steam serves) is fetched once more, then checked the same way",
      "if case .failure = outcome, info.fileSize > 0, body.count != Int(info.fileSize) {\n            body = try await get()" in fetch
      and "return try outcome.get()" in fetch)
check("the log names what arrived instead of the error's type name",
      'case let cloud as SteamCloudTransfer.FetchError: return "cloud-\\(cloud.kind) \\(cloud.detail)"' in lib)

swiftc = shutil.which("swiftc")
if not swiftc:
    print("SKIP the compiled checks: no swiftc")
else:
    body = (cut("    static func unzipFirstEntry(_ data: Data, rawSize: Int) -> Data? {")
            + src[src.index("    /// madeira-bcd (Porthole 1.3.1): what arrived when a cloud file does not check out"):
                  src.index("    /// Puts downloaded bytes in place.")])
    harness = '''import Foundation
import Compression
import CryptoKit
struct SteamCloudDownloadInfo { var fileSize: UInt64 = 0; var rawSize: UInt64 = 0 }
enum SteamCloudTransfer {
    static let maxFileBytes = 256 << 20
''' + body + '''}
let a = CommandLine.arguments
let data = try! Data(contentsOf: URL(fileURLWithPath: a[1]))
let sha = Data(stride(from: 0, to: a[2].count, by: 2).map { UInt8(a[2].dropFirst($0).prefix(2), radix: 16)! })
let info = SteamCloudDownloadInfo(fileSize: UInt64(a[3])!, rawSize: UInt64(a[4])!)
switch SteamCloudTransfer.checked(data, info: info, expectedSHA: sha) {
case .success(let d): print("OK \\(d.count)")
case .failure(let e as SteamCloudTransfer.FetchError): print("FAIL \\(e.kind) \\(e.detail)")
case .failure(let e): print("FAIL other \\(e)")
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "h.swift").write_text(harness)
        b = subprocess.run([swiftc, "-O", str(t / "h.swift"), "-o", str(t / "h")], capture_output=True, text=True)
        check("the cut-out functions compile on the host", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            raw = (b"BCD save file " * 4000) + os.urandom(64)
            sha = hashlib.sha1(raw).hexdigest()

            def run(name, served, file_size=None, raw_size=None):
                (t / name).write_bytes(served)
                r = subprocess.run([str(t / "h"), str(t / name), sha, str(len(served) if file_size is None else file_size),
                                    str(len(raw) if raw_size is None else raw_size)], capture_output=True, text=True)
                return r.stdout.strip()

            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("f", raw)
            co = zlib.compressobj(9, zlib.DEFLATED, -15)
            bare = co.compress(raw) + co.flush()
            check("as served (the file itself)", run("plain", raw) == f"OK {len(raw)}")
            check("a one-entry zip, as before", run("zip", zbuf.getvalue()) == f"OK {len(raw)}")
            check("a zlib stream (new, failure path only)", run("zlib", zlib.compress(raw, 9)) == f"OK {len(raw)}")
            check("gzip (new)", run("gz", gzip.compress(raw, mtime=0)) == f"OK {len(raw)}")
            extra = os.urandom(300)   # an extra field longer than 255 bytes: both length bytes count
            named = (b"\x1f\x8b\x08\x0c" + b"\0" * 4 + b"\x02\x03" + len(extra).to_bytes(2, "little") + extra + b"save.dat\0"
                     + bare + zlib.crc32(raw).to_bytes(4, "little") + len(raw).to_bytes(4, "little"))
            check("gzip with an extra field and a file name (new)", run("gzx", named) == f"OK {len(raw)}")
            check("bare deflate (new)", run("deflate", bare) == f"OK {len(raw)}")
            bad = run("bad", raw[:-1] + bytes([raw[-1] ^ 0x01]))   # the last byte always changes (raw ends in random bytes: + b"X" left it equal 1 time in 256, build 671)
            check("a file whose SHA-1 differs is refused and named: kind, bytes served, first 8 bytes, Steam's sizes",
                  bad.startswith("FAIL checksum served=") and "head=" in bad and "steam-served=" in bad and "raw=" in bad, bad)
            junk = run("junk", b"\x50\x4b\x03\x04" + os.urandom(200), raw_size=len(raw))
            check("an archive that does not unpack is a decompress failure", junk.startswith("FAIL decompress"), junk)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-steam-cloud-fetch.py" in wf)
print("PASS: Steam Cloud fetch" if ok else "FAILED: Steam Cloud fetch")
sys.exit(0 if ok else 1)
