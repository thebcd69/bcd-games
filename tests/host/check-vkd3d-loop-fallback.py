#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-vkd3d-loop-fallback.py: Wine's HLSL compiler writes a loop it cannot unroll as a bounded run of guarded copies, on request (round 707, S.T.A.L.K.E.R. Call of Pripyat 13CYSY).

S.T.A.L.K.E.R. Call of Pripyat compiles its R2 shader accum_volumetric_sun (ps_3_0, entry main) at run time; its loop runs `n` times where n comes
from a texture sample (at most 39), vkd3d-shader cannot unroll it, and the Shader Model 3 writer has no loop instruction: "E5017: Aborting due to
not yet implemented feature: Instruction type HLSL_IR_LOOP". The fixture, tests/fixtures/hlsl/loop-runtime-count.hlsl, is a shader written for this
check (no game code) with that shape: ps_3_0 main, n = (int)((P.z - 0.3) / (P.z / 40)) from a texture sample, four shadow taps (texldl) a pass. With env.MADEIRA_HLSL_LOOP_FALLBACK set (1 = 64 copies, 8..256 = that many; off
otherwise) unroll_loops() asks the unroller once more, with an explicit limit, for a loop below Shader Model 4 it gave up on. Checks:
  - CI applies the patch before the i386 farm is built and runs this check; the farm's cache key hashes tools/patch-wine-*.py;
  - with a Wine tree (wine/ of this tree, WINE_SRC or ~/madeira-bcd/wine; skipped without one): the patch applies once to a copy of
    hlsl_codegen.c, a second run changes nothing, a moved anchor refuses it and leaves the file alone; the key is read once, in one helper, and
    the retry runs only after the first try failed, for no explicit limit, no forced [unroll] and a target below Shader Model 4;
  - the compiler itself (the Mac: a C compiler, flex, and the parser files of the full tree's i386 build): vkd3d-shader is built on the host
    twice, with and without the patch, and a driver compiles that fixture (with the flags the game passes, from the log) and small shaders. Key unset, the shader fails with E5017 HLSL_IR_LOOP as before. With the key it compiles, the
    D3DBC has no rep / loop opcode, holds exactly N guarded copies, and the line is printed once a process. The copies are then EXECUTED by a small
    interpreter of vkd3d's disassembly, with chosen texture values, for counts below, at and above N: the fixture shader gives what the stock
    unroller gives for the same loop written with a constant bound and the count as a break test (and, at 39, what it gives with n forced to 39),
    and small for / while / continue / break loops give what the HLSL says, a count above N cut at N. Key values that are not 1 or a number from 8
    to 256 leave it off. A source that compiled before (a constant count, an explicit [unroll(N)], a Shader Model 4 target) is the same bytes
    with the key set, unset and without the patch; a forced [unroll] and a [loop] stay what they were; the key unset gives the same bytes as the
    tree without the patch for every source here;
  - with the llvm-mingw toolchain and the full tree's i386 build (the Mac): the patched file compiles for the i386 farm with the build's flags,
    with no error and no warning.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import importlib.util, math, os, re, shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def finish():
    print("PASS: a loop vkd3d cannot unroll can be written as guarded copies" if ok else "FAILED: vkd3d hlsl loop fallback")
    sys.exit(0 if ok else 1)


spec = importlib.util.spec_from_file_location("loopfallback", root / "tools/patch-wine-vkd3d-loop-fallback.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
STEP = "      - name: Patch wine vkd3d-shader to write a loop it cannot unroll as guarded copies (opt-in)\n"
step = wf.find(STEP)
check("CI runs this check and applies the patch before the i386 farm is built",
      step >= 0 and "python3 tests/host/check-vkd3d-loop-fallback.py\n          python3 tools/patch-wine-vkd3d-loop-fallback.py wine\n" in wf[step:step + 500]
      and step < wf.index("      - name: Build i386 farm (WoW64)\n"))
rev = wf.find("      - name: i386 farm revision\n")
check("the i386 farm's cache key hashes tools/patch-wine-*.py (a new Wine patch rebuilds the farm)",
      rev >= 0 and "hashFiles('build/wine-i386/build.sh', 'tools/patch-dxmt-*.py', 'tools/patch-wine-*.py')" in wf[rev:rev + 500])

FIXTURE = root / "tests/fixtures/hlsl/loop-runtime-count.hlsl"
fixture = FIXTURE.read_text() if FIXTURE.is_file() else ""
check("the fixture exists, written for BCD's tests (BCD's licence header, no game code)",
      FIXTURE.is_file() and fixture.startswith("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n// BCD Material: ")
      and "no game code" in fixture)
check("the fixture holds no path, name or address",
      not re.search(r"[A-Za-z]:\\|/Users/|/home/|/private/|@|https?:", fixture))
LOOP_HEAD = "    for (int i = 0; i < n; ++i)\n    {\n"
N_DEF = "int n = (int)((P.z - 0.3) / deltaDepth);\n"
LOOP_LINE = next((i + 1 for i, l in enumerate(fixture.splitlines()) if l == LOOP_HEAD.splitlines()[0]), 0)
check("the fixture's loop (the line E5017 names) has a count computed from a texture sample, the log's expression, and four shadow taps a pass",
      LOOP_LINE > 0 and fixture.count(N_DEF) == 1 and fixture.count(LOOP_HEAD) == 1 and "float3 direction = P / 40;" in fixture
      and "float3 P = tex2D(s_position, tc).xyz;" in fixture and fixture.count("tap(current, float2(") == 4 and "tex2Dlod(s_smap" in fixture
      and "float4 main(float2 tc : TEXCOORD0) : COLOR" in fixture)

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    finish()

src = (wine / P.FILE).read_text()
if P.MARKER in src:
    print("note: the Wine tree is already patched; only the patched file is checked (no comparison with the tree without the patch)")
    patched, stock = src, None
else:
    patched, stock = None, src
with tempfile.TemporaryDirectory(prefix="madeira-vkd3d-loopfb-") as tmp:
    tmp = Path(tmp)
    if patched is None:
        tree = tmp / "w"
        (tree / Path(P.FILE).parent).mkdir(parents=True)
        t = tree / P.FILE
        t.write_text(src)
        script = str(root / "tools/patch-wine-vkd3d-loop-fallback.py")
        r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        patched = t.read_text()
        r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == patched and P.MARKER in patched
              and patched.count(P.MARKER) == 3,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)

        def refused(what, moved, needle):
            t.write_text(moved)
            r = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
            check("a moved anchor refuses the patch and leaves the file alone: " + what,
                  r.returncode != 0 and needle in r.stderr and t.read_text() == moved and P.MARKER not in t.read_text(), r.stderr)

        refused("the unroller's signature", src.replace(P.UNROLL, P.UNROLL.replace("*loop,", "*the_loop,"), 1),
                "loop_unrolling_unroll_loop of hlsl_codegen.c not found once")
        refused("the explicit-limit exit", src.replace(P.QUIET, P.QUIET.replace("!unroll_limit && i == max_iterations", "!unroll_limit && max_iterations == i"), 1),
                "explicit-limit exit")
        refused("unroll_loops", src.replace(P.ANCHOR, P.ANCHOR.replace("void *context", "void *ctx_data"), 1), "unroll_loops of hlsl_codegen.c")
        refused("the call", src.replace(P.CALL, P.CALL.replace("unroll_limit))", "unroll_limit ))"), 1), "loop_unrolling_unroll_loop call")
        t.write_text(patched.replace(P.CALL_NEW, P.CALL, 1))
        r4 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("a half-applied file (helpers without the call) is refused by name", r4.returncode != 0 and "partial or changed" in r4.stderr, r4.stderr)

    # ------------------------------------------------------------------ what the patched source does
    def func(text, name):
        i = text.find(name)
        return text[i:text.index("\n}\n", i) + 3] if i >= 0 else ""

    def ordered(text, *needles):
        """True when every needle is in text, each after the one before."""
        at = 0
        for needle in needles:
            at = text.find(needle, at) + 1
            if not at:
                return False
        return True

    reader = func(patched, "static unsigned int madeira_hlsl_loop_fallback(void)")
    retry = func(patched, "static bool madeira_hlsl_unroll_fallback(")
    caller = func(patched, "static bool unroll_loops(")
    check("the key is read once, in one helper, and nowhere else",
          patched.count('getenv("MADEIRA_HLSL_LOOP_FALLBACK")') == 1 and reader.count('getenv("MADEIRA_HLSL_LOOP_FALLBACK")') == 1
          and "static int copies = -1;" in reader and patched.count("madeira_hlsl_loop_fallback()") == 1 and retry.count("madeira_hlsl_loop_fallback()") == 1)
    check("the retry goes out before it reads the key for an explicit limit, a forced [unroll] and Shader Model 4 and later",
          ordered(retry, "if (unroll_limit || loop->unroll_type == HLSL_LOOP_FORCE_UNROLL || hlsl_version_ge(ctx, 4, 0))\n        return false;",
                  "madeira_hlsl_loop_fallback()", "loop_unrolling_unroll_loop(ctx, program, loop, copies)"))
    check("the first try is the stock call (same limit), the retry only runs after it failed, the loop is marked FORCE_LOOP only when both failed, and the pass still reports progress",
          P.CALL_NEW in caller and ordered(caller, "loop_unrolling_unroll_loop(ctx, program, loop, unroll_limit)", "madeira_hlsl_unroll_fallback(",
                                           "loop->unroll_type = HLSL_LOOP_FORCE_LOOP;") and caller.rstrip().endswith("return true;\n}"))
    check("the line is written once a process, through the error channel the log carries, naming the number of copies",
          'ERR("[hlsl-loop-fallback] madeira-bcd: a loop is written as %u guarded copies\\n", copies);' in retry
          and "vkd3d_atomic_increment_u32(&reported) == 1" in retry)
    check("the explicit-limit exit the retry relies on is still in the tree's unroller",
          "    if (!unroll_limit && i == max_iterations)\n" in patched)

    flex = shutil.which("flex")
    cc = shutil.which("cc") or shutil.which("clang")
    gen_src = wine / "build-i386/libs/vkd3d/libs/vkd3d-shader"
    need = ["hlsl.tab.c", "hlsl.tab.h", "preproc.tab.c", "preproc.tab.h", "preproc.yy.c"]
    lex = (wine / "libs/vkd3d/libs/vkd3d-shader/hlsl.l").read_text()
    if not flex:
        print("skip: no flex here")
    elif not cc or not all((gen_src / n).is_file() for n in need):
        print("skip: the compiler checks need a C compiler and the parser files of the full tree's i386 build "
              "(wine/build-i386/libs/vkd3d/libs/vkd3d-shader)")
    else:
        (tmp / "o.l").write_text(lex)
        fo = subprocess.run([flex, "-o", str(tmp / "hlsl.yy.c"), str(tmp / "o.l")], capture_output=True, text=True)
        check("flex builds the scanner", fo.returncode == 0, fo.stderr)
        V = wine / "libs/vkd3d"
        inc = tmp / "inc"
        (inc / "wine").mkdir(parents=True)
        shutil.copy(wine / "include/wine/rbtree.h", inc / "wine")
        shutil.copy(wine / "include/wine/list.h", inc / "wine")
        (inc / "config.h").write_text((V / "config.h").read_text() + "\n#define HAVE_SYNC_ADD_AND_FETCH 1\n#define HAVE_SYNC_BOOL_COMPARE_AND_SWAP 1\n"
                                      "#define HAVE_BUILTIN_CLZ 1\n#define HAVE_BUILTIN_CTZ 1\n#define HAVE_BUILTIN_ADD_OVERFLOW 1\n"
                                      "#define HAVE_BUILTIN_POPCOUNT 1\n")
        (inc / "windows.h").write_text(r'''/* host stand-in for the Windows names vkd3d-shader uses outside its _WIN32 blocks (this check only) */
#ifndef HOST_STUB_WINDOWS_H
#define HOST_STUB_WINDOWS_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
typedef int32_t HRESULT; typedef int BOOL; typedef uint32_t DWORD, UINT, ULONG; typedef int32_t LONG, INT; typedef uint16_t WCHAR;
typedef unsigned char BYTE; typedef float FLOAT; typedef double DOUBLE; typedef uint64_t UINT64, ULONGLONG; typedef int64_t INT64, LONGLONG;
typedef size_t SIZE_T; typedef void *HANDLE;
typedef struct { uint32_t Data1; uint16_t Data2, Data3; uint8_t Data4[8]; } GUID; typedef GUID IID; typedef const IID *REFIID;
#ifndef max
#define max(a, b) (((a) > (b)) ? (a) : (b))
#endif
#ifndef min
#define min(a, b) (((a) < (b)) ? (a) : (b))
#endif
#define CONTAINING_RECORD(address, type, field) ((type *)((char *)(address) - offsetof(type, field)))
#define TRUE 1
#define FALSE 0
#define S_OK ((HRESULT)0)
#define S_FALSE ((HRESULT)1)
#define E_FAIL ((HRESULT)0x80004005)
#define E_NOTIMPL ((HRESULT)0x80004001)
#define E_NOINTERFACE ((HRESULT)0x80004002)
#define E_POINTER ((HRESULT)0x80004003)
#define E_ABORT ((HRESULT)0x80004004)
#define E_INVALIDARG ((HRESULT)0x80070057)
#define E_OUTOFMEMORY ((HRESULT)0x8007000e)
#define DXGI_ERROR_NOT_FOUND ((HRESULT)0x887a0002)
#define DXGI_ERROR_MORE_DATA ((HRESULT)0x887a0003)
#define DXGI_ERROR_UNSUPPORTED ((HRESULT)0x887a0004)
#define DXGI_ERROR_ALREADY_EXISTS ((HRESULT)0x887a0036)
#define FAILED(x) ((HRESULT)(x) < 0)
#define SUCCEEDED(x) ((HRESULT)(x) >= 0)
#endif
''')
        enum = re.compile(r'typedef\s+enum\s+\w*\s*\{.*?\}\s*[\w\s,\*]+;', re.S)
        dx = [e for e in enum.findall((wine / "include/d3dx9shader.h").read_text()) if "D3DXPARAMETER" in e or "D3DXREGISTER" in e]
        (inc / "d3dx9shader.h").write_text("#define D3DPS_VERSION(major, minor) (0xFFFF0000 | ((major) << 8) | (minor))\n"
                                          "#define D3DVS_VERSION(major, minor) (0xFFFE0000 | ((major) << 8) | (minor))\n" + "\n".join(dx) + "\n")
        dc = wine / "build-i386/include/d3dcommon.h"
        (inc / "d3dcommon.h").write_text("\n".join(enum.findall(dc.read_text())) + "\n" if dc.is_file() else "")

        # The driver compiles what D3DCompile2VKD3D compiles (its options for the flags a caller gives: debug, row-major packing), writes the last
        # blob of a run to a file and prints the compiler's text; `asm` prints a blob as text.
        harness = tmp / "harness.c"
        harness.write_text(r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include "vkd3d_shader_private.h"
static char *slurp(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb"); char *b; long len;
    if (!f) exit(9);
    fseek(f, 0, SEEK_END); len = ftell(f); fseek(f, 0, SEEK_SET);
    b = malloc(len + 1); if (fread(b, 1, len, f) != (size_t)len) exit(9); b[len] = 0; fclose(f); *n = len; return b;
}
static void print_messages(const char *messages)
{
    const char *p, *end;
    for (p = messages; p && *p; p = end ? end + 1 : p + strlen(p))
    {
        end = strchr(p, '\n');
        if (end ? end > p : *p)
            printf("msg| %.*s\n", (int)(end ? end - p : strlen(p)), p);
    }
}
int main(int argc, char **argv)
{
    const char *mode = argv[1], *profile = argv[3];
    int new_style = profile[3] >= '4', i, reps = 1;
    unsigned int flags = 0;
    size_t size;
    char *source = slurp(argv[2], &size);

    if (!strcmp(mode, "asm"))
    {
        struct vkd3d_shader_compile_info info = {0};
        struct vkd3d_shader_compile_option options[1];
        struct vkd3d_shader_code code = {0};
        char *messages = NULL;
        int ret;
        options[0].name = VKD3D_SHADER_COMPILE_OPTION_API_VERSION;
        options[0].value = VKD3D_SHADER_API_VERSION_CURRENT;
        info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
        info.source.code = source;
        info.source.size = size;
        info.source_type = new_style ? VKD3D_SHADER_SOURCE_DXBC_TPF : VKD3D_SHADER_SOURCE_D3D_BYTECODE;
        info.target_type = VKD3D_SHADER_TARGET_D3D_ASM;
        info.options = options;
        info.option_count = 1;
        info.log_level = VKD3D_SHADER_LOG_INFO;
        info.source_name = "blob";
        ret = vkd3d_shader_compile(&info, &code, &messages);
        if (ret >= 0)
            fwrite(code.code, 1, code.size, stdout);
        else
            print_messages(messages);
        vkd3d_shader_free_messages(messages);
        vkd3d_shader_free_shader_code(&code);
        return ret >= 0 ? 0 : 1;
    }
    flags = (unsigned int)strtoul(argv[4], NULL, 0);
    reps = atoi(argv[5]);
    for (i = 0; i < reps; ++i)
    {
        struct vkd3d_shader_preprocess_info preprocess_info = {0};
        struct vkd3d_shader_hlsl_source_info hlsl_info = {0};
        struct vkd3d_shader_compile_info compile_info = {0};
        struct vkd3d_shader_compile_option options[3];
        struct vkd3d_shader_code code = {0};
        char *messages = NULL;
        int ret;
        compile_info.option_count = 0;
        options[compile_info.option_count].name = VKD3D_SHADER_COMPILE_OPTION_API_VERSION;
        options[compile_info.option_count++].value = VKD3D_SHADER_API_VERSION_CURRENT;
        if (!(flags & 1))
        {
            options[compile_info.option_count].name = VKD3D_SHADER_COMPILE_OPTION_STRIP_DEBUG;
            options[compile_info.option_count++].value = 1;
        }
        if (flags & 8)
        {
            options[compile_info.option_count].name = VKD3D_SHADER_COMPILE_OPTION_PACK_MATRIX_ORDER;
            options[compile_info.option_count++].value = VKD3D_SHADER_COMPILE_OPTION_PACK_MATRIX_ROW_MAJOR;
        }
        compile_info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
        compile_info.next = &preprocess_info;
        compile_info.source.code = source;
        compile_info.source.size = size;
        compile_info.source_type = VKD3D_SHADER_SOURCE_HLSL;
        compile_info.target_type = new_style ? VKD3D_SHADER_TARGET_DXBC_TPF : VKD3D_SHADER_TARGET_D3D_BYTECODE;
        compile_info.options = options;
        compile_info.log_level = VKD3D_SHADER_LOG_INFO;
        compile_info.source_name = "<anonymous>";
        preprocess_info.type = VKD3D_SHADER_STRUCTURE_TYPE_PREPROCESS_INFO;
        preprocess_info.next = &hlsl_info;
        hlsl_info.type = VKD3D_SHADER_STRUCTURE_TYPE_HLSL_SOURCE_INFO;
        hlsl_info.profile = profile;
        hlsl_info.entry_point = "main";
        ret = vkd3d_shader_compile(&compile_info, &code, &messages);
        printf("compile %d ret=%d size=%zu\n", i, ret, ret >= 0 ? code.size : (size_t)0);
        print_messages(messages);
        if (ret >= 0 && i == reps - 1 && argc > 6)
        {
            FILE *f = fopen(argv[6], "wb");
            if (!f || fwrite(code.code, 1, code.size, f) != code.size) return 9;
            fclose(f);
        }
        vkd3d_shader_free_messages(messages);
        vkd3d_shader_free_shader_code(&code);
    }
    return 0;
}
""")
        cflags = ["-std=gnu99", "-O1", "-w", "-include", str(inc / "config.h"), "-I" + str(V / "include"), "-I" + str(V / "include/private"),
                  "-I" + str(V / "libs/vkd3d-shader"), "-I" + str(gen_src), "-I" + str(inc), "-DLIBVKD3D_SHADER_SOURCE"]
        shader_dir = V / "libs/vkd3d-shader"
        common = [s for s in sorted(shader_dir.glob("*.c")) if s.name != "hlsl_codegen.c"]
        common += [V / "libs/vkd3d-common" / n for n in ("debug.c", "error.c", "memory.c", "utf8.c")]
        common += [gen_src / "hlsl.tab.c", gen_src / "preproc.tab.c", gen_src / "preproc.yy.c", tmp / "hlsl.yy.c", harness]
        patched_c = tmp / "hlsl_codegen-patched.c"
        patched_c.write_text(patched)
        variants = {"patched": patched_c}
        if stock is not None:
            stock_c = tmp / "hlsl_codegen-stock.c"
            stock_c.write_text(stock)
            variants["stock"] = stock_c
        odir = tmp / "obj"
        odir.mkdir()

        def compile_one(item):
            tag, s = item
            o = odir / (tag + "-" + s.stem + ".o")
            r = subprocess.run([cc] + cflags + ["-c", str(s), "-o", str(o)], capture_output=True, text=True)
            return tag, s, o, r

        jobs = [("common-%d" % i, s) for i, s in enumerate(common)] + [("cg-" + k, s) for k, s in variants.items()]
        with ThreadPoolExecutor(max(2, os.cpu_count() or 2)) as ex:
            built = list(ex.map(compile_one, jobs))
        bad = [(str(s), r.stderr[-600:]) for tag, s, o, r in built if r.returncode != 0]
        check("vkd3d-shader builds on the host with and without the patch" if stock is not None else "vkd3d-shader builds on the host", not bad, bad[:2])
        exes = {}
        if not bad:
            commons = [o for tag, s, o, r in built if tag.startswith("common-")]
            objs = {tag[3:]: o for tag, s, o, r in built if tag.startswith("cg-")}
            for k in variants:
                exe = tmp / ("vkd3d-" + k)
                r = subprocess.run([cc, "-o", str(exe)] + [str(o) for o in commons] + [str(objs[k])], capture_output=True, text=True)
                check("the host compiler (%s) links" % k, r.returncode == 0, r.stderr[-600:])
                if r.returncode == 0:
                    exes[k] = exe
        if "patched" in exes:
            work = tmp / "run"
            work.mkdir()
            counter = [0]

            def run(source, profile="ps_3_0", key=None, flags=0, reps=1, exe="patched"):
                """Compiles source; returns (status lines, messages, error-channel lines, blob or None)."""
                counter[0] += 1
                f = work / ("s%d.hlsl" % counter[0])
                f.write_text(source)
                out = work / ("s%d.bin" % counter[0])
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_HLSL_LOOP_FALLBACK"}
                if key is not None:
                    env["MADEIRA_HLSL_LOOP_FALLBACK"] = key
                p = subprocess.run([str(exes[exe]), "hlsl", str(f), profile, str(flags), str(reps), str(out)], capture_output=True, text=True, env=env)
                lines = p.stdout.splitlines()
                return ([l for l in lines if l.startswith("compile ")], [l[5:] for l in lines if l.startswith("msg| ")],
                        [l for l in p.stderr.splitlines() if "[hlsl-loop-fallback]" in l], out.read_bytes() if out.is_file() else None, p.returncode)

            def ret(status, i=-1):
                return int(re.search(r"ret=(-?\d+)", status[i]).group(1))

            def disasm(blob, profile="ps_3_0"):
                f = work / "blob.bin"
                f.write_bytes(blob)
                p = subprocess.run([str(exes["patched"]), "asm", str(f), profile], capture_output=True, text=True)
                return p.stdout if p.returncode == 0 else p.stdout + p.stderr

            def sm1_ops(blob):
                """The opcodes of a Direct3D 9 shader token stream (comments skipped); None when the stream does not end where it should."""
                words = struct.unpack("<%dI" % (len(blob) // 4), blob[:len(blob) // 4 * 4])
                i, ops = 1, []
                while i < len(words):
                    w = words[i]
                    if w & 0xFFFF == 0xFFFF:
                        return ops if i == len(words) - 1 else None
                    if w & 0xFFFF == 0xFFFE:
                        i += 1 + ((w >> 16) & 0x7FFF)
                        continue
                    ops.append(w & 0xFFFF)
                    i += 1 + ((w >> 24) & 0xF)
                return None

            # A small interpreter for what vkd3d writes for these shaders (its disassembly of the ps_3_0 bytecode: mov add sub mul mad rcp rsq min max slt sge
            # cmp frc dp3 dp4 texld texldl, if_ne / else / endif, def): the copies are executed with chosen texture values and compared with what the
            # HLSL says, not only counted. An opcode it does not know stops the check by name.
            def sm1_prepare(asm):
                prog, consts, match, stack = [], {}, {}, []
                for line in asm.splitlines():
                    line = line.strip()
                    if not line or line.startswith(("ps_", "dcl")):
                        continue
                    if line.startswith("def "):
                        m = re.match(r"def c(\d+) = (.*)$", line)
                        consts[int(m.group(1))] = [float(x) for x in m.group(2).split(",")]
                        continue
                    op, _, rest = line.partition(" ")
                    prog.append((op, [a.strip() for a in rest.split(",")] if rest else []))
                for i, (op, _) in enumerate(prog):
                    if op.startswith("if"):
                        stack.append([i, None])
                    elif op == "else":
                        stack[-1][1] = i
                    elif op == "endif":
                        j, e = stack.pop()
                        match[j] = (e, i)
                        if e is not None:
                            match[e] = i
                if stack:
                    raise ValueError("unbalanced if / endif")
                return prog, consts, match

            OPERAND = re.compile(r"^(-)?(\|)?(oC|[rcvs])(\d+)(?:\.([xyzw]{1,4}))?(\|)?$")

            def sm1_execute(prepared, inputs, constant, sample):
                """Runs the program once; returns oC0. constant(k) gives the constants the shader does not define, sample(sampler, coord, lod) a texel."""
                prog, consts, match = prepared
                regs = {}

                def reg(kind, n):
                    if kind == "c":
                        return consts.get(n) or constant(n)
                    return inputs[n] if kind == "v" else regs.setdefault((kind, n), [0.0] * 4)

                def src(text):
                    m = OPERAND.match(text)
                    if not m:
                        raise ValueError("operand " + text)
                    neg, bar, kind, n, sw, _ = m.groups()
                    v = list(reg(kind, int(n)))
                    if sw:
                        v = [v["xyzw".index(c)] for c in (sw + sw[-1] * 4)[:4]]
                    if bar:
                        v = [abs(x) for x in v]
                    return [-x for x in v] if neg else v

                def rcp(x):
                    return 1.0 / x if x else math.copysign(math.inf, x)

                pc = 0
                while pc < len(prog):
                    op, args = prog[pc]
                    if op.startswith("if"):
                        if op != "if_ne":
                            raise ValueError("opcode " + op)
                        if src(args[0])[0] != src(args[1])[0]:
                            pc += 1
                        else:
                            e, end = match[pc]
                            pc = (end if e is None else e) + 1
                        continue
                    if op in ("else", "endif"):
                        pc = match[pc] + 1 if op == "else" else pc + 1
                        continue
                    m = re.match(r"^(oC|[rc])(\d+)(?:\.([xyzw]{1,4}))?$", args[0])
                    if not m:
                        raise ValueError("destination " + args[0])
                    kind, n, mask = m.group(1), int(m.group(2)), m.group(3) or "xyzw"
                    s = [src(a) for a in args[1:]]
                    base = op.split("_")[0]
                    if base == "mov": r = s[0]
                    elif base == "add": r = [x + y for x, y in zip(*s)]
                    elif base == "sub": r = [x - y for x, y in zip(*s)]
                    elif base == "mul": r = [x * y for x, y in zip(*s)]
                    elif base == "mad": r = [x * y + z for x, y, z in zip(*s)]
                    elif base == "rcp": r = [rcp(s[0][0])] * 4
                    elif base == "rsq": r = [1.0 / math.sqrt(abs(s[0][0]))] * 4
                    elif base == "min": r = [min(x, y) for x, y in zip(*s)]
                    elif base == "max": r = [max(x, y) for x, y in zip(*s)]
                    elif base == "slt": r = [1.0 if x < y else 0.0 for x, y in zip(*s)]
                    elif base == "sge": r = [1.0 if x >= y else 0.0 for x, y in zip(*s)]
                    elif base == "cmp": r = [y if x >= 0 else z for x, y, z in zip(*s)]
                    elif base == "frc": r = [x - math.floor(x) for x in s[0]]
                    elif base == "dp3": r = [sum(x * y for x, y in zip(s[0][:3], s[1][:3]))] * 4
                    elif base == "dp4": r = [sum(x * y for x, y in zip(*s))] * 4
                    elif base in ("texld", "texldl"): r = sample(int(args[2][1:].split(".")[0]), s[0], s[0][3] if base == "texldl" else None)
                    else: raise ValueError("opcode " + op)
                    if "_sat" in op:
                        r = [min(max(x, 0.0), 1.0) for x in r]
                    dest = reg(kind, n) if kind != "c" else None
                    if dest is None:
                        raise ValueError("destination " + args[0])
                    for c in mask:
                        dest["xyzw".index(c)] = r["xyzw".index(c)]
                    pc += 1
                return regs[("oC", 0)]

            def sm1_equal(a, b, rel=1e-6):
                return all((math.isnan(x) and math.isnan(y)) or math.isclose(x, y, rel_tol=rel, abs_tol=1e-12) for x, y in zip(a, b))

            def constant(k):
                return [0.37 + 0.011 * k, 0.53 + 0.007 * k, 0.29 + 0.013 * k, 0.61 + 0.005 * k]

            REP, ENDREP, LOOP, ENDLOOP, IFC, ENDIF, TEXLDL = 38, 39, 27, 29, 41, 43, 95
            GAME_FLAGS = 0x10009   # what the game passes (log 13CYSY): debug, row-major packing, the legacy D3DX flag

            # ---------------------------------------------------------------- the fixture's shader
            st, msg, line, blob, rc = run(fixture, flags=GAME_FLAGS)
            check("the game's flags, key unset: the shader fails as it did (E5017, HLSL_IR_LOOP, at the loop's line)",
                  rc == 0 and ret(st) < 0 and blob is None and any(re.fullmatch(r"<anonymous>:%d:\d+: E5017: Aborting due to not yet implemented feature: Instruction type HLSL_IR_LOOP\." % LOOP_LINE, m) for m in msg) and not line, (st, msg[-2:], line))
            for off in ("0", "7", "257"):
                st, msg, line, blob, rc = run(fixture, key=off, flags=GAME_FLAGS)
                check("key %r leaves it off: the same error, nothing printed" % off,
                      rc == 0 and ret(st) < 0 and blob is None and any("E5017" in m and "HLSL_IR_LOOP" in m for m in msg) and not line, (st, msg[-2:], line))
            st, msg, line, blob, rc = run(fixture, key="40", flags=GAME_FLAGS)
            ops = sm1_ops(blob) if blob else None
            check("key 40: the shader compiles (ps_3_0 bytecode, no error in the compiler's text)",
                  rc == 0 and st and ret(st) == 0 and blob and struct.unpack("<I", blob[:4])[0] == 0xFFFF0300 and ops is not None
                  and not any("E5" in m and ": E" in m for m in msg), (st, msg[-3:]))
            check("key 40: no rep / loop opcode in the bytecode; branches are balanced",
                  ops is not None and not {REP, ENDREP, LOOP, ENDLOOP} & set(ops) and ops.count(IFC) == ops.count(ENDIF) and ops.count(IFC) >= 40,
                  (ops or [])[:60])
            check("key 40: 40 guarded copies of the loop body (4 shadow taps each)", ops is not None and ops.count(TEXLDL) == 4 * 40, ops.count(TEXLDL) if ops else None)
            check("key 40: the line is printed once and names the copies", line and len(line) == 1 and line[0].endswith(
                "[hlsl-loop-fallback] madeira-bcd: a loop is written as 40 guarded copies"), line)
            text = disasm(blob) if blob else ""
            check("key 40: the disassembly agrees (ps_3_0, texldl, if_ne, no rep / loop)", text.startswith("ps_3_0") and text.count("texldl") == 160
                  and not re.search(r"^\s*(rep|endrep|loop|endloop)\b", text, re.M), text[:200])
            for key, copies in (("1", 64), ("8", 8)):
                st, msg, line, blob, rc = run(fixture, key=key, flags=GAME_FLAGS)
                ops = sm1_ops(blob) if blob else None
                check("key %s: %d guarded copies (4 taps each), no loop opcode, one line" % (key, copies),
                      rc == 0 and st and ret(st) == 0 and ops is not None and ops.count(TEXLDL) == 4 * copies and not {REP, ENDREP, LOOP, ENDLOOP} & set(ops)
                      and len(line) == 1 and line[0].endswith("a loop is written as %d guarded copies" % copies),
                      (st, msg[-2:], line, ops.count(TEXLDL) if ops else None))
            st, msg, line, blob, rc = run(fixture, key="40", flags=GAME_FLAGS, reps=3)
            check("a process compiling the shader three times prints the line once", len(st) == 3 and all(ret(st, i) == 0 for i in range(3)) and len(line) == 1, (st, line))
            st1, msg1, line1, blob1, rc1 = run(fixture, key="40", flags=0)
            check("the same without the game's flags (debug stripped): it compiles to ps_3_0 bytecode as well",
                  rc1 == 0 and ret(st1) == 0 and blob1 and len(line1) == 1, (st1, msg1[-2:]))

            # the n-forced-to-a-constant twin: the stock unroller does it, so the key changes nothing
            const_fix = fixture.replace(N_DEF, "int n = 39;\n")
            results = {}
            for label, kw in (("unset", {}), ("key 40", {"key": "40"}), ("key 1", {"key": "1"})):
                results[label] = run(const_fix, flags=GAME_FLAGS, **kw)
            base = results["unset"]
            check("n forced to a constant: it compiles with and without the key, to the same bytes, and prints nothing",
                  base[4] == 0 and ret(base[0]) == 0 and base[3] and all(r[3] == base[3] and r[1] == base[1] and not r[2] for r in results.values()),
                  [(k, r[0], len(r[3] or b"")) for k, r in results.items()])
            ops = sm1_ops(base[3]) if base[3] else None
            check("n forced to a constant: 39 copies, the stock unroller's, no loop opcode",
                  ops is not None and ops.count(TEXLDL) == 4 * 39 and not {REP, ENDREP, LOOP, ENDLOOP} & set(ops), ops.count(TEXLDL) if ops else None)

            # the copies executed: the fallback's shader against the stock unroller's, run on the same texture values for many counts
            #   twin 40: the same loop written with the constant bound 40 and the count as a break test (the stock unroller writes it)
            #   twin 41: the same with 41, to show the comparison can tell one copy more
            #   const 39: n forced to 39 (the stock unroller writes 39 copies)
            twin40 = fixture.replace(LOOP_HEAD, "    for (int i = 0; i < 40; ++i)\n    {\n        if (!(i < n))\n            break;\n")
            twin41 = fixture.replace(LOOP_HEAD, "    for (int i = 0; i < 41; ++i)\n    {\n        if (!(i < n))\n            break;\n")
            check("(the twins of the loop were written)", twin40 != fixture and twin41 != fixture and twin40 != twin41)
            built = {"fallback": run(fixture, key="40", flags=GAME_FLAGS), "twin 40": run(twin40, flags=GAME_FLAGS),
                     "twin 41": run(twin41, flags=GAME_FLAGS), "const 39": run(const_fix, flags=GAME_FLAGS)}
            check("the twins compile with the stock unroller, without the key, and print nothing",
                  all(ret(b[0]) == 0 and b[3] for b in built.values()) and not any(b[2] for k, b in built.items() if k != "fallback"),
                  {k: (b[0], b[1][-1:]) for k, b in built.items()})
            if all(b[3] for b in built.values()):
                progs = {k: sm1_prepare(disasm(b[3])) for k, b in built.items()}
                found = re.search(r"^texld [^,]+, [^,]+, s(\d+)", disasm(built["fallback"][3]), re.M)
                check("the fixture shader reads its position map with the one plain texld", found is not None)
                position = int(found.group(1)) if found else 1
                COUNTS = (0, 1, 2, 3, 10, 20, 38, 39, 40, 41, 45)

                def game_texels(n):
                    z = 12.0 / (39.5 - n)   # n = (int)((z - 0.3) / (z / 40)) = (int)(40 - 12 / z): this z gives n exactly (a negative z gives n of 40 and more)

                    def sample(sampler, coord, lod):
                        if sampler == position:
                            return [0.4, 0.3, z, 1.0]
                        return [0.5 + 0.5 * math.sin(3 * coord[0] + 2 * coord[1]), coord[1], coord[0], 1.0]
                    return sample

                out = {}
                try:
                    for k, pr in progs.items():
                        out[k] = {n: sm1_execute(pr, {0: [0.3, 0.6, 0.0, 0.0]}, constant, game_texels(n)) for n in COUNTS}
                except ValueError as error:
                    out = None
                    check("the interpreter runs the fixture shader", False, error)
                if out:
                    check("executed, the fallback's 40 copies give the stock unroller's result for the loop bounded by 40 with the count as a break test, for counts %s (40 and above are cut at 40)" % (COUNTS,),
                          all(sm1_equal(out["fallback"][n], out["twin 40"][n], 1e-9) for n in COUNTS),
                          [n for n in COUNTS if not sm1_equal(out["fallback"][n], out["twin 40"][n], 1e-9)])
                    check("executed, for the count 39 the fallback gives the result of the shader with n forced to 39 (the stock unroller's 39 copies)",
                          sm1_equal(out["fallback"][39], out["const 39"][39], 1e-9), (out["fallback"][39], out["const 39"][39]))
                    check("the comparison can tell: the results differ for every count from 0 to 39 and one copy more (41) is seen at the count 41",
                          len({tuple(round(x, 12) for x in out["fallback"][n]) for n in COUNTS if n <= 39}) == len([n for n in COUNTS if n <= 39])
                          and not sm1_equal(out["fallback"][41], out["twin 41"][41], 1e-9) and not sm1_equal(out["fallback"][39], out["fallback"][38]),
                          [out["fallback"][n] for n in (38, 39, 41)])

            # ---------------------------------------------------------------- small shaders
            def shader(loop, decl="int n = (int)(tex2D(cnt, uv).x * 39.0);"):
                return ("sampler2D cnt : register(s0);\nsampler2D s : register(s1);\nfloat4 main(float2 uv : TEXCOORD0) : COLOR\n{\n    float4 r = 0;\n    "
                        + decl + "\n" + loop + "\n    return r;\n}\n")

            body = "        r += tex2Dlod(s, float4(uv + i * 0.01, 0, 0));\n"
            DYN = shader("    for (int i = 0; i < n; ++i)\n" + body)
            for key, copies in (("40", 40), ("1", 64), ("8", 8), ("256", 256)):
                st, msg, line, blob, rc = run(DYN, key=key)
                ops = sm1_ops(blob) if blob else None
                check("a loop counting to a texture value, key %s: %d copies, no loop opcode, one line" % (key, copies),
                      rc == 0 and ret(st) == 0 and ops is not None and ops.count(TEXLDL) == copies and not {REP, ENDREP, LOOP, ENDLOOP} & set(ops)
                      and len(line) == 1 and line[0].endswith("%d guarded copies" % copies), (st, msg[-2:], line))
            st, msg, line, blob, rc = run(DYN)
            check("the same loop, key unset: E5017 HLSL_IR_LOOP", ret(st) < 0 and any("E5017" in m and "HLSL_IR_LOOP" in m for m in msg) and not line, (st, msg))
            for off in ("", "0", "2", "abc", "40x", " 40", "40 ", "-40", "+40", "4.0", "0x28", "1e1", "7", "257", "1000", "99999999999999999999"):
                st, msg, line, blob, rc = run(DYN, key=off)
                check("key %r is not 1 or a number from 8 to 256: off (the same error, nothing printed)" % off,
                      rc == 0 and ret(st) < 0 and blob is None and any("E5017" in m and "HLSL_IR_LOOP" in m for m in msg) and not line, (st, msg[-2:], line))
            for on, copies in (("8", 8), ("9", 9), ("255", 255), ("256", 256)):
                st, msg, line, blob, rc = run(DYN, key=on)
                check("key %r is %d copies" % (on, copies), rc == 0 and ret(st) == 0 and sm1_ops(blob).count(TEXLDL) == copies and len(line) == 1, (st, msg[-2:], line))
            st, msg, line, blob, rc = run(DYN, key="40", reps=3)
            check("a loop that cannot run more than the copy count is cut at it: 40 copies for a loop that could run 100 times",
                  ret(st) == 0 and sm1_ops(blob).count(TEXLDL) == 40 and len(line) == 1)
            TWO = shader("    for (int i = 0; i < n; ++i)\n" + body + "    for (int j = 0; j < n; ++j)\n" + body.replace("i * 0.01", "j * 0.02"))
            st, msg, line, blob, rc = run(TWO, key="12")
            check("two such loops: both are written (12 copies each), the line is printed once",
                  ret(st) == 0 and sm1_ops(blob).count(TEXLDL) == 24 and len(line) == 1 and line[0].endswith("12 guarded copies"), (st, msg[-2:], line))
            BRK = shader("    for (int i = 0; i < n; ++i)\n    {\n        if (i == 2) continue;\n        if (r.x > 3.0) break;\n" + body + "    }\n")
            st, msg, line, blob, rc = run(BRK, key="16")
            ops = sm1_ops(blob) if blob else None
            check("a continue and a break in the body: 16 copies (the copy for i == 2 is a continue the unroller sees and drops its tap: 15 taps), no loop opcode, branches balanced",
                  ret(st) == 0 and ops is not None and ops.count(TEXLDL) == 15 and not {REP, ENDREP, LOOP, ENDLOOP} & set(ops) and ops.count(IFC) == ops.count(ENDIF),
                  (st, msg[-2:], ops.count(TEXLDL) if ops else None))
            WHL = shader("    while (n > 0)\n    {\n        --n;\n" + body.replace("i * 0.01", "n * 0.01") + "    }\n")
            st, msg, line, blob, rc = run(WHL, key="16")
            check("a while loop whose count is a texture value: 16 copies", ret(st) == 0 and sm1_ops(blob).count(TEXLDL) == 16 and len(line) == 1, (st, msg[-2:]))

            # the same loops executed: the copies compute what the HLSL says, for counts below, at and above the number of copies
            U, V = 0.3, 0.6

            def tex_s(c):
                return [c[0], c[1], c[0] * c[1], 1.0]

            def small_texels(n):
                def sample(sampler, coord, lod):
                    return [(n + 0.5) / 39.0, 0.0, 0.0, 1.0] if sampler == 0 else tex_s(coord)   # sampler 0 gives the count n, sampler 1 the taps
                return sample

            def add4(a, b):
                return [x + y for x, y in zip(a, b)]

            def ref_for(n, copies):
                r = [0.0] * 4
                for i in range(min(n, copies)):
                    r = add4(r, tex_s([U + i * 0.01, V + i * 0.01]))
                return r

            def ref_two(n, copies):
                r = ref_for(n, copies)
                for j in range(min(n, copies)):
                    r = add4(r, tex_s([U + j * 0.02, V + j * 0.02]))
                return r

            def ref_break(n, copies):
                r = [0.0] * 4
                for i in range(min(n, copies)):
                    if i == 2:
                        continue
                    if r[0] > 3.0:
                        break
                    r = add4(r, tex_s([U + i * 0.01, V + i * 0.01]))
                return r

            def ref_while(n, copies):
                r, m = [0.0] * 4, n
                for _ in range(min(n, copies)):
                    m -= 1
                    r = add4(r, tex_s([U + m * 0.01, V + m * 0.01]))
                return r

            def ref_nested(n, copies):
                r = [0.0] * 4
                for i in range(min(n, copies)):
                    for j in range(min(n, copies)):
                        r = add4(r, tex_s([U + i * 0.01 + j * 0.02, V + i * 0.01 + j * 0.02]))
                return r

            def semantics(label, source, key, copies, ref, counts):
                st, msg, line, blob, rc = run(source, key=key)
                if rc or not st or ret(st) != 0 or not blob:
                    check(label, False, (st, msg[-2:]))
                    return None
                try:
                    prog = sm1_prepare(disasm(blob))
                    got = {n: sm1_execute(prog, {0: [U, V, 0.0, 0.0]}, constant, small_texels(n)) for n in counts}
                except ValueError as error:
                    check(label, False, error)
                    return None
                bad = [(n, got[n], ref(n, copies)) for n in counts if not sm1_equal(got[n], ref(n, copies))]
                check(label, not bad, bad[:2])
                return got

            got = semantics("executed, the loop counting to a texture value, 40 copies: the sum of min(n, 40) taps for n = 0 .. 3, 8, 12, 15 .. 17, 20, 39 .. 41, 57",
                            DYN, "40", 40, ref_for, (0, 1, 2, 3, 8, 12, 15, 16, 17, 20, 39, 40, 41, 57))
            check("(the comparison can tell 41 copies from 40)", got is not None and not sm1_equal(got[41], ref_for(41, 41)) and not sm1_equal(got[40], got[39]))
            semantics("executed, the same loop with key 1 (64 copies): n = 0, 5, 63, 64, 65, 80", DYN, "1", 64, ref_for, (0, 5, 63, 64, 65, 80))
            semantics("executed, the same loop with key 8 (8 copies): n = 0, 1, 7, 8, 9, 20", DYN, "8", 8, ref_for, (0, 1, 7, 8, 9, 20))
            semantics("executed, the same loop with key 256 (256 copies): n = 0, 3, 255, 256, 257, 300", DYN, "256", 256, ref_for, (0, 3, 255, 256, 257, 300))
            semantics("executed, two such loops, 12 copies each: n = 0, 1, 11, 12, 13, 30", TWO, "12", 12, ref_two, (0, 1, 11, 12, 13, 30))
            semantics("executed, a continue (i == 2) and a break (r.x > 3) in the body, 16 copies: n = 0, 1, 2, 3, 8, 12, 15, 16, 17, 20", BRK, "16", 16, ref_break,
                      (0, 1, 2, 3, 8, 12, 15, 16, 17, 20))
            semantics("executed, a while loop counting a texture value down, 16 copies: n = 0, 1, 2, 8, 15, 16, 17, 20", WHL, "16", 16, ref_while,
                      (0, 1, 2, 8, 15, 16, 17, 20))
            NESTED = shader("    for (int i = 0; i < n; ++i)\n    {\n        for (int j = 0; j < n; ++j)\n"
                            "            r += tex2Dlod(s, float4(uv + i * 0.01 + j * 0.02, 0, 0));\n    }\n")
            semantics("executed, a loop inside a loop, both counting to the texture value, 10 copies each: n = 0, 1, 2, 3, 9, 10, 11, 15", NESTED, "10", 10, ref_nested,
                      (0, 1, 2, 3, 9, 10, 11, 15))

            # sources the stock compiler handles: the key must not change a byte of them
            CONST = shader("    for (int i = 0; i < 100; ++i)\n" + body, decl="")
            UNROLL_N = shader("    [unroll(8)] for (int i = 0; i < n; ++i)\n" + body)
            FORCED = shader("    [unroll] for (int i = 0; i < n; ++i)\n" + body)
            FORCE_LOOP = shader("    [loop] for (int i = 0; i < n; ++i)\n" + body)
            plain = {"a constant count of 100 (above the copy count)": CONST, "an explicit [unroll(8)]": UNROLL_N}
            for label, source in plain.items():
                a = run(source)
                outs = {k: run(source, key=k) for k in ("40", "64", "256")}
                check("%s: it compiles, and the key changes nothing (same bytes, same text, nothing printed)" % label,
                      a[4] == 0 and ret(a[0]) == 0 and a[3] and all(o[3] == a[3] and o[1] == a[1] and not o[2] for o in outs.values()), (a[0], a[1][-2:]))
            check("a constant count of 100 is unrolled in full (100 copies, not cut at the key's count)", sm1_ops(run(CONST, key="40")[3]).count(TEXLDL) == 100)
            check("an explicit [unroll(8)] is 8 copies whatever the key says", sm1_ops(run(UNROLL_N, key="64")[3]).count(TEXLDL) == 8)
            for label, source, needle in (("a forced [unroll]", FORCED, "Unable to unroll loop"), ("a [loop]", FORCE_LOOP, "HLSL_IR_LOOP")):
                a = run(source)
                b = run(source, key="40")
                check("%s stays refused with the key set (same error text, no bytes, nothing printed)" % label,
                      ret(a[0]) < 0 and ret(b[0]) < 0 and a[1] == b[1] and any(needle in m for m in b[1]) and b[3] is None and not b[2], (a[1][-2:], b[1][-2:]))

            # Shader Model 4 writes a real loop: the key is not looked at
            SM4 = ("Texture2D t : register(t0);\nSamplerState s : register(s0);\n"
                   "float4 main(float2 uv : TEXCOORD0) : SV_Target\n{\n    float4 r = 0;\n"
                   "    int n = (int)(t.Sample(s, uv).x * 39.0);\n    for (int i = 0; i < n; ++i)\n"
                   "        r += t.SampleLevel(s, uv + i * 0.01, 0);\n    return r;\n}\n")
            a = run(SM4, profile="ps_4_0")
            outs = [run(SM4, profile="ps_4_0", key=k) for k in ("1", "40", "256")]
            check("ps_4_0 with the same kind of loop: it compiles, and the key changes nothing (same bytes, same text, nothing printed)",
                  a[4] == 0 and ret(a[0]) == 0 and a[3] and all(o[3] == a[3] and o[1] == a[1] and not o[2] for o in outs), (a[0], a[1][-2:]))
            check("ps_4_0: the loop is still a loop in the code (no copies)", re.search(r"^\s*loop\b", disasm(a[3], "ps_4_0"), re.M) is not None, disasm(a[3], "ps_4_0")[:300])
            a = run(fixture, profile="ps_4_0", flags=GAME_FLAGS)
            b = run(fixture, profile="ps_4_0", key="40", flags=GAME_FLAGS)
            check("the fixture shader as ps_4_0: the same result with and without the key, nothing printed (whatever it is: the key is not looked at)",
                  a[0] == b[0] and a[1] == b[1] and a[3] == b[3] and not b[2], (a[0], b[0], a[1][-2:], b[1][-2:]))

            # the tree without the patch: key unset, the patched compiler gives the same result for everything above
            if "stock" in exes:
                corpus = [("the fixture shader", fixture, "ps_3_0", GAME_FLAGS), ("the fixture shader, n a constant", const_fix, "ps_3_0", GAME_FLAGS),
                          ("a loop counting to a texture value", DYN, "ps_3_0", 0), ("a constant count of 100", CONST, "ps_3_0", 0),
                          ("an explicit [unroll(8)]", UNROLL_N, "ps_3_0", 0), ("a forced [unroll]", FORCED, "ps_3_0", 0),
                          ("a [loop]", FORCE_LOOP, "ps_3_0", 0), ("a break and a continue", BRK, "ps_3_0", 0), ("a while loop", WHL, "ps_3_0", 0),
                          ("ps_4_0 with a loop", SM4, "ps_4_0", 0), ("the fixture shader as ps_4_0", fixture, "ps_4_0", GAME_FLAGS)]
                same = []
                for label, source, profile, fl in corpus:
                    a = run(source, profile=profile, flags=fl, exe="stock")
                    b = run(source, profile=profile, flags=fl)
                    same.append((label, a[0] == b[0] and a[1] == b[1] and a[3] == b[3] and not b[2]))
                check("key unset: the patched compiler gives the tree's result (status, text, bytes) for all %d sources" % len(corpus),
                      all(s for _, s in same), [l for l, s in same if not s])
                a = run(DYN, exe="stock")
                check("the tree without the patch refuses the game's kind of loop, and the key (which it does not know) changes nothing there",
                      ret(a[0]) < 0 and any("HLSL_IR_LOOP" in m for m in a[1]), a[1][-2:])

    mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    build_dir = Path.home() / "madeira-bcd/wine/build-i386"
    if not (mingw / "i686-w64-mingw32-gcc").is_file() or not (build_dir / "include").is_dir():
        print("skip: the PE compile needs llvm-mingw and the full tree's wine/build-i386 (the Mac)")
    else:
        f = tmp / "hlsl_codegen.c"
        f.write_text(patched)
        env = dict(os.environ, PATH=str(mingw) + os.pathsep + os.environ["PATH"])
        cmd = ["i686-w64-mingw32-gcc", "-D__STDC__", "-c", "-o", str(tmp / "u.o"), str(f),
               "-I" + str(build_dir / "libs/vkd3d"), "-I" + str(wine / "libs/vkd3d"), "-I" + str(build_dir / "include"), "-I" + str(wine / "include"),
               "-I" + str(wine / "include/msvcrt"), "-I" + str(wine / "libs/vkd3d/include"), "-I" + str(wine / "libs/vkd3d/include/private"),
               "-I" + str(wine / "libs/vkd3d/libs/vkd3d"), "-I" + str(wine / "libs/vkd3d/libs/vkd3d-shader"), "-I" + str(wine / "libs/vkd3d/libs/vkd3d-utils"),
               "-D_UCRT", "-DWINE_NO_NAMELESS_EXTENSION", "-DLIBVKD3D_SOURCE", "-DLIBVKD3D_SHADER_SOURCE", "-DLIBVKD3D_UTILS_SOURCE", "-D__WINE_PE_BUILD",
               "-Wall", "-target", "i686-windows", "-fuse-ld=lld", "--no-default-config", "-fno-strict-aliasing", "-Wno-microsoft-enum-forward-reference",
               "-ffunction-sections", "-fasync-exceptions", "-ffp-exception-behavior=maytrap", "-fms-hotpatch", "-fno-omit-frame-pointer",
               "-mlong-double-64", "-O2"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        check("hlsl_codegen.c compiles for the i386 farm with the build's flags (no error, no warning)",
              r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

finish()
