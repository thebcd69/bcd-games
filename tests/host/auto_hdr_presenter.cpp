// The generated includes contain production presenter methods, metadata,
// format conversion and constructor setup. Only the platform/GPU is mocked.
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#define DXMT_NATIVE 1
#include "winemetal.h"
#define WARN(...) ((void)0)
#define unlikely(x) (x)
namespace env {
std::string getEnvVar(const char *key) { const char *v = std::getenv(key); return v ? v : ""; }
}
static uint32_t control_flags = 1, control_revision = 1;
static bool old_backend;
void MadeiraCtl(madeira_ctl_args *a) {
  if (a->op == 60 && !old_backend) { a->ret = 0x48445200u | control_flags; a->len = control_revision; }
}
static WMTEDRValue headroom{1.0f, 1.0f};
static uint64_t display_version;
static WMTColorSpace display_space = WMTColorSpaceSRGB;
void WMTQueryDisplaySettingForLayer(obj_handle_t, uint64_t *version, WMTColorSpace *space,
                                   WMTHDRMetadata *hdr, WMTEDRValue *edr) {
  *version = display_version; *space = display_space; *hdr = {}; *edr = headroom;
}
#include "production_hdr_types.inc"
struct MockLayer {
  obj_handle_t handle = 1;
  WMTLayerProps props{};
  WMTColorSpace space = WMTColorSpaceSRGB;
  void setProps(WMTLayerProps p) { props = p; }
  void setColorSpace(WMTColorSpace c) { space = c; }
};
struct MockFence { void wait(uint64_t) {} };
struct Presenter {
  struct PresentState { DXMTPresentMetadata metadata; uint64_t id; Presenter *presenter; };
  MockLayer layer_;
  WMTLayerProps layer_props_{};
  bool auto_hdr_requested_ = false, has_hdr_metadata_ = false;
  float auto_hdr_peak_ = 2;
  WMTPixelFormat source_format_ = WMTPixelFormatInvalid, sdr_layer_format_ = WMTPixelFormatInvalid;
  uint8_t sample_count_ = 1;
  WMTColorSpace colorspace_ = WMTColorSpaceSRGB, display_colorspace_ = WMTColorSpaceSRGB;
  WMTHDRMetadata hdr_metadata_{}, display_hdr_metadata_{};
  WMTEDRValue display_edr_value_{};
  uint64_t display_setting_version_ = 0, gamma_version_ = 0, frame_requested_ = 0;
  MockFence frame_presented_;
  std::atomic_flag pso_valid = ATOMIC_FLAG_INIT;
  int builds = 0;
  std::array<bool, 4> specialization{};
  void buildRenderPipelineState(bool pq, bool hdr, bool ms, bool gamma) {
    builds++; specialization = {pq, hdr, ms, gamma};
  }
  void setup();
  bool changeLayerProperties(WMTPixelFormat, WMTColorSpace, double, double, uint8_t);
  bool changeLayerColorSpace(WMTColorSpace);
  void changeHDRMetadata(const WMTHDRMetadata *);
  PresentState synchronizeLayerProperties();
};
#include "production_hdr_methods.inc"
static void near(float a, float b) { assert(std::abs(a - b) < 0.0001); }
int main() {
  static_assert(sizeof(DXMTPresentMetadata) == 24);
  headroom = {4, 8};
  unsetenv("DXMT_AUTO_HDR");
  Presenter off; off.setup();
  off.changeLayerProperties(WMTPixelFormatBGRA8Unorm, WMTColorSpaceSRGB, 10, 10, 1);
  near(off.synchronizeLayerProperties().metadata.auto_hdr_peak, 0);
  assert(off.layer_.props.pixel_format == WMTPixelFormatBGRA8Unorm);
  assert(off.layer_.space == WMTColorSpaceSRGB);
  setenv("DXMT_AUTO_HDR", "1", 1);
  for (auto [text, expected] : {std::pair{"", 2.f}, {"nan", 2.f}, {"inf", 2.f}, {"garbage", 2.f},
                               {"2oops", 2.f}, {"1.5", 1.5f}, {"-1", 1.f}, {"99", 8.f}}) {
    setenv("DXMT_AUTO_HDR_PEAK", text, 1);
    Presenter p; p.setup(); near(p.auto_hdr_peak_, expected);
  }
  unsetenv("DXMT_AUTO_HDR_PEAK");
  Presenter p; p.setup();
  p.changeLayerProperties(WMTPixelFormatBGRA8Unorm_sRGB, WMTColorSpaceSRGB, 10, 10, 1);
  near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 2);
  assert(p.layer_.props.pixel_format == WMTPixelFormatRGBA16Float);
  assert(p.layer_.space == WMTColorSpaceHDR_scRGB);
  near(p.synchronizeLayerProperties().metadata.edr_scale, 1); // no 80-nit scRGB darkening
  int builds = p.builds;
  control_flags = 0; control_revision++;
  auto sdr = p.synchronizeLayerProperties().metadata;
  near(sdr.auto_hdr_peak, 1); assert(sdr.auto_hdr_flags == 8 && sdr.auto_hdr_revision == control_revision);
  assert(p.layer_.props.pixel_format == WMTPixelFormatRGBA16Float && p.layer_.space == WMTColorSpaceHDR_scRGB);
  assert(p.builds == builds);
  control_flags = 3;
  auto pattern = p.synchronizeLayerProperties().metadata;
  assert(pattern.auto_hdr_flags == 11); near(pattern.auto_hdr_peak, 2);
  old_backend = true;
  auto fallback = p.synchronizeLayerProperties().metadata;
  near(fallback.auto_hdr_peak, 2); assert(fallback.auto_hdr_revision == 0);
  old_backend = false; control_flags = 1;
  headroom = {1.25, 8};
  near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 1.25);
  headroom = {1, 1};
  near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 1);
  assert(p.builds == builds); // dynamic headroom must not rebuild pipelines
  // Native HDR and unsupported formats must restore the normal presentation.
  for (auto [format, space] : {std::pair{WMTPixelFormatRGBA16Float, WMTColorSpaceHDR_scRGB},
                               {WMTPixelFormatRGB10A2Unorm, WMTColorSpaceHDR_PQ},
                               {WMTPixelFormatRGBA16Float, WMTColorSpaceSRGB}}) {
    p.changeLayerProperties(format, space, 20, 20, 1);
    near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 0);
    assert(p.layer_.props.pixel_format == format);
    assert(p.layer_.space == space);
  }
  p.changeLayerProperties(WMTPixelFormatBGRA8Unorm, WMTColorSpaceSRGB, 20, 20, 1);
  near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 1);
  p.changeLayerColorSpace(WMTColorSpaceBT2020);
  near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 0);
  assert(p.layer_.props.pixel_format == WMTPixelFormatBGRA8Unorm);
  p.changeLayerColorSpace(WMTColorSpaceSRGB);
  near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 1);
  display_version = 1; display_space = WMTColorSpaceHDR_PQ;
  near(p.synchronizeLayerProperties().metadata.auto_hdr_peak, 0);
  assert(p.layer_.props.pixel_format == WMTPixelFormatBGRA8Unorm);
  unsetenv("DXMT_AUTO_HDR");
  Presenter next; next.setup(); assert(!next.auto_hdr_requested_);
  std::cout << "PASS: production presenter: defaults, peak parsing, dynamic headroom, native HDR bypass and color/format transitions\n";
}
