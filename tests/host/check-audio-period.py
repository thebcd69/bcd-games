#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_AUDIO_IO_MS and MADEIRA_AUDIO_PERIOD_MS: cover a whole RemoteIO slice for a client that queues one engine period.

A shared-mode client that keeps only one 10 ms engine period queued (IAudioClient3 low latency, a Godot 4 game) wrote 480
frames at a time while RemoteIO pulled about 1024 per render callback, so every callback emptied the queue and played silence
for the rest: about 47 % of the audio reached the device. The app can ask the audio session for a smaller IO buffer
(env.MADEIRA_AUDIO_IO_MS = N), and the driver then reports an engine period of at least two slices, never under 10 ms.

Both are OFF in BCD unless set (the port this came from asks for 5 ms in every session, which changes the latency and the
number of render callbacks of every game): with neither variable the session keeps the system's slice, publishes nothing, and
GetDevicePeriod answers 10 ms (minimum 5 ms) with no log line, as it always did.

  1. ios_engine_period and ios_get_device_period, cut out of audio_null_ios.c and run on the host: the default; an explicit
     MADEIRA_AUDIO_PERIOD_MS (1..200; 10 = as before; out of range ignored); a granted slice (MADEIRA_AUDIO_IO_US): twice the
     slice, never under 10 ms; the 5 ms floor of the minimum period unless the period is longer than 10 ms; one log line only
     when the period is not the default;
  2. WineProcessBridge.m: madeira_audio_io_slice returns at once without MADEIRA_AUDIO_IO_MS (nothing asked, nothing published),
     is called once per session after the env.* lines are exported, and publishes MADEIRA_AUDIO_IO_US only when it asked.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
drv = (R / "build/ntdll-unix/audio_null_ios.c").read_text()
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


a = drv.index("static REFERENCE_TIME ios_engine_period(void) {")
b = drv.index("static NTSTATUS ios_get_device_period(void *args) {")
b2 = drv.index("\n}\n", b) + 3
chunk = drv[a:b2]
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
typedef int64_t REFERENCE_TIME;
typedef int NTSTATUS;
#define STATUS_SUCCESS 0
#define S_OK 0
struct get_device_period_params { REFERENCE_TIME *def_period; REFERENCE_TIME *min_period; int result; };
''' + chunk + r'''
int main(void)
{
    REFERENCE_TIME d = 0, m = 0;
    struct get_device_period_params p = { &d, &m, -1 };
    ios_get_device_period(&p);
    ios_get_device_period(&p);
    printf("%lld %lld\n", (long long)d, (long long)m);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        build = subprocess.run([cc, "-std=gnu99", "-Wall", "-Werror", "-o", exe, c], capture_output=True, text=True)
        check("the driver's period code builds on the host", build.returncode == 0, build.stderr[-1500:])
        if build.returncode == 0:
            def run(**env_extra):
                env = {k: v for k, v in os.environ.items() if k not in ("MADEIRA_AUDIO_PERIOD_MS", "MADEIRA_AUDIO_IO_US")}
                env.update(env_extra)
                r = subprocess.run([exe], capture_output=True, text=True, env=env)
                return r.stdout.strip(), r.stderr.strip()

            out, err = run()
            check("default: 10 ms, minimum 5 ms, no log line", out == "100000 50000" and err == "", (out, err))
            out, err = run(MADEIRA_AUDIO_PERIOD_MS="", MADEIRA_AUDIO_IO_US="")
            check("empty variables: the same", out == "100000 50000" and err == "", (out, err))
            out, err = run(MADEIRA_AUDIO_PERIOD_MS="10")
            check("MADEIRA_AUDIO_PERIOD_MS=10 is what it was (no log line)", out == "100000 50000" and err == "", (out, err))
            out, err = run(MADEIRA_AUDIO_PERIOD_MS="20")
            check("MADEIRA_AUDIO_PERIOD_MS=20: 20 ms, the minimum follows, one log line", out == "200000 200000" and err.count("\n") == 0 and "20.00 ms" in err, (out, err))
            out, err = run(MADEIRA_AUDIO_PERIOD_MS="0.5")
            check("an out-of-range period is ignored", out == "100000 50000", (out, err))
            out, err = run(MADEIRA_AUDIO_PERIOD_MS="500")
            check("  also above 200 ms", out == "100000 50000", (out, err))
            out, err = run(MADEIRA_AUDIO_IO_US="21333")
            check("a granted 21.3 ms slice: two slices", out == "426660 426660" and "42.67 ms" in err, (out, err))
            out, err = run(MADEIRA_AUDIO_IO_US="5333")
            check("a granted 5.3 ms slice: two slices is just over 10 ms", out == "106660 106660", (out, err))
            out, err = run(MADEIRA_AUDIO_IO_US="2000")
            check("a granted slice under 5 ms: never under 10 ms", out == "100000 50000" and err == "", (out, err))
            out, err = run(MADEIRA_AUDIO_IO_US="21333", MADEIRA_AUDIO_PERIOD_MS="15")
            check("an explicit period wins over the slice", out == "150000 150000", (out, err))
            out, err = run(MADEIRA_AUDIO_IO_US="abc")
            check("a slice that is no number is ignored", out == "100000 50000", (out, err))

fn = bridge[bridge.index("static void madeira_audio_io_slice(void)"):]
fn = fn[:fn.index("\n}\n")]
check("madeira_audio_io_slice asks for nothing without MADEIRA_AUDIO_IO_MS",
      "double ms = io && *io ? atof(io) : 0;\n    if (ms <= 0 || ms > 100) return;" in fn)
check("  and publishes the granted slice only after asking", fn.index("setPreferredIOBufferDuration") < fn.index('setenv("MADEIRA_AUDIO_IO_US"'))
check("it is called once, after the observers (the env.* lines are exported by then)",
      bridge.count("madeira_audio_io_slice();") == 1 and bridge.index("madeira_engine_observers_install();   /* madeira-bcd") < bridge.index("madeira_audio_io_slice();"))
check("the session setup is as it was: no preferred buffer duration there", bridge.count("setPreferredIOBufferDuration") == 2)
text = (R / "recommended/recommendations.json").read_text() + (R / "build/tools/autotune_rules.py").read_text()
# Round 625 (owner's item 13, agent D1 S3-b): Dead Rising's list asks for a 5 ms IO buffer from build 625; no other list and no auto-tune rule.
_recs = json.loads((R / "recommended/recommendations.json").read_text())
_io = sorted(r["id"] for r in _recs["rules"] if "MADEIRA_AUDIO_IO_MS" in json.dumps(r))
check("only Dead Rising's list sets the IO buffer (5 ms, build 625); no list sets the period and no auto-tune rule sets either",
      _io == ["dead-rising"] and {"line": "env.MADEIRA_AUDIO_IO_MS = 5", "minBuild": 625} in next(r for r in _recs["rules"] if r["id"] == "dead-rising")["config"]
      and "MADEIRA_AUDIO_PERIOD_MS" not in text and "MADEIRA_AUDIO_IO_MS" not in (R / "build/tools/autotune_rules.py").read_text())

print("PASS: audio period" if ok else "FAIL: audio period")
sys.exit(0 if ok else 1)
