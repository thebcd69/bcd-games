#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Japanese locale for one game (env.MADEIRA_JAPANESE = 1; upstream Madeira #567 by nekodesusan-prog, in BCD's form:
app/Madeira/BCDJapanese.c). Off by default; a game without the line must find the prefix and its environment exactly as before.

1. Wiring (source): the bridge runs the prefix step only while no wineserver runs (before the registry is read), exports the locale
   after the game's own env lines and restores it at the session end; the game page's Compatibility switch writes the game's own
   line; the catalog, the list policy, the Xcode project (BCDJapanese.c, the fonts-ja folder), .gitignore, the fetch step and this
   check in CI; the font's notice and licence (fonts-ja/, LICENSES/ and the app's legal/LICENSES/ alike) and THIRD-PARTY-NOTICES.
2. BCDJapanese.c is compiled on the host with a harness and run against a scratch prefix made from the app's own prefix template:
   - which values turn it on (the environment, bcd.cfg, the game's file; only "1");
   - off without the marker: nothing is created, user.reg is byte for byte the same;
   - on: the marker, the font link into the bundle, the 17 font names under HKCU\\Software\\Wine\\Fonts\\Replacements, a one-time
     backup; a second start changes nothing; an app update (another bundle path) relinks;
   - off after it: the names, the emptied section, the link, the backup and the marker go, and user.reg is the template's again byte
     for byte; a take-back whose write fails keeps the marker and the backup; an empty user.reg has nothing to take back;
   - a player's own replacement values and section stay; a player's own value for one of the names is neither replaced nor removed;
     a font file of the game's own of that name is left alone;
   - a build without BCD's font has no Japanese locale: the switch acts as off (what an earlier session left is taken back), and the
     session's environment is not touched; the game page hides the switch in such a build;
   - the session's LC_ALL, LANG and MADEIRA_NLS_EXTRA, and LC_ALL and LANG put back (or unset) with MADEIRA_JAPANESE at the end;
     a Japanese LC_ALL of the game's own is kept, any other (a list row's en_US.UTF-8) is replaced for the session, as the notes say.
3. tools/fetch-japanese-font.sh with file:// sources: a wrong file is refused (exit 0, a warning, nothing written); the right one
   (when this machine has it: app/Madeira/fonts-ja/, $BCD_JAPANESE_FONT_TEST) is copied and cached. When the font is here: its
   SHA-256 and size are the pinned ones and its family name is the one the replacements name.
