#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Cut a session log down to an auto-tune fixture (docs/AUTO-TUNE.md).

    tools/autotune-fixture.py LOG NAME [--end JSON] [--note TEXT]

Writes tests/fixtures/autotune/NAME.log: only the lines auto-tune reads, which are
the scanner's markers (app/Madeira/AutoTuneCore.swift) and every rule signal
(build/tools/autotune_rules.py). Lines that repeat are thinned: the first and last
few are kept, plus enough of every counted signal to pass its threshold. Personal
data is scrubbed: container paths, Windows user names, e-mail and IP addresses,
UUIDs, an install folder named like a web site (C:\\Games\\... instead) and, on the
owner's Mac, every entry of the private needles file.

The fixture is then replayed next to the full log. Both must give the same outcome,
stage, rules and next line, or the cut lost something. NAME.expect is written with
that result, the --end facts and the --note. Read it before committing: it is what
tests/host/check-autotune.py holds the rules to from then on.

Adding a rule (the whole workflow):
  1. add it to build/tools/autotune_rules.py and run build/tools/gen-autotune-rules.py;
  2. cut the log that showed the failure: tools/autotune-fixture.py <log> <game>-<date>-<time>;
  3. check that NAME.expect proposes the line that fixed the game;
  4. run tests/host/check-autotune.py; commit the four files together.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[1]
CORE = R / "app/Madeira/AutoTuneCore.swift"
RULES_SWIFT = R / "app/Madeira/AutoTuneRules.generated.swift"
REPLAY = R / "tests/host/autotune_replay.swift"
OUT = R / "tests/fixtures/autotune"


def needles():
    """(needle, keep) pairs: keep = how many matching lines to keep at each end."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("autotune_rules", R / "build/tools/autotune_rules.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = {}
    # The scanner's markers: Array("...".utf8) in AutoTuneCore.swift (escaped quotes unescaped).
    for m in re.finditer(r'Array\("((?:[^"\\]|\\.)*)"\.utf8\)', CORE.read_text()):
        out.setdefault(m.group(1).replace('\\"', '"'), 6)
    for rule in mod.RULES:
        for sig in rule.get("all", []) + rule.get("any", []) + rule.get("none", []):
            # A counted signal keeps enough lines to pass its threshold.
            keep = max(6, (sig.get("count") or 1) + 5)
            first = sig["has"][0]
            out[first] = max(out.get(first, 0), keep)
    return out


SCRUB = [
    (re.compile(r"/(?:private/)?var/mobile/Containers/(?:Data|Shared/AppGroup)/(?:Application/)?[0-9A-Fa-f-]{36}"), "<container>"),
    (re.compile(r"(?i)(\\\\?users\\\\?)(?!public\b|mobile\b|<user>)[^\\/\"' ]+"), r"\1<user>"),
    (re.compile(r"/Users/[^/\"' ]+"), "/Users/<user>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "<id>"),
    # 2026-10-17, before the Send Log alpha: identifiers a long or networked session can show.
    (re.compile(r"(?i)(userdata[\\/]+)\d+"), r"\1<steamid>"),
    (re.compile(r"\b7656119\d{10}\b"), "<steamid>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"), "<ip6>"),
    (re.compile(r"(?i)\b(?:fe80|f[cd][0-9a-f]{2}|[23][0-9a-f]{3})(?::[0-9a-f]{1,4})*::(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})*)?"), "<ip6>"),
    (re.compile(r"""[^\s'’"\\/]{1,40}['’]s (?=(?:[A-Za-z0-9-]+ )?(?:iPhone|iPad|Mac|MacBook|AirPods|Keyboard|Mouse|Trackpad|Controller|DualSense|DualShock|Xbox|Joy-Con)\b)"""), "<name>'s "),
    # 2026-10-22, Epic Games sign-in (BCDEpic): Epic's two token families and the sign-in page's code.
    (re.compile(r"\beg1~[A-Za-z0-9._~+/=-]+"), "eg1~<token>"),
    (re.compile(r"\begoc1~[A-Za-z0-9._~+/=-]+"), "egoc1~<token>"),
    (re.compile(r'("authorizationCode"\s*:\s*")[0-9A-Za-z]{16,64}'), r"\1<code>"),
    # 2026-10-22, Epic Games milestone 2 (BCDEpicStart): a signed-in start's one-time code, account ID and name on a command line (the
    # runtime masks them "*" before they are logged; a value is replaced here, a mask or placeholder is left), and a code in an address.
    (re.compile(r"""(?i)(-AUTH_PASSWORD=)(?![*<])[^\s"'\\]+"""), r"\1<code>"),
    (re.compile(r"""(?i)(-epicuserid=)(?![*<])[0-9A-Za-z]{8,64}"""), r"\1<id>"),
    (re.compile(r"""(?i)(-epicusername=)(?![*<])(?:\\?"[^"]*?\\?"|[^\s"'\\]+)"""), r"\1<name>"),
    (re.compile(r"""(?i)((?:exchangeCode|userCode)=|[?&]code=)(?![*<])[^&\s"'\\]+"""), r"\1<code>"),
]


