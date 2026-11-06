// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

/// The machine an EA game's licence is asked for (docs/EA.md): the licence is bound to a hash of what the game itself reads about the
/// machine, so the hash must be made from what BCD's launch of that game reports. Three things BCD changes per game reach the hash:
/// the graphics card (the game's `gpu-card` line, else the NVIDIA switch, else Apple's: build/win32u-unix/sysparams_ios.c,
/// ios_virtual_gpu_ids), AVX (FEX's CPUID leaf 1 ECX: Game details › AVX, or `env.MADEIRA_FEX_AVX` = 1 or 2) and the classic CPUID
/// (`env.MADEIRA_FEX_CPUID_CLASSIC` = 1 sets DE and PSE in leaf 1 EDX: tools/patch-fex-ios-cpuid-classic.py). Plain values and rules, no
/// engine type: tests/host/check-ea.py compiles this file with BCDGPUCards.swift. BCDEAMachineLive.swift reads a game's lines.
enum BCDEAMachine {
    struct Choice: Equatable {
        var vendor: UInt16 = BCDGPUCards.apple
        var device: UInt16 = 0x0001
        var avx = false
        var classicCPUID = false
    }

    /// CPUID.1:EDX bits 2 (DE) and 3 (PSE).
    static let classicEDXBits: UInt32 = 0xC

    /// `line` answers a key the way the launch reads it (nil: not set); the two switches are the game's own Game details choices.
    static func choose(line: (String) -> String?, nvidiaSwitch: Bool, avxSwitch: Bool) -> Choice {
        var choice = Choice()
        let word = (line(BCDGPUCards.key) ?? "").trimmingCharacters(in: .whitespaces)
        let nvidiaLine = (line("env.DXMT_ENABLE_NVEXT") ?? "").trimmingCharacters(in: .whitespaces).first == "1"
        if let card = BCDGPUCards.card(word) {
            // A chosen card (the Apple card too) decides the identity and turns the older NVIDIA switch off for the game
            // (BCDGPUCards.environment), so neither the switch nor the line below counts.
            choice.vendor = card.vendor
            choice.device = card.device
        } else if nvidiaSwitch || nvidiaLine {
            choice.vendor = BCDGPUCards.nvidia
            choice.device = 0x2544
        }
        let avx = (line("env.MADEIRA_FEX_AVX") ?? "").trimmingCharacters(in: .whitespaces)
        choice.avx = avxSwitch || avx == "1" || avx == "2"
        choice.classicCPUID = (line("env.MADEIRA_FEX_CPUID_CLASSIC") ?? "").trimmingCharacters(in: .whitespaces) == "1"
        return choice
    }
}
