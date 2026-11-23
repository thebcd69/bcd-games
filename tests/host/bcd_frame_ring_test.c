// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The frame-time ring and the session statistics (app/Madeira/BCDFrameRing.c), compiled on the host by tests/host/check-bcd-hud.py.

#include "BCDFrameRing.h"

#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int failures;
#define CHECK(cond, what) do { int ok_ = (cond); printf("%s %s\n", ok_ ? "ok  " : "FAIL", what); if (!ok_) failures++; } while (0)

static int near_us(uint32_t got, uint32_t want, uint32_t tolerance) {
    return got + tolerance >= want && got <= want + tolerance;
}

typedef struct { int id; int n; } producer_arg;
static _Atomic uint64_t g_clock = 1000000000ull;

static void *producer(void *p) {
    producer_arg *a = p;
    for (int i = 0; i < a->n; i++) {
        uint64_t t = atomic_fetch_add_explicit(&g_clock, 16000000ull, memory_order_relaxed);
        bcd_frame_ring_push_at(t);
    }
    return NULL;
}

static _Atomic int g_reading = 1;
static uint64_t g_drained, g_lost;
static int g_out_of_order;

static void *reader(void *p) {
    (void)p;
    bcd_frame_reader r = bcd_frame_reader_start();
    uint64_t buf[256], last = 0;
    while (atomic_load(&g_reading)) {
        uint64_t lost = 0;
        uint32_t n = bcd_frame_ring_drain(&r, buf, 256, &lost);
        g_lost += lost;
        for (uint32_t i = 0; i < n; i++) {
            if (buf[i] <= last) g_out_of_order++;
            last = buf[i];
        }
        g_drained += n;
    }
    return NULL;
}

