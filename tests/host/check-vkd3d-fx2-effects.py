#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# Portions copyright 2026 125hz: code taken from https://github.com/willfaust/Madeira (pull request #575, tests/host/check-fx2-effects.py)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""The effect writer of the Wine tree's vkd3d-shader: the fx_2_0 writer of Wine fork commits 8be7412f882 + ddf92babc5b by 125hz
(willfaust/wine#71, Madeira #575: sampler initializers, pass assignments and the resource records d3dx9 reads them from) and BCD's
compile-state reset on top (every shader of an effect compiled from the state a fresh context has). Keyless: it acts on effects
that failed to compile before (fx_2_0) or compiled to wrong shaders (fx_4_0 / fx_5_0); MADEIRA_FX_WRITER=0 gives back the writer
before, byte for byte.

An effect compiled from text at run time (D3DXCreateEffect on a .fx source) goes Wine's d3dx9 -> d3dcompiler -> vkd3d-shader, target
fx_2_0. Release 1.19 stopped at the first sampler with a state block ("E5017: Aborting due to not yet implemented feature: Writing
fx_2_0 sampler objects initializers is not implemented") and at every pass with assignments ("Write pass assignments"): AstroTripper
(32-bit, Send Log 41T6YN, build 653) got no effect and called GetParameterByName on the NULL effect 0.2 s later. Every shader of an
effect is compiled in the one context the effect was parsed in, and the code generator keeps state between compiles: a later fx_2_0
shader carried an earlier one's literal `def`s over its own constants, and every fx_4_0 / fx_5_0 shader, the first too, read its
constant buffers behind the layout the effect binds. vkd3d-shader is linked into wined3d.dll, which the d3dcompiler modules import;
the i386 farm builds it from this tree, the 64-bit wined3d.dll is a shipped file and does not change.

Always (the source):
  - CI runs this check; the auto-tune rule names the two refusals (fixed in 660) on BCD's 32-bit compile diagnostic, is silent with
    MADEIRA_FX_WRITER=0, and its fixture is there;
  - fx.c has the writer's pieces, the old refusals only behind fx_writer_enabled() (MADEIRA_FX_WRITER, "0" exactly, read once), the
    reset before every effect shader's compile, D3D bytecode only inside an fx_2_0 effect; write_fx_2_parameters walks the uniform
    globals (a compile rebuilds the externs list) without the variables a compile adds; the trailer writes the record counts.
With a C compiler, bison 3 or later, flex and Wine's generated headers (wine/build-native/include, $WINE_GEN_INCLUDE or
~/madeira-bcd/wine/build-native/include), the tree's vkd3d-shader is built for the host with BCD's build-time vkd3d patches applied
(the lexer and grammar the i386 farm builds), and a driver compiles HLSL the way D3DCompile2VKD3D does:
  - upstream's sample effect and an AstroTripper-like one (six sampler state blocks, pass shaders, named and boolean states) compile
    to fx_2_0 and read back the way dlls/d3dx9_36/effect.c reads them: states follow their counts, Texture = <t> is an object whose
    resource record names the parameter, each pass shader is an object whose resource record carries SM3 bytecode, a vector state
    is a column of its components, object ids are unique and the records end at the file's end;
  - every shader of every effect of a corpus (fx_2_0: the two above, two techniques sharing a vertex shader, literals and uniforms
    that collided, sin/cos, separate texture and sampler objects, shader parameters, reserved registers; fx_4_0 / fx_5_0: constant
    buffers, globals, textures, two buffers, shader variables, a geometry shader, a legacy sampler) is byte for byte the same
    function compiled alone with the same profile, and the same tree without the reset gets some of them wrong (the check bites);
  - MADEIRA_FX_WRITER=0: the two effects are refused with the two E5017 messages; other values keep the writer on;
  - when the tree's git history has the writer before the change: that writer refuses both effects with the two E5017 messages, and
    with MADEIRA_FX_WRITER=0 the tree gives the same bytes or the same messages for every input; with the writer on, every effect
    and shader of a corpus the old writer compiled comes out byte for byte the same, except the documented changes: a numeric
    fx_2_0 parameter without an initializer points at zeros (fxc's output) instead of value offset 0 (the first type record), and an
    fx_4_0 / fx_5_0 effect's shaders are the stand-alone ones while the rest of the effect (vkd3d's own effect parser prints it) is
    unchanged.
"""
from pathlib import Path
import concurrent.futures
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
REL = "libs/vkd3d/libs/vkd3d-shader/fx.c"
failures = []


def check(what, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:2500]) if detail and not cond else ""))
    if not cond:
        failures.append(what)


def finish():
    print("FAILED: vkd3d fx_2_0 effects" if failures else "PASS: vkd3d fx_2_0 effects")
    sys.exit(1 if failures else 0)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.find("      - name: Check the fx_2_0 effect writer\n")
check("CI runs this check, before the i386 farm is built",
      0 <= step < wf.index("      - name: Build i386 farm (WoW64)\n") and "python3 tests/host/check-vkd3d-fx2-effects.py" in wf[step:step + 200])
rules = (root / "build/tools/autotune_rules.py").read_text()
rb = ""
if '"id": "hlsl-fx2-effect-writer"' in rules:
    rb = rules[rules.index('"id": "hlsl-fx2-effect-writer"'):]
    rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule names the two refusals (fixed in 660) on BCD's 32-bit compile diagnostic, is silent with "
      "MADEIRA_FX_WRITER=0, and its fixture is there",
      '"fixedIn": 660' in rb and 's("madeira_compile_diag", "Writing fx_2_0 sampler objects initializers is not implemented")' in rb
      and 's("madeira_compile_diag", "Aborting due to not yet implemented feature: Write pass assignments")' in rb
      and '"none": [s("MADEIRA_FX_WRITER=0")]' in rb
      and (root / "tests/fixtures/autotune/astrotripper-fx2-effect-2026-11-11.expect").is_file(), rb[:400])
catalog = (root / "build/tools/gen-config-catalog.py").read_text()
ent = catalog[catalog.index('"env.MADEIRA_FX_WRITER"'):] if '"env.MADEIRA_FX_WRITER"' in catalog else ""
ent = ent[:ent.index("},\n") + 2] if ent else ""
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the switch: a catalog entry (Wine libraries, on by default), a list may set it to 0 or 1, the session end drops it",
      '"category": "Wine libraries"' in ent and '"kind": "bool"' in ent and '"default": "1"' in ent
      and '"wine/libs/vkd3d/libs/vkd3d-shader/fx.c"' in ent
      and '"MADEIRA_FX_WRITER",' in pol.split("static let allowedEnvNames")[1].split("]\n")[0]
      and 'throw RecommendationsError.policy("\\(path): MADEIRA_FX_WRITER is 0 or 1")' in pol
      and 'unsetenv("MADEIRA_FX_WRITER");' in (root / "app/Madeira/WineProcessBridge.m").read_text(), ent[:300])

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / REL).is_file()), None)
if not wine:
    print("SKIP: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine)")
    finish()
src = (wine / REL).read_text(encoding="utf-8", errors="replace")

# ---- the source (upstream's structural part and BCD's)
for refusal in ("Writing fx_2_0 state assignments is not implemented", "Writing fx_2_0 shader objects initializers is not implemented"):
    check("fx.c no longer has: " + refusal, refusal not in src)
for piece in ("static uint32_t write_fx_2_sampler_initializer(", "static void write_fx_2_resource(", "static uint32_t write_fx_2_object_type(",
              "static bool compile_shader(", "struct vkd3d_bytecode_buffer *fx_2_state_buffer;", "fx_2_bool_values[]",
              "static void reset_effect_compile_state(struct hlsl_ctx *ctx)", "static bool fx_writer_enabled(void)"):
    check("fx.c has " + piece, piece in src)
sw = src[src.index("static bool fx_writer_enabled(void)"):] if "static bool fx_writer_enabled(void)" in src else ""
sw = sw[:sw.index("\n}\n")] if sw else ""
check("the switch reads MADEIRA_FX_WRITER once per process; only \"0\" turns the writer off",
      "static int enabled = -1;" in sw and 'getenv("MADEIRA_FX_WRITER")' in sw
      and "enabled = !(value && value[0] == '0' && !value[1]);" in sw and src.count('"MADEIRA_FX_WRITER"') == 1, sw)
# The writer before, behind the switch: its three refusals, each once, and offset 0 for a value without an initializer.
gates = {
    "Write pass assignments.": "    if (!fx_writer_enabled())\n    {\n        if (var->state_block_count && var->state_blocks[0]->count)\n"
                               "            hlsl_fixme(fx->ctx, &var->loc, \"Write pass assignments.\");",
    "Writing fx_2_0 sampler objects initializers is not implemented.": "        if (fx_writer_enabled())\n            return write_fx_2_sampler_initializer(var, fx);\n"
                               "        for (i = 0; i < elements_count; ++i)\n            hlsl_fixme(fx->ctx, &var->loc, \"Writing fx_2_0 sampler objects initializers is not implemented.\");",
    "Write fx 2.0 parameter class %#x.": "            if (fx_writer_enabled())\n                return true;\n"
                               "            hlsl_fixme(ctx, loc, \"Write fx 2.0 parameter class %#x.\", type->class);",
    "offset 0": "        if (!fx_writer_enabled())\n            return 0;\n        /* A parameter without an initializer gets zeros",
}
for what, piece in gates.items():
    check(f"the writer before, behind the switch: {what}", src.count(piece) == 1 and (what == "offset 0" or src.count(what) == 1))
reset = src[src.index("static void reset_effect_compile_state(struct hlsl_ctx *ctx)"):] if "static void reset_effect_compile_state(" in src else ""
reset = reset[:reset.index("\n}\n")] if reset else ""
check("the reset gives back what a fresh context has: constant defs, sin/cos constants, counters, the variables' registers, bind "
      "counts, use flags and object usage, the buffers' layout, and moves the lowering's globals out",
      all(x in reset for x in ("ctx->constant_defs.count = 0;", "memset(&ctx->d3dsincosconst1, 0,", "memset(&ctx->d3dsincosconst2, 0,",
                               "ctx->ssa_count = 0;", "ctx->temp_count = 0;", "ctx->indexable_temp_count = 0;",
                               "LIST_FOR_EACH_ENTRY(scope, &ctx->scopes, struct hlsl_scope, entry)", "memset(var->regs, 0, sizeof(var->regs));",
                               "memset(var->bind_count, 0, sizeof(var->bind_count));", "var->is_read = false;",
                               "memset(var->objects_usage[k], 0, var->data_type->reg_size[k] * sizeof(*var->objects_usage[k]));",
                               "buffer->size = 0;", "buffer->used_size = 0;", "memset(&buffer->reg, 0, sizeof(buffer->reg));",
                               "if (!var->is_synthetic)", "list_add_tail(&ctx->dummy_scope->vars, &var->scope_entry);")), reset[:300])
comp = src[src.index("static bool compile_shader("):] if "static bool compile_shader(" in src else ""
comp = comp[:comp.index("\n}\n")] if comp else ""
EVERY = "    if (fx_writer_enabled())\n        reset_effect_compile_state(ctx);\n"
FX2_ONLY = "    if (fx_writer_enabled() && ctx->profile->major_version == 2)\n        reset_effect_compile_state(ctx);\n"
reset_every = EVERY in comp
check("compile_shader resets before hlsl_emit_vsir (every effect's shaders, or only fx_2_0's), and writes D3D bytecode only inside "
      "an fx_2_0 effect", src.count("reset_effect_compile_state(ctx);") == 1 and (reset_every or FX2_ONLY in comp)
      and 0 < comp.find("reset_effect_compile_state(ctx);") < comp.find("ret = hlsl_emit_vsir(")
      and "bool d3dbc = fx->ctx->profile->major_version == 2 && compile->profile->major_version < 4;" in comp, comp[:400])
if "static void write_fx_2_state_assignment(" in src and "static void write_fx_2_parameters(" in src and "static int hlsl_fx_2_write(" in src:
    assign = src[src.index("static void write_fx_2_state_assignment("):]
    assign = assign[:assign.index("\n}\n")]
    check("write_fx_2_state_assignment writes constants, compiled shaders and <parameter> references",
          all(c in assign for c in ("case HLSL_IR_CONSTANT:", "case HLSL_IR_COMPILE:", "case HLSL_IR_LOAD:")))
    params = src[src.index("static void write_fx_2_parameters("):]
    params = params[:params.index("\n}\n")]
    check("write_fx_2_parameters walks the uniform globals, not the externs list, without the variables a compile adds",
          "&ctx->globals->vars" in params and "extern_vars" not in params and "HLSL_STORAGE_UNIFORM" in params
          and "if (var->is_synthetic)\n            continue;" in params)
    trailer = src[src.index("static int hlsl_fx_2_write("):]
    trailer = trailer[:trailer.index("\n}\n")]
    check("hlsl_fx_2_write writes the object record count, the resource count and the resource records",
          "fx.object_record_count" in trailer and "fx.resource_count" in trailer and "fx.resources.data" in trailer)
else:
    check("fx.c has the fx_2_0 writer's functions", False)


# ---- the host build
def find_bison():
    for b in ("/usr/local/opt/bison/bin/bison", "/opt/homebrew/opt/bison/bin/bison", shutil.which("bison") or ""):
        if not b or not Path(b).is_file():
            continue
        v = subprocess.run([b, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        m = re.search(r"(\d+)\.(\d+)", v)
        if m and int(m.group(1)) >= 3:
            return b
    return None


cc = os.environ.get("CC") or shutil.which("clang") or shutil.which("cc")
bison, flex = find_bison(), shutil.which("flex")
gen = next((g for g in ([Path(os.environ["WINE_GEN_INCLUDE"])] if os.environ.get("WINE_GEN_INCLUDE") else []) +
            [wine / "build-native/include", Path.home() / "madeira-bcd/wine/build-native/include"]
            if (g / "d3dcommon.h").is_file() and (g / "objidlbase.h").is_file()), None)
if not (cc and bison and flex and gen):
    print("SKIP: the compiled part needs a C compiler, bison 3+, flex and Wine's generated headers (cc=%s bison=%s flex=%s headers=%s)"
          % (bool(cc), bool(bison), bool(flex), bool(gen)))
    finish()

# BCD's build-time vkd3d patches, in the workflow's order (the farm builds the lexer and grammar they write).
vk_scripts = re.findall(r"python3 (tools/patch-wine-vkd3d-[\w-]+\.py) wine", wf)
check("the workflow's vkd3d patch scripts are found", len(vk_scripts) >= 3, vk_scripts)


def old_fx():
    """fx.c before the writer's change, from the tree's history; None in a shallow checkout."""
    if not (wine / ".git").exists():
        return None
    log = subprocess.run(["git", "-C", str(wine), "log", "--format=%H", "-n1", "--fixed-strings",
                          "--grep=vkd3d-shader/fx: Write fx_2_0 sampler initializers, pass assignments", "--", REL],
                         capture_output=True, text=True)
    h = log.stdout.strip()
    if log.returncode or not h:
        return None
    r = subprocess.run(["git", "-C", str(wine), "show", f"{h}^:{REL}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "vkd3d_common.h"   /* VKD3D_SHADER_API_VERSION_CURRENT, as vkd3d-utils gets it */
#include "vkd3d_shader.h"
/* fxc PROFILE IN OUT [ENTRY|-]: the options D3DCompile2VKD3D sets without D3DCOMPILE_DEBUG (vkd3d-utils/vkd3d_utils_main.c).
 * fxc dump IN OUT: vkd3d's own effect parser prints the effect IN as text (vkd3d-compiler -x fx -b d3d-asm). */
static enum vkd3d_shader_target_type target_for(const char *p)
{
    if (!strncmp(p, "fx_", 3)) return VKD3D_SHADER_TARGET_FX;
    if ((p[0] == 'v' || p[0] == 'p') && p[1] == 's' && p[2] == '_' && p[3] >= '1' && p[3] <= '3') return VKD3D_SHADER_TARGET_D3D_BYTECODE;
    return VKD3D_SHADER_TARGET_DXBC_TPF;
}
int main(int argc, char **argv)
{
    struct vkd3d_shader_compile_option options[2] = {
        {VKD3D_SHADER_COMPILE_OPTION_API_VERSION, VKD3D_SHADER_API_VERSION_CURRENT},
        {VKD3D_SHADER_COMPILE_OPTION_STRIP_DEBUG, 1},
    };
    struct vkd3d_shader_hlsl_source_info hlsl = {0};
    struct vkd3d_shader_compile_info info = {0};
    struct vkd3d_shader_code out = {0};
    char *messages = NULL, *data;
    long size;
    FILE *f;
    int ret;
    if (argc < 4 || !(f = fopen(argv[2], "rb"))) return 2;
    fseek(f, 0, SEEK_END); size = ftell(f); fseek(f, 0, SEEK_SET);
    if (!(data = malloc(size + 1)) || fread(data, 1, size, f) != (size_t)size) return 2;
    fclose(f);
    info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
    info.source.code = data;
    info.source.size = size;
    info.options = options;
    if (!strcmp(argv[1], "dump"))
    {
        info.source_type = VKD3D_SHADER_SOURCE_FX;
        info.target_type = VKD3D_SHADER_TARGET_D3D_ASM;
        info.option_count = 1;
    }
    else
    {
        hlsl.type = VKD3D_SHADER_STRUCTURE_TYPE_HLSL_SOURCE_INFO;
        hlsl.profile = argv[1];
        hlsl.entry_point = argc > 4 && strcmp(argv[4], "-") ? argv[4] : NULL;
        info.next = &hlsl;
        info.source_type = VKD3D_SHADER_SOURCE_HLSL;
        info.target_type = target_for(argv[1]);
        info.option_count = 2;
    }
    info.log_level = VKD3D_SHADER_LOG_INFO;
    info.source_name = "<anonymous>";
    ret = vkd3d_shader_compile(&info, &out, &messages);
    if (messages && *messages) fputs(messages, stderr);
    vkd3d_shader_free_messages(messages);
    if (ret < 0) { fprintf(stderr, "vkd3d_shader_compile: %d\n", ret); return 1; }
    if (!(f = fopen(argv[3], "wb"))) return 2;
    fwrite(out.code, 1, out.size, f);
    fclose(f);
    vkd3d_shader_free_shader_code(&out);
    return 0;
}
'''


def build(tmp):
    """Builds fxc-new (this tree), fxc-noreset (this tree without the compile-state reset: what the check must catch) and fxc-old
    (when the history has the old writer). Returns {name: path}."""
    before = len(failures)
    vk = tmp / "w/libs/vkd3d"
    shutil.copytree(wine / "libs/vkd3d", vk)
    for sc in vk_scripts:
        r = subprocess.run([sys.executable, str(root / sc), str(tmp / "w")], capture_output=True, text=True)
        check(f"{Path(sc).name} applies to (or is already in) the copy", r.returncode == 0, r.stdout[-400:] + r.stderr[-400:])
    sh = vk / "libs/vkd3d-shader"
    o = tmp / "obj"
    o.mkdir()
    for base, kind in (("hlsl", "y"), ("preproc", "y")):
        r = subprocess.run([bison, "-o", str(o / f"{base}.tab.c"), f"--defines={o / f'{base}.tab.h'}", str(sh / f"{base}.y")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        check(f"bison generates {base}.tab.c", r.returncode == 0, r.stderr[-800:])
    for base in ("hlsl", "preproc"):
        r = subprocess.run([flex, f"-o{o / f'{base}.yy.c'}", str(sh / f"{base}.l")], capture_output=True, text=True)
        check(f"flex generates {base}.yy.c", r.returncode == 0, r.stderr[-800:])
    if len(failures) > before:
        return {}
    cflags = ["-O1", "-w", f"-I{o}", f"-I{vk}", f"-I{vk / 'include'}", f"-I{vk / 'include/private'}", f"-I{sh}", f"-I{gen}",
              f"-I{wine / 'include'}", "-DWINE_NO_NAMELESS_EXTENSION", "-DLIBVKD3D_SHADER_SOURCE", "-D__WINESRC__", "-DWINE_UNIX_LIB",
              "-DHAVE_SYNC_ADD_AND_FETCH=1", "-DHAVE_SYNC_BOOL_COMPARE_AND_SWAP=1"]
    sources = sorted(p for p in sh.glob("*.c") if p.name != "fx.c")
    sources += [vk / "libs/vkd3d-common" / n for n in ("debug.c", "error.c", "memory.c", "utf8.c")]
    sources += [o / "hlsl.tab.c", o / "hlsl.yy.c", o / "preproc.tab.c", o / "preproc.yy.c"]
    units = [(s, o / (s.stem.replace(".", "_") + ".o")) for s in sources]
    old = old_fx()
    tree = (sh / "fx.c").read_text()
    (o / "fx-new.c").write_text(tree)
    units.append((o / "fx-new.c", o / "fx-new.o"))
    if tree.count("        reset_effect_compile_state(ctx);\n") == 1:
        (o / "fx-noreset.c").write_text(tree.replace("        reset_effect_compile_state(ctx);\n", "        (void)0;\n"))
        units.append((o / "fx-noreset.c", o / "fx-noreset.o"))
    if old is not None:
        (o / "fx-old.c").write_text(old)
        units.append((o / "fx-old.c", o / "fx-old.o"))
    (o / "fxc.c").write_text(DRIVER)
    units.append((o / "fxc.c", o / "fxc.o"))

    def compile_one(u):
        s, obj = u
        extra = [f"-I{sh}"] if s.parent == o else []
        r = subprocess.run([cc, *cflags, *extra, "-c", "-o", str(obj), str(s)], capture_output=True, text=True)
        return s.name, r.returncode, r.stderr[-1200:]

    with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("BCD_HOST_JOBS", "4"))) as ex:
        results = list(ex.map(compile_one, units))
    bad = [(n, e) for n, rc, e in results if rc]
    check(f"vkd3d-shader's {len(units)} units compile for the host", not bad, bad[:2])
    if bad:
        return {}
    common = [str(obj) for s, obj in units if obj.name not in ("fx-new.o", "fx-noreset.o", "fx-old.o")]
    out = {}
    for name in ("new", "noreset", "old"):
        if not (o / f"fx-{name}.o").is_file():
            continue
        exe = tmp / f"fxc-{name}"
        r = subprocess.run([cc, "-o", str(exe), *common, str(o / f"fx-{name}.o"), "-lm", "-lpthread"], capture_output=True, text=True)
        check(f"the {name} writer links", r.returncode == 0, r.stderr[-1200:])
        if r.returncode == 0:
            out[name] = exe
    return out


def compile_with(exe, profile, text, tmp, tag, entry="-", writer=None):
    """writer: the value of MADEIRA_FX_WRITER for this run (None: unset)."""
    s = tmp / f"{tag}.hlsl"
    s.write_text(text)
    out = tmp / f"{tag}.bin"
    if out.exists():
        out.unlink()
    env = {k: v for k, v in os.environ.items() if k != "MADEIRA_FX_WRITER"}
    if writer is not None:
        env["MADEIRA_FX_WRITER"] = writer
    r = subprocess.run([str(exe), profile, str(s), str(out), entry], capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, (out.read_bytes() if r.returncode == 0 and out.is_file() else None), r.stderr


def effect_text(exe, data, tmp, tag):
    """vkd3d's own effect parser's text of an effect, every shader body ("asm { ... }") folded to one line; None if it fails."""
    b = tmp / f"{tag}.fxbin"
    b.write_bytes(data)
    out = tmp / f"{tag}.txt"
    r = subprocess.run([str(exe), "dump", str(b), str(out)], capture_output=True, text=True, timeout=120)
    if r.returncode or not out.is_file():
        return None
    lines, skip = [], None
    for line in out.read_text().splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if skip is not None:
            if indent == skip and line.strip() in ("}", "};"):
                lines.append(" " * indent + "asm { ... }" + line.strip()[1:])
                skip = None
            continue
        if line.strip() == "asm {":
            skip = indent
            continue
        lines.append(line)
    return lines if skip is None else None


# ---- reading an fx_2_0 binary the way dlls/d3dx9_36/effect.c does (upstream's reader)
# d3dx9shader.h
PT_INT, PT_FLOAT, PT_TEXTURE, PT_SAMPLER, PT_SAMPLERCUBE, PT_PIXELSHADER, PT_VERTEXSHADER = 2, 3, 5, 10, 14, 15, 16
PC_MATRIX_ROWS, PC_OBJECT = 2, 4
# dlls/d3dx9_36/effect.c state_table indices
OP_ZENABLE, OP_ZWRITEENABLE, OP_SRCBLEND, OP_DESTBLEND, OP_CULLMODE, OP_ALPHABLENDENABLE, OP_MATERIALDIFFUSE = 0, 3, 6, 7, 8, 13, 127
OP_VERTEXSHADER, OP_PIXELSHADER, OP_TEXTURE, OP_ADDRESSU, OP_ADDRESSV, OP_MAGFILTER, OP_MINFILTER, OP_MIPFILTER = 146, 147, 164, 165, 166, 169, 170, 171
VS_3_0, PS_3_0 = 0xfffe0300, 0xffff0300


class Reader:
    def __init__(self, data):
        self.d = data
        tag, size = struct.unpack_from("<II", data, 0)
        check("the binary starts with the fx_2_0 tag", tag == 0xfeff0901, hex(tag))
        self.base = 8
        self.pos = 8 + size

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.pos)[0]
        self.pos += 4
        return v

    def at(self, off):
        return struct.unpack_from("<I", self.d, self.base + off)[0]

    def name(self, off):
        n = self.at(off)
        return self.d[self.base + off + 4:self.base + off + 4 + n].rstrip(b"\0").decode() if n else ""

    def typedef(self, off):
        t, c, no, so, ec = struct.unpack_from("<5I", self.d, self.base + off)
        rows = cols = None
        if c in (0, PC_MATRIX_ROWS, 3):
            rows, cols = struct.unpack_from("<II", self.d, self.base + off + 20)
        return dict(type=t, cls=c, name=self.name(no), elems=ec, rows=rows, cols=cols)

    def state(self):
        op, index, to, vo = self.u32(), self.u32(), self.u32(), self.u32()
        return dict(op=op, index=index, td=self.typedef(to), value=self.at(vo), vo=vo)

    def data(self):
        size = self.u32()
        blob = self.d[self.pos:self.pos + size]
        self.pos += (size + 3) & ~3
        return blob


def read_effect(data):
    """The whole structure: parameters (with sampler states), techniques, passes, object records and resources."""
    rd = Reader(data)
    nparams, ntech, _nshaders, nobjects = rd.u32(), rd.u32(), rd.u32(), rd.u32()
    eff = dict(params=[], techniques=[], records=[], resources=[], nobjects=nobjects)
    for i in range(nparams):
        to, vo, _flags, nann = rd.u32(), rd.u32(), rd.u32(), rd.u32()
        td = rd.typedef(to)
        for _ in range(nann):
            rd.u32(); rd.u32()
        p = dict(td=td, vo=vo, states=None, value=None)
        if PT_SAMPLER <= td["type"] <= PT_SAMPLERCUBE:
            save = rd.pos
            rd.pos = rd.base + vo
            count = rd.u32()
            p["states"] = [rd.state() for _ in range(count)]
            rd.pos = save
        elif td["type"] == PT_TEXTURE:
            p["value"] = rd.at(vo)
        eff["params"].append(p)
    for _ in range(ntech):
        no, nann, npass = rd.u32(), rd.u32(), rd.u32()
        for _ in range(nann):
            rd.u32(); rd.u32()
        tech = dict(name=rd.name(no), passes=[])
        for _ in range(npass):
            pn, pann, nstates = rd.u32(), rd.u32(), rd.u32()
            for _ in range(pann):
                rd.u32(); rd.u32()
            tech["passes"].append(dict(name=rd.name(pn), states=[rd.state() for _ in range(nstates)]))
        eff["techniques"].append(tech)
    nrecords, nresources = rd.u32(), rd.u32()
    for _ in range(nrecords):
        eff["records"].append((rd.u32(), rd.data()))
    for _ in range(nresources):
        tech, index, elem, sidx, usage = rd.u32(), rd.u32(), rd.u32(), rd.u32(), rd.u32()
        eff["resources"].append(dict(key=(tech, index, elem, sidx), usage=usage, blob=rd.data()))
    eff["end"], eff["size"] = rd.pos, len(data)
    return eff


SAMPLE = r'''
texture ColorTex;
float4 Tint = float4(1, 1, 1, 1);

sampler2D ColorSampler
{
    Texture = <ColorTex>;
    AddressU = Clamp;
    MinFilter = Linear;
    MagFilter = Point;
};

void VSMain(in float4 pos : POSITION, in float2 uv : TEXCOORD0,
            out float4 opos : POSITION, out float2 ouv : TEXCOORD0)
{
    opos = pos;
    ouv = uv;
}

float4 PSMain(in float2 uv : TEXCOORD0) : COLOR0
{
    return tex2D(ColorSampler, uv) * Tint;
}

float4 PSDark(in float2 uv : TEXCOORD0) : COLOR0
{
    return tex2D(ColorSampler, uv) * 0.5;
}

technique Blit
{
    pass P0
    {
        VertexShader = compile vs_3_0 VSMain();
        PixelShader = compile ps_3_0 PSMain();
        ZEnable = 0;
        AlphaBlendEnable = false;
        CullMode = None;
    }
    pass P1
    {
        PixelShader = compile ps_3_0 PSDark();
        VertexShader = compile vs_3_0 VSMain();
        ZWriteEnable = false;
        MaterialDiffuse = float4(1, 0.5, 0.25, 1);
        ZEnable = true;
    }
}
'''

# Shaped like the effect AstroTripper builds at start (41T6YN: shared matrices bound to registers, six sampler state blocks on lines
# 25 to 30, one technique with pass assignments on line 73); the game's own source is not in the log beyond its first bytes.
ASTRO = r'''#define zzzpow( a, b ) pow((a),(b))

shared float4x4 projTM: register(c0);
shared float4x4 worldTM: register(c4);
shared float4 invprojTM[ 4 ];
float4 fogColor = float4(0.2, 0.3, 0.4, 1);
float glow = 0.5;

texture tex0;
texture tex1;
texture tex2;
texture tex3;
texture tex4;
texture tex5;

// padding so the sampler blocks sit where the game's do



sampler2D s0 { Texture = <tex0>; MinFilter = Linear; MagFilter = Linear; MipFilter = Linear; AddressU = Wrap; AddressV = Wrap; };
sampler2D s1 { Texture = <tex1>; MinFilter = Point; MagFilter = Point; MipFilter = None; AddressU = Clamp; AddressV = Clamp; };
sampler2D s2 { Texture = <tex2>; MinFilter = Linear; MagFilter = Linear; AddressU = Clamp; AddressV = Clamp; };
sampler2D s3 { Texture = <tex3>; MinFilter = Anisotropic; MagFilter = Linear; MipFilter = Linear; };
sampler2D s4 { Texture = <tex4>; AddressU = Mirror; AddressV = Mirror; };
sampler2D s5 { Texture = <tex5>; MinFilter = Linear; MagFilter = Linear; MipFilter = Point; AddressU = Border; };

struct VSOut { float4 pos : POSITION; float2 uv : TEXCOORD0; float fog : TEXCOORD1; };

VSOut VSMain(float4 pos : POSITION, float2 uv : TEXCOORD0)
{
    VSOut o;
    o.pos = mul(mul(pos, worldTM), projTM);
    o.uv = uv;
    o.fog = saturate(o.pos.z * invprojTM[2].z);
    return o;
}

float4 PSMain(VSOut i) : COLOR0
{
    float4 c = tex2D(s0, i.uv) * tex2D(s1, i.uv) + tex2D(s2, i.uv) * glow;
    c += tex2D(s3, i.uv) * tex2D(s4, i.uv) * tex2D(s5, i.uv);
    c.rgb = lerp(c.rgb, fogColor.rgb, zzzpow(i.fog, 2.0));
    return c;
}









technique Main
{
    pass P0
    {
        VertexShader = compile vs_3_0 VSMain();
        PixelShader = compile ps_3_0 PSMain();
        AlphaBlendEnable = TRUE;
        SrcBlend = SrcAlpha;
        DestBlend = InvSrcAlpha;
        ZWriteEnable = FALSE;
        CullMode = NONE;
    }
}
'''

# Effects and shaders the writer of release 1.19 compiles. Each must come out byte for byte the same from the new writer, except
# 'fx2-uninit', whose numeric parameter without an initializer is the one documented change.
CORPUS = {
    "fx2-params": ("fx_2_0", r'''
float4 a = float4(1, 2, 3, 4);
float b = 2.5;
int c = 3;
bool d = true;
float4x4 m = { 1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1 };
string label = "hello";
texture t;
float e < string UIName = "E"; > = 1.0;
technique T0 < string group = "g"; > { pass P0 { } pass P1 < int order = 2; > { } }
technique T1 { pass Only { } }
'''),
    "fx2-uninit": ("fx_2_0", r'''
float4 tint;
float scale = 2;
technique T { pass P { } }
'''),
    "fx4-technique": ("fx_4_0", r'''
cbuffer cb0 { float4 color = float4(1, 0, 0, 1); float4x4 wvp; };
Texture2D tex;
SamplerState samp { Filter = MIN_MAG_MIP_LINEAR; AddressU = Wrap; AddressV = Clamp; };
BlendState bs { BlendEnable[0] = FALSE; };
DepthStencilState ds { DepthEnable = FALSE; DepthWriteMask = 0; };
RasterizerState rs { CullMode = None; };
float4 VS(float4 p : POSITION) : SV_Position { return mul(p, wvp); }
float4 PS(float4 p : SV_Position) : SV_Target { return color * tex.Sample(samp, p.xy); }
technique10 T0
{
    pass P0
    {
        SetVertexShader(CompileShader(vs_4_0, VS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_4_0, PS()));
        SetBlendState(bs, float4(0, 0, 0, 0), 0xffffffff);
        SetDepthStencilState(ds, 0);
        SetRasterizerState(rs);
    }
}
'''),
    "fx5-technique": ("fx_5_0", r'''
cbuffer cb0 { float4 color = float4(0, 1, 0, 1); };
float4 VS(float4 p : POSITION) : SV_Position { return p; }
float4 PS(float4 p : SV_Position) : SV_Target { return color; }
technique11 T0 { pass P0 { SetVertexShader(CompileShader(vs_5_0, VS())); SetPixelShader(CompileShader(ps_5_0, PS())); } }
'''),
    "fx4-strings": ("fx_4_0", r'''
string s = "text";
float4 v = float4(1, 2, 3, 4);
technique10 T { pass P { } }
'''),
    "vs_2_0": ("vs_2_0", "float4x4 m; float4 main(float4 p : POSITION) : POSITION { return mul(p, m); }"),
    "ps_2_0": ("ps_2_0", "sampler s; float4 k; float4 main(float2 uv : TEXCOORD0) : COLOR { return tex2D(s, uv) * k; }"),
    "vs_3_0": ("vs_3_0", "float4x4 m; struct O { float4 p : POSITION; float2 t : TEXCOORD0; }; O main(float4 p : POSITION, float2 t : TEXCOORD0) { O o; o.p = mul(p, m); o.t = t; return o; }"),
    "ps_3_0": ("ps_3_0", "sampler2D s; float g; float4 main(float2 uv : TEXCOORD0) : COLOR0 { float4 c = tex2D(s, uv); return lerp(c, c * c, g); }"),
    "vs_4_0": ("vs_4_0", "cbuffer c { float4x4 m; }; float4 main(float4 p : POSITION) : SV_Position { return mul(p, m); }"),
    "ps_5_0": ("ps_5_0", "Texture2D t; SamplerState s; float4 main(float4 p : SV_Position, float2 uv : TEXCOORD0) : SV_Target { return t.Sample(s, uv); }"),
}


# Every shader of these effects must be the same function compiled alone (one context compiles all shaders of an effect). fx_2_0:
# the two above, two techniques that share a vertex shader (the second one's got the first pixel shader's `def c1 = 2, 2, 2, 2`),
# a vertex shader literal over a pixel shader uniform, two uniforms that ended on one register, sin/cos constants, separate
# texture and sampler objects (combined samplers the compile adds to the globals), shader parameters, reserved registers.
# fx_4_0 / fx_5_0: every shader read its constant buffers behind the layout the effect binds, the first one too.
EFFECTS = {
    "sample": ("fx_2_0", SAMPLE),
    "astro": ("fx_2_0", ASTRO),
    "two-techniques": ("fx_2_0", r'''
float4x4 wvp;
float4 colour = float4(1, 1, 1, 1);
texture tex;
sampler2D smp { Texture = <tex>; MinFilter = Linear; MagFilter = Linear; };
struct VSO { float4 pos : POSITION; float2 uv : TEXCOORD0; };
VSO VS(float4 pos : POSITION, float2 uv : TEXCOORD0) { VSO o; o.pos = mul(pos, wvp); o.uv = uv; return o; }
float4 PSBright(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(smp, uv) * colour * 2.0; }
float4 PSDim(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(smp, uv) * 0.5; }
technique Bright { pass P { VertexShader = compile vs_2_0 VS(); PixelShader = compile ps_2_0 PSBright(); } }
technique Dim { pass P { VertexShader = compile vs_2_0 VS(); PixelShader = compile ps_2_0 PSDim(); } }
'''),
    "literal-over-uniform": ("fx_2_0", r'''
float4 colour = float4(1, 0.5, 0.25, 1);
float4 VS(float4 p : POSITION) : POSITION { return p * 0.5; }
float4 PS() : COLOR0 { return colour; }
technique T { pass P { VertexShader = compile vs_2_0 VS(); PixelShader = compile ps_2_0 PS(); } }
'''),
    "two-uniforms-one-register": ("fx_2_0", r'''
float4 a, b, c, d;
float4 VS(float4 p : POSITION) : POSITION { return p + a + b; }
float4 PS() : COLOR0 { return b + c + d; }
technique T { pass P { VertexShader = compile vs_2_0 VS(); PixelShader = compile ps_2_0 PS(); } }
'''),
    "sincos": ("fx_2_0", r'''
float4x4 wvp; float t; float4 k;
float4 VS(float4 p : POSITION) : POSITION { return mul(p, wvp) + sin(t); }
float4 PS(float2 uv : TEXCOORD0) : COLOR0 { return k * cos(uv.x) + 0.25; }
float4 PS2(float2 uv : TEXCOORD0) : COLOR0 { return k * sin(uv.y); }
technique T { pass A { VertexShader = compile vs_2_0 VS(); PixelShader = compile ps_2_0 PS(); } pass B { PixelShader = compile ps_2_0 PS2(); } }
'''),
    "separate-objects": ("fx_2_0", r'''
Texture2D tA; Texture2D tB; SamplerState sLin; float4 k;
struct VSO { float4 p : POSITION; float2 uv : TEXCOORD0; };
VSO VS(float4 p : POSITION, float2 uv : TEXCOORD0) { VSO o; o.p = p; o.uv = uv; return o; }
float4 PS1(VSO i) : COLOR0 { return tA.Sample(sLin, i.uv) + tB.Sample(sLin, i.uv) * k; }
float4 PS2(VSO i) : COLOR0 { return tB.Sample(sLin, i.uv); }
float4 PS3(VSO i) : COLOR0 { return k; }
technique T { pass A { VertexShader = compile vs_3_0 VS(); PixelShader = compile ps_3_0 PS1(); }
              pass B { VertexShader = compile vs_3_0 VS(); PixelShader = compile ps_3_0 PS2(); }
              pass C { PixelShader = compile ps_3_0 PS3(); } }
'''),
    "shader-parameters": ("fx_2_0", r'''
Texture2D tA; SamplerState sLin; float4 k; float4x4 m;
float4 VS(float4 p : POSITION) : POSITION { return mul(p, m) * 0.5; }
float4 PS1(float2 uv : TEXCOORD0) : COLOR0 { return tA.Sample(sLin, uv) * k; }
float4 PS2(float2 uv : TEXCOORD0) : COLOR0 { return tA.Sample(sLin, uv) + 0.25; }
vertexshader vsp = compile vs_3_0 VS();
pixelshader psp = compile ps_3_0 PS1();
technique T { pass A { VertexShader = (vsp); PixelShader = (psp); } pass B { PixelShader = compile ps_3_0 PS2(); } }
'''),
    "reserved-registers": ("fx_2_0", r'''
float4x4 wvp : register(c0); float4 a : register(c10); float4 b; float4 c : register(c4);
sampler2D s5 : register(s5); sampler2D s0;
float4 VS(float4 p : POSITION) : POSITION { return mul(p, wvp) + a + 0.25; }
float4 PS(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(s5, uv) * b + tex2D(s0, uv) * c + 0.75; }
float4 PS2(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(s0, uv) * a; }
technique T { pass A { VertexShader = compile vs_2_0 VS(); PixelShader = compile ps_2_0 PS(); } pass B { PixelShader = compile ps_2_0 PS2(); } }
'''),
    "fx4-two-passes": ("fx_4_0", r'''
cbuffer cb { float4x4 wvp; float4 colA; float4 colB; };
float4 VS(float4 p : POSITION) : SV_Position { return mul(p, wvp); }
float4 PSA(float4 p : SV_Position) : SV_Target { return colA; }
float4 PSB(float4 p : SV_Position) : SV_Target { return colB * 2; }
technique10 T { pass P0 { SetVertexShader(CompileShader(vs_4_0, VS())); SetPixelShader(CompileShader(ps_4_0, PSA())); }
                pass P1 { SetVertexShader(CompileShader(vs_4_0, VS())); SetPixelShader(CompileShader(ps_4_0, PSB())); } }
'''),
    "fx4-globals": ("fx_4_0", r'''
float4x4 wvp; float4 colA; float4 colB; float scale;
float4 VS(float4 p : POSITION) : SV_Position { return mul(p, wvp) * scale; }
float4 PSA(float4 p : SV_Position) : SV_Target { return colA; }
float4 PSB(float4 p : SV_Position) : SV_Target { return colB; }
technique10 A { pass P { SetVertexShader(CompileShader(vs_4_0, VS())); SetPixelShader(CompileShader(ps_4_0, PSA())); } }
technique10 B { pass P { SetPixelShader(CompileShader(ps_4_0, PSB())); SetVertexShader(CompileShader(vs_4_0, VS())); } }
'''),
    "fx4-textures-two-buffers": ("fx_4_0", r'''
cbuffer cb1 { float4 c1; }; cbuffer cb2 { float4 c2; };
Texture2D texA; Texture2D texB; SamplerState smp { Filter = MIN_MAG_MIP_LINEAR; };
float4 PS1(float4 p : SV_Position, float2 uv : TEXCOORD0) : SV_Target { return texA.Sample(smp, uv) + texB.Sample(smp, uv) * c1; }
float4 PS2(float4 p : SV_Position, float2 uv : TEXCOORD0) : SV_Target { return texB.Sample(smp, uv) * c2; }
technique10 T { pass P0 { SetPixelShader(CompileShader(ps_4_0, PS1())); } pass P1 { SetPixelShader(CompileShader(ps_4_0, PS2())); } }
'''),
    "fx4-shader-variables": ("fx_4_0", r'''
cbuffer cb { float4 a; float4 b; };
float4 VS(float4 p : POSITION) : SV_Position { return p + a; }
float4 PS(float4 p : SV_Position) : SV_Target { return b; }
VertexShader vs = CompileShader(vs_4_0, VS());
PixelShader ps = CompileShader(ps_4_0, PS());
technique10 T { pass P { SetVertexShader(vs); SetPixelShader(ps); } }
'''),
    "fx4-geometry-shader": ("fx_4_0", r'''
struct V { float4 p : SV_Position; float4 c : COLOR0; };
cbuffer cb { float4 tint; float scale; };
V VS(float4 p : POSITION) { V o; o.p = p * scale; o.c = tint; return o; }
[maxvertexcount(3)] void GS(triangle V i[3], inout TriangleStream<V> s) { for (int k = 0; k < 3; ++k) { V o = i[k]; o.c *= tint; s.Append(o); } }
float4 PS(V i) : SV_Target { return i.c * tint; }
technique10 T { pass A { SetVertexShader(CompileShader(vs_4_0, VS())); SetGeometryShader(CompileShader(gs_4_0, GS())); SetPixelShader(CompileShader(ps_4_0, PS())); }
                pass B { SetVertexShader(CompileShader(vs_4_0, VS())); SetGeometryShader(NULL); SetPixelShader(CompileShader(ps_4_0, PS())); } }
'''),
    "fx4-legacy-sampler": ("fx_4_1", r'''
sampler2D legacy; float4 k;
float4 PS1(float4 p : SV_Position, float2 uv : TEXCOORD0) : SV_Target { return tex2D(legacy, uv) * k; }
float4 PS2(float4 p : SV_Position, float2 uv : TEXCOORD0) : SV_Target { return tex2D(legacy, uv); }
technique10 T { pass A { SetPixelShader(CompileShader(ps_4_1, PS1())); } pass B { SetPixelShader(CompileShader(ps_4_1, PS2())); } }
'''),
    "fx5-two-passes": ("fx_5_0", r'''
cbuffer cb { float4x4 wvp; float4 colA; float4 colB; };
Texture2D texA; Texture2D texB; SamplerState smp;
float4 VS(float4 p : POSITION) : SV_Position { return mul(p, wvp); }
float4 PSA(float4 p : SV_Position, float2 uv : TEXCOORD0) : SV_Target { return colA * texA.Sample(smp, uv) + texB.Sample(smp, uv); }
float4 PSB(float4 p : SV_Position, float2 uv : TEXCOORD0) : SV_Target { return colB * texB.Sample(smp, uv); }
technique11 T { pass P0 { SetVertexShader(CompileShader(vs_5_0, VS())); SetPixelShader(CompileShader(ps_5_0, PSA())); }
                pass P1 { SetVertexShader(CompileShader(vs_5_0, VS())); SetPixelShader(CompileShader(ps_5_0, PSB())); } }
'''),
}
EFFECTS["fx4-technique"] = CORPUS["fx4-technique"]
EFFECTS["fx5-technique"] = CORPUS["fx5-technique"]
COMPILE2 = re.compile(r"compile\s+([vp]s_\d_\w)\s+(\w+)\s*\(([^)]*)\)")
COMPILE4 = re.compile(r"CompileShader\(\s*(\w+)\s*,\s*(\w+)\s*\(([^)]*)\)\s*\)")


def shader_blobs(profile, data):
    """The shaders inside an effect, in the order the writer wrote them. fx_2_0: the object records of shader parameters (written
    with the parameters) and the usage-0 resource records of the passes; fx_4_0 / fx_5_0: the DXBC containers."""
    if profile == "fx_2_0":
        eff = read_effect(data)
        recs = [b for _, b in eff["records"] if len(b) >= 4 and struct.unpack_from("<I", b, 0)[0] >> 16 in (0xfffe, 0xffff)]
        return recs + [r["blob"] for r in eff["resources"] if r["usage"] == 0]
    out, i = [], 0
    while (i := data.find(b"DXBC", i)) >= 0:
        size = struct.unpack_from("<I", data, i + 24)[0]
        out.append(data[i:i + size])
        i += size
    return out


def shaders_vs_alone(exe, tag, profile, text, tmp):
    """[(function, profile, equal)] for every shader of the effect, or a string saying why it could not be compared."""
    rc, data, err = compile_with(exe, profile, text, tmp, f"{tag}-{exe.name}")
    if rc or not data:
        return "not compiled: " + err.strip()[-300:]
    comps = (COMPILE2 if profile == "fx_2_0" else COMPILE4).findall(text)
    blobs = shader_blobs(profile, data)
    if len(comps) != len(blobs):
        return f"{len(comps)} compile expressions, {len(blobs)} shaders in the effect"
    out = []
    for n, ((prof, fn, args), blob) in enumerate(zip(comps, blobs)):
        if args.strip():
            return f"{fn}: arguments, no stand-alone form"
        r2, alone, e2 = compile_with(exe, prof, text, tmp, f"{tag}-{exe.name}-alone-{n}", fn)
        if r2 or not alone:
            return f"{fn}/{prof} alone: " + e2.strip()[-200:]
        out.append((fn, prof, alone == blob))
    return out


def check_sample(eff):
    """Upstream's read-back of the sample effect."""
    params = eff["params"]
    check("sample: three parameters, one technique", len(params) == 3 and len(eff["techniques"]) == 1)
    if len(params) != 3 or len(eff["techniques"]) != 1:
        return
    check("sample: parameters ColorTex, Tint, ColorSampler", [p["td"]["name"] for p in params] == ["ColorTex", "Tint", "ColorSampler"])
    samp = params[2]
    st = samp["states"] or []
    check("sample: the sampler's four states follow its count, in order",
          [s["op"] for s in st] == [OP_TEXTURE, OP_ADDRESSU, OP_MINFILTER, OP_MAGFILTER], [s["op"] for s in st])
    ids = [params[0]["value"]]
    objs = []
    if len(st) == 4:
        check("sample: Texture = <ColorTex> is an object, the numeric states INT 1x1 (Clamp, Linear, Point)",
              st[0]["td"]["type"] == PT_TEXTURE and st[0]["td"]["cls"] == PC_OBJECT
              and all(s["td"]["type"] == PT_INT and s["td"]["cls"] == PC_MATRIX_ROWS and s["td"]["rows"] == 1 and s["td"]["cols"] == 1
                      for s in st[1:]) and [s["value"] for s in st[1:]] == [3, 2, 1])
        check("sample: sampler states carry index 256", all(s["index"] == 256 for s in st))
        ids.append(st[0]["value"])
        objs.append(((0xffffffff, 2, 0, 0), OP_TEXTURE))
    passes = eff["techniques"][0]["passes"]
    want = [[OP_VERTEXSHADER, OP_PIXELSHADER, OP_ZENABLE, OP_ALPHABLENDENABLE, OP_CULLMODE],
            [OP_PIXELSHADER, OP_VERTEXSHADER, OP_ZWRITEENABLE, OP_MATERIALDIFFUSE, OP_ZENABLE]]
    check("sample: the passes' states", [[s["op"] for s in p["states"]] for p in passes] == want)
    if [[s["op"] for s in p["states"]] for p in passes] == want:
        for pi, p in enumerate(passes):
            for k, s in enumerate(p["states"]):
                if s["op"] in (OP_VERTEXSHADER, OP_PIXELSHADER):
                    ids.append(s["value"])
                    objs.append(((0, pi, 0xffffffff, k), s["op"]))
        md = passes[1]["states"][3]
        check("sample: MaterialDiffuse = float4(...) is FLOAT 4x1 with every component",
              md["td"]["type"] == PT_FLOAT and md["td"]["cls"] == PC_MATRIX_ROWS and md["td"]["rows"] == 4 and md["td"]["cols"] == 1
              and struct.unpack_from("<4f", eff["_data"], 8 + md["vo"]) == (1.0, 0.5, 0.25, 1.0))
        check("sample: ZEnable = 0, AlphaBlendEnable = false, CullMode = None; ZWriteEnable = false, ZEnable = true",
              [s["value"] for s in passes[0]["states"][2:]] == [0, 0, 1]
              and [passes[1]["states"][2]["value"], passes[1]["states"][4]["value"]] == [0, 1])
    check_objects(eff, ids, objs, "sample", records=1, names={(0xffffffff, 2, 0, 0): b"ColorTex\0"})


def check_objects(eff, ids, objs, label, records, names):
    check(f"{label}: {records} object record(s) (the texture parameters)", len(eff["records"]) == records
          and all(oid in ids and not blob for oid, blob in eff["records"]), [(o, len(b)) for o, b in eff["records"]])
    check(f"{label}: one resource record per object state", len(eff["resources"]) == len(objs), (len(eff["resources"]), len(objs)))
    for key, op in objs:
        res = [r for r in eff["resources"] if r["key"] == key]
        if len(res) != 1:
            check(f"{label}: the resource record of {key}", False, len(res))
            continue
        r = res[0]
        if op == OP_TEXTURE:
            check(f"{label}: {key} names its parameter", r["usage"] == 1 and r["blob"] == names.get(key), r["blob"])
        else:
            ver = struct.unpack_from("<I", r["blob"], 0)[0] if len(r["blob"]) >= 4 else 0
            check(f"{label}: {key} carries SM3 bytecode", r["usage"] == 0 and ver == (VS_3_0 if op == OP_VERTEXSHADER else PS_3_0), hex(ver))
    check(f"{label}: object ids unique and below the object count", len(ids) == len(set(ids)) and all(0 < i < eff["nobjects"] for i in ids),
          (ids, eff["nobjects"]))
    check(f"{label}: the records end at the file's end", eff["end"] == eff["size"], (eff["end"], eff["size"]))


def check_astro(eff):
    params = eff["params"]
    samplers = [p for p in params if p["states"] is not None]
    check("AstroTripper-like: six sampler parameters with their states", len(samplers) == 6, len(samplers))
    want_counts = [6, 6, 5, 4, 3, 5]
    check("AstroTripper-like: each sampler's state count", [len(p["states"]) for p in samplers] == want_counts,
          [len(p["states"]) for p in samplers])
    ids, objs = [], []
    for p in params:
        if p["td"]["type"] == PT_TEXTURE:
            ids.append(p["value"])
    names, number = {}, 0
    for idx, p in enumerate(params):
        if p["states"] is None:
            continue
        for k, s in enumerate(p["states"]):
            if s["op"] == OP_TEXTURE:
                ids.append(s["value"])
                objs.append(((0xffffffff, idx, 0, k), OP_TEXTURE))
                names[(0xffffffff, idx, 0, k)] = b"tex%d\0" % number   # sampler sN reads texN
        number += 1
    passes = eff["techniques"][0]["passes"] if eff["techniques"] else []
    check("AstroTripper-like: one pass with seven states",
          len(passes) == 1 and [s["op"] for s in passes[0]["states"]] ==
          [OP_VERTEXSHADER, OP_PIXELSHADER, OP_ALPHABLENDENABLE, OP_SRCBLEND, OP_DESTBLEND, OP_ZWRITEENABLE, OP_CULLMODE])
    if passes and len(passes[0]["states"]) == 7:
        st = passes[0]["states"]
        # D3DBLEND_SRCALPHA 5, D3DBLEND_INVSRCALPHA 6, D3DCULL_NONE 1; TRUE / FALSE by name
        check("AstroTripper-like: TRUE, SrcAlpha, InvSrcAlpha, FALSE, NONE", [s["value"] for s in st[2:]] == [1, 5, 6, 0, 1],
              [s["value"] for s in st[2:]])
        for k in (0, 1):
            ids.append(st[k]["value"])
            objs.append(((0, 0, 0xffffffff, k), st[k]["op"]))
    check_objects(eff, ids, objs, "AstroTripper-like", records=6, names=names)


with tempfile.TemporaryDirectory(prefix="bcd-vkd3d-fx2-") as tmp:
    tmp = Path(tmp)
    exes = build(tmp)
    if "new" not in exes:
        finish()
    new = exes["new"]
    for label, text, checker in (("sample", SAMPLE, check_sample), ("AstroTripper-like", ASTRO, check_astro)):
        rc, data, err = compile_with(new, "fx_2_0", text, tmp, label.replace(" ", "_") + "-new")
        check(f"{label}: the new writer compiles it to fx_2_0", rc == 0 and data, err[-1500:])
        if data:
            eff = read_effect(data)
            eff["_data"] = data
            checker(eff)

    # ---- every shader of an effect is the same function compiled alone
    noreset = exes.get("noreset")
    check("the tree without the reset is built (the comparison below must catch it)", noreset is not None)
    caught = []
    for tag, (profile, text) in EFFECTS.items():
        res = shaders_vs_alone(new, tag, profile, text, tmp)
        if isinstance(res, str):
            check(f"{tag} ({profile}): its shaders are compared with their stand-alone compiles", False, res)
            continue
        fx2 = profile == "fx_2_0"
        if fx2 or reset_every:
            check(f"{tag} ({profile}): each of its {len(res)} shaders is byte for byte the function compiled alone",
                  all(eq for _, _, eq in res), [f"{fn} ({prof})" for fn, prof, eq in res if not eq])
        else:
            print(f"note: {tag} ({profile}): fx_4_0 / fx_5_0 shaders are not reset in this tree; "
                  f"{sum(1 for _, _, eq in res if not eq)} of {len(res)} differ from their stand-alone compiles")
        if noreset:
            bad = shaders_vs_alone(noreset, tag, profile, text, tmp)
            if not isinstance(bad, str) and (fx2 or reset_every):
                caught += [f"{tag}: {fn} ({prof})" for fn, prof, eq in bad if not eq]
    if noreset:
        must = ["two-techniques: VS (vs_2_0)", "literal-over-uniform: PS (ps_2_0)", "two-uniforms-one-register: PS (ps_2_0)"]
        if reset_every:
            must += ["fx4-two-passes: VS (vs_4_0)", "fx4-two-passes: PSA (ps_4_0)", "fx5-two-passes: VS (vs_5_0)"]
        check("without the reset the comparison fails: " + "; ".join(must), all(m in caught for m in must), caught)

    # ---- MADEIRA_FX_WRITER: "0" gives back the writer before; anything else keeps this one
    for label, text in (("sample", SAMPLE), ("AstroTripper-like", ASTRO)):
        rc, data, err = compile_with(new, "fx_2_0", text, tmp, label.replace(" ", "_") + "-off", writer="0")
        check(f"{label}: MADEIRA_FX_WRITER=0 refuses it with the E5017 messages of the writer before",
              rc != 0 and "Writing fx_2_0 sampler objects initializers is not implemented" in err and "Write pass assignments" in err,
              err[-600:])
    rc_on, d_on, _ = compile_with(new, "fx_2_0", SAMPLE, tmp, "sample-on")
    for value in ("1", "", "00", " 0", "false"):
        rc, data, err = compile_with(new, "fx_2_0", SAMPLE, tmp, "sample-value", writer=value)
        check(f"MADEIRA_FX_WRITER={value!r} keeps the writer on", rc_on == 0 and rc == 0 and data == d_on, err[-300:])

    old = exes.get("old")
    if not old:
        print("SKIP: the tree's history has no writer before the change (a shallow checkout): the old-writer comparison runs on the Mac")
        finish()
    for label, text in (("sample", SAMPLE), ("AstroTripper-like", ASTRO)):
        rc, data, err = compile_with(old, "fx_2_0", text, tmp, label.replace(" ", "_") + "-old")
        check(f"{label}: the writer before the change refuses it with the E5017 messages (the check bites)",
              rc != 0 and "Writing fx_2_0 sampler objects initializers is not implemented" in err and "Write pass assignments" in err,
              err[-600:])
    # The switch off: the effects above, the corpus below and every corpus input as an effect of the other models give the old
    # writer's bytes or the old writer's messages.
    inputs = [(f"effect-{k}", p, v, "-") for k, (p, v) in EFFECTS.items()]
    inputs += [(f"corpus-{k}", p, v, "-" if p.startswith("fx") else "main") for k, (p, v) in CORPUS.items()]
    inputs += [(f"as-{m}-{k}", m, v, "-") for k, (p, v) in CORPUS.items() for m in ("fx_2_0", "fx_4_0", "fx_5_0") if m != p]
    same, bad = 0, []
    for tag, profile, text, entry in inputs:
        r_o, d_o, e_o = compile_with(old, profile, text, tmp, tag + "-o", entry)
        r_n, d_n, e_n = compile_with(new, profile, text, tmp, tag + "-n0", entry, writer="0")
        if (r_o, d_o, e_o.strip()) == (r_n, d_n, e_n.strip()):
            same += 1
        else:
            bad.append((tag, r_o, r_n, len(d_o or b""), len(d_n or b""), e_n.strip()[-160:]))
    check(f"MADEIRA_FX_WRITER=0: all {len(inputs)} inputs give the old writer's bytes or its messages", not bad and same == len(inputs),
          bad[:4])

    compiled_before = 0
    for tag, (profile, text) in CORPUS.items():
        rc_o, d_o, e_o = compile_with(old, profile, text, tmp, tag + "-old", "main" if not profile.startswith("fx") else "-")
        rc_n, d_n, e_n = compile_with(new, profile, text, tmp, tag + "-new", "main" if not profile.startswith("fx") else "-")
        if rc_o != 0:
            print(f"note: {tag} ({profile}) did not compile with the old writer either; not compared ({e_o.strip()[-160:]})")
            continue
        compiled_before += 1
        if tag == "fx2-uninit":
            ok = rc_n == 0 and d_n is not None and len(d_n) >= len(d_o)
            if ok:
                eo, en = read_effect(d_o), read_effect(d_n)
                tint_o, tint_n = eo["params"][0], en["params"][0]
                zeros = struct.unpack_from("<4I", d_n, 8 + tint_n["vo"]) == (0, 0, 0, 0)
                check("fx2-uninit: before, the parameter without an initializer pointed at value offset 0 (the first type record)",
                      tint_o["vo"] == 0)
                check("fx2-uninit: now it points at four zeros, as fxc writes it; the initialized one keeps its value",
                      tint_n["vo"] != 0 and zeros and struct.unpack_from("<f", d_n, 8 + en["params"][1]["vo"])[0] == 2.0)
                check("fx2-uninit: the rest of the binary has the same structure", len(eo["params"]) == len(en["params"]) and
                      [t["name"] for t in eo["techniques"]] == [t["name"] for t in en["techniques"]] and en["end"] == en["size"])
            else:
                check("fx2-uninit: the new writer compiles it", False, e_n[-600:])
            continue
        if reset_every and profile in ("fx_4_0", "fx_4_1", "fx_5_0") and d_o and shader_blobs(profile, d_o):
            # Shaders inside: the reset makes them the stand-alone compiles (above); vkd3d's effect parser must print the rest of
            # the effect (buffers, variables and their offsets, techniques, states) exactly as before.
            t_o, t_n = effect_text(new, d_o, tmp, f"{tag}-o"), effect_text(new, d_n or b"", tmp, f"{tag}-n")
            check(f"{tag} ({profile}): only its shaders changed: vkd3d's effect parser prints the rest as before",
                  rc_n == 0 and t_o is not None and t_o == t_n
                  and len(shader_blobs(profile, d_o)) == len(shader_blobs(profile, d_n or b"")), (t_o or [])[:6])
            continue
        check(f"{tag} ({profile}): byte for byte what the old writer made", rc_n == 0 and d_n == d_o,
              (rc_n, len(d_n or b""), len(d_o or b""), e_n[-300:]))
        if profile == "fx_2_0" and d_n:
            e = read_effect(d_n)
            check(f"{tag}: reads back to the file's end", e["end"] == e["size"])
    if reset_every:
        for tag, (profile, text) in EFFECTS.items():
            if profile == "fx_2_0":
                continue
            r_o, d_o, _ = compile_with(old, profile, text, tmp, f"{tag}-struct-o")
            r_n, d_n, _ = compile_with(new, profile, text, tmp, f"{tag}-struct-n")
            t_o = effect_text(new, d_o, tmp, f"{tag}-so") if d_o else None
            t_n = effect_text(new, d_n, tmp, f"{tag}-sn") if d_n else None
            check(f"{tag} ({profile}): the old writer's effect and this one differ only in their shaders", r_o == 0 and r_n == 0
                  and t_o is not None and t_o == t_n and d_o != d_n, (t_o or [])[:4])
    check("the old writer compiled most of the corpus (the comparison is not empty)", compiled_before >= 8, compiled_before)
finish()
