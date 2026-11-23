// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDFrameRing.c
//  Madeira
//
//  The frame-time ring and the session's frame statistics (BCDFrameRing.h).
//
//  What one Present costs here: one clock read, one fetch-add and one store into the ring, one
//  exchange for the gap to the Present before, one fetch-add into the histogram, and a compare
//  that is false almost always (the worst gap). No lock, no allocation, no system call beyond the
//  clock (mach_absolute_time reads the counter register). Nothing here ever waits for a reader.
//

#include "BCDFrameRing.h"
#include "BCDPauseGate.h"

#include <stdatomic.h>
#include <string.h>

#if defined(__APPLE__)
#include <mach/mach_time.h>
#else
#include <time.h>
#endif

/// 4096 presents: 68 s at 60 fps, 17 s at 240. A reader that asks every few seconds loses none.
#define RING_SIZE 4096u
#define RING_MASK (RING_SIZE - 1u)
/// A reader never reaches into the newest slots: a producer may have reserved one and not stored yet.
#define RING_SAFE (RING_SIZE - 16u)
#define BUCKETS (BCD_FR_FINE_BUCKETS + BCD_FR_COARSE_BUCKETS)

static _Atomic uint64_t g_ring[RING_SIZE];
static _Atomic uint64_t g_head;          // presents taken, ever
/// The frames made by frame generation: the same kind of ring, apart (BCDFrameRing.h).
static _Atomic uint64_t g_made_ring[RING_SIZE];
static _Atomic uint64_t g_made_head;
static _Atomic uint64_t g_made_newest;   // the newest timestamp handed out: each one is later than the one before
static _Atomic uint64_t g_prev;          // the newest present's timestamp this session (0: none yet)
static _Atomic uint32_t g_hist[BUCKETS];
static _Atomic uint64_t g_active_us, g_stalls, g_stall_us;
static _Atomic uint64_t g_first, g_newest;
static _Atomic uint32_t g_worst;
static _Atomic uint32_t g_numer;         // a tick is g_numer / g_denom nanoseconds; 0 until read
static _Atomic uint32_t g_denom = 1;

static void timebase_init(void) {
#if defined(__APPLE__)
    mach_timebase_info_data_t tb;
    if (mach_timebase_info(&tb) == 0 && tb.numer != 0 && tb.denom != 0) {
        atomic_store_explicit(&g_denom, tb.denom, memory_order_relaxed);
        atomic_store_explicit(&g_numer, tb.numer, memory_order_relaxed);
        return;
    }
#endif
    atomic_store_explicit(&g_denom, 1, memory_order_relaxed);
    atomic_store_explicit(&g_numer, 1, memory_order_relaxed);
}

void bcd_frame_set_timebase(uint32_t numer, uint32_t denom) {
    if (numer == 0 || denom == 0) return;
    atomic_store_explicit(&g_denom, denom, memory_order_relaxed);
    atomic_store_explicit(&g_numer, numer, memory_order_relaxed);
}

uint64_t bcd_frame_now_ticks(void) {
#if defined(__APPLE__)
    return mach_absolute_time();
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
#endif
}

double bcd_frame_ticks_to_seconds(uint64_t ticks) {
    if (atomic_load_explicit(&g_numer, memory_order_relaxed) == 0) timebase_init();
    uint32_t n = atomic_load_explicit(&g_numer, memory_order_relaxed);
    uint32_t d = atomic_load_explicit(&g_denom, memory_order_relaxed);
    return (double)ticks * (double)n / (double)d / 1e9;
}

static inline uint32_t bucket_for(uint64_t us) {
    if (us < 100000u) return (uint32_t)(us / 100u);
    uint32_t b = BCD_FR_FINE_BUCKETS + (uint32_t)((us - 100000u) / 5000u);
    return b < BUCKETS ? b : BUCKETS - 1u;
}

/// The middle of a bucket, in microseconds.
static inline uint32_t bucket_middle_us(uint32_t b) {
    if (b < BCD_FR_FINE_BUCKETS) return b * 100u + 50u;
    return 100000u + (b - BCD_FR_FINE_BUCKETS) * 5000u + 2500u;
}

