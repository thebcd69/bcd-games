#!/bin/bash
# Build Wine PE DLLs that upstream's arm64ec-windows set does not ship but
# games import (and, since ml2106, xinput1_1-1_4 with host rumble; see below): older VC++ runtimes (Crysis's Bin64\Crysis64.exe needs
# msvcr80), D3DX9/10/11, d3d10, avifil32, XAudio2, dinput, RichEdit (the
# Rockstar Games SDK / Launcher installers load Msftedit.dll and gdiplus.dll), srvcli (forwarders to
# netapi32: Microsoft Flight Simulator 2024's program imports it). Configured the way
# upstream's build/wine-pe/build-ntdll.sh configures wine/build-arm64ec;
# stripped and padded by 64 KB past SizeOfImage like the shipped builtins.
# Also ucrtbase-x64math.dll, an opt-in variant of ucrtbase (see UCX below), and
# kernelbase-fibertrace.dll, an opt-in diagnostic kernelbase (see FBT below), and
# wer-optin.dll, Wine's own wer.dll for the one game that imports it (see WER below), and
# wintypes-optin.dll, Wine's own wintypes.dll for the games that import it (see WTY below), and
# setupapi-optin.dll, the shipped setupapi.dll with a lock round its device node table (see SDL below).
# Also (2026-10-22, Resident Evil 2) winedmo.dll, the demuxer DLL nothing shipped imports, and the opt-in
# mfasfsrcsnk-optin.dll, wmvdecod-optin.dll and wmadmod-optin.dll: Wine's ASF byte stream handler (built with
# tools/patch-wine-mfasfsrcsnk-winedmo.py) and the WMV and WMA decoder MFTs behind it (see OPTIN_WANT below).
# A DLL upstream already ships is never replaced. Run from the repository
# root; needs llvm-mingw on PATH (or MINGW) and the native wine tools.
#   usage: build-wine-extra-dlls.sh [native tools dir]
set -u
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
export PATH="$MINGW:$PATH"
TOOLS="${1:-}"
B="${WINE_EC_BUILD:-$R/wine/build-arm64ec}"
SHIP="${SHIP_DIR:-$R/app/Madeira/arm64ec-windows}"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc)"

WANT="msvcr70 msvcr71 msvcr80 msvcr90 msvcr100 msvcr110 msvcrt20 msvcrt40 msvcirt
      msvcp60 msvcp70 msvcp71 msvcp80 msvcp90 msvcp100 msvcp110 msvcp120
      vcomp vcomp90 vcomp100 vcomp110 vcomp120 vcomp140
      d3d10 d3d10_1 avifil32 msvfw32 dinput
      wbemprox wbemdisp wmiutils
      riched20 riched32 msftedit gdiplus mlang usp10 cabinet sspicli msxml3 msxml6
      msasn1 wldp hnetcfg msctf xmllite netprofm d2d1 wmvcore winegstreamer srvcli winedmo
      xaudio2_0 xaudio2_1 xaudio2_2 xaudio2_3 xaudio2_4 xaudio2_5 xaudio2_6 xaudio2_7 xaudio2_8 xaudio2_9
      x3daudio1_0 x3daudio1_1 x3daudio1_2 x3daudio1_3 x3daudio1_4 x3daudio1_5 x3daudio1_6 x3daudio1_7
      xapofx1_1 xapofx1_2 xapofx1_3 xapofx1_4 xapofx1_5
      d3dcompiler_33 d3dcompiler_34 d3dcompiler_35 d3dcompiler_36 d3dcompiler_37 d3dcompiler_38
      d3dcompiler_39 d3dcompiler_40 d3dcompiler_41 d3dcompiler_42 d3dcompiler_46
      d3dx10_33 d3dx10_34 d3dx10_35 d3dx10_36 d3dx10_37 d3dx10_38 d3dx10_39 d3dx10_40 d3dx10_41 d3dx10_42 d3dx10_43
      d3dx11_42 d3dx11_43"
for n in $(seq 24 42); do WANT="$WANT d3dx9_$n"; done

shipped() { ls "$SHIP" | tr 'A-Z' 'a-z' | grep -qx "$1.dll"; }
targets=""; todo=""
for d in $WANT; do
    [ -d "$R/wine/dlls/$d" ] || continue
    shipped "$d" && continue
    targets="$targets dlls/$d/arm64ec-windows/$d.dll"; todo="$todo $d"
done
# ml2106: the one exception to "never replaces a shipped DLL". xinput1_1-1_4
# (one source, dlls/xinput1_3/main.c) are rebuilt with
# tools/patch-wine-xinput-vibration.py, so XInputSetState reaches the host pad
# (docs/dualsense-output.md), and replace upstream's copies -- which were built
# from this same submodule without the patch. xinput9_1_0 forwards to
# xinput1_4 at run time. A failed build keeps the shipped copies (no rumble,
# nothing else changes). MADEIRA_XINPUT_RUMBLE_BUILD=0 skips it.
XI=""
if [ "${MADEIRA_XINPUT_RUMBLE_BUILD:-1}" != 0 ]; then
    for d in xinput1_1 xinput1_2 xinput1_3 xinput1_4; do
        [ -d "$R/wine/dlls/$d" ] || continue
        targets="$targets dlls/$d/arm64ec-windows/$d.dll"; XI="$XI $d"
    done
fi
# madeira-bcd: the second exception. windows.gaming.input is rebuilt with
# tools/patch-wine-wgi-host-pads.py, so a game reading only
# Windows.Gaming.Input.Gamepad sees the XInput pads (opt-in at run time:
# MADEIRA_WGI_HOST_PADS=1; without it the DLL behaves as the shipped one). A
# failed build keeps the shipped copy. MADEIRA_WGI_HOST_PADS_BUILD=0 skips it.
WGI=""
if [ "${MADEIRA_WGI_HOST_PADS_BUILD:-1}" != 0 ] && [ -d "$R/wine/dlls/windows.gaming.input" ]; then
    targets="$targets dlls/windows.gaming.input/arm64ec-windows/windows.gaming.input.dll"; WGI=windows.gaming.input
