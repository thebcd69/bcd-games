#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Make the public alpha IPA from an unsigned one (madeira-bcd FORK-ONLY: never upstream).

Owner, 2026-10-17: the Discord alpha link serves an unsigned IPA that carries nothing about
the owner (nothing about the owner in it); a signed one showed the owner's name
in its certificate. Testers re-sign it anyway.

    tools/make-alpha-ipa.py IN.ipa OUT.ipa [--needles FILE]

- Drops the fork's build labels from Info.plist: MadeiraRepo and MadeiraCommit (the repo
  slug and a commit id lead to the fork on GitHub). MadeiraBuilt, a time, stays.
- Refuses an IPA with a provisioning profile, or any Mach-O whose code signature holds a
  certificate (a CMS blob): a real signature names its owner. An ad-hoc signature, which
  tools/ota-variants.py adds to carry the entitlements sideloaders filter by (2026-10-18),
  holds none and passes.
- Refuses an IPA whose app or extension has a bundle identifier other than com.thebcd69.bcd and
  com.thebcd69.bcd.<name> (owner, 2026-11-16: the owner's signing App ID, which his private OTA
  carries, never reaches anything public).
- Drops Microsoft's Visual C++ runtime (Payload/<app>.app/x86_64-vcruntime/*.dll; the owner's
  builds bundle it, its licence lets only licensed Visual Studio users redistribute it, and
  BCD downloads it on the device instead). The folder itself stays. Then refuses any other
  Windows file whose Authenticode signature names Microsoft: no Microsoft binary is ever in a
  public package (legal audit, 2026-10-18).
- Scans every file of the result, case-insensitively, for each line of the needles file
  (default ~/madeira-local/state/alpha-needles.txt, mode 600, never in the repository: the
  owner's names, account and device ids). One hit and nothing is written. Hits are reported
  by file and needle number, never by the needle itself.
Prints the SHA-256 of what it wrote.
"""
import argparse
import hashlib
import importlib.util
import io
import os
import plistlib
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DROP = ("MadeiraRepo", "MadeiraCommit")
VCRT_DIR = "/x86_64-vcruntime/"
DEFAULT_NEEDLES = os.path.expanduser("~/madeira-local/state/alpha-needles.txt")
PUBLIC_ID = "com.thebcd69.bcd"   # the public IPA's only identity (owner, 2026-11-16)


def load_needles(path):
    with open(path, encoding="utf-8") as f:
        out = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not out:
        sys.exit("make-alpha-ipa: the needles file is empty")
    return [n.lower().encode("utf-8") for n in out]


def certificate_signed(data):
    """True when a Mach-O (thin or fat) carries a code signature with a CMS blob."""
    def thin(off):
        magic = struct.unpack_from("<I", data, off)[0]
        if magic not in (0xfeedface, 0xfeedfacf):
            return False
        ncmds = struct.unpack_from("<I", data, off + 16)[0]
        p = off + (32 if magic == 0xfeedfacf else 28)
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", data, p)
            if size < 8:
                return False
            if cmd == 0x1d:  # LC_CODE_SIGNATURE
                dataoff = struct.unpack_from("<I", data, p + 8)[0]
                sb = off + dataoff
                if struct.unpack_from(">I", data, sb)[0] != 0xfade0cc0:  # CSMAGIC_EMBEDDED_SIGNATURE
                    return False
                count = struct.unpack_from(">I", data, sb + 8)[0]
                for i in range(count):
                    typ, boff = struct.unpack_from(">II", data, sb + 12 + 8 * i)
                    if typ == 0x10000:  # CSSLOT_SIGNATURESLOT: a wrapper with a CMS inside
                        bmagic, blen = struct.unpack_from(">II", data, sb + boff)
                        if bmagic == 0xfade0b01 and blen > 8:
                            return True
                return False
            p += size
        return False
    try:
        if len(data) < 32:
            return False
        if struct.unpack_from(">I", data, 0)[0] in (0xcafebabe, 0xcafebabf):  # fat
            wide = struct.unpack_from(">I", data, 0)[0] == 0xcafebabf
            n = struct.unpack_from(">I", data, 4)[0]
            step = 32 if wide else 20
            offs = [struct.unpack_from(">Q" if wide else ">I", data, 8 + i * step + 8)[0] for i in range(min(n, 64))]
            return any(thin(o) for o in offs)
        return thin(0)
    except struct.error:  # not a well-formed Mach-O (Java class files share the fat magic)
        return False


def microsoft_signed(data):
    """True for a PE file whose Authenticode certificate table names Microsoft."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return False
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if pe + 24 + 2 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        return False
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dirs = opt + (96 if magic == 0x10b else 112)
    if magic not in (0x10b, 0x20b) or dirs + 8 * 5 > len(data):
        return False
    off, size = struct.unpack_from("<II", data, dirs + 8 * 4)   # the security directory: a file offset
    if not size or off + size > len(data):
        return False
    blob = data[off:off + size]
    return any(name in blob for name in (b"Microsoft Corporation", b"Microsoft Code Signing", b"Microsoft Windows"))


def scan(data, needles):
    low = data.lower()
    return [i for i, n in enumerate(needles, 1) if n in low]


SIDELOAD_KEYS = (b"com.apple.developer.kernel.increased-memory-limit",
                 b"com.apple.developer.kernel.extended-virtual-addressing")


def sealed_app(ipa_path, root):
    """The app of `ipa_path`, unpacked into `root` with its permissions (ditto), when its
    signature seals the bundle; else None (nothing to re-seal)."""
    subprocess.run(["ditto", "-x", "-k", str(ipa_path), str(root)], check=True, capture_output=True)
    apps = list((Path(root) / "Payload").glob("*.app"))
    if len(apps) != 1:
        return None
    ok = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(apps[0])], capture_output=True).returncode == 0
    return apps[0] if ok else None


