"""Regenerates assets/icon.ico from the same drawing the tray uses."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication  # noqa: E402

from quickfix.ui_widgets import app_icon  # noqa: E402

app = QApplication([])
out = Path(__file__).resolve().parent.parent / "assets/icon.ico"
out.parent.mkdir(exist_ok=True)
assert app_icon().pixmap(64, 64).save(str(out), "ICO"), "Qt could not write the icon"
