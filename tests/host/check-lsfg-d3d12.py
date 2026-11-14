#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Lossless Scaling frame generation for Direct3D 12 games: the bridge in d3d11-src.dll (tools/patch-d3d11-src-lsfg-bridge.py) and
its caller in madeira_d3d12.c (mad_lsfg_before, mad_lsfg_show). No file of Lossless Scaling, no Metal device, no Wine, no iPhone.

1. The bridge script applies to the copy tools/build-d3d11-dll.sh makes (after tools/patch-dxmt-lsfg.py), is idempotent, refuses a tree
   without d3d11_lsfg.hpp, and is NOT one of the workflow's DXMT patch steps: the dxmt submodule and the 32-bit d3d11.dll never get it.
2. tools/build-d3d11-dll.sh applies it, compiles the bridge into the patched DLL only, links that DLL with the copy's d3d11.def, and
   checks the exports: upstream's, plus exactly the bridge's three when it applied.
3. The patched sources compile for arm64ec when the llvm-mingw toolchain is there.
4. The two sides agree on the frame structure: struct DXMTLsfgFrame (C++, the bridge) and struct lsfg_bridge_frame (C, the Direct3D 12
   runtime) are compiled on the host and compared field by field.
5. The Direct3D 12 side, cut out of madeira_d3d12.c and run on the host with stand-ins for the Metal calls: without MADEIRA_LSFG_CTL
   the first Present reads the variable and nothing else ever happens; a d3d11.dll without the bridge switches it off with one line;
   a frame the bridge takes is copied, signalled and committed before the passes run; each generated picture goes out from a command
   buffer of its own, after the passes' event, the last one signals "done"; passes that are late are not waited for; no drawable and
   no command buffer never leave the bridge waiting; MetalFX in the swap chain enlarges generated pictures too; one swap chain is
   followed, and the next one after it is released.
SKIP (exit 0) without the dxmt sources.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import winemetal_fx_kit as kit   # noqa: E402

if not kit.have_sources():
    print(f"SKIP: no dxmt sources at {kit.SRC} (check out the submodule or set DXMT_SRC_DIR)")
    sys.exit(0)
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
    return cond


SCRIPT = "tools/patch-d3d11-src-lsfg-bridge.py"
mod = kit.load(SCRIPT)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
sh = (R / "tools/build-d3d11-dll.sh").read_text()
SRC = kit.SRC


def run(script, cwd, *args):
    return subprocess.run([sys.executable, str(R / script), *args], cwd=cwd, capture_output=True, text=True)


