#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The committed aarch64 wow64.dll answers a NULL SID instead of reading through it (round 624, Garry's Mod).

tools/patch-wow64-token-null-sid.py patched the committed DLL once (49 bytes). This holds it there:
  - the committed file is the patched form (sha256), and the tool says so with --verify;
  - .text's VirtualSize covers the cave and stays below the next section;
  - when llvm-objdump is there, the detour, the cave and both tails disassemble to the reviewed instructions.
"""
import shutil
import struct
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
dll = root / "app/Madeira/aarch64-windows/wow64.dll"
tool = root / "tools/patch-wow64-token-null-sid.py"
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


r = subprocess.run([sys.executable, str(tool), "--verify", str(dll)], capture_output=True, text=True)
check("the committed wow64.dll is the patched form (tool --verify)", r.returncode == 0, r.stdout + r.stderr)

d = dll.read_bytes()
pe = struct.unpack_from("<I", d, 0x3c)[0]
nsec = struct.unpack_from("<H", d, pe + 6)[0]
optsz = struct.unpack_from("<H", d, pe + 20)[0]
secs = []
for i in range(nsec):
    o = pe + 24 + optsz + 40 * i
    secs.append((bytes(d[o:o + 8]).rstrip(b"\0").decode(), struct.unpack_from("<I", d, o + 8)[0], struct.unpack_from("<I", d, o + 12)[0],
                 struct.unpack_from("<I", d, o + 16)[0]))
text = [s for s in secs if s[0] == ".text"]
check(".text VirtualSize is 0x11fc0 (covers the cave 0x21f90..0x21fbc)", text and text[0][1] == 0x11fc0, secs)
if text:
    end = text[0][2] + text[0][1]
    nxt = min((s[2] for s in secs if s[2] > text[0][2]), default=None)
    check(".text still ends below the next section", nxt is None or end <= nxt, (hex(end), nxt and hex(nxt)))
    check("the cave lies inside .text's raw data", text[0][2] <= 0x21f90 and 0x21fbc <= text[0][2] + text[0][3])

objdump = shutil.which("llvm-objdump") or (shutil.which("xcrun") and "xcrun")
if objdump:
    cmd = ["xcrun", "llvm-objdump"] if objdump == "xcrun" else [objdump]

    def dis(a, b):
        out = subprocess.run(cmd + ["-d", f"--start-address={a:#x}", f"--stop-address={b:#x}", str(dll)], capture_output=True, text=True).stdout
        rows = []
        for l in out.splitlines():
            if not l.strip().startswith("18") or "\t" not in l:
                continue
            parts = l.split("\t")
            rows.append("\t".join(x.strip() for x in parts[1:]).split(" <")[0].strip())
        return rows
    base = 0x180000000
    want_site = ["b\t0x180021f90", "ldrb\tw8, [x1, #0x1]", "lsl\tx8, x8, #2"]
    want_cave = ["ldr\tx1, [sp]", "cbz\tx1, 0x180021fa0", "ldrb\tw8, [x1, #0x1]", "b\t0x180016748", "mov\tw22, #0x4",
                 "cmp\tx21, #0x4", "b.lo\t0x180021fb8", "cbz\tx20, 0x180021fb8", "str\twzr, [x20]", "b\t0x1800169b4", "b\t0x1800167a4"]
    site = dis(base + 0x16740, base + 0x1674c)
    cave = dis(base + 0x21f90, base + 0x21fbc)
    norm = lambda xs: [x.split("//")[0].strip() for x in xs]
    check("the detour at 0x16740 and the untouched next instructions", norm(site) == want_site, site)
    check("the cave disassembles to the reviewed instructions", norm(cave) == want_cave, cave)
    tail_ok = norm(dis(base + 0x169b4, base + 0x169c0))
    check("the success tail returns 0 and stores w22 to *retlen", tail_ok[:3] == ["mov\tw0, wzr", "cbz\tx19, 0x1800169c0", "str\tw22, [x25]"], tail_ok)
    tail_small = norm(dis(base + 0x167a4, base + 0x167ac))
    check("the small-buffer tail returns STATUS_BUFFER_TOO_SMALL", tail_small[:2] == ["mov\tw0, #0x23", "movk\tw0, #0xc000, lsl #16"], tail_small)
else:
    print("skip the disassembly: no llvm-objdump")

print("PASS: wow64 token null sid" if ok else "FAILED: wow64 token null sid")
sys.exit(0 if ok else 1)
