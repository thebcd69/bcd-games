# Auto HDR live comparison and signal validation

This fork is based on BCD 0.1.611 (`169a939c`), with the Auto HDR experiment
rebased by the operator (`e735b5e9`). Local build 612 adds live comparison and
measurement. Auto HDR expands an SDR game's highlights; it does not recover
missing native HDR information.

## On the phone

1. Enable **Auto HDR (experimental)** in the game's Display settings before
   launching it. Use the updated DXMT Direct3D 9/11 renderer. The live controls
   are unavailable for a session started without Auto HDR, or for Direct3D 12.
2. Choose **Performance HUD → 4**. HDR output appears as one Report entry;
   sampling follows that layout automatically. A Custom HUD can include it too.
3. In a bright scene, use **Display → Auto HDR comparison → SDR/HDR**, then
   resume. Both use the same float/EDR layer so SDR white stays stable.
4. For reference bands, open **Game → Options → Display → HDR calibration
   (advanced)** before launch. Choose a 10- or 30-second startup pattern; it
   returns to gameplay automatically. Keep it Off for gameplay recordings.

See [Recording Auto HDR evidence](auto-hdr-recording.md) for a short test plan
and the `[hdr-evidence]` records in each game's existing log.

**Game frame contains an HDR signal** requires measured finite pixels above
1× SDR white, an RGBA16Float drawable, extended-linear-sRGB color space, EDR
enabled, and current display headroom above 1×. Pattern measurements are
explicitly labelled **HDR test pattern confirmed**. This validates the rendered
signal and display configuration, not physical panel brightness in nits.

A dark scene may have no HDR highlights. Sampling checks a uniform 64×36 grid
(2,304 pixels), at most twice a second; zero positive samples cannot rule out
small highlights between the sample points. Headroom changes with the display
and operating conditions. Screenshots or an SDR preview are not a measurement
of the physical display's HDR output.

Live comparison lasts for this session; calibration settings are saved per game. Resume the game to apply a change and acquire a
new measurement; every change invalidates earlier in-flight results. Choose a HUD without the HDR item to stop the extra GPU sampling work. Diagnostic
lines use the `[hdr-signal]` prefix in the session log.

## Host validation

Run `python3 tests/host/check-dxmt-auto-hdr.py` on a Mac with Xcode and a Metal
GPU. It applies both patches to an isolated pin, checks idempotence and drift,
compiles the production iOS presenter/bridge and iOS/macOS shader, renders the
actual present pass numerically, tests the native probe on real Metal
drawables, and checks the UI evidence rules. It does not emulate an HDR panel.

`python3 tests/host/check-dxmt-patch-chain.py` checks interaction with all DXMT,
airconv and winemetal patches in workflow order, twice.
