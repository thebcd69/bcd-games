// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The behaviour half of tests/host/check-vt-interp.py. That script takes tests/host/present_fx_harness.m up to its main() (the
// stand-ins for Metal and the code of tools/patch-winemetal-present-fx.py), applies the hook lines of
// tools/patch-winemetal-vt-interp.py to it, and puts the three parts of this file around that script's block:
//   PRELUDE  stand-ins for VideoToolbox's frame processor and for what the block calls in winemetal_unix.c
//   AFTER    what the stand-ins do (they use the block's own test picture functions)
//   TESTS    main()
// The pixel buffers are real (CoreVideo works without a GPU); VideoToolbox's model, Metal and the screen are not.

//@@PRELUDE
#import <VideoToolbox/VideoToolbox.h>
#import <CoreVideo/CoreVideo.h>
#import <CoreMedia/CoreMedia.h>
#import <IOSurface/IOSurface.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <math.h>

// ---- what the block calls in winemetal_unix.c
static volatile int g_madeira_vsync_mode = 0, g_madeira_panel_hz = 120;
static _Atomic int g_lsfg_shown = 1;
static int g_limiter_calls, g_limiter_on, g_wait_active_calls, g_encode_present_calls;
static int madeira_app_wait_active(const char *where) { (void)where; g_wait_active_calls++; return 0; }
static void ios_frame_limiter(unsigned long long ns) { (void)ns; g_limiter_calls++; }
unsigned long long ios_fps_cap_dxmt_pace(void) { return 0; }
static unsigned long long madeira_present_limit_to(double period) { (void)period; return 1; }
static int madeira_present_limiter_enabled(void) { return g_limiter_on; }
static void ios_frame_encode_present(int skipped) { (void)skipped; g_encode_present_calls++; }

// ---- VideoToolbox's frame processor
static int g_vt_supported = 1, g_vt_max_width = 1920, g_vt_start_ms = 30, g_vt_start_fail, g_vt_process_ms = 2, g_vt_fail;
static unsigned g_vt_formats[4] = { 0x34323076u /* 420v */, 0x42475241u /* BGRA */, 0, 0 };
static _Atomic int g_vt_configs, g_vt_started, g_vt_ended, g_vt_processed;
@interface FakeVTConfig : NSObject
@property (nonatomic) NSInteger frameWidth, frameHeight, spatialScaleFactor, numberOfInterpolatedFrames;
+ (BOOL)isSupported;
+ (CMVideoDimensions)maximumDimensions;
+ (CMVideoDimensions)minimumDimensions;
- (instancetype)initWithFrameWidth:(NSInteger)w frameHeight:(NSInteger)h numberOfInterpolatedFrames:(NSInteger)n;
- (instancetype)initWithFrameWidth:(NSInteger)w frameHeight:(NSInteger)h spatialScaleFactor:(NSInteger)f;
- (NSArray<NSNumber *> *)frameSupportedPixelFormats;
- (NSDictionary *)sourcePixelBufferAttributes;
- (NSDictionary *)destinationPixelBufferAttributes;
@end
@interface FakeVTFrame : NSObject
@property (nonatomic) CVPixelBufferRef buffer;
@property (nonatomic) CMTime presentationTimeStamp;
- (instancetype)initWithBuffer:(CVPixelBufferRef)b presentationTimeStamp:(CMTime)t;
@end
@interface FakeVTParams : NSObject
@property (nonatomic, retain) FakeVTFrame *sourceFrame, *previousFrame;
@property (nonatomic, retain) NSArray *interpolationPhase, *destinationFrames;
- (instancetype)initWithSourceFrame:(FakeVTFrame *)s previousFrame:(FakeVTFrame *)p interpolationPhase:(NSArray *)ph destinationFrames:(NSArray *)d;
@end
@interface FakeVTProcessor : NSObject
@property (nonatomic, retain) FakeVTConfig *config;
- (BOOL)startSessionWithConfiguration:(id)c error:(NSError **)e;
- (BOOL)processWithParameters:(id)p error:(NSError **)e;
- (void)processWithCommandBuffer:(id)cb parameters:(id)p;
- (void)endSession;
@end
#define VTLowLatencyFrameInterpolationConfiguration FakeVTConfig
#define VTLowLatencyFrameInterpolationParameters FakeVTParams
#define VTFrameProcessorFrame FakeVTFrame
#define VTFrameProcessor FakeVTProcessor
#define MTLCreateSystemDefaultDevice() ((id)[[FakeDevice alloc] init])

