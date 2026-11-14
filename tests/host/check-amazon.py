#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The experimental Amazon Games store (Porthole port, item 13; app/Madeira/SwiftAmazon/, BCDAmazon/). No Amazon account, no network.

1. Compiled and run here (tests/host/bcd_amazon_checks.swift): the build manifest (a header, an RSA signature over the LZMA-packed protobuf
   list, made here with a key of the test's own) is read only when its signature checks out; a changed byte or another key is refused;
   unsafe paths and an unknown checksum are refused; the entitlements become the games (add-ons left out); fuel.json, with comments and
   trailing commas, names the program; the sign-in address's code is found; the folder name is plain and unique.
2. Off path, on the source: the store is `env.BCD_AMAZON`, OFF unless set; every hook BCDLiveStore and the game page call returns at its
   first test while it is off; the library's Amazon chip shows only with games or an account; nothing of Amazon runs at app start.
3. The card in Settings › Accounts, the project, the credit, the workflow.
"""
from pathlib import Path
import lzma
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
failures = []


def check(what, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + what + (f"\n      {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(what)
    return cond


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def field(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint(number << 3 | 2) + varint(len(value)) + value


def manifest(files, dirs=()):
    package = field(1, "game")
    for path, size, digest in files:
        package += field(2, field(1, path) + field(3, size) + field(5, field(1, 0) + field(2, digest)))
    for d in dirs:
        package += field(3, field(1, d))
    return field(1, package) + field(1, field(1, "second package, ignored"))


swiftc = shutil.which("swiftc")
openssl = shutil.which("openssl")
if not swiftc or not openssl:
    check("swiftc and openssl for the behaviour checks", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-amazon-") as tmp:
        t = Path(tmp)
        import hashlib
        a, b = b"hello amazon", b"\x00" * 3
        body = manifest([("Bin\\Game.exe", len(a), hashlib.sha256(a).digest()), ("fuel.json", len(b), hashlib.sha256(b).digest())], ["Bin", "Data"])
        packed = lzma.compress(body, format=lzma.FORMAT_XZ)
        for name in ("key", "other"):
            subprocess.run([openssl, "genrsa", "-out", str(t / f"{name}.pem"), "2048"], check=True, capture_output=True)
            subprocess.run([openssl, "rsa", "-in", str(t / f"{name}.pem"), "-pubout", "-outform", "DER", "-out", str(t / f"{name}.spki")],
                           check=True, capture_output=True)
        (t / "body").write_bytes(body)
        subprocess.run([openssl, "dgst", "-sha256", "-sign", str(t / "key.pem"), "-out", str(t / "sig"), str(t / "body")], check=True)
        signature = (t / "sig").read_bytes()
        header = field(1, field(1, 1)) + field(2, field(1, 0) + field(2, hashlib.sha256(body).digest())) + field(3, field(1, 0) + field(2, signature))
        good = len(header).to_bytes(4, "big") + header + packed
        (t / "good.proto").write_bytes(good)
        bad = bytearray(good)
        bad[-40] ^= 1                                   # inside the packed body: it no longer unpacks or no longer matches
        (t / "bad.proto").write_bytes(bytes(bad))
        tampered_body = manifest([("..\\evil.exe", 1, hashlib.sha256(b"x").digest())])
        evil = len(header).to_bytes(4, "big") + header + lzma.compress(tampered_body, format=lzma.FORMAT_XZ)
        (t / "evil.proto").write_bytes(evil)
        stub = t / "stub.swift"
        stub.write_text("import Foundation\nstruct LogEntry { enum Level { case info, error } }\n"
                        "final class LogStore { nonisolated(unsafe) static let shared = LogStore(); var lines: [String] = []\n"
                        "    func log(_ m: String, level: LogEntry.Level = .info) { lines.append(m) } }\n")
        exe = t / "bcd-amazon-checks"
        r = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-o", str(exe), str(stub),
                            str(APP / "SwiftAmazon/AmazonManifest.swift"), str(APP / "SwiftAmazon/AmazonAuth.swift"),
                            str(APP / "SwiftAmazon/AmazonAPI.swift"), str(R / "tests/host/bcd_amazon_checks.swift")], capture_output=True, text=True)
        if check("the store's own files compile on the host with the harness", r.returncode == 0, (r.stderr or r.stdout)[-3000:]):
            r = subprocess.run([str(exe), str(t)], capture_output=True, text=True, timeout=120)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print("  " + line)
            n = sum(1 for l in r.stdout.splitlines() if l.startswith("PASS"))
            check(f"the Swift harness: {n} checks, none failing", r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-2000:])

# ---- 2. off path
lib = (APP / "SwiftAmazon/AmazonLibrary.swift").read_text()
hooks = (APP / "BCDAmazon/BCDLiveStoreAmazon.swift").read_text()
live = (APP / "BCDLiveStore.swift").read_text()
page_live = (APP / "BCDGamePageLive.swift").read_text()
check("the store is env.BCD_AMAZON, off unless set", 'static var enabled: Bool { MadeiraConfig.flag("BCD_AMAZON", fallback: false) }' in lib)
funcs = re.findall(r"\n    (?:func|var) (amazon\w*)[^\n]*\{\n([^\n]*)", hooks)
first_lines = {name: line.strip() for name, line in funcs}
check("every hook returns at its first test while the store is off",
      len(first_lines) >= 13 and "amazon" in first_lines
      and all(l.startswith("guard AmazonLibrary.enabled") or l.startswith("AmazonLibrary.enabled ?") or l.startswith("guard front == .amazon, AmazonLibrary.enabled")
                                     or l.startswith("BCDAmazonSignal.shared.subject") for l in first_lines.values()), first_lines)
check("the start of the library's work (start, refresh, sign-in) checks the switch too",
      "guard Self.enabled, !started else { return }" in lib and "guard Self.enabled, signedIn, !refreshing else { return }" in lib)
for hook in ("amazonChanges", "amazonAppeared()", "amazonBecameActive()", "amazonMerge(&games)", "amazonDownloads()", "amazonAccounts()",
             "amazonPlayEntry(game)", "amazon(.install, game.id)", "amazon(.uninstall, game.id)", "amazon(.pause, id)", "amazon(.resume, id)",
             "amazon(.cancel, id)", "amazonAccountAction(signIn: true, front)", "amazonAccountAction(signIn: false, front)", "amazonRefresh()"):
    check(f"BCDLiveStore calls {hook}, after the EA store's", hook in live)
check("the game page asks the store for its entry, note and changes", all(h in page_live for h in ("amazonEntryID(game.id)", "amazonNote(game.id)", "amazonDetailChanges")))
check("the library's chips show a store only with games or an account (the Amazon chip stays hidden while off)",
      "case .store(let s): return store.count(s) > 0 || store.accounts.contains { $0.store == s && $0.available }" in (APP / "BCDLibraryView.swift").read_text())
outside = [p.name for p in APP.rglob("*.swift") if "AmazonLibrary." in p.read_text() and p.parent.name not in ("SwiftAmazon", "BCDAmazon")
           and p.name != "ConfigCatalog.generated.swift"]   # the catalog names the switch's source file
check("only the store's own folders name AmazonLibrary (BCD's screens go through the hooks)", not outside, outside)

# ---- 3. the rest
views = (APP / "BCDAmazon/BCDAmazonViews.swift").read_text()
check("Settings › Accounts: the card, its switch off by default, sign-in on Amazon's own page",
      "BCDAmazonCard()" in (APP / "BCDStores/BCDStoreCards.swift").read_text() and 'MadeiraConfig.set("env.BCD_AMAZON", on ? "1" : nil)' in views
      and "BCDLiveStore.shared.signIn(.amazon)" in views and "BCDWebSignIn.present(page)" in hooks)
check("tokens in the Keychain, this device only, never logged",
      "kSecAttrAccessibleWhenUnlockedThisDeviceOnly" in (APP / "SwiftAmazon/AmazonAuth.swift").read_text()
      and not re.search(r"log\([^\n]*(accessToken|refreshToken|code\))", (APP / "SwiftAmazon/AmazonAuth.swift").read_text() + lib + hooks))
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("every new file is in the project", all(f'path = "{d}/{n}";' in pbx for d, n in (
    ("SwiftAmazon", "AmazonManifest.swift"), ("SwiftAmazon", "AmazonAuth.swift"), ("SwiftAmazon", "AmazonAPI.swift"),
    ("SwiftAmazon", "AmazonLibrary.swift"), ("BCDAmazon", "BCDLiveStoreAmazon.swift"), ("BCDAmazon", "BCDAmazonViews.swift"))))
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
taken = ("AmazonManifest.swift", "AmazonAuth.swift", "AmazonAPI.swift")
check("Porthole's files keep their licence and copyright, and have their rows",
      all((APP / "SwiftAmazon" / n).read_text().startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright (C) 2026 The Porthole authors.")
          and f"| `app/Madeira/SwiftAmazon/{n}` |" in notices for n in taken))
check("the workflow runs this check", "run: python3 tests/host/check-amazon.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: Amazon Games" if failures else "PASS: Amazon Games")
sys.exit(1 if failures else 0)
