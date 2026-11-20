// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDStartMenu.c
//
//  BCD's Start menu shortcuts (BCDStartMenu.h). The file layout, all integers
//  little-endian ([MS-SHLLINK]):
//
//    ShellLinkHeader  76 bytes: size 0x4C, the CLSID 00021401-0000-0000-C000-000000000046,
//                     LinkFlags, file attributes, three FILETIMEs, the target's size, the icon
//                     index, the show command, a hot key and three reserved fields.
//    LinkInfo         (HasLinkInfo) size, header size 0x1C, flags (1: VolumeIDAndLocalBasePath),
//                     the offsets of the VolumeID, the local base path, the network block (none)
//                     and the common path suffix; the VolumeID (size, drive type, serial, label
//                     offset, label); the base path and the suffix, null-ended ANSI strings.
//    StringData       the name, relative path, working folder, arguments and icon location that
//                     the flags announce, each a 16-bit character count and that many UTF-16
//                     characters (IsUnicode).
//    ExtraData        a terminal block: four zero bytes.
//
//  Wine's loader (shell32 shelllink.c) reads the header, an item ID list when the flags have one,
//  the LinkInfo, the strings, then one more 32-bit word that should be zero; it takes the target
//  from the LinkInfo's local path. BCD's links carry no item ID list and no relative path.

#include "BCDStartMenu.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* ---------------------------------------------------------------- the entries */

#define BCD_TESTS "Programs/BCD Tests"
#define BCD_TOOLS "Programs/BCD Tools"
#define BCD_RUNTIMES "Programs/BCD Runtimes"     /* round 603 */
#define BCD_7ZIP "Programs/7-Zip"                /* round 603 */

/* The entries may leave out the last two fields (arguments, flags): they are NULL and 0 then, on purpose. */
#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wmissing-field-initializers"
#endif