fi
# madeira-bcd: not an exception, a new file. ucrtbase-x64math.dll is Wine's
# ucrtbase built with tools/patch-wine-ucrtbase-x64-math.py (floor and sqrt as
# x64 code for x64 callers) and ships NEXT TO the shipped ucrtbase.dll, which
# stays; only a game with env.MADEIRA_UCRT_X64_MATH = 1 runs it. Built in a make
# of its own after the patches above are undone (see below).
# MADEIRA_UCRT_X64_MATH_BUILD=0 skips it.
UCX=""
if [ "${MADEIRA_UCRT_X64_MATH_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/ucrtbase/Makefile.in" ]; then
    UCX=ucrtbase
fi
# madeira-bcd: not an exception either, a new file. ntdll-sync.dll is Wine's ntdll
# built with tools/patch-wine-ntdll-sync.py (cheaper SRW locks: a release wakes
# only a waiter the lock word records, no shared ring; x64 bodies for the SRW
# and critical-section exports) and ships NEXT TO the shipped ntdll.dll, which stays. The unix loader
# maps it unless env.MADEIRA_NTDLL_SYNC = 0. It is built only when this tree,
# unpatched, first rebuilds the shipped ntdll.dll byte for byte apart from its
# time stamps (__FILE__ paths mapped to the ../ upstream's build/wine-pe/
# build-ntdll.sh tree has). MADEIRA_NTDLL_SYNC_BUILD=0 skips it.
NTS=""
if [ "${MADEIRA_NTDLL_SYNC_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/ntdll/sync.c" ] && [ -f "$SHIP/ntdll.dll" ]; then
    NTS=ntdll
fi
# madeira-bcd, from thebcd69/bcd-games#4 and thebcd69/bcd-wine#1 by mckenna654 (modified 2026-10-24): another new file.
# ntdll-xsec.dll is Wine's ntdll built with tools/patch-wine-ntdll-xsec.py (the emulator is told about the executable views of
# non-image sections, and two code-probe recoveries) and ships NEXT TO the shipped ntdll.dll, which stays. The unix loader maps it only
# with env.MADEIRA_NTDLL_XSEC = 1 (Diablo II: Resurrected's list). Built only when this tree, unpatched, first rebuilds the shipped
# ntdll.dll byte for byte apart from its time stamps (the same proof as for ntdll-sync.dll). MADEIRA_NTDLL_XSEC_BUILD=0 skips it.
XSE=""
if [ "${MADEIRA_NTDLL_XSEC_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/ntdll/signal_arm64ec.c" ] && [ -f "$SHIP/ntdll.dll" ]; then
    XSE=ntdll
fi
# madeira-bcd: a new file too, a diagnostic. kernelbase-fibertrace.dll is Wine's
# kernelbase built with tools/patch-wine-kernelbase-fibertrace.py (the fiber calls,
# parked fibers and overlapped reads logged as [fiber-trace] lines) and ships NEXT
# TO the shipped kernelbase.dll, which stays; only a game with
# env.MADEIRA_FIBER_TRACE = 1 runs it. Stripped like build/wine-pe/build-modules.sh
# strips the shipped one (--strip-debug). MADEIRA_FIBER_TRACE_BUILD=0 skips it.
FBT=""
if [ "${MADEIRA_FIBER_TRACE_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/kernelbase/thread.c" ] && [ -f "$SHIP/kernelbase.dll" ]; then
    FBT=kernelbase
fi
# madeira-bcd (round 624, agent E2 S3): kernelbase-optin.dll is Wine's kernelbase built from this tree as it is pinned (no script
# patches it), so the mirror's own changes to it run: dlls/kernelbase/process.c answers GetProcessMitigationPolicy with an empty
# structure under env.MADEIRA_ZERO_MITIGATION_POLICY = 1 (Glyphica: Typing Survival's ChakraCore fails fast without it). The shipped
# kernelbase.dll is a prebuilt file this script never replaces and that has none of the mirror's changes (its strings hold no
# "MADEIRA_"; Glyphica failed the same way with the key set: 7611AG, build 600; J9JDBG, build 623). The variant ships NEXT TO it and
# only a game whose file lists kernelbase in env.MADEIRA_OPTIN_DLLS runs it (WineProcessBridge.m's opt-in loop links it as
# kernelbase.dll for that session; the next session's farm relink restores the shipped one). MADEIRA_ZMP_BUILD=0 skips it.
ZMP=""
if [ "${MADEIRA_ZMP_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/kernelbase/process.c" ] && [ -f "$SHIP/kernelbase.dll" ]; then
    ZMP=kernelbase
fi
# madeira-bcd: a new file too. wer-optin.dll is Wine's own, unpatched wer.dll (Hello Neighbor's
# UE4 executable imports it and exited at once with 0xc0000135, a tester's log, build 573). It is
# NOT shipped as wer.dll: Wine's wer.dll has 68 unimplemented exports, so a program that loads
# wer.dll optionally today could be aborted by one of them. Only a game with env.MADEIRA_WER = 1
# gets it, linked as wer.dll for that session (app/Madeira/WineProcessBridge.m; auto-tune sets the
# line itself after a "Library wer.dll ... not found" failure). MADEIRA_WER_BUILD=0 skips it.
WER=""
if [ "${MADEIRA_WER_BUILD:-1}" != 0 ] && [ -d "$R/wine/dlls/wer" ] && ! shipped wer; then
    targets="$targets dlls/wer/arm64ec-windows/wer.dll"; WER=wer
fi
# madeira-bcd: wintypes-optin.dll, the same way. Unity IL2CPP games' GameAssembly.dll imports
# api-ms-win-core-winrt-robuffer-l1-1-0.dll, which Wine's apiset maps to wintypes.dll; the 64-bit
# set ships none, so Megabonk, No, I'm not a Human and Over the Hill ended at once with c0000135
# (testers' logs J7VAJV, TZB1JX, HXQQN1, build 574). Not shipped as wintypes.dll: Chromium (Steam's
# web helper) loads wintypes.dll optionally today and works without it. Only a game with
# env.MADEIRA_WINTYPES = 1 gets it, linked as wintypes.dll for that session. MADEIRA_WTY_BUILD=0 skips it.
WTY=""
if [ "${MADEIRA_WTY_BUILD:-1}" != 0 ] && [ -d "$R/wine/dlls/wintypes" ] && ! shipped wintypes; then
    targets="$targets dlls/wintypes/arm64ec-windows/wintypes.dll"; WTY=wintypes