// ---- Metal: what present-fx's stand-ins did not need
static NSString *surface_name(IOSurfaceRef s, NSUInteger plane);
static int g_gpu_ms, g_copy_error;
@interface FakeDevice (VTI)
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)d iosurface:(IOSurfaceRef)s plane:(NSUInteger)p;
- (id)newCommandQueue;
@end
@interface FakeCommandBuffer (VTI)
- (void)waitUntilCompleted;
- (MTLCommandBufferStatus)status;
- (NSError *)error;
@end
@interface FakeEncoder (VTI)
- (void)setFragmentBytes:(const void *)b length:(NSUInteger)l atIndex:(NSUInteger)i;
@end

//@@AFTER
@implementation FakeVTConfig
+ (BOOL)isSupported { return g_vt_supported != 0; }
+ (CMVideoDimensions)maximumDimensions { CMVideoDimensions d = { g_vt_max_width, g_vt_max_width * 9 / 16 }; return d; }
+ (CMVideoDimensions)minimumDimensions { CMVideoDimensions d = { 256, 144 }; return d; }
- (instancetype)initWithFrameWidth:(NSInteger)w frameHeight:(NSInteger)h numberOfInterpolatedFrames:(NSInteger)n {
  if (w > g_vt_max_width) { [self release]; return nil; }
  self = [super init];
  _frameWidth = w; _frameHeight = h; _numberOfInterpolatedFrames = n; _spatialScaleFactor = 1;
  atomic_fetch_add(&g_vt_configs, 1);
  return self;
}
- (instancetype)initWithFrameWidth:(NSInteger)w frameHeight:(NSInteger)h spatialScaleFactor:(NSInteger)f {
  if (w * f > g_vt_max_width) { [self release]; return nil; }
  self = [super init];
  _frameWidth = w; _frameHeight = h; _numberOfInterpolatedFrames = 1; _spatialScaleFactor = f;
  atomic_fetch_add(&g_vt_configs, 1);
  return self;
}
- (NSArray<NSNumber *> *)frameSupportedPixelFormats {
  NSMutableArray *a = [NSMutableArray array];
  for (int i = 0; i < 4; i++) if (g_vt_formats[i]) [a addObject:@(g_vt_formats[i])];
  return a;
}
- (NSDictionary *)sourcePixelBufferAttributes { return @{ (id)kCVPixelBufferIOSurfacePropertiesKey: @{} }; }
- (NSDictionary *)destinationPixelBufferAttributes { return @{ (id)kCVPixelBufferIOSurfacePropertiesKey: @{} }; }
@end
@implementation FakeVTFrame
- (instancetype)initWithBuffer:(CVPixelBufferRef)b presentationTimeStamp:(CMTime)t {
  if (!b || !CVPixelBufferGetIOSurface(b)) { [self release]; return nil; }
  self = [super init];
  _buffer = CVPixelBufferRetain(b); _presentationTimeStamp = t;
  return self;
}
- (void)dealloc { CVPixelBufferRelease(_buffer); [super dealloc]; }
@end
@implementation FakeVTParams
- (instancetype)initWithSourceFrame:(FakeVTFrame *)s previousFrame:(FakeVTFrame *)p interpolationPhase:(NSArray *)ph destinationFrames:(NSArray *)d {
  self = [super init];
  self.sourceFrame = s; self.previousFrame = p; self.interpolationPhase = ph; self.destinationFrames = d;
  return self;
}
- (void)dealloc { [_sourceFrame release]; [_previousFrame release]; [_interpolationPhase release]; [_destinationFrames release]; [super dealloc]; }
@end
// What the model would do with the test picture: the bar of the frame before and of this frame, at each phase between them.
static BOOL fake_vt_make(FakeVTProcessor *self, FakeVTParams *p, NSString **why) {
  NSInteger scale = self.config.spatialScaleFactor, n = (NSInteger)p.interpolationPhase.count, i;
  CMTime a = p.previousFrame.presentationTimeStamp, b = p.sourceFrame.presentationTimeStamp;
  double x0, x1;
  size_t w = CVPixelBufferGetWidth(p.sourceFrame.buffer);
  NSMutableString *phases = [NSMutableString string];
  if (!self.config) { *why = @"no session"; return NO; }
  if (CMTimeCompare(a, b) >= 0) { *why = @"the frame before is not earlier"; return NO; }
  if ((NSInteger)p.destinationFrames.count != n + (scale > 1 ? 1 : 0)) { *why = @"wrong number of destinations"; return NO; }
  if (n > (1 << self.config.numberOfInterpolatedFrames) - 1) { *why = @"more phases than the configuration has"; return NO; }
  if (p.sourceFrame.buffer == p.previousFrame.buffer) { *why = @"the same buffer twice"; return NO; }
  x0 = madeira_vti_find(p.previousFrame.buffer); x1 = madeira_vti_find(p.sourceFrame.buffer);
  for (i = 0; i < n; i++) {
    FakeVTFrame *d = p.destinationFrames[(NSUInteger)i];
    double phase = [p.interpolationPhase[(NSUInteger)i] doubleValue];
    CMTime t = d.presentationTimeStamp;
    if (d.buffer == p.sourceFrame.buffer || d.buffer == p.previousFrame.buffer) { *why = @"a destination is a source"; return NO; }
    if (CMTimeCompare(a, t) >= 0 || CMTimeCompare(t, b) >= 0) { *why = @"a picture's time is not between the two frames"; return NO; }
    if ((size_t)(w * (size_t)scale) != CVPixelBufferGetWidth(d.buffer)) { *why = @"a destination has the wrong size"; return NO; }
    if (x0 >= 0 && x1 >= 0) {
      unsigned bar = (unsigned)(w / 16);
      madeira_vti_paint(d.buffer, (unsigned)((x0 + (x1 - x0) * phase - bar / 2.0) * scale + 0.5), bar * (unsigned)scale);
    }
    [phases appendFormat:@"%s%.2f", i ? "," : "", phase];
  }
  if (scale > 1 && x1 >= 0) {
    FakeVTFrame *d = p.destinationFrames[(NSUInteger)n];
    unsigned bar = (unsigned)(w / 16);
    madeira_vti_paint(d.buffer, (unsigned)((x1 - bar / 2.0) * scale + 0.5), bar * (unsigned)scale);
  }
  op(@"VT: %@ + %@ -> %ld picture(s) at %@", surface_name(CVPixelBufferGetIOSurface(p.previousFrame.buffer), 9),
     surface_name(CVPixelBufferGetIOSurface(p.sourceFrame.buffer), 9), (long)n, phases);
  atomic_fetch_add(&g_vt_processed, 1);
  return YES;
}
@implementation FakeVTProcessor
- (BOOL)startSessionWithConfiguration:(id)c error:(NSError **)e {
  usleep((useconds_t)g_vt_start_ms * 1000);
  if (g_vt_start_fail) { if (e) *e = [NSError errorWithDomain:@"fake" code:-19736 userInfo:@{ NSLocalizedDescriptionKey: @"the pipeline could not be made" }]; return NO; }
  self.config = c;
  atomic_fetch_add(&g_vt_started, 1);
  return YES;
}
- (BOOL)processWithParameters:(id)p error:(NSError **)e {
  NSString *why = nil;
  usleep((useconds_t)g_vt_process_ms * 1000);
  if (g_vt_fail > 0) { g_vt_fail--; why = @"a made-up failure"; }
  if (why || !fake_vt_make(self, p, &why)) {
    if (e) *e = [NSError errorWithDomain:@"fake" code:-19740 userInfo:@{ NSLocalizedDescriptionKey: why }];
    return NO;
  }
  return YES;
}
- (void)processWithCommandBuffer:(id)cb parameters:(id)p {
  NSString *why = nil;
  if (!fake_vt_make(self, p, &why)) [NSException raise:@"FakeVT" format:@"%@", why];
  op(@"%@: VideoToolbox encoded", [(FakeCommandBuffer *)cb name]);
}
- (void)endSession { if (self.config) atomic_fetch_add(&g_vt_ended, 1); self.config = nil; }
- (void)dealloc { [_config release]; [super dealloc]; }
@end