def reseal(data):
    """2026-10-19: dropping the build labels and Microsoft's DLLs breaks the ad-hoc seal
    ota-variants.py made ("invalid Info.plist (plist or signature have been modified)").
    Sideloaders re-sign anyway, but the package should verify: sign it again exactly as
    ota-variants.py does (the same entitlements: Increased Memory Limit and Extended Virtual
    Addressing, which SideStore/AltStore copy only when the app carries them), check the seal
    and those two entitlements, and pack it the same way."""
    spec = importlib.util.spec_from_file_location("ota_variants", Path(__file__).with_name("ota-variants.py"))
    ota = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ota)
    with tempfile.TemporaryDirectory(prefix="bcd-alpha-") as tmp:
        src = Path(tmp) / "in.ipa"
        src.write_bytes(data)
        root = Path(tmp) / "x"
        subprocess.run(["ditto", "-x", "-k", str(src), str(root)], check=True, capture_output=True)
        app = next((root / "Payload").glob("*.app"))
        ota.fakesign(app)
        v = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
        if v.returncode != 0:
            sys.exit(f"make-alpha-ipa: the re-sealed app does not verify: {v.stderr.strip()[:300]}")
        ents = subprocess.run(["codesign", "-d", "--entitlements", ":-", str(app)], capture_output=True).stdout
        missing = [k.decode() for k in SIDELOAD_KEYS if k not in ents]
        if missing:
            sys.exit(f"make-alpha-ipa: the re-sealed app lost {missing}")
        out = Path(tmp) / "out.ipa"
        # As ota-variants.py packs it: no ._ AppleDouble files the signature does not cover.
        subprocess.run(["ditto", "-c", "-k", "--norsrc", "--noextattr", "--noqtn", "--noacl",
                        "--keepParent", "Payload", str(out)], cwd=root, check=True)
        return out.read_bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--needles", default=DEFAULT_NEEDLES)
    a = ap.parse_args()
    needles = load_needles(a.needles)
    zin = zipfile.ZipFile(a.src)
    names = zin.namelist()
    bad = [n for n in names if n.endswith(".mobileprovision")]
    bad += [n for n in names if not n.endswith("/") and certificate_signed(zin.read(n))]
    if bad:
        sys.exit(f"make-alpha-ipa: refused, the IPA is signed with a certificate or carries a profile: {bad[:3]}")
    plists = [n for n in names if n.count("/") == 2 and n.startswith("Payload/") and n.endswith(".app/Info.plist")]
    if len(plists) != 1:
        sys.exit(f"make-alpha-ipa: expected one Payload/<app>.app/Info.plist, found {plists}")
    # Owner, 2026-11-16 (strict): the public package keeps BCD's own identity, never the owner's signing App ID that
    # his private OTA carries. The app and every extension must be com.thebcd69.bcd or com.thebcd69.bcd.<name>.
    ids = {}
    for n in names:
        if n.startswith("Payload/") and n.endswith("/Info.plist") and (n == plists[0] or ".appex/Info.plist" in n):
            ids[n] = plistlib.loads(zin.read(n)).get("CFBundleIdentifier", "")
    foreign = {n: i for n, i in ids.items() if not (i == PUBLIC_ID or i.startswith(PUBLIC_ID + "."))}
    if foreign:
        sys.exit(f"make-alpha-ipa: refused, a bundle identifier other than {PUBLIC_ID}: {sorted(foreign)[:3]}")
    buf = io.BytesIO()
    hits, dropped, stripped, microsoft = [], [], [], []
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            if VCRT_DIR in info.filename and info.filename.lower().endswith(".dll"):
                stripped.append(info.filename)
                continue
            data = zin.read(info)
            if not info.is_dir() and microsoft_signed(data):
                microsoft.append(info.filename)
            if info.filename == plists[0]:
                pl = plistlib.loads(data)
                fmt = plistlib.FMT_BINARY if data.startswith(b"bplist") else plistlib.FMT_XML
                for k in DROP:
                    if k in pl:
                        del pl[k]
                        dropped.append(k)
                data = plistlib.dumps(pl, fmt=fmt)
            if not info.is_dir():
                hits += [(info.filename, i) for i in scan(data, needles)]
            zout.writestr(info, data, compress_type=info.compress_type)
    if microsoft:
        sys.exit(f"make-alpha-ipa: refused, Microsoft-signed Windows files in a public package: {microsoft[:5]}")
    if hits:
        for name, i in hits[:20]:
            print(f"HIT needle #{i} in {name}")
        sys.exit(f"make-alpha-ipa: refused, {len(hits)} hit(s); nothing written")
    result, sealed = buf.getvalue(), "not sealed: left as it came"
    with tempfile.TemporaryDirectory(prefix="bcd-alpha-in-") as tmp:
        if sealed_app(a.src, tmp):
            result = reseal(result)
            # The new seal adds _CodeSignature files: everything is checked again.
            with zipfile.ZipFile(io.BytesIO(result)) as z:
                again = [(n, i) for n in z.namelist() if not n.endswith("/") for i in scan(z.read(n), needles)]
                certs = [n for n in z.namelist() if not n.endswith("/") and certificate_signed(z.read(n))]
            if again or certs:
                sys.exit(f"make-alpha-ipa: refused after re-sealing: {len(again)} needle hit(s), certificates in {certs[:3]}")
            sealed = "re-sealed ad-hoc (Increased Memory Limit, Extended Virtual Addressing)"
    with open(a.dst, "wb") as f:
        f.write(result)
    print(f"make-alpha-ipa: {len(names)} files, dropped {dropped or 'nothing'}, "
          f"left out {len(stripped)} Microsoft VC++ runtime DLL(s), "
          f"{len(needles)} needles, no hit; {sealed}; sha256 {hashlib.sha256(result).hexdigest()}")


if __name__ == "__main__":
    main()
