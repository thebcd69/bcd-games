// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 nekodesusan-prog: code taken from https://github.com/willfaust/Madeira (pull request #567, commit 025c0ca2)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
//
//  BCDJapanese.c: see BCDJapanese.h. Upstream's madeira_japanese_on / madeira_japanese_prefix (WineProcessBridge.m), in C, with
//  BCD's changes: the font is linked from the bundle instead of copied, it is fetched at build time instead of committed, and a
//  session without the switch takes back the link and the names a Japanese session left (upstream keeps them in the prefix).

#include "BCDJapanese.h"
#include "../../build/madeira_cfg.h"

#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define BCD_JA_FONT_FILE "NotoSansJP-Regular.otf"
#define BCD_JA_FAMILY    "Noto Sans JP"
#define BCD_JA_MARKER    ".bcd-japanese"
/* The key as Wine's registry text writes it: backslashes doubled. */
#define BCD_JA_SECTION   "[Software\\\\Wine\\\\Fonts\\\\Replacements]"

/* The Windows Japanese font names, escaped the way Wine's registry text writes them (characters above 0x7e as \xNNNN). */
static const char *const bcd_ja_names[] = {
    "MS Gothic", "MS PGothic", "MS UI Gothic", "MS Mincho", "MS PMincho",
    "\\xff2d\\xff33 \\x30b4\\x30b7\\x30c3\\x30af",           /* ＭＳ ゴシック */
    "\\xff2d\\xff33 \\xff30\\x30b4\\x30b7\\x30c3\\x30af",    /* ＭＳ Ｐゴシック */
    "\\xff2d\\xff33 \\x660e\\x671d",                         /* ＭＳ 明朝 */
    "\\xff2d\\xff33 \\xff30\\x660e\\x671d",                  /* ＭＳ Ｐ明朝 */
    "Meiryo", "Meiryo UI", "\\x30e1\\x30a4\\x30ea\\x30aa",   /* メイリオ */
    "Yu Gothic", "Yu Gothic UI", "Yu Mincho",
    "\\x6e38\\x30b4\\x30b7\\x30c3\\x30af", "\\x6e38\\x660e\\x671d",   /* 游ゴシック, 游明朝 */
};
enum { bcd_ja_name_count = sizeof(bcd_ja_names) / sizeof(bcd_ja_names[0]) };

int bcd_japanese_font_in(const char *resources)
{
    char src[PATH_MAX];
    struct stat st;

    if (!resources || !*resources) return 0;
    if (snprintf(src, sizeof src, "%s/fonts-ja/" BCD_JA_FONT_FILE, resources) >= (int)sizeof src) return 0;
    return !stat(src, &st) && S_ISREG(st.st_mode) && st.st_size > 0;
}

int bcd_japanese_on(void)
{
    const char *e = getenv("MADEIRA_JAPANESE");
    char v[16];

    if (e && *e) return !strcmp(e, "1");
    if (madeira_cfg_get("env.MADEIRA_JAPANESE", v, sizeof v) && v[0]) return !strcmp(v, "1");
    return 0;
}

/* ---- user.reg as lines ---- */

struct bcd_ja_lines { char **v; size_t n, cap; };

static int bcd_ja_push(struct bcd_ja_lines *l, size_t at, const char *s, size_t len)
{
    char *copy;
    if (l->n == l->cap) {
        size_t cap = l->cap ? l->cap * 2 : 1024;
        char **nv = realloc(l->v, cap * sizeof(*nv));
        if (!nv) return 0;
        l->v = nv; l->cap = cap;
    }
    if (!(copy = malloc(len + 1))) return 0;
    memcpy(copy, s, len); copy[len] = 0;
    memmove(l->v + at + 1, l->v + at, (l->n - at) * sizeof(*l->v));
    l->v[at] = copy; l->n++;
    return 1;
}

static void bcd_ja_drop(struct bcd_ja_lines *l, size_t at)
{
    free(l->v[at]);
    memmove(l->v + at, l->v + at + 1, (l->n - at - 1) * sizeof(*l->v));
    l->n--;
}

static void bcd_ja_free(struct bcd_ja_lines *l)
{
    for (size_t i = 0; i < l->n; i++) free(l->v[i]);
    free(l->v);
    l->v = NULL; l->n = l->cap = 0;
}

