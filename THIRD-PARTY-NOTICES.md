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
| **FFmpeg** 7.1.5 — libavformat, libavcodec, libavutil, libswresample | **LGPL-2.1-or-later** | Static (`libavformat.a`, `libavcodec.a`, `libavutil.a`, `libswresample.a`). Configured `--disable-gpl --disable-nonfree --disable-version3 --disable-everything`, then only: the WMA-family decoders (`wmav1,wmav2,wmapro,wmalossless,xma1,xma2`), the `mp1,mp2,mp3` and `pcm_u8,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le` decoders, the Windows Media Video decoders (`wmv1,wmv2,wmv3,vc1`; they select FFmpeg's h263 decoder code, and its `h263` parser comes with it), the `mp3`, `wav`, `mov` (MP4/MOV/M4A) and `asf` demuxers and the `mpegaudio` parser. No H.264, HEVC or AAC decoder or parser (those streams are decoded by Apple's VideoToolbox/AudioToolbox), no protocols, no muxers, no zlib. No GPL, version3 or non-free component is enabled, so the result is LGPL-2.1-or-later. Used only by `build/ntdll-unix/winegstreamer_unixlib_ios.c`, `build/ntdll-unix/winegstreamer_wmv_ios.c` and `build/ntdll-unix/wg_parser_av_ios.c`, winegstreamer's unix side on this port (the Windows WMA and WMV decoder MFT/DMOs, and the wg_parser behind quartz's MP3/WAV splitters and Media Foundation's MP4 source); upstream Wine implements all of it with GStreamer, which does not exist on iOS; and by `build/ntdll-unix/winedmo_unixlib_ios.c`, winedmo's unix side (the ASF demuxer behind Wine's ASF media source, for the games that opt in), which is taken from Wine's `dlls/winedmo` (LGPL-2.1-or-later, Copyright 2024 Rémi Bernon for CodeWeavers) and carries a "Modified by BCD" notice. |
| **TimGM6mb SoundFont** (the General MIDI sound bank for games' MIDI music) | **GPL-2.0** (as Debian's copyright file for `timgm6mb-soundfont` records it; Copyright 2004 Tim Brechbill, 2010 David Bolton, who tidied it for MuseScore). The author told the MuseScore project that some of the samples are his own and that the rest are public domain or under the GPL | Unmodified data file (5,969,788 bytes), downloaded at build time by `tools/fetch-soundfont.sh` into `app/Madeira/soundfont/` (SHA-256 `c5378b62028c920cb11e4803327983fee2f2cdff5dc89c708e39da417e51c854`, checked; two sources give the same file: Debian's archive and the MuseScore repository) and bundled; it is **not** committed to this repository | Played by Apple's AUMIDISynth when a game opens a MIDI output device (`build/ntdll-unix/bcd_midi_synth_ios.m`). It is a data file read by Apple's synthesizer, not a program linked with BCD, so it stays under its own licence beside BCD's as part of an aggregate (GPL-2.0 and GPL-3.0 section 5). A SoundFont is its own source: the file is the form it is edited in. Licence text: `LICENSES/GPL-2.0.txt` (in the app: `legal/LICENSES/GPL-2.0.txt`); the notice that ships beside it: `app/Madeira/soundfont/NOTICE-TimGM6mb.txt`. A bank of the player's own (Files > BCD > SoundFonts) is the player's, never part of BCD. |
| **Noto Sans JP** (Noto Sans CJK, the Japanese SubsetOTF file, Regular, version 2.004; the Japanese locale's font) | **SIL Open Font License 1.1** (Copyright 2014-2021 Adobe, the file's own copyright record; "Noto is a trademark of Google Inc.", its trademark record) | Unmodified font file (4,533,028 bytes), downloaded at build time by `tools/fetch-japanese-font.sh` into `app/Madeira/fonts-ja/` (SHA-256 `dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073`, checked; two sources give the same file: [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) commit `f8d157532fbfaeda587e826d4cd5b21a49186f7c` and Madeira commit `025c0ca2` of pull request #567, by nekodesusan-prog, which bundles it) and bundled; it is **not** committed to this repository | Linked into a game's `C:\windows\Fonts` only for a session with the Japanese locale (`env.MADEIRA_JAPANESE = 1`, `app/Madeira/BCDJapanese.c`), with the Windows Japanese font names mapped onto it; the next session without it takes both away. A data file read by Wine's font code, not a program linked with BCD, so it stays under its own licence beside BCD's. Licence text: `app/Madeira/fonts-ja/LICENSE-NotoSansJP-OFL-1.1.txt` and `LICENSES/OFL-1.1-NotoSansJP.txt` (in the app: `fonts-ja/` and `legal/LICENSES/OFL-1.1-NotoSansJP.txt`); the notice beside it: `app/Madeira/fonts-ja/NOTICE-NotoSansJP.txt`. The Japanese locale also uses Wine's code page tables `c_932/936/949/950.nls`, which BCD has shipped unmodified in `app/Madeira/nls/extra` since build 591. |
| **{fmt}** | MIT | Static (`libfmt.a`), via FEX. |
| **xxHash** | BSD-2-Clause | Static (`libxxhash.a`). |
| **Cephes** | permissive (Moshier) | Static (`libcephes_128bit.a`), via FEX. |
| **Berkeley SoftFloat 3e** | BSD-3-Clause | Static (`libsoftfloat_3e.a`), via FEX. |
| **Steam sign-in, owned library and downloads (`app/Madeira/SwiftSteam/`)** | Derived from Jfishin's Madeira Steam client, used with the author's permission (see below); Jfishin confirmed that he wrote the depot downloader himself | GPL-3.0-or-later + Converter Exception, Copyright 2026 Jfishin, 125hz | Sign-in to Steam's public `IAuthenticationService` over HTTPS and a Keychain token store; a Steam connection (WebSocket over TLS) that lists the account's owned games; downloads from Steam's content servers into Madeira Dock's Steam library folder. File-by-file provenance and the licence audit of the downloader: `docs/STEAM_SIGNIN.md`, `docs/STEAM_LIBRARY.md`. No Valve or third-party Steam code or binaries, and no Steam Cloud, launch-emulator or DRM code. |
| **Epic Cloud saves (`app/Madeira/BCDEpic/EpicFormats.swift`, `EpicCloudSaves.swift`)** | Upstream Madeira's files: GPL-3.0-or-later + Converter Exception; `EpicFormats.swift` Copyright 2026 Akshay CM. `EpicCloudSaves.swift` is a port of parts of **Legendary** (`https://github.com/derrod/legendary`, GPL-3.0-or-later, derrod and contributors): `api/egs.py`, `core.py`, `utils/savegame_helper.py`, `models/chunk.py`, `models/manifest.py`, `utils/rolling_hash.py`. Madeira's `docs/EPIC.md` credits the game client to Akshay CM (Madeira #265) and the cloud save code and its fixtures to Tabîrcă Nicolae-Eduard (Madeira #207); intraducine combined them in Madeira #443 (see "Code from Madeira after BCD's fork") | Kept with their own headers; `EpicCloudSaves.swift` carries BCD's changes marked in its header (the URLSession transport is not carried; the date formatters are integer arithmetic). BCD's `BCDEpicCloudCore.swift` is BCD's own and `BCDEpicCloud.swift` is adapted from upstream Madeira's `EpicCloud.swift` (its header says so) | Compares a game's saves with Epic's cloud save snapshots, only for a game the player turned it on for, and writes a new snapshot only on the player's choice. The rest of BCD's Epic engine (`BCDEpic/`, `BCDContent/`) is BCD's own code, written from protocol facts. |
| **GOG install script and redistributables (`app/Madeira/BCDGOG/GOGInstallScript.swift`, `GOGSetup.swift`)** | Upstream Madeira's files: GPL-3.0-or-later + Converter Exception, Copyright 2026 Madeira contributors. They follow **Playport's** `GOGInstallScript` and `GOGDependencies` (`https://github.com/playportdev/playport`, commit `412de5f`, GPL-3.0-or-later; carried by upstream Madeira from willfaust/Madeira #479) | Kept with their own headers; `GOGSetup.swift` carries BCD's changes marked in its header (BCD's own helpers instead of upstream's app types, the result folder, BCD's wording, an argument filter). Notice: `app/Madeira/legal/PLAYPORT.txt` | Reads a GOG build's `goggame-<id>.script` (its registry values are written into the prefix before a start) and GOG's dependency repository (which redistributables a build needs and which installer runs). The rest of BCD's GOG (`BCDGOG/`, `BCDContent/`) is BCD's own code, written from GOG's public replies and community documentation; the formats follow Heroic's `gogdl` (GPL-3.0); of gogdl's code only the language table of GOG's generation-1 downloads is in BCD (`app/Madeira/BCDGOG/BCDGOGContentV1.swift`, see "Code from heroic-gogdl"). |
| **GOG Galaxy service (`app/Madeira/BCDGOG/Galaxy/GalaxyWire.swift`, `GalaxyService.swift`, `GalaxyListener.swift`)** | The **Playport** authors' files (`https://github.com/playportdev/playport`, commit `412de5f`): GPL-3.0-or-later with the Madeira Converter Exception, version 1, as the Playport authors adopted it for their own code on 2026-09-24 (an additional permission under GPL-3.0 section 7 with the same text as `LICENSE-EXCEPTION.md`); carried by upstream Madeira on its branch `playport/gog` (commit `9d6dad8`, intraducine; not merged into Madeira's main when BCD took it on 2026-11-14) | `GalaxyWire.swift` as it came; the other two with BCD's changes listed in their headers (Playport's ContentKit types replaced by three small ones in `BCDGOGGalaxyCore.swift`, BCD's own GOG sign-in as the token source). The game's Galaxy client type and the token grant in `BCDGOGGalaxyCore.swift` follow Playport's `GOGContent.swift` and `GOGSession.swift`; Playport's Galaxy tests are `tests/host/gog-galaxy/GalaxyTests.swift`. Notice: `app/Madeira/legal/PLAYPORT.txt`. BCD's section 7 terms do not apply to these files | Answers a GOG game's Galaxy SDK on 127.0.0.1:9977 while the game runs (the game's sign-in, achievements, statistics, leaderboards, play time), only for a game whose switch is on (`env.BCD_GOG_GALAXY = 1`, off by default). Playport's install engine (ContentKit, GOGInstaller) and its start helper were not taken: BCD's GOG installs, updates and repairs are its own code. |
| **Zstandard educational decoder** | BSD-3-Clause or GPL-2.0 | BSD-3-Clause selected | `app/Madeira/SwiftSteam/zstd_edu.c/.h` (Meta Platforms). Decodes Steam's zstd content chunks. Madeira's error-recovery wrapper (Jfishin's, kept) is serialized across threads. Licence text in `LICENSES/ZSTD-BSD.txt`. |
| **liblzma** | Public domain / 0BSD | System library | Linked dynamically from the iOS SDK (`liblzma.tbd`) for Steam's LZMA content chunks; nothing is bundled. |
| **Madeira Dock (`madeira-dock`, built into `arm64ec-windows/dockhost.exe`)** | original work, Copyright 2026 125hz | GPL-3.0-or-later + Converter Exception | Headless host for Valve's Steam client, a separate Windows program started in the Wine session. Built from the submodule by `build/madeira-dock/build.sh`; not committed as a binary. Its statically linked LLVM/MinGW-w64 runtime notices ship in `dock-notices.txt`. Valve's client files are downloaded by the user from Valve and are not covered by Madeira's licence. See `docs/MADEIRA_DOCK.md`. |
| **StikJIT** 1.9.0 | MPL-2.0 | Bundled XCFramework in the iOS 26 JIT helper | Enables JIT for Madeira through the device's RSD tunnel. Exact corresponding source: [`StikDebug/StikJIT` tag `1.9.0`](https://github.com/StikDebug/StikJIT/tree/1.9.0); license text: `LICENSES/MPL-2.0.txt`. The framework contains **idevice**, Copyright 2026 Jackson Coxson, under MIT; source: [`jkcoxson/idevice`](https://github.com/jkcoxson/idevice), license text: `LICENSES/MIT-idevice.txt`. |
| **On-device pairing** (`libmadeira_rppairing.a`) | **idevice** 0.1.68, Copyright 2026 Jackson Coxson, MIT; its Rust dependencies under MIT, Apache-2.0, BSD-3-Clause or ISC | Madeira's wrapper (`build/rppairing-ios`) **GPL-3.0-or-later**; dependencies unmodified, static in the app | iOS 27 on-device remote pairing for Built-in StikJIT (`app/Madeira/JITPairing.swift`, docs/JIT.md). Built from crates.io at the versions pinned in `build/rppairing-ios/Cargo.lock`. Every crate in the binary, with its licence files, is listed in `app/Madeira/legal/LICENSES-rppairing-crates.txt` (bundled as `legal/LICENSES-rppairing-crates.txt`), regenerated by `build/rppairing-ios/build.sh`. idevice's licence text: `LICENSES/MIT-idevice.txt`. |
| **libxml2** 2.12.10 and **libxslt** 1.1.45 | MIT (Daniel Veillard and contributors) | Unmodified, statically linked into Wine's `msxml3.dll` (`arm64ec-windows`) | Wine's bundled copies (`wine/libs/xml2`, `wine/libs/xslt`), built with the module by `build/wine-pe/build-modules.sh`. Licence texts in `LICENSES/MIT-libxml2.txt` and `LICENSES/MIT-libxslt.txt`. |
| **NVAPI SDK headers** | MIT (NVIDIA Corporation & affiliates) | Unmodified headers, compiled into DXMT's `nvapi64.dll` (`arm64ec-windows`) | `dxmt/external/nvapi`. DXMT's own NVAPI implementation answers only with Madeira's opt-in "Report an NVIDIA GPU" switch; no NVIDIA binary is shipped. Licence text in `LICENSES/MIT-NVAPI.txt`. |
| **d3d8to9** v1.16.0 (crosire) | BSD-2-Clause | Unmodified source (`third-party/d3d8to9`), built by `tools/build-d3d8to9-dll.sh` as the separate DLL `i386-windows/d3d8to9.dll` | Direct3D 8 on Direct3D 9 (which is DXMT's in a 32-bit session) for old games, opt-in per game (`env.MADEIRA_D3D8 = d3d9`); Wine's `d3d8.dll` stays the default. Copyright (C) 2015 Patrick Mours. Licence text: `LICENSES/BSD-2-d3d8to9.txt`; the project's source and tag are recorded in `third-party/d3d8to9/BCD-SOURCE.txt`. |
| **cnc-ddraw** v7.1.0.0 (FunkyFr3sh) | MIT; inside it: LodePNG (zlib licence), Khronos GL headers (MIT), `DetourEnumerateModules` from Microsoft Detours (MIT), CRC-32 by Craig Bruce (public domain), mingw-w64's `versionhelpers.h` | Unmodified source (`third-party/cnc-ddraw`, without its two Microsoft DirectX SDK headers and without Detours), built by `tools/build-cnc-ddraw-dll.sh` as the separate DLL `i386-windows/cnc-ddraw.dll` | A DirectDraw replacement for old 2D games (scaling, borderless full screen, Direct3D 9 or GDI drawing), opt-in per game (`env.MADEIRA_DDRAW = cnc`); Wine's `ddraw.dll` stays the default. Copyright (c) 2022 github.com/FunkyFr3sh. Licence texts: `LICENSES/MIT-cnc-ddraw.txt`, `LICENSES/zlib-lodepng.txt`, `LICENSES/MIT-Detours.txt`; provenance in `third-party/cnc-ddraw/BCD-SOURCE.txt`. |
| **DirectX Shader Compiler (DXC)** 1.9.2609 (Microsoft and the LLVM Team) | University of Illinois/NCSA Open Source Licence; inside it: miniz (MIT; `miniz.c` public domain), Henry Spencer's regex code (its own permissive terms), ConvertUTF (Unicode, Inc.), MD5 (public domain), `regstrlcpy.c` (ISC-style, Todd C. Miller) | **Patched**: one patch, `build/dxc-win/0001-mingw-w64-build.patch`, the MinGW-w64 port of DXC pull request #8265 by **Raed Rizqie** (five commits of 2026-03-11, head `c1d55772d52b54a28c69f713964295d7a407da7f`, offered to the project under its licence; closed unmerged), applied for BCD in November 2026 without the second of its two `StringRef.h` hunks (the removal of the `is_nothrow_constructible` specialisations, which this tag already skips under libc++) and with its MinGW `MS_SUPPORT_VARIABLE_LANGOPTS` define replaced by the out-of-class language-option definitions LLVM's Unix build already has (`LangOptions.cpp`; with the define the compiler read HLSL as C) | The DLL `arm64ec-windows/dxcompiler-optin.dll` (x86-64 Windows), built by `tools/build-dxcompiler.sh` from the project's tag `v1.9.2609` (commit `01b62ad47db7dee3dd33f90e7339cf9e860b3f93`, cloned at build time and checked) with the tracked llvm-mingw; the binary is not committed. Runs as `dxcompiler.dll` only for a game whose file says `env.MADEIRA_OPTIN_DLLS = dxcompiler`: wgpu games compile their Direct3D 12 shaders with it instead of Wine's FXC (round 663). Microsoft's own release binaries and their `dxil.dll` are **not** used (that package's Microsoft Software License Terms restrict it to Windows). Copyright (c) 2003-2015 University of Illinois at Urbana-Champaign; Copyright (C) Microsoft Corporation. Licence texts, as the project ships them: `LICENSES/DirectXShaderCompiler-LICENSE.txt`, `LICENSES/DirectXShaderCompiler-ThirdPartyNotices.txt`; provenance in `build/dxc-win/BCD-SOURCE.txt`. Linked statically against llvm-mingw's runtime (libc++, libunwind: Apache-2.0 with LLVM exception; the MinGW-w64 runtime: `LICENSES/MinGW-w64-runtime.txt`). A separate DLL a Windows program loads; nothing of DXC is linked into BCD. |
| **Mesa** 25.0.7 (OSMesa, Zink, softpipe; the Mesa project, mesa3d.org) | MIT; individual files carry other permissive licences (the source's `docs/license.rst`, copied into the bundle as `licenses/Mesa-license.rst`) | **Patched**: `build/mesa-ios/patches` (eight patches, offered under Mesa's MIT licence) | Separate dylib `gl/libOSMesa.dylib`, the desktop OpenGL backend of BCD's OpenGL driver (OSMesa draws through Zink): used only by a game whose `gl-backend` is `zink`. Built by `build/mesa-ios/build.sh` from the unmodified 25.0.7 release tarball (SHA-256 in the script) plus those patches; the binary is not committed. Licence text: `LICENSES/MIT-Mesa.txt`. |
| **MoltenVK** 1.4.2 (Khronos Group; The Brenwill Workshop Ltd) | Apache-2.0; inside it SPIRV-Cross and SPIRV-Tools (Apache-2.0) and cereal (BSD-3-Clause) | Unmodified. | Separate dylib `gl/libMoltenVK.dylib` (Vulkan on Metal), the Vulkan driver Mesa's Zink runs on. Built by `build/moltenvk-ios/build.sh` from the v1.4.2 tag (its commit is pinned in the script) and the dependencies its `fetchDependencies` pins; not committed. The script copies the licence texts of MoltenVK, SPIRV-Cross, SPIRV-Tools and cereal into the bundle's `licenses/` (`MoltenVK-Apache-2.0.txt`, `SPIRV-Cross-Apache-2.0.txt`, `SPIRV-Tools-Apache-2.0.txt`, `cereal-BSD-3-Clause.txt`). |
| **Mesa KosmicKrisp** (Mesa's Vulkan-on-Metal-4 driver by LunarG and Google, `src/kosmickrisp`; Mesa at commit `b39d173c`, 26.3.0-devel; the Mesa project, mesa3d.org) | MIT; individual files carry other permissive licences (the source's `docs/license.rst`, copied into the bundle as `licenses/KosmicKrisp-Mesa-license.rst`; licence text and copyright holders: `LICENSES/MIT-Mesa-KosmicKrisp.txt`) | **Patched**: `build/mesa-ios/playport-patches` (nineteen patches by the Playport authors, from playportdev/playport `412de5f` as Madeira's branch `playport/kosmickrisp` carries them; they change Mesa code and are offered under Mesa's MIT licence). The other build profile (`KK_PROFILE=release`, not the default) is the unmodified Mesa 26.2.4 release tarball with the six patches of `build/mesa-ios/kosmickrisp-patches` (125hz, Madeira #462; MIT). | Separate dylib `gl/libvulkan_kosmickrisp.dylib` (Vulkan 1.4 on Metal 4): used only by a game whose own file says `vulkan = kosmickrisp`, on iOS 26 or later with a GPU of the Metal 4 family. Built by `build/mesa-ios/build-kosmickrisp-bcd.sh` from the source archive of that commit (its SHA-256 is in `build/mesa-ios/fetch-playport.py`) plus those patches; the binary is not committed, and a build without it keeps Vulkan off for such a game. Build-time tools only, not shipped: LLVM, SPIRV-LLVM-Translator and SPIRV-Tools (they turn Mesa's own OpenCL C kernels into the shader source the library carries). |
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
| 2026-11-14 | From Madeira's branch `codex/d3d12-consolidation` (not merged into its main at that date): the Direct3D 12 sampler cache (`sampler-cache`) and the concurrent compute encoders (`cs-concurrent`), by Shitiz Garg with follow-ups by intraducine; the first is on by default in BCD with a switch that turns it off, the second is off unless set | 42 |
| 2026-11-14 | Madeira #575 (125hz): the host check of the fx_2_0 effect writer, taken with the writer itself (Madeira's Wine fork) and Direct3D 9 luminance render targets (Madeira's DXMT fork) | 37 |
| 2026-11-14 | Madeira #567 (nekodesusan-prog): the Japanese locale per game | 3 |

In all, **16,851 lines in 163 files** of this repository are identical to code that 25 people wrote for Madeira, counted as
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
| commit `3557bae` | intraducine | 2026-11-11 | 2026-11-14 | 15 | 1 | Fix D3D12 cache lifetime and scan accounting (the sampler cache per device) |
| commit `5a3db65` | Shitiz Garg | 2026-11-08 | 2026-11-14 | 13 | 1 | madeira-d3d12: identical CreateSampler descriptions share one Metal sampler |
| commit `984ed08` | Shitiz Garg | 2026-11-08 | 2026-11-14 | 7 | 1 | madeira-d3d12: opt-in concurrent compute encoders between barriers (cs-concurrent) |
| commit `826a967` | intraducine | 2026-11-12 | 2026-11-14 | 7 | 1 | Initialize concurrent compute option atomically |
| #575 | 125hz | 2026-11-05 | 2026-11-14 | 37 | 1 | Run-time compiled fx_2_0 effects and luminance render targets (the host check of the effect writer; the writer is in Madeira's Wine fork, the render targets in its DXMT fork, see "The submodule forks") |
| #567 | nekodesusan-prog | 2026-11-05 | 2026-11-14 | 3 | 1 | Japanese locale per game: code page 932, the CJK code page tables and a bundled Japanese font |

Madeira #461, #462 and #443 are pull requests in which intraducine combined other contributors' pull requests; the lines
are listed under the contributors whose commits they come from. Madeira's `docs/EPIC.md` states that the Epic game client is Akshay CM's
(Madeira #265) and the cloud save code and its interoperability fixtures are Tabîrcă Nicolae-Eduard's (Madeira #207), and that the cloud save code
follows Legendary (see the Epic row above); #443 also carries work by Ash Hopkins (#248) and Sacha Tardat. The four commits taken on
2026-11-14 are on Madeira's branch `codex/d3d12-consolidation`; they are counted without the run-of-three rule, because the two authors'
lines alternate inside the functions BCD took.

### People

| Author | Lines | Files |
|---|---|---|
| ft7t8drgwf-wq (insertfloppy) | 6990 | 55 |
| 125hz | 2458 | 36 |
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
| intraducine | 90 | 5 |
| domainus | 68 | 2 |
| NevesHenry | 56 | 5 |
| Tomba247 | 53 | 1 |
| byKAYRA | 44 | 2 |
| Shitiz Garg | 20 | 1 |
| JMRBDev | 12 | 1 |
| Kesavamoorthi G | 11 | 1 |
| Nikita Kiselov | 8 | 1 |
| nekodesusan-prog | 3 | 1 |

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
| `app/Madeira/BCDJapanese.c` | portions | 3 | #567 nekodesusan-prog 3 |
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
| `madeira-d3d12/src/pe/madeira_d3d12.c` | portions | 275 | #462 spitefulowl 141; #516 125hz 92; `3557bae` intraducine 15; `5a3db65` Shitiz Garg 13; `984ed08` Shitiz Garg 7; `826a967` intraducine 7 |
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

Taken after this count (2026-11-07, build 635): Madeira's external drive support, pull requests #464 ("Add external drive support": Connor
Gow's feature commit `41fa0ad`, and intraducine's `cb578f1` with David Brookes, whose safety checks it adapts from Madeira #235), #501
(Jelly-RayTian) and #536 (alantangok), under GPL-3.0-or-later with the Madeira Converter Exception; BCD's section 7 terms do not apply to these
lines. Counted as above (833 lines in 13 files: Connor Gow 334, intraducine with David Brookes 437, alantangok 56, Jelly-RayTian 6):

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `app/Madeira/ExternalDrives.swift` | whole file | 317 | #464 Connor Gow 221; #464 intraducine (with David Brookes) 90; #501 Jelly-RayTian 6 |
| `app/Madeira/ExternalStorage.swift` | whole file | 91 | #464 intraducine (with David Brookes) 91 |
| `app/Madeira/Library.swift` | portions | 6 | #464 Connor Gow 3; #536 alantangok 3 |
| `app/Madeira/MadeiraDock.swift` | portions | 5 | #464 Connor Gow 5 |
| `app/Madeira/SteamCloud.swift` | portions | 20 | #464 intraducine (with David Brookes) 20 |
| `app/Madeira/SteamInstall.swift` | portions | 32 | #464 intraducine (with David Brookes) 32 |
| `app/Madeira/SteamOwnedLibrary.swift` | portions | 40 | #464 Connor Gow 17; #464 intraducine (with David Brookes) 23 |
| `app/Madeira/SwiftSteam/Content/ContentDecryptor.swift` | portions | 4 | #464 intraducine (with David Brookes) 4 |
| `app/Madeira/SwiftSteam/Content/DepotDownloader.swift` | portions | 88 | #464 Connor Gow 88 |
| `tests/host/check-executable-browser-paths.py` | whole file | 53 | #536 alantangok 53 |
| `tests/host/check-external-storage.py` | portions | 106 | #464 intraducine (with David Brookes) 106 |
| `tests/host/check-steam-cloud.py` | portions | 58 | #464 intraducine (with David Brookes) 58 |
| `tests/host/check-steam-library.py` | portions | 13 | #464 intraducine (with David Brookes) 13 |

Single lines of #464 below the count's threshold are credited in their files' headers too (`ContentView.swift`, `MadeiraApp.swift`,
`SteamGames.swift`). `docs/EXTERNAL-STORAGE.md` names the pull requests and their authors.

Taken after this count (2026-11-14, build 660), from Madeira's main (pull request #462) and three Madeira branches that were not merged into
Madeira's main on that day, under
GPL-3.0-or-later with the Madeira Converter Exception unless said otherwise; BCD's section 7 terms do not apply to these lines. Counted as above.

*Steam Friends through the Dock*: Madeira pull request #572 by Vugar (branch `steam-friends-pr-572-replacement`: commits `ac65265`, `6626a28`,
`044babd`, `244ebd8`, `ccd7a3d`, `8286d1b`, `f9bf0c3` of 2026-11-04 to 2026-11-08) with `willfaust/madeira-dock` pull request #10 (see "The submodule
forks"). 408 lines in 6 files, all Vugar:

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `app/Madeira/DockFriends.swift` | whole file (Madeira's row view left out) | 178 | #572 Vugar 178 |
| `app/Madeira/DockFriendsMenu.swift` | portions | 3 | #572 Vugar 3 |
| `app/Madeira/MadeiraDock.swift` | portions | 19 | #572 Vugar 19 |
| `docs/MADEIRA_DOCK.md` | portions | 5 | #572 Vugar 5 |
| `tests/host/check-dock-friends.py` | whole file | 197 | #572 Vugar 197 |
| `tests/host/check-dock-report.py` | portions | 6 | #572 Vugar 6 |

*KosmicKrisp*: the build recipe of Madeira pull request #462 (125hz's `build-kosmickrisp.sh` and cross file, squashed by intraducine) and
branch `playport/kosmickrisp` (commit `07471e3`, intraducine, 2026-10-28: the profile switch, the pinned source and the Playport authors'
patches). 215 lines in 6 files, without the patches:

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `build/mesa-ios/build-kosmickrisp.sh` | whole file | 84 | #462 125hz 80; `07471e3` intraducine 4 |
| `build/mesa-ios/kosmickrisp-ios-arm64.cross` | whole file | 25 | #462 125hz 25 |
| `build/mesa-ios/build-playport-kosmickrisp.sh` | whole file | 10 | `07471e3` intraducine 10 |
| `build/mesa-ios/fetch-playport.py` | whole file | 24 | `07471e3` intraducine 24 |
| `build/mesa-ios/playport-patches/README.md` | whole file | 16 | `07471e3` intraducine 16 |
| `tests/host/check-playport-mesa-profile.py` | whole file | 56 | `07471e3` intraducine 56 |

Files taken whole that are left without a header comment, because the build applies them with `patch`: the six Mesa patches
`build/mesa-ios/kosmickrisp-patches/kosmickrisp-ios-0001-*.patch` to `0006-*.patch` (125hz, Madeira #462; 652 lines) and the nineteen Mesa
patches `build/mesa-ios/playport-patches/0001-*.patch` to `0019-*.patch` (the Playport authors, playportdev/playport `412de5f`, each with its
own `From:` line; 5,022 lines). All twenty-five change Mesa code and are offered under Mesa's licence (MIT), not under the GPL. Five lines of
`build/mesa-ios/build-kosmickrisp-bcd.sh` (how SPIRV-LLVM-Translator is built) are from Madeira #476 (intraducine) and credited in its header.

*GOG Galaxy service*: branch `playport/gog` (commit `9d6dad8`, intraducine, 2026-10-28), which carries the Playport authors' code
(playportdev/playport `412de5f`; GPL-3.0-or-later with the same exception text, adopted by the Playport authors for their own code). The
copyright is the Playport authors'. 1,138 lines in 5 files:

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `app/Madeira/BCDGOG/Galaxy/GalaxyWire.swift` | whole file | 385 | `9d6dad8` the Playport authors 385 |
| `app/Madeira/BCDGOG/Galaxy/GalaxyService.swift` | whole file (the token source over Playport's session left out) | 303 | `9d6dad8` the Playport authors 303 |
| `app/Madeira/BCDGOG/Galaxy/GalaxyListener.swift` | whole file | 124 | `9d6dad8` the Playport authors 124 |
| `app/Madeira/BCDGOG/Galaxy/BCDGOGGalaxyCore.swift` | portions | 24 | `9d6dad8` the Playport authors 24 |
| `tests/host/gog-galaxy/GalaxyTests.swift` | whole file | 302 | `9d6dad8` the Playport authors 302 |

Taken after this count (2026-11-08, build 646): Madeira pull request #556 by Henry Morin ("Vector registers in thread contexts; stop JIT
pool code-buffer leaks (Persona 5 Royal reaches gameplay)", commit `10df6a1` of 2026-11-05), under GPL-3.0-or-later with the Madeira
Converter Exception (the lines in `virtual_ios.c` and `mach_ios.c`: LGPL-2.1-or-later, as those Wine files); BCD's section 7 terms do
not apply to these lines. Counted as above (194 lines in 5 files, all Henry Morin):

| `build/ntdll-unix/virtual_ios.c` | portions | 38 | #556 Henry Morin 38 |
| `build/wineserver/mach_ios.c` | portions | 45 | #556 Henry Morin 45 |
| `tests/host/check-ctx-fp.py` | whole file | 83 | #556 Henry Morin 83 |
| `tests/host/check-pool-code-exit.py` | portions | 15 | #556 Henry Morin 15 |
| `tests/host/check-pool-startup-budget.py` | portions | 13 | #556 Henry Morin 13 |

Taken after this count (2026-11-08, build 646): Madeira's Windows Vulkan for x64 programs, pull requests #353 (125hz, commit `b74cb82`), #400
(Jelly-RayTian, commit `ac746ed`) and #462 (intraducine, who merged both, commit `7ed32a9`), and Madeira's Wine commit `df1c5ea5` (125hz, signed off by
intraducine), which BCD writes into the Wine tree at build time (`tools/patch-wine-vulkan-suboptimal.py`). The Wine unix side (`vulkan_ios.c`,
`vulkan_surface_ios.c`, the `d3dkmt_ios.c`, `driver_ios.c` and `virtual_ios.c` lines and the Wine change) is LGPL-2.1-or-later, the rest GPL-3.0-or-later with the
Madeira Converter Exception; BCD's section 7 terms do not apply to these lines. `WineProcessBridge.m`'s session set-up is BCD's adaptation of 125hz's. The idea in
`vulkan_ios.c`'s re-entrancy guard is NutsEmu's Wine patch 0083 (no code taken). Counted as above (641 lines in 18 files):

| `app/Madeira.xcodeproj/project.pbxproj` | portions | 4 | #353 125hz 4 |
| `app/Madeira/IOSDisplayShim.m` | portions | 13 | #400 Jelly-RayTian 13 |
| `app/Madeira/WineProcessBridge.m` | portions (adapted) | 20 | #353 125hz 20 |
| `app/Madeira/Winios/VulkanLayerGeometry.h` | whole file | 26 | #400 Jelly-RayTian 26 |
| `app/Madeira/Winios/Winios.m` | portions | 21 | #400 Jelly-RayTian 21 |
| `build/ntdll-unix/build.sh` | portions | 5 | #353 125hz 5 |
| `build/ntdll-unix/virtual_ios.c` | portions | 5 | #353 125hz 5 |
| `build/win32u-unix/build.sh` | portions | 3 | #353 125hz 3 |
| `build/win32u-unix/d3dkmt_ios.c` | portions | 7 | #353 125hz 7 |
| `build/win32u-unix/driver_ios.c` | portions | 8 | #353 125hz and #462 intraducine 8 |
| `build/win32u-unix/vulkan_ios.c` | whole file | 81 | #353 125hz, #400 Jelly-RayTian and #462 intraducine 81 |
| `build/win32u-unix/vulkan_surface_ios.c` | whole file | 87 | #400 Jelly-RayTian and #462 intraducine 87 |
| `tests/host/check-unixlib-binder.py` | portions | 2 | #400 Jelly-RayTian 2 |
| `tests/host/check-vk-surface-query.py` | whole file | 124 | #462 intraducine and #353 125hz 124 |
| `tests/host/check-vkd3d-fx2-effects.py` | portions | 37 | #575 125hz 37 |
| `tests/host/check-vulkan-layer-geometry.py` | whole file | 62 | #400 Jelly-RayTian 62 |
| `tests/host/check-vulkan-opt-in.py` | portions | 61 | #400 Jelly-RayTian and #462 intraducine 61 |
| `tests/host/check-vulkan-surface.py` | portions | 89 | #400 Jelly-RayTian and #462 intraducine 89 |
| `tools/patch-wine-vulkan-suboptimal.py` | portions | 23 | Madeira's Wine commit df1c5ea5 (#353) 125hz 23 |

Taken after this count (2026-11-14, build 660): from Madeira's main after `5fda6a0`, pull requests #604 (NevesHenry, with intraducine's
limit on manifest totals: malformed Steam protobuf tags and manifests throw instead of stopping the app), #541 (alantangok: the built-in
JIT's Prepare retries a duplicate Cryptex install once the Developer Disk Image is mounted; BCD took the Prepare half), #607 (intraducine:
FFmpeg 7.1.5, its build objects by version), #579 (NevesHenry: the check of a wineserver that dies at start-up; BCD had the fix itself
since round 624), #568 (Henry Morin, with 125hz's follow-up `43c2011`: Persona 3 Reload's list), #564 (NevesHenry: `-fno-builtin` for
check-vcrt-arm64-key, commit `ba96c15`) and 125hz's #556 follow-ups `494274a` (the MADEIRA_CTX_FP answer kept for a second) and `18fcffa`
(its check), under GPL-3.0-or-later with the Madeira Converter Exception (the lines in `mach_ios.c`: LGPL-2.1-or-later, as that Wine
file); BCD's section 7 terms do not apply to these lines.
Counted as above (355 lines in 10 files: alantangok 107, NevesHenry with intraducine 103, NevesHenry 60, intraducine 58, 125hz 22, Henry Morin 5):

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `app/MadeiraJITHelper/MadeiraJITHelper.swift` | portions | 17 | #541 alantangok 17 |
| `build/ffmpeg/build.sh` | portions | 3 | #607 intraducine 3 |
| `build/wineserver/mach_ios.c` | portions | 10 | commit `494274a` (#556 follow-up) 125hz 10 |
| `recommended/recommendations.json` | portions | 5 | #568 Henry Morin 5 |
| `tests/host/check-ctx-fp.py` | whole file | 12 | commit `18fcffa` (#556 follow-up) 125hz 12 (the rest: #556 Henry Morin, above) |
| `tests/host/check-ffmpeg-source-reuse.py` | whole file | 55 | #607 intraducine 55 |
| `tests/host/check-jit-cryptex-recovery.py` | whole file | 90 | #541 alantangok 90 |
| `tests/host/check-steam-malformed-protobuf.py` | whole file | 103 | #604 NevesHenry and intraducine 103 |
| `tests/host/check-vcrt-arm64-key.py` | portions | 3 | #564 NevesHenry 3 |
| `tests/host/check-wineserver-fatal.py` | whole file | 57 | #579 NevesHenry 57 |

Lines of #604 below the count's threshold are credited in their files' headers too (`SteamProtoMessages.swift`, `DepotManifest.swift`).

Vulkan for 32-bit programs (2026-11-14, build 660) is BCD's own work on that route and on Wine's own WoW64 Vulkan path; Madeira has no 32-bit
Vulkan and no Madeira code was taken for it. `tools/patch-wine-vulkan-wow64.py` changes Wine's `dlls/winevulkan` (the generator `make_vulkan`, the
generated `vulkan_thunks.c`, `vulkan.c`, `vulkan_private.h`) and `dlls/win32u/vulkan.c` in the checked-out tree at build time: the text it writes and
the Wine lines it quotes to find its places are LGPL-2.1-or-later like Wine, and Wine's own copyright lines in those files stand (Roderick
Colenbrander, Jacek Caban for CodeWeavers and Wine's other authors). It builds on two things that are in Madeira's Wine fork already and are credited
in the fork's `LICENSE-MADEIRA.md`: 125hz's guest-window helpers `ios_wow_host_ptr` / `ios_wow_guest_ptr32` (`include/wine/unixlib.h`, `970dac54a4e`
and `db62a711998`) and his per-process `caller_zero_bits()` in win32u's Vulkan memory code (`1a73c698b8c`); and it does for `make_vulkan` what
Madeira's OpenGL change by Connor Gow does for `make_opengl` (the WoW64 thunks convert embedded guest pointers and keep handles and callbacks
unchanged; the idea, no code). `tests/host/vulkan-wow64/harness.c` follows
Wine's `dlls/winevulkan/loader.c` where it plays the 32-bit `winevulkan.dll` (the client objects and the decoding of the two debug callback blocks).

### Game lists

These entries of BCD's recommended settings follow tested lists of Madeira, and the notes of each entry say so: Fallout: New Vegas and
Fallout 3 (the older NVIDIA card, ported 2026-10-28 from the list of ft7t8drgwf-wq, Madeira `adec653`, with DXMT `f25e556`); Call of Duty:
Black Ops (the card and the resolution, from Madeira #519 by ft7t8drgwf-wq), The Escapists 2 (from Madeira #548 by ft7t8drgwf-wq),
Borderlands 2 (from Madeira #531 by ft7t8drgwf-wq) and Dark Souls: Remastered (from Madeira #461, combined by intraducine), all ported on
2026-11-05; Persona 3 Reload (from Madeira #568 by Henry Morin, with 125hz's follow-up), ported on 2026-11-14.

### The submodule forks

BCD's forks of Wine, FEX-Emu and DXMT hold commits that Madeira's forks hold too, with their authors kept in the commit history:
the file `LICENSE-MADEIRA.md` of each fork lists them, with the Madeira commit each comes from (`wine/LICENSE-MADEIRA.md`,
`FEX/LICENSE-MADEIRA.md`, `dxmt/LICENSE-MADEIRA.md`). BCD's `madeira-dock` is a commit of Madeira's own (`72558e4`) plus one commit: Steam Friends
(`willfaust/madeira-dock` pull request #10 by Vugar, head `9ce5d63`, taken on 2026-11-14 before it merged; GPL-3.0-or-later with the Madeira
Converter Exception, Vugar the commit's author). Its files are kept byte for byte with the pull request: the new files `src/friends.c`,
`src/friends.h`, `src/friends_format.c`, `src/friends_format.h` and `tests/test-friends_format.c` carry Madeira's usual header naming 125hz,
but they are Vugar's work (every commit of the pull request is his). It holds nothing of BCD's beyond the three lines that place the change in
that tree; BCD's own changes to the Dock are made at build time by `tools/patch-dock-*.py`.

Two changes of Wine itself, made after the Wine 11.4 that BCD's fork is based on, are not commits of the fork: BCD writes them into the
Wine tree at build time, for the 32-bit ntdll only (the 64-bit ntdll.dll is a committed binary). Both are by Paul Gofman for CodeWeavers,
**LGPL-2.1-or-later** like Wine (BCD's section 7 terms do not apply to these lines); the scripts and their checks are BCD's.

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `tools/patch-wine-veh-next-handler.py` | portions | 1 | Wine commit da00e5f5cc9 (2026-03-23, "ntdll: Do not keep next handler pointer outside of lock in call_vectored_handlers().") Paul Gofman 1 |
| `tools/patch-wine-woa-wake-order.py` | portions | 5 | Wine commit 81e52628b25 (2026-10-12, "ntdll: Make sure entry is removed from queue if unlocked entry.addr precheck in RtlWaitOnAddress() suggests so.") Paul Gofman 5 |

### Other people's work in BCD

Pull requests sent to BCD (`thebcd69/bcd-games`: #4 by mckenna654, #5 and #16 by le0-VV, #11 by vancez, #12 by Pardoxum, #27 by creeper4004, #31 by iqui27 (Henrique Rocha), #33 by Taala2, #43 by pauloec (Paulo Eduardo Correa),
#46 by rbarton124) and work from rtmtree's
Persona 3 Reload fork of BCD (helper memory, root certificate enumeration, the bytes at the instruction pointer in the guest log) are credited
in the files that hold them and in `README-BCD.md`.

Work from netheritenee's fork of BCD 0.1.659 (a report shared through the owner on 2026-11-15; netheritenee on Reddit), taken in build 665:
the finding that a process started by another one (RDR2 and GTA V Enhanced from Rockstar's launcher, Social Club's helper) never
healed its kernel32 imports into its own ntdll copy, with the one-condition change of `ios_jit_heal_aux_iat` that lets it
(`build/ntdll-unix/virtual_ios.c`, LGPL-2.1-or-later like that Wine file; BCD added the off switch `env.MADEIRA_AUX_IAT_HEAL_CHILD`, the
`MADEIRA_IMAGE_RELOAD=0` guard and the cases of `tests/host/check-aux-iat-heal.py`); the Red Dead Redemption 2 settings of the two RDR2
rows of `recommended/recommendations.json` (rdr2-steam v16, rdr2-other v7, 22 lines from build 665, listed in the rows' notes); and the
finding that `env.MADEIRA_NTDLL_SYNC = native` dies at start (the catalog note). The report's other findings (a binary kernel32 timer
patch, which BCD's own `kernel32-usd.dll` of build 663 replaces) were not taken.

Pull request #46 of `thebcd69/bcd-games` by rbarton124 (Russell Barton), "Ubisoft Connect and its
games now run on BCD" (taken 2026-11-11, build 652): twenty commits written against 0.1.634 and signed off under the Developer Certificate of Origin (the author says the
changes were written with an AI assistant and tested by him on a device), submitted under GPL-3.0-or-later with the Madeira Converter Exception,
his changes inside BCD Material files on the terms of `LICENSE-BCD.md`, so those files stay BCD Material (the lines in `build/ntdll-unix/`:
LGPL-2.1-or-later, as those Wine files, the way Madeira's contributors' lines there are treated above). What came in: the TLS slot of an ARM64EC
module reaches its pool copy; reserved areas keep FEX and thin areas apart from a generic merge; a 64-bit hook zone above each ntdll
(`env.MADEIRA_X64_HOOK_ZONE`); a program's WriteProcessMemory into itself (`env.MADEIRA_SELF_WRITE`); I/O completions in a non-alertable
sleep and a one-second re-check of an infinite wait on an address, both as Wine patch scripts; the release of what a killed process held;
Ubisoft Connect's Chromium switches and the in-memory libcef.dll change for one checked build; the Dock's handling of Ubisoft Connect's
installer, session lines and resident client; merging and cutting of free code buffers and a code-buffer policy for the program being played;
Wine's DxDiag provider as an opt-in DLL with its DirectShow patch; an empty OpenGL extension list; the FEX split-lock patch
(`tools/patch-fex-ios-split-lock.py`); the game's own file for `env.MADEIRA_HIDPAD_XINPUT`; the Xbox 360 controller identity; and the lists of
Far Cry 3, Far Cry 4 and Ubisoft Connect. Counted from the pull request's own diff, before BCD's changes (6028 added lines in
59 files, all rbarton124; generated files are not counted):

| File | Taken | Lines | Source, author, lines |
|---|---|---|---|
| `tests/host/check-dxdiagn-optin.py` | whole file | 122 | #46 rbarton124 122 |
| `tests/host/check-fex-split-lock.py` | whole file | 364 | #46 rbarton124 364 |
| `tests/host/check-gl-empty-extensions.py` | whole file | 199 | #46 rbarton124 199 |
| `tests/host/check-kill-reap.py` | whole file | 85 | #46 rbarton124 85 |
| `tests/host/check-pool-carve-merge.py` | whole file | 402 | #46 rbarton124 402 |
| `tests/host/check-pool-code-policy.py` | whole file | 476 | #46 rbarton124 476 |
| `tests/host/check-reserved-area-merge.py` | whole file | 126 | #46 rbarton124 126 |
| `tests/host/check-self-write.py` | whole file | 385 | #46 rbarton124 385 |
| `tests/host/check-sleep-kernel-apc.py` | whole file | 178 | #46 rbarton124 178 |
| `tests/host/check-tls-index-sync.py` | whole file | 182 | #46 rbarton124 182 |
| `tests/host/check-ubi-cef.py` | whole file | 235 | #46 rbarton124 235 |
| `tests/host/check-woa-recheck.py` | whole file | 313 | #46 rbarton124 313 |
| `tests/host/check-x64-hook-zone.py` | whole file | 370 | #46 rbarton124 370 |
| `tools/patch-fex-ios-split-lock.py` | whole file | 194 | #46 rbarton124 194 |
| `tools/patch-wine-dxdiagn-directshow.py` | whole file | 133 | #46 rbarton124 133 |
| `tools/patch-wine-sleep-kernel-apc.py` | whole file | 106 | #46 rbarton124 106 |
| `tools/patch-wine-woa-recheck.py` | whole file | 232 | #46 rbarton124 232 |
| `.github/workflows/build-ipa.yml` | portions | 80 | #46 rbarton124 80 |
| `app/BCDPreview/BCDSettingsPreviewData.swift` | portions | 1 | #46 rbarton124 1 |
| `app/Madeira/BCDPadModel.swift` | portions | 9 | #46 rbarton124 9 |
| `app/Madeira/BCDSessionLive.swift` | portions | 4 | #46 rbarton124 4 |
| `app/Madeira/BCDSettingsSections.swift` | portions | 2 | #46 rbarton124 2 |
| `app/Madeira/ContentView.swift` | portions | 8 | #46 rbarton124 8 |
| `app/Madeira/DockGameEnd.swift` | portions | 8 | #46 rbarton124 8 |
| `app/Madeira/DockInstallers.swift` | portions | 68 | #46 rbarton124 68 |
| `app/Madeira/GameRecommendationsList.swift` | portions | 52 | #46 rbarton124 52 |
| `app/Madeira/GamepadInput.swift` | portions | 15 | #46 rbarton124 15 |
| `app/Madeira/Library.swift` | portions | 10 | #46 rbarton124 10 |
| `app/Madeira/MadeiraDock.swift` | portions | 2 | #46 rbarton124 2 |
| `app/Madeira/WineProcessBridge.m` | portions | 12 | #46 rbarton124 12 |
| `build/hidpad/hidpad_ids.h` | portions | 25 | #46 rbarton124 25 |
| `build/hidpad/hidpad_reports.h` | portions | 66 | #46 rbarton124 66 |
| `build/ntdll-unix/loader_ios.c` | portions | 13 | #46 rbarton124 13 |
| `build/ntdll-unix/process_ios.c` | portions | 142 | #46 rbarton124 142 |
| `build/ntdll-unix/virtual_ios.c` | portions | 878 | #46 rbarton124 878 |
| `build/tools/autotune_rules.py` | portions | 10 | #46 rbarton124 10 |
| `build/tools/gen-config-catalog.py` | portions | 103 | #46 rbarton124 103 |
| `recommended/recommendations.json` | portions | 77 | #46 rbarton124 77 |
| `tests/host/bcd_padmodel_checks.swift` | portions | 8 | #46 rbarton124 8 |
| `tests/host/check-bcd-padmodel.py` | portions | 18 | #46 rbarton124 18 |
| `tests/host/check-d3d9-x64.py` | portions | 5 | #46 rbarton124 5 |
| `tests/host/check-desktop-exit.py` | portions | 3 | #46 rbarton124 3 |
| `tests/host/check-dock-contract.py` | portions | 5 | #46 rbarton124 5 |
| `tests/host/check-dock-game-end.py` | portions | 21 | #46 rbarton124 21 |
| `tests/host/check-dock-installers.py` | portions | 79 | #46 rbarton124 79 |
| `tests/host/check-farm-imports.py` | portions | 2 | #46 rbarton124 2 |
| `tests/host/check-frontend.py` | portions | 5 | #46 rbarton124 5 |
| `tests/host/check-game-recommendations.py` | portions | 26 | #46 rbarton124 26 |
| `tests/host/check-hidpad.py` | portions | 93 | #46 rbarton124 93 |
| `tests/host/check-log-redaction.py` | portions | 2 | #46 rbarton124 2 |
| `tests/host/check-pool-code-exit.py` | portions | 10 | #46 rbarton124 10 |
| `tests/host/check-pool-startup-budget.py` | portions | 8 | #46 rbarton124 8 |
| `tests/host/check-session-switches.py` | portions | 12 | #46 rbarton124 12 |
| `tests/host/check-thin-reserve.py` | portions | 2 | #46 rbarton124 2 |
| `tests/host/check-zero-mitigation-policy.py` | portions | 2 | #46 rbarton124 2 |
| `tools/build-wine-extra-dlls.sh` | portions | 29 | #46 rbarton124 29 |
| `tools/build-xtajit-wow64.sh` | portions | 4 | #46 rbarton124 4 |
| `tools/build-xtajit64.sh` | portions | 4 | #46 rbarton124 4 |
| `tools/farm-baseline.json` | portions | 3 | #46 rbarton124 3 |
| `app/Madeira/arm64ec-windows/windowscodecs.dll`, `wininet.dll`, `cryptnet.dll` | not taken | 0 | #46 rbarton124's commit 16 strips their debug sections; BCD did the same to its own copies with `llvm-strip --strip-debug` and took no binary from the pull request |

### Pull requests sent to BCD, file by file

Each file below also carries the author's "Portions copyright" line in its header. All of it is under GPL-3.0-only (the files of the Wine unix side,
`build/ntdll-unix/`, under LGPL-2.1-or-later like Wine); a file that was BCD's before keeps BCD's lines, and BCD's changes to the received code are marked
"modified for BCD" in the header or at the lines.

| Pull request | Author | Files and what |
|---|---|---|
| #43, 2026-11-04 | pauloec (Paulo Eduardo Correa) | The native iPhone Duo interface: `app/Madeira/BCDFrontEnd.swift` (`BCDDuoFrontEnd`), `BCDLibraryView.swift` (`BCDDuoLibraryView`), `BCDSettings.swift` (`BCDDuoSettingsRoot`), `BCDHomeView.swift` (Windows desktop card, controller navigation), `BCDDownloadsView.swift`, `BCDFocus.swift` (navigators, layer ownership), `BCDFoldable.swift` (`BCDDuoFeatures`, `BCDDuoPageOwner`), `BCDFoldableSettings.swift`, `BCDFoldableUI.swift`, `BCDGamePage.swift` (embedded page), `BCDLiveFrontEnd.swift` (system prompts as menus), `BCDLiveStore.swift`, `BCDMenu.swift`, `BCDSessionLive.swift`, `BCDStyle.swift` (Liquid Glass), `BCDArt.swift`, `BCDSectionKit.swift`, `BCDSettingsCore.swift`, `BCDSettingsSections.swift`, `BCDStore.swift`, `BCDGyroLive.swift`, `BCDInputLive.swift`, `ContentView.swift`, `GamepadInput.swift`, `HardwareInput.swift`, `Library.swift`; the Windows desktop's quit and background compositor: `WineProcessBridge.m/.h`, `WineServerBridge.m/.h`, `Winios/Winios.m/.h`, `build/wineserver/fd_ios.c`, `mach_ios.c`; `tools/patch-dxmt-metal-atomics.py`; host checks `check-duo-controller-navigation.py`, `check-duo-download-session.py`, `check-desktop-quit.py`, `check-dxmt-metal-atomics.py` and edits to six existing checks |
| #31, 2026-10-27 | iqui27 (Henrique Rocha) | The tabletop pose: `app/Madeira/ContentView.swift` (`FoldTabletop`, the game surface, touch mapping and touch controls below the fold), `BCDFoldable.swift` (`BCDTabletop`), `tests/host/check-bcd-touch.py` |
| #27, 2026-10-27 | creeper4004 | Colour profiles: `app/Madeira/BCDThemeCore.swift`, `BCDTheme.swift`, `BCDThemes.json`, `BCDSettingsAppearance.swift`, `docs/color-profiles.md`, `tests/host/check-bcd-themes.py`; one-line colour changes in `BCDStyle.swift`, `BCDSettings.swift`, `BCDSettingsCore.swift`, `BCDAccounts.swift`, `BCDComponents.swift`, `BCDControllerScreen.swift`, `BCDFrontEnd.swift`, `BCDGamePage.swift`, `BCDHomeView.swift`, `BCDInGameMenu.swift`, `BCDSectionKit.swift`, `BCDSession.swift`, `BCDSetupView.swift`, `BCDStartingScreen.swift`, `BCDTouchControls.swift`, `BCDTouchEditor.swift`, `MadeiraApp.swift`, `tools/ui-preview.py` |
| netheritenee's fork, 2026-11-15 | netheritenee | The child-process condition of `ios_jit_heal_aux_iat` in `build/ntdll-unix/virtual_ios.c`; the RDR2 settings of `recommended/recommendations.json` (rdr2-steam v16, rdr2-other v7) |
| #33, 2026-10-30 | Taala2 | Cross-process TlsFree diagnostics: `build/ntdll-unix/ios_tls_diag_util.h`, `ios_nw_tls_diag.h`, parts of `ios_tls_clear.h`, `virtual_ios.c`, `signal_arm64_ios.c`; `tests/host/check-tls-clear-process.py` |

## Code from Porthole (tylerkaska112/Porthole)

Porthole ([tylerkaska112/Porthole](https://github.com/tylerkaska112/Porthole)) is another fork of Madeira, by The Porthole authors. In
November 2026 BCD took the code listed here from Porthole 1.3.0 (commit `99ec315` of 2026-11-12).

**Licence position.** Porthole's own code is licensed under **GPL-3.0-or-later with the Madeira Converter Exception** (its `LICENSE` and
`LICENSE-EXCEPTION.md`; its files carry `SPDX-License-Identifier: GPL-3.0-or-later` and `Copyright (C) 2026 The Porthole authors`). BCD took
it under those terms; the copyright stays with The Porthole authors, and BCD's additional terms under GPL-3.0 section 7 (`LICENSE-BCD.md`) do
not apply to it, including where it sits in a file that also holds BCD's own text. A file that holds some of Porthole's code carries a
"Portions copyright 2026 The Porthole authors" line in its header naming the source and the date (GPL-3.0 section 5); none of those files is
BCD Material. Porthole's `README.md` says that a large part of its code was written with an AI assistant.

**Counts.** "Lines" are non-trivial lines identical to Porthole's, by the rule of the Madeira section above (white space collapsed, at least
12 characters, not a lone brace or keyword), counted by machine against the Porthole files named beside them: over the whole file for a file
the port added, over the lines the port added for a file BCD had before. Where BCD rewrote Porthole's code under its own
names the number in brackets is the lines that follow Porthole's statements, counted by hand; those files are credited all the same.

### Files

| File | Taken | Lines | From Porthole (path at commit `99ec315`), what |
|---|---|---|---|
| `tools/patch-winemetal-framegen-live.py` | portions | 3 (34) | `patches/0135-dxmt-frame-generation-gating-and-stats.patch`, `0136-dxmt-frame-generation-runtime-switch.patch`, `0137-dxmt-frame-generation-strict-drawable-gate.patch`, `0138-dxmt-frame-generation-slow-games.patch`: counting the frames this path presented until they reach the screen (`madeira_fgl_track`, the presented handler), the gate "frames in flight + 3 > the layer's drawable count" and "game frame shorter than 1.9 screen periods" (`madeira_fgl_gate_decide`, `madeira_fgl_may_generate`), a frame not generated moving the history on, the 160 ms stall limit and the 10-block search under 20 FPS, the idea of a run-time switch with the pictures released at the next present, and the shape of the two-second `[framegen]` line. BCD's own in the same file: the spare interpolator set and the build limit, the GPU guard, the count's restart, measure only, the status for the menu |
| `tools/patch-winemetal-present-fx.py` | portions | 3 (12) | `patches/0196-upscale-to-the-screen-dxmt.patch` and `0196-upscale-to-the-screen-madeira-d3d12.patch`: the "to the screen" factor (the smaller of screen / picture in both directions, at most 2, no scaler under 1.1; `madeira_pfx_target`). `patches/0189-lossless-scaling-frame-generation.patch`: every generated frame is presented from a command buffer of its own that is committed before the next drawable is taken (`madeira_pfx_finish_pair`; the reason, three drawables per layer, is in that patch's notes). BCD's own in the same file: the stand-in drawable, the deferred present, the scaler and its bilinear fallback, the live switch |
| `madeira-d3d12/src/pe/madeira_d3d12.c` | portions | 82 (7) | `patches/0196-upscale-to-the-screen-madeira-d3d12.patch`: the "to the screen" factor for a swap chain (`mad_pfx_upscale`); BCD reads the screen's size from winemetal (MadeiraCtl op 30) and writes the value as `fit`. `patches/0191-lossless-scaling-d3d12-bridge-madeira-d3d12.patch`: the Direct3D 12 side of the Lossless Scaling bridge (`struct lsfg_bridge_frame`, `g_lsfg`, `mad_blit_texture`, `mad_lsfg_before`, `mad_lsfg_show` and their call in `mad_present_run`), with the lines of 0196 that enlarge a generated picture with the swap chain's MetalFX scaler. BCD's changes: only the app's `MADEIRA_LSFG_CTL` switches it on, BCD's log marker, and the swap chain it follows is given up when that swap chain is released |
| `app/Madeira/BCDPresentFX.swift` | portions | 0 (4) | `patches/0196-upscale-to-the-screen.md` and its two patches: the same factor in the app (`BCDUpscale.fitFactor`), for the Direct3D 11 swap chain of a session without present-fx |
| `tools/patch-dxmt-lsfg.py` | whole file (the header it writes, `dxmt/src/d3d11/d3d11_lsfg.hpp`, and the code it puts into `d3d11_swapchain.cpp`) | 623 | `patches/0189-lossless-scaling-frame-generation.patch` (`d3d11_lsfg.hpp`: `Control`, `ShaderStore`, `FrameGen`; `LsfgBeforePresent`, the presents of the generated pictures), `patches/0191-lossless-scaling-d3d12-bridge-dxmt.patch` (its changes to `d3d11_lsfg.hpp`: `processAllowed`, sRGB back buffers, `generatedFormat`), `patches/0197-lsfg-upscale-own-scaler.patch` (`EnsureUpscaledGenerated`). BCD's changes: a patch script instead of a patch, BCD's folder in the prefix, the log marker, the present state of a frame without generated pictures is taken where it was before. The pass layout in that header follows lsfg-vk 1.0.0 (below) |
| `tools/patch-d3d11-src-lsfg-bridge.py` | whole file (the file it writes, `dxmt/src/d3d11/d3d11_lsfg_bridge.cpp` in d3d11-src.dll's copy of the sources, and the code it puts into `d3d11_fence.cpp`, `d3d11_fence.hpp` and `d3d11.def`) | 221 | `patches/0191-lossless-scaling-d3d12-bridge-dxmt.patch`: `d3d11_lsfg_bridge.cpp` (`DXMTLsfgBegin`, `DXMTLsfgRun`, `DXMTLsfgAbort`: the passes of a Direct3D 12 game run on a Direct3D 11 device of their own), `CreateFenceExposingEvent`, the three exports. BCD's changes: a patch script instead of a patch, applied only to the copy `tools/build-d3d11-dll.sh` builds d3d11-src.dll from; the file's header |
| `app/Madeira/BCDLossless.swift` | portions | 90 | `app/Sources/Core/LosslessLicence.swift` (the receipt, its status, the sealed file: `BCDLosslessLicence`), `app/Sources/Core/LosslessShaders.swift` (the PE resource reader and the container: `BCDLosslessShaders`), `app/Sources/Core/LosslessScaling.swift` (the control file's fields and text, the status sentences). BCD's changes: the account is the SHA-256 of the Steam account's name, nobody signed in does not count, only the frame generation's 48 shaders are read and written, the menu's rows and Settings' words |
| `app/Madeira/BCDLosslessLive.swift` | portions | 67 | `app/Sources/Core/LosslessScaling.swift`: the device secret in the Keychain, `verify`, `download`, `remove`, `dllData`, `prepareForGame`, `apply`, `engineStatus`, `sessionEnded`. BCD's changes: BCD's Steam connection (`SteamOwnedLibrary.licenceNow`, `downloadForApp`), the start of a session on the launch worker, the frame-rate limit, one generator per session |
| `tools/patch-fex-ios-callret-lean.py` | portions (in the code it writes into FEX's two files) | 0 (5) | `patches/0143-fex-lean-callret.patch`, its changes to FEX's `JIT.cpp` and `JITClass.h`: no suspend doorbell test at the entry point a guest CALL falls through into (`EmitEntryPoint`'s `SkipSuspendCheck`, `IsCallReturnFallthrough`). BCD's changes: an overlay script for the ARM64EC module's build, off unless `MADEIRA_FEX_CALLRET_LEAN` is exactly 1 (Porthole has it on by default), the switch read in the JIT file and said in the log by the module. Not taken from that patch: the two-instruction call-ret stack guard and the 8 MiB-aligned stack base (BCD's JIT keeps no call-ret stack) |
| `tests/host/bcd_lossless_checks.swift` | portions | 10 (31) | `app/Tests/Licence/main.swift`: the receipt and sealed-file cases |
| `app/Madeira/BCDDriveSpeed.swift` | whole file but its last type | 61 | `app/Sources/Core/DriveSpeedTest.swift`: the drive speed test (`BCDDriveSpeed`, `BCDDriveWriteMode` with the 40 MB/s limit, `BCDDriveSpeedTest.quickWriteMBps` and `run`). BCD's changes: BCD's names and folder, the file system's name is read in the file itself. `BCDDriveGentle` at its end is BCD's own; that a slow drive gets four chunks in flight, in order, follows `app/Sources/Steam/SteamInstaller.swift` and `Core/ExternalLibrary.swift` (knowledge only) |
| `app/Madeira/BCDSchedHints.swift` | portions | 1 (9) | `app/Sources/Core/CPUClampMonitor.swift`: the rule that says iOS holds the fast cores back (`BCDCPUClamp.step`: a sample counts under half a core of load or more; 1.3 GHz or below for three seconds, 1.8 GHz or above for two) and the idea of the two log lines. `app/Sources/Core/SchedHints.swift`: which hints there are. BCD's changes: the rule works on time instead of on a count of samples, one key with a log-only value, the totals for the session's end |
| `app/Madeira/BCDSchedHintsLive.swift` | portions | 4 (9) | `app/Sources/Core/SchedHints.swift`: the activity's options (user-initiated, latency-critical, no idle sleep) and asking the kernel for the first latency and throughput tiers at a session's start (`begin`, `end`). `app/Sources/Core/CPUClampMonitor.swift`: the once-a-second reading of the counters and the thermal state in a word (`tick`, `thermal`). BCD's changes: off unless `sched-hints` asks, the tiers are read back for the log and put back at the end |
| `app/Madeira/BCDSchedHints.m` | portions | 8 (7) | `app/Sources/Core/SampleProfiler.c`: `sched_hint_task_qos` (`madeira_sched_task_qos`) and `cpu_clock_sample` (`madeira_cpu_clock_sample`). BCD's changes: the tiers are passed as the kernel's two-field structure (it refuses the single number Porthole passes), `madeira_sched_task_tiers` reads them back, no energy counter |
| `app/Madeira/BCDPadAuto.swift` | the two rules (all but BCD's key, words and remembered lists) | 35 | `app/Sources/Core/PadAuto.swift` (`PadAuto`: the controller becomes keyboard and mouse when the game has not read it after a grace period of 25 seconds, and a controller again at its first read; `BCDPadAuto`) and `app/Sources/Core/LayoutSuggestion.swift` (`LayoutSuggestion`: the 20-second rule for the on-screen controller; `BCDLayoutHint`). BCD's changes: BCD's names, the game's own line `pad-auto`, the words, the remembered lists as plain functions, the hint is a line over the game instead of a card |
| `app/Madeira/Winios/WiniosGamepad.c` | portions | 5 | `patches/0019-pad-read-counters.patch`: the count of the game's controller reads (`pad_reads`, `winios_gamepad_read_count`). BCD's changes: only the XInput slots are counted (BCD's HID pad is read by the wineserver's own statistics too) |
| `app/Madeira/Winios/WiniosGamepad.h` | portions | 1 | `patches/0019-pad-read-counters.patch`: the declaration of `winios_gamepad_read_count` |
| `app/Madeira/BCDFloatingStick.swift` | whole file | 11 | `app/Sources/Core/FloatingStick.swift`: the floating stick's zone (its frame grown by one width each side and 0.8 of its height above and below) and where its ring is centred (`BCDFloatingStick.zone`, `centre`). BCD's changes: BCD's names; the setting is the in-game menu's instead of an app-wide one, and a control in the zone keeps its touches by lying above it instead of by a list of neighbours |
| `app/Madeira/TouchGamepad.swift` | portions | 3 (6) | `app/Sources/UI/HUD.swift`: the safe area a floating stick's ring may be drawn in (`TouchPadView.field`) and the stick's push measured from the ring's centre, one thumb to a stick (the floating branch of `touchesBegan`) |
| `app/Madeira/TouchControlPresets.swift` | portions | 42 | `app/Sources/Core/VirtualPadModel.swift` (`VirtualPadLayout.place` and `frames`): five of Porthole's arrangements (sticks side by side in the middle, handheld, retro, racing, minimal) with their proportions, room and shrinking, and Porthole's places for the face buttons in a cluster (`ControlPresetLayout.arranged`). BCD's changes: BCD's own controls (four buttons for a cluster, BCD's wide shoulders, upright rectangles for the racing pedals), View and Menu beside the overlay's top buttons, BCD's margins, the names; the left-handed layout is BCD's own controller layout turned round (Porthole's idea of a mirrored layout) |
| `app/Madeira/BCDLogThin.swift` | whole file | 35 | `app/Sources/Core/LogThinner.swift`: which lines of a big log are sent (the first 150 of each kind and one in 40 after that, the first 300 and last 400 lines, the app's own lines, a kind of tags kept whole, every 5th line that names a failure), how a line's kind is found, and the note at the top (`BCDLogThinner`). BCD's changes: the decision made one line at a time over two passes of the file instead of on the whole log in memory, BCD's tags kept whole, the note's words, the switch (Settings › Logs, off by default) |
| `app/Madeira/SwiftAmazon/AmazonManifest.swift` | whole file | 206 | `app/Sources/Amazon/AmazonManifest.swift`: the Amazon Games build manifest (the header, the LZMA-packed protobuf list read by hand, the RSA signature checked against Amazon's public key before a field is trusted) and the game's `fuel.json`. BCD's changes: the header only |
| `app/Madeira/SwiftAmazon/AmazonAuth.swift` | whole file but its network session | 158 | `app/Sources/Amazon/AmazonAuth.swift`: the sign-in (the login page's address with a PKCE challenge, the code in the address it ends on, the device registration, the refreshed token, the sign-out) and the Keychain store. BCD's changes: the sign-in page is BCD's sign-in sheet instead of a web view of its own, BCD's Keychain name and network session, no SwiftUI, the account is read by `AmazonLibrary`, the messages |
| `app/Madeira/SwiftAmazon/AmazonAPI.swift` | whole file but `AmazonProgress` | 226 | `app/Sources/Amazon/AmazonLibrary.swift` (`AmazonGame`, `AmazonAPI`: the entitlements and where a build is, the games among the entitlements) and `AmazonInstaller.swift` (the download worker: the folder name, the safe paths, the address that is renewed, the byte meter, every file fetched, checked against its SHA-256 and moved in, the program `fuel.json` names). BCD's changes: a file is fetched whole (no ranged fetcher), BCD's progress type, the record names BCD's library entry, the messages |
| `app/Madeira/SwiftAmazon/AmazonLibrary.swift` | portions | 22 | `app/Sources/Amazon/AmazonLibrary.swift` and `AmazonInstaller.swift`: reading the account's list and the queue's start, pause and end. The rest is BCD's, shaped like BCD's EA store (the switch, the library entry, Play, the pause for a game session) |

`tools/patch-winemetal-vt-interp.py` (BCD's own; Apple's VideoToolbox frame interpolation as an experiment) presents each generated picture
the way the present-fx row above describes, from a command buffer of its own that is committed before the next drawable is taken. That is
the arrangement of Porthole's patch 0189, taken as knowledge only: no code of Porthole is in that script.

`app/Madeira/AutoTuneAsk.swift` (auto-tune's "Ask before trying a fix"), `app/Madeira/BCDExplain.swift` (the in-app "What does this mean?"),
the `[benchmark]` line in `app/Madeira/BCDPerfTraceCore.swift` and the left-handed built-in touch layout in
`app/Madeira/TouchControlPresets.swift` are BCD's own code. They follow ideas of Porthole's `app/Sources/Core/AutoFix.swift` and
`AutoFixStore.swift` (a fix that waits for Apply, Undo, the next run judges it), `DocsLink.swift` (a message's own explanation),
`Telemetry.swift` (`benchmarkLine`'s fields) and `VirtualPadModel.swift` (a mirrored layout), taken as knowledge only.

### lsfg-vk 1.0.0

The order of Lossless Scaling's frame generation passes, what each reads and writes, the thread-group counts and the constant buffer in
`dxmt/src/d3d11/d3d11_lsfg.hpp` (written by `tools/patch-dxmt-lsfg.py`), and the list of shader ids in `app/Madeira/BCDLossless.swift`, follow
**lsfg-vk 1.0.0** ([PancakeTAS/lsfg-vk](https://github.com/PancakeTAS/lsfg-vk), tag `v1.0.0`, `framegen/v3.1_src`, `framegen/v3.1p_src` and
`extract.cpp`), **MIT, Copyright (c) 2025 lsfg-vk**; Porthole wrote its C++ from that layout and BCD took Porthole's. The copyright and permission
notice are in the header itself and in `LICENSES/MIT-lsfg-vk.txt`. Later versions of lsfg-vk are under other licences (GPL-3.0, then
CC BY-NC-ND 4.0) and nothing of them is used.

**Lossless Scaling itself is not in BCD.** No file, shader or other part of Lossless Scaling (THS; Steam app 993090) is in this repository or
in the app. A person who owns it lets BCD download it from Steam with their own account; BCD keeps its `Lossless.dll` encrypted on that device,
reads the frame generation's compiled shaders out of it in memory when a game with the switch on starts, and removes them from the game's prefix
when the session ends. Porthole's LS1 / LS1 performance upscaler (its patches 0200 to 0202) was not taken.

## Code from heroic-gogdl (Heroic-Games-Launcher/heroic-gogdl)

heroic-gogdl ([Heroic-Games-Launcher/heroic-gogdl](https://github.com/Heroic-Games-Launcher/heroic-gogdl)) is the GOG download module
of the Heroic Games Launcher, by Paweł Lidwin (imLinguin) and its contributors. In November 2026 (build 663) BCD took the code listed
here from it, at commit `6c6ef53f` of 2026-10-28.

**Licence position.** heroic-gogdl is licensed under the **GNU General Public License version 3** (its `LICENSE`; its `pyproject.toml`
says `GPL-3`), which BCD's GPL-3.0-only licence is the same as. The copyright of the code taken stays with its author; BCD's additional
terms under GPL-3.0 section 7 (`LICENSE-BCD.md`) do not apply to it, including where it sits in a file that also holds BCD's own text.
The file carries a "Portions copyright" line in its header naming the source and the date (GPL-3.0 section 5).

**What is not code.** The rest of BCD's generation-1 reader (the builds list, the repository and depot manifests, main.bin read by
HTTP Range through the generation-1 secure link, the MD5 checks, the support folder's `app/` files) is BCD's own code, written from
GOG's public replies and following the formats as gogdl's `gogdl/dl/managers/v1.py`, `gogdl/dl/objects/v1.py` and
`gogdl/dl/dl_utils.py` read them (knowledge only: no statement of them is copied).

| File | Taken | Lines | From heroic-gogdl (path at commit `6c6ef53f`), author, what |
|---|---|---|---|
| `app/Madeira/BCDGOG/BCDGOGContentV1.swift` | portions | 0 (22) | `gogdl/languages.py` (Paweł Lidwin, its list from commit `e0db474` of 2025-03-09; the comparison rule from `eee0bbb` and `27d06fd` of 2026-06): GOG's 84 language codes, English names and older codes (`v1Languages`, the native names left out) and the rule that a language matches by code, name or older code, letter case aside (`v1LanguageCode`). "Lines" by the rule of the Madeira section above: no line is identical (Python into Swift); the 22 table lines in brackets follow gogdl's entries one for one, counted by hand |

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

- `build/ffmpeg/src/ffmpeg-7.1.5.tar.xz` -- the unmodified upstream release
  from `ffmpeg.org`
- `build/ffmpeg/src/SHA256SUMS` -- its checksum
  (`de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f`), which
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
