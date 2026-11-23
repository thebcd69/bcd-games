#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The size/move loop of a game window drains the app's input ring (round 707, agent G1 S23; MemeticBox HK9M6K); no Wine runs.

A click on a decorated game window's frame started sys_command_size_move (build/win32u-unix/defwnd_ios.c), whose GetMessage waits without
waking the driver in game mode: the button-up stayed in the app's ring and the game froze for good. The loop now marks its thread as a dialog
thread for as long as it runs (driver_ios.c winios_input_wake_enter / _leave: message_ios.c's wait then wakes every 16 ms), and puts the mark
it had back after; MADEIRA_GAME_INPUT_WAKE=0 turns it off. Checks: the loop's only exits are breaks (so the leave after it always runs), enter
comes before the loop and leave after it, and the two functions, compiled on the host, set and restore the mark.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
defwnd = (root / "build/win32u-unix/defwnd_ios.c").read_text()
driver = (root / "build/win32u-unix/driver_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


fn = defwnd[defwnd.index("static void sys_command_size_move( HWND hwnd, WPARAM wparam )"):]
fn = fn[:fn.index("\n}\n")]
loop = fn[fn.index("    for (;;)\n"):fn.index('winios_input_wake_leave( wake_saved );')]
check("enter comes right before the loop and leave right after it, before the exit line",
      fn.index("winios_input_wake_enter( &wake_saved );") < fn.index("    for (;;)\n") < fn.index("winios_input_wake_leave( wake_saved );")
      < fn.index('"[winios-move] exit moved=%d'))
check("the loop has no return or goto (every exit passes the leave)", re.search(r"\breturn\b|\bgoto\b", loop) is None)
check("CI runs this check", "python3 tests/host/check-move-loop-input-wake.py" in (root / ".github/workflows/build-ipa.yml").read_text())
enter = driver[driver.index("void winios_input_wake_enter( int *saved )"):]
enter = enter[:enter.index("void winios_input_wake_leave( int saved )")]
leave = driver[driver.index("void winios_input_wake_leave( int saved )"):]
leave = leave[:leave.index("\n}\n") + 3]
program = "#include <stdio.h>\n#include <stdlib.h>\nstatic __thread int winios_thread_shows_dialog;\n" + enter + leave + r'''
int main( int argc, char **argv )
{
    int saved, a, b;
    winios_thread_shows_dialog = atoi( argv[1] );
    winios_input_wake_enter( &saved );
    a = winios_thread_shows_dialog;
    winios_input_wake_leave( saved );
    b = winios_thread_shows_dialog;
    printf( "%d %d\n", a, b );
    return 0;
}
'''
tmp = Path(tempfile.mkdtemp(prefix="madeira-move-wake-"))
(tmp / "m.c").write_text(program)
r = subprocess.run(["cc", "-std=gnu11", "-O1", "-Wall", "-Werror", "-o", str(tmp / "m"), str(tmp / "m.c")], capture_output=True, text=True)
check("the two functions compile on the host", r.returncode == 0, r.stderr)
if r.returncode == 0:
    def run(mark, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
        e.update(env)
        return subprocess.run([str(tmp / "m"), str(mark)], capture_output=True, text=True, env=e).stdout.strip()
    check("an unmarked game thread is marked inside the loop and unmarked after it", run(0) == "1 0", run(0))
    check("a dialog thread stays marked after the loop", run(1) == "1 1", run(1))
    check("MADEIRA_GAME_INPUT_WAKE=0: nothing changes", run(0, MADEIRA_GAME_INPUT_WAKE="0") == "0 0", run(0, MADEIRA_GAME_INPUT_WAKE="0"))
print("PASS: the size/move loop drains input" if ok else "FAIL: the size/move loop drains input")
sys.exit(0 if ok else 1)
