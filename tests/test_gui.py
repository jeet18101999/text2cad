"""Smoke test for the desktop app: opens the window, builds a template part, saves a screenshot.

Run: python tests/test_gui.py [screenshot.png]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from text2cad.gui import MainWindow  # noqa: E402

shot = Path(sys.argv[1]) if len(sys.argv) > 1 else None
app = QApplication(sys.argv)
app.setStyle("Fusion")
win = MainWindow()
win.show()
failures = []


def step_build():
    win.on_template("spur_gear")
    QTimer.singleShot(500, wait_build)


def wait_build():
    if win.worker is not None and win.worker.isRunning():
        QTimer.singleShot(300, wait_build)
        return
    if win.current is None or win.current.template != "spur_gear":
        failures.append("template build did not produce a part")
    else:
        # Edit a parameter and rebuild through the form.
        ann, widget = win.param_widgets["teeth"]
        widget.setValue(24)
        win.on_rebuild()
        QTimer.singleShot(500, wait_rebuild)
        return
    finish()


def wait_rebuild():
    if win.worker is not None and win.worker.isRunning():
        QTimer.singleShot(300, wait_rebuild)
        return
    if win.current.params.get("teeth") != 24 or win.history[-1] != "edited parameters":
        failures.append(f"rebuild failed: {win.current.params}")
    if win.current.stats["bbox_mm"][0] != 52.0:  # module 2 * (24 + 2)
        failures.append(f"unexpected gear size {win.current.stats['bbox_mm']}")
    finish()


def finish():
    app.processEvents()
    if shot:
        win.grab().save(str(shot))
    print("GUI status:", win.status_label.text())
    print("FAIL: " + "; ".join(failures) if failures else "GUI smoke test passed")
    win.close()
    app.exit(1 if failures else 0)


QTimer.singleShot(1500, step_build)
sys.exit(app.exec())
