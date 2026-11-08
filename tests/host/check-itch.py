#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 ft7t8drgwf-wq
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Modified by BCD (thebcd69), 2026-11-05: Madeira's check-itch-store.py for BCD's files and hooks (SwiftItch/, BCDItch/, BCDStores/); the compiled Swift
# half runs the real SwiftItch sources (ItchInstall.swift holds what Madeira's check cut out of ItchLibrary.swift) and the installer folder is .bcd-setup.
"""Host checks for the itch.io store in BCD (docs/ITCH.md). Never contacts itch.io.

Source checks: licence headers (Madeira's files keep theirs, BCD's carry BCD's), every itch.io source in the Xcode project, the .7z reader's
header in the bridging header, the device-only Keychain item for the API key, no key in a log line, itch.io inert until the player has signed
in (every hook of BCDLiveStoreItch behind ItchLibrary.active, the switch env.BCD_ITCH defaulting on, no Keychain/file/network at start), the
hooks in BCDLiveStore, the game page, the Library and the accounts section, the preview build never compiling an itch.io type, the docs.

Swift (ItchAPI.swift, ItchArchive.swift and ItchInstall.swift compiled on the host): itch.io's answers in snake_case, camelCase and the old
traits form; which upload installs; which program starts; installers and their batch files; the zip unpacker on archives made here with Python's
zipfile: stored and deflated entries over several 1 MB pieces, ZIP64, a damaged CRC, names that leave the folder, two names that differ only in
case, a symbolic link, a truncated archive, and the old install folder kept when an unpack fails.

  check-itch.py [--require-swift]
"""
from pathlib import Path
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

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
    """The source without comments and without string contents (enough for the guards below)."""
    out = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    out = re.sub(r'//[^\n]*', '', out)
    return re.sub(r'"(?:\\.|[^"\\\n])*"', '""', out)


def function_body(text, signature):
    """The braces after `signature`, or None."""
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
derived = [app / 'SwiftItch' / n for n in ('ItchAPI.swift', 'ItchArchive.swift', 'ItchInstall.swift', 'ItchLibrary.swift', 'itch_7z.c', 'itch_7z.h')]
written = [app / 'BCDItch/BCDLiveStoreItch.swift', app / 'BCDItch/BCDItchViews.swift', app / 'BCDStores/BCDStoreCards.swift']
project = (app.parent / 'Madeira.xcodeproj/project.pbxproj').read_text()
for path in derived + written:
    require(path.exists(), f'{path.relative_to(app)}: exists')
# The .7z reader is Madeira's, byte for byte (Madeira 5fda6a0, v0.2.0).
import hashlib
for name, digest in {'itch_7z.c': 'bc1594823fbbce1d746618790fc39d07b12a436df316b6ad7ce6face97793b5b',
                     'itch_7z.h': '6d42ed0da937f5c4887b4e12efb9b8a95d49deebd62e84b16642bc02c79f64ae'}.items():
    require(hashlib.sha256((app / 'SwiftItch' / name).read_bytes()).hexdigest() == digest, f'{name}: byte for byte Madeira\'s (5fda6a0)')
for path in derived:
    text = path.read_text()
    if path.suffix == '.c' or path.suffix == '.h':
        require(text.startswith('// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright 2026 Madeira contributors\n'), f'{path.name}: Madeira\'s licence header')
    else:
        require(text.startswith('// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright 2026 ft7t8drgwf-wq\n// Madeira Converter Exception: see LICENSE-EXCEPTION.md\n'),
                f'{path.name}: Madeira\'s licence header')
        require('BCD Material' not in text, f'{path.name}: not BCD Material (it holds Madeira\'s code)')
    if path.suffix == '.swift':
        require('// Modified by BCD (thebcd69), 2026-11-05:' in text, f'{path.name}: marked as modified, with the date')
for path in written:
    if path.name == 'BCDItchViews.swift':   # holds Madeira code (#499): not BCD Material (LICENSE-BCD.md section 1), credited
        require(path.read_text().startswith(BCD_HEADER.split('// BCD Material')[0] + '// Portions copyright 2026 ft7t8drgwf-wq'),
                f'{path.name}: BCD\'s copyright and the Madeira credit, no BCD Material line')
    else:
        require(path.read_text().startswith(BCD_HEADER), f'{path.name}: BCD\'s licence header')
for path in derived + written:
    name = path.name
    if path.suffix == '.h':
        continue
    built = re.search(r'/\* ' + re.escape(str(path.relative_to(app))) + r' in Sources \*/ = \{isa = PBXBuildFile; fileRef = (A2F7[35][0-9A-F]{3}) /\* ' + re.escape(name) + r' \*/; \};', project)
    require(built is not None and project.count(built.group(1)) == 3, f'{name}: built by the Xcode project (IDs A1F73xxx / A1F75xxx and A2...)')
