# Enshrouded crash in BCD 0.1.665 on iPad (queued 2026-11-16 by the user: "Yes, after resume")
Do on resume, in the ios-emu project chat, on Fable (paced by the usage board).
- Source: ~/src/emu-upstreams/bcd-games (pulled to 41e972b = 0.1.665). User's IPA: ~/Downloads/BCD.ipa; data-only patch: ~/src/bcd-patches/patch_bcd_ipa.py -> ~/Downloads/BCD-mac-lab.ipa (Vortex rule).
- Logs: ~/.claude/uploads/1a9d4fec-3aa7-4bd0-b109-32e54193cc35/ (001f2560-BCD_log_the_run_before.txt = full run, 49k lines; 0d0be9a0-text.txt = auto-tune report).
- Crash: after 452 s / 6060 frames, enshrouded.exe JIT code (FEX arm64ec) does `str w6,[x8]` with x8=0 (pc 0x1630d33ec, insn 0x99000106, log line ~44860-44900), c0000005, game SEH -> crash handler -> exit. A second c0000005 at line ~48014 (tid 0038, p1 = ASCII "KClientJ" used as a pointer) ends the process. Auto-tune: dock-game-died-session-open + x64-null-fault-diag, "next nothing".
- Also: every start logs `ml1036: no hole fits a 1152MB pool — SHRINKING to 592/624MB`.
- Goal: find the root cause, fix it in source, build a BCD IPA, verify as far as possible without an iPad (Simulator / unit tests), give the user the IPA. GPL-3 + LICENSE-BCD.md terms; keep notices.

## Done 2026-11-16 (main chat)
Root cause: Metal compiler XPC connection lost under memory pressure -> KosmicKrisp compile fails -> game aborts (dockhost crash after is a side effect). Fix: kk-retry shim (~/src/bcd-patches/kk-retry, Mac test PASS) in ~/Downloads/BCD-mac-lab.ipa. Not tested on iPad.
