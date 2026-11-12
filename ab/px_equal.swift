import Accelerate
import CoreGraphics
import Foundation
import ScreenCaptureKit

func captureSync(_ filter: SCContentFilter, _ config: SCStreamConfiguration) -> CGImage? {
    let sem = DispatchSemaphore(value: 0)
    var out: CGImage?
    SCScreenshotManager.captureImage(contentFilter: filter, configuration: config) { image, _ in out = image; sem.signal() }
    sem.wait()
    return out
}

func compare(_ label: String, _ image: CGImage) {
    let w = image.width, h = image.height
    var viaVImage = [UInt8](repeating: 0, count: w * h * 4)
    var format = vImage_CGImageFormat(bitsPerComponent: 8, bitsPerPixel: 32, colorSpace: Unmanaged.passRetained(CGColorSpaceCreateDeviceRGB()), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue), version: 0, decode: nil, renderingIntent: .defaultIntent)
    let t0 = DispatchTime.now().uptimeNanoseconds
    viaVImage.withUnsafeMutableBytes { raw in
        var dest = vImage_Buffer(data: raw.baseAddress, height: vImagePixelCount(h), width: vImagePixelCount(w), rowBytes: w * 4)
        let err = vImageBuffer_InitWithCGImage(&dest, &format, nil, image, vImage_Flags(kvImageNoAllocate))
        precondition(err == kvImageNoError)
    }
    let t1 = DispatchTime.now().uptimeNanoseconds
    let d0 = DispatchTime.now().uptimeNanoseconds
    var drawn = [UInt8](repeating: 0, count: w * h * 4)
    drawn.withUnsafeMutableBytes { raw in
        let ctx = CGContext(data: raw.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.setBlendMode(.copy)
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    let d1 = DispatchTime.now().uptimeNanoseconds
    let data = image.dataProvider!.data!
    let src = CFDataGetBytePtr(data)!
    let rowBytes = image.bytesPerRow
    var differing = 0, maxDelta = 0, translucent = 0
    for y in 0..<h {
        for x in 0..<w {
            let s = src + y * rowBytes + x * 4
            let d = (y * w + x) * 4
            let pairs = [(Int(s[2]), Int(drawn[d])), (Int(s[1]), Int(drawn[d + 1])), (Int(s[0]), Int(drawn[d + 2])), (Int(s[3]), Int(drawn[d + 3]))]
            if s[3] != 255 { translucent += 1 }
            var diff = false
            for (a, b) in pairs where a != b { diff = true; maxDelta = max(maxDelta, abs(a - b)) }
            if diff { differing += 1 }
        }
    }
    var vDiff = 0, vMax = 0, hist = [Int](repeating: 0, count: 4)
    for i in stride(from: 0, to: w * h * 4, by: 4) {
        var m = 0
        for c in 0..<4 { m = max(m, abs(Int(viaVImage[i + c]) - Int(drawn[i + c]))) }
        if m > 0 { vDiff += 1; vMax = max(vMax, m) }
        hist[min(m, 3)] += 1
    }
    print("  vImage vs draw: \(vDiff) pixels differ (max channel delta \(vMax); delta 0/1/2/3+ = \(hist)); vImage took \((t1 - t0) / 1_000_000) ms, draw took \((d1 - d0) / 1_000_000) ms")
    let name = image.colorSpace?.name.map { $0 as String } ?? "unnamed"
    print("\(label): \(w)x\(h) bpp=\(image.bitsPerPixel) bpc=\(image.bitsPerComponent) stride=\(rowBytes) bitmapInfo=\(image.bitmapInfo.rawValue) alpha=\(image.alphaInfo.rawValue) space=\(name) model=\(image.colorSpace?.model.rawValue ?? -1) dataLen=\(CFDataGetLength(data)) | pixels differing draw vs provider: \(differing) of \(w * h) (max channel delta \(maxDelta)), non-opaque pixels \(translucent)")
}

let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() })!
let filter = SCContentFilter(display: display, excludingWindows: [])
filter.includeMenuBar = true
let config = SCStreamConfiguration()
config.width = Int(CGFloat(display.width) * CGFloat(filter.pointPixelScale))
config.height = Int(CGFloat(display.height) * CGFloat(filter.pointPixelScale))
config.showsCursor = false
compare("display", captureSync(filter, config)!)

let windows = content.windows.filter { $0.windowLayer == 0 && $0.frame.width > 300 && $0.frame.height > 200 && $0.isOnScreen }
for window in windows.prefix(2) {
    let wf = SCContentFilter(desktopIndependentWindow: window)
    let wc = SCStreamConfiguration()
    wc.width = Int(wf.contentRect.width * CGFloat(wf.pointPixelScale))
    wc.height = Int(wf.contentRect.height * CGFloat(wf.pointPixelScale))
    wc.showsCursor = false
    wc.ignoreShadowsSingleWindow = true
    if let image = captureSync(wf, wc) { compare("window \(window.owningApplication?.applicationName ?? "?")", image) }
}
