#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-vkd3d-keyword-names.py: six more words Direct3D 9 allows as names are names in Wine's HLSL compiler (night round 2026-11-11, G11 S2).

`line`, `lineadj`, `triangle`, `triangleadj`, `linear` and `centroid` as a variable name fail today with "E5000: syntax error, unexpected KW_<WORD>"
(vkd3d reserves the Shader Model 4/5 words); Microsoft's Direct3D 9 compiler does not. Round 630's `point` patch did the same for one word.
Checks:
  - CI applies the patch (after the `point` patch) before the i386 farm is built and runs this check;
  - with a Wine tree (wine/ of this tree, WINE_SRC or ~/madeira-bcd/wine; skipped without it): on a copy of vkd3d-shader's hlsl.l the patch
    replaces the six keyword rules by a keyword rule that needs white space and a letter or underscore after the word and a name rule whose action
    is the {IDENTIFIER} rule's (without its ConstantBuffer test); a second run changes nothing; it works with and without the `point` patch applied
    first; a moved rule refuses it and leaves the file alone; flex turns the patched lexer into a scanner with no warning;
  - the compiler itself (the Mac: a C compiler, flex, and the parser files of the full tree's i386 build): vkd3d-shader is built twice on the
    host, with the lexer before and after this patch, and shaders are compiled with both: each of the six words as a variable, a parameter, a vertex
    input, a struct member and before "=" on the next line (Shader Model 2, 3 and 4) compiles only with the patch; the geometry-shader keywords
    (space, new line), the interpolation modifiers, identifiers that start with the words and a plain shader give the same bytes with both.
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


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, root / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = load("keywordnames", "tools/patch-wine-vkd3d-keyword-names.py")
PT = load("pointident", "tools/patch-wine-vkd3d-point-ident.py")

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine vkd3d-shader to read more HLSL keywords as names\n")
check("CI runs this check and applies the patch after the point patch and before the i386 farm is built",
      "python3 tests/host/check-vkd3d-keyword-names.py\n          python3 tools/patch-wine-vkd3d-keyword-names.py wine\n" in wf[step:step + 500]
      and wf.index("      - name: Patch wine vkd3d-shader to read point as a name\n") < step < wf.index("      - name: Build i386 farm (WoW64)\n"))

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    print("PASS: six more words are names in the HLSL compiler" if ok else "FAILED: vkd3d keyword names")
    sys.exit(0 if ok else 1)

lex = (wine / P.FILE).read_text()
if P.MARKER in lex:
    print("note: the Wine tree is already patched with this patch; only its static checks run")
    check("the patched lexer has the six name rules", lex.count(P.MARKER) == len(P.WORDS))
    print("PASS: six more words are names in the HLSL compiler" if ok else "FAILED: vkd3d keyword names")
    sys.exit(0 if ok else 1)
with tempfile.TemporaryDirectory(prefix="madeira-vkd3d-kwnames-") as tmp:
    tmp = Path(tmp)
    script = str(root / "tools/patch-wine-vkd3d-keyword-names.py")
    pscript = str(root / "tools/patch-wine-vkd3d-point-ident.py")
    tree = tmp / "w"
    (tree / Path(P.FILE).parent).mkdir(parents=True)
    t = tree / P.FILE
    # the order of the CI: the point patch, then this one; the "before" lexer is the first of the two
    t.write_text(lex)
    subprocess.run([sys.executable, pscript, str(tree)], capture_output=True, text=True)
    orig_lex = t.read_text()
    r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    patched = t.read_text()
    r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("it applies once; a second run says so and changes nothing",
          r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == patched and patched.count(P.MARKER) == 6,
          r1.stdout + r1.stderr + r2.stdout + r2.stderr)
    for w, tok in P.WORDS:
        check("`%s`: the keyword needs white space and a letter or underscore after it; the old rule is gone; the name rule comes before {IDENTIFIER}" % w,
              ("%s/({WS}|{NEWLINE})+[A-Za-z_] {return %s;}\n" % (w, tok)) in patched and P.old_rule(w, tok) not in patched
              and patched.index("%s/({WS}" % w) < patched.index("%-24s{\n" % w) < patched.index("{IDENTIFIER}            {"))
    ident = patched[patched.index("{IDENTIFIER}            {\n"):]
    ident = ident[:ident.index("\n                        }\n")]
    strip = lambda block: [l.strip() for l in block.splitlines()[1:] if l.strip() and not l.strip().startswith(("/*", "*"))]
    ident_body = [l for l in strip(ident) if "ConstantBuffer" not in l and l != "return KW_CONSTANTBUFFER;"]
    bodies = []
    for w, tok in P.WORDS:
        blk = patched[patched.index("%-24s{\n" % w):]
        bodies.append(strip(blk[:blk.index("\n                        }\n")]))
    check("every name rule's action is the {IDENTIFIER} rule's, without its ConstantBuffer test", all(b == ident_body for b in bodies), (bodies[0], ident_body))
    # either order with the point patch works
    t.write_text(lex)
    subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    r_a = subprocess.run([sys.executable, pscript, str(tree)], capture_output=True, text=True)
    check("applied before the point patch, both are in (the order does not matter)", r_a.returncode == 0 and t.read_text().count(P.MARKER) == 6
          and PT.MARKER in t.read_text(), r_a.stdout + r_a.stderr)
    t.write_text(orig_lex.replace(P.old_rule("line", "KW_LINE"), P.old_rule("line", "KW_LINE").replace("{return", "{ return"), 1))
    r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("a moved rule refuses the patch and leaves the file alone",
          r3.returncode != 0 and "`line` keyword rule" in r3.stderr and P.MARKER not in t.read_text(), r3.stderr)

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
            names = ["line", "triangle", "lineadj", "triangleadj", "linear", "centroid"]
            cases = []   # (profile, source)
            for w in names:
                cases.append(("ps_3_0", "float4 main(float4 p : TEXCOORD0) : COLOR { float3 %s = p.xyz; %s.x += 1; return float4(%s, 1); }" % (w, w, w)))
            cases += [
                ("ps_3_0", "float f(float3 line) { return line.x + line.y; }\nfloat4 main(float4 p : TEXCOORD0) : COLOR { return f(p.xyz).xxxx; }"),
                ("ps_2_0", "struct V { float3 triangle : TEXCOORD0; };\nfloat4 main(V v) : COLOR { return float4(v.triangle, 1); }"),
                ("vs_3_0", "float4 main(float4 linear : POSITION) : POSITION { return linear * 2; }"),
                ("ps_4_0", "float4 main(float4 p : TEXCOORD0) : SV_Target { float3 centroid = p.xyz; return float4(centroid, 1); }"),
                ("ps_3_0", "float4 main(float4 p : TEXCOORD0) : COLOR { float3 lineadj\n   = p.xyz; return float4(lineadj , 1); }"),
                ("ps_4_0", "float4 main(float4 p : TEXCOORD0) : SV_Target { float4 triangleadj = p; return triangleadj; }"),
            ]
            n_names = len(cases)
            cases += [
                ("gs_4_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(2)] void main(line S i[2], inout LineStream<S> o) { o.Append(i[0]); o.Append(i[1]); }"),
                ("gs_4_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(3)] void main(triangle S i[3], inout TriangleStream<S> o) { o.Append(i[0]); o.Append(i[1]); o.Append(i[2]); }"),
                ("gs_4_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(4)] void main(lineadj S i[4], inout LineStream<S> o) { o.Append(i[1]); o.Append(i[2]); }"),
                ("gs_4_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(3)] void main(triangleadj S i[6], inout TriangleStream<S> o) { o.Append(i[0]); o.Append(i[2]); o.Append(i[4]); }"),
                ("gs_4_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(3)] void main(triangle\n  S i[3], inout TriangleStream<S> o) { o.Append(i[0]); }"),
                ("gs_5_0", "struct S { float4 p : SV_POSITION; };\n[maxvertexcount(2)] void main(line in S i[2], inout LineStream<S> o) { o.Append(i[0]); }"),
                ("ps_4_0", "struct I { linear float4 a : TEXCOORD0; centroid float4 b : TEXCOORD1; noperspective float4 c : TEXCOORD2; };\nfloat4 main(I i) : SV_Target { return i.a + i.b + i.c; }"),
                ("ps_4_0", "struct I { linear\n  float4 a : TEXCOORD0; centroid\tfloat4 b : TEXCOORD1; };\nfloat4 main(I i) : SV_Target { return i.a + i.b; }"),
                ("ps_3_0", "float4 main(float4 p : TEXCOORD0) : COLOR { float lines = p.x; float linear_x = p.y; float trianglefan = p.z; float centroids = p.w; float lineadjacent = 1; return float4(lines, linear_x, trianglefan, centroids + lineadjacent); }"),
                ("ps_3_0", "sampler2D s; float4 main(float2 t : TEXCOORD0) : COLOR { return tex2D(s, t) * 0.5; }"),
            ]
            c_array = ",\n".join('    {"%s", "%s"}' % (pr, s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")) for pr, s in cases)
            harness.write_text(r"""
#include <stdio.h>
#include <string.h>
#include "vkd3d_shader.h"
static const char *cases[][2] = {
""" + c_array + r"""
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
""")
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
                    check("every case compiled in both", len(o) == len(cases) and len(p) == len(cases), (len(o), len(p)))
                    names = range(n_names)
                    check("each of the six words as a variable, parameter, vertex input, member and before \"=\" (SM2/3/4) compiles only with the patch",
                          all(o[n][1] != "0" and p[n][1] == "0" for n in names), [(cases[n][1][:60], o[n], p[n]) for n in names if not (o[n][1] != "0" and p[n][1] == "0")])
                    same = range(n_names, len(cases))
                    check("the geometry-shader keywords (space, new line, `in`), the interpolation modifiers, identifiers that start with the words and a plain "
                          "shader: the same bytes", all(o[n][1] == "0" and o[n] == p[n] for n in same),
                          [(cases[n][1][:60], o[n], p[n]) for n in same if not (o[n][1] == "0" and o[n] == p[n])])

print("PASS: six more words are names in the HLSL compiler" if ok else "FAILED: vkd3d keyword names")
sys.exit(0 if ok else 1)
