#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT IDXGIOutput display mode lists with any bits per pixel (tools/patch-dxgi-modes-any-bpp.py, opt-in: env.DXMT_MODE_LIST_ANY_BPP = 1;
night round 2026-11-11, M14 = A1 S6 step 2, Crash Bandicoot N. Sane Trilogy).

The game takes the last element of an empty vector of display modes; DXMT's GetDisplayModeList1 lists no mode for a format whose bits per pixel
(GetMonitorFormatBpp: 64 for R16G16B16A16_FLOAT) differ from the monitor's modes' (BCD's list is 32 bpp). Checks, on a copy of
dxmt/src/dxgi/dxgi_output.cpp (or $DXMT_SRC/src/dxgi/dxgi_output.cpp):
  - the patch applies, a second run changes nothing, a moved anchor is refused with exit 1 and the file is left as it was;
  - the patched filter runs as plain C++ against a monitor of 32 bpp modes: without the key (unset or 0) an FP16 request lists nothing, as
    before; with it every mode is listed for FP16 and the first four are logged; a 32-bit format is the same either way and logs nothing;
  - the switch is read once with env::getEnvVar, and tools/build-dxgi-dll.sh patches a copy of dxgi_output.cpp, links the plain build with
    the unpatched object, warns and goes on when the patch does not apply, and fails when the patched DLL lacks its log string.
With an arm64ec llvm-mingw ($MINGW, or arm64ec-w64-mingw32-clang++ on PATH) it also compiles the patched file (-fsyntax-only).
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
dxmt = Path(os.environ.get("DXMT_SRC", root / "dxmt"))
if not (dxmt / "src/dxgi/dxgi_output.cpp").exists():
    alt = Path.home() / "madeira-bcd/dxmt"
    dxmt = alt if (alt / "src/dxgi/dxgi_output.cpp").exists() else dxmt
out_cpp = dxmt / "src/dxgi/dxgi_output.cpp"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1200]) if (detail and not cond) else ""))
    ok &= bool(cond)


