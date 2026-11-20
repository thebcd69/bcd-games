#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Bogdan Seniuc
# Taken by BCD (thebcd69) from https://github.com/thebcd69/bcd-games (pull request #55 by bogsen), November 2026. See THIRD-PARTY-NOTICES.md.
# Modified by BCD (thebcd69), 2026-11-15: the harness takes iOS's 16 KB host page on every host (the Intel build Mac has 4 KB pages).
"""Production-extracted zero backing and reservation/decommit hooks, real Darwin COW VM."""
from pathlib import Path
import subprocess,tempfile,argparse
ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path);args=ap.parse_args()
root=Path(__file__).resolve().parent
source=args.source or (root/'virtual_ios.c' if (root/'virtual_ios.c').exists() else root.parents[1]/'build'/'ntdll-unix'/'virtual_ios.c');s=source.read_text()
def marker(name):
 return s[s.index('/* '+name+':begin'):s.index('/* '+name+':end */')]
def function(name):
 endname=s.index(name+'(') if name+'(' in s else s.index(name+' (')
 start=s.rfind('\n',0,endname)+1;brace=s.index('{',endname);level=1;i=brace+1
 while level:
  level+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[start:i]
helpers=marker('fex-zero-file');zero_log=marker('fex-zero-file-zero-log')
reserve=marker('fex-zero-file-reserve-hook');decommit=marker('fex-zero-file-decommit-hook')
# Existing split/free lifecycle must retain the bit through actual production functions.
remove=function('remove_pages_from_view');free=function('free_pages');unix_prot=function('get_unix_prot')
assert 'ios_zero_file_init();' in function('virtual_init')
alloc=function('allocate_virtual_memory') if False else s[s.index('static NTSTATUS allocate_virtual_memory(',s.index('/* Madeira: THIN RESERVATIONS.')):]
assert alloc.index('ios_swap_whole_resv')<alloc.index('ios_zero_file_reserve')<alloc.index('allocation_done:')
assert 'MAP_SHARED' not in helpers and 'pwrite(' not in helpers
fixture=r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#define WINE_IOS 1
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017u
#define STATUS_CONFLICTING_ADDRESSES 0xc0000018u
#define MEM_RESERVE 0x2000
#define MEM_TOP_DOWN 0x100000
#define MEM_COMMIT 0x1000
#define MEM_WRITE_WATCH 0x200000
#define PAGE_NOACCESS 1
#define PAGE_READWRITE 4
#define VPROT_READ 1
#define VPROT_WRITE 2
#define VPROT_EXEC 4
#define VPROT_WRITECOPY 8
#define VPROT_GUARD 16
#define VPROT_WRITEWATCH 32
#define VPROT_COMMITTED 64
#define VPROT_SYSTEM 128
#define SEC_FILE 256
#define SEC_IMAGE 512
#define SEC_RESERVE 1024
#define SEC_COMMIT 2048
#define VPROT_PLACEHOLDER 4096
#define VPROT_FREE_PLACEHOLDER 8192
#define VPROT_ARM64EC 16384
#define IOS_VIEW_ZERO_FILE 0x40
#define FALSE 0
#define ERR(...) ((void)0)
#define VIRTUAL_DEBUG_DUMP_VIEW(v) ((void)0)
#define ROUND_SIZE(a,n,m) (((uintptr_t)(a)+(uintptr_t)(n)+(m))&~(uintptr_t)(m))
typedef unsigned char BYTE;typedef uintptr_t UINT_PTR;typedef unsigned int NTSTATUS;typedef unsigned int ULONG;
struct file_view {void*base;size_t size;unsigned protect,ios_flags;uint64_t wow_owner;void*ios_owner;unsigned ios_fex_name,ios_fex_alignment;uint64_t ios_fex_name_owner;};
static uintptr_t arena,ios_fex_arena_base_unix,ios_fex_arena_end_unix;
static size_t host_page_mask,page_size=4096,arena_size=128u<<20;
static int force_exec_prot,locked,alias_overlap,swap_overlap,pool_overlap,fail_file,fail_anon,file_maps,anon_maps,deleted,init_fail;
static struct file_view *table[32];static unsigned nt;
static unsigned char vp[128u<<20>>12];
static int fd_count,log_lines;
static int mock_mkstemp(char*p){assert(!locked);if(init_fail==1){errno=ENOSPC;return-1;}int f=mkstemp(p);if(f>=0)fd_count++;return f;}
static int mock_open(const char*p,int flags){assert(!locked&&flags==(O_RDONLY|O_CLOEXEC|O_NOFOLLOW));if(init_fail==2)return-1;int f=open(p,flags);if(f>=0)fd_count++;return f;}
static int mock_close(int f){assert(!locked);int r=close(f);if(!r)fd_count--;return r;}
static int mock_ftruncate(int f,off_t n){assert(!locked&&n==(64u<<20));if(init_fail==3)return-1;return ftruncate(f,n);}
static int mock_fchmod(int f,mode_t m){assert(!locked&&m==0600);return fchmod(f,m);}
static int mock_fstat(int f,struct stat*v){assert(!locked);return fstat(f,v);}
static int mock_unlink(const char*p){assert(!locked);return unlink(p);}
static int quiet_log(int fd,const char*fmt,...){(void)fd;(void)fmt;log_lines++;return 0;}
static int is_view_valloc(const struct file_view*v){return !(v->protect&(SEC_FILE|SEC_IMAGE|SEC_RESERVE|SEC_COMMIT));}
static struct file_view*find_view(const void*b,size_t n){uintptr_t a=(uintptr_t)b;for(unsigned i=0;i<nt;i++){uintptr_t v=(uintptr_t)table[i]->base;if(a>=v&&a-v<=table[i]->size&&n<=table[i]->size-(a-v))return table[i];}return NULL;}
static uintptr_t test_window_lo, test_window_hi;
static uintptr_t ios_wow_window_covering(const void *lo,const void *hi){return (uintptr_t)lo<test_window_hi && (uintptr_t)hi>test_window_lo ? test_window_lo:0;}
static unsigned get_page_vprot(const void*p){uintptr_t a=(uintptr_t)p;assert(a>=arena&&a<arena+arena_size);return vp[(a-arena)>>12];}
static void set_page_vprot(void*b,size_t n,unsigned f){assert((uintptr_t)b>=arena&&(uintptr_t)b+n<=arena+arena_size);for(size_t o=0;o<n;o+=page_size)vp[((uintptr_t)b-arena+o)>>12]=(unsigned char)f;}
static void set_page_vprot_bits(void*b,size_t n,unsigned set,unsigned clear){for(size_t o=0;o<n;o+=page_size){unsigned char *p=&vp[((uintptr_t)b-arena+o)>>12];*p=(unsigned char)((*p&~clear)|set);}}
static int ios_jit_pool_intersects(const void*b,size_t n){(void)b;(void)n;return pool_overlap;}
static int ios_swap_overlaps(const void*b,size_t n){(void)b;(void)n;return swap_overlap;}
static int ios_jit_anon_alias_overlaps(void*b,size_t n,void*x,void*y){(void)b;(void)n;assert(!x&&!y);return alias_overlap;}
static void*mock_mmap(void*b,size_t n,int prot,int flags,int fd,off_t off){assert(locked&&b&&n&&!(n&host_page_mask)&&flags==(MAP_FIXED|MAP_PRIVATE)&&off==0);assert(find_view(b,n));assert((fcntl(fd,F_GETFL)&O_ACCMODE)==O_RDONLY);assert(fcntl(fd,F_GETFD)&FD_CLOEXEC);file_maps++;if(fail_file){errno=ENOMEM;return MAP_FAILED;}return mmap(b,n,prot,flags,fd,off);}
static void*anon_mmap_fixed(void*b,size_t n,int prot,int flags){assert(locked&&flags==0);anon_maps++;if(fail_anon){errno=ENOMEM;return MAP_FAILED;}return mmap(b,n,prot,MAP_PRIVATE|MAP_ANON|MAP_FIXED,-1,0);}
#define mkstemp mock_mkstemp
#define open mock_open
#define close mock_close
#define ftruncate mock_ftruncate
#define fchmod mock_fchmod
#define fstat mock_fstat
#define unlink mock_unlink
#define dprintf quiet_log
#define mmap mock_mmap
'''+unix_prot+'\n'+helpers+r'''
#undef mkstemp
#undef open
#undef close
#undef ftruncate
#undef fchmod
#undef fstat
#undef unlink
#undef mmap
static struct file_view*alloc_view(void){return calloc(1,sizeof(struct file_view));}
static void register_view(struct file_view*v){assert(nt<32);table[nt++]=v;}
static void unregister_view(struct file_view*v){unsigned i;for(i=0;i<nt;i++)if(table[i]==v)break;assert(i<nt);table[i]=table[--nt];}
static void unmap_area(void*b,size_t n){assert(locked);assert(mmap(b,n,PROT_NONE,MAP_FIXED|MAP_PRIVATE|MAP_ANON,-1,0)==b);}
static void clear_arm64ec_range(void*b,size_t n){(void)b;(void)n;assert(0);}
static void delete_view(struct file_view*v){assert(locked);unmap_area(v->base,v->size);set_page_vprot(v->base,v->size,0);unregister_view(v);free(v);deleted++;}
'''+remove+'\n'+free+r'''
static NTSTATUS reserve_hook(struct file_view*view,unsigned type,unsigned protect,unsigned attributes){NTSTATUS status=0;
'''+reserve+r'''
allocation_done:return status;}
static NTSTATUS reset_hook(struct file_view*view,char*host_start,char*host_end){const char *dc_branch=NULL;const void *dc_verify=(void*)1;
'''+decommit+r'''
 {if(anon_mmap_fixed(host_start,(size_t)(host_end-host_start),PROT_READ|PROT_WRITE,0)==MAP_FAILED)return STATUS_NO_MEMORY;dc_branch="anonymous";}
 (void)dc_verify;assert(dc_branch);set_page_vprot_bits(host_start,(size_t)(host_end-host_start),0,VPROT_COMMITTED);return STATUS_SUCCESS;}
