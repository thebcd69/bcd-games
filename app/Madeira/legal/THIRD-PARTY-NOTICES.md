# Third-party components

**BCD** is a fork of **Madeira** by Will Faust and its contributors. Everything
below about Madeira stays true for BCD. BCD's own files (marked "BCD Material" in
their headers) are GPL-3.0-only with the Madeira Converter Exception and the
additional terms under GPL-3.0 section 7 in `LICENSE-BCD.md`; files BCD changed
keep their authors' licences and notices. BCD's changes to Wine are
LGPL-2.1-or-later, like Wine.

Madeira is built from several upstream projects plus original work. Each
component keeps its own license. This file records what is here, what ships in
the binary, and what is still unresolved.

Madeira's own code is under **GPL-3.0-or-later** (`LICENSE`). The combined
application as distributed is under **GPL-3.0**, because BCD's own files are
GPL-3.0-only (`LICENSE-BCD.md`). Each upstream keeps its own license; the
dependency license texts are in `LICENSES/`. See "Why GPL-3.0-or-later" below.

## Components that ship in the built app

| Component | Upstream license | **Madeira's fork** | Notes |
|---|---|---|---|
| **Wine** | LGPL-2.1-or-later | **GPL-3.0-or-later** | Fork relicensed under LGPL-2.1 §3, which expressly permits applying the ordinary GPL to a copy. `ntdll`, `wineserver`, `win32u`, ARM64EC loader modified for iOS. |
| **FEX-Emu** | MIT | upstream MIT + **modifications GPL-3.0-or-later** | Forked. x86-64 → ARM64 translation. |
| **DXMT** | MIT | upstream MIT + **modifications GPL-3.0-or-later** | Forked. D3D11 → Metal. |
| **DXMT — Direct3D 9 / DXSO frontend** | **LGPL-2.1-or-later** | **LGPL-2.1-or-later**, kept as received | Written by **David Acevedo** (his commit `fe69cd3`, "feat(d3d9): add a Direct3D 9 frontend") and imported into `dxmt` from his fork of the same upstream: `https://github.com/dacevedo12/dxmt.git`, tag `v0.4-d3d9`, commit `e8dd4c656dcb74a6d970a30a397d1558b0e3fb2b`. That tree's licence is the DXMT project notice ("Copyright (c) 2023-2026 Feifan He for CodeWeavers", LGPL-2.1-or-later), which postdates upstream's MIT→LGPL relicense, so the import is **not** MIT. It is kept under LGPL-2.1-or-later as received: the LGPL-2.1 §3 option to distribute a copy under the ordinary GPL is not exercised, and that choice is left to the maintainer. 125hz's additions to the D3D9 path are licensed like the DXMT row. Licence text in `dxmt/COPYING.LIB` and `LICENSES/LGPL-2.1.txt`; the file-by-file list is in `dxmt/LICENSE-MADEIRA.md`. |
| **rpmalloc** | 0BSD | 0BSD + **Will Faust's modifications GPL-3.0-or-later** | Nested submodule of FEX, forked to `willfaust/rpmalloc`. Commits by Ryan Houdek are **not** relicensed. |
| **GMP** 6.3.0 | **LGPL-3.0-or-later** or GPL-2.0-or-later | Static (`libgmp.a`). |
| **Nettle / Hogweed** 3.10.1 | **LGPL-3.0-or-later** or GPL-2.0-or-later | Static (`libnettle.a`, `libhogweed.a`). |
| **GnuTLS** 3.8.9 | LGPL-2.1-or-later | Static (`libgnutls.a`). Used by Wine's bcrypt/secur32/crypt32. |
| **FreeType** 2.13.3 | FreeType License (FTL), chosen from its FTL / GPL-2.0 dual licence | Static, merged into `libwin32u_unix.a` (`build/freetype-ios/build.sh`) | Wine's font rendering. Portions of this software are copyright © 2024 The FreeType Project (www.freetype.org). All rights reserved. The software is based in part on the work of the FreeType Team. Licence text: `app/Madeira/legal/LICENSES/FTL-FreeType-2.13.3.txt`. |
| **Libraries inside Wine's modules** | per library, below | Unmodified, built into Wine's Windows modules from Wine's `libs/` | capstone (3-clause BSD-style, with LLVM's licence for its LLVM-derived parts), compiler-rt (University of Illinois/NCSA or MIT), FAudio (zlib), FluidSynth (LGPL-2.1-or-later), GSM 06.10 (its own permissive licence), libjpeg (IJG: this software is based in part on the work of the Independent JPEG Group), jxrlib (BSD-2-Clause), Little CMS (MIT), OpenLDAP (OpenLDAP Public License), mpg123 (LGPL-2.1), musl (MIT), libpng (libpng licence), libtiff (libtiff licence), LibTomCrypt (public domain / WTFPL), vkd3d (LGPL-2.1-or-later), zlib (zlib). Their licence files, as Wine ships them: `app/Madeira/legal/LICENSES/wine-libs/`. |
| **FFmpeg** 7.1.1 — libavformat, libavcodec, libavutil, libswresample | **LGPL-2.1-or-later** | Static (`libavformat.a`, `libavcodec.a`, `libavutil.a`, `libswresample.a`). Configured `--disable-gpl --disable-nonfree --disable-version3 --disable-everything`, then only: the WMA-family decoders (`wmav1,wmav2,wmapro,wmalossless,xma1,xma2`), the `mp1,mp2,mp3` and `pcm_u8,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le` decoders, the Windows Media Video decoders (`wmv1,wmv2,wmv3,vc1`; they select FFmpeg's h263 decoder code, and its `h263` parser comes with it), the `mp3`, `wav`, `mov` (MP4/MOV/M4A) and `asf` demuxers and the `mpegaudio` parser. No H.264, HEVC or AAC decoder or parser (those streams are decoded by Apple's VideoToolbox/AudioToolbox), no protocols, no muxers, no zlib. No GPL, version3 or non-free component is enabled, so the result is LGPL-2.1-or-later. Used only by `build/ntdll-unix/winegstreamer_unixlib_ios.c`, `build/ntdll-unix/winegstreamer_wmv_ios.c` and `build/ntdll-unix/wg_parser_av_ios.c`, winegstreamer's unix side on this port (the Windows WMA and WMV decoder MFT/DMOs, and the wg_parser behind quartz's MP3/WAV splitters and Media Foundation's MP4 source); upstream Wine implements all of it with GStreamer, which does not exist on iOS; and by `build/ntdll-unix/winedmo_unixlib_ios.c`, winedmo's unix side (the ASF demuxer behind Wine's ASF media source, for the games that opt in), which is taken from Wine's `dlls/winedmo` (LGPL-2.1-or-later, Copyright 2024 Rémi Bernon for CodeWeavers) and carries a "Modified by BCD" notice. |
| **TimGM6mb SoundFont** (the General MIDI sound bank for games' MIDI music) | **GPL-2.0** (as Debian's copyright file for `timgm6mb-soundfont` records it; Copyright 2004 Tim Brechbill, 2010 David Bolton, who tidied it for MuseScore). The author told the MuseScore project that some of the samples are his own and that the rest are public domain or under the GPL | Unmodified data file (5,969,788 bytes), downloaded at build time by `tools/fetch-soundfont.sh` into `app/Madeira/soundfont/` (SHA-256 `c5378b62028c920cb11e4803327983fee2f2cdff5dc89c708e39da417e51c854`, checked; two sources give the same file: Debian's archive and the MuseScore repository) and bundled; it is **not** committed to this repository | Played by Apple's AUMIDISynth when a game opens a MIDI output device (`build/ntdll-unix/bcd_midi_synth_ios.m`). It is a data file read by Apple's synthesizer, not a program linked with BCD, so it stays under its own licence beside BCD's as part of an aggregate (GPL-2.0 and GPL-3.0 section 5). A SoundFont is its own source: the file is the form it is edited in. Licence text: `LICENSES/GPL-2.0.txt` (in the app: `legal/LICENSES/GPL-2.0.txt`); the notice that ships beside it: `app/Madeira/soundfont/NOTICE-TimGM6mb.txt`. A bank of the player's own (Files > BCD > SoundFonts) is the player's, never part of BCD. |
| **{fmt}** | MIT | Static (`libfmt.a`), via FEX. |
| **xxHash** | BSD-2-Clause | Static (`libxxhash.a`). |
| **Cephes** | permissive (Moshier) | Static (`libcephes_128bit.a`), via FEX. |
| **Berkeley SoftFloat 3e** | BSD-3-Clause | Static (`libsoftfloat_3e.a`), via FEX. |
| **Steam sign-in, owned library and downloads (`app/Madeira/SwiftSteam/`)** | Derived from Jfishin's Madeira Steam client, used with the author's permission (see below); Jfishin confirmed that he wrote the depot downloader himself | GPL-3.0-or-later + Converter Exception, Copyright 2026 Jfishin, 125hz | Sign-in to Steam's public `IAuthenticationService` over HTTPS and a Keychain token store; a Steam connection (WebSocket over TLS) that lists the account's owned games; downloads from Steam's content servers into Madeira Dock's Steam library folder. File-by-file provenance and the licence audit of the downloader: `docs/STEAM_SIGNIN.md`, `docs/STEAM_LIBRARY.md`. No Valve or third-party Steam code or binaries, and no Steam Cloud, launch-emulator or DRM code. |
| **Epic Cloud saves (`app/Madeira/BCDEpic/EpicFormats.swift`, `EpicCloudSaves.swift`)** | Upstream Madeira's files: GPL-3.0-or-later + Converter Exception; `EpicFormats.swift` Copyright 2026 Akshay CM. `EpicCloudSaves.swift` is a port of parts of **Legendary** (`https://github.com/derrod/legendary`, GPL-3.0-or-later, derrod and contributors): `api/egs.py`, `core.py`, `utils/savegame_helper.py`, `models/chunk.py`, `models/manifest.py`, `utils/rolling_hash.py`. Madeira's `docs/EPIC.md` credits the game client to Akshay CM (Madeira #265) and the cloud save code and its fixtures to Tabîrcă Nicolae-Eduard (Madeira #207); intraducine combined them in Madeira #443 (see "Code from Madeira after BCD's fork") | Kept with their own headers; `EpicCloudSaves.swift` carries BCD's changes marked in its header (the URLSession transport is not carried; the date formatters are integer arithmetic). BCD's `BCDEpicCloudCore.swift` is BCD's own and `BCDEpicCloud.swift` is adapted from upstream Madeira's `EpicCloud.swift` (its header says so) | Compares a game's saves with Epic's cloud save snapshots, only for a game the player turned it on for, and writes a new snapshot only on the player's choice. The rest of BCD's Epic engine (`BCDEpic/`, `BCDContent/`) is BCD's own code, written from protocol facts. |
| **GOG install script and redistributables (`app/Madeira/BCDGOG/GOGInstallScript.swift`, `GOGSetup.swift`)** | Upstream Madeira's files: GPL-3.0-or-later + Converter Exception, Copyright 2026 Madeira contributors. They follow **Playport's** `GOGInstallScript` and `GOGDependencies` (`https://github.com/playportdev/playport`, commit `412de5f`, GPL-3.0-or-later; carried by upstream Madeira from willfaust/Madeira #479) | Kept with their own headers; `GOGSetup.swift` carries BCD's changes marked in its header (BCD's own helpers instead of upstream's app types, the result folder, BCD's wording, an argument filter). Notice: `app/Madeira/legal/PLAYPORT.txt` | Reads a GOG build's `goggame-<id>.script` (its registry values are written into the prefix before a start) and GOG's dependency repository (which redistributables a build needs and which installer runs). The rest of BCD's GOG (`BCDGOG/`, `BCDContent/`) is BCD's own code, written from GOG's public replies and community documentation; the formats follow Heroic's `gogdl` (GPL-3.0) and no gogdl code is included. |
| **Zstandard educational decoder** | BSD-3-Clause or GPL-2.0 | BSD-3-Clause selected | `app/Madeira/SwiftSteam/zstd_edu.c/.h` (Meta Platforms). Decodes Steam's zstd content chunks. Madeira's error-recovery wrapper (Jfishin's, kept) is serialized across threads. Licence text in `LICENSES/ZSTD-BSD.txt`. |
| **liblzma** | Public domain / 0BSD | System library | Linked dynamically from the iOS SDK (`liblzma.tbd`) for Steam's LZMA content chunks; nothing is bundled. |
| **Madeira Dock (`madeira-dock`, built into `arm64ec-windows/dockhost.exe`)** | original work, Copyright 2026 125hz | GPL-3.0-or-later + Converter Exception | Headless host for Valve's Steam client, a separate Windows program started in the Wine session. Built from the submodule by `build/madeira-dock/build.sh`; not committed as a binary. Its statically linked LLVM/MinGW-w64 runtime notices ship in `dock-notices.txt`. Valve's client files are downloaded by the user from Valve and are not covered by Madeira's licence. See `docs/MADEIRA_DOCK.md`. |
| **StikJIT** 1.9.0 | MPL-2.0 | Bundled XCFramework in the iOS 26 JIT helper | Enables JIT for Madeira through the device's RSD tunnel. Exact corresponding source: [`StikDebug/StikJIT` tag `1.9.0`](https://github.com/StikDebug/StikJIT/tree/1.9.0); license text: `LICENSES/MPL-2.0.txt`. The framework contains **idevice**, Copyright 2026 Jackson Coxson, under MIT; source: [`jkcoxson/idevice`](https://github.com/jkcoxson/idevice), license text: `LICENSES/MIT-idevice.txt`. |
| **On-device pairing** (`libmadeira_rppairing.a`) | **idevice** 0.1.68, Copyright 2026 Jackson Coxson, MIT; its Rust dependencies under MIT, Apache-2.0, BSD-3-Clause or ISC | Madeira's wrapper (`build/rppairing-ios`) **GPL-3.0-or-later**; dependencies unmodified, static in the app | iOS 27 on-device remote pairing for Built-in StikJIT (`app/Madeira/JITPairing.swift`, docs/JIT.md). Built from crates.io at the versions pinned in `build/rppairing-ios/Cargo.lock`. Every crate in the binary, with its licence files, is listed in `app/Madeira/legal/LICENSES-rppairing-crates.txt` (bundled as `legal/LICENSES-rppairing-crates.txt`), regenerated by `build/rppairing-ios/build.sh`. idevice's licence text: `LICENSES/MIT-idevice.txt`. |
| **libxml2** 2.12.10 and **libxslt** 1.1.45 | MIT (Daniel Veillard and contributors) | Unmodified, statically linked into Wine's `msxml3.dll` (`arm64ec-windows`) | Wine's bundled copies (`wine/libs/xml2`, `wine/libs/xslt`), built with the module by `build/wine-pe/build-modules.sh`. Licence texts in `LICENSES/MIT-libxml2.txt` and `LICENSES/MIT-libxslt.txt`. |
| **NVAPI SDK headers** | MIT (NVIDIA Corporation & affiliates) | Unmodified headers, compiled into DXMT's `nvapi64.dll` (`arm64ec-windows`) | `dxmt/external/nvapi`. DXMT's own NVAPI implementation answers only with Madeira's opt-in "Report an NVIDIA GPU" switch; no NVIDIA binary is shipped. Licence text in `LICENSES/MIT-NVAPI.txt`. |
| **d3d8to9** v1.16.0 (crosire) | BSD-2-Clause | Unmodified source (`third-party/d3d8to9`), built by `tools/build-d3d8to9-dll.sh` as the separate DLL `i386-windows/d3d8to9.dll` | Direct3D 8 on Direct3D 9 (which is DXMT's in a 32-bit session) for old games, opt-in per game (`env.MADEIRA_D3D8 = d3d9`); Wine's `d3d8.dll` stays the default. Copyright (C) 2015 Patrick Mours. Licence text: `LICENSES/BSD-2-d3d8to9.txt`; the project's source and tag are recorded in `third-party/d3d8to9/BCD-SOURCE.txt`. |
| **cnc-ddraw** v7.1.0.0 (FunkyFr3sh) | MIT; inside it: LodePNG (zlib licence), Khronos GL headers (MIT), `DetourEnumerateModules` from Microsoft Detours (MIT), CRC-32 by Craig Bruce (public domain), mingw-w64's `versionhelpers.h` | Unmodified source (`third-party/cnc-ddraw`, without its two Microsoft DirectX SDK headers and without Detours), built by `tools/build-cnc-ddraw-dll.sh` as the separate DLL `i386-windows/cnc-ddraw.dll` | A DirectDraw replacement for old 2D games (scaling, borderless full screen, Direct3D 9 or GDI drawing), opt-in per game (`env.MADEIRA_DDRAW = cnc`); Wine's `ddraw.dll` stays the default. Copyright (c) 2022 github.com/FunkyFr3sh. Licence texts: `LICENSES/MIT-cnc-ddraw.txt`, `LICENSES/zlib-lodepng.txt`, `LICENSES/MIT-Detours.txt`; provenance in `third-party/cnc-ddraw/BCD-SOURCE.txt`. |
| **Mesa** 25.0.7 (OSMesa, Zink, softpipe; the Mesa project, mesa3d.org) | MIT; individual files carry other permissive licences (the source's `docs/license.rst`, copied into the bundle as `licenses/Mesa-license.rst`) | **Patched**: `build/mesa-ios/patches` (eight patches, offered under Mesa's MIT licence) | Separate dylib `gl/libOSMesa.dylib`, the desktop OpenGL backend of BCD's OpenGL driver (OSMesa draws through Zink): used only by a game whose `gl-backend` is `zink`. Built by `build/mesa-ios/build.sh` from the unmodified 25.0.7 release tarball (SHA-256 in the script) plus those patches; the binary is not committed. Licence text: `LICENSES/MIT-Mesa.txt`. |
| **MoltenVK** 1.4.2 (Khronos Group; The Brenwill Workshop Ltd) | Apache-2.0; inside it SPIRV-Cross and SPIRV-Tools (Apache-2.0) and cereal (BSD-3-Clause) | Unmodified. | Separate dylib `gl/libMoltenVK.dylib` (Vulkan on Metal), the Vulkan driver Mesa's Zink runs on. Built by `build/moltenvk-ios/build.sh` from the v1.4.2 tag (its commit is pinned in the script) and the dependencies its `fetchDependencies` pins; not committed. The script copies the licence texts of MoltenVK, SPIRV-Cross, SPIRV-Tools and cereal into the bundle's `licenses/` (`MoltenVK-Apache-2.0.txt`, `SPIRV-Cross-Apache-2.0.txt`, `SPIRV-Tools-Apache-2.0.txt`, `cereal-BSD-3-Clause.txt`). |
| **LuaJIT** 2.1 (Mike Pall) | MIT | Unmodified source at the commit pinned in `build/luajit-x64/build.sh`, built by it for x86-64 Windows | The DLL `compat/love/lua51.dll` (interpreter only, GC64): loaded in place of a LÖVE game's own `lua51.dll` when that one is an x64 LuaJIT that needs memory below 2 GB, only in a session whose game has `gl-backend` on (`build/wineserver/luajit_compat.c`); the game's file is never changed. The script copies LuaJIT's licence into the bundle as `licenses/LuaJIT-MIT.txt`. |
| **7-Zip** 26.05 (Igor Pavlov) | LGPL-2.1-or-later; BSD 3-clause for its LZFSE and Zstandard decoders (`CPP/7zip/Compress/LzfseDecoder.cpp`, `C/ZstdDec.c`); BSD 2-clause for `C/Xxh64.c`; parts public domain. The RAR decoders carry the "unRAR licence restriction" and are **not built** | Unmodified source (`third-party/7zip`, the project's `7z2605-src.tar.xz`, SHA-256 `0b6080b8c3e7a579161d2d495224c0e51c467576cb4c7048b0ab485ef44cc22e`), built by `tools/build-7zip.py` as the two separate Windows programs `arm64ec-windows/7zFM.exe` (the File Manager) and `7z.exe` (the command-line program) | An archive manager in the Wine desktop's Start menu ("7-Zip" folder); BCD's runtime installer also unpacks NVIDIA's PhysX package with `7z.exe`. Built with 7-Zip's own `DISABLE_RAR` / `DISABLE_RAR_COMPRESS` code selection, so `.rar` archives are not supported and the unRAR restriction does not apply to anything BCD ships. Copyright (C) 1999-2026 Igor Pavlov. The project's `License.txt` is kept as received: `LICENSES/7-Zip-License.txt`; provenance and what is excluded: `third-party/7zip/BCD-SOURCE.txt`. The programs are linked statically against llvm-mingw's runtime (libc++, libunwind: Apache-2.0 with LLVM exception; the MinGW-w64 runtime: `LICENSES/MinGW-w64-runtime.txt`). Separate programs started by the user; nothing of 7-Zip is linked into BCD. |
| **Wine Mono** 11.0.0 | Mono: LGPL or MIT X11 per part; FNA: Ms-PL and MIT; FAudio, FNA3D, MojoShader, SDL3: zlib; winforms, wpf, monoDX, System.Speech and Wine Mono's own code: MIT (`build/wine-mono/COPYING`) | Prebuilt runtime, unmodified except `lib/mono/4.5/mscorlib.dll` (4 IL bytes in `GC.Collect`, `build/wine-mono/bundle.sh`), bundled as `wine-mono/` with that COPYING only in a development build made after `build/wine-mono/fetch.sh`; **release builds do not include it** (the packaging step removes `wine-mono/`). Instead the app downloads it from WineHQ when the user asks (setup or Settings › .NET Framework, `app/Madeira/WineMono.swift`), checks it against the same SHA-256, unpacks it without `lib/mono/*-api` and applies the same mscorlib patch on the device | Runs .NET Framework programs; Wine's mscoree loads it. The x86 tarball from `dl.winehq.org`, SHA-256 pinned in `build/wine-mono/pin.sh`. The compile-time reference assemblies (`lib/mono/*-api`, which include the GPL-licensed ICSharpCode.SharpZipLib) are not bundled. Exact corresponding source: `wine-mono-11.0.0-src.tar.xz` from the same folder; see "Corresponding source" below. |

## Why GPL-3.0-or-later

The intent is that derivatives stay open source. LGPL deliberately permits
proprietary applications to link against the covered work, which does not serve
that intent; GPL requires distributed derivative and combined works to remain
GPL-compatible and to offer source.

The dependencies permit this. Wine, GnuTLS and FFmpeg are LGPL-2.1-**or-later** and GMP
and Nettle are LGPL-3.0-or-later; LGPL explicitly allows a combined work to be
distributed under GPL terms, and the MIT and BSD components impose no obstacle.

What GPL does and does not achieve here, stated plainly so it is not
over-relied on:

- Obligations attach on **distribution**. Someone may modify this privately and
  never publish anything.
- It does **not** cover games, data files or other independent programs merely
  run through Madeira. They are separate works.
- It cannot stop anyone independently reimplementing the same functionality.
- FEX-Emu and DXMT code already published under MIT **remains available under
  MIT**. Choosing GPL here cannot revoke a grant those projects already made.
  If the intent is for changes inside the submodule forks to be GPL too, those
  repositories need their own licensing decision; this file governs the main
  repository.

## Each fork carries its own license notice

`THIRD-PARTY-NOTICES.md` in this repository does **not** relicense code that
lives in a separate submodule. Each fork therefore carries its own
`LICENSE-MADEIRA.md` and `CONTRIBUTING.md` stating what is licensed how:

- `wine/LICENSE-MADEIRA.md` — the LGPL-2.1 §3 conversion, exactly what changed
  and the two deliberate exceptions.
- `FEX/LICENSE-MADEIRA.md`, `dxmt/LICENSE-MADEIRA.md` — upstream MIT
  preserved; Madeira's modifications GPL-3.0-or-later. `dxmt`'s notice
  also records the Direct3D 9 / DXSO import, file by file, which is kept under
  LGPL-2.1-or-later as received.
- `FEX/External/rpmalloc/LICENSE-MADEIRA.md` — 0BSD preserved; only Will
  Faust's commits are GPL, and authorship is distinguishable via `git log`.

### What relicensing does and does not achieve

The goal is that **distributed** derivatives stay open source. Stated honestly:

- **It is not retroactive.** The FEX, DXMT and Wine forks were public before
  this change, under their upstream permissive/lesser licenses. Anyone who
  already obtained a copy keeps those rights, and that grant cannot be revoked.
  Only contributions from 2026-08-28 onward are GPL-only.
- Upstream projects are unaffected and their code stays available from them
  under its original license.
- The GPL constrains distribution, not private modification or internal use.
- It does not cover games or other independent programs merely run through
  Madeira.

## Original vs. derived work in this repository

Do **not** assume that everything outside the submodules is original. It is not.

- `build/ntdll-unix/`, `build/wineserver/`, `build/win32u-unix/` and similar are
  substantially **Wine-derived**. Files such as `build/ntdll-unix/loader_ios.c`
  are forks of upstream Wine sources and retain upstream copyright headers
  (e.g. "Copyright (C) 2020 Alexandre Julliard"). Roughly 2,700 files under
  `build/` carry an upstream copyright notice of some kind.
- `build/gnutls-ios/` contains build scripting only; the library sources are
  fetched separately (see the open issue below).
- `app/`, `tools/` and `tests/` are largely original, but contain vendored and
  derived files too.
- `app/Madeira/SwiftSteam/` is derived from Jfishin's Madeira Steam client. Jfishin
  gave permission to use it ("do whatever you want with it") in the Madeira
  Discord server on 2026-09-22; Will Faust is a member of that server and saw
  the message. Details and a per-file audit: `docs/STEAM_SIGNIN.md`.
- `app/Madeira/SwiftItch/` (the itch.io store) and `app/Madeira/SwiftEA/` (the experimental EA store) are Madeira's, taken on
  2026-11-05 from Madeira 5fda6a0 (v0.2.0): `ItchAPI.swift` and `ItchArchive.swift` by ft7t8drgwf-wq; `itch_7z.c`, `itch_7z.h` and
  the files of `SwiftEA/` by the Madeira contributors; `SwiftEA/EARegistry.swift` is 125hz's registry writer under names of its
  own. GPL-3.0-or-later with the Madeira Converter Exception; their headers are kept and BCD's changes are marked "Modified by
  BCD". The itch.io calls follow go-itchio (MIT) and the EA protocol work follows Maxima (GPL-3.0; Sean Kahler, Nick Whelan,
  Paweł Lidwin and the Maxima contributors) as knowledge only: no go-itchio and no Maxima code is included. `itch_7z.c` is written
  from 7-Zip's published 7z format description and calls zlib and liblzma, which the iOS SDK provides.

Rather than claim authorship of whole directories: **original Madeira-authored
files that do not carry another license notice are licensed under
GPL-3.0-or-later.** Files carrying their own copyright or license header are
governed by that header.

## Code from Madeira after BCD's fork (willfaust/Madeira)

BCD is a fork of Madeira ([willfaust/Madeira](https://github.com/willfaust/Madeira)) by Will Faust and its contributors,
forked at Madeira commit `279fd0f` of 2026-10-15. After that point BCD took code from Madeira's repository, and from
Madeira's Wine, FEX-Emu and DXMT forks, in several rounds. This section says what it is, how it came in, who wrote it and under
which licence.

**What entered, and when.** The date is that of BCD's own commit that first held the lines.

| Date | What | Lines |
|---|---|---|
| 2026-09-29 | Will Faust's swap tier (Madeira commit `a15332c`, "Swap tier: memory pool of extension-owned memory as the first stage"), merged into BCD from Madeira's main of that day: `MemoryHostProtocol.h`, `MemoryHostTest.h` and `.m`, `MemoryPool.m`, `MadeiraMemoryHost/MemoryHost.m` and a part of `virtual_ios.c` | 418 |
| 2026-10-25 | BCD's first port of the Madeira pull requests merged after the fork point: thin reservations (Connor Gow, #227), clipboard paste (alantangok), Steam game language and download sheet (wamei, #381, #382), Steam Cloud remote cache and off switch (wamei, 125hz, #404, #237), narrowed image base and thread QoS (meshoklv, #385, #401), exe window and Dock starting screen (125hz, NevesHenry, #240, #243, #398), guest stdin and stdout pipes (SirDank), mouse wheel (NevesHenry, #413), arena guard (Tomba247, #386), shared object alignment (alantangok, #377), thermal frame cap (125hz, #246), VC++ runtime tools (byKAYRA, #251) and others | 2,192 |
| 2026-10-26 to 2026-10-31 | The older NVIDIA card for Fallout 3 and Fallout: New Vegas (ft7t8drgwf-wq, Madeira `adec653`), a 32-bit program's branch to a low address (Madeira `726686e`) and small follow-ups | 44 |
| 2026-11-03 | LSE read-modify-write atomics on aliased pages (Ash Hopkins, Madeira `b7640ef`) | 282 |
| 2026-11-04 | The first lines of Madeira's Social Club table for helper build 2.4.0.296 (125hz, Madeira commit `15ebc3d` of 2026-11-03, #559), in BCD's commit of that day; BCD took the whole table on 2026-11-05 | 82 |
| 2026-11-05 | The port of Madeira v0.2.0 (Madeira commit `5fda6a0`, 2026-11-03), with Social Club helper build 2.4.0.296 (Madeira #559): the EA app and itch.io stores, Steam game versions, Workshop and downloads from a PC on the same network, the GOG install script and redistributables, Epic cloud saves, the OpenGL driver with Mesa and MoltenVK, keeping games and downloads running in the background, Direct3D 12 and memory items, log redaction and diagnostics | 13,751 |

In all, **16,769 lines in 161 files** of this repository are identical to code that 23 people wrote for Madeira, counted as
described below. BCD's own changes to those files are marked "Modified by BCD" in the file headers where the files carry such a note.
The code in BCD's submodule forks is covered by the forks' own notices (see "The submodule forks" below).

**Licence position.** The code in this section is Madeira's. It is licensed under **GPL-3.0-or-later with the Madeira Converter Exception**
(`LICENSE`, `LICENSE-EXCEPTION.md`), except its lines in the Wine unix side (`build/*-unix/`, `build/wineserver/`), which are
**LGPL-2.1-or-later** like Wine (`docs/LICENSING.md`); the copyright stays with the contributors named here, who contributed it under Madeira's
`CONTRIBUTING.md`. BCD took it under those terms, and BCD's additional terms under GPL-3.0 section 7 (`LICENSE-BCD.md`) do not
apply to it, including where it sits in a file that also holds BCD Material: those terms cover only what BCD itself wrote. A file that is
Madeira's own keeps Madeira's header; a file that holds some of Madeira's code carries a "Portions copyright" line in its header naming the
author, the source and the date (GPL-3.0 section 5). Where a file also carries a Wine header, Wine's notices stay as they are.

**Counts.** Counts are non-trivial lines identical to Madeira's, by source. A line is non-trivial when, with its white space
collapsed, it has at least 12 characters other than spaces and an alphanumeric run of at least three, and it is not a lone keyword, brace or comment
marker. A line counts for a source when Madeira introduced that text after the fork point (it is not in Madeira's tree of 2026-10-15),
BCD did not have the text earlier than Madeira did, it sits in a run of at least three such lines from the same source in the file, and the
file has at least five such lines from that source (three or four where the BCD commit that brought them is that source's port). Lines
in generated files, licence texts, Xcode project files and vendored third-party libraries are left out. Other cut-offs move the total by
no more than about 15%. "Source" is a Madeira pull request, or a commit where the pull request is not recorded in Madeira's history; a pull request
number in Madeira is `https://github.com/willfaust/Madeira/pull/<number>`.

### Sources

| Source | Author | In Madeira | Taken by BCD | Lines | Files | What |
|---|---|---|---|---|---|---|
| commit `a15332c` | Will Faust | 2026-09-29 | 2026-09-29 | 418 | 6 | Will Faust's swap-tier commit (a15332c, merged into BCD on 2026-09-29) |
| commit `89cc3e0` | byKAYRA | 2026-10-11 | 2026-10-23 to 2026-10-25 | 19 | 2 | tools: harden fetch-vcruntime.sh and check-vcruntime.py |
| #227 | Connor Gow | 2026-10-08 | 2026-10-25 | 312 | 2 | ntdll: thin reservations for 1-2 GB read-write MEM_RESERVE |
| commit `abca30a` | alantangok | 2026-10-17 | 2026-10-25 | 211 | 8 | feat: paste iPad clipboard text into Windows programs |
| #381 | wamei | 2026-10-18 | 2026-10-25 | 171 | 6 | Steam downloads: choose the game language |
| #385 | meshoklv | 2026-10-18 | 2026-10-25 | 158 | 1 | ntdll iOS: give a narrowed image base a low name; VirtualQuery reports only allocatable memory as free |
| #401 | meshoklv | 2026-10-21 | 2026-10-25 | 142 | 2 | ntdll-unix: opt-in QoS class per guest thread by thread name (MADEIRA_THREAD_QOS) |
| #404 | wamei | 2026-10-20 | 2026-10-25 | 113 | 3 | Steam Cloud: list API saves in the session client's remotecache.vdf |
| #399 | Jelly-RayTian | 2026-10-21 | 2026-10-25 | 96 | 3 | build: detect optional rusage diagnostic fields in the iOS SDK |
| commit `9806664` | alantangok | 2026-10-17 | 2026-10-25 | 84 | 5 | fix: keep paste in the keyboard bar and pace FIFO input delivery |
| commit `4e568c1` | SirDank | 2026-10-16 | 2026-10-25 | 82 | 3 | Connect the guest's stdin to a pipe, behind an opt-in flag |
| #240 | 125hz | 2026-10-10 | 2026-10-25 | 81 | 2 | Exe window: hand the fixed base to a relaunched copy of the same program |
| #223 | domainus | 2026-10-07 | 2026-10-25 | 68 | 2 | SteamSession: resume a service call once when its send fails after disconnect |
| commit `4eea7e5` | Tomba247 | 2026-10-18 | 2026-10-25 | 53 | 1 | arena-guard: keep fixed/hinted guest reservations out of the FEX arena |
| #377 | alantangok | 2026-10-17 | 2026-10-25 | 50 | 1 | fix: align wineserver shared object allocations on ARM64 |
| commit `ba617bb` | SirDank | 2026-10-16 | 2026-10-25 | 47 | 2 | Give the guest's stdout its own pipe, behind an opt-in flag |
| #237 | 125hz | 2026-10-05 | 2026-10-25 | 44 | 3 | Steam Cloud: a switch in Settings › Steam turns it off completely |
| #246 | 125hz | 2026-10-10 | 2026-10-25 | 42 | 1 | Display: opt-in thermal frame cap |
| commit `bbe0b12` | wamei | 2026-10-19 | 2026-10-25 | 40 | 2 | Executable window: size it for a large fixed-base exe |
| #389 | meshoklv | 2026-10-20 | 2026-10-25 | 39 | 2 | Madeira Dock: replace Steam discovery values left by a client that is gone |
| commit `91e1e57` | alantangok | 2026-10-17 | 2026-10-25 | 36 | 3 | fix: queue repeated pastes and cancel from keyboard controls |
| #243 | 125hz | 2026-10-10 | 2026-10-25 | 30 | 4 | Dock start: keep restoring born-minimized windows after the starting screen |
| #398 | NevesHenry | 2026-10-20 | 2026-10-25 | 29 | 3 | Dock: the starting screen reveals the game's own message box |
| #413 | NevesHenry | 2026-10-22 | 2026-10-25 | 27 | 2 | Hardware input: the GCMouse wheel scrolls vertically |
| commit `8deb759` | 125hz | 2026-10-24 | 2026-10-25 | 26 | 5 | Host checks: the definition, not #227's forward declaration; #240's wi |
| #363 | SirDank | 2026-10-16 | 2026-10-25 | 24 | 1 | madeira.cfg: MADEIRA_ENV_LOCKED keeps a host's own names from env.* lines |
| #251 | byKAYRA | 2026-10-10 | 2026-10-25 | 22 | 1 | tools: add fetch-vcruntime.sh and check-vcruntime host test |
| #382 | wamei | 2026-10-18 | 2026-10-25 | 18 | 3 | Steam download sheet: the game's download size and size on disk |
| #349 | 125hz | 2026-10-10 | 2026-10-25 | 16 | 2 | Audio: cover a whole RemoteIO slice for clients that queue one engine period |
| #362 | SirDank | 2026-10-16 | 2026-10-25 | 16 | 2 | JITAllocator: accessors for the early VA reservation |
| commit `facd2cc` | alantangok | 2026-10-17 | 2026-10-25 | 16 | 4 | fix: route Command-V through the active text bridge |
| #360 | SirDank | 2026-10-16 | 2026-10-25 | 14 | 2 | WineProcessBridge: wine_process_exit_code() for how the program ended |
| #462 | JMRBDev | 2026-09-13 | 2026-10-25 | 12 | 1 | Graphics backends, Direct3D and build packaging |
| commit `39c7465` | Kesavamoorthi G | 2026-10-06 | 2026-10-25 | 7 | 1 | PrefixExtractor: reject ".." and absolute paths in tar entries |
| #244 | 125hz | 2026-10-10 | 2026-10-25 | 6 | 1 | Throttle DXMT's memory census by default |
| commit `9344146` | Kesavamoorthi G | 2026-10-06 | 2026-10-25 | 4 | 1 | PrefixExtractor: validate tar size field and empty names |
| commit `20c5218` | wamei | 2026-10-19 | 2026-10-25 | 4 | 1 | pool-pair: also pair the two runs above the window when the single poo |
| commit `a6acc4e` | byKAYRA | 2026-10-13 | 2026-10-25 | 3 | 1 | tools: include mfc140 and mfc140u in fetch and check scripts |
| commit `adec653` | ft7t8drgwf-wq (insertfloppy) | 2026-10-26 | 2026-10-28 | 18 | 2 | Library: "Older NVIDIA card (Direct3D 9)" for Fallout 3 and New Vegas |
| #469 | ft7t8drgwf-wq (insertfloppy) | 2026-10-27 | 2026-10-28 to 2026-11-05 | 18 | 2 | ntdll iOS: a 32-bit program's branch to a low address faults in the gu; Game details: Pause threads fully, automatic for Unity IL2CPP games |
| commit `74e05ae` | spitefulowl | 2026-10-08 | 2026-10-30 to 2026-11-05 | 63 | 1 | ntdll iOS: arena patch for Final Fantasy Tactics - The Ivalice Chronic |
| commit `0db1b82` | Nikita Kiselov | 2026-10-06 | 2026-11-03 | 8 | 1 | Native alias writes: keep FEX's code invalidation, split spanning stor |
| #461 | Ash Hopkins | 2026-10-10 | 2026-11-03 to 2026-11-05 | 820 | 7 | Runtime, input, launch settings and diagnostics (commits: Redact credential-like arguments from the command lines Ma; ntdll iOS: emulate LSE read-modify-write atomics into alia) |
| #559 | 125hz | 2026-11-04 | 2026-11-04 to 2026-11-05 | 1418 | 11 | Social Club: the renderer bridge for helper build 2.4.0.296 |
| #523 | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 2410 | 11 | EA app section: sign-in, library and downloads without the EA app (commits: Add EA app support: sign-in, downloads and launches withou; EA download: Madeira's own DEFLATE decoder in place of the) |
| #462 | Connor Gow | 2026-10-08 | 2026-11-05 | 1481 | 22 | Graphics backends, Direct3D and build packaging (commits: OpenGL: a WGL driver for iOS, with OpenGL ES and desktop O; build: Mesa (OSMesa + Zink) and MoltenVK for iOS) |
| #520 | ft7t8drgwf-wq (insertfloppy) | 2026-11-01 | 2026-11-05 | 1115 | 6 | itch.io: unpack .7z downloads, and archives inside the download |
| #499 | ft7t8drgwf-wq (insertfloppy) | 2026-10-31 | 2026-11-05 | 1094 | 8 | itch.io store: the account's games, free games, downloads and installs |
| #505 | llamajose | 2026-11-01 | 2026-11-05 | 793 | 6 | Steam: download games from a PC on the same network |
| #443 | Tabîrcă Nicolae-Eduard | 2026-10-26 | 2026-11-05 | 773 | 7 | Epic: combine game downloads and cloud saves (combined by intraducine in #443) |
| #498 | ft7t8drgwf-wq (insertfloppy) | 2026-10-31 | 2026-11-05 | 640 | 8 | Keep games and downloads running when Madeira leaves the screen (commits: Keep games and downloads running when Madeira leaves the s; Background run: CPU cap, endpoint started in front, stalle) |
| #491 | ft7t8drgwf-wq (insertfloppy) | 2026-10-30 | 2026-11-05 | 558 | 7 | Steam: Steam Workshop page per game and a Workshop area |
| #462 | 125hz | 2026-10-10 | 2026-11-05 | 467 | 5 | Graphics backends, Direct3D and build packaging (commits: Mesa/Zink: vertex outputs match fragment inputs for Metal ; Mesa/Zink: shader disk cache on, kept per build under the ) |
| #443 | Akshay CM | 2026-10-26 | 2026-11-05 | 379 | 1 | Epic: combine game downloads and cloud saves (combined by intraducine in #443) |
| #490 | ft7t8drgwf-wq (insertfloppy) | 2026-10-30 | 2026-11-05 | 348 | 8 | Steam: pick a game's version (beta branches), with beta codes |
| #551 | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 325 | 4 | Add a GOG store: sign-in, library, downloads, repair and Galaxy servic |
| #461 | Ixion | 2026-10-24 | 2026-11-05 | 179 | 2 | Runtime, input, launch settings and diagnostics |
| #462 | spitefulowl | 2026-10-08 | 2026-11-05 | 154 | 3 | Graphics backends, Direct3D and build packaging (commits: madeira-d3d12: decode BC uploads on GPUs without BC sampli; madeira-d3d12: 2D views for DXBC texture2d bindings) |
| #526 | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 148 | 2 | Log the faulting code and its callers on fatal guest faults |
| #516 | 125hz | 2026-10-28 | 2026-11-05 | 125 | 2 | Swap tier donor pool, D3D12 pipeline archives, performance overlay and engine fixes |
| #517 | 125hz | 2026-10-28 | 2026-11-05 | 115 | 2 | Follow-ups from device logs: address pressure, Dock fastsync, images above 4 GB, SRW diagnostics (commits: WoW64: name the holders of a stuck SRW lock; FEX records p; WoW64: hand idle window placeholders back under address pr) |
| #471 | Jelly-RayTian | 2026-10-28 | 2026-11-05 | 107 | 2 | ntdll: reserve enough startup storage for long working directories |
| #493 | ft7t8drgwf-wq (insertfloppy) | 2026-10-30 | 2026-11-05 | 106 | 4 | Steam: faster repair start, and recover from refused depot file lists (commits: Steam: start checks and repairs sooner, and say what the w; Steam: recover from refused depot file lists) |
| #510 | alantangok | 2026-10-22 | 2026-11-05 | 70 | 2 | iPad input follow-ups: hidden-cursor absolute pointer, input wake masks |
| #477 | intraducine | 2026-10-29 | 2026-11-05 | 65 | 3 | Recover initial Steam connections across distinct CM servers |
| #465 | Jelly-RayTian | 2026-10-28 | 2026-11-05 | 54 | 2 | ntdll: decode the UTF-8 initial working directory before building its  |
| commit `62e47df` | spitefulowl | 2026-10-08 | 2026-11-05 | 48 | 2 | ntdll iOS: serve a too-large reservation in part, opt-in per game |
| commit `89ff82a` | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 41 | 1 | EA app: add the registry writer EALibrary.writeRegistry uses |
| commit `459dfa9` | ft7t8drgwf-wq (insertfloppy) | 2026-10-31 | 2026-11-05 | 39 | 1 | ntdll iOS: run a misaligned SWP inside one 16-byte granule as a hardwa |
| commit `db80643` | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 37 | 1 | EA download: read a split zip header whole; retry failed files in extr |
| commit `dd398e2` | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 30 | 1 | EA: pass the catalog locale as a Locale variable; a refused catalog no |
| commit `ada55ec` | ft7t8drgwf-wq (insertfloppy) | 2026-11-01 | 2026-11-05 | 28 | 1 | Prefix: create wine.inf's empty system folders (syswow64\mui and frien |
| #549 | 125hz | 2026-11-02 | 2026-11-05 | 28 | 2 | Dock: the held guest window goes only to the game's own 32-bit programs |
| commit `fb9158d` | 125hz | 2026-10-28 | 2026-11-05 | 20 | 3 | Host tests: follow the current sources |
| #524 | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 13 | 1 | Steam downloads: install the later depot's copy when two depots list one file |
| commit `d6857d5` | llamajose | 2026-11-01 | 2026-11-05 | 13 | 1 | Steam logon: report the Wi-Fi address, not the cellular one |
| commit `5c77821` | ft7t8drgwf-wq (insertfloppy) | 2026-11-02 | 2026-11-05 | 11 | 1 | EA download: a failed attempt takes its bytes back out of the progress |
| #551 | Connor Gow | 2026-11-03 | 2026-11-05 | 9 | 1 | GOG: install the support folder's app files |
| commit `f44a350` | ft7t8drgwf-wq (insertfloppy) | 2026-11-01 | 2026-11-05 | 7 | 1 | check-itch-store: build the 7z reader for ItchArchive.swift |
| #538 | alantangok | 2026-11-02 | 2026-11-05 | 4 | 1 | Steam: retry an unloaded or stale library after returning to the app |
| #475 | ft7t8drgwf-wq (insertfloppy) | 2026-10-28 | 2026-11-05 | 4 | 1 | ntdll iOS: drop a dead process's directory listings; Wine directory and heap fixes |
| commit `dc4a4e3` | intraducine | 2026-10-28 | 2026-11-05 | 3 | 1 | Build complete IPAs from the Terraria Wine pin |
| commit `1dbd877` | 125hz | 2026-11-02 | 2026-11-05 | 3 | 1 | Audio: the background keep-alive leaves an endpoint iOS holds stopped |

Madeira #461, #462 and #443 are pull requests in which intraducine combined other contributors' pull requests; the lines
are listed under the contributors whose commits they come from. Madeira's `docs/EPIC.md` states that the Epic game client is Akshay CM's
(Madeira #265) and the cloud save code and its interoperability fixtures are Tabîrcă Nicolae-Eduard's (Madeira #207), and that the cloud save code
follows Legendary (see the Epic row above); #443 also carries work by Ash Hopkins (#248) and Sacha Tardat.

### People

| Author | Lines | Files |
|---|---|---|
| ft7t8drgwf-wq (insertfloppy) | 6990 | 55 |
| 125hz | 2421 | 35 |
| Connor Gow | 1802 | 24 |
| Ash Hopkins | 820 | 7 |
| llamajose | 806 | 7 |
| Tabîrcă Nicolae-Eduard | 773 | 7 |
| alantangok | 471 | 13 |
| Will Faust | 418 | 6 |
| Akshay CM | 379 | 1 |
| wamei | 346 | 11 |
| meshoklv | 339 | 5 |
| spitefulowl | 265 | 5 |
| Jelly-RayTian | 257 | 6 |
| SirDank | 183 | 5 |
| Ixion | 179 | 2 |
| intraducine | 68 | 4 |
| domainus | 68 | 2 |
| NevesHenry | 56 | 5 |
| Tomba247 | 53 | 1 |
| byKAYRA | 44 | 2 |
| JMRBDev | 12 | 1 |
| Kesavamoorthi G | 11 | 1 |
| Nikita Kiselov | 8 | 1 |

### Files

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `.github/workflows/build-ipa.yml` | portions | 14 | #462 Connor Gow 14 |
| `.gitignore` | portions | 13 | #462 Connor Gow 13 |
| `app/Madeira/BCDEpic/BCDEpicCloud.swift` | portions | 76 | #443 Tabîrcă Nicolae-Eduard 76 |
| `app/Madeira/BCDEpic/BCDEpicCloudCore.swift` | portions | 16 | #443 Tabîrcă Nicolae-Eduard 16 |
| `app/Madeira/BCDEpic/EpicCloudSaves.swift` | whole file | 389 | #443 Tabîrcă Nicolae-Eduard 389 |
| `app/Madeira/BCDEpic/EpicFormats.swift` | whole file | 379 | #443 Akshay CM 379 |
| `app/Madeira/BCDGOG/GOGInstallScript.swift` | whole file | 143 | #551 ft7t8drgwf-wq 134; #551 Connor Gow 9 |
| `app/Madeira/BCDGOG/GOGSetup.swift` | whole file | 115 | #551 ft7t8drgwf-wq 115 |
| `app/Madeira/BCDGPUCards.swift` | portions | 9 | `adec653` ft7t8drgwf-wq 9 |
| `app/Madeira/BCDGPUCardsLive.swift` | portions | 9 | `adec653` ft7t8drgwf-wq 9 |
| `app/Madeira/BCDItch/BCDItchViews.swift` | portions | 6 | #499 ft7t8drgwf-wq 6 |
| `app/Madeira/BCDSteamWorkshop/SteamWorkshopFiles.swift` | whole file | 222 | #491 ft7t8drgwf-wq 222 |
| `app/Madeira/BCDSteamWorkshop/SteamWorkshopModel.swift` | whole file | 205 | #491 ft7t8drgwf-wq 205 |
| `app/Madeira/BackgroundRun.swift` | whole file | 181 | #498 ft7t8drgwf-wq 181 |
| `app/Madeira/ContentView.swift` | portions | 17 | #244 125hz 6; `abca30a` alantangok 4; `facd2cc` alantangok 4; `bbe0b12` wamei 3 |
| `app/Madeira/DockStartScreen.swift` | portions | 13 | #398 NevesHenry 10; #243 125hz 3 |
| `app/Madeira/HardwareInput.swift` | portions | 120 | `abca30a` alantangok 75; `91e1e57` alantangok 23; #413 NevesHenry 9; `9806664` alantangok 8; `facd2cc` alantangok 5 |
| `app/Madeira/JITAllocator.c` | portions | 10 | #362 SirDank 10 |
| `app/Madeira/JITAllocator.h` | portions | 6 | #362 SirDank 6 |
| `app/Madeira/Library.swift` | portions | 13 | #551 ft7t8drgwf-wq 5; `abca30a` alantangok 4; `facd2cc` alantangok 4 |
| `app/Madeira/Madeira-Bridging-Header.h` | portions | 11 | #498 ft7t8drgwf-wq 11 |
| `app/Madeira/MemoryHostProtocol.h` | whole file | 41 | `a15332c` Will Faust 41 |
| `app/Madeira/MemoryHostTest.h` | whole file | 6 | `a15332c` Will Faust 6 |
| `app/Madeira/MemoryHostTest.m` | whole file | 228 | `a15332c` Will Faust 228 |
| `app/Madeira/MemoryPool.m` | whole file | 64 | `a15332c` Will Faust 64 |
| `app/Madeira/PrefixExtractor.c` | portions | 11 | `39c7465` Kesavamoorthi G 7; `9344146` Kesavamoorthi G 4 |
| `app/Madeira/ProMotionIntent.swift` | portions | 42 | #246 125hz 42 |
| `app/Madeira/SteamCloud.swift` | portions | 96 | #404 wamei 96 |
| `app/Madeira/SteamDownloadBackground.swift` | portions | 3 | #498 ft7t8drgwf-wq 3 |
| `app/Madeira/SteamGames.swift` | portions | 9 | #381 wamei 9 |
| `app/Madeira/SteamInstall.swift` | portions | 10 | #381 wamei 10 |
| `app/Madeira/SteamOwnedLibrary.swift` | portions | 119 | #490 ft7t8drgwf-wq 37; #381 wamei 31; #237 125hz 20; #491 ft7t8drgwf-wq 13; #382 wamei 8; #538 alantangok 4; #404 wamei 3; #493 ft7t8drgwf-wq 3 |
| `app/Madeira/SteamRuntime.swift` | portions | 27 | #389 meshoklv 27 |
| `app/Madeira/StikJITHelper.swift` | portions | 41 | `bbe0b12` wamei 37; `20c5218` wamei 4 |
| `app/Madeira/SwiftEA/EAAPI.swift` | whole file | 429 | #523 ft7t8drgwf-wq 399; `dd398e2` ft7t8drgwf-wq 30 |
| `app/Madeira/SwiftEA/EADownloader.swift` | whole file | 321 | #523 ft7t8drgwf-wq 273; `db80643` ft7t8drgwf-wq 37; `5c77821` ft7t8drgwf-wq 11 |
| `app/Madeira/SwiftEA/EAFormats.swift` | whole file | 728 | #523 ft7t8drgwf-wq 728 |
| `app/Madeira/SwiftEA/EALibrary.swift` | whole file | 277 | #523 ft7t8drgwf-wq 243; #443 Tabîrcă Nicolae-Eduard 23; #499 ft7t8drgwf-wq 11 |
| `app/Madeira/SwiftEA/EALicense.swift` | whole file | 66 | #523 ft7t8drgwf-wq 66 |
| `app/Madeira/SwiftEA/EARegistry.swift` | portions | 41 | `89ff82a` ft7t8drgwf-wq 41 |
| `app/Madeira/SwiftEA/EAService.swift` | whole file | 240 | #523 ft7t8drgwf-wq 240 |
| `app/Madeira/SwiftItch/ItchAPI.swift` | whole file | 267 | #499 ft7t8drgwf-wq 267 |
| `app/Madeira/SwiftItch/ItchArchive.swift` | whole file | 289 | #499 ft7t8drgwf-wq 175; #520 ft7t8drgwf-wq 114 |
| `app/Madeira/SwiftItch/ItchInstall.swift` | whole file | 163 | #499 ft7t8drgwf-wq 163 |
| `app/Madeira/SwiftItch/ItchLibrary.swift` | whole file | 297 | #499 ft7t8drgwf-wq 264; #443 Tabîrcă Nicolae-Eduard 24; #520 ft7t8drgwf-wq 9 |
| `app/Madeira/SwiftItch/itch_7z.c` | whole file | 815 | #520 ft7t8drgwf-wq 815 |
| `app/Madeira/SwiftItch/itch_7z.h` | whole file | 37 | #520 ft7t8drgwf-wq 37 |
| `app/Madeira/SwiftSteam/Content/DepotDownloader.swift` | portions | 231 | #505 llamajose 114; #493 ft7t8drgwf-wq 96; #524 ft7t8drgwf-wq 13; #491 ft7t8drgwf-wq 5; #490 ft7t8drgwf-wq 3 |
| `app/Madeira/SwiftSteam/Content/SteamLANPeer.swift` | whole file | 533 | #505 llamajose 533 |
| `app/Madeira/SwiftSteam/Core/CMServerList.swift` | portions | 3 | #477 intraducine 3 |
| `app/Madeira/SwiftSteam/Core/SteamCMSession.swift` | portions | 4 | #493 ft7t8drgwf-wq 4 |
| `app/Madeira/SwiftSteam/Core/SteamConnection.swift` | portions | 40 | #477 intraducine 40 |
| `app/Madeira/SwiftSteam/Core/SteamError.swift` | portions | 28 | #477 intraducine 22; #493 ft7t8drgwf-wq 3; #490 ft7t8drgwf-wq 3 |
| `app/Madeira/SwiftSteam/Core/SteamProtocol.swift` | portions | 19 | #505 llamajose 10; #491 ft7t8drgwf-wq 5; #490 ft7t8drgwf-wq 4 |
| `app/Madeira/SwiftSteam/Core/SteamSession.swift` | portions | 23 | #505 llamajose 20; #491 ft7t8drgwf-wq 3 |
| `app/Madeira/SwiftSteam/Install/AppManifestWriter.swift` | portions | 7 | #490 ft7t8drgwf-wq 4; #381 wamei 3 |
| `app/Madeira/SwiftSteam/Library/SteamAppInfo.swift` | portions | 194 | #490 ft7t8drgwf-wq 99; #381 wamei 90; #382 wamei 5 |
| `app/Madeira/SwiftSteam/Library/SteamLibraryFetcher.swift` | portions | 100 | #490 ft7t8drgwf-wq 100 |
| `app/Madeira/SwiftSteam/Proto/SteamProtoMessages.swift` | portions | 13 | `d6857d5` llamajose 13 |
| `app/Madeira/WineProcessBridge.h` | portions | 14 | `ba617bb` SirDank 7; `4e568c1` SirDank 4; #360 SirDank 3 |
| `app/Madeira/WineProcessBridge.m` | portions | 170 | `4e568c1` SirDank 64; `ba617bb` SirDank 40; `ada55ec` ft7t8drgwf-wq 28; #363 SirDank 24; #360 SirDank 11; #349 125hz 3 |
| `app/Madeira/Winios/BackgroundCPUCap.c` | whole file | 119 | #498 ft7t8drgwf-wq 119 |
| `app/Madeira/Winios/MetalBackgroundGate.m` | whole file | 70 | #498 ft7t8drgwf-wq 70 |
| `app/Madeira/Winios/Winios.h` | portions | 7 | `abca30a` alantangok 4; #243 125hz 3 |
| `app/Madeira/Winios/Winios.m` | portions | 29 | `abca30a` alantangok 14; `9806664` alantangok 12; #243 125hz 3 |
| `app/Madeira/Winios/WiniosGL.m` | whole file | 319 | #462 Connor Gow 303; #462 125hz 16 |
| `app/Madeira/compat/love/README.md` | whole file | 6 | #462 Connor Gow 6 |
| `app/Madeira/gl/README.md` | whole file | 4 | #462 Connor Gow 4 |
| `app/MadeiraMemoryHost/MemoryHost.m` | whole file | 66 | `a15332c` Will Faust 66 |
| `build/luajit-x64/build.sh` | whole file | 33 | #462 Connor Gow 33 |
| `build/madeira-d3d12/build-pe.sh` | portions | 7 | #462 spitefulowl 4; `dc4a4e3` intraducine 3 |
| `build/madeira_redact.h` | whole file | 184 | #461 Ash Hopkins 184 |
| `build/mesa-ios/build.sh` | whole file | 42 | #462 Connor Gow 35; #462 125hz 7 |
| `build/mesa-ios/ios-arm64.cross` | whole file | 21 | #462 Connor Gow 21 |
| `build/mesa-ios/patches/0001-zink-vulkan-library-from-env.patch` | whole file | 15 | #462 Connor Gow 15 |
| `build/mesa-ios/patches/0002-osmesa-link-zink.patch` | whole file | 9 | #462 Connor Gow 9 |
| `build/mesa-ios/patches/0003-sw-helper-declare-zink.patch` | whole file | 8 | #462 Connor Gow 8 |
| `build/mesa-ios/patches/0004-zink-moltenvk-io-and-no-pipeline.patch` | whole file | 123 | #462 Connor Gow 123 |
| `build/mesa-ios/patches/0005-zink-moltenvk-no-dynamic-triangle-fans.patch` | whole file | 15 | #462 Connor Gow 15 |
| `build/mesa-ios/patches/0006-zink-moltenvk-prefix-variable-names.patch` | whole file | 57 | #462 Connor Gow 57 |
| `build/mesa-ios/patches/0007-zink-moltenvk-depth-clamp-and-no-overflow-query.patch` | whole file | 27 | #462 Connor Gow 27 |
| `build/mesa-ios/patches/0008-zink-moltenvk-zero-unwritten-fs-inputs.patch` | whole file | 157 | #462 125hz 157 |
| `build/mesa-ios/patches/README.md` | whole file | 7 | #462 125hz 7 |
| `build/moltenvk-ios/build.sh` | portions | 16 | #462 Connor Gow 16 |
| `build/ntdll-unix/audio_null_ios.c` | portions | 67 | #498 ft7t8drgwf-wq 51; #349 125hz 13; `1dbd877` 125hz 3 |
| `build/ntdll-unix/build.sh` | portions | 15 | #399 Jelly-RayTian 7; #401 meshoklv 4; #462 Connor Gow 4 |
| `build/ntdll-unix/env_ios.c` | portions | 29 | `4e568c1` SirDank 14; #465 Jelly-RayTian 8; #471 Jelly-RayTian 7 |
| `build/ntdll-unix/process_ios.c` | portions | 22 | #240 125hz 16; #461 Ash Hopkins 3; #549 125hz 3 |
| `build/ntdll-unix/server_ios.c` | portions | 4 | #475 ft7t8drgwf-wq 4 |
| `build/ntdll-unix/signal_arm64_ios.c` | portions | 265 | #517 125hz 93; #526 ft7t8drgwf-wq 66; #461 Ash Hopkins 45; `459dfa9` ft7t8drgwf-wq 39; #469 ft7t8drgwf-wq 14; `0db1b82` Nikita Kiselov 8 |
| `build/ntdll-unix/thread_ios.c` | portions | 138 | #401 meshoklv 138 |
| `build/ntdll-unix/virtual_ios.c` | portions | 1214 | #559 125hz 486; #227 Connor Gow 239; #385 meshoklv 158; #240 125hz 65; `74e05ae` spitefulowl 63; `4eea7e5` Tomba247 53; `62e47df` spitefulowl 44; #516 125hz 33; #549 125hz 25; #517 125hz 22; `a15332c` Will Faust 13; #462 Connor Gow 13 |
| `build/tools/gen-config-catalog.py` | portions | 21 | #498 ft7t8drgwf-wq 17; `9806664` alantangok 4 |
| `build/win32u-unix/driver_ios.c` | portions | 24 | `abca30a` alantangok 24 |
| `build/win32u-unix/opengl_ios.c` | whole file | 833 | #462 Connor Gow 553; #462 125hz 280 |
| `build/wineserver/fd_ios.c` | portions | 7 | #461 Ixion 7 |
| `build/wineserver/luajit_compat.c` | whole file | 76 | #462 Connor Gow 76 |
| `build/wineserver/mach_ios.c` | portions | 4 | #469 ft7t8drgwf-wq 4 |
| `build/wineserver/mapping_ios.c` | portions | 13 | #462 Connor Gow 13 |
| `build/wineserver/queue_ios.c` | portions | 3 | #510 alantangok 3 |
| `docs/BUILDING.md` | portions | 6 | #399 Jelly-RayTian 6 |
| `docs/EA.md` | portions | 4 | #523 ft7t8drgwf-wq 4 |
| `docs/ITCH.md` | portions | 5 | #499 ft7t8drgwf-wq 5 |
| `docs/KEYBOARD_MOUSE.md` | portions | 28 | `abca30a` alantangok 16; `9806664` alantangok 9; `91e1e57` alantangok 3 |
| `docs/MADEIRA_DOCK.md` | portions | 6 | #398 NevesHenry 6 |
| `docs/STEAM_CLOUD.md` | portions | 22 | #404 wamei 14; #237 125hz 8 |
| `docs/STEAM_LIBRARY.md` | portions | 9 | #382 wamei 5; #223 domainus 4 |
| `madeira-d3d12/src/pe/mad_bcn.cpp` | whole file | 9 | #462 spitefulowl 9 |
| `madeira-d3d12/src/pe/madeira_d3d12.c` | portions | 233 | #462 spitefulowl 141; #516 125hz 92 |
| `tests/host/check-background-run.py` | portions | 188 | #498 ft7t8drgwf-wq 188 |
| `tests/host/check-clipboard-paste.py` | whole file | 134 | `abca30a` alantangok 70; `9806664` alantangok 51; `91e1e57` alantangok 10; `facd2cc` alantangok 3 |
| `tests/host/check-dircache.py` | whole file | 172 | #461 Ixion 172 |
| `tests/host/check-dock-components.py` | portions | 12 | #389 meshoklv 12 |
| `tests/host/check-dock-start-screen.py` | portions | 34 | #243 125hz 21; #398 NevesHenry 13 |
| `tests/host/check-ea-formats.py` | whole file | 194 | #523 ft7t8drgwf-wq 194 |
| `tests/host/check-ea.py` | portions | 16 | #523 ft7t8drgwf-wq 16 |
| `tests/host/check-epic-cloud-legendary.py` | whole file | 150 | #443 Tabîrcă Nicolae-Eduard 150 |
| `tests/host/check-first-process-exiting.py` | whole file | 94 | #461 Ash Hopkins 94 |
| `tests/host/check-fixed-base-guard.py` | portions | 3 | `8deb759` 125hz 3 |
| `tests/host/check-guest-ctx.py` | whole file | 82 | #526 ft7t8drgwf-wq 82 |
| `tests/host/check-hardware-input.py` | portions | 18 | #413 NevesHenry 18 |
| `tests/host/check-helper-memory.py` | portions | 5 | `8deb759` 125hz 5 |
| `tests/host/check-inet-poll-cache.py` | whole file | 116 | #461 Ash Hopkins 116 |
| `tests/host/check-initial-cwd-utf8.py` | whole file | 46 | #465 Jelly-RayTian 46 |
| `tests/host/check-input-queue-wake.py` | whole file | 67 | #510 alantangok 67 |
| `tests/host/check-itch-7z.py` | whole file | 121 | #520 ft7t8drgwf-wq 121 |
| `tests/host/check-itch.py` | whole file | 210 | #499 ft7t8drgwf-wq 203; `f44a350` ft7t8drgwf-wq 7 |
| `tests/host/check-jit-alias-atomics.py` | whole file | 227 | #461 Ash Hopkins 227 |
| `tests/host/check-log-redaction.py` | whole file | 151 | #461 Ash Hopkins 151 |
| `tests/host/check-oversize-reserve.py` | portions | 4 | `62e47df` spitefulowl 4 |
| `tests/host/check-place-proof.py` | portions | 7 | `8deb759` 125hz 7 |
| `tests/host/check-rusage-sdk.py` | whole file | 83 | #399 Jelly-RayTian 83 |
| `tests/host/check-sc-helper-binary.py` | whole file | 265 | #559 125hz 265 |
| `tests/host/check-sc-ipc-rebind.py` | portions | 13 | #559 125hz 13 |
| `tests/host/check-sc-ipc-split.py` | portions | 257 | #559 125hz 247; `fb9158d` 125hz 10 |
| `tests/host/check-sc-render-handler.py` | portions | 95 | #559 125hz 95 |
| `tests/host/check-sc-render-loop.py` | portions | 253 | #559 125hz 247; `fb9158d` 125hz 6 |
| `tests/host/check-sc-runstate.py` | portions | 7 | #559 125hz 7 |
| `tests/host/check-sc-ui-close.py` | portions | 11 | #559 125hz 11 |
| `tests/host/check-session-object-alignment.py` | whole file | 50 | #377 alantangok 50 |
| `tests/host/check-startup-cwd-layout.py` | whole file | 100 | #471 Jelly-RayTian 100 |
| `tests/host/check-steam-cloud.py` | portions | 16 | #237 125hz 16 |
| `tests/host/check-steam-lan-peer.py` | whole file | 113 | #505 llamajose 113 |
| `tests/host/check-steam-library.py` | portions | 126 | #490 ft7t8drgwf-wq 98; #381 wamei 28 |
| `tests/host/check-steam-session-jobs.py` | whole file | 67 | #223 domainus 64; #505 llamajose 3 |
| `tests/host/check-swap-cap.py` | portions | 5 | `8deb759` 125hz 5 |
| `tests/host/check-swap-coverage.py` | portions | 6 | `8deb759` 125hz 6 |
| `tests/host/check-vcruntime.py` | whole file | 41 | #251 byKAYRA 22; `89cc3e0` byKAYRA 16; `a6acc4e` byKAYRA 3 |
| `tests/host/ea-formats/HostDigest.swift` | whole file | 59 | #523 ft7t8drgwf-wq 59 |
| `tests/host/ea-formats/main.swift` | whole file | 188 | #523 ft7t8drgwf-wq 188 |
| `tests/host/epic-cloud/main.swift` | whole file | 95 | #443 Tabîrcă Nicolae-Eduard 95 |
| `tests/host/gog-script/main.swift` | whole file | 71 | #551 ft7t8drgwf-wq 71 |
| `tests/host/itch-7z/make-fixtures.sh` | whole file | 19 | #520 ft7t8drgwf-wq 19 |
| `tests/host/sc-ipc-split.S` | portions | 18 | #559 125hz 18 |
| `tests/host/sc-render-handler.S` | whole file | 19 | #559 125hz 19 |
| `tests/host/sc-render-loop.S` | portions | 14 | #559 125hz 10; `fb9158d` 125hz 4 |
| `tests/host/steam_workshop_files_checks.swift` | whole file | 105 | #491 ft7t8drgwf-wq 105 |
| `tests/x64/glsampler-x64.c` | whole file | 123 | #462 Connor Gow 123 |
| `tests/x64/thinreserve-x64.c` | whole file | 73 | #227 Connor Gow 73 |
| `tools/fetch-vcruntime.md` | portions | 3 | `89cc3e0` byKAYRA 3 |
| `tools/fetch-vcruntime.sh` | portions | 12 | #462 JMRBDev 12 |

Files taken whole that are left without a header comment: the twelve 7-Zip test archives `tests/host/itch-7z/*.7z` (binary; ft7t8drgwf-wq, Madeira
#520, taken 2026-11-05) and the eight Mesa patches `build/mesa-ios/patches/0001-*.patch` to `0008-*.patch` (Connor Gow and 125hz, Madeira #462;
the table counts their lines), which the build applies with `patch`.

Taken after this count: Madeira's commit `755a893` (ABTOPEMOHT, Madeira #566 and #574; taken 2026-11-06): the Mesa patch `build/mesa-ios/patches/0009-osmesa-allow-compressed-fallback.patch` and the check program `tests/x64/gls3tc-x64.c`, both credited in their headers and in `build/mesa-ios/patches/README.md`.

### Game lists

These entries of BCD's recommended settings follow tested lists of Madeira, and the notes of each entry say so: Fallout: New Vegas and
Fallout 3 (the older NVIDIA card, ported 2026-10-28 from the list of ft7t8drgwf-wq, Madeira `adec653`, with DXMT `f25e556`); Call of Duty:
Black Ops (the card and the resolution, from Madeira #519 by ft7t8drgwf-wq), The Escapists 2 (from Madeira #548 by ft7t8drgwf-wq),
Borderlands 2 (from Madeira #531 by ft7t8drgwf-wq) and Dark Souls: Remastered (from Madeira #461, combined by intraducine), all ported on
2026-11-05.

### The submodule forks

BCD's forks of Wine, FEX-Emu and DXMT hold commits that Madeira's forks hold too, with their authors kept in the commit history:
the file `LICENSE-MADEIRA.md` of each fork lists them, with the Madeira commit each comes from (`wine/LICENSE-MADEIRA.md`,
`FEX/LICENSE-MADEIRA.md`, `dxmt/LICENSE-MADEIRA.md`). BCD's `madeira-dock` is at a commit of Madeira's own and holds nothing of BCD's.

### Other people's work in BCD

Pull requests sent to BCD (`thebcd69/bcd-games`: #4 by mckenna654, #5 and #16 by le0-VV, #11 by vancez, #12 by Pardoxum) and work from rtmtree's
Persona 3 Reload fork of BCD (helper memory, root certificate enumeration, the bytes at the instruction pointer in the guest log) are credited
in the files that hold them and in `README-BCD.md`.

## Microsoft Visual C++ runtime redistributables — NOT DISTRIBUTED

Games built with MSVC require Microsoft's Visual C++ runtime DLLs. Those are
Microsoft-authored binaries, redistributable only under the Visual Studio
redistributable terms and only in unmodified form. They are **not** covered by
this project's license and are **no longer tracked in this repository**;
`app/Madeira/x86_64-vcruntime/*.dll` is gitignored and must be supplied locally.
See `tools/fetch-vcruntime.md`.

### Earlier malformed copies were purged from history

Twelve such DLLs were previously committed here, and they had been modified:
each one's Authenticode signature had been truncated away, removing exactly the
advertised certificate payload and leaving a PE header still claiming a
signature the file no longer contained.

Those blobs have been removed from this repository's history entirely. Anyone
holding a clone or fork taken before the rewrite may still have them, and should
not redistribute those copies.

## Corresponding source for the statically linked libraries

`libgmp.a`, `libnettle.a`, `libhogweed.a` and `libgnutls.a` are tracked as
compiled binaries, so the exact sources they were built from are tracked too:

- `build/gnutls-ios/src/gmp-6.3.0.tar.xz`
- `build/gnutls-ios/src/nettle-3.10.1.tar.gz`
- `build/gnutls-ios/src/gnutls-3.8.9.tar.xz`
- `build/gnutls-ios/src/SHA256SUMS` -- checksums for the above
- `build/gnutls-ios/build.sh` -- the exact build machinery and configure flags

All three are unmodified upstream releases; no patches are applied. A reference
to an upstream project would not have been enough on its own, which is why the
tarballs themselves are here.

FFmpeg's archives are git-ignored outputs of its build script (like
`libntdll_unix.a`), but the app links them, so a built package contains them
and the exact source they are built from is tracked here in the same way:

- `build/ffmpeg/src/ffmpeg-7.1.1.tar.xz` -- the unmodified upstream release
  from `ffmpeg.org`
- `build/ffmpeg/src/SHA256SUMS` -- its checksum
  (`733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1`), which
  every run of the build script verifies before extracting
- `build/ffmpeg/build.sh` -- the exact build machinery and configure flags. No
  patches are applied, and the script needs no network.

Wine Mono is not linked into anything: `build/wine-mono/bundle.sh` copies the
prebuilt runtime into the bundle as files. Its source release is 372 MB, too
large for the repository, so it is pinned rather than tracked:

- `build/wine-mono/pin.sh` -- the version, and the SHA-256 of both the x86
  tarball that is bundled and `wine-mono-11.0.0-src.tar.xz`, its source
  (`c504cb0b91ce09b72869e063844ce114564d4ac340479a82aa6789f51510e520`)
- `build/wine-mono/fetch.sh --source` -- downloads both from
  `https://dl.winehq.org/wine/wine-mono/11.0.0/` and verifies them

Release builds do not ship Wine Mono (decided 2026-10-06): the packaging step
removes `wine-mono/` from the app, and Madeira fetches it from WineHQ on first
use instead (`app/Madeira/WineMono.swift`), so the copy on a device comes from
WineHQ, not from Madeira. A release that did ship it would have to attach that source
tarball next to the package and add the licence texts COPYING lists to
`LICENSES/`.

## Relinking and static linking

The Wine-derived unix libraries, the crypto stack and FFmpeg are linked **statically**
into the app binary.

LGPL section 6 (v2.1) / section 4 (v3) contemplates a recipient being able to
relink the application against a modified version of the library. Providing the
library's source alone does not satisfy this; the relevant clauses also
contemplate supplying the application in a form — object code or source — that
permits relinking. This repository does not currently ship such a package.

Note the distinction between publishing source and distributing binaries.
Publishing this repository alone distributes no combined binary, so the
question does not arise. **Handing someone a built `.ipa` does distribute one.**

For any `.ipa` given to a tester, supply the corresponding source at that exact
commit together with the build and signing instructions needed to reproduce it.
That is a practice to follow, not something the repository can satisfy on its
own.

If relinking needs to be supported properly, shipping the Wine-derived parts as
a dynamic framework is one possible approach, and easier to plan for than to
retrofit -- though on iOS it is not a complete answer by itself, since code
signing may still prevent a recipient from substituting a modified framework
into a signed app.

## Present in the working tree but not distributed

`LiveContainer/` and `research/LiveContainer/` are AGPL-3.0 reference copies
used for local research. Both are gitignored, untracked, and no part of them is
linked into or shipped with the app. They form no part of the combined work.
