#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[wow-cpu-pool]: the WoW64 CPU module (xtajit.dll) runs from its JIT-pool copy only.

Every 32-bit program died at its start on iOS 18 (16 logs of 16, builds 573/574; none of 44 on iOS 27). The
module has two copies there: a JIT-pool copy (made by the first protection request, before its entry point
has run) and an executable PE view (the plain mprotect(r-x) fast path in mprotect_exec succeeds where TXM does
not police code). The loader's call to the entry point ran in the view: _CRT_INIT, __main and every global
constructor initialised the view's .data/.bss, while wow64.dll calls BTCpuProcessInit/BTCpuThreadInit in the
pool copy, whose .bss was still zero (`Threads`, an unordered_map: max_load_factor 0, so emplace throws
__next_prime overflow). build/ntdll-unix/virtual_ios.c takes EXEC back from the view when a pool copy exists.

Checks, without a Wine run:
  - the view flag is a fresh bit; the mark is set in map_image_into_view before the first protection
    request, and only for a native ARM64 PE32+ named xtajit.dll;
  - mprotect_exec asks ios_exec_in_place_split only inside the branch where the fast path really got EXEC
    (so a TXM kernel, where it never does, sees no change), takes EXEC back with a plain mprotect without it,
    and then falls through to the pool path; the DEP-off site and every other image are untouched;
  - the production helpers, compiled against a model of the view tree and the pool mappings: the name test, the
    mark, and the decision (take back only for a marked view whose pool copy exists; never for a module with no
    pool copy, an unmapped copy, another image, the Mach exception thread or MADEIRA_WOW_CPU_POOL_ONLY=0);
  - the switch is exactly "0"; the catalog overlay documents it; the CI workflow runs this check;
  - the auto-tune rule (id wow64-thread-map-uninitialised, fixedIn 576) and its single fixture;
  - the shipped xtajit.dll is a native ARM64 PE32+ with the three BTCpu entry points wow64 translates and a TLS
    callback table (the loader runs TLS callbacks and the entry point at the PE address);
  - wow64.dll translates BTCpuProcessInit/BTCpuThreadInit to the pool copy (when the wine submodule is present).
