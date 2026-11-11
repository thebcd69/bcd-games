#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_KUSER_KEEP_REGS: registers that only hold a KUSER-looking value are left alone after the sub-floor service (signal_arm64_ios.c).

In the Mach exception handler the sub-floor service (ml953) runs first and emulates an access to KUSER_SHARED_DATA at
0x7ffe0000, after which step 1 ("redirect user_shared_data accesses") used to run anyway and rewrite EVERY general register
whose value lies in 0x7FFE0000..0x7FFEFFFF to the real shared-data address, data registers included. The Adventures of Elliot
(Denuvo) keeps the constant 0x7ffe0283 in rcx while it reads KUSER through another register; rcx came back as the real
address and the TLS pointer it computed was off by exactly the relocation.

That rebase is also what lets the next read through a rebased register go straight to the real page instead of faulting into
the service again, so every game that reads KUSER often runs with it today. In BCD the change is OFF unless
env.MADEIRA_KUSER_KEEP_REGS = 1 in the game's own file. Checks:
  1. the step-1 condition, cut out of the source and evaluated for every (srcwatch hit, handled, switch) combination: with the
     switch off the scan runs exactly when it always did (no srcwatch hit); with it on, also not after the service handled the access;
  2. the switch is read once and only "1" turns it on; only The Adventures of Elliot's list sets it, and no auto-tune rule.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/signal_arm64_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


m = re.search(r"if \((!srcwatch_hit && \(!handled \|\| !ios_kuser_keep_regs\(\)\)) &&\n\s+fault_addr >= 0x7FFE0000 && fault_addr < 0x7FFF0000 && ios_exc_usd\)", src)
check("step 1 is gated by the switch", bool(m))
a = src.index("static int ios_kuser_keep_regs( void )")
fn = src[a:src.index("\n}\n", a) + 3]
check("the switch is read once and only '1' turns it on", src.count('getenv( "MADEIRA_KUSER_KEEP_REGS" )') == 1 and "on = (e && e[0] == '1');" in fn)
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc or not m:
    print("SKIP host run")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
''' + fn + r'''
int main(void)
{
    int srcwatch_hit, handled;
    for (srcwatch_hit = 0; srcwatch_hit < 2; srcwatch_hit++)
        for (handled = 0; handled < 2; handled++)
            printf("%d", (''' + m.group(1) + r''') ? 1 : 0);
    printf("\n");
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        b = subprocess.run([cc, "-std=gnu99", "-Wall", "-Werror", "-o", exe, c], capture_output=True, text=True)
        check("the condition builds on the host", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            def run(**env_extra):
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_KUSER_KEEP_REGS"}
                env.update(env_extra)
                return subprocess.run([exe], capture_output=True, text=True, env=env).stdout.strip()
            # order: (hit 0, handled 0), (0, 1), (1, 0), (1, 1)
            for label, kw in (("unset", {}), ("empty", dict(MADEIRA_KUSER_KEEP_REGS="")), ("0", dict(MADEIRA_KUSER_KEEP_REGS="0")),
                              ("true", dict(MADEIRA_KUSER_KEEP_REGS="true"))):
                check("switch %s: the scan runs whenever no source-watch hit, handled or not (as always)" % label, run(**kw) == "1100", run(**kw))
            check("MADEIRA_KUSER_KEEP_REGS=1: not after the service handled the access", run(MADEIRA_KUSER_KEEP_REGS="1") == "1000",
                  run(MADEIRA_KUSER_KEEP_REGS="1"))

import json
recs = json.loads((R / "recommended/recommendations.json").read_text())["rules"]
# Night round 2026-11-11 (agents G5 S1, G11 S4, G7 S3/S17/S20): the protected-code family's lists carry it as an experiment (from build 602 for the
# games that did not start, 652 for Football Manager's), and two auto-tune rules offer it after the crash those games show (a wild pointer off by the
# real shared page's displacement); no other list and no other rule does.
rules_text = (R / "build/tools/autotune_rules.py").read_text()
check("only The Adventures of Elliot's list (build 603, its Denuvo TLS fault) and the protected-code family's (Football Manager 27 and the installed copies, Yakuza Kiwami 3, "
      "Metaphor, Prince of Persia: The Lost Crown, PRAGMATA) set it; the two rules kuser-register-rebase-protected-unity and kuser-rebase-protected-program offer it",
      sorted(r["id"] for r in recs if "MADEIRA_KUSER_KEEP_REGS" in json.dumps(r["config"])) == sorted([
          "the-adventures-of-elliot", "football-manager-27", "football-manager-other", "yakuza-kiwami-3-dark-ties", "metaphor-refantazio",
          "prince-of-persia-the-lost-crown", "pragmata"])
      and rules_text.count("MADEIRA_KUSER_KEEP_REGS = 1\"]") == 2 and "kuser-register-rebase-protected-unity" in rules_text and "kuser-rebase-protected-program" in rules_text)

print("PASS: KUSER keep regs" if ok else "FAIL: KUSER keep regs")
sys.exit(0 if ok else 1)
