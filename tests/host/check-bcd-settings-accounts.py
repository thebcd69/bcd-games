#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Settings › Accounts, JIT and BCD Dock in BCD's interface, on the host.

The sections are app/Madeira/BCDAccounts.swift, BCDJIT.swift and BCDDock.swift, built
from BCDSectionKit.swift; BCDAccountsLive/BCDJITLive/BCDDockLive.swift carry them out
on the engine; app/BCDPreview/BCDPreview{Accounts,JIT,Dock,Sections}.swift show them
on sample data (docs/BCD-UI-ARCHITECTURE.md).

1. Every file has BCD's licence header, word for word.
2. The views name no engine type (comments and strings aside). The live files are in
   tools/ui-preview.py's ENGINE_BOUND and the views are not. The Xcode project builds
   all seven app files, with IDs from A1000B60-A1000B7F and A2000B60-A2000B7F.
3. Controls: every Button in the views registers with BCDFocus (.bcdFocusable), and
   every text field sits in a FieldBox, which does too. No system control or alert a
   controller cannot reach. No animation that runs on its own. No "Madeira" in any
   text a person reads.
4. Accounts:
   - Sign out happens only from its question.
   - The password stays in the sign-in page's SecureField: it goes to the backend once
     and the field is emptied. It is never logged or stored, in the views, the live
     file or the sample.
   - The live sign-in is the app's own SteamSignInModel: no Steam auth or Keychain
     code of its own.
   - Epic Games and GOG have their own cards: sign in on the store's own page, sign out only from a question.
5. JIT:
   - The rules are compiled with swiftc and run. Per iOS version and environment
     they offer only the methods that work there, and ask for StikDebug's Always Run
     Scripts exactly where StikDebug needs it.
   - They mirror the app's own gates (JITBuiltInHost, JITPairing, JITNetwork).
   - The StikDebug guide carries the step, and the method list comes from the rules.
   - Enable JIT is the closure the live backend is given.
   - The live file changes no JIT engine state.
