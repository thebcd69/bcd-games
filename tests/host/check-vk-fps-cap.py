#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app's frame-rate limit holds a Windows Vulkan game's presents (build 703; tools/patch-wine-vulkan-fps-cap.py and
madeira_vk_fps_cap_pace in build/win32u-unix/vulkan_ios.c).

Checks:
  1. the patch applies once to Wine's committed win32u/vulkan.c (a second run only says so), adds one line right before the host's
     present and nothing else, and fails by name when its anchor moved;
  2. with the frame ring, suboptimal and 32-bit Vulkan patches, the four apply in every order with the same result;
  3. the pacing, compiled on the host from vulkan_ios.c and timed: 60, 40 and 30 FPS hold a fast game to the limit; no limit
     (vsync modes 0 and 2), MADEIRA_VK_FPS_CAP=0 and a link without the app's getter never wait; a game slower than the limit
     never waits either;
  4. wiring: the helper sits before Wine's vulkan.c in vulkan_ios.c, and CI runs this check and then the patch before win32u-unix
     is built.

    tests/host/check-vk-fps-cap.py [--require-wine]     WINE_SRC=<wine tree> to name another tree
"""
import itertools
import os
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
REQUIRE = "--require-wine" in sys.argv
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    ok = ok and bool(cond)


import importlib.util
spec = importlib.util.spec_from_file_location("wow64", R / "tools/patch-wine-vulkan-wow64.py")
wow64 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wow64)
RELS = [rel for rel, _, _ in wow64.FILES]
if "dlls/win32u/vulkan.c" not in RELS:
    RELS.append("dlls/win32u/vulkan.c")
wine = Path(os.environ.get("WINE_SRC", R / "wine"))


def pristine(rel):
    if (wine / ".git").exists() or (wine / rel).is_file():
        r = subprocess.run(["git", "-C", str(wine), "show", "HEAD:" + rel], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
        if (wine / rel).is_file():
            return (wine / rel).read_text()
    bare = Path.home() / "madeira-local/kaynak/wine.git"
    if bare.is_dir():
        pin = subprocess.run(["git", "-C", str(R), "ls-tree", "HEAD", "wine"], capture_output=True, text=True).stdout.split()
        if len(pin) >= 3:
            r = subprocess.run(["git", "-C", str(bare), "show", f"{pin[2]}:{rel}"], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout:
                return r.stdout
    return None


def run_script(name, cwd):
    return subprocess.run([sys.executable, str(R / "tools" / name)], cwd=cwd, capture_output=True, text=True)


tool = R / "tools/patch-wine-vulkan-fps-cap.py"
check("BCD's header on the patch script", tool.read_text().startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)"))
anchor = "    res = device->p_vkQueuePresentKHR( queue->host.queue, present_info );\n"
line = "    madeira_vk_fps_cap_pace();   /* madeira-bcd fps cap (Vulkan) (tools/patch-wine-vulkan-fps-cap.py): the app's frame-rate limit */\n"

with tempfile.TemporaryDirectory(prefix="bcd-vk-fps-cap-") as tmp_name:
    tmp = Path(tmp_name)
    files = {rel: pristine(rel) for rel in RELS}
    if any(v is None for v in files.values()):
        print("SKIP: no Wine sources (check out the wine submodule, or set WINE_SRC)" if not REQUIRE else "FAIL: no Wine sources")
        if REQUIRE:
            ok = False
    else:
        w32 = files["dlls/win32u/vulkan.c"]

        def tree(name):
            for rel, text in files.items():
                p = tmp / name / "wine" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            return tmp / name

        a = tree("a")
        r1, r2 = run_script(tool.name, a), run_script(tool.name, a)
        patched = (a / "wine/dlls/win32u/vulkan.c").read_text()
        check("the patch applies once; a second run only says it is there",
              r1.returncode == 0 and "frame-rate limit patched" in r1.stdout and r2.returncode == 0 and "already patched" in r2.stdout,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        check("one line, right before the host's present, and nothing else", patched == w32.replace(anchor, line + anchor, 1) and w32.count(anchor) == 1)
        bad = tmp / "bad"
        (bad / "wine/dlls/win32u").mkdir(parents=True)
        (bad / "wine/dlls/win32u/vulkan.c").write_text(w32.replace(anchor, anchor.replace("present_info )", "info )")))
        r = run_script(tool.name, bad)
        check("an anchor that moved fails by name", r.returncode != 0 and "anchor found 0 times" in (r.stdout + r.stderr), r.stdout + r.stderr)
        names = [tool.name, "patch-wine-vulkan-frame-ring.py", "patch-wine-vulkan-suboptimal.py", "patch-wine-vulkan-wow64.py"]
        results, errors = set(), []
        for k, order in enumerate(itertools.permutations(names)):
            t = tree(f"order{k}")
            for n in order:
                r = run_script(n, t)
                if r.returncode != 0:
                    errors.append(f"{order}: {n}: {r.stdout}{r.stderr}")
            out = (t / "wine/dlls/win32u/vulkan.c").read_text()
            results.add(out)
            if not all(m in out for m in ("madeira-bcd fps cap (Vulkan)", "madeira-bcd frame ring (Vulkan)", "ignore_host_suboptimal", "host_alloc")):
                errors.append(f"{order}: a change is missing")
            if not (out.index("madeira_vk_fps_cap_pace();") < out.index(anchor) < out.index("madeira-bcd frame ring (Vulkan)")):
                errors.append(f"{order}: the limit is not before the present, or the ring not after it")
        check("with the frame ring, suboptimal and 32-bit Vulkan patches, all 24 orders apply and give the same win32u/vulkan.c",
              not errors and len(results) == 1, "\n".join(errors[:6]))

    # 3. the pacing, timed on the host
    vk_ios = (R / "build/win32u-unix/vulkan_ios.c").read_text()
    start = vk_ios.index("#include <mach/mach_time.h>\nextern int madeira_get_vsync_locked(void) __attribute__((weak_import));")
    body = vk_ios[start:vk_ios.index("\n}\n", vk_ios.index("static __attribute__((unused)) void madeira_vk_fps_cap_pace(void)")) + 3]
    (tmp / "pace.c").write_text("#include <stdio.h>\n#include <stdlib.h>\n#include <stdint.h>\n#include <time.h>\n#include <unistd.h>\n" + body + r'''
static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }
int main(int argc, char **argv) {
  int frames = atoi(argv[1]), work_us = atoi(argv[2]);
  double t0 = now_ms(), waited = 0;
  for (int i = 0; i < frames; i++) {
    if (work_us) usleep(work_us);
    double a = now_ms();
    madeira_vk_fps_cap_pace();
    waited += now_ms() - a;
  }
  printf("%.3f %.3f\n", (now_ms() - t0) / frames, waited / frames);
  return 0;
}
''')
    # round 707 (A2 F12): the governor's fixed frame cap (server_ios.c's ios_fps_cap), set here from TEST_CAP
    (tmp / "getter.c").write_text('#include <stdlib.h>\nint madeira_get_vsync_locked(void) { const char *m = getenv("TEST_MODE"); return m ? atoi(m) : 1; }\n'
                                  'volatile int ios_fps_cap;\n__attribute__((constructor)) static void cap(void) { const char *c = getenv("TEST_CAP"); ios_fps_cap = c ? atoi(c) : 0; }\n')
    builds = [subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-o", str(tmp / "pace"), str(tmp / "pace.c"), str(tmp / "getter.c")], capture_output=True, text=True),
              subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-o", str(tmp / "pace-nogetter"), str(tmp / "pace.c"), "-Wl,-U,_madeira_get_vsync_locked", "-Wl,-U,_ios_fps_cap"], capture_output=True, text=True)]
    check("the pacing compiles on the host, with the app's getter and without it", all(b.returncode == 0 for b in builds), "\n".join(b.stderr for b in builds))
    if all(b.returncode == 0 for b in builds):
        def timed(binary, frames, work_us, **env):
            e = {k: v for k, v in os.environ.items() if not k.startswith(("MADEIRA_", "TEST_"))}
            e.update(env)
            r = subprocess.run([str(tmp / binary), str(frames), str(work_us)], capture_output=True, text=True, env=e)
            mean, waited = (float(x) for x in r.stdout.split())
            return mean, waited, r.stderr

        # the first frame starts the grid without a wait, so N frames take N - 1 periods
        held = lambda mean, frames, period: period * (frames - 1) / frames * 0.97 <= mean <= period * (frames - 1) / frames + 2.5
        m60, _, log = timed("pace", 31, 0, TEST_MODE="1")
        check(f"60 FPS: a fast game is held to 16.7 ms a frame ({m60:.2f} ms over 31 frames)", held(m60, 31, 1000 / 60))
        check("...and the log says once that the limit holds Vulkan presents", log.count("[winios-vk] the frame-rate limit holds Vulkan presents") == 1, log)
        m40, _, _ = timed("pace", 21, 0, TEST_MODE="4")
        check(f"40 FPS: 25 ms ({m40:.2f} ms over 21 frames)", held(m40, 21, 25.0))
        m30, _, _ = timed("pace", 16, 0, TEST_MODE="3")
        check(f"30 FPS: 33.3 ms ({m30:.2f} ms over 16 frames)", held(m30, 16, 1000 / 30))
        free = [timed("pace", 50, 0, TEST_MODE=m)[0] for m in ("0", "2")]
        check(f"no limit (vsync modes 0 and 2): no wait ({free[0]:.3f}, {free[1]:.3f} ms)", all(x < 1.0 for x in free))
        # round 707 (A2 F12): the fixed frame cap holds a Vulkan game too, the lower of it and the limit
        c25, _, _ = timed("pace", 16, 0, TEST_MODE="0", TEST_CAP="25")
        check(f"a frame cap of 25 with no limit: 40 ms ({c25:.2f} ms over 16 frames)", held(c25, 16, 40.0))
        c25l, _, _ = timed("pace", 16, 0, TEST_MODE="1", TEST_CAP="25")
        check(f"a frame cap of 25 under the 60 FPS limit: the lower rate, 40 ms ({c25l:.2f} ms)", held(c25l, 16, 40.0))
        c60l, _, _ = timed("pace", 16, 0, TEST_MODE="3", TEST_CAP="60")
        check(f"a frame cap of 60 under the 30 FPS limit: 33.3 ms ({c60l:.2f} ms)", held(c60l, 16, 1000 / 30))
        cbad = timed("pace", 50, 0, TEST_MODE="0", TEST_CAP="5")[0]
        check(f"a cap outside 10...120 is none: no wait ({cbad:.3f} ms)", cbad < 1.0)
        off, _, log = timed("pace", 50, 0, TEST_MODE="1", MADEIRA_VK_FPS_CAP="0")
        check(f"MADEIRA_VK_FPS_CAP=0: no wait ({off:.3f} ms), and the log says so", off < 1.0 and "does not hold" in log, log)
        nog, _, _ = timed("pace-nogetter", 50, 0)
        check(f"a link without the app's getter: no wait ({nog:.3f} ms)", nog < 1.0)
        _, slow_wait, _ = timed("pace", 20, 25000, TEST_MODE="1")
        check(f"a game slower than the limit (25 ms a frame) never waits ({slow_wait:.3f} ms a frame)", slow_wait < 1.0)
        m_mid, mid_wait, _ = timed("pace", 31, 8000, TEST_MODE="1")
        check(f"a faster game (8 ms of work a frame) is held to 16.7 ms ({m_mid:.2f} ms, {mid_wait:.2f} ms waited a frame)",
              held(m_mid, 31, 1000 / 60) and mid_wait > 2.0)

# 4. wiring
vk_ios = (R / "build/win32u-unix/vulkan_ios.c").read_text()
check("vulkan_ios.c: the helper comes before Wine's vulkan.c and the dlopen / dlsym redefinitions",
      vk_ios.index("static __attribute__((unused)) void madeira_vk_fps_cap_pace(void)") < vk_ios.index("#define dlopen( name, flags )")
      < vk_ios.index('#include "../../wine/dlls/win32u/vulkan.c"'))
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI: this check, then the patch and its marker, before win32u-unix is built",
      "python3 tests/host/check-vk-fps-cap.py --require-wine" in wf
      and wf.index("python3 tests/host/check-vk-fps-cap.py --require-wine") < wf.index("python3 tools/patch-wine-vulkan-fps-cap.py")
      < wf.index('grep -q "madeira-bcd fps cap (Vulkan)" wine/dlls/win32u/vulkan.c') < wf.index("- name: Build win32u-unix"))

print("PASS: the frame-rate limit for Vulkan presents" if ok else "FAIL: the frame-rate limit for Vulkan presents")
sys.exit(0 if ok else 1)
