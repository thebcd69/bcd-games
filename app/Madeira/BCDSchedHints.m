// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315,
// app/Sources/Core/SampleProfiler.c: sched_hint_task_qos, cpu_clock_sample) under GPL-3.0-or-later and modified for BCD in
// November 2026. See THIRD-PARTY-NOTICES.md.

// Scheduler hints for a running game, and the counters behind the watch on the fast cores' clock (app/Madeira/BCDSchedHints.swift,
// BCDSchedHintsLive.swift). Nothing here runs unless a session asks through bcd.cfg's sched-hints.
//
// BCD's changes to Porthole's two functions: the kernel takes the two tiers only as its two-field structure (a count of 1, as
// Porthole passes, is refused with KERN_INVALID_ARGUMENT), the tiers can be read back for the log, and the process is put back to
// what it had when the session ends.

#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/task_policy.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

// proc_pid_rusage is in libproc, whose header the iOS SDK does not ship; the engine's [xp] sampler (build/ntdll-unix/server_ios.c)
// reads the same counters the same way.
extern int proc_pid_rusage(int pid, int flavor, void *buffer);

/// The process's latency and throughput tiers as the kernel has them. 0 = read.
int madeira_sched_task_tiers(unsigned *latency, unsigned *throughput) {
    struct task_qos_policy q;
    mach_msg_type_number_t count = TASK_QOS_POLICY_COUNT;
    boolean_t get_default = FALSE;
    memset(&q, 0, sizeof q);
    kern_return_t kr = task_policy_get(mach_task_self(), TASK_BASE_QOS_POLICY, (task_policy_t)&q, &count, &get_default);
    if (kr == KERN_SUCCESS) { *latency = (unsigned)q.task_latency_qos_tier; *throughput = (unsigned)q.task_throughput_qos_tier; }
    return (int)kr;
}

/// Asks the kernel for the given latency and throughput tiers for this process (LATENCY_QOS_TIER_0 and THROUGHPUT_QOS_TIER_0 are the
/// first, what a foreground app's interactive work gets; 0 and 0 are "unspecified", the state a process starts with). The two
/// answers are returned for the log (0 = taken).
void madeira_sched_task_qos(unsigned latency, unsigned throughput, int *kr_latency, int *kr_throughput) {
    struct task_qos_policy q;
    q.task_latency_qos_tier = (integer_t)latency;
    q.task_throughput_qos_tier = (integer_t)throughput;
    *kr_latency = (int)task_policy_set(mach_task_self(), TASK_BASE_LATENCY_QOS_POLICY, (task_policy_t)&q, TASK_QOS_POLICY_COUNT);
    *kr_throughput = (int)task_policy_set(mach_task_self(), TASK_BASE_THROUGHPUT_QOS_POLICY, (task_policy_t)&q, TASK_QOS_POLICY_COUNT);
}

/// The first tiers, for Swift (the SDK's enum constants are not imported as numbers).
unsigned madeira_sched_first_latency_tier(void) { return (unsigned)LATENCY_QOS_TIER_0; }
unsigned madeira_sched_first_throughput_tier(void) { return (unsigned)THROUGHPUT_QOS_TIER_0; }

/// What the process has used so far: milliseconds on the fast (performance) cores, their cycles, and the time now in milliseconds.
/// 0 = read.
int madeira_cpu_clock_sample(double *fast_ms, double *fast_cycles, double *wall_ms) {
    static mach_timebase_info_data_t tb;
    struct rusage_info_v6 ru;
    memset(&ru, 0, sizeof ru);
    if (proc_pid_rusage(getpid(), 6 /* RUSAGE_INFO_V6 */, &ru) != 0) return 1;
    if (!tb.denom) mach_timebase_info(&tb);
    *fast_ms = (double)(ru.ri_user_ptime + ru.ri_system_ptime) * tb.numer / tb.denom / 1e6;
    *fast_cycles = (double)ru.ri_pcycles;
    *wall_ms = (double)mach_absolute_time() * tb.numer / tb.denom / 1e6;
    return 0;
}