const bcd_start_entry bcd_start_entries[] = {
    { "",         "Explorer",        "explorer.exe",        "Opens Wine's file manager" },
    /* The graphics, sound, video and input tests (round 602, tests/dxtests). The 32-bit programs (*-x86.exe) are found in the 32-bit
     * set (syswow64) by where the app carries them: most Direct3D 9 games are 32-bit, and Direct3D 8 and DirectDraw games all are.
     * "DirectX 9 test" is the 32-bit one; the 64-bit one keeps the old program and is called "DirectX 9 test (64-bit)". */
    { BCD_TESTS,  "DirectX 8 test",  "d3d8-test-x86.exe",   "A spinning cube drawn with Direct3D 8 (32-bit)" },
    { BCD_TESTS,  "DirectX 9 test",  "d3d9-test-x86.exe",   "A spinning cube drawn with Direct3D 9 (32-bit)" },
    { BCD_TESTS,  "DirectX 9 test (64-bit)", "d3d9-cube-x64.exe", "A spinning cube drawn with Direct3D 9 (64-bit)" },
    { BCD_TESTS,  "DirectX 10 test", "d3d10-cube-x64.exe",  "A spinning cube drawn with Direct3D 10 (64-bit)" },
    { BCD_TESTS,  "DirectX 11 test", "d3d11-cube-x64.exe",  "A spinning cube drawn with Direct3D 11 (64-bit)" },
    { BCD_TESTS,  "DirectX 12 test", "d3d12-cube-x64.exe",  "A spinning cube drawn with Direct3D 12 (64-bit)" },
    /* The OpenGL and Vulkan tests (tests/dxtests/gl_test.c, vulkan_test.c): the same cube. A session has OpenGL or Vulkan only when
     * the Windows desktop's own Options turn it on (Direct3D > OpenGL, Vulkan); without it the test says so and how. */
    { BCD_TESTS,  "OpenGL test",     "gl-test-x64.exe",     "A spinning cube drawn with OpenGL (64-bit)" },
    { BCD_TESTS,  "OpenGL test (32-bit)", "gl-test-x86.exe", "A spinning cube drawn with OpenGL (32-bit)" },
    { BCD_TESTS,  "Vulkan test",     "vulkan-test-x64.exe", "A spinning cube drawn with Vulkan (64-bit)" },
    { BCD_TESTS,  "Vulkan test (32-bit)", "vulkan-test-x86.exe", "A spinning cube drawn with Vulkan (32-bit)" },
    { BCD_TESTS,  "DirectDraw test", "ddraw-test-x86.exe",  "2D surfaces, blits, colour key, palette and a full-screen flip with DirectDraw (32-bit)" },
    { BCD_TESTS,  "Frame time test", "frametime-test-x64.exe", "A Direct3D 11 scene with a frame-time graph: FPS, stutters, 60 or 120 Hz (64-bit)" },
    { BCD_TESTS,  "Resolution and full-screen test", "resolution-test-x64.exe", "Display modes, window resizing, a mode change and back, DXGI full screen (64-bit)" },
    { BCD_TESTS,  "Sound test",      "sound-test-x64.exe",  "A left, a right and a centre tone through XAudio2 and DirectSound (64-bit)" },
    { BCD_TESTS,  "Sound test (32-bit)", "sound-test-x86.exe", "A left, a right and a centre tone through XAudio2 and DirectSound (32-bit)" },
    { BCD_TESTS,  "MIDI test",       "midi-test-x86.exe",   "A scale, a chord, five instruments, drums and a tune played through MIDI, as games play music (32-bit)" },
    { BCD_TESTS,  "MIDI test (64-bit)", "midi-test-x64.exe", "A scale, a chord, five instruments, drums and a tune played through MIDI, as games play music (64-bit)" },
    { BCD_TESTS,  "Video test",      "video-test-x86.exe",  "Media Foundation playing two short clips, MP4 (H.264) and WMV (32-bit)" },
    { BCD_TESTS,  "Video test (64-bit)", "video-test-x64.exe", "Media Foundation playing two short clips, MP4 (H.264) and WMV (64-bit)" },
    { BCD_TESTS,  "Controller test", "controller-test-x64.exe", "XInput pad picture with rumble, and the DirectInput game controllers (64-bit)" },
    { BCD_TESTS,  "Keyboard, mouse and touch test", "input-test-x64.exe", "Keys, mouse buttons, wheel and position, raw input and touch (64-bit)" },
};
const size_t bcd_start_entry_count = sizeof(bcd_start_entries) / sizeof(bcd_start_entries[0]);

/* The system tests and Windows' own tools (round 602). The tests are ONE program, bcd-systest-<arch>.exe (tests/systests), told
 * what to do by its first argument; each also writes its result to the session log, which "Send Log" carries. The x64 build is
 * in arm64ec-windows (system32), the 32-bit build in i386-windows (syswow64). */
#define SYSTEST     "bcd-systest-x64.exe"
#define SYSTEST_X86 "bcd-systest-x86.exe"

