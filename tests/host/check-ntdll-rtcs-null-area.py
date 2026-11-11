#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-ntdll-rtcs-null-area.py: the ARM64EC ntdll's `[rtcs] pre:` argument is not read through a NULL CPU area (night round 2026-11-11, R1 S3b).

A native ARM64 thread of an ARM64EC session has no CPU area; ntdll.dll+0x61378 (`ldrb w8, [x8]`, the `insim=` argument of the `[rtcs] pre:` ERR in
prepare_exception_arm64ec) read NULL+0 on the first exception: the "identical redeliveries" storm of KT9K9S, MRX800, tur626's 28 sessions.

Checks: the committed app/Madeira/arm64ec-windows/ntdll.dll is either the file the patch was written for or its patched form (this check passes before and after the
lead commits the patched binary); on a copy the script patches exactly four bytes at 0x61378 (`ldrb w8,[x8]` -> `mov w8,wzr`), a second run says so, --verify accepts
the result and refuses the original, and a different DLL is refused; the shipped file's instruction sequence around the site is the one the patch assumes
(`ldr x8,[x18,#0x1788]` ... `ldrb w8,[x8]` ... `str w8,[sp,#0x28]`).
"""
from pathlib import Path
import hashlib, shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


patch = root / "tools/patch-ntdll-rtcs-null-area.py"
dll = root / "app/Madeira/arm64ec-windows/ntdll.dll"
SRC = "596ba0e4853e56ed61e006a20651edfb86cf49162bb0ac9bedc91d5873d43ada"
PATCHED = "8c391db8ec70e2736902f137de9526a66c3ebb0a000d2ea625b62948bd6b092a"
check("the patch script exists", patch.is_file())
d = dll.read_bytes() if dll.is_file() else b""
h = hashlib.sha256(d).hexdigest()
check("the committed ntdll.dll is the file the patch was written for or its patched form", h in (SRC, PATCHED), h)
ldr_x8 = struct.pack("<I", 0xF94BC648)
site = d[0x61378:0x6137C] if d else b""
check("the instruction at 0x61378 is `ldrb w8,[x8]` (unpatched) or `mov w8,wzr` (patched)", site in (bytes.fromhex("08014039"), bytes.fromhex("e8031f2a")), site.hex())
if h == SRC:
    check("the unpatched file loads the area pointer (`ldr x8,[x18,#0x1788]`) 0x1c bytes earlier and stores the byte to [sp,#0x28] 0x24 bytes later",
          d[0x6135C:0x61360] == ldr_x8 and d[0x6139C:0x613A0] == struct.pack("<I", 0xB9002BE8), (d[0x6135C:0x61360].hex(), d[0x6139C:0x613A0].hex()))
with tempfile.TemporaryDirectory(prefix="madeira-ntdll-rtcs-") as tmp:
    t = Path(tmp) / "ntdll.dll"
    if h == SRC:
        t.write_bytes(d)
        r1 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        r3 = subprocess.run([sys.executable, str(patch), "--verify", str(t)], capture_output=True, text=True)
        n = t.read_bytes()
        diff = [i for i in range(len(d)) if d[i] != n[i]]
        check("the script patches the copy (exactly four bytes, all at 0x61378..0x6137b), a second run says so, --verify accepts the result",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and r3.returncode == 0 and diff == [0x61378, 0x61379, 0x6137A, 0x6137B]
              and n[0x61378:0x6137C] == bytes.fromhex("e8031f2a") and hashlib.sha256(n).hexdigest() == PATCHED, (r1.stdout + r1.stderr, r2.stdout, r3.stderr, diff))
        o = Path(tmp) / "orig.dll"
        o.write_bytes(d)
        rv = subprocess.run([sys.executable, str(patch), "--verify", str(o)], capture_output=True, text=True)
        check("--verify refuses the original", rv.returncode != 0)
    elif h == PATCHED:
        rv = subprocess.run([sys.executable, str(patch), "--verify", str(dll)], capture_output=True, text=True)
        check("--verify accepts the committed (patched) file", rv.returncode == 0, rv.stdout + rv.stderr)
        t.write_bytes(d)
        r2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        check("running the script on the patched file says so and changes nothing", r2.returncode == 0 and "already in" in r2.stdout and t.read_bytes() == d, r2.stdout + r2.stderr)
    other = Path(tmp) / "other.dll"
    other.write_bytes(d[:0x61378] + b"\x00\x00\x00\x00" + d[0x6137C:] if d else b"")
    r4 = subprocess.run([sys.executable, str(patch), str(other)], capture_output=True, text=True)
    check("a different DLL (other bytes at the site) is refused and left alone", r4.returncode != 0 and other.read_bytes()[0x61378:0x6137C] == b"\x00\x00\x00\x00", r4.stdout + r4.stderr)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-ntdll-rtcs-null-area.py" in wf)
print("PASS: ntdll [rtcs] pre byte patch" if ok else "FAILED: ntdll [rtcs] pre byte patch")
sys.exit(0 if ok else 1)
