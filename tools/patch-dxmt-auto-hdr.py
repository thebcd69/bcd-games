#!/usr/bin/env python3
"""Opt-in SDR highlight expansion in DXMT's existing present pass.

Run from the repository root. --presenter-dir patches a private DXMT core
copy (the ARM64EC d3d11 build); otherwise also patches iOS headroom queries.
No submodule pin changes. All anchors are checked before any file is written.
"""
import argparse
import pathlib
import sys

MARKER = "madeira-bcd: auto HDR"

SHADER = r'''// madeira-bcd: auto HDR. Input is display-encoded sRGB, output linear EDR.
// Keep shadows/midtones; smoothly expand the top 40% of linear maxRGB.
// One gain for all channels preserves hue, including saturated highlights.
float3 madeira_auto_hdr(float3 srgb, float peak) {
  srgb = clamp(srgb, 0.0f, 1.0f);
  float3 linear = select(pow((srgb + 0.055f) / 1.055f, 2.4f),
                         srgb / 12.92f, srgb <= 0.04045f);
  float level = max3(linear.r, linear.g, linear.b);
  float t = clamp((level - 0.6f) / 0.4f, 0.0f, 1.0f);
  float gain = 1.0f + (peak - 1.0f) * t * t * (3.0f - 2.0f * t);
  return linear * gain;
}

'''

SETUP = r'''  // madeira-bcd: auto HDR. Read per presenter, never cache across sessions.
  auto_hdr_requested_ = env::getEnvVar("DXMT_AUTO_HDR") == "1";
  auto peak_text = env::getEnvVar("DXMT_AUTO_HDR_PEAK");
  char *end = nullptr;
  float peak = std::strtof(peak_text.c_str(), &end);
  auto_hdr_peak_ = !peak_text.empty() && end == peak_text.c_str() + peak_text.size() && std::isfinite(peak)
      ? std::clamp(peak, 1.0f, 8.0f) : 2.0f;
  if (auto_hdr_requested_)
    WARN("[auto-hdr] requested: SDR DXMT present, peak ", auto_hdr_peak_, "x SDR white (limited by display headroom)");
'''

SELECT = r'''  // Only SDR 8-bit swapchains. Native PQ/scRGB and float buffers bypass it.
  auto source = ORIGINAL_FORMAT(Forget_sRGB(source_format_));
  bool auto_hdr = auto_hdr_requested_ && colorspace_ == WMTColorSpaceSRGB &&
      final_colorspace == WMTColorSpaceSRGB &&
      (source == WMTPixelFormatBGRA8Unorm || source == WMTPixelFormatRGBA8Unorm);
  auto present_format = auto_hdr ? WMTPixelFormatRGBA16Float : sdr_layer_format_;
  if (layer_props_.pixel_format != present_format) {
    layer_props_.pixel_format = present_format;
    pso_valid.clear();
  }
  // Use linear metadata, so native scRGB's 80-nit scale is not applied.
  // The layer itself gets the same extended-linear space with EDR enabled.
  if (auto_hdr)
    final_colorspace = WMTColorSpaceSRGBLinear;
'''

HEADROOM = r'''#if TARGET_OS_IOS
/* madeira-bcd: auto HDR. Main-thread screen reads are asynchronous and cached
 * by app/Madeira/BCDHDR.m. This native query never waits for UIKit. */
extern void bcd_hdr_get_headroom(uint64_t layer, float *current, float *potential);
static struct WMTEDRValue madeira_ios_edr(CAMetalLayer *layer) {
  struct WMTEDRValue result = {1.0, 1.0};
  const char *enabled = getenv("DXMT_AUTO_HDR");
  if (!enabled || strcmp(enabled, "1")) return result;
  bcd_hdr_get_headroom((uint64_t)(uintptr_t)layer,
      &result.maximum_edr_color_component_value, &result.maximum_potential_edr_color_component_value);
  return result;
}
#endif

'''



