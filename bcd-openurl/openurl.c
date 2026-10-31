// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// winebrowser.exe of BCD (round 625, the owner's item 23): what a Windows program runs to open a web page (core.h says why and what it
// takes). Built with llvm-mingw for the three farms (tools/build-bcd-openurl.sh): x86-64 into arm64ec-windows, aarch64 into
// aarch64-windows, i686 into i386-windows in place of Wine's own winebrowser.exe, which can start no browser on iOS. It opens no window;
// it writes one small file and a line to the debug output (the engine mirrors that into BCD's log), and ends at once, as a browser
// that was started does. The page opens only if the player says so in BCD.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "core.h"

#define FOLDER_PARENT "C:\\ProgramData\\BCD"
#define FOLDER        "C:\\ProgramData\\BCD\\OpenURL"

static void say(const char *line) { OutputDebugStringA(line); }

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    static char line[4 * (OU_MAX_URL + 600)], url[OU_MAX_URL + 1];
    char host[128], name[64], part[MAX_PATH], done[MAX_PATH], env[8], text[256];
    const WCHAR *wide = GetCommandLineW();
    DWORD got, wrote;
    HANDLE h;
    int n;
    (void)inst; (void)prev; (void)cmd; (void)show;

    got = GetEnvironmentVariableA("MADEIRA_OPEN_URL", env, sizeof env);
    if (got && got < sizeof env && ou_off(env)) {
        say("[open-url] madeira-bcd: a program asked to open a page; MADEIRA_OPEN_URL=0, so it is not passed to BCD\n");
        return 1;
    }
    n = wide ? WideCharToMultiByte(CP_UTF8, 0, wide, -1, line, (int)sizeof line, NULL, NULL) : 0;
    if (n <= 0 || !ou_argument(line, url, sizeof url)) {
        say("[open-url] madeira-bcd: a program asked to open something that is not one address of at most 2048 characters\n");
        return 1;
    }
    if (!ou_is_page(url)) {
        say("[open-url] madeira-bcd: a program asked to open something other than an http or https page (a file, mail or ftp); "
            "BCD opens only web pages\n");
        return 1;
    }
    ou_host(url, host, sizeof host);
    CreateDirectoryA(FOLDER_PARENT, NULL);
    CreateDirectoryA(FOLDER, NULL);
    if (!ou_file_name(name, sizeof name, (unsigned long long)GetTickCount64(), (unsigned long)GetCurrentProcessId()) ||
        snprintf(part, sizeof part, "%s\\%s.part", FOLDER, name) <= 0 || snprintf(done, sizeof done, "%s\\%s", FOLDER, name) <= 0)
        return 1;
    h = CreateFileA(part, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        snprintf(text, sizeof text, "[open-url] madeira-bcd: could not write the request for %s (error %lu)\n", host, GetLastError());
        say(text);
        return 1;
    }
    {
        size_t len = strlen(url);
        BOOL ok = WriteFile(h, url, (DWORD)len, &wrote, NULL) && wrote == len && WriteFile(h, "\n", 1, &wrote, NULL);
        CloseHandle(h);
        /* the app takes only a finished ".url" file, never a half-written one */
        if (!ok || !MoveFileExA(part, done, MOVEFILE_REPLACE_EXISTING)) {
            DeleteFileA(part);
            snprintf(text, sizeof text, "[open-url] madeira-bcd: could not write the request for %s (error %lu)\n", host, GetLastError());
            say(text);
            return 1;
        }
    }
    snprintf(text, sizeof text, "[open-url] madeira-bcd: a program asks to open a page on %s (%u characters); BCD asks the player\n",
             host, (unsigned)strlen(url));
    say(text);
    return 0;
}