Needs python3 and a C compiler.
"""
from pathlib import Path
import importlib.util
import json
import re
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


# --- the view flag ------------------------------------------------------------
flags = {n: int(v, 16) for n, v in re.findall(r'#define (IOS_VIEW_\w+)\s+(0x[0-9a-fA-F]+)', native)}
check('IOS_VIEW_WOW_CPU is a fresh single bit', 'IOS_VIEW_WOW_CPU' in flags and flags['IOS_VIEW_WOW_CPU'] & (flags['IOS_VIEW_WOW_CPU'] - 1) == 0
      and list(flags.values()).count(flags['IOS_VIEW_WOW_CPU']) == 1 and flags['IOS_VIEW_WOW_CPU'] == 0x8)

# --- the mark in map_image_into_view ------------------------------------------
mapper = function(native, 'static NTSTATUS map_image_into_view(')
mark = mapper.index('ios_wow_cpu_mark_view( view, nt_name, nt );')
first = mapper.index('set_vprot( view, ptr, ROUND_SIZE( 0, header_size, align_mask ), VPROT_COMMITTED | VPROT_READ );')
check('the mark is set before the first protection request and before the eager copy loop',
      mark < first < mapper.index('mprotect_exec(sec_addr, sec_size, prot);'))
helper_mark = function(native, 'static void ios_wow_cpu_mark_view(')
check('the mark needs a native ARM64 PE32+ named xtajit.dll and the switch',
      'IMAGE_FILE_MACHINE_ARM64' in helper_mark and 'IMAGE_NT_OPTIONAL_HDR64_MAGIC' in helper_mark
      and 'ios_nt_name_has_basename( nt_name, "xtajit.dll" )' in helper_mark and 'ios_wow_cpu_pool_only_enabled()' in helper_mark
      and 'view->ios_flags |= IOS_VIEW_WOW_CPU;' in helper_mark)

# --- mprotect_exec -------------------------------------------------------------
prot = function(native, 'static inline int mprotect_exec(')
fast = prot.index('if (!ios_no_plain_exec() && !mprotect( base, size, unix_prot ))')
region = prot.index('mach_vm_region(mach_task_self(), &addr, &reg_size,', fast)
split = prot.index('ios_exec_in_place_split( base, size, unix_prot )', fast)
check('the split test runs after the Mach region query, short-circuited behind "EXEC was granted"',
      region < split and '(info.protection & VM_PROT_EXECUTE) &&\n                ios_exec_in_place_split' in prot[region:split + 80])
block = prot[split:prot.index('if (!exec_taken_back &&', split)]
check('EXEC is taken back with a plain mprotect without PROT_EXEC and nothing else is changed',
      'mprotect( base, size, unix_prot & ~PROT_EXEC )' in block and 'exec_taken_back = 1;' in block
      and 'ios_jit_copy_refused' not in block and 'return' not in block)
rest = prot[prot.index('if (!exec_taken_back &&', split):]
check('only when EXEC was not taken back does the old "genuinely RX/RWX - done" return stay',
      rest.index('return 0;  /* genuinely RX/RWX') < rest.index('falling through to JIT-pool path'))
check('the DEP-off fast path (force_exec_prot) and the switch MADEIRA_NO_PLAIN_EXEC are unchanged',
      'if (!ios_no_plain_exec() && !mprotect( base, size, unix_prot | PROT_EXEC )) return 0;' in prot)
check('the helper never runs on the Mach exception thread', 'if (ios_in_mach_exc) return 0;' in
      function(native, 'static int ios_exec_in_place_split('))

# --- the helpers, compiled against a model ------------------------------------------
parts = [function(native, sig) for sig in (
    'static int ios_wow_cpu_pool_only_enabled(',
    'static int ios_nt_name_has_basename(',
    'static void ios_wow_cpu_mark_view(',
    'static int ios_exec_in_place_split(')]
PRE = r'''
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { struct { unsigned short Machine; } FileHeader; struct { unsigned short Magic; } OptionalHeader; } IMAGE_NT_HEADERS;
#define IMAGE_FILE_MACHINE_ARM64 0xaa64
#define IMAGE_FILE_MACHINE_AMD64 0x8664
#define IMAGE_NT_OPTIONAL_HDR64_MAGIC 0x20b
#define IMAGE_NT_OPTIONAL_HDR32_MAGIC 0x10b
#define SEC_IMAGE 0x01000000u
#define IOS_VIEW_WOW_CPU 0x8
struct file_view { void *base; size_t size; unsigned int protect; unsigned int ios_flags; };
struct ios_jit_mapping { void *pe_base; void *jit_base; size_t size; unsigned char unmapped; };
static struct ios_jit_mapping ios_jit_mappings[8];
static int ios_jit_mapping_count;
static volatile int ios_in_mach_exc;
static struct file_view *views[4];
static struct file_view *find_view( const void *addr, size_t size )
{
    int i;
    for (i = 0; i < 4; i++)
        if (views[i] && (const char *)addr >= (const char *)views[i]->base &&
            (const char *)addr + size <= (const char *)views[i]->base + views[i]->size) return views[i];
    return NULL;
}
static int log_n, log_cpu_n, log_other_n;
static char last_log[600];
static int model_dprintf( int fd, const char *fmt, ... )
{
    va_list ap;
    (void)fd;
    va_start( ap, fmt );
    vsnprintf( last_log, sizeof(last_log), fmt, ap );
    va_end( ap );
    log_n++;
    if (strstr( last_log, "[exec-split]" ) && strstr( last_log, "[wow-cpu-pool]" )) log_cpu_n++;
    else if (strstr( last_log, "[exec-split]" )) log_other_n++;
    return 0;
}
#define dprintf model_dprintf
static const char *model_getenv( const char *name )
{
#ifdef ENV_VALUE
    return strcmp( name, "MADEIRA_WOW_CPU_POOL_ONLY" ) ? NULL : ENV_VALUE;
#else
    (void)name; return NULL;
#endif
}
#define getenv model_getenv
static const char *ios_pe_module_name( const void *b, size_t s ) { (void)b; (void)s; return "libwow64fex.dll"; }
'''
MAIN = r'''
static int bad;
#define EXPECT(what, cond) do { if (!(cond)) { printf("FAIL %s\n", what); bad = 1; } } while (0)

static UNICODE_STRING name_of( const char *s, WCHAR *buf )
{
    UNICODE_STRING u;
    size_t n = strlen( s ), i;
    for (i = 0; i < n; i++) buf[i] = (unsigned char)s[i];
    u.Length = (unsigned short)(n * 2); u.MaximumLength = u.Length; u.Buffer = buf;
    return u;
}

static int marked( const char *nt, unsigned short machine, unsigned short magic )
{
    static char region[0x10000];
    WCHAR buf[260];
    UNICODE_STRING u = name_of( nt, buf );
    struct file_view v = { region, sizeof(region), SEC_IMAGE, 0 };
    IMAGE_NT_HEADERS h; h.FileHeader.Machine = machine; h.OptionalHeader.Magic = magic;
    ios_wow_cpu_mark_view( &v, &u, &h );
    return (v.ios_flags & IOS_VIEW_WOW_CPU) != 0;
}

int main(void)
{
    static char r0[0x40000], r1[0x40000], r2[0x40000];
    struct file_view cpu = { r0, sizeof(r0), SEC_IMAGE, IOS_VIEW_WOW_CPU };
    struct file_view other = { r1, sizeof(r1), SEC_IMAGE, 0 };
    struct file_view anon = { r2, sizeof(r2), 0, IOS_VIEW_WOW_CPU };   /* flag but not an image: cannot happen, still no */
    int i;

    /* the name test and the mark */
#ifndef ENV_IS_OFF
    EXPECT( "windows path", marked( "\\??\\C:\\windows\\system32\\xtajit.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "upper case", marked( "\\??\\C:\\WINDOWS\\SYSTEM32\\XTAJIT.DLL", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "bare name", marked( "xtajit.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "unix separator", marked( "/var/x/aarch64-windows/xtajit.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "the EC module is not marked", !marked( "\\??\\C:\\windows\\system32\\xtajit64.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "a longer name is not marked", !marked( "\\??\\C:\\games\\notxtajit.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "a directory named like it is not marked", !marked( "\\??\\C:\\xtajit.dll\\other.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "not an x64 image of that name", !marked( "\\??\\C:\\x\\xtajit.dll", IMAGE_FILE_MACHINE_AMD64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    EXPECT( "not a PE32 image of that name", !marked( "\\??\\C:\\x\\xtajit.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR32_MAGIC ) );
    EXPECT( "no name", !marked( "", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
    {
        struct file_view v = { r0, 1, SEC_IMAGE, 0 };
        IMAGE_NT_HEADERS h; h.FileHeader.Machine = IMAGE_FILE_MACHINE_ARM64; h.OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC;
        ios_wow_cpu_mark_view( &v, NULL, &h );
        EXPECT( "NULL name", !(v.ios_flags & IOS_VIEW_WOW_CPU) );
    }
#else
    EXPECT( "MADEIRA_WOW_CPU_POOL_ONLY=0 marks nothing", !marked( "xtajit.dll", IMAGE_FILE_MACHINE_ARM64, IMAGE_NT_OPTIONAL_HDR64_MAGIC ) );
#endif

    views[0] = &cpu; views[1] = &other; views[2] = &anon;
#ifdef ENV_IS_OFF
    ios_jit_mappings[0].pe_base = r0; ios_jit_mappings[0].jit_base = (void *)0x300191000; ios_jit_mapping_count = 1;
    EXPECT( "switch 0: nothing is taken back", ios_exec_in_place_split( r0 + 0x1000, 0x3c000, PROT_READ | PROT_EXEC ) == 0 );
    EXPECT( "switch 0: the combination is only reported", log_cpu_n == 0 && log_other_n == 1 && strstr( last_log, "reported only" ) );
    return bad;
#endif
    /* no pool copy: the module runs in place only, one set of globals: untouched */
    EXPECT( "no pool copy: not taken back", ios_exec_in_place_split( r0 + 0x1000, 0x3c000, PROT_READ | PROT_EXEC ) == 0 );
    EXPECT( "no pool copy: no [exec-split] line", log_cpu_n == 0 && log_other_n == 0 );

    /* the pool copy of the CPU module exists (made by the first request): the split is taken back */
    ios_jit_mappings[0].pe_base = r0; ios_jit_mappings[0].jit_base = (void *)0x300191000; ios_jit_mappings[0].size = sizeof(r0);
    ios_jit_mapping_count = 1;
    EXPECT( "pool copy + marked view: taken back", ios_exec_in_place_split( r0 + 0x1000, 0x3c000, PROT_READ | PROT_EXEC ) == 1 );
    EXPECT( "...and logged once with the pool copy named", log_cpu_n == 1 && strstr( last_log, "0x300191000" ) && strstr( last_log, "taken back" ) );
    EXPECT( "a request starting at the view base", ios_exec_in_place_split( r0, 0x4000, PROT_READ | PROT_EXEC ) == 1 );
    EXPECT( "an address that is no view at all", ios_exec_in_place_split( (void *)0x1000, 0x1000, PROT_READ | PROT_EXEC ) == 0 );

    /* an unmapped (dead) copy is not a pool copy of this view */
    ios_jit_mappings[0].unmapped = 1;
    EXPECT( "unmapped copy: not taken back", ios_exec_in_place_split( r0 + 0x1000, 0x1000, PROT_READ | PROT_EXEC ) == 0 );
    ios_jit_mappings[0].unmapped = 0;

    /* the exception thread never does a view lookup */
    ios_in_mach_exc = 1;
    EXPECT( "Mach exception thread: not taken back", ios_exec_in_place_split( r0 + 0x1000, 0x1000, PROT_READ | PROT_EXEC ) == 0 );
    ios_in_mach_exc = 0;

    /* any other image: reported (16 lines), never taken back */
    ios_jit_mappings[1].pe_base = r1; ios_jit_mappings[1].jit_base = (void *)0x300720000; ios_jit_mappings[1].size = sizeof(r1);
    ios_jit_mapping_count = 2;
    log_other_n = 0;
    for (i = 0; i < 40; i++)
        EXPECT( "other image: never taken back", ios_exec_in_place_split( r1 + 0x1000, 0x4000, PROT_READ | PROT_EXEC ) == 0 );
    EXPECT( "other image: reported at most 16 times", log_other_n == 16 );
    /* the CPU module's own line is not starved by the other images' spent budget */
    log_cpu_n = 0;
    for (i = 0; i < 10; i++)
        EXPECT( "CPU module after the budget is spent: still taken back", ios_exec_in_place_split( r0 + 0x1000, 0x1000, PROT_READ | PROT_EXEC ) == 1 );
    EXPECT( "CPU module: at most 4 lines in total", log_cpu_n + 1 <= 4 );
    EXPECT( "a flagged non-image view is ignored", ios_exec_in_place_split( r2 + 0x1000, 0x1000, PROT_READ | PROT_EXEC ) == 0 );
    return bad;
}
'''


def run_model(env_value):
    src = PRE + '\n'.join(parts) + MAIN
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / 'model.c'
        c.write_text(src)
        exe = Path(d) / 'model'
        cmd = ['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter',
               '-fsanitize=undefined,address', '-o', str(exe), str(c)]
        if env_value is not None:
            cmd.insert(1, '-DENV_VALUE="%s"' % env_value)
            if env_value == '0':
                cmd.insert(1, '-DENV_IS_OFF=1')
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            return False
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout, r.stderr)
        return r.returncode == 0


check('the helpers compile against the model and decide as specified', run_model(None))
check('with MADEIRA_WOW_CPU_POOL_ONLY=0 nothing is marked and nothing is taken back', run_model('0'))
check('...and the values 1, yes and "" keep it on (only the exact "0" switches off)',
      run_model('1') and run_model('yes') and run_model(''))

# the switch, exactly "0"
enabled = function(native, 'static int ios_wow_cpu_pool_only_enabled(')
check('the switch is exactly "0"', "cached = (v && v[0] == '0' && !v[1]) ? 0 : 1;" in enabled)

# --- catalog, workflow ----------------------------------------------------------
catalog = (root / 'build/tools/gen-config-catalog.py').read_text()
m = re.search(r'"env\.MADEIRA_WOW_CPU_POOL_ONLY": \{(.*?)"sources"', catalog, re.S)
check('the catalog overlay documents env.MADEIRA_WOW_CPU_POOL_ONLY as a bool, default 1', bool(m) and '"kind": "bool", "default": "1"' in m.group(1))
check('the CI workflow runs this check', 'python3 tests/host/check-wow-cpu-pool.py' in (root / '.github/workflows/build-ipa.yml').read_text())

# --- the rule and its fixture ----------------------------------------------------
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)
rule = [r for r in rules.RULES if r['id'] == 'wow64-thread-map-uninitialised']
check('one rule wow64-thread-map-uninitialised, fixedIn 576, no config line', len(rule) == 1 and rule[0]['lines'] == [] and rule[0]['fixedIn'] == 576)
if rule:
    sig = [s['has'] for s in rule[0]['all']]
    check('it keys on the crash PC inside the module, the missing TLS pointer and the non-TXM start-up line',
          ['[mach_exc] pc PE: libwow64fex.dll+'] in sig and ['[x86_seg]', 'TEB[0x58]=0x0'] in sig
          and ['[jit-nodebug] debugger-less JIT pool: allowed'] in sig)   # night round 2026-11-11 (A5 S9): a 'none' keeps the pool-narrowing stop (KPX6DZ) out
fixtures = sorted(p.name for p in (root / 'tests/fixtures/autotune').glob('*.expect')
                  if 'wow64-thread-map-uninitialised' in json.loads(p.read_text())['matched'])
check('exactly one fixture, and it ends known', fixtures == ['half-life-2-2026-10-20.expect']
      and json.loads((root / 'tests/fixtures/autotune/half-life-2-2026-10-20.expect').read_text())['status'] == 'known')
check('the generated Swift rule table has the rule', '"wow64-thread-map-uninitialised"' in
      (root / 'app/Madeira/AutoTuneRules.generated.swift').read_text())

# --- the shipped module ---------------------------------------------------------
dll = (root / 'app/Madeira/aarch64-windows/xtajit.dll').read_bytes()
pe = struct.unpack_from('<I', dll, 0x3c)[0]
machine, nsec, _, _, _, optsz, _ = struct.unpack_from('<HHIIIHH', dll, pe + 4)
opt = pe + 24
magic = struct.unpack_from('<H', dll, opt)[0]
check('xtajit.dll is a native ARM64 PE32+', machine == 0xAA64 and magic == 0x20B)
secs = []
for i in range(nsec):
    o = opt + optsz + 40 * i
    name = dll[o:o + 8].rstrip(b'\0').decode()
    vsize, va, rsize, raw = struct.unpack_from('<IIII', dll, o + 8)
    secs.append((name, va, vsize, rsize, raw))


def rva_to_off(rva):
    for _, va, vsize, rsize, raw in secs:
        if va <= rva < va + max(vsize, rsize):
            return raw + rva - va
    return None


dd = opt + 112
exp_rva, exp_size = struct.unpack_from('<II', dll, dd)
tls_rva, tls_size = struct.unpack_from('<II', dll, dd + 9 * 8)
eo = rva_to_off(exp_rva)
n_names, _, names_rva = struct.unpack_from('<III', dll, eo + 24)
names_off = rva_to_off(names_rva)
exports = set()
for i in range(n_names):
    nrva = struct.unpack_from('<I', dll, names_off + 4 * i)[0]
    o = rva_to_off(nrva)
    exports.add(dll[o:dll.index(b'\0', o)].decode())
check('xtajit.dll exports the entry points wow64.dll translates to the pool copy',
      {'BTCpuProcessInit', 'BTCpuThreadInit', 'BTCpuSimulate', 'BTCpuGetBopCode'} <= exports)
check('xtajit.dll has a TLS directory (its callbacks and entry point run at the PE address in the loader)', tls_rva != 0 and tls_size >= 40)
entry = struct.unpack_from('<I', dll, opt + 16)[0]
check('its entry point is in .text, in the first 16 KB host page', 0x1000 <= entry < 0x4000 and secs[0][0] == '.text')

# --- wow64.dll's translation (needs the wine submodule) ----------------------------
sc = root / 'wine/dlls/wow64/syscall.c'
if sc.exists():
    text = sc.read_text()
    check('wow64.dll runs BTCpuProcessInit/BTCpuThreadInit through jit_pool_code_ptr (the pool copy)',
          'XLATE_PTR( BTCpuProcessInit );' in text and 'XLATE_PTR( BTCpuThreadInit );' in text)
else:
    print('note: no wine submodule here; the wow64.dll translation was not checked')

raise SystemExit(0 if ok else 1)
