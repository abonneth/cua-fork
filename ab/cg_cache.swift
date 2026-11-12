import Accelerate
import CoreGraphics
import Darwin
import Foundation
import ScreenCaptureKit

func footprintMB() -> Double {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
    let kr = withUnsafeMutablePointer(to: &info) {
        $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
        }
    }
    return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : -1
}

enum Read: String { case drawRGBA, drawBGRA, drawNative, provider, vimage }

nonisolated(unsafe) var checksum: UInt64 = 0

@inline(never)
func touched(_ bytes: Int) -> UnsafeMutableRawPointer {
    let p = calloc(bytes, 1)!
    memset(p, Int32(truncatingIfNeeded: bytes >> 12), bytes)
    checksum &+= UInt64(p.load(fromByteOffset: bytes - 1, as: UInt8.self))
    return p
}

func read(_ image: CGImage, _ how: Read, _ work: Bool) {
    let w = image.width, h = image.height
    let pixels = w * h * 4
    var buffer: UnsafeMutableRawPointer? = nil
    if how == .provider {
        let data = image.dataProvider!.data!
        precondition(CFDataGetLength(data) >= image.bytesPerRow * (h - 1) + w * 4)
        buffer = malloc(pixels)!
        memcpy(buffer, CFDataGetBytePtr(data), pixels)
    } else if how == .vimage {
        buffer = malloc(pixels)!
        var format = vImage_CGImageFormat(bitsPerComponent: 8, bitsPerPixel: 32, colorSpace: Unmanaged.passRetained(CGColorSpaceCreateDeviceRGB()), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue), version: 0, decode: nil, renderingIntent: .defaultIntent)
        var dest = vImage_Buffer(data: buffer, height: vImagePixelCount(h), width: vImagePixelCount(w), rowBytes: w * 4)
        let err = vImageBuffer_InitWithCGImage(&dest, &format, nil, image, vImage_Flags(kvImageNoAllocate))
        format.colorSpace.release()
        precondition(err == kvImageNoError, "vImage error \(err)")
    } else {
        buffer = malloc(pixels)!
        let space: CGColorSpace = how == .drawNative ? image.colorSpace! : CGColorSpaceCreateDeviceRGB()
        let info: UInt32 = how == .drawRGBA
            ? CGImageAlphaInfo.premultipliedLast.rawValue
            : CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
        let ctx = CGContext(data: buffer, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: space, bitmapInfo: info)!
        ctx.setBlendMode(.copy)
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    guard work else { free(buffer); return }
    let encodeInput = touched(pixels)
    free(encodeInput)
    free(buffer)
    let decoded = touched(pixels)
    let resized = touched(w * (h * 2 / 5) * 16)
    free(resized)
    free(decoded)
}

func captureSync(_ filter: SCContentFilter, _ config: SCStreamConfiguration) -> CGImage? {
    let sem = DispatchSemaphore(value: 0)
    var out: CGImage?
    SCScreenshotManager.captureImage(contentFilter: filter, configuration: config) { image, _ in
        out = image
        sem.signal()
    }
    sem.wait()
    return out
}

let args = CommandLine.arguments
let n = Int(args[1])!
let plan = args.dropFirst(2)
let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() })!
let filter = SCContentFilter(display: display, excludingWindows: [])
filter.includeMenuBar = true
let config = SCStreamConfiguration()
config.width = Int(CGFloat(display.width) * CGFloat(filter.pointPixelScale))
config.height = Int(CGFloat(display.height) * CGFloat(filter.pointPixelScale))
config.showsCursor = false
let scratchBytes = config.width * (config.height * 2 / 5) * 16
print("display \(config.width)x\(config.height), scratch buffer \(scratchBytes) bytes, macOS \(ProcessInfo.processInfo.operatingSystemVersionString)")

if let gate = ProcessInfo.processInfo.environment["GATE"] {
    print("pid \(getpid())"); fflush(stdout)
    while !FileManager.default.fileExists(atPath: gate) { usleep(200_000) }
}
for step in plan {
    if step.hasPrefix("idle") {
        let seconds = UInt32(step.dropFirst(4)) ?? 20
        sleep(seconds)
        print(String(format: "idle %d s: footprint %.1f MB", seconds, footprintMB()))
        continue
    }
    let parts = step.split(separator: "+")
    let how = Read(rawValue: String(parts[0]))!
    let withScratch = parts.count > 1
    let before = footprintMB()
    for _ in 0..<n {
        let done = DispatchSemaphore(value: 0)
        Thread {
            if let image = captureSync(filter, config) { read(image, how, withScratch) }
            done.signal()
        }.start()
        done.wait()
    }
    usleep(2_000_000)
    let after = footprintMB()
    _ = checksum
    print(String(format: "%dx %@: footprint %.1f -> %.1f MB (%+.1f per call)", n, step, before, after, (after - before) / Double(n)))
}
