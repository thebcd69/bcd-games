/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * A Qt-like user of ICU's C API (the calls Qt 6's QCollator, QStringConverter / QTextCodec, QTimeZone, QLocale case mapping and
 * QTextBoundaryFinder make), compiled against ICU's public headers with -DU_DISABLE_RENAMING=1 (unversioned names, as Qt is built on
 * Windows) and run in two places:
 *   - tools/build-icu-optin.sh runs it on the Mac, linked to the Mac's static ICU build and the TRIMMED data package, and compares its
 *     output with build/icu-win/icu-probe.expected: the trimmed data must answer everything below exactly as ICU's full data does;
 *   - tests/host/check-icu-optin.py runs the same program as a Windows program (x86-64 flavour of the DLLs, a host Wine, when there is one)
 *     against icuuc.dll and icuin.dll: the DLLs load, export the names, carry the data, and answer the same.
 * One "key=value" line per check; non-ASCII text is printed as \uXXXX. Lines that start with "info:" differ by design between the
 * full data and the trimmed one and are not compared.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
#include <unicode/utypes.h>
#include <unicode/uversion.h>
#include <unicode/ustring.h>
#include <unicode/ucnv.h>
#include <unicode/ucol.h>
#include <unicode/ubrk.h>
#include <unicode/uloc.h>
#include <unicode/ucal.h>
#include <unicode/udat.h>
#include <unicode/unum.h>
#include <unicode/unorm2.h>
#include <unicode/uchar.h>
#include <unicode/uscript.h>
#include <unicode/uenum.h>
#include <unicode/uidna.h>
#include <unicode/uregex.h>

static int failures;

static void show(const char *key, const UChar *s, int32_t n) {
    printf("%s=", key);
    if (n < 0) n = u_strlen(s);
    for (int32_t i = 0; i < n; i++)
        if (s[i] >= 0x20 && s[i] < 0x7f) putchar((int)s[i]);
        else printf("\\u%04X", (unsigned)s[i]);
    putchar('\n');
}
static void num(const char *key, long long v) { printf("%s=%lld\n", key, v); }
static void text(const char *key, const char *v) { printf("%s=%s\n", key, v); }
static void status(const char *key, UErrorCode e) {
    printf("%s=%s\n", key, U_SUCCESS(e) ? "ok" : u_errorName(e));
    if (U_FAILURE(e)) failures++;
}
/* UTF-8 literal to UTF-16 */
static int32_t U(UChar *dst, int32_t cap, const char *utf8) {
    UErrorCode e = U_ZERO_ERROR; int32_t n = 0;
    u_strFromUTF8(dst, cap, &n, utf8, -1, &e);
    return U_SUCCESS(e) ? n : -1;
}
static int cmp(UCollator *c, const char *a, const char *b) {
    UChar x[64], y[64]; int32_t n = U(x, 64, a), m = U(y, 64, b);
    return (int)ucol_strcoll(c, x, n, y, m);
}
static void versions(void) {
    UVersionInfo v; char buf[U_MAX_VERSION_STRING_LENGTH];
    u_getVersion(v); u_versionToString(v, buf); text("icu.version", buf);
    u_getUnicodeVersion(v); u_versionToString(v, buf); text("unicode.version", buf);
}

