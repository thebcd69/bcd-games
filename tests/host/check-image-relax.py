#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 64-bit image that finds no gap under BCD's furniture ceiling gets one more try without it (round 660, R1 S1, M004).

GTA IV through the Rockstar Launcher (KA69B6, 1BFMGY, build 653): the Launcher's delay-loaded wininet.dll was refused with c0000017 while
1.6 GB were free above the 32-bit window, the Launcher died ("Unable to launch game"). map_view treats the ceiling as advisory only for a
view that comes with no limit_high, and map_image_view always gave an image one. Checks, on build/ntdll-unix/virtual_ios.c: the retry
comes after every earlier attempt, only after STATUS_NO_MEMORY, only when the ceiling was BCD's own (the caller named none), never for a
32-bit guest image or the hook-zone image, without a limit_high, logged as [image-relax], and env.MADEIRA_IMAGE_RELAX = 0 (read at each
use, not kept for the app run) turns it off.
"""
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:600]) if detail and not cond else ""))
    ok &= bool(cond)


i = src.find("static NTSTATUS map_image_view( struct file_view **view_ret")
body = src[i:src.index("\n}\n", i)] if i >= 0 else ""
sw = src[src.find("static int ios_image_relax_enabled( void )"):]
sw = sw[:sw.find("\n}\n")]
check("the switch: 0 turns it off, read at each use", 'const char *e = getenv( "MADEIRA_IMAGE_RELAX" );' in sw
      and "return !(e && e[0] == '0' && !e[1]);" in sw and "static int enabled" not in sw, sw)
check("the caller's own limit is remembered before BCD's ceiling replaces it",
      "const BOOL own_ceiling = !limit_high;" in body and body.index("const BOOL own_ceiling = !limit_high;") < body.index("if (!limit_high)"))
gate = "if (status == STATUS_NO_MEMORY && own_ceiling && !wow_image && !hook_zone && ios_image_relax_enabled())"
check("the retry only after c0000017, only under BCD's own ceiling, never for a 32-bit or hook-zone image", gate in body)
if gate in body:
    after = body[body.index(gate):]
    check("it comes after every earlier attempt and asks for no limit_high",
          body.rindex("status = map_view( view_ret, NULL, size, top_down ? MEM_TOP_DOWN : 0, vprot, limit_low, zone_high, 0 );") < body.index(gate)
          and "map_view( view_ret, NULL, size, top_down ? MEM_TOP_DOWN : 0, vprot, limit_low, 0, 0 );" in after
          and '"[image-relax] #%ld image of 0x%lx bytes found no gap under the furniture ceiling %p: unclamped retry %s (%p) rev=r660\\n"' in after)
check("a request that succeeded before is not touched (the retry is the last thing before the return)",
      body.rstrip().endswith("return map_view( view_ret, NULL, size, top_down ? MEM_TOP_DOWN : 0, vprot, limit_low, limit_high, 0 );\n#endif")
      or "status = relaxed;\n    }\n    return status;\n#else" in body)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-image-relax.py" in wf)
print("PASS: image placement retry without the furniture ceiling" if ok else "FAILED: image placement retry")
sys.exit(0 if ok else 1)
