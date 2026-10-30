"""Thin ctypes layer over the Win32 calls we need: clipboard, synthetic keys, windows, hotkey parsing."""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_HANDLE, _UINT, _SIZE = wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t
for fn, res, args in [
    (user32.OpenClipboard, wintypes.BOOL, [wintypes.HWND]),
    (user32.CloseClipboard, wintypes.BOOL, []),
    (user32.EmptyClipboard, wintypes.BOOL, []),
    (user32.EnumClipboardFormats, _UINT, [_UINT]),
    (user32.GetClipboardData, _HANDLE, [_UINT]),
    (user32.SetClipboardData, _HANDLE, [_UINT, _HANDLE]),
    (user32.GetClipboardSequenceNumber, wintypes.DWORD, []),
    (user32.RegisterClipboardFormatW, _UINT, [wintypes.LPCWSTR]),
    (user32.IsClipboardFormatAvailable, wintypes.BOOL, [_UINT]),
    (user32.GetForegroundWindow, wintypes.HWND, []),
    (user32.GetWindowThreadProcessId, wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
    (user32.GetAsyncKeyState, ctypes.c_short, [ctypes.c_int]),
    (user32.RegisterHotKey, wintypes.BOOL, [wintypes.HWND, ctypes.c_int, _UINT, _UINT]),
    (user32.UnregisterHotKey, wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
    (user32.GetKeyboardLayoutList, ctypes.c_int, [ctypes.c_int, ctypes.POINTER(_HANDLE)]),
    (user32.ToUnicodeEx, ctypes.c_int, [_UINT, _UINT, ctypes.POINTER(ctypes.c_ubyte), wintypes.LPWSTR,
                                        ctypes.c_int, _UINT, _HANDLE]),
    (user32.MapVirtualKeyExW, _UINT, [_UINT, _UINT, _HANDLE]),
    (kernel32.GlobalAlloc, _HANDLE, [_UINT, _SIZE]),
    (kernel32.GlobalFree, _HANDLE, [_HANDLE]),
    (kernel32.GlobalLock, ctypes.c_void_p, [_HANDLE]),
    (kernel32.GlobalUnlock, wintypes.BOOL, [_HANDLE]),
    (kernel32.GlobalSize, _SIZE, [_HANDLE]),
    (kernel32.OpenProcess, _HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
    (kernel32.CloseHandle, wintypes.BOOL, [_HANDLE]),
    (kernel32.QueryFullProcessImageNameW, wintypes.BOOL, [_HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                          ctypes.POINTER(wintypes.DWORD)]),
    (kernel32.CreateMutexW, _HANDLE, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]),
]:
    fn.restype, fn.argtypes = res, args

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Formats that hold GDI handles or are synthesized from another format, so copying their bytes is wrong
# or pointless. Pictures survive through CF_DIB / CF_DIBV5, which are plain memory.
_SKIP_FORMATS = {1, 2, 3, 7, 9, 14, 16, 0x0080, 0x0082, 0x0083, 0x008E}
_MAX_SNAPSHOT = 256 * 1024 * 1024


class ClipboardBusy(Exception):
    pass


class _Clipboard:
    def __enter__(self):
        for _ in range(40):
            if user32.OpenClipboard(None):
                return self
            time.sleep(0.025)
        raise ClipboardBusy()

    def __exit__(self, *exc):
        user32.CloseClipboard()


def clipboard_sequence() -> int:
    return user32.GetClipboardSequenceNumber()


def _private_format(name: str) -> int:
    return user32.RegisterClipboardFormatW(name)


def _set_format(fmt: int, data: bytes) -> bool:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True  # the system owns the memory now


def _mark_private() -> None:
    """Keep our temporary writes out of Win+V history and the cloud clipboard."""
    _set_format(_private_format("ExcludeClipboardContentFromMonitorProcessing"), b"\0\0\0\0")
    _set_format(_private_format("CanIncludeInClipboardHistory"), b"\0\0\0\0")
    _set_format(_private_format("CanUploadToCloudClipboard"), b"\0\0\0\0")


@dataclass
class ClipboardSnapshot:
    formats: list[tuple[int, bytes]]
    complete: bool  # False if some format could not be saved (GDI-only content, huge data)


def snapshot_clipboard() -> ClipboardSnapshot:
    formats: list[tuple[int, bytes]] = []
    complete, total, fmt = True, 0, 0
    with _Clipboard():
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _SKIP_FORMATS or 0x0200 <= fmt <= 0x03FF:
                continue
            handle = user32.GetClipboardData(fmt)
            if not handle:
                complete = False
                continue
            size = kernel32.GlobalSize(handle)
            if not size or total + size > _MAX_SNAPSHOT:
                complete = False
                continue
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                complete = False
                continue
            try:
                formats.append((fmt, ctypes.string_at(ptr, size)))
                total += size
            finally:
                kernel32.GlobalUnlock(handle)
    return ClipboardSnapshot(formats, complete)


def restore_clipboard(snap: ClipboardSnapshot) -> None:
    with _Clipboard():
        user32.EmptyClipboard()
        _mark_private()  # what the user had is not new, so it must not appear in history again
        for fmt, data in snap.formats:
            _set_format(fmt, data)


def set_clipboard_text(text: str) -> None:
    with _Clipboard():
        user32.EmptyClipboard()
        _mark_private()
        _set_format(CF_UNICODETEXT, text.encode("utf-16-le") + b"\0\0")


def get_clipboard_text() -> str | None:
    with _Clipboard():
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)


# --- synthetic input ---------------------------------------------------------------------------------
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):  # only here so the union has the size Windows expects
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.restype = _UINT
user32.SendInput.argtypes = [_UINT, ctypes.POINTER(_INPUT), ctypes.c_int]

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
KEYEVENTF_KEYUP = 0x0002


