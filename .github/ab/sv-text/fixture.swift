import AppKit
import SwiftUI
import WebKit

final class Model: ObservableObject {
    @Published var text = ""
}

struct Field: View {
    @ObservedObject var model: Model
    var body: some View {
        TextField("swiftui", text: $model.text)
            .accessibilityIdentifier("sw")
            .frame(width: 240)
    }
}

let statePath = CommandLine.arguments[1]
let model = Model()
let app = NSApplication.shared
app.setActivationPolicy(.regular)

let window = NSWindow(
    contentRect: NSRect(x: 100, y: 100, width: 560, height: 420),
    styleMask: [.titled, .closable],
    backing: .buffered,
    defer: false
)
window.title = "SV Probe"

let ns = NSTextField(string: "")
ns.setAccessibilityIdentifier("ns")
let slider = NSSlider(value: 0, minValue: 0, maxValue: 100, target: nil, action: nil)
slider.setAccessibilityIdentifier("slider")
let stepper = NSStepper()
stepper.minValue = 0
stepper.maxValue = 100
stepper.increment = 1
stepper.setAccessibilityIdentifier("stepper")
let host = NSHostingView(rootView: Field(model: model))
let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 480, height: 80))
web.loadHTMLString(
    "<html><body><input id='web' aria-label='webfield' style='width:240px'></body></html>",
    baseURL: nil
)

let stack = NSStackView()
stack.orientation = .vertical
stack.alignment = .leading
stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
for view in [ns, host, web, slider, stepper] as [NSView] {
    stack.addArrangedSubview(view)
}
NSLayoutConstraint.activate([
    ns.widthAnchor.constraint(equalToConstant: 240),
    slider.widthAnchor.constraint(equalToConstant: 240),
    web.widthAnchor.constraint(equalToConstant: 480),
    web.heightAnchor.constraint(equalToConstant: 80),
])
window.contentView = stack
window.makeKeyAndOrderFront(nil)
app.activate(ignoringOtherApps: true)

Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
    web.evaluateJavaScript("document.getElementById('web').value") { value, _ in
        let state: [String: Any] = [
            "ns": ns.stringValue,
            "sw": model.text,
            "web": (value as? String) ?? "<none>",
            "slider": slider.doubleValue,
            "stepper": stepper.doubleValue,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: state) {
            try? data.write(to: URL(fileURLWithPath: statePath))
        }
    }
}
app.run()
