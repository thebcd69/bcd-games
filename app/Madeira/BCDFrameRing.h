// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDFrameRing.h
//  Madeira
//
//  The frame-time ring: when each Present of the process happened, kept for the in-game HUD
//  (its frame-time graph and 1% lows) and the session summary (the [session] log line).
//
//  The present path calls bcd_frame_ring_push() once per Present (tools/patch-winemetal-frame-ring.py
//  puts the call into DXMT's winemetal, next to the present counter that madeira_get_present_count
//  reads). It is wait-free and costs one clock read, two relaxed atomics and one store: it never
//  allocates, locks, logs or looks at who is reading. It runs whether or not a HUD is shown, so a
//  session that ran without one still has its summary.
//
//  The readers are Swift (BCDHUDLive.swift, BCDSessionRecorderLive.swift) and the host check
//  (tests/host/check-bcd-hud.py, which compiles BCDFrameRing.c alone).
//

#ifndef BCD_FRAME_RING_H
#define BCD_FRAME_RING_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// MARK: The present path

/// One Present has just been submitted. Wait-free; safe from any thread.
void bcd_frame_ring_push(void);

/// Forgets the newest present of the session's statistics, so that the next one starts a gap of its own (after a pause: the wait is not a frame).
void bcd_frame_forget_last(void);

/// The same with the clock given in ticks (mach_absolute_time units): the seam for tests.
void bcd_frame_ring_push_at(uint64_t ticks);

/// The clock the ring uses, in ticks, and the length of a tick.
uint64_t bcd_frame_now_ticks(void);
double bcd_frame_ticks_to_seconds(uint64_t ticks);

// MARK: The ring

/// A reader's place in the ring. Start with `bcd_frame_reader_start()` (or all zeros: it then reads
/// whatever the ring still holds).
typedef struct bcd_frame_reader {
    uint64_t cursor;   // how many presents it has taken from the ring
    uint64_t last;     // the newest timestamp it handed out
} bcd_frame_reader;

/// A reader that sees only the presents from now on.
bcd_frame_reader bcd_frame_reader_start(void);

/// Copies the timestamps (ticks, oldest first) that arrived since the reader's last call, at most
/// `max`, and moves the reader on. `lost` (may be NULL) gets how many presents were overwritten
/// before it looked: the reader asked less often than the ring is long. The newest present is left
/// for the next call, so a timestamp that is still being stored is never read half-written.
uint32_t bcd_frame_ring_drain(bcd_frame_reader *reader, uint64_t *out, uint32_t max, uint64_t *lost);

/// How many presents the ring has taken since the process began.
uint64_t bcd_frame_ring_count(void);

// MARK: Frames made by frame generation
//
// Apple's frame interpolation (winemetal's and KosmicKrisp's), Lossless Scaling (DXMT's, the Direct3D 12 bridge's and KosmicKrisp's)
// and MetalFX frame interpolation show frames of their own besides the game's. They come here, never into the ring above: the frame
// times, the lows and the session's statistics stay the game's own, and the HUD shows these next to the game's rate ("15+15").
// Exported by name (KosmicKrisp, a library of its own, finds bcd_frame_made_push_n in the process); wait-free, from any thread.

/// `count` frames made by frame generation have just been handed to the screen.
void bcd_frame_made_push_n(uint32_t count);
/// The same with the clock given in ticks: the seam for tests.
void bcd_frame_made_push_at(uint64_t ticks);
/// A reader of the frames made from now on, and their timestamps since its last call (as bcd_frame_ring_drain).
bcd_frame_reader bcd_frame_made_reader_start(void);
uint32_t bcd_frame_made_drain(bcd_frame_reader *reader, uint64_t *out, uint32_t max, uint64_t *lost);
/// How many frames made the ring has taken since the process began.
uint64_t bcd_frame_made_count(void);

// MARK: The session's statistics

/// Starts a new session's statistics (the histogram of frame times, the stalls, the worst frame).
/// The ring itself keeps flowing.
void bcd_frame_stats_begin(void);

/// What the session's frames add up to. A frame time is the gap between two Presents; a gap of 2 s
/// or more is a stall (loading, the app in the background, a menu) and is counted apart, so it
/// does not lift the 1% and 0.1% lows. Times are microseconds; the percentiles are the frame time
/// that 50, 90, 99 and 99.9% of the frames were quicker than (0.1 ms steps up to 100 ms, 5 ms to 2 s).
typedef struct bcd_frame_summary {
    uint64_t frames;        // gaps counted (the frames after the first)
    uint64_t active_us;     // their sum
    uint64_t stalls;        // gaps of 2 s or more
    uint64_t stall_us;      // their sum
    uint32_t worst_us;      // the longest gap of any kind (saturates at 4294 s)
    uint32_t p50_us, p90_us, p99_us, p999_us;
    uint64_t first_ticks;   // the first and the newest Present of the session; 0 before one
    uint64_t last_ticks;
} bcd_frame_summary;

void bcd_frame_stats_read(bcd_frame_summary *out);

/// Bucket scheme, exposed for the host check.
#define BCD_FR_FINE_BUCKETS 1000        // [0, 100 ms) in 100 us steps
#define BCD_FR_COARSE_BUCKETS 380       // [100 ms, 2 s) in 5 ms steps
#define BCD_FR_STALL_US 2000000u

/// Test seam: the tick length (numer/denom ns), as mach_timebase_info gives it. Not used by the app.
void bcd_frame_set_timebase(uint32_t numer, uint32_t denom);

#ifdef __cplusplus
}
#endif

#endif
