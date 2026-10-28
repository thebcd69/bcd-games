# Recording Auto HDR evidence

Choose **Performance HUD → 4** in the game's options or the in-game Display
menu. Report now includes **HDR output**, automatically samples the game
texture, and writes evidence to that game's existing log. There is no separate
measurement toggle or second overlay. Custom HUDs can include the HDR item too.

For each game you can test:

1. Keep brightness, resolution, frame limit and upscaling fixed, with frame
   generation off initially. If practical, first record 60 seconds with Auto HDR
   disabled before launch, then exit and enable it for the next run. Keep both
   game logs: this captures the full feature cost. The live SDR reference retains
   the float/EDR layer and only removes highlight expansion.
2. Record the same bright scene for about 60 seconds in HDR view, then 60 in
   SDR view, then 30 back in HDR. Use **Display → Auto HDR comparison → SDR/HDR**
   and resume after each change. Keep HUD 4 visible and include its session time.
3. Include a dark scene and a white HUD/menu if practical. Note any flicker,
   crushed shadows, changed colors, overly bright UI, stutter or crash.
4. Background and resume once. If practical, change brightness once and restore
   it; announce it in the recording so it can be matched to the headroom log.
5. Exit normally and export that game's diagnostic log. Send the log with the
   recording and the game's name. A D3D9 and a D3D11 game would give useful
   coverage; use games you already have. A recording may be tone-mapped by the
   recorder/player; the log's sampled values establish the rendered signal.

The existing **Documents/logs/<program>-<date>.txt** contains one-line JSON
records prefixed `[hdr-evidence]`: a session ID, game/API/bitness, build/commit,
device/OS, settings, control changes, background/foreground, and five-second
performance/signal samples. Every comparison change starts a new segment.
Samples include elapsed seconds, sample age, sampled peak, applied ceiling,
headroom, above-SDR counts, EDR/linear/float configuration, FPS, average frame
time, rolling 1% low, CPU/GPU load, memory and thermal state. The renderer's
existing startup lines show the DLL actually selected. The 1% low is a rolling
30-second window, so allow it to settle after switching. An end line summarizes
logged intervals; a crash may leave only the preceding records.

The test pattern and highlight ceiling are in **Game → Options → Display →
HDR calibration (advanced)**. Choose Off, 10 or 30 seconds of reference bands
at the first supported DXMT frames; the image then returns to gameplay. Those
intervals are labelled as calibration and do not count as gameplay confirmation.
Use pattern Off for the main recordings. The peak is a sampled multiplier of
SDR white, rather than a measurement of panel brightness in nits.
