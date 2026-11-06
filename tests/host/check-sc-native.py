#!/usr/bin/env python3
"""Native file access for madeira-d3d12's shader cache (shader-cache-native,
on by default; tools/patch-winemetal-sc-native.py); no device, no Wine.

madeira-d3d12 reads and writes its shader cache entries,
%LOCALAPPDATA%\\BCD\\ShaderCache\\<build>\\<bb>\\<key>.msc, through
winemetal (MadeiraCtl ops 20-22): openat/pread relative to an open descriptor
of the build directory, and the same temporary file renamed over the entry,
instead of Wine's file API (a create_file, get_handle_fd and close_handle
request and a server realpath() per entry). The files, names and contents are
the same either way. Checks:

1. The patch applies to winemetal_unix.c after the patches that run before it
   in build-ipa.yml (GPU fault info: ops 8 and 9; pso-warm: op 10), once
   (a second run says "already applied"), with no case value twice in
   _madeira_ctl, and stops by name when an anchor moved or op 20 is taken;
   the i386 entry still forwards op 7 only.
2. The patch's own code, compiled on this host (AddressSanitizer and
   UndefinedBehaviorSanitizer; the concurrent parts again under
   ThreadSanitizer): open (one handle per directory), put/get round trips in
   the loose layout (bucket directories made on demand), a short buffer gets
   the length, presence without reading, a missing entry is 0 and an
   unreadable one 3; a file another writer (Wine) placed is read byte for
   byte; 8 threads writing and reading the same entries never see a partial
   file; a writer killed at random points (SIGKILL) leaves every entry absent
   or whole.
3. madeira_d3d12.c's whole shader-cache section (mad_sc_* through
   mad_ir_convert_cached_impl), compiled against a POSIX stand-in for Wine's
   file API (each call counted) and a deterministic stand-in converter,
   linked with (2), run as separate launches over one cache directory:
   switch off and on (and the default, which is on) give the same results
   for every stage, cold and warm, in one-pass and size-then-fill mode,
   including refused stages and metallibs longer than the first buffer; a
   cold cache written natively is the same set of files, byte for byte, as
   one written through Wine; each way reads the other's files; with the
   switch on, a warm launch makes no Wine file call at all; without
   wine_get_unix_file_name or without the ops everything goes through Wine as
   before; 8 threads converting the same stages at once agree with one; the
   pso-lazy-libs key is reused only for the same arguments and inputs, and an
   entry just read or written is known to be there without a call.
4. Source checks: the switch defaults to 1 and each native branch sits in
   front of the unchanged Wine code; build-ipa.yml runs the patch and this
   check; the settings catalog lists the key.
Needs python3 and a C compiler (clang or gcc).
"""
from pathlib import Path
import filecmp
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("dxmt_base", __import__("pathlib").Path(__file__).resolve().parent / "dxmt_base.py")
dxmt_base = _ilu.module_from_spec(_spec); _spec.loader.exec_module(dxmt_base)

root = Path(__file__).resolve().parents[2]
patch_py = root / 'tools/patch-winemetal-sc-native.py'
before_patches = [root / 'tools/patch-dxmt-gpu-fault-info.py', root / 'tools/patch-winemetal-pso-warm.py']
wm_src = root / 'dxmt/src/winemetal/unix/winemetal_unix.c'
pe = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
cc = os.environ.get('CC') or shutil.which('clang') or shutil.which('cc') or shutil.which('gcc')
failures = []


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        failures.append(what)


def body(text, signature):
    i = text.index(signature)
    while text.find(';', i) < text.index('{', i):   # a prototype: the definition comes later
        i = text.index(signature, i + 1)
    j = text.index('{', i)
    depth = 0
    for k in range(j, len(text)):
        depth += {'{': 1, '}': -1}.get(text[k], 0)
        if depth == 0:
            return text[i:k + 1]
    raise SystemExit('unbalanced ' + signature)


spec = importlib.util.spec_from_file_location('sc_native_patch', patch_py)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

# ---------------------------------------------------------------------------
# 1. the patch on a copy of winemetal_unix.c
# ---------------------------------------------------------------------------
def run_patch(tree, script):
    return subprocess.run([sys.executable, str(script)], cwd=tree, capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix='madeira-sc-native-') as d:
    tree = Path(d)
    target = tree / 'dxmt/src/winemetal/unix/winemetal_unix.c'
    target.parent.mkdir(parents=True)
    # CI runs this check after every winemetal patch step: start from the file as committed
    committed = subprocess.run(['git', '-C', str(root / 'dxmt'), 'show',
                                dxmt_base.rev(root / 'dxmt') + ':src/winemetal/unix/winemetal_unix.c'],
                               capture_output=True, text=True)
    if committed.returncode == 0 and committed.stdout:
        target.write_text(committed.stdout)
    else:
        shutil.copy(wm_src, target)
    pristine = target.read_text()
    for p in before_patches:
        r = run_patch(tree, p)
        check(r.returncode == 0, f'{p.name} applies first (as in build-ipa.yml)')
    first = run_patch(tree, patch_py)
    second = run_patch(tree, patch_py)
    check(first.returncode == 0 and second.returncode == 0 and 'already applied' in second.stdout,
          'the patch applies once and a second run says "already applied"')
    patched = target.read_text()
    ctl = body(patched, 'static NTSTATUS _madeira_ctl(void *args) {')
    cases = re.findall(r'case (\d+):', ctl)
    check(all(str(op) in cases for op in (20, 21, 22)) and 'a->ret = madeira_scn_ctl(a);' in ctl
          and ctl.index('case 20:') < ctl.index('default: break;'),
          'ops 20-22 are in _madeira_ctl, before its default')
    check(len(cases) == len(set(cases)), f'no case value twice in _madeira_ctl ({sorted(set(cases), key=int)})')
    wow = body(patched, 'static NTSTATUS _madeira_ctl_wow64(void *args) {')
    check('a->op == 7' in wow and 'madeira_scn' not in wow, 'the i386 entry still forwards op 7 only')
    check(patched.index(patch.HELPERS) < patched.index('static NTSTATUS _madeira_ctl(void *args) {'),
          'the helpers come before _madeira_ctl')
    # stops by name when an anchor moved or an op is taken
    target.write_text(pristine.replace('  default: break;\n  }\n  return STATUS_SUCCESS;\n}\n',
                                       '  default:\n    break;\n  }\n  return STATUS_SUCCESS;\n}\n', 1))
    moved = run_patch(tree, patch_py)
    target.write_text(pristine.replace('  case 6:     /* ml1136', '  case 21:\n  case 6:     /* ml1136', 1))
    taken = run_patch(tree, patch_py)
    check(moved.returncode != 0 and 'moved' in moved.stdout + moved.stderr and
          taken.returncode != 0 and 'case 21' in taken.stdout + taken.stderr,
          'a moved anchor or a taken op stops the patch by name')

# ---------------------------------------------------------------------------
# 2 + 3. the patch's code and madeira_d3d12.c's shader cache, on this host
# ---------------------------------------------------------------------------
pe_slice = pe[pe.index('#define MAD_SC_MAGIC'):pe.index('/* madeira-bcd: SHARED METAL LIBRARIES.')]
# Build 632 (pso-archive, tests/host/check-pso-archive.py): mad_pso_archive_init sits between mad_scn_init and mad_sc_init and is called
# from the same `if (on)` line; this harness is about the shader cache, so its body (Win32 file and module calls it has no shims for) is
# replaced by an empty function here. The archive's own checks are in check-pso-archive.py.
_arch_a, _arch_b = pe_slice.find('/* PIPELINE ARCHIVE (bcd.cfg'), pe_slice.find('static int mad_sc_init(void) {')
if _arch_a >= 0 and _arch_b > _arch_a:
    pe_slice = pe_slice[:_arch_a] + 'static void mad_pso_archive_init(void) {}\n' + pe_slice[_arch_b:]
check('static int mad_scn_has(const UINT64 key[2]);' in pe[:pe.index('#define MAD_SC_MAGIC')] or
      'static int mad_scn_has(const UINT64 key[2]);' in pe_slice, 'the cut includes the native helpers')

HARNESS = r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <wchar.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include "madeira_ir_abi.h"

