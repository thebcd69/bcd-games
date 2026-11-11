// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The small copy of a screenshot that goes with Send Log, compiled on the host by tests/host/check-bcd-screenshot.py with
// app/Madeira/BCDSessionCore.swift (the rules) and app/Madeira/BCDScreenshotShrink.swift: the steps, the search with a stand-in encoder, and
// the real ImageIO copy of generated pictures.

import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: String = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
    if !ok { failures += 1 }
}

/// A picture of `w` x `h` pixels: a smooth gradient plus noise of the given strength (0 = none, 255 = white noise). Deterministic.
func picture(_ w: Int, _ h: Int, noise: Int) -> CGImage {
    var state: UInt64 = 0x9E3779B97F4A7C15
    var pixels = [UInt8](repeating: 255, count: w * h * 4)
    for y in 0..<h {
        for x in 0..<w {
            let i = (y * w + x) * 4
            for c in 0..<3 {
                state = state &* 6364136223846793005 &+ 1442695040888963407
                let n = noise == 0 ? 0 : Int((state >> 33) % UInt64(noise + 1)) - noise / 2
                let base = c == 0 ? 255 * x / w : (c == 1 ? 255 * y / h : 128)
                pixels[i + c] = UInt8(max(0, min(255, base + n)))
            }
        }
    }
    let space = CGColorSpaceCreateDeviceRGB()
    let ctx = pixels.withUnsafeMutableBytes {
        CGContext(data: $0.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: space,
                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    }
    return ctx.makeImage()!
}

func write(_ image: CGImage, to url: URL, quality: Double = 0.85) {
    let out = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(out, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
    precondition(CGImageDestinationFinalize(out))
}

func isWholeJPEG(_ d: Data) -> Bool { d.count > 4 && d[0] == 0xFF && d[1] == 0xD8 && d[2] == 0xFF && d[d.count - 2] == 0xFF && d[d.count - 1] == 0xD9 }

@main enum Run {
    static func main() throws {
        let target = BCDScreenshotRules.uploadTargetBytes
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-shrink-\(getpid())")
        try? FileManager.default.removeItem(at: dir)   // start empty: exit() skips defer, so a folder with a reused process id may be left
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        // ---- the steps ----------------------------------------------------------------------------------------------------
        let big = BCDScreenshotShrink.steps(sourceLongEdge: 2000)
        check("a large picture: 6 sizes by 4 qualities, 24 steps, from 1280 px at 0.75 to 640 px at 0.4",
              big.count == 24 && big.first!.edge == 1280 && big.first!.quality == 0.75 && big.last!.edge == 640 && big.last!.quality == 0.4)
        check("... the best quality at a size comes first, and no size comes back to a larger one",
              zip(big, big.dropFirst()).allSatisfy { $0.edge > $1.edge || ($0.edge == $1.edge && $0.quality > $1.quality) })
        check("... sizes: 1280, 1120, 960, 800, 720, 640", Array(Set(big.map { $0.edge })).sorted(by: >) == [1280, 1120, 960, 800, 720, 640])
        let mid = BCDScreenshotShrink.steps(sourceLongEdge: 900)
        check("a 900 px picture starts at its own size and never goes above it",
              mid.first!.edge == 900 && mid.allSatisfy { $0.edge <= 900 } && Array(Set(mid.map { $0.edge })).sorted(by: >) == [900, 800, 720, 640])
        let tiny = BCDScreenshotShrink.steps(sourceLongEdge: 320)
        check("a 320 px picture is one size at four qualities (never made larger)", tiny.count == 4 && tiny.allSatisfy { $0.edge == 320 })
        check("a 1 px picture does not make a 0 px step", BCDScreenshotShrink.steps(sourceLongEdge: 0).allSatisfy { $0.edge >= 1 })

        // ---- the search, with a stand-in encoder ------------------------------------------------------------------------------
        var asked: [(Int, Double)] = []
        let sizeOf: (Int, Double) -> Int = { edge, q in Int(Double(edge * edge) * q / 4) }   // 1280 px at 0.75 = 307 KB ... 640 px at 0.4 = 40 KB
        let fake: (Int, Double) -> (data: Data, width: Int, height: Int)? = { edge, q in
            asked.append((edge, q))
            return (Data(count: sizeOf(edge, q)), edge, edge / 2)
        }
        let won = BCDScreenshotShrink.search(steps: big, target: target, encode: fake)
        let expected = big.firstIndex { sizeOf($0.edge, $0.quality) <= target }!
        check("the search stops at the first copy at or under the target", won != nil && won!.fits && won!.data.count <= target
              && asked.count == expected + 1 && won!.tries == expected + 1, "tries \(String(describing: won?.tries)), asked \(asked.count), expected \(expected + 1)")
        check("... and the copy before it was over", expected > 0 && sizeOf(big[expected - 1].edge, big[expected - 1].quality) > target)
        check("... the copy reports its size, quality and the steps it took", won!.width == big[expected].edge && won!.quality == big[expected].quality)
        asked = []
        let first = BCDScreenshotShrink.search(steps: big, target: 10 << 20, encode: fake)
        check("a picture that already fits is made once, at 1280 px and quality 0.75", first?.tries == 1 && asked.count == 1 && first?.quality == 0.75 && first?.fits == true)
        asked = []
        let never = BCDScreenshotShrink.search(steps: big, target: 100, encode: fake)
        check("when nothing fits, the floor's picture is what comes back, marked as over the target",
              never != nil && !never!.fits && never!.width == 640 && never!.quality == 0.4 && never!.tries == big.count && asked.count == big.count)
        var calls = 0
        let skipping = BCDScreenshotShrink.search(steps: big, target: target) { edge, q in
            calls += 1
            return calls < 3 ? nil : (Data(count: 10), edge, edge)
        }
        check("a step that could not be encoded is skipped, not counted", skipping?.tries == 1 && skipping?.fits == true && calls == 3)
        check("when nothing could be made, there is no copy", BCDScreenshotShrink.search(steps: big, target: target, encode: { _, _ in nil }) == nil)

        // ---- the real thing, on generated pictures ------------------------------------------------------------------------------
        // A phone's screen as a game shows it, 2000 px wide: smooth with a little grain.
        let landscape = dir.appendingPathComponent("landscape.jpg")
        write(picture(2000, 922, noise: 60), to: landscape)
        let landscapeBytes = try Data(contentsOf: landscape)
        let copy = BCDScreenshotShrink.smallCopy(of: landscape)
        check("a 2000 px picture with grain becomes a whole JPEG of 100 KB or less", copy != nil && copy!.fits && copy!.data.count <= target && isWholeJPEG(copy!.data),
              "\(String(describing: copy?.data.count)) bytes")
        check("... at most 1280 px on the long side, the shape kept", copy != nil && max(copy!.width, copy!.height) <= 1280 && max(copy!.width, copy!.height) >= 640
              && abs(Double(copy!.width) / Double(copy!.height) - 2000.0 / 922.0) < 0.02, "\(String(describing: copy?.width))x\(String(describing: copy?.height))")
        check("... and the kept file is not touched", (try? Data(contentsOf: landscape)) == landscapeBytes)
        if let copy {
            print("note: grainy 2000x922 -> \(copy.width)x\(copy.height), \(copy.data.count) bytes, quality \(copy.quality), \(copy.tries) tries")
            let src = CGImageSourceCreateWithData(copy.data as CFData, nil)!
            let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any] ?? [:]
            let exif = (props[kCGImagePropertyExifDictionary] as? [CFString: Any]) ?? [:]
            check("... decodes as the size it says and carries no GPS, TIFF (make, model, time), IPTC or Apple block; the only Exif is its size and colour space",
                  (props[kCGImagePropertyPixelWidth] as? Int) == copy.width && (props[kCGImagePropertyPixelHeight] as? Int) == copy.height
                  && props[kCGImagePropertyGPSDictionary] == nil && props[kCGImagePropertyTIFFDictionary] == nil && props[kCGImagePropertyIPTCDictionary] == nil
                  && props[kCGImagePropertyMakerAppleDictionary] == nil
                  && Set(exif.keys.map { $0 as String }).isSubset(of: ["PixelXDimension", "PixelYDimension", "ColorSpace"]),
                  "\(props.keys.map { $0 as String }.sorted()) exif \(exif.keys.map { $0 as String }.sorted())")
        }

        let smooth = dir.appendingPathComponent("smooth.jpg")
        write(picture(2000, 922, noise: 0), to: smooth)
        let easy = BCDScreenshotShrink.smallCopy(of: smooth)
        check("a smooth picture is made once, at 1280 px and quality 0.75", easy?.tries == 1 && easy?.quality == 0.75 && max(easy?.width ?? 0, easy?.height ?? 0) == 1280
              && easy?.fits == true, "\(String(describing: easy?.width))x\(String(describing: easy?.height)) tries \(String(describing: easy?.tries))")

        let portrait = dir.appendingPathComponent("portrait.jpg")
        write(picture(922, 2000, noise: 60), to: portrait)
        let upright = BCDScreenshotShrink.smallCopy(of: portrait)
        check("a portrait picture is limited on its height", upright != nil && upright!.height <= 1280 && upright!.width < upright!.height && upright!.fits)

        let small = dir.appendingPathComponent("small.jpg")
        write(picture(320, 200, noise: 30), to: small)
        let kept = BCDScreenshotShrink.smallCopy(of: small)
        check("a picture smaller than the first step is never made larger", kept != nil && kept!.width == 320 && kept!.height == 200)

        // White noise is the hardest picture there is, and even it is under 100 KB at 640 px: the floor is not reached by a real screenshot.
        let noisy = dir.appendingPathComponent("noisy.jpg")
        write(picture(1600, 1600, noise: 255), to: noisy, quality: 0.9)
        let hard = BCDScreenshotShrink.smallCopy(of: noisy)
        check("white noise 1600 px square ends under the target, at a lower size and quality", hard != nil && hard!.fits && hard!.data.count <= target && isWholeJPEG(hard!.data)
              && hard!.tries > 1, "\(String(describing: hard?.width)) \(String(describing: hard?.data.count))")
        if let hard { print("note: white noise 1600x1600 -> \(hard.width)x\(hard.height), \(hard.data.count) bytes, quality \(hard.quality), \(hard.tries) tries") }
        // Asked for something it cannot reach (a target of 1 KB), it ends at the floor and says it is over: 640 px, quality 0.4, the smallest it makes.
        let floor = BCDScreenshotShrink.smallCopy(of: noisy, target: 1024)
        check("a target it cannot reach ends at the floor: 640 px, quality 0.4, marked over the target, a whole JPEG, every step tried",
              floor != nil && !floor!.fits && floor!.width == 640 && floor!.height == 640 && floor!.quality == 0.4 && floor!.data.count > 1024 && isWholeJPEG(floor!.data)
              && floor!.tries == BCDScreenshotShrink.steps(sourceLongEdge: 1600).count, "\(String(describing: floor?.width)) \(String(describing: floor?.tries))")
        check("the floor's picture is under what the intake takes", (floor?.data.count ?? Int.max) <= BCDScreenshotRules.maxSendBytes)

        check("a file that is not a picture gives no copy", BCDScreenshotShrink.smallCopy(of: {
            let u = dir.appendingPathComponent("text.jpg"); try? Data("not a picture".utf8).write(to: u); return u }()) == nil)
        check("a file that is not there gives no copy", BCDScreenshotShrink.smallCopy(of: dir.appendingPathComponent("gone.jpg")) == nil)

        print(failures == 0 ? "bcd_shot_shrink_checks: PASS" : "bcd_shot_shrink_checks: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