/* The file's lines without their '\n'. 0 when it cannot be read or is empty. */
static int bcd_ja_read(const char *path, struct bcd_ja_lines *l)
{
    FILE *f = fopen(path, "rb");
    char *buf, *p, *end;
    long size;
    int ok = 0;

    if (!f) return 0;
    if (fseek(f, 0, SEEK_END) || (size = ftell(f)) <= 0 || fseek(f, 0, SEEK_SET) || !(buf = malloc((size_t)size + 1))) {
        fclose(f);
        return 0;
    }
    if (fread(buf, 1, (size_t)size, f) == (size_t)size) {
        ok = 1;
        buf[size] = 0;
        for (p = buf, end = buf + size; p < end && ok; ) {
            char *nl = memchr(p, '\n', (size_t)(end - p));
            size_t len = nl ? (size_t)(nl - p) : (size_t)(end - p);
            ok = bcd_ja_push(l, l->n, p, len);
            p += len + (nl ? 1 : 0);
        }
    }
    free(buf);
    fclose(f);
    if (!ok) bcd_ja_free(l);
    return ok;
}

/* Written next to the file and renamed over it, so a reader never sees half a file. */
static int bcd_ja_write(const char *path, const struct bcd_ja_lines *l)
{
    char tmp[PATH_MAX];
    FILE *f;
    int ok = 1;

    if (snprintf(tmp, sizeof tmp, "%s.bcd-tmp", path) >= (int)sizeof tmp || !(f = fopen(tmp, "wb"))) return 0;
    for (size_t i = 0; i < l->n && ok; i++) ok = fputs(l->v[i], f) >= 0 && fputc('\n', f) != EOF;
    if (fclose(f) || !ok || rename(tmp, path)) { unlink(tmp); return 0; }
    return 1;
}

/* The section's header line, or -1; *end is the first line after the section. */
static long bcd_ja_section(const struct bcd_ja_lines *l, size_t *end)
{
    const size_t hl = strlen(BCD_JA_SECTION);
    for (size_t i = 0; i < l->n; i++) {
        if (strncasecmp(l->v[i], BCD_JA_SECTION, hl)) continue;
        size_t e = i + 1;
        while (e < l->n && l->v[e][0] != '[') e++;
        *end = e;
        return (long)i;
    }
    return -1;
}

/* Line i is the value `"name"=...` (the name compared without case, as Wine's registry does). */
static int bcd_ja_is_value(const char *line, const char *name)
{
    size_t n = strlen(name);
    return line[0] == '"' && !strncasecmp(line + 1, name, n) && line[n + 1] == '"' && line[n + 2] == '=';
}

/* Our value: the name, = "Noto Sans JP", nothing else. */
static int bcd_ja_is_ours(const char *line, const char *name)
{
    size_t n = strlen(name);
    return bcd_ja_is_value(line, name) && !strcmp(line + n + 3, "\"" BCD_JA_FAMILY "\"");
}

/* Adds the names that are not set yet. Returns how many were added, -1 when user.reg cannot be read or written. */
static int bcd_ja_map_names(const char *reg)
{
    struct bcd_ja_lines l = {0};
    size_t end = 0, at;
    long start;
    int added = 0;

    if (!bcd_ja_read(reg, &l)) return -1;
    start = bcd_ja_section(&l, &end);
    if (start < 0) {
        char head[128];
        while (l.n && !l.v[l.n - 1][0]) bcd_ja_drop(&l, l.n - 1);
        snprintf(head, sizeof head, BCD_JA_SECTION " %ld", (long)time(NULL));
        if (!bcd_ja_push(&l, l.n, "", 0) || !bcd_ja_push(&l, l.n, head, strlen(head)) || !bcd_ja_push(&l, l.n, "", 0)) {
            bcd_ja_free(&l);
            return -1;
        }
        start = (long)l.n - 2;
        end = l.n - 1;
    }
    at = (size_t)start + 1;
    while (at < end && l.v[at][0] == '#') at++;   /* the #time / #class option lines stay right under the header */
    for (int k = 0; k < bcd_ja_name_count; k++) {
        int present = 0;
        char line[256];
        for (size_t i = (size_t)start + 1; i < end && !present; i++) present = bcd_ja_is_value(l.v[i], bcd_ja_names[k]);
        if (present) continue;
        snprintf(line, sizeof line, "\"%s\"=\"" BCD_JA_FAMILY "\"", bcd_ja_names[k]);
        if (!bcd_ja_push(&l, at++, line, strlen(line))) { bcd_ja_free(&l); return -1; }
        end++;
        added++;
    }
    if (added) {
        char backup[PATH_MAX];
        struct stat st;
        /* Upstream's one-time copy of the file before its first change, kept for a manual restore (upstream calls it .madeira-bak;
         * BCD's own name, as BCD names its files). */
        if (snprintf(backup, sizeof backup, "%s.bcd-japanese-bak", reg) < (int)sizeof backup && stat(backup, &st)) {
            struct bcd_ja_lines old = {0};
            if (bcd_ja_read(reg, &old)) bcd_ja_write(backup, &old);
            bcd_ja_free(&old);
        }
        if (!bcd_ja_write(reg, &l)) added = -1;
    }
    bcd_ja_free(&l);
    return added;
}