require(re.search(r'A2F73006 /\* itch_7z\.h \*/ = \{isa = PBXFileReference; lastKnownFileType = sourcecode\.c\.h; path = "SwiftItch/itch_7z\.h"', project) is not None,
        'itch_7z.h is in the Xcode project')
require('#import "SwiftItch/itch_7z.h"' in (app / 'Madeira-Bridging-Header.h').read_text(), 'the .7z reader\'s header is in the bridging header')
require('liblzma.tbd' in project and 'libz.tbd' in project, 'the project links liblzma and zlib (the reader declares liblzma\'s few functions itself)')

# ------------------------------------------------------------------ the key, the logs
api = (app / 'SwiftItch/ItchAPI.swift').read_text()
require('kSecAttrAccessibleWhenUnlockedThisDeviceOnly' in api and 'kSecAttrSynchronizable as String: false' in api,
        'the API key: one device-only Keychain item, not synchronised')
require('private static let service = "bcd.itch.key"' in api, 'the Keychain item is bcd.itch.key')
require('https://api.itch.io' in api and 'api_key' not in api, 'the key goes in the Authorization header, never in an address')
require('downloadKey == 0 ? []' in api, 'a free game is asked for without a key')
require('"Madeira"' not in api and api.count('"BCD", forHTTPHeaderField: "User-Agent"') == 3, 'the User-Agent is BCD\'s')
for path in derived + written + [app / 'BCDLiveStore.swift']:
    if path.suffix != '.swift':
        continue
    for line in path.read_text().splitlines():
        if 'ItchLog.event' in line or 'LogStore' in line:
            compact = line.replace(' ', '')
            bad = [w for w in ('key', 'candidate', 'raw', 'accountName', 'request', 'url', 'entered') if '\\(' + w in compact]
            require(not bad, f'{path.name}: no key or account data in a log line: {line.strip()[:80]}')
for path in written:
    # What a person reads in BCD's own views and hooks never says Madeira.
    strings = re.findall(r'"((?:\\.|[^"\\\n])*)"', re.sub(r'//[^\n]*', '', path.read_text()))
    require(not any('Madeira' in s for s in strings), f'{path.name}: no "Madeira" in any text a person reads')
for path in (app / 'SwiftItch/ItchAPI.swift', app / 'SwiftItch/ItchInstall.swift', app / 'SwiftItch/ItchLibrary.swift', app / 'SwiftItch/ItchArchive.swift'):
    strings = re.findall(r'"((?:\\.|[^"\\\n])*)"', re.sub(r'//[^\n]*', '', path.read_text()))
    require(not [s for s in strings if re.search(r'\bMadeira\b', s)], f'{path.name}: no "Madeira" in any message')

# ------------------------------------------------------------------ inert until the player starts it
library = (app / 'SwiftItch/ItchLibrary.swift').read_text()
require('MadeiraConfig.flag("BCD_ITCH", fallback: true)' in library, 'env.BCD_ITCH: on by default (0 hides it)')
require('static var active: Bool { enabled && FileManager.default.fileExists(atPath: support.path) }' in library,
        'ItchLibrary.active: switched on and begun (the folder exists)')
require('try? FileManager.default.createDirectory(at: Self.support, withIntermediateDirectories: true)' in function_body(library, 'func signIn(key: String)') ,
        'signing in makes the folder (itch.io is "begun" from then on)')
hooks = (app / 'BCDItch/BCDLiveStoreItch.swift').read_text()
for signature in ('func itchMerge(', 'func itchDownloads(', 'func itchAccounts(', 'func itchPlayEntry(', 'func itch(_ action', 'func itchRefresh(',
                  'func itchAppeared(', 'func itchBecameActive(', 'func itchNote(', 'func itchEntryID(', 'func itchOpened(', 'func itchInstallAdvice('):
    body = function_body(hooks, signature)
    require(body is not None and 'guard ItchLibrary.active' in body.split('{', 1)[1][:200], f'{signature.strip("(")}: behind ItchLibrary.active')
for signature in ('func itchAccountAction(', 'func presentItchFind(', 'func presentItchKey('):
    body = function_body(hooks, signature)
    require(body is not None and 'ItchLibrary.enabled' in body.split('{', 1)[1][:200], f'{signature.strip("(")}: behind the switch')
