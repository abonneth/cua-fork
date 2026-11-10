import AppKit

setvbuf(stdout, nil, _IOLBF, 0)
_ = NSApplication.shared
NSApp.setActivationPolicy(.prohibited)

func now() -> String { String(format: "%.4f", Date().timeIntervalSince1970) }

NSWorkspace.shared.notificationCenter.addObserver(
    forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
) { note in
    let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
    print("\(now())\tactive\t\(app?.localizedName ?? "?")")
}

var lastFront = ""
var lastTop = ""
Timer.scheduledTimer(withTimeInterval: 0.003, repeats: true) { _ in
    let front = NSWorkspace.shared.frontmostApplication?.localizedName ?? "(none)"
    if front != lastFront {
        lastFront = front
        print("\(now())\tfront\t\(front)")
    }
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    let list = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
    let top = list.first { window in
        let layer = window[kCGWindowLayer as String] as? Int ?? -1
        let bounds = window[kCGWindowBounds as String] as? [String: CGFloat] ?? [:]
        return layer == 0 && (bounds["Height"] ?? 0) > 60 && (bounds["Width"] ?? 0) > 60
    }
    let owner = top?[kCGWindowOwnerName as String] as? String ?? "(none)"
    if owner != lastTop {
        lastTop = owner
        print("\(now())\ttop\t\(owner)")
    }
}
print("\(now())\tready\t-")
RunLoop.main.run()
