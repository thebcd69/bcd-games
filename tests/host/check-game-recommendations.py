#!/usr/bin/env python3
"""BCD's built-in recommended settings per game (owner's decision 2026-10-09).

Source checks (every host): the lists in recommended/recommendations.json (the
app's built-in copy since 2026-10-18, read by GameRecommendationsList.swift)
match the working lists they were taken from (Red Dead Redemption 2's line for
line), every key that is not env.NAME is one Madeira reads (the generated
settings catalog), no list carries DLL overrides for Rockstar's Social Club,
nothing a list or GameProfile writes into a game's config says "Madeira" (owner,
2026-10-18: a list starts "# BCD's recommended settings for ..."), and the app
applies them where the owner asked: a game's first sight (library start-up,
saving an entry, Game details, a start, the madeira-bcd home screen) and Reset
to Recommended at the bottom of both settings pages.

The Swift fixture (macOS CI) compiles the real GameRecommendations*.swift (the
lists read from the JSON) and GameRecommendationsApply.swift against small
stand-ins for the app types and checks how games are recognised (Steam games by
App ID or by their folder under steamapps\\common, every other game by its
program's name, GTA V Enhanced inside or outside a Steam library), the
first-sight, update and reset rules, and that a newer list (a download) reaches
untouched configs and never a changed one. Linux hosts without Swift run the
source checks; --require-swift turns a missing compiler into a failure.
"""
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
lists_src = (app / 'GameRecommendations.swift').read_text()
apply_src = (app / 'GameRecommendationsApply.swift').read_text()
library = (app / 'Library.swift').read_text()
files = (app / 'GameFiles.swift').read_text()
content = (app / 'ContentView.swift').read_text()
profiles = (app / 'GameProfiles.swift').read_text()
catalog = (app / 'ConfigCatalog.generated.swift').read_text()
project = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()


def block(source, start, end):
    i = source.index(start)
    return source[i:source.index(end, i) + len(end)]


# The lists, as the app reads them (GameRecommendationsList.swift): recommended/recommendations.json,
# its rules in order (the first rule that matches a game wins).
recs_path = root / 'recommended/recommendations.json'
recs = json.loads(recs_path.read_text())


def text(line):
    """A config line as the file writes it: a string, or an object for some builds only."""
    return line if isinstance(line, str) else line['line']


def builds(line):
    """The builds a config line is for: (first, last); a plain string is for every build."""
    if isinstance(line, str):
        return (0, 10 ** 9)
    return (line.get('minBuild', 0), line.get('maxBuild', 10 ** 9))


def swift_switches(rule):
    """The switches in the words of the Swift literals they replaced (2026-10-18), in
    GameRecommendation's argument order, so the expectations below stay word for word."""
    words = []
    for key in ('avx', 'nvidia', 'wineVCRT', 'fastSync', 'semaphoreFastPath', 'fpsMode', 'resolution', 'display'):
        if key not in rule.get('switches', {}):
            continue
        value = rule['switches'][key]
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        elif isinstance(value, int):
            value = str(value)
        elif key == 'resolution' and value == 'screen@720':
            value = 'GameRecommendation.screen720'
        else:
            value = json.dumps(value)
        words.append(f'{key}: {value}')
    return ', '.join(words)


presets = {}
for r in recs['rules']:
    assert set(r) <= {'id', 'title', 'version', 'minBuild', 'match', 'config', 'smallRAMConfig', 'switches', 'notes'}, r['id']
    assert set(r.get('switches', {})) <= {'avx', 'nvidia', 'wineVCRT', 'fastSync', 'semaphoreFastPath', 'fpsMode', 'resolution',
                                          'display'}, r['id']
    presets[r['id']] = dict(title=r['title'], version=r['version'], lines=[text(line) for line in r['config']],
                            small=[text(line) for line in r.get('smallRAMConfig', [])], switches=swift_switches(r),
                            line_builds=[builds(line) for line in r['config']],
                            small_builds=[builds(line) for line in r.get('smallRAMConfig', [])],
                            match=r.get('match'))
# Build 622 (owner, 2026-10-28): the heavy games BCD runs get a 30 FPS limit at first sight (the player can change it) and,
# as their last line from build 622 on, the one-time graphics tip before the first start (BCDGraphicsTip). Checked here;
# the line pins below are the lists before the tip.
HEAVY = ('rdr2-steam', 'rdr2-other', 'gta5e-steam', 'gta5e-other', 'gta5l-steam', 'gta5l-other', 'ghost-of-tsushima', 'god-of-war',
         'horizon-zero-dawn', 'horizon-forbidden-west', 'ratchet-clank-rift-apart', 'marvels-spider-man-remastered',
         'marvels-spider-man-2', 'marvels-spider-man-miles-morales', 'crysis-remastered', 'crysis-2-remastered', 'crysis-3-remastered',
         'batman-arkham-knight', 'reanimal')   # round 625 (owner's items 12 and 14): the same recipe, no forced resolution
for r in recs['rules']:
    tips = [line for line in r['config'] + r.get('smallRAMConfig', []) if 'low-graphics-tip' in (line if isinstance(line, str) else line['line'])]
    if r['id'] in HEAVY:
        assert tips == [{'line': 'low-graphics-tip = 1', 'minBuild': 622}] and r['config'][-1] == tips[0], (r['id'], tips)
        assert r['switches'].get('fpsMode') == 3, (r['id'], 'a heavy game starts at 30 FPS')
        assert presets[r['id']]['lines'][-1] == 'low-graphics-tip = 1' and presets[r['id']]['line_builds'][-1] == (622, 1000000000)
        presets[r['id']]['lines'].pop(); presets[r['id']]['line_builds'].pop()
    else:
        assert not tips, (r['id'], 'the graphics tip is the heavy games\' only')
assert 'ConfigOption(key: "low-graphics-tip"' in catalog and '"Windows, display & input"' in catalog.split('ConfigOption(key: "low-graphics-tip"')[1].split('\n')[0]
assert 'static let key = "low-graphics-tip"' in apply_src and 'enum BCDGraphicsTip' in apply_src
# The tip comes before the start: every start of BCD's interface goes through begin(), the front end shows it, OK starts the game.
_live = (app / 'BCDLiveStore.swift').read_text()
_front = (app / 'BCDLiveFrontEnd.swift').read_text()
_start = _live[_live.index('    private func start(_ game: BCDGame) {'):_live.index('    /// A start: a heavy game\'s one-time graphics tip first')]
assert _start.count('begin(entry, launch)') == 4 and 'launch(entry)' not in _start.replace('begin(entry, launch)', ''), _start
assert 'if BCDGraphicsTip.due(entry) {' in _live and 'BCDGraphicsTip.markShown(entry)' in _live and 'launch?(entry)' in _live
assert '.alert(BCDGraphicsTip.title, isPresented:' in _front and 'Button("OK") { live.graphicsTipAnswered() }' in _front
assert 'Very Low' in apply_src and 'AMD FSR' in apply_src and 'Balanced or ' in apply_src and '30 FPS' in apply_src
assert list(presets) == ['rdr2-steam', 'rdr2-other', 'gta5e-steam', 'gta5e-other', 'gta5l-steam', 'gta5l-other', 'ghost-of-tsushima', 'god-of-war',
                         'horizon-zero-dawn', 'horizon-forbidden-west', 'ratchet-clank-rift-apart', 'crysis-remastered',
                         'crysis-2-remastered', 'crysis-3-remastered', 'msfs-2024', 'counter-strike-2', 'warcraft-2-bne',
                         'breakneck', 'spelunky-2', 'albion-online', 'megabonk', 'no-im-not-a-human', 'over-the-hill', 'fort-solis', 'dragon-ball-sparking-zero', 'final-fantasy-vii-steam', 'animal-well', 'hogwarts-legacy', 'metal-gear-solid-v', 'stellar-blade-demo', 'star-wars-galactic-racer', 'minecraft-dungeons-2', 'denshattack', 'final-fantasy-resonance-demo', 'no-rest-for-the-wicked', 'quaver', 'dying-light', 'beamng-drive', 'monster-hunter-stories-3-trial', 'pwaat', 'lego-party', '1000xresist', 'american-truck-simulator', 'marvels-spider-man-miles-morales', 'diablo-ii-resurrected', 'clair-obscur-expedition-33', 'gta4-steam', 'gtasa-de-steam', 'unrailed-2-back-on-track', 'resident-evil-2', 'resident-evil-7', 'blazblue-entropy-effect', 'dark-souls-iii', 'marvels-spider-man-remastered', 'marvels-spider-man-2', 'raidou-remastered', 'yakuza-like-a-dragon', 'batman-arkham-city-goty', 'metal-gear-solid-peace-walker', 'lonely-mountains-snow-riders', 'trails-in-the-sky-1st-chapter', 'subnautica-2', 'rdr1-steam', 'resident-evil-requiem', 'resident-evil-requiem-demo', 'resident-evil-6', 'resident-evil-revelations-2',
                         'bioshock-infinite-epic', 'danganronpa-trigger-happy-havoc', 'life-is-strange-remastered', 'life-is-strange-2', 'project-zomboid', 'spiderheck', 'rounds', 'plateup', 'final-fantasy-tactics-ivalice-chronicles', 'age-of-empires-ii-hd', 'fallout-new-vegas', 'digimon-story-cyber-sleuth', 'final-fantasy-xv-demo', 'borderlands-3', 'zenless-zone-zero', 'jetboard-joust', 'sword-and-fairy-3', 'dead-as-disco', 'tung', 'darksiders-ii-deathinitive', 'nfs-most-wanted', 'nfs-underground-2', 'sekiro-shadows-die-twice', 'mudrunner', 'indivisible', 'terraria', 'risk-of-rain-2', 'jackal', 'stardew-valley', 'counter-strike-source', 'chuchel', 'botanicula', 'fallout-4', 'skyrim-special-edition', 'lego-batman-the-videogame', 'silent-hunter-3', 'just-cause-3', 'glyphica-typing-survival', 'grand-theft-auto-iii', 'postal-2', 'hotline-miami', 'max-payne-2', 'pico-park', 'dont-starve-together', 'a-hat-in-time', 'the-adventures-of-elliot', 'forza-horizon-4', 'tiny-tinas-wonderlands', 'far-cry-3', 'barotrauma', 'slay-the-spire-2', 'resident-evil-3', 'marvel-cosmic-invasion', 'haste', 'big-walk', 'ball-x-pit', 'just-volleyball-demo', 'farming-simulator-22-epic', 'max-payne-3', 'f1-2020', 'need-for-speed-the-run', 'bloons-td-6',
                         'control', 'no-more-room-in-hell', 'red-faction', 'from-the-darkness', 'metal-gear-solid-4-master-collection', 'mycopunk', 'arctic-awakening', 'supermarket-together', 'robocop-rogue-city', 'blasphemous', 'nwjs-game-exe', 'kerbal-space-program', 'mafia-2', 'fallout-3', 'overlord-ii', 'rise-of-the-tomb-raider', 'lucieneslife', 'ubisoft-connect', 'unreal-gold', 'repo', 'osu', 'outlast', 'satisfactory', 'the-witcher-3', 'yakuza-6-the-song-of-life', 'euro-truck-simulator-2', 'gal-gun-2', 'hello-neighbor', 'borderlands-goty-enhanced', 'dead-space-2023', 'schedule-i', 'dokimon-quest', 'sonic-adventure-dx', 'requiem-avenging-angel', 'friends-vs-friends', 'fallout-4-other', 'skyrim-special-edition-other', 'wrc-9', 'doki-doki-literature-club',   # round 624
                         'outlast-2', 'batman-arkham-knight', 'dead-rising', 'reanimal', 'pragmata', 'what-lies-in-the-multiverse', 'muv-luv-photonflowers',   # round 625
                         'standard'], list(presets)

catalog_keys = set(re.findall(r'ConfigOption\(key: "([^"]+)"', catalog))
for ident, p in presets.items():
    assert p['version'] >= 1, ident
    # The owner, 2026-10-18: the first line says BCD (it said "# Madeira's recommended settings for").
    assert p['lines'][0].startswith("# BCD's recommended settings for ") and p['lines'][0].endswith('.'), (ident, p['lines'][0])
    assert p['lines'][1] == "# Reset to Recommended, at the bottom of the game's settings, brings them back.", (ident, p['lines'][1])
    for lines, spans in ((p['lines'][2:], p['line_builds'][2:]), (p['small'], p['small_builds'])):
        seen = {}
        for line, span in zip(lines, spans):
            assert line == line.strip() and not line.startswith('#'), (ident, line)
            key, sep, value = line.partition(' = ')
            assert sep and key and value, (ident, line)
            # A key twice only when the two lines are for builds that do not overlap (e.g. 577-593 and 594 on, 2026-10-23):
            # every build then writes the key once.
            assert all(span[1] < other[0] or other[1] < span[0] for other in seen.get(key, [])), (ident, 'twice', key)
            seen.setdefault(key, []).append(span)
            if key.startswith('env.'):
                assert re.fullmatch(r'env\.[A-Za-z_][A-Za-z0-9_]*', key), (ident, key)
            else:
                assert key in catalog_keys, (ident, 'not a key Madeira reads', key)
            # Madeira's own settings only: nothing that loads a replaced Social Club, except rdr2-other's
            # owner-verified local DLL lines from build 616 (2026-10-27). SocialClubHelper.exe
            # may be named as a program in a list (env.DXMT_SMALL_RINGS), never in a DLL override.
            named = line.lower()
            if key != 'env.WINEDLLOVERRIDES':
                named = named.replace('socialclubhelper.exe', '')
            if ident != 'rdr2-other':
                assert 'socialclub' not in named and 'MADEIRA_DLL_LOCAL' not in line, (ident, line)
# The owner, 2026-10-18: nothing BCD writes into a game's config says Madeira: no list line (comments
# included; env.MADEIRA_* names are not the name), no title, and not the header GameProfile.set writes
# into a game's empty file.
for ident, p in presets.items():
    for line in p['lines'] + p['small'] + [p['title']]:
        assert 'Madeira' not in line, (ident, line)
header = block(profiles, '        if lines.isEmpty { lines = [', ']')
assert 'Madeira' not in header and '# BCD settings for' in header, header
print('PASS: forty-six lists in order, each starting "# BCD\'s recommended settings for", no key twice, every non-env key in '
      'the settings catalog, no Social Club DLL lines, "Madeira" in no line, title or GameProfile header')

# Red Dead Redemption 2 from Steam: the owner's list of 2026-10-09 (build 463), line for line,
# plus the lines of build 466's test (version 2), build 467's (version 3), build 470's (version 4) and
# build 472's (version 5: pool-mid), build 475's (version 6: ring-share), build 476's (version 7:
# swap-images, small DXMT rings for the launcher and Social Club, the ExecuteIndirect probe off)
# and build 480's (version 9: xinput1_4.dll loaded with the D3D12 device, the pool back to 128 MB of
# image headroom after 479's 192 starved the game's code buffer, the orphan-lock reaper stamped-only)
# and build 514's (version 10: the VC++ setup's elevated engine is not started; it crashed with a lock
# held and the first install waited forever).
rdr2 = '''d3d12-caps-log = 2
d3d12-shader-pack = 1
replay-split = 1
ring-share = 1
pool-low = 1
pool-mid = 1
pool-page-fit = 1
pool-split = 1
vram-mb = 2304
swap-mb = 8192
swap-mode = 2
swap-min-mb = 1
swap-images = 1
pso-warm = 2
fence-chain = 6
avail-phys = 1
indirect-fast = 1
ind-probe = 0
fullscreen-window = 1
env.WINE_D3D_CONFIG = renderer=no3d
env.FEX_VECTORTSOENABLED = 0
env.MADEIRA_BAND_CENSUS = 1
env.MADEIRA_RDR2_VA_HOLD = 1
env.MADEIRA_SWAP_RESERVE_MAX_MB = 9216
env.MADEIRA_THREAD_STACK_SPILL = 1
env.MADEIRA_SMALL_STACK_EXES = RDR2.exe
env.MADEIRA_D3D11_SRC = 1
env.MADEIRA_DOCK_GAME_SCM = 1
env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;RDR2.exe
env.MADEIRA_DXGI_SRC = 1
env.MADEIRA_DXGI_BUDGET_EVENTS = 1
env.DXMT_WSI_MONITOR_IDENTITY = 1
env.DXMT_WSI_MODE_TABLE = 1
env.DXMT_SMALL_RINGS = Launcher.exe;SocialClubHelper.exe
env.MADEIRA_EXECREQ_LEAVE = 1
env.MADEIRA_FEX_AVX = 0
env.MADEIRA_LOCK_ORPHAN = stamped
env.MADEIRA_PIN_GRAPHICS_DLLS = 1
env.MADEIRA_PRELOAD_DLLS = xinput1_4.dll
env.MADEIRA_POOL_HEAD_RESERVE_MB = 128
env.MADEIRA_POOL_LOW_IMAGES = 1
env.MADEIRA_POOL_RECYCLE_IMAGES = 1
env.MADEIRA_SC_PA_POOLS = 2
env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler;.be\\vc_redist
env.MADEIRA_TOUCH_MOUSE = 1
env.MADEIRA_WOW_MIN_FREE_GB = 2
env.MADEIRA_X64_GRAPHICS_ENTRY = 1
env.MADEIRA_X64_IMAGE_NOCOPY = 1
env.WINEDLLOVERRIDES = video64=
env.MADEIRA_DEVICE_STATS = 1
env.MADEIRA_METAL_HUD_MAIN = 1'''.split('\n')
assert presets['rdr2-steam']['lines'][2:] == rdr2, 'the RDR2 list differs from the owner\'s'
assert presets['rdr2-steam']['version'] == 12, presets['rdr2-steam']['version']   # 11 (2026-10-21): env.MADEIRA_FEX_AVX = 0; 12: 30 FPS + tip (build 622)
assert presets['rdr2-steam']['switches'] == ('avx: false, nvidia: false, wineVCRT: false, fastSync: true, '
                                             'semaphoreFastPath: false, fpsMode: 3, resolution: GameRecommendation.screen720, display: "fit"'), presets['rdr2-steam']['switches']
assert 'static let screen720 = "screen@720"' in lists_src and 'resolution == Self.screen720 ? "this screen\'s shape at 720 lines" : resolution' in lists_src
# Red Dead Redemption 2 outside Steam (owner's request, 2026-10-12, after GTA V Enhanced's two lists): the
# Steam copy's lines without the two only Madeira Dock reads, plus the ec-hook reporter off, which GTA V
# Enhanced's other copy has; the same switches.
rdr2_other = presets['rdr2-other']
dock_only = ['env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;RDR2.exe']
assert all(line in rdr2 for line in dock_only), 'the Dock lines are the Steam copy\'s'
# The AVX pin (v11 of the Steam list, 2026-10-21: auto-tune's fex-avx-on kept AVX on in the Rockstar Launcher's
# process only and every such session failed at the Launcher's service connection) is the Steam lists' alone.
avx_pin = 'env.MADEIRA_FEX_AVX = 0'
assert avx_pin in rdr2 and avx_pin in presets['gta5e-steam']['lines'] and avx_pin in presets['gta5l-steam']['lines']
# Round 624 (agent R, S23): GTA V Enhanced outside Steam (v4) carries the Rockstar-chain lines of the Steam copy, the AVX pin with them
# (every start of it stopped at the same launcher before; gta5l-other has had it since its own list). RDR2 outside Steam keeps AVX
# as it was: a copy of it played with its list (W9AC45).
assert avx_pin not in rdr2_other['lines'] and avx_pin in presets['gta5e-other']['lines']
_rdr2_other_base = [line for line in rdr2 if line not in dock_only and line != avx_pin]
_wdo_idx = _rdr2_other_base.index('env.WINEDLLOVERRIDES = video64=')
assert rdr2_other['lines'][2:] == (
    _rdr2_other_base[:_wdo_idx + 1]
    + ['env.WINEDLLOVERRIDES = video64=;socialclub=n,b;version=n,b', 'env.MADEIRA_DLL_LOCAL = socialclub.dll=version.dll']
    + _rdr2_other_base[_wdo_idx + 1:]
    + ['env.MADEIRA_EC_HOOK_TRACE = 0']
), rdr2_other['lines']
assert rdr2_other['version'] == 3 and rdr2_other['switches'] == presets['rdr2-steam']['switches'], rdr2_other
assert 'env.MADEIRA_EC_HOOK_TRACE = 0' in presets['gta5e-other']['lines']
# Every other game (owner's decision, 2026-10-12): the ec-hook reporter off, nothing else, no switches.
standard = presets['standard']
assert standard['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'] and standard['switches'] == '' and standard['version'] == 1, standard
assert 'if size != GameRecommendation.screen720 { entry.resolution = size }\n            else if let lines720 = ResolutionChoices.screen720Value { entry.resolution = lines720 }' in apply_src
gta = presets['gta5e-steam']
assert 'env.MADEIRA_CHILD_ARGS = PlayGTAV.exe -nobattleye' in gta['lines']
assert 'env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;GTA5_Enhanced.exe' in gta['lines']
assert 'semaphoreFastPath: true, fpsMode: 3' in gta['switches'] and 'nvidia: true' in gta['switches']   # 40 -> 30 FPS (owner, build 622)
assert 'env.MADEIRA_FASTSYNC_SEM = 1' in presets['gta5e-other']['lines']
# Version 2 of both GTA lists (2026-10-12): the orphan-lock reaper's stamped mode, as RDR2 and
# Horizon Zero Dawn have. The default mode released a busy SocialClubHelper.exe lock at Story Mode.
for ident in ('gta5e-steam', 'gta5e-other', 'rdr2-steam', 'rdr2-other', 'horizon-zero-dawn'):
    assert 'env.MADEIRA_LOCK_ORPHAN = stamped' in presets[ident]['lines'], ident
# Version 3 of the Steam list (build 514): the VC++ setup's elevated engine is not started, as for RDR2.
# steam 4 (2026-10-21): env.MADEIRA_FEX_AVX = 0
# other 4 (round 624): the Rockstar-chain lines of the Steam copy and env.MADEIRA_STEAM_IDENTITY = none (minBuild 624).
assert presets['gta5e-steam']['version'] == 5 and presets['gta5e-other']['version'] == 4, \
    (presets['gta5e-steam']['version'], presets['gta5e-other']['version'])
for ident in ('rdr2-steam', 'rdr2-other', 'gta5e-steam'):
    assert 'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler;.be\\vc_redist' in presets[ident]['lines'], ident
# GTA V Legacy (Steam) v1: the owner's 2026-10-15 lines; DirectX 11, so no D3D12 lines and no DualSense mode.
legacy = presets['gta5l-steam']
# v2 (owner, build 547): no vram-mb and no swap-mb in the list; a device under 12 GB gets the 3 GB RAM swap.
assert legacy['version'] == 4 and legacy['title'] == 'GTA V Legacy (Steam)', legacy   # 3: env.MADEIRA_FEX_AVX = 0; 4: 30 FPS + tip
assert not any(line.startswith(('vram-mb', 'swap-mb')) for line in legacy['lines']), legacy['lines']
assert legacy['small'] == ['swap-mb = 3072'], legacy['small']
assert 'static var smallRAMDevice: Bool { deviceRAMBytes < 10 << 30 }' in lists_src
# Round 624 (agent E1): Euro Truck Simulator 2 gets the BC texture clamp on a device under 10 GB only (2MJ7AZ, K299DR: killed at the
# limit of an 8 GB phone while loading 1.36 GB of BC textures).
assert [ident for ident, p in presets.items() if p['small']] == ['gta5l-steam', 'gta5l-other', 'metal-gear-solid-4-master-collection', 'euro-truck-simulator-2', 'outlast-2']
# Round 625 (agent L4 S2-a; XGFZMC, N3RS4C: killed at 4076 and 4090 of 4096 MB while an A16 expanded the game's BC textures): METAL GEAR
# SOLID 4 v2 gets the blanket clamp on a device under 10 GB only; its v1 lines (the launcher's wintypes, the 578 MB program without a pool
# copy) are unchanged. The clamp is the line auto-tune's rule mgs4-bc-decode-memory-kill gives, so the list key blocks nothing new.
_mgs4 = presets['metal-gear-solid-4-master-collection']
assert _mgs4['version'] == 2 and _mgs4['small'] == ['dxmt = d3d11.mipClampBC=1'] and _mgs4['small_builds'] == [(577, 1000000000)], _mgs4
assert _mgs4['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'], _mgs4['lines']
assert presets['euro-truck-simulator-2']['small'] == ['dxmt = d3d11.mipClampBC=1'] and presets['euro-truck-simulator-2']['small_builds'] == [(577, 1000000000)]
# Round 625 (owner's item 10, agent D2 S12; YAYHHE, 0XAKVY): Outlast 2's selective clamp, on a device under 10 GB only; it acts only within
# 1536 MB of the process limit, so in practice on 4 GB-limit phones. Nothing else in the list. Given by DXMT's environment switch
# MADEIRA_MIP_CLAMP_AUTO (build 625 in lists), never as a dxmt line: a list key blocks an auto-tune rule with the same key, and the
# game's rule outlast2-bc-decode-memory-kill gives dxmt = d3d11.mipClampBC=1 after a kill (agent L4 2.6: with a dxmt list line the
# engine passed the clamp over and tried the swap tier, which cannot help a GPU-memory kill).
assert presets['outlast-2']['small'] == ['env.MADEIRA_MIP_CLAMP_AUTO = 1'] and presets['outlast-2']['small_builds'] == [(625, 1000000000)]
_spec = __import__('importlib.util').util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
_tune = __import__('importlib.util').util.module_from_spec(_spec)
_spec.loader.exec_module(_tune)
_o2_rule = next(r for r in _tune.RULES if r['id'] == 'outlast2-bc-decode-memory-kill')
assert _o2_rule['lines'] == ['dxmt = d3d11.mipClampBC=1'], _o2_rule['lines']
_o2_row = next(r for r in recs['rules'] if r['id'] == 'outlast-2')
assert not any(text(x).split('=')[0].strip() == 'dxmt' for x in _o2_row['config'] + _o2_row.get('smallRAMConfig', [])), 'no dxmt list line for Outlast 2: it would block its rule'
assert presets['outlast-2']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'] and presets['outlast-2']['version'] == 1, presets['outlast-2']
# Round 625 (the owner's item 26; T3CXNQ, 4FD24J): the two 32-bit games that stopped for want of mfc140u.dll take Microsoft's 32-bit MFC
# (downloaded in Settings) from build 625; nothing else in their lists, and no other row sets the switch.
for _id in ('what-lies-in-the-multiverse', 'muv-luv-photonflowers'):
    assert presets[_id]['version'] == 1 and presets[_id]['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_MFC32 = 1'] \
        and presets[_id]['line_builds'][-1] == (625, 1000000000), presets[_id]
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_MFC32' in json.dumps(r['config'])) == ['muv-luv-photonflowers', 'what-lies-in-the-multiverse']
# Round 625 (agent L4 S3-a, NGEDJT): PRAGMATA's 372 MiB program has its relocations stripped; the window for it at 0x140000000 and no pool
# copy are the two lines auto-tune kept, from the first start. Its stop in its own start-up code is a known verdict (pragmata-startup-wild-read).
assert presets['pragmata']['version'] == 1 and presets['pragmata']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 400',
       'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['pragmata']['line_builds'][3:] == [(575, 1000000000), (576, 1000000000)], presets['pragmata']