require('ItchLibrary.active ? ItchLibrary.shared.objectWillChange' in hooks, 'the game page\'s watch reads ItchLibrary only when active')
require('BCDItchSignal.shared.subject.eraseToAnyPublisher()' in hooks and 'ItchLibrary' not in function_body(hooks, 'var itchChanges').replace('var itchChanges', ''),
        'the store\'s change publisher touches no itch.io model at start')
store = (app / 'BCDLiveStore.swift').read_text()
for needle in ('itchChanges,', 'itchAppeared()', 'itchBecameActive()', 'itchMerge(&games)', 'itchDownloads()', 'itchAccounts()',
               'itchPlayEntry(game)', 'if itch(.install, game.id) { return }', 'if itch(.update, game.id) { return }', 'if itch(.uninstall, game.id) { return }',
               'if itch(.pause, id) { return }', 'if itch(.resume, id) { return }', 'if itch(.cancel, id) { return }',
               'if itchAccountAction(signIn: true, front) { return }', 'if itchAccountAction(signIn: false, front) { return }', 'itchRefresh()',
               'case itchKey, itchFind', 'func openStoreTool(', 'func present(_ sheet: BCDLiveSheet)'):
    require(needle in store, f'BCDLiveStore: {needle}')
require(re.search(r'case steam, epic, gog, itch(, ea)?, local', (app / 'BCDStore.swift').read_text()) is not None and 'case .itch: return "itch.io"' in (app / 'BCDStore.swift').read_text(),
        'BCDStoreFront: itch.io')
live = (app / 'BCDGamePageLive.swift').read_text()
for needle in ('BCDLiveStore.shared.itchEntryID(game.id)', 'BCDLiveStore.shared.itchNote(game.id)', 'BCDLiveStore.shared.itchOpened(game.id)',
               'BCDLiveStore.shared.itchDetailChanges', 'BCDLiveStore.shared.itchInstallAdvice(game.id)'):
    require(needle in live, f'the game page: {needle}')
require(re.search(r'game\.store == \.itch(?: \|\| game\.store == \.ea)?, let at', live) is not None
        and re.search(r'else if game\.store == \.itch(?: \|\| game\.store == \.ea)? \{', live) is not None,
        'the game page: an itch.io game has no Name row and is uninstalled through the store')
accounts = (app / 'BCDAccounts.swift').read_text()
require('var extraStoreCards: AnyView { get }' in accounts and 'backend.extraStoreCards' in accounts and 'backend.closeExtraStoreQuestion()' in accounts
        and 'extension BCDAccountsBackend' in accounts, 'Settings › Accounts: the extra cards, with defaults the preview app needs no change for')
require('var extraStoreCards: AnyView { AnyView(BCDStoreCards()) }' in (app / 'BCDAccountsLive.swift').read_text(), 'BCDAccountsLive brings the cards')
require('BCDItchKeySheet()' in (app / 'BCDLiveFrontEnd.swift').read_text() and 'BCDItchFindSheet()' in (app / 'BCDLiveFrontEnd.swift').read_text(),
        'the front end shows the key sheet and Find a game')
require('openStoreTool(.itch)' in (app / 'BCDLibraryView.swift').read_text() and 'filter == .store(.itch)' in (app / 'BCDLibraryView.swift').read_text(),
        'Library › itch.io: Find a game')
# The preview build compiles the top-level BCD*.swift files that are not engine-bound: none of them may name an itch.io type.
bound = {n for found in re.findall(r'^ENGINE_BOUND\s*\|?=\s*\{([^}]*)\}', (root / 'tools/ui-preview.py').read_text(), re.M)
         for n in re.findall(r'"([^"]+\.swift)"', found)}
leaks = [p.name for p in sorted(app.glob('BCD*.swift')) if p.name not in bound
         and re.search(r'\b(ItchLibrary|ItchAPI|ItchFormat|ItchArchive|ItchSetup|ItchProgram|BCDItch\w+|BCDStoreCards|BCDStoresUI)\b', code_of(p.read_text()))]
require(not leaks, 'the preview build compiles no itch.io type' + (f' (found in {", ".join(leaks)})' if leaks else ''))
# Neither Steam's, GOG's nor Epic's files name itch.io.
named = [p.relative_to(app) for d in ('SwiftSteam', 'BCDGOG', 'BCDEpic', 'BCDContent') for p in sorted((app / d).glob('**/*.swift'))
         if re.search(r'\bItch\w+|\bBCDItch\w+', code_of(p.read_text()))]
