// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDJapanese.h
//
//  Japanese locale for one game (env.MADEIRA_JAPANESE = 1, the game page's Compatibility › Japanese locale; off by default), after
//  upstream Madeira's pull request #567 by nekodesusan-prog (the code that holds its lines is BCDJapanese.c).
//
//  Japanese games, visual novels above all, keep their text in Shift-JIS (code page 932) and ask GDI for MS Gothic, MS Mincho or
//  MS UI Gothic. Wine runs them with the phone's language and its code pages, so their text turns into mojibake or squares and
//  some stop at start when a file name or a script does not convert. With the switch on:
//    - Wine's system and user locale are Japanese for the session (LC_ALL = ja_JP.UTF-8, so ANSI and OEM code page 932; a
//      Japanese LC_ALL the game's file already sets is kept) and env.MADEIRA_NLS_EXTRA = 1 serves c_932.nls and the other CJK
//      tables from the bundle's nls/extra folder; both go back to what they were when the session ends;
//    - a Japanese font, the bundle's fonts-ja/NotoSansJP-Regular.otf (Noto Sans JP, SIL OFL 1.1; tools/fetch-japanese-font.sh
//      puts it there at build time), is linked into C:\windows\Fonts, and the Windows Japanese font names are mapped onto it in
//      HKCU\Software\Wine\Fonts\Replacements (a replacement only applies while no real font of that name is installed).
//  The prefix holds the link and the names only while a game with the switch runs: the next session without it takes both back
//  before the wineserver reads the registry (a marker file in the prefix says there is something to take back), and with them the
//  copy of user.reg made before the first change (user.reg.madeira-bak), so every other game starts with the prefix as it was.
//  Without the marker and without the switch nothing is read or written (one stat).
//  A build without the font (its fetch failed) has no Japanese locale: the game page hides the switch, and a switch set by hand
//  changes nothing but a [japanese] line in the log.
//  The locale replaces a LC_ALL the game's file or its list row sets (some rows set en_US.UTF-8 for a game that does not start
//  otherwise) for that session; the switch's note says so.
//  Pure C, compiled by tests/host/check-japanese-locale.py.

#ifndef BCD_JAPANESE_H
#define BCD_JAPANESE_H

/* 1 when this session runs with the Japanese locale: MADEIRA_JAPANESE in the environment, else env.MADEIRA_JAPANESE in bcd.cfg
 * or the game's own file ($MADEIRA_CFG_GAME, which wins). Only "1" counts. */
int bcd_japanese_on(void);

/* 1 when `resources` (the bundle's resource folder) holds fonts-ja/NotoSansJP-Regular.otf, a non-empty file: the Japanese locale
 * exists in this build only then. */
int bcd_japanese_font_in(const char *resources);

/* Before the wineserver reads the registry (madeira_seed_prefix_if_needed, while no server runs). on (and the bundle has the
 * font): links the font and maps the names in user.reg, after writing the marker; off, or no font in the bundle: takes back what
 * a Japanese session left (the names, the link, user.reg.madeira-bak), when the marker is there. `resources` is the bundle's
 * resource folder. Logs [japanese] lines through `say` (may be NULL). Returns the number of font names written (on) or taken
 * back (off). */
int bcd_japanese_prefix(const char *prefix, const char *resources, int on, void (*say)(const char *fmt, ...));

/* After the game's own env lines are exported, before Wine starts: with on and the font in `resources`, LC_ALL (unless already
 * Japanese), LANG and MADEIRA_NLS_EXTRA, remembering the values they replace; with on and no font, nothing. Writes one [japanese]
 * line to log_fd (-1: none). */
void bcd_japanese_session_env(int on, const char *resources, int log_fd);

/* When the session ends: LC_ALL and LANG get the values they had before bcd_japanese_session_env changed them (unset when they
 * had none) and MADEIRA_JAPANESE is unset, so a later game of this app run starts without them. */
void bcd_japanese_session_end(void);

#endif
