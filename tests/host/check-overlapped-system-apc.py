#!/usr/bin/env python3
"""Execute real nonblocking PE polls, native checkpoint and server_select loop.
Only syscall transport, scheduling tail and Windows wait/error APIs are mocked.
"""
from pathlib import Path
import importlib.util, os, subprocess, tempfile
root=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('patch', root/'tools/patch-wine-overlapped-system-apc.py')
patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)
k=patch.patch_kernelbase((root/'wine/dlls/kernelbase/file.c').read_text())
s=patch.patch_sync((root/'wine/dlls/ntdll/unix/sync.c').read_text())
assert patch.patch_kernelbase(k)==k and patch.patch_sync(s)==s
with tempfile.TemporaryDirectory(prefix='bcd-overlapped-build-') as tmp:
    tree=Path(tmp)
    originals={'dlls/kernelbase/file.c':(root/'wine/dlls/kernelbase/file.c').read_text(),
               'dlls/ntdll/unix/sync.c':(root/'wine/dlls/ntdll/unix/sync.c').read_text()}
    for name,text in originals.items():
        path=tree/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    command=[os.environ.get('PYTHON','python3'),str(root/'tools/patch-wine-overlapped-system-apc.py'),str(tree)]
    subprocess.run(command,check=True,capture_output=True)
    assert (tree/'dlls/kernelbase/file.c').read_text()==k
    assert (tree/'dlls/ntdll/unix/sync.c').read_text()==s
    subprocess.run(command,check=True,capture_output=True)
    assert (tree/'dlls/kernelbase/file.c').read_text()==k and (tree/'dlls/ntdll/unix/sync.c').read_text()==s
    print('PASS source-preparation command patches both PE and native sources idempotently')
srv=(root/'build/ntdll-unix/server_ios.c').read_text()
def fn(src, sig):
    a=src.index(sig);return src[a:src.index('\n}\n',a)+3]
