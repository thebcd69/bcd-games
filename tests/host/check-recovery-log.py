#!/usr/bin/env python3
"""Exercise the actual bounded recovery logs without running Wine or FEX."""
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[2]
loader=(root/'build/ntdll-unix/loader_ios.c').read_text()
signal=(root/'build/ntdll-unix/signal_arm64_ios.c').read_text()
def function(src,signature):
 a=src.index(signature)
 return src[a:src.index('\n}',a)+2]+'\n'
option=function(loader,'static int ios_recovery_log_option(')
recovery=function(loader,'static int ios_recovery_log_admit(')
ill=function(signal,'static int ios_ill_diag_admit(')
wrap=function(loader,'static NTSTATUS ios_wrap_2(')
assert loader.index('ios_recovery_log_option(), __ATOMIC_RELAXED') < loader.index('    start_main_thread();', loader.index('DECLSPEC_EXPORT void __wine_main('))
assert 'getenv' not in ill
assert wrap.index('ios_recovery_log_admit(')<wrap.index('ios_wrap_unix_call(2,')
assert 'return p->len;' in wrap and 'g_wine_unix_call_count++;' in wrap
body=signal[signal.index('static void ill_handler('):signal.index('static inline uint64_t ios_get_reg(')]
assert body.index('ios_track_signal(')<body.index('const int ill_report = ios_ill_diag_admit(')
assert body.index('if (ill_report)')<body.index('setup_exception( sigcontext, &rec );')
unit=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
static unsigned notes;
static int ios_recovery_log_sampling;
static int note(int fd,const char *fmt,...) { (void)fd;(void)fmt;++notes;return 0; }
#define dprintf note
typedef struct { int valid; } arm_thread_state64_t;
static uint64_t ios_fault_guest_rip(const arm_thread_state64_t *state,uint64_t pc) {
 return state->valid && pc==0x160000100 ? 0x140001000 : 0;
}
'''+option+recovery+ill+r'''
static unsigned repeat(const char *s,unsigned count) {
 unsigned kept=0;
 for(unsigned i=0;i<count;++i) kept+=ios_recovery_log_admit(s,(unsigned)strlen(s));
 return kept;
}
int main(int argc,char **argv) {
 if(argc==3 && !strcmp(argv[1],"replay")) {
  ios_recovery_log_sampling=1;
  FILE *f=fopen(argv[2],"r");assert(f);char line[16384];
  unsigned long long total=0,kept=0,lines=0;
  while(fgets(line,sizeof(line),f)) {
   unsigned n=(unsigned)strlen(line);total+=n;++lines;
   if(ios_recovery_log_admit(line,n))kept+=n;
  }
  fclose(f);printf("REPLAY: %llu lines, %llu bytes before, %llu retained by FEX-record filter\n",lines,total,kept);return 0;
 }
 assert(argc==1);
 const char *records[]={
  "D BC Exception: Code: C000001D Address: 170000100\n",
  "D BC Reconstructing context\n",
  "D BC pc: 170000100 rip: 140001000\n",
  "D BC Rethrowing onto guest stack: 700001000\n",
  "D BC Passing through exception\n"
 };
 const char *off[]={NULL,"0","", "invalid", "true"};
 arm_thread_state64_t enabled_state={1};
 for(unsigned mode=0;mode<sizeof(off)/sizeof(off[0]);++mode) {
  if(off[mode])setenv("MADEIRA_RECOVERY_LOG_SAMPLE",off[mode],1);else unsetenv("MADEIRA_RECOVERY_LOG_SAMPLE");
  ios_recovery_log_sampling=ios_recovery_log_option();assert(!ios_recovery_log_sampling);
  for(unsigned i=0;i<5;++i)assert(repeat(records[i],1000)==1000);
  for(unsigned i=0;i<1000;++i)assert(ios_ill_diag_admit(&enabled_state,0x160000100));
  assert(!notes);
 }
 setenv("MADEIRA_RECOVERY_LOG_SAMPLE","1",1);
 ios_recovery_log_sampling=ios_recovery_log_option();assert(ios_recovery_log_sampling);
 for(unsigned i=0;i<5;++i) assert(repeat(records[i],10000)==25);
 assert(repeat("E BC Invalid instruction in entry block: 140001000\n",1000)==1000);
 assert(repeat("A assertion failed\n",1000)==1000);
 assert(repeat("00bc:err:module:import_dll required library failed\n",1000)==1000);
 assert(repeat("D C0 Reconstructing context\n",1000)==1000);
 assert(repeat("D BC Reconstructing context failed: new diagnostic\n",1000)==1000);
 assert(repeat("D BC Exception: Code: C0000005 Address: 170000100 AV WRITE of DEAD\n",1)==1);
 for(unsigned i=1;i<5;++i) assert(repeat(records[i],1000)==1000);
 assert(repeat("D BC Exception: Code: C06D007E Address: 170000100\n",1)==1);
 assert(repeat(records[4],1000)==1000);
 repeat(records[0],1);
 repeat("D 1BC Exception: Code: C000001D Address: 170000100\n",1);
 assert(repeat(records[1],1000)==1000); /* hash collision keeps unknown context */
 repeat(records[0],1);
 assert(repeat("D BC Reconstructing context\nD BC Exception: Code: C0000005 Address: DEAD\n",1)==1);
 assert(repeat(records[1],1000)==1000);
 repeat(records[0],1);
 assert(repeat("D BC Reconstructing context",1)==1);
 assert(repeat(records[1],1000)==1000);
 const char *odd[]={"", "D", "D ", "D 0 pc: x\n", "D 123456789 pc: x\n", "D ZZ pc: x\n", "X BC Reconstructing context\n"};
 for(unsigned i=0;i<sizeof(odd)/sizeof(odd[0]);++i) assert(repeat(odd[i],1)==1);
 char bounded[]={'D',' ','B','C',' ','x','\n'};
 assert(ios_recovery_log_admit(bounded,sizeof(bounded))==1);
 arm_thread_state64_t state={1}; unsigned kept=0;
 for(unsigned i=0;i<1000;++i) assert(ios_ill_diag_admit(&state,0x100000100));
 for(unsigned i=0;i<25000;++i) kept+=ios_ill_diag_admit(&state,0x160000100);
 assert(kept==8+25000/1024);
 state.valid=0;
 for(unsigned i=0;i<1000;++i) assert(ios_ill_diag_admit(&state,0x160000100));
 assert(notes>0);
 puts("PASS: default/off/invalid keep all output; opt-in bounded recovery output; AV/delay-load errors, unknown contexts, collisions, mixed writes and unknown native faults retained");
}
'''
with tempfile.TemporaryDirectory(prefix='bcd-recovery-log-') as td:
 td=Path(td);(td/'t.c').write_text(unit)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(td/'t.c'),'-o',str(td/'t')],check=True)
 subprocess.run([str(td/'t')],check=True)
 if len(sys.argv)>1:subprocess.run([str(td/'t'),'replay',str(Path(sys.argv[1]).resolve())],check=True)
