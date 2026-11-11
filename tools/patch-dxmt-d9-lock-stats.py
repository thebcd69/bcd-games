#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: pixel statistics of what a game writes into an A8R8G8B8 surface, logged at UnlockRect (diagnostic, log only;
night round 2026-11-11, G1 S2).

Requiem: Avenging Angel (K21Z9Y build 630, H3RW0Y build 625; Steam 493310, 32-bit 3DFX.exe with a Glide wrapper that draws through
Direct3D 9) shows black rectangles where its UI sprites should be. Every sprite is two textures of the same size locked once and
written fully (a SYSTEMMEM A8R8G8B8 "cpu master, no upload" and a DEFAULT|DYNAMIC one that uploads its locked rect on unlock); the
[d9-lock] lines, the GPU trace and the formats show nothing wrong, so the open question is whether the wrapper WRITES black sprites
(its conversion buffer is a write-watched allocation BCD services page by page) or the draw turns good data black.

With env.DXMT_DUMP_D9_SHADER set (the diagnostic key the game's row already carries; no new name), the first 400 unlocks of a
writable A8R8G8B8/X8R8G8B8 surface of 1024 pixels or more print one line, taken from the CPU mirror before the upload:
  [d9-stat] madeira-bcd #N pool=P usage=U WxH rect=x,y wxh nz=<dwords != 0> rgb0=<alpha != 0 and RGB == 0> a0=<alpha == 0>
            a255=<alpha == 255> fnv=<FNV-1a 64 of the first 4096 bytes of the rect, row by row>
For each sprite the SYSTEMMEM write and the DEFAULT write (same size, same order) then show whether the wrapper's data is black
(rgb0 close to w*h, nz small), whether the two agree (fnv), and whether a write is fully transparent (a0 = w*h). Read-only: nothing is
written to the surface, the upload and everything after it are untouched; nothing is logged or computed without the key. Idempotent;
fails by name when the anchor moved. Reaches the 32-bit d3d9-emulated.dll (the CI's DXMT patch steps; Requiem is 32-bit) and the
64-bit d3d9-dxmt.dll built from the patched tree. Run from the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: d9-lock-stats"
REL = "d3d9/d3d9_surface.cpp"

OLD = """  d9LogLockPolicy(m_desc, m_locked_w, m_locked_h, m_locked_readonly, m_locked_no_dirty_update, defer_no_dirty);
"""
NEW = """  d9LogLockPolicy(m_desc, m_locked_w, m_locked_h, m_locked_readonly, m_locked_no_dirty_update, defer_no_dirty);
  /* """ + MARKER + """ (night round 2026-11-11, Requiem K21Z9Y / H3RW0Y, black UI rectangles): with DXMT_DUMP_D9_SHADER set, pixel statistics of
   * a written A8R8G8B8 / X8R8G8B8 rect, read from the CPU mirror before the upload below (which may reclaim it). Log only. */
  if (m_cpu_ptr != nullptr && !m_locked_readonly && m_locked_w * m_locked_h >= 1024 && m_locked_w <= m_desc.Width &&
      m_locked_h <= m_desc.Height && (m_desc.Format == D3DFMT_A8R8G8B8 || m_desc.Format == D3DFMT_X8R8G8B8)) {
    static const bool stats_on = [] {
      const std::string env_v = env::getEnvVar("DXMT_DUMP_D9_SHADER");
      return !env_v.empty() && env_v != "0";
    }();
    static std::atomic<unsigned> stats_n{0};
    if (stats_on && stats_n.load(std::memory_order_relaxed) < 400) {
      unsigned n = ++stats_n;
      if (n <= 400) {
        const uint8_t *base = static_cast<const uint8_t *>(m_cpu_ptr) + (size_t)m_locked_y * m_pitch + (size_t)m_locked_x * 4u;
        uint64_t nz = 0, rgb0 = 0, a0 = 0, a255 = 0, hash = 0xcbf29ce484222325ull;
        size_t hashed = 0;
        for (uint32_t y = 0; y < m_locked_h; ++y) {
          const uint8_t *row = base + (size_t)y * m_pitch;
          for (uint32_t x = 0; x < m_locked_w; ++x) {
            uint32_t px;
            std::memcpy(&px, row + (size_t)x * 4u, sizeof(px));
            const uint32_t a = px >> 24;
            nz += px != 0;
            rgb0 += a != 0 && (px & 0x00ffffffu) == 0;
            a0 += a == 0;
            a255 += a == 255;
          }
          for (uint32_t i = 0; i < m_locked_w * 4u && hashed < 4096; ++i, ++hashed)
            hash = (hash ^ row[i]) * 0x100000001b3ull;
        }
        Logger::info(str::format(
            "[d9-stat] madeira-bcd #", n, " pool=", static_cast<uint32_t>(m_desc.Pool), " usage=", m_desc.Usage, " ", m_desc.Width, "x",
            m_desc.Height, " rect=", m_locked_x, ",", m_locked_y, " ", m_locked_w, "x", m_locked_h, " nz=", nz, " rgb0=", rgb0, " a0=", a0,
            " a255=", a255, " fnv=", std::hex, hash
        ));
      }
    }
  }
"""


def main():
    path = ROOT / REL
    if not path.exists():
        sys.exit(f"patch-dxmt-d9-lock-stats: {path} not found (run from the repository root)")
    s = path.read_text()
    if MARKER in s:
        print(f"{REL}: already patched")
        return
    if s.count(OLD) != 1:
        sys.exit(f"patch-dxmt-d9-lock-stats: the d9LogLockPolicy call in UnlockRect was not found exactly once in {REL} (DXMT changed)")
    s = s.replace(OLD, NEW, 1)
    for inc in ("#include <atomic>", "#include <cstring>"):
        if inc not in s:
            first = s.index("#include")
            s = s[:first] + inc + "\n" + s[first:]
    path.write_text(s)
    print(f"{REL}: patched")


main()