static void collation(void) {
    UErrorCode e = U_ZERO_ERROR;
    UCollator *c = ucol_open("en_US", &e); status("ucol_open.en_US", e);
    if (!c) return;
    num("coll.a_B", cmp(c, "a", "B"));
    num("coll.a_A", cmp(c, "a", "A"));
    num("coll.resume_accent", cmp(c, "resume", "r\xc3\xa9sum\xc3\xa9"));
    num("coll.a-umlaut_z", cmp(c, "\xc3\xa4", "z"));
    num("coll.file2_file10_default", cmp(c, "file2", "file10"));
    ucol_setAttribute(c, UCOL_NUMERIC_COLLATION, UCOL_ON, &e);
    num("coll.file2_file10_numeric", cmp(c, "file2", "file10"));
    ucol_setAttribute(c, UCOL_STRENGTH, UCOL_SECONDARY, &e);
    num("coll.a_A_secondary", cmp(c, "a", "A"));
    status("ucol_setAttribute", e);
    UChar s[16]; int32_t n = U(s, 16, "abc"); uint8_t key[64];
    int32_t kl = ucol_getSortKey(c, s, n, key, sizeof key);
    num("coll.sortkey_nonempty", kl > 1);
    ucol_close(c);
    /* a locale ICU's trimmed data has no tailoring for falls back to the root collation (a warning, not an error) */
    e = U_ZERO_ERROR; c = ucol_open("de_DE", &e);
    status("ucol_open.de_DE", e);
    if (c) { num("coll.de.a-umlaut_z", cmp(c, "\xc3\xa4", "z")); ucol_close(c); }
}

static void casemap(void) {
    UErrorCode e = U_ZERO_ERROR; UChar src[64], dst[64]; int32_t n, m;
    n = U(src, 64, "stra\xc3\x9f" "e"); m = u_strToUpper(dst, 64, src, n, "de", &e); show("upper.de.strasse", dst, m); status("u_strToUpper", e);
    e = U_ZERO_ERROR; n = U(src, 64, "I"); m = u_strToLower(dst, 64, src, n, "tr", &e); show("lower.tr.I", dst, m);
    e = U_ZERO_ERROR; n = U(src, 64, "i"); m = u_strToUpper(dst, 64, src, n, "tr", &e); show("upper.tr.i", dst, m);
    e = U_ZERO_ERROR; n = U(src, 64, "hELLO wORLD, it's me"); m = u_strToTitle(dst, 64, src, n, NULL, "en_US", &e);
    show("title.en", dst, m); status("u_strToTitle", e);
    e = U_ZERO_ERROR; n = U(src, 64, "\xc3\x85ngstr\xc3\xb6m"); m = u_strFoldCase(dst, 64, src, n, U_FOLD_CASE_DEFAULT, &e); show("fold.Angstrom", dst, m);
}

static int count_breaks(UBreakIteratorType t, const char *loc, const char *utf8, UErrorCode *e) {
    UChar s[256]; int32_t n = U(s, 256, utf8);
    UBreakIterator *b = ubrk_open(t, loc, s, n, e);
    if (U_FAILURE(*e)) return -1;
    int count = 0; ubrk_first(b);
    while (ubrk_next(b) != UBRK_DONE) count++;
    ubrk_close(b);
    return count;
}
static void breaks(void) {
    UErrorCode e = U_ZERO_ERROR; int k;
    k = count_breaks(UBRK_WORD, "en_US", "Hello, world! 42 apples.", &e); num("brk.word.en", k); status("ubrk_open.word", e);
    e = U_ZERO_ERROR; k = count_breaks(UBRK_SENTENCE, "en_US", "One. Two? Three!", &e); num("brk.sentence.en", k);
    e = U_ZERO_ERROR; k = count_breaks(UBRK_CHARACTER, "en_US", "e\xcc\x81" "a\xf0\x9f\x91\xa8\xe2\x80\x8d\xf0\x9f\x91\xa9", &e); num("brk.grapheme", k);
    e = U_ZERO_ERROR; k = count_breaks(UBRK_LINE, "en_US", "The quick brown fox jumps over the lazy dog", &e); num("brk.line.en", k);
    /* Thai and Chinese: the dictionaries are not in the trimmed data; the iterator must still open and finish (the count is not compared) */
    e = U_ZERO_ERROR; k = count_breaks(UBRK_WORD, "th_TH", "\xe0\xb8\xaa\xe0\xb8\xa7\xe0\xb8\xb1\xe0\xb8\xaa\xe0\xb8\x94\xe0\xb8\xb5\xe0\xb8\x84\xe0\xb8\xa3\xe0\xb8\xb1\xe0\xb8\x9a", &e);
    printf("info:brk.word.th=%d\n", k); status("ubrk_open.thai", e);
    e = U_ZERO_ERROR; k = count_breaks(UBRK_WORD, "zh_CN", "\xe6\x88\x91\xe7\x88\xb1\xe5\x8c\x97\xe4\xba\xac\xe5\xa4\xa9\xe5\xae\x89\xe9\x97\xa8", &e);
    printf("info:brk.word.zh=%d\n", k); status("ubrk_open.chinese", e);
}

