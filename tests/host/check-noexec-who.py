#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Diablo II: Resurrected's two open questions, asked by the log itself (round 624, agent N S9; Send Logs 2T02WX = D4HYCZ).

The main thread 0xb8 ran into FEX's NoExec block at 0x7042580000 1405 times: a 1 MB section view the loader had unmapped itself,
whose range then held thread 0xd8's stack; the guest's own handler got each fault (delivered, not UNHANDLED), so neither the
[entry-fail] report nor anything else named that memory. Thread 0xd8 faulted at its first run with x18 = 0. Log only, capped:
  - build/ntdll-unix/signal_arm64_ios.c ios_noexec_note: in the Mach delivery path, for a null read at FEX's NoExec block
    (`mov w1,#0; ldr x1,[x1]`), the first three deliveries per (thread, guest RIP), 16 keys: [entry-fail] and [noexec-who];
  - build/ntdll-unix/virtual_ios.c ios_noexec_who: the newest DELETED and CREATED view-history records covering the RIP (the
    1 MB ring, then the small one) and what is there now: whose thread stack, a view, or nothing; virtual_mutex only tried;
  - build/ntdll-unix/thread_ios.c: NtCreateThreadEx records creator, start, parameter and stack of each thread (ring of 256);
    [x18-decline] prints the faulting thread's record once ([thread-origin]).

Checks: the three pieces cut from the sources and compiled on the host with fake memory, rings, TEB list and lock, driven case by
case; the wiring (the note right after the access violation record is filled, only for a null fault address; the record in
NtCreateThreadEx after start and parameter are set; the report in both [x18-decline] sites; no blocking lock in [noexec-who]).
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
sig = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
vir = (root / "build/ntdll-unix/virtual_ios.c").read_text()
thr = (root / "build/ntdll-unix/thread_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def fn(text, start):
    a = text.index(start)
    return text[a:text.index("\n}\n", a) + 3]


# ---------------------------------------------------------------- wiring
deliver = sig[sig.index("static int ios_mach_deliver_guest_exception_inner( thread_t thread"):]
deliver = deliver[:deliver.index("\n}\n")]
check("the note runs in the delivery path right after the access violation record is filled, only for a null fault address",
      sig.count("ios_noexec_note( state, thread_teb );") == 1
      and "        rec.ExceptionInformation[1] = fault_addr;\n        if (!fault_addr) ios_noexec_note( state, thread_teb );" in deliver)
calls = [m.start() for m in re.finditer(r"ios_thread_origin_report\( \(unsigned int\)", sig)]
declines = [m.start() for m in re.finditer(r"\[x18-decline\] #%d insns pc-64\.\.pc\+16", sig)]
check("both [x18-decline] reports print the thread's creation record",
      len(calls) == 2 and len(declines) == 2 and all(0 < c - d < 900 for c, d in zip(calls, declines)), (calls, declines))
create = thr[thr.index("NTSTATUS WINAPI NtCreateThreadEx( HANDLE *handle"):]
create = create[:create.index("\n}\n")]
check("NtCreateThreadEx records each thread after its start routine and parameter are set, before pthread_create",
      "    thread_data->param = param;\n    ios_thread_origin_note( tid, start, param, teb );" in create
      and create.index("ios_thread_origin_note(") < create.index("pthread_create( &pthread_id"))
who_src = fn(vir, "void ios_noexec_who( uint64_t addr, uint64_t guest_rip, unsigned int tid )")
check("[noexec-who] only tries the memory lock (it runs on the Mach exception thread)",
      "pthread_mutex_trylock( &virtual_mutex )" in who_src and "mutex_lock(" not in who_src.replace("pthread_mutex_trylock", "")
      .replace("pthread_mutex_unlock", ""))

cc = shutil.which("cc")
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)

    def build_run(name, code, args_list):
        (t / f"{name}.c").write_text(code)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-pthread",
                            "-o", str(t / name), str(t / f"{name}.c")], capture_output=True, text=True)
        if b.returncode:
            return None, b.stderr[-2000:]
        return [subprocess.run([str(t / name)] + list(a), capture_output=True, text=True).stdout for a in args_list], ""

    # ------------------------------------------------------------ ios_noexec_note
    note = fn(sig, "static void ios_noexec_note( const arm_thread_state64_t *st, uintptr_t teb )")
    harness_note = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uintptr_t ULONG_PTR;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define EXC_BAD_ACCESS 1
