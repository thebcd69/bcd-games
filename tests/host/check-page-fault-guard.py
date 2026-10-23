#!/usr/bin/env python3
"""Compile the real page-fault guard and retain memory-fault termination."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
a = src.index('                    if (req->exception == EXC_BAD_ACCESS)\n                    {\n                        static volatile uint64_t stuck_page;')
b = src.index('\n\n                    if (cnt <= 50', a)
guard = src[a:b]
old = guard.replace('if (req->exception == EXC_BAD_ACCESS)', 'if (1)', 1)
prefix = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#undef __darwin_arm_thread_state64_get_sp
#undef __darwin_arm_thread_state64_set_sp
#undef __darwin_arm_thread_state64_set_pc_fptr
#define EXC_BAD_ACCESS 1
#define EXC_BAD_INSTRUCTION 2
#define KERN_SUCCESS 0
#define ARM_THREAD_STATE64 6
#define VM_REGION_BASIC_INFO_COUNT_64 10
#define VM_REGION_BASIC_INFO_64 9
#define MACH_PORT_NULL 0
#define STDERR_FILENO 2
typedef uint64_t mach_vm_address_t;
typedef uint64_t mach_vm_size_t;
typedef unsigned mach_msg_type_number_t;
typedef unsigned mach_port_t;
typedef void *thread_state_t;
typedef void *vm_region_info_t;
typedef struct { int protection, max_protection; } vm_region_basic_info_data_64_t;
typedef struct { uint64_t pc, sp, __x[29]; } arm_thread_state64_t;
static unsigned killed;
static int mach_task_self(void) { return 0; }
static int mach_vm_region(int task, mach_vm_address_t *q, mach_vm_size_t *rs,
                         int f, vm_region_info_t r, mach_msg_type_number_t *rc, mach_port_t *ro)
{ (void)task;(void)q;(void)rs;(void)f;(void)r;(void)rc;(void)ro;return 1; }
static uint64_t __darwin_arm_thread_state64_get_sp(arm_thread_state64_t s) { return s.sp; }
#define __darwin_arm_thread_state64_set_sp(s,v) ((s).sp=(v))
#define __darwin_arm_thread_state64_set_pc_fptr(s,v) ((s).pc=(uintptr_t)(v))
void abort_thread(int status) { (void)status; }
static int thread_set_state(int t,int f,thread_state_t p,unsigned n)
{ (void)t;(void)f;(void)n;assert(((arm_thread_state64_t *)p)->pc==(uintptr_t)abort_thread);++killed;return 0; }
#define dprintf(...) ((void)0)
static void fault(int exception, uint64_t address) {
 struct { int exception; } request = {exception}, *req=&request;
 uint64_t fault_addr=address, fault_pc_check=0x16cff44e8;
 arm_thread_state64_t state={.pc=0,.sp=0x7000000101};
 int thread=1, handled=0; unsigned count=0;
'''
suffix = r'''
 (void)handled;
}
int main(int argc,char **argv) {
 assert(argc==2); unsigned n=0;
 int instruction=!strcmp(argv[1],"instruction");
 int advancing=!strcmp(argv[1],"advancing");
 int reset=!strcmp(argv[1],"reset");
 while(n<25000 && !killed) {
  uint64_t addr=instruction ? 0xd4400000 : 0x7000000000;
  if(advancing) addr+=(n%128)*8;
  if(reset && n>=49) addr+=0x4000;
  fault(instruction ? EXC_BAD_INSTRUCTION : EXC_BAD_ACCESS,addr);++n;
 }
 if(instruction) assert(!killed && n==25000);
 else if(advancing) assert(killed==1 && n==20001);
 else if(reset) assert(killed==1 && n==100);
 else assert(killed==1 && n==51);
 printf("PASS %s: %u faults, %u aborts\n",argv[1],n,killed);
}
'''
with tempfile.TemporaryDirectory(prefix='bcd-page-guard-') as td:
    td = Path(td)
    for label, block in [('old', old), ('fixed', guard)]:
        (td / 't.c').write_text(prefix + block + suffix)
        exe = td / label
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(td / 't.c'), '-o', str(exe)], check=True)
        for mode in (['instruction'] if label == 'old' else ['instruction', 'memory', 'advancing', 'reset']):
            r = subprocess.run([str(exe), mode], capture_output=True, text=True)
            if label == 'old':
                assert r.returncode != 0 and 'killed' in r.stderr
                print('PASS: old page guard incorrectly aborts the instruction-trap fixture')
            else:
                assert r.returncode == 0, r.stdout + r.stderr
                print(r.stdout.strip())
