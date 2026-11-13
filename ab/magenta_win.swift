import AppKit
// usage: magenta_win <x pt from left> <y pt from top> <seconds>: a 200x200 pt magenta window on the main display.
let args = CommandLine.arguments
let x = Double(args[1])!, y = Double(args[2])!, hold = Double(args[3])!
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let screen = NSScreen.screens[0]
let window = NSWindow(contentRect: CGRect(x: screen.frame.minX + x, y: screen.frame.maxY - y - 200, width: 200, height: 200), styleMask: .borderless, backing: .buffered, defer: false)
window.backgroundColor = NSColor(srgbRed: 1, green: 0, blue: 1, alpha: 1)
window.level = .screenSaver
window.orderFrontRegardless()
print("shown"); fflush(stdout)
DispatchQueue.main.asyncAfter(deadline: .now() + hold) { exit(0) }
app.run()
