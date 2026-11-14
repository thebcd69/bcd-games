/* Portions copyright 2026 ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (pull request #498)
 * under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md. */
#import "JITAllocator.h"
#import "FEXBridge.h"
#import "WineServerBridge.h"
#import "WineProcessBridge.h"
#import "IOSDisplayShim.h"
#import "Winios/Winios.h"
#import "Winios/WiniosCursor.h"
// ml2106: rumble / DualSense output to the physical pad (PadOutput.m)
#import "PadOutput.h"
#import "MemoryHostTest.h"
// Steam content decoders (liblzma shim, zstd decoder, zip chunks), used by the owned library's downloads.
#import "SwiftSteam/lzma_shim.h"
// itch.io downloads: the .7z reader (SwiftItch/ItchArchive.swift unpacks with it; zlib and liblzma are system libraries).
#import "SwiftItch/itch_7z.h"
// On-device remote pairing for Built-in StikJIT (build/rppairing-ios, JITPairing.swift).
#import "MadeiraRPPairing.h"
// BCD (round 604): the phone shaking with a game's rumble (BCDPhoneRumble.m), switched by the in-game menu.
#import "BCDPhoneRumble.h"
// BCD (round 604): the in-game menu's Pause holds the present path at its next frame (BCDPauseGate.c).
#import "BCDPauseGate.h"
// madeira-bcd: the bundled prefix template, read by the Dock's Reset (DockReset.swift).
#include "PrefixExtractor.h"
// BCD: the cabinet reader that unpacks Microsoft's VC++ installer on the device (VCRuntimeDownload.swift).
#include "BCDCab.h"
// BCD: the frame-time ring the present path writes (tools/patch-winemetal-frame-ring.py), read by the HUD and the session summary.
#include "BCDFrameRing.h"

// Wine file-based logging (server_ios.c)
void wine_log_set_file(const char *path);

// UI log callback (wine_log_ios.c) — forwards C logs to Swift UI
typedef void (*wine_ui_log_callback_t)(const char *message);
void wine_set_ui_log_callback(wine_ui_log_callback_t cb);

// BCD in the background (audio_null_ios.c, BackgroundRun.swift; Madeira #498): keep the
// RemoteIO endpoint running with no client (keepalive), render silence (mute),
// or stop it so iOS can suspend BCD (halt). Returns 1 when it runs.
int madeira_audio_background(int keepalive, int mute, int halt);
// Start the endpoint again after an audio interruption ended.
void madeira_audio_resume_after_interruption(void);
// Restart a running endpoint that rendered nothing since the last call (1 = restarted).
int madeira_audio_check(void);
// CPU cap while BCD runs in the background (Winios/BackgroundCPUCap.c):
// percent of one core, 0 = off (returns once every held thread runs again).
void madeira_bg_cpu_cap(int percent);
void madeira_bg_cpu_stats(double *cpu_s, double *wall_s, double *held_s);
// Metal command buffers off the main thread wait while BCD is inactive
// (Winios/MetalBackgroundGate.m). Returns the number of classes wrapped.
int madeira_metal_gate_install(void);
// madeira-bcd: hold (1) or release (0) that wait by hand, for a gate made after the notification.
void madeira_metal_gate_hold(int inactive);

// DXMT present counter (winemetal_unix.c) — for SwiftUI FPS overlay
#include <stdint.h>
uint64_t madeira_get_present_count(void);
// ml1174: GPU busy time for the performance overlay (winemetal_unix.c). While
// enabled, each committed command buffer adds its GPU time (union) on completion.
void madeira_gpu_meter_enable(int on);
double madeira_gpu_meter_busy_seconds(void);
uint64_t madeira_gpu_meter_cmdbufs(void);
// madeira-bcd HUD (BCDHUDLive.swift): read-only numbers for the HUD's JIT pool and swap items (virtual_ios.c): the pool's bytes handed out and
// its size, and the bytes of game data held in the swap file now.
void madeira_hud_jit_pool(unsigned long long *used, unsigned long long *total);
unsigned long long madeira_hud_swap_bytes(void);
// ml1098: ask the D3D12 runtime to capture the next N frames (winemetal_unix.c)
void madeira_capture_request(int frames);
// BCD (round 604): the in-game menu's Pause silences the audio endpoint (build/ntdll-unix/audio_null_ios.c); BCDFrameRing.h holds the gate for the frames.
void madeira_audio_set_paused(int paused);
// ml1133: ECO switch (ntdll unix sync.c). 1 = every guest thread drops to a
// low QoS class (efficiency cores, lower clocks) to save the SoC burst budget.
void madeira_set_eco(int on);
int madeira_get_eco(void);
// ml1136: live GPU encoder-sync mode (winemetal_unix.c): 1, 5, 6; 7 = mode 0 (no fences).
// The D3D12 runtime applies it at the next Present, for lists that start after that.
void madeira_set_fence_mode(int mode);

// DXMT vsync-lock toggle (winemetal_unix.c) — 1 = pace presents to 60
// via afterMinimumDuration, 0 = free-run to display max (120 ProMotion,
// requires CADisableMinimumFrameDurationOnPhone in Info.plist).
// Read per present; safe to flip live mid-game.
void madeira_set_vsync_locked(int locked);
int madeira_get_vsync_locked(void);