# GTA V Legacy outside Steam (Epic, Rockstar's store; owner 2026-10-24): the Steam list's lines without its two Dock lines, plus
# no Steam identity from build 598 (error 113 in every Epic start of build 592); the same RAM swap and switches.
_legacy_other = presets['gta5l-other']
_dock = ('env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;GTA5.exe')
assert _legacy_other['version'] == 2 and _legacy_other['title'] == 'GTA V Legacy (not Steam)', _legacy_other
assert _legacy_other['lines'][2:] == [l for l in legacy['lines'][2:] if l not in _dock] + ['env.MADEIRA_STEAM_IDENTITY = none'], _legacy_other['lines']
assert _legacy_other['line_builds'][-1] == (598, 1000000000) and set(_legacy_other['line_builds'][:-1]) == {(0, 1000000000)}
assert _legacy_other['small'] == legacy['small'] and _legacy_other['switches'] == legacy['switches']
assert 'env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;GTA5.exe' in legacy['lines']
assert 'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler;.be\\vc_redist' in legacy['lines']
assert 'env.DXMT_WSI_MODE_TABLE = 1' in legacy['lines'] and 'env.MADEIRA_EC_HOOK_TRACE = 0' in legacy['lines']
assert not any(line.startswith(('d3d12-', 'pso-warm', 'env.MADEIRA_PAD_MODE', 'env.MADEIRA_D3D11_SRC', 'env.MADEIRA_GUEST_LOG'))
               for line in legacy['lines']), legacy['lines']
assert legacy['switches'] == ('avx: false, nvidia: true, wineVCRT: false, fastSync: true, semaphoreFastPath: true, fpsMode: 3, '
                              'display: "fit"'), legacy['switches']
assert 'resolution: "1280x720"' in presets['ghost-of-tsushima']['switches'] and 'avx: true' in presets['ghost-of-tsushima']['switches']
assert 'resolution: "1920x1080"' in presets['god-of-war']['switches']
# Horizon Zero Dawn, still being brought up: build 477's test list (register spaces other than 0,
# DXGI lists the virtual monitor's own modes so the game can pick this screen's resolution, its
# window is made foreground while none is, XInput pads reach Windows.Gaming.Input, and the
# shaders the converter refuses are retried with sized ranges, remembered and replaced by
# placeholder pipelines; pipelines are warmed in the background). Version 5 (build 480): the
# orphan-lock reaper releases only locks a live thread has stamped. Version 6 (build 481): shader
# libraries only for pipelines in use, rectangle clears, the game's memory in the swap file
# (broad, from 1 MB), and no pso-warm (its queue held 24,570 pipelines when iOS closed the game).
# Version 7 (build 483): the skin-check and view-census diagnostics for its compute-skinned
# characters (missing faces, missing hair, spikes every few frames on 481, gpu-sync or not).
# Version 8 (build 484): every candidate fix at once, one line each, to be halved if it helps:
# no wave operations, raw views readable as typed, ShaderAtomic on R32 typed UAVs, and FEX's
# x86 ordering for vector, memcpy and split-lock accesses and for images with volatile metadata.
# Version 9 (build 485): none of those helped (owner, 484 test), so they are gone again;
# typed buffer views start at Apple's 16-byte boundary, and skin-check traces whole frames and
# dumps the compute skinning's data, comparing what the GPU read with what the CPU wrote.
# Version 10 (build 486): the characters came out whole on 485; typed views start exactly at
# their first element where the probe allows (plants and lighting still flickered), and the
# skinning diagnostics are gone again (view-census stays, it counts views left with an offset).
# Version 11 (build 487): nothing changed on 486 (the GPU rounds a texture buffer's start down
# to 16 bytes; walls, trees and plants vanished for single frames); typed views of upload
# memory off a 16-byte boundary read an aligned copy, a queue's wait holds until the awaited
# work is committed, and vis-trace logs every frame's draws and the culling pyramid.
# Version 12 (build 489): desc-guard logs descriptors rewritten while a batch on the GPU uses them.
# Version 13 (build 490): the readback buffers (the culling pyramid) on Metal's own memory, not the swap tier.
# Version 14 (build 491): single constant buffers of descriptor tables read when the draw is recorded.
# Version 15 (build 492): the game rendered right on 491; the list without fence-strict, static-cbv,
# readback-swap and the trace diagnostics stayed clean, and without typed-view-shadow it broke again.
# Version 16 (build 494): + quiet-log and MADEIRA_SRV_FGETPATH, for frame rate (493, 2026-10-11).
hzd = presets['horizon-zero-dawn']
# Horizon Forbidden West, version 1: build 517's eleventh try (2026-10-13 10:55), its first list. AVX on,
# frees released at once, its programs from their own image, the swap file from 64 KB, a 3 GB budget with
# budget events, HZD's typed-view alignment without the aligned copies, open render passes across lists,
# and the frame-rate switches (quiet-log, lazy shader libraries, eco, lean diagnostics, quiet fixmes).
# Version 2 (build 526's test): the aligned copies on (vegetation in the sky without them, 525) and
# discarded targets loaded again (black squares in FSR 3's generated frames with rp-discard, 525).
# Version 3 (build 528's test): vegetation still in the sky on 526; the census and skin-check
# diagnostics that found HZD's view, without quiet-log (it hides their lines).
# Version 4 (build 529's test): the GPU read what the CPU wrote (528), but 1,789 typed views were
# R32G32B32 with no Metal format: typed-view-rgb32 on; the diagnostics off and quiet-log back.
# Version 5 (build 534): mip0-full, the fix for FSR 3's black squares (its inpainting pyramid, MipLevels 0;
# build 533: the squares are gone).
hfw = presets['horizon-forbidden-west']
assert hfw['lines'][2:] == ['vram-mb = 3072', 'swap-mb = 8192', 'swap-mode = 2', 'swap-min-mb = 1', 'swap-images = 1',
                            'typed-view-align = 16', 'typed-view-shadow = 1', 'typed-view-rgb32 = 1', 'mip0-full = 1', 'rp-carry = 1',
                            'quiet-log = 1', 'pso-lazy-libs = 1', 'eco = 1',
                            'env.MADEIRA_DXGI_SRC = 1', 'env.MADEIRA_DXGI_BUDGET_EVENTS = 1', 'env.MADEIRA_SWAP_MIN_KB = 64',
                            'env.MADEIRA_SWAP_RESERVE_MAX_MB = 9216', 'env.MADEIRA_INPUT_FOREGROUND = 1',
                            'env.MADEIRA_WGI_HOST_PADS = 1', 'env.MADEIRA_LOCK_ORPHAN = stamped', 'env.MADEIRA_SRV_FGETPATH = 1',
                            'env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_FREE_DELAY_MS = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1',
                            'env.MADEIRA_PAD_MODE = hid', 'env.MADEIRA_LEAN_DIAG = 1',
                            'env.WINEDEBUG = err+all,err-virtual,fixme-all'], hfw['lines']
assert hfw['version'] == 6 and hfw['switches'] == ('avx: true, nvidia: true, wineVCRT: false, fastSync: true, fpsMode: 3, '
                                                   'display: "fit"'), hfw
assert hzd['lines'][2:] == ['dxbc-register-spaces = 1', 'msc-unbounded-retry = 1', 'msc-fail-memo = 1',
                            'pso-placeholder = 1', 'pso-lazy-libs = 1', 'clear-rects = 1',
                            'typed-view-align = 16', 'typed-view-shadow = 1', 'view-census = 1',
                            'quiet-log = 1',
                            'swap-mb = 6144', 'swap-mode = 2', 'swap-min-mb = 1',
                            'env.DXMT_WSI_MONITOR_IDENTITY = 1', 'env.DXMT_WSI_MODE_TABLE = 1',
                            'env.MADEIRA_INPUT_FOREGROUND = 1', 'env.MADEIRA_WGI_HOST_PADS = 1',
                            'env.MADEIRA_LOCK_ORPHAN = stamped', 'env.MADEIRA_SRV_FGETPATH = 1',
                            'env.WINEDEBUG = fixme-input',
                            # Version 17 (2026-10-23, build 591): the Dock start's MFC link; a Dock session of this game cannot start without it
                            'env.MADEIRA_MFC_DOCK = 1'], hzd['lines']
rca = presets['ratchet-clank-rift-apart']
mm = presets['marvels-spider-man-miles-morales']
smr = presets['marvels-spider-man-remastered']   # 2026-10-23: the owner's latest Spider-Man settings (quiet-log, mip0-full, swap lines)
sm2 = presets['marvels-spider-man-2']
mcd2 = presets['minecraft-dungeons-2']   # 2026-10-24 (thebcd69/bcd-games#16 by le0-VV): the PR's recipe, quiet-log included
assert not any(line.startswith('env.MADEIRA_SRV_FGETPATH')
               for p in presets.values() if p not in (hzd, hfw, rca, sm2) for line in p['lines']), \
    'the build 494 fgetpath switch is the two Horizon games\', Rift Apart\'s and Spider-Man 2\'s only'
assert not any(line.startswith('quiet-log')
               for p in presets.values() if p not in (hzd, hfw, rca, mm, smr, sm2, mcd2) for line in p['lines']), \
    'quiet-log is the two Horizon games\', Rift Apart\'s, the three Spider-Man games\' and Minecraft Dungeons II\'s only'
assert hzd['version'] == 18 and hzd['switches'] == 'avx: false, nvidia: false, wineVCRT: false, fastSync: true, fpsMode: 3, display: "fit"', hzd
assert not any(line.startswith(('d3d12-wave-ops', 'd3d12-raw-typed-views', 'typed-uav-atomic', 'ignore-volatile-metadata',
                                 'env.FEX_MEMCPYSETTSOENABLED = 1', 'env.FEX_STRICTINPROCESSSPLITLOCKS'))
               for ident, p in presets.items() if ident != 'osu' for line in p['lines']), 'the build 484 experiment is in no list'
# Round 625 (owner's item 16, agent A S13; osu! SV6C4C): osu! alone tries the ordered vector and block-copy accesses (the game page's
# "Compatibility" FEX preset) as an experiment, after its two diagnostics; nothing else of the build 484 experiment.
_osu = presets['osu']
assert _osu['version'] == 2 and _osu['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X86_FAULT_DUMP = 1', 'env.MADEIRA_GUEST_LOG = all',
                                                      'env.FEX_VECTORTSOENABLED = 1', 'env.FEX_MEMCPYSETTSOENABLED = 1'], _osu
assert _osu['line_builds'][-2:] == [(0, 1000000000), (619, 1000000000)], _osu['line_builds']
assert not any(line.startswith('clear-rects')
               for p in presets.values() if p is not hzd for line in p['lines']), 'clear-rects is Horizon Zero Dawn\'s only'
assert not any(line.startswith('pso-lazy-libs')
               for p in presets.values() if p not in (hzd, hfw, rca, sm2) for line in p['lines']), \
    'pso-lazy-libs is the two Horizon games\', Rift Apart\'s and Spider-Man 2\'s only'
assert not any(line.startswith(('skin-check', 'skin-dump', 'fence-strict', 'vis-trace', 'readback-far', 'desc-guard', 'readback-swap',
                                 'static-cbv', 'env.FEX_VECTORTSOENABLED = 1'))
               for ident, p in presets.items() if ident != 'osu' for line in p['lines']), 'the build 482-491 experiments are in no list'   # osu!: round 625, below
# Ghost of Tsushima, version 2 (build 492): Horizon Zero Dawn's proven typed-view fix, with the census.
# Version 3 (build 515, the owner's working list): the ec-hook reporter off, a 4 GB swap file, and
# thread stacks that may take the kernel's pick (a thread the game could not create froze it, 15:39).
got = presets['ghost-of-tsushima']
assert got['lines'][2:] == ['dxil-tess = 0', 'sampler-reduction = 3', 'ind-count = 6000', 'typed-view-align = 16',
                            'typed-view-shadow = 1', 'view-census = 1', 'env.MADEIRA_PAD_MODE = hid', 'swap-mb = 4096',
                            'env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_THREAD_STACK_SPILL = 1'], got['lines']
assert got['version'] == 4, got['version']   # 4: 30 FPS + tip (build 622)
assert got['switches'] == ('avx: true, nvidia: true, wineVCRT: false, fastSync: true, semaphoreFastPath: false, fpsMode: 3, '
                           'resolution: "1280x720", display: "fit"'), got['switches']
assert not any(line.startswith('typed-view-rgb32') for p in presets.values() if p not in (hfw, rca)
               for line in p['lines']), 'typed-view-rgb32 is HFW\'s and Rift Apart\'s only'
assert not any(line.startswith('mip0-full') for p in presets.values() if p not in (hfw, rca, mm, smr, sm2)
               for line in p['lines']), 'mip0-full is HFW\'s, Rift Apart\'s and the three Spider-Man games\' only'
# Ratchet & Clank: Rift Apart, version 1 (2026-10-14): not yet seen running (a tester's first try on 531 ended
# seconds after start with AVX off). HFW v5's general lines: no Decima typed-view alignment or copies, no
# rp-carry, no eco; AVX and NVIDIA on.
assert rca['lines'][2:] == ['vram-mb = 2048', 'swap-mb = 8192', 'swap-mode = 2', 'swap-min-mb = 1', 'swap-images = 1',
                            'typed-view-rgb32 = 1', 'mip0-full = 1', 'quiet-log = 1', 'pso-lazy-libs = 1', 'fence-early = 1',
                            'env.MADEIRA_DXGI_SRC = 1', 'env.MADEIRA_DXGI_BUDGET_EVENTS = 1', 'env.MADEIRA_SWAP_MIN_KB = 64',
                            'env.MADEIRA_SWAP_RESERVE_MAX_MB = 9216', 'env.MADEIRA_INPUT_FOREGROUND = 1',
                            'env.MADEIRA_WGI_HOST_PADS = 1', 'env.MADEIRA_LOCK_ORPHAN = stamped', 'env.MADEIRA_SRV_FGETPATH = 1',
                            'env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_FREE_DELAY_MS = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1',
                            'env.MADEIRA_PAD_MODE = hid', 'env.MADEIRA_LEAN_DIAG = 1', 'env.MADEIRA_JUMBO_HALF = 1',
                            'env.MADEIRA_CAGE_RELEASE = 1', 'env.MADEIRA_ALIAS_RELEASE = 1', 'env.LC_ALL = en_US.UTF-8',
                            'env.WINEDEBUG = err+all,err-virtual,fixme-all', 'env.MADEIRA_VA_PRESSURE_GAP_OK = 1'], rca['lines']
# Version 2 (build 550): env.MADEIRA_JUMBO_HALF, the owner's 549 try (22:07:15) ran out of 8 GB holes for
# the engine's arena probes after the intro videos. Rift Apart's only.
# Version 3 (build 550, 23:21:48, the game ran): env.MADEIRA_CAGE_RELEASE, the 8 GB V8 cage holdback goes to
# the guest band when it runs out (R6025 at 10-43 s before: secur32.dll found no VA). Rift Apart's only.
# Version 4 (build 553, 10:43:45 and 10:45:10: R6025 again): env.LC_ALL = en_US.UTF-8 back. v3 had left it
# out, and without it the game throws right after loading a non-English code page (857) (as on 550 22:49:00).
# Version 5 (build 555, 12:32:19: the save that froze on 553 loaded): env.MADEIRA_ALIAS_RELEASE, the JIT pool
# records of a released arena go with it (553 11:21:36 and 11:22:59 froze on the stale ones).
# Version 6 (owner, 2026-10-16, the game set aside): the owner's 558 run (15:58:00) line for line -- vram-mb 2048 and
# swap-mb 1024 as the owner ran them from 553 on, fence-early, and env.MADEIRA_VA_PRESSURE_GAP_OK (8 "not latched",
# 0 [steer], 5.45 GB peak, where 13:23 without it had 15 [steer] and 6.9 GB).
RCA_OWN = {'env.MADEIRA_JUMBO_HALF = 1', 'env.MADEIRA_CAGE_RELEASE = 1', 'env.MADEIRA_ALIAS_RELEASE = 1',
           'env.LC_ALL = en_US.UTF-8', 'fence-early = 1', 'env.MADEIRA_VA_PRESSURE_GAP_OK = 1'}
# Version 7 (owner, 2026-10-28): swap-mb = 8192; from build 622 swap-mb caps broad mode's disk for every commit (check-swap-cap.py),
# and 1024 would have kept in memory what the 2026-10-16 runs put on the disk (0.9-2.5 GB).
assert rca['version'] == 8, rca['version']   # 8: tip (build 622)
assert set(rca['lines'][2:]) - RCA_OWN - {'vram-mb = 2048', 'swap-mb = 8192'} < set(hfw['lines'][2:]), \
    'Rift Apart\'s lines are a subset of HFW\'s, apart from its own lines and its vram-mb / swap-mb values'
assert not any(line.startswith(('env.MADEIRA_JUMBO_HALF', 'env.MADEIRA_CAGE_RELEASE', 'env.MADEIRA_ALIAS_RELEASE',
                                'env.LC_ALL', 'fence-early', 'env.MADEIRA_VA_PRESSURE_GAP_OK')) for p in presets.values()
               if p is not rca for line in p['lines'] if not (p is sm2 and line in ('env.MADEIRA_JUMBO_HALF = 1', 'env.MADEIRA_CAGE_RELEASE = 1', 'env.MADEIRA_ALIAS_RELEASE = 1', 'env.LC_ALL = en_US.UTF-8', 'env.MADEIRA_VA_PRESSURE_GAP_OK = 1', 'fence-early = 2'))
               and not (p is presets['dying-light'] and line == 'env.LC_ALL = en_US.UTF-8')
               # round 624 (agent C, VCBB6X): The Witcher 3 asks for 32 GB in one piece and goes on with a NULL when refused; jumbo-half
               # (with its small-map step of build 624) gives it the half. Its only line of the six.
               and not (p is presets['the-witcher-3'] and line == 'env.MADEIRA_JUMBO_HALF = 1')), \
    'jumbo-half, cage-release, alias-release, LC_ALL, fence-early and va-pressure-gap are Rift Apart\'s and Spider-Man 2\'s only (LC_ALL: Dying Light\'s too; jumbo-half: The Witcher 3\'s too)'
# 2026-10-23 (owner: the list carries the latest Spider-Man settings, not running yet)
# v3 (owner, 2026-10-28): pso-warm = 2 (the 621 stutter in a new area was pipelines built at their first draw; the owner found it
# smooth with it), 30 FPS and the tip.
assert smr['version'] == 3 and smr['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'mip0-full = 1',
                                                    'quiet-log = 1', 'write-buffer-immediate = 1', 'pso-warm = 2'], smr
assert smr['switches'] == 'avx: false, nvidia: true, wineVCRT: false, fpsMode: 3, display: "fit"', smr['switches']
assert sm2['version'] == 8 and sm2['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1',
                                                    'env.MADEIRA_JUMBO_HALF = 1', 'env.MADEIRA_CAGE_RELEASE = 1',
                                                    'env.MADEIRA_ALIAS_RELEASE = 1', 'env.LC_ALL = en_US.UTF-8',
                                                    'env.MADEIRA_VA_PRESSURE_GAP_OK = 1', 'env.MADEIRA_SRV_FGETPATH = 1',
                                                    'env.MADEIRA_LEAN_DIAG = 1', 'env.MADEIRA_FREE_DELAY_MS = 0',
                                                    'env.MADEIRA_ECO_NATIVE = 1', 'env.FEX_TSOENABLED = 0',
                                                    'env.FEX_VECTORTSOENABLED = 0', 'env.FEX_MEMCPYSETTSOENABLED = 0',
                                                    'env.WINEDEBUG = err+all,err-virtual,fixme-all', 'mip0-full = 1',
                                                    'quiet-log = 1', 'pso-lazy-libs = 1', 'fence-early = 2',
                                                    'fence-poll-backoff = 64', 'fence-chain = 6', 'rp-carry = 1',
                                                    'swap-mb = 2048', 'swap-mode = 2', 'swap-min-mb = 16'] and sm2['switches'] == 'avx: true, nvidia: true, wineVCRT: false, fastSync: true, fpsMode: 3, display: "fit"', sm2
assert rca['version'] == 8 and rca['switches'] == ('avx: true, nvidia: true, wineVCRT: false, fastSync: true, fpsMode: 3, '
                                                   'display: "fit"'), rca
assert not any(line.startswith(('view-census', 'typed-view-align', 'typed-view-shadow'))
               for p in presets.values() if p not in (hzd, got, hfw) for line in p['lines']), \
    'the typed-view fix is HZD\'s, GoT\'s and HFW\'s only'
# Crysis Remastered, version 1 (build 528, 2026-10-13 22:08, the owner: it ran perfectly): the standard
# list's line with every switch off, at Fit.
crysis = presets['crysis-remastered']
assert crysis['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'], crysis['lines']
assert crysis['version'] == 2 and crysis['switches'] == 'avx: false, nvidia: false, wineVCRT: false, fpsMode: 3, display: "fit"', crysis
# Crysis 2 and 3 Remastered, version 1 (owner, 2026-10-15; not yet run with them): build 540's tries stopped with
# AVX off (Crysis 2) and in Microsoft's msvcp140 (Crysis 3), so AVX on, and Wine's C++ runtime for Crysis 3.
c = presets['crysis-2-remastered']
assert c['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'], c['lines']
assert c['version'] == 2 and c['switches'] == 'avx: true, nvidia: false, wineVCRT: false, fpsMode: 3, display: "fit"', c
# Crysis 3 Remastered, version 2 (547, 15:08:17): the same stop; the game's own x64 vcruntime140.dll next to Wine's
# ARM64EC msvcp140. env.MADEIRA_APP_DLL_SHADOW refuses that copy; Wine's C++ runtime switch off again.
c = presets['crysis-3-remastered']
assert c['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_APP_DLL_SHADOW = vcruntime140.dll'], c['lines']
assert c['version'] == 3 and c['switches'] == 'avx: true, nvidia: false, wineVCRT: false, fpsMode: 3, display: "fit"', c
# Round 624 (agent E2, S1): Outlast's OLGame.exe dies in msvcp100's start because Wine's ARM64EC msvcp100 calls the game's own x64 msvcr100
# (056DW5, 5KK967, ZEJDSW, 4GV864; every log of the game); its list takes Wine's msvcr100 and msvcp100 together.
assert presets['outlast']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_APP_DLL_SHADOW = msvcr100.dll;msvcp100.dll',
                                           'env.MADEIRA_D3D9_X64 = dxmt'], presets['outlast']['lines']
assert not any(line.startswith('env.MADEIRA_APP_DLL_SHADOW') for p in presets.values() if p is not c and p is not presets['outlast']
               for line in p['lines']), \
    'the app-local DLL shadow is Crysis 3 Remastered\'s and (round 624) Outlast\'s only'
# Microsoft Flight Simulator 2024, version 1 (for build 538): being brought up. Build 534 stopped in the loader
# (srvcli.dll missing, 2026-10-14 20:27); the owner's test settings: the standard line, AVX and NVIDIA on, Fit.
# Version 2: on 538 (21:20) the JIT pool ran out at CoherentGTCore.dll, so pure-x64 images get no pool copy.
# Version 3: on 538 (21:41) the game asked for a feature level 12_1 device; d3d12-fl-12-1 claims it (539, cancelled).
# Version 4: d3d12-sm-6-7 too; the game requires shader model 6.7 (540).
# Version 5: 540 stopped with the game's "FiberError"; env.MADEIRA_FIBER_TRACE = 1 logged the fiber calls (541's test).
# Version 6 (owner, 2026-10-15): the trace leaves the list; MSFS 2024's fibers are its own (547), the game is on hold.
msfs = presets['msfs-2024']
assert msfs['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'd3d12-fl-12-1 = 1',
                             'd3d12-sm-6-7 = 1'], msfs['lines']
assert not any(line.startswith('env.MADEIRA_FIBER_TRACE') for p in presets.values() for line in p['lines']), \
    'the fiber trace is a diagnostic, in no list'
assert not any(line.startswith(('d3d12-fl-12-1', 'd3d12-sm-6-7')) for p in presets.values() if p is not msfs for line in p['lines']), \
    'd3d12-fl-12-1 and d3d12-sm-6-7 are MSFS 2024\'s only'
