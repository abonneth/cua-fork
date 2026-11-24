import AppKit
import ApplicationServices

// Usage: probe <pid> <state.json|-> <label>
// Replays set_value's AXValue write on upstream main: a CFNumber first when the
// value parses as f64, a CFString only if that write fails.

print("AXIsProcessTrusted=\(AXIsProcessTrusted())")
guard CommandLine.arguments.count >= 4, let pid = pid_t(CommandLine.arguments[1]), pid > 0 else {
    exit(0)
}
let statePath = CommandLine.arguments[2]
let label = CommandLine.arguments[3]
let root = AXUIElementCreateApplication(pid)
AXUIElementSetAttributeValue(root, "AXManualAccessibility" as CFString, kCFBooleanTrue)
AXUIElementSetAttributeValue(root, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)

func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}

func text(_ element: AXUIElement, _ name: String) -> String? {
    attr(element, name) as? String
}

func describe(_ value: CFTypeRef?) -> String {
    guard let value else { return "nil" }
    let type = CFGetTypeID(value)
    if type == CFStringGetTypeID() { return "CFString(\"\(value as! String)\")" }
    if type == CFBooleanGetTypeID() { return "CFBoolean(\(value as! Bool))" }
    if type == CFNumberGetTypeID() { return "CFNumber(\(value as! NSNumber))" }
    return "type#\(type)"
}

func all(_ element: AXUIElement, depth: Int = 0, into out: inout [AXUIElement]) {
    guard depth < 60, out.count < 20000 else { return }
    out.append(element)
    for child in (attr(element, kAXChildrenAttribute) as? [AXUIElement]) ?? [] {
        all(child, depth: depth + 1, into: &out)
    }
}

func appState() -> [String: Any] {
    guard statePath != "-",
          let data = FileManager.default.contents(atPath: statePath),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return [:] }
    return object
}

var elements: [AXUIElement] = []
var targets: [(String, AXUIElement)] = []
for attempt in 0..<30 {
    elements = []
    all(root, into: &elements)
    targets = []
    for element in elements {
        let identifier = text(element, "AXIdentifier") ?? ""
        let role = text(element, kAXRoleAttribute) ?? ""
        let names = [text(element, "AXDescription"), text(element, "AXTitle")].compactMap { $0 }
        if ["ns", "sw", "slider", "stepper"].contains(identifier) {
            targets.append((identifier, element))
        } else if role == "AXTextField", names.contains("webfield") {
            targets.append(("web", element))
        }
    }
    if targets.contains(where: { $0.0 == "web" }) { break }
    print("attempt \(attempt): \(elements.count) elements, targets \(targets.map { $0.0 })")
    sleep(1)
}
print("targets: \(targets.map { "\($0.0)=\(text($0.1, kAXRoleAttribute) ?? "?")" })")

for (name, element) in targets {
    let values = ["slider", "stepper"].contains(name) ? ["42"] : ["cua-text", "007", "0612345678", "1.50"]
    for value in values {
        let before = describe(attr(element, kAXValueAttribute))
        var numberWrite = "skipped"
        var stringWrite = "skipped"
        var numberOk = false
        if let number = Double(value.trimmingCharacters(in: .whitespacesAndNewlines)) {
            let error = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, NSNumber(value: number))
            numberWrite = "\(error.rawValue)"
            numberOk = error == .success
        }
        if !numberOk {
            let error = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, value as CFString)
            stringWrite = "\(error.rawValue)"
        }
        usleep(1_000_000)
        let after = describe(attr(element, kAXValueAttribute))
        let shown = appState()[name].map { "\($0)" } ?? "n/a"
        print("PROBE \(label) \(name) value=\"\(value)\" number_write=\(numberWrite) string_write=\(stringWrite) ax_before=\(before) ax_after=\(after) app_state=\(shown)")
    }
}