# ---- 1. the script
check("the bridge script is not a workflow DXMT patch step (the submodule and the 32-bit d3d11.dll never get it), and this check runs",
      SCRIPT not in kit.chain() and "patch-d3d11-src-lsfg-bridge.py" not in wf and "run: python3 tests/host/check-lsfg-d3d12.py" in wf)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    root = t / "copy"
    for part in ("d3d11", "d3d10", "dxmt"):
        shutil.copytree(SRC / part, root / "dxmt/src" / part)
    d3d11 = root / "dxmt/src/d3d11"
    came_patched = (d3d11 / "d3d11_lsfg_bridge.cpp").exists()
    if not (d3d11 / "d3d11_lsfg.hpp").exists():
        p0 = run(SCRIPT, root, str(d3d11))
        check("a tree without d3d11_lsfg.hpp is refused, and left as it was",
              p0.returncode == 1 and "tools/patch-dxmt-lsfg.py has to run on this tree first" in p0.stdout
              and not (d3d11 / "d3d11_lsfg_bridge.cpp").exists() and "DXMTLsfgBegin" not in (d3d11 / "d3d11.def").read_text())
        run("tools/patch-dxmt-context-state-swap.py", root, str(d3d11))
        listed = re.findall(r'"(patch-dxmt-[a-z0-9-]+\.py)" "', sh)
        problems = [s for s in listed if run("tools/" + s, root).returncode]
        check("the copy is brought to the state build-d3d11-dll.sh has it in before the bridge (its BCD_PATCHES)", not problems and (d3d11 / "d3d11_lsfg.hpp").exists())
    before = {n: (d3d11 / n).read_text() for n in ("d3d11_fence.cpp", "d3d11_fence.hpp", "d3d11.def")}
    p1 = run(SCRIPT, root, str(d3d11))
    after = {n: (d3d11 / n).read_text() for n in before}
    p2 = run(SCRIPT, root, str(d3d11))
    check("the script applies and a second run changes nothing",
          p1.returncode == 0 and p2.returncode == 0 and "already patched" in p2.stdout and after == {n: (d3d11 / n).read_text() for n in before}
          and (d3d11 / "d3d11_lsfg_bridge.cpp").read_text() == mod.BRIDGE)
    exact = True
    for name, _, anchor, new, _ in mod.FILES:
        exact &= after[name].count(new) == 1 and (came_patched or after[name].replace(new, anchor, 1) == before[name])
    check("the three files are changed by the script's hunks and nothing else", exact)
    check("d3d11.def exports the three functions after upstream's four",
          after["d3d11.def"].rstrip().endswith("D3D11On12CreateDevice @24\n    DXMTLsfgBegin\n    DXMTLsfgRun\n    DXMTLsfgAbort"))

    # ---- 3. compiles for arm64ec
    mingw = next((p for p in (Path(os.environ.get("MINGW", "/nonexistent")), R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                              Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                  if (p / "arm64ec-w64-mingw32-clang++").exists()), None)
    if not mingw or not (R / "dxmt/include").is_dir():
        print("SKIP: no llvm-mingw toolchain (MINGW, toolchains/ or the build clone's) for the arm64ec compile part")
    else:
        d = R / "dxmt"
        (t / "gen").mkdir()
        (t / "gen/version.h").write_text('#define DXMT_VERSION "check"\n')
        for tu in ("d3d11_lsfg_bridge.cpp", "d3d11_fence.cpp"):
            c = subprocess.run([str(mingw / "arm64ec-w64-mingw32-clang++"), "-std=c++20", "-O3", "-DNDEBUG", "-Wimplicit-fallthrough",
                                "-Wno-missing-field-initializers", "-Wno-unused-parameter", "-Wno-cast-function-type", "-Wno-unused-private-field",
                                "-Wno-microsoft-exception-spec", "-Wno-extern-c-compat", "-Wno-unused-const-variable", "-Wno-missing-braces", "-fblocks",
                                "-DNOMINMAX", "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1", "-DDXMT_PAGE_SIZE=4096",
                                f"-I{d3d11}", f"-I{d}/src/dxgi", f"-I{root}/dxmt/src/dxmt", f"-I{d}/src/airconv", f"-I{d}/src/winemetal", f"-I{d}/src/util",
                                f"-I{d}/include", f"-I{d}/libs", f"-I{t}/gen", "-fsyntax-only", str(d3d11 / tu)], capture_output=True, text=True)
            if not check(f"{tu} compiles for arm64ec (C++20, the flags of tools/build-d3d11-dll.sh), without a warning",
                         c.returncode == 0 and "warning:" not in c.stderr):
                print("\n".join(l for l in c.stderr.splitlines() if "error" in l or "warning" in l)[:3000])

# ---- 2. the build recipe
check("build-d3d11-dll.sh applies the bridge to its copy after the BCD patches, and goes on without it when it does not apply",
      sh.index('(cd "$OUT/patchroot" && python3 "$R/tools/${BCD_PATCHES[$i]}") || fail') < sh.index('python3 "$R/tools/patch-d3d11-src-lsfg-bridge.py" "$OUT/tree/src/d3d11" || {')
      and 'LSFG_BRIDGE=0\n    PATCHED_DEF="$D/src/d3d11/d3d11.def"\n    rm -f "$OUT/tree/src/d3d11/d3d11_lsfg_bridge.cpp"' in sh)
check("the bridge is compiled into the patched DLL only, which is linked with the copy's d3d11.def; the unpatched DLL keeps the submodule's",
      'job "$OUT/tree" "$OUT/tree/src/d3d11/d3d11_lsfg_bridge.cpp" "$o" d3d11; PATCHED_OBJ+=("$o")' in sh
      and 'link_dll "$OUT/plain/d3d11.dll" "$OUT/plain/libdxmt.a" "$D/src/d3d11/d3d11.def" "${PLAIN_OBJ[@]}"' in sh
      and 'link_dll "$OUT/d3d11.dll" "$OUT/libdxmt.a" "$PATCHED_DEF" "${PATCHED_OBJ[@]}"' in sh
      and '"$CXX" -shared -o "$out" "$@" "$OUT/obj/version.o" "$def" \\' in sh and "d3d11_lsfg_bridge" not in sh.split("D3D11_SRC=(")[1].split(")")[0])
check("the exports must still be upstream's, plus exactly the bridge's three with the bridge and none of them without",
      "grep -vxE 'DXMTLsfg(Begin|Run|Abort)' \"$OUT/exports.built.all\" > \"$OUT/exports.built\"" in sh
      and '[ "$BRIDGE_EXPORTS" = 3 ] || fail' in sh and '[ "$BRIDGE_EXPORTS" = 0 ] || fail' in sh
      and 'diff "$OUT/exports.upstream" "$OUT/exports.built" > "$OUT/exports.diff"' in sh
      and 'LC_ALL=C grep -aqF "[lsfg] (Direct3D 12 bridge)" "$OUT/d3d11.dll" || fail' in sh and "[lsfg] (Direct3D 12 bridge)" in mod.BRIDGE)

# ---- 4 and 5. the Direct3D 12 runtime
d3d12 = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
START = "/* madeira-bcd: Lossless Scaling frame generation for Direct3D 12 games (Porthole port, item 1)."
block = d3d12[d3d12.index(START):d3d12.index("static ULONG STDMETHODCALLTYPE swap_Release(IDXGISwapChain4 *T) {")]
run_fn = kit.body(d3d12, "static void mad_present_run(struct mad_swapchain *s, UINT idx) {")
check("Present calls it in one place, before the game's own drawable is taken",
      run_fn.count("mad_lsfg_before(") == 1 and run_fn.count("mad_lsfg_show(") == 1
      and run_fn.index("UINT ngen = mad_lsfg_before(s, src, &lf);") < run_fn.index("drawable = MetalLayer_nextDrawable(s->layer);")
      and d3d12.count("mad_lsfg_before(") == 2 and d3d12.count("mad_lsfg_show(") == 2)
check("without the app's variable the first call switches it off for the process, before anything is loaded or made",
      block.index("if (g_lsfg.state < 0) return 0;") < block.index('GetEnvironmentVariableA("MADEIRA_LSFG_CTL"') < block.index('LoadLibraryA("d3d11.dll")')
      and '{ g_lsfg.state = -1; return 0; }' in block)
check("a released swap chain is let go (pointer compare only)",
      "if (g_lsfg.sc == (const void *)s) g_lsfg.sc = NULL;" in kit.body(d3d12, "static ULONG STDMETHODCALLTYPE swap_Release(IDXGISwapChain4 *T) {")
      and "const void *sc;" in block and "g_lsfg.sc->" not in d3d12)
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cc or not cxx:
    print("SKIP: no C / C++ compiler for the behaviour part")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        cstruct = block[block.index("struct lsfg_bridge_frame {"):block.index("};", block.index("struct lsfg_bridge_frame {")) + 2]
        cppstruct = mod.BRIDGE[mod.BRIDGE.index("struct DXMTLsfgFrame {"):mod.BRIDGE.index("};", mod.BRIDGE.index("struct DXMTLsfgFrame {")) + 2]
        fields = ["size", "width", "height", "dxgi_format", "input_texture", "input_event", "input_value", "count", "reserved",
                  "out_texture", "out_event", "out_value", "done_event", "done_value"]
        dump = lambda ty: "".join(f'  printf("{f} %zu %zu\\n", offsetof({ty}, {f}), sizeof((({ty} *)0)->{f}));\n' for f in fields)
        (t / "a.c").write_text("#include <stdio.h>\n#include <stddef.h>\n#include <stdint.h>\ntypedef uint32_t UINT; typedef uint64_t UINT64;\n" + cstruct
                               + "\nint main(void) {\n" + dump("struct lsfg_bridge_frame") + '  printf("total %zu\\n", sizeof(struct lsfg_bridge_frame));\n  return 0;\n}\n')
        (t / "b.cpp").write_text("#include <cstdio>\n#include <cstddef>\n#include <cstdint>\n" + cppstruct
                                 + "\nint main() {\n" + dump("DXMTLsfgFrame") + '  printf("total %zu\\n", sizeof(DXMTLsfgFrame));\n  return 0;\n}\n')
        a = subprocess.run([cc, "-std=gnu11", "-o", str(t / "a"), str(t / "a.c")], capture_output=True, text=True)
        b = subprocess.run([cxx, "-std=c++17", "-Wno-invalid-offsetof", "-o", str(t / "b"), str(t / "b.cpp")], capture_output=True, text=True)
        if check("both structures compile on the host", a.returncode == 0 and b.returncode == 0):
            oa = subprocess.run([str(t / "a")], capture_output=True, text=True).stdout
            ob = subprocess.run([str(t / "b")], capture_output=True, text=True).stdout
            check("struct lsfg_bridge_frame (madeira_d3d12.c) and struct DXMTLsfgFrame (the bridge) have the same fields at the same places (%s bytes)"
                  % (oa.strip().splitlines()[-1].split()[-1] if oa.strip() else "?"), oa == ob and len(oa.splitlines()) == len(fields) + 1)
        else:
            print((a.stderr + b.stderr)[-1500:])

        harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
typedef uint32_t UINT; typedef uint64_t UINT64; typedef long LONG; typedef void *HMODULE; typedef uint64_t obj_handle_t; typedef unsigned long DWORD;
enum { WMTBlitCommandCopyFromTextureToTexture = 7 };
struct wmtcmd_base { int type; };
struct wmtcmd_blit_copy_from_texture_to_texture { int type; obj_handle_t src; struct { UINT64 width, height, depth; } src_size; obj_handle_t dst; };
struct mad_device { obj_handle_t mtl_queue, enc_fence; };
struct mad_queue { struct mad_device *device; };
struct mad_resource { obj_handle_t texture; UINT width, height; };
struct mad_swapchain { struct mad_queue *queue; struct { UINT Format; } desc; obj_handle_t fx_scaler, fx_out, layer; UINT fx_w, fx_h; };
static char g_ops[16384], g_log[8192];
static void op(const char *fmt, ...) { va_list ap; size_t n = strlen(g_ops); va_start(ap, fmt); vsnprintf(g_ops + n, sizeof g_ops - n, fmt, ap); va_end(ap); strcat(g_ops, " | "); }
static void d3d12_log(const char *fmt, ...) { va_list ap; size_t n = strlen(g_log); va_start(ap, fmt); vsnprintf(g_log + n, sizeof g_log - n, fmt, ap); va_end(ap); }
static LONG InterlockedIncrement(LONG *v) { return ++*v; }
static unsigned g_enc_seq; static int g_f6_used; enum { F6_RENDER, F6_BLIT, F6_COMPUTE };
static void f6_encode(obj_handle_t enc, int kind, obj_handle_t *f, int n, obj_handle_t update) { (void)kind; (void)f; (void)n; (void)update; op("cb%llu: wait for the frame's fence", (unsigned long long)(enc >> 8)); }
/* the environment and the bridge */
static const char *g_env_ctl; static int g_have_dll = 1, g_have_bridge = 1, g_env_reads, g_loads;
static DWORD GetEnvironmentVariableA(const char *name, char *buf, DWORD n) { (void)buf; (void)n; g_env_reads++; return (!strcmp(name, "MADEIRA_LSFG_CTL") && g_env_ctl) ? (DWORD)strlen(g_env_ctl) : 0; }
static HMODULE LoadLibraryA(const char *name) { g_loads++; return (g_have_dll && !strcmp(name, "d3d11.dll")) ? (HMODULE)0x11 : NULL; }
struct lsfg_bridge_frame;
static int g_begin_answer = 1, g_run_answer = 1, g_begins, g_runs, g_aborts;
static int fake_begin(struct lsfg_bridge_frame *f);
static int fake_run(struct lsfg_bridge_frame *f);
static void fake_abort(struct lsfg_bridge_frame *f);
static void *GetProcAddress(HMODULE m, const char *name) {
  (void)m;
  if (!g_have_bridge) return NULL;
  return !strcmp(name, "DXMTLsfgBegin") ? (void *)fake_begin : !strcmp(name, "DXMTLsfgRun") ? (void *)fake_run : !strcmp(name, "DXMTLsfgAbort") ? (void *)fake_abort : NULL;
}
/* Metal */
static obj_handle_t g_next_cb = 1; static int g_no_cb, g_no_drawable_after = 99, g_drawables, g_passes_done = 1;
static UINT64 g_event[8];
static obj_handle_t MTLCommandQueue_commandBuffer(obj_handle_t q) { (void)q; return g_no_cb ? 0 : g_next_cb++; }
static obj_handle_t MTLCommandBuffer_blitCommandEncoder(obj_handle_t cb) { return cb << 8; }
static void MTLBlitCommandEncoder_encodeCommands(obj_handle_t enc, const struct wmtcmd_base *c) {
  const struct wmtcmd_blit_copy_from_texture_to_texture *t = (const void *)c;
  op("cb%llu: copy %llu -> %llu (%llux%llu)", (unsigned long long)(enc >> 8), (unsigned long long)t->src, (unsigned long long)t->dst, (unsigned long long)t->src_size.width, (unsigned long long)t->src_size.height);
}
static void MTLCommandEncoder_endEncoding(obj_handle_t enc) { (void)enc; }
static void MTLCommandBuffer_encodeSignalEvent(obj_handle_t cb, obj_handle_t ev, UINT64 v) { op("cb%llu: signal event %llu = %llu", (unsigned long long)cb, (unsigned long long)ev, (unsigned long long)v); }
static void MTLCommandBuffer_encodeWaitForEvent(obj_handle_t cb, obj_handle_t ev, UINT64 v) { op("cb%llu: wait event %llu = %llu", (unsigned long long)cb, (unsigned long long)ev, (unsigned long long)v); }
static void MTLCommandBuffer_encodeSpatialScale(obj_handle_t cb, obj_handle_t sc, obj_handle_t in, obj_handle_t out, obj_handle_t f) { (void)sc; (void)f; op("cb%llu: MetalFX %llu -> %llu", (unsigned long long)cb, (unsigned long long)in, (unsigned long long)out); }
static void MTLCommandBuffer_presentDrawable(obj_handle_t cb, obj_handle_t d) { op("cb%llu: present drawable %llu", (unsigned long long)cb, (unsigned long long)d); }
static void MTLCommandBuffer_commit(obj_handle_t cb) { op("cb%llu: commit", (unsigned long long)cb); }
static int MTLSharedEvent_waitUntilSignaledValue(obj_handle_t ev, UINT64 v, UINT ms) { op("CPU waits up to %u ms for event %llu = %llu", ms, (unsigned long long)ev, (unsigned long long)v); return g_passes_done; }
static UINT64 MTLSharedEvent_signaledValue(obj_handle_t ev) { return g_event[ev & 7]; }
static void MTLSharedEvent_signalValue(obj_handle_t ev, UINT64 v) { g_event[ev & 7] = v; op("CPU signals event %llu = %llu", (unsigned long long)ev, (unsigned long long)v); }
static obj_handle_t MetalLayer_nextDrawable(obj_handle_t layer) { (void)layer; if (g_drawables >= g_no_drawable_after) { op("no drawable"); return 0; } return 900 + ++g_drawables; }
static obj_handle_t MetalDrawable_texture(obj_handle_t d) { return d + 1000; }
''' + block + r'''
static int fake_begin(struct lsfg_bridge_frame *f) {
  g_begins++;
  if (!g_begin_answer) return 0;
  f->input_texture = 50; f->input_event = 1; f->input_value = (UINT64)g_begins;
  op("bridge: begin %ux%u format %u", f->width, f->height, f->dxgi_format);
  return 1;
}
static int fake_run(struct lsfg_bridge_frame *f) {
  int i;
  g_runs++;
  op("bridge: run");
  for (i = 0; i < g_run_answer; i++) f->out_texture[i] = 60 + (UINT64)i;
  f->out_event = 2; f->out_value = (UINT64)g_runs; f->done_event = 3; f->done_value = (UINT64)g_runs; f->count = (UINT)g_run_answer;
  return g_run_answer;
}
static void fake_abort(struct lsfg_bridge_frame *f) { (void)f; g_aborts++; op("bridge: abort"); }
static int fails;
static void expect(int c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) { fails++; printf("     ops: %s\n     log: %s\n", g_ops, g_log); } }
static void reset(void) { g_ops[0] = 0; g_log[0] = 0; g_next_cb = 1; g_drawables = 0; }
static struct mad_device dev = { 5, 0 };
static struct mad_queue queue = { &dev };
static struct mad_swapchain sc = { &queue, { 87 }, 0, 0, 77, 0, 0 }, other = { &queue, { 87 }, 0, 0, 78, 0, 0 };
static struct mad_resource back = { 40, 1920, 1080 }, tiny = { 41, 320, 200 };
/* what mad_present_run does with the two functions */
static UINT present(struct mad_swapchain *s, struct mad_resource *src) {
  struct lsfg_bridge_frame lf;
  UINT ngen = mad_lsfg_before(s, src, &lf);
  if (ngen) mad_lsfg_show(s, &lf, ngen, src->width, src->height);
  return ngen;
}
int main(int argc, char **argv) {
  int phase = argc > 1 ? atoi(argv[1]) : 0, i;
  if (phase == 0) {   /* the app did not set the variable */
    for (i = 0; i < 1000; i++) present(&sc, &back);
    expect(g_lsfg.state == -1 && g_env_reads == 1 && g_loads == 0 && g_begins == 0 && !g_ops[0] && !g_log[0],
           "without MADEIRA_LSFG_CTL: the first Present reads the variable, then 999 more do nothing at all: nothing loaded, no call, no line");
    return fails ? 1 : 0;
  }
  g_env_ctl = "C:\\ProgramData\\BCD\\lsfg.ctl";
  if (phase == 1) {   /* the session's d3d11.dll is upstream's: no bridge */
    g_have_bridge = 0;
    for (i = 0; i < 100; i++) present(&sc, &back);
    expect(g_lsfg.state == -1 && g_loads == 1 && !g_ops[0] && strstr(g_log, "d3d11.dll has no bridge (functions missing") && strstr(g_log, "\n") == g_log + strlen(g_log) - 1,
           "a d3d11.dll without the bridge: looked up once, one line says so, nothing more ever happens");
    return fails ? 1 : 0;
  }
  /* ---- the bridge is there */
  expect(present(&sc, &tiny) == 0 && g_begins == 0 && g_lsfg.sc == NULL, "a 320x200 swap chain (a probe or helper window) is not followed");
  g_begin_answer = 0; reset();
  expect(present(&sc, &back) == 0 && g_begins == 1 && !g_ops[0] && g_lsfg.sc == (const void *)&sc && strstr(g_log, "follows swap chain"),
         "the first large swap chain is followed; the bridge says no (game fast enough, switched off): no command buffer, nothing presented");
  expect(present(&other, &back) == 0 && g_begins == 1, "another swap chain is not asked about at all");
  g_begin_answer = 1; g_run_answer = 2; reset();
  expect(present(&sc, &back) == 2 && !strcmp(g_ops,
         "bridge: begin 1920x1080 format 87 | cb1: copy 40 -> 50 (1920x1080) | cb1: signal event 1 = 2 | cb1: commit | bridge: run | CPU waits up to 40 ms for event 2 = 1 | "
         "cb2: wait event 2 = 1 | cb2: copy 60 -> 1901 (1920x1080) | cb2: present drawable 901 | cb2: commit | "
         "cb3: wait event 2 = 1 | cb3: copy 61 -> 1902 (1920x1080) | cb3: signal event 3 = 1 | cb3: present drawable 902 | cb3: commit | "),
         "two generated pictures: the game's picture is copied, signalled and committed before the passes run; each generated picture goes out from a buffer of its own after the passes' event; the last signals done");
  g_run_answer = 1; g_passes_done = 0; reset();
  expect(present(&sc, &back) == 1 && strstr(g_ops, "CPU waits up to 40 ms") && strstr(g_ops, "CPU signals event 3 = 2") && !strstr(g_ops, "present drawable")
         && strstr(g_log, "the passes were not done after 40 ms"),
         "passes that are late: the frame goes without extra pictures, nothing waits on the GPU for them, and the bridge is told it may go on");
  g_passes_done = 1; g_run_answer = 3; g_no_drawable_after = 1; reset();
  expect(present(&sc, &back) == 3 && strstr(g_ops, "cb2: present drawable 901") && strstr(g_ops, "no drawable") && !strstr(g_ops, "cb3: present")
         && strstr(g_ops, "CPU signals event 3 = 3"), "the layer gives one drawable of three: one picture is shown and the bridge is not left waiting for the rest");
  g_no_drawable_after = 99; g_no_cb = 1; reset();
  expect(present(&sc, &back) == 0 && g_aborts == 1 && strstr(g_ops, "bridge: abort") && !strstr(g_ops, "bridge: run"), "no command buffer for the copy: the bridge is told, and no passes run");
  g_no_cb = 0; g_run_answer = 0; reset();
  expect(present(&sc, &back) == 0 && strstr(g_ops, "bridge: run") && !strstr(g_ops, "present"), "the passes made nothing (history not built yet, measure only): nothing extra is presented");
  g_run_answer = 1; g_f6_used = 1; dev.enc_fence = 9; reset();
  expect(present(&sc, &back) == 1 && strstr(g_ops, "cb1: wait for the frame's fence | cb1: copy 40 -> 50"), "with encoder fences in use the copy waits for the frame's batches first");
  g_f6_used = 0; dev.enc_fence = 0;
  sc.fx_scaler = 70; sc.fx_out = 71; sc.fx_w = 2880; sc.fx_h = 1620; reset();
  expect(present(&sc, &back) == 1 && strstr(g_ops, "cb2: wait event 2 = ") && strstr(g_ops, "cb2: MetalFX 60 -> 71 | cb2: copy 71 -> 1901 (2880x1620)"),
         "MetalFX upscaling in the swap chain: a generated picture is enlarged like the game's own before it is copied to the drawable");
  sc.fx_scaler = 0;
  /* the swap chain is released (what swap_Release does), a new one presents */
  if (g_lsfg.sc == (const void *)&sc) g_lsfg.sc = NULL;
  reset();
  expect(present(&other, &back) == 1 && g_lsfg.sc == (const void *)&other && present(&sc, &back) == 0, "after a release the next large swap chain is followed, and only that one");
  expect(g_env_reads == 1 && g_loads == 1, "the variable was read once and d3d11.dll looked up once for all of this");
  return fails ? 1 : 0;
}
'''
        (t / "h.c").write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-pointer-to-int-cast",
                            "-Wno-int-to-pointer-cast", "-Wno-incompatible-pointer-types", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
        if check("the Direct3D 12 side compiles on the host with stand-ins for the Metal calls", b.returncode == 0):
            for phase, what in ((0, "the variable is not set"), (1, "no bridge in d3d11.dll"), (2, "the bridge at work")):
                r = subprocess.run([str(t / "h"), str(phase)], capture_output=True, text=True, timeout=60)
                print(r.stdout.rstrip())
                check(f"harness phase {phase} ({what})", r.returncode == 0 and "ok  " in r.stdout)
        else:
            print("\n".join(l for l in b.stderr.splitlines() if "error" in l)[:3000])

swift = (R / "app/Madeira/BCDLossless.swift").read_text()
check("the game page's note names Direct3D 12 too", "For Direct3D 11 and 12 games." in swift)

print("FAILED: Lossless Scaling frame generation, Direct3D 12" if failures else "PASS: Lossless Scaling frame generation, Direct3D 12")
sys.exit(1 if failures else 0)
