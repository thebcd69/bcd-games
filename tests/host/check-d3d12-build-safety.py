#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The madeira_d3d12.dll build scripts fail loudly instead of shipping something half-built
(upstream Madeira #516; CI-only, nothing in the app or in a game changes).

tools/build-madeira-d3d12-dll.sh (what CI runs) and build/madeira-d3d12/build-pe.sh (the developer's):
  1. The vtable stubs are regenerated into the OUT folder and must equal the committed
     madeira-d3d12/src/pe/madeira_d3d12_stubs.h; a difference stops the build with a message (it used to
     overwrite the committed header, so a toolchain header update changed the source behind the build's back).
  2. MADEIRA_REQUIRE_KERNELS=1 fails the build when the helper kernels (mad_kernels.metal) did not build;
     unset it is the old behaviour (a warning and a DLL without them).
  3. build-pe.sh tries the kernels without the fi-nan-clean kernel (-DMAD_NO_NAN_CLEAN) as tools/ already did.

The segments of both scripts are cut out and run in bash with a fake xcrun and a fake generator; the
committed header must be byte-identical to what the pinned toolchain's d3d12.h generates (checked here
when that toolchain is next to the sources).
SKIP (exit 0) without bash.
"""
from pathlib import Path
import os
import shutil
import stat
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ci = root / "tools/build-madeira-d3d12-dll.sh"
dev = root / "build/madeira-d3d12/build-pe.sh"
wf = (root / ".github/workflows/build-ipa.yml").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


if not shutil.which("bash"):
    print("SKIP: no bash")
    sys.exit(0)


def seg(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i)]


STUB_A = {ci: 'echo "=== vtable stubs ==="', dev: "# Regenerate the stub tables"}
STUB_B = {ci: "\n# madeira-bcd: the shader cache's converter identity", dev: "\n# Build-time inputs of madeira_d3d12.c"}
KER_A = 'echo "=== helper kernels (metallib) ==="'
KER_B = {ci: 'echo "=== madeira_d3d12.dll (arm64ec) ==="', dev: 'echo "=== madeira_d3d12.dll (arm64ec) ==="'}

with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    bin_ = t / "bin"
    bin_.mkdir()
    # a fake xcrun: succeeds only when the command line holds $FAKE_NEED (or always when it is empty); the output file is written
    xcrun = bin_ / "xcrun"
    xcrun.write_text('''#!/bin/bash
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
echo "xcrun $*" >> "$FAKE_LOG"
if [ -n "${FAKE_FAIL_ALL:-}" ]; then echo "error: fake compile failure" >&2; exit 1; fi
if [ -n "${FAKE_NEED:-}" ] && [[ " $* " != *" $FAKE_NEED "* ]]; then echo "error: fake compile failure" >&2; exit 1; fi
[ -n "$out" ] && printf 'MTLB-fake' > "$out"
exit 0
''')
    xcrun.chmod(xcrun.stat().st_mode | stat.S_IEXEC)

    for script in (ci, dev):
        s = script.read_text()
        name = script.relative_to(root).as_posix()

        # ---- 1. the stubs
        stub = seg(s, STUB_A[script], STUB_B[script])
        for case, gen_out, expect_rc in (("same", "stubs-A\n", 0), ("different", "stubs-B\n", 1)):
            w = t / f"stubs-{script.stem}-{case}"
            (w / "src").mkdir(parents=True)
            (w / "out").mkdir()
            (w / "src/madeira_d3d12_stubs.h").write_text("stubs-A\n")
            (w / "src/gen_vtables.py").write_text("import sys\nopen(sys.argv[2], 'w').write(%r)\n" % gen_out)
            env = dict(os.environ, SRC=str(w / "src"), OUT=str(w / "out"), R=str(w), REPO_ROOT=str(w))
            prog = "set -eu\n" + stub + "\necho reached-the-end\n"
            r = subprocess.run(["bash", "-c", prog], env=env, capture_output=True, text=True, cwd=w)
            check(f"{name}: generated stubs {case} from the committed header -> exit {expect_rc}"
                  + (" and the build goes on" if expect_rc == 0 else " with a message, the committed header untouched"),
                  r.returncode == expect_rc and (("reached-the-end" in r.stdout) == (expect_rc == 0))
                  and (expect_rc == 0 or "differ" in r.stdout + r.stderr)
                  and (w / "src/madeira_d3d12_stubs.h").read_text() == "stubs-A\n"
                  and (w / "out/madeira_d3d12_stubs.h").read_text() == gen_out)

        # ---- 2 + 3. the kernels
        ker = seg(s, KER_A, KER_B[script])
        for case, extra_env, rc, want in (
            ("all compile", {}, 0, "flags="),
            ("only without fi-nan-clean", {"FAKE_NEED": "-DMAD_NO_NAN_CLEAN"}, 0, "-DMAD_NO_NAN_CLEAN"),
            ("only without the probe and clear-rects", {"FAKE_NEED": "-DMAD_NO_TEXBUF_PROBE"}, 0, "-DMAD_NO_TEXBUF_PROBE"),
            ("nothing compiles, switch unset", {"FAKE_FAIL_ALL": "1"}, 0, "NO-KERNELS"),
            ("nothing compiles, MADEIRA_REQUIRE_KERNELS=0", {"FAKE_FAIL_ALL": "1", "MADEIRA_REQUIRE_KERNELS": "0"}, 0, "NO-KERNELS"),
            ("nothing compiles, MADEIRA_REQUIRE_KERNELS=1", {"FAKE_FAIL_ALL": "1", "MADEIRA_REQUIRE_KERNELS": "1"}, 1, None),
        ):
            w = t / f"ker-{script.stem}-{len(list(t.iterdir()))}"
            (w / "src").mkdir(parents=True)
            (w / "out").mkdir()
            (w / "src/mad_kernels.metal").write_text("// fake\n")
            log = w / "xcrun.log"
            log.write_text("")
            env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", SRC=str(w / "src"), OUT=str(w / "out"), R=str(w),
                       REPO_ROOT=str(w), FAKE_LOG=str(log))
            env.pop("MADEIRA_REQUIRE_KERNELS", None)
            env.update(extra_env)
            prog = ('set -eu\nEXTRA=()\nKDEF=""\n' + ker +
                    '\nif [ "${KDEF:-}${EXTRA[*]:-}" = "" ]; then echo NO-KERNELS; fi\n'
                    'echo "KDEF=${KDEF:-} EXTRA=${EXTRA[*]:-}"\n')
            r = subprocess.run(["bash", "-c", prog], env=env, capture_output=True, text=True, cwd=w)
            first = [l for l in log.read_text().splitlines() if "--target=air64-apple-ios18.0" not in l]
            ok = r.returncode == rc
            if rc == 0 and want == "flags=":
                ok &= "MAD_HAVE_KERNELS" in r.stdout and len(first) == 1 and "-DMAD_NO" not in first[0]
            elif rc == 0 and want == "NO-KERNELS":
                ok &= "NO-KERNELS" in r.stdout and "MAD_HAVE_KERNELS" not in r.stdout
            elif rc == 0:
                ok &= "MAD_HAVE_KERNELS" in r.stdout and want in first[-1]
                # the fallbacks are tried in order, the one flag set that works is the one built with
                ok &= len(first) >= 2
            else:
                ok &= "MAD_HAVE_KERNELS" not in r.stdout
            check(f"{name}: kernels, {case} -> exit {rc}", ok)
            if not ok:
                print(r.stdout[-800:], r.stderr[-800:], "\n".join(first[-6:]))

    # ---- the fallbacks of the developer script are the CI script's
    s_dev = dev.read_text()
    check("build-pe.sh: the fi-nan-clean fallbacks are in (as tools/build-madeira-d3d12-dll.sh has them)",
          "mad_kernels_metal -DMAD_NO_NAN_CLEAN ||" in s_dev and "-DMAD_NO_TEXBUF_PROBE -DMAD_NO_CLEAR_RECTS -DMAD_NO_NAN_CLEAN" in s_dev)

# ---- the committed header equals the pinned toolchain's
inc = None
for base in (root / "toolchains", Path.home() / "madeira-bcd/toolchains"):
    p = base / "llvm-mingw-20260421-ucrt-macos-universal/generic-w64-mingw32/include/d3d12.h"
    if p.is_file():
        inc = p
        break
if inc:
    with tempfile.TemporaryDirectory() as t2:
        out = Path(t2) / "stubs.h"
        r = subprocess.run([sys.executable, str(root / "madeira-d3d12/src/pe/gen_vtables.py"), str(inc), str(out)], capture_output=True, text=True)
        check("the committed madeira_d3d12_stubs.h is what the pinned toolchain's d3d12.h generates",
              r.returncode == 0 and out.read_bytes() == (root / "madeira-d3d12/src/pe/madeira_d3d12_stubs.h").read_bytes())
else:
    print("note: the pinned llvm-mingw is not next to the sources; the stubs equality is checked where it is (CI does it in the build)")

check("CI runs this check before the DLL build", "tests/host/check-d3d12-build-safety.py" in wf
      and wf.index("run: python3 tests/host/check-d3d12-build-safety.py") < wf.index("run: bash tools/build-madeira-d3d12-dll.sh"))
check("the workflow does not set MADEIRA_REQUIRE_KERNELS (opt-in: the lead may add it to the build step's env)",
      "MADEIRA_REQUIRE_KERNELS:" not in wf and "MADEIRA_REQUIRE_KERNELS=1 " not in wf.replace("MADEIRA_REQUIRE_KERNELS=1 fails", ""))

print("FAILED: D3D12 build safety" if failures else "PASS: D3D12 build safety")
sys.exit(1 if failures else 0)
