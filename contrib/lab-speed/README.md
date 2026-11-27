# Lab speed notes (community contribution, untested on device)

This folder records a speed-tuning experiment on BCD 0.1.712, shared so the work is not lost.
The author has ended the project and will not follow up, so treat it as reference material.

- `speeddefaults.h` + `launchlog.m`: a small dylib that writes speed defaults to the top of `bcd.cfg`
  (user lines below still win). `bcd-lab-speed = 1` keeps only 10 safe switches, `= 0` disables all.
  73 switches total, chosen from a sweep of all 994 catalog options (see `NOTES.txt`).
- `patch_bcd_ipa.py` / `insert_dylib.py`: repack a BCD IPA with that dylib and optional rebuilt libraries.
- `fast.xcconfig`: MoltenVK build at -O3 with `-mcpu=apple-m4`. M4-only CPU tuning; not suitable as a default.
- Rebuilt with `-mcpu=apple-m4`: xtajit64, xtajit, madeira_d3d12, d3d11-src, dxgi-src. LTO does not link for ARM64EC.
- `ENSHROUDED-CRASH-BRIEF.md`: notes from getting Enshrouded running via the MoltenVK route.

Nothing here was measured on a device. Several switches trade correctness for speed (bounds checks, NaN rules, W^X).
