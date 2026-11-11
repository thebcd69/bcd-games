// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Portions copyright 2026 Paulo Eduardo Correa (pauloec): the native iPhone Duo interface (Liquid Glass, system tabs, controller navigation, Windows desktop on Home), from thebcd69/bcd-games pull request #43; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Accelerate
import ImageIO
import SwiftUI
import UIKit

/// Game art for BCD's interface, made cheap to show (docs/BCD-UI-ARCHITECTURE.md, Performance).
/// Games run in BCD's own process, so every picture the interface keeps is memory a game lacks:
/// - each picture is fetched once and kept on disk (Caches/BCDArt), then decoded off the main
///   thread at the size its frame shows it (the frame's points times the screen's scale, never
///   more than the picture has), so a 100-point cover never holds a 1920-pixel picture;
/// - one decoded copy per picture: a smaller frame reuses a larger copy, a larger frame replaces
///   it. Screens hold a picture only while it is on screen (BCDArtImage lets go when its view
///   scrolls away or closes); beyond that BCDArt keeps about one and a half screens of pixels
///   (`budget`), least wanted first out, and iOS can take that back under memory pressure;
/// - a picture's glow (its own colours spilling softly past its edges) is a bitmap of a few
///   dozen pixels, blurred once on the CPU when the picture loads. The screen only stretches
///   it: the GPU never blurs anything and a still screen costs nothing;
/// - `purge()` drops every decoded picture when a game starts, so the game gets the memory.
@MainActor final class BCDArt {
    static let shared = BCDArt()

    /// A decoded picture and, when asked for, its glow.
    struct Loaded {
        let image: UIImage
        let glow: BCDGlow?
    }

    /// How a picture sits in its frame: filling it (cut to it), or whole inside it.
    enum Fit { case fill, fit }

    /// One picture as decoded: `full` when that is the picture's own size (nothing sharper exists).
    private final class Box {
        let image: UIImage
        let full: Bool
        var glow: BCDGlow?
        init(image: UIImage, full: Bool, glow: BCDGlow?) {
            self.image = image
            self.full = full
            self.glow = glow
        }
        var cost: Int {
            let picture = image.cgImage.map { $0.bytesPerRow * $0.height } ?? 0
            let light = glow?.image.cgImage.map { $0.bytesPerRow * $0.height } ?? 0
            return picture + light
        }
        /// Whether this copy shows sharp in a frame of `pixels` (at most 1:1).
        func covers(_ pixels: CGSize, _ fit: Fit) -> Bool {
            if full { return true }
            guard let cg = image.cgImage, cg.width > 0, cg.height > 0 else { return false }
            let x = pixels.width / CGFloat(cg.width), y = pixels.height / CGFloat(cg.height)
            return (fit == .fill ? max(x, y) : min(x, y)) <= 1.001
        }
    }

    private struct Decoded {
        var image: CGImage?
        var full = false
        var glow: BCDGlow?
        var misses: [URL] = []
    }

    private let memory = NSCache<NSString, Box>()
    private var running: [String: Task<Decoded, Never>] = [:]
    /// Addresses that answered with nothing usable this session (a store without that size).
    private var missing = Set<URL>()
    private var generation = 0

    private init() {
        memory.totalCostLimit = Self.budget
    }

