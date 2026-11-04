#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's recommended settings as data, online and signed (owner's decision, 2026-10-18).

The lists moved from Swift literals (GameRecommendations.swift) to recommended/recommendations.json,
which every build bundles and BCD's public repository serves with an Ed25519 signature
(GameRecommendationsList.swift reads it, GameRecommendationsSource.swift fetches and checks it,
tools/publish-recommendations.py signs it). This check proves:

  - the built-in list equals the Swift literals it replaced, byte for byte, on a 12 GB phone, an
    8 GB one and a 16 GB iPad: every rule's file text, switches, title and version, with the
    owner's one change of 2026-10-18 (its first line says "# BCD's recommended settings for"
    where it said "# Madeira's"). The literals are tests/host/fixtures/recommendations/
    GameRecommendations-a9851601.swift.txt, compiled beside the new code; a rule whose version
    was raised since may differ, a lower version or a missing rule fails;
  - games are recognised as before: every path of a generated corpus the literals matched gets
    the same rule (a path they did not match may match a newer rule);
  - the built-in list passes the policy a download must pass (it can be published as it is);
  - signatures: valid, a changed byte, another key, a signature of other bytes, a Python-made
    signature (the tool's Ed25519 against CryptoKit), RFC 8032's vectors for the tool;
  - the layout and the policy: unknown fields, wrong types, keys a download may not set,
    WINEDLLOVERRIDES that would load a native DLL, minBuild / maxBuild;
  - the pipeline, with a stand-in network: offline and 404 keep the list in use (a 404 quietly),
    a bad signature, a changed byte, another key, bad JSON, an unknown key, a removed rule or a
    lowered rule version are rejected and logged, an older or equal list is "up to date", a newer
    one is used, kept, and used again at the next start, a kept file that no longer checks out is
    dropped for the built-in list, a newer built-in list wins over an older download, the start's
    check waits 6 hours after an answer, two refreshes share one fetch;
  - the wiring: the Xcode project (the two Swift files, the JSON as a resource), LibraryModel's
    start hook, the CI step, the URLs, the public key, and no "Madeira" in what the model says.

--require-swift turns a missing compiler into a failure (macOS CI).
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'app' / 'Madeira'
JSON_PATH = ROOT / 'recommended' / 'recommendations.json'
SIG_PATH = ROOT / 'recommended' / 'recommendations.json.sig'
FIXTURE = ROOT / 'tests' / 'host' / 'fixtures' / 'recommendations' / 'GameRecommendations-a9851601.swift.txt'
FIXTURE_SHA256 = '5dbaaa4d9382e0aad3ab0dd82a5cef088df7dd8fa34742dfe079a1b4c08e08f0'   # 2026-10-28: three /// comment lines put in English (privacy); no literal changed   # git show a9851601:app/Madeira/GameRecommendations.swift
LEDGER = Path.home() / 'madeira-local' / 'state' / 'recommendations-signed'
HEADER = ('// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n'
          '// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n')

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()

failures = []


def check(what, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + what + ('' if ok or not detail else f'\n     {detail}'))
    if not ok:
        failures.append(what)


def block(source, start, end):
    i = source.index(start)
    return source[i:source.index(end, i) + len(end)]


spec = importlib.util.spec_from_file_location('publish_recommendations', ROOT / 'tools' / 'publish-recommendations.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

# ---- the files ----------------------------------------------------------------------------------
source = (APP / 'GameRecommendationsSource.swift').read_text()
listing = (APP / 'GameRecommendationsList.swift').read_text()
lists = (APP / 'GameRecommendations.swift').read_text()
for path in (APP / 'GameRecommendationsSource.swift', APP / 'GameRecommendationsList.swift'):
    check(f'{path.name}: BCD\'s licence header', path.read_text().startswith(HEADER))
for path in (ROOT / 'tools' / 'publish-recommendations.py', Path(__file__)):
    check(f'{path.name}: BCD\'s licence header', path.read_text().split('\n', 1)[1].startswith(HEADER.replace('// ', '# ')))
data = JSON_PATH.read_bytes()
try:
    doc = tool.strict_json(data)
except ValueError as e:
    doc = None
    check('recommendations.json is JSON with no key twice in an object', False, str(e))
check('recommendations.json: format 1, a version, a date, rules ending with "standard"',
      bool(doc) and doc.get('format') == 1 and isinstance(doc.get('version'), int) and doc['version'] >= 1
      and re.fullmatch(r'\d{4}-\d{2}-\d{2}', doc.get('generated', '')) is not None and doc['rules'][-1]['id'] == 'standard')
check('the frozen Swift literals are a9851601\'s GameRecommendations.swift, byte for byte (three comment lines in English since 2026-10-28)',
      hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256)
check('GameRecommendations.swift holds no list any more (one source: the JSON)',
      'static let rdr2Steam' not in lists and 'config: """' not in lists and 'env.MADEIRA_' not in lists)

# ---- the wiring ---------------------------------------------------------------------------------
project = (ROOT / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
for name, build, ref in (('GameRecommendationsList.swift', 'A1000BA0', 'A2000BA0'),
                         ('GameRecommendationsSource.swift', 'A1000BA1', 'A2000BA1')):
    check(f'{name} is in the app\'s sources ({build}/{ref})',
          project.count(f'{build} /* {name} in Sources */') == 2 and project.count(f'{ref} /* {name} */') == 3
          and f'{ref} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = {name}; sourceTree = "<group>"; }};' in project)
resources = block(project, 'A3000003 /* Resources */ = {', '};')
check('recommendations.json is a bundle resource of the app (A1000BA2/A2000BA2, ../recommended from app/)',
      'A2000BA2 /* recommendations.json */ = {isa = PBXFileReference; lastKnownFileType = text.json; name = recommendations.json; '
      'path = ../recommended/recommendations.json; sourceTree = SOURCE_ROOT; };' in project
      and 'A1000BA2 /* recommendations.json in Resources */ = {isa = PBXBuildFile; fileRef = A2000BA2 /* recommendations.json */; };' in project
      and 'A1000BA2 /* recommendations.json in Resources */,' in resources and project.count('A1000BA2') == 2
      and (ROOT / 'app' / '..' / 'recommended' / 'recommendations.json').resolve() == JSON_PATH.resolve())
check('the app reads the bundled copy', 'Bundle.main.url(forResource: "recommendations", withExtension: "json")' in source)
library = (APP / 'Library.swift').read_text()
init = block(library, '    private init() {', '\n    }\n')
check('LibraryModel.init starts the check after the library took its lists; a newer list goes into use only with no game '
      'running, and re-applies them',
      '        applyRecommendations()\n        RecommendationsSource.start(idle: { [weak self] in self?.current == nil && '
      'wine_process_is_running() == 0 }, onChange: { [weak self] in self?.applyRecommendations() })   // BCD: ' in init)
check('the hook is the library\'s one use of the model', len(re.findall(r'\bRecommendationsSource\b', library)) == 1)
workflow = (ROOT / '.github/workflows/build-ipa.yml').read_text()
check('build-ipa.yml runs this check next to the recommended-settings one',
      'run: python3 tests/host/check-remote-recommendations.py --require-swift' in workflow
      and workflow.index('check-game-recommendations.py') < workflow.index('check-remote-recommendations.py') < workflow.index('check-autotune.py'))
check('the two addresses: the public repository, raw, main',
      'URL(string: "https://raw.githubusercontent.com/thebcd69/bcd-games/main/recommended/recommendations.json")!' in source
      and 'URL(string: "https://raw.githubusercontent.com/thebcd69/bcd-games/main/recommended/recommendations.json.sig")!' in source
      and source.count('https://') == 4)   # the two above and the two in the header comment
key = re.search(r'static let publicKey = "([A-Za-z0-9+/=]+)"', source)
check('the app carries a 32-byte Ed25519 public key', bool(key) and len(base64.b64decode(key.group(1))) == 32)
app_key = base64.b64decode(key.group(1)) if key else b''
model = source[source.index('// MARK: - The model'):]
said = re.findall(r'"((?:[^"\\]|\\.)*)"', model)
check('nothing the model says or logs names Madeira', not any('Madeira' in s for s in said), [s for s in said if 'Madeira' in s])
check('a refresh logs with the [recs] tag; a 404 is logged quietly (not as an error)',
      'LogStore.shared.log("[recs] no online list yet (404); ' in source
      and 'no online list yet (404); \\(current.origin.rawValue) list v\\(current.version) stays")\n' in source)

# ---- the signing tool -------------------------------------------------------------------------
VECTORS = [   # RFC 8032 section 7.1, tests 1-3
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60', '',
     'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb', '72',
     '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7', 'af82',
     'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
]
ok = True
for seed, message, public, signature in VECTORS:
    seed, message, public, signature = (bytes.fromhex(x) for x in (seed, message, public, signature))
    made = tool.sign(seed, message)
    bad = bytearray(made)
    bad[40] ^= 1
    ok &= (tool.public_key(seed) == public and made == signature and tool.verify(public, message, made)
           and not tool.verify(public, message + b'x', made) and not tool.verify(public, message, bytes(bad)))
check('the tool\'s Ed25519 gives RFC 8032\'s keys and signatures, and refuses a changed byte', ok)
check('the tool\'s key file is PKCS#8 PEM and reads back', tool.seed_from_pem(tool.pem(bytes(range(32)))) == bytes(range(32)))
old = {'format': 1, 'version': 3, 'generated': '2026-10-18', 'rules': [
    {'id': 'a', 'title': 'A', 'version': 2, 'match': {'programs': [{'name': 'a.exe'}]}, 'config': ['# a', 'x = 1']},
    {'id': 'standard', 'title': 's', 'version': 1, 'config': ['# s', 'env.MADEIRA_EC_HOOK_TRACE = 0']}]}


def variant(**changes):
    new = json.loads(json.dumps(old))
    for key, value in changes.items():
        if key == 'rule_a':
            new['rules'][0].update(value)
        elif key == 'drop_a':
            del new['rules'][0]
        else:
            new[key] = value
    return new


check('the tool: a newer list with its changed rule at a higher version may follow',
      tool.compare(variant(version=4, rule_a={'version': 3, 'config': ['# a', 'x = 2']}), old) == [])
check('the tool: notes alone may change without a rule version', tool.compare(variant(version=4, rule_a={'notes': ['n']}), old) == [])
check('the tool refuses: the same or a lower list version',
      len(tool.compare(variant(version=3), old)) == 1 and len(tool.compare(variant(version=2), old)) == 1)
check('the tool refuses: a rule gone', any('is gone' in p for p in tool.compare(variant(version=4, drop_a=True), old)))
check('the tool refuses: a rule version lowered', any('lower' in p for p in tool.compare(variant(version=4, rule_a={'version': 1}), old)))
check('the tool refuses: a rule changed at the same version',
      any('raise its version' in p for p in tool.compare(variant(version=4, rule_a={'config': ['# a', 'x = 2']}), old)))
check('the tool refuses: an existing rule given a minBuild (older builds would lose it)',
      any('minBuild' in p for p in tool.compare(variant(version=4, rule_a={'version': 3, 'minBuild': 700}), old)))
narrower = variant(version=4, rule_a={'version': 3, 'match': {'programs': [{'name': 'a.exe', 'steam': True}]}})
check('the tool refuses: a rule that stops matching a program it matched (unless told it is the point)',
      any('no longer matches program' in p for p in tool.compare(narrower, old))
      and tool.compare(narrower, old, allow_narrower=True) == []
      and tool.compare(variant(version=4, rule_a={'version': 3, 'match': {'programs': [{'name': 'a.exe'}, {'name': 'b.exe'}],
                                                                          'steamAppIDs': [7]}}), old) == [])
try:
    tool.strict_json(b'{"a": 1, "a": 2}')
    twice = False
except ValueError:
    twice = True
check('the tool refuses JSON with a key twice in an object', twice)

# ---- the committed signature ------------------------------------------------------------------
sig_bytes = SIG_PATH.read_bytes() if SIG_PATH.exists() else b''   # read once: the Swift side judges these same bytes
committed = tool.signature_from_file(sig_bytes) if sig_bytes else None
signed = bool(committed) and tool.verify(app_key, data, committed)
if signed:
    print(f'ok   recommendations.json.sig is valid for this list (v{doc["version"]}) under the app\'s key')
else:
    print('WARN recommendations.json changed after its signature (or has none): run tools/publish-recommendations.py '
          'before the list goes online; the app\'s built-in copy needs no signature')
previous = tool.last_signed(LEDGER) if LEDGER.is_dir() else None
if previous and doc and doc['version'] < previous[0]:
    print(f'WARN this list (v{doc["version"]}) is older than the last one signed on this Mac (v{previous[0]}): a device '
          'that downloaded that one keeps it over this build\'s copy')

# ---- the Swift fixture ------------------------------------------------------------------------
STUBS = r'''
import Foundation

final class LogStore {
    static let shared = LogStore()
    enum Level { case info, error }
    var lines: [(String, Level)] = []
    func log(_ message: String, level: Level = .info) { lines.append((message, level)) }
    func last(_ prefix: String) -> (String, Level)? { lines.last { $0.0.hasPrefix(prefix) } }
}

struct ConfigOption {
    enum Kind { case bool, int, text, choice }
    let key: String
    let category: String
    init(key: String, title: String, kind: Kind, defaultValue: String, category: String, note: String,
         choices: [(String, String)], sources: [String]) {
        self.key = key
        self.category = category
    }
}

enum ConfigCatalog {}
'''

MAIN = r'''
import Foundation
import CryptoKit

var failures = 0
func check(_ ok: Bool, _ what: String, line: Int = #line) {
    if !ok { print("FAILED (main.swift line \(line)): \(what)"); failures += 1 }
}
let env = ProcessInfo.processInfo.environment
let repoJSON = URL(fileURLWithPath: env["BCD_RECS_JSON"]!)
let work = URL(fileURLWithPath: env["BCD_RECS_WORK"]!, isDirectory: true)
let support = work.appendingPathComponent("support", isDirectory: true)
UserDefaults.standard.removeObject(forKey: RecommendationsSource.lastCheckKey)
RecommendationsStore.builtInURL = repoJSON
RecommendationsStore.directory = support
RecommendationsStore.build = 602
let builtInData = try! Data(contentsOf: repoJSON)

// MARK: 1. The built-in list is the Swift literals it replaced

let builtInFile = try! RecommendationsFile.parse(builtInData)
do {
    try builtInFile.checkPolicy(RecommendationsStore.policy, build: nil, everyLine: true)
    check(true, "")
} catch {
    check(false, "the built-in list fails the download policy: \(error)")
}
let active = GameRecommendations.active
check(active.origin == .builtIn && active.version == builtInFile.version && active.rules.count == builtInFile.rules.count,
      "the app starts with the built-in list")
var same = 0, newer = 0
for ram: UInt64 in [11_695 << 20, 7_500 << 20, 16 << 30] {
    GameRecommendations.deviceRAMBytes = ram
    OldGameRecommendations.deviceRAMBytes = ram
    for old in OldGameRecommendations.all {
        guard let new = active.recommendations.first(where: { $0.id == old.id }) else {
            check(false, "\(old.id): the rule is gone"); continue
        }
        check(new.version >= old.version, "\(old.id): version \(new.version) is lower than the literal's \(old.version)")
        guard new.version == old.version else { newer += 1; continue }
        same += 1
        check(new.title == old.title && new.config == old.config && new.fileText == old.fileText, "\(old.id): its file text")
        check(new.avx == old.avx && new.nvidia == old.nvidia && new.wineVCRT == old.wineVCRT && new.fastSync == old.fastSync
              && new.semaphoreFastPath == old.semaphoreFastPath && new.fpsMode == old.fpsMode && new.resolution == old.resolution
              && new.display == old.display && new.smallRAMConfig == old.smallRAMConfig, "\(old.id): its switches")
        check(new.switchSummary == old.switchSummary && new.summary(home: true) == old.summary(home: true), "\(old.id): its summary")
    }
}
GameRecommendations.deviceRAMBytes = 11_695 << 20
OldGameRecommendations.deviceRAMBytes = 11_695 << 20
let oldOrder = OldGameRecommendations.all.map(\.id)
check(active.recommendations.map(\.id).filter(oldOrder.contains) == oldOrder, "the rules keep the literals' order (the first match wins)")
print("PASS: \(same / 3) rules equal the Swift literals on 12 GB, 8 GB and 16 GB devices, file text, switches and summaries"
      + (newer > 0 ? " (\(newer / 3) at a newer version)" : ""))

// MARK: 2. Games are recognised as before

let steamRoots = [#"C:\Program Files (x86)\Steam\steamapps\common"#, #"D:\SteamLibrary\steamapps\common"#,
                  #"E:\Games\STEAMAPPS\COMMON"#, "F:/Lib/steamapps/common"]
let otherRoots = [#"C:\Games"#, #"D:\Some Folder\Sub"#]
let folders = ["Red Dead Redemption 2", "Grand Theft Auto V Enhanced", "Grand Theft Auto V", "Ghost of Tsushima DIRECTOR'S CUT",
               "Ghost of Tsushima", "GodOfWar", "God of War", "Horizon Zero Dawn", "Horizon Forbidden West Complete Edition",
               "Ratchet & Clank - Rift Apart", "Crysis Remastered", "Crysis 2 Remastered", "Crysis 3 Remastered", "MSFS2024",
               "Counter-Strike Global Offensive", "Breakneck", "Portal 2", "Horizon Zero Dawn Remastered", "GTAV Enhanced Copy"]
let programs = ["RDR2.exe", "PlayRDR2.exe", "PlayGTAV.exe", "GTA5_Enhanced.exe", "GTA5.exe", "GhostOfTsushima.exe", "GoW.exe",
                "HorizonZeroDawn.exe", "HorizonForbiddenWest.exe", "RiftApart.exe", "CrysisRemastered.exe", "Crysis2Remastered.exe",
                "Crysis3Remastered.exe", "FlightSimulator2024.exe", "cs2.exe", "Warcraft II BNE.exe", "Breakneck.exe", "foo.exe",
                "vc_redist.x64.exe", "GoWR.exe", "Crysis.exe", "FlightSimulator.exe", "HorizonZeroDawnRemastered.exe", "start.bat",
                "launch.cmd", "crs-handler.exe"]
let appIDs: [Int?] = [nil, 1174180, 3240220, 271590, 2215430, 1593500, 1151640, 2420110, 1895880, 1715130, 2096600, 2096610,
                      2537590, 730, 432940, 620, 1]
let besides: [[String]] = [[], ["GTA5_Enhanced.exe"], ["GTA5.exe"], ["GTA5_Enhanced.exe", "GTA5.exe"]]
var compared = 0, recognised = 0, newlyRecognised = 0
func compare(_ path: String, _ app: Int?, _ files: Set<String>) {
    let exists: (String) -> Bool = { files.contains($0.lowercased()) }
    let before = OldGameRecommendations.match(windowsPath: path, steamAppID: app, fileExists: exists)?.id
    let now = GameRecommendations.match(windowsPath: path, steamAppID: app, fileExists: exists)?.id
    compared += 1
    if let before {
        recognised += 1
        check(now == before, "\(path) (App ID \(app.map(String.init) ?? "none")): \(now ?? "none") instead of \(before)")
    } else if now != nil {
        newlyRecognised += 1
    }
}
for root in steamRoots + otherRoots {
    for folder in folders {
        for name in [folder, folder.uppercased()] {
            for app in appIDs {
                compare(root + #"\"# + name, app, [])
                compare(root + #"\"# + name + #"\"#, app, [])
            }
        }
    }
}
for root in steamRoots + otherRoots {
    for folder in ["Red Dead Redemption 2", "Grand Theft Auto V Enhanced", "Grand Theft Auto V", "Some Game"] {
        for sub in ["", #"\Bin64"#] {
            let dir = (root + #"\"# + folder + sub).replacingOccurrences(of: "/", with: #"\"#)
            for program in programs {
                for name in [program, program.uppercased(), program.lowercased()] {
                    for files in besides {
                        let set = Set(files.map { (dir + #"\"# + $0).lowercased() })
                        for app in [nil, 1174180, 271590, 620] as [Int?] {
                            compare(root + #"\"# + folder + sub + #"\"# + name, app, set)
                        }
                    }
                }
            }
        }
    }
}
for path in ["", #"C:\"#, "C:", #"\\"#, "steamapps", #"steamapps\common"#, #"x\steamapps\common\"#] {
    for app in appIDs { compare(path, app, []) }
}
print("PASS: \(compared) paths, the \(recognised) the literals recognised get the same rule"
      + (newlyRecognised > 0 ? " (\(newlyRecognised) more match newer rules)" : ""))

// MARK: 3. Signatures

let key = Curve25519.Signing.PrivateKey()
let otherKey = Curve25519.Signing.PrivateKey()
let testPublicKey = key.publicKey.rawRepresentation.base64EncodedString()
func sigFile(_ signature: Data) -> Data { Data((signature.base64EncodedString() + "\n").utf8) }
func sign(_ data: Data, with k: Curve25519.Signing.PrivateKey = key) -> Data { try! k.signature(for: data) }
let goodSignature = sign(builtInData)
var flipped = builtInData
flipped[flipped.count / 2] ^= 0x20
var badSignature = goodSignature
badSignature[10] ^= 1
check(RecommendationsSignature.isValid(goodSignature, for: builtInData, publicKey: testPublicKey), "a valid signature")
check(!RecommendationsSignature.isValid(goodSignature, for: flipped, publicKey: testPublicKey), "one byte of the file changed")
check(!RecommendationsSignature.isValid(badSignature, for: builtInData, publicKey: testPublicKey), "one bit of the signature changed")
check(!RecommendationsSignature.isValid(sign(builtInData, with: otherKey), for: builtInData, publicKey: testPublicKey), "another key")
check(!RecommendationsSignature.isValid(goodSignature, for: builtInData, publicKey: "not a key"), "no key")
check(RecommendationsSignature.signature(fromFile: sigFile(goodSignature)) == goodSignature
      && RecommendationsSignature.signature(fromFile: Data("  \(goodSignature.base64EncodedString())\r\n".utf8)) == goodSignature,
      "a .sig file reads, spaces and line ends aside")
check(RecommendationsSignature.signature(fromFile: Data("not base64\n".utf8)) == nil
      && RecommendationsSignature.signature(fromFile: sigFile(goodSignature.prefix(63))) == nil
      && RecommendationsSignature.signature(fromFile: Data(repeating: 0x41, count: 4096)) == nil, "a .sig file that is no signature")
let pyData = try! Data(contentsOf: URL(fileURLWithPath: env["BCD_RECS_PY_DATA"]!))
let pySig = RecommendationsSignature.signature(fromFile: try! Data(contentsOf: URL(fileURLWithPath: env["BCD_RECS_PY_SIG"]!)))
check(pySig != nil && RecommendationsSignature.isValid(pySig!, for: pyData, publicKey: env["BCD_RECS_PY_KEY"]!)
      && !RecommendationsSignature.isValid(pySig!, for: pyData + Data([0x0a]), publicKey: env["BCD_RECS_PY_KEY"]!),
      "CryptoKit takes the tool's (Python) signature, and not for other bytes")
// The committed list and .sig as the Python side read them (one snapshot for both sides).
let committedJSON = try! Data(contentsOf: URL(fileURLWithPath: env["BCD_RECS_COMMITTED_JSON"]!))
let committed = (try? Data(contentsOf: URL(fileURLWithPath: env["BCD_RECS_COMMITTED_SIG"]!))).flatMap(RecommendationsSignature.signature(fromFile:))
print("COMMITTED-SIG: " + ((committed.map { RecommendationsSignature.isValid($0, for: committedJSON, publicKey: RecommendationsSignature.publicKey) } ?? false)
                            ? "valid" : "invalid"))
print("PASS: signatures: valid, a changed byte or bit, another key, a .sig file's text, the tool's own signature")

// MARK: 4. The layout and the policy

func object(_ data: Data) -> [String: Any] { try! JSONSerialization.jsonObject(with: data) as! [String: Any] }
func data(_ object: [String: Any]) -> Data { try! JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted]) }
func edited(_ change: (inout [String: Any]) -> Void) -> Data {
    var o = object(builtInData)
    change(&o)
    return data(o)
}
func editRule(_ id: String, _ change: @escaping (inout [String: Any]) -> Void) -> Data {
    edited { o in
        var rules = o["rules"] as! [[String: Any]]
        let i = rules.firstIndex { $0["id"] as? String == id }!
        change(&rules[i])
        o["rules"] = rules
    }
}
func addLine(_ id: String, _ line: Any) -> Data { editRule(id) { r in r["config"] = (r["config"] as! [Any]) + [line] } }
enum Kind { case ok, format, policy }
func kind(_ data: Data, build: Int? = 600, everyLine: Bool = false) -> Kind {
    do {
        let file = try RecommendationsFile.parse(data)
        try file.checkPolicy(RecommendationsStore.policy, build: build, everyLine: everyLine)
        return .ok
    } catch RecommendationsError.policy {
        return .policy
    } catch {
        return .format
    }
}
check(kind(builtInData) == .ok, "the built-in list")
check(kind(edited { $0["comment"] = "x" }) == .format, "an unknown top-level field")
check(kind(editRule("rdr2-steam") { $0["minbuild"] = 1 }) == .format, "an unknown rule field (a typo)")
check(kind(editRule("rdr2-steam") { $0["switches"] = ["avx": true, "metalfx": true] }) == .format, "an unknown switch")
check(kind(edited { $0["format"] = 2 }) == .format, "a format this build does not read")
check(kind(edited { o in o["rules"] = (o["rules"] as! [[String: Any]]).filter { $0["id"] as? String != "standard" } }) == .format,
      "no standard rule")
check(kind(editRule("standard") { $0["match"] = ["programs": [["name": "x.exe"]]] }) == .format, "a standard rule with a match")
check(kind(editRule("breakneck") { $0["match"] = [String: Any]() }) == .format, "a rule that matches nothing")
check(kind(editRule("rdr2-steam") { $0["switches"] = ["avx": 1] }) == .format, "1 for true")
check(kind(editRule("rdr2-steam") { $0["switches"] = ["fpsMode": 9] }) == .format, "an FPS mode that does not exist")
check(kind(editRule("rdr2-steam") { $0["switches"] = ["resolution": "huge"] }) == .format, "a resolution that is not WxH")
check(kind(editRule("rdr2-steam") { $0["switches"] = ["display": "zoom"] }) == .format, "a display mode that does not exist")
check(kind(editRule("rdr2-steam") { $0["version"] = "11" }) == .format, "a version as text")
check(kind(Data(String(decoding: builtInData, as: UTF8.self).replacingOccurrences(of: "\"version\": 13,", with: "\"version\": 13.5,")   /* rdr2-steam v13 (round 630) */.utf8)) == .format,
      "a version that is not whole")
check(kind(editRule("breakneck") { $0["match"] = ["programs": [["name": #"C:\Games\breakneck.exe"#]]] }) == .format, "a program with a folder")
check(kind(editRule("breakneck") { $0["match"] = ["steamFolders": ["Breakneck"]] }) == .format, "a Steam folder not lowercased")
check(kind(addLine("breakneck", " swap-mb = 1024")) == .format && kind(addLine("breakneck", "swap-mb=1024")) == .format
      && kind(addLine("breakneck", "")) == .format && kind(addLine("breakneck", "swap-mb = 1\u{7}")) == .format,
      "lines that are not \"key = value\" as the lists write them")
check(kind(addLine("breakneck", "env.MADEIRA_WOW_RWX_PLAIN = 0")) == .format, "a key twice for the same builds")
check(kind(editRule("breakneck") { rule in
    let kept = (rule["config"] as! [Any]).filter { line in (line as? String) != "env.MADEIRA_WOW_RWX_PLAIN = 1" }
    rule["config"] = kept + [["line": "env.MADEIRA_WOW_RWX_PLAIN = 1", "maxBuild": 599], ["line": "env.MADEIRA_WOW_RWX_PLAIN = 0", "minBuild": 600]]
}) == .ok, "the same key for builds that do not overlap")
check(kind(addLine("breakneck", ["line": "swap-mb = 1024"])) == .format, "a line object without a build")
check(kind(addLine("breakneck", ["line": "swap-mb = 1024", "minBuild": 601, "maxBuild": 600])) == .format, "maxBuild below minBuild")
check(kind(addLine("breakneck", "# a comment the file may carry")) == .ok, "comments")
check(kind(addLine("breakneck", "remote = 192.168.1.2 token")) == .policy, "an app-level catalog key (a remote Metal host)")
check(kind(addLine("breakneck", "args = -evil")) == .policy, "launch arguments")
check(kind(addLine("breakneck", "no-such-key = 1")) == .policy, "a key the catalog does not have")
check(kind(addLine("breakneck", "env.MADEIRA_EXE = cmd.exe")) == .policy, "an environment name off the list")
check(kind(addLine("breakneck", "env.DYLD_INSERT_LIBRARIES = x")) == .policy, "a loader variable")
check(kind(addLine("breakneck", "env.WINEDLLOVERRIDES = d3d11=n")) == .policy && kind(addLine("breakneck", "env.WINEDLLOVERRIDES = d3d11=n,b")) == .policy,
      "a DLL override that loads a native DLL")
check(kind(addLine("breakneck", "env.WINEDLLOVERRIDES = video64=;mfplat=b")) == .ok, "DLLs off or Wine's own")
check(kind(addLine("breakneck", #"env.MADEIRA_PRELOAD_DLLS = C:\x\evil.dll"#)) == .policy, "a DLL to preload given with a folder")
check(kind(addLine("breakneck", "dxmt = d3d11.mipClampBC=1")) == .ok && kind(addLine("breakneck", "swap-min-kb = 64")) == .ok,
      "the two app-level keys that are game settings")
check(kind(addLine("breakneck", "env.MADEIRA_FEX_AVX = 1")) == .ok, "auto-tune's known fix")
// god-of-war: a row without an opt-in line of its own (the Rockstar rows carry tbs since round 630, and a key may not come twice)
check(kind(addLine("god-of-war", "env.MADEIRA_OPTIN_DLLS = oledlg")) == .ok && kind(addLine("god-of-war", "env.MADEIRA_OPTIN_DLLS = fwpuclnt,SensApi")) == .ok
      && kind(addLine("god-of-war", "env.MADEIRA_OPTIN_DLLS = sensapi;oledlg")) == .ok && kind(addLine("god-of-war", "env.MADEIRA_OPTIN_DLLS = oledlg,evil")) == .policy
      && kind(addLine("god-of-war", "env.MADEIRA_OPTIN_DLLS = oledlg32")) == .policy, "the opt-in DLLs oledlg, fwpuclnt and sensapi are allowed, another name is not")
check(kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 400000000")) == .ok && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 0x400000000")) == .ok
      && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 40000000")) == .ok && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 1000000000")) == .ok,
      "the .NET GC's address range, 1 GB to 64 GB in hexadecimal (with or without 0x)")
check(kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 20000000")) == .policy && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 2000000000")) == .policy
      && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 16GB")) == .policy && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = -400000000")) == .policy
      && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 0x")) == .policy && kind(addLine("breakneck", "env.DOTNET_GCRegionRange = 4000000000000000000")) == .policy,
      "...and nothing else: not under 1 GB, not over 64 GB, not text")
check(["MADEIRA_FAULT_GUARD_RATE", "MADEIRA_MFC_DOCK", "MADEIRA_DOCK_SCRIPT_REGISTRY", "MADEIRA_ANON_CAS_FLUSH", "MADEIRA_NLS_EXTRA", "MADEIRA_WG_VIDEO"]
        .allSatisfy { kind(addLine("breakneck", "env.\($0) = 1")) == .ok && kind(addLine("breakneck", "env.\($0) = 0")) == .ok },
      "the build 591 switches (and Albino Lullaby's media switch) that are 0 or 1")
check(["MADEIRA_FAULT_GUARD_RATE", "MADEIRA_MFC_DOCK", "MADEIRA_DOCK_SCRIPT_REGISTRY", "MADEIRA_ANON_CAS_FLUSH", "MADEIRA_NLS_EXTRA", "MADEIRA_WG_VIDEO"]
        .allSatisfy { kind(addLine("breakneck", "env.\($0) = 2")) == .policy && kind(addLine("breakneck", "env.\($0) = on")) == .policy
                      && kind(addLine("breakneck", "env.\($0) = 1;2")) == .policy },
      "...and nothing else: not 2, not text")
check(kind(addLine("breakneck", "env.MADEIRA_CTX_FRAME = 1")) == .ok && kind(addLine("breakneck", "env.MADEIRA_CTX_FRAME = 0")) == .ok
      && ["2", "on", "1;2", "-1"].allSatisfy { kind(addLine("breakneck", "env.MADEIRA_CTX_FRAME = \($0)")) == .policy },
      "the Wine server's stopped-thread context switch (build 598): 0 or 1, nothing else")
check(["MADEIRA_NTDLL_XSEC", "MADEIRA_SHARED_SECTION_EXEC", "MADEIRA_NTDLL_DATA_SYNC_RANGE", "MADEIRA_FAULT_LOOP_GUEST_PROGRESS", "MADEIRA_D2R_PROBE_RECOVERY"]
        .allSatisfy { name in kind(addLine("breakneck", "env.\(name) = 1")) == .ok && kind(addLine("breakneck", "env.\(name) = 0")) == .ok
                      && ["2", "on", "1;2", "-1"].allSatisfy { v in kind(addLine("breakneck", "env.\(name) = \(v)")) == .policy } },
      "Diablo II: Resurrected's five executable-section switches (build 598, bcd-games#4): 0 or 1, nothing else")
check(["8", "32", "64", "512"].allSatisfy { kind(addLine("breakneck", "env.MADEIRA_USER_VA_GB = \($0)")) == .ok }
      && ["0", "7", "513", "1000", "32GB", "-32", "3.5", "0x20"].allSatisfy { kind(addLine("breakneck", "env.MADEIRA_USER_VA_GB = \($0)")) == .policy },
      "the advertised address space in whole GiB, 8 to 512 (FINAL FANTASY TACTICS)")
check(kind(addLine("breakneck", "env._JAVA_OPTIONS = -XX:-UseZGC")) == .ok
      && ["-XX:+UseZGC", "-Xmx1g", "-XX:-UseZGC -Xmx1g", "-javaagent:x.jar", "-XX:-UseZGC;-Xmx1g", "-XX:-UseG1GC"].allSatisfy { kind(addLine("breakneck", "env._JAVA_OPTIONS = \($0)")) == .policy },
      "the JVM's own variable only with the one value that turns ZGC off (Project Zomboid)")
let later = addLine("breakneck", ["line": "a-key-of-build-700 = 1", "minBuild": 700])
check(kind(later, build: 600) == .ok && kind(later, build: 700) == .policy && kind(later, build: 600, everyLine: true) == .policy
      && kind(later, build: nil) == .ok,
      "a line for a later build is checked by the builds that apply it (and by the tool)")
print("PASS: the layout (unknown fields, types, lines, builds) and the policy (catalog game keys, the environment list, DLL overrides)")

// MARK: 5. minBuild / maxBuild

let gated = try! RecommendationsFile.parse(editRule("breakneck") { r in
    r["config"] = (r["config"] as! [Any]) + [["line": "env.MADEIRA_FEX_AVX = 1", "minBuild": 601],
                                            ["line": "env.MADEIRA_LEAN_DIAG = 1", "maxBuild": 599]]
})
func breakneck(_ build: Int?) -> String { gated.list(build: build, origin: .builtIn).recommendations.first { $0.id == "breakneck" }!.config }
check(!breakneck(600).contains("FEX_AVX") && !breakneck(600).contains("LEAN_DIAG"), "build 600: neither")
check(breakneck(601).hasSuffix("env.MADEIRA_FEX_AVX = 1") && breakneck(599).hasSuffix("env.MADEIRA_LEAN_DIAG = 1"), "each its builds")
check(!breakneck(nil).contains("FEX_AVX") && !breakneck(nil).contains("LEAN_DIAG"), "an unknown build: no limited line")
let newRule = try! RecommendationsFile.parse(edited { o in
    var rules = o["rules"] as! [[String: Any]]
    rules.insert(["id": "new-game", "title": "New Game", "version": 1, "minBuild": 650, "match": ["programs": [["name": "newgame.exe"]]],
                  "config": ["# BCD's recommended settings for New Game.", "env.MADEIRA_FEX_AVX = 1"]], at: 0)
    o["rules"] = rules
})
check(newRule.list(build: 649, origin: .builtIn).match(windowsPath: #"C:\G\NewGame.exe"#, steamAppID: nil, fileExists: { _ in false }) == nil
      && newRule.list(build: 650, origin: .builtIn).match(windowsPath: #"C:\G\NewGame.exe"#, steamAppID: nil, fileExists: { _ in false })?.id == "new-game",
      "a rule's minBuild")
print("PASS: minBuild and maxBuild, for a line and a rule")

// MARK: 6. The pipeline, with a stand-in network

RecommendationsStore.publicKey = testPublicKey
func signed(_ json: Data, with k: Curve25519.Signing.PrivateKey = key) -> RecommendationsFetch { .ok(json: json, signature: sigFile(sign(json, with: k))) }
func bumped(_ version: Int, rdr2: Int? = nil) -> Data {
    var o = object(builtInData)
    o["version"] = version
    var rules = o["rules"] as! [[String: Any]]
    let i = rules.firstIndex { $0["id"] as? String == "rdr2-steam" }!
    rules[i]["version"] = rdr2 ?? (rules[i]["version"] as! Int) + 1
    rules[i]["config"] = (rules[i]["config"] as! [Any]) + ["env.MADEIRA_FREE_DELAY_MS = 0"]   // not a line rdr2-steam has (it pins MADEIRA_FEX_AVX = 0 since v11)
    o["rules"] = rules
    return data(o)
}
func fresh() {
    try? FileManager.default.removeItem(at: support)
    UserDefaults.standard.removeObject(forKey: RecommendationsSource.lastCheckKey)
    GameRecommendations.reload()
}
var fetches = 0
func source(_ answer: RecommendationsFetch) -> RecommendationsSource {
    RecommendationsSource(fetch: { fetches += 1; return answer })
}
func kept() -> Bool { FileManager.default.fileExists(atPath: RecommendationsStore.keptURL!.path) }
let v1 = builtInFile.version

fresh()
var s = source(.offline("The Internet connection appears to be offline."))
var r = await s.refresh()
check(r == .offline(version: v1, origin: .builtIn, notPublished: false) && s.lastChecked == nil
      && GameRecommendations.active.origin == .builtIn && LogStore.shared.last("[recs] offline") != nil, "offline: the built-in list stays")
check(r.message == "Offline: using the built-in list (list \(v1)).", r.message)

s = source(.notPublished)
r = await s.refresh()
let quiet = LogStore.shared.last("[recs] no online list yet (404)")
check(r == .offline(version: v1, origin: .builtIn, notPublished: true) && quiet?.1 == .info && s.lastChecked != nil
      && GameRecommendations.active.version == v1 && !kept(), "a 404: quietly the built-in list")
check(r.message == "No online list yet: using the built-in list (list \(v1)).", r.message)

let rejections: [(String, RecommendationsFetch)] = [
                       ("a signature of other bytes", RecommendationsFetch.ok(json: bumped(v1 + 1), signature: sigFile(sign(builtInData)))),
                       ("one byte changed after signing", { () -> RecommendationsFetch in
                           let json = bumped(v1 + 1)
                           var changed = json
                           changed[changed.count / 3] ^= 0x01
                           return .ok(json: changed, signature: sigFile(sign(json)))
                       }()),
                       ("another key", signed(bumped(v1 + 1), with: otherKey)),
                       ("a .sig file that is no signature", .ok(json: bumped(v1 + 1), signature: Data("<html>404</html>".utf8))),
                       ("bad JSON", signed(Data("{\"format\": 1, \"version\": 9".utf8))),
                       ("an unknown key", signed(editRule("rdr2-steam") { r in
                           r["config"] = (r["config"] as! [Any]) + ["remote = 10.0.0.1 x"]
                       }.replacingVersion(v1 + 1))),
                       ("a rule removed", signed(edited { o in
                           o["version"] = v1 + 1
                           o["rules"] = (o["rules"] as! [[String: Any]]).filter { $0["id"] as? String != "breakneck" }
                       })),
                       ("a rule's version lowered", signed(bumped(v1 + 1, rdr2: 9))),
                       ("a redirect elsewhere", .invalid("redirected to example.com"))]
for (what, answer) in rejections {
    fresh()
    LogStore.shared.lines.removeAll()
    s = source(answer)
    r = await s.refresh()
    guard case .rejected(let reason, let version) = r else { check(false, "\(what): \(r)"); continue }
    check(version == v1 && GameRecommendations.active.origin == .builtIn && GameRecommendations.active.version == v1 && !kept()
          && LogStore.shared.last("[recs] rejected the online list: ") != nil && r.message.hasPrefix("Rejected: ")
          && !reason.isEmpty, "\(what): rejected, the built-in list stays (\(reason))")
}
fresh()
LogStore.shared.lines.removeAll()
_ = await source(rejections[5].1).refresh()
check(LogStore.shared.lines.contains { $0.0.contains("remote is not a game setting") && $0.1 == .error },
      "the log names the key a download may not set")

fresh()
s = source(signed(builtInData))
r = await s.refresh()
check(r == .upToDate(version: v1) && r.message.hasPrefix("Up to date") && !kept(), "the same list online: up to date")

fresh()
var changes = 0
s = source(signed(bumped(v1 + 1)))
s.onChange = { changes += 1 }
r = await s.refresh()
let rdr2Title = GameRecommendations.active.recommendations.first { $0.id == "rdr2-steam" }!.title
check(r == .updated(version: v1 + 1, changed: [rdr2Title]) && changes == 1 && kept(), "a newer list: in use, kept, the library told once")
check(r.message == "Updated to list \(v1 + 1): 1 change (\(rdr2Title)).", r.message)
check(GameRecommendations.active.origin == .downloaded && GameRecommendations.active.version == v1 + 1 && s.version == v1 + 1
      && s.origin == .downloaded && s.sourceDescription.hasPrefix("Downloaded: list \(v1 + 1)"), "the model shows it")
check(GameRecommendations.match(windowsPath: #"C:\Program Files (x86)\Steam\steamapps\common\Red Dead Redemption 2"#, steamAppID: nil,
                                fileExists: { _ in false })!.fileText.hasSuffix("env.MADEIRA_FREE_DELAY_MS = 0\n"), "games match the newer list")
s = source(signed(bumped(v1 + 1)))
r = await s.refresh()
check(r == .upToDate(version: v1 + 1), "the same newer list again: up to date")
s = source(signed(builtInData))
r = await s.refresh()
check(r == .upToDate(version: v1 + 1) && GameRecommendations.active.version == v1 + 1, "an older list online: the newer one stays")
s = source(.offline("timed out"))
r = await s.refresh()
check(r.message == "Offline: using the downloaded list (list \(v1 + 1)).", r.message)

GameRecommendations.reload()
check(GameRecommendations.active.origin == .downloaded && GameRecommendations.active.version == v1 + 1, "the next start uses the kept download")

fresh()
changes = 0
s = source(signed(bumped(v1 + 1)))
s.idle = { false }
s.onChange = { changes += 1 }
r = await s.refresh()
check(GameRecommendations.active.origin == .builtIn && s.version == v1 && changes == 0 && kept()
      && LogStore.shared.last("[recs] a game is running: list v\(v1 + 1) is used from the next start") != nil,
      "a newer list that comes while a game runs is kept, not used: the running game's config does not change")
GameRecommendations.reload()
check(GameRecommendations.active.origin == .downloaded && GameRecommendations.active.version == v1 + 1, "the next start uses it")
var keptData = try! Data(contentsOf: RecommendationsStore.keptURL!)
keptData[keptData.count - 40] ^= 0x02
try! keptData.write(to: RecommendationsStore.keptURL!)
GameRecommendations.reload()
check(GameRecommendations.active.origin == .builtIn && LogStore.shared.last("[recs] the kept download was not used") != nil,
      "a kept download that no longer checks out: the built-in list")

fresh()
s = source(signed(bumped(v1 + 1)))
_ = await s.refresh()
let newerBuiltIn = work.appendingPathComponent("newer-built-in.json")
try! bumped(v1 + 5).write(to: newerBuiltIn)
RecommendationsStore.builtInURL = newerBuiltIn
GameRecommendations.reload()
check(GameRecommendations.active.origin == .builtIn && GameRecommendations.active.version == v1 + 5, "an app update with a newer list wins over the download")
RecommendationsStore.builtInURL = repoJSON

fresh()
s = source(signed(editRule("rdr2-steam") { r in r["minBuild"] = 700 }.replacingVersion(v1 + 1)))
r = await s.refresh()
check({ if case .rejected = r { return true }; return false }() && GameRecommendations.active.origin == .builtIn,
      "a list that would take a rule away from this build (its minBuild) is rejected")

fresh()
fetches = 0
let slow = RecommendationsSource(fetch: { fetches += 1; try? await Task.sleep(nanoseconds: 200_000_000); return .notPublished })
async let first = slow.refresh()
async let second = slow.refresh()
let both = await [first, second]
check(fetches == 1 && both[0] == both[1], "two refreshes at once share one fetch")

let now = Date()
check(RecommendationsSource.backgroundCheckDue(lastChecked: nil, now: now)
      && !RecommendationsSource.backgroundCheckDue(lastChecked: now.addingTimeInterval(-3600), now: now)
      && RecommendationsSource.backgroundCheckDue(lastChecked: now.addingTimeInterval(-7 * 3600), now: now)
      && RecommendationsSource.backgroundCheckDue(lastChecked: now.addingTimeInterval(3600), now: now),
      "the start's check: none within 6 hours of an answer (a clock set back does not stop it)")
fresh()
UserDefaults.standard.set(Date().addingTimeInterval(-3600).timeIntervalSince1970, forKey: RecommendationsSource.lastCheckKey)
fetches = 0
RecommendationsSource.shared.fetch = { fetches += 1; return .notPublished }
RecommendationsSource.start(idle: { true }, onChange: { })
RecommendationsSource.start(idle: { true }, onChange: { })
try? await Task.sleep(nanoseconds: 300_000_000)
check(fetches == 0, "an answer an hour ago: the start does not ask")
check(![RecommendationsRefreshResult.upToDate(version: 1), .updated(version: 2, changed: ["X", "Y"]), .updated(version: 2, changed: []),
        .offline(version: 1, origin: .builtIn, notPublished: true), .offline(version: 1, origin: .downloaded, notPublished: false),
        .rejected(reason: "x", version: 1)].contains { $0.message.contains("Madeira") }, "no message names Madeira")
print("PASS: offline and 404 keep the list (quietly), bad signatures, changed bytes, other keys, bad JSON, unknown keys, removed or "
      + "lowered rules are rejected, older lists are up to date, a newer one is used, kept and read again (while a game runs: "
      + "kept for the next start), a broken kept file falls back, a newer build wins, one fetch at a time, 6 hours between "
      + "the start's checks")

UserDefaults.standard.removeObject(forKey: RecommendationsSource.lastCheckKey)
exit(failures == 0 ? 0 : 1)
'''

EXTRAS = r'''
import Foundation

extension Data {
    /// The JSON with its top-level "version" set (the test's lists).
    func replacingVersion(_ version: Int) -> Data {
        var o = try! JSONSerialization.jsonObject(with: self) as! [String: Any]
        o["version"] = version
        return try! JSONSerialization.data(withJSONObject: o, options: [.prettyPrinted])
    }
}
'''

with tempfile.TemporaryDirectory(prefix='bcd-remote-recs-') as directory:
    tmp = Path(directory)
    compiler = shutil.which('swiftc')
    if not compiler:
        if args.require_swift:
            raise SystemExit('Swift compiler required for the remote recommendations fixture')
        print('SKIP: Swift compiler unavailable; macOS CI runs this check with --require-swift')
    else:
        old_src = FIXTURE.read_text()
        # The owner's change of 2026-10-18: a list's first line says BCD; nothing else of the literals changes.
        check('the literals carry eighteen "# Madeira\'s recommended settings for" lines, now "# BCD\'s"',
              old_src.count("        # Madeira's recommended settings for ") == 18)
        old_src = old_src.replace("        # Madeira's recommended settings for ", "        # BCD's recommended settings for ")
        old_src = re.sub(r'\bGameRecommendations\b', 'OldGameRecommendations', old_src)
        old_src = re.sub(r'\bGameRecommendation\b', 'OldGameRecommendation', old_src)
        (tmp / 'Old.swift').write_text(old_src)
        (tmp / 'Stubs.swift').write_text(STUBS)
        (tmp / 'Extras.swift').write_text(EXTRAS)
        (tmp / 'main.swift').write_text(MAIN)
        # The tool's signature, for CryptoKit to check.
        seed = hashlib.sha256(b'bcd-remote-recommendations-test-key').digest()
        (tmp / 'py.json').write_bytes(data)
        (tmp / 'committed.json').write_bytes(data)
        (tmp / 'committed.sig').write_bytes(sig_bytes)
        (tmp / 'py.json.sig').write_bytes(tool.signature_file(tool.sign(seed, data)))
        (tmp / 'work').mkdir()
        binary = tmp / 'bcd-remote-recs-check'
        built = subprocess.run([compiler] + [str(APP / n) for n in ('GameRecommendations.swift', 'GameRecommendationsList.swift',
                                                                    'GameRecommendationsSource.swift', 'ConfigCatalog.generated.swift')]
                               + [str(tmp / n) for n in ('Old.swift', 'Stubs.swift', 'Extras.swift', 'main.swift')]
                               + ['-o', str(binary)], capture_output=True, text=True)
        check('the fixture compiles (the app\'s files, the literals as OldGameRecommendations)', built.returncode == 0,
              built.stderr[-4000:])
        if built.returncode == 0:
            env = dict(os.environ, BCD_RECS_JSON=str(JSON_PATH), BCD_RECS_WORK=str(tmp / 'work'),
                       BCD_RECS_PY_DATA=str(tmp / 'py.json'), BCD_RECS_PY_SIG=str(tmp / 'py.json.sig'),
                       BCD_RECS_PY_KEY=base64.b64encode(tool.public_key(seed)).decode(),
                       BCD_RECS_COMMITTED_JSON=str(tmp / 'committed.json'), BCD_RECS_COMMITTED_SIG=str(tmp / 'committed.sig'))
            run = subprocess.run([str(binary)], capture_output=True, text=True, env=env, timeout=600)
            print(run.stdout.strip())
            if run.stderr.strip():
                print(run.stderr.strip()[-2000:])
            check('the Swift fixture passes', run.returncode == 0)
            check('the Swift side agrees about the committed signature',
                  ('COMMITTED-SIG: valid' in run.stdout) == signed)

if failures:
    print(f'check-remote-recommendations: {len(failures)} FAILED')
    raise SystemExit(1)
print('check-remote-recommendations: all passed')
