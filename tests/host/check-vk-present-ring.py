#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Windows Vulkan game's presents go into the frame-time ring (build 702; tools/patch-wine-vulkan-frame-ring.py).

Checks:
  1. the patch applies once to Wine's committed win32u/vulkan.c (a second run only says so), changes nothing but the one block after the
     host's vkQueuePresentKHR, and fails by name when its anchor moved;
  2. with tools/patch-wine-vulkan-suboptimal.py and tools/patch-wine-vulkan-wow64.py, the three apply in every order with the same result;
  3. the block, compiled on the host: one push per accepted present (VK_SUCCESS, VK_SUBOPTIMAL_KHR), none for an error, and a link
     without the app's ring (the weak import undefined) skips the call;
  4. wiring: win32u-unix compiles vulkan.c through vulkan_ios.c, the app has the ring, the HUD and the session read it, and CI runs this
     check and then the patch before win32u-unix is built.

    tests/host/check-vk-present-ring.py [--require-wine]     WINE_SRC=<wine tree> to name another tree
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


sys.path.insert(0, str(R / "tools"))
import importlib.util
spec = importlib.util.spec_from_file_location("wow64", R / "tools/patch-wine-vulkan-wow64.py")
wow64 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wow64)
RELS = [rel for rel, _, _ in wow64.FILES]
if "dlls/win32u/vulkan.c" not in RELS:
    RELS.append("dlls/win32u/vulkan.c")

wine = Path(os.environ.get("WINE_SRC", R / "wine"))


def pristine(rel):
    """Wine's committed file: from the submodule's HEAD, else (a developer machine) the fork's local bare copy at the pinned commit."""
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


tool = R / "tools/patch-wine-vulkan-frame-ring.py"
src = tool.read_text()
check("BCD's header on the patch script", src.startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)"))

files = {rel: pristine(rel) for rel in RELS}
if any(v is None for v in files.values()):
    print("SKIP: no Wine sources (check out the wine submodule, or set WINE_SRC)" if not REQUIRE else "FAIL: no Wine sources")
    if REQUIRE:
        ok = False
