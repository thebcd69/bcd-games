#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-rpc-np-diag.py: [rpc-np] logs a failed RPC named-pipe read's status; nothing else changes.

On the pinned Wine source (when checked out): applies once; the only change is a block on the failure path
that logs at most 16 times; the return value is untouched. The workflow runs it.
"""
from pathlib import Path
import shutil, subprocess, sys, tempfile
root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


src = root / "wine/dlls/rpcrt4/rpc_transport.c"
if src.is_file():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "wine/dlls/rpcrt4"
        d.mkdir(parents=True)
        # Start from the file as committed (a build tree's wine already carries the patch); without git use the working file.
        r = subprocess.run(["git", "-C", str(root / "wine"), "show", "HEAD:dlls/rpcrt4/rpc_transport.c"], capture_output=True, text=True)
        text = r.stdout if r.returncode == 0 and r.stdout else src.read_text()
        (d / "rpc_transport.c").write_text(text)
        p = root / "tools/patch-wine-rpc-np-diag.py"
        r1 = subprocess.run([sys.executable, str(p), str(Path(t) / "wine")], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(p), str(Path(t) / "wine")], capture_output=True, text=True)
        check("applies once", r1.returncode == 0 and "applied" in r1.stdout and "already" in r2.stdout)
        before, after = text, (d / "rpc_transport.c").read_text()
        added = after.replace(before.split("    release_np_event(connection, event);\n    return status")[0], "", 1)
        check("only the failure path logs, at most 16 times, and the return is unchanged",
              "if (status && status != STATUS_BUFFER_OVERFLOW)" in after and "<= 16" in after and
              after.count("return status && status != STATUS_BUFFER_OVERFLOW ? -1 : connection->io_status.Information;") == 1 and
              len(after.splitlines()) - len(before.splitlines()) == 7)
else:
    print("SKIP: no wine sources (check out the submodule)")
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs it", "python3 tools/patch-wine-rpc-np-diag.py wine" in wf)
print("PASS: rpc np diag" if ok else "FAILED: rpc np diag")
sys.exit(0 if ok else 1)
