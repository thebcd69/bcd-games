#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
"""Live SDR/HDR comparison, calibration pattern and tagged drawable measurements.

Apply after patch-dxmt-auto-hdr.py. --presenter-dir patches the private ARM64EC
core copy too. MadeiraCtl's existing fixed layout/export is unchanged; ops 60/61
carry only integers, a native drawable handle and inline float bits, also on i386.
"""
import argparse
from pathlib import Path
import sys

MARKER = "madeira-bcd: HDR live v2"
CONTROL = '''  // madeira-bcd: HDR live v2. The layer stays float/EDR for a stable A/B
  // reference: SDR comparison uses peak 1 through the same linear output path.
  uint32_t hdr_flags = 1, hdr_revision = 0;
  if (auto_hdr_requested_) {
    madeira_ctl_args live = {};
    live.op = 60;
    MadeiraCtl(&live);
    if ((live.ret & 0xffffff00u) == 0x48445200u) {
      hdr_flags = live.ret & 7u;
      hdr_revision = (uint32_t)live.len;
    }
  }
'''
TAG = '''  // Tag this exact drawable with the uniforms used for its game frame.
  // A toggle invalidates older in-flight measurements by revision.
  if (metadata.auto_hdr_revision) {
    madeira_ctl_args tag = {};
    tag.op = 61;
    tag.ptr = drawable.handle;
    tag.len = ((uint64_t)metadata.auto_hdr_flags << 32) | metadata.auto_hdr_revision;
    std::memcpy(tag.name, &metadata.auto_hdr_peak, sizeof(float));
    MadeiraCtl(&tag);
  }
'''
PATTERN = '''  // Five linear reference bands: 18% gray, SDR white, 1.25x, 1.5x, 2x.
  // They use the same output texture and headroom limit as the game.
  if (meta.auto_hdr_peak >= 1.0f && (meta.auto_hdr_flags & 2u)) {
    constexpr float levels[5] = {0.18f, 1.0f, 1.25f, 1.5f, 2.0f};
    uint band = uint(clamp(input.uv.x, 0.0f, 1.0f) * 5.0f);
    band = band > 4u ? 4u : band;
    return float4(float3(min(levels[band], meta.auto_hdr_peak)), 1.0f);
  }
'''
NATIVE_DECL = '''/* madeira-bcd: HDR live v2. Implemented by app/Madeira/BCDHDR.m. */
extern uint32_t bcd_hdr_control_get(uint32_t *revision);
extern void bcd_hdr_tag_drawable(uint64_t drawable, uint32_t revision, uint32_t flags, float peak);
extern void bcd_hdr_probe_encode(uint64_t command_buffer, uint64_t drawable);

'''
OPS = '''  case 60: { // HDR live controls; fixed-layout integer reply, no guest pointers
    if (wmtr_enabled()) break;
    uint32_t revision = 0;
    a->ret = bcd_hdr_control_get(&revision);
    a->len = revision;
    break;
  }
  case 61: { // exact drawable's applied HDR uniforms, inline (also safe on i386)
    if (wmtr_enabled()) break;
    float peak;
    memcpy(&peak, a->name, sizeof(peak));
    bcd_hdr_tag_drawable(a->ptr, (uint32_t)a->len, (uint32_t)(a->len >> 32), peak);
    a->ret = 1;
    break;
  }
'''


def changes(core, native):
    fields = "  uint32_t auto_hdr_flags;\n  uint32_t auto_hdr_revision;\n"
    edits = {
        core / "dxmt_presenter.hpp": [("  float auto_hdr_peak; // madeira-bcd: auto HDR; 0 disables, >=1 is linear EDR output\n",
            "  float auto_hdr_peak; // madeira-bcd: HDR live v2; 0 bypasses, 1 is SDR comparison\n" + fields)],
        core / "dxmt_presenter.cpp": [
            ("#include <cstdlib>\n", "#include <cstdlib>\n#include <cstring>\n"),
            ("  // Only SDR 8-bit swapchains.", CONTROL + "  // Only SDR 8-bit swapchains."),
            ("  metadata.auto_hdr_peak = auto_hdr\n      ? std::clamp(display_edr_value_.maximum_edr_color_component_value, 1.0f, auto_hdr_peak_) : 0.0f;",
             "  metadata.auto_hdr_flags = auto_hdr ? (hdr_flags | 8u) : 0u;\n  metadata.auto_hdr_revision = hdr_revision;\n  metadata.auto_hdr_peak = auto_hdr\n      ? ((hdr_flags & 1u) ? std::clamp(display_edr_value_.maximum_edr_color_component_value, 1.0f, auto_hdr_peak_) : 1.0f) : 0.0f;"),
            ("  WMTRenderPassInfo info;\n", TAG + "  WMTRenderPassInfo info;\n"),
        ],
        core / "dxmt_command.metal": [
            ("  float auto_hdr_peak; // madeira-bcd: auto HDR; matches the CPU metadata\n",
             "  float auto_hdr_peak; // madeira-bcd: HDR live v2; matches the CPU metadata\n" + fields.replace("uint32_t", "uint")),
            ("  float4 output = float4(0);\n  if (present_backbuffer_is_ms)", PATTERN + "  float4 output = float4(0);\n  if (present_backbuffer_is_ms)"),
        ],
    }
    if native:
        edits[native] = [
            ("static NTSTATUS\n_MTLCommandBuffer_presentDrawable(void *obj) {", NATIVE_DECL + "static NTSTATUS\n_MTLCommandBuffer_presentDrawable(void *obj) {"),
            ("  int mode = g_madeira_vsync_mode;\n", "  int mode = g_madeira_vsync_mode;\n  bcd_hdr_probe_encode(params->handle, params->arg);\n"),
            ('  madeira_log_present_cadence("presentDrawableAfterMinDuration", params->arg1);',
             '  bcd_hdr_probe_encode(params->handle, params->arg0);\n  madeira_log_present_cadence("presentDrawableAfterMinDuration", params->arg1);'),
            ("  a->ret = 0;\n  switch (a->op) {\n", "  a->ret = 0;\n  switch (a->op) {\n" + OPS),
            ("  if (a && a->op == 7) return _madeira_ctl(args);", "  if (a && (a->op == 7 || a->op == 60 || a->op == 61)) return _madeira_ctl(args);"),
        ]
    return edits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presenter-dir", type=Path)
    args = parser.parse_args()
    core = args.presenter_dir or Path("dxmt/src/dxmt")
    native = None if args.presenter_dir else Path("dxmt/src/winemetal/unix/winemetal_unix.c")
    pending = {}
    for path, edits in changes(core, native).items():
        source = path.read_text()
        if MARKER in source:
            print(f"{path.name}: HDR live v2 already patched")
            continue
        for old, new in edits:
            if source.count(old) != 1:
                print(f"::error::{path}: HDR live anchor not found once: {old[:80]!r}")
                return 1
            source = source.replace(old, new)
        pending[path] = source
    for path, source in pending.items():
        path.write_text(source)
        print(f"{path.name}: HDR live v2 patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
