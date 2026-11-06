#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Turn the [jumbo-code] rows of a session log into x86-64 assembly (env.MADEIRA_JUMBO_CODE_DUMP = 1, build/ntdll-unix/virtual_ios.c).

    tools/jumbo-code-disasm.py SESSION.log [--call N] [--place rip|ret] [--raw OUTDIR]

The log holds, for the first two reservations of 64 GB or more, 16-byte hex rows of the guest's code around two places: "rip" (the emulator's
saved RIP) and "ret" (the word on top of the guest stack, the return address into the game). Rows of one place are contiguous; this tool joins
them, writes them into an assembly file as .byte lines, assembles that with clang and lists it with objdump (both come with the Xcode command
line tools), and shifts every address and branch target to the guest's real address. A "??" row (an unreadable page) ends a blob. No
disassembler is bundled or downloaded. With --raw the blobs are also written as raw files (OUTDIR/call<N>-<place>-<address>.bin) for any other
disassembler (Ghidra, rasm2, ndisasm -b 64).

    tools/jumbo-code-disasm.py --selftest
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROW = re.compile(r"\[jumbo-code\] r\d+ #(\d+) (rip|ret) ([0-9a-f]+): ([0-9a-f?]{32})")
HEAD = re.compile(r"\[jumbo-code\] r\d+ #(\d+) size=(0x[0-9a-f]+) rip=(0x[0-9a-f]+) rsp=(0x[0-9a-f]+) \[rsp\]=(0x[0-9a-f]+)")


def blobs(text):
    """{(call, place): [(address, bytes)]}, rows of one place joined while contiguous and readable."""
    found = {}
    for m in ROW.finditer(text):
        call, place, addr, hexrow = int(m.group(1)), m.group(2), int(m.group(3), 16), m.group(4)
        found.setdefault((call, place), []).append((addr, hexrow))
    out = {}
    for key, rows in found.items():
        cur = None
        parts = []
        for addr, hexrow in rows:
            if "?" in hexrow:
                cur = None
                continue
            if cur is not None and addr == cur[0] + len(cur[1]):
                cur[1] += bytes.fromhex(hexrow)
            else:
                cur = [addr, bytes.fromhex(hexrow)]
                parts.append(cur)
        out[key] = [(a, bytes(b)) for a, b in parts]
    return out


def asm_of(data):
    lines = [".text", ".globl _blob", "_blob:"]
    for i in range(0, len(data), 8):
        lines.append(".byte " + ",".join(str(b) for b in data[i:i + 8]))
    return "\n".join(lines) + "\n"


def rebase(listing, base):
    """objdump numbers the blob from 0; make every address, branch target and rip-relative target the guest's."""
    def addr(m):
        return "%x" % ((int(m.group(1), 16) + base) & 0xffffffffffffffff)

    out = []
    for line in listing.splitlines():
        m = re.match(r"^\s*([0-9a-f]+):\s", line)
        if not m:
            continue
        line = re.sub(r"^(\s*)([0-9a-f]+):", lambda x: "%s%x:" % (x.group(1), (int(x.group(2), 16) + base) & 0xffffffffffffffff), line)
        line = re.sub(r"\s*<_blob[^>]*>", "", line)
        line = re.sub(r"(##\s*)0x([0-9a-f]+)", lambda x: x.group(1) + "0x" + addr(re.match(r"([0-9a-f]+)", x.group(2))), line)
        line = re.sub(r"(\b(?:call|jmp|j[a-z]+|loop[a-z]*)\s+)0x([0-9a-f]+)", lambda x: x.group(1) + "0x" + addr(re.match(r"([0-9a-f]+)", x.group(2))), line)
        out.append(line)
    return "\n".join(out)