// madeira-bcd: frame generation's live switch (winemetal_unix.c, tools/patch-winemetal-framegen-live.py). madeira_fgl_set: 1 = on,
// 0 = off, -1 = as the game started (env.MADEIRA_FRAMEGEN); it and madeira_fgl_get return what is in force (1 or 0; always 0 below
// iOS 26). madeira_fgl_set_measure(1): the interpolator runs, nothing extra is shown. madeira_fgl_set_guard: the GPU guard's limit in
// percent (0 = off, 1 = 95, -1 = the key env.MADEIRA_FRAMEGEN_GPU_GUARD or the default). madeira_fgl_status returns 0 off, 1 on and
// waiting, 2 generating, 3 paused (the GPU is full), 4 cannot run here, 5 measuring only; gpu_busy is 0 to 1, or -1 without the guard.
int madeira_fgl_set(int on);
// madeira-bcd: a session ended: frame generation's and present-fx's readings of their keys go, so a later session reads its own.
void madeira_fgl_session_reset(void);
void madeira_pfx_session_reset(void);
int madeira_fgl_get(void);
void madeira_fgl_set_measure(int on);
void madeira_fgl_set_guard(int percent);
int madeira_fgl_status(int *width, int *height, double *game_fps, double *generated_fps, double *gpu_busy);
// madeira-bcd: present-fx (winemetal_unix.c, tools/patch-winemetal-present-fx.py; only a session with env.MADEIRA_PRESENT_FX = 1 acts on
// them). madeira_pfx_set_screen: the screen's size in pixels, for "to the screen". madeira_pfx_set_upscale: 0 = none, 1 = a factor
// (milli = the factor times 1000, 1100 to 2000), 2 = to the screen; it takes effect at the next frame. madeira_pfx_status returns the
// mode in force (-1 before a layer was set up) with the size the game draws, the size shown and how the last frame was enlarged
// (0 a copy, 1 MetalFX, 2 bilinear).
// madeira-bcd: Lossless Scaling frame generation's pacing (winemetal_unix.c, tools/patch-winemetal-lsfg-pacing.py): how many frames are
// shown per game frame (2 to 4 while frames are added; 1 = none, the default). The frame-rate limit then counts every frame shown.
void madeira_lsfg_set_shown(int frames_per_game_frame);
void madeira_pfx_set_screen(int a, int b);
void madeira_pfx_set_upscale(int mode, int milli);
int madeira_pfx_status(int *game_w, int *game_h, int *shown_w, int *shown_h, int *kind);
// madeira-bcd: the VideoToolbox frame interpolation experiment (winemetal_unix.c, tools/patch-winemetal-vt-interp.py). madeira_vti_set:
// the in-game menu's switch (1, 0, -1 = as the game started); returns what is in force. madeira_vti_status: 0 off, 1 the model is
// loading, 2 ready, 3 making pictures, 4 not possible (madeira_vti_why writes the reason and returns its length). madeira_vti_probe: the
// device test, slow (a model is loaded for every line), never on the main thread; `out` gets its lines. All of them do nothing in a
// session without MADEIRA_VT_INTERP, except the probe, which needs no session.
int madeira_vti_set(int on);
int madeira_vti_status(int *width, int *height, double *game_fps, double *generated_fps, double *pair_ms);
int madeira_vti_why(char *out, int cap);
void madeira_vti_session_ended(void);
int madeira_vti_probe(char *out, int cap);
// madeira-bcd: scheduler hints and the fast cores' clock (BCDSchedHints.m; bcd.cfg sched-hints). madeira_sched_task_tiers reads the
// process's latency and throughput tiers (0 = read); madeira_sched_task_qos asks for two tiers and returns the kernel's two answers
// (0 = taken); madeira_cpu_clock_sample: milliseconds the process ran on the fast cores, their cycles, and the time now (0 = read).
int madeira_sched_task_tiers(unsigned *latency, unsigned *throughput);
void madeira_sched_task_qos(unsigned latency, unsigned throughput, int *kr_latency, int *kr_throughput);
unsigned madeira_sched_first_latency_tier(void);
unsigned madeira_sched_first_throughput_tier(void);
int madeira_cpu_clock_sample(double *fast_ms, double *fast_cycles, double *wall_ms);

// ml1050: what the PANEL can do and what we asked it for, published from
// Swift (only UIKit knows) so the native [frame] line can print the
// quantisation grid every present snaps to. panel_hz = the display's own
// maximum, intent_hz = the CADisplayLink rate currently requested (0 = no
// intent armed). Both are diagnostics; nothing branches on them.
// Defined by DXMT from willfaust/dxmt#1 on; IOSDisplayShim.m carries a weak
// no-op fallback, so the app links and runs against main's DXMT too.
void madeira_set_display_max_fps(int panel_hz, int intent_hz);
// 1 when DXMT's own madeira_set_display_max_fps (and with it the 30 FPS cap,
// vsync mode 3) is linked; valid after the first madeira_set_display_max_fps call.
int madeira_dxmt_has_display_pacing(void);
// 1 when DXMT has the 40 FPS cap (vsync mode 4). Defined by DXMT; IOSDisplayShim.m
// carries a weak fallback that returns 0, so the app links against a DXMT without it.
int madeira_dxmt_has_40_cap(void);

/* ml526: startup phase timeline (Winios.m) */
void winios_phase(const char *name);

/* madeira-d3d12: M1 shader-converter canary, compiled into libdxmt.
 * Returns the number of failed checks; 0 means the gate passed. Output goes to
 * stderr, which is already captured into bcd-log.txt. */
int madeira_d3d12_canary_run(const char *fixture_dir, const char *dylib_path,
                             void (*sink)(const char *));
/* Same run, but also writes the full per-check transcript to log_path, which is
 * how the in-app detail is captured: stderr is not redirected into
 * bcd-log.txt until later in startup. */
int madeira_d3d12_canary_run_log(const char *fixture_dir, const char *dylib_path,
                                 void (*sink)(const char *), const char *log_path,
                                 const char *build_id);
