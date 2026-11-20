#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Value 2 of d3d11.mipClampAuto / MADEIRA_MIP_CLAMP_AUTO: single-level BC textures at half size and the auto clamp from 256 px,
while the game is close to its memory limit, on a GPU without BC (round 677, agent G3 S1; the owner's decision of 2026-11-19).

On a GPU that cannot sample BC (A13-A16 phones) DXMT creates every BC texture uncompressed and decodes it at upload: RGBA8, four
times BC3/BC7 and eight times BC1. The two relief valves only drop top mip levels: d3d11.mipClampBC=N for every texture with two
levels or more, MADEIRA_MIP_CLAMP_AUTO = 1 for those with a side of 1,024 px or more once the process is within
d3d11.mipClampAutoMB (1,536 MB) of its limit. Shroom and Gloom's textures are single-level BC7 sprites (958 of 960 have one level:
Send Logs 7RDHAH, 8GQH2M, E6SKGK, 4354GN, build 665, killed at 4,043-4,087 of 4,095 MB, the clamp touched two); Schedule I
(EM08Q8, killed at 4,077 MB, the value-1 line clamped 3 of 1,088) and Nunholy (DBG514, 4,090 MB) have mostly 256-512 px ones.

With the value 2 (and nothing else; 0, 1 and unset are unchanged, and the 32-bit module's default stays 1):
  * the auto clamp considers textures from 256 px on a side (mip_clamp::AutoSizeEligibleAtLevel), under the same pressure test;
  * a texture that is BC1/BC2/BC3/BC7, created with initial data, Usage DEFAULT or IMMUTABLE, bound only as a shader resource,
    without CPU access, misc flags or multisampling, with one level and one slice, both sides multiples of 4 and at least 65,536
    texels (mip_clamp::SingleLevelHalfEligible), on a GPU without BC sampling, created while the process is within the threshold of
    its limit, gets a Metal texture of half its width and height (Texture::scaleShift() == 1). The D3D descriptor stays logical
    (GetDesc tells the game the real size); initWithData decodes the caller's full-size blocks straight to half size with the
    existing bc_decode_image_half (a 2x2 box per 4x4 block; ml1256's refill workers use it already). Sampling with normalised
    coordinates is unchanged; the texture is blurrier.
  * a copy or an update that would write into such a texture (or read from it) is dropped and counted ("[bc-half] madeira-bcd:
    copy on a half-size texture dropped (#n)"): every census of these games shows no runtime operation on BC textures, and only
    textures created with their data qualify.
Every halved texture is logged with the clamp's rate ("[bc-half] madeira-bcd #n single-level WxH format=F -> physical W/2xH/2
headroom=N MB": the first 8, then every 64th). The config line says the level ("[bc-half] madeira-bcd level 2: ...").

d3d11-src.dll only (tools/build-d3d11-dll.sh, BCD_PATCHES; a 64-bit game gets it with env.MADEIRA_D3D11_SRC = 1): the committed
64-bit d3d11.dll reads 2 as 1, as it reads any value that is not off. Idempotent; fails by name when an anchor moved. Run in the
directory whose "dxmt" is the tree to patch.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd [bc-half]"
NAME = "patch-dxmt-bc-half"

POLICY = "d3d11/d3d11_mip_clamp_policy.hpp"
DEVICE = "d3d11/d3d11_texture_device.cpp"
TEX_H = "dxmt/dxmt_texture.hpp"
TEX_C = "dxmt/dxmt_texture.cpp"
INIT = "dxmt/dxmt_resource_initializer.cpp"
CTX = "d3d11/d3d11_context_impl.cpp"

EDITS = {}

EDITS[POLICY] = [(
    """inline bool AutoSizeEligible(uint32_t width, uint32_t height) {
  return width >= kAutoMinDimension || height >= kAutoMinDimension;
}
""",
    """inline bool AutoSizeEligible(uint32_t width, uint32_t height) {
  return width >= kAutoMinDimension || height >= kAutoMinDimension;
}

/* """ + MARKER + """ (round 677, G3 S1): level 2 of d3d11.mipClampAuto / MADEIRA_MIP_CLAMP_AUTO ("2"; any other value that is
 * not off is level 1, as before). Level 2 lets the auto clamp consider textures from 256 px on a side, and creates a single-level
 * BC texture at half its width and height (the Metal texture only; the D3D descriptor stays logical) under the same pressure. */
constexpr uint32_t kAutoMinDimensionLevel2 = 256;
constexpr uint64_t kHalfMinTexels = 256u * 256u;

inline uint32_t AutoLevel(const char *v) {
  if (!v)
    return 1;
  while (*v == ' ' || *v == '\\t')
    v++;
  if (*v != '2')
    return 1;
  v++;
  while (*v == ' ' || *v == '\\t')
    v++;
  return *v ? 1 : 2;
}

inline bool AutoSizeEligibleAtLevel(uint32_t width, uint32_t height, uint32_t level) {
  const uint32_t min_dim = level >= 2 ? kAutoMinDimensionLevel2 : kAutoMinDimension;
  return width >= min_dim || height >= min_dim;
}

/* One level, one slice, both sides whole 4x4 blocks (bc_decode_image_half halves a block into 2x2 texels) and at least
 * 65,536 texels: a smaller texture is not worth the blur. */
inline bool SingleLevelHalfEligible(uint32_t width, uint32_t height, uint32_t mip_levels, uint32_t array_size) {
  return mip_levels == 1 && array_size == 1 && width >= 8 && height >= 8 && !(width & 3u) && !(height & 3u) &&
         (uint64_t)width * height >= kHalfMinTexels;
}

/* BC1, BC2, BC3 and BC7 (typeless, UNORM, sRGB): DXGI 70-78 and 97-99, the formats that decode to RGBA8. */
inline bool HalfFormat(uint32_t dxgi_format) {
  return (dxgi_format >= 70 && dxgi_format <= 78) || (dxgi_format >= 97 && dxgi_format <= 99);
}
""")]

EDITS[DEVICE] = [
    ("""struct MipClampAutoConfig {
  bool enabled;
  uint32_t threshold_mb;
};
""",
     """struct MipClampAutoConfig {
  bool enabled;
  uint32_t threshold_mb;
  uint32_t level = 0;   /* """ + MARKER + """: 1, or 2 for the value "2"; 0 when off */
};
"""),
    ("""    c.threshold_mb = (uint32_t)std::max(0, Config::getInstance().getOption<int>("d3d11.mipClampAutoMB", 1536));
""",
     """    c.threshold_mb = (uint32_t)std::max(0, Config::getInstance().getOption<int>("d3d11.mipClampAutoMB", 1536));
    /* """ + MARKER + """ */
    c.level = !c.enabled ? 0 : std::max(opt.empty() ? 1u : mip_clamp::AutoLevel(opt.c_str()),
                                        env_v.empty() ? 1u : mip_clamp::AutoLevel(env_v.c_str()));
    if (c.level >= 2)
      ERR("[bc-half] madeira-bcd level 2: the auto clamp from ", mip_clamp::kAutoMinDimensionLevel2,
          " px, and single-level BC textures at half size on a GPU without BC, within ", c.threshold_mb, " MB of the limit");
"""),
    ("""  if (!cfg.enabled || !cfg.threshold_mb || !mip_clamp::AutoSizeEligible(width, height))
    return 0;
""",
     """  if (!cfg.enabled || !cfg.threshold_mb || !mip_clamp::AutoSizeEligibleAtLevel(width, height, cfg.level))   /* """ + MARKER + """ */
    return 0;
"""),
    ("""template <typename tag>
HRESULT CreateDeviceTextureInternal(MTLD3D11Device *pDevice,
""",
     """/* """ + MARKER + """ (round 677, G3 S1): the pressure test of MipClampAutoBias for a single-level BC texture at level 2 (the
 * same counters and headroom reading, so both decide from one measurement). */
static std::atomic<uint64_t> g_half_count{0};

static bool
BcHalfUnderPressure(int64_t *headroom_out) {
  const MipClampAutoConfig &cfg = GetMipClampAutoConfig();
  if (cfg.level < 2 || !cfg.threshold_mb)
    return false;
  const uint64_t n = g_auto_candidates.fetch_add(1, std::memory_order_relaxed) + 1;
  int64_t headroom = g_auto_headroom_mb.load(std::memory_order_relaxed);
  if (mip_clamp::ShouldRequery(n, headroom, cfg.threshold_mb)) {
    int64_t foot = 0;
    const int64_t fresh = QueryHeadroomMB(&foot);
    if (fresh == mip_clamp::kHeadroomUnavailable && headroom == mip_clamp::kHeadroomUnknown)
      ERR("[bc-half] madeira-bcd: memory headroom unavailable here; single-level BC textures stay full size");
    headroom = fresh;
    g_auto_headroom_mb.store(headroom, std::memory_order_relaxed);
    g_auto_footprint_mb.store(foot, std::memory_order_relaxed);
  }
  *headroom_out = headroom;
  return mip_clamp::UnderPressure(headroom, cfg.threshold_mb);
}

template <typename tag>
HRESULT CreateDeviceTextureInternal(MTLD3D11Device *pDevice,
"""),
    ("""  uint32_t mip_bias = 0;
  typename tag::DESC1 physDesc = finalDesc;
""",
     """  uint32_t mip_bias = 0;
  uint32_t scale_shift = 0;   /* """ + MARKER + """: 1 = the Metal texture is half the logical width and height */
  typename tag::DESC1 physDesc = finalDesc;
"""),
    ("""      mip_bias = MipClampAutoBias(finalDesc.Width, finalDesc.Height, finalDesc.MipLevels);
    }
    if (mip_bias) {
""",
     """      mip_bias = MipClampAutoBias(finalDesc.Width, finalDesc.Height, finalDesc.MipLevels);
    }
    /* """ + MARKER + """ (round 677, G3 S1: Shroom and Gloom's single-level BC7 sprites, which no clamp could shrink): level 2
     * only, a texture whose whole content arrives here and is never written again (initial data, shader resource only, no CPU
     * access, no misc flag), on a GPU that decodes BC on the CPU, while the process is within the threshold of its limit. Only
     * `info` shrinks: the descriptor the game reads stays logical, initWithData decodes the full-size blocks to half size, and
     * the context drops a copy or update that touches the texture. */
    if (!mip_bias && pInitialData && GetMipClampAutoConfig().level >= 2 && !finalDesc.MiscFlags &&
        mip_clamp::HalfFormat((uint32_t)finalDesc.Format) &&
        (finalDesc.Usage == D3D11_USAGE_DEFAULT || finalDesc.Usage == D3D11_USAGE_IMMUTABLE) &&
        finalDesc.BindFlags == D3D11_BIND_SHADER_RESOURCE && !finalDesc.CPUAccessFlags &&
        finalDesc.SampleDesc.Count <= 1 &&
        mip_clamp::SingleLevelHalfEligible(finalDesc.Width, finalDesc.Height, finalDesc.MipLevels, finalDesc.ArraySize) &&
        !pDevice->GetMTLDevice().supportsBCTextureCompression()) {
      int64_t headroom = 0;
      if (BcHalfUnderPressure(&headroom)) {
        scale_shift = 1;
        physDesc.Width  = finalDesc.Width >> 1;
        physDesc.Height = finalDesc.Height >> 1;
        info.width      = physDesc.Width;
        info.height     = physDesc.Height;
        const uint64_t n = g_half_count.fetch_add(1, std::memory_order_relaxed) + 1;
        if (mip_clamp::ShouldLogClamp(n))
          ERR("[bc-half] madeira-bcd #", n, " single-level ", finalDesc.Width, "x", finalDesc.Height, " format=",
              (uint32_t)finalDesc.Format, " -> physical ", physDesc.Width, "x", physDesc.Height, " headroom=", headroom, " MB");
      }
    }
    if (mip_bias) {
"""),
    ("""  texture->setMipBias(mip_bias);

  auto &initializer = pDevice->GetDXMTDevice().queue().initializer;
""",
     """  texture->setMipBias(mip_bias);
  texture->setScaleShift(scale_shift);   /* """ + MARKER + """ */

  auto &initializer = pDevice->GetDXMTDevice().queue().initializer;
"""),
]

EDITS[TEX_H] = [
    ("""  void setMipBias(uint32_t bias) { mip_bias_ = bias; }
""",
     """  void setMipBias(uint32_t bias) { mip_bias_ = bias; }
  /* """ + MARKER + """ (round 677): 1 when the Metal texture is half the logical width and height (level 2 of
   * d3d11.mipClampAuto, a single-level BC texture created with its data). initWithData decodes the logical blocks to half size;
   * the context drops copies and updates that touch such a texture. */
  uint32_t scaleShift() const { return scale_shift_; }
  void setScaleShift(uint32_t shift) { scale_shift_ = shift; }
"""),
    ("""  uint32_t mip_bias_ = 0;          /* ml745, see mipBias() */
""",
     """  uint32_t mip_bias_ = 0;          /* ml745, see mipBias() */
  uint32_t scale_shift_ = 0;       /* """ + MARKER + """, see scaleShift() */
"""),
]

EDITS[TEX_C] = [(
    """  texture->mip_bias_ = mip_bias_;
""",
    """  texture->mip_bias_ = mip_bias_;
  texture->scale_shift_ = scale_shift_;   /* """ + MARKER + """ */
""")]

EDITS[INIT] = [
    ("""    if (decoded) {
      bc_decode_image((const uint8_t *)data, row_pitch, decoded.get(), width_sub, height_sub, bc_kind);
""",
     """    if (decoded) {
      /* """ + MARKER + """: the caller's data is the LOGICAL level, twice the physical width and height. */
      if (texture->scaleShift() == 1 && level == 0)
        bc_decode_image_half((const uint8_t *)data, row_pitch, decoded.get(), width_sub * 2, height_sub * 2, bc_kind);
      else
        bc_decode_image((const uint8_t *)data, row_pitch, decoded.get(), width_sub, height_sub, bc_kind);
"""),
    ("""  size_t bytes_per_row_needed = texel_size * align(width_sub, block_size) / block_size;
  size_t bytes_per_row_increment = is_1d_tex ? bytes_per_row_needed : row_pitch;
""",
     """  /* """ + MARKER + """: a half-size texture takes only the half-size decode above; anything else (no decode, out of memory)
   * would be logical-size data for a smaller texture. */
  if (texture->scaleShift() && (!decoded || data != (const void *)decoded.get())) {
    ERR("[bc-half] madeira-bcd: upload of a half-size texture skipped (level ", level, ", no half-size decode)");
    return 0;
  }
  size_t bytes_per_row_needed = texel_size * align(width_sub, block_size) / block_size;
  size_t bytes_per_row_increment = is_1d_tex ? bytes_per_row_needed : row_pitch;
"""),
]

UPDATE_OLD = """    if (auto dst = GetTexture(cmd.pDst)) {
      /* ml745: TRANSLATE THE MIP INDEX THROUGH THE CLAMP.
"""
UPDATE_NEW = """    if (auto dst = GetTexture(cmd.pDst)) {
      if (dst->scaleShift()) {   /* """ + MARKER + """: logical-size data for a half-size texture */
        BcHalfDropped("update");
        return;
      }
      /* ml745: TRANSLATE THE MIP INDEX THROUGH THE CLAMP.
"""
EDITS[CTX] = [
    ("""inline std::atomic<uint32_t> g_cp_dropped{0};     /* logical mip below a bias  */
""",
     """inline std::atomic<uint32_t> g_cp_dropped{0};     /* logical mip below a bias  */
/* """ + MARKER + """ (round 677): a copy or update that touches a half-size texture (Texture::scaleShift) is dropped: its
 * extents are logical, the texture is not. Only textures created with their data and never written again are halved. */
inline std::atomic<uint32_t> g_half_dropped{0};
inline void
BcHalfDropped(const char *op) {
  const uint32_t n = g_half_dropped.fetch_add(1, std::memory_order_relaxed) + 1;
  if (n <= 8 || (n % 64) == 0)
    ERR("[bc-half] madeira-bcd: ", op, " on a half-size texture dropped (#", n, ")");
}
"""),
    ("""  CopyTexture(TextureCopyCommand &&cmd) {
    if (cmd.Invalid)
      return;
""",
     """  CopyTexture(TextureCopyCommand &&cmd) {
    if (cmd.Invalid)
      return;

    {   /* """ + MARKER + """ */
      auto half_src = GetTexture(cmd.pSrc);
      auto half_dst = GetTexture(cmd.pDst);
      if ((half_src && half_src->scaleShift()) || (half_dst && half_dst->scaleShift())) {
        BcHalfDropped("copy");
        return;
      }
    }
"""),
]
CTX_UPDATE_COUNT = 2


def main():
    texts = {}
    for rel in EDITS:
        path = ROOT / rel
        if not path.exists():
            sys.exit(f"{NAME}: {path} not found (run in the directory whose dxmt is the tree to patch)")
        texts[rel] = path.read_text()
    done = [rel for rel, s in texts.items() if MARKER in s]
    if len(done) == len(EDITS):
        print(f"{NAME}: already patched")
        return
    if done:
        sys.exit(f"{NAME}: only partly patched ({', '.join(done)}): start from an unpatched tree")
    for rel, pairs in EDITS.items():
        for old, _ in pairs:
            if texts[rel].count(old) != 1:
                sys.exit(f"{NAME}: an anchor was not found exactly once in {rel} (DXMT changed): " + old.strip().splitlines()[0].strip())
    if texts[CTX].count(UPDATE_OLD) != CTX_UPDATE_COUNT:
        sys.exit(f"{NAME}: UpdateTexture's mip-bias block is not in {CTX} exactly {CTX_UPDATE_COUNT} times (DXMT changed)")
    if "bc_decode_image_half(" not in texts[INIT]:
        sys.exit(f"{NAME}: bc_decode_image_half is not in {INIT} (DXMT changed)")
    for rel, pairs in EDITS.items():
        for old, new in pairs:
            texts[rel] = texts[rel].replace(old, new, 1)
    texts[CTX] = texts[CTX].replace(UPDATE_OLD, UPDATE_NEW)
    if "#include <atomic>" not in texts[DEVICE]:
        first = texts[DEVICE].index("#include")
        texts[DEVICE] = texts[DEVICE][:first] + "#include <atomic>\n" + texts[DEVICE][first:]
    for rel, s in texts.items():
        (ROOT / rel).write_text(s)
    print(f"{NAME}: patched " + ", ".join(EDITS))


main()
