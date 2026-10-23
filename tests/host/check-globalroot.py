#!/usr/bin/env python3
r"""Test the real Wine DOS-prefix and drive resolver on GLOBALROOT paths.

Only the final component lookup is replaced with a POSIX file lookup. It
checks the original NT offset and forwarded options, then reads real fixture
files. This runs no Wine process and does not test the object manager or game.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
WINE = Path(os.environ.get('WINE_SRC', ROOT / 'wine'))
spec = importlib.util.spec_from_file_location('globalroot_patch', ROOT / 'tools/patch-wine-globalroot.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
source = (WINE / 'dlls/ntdll/unix/file.c').read_text()
patched = patcher.patch(source)
assert patcher.patch(patched) == patched
baseline = patched.replace(patcher.NEW, patcher.OLD)
assert baseline != patched
for drift in (baseline.replace(patcher.OLD, '', 1), baseline + '\n' + patcher.OLD):
    try:
        patcher.patch(drift)
    except ValueError:
        pass
    else:
        raise AssertionError('missing or ambiguous anchors must fail before writing')


def function(text, signature):
    start = text.index(signature)
    end = text.index('\n}\n', start) + 2
    return text[start:end]


prefix_signature = 'static inline int get_dos_prefix_len('
resolver_signature = 'static NTSTATUS nt_to_unix_file_name_no_root('
# The prefix helper is the only runtime change. This includes every caller.
assert patched.replace(patcher.NEW, patcher.OLD) == baseline
assert function(patched, resolver_signature) == function(baseline, resolver_signature)
assert baseline.count('get_dos_prefix_len( attr->ObjectName )') == 1
ci = (ROOT / '.github/workflows/build-ipa.yml').read_text()
assert ci.index('python3 tests/host/check-globalroot.py') < ci.index('run: bash build/ntdll-unix/build.sh')
assert ci.index('python3 tools/patch-wine-globalroot.py') < ci.index('run: bash build/ntdll-unix/build.sh')
build = (ROOT / 'build/ntdll-unix/build.sh').read_text()
assert build.index('tools/patch-wine-globalroot.py') < build.index('compile_one()')

harness = r'''
#include <assert.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "ntstatus.h"
#include "windef.h"
#include "winternl.h"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define MAX_DIR_ENTRY_LEN 255
#define INVALID_NT_CHARS '*','?','<','>','|','"'
static const char *config_dir = ".";
static const WCHAR nt_prefixW[] = {'\\','?','?','\\'};
static UINT expected_disposition;
static BOOL expected_reparse;
static unsigned int expected_reparse_count;
static const char *relative_file;
static int lookup_calls;

/* These adapters cover ASCII fixture names; Wine's WCHAR remains 16 bits. */
static int ascii_wcsnicmp(const WCHAR *a, const WCHAR *b, size_t n)
{
    for (size_t i = 0; i < n; i++)
    {
        if (a[i] != b[i])
        {
            if (a[i] > 127 || b[i] > 127) return 1;
            if (tolower(a[i]) != tolower(b[i])) return 1;
        }
        if (!a[i]) break;
    }
    return 0;
}
static const WCHAR *short_wcschr(const WCHAR *s, WCHAR c)
{
    do { if (*s == c) return s; } while (*s++);
    return NULL;
}
#define wcsnicmp ascii_wcsnicmp
#define wcschr short_wcschr
static int ntdll_wcstoumbs(const WCHAR *src, unsigned int len, char *dst,
                          unsigned int capacity, BOOL strict)
{
    (void)strict;
    if (len > capacity) return -1;
    for (unsigned int i = 0; i < len; i++)
    {
        assert(src[i] < 128);
        dst[i] = src[i];
    }
    return len;
}
static NTSTATUS get_dos_device(char **name, int pos)
{
    (void)pos;
    free(*name); *name = NULL;
    return STATUS_BAD_DEVICE_TYPE;
}
static NTSTATUS lookup_unix_name(int root_fd, OBJECT_ATTRIBUTES *attr, UNICODE_STRING *nt,
                                unsigned int nt_pos, char **buffer, int capacity, int pos,
                                UINT disposition, BOOL reparse, BOOL is_unix, unsigned int count)
{
    (void)nt;
    lookup_calls++;
    assert(root_fd == AT_FDCWD && !is_unix);
    assert(disposition == expected_disposition && reparse == expected_reparse);
    assert(count == expected_reparse_count && relative_file);
    unsigned int len = attr->ObjectName->Length / sizeof(WCHAR);
    assert(nt_pos <= len && len - nt_pos == strlen(relative_file));
    assert(pos + 1 + (int)strlen(relative_file) < capacity);
    char *path = *buffer;
    path[pos++] = '/';
    for (unsigned int i = nt_pos; i < len; i++)
    {
        WCHAR c = attr->ObjectName->Buffer[i];
        assert(c < 128);
        if (c == '\\') c = '/';
        assert(c == relative_file[i - nt_pos]);
        path[pos++] = c;
    }
    path[pos] = 0;
    struct stat st;
    if (stat(path, &st)) return STATUS_OBJECT_NAME_NOT_FOUND;
    return disposition == FILE_CREATE ? STATUS_OBJECT_NAME_COLLISION : STATUS_SUCCESS;
}
@FUNCTIONS@

/* Allocate exactly Length bytes: the input is deliberately not NUL-terminated. */
static UNICODE_STRING wide_n(const char *s, size_t n)
{
    assert(n <= 32767);
    WCHAR *buffer = malloc(n * sizeof(WCHAR));
    assert(buffer || !n);
    for (size_t i = 0; i < n; i++) buffer[i] = (unsigned char)s[i];
    return (UNICODE_STRING){n * sizeof(WCHAR), n * sizeof(WCHAR), buffer};
}
static int resolve(const char *request, NTSTATUS expected, const char *file,
                   UINT disposition, BOOL reparse, unsigned int count)
{
    UNICODE_STRING name = wide_n(request, strlen(request)), nt = {0};
    OBJECT_ATTRIBUTES attr = {0};
    attr.Length = sizeof(attr); attr.ObjectName = &name;
    attr.Attributes = OBJ_CASE_INSENSITIVE;
    OBJECT_ATTRIBUTES saved = attr;
    char *unix_name = NULL;
    expected_disposition = disposition; expected_reparse = reparse;
    expected_reparse_count = count; relative_file = file;
    int before = lookup_calls;
    NTSTATUS status = nt_to_unix_file_name_no_root(&attr, &nt, &unix_name, disposition, reparse, count);
    if (status != expected)
    {
        fprintf(stderr, "GLOBALROOT resolver: %s returned %08x, expected %08x\n",
                request, (unsigned int)status, (unsigned int)expected);
        free(unix_name); free(name.Buffer); free(nt.Buffer);
        return 1;
    }
    assert(!memcmp(&attr, &saved, sizeof(attr)) && !nt.Buffer);
    for (size_t i = 0; i < strlen(request); i++) assert(name.Buffer[i] == (unsigned char)request[i]);
    if (!status)
    {
        struct stat actual, expected_stat;
        assert(lookup_calls == before + 1 && unix_name && file);
        assert(!stat(unix_name, &actual) && !stat(file, &expected_stat));
        assert(actual.st_dev == expected_stat.st_dev && actual.st_ino == expected_stat.st_ino);
        FILE *f = fopen(unix_name, "rb"); assert(f);
        char bytes[16] = {0}; assert(fread(bytes, 1, sizeof(bytes) - 1, f) == 7);
        assert(!strcmp(bytes, "fixture") && !fclose(f));
    }
    else assert(!unix_name);
    free(name.Buffer);
    return 0;
}
static void prefix(const char *text, int expected)
{
    UNICODE_STRING name = wide_n(text, strlen(text));
    assert(get_dos_prefix_len(&name) == expected);
    free(name.Buffer);
}
int main(void)
{
    _Static_assert(sizeof(WCHAR) == 2, "Windows UTF-16 ABI");
    const char *base = "Program Files (x86)/Steam/steamapps/common/Minecraft Dungeons II/Dungeons/Binaries/Win64/";
    const char *names[] = {"Splitmap-Shipping.dat", "Dungeons-Win64-Shipping.exe"};
    const char *wrappers[] = {"\\??\\", "\\DosDevices\\", "\\??\\GLOBALROOT\\??\\",
        "\\??\\gLoBaLrOoT\\dOsDeViCeS\\", "\\DosDevices\\GLOBALROOT\\??\\",
        "\\??\\GLOBALROOT\\??\\GLOBALROOT\\??\\"};
    for (size_t i = 0; i < ARRAY_SIZE(names); i++)
    {
        char file[512]; snprintf(file, sizeof(file), "%s%s", base, names[i]);
        for (size_t j = 0; j < ARRAY_SIZE(wrappers); j++)
        {
            char request[1024]; snprintf(request, sizeof(request), "%sC:\\%s", wrappers[j], file);
            for (char *p = request; *p; p++) if (*p == '/') *p = '\\';
            NTSTATUS expected = STATUS_SUCCESS;
#ifndef WINE_IOS
            if (j >= 2) expected = STATUS_BAD_DEVICE_TYPE;
#endif
            if (resolve(request, expected, file, FILE_OPEN, FALSE, 0)) return 1;
            if (!expected && resolve(request, expected, file, FILE_OPEN_IF, TRUE, 7)) return 1;
        }
    }
    prefix("", 0);
    prefix("\\??\\", 4);
    prefix("\\??\\C:\\GLOBALROOT\\x", 4);
    prefix("\\??\\GLOBALROOT", 4);
    prefix("\\??\\GLOBALROOTsuffix\\??\\C:\\x", 4);
    prefix("\\??\\GLOBALROOT/??\\C:\\x", 4);
#ifdef WINE_IOS
    prefix("\\??\\GLOBALROOT\\??\\C:\\x", 18);
    prefix("\\??\\GLOBALROOT\\DosDevices\\C:\\x", 26);
    prefix("\\??\\GLOBALROOT\\", 0);
    prefix("\\??\\GLOBALROOT\\??\\", 18);
    prefix("\\??\\GLOBALROOT\\Device\\Null", 0);
#endif
    const char *native[] = {"\\Device\\Null", "\\??\\GLOBALROOT\\Device\\Null",
        "\\??\\GLOBALROOTsuffix\\??\\C:\\x", "\\??\\GLOBALROOT\\", "\\??\\GLOBALROOT"};
    for (size_t i = 0; i < ARRAY_SIZE(native); i++)
        if (resolve(native[i], STATUS_BAD_DEVICE_TYPE, NULL, FILE_OPEN, FALSE, 0)) return 1;
    const char *truncated = "\\??\\GLOBALROOT\\??\\GLOBALROOT\\DosDevices\\C:\\x";
    for (size_t i = 0; i <= strlen(truncated); i++)
    {
        UNICODE_STRING name = wide_n(truncated, i);
        int n = get_dos_prefix_len(&name);
        assert(n >= 0 && (unsigned int)n <= i);
        free(name.Buffer);
    }
    puts("PASS: drive paths read the same inode and contents; offsets/options/names preserved; native fallback, lookalikes and bounded UTF-16 inputs");
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='bcd-globalroot-') as temp:
    temp = Path(temp)
    directory = temp / 'Program Files (x86)/Steam/steamapps/common/Minecraft Dungeons II/Dungeons/Binaries/Win64'
    directory.mkdir(parents=True)
    for filename in ('Splitmap-Shipping.dat', 'Dungeons-Win64-Shipping.exe'):
        (directory / filename).write_text('fixture')
    (temp / 'dosdevices').mkdir()
    (temp / 'dosdevices/c:').symlink_to('..')
    env = {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=' + ('0' if sys.platform == 'darwin' else '1')}
    for label, text, ios in (('baseline', baseline, True), ('fixed', patched, True), ('non-ios', patched, False)):
        functions = function(text, prefix_signature) + '\n\n' + function(text, resolver_signature)
        unit = temp / (label + '.c')
        unit.write_text(harness.replace('@FUNCTIONS@', functions))
        binary = temp / label
        command = [os.environ.get('CC', 'cc'), '-std=c11', '-D_GNU_SOURCE', '-D_WIN64', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-O1', '-I', str(WINE / 'include')]
        if ios:
            command.append('-DWINE_IOS=1')
        subprocess.run(command + [str(unit), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], cwd=temp, env=env, capture_output=True, text=True)
        if label == 'baseline':
            assert result.returncode == 1 and 'GLOBALROOT resolver:' in result.stderr and 'c00000cb' in result.stderr, result.stderr
            print('PASS: unpatched Wine reproduces the GLOBALROOT path failure before component lookup')
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            print(label + ': ' + result.stdout.strip())
print('PASS: patch is idempotent, rejects anchor drift, and runs before compilation in CI and local builds')
