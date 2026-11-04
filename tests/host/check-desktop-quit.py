#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Exercise the actual bridge shutdown admission/join code with real pthreads."""
from pathlib import Path
import subprocess, tempfile
root = Path(__file__).resolve().parents[2]
bridge = (root / "app/Madeira/WineServerBridge.m").read_text()
globals = bridge[bridge.index("int g_wineserver_desktop_shutdown = 0;"):bridge.index("static char *g_prefix_path")]
functions = bridge[bridge.index("int wineserver_request_desktop_shutdown(void)"):]
fixture = r"""
#include <pthread.h>
#include <stdatomic.h>
#include <assert.h>
#include <unistd.h>
#include <stdio.h>
int g_wineserver_running = 1, g_wineserver_should_stop;
#define wine_log_msg(...) ((void)0)
static _Atomic int joins, consumed, drained;
static int checked_join(pthread_t t, void **r) { atomic_fetch_add(&joins, 1); return pthread_join(t,r); }
#define pthread_join checked_join
""" + globals + functions + r"""
#undef pthread_join
static void *server(void *unused) {
    while (!__atomic_load_n(&g_wineserver_desktop_shutdown, __ATOMIC_ACQUIRE)) usleep(100);
    int queued = 1;
    assert(__atomic_compare_exchange_n(&g_wineserver_desktop_shutdown, &queued, 2, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE));
    atomic_store(&consumed, 1);
    usleep(30000); // normal shutdown timers must be allowed to drain
    assert(!g_wineserver_should_stop);
    atomic_store(&drained, 1);
    pthread_exit(NULL); // mirrors request_ios.c's shutdown timer, bypassing wrapper tail
}
static void *stop(void *unused) { wineserver_stop(); assert(atomic_load(&drained)); return NULL; }
int main(void) {
    assert(!pthread_create(&g_wineserver_thread, NULL, server, NULL));
    assert(wineserver_request_desktop_shutdown());
    assert(!wineserver_request_desktop_shutdown());
    pthread_t a, b; pthread_create(&a,NULL,stop,NULL); pthread_create(&b,NULL,stop,NULL);
    pthread_join(a,NULL); pthread_join(b,NULL);
    assert(atomic_load(&joins)==1 && atomic_load(&consumed)==1);
    assert(!g_wineserver_running && !g_wineserver_thread && g_wineserver_join_state==2);
    assert(!wineserver_request_desktop_shutdown());
    // An ordinary stop already committed must reject a late desktop request.
    g_wineserver_running=1; g_wineserver_join_state=0; g_wineserver_should_stop=1;
    __atomic_store_n(&g_wineserver_desktop_shutdown,0,__ATOMIC_RELEASE);
    assert(!wineserver_request_desktop_shutdown());
    puts("PASS: desktop shutdown drains, joins once, rejects duplicate/late requests");
}
"""
with tempfile.TemporaryDirectory() as tmp:
    source=Path(tmp)/"quit.c"; exe=Path(tmp)/"quit"; source.write_text(fixture)
    subprocess.run(["clang","-std=c11","-pthread",str(source),"-o",str(exe)],check=True)
    subprocess.run([str(exe)],check=True,timeout=10)

# Exercise the production native-death acknowledgement with a deterministic Mach fixture.
process = (root / "app/Madeira/WineProcessBridge.m").read_text()
ack = process[process.index("static int desktop_thread_is_gone("):process.index("/* The library's call", process.index("static int desktop_thread_is_gone("))]
pre = r"""
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <assert.h>
#include <unistd.h>
typedef unsigned thread_t;
typedef unsigned mach_port_type_t;
typedef int kern_return_t;
typedef int thread_basic_info_data_t;
typedef unsigned mach_msg_type_number_t;
typedef int *thread_info_t;
#define KERN_SUCCESS 0
#define KERN_INVALID_ARGUMENT 4
#define MACH_SEND_INVALID_DEST 20
#define KERN_TERMINATED 37
#define MACH_PORT_TYPE_DEAD_NAME 16
#define MACH_PORT_RIGHT_SEND 0
#define THREAD_BASIC_INFO_COUNT 1
#define THREAD_BASIC_INFO 3
static int scenario, ticks, retains, releases, finalized, queued;
static int g_wine_running=1, g_session_end_claim, g_wine_exit_code;
static int mach_task_self(void) { return 1; }
static int ios_thread_registry_count(void) { return 1; }
// Slot zero is replaced while shutdown runs, with NO count increase.
static thread_t ios_thread_registry_mach(int slot) { return queued ? 2 : 1; }
static int dead(thread_t port) { return queued && (port==1 || (scenario==1 && ticks>=2) || (scenario==4 && ticks>=799)); }
static kern_return_t mach_port_type(int task, thread_t port, mach_port_type_t *type) { *type=dead(port)?MACH_PORT_TYPE_DEAD_NAME:0; return KERN_SUCCESS; }
static kern_return_t thread_info(thread_t port, int flavor, thread_info_t info, mach_msg_type_number_t *size) {
    if (dead(port)) return KERN_INVALID_ARGUMENT;
    return scenario==2 ? 99 : KERN_SUCCESS; // unknown must not mean dead
}
static int mach_port_mod_refs(int task, thread_t port, int kind, int delta) { retains++; return 0; }
static int mach_port_deallocate(int task, thread_t port) { releases++; return 0; }
static int wineserver_request_desktop_shutdown(void) { if(scenario==3)return 0; queued=1;return 1; }
static void wineserver_stop(void) {}
static void wine_session_end_work(void) { finalized++; g_wine_running=0; }
static int fake_usleep(unsigned usec) { ticks++; return 0; }
#define usleep fake_usleep
"""
post = r"""
int main(void) {
 for (scenario=1;scenario<=4;scenario++) {
  ticks=retains=releases=finalized=queued=g_session_end_claim=0;g_wine_running=1;
  int result=wine_session_quit_desktop();
  if(scenario==1) { assert(result==1 && finalized==1 && !g_wine_running); assert(retains==2); }
  else assert(result==-1 && !finalized && g_wine_running);
  assert(retains==releases);
 }
 puts("PASS: native ack catches reused slots, refuses unknown/refused/unstable completion, releases rights");
}
"""
with tempfile.TemporaryDirectory() as tmp:
    source=Path(tmp)/"ack.c"; exe=Path(tmp)/"ack"; source.write_text(pre+ack+post)
    subprocess.run(["clang","-std=c11",str(source),"-o",str(exe)],check=True)
    subprocess.run([str(exe)],check=True,timeout=10)
