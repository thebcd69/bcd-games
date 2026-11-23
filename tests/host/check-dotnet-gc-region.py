#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.DOTNET_GCRegionRange for a .NET 7+ game whose garbage collector cannot reserve 256 GB; no Wine runs.

Unrailed! 2: Back on Track (Steam 2211170, build 576, tester log CR8H6H) starts a .NET 7+ runtime (Godot's
Bootstrap.exe) that stops at once: "GC heap initialization failed with error 0x8007000E" (E_OUTOFMEMORY) and
"Failed to create CoreCLR". The regions-based GC reserves a range of virtual memory when it starts, 256 GB by
default; the log's request is 0x3fd2d28000 (261421 MB), and BCD's address map has no such gap ("[mmap-place]
ml1027 NO GAP FITS ... largest gap 32768 MB", "[jumbo] kernel-pick reserve failed (0xc0000017)"). Microsoft
documents DOTNET_GCRegionRange (hexadecimal bytes, .NET 7 and later) for exactly this E_OUTOFMEMORY and
advises two to five times the committed heap, or twice the physical memory the process has when that is
unknown; BCD reports at most 8 GB, so 16 GB = 400000000, which fits the 32 GB gap. Nothing in the app reads
the name; this check holds the wiring together:

  - auto-tune's rule "dotnet-gc-region-range" adds the line after that error, at once (no unchanged retry),
    and not again when the session already ran with it;
  - a fixture from the tester's log proves the rule, and the value is the documented one that fits the gap
    the log reports;
  - the settings catalog documents the name (build/tools/gen-config-catalog.py OVERLAY);
  - a downloaded list may set it, as a hexadecimal size from 1 GB to 64 GB, and only builds from 579 get the
    line (older builds' policy does not know the name: a download naming it would be refused whole);
  - Unrailed 2's list (Steam 2211170) carries it.
"""
from pathlib import Path
import json
import re
import sys

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


VALUE = '400000000'
rules = (root / 'build/tools/autotune_rules.py').read_text()
rule = rules[rules.index('"id": "dotnet-gc-region-range"'):]
rule = rule[:rule.index('\n    },')]
check('auto-tune adds the 16 GB range after the runtime\'s out-of-memory at GC initialisation',
      '"lines": ["env.DOTNET_GCRegionRange = %s"]' % VALUE in rule and
      's("GC heap initialization failed with error 0x8007000E", "ReportEventW")' in rule)
check('...at once, and not again when the session already ran with it',
      's("[madeira-env] game DOTNET_GCRegionRange=")' in rule and '"noRetry": True' in rule)

# The log line the "none" signal reads is the one the process bridge prints for a game's env lines,
# and the line auto-tune's own glue writes for a kept key.
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
glue = (root / 'app/Madeira/AutoTune.swift').read_text()
check('the "none" signal is the line the process bridge prints for a game env line',
      'fprintf(stderr, "[madeira-env] game %s=%s\\n", k.UTF8String, v.UTF8String);' in bridge and
      '"[madeira-env] game \\(key.dropFirst(4))=\\(value)"' in glue)

fix = root / 'tests/fixtures/autotune'
expect = json.loads((fix / 'unrailed-2-2026-10-21.expect').read_text())
log = (fix / 'unrailed-2-2026-10-21.log').read_text()
check('the fixture is the tester\'s session: the runtime\'s error is in it, the runtime was not given the range',
      'GC heap initialization failed with error 0x8007000E' in log and 'DOTNET_GCRegionRange' not in log)
check('...and gets exactly that line, from this rule',
      expect['matched'] == ['dotnet-gc-region-range'] and expect['next'] == ['env.DOTNET_GCRegionRange = %s' % VALUE] and
      expect['outcome'] == 'crashed')
# The gap and the request are in the log the fixture was cut from (CR8H6H L28741-28742, 28789); the rule and the list quote them.
gap_mb = int(re.search(r'largest gap\s+(?:#\s*)?(\d+) MB', rule).group(1))
check('the rule quotes the log\'s 256 GB request and its 32 GB largest gap',
      gap_mb == 32768 and '0x3fd2d28000' in rule and '261421 MB' in rule)
range_mb = int(VALUE, 16) >> 20
check('the range (%d MB) is Microsoft\'s advice for this failure (twice the 8 GB BCD reports) and fits the largest gap (%d MB)'
      % (range_mb, gap_mb), range_mb == 2 * 8192 and range_mb < gap_mb)

gen = (root / 'build/tools/gen-config-catalog.py').read_text()
entry = gen[gen.index('"env.DOTNET_GCRegionRange": {'):]
entry = entry[:entry.index('\n    "env.MADEIRA_FIBER_TRACE"')]
check('the catalog documents the name: text, empty by default (the runtime\'s 256 GB), the failure and the value',
      '"kind": "text", "default": ""' in entry and '0x8007000E' in entry and '400000000' in entry and '32 GB' in entry)

policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a downloaded list may set the name, as a hexadecimal size from 1 GB to 64 GB',
      '"DOTNET_GCRegionRange"' in policy and 'case "DOTNET_GCRegionRange":' in policy and
      'bytes >= 0x4000_0000, bytes <= 0x10_0000_0000' in policy)

recs = json.loads((root / 'recommended/recommendations.json').read_text())
u = [r for r in recs['rules'] if r['id'] == 'unrailed-2-back-on-track']
check('Unrailed 2\'s list (Steam 2211170) carries the line from build 579 only',
      len(u) == 1 and u[0]['match']['steamAppIDs'] == [2211170] and
      {'line': 'env.DOTNET_GCRegionRange = %s' % VALUE, 'minBuild': 579} in u[0]['config'] and
      not any(isinstance(c, str) and 'DOTNET' in c for c in u[0]['config']))
others = [r['id'] for r in recs['rules'] if r['id'] != 'unrailed-2-back-on-track' and
          any('DOTNET_GCRegionRange' in (c if isinstance(c, str) else c['line']) for c in r['config'])]
# Round 620: Barotrauma (V0V96V) and Slay the Spire 2 (2K0RGS, 6YPKSD, HV84ES, ZNRMDX) failed the same 256 GB reserve on build 611/614;
# their lists carry the line from build 579 as Unrailed's does. Any other game keeps its own reservation.
# Round 707 (rule dotnet-gc-region-range-failfast): We Steal From Old People's new row, whose log fails the same 256 GB reserve.
check('only Barotrauma\'s, Slay the Spire 2\'s and (round 707) We Steal From Old People\'s lists set it besides (a game that starts keeps its own 256 GB reservation)',
      sorted(others) == ['barotrauma', 'slay-the-spire-2', 'we-steal-from-old-people'] and all({'line': 'env.DOTNET_GCRegionRange = %s' % VALUE, 'minBuild': 579} in
      next(r for r in recs['rules'] if r['id'] == i)['config'] for i in others))
print('PASS: .NET GC region range' if ok else 'FAILED: .NET GC region range')
sys.exit(0 if ok else 1)
