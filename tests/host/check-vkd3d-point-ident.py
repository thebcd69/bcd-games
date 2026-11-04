#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-vkd3d-point-ident.py: Wine's HLSL compiler reads `point` as a name unless a type or a modifier follows it (round 630, O3 S19).

S.T.A.L.K.E.R. Clear Sky (RVFPNE): "E5000: syntax error, unexpected KW_POINT" in a Direct3D 9 shader that names a variable `point`.

Checks:
  - CI applies the patch before the i386 farm is built and runs this check;
  - Wine source (wine/ of this tree, WINE_SRC, or ~/madeira-bcd/wine; skipped without it): on a copy of vkd3d-shader's hlsl.l the patch
    replaces the one keyword rule by a keyword rule that needs white space and a letter or underscore after `point`, and a name rule
    whose action is the {IDENTIFIER} rule's (without its ConstantBuffer test); a second run changes nothing; a moved rule refuses it;
    flex turns the patched lexer into a scanner with no warning;
  - the compiler itself (the Mac: a C compiler, flex, and the parser files of the full tree's i386 build): vkd3d-shader is built twice on
    the host, with the original and the patched lexer, and eleven shaders are compiled with both: `point` as a variable, a parameter, a
    vertex input, a struct member, in Shader Model 2, 3 and 4, and before "=" on the next line, compile only with the patch; the
    geometry-shader keyword (space, new line, a modifier after it), identifiers that start with "point" and a shader without the word
    give the same bytes with both;
  - the auto-tune rule names the error and the fixture is there.
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


spec = importlib.util.spec_from_file_location("pointident", root / "tools/patch-wine-vkd3d-point-ident.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine vkd3d-shader to read point as a name\n")
check("CI runs this check and applies the patch before the i386 farm is built",
      "python3 tests/host/check-vkd3d-point-ident.py\n          python3 tools/patch-wine-vkd3d-point-ident.py wine\n" in wf[step:step + 400]
      and step < wf.index("      - name: Build i386 farm (WoW64)\n"))
rules = (root / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "hlsl-point-keyword-name"'):]
rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule names the error (fixed in 630) and its fixture is there",
      '"fixedIn": 630' in rb and 's("unexpected KW_POINT")' in rb
      and (root / "tests/fixtures/autotune/stalker-clear-sky-hlsl-point-2026-10-30.expect").is_file())

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    print("PASS: `point` is a name unless a type follows it" if ok else "FAILED: vkd3d point-ident")
    sys.exit(0 if ok else 1)

lex = (wine / P.FILE).read_text()
orig_lex = lex
if P.MARKER in lex:
    print("note: the Wine tree is already patched; the original lexer is rebuilt from it for the comparison")
    orig_lex = lex.replace(P.NEW, P.OLD, 1)
with tempfile.TemporaryDirectory(prefix="madeira-vkd3d-point-") as tmp:
    tmp = Path(tmp)
    tree = tmp / "w"
    (tree / Path(P.FILE).parent).mkdir(parents=True)
    t = tree / P.FILE
    t.write_text(orig_lex)
    script = str(root / "tools/patch-wine-vkd3d-point-ident.py")
    r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    patched = t.read_text()
    r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("it applies once; a second run says so and changes nothing",
          r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == patched and P.MARKER in patched,
          r1.stdout + r1.stderr + r2.stdout + r2.stderr)
    check("the keyword rule needs white space and a letter or underscore after `point`; the old rule is gone",
          "point/({WS}|{NEWLINE})+[A-Za-z_] {return KW_POINT;      }\n" in patched and P.OLD not in patched
          and patched.index("point/({WS}") < patched.index("point                   {\n") < patched.index("{IDENTIFIER}            {"))
    ident = patched[patched.index("{IDENTIFIER}            {\n"):]
    ident = ident[:ident.index("\n                        }\n")]
    name = patched[patched.index("point                   {\n"):]
    name = name[:name.index("\n                        }\n")]
    strip = lambda block: [l.strip() for l in block.splitlines()[1:] if l.strip() and not l.strip().startswith(("/*", "*"))]
    ident_body = [l for l in strip(ident) if "ConstantBuffer" not in l and l != "return KW_CONSTANTBUFFER;"]
    check("the name rule's action is the {IDENTIFIER} rule's, without its ConstantBuffer test", strip(name) == ident_body,
          (strip(name), ident_body))
    t.write_text(orig_lex.replace(P.OLD, P.OLD.replace("{return KW_POINT;", "{ return KW_POINT;"), 1))
    r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("a moved rule refuses the patch and leaves the file alone",
          r3.returncode != 0 and "keyword rule" in r3.stderr and P.MARKER not in t.read_text(), r3.stderr)

    flex = shutil.which("flex")
    cc = shutil.which("cc") or shutil.which("clang")
    gen_src = wine / "build-i386/libs/vkd3d/libs/vkd3d-shader"
    if not flex:
        print("skip: no flex here")
    else:
        (tmp / "o.l").write_text(orig_lex)
        (tmp / "p.l").write_text(patched)
        fo = subprocess.run([flex, "-o", str(tmp / "hlsl.yy.orig.c"), str(tmp / "o.l")], capture_output=True, text=True)
        fp = subprocess.run([flex, "-o", str(tmp / "hlsl.yy.patched.c"), str(tmp / "p.l")], capture_output=True, text=True)
        check("flex builds both scanners with no warning", fo.returncode == 0 and fp.returncode == 0 and not fo.stderr and not fp.stderr,
              fo.stderr + fp.stderr)
        need = ["hlsl.tab.c", "hlsl.tab.h", "preproc.tab.c", "preproc.tab.h", "preproc.yy.c"]
        if not cc or not all((gen_src / n).is_file() for n in need):
            print("skip: the compiler comparison needs a C compiler and the parser files of the full tree's i386 build "
                  "(wine/build-i386/libs/vkd3d/libs/vkd3d-shader)")
        else:
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
            harness.write_text(r'''
#include <stdio.h>
#include <string.h>
#include "vkd3d_shader.h"
static const char *cases[][2] = {
    {"ps_3_0", "float4 main(float4 p : TEXCOORD0) : COLOR { float3 point = p.xyz; return float4(point, 1); }"},
    {"ps_3_0", "float f(float3 point) { return point.x + point.y; }\nfloat4 main(float4 p : TEXCOORD0) : COLOR { return f(p.xyz).xxxx; }"},
    {"vs_3_0", "float4 main(float4 point : POSITION) : POSITION { return point * 2; }"},
    {"ps_2_0", "struct V { float3 point : TEXCOORD0; };\nfloat4 main(V v) : COLOR { return float4(v.point, 1); }"},
    {"gs_4_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(1)] void main(point S i[1], inout PointStream<S> o) { o.Append(i[0]); }"},
    {"gs_4_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(1)] void main(point\n   S i[1], inout PointStream<S> o) { o.Append(i[0]); }"},
    {"gs_5_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(1)] void main(point in S i[1], inout PointStream<S> o) { o.Append(i[0]); }"},
    {"ps_3_0", "float4 main(float4 p : TEXCOORD0) : COLOR { float points = p.x; float pointer = p.y; float point_x = p.z; float pointA = p.w; return float4(points, pointer, point_x, pointA); }"},
    {"ps_4_0", "float4 main(float4 p : TEXCOORD0) : SV_Target { float3 point = p.xyz; return float4(point, 1); }"},
    {"ps_3_0", "sampler2D s; float4 main(float2 t : TEXCOORD0) : COLOR { return tex2D(s, t) * 0.5; }"},
    {"ps_3_0", "float4 main(float4 p : TEXCOORD0) : COLOR { float3 point\n   = p.xyz; return float4(point , 1); }"},
};
int main(void)
{
    for (unsigned int n = 0; n < sizeof(cases) / sizeof(cases[0]); ++n)
    {
        struct vkd3d_shader_hlsl_source_info hlsl = {0};
        struct vkd3d_shader_compile_info info = {0};
        struct vkd3d_shader_code out = {0};
        char *messages = NULL;
        unsigned long h = 5381;
        int ret;
        hlsl.type = VKD3D_SHADER_STRUCTURE_TYPE_HLSL_SOURCE_INFO;
        hlsl.profile = cases[n][0];
        hlsl.entry_point = "main";
        info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
        info.next = &hlsl;
        info.source.code = cases[n][1];
        info.source.size = strlen(cases[n][1]);
        info.source_type = VKD3D_SHADER_SOURCE_HLSL;
        info.target_type = cases[n][0][3] >= '4' ? VKD3D_SHADER_TARGET_DXBC_TPF : VKD3D_SHADER_TARGET_D3D_BYTECODE;
        info.log_level = VKD3D_SHADER_LOG_ERROR;
        ret = vkd3d_shader_compile(&info, &out, &messages);
        for (size_t i = 0; i < out.size; ++i) h = h * 33 + ((const unsigned char *)out.code)[i];
        printf("%u %d %zu %08lx\n", n, ret, out.size, h & 0xffffffffUL);
        vkd3d_shader_free_messages(messages);
        vkd3d_shader_free_shader_code(&out);
    }
    return 0;
}
''')
            cflags = ["-std=gnu99", "-O1", "-w", "-include", str(inc / "config.h"), "-I" + str(V / "include"), "-I" + str(V / "include/private"),
                      "-I" + str(V / "libs/vkd3d-shader"), "-I" + str(gen_src), "-I" + str(inc), "-DLIBVKD3D_SHADER_SOURCE"]
            common = sorted((V / "libs/vkd3d-shader").glob("*.c")) + [V / "libs/vkd3d-common" / n for n in ("debug.c", "error.c", "memory.c", "utf8.c")]
            common += [gen_src / "hlsl.tab.c", gen_src / "preproc.tab.c", gen_src / "preproc.yy.c"]
            odir = tmp / "obj"
            odir.mkdir()

            def compile_one(src):
                o = odir / (src.stem + "-" + str(abs(hash(str(src))) % 10**8) + ".o")
                r = subprocess.run([cc] + cflags + ["-c", str(src), "-o", str(o)], capture_output=True, text=True)
                return src, o, r

            with ThreadPoolExecutor(max(2, os.cpu_count() or 2)) as ex:
                built = list(ex.map(compile_one, common + [tmp / "hlsl.yy.orig.c", tmp / "hlsl.yy.patched.c", harness]))
            bad = [(str(s), r.stderr[-400:]) for s, o, r in built if r.returncode != 0]
            check("vkd3d-shader builds on the host with both lexers", not bad, bad[:2])
            if not bad:
                objs = {s: o for s, o, r in built}
                base = [str(objs[s]) for s in common] + [str(objs[harness])]
                exes = {}
                for which in ("orig", "patched"):
                    exe = tmp / ("vkd3d-" + which)
                    r = subprocess.run([cc, "-o", str(exe)] + base + [str(objs[tmp / ("hlsl.yy." + which + ".c")])], capture_output=True, text=True)
                    exes[which] = exe if r.returncode == 0 else None
                check("both compilers link", all(exes.values()))
                if all(exes.values()):
                    res = {w: [l.split() for l in subprocess.run([str(e)], capture_output=True, text=True).stdout.splitlines()]
                           for w, e in exes.items()}
                    o, p = res["orig"], res["patched"]
                    names = (0, 1, 2, 3, 8, 10)
                    check("`point` as a variable, parameter, input, member (SM2/3/4) and before \"=\" compiles only with the patch",
                          all(o[n][1] != "0" and p[n][1] == "0" for n in names), [(o[n], p[n]) for n in names])
                    same = (4, 5, 6, 7, 9)
                    check("the geometry-shader keyword, identifiers that start with point and a plain shader: the same bytes",
                          all(o[n][1] == "0" and o[n] == p[n] for n in same), [(o[n], p[n]) for n in same])

print("PASS: `point` is a name unless a type follows it" if ok else "FAILED: vkd3d point-ident")
sys.exit(0 if ok else 1)