const bcd_start_entry bcd_start_system_entries[] = {
    { BCD_TESTS,  "Run all tests",       SYSTEST,     "Runs the quick system tests one after the other and writes one report", "all" },
    { BCD_TESTS,  "System info",         SYSTEST,     "Device, Windows version, graphics, memory and BCD build as a game sees them", "sysinfo" },
    { BCD_TESTS,  "CPU test",            SYSTEST,     "The processor features a game sees (SSE, AVX, BMI...) and a short speed test", "cpu" },
    { BCD_TESTS,  "RAM test 2 GB",       SYSTEST,     "Takes up to 2 GB and stops before iOS would close BCD", "ram 2" },
    { BCD_TESTS,  "RAM test 4 GB",       SYSTEST,     "Takes up to 4 GB and stops before iOS would close BCD", "ram 4" },
    { BCD_TESTS,  "RAM test 6 GB",       SYSTEST,     "Takes up to 6 GB and stops before iOS would close BCD", "ram 6" },
    { BCD_TESTS,  "RAM test 8 GB",       SYSTEST,     "Takes up to 8 GB and stops before iOS would close BCD", "ram 8" },
    { BCD_TESTS,  "RAM test 12 GB",      SYSTEST,     "Takes up to 12 GB and stops before iOS would close BCD", "ram 12" },
    { BCD_TESTS,  "RAM test (32-bit)",   SYSTEST_X86, "How much of its 4 GB address space a 32-bit game gets", "ram32" },
    { BCD_TESTS,  "JIT pool test",       SYSTEST,     "The size of this session's JIT pool and how full it is", "jit" },
    { BCD_TESTS,  "VC++ and .NET check", SYSTEST,     "Which Visual C++ runtimes and .NET or Mono parts a game finds", "vcrt" },
    { BCD_TESTS,  "Disk speed test",     SYSTEST,     "Write and read speed of C: and of the external drive when there is one", "disk" },
    { BCD_TESTS,  "Network test",        SYSTEST,     "DNS and HTTPS to Steam, Epic, GOG, Battle.net and EA", "net" },

    /* Windows' own tools. Game Controllers is control.exe with joy.cpl, which only the 32-bit set carries (syswow64). */
    { BCD_TOOLS,  "Task Manager",        "taskmgr.exe", "Lists the running programs" },
    { BCD_TOOLS,  "Command Prompt",      "cmd.exe",     "A Windows command line" },
    { BCD_TOOLS,  "Notepad",             "notepad.exe", "A plain text editor" },
    { BCD_TOOLS,  "Registry Editor",     "regedit.exe", "Shows and changes the registry of this Windows" },
    { BCD_TOOLS,  "Game Controllers",    "control.exe", "Lists the game controllers and tests them", "joy.cpl" },
};
const size_t bcd_start_system_entry_count = sizeof(bcd_start_system_entries) / sizeof(bcd_start_system_entries[0]);

/* Round 603: the runtimes folder, 7-Zip and three more of Windows' own tools, a table of their own so that the tables above can grow
 * without touching these lines.
 * BCD Runtimes: one program, bcd-runtimes-x64.exe (bcd-runtimes/runtimes.c), told what to do by its first argument. Each entry shows what it
 * will download, from where and how big, and downloads the vendor's own file only when the person presses its button (SHA-256 checked,
 * never part of BCD's package).
 * 7-Zip: 7zFM.exe, built from the project's source without its RAR code (tools/build-7zip.py), a separate Windows program; its
 * command-line twin 7z.exe is in the same folder of the app but has no entry (it needs a Command Prompt).
 * winecfg.exe is in the aarch64 set (system32); control.exe and its .cpl files only in the 32-bit set (syswow64), like joy.cpl above. */
#define RUNTIMES "bcd-runtimes-x64.exe"

const bcd_start_entry bcd_start_extra_entries[] = {
    { BCD_RUNTIMES, "Installed runtimes",  RUNTIMES, "Shows which of these runtimes are installed", "status" },
    { BCD_RUNTIMES, "XNA Framework 4.0",   RUNTIMES, "Installs Microsoft's XNA Framework 4.0 for XNA games (downloaded from Microsoft when you press the button)", "xna" },
    { BCD_RUNTIMES, "PhysX (legacy)",      RUNTIMES, "Installs NVIDIA's older PhysX System Software for older games (downloaded from NVIDIA when you press the button)", "physx" },
    { BCD_RUNTIMES, "OpenAL",              RUNTIMES, "Installs OpenAL for games that expect it in Windows (downloaded from openal.org when you press the button)", "openal" },
    { BCD_RUNTIMES, "DirectX June 2010",   RUNTIMES, "Adds the older DirectX files BCD does not carry, never replacing one it has (downloaded from Microsoft when you press the button)", "dx2010" },

    { BCD_7ZIP,     "7-Zip File Manager",  "7zFM.exe", "Opens and makes 7z, zip, tar, iso, cab and many other archives (RAR is not supported)" },

    { BCD_TOOLS,    "Wine Configuration",  "winecfg.exe", "Windows version, DLL overrides, drives and audio of this Windows" },
    { BCD_TOOLS,    "Control Panel",       "control.exe", "The Control Panel of this Windows" },
    { BCD_TOOLS,    "Uninstall a program", "control.exe", "Lists the programs installed in this Windows and removes them", "appwiz.cpl" },
};
const size_t bcd_start_extra_entry_count = sizeof(bcd_start_extra_entries) / sizeof(bcd_start_extra_entries[0]);

