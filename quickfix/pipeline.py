"""The capture -> Claude -> replace flow. Free of Qt; the Windows calls are injected so tests can drive it."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

from . import guard, winapi
from .claude_cli import CliError

log = logging.getLogger("quickfix")


class Status(Enum):
    REPLACED = "replaced"
    NO_CHANGE = "no_change"
    POPUP = "popup"      # we have a result but pasting it was not safe
    ERROR = "error"      # nothing was changed
    IGNORED = "ignored"  # excluded app


@dataclass
class Outcome:
    status: Status
    message: str = ""
    original: str = ""
    corrected: str = ""
    seconds: float = 0.0


class Win32Platform:
    foreground = staticmethod(winapi.foreground_window)
    process_name = staticmethod(winapi.window_process_name)
    sequence = staticmethod(winapi.clipboard_sequence)
    snapshot = staticmethod(winapi.snapshot_clipboard)
    restore = staticmethod(winapi.restore_clipboard)
    get_text = staticmethod(winapi.get_clipboard_text)
    set_text = staticmethod(winapi.set_clipboard_text)
    send_ctrl = staticmethod(winapi.send_ctrl)
    modifiers_down = staticmethod(winapi.modifiers_down)
    key_down = staticmethod(winapi.key_down)
    sleep = staticmethod(time.sleep)
    now = staticmethod(time.perf_counter)


COPY_WAIT_S = 0.8
MODIFIER_WAIT_S = 2.0
FOCUS_MSG = "The active window changed while Claude was working, so the text was not pasted."
CLIPBOARD_MSG = "The clipboard changed while Claude was working, so the text was not pasted."
SELECTION_MSG = "The selected text changed while Claude was working, so it was not replaced."
KEYS_MSG = "Keys were held down at the moment of pasting, so the text was not pasted."


class Pipeline:
    def __init__(self, runner, platform=None):
        self.runner = runner
        self.p = platform or Win32Platform

    def run(self, mode, cfg, trigger_vk: int = 0, on_captured=None) -> Outcome:
        started = self.p.now()
        hwnd = self.p.foreground()
        exe = self.p.process_name(hwnd) if hwnd else ""
        excluded = {e.lower() for e in cfg.excluded}
        if exe in excluded:
            log.info("skipped: excluded app")
            return Outcome(Status.IGNORED, f"{exe} is on the exclusion list. Nothing was read.")

        if not self._wait_keys_released(trigger_vk, MODIFIER_WAIT_S):
            return Outcome(Status.ERROR, "Release the shortcut keys and try again.")

        try:
            text = self._capture()
        except winapi.ClipboardBusy:
            return Outcome(Status.ERROR, "Another program is holding the clipboard. Try again.")
        if not text or not text.strip():
            hint = "" if exe else " If this app runs as administrator, QuickFix AI cannot read it."
            return Outcome(Status.ERROR, "No text is selected." + hint)
        if len(text) > cfg.max_chars:
            return Outcome(Status.ERROR, f"The selection is too long ({len(text)} characters, the limit is "
                                         f"{cfg.max_chars}). Nothing was sent.")
        if on_captured:
            on_captured()

        seq_after_capture = self.p.sequence()
        try:
            answer = self.runner.run(mode.prompt, text, cfg)
        except CliError as exc:
            log.info("cli error: %s after %.1fs", exc.kind, self.p.now() - started)
            return Outcome(Status.ERROR, exc.message, original=text, seconds=self.p.now() - started)

        checked = guard.check(answer.text, text, mode.strict)
        seconds = self.p.now() - started
        if not checked.ok:
            log.info("guard refused the result after %.1fs", seconds)
            return Outcome(Status.POPUP, checked.reason, text, checked.text or answer.text, seconds)
        if checked.text == text:
            log.info("no change needed, %.1fs", seconds)
            return Outcome(Status.NO_CHANGE, "No changes needed.", text, text, seconds)

        problem = self._replace(hwnd, seq_after_capture, text, checked.text, cfg)
        seconds = self.p.now() - started
        log.info("mode=%s model=%s chars=%d api=%.1fs total=%.1fs result=%s", mode.id, cfg.model, len(text),
                 answer.seconds_api, seconds, "popup" if problem else "replaced")
        if problem:
            return Outcome(Status.POPUP, problem, text, checked.text, seconds)
        return Outcome(Status.REPLACED, "", text, checked.text, seconds)

    def _wait_keys_released(self, trigger_vk: int, timeout: float) -> bool:
        deadline = self.p.now() + timeout
        while self.p.modifiers_down() or (trigger_vk and self.p.key_down(trigger_vk)):
            if self.p.now() > deadline:
                return False
            self.p.sleep(0.01)
        self.p.sleep(0.03)  # let the target app see the key-ups before our Ctrl+C
        return True

    def _copy_selection(self) -> str | None:
        """Sends Ctrl+C. None means the app did not touch the clipboard, so nothing was selected."""
        before = self.p.sequence()
        self.p.send_ctrl("c")
        deadline = self.p.now() + COPY_WAIT_S
        while self.p.now() < deadline:
            if self.p.sequence() != before:
                self.p.sleep(0.03)  # apps often write several formats one after another
                return self.p.get_text()
            self.p.sleep(0.01)
        return None

    def _capture(self) -> str | None:
        saved = self.p.snapshot()
        try:
            return self._copy_selection()
        finally:
            self._restore(saved)

    def _restore(self, saved) -> bool:
        """A clipboard manager can hold the clipboard for a moment, so try a few times."""
        for _ in range(3):
            try:
                self.p.restore(saved)
                return True
            except winapi.ClipboardBusy:
                self.p.sleep(0.2)
        log.warning("could not restore the clipboard")
        return False

    def _replace(self, hwnd, seq_after_capture, original, corrected, cfg) -> str:
        """Returns '' on success, otherwise why it was not safe. The user's text stays intact in that case."""
        if self.p.foreground() != hwnd:
            return FOCUS_MSG
        if self.p.sequence() != seq_after_capture:
            return CLIPBOARD_MSG
        if not self._wait_keys_released(0, 1.0):
            return KEYS_MSG
        pasted = False
        try:
            saved = self.p.snapshot()
            ours = None
            try:
                # Copy once more: pasting over a selection the user moved meanwhile would destroy other text.
                current = self._copy_selection()
                if current is None or current != original:
                    return SELECTION_MSG
                if self.p.foreground() != hwnd:
                    return FOCUS_MSG
                self.p.set_text(corrected)
                ours = self.p.sequence()
                if self.p.foreground() != hwnd:  # set_text can wait up to a second for the clipboard
                    return FOCUS_MSG
                pasted = True
                self.p.send_ctrl("v")
                self.p.sleep(cfg.paste_delay_ms / 1000)  # apps read the clipboard a moment after Ctrl+V
            finally:
                # Skip the restore only when the user really copied something new during those few ms.
                if ours is None or self.p.sequence() == ours:
                    self._restore(saved)
        except winapi.ClipboardBusy:
            if pasted:  # a popup would invite pasting the same text twice
                return ""
            return "Another program is holding the clipboard, so the text was not pasted."
        return ""