# An install folder named like a web site ("The-Game-Site.com"): a player's download folder says nothing a rule needs and must
# not reach a public fixture (round 624). Windows paths and container paths only; a URL's host ("//host.io/") and the
# ".net" folders Windows and Battle.net use are left alone. tests/host/check-autotune.py holds every fixture to the same pattern.
SITE_FOLDER = re.compile(r"(?i)(?:(?<=\\)|(?<=[^:/]/))([^\\/\s\"'<>|:*?]+\.(?:com|org|to|ru|me|io|cc|xyz|info|site|games|vip|club))([\\/]+)")


def _site_folder(m):
    before = m.string[:m.start()]
    # "C:\Games\<site folder>\Game" becomes "C:\Games\Game", anything else "<...>\Games\Game".
    return "" if re.search(r"(?i)(?:^|[\\/])Games[\\/]+$", before) else "Games" + m.group(2)


def _private_needles():
    """The owner's private needles file (personal data, download-site names), never in the repository. Missing: nothing."""
    p = Path.home() / "madeira-local" / "state" / "alpha-needles.txt"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [re.compile(re.escape(n), re.I) for n in (l.strip() for l in lines) if len(n) >= 6 and not n.startswith("#")]


PRIVATE_NEEDLES = _private_needles()


def scrub(line):
    for pat, rep in SCRUB:
        line = pat.sub(rep, line)
    line = SITE_FOLDER.sub(_site_folder, line)
    for pat in PRIVATE_NEEDLES:
        line = pat.sub("<redacted>", line)
    return line


# The scanner keeps a [win-name] line when its text reads like an error (AutoTuneCore.swift errorWords).
ERROR_WORDS = ("error", "Error", "ERROR", "could not", "Could not", "Could Not", "failed", "Failed",
               "not supported", "unable", "Unable", "cannot", "Cannot", "Fatal", "fatal")


def cut(log, wanted):
    hits = {n: [] for n in wanted}
    keep = set()
    with open(log, "rb") as f:
        for i, raw in enumerate(f):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            for n in wanted:
                if n in line:
                    hits[n].append(i)
            if "[win-name] #" in line and ' text="' in line and any(w in line for w in ERROR_WORDS) and len(keep) < 4000:
                keep.add(i)
    for n, idx in hits.items():
        k = wanted[n]
        keep.update(idx[:k])
        keep.update(idx[-k:])
    lines = []
    with open(log, "rb") as f:
        for i, raw in enumerate(f):
            if i in keep:
                lines.append(scrub(raw.decode("utf-8", "replace").rstrip("\r\n")[:2000]))
    return lines


def replay(paths, end):
    swiftc = shutil.which("swiftc")
    if not swiftc:
        sys.exit("autotune-fixture: swiftc is needed to check the cut")
    with tempfile.TemporaryDirectory(prefix="autotune-replay-") as tmp:
        exe = os.path.join(tmp, "replay")
        b = subprocess.run([swiftc, "-O", "-parse-as-library", "-swift-version", "5", "-o", exe,
                            str(CORE), str(RULES_SWIFT), str(REPLAY)], capture_output=True, text=True)
        if b.returncode:
            sys.exit(b.stderr[-3000:])
        r = subprocess.run([exe, "--end", json.dumps(end)] + [str(p) for p in paths], capture_output=True, text=True)
        if r.returncode:
            sys.exit(r.stderr[-3000:])
        return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]


KEYS = ("outcome", "stage", "matched", "dialogs", "status", "next")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("name")
    ap.add_argument("--end", default="{}", help='AutoTuneSessionEnd fields, e.g. {"killed": true}')
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    end = json.loads(a.end)
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = OUT / (a.name + ".log")
    lines = cut(a.log, needles())
    fixture.write_text("\n".join(lines) + "\n")
    full, cutr = replay([a.log, fixture], end)
    diff = [k for k in KEYS + ("monoMissing",) if full[k] != cutr[k]]
    if diff:
        fixture.unlink()
        sys.exit("the cut lost something (%s):\n full: %s\n cut:  %s" % (", ".join(diff),
                 {k: full[k] for k in diff}, {k: cutr[k] for k in diff}))
    expect = {"end": end, "note": a.note}
    expect.update({k: cutr[k] for k in KEYS})
    if cutr["monoMissing"]:
        expect["monoMissing"] = True   # the game page's Wine Mono hint (only a fixture that shows it says so)
    (OUT / (a.name + ".expect")).write_text(json.dumps(expect, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("%s: %d lines; %s, stage %d, matched %s, next %s" % (fixture.name, len(lines), cutr["outcome"],
          cutr["stage"], cutr["matched"], cutr["next"]))


if __name__ == "__main__":
    main()