/* Round 604: more of Wine's own programs, a fourth table so that the three above are not touched by these lines. They are in the 32-bit set
 * of the app (WordPad, Winefile, control.exe with its panels, Internet Explorer) or in both (WineMine: the native one is found first).
 * Wine has no intl.cpl, main.cpl or mmsys.cpl (its Control Panel's applets are appwiz, desk, inetcpl, irprops, bthprops and joy, and winecfg
 * has the audio and drive pages), so the panels here are Display and Internet options. Internet Explorer is the 32-bit one and is written only
 * when Wine Gecko is there (BCD_START_GECKO): the 64-bit Gecko cannot be loaded by a native ARM64 iexplore.exe, the 32-bit one is the same
 * route the installers take. */
const bcd_start_entry bcd_start_more_entries[] = {
    { BCD_TOOLS,    "WordPad",                  "wordpad.exe",  "Writes and reads rich text (.rtf) and plain text documents" },
    { BCD_TOOLS,    "File Manager (Winefile)",  "winefile.exe", "A classic two-pane file manager (Wine's own)" },
    { BCD_TOOLS,    "Display settings",         "control.exe",  "Wine's display settings panel", "desk.cpl" },
    { BCD_TOOLS,    "Internet options",         "control.exe",  "Wine's Internet options: certificates, proxy and history", "inetcpl.cpl" },
    { BCD_TOOLS,    "Internet Explorer",        "iexplore.exe", "Wine's web browser; it needs Wine Gecko (Settings: HTML engine for installers)", NULL, BCD_START_X86 | BCD_START_GECKO },
    { BCD_TESTS,    "Minesweeper (WineMine)",   "winemine.exe", "A small game that tests mouse, touch and window handling" },
};
const size_t bcd_start_more_entry_count = sizeof(bcd_start_more_entries) / sizeof(bcd_start_more_entries[0]);
#if defined(__clang__)
#pragma clang diagnostic pop
#endif

/* Build 601 (the first with a Start menu) put the DirectX tests in "Programs/BCD". They live in "Programs/BCD Tests" now. */
const bcd_start_retired_entry bcd_start_retired[] = {
    { "Programs/BCD", "DirectX 9 test" },
    { "Programs/BCD", "DirectX 10 test" },
    { "Programs/BCD", "DirectX 11 test" },
    { "Programs/BCD", "DirectX 12 test" },
};
const size_t bcd_start_retired_count = sizeof(bcd_start_retired) / sizeof(bcd_start_retired[0]);

size_t bcd_start_all_count(void)
{
    return bcd_start_entry_count + bcd_start_system_entry_count + bcd_start_extra_entry_count + bcd_start_more_entry_count;
}

const bcd_start_entry *bcd_start_all_entry(size_t i)
{
    if (i < bcd_start_entry_count) return &bcd_start_entries[i];
    i -= bcd_start_entry_count;
    if (i < bcd_start_system_entry_count) return &bcd_start_system_entries[i];
    i -= bcd_start_system_entry_count;
    if (i < bcd_start_extra_entry_count) return &bcd_start_extra_entries[i];
    i -= bcd_start_extra_entry_count;
    return i < bcd_start_more_entry_count ? &bcd_start_more_entries[i] : NULL;
}

/* ---------------------------------------------------------------- bytes */

