import CoreGraphics
import Foundation

setvbuf(stdout, nil, _IOLBF, 0)
signal(SIGTERM) { _ in exit(0) }
let interval = Double(CommandLine.arguments.dropFirst().first ?? "0.04") ?? 0.04
let source = CGEventSource(stateID: .hidSystemState)
while true {
    CGEvent(keyboardEventSource: source, virtualKey: 7, keyDown: true)?.post(tap: .cghidEventTap)
    CGEvent(keyboardEventSource: source, virtualKey: 7, keyDown: false)?.post(tap: .cghidEventTap)
    print("x")
    Thread.sleep(forTimeInterval: interval)
}
