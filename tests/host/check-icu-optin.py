#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""icuuc-optin.dll and icuin-optin.dll (env.MADEIRA_OPTIN_DLLS = icuuc,icuin): ICU for Qt 6 programs (M1-S10, build 708).

Qt6Core.dll imports icuuc.dll and icuin.dll. Windows 10 and newer have them in System32; Wine and BCD had neither, and a PySide6 / PyInstaller
launcher (Bloodborne's, Anki, the Albion launcher) stopped with c0000135 ("Library icuuc.dll (which is needed by ...\\Qt6Core.dll) not found").
BCD builds ICU4C 78.3 for ARM64EC with its llvm-mingw, the C API unversioned as Windows' ICU has it, and ships the two DLLs opt-in, as
dxcompiler-optin.dll:

  - tools/build-icu-optin.sh: the tarball from Debian's archive by SHA-256 (a mismatch stops by name), its LICENSE checked against the one that
    ships, a native ICU for the tools, the data package trimmed with icupkg (build/icu-win/trim-data.py: root and English, 6.8 MB of 33), the
    trimmed data judged by a Qt-like probe on the Mac (build/icu-win/icu-probe.c, expected output build/icu-win/icu-probe.expected), ICU's own
    configure and makefiles for common and i18n, the data linked into icuuc.dll, stripped, checked (build/icu-win/check-icu-dlls.py), shipped as
    arm64ec-windows/icuuc-optin.dll and icuin-optin.dll; no llvm-mingw: nothing shipped, exit 0;
  - the app links <name>-optin.dll as <name>.dll for a game that lists the name (no code of its own), tools/check-optin-dlls-shipped.py asks for both files.

What runs here, always: the script's pins and steps, the trim rules, the checker's header reader, the probe and its expected answers, the licence,
and the wiring of every file this change touches. Wiring that is not applied yet is reported as "todo" (the lead applies the snippets of M1-S10);
`--strict` (or BCD_ICU_STRICT=1) makes a todo a failure, and the day all of it is in, STRICT below is set to True.

With files at hand it also checks them: BCD_ICU_OUT (default build/icu-win/out) and app/Madeira/arm64ec-windows/icu{uc,in}-optin.dll, the ARM64EC
DLLs, against the headers of the extracted tarball (build/icu-win/work/src/icu/source) when it is there, and every import against BCD's own
ARM64EC farm (BCD_ARM64EC_FARM, default the repository's arm64ec-windows folder when it holds kernel32, kernelbase, advapi32 and ucrtbase).

  BCD_ICU_BUILD=1    run tools/build-icu-optin.sh for ARM64EC into a temporary folder first (needs llvm-mingw and the tarball: network, or
                     ICU_TARBALL=<file>; BCD_ICU_WORK=<dir> keeps the downloaded tarball, the source and the host build between runs)
  BCD_HOST_WINE      a Wine that runs x86-64 programs (CrossOver's bin/wine works): with it, the x86-64 test flavour of the same build
                     (ICU_TARGET=x86_64-w64-mingw32; BCD_ICU_X64_DIR = a folder with its icuuc.dll, icuin.dll and icu-probe.exe, else built
                     as above) runs icu-probe.exe, which must answer exactly as expected: the DLLs load, export the names, carry the data.
                     ARM64EC itself cannot run on a Mac: what a device has to show is listed in the owner's report.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
STRICT = True   # round 707: the snippets are in (the lead, 2026-11-22)
ok = True
todo = 0


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


def wired(what, applied, cond):
    """A check of a file this change does not own: skipped as a "todo" until `applied` (the file shows the change), then it must hold."""
    global todo
    if applied:
        check(what, cond)
    elif STRICT:
        check(what + ' (not applied)', False)
    else:
        todo += 1
        print('todo ' + what)


TEMPS = []


def mk(prefix):
    """A temporary folder, removed when the check ends (a build's output is about 30 MB; BCD_ICU_WORK is the one place that is kept)."""
    d = tempfile.mkdtemp(prefix=prefix)
    TEMPS.append(d)
    return d


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors='replace', **kw)


D = root / 'build/icu-win'
build = (root / 'tools/build-icu-optin.sh').read_text()
src_note = (D / 'BCD-SOURCE.txt').read_text() if (D / 'BCD-SOURCE.txt').exists() else ''
lic_file = D / 'ICU-LICENSE.txt'
lic_sha = 'e55522d81edc687a341a4411e0776e54ca654e90147f354a90458aaced4116af'

# ---------------------------------------------------------------- the build script
check('the script pins ICU 78.3 by the SHA-256 of Debian\'s icu_78.3.orig.tar.gz (Checksums-Sha256 of icu_78.3-2.dsc), from Debian\'s archive and not from GitHub',
      'ICU_VERSION=78.3' in build and 'ICU_SHA256=3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0' in build
      and 'https://deb.debian.org/debian/pool/main/i/icu/$ICU_FILE' in build and 'https://snapshot.debian.org/archive/debian/' in build
      and 'github' not in build.replace('NOT GitHub', '').replace('nothing from GitHub', '').lower())
check('a file that is not the pinned one stops the script by name before anything is unpacked ("ICU_SHA256 mismatch"), whether it came from a source or ICU_TARBALL',
      build.count('ICU_SHA256 mismatch') >= 2 and build.index('verify_tar "$ICU_TARBALL"') < build.index('tar -xzf "$TAR"')
      and '[ "$(sha "$TAR.part")" != "$ICU_SHA256" ]' in build and 'rm -f "$TAR.part"' in build)
check('a source that cannot be reached tries the next one; a wrong file does not (it stops)',
      'for url in "${ICU_URLS[@]}"' in build and 'could not be downloaded' in build and 'die "ICU_SHA256 mismatch' in build)
check('the tarball\'s LICENSE is checked against its pin and against build/icu-win/ICU-LICENSE.txt, which ships',
      'ICU_LICENSE_SHA256=%s' % lic_sha in build and 'cmp -s "$ICU/LICENSE" "$LICENSE_FILE"' in build and 'ICU_LICENSE_SHA256 mismatch' in build)
check('only what CI has is needed: macOS tools, python3, curl, tar, shasum, llvm-mingw; no git, no network after the download, no pip',
      all(t not in build for t in ('git clone', 'git -C', 'pip ', 'brew ', 'apt-get', 'npm ')) and 'python3 -I' in build)
check('no llvm-mingw ships nothing and exits 0; a failure of the download, a checksum, the build or a check exits 1',
      'are not built"\n    exit 0' in build and 'die() { echo "build-icu-optin: $*" >&2; exit 1; }' in build)
check('ICU\'s own configure and makefiles, no patch: --disable-renaming (unversioned C names), common and i18n only (no tools, tests, samples, extras, layoutex, icuio), shared, cross build against the host build',
      all(f in build for f in ('--disable-renaming', '--disable-tools', '--disable-tests', '--disable-samples', '--disable-extras',
                               '--disable-layoutex', '--disable-icuio', '--enable-shared', '--disable-static', '--with-cross-build="$HOST"',
                               'make SHELL=/bin/bash -j"$JOBS" -C "$B/common"', 'make SHELL=/bin/bash -j"$JOBS" -C "$B/i18n"'))
      and 'patch' not in build.replace('No patch', '').replace('(no patch', '').split('set -euo pipefail')[1].lower())
check('the DLLs are named icuuc.dll / icuin.dll (SO_TARGET_VERSION_MAJOR= drops ICU\'s 78), the data is an object in icuuc.dll (icudt78_dat, .incbin; no icudt78.dll), icuin links the import library of icuuc',
      'SO_TARGET_VERSION_MAJOR=' in build and 'LIBICUDT="$B/data-embed/icudt${ICU_MAJOR}_dat.o"' in build and '.incbin "icudt${ICU_MAJOR}l.dat"' in build
      and 'LIBICUUC="$B/lib/libicuuc.dll.a"' in build and '.globl icudt${ICU_MAJOR}_dat' in build)
check('libc++, libunwind and the MinGW runtime are linked in (no libc++.dll), the build machine\'s paths are mapped away, the PE time stamp is left at 0 (reproducible), and macOS\'s sh is not used for the makefiles (echo -n)',
      '-static-libgcc -static-libstdc++ -Wl,--no-insert-timestamp' in build and 'CPPFLAGS="-ffile-prefix-map=$WORK=build"' in build and 'SHELL=/bin/bash' in build)
check('the DLLs must need no runtime pseudo relocations (MinGW auto-import fix-ups of data at load time): the script reads the list bounds from the symbol table of the unstripped DLL and stops if the list is not empty',
      '__RUNTIME_PSEUDO_RELOC_LIST__' in build and '__RUNTIME_PSEUDO_RELOC_LIST_END__' in build and 'runtime pseudo relocations' in build
      and build.index('runtime pseudo relocations') < build.index('"$MINGW/llvm-strip" -o'))
check('the trimmed data is judged by the probe on the Mac before any DLL is built, and icupkg\'s dependency check may complain only about the five dictionaries',
      build.index('diff -u "$EXPECTED" "$HP/out.txt"') < build.index('LD="-static-libgcc -static-libstdc++')
      and 'brkitr/(cj|khmer|lao|burmese|thai)dict' in build and '"$HOST/bin/icupkg" --ignore-deps -r' in build)
check('the result is stripped, checked with the public headers of both libraries, stamped, and shipped only as icuuc-optin.dll / icuin-optin.dll (never as icuuc.dll or icuin.dll), and only for ARM64EC',
      '"$MINGW/llvm-strip" -o "$OUT/$d.dll" "$B/lib/$d.dll"' in build and '--icu-source "$ICU/source"' in build and 'echo "$STAMP" > "$OUT/stamp"' in build
      and '"$SHIP_DIR/$d-optin.dll"' in build and not re.search(r'SHIP_DIR/(\$d|icuuc|icuin)\.dll', build)
      and 'arm64ec-w64-mingw32) FLAVOUR=arm64ec; SHIPS=1' in build and 'x86_64-w64-mingw32)  FLAVOUR=x86_64;  SHIPS=0' in build)
check('current DLLs in OUT (the stamp: pin, script, trim rules, probe, expected answers, licence, compiler) are shipped without a build',
      '[ "$(cat "$OUT/stamp" 2>/dev/null)" = "$STAMP" ]' in build and 'shasum -a 256 "$SELF" "$TRIM" "$PROBE" "$EXPECTED" "$LICENSE_FILE"' in build)
r = sh(['bash', '-n', str(root / 'tools/build-icu-optin.sh')])
check('the script is valid bash', r.returncode == 0)
r = sh(['bash', str(root / 'tools/build-icu-optin.sh')], env=dict(os.environ, MINGW='/nonexistent', WORK=tempfile.gettempdir() + '/icu-none'))
check('with no llvm-mingw it says so, ships nothing and exits 0', r.returncode == 0 and 'no llvm-mingw' in r.stdout and 'are not built' in r.stdout)
tmp_tar = Path(mk('check-icu-tar-')) / 'icu_78.3.orig.tar.gz'
tmp_tar.write_bytes(b'not the tarball')
mingw_dir = Path(os.environ.get('MINGW') or (root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'))
if (mingw_dir / 'arm64ec-w64-mingw32-clang').exists():
    r = sh(['bash', str(root / 'tools/build-icu-optin.sh')], env=dict(os.environ, MINGW=str(mingw_dir), ICU_TARBALL=str(tmp_tar),
                                                                     WORK=mk('check-icu-work-'), OUT=mk('check-icu-out-'),
                                                                     SHIP_DIR=mk('check-icu-ship-')))
    check('a file that is not the pinned tarball stops the script by name, exit 1, nothing shipped',
          r.returncode == 1 and 'ICU_SHA256 mismatch' in r.stderr and 'icu_78.3.orig.tar.gz' in r.stderr and '3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0' in r.stderr)
else:
    print('skip the wrong-tarball run (no llvm-mingw at %s)' % mingw_dir)

# ---------------------------------------------------------------- the licence and the provenance note
lic = lic_file.read_bytes() if lic_file.exists() else b''
check('the ICU LICENSE is kept exactly as received (Unicode License V3 with the third-party notices; SHA-256 of icu/LICENSE in the 78.3 tarball)',
      lic and __import__('hashlib').sha256(lic).hexdigest() == lic_sha and b'UNICODE LICENSE V3' in lic and b'SPDX-License-Identifier: Unicode-3.0' in lic)
check('BCD-SOURCE.txt names the version, the source and its checksum, the licence, that nothing is patched, what is trimmed, and that nothing of ICU is linked into BCD',
      src_note and all(t in src_note for t in ('ICU 78.3', 'icu_78.3.orig.tar.gz', '3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0',
                                                'Unicode License V3', 'No patch', 'icuuc-optin.dll', 'icuin-optin.dll', 'trim-data.py', 'nothing of ICU is linked into BCD')))

# ---------------------------------------------------------------- the trim rules
trim = load(D / 'trim-data.py', 'trim_data')
gone = lambda *items: all(trim.removed(i) for i in items)
kept = lambda *items: not any(trim.removed(i) for i in items)
check('the trim rules keep the root and English locales in every tree, the pool and index files, and the non-locale data',
      kept('root.res', 'en.res', 'en_001.res', 'en_GB.res', 'en_US.res', 'en_US_POSIX.res', 'res_index.res', 'pool.res', 'supplementalData.res',
           'zoneinfo64.res', 'metaZones.res', 'windowsZones.res', 'timezoneTypes.res', 'keyTypeData.res', 'likelySubtags.res', 'numberingSystems.res',
           'curr/root.res', 'curr/en.res', 'curr/pool.res', 'curr/supplementalData.res', 'lang/en.res', 'lang/pool.res', 'region/en.res', 'zone/en.res',
           'zone/root.res', 'zone/tzdbNames.res', 'unit/en.res', 'rbnf/en.res', 'coll/root.res', 'coll/en.res', 'coll/ucadata.icu', 'coll/res_index.res',
           'brkitr/root.res', 'brkitr/word.brk', 'brkitr/line.brk', 'brkitr/char.brk', 'brkitr/sent.brk', 'brkitr/title.brk'))
check('it keeps the Unicode data and the converters a Windows program meets (every IANA single-byte set, GBK, GB 18030, Big5, Big5-HKSCS, Shift_JIS, EUC-JP, EUC-KR, windows-949)',
      kept('cnvalias.icu', 'nfkc.nrm', 'nfkc_cf.nrm', 'nfkc_scf.nrm', 'uts46.nrm', 'uemoji.icu', 'ulayout.icu', 'rfc3491.spp',
           'ibm-5348_P100-1997.cnv', 'ibm-5346_P100-1998.cnv', 'ibm-912_P100-1995.cnv', 'ibm-878_P100-1996.cnv', 'ibm-437_P100-1995.cnv', 'windows-874-2000.cnv',
           'ibm-1386_P100-2001.cnv', 'gb18030-2022.cnv', 'ibm-1373_P100-2002.cnv', 'ibm-5471_P100-2006.cnv', 'ibm-1375_P100-2008.cnv', 'ibm-943_P15A-2003.cnv',
           'euc-jp-2007.cnv', 'ibm-970_P110_P110-2006_U2.cnv', 'ibm-949_P11A-1999.cnv', 'ibm-1363_P11B-1998.cnv', 'windows-949-2000.cnv'))
check('it removes the other locales, the tailored collations, the word-break dictionaries, transliteration, character names, the spoof data and mainframe / rare East Asian converters',
      gone('de.res', 'zh_Hant_TW.res', 'ja_JP_TRADITIONAL.res', 'ca_ES_VALENCIA.res', 'sr_Latn_BA.res', 'en_AU.res', 'curr/de.res', 'lang/fr.res', 'region/ja.res',
           'zone/tr.res', 'unit/de.res', 'rbnf/es.res', 'coll/zh.res', 'coll/de__PHONEBOOK.res', 'coll/ja.res', 'brkitr/cjdict.dict', 'brkitr/thaidict.dict',
           'brkitr/khmerdict.dict', 'brkitr/laodict.dict', 'brkitr/burmesedict.dict', 'brkitr/ja.res', 'translit/root.res', 'translit/en.res',
           'unames.icu', 'confusables.cfu', 'euc-tw-2014.cnv', 'cns-11643-1992.cnv', 'ibm-933_P110-1995.cnv', 'ibm-1388_P100-2024.cnv', 'icu-internal-compound-t.cnv'))
check('the rules are pure functions of the item name: split() cuts tree, name and extension, and the same item always gets the same answer',
      trim.split('coll/de.res') == ('coll', 'de', 'res') and trim.split('cnvalias.icu') == ('', 'cnvalias', 'icu') and trim.split('zone/tzdbNames.res') == ('zone', 'tzdbNames', 'res')
      and trim.removed('coll/zh.res') is True and trim.removed('coll/root.res') is False and trim.removed('coll/zh.res') == trim.removed('coll/zh.res'))
listing = D / 'work/data/all.lst'
if listing.exists():
    items = [l.strip() for l in listing.read_text().splitlines() if l.strip()]
    keep_n = sum(1 for i in items if not trim.removed(i))
    check('the real package list (%d items) keeps %d' % (len(items), keep_n), len(items) > 4000 and 250 <= keep_n <= 300)

# ---------------------------------------------------------------- the checker and its header reader
chk = load(D / 'check-icu-dlls.py', 'check_icu_dlls')
with tempfile.TemporaryDirectory() as td:
    for sub in ('common', 'i18n'):
        (Path(td) / sub / 'unicode').mkdir(parents=True)
    (Path(td) / 'common/unicode/ustring.h').write_text('''
/** @code u_example_in_comment(x); U_CAPI int U_EXPORT2 u_commented_out(void); @endcode */
U_CAPI int32_t U_EXPORT2
u_strlen(const char16_t *s);
U_CAPI const char16_t * U_EXPORT2 u_strchr(const char16_t *s, char16_t c);
U_CAPI UBool U_EXPORT2
    u_wrapped (
        int a);
static inline int not_exported(void) { return 0; }
''')
    (Path(td) / 'common/unicode/icuplug.h').write_text('U_CAPI int U_EXPORT2 uplug_getPlugName(void *p);\n')
    (Path(td) / 'i18n/unicode/ucol.h').write_text('U_CAPI UCollator* U_EXPORT2\nucol_open(const char *loc, UErrorCode *status);\n')
    check('the checker reads U_CAPI ... U_EXPORT2 name( over lines, skips comments, static inline functions and icuplug.h (the plug-in API ICU does not build)',
          chk.header_functions(td, 'uc') == {'u_strlen', 'u_strchr', 'u_wrapped'} and chk.header_functions(td, 'in') == {'ucol_open'})
check('the checker names the ICU functions Qt 6 uses, with no duplicates', len(chk.QT_COMMON) == len(set(chk.QT_COMMON)) and len(chk.QT_I18N) == len(set(chk.QT_I18N))
      and {'u_strToUpper', 'ubrk_open', 'ucnv_open'} <= set(chk.QT_COMMON) and {'ucol_open', 'ucal_open', 'udat_open'} <= set(chk.QT_I18N))
check('what the checker allows imports of is kernel32, advapi32 and the UCRT sets (icuuc.dll for icuin only); no runtime DLL',
      chk.ALLOWED == {'kernel32.dll', 'advapi32.dll'} and 'libc++' in chk.FORBIDDEN_PREFIX and 'icudt' in chk.FORBIDDEN_PREFIX)

# ---------------------------------------------------------------- the probe
probe = (D / 'icu-probe.c').read_text()
expected = (D / 'icu-probe.expected').read_text()
check('the probe is a Qt-like user of the C API: collation, case mapping, break iterators, normalisation, converters, time zones, formatting, locales, IDNA, regular expressions',
      all(f in probe for f in ('ucol_strcoll', 'u_strToTitle', 'ubrk_open', 'unorm2_normalize', 'ucnv_fromUChars', 'ucal_getWindowsTimeZoneID',
                               'ucal_getTimeZoneIDForWindowsID', 'udat_format', 'unum_formatDouble', 'uloc_forLanguageTag', 'uidna_nameToASCII', 'uregex_find')))
check('its answers are expected from ICU 78.3 with the full data; lines that differ by design are "info:" and are not in the expected file',
      'icu.version=78.3' in expected and 'unicode.version=17.0' in expected and 'failures=0' in expected and 'info:' not in expected
      and 'conv.sjis.a=82A0 roundtrip=ok' in expected and 'tz.windows_id.NY=Eastern Standard Time' in expected and 'tz.iana_id.EST.US=America/New_York' in expected
      and expected.count('\n') == 87 and probe.count('printf("info:') + probe.count('"info:') >= 5)

# ---------------------------------------------------------------- wiring: the files this change does not own
policy_p = root / 'app/Madeira/GameRecommendationsList.swift'
policy = policy_p.read_text()
case = policy[policy.index('case "MADEIRA_OPTIN_DLLS":'):policy.index('case "MADEIRA_WG_VPROC":')]
has_policy = '"icuuc"' in case
wired('a downloaded list may name icuuc and icuin (else a 708 device would refuse the whole online list); the message names them',
      has_policy, '|| ["icuuc", "icuin"].contains($0)' in case and 'rstrtmgr, or icuuc, icuin, or glu32, tdh,' in case)
wired('the policy text that check-kernelbase-optin.py, check-dxcompiler-optin.py and check-optin-dlls.py assert (the guard tail, the message from "or glu32" on) is as it was',
      has_policy, '|| $0 == "kernelbase" || $0 == "dcomp" || $0 == "msvproc" || $0 == "xinputuap" || $0 == "httpapi" || $0 == "dbgcore" }) else {' in policy
      and 'or glu32, tdh, gamingtcui, qwave, or kernelbase, dcomp, msvproc, xinputuap, httpapi, dbgcore, or msvcp_win, bluetoothapis, gameinput, ntdsapi, mfplay, avicap32, coremessaging, combase, or dxcompiler, or mscms")' in policy)
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");'):]
check('the bridge links any shipped <name>-optin.dll the game lists as <name>.dll in sysx64 (and system32 in an ARM64EC session), and removes only a link of ours otherwise',
      'hasSuffix:@"-optin.dll"' in blk and 'drive_c/windows/sysx64' in blk and '[to.lastPathComponent isEqualToString:file]' in blk)
wired('listing icuin brings icuuc along (icuin.dll imports icuuc.dll), as evr brings dxva2', 'containsObject:@"icuin"' in blk,
      '[wanted containsObject:@"icuin"]) [wanted addObject:@"icuuc"];' in blk)
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
opt = gen[gen.index('"env.MADEIRA_OPTIN_DLLS": {'):gen.index('"env.MADEIRA_WINTYPES": {')]
has_catalog = 'icuuc' in opt
wired('the catalog says what icuuc and icuin are, names the build script and that auto-tune adds them',
      has_catalog, '"tools/build-icu-optin.sh"' in opt and 'icuuc and icuin are ICU' in opt and 'it adds icuuc,icuin (build 708)' in opt)
cat_swift = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
wired('the generated catalog has it (run build/tools/gen-config-catalog.py)', has_catalog, 'icuuc and icuin are ICU' in cat_swift)
# gitignore, licences, notices
gi = (root / '.gitignore').read_text()
wired('the source, the build trees and the DLLs are not committed', 'build/icu-win/work/' in gi,
      'build/icu-win/work/\n' in gi and 'build/icu-win/out/\n' in gi and 'app/Madeira/arm64ec-windows/icuuc-optin.dll\n' in gi
      and 'app/Madeira/arm64ec-windows/icuin-optin.dll\n' in gi)
lp, la = root / 'LICENSES/ICU-LICENSE.txt', root / 'app/Madeira/legal/LICENSES/ICU-LICENSE.txt'
wired("ICU's LICENSE ships: LICENSES/ICU-LICENSE.txt and the app's legal/LICENSES/ICU-LICENSE.txt are the bytes of build/icu-win/ICU-LICENSE.txt",
      lp.exists() or la.exists(), lp.exists() and la.exists() and lp.read_bytes() == lic and la.read_bytes() == lic)
notices_p = root / 'THIRD-PARTY-NOTICES.md'
notices = notices_p.read_text()
wired("THIRD-PARTY-NOTICES.md has ICU's row (licence, version, source and its checksum, trimmed data, no patch, the two DLLs) and the app's copy is the same file",
      '**ICU' in notices, '| **ICU for C/C++ (ICU4C)** 78.3' in notices and 'Unicode License V3' in notices and '`arm64ec-windows/icuuc-optin.dll`' in notices
      and '`arm64ec-windows/icuin-optin.dll`' in notices and 'LICENSES/ICU-LICENSE.txt' in notices and 'tools/build-icu-optin.sh' in notices
      and '3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0' in notices
      and (root / 'app/Madeira/legal/THIRD-PARTY-NOTICES.md').read_text() == notices)
short_p = root / 'app/Madeira/licenses/THIRD-PARTY-NOTICES.txt'
short = short_p.read_text()
wired('the shipped short notice names ICU, its licence and the two DLLs', 'ICU' in short and 'icuuc-optin.dll' in short,
      'ICU 78.3' in short and 'Unicode License V3' in short and 'icuuc-optin.dll' in short and 'icuin-optin.dll' in short and 'legal/LICENSES/ICU-LICENSE.txt' in short)
# CI
wf = (root / '.github/workflows/build-ipa.yml').read_text()
key = "icu-win-arm64ec-v1-${{ hashFiles('tools/build-icu-optin.sh', 'build/icu-win/trim-data.py', 'build/icu-win/icu-probe.c', 'build/icu-win/icu-probe.expected', 'build/icu-win/check-icu-dlls.py', 'build/icu-win/ICU-LICENSE.txt') }}"
has_ci = 'build-icu-optin.sh' in wf
if has_ci:
    i_dxc = wf.find('- name: Build dxcompiler.dll and ship it as dxcompiler-optin.dll')
    i_restore = wf.find('- name: Restore ICU (icuuc.dll and icuin.dll, ARM64EC)')
    i_build = wf.find('- name: Build ICU and ship it as icuuc-optin.dll and icuin-optin.dll')
    i_save = wf.find('- name: Save ICU (icuuc.dll and icuin.dll, ARM64EC)')
    i_verify = wf.find('- name: Verify the opt-in DLLs the lists and rules name')
    i_chk = wf.find('- name: Check the ICU opt-in DLLs')
else:
    i_dxc = i_restore = i_build = i_save = i_verify = i_chk = -1
wired('CI restores, builds (continue-on-error, only with llvm-mingw) and saves build/icu-win/out after dxcompiler and before the opt-in DLL check; the cache key hashes the inputs only',
      has_ci, -1 < i_dxc < i_restore < i_build < i_save < i_verify and wf.count(key) == 2 and 'run: bash tools/build-icu-optin.sh' in wf[i_build:i_save]
      and 'continue-on-error: true' in wf[i_build:i_save] and "if: env.MINGW_OK == '1'" in wf[i_build:i_save] and 'path: build/icu-win/out' in wf[i_restore:i_build]
      and "hashFiles('build/icu-win/**" not in wf and 'build/icu-win/work' not in wf)
wired('CI runs this check with the other host checks', has_ci, 'run: python3 tests/host/check-icu-optin.py' in wf and -1 < i_chk)
# the ship check, auto-tune, the list, the farm baseline
shipped = load(root / 'tools/check-optin-dlls-shipped.py', 'check_optin_dlls_shipped')
with tempfile.TemporaryDirectory() as td:
    fake_recs = Path(td) / 'r.json'
    fake_recs.write_text(json.dumps({'rules': [
        {'id': 'bloodborne-launcher', 'config': ['# c', {'line': 'env.MADEIRA_OPTIN_DLLS = icuuc,icuin', 'minBuild': 708}]},
        {'id': 'lone-icuin', 'config': ['env.MADEIRA_OPTIN_DLLS = icuin']}]}))
    fake_rules = Path(td) / 'rules.py'
    fake_rules.write_text('RULES = [{"id": "qt6core-icuuc-missing", "lines": ["env.MADEIRA_OPTIN_DLLS = icuuc,icuin"]}]\n')
    names = shipped.wanted(fake_recs, fake_rules)
    check('the ship check reads icuuc and icuin from a list line and a rule, and a list that names icuin alone wants icuuc too (icuin.dll imports icuuc.dll)',
          set(names) == {'icuuc', 'icuin'} and any('lone-icuin' in w for w in names['icuuc']) and shipped.shipped_as('icuuc') == 'icuuc-optin.dll'
          and shipped.shipped_as('icuin') == 'icuin-optin.dll')
    folder = Path(td) / 'ship'
    folder.mkdir()
    (folder / 'icuuc-optin.dll').write_bytes(b'x')
    gone_files = shipped.missing(folder, names)
    check('with only icuuc-optin.dll in the folder, the ship check names icuin-optin.dll and who wants it', set(gone_files) == {'icuin'} and 'rule qt6core-icuuc-missing' in ', '.join(gone_files['icuin']))
    (folder / 'icuin-optin.dll').write_bytes(b'x')
    check('with both files it is satisfied', shipped.missing(folder, names) == {})
rules_mod = load(root / 'build/tools/autotune_rules.py', 'autotune_rules')
by_id = {r['id']: r for r in rules_mod.RULES}
rule, old_rule = by_id.get('qt6core-icuuc-missing'), by_id.get('qt6core-icuuc-missing-before-708')
signal = [{'has': ['err:module:import_dll Library icuuc.dll (which is needed by', 'Qt6Core.dll']}]
wired('auto-tune has qt6core-icuuc-missing: the two DLLs, no retry, only for the loader\'s icuuc.dll miss of Qt6Core.dll, not again once linked',
      rule is not None, rule and rule['lines'] == ['env.MADEIRA_OPTIN_DLLS = icuuc,icuin'] and rule.get('noRetry') is True and 'fixedIn' not in rule
      and rule['all'] == signal and rule['none'] == [{'has': ['MADEIRA_OPTIN_DLLS: icuuc.dll -> icuuc-optin.dll']}] and rule.get('outcomes') == ['crashed', 'exited-early'])
wired('auto-tune has qt6core-icuuc-missing-before-708: a fixedIn rule (no lines: the engine never tries the lines of a fixedIn rule) for builds up to 707, ahead of the one with the line',
      old_rule is not None, old_rule and old_rule['lines'] == [] and old_rule.get('fixedIn') == 708 and old_rule.get('maxBuild') == 707 and old_rule['all'] == signal
      and old_rule['priority'] < rule['priority'])
gen_swift = (root / 'app/Madeira/AutoTuneRules.generated.swift').read_text()
wired('the generated rule table has both (run build/tools/gen-autotune-rules.py)', rule is not None,
      '"qt6core-icuuc-missing"' in gen_swift and '"qt6core-icuuc-missing-before-708"' in gen_swift and '"env.MADEIRA_OPTIN_DLLS = icuuc,icuin"' in gen_swift)
fxdir = root / 'tests/fixtures/autotune'
f_old, f_new = fxdir / 'bloodborne-qt6core-icuuc-missing-2026-11-21', fxdir / 'bloodborne-qt6core-icuuc-missing-build708-2026-11-21'
have_fx = f_old.with_suffix('.expect').exists() and f_new.with_suffix('.expect').exists()
if have_fx:
    e_old, e_new = (json.loads(f.with_suffix('.expect').read_text()) for f in (f_old, f_new))
    l_old, l_new = f_old.with_suffix('.log').read_text(), f_new.with_suffix('.log').read_text()
wired('the fixture from 7S40B4 (Bloodborne, build 686, generic paths C:\\Games\\...) ends "known: fixed in 708"; the same lines with build 708\'s number get the line first',
      have_fx or rule is not None,
      have_fx and e_old['status'] == 'known' and e_old['matched'][:1] == ['qt6core-icuuc-missing-before-708'] and e_old['next'] == []
      and e_new['status'] == 'trying' and e_new['matched'] == ['qt6core-icuuc-missing'] and e_new['next'] == ['env.MADEIRA_OPTIN_DLLS = icuuc,icuin']
      and '[build] v0.1.686 (686)' in l_old and '[build] v0.1.708 (708)' in l_new and 'err:module:import_dll Library icuuc.dll (which is needed by' in l_old
      and 'C:\\Games\\' in l_old and 'PC drm free' not in l_old + l_new and 'External' not in l_old + l_new
      and l_new == l_old.replace('[build] v0.1.686 (686)', '[build] v0.1.708 (708)'))
recs = json.loads((root / 'recommended/recommendations.json').read_text())
row = next((r for r in recs['rules'] if 'icuuc' in json.dumps(r.get('config', []))), None)
# (the lead, round 707: the line carries minBuild 708, not the row, so that the round's new rows are pinned alike; an older build's policy
# never checks a line for a later build)
wired("the Bloodborne launcher's row has env.MADEIRA_OPTIN_DLLS = icuuc,icuin from build 708, matches its launcher by name, and no other list names icuuc",
      row is not None, row and row['id'] == 'bloodborne-launcher' and 'minBuild' not in row
      and {'line': 'env.MADEIRA_OPTIN_DLLS = icuuc,icuin', 'minBuild': 708} in row['config']
      and row['match'] == {'programs': [{'name': 'bloodborne.exe'}, {'name': 'play bloodborne.exe'}]}
      and [r['id'] for r in recs['rules'] if 'icuuc' in json.dumps(r.get('config', []) + r.get('smallRAMConfig', []))] == ['bloodborne-launcher'])
baseline = json.loads((root / 'tools/farm-baseline.json').read_text())
gaps = baseline['farms']['arm64ec-windows'].get('staticGaps', {})
wired("the farm baseline accepts icuin-optin.dll's import of icuuc.dll (the app links icuuc-optin.dll under that name), as evr-optin.dll's of dxva2.dll",
      'icuin-optin.dll' in gaps, gaps.get('icuin-optin.dll') == ['icuuc.dll'])

# ---------------------------------------------------------------- DLLs at hand
farm = Path(os.environ.get('BCD_ARM64EC_FARM') or (root / 'app/Madeira/arm64ec-windows'))
farm_ok = all((farm / n).exists() for n in ('kernel32.dll', 'kernelbase.dll', 'advapi32.dll', 'ucrtbase.dll'))
llvm_readobj = mingw_dir / 'llvm-readobj'


def farm_imports(dll):
    """Every function a DLL imports by name from kernel32, advapi32 or the UCRT sets, that BCD's ARM64EC farm does not export (empty: all resolve)."""
    def lines(path, what):
        return sh([str(llvm_readobj), what, str(path)]).stdout.splitlines()
    exp = {}
    for name in ('kernel32', 'kernelbase', 'advapi32', 'ucrtbase'):
        exp[name] = set(re.findall(r'^\s+(?:Name|Forwarder): (\S+)', '\n'.join(lines(farm / (name + '.dll'), '--coff-exports')), re.M))
    apiset = (farm / 'apisetschema.dll').read_bytes() if (farm / 'apisetschema.dll').exists() else b''
    cur, missing = None, {}
    for line in lines(dll, '--coff-imports'):
        m = re.match(r'\s+Name: (\S+\.dll)$', line, re.I)
        if m:
            cur = m.group(1).lower()
            # an api-ms-win-crt-*-l1-1-0.dll contract must be one of the farm's apisetschema.dll (names are stored UTF-16 without "api-" and the version)
            if cur.startswith('api-ms-win-') and apiset and cur[4:-4].rsplit('-', 2)[0].encode('utf-16le') not in apiset:
                missing.setdefault(cur, []).append('(contract not in apisetschema.dll)')
            continue
        m = re.match(r'\s+Symbol: (\S+) \(', line)
        if m and cur and cur != 'icuuc.dll':
            pool = exp['kernel32'] | exp['kernelbase'] if cur == 'kernel32.dll' else exp['advapi32'] | exp['kernelbase'] if cur == 'advapi32.dll' else exp['ucrtbase']
            if m.group(1) not in pool:
                missing.setdefault(cur, []).append(m.group(1))
    return missing


def check_dlls(label, files, machine, icu_source):
    for f in files:
        bad = chk.problems(f, machine, icu_source)
        check('%s %s: %s, the unversioned C API%s, ICU data %s, imports only what Wine ships (%s)'
              % (label, f.name, machine, ' with every public header function' if icu_source else '', 'inside' if 'icuuc' in f.name else 'not inside', bad or 'clean'), not bad)
        if farm_ok and llvm_readobj.exists() and machine == 'arm64ec':
            miss = farm_imports(f)
            check('%s %s: every imported function resolves in BCD\'s ARM64EC kernel32, kernelbase, advapi32 and ucrtbase, every api-ms-win-crt contract is in its apisetschema.dll (%s)' % (label, f.name, miss or 'all do'), not miss)


work_src = Path(os.environ.get('BCD_ICU_WORK') or (D / 'work')) / 'src/icu/source'
icu_source = str(work_src) if (work_src / 'common/unicode/ucol.h').exists() or (work_src / 'i18n/unicode/ucol.h').exists() else None
if icu_source is None:
    print('skip the header completeness check (no extracted tarball at %s)' % work_src)
if os.environ.get('BCD_ICU_BUILD') == '1' and (mingw_dir / 'arm64ec-w64-mingw32-clang').exists():
    bt = Path(mk('check-icu-build-'))
    work = Path(os.environ.get('BCD_ICU_WORK') or (bt / 'work'))
    env = dict(os.environ, MINGW=str(mingw_dir), WORK=str(work), OUT=str(bt / 'out'), SHIP_DIR=str(bt / 'ship'))
    r = sh(['bash', str(root / 'tools/build-icu-optin.sh')], env=env)
    check('tools/build-icu-optin.sh builds the ARM64EC DLLs and ships them into a temporary folder (%s)' % (r.stdout.strip().splitlines() or ['no output'])[-1][:200]
          if r.returncode == 0 else 'tools/build-icu-optin.sh failed: ' + (r.stderr or r.stdout)[-600:], r.returncode == 0)
    if r.returncode == 0:
        icu_source = str(work / 'src/icu/source')
        check_dlls('built', [bt / 'ship/icuuc-optin.dll', bt / 'ship/icuin-optin.dll'], 'arm64ec', icu_source)
        check('the shipped files are the checked ones', all((bt / 'ship' / (n + '-optin.dll')).read_bytes() == (bt / 'out' / (n + '.dll')).read_bytes() for n in ('icuuc', 'icuin')))
        r2 = sh(['bash', str(root / 'tools/build-icu-optin.sh')], env=env)
        check('a second run finds the current DLLs by their stamp and ships them again without building', r2.returncode == 0 and 'holds current DLLs' in r2.stdout)
elif os.environ.get('BCD_ICU_BUILD') == '1':
    print('skip BCD_ICU_BUILD (no llvm-mingw at %s)' % mingw_dir)
out_dir = Path(os.environ.get('BCD_ICU_OUT') or (D / 'out'))
for label, folder, names in (('build/icu-win/out', out_dir, ('icuuc.dll', 'icuin.dll')),
                             ('app/Madeira/arm64ec-windows', root / 'app/Madeira/arm64ec-windows', ('icuuc-optin.dll', 'icuin-optin.dll'))):
    files = [folder / n for n in names]
    if all(f.exists() for f in files):
        check_dlls(label, files, 'arm64ec', icu_source)
    else:
        print('skip %s (not built here)' % label)
shipped_dir = root / 'app/Madeira/arm64ec-windows'
if all((out_dir / n).exists() for n in ('icuuc.dll', 'icuin.dll')) and all((shipped_dir / (n + '-optin.dll')).exists() for n in ('icuuc', 'icuin')):
    check('the shipped icuuc-optin.dll and icuin-optin.dll are the built ones',
          all((out_dir / (n + '.dll')).read_bytes() == (shipped_dir / (n + '-optin.dll')).read_bytes() for n in ('icuuc', 'icuin')))

# ---------------------------------------------------------------- the host run (optional): the x86-64 flavour under a Wine
wine = os.environ.get('BCD_HOST_WINE')
x64 = os.environ.get('BCD_ICU_X64_DIR')
if wine and not x64 and (mingw_dir / 'x86_64-w64-mingw32-clang').exists():
    xt = Path(mk('check-icu-x64-'))
    work = Path(os.environ.get('BCD_ICU_WORK') or (xt / 'work'))
    r = sh(['bash', str(root / 'tools/build-icu-optin.sh')], env=dict(os.environ, ICU_TARGET='x86_64-w64-mingw32', MINGW=str(mingw_dir), WORK=str(work), OUT=str(xt / 'out')))
    check('tools/build-icu-optin.sh ICU_TARGET=x86_64-w64-mingw32 builds the test flavour' if r.returncode == 0 else 'the x86-64 test flavour failed: ' + (r.stderr or r.stdout)[-600:], r.returncode == 0)
    x64 = str(xt / 'out') if r.returncode == 0 else None
if wine and x64 and all((Path(x64) / n).exists() for n in ('icuuc.dll', 'icuin.dll', 'icu-probe.exe')):
    check_dlls('x86-64 test flavour', [Path(x64) / 'icuuc.dll', Path(x64) / 'icuin.dll'], 'x86_64', icu_source)
    run = Path(mk('check-icu-wine-'))
    for n in ('icuuc.dll', 'icuin.dll', 'icu-probe.exe'):
        shutil.copy(Path(x64) / n, run / n)
    env = dict(os.environ, WINEDEBUG='-all', WINEPREFIX=str(run / 'prefix'), WINEDLLOVERRIDES='winemac.drv=;mscoree,mshtml=')
    cmd = [wine]
    cx = re.match(r'(.*/SharedSupport/CrossOver)/', wine)
    if cx:   # CrossOver's bin/wine is a wrapper that wants a bottle; its loader runs a prefix of ours directly
        base = cx.group(1)
        cmd = [base + '/lib/wine/x86_64-unix/wine']
        env.update(CX_ROOT=base, WINEDLLPATH=base + '/lib/wine', WINELOADER=cmd[0], WINESERVER=base + '/bin/wineserver',
                   DYLD_FALLBACK_LIBRARY_PATH='%s/lib/x86_64:%s/lib:/usr/lib' % (base, base))
    try:
        r = subprocess.run(cmd + ['icu-probe.exe'], cwd=run, env=env, capture_output=True, text=True, errors='replace', timeout=300)
        got = r.stdout.replace('\r', '')
        lines = [l for l in got.splitlines() if not l.startswith('info:')]
        check('host Wine: icu-probe.exe (x86-64) loads icuuc.dll and icuin.dll, runs, and answers all %d expected lines exactly (rc %d)' % (expected.count('\n'), r.returncode),
              r.returncode == 0 and '\n'.join(lines) + '\n' == expected)
        if '\n'.join(lines) + '\n' != expected:
            import difflib
            print(''.join(difflib.unified_diff(expected.splitlines(True), [l + '\n' for l in lines], 'expected', 'got'))[:1500], r.stderr[:500])
        info = [l for l in got.splitlines() if l.startswith('info:')]
        check('the lines that differ by design are all there (%d)' % len(info), len(info) == 5)
    finally:
        if 'WINESERVER' in env:
            subprocess.run([env['WINESERVER'], '-k'], env=env, capture_output=True)
        shutil.rmtree(run, ignore_errors=True)
else:
    print('skip the host run (BCD_HOST_WINE names no wine, or no x86-64 test flavour: build it with ICU_TARGET=x86_64-w64-mingw32, or set BCD_ICU_X64_DIR)')

for t in TEMPS:
    shutil.rmtree(t, ignore_errors=True)
print('%d wiring check(s) wait for the snippets' % todo if todo else 'all wiring is in')
print('PASS: ICU opt-in' if ok else 'FAILED: ICU opt-in')
sys.exit(0 if ok else 1)