int main(void) {
    // A tick is a nanosecond here: the numbers below are exact.
    bcd_frame_set_timebase(1, 1);
    CHECK(near_us((uint32_t)(bcd_frame_ticks_to_seconds(2500000000ull) * 1000), 2500, 1), "ticks become seconds through the timebase");
    bcd_frame_set_timebase(125, 3);
    CHECK(near_us((uint32_t)(bcd_frame_ticks_to_seconds(24000000ull) * 1000), 1000, 1), "... a 24 MHz counter (125/3): 24 million ticks are a second");
    bcd_frame_set_timebase(1, 1);

    // ---- a steady 60 fps with a slow frame in every hundred ----------------------------------------
    bcd_frame_stats_begin();
    bcd_frame_summary s;
    bcd_frame_stats_read(&s);
    CHECK(s.frames == 0 && s.first_ticks == 0 && s.worst_us == 0, "a new session starts empty");
    uint64_t t = 5000000000ull;
    bcd_frame_ring_push_at(t);
    for (int i = 1; i <= 1000; i++) {
        t += (i % 100 == 0) ? 50000000ull : 16666667ull;
        bcd_frame_ring_push_at(t);
    }
    bcd_frame_stats_read(&s);
    CHECK(s.frames == 1000, "the first present has no gap: a thousand gaps for 1001 presents");
    CHECK(near_us(s.p50_us, 16650, 100), "the median frame is 16.65 ms");
    CHECK(near_us(s.p90_us, 16650, 100), "the 90th percentile is still a normal frame");
    CHECK(near_us(s.p99_us, 16650, 100), "10 slow frames of 1000 (1.0%): the 99th percentile frame is a normal one");
    CHECK(near_us(s.p999_us, 50050, 100), "... and the 99.9th is a slow one (50 ms)");
    CHECK(near_us(s.worst_us, 50000, 5), "the worst frame is 50 ms");
    CHECK(s.stalls == 0 && s.stall_us == 0, "no stalls");
    uint64_t active = 990ull * 16666 + 10ull * 50000;
    CHECK(s.active_us + 1000 >= active && s.active_us <= active + 1000, "the active time is the sum of the gaps");
    CHECK(s.first_ticks == 5000000000ull && s.last_ticks == t, "the first and the newest present are remembered");

    // ---- a stall is counted apart ---------------------------------------------------------------------
    t += 3000000000ull;
    bcd_frame_ring_push_at(t);
    t += 16666667ull;
    bcd_frame_ring_push_at(t);
    bcd_frame_summary s2;
    bcd_frame_stats_read(&s2);
    CHECK(s2.stalls == 1 && s2.stall_us >= 2999000 && s2.stall_us <= 3001000, "a 3 s gap is a stall");
    CHECK(s2.frames == s.frames + 1, "... the frame after it is a normal one; the stall is in no bucket");
    CHECK(s2.p999_us == s.p999_us && s2.p99_us == s.p99_us, "... so it does not lift the lows");
    CHECK(near_us(s2.worst_us, 3000000, 10), "... but it is the worst gap");

    // ---- a very short gap, a repeated timestamp, a stamp that goes back -------------------------------------
    bcd_frame_stats_begin();
    bcd_frame_ring_push_at(1000);
    bcd_frame_ring_push_at(1000);
    bcd_frame_ring_push_at(900);
    bcd_frame_ring_push_at(2000);
    bcd_frame_stats_read(&s);
    CHECK(s.frames <= 1, "equal or backwards timestamps make no gap");

    // ---- begin() starts again ----------------------------------------------------------------------------
    bcd_frame_stats_begin();
    bcd_frame_stats_read(&s);
    CHECK(s.frames == 0 && s.stalls == 0 && s.active_us == 0 && s.worst_us == 0 && s.p50_us == 0, "begin() clears the session");

    // ---- the ring ------------------------------------------------------------------------------------------
    uint64_t base = bcd_frame_ring_count();
    bcd_frame_reader r = bcd_frame_reader_start();
    CHECK(r.cursor == base, "a reader starts at the present count");
    uint64_t out[64], lost = 99;
    CHECK(bcd_frame_ring_drain(&r, out, 64, &lost) == 0 && lost == 0, "nothing new: nothing drained");
    for (int i = 1; i <= 10; i++) bcd_frame_ring_push_at(8000000000ull + (uint64_t)i * 1000);
    uint32_t n = bcd_frame_ring_drain(&r, out, 64, &lost);
    CHECK(n == 9 && lost == 0, "ten presents: nine drained, the newest left for the next call");
    CHECK(out[0] == 8000001000ull && out[8] == 8000009000ull, "oldest first, exact");
    bcd_frame_ring_push_at(8000011000ull);
    n = bcd_frame_ring_drain(&r, out, 64, &lost);
    CHECK(n == 1 && out[0] == 8000010000ull, "the next call takes the one that was left");
    for (int i = 12; i <= 40; i++) bcd_frame_ring_push_at(8000000000ull + (uint64_t)i * 1000);
    uint32_t first = bcd_frame_ring_drain(&r, out, 10, &lost);
    uint32_t more = bcd_frame_ring_drain(&r, out + 10, 54, &lost);
    CHECK(first == 10 && more == 19 && out[0] == 8000011000ull && out[28] == 8000039000ull, "... in order, none twice or lost");
    // a reader that does not look for longer than the ring is long loses the oldest
    bcd_frame_reader slow = bcd_frame_reader_start();
    for (int i = 1; i <= 10000; i++) bcd_frame_ring_push_at(9000000000ull + (uint64_t)i * 1000);
    uint64_t got = 0, total_lost = 0;
    uint64_t buf[512];
    for (;;) {
        uint64_t l = 0;
        uint32_t k = bcd_frame_ring_drain(&slow, buf, 512, &l);
        total_lost += l;
        got += k;
        if (!k) break;
    }
    CHECK(total_lost > 5000 && got > 3500 && got + total_lost == 9999, "a reader 10000 presents behind loses the old ones and says how many");

    // ---- many presenting threads and a reader at once ----------------------------------------------------------
    bcd_frame_stats_begin();
    uint64_t before = bcd_frame_ring_count();
    pthread_t readers, threads[4];
    producer_arg args[4];
    g_drained = g_lost = 0;
    g_out_of_order = 0;
    atomic_store(&g_reading, 1);
    pthread_create(&readers, NULL, reader, NULL);
    for (int i = 0; i < 4; i++) { args[i].id = i; args[i].n = 200000; pthread_create(&threads[i], NULL, producer, &args[i]); }
    for (int i = 0; i < 4; i++) pthread_join(threads[i], NULL);
    atomic_store(&g_reading, 0);
    pthread_join(readers, NULL);
    CHECK(bcd_frame_ring_count() - before == 800000, "800000 presents from four threads are all counted");
    CHECK(g_drained + g_lost <= 800000 && g_drained > 0, "the reader saw some and counted what it lost");
    CHECK(g_out_of_order == 0, "the reader never hands out a timestamp that is not newer than the last");
    bcd_frame_stats_read(&s);
    printf("note: %" PRIu64 " gaps counted of 800000 presents from four racing threads\n", s.frames);
    CHECK(s.frames > 100000 && s.frames <= 800000, "the statistics counted gaps (a present whose stamp is older than the one before makes none)");

    // ---- what one present costs ------------------------------------------------------------------------------
    bcd_frame_stats_begin();
    struct timespec a, b;
    const int N = 5000000;
    uint64_t clock = 1ull << 40;
    clock_gettime(CLOCK_MONOTONIC, &a);
    for (int i = 0; i < N; i++) { clock += 16666667ull; bcd_frame_ring_push_at(clock); }
    clock_gettime(CLOCK_MONOTONIC, &b);
    double ns = ((b.tv_sec - a.tv_sec) * 1e9 + (b.tv_nsec - a.tv_nsec)) / N;
    printf("note: %.1f ns per present with the clock given\n", ns);
#if defined(__has_feature)
#if __has_feature(thread_sanitizer)
    printf("note: the thread sanitizer instruments every access: the cost is not judged under it\n");
#define BCD_JUDGE_COST 0
#endif
#endif
#ifndef BCD_JUDGE_COST
#define BCD_JUDGE_COST 1
#endif
    if (BCD_JUDGE_COST) CHECK(ns < 600, "a present costs well under a microsecond (this host, address and undefined-behaviour sanitizers on)");
    bcd_frame_stats_read(&s);
    CHECK(s.frames == (uint64_t)N - 1, "and every one was counted");
    uint64_t c0 = bcd_frame_now_ticks();
    uint64_t c1 = bcd_frame_now_ticks();
    CHECK(c1 >= c0, "the clock does not go back");

    /* frames made by frame generation: a ring of their own, never the game's */
    {
        bcd_frame_summary before, after;
        uint64_t ring_before = bcd_frame_ring_count(), made_before = bcd_frame_made_count(), got[64];
        bcd_frame_reader mr = bcd_frame_made_reader_start(), gr = bcd_frame_reader_start();
        uint64_t lost = 0;
        uint32_t n, k;
        int ordered = 1;
        bcd_frame_stats_read(&before);
        for (k = 0; k < 10; k++) bcd_frame_made_push_n(1);
        bcd_frame_made_push_n(3);
        bcd_frame_made_push_n(0);
        bcd_frame_stats_read(&after);
        CHECK(bcd_frame_made_count() == made_before + 13 && bcd_frame_ring_count() == ring_before,
              "frames made go into their own ring (13 of them), none into the game's");
        CHECK(after.frames == before.frames && after.active_us == before.active_us && after.worst_us == before.worst_us,
              "... and the session's statistics (the game's frame times) do not see them");
        n = bcd_frame_made_drain(&mr, got, 64, &lost);
        for (k = 1; k < n; k++) ordered = ordered && got[k] > got[k - 1];
        CHECK(n == 12 && lost == 0 && ordered, "a reader gets them in order, every one but the newest (left for its next call), three at once one tick apart");
        CHECK(bcd_frame_ring_drain(&gr, got, 64, &lost) == 0, "the game's reader gets none of them");
        bcd_frame_made_push_n(1);
        CHECK(bcd_frame_made_drain(&mr, got, 64, &lost) == 1, "the newest comes with the next call");
    }

    printf(failures == 0 ? "bcd_frame_ring_test: PASS\n" : "bcd_frame_ring_test: FAILED\n");
    return failures == 0 ? 0 : 1;
}
