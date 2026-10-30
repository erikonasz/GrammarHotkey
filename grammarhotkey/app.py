"""Tray application: registers the hotkeys and runs one pipeline at a time on a worker thread."""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, __version__, claude_cli, config as cfgmod, winapi
from .claude_cli import ClaudeRunner
from .pipeline import Outcome, Pipeline, Status
from .settings_ui import SettingsDialog, hotkey_warning
from .ui_widgets import Indicator, ResultPopup, app_icon

WM_HOTKEY = 0x0312
log = logging.getLogger("grammarhotkey")


def setup_logging() -> None:
    """Metadata only (mode, model, timings, error kind). Selected text and CLI output never reach this log."""
    directory = cfgmod.data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(directory / "app.log", maxBytes=200_000, backupCount=1,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                winapi.mask_alt()  # right here, on the message thread: Alt must still be down
                self.callback(int(msg.wParam))
                return True, 0
        return False, 0


class Controller(QObject):
    finished = Signal(object)
    captured = Signal(str)
    problem = Signal(str)

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self.qt_app = qt_app
        self.cfg = cfgmod.load()
        self.runner = ClaudeRunner()
        self.pipeline = Pipeline(self.runner)
        self.indicator = Indicator()
        self.busy = False
        self.worker: threading.Thread | None = None
        self.paused = False
        self.settings_open = False
        self.popups: list[ResultPopup] = []
        self.registered: dict[int, cfgmod.Mode] = {}

        self.filter = HotkeyFilter(self.on_hotkey)
        qt_app.installNativeEventFilter(self.filter)
        self.finished.connect(self.on_finished)
        self.captured.connect(lambda name: self.indicator.busy(f"{name}..."))
        self.problem.connect(self.on_startup_problem)

        self.tray = QSystemTrayIcon(app_icon(), qt_app)
        self.tray.setToolTip(APP_NAME)
        self.menu = QMenu()
        self.status_action = self.menu.addAction("Ready")
        self.status_action.setEnabled(False)
        self.cancel_action = self.menu.addAction("Cancel current request", self.runner.cancel)
        self.cancel_action.setEnabled(False)
        self.pause_action = self.menu.addAction("Pause shortcuts", self.toggle_pause)
        self.pause_action.setCheckable(True)
        self.menu.addAction("Settings...", self.open_settings)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        self.register_hotkeys()
        threading.Thread(target=self._check_startup, daemon=True).start()
        log.info("started version %s", __version__)

    # --- hotkeys ---------------------------------------------------------------------------------
    def unregister_hotkeys(self) -> None:
        for hotkey_id in self.registered:
            winapi.user32.UnregisterHotKey(None, hotkey_id)
        self.registered.clear()

    def register_hotkeys(self) -> None:
        self.unregister_hotkeys()
        failed, warnings = [], []
        for index, mode in enumerate(self.cfg.modes, start=1):
            if not mode.enabled or not mode.hotkey:
                continue
            parsed = winapi.parse_hotkey(mode.hotkey)
            if not parsed:
                failed.append(mode.hotkey)
                continue
            mods, vk = parsed
            if winapi.user32.RegisterHotKey(None, index, mods | winapi.MOD_NOREPEAT, vk):
                self.registered[index] = mode
            else:
                failed.append(mode.hotkey)
            warning = hotkey_warning(mode.hotkey)
            if warning:
                warnings.append(warning)
        if failed:
            self.tray.showMessage(APP_NAME, f"Another program already uses {', '.join(failed)}. "
                                            "Pick a different shortcut in Settings.",
                                  QSystemTrayIcon.MessageIcon.Warning)
            log.warning("hotkeys unavailable: %s", ", ".join(failed))
        if warnings:
            self.tray.showMessage(APP_NAME, "\n".join(warnings), QSystemTrayIcon.MessageIcon.Warning)

    def on_hotkey(self, hotkey_id: int) -> None:
        mode = self.registered.get(hotkey_id)
        if mode is None or self.paused or self.settings_open:
            return
        if self.busy:
            self.indicator.flash("Still working on the previous request.")
            return
        self.busy = True
        self.runner.begin()
        self.cancel_action.setEnabled(True)
        self.tray.setIcon(app_icon(busy=True))
        _, vk = winapi.parse_hotkey(mode.hotkey)
        self.worker = threading.Thread(target=self._work, args=(mode, vk), daemon=True)
        self.worker.start()

    # --- worker ----------------------------------------------------------------------------------
    def _work(self, mode: cfgmod.Mode, vk: int) -> None:
        try:
            outcome = self.pipeline.run(mode, self.cfg, vk, on_captured=lambda: self.captured.emit(mode.name))
        except Exception:  # keep the app alive; the traceback holds no user text because locals are not logged
            log.exception("pipeline failed")
            outcome = Outcome(Status.ERROR, "Something went wrong. Your text was not changed.")
        self.finished.emit(outcome)

    def on_finished(self, outcome: Outcome) -> None:
        self.busy = False
        self.cancel_action.setEnabled(False)
        self.tray.setIcon(app_icon())
        if outcome.status is Status.REPLACED:
            if self.cfg.show_success:
                self.indicator.flash(f"Fixed in {outcome.seconds:.1f} s", "ok")
            else:
                self.indicator.hide()
        elif outcome.status is Status.NO_CHANGE:
            self.indicator.flash(f"{outcome.message} ({outcome.seconds:.1f} s)", "ok")
        elif outcome.status is Status.POPUP:
            self.indicator.hide()
            popup = ResultPopup(outcome.message, outcome.original, outcome.corrected)
            self.popups.append(popup)
            popup.destroyed.connect(lambda *_: self.popups.remove(popup) if popup in self.popups else None)
            popup.show()
            popup.raise_()
            popup.activateWindow()
        else:
            self.indicator.flash(outcome.message, "error" if outcome.status is Status.ERROR else "info", 6000)

    # --- startup checks ----------------------------------------------------------------------------
    def _check_startup(self) -> None:
        notes = [claude_cli.billing_warning(), claude_cli.startup_problem(self.cfg.claude_path)]
        text = "\n".join(n for n in notes if n)
        if text:
            self.problem.emit(text)

    def on_startup_problem(self, text: str) -> None:
        self.status_action.setText(text.split(".")[0][:70])
        self.tray.setToolTip(f"{APP_NAME}: {text[:100]}")
        self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Warning)
        log.info("startup note shown")

    # --- menu ------------------------------------------------------------------------------------
    def on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_settings()

    def toggle_pause(self) -> None:
        self.paused = self.pause_action.isChecked()
        self.status_action.setText("Paused" if self.paused else "Ready")

    def open_settings(self) -> None:
        if self.settings_open:
            return
        self.settings_open = True
        self.unregister_hotkeys()  # so that pressing a current shortcut in the editor reaches the editor
        dialog = SettingsDialog(self.cfg)
        try:
            if dialog.exec() and dialog.result_cfg:
                self.cfg = dialog.result_cfg
                cfgmod.save(self.cfg)
        finally:
            self.settings_open = False
            self.register_hotkeys()

    def quit(self) -> None:
        self.runner.cancel()
        self.unregister_hotkeys()
        if self.worker and self.worker.is_alive():
            self.worker.join(3)  # it may be between paste and clipboard restore
        self.tray.hide()
        self.qt_app.quit()


def main() -> int:
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setQuitOnLastWindowClosed(False)
    if not winapi.single_instance():
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running. Look for the blue G in the tray.")
        return 0
    setup_logging()
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "The Windows notification area is not available.")
        return 1
    controller = Controller(qt_app)  # noqa: F841 - must outlive the event loop
    return qt_app.exec()