def block(src):return src.split('/* overlapped-system-apc:begin',1)[1].split('*/',1)[1].split('/* overlapped-system-apc:end */',1)[0]
pe=block(k);native=block(s)
safety=fn(srv,'int ios_alert_exit_safe(void)')
polls=fn(k,'BOOL WINAPI DECLSPEC_HOTPATCH GetOverlappedResult(')+fn(k,'BOOL WINAPI DECLSPEC_HOTPATCH GetOverlappedResultEx(')
select=fn(srv,'unsigned int server_select( const union select_op *')
yield_body=fn(s,'NTSTATUS WINAPI NtYieldExecution(void)')
assert yield_body.index('ios_yield_system_apcs();')<yield_body.index('IOS_ECO_POLL();')
pre=r'''
#define _POSIX_C_SOURCE 200809L
#define WINE_IOS 1
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <pthread.h>
#include <errno.h>
#include <wchar.h>
#define WINAPI
#define DECLSPEC_HOTPATCH
#define FALSE 0
#define TRUE 1
#define STATUS_SUCCESS 0u
#define STATUS_TIMEOUT 0x102u
#define STATUS_PENDING 0x103u
#define STATUS_USER_APC 0xc0u
#define STATUS_KERNEL_APC 0x100u
#define STATUS_PIPE_BROKEN 0xc000014bu
#define ERROR_IO_INCOMPLETE 996u
#define ERROR_BROKEN_PIPE 109u
#define WAIT_FAILED 0xffffffffu
#define WAIT_TIMEOUT 258u
#define INFINITE 0xffffffffu
#define SELECT_INTERRUPTIBLE 1u
#define SELECT_ALERTABLE 2u
#define SELECT_SIGNAL_AND_WAIT 3u
#define SERVER_CTX_EXEC_SPACE 1u
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define TRACE(...) ((void)0)
static unsigned witnesses;
#define ERR(...) (++witnesses)
typedef uint32_t NTSTATUS,DWORD,UINT,obj_handle_t;
typedef int32_t LONG;
typedef int BOOL;
typedef wchar_t WCHAR;
typedef uintptr_t ULONG_PTR;
typedef void *HANDLE;
typedef DWORD *LPDWORD;
typedef size_t data_size_t;
typedef int64_t timeout_t;
#if TEST_STATUS_BITS == 64
typedef uint64_t status_word;
#else
typedef uint32_t status_word;
#endif
typedef struct { status_word Internal,InternalHigh;HANDLE hEvent; } OVERLAPPED,*LPOVERLAPPED;
static DWORD last_error;
static DWORD GetLastError(void){return last_error;}
static void SetLastError(DWORD e){last_error=e;}
static DWORD GetEnvironmentVariableW(const WCHAR*n,WCHAR*out,DWORD cap){
 assert(!wcscmp(n,L"MADEIRA_OVERLAPPED_SYSTEM_APC"));const char*v=getenv("MADEIRA_OVERLAPPED_SYSTEM_APC");
 last_error=1234;if(!v)return 0;size_t len=strlen(v);if(len>=cap)return len+1;
 for(size_t i=0;i<=len;++i)out[i]=(unsigned char)v[i];return len;
}
#define ReadAcquire(p) __atomic_load_n((p),__ATOMIC_ACQUIRE)
static LONG InterlockedCompareExchange(LONG*p,LONG n,LONG old){__atomic_compare_exchange_n(p,&old,n,0,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);return old;}
static DWORD RtlNtStatusToDosError(NTSTATUS s){return s==STATUS_PIPE_BROKEN?ERROR_BROKEN_PIPE:s;}
union select_op { unsigned op;struct {unsigned op,signal;} signal_and_wait;};
struct user_apc {int value;};
struct context_data {int machine;unsigned flags;};
union apc_call {struct user_apc user;int type;};
union apc_result {int value;};
struct mock_request {UINT flags;uintptr_t cookie;obj_handle_t prev_apc;timeout_t timeout;data_size_t size;};
struct mock_reply {int signaled;obj_handle_t apc_handle;};
static struct mock_reply *current_reply;
static union apc_call *reply_call;
static sigset_t server_block_set;
static unsigned requests,kernel_pending,user_pending,callbacks,parks,yields,waits,events_consumed,sections;
static unsigned reenter,terminal_status,wait_return,wait_alertable;
static NTSTATUS completion_status;
static unsigned completion_info;
static OVERLAPPED *target;
static void ios_tls38_poll(const char*x){(void)x;}
static uintptr_t wine_server_client_ptr(void*p){return(uintptr_t)p;}
static void wine_server_add_data(struct mock_request*r,const void*p,size_t z){(void)r;(void)p;(void)z;}
static void wine_server_set_reply(struct mock_request*r,void*p,size_t z){(void)r;assert(z>=sizeof(*reply_call));reply_call=p;}
static size_t wine_server_reply_size(struct mock_reply*r){(void)r;return sizeof(union apc_call);}
#define SERVER_START_REQ(name) {struct mock_request request={0};struct mock_reply response={0};struct mock_request *req=&request;struct mock_reply *reply=&response;current_reply=reply;
#define SERVER_END_REQ }
static unsigned server_call_unlocked(struct mock_request*r){
 ++requests;assert(requests<40);assert(r->flags==SELECT_INTERRUPTIBLE);assert(r->timeout==0&&!r->size);
 current_reply->signaled=1;errno=EIO;
 if(kernel_pending){--kernel_pending;reply_call->type=1;return STATUS_KERNEL_APC;}
 if(user_pending&&(r->flags&SELECT_ALERTABLE)){--user_pending;assert(!"must not dequeue user APC");}
 return terminal_status?terminal_status:STATUS_TIMEOUT;
}
static unsigned wait_select_reply(int*c){(void)c;++parks;assert(!"zero timeout must not park");return STATUS_TIMEOUT;}
static NTSTATUS NtYieldExecution(void);
static void invoke_system_apc(const union apc_call*c,union apc_result*r,BOOL self){
 (void)c;(void)r;assert(!self);++callbacks;sigset_t mask;pthread_sigmask(SIG_SETMASK,NULL,&mask);
 assert(sigismember(&mask,SIGUSR1)&&sigismember(&mask,SIGUSR2));
 if(reenter){unsigned before=requests;NtYieldExecution();assert(before==requests);}
 target->InternalHigh=completion_info;__atomic_store_n(&target->Internal,completion_status,__ATOMIC_RELEASE);
 errno=EBADF;
}
#define IOS_SECTION_DEPTH 0x0fffffffu
#define IOS_SECTION_ABORT 0x40000000u
#define IOS_SECTION_EXITING 0x20000000u
static int ios_section_key_ready=1;
static uintptr_t ios_section_state(void){return sections;}
static unsigned int server_select(const union select_op*,data_size_t,UINT,timeout_t,struct context_data*,struct user_apc*);
static DWORD WaitForSingleObjectEx(HANDLE h,DWORD timeout,BOOL alertable){
 (void)h;assert(timeout);++waits;wait_alertable=alertable;
 if(!wait_return){target->Internal=completion_status;target->InternalHigh=completion_info;++events_consumed;}
 if(wait_return==WAIT_FAILED)SetLastError(555);return wait_return;
}
static DWORD WaitForSingleObject(HANDLE h,DWORD timeout){return WaitForSingleObjectEx(h,timeout,FALSE);}
'''
bridge=r'''
static NTSTATUS NtYieldExecution(void){++yields;ios_yield_system_apcs();return 0x40000024;}
'''
main=r'''
static void reset(OVERLAPPED*ov){
 requests=kernel_pending=user_pending=callbacks=parks=yields=waits=events_consumed=sections=reenter=terminal_status=wait_return=wait_alertable=0;
 completion_status=STATUS_SUCCESS;completion_info=82;target=ov;ov->Internal=STATUS_PENDING;ov->InternalHigh=0;ov->hEvent=(HANDLE)0x74;
 errno=EDOM;last_error=777;
}
static void masks(void){sigset_t m;pthread_sigmask(SIG_SETMASK,NULL,&m);assert(!sigismember(&m,SIGUSR1)&&sigismember(&m,SIGUSR2));}
int main(int argc,char**argv){
 assert(argc==2);if(strcmp(argv[1],"unset"))setenv("MADEIRA_OVERLAPPED_SYSTEM_APC",argv[1],1);
 sigemptyset(&server_block_set);sigaddset(&server_block_set,SIGUSR1);sigset_t initial;sigemptyset(&initial);sigaddset(&initial,SIGUSR2);pthread_sigmask(SIG_SETMASK,&initial,NULL);
 OVERLAPPED ov;DWORD result=999;reset(&ov);
 BOOL enabled=!strcmp(argv[1],"1");kernel_pending=user_pending=1;
 BOOL ok=GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE);
 if(!enabled){assert(!ok&&last_error==ERROR_IO_INCOMPLETE&&result==999&&!yields&&!requests&&!witnesses&&kernel_pending==1);
   assert(!GetOverlappedResultEx((HANDLE)0x70,&ov,&result,0,TRUE));assert(!yields&&!requests);
   NtYieldExecution();assert(!requests&&kernel_pending==1&&errno==EDOM);puts("PASS disabled/malformed PE and native exact no-roundtrip");return 0;}
 assert(ok&&result==82&&last_error==0&&requests==2&&callbacks==1&&user_pending==1&&!events_consumed&&!waits&&!parks&&errno==EDOM&&witnesses==1);masks();
 reset(&ov);kernel_pending=user_pending=1;completion_status=STATUS_PIPE_BROKEN;completion_info=0;result=999;
 assert(!GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(last_error==ERROR_BROKEN_PIPE&&result==0&&callbacks==1&&user_pending==1&&witnesses==1);masks();
 reset(&ov);result=999;assert(!GetOverlappedResultEx((HANDLE)0x70,&ov,&result,0,TRUE));assert(last_error==ERROR_IO_INCOMPLETE&&result==999&&requests==1&&!waits&&!events_consumed);masks();
 reset(&ov);kernel_pending=user_pending=1;assert(GetOverlappedResultEx((HANDLE)0x70,&ov,&result,0,TRUE));assert(result==82&&callbacks==1&&user_pending==1&&!waits&&!events_consumed);
 reset(&ov);kernel_pending=1;sections=1;result=999;assert(!GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(!requests&&kernel_pending==1&&result==999);sections=0;assert(GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(callbacks==1);
 reset(&ov);kernel_pending=1;sections=IOS_SECTION_ABORT;assert(!GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(!requests);sections=IOS_SECTION_EXITING;assert(!GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(!requests);sections=0;ios_section_key_ready=0;assert(!GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(!requests);ios_section_key_ready=1;
 reset(&ov);kernel_pending=1;reenter=1;assert(GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(requests==2&&yields==2&&callbacks==1&&errno==EDOM);masks();
 reset(&ov);terminal_status=0xc0000120;assert(!GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(ov.Internal==STATUS_PENDING&&last_error==ERROR_IO_INCOMPLETE&&errno==EDOM);masks();
 reset(&ov);ov.Internal=STATUS_SUCCESS;ov.InternalHigh=17;assert(GetOverlappedResult((HANDLE)0x70,&ov,&result,FALSE));assert(result==17&&!yields&&!requests);
 reset(&ov);assert(GetOverlappedResult((HANDLE)0x70,&ov,&result,TRUE));assert(waits==1&&!yields&&!requests&&events_consumed==1);
 reset(&ov);assert(GetOverlappedResultEx((HANDLE)0x70,&ov,&result,10,TRUE));assert(waits==1&&wait_alertable&&!yields&&!requests);
 reset(&ov);wait_return=WAIT_FAILED;assert(!GetOverlappedResult((HANDLE)0x70,&ov,&result,TRUE));assert(last_error==555&&!yields);
 reset(&ov);wait_return=WAIT_TIMEOUT;assert(!GetOverlappedResultEx((HANDLE)0x70,&ov,&result,10,FALSE));assert(last_error==WAIT_TIMEOUT&&!yields);
 reset(&ov);kernel_pending=1;assert(madeira_overlapped_poll(&ov,STATUS_PENDING)==0);assert(last_error==777&&errno==EDOM);
 puts("PASS real pending publication, terminal error, Ex zero, user APC/event isolation, section guard, recursion, masks, errno/LastError, original blocking/already-complete paths");
}
'''
with tempfile.TemporaryDirectory(prefix='bcd-overlapped-apc-') as tmp:
    p=Path(tmp);c=p/'test.c';exe=p/'test';text=pre+safety+native+select+bridge+pe+polls+main;c.write_text(text)
    flags=[os.environ.get('CC','clang'),'-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined']
    for bits in (32,64):
        c.write_text(text)
        architecture_flags=flags+['-DTEST_STATUS_BITS='+str(bits)]
        subprocess.run(architecture_flags+[str(c),'-o',str(exe)],check=True)
        for mode in ['unset','','0','11','true','1']:
            z=subprocess.run([str(exe),mode],capture_output=True,text=True,check=True);print(str(bits)+'-bit status fields: '+z.stdout.strip())
        for name,bad in [('no-checkpoint',text.replace('        NtYieldExecution();\n        status = ReadAcquire','        /* removed */\n        status = ReadAcquire',1)),('no-recursion-guard',text.replace(' && !active && ios_alert_exit_safe()', ' && ((void)active, 1) && ios_alert_exit_safe()',1))]:
            c.write_text(bad);subprocess.run(architecture_flags+[str(c),'-o',str(exe)],check=True)
            z=subprocess.run([str(exe),'1'],capture_output=True,text=True);assert z.returncode!=0,name
            print('PASS negative control detected',bits,name)