fi
# madeira-bcd: more DLLs a game's program imports that the set lacks, each shipped as <name>-optin.dll
# and linked as <name>.dll only for a game whose file lists it in env.MADEIRA_OPTIN_DLLS (comma or
# semicolon separated; app/Madeira/WineProcessBridge.m), so no program that loads one optionally today
# sees a change. dxva2: Fort Solis and DRAGON BALL Sparking! ZERO import it and ended at once with
# c0000135 (testers' logs 94MSWJ, NZ3ANE, H6XVPV, build 574); evr: Spider-Man: Miles Morales'
# crs-video.exe (PD7S1E); xpsprint: Animal Well (NWHG8J, build 565); sfc, sfc_os and tbs: Diablo II: Resurrected's
# D2R_loader.dll imports sfc.dll and tbs.dll and ended with c0000135 (bcd-games issue #3, build 573; sfc.dll forwards
# to sfc_os.dll); twinapi.appcore: Senua's Saga: Hellblade II's XCurl.dll and Microsoft.Xbox.Services.GDK.C.Thunks.dll
# import api-ms-win-core-psm-appnotify-l1-1-0.dll, which Wine's apiset maps to twinapi.appcore.dll, and the program ended
# with c0000135 (tester's log XJ7Z6Y, build 576; Wine's DLL has RegisterAppStateChangeNotification and its three
# neighbours, each a stub that returns ERROR_CALL_NOT_IMPLEMENTED); mfasfsrcsnk, wmvdecod and wmadmod: Resident Evil 2
# plays WMV cutscenes through a Media Foundation source reader and ended at its first one (c0000135 for
# C:\windows\system32\mfasfsrcsnk.dll, CLSID {41457294-644c-4298-a28a-bd69f2c0cf3b}, tester's log re2-ipadm4-579, build
# 579): mfasfsrcsnk is Wine's ASF byte stream handler (built with tools/patch-wine-mfasfsrcsnk-winedmo.py, which makes it
# use the FFmpeg demuxer in winedmo.dll, built above as a plain DLL because nothing shipped imports it), wmvdecod the WMV
# decoder MFT and wmadmod the WMA decoder MFT the reader finds in the registry; both decoders run on BCD's winegstreamer
# unix side, which a 64-bit game gets with env.MADEIRA_WG_64BIT = 1 (build/ntdll-unix/winegstreamer_wmv_ios.c).
# The ByteStreamHandlers entries for .wmv and .asf and the CLSIDs are already in the prefix template. MADEIRA_OPTIN_BUILD=0 skips them.
# oledlg, fwpuclnt and sensapi (2026-10-23, testers' logs of build 580): Digimon Story Cyber Sleuth's exe imports oledlg.dll
# (PGET5A), FINAL FANTASY XV's ffxv_s.exe imports fwpuclnt.dll (DM9SAF), Borderlands 3's Vivox SDK (vivoxsdk.dll) imports
# SensApi.dll (VFM84T); each ended at once with c0000135 (Borderlands 3 with UE4's delay-load exception). The 32-bit set has
# all three; the 64-bit one had none. oledlg imports ole32, comdlg32, user32 and advapi32, all shipped; fwpuclnt and sensapi
# import nothing beyond the kernel. A program that loads one of them optionally today keeps running without it, hence opt-in.
# ddraw (2026-10-23, the owner's Dying Light log 17:27:59, build 593): its renderer rd3d11_x64_rwdi.dll imports DDRAW.dll, which the
# 64-bit set lacks; the game said "Cannot load 'rd3d11_x64_rwdi.dll'" and "FATAL ERROR Cannot initialize renderer". Wine's ddraw imports
# wined3d, user32, gdi32 and advapi32, all shipped.
# msi, mspatcha, odbccp32, sxs and rstrtmgr (2026-10-23, the owner's EA app install on build 594, log 19:20:11): the MSI package
# installed, then its StartServices action started EABackgroundService.exe, a 64-bit service that imports msi.dll and
# RstrtMgr.dll; the 64-bit set has neither, the loader stopped with c0000135, services.exe answered 1053 and the MSI rolled back
# (1627, "INST-14-1627"). msi.dll in turn imports mspatcha, odbccp32 and sxs, the three others the 64-bit set lacks; all
# their own imports ship. Only the EA app's launcher entry names them.
# gamingtcui (2026-10-28, round 620, Forza Horizon 4, testers' logs YFK3A6/TNE8NW/5TZDYZ/SJT15P, build 614): ForzaHorizon4.exe
# imports api-ms-win-gaming-tcui-l1-1-0.dll, which Wine's API set schema maps to gamingtcui.dll; only the 32-bit set has it, so
# the 64-bit loader ended with c0000135 ("[dll-missing] #1 L"gamingtcui.dll""). Wine's gamingtcui imports nothing beyond the kernel.
# qwave (2026-10-28, round 623, Bloons TD 6, tester's log 4EJFKD, build 622): the Unity game's Plugins\x86_64\discord_partner_sdk.dll (the
# Discord Social SDK) imports qwave.dll, Windows' quality-of-service API; the 64-bit set lacks it, the plugin cannot load, and the IL2CPP
# game ends with "UnhandledExceptionHandler caught: Unable to load DLL 'discord_partner_sdk'" 16 s into the start. A Mono Unity game
# (Mycopunk, BP35FG/NEQQN0) logs the same import failure and goes on, so it is opt-in. Wine's qwave imports nothing beyond the kernel.
OPTIN_WANT="dxva2 evr xpsprint sfc sfc_os tbs twinapi.appcore mfasfsrcsnk wmvdecod wmadmod oledlg fwpuclnt sensapi ddraw msi mspatcha odbccp32 sxs rstrtmgr glu32 tdh gamingtcui qwave"
OPT=""
if [ "${MADEIRA_OPTIN_BUILD:-1}" != 0 ]; then
    for d in $OPTIN_WANT; do
        [ -d "$R/wine/dlls/$d" ] || continue
        shipped "$d" && continue
        targets="$targets dlls/$d/arm64ec-windows/$d.dll"; OPT="$OPT $d"
    done
