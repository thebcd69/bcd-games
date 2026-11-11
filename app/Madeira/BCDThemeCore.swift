// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 creeper4004
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

/// Portable profile format: no SwiftUI, UIKit or engine state in the file.
struct BCDThemeProfile: Codable, Equatable, Identifiable {
    var version = 1
    var id: String
    var name: String
    var seed: String?
    var colors: [String: String]

    static let roles = ["ground", "groundTop", "surface", "surfaceRaised", "stroke", "text", "textSoft",
                        "accent", "accentSecondary", "accentSoft", "success", "warning", "danger", "panel", "onAccent"]
    static let original = BCDThemeProfile(id: "bcd-violet", name: "BCD Violet", colors: [
        "ground": "#0B0816", "groundTop": "#1A0F33", "surface": "#1A1430", "surfaceRaised": "#231B3F",
        "stroke": "#2F2350", "text": "#F4F1FF", "textSoft": "#B7AECF", "accent": "#7C3AED",
        "accentSecondary": "#D946EF", "accentSoft": "#C9B7FF", "success": "#34D399", "warning": "#FFC24A",
        "danger": "#FF7588", "panel": "#100C1F", "onAccent": "#FFFFFF"
    ])

    func validated() throws -> Self {
        guard version == 1 else { throw BCDThemeError.invalid("This profile uses an unsupported version.") }
        guard !id.isEmpty, id.count <= 80, id.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_{}")).contains($0) }),
              !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 60,
              !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
            throw BCDThemeError.invalid("Use a profile name of 1–60 characters and a simple profile ID.")
        }
        for role in Self.roles {
            guard let value = colors[role], BCDThemeRGB(hex: value) != nil else {
                throw BCDThemeError.invalid("The \(role) color must be a six-digit hex color, such as #7C3AED.")
            }
        }
        if let seed, BCDThemeRGB(hex: seed) == nil { throw BCDThemeError.invalid("The main color is not a valid hex color.") }
        // Files can style every surface, but cannot hide text or turn an action into an unreadable block.
        for background in ["ground", "groundTop", "surface", "surfaceRaised", "panel"] {
            for foreground in ["text", "textSoft", "accentSoft"] {
                guard rgb(foreground).contrast(with: rgb(background)) >= 4.5 else {
                    throw BCDThemeError.invalid("\(foreground) needs more contrast against \(background).")
                }
            }
        }
        for accent in ["accent", "accentSecondary"] {
            guard rgb("onAccent").contrast(with: rgb(accent)) >= 3 else {
                throw BCDThemeError.invalid("Button text needs more contrast against \(accent).")
            }
        }
        return self
    }

    func rgb(_ role: String) -> BCDThemeRGB {
        BCDThemeRGB(hex: colors[role] ?? "") ?? BCDThemeRGB(hex: Self.original.colors[role] ?? "#FFFFFF")!
    }

    static func decode(_ data: Data) throws -> Self {
        guard data.count <= 65_536 else { throw BCDThemeError.invalid("Choose a color profile smaller than 64 KB.") }
        do { return try JSONDecoder().decode(Self.self, from: data).validated() }
        catch let error as BCDThemeError { throw error }
        catch { throw BCDThemeError.invalid("This file is not a BCD color profile. Export one from Appearance to use as a template.") }
    }

    func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(validated())
    }

    /// One main color also tints the surfaces. Keep readable action fills even for yellow or white seeds.
    static func generated(main: BCDThemeRGB, id: String = "custom", name: String = "My colors") -> Self {
        let black = BCDThemeRGB(red: 0, green: 0, blue: 0)
        let white = BCDThemeRGB(red: 1, green: 1, blue: 1)
        var accent = main
        while accent.contrast(with: white) < 4.5 { accent = accent.mixed(with: black, amount: 0.04) }
        let secondary = accent.mixed(with: BCDThemeRGB(red: main.blue, green: main.red, blue: main.green), amount: 0.25)
        var result = original
        result.id = id
        result.name = name
        result.seed = main.hex
        result.colors.merge([
            "ground": main.mixed(with: black, amount: 0.96).hex,
            "groundTop": main.mixed(with: black, amount: 0.84).hex,
            "surface": main.mixed(with: black, amount: 0.89).hex,
            "surfaceRaised": main.mixed(with: black, amount: 0.82).hex,
            "panel": main.mixed(with: black, amount: 0.94).hex,
            "stroke": main.mixed(with: white, amount: 0.20).mixed(with: black, amount: 0.60).hex,
            "text": main.mixed(with: white, amount: 0.96).hex,
            "textSoft": main.mixed(with: white, amount: 0.72).hex,
            "accent": accent.hex, "accentSecondary": secondary.accessibleFill.hex,
            "accentSoft": main.mixed(with: white, amount: 0.70).hex
        ]) { _, new in new }
        return result
    }
}

enum BCDThemeError: LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case .invalid(let message) = self { return message }; return nil }
}

struct BCDThemeRGB: Equatable {
    var red: Double
    var green: Double
    var blue: Double

    init(red: Double, green: Double, blue: Double) {
        func bounded(_ value: Double) -> Double { value.isFinite ? min(1, max(0, value)) : 0 }
        self.red = bounded(red); self.green = bounded(green); self.blue = bounded(blue)
    }
    init?(hex: String) {
        guard hex.count == 7, hex.first == "#", hex.dropFirst().allSatisfy({ $0.isASCII && $0.isHexDigit }),
              let value = UInt32(hex.dropFirst(), radix: 16) else { return nil }
        red = Double((value >> 16) & 255) / 255
        green = Double((value >> 8) & 255) / 255
        blue = Double(value & 255) / 255
    }
    var hex: String { String(format: "#%02X%02X%02X", Int((red * 255).rounded()), Int((green * 255).rounded()), Int((blue * 255).rounded())) }
    func mixed(with other: Self, amount: Double) -> Self {
        Self(red: red * (1 - amount) + other.red * amount, green: green * (1 - amount) + other.green * amount,
             blue: blue * (1 - amount) + other.blue * amount)
    }
    private var luminance: Double {
        func linear(_ v: Double) -> Double { v <= 0.04045 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4) }
        return linear(red) * 0.2126 + linear(green) * 0.7152 + linear(blue) * 0.0722
    }
    func contrast(with other: Self) -> Double { (max(luminance, other.luminance) + 0.05) / (min(luminance, other.luminance) + 0.05) }
    var accessibleFill: Self {
        var color = self
        while color.contrast(with: Self(red: 1, green: 1, blue: 1)) < 4.6 { color = color.mixed(with: Self(red: 0, green: 0, blue: 0), amount: 0.04) }
        return color
    }
}