/* Takes our values out. The template's section keeps its own values (Arial and the others onto Tahoma); a section this code
 * made in a user.reg without one is taken away once it is empty. Returns how many, -1 on a write error. */
static int bcd_ja_unmap_names(const char *reg)
{
    struct bcd_ja_lines l = {0};
    size_t end = 0;
    long start;
    int removed = 0, values = 0;

    if (!bcd_ja_read(reg, &l)) {   /* no user.reg, or an empty one: nothing of ours in it; one that cannot be read: try again next time */
        struct stat st;
        return stat(reg, &st) || !st.st_size ? 0 : -1;
    }
    if ((start = bcd_ja_section(&l, &end)) < 0) { bcd_ja_free(&l); return 0; }
    for (size_t i = end; i-- > (size_t)start + 1; ) {
        int ours = 0;
        for (int k = 0; k < bcd_ja_name_count && !ours; k++) ours = bcd_ja_is_ours(l.v[i], bcd_ja_names[k]);
        if (ours) { bcd_ja_drop(&l, i); end--; removed++; }
    }
    for (size_t i = (size_t)start + 1; i < end; i++) if (l.v[i][0] == '"' || l.v[i][0] == '@') values++;
    if (removed && !values) {
        /* The header, its option lines and the blank lines up to the next section. */
        while (end > (size_t)start) { bcd_ja_drop(&l, (size_t)start); end--; }
        while ((size_t)start > 0 && (size_t)start <= l.n && !l.v[start - 1][0] && ((size_t)start == l.n || !l.v[start][0]))
            { bcd_ja_drop(&l, (size_t)start - 1); start--; }
    }
    if (removed && !bcd_ja_write(reg, &l)) removed = -1;
    bcd_ja_free(&l);
    return removed;
}

/* ---- the font link and the marker ---- */

/* 1 when path is a symbolic link to a fonts-ja/NotoSansJP-Regular.otf (a bundle's, any bundle path). */
static int bcd_ja_link_is_ours(const char *path)
{
    char to[PATH_MAX];
    ssize_t n = readlink(path, to, sizeof to - 1);
    const char *tail = "/fonts-ja/" BCD_JA_FONT_FILE;
    if (n <= 0) return 0;
    to[n] = 0;
    return (size_t)n >= strlen(tail) && !strcmp(to + n - strlen(tail), tail);
}

