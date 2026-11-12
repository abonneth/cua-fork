// Drawing a large CGImage into a bitmap context makes CoreGraphics cache a converted copy of
// its pixels in the purgeable malloc zone. On Apple silicon that allocation can be served from
// a block the process freed earlier; the block is not purgeable, so the copy stays resident.
//
//   swiftc -Onone draw_copy_repro.swift -o draw_copy_repro
//   ./draw_copy_repro draw 20     footprint grows with every image
//   ./draw_copy_repro vimage 20   footprint stays flat
import Accelerate
import CoreGraphics
import Darwin
import Foundation

func footprintMB() -> Double {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
    _ = withUnsafeMutablePointer(to: &info) {
        $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
        }
    }
    return Double(info.phys_footprint) / 1_048_576
}

@inline(never)
func scratch(_ bytes: Int) -> UnsafeMutableRawPointer {
    let p = calloc(bytes, 1)!
    memset(p, 7, bytes)
    return p
}

let mode = CommandLine.arguments[1]
let count = Int(CommandLine.arguments[2])!
let width = 3420, height = 2214
let pixels = width * height * 4

func makeImage(_ seed: UInt8) -> CGImage {
    let bytes = UnsafeMutablePointer<UInt8>.allocate(capacity: pixels)
    memset(bytes, Int32(seed), pixels)
    let provider = CGDataProvider(dataInfo: nil, data: bytes, size: pixels) { _, data, _ in
        data.deallocate()
    }!
    return CGImage(
        width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
        space: CGColorSpace(name: CGColorSpace.displayP3)!,
        bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue),
        provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
}

func readRGBA(_ image: CGImage, into buffer: UnsafeMutableRawPointer) {
    if mode == "vimage" {
        var format = vImage_CGImageFormat(
            bitsPerComponent: 8, bitsPerPixel: 32, colorSpace: Unmanaged.passRetained(CGColorSpaceCreateDeviceRGB()),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue), version: 0, decode: nil,
            renderingIntent: .defaultIntent)
        var dest = vImage_Buffer(data: buffer, height: vImagePixelCount(height), width: vImagePixelCount(width), rowBytes: width * 4)
        precondition(vImageBuffer_InitWithCGImage(&dest, &format, nil, image, vImage_Flags(kvImageNoAllocate)) == kvImageNoError)
        format.colorSpace.release()
    } else {
        let context = CGContext(
            data: buffer, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setBlendMode(.copy)
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
    }
}

print(String(format: "%@, %dx%d, macOS %@, start %.0f MB", mode, width, height, ProcessInfo.processInfo.operatingSystemVersionString, footprintMB()))
for index in 1...count {
    autoreleasepool {
        let image = makeImage(UInt8(truncatingIfNeeded: index))
        let rgba = malloc(pixels)!
        readRGBA(image, into: rgba)
        let copy = scratch(pixels)
        free(copy)
        free(rgba)
        // What an image pipeline does next: a decoded copy and a larger resize buffer, both freed.
        let decoded = scratch(pixels)
        let resize = scratch(width * (height * 2 / 5) * 16)
        free(resize)
        free(decoded)
    }
    if index % 5 == 0 { print(String(format: "after %2d images: footprint %.0f MB", index, footprintMB())) }
}