require(not named, 'Steam\'s, GOG\'s, Epic\'s and the content engine\'s files name nothing of itch.io' + (f' (found in {named})' if named else ''))
require('env.BCD_ITCH' in (root / 'docs/ITCH.md').read_text() or 'BCD_ITCH' in (root / 'docs/ITCH.md').read_text(), 'docs/ITCH.md names the switch')
docs = (root / 'docs/ITCH.md').read_text()
require('tests/host/check-itch.py' in docs and 'tests/host/check-itch-7z.py' in docs, 'docs name both checks')
notices = (root / 'THIRD-PARTY-NOTICES.md').read_text()
require('SwiftItch' in notices, 'THIRD-PARTY-NOTICES.md names SwiftItch')

# ------------------------------------------------------------------ compiled Swift
checks = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
func json(_ text: String) -> [String: Any] { try! ItchFormat.json(Data(text.utf8)) }

@main
struct Checks {
    static func main() throws {
        let work = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)

        // Owned keys: snake_case (the server), camelCase, the old traits; tools kept, other kinds and Mac-only games not.
        let page = json("""
        {"page":1,"per_page":50,"owned_keys":[
          {"id":11,"game_id":101,"game":{"id":101,"title":"Celeste ","classification":"game","cover_url":"https://img.itch.zone/c.png","url":"https://mattmakesgames.itch.io/celeste","platforms":{"windows":"all","osx":"all"}}},
          {"id":12,"gameId":102,"game":{"id":102,"title":"Tool","classification":"tool","traits":["p_windows"]}},
          {"id":13,"game_id":103,"game":{"id":103,"title":"Soundtrack","classification":"soundtrack","platforms":{"windows":"all"}}},
          {"id":14,"game_id":104,"game":{"id":104,"title":"Mac only","classification":"game","platforms":{"osx":"all"}}},
          {"id":15,"game_id":105,"game":{"id":105,"title":"No platforms listed","classification":"game","cover_url":"http://insecure/x.png"}}
        ]}
        """)
        let (games, keys) = ItchFormat.ownedKeys(page)
        require(keys == 5 && games.map(\.id) == [101, 102, 105], "owned keys: Windows games and tools, unlisted platforms kept (\(games.map(\.id)))")
        require(games[0].title == "Celeste" && games[0].downloadKeyID == 11 && games[0].cover?.host == "img.itch.zone"
                && games[0].url?.path == "/celeste", "a game's title, key, cover and page")
        require(games[2].cover == nil, "only https artwork")
        require((try? ItchFormat.json(Data(#"{"errors":["invalid key"]}"#.utf8))) == nil, "an error answer is an error")
        do { _ = try ItchFormat.json(Data(#"{"errors":["invalid key"]}"#.utf8)); require(false, "bad key") }
        catch ItchAPIError.badKey { require(true, "a refused key says so") } catch { require(false, "bad key: \(error)") }

        // Uploads and the choice.
        let uploads = ItchFormat.uploads(json("""
        {"uploads":[
          {"id":1,"filename":"celeste-linux.zip","type":"default","size":900,"platforms":{"linux":"all"}},
          {"id":2,"filename":"celeste-win32.zip","type":"default","size":500,"platforms":{"windows":"386"}},
          {"id":3,"filename":"celeste-win.zip","type":"default","size":400,"platforms":{"windows":"all"},"build_id":77,"build":{"id":77,"user_version":"1.4"}},
          {"id":4,"filename":"Celeste-setup.exe","type":"default","size":800,"platforms":{"windows":"all"}},
          {"id":5,"filename":"ost.zip","type":"soundtrack","size":9000,"platforms":{"windows":"all"}},
          {"id":6,"filename":"demo.zip","type":"default","size":9000,"traits":["p_windows","demo"]},
          {"id":7,"filename":"preorder.zip","type":"default","size":9000,"platforms":{"windows":"all"},"preorder":true}
        ]}
        """))
        require(uploads.count == 7 && uploads[2].buildID == 77 && uploads[2].version == "1.4" && uploads[5].demo && uploads[5].windows,
                "uploads: builds, versions, demo and Windows traits")
        require(ItchFormat.choose(uploads)?.id == 3, "the choice: the game's own Windows .zip, any architecture, before 32-bit, an installer, the soundtrack, a demo or a preorder")
        require(ItchFormat.choose(uploads.filter { $0.id != 3 })?.id == 4, "without it: the installer before the 32-bit build")
        require(ItchFormat.choose(uploads.filter { ![2, 3, 4].contains($0.id) })?.id == 6, "only a demo: the demo")
        require(ItchFormat.choose(uploads.filter { [1, 5, 7].contains($0.id) }) == nil, "no Windows game file: none")

        // Which program starts the game.
        require(ItchProgram.manifestAction("""
        [[prereqs]]
        name = "vcredist-2015-x64"
        [[actions]]
        name = "play"
        path = "bin\\\\Game.exe"
        """) == "bin/Game.exe", ".itch.toml: the first action's program")
        require(ItchProgram.manifestAction("[[actions]]\npath = \"../evil.exe\"\n") == nil, ".itch.toml: never outside the folder")
        require(ItchProgram.choose(["Game/Game.exe"], title: "x") == "Game/Game.exe", "the only program")
        require(ItchProgram.choose(["Celeste.exe", "Other.exe"], title: "Celeste") == "Celeste.exe", "the one named like the game")
        require(ItchProgram.choose(["a/b/Tool.exe", "Run.exe"], title: "Thing") == "Run.exe", "the one nearest the top")
        require(ItchProgram.choose(["A.exe", "B.exe"], title: "Thing") == nil, "two equally likely: none")
        require(ItchProgram.helper("unins000.exe") && ItchProgram.helper("UnityCrashHandler64.exe") && ItchProgram.helper("vc_redist.x64.exe")
                && !ItchProgram.helper("Game.exe"), "helpers are never the game")
        let folder = work.appendingPathComponent("prog", isDirectory: true)
        try FileManager.default.createDirectory(at: folder.appendingPathComponent("Game"), withIntermediateDirectories: true)
        for name in ["Game/Game.exe", "Game/UnityCrashHandler64.exe", "unins000.exe"] {
            FileManager.default.createFile(atPath: folder.appendingPathComponent(name).path, contents: Data("MZ".utf8))
        }
        require(ItchProgram.find(in: folder, title: "Whatever") == "Game/Game.exe", "find: the game's program, helpers aside")

        // Free games: collections, search and pasted links; no key.
        let free = ItchFormat.game(json(#"{"id":7,"title":"Free Thing","classification":"game","min_price":0,"platforms":{"windows":"all"},"user":{"username":"dev","display_name":"Dev"}}"#))
        require(free?.downloadKeyID == 0 && free?.owned == false && free?.minPrice == 0 && free?.author == "Dev", "a game object: no key, its price and author")
        require(ItchFormat.collections(json(#"{"collections":[{"id":5,"title":"A"},{"id":6}]}"#)) == [5, 6], "the account's collections")
        let collected = ItchFormat.collectionGames(json(#"{"page":1,"per_page":30,"collection_games":[{"game_id":8,"game":{"id":8,"title":"In a collection","platforms":{"windows":"all"}}},{"gameId":9,"game":{"id":9,"title":"Mac","platforms":{"osx":"all"}}}]}"#))
        require(collected.games.map(\.id) == [8] && collected.count == 2, "a collection's Windows games (snake_case and camelCase)")
        require(ItchFormat.searchGames(json(#"{"games":[{"id":10,"title":"Found","minPrice":500,"platforms":{"windows":"all"}},{"id":11,"title":"Book","classification":"book"}]}"#)).map(\.minPrice) == [500],
                "search: Windows games with their price")
        require(ItchFormat.dataURL("https://dev.itch.io/free-thing?x=1#y")?.absoluteString == "https://dev.itch.io/free-thing/data.json"
                && ItchFormat.dataURL("dev.itch.io/free-thing")?.absoluteString == "https://dev.itch.io/free-thing/data.json",
                "a pasted game link: its data.json")
        require(ItchFormat.dataURL("https://itch.io/games/free") == nil && ItchFormat.dataURL("https://api.itch.io/x") == nil
                && ItchFormat.dataURL("https://evil.com/dev.itch.io/x") == nil && ItchFormat.dataURL("https://dev.itch.io.evil.com/x") == nil
                && ItchFormat.dataURL("https://dev.itch.io/") == nil, "only an author's itch.io game page")
        require(ItchFormat.pageGameID(#"<meta name="itch:path" content="games/123456"/>"#) == 123456 && ItchFormat.pageGameID("<html>") == nil,
                "a game page's id from its itch:path tag")

        // Installers: sniffed like the itch app's, run unattended into the game's folder.
        func exe(_ marker: String) -> Data { Data("MZ".utf8) + Data(count: 4000) + Data(marker.utf8) + Data(count: 100) }
        require(ItchSetup.kind(fileName: "game_setup.exe", head: exe("Inno Setup Setup Data (6.2.0)")) == .inno, "Inno Setup")
        require(ItchSetup.kind(fileName: "x.exe", head: exe("NullsoftInst")) == .nsis, "NSIS")
        require(ItchSetup.kind(fileName: "Game.msi", head: Data()) == .msi
                && ItchSetup.kind(fileName: "x", head: Data([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])) == .msi, "MSI")
        require(ItchSetup.kind(fileName: "MyGame-Installer.exe", head: exe("nothing")) == .other, "another installer by its name")
        require(ItchSetup.kind(fileName: "MyGame.exe", head: exe("nothing")) == nil && ItchSetup.kind(fileName: "unins000.exe", head: exe("x")) == nil
                && ItchSetup.kind(fileName: "setup.txt", head: Data("hello".utf8)) == nil, "the game itself, an uninstaller and a text file are not installers")
        let inno = ItchSetup.script(kind: .inno, installer: "C:\\itch\\G\\.bcd-setup\\setup.exe", target: "C:\\itch\\G", title: "G")
        require(inno.contains("start \"\" /wait \"C:\\itch\\G\\.bcd-setup\\setup.exe\" /SP- /SILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /DIR=\"C:\\itch\\G\"")
                && inno.hasPrefix("@echo off\r\n") && inno.hasSuffix("echo ran>\"%~dp0ran\"\r\n"), "Inno: silent into the game's folder, CRLF, a mark once it ended")
        let nsis = ItchSetup.script(kind: .nsis, installer: "C:\\itch\\A & B\\s.exe", target: "C:\\itch\\A & B 100%", title: "A & B")
        require(nsis.contains("/S /D=C:\\itch\\A ^& B 100%%") && nsis.contains("\"C:\\itch\\A & B\\s.exe\""), "NSIS: /D= last, unquoted, specials escaped")
        let msi = ItchSetup.script(kind: .msi, installer: "C:\\itch\\G\\g.msi", target: "C:\\itch\\G", title: "G")
        require(msi.contains("msiexec /i \"C:\\itch\\G\\g.msi\" /qb /norestart TARGETDIR=\"C:\\itch\\G\""), "MSI: msiexec with a progress bar")
        require(!ItchSetup.script(kind: .other, installer: "C:\\s.exe", target: "C:\\t", title: "x").contains("/S"), "another installer runs as it is")
        require(ItchSetup.windowsPath("itch/My Game") == "C:\\itch\\My Game", "drive_c paths as Windows paths")

        let setupGame = work.appendingPathComponent("setup-game", isDirectory: true)
        let hidden = setupGame.appendingPathComponent(ItchSetup.folderName, isDirectory: true)
        try FileManager.default.createDirectory(at: hidden, withIntermediateDirectories: true)
        try exe("Inno Setup Setup Data").write(to: hidden.appendingPathComponent("game-setup.exe"))
        require(ItchProgram.find(in: setupGame, title: "Game") == nil, "the hidden setup folder is never the game")
        require(ItchSetup.installer(in: setupGame).map { "\($0.0.rawValue) \($0.1)" } == "inno .bcd-setup/game-setup.exe", "the downloaded installer")
        let zipped = work.appendingPathComponent("zipped-setup", isDirectory: true)
        try FileManager.default.createDirectory(at: zipped.appendingPathComponent("redist"), withIntermediateDirectories: true)
        try exe("NullsoftInst").write(to: zipped.appendingPathComponent("Setup.exe"))
        try exe("x").write(to: zipped.appendingPathComponent("readme.txt"))
        require(ItchProgram.find(in: zipped, title: "Game") == nil && ItchSetup.installer(in: zipped)?.1 == "Setup.exe", "an archive with one installer")

        let drive = work.appendingPathComponent("drive_c", isDirectory: true)
        let before = Date()
        for dir in ["Program Files (x86)/Cool Game", "Program Files/Common Files/Thing", "users/mobile/AppData/Local/Programs"] {
            try FileManager.default.createDirectory(at: drive.appendingPathComponent(dir), withIntermediateDirectories: true)
        }
        require(ItchSetup.installedElsewhere(drive: drive, title: "Cool Game", since: before) == "Program Files (x86)/Cool Game",
                "the folder an installer chose, named like the game")
        require(ItchSetup.installedElsewhere(drive: drive, title: "Other", since: before) == "Program Files (x86)/Cool Game",
                "the only new folder")
        require(ItchSetup.installedElsewhere(drive: drive, title: "Other", since: Date().addingTimeInterval(3600)) == nil, "nothing new: none")

        // The unpacker.
        func unpack(_ name: String) -> (Result<UInt64, Error>, URL) {
            let target = work.appendingPathComponent("out-" + name, isDirectory: true)
            return (Result { try ItchArchive.unpack(work.appendingPathComponent(name + ".zip"), to: target) }, target)
        }
        var reported: (UInt64, UInt64) = (0, 0)
        let good = work.appendingPathComponent("out-good", isDirectory: true)
        let bytes = try ItchArchive.unpack(work.appendingPathComponent("good.zip"), to: good) { reported = ($0, $1) }
        let expected = try Data(contentsOf: work.appendingPathComponent("expected-big.bin"))
        require((try? Data(contentsOf: good.appendingPathComponent("Game/Data/big.bin"))) == expected, "deflated entry over several pieces")
        require((try? Data(contentsOf: good.appendingPathComponent("Game/raw.bin"))) == expected.prefix(3_000_000), "stored entry over several pieces")
        require((try? String(contentsOf: good.appendingPathComponent("Game/Game.exe"), encoding: .utf8)) == "MZ game", "a small file")
        require(FileManager.default.fileExists(atPath: good.appendingPathComponent("Game/empty").path), "a folder entry")
        require(bytes == reported.1 && reported.0 == reported.1 && bytes > 0, "progress ends at the total (\(reported))")
        require(!FileManager.default.fileExists(atPath: work.appendingPathComponent(".out-good.unpacking").path), "no staging folder left")
        require(ItchArchive.isZip(work.appendingPathComponent("good.zip")) && !ItchArchive.isZip(work.appendingPathComponent("expected-big.bin")), "isZip")

        let (zip64, zip64Out) = unpack("zip64")
        if case .success = zip64 { require((try? String(contentsOf: zip64Out.appendingPathComponent("a.txt"), encoding: .utf8)) == "zip64 entry", "ZIP64 archive") }
        else { require(false, "ZIP64 archive: \(zip64)") }
        let unicode = unpack("unicode")
        if case .success = unicode.0 { require(FileManager.default.fileExists(atPath: unicode.1.appendingPathComponent("Jeu/Données/é.txt").path), "UTF-8 names") }
        else { require(false, "UTF-8 names: \(unicode.0)") }

        for (name, label) in [("crc", "a damaged CRC"), ("traversal", "a name that leaves the folder"), ("absolute", "an absolute name"),
                              ("case", "two names that differ only in case"), ("symlink", "a symbolic link"), ("truncated", "a cut-off archive")] {
            // An install folder from before stays when the unpack fails.
            let target = work.appendingPathComponent("out-" + name, isDirectory: true)
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: target.appendingPathComponent("old.txt").path, contents: Data("old".utf8))
            let result = Result { try ItchArchive.unpack(work.appendingPathComponent(name + ".zip"), to: target) }
            var failed = false
            if case .failure = result { failed = true }
            require(failed && FileManager.default.fileExists(atPath: target.appendingPathComponent("old.txt").path)
                    && !FileManager.default.fileExists(atPath: work.appendingPathComponent("evil.txt").path),
                    "\(label) stops the unpack and the old folder stays (\(result))")
        }
        if failures > 0 { print("FAILURES: \(failures)"); exit(1) }
        print("PASS: all itch.io Swift checks")
    }
}
'''
stubs = r'''
import Foundation
final class LogStore {
    nonisolated(unsafe) static let shared = LogStore()
    func log(_ message: String) {}
}
'''

def make_archives(work):
    rng = random.Random(4)
    big = bytes(rng.getrandbits(8) for _ in range(1_500_000)) + b'madeira ' * 400_000   # ~4.7 MB, part compressible
    (work / 'expected-big.bin').write_bytes(big)
    with zipfile.ZipFile(work / 'good.zip', 'w') as z:
        z.writestr('Game/', '')
        z.writestr('Game/empty/', '')
        z.writestr(zipfile.ZipInfo('Game/Game.exe'), 'MZ game', compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('Game/Data/big.bin', big, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('Game/raw.bin', big[:3_000_000], compress_type=zipfile.ZIP_STORED)
    with zipfile.ZipFile(work / 'zip64.zip', 'w', allowZip64=True) as z:
        with z.open('a.txt', 'w', force_zip64=True) as f:
            f.write(b'zip64 entry')
    # Force a ZIP64 end record too: rewrite the archive with the end-of-directory fields at their ZIP64 markers.
    data = bytearray((work / 'zip64.zip').read_bytes())
    eocd = data.rfind(b'PK\x05\x06')
    if data.rfind(b'PK\x06\x06') < 0:
        cd_size = int.from_bytes(data[eocd + 12:eocd + 16], 'little')
        cd_off = int.from_bytes(data[eocd + 16:eocd + 20], 'little')
        count = int.from_bytes(data[eocd + 10:eocd + 12], 'little')
        z64 = (b'PK\x06\x06' + (44).to_bytes(8, 'little') + (45).to_bytes(2, 'little') + (45).to_bytes(2, 'little')
               + (0).to_bytes(4, 'little') + (0).to_bytes(4, 'little') + count.to_bytes(8, 'little') + count.to_bytes(8, 'little')
               + cd_size.to_bytes(8, 'little') + cd_off.to_bytes(8, 'little'))
        loc = b'PK\x06\x07' + (0).to_bytes(4, 'little') + eocd.to_bytes(8, 'little') + (1).to_bytes(4, 'little')
        end = bytearray(data[eocd:eocd + 22])
        end[10:12] = b'\xff\xff'; end[8:10] = b'\xff\xff'; end[12:16] = b'\xff\xff\xff\xff'; end[16:20] = b'\xff\xff\xff\xff'
        data = data[:eocd] + z64 + loc + end
        (work / 'zip64.zip').write_bytes(bytes(data))
    with zipfile.ZipFile(work / 'unicode.zip', 'w') as z:
        z.writestr('Jeu/Données/é.txt', 'ok')
    with zipfile.ZipFile(work / 'crc.zip', 'w') as z:
        z.writestr('a.txt', 'some content here', compress_type=zipfile.ZIP_STORED)
    data = bytearray((work / 'crc.zip').read_bytes())
    data[data.index(b'some content')] ^= 1
    (work / 'crc.zip').write_bytes(bytes(data))
    with zipfile.ZipFile(work / 'traversal.zip', 'w') as z:
        z.writestr('ok.txt', 'ok')
        z.writestr('../evil.txt', 'evil')
    # zipfile strips leading slashes: write a name of the same length, then put the slash in by hand.
    with zipfile.ZipFile(work / 'absolute.zip', 'w') as z:
        z.writestr('placeholder', 'x')
    (work / 'absolute.zip').write_bytes((work / 'absolute.zip').read_bytes().replace(b'placeholder', b'/abs/evil.t'))
    with zipfile.ZipFile(work / 'case.zip', 'w') as z:
        z.writestr('Game/Data.pak', 'a')
        z.writestr('game/data.PAK', 'b')
    with zipfile.ZipFile(work / 'symlink.zip', 'w') as z:
        info = zipfile.ZipInfo('link')
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        z.writestr(info, '/etc/passwd')
    good = (work / 'good.zip').read_bytes()
    (work / 'truncated.zip').write_bytes(good[:len(good) // 2] + good[-200:])



with tempfile.TemporaryDirectory(prefix='bcd-itch-') as tmp:
    tmp = Path(tmp)
    work = tmp / 'work'; work.mkdir()
    make_archives(work)
    (tmp / 'stubs.swift').write_text(stubs)
    (tmp / 'checks.swift').write_text(checks)
    # zlib as a Swift module on Linux (Apple's SDKs have one).
    (tmp / 'zlib').mkdir()
    (tmp / 'zlib/module.modulemap').write_text('module zlib [system] {\n  header "shim.h"\n  link "z"\n  export *\n}\n')
    (tmp / 'zlib/shim.h').write_text('#include <zlib.h>\n')
    exe = tmp / 'check'
    if not Path(SWIFTC).exists() and not shutil.which(SWIFTC):
        if '--require-swift' in sys.argv:
            require(False, 'Swift compiler available (--require-swift)')
        else:
            print('SKIP: Swift compiler unavailable; the compiled itch.io checks did not run')
    else:
        extra = [] if sys.platform == 'darwin' else ['-I', str(tmp / 'zlib')]
        # ItchArchive.swift's .7z half calls the C reader (itch_7z.c), which the app gets through its bridging header:
        # build it here and import its header the same way.
        cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
        reader = tmp / 'itch_7z.o'
        built_c = bool(cc) and subprocess.run([cc, '-c', '-O1', '-I', str(app / 'SwiftItch'),
                                               str(app / 'SwiftItch/itch_7z.c'), '-o', str(reader)]).returncode == 0
        require(built_c, 'itch_7z.c compiles on the host')
        lzma = ['-llzma']
        build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', *extra,
                                '-import-objc-header', str(app / 'SwiftItch/itch_7z.h'), '-o', str(exe),
                                str(tmp / 'stubs.swift'), str(app / 'SwiftItch/ItchAPI.swift'), str(app / 'SwiftItch/ItchArchive.swift'),
                                str(app / 'SwiftItch/ItchInstall.swift'), str(tmp / 'checks.swift'), str(reader), *lzma, '-lz'])
        require(build.returncode == 0, 'the itch.io sources compile on the host')
        if build.returncode == 0:
            run = subprocess.run([str(exe), str(work)])
            require(run.returncode == 0, 'itch.io checks pass')

if failures:
    print(f'FAILURES: {failures}')
    sys.exit(1)
print('PASS: itch.io store')
