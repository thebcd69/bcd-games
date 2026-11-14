#!/usr/bin/env python3
"""Actual staged helper, real charged named Mach VM; preparation is mocked, never executed."""
from pathlib import Path
import argparse, subprocess, tempfile
ap=argparse.ArgumentParser();ap.add_argument('--source-root',type=Path);args=ap.parse_args()
here=Path(__file__).resolve().parent
root=args.source_root or (here if (here/'JITAllocator.c').exists() else here.parents[1]/'app'/'Madeira')
s=(root/'JITAllocator.c').read_text();block=s[s.index('/* tagged-pool-region:begin'):s.index('/* tagged-pool-region:end */')+len('/* tagged-pool-region:end */')]
assert '__attribute__((noinline, optnone))\nvoid jit26_detach(void)' in s
assert 'mach_memory_entry_ownership' not in block and 'NO_FOOTPRINT' not in block
assert 'vm_protect(' not in block and 'vm_remap(' not in block
fixture=r'''
#include <mach/mach.h>
#include <mach/vm_map.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
#include "JITAllocator.h"
#define JIT_PAGE_SIZE 16384
static const char *mode;
static size_t request_size=4*JIT_PAGE_SIZE;
static vm_size_t test_page=JIT_PAGE_SIZE;
static mach_port_t live_entry;
static vm_address_t live_rx, foreign;
static vm_size_t live_size;
static int entries,maps,queries,prepares,unmaps,ports,checked_markers;
static bool succeeded, borrowing;
static vm_address_t borrowed_rw;
static void jit_log(const char *fmt,...){(void)fmt;errno=EIO;}
static bool ismode(const char*s){return !strcmp(mode,s);}
static kern_return_t entry_wrap(vm_map_t task,memory_object_size_t*n,memory_object_offset_t off,vm_prot_t flags,mach_port_t*out,mach_port_t parent){
 assert(!live_entry&&!live_rx&&*n==request_size&&!off&&!parent&&task==mach_task_self());
 assert(flags==(MAP_MEM_NAMED_CREATE|MAP_MEM_LEDGER_TAGGED|VM_PROT_ALL));entries++;errno=ERANGE;
 if(ismode("entry-fail")){*out=mach_task_self();return KERN_FAILURE;}
 if(ismode("entry-null")){*out=MACH_PORT_NULL;return KERN_SUCCESS;}
 kern_return_t k=mach_make_memory_entry_64(task,n,off,flags,out,parent);assert(!k);live_entry=*out;
 if(ismode("entry-short"))*n-=JIT_PAGE_SIZE;
 if(ismode("entry-long"))*n+=JIT_PAGE_SIZE;
 return k;}
static kern_return_t map_wrap(vm_map_t task,vm_address_t*a,vm_size_t n,vm_address_t mask,int flags,mach_port_t entry,vm_object_offset_t off,boolean_t copy,vm_prot_t cur,vm_prot_t max,vm_inherit_t inherit){
 assert(entries==1&&!maps&&!live_rx&&live_entry==entry&&*a==0&&n==request_size&&!mask&&flags==VM_FLAGS_ANYWHERE&&!off&&!copy);
 assert(cur==(VM_PROT_READ|VM_PROT_EXECUTE)&&max==VM_PROT_ALL&&inherit==VM_INHERIT_NONE);maps++;errno=ERANGE;
 if(ismode("map-fail")){*a=foreign;return KERN_NO_SPACE;}
 if(ismode("high-map"))*a=UINT64_C(0x1000000000);
 if(ismode("edge-map"))*a=UINT64_C(0x1000000000)-JIT_PAGE_SIZE;
 kern_return_t k=vm_map(task,a,n,mask,flags,entry,off,copy,cur,max,inherit);assert(!k);live_rx=*a;live_size=n;return k;}
static kern_return_t query_wrap(vm_map_t task,vm_address_t*a,vm_size_t*n,natural_t*d,vm_region_recurse_info_t raw,mach_msg_type_number_t*count){
 assert(live_rx);vm_address_t asked=*a;bool isrw=borrowed_rw && asked>=borrowed_rw && asked<borrowed_rw+live_size;assert(isrw||(asked>=live_rx&&asked<live_rx+live_size));queries++;errno=ERANGE;
 if(ismode("unmap-fail")||(ismode("borrow-query-fail")&&borrowing)||ismode("query-fail")|| (ismode("post-query-fail")&&prepares))return KERN_FAILURE;
 kern_return_t k=vm_region_recurse_64(task,a,n,d,raw,count);assert(!k);vm_region_submap_info_data_64_t*i=(void*)raw;
 if(ismode("query-short"))(*count)--;
 if(ismode("query-submap"))i->is_submap=1;
 if(ismode("query-gap"))*a=asked+JIT_PAGE_SIZE;
 if(ismode("query-empty")){*a=asked;*n=0;}
 if(ismode("query-writable")||(ismode("post-writable")&&prepares))i->protection|=VM_PROT_WRITE;
 if(ismode("query-noexecute"))i->protection&=~VM_PROT_EXECUTE;
 if(ismode("query-noread"))i->protection&=~VM_PROT_READ;
 if(ismode("query-max"))i->max_protection&=~VM_PROT_WRITE;
 if(ismode("split-ok")||ismode("split-bad")){*a=asked;*n=JIT_PAGE_SIZE;if(ismode("split-bad")&&asked==live_rx+live_size-JIT_PAGE_SIZE)i->protection=VM_PROT_NONE;}
 if(borrowing&&isrw){
  if(ismode("rw-noread"))i->protection&=~VM_PROT_READ;
  if(ismode("rw-nowrite"))i->protection&=~VM_PROT_WRITE;
  if(ismode("rw-executable"))i->protection|=VM_PROT_EXECUTE;
  if(ismode("rw-max"))i->max_protection&=~VM_PROT_WRITE;
 }
 return k;}
static void *jit26_prepare_region_mock(void*p,size_t n){
 assert((uintptr_t)p==live_rx&&n==live_size&&entries==1&&maps==1&&queries&&!prepares&&!live_entry&&borrowed_rw);
 assert(live_rx<UINT64_C(0x1000000000)&&n<=UINT64_C(0x1000000000)-live_rx);prepares++;errno=ERANGE;
 if(ismode("helper-return"))return (void*)foreign;
 if(ismode("helper-null"))return NULL;
 for(size_t off=0;off<n;off+=JIT_PAGE_SIZE){assert(*(unsigned char*)(borrowed_rw+off)==0);checked_markers++;
  if(!ismode("no-markers")&&!(ismode("last-marker")&&off==n-JIT_PAGE_SIZE)&&!(ismode("middle-marker")&&off==JIT_PAGE_SIZE))*(unsigned char*)(borrowed_rw+off)=0x69;}
 return p;}
static kern_return_t port_wrap(ipc_space_t task,mach_port_name_t port){
 assert(port==live_entry&&port!=mach_task_self());ports++;errno=ERANGE;
 if(ismode("port-fail")||ismode("both-cleanup-fail"))return KERN_INVALID_RIGHT;
 kern_return_t k=mach_port_deallocate(task,port);assert(!k);live_entry=0;return k;}
static kern_return_t unmap_wrap(vm_map_t task,vm_address_t a,vm_size_t n){
 assert(live_rx&&a==live_rx&&n==live_size&&a!=foreign);unmaps++;errno=ERANGE;
 if(ismode("unmap-fail")||ismode("both-cleanup-fail"))return KERN_FAILURE;
 kern_return_t k=vm_deallocate(task,a,n);assert(!k);live_rx=0;return k;}
#define vm_page_size test_page
#define mach_make_memory_entry_64 entry_wrap
#define vm_map map_wrap
#define vm_region_recurse_64 query_wrap
#define mach_port_deallocate port_wrap
#define vm_deallocate unmap_wrap
#define jit26_prepare_region jit26_prepare_region_mock
'''+block+r'''
#undef vm_page_size
#undef mach_make_memory_entry_64
#undef vm_map
#undef vm_region_recurse_64
#undef mach_port_deallocate
#undef vm_deallocate
#undef jit26_prepare_region
int main(int argc,char**argv){assert(argc==2);mode=argv[1];
 assert(vm_allocate(mach_task_self(),&foreign,JIT_PAGE_SIZE,VM_FLAGS_ANYWHERE)==KERN_SUCCESS);memset((void*)foreign,0x57,JIT_PAGE_SIZE);
 if(ismode("zero"))request_size=0;
 if(ismode("unaligned"))request_size--;
 if(ismode("overflow"))request_size=SIZE_MAX;
 if(ismode("huge"))request_size=UINT64_C(0x1000004000);
 if(ismode("wrong-page"))test_page=4096;
 int fatal=123;errno=EDOM;
 void *rx=jit_tagged_region(request_size,ismode("null-output")?NULL:&fatal);assert(errno==EDOM);
 assert(prepares==0&&checked_markers==0); /* Raw phase must never prepare/read marker bytes. */
 bool raw_fail=ismode("zero")||ismode("unaligned")||ismode("overflow")||ismode("huge")||ismode("wrong-page")||ismode("null-output")||ismode("entry-fail")||ismode("entry-null")||ismode("entry-short")||ismode("entry-long")||ismode("map-fail")||ismode("high-map")||ismode("edge-map")||ismode("query-fail")||ismode("query-short")||ismode("query-submap")||ismode("query-gap")||ismode("query-empty")||ismode("query-writable")||ismode("query-noexecute")||ismode("query-noread")||ismode("query-max")||ismode("split-bad")||ismode("port-fail")||ismode("unmap-fail")||ismode("both-cleanup-fail");
 assert((rx==NULL)==raw_fail);
 bool fatal_expected=ismode("port-fail")||ismode("unmap-fail")||ismode("both-cleanup-fail");
 if(ismode("null-output"))assert(fatal==123);else assert((fatal!=0)==fatal_expected);
 if(ismode("both-cleanup-fail"))assert(fatal==KERN_INVALID_RIGHT);
 if(ismode("zero")||ismode("unaligned")||ismode("overflow")||ismode("huge")||ismode("wrong-page")||ismode("null-output"))assert(!entries&&!maps&&!prepares);
 if(ismode("map-fail"))assert(!unmaps&&ports==1);
 if(ismode("entry-fail")||ismode("entry-null"))assert(!ports&&!maps);
 if(ismode("high-map")||ismode("edge-map"))assert(!queries&&!prepares&&unmaps==1);
 if(rx){
  assert(rx==(void*)live_rx&&!live_entry&&ports==1&&maps==1&&!unmaps);
  vm_prot_t cur=0,max=0;
  assert(vm_remap(mach_task_self(),&borrowed_rw,live_size,0,VM_FLAGS_ANYWHERE,mach_task_self(),live_rx,FALSE,&cur,&max,VM_INHERIT_NONE)==KERN_SUCCESS);
  assert(vm_protect(mach_task_self(),borrowed_rw,live_size,FALSE,VM_PROT_READ|VM_PROT_WRITE)==KERN_SUCCESS);
  for(size_t off=0;off<live_size;off+=JIT_PAGE_SIZE)assert(*(unsigned char*)(borrowed_rw+off)==0);
  if(ismode("different-object")){
   assert(vm_deallocate(mach_task_self(),borrowed_rw,live_size)==KERN_SUCCESS);borrowed_rw=0;
   assert(vm_allocate(mach_task_self(),&borrowed_rw,live_size,VM_FLAGS_ANYWHERE)==KERN_SUCCESS);
  }
  borrowing=true;int oldports=ports,oldunmaps=unmaps;errno=EDOM;
  void*arg_rx=rx;void*arg_rw=(void*)borrowed_rw;size_t arg_size=live_size;
  if(ismode("borrow-zero"))arg_size=0;
  if(ismode("borrow-size"))arg_size--;
  if(ismode("borrow-null"))arg_rw=NULL;
  if(ismode("borrow-overlap"))arg_rw=rx;
  if(ismode("borrow-high"))arg_rx=(void*)UINT64_C(0x1000000000);
  if(ismode("borrow-overflow"))arg_rw=(void*)(UINTPTR_MAX-(JIT_PAGE_SIZE-1));
  bool ok=jit_tagged_prepare_owned_region(arg_rx,arg_rw,arg_size);assert(errno==EDOM);
  succeeded=ismode("ok")||ismode("split-ok");assert(ok==succeeded);
  assert(ports==oldports&&unmaps==oldunmaps&&live_rx&&borrowed_rw); /* borrowed failure never releases */
  if(succeeded){assert(prepares==1&&checked_markers==4);
   for(size_t off=0;off<live_size;off+=JIT_PAGE_SIZE){assert(*(unsigned char*)(borrowed_rw+off)==0x69);*(unsigned char*)(borrowed_rw+off+128)=0xa3;assert(*(unsigned char*)(live_rx+off+128)==0xa3);}
  }
  assert(vm_deallocate(mach_task_self(),borrowed_rw,live_size)==KERN_SUCCESS);borrowed_rw=0;
 }
 if(!fatal_expected&&!rx)assert(!live_entry&&!live_rx);
 if(ismode("port-fail"))assert(live_entry&&!live_rx&&unmaps==1&&ports==1);
 if(ismode("unmap-fail"))assert(!live_entry&&live_rx&&unmaps==1);
 if(ismode("both-cleanup-fail"))assert(live_entry&&live_rx&&ports==1&&unmaps==1);
 if(live_rx)assert(vm_deallocate(mach_task_self(),live_rx,live_size)==KERN_SUCCESS);
 if(live_entry)assert(mach_port_deallocate(mach_task_self(),live_entry)==KERN_SUCCESS);
 for(size_t i=0;i<JIT_PAGE_SIZE;i++)assert(*(unsigned char*)(foreign+i)==0x57);
 assert(vm_deallocate(mach_task_self(),foreign,JIT_PAGE_SIZE)==KERN_SUCCESS);
 printf("PASS %s entries=%d maps=%d prepare=%d fatal=%d\n",mode,entries,maps,prepares,fatal);}
'''
modes=['ok','split-ok','zero','unaligned','overflow','huge','wrong-page','null-output','entry-fail','entry-null','entry-short','entry-long','map-fail','high-map','edge-map','query-fail','post-query-fail','query-short','query-submap','query-gap','query-empty','query-writable','post-writable','query-noexecute','query-noread','query-max','split-bad','helper-return','helper-null','no-markers','last-marker','middle-marker','port-fail','unmap-fail','both-cleanup-fail']
modes += ['borrow-query-fail','rw-noread','rw-nowrite','rw-executable','rw-max','different-object','borrow-zero','borrow-size','borrow-null','borrow-overlap','borrow-high','borrow-overflow']
with tempfile.TemporaryDirectory(prefix='tagged-pool-') as tmp:
 p=Path(tmp);src=p/'test.c';exe=p/'test'
 def compile_text(text):
  src.write_text(text);subprocess.run(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I',str(root),str(src),'-o',str(exe)],check=True)
 compile_text(fixture)
 for mode in modes:subprocess.run([str(exe),mode],check=True)
 for title,old,new,mode in [
  ('partial preparation',"if (*(const volatile unsigned char *)(rx + offset) != 0x69 ||\n            *(const volatile unsigned char *)(rw + offset) != 0x69) goto out;","if (false) goto out;",'last-marker'),
  ('foreign failed-map output',"if (kr != KERN_SUCCESS) goto out;\n    own_rx = true;","own_rx = true;\n    if (kr != KERN_SUCCESS) goto out;",'map-fail'),
  ('errno restoration',"errno = saved_errno;","(void)saved_errno;",'ok')]:
  assert old in fixture;compile_text(fixture.replace(old,new,1));r=subprocess.run([str(exe),mode],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);assert r.returncode!=0,title;print('PASS negative control:',title)
 print(f'PASS {len(modes)} real-Mach cases +3 negative controls; no host execution or phone-ledger claim')
