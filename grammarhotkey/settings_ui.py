"""Settings window: modes and shortcuts, model, autostart, exclusions."""
from __future__ import annotations

import copy
import sys
import threading
import uuid
import winreg
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from . import APP_NAME, claude_cli, config as cfgmod, winapi
from .ui_widgets import app_icon

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve().parent.parent / "run.pyw"}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, "GrammarHotkey")
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "GrammarHotkey", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, "GrammarHotkey")
            except OSError:
                pass


def hotkey_warning(text: str) -> str:
    parsed = winapi.parse_hotkey(text)
    if not text:
        return ""
    if not parsed:
        return f"{text}: use Ctrl, Alt or Win plus a letter, digit or F key."
    mods, vk = parsed
    if mods & winapi.MOD_CONTROL and mods & winapi.MOD_ALT:
        chars = winapi.altgr_conflicts(vk)
        if chars:
            return f"{text} is also AltGr on your keyboard and would block typing: {' '.join(chars)}"
    return ""


class SettingsDialog(QDialog):
    _check_done = Signal(str)

    def __init__(self, cfg: cfgmod.Config):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} settings")
        self.setWindowIcon(app_icon())
        self.resize(760, 560)
        self.cfg = copy.deepcopy(cfg)
        self.result_cfg: cfgmod.Config | None = None
        self._row = -1

        tabs = QTabWidget()
        tabs.addTab(self._build_modes(), "Modes and shortcuts")
        tabs.addTab(self._build_general(), "General")
        tabs.addTab(self._build_exclusions(), "Excluded apps")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        self._check_done.connect(self.check_label.setText)
        self._fill_modes()

    # --- modes ---------------------------------------------------------------------------------
    def _build_modes(self) -> QWidget:
        page = QWidget()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Shortcut", "On"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.currentCellChanged.connect(lambda row, *_: self._select(row))
        add = QPushButton("Add custom prompt")
        add.clicked.connect(self._add)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        reset = QPushButton("Restore defaults")
        reset.clicked.connect(self._reset)
        row = QHBoxLayout()
        for b in (add, remove, reset):
            row.addWidget(b)
        row.addStretch()
        self.warning = QLabel()
        self.warning.setStyleSheet("color:#b45309")
        self.warning.setWordWrap(True)
        self.prompt = QPlainTextEdit()
        self.strict = QCheckBox("Strict check: refuse results that differ a lot from the original, "
                                "or that lose a link or emoji")
        layout = QVBoxLayout(page)
        layout.addWidget(self.table, 2)
        layout.addLayout(row)
        layout.addWidget(self.warning)
        layout.addWidget(QLabel("Instruction sent to Claude for the selected mode:"))
        layout.addWidget(self.prompt, 3)
        layout.addWidget(self.strict)
        return page

    def _fill_modes(self) -> None:
        self._row = -1
        self.table.setRowCount(0)
        for mode in self.cfg.modes:
            self._append_row(mode)
        if self.cfg.modes:
            self.table.setCurrentCell(0, 0)
        self._refresh_warnings()

    def _append_row(self, mode: cfgmod.Mode) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(mode.name))
        edit = QKeySequenceEdit(QKeySequence(mode.hotkey))
        edit.setMaximumSequenceLength(1)
        edit.setClearButtonEnabled(True)
        edit.keySequenceChanged.connect(lambda _: self._refresh_warnings())
        self.table.setCellWidget(row, 1, edit)
        on = QCheckBox()
        on.setChecked(mode.enabled)
        self.table.setCellWidget(row, 2, on)

    def _select(self, row: int) -> None:
        self._store_prompt()
        self._row = row
        if 0 <= row < len(self.cfg.modes):
            self.prompt.setPlainText(self.cfg.modes[row].prompt)
            self.strict.setChecked(self.cfg.modes[row].strict)

    def _store_prompt(self) -> None:
        if 0 <= self._row < len(self.cfg.modes):
            self.cfg.modes[self._row].prompt = self.prompt.toPlainText().strip()
            self.cfg.modes[self._row].strict = self.strict.isChecked()

    def _sync_rows(self) -> None:
        self._store_prompt()
        for row, mode in enumerate(self.cfg.modes):
            mode.name = self.table.item(row, 0).text().strip() or mode.name
            mode.hotkey = self.table.cellWidget(row, 1).keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            mode.enabled = self.table.cellWidget(row, 2).isChecked()

    def _add(self) -> None:
        self._sync_rows()
        self.cfg.modes.append(cfgmod.Mode(uuid.uuid4().hex[:8], "Custom", "",
                                          "Describe here what to do with the selected text."))
        self._fill_modes()
        self.table.setCurrentCell(len(self.cfg.modes) - 1, 0)

    def _remove(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.cfg.modes):
            self._sync_rows()
            del self.cfg.modes[row]
            self._fill_modes()

    def _reset(self) -> None:
        self.cfg.modes = cfgmod.default_modes()
        self._fill_modes()

    def _hotkey_texts(self) -> list[str]:
        return [self.table.cellWidget(r, 1).keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                for r in range(self.table.rowCount())]

    def _refresh_warnings(self) -> None:
        notes = [w for w in map(hotkey_warning, self._hotkey_texts()) if w]
        self.warning.setText("\n".join(notes))

    # --- general ---------------------------------------------------------------------------------
    def _build_general(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(cfgmod.MODELS)
        self.model.setCurrentText(self.cfg.model)
        form.addRow("Model", self.model)
        self.effort = QComboBox()
        self.effort.addItems(cfgmod.EFFORTS)
        self.effort.setCurrentText(self.cfg.effort)
        form.addRow("Effort (low is fastest)", self.effort)
        self.timeout = QSpinBox(minimum=10, maximum=300, suffix=" s", value=self.cfg.timeout_s)
        form.addRow("Timeout", self.timeout)
        self.paste_delay = QSpinBox(minimum=100, maximum=3000, singleStep=50, suffix=" ms", value=self.cfg.paste_delay_ms)
        form.addRow("Wait after paste", self.paste_delay)
        self.max_chars = QSpinBox(minimum=100, maximum=200000, singleStep=1000, value=self.cfg.max_chars)
        form.addRow("Longest selection (characters)", self.max_chars)
        self.autostart = QCheckBox("Start with Windows")
        self.autostart.setChecked(autostart_enabled())
        form.addRow("", self.autostart)
        self.show_success = QCheckBox("Show a short note after each correction")
        self.show_success.setChecked(self.cfg.show_success)
        form.addRow("", self.show_success)
        self.claude_path = QLineEdit(self.cfg.claude_path)
        self.claude_path.setPlaceholderText("Empty = find it automatically")
        form.addRow("Path to claude", self.claude_path)
        check = QPushButton("Check Claude Code")
        check.clicked.connect(self._check)
        self.check_label = QLabel()
        self.check_label.setWordWrap(True)
        form.addRow(check, self.check_label)
        return page

    def _check(self) -> None:
        self.check_label.setText("Checking...")
        path = self.claude_path.text().strip()

        def work():
            problem = claude_cli.startup_problem(path) or claude_cli.billing_warning()
            if not problem:
                status = claude_cli.auth_status(path)
                problem = f"OK: signed in, plan {status.get('subscriptionType') or 'unknown'}."
            self._check_done.emit(problem)

        threading.Thread(target=work, daemon=True).start()

    # --- exclusions ------------------------------------------------------------------------------
    def _build_exclusions(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("One program file per line. In these apps the shortcut does nothing and no text is read. "
                      "Password managers are here for privacy, terminals because Ctrl+C would stop the "
                      "running program, remote desktops because the keys would land on the other computer.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.excluded = QPlainTextEdit("\n".join(self.cfg.excluded))
        layout.addWidget(self.excluded)
        return page

    # --- save ------------------------------------------------------------------------------------
    def _save(self) -> None:
        self._sync_rows()
        seen, problems = {}, []
        for mode in self.cfg.modes:
            if not mode.prompt:
                problems.append(f"{mode.name}: the instruction is empty.")
            if not mode.hotkey:
                continue
            parsed = winapi.parse_hotkey(mode.hotkey)
            if not parsed:
                problems.append(f"{mode.name}: {mode.hotkey} cannot be used. Use Ctrl, Alt or Win plus a letter, "
                                "digit or F key.")
            elif parsed in seen:
                problems.append(f"{mode.name} and {seen[parsed]} use the same shortcut.")
            else:
                seen[parsed] = mode.name
        if problems:
            QMessageBox.warning(self, APP_NAME, "\n".join(problems))
            return
        self.cfg.model = self.model.currentText().strip() or "sonnet"
        self.cfg.effort = self.effort.currentText()
        self.cfg.timeout_s = self.timeout.value()
        self.cfg.paste_delay_ms = self.paste_delay.value()
        self.cfg.max_chars = self.max_chars.value()
        self.cfg.show_success = self.show_success.isChecked()
        self.cfg.claude_path = self.claude_path.text().strip()
        self.cfg.excluded = [x.strip().lower() for x in self.excluded.toPlainText().splitlines() if x.strip()]
        try:
            set_autostart(self.autostart.isChecked())
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not change autostart: {exc}")
        self.result_cfg = self.cfg
        self.accept()