assert msfs['version'] == 6 and msfs['switches'] == 'avx: true, nvidia: true, wineVCRT: false, display: "fit"', msfs
# Counter-Strike 2, version 1 (owner, 2026-10-16; not yet run): Direct3D 11 through DXMT like Crysis Remastered, the
# standard line, every switch off (CS2 needs SSE4.2, which FEX reports, and no AVX). Version 2 (owner's runs,
# 2026-10-20): the JIT pool shrank and filled while cs2.exe loaded; pool-split = 1 started it.
cs2 = presets['counter-strike-2']
assert cs2['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'pool-split = 1'], cs2['lines']
assert cs2['version'] == 2 and cs2['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', cs2
# Spelunky 2 and Albion Online's launcher, version 1 (2026-10-20 logs J2YJF8, BYBFCH, PGKTDD): the same full pool;
# the standard line plus the split pool, every switch off.
# Spelunky 2 version 2 (2026-10-21, logs XKGV37 / JBNAZ1): plus the two DXMT monitor lines RDR2's list uses.
# Spelunky 2 version 3 (round 579): NOCOPY instead of the split pool (JBNAZ1 got furthest with NOCOPY alone; the 558 MiB
# exe fits no split-pool region).
q = presets['spelunky-2']
assert q['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'env.DXMT_WSI_MONITOR_IDENTITY = 1',
                          'env.DXMT_WSI_MODE_TABLE = 1'], q['lines']
assert q['version'] == 3 and q['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', q
assert {'line': 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'minBuild': 576} in next(r for r in recs['rules'] if r['id'] == 'spelunky-2')['config']
for ident, extra, ver in (('albion-online', [], 1),):
    q = presets[ident]
    assert q['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'pool-split = 1'] + extra, (ident, q['lines'])
    assert q['version'] == ver and q['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', q
# Warcraft II BNE, version 1 (tester's build 559 logs, 2026-10-16): the standard line and plain RWX memory for its
# self-modifying blitters, every switch off.
wc2 = presets['warcraft-2-bne']
assert wc2['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WOW_RWX_PLAIN = 1'], wc2['lines']
assert wc2['version'] == 1 and wc2['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', wc2
# Breakneck, version 1 (the owner's build 562 runs, 2026-10-17): an old 32-bit Unity game; the same plain RWX memory
# for Mono's JIT chunks, every switch off.
bn = presets['breakneck']
assert bn['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WOW_RWX_PLAIN = 1'], bn['lines']
assert bn['version'] == 1 and bn['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', bn
# Round 624 (agent D2, PA7PQB): From the Darkness, a 32-bit Unity game whose Mono call-site patcher wrote through the store emulator
# 92,000 times a second (3.4 FPS), gets the plain RWX memory Aegis Defenders runs with.
assert presets['from-the-darkness']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WOW_RWX_PLAIN = 1'], presets['from-the-darkness']['lines']
assert not any(line.startswith('env.MADEIRA_WOW_RWX_PLAIN') for p in presets.values() if p not in (wc2, bn, presets['pwaat'], presets['from-the-darkness'])
               for line in p['lines']), \
    'plain RWX memory is Warcraft II\'s, Breakneck\'s and PWAAT\'s only'
print('PASS: RDR2 (Steam) is the owner\'s 463 list plus the 466, 467, 470, 472, 475, 476, 480 and 514 test lines and the AVX pin (v11), line for line with its switches (720 lines of this screen), and outside Steam the same without the Dock lines, with the ec-hook reporter off (v1); GTA V Enhanced keeps its tested lines plus the stamped orphan-lock mode (v2) and, from Steam, the VC++ engine block (v3) and the AVX pin (v4), GoW its tested lines; Horizon Zero Dawn has build 491\'s proven list plus build 494\'s frame-rate switches (v16), GoT the owner\'s build 515 list (v3); Horizon Forbidden West its eleventh try\'s list with the aligned copies on and discarded targets loaded (v4: float3 views get a copy too; v5: MipLevels 0 textures get their full chain); Rift Apart HFW\'s general lines with AVX and NVIDIA on (v1); GTA V Legacy from Steam the owner\'s DirectX 11 lines (v1), without vram-mb and swap-mb and with the 3 GB RAM swap only under 12 GB of RAM (v2) and the AVX pin (v3); Crysis Remastered the standard line with every switch off (v1); Crysis 3 Remastered with its own vcruntime140.dll refused (v2); MSFS 2024 the standard line with AVX and NVIDIA on (v1), pure-x64 images without a pool copy (v2), feature level 12_1 (v3), shader model 6.7 (v4), the fiber trace (v5), then without it (v6); Counter-Strike 2 the standard line with every switch off (v1), then the split pool (v2); Spelunky 2 and Albion Online the split pool (v1); Warcraft II BNE plain RWX memory (v1); Breakneck the same (v1); every other game the standard list (v1)')

# Megabonk v2 (round 620, thebcd69/bcd-games#18 and #25, tested by the reporter on build 614): the Windows.Gaming.Input pad and the real
# SuspendThread the IL2CPP GC needs when a match starts; the second only for build 620, which lets a downloaded list set it.
assert presets['megabonk']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1', 'env.MADEIRA_WGI_HOST_PADS = 1',
                                             'env.MADEIRA_REAL_SUSPEND = 1'] and presets['megabonk']['version'] == 2, presets['megabonk']
assert presets['megabonk']['line_builds'][-1] == (620, 1000000000), presets['megabonk']['line_builds']
assert presets['no-im-not-a-human']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1'] and presets['no-im-not-a-human']['version'] == 1, presets['no-im-not-a-human']
assert presets['over-the-hill']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1'] and presets['over-the-hill']['version'] == 1, presets['over-the-hill']
assert presets['fort-solis']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = dxva2'] and presets['fort-solis']['version'] == 1, presets['fort-solis']
assert presets['dragon-ball-sparking-zero']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = dxva2'] and presets['dragon-ball-sparking-zero']['version'] == 1, presets['dragon-ball-sparking-zero']
# Round 624 (agent B S6, MMJZ8K): FINAL FANTASY VII's .NET launcher shows its own exception box; the diagnostic logs the box's details pane.
# Round 624 (Discord: "NoDriver calling waveOutOpen"): v2 adds BCD's default WINEDEBUG with warn+winmm (err-virtual kept), build 624 on.
assert presets['final-fantasy-vii-steam']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WIN_TEXT = 1',
                                                           'env.WINEDEBUG = err+all,err-virtual,fixme-d3dcompiler,warn+winmm'] \
    and presets['final-fantasy-vii-steam']['version'] == 2 \
    and presets['final-fantasy-vii-steam']['line_builds'][-2:] == [(624, 1000000000), (624, 1000000000)], presets['final-fantasy-vii-steam']
assert presets['animal-well']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = xpsprint', 'present-lum = 1'] and presets['animal-well']['version'] == 2, presets['animal-well']
# v2 (round 624, agent E1, GBQRKH): the Epic build's program is 451.9 MiB, 3.7 MiB more than v1's 448 MB window; 512 MB holds both programs.
assert presets['hogwarts-legacy']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 512'] and presets['hogwarts-legacy']['version'] == 2, presets['hogwarts-legacy']
assert presets['metal-gear-solid-v']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 176'] and presets['metal-gear-solid-v']['version'] == 1, presets['metal-gear-solid-v']
assert presets['stellar-blade-demo']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 320', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['stellar-blade-demo']['version'] == 2, presets['stellar-blade-demo']
assert presets['star-wars-galactic-racer']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 496', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['star-wars-galactic-racer']['version'] == 2, presets['star-wars-galactic-racer']
assert presets['denshattack']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_VCRT_ARM64_KEY = 1'] and presets['denshattack']['version'] == 1, presets['denshattack']
# v3 (round 620, J4DADQ/TQ3VF6): the 474.1 MiB relocation-stripped game exe needs a 496 MB window at 0x140000000.
assert presets['final-fantasy-resonance-demo']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_VCRT_ARM64_KEY = 1', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'exe-window-mb = 496'] and presets['final-fantasy-resonance-demo']['version'] == 3, presets['final-fantasy-resonance-demo']
# Round 620 (2026-10-28): twelve new lists, each from the round's logs or a tester's report on GitHub (notes in the list). A line that
# needs build 620 (the gamingtcui DLL, the real-suspend switch a download may set) carries minBuild 620; the others reuse older keys.
_r620 = {
    'forza-horizon-4': ['env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'env.MADEIRA_OPTIN_DLLS = dxva2,gamingtcui'],
    'tiny-tinas-wonderlands': ['env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'env.MADEIRA_OPTIN_DLLS = sensapi', 'd3d12-pipeline-library = 1'],
    'far-cry-3': ['env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1'],
    'barotrauma': ['env.DOTNET_GCRegionRange = 400000000'],
    'slay-the-spire-2': ['env.DOTNET_GCRegionRange = 400000000'],
    'resident-evil-3': ['env.MADEIRA_WG_64BIT = 1', 'env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod'],
    'marvel-cosmic-invasion': ['env.MADEIRA_WGI_HOST_PADS = 1'],
    'haste': ['env.MADEIRA_WGI_HOST_PADS = 1', 'dxmt = d3d11.mipClampBC=1'],
    'big-walk': ['env.MADEIRA_REAL_SUSPEND = 1'],
    'ball-x-pit': ['env.MADEIRA_REAL_SUSPEND = 1'],
    'just-volleyball-demo': ['d3d12-typed-uav-load = 1'],
    'farming-simulator-22-epic': ['env.BCD_EPIC_HOST_START = 1'],
}
# Round 624 (agent E2, S2a: RN1Z90, JVJQNH): Slay the Spire 2 v2 also runs its 853 MB program from its image on the first start.
_r620['slay-the-spire-2'] = _r620['slay-the-spire-2'] + ['env.MADEIRA_X64_IMAGE_NOCOPY = 1']
# Round 624 (agent E1 S9, Far Cry 3's updater): v2 logs the failed registry requests (MADEIRA_REG_TRACE, build 624). Marvel Cosmic Invasion v2
# (Discord W6): the program match no longer needs a Steam copy (the ParisEngine.dll check beside it stays); its lines are unchanged.
_r620['far-cry-3'] = _r620['far-cry-3'] + ['env.MADEIRA_REG_TRACE = 1']
# Round 624 (agent E2, S2b: BMVD7C): v3 serves Godot 4.5's composition swap chain on the game's window (build 624).
_r620['slay-the-spire-2'] = _r620['slay-the-spire-2'] + ['env.MADEIRA_DXGI_SRC = 1', 'env.MADEIRA_DXGI_COMPOSITION = 1', 'env.MADEIRA_OPTIN_DLLS = dcomp']
# Round 625 (agent L4 S1-a: ZXCXT5, BMVD7C): v4 switches the .NET runtime's write-xor-execute code memory off (the fail-fast at its write
# barrier, c0000602) and gives the 32-bit LDADDAL emulation auto-tune added to every log that went further (build 608).
_r620['slay-the-spire-2'] = _r620['slay-the-spire-2'] + ['env.DOTNET_EnableWriteXorExecute = 0', 'env.MADEIRA_ANON_LDADDAL32 = 1']
_r620_v = {'slay-the-spire-2': 4, 'far-cry-3': 2, 'marvel-cosmic-invasion': 2}
for _id, _lines in _r620.items():
    assert presets[_id]['version'] == _r620_v.get(_id, 1) and presets[_id]['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'] + _lines, (_id, presets[_id])
assert presets['slay-the-spire-2']['line_builds'][-6:] == [(576, 1000000000), (624, 1000000000), (624, 1000000000), (624, 1000000000),
                                                          (576, 1000000000), (608, 1000000000)], presets['slay-the-spire-2']['line_builds']
assert presets['far-cry-3']['line_builds'][-1] == (624, 1000000000), presets['far-cry-3']['line_builds']
for _id, _line in (('forza-horizon-4', 'env.MADEIRA_OPTIN_DLLS = dxva2,gamingtcui'), ('big-walk', 'env.MADEIRA_REAL_SUSPEND = 1'),
                   ('ball-x-pit', 'env.MADEIRA_REAL_SUSPEND = 1')):
    _i = presets[_id]['lines'].index(_line)
    assert presets[_id]['line_builds'][_i] == (620, 1000000000), (_id, presets[_id]['line_builds'])
# Round 620, after the Discord reports: two 64-bit/32-bit Direct3D 11 fixes that need build 620's DXMT. F1 2020 is 64-bit, so its line
# selects d3d11-src.dll (the only 64-bit d3d11.dll with BCD's patches); Need for Speed: The Run is 32-bit and needs only the switch.
for _id, _line in (('f1-2020', 'env.MADEIRA_D3D11_SRC = 1'), ('need-for-speed-the-run', 'env.MADEIRA_BUFFER_RTV_EMPTY = 1')):
    _extra = ['env.MADEIRA_PAUSE_IN_BACKGROUND = 1'] if _id == 'f1-2020' else []
    assert presets[_id]['version'] == (3 if _id == 'f1-2020' else 1) and presets[_id]['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', _line] + _extra, (_id, presets[_id])   # F1 2020 v3: any copy (round 624)
    assert presets[_id]['line_builds'][presets[_id]['lines'].index(_line)] == (620, 1000000000), (_id, presets[_id]['line_builds'])
# F1 2020 v2 (owner, 2026-10-28; it runs on 0.1.622, logs KV4KKE/TX41T6): the switches of the runs that worked, and the pause in the
# background from build 623 (the build whose allowedEnvNames has it); no swap line (6.7 GB at 1112x832).
assert presets['f1-2020']['line_builds'][-1] == (623, 1000000000) and presets['f1-2020']['switches'] == 'avx: false, nvidia: false, wineVCRT: true', presets['f1-2020']
assert '"MADEIRA_PAUSE_IN_BACKGROUND",   // build 623' in (app / 'GameRecommendationsList.swift').read_text()
# Resident Evil 3's program name is shared with another project's re3.exe: only beside the RE Engine's data file.
assert presets['resident-evil-3']['match']['programs'] == [{'name': 're3.exe', 'beside': 're_chunk_000.pak'}], presets['resident-evil-3']['match']
# MARVEL Cosmic Invasion's program is a generic Game.exe: only beside its engine (v2, round 624: in any library, not only Steam's; the owner's
# rule makes no difference between stores, and a copy added with the + button got no pad).
assert presets['marvel-cosmic-invasion']['match']['programs'] == [{'name': 'game.exe', 'beside': 'ParisEngine.dll'}]
# Farming Simulator 22's line matters only for an Epic start; the program is matched only outside a Steam library.
assert presets['farming-simulator-22-epic']['match'] == {'programs': [{'name': 'farmingsimulator2022.exe', 'steam': False}]}
assert presets['clair-obscur-expedition-33']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['clair-obscur-expedition-33']['version'] == 1 and presets['clair-obscur-expedition-33']['switches'] == '', presets['clair-obscur-expedition-33']
# Build 579 (2026-10-21): the two Rockstar Launcher titles outside the GTA V / RDR2 lists (Send Logs 4GAWSA, MM0DAY: the Launcher's
# RockstarService.exe found no service manager, "[svc] Unable to open SC manager! (1722)"): GTA V's Launcher lines and no engine line.
_scm = {'line': 'env.MADEIRA_DOCK_GAME_SCM = 1', 'minBuild': 576}
# Round 620: Max Payne 3 starts the same Launcher (FXY57T, YXFHTE): the San Andreas DE lines, plus pool-split for the pool that shrank
# to 560 MB before RockstarService.exe's code buffers were refused.
assert presets['max-payne-3']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_FEX_AVX = 0',
    'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'pool-split = 1'] and presets['max-payne-3']['version'] == 1
for _id in ('gta4-steam', 'gtasa-de-steam'):
    _rule = next(r for r in recs['rules'] if r['id'] == _id)
    # (no KEEP_ALIVE: GTA V's line, with no evidence in these logs; the lead left it out)
    # (GTA IV's version 2, 2026-10-23, adds Social Club's layout 2 after the AVX pin: the Launcher's browser had no PartitionAlloc pools in
    # J1J40D and 3JTHRV; San Andreas DE and RDR1 stay without it, unproven)
    _sc2 = ['env.MADEIRA_SC_PA_POOLS = 2'] if _id == 'gta4-steam' else []
    # (GTA IV's version 3, round 624, agent R S16: the pool lines of the other Rockstar lists and the 32-bit window's free-space line; none of
    # the 36 GTA IV Steam Dock sessions of the archive reached the game, 12 of 32 ran out of pool, and 151 Rockstar-chain sessions with these
    # lines had no pool exhaustion)
    _pool4 = ['pool-low = 1', 'pool-page-fit = 1', 'pool-split = 1', 'env.MADEIRA_POOL_HEAD_RESERVE_MB = 128', 'env.MADEIRA_POOL_LOW_IMAGES = 1',
              'env.MADEIRA_POOL_RECYCLE_IMAGES = 1', 'env.MADEIRA_WOW_MIN_FREE_GB = 2'] if _id == 'gta4-steam' else []
    assert presets[_id]['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_FEX_AVX = 0'] + _sc2 + [
                                         'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] + _pool4, presets[_id]
    assert presets[_id]['version'] == (3 if _id == 'gta4-steam' else 1) and presets[_id]['switches'] == '' and not presets[_id]['small'], presets[_id]
    # every line after the standard one goes to builds from 576 on; none of GTA V's engine, DXGI, adapter or pool lines (GTA IV: the layout only)
    assert all(isinstance(l, dict) and l.get('minBuild') == 576 for l in _rule['config'][3:]), _id
    assert not any(k in ' '.join(l for l in presets[_id]['lines'] if l not in _pool4) for k in ('pool-split', 'DXGI', 'D3D11_SRC', 'KMT_ADAPTER',
                                                                  'DXMT_', 'LOCK_ORPHAN', 'EXECREQ_LEAVE', 'CHILD_ARGS', 'vc_redist')), _id
    assert ('SC_PA_POOLS' in ' '.join(presets[_id]['lines'])) == (_id == 'gta4-steam'), _id
assert _scm in next(r for r in recs['rules'] if r['id'] == 'gta4-steam')['config']
# Senua's Saga: Hellblade II has no list: the only log (XJ7Z6Y) was a copy outside Steam; its DLL and auto-tune rule are engine-level.
# Build 579 (2026-10-21): Unrailed! 2 (Send Log CR8H6H): a .NET 7+ garbage collector reserves 256 GB; DOTNET_GCRegionRange = 16 GB (hex), build 579 on.
assert presets['unrailed-2-back-on-track']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.DOTNET_GCRegionRange = 400000000'] and presets['unrailed-2-back-on-track']['version'] == 1 and presets['unrailed-2-back-on-track']['switches'] == '', presets['unrailed-2-back-on-track']
assert {'line': 'env.DOTNET_GCRegionRange = 400000000', 'minBuild': 579} in next(r for r in recs['rules'] if r['id'] == 'unrailed-2-back-on-track')['config']
# Resident Evil 2, version 1 (2026-10-22; build 579 log re2-ipadm4-579: the ASF byte stream handler's DLL is missing and the game reads NULL after
# its first cutscene): BCD's 64-bit media decoders and the three opt-in DLLs, for build 580 and later; nothing else (docs/MEDIA.md, ASF and WMV).
assert presets['resident-evil-2']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WG_64BIT = 1', 'env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod'] and presets['resident-evil-2']['version'] == 1 and presets['resident-evil-2']['switches'] == '', presets['resident-evil-2']
_re2 = next(r for r in recs['rules'] if r['id'] == 'resident-evil-2')
assert {'line': 'env.MADEIRA_WG_64BIT = 1', 'minBuild': 580} in _re2['config'] and {'line': 'env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod', 'minBuild': 580} in _re2['config'], _re2['config']
# Resident Evil 7 biohazard: the same failure chain (log 5MXS1S, build 576), the same two lines; only these two games list them
assert presets['resident-evil-7']['lines'][2:] == presets['resident-evil-2']['lines'][2:] and presets['resident-evil-7']['version'] == 1, presets['resident-evil-7']
assert [r['id'] for r in recs['rules'] if any(n in json.dumps(r['config']) for n in ('mfasfsrcsnk', 'wmvdecod', 'wmadmod', 'MADEIRA_WG_64BIT'))] == ['resident-evil-2', 'resident-evil-7', 'fallout-4', 'skyrim-special-edition', 'resident-evil-3', 'fallout-4-other', 'skyrim-special-edition-other']  # Fallout 4 v2 (round 624): wmadmod alone, its xWMA voices and music (0PT2YV vs WRSNC8); Skyrim SE: wmadmod alone, its xWMA voice lines (2026-10-24); Resident Evil 3: RE2's lines (round 620)
# Build 580 list (2026-10-22, the tur580 log wave; docs/DURUM.md section 2): lists for games that fail today, each line from a log. Every
# line that names a key a download may set only from build 580 (FEX_X87REDUCEDPRECISION, MADEIRA_TLS_CLEAR_OWN_PROCESS, MADEIRA_VDCFG_PATH:
# build 579's policy does not know them and would reject the whole download) carries minBuild 580.
_std = 'env.MADEIRA_EC_HOOK_TRACE = 0'
_nocopy = 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'
for _id in ('blazblue-entropy-effect', 'dark-souls-iii', 'raidou-remastered'):   # Spider-Man Remastered: v2 (2026-10-23), pinned above
    # Dark Souls III v2 (round 624, agent C S1: EQKH79, SJ0TZ6 and 8 older logs): the source-built Direct3D 11 with the SRV mip-count fix,
    # both from build 624; and its program match no longer needs a Steam install.
    _more = ['env.MADEIRA_D3D11_SRC = 1', 'env.MADEIRA_SRV_GETDESC_RESOLVE = 1'] if _id == 'dark-souls-iii' else []
    assert presets[_id]['lines'][2:] == [_std, _nocopy] + _more and presets[_id]['version'] == 2 and presets[_id]['switches'] == '', presets[_id]   # all v2 (round 624: DS3's fix, the other two any copy)
    assert {'line': _nocopy, 'minBuild': 576} in next(r for r in recs['rules'] if r['id'] == _id)['config'], _id
assert presets['dark-souls-iii']['line_builds'][-2:] == [(624, 1000000000), (624, 1000000000)]
assert presets['yakuza-like-a-dragon']['lines'][2:] == [_std, 'exe-window-mb = 416', _nocopy] and presets['yakuza-like-a-dragon']['version'] == 2, presets['yakuza-like-a-dragon']
# Round 624 (Discord forum item 5): Dead Space (2023), a 419 MiB fixed-base program; its siblings' two lines, by App ID and folder only.
assert presets['dead-space-2023']['lines'][2:] == [_std, 'exe-window-mb = 448', _nocopy] and presets['dead-space-2023']['version'] == 1, presets['dead-space-2023']
# Round 624 (Discord): controller experiments for two Unity games and a diagnostic line for a slow 32-bit Direct3D 8 game.
for _id in ('schedule-i', 'dokimon-quest'):
    assert presets[_id]['lines'][2:] == [_std, 'env.MADEIRA_WGI_HOST_PADS = 1'] and presets[_id]['version'] == 1 \
        and presets[_id]['line_builds'][-1] == (624, 1000000000), presets[_id]
assert presets['sonic-adventure-dx']['lines'][2:] == [_std, 'env.MADEIRA_FRAME_STATS = 1'] and presets['sonic-adventure-dx']['version'] == 1, presets['sonic-adventure-dx']
assert presets['requiem-avenging-angel']['lines'][2:] == presets['lego-batman-the-videogame']['lines'][2:6] \
    and presets['requiem-avenging-angel']['version'] == 1, presets['requiem-avenging-angel']   # LEGO Batman's diagnostic set
assert presets['friends-vs-friends']['lines'][2:] == [_std, 'env.MADEIRA_GUEST_LOG = all', 'env.MADEIRA_GUEST_LOG_LIMIT = 800'] \
    and presets['friends-vs-friends']['version'] == 1, presets['friends-vs-friends']   # agent E1 S4 (YKCQXE): the game's own log
assert presets['batman-arkham-city-goty']['lines'][2:] == [_std, 'env.FEX_X87REDUCEDPRECISION = 0', 'env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1'] and presets['batman-arkham-city-goty']['version'] == 3, presets['batman-arkham-city-goty']   # v2 (2026-10-23): BmLauncher.exe beside BatmanAC.exe
_bat = next(r for r in recs['rules'] if r['id'] == 'batman-arkham-city-goty')
assert {'line': 'env.FEX_X87REDUCEDPRECISION = 0', 'minBuild': 580} in _bat['config'] and {'line': 'env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1', 'minBuild': 580} in _bat['config'], _bat['config']
for _id in ('metal-gear-solid-peace-walker', 'lonely-mountains-snow-riders'):   # Lonely Mountains v2 (round 624): any copy
    assert presets[_id]['lines'][2:] == [_std, 'env.MADEIRA_WINTYPES = 1'] and presets[_id]['version'] == (2 if _id == 'lonely-mountains-snow-riders' else 1), presets[_id]
assert presets['trails-in-the-sky-1st-chapter']['lines'][2:] == [_std, 'env.MADEIRA_VDCFG_PATH = 1'] and presets['trails-in-the-sky-1st-chapter']['version'] == 2, presets['trails-in-the-sky-1st-chapter']   # v2 (round 623): a program-folder start matches too
assert {'line': 'env.MADEIRA_VDCFG_PATH = 1', 'minBuild': 580} in next(r for r in recs['rules'] if r['id'] == 'trails-in-the-sky-1st-chapter')['config']
assert presets['subnautica-2']['lines'][2:] == [_std, _nocopy] and presets['subnautica-2']['version'] == 2, presets['subnautica-2']
assert presets['rdr1-steam']['lines'][2:] == [_std, 'env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler', _nocopy] and presets['rdr1-steam']['version'] == 1, presets['rdr1-steam']
assert all(isinstance(l, dict) and l.get('minBuild') == 576 for l in next(r for r in recs['rules'] if r['id'] == 'rdr1-steam')['config'][3:])
assert not any('FEX_AVX' in l for l in presets['rdr1-steam']['lines']), 'RDR1 leaves out gta4-steam\'s AVX pin (no c000001d in its log)'
# Build 591 list (2026-10-23, the tur591 log wave; docs/DURUM.md section 2): lists for games that fail today, each line from a log. Every line
# whose switch is read only from build 591 on (the first five names below, and _JAVA_OPTIONS) carries minBuild 591; the older switches keep the
# build their neighbours use. The game's own line list, in order, is pinned for each new rule.
def _cfg(_id):
    return next(r for r in recs['rules'] if r['id'] == _id)['config']
_req = presets['resident-evil-requiem']
assert _req['lines'][2:] == [_std, 'exe-window-mb = 592', _nocopy, 'd3d12-pso-cached-blob = 1'] and _req['version'] == 3 and _req['switches'] == '', _req   # v2: build 598's cached-blob line (list v8); v3 (round 624): any copy
assert {'line': 'exe-window-mb = 592', 'minBuild': 575} in _cfg('resident-evil-requiem') and {'line': _nocopy, 'minBuild': 576} in _cfg('resident-evil-requiem')
assert presets['life-is-strange-remastered']['lines'][2:] == [_std, _nocopy] and presets['life-is-strange-remastered']['version'] == 2, presets['life-is-strange-remastered']
assert presets['life-is-strange-2']['lines'][2:] == [_std, 'exe-window-mb = 400', _nocopy] and presets['life-is-strange-2']['version'] == 2, presets['life-is-strange-2']
for _id in ('life-is-strange-remastered', 'life-is-strange-2'):
    assert {'line': _nocopy, 'minBuild': 576} in _cfg(_id), _id
# a fixed-base window is the image plus 16-20 MiB and fits the free run above 0x140000000 (751 MiB in the RE Requiem logs, 721 in Life is Strange 2's)
assert 575.07 < 592 < 720 and 379.2 < 400 < 720
assert presets['project-zomboid']['lines'][2:] == [_std, 'env._JAVA_OPTIONS = -XX:-UseZGC'] and presets['project-zomboid']['version'] == 2, presets['project-zomboid']
assert {'line': 'env._JAVA_OPTIONS = -XX:-UseZGC', 'minBuild': 591} in _cfg('project-zomboid')
_new591 = {
    'spiderheck': 'env.MADEIRA_FAULT_GUARD_RATE = 1', 'rounds': 'env.MADEIRA_FAULT_GUARD_RATE = 1', 'plateup': 'env.MADEIRA_FAULT_GUARD_RATE = 1',
    'final-fantasy-tactics-ivalice-chronicles': 'env.MADEIRA_USER_VA_GB = 32', 'age-of-empires-ii-hd': 'env.MADEIRA_MFC_DOCK = 1',
    'fallout-new-vegas': 'env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1', 'digimon-story-cyber-sleuth': 'env.MADEIRA_OPTIN_DLLS = oledlg',
    'final-fantasy-xv-demo': 'env.MADEIRA_OPTIN_DLLS = fwpuclnt', 'borderlands-3': 'env.MADEIRA_OPTIN_DLLS = sensapi',
    'jetboard-joust': 'env.MADEIRA_ANON_CAS_FLUSH = 1', 'sword-and-fairy-3': 'env.MADEIRA_NLS_EXTRA = 1',
}
for _id, _line in _new591.items():
    assert presets[_id]['lines'][2:] == [_std, _line] + (['env.MADEIRA_D3D9_LAST = 1', 'gpu-card = nvidia-7900gs'] if _id == 'fallout-new-vegas' else []) \
        and presets[_id]['version'] == (3 if _id == 'fallout-new-vegas' else 1 if _id in ('age-of-empires-ii-hd', 'sword-and-fairy-3') else 2) \
        and presets[_id]['switches'] == '', (_id, presets[_id])   # Fallout: New Vegas: + build 598's diagnostic (v2, list v8) and build 623's card (v3), below;
        # round 624: the seven Steam-only program matches of this batch match any copy (v2)
    assert {'line': _line, 'minBuild': 591} in _cfg(_id), _id
# bcd-games issue #13 (2026-10-23): Unreal start-up programs that ask for the ARM64 VC++ redistributable
assert presets['dead-as-disco']['lines'][2:] == [_std, 'env.MADEIRA_OPTIN_DLLS = tbs', 'env.MADEIRA_VCRT_ARM64_KEY = 1'] and presets['dead-as-disco']['version'] == 1, presets['dead-as-disco']
assert {'line': 'env.MADEIRA_OPTIN_DLLS = tbs', 'minBuild': 580} in _cfg('dead-as-disco') and {'line': 'env.MADEIRA_VCRT_ARM64_KEY = 1', 'minBuild': 576} in _cfg('dead-as-disco')
assert presets['tung']['lines'][2:] == [_std, 'env.MADEIRA_VCRT_ARM64_KEY = 1'] and {'line': 'env.MADEIRA_VCRT_ARM64_KEY = 1', 'minBuild': 576} in _cfg('tung')
# Darksiders II Deathinitive Edition (64-bit Direct3D 9, 2026-10-23): DXMT's Direct3D 9 for 64-bit programs, an opt-in line from build 595.
# Wine's d3d9.dll stays every other game's (tests/host/check-d3d9-x64.py).
_ds2 = presets['darksiders-ii-deathinitive']['lines'][2:]
assert _ds2[:2] == [_std, 'env.MADEIRA_D3D9_X64 = dxmt'] and presets['darksiders-ii-deathinitive']['version'] == 1 \
    and presets['darksiders-ii-deathinitive']['switches'] == '', presets['darksiders-ii-deathinitive']
assert {'line': 'env.MADEIRA_D3D9_X64 = dxmt', 'minBuild': 595} in _cfg('darksiders-ii-deathinitive')
# Build 597 (ajan17-ds2, 2026-10-23): the crash after the second intro video is a NULL object in the game's own code; three diagnostic lines, all
# from build 597, make the next log name it (DXMT's last-calls ring and the 64-bit fault report of d3d9-dxmt.dll, Wine's file open trace).
# They are not fixes. Only this list carries them (tests/host/check-d3d9-fault-dump.py).
assert _ds2[2:] == ['env.MADEIRA_D3D9_LAST = 1', 'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 30000'], _ds2
for _l in ('env.MADEIRA_D3D9_LAST = 1', 'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 30000'):
    assert {'line': _l, 'minBuild': 597} in _cfg('darksiders-ii-deathinitive'), _l
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_D3D9_LAST' in json.dumps(r['config'])) == ['darksiders-ii-deathinitive', 'fallout-3', 'fallout-new-vegas']   # + Fallout: New Vegas (ajan20, build 598, a diagnostic)
# Terraria (2026-10-24, build 598): a diagnostic only, the file trace, for its "Failed to load asset" box (four logs); no fix, no other game.
assert presets['terraria']['lines'][2:] == [_std, 'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 60000'] and presets['terraria']['version'] == 1 \
    and presets['terraria']['switches'] == '', presets['terraria']
for _l in ('env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 60000'):
    assert {'line': _l, 'minBuild': 598} in _cfg('terraria'), _l
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_FILE_TRACE' in json.dumps(r['config'])) == ['bioshock-infinite-epic', 'blasphemous', 'danganronpa-trigger-happy-havoc', 'darksiders-ii-deathinitive', 'doki-doki-literature-club', 'nfs-most-wanted', 'nfs-underground-2', 'overlord-ii', 'terraria']   # + BioShock Infinite (Epic) and Danganronpa (ajan20, build 598, diagnostics); round 624: Doki Doki, the two Need for Speed lists
# ajan23/loglar (2026-10-24, build 598): two more 64-bit Direct3D 9 games that crash on the failed Direct3DCreate9 (MudRunner, Indivisible), and
# Sekiro, whose unhandled C++ exception follows two lookups of code page 932. Each list's own line only, opt-in, from build 598.
for _id, _line in (('mudrunner', 'env.MADEIRA_D3D9_X64 = dxmt'), ('indivisible', 'env.MADEIRA_D3D9_X64 = dxmt'), ('sekiro-shadows-die-twice', 'env.MADEIRA_NLS_EXTRA = 1')):
    assert presets[_id]['lines'][2:] == [_std, _line] and presets[_id]['version'] == 1 and presets[_id]['switches'] == '', (_id, presets[_id])
    assert {'line': _line, 'minBuild': 598} in _cfg(_id), _id
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_D3D9_X64' in json.dumps(r['config'])) == ['a-hat-in-time', 'counter-strike-source', 'darksiders-ii-deathinitive', 'dont-starve-together', 'indivisible', 'mudrunner', 'no-more-room-in-hell', 'outlast', 'pico-park'], 'only Darksiders II, MudRunner, Indivisible, Counter-Strike: Source (round 2026-10-24), PICO PARK, Don\'t Starve Together, A Hat in Time (round 602) and No More Room in Hell, Outlast (round 624) carry the 64-bit D3D9 line'
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_NLS_EXTRA' in json.dumps(r['config'])) == ['sekiro-shadows-die-twice', 'sword-and-fairy-3'], 'only Sword and Fairy 3 and Sekiro carry the extra code pages'
assert presets['counter-strike-source']['lines'][2:] == [_std, 'env.MADEIRA_D3D9_X64 = dxmt'] and {'line': 'env.MADEIRA_D3D9_X64 = dxmt', 'minBuild': 598} in _cfg('counter-strike-source') and presets['counter-strike-source']['version'] == 1
# Round 2026-10-24 (ajan22, builds 598 and later): two games whose log stop has a cause with an existing opt-in switch, each line from build 598.
# Risk of Rain 2: Rewired_WindowsGamingInput.dll's RoGetActivationFactory finds no wintypes.dll and throws an uncaught C++ exception (Send Log 1M1KDQ).
# Stardew Valley: the .NET 6 CoreCLR's emulated CAS leaves FEX's translation stale and its main thread spins (Send Log 7FZXF2; Jetboard Joust's cause).
for _id, _line in (('risk-of-rain-2', 'env.MADEIRA_WINTYPES = 1'), ('stardew-valley', 'env.MADEIRA_ANON_CAS_FLUSH = 1')):
    assert presets[_id]['lines'][2:] == [_std, _line] and presets[_id]['version'] == 1 and presets[_id]['switches'] == '', (_id, presets[_id])
    assert {'line': _line, 'minBuild': 598} in _cfg(_id), _id
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_ANON_CAS_FLUSH' in json.dumps(r['config'])) == ['jetboard-joust', 'stardew-valley'], 'only the CoreCLR games carry the CAS flush'
assert '"MADEIRA_D3D9_X64"' in (app / 'GameRecommendationsList.swift').read_text() and 'guard value == "dxmt" || value == "wined3d"' in (app / 'GameRecommendationsList.swift').read_text()
# Build 598 (ajan21-loglar, 2026-10-24): the report of a 32-bit program's access violation or int3 (ios_x86_fault_diag.h) for the two Need for Speed
# games that stop at a fixed place of their own program (speed.exe at 0x81AB7A, SPEED2.EXE at 0x4405A9). Log only, a diagnostic and no fix;
# only these two lists carry it (tests/host/check-x86-fault-diag.py).
# Round 624 (agent A S12, B1MR64/MVZAD0): version 2 adds the file trace, to see whether the track loader read its file completely.
for _id in ('nfs-most-wanted', 'nfs-underground-2'):
    assert presets[_id]['lines'][2:] == [_std, 'env.MADEIRA_X86_FAULT_DUMP = 1', 'env.MADEIRA_FILE_TRACE = 1'] and presets[_id]['version'] == 2 \
        and presets[_id]['switches'] == '', (_id, presets[_id])
    assert {'line': 'env.MADEIRA_X86_FAULT_DUMP = 1', 'minBuild': 598} in _cfg(_id) and {'line': 'env.MADEIRA_FILE_TRACE = 1', 'minBuild': 598} in _cfg(_id), _id
# Round 2026-10-24 (ajan22): the two Adobe AIR games (32-bit; Send Logs 3YS003, Z8054X, 4Y7X8M, WF7MW9) get the same log-only report.
for _id in ('chuchel', 'botanicula'):
    assert presets[_id]['lines'][2:] == [_std, 'env.MADEIRA_X86_FAULT_DUMP = 1'] and presets[_id]['version'] == 1 and presets[_id]['switches'] == '', (_id, presets[_id])
    assert {'line': 'env.MADEIRA_X86_FAULT_DUMP = 1', 'minBuild': 598} in _cfg(_id), _id
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_X86_FAULT_DUMP' in json.dumps(r['config'])) == ['botanicula', 'chuchel', 'doki-doki-literature-club', 'mafia-2', 'nfs-most-wanted', 'nfs-underground-2', 'osu', 'overlord-ii', 'ubisoft-connect', 'unreal-gold']   # round 624: six diagnostic lists
assert presets['wrc-9']['lines'][2:] == [_std, 'env.MADEIRA_D3D11_SRC = 1', 'env.DXMT_QUERY_TS_WATCHDOG = 1'] and presets['wrc-9']['version'] == 1 \
    and presets['wrc-9']['line_builds'][-2:] == [(624, 1000000000), (624, 1000000000)], presets['wrc-9']   # agent D2 S11 (JDJ8KE): the timestamp watchdog
for _id, _mb in (('fallout-4-other', 614), ('skyrim-special-edition-other', 598)):   # round 624: the voice line for a copy outside Steam (no launcher swap)
    assert presets[_id]['lines'][2:] == [_std, 'env.MADEIRA_OPTIN_DLLS = wmadmod'] and presets[_id]['version'] == 1, presets[_id]
    assert {'line': 'env.MADEIRA_OPTIN_DLLS = wmadmod', 'minBuild': _mb} in _cfg(_id), _id
assert presets['doki-doki-literature-club']['lines'][2:] == [_std, 'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 12000', 'env.MADEIRA_GUEST_LOG = all',
                                                              'env.MADEIRA_GUEST_LOG_LIMIT = 800', 'env.MADEIRA_X86_FAULT_DUMP = 1'] \
    and presets['doki-doki-literature-club']['version'] == 1, presets['doki-doki-literature-club']   # agent E1 S7 (TS8ME1): Ren'Py's exit(-1)
assert {'line': 'env.MADEIRA_GUEST_LOG = all', 'minBuild': 623} in _cfg('doki-doki-literature-club'), 'the Ren\'Py mirror ships from 623'
_ug = ' '.join(next(r for r in recs['rules'] if r['id'] == 'unreal-gold')['notes'])
assert 'Fire.dll+0x15E62' in _ug and 'shortened' not in _ug and ' ...' not in _ug, 'the Unreal Gold note says what the logs show (no placeholder)'
assert '"MADEIRA_X86_FAULT_DUMP"' in (app / 'GameRecommendationsList.swift').read_text()
# Build 598 (ajan28-ozellik, 2026-10-24): Fallout 4's Steam launch entry is its launcher alone (Fallout4Launcher.exe, key 0), which cannot start here; the
# game's list swaps it for Fallout4.exe of the same folder (env.MADEIRA_SPAWN_SWAP, tests/host/check-spawn-swap.py). Only Steam's copy: a copy outside
# Steam keeps the standard list (its launcher is how a player enables mods there).
assert presets['fallout-4']['lines'][2:] == [_std, 'env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe', 'env.MADEIRA_OPTIN_DLLS = wmadmod'] and presets['fallout-4']['version'] == 2 and presets['fallout-4']['switches'] == ''
assert {'line': 'env.MADEIRA_OPTIN_DLLS = wmadmod', 'minBuild': 614} in _cfg('fallout-4')   # round 624: from build 613 the line also brings MADEIRA_WG_64BIT
assert {'line': 'env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe', 'minBuild': 598} in _cfg('fallout-4')
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_SPAWN_SWAP' in json.dumps(r['config'])) == ['fallout-4', 'skyrim-special-edition']
assert {'line': 'env.MADEIRA_SPAWN_SWAP = SkyrimSELauncher.exe=SkyrimSE.exe', 'minBuild': 598} in _cfg('skyrim-special-edition')
assert {'line': 'env.MADEIRA_OPTIN_DLLS = wmadmod', 'minBuild': 598} in _cfg('skyrim-special-edition')
# Round tur600 (ajan38, build 600): Glyphica: Typing Survival (neosphere.exe, ChakraCore) ends in a fail-fast over an unwritten mitigation policy; the Wine fork's
# kernelbase zeroes it when env.MADEIRA_ZERO_MITIGATION_POLICY is 1. The new rule and its line both carry minBuild 600; no other list has the line
# (tests/host/check-zero-mitigation-policy.py holds the rest).
# v2 (round 624): from build 601 on; the public build 600 does not know the key and refused the whole online list for it.
assert {'line': 'env.MADEIRA_ZERO_MITIGATION_POLICY = 1', 'minBuild': 601} in _cfg('glyphica-typing-survival')
assert next(r for r in recs['rules'] if r['id'] == 'glyphica-typing-survival')['minBuild'] == 600
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_ZERO_MITIGATION_POLICY' in json.dumps(r['config'])) == ['glyphica-typing-survival']
# Round 602 (ajan41/tur602): the Direct3D 8 games. Each list is the standard line and the d3d8to9 line, which only builds from 602 apply; nothing else names the key.
_d8 = ['grand-theft-auto-iii', 'postal-2', 'hotline-miami', 'max-payne-2', 'red-faction']   # Red Faction: round 624 (agent D2, 8R8V3Z)
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_D3D8' in json.dumps(r['config'])) == sorted(_d8), 'only the five Direct3D 8 games carry env.MADEIRA_D3D8'
for _id in _d8:
    _r = next(r for r in recs['rules'] if r['id'] == _id)
    assert _r['config'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', {'line': 'env.MADEIRA_D3D8 = d3d9', 'minBuild': 602}] and _r['version'] == 1 and 'minBuild' not in _r, _id
assert not [r['id'] for r in recs['rules'] if 'MADEIRA_DDRAW' in json.dumps(r['config'])], 'no list sets cnc-ddraw yet (no log shows a 2D DirectDraw game that fails)'
# Upstream port 2026-10-25 (ajan45, build 602): The Adventures of Elliot (Steam 3483510, a 473 MiB fixed-base program; BCD log 8Q0SJ8, build 592, c0000018 for want of
# a window). The rule and every line carry minBuild 602; env.MADEIRA_KUSER_KEEP_REGS and pool-above-window (both off unless set) are set by this list and nothing else
# (exe-window-mb = 480, the image not copied and the pool settings are the game's own, as the other large-program lists have them).
_elliot = ['exe-window-mb = 480', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'pool-split = 1', 'pool-low = 1', 'pool-low-margin = 32', 'pool-above-window = 1',
           'env.MADEIRA_KUSER_KEEP_REGS = 1']
assert [l for l in _cfg('the-adventures-of-elliot') if isinstance(l, dict)] == [{'line': l, 'minBuild': 602} for l in _elliot]
assert [l for l in _cfg('the-adventures-of-elliot') if isinstance(l, str) and not l.startswith('#')] == ['env.MADEIRA_EC_HOOK_TRACE = 0']
assert next(r for r in recs['rules'] if r['id'] == 'the-adventures-of-elliot')['minBuild'] == 602
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_KUSER_KEEP_REGS' in json.dumps(r['config'])) == ['the-adventures-of-elliot']
assert sorted(r['id'] for r in recs['rules'] if 'pool-above-window' in json.dumps(r['config'])) == ['the-adventures-of-elliot']
assert '"MADEIRA_KUSER_KEEP_REGS"' in (app / 'GameRecommendationsList.swift').read_text()
assert '"MADEIRA_SPAWN_SWAP"' in (app / 'GameRecommendationsList.swift').read_text() and 'case "MADEIRA_SPAWN_SWAP":' in (app / 'GameRecommendationsList.swift').read_text()
# ZZZ: dxva2 is an old opt-in name (Fort Solis, Dragon Ball Sparking Zero carry it at 575)
assert presets['zenless-zone-zero']['lines'][2:] == [_std, 'env.MADEIRA_OPTIN_DLLS = dxva2'] and {'line': 'env.MADEIRA_OPTIN_DLLS = dxva2', 'minBuild': 575} in _cfg('zenless-zone-zero')
# the two rules that already existed: GTA IV gains Social Club's layout 2 (version 2), Horizon Zero Dawn the Dock MFC link (version 17)
assert presets['gta4-steam']['version'] == 3 and 'env.MADEIRA_SC_PA_POOLS = 2' in presets['gta4-steam']['lines']   # 3: round 624 pool lines and {'line': 'env.MADEIRA_SC_PA_POOLS = 2', 'minBuild': 576} in _cfg('gta4-steam')
for _id in ('gtasa-de-steam', 'rdr1-steam'):
    assert not any('SC_PA_POOLS' in l for l in presets[_id]['lines']), _id + ': unproven, left alone'
assert presets['horizon-zero-dawn']['version'] == 18 and presets['horizon-zero-dawn']['lines'][-1] == 'env.MADEIRA_MFC_DOCK = 1'
assert {'line': 'env.MADEIRA_MFC_DOCK = 1', 'minBuild': 591} in _cfg('horizon-zero-dawn') and presets['horizon-zero-dawn']['lines'][2:-1] == [l for l in presets['horizon-zero-dawn']['lines'][2:] if 'MFC_DOCK' not in l]
# no other game of the list carries a build 591 switch: only these rules name them (Far Cry 3: the install-script registry, round 620)
_names591 = ('MADEIRA_FAULT_GUARD_RATE', 'MADEIRA_USER_VA_GB', 'MADEIRA_MFC_DOCK', 'MADEIRA_DOCK_SCRIPT_REGISTRY', 'MADEIRA_ANON_CAS_FLUSH', 'MADEIRA_NLS_EXTRA', '_JAVA_OPTIONS')
assert sorted(r['id'] for r in recs['rules'] if any(n in json.dumps(r['config']) for n in _names591)) == sorted([i for i in _new591 if 'OPTIN_DLLS' not in _new591[i]] + ['project-zomboid', 'horizon-zero-dawn', 'sekiro-shadows-die-twice', 'stardew-valley', 'far-cry-3', 'fallout-3'])   # Sekiro: build 598's NLS_EXTRA; Fallout 3: round 624 (New Vegas's script-registry line)
# the names a list line may use only from build 591 are in the policy, with a value check
_policy_src = (app / 'GameRecommendationsList.swift').read_text()
for _name in _names591:
    assert '"%s"' % _name in _policy_src, _name
assert 'case "MADEIRA_FAULT_GUARD_RATE", "MADEIRA_MFC_DOCK", "MADEIRA_DOCK_SCRIPT_REGISTRY", "MADEIRA_ANON_CAS_FLUSH", "MADEIRA_NLS_EXTRA", "MADEIRA_WG_VIDEO":' in _policy_src
assert 'case "MADEIRA_USER_VA_GB":' in _policy_src and 'guard value == "-XX:-UseZGC" else' in _policy_src
# The three fixed-base / pool games that already had a list keep their old lines and gain the new one at the end.
for _id, _lines in (('star-wars-galactic-racer', [_nocopy]), ('beamng-drive', [_nocopy]), ('no-rest-for-the-wicked', ['env.MADEIRA_WINTYPES = 1', 'env.MADEIRA_CTX_FRAME = 1'])):
    assert presets[_id]['lines'][-len(_lines):] == _lines, _id
# the names a list line may use only from build 580 are in the policy, with a 0-or-1 check
for _name in ('FEX_X87REDUCEDPRECISION', 'MADEIRA_TLS_CLEAR_OWN_PROCESS', 'MADEIRA_VDCFG_PATH'):
    _policy_src = (app / 'GameRecommendationsList.swift').read_text()
    assert '"%s"' % _name in _policy_src and 'case "FEX_X87REDUCEDPRECISION", "MADEIRA_TLS_CLEAR_OWN_PROCESS", "MADEIRA_VDCFG_PATH":' in _policy_src, _name
    assert any(isinstance(l, dict) and l['line'].startswith('env.%s ' % _name) and l.get('minBuild') == 580 for r in recs['rules'] for l in r['config']), _name
# a new rule's notes quote the log lines they come from
for _id in ['blazblue-entropy-effect', 'dark-souls-iii', 'marvels-spider-man-remastered', 'marvels-spider-man-2', 'raidou-remastered', 'yakuza-like-a-dragon', 'batman-arkham-city-goty',
            'metal-gear-solid-peace-walker', 'lonely-mountains-snow-riders', 'trails-in-the-sky-1st-chapter', 'subnautica-2', 'rdr1-steam'] + list(_new591) + ['resident-evil-requiem', 'life-is-strange-remastered', 'life-is-strange-2', 'project-zomboid']:
    _notes = ' '.join(next(r for r in recs['rules'] if r['id'] == _id)['notes'])
    assert re.search(r'\bL[0-9]{3,6}\b', _notes), _id
# Build 579 (2026-10-21): the NOCOPY line of the three games whose images got no JIT-pool copy (Send Logs YBX2WT, AVFMGC, EJDV7Z, YBF7CN)
# goes to builds from 576 on, which know the key (it is older, but the line is new to these games' lists).
for _id in ('stellar-blade-demo', 'final-fantasy-resonance-demo', 'clair-obscur-expedition-33'):
    _rule = next(r for r in recs['rules'] if r['id'] == _id)
    assert {'line': 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'minBuild': 576} in _rule['config'], _id
# Stellar Blade Demo and FINAL FANTASY RESONANCE DEMO carry no pool-split: no region of a split pool takes their 314 / 474 MiB exe.
assert not any('pool-split' in l for _id in ('stellar-blade-demo', 'final-fantasy-resonance-demo', 'clair-obscur-expedition-33') for l in presets[_id]['lines'])
assert presets['no-rest-for-the-wicked']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_FEX_BAND_KEEP_MB = 6144', 'dxmt = d3d11.mipClampBC=1', 'env.MADEIRA_WINTYPES = 1', 'env.MADEIRA_CTX_FRAME = 1'] and presets['no-rest-for-the-wicked']['version'] == 4, presets['no-rest-for-the-wicked']
assert presets['quaver']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.DOTNET_EnableWriteXorExecute = 0'] and presets['quaver']['version'] == 1, presets['quaver']
assert presets['dying-light']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.LC_ALL = en_US.UTF-8', 'env.MADEIRA_OPTIN_DLLS = setupapi', 'env.MADEIRA_OPTIN_DLLS = setupapi,ddraw'] and presets['dying-light']['line_builds'][4:] == [(577, 593), (594, 10 ** 9)] and presets['dying-light']['version'] == 2, presets['dying-light']   # v2 (2026-10-23): English locale; setupapi for 577-593, setupapi,ddraw from 594
assert presets['beamng-drive']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_MACHINE = 1', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['beamng-drive']['version'] == 2, presets['beamng-drive']
assert presets['diablo-ii-resurrected']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = sfc,sfc_os,tbs', 'env.MADEIRA_NTDLL_XSEC = 1', 'env.MADEIRA_SHARED_SECTION_EXEC = 1', 'env.MADEIRA_NTDLL_DATA_SYNC_RANGE = 1', 'env.MADEIRA_FAULT_LOOP_GUEST_PROGRESS = 1', 'env.MADEIRA_D2R_PROBE_RECOVERY = 1'] and presets['diablo-ii-resurrected']['version'] == 2 and presets['diablo-ii-resurrected']['switches'] == '', presets['diablo-ii-resurrected']   # v2 (2026-10-24): the five executable-section switches of bcd-games#4 (build 598)
assert presets['american-truck-simulator']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_TINY_WINDOW_FIX = 1'] and presets['american-truck-simulator']['version'] == 1 and presets['american-truck-simulator']['switches'] == '', presets['american-truck-simulator']
assert presets['monster-hunter-stories-3-trial']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['monster-hunter-stories-3-trial']['version'] == 1, presets['monster-hunter-stories-3-trial']
assert presets['pwaat']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WOW_RWX_PLAIN = 1'] and presets['pwaat']['version'] == 1, presets['pwaat']
for ident in ('lego-party', '1000xresist'):
    assert presets[ident]['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1'] and presets[ident]['version'] == 1, presets[ident]
# Marvel's Spider-Man: Miles Morales, version 1 (2026-10-21; logs PD7S1E 574 and the 10:37 build 576 run end on Metal's "level range (2) must
# be <= mipmapLevelCount (1)" after a MipLevels 0 UAV texture; HQFPWG, build 574; the 548 fixture's JIT pool): mip0-full, quiet-log, the standard
# line and auto-tune's own NOCOPY line; NVIDIA on as in the logs that rendered. Rift Apart's other lines are left out on purpose
# (swap tier, DXGI source, cage/jumbo/alias, LC_ALL, va-pressure-gap: the notes say why for each).
# Version 2 (owner, 2026-10-28): Spider-Man Remastered's lines and switches (the same Nixxes engine), for the Steam copy and
# the DRM-free one (milesmorales.exe in any folder).
assert mm['lines'][2:] == smr['lines'][2:] and mm['version'] == 2, mm
assert mm['line_builds'] == smr['line_builds'] and mm['switches'] == smr['switches'], mm
assert mm['match'] == {'steamAppIDs': [1817190], 'steamFolders': ["marvel's spider-man miles morales"],
                       'programs': [{'name': 'milesmorales.exe'}]}, mm['match']
assert not any(line.startswith(('vram-mb', 'swap-', 'env.MADEIRA_VA_PRESSURE_GAP_OK', 'env.MADEIRA_OPTIN_DLLS', 'env.LC_ALL'))
               for line in mm['lines']), 'Miles Morales\' list has none of Rift Apart\'s memory, locale or address-space lines, and no evr opt-in'
mm_notes = ' '.join(n for r in recs['rules'] if r['id'] == 'marvels-spider-man-miles-morales' for n in r['notes'])
assert all(t in mm_notes for t in ('mip0-full = 1:', 'L83617', 'quiet-log = 1:', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1:', 'Left out on purpose',
                                   'MADEIRA_VA_PRESSURE_GAP_OK', 'MADEIRA_OPTIN_DLLS = evr', 'not tried on a device')), 'the notes give each line\'s reason'
# Recognition: Steam by App ID or steamapps\common folder; everything else by the program's name. Each
# rule's "match" is what match() of the Swift literals did (until 2026-10-18), rule by rule; the first
# rule that matches wins, so Enhanced (GTA5_Enhanced.exe beside the program) comes before Legacy.
MATCH = {
    'rdr2-steam': {'steamAppIDs': [1174180], 'steamFolders': ['red dead redemption 2'],
                   'programs': [{'name': 'rdr2.exe', 'steam': True}, {'name': 'playrdr2.exe', 'steam': True}]},
    'rdr2-other': {'programs': [{'name': 'rdr2.exe', 'steam': False}, {'name': 'playrdr2.exe', 'steam': False}, {'name': 'rdr2launcher.exe', 'steam': False}]},
    'gta5e-steam': {'steamAppIDs': [3240220], 'steamFolders': ['grand theft auto v enhanced'],
                    'programs': [{'name': 'playgtav.exe', 'steam': True, 'beside': 'GTA5_Enhanced.exe'},
                                 {'name': 'gta5_enhanced.exe', 'steam': True, 'beside': 'GTA5_Enhanced.exe'}]},
    'gta5e-other': {'programs': [{'name': 'playgtav.exe', 'steam': False, 'beside': 'GTA5_Enhanced.exe'},
                                 {'name': 'gta5_enhanced.exe', 'steam': False, 'beside': 'GTA5_Enhanced.exe'}]},
    'gta5l-steam': {'steamAppIDs': [271590], 'steamFolders': ['grand theft auto v'],
                    'programs': [{'name': 'playgtav.exe', 'steam': True, 'beside': 'GTA5.exe', 'notBeside': 'GTA5_Enhanced.exe'},
                                 {'name': 'gta5.exe', 'steam': True}]},
    'gta5l-other': {'programs': [{'name': 'playgtav.exe', 'steam': False, 'beside': 'GTA5.exe', 'notBeside': 'GTA5_Enhanced.exe'},
                                 {'name': 'gta5.exe', 'steam': False}]},
    'ghost-of-tsushima': {'steamAppIDs': [2215430], 'steamFolders': ["ghost of tsushima director's cut", 'ghost of tsushima'],
                          'programs': [{'name': 'ghostoftsushima.exe'}]},
    'god-of-war': {'steamAppIDs': [1593500], 'steamFolders': ['godofwar', 'god of war'], 'programs': [{'name': 'gow.exe'}]},
    'horizon-zero-dawn': {'steamAppIDs': [1151640], 'steamFolders': ['horizon zero dawn'], 'programs': [{'name': 'horizonzerodawn.exe'}]},
    'horizon-forbidden-west': {'steamAppIDs': [2420110], 'steamFolders': ['horizon forbidden west complete edition'],
                               'programs': [{'name': 'horizonforbiddenwest.exe'}]},
    'ratchet-clank-rift-apart': {'steamAppIDs': [1895880], 'steamFolders': ['ratchet & clank - rift apart'],
                                 'programs': [{'name': 'riftapart.exe'}]},
    'crysis-remastered': {'steamAppIDs': [1715130], 'steamFolders': ['crysis remastered'], 'programs': [{'name': 'crysisremastered.exe'}]},
    'crysis-2-remastered': {'steamAppIDs': [2096600], 'steamFolders': ['crysis 2 remastered'],
                            'programs': [{'name': 'crysis2remastered.exe'}]},
    'crysis-3-remastered': {'steamAppIDs': [2096610], 'steamFolders': ['crysis 3 remastered'],
                            'programs': [{'name': 'crysis3remastered.exe'}]},
    'msfs-2024': {'steamAppIDs': [2537590], 'steamFolders': ['msfs2024'], 'programs': [{'name': 'flightsimulator2024.exe'}]},
    'counter-strike-2': {'steamAppIDs': [730], 'steamFolders': ['counter-strike global offensive'], 'programs': [{'name': 'cs2.exe'}]},
    'warcraft-2-bne': {'programs': [{'name': 'warcraft ii bne.exe'}]},
    'breakneck': {'steamAppIDs': [432940], 'steamFolders': ['breakneck'], 'programs': [{'name': 'breakneck.exe'}]},
    'spelunky-2': {'steamAppIDs': [418530], 'steamFolders': ['spelunky 2'], 'programs': [{'name': 'spel2.exe'}]},
    'albion-online': {'steamAppIDs': [761890], 'steamFolders': ['albion online'], 'programs': [{'name': 'albionlauncher.exe'}]},
    'megabonk': {'steamAppIDs': [3405340], 'steamFolders': ['megabonk'], 'programs': [{'name': 'megabonk.exe'}]},
    'no-im-not-a-human': {'steamAppIDs': [3180070], 'steamFolders': ["no, i'm not a human"], 'programs': [{'name': 'noimnotahuman.exe'}]},
    'over-the-hill': {'steamAppIDs': [2929250], 'steamFolders': ['over the hill'], 'programs': [{'name': 'over the hill.exe'}]},
    'fort-solis': {'steamAppIDs': [1931730], 'steamFolders': ['fort solis'], 'programs': [{'name': 'fortsolis.exe'}]},
    'dragon-ball-sparking-zero': {'steamAppIDs': [1790600], 'steamFolders': ['dragon ball sparking! zero'], 'programs': [{'name': 'sparkingzero-win64-shipping.exe'}]},
    'final-fantasy-vii-steam': {'steamAppIDs': [39140], 'steamFolders': ['final fantasy vii steam edition'], 'programs': [{'name': 'ffvii_launcher.exe'}]},
    'animal-well': {'steamAppIDs': [813230], 'steamFolders': ['animal well'], 'programs': [{'name': 'animal well.exe'}]},
    'hogwarts-legacy': {'steamAppIDs': [990080], 'steamFolders': ['hogwarts legacy'], 'programs': [{'name': 'hogwartslegacy.exe'}]},
    'metal-gear-solid-v': {'steamAppIDs': [287700], 'steamFolders': ['mgs_tpp'], 'programs': [{'name': 'mgsvtpp.exe'}]},
    'stellar-blade-demo': {'steamAppIDs': [3564860], 'steamFolders': ['stellarbladedemo'], 'programs': [{'name': 'sb-win64-shipping.exe'}]},
    'star-wars-galactic-racer': {'steamAppIDs': [4078430], 'steamFolders': ['star wars galactic racer'], 'programs': [{'name': 'swgr-win64-shipping.exe'}]},
    # Minecraft Dungeons II: the Steam folder only (the PR's own paths; no App ID or log yet). Its programs, Dungeons.exe and
    # Dungeons-Win64-Shipping.exe, are Minecraft Dungeons' (the first game's) names too, so no program entry.
    'minecraft-dungeons-2': {'steamFolders': ['minecraft dungeons ii']},
    'denshattack': {'steamAppIDs': [2524850], 'steamFolders': ['denshattack'], 'programs': [{'name': 'denshattack_proto.exe'}]},
    'final-fantasy-resonance-demo': {'steamAppIDs': [4474710], 'steamFolders': ['final fantasy resonance demo'], 'programs': [{'name': 'ffrs.exe'}]},
    'no-rest-for-the-wicked': {'steamAppIDs': [1371980], 'steamFolders': ['norestforthewicked'], 'programs': [{'name': 'norestforthewicked.exe'}]},
    'quaver': {'steamAppIDs': [980610], 'steamFolders': ['quaver'], 'programs': [{'name': 'quaver.exe'}]},
    'dying-light': {'steamAppIDs': [239140], 'steamFolders': ['dying light'], 'programs': [{'name': 'dyinglightgame.exe'}]},
    'beamng-drive': {'steamAppIDs': [284160], 'steamFolders': ['beamng.drive'], 'programs': [{'name': 'beamng.drive.exe'}]},
    'monster-hunter-stories-3-trial': {'steamAppIDs': [3470630], 'steamFolders': ['monster_hunter_stories_3_twisted_reflection_trial'], 'programs': [{'name': 'monster_hunter_stories_3_twisted_reflection_trial.exe'}]},
    'pwaat': {'steamAppIDs': [787480], 'steamFolders': ['phoenix wright ace attorney trilogy'], 'programs': [{'name': 'pwaat.exe'}]},
    'lego-party': {'steamAppIDs': [1969370], 'steamFolders': ['lego party'], 'programs': [{'name': 'lego party.exe'}]},
    '1000xresist': {'steamAppIDs': [1675830], 'steamFolders': ['1000xresist'], 'programs': [{'name': '1000xresist.exe'}]},
    'american-truck-simulator': {'steamAppIDs': [270880], 'steamFolders': ['american truck simulator'], 'programs': [{'name': 'amtrucks.exe'}]},
    'marvels-spider-man-miles-morales': {'steamAppIDs': [1817190], 'steamFolders': ["marvel's spider-man miles morales"], 'programs': [{'name': 'milesmorales.exe'}]},
    'diablo-ii-resurrected': {'steamAppIDs': [2536520], 'steamFolders': ['diablo ii resurrected'], 'programs': [{'name': 'd2r.exe'}]},
    'clair-obscur-expedition-33': {'steamAppIDs': [1903340], 'steamFolders': ['expedition 33'], 'programs': [{'name': 'expedition33_steam.exe'}, {'name': 'sandfall-win64-shipping.exe'}]},
    'gta4-steam': {'steamAppIDs': [12210], 'steamFolders': ['grand theft auto iv'], 'programs': [{'name': 'playgtaiv.exe', 'steam': True}, {'name': 'gtaiv.exe', 'steam': True}]},
    'gtasa-de-steam': {'steamAppIDs': [1547000], 'steamFolders': ['gta san andreas - the definitive edition'], 'programs': [{'name': 'playgtasanandreas.exe', 'steam': True}]},
    'unrailed-2-back-on-track': {'steamAppIDs': [2211170], 'steamFolders': ['unrailed! 2 back on track']},
    'resident-evil-2': {'steamAppIDs': [883710], 'steamFolders': ['resident evil 2  biohazard re2'], 'programs': [{'name': 're2.exe'}]},
    'resident-evil-7': {'steamAppIDs': [418370], 'steamFolders': ['resident evil 7 biohazard'], 'programs': [{'name': 're7.exe'}]},
    'blazblue-entropy-effect': {'steamAppIDs': [2273430], 'steamFolders': ['blazblueentropyeffect'], 'programs': [{'name': 'blazblueentropyeffect.exe'}]},
    'dark-souls-iii': {'steamAppIDs': [374320], 'steamFolders': ['dark souls iii'], 'programs': [{'name': 'darksoulsiii.exe'}]},   # v2 (round 624): any copy
    'marvels-spider-man-remastered': {'steamAppIDs': [1817070], 'steamFolders': ["marvel's spider-man remastered"], 'programs': [{'name': 'spider-man.exe'}]},
    'marvels-spider-man-2': {'steamAppIDs': [2651280], 'steamFolders': ["marvel's spider-man 2"], 'programs': [{'name': 'spider-man2.exe'}]},
    'raidou-remastered': {'steamAppIDs': [2288350], 'steamFolders': ['raidou tmotsa'], 'programs': [{'name': 'raidou tmotsa.exe'}]},
    'yakuza-like-a-dragon': {'steamAppIDs': [1235140], 'steamFolders': ['yakuza like a dragon'], 'programs': [{'name': 'yakuzalikeadragon.exe'}]},
    'batman-arkham-city-goty': {'steamAppIDs': [200260], 'steamFolders': ['batman arkham city goty'], 'programs': [{'name': 'batmanac.exe'}, {'name': 'bmlauncher.exe', 'beside': 'BatmanAC.exe'}]},
    'metal-gear-solid-peace-walker': {'steamAppIDs': [2492660], 'steamFolders': ['mgs_pw']},
    'lonely-mountains-snow-riders': {'steamAppIDs': [2545360], 'steamFolders': ['lmsr'], 'programs': [{'name': 'lmsr_windows64_steam.exe'}]},
    'trails-in-the-sky-1st-chapter': {'steamAppIDs': [3375780], 'steamFolders': ['trails in the sky 1st chapter', 'sora no kiseki the 1st'], 'programs': [{'name': 'sora_1st.exe', 'steam': True}, {'name': 'sora_1st.exe', 'steam': False}]},
    'subnautica-2': {'steamAppIDs': [1962700], 'steamFolders': ['subnautica2'], 'programs': [{'name': 'subnautica2-win64-shipping.exe'}]},
    'rdr1-steam': {'steamAppIDs': [2668510], 'steamFolders': ['red dead redemption'], 'programs': [{'name': 'playrdr.exe', 'steam': True}]},
    'resident-evil-requiem': {'steamAppIDs': [3764200], 'steamFolders': ['resident evil requiem biohazard requiem'], 'programs': [{'name': 're9.exe'}]},
    'resident-evil-requiem-demo': {'steamAppIDs': [4459100], 'steamFolders': ['resident evil requiem biohazard requiem - demo'], 'programs': [{'name': 're9demo.exe'}]},
    'resident-evil-6': {'steamAppIDs': [221040], 'steamFolders': ['resident evil 6'], 'programs': [{'name': 'bh6.exe'}]},   # v2 (round 624, 93WQEN): any copy
    'resident-evil-revelations-2': {'steamAppIDs': [287290], 'steamFolders': ['resident evil revelations 2'], 'programs': [{'name': 'rerev2.exe'}]},   # v2 (round 624): any copy
    'bioshock-infinite-epic': {'programs': [{'name': 'shippingpc-xgame.exe', 'beside': 'EOSSDK-Win32-Shipping.dll'}]},
    'danganronpa-trigger-happy-havoc': {'steamAppIDs': [413410], 'steamFolders': ['danganronpa trigger happy havoc'], 'programs': [{'name': 'dr1_us.exe'}]},
    'life-is-strange-remastered': {'steamAppIDs': [1265920], 'steamFolders': ['lis - remastered'], 'programs': [{'name': 'lis-win64-shipping.exe'}]},
    'life-is-strange-2': {'steamAppIDs': [532210], 'steamFolders': ['life is strange 2'], 'programs': [{'name': 'lis2-win64-shipping.exe'}]},
    'project-zomboid': {'steamAppIDs': [108600], 'steamFolders': ['projectzomboid'], 'programs': [{'name': 'projectzomboid64.exe'}]},
    'spiderheck': {'steamAppIDs': [1329500], 'steamFolders': ['spiderheck'], 'programs': [{'name': 'spiderheckapp.exe'}]},
    'rounds': {'steamAppIDs': [1557740], 'steamFolders': ['rounds'], 'programs': [{'name': 'rounds.exe'}]},
    'plateup': {'steamAppIDs': [1599600], 'steamFolders': ['plateup'], 'programs': [{'name': 'plateup.exe'}]},
    'final-fantasy-tactics-ivalice-chronicles': {'steamAppIDs': [1004640], 'steamFolders': ['final fantasy tactics - the ivalice chronicles'], 'programs': [{'name': 'fft_enhanced.exe'}]},
    'age-of-empires-ii-hd': {'steamAppIDs': [221380], 'steamFolders': ['age2hd']},
    'fallout-new-vegas': {'steamAppIDs': [22380], 'steamFolders': ['fallout new vegas'], 'programs': [{'name': 'falloutnv.exe', 'steam': True}, {'name': 'falloutnvlauncher.exe', 'steam': True}]},
    'digimon-story-cyber-sleuth': {'steamAppIDs': [1042550], 'steamFolders': ['digimon story cyber sleuth complete edition'], 'programs': [{'name': 'digimon story cs.exe'}]},
    'final-fantasy-xv-demo': {'steamAppIDs': [671210], 'steamFolders': ['final fantasy xv playable demo'], 'programs': [{'name': 'ffxv_s.exe'}]},
    'borderlands-3': {'steamAppIDs': [397540], 'steamFolders': ['borderlands 3'], 'programs': [{'name': 'borderlands3.exe'}]},
    'zenless-zone-zero': {'steamAppIDs': [4162040], 'steamFolders': ['zenless zone zero'], 'programs': [{'name': 'hyp.exe', 'steam': True}]},
    'jetboard-joust': {'steamAppIDs': [1181600], 'steamFolders': ['jetboardjoust'], 'programs': [{'name': 'jetboardjoust.exe'}]},
    'sword-and-fairy-3': {'programs': [{'name': 'pal3.exe'}]},
    'dead-as-disco': {'steamAppIDs': [3404260], 'programs': [{'name': 'pagoda.exe'}]},
    'tung': {'steamFolders': ['tung'], 'programs': [{'name': 'tung.exe'}]},
    'darksiders-ii-deathinitive': {'steamAppIDs': [388410], 'steamFolders': ['darksiders ii deathinitive edition'], 'programs': [{'name': 'darksiders2.exe'}]},
    'nfs-most-wanted': {'programs': [{'name': 'speed.exe'}]},
    'nfs-underground-2': {'programs': [{'name': 'speed2.exe'}]},
    'sekiro-shadows-die-twice': {'steamAppIDs': [814380], 'steamFolders': ['sekiro'], 'programs': [{'name': 'sekiro.exe'}]},
    'mudrunner': {'steamAppIDs': [675010], 'steamFolders': ['mudrunner'], 'programs': [{'name': 'mudrunner.exe'}]},
    'indivisible': {'steamAppIDs': [421170], 'steamFolders': ['indivisible'], 'programs': [{'name': 'indivisible.exe'}]},
    'terraria': {'steamAppIDs': [105600], 'steamFolders': ['terraria'], 'programs': [{'name': 'terraria.exe'}]},
    'risk-of-rain-2': {'steamAppIDs': [632360], 'steamFolders': ['risk of rain 2'], 'programs': [{'name': 'risk of rain 2.exe'}]},
    'jackal': {'steamAppIDs': [3124230], 'steamFolders': ['jackal'], 'programs': [{'name': 'jackal.exe'}]},
    'stardew-valley': {'steamAppIDs': [413150], 'steamFolders': ['stardew valley'], 'programs': [{'name': 'stardew valley.exe'}]},
    'counter-strike-source': {'steamAppIDs': [240], 'steamFolders': ['counter-strike source'], 'programs': [{'name': 'cstrike_win64.exe'}]},
    'chuchel': {'steamFolders': ['chuchel'], 'programs': [{'name': 'chuchel.exe'}]},
    'botanicula': {'steamFolders': ['botanicula'], 'programs': [{'name': 'botanicula.exe'}]},
    'fallout-4': {'steamAppIDs': [377160], 'steamFolders': ['fallout 4'], 'programs': [{'name': 'fallout4launcher.exe', 'steam': True}, {'name': 'fallout4.exe', 'steam': True}]},
    'skyrim-special-edition': {'steamAppIDs': [489830], 'steamFolders': ['skyrim special edition'], 'programs': [{'name': 'skyrimselauncher.exe', 'steam': True}, {'name': 'skyrimse.exe', 'steam': True}]},
    'lego-batman-the-videogame': {'steamAppIDs': [21000], 'steamFolders': ['lego batman'], 'programs': [{'name': 'legobatman.exe'}]},
    'silent-hunter-3': {'steamAppIDs': [15210], 'steamFolders': ['silent hunter 3'], 'programs': [{'name': 'sh3.exe', 'steam': True}]},
    'just-cause-3': {'steamAppIDs': [225540], 'steamFolders': ['just cause 3'], 'programs': [{'name': 'justcause3.exe'}]},
    'glyphica-typing-survival': {'steamAppIDs': [2400160], 'steamFolders': ['glyphica'], 'programs': [{'name': 'neosphere.exe', 'beside': 'ChakraCore.dll'}]},   # build 600 (ajan38/tur600): ChakraCore's fail-fast
    # Round 602 (ajan41/tur602): the four Direct3D 8 games of the logs, env.MADEIRA_D3D8 = d3d9 from build 602 (tests/host/check-d3d8-to-d3d9.py).
    'grand-theft-auto-iii': {'steamAppIDs': [12100], 'steamFolders': ['grand theft auto 3'], 'programs': [{'name': 'gta3.exe'}]},
    'postal-2': {'steamAppIDs': [223470], 'steamFolders': ['postal2complete'], 'programs': [{'name': 'postal2.exe'}]},
    'hotline-miami': {'steamAppIDs': [219150], 'steamFolders': ['hotline_miami'], 'programs': [{'name': 'hotlinemiami_original.exe'}]},
    'max-payne-2': {'steamAppIDs': [12150], 'steamFolders': ['max payne 2 the fall of max payne'], 'programs': [{'name': 'maxpayne2.exe'}]},
    # Round 602 (ajan41/tur602): three 64-bit Direct3D 9 games whose own program asks Wine's d3d9 for a device (env.MADEIRA_D3D9_X64 = dxmt from build 602).
    'pico-park': {'steamAppIDs': [1509960], 'steamFolders': ['pico_park_online'], 'programs': [{'name': 'pico_park.exe'}]},
    'dont-starve-together': {'steamAppIDs': [322330], 'steamFolders': ["don't starve together"], 'programs': [{'name': 'dontstarve_steam_x64.exe'}]},
    'a-hat-in-time': {'steamAppIDs': [253230], 'steamFolders': ['hatintime'], 'programs': [{'name': 'hatintimegame.exe'}]},
    'the-adventures-of-elliot': {'steamAppIDs': [3483510], 'steamFolders': ['the adventures of elliot the millennium tales'], 'programs': [{'name': 'elliot-win64-shipping.exe'}]},   # build 602 (ajan45, upstream port): a 473 MiB fixed-base program
    # Round 620 (2026-10-28): the round's logs and the testers' GitHub reports (#18, #19, #25).
    'forza-horizon-4': {'steamAppIDs': [1293830], 'steamFolders': ['forzahorizon4'], 'programs': [{'name': 'forzahorizon4.exe'}]},
    'tiny-tinas-wonderlands': {'steamAppIDs': [1286680], 'steamFolders': ["tiny tina's wonderlands"], 'programs': [{'name': 'wonderlands.exe'}]},
    'far-cry-3': {'steamAppIDs': [220240], 'steamFolders': ['far cry 3'], 'programs': [{'name': 'fc3updatersteam.exe', 'steam': True}]},
    'barotrauma': {'steamAppIDs': [602960], 'steamFolders': ['barotrauma'], 'programs': [{'name': 'barotrauma.exe'}]},
    'slay-the-spire-2': {'steamAppIDs': [2868840], 'steamFolders': ['slay the spire 2'], 'programs': [{'name': 'slaythespire2.exe'}]},
    'resident-evil-3': {'steamAppIDs': [952060], 'programs': [{'name': 're3.exe', 'beside': 're_chunk_000.pak'}]},
    'marvel-cosmic-invasion': {'steamAppIDs': [2753970], 'steamFolders': ['marvel cosmic invasion'], 'programs': [{'name': 'game.exe', 'beside': 'ParisEngine.dll'}]},
    'haste': {'steamAppIDs': [1796470], 'steamFolders': ['haste'], 'programs': [{'name': 'haste.exe'}]},
    'big-walk': {'steamAppIDs': [1478500], 'steamFolders': ['big walk'], 'programs': [{'name': 'big walk.exe'}]},
    'ball-x-pit': {'steamAppIDs': [2062430]},
    'just-volleyball-demo': {'steamAppIDs': [5401090], 'steamFolders': ['just volleyball demo'], 'programs': [{'name': 'volleyballdemo.exe'}]},
    'farming-simulator-22-epic': {'programs': [{'name': 'farmingsimulator2022.exe', 'steam': False}]},
    'max-payne-3': {'steamAppIDs': [204100], 'steamFolders': ['max payne 3'], 'programs': [{'name': 'playmaxpayne3.exe', 'steam': True}, {'name': 'maxpayne3.exe', 'steam': True}]},   # round 620: the Rockstar Launcher's lines (FXY57T, YXFHTE)
    'f1-2020': {'steamAppIDs': [1080110], 'steamFolders': ['f1 2020'], 'programs': [{'name': 'f1_2020_dx12.exe'}]},   # round 620: d3d11-src.dll (4W6WMV, G1WB9T)
    'need-for-speed-the-run': {'programs': [{'name': 'need for speed the run.exe'}]},   # round 620 (Discord): EA only, matched by its program
    'bloons-td-6': {'steamAppIDs': [960090], 'steamFolders': ['bloonstd6'], 'programs': [{'name': 'bloonstd6.exe'}]},
    # round 624 (the ten analysis reports of tur624)
    'control': {'steamAppIDs': [870780], 'steamFolders': ['control'], 'programs': [{'name': 'control.exe', 'beside': 'Control_DX11.exe'}, {'name': 'control_dx11.exe'}, {'name': 'control_dx12.exe'}]},
    'no-more-room-in-hell': {'steamAppIDs': [224260], 'steamFolders': ['nmrih'], 'programs': [{'name': 'nmrih.exe'}, {'name': 'nmrih_win64.exe'}]},
    'red-faction': {'steamAppIDs': [20530], 'steamFolders': ['red faction'], 'programs': [{'name': 'redfaction.exe'}]},
    'from-the-darkness': {'programs': [{'name': 'from the darkness.exe'}]},
    'metal-gear-solid-4-master-collection': {'steamAppIDs': [2492670], 'steamFolders': ['metal gear solid 4']},
    'mycopunk': {'steamAppIDs': [3247750], 'steamFolders': ['mycopunk'], 'programs': [{'name': 'mycopunk.exe'}]},
    'arctic-awakening': {'programs': [{'name': 'arctic awakening.exe'}]},
    'supermarket-together': {'steamAppIDs': [2709570], 'steamFolders': ['supermarket together'], 'programs': [{'name': 'supermarket together.exe'}]},
    'robocop-rogue-city': {'steamAppIDs': [1681430], 'steamFolders': ['robocop rogue city'], 'programs': [{'name': 'robocop-win64-shipping.exe'}, {'name': 'robocop.exe'}]},
    'blasphemous': {'steamAppIDs': [774361], 'steamFolders': ['blasphemous'], 'programs': [{'name': 'blasphemous.exe'}]},
    'nwjs-game-exe': {'programs': [{'name': 'game.exe', 'beside': 'nw_elf.dll'}, {'name': 'game.exe', 'beside': 'nw.dll'}]},
    'kerbal-space-program': {'steamAppIDs': [220200], 'programs': [{'name': 'ksp_x64.exe'}, {'name': 'ksp.exe'}]},
    'mafia-2': {'programs': [{'name': 'mafia2.exe'}]},
    'fallout-3': {'steamAppIDs': [22300, 22370], 'steamFolders': ['fallout 3 goty', 'fallout 3'], 'programs': [{'name': 'fallout3.exe'}, {'name': 'falloutlauncher.exe'}, {'name': 'fallout3launcher.exe'}]},
    'overlord-ii': {'programs': [{'name': 'overlord2.exe'}]},
    'rise-of-the-tomb-raider': {'steamAppIDs': [391220], 'steamFolders': ['rise of the tomb raider'], 'programs': [{'name': 'rottr.exe'}]},
    'lucieneslife': {'programs': [{'name': 'lucieneslife.exe'}]},
    'ubisoft-connect': {'programs': [{'name': 'ubisoftconnect.exe'}, {'name': 'upc.exe'}]},
    'unreal-gold': {'steamAppIDs': [13250], 'steamFolders': ['unreal gold'], 'programs': [{'name': 'unreal.exe'}]},
    'repo': {'steamAppIDs': [3241660], 'steamFolders': ['repo'], 'programs': [{'name': 'repo.exe'}]},
    'osu': {'programs': [{'name': 'osu!.exe'}]},
    'outlast': {'steamAppIDs': [238320], 'steamFolders': ['outlast'], 'programs': [{'name': 'outlastlauncher.exe'}, {'name': 'olgame.exe'}]},
    'satisfactory': {'steamAppIDs': [526870], 'steamFolders': ['satisfactory'], 'programs': [{'name': 'factorygamesteam.exe'}, {'name': 'factorygameegs.exe'}]},
    'the-witcher-3': {'steamAppIDs': [292030], 'steamFolders': ['the witcher 3', 'the witcher 3 wild hunt'], 'programs': [{'name': 'witcher3.exe'}]},
    'yakuza-6-the-song-of-life': {'steamAppIDs': [1388590], 'steamFolders': ['yakuza 6 - the song of life'], 'programs': [{'name': 'yakuza6.exe'}]},
    'euro-truck-simulator-2': {'steamAppIDs': [227300], 'steamFolders': ['euro truck simulator 2'], 'programs': [{'name': 'eurotrucks2.exe'}]},
    'gal-gun-2': {'steamAppIDs': [851890], 'steamFolders': ['galgun 2'], 'programs': [{'name': 'galgun2.exe'}, {'name': 'galgun2-win64-shipping.exe'}]},
    'hello-neighbor': {'programs': [{'name': 'helloneighbor.exe'}, {'name': 'helloneighbor-win64-shipping.exe'}]},
    # round 624 (agent A S7, V0FQAS): any copy of the game (no store limit), FEX's bigger alias table
    'borderlands-goty-enhanced': {'steamAppIDs': [729040], 'steamFolders': ['borderlandsgotyenhanced'], 'programs': [{'name': 'borderlandsgoty.exe'}]},
    'dead-space-2023': {'steamAppIDs': [1693980], 'steamFolders': ['dead space (2023)']},   # no program: the 2008 game's is Dead Space.exe too
    'schedule-i': {'steamAppIDs': [3164500], 'steamFolders': ['schedule i'], 'programs': [{'name': 'schedule i.exe'}]},
    'dokimon-quest': {'steamAppIDs': [2019300]},   # no log names its folder or program yet
    'sonic-adventure-dx': {'steamAppIDs': [71250], 'programs': [{'name': 'sonic.exe'}]},
    'requiem-avenging-angel': {'steamAppIDs': [493310], 'programs': [{'name': '3dfx.exe'}]},
    'friends-vs-friends': {'steamAppIDs': [1785150], 'steamFolders': ['friends vs friends'], 'programs': [{'name': 'friends vs friends.exe'}]},
    'fallout-4-other': {'programs': [{'name': 'fallout4launcher.exe', 'steam': False}, {'name': 'fallout4.exe', 'steam': False}]},
    'skyrim-special-edition-other': {'programs': [{'name': 'skyrimselauncher.exe', 'steam': False}, {'name': 'skyrimse.exe', 'steam': False}]},
    'wrc-9': {'steamAppIDs': [1267540], 'steamFolders': ['wrc 9 fia world rally championship'], 'programs': [{'name': 'wrc9.exe'}]},
    'doki-doki-literature-club': {'steamAppIDs': [698780], 'steamFolders': ['doki doki literature club'], 'programs': [{'name': 'ddlc.exe'}]},
    'outlast-2': {'programs': [{'name': 'outlast2.exe'}]},   # round 625 (owner's item 10, agent D2 S12)
    'batman-arkham-knight': {'steamAppIDs': [208650], 'steamFolders': ['batman arkham knight'], 'programs': [{'name': 'batmanak.exe'}]},   # item 12
    'dead-rising': {'steamAppIDs': [427190], 'steamFolders': ['dead rising'], 'programs': [{'name': 'deadrising.exe'}]},   # item 13
    'reanimal': {'programs': [{'name': 'reanimal.exe'}]},   # item 14
    'pragmata': {'steamAppIDs': [3357650], 'steamFolders': ['pragmata'], 'programs': [{'name': 'pragmata.exe'}]},   # agent L4 S3-a (NGEDJT)
    'what-lies-in-the-multiverse': {'programs': [{'name': 'what lies in the multiverse.exe'}]},   # the owner's item 26 (T3CXNQ, a GOG copy)
    'muv-luv-photonflowers': {'steamAppIDs': [889700], 'steamFolders': ['muv-luv photonflowers'], 'programs': [{'name': 'muv-luv_pf.exe'}]},   # item 26 (4FD24J)
}
for ident, p in presets.items():
    assert p['match'] == MATCH.get(ident), (ident, p['match'])
assert presets['standard']['match'] is None and list(MATCH) == list(presets)[:-1]
# The Swift that applies a match (GameRecommendationList.match, GameRecommendations.swift).
match = block(lists_src, '    func match(windowsPath: String, steamAppID: Int?, fileExists: (String) -> Bool) -> GameRecommendation? {\n        let path',
              '\n    }\n')
assert 'guard last.hasSuffix(".exe") else {' in match and 'let installFolder = last == steamFolder ? steamFolder : nil' in match
assert 'if let steamAppID, match.steamAppIDs.contains(steamAppID) { return true }' in match
assert 'if let installFolder, match.steamFolders.contains(installFolder) { return true }' in match
assert 'let inSteam = steamAppID != nil || steamFolder != nil' in match
assert 'for program in match.programs where program.name == last {' in match
assert 'if let steam = program.steam, steam != inSteam { continue }' in match
assert 'if let beside = program.beside, !fileExists(folder + "\\\\" + beside) { continue }' in match
assert 'if let notBeside = program.notBeside, fileExists(folder + "\\\\" + notBeside) { continue }' in match
assert match.index('return rules.first { rule in') < match.index('for rule in rules {'), 'Steam folders before programs'

# Where the app applies them.
save = block(library, '    func save(_ entry: LibraryEntry) {', '\n    }\n')
assert save.index('let isNew = !next.contains { $0.id == entry.id || (entry.steamAppID != nil && $0.steamAppID == entry.steamAppID) }') \
    < save.index('GameRecommendations.prepare(&entry, new: isNew)') < save.index('persist(next)')
init = block(library, '    private init() {', '\n    }\n')
assert init.index('resetPhoneResolution()') < init.index('applyRecommendations()')
apply_all = block(library, '    private func applyRecommendations() {', '\n    }\n')
assert 'guard !readOnly' in apply_all and 'GameRecommendations.prepare(&next[i])' in apply_all and 'persist(next)' in apply_all
# Wave 3 (2026-10-19): the classic Game details page (its first sight, Reset to Recommended and Remove), the classic
# library's Add by hand and the earlier home screen's launch and settings sheet are deleted. First sight is decided where
# the engine decides it (LibraryModel.save, the start-up pass and ContentView.launchLibraryEntry, checked here), and BCD's
# game page shows the list and offers Reset to Recommended through the same calls.
assert 'struct LibraryDetail' not in library and 'GameSettingsSheet' not in library + content
bcd_page = (app / 'BCDGamePageLive.swift').read_text()
assert 'change(game) { entry in _ = GameRecommendations.reset(&entry) }' in bcd_page
assert 'if let rec = GameRecommendations.recommendation(for: entry) {' in bcd_page
launch = block(content, '    private func launchLibraryEntry(_ entry: LibraryEntry) {', '\n    }\n')
assert launch.index('if GameRecommendations.prepare(&entry, new: isNew) { library.save(entry) }') < launch.index('BCDLaunch.applyLibrary(entry, sessionLog: false)')
for name in ('GameRecommendations.swift', 'GameRecommendationsApply.swift'):
    assert project.count(f'/* {name} in Sources */') == 2, name   # PBXBuildFile and the Sources phase
    assert project.count(f'/* {name} */') == 3, name              # fileRef, PBXFileReference and the group
# The rules: first sight only for a game with nothing of its own; switches only on first sight or a reset.
due = block(apply_src, '    private static func due(', '\n    }\n')
assert due.index('guard mark != "", let rec = recommendation(') < due.index('let text = GameProfile(windowsPath: windowsPath).text')
assert 'guard mark != stamp(rec), mark.hasSuffix("#" + fileHash(text)) else { return nil }' in due
assert '!(text + (ownLines ?? "")).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty' in due and 'setMark("", windowsPath)' in due
# The standard list for every game but the Desktop, and later a game's own list over it as on first sight.
fallback = block(apply_src, '    static func recommendation(windowsPath: String, steamAppID: Int?) -> GameRecommendation? {', '\n    }\n')
assert 'if let rec = match(windowsPath: windowsPath, steamAppID: steamAppID, fileExists: driveFileExists) { return rec }' in fallback
assert 'return parts.count > 1 ? standard : nil' in fallback
assert 'let fromStandard = mark.hasPrefix(standardID + "#") && rec.id != standardID' in due
assert 'static let standardID = "standard"' in lists_src
assert 'entry.desktop == true ? nil : recommendation(windowsPath: entry.windowsPath, steamAppID: entry.steamAppID)' in apply_src
put = block(apply_src, '    private static func put(', '\n    }\n')
assert 'guard file.text == rec.fileText' in put and 'if kind != .updated {' in put
assert 'LibraryPrefs.setSafeSync(!on, for: windowsPath)' in put and 'LibraryPrefs.setScreen(size, for: windowsPath)' in put
home_lines = block(apply_src, '    private static func libraryLines(for windowsPath: String) -> String {', '\n    }\n')
assert 'if entry.steamAppID != nil, key.hasPrefix(path + "\\\\")' in home_lines and 'GameProfile(windowsPath: entry.windowsPath).text' in home_lines
assert 'takeEntryFields(rec, &entry)\n            LibraryModel.shared.save(entry)' in apply_src
print('PASS: first sight in the library (start-up, save, start); BCD\'s game page resets to the recommended list')

# The Swift fixture: the real files with small stand-ins for the app types they use.
stable_hash = block(files, '    static func stableHash(_ s: String) -> UInt64 {', '\n    }\n')
profile_text = block(profiles, '    var text: String {', '\n    }\n')
profile_parse = block(profiles, '    static func parse(_ text: String) -> [String: String] {', '\n    }\n')
stubs = r'''import Foundation

enum CoverStore {
''' + stable_hash + r'''}

enum DisplayMode: String { case fit, fill, stretch, aspect }

enum ResolutionChoices { static var screen720Value: String? { "1568x720" } }   // a 19.5:9 iPhone

final class LibraryModel {
    static let shared = LibraryModel()
    var entries: [LibraryEntry] = []
    func save(_ entry: LibraryEntry) {
        if let i = entries.firstIndex(where: { $0.windowsPath == entry.windowsPath }) { entries[i] = entry } else { entries.append(entry) }
    }
    static var drive: URL { URL(fileURLWithPath: ProcessInfo.processInfo.environment["MADEIRA_TEST_DRIVE"]!, isDirectory: true) }
}

final class LogStore {
    static let shared = LogStore()
    enum Level { case info, error }
    var lines: [String] = []
    func log(_ message: String, level: Level = .info) { lines.append(message) }
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

struct LibraryEntry {
    var title: String
    var windowsPath: String
    var steamAppID: Int? = nil
    var desktop: Bool? = nil
    var config: String? = nil
    var resolution = "1408x648"
    var display: String? = nil
    var fpsMode = 1
    var fastSync: Bool? = nil
    var semaphoreFastPath: Bool? = nil
}

final class PrefStore {
    static let shared = PrefStore()
    var flags: [String: Bool] = [:]
    var screens: [String: String] = [:]
}

enum LibraryPrefs {
    static let screenSizes = ["", "1280x720", "fill", "fill-mfx15", "1600x900", "1920x1080"]
    static func avx(_ path: String) -> Bool { PrefStore.shared.flags["avx|" + path] ?? false }
    static func setAVX(_ on: Bool, for path: String) { PrefStore.shared.flags["avx|" + path] = on ? true : nil }
    static func nvidia(_ path: String) -> Bool { PrefStore.shared.flags["nvidia|" + path] ?? false }
    static func setNvidia(_ on: Bool, for path: String) { PrefStore.shared.flags["nvidia|" + path] = on ? true : nil }
    static func wineVCRT(_ path: String) -> Bool { PrefStore.shared.flags["vcrt|" + path] ?? false }
    static func setWineVCRT(_ on: Bool, for path: String) { PrefStore.shared.flags["vcrt|" + path] = on ? true : nil }
    static func safeSync(_ path: String) -> Bool { PrefStore.shared.flags["safe|" + path] ?? false }
    static func setSafeSync(_ on: Bool, for path: String) { PrefStore.shared.flags["safe|" + path] = on ? true : nil }
    static func screen(_ path: String) -> String { PrefStore.shared.screens[path] ?? "" }
    static func setScreen(_ size: String, for path: String) { PrefStore.shared.screens[path] = size.isEmpty ? nil : size }
}

struct GameProfile {
    let windowsPath: String
    var url: URL? {
        URL(fileURLWithPath: ProcessInfo.processInfo.environment["MADEIRA_TEST_CONFIGS"]!, isDirectory: true)
            .appendingPathComponent(String(format: "%016llx.cfg", CoverStore.stableHash(windowsPath.lowercased())))
    }
''' + profile_text + profile_parse + '}\n'

main = r'''import Foundation

func check(_ ok: Bool, _ what: String, line: Int = #line) {
    if !ok { print("FAILED (main.swift line \(line)): \(what)"); exit(1) }
}

let R = GameRecommendations.self
let steam = #"C:\Program Files (x86)\Steam\steamapps\common"#

// The lists: the repository's recommended/recommendations.json, the app's bundle resource, read as
// the app reads it (RecommendationsStore.initialList); no download is kept here.
RecommendationsStore.builtInURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["BCD_TEST_RECOMMENDATIONS"]!)
RecommendationsStore.directory = URL(fileURLWithPath: ProcessInfo.processInfo.environment["BCD_TEST_RECS_DIR"]!, isDirectory: true)
RecommendationsStore.build = 602
check(R.active.origin == .builtIn && R.all.count == @RULE_COUNT@ && R.standard?.id == "standard", "the built-in list")
// Upstream port 2026-10-25 (build 602): The Adventures of Elliot's rule and lines are for this build and later; an older build reads the list without the rule.
check(RecommendationsStore.builtInFile()?.list(build: 601, origin: .builtIn).rules.contains { $0.recommendation.id == "the-adventures-of-elliot" } == false
      && RecommendationsStore.builtInFile()?.list(build: 602, origin: .builtIn).rules.contains { $0.recommendation.id == "the-adventures-of-elliot" } == true
      && RecommendationsStore.builtInFile()?.list(build: 601, origin: .builtIn).rules.count == @RULE_COUNT@ - 1,
      "the Elliot rule is dropped by build 601 and kept from 602")

extension GameRecommendations {
    static func rule(_ id: String) -> GameRecommendation { all.first { $0.id == id }! }
    static var rdr2Steam: GameRecommendation { rule("rdr2-steam") }
    static var gta5EnhancedSteam: GameRecommendation { rule("gta5e-steam") }
    static var gta5EnhancedOther: GameRecommendation { rule("gta5e-other") }
    static var gta5LegacySteam: GameRecommendation { rule("gta5l-steam") }
    static var gta5LegacyOther: GameRecommendation { rule("gta5l-other") }
    static var ghostOfTsushima: GameRecommendation { rule("ghost-of-tsushima") }
    static var godOfWar: GameRecommendation { rule("god-of-war") }
}

func id(_ path: String, _ app: Int? = nil, files: [String] = []) -> String {
    let have = Set(files.map { $0.lowercased() })
    return R.match(windowsPath: path, steamAppID: app, fileExists: { have.contains($0.lowercased()) })?.id ?? "none"
}

// How a game is recognised.
let rdr2 = steam + #"\Red Dead Redemption 2"#
check(id(rdr2, 1174180) == "rdr2-steam", "RDR2 by its App ID")
check(id(rdr2) == "rdr2-steam", "RDR2 by its Steam folder")
check(id("D:\\SteamLibrary\\steamapps\\common\\Red Dead Redemption 2\\") == "rdr2-steam", "another Steam library, trailing backslash")
check(id(#"C:\Games\Red Dead Redemption 2\RDR2.exe"#) == "rdr2-other" && id(#"C:\Games\Red Dead Redemption 2\PlayRDR2.exe"#) == "rdr2-other"
      && id(#"C:\Games\Red Dead Redemption 2\RDR2Launcher.exe"#) == "rdr2-other",
      "RDR2 outside Steam: the other copy's list")
check(id(rdr2 + #"\RDR2.exe"#) == "rdr2-steam" && id(rdr2 + #"\PlayRDR2.exe"#) == "rdr2-steam", "RDR2's programs inside Steam")
check(id(rdr2 + #"\Redistributables\vc_redist.x64.exe"#) == "none", "a redistributable in a game's Steam folder")
check(id(rdr2 + #"\start.bat"#) == "none" && id(steam + #"\Grand Theft Auto V Enhanced\launch.cmd"#) == "none",
      "a batch file inside a Steam game's folder")
let gta = steam + #"\Grand Theft Auto V Enhanced"#
check(id(gta, 3240220) == "gta5e-steam", "GTA V Enhanced by its App ID")
check(id(gta) == "gta5e-steam", "GTA V Enhanced by its Steam folder")
check(id(gta + #"\PlayGTAV.exe"#, files: [gta + #"\GTA5_Enhanced.exe"#]) == "gta5e-steam", "PlayGTAV.exe inside Steam is the Steam copy")
let renamed = steam + #"\GTAV Enhanced Copy"#
check(id(renamed + #"\PlayGTAV.exe"#, files: [renamed + #"\GTA5_Enhanced.exe"#]) == "gta5e-steam", "in any Steam library folder")
check(id(gta + #"\_CommonRedist\vc_redist.x64.exe"#, files: [gta + #"\GTA5_Enhanced.exe"#]) == "none", "not its redistributables")
let other = #"C:\Games\GTA V Enhanced"#
check(id(other + #"\PlayGTAV.exe"#, files: [other + #"\GTA5_Enhanced.exe"#]) == "gta5e-other", "PlayGTAV.exe elsewhere")
check(id(other + #"\GTA5_Enhanced.exe"#, files: [other + #"\GTA5_Enhanced.exe"#]) == "gta5e-other", "GTA5_Enhanced.exe elsewhere")
check(id(#"C:\GAMES\gta v enhanced\PLAYGTAV.EXE"#, files: [other + #"\gta5_enhanced.exe"#]) == "gta5e-other", "names in any case")
check(id("C:/Games/GTA V Enhanced/PlayGTAV.exe", files: [other + #"\GTA5_Enhanced.exe"#]) == "gta5e-other", "forward slashes")
check(id(#"C:\Games\GTA V\PlayGTAV.exe"#, files: [#"C:\Games\GTA V\GTA5.exe"#]) == "gta5l-other", "the legacy edition outside Steam: its own list")
check(id(#"C:\Program Files\Epic Games\GTAV\PlayGTAV.exe"#, files: [#"C:\Program Files\Epic Games\GTAV\GTA5.exe"#]) == "gta5l-other",
      "the Epic copy's PlayGTAV.exe")
check(id(#"C:\Games\GTA V\GTA5.exe"#) == "gta5l-other", "GTA5.exe outside Steam: the legacy list")
check(id(#"C:\Games\GTA V\PlayGTAV.exe"#) == "none", "PlayGTAV.exe outside Steam with neither game program beside it")
let legacy = steam + #"\Grand Theft Auto V"#
check(id(legacy, 271590) == "gta5l-steam", "GTA V Legacy by its App ID")
check(id(legacy) == "gta5l-steam", "GTA V Legacy by its Steam folder")
check(id(legacy + #"\PlayGTAV.exe"#, files: [legacy + #"\GTA5.exe"#]) == "gta5l-steam", "Legacy's PlayGTAV.exe inside Steam")
check(id(legacy + #"\GTA5.exe"#) == "gta5l-steam", "Legacy's GTA5.exe inside Steam")
check(id(legacy + #"\PlayGTAV.exe"#) == "none", "PlayGTAV.exe inside Steam with neither game program beside it")
check(id(gta + #"\PlayGTAV.exe"#, files: [gta + #"\GTA5_Enhanced.exe"#, gta + #"\GTA5.exe"#]) == "gta5e-steam",
      "GTA5_Enhanced.exe beside PlayGTAV.exe stays Enhanced, even with a GTA5.exe there")
check(id(legacy + #"\_CommonRedist\vc_redist.x64.exe"#, 271590) == "none", "not Legacy's redistributables")
// The device's RAM: GTA V Legacy's 3 GB RAM swap only under 12 GB; every other list the same everywhere.
R.deviceRAMBytes = 11_695 << 20
let onTwelve = R.all.map { $0.fileText }
check(!R.gta5LegacySteam.fileText.contains("swap-mb") && !R.gta5LegacySteam.fileText.contains("vram-mb"),
      "a 12 GB phone: no RAM swap in GTA V Legacy's list")
R.deviceRAMBytes = 16 << 30
check(R.all.map { $0.fileText } == onTwelve, "a 16 GB iPad: the same lists as a 12 GB phone")
R.deviceRAMBytes = 7_500 << 20
check(R.gta5LegacySteam.fileText.hasSuffix("env.MADEIRA_METAL_HUD_MAIN = 1\nswap-mb = 3072\n"),
      "an 8 GB phone: the 3 GB RAM swap after GTA V Legacy's list")
check(R.gta5LegacyOther.fileText.hasSuffix("env.MADEIRA_STEAM_IDENTITY = none\nswap-mb = 3072\n"),
      "an 8 GB phone: the same 3 GB RAM swap after the non-Steam Legacy list")
// round 624: Euro Truck Simulator 2 gets the BC texture clamp on a device under 10 GB (its smallRAMConfig)
check(R.all.first { $0.id == "euro-truck-simulator-2" }?.fileText.hasSuffix("dxmt = d3d11.mipClampBC=1\n") == true,
      "an 8 GB phone: the BC texture clamp after Euro Truck Simulator 2's list")
// round 625 (agent L4 S2-a): METAL GEAR SOLID 4 the same clamp (its smallRAMConfig); Outlast 2's line is for build 625 on, not this build 602
check(R.all.first { $0.id == "metal-gear-solid-4-master-collection" }?.fileText.hasSuffix("env.MADEIRA_X64_IMAGE_NOCOPY = 1\ndxmt = d3d11.mipClampBC=1\n") == true,
      "an 8 GB phone: the BC texture clamp after METAL GEAR SOLID 4's list")
check(zip(R.all, onTwelve).allSatisfy { $0.0.id == "gta5l-steam" || $0.0.id == "gta5l-other" || $0.0.id == "euro-truck-simulator-2"
                                        || $0.0.id == "metal-gear-solid-4-master-collection" || $0.0.fileText == $0.1 },
      "every other list is the same on an 8 GB phone")
R.deviceRAMBytes = 11_695 << 20
check(id(other + #"\PlayGTAV.exe"#) == "none", "PlayGTAV.exe without GTA5_Enhanced.exe beside it")
check(id(#"C:\GhostOfTsushima\GhostOfTsushima.exe"#) == "ghost-of-tsushima", "GoT by its program")
check(id(#"E:\Some Other Folder\ghostoftsushima.EXE"#) == "ghost-of-tsushima", "GoT anywhere, in any case")
check(id(steam + #"\Ghost of Tsushima DIRECTOR'S CUT"#, 2215430) == "ghost-of-tsushima", "GoT from Steam")
check(id(steam + #"\Ghost of Tsushima DIRECTOR'S CUT"#) == "ghost-of-tsushima", "GoT by its Steam folder")
check(id(steam + #"\GodOfWar"#) == "god-of-war" && id("D:\\SteamLibrary\\steamapps\\common\\God of War") == "god-of-war",
      "GoW by its Steam folder")
check(id(steam + #"\Horizon Zero Dawn"#) == "horizon-zero-dawn", "Horizon Zero Dawn by its Steam folder")
check(id(steam + #"\Horizon Zero Dawn Remastered"#, 2561580) != "horizon-zero-dawn", "the remaster is not the original: the original's lines never reach it")
check(id(steam + #"\Ghost"#, 1, files: [steam + #"\Ghost\GhostOfTsushima.exe"#]) == "none", "a library Steam game is its App ID or folder name")
check(id(#"C:\God of War\GoW.exe"#) == "god-of-war", "GoW by its program")
check(id(steam + #"\God of War"#, 1593500) == "god-of-war", "GoW from Steam")
check(id(#"C:\Games\God of War Ragnarok\GoWR.exe"#) == "none", "Ragnarok is another game")
check(id(#"C:\Horizon - Zero Down CE\HorizonZeroDawn.exe"#) == "horizon-zero-dawn", "Horizon Zero Dawn by its program (GOG)")
check(id(steam + #"\Horizon Zero Dawn\HorizonZeroDawn.exe"#) == "horizon-zero-dawn", "its program inside Steam")
check(id(steam + #"\Horizon Zero Dawn"#, 1151640) == "horizon-zero-dawn", "Horizon Zero Dawn from Steam")
check(id(#"C:\Games\Horizon Zero Dawn Remastered\HorizonZeroDawnRemastered.exe"#) == "none", "the remaster is another game")
let hfw = steam + #"\Horizon Forbidden West Complete Edition"#
check(id(hfw, 2420110) == "horizon-forbidden-west", "Horizon Forbidden West by its App ID")
check(id(hfw) == "horizon-forbidden-west", "Horizon Forbidden West by its Steam folder")
check(id(hfw + #"\HorizonForbiddenWest.exe"#) == "horizon-forbidden-west", "its program inside Steam")
check(id(#"D:\Games\HFW\horizonforbiddenwest.EXE"#) == "horizon-forbidden-west", "its program anywhere, in any case")
check(id(hfw + #"\crs-handler.exe"#) == "none", "not its crash reporter")
let crysis = steam + #"\Crysis Remastered"#
check(id(crysis, 1715130) == "crysis-remastered", "Crysis Remastered by its App ID")
check(id(crysis) == "crysis-remastered", "Crysis Remastered by its Steam folder")
check(id(crysis + #"\Bin64\CrysisRemastered.exe"#) == "crysis-remastered", "its program inside Steam")
check(id(#"C:\Games\Crysis\Bin64\Crysis.exe"#) == "none", "the original Crysis is another game")
let crysis2 = steam + #"\Crysis 2 Remastered"#, crysis3 = steam + #"\Crysis 3 Remastered"#
check(id(crysis2, 2096600) == "crysis-2-remastered" && id(crysis3, 2096610) == "crysis-3-remastered", "Crysis 2 and 3 Remastered by their App IDs")
check(id(crysis2) == "crysis-2-remastered" && id(crysis3) == "crysis-3-remastered", "by their Steam folders")
check(id(crysis2 + #"\Bin64\Crysis2Remastered.exe"#) == "crysis-2-remastered"
      && id(crysis3 + #"\Bin64\Crysis3Remastered.exe"#) == "crysis-3-remastered", "their programs inside Steam")
check(id(#"C:\Games\Crysis 2\Bin32\Crysis2.exe"#) == "none", "the original Crysis 2 is another game")
let rca = steam + #"\Ratchet & Clank - Rift Apart"#
check(id(rca, 1895880) == "ratchet-clank-rift-apart", "Rift Apart by its App ID")
check(id(rca) == "ratchet-clank-rift-apart", "Rift Apart by its Steam folder")
check(id(rca + #"\RiftApart.exe"#) == "ratchet-clank-rift-apart", "its program inside Steam")
check(id(#"D:\Games\RC\RIFTAPART.exe"#) == "ratchet-clank-rift-apart", "its program anywhere, in any case")
let msfs = steam + #"\MSFS2024"#
check(id(msfs, 2537590) == "msfs-2024", "MSFS 2024 by its App ID")
check(id(msfs) == "msfs-2024", "MSFS 2024 by its Steam folder")
check(id(msfs + #"\FlightSimulator2024.exe"#) == "msfs-2024", "its program inside Steam")
check(id(#"C:\Games\MSFS2024\FlightSimulator.exe"#) == "none", "Flight Simulator 2020's program is another game")
let milesm = steam + #"\Marvel's Spider-Man Miles Morales"#
check(id(milesm, 1817190) == "marvels-spider-man-miles-morales", "Miles Morales by its App ID")
check(id(milesm) == "marvels-spider-man-miles-morales", "Miles Morales by its Steam folder")
check(id(milesm + #"\MilesMorales.exe"#) == "marvels-spider-man-miles-morales", "its program inside Steam")
check(id(steam + #"\Marvel's Spider-Man Remastered"#, 1817070) == "marvels-spider-man-remastered", "Spider-Man Remastered has its own list (build 580), not Miles Morales'")
// Build 580's lists (tur580): each by App ID, by Steam folder and by its program inside Steam; the same program outside Steam is nobody's
// (no copy gets a list), and the original of a remaster keeps its own.
let list580: [(String, Int, String, String?)] = [
    ("BlazblueEntropyEffect", 2273430, "blazblue-entropy-effect", "BlazblueEntropyEffect.exe"),
    ("DARK SOULS III", 374320, "dark-souls-iii", #"Game\DarkSoulsIII.exe"#),
    ("Raidou TMOTSA", 2288350, "raidou-remastered", "Raidou TMOTSA.exe"),
    ("Yakuza Like a Dragon", 1235140, "yakuza-like-a-dragon", #"runtime\media\YakuzaLikeADragon.exe"#),
    ("Batman Arkham City GOTY", 200260, "batman-arkham-city-goty", #"Binaries\Win32\BatmanAC.exe"#),
    ("MGS_PW", 2492660, "metal-gear-solid-peace-walker", nil),
    ("LMSR", 2545360, "lonely-mountains-snow-riders", "LMSR_Windows64_Steam.exe"),
    ("Trails in the Sky 1st Chapter", 3375780, "trails-in-the-sky-1st-chapter", "sora_1st.exe"),
    ("Subnautica2", 1962700, "subnautica-2", #"Subnautica2\Binaries\Win64\Subnautica2-Win64-Shipping.exe"#),
    ("Red Dead Redemption", 2668510, "rdr1-steam", "PlayRDR.exe"),
]
for (folder, app, rule, exe) in list580 {
    check(id(steam + "\\" + folder, app) == rule, "\(rule) by its App ID")
    check(id(steam + "\\" + folder) == rule, "\(rule) by its Steam folder")
    if let exe {
        check(id(steam + "\\" + folder + "\\" + exe) == rule, "\(rule): its program inside Steam")
        // Round 623: Trails in the Sky's v2 also matches a program-folder start (M5G99X); every other list stays Steam-only.
        // Round 624 (owner: where a copy came from does not matter): Dark Souls III and the two Resident Evil lists too.
        // Round 624 (D2 S5): every list of this batch whose lines say nothing about Steam; the Dock-key and twin lists (rdr1-steam ...) stay Steam-only.
        let outside = ["trails-in-the-sky-1st-chapter", "dark-souls-iii", "resident-evil-6", "resident-evil-revelations-2", "blazblue-entropy-effect",
                       "raidou-remastered", "yakuza-like-a-dragon", "batman-arkham-city-goty", "lonely-mountains-snow-riders", "subnautica-2",
                       "resident-evil-requiem", "resident-evil-requiem-demo"].contains(rule) ? rule : "none"
        check(id(#"D:\Games\Copy\"# + (exe.split(separator: "\\").last.map(String.init) ?? exe)) == outside, "\(rule): the same program outside Steam gets \(outside == "none" ? "no list" : "its list")")
    }
}
// Arkham City v2 (2026-10-23, Send Log TPKM8C): its launcher started from the library takes the game's list when BatmanAC.exe is beside it;
// Arkham Asylum's BmLauncher.exe (no BatmanAC.exe beside it) does not.
let acBin = steam + #"\Batman Arkham City GOTY\Binaries\Win32"#
check(id(acBin + #"\BmLauncher.exe"#, files: [acBin + #"\BatmanAC.exe"#]) == "batman-arkham-city-goty", "Arkham City's BmLauncher.exe beside BatmanAC.exe")
check(id(steam + #"\Batman Arkham Asylum GOTY\Binaries\BmLauncher.exe"#) != "batman-arkham-city-goty", "Arkham Asylum's BmLauncher.exe is not Arkham City")
check(id(#"D:\Games\Copy\BmLauncher.exe"#, files: [#"D:\Games\Copy\BatmanAC.exe"#]) == "batman-arkham-city-goty",
      "round 624: the launcher outside Steam takes the game's list too when BatmanAC.exe is beside it")
check(id(#"D:\Games\Copy\BmLauncher.exe"#) == "none", "the launcher anywhere without BatmanAC.exe beside it gets no list")
// Build 591's lists (tur591): the same recognition, for the games that fail today.
let list591: [(String, Int, String, String?)] = [
    ("RESIDENT EVIL requiem BIOHAZARD requiem", 3764200, "resident-evil-requiem", "re9.exe"),
    ("LIS - Remastered", 1265920, "life-is-strange-remastered", #"LIS\Binaries\Win64\LiS-Win64-Shipping.exe"#),
    ("Life is Strange 2", 532210, "life-is-strange-2", #"LIS2\Binaries\Win64\LIS2-Win64-Shipping.exe"#),
    ("ProjectZomboid", 108600, "project-zomboid", "ProjectZomboid64.exe"),
    ("SpiderHeck", 1329500, "spiderheck", "SpiderHeckApp.exe"),
    ("ROUNDS", 1557740, "rounds", "ROUNDS.exe"),
    ("PlateUp", 1599600, "plateup", #"PlateUp\PlateUp.exe"#),
    ("FINAL FANTASY TACTICS - The Ivalice Chronicles", 1004640, "final-fantasy-tactics-ivalice-chronicles", "FFT_enhanced.exe"),
    ("Age2HD", 221380, "age-of-empires-ii-hd", nil),
    ("Fallout New Vegas", 22380, "fallout-new-vegas", "FalloutNVLauncher.exe"),
    ("Digimon Story Cyber Sleuth Complete Edition", 1042550, "digimon-story-cyber-sleuth", #"app_digister\Digimon Story CS.exe"#),
    ("FINAL FANTASY XV PLAYABLE DEMO", 671210, "final-fantasy-xv-demo", "ffxv_s.exe"),
    ("Borderlands 3", 397540, "borderlands-3", #"OakGame\Binaries\Win64\Borderlands3.exe"#),
    ("Zenless Zone Zero", 4162040, "zenless-zone-zero", "HYP.exe"),
    ("JetboardJoust", 1181600, "jetboard-joust", "JetboardJoust.exe"),
]
// 2026-10-23: Darksiders II Deathinitive Edition (64-bit Direct3D 9), build 595.
let list595: [(String, Int, String, String?)] = [
    ("Darksiders II Deathinitive Edition", 388410, "darksiders-ii-deathinitive", "Darksiders2.exe"),
]
for (folder, app, rule, exe) in list595 {
    check(id(steam + "\\" + folder, app) == rule, "\(rule) by its App ID")
    check(id(steam + "\\" + folder) == rule, "\(rule) by its Steam folder")
    if let exe {
        check(id(steam + "\\" + folder + "\\" + exe) == rule, "\(rule): its program inside Steam")
        check(id(#"D:\Games\Copy\"# + exe) == rule, "\(rule): its program's name matches wherever it is installed")
    }
}
// 2026-10-24: Sekiro (code page 932), MudRunner and Indivisible (64-bit Direct3D 9), build 598; and (ajan22) Risk of Rain 2, Stardew Valley and Counter-Strike: Source (by App ID, Steam folder and program name, inside Steam or not).
let list598: [(String, Int, String, String?)] = [
    ("Sekiro", 814380, "sekiro-shadows-die-twice", "sekiro.exe"),
    ("MudRunner", 675010, "mudrunner", "MudRunner.exe"),
    ("Indivisible", 421170, "indivisible", "Indivisible.exe"),
    ("Terraria", 105600, "terraria", "Terraria.exe"),
    ("Risk of Rain 2", 632360, "risk-of-rain-2", "Risk of Rain 2.exe"),
    ("Stardew Valley", 413150, "stardew-valley", "Stardew Valley.exe"),
    ("Counter-Strike Source", 240, "counter-strike-source", "cstrike_win64.exe"),
    ("Lego Batman", 21000, "lego-batman-the-videogame", "LEGOBatman.exe"),
    ("Silent Hunter 3", 15210, "silent-hunter-3", nil),
    ("Just Cause 3", 225540, "just-cause-3", "JustCause3.exe"),
]
for (folder, app, rule, exe) in list598 {
    check(id(steam + "\\" + folder, app) == rule, "\(rule) by its App ID")
    check(id(steam + "\\" + folder) == rule, "\(rule) by its Steam folder")
    if let exe {
        check(id(steam + "\\" + folder + "\\" + exe) == rule, "\(rule): its program inside Steam")
        check(id(#"D:\Games\Copy\"# + exe) == rule, "\(rule): its program's name matches wherever it is installed")
    }
}
// Upstream port 2026-10-25 (ajan45, build 602): The Adventures of Elliot, by its App ID, its Steam folder and its shipping program's name wherever it is installed. The
// launcher stub Elliot.exe is a generic name and takes nothing.
let elliotFolder = "The Adventures of Elliot The Millennium Tales"
check(id(steam + "\\" + elliotFolder, 3483510) == "the-adventures-of-elliot", "the-adventures-of-elliot by its App ID")
check(id(steam + "\\" + elliotFolder) == "the-adventures-of-elliot", "the-adventures-of-elliot by its Steam folder")
check(id(steam + "\\" + elliotFolder + #"\Elliot\Binaries\Win64\Elliot-Win64-Shipping.exe"#) == "the-adventures-of-elliot", "the-adventures-of-elliot: its program inside Steam")
check(id(#"D:\Games\Copy\Elliot-Win64-Shipping.exe"#) == "the-adventures-of-elliot", "the-adventures-of-elliot: its program's name matches wherever it is installed")
check(id(#"D:\Games\Copy\Elliot.exe"#) == "none" && id(steam + #"\The Adventures of Elliot Soundtrack"#, 3483511) == "none",
      "the launcher stub Elliot.exe and a neighbouring App ID take nothing")
check(id(steam + #"\Silent Hunter 3\sh3.exe"#) == "silent-hunter-3", "silent-hunter-3: its program inside Steam")
// 2026-10-24 (ajan26): Silent Hill 3 is also sh3.exe; Silent Hunter 3's list takes the program inside a Steam library only. LEGO Batman 2 is another game.
check(id(#"D:\Games\Silent Hill 3\sh3.exe"#) == "none" && id(steam + #"\Lego Batman 2"#, 213330) == "none" && id(#"D:\Games\Copy\LEGOBatman2.exe"#) == "none"
      && id(steam + #"\Just Cause 2"#, 8190) == "none", "Silent Hill 3 outside Steam, LEGO Batman 2 and Just Cause 2 do not take these lists")
check(id(steam + #"\Risk of Rain"#, 248820) == "none" && id(steam + #"\Stardew Valley Expanded"#) == "none", "the first Risk of Rain and a mod folder do not take these lists")
check(id(steam + #"\Counter-Strike Global Offensive"#, 730) != "counter-strike-source" && id(steam + #"\Half-Life 2"#, 220) == "none", "Counter-Strike 2 and Half-Life 2 do not take Counter-Strike: Source's list")
check(id(steam + #"\Spintires"#, 263280) == "none" && id(steam + #"\Indivisible Soundtrack"#, 1094140) == "none"
      && id(steam + #"\tModLoader"#, 1281930) == "none" && id(steam + #"\Terraria Dedicated Server"#) == "none",
      "Spintires, Indivisible's soundtrack, tModLoader and the Terraria server folder do not take MudRunner's, Indivisible's or Terraria's list")
check(id(steam + #"\Darksiders Warmastered Edition"#, 462780) == "none" && id(steam + #"\Darksiders Genesis"#, 710920) == "none",
      "the other Darksiders games do not take Darksiders II's list (their Direct3D is another story)")
// 2026-10-24: Need for Speed Most Wanted (speed.exe) and Underground 2 (SPEED2.EXE), build 598's report of a 32-bit program's crash. Non-Steam
// programs: recognised by the program's name, wherever it is installed and in any case; nothing else takes them.
for (program, rule) in [("speed.exe", "nfs-most-wanted"), ("Speed.exe", "nfs-most-wanted"), ("SPEED2.EXE", "nfs-underground-2")] {
    check(id(#"C:\games\Need for Speed Most Wanted\"# + program) == rule, "\(rule): \(program) wherever it is installed")
    check(id(#"D:\Copy\"# + program) == rule, "\(rule): \(program) on another drive")
}
check(id(#"C:\games\NFS\speed3.exe"#) == "none" && id(#"C:\games\NFS\nfsc.exe"#) == "none" && id(#"C:\games\NFS\speedrunner.exe"#) == "none",
      "other programs with a similar name take neither Need for Speed list")
// 2026-10-24 (ajan22): Chuchel and Botanicula (32-bit Adobe AIR programs): by their program's name wherever they are installed, and by their Steam folder.
for (folder, program, rule) in [("Chuchel", "Chuchel.exe", "chuchel"), ("Chuchel", "chuchel.exe", "chuchel"), ("Botanicula", "Botanicula.exe", "botanicula")] {
    check(id(#"C:\Program Files\Epic Games\X\"# + program) == rule, "\(rule): \(program) wherever it is installed")
    check(id(steam + "\\" + folder + "\\" + program) == rule, "\(rule): \(program) inside Steam")
}
check(id(steam + #"\Chuchel"#) == "chuchel" && id(steam + #"\Botanicula"#) == "botanicula", "the Steam folders take their lists")
check(id(#"C:\games\chuchel2.exe"#) == "none" && id(#"C:\games\botanicula_demo.exe"#) == "none", "other programs with a similar name take neither list")
for (folder, app, rule, exe) in list591 {
    check(id(steam + "\\" + folder, app) == rule, "\(rule) by its App ID")
    check(id(steam + "\\" + folder) == rule, "\(rule) by its Steam folder")
    if let exe {
        check(id(steam + "\\" + folder + "\\" + exe) == rule, "\(rule): its program inside Steam")
        // round 624 (owner: where a copy came from does not matter): these lists take their program wherever it is installed
        let anyCopy: Set<String> = ["dark-souls-iii", "resident-evil-6", "resident-evil-revelations-2",
            // round 624: the program matches without a Steam condition (nothing in their lines is about Steam)
            "blazblue-entropy-effect", "raidou-remastered", "yakuza-like-a-dragon", "subnautica-2", "resident-evil-requiem", "resident-evil-requiem-demo",
            "life-is-strange-remastered", "life-is-strange-2", "project-zomboid", "spiderheck", "rounds", "plateup", "final-fantasy-tactics-ivalice-chronicles",
            "digimon-story-cyber-sleuth", "final-fantasy-xv-demo", "borderlands-3", "jetboard-joust", "jackal", "f1-2020",
            "lonely-mountains-snow-riders", "batman-arkham-city-goty"]
        check(id(#"D:\Games\Copy\"# + (exe.split(separator: "\\").last.map(String.init) ?? exe)) == (anyCopy.contains(rule) ? rule : "none"),
              anyCopy.contains(rule) ? "\(rule): the same program outside Steam gets the list too (round 624)" : "\(rule): the same program outside Steam gets no list")
    }
}
// 2026-10-23 (owner): the Spider-Man games by their program's name, wherever they are installed, and by Steam's App ID and folder.
check(id(steam + #"\Marvel's Spider-Man Remastered"#, 1817070) == "marvels-spider-man-remastered" && id(steam + #"\Marvel's Spider-Man Remastered"#) == "marvels-spider-man-remastered"
      && id(steam + #"\Marvel's Spider-Man Remastered\Spider-Man.exe"#) == "marvels-spider-man-remastered" && id(#"D:\Games\SMR\Spider-Man.exe"#) == "marvels-spider-man-remastered",
      "Spider-Man Remastered by its App ID, its folder and its program's name, inside Steam or not")
check(id(steam + #"\Marvel's Spider-Man 2"#, 2651280) == "marvels-spider-man-2" && id(steam + #"\Marvel's Spider-Man 2"#) == "marvels-spider-man-2"
      && id(steam + #"\Marvel's Spider-Man 2\Spider-Man2.exe"#) == "marvels-spider-man-2" && id(#"D:\Games\SM2\Spider-Man2.exe"#) == "marvels-spider-man-2",
      "Spider-Man 2 by its App ID, its folder and its program's name, inside Steam or not")
check(id(steam + #"\Age2HD\Launcher.exe"#) == "none", "Launcher.exe is too generic a name: the Age of Empires II HD rule has no program")
check(id(#"C:\Sword and Fairy 3\PAL3.exe"#) == "sword-and-fairy-3" && id(#"D:\Games\PAL3\PAL3.exe"#) == "sword-and-fairy-3", "Sword and Fairy 3 by its program's name, wherever it is (no App ID: a local game)")
// Round 624 (agent A, seven logs): Fallout 3 has its own list now (New Vegas's lines); New Vegas's list still is not Fallout 3's.
check(id(steam + #"\Fallout 3 goty"#) == "fallout-3" && id(steam + #"\Fallout 3 goty"#, 22370) == "fallout-3" && id(#"D:\Games\Fallout 3\Fallout3.exe"#) == "fallout-3"
      && id(steam + #"\Fallout New Vegas"#, 22380) == "fallout-new-vegas", "Fallout 3 by its App ID, folder and program; Fallout: New Vegas' list is not Fallout 3's")
// 2026-10-24 (ajan28): Fallout 4 has its own list, from Steam's copy only (App ID, folder, the launcher and the game); a copy outside Steam keeps the standard list.
check(id(steam + #"\Fallout 4"#, 377160) == "fallout-4" && id(steam + #"\Fallout 4"#) == "fallout-4" && id(steam + #"\Fallout 4\Fallout4Launcher.exe"#) == "fallout-4"
      && id(steam + #"\Fallout 4\Fallout4.exe"#) == "fallout-4" && id(steam + #"\Fallout New Vegas"#, 22380) != "fallout-4",
      "Fallout 4 by its App ID, its folder and its two programs inside Steam; New Vegas is not Fallout 4")
check(id(#"C:\Fallout 4\Fallout4.exe"#) == "fallout-4-other" && id(#"D:\Games\Fallout 4\Fallout4Launcher.exe"#) == "fallout-4-other",
      "a Fallout 4 copy outside Steam gets its own list: the voice line, not the launcher swap (its launcher is how a player enables mods there)")
check(id(#"D:\Games\Skyrim\SkyrimSE.exe"#) == "skyrim-special-edition-other" && id(#"D:\Games\Skyrim\SkyrimSELauncher.exe"#) == "skyrim-special-edition-other"
      && id(steam + #"\Skyrim Special Edition\SkyrimSE.exe"#) == "skyrim-special-edition", "Skyrim SE: the Steam copy its list, another copy the voice line alone")
check(id(steam + #"\Red Dead Redemption 2"#, 1174180) == "rdr2-steam" && id(steam + #"\Red Dead Redemption 2"#) == "rdr2-steam", "RDR1's list does not take RDR2's folder or App ID")
check(id(steam + #"\MGS_PW\launcher\launcher.exe"#) == "none", "a program called launcher.exe inside Steam is nobody's (the rule has no program name)")
let gta4s = steam + #"\Grand Theft Auto IV"#
check(id(gta4s, 12210) == "gta4-steam", "GTA IV: The Complete Edition by its App ID")
check(id(gta4s) == "gta4-steam", "GTA IV: The Complete Edition by its Steam folder")
check(id(gta4s + #"\GTAIV\PlayGTAIV.exe"#) == "gta4-steam" && id(gta4s + #"\GTAIV\GTAIV.exe"#) == "gta4-steam", "its programs inside Steam")
check(id(#"C:\Grand Theft Auto IV\GTAIV.exe"#) == "none" && id(#"C:\Games\GTAIV\PlayGTAIV.exe"#) == "none",
      "GTA IV outside a Steam library (the owner's local entry) is not matched: it needs no Launcher")
let gtasa = steam + #"\GTA San Andreas - The Definitive Edition"#
check(id(gtasa, 1547000) == "gtasa-de-steam" && id(gtasa) == "gtasa-de-steam", "GTA San Andreas DE by its App ID and its Steam folder")
check(id(gtasa + #"\PlayGTASanAndreas.exe"#) == "gtasa-de-steam" && id(#"C:\Games\SA\PlayGTASanAndreas.exe"#) == "none",
      "its program inside Steam only")
let unrailed2 = steam + #"\Unrailed! 2 Back on Track"#
check(id(unrailed2, 2211170) == "unrailed-2-back-on-track" && id(unrailed2) == "unrailed-2-back-on-track", "Unrailed! 2 by its App ID and its Steam folder")
check(id(unrailed2 + #"\data_UnrailedGodot_windows_x86_64\Bootstrap.exe"#) == "none" && id(#"C:\Games\Foo\Bootstrap.exe"#) == "none",
      "Bootstrap.exe is too generic a name to be matched on its own")
let re2 = steam + #"\RESIDENT EVIL 2  BIOHAZARD RE2"#
check(id(re2, 883710) == "resident-evil-2" && id(re2) == "resident-evil-2", "Resident Evil 2 by its App ID and its Steam folder (two spaces in the folder's name)")
check(id(re2 + #"\re2.exe"#) == "resident-evil-2" && id(#"C:\Games\RE2\re2.exe"#) == "resident-evil-2", "its program, wherever it is")
let re7 = steam + #"\RESIDENT EVIL 7 biohazard"#
check(id(re7, 418370) == "resident-evil-7" && id(re7) == "resident-evil-7" && id(re7 + #"\re7.exe"#) == "resident-evil-7", "Resident Evil 7 by its App ID, its Steam folder and its program")
let exp33 = steam + #"\Expedition 33"#
check(id(exp33, 1903340) == "clair-obscur-expedition-33", "Clair Obscur: Expedition 33 by its App ID")
check(id(exp33) == "clair-obscur-expedition-33", "Clair Obscur: Expedition 33 by its Steam folder")
check(id(exp33 + #"\Expedition33_Steam.exe"#) == "clair-obscur-expedition-33"
      && id(exp33 + #"\Sandfall\Binaries\Win64\SandFall-Win64-Shipping.exe"#) == "clair-obscur-expedition-33", "its programs inside Steam")
check(id(#"C:\Games\Foo\foo.exe"#) == "none" && id(steam + #"\Portal 2"#, 620) == "none", "other games")
check(id("") == "none" && id("C:\\") == "none", "no path")
print("PASS: Steam games by App ID or steamapps\\common folder, GTA V Enhanced and RDR2 inside and outside Steam, GoT, GoW, Horizon Zero Dawn, Horizon Forbidden West, Rift Apart, Crysis Remastered and MSFS 2024 by their programs, others none")

// The lists themselves.
check(Set(R.all.map(\.id)).count == R.all.count, "ids are unique")
for rec in R.all {
    check(rec.fileText.hasSuffix("\n") && !rec.fileText.hasSuffix("\n\n"), "\(rec.id): one trailing newline")
    check(GameProfile.parse(rec.config).count == rec.config.split(separator: "\n").count - 2, "\(rec.id): every line but the comments parses")
}
check(R.gta5EnhancedSteam.switchSummary == "AVX off, NVIDIA on, Wine's C++ runtime off, fast semaphore waits on, FPS limit 30",
      R.gta5EnhancedSteam.switchSummary)
check(R.gta5EnhancedSteam.summary(home: true) == "AVX off, NVIDIA on, Wine's C++ runtime off", R.gta5EnhancedSteam.summary(home: true))
check(R.note(R.ghostOfTsushima, home: true).contains("1280x720") && !R.note(R.ghostOfTsushima, home: true).contains("semaphore"),
      R.note(R.ghostOfTsushima, home: true))
check(R.note(R.rdr2Steam).contains("@RDR2_CONFIG_LINES@ config lines") && R.note(R.rdr2Steam).contains("this screen's shape at 720 lines") && R.note(nil).contains("no recommended setup"), R.note(R.rdr2Steam))
// Every other game: the standard list; a path that names nothing: none.
check(R.recommendation(windowsPath: #"C:\Games\Foo\foo.exe"#, steamAppID: nil)?.id == "standard"
      && R.recommendation(windowsPath: steam + #"\Portal 2"#, steamAppID: 620)?.id == "standard", "every other game: the standard list")
check(R.recommendation(windowsPath: "", steamAppID: nil) == nil && R.recommendation(windowsPath: "C:\\", steamAppID: nil) == nil,
      "a path that names nothing has none")
check(R.note(R.standard).contains("standard settings: 1 config line.") && R.note(R.standard, home: true) == R.note(R.standard)
      && R.question(R.standard).contains("standard settings"), R.note(R.standard))

// First sight, the player's changes, newer lists and Reset to Recommended.
let key = "madeira.recommended.applied"
UserDefaults.standard.removeObject(forKey: key)
func mark(_ path: String) -> String? { (UserDefaults.standard.dictionary(forKey: key) as? [String: String])?[path.lowercased()] }
func file(_ path: String) -> String { GameProfile(windowsPath: path).text }

var red = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: rdr2, steamAppID: 1174180)
red.display = "fill"
LibraryPrefs.setNvidia(true, for: rdr2)
check(R.prepare(&red), "first sight takes the entry's fields")
check(file(rdr2) == R.rdr2Steam.fileText, "first sight writes the list")
check(red.fastSync == true && red.semaphoreFastPath == false && red.display == nil && red.fpsMode == 3, "first sight's entry fields (30 FPS, build 622)")
check(red.resolution == "1568x720", "this screen's shape at 720 lines")
check(!LibraryPrefs.avx(rdr2) && !LibraryPrefs.nvidia(rdr2) && !LibraryPrefs.wineVCRT(rdr2), "first sight's switches")
check(mark(rdr2)?.hasPrefix("rdr2-steam#\(R.rdr2Steam.version)#") == true, "what was written is recorded")
check(!R.prepare(&red) && file(rdr2) == R.rdr2Steam.fileText, "once")
GameProfile(windowsPath: rdr2).text = R.rdr2Steam.fileText + "fence-chain = 1\n"
check(!R.prepare(&red) && file(rdr2).hasSuffix("fence-chain = 1\n"), "a changed config is the player's")
GameProfile(windowsPath: rdr2).text = ""
check(!R.prepare(&red) && file(rdr2).isEmpty, "an emptied config stays empty")
red.fpsMode = 2
LibraryPrefs.setNvidia(true, for: rdr2)
check(R.reset(&red)?.id == "rdr2-steam" && file(rdr2) == R.rdr2Steam.fileText, "Reset writes the list again")
check(!LibraryPrefs.nvidia(rdr2) && red.fpsMode == 3, "Reset writes the list's switches (its 30 FPS too)")
// A list Madeira wrote that nobody changed follows a newer version: the config file only.
let older = "# an older list\nvram-mb = 2048\n"
GameProfile(windowsPath: rdr2).text = older
var marks = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
marks[rdr2.lowercased()] = "rdr2-steam#0#" + String(CoverStore.stableHash(older), radix: 16)
UserDefaults.standard.set(marks, forKey: key)
LibraryPrefs.setAVX(true, for: rdr2)
check(!R.prepare(&red) && file(rdr2) == R.rdr2Steam.fileText, "an untouched older list is updated")
check(LibraryPrefs.avx(rdr2) && !R.prepare(&red), "an update leaves the switches, once")
print("PASS: first sight writes the list and its switches once; changed or emptied configs stay; Reset writes it again; untouched lists follow newer versions")

var gtaEntry = LibraryEntry(title: "GTA V Enhanced", windowsPath: gta, steamAppID: 3240220)
GameProfile(windowsPath: gta).text = "vram-mb = 4096\n"
check(!R.prepare(&gtaEntry) && file(gta) == "vram-mb = 4096\n" && mark(gta) == "" && gtaEntry.fpsMode == 1, "a config of its own stays")
GameProfile(windowsPath: gta).text = ""
check(!R.prepare(&gtaEntry) && file(gta).isEmpty, "and is not filled in later")
check(R.reset(&gtaEntry)?.id == "gta5e-steam" && file(gta) == R.gta5EnhancedSteam.fileText, "GTA V Enhanced Reset")
check(gtaEntry.fpsMode == 3 && gtaEntry.semaphoreFastPath == true && LibraryPrefs.nvidia(gta) && !LibraryPrefs.avx(gta), "its switches")
let gowExe = #"C:\Games\God of War\GoW.exe"#
var gow = LibraryEntry(title: "God of War", windowsPath: gowExe)
gow.config = "dxmt = d3d11.mipClampBC=1"
check(!R.prepare(&gow) && file(gowExe).isEmpty && mark(gowExe) == "", "lines in upstream's config field are the game's own")
check(R.reset(&gow)?.id == "god-of-war" && gow.resolution == "1920x1080" && gow.config == nil, "God of War Reset")
check(LibraryPrefs.screen(gowExe) == "1920x1080" && !LibraryPrefs.safeSync(gowExe), "the home screen's forms of the switches too")
var desk = LibraryEntry(title: "Desktop", windowsPath: #"C:\windows\system32\explorer.exe"#)
desk.desktop = true
check(!R.prepare(&desk) && R.recommendation(for: desk) == nil, "the Desktop has none")
let fooExe = #"C:\Games\Foo\foo.exe"#
var foo = LibraryEntry(title: "Foo", windowsPath: fooExe)
GameProfile(windowsPath: fooExe).text = "fence-chain = 6\n"
check(!R.prepare(&foo) && file(fooExe) == "fence-chain = 6\n" && mark(fooExe) == "", "a game without a list keeps a config of its own")
check(R.reset(&foo)?.id == "standard" && file(fooExe) == R.standard!.fileText, "its Reset writes the standard list")
let barExe = #"C:\Games\Bar\bar.exe"#
var bar = LibraryEntry(title: "Bar", windowsPath: barExe)
check(R.prepare(&bar) && file(barExe) == R.standard!.fileText && bar.resolution == "1408x648" && bar.fpsMode == 1
      && !LibraryPrefs.avx(barExe) && mark(barExe)?.hasPrefix("standard#1#") == true,
      "first sight of a game without a list: the standard list, no switches")
// An untouched standard list gives way to the game's own list later, as on first sight: switches too.
let laterExe = #"C:\Later\GoW.exe"#
GameProfile(windowsPath: laterExe).text = R.standard!.fileText
marks = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
marks[laterExe.lowercased()] = "standard#1#" + String(CoverStore.stableHash(R.standard!.fileText), radix: 16)
UserDefaults.standard.set(marks, forKey: key)
var later = LibraryEntry(title: "God of War", windowsPath: laterExe)
check(R.prepare(&later) && file(laterExe) == R.godOfWar.fileText && later.resolution == "1920x1080"
      && LibraryPrefs.screen(laterExe) == "1920x1080", "an untouched standard list gives way to the game's list, switches too")
print("PASS: own configs (the file or upstream's field) stay until Reset; Reset writes every switch of the list; no list of its own: the standard list")

// The madeira-bcd home screen: keyed by the program, its own forms of the switches, drive_c looked up.
let drive = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MADEIRA_TEST_DRIVE"]!, isDirectory: true)
let folder = drive.appendingPathComponent("Games/GTA V Enhanced", isDirectory: true)
try! FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
check(FileManager.default.createFile(atPath: folder.appendingPathComponent("gta5_enhanced.EXE").path, contents: Data()), "fixture")
let play = #"C:\Games\GTA V Enhanced\PlayGTAV.exe"#
check(R.recommendation(windowsPath: play, steamAppID: nil)?.id == "gta5e-other", "drive_c is looked up in any case")
check(R.recommendation(windowsPath: #"C:\Games\GTA V\PlayGTAV.exe"#, steamAppID: nil)?.id == "standard", "a missing game file: the standard list")
R.prepare(windowsPath: play, title: "GTA V Enhanced")
check(file(play) == R.gta5EnhancedOther.fileText && LibraryPrefs.nvidia(play) && !LibraryPrefs.safeSync(play)
      && LibraryPrefs.screen(play) == "1280x720", "the home screen's first sight")
LibraryPrefs.setScreen("", for: play)
check(R.reset(windowsPath: play, title: "GTA V Enhanced")?.id == "gta5e-other" && LibraryPrefs.screen(play) == "1280x720", "the home screen's Reset")
var playEntry = LibraryEntry(title: "GTA V Enhanced", windowsPath: play)
check(!R.prepare(&playEntry), "the library's entry for the same program sees it was written")
check(R.prepare(&playEntry, new: true) && playEntry.resolution == "1280x720" && playEntry.fastSync == true,
      "an entry new to the library takes the fields of a list written before")
var page = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: rdr2, steamAppID: 1174180)
page.display = "fill"
check(R.prepare(&page, new: true) && page.display == nil && page.fastSync == true && file(rdr2) == R.rdr2Steam.fileText,
      "a Steam game's first page made before the library stored its entry")
let gotSteam = steam + #"\Ghost of Tsushima DIRECTOR'S CUT"#
let olderGoT = "# an older list\nswap-mb = 2048\n"
GameProfile(windowsPath: gotSteam).text = olderGoT
marks = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
marks[gotSteam.lowercased()] = "ghost-of-tsushima#0#" + String(CoverStore.stableHash(olderGoT), radix: 16)
UserDefaults.standard.set(marks, forKey: key)
var gotEntry = LibraryEntry(title: "Ghost of Tsushima", windowsPath: gotSteam, steamAppID: 2215430)
check(R.prepare(&gotEntry, new: true) && file(gotSteam) == R.ghostOfTsushima.fileText && gotEntry.resolution == "1280x720",
      "a new entry whose untouched older list is updated takes the list's fields too")
let ownExe = #"C:\Games\Ghost Own\GhostOfTsushima.exe"#
GameProfile(windowsPath: ownExe).text = "dxil-tess = 1\n"
var own = LibraryEntry(title: "Ghost of Tsushima", windowsPath: ownExe)
check(!R.prepare(&own, new: true) && mark(ownExe) == "" && own.resolution == "1408x648" && !LibraryPrefs.avx(ownExe),
      "a config of its own stays its own for a new entry too")
let got = #"C:\GoT\GhostOfTsushima.exe"#
R.prepare(windowsPath: got, title: "Ghost of Tsushima")
check(LibraryPrefs.avx(got) && LibraryPrefs.nvidia(got) && LibraryPrefs.screen(got) == "1280x720", "GoT on the home screen")
// Lines in the library entry's upstream config field for the same program are the game's own.
let otherGoW = #"C:\Other\GoW.exe"#
LibraryModel.shared.entries = [LibraryEntry(title: "God of War", windowsPath: otherGoW, config: "dxmt = d3d11.mipClampBC=1")]
R.prepare(windowsPath: otherGoW, title: "God of War")
check(file(otherGoW).isEmpty && mark(otherGoW) == "" && LibraryPrefs.screen(otherGoW).isEmpty, "the library's own lines count on the home screen")
let lib2 = #"D:\Library2\steamapps\common\Red Dead Redemption 2"#
GameProfile(windowsPath: lib2).text = "vram-mb = 2304\n"
LibraryModel.shared.entries = [LibraryEntry(title: "Red Dead Redemption 2", windowsPath: lib2, steamAppID: 1174180)]
R.prepare(windowsPath: lib2 + #"\RDR2.exe"#, title: "Red Dead Redemption 2")
check(file(lib2 + #"\RDR2.exe"#).isEmpty && mark(lib2 + #"\RDR2.exe"#) == "",
      "the library Steam game's own config counts for its program on the home screen")
let gowHome = #"C:\Home\GoW.exe"#
LibraryModel.shared.entries = [LibraryEntry(title: "God of War", windowsPath: gowHome, config: "dxmt = d3d11.mipClampBC=1")]
check(R.reset(windowsPath: gowHome, title: "God of War")?.id == "god-of-war" && file(gowHome) == R.godOfWar.fileText
      && LibraryModel.shared.entries[0].resolution == "1920x1080" && LibraryModel.shared.entries[0].config == nil,
      "a home screen Reset reaches the library entry for the same program")
check(LogStore.shared.lines.contains { $0.hasPrefix("[recommended] Red Dead Redemption 2: rdr2-steam v\(R.rdr2Steam.version) written (first sight)") }, "log line")
UserDefaults.standard.removeObject(forKey: key)
print("PASS: the home screen shares the files and records; Safe thread sync and Screen size take the list's values")

// A newer list, as a download brings it (GameRecommendationsSource.swift puts it in use): the same rules
// apply. An untouched config follows it at its next prepare (the file only), a config the player changed
// is never written, a game seen for the first time and Reset to Recommended take it whole.
LibraryModel.shared.entries = []
let libA = #"E:\Lib1\steamapps\common\Red Dead Redemption 2"#, libB = #"E:\Lib2\steamapps\common\Red Dead Redemption 2"#
var untouched = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: libA, steamAppID: 1174180)
var changed = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: libB, steamAppID: 1174180)
check(R.prepare(&untouched) && R.prepare(&changed) && file(libA) == R.rdr2Steam.fileText && file(libB) == R.rdr2Steam.fileText,
      "first sight with the built-in list")
GameProfile(windowsPath: libB).text = R.rdr2Steam.fileText + "fence-chain = 1\n"
let builtInRDR2 = R.rdr2Steam
var newer = try! RecommendationsFile.parse(try! Data(contentsOf: RecommendationsStore.builtInURL!))
let at = newer.rules.firstIndex { $0.id == "rdr2-steam" }!
newer.version += 1
newer.rules[at].version += 1
newer.rules[at].config.append(RecommendationLine(text: "env.MADEIRA_DEVICE_STATS_EXTRA = 1"))
newer.rules[at].config.append(RecommendationLine(text: "env.MADEIRA_LATER_BUILD = 1", minBuild: 604))
newer.rules[at].switches.avx = true
R.install(newer.list(build: RecommendationsStore.build, origin: .downloaded))
check(R.rdr2Steam.version == builtInRDR2.version + 1 && R.rdr2Steam.fileText == builtInRDR2.fileText + "env.MADEIRA_DEVICE_STATS_EXTRA = 1\n",
      "the newer list in use, without the line for a later build")
LibraryPrefs.setAVX(false, for: libA)
check(!R.prepare(&untouched) && file(libA) == R.rdr2Steam.fileText && !LibraryPrefs.avx(libA),
      "an untouched config follows the newer list, its switches stay")
check(file(libB).hasSuffix("fence-chain = 1\n") && !file(libB).contains("EXTRA") && !R.prepare(&changed) && !file(libB).contains("EXTRA"),
      "a config the player changed is never written")
check(R.reset(&changed)?.version == builtInRDR2.version + 1 && file(libB) == R.rdr2Steam.fileText && LibraryPrefs.avx(libB),
      "Reset to Recommended writes the newer list and its switches")
let libC = #"E:\Lib3\steamapps\common\Red Dead Redemption 2"#
var fresh = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: libC, steamAppID: 1174180)
check(R.prepare(&fresh) && file(libC) == R.rdr2Steam.fileText && LibraryPrefs.avx(libC), "a game seen for the first time takes it whole")
check(LogStore.shared.lines.contains("[recommended] Red Dead Redemption 2: rdr2-steam v\(builtInRDR2.version + 1) written (updated)"),
      "the update's log line")
check(R.followsList(windowsPath: libA) == true && R.followsList(windowsPath: libC) == true, "untouched games follow their list")
GameProfile(windowsPath: libA).text = R.rdr2Steam.fileText + "fence-chain = 1\n"
let ownLib = #"E:\Own\steamapps\common\Red Dead Redemption 2"#
GameProfile(windowsPath: ownLib).text = "vram-mb = 2304\n"
var ownEntry = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: ownLib, steamAppID: 1174180)
check(!R.prepare(&ownEntry) && mark(ownLib) == "", "a config of its own is kept")
check(R.followsList(windowsPath: libA) == false && R.followsList(windowsPath: #"E:\Never\Seen.exe"#) == nil
      && R.followsList(windowsPath: ownLib) == false, "a changed config or one of its own does not; a game never seen: nil")
R.reload()
check(R.active.origin == .builtIn && R.rdr2Steam == builtInRDR2, "the next start reads the built-in list again (nothing was kept)")
UserDefaults.standard.removeObject(forKey: key)
print("PASS: a newer list reaches untouched configs (file only), never a changed one; first sight and Reset take it whole")
'''

# Round 2026-10-24 (ajan20, build 598): three lists from the logs of 2026-10-23/24, each new line carrying minBuild 598.
#  - Resident Evil Requiem Demo (Steam 4459100) and the full game: d3d12-pso-cached-blob = 1 (ID3D12PipelineState::GetCachedBlob answers a blob; the demo
#    ended right after it, tests/host/check-d3d12-pso-cached-blob.py); the demo also keeps auto-tune's NOCOPY line, like the full game.
#  - Resident Evil 6 (Steam 221040) and Revelations 2 (Steam 287290): env.MADEIRA_D3D9 = emulated, the standard frontend, over a global "Native
#    ARM64" choice, which makes both call address 0 a few seconds after the first frame (rule d3d9-native-null-call).
_d3d12blob = {'line': 'd3d12-pso-cached-blob = 1', 'minBuild': 598}
assert _d3d12blob in _cfg('resident-evil-requiem') and _d3d12blob in _cfg('resident-evil-requiem-demo')
assert presets['resident-evil-requiem-demo']['lines'][2:] == [_std, _nocopy, 'd3d12-pso-cached-blob = 1'] and presets['resident-evil-requiem-demo']['version'] == 2 \
    and presets['resident-evil-requiem-demo']['switches'] == '', presets['resident-evil-requiem-demo']
assert {'line': _nocopy, 'minBuild': 576} in _cfg('resident-evil-requiem-demo')
assert sorted(r['id'] for r in recs['rules'] if 'd3d12-pso-cached-blob' in json.dumps(r['config'])) == ['resident-evil-requiem', 'resident-evil-requiem-demo'], \
    'only the two Resident Evil Requiem lists carry the cached-blob line'
_d3d9emu = {'line': 'env.MADEIRA_D3D9 = emulated', 'minBuild': 598}
for _id in ('resident-evil-6', 'resident-evil-revelations-2'):
    # v2 (round 624, agent D2: 93WQEN, a copy from outside Steam at 60 FPS): the same lines, the program match without the Steam condition.
    assert presets[_id]['lines'][2:] == [_std, 'env.MADEIRA_D3D9 = emulated'] and presets[_id]['version'] == 2 and presets[_id]['switches'] == '', presets[_id]
    assert _d3d9emu in _cfg(_id), _id
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_D3D9 =' in json.dumps(r['config'])) == ['resident-evil-6', 'resident-evil-revelations-2'], \
    'only the two Resident Evil lists set the Direct3D 9 frontend'
assert '"MADEIRA_D3D9"' in (app / 'GameRecommendationsList.swift').read_text() and 'guard value == "emulated"' in (app / 'GameRecommendationsList.swift').read_text()

#  - Fallout: New Vegas (Steam 22380): the registry line from build 591 (the launcher passes since 591/592) plus a diagnostic from build 598: 4 logs on 4 devices
#    (builds 592) end 25-62 s into the game with a write through a NULL object in FalloutNV.exe+0x757AA9 on the loading thread; the last 64 Direct3D 9 calls
#    with their results (MADEIRA_D3D9_LAST, build 597) name the call that failed. Not a fix; only this list carries it besides Darksiders II.
#    v3 (build 623): the older NVIDIA card (GeForce 7900 GS on nv4_disp.dll, d3d9.customDriver), upstream Madeira's cure for the same crash address.
assert presets['fallout-new-vegas']['lines'][2:] == [_std, 'env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1', 'env.MADEIRA_D3D9_LAST = 1', 'gpu-card = nvidia-7900gs'] \
    and presets['fallout-new-vegas']['version'] == 3 and presets['fallout-new-vegas']['switches'] == '', presets['fallout-new-vegas']
assert {'line': 'gpu-card = nvidia-7900gs', 'minBuild': 623} in _cfg('fallout-new-vegas')
# Round 624 (agent A, seven logs): Fallout 3, the same Gamebryo render path, carries New Vegas's card too.
assert sorted(r['id'] for r in recs['rules'] if 'nvidia-7900gs' in json.dumps(r['config'])) == ['fallout-3', 'fallout-new-vegas'], 'only New Vegas and Fallout 3 carry the older NVIDIA card'
assert {'line': 'env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1', 'minBuild': 591} in _cfg('fallout-new-vegas')
assert {'line': 'env.MADEIRA_D3D9_LAST = 1', 'minBuild': 598} in _cfg('fallout-new-vegas')
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_D3D9_LAST' in json.dumps(r['config'])) == ['darksiders-ii-deathinitive', 'fallout-3', 'fallout-new-vegas']

#  - BioShock Infinite: Complete Edition from the Epic Games Store (program ShippingPC-XGame.exe with EOSSDK-Win32-Shipping.dll beside it; the Steam copy has
#    no such file): eight logs of one tester end 1.5-3 s after the start with ExitProcess(1) and no reason. Four diagnostic lines from build 598 (the game's own
#    text logs mirrored whole, the files it opens) name the reason in the next log. Not a fix; only this list carries MADEIRA_GUEST_LOG.
assert presets['bioshock-infinite-epic']['lines'][2:] == [_std, 'env.BCD_EPIC_HOST_START = 1', 'env.MADEIRA_GUEST_LOG = all', 'env.MADEIRA_GUEST_LOG_LIMIT = 1500',
                                                          'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 30000'] \
    and presets['bioshock-infinite-epic']['version'] == 1 and presets['bioshock-infinite-epic']['switches'] == '', presets['bioshock-infinite-epic']
for _l in ('env.BCD_EPIC_HOST_START = 1', 'env.MADEIRA_GUEST_LOG = all', 'env.MADEIRA_GUEST_LOG_LIMIT = 1500', 'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 30000'):
    assert {'line': _l, 'minBuild': 598} in _cfg('bioshock-infinite-epic'), _l
assert sorted(r['id'] for r in recs['rules'] if 'BCD_EPIC_HOST_START' in json.dumps(r['config'])) == ['bioshock-infinite-epic', 'farming-simulator-22-epic'], \
    'only the BioShock Infinite and Farming Simulator 22 (Epic) lists hold the host start (FS22: round 620, 5C4SAB)'
# Round 624: the diagnostic lists of Blasphemous, the NW.js games and osu! mirror the game's own log too (each to name its stop).
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_GUEST_LOG' in json.dumps(r['config'])) == ['bioshock-infinite-epic', 'blasphemous', 'danganronpa-trigger-happy-havoc', 'doki-doki-literature-club', 'friends-vs-friends', 'nwjs-game-exe', 'osu'], \
    'only the BioShock Infinite (Epic), Danganronpa and (round 624) Blasphemous, NW.js, osu! and Friends vs Friends lists mirror the whole log'
assert sorted(r['id'] for r in recs['rules'] if 'MADEIRA_FILE_TRACE =' in json.dumps(r['config'])) == ['bioshock-infinite-epic', 'blasphemous', 'danganronpa-trigger-happy-havoc', 'darksiders-ii-deathinitive', 'doki-doki-literature-club', 'nfs-most-wanted', 'nfs-underground-2', 'overlord-ii', 'terraria']   # + round 624's diagnostic lists
assert '"MADEIRA_GUEST_LOG"' in (app / 'GameRecommendationsList.swift').read_text() and 'guard value == "all"' in (app / 'GameRecommendationsList.swift').read_text()

#  - Danganronpa: Trigger Happy Havoc (DR1_us.exe): two logs end 1 s after the start, the program having asked for a window of 16711308 x 16711308 pixels, Direct3D 9
#    taking 32767 x 32767 for its back buffer and Metal refusing it. Four diagnostic lines from build 598 (the game's own text logs mirrored whole, the files it opens)
#    name what it reads. Not a fix.
assert presets['danganronpa-trigger-happy-havoc']['lines'][2:] == [_std, 'env.MADEIRA_GUEST_LOG = all', 'env.MADEIRA_GUEST_LOG_LIMIT = 800',
                                                                   'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 30000'] \
    and presets['danganronpa-trigger-happy-havoc']['version'] == 1 and presets['danganronpa-trigger-happy-havoc']['switches'] == '', presets['danganronpa-trigger-happy-havoc']
for _l in ('env.MADEIRA_GUEST_LOG = all', 'env.MADEIRA_GUEST_LOG_LIMIT = 800', 'env.MADEIRA_FILE_TRACE = 1', 'env.MADEIRA_FILE_TRACE_LIMIT = 30000'):
    assert {'line': _l, 'minBuild': 598} in _cfg('danganronpa-trigger-happy-havoc'), _l

with tempfile.TemporaryDirectory(prefix='madeira-recommended-') as directory:
    temporary = Path(directory)
    compiler = shutil.which('swiftc')
    if compiler:
        (temporary / 'Stubs.swift').write_text(stubs)
        # the RDR2 list's config lines (its two comment lines are not config)
        rdr2_config_lines = sum(1 for line in presets['rdr2-steam']['lines']
                                if line.strip() and not line.strip().startswith('#'))
        (temporary / 'main.swift').write_text(main.replace('@RDR2_CONFIG_LINES@', str(rdr2_config_lines))
                                              .replace('@RULE_COUNT@', str(len(presets))))
        (temporary / 'drive').mkdir()
        (temporary / 'configs').mkdir()
        (temporary / 'recs').mkdir()
        binary = temporary / 'recommended'
        subprocess.run([compiler] + [str(app / name) for name in ('GameRecommendations.swift', 'GameRecommendationsList.swift',
                                                                  'GameRecommendationsSource.swift', 'GameRecommendationsApply.swift',
                                                                  'ConfigCatalog.generated.swift')]
                       + [str(temporary / 'Stubs.swift'), str(temporary / 'main.swift'), '-o', str(binary)], check=True)
        env = dict(os.environ, MADEIRA_TEST_DRIVE=str(temporary / 'drive'), MADEIRA_TEST_CONFIGS=str(temporary / 'configs'),
                   BCD_TEST_RECOMMENDATIONS=str(recs_path), BCD_TEST_RECS_DIR=str(temporary / 'recs'))
        subprocess.run([str(binary)], check=True, env=env)
    elif args.require_swift:
        raise SystemExit('Swift compiler required for the recommended-settings fixture')
    else:
        print('SKIP: Swift compiler unavailable locally; macOS CI must run this check with --require-swift')
