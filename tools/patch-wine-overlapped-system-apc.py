#!/usr/bin/env python3
"""Let opt-in nonblocking I/O polls dispatch system completions on iOS."""
from pathlib import Path
import sys

PE = '''/* overlapped-system-apc:begin -- a nonblocking poll still needs kernel I/O completion delivery. */
static NTSTATUS madeira_overlapped_poll( OVERLAPPED *overlapped, NTSTATUS status )
{
    static LONG enabled = -1;
    DWORD error;

    if (status != STATUS_PENDING) return status;
    error = GetLastError();
    if (ReadAcquire( &enabled ) == -1)
    {
        WCHAR value[2];
        DWORD len = GetEnvironmentVariableW( L"MADEIRA_OVERLAPPED_SYSTEM_APC", value, ARRAY_SIZE(value) );
        InterlockedCompareExchange( &enabled, len == 1 && value[0] == '1', -1 );
    }
    if (ReadAcquire( &enabled ))
    {
        static LONG witnessed;
        LONG first = !InterlockedCompareExchange( &witnessed, 1, 0 );
        NtYieldExecution();
        status = ReadAcquire( (LONG *)&overlapped->Internal );
        if (first) ERR( "[overlapped-system-apc] pid=%04lx tid=%04lx first nonblocking pending poll; after=%08lx\\n",
                        GetCurrentProcessId(), GetCurrentThreadId(), status );
    }
    SetLastError( error );
    return status;
}
/* overlapped-system-apc:end */

'''

NATIVE = '''#ifdef WINE_IOS
/* overlapped-system-apc:begin -- dispatch system completions without an alertable wait or consuming an event. */
static void ios_yield_system_apcs(void)
{
    static int enabled = -1;
    static __thread int active;
    int saved_errno = errno;
    struct user_apc apc;
    extern int ios_alert_exit_safe(void);

    if (__atomic_load_n( &enabled, __ATOMIC_ACQUIRE ) == -1)
    {
        const char *value = getenv( "MADEIRA_OVERLAPPED_SYSTEM_APC" );
        __atomic_store_n( &enabled, value && value[0] == '1' && !value[1], __ATOMIC_RELEASE );
    }
    if (__atomic_load_n( &enabled, __ATOMIC_ACQUIRE ) && !active && ios_alert_exit_safe())
    {
        active = 1;
        /* server_wait() yields on timeout; use the lower-level loop to avoid recursion. */
        server_select( NULL, 0, SELECT_INTERRUPTIBLE, 0, NULL, &apc );
        active = 0;
    }
    errno = saved_errno;
}
/* overlapped-system-apc:end */
#endif

'''

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('overlapped-system-apc: expected one anchor: ' + old[:100])
    return text.replace(old, new, 1)

def patch_kernelbase(text):
    if '/* overlapped-system-apc:begin' in text:
        assert PE in text and text.count('status = madeira_overlapped_poll(') == 2
        return text
    marker = '/***********************************************************************\n *\tGetOverlappedResult   (kernelbase.@)'
    text = replace_once(text, marker, PE + marker)
    first = text.index('BOOL WINAPI DECLSPEC_HOTPATCH GetOverlappedResult(')
    second = text.index('BOOL WINAPI DECLSPEC_HOTPATCH GetOverlappedResultEx(')
    load = '    status = ReadAcquire( (LONG *)&overlapped->Internal );'
    a = replace_once(text[first:second], load, load + '\n    if (!wait) status = madeira_overlapped_poll( overlapped, status );')
    b = replace_once(text[second:], '    ' + load, '    ' + load + '\n        if (!timeout) status = madeira_overlapped_poll( overlapped, status );')
    return text[:first] + a + b

def patch_sync(text):
    if '/* overlapped-system-apc:begin' in text:
        assert NATIVE in text and text.count('    ios_yield_system_apcs();') == 1
        return text
    marker = '/******************************************************************\n *\t\tNtYieldExecution (NTDLL.@)'
    text = replace_once(text, marker, NATIVE + marker)
    return replace_once(text, 'NTSTATUS WINAPI NtYieldExecution(void)\n{',
                        'NTSTATUS WINAPI NtYieldExecution(void)\n{\n#ifdef WINE_IOS\n    ios_yield_system_apcs();\n#endif')

def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: patch-wine-overlapped-system-apc.py WINE_ROOT')
    root = Path(sys.argv[1])
    paths = [(root/'dlls/ntdll/unix/sync.c', patch_sync),
             (root/'dlls/kernelbase/file.c', patch_kernelbase)]
    changes = [(p, p.read_text(), fn(p.read_text())) for p, fn in paths]
    for path, old, new in changes:
        if new != old: path.write_text(new)
        print('overlapped-system-apc:', path, 'patched' if new != old else 'already patched')

if __name__ == '__main__':
    main()
