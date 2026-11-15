#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""MessageBoxTimeoutA / MessageBoxTimeoutW honour their timeout (round 660, M106; keyless: a plain gap).

user32.dll's MessageBoxTimeoutA/W (an undocumented API with the signature of MessageBoxEx plus a time in milliseconds) logs
"fixme:dialog:MessageBoxTimeoutA timeout not supported (10000)" and shows an ordinary box, so a program that relies on the box going away by
itself waits for ever: Ultimate Chicken Horse's startup "Credits" box (84 s in the log) and Slay the Spire 2 (build 611, CGEZSY; 2 of about 4,000 logs).
Both are 64-bit programs.

With a time in milliseconds greater than 0 and not INFINITE (and below 2^31, SetTimer's limit), the box starts a timer on its dialog (id 0x4d42) when
it is initialised; the timer ends the dialog with MB_TIMEDOUT (32000), which is what Windows returns. 0 and INFINITE give the box as it is today
(no timer), and every other message box entry point (MessageBoxA/W, MessageBoxExA/W, MessageBoxIndirectA/W) passes 0: its behaviour does not change.
MessageBoxTimeoutA converts like MessageBoxExA does and forwards to the same code. The FIXME is gone (nothing is unsupported any more).

Implementation: the dialog parameter becomes a copy of the MSGBOXPARAMSW followed by the time (struct madeira_msgbox_args, the copy first, so the
dialog procedure's cast to LPMSGBOXPARAMSW stays valid); MessageBoxIndirectA/W become thin wrappers of static msgbox_indirect_a/w that take the time.

WHERE THIS REACHES: the 32-bit user32 through the i386 farm, and the 64-bit user32 through the rebuilt set (tools/build-wine-fresh-dlls.sh,
env.MADEIRA_WIN64_FRESH = 1); the committed 64-bit user32.dll is a frozen binary.
Applies on the pinned file and on the file after tools/patch-wine-cpu-error-diag.py (the other step of the workflow that edits msgbox.c).
Idempotent; fails by name when an anchor moved.
Usage: patch-wine-msgbox-timeout.py WINE-DIR | path/to/wine/dlls/user32/msgbox.c
"""
from pathlib import Path
import sys

FILE = "dlls/user32/msgbox.c"
MARKER = "madeira-bcd: MessageBoxTimeout"

DECL_ANCHOR = """/**************************************************************************
 *           MSGBOX_DlgProc
 *
 * Dialog procedure for message boxes.
 */
"""
DECL_NEW = """/* madeira-bcd: MessageBoxTimeout (tools/patch-wine-msgbox-timeout.py, round 660 M106): the dialog's parameter is a copy of the caller's
 * MSGBOXPARAMSW (first, so a cast to LPMSGBOXPARAMSW stays valid) and the time in milliseconds; 0 = no timer. */
#define MADEIRA_MSGBOX_TIMER    0x4d42
#define MADEIRA_MB_TIMEDOUT     32000
struct madeira_msgbox_args
{
    MSGBOXPARAMSW mbp;
    DWORD         timeout;
};
static INT msgbox_indirect_a( LPMSGBOXPARAMSA msgbox, DWORD timeout );
static INT msgbox_indirect_w( LPMSGBOXPARAMSW msgbox, DWORD timeout );

"""

INIT_OLD = """       SetPropA(hwnd, "WINE_MSGBOX_HELPCALLBACK", mbp->lpfnMsgBoxCallback);
       break;
   }
"""
INIT_NEW = """       SetPropA(hwnd, "WINE_MSGBOX_HELPCALLBACK", mbp->lpfnMsgBoxCallback);
       {   /* madeira-bcd: MessageBoxTimeout */
           DWORD timeout = ((struct madeira_msgbox_args *)lParam)->timeout;
           if (timeout && timeout != INFINITE && timeout <= 0x7fffffff)
               SetTimer(hwnd, MADEIRA_MSGBOX_TIMER, timeout, NULL);
       }
       break;
   }

   case WM_TIMER:
    if (wParam == MADEIRA_MSGBOX_TIMER)   /* madeira-bcd: MessageBoxTimeout */
    {
        NtUserKillTimer(hwnd, MADEIRA_MSGBOX_TIMER);   /* user32.spec forwards KillTimer to this */
        EndDialog(hwnd, MADEIRA_MB_TIMEDOUT);
    }
    break;