def replacements(core, native):
    metadata = "  float max_display_luminance;\n"
    result = {
        core / "dxmt_presenter.hpp": [
            (metadata, metadata + "  float auto_hdr_peak; // madeira-bcd: auto HDR; 0 disables, >=1 is linear EDR output\n"),
            ("  uint32_t sample_count_;\n", "  uint32_t sample_count_;\n  bool auto_hdr_requested_ = false;\n  float auto_hdr_peak_ = 2.0f;\n  WMTPixelFormat sdr_layer_format_ = WMTPixelFormatInvalid;\n"),
        ],
        core / "dxmt_presenter.cpp": [
            ('#include <algorithm>\n', '#include <algorithm>\n#include <cmath>\n#include <cstdlib>\n#include "util_env.hpp"\n#include "log/log.hpp"\n'),
            ("  layer_.getProps(layer_props_);\n", SETUP + "  layer_.getProps(layer_props_);\n"),
            ("  layer_props_.drawable_height = height;\n", "  sdr_layer_format_ = layer_props_.pixel_format;\n  layer_props_.drawable_height = height;\n"),
            ("  auto is_hdr = WMT_COLORSPACE_IS_HDR(final_colorspace);\n", SELECT + "  auto is_hdr = WMT_COLORSPACE_IS_HDR(final_colorspace);\n"),
            ("    layer_.setColorSpace(final_colorspace);\n", "    layer_.setColorSpace(auto_hdr ? WMTColorSpaceHDR_scRGB : final_colorspace);\n"),
            ("  metadata.edr_scale = 1.0;\n", "  metadata.auto_hdr_peak = auto_hdr\n      ? std::clamp(display_edr_value_.maximum_edr_color_component_value, 1.0f, auto_hdr_peak_) : 0.0f;\n  metadata.edr_scale = 1.0;\n"),
        ],
        core / "dxmt_command.metal": [
            (metadata, metadata + "  float auto_hdr_peak; // madeira-bcd: auto HDR; matches the CPU metadata\n"),
            ("[[fragment]] float4 fs_present_quad(\n", SHADER + "[[fragment]] float4 fs_present_quad(\n"),
            ("  if (present_hdr_pq)\n    output_rgb = pq_to_linear(output_rgb);\n", "  if (meta.auto_hdr_peak >= 1.0f)\n    return float4(madeira_auto_hdr(output_rgb, meta.auto_hdr_peak), output.w);\n  if (present_hdr_pq)\n    output_rgb = pq_to_linear(output_rgb);\n"),
        ],
    }
    if native:
        result[native] = [
            ("static NTSTATUS\n_MetalLayer_getEDRValue(void *obj) {", HEADROOM + "static NTSTATUS\n_MetalLayer_getEDRValue(void *obj) {"),
            ("  value->maximum_potential_edr_color_component_value = 1.0;\n\n#if !TARGET_OS_IOS", "  value->maximum_potential_edr_color_component_value = 1.0;\n\n#if TARGET_OS_IOS\n  *value = madeira_ios_edr(layer);\n#endif\n#if !TARGET_OS_IOS"),
            ("#if TARGET_OS_IOS\n  (void)layer;\n  return STATUS_SUCCESS;\n#else", "#if TARGET_OS_IOS\n  (void)hdr_metadata_out;\n  params->edr_value = madeira_ios_edr(layer);\n  return STATUS_SUCCESS;\n#else"),
        ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presenter-dir", type=pathlib.Path)
    args = parser.parse_args()
    core = args.presenter_dir or pathlib.Path("dxmt/src/dxmt")
    native = None if args.presenter_dir else pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
    pending = {}
    for path, changes in replacements(core, native).items():
        source = path.read_text()
        if MARKER in source or "madeira-bcd: HDR live v2" in source:
            print(f"{path.name}: auto HDR already patched")
            continue
        for old, new in changes:
            if source.count(old) != 1:
                print(f"::error::{path}: auto HDR anchor not found once: {old[:90]!r}")
                return 1
            source = source.replace(old, new)
        pending[path] = source
    for path, source in pending.items():
        path.write_text(source)
        print(f"{path.name}: auto HDR patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