else:
    w32 = files["dlls/win32u/vulkan.c"]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)

        def tree(name):
            for rel, text in files.items():
                p = tmp / name / "wine" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            return tmp / name

        # 1. once, idempotent, nothing else changed, a moved anchor fails by name
        a = tree("a")
        r1, r2 = run_script(tool.name, a), run_script(tool.name, a)
        patched = (a / "wine/dlls/win32u/vulkan.c").read_text()
        check("the patch applies once; a second run only says it is there",
              r1.returncode == 0 and "Vulkan frame ring patched" in r1.stdout and r2.returncode == 0 and "already patched" in r2.stdout
              and patched.count("madeira-bcd frame ring (Vulkan)") == 1, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        anchor = "    res = device->p_vkQueuePresentKHR( queue->host.queue, present_info );\n"
        i = patched.index(anchor) + len(anchor)
        block = patched[i:patched.index("\n    }\n", i) + 7]
        check("nothing but the block after the host's present changed", patched.replace(block, "", 1) == w32, block)
        fn = patched[patched.index("static VkResult win32u_vkQueuePresentKHR("):]
        fn = fn[:fn.index("\n}\n")]
        check("the block is inside win32u_vkQueuePresentKHR, right after the host's present, before the per-swapchain loop",
              block in fn and fn.index(anchor) + len(anchor) == fn.index(block) and fn.index(block) < fn.index("client_surface_present( surface->client );"))
        check("win32u_vkQueuePresentKHR takes no lock of its own: the Pause gate can wait in the push (surfaces_lock is only taken inside client_surface_present)",
              "pthread_mutex_lock" not in fn and "surfaces_lock" not in fn, fn)
        check("the push is a weak import, only for an accepted present",
              "extern void bcd_frame_ring_push(void) __attribute__((weak_import));" in block and "if (res >= VK_SUCCESS)" in block
              and "if (bcd_frame_ring_push)" in block)
        bad = tmp / "bad"
        (bad / "wine/dlls/win32u").mkdir(parents=True)
        (bad / "wine/dlls/win32u/vulkan.c").write_text(w32.replace(anchor, anchor.replace("present_info )", "info )")))
        r = run_script(tool.name, bad)
        check("an anchor that moved fails by name", r.returncode != 0 and "anchor found 0 times" in (r.stdout + r.stderr), r.stdout + r.stderr)

        # 2. every order of the three scripts gives the same file
        names = [tool.name, "patch-wine-vulkan-suboptimal.py", "patch-wine-vulkan-wow64.py"]
        results, errors = set(), []
        for k, order in enumerate(itertools.permutations(names)):
            t = tree(f"order{k}")
            for n in order:
                r = run_script(n, t)
                if r.returncode != 0:
                    errors.append(f"{order}: {n}: {r.stdout}{r.stderr}")
            out = (t / "wine/dlls/win32u/vulkan.c").read_text()
            results.add(out)
            if not ("madeira-bcd frame ring (Vulkan)" in out and "ignore_host_suboptimal" in out and "host_alloc" in out):
                errors.append(f"{order}: a change is missing")
        check("with the suboptimal and 32-bit Vulkan patches, all six orders apply and give the same win32u/vulkan.c",
              not errors and len(results) == 1, "\n".join(errors))

        # 3. the block compiled: pushes per accepted present, none for an error, skipped without the ring
        harness = tmp / "h.c"
        harness.write_text(
            "#include <stdio.h>\n"
            "typedef enum { VK_ERROR_OUT_OF_DATE_KHR = -1000001004, VK_ERROR_DEVICE_LOST = -4, VK_SUCCESS = 0,\n"
            "               VK_SUBOPTIMAL_KHR = 1000001003 } VkResult;\n"
            "extern int pushes;\n"
            "static void present(VkResult res)\n{\n" + block + "}\n"
            "int main(void) {\n"
            "  VkResult seq[] = { VK_SUCCESS, VK_SUBOPTIMAL_KHR, VK_ERROR_OUT_OF_DATE_KHR, VK_SUCCESS, VK_ERROR_DEVICE_LOST };\n"
            "  for (unsigned i = 0; i < sizeof seq / sizeof seq[0]; i++) present(seq[i]);\n"
            "  printf(\"%d\\n\", pushes); return 0; }\n")
        # The ring in a file of its own, as in the app (BCDFrameRing.c is not the translation unit of win32u's vulkan.c).
        (tmp / "ring.c").write_text("int pushes;\nvoid bcd_frame_ring_push(void) { pushes++; }\n")
        (tmp / "noring.c").write_text("int pushes = -1;\n")
        outs = {}
        for name, extra in (("with", [str(tmp / "ring.c")]), ("without", [str(tmp / "noring.c"), "-Wl,-U,_bcd_frame_ring_push"])):
            exe = tmp / f"h-{name}"
            cc = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Werror", "-o", str(exe), str(harness)] + extra, capture_output=True, text=True)
            outs[name] = subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip() if cc.returncode == 0 else cc.stderr
        check("compiled: three accepted presents of five push three times (errors push nothing)", outs["with"] == "3", outs)
        check("compiled: a link without the app's ring skips the call and runs", outs["without"] == "-1", outs)

# 4. wiring
vk_ios = (R / "build/win32u-unix/vulkan_ios.c").read_text()
build = (R / "build/win32u-unix/build.sh").read_text()
check("win32u-unix compiles Wine's vulkan.c through vulkan_ios.c",
      '#include "../../wine/dlls/win32u/vulkan.c"' in vk_ios and 'compile_one "$BUILD_DIR/vulkan_ios.c" "vulkan"' in build)
ring = (R / "app/Madeira/BCDFrameRing.c").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the app defines the ring's push and builds it", "void bcd_frame_ring_push(void) {" in ring and "BCDFrameRing.c in Sources" in pbx)
gl = (R / "app/Madeira/Winios/WiniosGL.m").read_text()
surf = (R / "build/win32u-unix/vulkan_surface_ios.c").read_text()
check("the present note under surfaces_lock stays a plain count (no ring push, no Pause gate there)",
      "madeira_vulkan_present_note();" in surf and "bcd_frame_ring_push" not in surf
      and "atomic_fetch_add_explicit(&vulkan_present_count, 1, memory_order_relaxed);" in gl)
hud = (R / "app/Madeira/BCDHUDLive.swift").read_text()
check("the HUD's FPS comes from the ring", "bcd_frame_ring_drain(" in hud and "d.fps = BCDFrameMath.fps(frames, now: now)" in hud)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI: this check, then the patch and its marker, before win32u-unix is built",
      "python3 tests/host/check-vk-present-ring.py --require-wine" in wf
      and wf.index("python3 tests/host/check-vk-present-ring.py --require-wine") < wf.index("python3 tools/patch-wine-vulkan-frame-ring.py")
      < wf.index('grep -q "madeira-bcd frame ring (Vulkan)" wine/dlls/win32u/vulkan.c') < wf.index("- name: Build win32u-unix"))

print("PASS: Vulkan presents in the frame ring" if ok else "FAIL: Vulkan presents in the frame ring")
sys.exit(0 if ok else 1)
