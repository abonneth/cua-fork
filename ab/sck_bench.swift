import CoreGraphics
import Darwin
import Foundation
import ScreenCaptureKit

// usage: sck_bench <calls per round> <rounds>
// Times the parts of a main-display screenshot through ScreenCaptureKit, three ways, in alternating rounds:
//   fresh  = shareable-content query + filter + capture on every call
//   cached = display and filter built once, capture only
//   rect   = captureImage(in:)

func ms(_ start: UInt64) -> Double { Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000 }
func now() -> UInt64 { DispatchTime.now().uptimeNanoseconds }
func p(_ values: [Double], _ q: Double) -> Double {
    let sorted = values.sorted()
    return sorted.isEmpty ? -1 : sorted[min(sorted.count - 1, Int(Double(sorted.count) * q))]
}
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

let calls = Int(CommandLine.arguments.dropFirst().first ?? "20") ?? 20
let rounds = Int(CommandLine.arguments.dropFirst(2).first ?? "3") ?? 3
let mainID = CGMainDisplayID()
let pixelWidth = CGDisplayPixelsWide(mainID)
let pixelHeight = CGDisplayPixelsHigh(mainID)
let mode = CGDisplayCopyDisplayMode(mainID)
let nativeWidth = mode?.pixelWidth ?? pixelWidth
let nativeHeight = mode?.pixelHeight ?? pixelHeight

func configuration() -> SCStreamConfiguration {
    let config = SCStreamConfiguration()
    config.width = nativeWidth
    config.height = nativeHeight
    config.showsCursor = false
    return config
}

func content() async throws -> SCShareableContent {
    try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
}

func display(in content: SCShareableContent) -> SCDisplay {
    content.displays.first(where: { $0.displayID == mainID }) ?? content.displays[0]
}

var query: [Double] = [], freshCapture: [Double] = [], freshTotal: [Double] = []
var cachedCapture: [Double] = [], rectCapture: [Double] = []
var windowCounts: [Int] = []

let first = try await content()
let cachedDisplay = display(in: first)
let cachedFilter = SCContentFilter(display: cachedDisplay, excludingWindows: [])
if #available(macOS 14.2, *) { cachedFilter.includeMenuBar = true }
let cachedConfig = configuration()
print("display \(nativeWidth)x\(nativeHeight) px, \(first.windows.count) on-screen windows, macOS \(ProcessInfo.processInfo.operatingSystemVersionString)")
print("start footprint \(String(format: "%.1f", footprintMB())) MB")

for round in 1...rounds {
    for _ in 0..<calls {
        let t0 = now()
        let shareable = try await content()
        let q = ms(t0)
        let filter = SCContentFilter(display: display(in: shareable), excludingWindows: [])
        if #available(macOS 14.2, *) { filter.includeMenuBar = true }
        let t1 = now()
        let image: CGImage = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration())
        freshCapture.append(ms(t1))
        freshTotal.append(ms(t0))
        query.append(q)
        windowCounts.append(shareable.windows.count)
        precondition(image.width == nativeWidth)
    }
    let afterFresh = footprintMB()
    for _ in 0..<calls {
        let t0 = now()
        let image: CGImage = try await SCScreenshotManager.captureImage(contentFilter: cachedFilter, configuration: cachedConfig)
        cachedCapture.append(ms(t0))
        precondition(image.width == nativeWidth)
    }
    let afterCached = footprintMB()
    if #available(macOS 15.2, *) {
        for _ in 0..<calls {
            let t0 = now()
            let image: CGImage = try await SCScreenshotManager.captureImage(in: CGDisplayBounds(mainID))
            rectCapture.append(ms(t0))
            precondition(image.width > 0)
        }
    }
    print(String(format: "round %d footprint: after fresh %.1f MB, after cached %.1f MB, after rect %.1f MB", round, afterFresh, afterCached, footprintMB()))
}

func row(_ label: String, _ values: [Double]) {
    print(String(format: "%-34@ n=%3d  p50=%6.1f ms  p90=%6.1f ms  min=%6.1f ms", label as NSString, values.count, p(values, 0.5), p(values, 0.9), values.min() ?? -1))
}
row("fresh: shareable-content query", query)
row("fresh: capture", freshCapture)
row("fresh: total", freshTotal)
row("cached filter: capture", cachedCapture)
row("captureImage(in:)", rectCapture)