void bcd_frame_ring_push_at(uint64_t t) {
    uint64_t slot = atomic_fetch_add_explicit(&g_head, 1, memory_order_relaxed);
    atomic_store_explicit(&g_ring[slot & RING_MASK], t, memory_order_relaxed);
    atomic_store_explicit(&g_newest, t, memory_order_relaxed);
    if (atomic_load_explicit(&g_first, memory_order_relaxed) == 0) {
        uint64_t none = 0;
        atomic_compare_exchange_strong_explicit(&g_first, &none, t, memory_order_relaxed, memory_order_relaxed);
    }
    uint64_t prev = atomic_exchange_explicit(&g_prev, t, memory_order_relaxed);
    if (prev == 0 || t <= prev) return;
    uint32_t numer = atomic_load_explicit(&g_numer, memory_order_relaxed);
    if (numer == 0) { timebase_init(); numer = atomic_load_explicit(&g_numer, memory_order_relaxed); }
    uint32_t denom = atomic_load_explicit(&g_denom, memory_order_relaxed);
    uint64_t us = ((t - prev) * numer) / ((uint64_t)denom * 1000u);
    uint64_t capped = us > 0xFFFFFFFFull ? 0xFFFFFFFFull : us;
    if (capped > atomic_load_explicit(&g_worst, memory_order_relaxed)) {
        uint32_t seen = atomic_load_explicit(&g_worst, memory_order_relaxed);
        while (capped > seen && !atomic_compare_exchange_weak_explicit(&g_worst, &seen, (uint32_t)capped,
                                                                       memory_order_relaxed, memory_order_relaxed)) {}
    }
    if (us >= BCD_FR_STALL_US) {
        atomic_fetch_add_explicit(&g_stalls, 1, memory_order_relaxed);
        atomic_fetch_add_explicit(&g_stall_us, us, memory_order_relaxed);
        return;
    }
    atomic_fetch_add_explicit(&g_active_us, us, memory_order_relaxed);
    atomic_fetch_add_explicit(&g_hist[bucket_for(us)], 1, memory_order_relaxed);
}

void bcd_frame_ring_push(void) {
    bcd_frame_ring_push_at(bcd_frame_now_ticks());
    // Paused (the in-game menu): this frame is on its way to the screen; the next one waits until the game is resumed (BCDPauseGate.c).
    bcd_pause_gate_pass();
}

void bcd_frame_forget_last(void) {
    atomic_store_explicit(&g_prev, 0, memory_order_relaxed);
}

uint64_t bcd_frame_ring_count(void) {
    return atomic_load_explicit(&g_head, memory_order_acquire);
}

bcd_frame_reader bcd_frame_reader_start(void) {
    bcd_frame_reader r;
    r.cursor = atomic_load_explicit(&g_head, memory_order_acquire);
    r.last = 0;
    return r;
}

static uint32_t ring_drain(_Atomic uint64_t *ring, _Atomic uint64_t *head_p, bcd_frame_reader *reader, uint64_t *out, uint32_t max,
                           uint64_t *lost) {
    uint64_t gone = 0;
    uint32_t n = 0;
    if (!reader || !out) { if (lost) *lost = 0; return 0; }
    uint64_t head = atomic_load_explicit(head_p, memory_order_acquire);
    uint64_t end = head > 0 ? head - 1 : 0;
    uint64_t oldest = end > RING_SAFE ? end - RING_SAFE : 0;
    if (reader->cursor < oldest) { gone = oldest - reader->cursor; reader->cursor = oldest; }
    while (reader->cursor < end && n < max) {
        uint64_t t = atomic_load_explicit(&ring[reader->cursor & RING_MASK], memory_order_relaxed);
        reader->cursor++;
        if (t == 0 || t <= reader->last) continue;   // not stored yet (first lap) or out of order
        out[n++] = t;
        reader->last = t;
    }
    if (lost) *lost = gone;
    return n;
}

uint32_t bcd_frame_ring_drain(bcd_frame_reader *reader, uint64_t *out, uint32_t max, uint64_t *lost) {
    return ring_drain(g_ring, &g_head, reader, out, max, lost);
}

// MARK: Frames made by frame generation

#define BCD_FR_EXPORT __attribute__((used, visibility("default")))