int bcd_japanese_prefix(const char *prefix, const char *resources, int on, void (*say)(const char *fmt, ...))
{
    char marker[PATH_MAX], reg[PATH_MAX], dst[PATH_MAX], src[PATH_MAX], fonts[PATH_MAX];
    struct stat st;
    int n;

    if (!prefix || !*prefix) return 0;
    snprintf(marker, sizeof marker, "%s/" BCD_JA_MARKER, prefix);
    snprintf(reg, sizeof reg, "%s/user.reg", prefix);
    snprintf(fonts, sizeof fonts, "%s/drive_c/windows/Fonts", prefix);
    snprintf(dst, sizeof dst, "%s/" BCD_JA_FONT_FILE, fonts);

    if (on && !bcd_japanese_font_in(resources)) {
        /* A build whose font fetch failed: the game page hides the switch and the session keeps its own locale
         * (bcd_japanese_session_env), so the prefix is treated as for a game without it. */
        if (say) say("[japanese] env.MADEIRA_JAPANESE = 1, but fonts-ja/" BCD_JA_FONT_FILE " is not in this build: the Japanese "
                     "locale is off for this session");
        on = 0;
    }
    if (!on) {
        int removed, unlinked = 0;
        if (lstat(marker, &st)) return 0;   /* no Japanese session left anything: nothing is read or written */
        removed = bcd_ja_unmap_names(reg);
        if (bcd_ja_link_is_ours(dst) && !unlink(dst)) unlinked = 1;
        if (removed >= 0) {   /* a failed write keeps both, so the next session tries again */
            char backup[PATH_MAX];
            /* The copy bcd_ja_map_names made before the first change: user.reg is what it was again, so it goes too. */
            if (snprintf(backup, sizeof backup, "%s.bcd-japanese-bak", reg) < (int)sizeof backup) unlink(backup);
            unlink(marker);
        }
        if (say) say("[japanese] off for this game: %d Japanese font names and %s the last Japanese-locale session added are taken back%s",
                     removed < 0 ? 0 : removed, unlinked ? "the font link" : "no font link", removed < 0 ? " (user.reg could NOT be written)" : "");
        return removed < 0 ? 0 : removed;
    }

    snprintf(src, sizeof src, "%s/fonts-ja/" BCD_JA_FONT_FILE, resources);
    /* The marker first: whatever follows, the next session without the switch knows there may be something to take back. */
    {
        FILE *m = fopen(marker, "w");
        if (m) { fputs("Japanese locale: the font link and the font names in user.reg are BCD's (BCDJapanese.c)\n", m); fclose(m); }
    }
    mkdir(fonts, 0755);
    if (!lstat(dst, &st) && !bcd_ja_link_is_ours(dst)) {
        /* A file (or a link) of that name that is not BCD's: the game's or the player's own copy, which gives the family as well. */
        if (say) say("[japanese] C:\\windows\\Fonts\\" BCD_JA_FONT_FILE " is the game's or the player's own; left as it is");
    } else {
        char to[PATH_MAX];
        ssize_t len = readlink(dst, to, sizeof to - 1);
        if (len > 0) to[len] = 0;
        if (len <= 0 || strcmp(to, src)) {   /* none yet, or a link into an older bundle path (an app update moves it) */
            unlink(dst);
            if (symlink(src, dst)) {
                if (say) say("[japanese] " BCD_JA_FONT_FILE " could NOT be linked into C:\\windows\\Fonts (errno %d)", errno);
                return 0;
            }
            if (say) say("[japanese] " BCD_JA_FONT_FILE " linked into C:\\windows\\Fonts");
        }
    }
    n = bcd_ja_map_names(reg);
    if (n < 0) {
        if (say) say("[japanese] user.reg could not be read or written; the Japanese font names are not mapped this time");
        return 0;
    }
    if (n && say) say("[japanese] %d Japanese font names mapped to " BCD_JA_FAMILY " in user.reg", n);
    return n;
}

/* ---- the session's locale ---- */

static int bcd_ja_saved;
static char *bcd_ja_lc_all, *bcd_ja_lang;

static char *bcd_ja_dup_env(const char *name)
{
    const char *v = getenv(name);
    return v ? strdup(v) : NULL;
}

void bcd_japanese_session_env(int on, const char *resources, int log_fd)
{
    const char *lc;

    if (!on) return;
    if (!bcd_japanese_font_in(resources)) {
        if (log_fd >= 0)
            dprintf(log_fd, "[japanese] env.MADEIRA_JAPANESE = 1, but fonts-ja/" BCD_JA_FONT_FILE " is not in this build: the locale is not "
                    "changed\n");
        return;
    }
    if (!bcd_ja_saved) {
        bcd_ja_lc_all = bcd_ja_dup_env("LC_ALL");
        bcd_ja_lang = bcd_ja_dup_env("LANG");
        bcd_ja_saved = 1;
    }
    lc = getenv("LC_ALL");
    if (!lc || strncmp(lc, "ja", 2)) setenv("LC_ALL", "ja_JP.UTF-8", 1);
    setenv("LANG", getenv("LC_ALL"), 1);
    setenv("MADEIRA_NLS_EXTRA", "1", 1);
    if (log_fd >= 0)
        dprintf(log_fd, "[japanese] LC_ALL=%s MADEIRA_NLS_EXTRA=1: Japanese system and user locale, code page 932\n", getenv("LC_ALL"));
}

void bcd_japanese_session_end(void)
{
    if (bcd_ja_saved) {
        if (bcd_ja_lc_all) setenv("LC_ALL", bcd_ja_lc_all, 1); else unsetenv("LC_ALL");
        if (bcd_ja_lang) setenv("LANG", bcd_ja_lang, 1); else unsetenv("LANG");
        free(bcd_ja_lc_all); free(bcd_ja_lang);
        bcd_ja_lc_all = bcd_ja_lang = NULL;
        bcd_ja_saved = 0;
    }
    unsetenv("MADEIRA_JAPANESE");
}