    /// What BCDArt keeps decoded besides the pictures on screen: about one and a half screens of
    /// pixels (iPhone 13 Pro: 18 MB; SE: 6 MB; iPad Pro 13-inch: 34 MB). Enough for Home's art and
    /// a screen of the library to be there when the player switches between them.
    static var budget: Int {
        let screen = UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.screen }.first
        let pixels = screen.map { $0.nativeBounds.width * $0.nativeBounds.height } ?? 1170 * 2532
        return Int(pixels * 4 * 1.5)
    }

    /// The picture already decoded for these addresses, at whatever size, without waiting: what a
    /// view shows the moment it appears, until `load` brings the size (and the glow) it needs.
    func cached(_ urls: [URL], glow: Bool) -> Loaded? {
        guard let box = memory.object(forKey: Self.key(urls) as NSString) else { return nil }
        return Loaded(image: box.image, glow: glow ? box.glow : nil)
    }

    /// The first of `urls` that loads, decoded for a frame of `pixels` (`fit`: how it sits there).
    func load(_ urls: [URL], pixels: CGSize, fit: Fit, glow: Bool) async -> Loaded? {
        let key = Self.key(urls)
        if let hit = sharp(key, pixels, fit, glow) { return hit }
        let generation = self.generation
        // A copy of the same picture on its way (another frame asked for it): wait, then look again.
        if let current = running[key] {
            _ = await current.value
            guard generation == self.generation else { return nil }
            if let hit = sharp(key, pixels, fit, glow) { return hit }
        }
        let candidates = urls.filter { !missing.contains($0) }
        guard !candidates.isEmpty, !Task.isCancelled else { return nil }
        let task = Task.detached(priority: .utility) { () -> Decoded in
            var misses: [URL] = []
            for url in candidates {
                // The view that asked left the screen (a card scrolled past): nothing more to do,
                // and an address not fetched is not a missing one.
                if Task.isCancelled { break }
                guard let data = await BCDArtFiles.data(for: url) else {
                    if Task.isCancelled { break }
                    misses.append(url)
                    continue
                }
                guard let picture = BCDArtFiles.decode(data, pixels: pixels, fit: fit) else {
                    misses.append(url)
                    continue
                }
                return Decoded(image: picture.image, full: picture.full,
                               glow: glow ? BCDGlow.make(from: picture.image) : nil, misses: misses)
            }
            return Decoded(misses: misses)
        }
        running[key] = task
        let result = await withTaskCancellationHandler { await task.value } onCancel: { task.cancel() }
        if running[key] == task { running[key] = nil }
        guard generation == self.generation else { return nil }
        missing.formUnion(result.misses)
        guard let image = result.image else { return nil }
        let box = Box(image: UIImage(cgImage: image), full: result.full, glow: result.glow)
        if let old = memory.object(forKey: key as NSString) {
            if box.glow == nil { box.glow = old.glow }
            // A larger copy decoded meanwhile stays: this frame shows it just as sharp.
            if old.covers(CGSize(width: image.width, height: image.height), .fill) && !box.full {
                if old.glow == nil { old.glow = box.glow }
                return Loaded(image: old.image, glow: glow ? old.glow : nil)
            }
        }
        memory.setObject(box, forKey: key as NSString, cost: box.cost)
        return Loaded(image: box.image, glow: glow ? box.glow : nil)
    }

    /// The cached copy when it is sharp enough for the frame, its glow made first when asked for
    /// (a few dozen pixels: cheap).
    private func sharp(_ key: String, _ pixels: CGSize, _ fit: Fit, _ glow: Bool) -> Loaded? {
        guard let box = memory.object(forKey: key as NSString), box.covers(pixels, fit) else { return nil }
        if glow, box.glow == nil, let cg = box.image.cgImage {
            box.glow = BCDGlow.make(from: cg)
            memory.setObject(box, forKey: key as NSString, cost: box.cost)
        }
        return Loaded(image: box.image, glow: glow ? box.glow : nil)
    }

    /// Forget every decoded picture: a game is starting. The files on disk stay.
    func purge() {
        generation += 1
        memory.removeAllObjects()
        running.removeAll()
    }

    private static func key(_ urls: [URL]) -> String {
        urls.map(\.absoluteString).joined(separator: "|")
    }
}

/// Fetching and decoding, off the main thread.
enum BCDArtFiles {
    static let folder = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("BCDArt", isDirectory: true)

    /// The picture's bytes: from the app (file addresses), else from the disk cache, else from
    /// the store's server (https only), kept for next time.
    static func data(for url: URL) async -> Data? {
        if url.isFileURL { return try? Data(contentsOf: url, options: .mappedIfSafe) }
        guard url.scheme == "https" else { return nil }
        let file = folder.appendingPathComponent(name(for: url))
        if let data = try? Data(contentsOf: file, options: .mappedIfSafe), !data.isEmpty { return data }
        guard let (data, response) = try? await URLSession.shared.data(from: url),
              (response as? HTTPURLResponse)?.statusCode == 200, !data.isEmpty,
              let source = CGImageSourceCreateWithData(data as CFData, nil), CGImageSourceGetCount(source) > 0 else { return nil }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try? data.write(to: file, options: .atomic)
        return data
    }

    /// A stable file name for an address.
    static func name(for url: URL) -> String {
        var hash: UInt64 = 1469598103934665603
        for byte in url.absoluteString.utf8 { hash = (hash ^ UInt64(byte)) &* 1099511628211 }
        let ext = url.pathExtension.isEmpty ? "img" : url.pathExtension
        return String(hash, radix: 16) + "." + ext
    }