SKIP of the compiled part (exit 0) without a C compiler.
"""
from pathlib import Path
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
failures = []


def check(what, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:2000]) if detail and not cond else ""))
    if not cond:
        failures.append(what)


def read(p):
    return Path(p).read_text(encoding="utf-8", errors="replace")


SHA = "dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073"
SIZE = 4533028
FAMILY = "Noto Sans JP"

# ---- 1. wiring
bridge = read(APP / "WineProcessBridge.m")
seed = bridge[bridge.index("void madeira_seed_prefix_if_needed(const char *prefix_path) {"):]
seed = seed[:seed.index("\n}\n")]
check("the bridge includes BCDJapanese.h", '#include "BCDJapanese.h"' in bridge)
check("the prefix step runs in madeira_seed_prefix_if_needed, only while no wineserver runs, with the switch's value",
      "if (!wineserver_is_running()) {" in seed and
      "bcd_japanese_prefix( prefix_path, [[NSBundle mainBundle] resourcePath].fileSystemRepresentation, bcd_japanese_on(), ws_log );" in seed
      and seed.index("madeira_seed_fonts_and_vc80_amd64( fm, prefix );") < seed.index("bcd_japanese_prefix("))
game_loop = bridge.index('fprintf(stderr, "[madeira-env] game %s=%s\\n", k.UTF8String, v.UTF8String);')
env_call = bridge.find("bcd_japanese_session_env(bcd_japanese_on(), [[NSBundle mainBundle] resourcePath].fileSystemRepresentation, STDERR_FILENO);")
check("the session's locale is exported once, right after the game's own env lines", bridge.count("bcd_japanese_session_env(") == 1
      and game_loop < env_call < game_loop + 900)
end = bridge.index('    unsetenv("MADEIRA_NLS_EXTRA");    /* madeira-bcd: likewise')
check("the session end puts the locale back", bridge.count("bcd_japanese_session_end();") == 1
      and 0 < bridge.index("bcd_japanese_session_end();") - end < 300)
pbx = read(R / "app/Madeira.xcodeproj/project.pbxproj")
check("the Xcode project compiles BCDJapanese.c and carries the fonts-ja folder",
      "A10041A1 /* BCDJapanese.c in Sources */ = {isa = PBXBuildFile; fileRef = A20041A1 /* BCDJapanese.c */; };" in pbx
      and pbx.count("A10041A1 /* BCDJapanese.c in Sources */") == 2
      and 'A20009A1 /* fonts-ja */ = {isa = PBXFileReference; lastKnownFileType = folder; path = "fonts-ja"; sourceTree = "<group>"; };' in pbx
      and pbx.count("A10009A1 /* fonts-ja in Resources */") == 2)
page = read(APP / "BCDGamePageLive.swift")
check("the game page: Compatibility's Japanese locale switch shows and writes the game's own line",
      'Self.toggle("japanese", "Japanese locale", GameProfile(windowsPath: path).get("env.MADEIRA_JAPANESE") == "1",' in page
      and 'case "japanese": setLine(game, "env.MADEIRA_JAPANESE", on ? "1" : "")' in page
      and page.index('Self.toggle("japanese"') > page.index("private func compatibility(") > 0
      and page.index('Self.toggle("japanese"') < page.index("// MARK: Controls and the screen during play"))
gate = page[page.index("private func compatibility("):page.index("// MARK: Controls and the screen during play")]
check("... only in a build that has the font (the switch is hidden without it), and its note says it replaces the game's LC_ALL",
      gate.count('Self.toggle("japanese"') == 1
      and gate.index("if Self.japaneseFontBundled {") < gate.index('Self.toggle("japanese"') < gate.index("settings += [")
      and 'private static let japaneseFontBundled = Bundle.main.path(forResource: "NotoSansJP-Regular", ofType: "otf", inDirectory: "fonts-ja") != nil' in gate
      and "it replaces a locale its own settings or its list row set (env.LC_ALL" in gate)
gen = read(R / "build/tools/gen-config-catalog.py")
ent = gen[gen.index('"env.MADEIRA_JAPANESE"'):] if '"env.MADEIRA_JAPANESE"' in gen else ""
ent = ent[:ent.index('"sources"')] if ent else ""
note = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', ent[ent.index('"note":') + 7:])) if '"note":' in ent else ""
check("the catalog entry: Wine core, a switch, default off; it says it replaces a LC_ALL line and is absent without the font",
      '"category": "Wine core (ntdll)"' in ent and '"kind": "bool"' in ent and '"default": "0"' in ent
      and "the Japanese LC_ALL replaces one its own file or its list row sets" in note
      and "A build without the font (its fetch failed) has no Japanese locale" in note, note[:600])
pol = read(APP / "GameRecommendationsList.swift")
check("a list may set it, only to 0 or 1", '"MADEIRA_JAPANESE",' in pol.split("static let allowedEnvNames")[1].split("]\n")[0]
      and 'throw RecommendationsError.policy("\\(path): MADEIRA_JAPANESE is 0 or 1")' in pol)
gi = read(R / ".gitignore")
check(".gitignore keeps the font out of the repository", "app/Madeira/fonts-ja/*.otf" in gi.splitlines())
wf = read(R / ".github/workflows/build-ipa.yml")
check("CI fetches the font and runs this check", "bash tools/fetch-japanese-font.sh" in wf and "python3 tests/host/check-japanese-locale.py" in wf)
fetch = read(R / "tools/fetch-japanese-font.sh")
notice = read(APP / "fonts-ja/NOTICE-NotoSansJP.txt") if (APP / "fonts-ja/NOTICE-NotoSansJP.txt").is_file() else ""
check("the fetch script and the notice pin the same file", f"SHA256={SHA}" in fetch and f"SIZE={SIZE}" in fetch and SHA in notice
      and "4,533,028 bytes" in notice)
NOTO = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"   # notofonts/noto-cjk; GitHub resolves the short f8d1575 to it
check("the first source names the noto-cjk commit in full (the short form needs GitHub to resolve it), and the notice does too",
      f'"https://raw.githubusercontent.com/notofonts/noto-cjk/{NOTO}/Sans/SubsetOTF/JP/NotoSansJP-Regular.otf"' in fetch
      and f"commit {NOTO})" in notice)
lic = APP / "fonts-ja/LICENSE-NotoSansJP-OFL-1.1.txt"
check("the font's licence (SIL OFL 1.1, with its copyright line) is next to it, in LICENSES and in the app's legal folder",
      lic.is_file() and read(lic).startswith("Copyright 2014-2021 Adobe (http://www.adobe.com/).\n\nThis Font Software is licensed under the SIL Open Font License,")
      and "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007" in read(lic)
      and (R / "LICENSES/OFL-1.1-NotoSansJP.txt").is_file() and (APP / "legal/LICENSES/OFL-1.1-NotoSansJP.txt").is_file()
      and read(R / "LICENSES/OFL-1.1-NotoSansJP.txt") == read(lic) == read(APP / "legal/LICENSES/OFL-1.1-NotoSansJP.txt"))
tpn = read(R / "THIRD-PARTY-NOTICES.md")
check("THIRD-PARTY-NOTICES names the font and the credit, in both copies",
      "**Noto Sans JP**" in tpn and "nekodesusan-prog" in tpn and tpn == read(APP / "legal/THIRD-PARTY-NOTICES.md"))
short = read(APP / "licenses/THIRD-PARTY-NOTICES.txt")
check("the short notice the package ships names the font, its licence and its SHA-256",
      "NotoSansJP-Regular.otf (fonts-ja/)" in short and "SIL Open Font\n  License 1.1" in short and SHA in short.replace("\n  ", ""))

# ---- 2. the C code on the host
cc = os.environ.get("CC") or shutil.which("clang") or shutil.which("cc")
HARNESS = r'''
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "BCDJapanese.h"
static void say(const char *fmt, ...) { va_list a; va_start(a, fmt); vprintf(fmt, a); va_end(a); printf("\n"); }
static void show(const char *n) { const char *v = getenv(n); printf("%s=%s\n", n, v ? v : "(unset)"); }
int main(int argc, char **argv)
{
    if (!strcmp(argv[1], "on")) { printf("on=%d\n", bcd_japanese_on()); return 0; }
    if (!strcmp(argv[1], "prefix")) { int n = bcd_japanese_prefix(argv[2], argc > 4 ? argv[4] : NULL, atoi(argv[3]), say); printf("n=%d\n", n); return 0; }
    if (!strcmp(argv[1], "env")) {
        bcd_japanese_session_env(atoi(argv[2]), argc > 3 ? argv[3] : NULL, 1);
        show("LC_ALL"); show("LANG"); show("MADEIRA_NLS_EXTRA");
        bcd_japanese_session_end();
        printf("--end\n");
        show("LC_ALL"); show("LANG"); show("MADEIRA_JAPANESE");
        return 0;
    }
    return 2;
}
'''
if not cc:
    print("SKIP: no C compiler for the compiled part")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-japanese-") as t:
        t = Path(t)
        (t / "h.c").write_text(HARNESS)
        exe = t / "h"
        b = subprocess.run([cc, "-O1", "-Wall", "-Wno-unused-function", f"-I{APP}", "-o", str(exe), str(t / "h.c"), str(APP / "BCDJapanese.c")],
                           capture_output=True, text=True)
        check("BCDJapanese.c compiles on the host", b.returncode == 0, b.stderr[-2000:])
        if b.returncode == 0:
            base = {k: v for k, v in os.environ.items() if k not in ("MADEIRA_JAPANESE", "MADEIRA_CFG_GAME", "MADEIRA_DOCS_DIR", "LC_ALL",
                                                                    "LANG", "MADEIRA_NLS_EXTRA", "CFFIXED_USER_HOME")}
            docs = t / "docs"
            docs.mkdir()
            base["MADEIRA_DOCS_DIR"] = str(docs)

            def run(*args, **env):
                e = dict(base)
                for k, v in env.items():
                    if v is None:
                        e.pop(k, None)
                    else:
                        e[k] = v
                r = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, env=e, timeout=60)
                return r.stdout

            # which values turn it on
            check("unset everywhere: off", run("on") == "on=0\n")
            check("MADEIRA_JAPANESE=1: on; 0, true and 2: off",
                  run("on", MADEIRA_JAPANESE="1") == "on=1\n" and all(run("on", MADEIRA_JAPANESE=v) == "on=0\n" for v in ("0", "true", "2")))
            (docs / "bcd.cfg").write_text("env.MADEIRA_JAPANESE = 1\n")
            game = t / "game.cfg"
            game.write_text("env.MADEIRA_JAPANESE = 0\n")
            check("bcd.cfg's line turns it on; the game's own file wins; the environment wins over both",
                  run("on") == "on=1\n" and run("on", MADEIRA_CFG_GAME=str(game)) == "on=0\n"
                  and run("on", MADEIRA_CFG_GAME=str(game), MADEIRA_JAPANESE="1") == "on=1\n")
            (docs / "bcd.cfg").unlink()
            game.write_text("# a game\nenv.MADEIRA_JAPANESE = 1\n")
            check("the game's own line alone turns it on", run("on", MADEIRA_CFG_GAME=str(game)) == "on=1\n")

            # a scratch prefix from the app's template
            with tarfile.open(APP / "prefix-template.tar.gz") as tf:
                member = next(m for m in tf.getmembers() if m.name.endswith("user.reg"))
                template = tf.extractfile(member).read()
            pfx = t / "prefix"
            (pfx / "drive_c/windows/Fonts").mkdir(parents=True)
            (pfx / "user.reg").write_bytes(template)
            res = t / "bundle1"
            (res / "fonts-ja").mkdir(parents=True)
            (res / "fonts-ja/NotoSansJP-Regular.otf").write_bytes(b"OTTO-not-a-real-font")
            font = pfx / "drive_c/windows/Fonts/NotoSansJP-Regular.otf"
            reg = pfx / "user.reg"
            marker = pfx / ".bcd-japanese"

            def listing():
                return sorted(str(p.relative_to(pfx)) for p in pfx.rglob("*"))

            before = listing()
            out = run("prefix", pfx, 0, res)
            check("off without the marker: nothing is said, made or changed", out == "n=0\n" and listing() == before
                  and reg.read_bytes() == template, out)
            out = run("prefix", pfx, 1, res)
            text = reg.read_text()
            sec = text[text.find("[Software\\\\Wine\\\\Fonts\\\\Replacements]"):]
            sec = sec[:sec.find("\n\n")] if "\n\n" in sec else sec
            names = [line for line in sec.splitlines()[1:] if line.endswith(f'="{FAMILY}"')]
            check("on: 17 Japanese font names mapped onto Noto Sans JP in the template's own Replacements section, under its #time line",
                  "n=17\n" in out and len(names) == 17 and '"MS Gothic"="Noto Sans JP"' in names
                  and '"\\xff2d\\xff33 \\x30b4\\x30b7\\x30c3\\x30af"="Noto Sans JP"' in names and text.count("Fonts\\\\Replacements]") == 1
                  and sec.splitlines()[1].startswith("#time=") and sec.splitlines()[2] == '"MS Gothic"="Noto Sans JP"'
                  and '"Arial"="Tahoma"' in sec, out)
            check("on: the marker, the link into the bundle's fonts-ja, the one-time backup",
                  marker.is_file() and font.is_symlink() and os.readlink(font) == str(res / "fonts-ja/NotoSansJP-Regular.otf")
                  and (pfx / "user.reg.bcd-japanese-bak").read_bytes() == template)
            snap = reg.read_bytes()
            out = run("prefix", pfx, 1, res)
            check("on again: nothing more is written", "n=0\n" in out and reg.read_bytes() == snap and os.readlink(font).startswith(str(res)), out)
            res2 = t / "bundle2"
            shutil.copytree(res, res2)
            out = run("prefix", pfx, 1, res2)
            check("an app update (another bundle path) relinks the font", os.readlink(font) == str(res2 / "fonts-ja/NotoSansJP-Regular.otf")
                  and reg.read_bytes() == snap, out)
            out = run("prefix", pfx, 0, res2)
            check("off after it: the names, the section, the link, the backup and the marker go; user.reg is the template again, byte for byte",
                  "n=17\n" in out and reg.read_bytes() == template and not font.exists() and not font.is_symlink() and not marker.exists()
                  and not (pfx / "user.reg.bcd-japanese-bak").exists() and listing() == before, out)
            out = run("prefix", pfx, 0, res2)
            check("the next start without the switch reads nothing (no marker)", out == "n=0\n")

            # a player's own replacement values (in the template's section, as regedit would write them)
            assert '"serif"="Tahoma"\n' in template.decode()
            own = template.decode().replace('"serif"="Tahoma"\n', '"serif"="Tahoma"\n"Wingdings"="Winedings"\n"MS Gothic"="My Gothic"\n', 1)
            reg.write_text(own)
            out = run("prefix", pfx, 1, res2)
            text = reg.read_text()
            check("with a player's values: 16 names added, the player's MS Gothic kept",
                  "n=16\n" in out and text.count("Fonts\\\\Replacements]") == 1 and '"MS Gothic"="My Gothic"' in text
                  and '"MS Gothic"="Noto Sans JP"' not in text and '"Wingdings"="Winedings"' in text, out)
            out = run("prefix", pfx, 0, res2)
            check("off: only BCD's 16 go, the player's values stay", "n=16\n" in out and reg.read_text() == own, out)

            # a prefix whose user.reg has no Replacements section: BCD's own section, emptied and taken away again
            bare = template.decode()
            i = bare.index("[Software\\\\Wine\\\\Fonts\\\\Replacements]")
            j = bare.find("\n[", i + 1)
            bare = (bare[:i] + (bare[j + 1:] if j >= 0 else "")).rstrip("\n") + "\n"
            reg.write_text(bare)
            out = run("prefix", pfx, 1, res2)
            check("no Replacements section: one is made with the 17 names", "n=17\n" in out and reg.read_text().count("Fonts\\\\Replacements]") == 1, out)
            out = run("prefix", pfx, 0, res2)
            check("... and taken away again: user.reg as it was, byte for byte", "n=17\n" in out and reg.read_text() == bare, out)

            # a take-back whose write fails keeps the marker and the backup (the next start tries again); an empty user.reg
            reg.write_bytes(template)
            out = run("prefix", pfx, 1, res2)
            blocker = pfx / "user.reg.bcd-tmp"
            blocker.mkdir()   # the rename's temporary file cannot be made
            out = run("prefix", pfx, 0, res2)
            check("a take-back whose user.reg write fails keeps the marker and the backup, and says so",
                  "could NOT be written" in out and marker.is_file() and (pfx / "user.reg.bcd-japanese-bak").is_file()
                  and '"MS Gothic"="Noto Sans JP"' in reg.read_text(), out)
            blocker.rmdir()
            out = run("prefix", pfx, 0, res2)
            check("... and the next start takes everything back", "n=17\n" in out and reg.read_bytes() == template and not marker.exists()
                  and not (pfx / "user.reg.bcd-japanese-bak").exists() and listing() == before, out)
            marker.write_text("left by a Japanese session\n")
            reg.write_bytes(b"")
            out = run("prefix", pfx, 0, res2)
            check("an empty user.reg has nothing to take back: the marker goes, no write error", "n=0\n" in out and not marker.exists()
                  and "could NOT" not in out and reg.read_bytes() == b"", out)

            # a game's own font file of that name, and a build without the font
            reg.write_bytes(template)
            font.write_bytes(b"the game's own copy")
            out = run("prefix", pfx, 1, res2)
            check("a font file of the game's own of that name is left alone (the names still map)", "left as it is" in out and not font.is_symlink()
                  and font.read_bytes() == b"the game's own copy" and "n=17\n" in out, out)
            out = run("prefix", pfx, 0, res2)
            check("... and stays when the switch is off again", font.read_bytes() == b"the game's own copy" and reg.read_bytes() == template, out)
            font.unlink()
            empty = t / "bundle3"
            empty.mkdir()
            out = run("prefix", pfx, 1, empty)
            check("a build without BCD's font: no names, no link, no marker, and it says so",
                  "n=0\n" in out and "is not in this build: the Japanese locale is off for this session" in out
                  and reg.read_bytes() == template and not font.is_symlink() and not marker.exists(), out)
            run("prefix", pfx, 1, res2)
            check("(a Japanese session with the font, for the next case)", marker.is_file() and font.is_symlink())
            out = run("prefix", pfx, 1, empty)
            check("the switch on in a build without the font takes back what a session with the font left",
                  "is not in this build" in out and "n=17\n" in out and reg.read_bytes() == template and not font.is_symlink()
                  and not font.exists() and not marker.exists() and not (pfx / "user.reg.bcd-japanese-bak").exists(), out)

            # the session's locale
            out = run("env", 1, res, MADEIRA_JAPANESE="1")
            check("on: LC_ALL and LANG ja_JP.UTF-8, MADEIRA_NLS_EXTRA=1; at the end LC_ALL, LANG and MADEIRA_JAPANESE unset",
                  out.split("--end\n")[0].endswith("LC_ALL=ja_JP.UTF-8\nLANG=ja_JP.UTF-8\nMADEIRA_NLS_EXTRA=1\n")
                  and out.split("--end\n")[1] == "LC_ALL=(unset)\nLANG=(unset)\nMADEIRA_JAPANESE=(unset)\n", out)
            out = run("env", 1, res, LC_ALL="en_US.UTF-8", LANG="tr_TR.UTF-8")
            check("an English LC_ALL of the game's file is replaced for the session and put back after it, LANG too",
                  "LC_ALL=ja_JP.UTF-8\nLANG=ja_JP.UTF-8" in out and out.split("--end\n")[1].startswith("LC_ALL=en_US.UTF-8\nLANG=tr_TR.UTF-8\n"), out)
            out = run("env", 1, res, LC_ALL="ja_JP.SJIS")
            check("a Japanese LC_ALL of the game's own is kept", "LC_ALL=ja_JP.SJIS\nLANG=ja_JP.SJIS" in out
                  and out.split("--end\n")[1].startswith("LC_ALL=ja_JP.SJIS\nLANG=(unset)\n"), out)
            out = run("env", 0, res, LC_ALL="en_US.UTF-8")
            check("off: the environment is not touched", out.startswith("LC_ALL=en_US.UTF-8\nLANG=(unset)\nMADEIRA_NLS_EXTRA=(unset)\n")
                  and "[japanese]" not in out, out)
            out = run("env", 1, empty, LC_ALL="en_US.UTF-8", MADEIRA_JAPANESE="1")
            check("on in a build without the font: the environment is not touched and the log says why",
                  out.startswith("[japanese] env.MADEIRA_JAPANESE = 1, but fonts-ja/NotoSansJP-Regular.otf is not in this build: the locale is not changed\n"
                                 "LC_ALL=en_US.UTF-8\nLANG=(unset)\nMADEIRA_NLS_EXTRA=(unset)\n"), out)

# ---- 3. the fetch script and the font itself
real = next((p for p in (Path(os.environ["BCD_JAPANESE_FONT_TEST"]) if os.environ.get("BCD_JAPANESE_FONT_TEST") else None,
                         APP / "fonts-ja/NotoSansJP-Regular.otf") if p and p.is_file()), None)
with tempfile.TemporaryDirectory(prefix="bcd-fetch-japanese-font-") as t:
    t = Path(t)
    (t / "wrong.otf").write_bytes(b"not the font")
    env = dict(os.environ, BCD_JAPANESE_FONT_CACHE=str(t / "cache"), BCD_JAPANESE_FONT_URLS=f"file://{t}/wrong.otf file://{t}/missing.otf")
    r = subprocess.run(["bash", str(R / "tools/fetch-japanese-font.sh"), str(t / "dest")], capture_output=True, text=True, env=env)
    check("fetch: a wrong file is refused (exit 0, a warning, nothing written)", r.returncode == 0 and "::warning::" in r.stdout
          and not (t / "dest/NotoSansJP-Regular.otf").exists(), r.stdout + r.stderr)
    if real:
        env["BCD_JAPANESE_FONT_URLS"] = f"file://{t}/wrong.otf file://{real}"
        r = subprocess.run(["bash", str(R / "tools/fetch-japanese-font.sh"), str(t / "dest2")], capture_output=True, text=True, env=env)
        check("fetch: the right file is copied and cached", r.returncode == 0 and (t / "dest2/NotoSansJP-Regular.otf").read_bytes() == real.read_bytes()
              and (t / "cache/NotoSansJP-Regular.otf").is_file(), r.stdout + r.stderr)
        data = real.read_bytes()
        check("the font is the pinned file", hashlib.sha256(data).hexdigest() == SHA and len(data) == SIZE)
        n = struct.unpack(">H", data[4:6])[0]
        tabs = {}
        for i in range(n):
            tag, _cs, off, ln = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            tabs[tag] = (off, ln)
        off, _ = tabs[b"name"]
        _fmt, cnt, so = struct.unpack(">HHH", data[off:off + 6])
        families = set()
        for i in range(cnt):
            p, _e, _l, nid, ln, o = struct.unpack(">HHHHHH", data[off + 6 + 12 * i:off + 18 + 12 * i])
            if p == 3 and nid == 1:
                families.add(data[off + so + o:off + so + o + ln].decode("utf-16-be"))
        check("its family name is the one the replacements name", families == {FAMILY}, families)
    else:
        print("SKIP: the font is not on this machine (tools/fetch-japanese-font.sh, BCD_JAPANESE_FONT_TEST): its own checks did not run")

print("FAILED: Japanese locale" if failures else "PASS: Japanese locale")
sys.exit(1 if failures else 0)
