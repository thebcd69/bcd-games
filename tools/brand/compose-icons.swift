// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

// compose-icons <transparent-logo.png> <mesh.png> <outdir>
// The 2026-11-11 icon (owner's choice): the transparent logo on the mesh for the light icon, the transparent logo alone for the
// dark icon (iOS draws its own dark background under it). Writes, all from the two 1024x1024 sources:
//   AppIcon-1024.png         light: the mesh, then the logo over it (opaque);
//   AppIcon-Dark-1024.png    dark: the logo on a transparent background;
//   AppIcon-Tinted-1024.png  tinted: the logo as opaque gray on black (iOS tints it by luminance);
//   BCDLogo.png              the in-app mark, light: AppIcon-1024 at 256x256;
//   readme-icon-256.png      the README's icon: the dark icon as iOS shows it (the logo on iOS's dark background, about 32 gray at the
//                            top to 14 at the bottom), clipped to iOS's rounded square (22.37 %), transparent outside.
// The in-app mark has one version: BCD's interface is always dark, so a dark variant would show everywhere.
let a = CommandLine.arguments
guard a.count == 4 else { print("usage: compose-icons <transparent-logo.png> <mesh.png> <outdir>"); exit(2) }
let out = URL(fileURLWithPath: a[3])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
func load(_ p: String) -> CGImage { CGImageSourceCreateImageAtIndex(CGImageSourceCreateWithURL(URL(fileURLWithPath: p) as CFURL, nil)!, 0, nil)! }
func canvas(_ n: Int, alpha: Bool) -> CGContext {
    let c = CGContext(data: nil, width: n, height: n, bitsPerComponent: 8, bytesPerRow: 0, space: srgb,
                      bitmapInfo: alpha ? CGImageAlphaInfo.premultipliedLast.rawValue : CGImageAlphaInfo.noneSkipLast.rawValue)!
    c.interpolationQuality = .high
    return c
}
func save(_ image: CGImage, _ name: String) {
    let d = CGImageDestinationCreateWithURL(out.appendingPathComponent(name) as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(d, image, nil)
    guard CGImageDestinationFinalize(d) else { print("could not write \(name)"); exit(1) }
    print("wrote \(name) \(image.width)x\(image.height)")
}
let logo = load(a[1]), mesh = load(a[2])
guard logo.width == 1024, logo.height == 1024, mesh.width == 1024, mesh.height == 1024 else { print("both sources must be 1024x1024"); exit(1) }
let full = CGRect(x: 0, y: 0, width: 1024, height: 1024)

let light = canvas(1024, alpha: false); light.draw(mesh, in: full); light.draw(logo, in: full)
let lightImage = light.makeImage()!
save(lightImage, "AppIcon-1024.png")

let dark = canvas(1024, alpha: true); dark.clear(full); dark.draw(logo, in: full)
save(dark.makeImage()!, "AppIcon-Dark-1024.png")

let gray = CGContext(data: nil, width: 1024, height: 1024, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceGray(),
                     bitmapInfo: CGImageAlphaInfo.none.rawValue)!
gray.setFillColor(gray: 0, alpha: 1); gray.fill(full); gray.draw(logo, in: full)
save(gray.makeImage()!, "AppIcon-Tinted-1024.png")

let small = CGRect(x: 0, y: 0, width: 256, height: 256)
let mark = canvas(256, alpha: false); mark.draw(lightImage, in: small)
save(mark.makeImage()!, "BCDLogo.png")

let ground = CGGradient(colorsSpace: srgb, colors: [CGColor(srgbRed: 32 / 255, green: 32 / 255, blue: 32 / 255, alpha: 1),
                                                    CGColor(srgbRed: 14 / 255, green: 14 / 255, blue: 14 / 255, alpha: 1)] as CFArray,
                        locations: [0, 1])!
let darkMark = canvas(256, alpha: false)
darkMark.drawLinearGradient(ground, start: CGPoint(x: 128, y: 256), end: CGPoint(x: 128, y: 0), options: [])
darkMark.draw(logo, in: small)
let darkMarkImage = darkMark.makeImage()!

let readme = canvas(256, alpha: true); readme.clear(small)
readme.addPath(CGPath(roundedRect: small, cornerWidth: 256 * 0.2237, cornerHeight: 256 * 0.2237, transform: nil)); readme.clip()
readme.draw(darkMarkImage, in: small)
save(readme.makeImage()!, "readme-icon-256.png")