"""

TIMEOUT_A_OLD = """INT WINAPI MessageBoxTimeoutA( HWND hWnd, LPCSTR text, LPCSTR title,
                               UINT type, WORD langid, DWORD timeout )
{
    FIXME("timeout not supported (%lu)\\n", timeout);
    return MessageBoxExA( hWnd, text, title, type, langid );
}
"""
TIMEOUT_A_NEW = """INT WINAPI MessageBoxTimeoutA( HWND hWnd, LPCSTR text, LPCSTR title,
                               UINT type, WORD langid, DWORD timeout )
{
    MSGBOXPARAMSA msgbox;

    msgbox.cbSize = sizeof(msgbox);
    msgbox.hwndOwner = hWnd;
    msgbox.hInstance = 0;
    msgbox.lpszText = text;
    msgbox.lpszCaption = title;
    msgbox.dwStyle = type;
    msgbox.lpszIcon = NULL;
    msgbox.dwContextHelpId = 0;
    msgbox.lpfnMsgBoxCallback = NULL;
    msgbox.dwLanguageId = langid;

    return msgbox_indirect_a( &msgbox, timeout );   /* madeira-bcd: MessageBoxTimeout */
}
"""
TIMEOUT_W_OLD = """INT WINAPI MessageBoxTimeoutW( HWND hWnd, LPCWSTR text, LPCWSTR title,
                               UINT type, WORD langid, DWORD timeout )
{
    FIXME("timeout not supported (%lu)\\n", timeout);
    return MessageBoxExW( hWnd, text, title, type, langid );
}
"""
TIMEOUT_W_NEW = """INT WINAPI MessageBoxTimeoutW( HWND hWnd, LPCWSTR text, LPCWSTR title,
                               UINT type, WORD langid, DWORD timeout )
{
    MSGBOXPARAMSW msgbox;

    msgbox.cbSize = sizeof(msgbox);
    msgbox.hwndOwner = hWnd;
    msgbox.hInstance = 0;
    msgbox.lpszText = text;
    msgbox.lpszCaption = title;
    msgbox.dwStyle = type;
    msgbox.lpszIcon = NULL;
    msgbox.dwContextHelpId = 0;
    msgbox.lpfnMsgBoxCallback = NULL;
    msgbox.dwLanguageId = langid;

    return msgbox_indirect_w( &msgbox, timeout );   /* madeira-bcd: MessageBoxTimeout */
}
"""

IND_A_OLD = """INT WINAPI MessageBoxIndirectA( LPMSGBOXPARAMSA msgbox )
{
    MSGBOXPARAMSW msgboxW;"""
IND_A_NEW = """INT WINAPI MessageBoxIndirectA( LPMSGBOXPARAMSA msgbox )
{
    return msgbox_indirect_a( msgbox, 0 );
}

static INT msgbox_indirect_a( LPMSGBOXPARAMSA msgbox, DWORD timeout )
{
    MSGBOXPARAMSW msgboxW;"""
IND_A_CALL_OLD = "    ret = MessageBoxIndirectW(&msgboxW);\n"
IND_A_CALL_NEW = "    ret = msgbox_indirect_w(&msgboxW, timeout);\n"

IND_W_OLD = """INT WINAPI MessageBoxIndirectW( LPMSGBOXPARAMSW msgbox )
{
    LPVOID tmplate;"""
IND_W_NEW = """INT WINAPI MessageBoxIndirectW( LPMSGBOXPARAMSW msgbox )
{
    return msgbox_indirect_w( msgbox, 0 );
}

static INT msgbox_indirect_w( LPMSGBOXPARAMSW msgbox, DWORD timeout )
{
    struct madeira_msgbox_args args;
    LPVOID tmplate;"""
DLG_OLD = """    ret=DialogBoxIndirectParamW(msgbox->hInstance, tmplate,
                                msgbox->hwndOwner, MSGBOX_DlgProc, (LPARAM)msgbox);
"""
DLG_NEW = """    args.mbp = *msgbox;   /* madeira-bcd: MessageBoxTimeout */
    args.timeout = timeout;
    ret=DialogBoxIndirectParamW(msgbox->hInstance, tmplate,
                                msgbox->hwndOwner, MSGBOX_DlgProc, (LPARAM)&args);
"""

EDITS = [
    (DECL_ANCHOR, DECL_NEW + DECL_ANCHOR),
    (INIT_OLD, INIT_NEW),
    (TIMEOUT_A_OLD, TIMEOUT_A_NEW),
    (TIMEOUT_W_OLD, TIMEOUT_W_NEW),
    (IND_A_OLD, IND_A_NEW),
    (IND_A_CALL_OLD, IND_A_CALL_NEW),
    (IND_W_OLD, IND_W_NEW),
    (DLG_OLD, DLG_NEW),
]


def main():
    arg = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = arg if arg.suffix == ".c" else arg / FILE
    text = path.read_text()
    if MARKER in text:
        print("MessageBoxTimeout: already patched")
        return
    for old, _ in EDITS:
        if text.count(old) != 1:
            sys.exit("patch-wine-msgbox-timeout: an anchor was not found exactly once in %s: %r" % (path, old.strip().splitlines()[0]))
    for old, new in EDITS:
        text = text.replace(old, new)
    path.write_text(text)
    print("MessageBoxTimeout: patched")


if __name__ == "__main__":
    main()