def disassemble(data, base):
    cc, objdump = shutil.which("clang") or shutil.which("cc"), shutil.which("objdump")
    if not cc or not objdump:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        s, o = Path(tmp) / "b.s", Path(tmp) / "b.o"
        s.write_text(asm_of(data))
        r = subprocess.run([cc, "-arch", "x86_64", "-c", str(s), "-o", str(o)], capture_output=True, text=True)
        if r.returncode:
            return "assembling failed: " + r.stderr[-400:]
        r = subprocess.run([objdump, "-d", "--x86-asm-syntax=intel", str(o)], capture_output=True, text=True)
        return rebase(r.stdout, base) if r.returncode == 0 else "objdump failed: " + r.stderr[-400:]


def selftest():
    # the HALT site of tlou-i.exe (log 2 of build 633: [int3-guest] CODE rip-16..rip+15) as a [jumbo-code] ret place at 0x1491a8ece
    code = bytes.fromhex("488d15f9bcc501ffd6e8d27926ffcc4885db0f849f8a20ff33c9ffd390e995" + "00" * 17)
    row = "[jumbo-code] r634 #2 ret %x: %s"
    log = "\n".join([
        "[jumbo-code] r634 #2 size=0x8000000000 rip=0x1483b3d99 rsp=0x70a00edcf0 [rsp]=0x1491a8ede (the emulator's saved state; the stack is the game's)",
        row % (0x1491a8ec0, "00" * 16),
        row % (0x1491a8ed0, code[:16].hex()),
        row % (0x1491a8ee0, code[16:32].hex()),
        row % (0x1491a8ef0, "?" * 32),
        row % (0x1491a8f00, "90" * 16),
    ])
    found = blobs(log)
    assert list(found) == [(2, "ret")], found
    assert [(a, len(b)) for a, b in found[(2, "ret")]] == [(0x1491a8ec0, 48), (0x1491a8f00, 16)], found
    assert HEAD.search(log).group(5) == "0x1491a8ede"
    text = disassemble(found[(2, "ret")][0][1], 0x1491a8ec0)
    if text is None:
        print("skip the disassembly: no clang/objdump")
    else:
        # lea rdx, [rip + 0x1c5bcf9] at 0x1491a8ed0 (7 bytes) points at 0x1491a8ed7 + 0x1c5bcf9; call rel32 -0xd9862e from 0x1491a8ede
        assert re.search(r"1491a8ed0:\s+48 8d 15 f9 bc c5 01\s+lea\s+rdx, \[rip \+ 0x1c5bcf9\]\s+## 0x14ae04bd0", text), text
        assert re.search(r"1491a8ed7:\s+ff d6\s+call\s+rsi", text), text
        assert re.search(r"1491a8ed9:\s+e8 d2 79 26 ff\s+call\s+0x1484108b0\b", text), text
        assert re.search(r"1491a8ede:\s+cc\s+int3", text), text
        assert "1491a8f00" not in text, text   # the blob after the unreadable row is a blob of its own
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?")
    ap.add_argument("--call", type=int, help="only this reservation (the #N of the [jumbo-code] lines)")
    ap.add_argument("--place", choices=("rip", "ret"))
    ap.add_argument("--raw", help="also write each blob as a raw file in this directory")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.log:
        ap.error("a session log, or --selftest")
    text = Path(a.log).read_text(errors="replace")
    for m in HEAD.finditer(text):
        print("call #%s size=%s rip=%s rsp=%s [rsp]=%s" % m.groups())
    found = blobs(text)
    if not found:
        sys.exit("no [jumbo-code] rows in %s (env.MADEIRA_JUMBO_CODE_DUMP = 1 in the game's file, build 634 or later)" % a.log)
    for (call, place), parts in sorted(found.items()):
        if (a.call and call != a.call) or (a.place and place != a.place):
            continue
        for base, data in parts:
            print("\n=== call #%d, %s, %d bytes from 0x%x ===" % (call, place, len(data), base))
            if a.raw:
                Path(a.raw).mkdir(parents=True, exist_ok=True)
                (Path(a.raw) / ("call%d-%s-%x.bin" % (call, place, base))).write_bytes(data)
            lst = disassemble(data, base)
            print(lst if lst is not None else "(no clang/objdump: use --raw and another disassembler)")


if __name__ == "__main__":
    main()