typedef int NTSTATUS;
#define STATUS_SUCCESS 0
struct madeira_ctl_args { uint32_t op; uint32_t ret; uint64_t ptr; uint64_t len; char name[160]; };

/* ===== winemetal's side: the patch's code ===== */
@HELPERS@
static void wm_ctl(struct madeira_ctl_args *a) {   /* the patched _madeira_ctl, ops 20-22 */
  a->ret = 0;
  switch (a->op) {
@CASES@
  default: break;
  }
}

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d: ", __func__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* ===== madeira-d3d12's side: Windows on POSIX, each file call counted ===== */
typedef uint64_t UINT64; typedef uint32_t UINT32; typedef unsigned UINT; typedef int64_t LONG64, LONGLONG;
typedef long LONG; typedef unsigned long ULONG, DWORD; typedef int BOOL; typedef size_t SIZE_T; typedef uintptr_t ULONG_PTR;
typedef wchar_t WCHAR; typedef const WCHAR *LPCWSTR; typedef void *HANDLE; typedef void *HMODULE;
typedef union { struct { uint32_t LowPart; int32_t HighPart; } u; int64_t QuadPart; } LARGE_INTEGER;
#define TRUE 1
#define FALSE 0
#define CDECL
#define WINAPI
#define MAX_PATH 260
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define INVALID_FILE_ATTRIBUTES ((DWORD)0xffffffffu)
#define FILE_ATTRIBUTE_DIRECTORY 0x10
#define GENERIC_READ 0x80000000u
#define GENERIC_WRITE 0x40000000u
#define FILE_SHARE_READ 1
#define FILE_SHARE_DELETE 4
#define CREATE_ALWAYS 2
#define OPEN_EXISTING 3
#define MOVEFILE_REPLACE_EXISTING 1
#define ERROR_ALREADY_EXISTS 183
#define TLS_OUT_OF_INDEXES ((DWORD)0xffffffffu)
#define MAD_LOC_MAX 64
#define MAD_SC_CONVERTER_ID "host-test"
#define _snwprintf swprintf
typedef pthread_rwlock_t SRWLOCK;
#define SRWLOCK_INIT PTHREAD_RWLOCK_INITIALIZER
static void AcquireSRWLockExclusive(SRWLOCK *l) { pthread_rwlock_wrlock(l); }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { pthread_rwlock_unlock(l); }
static void AcquireSRWLockShared(SRWLOCK *l) { pthread_rwlock_rdlock(l); }
static void ReleaseSRWLockShared(SRWLOCK *l) { pthread_rwlock_unlock(l); }
static LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG InterlockedOr(volatile LONG *p, LONG v) { return __atomic_fetch_or(p, v, __ATOMIC_SEQ_CST); }
static LONG InterlockedAnd(volatile LONG *p, LONG v) { return __atomic_fetch_and(p, v, __ATOMIC_SEQ_CST); }
static LONG InterlockedCompareExchange(volatile LONG *p, LONG x, LONG c) {
    __atomic_compare_exchange_n(p, &c, x, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); return c; }
static LONG64 InterlockedExchangeAdd64(volatile LONG64 *p, LONG64 v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }
#define MemoryBarrier() __atomic_thread_fence(__ATOMIC_SEQ_CST)
static DWORD TlsAlloc(void) { pthread_key_t k; return pthread_key_create(&k, NULL) ? TLS_OUT_OF_INDEXES : (DWORD)k; }
static BOOL TlsFree(DWORD i) { return !pthread_key_delete((pthread_key_t)i); }
static void *TlsGetValue(DWORD i) { return pthread_getspecific((pthread_key_t)i); }
static BOOL TlsSetValue(DWORD i, void *v) { return !pthread_setspecific((pthread_key_t)i, v); }
static __thread DWORD last_error, thread_id;
static _Atomic DWORD thread_ids;
static DWORD GetLastError(void) { return last_error; }
static DWORD GetCurrentThreadId(void) { if (!thread_id) thread_id = atomic_fetch_add(&thread_ids, 1) + 1; return thread_id; }

