# Experimental Auto HDR

Auto HDR expands the highlights of **SDR DXMT games** into the display's available
HDR range. It uses Apple's EDR output directly, without installing ReShade. It is
off by default. It cannot recover detail already clipped by a game's SDR renderer.

## Try it

Use a build containing `tools/patch-dxmt-auto-hdr.py` and its matching shaders.
Open a game's settings, choose **Display**, and turn on **Auto HDR (experimental)**
directly below Frame generation. It is also available on iOS 18–25, where Frame
generation is hidden. Restart the game to apply it.

For **64-bit Direct3D 10/11**, the switch automatically selects the source-built
D3D11 renderer. No extra renderer setting is needed.

For manual configuration, add to the game's own config:

```ini
env.DXMT_AUTO_HDR = 1
env.DXMT_AUTO_HDR_PEAK = 2
```

For **64-bit Direct3D 9**, use BCD's DXMT backend as usual:

```ini
env.MADEIRA_D3D9_X64 = dxmt
```

The native and emulated 32-bit DXMT backends pick up the experiment when rebuilt.
The two Auto HDR controls also appear in Settings > All settings, under
Direct3D 9/10/11 (DXMT). Prefer a game's config when comparing results. Restart
the session after changing these options. Set `env.DXMT_AUTO_HDR = 0` to disable
it; omitting the line uses the global setting, or off when no global setting exists.
The game's Display toggle follows the global setting until changed; switching it
off writes an explicit override, so a global on cannot turn it back on.

`DXMT_AUTO_HDR_PEAK` is a multiplier of SDR white, between 1 and 8; the default is
2. Decimal values work. Invalid values use 2 and finite values outside the range
are clamped. A value of 1 keeps SDR brightness through the same linear EDR path,
which is useful for comparison. The actual peak is limited to the display's
**current** headroom each frame, so it can change with brightness, thermal state,
or the display the window is on. An SDR display or detached layer uses a peak of 1.

The log contains `[auto-hdr] requested` when the new presenter reads the option.
For 64-bit D3D10/11, also check the `[WineProc]` line selecting `d3d11-src.dll`.
If the build says that DLL is missing, the prebuilt D3D11 renderer has no Auto HDR.

## Scope and tradeoffs

- The experiment handles sRGB 8-bit RGBA/BGRA swapchains, including sRGB texture
  views. Floating-point, PQ, scRGB and other color spaces bypass highlight expansion.
- It leaves blacks and the lower 60% of linear maxRGB unchanged, then smoothly
  expands highlights. All three channels get the same gain to preserve hue.
  White menus and HUD elements brighten too: the final image has no UI mask.
- The final layer uses RGBA16Float, extended linear sRGB and EDR. There is no extra
  full-screen pass, but the drawable uses more memory/bandwidth than BGRA8, and the
  shader does more arithmetic. iOS headroom is refreshed asynchronously on the main thread at most twice a second per layer; rendering reads a bounded-age cache. Probe preparation runs on a utility queue and its shared buffer is reused after GPU completion.
- Native game HDR discovery/metadata and BCD's Direct3D 12 presentation are separate
  work. This change does not advertise an HDR Windows monitor or implement D3D12 HDR.
- Test frame generation and upscaling combinations separately. Screenshots,
  recordings and mirroring may represent HDR differently from the physical display.

Apple documents the EDR layer setup in
[Performing your own tone mapping](https://developer.apple.com/metal/performing-your-own-tone-mapping)
and the iOS limits in
[currentEDRHeadroom](https://developer.apple.com/documentation/uikit/uiscreen/currentedrheadroom)
and [potentialEDRHeadroom](https://developer.apple.com/documentation/uikit/uiscreen/potentialedrheadroom).

## Build integration

The IPA workflow builds the optional `d3d11-src.dll` with `D3D11_AUTO_HDR=1` on a
private source copy before patching the submodule. It pairs that copy's presenter
with a freshly compiled Metal 3.1 shader; the pristine DLL comparison retains the
original shader and metadata. Auto HDR selects the source DLL for its session;
`env.MADEIRA_D3D11_SRC = 1` can still select it independently.

Later, `patch-dxmt-auto-hdr.py` runs after the layer-format patch and before the
i386 farm and native iOS builds. Their normal shader builds consume the patched
source. The ARM64EC D3D9 recipe also rebuilds the shader when it sees the patch;
reusing its usual prebuilt shader would silently omit the effect. Shader compilation
failure stops those source DLL builds. Submodule pins stay unchanged.

For a local source-D3D11 build, start with the pinned, unpatched DXMT checkout:

```sh
D3D11_AUTO_HDR=1 bash tools/build-d3d11-dll.sh
```

For a complete local IPA build, apply the workflow's full patch sequence, including:

```sh
python3 tools/patch-dxmt-layer-format.py
python3 tools/patch-dxmt-auto-hdr.py
python3 tools/patch-dxmt-hdr-live.py
```

Then rebuild the relevant DXMT/native libraries and IPA. Changing config alone
cannot add the shader to an existing released IPA.

## Validation

```sh
python3 tests/host/check-dxmt-auto-hdr.py
python3 tests/host/check-dxmt-patch-chain.py
```

The first check applies the patch in isolation, rejects moved anchors without
partial writes, checks a repeated run, executes the production presenter's state
transitions, compiles the iOS bridge and presenter, and compiles the full shader for
iOS 18 and macOS at Metal 3.1. On a Mac with a Metal GPU it renders through the real
present shader and reads back floating-point pixels: black, midtones, monotonic
ramps, peaks 1–8, sRGB decoding, hue ratios, alpha, scaling and gamma ramps.

These checks establish numerical output and build compatibility. They do not
establish game compatibility, sustained performance, or physical HDR appearance on
an iPhone/iPad. Device testing should compare the same scene with Auto HDR off,
peak 1 and peak 2, including white HUDs, brightness changes, an SDR external display,
and a later session with the option off.
