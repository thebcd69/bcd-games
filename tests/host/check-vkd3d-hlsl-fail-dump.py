#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-vkd3d-hlsl-fail-dump.py: Wine's HLSL compiler dumps the shader it refused, on request (night round 2026-11-11, G11 S3).

S.T.A.L.K.E.R. (xrEngine compiles ~100 Direct3D 9 shaders at run time) stops at the first shader vkd3d refuses and the log carries only its first
error, so each device round finds one more wall. With env.MADEIRA_HLSL_FAIL_DUMP = 1 a failed compile writes the compiler's text and the
preprocessed source with the numbers the error lines cite. Checks:
  - CI applies the patch before the i386 farm is built and runs this check;
  - with a Wine tree (wine/ of this tree, WINE_SRC or ~/madeira-bcd/wine; skipped without one): the patch applies once to a copy of
    vkd3d_utils_main.c, a second run changes nothing, a moved call refuses it and leaves the file alone; the dump runs only for a failed
    compile (`ret < 0`) and only through the helper that reads the key;
  - the compiler itself (the Mac: a C compiler, flex, and the parser files of the full tree's i386 build): vkd3d-shader is built on the host
    and the real helper, extracted from the patched file, is called after a failing compile exactly as D3DCompile2VKD3D calls it: with the key
    unset nothing is printed; with it set a failing shader prints its header, the compiler's text and the preprocessed lines, and the line the
    error cites holds the offending name (macros expanded); a shader that compiles prints nothing; a process dumps 3 failures at most; a
    1000-line shader stops at 800 lines with a note; a line of 700 characters is cut at 600;
  - with the llvm-mingw toolchain and the full tree's i386 build (the Mac): the patched file compiles for the i386 farm with the build's flags,
    with no error and no warning.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import importlib.util, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("hlslfail", root / "tools/patch-wine-vkd3d-hlsl-fail-dump.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine vkd3d-utils to dump a refused shader (opt-in)\n")
check("CI runs this check and applies the patch before the i386 farm is built",
      "python3 tests/host/check-vkd3d-hlsl-fail-dump.py\n          python3 tools/patch-wine-vkd3d-hlsl-fail-dump.py wine\n" in wf[step:step + 500]
      and step < wf.index("      - name: Build i386 farm (WoW64)\n"))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the key is in the settings catalog (default off)",
      '"env.MADEIRA_HLSL_FAIL_DUMP"' in cat and 'MADEIRA_HLSL_FAIL_DUMP' in cat[cat.index('"env.MADEIRA_HLSL_FAIL_DUMP"'):][:900]
      and '"default": "0"' in cat[cat.index('"env.MADEIRA_HLSL_FAIL_DUMP"'):][:900])

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    print("PASS: a refused shader can be dumped" if ok else "FAILED: vkd3d hlsl fail dump")
    sys.exit(0 if ok else 1)

src = (wine / P.FILE).read_text()
if P.MARKER in src:
    print("note: the Wine tree is already patched; only the patched file is checked")
    patched = src
else:
    patched = None