/* Wine's file API on POSIX. C: is a host directory. */
static char g_root[600];
static _Atomic long w_create, w_size, w_read, w_write, w_close, w_attr, w_move, w_delete, w_mkdir;
static int to_host(LPCWSTR w, char *out, size_t cap) {
    char n[1200];
    size_t k = wcstombs(n, w, sizeof n);
    if (k == (size_t)-1 || k >= sizeof n || !((n[0] == 'C' || n[0] == 'c') && n[1] == ':')) return 0;
    if (snprintf(out, cap, "%s%s", g_root, n + 2) >= (int)cap) return 0;
    for (char *p = out; *p; p++) if (*p == '\\') *p = '/';
    return 1;
}
struct wfile { int magic, fd; };
#define WFILE_MAGIC 0x57464c45
static HANDLE CreateFileW(LPCWSTR path, DWORD access, DWORD share, void *sa, DWORD disp, DWORD flags, HANDLE tmpl) {
    char p[1300]; int fd; struct stat st; struct wfile *f;
    (void)access; (void)share; (void)sa; (void)flags; (void)tmpl;
    atomic_fetch_add(&w_create, 1);
    if (!to_host(path, p, sizeof p)) { last_error = 3; return INVALID_HANDLE_VALUE; }
    fd = disp == OPEN_EXISTING ? open(p, O_RDONLY | O_CLOEXEC) : open(p, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0666);
    if (fd < 0) { last_error = errno == ENOENT ? 2 : 5; return INVALID_HANDLE_VALUE; }
    if (!fstat(fd, &st) && S_ISDIR(st.st_mode)) { close(fd); last_error = 5; return INVALID_HANDLE_VALUE; }   /* no backup semantics */
    f = malloc(sizeof *f); f->magic = WFILE_MAGIC; f->fd = fd;
    return f;
}
static BOOL GetFileSizeEx(HANDLE h, LARGE_INTEGER *sz) {
    struct wfile *f = h; struct stat st;
    atomic_fetch_add(&w_size, 1);
    if (fstat(f->fd, &st)) return FALSE;
    sz->QuadPart = st.st_size;
    return TRUE;
}
static BOOL ReadFile(HANDLE h, void *buf, DWORD n, DWORD *got, void *ov) {
    struct wfile *f = h; size_t off = 0; (void)ov;
    atomic_fetch_add(&w_read, 1);
    while (off < n) { ssize_t r = read(f->fd, (char *)buf + off, n - off); if (r <= 0) break; off += (size_t)r; }
    *got = (DWORD)off;
    return TRUE;
}
static BOOL WriteFile(HANDLE h, const void *buf, DWORD n, DWORD *put, void *ov) {
    struct wfile *f = h; size_t off = 0; (void)ov;
    atomic_fetch_add(&w_write, 1);
    while (off < n) { ssize_t r = write(f->fd, (const char *)buf + off, n - off); if (r <= 0) break; off += (size_t)r; }
    *put = (DWORD)off;
    return off == n;
}
#define THREAD_HANDLE ((HANDLE)(intptr_t)0x7e57)
static BOOL CloseHandle(HANDLE h) {
    struct wfile *f = h;
    if (h == THREAD_HANDLE) return TRUE;
    atomic_fetch_add(&w_close, 1);
    if (!f || f->magic != WFILE_MAGIC) { fails++; printf("FAIL: CloseHandle of a bad handle\n"); return FALSE; }
    close(f->fd); f->magic = 0; free(f);
    return TRUE;
}
static BOOL MoveFileExW(LPCWSTR from, LPCWSTR to, DWORD flags) {
    char a[1300], b[1300]; (void)flags;
    atomic_fetch_add(&w_move, 1);
    return to_host(from, a, sizeof a) && to_host(to, b, sizeof b) && !rename(a, b);
}
static BOOL DeleteFileW(LPCWSTR path) { char p[1300]; atomic_fetch_add(&w_delete, 1); return to_host(path, p, sizeof p) && !unlink(p); }
static BOOL RemoveDirectoryW(LPCWSTR path) { char p[1300]; return to_host(path, p, sizeof p) && !rmdir(p); }
static BOOL CreateDirectoryW(LPCWSTR path, void *sa) {
    char p[1300]; (void)sa;
    atomic_fetch_add(&w_mkdir, 1);
    if (!to_host(path, p, sizeof p)) { last_error = 3; return FALSE; }
    if (!mkdir(p, 0777)) return TRUE;
    last_error = errno == EEXIST ? ERROR_ALREADY_EXISTS : 3;
    return FALSE;
}
static DWORD GetFileAttributesW(LPCWSTR path) {
    char p[1300]; struct stat st;
    atomic_fetch_add(&w_attr, 1);
    if (!to_host(path, p, sizeof p) || stat(p, &st)) return INVALID_FILE_ATTRIBUTES;
    return S_ISDIR(st.st_mode) ? FILE_ATTRIBUTE_DIRECTORY : 0x80;
}
typedef struct { DWORD dwFileAttributes; WCHAR cFileName[MAX_PATH]; } WIN32_FIND_DATAW;
struct wfind { DIR *d; char dir[1300]; };
static BOOL find_fill(struct wfind *f, WIN32_FIND_DATAW *fd) {
    struct dirent *e; char p[1600]; struct stat st;
    if (!(e = readdir(f->d))) return FALSE;
    snprintf(p, sizeof p, "%s/%s", f->dir, e->d_name);
    fd->dwFileAttributes = (!stat(p, &st) && S_ISDIR(st.st_mode)) ? FILE_ATTRIBUTE_DIRECTORY : 0x80;
    mbstowcs(fd->cFileName, e->d_name, MAX_PATH);
    return TRUE;
}
static HANDLE FindFirstFileW(LPCWSTR pattern, WIN32_FIND_DATAW *fd) {
    struct wfind *f = calloc(1, sizeof *f); size_t n;
    if (!to_host(pattern, f->dir, sizeof f->dir) || (n = strlen(f->dir)) < 2 || strcmp(f->dir + n - 2, "/*")) { free(f); return INVALID_HANDLE_VALUE; }
    f->dir[n - 2] = 0;
    if (!(f->d = opendir(f->dir))) { free(f); return INVALID_HANDLE_VALUE; }
    if (!find_fill(f, fd)) { closedir(f->d); free(f); return INVALID_HANDLE_VALUE; }
    return f;
}
static BOOL FindNextFileW(HANDLE h, WIN32_FIND_DATAW *fd) { return find_fill(h, fd); }
static BOOL FindClose(HANDLE h) { struct wfind *f = h; closedir(f->d); free(f); return TRUE; }
static const wchar_t *g_localappdata = L"C:\\users\\t\\AppData\\Local";
static DWORD GetEnvironmentVariableW(LPCWSTR name, WCHAR *buf, DWORD n) {
    size_t l = wcslen(g_localappdata);
    if (wcscmp(name, L"LOCALAPPDATA") || l >= n) return 0;
    wcscpy(buf, g_localappdata);
    return (DWORD)l;
}
static DWORD GetEnvironmentVariableA(const char *name, char *buf, DWORD n) { (void)name; (void)buf; (void)n; return 0; }
typedef DWORD (*thread_fn)(void *);
struct thr { thread_fn fn; void *arg; };
static void *thr_main(void *p) { struct thr t = *(struct thr *)p; free(p); t.fn(t.arg); return NULL; }
static HANDLE CreateThread(void *sa, size_t st, thread_fn fn, void *arg, DWORD fl, DWORD *id) {
    pthread_t th; struct thr *t = malloc(sizeof *t); (void)sa; (void)st; (void)fl; (void)id;
    t->fn = fn; t->arg = arg;
    if (pthread_create(&th, NULL, thr_main, t)) { free(t); return NULL; }
    pthread_detach(th);
    return THREAD_HANDLE;
}
/* kernel32: wine_get_unix_file_name, or none */
static int g_no_unix_name, g_unix_name_calls;
static char *fake_unix_name(LPCWSTR w) { char p[1300]; g_unix_name_calls++; return to_host(w, p, sizeof p) ? strdup(p) : NULL; }
static HMODULE GetModuleHandleW(LPCWSTR name) { return !wcscmp(name, L"kernel32.dll") ? (HMODULE)(intptr_t)1 : NULL; }
static void *GetProcAddress(HMODULE h, const char *name) {
    return h && !g_no_unix_name && !strcmp(name, "wine_get_unix_file_name") ? (void *)fake_unix_name : NULL; }
static HANDLE GetProcessHeap(void) { return (HANDLE)(intptr_t)1; }
static BOOL HeapFree(HANDLE heap, DWORD fl, void *p) { (void)heap; (void)fl; free(p); return TRUE; }

/* madeira-d3d12's own helpers the section uses */
static pthread_mutex_t log_mu = PTHREAD_MUTEX_INITIALIZER;
static char g_log[1 << 16]; static size_t g_log_n;
static void d3d12_log(const char *fmt, ...) {
    char b[1000]; va_list ap; size_t n;
    va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    n = strlen(b);
    pthread_mutex_lock(&log_mu);
    if (g_log_n + n + 1 < sizeof g_log) { memcpy(g_log + g_log_n, b, n + 1); g_log_n += n; }
    pthread_mutex_unlock(&log_mu);
    fputs(b, stderr);
}
static char g_cfg[256];   /* "key=value,..." */
static long long mad_cfg_int_pe(const char *key, long long dflt) {
    const char *p = g_cfg; size_t kl = strlen(key);
    while (p && *p) {
        if (!strncmp(p, key, kl) && p[kl] == '=') return strtoll(p + kl + 1, NULL, 0);
        p = strchr(p, ','); if (p) p++;
    }
    return dflt;
}
static int mad_cfg_str_pe(const char *key, char *out, size_t cap) { (void)key; (void)out; (void)cap; return 0; }
static volatile LONG64 g_pt_conv;
static LONG64 mad_qpc(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (LONG64)t.tv_sec * 1000000000 + t.tv_nsec; }
/* winemetal.dll as madeira-d3d12 imports it */
static int g_no_ops;
static _Atomic long ctl_calls[32];
static void MadeiraCtl(struct madeira_ctl_args *a) {
    if (a->op < 32) atomic_fetch_add(&ctl_calls[a->op], 1);
    if (g_no_ops) { a->ret = 0; return; }   /* a winemetal without the patch */
    wm_ctl(a);
}
/* the converter: outputs a deterministic function of every input the key covers */
static _Atomic long conversions, convert_calls;
static uint64_t fnv(uint64_t h, const void *p, size_t n) {
    const unsigned char *c = p; for (size_t i = 0; i < n; i++) { h ^= c[i]; h *= 0x100000001b3ull; } return h; }