BCD_FR_EXPORT void bcd_frame_made_push_at(uint64_t t) {
    uint64_t slot = atomic_fetch_add_explicit(&g_made_head, 1, memory_order_relaxed);
    atomic_store_explicit(&g_made_ring[slot & RING_MASK], t, memory_order_relaxed);
}

BCD_FR_EXPORT void bcd_frame_made_push_n(uint32_t count) {
    // Each timestamp later than the one before (the reader hands out only newer ones, and two calls can read the same tick):
    // the clock, or one tick past the newest, for several at once one tick apart.
    if (count == 0) return;
    if (count > 8u) count = 8u;
    uint64_t now = bcd_frame_now_ticks(), prev = atomic_load_explicit(&g_made_newest, memory_order_relaxed), t;
    do {
        t = now > prev ? now : prev + 1;
    } while (!atomic_compare_exchange_weak_explicit(&g_made_newest, &prev, t + count - 1, memory_order_relaxed, memory_order_relaxed));
    for (uint32_t i = 0; i < count; i++) bcd_frame_made_push_at(t + i);
}

bcd_frame_reader bcd_frame_made_reader_start(void) {
    bcd_frame_reader r;
    r.cursor = atomic_load_explicit(&g_made_head, memory_order_acquire);
    r.last = 0;
    return r;
}

uint32_t bcd_frame_made_drain(bcd_frame_reader *reader, uint64_t *out, uint32_t max, uint64_t *lost) {
    return ring_drain(g_made_ring, &g_made_head, reader, out, max, lost);
}

uint64_t bcd_frame_made_count(void) {
    return atomic_load_explicit(&g_made_head, memory_order_acquire);
}

void bcd_frame_stats_begin(void) {
    for (uint32_t i = 0; i < BUCKETS; i++) atomic_store_explicit(&g_hist[i], 0, memory_order_relaxed);
    atomic_store_explicit(&g_active_us, 0, memory_order_relaxed);
    atomic_store_explicit(&g_stalls, 0, memory_order_relaxed);
    atomic_store_explicit(&g_stall_us, 0, memory_order_relaxed);
    atomic_store_explicit(&g_worst, 0, memory_order_relaxed);
    atomic_store_explicit(&g_first, 0, memory_order_relaxed);
    atomic_store_explicit(&g_newest, 0, memory_order_relaxed);
    atomic_store_explicit(&g_prev, 0, memory_order_relaxed);
}

/// The middle of the bucket in which the `fraction` of the frames (0..1) has been reached.
static uint32_t percentile_us(const uint32_t *hist, uint64_t total, double fraction) {
    if (total == 0) return 0;
    uint64_t want = (uint64_t)(fraction * (double)total);
    if ((double)want < fraction * (double)total) want++;   // round up: the p-th percentile frame
    if (want == 0) want = 1;
    uint64_t seen = 0;
    for (uint32_t b = 0; b < BUCKETS; b++) {
        seen += hist[b];
        if (seen >= want) return bucket_middle_us(b);
    }
    return bucket_middle_us(BUCKETS - 1u);
}

void bcd_frame_stats_read(bcd_frame_summary *out) {
    if (!out) return;
    memset(out, 0, sizeof *out);
    uint32_t hist[BUCKETS];
    uint64_t total = 0;
    for (uint32_t i = 0; i < BUCKETS; i++) {
        hist[i] = atomic_load_explicit(&g_hist[i], memory_order_relaxed);
        total += hist[i];
    }
    out->frames = total;
    out->active_us = atomic_load_explicit(&g_active_us, memory_order_relaxed);
    out->stalls = atomic_load_explicit(&g_stalls, memory_order_relaxed);
    out->stall_us = atomic_load_explicit(&g_stall_us, memory_order_relaxed);
    out->worst_us = atomic_load_explicit(&g_worst, memory_order_relaxed);
    out->p50_us = percentile_us(hist, total, 0.50);
    out->p90_us = percentile_us(hist, total, 0.90);
    out->p99_us = percentile_us(hist, total, 0.99);
    out->p999_us = percentile_us(hist, total, 0.999);
    out->first_ticks = atomic_load_explicit(&g_first, memory_order_relaxed);
    out->last_ticks = atomic_load_explicit(&g_newest, memory_order_relaxed);
}