typedef struct { uint64_t __x[29]; uint64_t __fp, __lr, __sp, __pc; uint32_t __cpsr, __pad; } arm_thread_state64_t;
#define __darwin_arm_thread_state64_get_pc(s) ((s).__pc)
typedef struct { void *Peb; struct { void *UniqueProcess, *UniqueThread; } ClientId; } TEB;
#define JIT_LO 0x150000000ull
#define JIT_HI 0x158000000ull
static int ios_ajan2_pc_is_jit( uint64_t pc ) { return pc >= JIT_LO && pc < JIT_HI; }
/* fake memory: the thunk words, a FEX frame, two TEBs */
static uint32_t thunk[2] = { 0x52800001, 0xf9400021 };
static uint64_t thunk_pc = 0x150001004ull;
static uint64_t frame_addr = 0x7000100000ull, frame_rip;
static TEB teb_a = { (void *)0x10fbd4000, { 0, (void *)0xb8 } }, teb_b = { (void *)0x2000, { 0, (void *)0xd8 } };
static int ios_ajan2_read( uint64_t addr, void *out, size_t n )
{
  if (addr == thunk_pc - 4 && n == 4) { memcpy( out, &thunk[0], 4 ); return 1; }
  if (addr == thunk_pc && n == 4) { memcpy( out, &thunk[1], 4 ); return 1; }
  if (addr == frame_addr + 0x18 && n == 8) { memcpy( out, &frame_rip, 8 ); return 1; }
  if (addr >= (uintptr_t)&teb_a && addr + n <= (uintptr_t)&teb_a + sizeof(teb_a)) { memcpy( out, (void *)(uintptr_t)addr, n ); return 1; }
  if (addr >= (uintptr_t)&teb_b && addr + n <= (uintptr_t)&teb_b + sizeof(teb_b)) { memcpy( out, (void *)(uintptr_t)addr, n ); return 1; }
  return 0;
}
static uint64_t ios_wow_base_for_peb( void *peb ) { return (uintptr_t)peb == 0x2000 ? 0x7f0000000000ull : 0; }
static void ios_ajan2_fault_diag( const arm_thread_state64_t *st, uintptr_t teb, int exc, uintptr_t fa )
{ printf( "entry-fail exc=%d fa=%lx\n", exc, (unsigned long)fa ); }
void ios_noexec_who( uint64_t addr, uint64_t guest_rip, unsigned int tid )
{ printf( "who addr=%llx rip=%llx tid=%04x\n", (unsigned long long)addr, (unsigned long long)guest_rip, tid ); }
@NOTE@
static void deliver( uintptr_t teb, uint64_t rip, uint64_t pc, uint64_t frame )
{
  arm_thread_state64_t st; memset( &st, 0, sizeof st );
  st.__pc = pc; st.__x[28] = frame; frame_rip = rip;
  ios_noexec_note( &st, teb );
}
int main( void )
{
  uintptr_t a = (uintptr_t)&teb_a, b = (uintptr_t)&teb_b;
  for (int i = 0; i < 5; i++) deliver( a, 0x7042580000ull, thunk_pc, frame_addr );   /* the same RIP 5 times: 3 reports */
  printf( "--- other rip\n" );
  deliver( a, 0x12688f3d0ull, thunk_pc, frame_addr );
  printf( "--- 32-bit guest\n" );
  deliver( b, 0x00401000ull, thunk_pc, frame_addr );
  printf( "--- not the thunk\n" );
  thunk[1] = 0xf9400022; deliver( a, 0x7042590000ull, thunk_pc, frame_addr ); thunk[1] = 0xf9400021;
  printf( "--- not JIT\n" );
  deliver( a, 0x7042590000ull, 0x100000000ull, frame_addr );
  printf( "--- no frame\n" );
  deliver( a, 0x7042590000ull, thunk_pc, 0x10 );
  printf( "--- keys\n" );
  for (uint64_t r = 0; r < 20; r++) deliver( a, 0x7100000000ull + r * 0x1000, thunk_pc, frame_addr );
  return 0;
}
'''
    outs, err = build_run("note", harness_note.replace("@NOTE@", note), [[]])
    check("ios_noexec_note compiles on the host (fake thread state, memory and TEBs)", outs is not None, err)
    if outs:
        out = outs[0]
        first = out[:out.index("--- other rip")]
        check("the same thread and RIP: [entry-fail] and [noexec-who] for the first three deliveries only",
              first.count("entry-fail exc=1 fa=0") == 3 and first.count("who addr=7042580000 rip=7042580000 tid=00b8") == 3, out)
        seg = out[out.index("--- other rip"):out.index("--- 32-bit guest")]
        check("another RIP of the thread is a new key", "who addr=12688f3d0 rip=12688f3d0 tid=00b8" in seg, out)
        seg = out[out.index("--- 32-bit guest"):out.index("--- not the thunk")]
        check("a 32-bit guest RIP is looked up in its process's window", "who addr=7f0000401000 rip=401000 tid=00d8" in seg, out)
        seg = out[out.index("--- not the thunk"):out.index("--- keys")]
        check("not FEX's NoExec block, not JIT code, or no FEX frame: nothing", "who" not in seg and "entry-fail" not in seg, out)
        seg = out[out.index("--- keys"):]
        check("16 (thread, RIP) keys at most: the rest print nothing", seg.count("who addr=") == 16 - 3, seg)

    # ------------------------------------------------------------ ios_noexec_who
    a = vir.index("struct ios_vh_site\n{")
    rings = vir[a:vir.index("static __thread const char *ios_vh_why;", a)]
    small = vir[vir.index("struct ios_vhs_rec\n{"):vir.index("static void ios_vhs_note( char kind")]
    ms = fn(vir, "static unsigned int ios_vh_ms( void )")
    harness_who = r'''
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
typedef uintptr_t ULONG_PTR;
#define HandleToULong(h) ((unsigned long)(uintptr_t)(h))
#define CONTAINING_RECORD(address, type, field) ((type *)((char *)(address) - offsetof(type, field)))
struct list { struct list *next, *prev; };
#define LIST_FOR_EACH_ENTRY(elem, list, type, field) \
  for ((elem) = CONTAINING_RECORD((list)->next, type, field); &(elem)->field != (list); \
       (elem) = CONTAINING_RECORD((elem)->field.next, type, field))