static void normalization(void) {
    UErrorCode e = U_ZERO_ERROR; UChar src[32], dst[32]; int32_t n, m;
    const UNormalizer2 *nfc = unorm2_getNFCInstance(&e), *nfd = unorm2_getNFDInstance(&e), *nfkc = unorm2_getNFKCInstance(&e);
    status("unorm2_get", e);
    n = U(src, 32, "e\xcc\x81"); m = unorm2_normalize(nfc, src, n, dst, 32, &e); show("nfc.e-acute", dst, m);
    n = U(src, 32, "\xc3\xa9"); m = unorm2_normalize(nfd, src, n, dst, 32, &e); show("nfd.e-acute", dst, m);
    n = U(src, 32, "\xef\xac\x81"); m = unorm2_normalize(nfkc, src, n, dst, 32, &e); show("nfkc.fi-ligature", dst, m);
    e = U_ZERO_ERROR;
    const UNormalizer2 *cf = unorm2_getInstance(NULL, "nfkc_cf", UNORM2_COMPOSE, &e); status("unorm2_getInstance.nfkc_cf", e);
    if (cf) { n = U(src, 32, "\xc3\x85"); m = unorm2_normalize(cf, src, n, dst, 32, &e); show("nfkc_cf.A-ring", dst, m); }
}

static void conv(const char *name, const char *utf8, const char *key) {
    UErrorCode e = U_ZERO_ERROR; UChar s[32]; char out[64];
    UConverter *c = ucnv_open(name, &e);
    if (U_FAILURE(e)) { printf("conv.%s=%s\n", key, u_errorName(e)); failures++; return; }
    int32_t n = U(s, 32, utf8);
    int32_t m = ucnv_fromUChars(c, out, sizeof out, s, n, &e);
    char label[64]; snprintf(label, sizeof label, "conv.%s", key);
    if (U_FAILURE(e)) { printf("%s=%s\n", label, u_errorName(e)); failures++; }
    else {
        UChar back[32]; e = U_ZERO_ERROR;
        int32_t bn = ucnv_toUChars(c, back, 32, out, m, &e);
        printf("%s=", label); for (int32_t i = 0; i < m; i++) printf("%02X", (unsigned)(unsigned char)out[i]);
        printf(" roundtrip=%s\n", (U_SUCCESS(e) && bn == n && !memcmp(back, s, n * 2)) ? "ok" : "BAD");
    }
    ucnv_close(c);
}
static void converters(void) {
    conv("UTF-8", "\xe2\x82\xac", "utf8.euro");
    conv("windows-1252", "\xc3\xa9\xe2\x82\xac", "cp1252.eacute-euro");
    conv("windows-1251", "\xd1\x8f", "cp1251.ya");
    conv("ISO-8859-2", "\xc5\x82", "iso2.lstroke");
    conv("KOI8-R", "\xd1\x8f", "koi8r.ya");
    conv("IBM437", "\xc3\xa9", "cp437.eacute");
    conv("Shift_JIS", "\xe3\x81\x82", "sjis.a");
    conv("windows-31j", "\xe3\x81\x82", "cp932.a");
    conv("EUC-JP", "\xe3\x81\x82", "eucjp.a");
    conv("GBK", "\xe4\xbd\xa0", "gbk.ni");
    conv("GB18030", "\xf0\x9f\x98\x80", "gb18030.emoji");
    conv("Big5", "\xe6\x88\x91", "big5.wo");
    conv("EUC-KR", "\xed\x95\x9c", "euckr.han");
    conv("windows-949", "\xed\x95\x9c", "cp949.han");
    conv("ISO-2022-JP", "\xe3\x81\x82", "iso2022jp.a");
    conv("UTF-16LE", "A", "utf16le.A");
    UErrorCode e = U_ZERO_ERROR;
    const char *iana = ucnv_getStandardName("cp1252", "IANA", &e); text("conv.iana.cp1252", iana ? iana : "(none)");
    num("info:conv.available_names", ucnv_countAvailable());
}

