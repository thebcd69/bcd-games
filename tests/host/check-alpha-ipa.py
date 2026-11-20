#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/make-alpha-ipa.py (madeira-bcd FORK-ONLY): the public alpha IPA carries nothing
about the owner (owner, 2026-10-17).

Checks, on synthetic IPAs and a synthetic needles file (the real one lives outside the
repository):
  - a clean unsigned IPA: MadeiraRepo and MadeiraCommit are gone from Info.plist, its
    other keys and format stay, every other file is byte-identical with its zip attributes
    (a framework symlink stays a symlink);
  - a needle in any file, in any letter case: refused, nothing written, and the report names
    the needle by number only;
  - an IPA whose Mach-O carries a certificate (a CMS blob: /usr/bin/true, Apple-signed, fat)
    or a provisioning profile: refused; an ad-hoc signed one (no certificate, the
    entitlements sideloaders filter by, 2026-10-18) passes;
  - the tool reads its needles from outside the repository and is marked FORK-ONLY; CI runs
    this check.
"""
import os
import plistlib
import subprocess
import sys
import tempfile
import struct
import zipfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
TOOL = R / "tools/make-alpha-ipa.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


def ipa(path, extra=None, plist=None):
    pl = plist or {"CFBundleIdentifier": "com.thebcd69.bcd", "MadeiraRepo": "someone/fork",
                   "MadeiraCommit": "0123456789abcdef0123456789abcdef01234567", "MadeiraBuilt": "2026-10-17T12:00:00Z"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Payload/", b"")
        z.writestr("Payload/App.app/", b"")
        z.writestr("Payload/App.app/Info.plist", plistlib.dumps(pl, fmt=plistlib.FMT_BINARY))
        z.writestr("Payload/App.app/App", b"\xcf\xfa\xed\xfe plain machine code, Mirror, demirror")
        link = zipfile.ZipInfo("Payload/App.app/Frameworks/R.framework/R")
        link.external_attr = (0o120755 << 16)
        z.writestr(link, b"Versions/A/R")
        for name, data in (extra or {}).items():
            z.writestr(name, data)


src = TOOL.read_text()
check("the tool is marked FORK-ONLY", "FORK-ONLY" in src[:400])
check("its needles live outside the repository", 'os.path.expanduser("~/madeira-local/state/alpha-needles.txt")' in src)
CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "run: python3 tests/host/check-alpha-ipa.py" in CI.read_text())

with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    needles = t / "needles.txt"
    needles.write_text("# test\nSecret Person\nTEAMID1234\n")
    run = lambda a, b: subprocess.run([sys.executable, str(TOOL), str(a), str(b), "--needles", str(needles)],
                                      capture_output=True, text=True)

    def pe(cert=b""):
        """A minimal PE32+ file, with a certificate table holding `cert` when given."""
        data = bytearray(0x200)
        data[0:2] = b"MZ"
        struct.pack_into("<I", data, 0x3c, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", data, 0x80 + 24, 0x20b)
        if cert:
            struct.pack_into("<II", data, 0x80 + 24 + 112 + 32, len(data), len(cert))
            data += cert
        return bytes(data)

    ms = pe(b"0\x82... CN=Microsoft Corporation, O=Microsoft Corporation ...")
    ipa(t / "vcrt.ipa", extra={"Payload/App.app/x86_64-vcruntime/.gitkeep": b"",
                               "Payload/App.app/x86_64-vcruntime/vcruntime140.dll": ms,
                               "Payload/App.app/x86_64-vcruntime/mfc140u.dll": ms,
                               "Payload/App.app/wine/x86_64-windows/kernel32.dll": pe()})
    r = run(t / "vcrt.ipa", t / "vcrt-out.ipa")
    names = zipfile.ZipFile(t / "vcrt-out.ipa").namelist() if (t / "vcrt-out.ipa").exists() else []
    check("Microsoft's VC++ runtime DLLs are left out; the folder and unsigned Windows files stay",
          r.returncode == 0 and "Payload/App.app/x86_64-vcruntime/.gitkeep" in names
          and not any(n.endswith(".dll") and "x86_64-vcruntime" in n for n in names)
          and "Payload/App.app/wine/x86_64-windows/kernel32.dll" in names and "2 Microsoft VC++" in r.stdout,
          r.stdout + r.stderr)
    ipa(t / "msdll.ipa", extra={"Payload/App.app/wine/x86_64-windows/d3dcompiler_47.dll": ms})
    r = run(t / "msdll.ipa", t / "msdll-out.ipa")
    check("any other Microsoft-signed Windows file: refused and nothing written",
          r.returncode != 0 and not (t / "msdll-out.ipa").exists() and "Microsoft-signed" in r.stderr, r.stdout + r.stderr)

    ipa(t / "clean.ipa", {"Payload/App.app/PlugIns/H.appex/Info.plist": plistlib.dumps({"CFBundleIdentifier": "com.thebcd69.bcd.JITHelper"})})
    r = run(t / "clean.ipa", t / "out.ipa")
    check("a clean IPA is written", r.returncode == 0 and (t / "out.ipa").exists(), r.stdout + r.stderr)
    if (t / "out.ipa").exists():
        zi, zo = zipfile.ZipFile(t / "clean.ipa"), zipfile.ZipFile(t / "out.ipa")
        pl = plistlib.loads(zo.read("Payload/App.app/Info.plist"))
        check("MadeiraRepo and MadeiraCommit are gone; the other keys stay",
              "MadeiraRepo" not in pl and "MadeiraCommit" not in pl
              and pl.get("MadeiraBuilt") == "2026-10-17T12:00:00Z" and pl.get("CFBundleIdentifier") == "com.thebcd69.bcd")
        check("Info.plist stays a binary plist", zo.read("Payload/App.app/Info.plist").startswith(b"bplist"))
        same = all(zi.read(n) == zo.read(n) and zi.getinfo(n).external_attr == zo.getinfo(n).external_attr
                   for n in zi.namelist() if not n.endswith("Info.plist"))
        check("every other file is byte-identical, with its attributes (a symlink stays one)",
              same and zi.namelist() == zo.namelist())

    ipa(t / "named.ipa", {"Payload/App.app/credits.txt": b"thanks to secret PERSON for everything"})
    r = run(t / "named.ipa", t / "named-out.ipa")
    check("a needle in any file, in any case: refused and nothing written",
          r.returncode != 0 and not (t / "named-out.ipa").exists() and "needle #1 in Payload/App.app/credits.txt" in r.stdout)
    check("the report never prints a needle", "secret" not in (r.stdout + r.stderr).lower())

    ipa(t / "team.ipa", plist={"CFBundleIdentifier": "com.thebcd69.bcd", "MadeiraNote": "TEAMID1234"})
    r = run(t / "team.ipa", t / "team-out.ipa")
    check("a needle inside Info.plist: refused", r.returncode != 0 and not (t / "team-out.ipa").exists())

    # Owner, 2026-11-16 (strict): the public IPA keeps com.thebcd69.bcd; the owner's signing App ID never reaches it.
    ipa(t / "ownid.ipa", plist={"CFBundleIdentifier": "com.someone.signer"})
    r = run(t / "ownid.ipa", t / "ownid-out.ipa")
    check("an app whose bundle identifier is not com.thebcd69.bcd (a private OTA's App ID): refused and nothing written",
          r.returncode != 0 and not (t / "ownid-out.ipa").exists() and "bundle identifier" in r.stderr, r.stdout + r.stderr)
    ipa(t / "ownext.ipa", {"Payload/App.app/PlugIns/H.appex/Info.plist": plistlib.dumps({"CFBundleIdentifier": "com.someone.signer.JITHelper"})})
    r = run(t / "ownext.ipa", t / "ownext-out.ipa")
    check("an extension with another identity: refused", r.returncode != 0 and not (t / "ownext-out.ipa").exists(), r.stdout + r.stderr)
    ipa(t / "bcd2.ipa", plist={"CFBundleIdentifier": "com.thebcd69.bcd2"})
    r = run(t / "bcd2.ipa", t / "bcd2-out.ipa")
    check("BCD2's identity is not the public one either: refused (the release publishes BCD only)", r.returncode != 0, r.stdout + r.stderr)
    tool = TOOL.read_text()
    check("the identity rule is in the tool", 'PUBLIC_ID = "com.thebcd69.bcd"' in tool)

    apple = Path("/usr/bin/true").read_bytes()
    ipa(t / "signed.ipa", {"Payload/App.app/App": apple,
                           "Payload/App.app/_CodeSignature/CodeResources": b"<plist/>"})
    r = run(t / "signed.ipa", t / "signed-out.ipa")
    check("a Mach-O signed with a certificate: refused",
          r.returncode != 0 and "certificate" in r.stderr and not (t / "signed-out.ipa").exists(), r.stderr)
    adhoc = t / "adhoc-bin"
    adhoc.write_bytes(apple)
    subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(adhoc)], check=True, capture_output=True)
    ipa(t / "adhoc.ipa", {"Payload/App.app/App": adhoc.read_bytes(),
                          "Payload/App.app/_CodeSignature/CodeResources": b"<plist/>"})
    r = run(t / "adhoc.ipa", t / "adhoc-out.ipa")
    check("an ad-hoc signed IPA (entitlements, no certificate): accepted",
          r.returncode == 0 and (t / "adhoc-out.ipa").exists(), r.stdout + r.stderr)
    ipa(t / "prof.ipa", {"Payload/App.app/embedded.mobileprovision": b"cms"})
    r = run(t / "prof.ipa", t / "prof-out.ipa")
    check("an IPA with a provisioning profile: refused", r.returncode != 0 and not (t / "prof-out.ipa").exists())

print("PASS: alpha IPA" if ok else "FAILED: alpha IPA")
sys.exit(0 if ok else 1)
