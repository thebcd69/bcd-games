#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Check kernel32-usd.dll against the shipped kernel32.dll before it ships (tools/build-wine-extra-dlls.sh, build 663).

    verify-kernel32-usd.py NEW.dll SHIPPED.dll

Passes only when NEW is an ARM64EC PE with exactly the shipped file's exports (names, ordinals, forwards), its native
GetTickCount and GetTickCount64 no longer build the address 0x7ffe0000 while the shipped file's still do (so the patch of
tools/patch-wine-kernel32-usd-direct.py is in), and every other function has the shipped file's instructions (numbers aside:
addresses and displacements move when a function grows). Needs llvm-readobj and
llvm-objdump on PATH (llvm-mingw's).
"""
import re
import shutil
import subprocess
import sys


def tool(name):
    path = shutil.which(name)
    if not path:
        sys.exit("verify-kernel32-usd: %s is not on PATH" % name)
    return path


READOBJ, OBJDUMP = tool("llvm-readobj"), tool("llvm-objdump")


def run(*args):
    r = subprocess.run(list(args), capture_output=True, text=True)
    if r.returncode:
        sys.exit("verify-kernel32-usd: %s failed: %s" % (" ".join(args[:2]), r.stderr[-400:]))
    return r.stdout


def headers(path):
    text = run(READOBJ, "--file-headers", path)
    machine = re.search(r"Machine: (\S+)", text)
    base = re.search(r"ImageBase: 0x([0-9A-Fa-f]+)", text)
    return (machine.group(1) if machine else ""), int(base.group(1), 16) if base else 0


def exports(path):
    text = run(READOBJ, "--coff-exports", path)
    out = {}
    for block in text.split("Export {")[1:]:
        name = re.search(r"Name: (\S*)", block)
        ordinal = re.search(r"Ordinal: (\d+)", block)
        rva = re.search(r"RVA: 0x([0-9A-Fa-f]+)", block)
        fwd = re.search(r"ForwardedTo: (\S+)", block)
        key = name.group(1) if name and name.group(1) else "#" + (ordinal.group(1) if ordinal else "?")
        out[key] = (ordinal.group(1) if ordinal else None, fwd.group(1) if fwd else None, int(rva.group(1), 16) if rva else None)
    return out


def native_body(path, base, rva):
    """The ARM64EC body the export's x64 entry thunk jumps to, up to its first ret."""
    thunk = run(OBJDUMP, "-d", "--start-address=%#x" % (base + rva), "--stop-address=%#x" % (base + rva + 0x20), path)
    jump = re.search(r"\bjmp\s+0x([0-9a-f]+)", thunk)
    target = int(jump.group(1), 16) if jump else base + rva
    body = run(OBJDUMP, "-d", "--triple=aarch64-windows", "--start-address=%#x" % target, "--stop-address=%#x" % (target + 0x100), path)
    lines = []
    for line in body.splitlines():
        if not re.match(r"\s*[0-9a-f]+:", line):
            continue
        lines.append(line)
        if re.search(r"\bret\b", line):
            break
    return lines


def functions(path):
    """Every function of the ARM64EC code, its instructions with the numbers taken out: addresses, branch displacements and the
    x64 entry thunks' jump bytes move when one function grows, the instructions themselves do not."""
    text = run(OBJDUMP, "-d", "--triple=aarch64-windows", path)
    out, name = {}, None
    for line in text.splitlines():
        m = re.match(r"^[0-9a-f]+ <(.*)>:$", line)
        if m:
            name = m.group(1)
            out.setdefault(name, [])
            continue
        if name is None or not re.match(r"\s*[0-9a-f]+:", line):
            continue
        ins = line.split(":", 1)[1].split("//")[0].strip()
        ins = re.sub(r"^([0-9a-f]{2} )+", "", ins)
        ins = re.sub(r"^[0-9a-f]{8}\s+", "", ins)
        ins = re.sub(r"<[^>]*>", "", ins)
        ins = re.sub(r"#?-?0x[0-9a-f]+|#-?\d+", "N", ins)
        out[name].append(ins.strip())
    return out


def loads_kuser(lines):
    return any(re.search(r"#0x7ffe, lsl #16", l) for l in lines)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    new, old = sys.argv[1], sys.argv[2]
    (mnew, bnew), (mold, bold) = headers(new), headers(old)
    if "ARM64EC" not in mnew.upper() and "A641" not in mnew.upper():
        sys.exit("verify-kernel32-usd: %s is not ARM64EC (%s)" % (new, mnew))
    enew, eold = exports(new), exports(old)
    if {k: v[:2] for k, v in enew.items()} != {k: v[:2] for k, v in eold.items()}:
        missing = sorted(set(eold) - set(enew))[:8]
        extra = sorted(set(enew) - set(eold))[:8]
        sys.exit("verify-kernel32-usd: the exports differ from the shipped kernel32.dll (missing %s, extra %s)" % (missing, extra))
    for name in ("GetTickCount", "GetTickCount64"):
        if name not in enew or enew[name][2] is None:
            sys.exit("verify-kernel32-usd: no %s export" % name)
        now, before = native_body(new, bnew, enew[name][2]), native_body(old, bold, eold[name][2])
        if not now or loads_kuser(now):
            sys.exit("verify-kernel32-usd: %s still builds 0x7ffe0000 (the patch is not in)" % name)
        if not loads_kuser(before):
            sys.exit("verify-kernel32-usd: the shipped %s does not build 0x7ffe0000: this check no longer knows the shipped file" % name)
    # Everything else is the shipped code: the tree that builds it must still be the one the shipped file came from (build 663: 1787
    # functions, only these two differ).
    fnew, fold = functions(new), functions(old)
    moved = sorted(n for n in set(fnew) | set(fold) if fnew.get(n) != fold.get(n) and n not in ("GetTickCount", "GetTickCount64"))
    if moved:
        sys.exit("verify-kernel32-usd: %d functions besides GetTickCount and GetTickCount64 differ from the shipped kernel32.dll "
                 "(the tree is not the shipped file's): %s" % (len(moved), moved[:10]))
    print("verify-kernel32-usd: %s has the shipped exports (%d) and the shipped code in all %d functions but GetTickCount and "
          "GetTickCount64, which read KUSER_SHARED_DATA through BCD's answer" % (new, len(enew), len(fnew)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