with tempfile.TemporaryDirectory(prefix="madeira-vkd3d-hlslfail-") as tmp:
    tmp = Path(tmp)
    if patched is None:
        tree = tmp / "w"
        (tree / Path(P.FILE).parent).mkdir(parents=True)
        t = tree / P.FILE
        t.write_text(src)
        script = str(root / "tools/patch-wine-vkd3d-hlsl-fail-dump.py")
        r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        patched = t.read_text()
        r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == patched and P.MARKER in patched,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        t.write_text(src.replace(P.CALL, P.CALL.replace("&byte_code, &messages)", "&byte_code,  &messages)"), 1))
        r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("a moved call refuses the patch and leaves the file alone",
              r3.returncode != 0 and "vkd3d_shader_compile call" in r3.stderr and P.MARKER not in t.read_text(), r3.stderr)
    body = patched[patched.index("static void madeira_hlsl_fail_dump("):]
    helper = body[:body.index("\n}\n") + 3]
    call = patched[patched.index(P.CALL):][:len(P.CALL_NEW) + 80]
    check("the dump runs only for a failed compile (ret < 0), right after vkd3d_shader_compile, before the messages are handed on",
          P.CALL_NEW in patched and patched.index(P.CALL_NEW) < patched.index("if (messages && messages_blob)", patched.index(P.CALL_NEW)))
    check("the helper reads the key once and returns before any work when it is off",
          helper.count('getenv("MADEIRA_HLSL_FAIL_DUMP")') == 1 and helper.index("if (!enabled ||") < helper.index("vkd3d_shader_preprocess("))

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
        if True:
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

            harness = tmp / "harness.c"
            harness.write_text(r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include "vkd3d_shader_private.h"
@HELPER@
static char *slurp(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb"); char *b; long len;
    if (!f) exit(9);
    fseek(f, 0, SEEK_END); len = ftell(f); fseek(f, 0, SEEK_SET);
    b = malloc(len + 1); if (fread(b, 1, len, f) != (size_t)len) exit(9); b[len] = 0; fclose(f); *n = len; return b;
}
int main(int argc, char **argv)
{
    const char *profile = argv[2];
    int reps = atoi(argv[3]), i;
    size_t size;
    char *source = slurp(argv[1], &size);
    for (i = 0; i < reps; ++i)
    {
        struct vkd3d_shader_preprocess_info preprocess_info = {0};
        struct vkd3d_shader_hlsl_source_info hlsl_info = {0};
        struct vkd3d_shader_compile_info compile_info = {0};
        struct vkd3d_shader_compile_option options[1];
        struct vkd3d_shader_code code = {0};
        char *messages = NULL;
        int ret;
        options[0].name = VKD3D_SHADER_COMPILE_OPTION_API_VERSION;
        options[0].value = VKD3D_SHADER_API_VERSION_CURRENT;
        compile_info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
        compile_info.next = &preprocess_info;
        compile_info.source.code = source;
        compile_info.source.size = size;
        compile_info.source_type = VKD3D_SHADER_SOURCE_HLSL;
        compile_info.target_type = profile[3] >= '4' ? VKD3D_SHADER_TARGET_DXBC_TPF : VKD3D_SHADER_TARGET_D3D_BYTECODE;
        compile_info.options = options;
        compile_info.option_count = 1;
        compile_info.log_level = VKD3D_SHADER_LOG_INFO;
        compile_info.source_name = "<anonymous>";
        preprocess_info.type = VKD3D_SHADER_STRUCTURE_TYPE_PREPROCESS_INFO;
        preprocess_info.next = &hlsl_info;
        hlsl_info.type = VKD3D_SHADER_STRUCTURE_TYPE_HLSL_SOURCE_INFO;
        hlsl_info.profile = profile;
        hlsl_info.entry_point = "main";
        ret = vkd3d_shader_compile(&compile_info, &code, &messages);
        if (ret < 0)
            madeira_hlsl_fail_dump(&compile_info, &preprocess_info, profile, "main", 0x1000, messages);
        printf("compile %d ret=%d\n", i, ret);
        vkd3d_shader_free_messages(messages);
        vkd3d_shader_free_shader_code(&code);
    }
    return 0;
}
""".replace("@HELPER@", helper))
            cflags = ["-std=gnu99", "-O1", "-w", "-include", str(inc / "config.h"), "-I" + str(V / "include"), "-I" + str(V / "include/private"),
                      "-I" + str(V / "libs/vkd3d-shader"), "-I" + str(gen_src), "-I" + str(inc), "-DLIBVKD3D_SHADER_SOURCE"]
            common = sorted((V / "libs/vkd3d-shader").glob("*.c")) + [V / "libs/vkd3d-common" / n for n in ("debug.c", "error.c", "memory.c", "utf8.c")]
            common += [gen_src / "hlsl.tab.c", gen_src / "preproc.tab.c", gen_src / "preproc.yy.c"]
            odir = tmp / "obj"
            odir.mkdir()

            def compile_one(s):
                o = odir / (s.stem + "-" + str(abs(hash(str(s))) % 10**8) + ".o")
                r = subprocess.run([cc] + cflags + ["-c", str(s), "-o", str(o)], capture_output=True, text=True)
                return s, o, r

            with ThreadPoolExecutor(max(2, os.cpu_count() or 2)) as ex:
                built = list(ex.map(compile_one, common + [tmp / "hlsl.yy.c", harness]))
            bad = [(str(s), r.stderr[-600:]) for s, o, r in built if r.returncode != 0]
            check("vkd3d-shader and the extracted helper build on the host", not bad, bad[:2])
            if not bad:
                objs = {s: o for s, o, r in built}
                exe = tmp / "vkd3d-fail"
                r = subprocess.run([cc, "-o", str(exe)] + [str(objs[s]) for s in common] + [str(objs[tmp / "hlsl.yy.c"]), str(objs[harness])],
                                   capture_output=True, text=True)
                check("the host compiler with the helper links", r.returncode == 0, r.stderr[-600:])
                if r.returncode == 0:
                    def failed(out):
                        return len(out) == 1 and re.fullmatch(r"compile 0 ret=-\d+", out[0]) is not None

                    def run(source, profile="ps_3_0", reps=1, key=None):
                        f = tmp / "shader.hlsl"
                        f.write_text(source)
                        env = dict(os.environ)
                        env.pop("MADEIRA_HLSL_FAIL_DUMP", None)
                        if key is not None:
                            env["MADEIRA_HLSL_FAIL_DUMP"] = key
                        p = subprocess.run([str(exe), str(f), profile, str(reps)], capture_output=True, text=True, env=env)
                        return p.returncode, p.stdout.splitlines(), [l.split(" ", 1)[1] if " " in l else l for l in p.stderr.splitlines()]

                    bad_src = ("#define COL float4(1, 0, 0, 1)\n"
                               "float4 helper(float4 a)\n"
                               "{\n"
                               "    return a * 2;\n"
                               "}\n"
                               "float4 main(float4 p : TEXCOORD0) : COLOR\n"
                               "{\n"
                               "    float4 r = helper(p) + COL;\n"
                               "    return r + undeclared_thing;\n"
                               "}\n")
                    good_src = bad_src.replace("undeclared_thing", "p")
                    rc, out, err = run(bad_src)
                    check("key unset: the shader fails as before and nothing is printed", rc == 0 and failed(out) and not err, (rc, out, err[:3]))
                    rc, out, err = run(bad_src, key="0")
                    check("key 0: nothing is printed either", rc == 0 and failed(out) and not err, (rc, out, err[:3]))
                    rc, out, err = run(good_src, key="1")
                    check("key set, a shader that compiles: nothing is printed", rc == 0 and out == ["compile 0 ret=0"] and not err, (rc, out, err[:3]))
                    rc, out, err = run(bad_src, key="1")
                    fl = [l for l in err if "[hlsl-fail]" in l]
                    msg = [l for l in fl if "<anonymous>:" in l and "| " not in l]
                    numbered = {int(m.group(1)): m.group(2) for m in (re.search(r"\[hlsl-fail\]\s+(\d+)\| (.*)", l) for l in fl) if m}
                    cited = int(re.search(r"<anonymous>:(\d+):", msg[0]).group(1)) if msg else 0
                    check("key set: a header with the profile, entry, flags and size, then the compiler's text", len(fl) > 3 and
                          '[hlsl-fail] profile="ps_3_0" entry="main" flags=0x1000 bytes=%d' % len(bad_src) in fl[0] and bool(msg), fl[:4])
                    check("the dump has every preprocessed line, numbered, with the macro expanded in place (the #define line stays an empty line: numbers stay aligned)",
                          set(range(1, 11)) <= set(numbered) and "float4 ( 1 , 0 , 0 , 1 )" in numbered.get(8, "") and "helper ( p )" in numbered.get(8, ""), numbered)
                    check("the line the error cites holds the offending name",
                          cited == 9 and "undeclared_thing" in numbered.get(cited, ""), (cited, numbered.get(cited), msg[:1]))
                    check("the compile result is unchanged by the dump (still failed)", rc == 0 and failed(out), out)
                    rc, out, err = run(bad_src, key="1", reps=5)
                    check("a process dumps 3 failures at most", len([l for l in err if "profile=" in l]) == 3 and out[-1].startswith("compile 4 ret=-") and out[-1] != "compile 4 ret=0", (len(err), out))
                    long_src = "".join("static const float c%d = %d.0;\n" % (i, i) for i in range(1000)) + bad_src
                    rc, out, err = run(long_src, key="1")
                    nums = [int(m.group(1)) for m in (re.search(r"\[hlsl-fail\]\s+(\d+)\| ", l) for l in err) if m]
                    check("a 1010-line shader: the dump stops at 800 lines (or 64 KB) with a note",
                          rc == 0 and nums and max(nums) <= 800 and any("the dump stops here" in l for l in err), (max(nums) if nums else None, err[-2:]))
                    wide_src = "float4 main() : COLOR { return float4(0, 0, 0, 0) + " + "float4(1, 1, 1, 1) + " * 40 + " undeclared_wide; }\n"
                    rc, out, err = run(wide_src, key="1")
                    wide = [l for l in err if re.search(r"\[hlsl-fail\]\s+1\| ", l)]
                    check("a 700-character line is cut at 600", rc == 0 and len(wide) == 1 and 600 <= len(wide[0].split("| ", 1)[1]) <= 600, [len(w) for w in wide])

    mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    build_dir = Path.home() / "madeira-bcd/wine/build-i386"
    if not (mingw / "i686-w64-mingw32-gcc").is_file() or not (build_dir / "include").is_dir():
        print("skip: the PE compile needs llvm-mingw and the full tree's wine/build-i386 (the Mac)")
    else:
        f = tmp / "vkd3d_utils_main.c"
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
        check("vkd3d_utils_main.c compiles for the i386 farm with the build's flags (no error, no warning)",
              r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

print("PASS: a refused shader can be dumped" if ok else "FAILED: vkd3d hlsl fail dump")
sys.exit(0 if ok else 1)