fi
# madeira-bcd: setupapi-optin.dll, a patched copy of a DLL that IS shipped, so it cannot use the OPT list
# above (which skips shipped names). Wine's setupapi has no lock round its device node table and
# publishes the table's size before the table: Dying Light's libpad.dll and the game's main thread listed
# devices at the same moment and one read a NULL table (setupapi.dll+0x132bc, log 1c807435, build 576).
# tools/patch-wine-setupapi-devinst-lock.py adds the lock; the result ships NEXT TO the shipped
# setupapi.dll, which stays byte for byte, and only a game with env.MADEIRA_OPTIN_DLLS = setupapi runs it
# (app/Madeira/WineProcessBridge.m links it as setupapi.dll for that session; the next session's farm
# relink restores the shipped one). Built in a make of its own after the patches above are undone (see
# below). MADEIRA_SETUPAPI_BUILD=0 skips it.
SDL=""
if [ "${MADEIRA_SETUPAPI_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/setupapi/devinst.c" ] && [ -f "$SHIP/setupapi.dll" ]; then
    SDL=setupapi
fi
# madeira-bcd (round 624, agent E2 S2b): dcomp-optin.dll, a patched copy of a DLL that IS shipped (the shipped dcomp.dll is Wine's
# stub: every DCompositionCreateDevice answers E_NOTIMPL). tools/patch-wine-dcomp-null.py gives DCompositionCreateDevice a device
# whose target and visual accept every call and draw nothing: Slay the Spire 2's Godot 4.5 hands its swap chain (served on the
# game's window by dxgi-src.dll under env.MADEIRA_DXGI_COMPOSITION = 1) to a composition target and stops when that fails. Only
# a game with env.MADEIRA_OPTIN_DLLS = dcomp gets it (WineProcessBridge.m's opt-in loop). MADEIRA_DCOMP_BUILD=0 skips it.
DCO=""
if [ "${MADEIRA_DCOMP_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/dcomp/device.c" ] && [ -f "$SHIP/dcomp.dll" ]; then
    DCO=dcomp
fi
[ -n "$todo$XI$WGI$UCX$NTS$XSE$FBT$ZMP$WER$WTY$OPT$SDL$DCO" ] || { echo "nothing to build"; exit 0; }

# The cached tree may have disabled winegstreamer because macOS has no
# GStreamer headers. Only its real PE frontend is needed here; the iOS unix
# backend already lives in libntdll_unix.a. Reconfigure that older cache too.
if [ ! -f "$B/Makefile" ] ||
   ! grep -q '^dlls/winegstreamer/arm64ec-windows/winegstreamer.dll:' "$B/Makefile"; then
    mkdir -p "$B"
    ( cd "$B" && "$R/wine/configure" --enable-archs=arm64ec --enable-winegstreamer --without-x --disable-tests \
          --without-freetype --without-gnutls ${TOOLS:+--with-wine-tools="$TOOLS"} ) > "$B.cfg.log" 2>&1 \
        || { tail -20 "$B.cfg.log"; echo "::error::wine arm64ec configure failed"; exit 1; }
fi
# widl looks for imported typelibs (stdole2.tlb) under aarch64-windows, its
# arch dir for ARM64EC, but an arm64ec-only tree builds them under
# arm64ec-windows; without this riched20, hnetcfg and wbemdisp fail with
# "cannot find stdole2.tlb".
mkdir -p "$B/dlls/stdole2.tlb" && ln -sfn arm64ec-windows "$B/dlls/stdole2.tlb/aarch64-windows"
# msvcr*: mirror the data exports into the PE mapping (see the script).
python3 "$R/tools/patch-wine-msvcrt-datasync.py" "$R/wine/dlls/msvcrt/main.c"
# d2d1: DC render targets without IDXGISurface1 (DXMT), see the script.
case " $todo " in *" d2d1 "*) python3 "$R/tools/patch-wine-d2d1-dc-readback.py" "$R/wine/dlls/d2d1/dc_render_target.c" ;; esac
xi_patched=0
if [ -n "$XI" ]; then
    python3 "$R/tools/patch-wine-xinput-vibration.py" "$R/wine/dlls/xinput1_3/main.c" && xi_patched=1
    # Old objects from an unpatched build must not satisfy make.
    for d in $XI; do rm -f "$B/dlls/$d/arm64ec-windows/$d.dll" "$B/dlls/$d"/arm64ec-windows/*.o; done
fi
wgi_patched=0
if [ -n "$WGI" ]; then
    python3 "$R/tools/patch-wine-wgi-host-pads.py" "$R/wine/dlls/windows.gaming.input" && wgi_patched=1
    rm -f "$B/dlls/windows.gaming.input/arm64ec-windows/windows.gaming.input.dll" \
          "$B/dlls/windows.gaming.input"/arm64ec-windows/*.o
fi
# Delay imports become plain imports. lld's ARM64EC delay-load stub is x64 code
# inside .text (`lea rax, __imp_aux_X; jmp __tailMerge`), and the pool copy
# Madeira runs ARM64EC code from relocates the delay IAT to the stub's POOL
# address, so the first call executes x64 code outside the emulator's
# executable ranges: NoExec, then an unhandled c0000005 (Rockstar Games
# Launcher in d2d1's DWriteCreateFactory stub, 2026-10-03 19:42 log). Only
# when every delayed DLL ships or is built here; otherwise the module is left
# as it is.
undelayed=""
for d in $todo $OPT; do
    mk="$R/wine/dlls/$d/Makefile.in"
    delayed="$(sed -n 's/^DELAYIMPORTS[[:space:]]*=[[:space:]]*//p' "$mk")"
    [ -n "$delayed" ] && grep -q "^IMPORTS" "$mk" || continue
    ok=1
    for i in $delayed; do
        shipped "$i" || case " $todo $OPT " in *" $i "*) ;; *) ok=0 ;; esac
    done
    [ "$ok" = 1 ] || continue
    sed -i.bak -e '/^DELAYIMPORTS[[:space:]]*=/d' -e "s/^\(IMPORTS[[:space:]]*=.*\)\$/\1 $delayed/" "$mk" && rm -f "$mk.bak"
    rm -f "$B/dlls/$d/arm64ec-windows/$d.dll"
    undelayed="$undelayed $d"
