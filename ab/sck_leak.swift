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

func liveImages() -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/heap")
    p.arguments = ["\(getpid())"]
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = Pipe()
    do { try p.run() } catch { return "heap failed" }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    let text = String(data: data, encoding: .utf8) ?? ""
    var counts: [String] = []
    for name in ["CGImage", "IOSurface"] {
        var n = "0"
        for line in text.split(separator: "\n") {
            let parts = line.split(separator: " ", omittingEmptySubsequences: true)
            if parts.count >= 4, parts[3] == name { n = String(parts[0]) }
        }
        counts.append("\(name)=\(n)")
    }
    return counts.joined(separator: " ")
}

func render(_ image: CGImage) -> Int {
    let w = image.width, h = image.height
    guard let ctx = CGContext(
        data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return 0 }
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    return w * h
}

func report(_ label: String) {
    print("\(label.padding(toLength: 54, withPad: " ", startingAt: 0)) footprint=\(String(format: "%7.1f", footprintMB())) MB  \(liveImages())")
}

let n = Int(CommandLine.arguments.dropFirst().first ?? "10") ?? 10
let draw = !CommandLine.arguments.contains("--no-render")
let bounds = CGDisplayBounds(CGMainDisplayID())

func phase(_ label: String, _ capture: () async throws -> CGImage) async {
    var ok = 0
    var firstError = ""
    for _ in 0..<n {
        do {
            let image = try await capture()
            if draw { _ = render(image) }
            ok += 1
        } catch {
            if firstError.isEmpty { firstError = "\(error)" }
        }
    }
    try? await Task.sleep(nanoseconds: 2_000_000_000)
    report("\(n)x \(label) (ok \(ok))")
    if !firstError.isEmpty { print("   first error: \(firstError)") }
}

report("start")

let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
guard let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) ?? content.displays.first else {
    print("no display")
    exit(2)
}
let filter = SCContentFilter(display: display, excludingWindows: [])
let config = SCStreamConfiguration()
config.width = Int(CGFloat(display.width) * CGFloat(filter.pointPixelScale))
config.height = Int(CGFloat(display.height) * CGFloat(filter.pointPixelScale))
config.showsCursor = false
report("after shareable content")

await phase("captureImage(contentFilter:configuration:)") {
    try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
}
await phase("captureImage(contentFilter:configuration:)") {
    try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
}
if #available(macOS 15.2, *) {
    await phase("captureImage(in:) async") {
        try await SCScreenshotManager.captureImage(in: bounds)
    }
    await phase("captureImage(in:) async") {
        try await SCScreenshotManager.captureImage(in: bounds)
    }
    await phase("captureImage(in:completionHandler:)") {
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<CGImage, Error>) in
            SCScreenshotManager.captureImage(in: bounds) { image, error in
                if let image { c.resume(returning: image) } else { c.resume(throwing: error ?? CocoaError(.featureUnsupported)) }
            }
        }
    }
}
try? await Task.sleep(nanoseconds: 10_000_000_000)
report("idle 10s")
