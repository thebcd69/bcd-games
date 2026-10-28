import Foundation
import Metal

// Render through DXMT's actual present fragment function. Read back floating
// pixels; this checks HDR values numerically without requiring an HDR monitor.
guard let device = MTLCreateSystemDefaultDevice() else {
    print("SKIP: no Metal GPU")
    exit(0)
}
let library = try device.makeLibrary(URL: URL(fileURLWithPath: CommandLine.arguments[1]))
let queue = device.makeCommandQueue()!

func render(_ pixels: [SIMD4<Float>], peak: Float, srgb: Bool = false,
            scaled: Bool = false, gamma: Bool = false, flags: UInt32 = 0) throws -> [Float] {
    let sourceFormat: MTLPixelFormat = srgb ? .rgba8Unorm_srgb : .rgba32Float
    let desc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: sourceFormat,
        width: pixels.count, height: 1, mipmapped: false)
    desc.storageMode = .shared
    desc.usage = .shaderRead
    let source = device.makeTexture(descriptor: desc)!
    if srgb {
        let bytes = pixels.flatMap { p in (0..<4).map { UInt8((p[$0] * 255).rounded()) } }
        bytes.withUnsafeBytes { source.replace(region: MTLRegionMake2D(0, 0, pixels.count, 1),
            mipmapLevel: 0, withBytes: $0.baseAddress!, bytesPerRow: pixels.count * 4) }
    } else {
        pixels.withUnsafeBytes { source.replace(region: MTLRegionMake2D(0, 0, pixels.count, 1),
            mipmapLevel: 0, withBytes: $0.baseAddress!, bytesPerRow: pixels.count * 16) }
    }
    let constants = MTLFunctionConstantValues()
    for (index, value) in [!scaled, false, false, srgb, false, gamma].enumerated() {
        var b = value
        constants.setConstantValue(&b, type: .bool, index: 0x100 + index)
    }
    let pipeline = MTLRenderPipelineDescriptor()
    pipeline.vertexFunction = library.makeFunction(name: "vs_present_quad")!
    pipeline.fragmentFunction = try library.makeFunction(name: "fs_present_quad", constantValues: constants)
    pipeline.colorAttachments[0].pixelFormat = .rgba16Float
    let state = try device.makeRenderPipelineState(descriptor: pipeline)
    let targetDesc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .rgba16Float,
        width: pixels.count, height: 1, mipmapped: false)
    targetDesc.storageMode = .shared
    targetDesc.usage = .renderTarget
    let target = device.makeTexture(descriptor: targetDesc)!
    let pass = MTLRenderPassDescriptor()
    pass.colorAttachments[0].texture = target
    pass.colorAttachments[0].loadAction = .dontCare
    pass.colorAttachments[0].storeAction = .store
    let command = queue.makeCommandBuffer()!
    let encoder = command.makeRenderCommandEncoder(descriptor: pass)!
    encoder.setRenderPipelineState(state)
    encoder.setFragmentTexture(source, index: 0)
    if gamma {
        // Identity gamma ramp. Its last entry must still produce the HDR peak.
        let lutDesc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .rgba32Float,
            width: 1024, height: 1, mipmapped: false)
        lutDesc.storageMode = .shared
        let lut = device.makeTexture(descriptor: lutDesc)!
        let values = (0..<1024).map { SIMD4<Float>(repeating: Float($0) / 1023) }
        values.withUnsafeBytes { lut.replace(region: MTLRegionMake2D(0, 0, 1024, 1),
            mipmapLevel: 0, withBytes: $0.baseAddress!, bytesPerRow: 1024 * 16) }
        encoder.setFragmentTexture(lut, index: 1)
    }
    // Four float fields followed by two uint32 fields, matching the CPU metadata.
    let metadata: [Float] = [1, 10000, 800, peak, Float(bitPattern: flags), Float(bitPattern: 1)]
    metadata.withUnsafeBytes { encoder.setFragmentBytes($0.baseAddress!, length: $0.count, index: 0) }
    encoder.setViewport(MTLViewport(originX: 0, originY: 0, width: Double(pixels.count), height: 1, znear: 0, zfar: 1))
    encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
    encoder.endEncoding()
    command.commit()
    command.waitUntilCompleted()
    precondition(command.status == .completed, "GPU failure: \(String(describing: command.error))")
    var bits = [UInt16](repeating: 0, count: pixels.count * 4)
    bits.withUnsafeMutableBytes { target.getBytes($0.baseAddress!, bytesPerRow: pixels.count * 8,
        from: MTLRegionMake2D(0, 0, pixels.count, 1), mipmapLevel: 0) }
    return bits.map { Float(Float16(bitPattern: $0)) }
}

func near(_ actual: Float, _ expected: Float, _ label: String, tolerance: Float = 0.004) {
    precondition(actual.isFinite && abs(actual - expected) < tolerance,
                 "\(label): got \(actual), expected \(expected)")
}

let ramp = (0...255).map { SIMD4<Float>(Float($0) / 255, Float($0) / 255, Float($0) / 255, 1) }
for peak: Float in [1, 1.25, 2, 8] {
    let output = try render(ramp, peak: peak)
    near(output[0], 0, "black")
    near(output[255 * 4], peak, "white reaches peak")
    near(output[128 * 4], 0.21586, "midtone preserved")
    for i in 1...255 {
        precondition(output[i * 4] >= output[(i - 1) * 4], "non-monotonic ramp")
        precondition(output[i * 4] <= peak + 0.005, "headroom exceeded")
        near(output[i * 4 + 3], 1, "alpha")
    }
}
let off = try render(ramp, peak: 0)
near(off[128 * 4], 128.0 / 255, "disabled path unchanged")
let encoded = try render(ramp, peak: 2, srgb: true)
let unorm = try render(ramp, peak: 2)
for i in stride(from: 0, to: encoded.count, by: 4) {
    near(encoded[i], unorm[i], "sRGB texture decoded exactly once")
}
let colors: [SIMD4<Float>] = [.init(1, 0.5, 0.25, 0.5), .init(0, 1, 0, 1), .init(1, 1, 1, 1)]
let color = try render(colors, peak: 2)
near(color[0], 2, "red peak")
near(color[1] / color[0], 0.21404, "hue ratio green/red")
near(color[2] / color[0], 0.05088, "hue ratio blue/red")
near(color[3], 0.5, "fractional alpha preserved")
near(color[5], 2, "saturated green highlight")
let scaled = try render(colors, peak: 2, scaled: true)
near(scaled[8], 2, "scaled present specialization")
let gamma = try render(colors, peak: 2, gamma: true)
near(gamma[8], 2, "gamma ramp remains compatible")
print("PASS: actual Metal present shader: ramps, SDR fallback, sRGB, hue, alpha, scaling and gamma")

let bands = [SIMD4<Float>](repeating: .init(0, 0, 0, 1), count: 5)
for limit: Float in [1, 1.25, 2] {
    let pattern = try render(bands, peak: limit, flags: 2)
    for (i, expected) in [Float(0.18), 1, 1.25, 1.5, 2].enumerated() {
        near(pattern[i * 4], min(expected, limit), "test pattern band")
    }
}
let bypass = try render(bands, peak: 0, flags: 2)
near(bypass[8], 0, "pattern cannot replace unsupported/native HDR content")
print("PASS: actual live comparison and five-band HDR calibration shader")
