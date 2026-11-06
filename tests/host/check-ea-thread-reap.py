#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""EA 608: a recycled WoW TEB must never free the current thread's stack.

Run the production handoff against real pthread joins, unmapped old TEBs,
same-address replacements before/during join, and join failure. The join must
run without the VM lock; freeing must run with it, after identity validation.
The original non-EA exit path is also executed for absent/invalid switches.
No Windows or iOS application runs.

madeira-bcd (ajan62, X9; upstream Madeira ml2290): the raw hand-off of every other session no longer joins or frees the TEB of
the thread that is exiting (the 608 failure outside EA: GTA V Legacy's launcher setup, vcredist chains), and
env.MADEIRA_THREAD_REAP_CHECKED = 1 (default off) takes the checked hand-off for every process. The build-631 branch is kept below as
the negative control.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
virtual = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
thread = (root / 'build/ntdll-unix/thread_ios.c').read_text()
block = virtual.split('/* ios-ea-thread-reap begin */', 1)[1].split('/* ios-ea-thread-reap end */', 1)[0]
exit_body = thread.split('static DECLSPEC_NORETURN void exit_thread( int status )', 1)[1].split('\n}\n', 1)[0]
dispatch = exit_body[exit_body.index('    const char *ea_cef'):exit_body.index('    pthread_exit_wrapper( status );')]
legacy = dispatch[dispatch.index('else if ((teb ='):].replace('else if', 'if', 1)
# the raw branch exactly as build 631 shipped it (before the self-TEB guard): the negative control of the 608 failure
legacy_631 = """if ((teb = InterlockedExchangePointer( &prev_teb, NtCurrentTeb() )))
    {
        struct ntdll_thread_data *thread_data = (struct ntdll_thread_data *)&teb->GdiTebBatch;

        if (thread_data->pthread_id)
        {
            pthread_join( thread_data->pthread_id, NULL );
            virtual_free_teb( teb );
        }
    }
"""
assert 'if (teb == NtCurrentTeb())' in legacy and 'if (teb == NtCurrentTeb())' not in legacy_631
assert 'getenv( "MADEIRA_THREAD_REAP_CHECKED" )' in dispatch and '(checked && !strcmp( checked, "1" ))' in dispatch
assert thread.count('ios_ea_reap_exited_thread( NtCurrentTeb() );') == 1
assert virtual.count('PTHREAD_MUTEX_RECURSIVE') >= 1

harness = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
typedef uintptr_t ULONG_PTR;
typedef struct { void *UniqueProcess, *UniqueThread; } CLIENT_ID;
struct list { struct list *next, *prev; };
struct ntdll_thread_data { struct list entry; pthread_t pthread_id; };
typedef struct { CLIENT_ID ClientId; struct ntdll_thread_data GdiTebBatch; } TEB;
#define CONTAINING_RECORD(p,t,f) ((t *)((char *)(p) - offsetof(t,f)))
#define LIST_FOR_EACH_ENTRY(p,h,t,f) \
    for (struct list *node=(h)->next; node!=(h) && ((p)=CONTAINING_RECORD(node,t,f),1); node=node->next)
static struct list teb_list;
static pthread_mutex_t virtual_mutex;
static _Thread_local int vm_depth;
static atomic_uint joins;
static unsigned frees, legacy_exchanges;
static int force_join_error, replace_during_join;
static TEB *last_freed, *replace_teb;
static _Thread_local TEB *current_teb;
static void add(TEB *t) {
    struct list *e=&t->GdiTebBatch.entry;
    e->next=&teb_list; e->prev=teb_list.prev; e->prev->next=e; teb_list.prev=e;
}
static void remove_teb(TEB *t) {
    struct list *e=&t->GdiTebBatch.entry;
    e->prev->next=e->next; e->next->prev=e->prev;
}
static void server_enter_uninterrupted_section(pthread_mutex_t *m, sigset_t *s) {
    (void)s; assert(!pthread_mutex_lock(m)); vm_depth++;
}
static void server_leave_uninterrupted_section(pthread_mutex_t *m, sigset_t *s) {
    (void)s; assert(vm_depth); vm_depth--; assert(!pthread_mutex_unlock(m));
}
static void virtual_free_teb(TEB *t) {
    assert(vm_depth); assert(t != current_teb);
    frees++; last_freed=t; remove_teb(t);
    /* The real free recursively enters this same mutex. */
    sigset_t s; server_enter_uninterrupted_section(&virtual_mutex,&s);
    server_leave_uninterrupted_section(&virtual_mutex,&s);
}
static int test_join(pthread_t id, void **value) {
    assert(!vm_depth); assert(id != pthread_self()); joins++;
    if (force_join_error) return force_join_error;
    int r=pthread_join(id,value);
    if (!r && replace_during_join) {
        sigset_t s; server_enter_uninterrupted_section(&virtual_mutex,&s);
        remove_teb(replace_teb);
        replace_teb->ClientId.UniqueThread=(void *)0x9999;
        add(replace_teb);
        server_leave_uninterrupted_section(&virtual_mutex,&s);
    }
    return r;
}
#define pthread_join test_join
''' + block + r'''
#undef pthread_join
static TEB *NtCurrentTeb(void) { return current_teb; }
static void *InterlockedExchangePointer(void **slot, void *v) {
    void *old=*slot; *slot=v; legacy_exchanges++; return old;
}
/* Run exactly the production EA/legacy branch, including the legacy join. */
static void dispatch_exit(void) {
    static void *prev_teb;
    TEB *teb;
''' + dispatch + r'''
}
static unsigned old_live_releases, old_failed_joins;
static int old_join(pthread_t p, void **v) {
    (void)v; assert(pthread_equal(p,pthread_self())); old_failed_joins++; return EDEADLK;
}
static void old_free(TEB *t) { assert(t==current_teb); old_live_releases++; }
#define pthread_join old_join
#define virtual_free_teb old_free
static void reproduce_608(void) {
    void *prev_teb=current_teb; TEB *teb;
''' + legacy_631 + r'''
}
/* the production raw branch on the same situation: the exiting thread's own TEB is the previous one */
static void self_case_production(void) {
    void *prev_teb=current_teb; TEB *teb;
''' + legacy + r'''
}
#undef pthread_join
#undef virtual_free_teb
static void *worker(void *arg) { return arg; }
static pthread_t new_worker(void) {
    pthread_t p; assert(!pthread_create(&p,NULL,worker,NULL)); return p;
}
static void init(TEB *t, unsigned pid, unsigned tid, pthread_t p) {
    memset(t,0,sizeof(*t)); t->ClientId.UniqueProcess=(void *)(uintptr_t)pid;
    t->ClientId.UniqueThread=(void *)(uintptr_t)tid; t->GdiTebBatch.pthread_id=p; add(t);
}
static void reset(void) {
    memset(&ios_ea_pending_thread,0,sizeof(ios_ea_pending_thread));
    teb_list.next=teb_list.prev=&teb_list; joins=frees=0; last_freed=NULL;
    force_join_error=replace_during_join=0;
}
/* Recorded identity of the preceding exited thread, before a window changes. */
static void seed(TEB *t) {
    ios_ea_pending_thread.teb=t; ios_ea_pending_thread.client=t->ClientId;
    ios_ea_pending_thread.pthread_id=t->GdiTebBatch.pthread_id;
}
static atomic_uint concurrent_done;
static void *concurrent_exit(void *arg) {
    TEB *t=arg; current_teb=t;
    sigset_t s; server_enter_uninterrupted_section(&virtual_mutex,&s);
    t->GdiTebBatch.pthread_id=pthread_self();
    server_leave_uninterrupted_section(&virtual_mutex,&s);
    ios_ea_reap_exited_thread(t);
    atomic_fetch_add(&concurrent_done,1); return NULL;
}
int main(void) {
    pthread_mutexattr_t attr; pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr,PTHREAD_MUTEX_RECURSIVE);
    assert(!pthread_mutex_init(&virtual_mutex,&attr)); pthread_mutexattr_destroy(&attr);
    TEB old, live, other;
    memset(&live,0,sizeof(live)); live.GdiTebBatch.pthread_id=pthread_self(); current_teb=&live;
    reproduce_608(); assert(old_failed_joins==1 && old_live_releases==1);
    puts("PASS negative control: the build-631 branch releases the live stack despite self-join EDEADLK");
    self_case_production(); assert(old_failed_joins==1 && old_live_releases==1);
    puts("PASS the production raw branch on the same situation joins nothing and frees nothing (the 608 failure outside EA)");
    reset(); init(&old,0x31c,0x324,new_worker()); init(&live,0x330,0x338,pthread_self());
    current_teb=&live; seed(&old); ios_ea_reap_exited_thread(&live);
    assert(joins==1 && frees==1 && last_freed==&old);
    puts("PASS real exited predecessor joined outside VM lock and freed under recursive lock");

    reset(); init(&live,0x31c,0x324,pthread_self()); current_teb=&live;
    ios_ea_reap_exited_thread(&live);
    live.ClientId.UniqueProcess=(void *)0x330; live.ClientId.UniqueThread=(void *)0x338;
    ios_ea_reap_exited_thread(&live);
    assert(!joins && !frees);
    puts("PASS 608 same-address current TEB reuse: no self-join and no live stack release");

    long pagesize=sysconf(_SC_PAGESIZE);
    TEB *gone=mmap(NULL,pagesize,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(gone!=MAP_FAILED); reset();
    init(gone,10,11,(pthread_t)(uintptr_t)0x1234); init(&live,12,13,pthread_self()); current_teb=&live;
    seed(gone); remove_teb(gone); assert(!mprotect(gone,pagesize,PROT_NONE));
    ios_ea_reap_exited_thread(&live); assert(!joins && !frees); munmap(gone,pagesize);
    puts("PASS unmapped old window: no stale TEB read or native join");

    gone=mmap(NULL,pagesize,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0); assert(gone!=MAP_FAILED);
    struct ios_ea_retired_thread kept=ios_ea_pending_thread;
    ios_ea_reap_exited_thread(gone);
    assert(!memcmp(&kept,&ios_ea_pending_thread,sizeof(kept))); munmap(gone,pagesize);
    init(&other,99,100,(pthread_t)(uintptr_t)0x1234);
    ios_ea_reap_exited_thread(&other);
    assert(!memcmp(&kept,&ios_ea_pending_thread,sizeof(kept)));
    puts("PASS laggard caller with unmapped or reassigned TEB cannot read it or alter pending handoff");

    for (int field=0;field<3;field++) {
        reset(); init(&old,10,11,(pthread_t)(uintptr_t)0x1234); init(&live,12,13,pthread_self()); current_teb=&live;
        seed(&old);
        if (field==0) old.ClientId.UniqueProcess=(void *)99;
        if (field==1) old.ClientId.UniqueThread=(void *)99;
        if (field==2) old.GdiTebBatch.pthread_id=(pthread_t)(uintptr_t)0x5678;
        ios_ea_reap_exited_thread(&live); assert(!joins && !frees);
    }
    puts("PASS reused other TEB: process, thread and native identity mismatches each refuse cleanup");

    reset(); pthread_t p=new_worker(); init(&old,10,11,p); init(&live,12,13,pthread_self()); current_teb=&live;
    seed(&old); force_join_error=ESRCH; ios_ea_reap_exited_thread(&live);
    assert(joins==1 && !frees); assert(!pthread_join(p,NULL));
    puts("PASS failed join cannot release any TEB or stack");

    reset(); init(&old,10,11,new_worker()); init(&live,12,13,pthread_self()); current_teb=&live;
    seed(&old); replace_teb=&old; replace_during_join=1;
    ios_ea_reap_exited_thread(&live); assert(joins==1 && !frees);
    puts("PASS window replacement during join: post-join identity check protects replacement");

    reset(); init(&old,10,11,new_worker()); init(&live,14,15,pthread_self()); current_teb=&live;
    seed(&old); ios_ea_reap_exited_thread(&live);
    init(&other,12,13,new_worker()); seed(&other); ios_ea_reap_exited_thread(&live);
    assert(joins==2 && frees==2 && last_freed==&other);
    puts("PASS successive ordinary exits still reap both predecessors");

    reset(); enum { N=16 }; TEB concurrent[N]; pthread_t ids[N];
    init(&live,14,15,pthread_self()); current_teb=&live;
    for (unsigned i=0;i<N;i++) init(&concurrent[i],20,100+i,(pthread_t)0);
    for (unsigned i=0;i<N;i++) assert(!pthread_create(&ids[i],NULL,concurrent_exit,&concurrent[i]));
    while (atomic_load(&concurrent_done)!=N) usleep(1000);
    ios_ea_reap_exited_thread(&live);
    assert(joins==N && frees==N);
    puts("PASS 16 concurrent actual exit handoffs: all predecessors joined/freed once, no deadlock");

    reset(); init(&live,14,15,pthread_self()); current_teb=&live;
    /* Legacy cases need no live pthread to join, and keep the raw handoff. */
    const char *values[]={NULL,"","0","2","true","1x","1"};
    for (unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++) {
        if (values[i]) setenv("MADEIRA_EA_CEF",values[i],1); else unsetenv("MADEIRA_EA_CEF");
        unsigned before=legacy_exchanges;
        live.GdiTebBatch.pthread_id=(i==6 ? pthread_self() : (pthread_t)0);
        dispatch_exit();
        assert(legacy_exchanges-before==(i==6 ? 0 : 1));
        assert((ios_ea_pending_thread.teb!=NULL)==(i==6));
    }
    puts("PASS actual exit branch: only exact EA=1 uses new cleanup; other sessions retain legacy handoff");

    /* madeira-bcd: env.MADEIRA_THREAD_REAP_CHECKED = 1 (exactly) takes the checked cleanup for every process; default off */
    unsetenv("MADEIRA_EA_CEF"); ios_ea_pending_thread.teb=NULL;
    const char *checked_values[]={NULL,"","0","2","true","1x","1"};
    for (unsigned i=0;i<sizeof(checked_values)/sizeof(checked_values[0]);i++) {
        if (checked_values[i]) setenv("MADEIRA_THREAD_REAP_CHECKED",checked_values[i],1); else unsetenv("MADEIRA_THREAD_REAP_CHECKED");
        unsigned before=legacy_exchanges;
        memset(&ios_ea_pending_thread,0,sizeof(ios_ea_pending_thread));
        live.GdiTebBatch.pthread_id=(i==6 ? pthread_self() : (pthread_t)0);
        dispatch_exit();
        assert(legacy_exchanges-before==(i==6 ? 0 : 1));
        assert((ios_ea_pending_thread.teb!=NULL)==(i==6));
    }
    unsetenv("MADEIRA_THREAD_REAP_CHECKED");
    puts("PASS env.MADEIRA_THREAD_REAP_CHECKED: only exactly 1 takes the checked cleanup; unset, 0 and anything else keep the raw hand-off");
    return 0;
}
'''
cc = shutil.which('clang') or shutil.which('cc')
assert cc, 'a C compiler is required'
with tempfile.TemporaryDirectory(prefix='bcd-ea-reap-') as tmp:
    source, binary = Path(tmp) / 'reap.c', Path(tmp) / 'reap'
    source.write_text(harness)
    subprocess.run([cc, '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-pthread', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=15)
