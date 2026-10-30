"""The safety rules of the flow, against a simulated window + clipboard."""
import os

import pytest

from quickfix import winapi
from quickfix.claude_cli import CliError, CliResult, NOT_LOGGED_IN, TIMEOUT
from quickfix.config import Config, Mode
from quickfix.pipeline import (CLIPBOARD_MSG, FOCUS_MSG, KEYS_MSG, SELECTION_MSG, Pipeline, Status)


class Sim:
    """A pretend Windows: Ctrl+C copies `selection`, Ctrl+V replaces it."""

    def __init__(self, selection="as nezinau"):
        self.selection = selection
        self.document_changed_to = None
        self.clip = "USER CLIPBOARD"
        self.seq = 1
        self.fg = 100
        self.exe = "notepad.exe"
        self.t = 0.0
        self.keys_down_for = 0
        self.keys_stuck = False

    def foreground(self): return self.fg
    def process_name(self, hwnd): return self.exe
    def sequence(self): return self.seq
    def snapshot(self): return self.clip
    def get_text(self): return self.clip
    def now(self): return self.t
    def sleep(self, s): self.t += s
    def key_down(self, vk): return False

    def _write(self, value):
        self.clip = value
        self.seq += 1

    def restore(self, saved): self._write(saved)
    def set_text(self, text): self._write(text)

    def send_ctrl(self, letter):
        if letter == "c" and self.selection:
            self._write(self.selection)
        elif letter == "v":
            self.document_changed_to = self.clip

    def modifiers_down(self):
        if self.keys_stuck:
            return True
        if self.keys_down_for > 0:
            self.keys_down_for -= 1
            return True
        return False


class Runner:
    def __init__(self, answer="Aš nežinau", during=None, error=None):
        self.answer, self.during, self.error = answer, during, error
        self.calls = 0

    def run(self, instruction, text, cfg):
        self.calls += 1
        if self.during:
            self.during()
        if self.error:
            raise self.error
        return CliResult(self.answer)


MODE = Mode("grammar", "Grammar", "Ctrl+Alt+G", "fix", strict=True)


def go(sim, runner=None):
    runner = runner or Runner()
    return Pipeline(runner, sim).run(MODE, Config()), runner


def test_happy_path_replaces_and_restores_the_clipboard():
    sim = Sim()
    out, _ = go(sim)
    assert out.status is Status.REPLACED
    assert sim.document_changed_to == "Aš nežinau"
    assert sim.clip == "USER CLIPBOARD"


def test_waits_for_modifiers_before_copying():
    sim = Sim()
    sim.keys_down_for = 5
    assert go(sim)[0].status is Status.REPLACED


def test_stuck_modifiers_never_send_copy():
    sim = Sim()
    sim.keys_stuck = True
    out, runner = go(sim)
    assert out.status is Status.ERROR and runner.calls == 0 and sim.clip == "USER CLIPBOARD"


def test_empty_selection_sends_nothing_to_claude():
    sim = Sim(selection="")
    out, runner = go(sim)
    assert out.status is Status.ERROR and "No text" in out.message and runner.calls == 0
    assert sim.clip == "USER CLIPBOARD"


def test_whitespace_selection_sends_nothing():
    sim = Sim(selection="  \n ")
    out, runner = go(sim)
    assert out.status is Status.ERROR and runner.calls == 0


def test_excluded_app_is_not_read_at_all():
    sim = Sim()
    sim.exe = "nordpass.exe"
    out, runner = go(sim)
    assert out.status is Status.IGNORED and runner.calls == 0 and sim.seq == 1


def test_too_long_selection_is_not_sent():
    sim = Sim(selection="x" * 20001)
    out, runner = go(sim)
    assert out.status is Status.ERROR and runner.calls == 0


def test_focus_change_goes_to_popup_without_pasting():
    sim = Sim()
    out, _ = go(sim, Runner(during=lambda: setattr(sim, "fg", 200)))
    assert out.status is Status.POPUP and out.message == FOCUS_MSG
    assert sim.document_changed_to is None and out.corrected == "Aš nežinau" and out.original == "as nezinau"


def test_clipboard_change_goes_to_popup_and_keeps_the_new_clipboard():
    sim = Sim()
    out, _ = go(sim, Runner(during=lambda: sim._write("USER COPIED THIS")))
    assert out.status is Status.POPUP and out.message == CLIPBOARD_MSG
    assert sim.document_changed_to is None and sim.clip == "USER COPIED THIS"


def test_moved_selection_goes_to_popup():
    sim = Sim()
    out, _ = go(sim, Runner(during=lambda: setattr(sim, "selection", "other text")))
    assert out.status is Status.POPUP and out.message == SELECTION_MSG
    assert sim.document_changed_to is None and sim.clip == "USER CLIPBOARD"


