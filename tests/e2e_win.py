"""End-to-end test on the real desktop: the real app, real RegisterHotKey, real clipboard, real Notepad and
Chrome, driven with synthetic keys. Claude is replaced by tests/fake_claude.py unless --real is passed.

    python -X utf8 tests/e2e_win.py            all failure cases, no quota used
    python -X utf8 tests/e2e_win.py --real     real `claude`: correction + latency in Notepad and Chrome

It takes over keyboard focus while it runs, so do not type during it. Your clipboard is saved and put back.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from grammarhotkey import winapi  # noqa: E402
from grammarhotkey.config import GRAMMAR_PROMPT  # noqa: E402

user32 = winapi.user32
PY = str(ROOT / ".venv/Scripts/python.exe")
FAKE = str(ROOT / "tests/fake_claude.cmd")
POPUP_TITLE = "GrammarHotkey: not pasted"
EXE = ""  # --exe: test the packaged build instead of the sources
# Not the default shortcut, so the tests can run while the user's own copy holds Ctrl+Alt+G.
TEST_HOTKEY = "Y"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


# --- desktop helpers ---------------------------------------------------------------------------------
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.BringWindowToTop.argtypes = [wintypes.HWND]


def title_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def windows_with(text: str) -> list[int]:
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and text in title_of(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def wait_window(text: str, timeout=15.0) -> int:
    end = time.time() + timeout
    while time.time() < end:
        hits = windows_with(text)
        if hits:
            return hits[0]
        time.sleep(0.1)
    raise RuntimeError(f"no window with {text!r}")


def activate(hwnd) -> None:
    if user32.GetForegroundWindow() == hwnd:
        return
    for _ in range(10):
        # Any injected input lifts Windows' foreground lock for this process. Not Alt: that would open
        # the menu mode of the window that has focus and swallow the next keys. 0x07 is an unassigned key.
        winapi._send([(0x07, False), (0x07, True)])
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        if user32.GetForegroundWindow() == hwnd:
            return
    raise RuntimeError(f"cannot focus {title_of(hwnd)!r}")


def keys(*vks) -> None:
    winapi._send([(v, False) for v in vks] + [(v, True) for v in reversed(vks)])
    time.sleep(0.08)


def hotkey(letter=TEST_HOTKEY, alt_first=False) -> None:
    """Pressed like a person: the app gets time to react while the keys are down, then they come up."""
    vk = ord(letter)
    winapi._send([(winapi.VK_CONTROL, False), (winapi.VK_MENU, False), (vk, False)])
    time.sleep(0.07)
    ups = [vk, winapi.VK_MENU, winapi.VK_CONTROL] if alt_first else [vk, winapi.VK_CONTROL, winapi.VK_MENU]
    for up in ups:
        winapi._send([(up, True)])
        time.sleep(0.02)


def write_sentinel() -> bytes:
    """Clipboard with text + HTML + a picture, as a real copy from a browser would have."""
    html = b"Version:0.9\r\nSENTINEL-HTML\0"
    header = (40).to_bytes(4, "little") + (1).to_bytes(4, "little") + (1).to_bytes(4, "little") + \
        (1).to_bytes(2, "little") + (32).to_bytes(2, "little") + bytes(24)
    dib = header + b"\x11\x22\x33\x00"
    with winapi._Clipboard():
        user32.EmptyClipboard()
        winapi._set_format(winapi.CF_UNICODETEXT, "SENTINEL ąč😀".encode("utf-16-le") + b"\0\0")
        winapi._set_format(user32.RegisterClipboardFormatW("HTML Format"), html)
        winapi._set_format(8, dib)
    return html


def sentinel_intact(html: bytes) -> bool:
    snap = winapi.snapshot_clipboard()
    data = dict(snap.formats)
    fmt = user32.RegisterClipboardFormatW("HTML Format")
    return winapi.get_clipboard_text() == "SENTINEL ąč😀" and data.get(fmt, b"").startswith(html) and 8 in data


def put_text(text: str) -> None:
    """Replace the focused document with text, and leave all of it selected."""
    winapi.set_clipboard_text(text)
    keys(winapi.VK_CONTROL, ord("A"))
    keys(winapi.VK_CONTROL, ord("V"))
    time.sleep(0.2)
    keys(winapi.VK_CONTROL, ord("A"))


def read_doc() -> str:
    before = winapi.clipboard_sequence()
    keys(winapi.VK_CONTROL, ord("A"))
    keys(winapi.VK_CONTROL, ord("C"))
    end = time.time() + 1
    while winapi.clipboard_sequence() == before and time.time() < end:
        time.sleep(0.02)
    time.sleep(0.05)
    return winapi.get_clipboard_text() or ""


# --- the app -----------------------------------------------------------------------------------------
class App:
    def __init__(self, workdir: Path, **config):
        self.dir = workdir
        self.mode_file = workdir / "mode.txt"
        self.count_file = workdir / "count.txt"
        self.set_mode("ok")
        self.count_file.write_text("", encoding="utf-8")
        config.setdefault("modes", [{"id": "grammar", "name": "Grammar", "hotkey": f"Ctrl+Alt+{TEST_HOTKEY}",
                                     "prompt": GRAMMAR_PROMPT, "strict": True}])
        (workdir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        env = dict(os.environ, GRAMMARHOTKEY_CONFIG_DIR=str(workdir), FAKE_CLAUDE_MODE_FILE=str(self.mode_file),
                   FAKE_CLAUDE_COUNT_FILE=str(self.count_file))
        self.log = workdir / "app.log"
        log_start = len(self.log_text()) if self.log.exists() else 0  # the log survives between runs
        command = [EXE] if EXE else [PY, "-X", "utf8", "-m", "grammarhotkey"]
        self.proc = subprocess.Popen(command, cwd=ROOT, env=env)
        end = time.time() + 15
        while time.time() < end:
            if self.log.exists() and "started version" in self.log_text()[log_start:]:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("the app did not start")
        time.sleep(0.5)

    def set_mode(self, mode: str) -> None:
        self.mode_file.write_text(mode, encoding="utf-8")

    def calls(self) -> int:
        return len(self.count_file.read_text(encoding="utf-8").splitlines())

    def log_text(self) -> str:
        return self.log.read_text(encoding="utf-8")

    def log_lines_since(self, offset: int) -> str:
        return self.log_text()[offset:]

    def stop(self) -> None:
        # /T because a one-file exe is two processes; the survivor would keep the hotkeys.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
        self.proc.wait(10)
        time.sleep(0.3)


def wait_log(app, offset: int, pattern: str, timeout=15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if re.search(pattern, app.log_lines_since(offset)):
            return True
        time.sleep(0.05)
    return False


def close_popups() -> str:
    """Closes our popups and returns the title found, '' if none."""
    hits = windows_with(POPUP_TITLE)
    for hwnd in hits:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
    time.sleep(0.2)
    return POPUP_TITLE if hits else ""


def wait_popup(timeout=12.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if windows_with(POPUP_TITLE):
            return True
        time.sleep(0.05)
    return False


# --- scenarios ---------------------------------------------------------------------------------------
def correction(app, hwnd, label, text, expected, patience=8) -> float:
    activate(hwnd)
    put_text(text)
    html = write_sentinel()
    offset = len(app.log_text())
    start = time.perf_counter()
    hotkey()
    replaced = wait_log(app, offset, r"result=replaced", patience)
    elapsed = time.perf_counter() - start
    clipboard_ok = sentinel_intact(html)
    still_focused = user32.GetForegroundWindow() == hwnd
    if not replaced:
        print("      log:", app.log_lines_since(offset).strip() or "nothing logged", flush=True)
        close_popups()
        activate(hwnd)
    doc = read_doc()
    ok = replaced and bool(doc.strip()) and (doc.rstrip("\r\n") == expected if expected is not None
                                             else doc.rstrip("\r\n") != text)
    record(f"{label}: corrected in place", ok, repr(doc[:70]))
    record(f"{label}: clipboard restored (text + html + picture)", clipboard_ok)
    record(f"{label}: focus stayed on the app", still_focused)
    return elapsed


def fake_suite(notepad, chrome, work: Path) -> None:
    app = App(work, claude_path=FAKE, timeout_s=4)
    try:
        lat = []
        lat.append(correction(app, notepad, "Notepad LT", "as nezinau ka darit", "Aš nežinau ką daryti"))
        correction(app, notepad, "Notepad multiline+emoji", "sveikas 😀\r\n\r\nteh antra eilute\r\ntrecia",
                   "Sveikas 😀\r\n\r\nthe antra eilute\r\ntrecia")
        lat.append(correction(app, chrome, "Chrome LT", "as nezinau ka darit", "Aš nežinau ką daryti"))
        correction(app, chrome, "Chrome multiline+emoji", "sveikas 😀\n\nteh antra", None)

        # Whichever modifier is released first, the app must still get Ctrl+C
        activate(notepad)
        put_text("as nezinau")
        offset = len(app.log_text())
        hotkey(alt_first=True)
        wait_log(app, offset, r"result=replaced", 8)
        record("Notepad, Alt released before Ctrl", read_doc() == "Aš nežinau")

        # Empty selection
        activate(notepad)
        put_text("tekstas lieka")
        keys(0x23)  # End: selection gone
        html = write_sentinel()
        calls, offset = app.calls(), len(app.log_text())
        hotkey()
        time.sleep(1.5)
        record("Empty selection: nothing sent", app.calls() == calls)
        record("Empty selection: clipboard intact", sentinel_intact(html))
        record("Empty selection: no popup", not windows_with(POPUP_TITLE))
        record("Empty selection: document unchanged", read_doc() == "tekstas lieka")

        # Focus changes while Claude works
        put_text("as nezinau")
        app.set_mode("slow2")
        html = write_sentinel()
        hotkey()
        time.sleep(0.6)
        activate(chrome)
        popup = wait_popup()
        record("Focus change: popup instead of paste", popup)
        record("Focus change: clipboard intact", sentinel_intact(html))
        close_popups()
        activate(notepad)
        record("Focus change: original untouched", read_doc() == "as nezinau")

        # Clipboard changes while Claude works
        put_text("as nezinau")
        hotkey()
        time.sleep(0.6)
        winapi.set_clipboard_text("VARTOTOJAS NUKOPIJAVO")
        popup = wait_popup()
        record("Clipboard change: popup instead of paste", popup)
        record("Clipboard change: user's new copy kept", winapi.get_clipboard_text() == "VARTOTOJAS NUKOPIJAVO")
        close_popups()
        activate(notepad)
        record("Clipboard change: original untouched", read_doc() == "as nezinau")

        # Selection moved while Claude works
        put_text("as nezinau")
        html = write_sentinel()
        hotkey()
        time.sleep(0.6)
        keys(0x23)
        popup = wait_popup()
        record("Selection moved: popup instead of paste", popup)
        record("Selection moved: clipboard intact", sentinel_intact(html))
        close_popups()
        activate(notepad)
        record("Selection moved: original untouched", read_doc() == "as nezinau")

        # One request at a time
        put_text("as nezinau")
        calls = app.calls()
        hotkey()
        time.sleep(0.5)
        hotkey()
        time.sleep(3)
        record("Second press while busy: only one request", app.calls() == calls + 1)
        app.set_mode("ok")
        close_popups()
        activate(notepad)

        # CLI failures: the text must stay, and the log must name the kind
        for mode, kind in (("hang", "timeout"), ("notlogged", "not_logged_in"), ("expired", "not_logged_in"),
                           ("limit", "usage_limit")):
            put_text("as nezinau")
            app.set_mode(mode)
            offset = len(app.log_text())
            html = write_sentinel()
            hotkey()
            seen = wait_log(app, offset, f"cli error: {kind}", 10)
            record(f"CLI {mode}: reported as {kind}", seen)
            record(f"CLI {mode}: clipboard intact", sentinel_intact(html))
            record(f"CLI {mode}: text unchanged", read_doc() == "as nezinau")
        left = subprocess.run(["powershell", "-NoProfile", "-Command",
                               "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -like '*fake_claude.py*'}"
                               " | Measure-Object).Count"], capture_output=True, text=True).stdout.strip()
        record("Timeout: no process left behind", left == "0", f"left={left}")

        # Guard on the output
        for mode in ("empty", "huge"):
            put_text("as nezinau")
            app.set_mode(mode)
            hotkey()
            popup = wait_popup()
            record(f"Output {mode}: popup instead of paste", popup)
            close_popups()
            activate(notepad)
            record(f"Output {mode}: text unchanged", read_doc() == "as nezinau")
        put_text("as nezinau")
        app.set_mode("commentary")
        offset = len(app.log_text())
        hotkey()
        wait_log(app, offset, r"result=replaced", 10)
        record("Output with commentary and fence: cleaned", read_doc() == "Aš nežinau")
        app.set_mode("ok")

        # Nothing typed may reach the log
        log = app.log_text()
        leaks = [w for w in ("nezinau", "nežinau", "eilute", "tekstas", "SENTINEL") if w in log]
        record("Log holds no selected text", not leaks, ",".join(leaks))
        record("App overhead with an instant fake CLI", True, f"{statistics.mean(lat):.2f} s per correction")
    finally:
        app.stop()

    # Missing CLI
    app = App(work, claude_path=r"C:\nope\claude.exe")
    try:
        activate(notepad)
        put_text("as nezinau")
        offset = len(app.log_text())
        hotkey()
        record("Missing CLI: reported", wait_log(app, offset, "cli error: not_found", 6))
        record("Missing CLI: text unchanged", read_doc() == "as nezinau")
    finally:
        app.stop()

    # Excluded app
    app = App(work, claude_path=FAKE, excluded=["notepad.exe"])
    try:
        activate(notepad)
        put_text("as nezinau")
        keys(winapi.VK_CONTROL, ord("A"))
        html = write_sentinel()
        seq = winapi.clipboard_sequence()
        calls = app.calls()
        hotkey()
        time.sleep(1.2)
        record("Excluded app: clipboard not even touched", winapi.clipboard_sequence() == seq)
        record("Excluded app: nothing sent", app.calls() == calls)
        record("Excluded app: clipboard intact", sentinel_intact(html))
    finally:
        app.stop()


def real_suite(notepad, chrome, work: Path) -> None:
    samples = [
        ("as nezinau ka darit rytoj gal nueisiu i parduotuve ir nupirksiu pieno", "short"),
        ("labas, siandien buvo labai gera diena 😀 nuejom i parka su sunimi.\r\n\r\n"
         "rytoj vaziuosim i kauna, gal susitiksim su draugais ir nueisim i kavine. parasyk jei nori kartu", "multiline"),
        ("i dont no what to do, can you sent me teh link https://example.com/a?b=1 pls", "english+url"),
    ]
    app = App(work, timeout_s=90)
    timings = []
    try:
        for hwnd, where in ((notepad, "Notepad"), (chrome, "Chrome")) * 2:
            for text, label in samples:
                timings.append(correction(app, hwnd, f"{where} {label}", text, None, patience=40))
                print("      ->", read_doc().replace("\r", "")[:200], flush=True)
        print(f"\nLatency over {len(timings)} real corrections: min {min(timings):.1f} s, "
              f"median {statistics.median(timings):.1f} s, max {max(timings):.1f} s")
    finally:
        app.stop()


def cleanup_windows() -> None:
    for hwnd in windows_with("QF-E2E-CHROME"):
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
    # Tab by tab, and only while the title is ours: Notepad may have restored the user's own tabs.
    for _ in range(20):
        hits = windows_with("qf-e2e-notepad")
        if not hits:
            break
        try:
            activate(hits[0])
            keys(winapi.VK_CONTROL, ord("S"))  # saved, so closing asks nothing
            time.sleep(0.4)
            keys(winapi.VK_CONTROL, ord("W"))
            time.sleep(0.4)
        except RuntimeError:
            break
    time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--exe", default="", help="path to GrammarHotkey.exe")
    args = parser.parse_args()
    global EXE
    EXE = args.exe

    saved = winapi.snapshot_clipboard()
    work = Path(tempfile.mkdtemp(prefix="grammarhotkey_e2e_"))
    doc = work / "qf-e2e-notepad.txt"
    doc.write_text("", encoding="utf-8")
    page = work / "qf-e2e-chrome.html"
    page.write_text("<!doctype html><meta charset=utf-8><title>QF-E2E-CHROME</title>"
                    "<textarea autofocus rows=12 cols=70></textarea>", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(doc)])
    notepad = wait_window("qf-e2e-notepad")
    chrome_exe = next(p for p in (Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
                                  Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe")
                      if p.exists())
    subprocess.Popen([str(chrome_exe), "--new-window", page.as_uri()])
    chrome = wait_window("QF-E2E-CHROME")
    time.sleep(1)
    try:
        (real_suite if args.real else fake_suite)(notepad, chrome, work)
    finally:
        close_popups()
        cleanup_windows()
        winapi.restore_clipboard(saved)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    for name, _, detail in failed:
        print("  FAILED:", name, detail)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