static void MadeiraIRConvert(void *arg) {
    struct madeira_ir_convert_args *a = arg;
    const struct madeira_ir_input_layout *lay = (const void *)(uintptr_t)a->layout;
    const unsigned char *dx = (const void *)(uintptr_t)a->dxil;
    uint64_t h = 0xcbf29ce484222325ull, len, i;
    atomic_fetch_add(&convert_calls, 1);
    h = fnv(h, dx, (size_t)a->dxil_len);
    if (a->vs_bytecode) h = fnv(h, (const void *)(uintptr_t)a->vs_bytecode, (size_t)a->vs_bytecode_len);
    if (lay) h = fnv(h, lay, sizeof *lay);
    if (a->params) h = fnv(h, (const void *)(uintptr_t)a->params, (size_t)a->num_params * sizeof(struct madeira_ir_root_param));
    h = fnv(h, &a->ps_flags, sizeof a->ps_flags);
    memset(a->ret_note, 0, sizeof a->ret_note);
    a->ret_stage = (uint32_t)(h & 7); a->ret_backend = (uint32_t)((h >> 3) & 1); a->ret_error_code = 0;
    if (a->dxil_len && dx[0] == 0xee) {   /* this one the converter refuses */
        a->ret_status = MADEIRA_IR_COMPILE_FAILED; a->ret_error_code = 4; a->ret_len = 0;
        snprintf(a->ret_note, sizeof a->ret_note, "refused %08x", (unsigned)h);
        return;
    }
    len = 96 + (h % 4096) + (dx[1] == 0xbb ? a->dxil_len * 4 + 70000 : 0);   /* some outgrow the first buffer */
    a->ret_len = len;
    a->ret_vs_input_count = 3; a->ret_loc_count = 5;
    a->ret_tg_size[0] = 8; a->ret_tg_size[1] = (uint32_t)(h >> 60) + 1; a->ret_tg_size[2] = 1;
    a->ret_vs_output_size = (uint32_t)(h >> 40) & 0xff; a->ret_arg_qwords = (uint32_t)(h >> 20) & 0x3f;
    a->ret_cb_table_bind = 2; a->ret_arg_table_bind = 3; a->ret_air_slot_mask = (uint32_t)h;
    a->ret_air_nranges = a->out_air_ranges ? 2 : 0;
    snprintf(a->ret_note, sizeof a->ret_note, "note %08x", (unsigned)(h >> 8));
    if (!a->out_buf || a->out_cap < len) { a->ret_status = MADEIRA_IR_BUFFER_TOO_SMALL; return; }
    for (i = 0; i < len; i++) ((unsigned char *)(uintptr_t)a->out_buf)[i] = (unsigned char)(h >> ((i % 8) * 8)) ^ (unsigned char)i;
    if (a->out_entry) snprintf((char *)(uintptr_t)a->out_entry, MADEIRA_IR_ENTRY_MAX, "fn_%016llx", (unsigned long long)h);
    if (a->out_vs_inputs)
        for (i = 0; i < 3 && i < a->vs_input_cap; i++) {
            struct madeira_ir_vs_input *v = (struct madeira_ir_vs_input *)(uintptr_t)a->out_vs_inputs + i;
            memset(v, 0, sizeof *v); snprintf(v->name, sizeof v->name, "ATTR%u", (unsigned)i); v->attribute = (uint32_t)(i + (h & 3));
        }
    if (a->out_locs)
        for (i = 0; i < 5 && i < a->loc_cap; i++) {
            struct madeira_ir_loc *l = (struct madeira_ir_loc *)(uintptr_t)a->out_locs + i;
            memset(l, 0, sizeof *l); l->type = (uint32_t)i; l->space = (uint32_t)(h >> 16) & 7; l->slot = (uint32_t)i * 3;
        }
    if (a->out_air_ranges)
        for (i = 0; i < 2 && i < a->air_range_cap; i++) {
            unsigned char *r = (unsigned char *)(uintptr_t)a->out_air_ranges + i * sizeof(struct madeira_ir_air_range);
            memset(r, (int)(h >> (i * 8)) & 0xff, sizeof(struct madeira_ir_air_range));
        }
    a->ret_len2 = 0;
    if (lay && a->out_buf2 && a->out_cap2 >= 200) {
        a->ret_len2 = 100 + (h % 100);
        memset((void *)(uintptr_t)a->out_buf2, (int)(h & 0xff), (size_t)a->ret_len2);
    }
    a->ret_status = MADEIRA_IR_OK;
    atomic_fetch_add(&conversions, 1);
}

/* ===== madeira_d3d12.c, the shader-cache section as it is ===== */
@PE_SLICE@

/* ===== the driver ===== */
#define NST 120
struct stage { unsigned char *dxil; size_t n; int kind, air; };
static struct stage S[NST];
static struct madeira_ir_root_param RP[3];
static struct madeira_ir_root_range RR[4];
static struct madeira_ir_input_layout LAY[3];
static uint64_t xs(uint64_t *s) { *s ^= *s << 13; *s ^= *s >> 7; *s ^= *s << 17; return *s; }
static void make_stages(void) {
    uint64_t s = 0x1234567;
    for (int i = 0; i < 3; i++) { memset(&RP[i], 0, sizeof RP[i]); RP[i].type = (uint32_t)i; RP[i].shader_register = (uint32_t)i + 1; }
    for (int i = 0; i < 4; i++) { memset(&RR[i], 0, sizeof RR[i]); RR[i].range_type = (uint32_t)i; RR[i].num_descriptors = 8u << i; }
    for (int l = 0; l < 3; l++) {
        memset(&LAY[l], 0, sizeof LAY[l]); LAY[l].n = (uint32_t)l + 1;
        for (int e = 0; e <= l; e++) { snprintf(LAY[l].el[e].semantic, sizeof LAY[l].el[e].semantic, "TEXCOORD"); LAY[l].el[e].semantic_index = (uint32_t)e; LAY[l].el[e].format = 2u + (uint32_t)l; }
    }
    for (int i = 0; i < NST; i++) {
        S[i].n = 64 + (size_t)(xs(&s) % 20000);
        S[i].dxil = malloc(S[i].n);
        for (size_t k = 0; k < S[i].n; k++) S[i].dxil[k] = (unsigned char)xs(&s);
        S[i].dxil[0] = (i % 29 == 7) ? 0xee : 0x44;   /* a few refused */
        S[i].dxil[1] = (i % 17 == 3) ? 0xbb : 0x58;   /* a few longer than the first buffer */
        S[i].kind = i % 3; S[i].air = (i % 5 == 0);
    }
}
static void fill_inputs(struct madeira_ir_convert_args *a, int i, uint64_t air) {
    memset(a, 0, sizeof *a);
    a->dxil = (uint64_t)(uintptr_t)S[i].dxil; a->dxil_len = S[i].n; a->entry_point = (uint64_t)(uintptr_t)"main";
    a->params = (uint64_t)(uintptr_t)RP; a->num_params = 3; a->ranges = (uint64_t)(uintptr_t)RR; a->num_ranges = 4;
    a->target_os = 1; a->gpu_family = 1009; a->os_version = (uint64_t)(uintptr_t)"17.0";
    if (S[i].kind == 0) a->layout = (uint64_t)(uintptr_t)&LAY[i % 3];
    if (S[i].kind == 1) {
        int v = (i + 1) % NST;
        a->ps_valid = 1; a->ps_sample_mask = ~0u; a->ps_flags = (uint32_t)i & 3; a->ps_unorm_output_mask = 1;
        a->vs_bytecode = (uint64_t)(uintptr_t)S[v].dxil; a->vs_bytecode_len = S[v].n;
    }
    if (S[i].air) { a->out_air_ranges = air; a->air_range_cap = MADEIRA_IR_AIR_RANGE_MAX; }
}
/* One stage as mad_convert_stage_opts converts it; returns a digest of every output. */
static uint64_t convert_one(int i, int onepass, int lz_check) {
    struct madeira_ir_convert_args a;
    struct madeira_ir_vs_input vsin[32]; struct madeira_ir_loc locs[MAD_LOC_MAX];
    struct madeira_ir_air_range air[MADEIRA_IR_AIR_RANGE_MAX];
    char name[MADEIRA_IR_ENTRY_MAX];
    unsigned char *buf = NULL, *buf2 = NULL; size_t need = 0, cap2 = 256u << 10;
    uint64_t h = 0xcbf29ce484222325ull;
    memset(vsin, 0, sizeof vsin); memset(locs, 0, sizeof locs); memset(air, 0, sizeof air); name[0] = 0;
    if (onepass) {
        need = S[i].n * 4 + 64u * 1024u; buf = malloc(need);
        if (S[i].kind == 0) buf2 = malloc(cap2);
    }
    fill_inputs(&a, i, (uint64_t)(uintptr_t)air);
    a.out_entry = (uint64_t)(uintptr_t)name;
    if (onepass) {
        a.out_buf = (uint64_t)(uintptr_t)buf; a.out_cap = need;
        a.out_vs_inputs = (uint64_t)(uintptr_t)vsin; a.vs_input_cap = 32;
        a.out_locs = (uint64_t)(uintptr_t)locs; a.loc_cap = MAD_LOC_MAX;
        if (buf2) { a.out_buf2 = (uint64_t)(uintptr_t)buf2; a.out_cap2 = cap2; }
    }
    mad_ir_convert_cached(&a);
    if (a.ret_status == MADEIRA_IR_BUFFER_TOO_SMALL || (!buf && a.ret_status == MADEIRA_IR_OK)) {
        need = (size_t)a.ret_len;
        free(buf); buf = malloc(need ? need : 1);
        if (!buf2 && S[i].kind == 0) buf2 = malloc(cap2);
        fill_inputs(&a, i, (uint64_t)(uintptr_t)air);
        a.out_buf = (uint64_t)(uintptr_t)buf; a.out_cap = need;
        a.out_vs_inputs = (uint64_t)(uintptr_t)vsin; a.vs_input_cap = 32;
        a.out_locs = (uint64_t)(uintptr_t)locs; a.loc_cap = MAD_LOC_MAX;
        a.out_entry = (uint64_t)(uintptr_t)name;
        if (buf2) { a.out_buf2 = (uint64_t)(uintptr_t)buf2; a.out_cap2 = cap2; }
        mad_ir_convert_cached(&a);
    }
    h = fnv(h, &a.ret_status, sizeof a.ret_status); h = fnv(h, &a.ret_len, sizeof a.ret_len);
    h = fnv(h, &a.ret_error_code, sizeof a.ret_error_code); h = fnv(h, &a.ret_stage, sizeof a.ret_stage);
    h = fnv(h, a.ret_note, strnlen(a.ret_note, sizeof a.ret_note));
    if (a.ret_status == MADEIRA_IR_OK) {
        h = fnv(h, buf, (size_t)a.ret_len); h = fnv(h, name, strlen(name));
        h = fnv(h, &a.ret_vs_input_count, 4); h = fnv(h, vsin, sizeof vsin);
        h = fnv(h, &a.ret_loc_count, 4); h = fnv(h, locs, sizeof locs);
        h = fnv(h, a.ret_tg_size, sizeof a.ret_tg_size); h = fnv(h, &a.ret_backend, 4);
        h = fnv(h, &a.ret_vs_output_size, 4); h = fnv(h, &a.ret_arg_qwords, 4); h = fnv(h, &a.ret_air_slot_mask, 4);
        h = fnv(h, &a.ret_air_nranges, 4); h = fnv(h, air, sizeof air);
        h = fnv(h, &a.ret_len2, sizeof a.ret_len2); if (buf2 && a.ret_len2) h = fnv(h, buf2, (size_t)a.ret_len2);
        if (lz_check) {   /* the pso-lazy-libs step of mad_convert_stage_opts */
            UINT64 k1[2] = {0, 0}, k2[2];
            long before = ctl_calls[21];
            int again = mad_scn_key_again(&a, k1);
            mad_sc_key(&a, k2);
            CHECK(again == (g_scn != 0), "stage %d: key reused %d with the store %u", i, again, (unsigned)g_scn);
            CHECK(!again || (k1[0] == k2[0] && k1[1] == k2[1]), "stage %d: the reused key differs", i);
            CHECK(mad_sc_exists(k2) == 1, "stage %d: the entry is not there", i);
            CHECK(!g_scn || ctl_calls[21] == before, "stage %d: asked winemetal whether the entry it just used is there", i);
            a.dxil_len--;   /* other inputs: never the old key */
            CHECK(!mad_scn_key_again(&a, k1), "stage %d: a key reused for other inputs", i);
            a.dxil_len++;
        }
    }
    free(buf); free(buf2);
    return h;
}