#define LNK_HEADER_SIZE 0x4C
#define LNK_MAX_TEXT    1000
#define SLDF_LINK_INFO  0x00000002u   /* HasLinkInfo */
#define SLDF_NAME       0x00000004u   /* HasName */
#define SLDF_WORKINGDIR 0x00000010u   /* HasWorkingDir */
#define SLDF_ARGS       0x00000020u   /* HasArguments */
#define SLDF_UNICODE    0x00000080u   /* IsUnicode */
#define SLDF_ID_LIST    0x00000001u   /* HasLinkTargetIDList */

/* CLSID_ShellLink {00021401-0000-0000-C000-000000000046}, as GUID bytes. */
static const uint8_t lnk_clsid[16] = { 0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
                                       0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 };

static void put16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { put16(p, v); put16(p + 2, v >> 16); }
static uint32_t get16(const uint8_t *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8); }
static uint32_t get32(const uint8_t *p) { return get16(p) | (get16(p + 2) << 16); }

/* ASCII text of an acceptable length: 1 when it can go into a link. */
static int lnk_text_ok(const char *s)
{
    size_t n = 0;
    for (; s[n]; n++)
        if ((unsigned char)s[n] >= 0x80 || n >= LNK_MAX_TEXT) return 0;
    return 1;
}

/* A counted UTF-16 string at out[*pos]; 0 when it does not fit. */
static int lnk_put_string(uint8_t *out, size_t cap, size_t *pos, const char *prefix, const char *s)
{
    size_t n = strlen(prefix) + strlen(s), i, k = 0;
    if (*pos + 2 + 2 * n > cap) return 0;
    put16(out + *pos, (uint32_t)n);
    *pos += 2;
    for (i = 0; prefix[i]; i++, k++) put16(out + *pos + 2 * k, (uint8_t)prefix[i]);
    for (i = 0; s[i]; i++, k++) put16(out + *pos + 2 * k, (uint8_t)s[i]);
    *pos += 2 * n;
    return 1;
}

size_t bcd_lnk_build(const char *target, const char *arguments, const char *working_dir,
                     const char *description, uint8_t *out, size_t cap)
{
    size_t pos = 0, target_len, info_size, vol_size;
    uint32_t flags = SLDF_LINK_INFO | SLDF_NAME | SLDF_UNICODE;

    if (!target || !*target || !description || !out) return 0;
    if (!lnk_text_ok(target) || !lnk_text_ok(description)) return 0;
    if (arguments && (!lnk_text_ok(arguments))) return 0;
    if (working_dir && (!lnk_text_ok(working_dir))) return 0;
    if (arguments && *arguments) flags |= SLDF_ARGS;
    if (working_dir && *working_dir) flags |= SLDF_WORKINGDIR;

    target_len = strlen(target);
    vol_size = 0x10 + 1;                                  /* VolumeID: four fields and the empty label's null */
    info_size = 0x1C + vol_size + target_len + 1 + 1;     /* header, VolumeID, base path, empty common suffix */
    if (cap < LNK_HEADER_SIZE + info_size) return 0;
    memset(out, 0, LNK_HEADER_SIZE + info_size);          /* every gap, label and null below stays zero */

    /* ShellLinkHeader */
    put32(out + 0x00, LNK_HEADER_SIZE);
    memcpy(out + 0x04, lnk_clsid, 16);
    put32(out + 0x14, flags);
    put32(out + 0x18, 0x20);                              /* FILE_ATTRIBUTE_ARCHIVE */
    /* the three FILETIMEs (0x1C, 0x24, 0x2C) and the file size (0x34) stay zero: the target is not measured */
    put32(out + 0x38, 0);                                 /* icon index */
    put32(out + 0x3C, 1);                                 /* SW_SHOWNORMAL */
    pos = LNK_HEADER_SIZE;

    /* LinkInfo */
    put32(out + pos + 0x00, (uint32_t)info_size);
    put32(out + pos + 0x04, 0x1C);                        /* LinkInfoHeaderSize */
    put32(out + pos + 0x08, 1);                           /* VolumeIDAndLocalBasePath */
    put32(out + pos + 0x0C, 0x1C);                        /* VolumeIDOffset */
    put32(out + pos + 0x10, (uint32_t)(0x1C + vol_size)); /* LocalBasePathOffset */
    put32(out + pos + 0x14, 0);                           /* CommonNetworkRelativeLinkOffset: none */
    put32(out + pos + 0x18, (uint32_t)(0x1C + vol_size + target_len + 1)); /* CommonPathSuffixOffset */
    put32(out + pos + 0x1C + 0x00, (uint32_t)vol_size);   /* VolumeIDSize */
    put32(out + pos + 0x1C + 0x04, 3);                    /* DRIVE_FIXED */
    put32(out + pos + 0x1C + 0x08, 0);                    /* DriveSerialNumber */
    put32(out + pos + 0x1C + 0x0C, 0x10);                 /* VolumeLabelOffset */
    /* the label is the one zero byte already there */
    memcpy(out + pos + 0x1C + vol_size, target, target_len);   /* LocalBasePath, null-ended by the cleared buffer */
    pos += info_size;

    /* StringData */
    if (!lnk_put_string(out, cap, &pos, BCD_LNK_MARKER, description)) return 0;
    if ((flags & SLDF_WORKINGDIR) && !lnk_put_string(out, cap, &pos, "", working_dir)) return 0;
    if ((flags & SLDF_ARGS) && !lnk_put_string(out, cap, &pos, "", arguments)) return 0;

    /* ExtraData: the terminal block */
    if (pos + 4 > cap) return 0;
    put32(out + pos, 0);
    pos += 4;
    return pos;
}

