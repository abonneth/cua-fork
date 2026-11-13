import AppKit
import CoreGraphics
import ScreenCaptureKit

// Does a display SCContentFilter built before a window existed still capture that window?
// Also: does a window excluded by the cached filter stay excluded, and a display filter survive n reuses.

func pixel(_ image: CGImage, _ x: Int, _ y: Int) -> (Int, Int, Int) {
    var px = [UInt8](repeating: 0, count: 4)
    let ctx = CGContext(data: &px, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(image, in: CGRect(x: -x, y: -(image.height - 1 - y), width: image.width, height: image.height))
    return (Int(px[0]), Int(px[1]), Int(px[2]))
}

func isMagenta(_ p: (Int, Int, Int)) -> Bool { p.0 > 200 && p.1 < 80 && p.2 > 200 }

@MainActor func makeWindow(_ rect: CGRect) -> NSWindow {
    let window = NSWindow(contentRect: rect, styleMask: .borderless, backing: .buffered, defer: false)
    window.backgroundColor = NSColor(srgbRed: 1, green: 0, blue: 1, alpha: 1)
    window.level = .screenSaver
    window.isReleasedWhenClosed = false
    window.orderFrontRegardless()
    return window
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

Task { @MainActor in
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let mainID = CGMainDisplayID()
        let display = content.displays.first { $0.displayID == mainID }!
        let cached = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        let scale = Int(cached.pointPixelScale)
        config.width = display.width * scale
        config.height = display.height * scale
        config.showsCursor = false

        // Window appears at top-left 200x200 pt at (300, 300) from the top-left corner.
        let screen = NSScreen.screens.first { ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID) == mainID }!
        let rect = CGRect(x: screen.frame.minX + 300, y: screen.frame.maxY - 500, width: 200, height: 200)
        let probeX = (300 + 100) * scale, probeY = (300 + 100) * scale

        let before = try await SCScreenshotManager.captureImage(contentFilter: cached, configuration: config)
        print("before the window exists: magenta=\(isMagenta(pixel(before, probeX, probeY)))")

        let window = makeWindow(rect)
        try await Task.sleep(nanoseconds: 600_000_000)
        let afterCached = try await SCScreenshotManager.captureImage(contentFilter: cached, configuration: config)
        print("window opened after the filter was built, cached filter: magenta=\(isMagenta(pixel(afterCached, probeX, probeY)))")

        let fresh = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let scWindow = fresh.windows.first { $0.windowID == CGWindowID(window.windowNumber) }
        print("fresh shareable content lists the window: \(scWindow != nil)")
        if let scWindow {
            let excluding = SCContentFilter(display: display, excludingWindows: [scWindow])
            let excluded = try await SCScreenshotManager.captureImage(contentFilter: excluding, configuration: config)
            print("filter excluding the window (display object from the first query): magenta=\(isMagenta(pixel(excluded, probeX, probeY)))")
            // Move the window; the same excluding filter must still leave it out.
            window.setFrameOrigin(CGPoint(x: rect.minX + 400, y: rect.minY))
            try await Task.sleep(nanoseconds: 600_000_000)
            let moved = try await SCScreenshotManager.captureImage(contentFilter: excluding, configuration: config)
            print("same excluding filter after the window moved 400 pt right: magenta at new spot=\(isMagenta(pixel(moved, probeX + 400 * scale, probeY)))")
            let movedCached = try await SCScreenshotManager.captureImage(contentFilter: cached, configuration: config)
            print("cached plain filter after the move: magenta at new spot=\(isMagenta(pixel(movedCached, probeX + 400 * scale, probeY))) at old spot=\(isMagenta(pixel(movedCached, probeX, probeY)))")
        }
        window.close()
        try await Task.sleep(nanoseconds: 600_000_000)
        let closed = try await SCScreenshotManager.captureImage(contentFilter: cached, configuration: config)
        print("after the window closed, cached filter: magenta=\(isMagenta(pixel(closed, probeX + 400 * scale, probeY)))")
    } catch {
        print("error \(error)")
    }
    exit(0)
}
app.run()