static void timezones(void) {
    UErrorCode e = U_ZERO_ERROR; UChar id[64], out[128]; int32_t n, m;
    /* 2025-07-01 12:00:00 UTC = 1751371200000 ms */
    n = U(id, 64, "America/New_York");
    UCalendar *cal = ucal_open(id, n, "en_US", UCAL_GREGORIAN, &e); status("ucal_open.New_York", e);
    if (cal) {
        ucal_setMillis(cal, 1751371200000.0, &e);
        num("tz.NY.zone_offset_ms", ucal_get(cal, UCAL_ZONE_OFFSET, &e));
        num("tz.NY.dst_offset_ms", ucal_get(cal, UCAL_DST_OFFSET, &e));
        num("tz.NY.in_dst", ucal_inDaylightTime(cal, &e));
        e = U_ZERO_ERROR; m = ucal_getTimeZoneDisplayName(cal, UCAL_STANDARD, "en_US", out, 128, &e); show("tz.NY.standard_name", out, m);
        m = ucal_getTimeZoneDisplayName(cal, UCAL_SHORT_DST, "en_US", out, 128, &e); show("tz.NY.short_dst", out, m);
        ucal_close(cal);
    }
    n = U(id, 64, "Europe/Istanbul"); e = U_ZERO_ERROR;
    cal = ucal_open(id, n, "en_US", UCAL_GREGORIAN, &e);
    if (cal) { ucal_setMillis(cal, 1751371200000.0, &e); num("tz.Istanbul.zone_offset_ms", ucal_get(cal, UCAL_ZONE_OFFSET, &e)); ucal_close(cal); }
    status("ucal_open.Istanbul", e);
    /* Qt's QTimeZone maps Windows ids to IANA ids and back through windowsZones.res */
    e = U_ZERO_ERROR; n = U(id, 64, "America/New_York"); m = ucal_getWindowsTimeZoneID(id, n, out, 128, &e); show("tz.windows_id.NY", out, m);
    e = U_ZERO_ERROR; n = U(id, 64, "Eastern Standard Time"); m = ucal_getTimeZoneIDForWindowsID(id, n, "US", out, 128, &e); show("tz.iana_id.EST.US", out, m);
    e = U_ZERO_ERROR; UEnumeration *en = ucal_openTimeZones(&e); status("ucal_openTimeZones", e);
    if (en) { int32_t c = uenum_count(en, &e); num("tz.count_over_400", c > 400); uenum_close(en); }
    e = U_ZERO_ERROR; n = U(id, 64, "Asia/Calcutta"); m = ucal_getCanonicalTimeZoneID(id, n, out, 128, NULL, &e); show("tz.canonical.Calcutta", out, m);
}

