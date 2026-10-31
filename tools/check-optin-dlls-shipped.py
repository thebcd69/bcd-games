#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Every DLL a game list or an auto-tune rule names in env.MADEIRA_OPTIN_DLLS ships as <name>-optin.dll (round 624, agent E2 S3).

tools/build-wine-extra-dlls.sh builds the opt-in DLLs in a CI step that may fail without failing the build (continue-on-error);
a DLL that did not build leaves a list line that does nothing on the phone, which only the next log would show (the bridge then
logs "MADEIRA_OPTIN_DLLS lists <name>, which this build does not ship"). Glyphica's kernelbase-optin.dll (build 624) is the newest.

  check-optin-dlls-shipped.py DIR [RECOMMENDATIONS.json] [AUTOTUNE_RULES.py]
      DIR: the arm64ec-windows folder the IPA packs. Exit 1 naming each missing file and who wants it.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE = re.compile(r'env\.MADEIRA_OPTIN_DLLS\s*=\s*(.+)$')


def wanted(recs_path, rules_path):
    names = {}

    def add(line, who):
        m = LINE.match(line.strip())
        if m:
            for n in re.split(r'[,; ]+', m.group(1).strip().lower()):
                if n.endswith('.dll'):
                    n = n[:-4]
                if n:
                    names.setdefault(n, set()).add(who)

    recs = json.loads(Path(recs_path).read_text())
    for r in recs['rules']:
        for c in (r.get('config') or []) + (r.get('smallRAMConfig') or []):
            add(c['line'] if isinstance(c, dict) else c, 'list ' + r['id'])
    spec = importlib.util.spec_from_file_location('autotune_rules', rules_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for r in mod.RULES:
        for line in r.get('lines', []):
            add(line, 'auto-tune rule ' + r['id'])
    return names


def missing(folder, names):
    have = {p.name.lower() for p in Path(folder).iterdir()} if Path(folder).is_dir() else set()
    return {n: who for n, who in names.items() if n + '-optin.dll' not in have}


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    recs = sys.argv[2] if len(sys.argv) > 2 else ROOT / 'recommended/recommendations.json'
    rules = sys.argv[3] if len(sys.argv) > 3 else ROOT / 'build/tools/autotune_rules.py'
    names = wanted(recs, rules)
    gone = missing(sys.argv[1], names)
    for n, who in sorted(gone.items()):
        print('::error::%s-optin.dll is not in %s, but %s name it in env.MADEIRA_OPTIN_DLLS' % (n, sys.argv[1], ', '.join(sorted(who))))
    if gone:
        sys.exit(1)
    print('opt-in DLLs: all %d named by the lists and auto-tune rules ship (%s)' % (len(names), ', '.join(sorted(names))))
