import CoreGraphics
import Foundation
import ScreenCaptureKit

// usage: two_clients rect|filter <hold seconds>
// Takes one ScreenCaptureKit screenshot of the main display, prints how long it took, then stays alive.

let mode = CommandLine.arguments.dropFirst().first ?? "rect"
let hold = Double(CommandLine.arguments.dropFirst(2).first ?? "0") ?? 0
let started = Date()

func elapsed() -> String { String(format: "%.0f ms", Date().timeIntervalSince(started) * 1000) }

let work = Task { () -> String in
    do {
        if mode == "rect" {
            let image: CGImage = try await SCScreenshotManager.captureImage(in: CGDisplayBounds(CGMainDisplayID()))
            return "ok \(image.width)x\(image.height)"
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else { return "no display" }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.width = display.width
        config.height = display.height
        let image: CGImage = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        return "ok \(image.width)x\(image.height)"
    } catch {
        return "error \(error)"
    }
}

let watchdog = Task {
    try await Task.sleep(nanoseconds: 15_000_000_000)
    print("pid \(getpid()) \(mode): NO RESULT after \(elapsed())")
    fflush(stdout)
}

let result = await work.value
watchdog.cancel()
print("pid \(getpid()) \(mode): \(result) after \(elapsed())")
fflush(stdout)
if hold > 0 { try? await Task.sleep(nanoseconds: UInt64(hold * 1_000_000_000)) }