int bcd_lnk_owner(const uint8_t *data, size_t len)
{
    size_t pos = LNK_HEADER_SIZE, i, n;
    uint32_t flags;
    const size_t mlen = strlen(BCD_LNK_MARKER);

    if (!data || len < LNK_HEADER_SIZE) return -1;
    if (get32(data) != LNK_HEADER_SIZE || memcmp(data + 4, lnk_clsid, 16) != 0) return -1;
    flags = get32(data + 0x14);
    if (flags & SLDF_ID_LIST) {
        if (pos + 2 > len) return -1;
        pos += 2 + get16(data + pos);
    }
    if (flags & SLDF_LINK_INFO) {
        if (pos + 4 > len) return -1;
        n = get32(data + pos);
        if (n < 4 || pos + n > len) return -1;
        pos += n;
    }
    if (!(flags & SLDF_NAME)) return 0;                   /* a link with no description is not BCD's */
    if (pos + 2 > len) return -1;
    n = get16(data + pos);
    pos += 2;
    if (flags & SLDF_UNICODE) {
        if (pos + 2 * n > len) return -1;
        if (n < mlen) return 0;
        for (i = 0; i < mlen; i++)
            if (get16(data + pos + 2 * i) != (uint8_t)BCD_LNK_MARKER[i]) return 0;
    } else {
        if (pos + n > len) return -1;
        if (n < mlen) return 0;
        if (memcmp(data + pos, BCD_LNK_MARKER, mlen) != 0) return 0;
    }
    return 1;
}

/* ---------------------------------------------------------------- the prefix */

#define STARTMENU_REL "drive_c/ProgramData/Microsoft/Windows/Start Menu"
#define LNK_FILE_MAX  4096

