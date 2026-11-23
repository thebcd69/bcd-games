#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Check Turing Complete's recommended profile using the app's actual Swift policy and matcher."""
import argparse
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument('--require-swift', action='store_true')
args = ap.parse_args()
doc = json.loads((ROOT / 'recommended/recommendations.json').read_text())
rule, = [r for r in doc['rules'] if r['id'] == 'turing-complete']
assert rule['match'] == {'steamAppIDs': [1444480]}
assert rule['minBuild'] > 712
assert rule['config'] == [
    'env.MADEIRA_EC_HOOK_TRACE = 0',
    'env.MESA_GL_VERSION_OVERRIDE = 4.1',
    'env.MESA_GLSL_VERSION_OVERRIDE = 410',
    'env.FEX_SMCCHECKS = full',
    'env.MADEIRA_FEX_EXEC_RECHECK = 1',
    'env.MADEIRA_X64_IMAGE_NOCOPY = 1',
    'gl-backend = zink',
]
swift = shutil.which('swiftc')
if not swift:
    if args.require_swift:
        raise SystemExit('swiftc is required')
    print('SKIP: Swift policy/matcher checks need swiftc')
    raise SystemExit(0)
with tempfile.TemporaryDirectory(prefix='bcd-turing-recs-') as tmp:
    t = Path(tmp)
    support = t / 'Support.swift'
    support.write_text('''import Foundation
// Only the unused app-store entry point is supplied here. Parsing, policy
// validation, build filtering and game matching are production code.
enum RecommendationsStore {
    static func initialList() -> GameRecommendationList {
        fatalError("This harness uses the real parsed file directly, not the app's download store")
    }
}
''')
    main = t / 'main.swift'
    main.write_bytes((ROOT / 'tests/host/turing_recommendations_checks.swift').read_bytes())
    app = ROOT / 'app/Madeira'
    # Use actual generated catalog metadata without compiling the large array of
    # UI labels, help text and source filenames that this policy never reads.
    pairs = re.findall(r'^\s*ConfigOption\(key: "([^"]+)".*?category: "([^"]+)"',
                       (app / 'ConfigCatalog.generated.swift').read_text(), re.M)
    assert len(pairs) > 100 and any(k == 'gl-backend' for k, c in pairs)
    catalog = t / 'catalog.json'
    catalog.write_text(json.dumps([{'key': k, 'category': c} for k, c in pairs]))
    sources = [app / n for n in ('GameRecommendations.swift', 'GameRecommendationsList.swift')]
    if sys.platform == 'linux':
        # Corelibs Foundation does not re-export CoreFoundation or provide Apple's
        # NSNumber -> CFNumber cast. Adapt only those existing parser bridges;
        # the production recommendation policy and matcher remain unchanged.
        portable = t / 'GameRecommendationsList.swift'
        text = sources[1].read_text().replace('import Foundation\n', 'import Foundation\nimport CoreFoundation\n', 1)
        text = text.replace('let number = value as! CFNumber',
                            'let number = unsafeBitCast(value as! NSNumber, to: CFNumber.self)', 1)
        portable.write_text(text)
        sources[1] = portable
    subprocess.run([swift, '-O', str(support), *map(str, sources), str(main), '-o', str(t / 'check')], check=True)
    subprocess.run([str(t / 'check'), str(ROOT / 'recommended/recommendations.json'), str(catalog)], check=True)
print('PASS: Turing Complete recommended profile, policy and build gate')
