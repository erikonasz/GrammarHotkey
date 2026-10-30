"""Small windows: the non-activating indicator and the fallback popup."""
from __future__ import annotations

import ctypes

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
                               QWidget)

from . import APP_NAME


def app_icon(busy: bool = False) -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d97706" if busy else "#2563eb"))
        p.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
        p.setPen(QColor("white"))
        font = p.font()
        font.setBold(True)
        font.setPixelSize(int(size * 0.72))
        p.setFont(font)
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "G")
        p.end()
        icon.addPixmap(pix)
    return icon


class Indicator(QWidget):
    """A pill near the cursor. It must never take focus: the pasted text depends on the target keeping it."""

    COLORS = {"busy": "#1f2937", "ok": "#166534", "info": "#1f2937", "error": "#991b1b"}

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self._hide = QTimer(self, singleShot=True, timeout=self.hide)
        self._tick = QTimer(self, interval=500, timeout=self._on_tick)
        self._base, self._seconds = "", 0.0

    def _style(self, kind: str) -> None:
        self.label.setStyleSheet(f"background:{self.COLORS[kind]};color:white;border-radius:10px;"
                                 "padding:7px 14px;font-size:13px;")

    def _place(self) -> None:
        self.label.adjustSize()
        self.adjustSize()
        pos = QCursor.pos() + QPoint(16, 18)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        pos.setX(min(pos.x(), area.right() - self.width() - 8))
        pos.setY(min(pos.y(), area.bottom() - self.height() - 8))
        self.move(pos)
        self.show()
        hwnd = int(self.winId())
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOPMOST = -20, 0x08000000, 0x00000008
        user32 = ctypes.windll.user32
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOPMOST)

    def busy(self, text: str) -> None:
        self._hide.stop()
        self._base, self._seconds = text, 0.0
        self._style("busy")
        self.label.setText(text)
        self._place()
        self._tick.start()

    def _on_tick(self) -> None:
        self._seconds += 0.5
        self.label.setText(f"{self._base}  {int(self._seconds)} s")

    def flash(self, text: str, kind: str = "info", ms: int = 1800) -> None:
        self._tick.stop()
        self._style(kind)
        self.label.setText(text)
        self._place()
        self._hide.start(ms)


class ResultPopup(QDialog):
    """Shown whenever a result exists but pasting was not safe. The original was not touched."""

    def __init__(self, reason: str, original: str, corrected: str):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(f"{APP_NAME}: not pasted")
        self.setWindowIcon(app_icon())
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        why = QLabel(reason + " Your original text was not changed.")
        why.setWordWrap(True)
        layout.addWidget(why)
        layout.addWidget(QLabel("Result:"))
        self.result = QPlainTextEdit(corrected.replace("\r\n", "\n"))
        layout.addWidget(self.result, 3)
        layout.addWidget(QLabel("Original:"))
        source = QPlainTextEdit(original.replace("\r\n", "\n"))
        source.setReadOnly(True)
        layout.addWidget(source, 2)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton("Copy result", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self._copy)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.result.toPlainText())
