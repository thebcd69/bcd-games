#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wineserver-proc-log.py: the wineserver names new processes and a refused init_process_done (round 624, agent C S10 /
RETRO-1 R8; MGS Ground Zeroes HMAHPP, PFDA0D: a second copy whose init the server refused, while the log could not say why).

Checks: the patch applies to the pinned wine/server/process.c (a copy) once, then says "already patched"; an anchor that moved fails
by name and leaves the file alone; the refusal itself is unchanged and both lines are WINE_IOS only, 64 at most each; the two inserted
blocks, cut from the patched file and compiled on the host against a fake process, print exactly the documented lines; CI runs this
check and the patch before the server is built. SKIP of the file part (exit 0) when no wine source is available.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check and then the patch, before the wineserver is built",
      "python3 tests/host/check-wineserver-proc-log.py\n          python3 tools/patch-wineserver-proc-log.py wine" in wf)
build = (root / "build/wineserver/build.sh").read_text()
check("the iOS wineserver compiles wine/server/process.c (so the patch reaches it)", '"process_ios:$WINE_SRC/server/process.c:process.o"' in build)

wine = None
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine"):
    if (cand / "server/process.c").is_file():
        wine = cand
        break
if not wine:
    print("SKIP: no wine source (check out the submodule or set WINE_SRC)")
    print("PASS: wineserver proc log (static part)" if ok else "FAIL: wineserver proc log")
    sys.exit(0 if ok else 1)

patch = root / "tools/patch-wineserver-proc-log.py"
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "server").mkdir()
    pristine = (wine / "server/process.c").read_text()
    (t / "server/process.c").write_text(pristine)
    p1 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
    p2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
    check("applies once, then says already patched", p1.returncode == 0 and "lines added" in p1.stdout
          and p2.returncode == 0 and "already patched" in p2.stdout, p1.stdout + p1.stderr + p2.stdout)
    after = (t / "server/process.c").read_text()
    refuse = after[after.index("DECL_HANDLER(init_process_done)"):]
    refuse = refuse[:refuse.index("    current->teb = req->teb;")]
    check("the refusal is unchanged (STATUS_INVALID_PARAMETER, return) and the line is WINE_IOS only, 64 at most",
          refuse.index("#ifdef WINE_IOS") < refuse.index("[srv-proc] init_process_done") < refuse.index("#endif")
          < refuse.index("set_error( STATUS_INVALID_PARAMETER );") and "if (srv_proc_refused < 64)" in refuse)
    newp = after[after.index("    reply->pid = get_process_id( process );"):]
    newp = newp[:newp.index(" done:")]
    check("the new-process line comes after the reply's handle, WINE_IOS only, 64 at most",
          newp.index("reply->handle = alloc_handle_no_access_check(") < newp.index("#ifdef WINE_IOS") < newp.index("[srv-proc] new pid=")
          and "if (srv_proc_new < 64)" in newp)
    check("nothing else changed", re.sub(r"#ifdef WINE_IOS\n(?:(?!#endif).)*?srv-proc-log-v1.*?#endif\n", "", after, flags=re.S) == pristine)

    moved = pristine.replace("    if (is_process_init_done(process))\n    {\n        set_error( STATUS_INVALID_PARAMETER );",
                             "    if (is_process_init_done( process ))\n    {\n        set_error( STATUS_INVALID_PARAMETER );", 1)
    (t / "server/process.c").write_text(moved)
    p3 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
    check("a moved anchor fails by name, the file unchanged", p3.returncode != 0 and "init_process_done's refusal" in p3.stdout
          and (t / "server/process.c").read_text() == moved, p3.stdout + p3.stderr)

    b_refuse = refuse[refuse.index("#ifdef WINE_IOS") + len("#ifdef WINE_IOS\n"):refuse.index("#endif")]
    b_new = newp[newp.index("#ifdef WINE_IOS") + len("#ifdef WINE_IOS\n"):newp.index("#endif")]
    harness = r'''
#include <stdio.h>
typedef unsigned int process_id_t;
enum startup_state { STARTUP_IN_PROGRESS, STARTUP_DONE, STARTUP_ABORTED };
struct process { process_id_t id; enum startup_state startup_state; unsigned long long peb; };
static process_id_t get_process_id( struct process *p ) { return p->id; }
static void refused( struct process *process )
{
@REFUSE@
}
static void created( struct process *process, struct process *parent, unsigned int flags )
{
    struct { process_id_t pid; } r, *reply = &r;
    struct { unsigned int flags; } q, *req = &q;
    q.flags = flags; r.pid = get_process_id( process );
@NEW@
}
int main( void )
{
    struct process p = { 0x128, STARTUP_DONE, 0x7ffd1000 }, parent = { 0x24, STARTUP_DONE, 0 };
    created( &p, &parent, 0x4 );
    refused( &p );
    for (int i = 0; i < 100; i++) { created( &p, &parent, 0 ); refused( &p ); }
    return 0;
}
'''
    (t / "h.c").write_text(harness.replace("@REFUSE@", b_refuse).replace("@NEW@", b_new))
    cc = shutil.which("cc")
    b = subprocess.run([cc, "-std=c11", "-Wall", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the two inserted blocks compile on the host", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        err = subprocess.run([str(t / "h")], capture_output=True, text=True).stderr.splitlines()
        check("the documented lines, exactly",
              err[0] == "[srv-proc] new pid=0128 parent=0024 flags=0x4 rev=r624"
              and err[1] == "[srv-proc] init_process_done pid=0128 REFUSED: its start was already done (startup_state=1 peb=7ffd1000) rev=r624",
              err[:2])
        check("64 lines at most of each", sum(l.startswith("[srv-proc] new") for l in err) == 64
              and sum("REFUSED" in l for l in err) == 64, len(err))

print("PASS: wineserver proc log" if ok else "FAIL: wineserver proc log")
sys.exit(0 if ok else 1)
