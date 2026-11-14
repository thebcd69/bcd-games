#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Madeira contributors
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Modified by BCD (thebcd69), 2026-11-05: Madeira's check-ea-store.py for BCD's files and hooks (SwiftEA/, BCDEA/, BCDStores/), with BCD's rules for an
# experimental store (off until the player turns it on), the files that must stay Madeira's byte for byte, the EA constants, and the registry writer and
# BCD's machine choice compiled and run.
# Portions copyright 2026 ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (pull request #523)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""Host checks for the experimental EA store in BCD (docs/EA.md). Never contacts EA.

Source checks: licence headers; the files Madeira wrote that BCD did not touch are Madeira's byte for byte; every EA source in the Xcode project;
the device-only Keychain item; no token or code in a log line; the store OFF unless env.BCD_EA is set, and every hook behind that switch (nothing
read, fetched, written or listened to while it is off); the start path's one guard; the EA app's environment list and the one that is cleared;
the local service's port, loopback and answers; the EA constants as Madeira has them; the preview build never compiling an EA type; the docs.

Swift (EARegistry.swift, BCDEAMachine.swift with BCDGPUCards.swift, compiled on the host): the registry writer's lines and escapes, both registry
views, a value that follows the install folder rewritten and any other kept, a second write changing nothing; and the machine BCD's launch reports
(the game's gpu-card line, the NVIDIA switch and line, AVX 1 and 2, the classic CPUID).

  check-ea.py [--require-swift]
"""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failures = 0

BCD_HEADER = ('// SPDX-License-Identifier: GPL-3.0-only\n'
              '// Copyright 2026 thebcd69 (BCD)\n'
              '// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n')


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def code_of(text):
    out = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    out = re.sub(r'//[^\n]*', '', out)
    return re.sub(r'"(?:\\.|[^"\\\n])*"', '""', out)


def function_body(text, signature):
    p = text.find(signature)
    if p < 0:
        return None
    a = text.index('{', p)
    n, b = 1, a + 1
    while n and b < len(text):
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


# ------------------------------------------------------------------ the files
ea_dir = app / 'SwiftEA'
madeira_files = ['EAAPI.swift', 'EADownloader.swift', 'EAFormats.swift', 'EALibrary.swift', 'EALicense.swift', 'EAService.swift']
modified = {'EAAPI.swift', 'EAFormats.swift', 'EALibrary.swift', 'EAService.swift'}
written = [app / 'BCDEA/BCDEAMachine.swift', app / 'BCDEA/BCDEAMachineLive.swift', app / 'BCDEA/BCDLiveStoreEA.swift', app / 'BCDEA/BCDEAViews.swift',
           app / 'BCDStores/BCDStoreCards.swift']
project = (app.parent / 'Madeira.xcodeproj/project.pbxproj').read_text()

# Madeira's own files that BCD did not change are Madeira's, byte for byte (Madeira 5fda6a0, v0.2.0).
verbatim = {
    'SwiftEA/EADownloader.swift': '0b68e329b552918d59afa4d6aa636edf07109f00cb473b3eca84cd171b5317d3',
    'SwiftEA/EALicense.swift': 'd223083b0f1d7daff2ff035bce3cc5bb1632a4e7626862548cdb300202cc7686',
}
for name, digest in verbatim.items():
    require(hashlib.sha256((app / name).read_bytes()).hexdigest() == digest, f'{name}: byte for byte Madeira\'s (5fda6a0)')

for name in madeira_files + ['EARegistry.swift']:
    path = ea_dir / name
    require(path.exists(), f'{name}: exists')
    text = path.read_text()
    owner = '125hz' if name == 'EARegistry.swift' else 'Madeira contributors'
    require(text.startswith(f'// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright 2026 {owner}\n// Madeira Converter Exception: see LICENSE-EXCEPTION.md\n'),
            f'{name}: Madeira\'s licence header ({owner})')
    require('BCD Material' not in text, f'{name}: not BCD Material (it holds Madeira\'s code)')
    if name in modified or name == 'EARegistry.swift':
        require('// Modified by BCD (thebcd69), 2026-11-05:' in text, f'{name}: marked as modified, with the date')
    else:
        require('Modified by BCD' not in text, f'{name}: unmodified')
for path in written:
    require(path.read_text().startswith(BCD_HEADER), f'{path.name}: BCD\'s licence header')
for path in sorted(ea_dir.glob('*.swift')) + written:
    name = path.name
    rel = str(path.relative_to(app))
    built = re.search(r'/\* ' + re.escape(rel) + r' in Sources \*/ = \{isa = PBXBuildFile; fileRef = (A2F7[345][0-9A-F]{3}) /\* ' + re.escape(name) + r' \*/; \};', project)
    require(built is not None and project.count(built.group(1)) == 3, f'{name}: built by the Xcode project (IDs A1F74xxx / A1F75xxx and A2...)')

# ------------------------------------------------------------------ the tokens, the logs
api = (ea_dir / 'EAAPI.swift').read_text()
require('kSecAttrAccessibleWhenUnlockedThisDeviceOnly' in api and 'kSecAttrSynchronizable as String: false' in api,
        'tokens: one device-only Keychain item, not synchronised')
require('private static let service = "bcd.ea.tokens"' in api, 'the Keychain item is bcd.ea.tokens')
for path in sorted(ea_dir.glob('*.swift')) + written + [app / 'BCDLiveStore.swift']:
    for line in path.read_text().splitlines():
        if 'EALog.event' in line or 'LogStore' in line:
            compact = line.replace(' ', '')
            bad = [w for w in ('accessToken', 'refreshToken', 'code', 'displayName', 'userID', 'personaID', 'accountName', 'opaque', 'signature', 'verifier')
                   if '\\(' + w in compact]
            if bad:
                require(False, f'{path.name}: no credential in a log line: {line.strip()[:80]}')
require(True, 'no token, code, name or ID in any EA log line')
for path in written:
    strings = re.findall(r'"((?:\\.|[^"\\\n])*)"', re.sub(r'//[^\n]*', '', path.read_text()))
    require(not any('Madeira' in s for s in strings), f'{path.name}: no "Madeira" in any text a person reads')
for path in (ea_dir / 'EAAPI.swift', ea_dir / 'EAFormats.swift', ea_dir / 'EALibrary.swift', ea_dir / 'EADownloader.swift'):
    strings = re.findall(r'"((?:\\.|[^"\\\n])*)"', re.sub(r'//[^\n]*', '', path.read_text()))
    # EA's own query names and its user agent carry the old name by design: the constants stay as Madeira has them.
    shown = [s for s in strings if re.search(r'\bMadeira\b', s) and not s.startswith('Madeira (EA client)') and 'query Madeira' not in s]
    require(not shown, f'{path.name}: no "Madeira" in any message (EA\'s constants aside)')

# ------------------------------------------------------------------ off until the player turns it on
library = (ea_dir / 'EALibrary.swift').read_text()
require('static var enabled: Bool { MadeiraConfig.flag("BCD_EA", fallback: false) }' in library, 'env.BCD_EA: OFF by default')
hooks = (app / 'BCDEA/BCDLiveStoreEA.swift').read_text()
body = function_body(hooks, 'func eaAccountAction(')
require(body is not None and 'EALibrary.enabled' in body.split('{', 1)[1][:200], 'eaAccountAction: behind the switch')
for signature in ('func eaMerge(', 'func eaDownloads(', 'func eaAccounts(', 'func eaPlayEntry(', 'func ea(_ action', 'func eaRefresh(',
                  'func eaAppeared(', 'func eaBecameActive(', 'func eaNote(', 'func eaEntryID(', 'func eaCanRepair(', 'func eaOpened(', 'func eaInstallAdvice('):
    body = function_body(hooks, signature)
    require(body is not None and 'guard EALibrary.enabled' in body.split('{', 1)[1][:200], f'{signature.strip("(")}: behind EALibrary.enabled')
require('EALibrary.enabled ? EALibrary.shared.objectWillChange' in hooks, 'the game page\'s watch reads EALibrary only while it is on')
require('guard !bound, EALibrary.enabled' in hooks, 'the change signal binds to EALibrary only while it is on')
require('BCDEASignal.shared.subject.eraseToAnyPublisher()' in hooks and 'EALibrary' not in function_body(hooks, 'var eaChanges').replace('var eaChanges', ''),
        'the store\'s change publisher touches no EA model at start')
content = (app / 'ContentView.swift').read_text()
require('guard !EALibrary.enabled || EALibrary.shared.preLaunch(entry, retry: { startLibraryEntry(entry) }) else { return }' in content,
        'ContentView: the one start guard, behind the switch')
require(code_of(content).count('EALibrary') == 2, 'ContentView names EALibrary in that one guard only')
store = (app / 'BCDLiveStore.swift').read_text()
for needle in ('eaChanges,', 'eaAppeared()', 'eaBecameActive()', 'eaMerge(&games)', 'eaDownloads()', 'eaAccounts()', 'eaPlayEntry(game)',
               'if ea(.install, game.id) { return }', 'if ea(.update, game.id) { return }', 'if ea(.repair, game.id) { return }', 'if ea(.uninstall, game.id) { return }',
               'if ea(.pause, id) { return }', 'if ea(.resume, id) { return }', 'if ea(.cancel, id) { return }',
               'if eaAccountAction(signIn: true, front) { return }', 'if eaAccountAction(signIn: false, front) { return }', 'eaRefresh()'):
    require(needle in store, f'BCDLiveStore: {needle}')
require(re.search(r'case steam, epic, gog, itch, ea(, amazon)?, local', (app / 'BCDStore.swift').read_text()) is not None   # Porthole port, item 13
        and 'case .ea: return "EA games"' in (app / 'BCDStore.swift').read_text(),
        'BCDStoreFront: ea')
views = (app / 'BCDEA/BCDEAViews.swift').read_text()
require('MadeiraConfig.set("env.BCD_EA", on ? "1" : nil)' in views, 'the switch writes env.BCD_EA (removed when off)')
require('EALibrary.shared.switchedOff()' in views, 'switching off stops what runs')
require('static func clearEnvironmentIfSet' not in library and 'func clearEnvironmentIfSet()' in library and 'guard environmentSet else { return }' in library,
        'a start of any other game clears the EA environment only if an EA start set it')
live = (app / 'BCDGamePageLive.swift').read_text()
for needle in ('BCDLiveStore.shared.eaEntryID(game.id)', 'BCDLiveStore.shared.eaNote(game.id)', 'BCDLiveStore.shared.eaOpened(game.id)',
               'BCDLiveStore.shared.eaDetailChanges', 'BCDLiveStore.shared.eaCanRepair(game.id)'):
    require(needle in live, f'the game page: {needle}')
require("game?.store == .ea ? \"EA's current build\" : \"Steam\"" in (app / 'BCDGamePage.swift').read_text(), 'the game page: Check files says whose build it compares with')
bound = {n for found in re.findall(r'^ENGINE_BOUND\s*\|?=\s*\{([^}]*)\}', (root / 'tools/ui-preview.py').read_text(), re.M)
         for n in re.findall(r'"([^"]+\.swift)"', found)}
leaks = [p.name for p in sorted(app.glob('BCD*.swift')) if p.name not in bound
         and re.search(r'\b(EALibrary|EAAPI|EAService|EAFormats|EAHardware\w*|EARegistry\w*|BCDEA\w+|BCDStoreCards|BCDStoresUI)\b', code_of(p.read_text()))]
require(not leaks, 'the preview build compiles no EA type' + (f' (found in {", ".join(leaks)})' if leaks else ''))
named = [p.relative_to(app) for d in ('SwiftSteam', 'BCDGOG', 'BCDEpic', 'BCDContent', 'SwiftItch') for p in sorted((app / d).glob('**/*.swift'))
         if re.search(r'\bEA(Library|API|Service|Formats|Hardware)\w*|\bBCDEA\w*', code_of(p.read_text()))]
require(not named, 'Steam\'s, GOG\'s, Epic\'s, itch.io\'s and the content engine\'s files name nothing of EA' + (f' (found in {named})' if named else ''))
ea_code = '\n'.join(p.read_text() for p in sorted(ea_dir.glob('*.swift')))
require(re.search(r'\b(Epic|GOG|Itch|Steam)[A-Z]\w*', code_of(ea_code)) is None, 'the EA code stands on its own (no other store\'s types)')
require(re.search(r'\bBCD\w*\b', code_of(ea_code)) is None or set(re.findall(r'\bBCD\w*\b', code_of(ea_code))) <= {'BCDEAMachine'},
        'the EA code names one BCD type only: BCDEAMachine (the machine a launch reports)')

# ------------------------------------------------------------------ the environment, the service
names = re.search(r'static let environmentNames = \[(.*?)\]', library, re.S)
require(names is not None, 'EALibrary names the EA environment')
if names:
    listed = set(re.findall(r'"([A-Za-z]+)"', names.group(1)))
    block = re.search(r'let environment: \[String: String\] = \[(.*?)\n        \]', library, re.S)
    used = set(re.findall(r'"([A-Za-z]+)": ', block.group(1))) if block else set()
    require(block is not None and used == listed, f'the launch environment and the cleared list are the same ({len(listed)} names)')
    for name in ('EALsxPort', 'EARtPLaunchCode', 'ContentId', 'EALaunchUserAuthToken', 'EAAccessTokenJWS', 'EALicenseToken', 'EAGenericAuthToken'):
        require(name in listed, f'environment: {name}')
require('"EALsxPort": String(EAService.port)' in library, 'the game is told the service port BCD listens on')
service = (ea_dir / 'EAService.swift').read_text()
require('static let port: UInt16 = 3216' in service, 'the service listens on the EA app\'s port')
require('inet_addr("127.0.0.1")' in service, 'the service listens on the loopback only')
for name in ('ChallengeResponse', 'GetConfig', 'GetProfile', 'GetSetting', 'GetInternetConnectedState', 'GetGameInfo', 'GetAllGameInfo',
             'GetAuthCode', 'RequestLicense', 'QueryEntitlements', 'GetPresence', 'SetPresence', 'GetBlockList', 'QueryFriends',
             'QueryPresence', 'QueryOffers', 'QueryImage', 'GetVoipStatus', 'IsProgressiveInstallationAvailable', 'AreChunksInstalled'):
    require(f'case "{name}"' in service, f'service answers {name}')
licence = (ea_dir / 'EALicense.swift').read_text()
require('ProgramData/Electronic Arts/EA Services/License' in licence, 'licences go where the game reads them')
require('renewAfter: TimeInterval = 14 * 24 * 3600' in licence, 'licences are renewed after two weeks')

# ------------------------------------------------------------------ the EA constants, as Madeira has them
formats = (ea_dir / 'EAFormats.swift').read_text()
constants = [
    ('EAAPI.swift', 'static let clientID = "JUNO_PC_CLIENT"'),
    ('EAAPI.swift', 'private static let clientSecret = "4mRLtYMb6vq9qglomWEaT4ChxsXWcyqbQpuBNfMPOYOiDmYYQmjuaBsF2Zp0RyVeWkfqhE9TuGgAw7te"'),
    ('EAAPI.swift', 'static let redirect = "qrc:///html/login_successful.html"'),
    ('EAAPI.swift', 'static let userAgent = "Madeira (EA client)"'),
    ('EAAPI.swift', 'private static let accounts = "https://accounts.ea.com/connect"'),
    ('EAAPI.swift', 'private static let serviceLayer = "https://service-aggregation-layer.juno.ea.com/graphql"'),
    ('EAAPI.swift', 'static let licenses = "https://proxy.novafusion.ea.com/licenses"'),
    ('EAAPI.swift', 'request.setValue("Origin Online Activation", forHTTPHeaderField: "X-Requester-Id")'),
    ('EAAPI.swift', 'request.setValue("EACTransaction", forHTTPHeaderField: "User-Agent")'),
    ('EAAPI.swift', 'URLQueryItem(name: "sbiod_enabled", value: "false")'),
    ('EAAPI.swift', 'URLQueryItem(name: "code_challenge_method", value: "S256")'),
    ('EAAPI.swift', 'query MadeiraOwnedGames($next: String, $limit: Int)'),
    ('EAFormats.swift', 'static let key: [UInt8] = [65, 50, 114, 45, 208, 130, 239, 176, 220, 100, 87, 197, 118, 104, 202, 9]'),
    ('EAFormats.swift', 'static let fixedKey: [UInt8] = Array(0..<16)'),
    ('EAFormats.swift', '"v1": Data("ISa3dpGOc8wW7Adn4auACSQmaccrOyR2".utf8), "v2": Data("nt5FfJbdPzNcl2pkC3zgjO43Knvscxft".utf8)'),
    ('EAFormats.swift', 'let time = (104_729 &* year) ^ (month &* 224_737) ^ (day &* 350_377)'),
    ('EAFormats.swift', 'var cpuEAX: UInt32 = 0x000A_0661'),
    ('EAFormats.swift', 'var cpuECX: UInt32 = 0x82D8_230F'),
    ('EAFormats.swift', 'var cpuEDX: UInt32 = 0x2F8B_FBF3'),
    ('EAFormats.swift', 'var boardManufacturer = "Apple Inc."'),
    ('EAFormats.swift', 'var cpuBrand = "Unknown ARM CPU"'),
    ('EAService.swift', 'private static let challengeVersion = "10,5,30,15625"'),
    ('EAService.swift', 'static let languages = "ar_SA,de_DE,en_US,es_ES,es_MX,fr_FR,it_IT,ja_JP,ko_KR,pl_PL,pt_BR,ru_RU,zh_CN,zh_TW"'),
    ('EALibrary.swift', 'scopes: ["basic.commerce.cartv2", "service.atom", "dp.client.default", "signin", "social_recommendation_user",'),
    ('EALibrary.swift', 'tokenFormat: "OPAQUE", expiresIn: 550'),
]
for name, text in constants:
    require(text in (ea_dir / name).read_text(), f'EA constant kept as Madeira has it ({name}): {text[:60]}')

# No ICU date formatter anywhere in the EA code (tests/host/check-bcd-game-page.py forbids them in the whole app: one killed BCD in tester logs).
icu = re.compile(r'DateFormatter|\.formatted\(|RelativeDateTimeFormatter|DateIntervalFormatter|DateComponentsFormatter|\.dateFormat\b|\.dateStyle\b|\.timeStyle\b')
require(not [p.name for p in sorted(ea_dir.glob('*.swift')) + written if icu.search(code_of(p.read_text()))], 'no ICU date formatter in the EA code')

# ------------------------------------------------------------------ the docs and notices
docs = (root / 'docs/EA.md').read_text()
require('Maxima' in docs and 'GPL-3.0' in docs, 'docs credit Maxima')
require('tests/host/check-ea-formats.py' in docs and 'tests/host/check-ea.py' in docs, 'docs name both checks')
require('env.BCD_EA' in docs and 'It is off.' in docs, 'docs say it is off and name the switch')
require('SwiftEA' in (root / 'THIRD-PARTY-NOTICES.md').read_text(), 'THIRD-PARTY-NOTICES.md names SwiftEA')

# ------------------------------------------------------------------ compiled Swift
checks = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
func value(_ v: EARegistryValue) -> String { EARegistry.line(v) }

@main
struct Checks {
    static func main() throws {
        // The registry writer: Wine's own spelling.
        func s(_ key: String, _ name: String, _ text: String, follows: Bool = false) -> EARegistryValue {
            EARegistryValue(hive: .machine, key: key, name: name, data: .string(text), followsInstall: follows)
        }
        require(value(s("Software\\X", "Install Dir", "C:\\Program Files\\EA Games\\X\\")) == "\"Install Dir\"=\"C:\\\\Program Files\\\\EA Games\\\\X\\\\\"",
                "a path: backslashes doubled, as wineserver writes it")
        require(value(s("Software\\X", "", "a\"b")) == "@=\"a\\\"b\"", "the default value, a quote escaped")
        require(value(s("Software\\X", "N", "é")) == "\"N\"=\"\\xe9\"", "non-ASCII as \\x of the UTF-16 unit")
        require(value(EARegistryValue(hive: .machine, key: "Software\\X", name: "D", data: .dword(255), followsInstall: false)) == "\"D\"=dword:000000ff", "a dword")
        require(EARegistry.keys(.machine, "Software\\Electronic Arts\\Origin") == ["Software\\Electronic Arts\\Origin", "Software\\Wow6432Node\\Electronic Arts\\Origin"],
                "HKLM\\Software: both registry views")
        require(EARegistry.keys(.machine, "Software\\Wow6432Node\\A") == ["Software\\Wow6432Node\\A"] && EARegistry.keys(.user, "Software\\A") == ["Software\\A"],
                "a Wow6432Node key and a user key: one view")

        let base = "WINE REGISTRY Version 2\n;; All keys relative to \\\\Machine\n\n[Software\\\\Electronic Arts\\\\Origin] 1000\n\"ClientPath\"=\"C:\\\\old.exe\"\n\n[Software\\\\Other] 5\n\"A\"=\"1\"\n"
        let values = [
            s("Software\\Electronic Arts\\EA Desktop", "InstallSuccessful", "true"),
            s("Software\\Electronic Arts\\Origin", "InstallSuccessful", "true"),
            s("Software\\Electronic Arts\\Origin", "ClientPath", "C:\\Windows\\System32\\conhost.exe"),
            s("Software\\BioWare\\Mass Effect", "Install Dir", "C:\\Program Files\\EA Games\\Mass Effect\\", follows: true),
        ]
        let first = EARegistry.write(values, in: base, now: 2000)
        require(first.text.contains("[Software\\\\Electronic Arts\\\\EA Desktop] 2000\n\"InstallSuccessful\"=\"true\""), "a missing key is added with its value")
        require(first.text.contains("[Software\\\\Wow6432Node\\\\Electronic Arts\\\\EA Desktop] 2000"), "and in the 32-bit view")
        require(first.text.contains("[Software\\\\Electronic Arts\\\\Origin] 1000\n\"ClientPath\"=\"C:\\\\old.exe\"\n\"InstallSuccessful\"=\"true\""),
                "a missing value joins its key, a value that is there stays (ClientPath)")
        require(first.text.contains("\"Install Dir\"=\"C:\\\\Program Files\\\\EA Games\\\\Mass Effect\\\\\""), "the game's install folder")
        require(first.text.contains("[Software\\\\Other] 5\n\"A\"=\"1\"\n"), "other keys are untouched")
        let again = EARegistry.write(values, in: first.text, now: 3000)
        require(again.changed == 0 && again.text == first.text, "a second write changes nothing")
        let moved = EARegistry.write([s("Software\\BioWare\\Mass Effect", "Install Dir", "C:\\Program Files\\EA Games\\Elsewhere\\", follows: true)], in: first.text, now: 4000)
        require(moved.changed == 2 && moved.text.contains("\\\\Elsewhere\\\\") && !moved.text.contains("\\\\Mass Effect\\\\\\\\\"") , "a path that follows the install folder is corrected (both views)")
        let kept = EARegistry.write([s("Software\\BioWare\\Mass Effect", "Install Dir", "C:\\Other\\")], in: first.text, now: 5000)
        require(kept.changed == 0, "a value that does not follow the install folder is never replaced")

        // The file write: both files, the backup once.
        let prefix = FileManager.default.temporaryDirectory.appendingPathComponent("ea-registry-\(ProcessInfo.processInfo.processIdentifier)", isDirectory: true)
        try FileManager.default.createDirectory(at: prefix, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: prefix) }
        try Data(base.utf8).write(to: prefix.appendingPathComponent("system.reg"))
        let changed = try EARegistry.write(values, prefix: prefix)
        let written = try String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)
        require(changed > 0 && written.contains("InstallSuccessful"), "system.reg is written")
        require(FileManager.default.fileExists(atPath: prefix.appendingPathComponent("system.reg.bcd-bak").path), "a one-time .bcd-bak copy is kept")
        let none = FileManager.default.temporaryDirectory.appendingPathComponent("ea-registry-none-\(ProcessInfo.processInfo.processIdentifier)", isDirectory: true)
        try FileManager.default.createDirectory(at: none, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: none) }
        let nothing = try EARegistry.write(values, prefix: none)
        require(nothing == 0, "a prefix with no registry file yet: nothing written")

        // The machine BCD's launch reports.
        var lines: [String: String] = [:]
        func choice(nvidia: Bool = false, avx: Bool = false) -> BCDEAMachine.Choice {
            BCDEAMachine.choose(line: { lines[$0] }, nvidiaSwitch: nvidia, avxSwitch: avx)
        }
        var c = choice()
        require(c.vendor == 0x106B && c.device == 0x0001 && !c.avx && !c.classicCPUID, "no choice: Apple's GPU 106B:0001, no AVX, plain CPUID")
        c = choice(nvidia: true)
        require(c.vendor == 0x10DE && c.device == 0x2544, "the NVIDIA switch: 10DE:2544")
        lines["env.DXMT_ENABLE_NVEXT"] = "1"
        c = choice()
        require(c.vendor == 0x10DE && c.device == 0x2544, "env.DXMT_ENABLE_NVEXT = 1 in the game's lines: 10DE:2544")
        lines["env.DXMT_ENABLE_NVEXT"] = "0"
        require(choice().vendor == 0x106B, "env.DXMT_ENABLE_NVEXT = 0: Apple's")
        lines["gpu-card"] = "amd"
        c = choice(nvidia: true)
        require(c.vendor == 0x1002 && c.device == 0x73DF, "a gpu-card line wins over the NVIDIA switch (AMD RX 6700 XT 1002:73DF)")
        lines["gpu-card"] = "amd-rx580"
        require(choice().device == 0x67DF, "another card: the RX 580")
        lines["gpu-card"] = "no-such-card"
        require(choice().vendor == 0x106B, "a card this build does not know: the game sees what it always saw")
        lines["gpu-card"] = "apple"
        c = choice(nvidia: true)
        require(c.vendor == 0x106B && c.device == 0x0001, "the Apple card chosen on purpose turns the NVIDIA switch off for the game")
        lines = [:]
        require(choice(avx: true).avx, "the AVX switch")
        lines["env.MADEIRA_FEX_AVX"] = "1"; require(choice().avx, "env.MADEIRA_FEX_AVX = 1")
        lines["env.MADEIRA_FEX_AVX"] = "2"; require(choice().avx, "env.MADEIRA_FEX_AVX = 2 (AVX without AVX2)")
        lines["env.MADEIRA_FEX_AVX"] = "0"; require(!choice().avx, "env.MADEIRA_FEX_AVX = 0")
        lines["env.MADEIRA_FEX_CPUID_CLASSIC"] = "1"
        require(choice().classicCPUID && BCDEAMachine.classicEDXBits == 0xC, "env.MADEIRA_FEX_CPUID_CLASSIC = 1: DE and PSE (EDX bits 2 and 3)")
        lines["env.MADEIRA_FEX_CPUID_CLASSIC"] = "2"
        require(!choice().classicCPUID, "any other value: plain")

        // Dates from numbers: no ICU date formatter in the app (EAClock), the same answers as ISO8601DateFormatter's on the texts a licence has.
        let moment = Date(timeIntervalSince1970: 1793881845)
        require(EAClock.parse("2026-11-05T12:30:45Z") == moment, "a UTC time")
        require(EAClock.parse("2026-11-05T12:30:45.500Z") == moment.addingTimeInterval(0.5), "with a fraction")
        require(EAClock.parse("2026-11-05T15:30:45+03:00") == moment && EAClock.parse("2026-11-05T07:30:45-0500") == moment, "with a zone offset")
        require(EAClock.parse("2026-11-05 12:30:45") == moment && EAClock.parse(" 2026-11-05T12:30:45 ") == moment, "no zone is UTC; a space or padding is fine")
        require(EAClock.parse("2024-02-29T00:00:00Z") != nil && EAClock.parse("2025-02-29T00:00:00Z") == nil && EAClock.parse("2026-13-01T00:00:00Z") == nil
                && EAClock.parse("2026-11-05T25:00:00Z") == nil && EAClock.parse("garbage") == nil && EAClock.parse("") == nil
                && EAClock.parse("2026-11-05T12:30:45Zjunk") == nil && EAClock.parse("2026-11-05T12:30:45.Z") == nil, "leap day, a wrong day, month, hour, shape")
        require(EALicenseInfo.date("2026-11-05T12:30:45Z") == moment, "the licence's start time goes through it")
        let reference = ISO8601DateFormatter()   // a host check may build one; the app may not
        reference.formatOptions = [.withInternetDateTime]
        for text in ["1970-01-01T00:00:00Z", "1999-12-31T23:59:59Z", "2000-02-29T12:00:00Z", "2026-11-05T12:30:45Z", "2038-01-19T03:14:07Z", "2100-03-01T00:00:00Z"] {
            require(EAClock.parse(text) == reference.date(from: text), "same as ISO8601DateFormatter: \(text)")
        }
        require(EAClock.utcText(moment) == "2026-11-05T12:30:45" && EAClock.utcText(Date(timeIntervalSince1970: 0)) == "1970-01-01T00:00:00"
                && EAClock.utcText(Date(timeIntervalSince1970: -86_400)) == "1969-12-31T00:00:00" && EAClock.utcText(Date(timeIntervalSince1970: .infinity)) == "1970-01-01T00:00:00",
                "the service's system time text, before 1970 and for a value that is no date")
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        var state: UInt64 = 7
        var agree = true
        for _ in 0..<5000 {
            state = state &* 6364136223846793005 &+ 1442695040888963407
            let seconds = Double(Int64(bitPattern: state >> 20) % 8_000_000_000 - 2_000_000_000)
            let date = Date(timeIntervalSince1970: seconds)
            let c = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
            let expected = String(format: "%04d-%02d-%02dT%02d:%02d:%02d", c.year!, c.month!, c.day!, c.hour!, c.minute!, c.second!)
            if EAClock.utcText(date) != expected || EAClock.parse(expected) != Date(timeIntervalSince1970: seconds.rounded(.down)) { agree = false; print("differs at \(seconds): \(EAClock.utcText(date)) vs \(expected)"); break }
        }
        require(agree, "5000 moments from 1906 to 2223: the text, and back, agree with the calendar")
        if failures > 0 { print("FAILURES: \(failures)"); exit(1) }
        print("PASS: all EA Swift checks")
    }
}
'''

with tempfile.TemporaryDirectory(prefix='bcd-ea-') as tmp:
    tmp = Path(tmp)
    (tmp / 'checks.swift').write_text(checks)
    if not Path(SWIFTC).exists() and not shutil.which(SWIFTC):
        if '--require-swift' in sys.argv:
            require(False, 'Swift compiler available (--require-swift)')
        else:
            print('SKIP: Swift compiler unavailable; the compiled EA checks did not run')
    else:
        exe = tmp / 'check'
        build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', '-o', str(exe),
                                str(app / 'SwiftEA/EARegistry.swift'), str(app / 'SwiftEA/EAFormats.swift'), str(app / 'BCDEA/BCDEAMachine.swift'), str(app / 'BCDGPUCards.swift'), str(tmp / 'checks.swift')])
        require(build.returncode == 0, 'EARegistry, EAFormats, BCDEAMachine and BCDGPUCards compile on the host')
        if build.returncode == 0:
            run = subprocess.run([str(exe)])
            require(run.returncode == 0, 'EA registry and machine checks pass')

if failures:
    print(f'FAILURES: {failures}')
    sys.exit(1)
print('PASS: EA store')