static void formatting(void) {
    UErrorCode e = U_ZERO_ERROR; UChar pat[64], utc[8], out[128]; int32_t m;
    U(utc, 8, "UTC"); int32_t pn = U(pat, 64, "yyyy-MM-dd HH:mm:ss EEEE MMMM");
    UDateFormat *f = udat_open(UDAT_PATTERN, UDAT_PATTERN, "en_US", utc, 3, pat, pn, &e); status("udat_open", e);
    if (f) {
        m = udat_format(f, 946825445000.0, out, 128, NULL, &e);   /* 2000-01-02 15:04:05 UTC, a Sunday */
        show("udat.pattern.en", out, m);
        udat_close(f);
    }
    e = U_ZERO_ERROR;
    UNumberFormat *nf = unum_open(UNUM_DECIMAL, NULL, 0, "en_US", NULL, &e); status("unum_open.decimal", e);
    if (nf) { m = unum_formatDouble(nf, 1234567.891, out, 128, NULL, &e); show("unum.decimal.en", out, m); unum_close(nf); }
    e = U_ZERO_ERROR; U(pat, 64, "USD");
    nf = unum_open(UNUM_CURRENCY, NULL, 0, "en_US", NULL, &e);
    if (nf) { unum_setTextAttribute(nf, UNUM_CURRENCY_CODE, pat, 3, &e); m = unum_formatDouble(nf, 1234.5, out, 128, NULL, &e); show("unum.currency.en", out, m); unum_close(nf); }
    status("unum_open.currency", e);
    e = U_ZERO_ERROR;
    nf = unum_open(UNUM_SPELLOUT, NULL, 0, "en_US", NULL, &e);
    if (nf) { m = unum_formatInt64(nf, 42, out, 128, NULL, &e); show("unum.spellout.en", out, m); unum_close(nf); }
    status("unum_open.spellout", e);
}

static void locales(void) {
    UErrorCode e = U_ZERO_ERROR; char buf[128]; UChar out[128]; int32_t m;
    m = uloc_forLanguageTag("zh-Hant-TW", buf, sizeof buf, NULL, &e); text("uloc.fromTag", buf); (void)m;
    e = U_ZERO_ERROR; uloc_toLanguageTag("sr_Latn_RS", buf, sizeof buf, 0, &e); text("uloc.toTag", buf);
    e = U_ZERO_ERROR; uloc_addLikelySubtags("en", buf, sizeof buf, &e); text("uloc.likely.en", buf);
    e = U_ZERO_ERROR; uloc_canonicalize("en_us.utf8", buf, sizeof buf, &e); text("uloc.canonical", buf);
    e = U_ZERO_ERROR; m = uloc_getDisplayLanguage("fr", "en", out, 128, &e); show("uloc.display.fr.in.en", out, m);
    e = U_ZERO_ERROR; m = uloc_getDisplayCountry("und_JP", "en", out, 128, &e); show("uloc.display.JP.in.en", out, m);
    e = U_ZERO_ERROR; m = uloc_getDisplayLanguage("fr", "de", out, 128, &e); printf("info:"); show("uloc.display.fr.in.de", out, m);
    num("info:uloc.available", uloc_countAvailable());
}

static void misc(void) {
    UErrorCode e = U_ZERO_ERROR; UChar src[64], out[64]; char ascii[128]; int32_t n, m;
    UIDNAInfo info = UIDNA_INFO_INITIALIZER; UIDNA *idna = uidna_openUTS46(UIDNA_DEFAULT, &e); status("uidna_openUTS46", e);
    if (idna) {
        n = U(src, 64, "b\xc3\xbc" "cher.de"); m = uidna_nameToASCII(idna, src, n, out, 64, &info, &e);
        u_UCharsToChars(out, ascii, m); ascii[m] = 0; text("idna.bucher", ascii);
        uidna_close(idna);
    }
    e = U_ZERO_ERROR; n = U(src, 64, "a+b");
    URegularExpression *re = uregex_open(src, n, 0, NULL, &e); status("uregex_open", e);
    if (re) { U(out, 64, "xaaabz"); uregex_setText(re, out, 6, &e); num("regex.find", uregex_find(re, 0, &e)); num("regex.start", uregex_start(re, 0, &e)); uregex_close(re); }
    num("script.thai", uscript_getScript(0x0E01, &e));
    num("char.isalpha.a-umlaut", u_isalpha(0xE4));
    num("char.digit.arabic-3", u_charDigitValue(0x0663));
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    versions(); collation(); casemap(); breaks(); normalization(); converters(); timezones(); formatting(); locales(); misc();
    printf("failures=%d\n", failures);
    return failures ? 1 : 0;
}