// Pixel buffers get short names in the order they are made: surf1, surf2, ... (".y" and ".c" for the planes of 4:2:0).
static NSMutableArray *g_surfaces;
static NSString *surface_name(IOSurfaceRef s, NSUInteger plane) {
  NSUInteger i;
  @synchronized (g_ops) {
    if (!g_surfaces) g_surfaces = [[NSMutableArray alloc] init];
    i = [g_surfaces indexOfObject:@(IOSurfaceGetID(s))];
    if (i == NSNotFound) { [g_surfaces addObject:@(IOSurfaceGetID(s))]; i = g_surfaces.count - 1; }
  }
  return plane == 9 ? [NSString stringWithFormat:@"surf%lu", (unsigned long)i + 1]
                    : [NSString stringWithFormat:@"surf%lu%s", (unsigned long)i + 1, IOSurfaceGetPlaneCount(s) > 1 ? (plane ? ".c" : ".y") : ""];
}
@implementation FakeDevice (VTI)
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)d iosurface:(IOSurfaceRef)s plane:(NSUInteger)p {
  FakeTexture *t = [[FakeTexture alloc] init];
  t.width = d.width; t.height = d.height; t.pixelFormat = d.pixelFormat; t.device = self;
  t.name = surface_name(s, p);
  return t;
}
- (id)newCommandQueue { return [[FakeQueue alloc] init]; }
@end
@implementation FakeCommandBuffer (VTI)
- (void)waitUntilCompleted { if (g_gpu_ms) usleep((useconds_t)g_gpu_ms * 1000); op(@"%@: waited for", self.name); }
- (MTLCommandBufferStatus)status { return g_copy_error ? MTLCommandBufferStatusError : MTLCommandBufferStatusCompleted; }
- (NSError *)error { return nil; }
@end
@implementation FakeEncoder (VTI)
- (void)setFragmentBytes:(const void *)b length:(NSUInteger)l atIndex:(NSUInteger)i { (void)b; (void)l; (void)i; }
@end