    /// Decoded in one pass at the size a frame of `pixels` shows it (`longSide`), never at full
    /// size first. `full`: that is the picture's own size.
    static func decode(_ data: Data, pixels: CGSize, fit: BCDArt.Fit) -> (image: CGImage, full: Bool)? {
        guard let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue,
              let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue,
              width > 0, height > 0 else {
            return nil
        }
        // EXIF orientations 5 to 8 turn the picture a quarter: its frame sees it the other way round.
        let turned = ((properties[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1) >= 5
        let size = turned ? CGSize(width: height, height: width) : CGSize(width: width, height: height)
        let native = Int(max(width, height))
        let long = longSide(of: size, in: pixels, fit: fit)
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: long,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
        return (image, long >= native)
    }

    /// The long side, in pixels, a picture of `size` needs in a frame of `pixels`: filling it, the
    /// picture's scale is the larger of the two ratios, fitted the smaller; never above the picture's
    /// own size. Rounded up to 16 pixels, so frames a point apart share one copy. An unknown frame
    /// gets the picture's own size.
    static func longSide(of size: CGSize, in pixels: CGSize, fit: BCDArt.Fit) -> Int {
        let native = Int(max(size.width, size.height).rounded(.up))
        guard size.width > 0, size.height > 0, pixels.width > 0, pixels.height > 0 else { return native }
        let x = pixels.width / size.width, y = pixels.height / size.height
        let scale = min(1, fit == .fill ? max(x, y) : min(x, y))
        let long = Int((scale * max(size.width, size.height)).rounded(.up))
        return min(native, max(16, (long + 15) / 16 * 16))
    }
}

/// A picture's glow: the picture shrunk to a few dozen pixels on a transparent canvas with
/// room around it, then blurred once on the CPU (vImage), so its colours spread past its
/// edges and fade out. `spreadX`/`spreadY` are that room as a share of the picture's width
/// and height: drawn over the picture's frame grown by them on every side, the glow lines up
/// with the picture. (Core Image is not used: its software renderer stalled in the simulator.)
struct BCDGlow {
    let image: UIImage
    let spreadX: CGFloat
    let spreadY: CGFloat

    static func make(from picture: CGImage) -> BCDGlow? {
        let long: CGFloat = 32
        let aspect = CGFloat(picture.width) / CGFloat(max(1, picture.height))
        let w = max(8, (aspect >= 1 ? long : long * aspect).rounded())
        let h = max(8, (aspect >= 1 ? long / aspect : long).rounded())
        let pad: CGFloat = 10
        let width = Int(w + 2 * pad), height = Int(h + 2 * pad)
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let canvas = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                     space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
              let pixels = canvas.data else { return nil }
        canvas.interpolationQuality = .medium
        canvas.draw(picture, in: CGRect(x: pad, y: pad, width: w, height: h))
        // Two tent passes, close to a Gaussian, carry the colours into the empty margin.
        var source = vImage_Buffer(data: pixels, height: vImagePixelCount(height), width: vImagePixelCount(width), rowBytes: width * 4)
        var scratch = [UInt8](repeating: 0, count: width * height * 4)
        let kernel = UInt32(Int(pad) | 1)
        let clear: [UInt8] = [0, 0, 0, 0]
        let blurred = scratch.withUnsafeMutableBytes { raw -> Bool in
            var temp = vImage_Buffer(data: raw.baseAddress, height: source.height, width: source.width, rowBytes: width * 4)
            let flags = vImage_Flags(kvImageBackgroundColorFill)
            return vImageTentConvolve_ARGB8888(&source, &temp, nil, 0, 0, kernel, kernel, clear, flags) == kvImageNoError
                && vImageTentConvolve_ARGB8888(&temp, &source, nil, 0, 0, kernel, kernel, clear, flags) == kvImageNoError
        }
        guard blurred else { return nil }
        saturate(pixels.assumingMemoryBound(to: UInt8.self), count: width * height, by: 1.5)
        guard let glow = canvas.makeImage() else { return nil }
        return BCDGlow(image: UIImage(cgImage: glow), spreadX: pad / w, spreadY: pad / h)
    }

    /// Richer colours for the light, on premultiplied RGBA (each channel stays within alpha).
    private static func saturate(_ p: UnsafeMutablePointer<UInt8>, count: Int, by amount: Float) {
        for i in 0..<count {
            let o = i * 4
            let a = Float(p[o + 3])
            guard a > 0 else { continue }
            let r = Float(p[o]), g = Float(p[o + 1]), b = Float(p[o + 2])
            let luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            p[o] = UInt8(max(0, min(a, luma + (r - luma) * amount)))
            p[o + 1] = UInt8(max(0, min(a, luma + (g - luma) * amount)))
            p[o + 2] = UInt8(max(0, min(a, luma + (b - luma) * amount)))
        }
    }
}

// MARK: - Showing art

/// Which glow a picture reports, and how bright (0...1).
struct BCDGlowRequest: Equatable {
    var id: String
    var strength: Double = 0.55
}

/// A picture from BCDArt filling its frame (cut to it), or `placeholder` while it loads or when
/// none of `urls` loads. A picture much wider than its frame (a store's wide header in a
/// tall cover's place) is shown whole over a dimmed, stretched copy of itself. With `glow`,
/// it reports its glow to the nearest `.bcdGlowLayer()`.
/// It holds its picture only while it is on screen: a card the grid scrolled away (a lazy grid
/// keeps its views) or a closed screen lets go of it, and BCDArt's budget decides what stays.
struct BCDArtImage<Placeholder: View>: View {
    let urls: [URL]
    let shape: CGFloat?
    let glow: BCDGlowRequest?
    let placeholder: () -> Placeholder
    @Environment(\.displayScale) private var scale
    /// The frame in points, as laid out.
    @State private var size: CGSize = .zero
    @State private var loaded: BCDArt.Loaded?

    /// `shape`: the frame's width / height, when known, to spot pictures of the wrong shape.
    init(urls: [URL], shape: CGFloat? = nil, glow: BCDGlowRequest? = nil,
         @ViewBuilder placeholder: @escaping () -> Placeholder) {
        self.urls = urls
        self.shape = shape
        self.glow = glow
        self.placeholder = placeholder
        _loaded = State(initialValue: BCDArt.shared.cached(urls, glow: glow != nil))
    }

    var body: some View {
        Color.clear
            .overlay { picture }
            .clipped()
            .onGeometryChange(for: CGSize.self) { $0.size } action: { size = $0 }
            .anchorPreference(key: BCDGlowKey.self, value: .bounds) { bounds in
                guard let glow, let made = loaded?.glow else { return [] }
                return [BCDGlowItem(id: glow.id, glow: made, bounds: bounds, strength: glow.strength)]
            }
            .onAppear { if loaded == nil { loaded = BCDArt.shared.cached(urls, glow: glow != nil) } }
            .onDisappear { loaded = nil }
            .task(id: BCDArtRequest(urls: urls, pixels: pixels)) {
                guard pixels.width >= 1, pixels.height >= 1 else { return }
                let result = await BCDArt.shared.load(urls, pixels: pixels, fit: .fill, glow: glow != nil)
                if !Task.isCancelled { loaded = result }
            }
    }

    /// The frame in pixels, in whole points (a layout a fraction of a point apart asks for no new copy).
    private var pixels: CGSize {
        CGSize(width: size.width.rounded(.up) * scale, height: size.height.rounded(.up) * scale)
    }

    @ViewBuilder private var picture: some View {
        if let image = loaded?.image {
            if wrongShape(image) {
                ZStack {
                    Image(uiImage: image).resizable().scaledToFill()
                        .overlay(Color.black.opacity(0.55))
                    Image(uiImage: image).resizable().scaledToFit()
                }
            } else {
                Image(uiImage: image).resizable().scaledToFill()
            }
        } else {
            placeholder()
        }
    }

    private func wrongShape(_ image: UIImage) -> Bool {
        guard let shape, image.size.height > 0 else { return false }
        return abs(log(image.size.width / image.size.height / shape)) > 0.5
    }
}

/// What a picture view asks BCDArt for: its addresses at its frame's size.
struct BCDArtRequest: Equatable {
    let urls: [URL]
    let pixels: CGSize
}

/// Decoration identities must not collide with the String IDs of scroll/focus targets.
struct BCDGlowRenderID: Hashable {
    let source: String
}

struct BCDGlowItem {
    let id: String
    var renderID: BCDGlowRenderID { BCDGlowRenderID(source: id) }
    let glow: BCDGlow
    let bounds: Anchor<CGRect>
    let strength: Double
}

struct BCDGlowKey: PreferenceKey {
    static var defaultValue: [BCDGlowItem] = []
    static func reduce(value: inout [BCDGlowItem], nextValue: () -> [BCDGlowItem]) {
        value.append(contentsOf: nextValue())
    }
}

extension View {
    /// Draws the glow of every BCDArtImage inside this view in one layer behind all of them,
    /// so one card's light never falls across another card. The glows are still pictures:
    /// nothing animates or blurs on its own.
    func bcdGlowLayer() -> some View {
        backgroundPreferenceValue(BCDGlowKey.self) { items in
            GeometryReader { proxy in
                ForEach(items, id: \.renderID) { item in
                    let frame = proxy[item.bounds]
                    Image(uiImage: item.glow.image)
                        .resizable()
                        .frame(width: frame.width * (1 + 2 * item.glow.spreadX),
                               height: frame.height * (1 + 2 * item.glow.spreadY))
                        .position(x: frame.midX, y: frame.midY)
                        .opacity(item.strength)
                }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
    }
}
