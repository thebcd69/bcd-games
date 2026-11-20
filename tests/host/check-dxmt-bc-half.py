#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Value 2 of MADEIRA_MIP_CLAMP_AUTO: single-level BC textures at half size and the auto clamp from 256 px (round 677, agent G3 S1;
the owner's decision of 2026-11-19; tools/patch-dxmt-bc-half.py, d3d11-src.dll only). No Wine, no GPU.

Shroom and Gloom (7 logs, build 665: killed at 4,043-4,087 of 4,095 MB, 958 of 960 BC textures single-level), Schedule I (EM08Q8)
and Nunholy (DBG514) die of BC textures a GPU without BC sampling expands to RGBA8; neither existing clamp can shrink them.

Checks:
  1. tools/build-d3d11-dll.sh's BCD_PATCHES carries the script with the marker its code leaves in the DLL, before
     patch-d3d11-src-all-arch.py (the copy steps and the idempotency of the whole list: tests/host/check-d3d11-src-fixes.py);
  2. on a copy of the pinned dxmt/src (the copy steps of the build script up to it): the script applies, a second run says
     "already patched", a tree with one of its files patched is refused, a moved anchor fails by name;
  3. the policy header, compiled on the host: AutoLevel reads "2" (spaces allowed) as 2 and anything else as 1; the auto clamp
     starts at 1,024 px for level 1 and 256 px for level 2; SingleLevelHalfEligible takes one level, one slice, sides that are whole
     4x4 blocks and 65,536 texels or more; HalfFormat is BC1/BC2/BC3/BC7 (DXGI 70-78, 97-99);
  4. bc_decode_image_half, cut out of the copy with bc_decode_image and compiled with dxmt_bcn: for random BC1, BC2, BC3 and BC7
     blocks it gives exactly the 2x2 box ((a+b+c+d+2)/4) of the full decode;
  5. the patched code keeps the gates: the level is 0 when the switch is off and 2 only for "2"; a texture is halved only with
     level 2, initial data, no mip bias, no misc flag, a half format, DEFAULT/IMMUTABLE, shader resource only, no CPU access, one
     sample, the size test and a GPU without BC, and only under pressure; the texture learns it right after its mip bias; the clone
     (shareTo) copies it; initWithData decodes to half size only for it and level 0 and skips any other upload into it; the copy
     path and both update paths drop an operation that touches it, before the mip-bias translation;
  6. the app: the list policy takes 0, 1 or 2; the catalog note names value 2; the rows give "= 2" from build 681 and no
     env.MADEIRA_D3D11_SRC line, any "= 1" line ends at 680; CI runs this check;
  7. (2026-11-20, the owner's rule that nothing that works may break) value 2 never moves a GPU that samples BC off the committed
     d3d11.dll: WineProcessBridge.m links d3d11-src.dll for it only when the game sets no MADEIRA_D3D11_SRC of its own and the GPU
     cannot sample BC (supportsBCTextureCompression), and reads "2" exactly as AutoLevel does (compiled on the host, same inputs);
     the committed 64-bit and 32-bit d3d11.dll carry the ml2000 clamp, whose IsOffValue takes "2" as on (the pinned source, as in
     dxmt 2dc8d16 that the committed d3d11.dll was built from), so there value 2 is value 1. The block itself runs on the host
     with each case (a GPU with and without BC, the game's own 0, a build without d3d11-src.dll) in tests/host/check-dxmt-fresh.py.
SKIP (exit 0) of 2-5 without dxmt sources (set DXMT_SRC_DIR to a directory holding an unpatched `src`), of 3-4 without a compiler.
"""
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SRC = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


sh = (R / "tools/build-d3d11-dll.sh").read_text()
m = re.search(r"\nBCD_PATCHES=\((.*?)\)\n", sh, re.S)
pairs = re.findall(r'"([^"]+\.py)" "((?:[^"\\]|\\.)*)"', m.group(1)) if m else []
names = [p for p, _ in pairs]
check(("patch-dxmt-bc-half.py", "[bc-half] madeira-bcd ") in pairs, "BCD_PATCHES carries patch-dxmt-bc-half.py with its marker")
check("patch-dxmt-bc-half.py" in names and names.index("patch-dxmt-bc-half.py") < names.index("patch-d3d11-src-all-arch.py"),
      "it runs before patch-d3d11-src-all-arch.py")
script = R / "tools/patch-dxmt-bc-half.py"

cxx = shutil.which("clang++") or shutil.which("c++")


def compile_run(t, name, code, extra=()):
    (t / f"{name}.cpp").write_text(code)
    b = subprocess.run([cxx, "-std=c++20", "-O1", "-I", str(t), *map(str, extra), str(t / f"{name}.cpp"), "-o", str(t / name)],
                       capture_output=True, text=True)
    if b.returncode:
        return None, b.stderr
    p = subprocess.run([str(t / name)], capture_output=True, text=True)
    return p, ""


if not (SRC / "d3d11/d3d11_texture_device.cpp").is_file():
    print(f"SKIP 2-5: no dxmt sources at {SRC} (check out the submodule or set DXMT_SRC_DIR)")
else:
    with tempfile.TemporaryDirectory(prefix="bc-half-") as tmp:
        tmp = pathlib.Path(tmp)
        tree = tmp / "tree/src"
        tree.mkdir(parents=True)
        for d in ("d3d11", "d3d10", "dxmt", "util", "winemetal"):
            shutil.copytree(SRC / d, tree / d)
        (tmp / "patchroot").mkdir()
        os.symlink(tmp / "tree", tmp / "patchroot/dxmt")

        def run(step, args=(), cwd=None):
            return subprocess.run([sys.executable, str(R / "tools" / step), *map(str, args)], cwd=cwd or tmp, capture_output=True, text=True)

        steps = [run("patch-dxmt-context-state-swap.py", [tree / "d3d11"]), run("patch-d3d11-src-small-rings.py", [tree / "dxmt"])]
        for s in names[:names.index("patch-dxmt-bc-half.py")] if "patch-dxmt-bc-half.py" in names else []:
            steps.append(run(s, cwd=tmp / "patchroot"))
        check(all(p.returncode == 0 for p in steps), "the build script's copy steps before it apply",
              [(p.args[1], (p.stdout + p.stderr)[-200:]) for p in steps if p.returncode])
        pristine = tmp / "pristine"
        shutil.copytree(tree, pristine)
        first = run("patch-dxmt-bc-half.py", cwd=tmp / "patchroot")
        second = run("patch-dxmt-bc-half.py", cwd=tmp / "patchroot")
        check(first.returncode == 0 and "patched" in first.stdout and "already" not in first.stdout, "the script applies", first.stdout + first.stderr)
        check(second.returncode == 0 and "already patched" in second.stdout, "a second run says already patched", second.stdout + second.stderr)

        # A tree with one file patched (an interrupted run) and a moved anchor are refused by name.
        for case in ("partial", "moved"):
            alt = tmp / case
            shutil.copytree(pristine, alt / "dxmt/src")
            if case == "partial":
                shutil.copy(tree / "dxmt/dxmt_texture.cpp", alt / "dxmt/src/dxmt/dxmt_texture.cpp")
            else:
                f = alt / "dxmt/src/dxmt/dxmt_texture.hpp"
                f.write_text(f.read_text().replace("void setMipBias(uint32_t bias) { mip_bias_ = bias; }", "void setMipBias(uint32_t b) { mip_bias_ = b; }"))
            p = run("patch-dxmt-bc-half.py", cwd=alt)
            want = "only partly patched" if case == "partial" else "dxmt/dxmt_texture.hpp"
            check(p.returncode != 0 and want in (p.stdout + p.stderr), f"a {case} tree is refused by name", p.stdout + p.stderr)

        policy = (tree / "d3d11/d3d11_mip_clamp_policy.hpp").read_text()
        dev = (tree / "d3d11/d3d11_texture_device.cpp").read_text()
        texh = (tree / "dxmt/dxmt_texture.hpp").read_text()
        texc = (tree / "dxmt/dxmt_texture.cpp").read_text()
        init = (tree / "dxmt/dxmt_resource_initializer.cpp").read_text()
        ctx = (tree / "d3d11/d3d11_context_impl.cpp").read_text()

        # --- 3. the policy header on the host ---
        if not cxx:
            print("SKIP 3-4: no C++ compiler")
        else:
            shutil.copy(tree / "d3d11/d3d11_mip_clamp_policy.hpp", tmp / "policy.hpp")
            p, err = compile_run(tmp, "policy", r'''#include "policy.hpp"
#include <cstdio>
using namespace dxmt::mip_clamp;
int main() {
  const char *v[] = {"2", " 2 ", "1", "on", "22", "", "0", "2x", "\t2"};
  for (auto s : v) printf("%u", AutoLevel(s));
  printf(" %u", AutoLevel(nullptr));
  printf(" %d%d%d%d%d", AutoSizeEligibleAtLevel(512, 512, 1), AutoSizeEligibleAtLevel(512, 512, 2), AutoSizeEligibleAtLevel(1024, 64, 1),
         AutoSizeEligibleAtLevel(255, 255, 2), AutoSizeEligibleAtLevel(64, 256, 2));
  printf(" %d%d%d%d%d%d%d%d", SingleLevelHalfEligible(2560, 2412, 1, 1), SingleLevelHalfEligible(2561, 2412, 1, 1),
         SingleLevelHalfEligible(2560, 2410, 1, 1), SingleLevelHalfEligible(2560, 2412, 2, 1), SingleLevelHalfEligible(2560, 2412, 1, 2),
         SingleLevelHalfEligible(128, 128, 1, 1), SingleLevelHalfEligible(256, 256, 1, 1), SingleLevelHalfEligible(4096, 16, 1, 1));
  printf(" ");
  for (unsigned f = 60; f < 104; f++) if (HalfFormat(f)) printf("%u,", f);
  printf("\n");
}
''')
            check(p is not None, "the policy header builds on the host", err)
            if p:
                check(p.stdout.strip() == "221111112 1 01101 10000011 70,71,72,73,74,75,76,77,78,97,98,99,",
                      "AutoLevel, AutoSizeEligibleAtLevel, SingleLevelHalfEligible and HalfFormat answer as specified", p.stdout)
            # 7: the clamp of the committed d3d11.dll (no level) takes "2" as on, like "1".
            shutil.copy(SRC / "d3d11/d3d11_mip_clamp_policy.hpp", tmp / "pinned_policy.hpp")
            p, err = compile_run(tmp, "offvalue", '#include "pinned_policy.hpp"\n#include <cstdio>\nusing namespace dxmt::mip_clamp;\n'
                                 'int main() { const char *v[] = {"2", " 2 ", "1", "0", "off", "No"}; for (auto s : v) printf("%d", IsOffValue(s)); }\n')
            check(p is not None and p.stdout.strip() == "000111", "the pinned clamp's IsOffValue takes 2 as on (value 1 there)",
                  (p.stdout if p else err))

            # --- 4. the half decode against the full decode and a 2x2 box ---
            a = init.index("uint32_t bc_decode_texel_size(int kind) {")
            b = init.index("/* ml676: which REMAPPED formats")
            shutil.copy(tree / "dxmt/dxmt_bcn.hpp", tmp / "dxmt_bcn.hpp")
            random.seed(677)
            blocks = bytes(random.randrange(256) for _ in range(16 * 16 * 8))
            (tmp / "blocks.bin").write_bytes(blocks)
            p, err = compile_run(tmp, "decode", '#include "dxmt_bcn.hpp"\n#include <cstdio>\n#include <vector>\nnamespace dxmt {\n' + init[a:b] + r'''
}
using namespace dxmt;
int main() {
  FILE *f = fopen("BLOCKS", "rb"); std::vector<uint8_t> src(16 * 16 * 8); fread(src.data(), 1, src.size(), f); fclose(f);
  const uint32_t w = 64, h = 32;
  int kinds[] = {1, 2, 3, 7}; int bad = 0, n = 0;
  for (int kind : kinds) {
    const size_t blk = kind == 1 ? 8 : 16, pitch = (w / 4) * blk;
    std::vector<uint8_t> full(w * h * 4), half((w / 2) * (h / 2) * 4);
    bc_decode_image(src.data(), pitch, full.data(), w, h, kind);
    bc_decode_image_half(src.data(), pitch, half.data(), w, h, kind);
    for (uint32_t y = 0; y < h / 2; y++)
      for (uint32_t x = 0; x < w / 2; x++)
        for (int c = 0; c < 4; c++) {
          auto at = [&](uint32_t xx, uint32_t yy) { return (unsigned)full[(yy * w + xx) * 4 + c]; };
          unsigned want = (at(2 * x, 2 * y) + at(2 * x + 1, 2 * y) + at(2 * x, 2 * y + 1) + at(2 * x + 1, 2 * y + 1) + 2) / 4;
          n++; if (half[(y * (w / 2) + x) * 4 + c] != want) bad++;
        }
  }
  printf("%d %d\n", n, bad);
}
'''.replace("BLOCKS", str(tmp / "blocks.bin")), extra=[tree / "dxmt/dxmt_bcn.cpp"])
            check(p is not None, "bc_decode_image_half and bc_decode_image build on the host with dxmt_bcn", err)
            if p:
                check(p.stdout.strip() == "8192 0", "the half decode is exactly the 2x2 box of the full decode (BC1, BC2, BC3, BC7)", p.stdout)

        # --- 5. the gates in the patched code ---
        cfg = dev[dev.index("GetMipClampAutoConfig() {"):dev.index("/* MadeiraCtl op 7")]
        check("c.level = !c.enabled ? 0 : std::max(opt.empty() ? 1u : mip_clamp::AutoLevel(opt.c_str()),\n"
              "                                        env_v.empty() ? 1u : mip_clamp::AutoLevel(env_v.c_str()));" in cfg
              and cfg.index("c.level =") > cfg.index("c.enabled = kMadeira32BitModule;"),
              "the level is 0 when the switch is off, 2 only for the value 2, after the on/off decision")
        check("!mip_clamp::AutoSizeEligibleAtLevel(width, height, cfg.level)" in dev and "AutoSizeEligible(width, height))" not in dev,
              "the auto clamp's size test takes the level")
        cre = dev[dev.index("HRESULT CreateDeviceTextureInternal("):]
        gate = cre[cre.index("if (!mip_bias && pInitialData && GetMipClampAutoConfig().level >= 2"):cre.index("if (BcHalfUnderPressure(&headroom))")]
        for part in ("!mip_bias", "pInitialData", "GetMipClampAutoConfig().level >= 2", "!finalDesc.MiscFlags",
                     "mip_clamp::HalfFormat((uint32_t)finalDesc.Format)",
                     "(finalDesc.Usage == D3D11_USAGE_DEFAULT || finalDesc.Usage == D3D11_USAGE_IMMUTABLE)",
                     "finalDesc.BindFlags == D3D11_BIND_SHADER_RESOURCE", "!finalDesc.CPUAccessFlags", "finalDesc.SampleDesc.Count <= 1",
                     "mip_clamp::SingleLevelHalfEligible(finalDesc.Width, finalDesc.Height, finalDesc.MipLevels, finalDesc.ArraySize)",
                     "!pDevice->GetMTLDevice().supportsBCTextureCompression()"):
            check(part in gate, f"a texture is halved only with {part}")
        check(cre.index("mip_bias = MipClampAutoBias(") < cre.index("if (!mip_bias && pInitialData") < cre.index("    if (mip_bias) {\n      physDesc.Width"),
              "the half decision comes after both clamps and before the clamp's own resize")
        body = cre[cre.index("if (BcHalfUnderPressure(&headroom)) {"):cre.index("    if (mip_bias) {\n      physDesc.Width")]
        check("scale_shift = 1;" in body and "info.width      = physDesc.Width;" in body and "info.height     = physDesc.Height;" in body
              and "physDesc.MipLevels" not in body and "finalDesc.Width =" not in body,
              "under pressure only the physical size halves (the descriptor the game reads stays logical)")
        press = dev[dev.index("BcHalfUnderPressure(int64_t *headroom_out) {"):dev.index("template <typename tag>\nHRESULT CreateDeviceTextureInternal(")]
        check("if (cfg.level < 2 || !cfg.threshold_mb)\n    return false;" in press and "mip_clamp::UnderPressure(headroom, cfg.threshold_mb)" in press
              and "g_auto_headroom_mb" in press, "the pressure test is the auto clamp's (same threshold and headroom reading)")
        check("  texture->setMipBias(mip_bias);\n  texture->setScaleShift(scale_shift);" in cre, "the texture learns it right after its mip bias")
        check("uint32_t scaleShift() const { return scale_shift_; }" in texh and "uint32_t scale_shift_ = 0;" in texh
              and "  texture->mip_bias_ = mip_bias_;\n  texture->scale_shift_ = scale_shift_;" in texc, "Texture keeps it and the clone copies it")
        dec = init[init.index("ResourceInitializer::initWithData("):]
        check("if (texture->scaleShift() == 1 && level == 0)\n        bc_decode_image_half((const uint8_t *)data, row_pitch, decoded.get(), "
              "width_sub * 2, height_sub * 2, bc_kind);\n      else\n        bc_decode_image(" in dec,
              "initWithData decodes the logical level to half size only for such a texture, level 0")
        g = dec.index("if (texture->scaleShift() && (!decoded || data != (const void *)decoded.get())) {")
        check(g < dec.index("size_t bytes_per_row_needed") and "return 0;" in dec[g:g + 260],
              "any other upload into such a texture is skipped before the copy is laid out")
        cp = ctx[ctx.index("  CopyTexture(TextureCopyCommand &&cmd) {"):]
        check(cp.index('BcHalfDropped("copy");') < cp.index("ml746: TRANSLATE MIP INDICES"),
              "the copy path drops a copy that reads or writes such a texture, before the mip-bias translation")
        check(ctx.count('if (dst->scaleShift()) {   /* madeira-bcd [bc-half]: logical-size data for a half-size texture */\n'
                        '        BcHalfDropped("update");\n        return;\n      }\n      /* ml745: TRANSLATE THE MIP INDEX THROUGH THE CLAMP.') == 2,
              "both update paths drop an update into such a texture, before the mip-bias translation")

# --- 6. the app ---
lst = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check('guard value == "0" || value == "1" || value == "2" else {\n                    throw RecommendationsError.policy("\\(path): '
      'MADEIRA_MIP_CLAMP_AUTO is 0, 1 or 2")' in lst, "the list policy takes 0, 1 or 2")
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
line = next((l for l in cat.splitlines() if 'key: "env.MADEIRA_MIP_CLAMP_AUTO"' in l), "")
check("2 (from build 681 on a GPU that cannot sample BC" in line and "[bc-half]" in line and "reads 2 as 1" in line,
      "the catalog note names value 2 and where it applies")
recs = json.loads((R / "recommended/recommendations.json").read_text())


def lines_of(rule):
    out = []
    for key in ("config", "smallRAMConfig"):
        for item in rule.get(key, []):
            if isinstance(item, dict):
                out.append((item["line"], item.get("minBuild"), item.get("maxBuild")))
            else:
                out.append((item, None, None))
    return out


for rid in ("shroom-and-gloom", "schedule-i", "nunholy"):
    rule = next((r for r in recs["rules"] if r["id"] == rid), None)
    got = lines_of(rule) if rule else []
    two = [(mn, mx) for l, mn, mx in got if l == "env.MADEIRA_MIP_CLAMP_AUTO = 2"]
    src = [(mn, mx) for l, mn, mx in got if l == "env.MADEIRA_D3D11_SRC = 1"]
    one = [(mn, mx) for l, mn, mx in got if l == "env.MADEIRA_MIP_CLAMP_AUTO = 1"]
    check(two == [(681, None)] and not src and all(mx is not None and mx <= 680 for mn, mx in one),
          f"{rid}: '= 2' from build 681, no env.MADEIRA_D3D11_SRC line, any '= 1' line ends at 680", got)
check("run: python3 tests/host/check-dxmt-bc-half.py" in (R / ".github/workflows/build-ipa.yml").read_text(), "CI runs this check")

# --- 7. value 2 keeps a GPU that samples BC on the committed d3d11.dll (2026-11-20) ---
for arch in ("arm64ec-windows", "i386-windows"):   # the 32-bit one is built in CI (a checkout without that step has none)
    f = R / "app/Madeira" / arch / "d3d11.dll"
    if arch == "i386-windows" and not f.is_file():
        print("SKIP the 32-bit d3d11.dll: not built in this checkout")
        continue
    dll = f.read_bytes() if f.is_file() else b""
    check(b"MADEIRA_MIP_CLAMP_AUTO" in dll and b"ml2000 auto on" in dll and b"[bc-half]" not in dll,
          f"the committed {arch} d3d11.dll has the ml2000 clamp and no level 2")
pinned = (SRC / "d3d11/d3d11_texture_device.cpp").read_text() if (SRC / "d3d11/d3d11_texture_device.cpp").is_file() else None
if pinned is not None:
    check("if (mip_clamp::IsOffValue(opt.c_str()) || mip_clamp::IsOffValue(env_v.c_str()))\n      c.enabled = false;\n"
          "    else if (!opt.empty() || !env_v.empty())\n      c.enabled = true;" in pinned,
          "the pinned clamp turns on for any value that is not 0/false/off/no")
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
fa = bridge.find("static bool bcd_mip_clamp_auto_level2(const char *v)")
fb = bridge.find("\n}\n", fa)
check(fa > 0 and fb > fa, "WineProcessBridge.m reads the level with bcd_mip_clamp_auto_level2")
ga = bridge.find("static bool bcd_gpu_samples_bc(void)")
gb = bridge.find("\n}\n", ga)
check(ga > 0 and "device.supportsBCTextureCompression" in bridge[ga:gb] and "MTLCreateSystemDefaultDevice()" in bridge[ga:gb],
      "bcd_gpu_samples_bc asks Metal's supportsBCTextureCompression, as DXMT does")
ka = bridge.find('const char *d3d11Src = getenv("MADEIRA_D3D11_SRC");')
kb = bridge.find("upstream's d3d11.dll stays", ka)
blk = bridge[ka:kb] if ka > 0 and kb > ka else ""
check('if (bcd_mip_clamp_auto_level2(getenv("MADEIRA_MIP_CLAMP_AUTO"))) {\n                    if (d3d11Src)' in blk
      and "else if (bcd_gpu_samples_bc())" in blk and "else\n                        halfBC = true;" in blk
      and blk.count("halfBC = true") == 1 and "if ((d3d11Src && d3d11Src[0] == '1') || halfBC) {" in blk,
      "d3d11-src.dll for value 2 only without the game's own MADEIRA_D3D11_SRC and only on a GPU that cannot sample BC")
check("MADEIRA_MIP_CLAMP_AUTO=2 on a GPU without BC sampling: d3d11.dll -> d3d11-src.dll" in blk
      and "MADEIRA_MIP_CLAMP_AUTO=2 on a GPU that samples BC textures: the committed d3d11.dll is kept" in blk
      and "MADEIRA_D3D11_SRC=1: d3d11.dll -> d3d11-src.dll (DXMT source build" in blk, "each case says so in the log")
if cxx and fa > 0 and fb > fa:
    with tempfile.TemporaryDirectory(prefix="bc-half-app-") as tmp:
        tmp = pathlib.Path(tmp)
        p, err = compile_run(tmp, "level2", "#include <cstdio>\n" + bridge[fa:fb + 2] + r'''
int main() {
  const char *v[] = {"2", " 2 ", "1", "on", "22", "", "0", "2x", "\t2"};
  for (auto s : v) printf("%d", bcd_mip_clamp_auto_level2(s));
  printf(" %d\n", bcd_mip_clamp_auto_level2(nullptr));
}
''')
        # AutoLevel gives 221111112 and 1 for nullptr (check 3): level 2 exactly where it gives 2.
        check(p is not None and p.stdout.strip() == "110000001 0", "the app reads 2 exactly where DXMT's AutoLevel does", (p.stdout if p else err))

print(("PASS" if not failures else "FAILED") + ": single-level BC textures at half size (MADEIRA_MIP_CLAMP_AUTO = 2)")
sys.exit(1 if failures else 0)
