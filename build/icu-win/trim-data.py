#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Which items of ICU's data package BCD's icuuc-optin.dll leaves out (tools/build-icu-optin.sh).

  trim-data.py ITEM_LIST       ITEM_LIST: `icupkg -l icudt78l.dat` (one item name a line, e.g. coll/de.res)
      prints the items to remove, one a line, for `icupkg -r`; prints nothing else.
  trim-data.py --keep ITEM_LIST    prints the items that stay instead.

The ICU 78.3 source tarball carries its data only as the finished package source/data/in/icudt78l.dat (33 MB, 4305 items); the
CLDR text sources are not in it, so the package is trimmed item by item with icupkg, not with ICU's data filter JSON.

What Qt 6 needs from ICU (QCollator, QStringConverter / QTextCodec, QTimeZone, QLocale's case mapping, the break iterators) is
the root locale, English, the Unicode property and normalisation data, the converters for the encodings a Windows program meets, the
time zone data and the collation root. What stays out: every other locale (a program that asks for "de" gets the root or English
data, ICU's normal fall-back, with U_USING_DEFAULT_WARNING), the collation tailorings of other languages, the break-iterator
dictionaries (Thai, Lao, Khmer, Burmese, Chinese/Japanese word breaking), the transliteration rules, the character names, the
confusables (spoof checker) and the converters of mainframe and rarely used East Asian encodings. Details in build/icu-win/BCD-SOURCE.txt.
Pure rules over names: no file is read except the list, and the same list always gives the same answer.
"""
import re
import sys

# The locales every kept tree keeps: the root, English, and English's parents and its two most used variants.
KEEP_LOCALES = {"root", "en", "en_001", "en_GB", "en_US", "en_US_POSIX"}
# Non-locale items inside the locale trees.
TREE_EXTRA = {"pool", "res_index", "supplementalData"}
# A locale name: 2-3 lowercase letters, then nothing or _-separated parts (script, region, variant; collation variants such as de__PHONEBOOK
# have an empty part): en, zh_Hant_TW, ca_ES_VALENCIA, ja_JP_TRADITIONAL. The camel-case data names (metaZones, supplementalData, ...)
# and root, pool do not match; res_index does (it is kept by name, TREE_EXTRA).
LOCALE = re.compile(r"^[a-z]{2,3}(_[A-Za-z0-9]*)*$")
# Trees whose every locale file but the kept ones goes (curr, lang, region, zone, unit, rbnf, coll, brkitr) and the whole trees that go.
LOCALE_TREES = {"curr", "lang", "region", "zone", "unit", "rbnf", "coll", "brkitr"}
DROP_TREES = {"translit"}
# Break-iterator dictionaries: the word-break data of Thai, Lao, Khmer, Burmese and Chinese/Japanese (3.0 MB). Without them ICU's rule
# based iterator breaks those scripts by its rules only (no dictionary engine), which is what it does for any script it has no word list for.
DROP_SUFFIX = (".dict",)
# Single items by name.
DROP_ITEMS = {"unames.icu",       # character names (u_charName, \N{...}): 0.34 MB
              "confusables.cfu"}  # the spoof checker's data: 0.05 MB
# Converters: every .cnv stays (the single-byte tables and the small mappings are about 3 KB each) except the ones named here, which are
# the large mappings of mainframe (EBCDIC double-byte) code pages and of East Asian encodings no Windows program writes today (EUC-TW, ISO-2022-CN,
# ISO-2022-CN-EXT, GB 2312-80 and ISO-2022-JP-2 stop opening; EUC-JP, EUC-KR, GBK, GB 18030, Big5, Big5-HKSCS, Shift_JIS, windows-949 and every IANA single-byte
# set stay: the check at the end of tools/build-icu-optin.sh opens each of them).
# icupkg refuses a package in which a kept converter needs a dropped one, so each base table whose extension is dropped goes with it
# (ibm-16684 and ibm-1399 are built on ibm-1390, ibm-937 on ibm-1371, ibm-939 on ibm-930, ibm-33722_P120 on the 2004 EUC-JP table); windows-949 needs
# ibm-1363_P11B and so that one stays.
DROP_CNV = {
    "cns-11643-1992", "euc-tw-2014", "ibm-1364_P110-2007", "ibm-1371_P100-1999",
    "ibm-1388_P100-2024", "ibm-1390_P110-2003", "ibm-33722_P12A_P12A-2004_U2", "ibm-5478_P100-1995",
    "ibm-930_P120-1999", "ibm-933_P110-1995", "ibm-935_P110-1999", "ibm-954_P101-2007", "ibm-964_P110-1999",
    "icu-internal-25546", "icu-internal-compound-t", "iso-ir-165",
    "ibm-16684_P110-2003", "ibm-937_P110-1999", "ibm-939_P120-1999", "ibm-33722_P120-1999", "ibm-1399_P110-2003",
}


def split(item):
    """(tree, name, extension) of an item such as coll/de.res, zone/pool.res or cnvalias.icu; tree is '' for a top-level item."""
    tree, _, rest = item.rpartition("/")
    name, dot, ext = rest.rpartition(".")
    return tree, name, ext if dot else ""


def removed(item):
    tree, name, ext = split(item)
    if item in DROP_ITEMS or item.endswith(DROP_SUFFIX):
        return True
    if tree in DROP_TREES:
        return True
    if tree == "" and ext == "cnv":
        return name in DROP_CNV
    if ext == "res" and (tree in LOCALE_TREES or tree == ""):
        if name in KEEP_LOCALES or name in TREE_EXTRA:
            return False
        return bool(LOCALE.match(name))
    if tree == "brkitr" and ext == "brk":
        return False
    return False


def main(argv):
    keep = "--keep" in argv
    args = [a for a in argv if a != "--keep"]
    if len(args) != 1:
        sys.exit(__doc__)
    items = [line.strip() for line in open(args[0], encoding="utf-8") if line.strip()]
    for item in items:
        if removed(item) != keep:
            print(item)


if __name__ == "__main__":
    main(sys.argv[1:])