static void set_root(const char *r) {   /* C: with the profile's AppData/Local, as a prefix has it */
    char p[700];
    snprintf(g_root, sizeof g_root, "%s", r);
    snprintf(p, sizeof p, "%s/users", r); mkdir(p, 0777);
    snprintf(p, sizeof p, "%s/users/t", r); mkdir(p, 0777);
    snprintf(p, sizeof p, "%s/users/t/AppData", r); mkdir(p, 0777);
    snprintf(p, sizeof p, "%s/users/t/AppData/Local", r); mkdir(p, 0777);
}
static long wine_file_calls(void) {
    return w_create + w_size + w_read + w_write + w_close + w_attr + w_move + w_delete + w_mkdir; }
static void reset_counts(void) {
    w_create = w_size = w_read = w_write = w_close = w_attr = w_move = w_delete = w_mkdir = 0;
    for (int i = 0; i < 32; i++) ctl_calls[i] = 0;
    conversions = convert_calls = 0;
}

/* phase ROOT CFG ONEPASS [no-unix-name] [no-ops] [lz]: every stage twice, one line per stage */
static int phase(int argc, char **argv) {
    int onepass = atoi(argv[4]);
    int lz = 0;
    set_root(argv[2]);
    snprintf(g_cfg, sizeof g_cfg, "%s", strcmp(argv[3], "-") ? argv[3] : "");
    for (int k = 5; k < argc; k++) {
        if (!strcmp(argv[k], "no-unix-name")) g_no_unix_name = 1;
        if (!strcmp(argv[k], "no-ops")) g_no_ops = 1;
        if (!strcmp(argv[k], "lz")) lz = 1;
    }
    make_stages();
    CHECK(mad_sc_init() == 1, "the shader cache is on");
    usleep(50000);   /* the prune thread has looked for other builds */
    reset_counts();
    for (int r = 0; r < 2; r++)
        for (int i = 0; i < NST; i++) {
            uint64_t d = convert_one(i, onepass, lz);
            if (!r) printf("stage %d %016llx\n", i, (unsigned long long)d);
        }
    printf("store %u conversions %ld calls %ld wine-file-calls %ld create %ld size %ld read %ld write %ld close %ld attr %ld "
           "move %ld mkdir %ld ctl20 %ld ctl21 %ld ctl22 %ld hits %ld misses %ld stored %ld unix-name %d\n",
           (unsigned)g_scn, (long)conversions, (long)convert_calls, wine_file_calls(), (long)w_create, (long)w_size, (long)w_read,
           (long)w_write, (long)w_close, (long)w_attr, (long)w_move, (long)w_mkdir, (long)ctl_calls[20], (long)ctl_calls[21],
           (long)ctl_calls[22], (long)g_sc_hit, (long)g_sc_miss, (long)g_sc_store, g_unix_name_calls);
    fflush(stdout);
    fprintf(stderr, "LOG-BEGIN\n%sLOG-END\n", g_log);
    return fails ? 1 : 0;
}

/* conc ROOT CFG: 8 threads convert every stage in their own order at once */
#define NTHR 8
static uint64_t conc_digest[NTHR][NST];
static void *conc_main(void *p) {
    int t = (int)(intptr_t)p;
    uint64_t s = 99 + (uint64_t)t * 7777;
    int order[NST];
    for (int i = 0; i < NST; i++) order[i] = i;
    for (int i = NST - 1; i > 0; i--) { int j = (int)(xs(&s) % (uint64_t)(i + 1)), x = order[i]; order[i] = order[j]; order[j] = x; }
    for (int i = 0; i < NST; i++) conc_digest[t][order[i]] = convert_one(order[i], t & 1, 0);
    return NULL;
}
static int conc(int argc, char **argv) {
    pthread_t th[NTHR];
    (void)argc;
    set_root(argv[2]);
    snprintf(g_cfg, sizeof g_cfg, "%s", strcmp(argv[3], "-") ? argv[3] : "");
    make_stages();
    CHECK(mad_sc_init() == 1, "the shader cache is on");
    for (int t = 0; t < NTHR; t++) pthread_create(&th[t], NULL, conc_main, (void *)(intptr_t)t);
    for (int t = 0; t < NTHR; t++) pthread_join(th[t], NULL);
    for (int i = 0; i < NST; i++) {
        for (int t = 1; t < NTHR; t++)
            CHECK(conc_digest[t][i] == conc_digest[0][i], "stage %d: thread %d disagrees", i, t);
        printf("stage %d %016llx\n", i, (unsigned long long)conc_digest[0][i]);
    }
    printf("store %u conversions %ld\n", (unsigned)g_scn, (long)conversions);
    return fails ? 1 : 0;
}

/* ===== the patch's code alone ===== */
static uint32_t op(uint32_t o, uint64_t ptr) {
    struct madeira_ctl_args a; memset(&a, 0, sizeof a); a.op = o; a.ptr = ptr; wm_ctl(&a); return a.ret; }
static uint32_t get(uint32_t h, uint64_t k0, uint64_t k1, void *buf, uint64_t cap, uint64_t *len) {
    struct madeira_scn_io io; uint32_t r;
    memset(&io, 0, sizeof io); io.key[0] = k0; io.key[1] = k1; io.data = (uint64_t)(uintptr_t)buf; io.cap = cap; io.handle = h;
    r = op(21, (uint64_t)(uintptr_t)&io); if (len) *len = io.len; return r; }
