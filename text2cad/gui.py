"""Text2CAD desktop application.

Run with:  pythonw -m text2cad.gui   (or the Text2CAD desktop shortcut)
"""

import os

os.environ.setdefault("QT_API", "pyside6")

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import typing  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Literal  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

import pyvista as pv  # noqa: E402
from pyvistaqt import QtInteractor  # noqa: E402
from PySide6.QtCore import QSettings, Qt, QThread, Signal  # noqa: E402
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from . import templates  # noqa: E402
from .generator import DEFAULT_BACKEND, OLLAMA_HOST, OLLAMA_MODEL  # noqa: E402
from .pipeline import BuildResult, build, build_template  # noqa: E402

APP_NAME = "Text2CAD"
ICON_PATH = PROJECT_ROOT / "assets" / "text2cad.ico"
DENSITIES = {"steel": 7.85e-3, "aluminium": 2.70e-3, "brass": 8.5e-3, "ABS/PLA": 1.1e-3}  # g/mm³
PART_COLOR = "#8fa6bf"


class Worker(QThread):
    """Runs a function off the UI thread. The function receives a progress callback."""

    progress = Signal(str)
    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn(self.progress.emit))
        except Exception as e:  # surface unexpected errors instead of killing the thread silently
            self.done.emit(e)


def check_ollama(model: str) -> str:
    """Returns a status message; starts the Ollama server if it isn't running."""
    import ollama

    client = ollama.Client(host=OLLAMA_HOST, timeout=5)
    try:
        names = [m.model for m in client.list().models]
    except Exception:
        exe = shutil.which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe")
        if not Path(exe).exists():
            return "⚠ Ollama is not installed — AI is unavailable (templates still work). Run install.bat."
        subprocess.Popen([exe, "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(30):
            QThread.sleep(1)
            try:
                names = [m.model for m in client.list().models]
                break
            except Exception:
                continue
        else:
            return "⚠ Could not start Ollama — open the Ollama app, then restart Text2CAD."
    if not any(n == model or n.split(":")[0] == model for n in names):
        return f"⚠ AI model '{model}' is not downloaded. Run:  ollama pull {model}"
    return f"AI ready: {model} (local)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.backend = self.settings.value("backend", DEFAULT_BACKEND)
        self.model = self.settings.value("model", OLLAMA_MODEL)
        self.max_attempts = int(self.settings.value("max_attempts", 4))
        self.current: BuildResult | None = None
        self.history: list[str] = []
        self.worker: Worker | None = None
        self.param_widgets: dict = {}

        self.setWindowTitle(APP_NAME)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1400, 850)
        self._build_ui()
        self._build_menus()
        self._refresh_state()
        self._run(lambda progress: check_ollama(self.model), self._on_ai_status, busy_text="Checking local AI...")

    # ------------------------------------------------------------ UI construction

    def _build_ui(self):
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 12, 6, 12)

        self.prompt_label = QLabel("<b>Describe your part</b>")
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "e.g. L-shaped mounting bracket, 80 mm legs, 40 mm wide, 5 mm thick, "
            "two M6 holes in each leg, 3 mm inner fillet")
        self.prompt.setFixedHeight(110)
        self.generate_btn = QPushButton("Generate  (Ctrl+Enter)")
        self.generate_btn.setDefault(True)
        self.generate_btn.setStyleSheet("QPushButton { background:#3b6ea5; color:white; padding:7px; font-weight:bold; }"
                                        "QPushButton:disabled { background:#9fb4c9; }")
        self.generate_btn.clicked.connect(self.on_generate)
        self.new_btn = QPushButton("New part")
        self.new_btn.clicked.connect(self.on_new)
        row = QHBoxLayout()
        row.addWidget(self.generate_btn, 3)
        row.addWidget(self.new_btn, 1)
        self.history_label = QLabel()
        self.history_label.setWordWrap(True)
        self.history_label.setStyleSheet("color:#555;")

        info_box = QGroupBox("Part")
        info_layout = QVBoxLayout(info_box)
        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_layout.addWidget(self.info_label)
        export_row = QHBoxLayout()
        self.save_step_btn = QPushButton("Save STEP…")
        self.save_stl_btn = QPushButton("Save STL…")
        self.save_code_btn = QPushButton("Save code…")
        self.save_step_btn.clicked.connect(lambda: self.on_save("model.step", "STEP files (*.step *.stp)", ".step"))
        self.save_stl_btn.clicked.connect(lambda: self.on_save("model.stl", "STL files (*.stl)", ".stl"))
        self.save_code_btn.clicked.connect(lambda: self.on_save("part.py", "Python files (*.py)", ".py"))
        for b in (self.save_step_btn, self.save_stl_btn, self.save_code_btn):
            export_row.addWidget(b)
        info_layout.addLayout(export_row)

        self.params_box = QGroupBox("Parameters")
        pv_layout = QVBoxLayout(self.params_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.params_host = QWidget()
        self.params_form = QFormLayout(self.params_host)
        scroll.setWidget(self.params_host)
        pv_layout.addWidget(scroll)
        self.rebuild_btn = QPushButton("Rebuild with these values")
        self.rebuild_btn.clicked.connect(self.on_rebuild)
        pv_layout.addWidget(self.rebuild_btn)

        lv.addWidget(self.prompt_label)
        lv.addWidget(self.prompt)
        lv.addLayout(row)
        lv.addWidget(self.history_label)
        lv.addWidget(info_box)
        lv.addWidget(self.params_box, 1)

        # Right side: 3D view / code / log
        self.tabs = QTabWidget()
        view_host = QWidget()
        view_layout = QVBoxLayout(view_host)
        view_layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(view_host)
        self.plotter.set_background("#dfe6ee", top="#ffffff")
        self.plotter.add_axes()
        view_layout.addWidget(self.plotter.interactor)
        self.tabs.addTab(view_host, "3D view")
        mono = QFont("Consolas", 10)
        self.code_view = QPlainTextEdit(readOnly=True)
        self.code_view.setFont(mono)
        self.tabs.addTab(self.code_view, "CadQuery code")
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setFont(mono)
        self.tabs.addTab(self.log_view, "Log")

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setSizes([430, 970])
        self.setCentralWidget(splitter)

        self.status_label = QLabel()
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(160)
        self.busy.hide()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.busy)

        submit = QAction(self)
        submit.setShortcut(QKeySequence("Ctrl+Return"))
        submit.triggered.connect(self.on_generate)
        self.addAction(submit)

    def _build_menus(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        file_menu.addAction("&New part", self.on_new, QKeySequence.New)
        file_menu.addSeparator()
        file_menu.addAction("Save &STEP…", self.save_step_btn.click, QKeySequence.Save)
        file_menu.addAction("Save S&TL…", self.save_stl_btn.click)
        file_menu.addAction("Save &code…", self.save_code_btn.click)
        file_menu.addAction("Open &output folder", self.on_open_folder)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close, QKeySequence.Quit)

        tpl_menu = mb.addMenu("&Templates")
        for name, spec in templates.TEMPLATES.items():
            act = tpl_menu.addAction(name.replace("_", " ").title(), lambda n=name: self.on_template(n))
            act.setStatusTip(spec["description"])

        view_menu = mb.addMenu("&View")
        view_menu.addAction("&Isometric", lambda: self._camera("iso"), QKeySequence("Ctrl+0"))
        view_menu.addAction("&Top (XY)", lambda: self._camera("xy"), QKeySequence("Ctrl+1"))
        view_menu.addAction("&Front (XZ)", lambda: self._camera("xz"), QKeySequence("Ctrl+2"))
        view_menu.addAction("&Side (YZ)", lambda: self._camera("yz"), QKeySequence("Ctrl+3"))
        self.edges_action = view_menu.addAction("Show &edges")
        self.edges_action.setCheckable(True)
        self.edges_action.setChecked(True)
        self.edges_action.toggled.connect(lambda _: self._show_part())

        ai_menu = mb.addMenu("&AI")
        group = QActionGroup(self)
        for key, label in (("ollama", "Local model (Ollama)"), ("claude", "Claude API (needs ANTHROPIC_API_KEY)")):
            act = ai_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.backend == key)
            act.triggered.connect(lambda _=False, k=key: self._set_setting("backend", k))
            group.addAction(act)
        ai_menu.addSeparator()
        ai_menu.addAction("Local &model name…", self.on_choose_model)
        ai_menu.addAction("Max self-repair &attempts…", self.on_choose_attempts)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction("&About", lambda: QMessageBox.about(
            self, APP_NAME,
            "<b>Text2CAD</b><br>Describe an engineering part in words and get a parametric CAD model.<br><br>"
            "AI: local Ollama model or Claude · CAD kernel: CadQuery / OpenCascade<br>"
            "Mouse: left-drag rotate · right-drag / wheel zoom · middle-drag pan"))

    # ------------------------------------------------------------ helpers

    def _log(self, text: str):
        self.log_view.appendPlainText(text)

    def _set_setting(self, key, value):
        setattr(self, key, value)
        self.settings.setValue(key, value)
        self._log(f"Setting {key} = {value}")

    def _run(self, fn, on_done, busy_text: str):
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = Worker(fn)
        self.worker.progress.connect(lambda msg: (self.status_label.setText(msg), self._log(msg)))
        self.worker.done.connect(lambda res: self._finish(on_done, res))
        self._set_busy(True, busy_text)
        self.worker.start()

    def _finish(self, on_done, result):
        self._set_busy(False)
        if isinstance(result, Exception):
            self._log(f"Unexpected error: {result!r}")
            QMessageBox.critical(self, APP_NAME, f"Unexpected error:\n{result}")
            return
        on_done(result)

    def _set_busy(self, busy: bool, text: str = ""):
        self.busy.setVisible(busy)
        for w in (self.generate_btn, self.new_btn, self.rebuild_btn):
            w.setEnabled(not busy)
        if text:
            self.status_label.setText(text)

    def _camera(self, view):
        {"iso": self.plotter.view_isometric, "xy": self.plotter.view_xy,
         "xz": self.plotter.view_xz, "yz": self.plotter.view_yz}[view]()

    # ------------------------------------------------------------ state rendering

    def _refresh_state(self):
        cur = self.current
        refining = cur is not None
        self.prompt_label.setText("<b>Change the current part</b>" if refining else "<b>Describe your part</b>")
        self.generate_btn.setText("Update part  (Ctrl+Enter)" if refining else "Generate  (Ctrl+Enter)")
        self.history_label.setText(("Built from: " + "  →  ".join(f"“{h}”" for h in self.history)) if self.history else "")
        for b in (self.save_step_btn, self.save_stl_btn, self.save_code_btn):
            b.setEnabled(refining)

        if cur is None:
            self.info_label.setText("No part yet. Describe one above, or pick a part from the <b>Templates</b> menu.")
            self.params_box.hide()
            self.code_view.setPlainText("")
            return

        bx, by, bz = cur.stats["bbox_mm"]
        vol = cur.stats["volume_mm3"]
        masses = " · ".join(f"{k} {vol * d:,.0f} g" for k, d in DENSITIES.items())
        source = f"template <b>{cur.template.replace('_', ' ')}</b>" if cur.mode == "template" else "<b>custom AI code</b>"
        self.info_label.setText(
            f"{cur.summary}<br><br><b>Size:</b> {bx:g} × {by:g} × {bz:g} mm<br><b>Volume:</b> {vol / 1000:,.2f} cm³"
            f"<br><b>Mass:</b> {masses}<br><span style='color:#666'>Built from {source}"
            + (f" in {len(cur.attempts)} attempt(s)" if cur.attempts else "") + "</span>")
        self.code_view.setPlainText(cur.code)
        self._fill_params()
        self._show_part()

    def _show_part(self):
        if self.current is None:
            return
        mesh = pv.read(str(self.current.output_dir / "model.stl"))
        self.plotter.clear_actors()
        self.plotter.add_mesh(mesh, color=PART_COLOR, smooth_shading=False, specular=0.3, name="part")
        if self.edges_action.isChecked():
            self.plotter.add_mesh(mesh.extract_feature_edges(35), color="#2b3a4a", line_width=1.2, name="edges")
        self.plotter.reset_camera()

    def _fill_params(self):
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self.param_widgets = {}
        cur = self.current
        if cur is None or cur.mode != "template":
            self.params_box.hide()
            return
        self.params_box.show()
        spec = templates.TEMPLATES[cur.template]
        for pname, (ann, _default) in templates.parameters(cur.template).items():
            value = cur.params[pname]
            if typing.get_origin(ann) is Literal:
                w = QComboBox()
                w.addItems(list(typing.get_args(ann)))
                w.setCurrentText(value)
            elif ann is bool:
                w = QCheckBox()
                w.setChecked(bool(value))
            elif ann is int:
                w = QSpinBox()
                w.setRange(0, 10000)
                w.setValue(int(value))
            elif ann is float:
                w = QDoubleSpinBox()
                w.setRange(0, 1_000_000)
                w.setDecimals(2)
                w.setSingleStep(0.5)
                w.setSuffix(" °" if pname == "pressure_angle" else (" mm" if pname != "module" else ""))
                w.setValue(float(value))
            else:
                w = QLineEdit(json.dumps([list(s) for s in value]))
            w.setToolTip(spec["params"].get(pname, ""))
            self.params_form.addRow(pname.replace("_", " "), w)
            self.param_widgets[pname] = (ann, w)

    def _read_params(self) -> dict:
        out = {}
        for pname, (ann, w) in self.param_widgets.items():
            if isinstance(w, QComboBox):
                out[pname] = w.currentText()
            elif isinstance(w, QCheckBox):
                out[pname] = w.isChecked()
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                out[pname] = w.value()
            else:
                out[pname] = json.loads(w.text())
        return out

    def _accept(self, result: BuildResult, request: str | None, reset_history: bool = False):
        self._log(f"Output folder: {result.output_dir}")
        for n, a in enumerate(result.attempts, 1):
            self._log(f"  attempt {n}: {a.mode} ({a.detail}) -> {'OK' if a.error is None else a.error}")
        if not result.ok:
            self.status_label.setText("Build failed")
            if result.code:
                self._log("Last attempted code:\n" + result.code)
            msg = f"Could not build the part:\n\n{result.error}"
            if self.current is not None:
                msg += "\n\nThe previous version is still shown."
            QMessageBox.warning(self, APP_NAME, msg)
            return
        if reset_history:
            self.history = []
        if request:
            self.history.append(request)
        self.current = result
        self.status_label.setText("Part built ✔")
        self._refresh_state()

    # ------------------------------------------------------------ actions

    def _on_ai_status(self, message: str):
        self.status_label.setText(message)
        self._log(message)

    def on_generate(self):
        text = self.prompt.toPlainText().strip()
        if not text or (self.worker is not None and self.worker.isRunning()):
            return
        current, backend, model, attempts = self.current, self.backend, self.model, self.max_attempts
        history = [h for h in self.history if h != "edited parameters"]
        if current is not None and current.mode == "template" and self.history and self.history[-1] == "edited parameters":
            history.append(f"(parameters edited by hand: {templates.describe(current.template, current.params)})")
        self._log(f"\n> {text}")

        def job(progress):
            return build(text, current=current, max_attempts=attempts, backend=backend, model=model,
                         on_progress=progress, history=history)

        def done(result):
            self._accept(result, text)
            if result.ok:
                self.prompt.clear()

        self._run(job, done, "Working...")

    def on_template(self, name: str):
        self._log(f"\n> template {name} (defaults)")
        self._run(lambda progress: build_template(name, {}, f"Default {name.replace('_', ' ')}."),
                  lambda r: self._accept(r, f"{name.replace('_', ' ')} template", reset_history=True),
                  f"Building {name}...")

    def on_rebuild(self):
        if self.current is None or self.current.mode != "template":
            return
        try:
            params = self._read_params()
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, APP_NAME, f"Invalid list value: {e}")
            return
        name, summary = self.current.template, self.current.summary
        self._run(lambda progress: build_template(name, params, summary),
                  lambda r: self._accept(r, "edited parameters"), "Rebuilding...")

    def on_new(self):
        self.current = None
        self.history = []
        self.plotter.clear_actors()
        self.prompt.clear()
        self._refresh_state()

    def on_save(self, filename: str, file_filter: str, ext: str):
        if self.current is None:
            return
        base = self.current.template or "part"
        target, _ = QFileDialog.getSaveFileName(self, "Save as", str(Path.home() / "Documents" / f"{base}{ext}"), file_filter)
        if target:
            shutil.copyfile(self.current.output_dir / filename, target)
            self.status_label.setText(f"Saved {target}")

    def on_open_folder(self):
        folder = self.current.output_dir if self.current else PROJECT_ROOT / "outputs"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def on_choose_model(self):
        name, ok = QInputDialog.getText(self, APP_NAME, "Ollama model name:", text=self.model)
        if ok and name.strip():
            self._set_setting("model", name.strip())
            self._run(lambda progress: check_ollama(self.model), self._on_ai_status, "Checking local AI...")

    def on_choose_attempts(self):
        n, ok = QInputDialog.getInt(self, APP_NAME, "Max self-repair attempts:", self.max_attempts, 1, 10)
        if ok:
            self._set_setting("max_attempts", n)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            if QMessageBox.question(self, APP_NAME, "A part is still being generated. Quit anyway?") != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.terminate()
        self.plotter.close()
        event.accept()


def main():
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Text2CAD.App")  # own taskbar icon
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
