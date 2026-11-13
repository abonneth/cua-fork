#!/bin/bash
# Builds the ScreenCaptureKit client of pair.py at every location the matrix compares.
set -euo pipefail
cd "$(dirname "$0")"
plist() {
  printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>\n<key>CFBundleIdentifier</key><string>dev.probe.sameid</string>\n<key>CFBundleExecutable</key><string>two_clients</string>\n<key>CFBundleName</key><string>Probe</string>\n<key>CFBundlePackageType</key><string>APPL</string>\n</dict></plist>\n' > "$1"
}
swiftc -O two_clients.swift -o two_clients
cp two_clients two_clients_copy
plist embed.plist
swiftc -O two_clients.swift -o two_clients_plist -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker embed.plist
for name in one two; do
  app="bundles/$name/Probe.app/Contents"
  mkdir -p "$app/MacOS" "$app/Resources/inner/bin" "$app/Helpers"
  plist "$app/Info.plist"
  cp two_clients "$app/MacOS/"
  cp two_clients "$app/Resources/inner/bin/"
  cp two_clients "$app/Helpers/"
done
