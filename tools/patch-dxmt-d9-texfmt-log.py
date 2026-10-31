#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Which texture formats a Direct3D 9 game creates, and what Metal format each became (round 624, Discord forum item 11).

Three 32-bit Direct3D 9 games on DXMT draw their scene with wrong textures: LEGO Batman (the picture "does not display
correctly"), Silent Hunter 3 ("pink popping textures") and Requiem: Avenging Angel (whole UI sprites solid black). The
[d3d9-census] and the shader dumps do not say which texture formats the game asked for: a 16-bit format with a sampler swizzle
(A4R4G4B4, A1R5G5B5), a palettised P8 Metal cannot create, a compressed DXTn, a luminance format, a float one. Now
CreateTexture, CreateCubeTexture and CreateVolumeTexture report each distinct (kind, format, pool, usage) the first time it is
created and again at its 100th and 1000th creation, 40 lines at most a process:

  [d3d9-tex-fmt] 2D fmt=A4R4G4B4(26) pool=MANAGED usage=0x0 metal=ABGR4Unorm-or-number(42) first=256x256x1 levels=1 count=1

A format DXMT cannot create shows metal=0 (the create then fails as before). Part of the D3D9 census (on by default,
MADEIRA_D3D9_CENSUS=0 turns it off with the rest); one short lock per texture creation, never on a draw. Nothing else changes.
PE side (the i386 farm key hashes tools/patch-dxmt-*.py). Idempotent (marker "d9-texfmt-v1"); exits 1 naming the anchor when
one moved, the files unchanged. Run from the repository root.
"""
import pathlib
import sys

D = pathlib.Path("dxmt/src/d3d9")
MARKER = "d9-texfmt-v1"

HPP = D / "d3d9_census.hpp"
HPP_OLD = """void queryIssued();                   /* Issue(D3DISSUE_END) on EVENT/OCCLUSION */
"""
HPP_NEW = """/* madeira-bcd d9-texfmt-v1 (round 624; tools/patch-dxmt-d9-texfmt-log.py): a texture of kind 0 (2D), 1 (cube) or 2 (volume) was
 * asked for with this format, pool and usage and lowered to this Metal pixel format (0: none, the create fails). [d3d9-tex-fmt]
 * the first time a (kind, format, pool, usage) is seen and at its 100th and 1000th creation, 40 lines at most. */
void textureFormat(unsigned kind, unsigned format, unsigned pool, unsigned usage, unsigned metal, unsigned width, unsigned height,
                   unsigned depth, unsigned levels);
void queryIssued();                   /* Issue(D3DISSUE_END) on EVENT/OCCLUSION */
"""

CPP = D / "d3d9_census.cpp"
CPP_OLD = """void queryIssued() {
"""
CPP_NEW = """/* madeira-bcd d9-texfmt-v1: see d3d9_census.hpp. Texture creation is rare next to draws: a spinlock and a 64-entry table. */
void textureFormat(unsigned kind, unsigned format, unsigned pool, unsigned usage, unsigned metal, unsigned width, unsigned height,
                   unsigned depth, unsigned levels) {
  struct Seen {
    unsigned kind, format, pool, usage;
    uint32_t count;
  };
  static Seen seen[64];
  static unsigned nseen, lines;
  static std::atomic<bool> busy{false};
  static const char *const kinds[3] = {"2D", "cube", "volume"};
  static const char *const pools[4] = {"DEFAULT", "MANAGED", "SYSTEMMEM", "SCRATCH"};
  char name[16], buf[256];
  unsigned i;
  uint32_t n;

  if (!g_on)
    return;
  while (busy.exchange(true, std::memory_order_acquire))
    ;
  for (i = 0; i < nseen; i++)
    if (seen[i].kind == kind && seen[i].format == format && seen[i].pool == pool && seen[i].usage == usage)
      break;
  if (i == nseen) {
    if (nseen == 64) {
      busy.store(false, std::memory_order_release);
      return;
    }
    seen[nseen++] = Seen{kind, format, pool, usage, 0};
  }
  n = ++seen[i].count;
  if ((n != 1 && n != 100 && n != 1000) || lines >= 40) {
    busy.store(false, std::memory_order_release);
    return;
  }
  lines++;
  busy.store(false, std::memory_order_release);
  /* a FOURCC (DXT1, ATI2, ...) as its four letters, every other format as its number */
  if (format > 0xff && ((format & 0xff) >= 0x20 && (format & 0xff) < 0x7f) && (((format >> 8) & 0xff) >= 0x20 && ((format >> 8) & 0xff) < 0x7f) &&
      (((format >> 16) & 0xff) >= 0x20 && ((format >> 16) & 0xff) < 0x7f) && ((format >> 24) >= 0x20 && (format >> 24) < 0x7f))
    snprintf(name, sizeof(name), "%c%c%c%c", (char)(format & 0xff), (char)((format >> 8) & 0xff), (char)((format >> 16) & 0xff),
             (char)(format >> 24));
  else
    snprintf(name, sizeof(name), "%u", format);
  snprintf(buf, sizeof(buf), "%s fmt=%s pool=%s usage=%#x metal=%u%s first=%ux%ux%u levels=%u count=%u", kind < 3 ? kinds[kind] : "?",
           name, pool < 4 ? pools[pool] : "?", usage, metal, metal ? "" : " (none: the create fails)", width, height, depth, levels,
           (unsigned)n);
  Logger::info(std::string("[d3d9-tex-fmt] ") + buf);
}

void queryIssued() {
"""

DEV = D / "d3d9_device.cpp"
SITES = [
    ("CreateTexture", "MTLD3D9Device::CreateTexture(\n",
     """  if (IsNullFormat(Format) && (Usage & D3DUSAGE_RENDERTARGET))
    pixelFormat = WMTPixelFormatBGRA8Unorm;
""",
     """  census::textureFormat(0, (unsigned)Format, (unsigned)Pool, (unsigned)Usage, (unsigned)pixelFormat, Width, Height, 1,
                        Levels);   /* madeira-bcd d9-texfmt-v1 */
"""),
    ("CreateVolumeTexture", "MTLD3D9Device::CreateVolumeTexture(\n",
     """  WMTPixelFormat pixelFormat = D3DFormatToMetal(Format, D3D9FormatUsage::SampleableTexture);
""",
     """  census::textureFormat(2, (unsigned)Format, (unsigned)Pool, (unsigned)Usage, (unsigned)pixelFormat, Width, Height, Depth,
                        Levels);   /* madeira-bcd d9-texfmt-v1 */
"""),
    ("CreateCubeTexture", "MTLD3D9Device::CreateCubeTexture(\n",
     """  WMTPixelFormat pixelFormat = D3DFormatToMetal(Format, formatUsage);
""",
     """  census::textureFormat(1, (unsigned)Format, (unsigned)Pool, (unsigned)Usage, (unsigned)pixelFormat, EdgeLength, EdgeLength, 6,
                        Levels);   /* madeira-bcd d9-texfmt-v1 */
"""),
]


def patch_device(text):
    for name, sig, after, insert in SITES:
        at = text.find(sig)
        if at < 0 or text.find(sig, at + 1) >= 0:
            raise ValueError(f"d3d9_device.cpp {name}: the function is not there once")
        end = text.index("\n}\n", at)
        body = text[at:end]
        if body.count(after) != 1:
            raise ValueError(f"d3d9_device.cpp {name}: the format lowering anchor found {body.count(after)} times (want 1)")
        body = body.replace(after, after + insert, 1)
        text = text[:at] + body + text[end:]
    return text


def main():
    texts = {p: p.read_text() for p in (HPP, CPP, DEV)}
    marked = [MARKER in t for t in texts.values()]
    if all(marked):
        print("d3d9 texture formats: already patched")
        return 0
    if any(marked):
        print("::error::patch-dxmt-d9-texfmt-log: only some files carry the marker -- dxmt moved, review this patch")
        return 1
    try:
        if texts[HPP].count(HPP_OLD) != 1:
            raise ValueError(f"d3d9_census.hpp: the query hooks found {texts[HPP].count(HPP_OLD)} times (want 1)")
        if texts[CPP].count(CPP_OLD) != 1:
            raise ValueError(f"d3d9_census.cpp: queryIssued found {texts[CPP].count(CPP_OLD)} times (want 1)")
        dev = patch_device(texts[DEV])
    except ValueError as e:
        print(f"::error::patch-dxmt-d9-texfmt-log: {e} -- dxmt moved, review this patch")
        return 1
    HPP.write_text(texts[HPP].replace(HPP_OLD, HPP_NEW, 1))
    CPP.write_text(texts[CPP].replace(CPP_OLD, CPP_NEW, 1))
    DEV.write_text(dev)
    print("d3d9 texture formats patched ([d3d9-tex-fmt], part of the D3D9 census)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
