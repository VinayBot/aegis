// Lifts the foreground subject out of a picture with Apple Vision (the same on-device model as
// "Lift subject" in Photos) and writes a transparent PNG. Nothing is downloaded or uploaded.
//
//   swift scripts/cutout.swift <input> <output.png>
import AppKit
import CoreImage
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write("usage: cutout.swift <input> <output.png>\n".data(using: .utf8)!)
    exit(2)
}
let input = URL(fileURLWithPath: args[1])
let output = URL(fileURLWithPath: args[2])

guard let source = CIImage(contentsOf: input) else {
    FileHandle.standardError.write("cannot read \(input.path)\n".data(using: .utf8)!)
    exit(1)
}

let request = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(ciImage: source)
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write("vision failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}
guard let observation = request.results?.first else {
    FileHandle.standardError.write("no subject found\n".data(using: .utf8)!)
    exit(1)
}

let maskBuffer = try observation.generateScaledMaskForImage(
    forInstances: observation.allInstances, from: handler)
let mask = CIImage(cvPixelBuffer: maskBuffer)

let clear = CIImage(color: .clear).cropped(to: source.extent)
let blend = CIFilter(name: "CIBlendWithMask")!
blend.setValue(source, forKey: kCIInputImageKey)
blend.setValue(clear, forKey: kCIInputBackgroundImageKey)
blend.setValue(mask, forKey: kCIInputMaskImageKey)
let result = blend.outputImage!

let context = CIContext()
guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else { exit(1) }
try context.writePNGRepresentation(
    of: result, to: output, format: .RGBA8, colorSpace: colorSpace)
print("wrote \(output.path) (\(observation.allInstances.count) instance(s))")