static uint32_t put(uint32_t h, uint64_t k0, uint64_t k1, const void *buf, uint64_t n) {
    struct madeira_scn_io io;
    memset(&io, 0, sizeof io); io.key[0] = k0; io.key[1] = k1; io.data = (uint64_t)(uintptr_t)buf; io.len = n; io.handle = h;
    return op(22, (uint64_t)(uintptr_t)&io); }
static void entry_path(const char *dir, uint64_t k0, uint64_t k1, char *out, size_t cap) {
    snprintf(out, cap, "%s/%02x/%016llx%016llx.msc", dir, (unsigned)(k0 >> 56), (unsigned long long)k0, (unsigned long long)k1); }
static int count_tmp(const char *dir) {   /* temporary files left in the bucket directories */
    DIR *d = opendir(dir), *b; struct dirent *e, *f; int n = 0; char sub[1300];
    if (!d) return 0;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        snprintf(sub, sizeof sub, "%s/%s", dir, e->d_name);
        if (!(b = opendir(sub))) continue;
        while ((f = readdir(b))) if (strstr(f->d_name, ".tmp")) n++;
        closedir(b);
    }
    closedir(d);
    return n;
}
/* a self-describing entry: header "MSC1", key, generation, then a pattern of the generation */
static void make_entry(unsigned char *b, size_t n, uint64_t k0, uint64_t k1, uint32_t gen) {
    uint32_t m = 0x3143534du;
    memcpy(b, &m, 4); memcpy(b + 4, &gen, 4); memcpy(b + 8, &k0, 8); memcpy(b + 16, &k1, 8);
    for (size_t i = 24; i < n; i++) b[i] = (unsigned char)(gen * 131u + i * 7u);
}
static int entry_whole(const unsigned char *b, size_t n, uint64_t k0, uint64_t k1) {
    uint32_t gen; uint64_t a0, a1;
    if (n < 24) return 0;
    memcpy(&gen, b + 4, 4); memcpy(&a0, b + 8, 8); memcpy(&a1, b + 16, 8);
    if (a0 != k0 || a1 != k1) return 0;
    for (size_t i = 24; i < n; i++) if (b[i] != (unsigned char)(gen * 131u + i * 7u)) return 0;
    return 1;
}

static const char *u_dir;
static _Atomic int u_stop;
static _Atomic long u_reads, u_whole, u_absent;
#define U_KEYS 6
#define U_LEN (1u << 20)
static void *u_writer(void *p) {
    uint32_t h = (uint32_t)(uintptr_t)p, gen = 1;
    unsigned char *b = malloc(U_LEN);
    while (!u_stop) {
        int k = (int)(gen % U_KEYS);
        size_t n = U_LEN - (gen % 9) * 1000;
        make_entry(b, n, 0x11ull << 56 | (uint64_t)k, 0xabcull, gen);
        CHECK(put(h, 0x11ull << 56 | (uint64_t)k, 0xabcull, b, n) == 1, "put");
        gen++;
    }
    free(b);
    return NULL;
}
static void *u_reader(void *p) {
    uint32_t h = (uint32_t)(uintptr_t)p;
    unsigned char *b = malloc(U_LEN);
    uint64_t s = (uint64_t)(uintptr_t)b | 1;
    while (!u_stop) {
        int k = (int)(xs(&s) % U_KEYS);
        uint64_t len = 0;
        uint32_t r = get(h, 0x11ull << 56 | (uint64_t)k, 0xabcull, b, U_LEN, &len);
        u_reads++;
        if (r == 0) u_absent++;
        else { CHECK(r == 1 && entry_whole(b, (size_t)len, 0x11ull << 56 | (uint64_t)k, 0xabcull), "a partial entry was read (r %u len %llu)", r, (unsigned long long)len); u_whole++; }
    }
    free(b);
    return NULL;
}