done
# mfasfsrcsnk-optin.dll: Wine's ASF byte stream handler, patched to use winedmo (see the script). A patch that does not
# apply drops the DLL (an unpatched one would hand the game to the generic handler, which cannot read ASF here).
MFA=""
case " $OPT " in *" mfasfsrcsnk "*) MFA=mfasfsrcsnk ;; esac
if [ -n "$MFA" ]; then
    if python3 "$R/tools/patch-wine-mfasfsrcsnk-winedmo.py" "$R/wine"; then
        rm -f "$B/dlls/mfasfsrcsnk/arm64ec-windows/mfasfsrcsnk.dll" "$B"/dlls/mfasfsrcsnk/arm64ec-windows/*.o
    else
        echo "::warning::mfasfsrcsnk winedmo patch did not apply; mfasfsrcsnk-optin.dll not shipped"
        targets="${targets/ dlls\/mfasfsrcsnk\/arm64ec-windows\/mfasfsrcsnk.dll/}"
        OPT="${OPT/ mfasfsrcsnk/}"
        MFA=""
    fi
fi
[ -n "$targets" ] && make -C "$B" -k -j"$JOBS" $targets > "$B.build.log" 2>&1
git -C "$R/wine" checkout -- dlls/msvcrt/main.c dlls/xinput1_3/main.c dlls/d2d1/dc_render_target.c \
    dlls/windows.gaming.input/provider.c dlls/windows.gaming.input/main.c
for d in $undelayed; do git -C "$R/wine" checkout -- "dlls/$d/Makefile.in"; done
[ -n "$MFA" ] && git -C "$R/wine" checkout -- dlls/mfsrcsnk/media_source.c
[ -n "$undelayed" ] && echo "::notice::delay imports linked as plain imports:$undelayed"
# madeira-bcd: ucrtbase-x64math.dll (see UCX above), from the restored tree:
# msvcrt/main.c no longer carries the data-sync patch, so the DLL differs from a
# plain ucrtbase build only by dlls/ucrtbase/x64math.c. Its delay imports stay,
# as in the shipped ucrtbase.dll. The four exports are checked to be x64 bodies
# before anything ships; the tree is restored afterwards.
if [ -n "$UCX" ]; then
    uc="$B/dlls/ucrtbase/arm64ec-windows/ucrtbase.dll"
    if python3 "$R/tools/patch-wine-ucrtbase-x64-math.py" "$R/wine"; then
        rm -f "$uc" "$B/dlls/ucrtbase/arm64ec-windows/x64math.o"
        make -C "$B" -j"$JOBS" dlls/ucrtbase/arm64ec-windows/ucrtbase.dll > "$B.ucrt.log" 2>&1
        if [ -f "$uc" ] && python3 "$R/tools/patch-wine-ucrtbase-x64-math.py" --verify "$uc"; then
            cp "$uc" "$SHIP/ucrtbase-x64math.dll.tmp"
            "$MINGW/llvm-strip" "$SHIP/ucrtbase-x64math.dll.tmp"
            python3 - "$SHIP/ucrtbase-x64math.dll.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
            mv "$SHIP/ucrtbase-x64math.dll.tmp" "$SHIP/ucrtbase-x64math.dll"
            echo "::notice::ucrtbase-x64math.dll (floor and sqrt as x64 code; env.MADEIRA_UCRT_X64_MATH = 1): built next to the shipped ucrtbase.dll"
        else
            echo "::warning::ucrtbase-x64math.dll did not build or failed its check; not shipped"
            grep -m 10 "error" "$B.ucrt.log"
        fi
        rm -f "$uc"
    else
        echo "::warning::ucrtbase x64-math patch did not apply; ucrtbase-x64math.dll not shipped"
    fi
    git -C "$R/wine" checkout -- dlls/ucrtbase/Makefile.in
    rm -f "$R/wine/dlls/ucrtbase/x64math.c"
fi
# madeira-bcd: kernelbase-fibertrace.dll (see FBT above), from the restored tree.
# Verified to carry the trace and the shipped kernelbase.dll's exports before it
# ships; the tree and the patched objects are restored afterwards
# (wine/build-arm64ec is cached).
if [ -n "$FBT" ]; then
    kb="$B/dlls/kernelbase/arm64ec-windows/kernelbase.dll"
    kbsrc="dlls/kernelbase/kernelbase.h dlls/kernelbase/thread.c dlls/kernelbase/debug.c dlls/kernelbase/file.c dlls/kernelbase/sync.c"
    if python3 "$R/tools/patch-wine-kernelbase-fibertrace.py" "$R/wine"; then
        rm -f "$kb" "$B"/dlls/kernelbase/arm64ec-windows/*.o
        make -C "$B" -j"$JOBS" dlls/kernelbase/arm64ec-windows/kernelbase.dll > "$B.kernelbase.log" 2>&1
        if [ -f "$kb" ] && cp "$kb" "$SHIP/kernelbase-fibertrace.dll.tmp" &&
           "$MINGW/arm64ec-w64-mingw32-strip" --strip-debug "$SHIP/kernelbase-fibertrace.dll.tmp" &&
           python3 "$R/tools/patch-wine-kernelbase-fibertrace.py" --verify "$SHIP/kernelbase-fibertrace.dll.tmp" "$SHIP/kernelbase.dll"; then
            mv "$SHIP/kernelbase-fibertrace.dll.tmp" "$SHIP/kernelbase-fibertrace.dll"
            echo "::notice::kernelbase-fibertrace.dll (fiber calls logged as [fiber-trace]; env.MADEIRA_FIBER_TRACE = 1): built next to the shipped kernelbase.dll"
        else
            rm -f "$SHIP/kernelbase-fibertrace.dll.tmp"
            echo "::warning::kernelbase-fibertrace.dll did not build or failed its check; not shipped (env.MADEIRA_FIBER_TRACE = 1 does nothing)"
            grep -m 10 "error" "$B.kernelbase.log"
        fi
    else
        echo "::warning::kernelbase fiber-trace patch did not apply; kernelbase-fibertrace.dll not shipped"
    fi
    # shellcheck disable=SC2086 # the list is five fixed paths
    git -C "$R/wine" checkout -- $kbsrc
    rm -f "$kb" "$B"/dlls/kernelbase/arm64ec-windows/*.o
fi
# madeira-bcd: kernelbase-optin.dll (see ZMP above), from the restored tree (the fiber-trace block above put its five files back).
# Only from a kernelbase the pin left as it is; verified to carry the switch's UTF-16 name and the shipped kernelbase.dll's exports
# before it ships (tools/verify-kernelbase-optin.py). Its objects are removed afterwards like the fiber-trace block's.
if [ -n "$ZMP" ]; then
    kb="$B/dlls/kernelbase/arm64ec-windows/kernelbase.dll"
    if git -C "$R/wine" diff --quiet -- dlls/kernelbase; then
        rm -f "$kb" "$B"/dlls/kernelbase/arm64ec-windows/*.o
        make -C "$B" -j"$JOBS" dlls/kernelbase/arm64ec-windows/kernelbase.dll > "$B.kernelbase-optin.log" 2>&1
        if [ -f "$kb" ] && cp "$kb" "$SHIP/kernelbase-optin.dll.tmp" &&
           "$MINGW/arm64ec-w64-mingw32-strip" --strip-debug "$SHIP/kernelbase-optin.dll.tmp" &&
           python3 "$R/tools/verify-kernelbase-optin.py" "$SHIP/kernelbase-optin.dll.tmp" "$SHIP/kernelbase.dll"; then
            mv "$SHIP/kernelbase-optin.dll.tmp" "$SHIP/kernelbase-optin.dll"
            echo "::notice::kernelbase-optin.dll (the tree's kernelbase; env.MADEIRA_OPTIN_DLLS = kernelbase): built next to the shipped kernelbase.dll"
        else
            rm -f "$SHIP/kernelbase-optin.dll.tmp"
            echo "::warning::kernelbase-optin.dll did not build or failed its check; not shipped (env.MADEIRA_OPTIN_DLLS = kernelbase does nothing)"
            grep -m 10 "error" "$B.kernelbase-optin.log"
        fi
    else
        echo "::warning::wine/dlls/kernelbase differs from the pin; kernelbase-optin.dll not built"
    fi
    rm -f "$kb" "$B"/dlls/kernelbase/arm64ec-windows/*.o
fi
# madeira-bcd: setupapi-optin.dll (see SDL above), from the restored tree. Stripped the way
# build/wine-pe/build-modules.sh strips the shipped one (--strip-debug), padded to at least SizeOfImage
# + 0x10000 like the other extra DLLs, and verified (the lock's marker, the shipped setupapi.dll's
# exports) before it ships. Its delay imports stay, as in the shipped setupapi.dll. The tree and the
# patched objects are restored afterwards (wine/build-arm64ec is cached).
if [ -n "$SDL" ]; then
    sa="$B/dlls/setupapi/arm64ec-windows/setupapi.dll"
    sdl_tmp="$SHIP/setupapi-optin.dll.tmp"
    if python3 "$R/tools/patch-wine-setupapi-devinst-lock.py" "$R/wine"; then
        rm -f "$sa" "$B"/dlls/setupapi/arm64ec-windows/*.o
        make -C "$B" -j"$JOBS" dlls/setupapi/arm64ec-windows/setupapi.dll > "$B.setupapi.log" 2>&1
        if [ -f "$sa" ] && cp "$sa" "$sdl_tmp" && "$MINGW/arm64ec-w64-mingw32-strip" --strip-debug "$sdl_tmp"; then
            python3 - "$sdl_tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
            if python3 "$R/tools/patch-wine-setupapi-devinst-lock.py" --verify "$sdl_tmp" "$SHIP/setupapi.dll"; then
                mv "$sdl_tmp" "$SHIP/setupapi-optin.dll"
                echo "::notice::setupapi-optin.dll (the shipped setupapi.dll with a lock round the device node table; linked as setupapi.dll only for a game with setupapi in env.MADEIRA_OPTIN_DLLS): built next to the shipped setupapi.dll"
            else
                rm -f "$sdl_tmp"
                echo "::warning::setupapi-optin.dll failed its check; not shipped (env.MADEIRA_OPTIN_DLLS = setupapi does nothing)"
            fi
        else
            rm -f "$sdl_tmp"
            echo "::warning::setupapi-optin.dll did not build; env.MADEIRA_OPTIN_DLLS = setupapi does nothing"
            grep -m 10 "error" "$B.setupapi.log"
        fi
    else
        echo "::warning::setupapi devinst-lock patch did not apply; setupapi-optin.dll not shipped"
    fi
    git -C "$R/wine" checkout -- dlls/setupapi/devinst.c
    rm -f "$sa" "$B"/dlls/setupapi/arm64ec-windows/*.o
fi
# madeira-bcd: dcomp-optin.dll (see DCO above), from the restored tree. The patched device.c includes dcomp.h, which widl makes from
# include/dcomp.idl (and its two imports) in a make of its own first: the object's dependencies were computed from the stub, so a
# parallel make would not wait for them. Stripped and padded like setupapi-optin.dll, verified (the null compositor's marker, the
# shipped dcomp.dll's exports) before it ships; the tree and the objects are restored afterwards.
if [ -n "$DCO" ]; then
    dc="$B/dlls/dcomp/arm64ec-windows/dcomp.dll"
    dc_tmp="$SHIP/dcomp-optin.dll.tmp"
    if python3 "$R/tools/patch-wine-dcomp-null.py" "$R/wine"; then
        rm -f "$dc" "$B"/dlls/dcomp/arm64ec-windows/*.o
        make -C "$B" include/dcomp.h include/dcompanimation.h include/dcomptypes.h > "$B.dcomp.log" 2>&1
        make -C "$B" -j"$JOBS" dlls/dcomp/arm64ec-windows/dcomp.dll >> "$B.dcomp.log" 2>&1
        if [ -f "$dc" ] && cp "$dc" "$dc_tmp" && "$MINGW/arm64ec-w64-mingw32-strip" --strip-debug "$dc_tmp"; then
            python3 - "$dc_tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
            if python3 "$R/tools/patch-wine-dcomp-null.py" --verify "$dc_tmp" "$SHIP/dcomp.dll"; then
                mv "$dc_tmp" "$SHIP/dcomp-optin.dll"
                echo "::notice::dcomp-optin.dll (a DirectComposition device that draws nothing; linked as dcomp.dll only for a game with dcomp in env.MADEIRA_OPTIN_DLLS): built next to the shipped dcomp.dll"
            else
                rm -f "$dc_tmp"
                echo "::warning::dcomp-optin.dll failed its check; not shipped (env.MADEIRA_OPTIN_DLLS = dcomp does nothing)"
            fi
        else
            rm -f "$dc_tmp"
            echo "::warning::dcomp-optin.dll did not build; env.MADEIRA_OPTIN_DLLS = dcomp does nothing"
            grep -m 10 "error" "$B.dcomp.log"
        fi
    else
        echo "::warning::the dcomp null-compositor patch did not apply; dcomp-optin.dll not shipped"
    fi
    git -C "$R/wine" checkout -- dlls/dcomp/device.c
    rm -f "$dc" "$B"/dlls/dcomp/arm64ec-windows/*.o
fi
# madeira-bcd: ntdll-sync.dll (see NTS above), from the restored tree. First the
# unpatched ntdll.dll, which must equal the shipped one apart from its time
# stamps; then the patched one, verified before it ships. The tree and the
# ntdll objects are restored afterwards (wine/build-arm64ec is cached).
if [ -n "$NTS" ]; then
    nt="$B/dlls/ntdll/arm64ec-windows/ntdll.dll"
    ntflags="-g -O2"
    ntsrc="$(sed -n 's/^srcdir = //p' "$B/Makefile")"
    case "$ntsrc" in /*) ntflags="$ntflags -ffile-prefix-map=$ntsrc=.. -fno-file-reproducible" ;; esac
    ntdll_make() {
        rm -f "$nt" "$B"/dlls/ntdll/arm64ec-windows/*.o
        make -C "$B" -j"$JOBS" arm64ec_CFLAGS="$ntflags" x86_64_CFLAGS="$ntflags" \
            dlls/ntdll/arm64ec-windows/ntdll.dll >> "$B.ntdll.log" 2>&1 && [ -f "$nt" ]
    }
    # stripped and padded to SizeOfImage + 0x50000, as build/wine-pe/build-ntdll.sh does
    ntdll_ship() {
        cp "$nt" "$1" && "$MINGW/arm64ec-w64-mingw32-strip" "$1" && python3 - "$1" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x50000
if len(d) > target:
    raise SystemExit("ntdll is larger than SizeOfImage + 0x50000")
open(p, 'ab').write(b'\0' * (target - len(d)))
PY
    }
    : > "$B.ntdll.log"
    if ntdll_make && ntdll_ship "$B/ntdll-stock.dll" &&
       python3 "$R/tools/patch-wine-ntdll-sync.py" --same-as "$B/ntdll-stock.dll" "$SHIP/ntdll.dll"; then
        if python3 "$R/tools/patch-wine-ntdll-sync.py" "$R/wine" && ntdll_make &&
           ntdll_ship "$SHIP/ntdll-sync.dll.tmp" &&
           python3 "$R/tools/patch-wine-ntdll-sync.py" --verify "$SHIP/ntdll-sync.dll.tmp" "$SHIP/ntdll.dll"; then
            mv "$SHIP/ntdll-sync.dll.tmp" "$SHIP/ntdll-sync.dll"
            echo "::notice::ntdll-sync.dll (cheaper SRW locks and critical sections; opt-in, mapped only with env.MADEIRA_NTDLL_SYNC = 1 or native): built next to the shipped ntdll.dll"
        else
            rm -f "$SHIP/ntdll-sync.dll.tmp"
            echo "::warning::ntdll-sync.dll did not build or failed its check; not shipped (games map the shipped ntdll.dll)"
            grep -m 10 "error" "$B.ntdll.log"
        fi
    else
        echo "::warning::this tree does not rebuild the shipped ntdll.dll apart from time stamps; ntdll-sync.dll not shipped"
        grep -m 10 "error" "$B.ntdll.log"
    fi
    git -C "$R/wine" checkout -- dlls/ntdll/sync.c dlls/ntdll/ntdll.spec dlls/ntdll/Makefile.in
    rm -f "$R/wine/dlls/ntdll/sync_x64.c" "$nt" "$B"/dlls/ntdll/arm64ec-windows/*.o "$B/ntdll-stock.dll"
fi
# madeira-bcd, from thebcd69/bcd-games#4 and thebcd69/bcd-wine#1 by mckenna654 (modified 2026-10-24): ntdll-xsec.dll (see XSE above),
# from the restored tree, the same way as ntdll-sync.dll: first the unpatched ntdll.dll, which must equal the shipped one apart from its
# time stamps (the check is patch-wine-ntdll-sync.py's --same-as); then the patched one, verified before it ships (marker, log strings,
# the stock export set, room for the RtlPcToFileHeader trampoline). The tree and the ntdll objects are restored afterwards
# (wine/build-arm64ec is cached). A failure ships nothing: env.MADEIRA_NTDLL_XSEC = 1 then maps the shipped ntdll.dll.
if [ -n "$XSE" ]; then
    xnt="$B/dlls/ntdll/arm64ec-windows/ntdll.dll"
    xflags="-g -O2"
    xsrc="$(sed -n 's/^srcdir = //p' "$B/Makefile")"
    case "$xsrc" in /*) xflags="$xflags -ffile-prefix-map=$xsrc=.. -fno-file-reproducible" ;; esac
    xse_make() {
        rm -f "$xnt" "$B"/dlls/ntdll/arm64ec-windows/*.o
        make -C "$B" -j"$JOBS" arm64ec_CFLAGS="$xflags" x86_64_CFLAGS="$xflags" \
            dlls/ntdll/arm64ec-windows/ntdll.dll >> "$B.ntdll-xsec.log" 2>&1 && [ -f "$xnt" ]
    }
    # stripped and padded to SizeOfImage + 0x50000, as build/wine-pe/build-ntdll.sh does
    xse_ship() {
        cp "$xnt" "$1" && "$MINGW/arm64ec-w64-mingw32-strip" "$1" && python3 - "$1" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x50000
if len(d) > target:
    raise SystemExit("ntdll is larger than SizeOfImage + 0x50000")
open(p, 'ab').write(b'\0' * (target - len(d)))
PY
    }
    : > "$B.ntdll-xsec.log"
    if xse_make && xse_ship "$B/ntdll-xsec-stock.dll" &&
       python3 "$R/tools/patch-wine-ntdll-sync.py" --same-as "$B/ntdll-xsec-stock.dll" "$SHIP/ntdll.dll"; then
        if python3 "$R/tools/patch-wine-ntdll-xsec.py" "$R/wine" && xse_make &&
           xse_ship "$SHIP/ntdll-xsec.dll.tmp" &&
           python3 "$R/tools/patch-wine-ntdll-xsec.py" --verify "$SHIP/ntdll-xsec.dll.tmp" "$SHIP/ntdll.dll"; then
            mv "$SHIP/ntdll-xsec.dll.tmp" "$SHIP/ntdll-xsec.dll"
            echo "::notice::ntdll-xsec.dll (executable views of non-image sections told to the emulator, two code-probe recoveries; opt-in, mapped only with env.MADEIRA_NTDLL_XSEC = 1): built next to the shipped ntdll.dll"
        else
            rm -f "$SHIP/ntdll-xsec.dll.tmp"
            echo "::warning::ntdll-xsec.dll did not build or failed its check; not shipped (env.MADEIRA_NTDLL_XSEC = 1 maps the shipped ntdll.dll)"
            grep -m 10 "error" "$B.ntdll-xsec.log"
        fi
    else
        echo "::warning::this tree does not rebuild the shipped ntdll.dll apart from time stamps; ntdll-xsec.dll not shipped"
        grep -m 10 "error" "$B.ntdll-xsec.log"
    fi
    git -C "$R/wine" checkout -- dlls/ntdll/signal_arm64ec.c
    rm -f "$xnt" "$B"/dlls/ntdll/arm64ec-windows/*.o "$B/ntdll-xsec-stock.dll"
fi
xi_built=0; xi_failed=""
if [ "$xi_patched" = 1 ]; then
    for d in $XI; do
        f="$B/dlls/$d/arm64ec-windows/$d.dll"
        if [ ! -f "$f" ]; then xi_failed="$xi_failed $d"; continue; fi
        cp "$f" "$SHIP/$d.dll.tmp"
        "$MINGW/llvm-strip" "$SHIP/$d.dll.tmp"
        python3 - "$SHIP/$d.dll.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
        mv "$SHIP/$d.dll.tmp" "$SHIP/$d.dll"
        xi_built=$((xi_built + 1))
    done
    echo "::notice::xinput with host rumble (ml2106): replaced $xi_built shipped DLLs${xi_failed:+ (failed, shipped copy kept:$xi_failed)}"
elif [ -n "$XI" ]; then
    echo "::warning::xinput rumble patch did not apply; shipped xinput DLLs kept"
fi
if [ "$wgi_patched" = 1 ]; then
    f="$B/dlls/windows.gaming.input/arm64ec-windows/windows.gaming.input.dll"
    if [ -f "$f" ]; then
        cp "$f" "$SHIP/windows.gaming.input.dll.tmp"
        "$MINGW/llvm-strip" "$SHIP/windows.gaming.input.dll.tmp"
        python3 - "$SHIP/windows.gaming.input.dll.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
        mv "$SHIP/windows.gaming.input.dll.tmp" "$SHIP/windows.gaming.input.dll"
        echo "::notice::windows.gaming.input with XInput pads as gamepads (MADEIRA_WGI_HOST_PADS=1): replaced the shipped DLL"
    else
        echo "::warning::windows.gaming.input with XInput pads did not build; shipped DLL kept"
    fi
elif [ -n "$WGI" ]; then
    echo "::warning::windows.gaming.input host-pad patch did not apply; shipped DLL kept"
fi
built=0; failed=""
for d in $todo; do
    f="$B/dlls/$d/arm64ec-windows/$d.dll"
    if [ ! -f "$f" ]; then failed="$failed $d"; continue; fi
    cp "$f" "$SHIP/$d.dll.tmp"
    "$MINGW/llvm-strip" "$SHIP/$d.dll.tmp"
    python3 - "$SHIP/$d.dll.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
    mv "$SHIP/$d.dll.tmp" "$SHIP/$d.dll"
    built=$((built + 1))
done
if [ -n "$WER" ]; then
    f="$B/dlls/wer/arm64ec-windows/wer.dll"
    if [ -f "$f" ]; then
        cp "$f" "$SHIP/wer-optin.dll.tmp"
        "$MINGW/llvm-strip" "$SHIP/wer-optin.dll.tmp"
        python3 - "$SHIP/wer-optin.dll.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
        mv "$SHIP/wer-optin.dll.tmp" "$SHIP/wer-optin.dll"
        echo "::notice::wer-optin.dll (Wine's wer.dll, linked as wer.dll only with env.MADEIRA_WER = 1): built; no wer.dll is shipped"
    else
        echo "::warning::wer-optin.dll did not build; env.MADEIRA_WER = 1 does nothing"
    fi
fi
if [ -n "$WTY" ]; then
    f="$B/dlls/wintypes/arm64ec-windows/wintypes.dll"
    if [ -f "$f" ]; then
        cp "$f" "$SHIP/wintypes-optin.dll.tmp"
        "$MINGW/llvm-strip" "$SHIP/wintypes-optin.dll.tmp"
        python3 - "$SHIP/wintypes-optin.dll.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
        mv "$SHIP/wintypes-optin.dll.tmp" "$SHIP/wintypes-optin.dll"
        echo "::notice::wintypes-optin.dll (Wine's wintypes.dll, linked as wintypes.dll only with env.MADEIRA_WINTYPES = 1): built; no wintypes.dll is shipped"
    else
        echo "::warning::wintypes-optin.dll did not build; env.MADEIRA_WINTYPES = 1 does nothing"
    fi
fi
if [ -n "$MFA" ]; then
    f="$B/dlls/mfasfsrcsnk/arm64ec-windows/mfasfsrcsnk.dll"
    if [ -f "$f" ] && ! python3 "$R/tools/patch-wine-mfasfsrcsnk-winedmo.py" --verify "$f"; then
        echo "::warning::mfasfsrcsnk.dll failed its check (no winedmo patch); not shipped"
        rm -f "$f"
    fi
fi
for d in $OPT; do
    f="$B/dlls/$d/arm64ec-windows/$d.dll"
    if [ -f "$f" ]; then
        cp "$f" "$SHIP/$d-optin.dll.tmp"
        "$MINGW/llvm-strip" "$SHIP/$d-optin.dll.tmp"
        python3 - "$SHIP/$d-optin.dll.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
        mv "$SHIP/$d-optin.dll.tmp" "$SHIP/$d-optin.dll"
        echo "::notice::$d-optin.dll (Wine's $d.dll, linked as $d.dll only for a game with $d in env.MADEIRA_OPTIN_DLLS): built"
    else
        echo "::warning::$d-optin.dll did not build; env.MADEIRA_OPTIN_DLLS = $d does nothing"
    fi
done
echo "::notice::built $built extra Wine DLLs for arm64ec${failed:+ (failed:$failed)}"
[ -n "$failed" ] && grep -m 10 "error" "$B.build.log"
exit 0
