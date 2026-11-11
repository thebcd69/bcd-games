#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 Paulo Eduardo Correa (pauloec); thebcd69/bcd-games#43
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Taken from thebcd69/bcd-games pull request #43 by Paulo Eduardo Correa (2026-11-04). Modified by BCD (thebcd69), 2026-11-11: the patch is
# applied only where the local Metal compiler needs it (--if-needed), so the checks cover both states of the shader.
"""tools/patch-dxmt-metal-atomics.py: patch safety (idempotent, rejects unknown input, --verify, --if-needed on both answers of the compiler) and,
where a Metal compiler and the DXMT sources exist, that the real tessellation shader compiles in Metal 3.1 in its current state."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--require-metal", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
patcher = root / "tools/patch-dxmt-metal-atomics.py"
shader = root / "dxmt/src/airconv/shaders/air_tessellation.metal"
old = "return __metal_atomic_fetch_add_explicit(out_count, 1, int(memory_order_relaxed), __METAL_MEMORY_SCOPE_THREADGROUP__);"
new = "return atomic_fetch_add_explicit(reinterpret_cast<threadgroup atomic_int *>(out_count), 1, memory_order_relaxed);"
HEAD = "#include <metal_stdlib>\nusing namespace metal;\n"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def run(*a):
    return subprocess.run([sys.executable, str(patcher), *a], capture_output=True, text=True)


spec = importlib.util.spec_from_file_location("patch_dxmt_metal_atomics", patcher)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

with tempfile.TemporaryDirectory(prefix="bcd-metal-atomic-") as temp:
    folder = Path(temp)
    fixture = folder / "atomic.metal"
    fixture.write_text(HEAD + "int get_next_index(threadgroup int *out_count) {\n  " + old + "\n}\n")
    original = fixture.read_text()

    # the unconditional form (what the pull request did): replaces, is idempotent
    check("plain run replaces the private intrinsic", run("--source", str(fixture)).returncode == 0 and new in fixture.read_text() and old not in fixture.read_text())
    changed = fixture.read_bytes()
    check("a second run changes nothing", run("--source", str(fixture)).returncode == 0 and fixture.read_bytes() == changed)
    check("--verify passes on the patched shader", run("--source", str(fixture), "--verify").returncode == 0)
    fixture.write_text(original)
    check("--verify fails on the unpatched shader and leaves it alone", run("--source", str(fixture), "--verify").returncode != 0 and fixture.read_text() == original)
    fixture.write_text("unrecognised shader version\n")
    r = run("--source", str(fixture))
    check("unknown input is refused and left alone", r.returncode != 0 and fixture.read_text() == "unrecognised shader version\n")
    r = run("--source", str(fixture), "--if-needed")
    check("unknown input is refused by --if-needed too", r.returncode != 0 and fixture.read_text() == "unrecognised shader version\n")

    # --if-needed, with the answers of the compiler stubbed: a compiler that accepts the original leaves it byte for byte alone;
    # one that rejects it gets the public overload, and only when that compiles; one that rejects both is an error.
    def answer(table):
        mod.compiles = lambda source: table(source)
    for what, table, expect_patch, expect_exit in (
        ("accepts the original: not patched", lambda s: True, False, 0),
        ("has no Metal toolchain (no answer): not patched", lambda s: None, False, 0),
        ("rejects the private intrinsic, accepts the public one: patched", lambda s: new in s, True, 0),
        ("rejects both: an error, nothing written", lambda s: False, False, 1),
    ):
        fixture.write_text(original)
        answer(table)
        try:
            code = mod.main(["--source", str(fixture), "--if-needed"])
        except SystemExit as exit_:
            code = 1 if exit_.code else 0
        text = fixture.read_text()
        check("--if-needed, a compiler that " + what, code == expect_exit and ((new in text and old not in text) == expect_patch) and (expect_patch or text == original))

    # the real shader, when the DXMT sources are checked out: it compiles in Metal 3.1 in the state it is in (patched or not)
    if shader.exists() and shutil.which("xcrun"):
        probe = subprocess.run(["xcrun", "-sdk", "macosx", "metal", "--version"], capture_output=True)
        if probe.returncode == 0:
            r = subprocess.run(["xcrun", "-sdk", "macosx", "metal", "-std=metal3.1", "--target=air64-apple-macos14.0", "-c", str(shader),
                                "-o", str(folder / "tessellation.air")], capture_output=True, text=True)
            check("the real tessellation shader compiles in Metal 3.1 as it is", r.returncode == 0 and (folder / "tessellation.air").stat().st_size > 0, r.stderr[-1500:])
            text = shader.read_text()
            if old in text:   # an unpatched copy: the public overload must compile too, so that a build with a newer Xcode has its way out
                copy = folder / "air_tessellation.metal"
                copy.write_text(text.replace(old, new))
                r = subprocess.run(["xcrun", "-sdk", "macosx", "metal", "-std=metal3.1", "--target=air64-apple-macos14.0", "-c", str(copy),
                                    "-o", str(folder / "patched.air")], capture_output=True, text=True)
                check("the same shader with the public overload compiles in Metal 3.1", r.returncode == 0, r.stderr[-1500:])
        elif args.require_metal:
            raise SystemExit("Metal compiler is required")
        else:
            print("skip the real shader: no Metal toolchain")
    elif args.require_metal:
        raise SystemExit("Metal compiler and the DXMT sources are required")
    else:
        print("skip the real shader: no xcrun or no dxmt/src/airconv/shaders/air_tessellation.metal")
sys.exit(0 if ok else 1)
