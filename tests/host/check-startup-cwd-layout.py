#!/usr/bin/env python3
# Copyright 2026 Jelly-RayTian. This file was taken from https://github.com/willfaust/Madeira (pull request #471)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""Execute env_ios.c's initial/child startup packing with long UTF-16 CWDs.
The allocation expressions, CWD packing and following string writes are taken
from production, with only the NT allocator replaced by exact-size malloc.

madeira-bcd: taken from upstream Madeira (16b3382, #471, Jelly-RayTian; GPL-3.0-or-later with the Madeira Converter Exception).
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/env_ios.c').read_text()

def function(name):
    start = source.index(name)
    brace = source.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

initial = function('static RTL_USER_PROCESS_PARAMETERS *build_initial_params(')
child = function('void unix_init_startup_info(')
helpers = '\n'.join(function(name) for name in (
    'static inline void copy_unicode_string(', 'static inline void put_unicode_string(',
    'static void copy_dos_path_string(', 'static inline WCHAR *get_dos_path('))

def between(text, start, end):
    return text[text.index(start) + len(start):text.index(end, text.index(start))]

initial_size = between(initial, 'debugstr_w(curdir) );', '    status = NtAllocateVirtualMemory(')
child_size = between(child, '    env[env_pos++] = 0;', '    status = NtAllocateVirtualMemory(')
initial_copy = initial[initial.index('    params->CurrentDirectory.DosPath.Buffer ='):initial.index('    free( nt_name.Buffer );', initial.index('    params->CurrentDirectory.DosPath.Buffer ='))]
child_copy = between(child, '    /* curdir is special */\n', '    if (info->runtime_len)')

program = r'''
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
typedef uint16_t WCHAR;
typedef uint16_t USHORT;
typedef unsigned int UINT;
typedef size_t SIZE_T;
#define MAX_PATH 260
#define max(a,b) ((a) > (b) ? (a) : (b))
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct {
    struct { UNICODE_STRING DosPath; } CurrentDirectory;
    UNICODE_STRING DllPath, ImagePathName, CommandLine, WindowTitle, Desktop, ShellInfo;
} RTL_USER_PROCESS_PARAMETERS;
struct startup_info_data {
    UINT curdir_len, dllpath_len, imagepath_len, cmdline_len, title_len, desktop_len, shellinfo_len, runtime_len;
};
static size_t wcslen(const WCHAR *p) { const WCHAR *end = p; while (*end) end++; return end - p; }
static void wcscpy(WCHAR *dst, const WCHAR *src) { while ((*dst++ = *src++)) {} }
static const WCHAR image[] = {'C',':','\\','g','.','e','x','e',0};
static const WCHAR nt_image[] = {'\\','?','?','\\','C',':','\\','g','.','e','x','e',0};
static const WCHAR command[] = {'g','.','e','x','e',' ','-','x',0};
static WCHAR *path_for(size_t units) {
    WCHAR *path = calloc(units + 1, sizeof(WCHAR));
    for (size_t i = 0; i < units; i++) path[i] = (i % 29 == 28) ? '\\' : (WCHAR)(0x4e00 + i % 100);
    if (units >= 3) { path[0] = 'C'; path[1] = ':'; path[2] = '\\'; }
    if (units > 20) { path[10] = 0xd83c; path[11] = 0xdfae; }
    return path;
}
static int verify(RTL_USER_PROCESS_PARAMETERS *params, const WCHAR *expected, const char *route, size_t units) {
    UNICODE_STRING *cwd = &params->CurrentDirectory.DosPath;
    size_t bytes = (units + 1) * sizeof(WCHAR);
    if (cwd->Length != units * sizeof(WCHAR) || cwd->MaximumLength < bytes ||
        memcmp(cwd->Buffer, expected, bytes) ||
        (char *)params->ImagePathName.Buffer < (char *)cwd->Buffer + cwd->MaximumLength ||
        memcmp(params->ImagePathName.Buffer, image, sizeof(image)) ||
        memcmp(params->CommandLine.Buffer, command, sizeof(command)) ||
        memcmp(params->WindowTitle.Buffer, image, sizeof(image))) {
        fprintf(stderr, "FAIL %s CWD layout at %zu UTF-16 units: length=%u capacity=%u required=%zu\n",
                route, units, cwd->Length, cwd->MaximumLength, bytes);
        return 1;
    }
    if (units < MAX_PATH && cwd->MaximumLength != MAX_PATH * sizeof(WCHAR)) return 1;
    return 0;
}
''' + helpers + r'''
static int initial_case(size_t units, int unc) {
    SIZE_T size, env_pos = 16, curdir_size = 0;
    RTL_USER_PROCESS_PARAMETERS *params;
    WCHAR *expected = path_for(units), *curdir = calloc(units + 5, sizeof(WCHAR));
    WCHAR *main_wargv[] = {(WCHAR *)image}, *cmdline = (WCHAR *)command, *dst, *dos_curdir;
    if (unc) {
        const WCHAR prefix[] = {'\\','\\','?','\\','U','N','C','\\'};
        memcpy(expected, prefix, sizeof(prefix));
        memcpy(curdir, expected, (units + 1) * sizeof(WCHAR)); curdir[1] = '?';
    } else {
        curdir[0] = '\\'; curdir[1] = '?'; curdir[2] = '?'; curdir[3] = '\\';
        memcpy(curdir + 4, expected, (units + 1) * sizeof(WCHAR));
    }
''' + initial_size + r'''
    (void)curdir_size; (void)dos_curdir;
    params = calloc(1, size);
''' + initial_copy + r'''
    int failed = verify(params, expected, "initial", units);
    free(params); free(curdir); free(expected);
    return failed;
}
static int child_case(size_t units) {
    SIZE_T size, env_pos = 16, curdir_size = 0;
    RTL_USER_PROCESS_PARAMETERS *params;
    struct startup_info_data storage = {0}, *info = &storage;
    WCHAR *expected = path_for(units), *src, *dst;
    UNICODE_STRING nt_name;
    info->curdir_len = units * sizeof(WCHAR);
    info->imagepath_len = sizeof(nt_image) - sizeof(WCHAR);
    info->cmdline_len = sizeof(command) - sizeof(WCHAR);
    info->title_len = sizeof(image) - sizeof(WCHAR);
    size_t input_size = info->curdir_len + info->imagepath_len + info->cmdline_len + info->title_len;
    WCHAR *input = calloc(1, input_size + sizeof(WCHAR));
    memcpy(input, expected, info->curdir_len);
    memcpy((char *)input + info->curdir_len, nt_image, info->imagepath_len);
    memcpy((char *)input + info->curdir_len + info->imagepath_len, command, info->cmdline_len);
    memcpy((char *)input + info->curdir_len + info->imagepath_len + info->cmdline_len, image, info->title_len);
''' + child_size + r'''
    (void)curdir_size;
    params = calloc(1, size); src = input; dst = (WCHAR *)(params + 1);
''' + child_copy + r'''
    int failed = verify(params, expected, "child", units);
    if ((char *)src != (char *)input + input_size) failed = 1;
    free(nt_name.Buffer); free(params); free(input); free(expected);
    return failed;
}
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    size_t units = strtoul(argv[2], NULL, 10);
    int result = !strcmp(argv[1], "child") ? child_case(units) : initial_case(units, !strcmp(argv[1], "initial-unc"));
    if (!result) printf("PASS %s CWD layout at %zu UTF-16 units\n", argv[1], units);
    return result;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-startup-cwd-') as directory:
    src = Path(directory) / 'test.c'
    exe = Path(directory) / 'test'
    src.write_text(program)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer', str(src), '-o', str(exe)], check=True)
    failed = 0
    cases = [(route, units) for route in ('initial', 'child')
             for units in (3, 10, 100, 258, 259, 260, 261, 300, 1024, 32766)]
    cases += [('child', 0)] + [('initial-unc', units) for units in (100, 260, 1024)]
    for route, units in cases:
        result = subprocess.run([str(exe), route, str(units)], capture_output=True, text=True)
        print(result.stdout, end='')
        if result.returncode:
            print(result.stderr[:2200])
            failed += 1
    if failed:
        raise SystemExit(f'{failed} startup CWD layouts failed')
    print('PASS 24 initial/child layouts (drive and extended UNC paths), long UTF-16 paths, following strings and source advancement (ASan/UBSan)')