6. Dock:
   - Record as done and Reset are saved only from their questions.
   - They go through MadeiraDockModel.setInstallRequest, the existing reset flow
     (DockInstallers.applyRequest and DockReset at the game's next Dock start).
   - The download is MadeiraDockModel.prepareClient.
   - The progress reader still matches SteamRuntime.swift's lines.
7. The preview app has the three screens, and it builds (tools/ui-preview.py
   --build-only) when xcrun is there; --require-preview fails without it.

Needs swiftc (xcrun's on macOS) for part 5. Synthetic data only: no Steam, no device.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
preview = root / 'app/BCDPreview'
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def block(text, header):
    """`header` and the braces after it."""
    p = text.index(header)
    a = text.index('{', p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


def split_swift(src):
    """(code, strings): the source with comments removed and string literals emptied
    (interpolated code is kept as code), and the literals' own text."""
    code, strings = [], []
    i, n = 0, len(src)

    def string(i, raw):
        # src[i] is the opening quote; returns the index after the closing one.
        text, j = [], i + 1
        while j < n:
            c = src[j]
            if c == '\\' and not raw:
                if src.startswith('\\(', j):
                    j, depth = j + 2, 1
                    code.append('(')
                    while j < n and depth:
                        if src[j] == '"':
                            j = string(j, False)
                            code.append('""')
                            continue
                        depth += (src[j] == '(') - (src[j] == ')')
                        if depth:
                            code.append(src[j])
                        j += 1
                    code.append(')')
                    continue
                text.append(src[j:j + 2])
                j += 2
                continue
            if c == '"':
                if raw and not src.startswith('"#', j):
                    text.append(c)
                    j += 1
                    continue
                strings.append(''.join(text))
                return j + (2 if raw else 1)
            text.append(c)
            j += 1
        strings.append(''.join(text))
        return j

    while i < n:
        if src.startswith('//', i):
            j = src.find('\n', i)
            i = n if j < 0 else j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            i = n if j < 0 else j + 2
        elif src.startswith('#"', i):
            code.append('""')
            i = string(i + 1, True)
        elif src[i] == '"':
            code.append('""')
            i = string(i, False)
        else:
            code.append(src[i])
            i += 1
    return ''.join(code), strings


HEADER = ('// SPDX-License-Identifier: GPL-3.0-only\n'
          '// Copyright 2026 thebcd69 (BCD)\n'
          '// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n')
VIEWS = ['BCDSectionKit.swift', 'BCDAccounts.swift', 'BCDJIT.swift', 'BCDDock.swift']
LIVE = ['BCDAccountsLive.swift', 'BCDJITLive.swift', 'BCDDockLive.swift']
SAMPLES = ['BCDPreviewAccounts.swift', 'BCDPreviewJIT.swift', 'BCDPreviewDock.swift', 'BCDPreviewSections.swift']

source = {name: (app / name).read_text() for name in VIEWS + LIVE}
source.update({name: (preview / name).read_text() for name in SAMPLES})
parts = {name: split_swift(text) for name, text in source.items()}
code = {name: parts[name][0] for name in parts}
strings = {name: parts[name][1] for name in parts}

# 1. Licence header.
for name, text in source.items():
    require(text.startswith(HEADER), f'{name}: BCD licence header, word for word')
require(Path(__file__).read_text().startswith('#!/usr/bin/env python3\n' + HEADER.replace('// ', '# ')),
        'this check: BCD licence header')

# 2. No engine types in the views; ENGINE_BOUND; the Xcode project.
ENGINE = ['LibraryModel', 'SteamSignInModel', 'SteamSignIn', 'SteamGuardPrompt', 'SteamGuardType', 'SteamTokenStore',
          'SteamCredentialAuth', 'SteamQRAuth', 'SteamSession', 'SteamLog', 'SteamCloudSetting', 'SteamOwnedLibrary',
          'MadeiraDockModel', 'MadeiraDock', 'DockGame', 'DockInstallers', 'DockInstallRequest', 'DockReset',
          'SteamRuntimeInstaller', 'SteamRuntimeFiles', 'JITCoordinator', 'JITMethod', 'JITPairingFileStore',
          'StikJITHelper', 'OnDevicePairing', 'JITNetworkShortcut', 'JITShortcutFile', 'LocalDevVPN', 'LoopbackProbe',
          'MadeiraBuiltInJIT', 'SigningStatus', 'EntitlementStatus', 'OnboardingModel', 'WineProcessBridge',
          'MadeiraConfig', 'LogStore', 'ContentView', 'isDebuggerAttached', 'wine_process_is_running',
          'BCDGOG', r'BCDGOG\w+', r'BCDEpic\w*', 'BCDLiveStore', r'BCDContent\w*', 'BCDWebSignIn', 'BCDKeychain',
          'wineserver_is_running', r'jit_\w+', r'madeira_\w+']
engine_re = re.compile(r'\b(' + '|'.join(ENGINE) + r')\b')
for name in VIEWS + SAMPLES:
    hits = sorted(set(engine_re.findall(code[name])))
    require(not hits, f'{name}: names no engine type' + (f' (found {", ".join(hits)})' if hits else ''))

tool = (root / 'tools/ui-preview.py').read_text()
bound = {name for found in re.findall(r'^ENGINE_BOUND\s*\|?=\s*\{([^}]*)\}', tool, re.M)
         for name in re.findall(r'"([^"]+\.swift)"', found)}
require(set(LIVE) <= bound, 'tools/ui-preview.py: the three live backends are ENGINE_BOUND')
require(not (set(VIEWS) & bound), 'tools/ui-preview.py: the views themselves are in the preview build')

project = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
sources_phase = block(project, 'A3000002 /* Sources */ = {')
group = block(project, 'A4000002 /* Madeira */ = {') if 'A4000002 /* Madeira */ = {' in project else project
for name in VIEWS + LIVE:
    build = re.search(r'(A1000B[67][0-9A-F]) /\* ' + re.escape(name) + r' in Sources \*/ = \{isa = PBXBuildFile; fileRef = (A2000B[67][0-9A-F]) /\* '
                      + re.escape(name) + r' \*/; \};', project)
    ok = bool(build)
    if build:
        ref = build.group(2)
        ok = (re.search(re.escape(ref) + r' /\* ' + re.escape(name) + r' \*/ = \{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = '
                        + re.escape(name) + r'; sourceTree = "<group>"; \};', project) is not None
              and f'{ref} /* {name} */,' in group
              and f'{build.group(1)} /* {name} in Sources */,' in sources_phase
              and project.count(build.group(1)) == 2 and project.count(ref) == 3)
    require(ok, f'Xcode project builds {name} (IDs A1000B60-7F / A2000B60-7F, file reference, group, Sources)')

# 3. Controls, motion, words.
FORBIDDEN = [r'\bToggle\s*\(', r'\bPicker\s*\(', r'\bMenu\s*\(', r'\bLink\s*\(', r'\bShareLink\s*\(', r'\.confirmationDialog\s*\(',
             r'\.alert\s*\(', r'\.contextMenu\b', r'repeatForever', r'\bProgressView\b', r'\.symbolEffect', r'\bTimelineView\b',
             r'Timer\.publish', r'Timer\.scheduledTimer', r'\.task\s*\{[^}]*while\b']
for name in VIEWS:
    hits = [p for p in FORBIDDEN if re.search(p, code[name])]
    require(not hits, f'{name}: no unreachable system control or alert, no animation of its own' + (f' ({hits})' if hits else ''))
    buttons = [m.start() for m in re.finditer(r'\bButton\s*[({]', code[name])]
    unmarked = [code[name].count('\n', 0, s) + 1 for k, s in enumerate(buttons)
                if '.bcdFocusable(' not in code[name][s:(buttons[k + 1] if k + 1 < len(buttons) else len(code[name]))][:2500]]
    require(buttons is not None and not unmarked, f'{name}: every Button is .bcdFocusable ({len(buttons)} found)'
            + (f'; not at lines {unmarked}' if unmarked else ''))
    fields = [m.start() for m in re.finditer(r'\b(TextField|SecureField)\s*\(', code[name])]
    loose = [code[name].count('\n', 0, s) + 1 for s in fields if 'FieldBox(' not in code[name][max(0, s - 300):s]]
    require(not loose, f'{name}: every text field is in a FieldBox ({len(fields)} found)' + (f'; not at lines {loose}' if loose else ''))
kit = code['BCDSectionKit.swift']
field_box = block(kit, 'struct FieldBox<Field: View>: View')
require('.bcdFocusable(id' in field_box, 'BCDSectionKit.FieldBox registers its field with BCDFocus')
buttons_mod = block(kit, 'struct Buttons<Key: Equatable>: ViewModifier')
require('button: .b' in buttons_mod and '{ if !inner() { back?() } }' in buttons_mod and 'hook(inner)' in buttons_mod
        and 'onChange(of: key)' in buttons_mod and '.onDisappear { hook?(nil) }' in buttons_mod,
        'BCDSectionKit.Buttons: B closes the section\'s own page or question first, then goes back (or the host\'s B asks first)')
inner = {'BCDAccounts.swift': 'inner: closeInner', 'BCDJIT.swift': 'inner: { false }', 'BCDDock.swift': 'inner: closeQuestion'}
for name, wiring in inner.items():
    require(f'BCDSectionKit.Buttons(focus: focus' in code[name] and wiring in code[name] and 'back: back, hook: backHook' in code[name]
            and 'var backHook: BCDSectionKit.BackHook? = nil' in code[name] and 'scrolls: scrolls' in code[name],
            f'{name}: B goes back through BCDSectionKit.Buttons; a host can scroll it and take B (scrolls, backHook)')
require('if confirmingSignOut {' in block(code['BCDAccounts.swift'], 'private func closeInner() -> Bool')
        and 'if page != .main {' in block(code['BCDAccounts.swift'], 'private func closeInner() -> Bool')
        and 'guard let question = asking else { return false }' in block(code['BCDDock.swift'], 'private func closeQuestion() -> Bool'),
        'B closes the sign-out question and the sign-in page (Accounts) and the reset question (Dock) first')
for name in VIEWS + LIVE + SAMPLES:
    said = [s for s in strings[name] if 'madeira' in s.lower()]
    require(not said, f'{name}: no "Madeira" in its text' + (f' ({said[:3]})' if said else ''))
moving = [m.group(0) for name in VIEWS for m in re.finditer(r'(?:withAnimation|\.animation)\([^)]*\)', code[name])]
long = [m for m in moving if re.search(r'duration:\s*0\.(?:2[6-9]|[3-9])|duration:\s*[1-9]', m)]
require(not long, f'the views animate only briefly, 0.25 s at most ({len(moving)} animations)' + (f': {long}' if long else ''))

# Memory comes first (docs/BCD-UI-ARCHITECTURE.md): no picture kept in a view's state, and each
# live backend lives only while its section shows (the wrappers own it as a @StateObject).
for name in VIEWS:
    require(not re.search(r'@State[^\n]*(UIImage|CGImage|Data\b)', code[name]), f'{name}: keeps no picture in its state')
for live, wrapper, backend in [('BCDAccountsLive.swift', 'BCDAccountsLiveSection', 'BCDAccountsLive()'),
                               ('BCDJITLive.swift', 'BCDJITLiveSection', 'BCDJITLive(enableJIT: enableJIT)'),
                               ('BCDDockLive.swift', 'BCDDockLiveSection', 'BCDDockLive(startDock: startDock)')]:
    body = block(code[live], f'struct {wrapper}: View')
    require('@StateObject private var backend' in body and backend in body and 'scrolls: scrolls' in body and 'backHook: backHook' in body,
            f'{live}: {wrapper} owns its backend while it shows and passes the host\'s scrolls and backHook')

# 4. Accounts.
accounts = code['BCDAccounts.swift']
question = block(accounts, 'private var signOutQuestion: some View')
require(accounts.count('backend.signOut()') == 1 and 'backend.signOut()' in question,
        'Accounts: Sign out only from its question')
accounts_text = source['BCDAccounts.swift']
sign_out_button = accounts_text[accounts_text.index('id: Self.prefix + "steam.signout") {'):][:240]
require('confirmingSignOut = true' in sign_out_button and 'backend.signOut' not in sign_out_button,
        'Accounts: the Sign out button asks first')
page = block(accounts, 'struct BCDSteamSignInPage<Backend: BCDAccountsBackend>: View')
submit = block(page, 'private func submit()')
require('@State private var password = ""' in page and 'SecureField(' in page and 'text: $password' in page,
        'Accounts: the password lives only in the sign-in page, in a SecureField')
require(accounts.count('backend.signIn(account:') == 1 and 'backend.signIn(account: name, password: password)' in submit
        and submit.index('backend.signIn(') < submit.index('password = ""'),
        'Accounts: the password goes to the backend once, on Sign in, and the field is emptied at once')
require(page.count('password = ""') >= 4, 'Accounts: the field is also emptied on leaving, on the QR code and when the page goes')
STORE_OR_LOG = re.compile(r'print\(|NSLog|os_log|Logger|\blog\(|\.event\(|UserDefaults|AppStorage|SceneStorage|SecItem|Keychain|'
                          r'\.write\(|FileManager|dump\(|debugPrint')
for name in ['BCDAccounts.swift', 'BCDAccountsLive.swift', 'BCDPreviewAccounts.swift', 'BCDSectionKit.swift']:
    lines = [line for line in code[name].splitlines() if 'password' in line.lower()]
    bad = [line.strip() for line in lines if STORE_OR_LOG.search(line)]
    require(not bad, f'{name}: the password is never logged or stored' + (f' ({bad})' if bad else ''))
live_accounts = code['BCDAccountsLive.swift']
password_lines = [line.strip() for line in live_accounts.splitlines() if 'password' in line]
require(password_lines == ['func signIn(account: String, password: String) {',
                           'SteamSignInModel.shared.signIn(account: account, password: password)'],
        'Accounts live: the password only passes through, into SteamSignInModel.signIn')
sample_lines = [line.strip() for line in code['BCDPreviewAccounts.swift'].splitlines() if 'password' in line]
require(sample_lines == ['func signIn(account: String, password: String) {'], 'Accounts sample: the password is not even read')
require(not re.search(r'\b(SteamCredentialAuth|SteamQRAuth|SteamTokenStore|SteamAuthAPI|SecItem\w*)\b', live_accounts)
        and 'SteamSignInModel.shared.signOut()' in live_accounts and 'SteamSignInModel.shared.beginQR()' in live_accounts
        and 'SteamSignInModel.shared.submitGuardCode(code)' in live_accounts
        and 'SteamCloudSetting.shared.on = on' in live_accounts,
        'Accounts live: the app\'s own sign-in model, sign-out and Steam Cloud setting; no Steam auth or Keychain code of its own')
require('comingSoon' not in accounts and 'Coming soon' not in ''.join(strings['BCDAccounts.swift']) and 'epicCard' in block(accounts, 'private var main: some View'),
        'Accounts: Epic Games has its own card (no "Coming soon" any more)')
epic_card = block(accounts, 'private var epicCard: some View')
epic_question = block(accounts, 'private var epicSignOutQuestion: some View')
require('backend.signInEpic()' in epic_card and accounts.count('backend.signOutEpic()') == 1 and 'backend.signOutEpic()' in epic_question
        and 'confirmingEpicSignOut = true' in epic_card and 'if confirmingEpicSignOut {' in block(accounts, 'private func closeInner() -> Bool'),
        'Accounts: Epic Games signs in from its card and signs out only from its question; B closes the question')
epic_words = ' '.join(strings['BCDAccounts.swift'])
require('BCD ends its Epic sign-in on this ' in epic_words and 'forgets the list of your Epic games.' in epic_words
        and 'Use Safari' in epic_words and 'Sign in to Epic Games' in epic_words and 'Signed in' in epic_words
        and 'BCD never sees your password' in epic_words and "BCD installs the ones it can run, and says why when it can't." in epic_words
        and 'Sign in to see the games you own on Epic Games and install them.' in epic_words and 'comes in a later update' not in epic_words,
        "Accounts: Epic's card says what signing out does, that the password goes to Epic's page, that a Google sign-in may need Use Safari and that BCD installs what it can run")
gog_card = block(accounts, 'private var gogCard: some View')
gog_question = block(accounts, 'private var gogSignOutQuestion: some View')
require('backend.signInGOG()' in gog_card and accounts.count('backend.signOutGOG()') == 1 and 'backend.signOutGOG()' in gog_question
        and 'confirmingGOGSignOut = true' in gog_card and 'if confirmingGOGSignOut {' in block(accounts, 'private func closeInner() -> Bool'),
        'Accounts: GOG signs in from its card and signs out only from its question; B closes the question')
gog_words = ' '.join(strings['BCDAccounts.swift'])
require('BCD forgets your GOG sign-in. GOG has no way to end it from outside; it lapses by itself. Installed games stay.' in gog_words
        and 'Use Safari' in gog_words and 'Sign in to GOG' in gog_words and 'Signed in' in gog_words,
        "Accounts: GOG's card says what signing out does and that a Google sign-in may need Use Safari")
require('BCDLiveStore.shared.signIn(.gog)' in live_accounts and 'BCDLiveStore.shared.signOut(.gog)' in live_accounts
        and 'BCDLiveStore.shared.gogAccount()' in live_accounts and 'BCDGOG.shared.objectWillChange' in live_accounts
        and not re.search(r'BCDGOG\.shared\.(signIn|signOut)|BCDWebSignIn|BCDKeychain|BCDGOGSession', live_accounts),
        "Accounts live: GOG's account, sign-in and sign-out go through the store's GOG hooks only")
require('BCDLiveStore.shared.signIn(.epic)' in live_accounts and 'BCDLiveStore.shared.signOut(.epic)' in live_accounts
        and 'BCDLiveStore.shared.epicAccount()' in live_accounts and 'BCDEpic.shared.objectWillChange' in live_accounts
        and not re.search(r'BCDEpic\.shared\.(signIn|signOut)|BCDWebSignIn|BCDKeychain|BCDEpicSession', live_accounts),
        "Accounts live: Epic's account, sign-in and sign-out go through the store's Epic hooks only")

# 5. JIT: the rules, compiled and run.
jit_text = source['BCDJIT.swift']
rules = jit_text[jit_text.index('// MARK: - Rules'):jit_text.index('// MARK: - End of rules')]
TEST = r'''
import Foundation
var failures = 0
func expect(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failures += 1 } }
func p(_ major: Int, _ model: String, lc: Bool = false, sim: Bool = false) -> BCDJITPlatform {
    BCDJITPlatform(osMajor: major, osMinor: 0, model: model, inLiveContainer: lc, simulator: sim)
}
let all: [BCDJITMethodChoice] = [.automatic, .stikDebug, .builtIn]
// iOS 18: StikDebug alone, and it needs Always Run Scripts on every device.
for model in ["iPhone11,2", "iPhone14,2", "iPhone15,2", "iPhone17,1", "iPad13,4", "iPad14,5", "iPad16,3"] {
    expect(BCDJITRules.offered(p(18, model)) == [.stikDebug], "iOS 18 \(model): StikDebug only")
    expect(BCDJITRules.needsAlwaysRunScripts(p(18, model)), "iOS 18 \(model): Always Run Scripts")
    expect(BCDJITRules.builtInBlock(p(18, model)) == .needsIOS26, "iOS 18 \(model): Built-in needs iOS 26")
    expect(!BCDJITRules.canPairOnDevice(p(18, model)) && !BCDJITRules.shortcutAvailable(p(18, model)), "iOS 18 \(model): no pairing here, no shortcut")
}
// iOS 26: every method; Always Run Scripts on the devices StikDebug counts as without TXM.
for (model, scripts) in [("iPhone12,1", false), ("iPhone13,2", false), ("iPhone14,1", false), ("iPhone14,2", true), ("iPhone14,7", true),
                         ("iPhone15,2", true), ("iPhone18,2", true), ("iPad13,4", false), ("iPad14,1", false), ("iPad14,4", false),
                         ("iPad14,5", true), ("iPad16,3", true), ("iPad8,11", false)] {
    let platform = p(26, model)
    expect(BCDJITRules.offered(platform) == all, "iOS 26 \(model): Automatic, StikDebug and Built-in")
    expect(BCDJITRules.stikDebugRunsScripts(platform) == scripts, "iOS 26 \(model): StikDebug runs the script by itself = \(scripts)")
    expect(BCDJITRules.needsAlwaysRunScripts(platform) == !scripts, "iOS 26 \(model): Always Run Scripts = \(!scripts)")
    expect(!BCDJITRules.canPairOnDevice(platform) && !BCDJITRules.shortcutAvailable(platform), "iOS 26 \(model): no pairing here, no shortcut")
}
// iOS 27 and later: every method, pairing on the device, the shortcut; Always Run Scripts only on iPad8,11/12.
for major in [27, 28] {
    for (model, scripts) in [("iPhone18,2", true), ("iPhone12,1", true), ("iPad8,10", true), ("iPad8,11", false), ("iPad8,12", false)] {
        let platform = p(major, model)
        expect(BCDJITRules.offered(platform) == all, "iOS \(major) \(model): every method")
        expect(BCDJITRules.needsAlwaysRunScripts(platform) == !scripts, "iOS \(major) \(model): Always Run Scripts = \(!scripts)")
        expect(BCDJITRules.canPairOnDevice(platform) && BCDJITRules.shortcutAvailable(platform), "iOS \(major) \(model): pairing here and the shortcut")
    }
}
// Inside LiveContainer: no built-in helper, so no Automatic either, no shortcut.
for major in [18, 26, 27] {
    let platform = p(major, "iPhone15,2", lc: true)
    expect(BCDJITRules.offered(platform) == [.stikDebug], "iOS \(major) in LiveContainer: StikDebug only")
    expect(BCDJITRules.builtInBlock(platform) == (major < 26 ? .needsIOS26 : .liveContainer), "iOS \(major) in LiveContainer: why Built-in is out")
    expect(!BCDJITRules.canPairOnDevice(platform) && !BCDJITRules.shortcutAvailable(platform), "iOS \(major) in LiveContainer: no pairing here, no shortcut")
}
expect(BCDJITRules.offered(p(27, "x86_64", sim: true)) == [.stikDebug] && BCDJITRules.builtInBlock(p(27, "arm64", sim: true)) == .simulator
       && !BCDJITRules.shortcutAvailable(p(27, "arm64", sim: true)), "simulator: no built-in helper, no shortcut")
expect(BCDJITRules.model("iPhone14,2")! == ("iPhone", 14, 2) && BCDJITRules.model("x86_64") == nil && BCDJITRules.model("iPad8,11")! == ("iPad", 8, 11)
       && BCDJITRules.model("iPhone") == nil && BCDJITRules.model("iPhone14,") == nil, "model numbers are read whole or not at all")
exit(failures == 0 ? 0 : 1)
'''
swiftc = os.environ.get('SWIFTC') or (['xcrun', 'swiftc'] if shutil.which('xcrun') else None) or shutil.which('swiftc')
if swiftc:
    with tempfile.TemporaryDirectory(prefix='bcd-settings-rules-') as tmp:
        work = Path(tmp)
        (work / 'rules.swift').write_text('import Foundation\n' + rules)
        (work / 'main.swift').write_text(TEST)
        command = (swiftc if isinstance(swiftc, list) else [swiftc]) + ['-o', str(work / 'rules'), str(work / 'main.swift'), str(work / 'rules.swift')]
        built = subprocess.run(command, capture_output=True, text=True)
        require(built.returncode == 0, 'JIT rules compile on their own' + ('' if built.returncode == 0 else ': ' + built.stderr[-1500:]))
        if built.returncode == 0:
            ran = subprocess.run([str(work / 'rules')], capture_output=True, text=True)
            fails = [line for line in ran.stdout.splitlines() if line.startswith('FAIL')]
            require(ran.returncode == 0, f'JIT rules: the method list per iOS version and environment '
                    f'({ran.stdout.count("PASS")} cases)' + (f'; {fails[:5]}' if fails else ''))
else:
    require(False, 'JIT rules: no swiftc to compile them (set SWIFTC)')

host = (app / 'JITBuiltInHost.swift').read_text()
reason = block(host, 'static var unavailableReason: String?')
require('targetEnvironment(simulator)' in reason and '#available(iOS 26.0, *)' in reason and 'getenv("LC_HOME_PATH")' in reason,
        'JIT rules mirror the app: the built-in helper needs a device, iOS 26 and no LiveContainer (JITBuiltInHost.swift)')
pairing = (app / 'JITPairing.swift').read_text()
network = (app / 'JITNetwork.swift').read_text()
require('majorVersion: 27' in block(pairing, 'static var isSupported: Bool') and 'static let hostName = "BCD"' in pairing,
        'JIT rules mirror the app: pairing on the device needs iOS 27, and Settings calls it "Pair with BCD" (JITPairing.swift)')
require('majorVersion: 27' in block(network, 'static var supported: Bool'),
        'JIT rules mirror the app: the JIT shortcut needs iOS 27 (JITNetwork.swift)')
jit = code['BCDJIT.swift']
stik_steps = jit_text[jit_text.index('case .stikDebug:\n            var list'):]
stik_steps = stik_steps[:stik_steps.index('return list')]
require('if BCDJITRules.needsAlwaysRunScripts(platform) {' in stik_steps and 'Always Run Scripts' in stik_steps,
        'JIT: the StikDebug guide asks for Always Run Scripts where StikDebug needs it')
require('let offered = BCDJITRules.offered(platform)' in jit and 'ForEach(offered)' in jit
        and 'if BCDJITRules.shortcutAvailable(platform) || state.shortcutOn { shortcutCard }' in jit
        and 'if BCDJITRules.builtInBlock(platform) == nil { pairingCard }' in jit,
        'JIT: the methods, the pairing and the shortcut shown come from the rules')
require('backend.enableJIT()' in jit and 'backend.importPairingFile(url)' in jit and 'backend.startPairing()' in jit
        and 'backend.openLocalDevVPN()' in jit, 'JIT: Enable JIT, pairing file import, pair again and LocalDevVPN are there')
live_jit = code['BCDJITLive.swift']
require('func enableJIT() { enable() }' in live_jit and live_jit.count('enable()') == 1,
        'JIT live: Enable JIT is the closure it is given (the interface\'s own Enable JIT)')
require(not re.search(r'StikJITHelper\.(enableJIT|allocatePool|allocateAndDetach|detachDebugger|waitForDebugger)|'
                      r'JITCoordinator\.shared\.enable\(|poolTaken\s*=|setenv\(|unsetenv\(|MadeiraConfig\.set', live_jit)
        and live_jit.count('.enableBuiltIn(') == 1
        and 'func enableWithBuiltIn() { JITCoordinator.shared.enableBuiltIn() }' in live_jit
        and 'StikJITHelper.ready' in live_jit and 'StikJITHelper.flaggedWithoutDebugger' in live_jit,
        'JIT live: reads the JIT state Play itself checks and changes no JIT engine state '
        '(the built-in helper on demand is the old setup\'s own enableBuiltIn)')
require('if BCDJITRules.shortcutAvailable(platform) || state.shortcutOn { shortcutCard }' in jit,
        'JIT: a JIT shortcut left on where it cannot work stays visible, so it can be turned off')
require('getenv("LC_HOME_PATH")' in source['BCDJITLive.swift'], 'JIT live: LiveContainer is recognised as the app recognises it')

# 6. Dock.
dock = code['BCDDock.swift']
record_q = block(dock, 'private func recordQuestion(_ game: BCDDockInstallGame) -> some View')
reset_q = block(dock, 'private func resetQuestion(_ game: BCDDockInstallGame) -> some View')
saved = re.findall(r'backend\.setRequest\(game\.id, \.(\w+)\)', dock)
require(sorted(saved) == ['record', 'reset', 'resetAndSignOut'] and 'backend.setRequest(game.id, .record)' in record_q
        and 'backend.setRequest(game.id, .reset)' in reset_q and 'backend.setRequest(game.id, .resetAndSignOut)' in reset_q,
        'Dock: Record as done and Reset are saved only from their questions')
row = block(dock, 'private func installRow(_ game: BCDDockInstallGame) -> some View')
require('ask(.record, game.id)' in row and 'ask(.reset, game.id)' in row and 'backend.setRequest(game.id, nil)' in row,
        'Dock: the row asks first, and a saved request can be cancelled')
live_dock = code['BCDDockLive.swift']
require('MadeiraDockModel.shared.setInstallRequest(appID, request.map(Self.engine))' in live_dock
        and 'case .resetAndSignOut: return .resetSignOut' in live_dock
        and not re.search(r'\bDockReset\.|DockInstallers\.(applyRequest|setRequest|setRunsNext)|SteamRuntimeInstaller', live_dock),
        'Dock live: Reset goes through the existing flow (MadeiraDockModel.setInstallRequest; DockReset at the next Dock start)')
require('MadeiraDockModel.shared.prepareClient()' in live_dock and 'MadeiraDockModel.shared.setRunsInstallers(appID, run)' in live_dock,
        'Dock live: the download and the one-time installs choice are the Dock model\'s own')
require('startDock(game, dock.compactPool)' in block(live_dock, 'func start(_ appID: Int)')
        and 'MadeiraDockModel.shared.compactPool = on' in live_dock
        and not re.search(r'MadeiraDock\.(configure|writeHandoff|requestLaunch|validate)|wine_|LibraryModel', live_dock),
        'Dock live: a game starts only through the start closure it is given (the interface\'s own Dock start), '
        'with the pool choice; no launch path of its own')
require('let startable = game.installed && state.components.installed && state.accountName != nil' in dock
        and 'enabled: startable) { backend.start(game.id) }' in dock,
        'Dock: Start only for a fully installed game, with the client files in place and Steam signed in')
model = (app / 'MadeiraDockModel.swift').read_text()
require('DockInstallers.setRequest(appID, request, prefix: MadeiraDock.prefix)' in block(model, 'func setInstallRequest(')
        and 'SteamRuntimeInstaller.shared.prepare(' in block(model, 'func prepareClient(replacing: Bool = false)'),
        'Dock: the model\'s setInstallRequest and prepareClient are still the reset flow and the pinned download')
runtime = (app / 'SteamRuntime.swift').read_text()
require('part \\(index + 1) of \\(SteamRuntimeFiles.packages.count)' in runtime
        and '"Installing the components into Windows…"' in runtime and '"Done. The client components are in place."' in runtime
        and 'part (\\d+) of \\d+' in source['BCDDockLive.swift']
        and 'hasPrefix("Installing the components")' in source['BCDDockLive.swift'],
        'Dock live: the download\'s step is read from the lines SteamRuntime.swift writes')

# 6b. Nothing of the classic screens is lost (owner, 2026-10-18: they are deleted): every function of
# SteamSignInView, JITSetupView/JITSettingsSection/OnDevicePairingPanel/jitConnectionActions and
# MadeiraDockView/DockInstallRequestRows had its place in the sections.
PARITY = {
    'BCDAccounts.swift': ['backend.signIn(account:', 'backend.submitGuardCode(', 'backend.beginQR()', 'backend.cancelSignIn()',
                          'backend.signOut()', 'backend.setCloud(', 'backend.runSetup()', 'SecureField(', '.oneTimeCode',
                          'UIApplication.shared.open(link)', 'steamGuard.canApprove', 'case .email(let hint)',
                          'backend.signInGOG()', 'backend.signOutGOG()'],
    'BCDJIT.swift': ['backend.enableJIT()', 'backend.choose(method)', 'backend.importPairingFile(url)', 'backend.startPairing()',
                     'backend.stopPairing()', 'backend.checkSetup()', 'backend.enableWithBuiltIn()', 'backend.clearDiskImageCache()',
                     'backend.openLocalDevVPN()', 'backend.setShortcut(', 'backend.addShortcut()', 'backend.runShortcut()',
                     'backend.runSetup()', 'ShareSheet(items:', 'state.txm', 'case .code(let pin)', 'shortBackground', 'BCDJITPairingPanel(run: state.pairing',
                     'BCDJITLinks.stikDebug', 'BCDJITLinks.pairingFileGuide', 'case .pairAgain', 'case .connectVPN', 'case .runShortcut'],
    'BCDDock.swift': ['backend.downloadComponents()', 'backend.replaceComponents()', 'backend.start(game.id)', 'backend.setCompactPool(', 'backend.setRunsInstalls(',
                      'backend.setRequest(game.id, .record)', 'backend.setRequest(game.id, .reset)',
                      'backend.setRequest(game.id, .resetAndSignOut)', 'backend.setRequest(game.id, nil)', 'state.lastResult',
                      'parts.error', 'signIn'],
}
for name, needles in PARITY.items():
    missing = [n for n in needles if n not in code[name]]
    require(not missing, f'{name}: every function of the classic screen it replaces is there' + (f' (missing {missing})' if missing else ''))

# 6c. The classic screens are unused by BCD's interface (owner, 2026-10-18): setup signs in and pairs
# with BCD's views, the shell's sign-in sheet is BCD's, and ContentView's on-demand JIT setup is
# Settings › JIT. Wave 3 (2026-10-19): the classic screens are deleted, so no file of the app names them any more.
CLASSIC = re.compile(r'\b(SteamSignInView|OnDevicePairingPanel|JITSetupView|JITSettingsSection|MadeiraDockView|'
                     r'DockInstallRequestRows|SteamSettingsSection)\s*[({]')
for path in sorted(app.rglob('*.swift')):
    hits = sorted(set(m.group(1) for m in CLASSIC.finditer(split_swift(path.read_text())[0])))
    require(not hits, f'{path.name}: uses no classic screen' + (f' ({hits})' if hits else ''))
setup_text = (app / 'BCDSetupView.swift').read_text()
content_text = (app / 'ContentView.swift').read_text()
front_text = (app / 'BCDLiveFrontEnd.swift').read_text() if (app / 'BCDLiveFrontEnd.swift').exists() else ''
require('.sheet(isPresented: $showSignIn) { BCDSteamSignInSheet() }' in setup_text
        and 'BCDJITPairingPanel(run: BCDJITLive.pairingRun(), idPrefix: "setup.jit.") { pairing.cancel() }' in setup_text,
        "setup: Steam sign-in and the pairing panel are BCD's, over the same models")
require('.sheet(isPresented: $jitCoordinator.showSetup) { BCDJITSetupSheet(enableJIT: { enableJIT() }) }' in content_text,
        "ContentView: the JIT setup a method needs is Settings › JIT, enabling through ContentView's own Enable JIT")
require(not front_text or 'BCDSteamSignInSheet()' in front_text, "the shell's Steam sign-in sheet is BCD's")
sheets = code['BCDAccountsLive.swift'] + code['BCDJITLive.swift']
require('struct BCDSteamSignInSheet: View' in sheets and 'BCDSteamSignInPage(backend: backend' in block(code['BCDAccountsLive.swift'], 'struct BCDSteamSignInSheet: View')
        and 'BCDJITLiveSection(enableJIT: enableJIT' in block(code['BCDJITLive.swift'], 'struct BCDJITSetupSheet: View'),
        "BCD's sign-in sheet and JIT setup sheet are the sections' own pages on the live backends")

# 7. The preview app.
app_text = (preview / 'BCDPreviewApp.swift').read_text()
sections = code['BCDPreviewSections.swift']
require('BCDPreviewSettingsSection.over(root, focus: focus)' in app_text and 'static func over<Root: View>' in sections and '["accounts", "jit", "dock"]' in source['BCDPreviewSections.swift']
        and 'BCDAccountsSection(backend: accounts' in sections and 'BCDJITSection(backend: jit' in sections
        and 'BCDDockSection(backend: dock' in sections and 'content(scrolls: false)' in sections,
        'preview app: -BCDTab settings-accounts, settings-jit and settings-dock show the sections on samples, '
        'alone and hosted as Settings hosts them (-hosted)')
if shutil.which('xcrun'):
    with tempfile.TemporaryDirectory(prefix='bcd-settings-preview-') as tmp:
        built = subprocess.run([sys.executable, str(root / 'tools/ui-preview.py'), '--build-only', '--out', tmp],
                               capture_output=True, text=True)
        require(built.returncode == 0, 'the preview app builds (no screen touches the engine)'
                + ('' if built.returncode == 0 else ': ' + (built.stderr or built.stdout)[-2000:]))
else:
    require('--require-preview' not in sys.argv, 'the preview app builds (needs xcrun)')
    print('SKIP: no xcrun here, so the preview app was not built')

print(f'\ncheck-bcd-settings-accounts: {"all passed" if failures == 0 else f"{failures} failed"}')
sys.exit(1 if failures else 0)
