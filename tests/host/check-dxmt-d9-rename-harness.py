#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Direct3D 9 dynamic buffer refresh after a failed rename, run (upstream merge 2026-11-14).

Madeira's dxmt fork imported BCD's rename drain (BCD 63ec5184, their 0a690ad7) and wrote a harness for it,
dxmt/tests/d3d9/check_dynamic_rename_wait.py, which BCD's fork now carries: both refreshWholeMirror callers (vertex and index
buffers) are cut out of d3d9_buffer.cpp and run under ASan/UBSan against a fake device: a rename that fails once or twice, a wait
that succeeds or is abandoned, a direct-mapped buffer, a clean one, a locked one. tests/host/check-d9-rename-wait.py reads the same
code as source invariants; this runs it.

The harness cuts the pinned source, so it runs on the files of the submodule's commit (git show HEAD:...), not on the working tree:
the workflow's tools/patch-dxmt-d9-ib-range.py adds census and range calls to the same two functions, which the fake device does not
have. SKIP (exit 0) without a dxmt git checkout, the harness or a C++ compiler."""
import pathlib
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
D = R / "dxmt"
wf = (R / ".github/workflows/build-ipa.yml").read_text()
if "python3 tests/host/check-dxmt-d9-rename-harness.py" not in wf:
    print("FAIL CI does not run this check")
    sys.exit(1)


def show(path):
    r = subprocess.run(["git", "-C", str(D), "show", f"HEAD:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


harness = show("tests/d3d9/check_dynamic_rename_wait.py") if (D / "src/d3d9").is_dir() else None
buffer_cpp = show("src/d3d9/d3d9_buffer.cpp") if harness else None
if not harness or not buffer_cpp:
    # A pin of the 2026-11-14 merge or later must carry the harness (its marker: the fork's in-process share switch); only an
    # older pin, or no checkout at all, is a SKIP.
    marker = show("src/d3d11/d3d11_texture_device.cpp") if (D / "src/d3d9").is_dir() else None
    if marker and b"MADEIRA_D3D11_LOCAL_SHARE" in marker:
        print("FAIL the dxmt pin is of the 2026-11-14 merge or later but tests/d3d9/check_dynamic_rename_wait.py is not in it")
        print("FAILED: dxmt-d9-rename-harness")
        sys.exit(1)
    print("SKIP: no dxmt git checkout with tests/d3d9/check_dynamic_rename_wait.py (a pin older than the 2026-11-14 merge)")
    sys.exit(0)
if not shutil.which("c++"):
    print("SKIP: no c++ compiler")
    sys.exit(0)
with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    (t / "tests/d3d9").mkdir(parents=True)
    (t / "src/d3d9").mkdir(parents=True)
    (t / "tests/d3d9/check_dynamic_rename_wait.py").write_bytes(harness)
    (t / "src/d3d9/d3d9_buffer.cpp").write_bytes(buffer_cpp)
    r = subprocess.run([sys.executable, str(t / "tests/d3d9/check_dynamic_rename_wait.py")], capture_output=True, text=True)
    print((r.stdout + r.stderr).strip()[-3000:])
    if r.returncode != 0 or "PASS" not in r.stdout:
        print("FAILED: dxmt-d9-rename-harness")
        sys.exit(1)
print("PASS: both Direct3D 9 mirror refreshes retry after the GPU drain (the pinned source, run under ASan/UBSan)")