def run(path):
    r = subprocess.run([sys.executable, str(root / "tools/patch-dxgi-modes-any-bpp.py"), str(path)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


sh = (root / "tools/build-dxgi-dll.sh").read_text()
check("tools/build-dxgi-dll.sh patches a copy of dxgi_output.cpp and a failed patch only warns (the build goes on without it)",
      'cp "$G/dxgi_output.cpp" "$OUT/src/dxgi_output.cpp"' in sh
      and 'python3 "$R/tools/patch-dxgi-modes-any-bpp.py" "$OUT/src/dxgi_output.cpp" || {\n    MODE_ANY_BPP=""' in sh
      and "::warning::tools/patch-dxgi-modes-any-bpp.py did not apply" in sh)
check("the plain build links the unpatched output object and the shipped one the patched copy",
      'compile "$G/dxgi_output.cpp" "$OUT/obj/dxgi_output_plain.o"' in sh and 'compile "$OUT/src/dxgi_output.cpp" "$OUT/obj/dxgi_output.o"' in sh
      and '"$OUT/obj/dxgi_adapter_plain.o" "$OUT/obj/dxgi_output_plain.o"' in sh and '"$OUT/obj/dxgi_adapter.o" "$OUT/obj/dxgi_output.o"' in sh)
check("the build fails when the patched DLL lacks the log string",
      '[ -n "$MODE_ANY_BPP" ]' in sh and 'madeira-bcd any-bpp #" "$OUT/dxgi.dll" || fail' in sh)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check before the dxgi-src.dll build", "python3 tests/host/check-dxgi-modes-any-bpp.py" in wf
      and wf.index("python3 tests/host/check-dxgi-modes-any-bpp.py") < wf.index("run: bash tools/build-dxgi-dll.sh"))

if not out_cpp.exists():
    print("note: %s not checked out (set DXMT_SRC); patch checks skipped" % out_cpp)
else:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        cpp = t / "dxgi_output.cpp"
        shutil.copy(out_cpp, cpp)
        original = cpp.read_text()
        rc, out = run(cpp)
        check("patch applies", rc == 0 and "mode lists with any bits per pixel" in out, out)
        src = cpp.read_text()
        rc, out = run(cpp)
        check("second run: idempotent, file unchanged", rc == 0 and "already patched" in out and cpp.read_text() == src)
        a = src.index("// Skip modes with incompatible formats")
        block = src[a:src.index("if (pDesc != nullptr) {", a)]
        check("the switch is read once with env::getEnvVar, off unless set to something other than 0",
              'env::getEnvVar("DXMT_MODE_LIST_ANY_BPP")' in block and "static const bool anyBpp = [] {" in block and '!v.empty() && v != "0"' in block)
        sl = src.splitlines()
        check("only the filter changes: the one `if` line that guarded it is the only original line gone (the patch adds the block around it)",
              [l for l in original.splitlines() if l not in sl] == ["      if (devMode.bitsPerPixel != GetMonitorFormatBpp(EnumFormat))"])
        check("without the key a mismatching mode still ends in `continue;` (nothing else leaves that block)",
              "if (!anyBpp)\n          continue;" in block)
        prog = r'''
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <sstream>
#include <string>
#include <vector>
enum DXGI_FORMAT : unsigned { DXGI_FORMAT_R8G8B8A8_UNORM = 28, DXGI_FORMAT_R10G10B10A2_UNORM = 24, DXGI_FORMAT_R16G16B16A16_FLOAT = 10 };
struct WsiMode { unsigned width, height, bitsPerPixel; bool interlaced = false; };
namespace env { std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? v : ""; } }
namespace str { template <typename... A> std::string format(const A &...a) { std::stringstream s; (s << ... << a); return s.str(); } }
struct Logger { static void warn(const std::string &m) { std::printf("WARN %s\n", m.c_str()); } };
static unsigned GetMonitorFormatBpp(DXGI_FORMAT f) { return f == DXGI_FORMAT_R16G16B16A16_FLOAT ? 64 : 32; }
static unsigned list(DXGI_FORMAT EnumFormat, const std::vector<WsiMode> &modes) {
  unsigned listed = 0;
  for (auto devMode : modes) {
      ''' + block.rstrip() + r'''
      listed++;
  }
  return listed;
}
int main() {
  std::vector<WsiMode> modes = {{640, 480, 32}, {800, 600, 32}, {1320, 2868, 32}, {1280, 720, 32}};
  std::printf("rgba=%u fp16=%u r10=%u\n", list(DXGI_FORMAT_R8G8B8A8_UNORM, modes), list(DXGI_FORMAT_R16G16B16A16_FLOAT, modes), list(DXGI_FORMAT_R10G10B10A2_UNORM, modes));
  std::vector<WsiMode> mixed = {{640, 480, 32}, {800, 600, 64}};
  std::printf("mixed: rgba=%u fp16=%u\n", list(DXGI_FORMAT_R8G8B8A8_UNORM, mixed), list(DXGI_FORMAT_R16G16B16A16_FLOAT, mixed));
  return 0;
}
'''
        cc = shutil.which("clang++") or shutil.which("c++")
        (t / "m.cpp").write_text(prog)
        b = subprocess.run([cc, "-std=c++20", "-w", "-o", str(t / "m"), str(t / "m.cpp")], capture_output=True, text=True) if cc else None
        if b is None or b.returncode != 0:
            check("the patched filter compiles on the host", False, b.stderr if b else "no C++ compiler")
        else:
            def go(val):
                env = {k: v for k, v in os.environ.items() if k != "DXMT_MODE_LIST_ANY_BPP"}
                if val is not None:
                    env["DXMT_MODE_LIST_ANY_BPP"] = val
                return subprocess.run([str(t / "m")], capture_output=True, text=True, env=env).stdout
            off, zero, on = go(None), go("0"), go("1")
            check("key unset or 0: an FP16 request lists nothing for a 32 bpp monitor (as before), 32-bit formats list every mode, nothing is logged",
                  "rgba=4 fp16=0 r10=4" in off and "mixed: rgba=1 fp16=1" in off and off == zero and "WARN" not in off, off)
            check("key on: the FP16 request lists every mode (the requested format's mode list stays complete), 32-bit formats are unchanged",
                  "rgba=4 fp16=4 r10=4" in on and "mixed: rgba=2 fp16=2" in on, on)
            check("key on: the first four admitted modes are logged with the format, the wanted bits, the mode and its bits",
                  on.count("WARN [dxgi-modes] madeira-bcd any-bpp #") == 4 and "#1: format 10 wants 64 bpp, the monitor's 640x480 mode has 32: listed" in on
                  and "#4: format 10 wants 64 bpp, the monitor's 1280x720 mode has 32: listed" in on, on)
        mingw = os.environ.get("MINGW") or str(Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
        cxx = shutil.which("arm64ec-w64-mingw32-clang++", path=mingw + os.pathsep + os.environ.get("PATH", ""))
        gdir = dxmt / "src/dxgi"
        if cxx and (dxmt / "include").is_dir():
            inc = ["-I%s" % gdir, "-I%s" % (dxmt / "src/dxmt"), "-I%s" % (dxmt / "src/util"), "-I%s" % (dxmt / "src/winemetal"),
                   "-I%s" % (dxmt / "src/airconv"), "-I%s" % (dxmt / "include"), "-I%s" % (dxmt / "libs")]
            r = subprocess.run([cxx, "-std=c++20", "-O1", "-DNDEBUG", "-fblocks", "-DNOMINMAX", "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1", "-DDXMT_PAGE_SIZE=4096", "-w"]
                               + inc + ["-fsyntax-only", str(cpp)], capture_output=True, text=True)
            check("the patched file compiles for arm64ec", r.returncode == 0, r.stderr[-1500:])
        else:
            print("skip: no arm64ec llvm-mingw (set MINGW); the patched file was not compiled")
        bad = t / "moved.cpp"
        bad.write_text(original.replace("      // Skip modes with incompatible formats\n", "      // Skip modes with incompatible pixel formats\n"))
        before = bad.read_text()
        rc, out = run(bad)
        check("moved anchor: exit 1, file untouched, the filter named", rc == 1 and bad.read_text() == before and "bits-per-pixel filter" in out, out)

print("PASS: DXGI any-bpp display mode lists" if ok else "FAILED: DXGI any-bpp display mode lists")
sys.exit(0 if ok else 1)
