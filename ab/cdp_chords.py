"""Do browser shortcuts sent to a page over CDP `Input.dispatchKeyEvent` reach the browser frame (tabs, reload)?"""

import json
import os
import time
import urllib.request

import websocket

BASE = "http://127.0.0.1:9222"
CHORDS = {
    "ctrl+t (new tab)": ("t", "KeyT", 84, 2),
    "ctrl+w (close tab)": ("w", "KeyW", 87, 2),
    "ctrl+l (address bar)": ("l", "KeyL", 76, 2),
    "f5 (reload)": ("F5", "F5", 116, 0),
    "ctrl+r (reload)": ("r", "KeyR", 82, 2),
}


def pages() -> list[dict]:
    return [t for t in json.load(urllib.request.urlopen(f"{BASE}/json/list")) if t["type"] == "page"]


class Page:
    def __init__(self, target: dict) -> None:
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10)
        self.next = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next += 1
        self.ws.send(json.dumps({"id": self.next, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self.next:
                return reply

    def value(self, expression: str):
        return self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})["result"]["result"].get("value")

    def chord(self, key: str, code: str, vk: int, modifiers: int) -> None:
        if modifiers:
            self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Control", "code": "ControlLeft", "windowsVirtualKeyCode": 17, "modifiers": 2})
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": modifiers}
        self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})
        if modifiers:
            self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Control", "code": "ControlLeft", "windowsVirtualKeyCode": 17})


def fresh_page() -> Page:
    page = Page(pages()[0])
    page.call("Page.navigate", {"url": "data:text/html,<input id=f value=hello><script>window.loadedAt=performance.timeOrigin;document.addEventListener('keydown',e=>{(window.keys=window.keys||[]).push((e.ctrlKey?'ctrl+':'')+e.key)})</script>"})
    time.sleep(1.5)
    page.value("document.getElementById('f').focus()")
    return page


lines = ["| chord sent to the page over CDP | page saw keydown | browser effect |", "|---|---|---|"]
for name, chord in CHORDS.items():
    page = fresh_page()
    tabs_before, loaded_before = len(pages()), page.value("window.loadedAt")
    page.chord(*chord)
    time.sleep(2)
    tabs_after = len(pages())
    try:
        seen, loaded_after = page.value("window.keys||[]"), page.value("window.loadedAt")
    except Exception as error:
        seen, loaded_after = f"page gone: {error}", None
    effect = []
    if tabs_after != tabs_before:
        effect.append(f"tabs {tabs_before} -> {tabs_after}")
    if loaded_after != loaded_before:
        effect.append("reloaded")
    lines.append(f"| {name} | {seen} | {', '.join(effect) or 'none'} |")
    print(lines[-1])
    page.ws.close()

page = fresh_page()
page.chord("a", "KeyA", 65, 2)
time.sleep(0.5)
selected = page.value("(e=>e.selectionEnd-e.selectionStart)(document.getElementById('f'))")
lines.append(f"| control: ctrl+a in a text field | {page.value('window.keys||[]')} | selected {selected} of 5 chars |")
print(lines[-1])
summary = "\n".join(lines)
with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
    out.write(summary + "\n")