def _send(events: list[tuple[int, bool]]) -> bool:
    inputs = (_INPUT * len(events))()
    for slot, (vk, up) in zip(inputs, events):
        slot.type = 1
        slot.u.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return user32.SendInput(len(events), inputs, ctypes.sizeof(_INPUT)) == len(events)


def send_ctrl(letter: str) -> bool:
    vk = ord(letter.upper())
    return _send([(VK_CONTROL, False), (vk, False), (vk, True), (VK_CONTROL, True)])


def mask_alt() -> bool:
    """Tap an unassigned key while Alt is held. Otherwise, releasing the hotkey's Alt can count as a lone Alt
    press: apps such as Windows 11 Notepad then move focus to their menu and ignore our Ctrl+C."""
    return _send([(0xE8, False), (0xE8, True)])


def modifiers_down() -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# --- windows ---------------------------------------------------------------------------------------
def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def window_process_name(hwnd: int) -> str:
    """Lower-case exe name of the window's process, '' if unknown (typically an elevated process)."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def single_instance(name: str = "QuickFixAI-single-instance") -> bool:
    """Keeps the mutex for the life of the process."""
    handle = kernel32.CreateMutexW(None, False, name)
    return bool(handle) and ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


# --- hotkeys --------------------------------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+G' -> (mods, vk). Needs at least one of Ctrl/Alt/Win and one A-Z, 0-9 or F1-F24 key."""
    mods, vk = 0, 0
    for part in (p.strip().lower() for p in (text or "").split("+")):
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
        elif part[:1] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        else:
            return None
    if not vk or not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None
    return mods, vk


def altgr_conflicts(vk: int, layouts: list[int] | None = None) -> list[str]:
    """Ctrl+Alt is the same as AltGr. Returns the characters this key types with AltGr on installed layouts,
    because such a hotkey would stop that character from being typed."""
    state = (ctypes.c_ubyte * 256)()
    state[VK_CONTROL] = state[VK_MENU] = 0x80
    if layouts is None:
        count = user32.GetKeyboardLayoutList(0, None)
        buf = (_HANDLE * count)()
        user32.GetKeyboardLayoutList(count, buf)
        layouts = [int(h or 0) for h in buf]
    found = []
    for layout in layouts:
        scan = user32.MapVirtualKeyExW(vk, 0, layout)
        out = ctypes.create_unicode_buffer(8)
        n = user32.ToUnicodeEx(vk, scan, state, out, 8, 0x4, layout)  # 0x4 = do not change keyboard state
        if n > 0 and out.value and out.value.isprintable():
            found.append(out.value[:n])
        elif n < 0:  # dead key
            found.append("dead key")
    return sorted(set(found))