//@@TESTS
static int wait_worker(void) {
  int i;
  for (i = 0; i < 12000 && atomic_load(&g_vti.outstanding) > 0; i++) usleep(250);
  return atomic_load(&g_vti.outstanding) == 0;
}
static int wait_idle(void) {
  int ok = wait_worker();
  drain_main();
  return ok;
}
static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static void sleep_until(double t) { double d = t - now_s(); if (d > 0) usleep((useconds_t)(d * 1e6)); }
static int wait_state(int state) {
  int i;
  for (i = 0; i < 3000 && atomic_load(&g_vti.state) != state; i++) usleep(1000);
  return atomic_load(&g_vti.state) == state;
}
static int count_of(NSString *needle) {
  int n = 0;
  @synchronized (g_ops) { for (NSString *s in g_ops) if ([s rangeOfString:needle].location != NSNotFound) n++; }
  return n;
}
static int logged(NSString *needle) {
  @synchronized (g_log) { return [g_log rangeOfString:needle].location != NSNotFound; }
}
// One frame of the game every `ms` on the clock, each waited for (the worker keeps up).
static void frames(FakeLayer *layer, FakeQueue *q, int n, int ms, int how) {
  NSString *tn = nil;
  double t0 = now_s();
  for (int i = 0; i < n; i++) {
    sleep_until(t0 + (i + 1) * ms / 1000.0);
    @autoreleasepool { game_frame(layer, q, how, &tn); }
    wait_worker();
  }
  drain_main();
}