struct ntdll_thread_data { int pad; struct list entry; };
typedef struct { void *Peb; struct { void *UniqueProcess, *UniqueThread; } ClientId; struct { void *StackBase; } Tib;
                 void *DeallocationStack; struct ntdll_thread_data GdiTebBatch; } TEB;
static struct list teb_list = { &teb_list, &teb_list };
struct file_view { void *base; size_t size; unsigned int protect; };
static struct file_view views[4]; static int nviews;
static struct file_view *find_view( const void *addr, size_t size )
{ for (int i = 0; i < nviews; i++) if ((const char *)addr >= (char *)views[i].base && (const char *)addr < (char *)views[i].base + views[i].size) return &views[i]; return NULL; }
static const char *ios_fault_view_kind( const struct file_view *v ) { return "VirtualAlloc"; }
static pthread_mutex_t virtual_mutex = PTHREAD_MUTEX_INITIALIZER;
@RINGS@
@SMALL@
@MS@
@WHO@
static void rec( char kind, uintptr_t base, size_t size, unsigned tid, const char *why )
{
  struct ios_vh_rec *r = &ios_vh_ring[ios_vh_pos++ % IOS_VH_N];
  memset( r, 0, sizeof *r ); r->base = (char *)base; r->size = size; r->protect = 0x40; r->kind = kind; r->tid = tid;
  r->ms = ios_vh_ms(); r->why = why;
}
static void srec( char kind, uintptr_t base, size_t size, unsigned tid )
{
  struct ios_vhs_rec *r = &ios_vhs_ring[ios_vhs_pos++ % IOS_VHS_N];
  r->base = (char *)base; r->size = size; r->protect = 0x4; r->kind = kind; r->tid = tid; r->ms = ios_vh_ms();
}
static TEB thread_d8;
static void *hold( void *arg ) { pthread_mutex_lock( &virtual_mutex ); *(volatile int *)arg = 1; while (*(volatile int *)arg == 1) ; pthread_mutex_unlock( &virtual_mutex ); return NULL; }
int main( void )
{
  /* 2T02WX: view B created and deleted by 0xb8, the range then thread 0xd8's stack */
  rec( 'c', 0x7042580000ull, 0x100000, 0xb8, NULL );
  rec( 'd', 0x7042580000ull, 0x100000, 0xb8, NULL );
  rec( 'c', 0x7042400000ull, 0x200000, 0xb8, NULL );   /* the stack's own view, created later */
  views[nviews++] = (struct file_view){ (void *)0x7042400000ull, 0x200000, 0x4 };
  thread_d8.ClientId.UniqueThread = (void *)0xd8; thread_d8.DeallocationStack = (void *)0x7042400000ull;
  thread_d8.Tib.StackBase = (void *)0x7042600000ull;
  thread_d8.GdiTebBatch.entry.next = &teb_list; thread_d8.GdiTebBatch.entry.prev = &teb_list;
  teb_list.next = teb_list.prev = &thread_d8.GdiTebBatch.entry;
  ios_noexec_who( 0x7042580000ull, 0x7042580000ull, 0xb8 );
  printf( "---\n" ); fflush( stdout );
  /* a small view in the second ring, nothing there now */
  srec( 'c', 0x7100010000ull, 0x11000, 0xc0 ); srec( 'd', 0x7100010000ull, 0x11000, 0xc4 );
  ios_noexec_who( 0x7100012000ull, 0x7100012000ull, 0xc0 );
  printf( "---\n" ); fflush( stdout );
  ios_noexec_who( 0x7300000000ull, 0x7300000000ull, 0xc0 );
  printf( "---\n" ); fflush( stdout );
  { pthread_t h; volatile int flag = 0; pthread_create( &h, NULL, hold, (void *)&flag ); while (!flag) ;
    ios_noexec_who( 0x7042580000ull, 0x7042580000ull, 0xb8 ); flag = 2; pthread_join( h, NULL ); }
  printf( "---\n" ); fflush( stdout );
  ios_noexec_who( 0, 0x401000, 0xd8 );
  return 0;
}
'''
    code = (harness_who.replace("@RINGS@", rings).replace("@SMALL@", small).replace("@MS@", ms)
            .replace("@WHO@", who_src.replace("dprintf( 2, ", "printf( ")))
    outs, err = build_run("who", code, [[]])
    check("ios_noexec_who compiles on the host with the real rings (fake views, TEB list and lock)", outs is not None, err)
    if outs:
        parts = [p.strip() for p in outs[0].split("---")]
        check("2T02WX's shape: the 1 MB view unmapped by 0xb8, the newest map over it the stack's own view, now thread 00d8's stack",
              re.fullmatch(r"\[noexec-who\] rev=624 rip=0x7042580000 tid=00b8 last-unmap \{view 0x7042580000\+0x100000 protect=0x40 by "
                           r"tid 00b8 \d+ ms ago\} last-map \{view 0x7042400000\+0x200000 protect=0x40 by tid 00b8 \d+ ms ago\} now: the "
                           r"stack of thread 00d8", parts[0]) is not None, parts[0])
        check("a small view from the second ring; nothing there now",
              re.fullmatch(r"\[noexec-who\] rev=624 rip=0x7100012000 tid=00c0 last-unmap \{small view 0x7100010000\+0x11000 protect=0x4 by "
                           r"tid 00c4 \d+ ms ago\} last-map \{small view 0x7100010000\+0x11000 protect=0x4 by tid 00c0 \d+ ms ago\} now: "
                           r"free \(no Wine view\)", parts[1]) is not None, parts[1])
        check("no history: said so", parts[2] == "[noexec-who] rev=624 rip=0x7300000000 tid=00c0 last-unmap {none in the history} "
              "last-map {none in the history} now: free (no Wine view)", parts[2])
        check("the memory lock held by another thread: the history still printed, the present not read, nothing blocks",
              parts[3].startswith("[noexec-who] rev=624 rip=0x7042580000 tid=00b8 last-unmap {view 0x7042580000+0x100000")
              and parts[3].endswith("now: not read (the memory lock is held)"), parts[3])
        check("a 32-bit RIP whose window is not known: one line saying so",
              parts[4] == "[noexec-who] rev=624 rip=0x401000 tid=00d8: a 32-bit guest whose window is not known", parts[4])

    # ------------------------------------------------------------ the thread origin ring
    a = thr.index("struct ios_thread_origin\n{")
    origin = thr[a:thr.index("/***********************************************************************\n *              NtCreateThreadEx")]
    harness_origin = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#define HandleToULong(h) ((unsigned long)(uintptr_t)(h))
typedef unsigned long (*PRTL_THREAD_START_ROUTINE)( void * );
typedef struct { struct { void *UniqueProcess, *UniqueThread; } ClientId; struct { void *StackBase; } Tib; void *DeallocationStack; } TEB;
static TEB creator = { { 0, (void *)0xb8 } };
static TEB *NtCurrentTeb( void ) { return &creator; }
@ORIGIN@
int main( void )
{
  TEB t = { { 0, 0 }, { (void *)0x7042600000ull }, (void *)0x7042400000ull };
  ios_thread_origin_note( 0xd4, (PRTL_THREAD_START_ROUTINE)0x71f3000000ull, (void *)0x1, &t );
  ios_thread_origin_note( 0xd8, (PRTL_THREAD_START_ROUTINE)0x71f30763f0ull, (void *)0x12345, &t );
  ios_thread_origin_report( 0xd8 ); fflush( stdout );
  ios_thread_origin_report( 0xd8 );   /* once per thread */
  ios_thread_origin_report( 0x0 );
  for (int i = 0; i < 6; i++) ios_thread_origin_report( 0x400 + i );   /* unknown: at most four lines */
  for (unsigned i = 0; i < 300; i++) ios_thread_origin_note( 0x1000 + i, 0, 0, &t );   /* the ring wraps */
  ios_thread_origin_report( 0xd4 );
  ios_thread_origin_report( 0x1000 + 299 );
  return 0;
}
'''
    code = harness_origin.replace("@ORIGIN@", origin).replace("dprintf( 2, ", "printf( ")
    outs, err = build_run("origin", code, [[]])
    check("the thread origin ring compiles on the host", outs is not None, err)
    if outs:
        lines = outs[0].splitlines()
        check("a thread's record: creator, start, parameter, stack, once",
              re.fullmatch(r"\[thread-origin\] rev=624 tid=00d8 created by tid 00b8 \d+ ms ago: start=0x71f30763f0 param=0x12345 "
                           r"stack 0x7042400000-0x7042600000 \(its fault was reported with x18 = 0\)", lines[0]) is not None
              and sum("tid=00d8" in l for l in lines) == 1, outs[0])
        unknown = [l for l in lines if "no creation record" in l]
        check("an unknown thread: at most four lines, a zero id none", len(unknown) == 4 and all("tid=04" in l for l in unknown), outs[0])
        check("after 300 more threads the oldest record is gone (one more 'no record' would be over the cap), the newest is there",
              "tid=00d4 created" not in outs[0] and re.search(r"tid=112b created by tid 00b8", outs[0]) is not None, outs[0])

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-noexec-who.py" in wf)
print("PASS: noexec-who and thread-origin" if ok else "FAIL: noexec-who and thread-origin")
raise SystemExit(0 if ok else 1)