def test_selection_cleared_goes_to_popup():
    sim = Sim()
    out, _ = go(sim, Runner(during=lambda: setattr(sim, "selection", "")))
    assert out.status is Status.POPUP and out.message == SELECTION_MSG
    assert sim.clip == "USER CLIPBOARD"


def test_user_holding_keys_at_paste_time_goes_to_popup():
    sim = Sim()
    out, _ = go(sim, Runner(during=lambda: setattr(sim, "keys_stuck", True)))
    assert out.status is Status.POPUP and out.message == KEYS_MSG and sim.document_changed_to is None


def test_cli_errors_change_nothing():
    for kind in (NOT_LOGGED_IN, TIMEOUT):
        sim = Sim()
        out, _ = go(sim, Runner(error=CliError(kind)))
        assert out.status is Status.ERROR and sim.document_changed_to is None and sim.clip == "USER CLIPBOARD"


def test_focus_lost_while_writing_the_clipboard_does_not_paste():
    sim = Sim()
    real_set = sim.set_text

    def slow_set(text):
        real_set(text)
        sim.fg = 200

    sim.set_text = slow_set
    out, _ = go(sim)
    assert out.status is Status.POPUP and out.message == FOCUS_MSG
    assert sim.document_changed_to is None and sim.clip == "USER CLIPBOARD"


def test_busy_clipboard_after_the_paste_is_not_reported_as_not_pasted():
    sim = Sim()
    real_send, real_restore = sim.send_ctrl, sim.restore
    state = {"pasted": False}

    def send(letter):
        real_send(letter)
        state["pasted"] = state["pasted"] or letter == "v"

    def restore(saved):
        if state["pasted"]:
            raise winapi.ClipboardBusy()
        real_restore(saved)

    sim.send_ctrl, sim.restore = send, restore
    out, _ = go(sim)
    assert out.status is Status.REPLACED and sim.document_changed_to == "Aš nežinau"


def test_guard_refusal_goes_to_popup():
    sim = Sim()
    out, _ = go(sim, Runner(answer=""))
    assert out.status is Status.ERROR or out.status is Status.POPUP
    assert sim.document_changed_to is None


def test_unchanged_text_is_not_pasted():
    sim = Sim(selection="Aš nežinau")
    out, _ = go(sim, Runner(answer="Aš nežinau"))
    assert out.status is Status.NO_CHANGE and sim.document_changed_to is None


def test_busy_clipboard_on_capture_is_a_clean_error():
    sim = Sim()

    def busy():
        raise winapi.ClipboardBusy()

    sim.snapshot = busy
    out, runner = go(sim)
    assert out.status is Status.ERROR and runner.calls == 0


@pytest.mark.parametrize("text,expected", [
    ("Ctrl+Alt+G", (winapi.MOD_CONTROL | winapi.MOD_ALT, ord("G"))),
    ("ctrl+shift+1", (winapi.MOD_CONTROL | winapi.MOD_SHIFT, ord("1"))),
    ("Alt+F2", (winapi.MOD_ALT, 0x71)),
    ("Shift+G", None), ("G", None), ("", None), ("Ctrl+Alt", None), ("Ctrl+Š", None),
])
def test_parse_hotkey(text, expected):
    assert winapi.parse_hotkey(text) == expected


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="needs an interactive desktop")
def test_real_clipboard_round_trip_with_lithuanian_and_emoji():
    saved = winapi.snapshot_clipboard()
    try:
        winapi.set_clipboard_text("ąčęėįšųūž 😀\r\nantra")
        snap = winapi.snapshot_clipboard()
        winapi.set_clipboard_text("kita")
        winapi.restore_clipboard(snap)
        assert winapi.get_clipboard_text() == "ąčęėįšųūž 😀\r\nantra"
    finally:
        winapi.restore_clipboard(saved)


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="needs an interactive desktop")
def test_real_clipboard_keeps_a_gdi_only_bitmap_as_a_picture():
    """Paint-style copies offer only CF_BITMAP; Windows must synthesize CF_DIB for our snapshot to keep it."""
    import ctypes
    gdi = ctypes.WinDLL("gdi32")
    gdi.CreateBitmap.restype = ctypes.c_void_p
    gdi.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    saved = winapi.snapshot_clipboard()
    try:
        with winapi._Clipboard():
            winapi.user32.EmptyClipboard()
            assert winapi.user32.SetClipboardData(2, gdi.CreateBitmap(4, 4, 1, 32, None))
        snap = winapi.snapshot_clipboard()
        assert 8 in dict(snap.formats) or 17 in dict(snap.formats)
        winapi.restore_clipboard(snap)
        assert winapi.user32.IsClipboardFormatAvailable(2)  # still pasteable as a picture
    finally:
        winapi.restore_clipboard(saved)