int main(int argc, char **argv) {
  int phase = argc > 1 ? atoi(argv[1]) : 0;
  @autoreleasepool {
    FakeDevice *dev = [[FakeDevice alloc] init];
    FakeLayer *layer = [[FakeLayer alloc] init];
    FakeQueue *q = [[FakeQueue alloc] init];
    NSString *tn = nil;
    g_ops = [[NSMutableArray alloc] init];
    g_log = [[NSMutableString alloc] init];
    layer.device = dev; layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    g_mode_key = 1;
    g_vti_passes = 1;   /* a pass is recorded with what it draws into */
    g_hooks = 1;        /* the frame hooks are on, as in a session with the HUD */
    madeira_pfx_set_screen(2622, 1206);

    if (phase == 0) {
      /* ---- the key is not set: present-fx alone, to the letter */
      unsetenv("MADEIRA_VT_INTERP");
      madeira_pfx_set_upscale(1, 1500);
      layer_set_props(layer, 1408, 648);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      drain_main();
      reset_ops(); g_cb_number = 0;
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([ops_text() isEqualToString:
              @"game: commit | layer nextDrawable -> drawable2(2112x972) | own2: MetalFX tex1(1408x648) -> tex2(2112x972) | "
               "own2: copy tex2(2112x972) -> drawable2(2112x972) | cadence presentDrawable | own2: present drawable2(2112x972) | own2: commit"],
             "without MADEIRA_VT_INTERP a frame of present-fx is what it was: commit, drawable, MetalFX, present, commit");
      frames(layer, q, 30, 1, 0);
      expect(atomic_load(&g_vti.want) == 0 && atomic_load(&g_vti.state) == 0 && atomic_load(&g_vti.outstanding) == 0 && !g_vti.s
             && atomic_load(&g_vt_configs) == 0 && !atomic_load(&g_pfx_force) && !logged(@"[vt-interp]"),
             "the key was read once (off): no session, no configuration, no worker, nothing forced, not a line in the log");
      expect(madeira_vti_set(1) == 0 && madeira_vti_status(NULL, NULL, NULL, NULL, NULL) == 0 && !logged(@"[vt-interp]"),
             "the menu's switch does nothing without the key, and the status is 0");
      madeira_vti_session_ended();
      expect(g_wait_active_calls == 0 && g_limiter_calls == 0, "none of the present call's steps was run by it");
      return g_fails ? 1 : 0;
    }
    if (phase == 1) {
      /* ---- the key is set but present-fx is not */
      setenv("MADEIRA_VT_INTERP", "1", 1);
      g_mode_key = 0;
      layer_set_props(layer, 1408, 648);
      frames(layer, q, 2, 1, 0);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(atomic_load(&g_vti.want) == -1 && [tn hasPrefix:@"drawable"] && atomic_load(&g_vt_configs) == 0,
             "MADEIRA_VT_INTERP without MADEIRA_PRESENT_FX: present-fx's hooks are not reached at all, the layer's own drawables");
      expect(madeira_vti_want() == 0 && logged(@"needs env.MADEIRA_PRESENT_FX = 1"), "asked directly, it says once what it needs and stays off");
      return g_fails ? 1 : 0;
    }

    if (phase == 2) {
      /* ---- one picture between two frames; BGRA layer and BGRA buffers: plain copies */
      setenv("MADEIRA_VT_INTERP", "1", 1);
      g_vt_start_ms = 400;
      layer_set_props(layer, 1280, 720);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn isEqualToString:@"tex1(1280x720)"] && atomic_load(&g_pfx_force) == 1,
             "key on, no upscaling: the game's side gets a picture of its own size in place of the drawable (the pictures are made from it)");
      expect(has(@": present drawable1(1280x720)") && g_legacy_presents == 1 && atomic_load(&g_vti.outstanding) == 0 && atomic_load(&g_vti.state) == 1
             && logged(@"follows layer"),
             "the first frame starts the session in the background and is presented as without the key (the model is still loading)");
      frames(layer, q, 3, 5, 0);
      expect(g_legacy_presents == 4, "frames keep presenting as without the key while the model loads");
      g_vt_start_ms = 30;
      expect(wait_state(2) && atomic_load(&g_vt_started) == 1 && logged(@"session ready: 1280x720, up to 1 picture(s) between two frames, pixel format 'BGRA',"),
             "the session is ready: BGRA is taken of the formats offered (420v, BGRA)");
      expect(madeira_vti_status(NULL, NULL, NULL, NULL, NULL) == 2, "the menu's status: ready, no picture yet");

      /* the first frame with the session: nothing before it */
      usleep(33000); reset_ops(); g_cb_number = 0;
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(wait_idle() && [ops_text() hasPrefix:@"game: commit | own2: copy tex1(1280x720) -> surf1 | own2: commit | cadence presentVTInterp"],
             "a frame with the session: right behind the game's own buffer its picture is copied into a pixel buffer, on the game's thread");
      expect(!has(@"VT:") && has(@"copy tex1(1280x720) -> drawable5(1280x720)") && count_of(@": present ") == 1 && g_legacy_presents == 4,
             "nothing before it yet: the worker presents the game's frame alone, and the present call's own code did not run");
      /* the second: one picture */
      usleep(33000); reset_ops(); g_cb_number = 0;
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(wait_idle() && [ops_text() isEqualToString:
              @"game: commit | own2: copy tex1(1280x720) -> surf2 | own2: commit | cadence presentVTInterp | own2: waited for | "
               "VT: surf1 + surf2 -> 1 picture(s) at 0.50 | own3: copy surf4 -> tex2(1280x720) | layer nextDrawable -> drawable6(1280x720) | "
               "own3: copy tex2(1280x720) -> drawable6(1280x720) | own3: present drawable6(1280x720) | own3: commit | "
               "layer nextDrawable -> drawable7(1280x720) | own4: copy tex1(1280x720) -> drawable7(1280x720) | "
               "own4: present drawable7(1280x720) at a later time | own4: commit"],
             "the next frame: the worker waits for the copy, VideoToolbox gets the two buffers, the picture goes out from a buffer of its own "
             "that is committed before the game's frame takes its drawable, and the game's frame follows at its own time");
      frames(layer, q, 6, 33, 0);
      expect(atomic_load(&g_vt_processed) == 7 && has(@"VT: surf2 + surf3") && has(@"VT: surf3 + surf1") && has(@"VT: surf1 + surf2"),
             "six more frames: the three buffers go round, each pair is the frame before and the frame");
      expect(atomic_load(&g_vti.generated) == 7 && !has(@"COMMITTED TWICE") && !atomic_load(&g_pfx_pending) && g_textures_made <= 4,
             "seven pictures so far; no buffer committed twice, nothing pending, no texture made per frame");
      expect(g_wait_active_calls >= 8 && g_encode_present_calls >= 8 && g_limiter_calls == 0,
             "the present call's start still runs on the game's thread (pause while inactive, the frame boundary); no limiter without a limit");

      /* ---- the screen cannot show more */
      g_madeira_panel_hz = 60;
      frames(layer, q, 10, 12, 0);   /* the measured frame time follows over a few frames */
      reset_ops();
      { unsigned long long before_n = atomic_load(&g_vti.generated), rate0 = atomic_load(&g_vti.skip_rate); frames(layer, q, 8, 12, 0);
        expect(atomic_load(&g_vti.generated) == before_n && !has(@"VT:") && atomic_load(&g_vti.skip_rate) == rate0 + 8 && count_of(@": present ") == 8,
               "a game at 80 on a 60 Hz screen: no pictures (the screen cannot show them), every frame of the game is presented"); }
      g_madeira_panel_hz = 120;

      /* ---- a frame-rate limit */
      g_madeira_vsync_mode = 3; g_limiter_calls = 0; reset_ops();
      frames(layer, q, 4, 33, 0);
      expect(g_limiter_calls == 4 && !has(@" after 0.033"), "limit 30: the limiter holds the game's frames on the game's thread; the pictures come on top");
      g_madeira_vsync_mode = 2; reset_ops();
      { int legacy = g_legacy_presents; frames(layer, q, 3, 33, 0);
        expect(g_legacy_presents == legacy + 3 && !has(@"VT:"), "no limit (frames may be skipped): as without the key"); }
      g_madeira_vsync_mode = 0;

      /* ---- a worker that is behind: the game's frames go on, in order, and no real drawable is taken beside it */
      frames(layer, q, 2, 33, 0);
      g_vt_process_ms = 120; reset_ops();
      { int legacy = g_legacy_presents, i; unsigned long long busy0 = atomic_load(&g_vti.skip_busy);
        for (i = 0; i < 6; i++) { usleep(33000); @autoreleasepool { game_frame(layer, q, 0, &tn); } }
        expect(wait_idle() && g_legacy_presents == legacy && atomic_load(&g_vti.skip_busy) > busy0,
               "VideoToolbox takes 120 ms a pair at 30 FPS: frames that find the worker busy get no pictures, and none is presented behind its back");
        expect(count_of(@": present drawable") >= 6 && !has(@"-> nil"), "every frame of the game still reached the screen"); }
      g_vt_process_ms = 2;

      /* ---- the game's side also presents with a minimum duration */
      frames(layer, q, 2, 33, 0); reset_ops();
      frames(layer, q, 2, 33, 1);
      expect(has(@" after 0.033") && !has(@"VT:"), "a present after a minimum duration gets no pictures and keeps its duration");

      /* ---- the copy fails on the GPU */
      frames(layer, q, 2, 33, 0);
      g_copy_error = 1; reset_ops();
      frames(layer, q, 1, 33, 0);
      g_copy_error = 0;
      expect(!has(@"VT:") && count_of(@": present ") == 1, "a copy that failed on the GPU: no picture from it");
      reset_ops(); frames(layer, q, 1, 33, 0);
      expect(!has(@"VT:"), "and the frame after it has no frame before it");
      reset_ops(); frames(layer, q, 1, 33, 0);
      expect(has(@"VT:"), "the one after that has again");

      /* ---- a stall */
      usleep(300000); reset_ops(); frames(layer, q, 1, 1, 0);
      expect(!has(@"VT:"), "after 300 ms without a frame the frame before is no neighbour: no picture");

      /* ---- the menu's switch */
      frames(layer, q, 2, 33, 0);
      expect(madeira_vti_status(NULL, NULL, NULL, NULL, NULL) >= 2, "status: ready or making pictures");
      expect(madeira_vti_set(0) == 0 && wait_state(0), "switched off from the menu");
      usleep(50000);
      expect(atomic_load(&g_vt_ended) == 1 && logged(@"session for 1280x720 closed"), "the session is closed and its model given back");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn hasPrefix:@"drawable"] && !atomic_load(&g_pfx_force) && madeira_vti_status(NULL, NULL, NULL, NULL, NULL) == 0,
             "off: the layer's own drawable again (nothing needs the game-size picture), status 0");
      expect(madeira_vti_set(1) == 1, "switched on again");
      frames(layer, q, 2, 5, 0);
      expect(wait_state(2) && atomic_load(&g_vt_started) == 2, "the model is loaded again");
      reset_ops(); frames(layer, q, 3, 33, 0);
      expect(has(@"VT:"), "and pictures are made again");

      /* ---- the game changes its size */
      layer_set_props(layer, 960, 540);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(2) && atomic_load(&g_vti.w) == 960 && logged(@"session ready: 960x540"), "another size: a session for it");
      usleep(50000);
      expect(atomic_load(&g_vt_ended) == 2, "and the one for the old size is closed");
      reset_ops(); frames(layer, q, 3, 33, 0);
      expect(has(@"VT:") && has(@"(960x540)"), "pictures at the new size");

      /* ---- three errors in a row */
      g_vt_fail = 3; reset_ops();
      frames(layer, q, 4, 33, 0);
      expect(atomic_load(&g_vti.state) == -1 && logged(@"three errors in a row") && madeira_vti_status(NULL, NULL, NULL, NULL, NULL) == 4,
             "VideoToolbox fails three times in a row: no more pictures in this session, status 4");
      { char why[200]; expect(madeira_vti_why(why, sizeof why) > 0 && strstr(why, "three times in a row"), "and the reason can be read"); }
      { int legacy = g_legacy_presents; frames(layer, q, 3, 33, 0);
        expect(g_legacy_presents == legacy + 3 && count_of(@": present ") >= 7, "the game's frames went on all the time, now as without the key"); }

      /* ---- the layer goes away while a frame is with the worker */
      madeira_vti_set(0); madeira_vti_set(1);
      frames(layer, q, 2, 5, 0);
      expect(wait_state(2), "switched off and on: a new session after the errors");
      frames(layer, q, 2, 33, 0);
      g_vt_process_ms = 150;
      usleep(33000);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      usleep(33000);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      [layer release]; layer = nil;
      expect(wait_idle(), "the layer is released while two frames are with the worker: they end without a crash");
      g_vt_process_ms = 2;
      madeira_vti_session_ended();
      usleep(80000);
      expect(atomic_load(&g_vt_ended) == atomic_load(&g_vt_started) && atomic_load(&g_vti.state) == 0, "the game ended: every session that was started is closed");
      return g_fails ? 1 : 0;
    }

    if (phase == 3) {
      /* ---- three pictures; only 4:2:0 is offered; an sRGB layer; with MetalFX upscaling by present-fx */
      setenv("MADEIRA_VT_INTERP", "3", 1);
      g_vt_formats[0] = 0x34323076u; g_vt_formats[1] = 0x34323066u; g_vt_formats[2] = 0;
      layer.pixelFormat = MTLPixelFormatBGRA8Unorm_sRGB;
      madeira_pfx_set_upscale(1, 2000);
      layer_set_props(layer, 960, 540);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(2) && logged(@"pixel format '420f' (the picture is converted to it and back)"), "of 420v and 420f the full-range one is taken");
      frames(layer, q, 2, 40, 0);
      reset_ops(); g_cb_number = 0;
      frames(layer, q, 1, 40, 0);
      expect(has(@"own2: pass tex1(960x540) -> surf3.y") && has(@"own2: pass tex1(960x540) -> surf3.c") && before(@"-> surf3.c", @"own2: commit"),
             "the game's picture goes into the two planes through two passes");
      expect(has(@"VT: surf2 + surf3 -> 3 picture(s) at 0.25,0.50,0.75") && count_of(@"MetalFX") == 4 && count_of(@": present drawable") == 4
             && count_of(@" at a later time") == 3,
             "25 FPS on a 120 Hz screen: three pictures at a quarter, a half and three quarters; all four frames are enlarged by MetalFX and presented, the later three at their times");
      expect(before(@"pass surf4.y -> ", @"pass surf5.y -> ") && before(@"pass surf5.y -> ", @"pass surf6.y -> ") && before(@"pass surf6.y", @"MetalFX tex1(960x540)"),
             "the pictures are converted back and shown in order, the game's own frame last");
      /* 60 FPS: only the middle one fits. The engine measures real time between presents: on a loaded machine the 20 ms frames
       * can come late enough to count as a slower game (three pictures). Such a run is measured again, three times at most. */
      for (int tries = 0; tries < 3; tries++) {
        double t0 = now_s();
        frames(layer, q, 12, 20, 0); reset_ops(); frames(layer, q, 3, 20, 0);
        if ((now_s() - t0) / 15.0 < 0.026) break;
        dprintf(STDOUT_FILENO, "   (the machine was slow: %.1f ms a frame instead of 20; measured again)\n", (now_s() - t0) / 15.0 * 1000.0);
      }
      expect(has(@"-> 1 picture(s) at 0.50") && !has(@"3 picture(s)"), "the same session at 50 FPS: one picture, at the half");
      madeira_vti_session_ended();

      /* ---- a picture format that is not handled */
      return g_fails ? 1 : 0;
    }
    if (phase == 4) {
      /* ---- things VideoToolbox or the picture do not allow */
      setenv("MADEIRA_VT_INTERP", "1", 1);
      layer.pixelFormat = MTLPixelFormatRGBA16Float;
      layer_set_props(layer, 1280, 720);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(-1) && logged(@"only 8-bit BGRA and RGBA pictures are handled") && atomic_load(&g_vt_configs) == 0 && g_legacy_presents == 3,
             "a 16-bit float picture: one line says why, the frames present as without the key");
      { int n = atomic_load(&g_vti.attempts); frames(layer, q, 20, 1, 0); expect(atomic_load(&g_vti.attempts) == n, "and it is not tried again for the same picture"); }
      layer.pixelFormat = MTLPixelFormatRGBA8Unorm;
      g_vt_max_width = 1024;
      layer_set_props(layer, 1280, 720);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(-1) && logged(@"takes no pictures of 1280x720"), "a size VideoToolbox refuses: said once");
      g_vt_max_width = 1920; g_vt_start_fail = 1;
      layer_set_props(layer, 1152, 648);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(-1) && logged(@"did not start: the pipeline could not be made"), "a session that does not start: said with VideoToolbox's reason");
      g_vt_start_fail = 0; g_vt_formats[0] = 0x78343230u /* x420 */; g_vt_formats[1] = 0;
      layer_set_props(layer, 1024, 576);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(-1) && logged(@"takes only the pixel formats 'x420' here"), "only a 10-bit format on offer: said, with its name");
      g_vt_formats[0] = 0x42475241u;
      layer_set_props(layer, 800, 450);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(2), "an RGBA layer with BGRA buffers: a session");
      frames(layer, q, 1, 33, 0); reset_ops(); frames(layer, q, 1, 33, 0);
      expect(has(@"(800x450) -> surf2") && count_of(@": pass tex") == 1 && has(@"VT:") && has(@"pass surf4 -> tex"), "the picture goes in and out through a pass (the channel order differs)");
      g_vt_supported = 0;
      layer_set_props(layer, 640, 360);
      frames(layer, q, 3, 5, 0);
      expect(wait_state(-1) && logged(@"this device does not support low-latency frame interpolation"), "a device without it: said");
      { int legacy = g_legacy_presents; frames(layer, q, 5, 5, 0); expect(g_legacy_presents == legacy + 5, "and through all of it every frame of the game was presented"); }
      return g_fails ? 1 : 0;
    }
    if (phase == 5) {
      /* ---- the probe */
      static char text[8192];
      int worked;
      g_vt_max_width = 1920; g_vt_start_ms = 1; g_vt_process_ms = 1;
      worked = madeira_vti_probe(text, sizeof text);
      printf("%s", text);
      expect(strstr(text, "low-latency frame interpolation: supported on this device") && strstr(text, "largest picture it says it takes: 1920x1080"),
             "it says whether the device supports it and the largest picture, when VideoToolbox tells");
      expect(strstr(text, "640x360, 1 picture: works. Format 'BGRA' (offered: '420v' 'BGRA')") && strstr(text, "bar in place in 10 of 10 pictures"),
             "one picture: ten pairs, and the bar is in the middle of the two frames in all ten");
      expect(strstr(text, "1920x1080, 3 pictures: works.") && strstr(text, "bar in place in 30 of 30 pictures"), "three pictures: thirty, each at its quarter");
      expect(strstr(text, "2560x1440, 1 picture: refused") && strstr(text, "2560x1440, 3 pictures: refused"), "a size that is refused is said so");
      expect(strstr(text, "960x540, 2x larger + 1 picture: works.") && strstr(text, "1280x720, 2x larger + 1 picture: refused")
             && !strstr(text, "1920x1080, 2x larger"), "twice as large: works up to half the largest width; after two sizes in a row that do not start the larger ones are not tried");
      expect(strstr(text, "1280x720, 1 picture, in a Metal command buffer: works.") != NULL, "and it finds out whether the processor can be put into a Metal command buffer");
      expect(worked == 5 + 5 + 2 + 1 && strstr(text, "done: 13 line(s) worked") && !logged(@"[vt-interp] probe:"), "13 lines worked, counted; with a buffer nothing goes to stderr (the app writes the lines to its log)");
      expect(atomic_load(&g_vt_started) == atomic_load(&g_vt_ended) && atomic_load(&g_vt_started) == 13, "every session it started is ended");
      g_vt_supported = 0;
      expect(madeira_vti_probe(text, sizeof text) == 0 && strstr(text, "NOT supported"), "a device without it: one line");
      expect(madeira_vti_probe(NULL, 0) == 0 && logged(@"[vt-interp] probe: VideoToolbox low-latency frame interpolation: NOT supported"), "without a buffer the lines go to stderr");
      { char tiny[24]; g_vt_supported = 1; madeira_vti_probe(tiny, sizeof tiny); expect(strlen(tiny) < sizeof tiny, "a small buffer is not overrun"); }
      expect(atomic_load(&g_vti.want) == -1 && !g_vti.s, "the probe did not touch the in-game state (the key was not even read)");
      return g_fails ? 1 : 0;
    }
  }
  return 2;
}