static struct file_view*newview(size_t off,size_t n){struct file_view*v=alloc_view();assert(v);v->base=(void*)(arena+off);v->size=n;assert(!find_view(v->base,n));assert(mmap(v->base,n,PROT_NONE,MAP_FIXED|MAP_PRIVATE|MAP_ANON,-1,0)==v->base);set_page_vprot(v->base,n,0);register_view(v);return v;}
static void commit(struct file_view*v,size_t off,size_t n){assert(off+n<=v->size);uintptr_t a=(uintptr_t)v->base+off,lo=a&~host_page_mask,hi=(a+n+host_page_mask)&~host_page_mask;assert(!mprotect((void*)lo,hi-lo,PROT_READ|PROT_WRITE));set_page_vprot((void*)a,n,VPROT_READ|VPROT_WRITE|VPROT_COMMITTED);}
static void zero_log_test(unsigned long dc_n){void*base=(void*)arena;size_t size=16384;
'''+zero_log+r'''
}
static vm_prot_t protection(void*p){mach_vm_address_t a=(uintptr_t)p;mach_vm_size_t n=0;natural_t depth=0;vm_region_submap_info_data_64_t i={0};mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;assert(mach_vm_region_recurse(mach_task_self(),&a,&n,&depth,(vm_region_recurse_info_t)&i,&count)==KERN_SUCCESS);assert(a<=(uintptr_t)p);return i.protection;}
int main(void){host_page_mask=16383;/* madeira-bcd (build 666): iOS's 16 KB host page on every host (the Intel build Mac has 4 KB pages) */void *a=mmap(NULL,arena_size,PROT_NONE,MAP_ANON|MAP_PRIVATE,-1,0);assert(a!=MAP_FAILED);arena=(uintptr_t)a;ios_fex_arena_base_unix=arena;ios_fex_arena_end_unix=arena+arena_size;assert(!setenv("TMPDIR","/tmp",1));
 for(unsigned i=0;i<4;i++){const char*flags[]={"","0","true","01"};assert(!setenv("MADEIRA_FEX_ZERO_FILE",flags[i],1));ios_zero_file_init();assert(ios_zero_file_fd==-1&&fd_count==0);}
 assert(!setenv("MADEIRA_FEX_ZERO_FILE","1",1));for(init_fail=1;init_fail<=3;init_fail++){ios_zero_file_init();assert(ios_zero_file_fd<0&&fd_count==0);}init_fail=0;errno=EDOM;ios_zero_file_init();assert(ios_zero_file_fd>=0&&fd_count==1&&errno==EDOM);int fd=ios_zero_file_fd;ios_zero_file_init();assert(ios_zero_file_fd==fd&&fd_count==1);struct stat st;assert(!fstat(fd,&st)&&st.st_nlink==0&&st.st_size==(64u<<20)&&(st.st_mode&0777)==0600);
 locked=1;struct file_view*v=newview(0,8u<<20);assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));assert(v->ios_flags==IOS_VIEW_ZERO_FILE&&v->protect==0&&protection(v->base)==VM_PROT_NONE);commit(v,4096,(8u<<20)-8192);for(size_t i=4096;i<(8u<<20)-4096;i+=16384)assert(!((volatile char*)v->base)[i]);((char*)v->base)[32768]=0x5a;
 struct file_view*w=newview(16u<<20,8u<<20);assert(!reserve_hook(w,MEM_RESERVE,PAGE_NOACCESS,0));commit(w,0,w->size);assert(!((char*)w->base)[32768]);((char*)w->base)[32768]=0x33;assert(((char*)v->base)[32768]==0x5a);unsigned char filebyte=1;assert(pread(fd,&filebyte,1,32768)==1&&!filebyte);errno=0;assert(pwrite(fd,&filebyte,1,0)==-1&&errno==EBADF);
 // Recommit/protection changes preserve existing COW data; reset replaces only requested full pages.
 commit(v,16384,32768);assert(((char*)v->base)[32768]==0x5a);assert(!reset_hook(v,(char*)v->base+32768,(char*)v->base+49152));assert(!((char*)v->base)[32768]&&((char*)w->base)[32768]==0x33);assert(v->protect==0&&v->ios_flags==IOS_VIEW_ZERO_FILE);assert(get_page_vprot((char*)v->base+32768)==(VPROT_READ|VPROT_WRITE));
 // Dynamic protection and backing changes refuse file replacement.
 int fm=file_maps;unsigned masks[]={VPROT_EXEC,VPROT_GUARD,VPROT_WRITEWATCH,VPROT_WRITECOPY};for(unsigned i=0;i<4;i++){set_page_vprot(v->base,16384,masks[i]);assert(!ios_zero_file_range(v,v->base,16384));}set_page_vprot(v->base,16384,0);alias_overlap=1;assert(!ios_zero_file_range(v,v->base,16384));alias_overlap=0;swap_overlap=1;assert(!ios_zero_file_range(v,v->base,16384));swap_overlap=0;pool_overlap=1;assert(!ios_zero_file_range(v,v->base,16384));pool_overlap=0;assert(file_maps==fm);
 // Actual production partial-free code preserves surviving COW bytes/flag and removes only released range.
 assert(!free_pages(w,(char*)w->base+(2u<<20),2u<<20));assert(nt==3);struct file_view*right=find_view((char*)w->base+(4u<<20),16384);assert(right&&right!=w&&(right->ios_flags&IOS_VIEW_ZERO_FILE));assert(((char*)w->base)[32768]==0x33);assert(!find_view((char*)w->base+(2u<<20),1));assert(!free_pages(right,right->base,right->size));assert(!free_pages(w,w->base,w->size));assert(!free_pages(v,v->base,v->size));assert(nt==0);
 // Noaccess reservation with partial host-page tail: leave that tail's existing bytes untouched.
 v=newview(0,(1u<<20)+8192);assert(!mprotect((char*)v->base+(1u<<20),16384,PROT_READ|PROT_WRITE));((char*)v->base)[1u<<20]=0x61;assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));assert(((char*)v->base)[1u<<20]==0x61);assert(!free_pages(v,v->base,v->size));
 // Reserve-only READWRITE keeps host NONE and allocation READWRITE until a later real commit.
 assert(get_unix_prot(VPROT_READ|VPROT_WRITE)==PROT_NONE);assert(get_unix_prot(VPROT_READ|VPROT_WRITE|VPROT_COMMITTED)==(PROT_READ|PROT_WRITE));
 v=newview(0,16u<<20);v->protect=VPROT_READ|VPROT_WRITE;set_page_vprot(v->base,v->size,v->protect);assert(!reserve_hook(v,MEM_RESERVE,PAGE_READWRITE,0));assert(v->ios_flags==IOS_VIEW_ZERO_FILE&&v->protect==(VPROT_READ|VPROT_WRITE)&&protection(v->base)==VM_PROT_NONE);assert(get_page_vprot(v->base)==(VPROT_READ|VPROT_WRITE));commit(v,0,65536);assert(!((char*)v->base)[0]);((char*)v->base)[0]=0x39;fm=file_maps;assert(!reserve_hook(v,MEM_COMMIT,PAGE_READWRITE,0));assert(file_maps==fm&&((char*)v->base)[0]==0x39);assert(protection((char*)v->base+65536)==VM_PROT_NONE);assert(!reset_hook(v,v->base,(char*)v->base+16384));assert(!((char*)v->base)[0]&&v->protect==(VPROT_READ|VPROT_WRITE));delete_view(v);
 // Exact fresh COMMIT|RESERVE RW mode keeps committed/private metadata and excludes later recommits.
 v=newview(0,(2u<<20)+8192);commit(v,0,v->size);v->protect=VPROT_READ|VPROT_WRITE|VPROT_COMMITTED;((char*)v->base)[2u<<20]=0x62;
 assert(!reserve_hook(v,MEM_RESERVE|MEM_COMMIT|MEM_TOP_DOWN,PAGE_READWRITE,0));assert(v->ios_flags==IOS_VIEW_ZERO_FILE&&v->protect==(VPROT_READ|VPROT_WRITE|VPROT_COMMITTED));assert(protection(v->base)==(VM_PROT_READ|VM_PROT_WRITE));assert(((char*)v->base)[2u<<20]==0x62);assert(!((char*)v->base)[0]);((char*)v->base)[0]=0x41;fm=file_maps;assert(!reserve_hook(v,MEM_COMMIT,PAGE_READWRITE,0));assert(file_maps==fm&&((char*)v->base)[0]==0x41);assert(!reset_hook(v,v->base,(char*)v->base+16384));assert(!((char*)v->base)[0]);assert(get_page_vprot(v->base)==(VPROT_READ|VPROT_WRITE));delete_view(v);
 v=newview(0,2u<<20);commit(v,0,v->size);v->protect=VPROT_READ|VPROT_WRITE|VPROT_COMMITTED;set_page_vprot(v->base,4096,VPROT_READ|VPROT_COMMITTED);fm=file_maps;assert(!reserve_hook(v,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE,0));assert(file_maps==fm&&!v->ios_flags);delete_view(v);
 for(int both=0;both<2;both++){v=newview(0,2u<<20);commit(v,0,v->size);v->protect=VPROT_READ|VPROT_WRITE|VPROT_COMMITTED;fail_file=1;fail_anon=both;NTSTATUS r=reserve_hook(v,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE,0);if(both){assert(r==STATUS_NO_MEMORY&&nt==0);}else{assert(!r&&protection(v->base)==(VM_PROT_READ|VM_PROT_WRITE)&&!((char*)v->base)[0]);delete_view(v);}fail_file=fail_anon=0;}
 // Every refusal is a no-op; disabled means no file call or bookkeeping bit.
 unsigned types[]={MEM_COMMIT,MEM_RESERVE|MEM_COMMIT,MEM_RESERVE|MEM_WRITE_WATCH};for(unsigned i=0;i<3;i++){v=newview(0,2u<<20);fm=file_maps;assert(!reserve_hook(v,types[i],PAGE_NOACCESS,0));assert(file_maps==fm&&!v->ios_flags);delete_view(v);}
 v=newview(0,2u<<20);fm=file_maps;assert(!reserve_hook(v,MEM_RESERVE,PAGE_READWRITE,0));assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,1));test_window_lo=(uintptr_t)v->base;test_window_hi=test_window_lo+v->size;assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));test_window_lo=test_window_hi=0;ios_zero_file_fd=-1;assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));ios_zero_file_fd=fd;assert(file_maps==fm&&!v->ios_flags);delete_view(v);
 // Bounds, existing commit and foreign view identity refuse without remapping.
 v=newview(0,2u<<20);fm=file_maps;commit(v,0,16384);((char*)v->base)[0]=0x19;assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));assert(file_maps==fm&&!v->ios_flags&&((char*)v->base)[0]==0x19);set_page_vprot(v->base,16384,0);assert(!ios_zero_file_range(v,(char*)v->base+1,16384));assert(!ios_zero_file_range(v,v->base,v->size+16384));struct file_view other=*v;assert(!ios_zero_file_range(&other,other.base,16384));uintptr_t end=ios_fex_arena_end_unix;ios_fex_arena_end_unix=arena+16384;assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));ios_fex_arena_end_unix=end;assert(file_maps==fm);delete_view(v);v=newview(0,(1u<<20)+8192);ios_fex_arena_end_unix=arena+(1u<<20);assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));assert(file_maps==fm);ios_fex_arena_end_unix=end;delete_view(v);
 v=newview(0,512u<<10);fm=file_maps;assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));assert(file_maps==fm);delete_view(v);v=newview(0,(64u<<20)+16384);assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));assert(file_maps==fm);delete_view(v);
 // Failed file mapping falls back to anonymous. Double failure deletes the fresh view and reports failure.
 v=newview(0,2u<<20);fail_file=1;assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));commit(v,0,v->size);assert(!((char*)v->base)[0]);delete_view(v);v=newview(0,2u<<20);fail_anon=1;int d=deleted;assert(reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0)==STATUS_NO_MEMORY);assert(nt==0&&deleted==d+1);fail_file=fail_anon=0;
 v=newview(0,2u<<20);assert(!reserve_hook(v,MEM_RESERVE,PAGE_NOACCESS,0));commit(v,0,v->size);((char*)v->base)[0]=0x7a;fail_file=fail_anon=1;assert(reset_hook(v,v->base,(char*)v->base+16384)==STATUS_NO_MEMORY);assert(((char*)v->base)[0]==0x7a&&get_page_vprot(v->base)&VPROT_COMMITTED);fail_file=fail_anon=0;delete_view(v);
 int logs_before=log_lines;for(unsigned long n=1;n<=10000;n++)zero_log_test(n);assert(log_lines-logs_before==10);
 locked=0;assert(!close(fd));ios_zero_file_fd=-1;fd_count--;assert(fd_count==0);assert(!munmap((void*)arena,arena_size));puts("PASS: production zero backing, actual reserve/reset/split/free hooks, COW/isolation/metadata, guards, default-off and failure cleanup");}
'''
with tempfile.TemporaryDirectory(prefix='zero-file-test-') as d:
 p=Path(d);(p/'test.c').write_text(fixture)
 subprocess.run(['xcrun','clang','-std=c11','-D_DARWIN_C_SOURCE','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)

 # Negative control: hiding a double mapping failure must be detected by the real reserve hook test.
 bad=fixture.replace('return STATUS_NO_MEMORY;', 'return STATUS_SUCCESS;',1)
 assert bad!=fixture
 (p/'negative.c').write_text(bad)
 subprocess.run(['xcrun','clang','-std=c11','-D_DARWIN_C_SOURCE','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'negative.c'),'-o',str(p/'negative')],check=True)
 badrun=subprocess.run([str(p/'negative')],capture_output=True,text=True)
 assert badrun.returncode!=0 and 'STATUS_NO_MEMORY' in badrun.stderr
 print('PASS: negative control catches swallowed mapping/fallback failure')