static int unit(int argc, char **argv) {
    char dir[1100], other[1100], path[1300], sub[1200];
    unsigned char *b = malloc(1 << 20), *c = malloc(1 << 20);
    uint64_t len = 0;
    uint32_t h, h2;
    (void)argc;
    snprintf(dir, sizeof dir, "%s/build", argv[2]); mkdir(dir, 0777);
    snprintf(other, sizeof other, "%s/other", argv[2]); mkdir(other, 0777);
    snprintf(path, sizeof path, "%s/missing", argv[2]);
    CHECK(op(20, (uint64_t)(uintptr_t)path) == 0, "a missing directory has no handle");
    h = op(20, (uint64_t)(uintptr_t)dir);
    CHECK(h == 1 && op(20, (uint64_t)(uintptr_t)dir) == h, "one handle per directory (%u)", h);
    h2 = op(20, (uint64_t)(uintptr_t)other);
    CHECK(h2 == 2, "another directory, another handle");
    CHECK(get(9, 1, 2, b, 100, NULL) == 3 && put(9, 1, 2, b, 100) == 0, "a bad handle reads 3 and writes nothing");
    /* a round trip in the loose layout */
    make_entry(b, 5000, 0xa1ull << 56 | 5, 77, 3);
    CHECK(get(h, 0xa1ull << 56 | 5, 77, c, 1 << 20, &len) == 0, "missing entry: 0");
    CHECK(put(h, 0xa1ull << 56 | 5, 77, b, 5000) == 1, "put");
    entry_path(dir, 0xa1ull << 56 | 5, 77, path, sizeof path);
    { struct stat st; CHECK(!stat(path, &st) && st.st_size == 5000, "the entry is the loose file %s", path); }
    CHECK(get(h, 0xa1ull << 56 | 5, 77, c, 1 << 20, &len) == 1 && len == 5000 && !memcmp(b, c, 5000), "read back byte for byte");
    CHECK(get(h, 0xa1ull << 56 | 5, 77, c, 4999, &len) == 2 && len == 5000, "a short buffer gets the length");
    CHECK(get(h, 0xa1ull << 56 | 5, 77, NULL, 0, &len) == 2 && len == 5000, "cap 0: it is there");
    CHECK(get(h, 0xa1ull << 56 | 6, 77, NULL, 0, &len) == 0, "cap 0: it is not");
    CHECK(get(h2, 0xa1ull << 56 | 5, 77, c, 1 << 20, &len) == 0, "another directory does not have it");
    make_entry(b, 7000, 0xa1ull << 56 | 5, 77, 4);
    CHECK(put(h, 0xa1ull << 56 | 5, 77, b, 7000) == 1 && get(h, 0xa1ull << 56 | 5, 77, c, 1 << 20, &len) == 1 &&
          len == 7000 && !memcmp(b, c, 7000), "a second put replaces the entry");
    /* a file another writer (Wine) placed */
    snprintf(sub, sizeof sub, "%s/3c", dir); mkdir(sub, 0777);
    entry_path(dir, 0x3cull << 56 | 9, 1, path, sizeof path);
    make_entry(b, 3333, 0x3cull << 56 | 9, 1, 8);
    { FILE *f = fopen(path, "wb"); fwrite(b, 1, 3333, f); fclose(f); }
    CHECK(get(h, 0x3cull << 56 | 9, 1, c, 1 << 20, &len) == 1 && len == 3333 && !memcmp(b, c, 3333), "a file Wine wrote is read as it is");
    /* what cannot be read here is 3, not a miss */
    entry_path(dir, 0x3cull << 56 | 10, 1, path, sizeof path); mkdir(path, 0777);
    CHECK(get(h, 0x3cull << 56 | 10, 1, c, 1 << 20, &len) == 3, "an entry that is a directory: 3");
    CHECK(get(h, 0x3cull << 56 | 10, 1, NULL, 0, &len) == 2, "cap 0: a directory by that name is there, as GetFileAttributesW says");
    entry_path(dir, 0x3cull << 56 | 11, 1, path, sizeof path); { FILE *f = fopen(path, "wb"); fclose(f); }
    CHECK(get(h, 0x3cull << 56 | 11, 1, c, 1 << 20, &len) == 1 && len == 0, "an empty entry reads as 0 bytes");
    snprintf(sub, sizeof sub, "%s/5d", dir); { FILE *f = fopen(sub, "wb"); fclose(f); }
    CHECK(get(h, 0x5dull << 56, 1, c, 1 << 20, &len) == 0, "a bucket that is a file: no such entry");
    CHECK(put(h, 0x5dull << 56, 1, b, 3333) == 0, "a bucket that is a file: not written");
    CHECK(count_tmp(dir) == 0, "no temporary file is left");
    if (!fails) printf("PASS: unit\n");

    /* 4 writers and 4 readers on the same entries at once: an entry is absent or whole */
    {
        pthread_t th[8];
        unsigned char *w = malloc(U_LEN);
        u_dir = dir;
        for (int k = 0; k < U_KEYS; k++) {   /* every entry is there before the threads start */
            make_entry(w, U_LEN / 2, 0x11ull << 56 | (uint64_t)k, 0xabcull, 0);
            CHECK(put(h, 0x11ull << 56 | (uint64_t)k, 0xabcull, w, U_LEN / 2) == 1, "first put");
        }
        free(w);
        for (int t = 0; t < 8; t++) pthread_create(&th[t], NULL, t < 4 ? u_writer : u_reader, (void *)(uintptr_t)h);
        /* at least 400 ms, then until the readers have seen 200 entries (a loaded or sanitized host is slow), at most 20 s */
        for (int ms = 0; ms < 20000 && (ms < 400 || u_whole < 200); ms += 10) usleep(10000);
        u_stop = 1;
        for (int t = 0; t < 8; t++) pthread_join(th[t], NULL);
        CHECK(u_absent == 0, "an entry was missing while it was being replaced (%ld times)", (long)u_absent);
        CHECK(u_whole > 50, "readers saw too few entries (%ld)", (long)u_whole);
        CHECK(count_tmp(dir) == 0, "no temporary file is left after the threads");
        if (!fails) printf("PASS: concurrent: %ld reads while 4 threads replaced the same entries, every one whole\n", (long)u_reads);
    }

    /* a writer killed at any point: every entry is absent or whole, and gets ignore its leftovers */
    if (argc < 4 || strcmp(argv[3], "no-fork")) {
        int killed = 0, whole = 0, absent = 0;
        uint64_t s = 42;
        for (int it = 0; it < 24; it++) {
            pid_t pid = fork();
            if (!pid) {
                unsigned char *w = malloc(8u << 20);
                for (uint32_t gen = 1;; gen++) {
                    size_t n = (4u << 20) + (gen % 5) * 100000;
                    make_entry(w, n, 0x77ull << 56 | (gen % 3), 5, gen);
                    put(h, 0x77ull << 56 | (gen % 3), 5, w, n);
                }
            }
            usleep((useconds_t)(xs(&s) % 30000));
            kill(pid, SIGKILL); waitpid(pid, NULL, 0); killed++;
            for (int k = 0; k < 3; k++) {
                unsigned char *r = malloc(8u << 20);
                uint32_t g = get(h, 0x77ull << 56 | (uint64_t)k, 5, r, 8u << 20, &len);
                CHECK(g == 0 || (g == 1 && entry_whole(r, (size_t)len, 0x77ull << 56 | (uint64_t)k, 5)), "after a kill: %u", g);
                if (g) whole++; else absent++;
                free(r);
            }
        }
        if (!fails) printf("PASS: %d writers killed mid-way: %d entries whole, %d absent, %d temporary files left (ignored)\n",
                           killed, whole, absent, count_tmp(dir));
    }
    free(b); free(c);
    return fails ? 1 : 0;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc >= 3 && !strcmp(argv[1], "unit")) return unit(argc, argv);
    if (argc >= 5 && !strcmp(argv[1], "phase")) return phase(argc, argv);
    if (argc >= 4 && !strcmp(argv[1], "conc")) return conc(argc, argv);
    fprintf(stderr, "usage\n");
    return 2;
}
'''


def build(folder, exe, sanitize, compiler=None):
    src = folder / 'sc.c'
    src.write_text(HARNESS.replace('@HELPERS@', patch.HELPERS).replace('@CASES@', patch.CASES)
                   .replace('@PE_SLICE@', pe_slice))
    flags = [compiler or cc, '-std=gnu11', '-g', '-O1', '-Wall', '-Wno-unused-function', '-Wno-format',
             '-I', str(root / 'madeira-d3d12/src'), str(src), '-o', str(exe), '-lpthread']
    r = subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True, text=True)
    if r.returncode != 0 and sanitize:
        return None, r.stderr
    return (exe if r.returncode == 0 else None), r.stderr


def run(exe, *args, env=None):
    e = dict(os.environ)
    e.update({'ASAN_OPTIONS': 'detect_leaks=0', 'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1',
              'TSAN_OPTIONS': 'halt_on_error=1'})
    if env:
        e.update(env)
    return subprocess.run([str(exe)] + [str(a) for a in args], capture_output=True, text=True, env=e, timeout=600)


def stages(out):
    return {int(m.group(1)): m.group(2) for m in re.finditer(r'^stage (\d+) ([0-9a-f]{16})$', out, re.M)}


def stats(out):
    line = [l for l in out.splitlines() if l.startswith('store ')]
    if not line:
        return {}
    w = line[-1].split()
    return {w[i]: int(w[i + 1]) for i in range(0, len(w) - 1, 2)}


with tempfile.TemporaryDirectory(prefix='madeira-sc-native-c-') as d:
    folder = Path(d)
    exe, err = build(folder, folder / 'sc', ['-fsanitize=address,undefined', '-fno-sanitize-recover=all'])
    sanitized = exe is not None
    if not exe:
        exe, err = build(folder, folder / 'sc', [])
    check(exe is not None, 'the patch\'s code and madeira_d3d12.c\'s shader cache build on this host' +
          ('' if exe else ': ' + err[-3000:]))
    if not exe:
        print('\n'.join('FAILED: ' + f for f in failures))
        sys.exit(1)
    print(f'     (built with {Path(cc).name}{", AddressSanitizer + UBSan" if sanitized else ""})')

    # 2. the patch's code alone
    u = folder / 'unit'
    u.mkdir()
    r = run(exe, 'unit', u)
    print(r.stdout.strip())
    check(r.returncode == 0, 'the native store on its own (round trips, layout, errors, threads, killed writers)' +
          ('' if r.returncode == 0 else ': ' + (r.stdout + r.stderr)[-3000:]))
    check('1 shader-cache entries were already there when stored' in r.stderr,
          'an entry stored over an existing one is counted (two threads that converted the same stage)')
    tsan = None
    for compiler in dict.fromkeys(c for c in (cc, shutil.which('gcc'), shutil.which('clang')) if c):
        tsan, terr = build(folder, folder / 'sc-tsan', ['-fsanitize=thread'], compiler)   # clang may lack the runtime
        if tsan:
            break
    if tsan:
        u2 = folder / 'unit-tsan'
        u2.mkdir()
        r = run(tsan, 'unit', u2, 'no-fork')
        check(r.returncode == 0 and 'concurrent' in r.stdout,
              'ThreadSanitizer: 4 writers and 4 readers on the same entries' + ('' if r.returncode == 0 else ': ' + r.stderr[-3000:]))
    else:
        print('     (no ThreadSanitizer on this host)')

    # 3. madeira_d3d12.c's shader cache over the store, launch after launch
    def phase(name, cache, cfg, onepass, *extra):
        cache.mkdir(exist_ok=True)
        r = run(exe, 'phase', cache, cfg, onepass, *extra)
        st, s = stages(r.stdout), stats(r.stdout)
        ok = r.returncode == 0 and len(st) == 120 and s
        check(ok, f'{name}: runs' + ('' if ok else ': ' + (r.stdout + r.stderr)[-3000:]))
        return st, s, r

    wine = folder / 'c-wine'
    native = folder / 'c-native'
    cold_off, s1, _ = phase('cold, shader-cache-native = 0', wine, 'shader-cache-native=0', 1)
    cold_on, s2, r2 = phase('cold, default (on)', native, '-', 1)
    check(cold_on == cold_off, 'cold cache: every stage converts to the same outputs with the switch on and off')
    check(s1.get('store') == 0 and s1.get('ctl20') == 0 and s1.get('ctl21') == 0 and s1.get('ctl22') == 0,
          'off: no op 20-22 is sent')
    check(s2.get('store') == 1 and s2.get('unix-name') == 1 and 'read and written natively' in r2.stderr,
          'on by default: winemetal opens the build directory once (one wine_get_unix_file_name)')
    check(s1.get('conversions') == s2.get('conversions') and s1.get('stored') == s2.get('stored') and
          s1.get('hits') == s2.get('hits') and s1.get('misses') == s2.get('misses'),
          f'cold: the same conversions ({s1.get("conversions")}), hits, misses and stores either way')
    check(s2.get('create') == 0 and s2.get('read') == 0 and s2.get('write') == 0 and s2.get('move') == 0 and
          s2.get('attr') == 0 and s2.get('close') == 0,
          f'cold, on: no Wine file call (off: {s1.get("create")} CreateFileW, {s1.get("move")} MoveFileExW, '
          f'{s1.get("attr")} GetFileAttributesW)')
    cmp = filecmp.dircmp(wine, native)

    def same_tree(c):
        if c.left_only or c.right_only or c.diff_files or c.funny_files:
            return False
        _, mismatch, errors = filecmp.cmpfiles(c.left, c.right, c.common_files, shallow=False)
        return not mismatch and not errors and all(same_tree(s) for s in c.subdirs.values())
    files = sum(len(fs) for _, _, fs in os.walk(native))
    check(same_tree(cmp) and files > 100, f'the cache written natively is the one Wine writes: the same {files} files, byte for byte')
    for tree in (wine, native):
        local = tree / 'users/t/AppData/Local'
        check((local / 'BCD/ShaderCache').is_dir() and sorted(p.name for p in local.iterdir()) == ['BCD'],
              f'{tree.name}: the cache lives in %LOCALAPPDATA%\\BCD\\ShaderCache, and nothing named Madeira is created beside it')

    warm_on, s3, _ = phase('warm, on, Wine\'s files', wine, 'shader-cache-native=1', 1)
    warm_off, s4, _ = phase('warm, off, native files', native, 'shader-cache-native=0', 1)
    check(warm_on == cold_off and warm_off == cold_off, 'warm: each way reads what the other wrote, the same outputs')
    check(s3.get('conversions') == 0 and s4.get('conversions') == 0, 'warm: nothing is converted again')
    check(s3.get('wine-file-calls') == 0 and s3.get('ctl22') == 0,
          f'warm, on: no Wine file call at all; {s3.get("ctl21")} native reads (off: {s4.get("create")} CreateFileW, '
          f'{s4.get("size")} GetFileSizeEx, {s4.get("read")} ReadFile, {s4.get("close")} CloseHandle, '
          f'{s4.get("attr")} GetFileAttributesW)')
    sz_on, s5, _ = phase('warm, on, size-then-fill', native, '-', 0)
    sz_off, s6, _ = phase('warm, off, size-then-fill', wine, 'shader-cache-native=0', 0)
    check(sz_on == sz_off and s5.get('conversions') == 0 and s5.get('wine-file-calls') == 0,
          'size-then-fill: the same outputs, no conversion, no Wine file call with the switch on')
    lz_on, s7, _ = phase('warm, on, pso-lazy-libs key and entry checks', native, '-', 1, 'lz')
    lz_off, s8, _ = phase('warm, off, pso-lazy-libs key and entry checks', wine, 'shader-cache-native=0', 1, 'lz')
    check(lz_on == cold_off and lz_off == cold_off and s8.get('attr', 0) > 0 and s7.get('attr') == 0,
          f'pso-lazy-libs: the key comes back for the same inputs only; an entry just read is there without a call '
          f'(off: {s8.get("attr")} GetFileAttributesW)')
    nn, s9, r9 = phase('no wine_get_unix_file_name', wine, '-', 1, 'no-unix-name')
    no, s10, r10 = phase('a winemetal without ops 20-22', native, '-', 1, 'no-ops')
    check(nn == cold_off and no == cold_off and s9.get('store') == 0 and s10.get('store') == 0 and
          s9.get('read', 0) > 0 and s10.get('read', 0) > 0 and 'native file access unavailable' in r9.stderr and
          'native file access unavailable' in r10.stderr,
          'without wine_get_unix_file_name or without the ops: Wine\'s file calls, the same outputs')
    cold2 = folder / 'c-conc'
    cold2.mkdir()
    r = run(exe, 'conc', cold2, '-')
    conc = stages(r.stdout)
    check(r.returncode == 0 and conc == cold_off,
          '8 threads converting the same stages at once into a cold cache agree with one' +
          ('' if r.returncode == 0 else ': ' + (r.stdout + r.stderr)[-2000:]))
    after, s11, _ = phase('the threads\' cache, read through Wine', cold2, 'shader-cache-native=0', 1)
    check(after == cold_off and s11.get('conversions') == 0, 'what the threads wrote is whole: read through Wine, nothing converts')
    print(f'     per launch over {2 * 120} stage conversions (120 distinct): cold off {s1.get("wine-file-calls")} Wine file calls, '
          f'cold on {s2.get("wine-file-calls")}; warm off {s4.get("wine-file-calls")}, warm on {s3.get("wine-file-calls")} '
          f'({s3.get("ctl21")} native reads)')

# ---------------------------------------------------------------------------
# 4. the source
# ---------------------------------------------------------------------------
init = body(pe, 'static void mad_scn_init(void) {')
check('mad_cfg_int_pe("shader-cache-native", 1)' in init, 'shader-cache-native is on by default (= 0 turns it off)')
rd = body(pe, 'static int mad_sc_read_file(const UINT64 key[2], unsigned char **blob_out, SIZE_T *size_out) {')
wr = body(pe, 'static void mad_sc_write_file(const UINT64 key[2], const unsigned char *blob, SIZE_T total) {')
ex = body(pe, 'static int mad_sc_exists(const UINT64 key[2]) {')
check(rd.index('if (g_scn) {') < rd.index('mad_sc_path(key, path, 0);') and
      'f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);' in rd and
      wr.index('if (g_scn && mad_scn_put(key, blob, total)) {') < wr.index('mad_sc_path(key, path, 1);') and
      'if (w && MoveFileExW(tmp, path, MOVEFILE_REPLACE_EXISTING)) InterlockedIncrement(&g_sc_store);' in wr and
      ex.index('if (g_scn) {') < ex.index('return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES;'),
      'each native branch sits in front of the unchanged Wine code, which takes over when it cannot')
sc_init = body(pe, 'static int mad_sc_init(void) {')
check(sc_init.index('if (on) { mad_scn_init(); mad_pso_archive_init(); MemoryBarrier(); }') < sc_init.index('g_sc_on = on;'),
      'the store is opened before the cache is published')
conv = body(pe, 'static obj_handle_t mad_convert_stage_opts(struct mad_device *d, struct mad_rootsig *rs,')
check('if (!mad_scn_key_again(&a, o->lz->key)) mad_sc_key(&a, o->lz->key); o->lz->ok = mad_sc_exists(o->lz->key);' in conv,
      'pso-lazy-libs takes the conversion\'s key when it is the same')
steps = re.findall(r'python3 (tools/patch-winemetal-[\w.-]+\.py)', workflow)
check('tools/patch-winemetal-sc-native.py' in steps and
      steps.index('tools/patch-winemetal-sc-native.py') > steps.index('tools/patch-winemetal-pso-warm.py') and
      workflow.index('tools/patch-winemetal-sc-native.py') < workflow.index('bash build/dxmt-ios/build.sh'),
      'build-ipa.yml applies the patch after pso-warm and before dxmt-ios is built')
check('python3 tests/host/check-sc-native.py' in workflow and
      workflow.index('python3 tests/host/check-sc-native.py') > workflow.index('python3 tests/host/check-quiet-log.py'),
      'build-ipa.yml runs this check after the quiet-log check')
entry = catalog[catalog.index('key: "shader-cache-native"'):] if 'key: "shader-cache-native"' in catalog else ''
check(entry != '' and 'defaultValue: "1"' in entry[:400], 'the settings catalog lists shader-cache-native, default 1')

if failures:
    print('\n'.join('FAILED: ' + f for f in failures))
    sys.exit(1)
print('check-sc-native: all checks passed')