static int is_dir(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static int exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

/* Every folder of `folder` below `base`, made when missing; 1 when the last one is there afterwards. */
static int make_folders(const char *base, const char *folder)
{
    char path[PATH_MAX];
    size_t len = strlen(base);
    const char *p = folder;

    if (len + 1 >= sizeof(path)) return 0;
    memcpy(path, base, len + 1);
    while (*p) {
        size_t part = strcspn(p, "/");
        if (part == 0) { p++; continue; }
        if (len + 1 + part >= sizeof(path)) return 0;
        path[len++] = '/';
        memcpy(path + len, p, part);
        len += part;
        path[len] = 0;
        if (!is_dir(path) && mkdir(path, 0755) != 0 && !is_dir(path)) return 0;
        p += part;
    }
    return is_dir(path);
}

/* The file's bytes (at most LNK_FILE_MAX + 1), or -1 when it is not a plain file one can read. */
static long read_plain(const char *path, uint8_t *buf)
{
    struct stat st;
    long total = 0;
    ssize_t got;
    int fd;

    if (lstat(path, &st) != 0 || !S_ISREG(st.st_mode)) return -1;
    fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    while (total < LNK_FILE_MAX + 1 && (got = read(fd, buf + total, (size_t)(LNK_FILE_MAX + 1 - total))) > 0) total += got;
    close(fd);
    return total;
}

static int write_file(const char *path, const uint8_t *data, size_t len)
{
    char tmp[PATH_MAX];
    size_t done = 0;
    int fd;

    if (snprintf(tmp, sizeof(tmp), "%s.bcdtmp", path) >= (int)sizeof(tmp)) return 0;
    fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return 0;
    while (done < len) {
        ssize_t n = write(fd, data + done, len - done);
        if (n <= 0) { close(fd); unlink(tmp); return 0; }
        done += (size_t)n;
    }
    close(fd);
    if (rename(tmp, path) != 0) { unlink(tmp); return 0; }
    return 1;
}

/* Where the entry's program is: 0 in the 64-bit farms (system32), 1 in the 32-bit set (syswow64), -1 when the app does not
 * carry it or the name is not a plain file name. */
static int program_farm(const char *bundle, const bcd_start_entry *e)
{
    static const char *const farms64[] = { "arm64ec-windows", "aarch64-windows" };
    char probe[PATH_MAX];
    size_t i;

    if (!e->program[0] || strpbrk(e->program, "/\\") || !strcmp(e->program, ".") || !strcmp(e->program, "..")) return -1;
    if (!(e->flags & BCD_START_X86))
        for (i = 0; i < sizeof(farms64) / sizeof(farms64[0]); i++)
            if (snprintf(probe, sizeof(probe), "%s/%s/%s", bundle, farms64[i], e->program) < (int)sizeof(probe) && exists(probe)) return 0;
    /* the 32-bit set counts only when it is a set: the session links syswow64 once the bundle has the i386 ntdll.dll
     * (madeira_bundle_has_i386 in WineProcessBridge.m); a lone tracked 32-bit test program is no set */
    if (snprintf(probe, sizeof(probe), "%s/i386-windows/ntdll.dll", bundle) < (int)sizeof(probe) && exists(probe) &&
        snprintf(probe, sizeof(probe), "%s/i386-windows/%s", bundle, e->program) < (int)sizeof(probe) && exists(probe)) return 1;
    return -1;
}

/* Whether Wine Gecko is available in this session (BCD_START_GECKO): in the prefix, or as BCD's downloaded files that XDG_CACHE_HOME names. */
static int gecko_available(const char *prefix)
{
    static const char *const homes[] = { "system32", "syswow64" };
    char probe[PATH_MAX];
    const char *cache = getenv("XDG_CACHE_HOME");
    size_t i;

    for (i = 0; i < sizeof(homes) / sizeof(homes[0]); i++)
        if (snprintf(probe, sizeof(probe), "%s/drive_c/windows/%s/gecko", prefix, homes[i]) < (int)sizeof(probe) && is_dir(probe)) return 1;
    if (cache && cache[0] && snprintf(probe, sizeof(probe), "%s/wine", cache) < (int)sizeof(probe)) {
        DIR *d = opendir(probe);
        struct dirent *de;
        int found = 0;
        if (d) {
            while (!found && (de = readdir(d)) != NULL) {
                size_t n = strlen(de->d_name);
                if (!strncmp(de->d_name, "wine-gecko-", 11) && n > 15 && !strcmp(de->d_name + n - 4, ".msi")) found = 1;
            }
            closedir(d);
        }
        return found;
    }
    return 0;
}

/* An earlier build's shortcut that this build no longer has (bcd_start_retired): removed when it is a plain file that is
 * BCD's own and not an entry of today's tables, and its folder with it when that is empty afterwards. */
static void retire_old(const char *menu, bcd_start_result *r)
{
    char file[PATH_MAX], dir[PATH_MAX];
    uint8_t current[LNK_FILE_MAX + 1];
    size_t i, k;

    for (i = 0; i < bcd_start_retired_count; i++) {
        const bcd_start_retired_entry *old = &bcd_start_retired[i];
        long have;
        int still_used = 0;

        for (k = 0; k < bcd_start_all_count(); k++) {
            const bcd_start_entry *e = bcd_start_all_entry(k);
            if (!strcmp(e->folder, old->folder) && !strcmp(e->name, old->name)) still_used = 1;
        }
        if (still_used) continue;
        if (snprintf(dir, sizeof(dir), "%s/%s", menu, old->folder) >= (int)sizeof(dir) ||
            snprintf(file, sizeof(file), "%s/%s.lnk", dir, old->name) >= (int)sizeof(file))
            continue;
        have = read_plain(file, current);               /* not a plain file (a folder, a symbolic link): left alone */
        if (have < 0 || have > LNK_FILE_MAX || bcd_lnk_owner(current, (size_t)have) != 1) continue;
        if (unlink(file) == 0) r->retired++;
        rmdir(dir);                                     /* only succeeds when empty; "Programs" itself is never touched */
    }
}

bcd_start_result bcd_startmenu_install(const char *prefix, const char *bundle)
{
    bcd_start_result r = { 0, 0, 0, 0, 0, 0, 0, 0 };
    char menu[PATH_MAX], file[PATH_MAX], target[256];
    uint8_t built[LNK_FILE_MAX], current[LNK_FILE_MAX + 1];
    size_t i;

    if (!prefix || !bundle || snprintf(menu, sizeof(menu), "%s/" STARTMENU_REL, prefix) >= (int)sizeof(menu) || !is_dir(menu)) {
        r.no_menu = 1;
        return r;
    }
    retire_old(menu, &r);
    for (i = 0; i < bcd_start_all_count(); i++) {
        const bcd_start_entry *e = bcd_start_all_entry(i);
        size_t size;
        long have;
        int farm;

        /* a program the app does not carry gets no entry; nor does one that waits for Wine Gecko (an entry already written stays) */
        farm = program_farm(bundle, e);
        if (farm < 0 || ((e->flags & BCD_START_GECKO) && !gecko_available(prefix))) {
            r.missing++;
            continue;
        }
        if (snprintf(target, sizeof(target), "C:\\windows\\%s\\%s", farm ? "syswow64" : "system32", e->program) >= (int)sizeof(target)) {
            r.failed++;
            continue;
        }
        size = bcd_lnk_build(target, e->arguments, NULL, e->description, built, sizeof(built));
        if (size == 0) { r.failed++; continue; }
        if (!make_folders(menu, e->folder)) { r.failed++; continue; }
        if (snprintf(file, sizeof(file), "%s/%s%s%s.lnk", menu, e->folder, *e->folder ? "/" : "", e->name) >= (int)sizeof(file)) {
            r.failed++;
            continue;
        }
        if (farm) r.x86++;
        if (lstat(file, &(struct stat){ 0 }) == 0) {
            have = read_plain(file, current);
            if (have < 0 || have > LNK_FILE_MAX || bcd_lnk_owner(current, (size_t)have) != 1) { r.foreign++; continue; }
            if ((size_t)have == size && memcmp(current, built, size) == 0) { r.current++; continue; }
        }
        if (write_file(file, built, size)) r.written++; else r.failed++;
    }
    return r;
}
