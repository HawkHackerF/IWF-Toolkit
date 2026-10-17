"""Main window: state, background task management and interface wiring."""

from __future__ import annotations

import csv
import datetime as _dt
import html
import json
import os
import traceback

import numpy as np
from PySide6.QtCore import QSize, Qt, QThread
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QDialog,
)

from .. import APP_NAME, APP_ORGANISATION, APP_SHORT, APP_VERSION
from ..config import Settings
from ..imaging import (
    ImageDoc,
    IMAGE_FILTER,
    SUPPORTED_EXTS,
    as_text,
    build_montage,
    extract_stream,
    to_display,
)
from ..scan import (
    SCAN_DEPTHS,
    ScanWorker,
    TaskWorker,
    decode_hit,
    hit_capacity_bytes,
    hit_filename,
)
from .preferences import PreferencesDialog
from .pages import (
    EmbedPage,
    ExtractPage,
    HexPage,
    MetadataPage,
    OverviewPage,
    PayloadsPage,
    PlanesPage,
    ReportPage,
    SteganalysisPage,
    StringsPage,
    StructurePage,
    VisualPage,
)
from . import icons
from .theme import DENSITY, OK
from .widgets import TextDialog

# Optional analysis modules. A failure to import one of them must not stop the
# application from starting; the affected page reports the problem instead.
try:
    from .. import metadata as metadata_mod

    METADATA_ERROR = ""
except Exception as exc:  # pragma: no cover - defensive
    metadata_mod = None
    METADATA_ERROR = f"{type(exc).__name__}: {exc}"

try:
    from .. import structure as structure_mod

    STRUCTURE_ERROR = ""
except Exception as exc:  # pragma: no cover - defensive
    structure_mod = None
    STRUCTURE_ERROR = f"{type(exc).__name__}: {exc}"

try:
    from .. import steganalysis as steg_mod

    STEG_ERROR = ""
except Exception as exc:  # pragma: no cover - defensive
    steg_mod = None
    STEG_ERROR = f"{type(exc).__name__}: {exc}"

PAGE_ORDER = [
    ("overview", OverviewPage),
    ("planes", PlanesPage),
    ("visual", VisualPage),
    ("metadata", MetadataPage),
    ("structure", StructurePage),
    ("strings", StringsPage),
    ("hex", HexPage),
    ("steganalysis", SteganalysisPage),
    ("payloads", PayloadsPage),
    ("extract", ExtractPage),
    ("embed", EmbedPage),
    ("report", ReportPage),
]

#: Action attribute -> (glyph name, use the accent tint). Setting the icon on
#: the QAction gives it to both the toolbar and the menus.
ACTION_ICONS: dict[str, tuple[str, bool]] = {
    "act_open": ("open", False),
    "act_reload": ("reload", False),
    "act_close": ("close", False),
    "act_save_report": ("report", False),
    "act_save_montage": ("montage", False),
    "act_export_planes": ("grid", False),
    "act_save_view": ("image", False),
    "act_quit": ("quit", False),
    "act_full": ("run", True),
    "act_meta": ("tag", False),
    "act_struct": ("grid", False),
    "act_steg": ("wave", False),
    "act_dct": ("bars", False),
    "act_palette": ("palette", False),
    "act_scan": ("search", True),
    "act_stop": ("stop", False),
    "act_extract_all": ("download", True),
    "act_extract_sel": ("download", False),
    "act_carve": ("carve", False),
    "act_fit": ("fit", False),
    "act_zoom_in": ("zoom_in", False),
    "act_zoom_out": ("zoom_out", False),
    "act_planes": ("layers", False),
    "act_blue": ("droplet", False),
    "act_docs": ("book", False),
    "act_about": ("info", False),
    "act_prefs": ("sliders", False),
}

#: Page key -> glyph shown on its tab and its entry in the View menu.
PAGE_ICONS: dict[str, str] = {
    "overview": "info",
    "planes": "layers",
    "visual": "image",
    "metadata": "tag",
    "structure": "grid",
    "strings": "text",
    "hex": "hash",
    "steganalysis": "wave",
    "payloads": "package",
    "extract": "download",
    "embed": "upload",
    "report": "report",
}


class MainWindow(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1500, 950)
        self.setAcceptDrops(True)

        self.doc: ImageDoc | None = None
        self.path: str | None = None
        self.metadata_report = None
        self.structure_report = None
        self.steg_summary = None
        self.dct_report = None
        self.palette_report = None
        self.scan_hits: list[dict] = []
        self.last_extraction: dict | None = None
        self.montage = None
        self.montage_tiles: list = []
        self._scan_thread = None
        self._scan_worker = None
        self._tasks: list[TaskWorker] = []
        self._file_bytes: bytes | None = None
        self.settings = Settings.load()

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(16, 16))
        self.setCentralWidget(self.tabs)
        self.pages: dict[str, object] = {}
        for key, cls in PAGE_ORDER:
            page = cls(self)
            self.pages[key] = page
            self.tabs.addTab(page, icons.icon(PAGE_ICONS.get(key, "info"), 16), cls.TITLE)

        self._build_actions()
        self._assign_icons()
        self._build_menus()
        self._build_toolbar()
        self.statusBar().showMessage("Ready. Open an image or drop one onto the window.")
        self.lbl_file = QLabel("  no image loaded  ")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.lbl_file)

        if path:
            self.load_image(path)

    # ------------------------------------------------------------------ setup
    def _build_actions(self):
        def act(text, shortcut=None, handler=None, checkable=False):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if handler:
                action.triggered.connect(handler)
            action.setCheckable(checkable)
            return action

        self.act_open = act("Open image...", "Ctrl+O", self.open_image)
        self.act_reload = act("Reload", "F5", lambda: self.path and self.load_image(self.path))
        self.act_close = act("Close image", "Ctrl+W", self.close_image)
        self.act_save_report = act("Save report...", "Ctrl+S", lambda: self.save_report_part("report", "txt"))
        self.act_save_montage = act("Save bit-plane montage...", None, self.save_montage)
        self.act_export_planes = act("Export all bit planes...", None, self.export_all_planes)
        self.act_save_view = act("Save current view...", None, self.save_current_view)
        self.act_quit = act("Quit", "Ctrl+Q", self.close)

        self.act_full = act("Run full analysis", "F8", self.run_full_analysis)
        self.act_meta = act("Metadata / EXIF", None, lambda: self.run_analysis("metadata"))
        self.act_struct = act("Container structure", None, lambda: self.run_analysis("structure"))
        self.act_steg = act("Steganalysis", None, lambda: self.run_analysis("steganalysis"))
        self.act_dct = act("JPEG DCT domain", None, lambda: self.run_analysis("dct"))
        self.act_palette = act("Palette analysis", None, self.run_palette_analysis)
        self.act_scan = act("Payload sweep", "Ctrl+R", self.start_scan)
        self.act_stop = act("Stop sweep", "Esc", self.stop_scan)
        self.act_extract_all = act("Extract all payloads...", "Ctrl+E", lambda: self.extract_all_payloads(False))
        self.act_extract_sel = act("Extract selected payloads...", None, lambda: self.extract_all_payloads(True))
        self.act_carve = act("Carve embedded object", None, self.carve_embedded)

        self.act_fit = act("Fit to window", "Ctrl+0", lambda: self._view("fit"))
        self.act_zoom_in = act("Zoom in", "Ctrl+=", lambda: self._view("in"))
        self.act_zoom_out = act("Zoom out", "Ctrl+-", lambda: self._view("out"))
        self.act_planes = act("Show bit-plane montage", "Ctrl+B", lambda: self.goto("planes"))
        self.act_blue = act("Open blue channel, bit 0", None, self.preset_blue_lsb)

        self.act_docs = act("Documentation", "F1", self.show_docs)
        self.act_about = act("About", None, self.about)
        self.act_prefs = act("Preferences...", "Ctrl+,", self.open_preferences)

    def _assign_icons(self):
        """Give every action its vector glyph, shared by menus and toolbar."""
        for attr, (name, accent) in ACTION_ICONS.items():
            action = getattr(self, attr, None)
            if action is None:
                continue
            action.setIcon(icons.icon(name, 20, accent=accent))
            # The toolbar is icon only, so the tooltip carries the name and key.
            hint = action.text()
            key = action.shortcut().toString()
            if key:
                hint = f"{hint}  ({key})"
            action.setToolTip(hint)

    def _build_menus(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        file_menu.addAction(self.act_open)
        file_menu.addAction(self.act_reload)
        file_menu.addAction(self.act_close)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save_report)
        file_menu.addAction(self.act_save_montage)
        file_menu.addAction(self.act_export_planes)
        file_menu.addAction(self.act_save_view)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = bar.addMenu("Edit")
        edit_menu.addAction(self.act_prefs)

        view_menu = bar.addMenu("View")
        for index, (key, cls) in enumerate(PAGE_ORDER):
            action = QAction(cls.TITLE, self)
            action.setIcon(icons.icon(PAGE_ICONS.get(key, "info"), 16))
            if index < 9:
                action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(lambda _=False, k=key: self.goto(k))
            view_menu.addAction(action)
        view_menu.addSeparator()
        view_menu.addAction(self.act_fit)
        view_menu.addAction(self.act_zoom_in)
        view_menu.addAction(self.act_zoom_out)
        view_menu.addSeparator()
        view_menu.addAction(self.act_planes)
        view_menu.addAction(self.act_blue)

        analysis_menu = bar.addMenu("Analysis")
        analysis_menu.addAction(self.act_full)
        analysis_menu.addSeparator()
        analysis_menu.addAction(self.act_meta)
        analysis_menu.addAction(self.act_struct)
        analysis_menu.addAction(self.act_steg)
        analysis_menu.addAction(self.act_dct)
        analysis_menu.addAction(self.act_palette)
        analysis_menu.addSeparator()
        analysis_menu.addAction(self.act_scan)
        analysis_menu.addAction(self.act_stop)
        analysis_menu.addSeparator()
        analysis_menu.addAction(self.act_extract_all)
        analysis_menu.addAction(self.act_extract_sel)
        analysis_menu.addSeparator()
        analysis_menu.addAction(self.act_carve)

        help_menu = bar.addMenu("Help")
        help_menu.addAction(self.act_docs)
        help_menu.addAction(self.act_about)

    def _build_toolbar(self):
        """One compact toolbar, grouped by task and separated by hairlines."""
        bar = QToolBar("Main", self)
        bar.setObjectName("main_toolbar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        bar.setIconSize(QSize(20, 20))
        bar.setContentsMargins(4, 2, 4, 2)
        self.addToolBar(bar)
        self.toolbar = bar

        groups = (
            (self.act_open, self.act_reload, self.act_close),
            (self.act_full, self.act_scan, self.act_stop),
            (self.act_extract_all, self.act_carve),
            (self.act_planes, self.act_fit),
            (self.act_save_report,),
            (self.act_prefs, self.act_docs),
        )
        for index, group in enumerate(groups):
            if index:
                bar.addSeparator()
            for action in group:
                bar.addAction(action)

        # Uniform square buttons keep the row tidy regardless of the label
        # length; the extension button is squared off as well so the bar height
        # stays constant.
        density = str(self.settings.get("theme_density"))
        knobs = DENSITY.get(density) or DENSITY["comfortable"]
        size = QSize(knobs["tool_h"] + 10, knobs["tool_h"] + 8)
        for widget in bar.findChildren(QToolButton):
            widget.setObjectName("ToolBarButton")
            widget.setFixedSize(size)
            # The name is assigned after the widget was polished, so ask the
            # style to re-evaluate the #ToolBarButton rules.
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)

    # --------------------------------------------------------------- settings
    def open_preferences(self):
        """Edit the user settings and push the result into the pages."""
        dialog = PreferencesDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings.update(dialog.collect())
        try:
            self.settings.save()
        except OSError as exc:
            QMessageBox.warning(self, "Preferences", f"The settings could not be saved:\n{exc}")
        self.apply_settings()

    def apply_settings(self):
        """Let each page re-read the settings it depends on."""
        for key in ("strings", "payloads", "hex", "extract"):
            page = self.pages.get(key)
            if page is not None and hasattr(page, "apply_settings"):
                page.apply_settings()

    # -------------------------------------------------------------- utilities
    def module(self, name: str):
        return {"metadata": metadata_mod, "structure": structure_mod, "steganalysis": steg_mod}.get(name)

    def module_error(self, name: str) -> str:
        return {
            "metadata": METADATA_ERROR,
            "structure": STRUCTURE_ERROR,
            "steganalysis": STEG_ERROR,
        }.get(name, "module unavailable")

    def steg_module(self):
        return steg_mod

    def steg_module_error(self) -> str:
        return STEG_ERROR or "steganalysis module unavailable"

    def status(self, message: str):
        self.statusBar().showMessage(message, 8000)

    def goto(self, key: str):
        page = self.pages.get(key)
        if page is not None:
            self.tabs.setCurrentWidget(page)

    def _view(self, mode: str):
        page = self.tabs.currentWidget()
        canvas = getattr(page, "canvas", None)
        if canvas is None:
            return
        if mode == "fit":
            canvas.fit()
        elif mode == "in":
            canvas.zoom_by(1.25)
        elif mode == "out":
            canvas.zoom_by(0.8)

    def pages_list(self):
        return [self.pages[key] for key, _ in PAGE_ORDER]

    # ---------------------------------------------------------------- loading
    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open image", "", IMAGE_FILTER)
        if path:
            self.load_image(path)

    def close_image(self):
        self.doc = None
        self.path = None
        self.metadata_report = None
        self.structure_report = None
        self.steg_summary = None
        self.dct_report = None
        self.palette_report = None
        self.scan_hits = []
        self.montage = None
        self._file_bytes = None
        self.lbl_file.setText("  no image loaded  ")
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        for page in self.pages_list():
            page.doc = None
            page.on_document_changed()

    def load_image(self, path: str):
        try:
            doc = ImageDoc(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open image", f"Could not open the image:\n\n{exc}")
            return
        self.stop_scan()
        self.doc = doc
        self.path = path
        self.metadata_report = None
        self.structure_report = None
        self.steg_summary = None
        self.dct_report = None
        self.palette_report = None
        self.scan_hits = []
        self._file_bytes = None
        try:
            with open(path, "rb") as handle:
                self._file_bytes = handle.read()
        except OSError:
            self._file_bytes = None
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION} - {doc.filename}")
        self.lbl_file.setText(f"  {doc.filename}  |  {doc.width}x{doc.height}  |  {doc.format}  ")
        for page in self.pages_list():
            page.doc = doc
            page.on_document_changed()
        self.rebuild_montage()
        self.goto("overview")
        self.status(doc.describe())
        self.run_full_analysis()

    # ------------------------------------------------------------- background
    def run_task(self, label: str, fn, on_result):
        self.set_busy(True, label)
        worker = TaskWorker(fn)

        def done(result):
            self.set_busy(False)
            on_result(result)

        def failed(message: str):
            self.set_busy(False)
            self.status(f"{label} failed.")
            QMessageBox.critical(self, label, message)

        worker.result.connect(done)
        worker.failed.connect(failed)
        worker.finished.connect(lambda: self._tasks.remove(worker) if worker in self._tasks else None)
        self._tasks.append(worker)
        worker.start()

    def set_busy(self, busy: bool, label: str = ""):
        QApplication.setOverrideCursor(Qt.BusyCursor) if busy else QApplication.restoreOverrideCursor()
        self.act_full.setEnabled(not busy)
        for action in (self.act_meta, self.act_struct, self.act_steg, self.act_dct, self.act_palette, self.act_scan):
            action.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        if busy and label:
            self.status(f"{label}...")

    # -------------------------------------------------------------- analysis
    def run_full_analysis(self):
        if self.doc is None:
            QMessageBox.information(self, "Analysis", "Open an image first.")
            return
        path = self.path
        array = self.doc.array

        def work():
            results = {}
            if metadata_mod is not None:
                results["metadata"] = metadata_mod.analyze_metadata(path)
            if structure_mod is not None:
                results["structure"] = structure_mod.analyze_structure(path)
            if steg_mod is not None:
                try:
                    results["steganalysis"] = steg_mod.steganalysis_summary(array)
                except Exception:
                    results["steganalysis"] = {"channels": {}, "findings": [traceback.format_exc()], "verdict": "failed"}
            return results

        self.run_task("Running full analysis", work, self._on_full_analysis)

    def run_analysis(self, kind: str):
        if self.doc is None:
            QMessageBox.information(self, "Analysis", "Open an image first.")
            return
        path = self.path
        array = self.doc.array

        if kind == "metadata":
            if metadata_mod is None:
                QMessageBox.warning(self, "Metadata", f"The metadata module could not be loaded:\n{METADATA_ERROR}")
                return
            self.run_task("Extracting metadata", lambda: metadata_mod.analyze_metadata(path), self._on_full_analysis)

        elif kind == "structure":
            if structure_mod is None:
                QMessageBox.warning(self, "Structure", f"The structure module could not be loaded:\n{STRUCTURE_ERROR}")
                return
            self.run_task("Parsing container", lambda: structure_mod.analyze_structure(path), self._on_full_analysis)

        elif kind == "steganalysis":
            if steg_mod is None:
                QMessageBox.warning(self, "Steganalysis", f"The steganalysis module could not be loaded:\n{STEG_ERROR}")
                return
            self.run_task(
                "Running steganalysis",
                lambda: {"steganalysis": steg_mod.steganalysis_summary(array)},
                self._on_full_analysis,
            )

        elif kind == "dct":
            if steg_mod is None:
                QMessageBox.warning(self, "DCT", f"The steganalysis module could not be loaded:\n{STEG_ERROR}")
                return

            def work():
                out = {}
                try:
                    out["quant"] = steg_mod.quantization_tables(path)
                except Exception as exc:
                    out["quant"] = {"error": str(exc)}
                try:
                    out["dct"] = steg_mod.dct_coefficients(path)
                except Exception as exc:
                    out["dct"] = {"supported": False, "reason": str(exc)}
                try:
                    out["hist"] = steg_mod.dct_lsb_histogram(path)
                except Exception as exc:
                    out["hist"] = {"supported": False, "findings": [str(exc)]}
                return {"dct": out}

            self.run_task("Analysing JPEG DCT domain", work, self._on_full_analysis)
        else:
            raise ValueError(kind)

    def run_palette_analysis(self):
        if self.doc is None:
            QMessageBox.information(self, "Palette", "Open an image first.")
            return
        if steg_mod is None:
            QMessageBox.warning(self, "Palette", f"The steganalysis module could not be loaded:\n{STEG_ERROR}")
            return
        if not self.doc.is_indexed:
            QMessageBox.information(
                self, "Palette analysis",
                "The loaded image is not palette indexed. Palette steganography only applies to "
                "indexed images (PNG or GIF in palette mode).",
            )
            return
        self.run_task(
            "Analysing palette",
            lambda: {"palette": steg_mod.palette_analysis(self.doc.pil)},
            self._on_full_analysis,
        )

    def _on_full_analysis(self, results):
        if not isinstance(results, dict):
            return
        notes = []
        if "metadata" in results:
            self.metadata_report = results["metadata"]
            notes.append("metadata")
        if "structure" in results:
            self.structure_report = results["structure"]
            notes.append("structure")
        if "steganalysis" in results:
            self.steg_summary = results["steganalysis"]
            notes.append("steganalysis")
        if "dct" in results:
            self.dct_report = results["dct"]
            notes.append("dct")
            extra = results["dct"].get("hist", {}).get("findings", [])
            if extra and self.steg_summary is not None:
                self.steg_summary.setdefault("findings", []).extend(extra)
        if "palette" in results:
            self.palette_report = results["palette"]
            notes.append("palette")
            if self.palette_report and self.metadata_report is not None:
                self.metadata_report.findings.extend(self.palette_report.get("findings", []))
        kinds = set(notes) | {"metadata", "structure", "steganalysis", "dct"}
        for page in self.pages_list():
            if page.doc is None:
                continue
            for kind in kinds:
                try:
                    page.on_analysis_ready(kind)
                except Exception:
                    traceback.print_exc()
        if notes:
            self.status("Analysis complete: " + ", ".join(notes))
        self._refresh_overview()

    def _refresh_overview(self):
        page = self.pages.get("overview")
        if page is not None:
            page.on_document_changed()

    # ---------------------------------------------------------------- montage
    def rebuild_montage(self):
        if self.doc is None:
            return
        page = self.pages.get("planes")
        invert = page.chk_invert.isChecked() if page is not None else False
        montage, tiles = build_montage(self.doc, invert=invert)
        self.montage = montage
        self.montage_tiles = tiles
        if page is not None:
            page.show_montage(montage, tiles)

    def save_montage(self):
        if self.montage is None:
            QMessageBox.information(self, "Save montage", "Load an image first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save montage", "bitplanes.png", "PNG (*.png);;All files (*.*)"
        )
        if not path:
            return
        from PIL import Image

        Image.fromarray(self.montage).save(path)
        self.status(f"Montage saved to {path}")

    def export_all_planes(self):
        if self.doc is None:
            QMessageBox.information(self, "Export planes", "Load an image first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose an output folder")
        if not folder:
            return
        from PIL import Image

        doc = self.doc
        count = 0
        for channel in doc.channels:
            values = doc.channel_array(channel)
            if values.ndim == 3:
                values = values[..., 0]
            for bit in range(doc.bits):
                plane = (((values >> bit) & 1) * 255).astype(np.uint8)
                name = f"{os.path.splitext(doc.filename)[0]}_{channel}_bit{bit}.png"
                Image.fromarray(plane, "L").save(os.path.join(folder, name))
                count += 1
        self.status(f"Exported {count} planes to {folder}")

    def save_current_view(self):
        page = self.pages.get("visual")
        image = page.current_image() if page is not None else None
        if image is None:
            QMessageBox.information(self, "Save view", "Nothing is displayed yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save view", "view.png", "PNG (*.png);;All files (*.*)")
        if not path:
            return
        from PIL import Image

        Image.fromarray(image).save(path)
        self.status(f"View saved to {path}")

    def on_tile_clicked(self, payload):
        if not isinstance(payload, dict):
            return
        kind = payload.get("kind")
        if kind in ("plane", "rgbplane"):
            carrier = payload.get("ch") or "RGB"
            bit = int(payload.get("bit") or 0)
            page = self.pages.get("visual")
            if page is not None:
                page.set_selection("RGB" if kind == "rgbplane" else carrier, bit, "plane")
            self.goto("visual")
        elif kind == "orig":
            self.goto("overview")

    def preset_blue_lsb(self):
        if self.doc is None:
            return
        carrier = "B" if self.doc.is_color else "Gray"
        page = self.pages.get("visual")
        if page is not None:
            page.set_selection(carrier, 0, "plane")
        self.goto("visual")

    # ------------------------------------------------------------------- scan
    def start_scan(self):
        if self.doc is None:
            QMessageBox.information(self, "Payload sweep", "Load an image first.")
            return
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return
        page = self.pages.get("payloads")
        depth = page.cb_depth.currentData()
        config = SCAN_DEPTHS.get(depth, SCAN_DEPTHS["standard"])
        worker = ScanWorker(
            self.doc,
            max_bytes=config["max_bytes"],
            offsets=config["offsets"],
            widths=config["widths"],
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(page.set_progress)
        worker.candidates.connect(self._on_scan_candidates)
        worker.flagFound.connect(self._on_scan_flags)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._scan_thread = thread
        self._scan_worker = worker
        page.set_status(f"Running {depth} sweep...")
        page.progress.setValue(0)
        self.act_scan.setEnabled(False)
        thread.start()

    def stop_scan(self):
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(3000)
        self._scan_thread = None
        self._scan_worker = None
        self.act_scan.setEnabled(True)

    def _on_scan_flags(self, hits):
        if isinstance(hits, list):
            self.scan_hits = self._merge_hits(hits + self.scan_hits)
            page = self.pages.get("payloads")
            if page is not None:
                page.show_results(self.scan_hits)

    def _on_scan_candidates(self, hits):
        self.act_scan.setEnabled(True)
        if isinstance(hits, list):
            self.scan_hits = self._merge_hits(self.scan_hits + hits)
            page = self.pages.get("payloads")
            if page is not None:
                page.show_results(self.scan_hits)
        flags = [h for h in self.scan_hits if h.get("kind") == "FLAG"]
        if flags:
            self.status(f"Sweep complete. {len(flags)} keyword flag hit(s) found.")
        else:
            self.status("Sweep complete. No keyword flag found; review the ranked candidates.")

    @staticmethod
    def _merge_hits(hits: list[dict]) -> list[dict]:
        seen = set()
        merged = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            key = (
                tuple(hit.get("spec", ())), hit.get("bit"), hit.get("width"),
                hit.get("offset"), hit.get("msb"), hit.get("match", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
        merged.sort(key=lambda h: (0 if h.get("kind") == "FLAG" else 1, -float(h.get("score", 0))))
        return merged

    def open_candidate(self, hit: dict):
        if self.doc is None:
            return
        try:
            values = decode_hit(self.doc, hit)
        except Exception as exc:
            QMessageBox.critical(self, "Candidate", f"Could not re-decode the candidate:\n{exc}")
            return
        raw = values.tobytes()
        text = as_text(values)
        head = text[:20000]
        printable = "".join(c if 32 <= ord(c) < 127 or c == "\n" else "." for c in head)
        shown = "".join(c if 32 <= ord(c) < 127 else "." for c in text[:1500])
        capacity = hit_capacity_bytes(self.doc, hit)
        dialog = TextDialog(
            f"Decoded payload - {hit.get('label', '')} bit {hit.get('bit', 0)}",
            f"Decoded from : {hit.get('method', '')} | carrier {hit.get('label', '')}\n"
            f"parameters   : bit {hit.get('bit', 0)}, width {hit.get('width', 1)}, "
            f"offset {hit.get('offset', 0)}, "
            f"{'MSB first' if hit.get('msb') else 'LSB first'}\n"
            f"size         : {len(raw)} bytes  (carrier capacity {capacity:,} bytes)\n"
            f"score        : {hit.get('score', 0):.3f}   entropy {hit.get('entropy', 0):.3f}\n"
            f"{'-' * 78}\n\n"
            f"{printable[:20000]}\n\n{'-' * 78}\n"
            f"first 1500 bytes with non-printables marked:\n{shown}",
            self,
            raw=raw,
        )
        dialog.exec()

    # ------------------------------------------------------------ extraction
    def _selected_hits(self) -> list[dict]:
        page = self.pages.get("payloads")
        if page is None:
            return []
        selected = getattr(page, "selected_hits", None)
        return selected() if callable(selected) else []

    def extract_all_payloads(self, selected_only: bool = False):
        """Decode every candidate in full and write the bytes to a folder.

        Unlike the sweep, which only reports that something looks embedded, this
        recovers the complete payload for each hit - text or binary - and drops a
        manifest describing how each one was obtained.
        """
        if self.doc is None:
            QMessageBox.information(self, "Extract payloads", "Load an image first.")
            return
        if selected_only:
            hits = self._selected_hits()
            if not hits:
                QMessageBox.information(
                    self, "Extract payloads",
                    "Select one or more candidate rows first, or use 'Extract all payloads'.",
                )
                return
        else:
            hits = list(self.scan_hits)
            if not hits:
                QMessageBox.information(
                    self, "Extract payloads",
                    "No candidates to extract. Run a payload sweep first.",
                )
                return
        cap = int(self.settings.get("extract_max_mb", 64)) * 1024 * 1024
        if self.settings.get("extract_confirm", True):
            answer = QMessageBox.question(
                self, "Extract payloads",
                f"Decode {len(hits)} candidate(s) in full and write them to a folder?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
        suggested = str(self.settings.get("extract_dir", "")).strip()
        folder = QFileDialog.getExistingDirectory(
            self, "Choose an output folder for the payloads", suggested
        )
        if not folder:
            return
        summary = self._write_payloads(hits, folder, cap)
        note = f"{summary['written']} payload(s) written to {folder}"
        if summary.get("truncated"):
            note += f", {summary['truncated']} truncated at {cap // (1024 * 1024)} MB"
        if summary["failed"]:
            note += f" ({summary['failed']} failed)"
        self.status(note)
        if folder != self.settings.get("extract_dir"):
            self.settings.set("extract_dir", folder)
            try:
                self.settings.save()
            except OSError:
                pass

    def _write_payloads(self, hits: list[dict], folder: str, max_bytes: int | None = None) -> dict:
        os.makedirs(folder, exist_ok=True)
        manifest = []
        written = 0
        failed = 0
        truncated = 0
        for index, hit in enumerate(hits):
            try:
                raw = decode_hit(self.doc, hit).tobytes()
            except Exception as exc:  # noqa: BLE001 - report per-hit, keep going
                failed += 1
                manifest.append({"index": index, "label": hit.get("label", ""), "error": str(exc)})
                continue
            was_truncated = bool(max_bytes) and len(raw) > max_bytes
            if was_truncated:
                raw = raw[:max_bytes]
                truncated += 1
            name = hit_filename(index, hit, raw)
            try:
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(raw)
            except OSError as exc:
                failed += 1
                manifest.append({"index": index, "label": hit.get("label", ""), "error": str(exc)})
                continue
            written += 1
            spec = hit.get("spec", ())
            entry = {
                "index": index,
                "file": name,
                "bytes": len(raw),
                "kind": hit.get("kind", ""),
                "method": hit.get("method", ""),
                "carrier": hit.get("label", ""),
                "spec": list(spec) if isinstance(spec, (list, tuple)) else spec,
                "bit": int(hit.get("bit", 0)),
                "width": int(hit.get("width", 1)),
                "offset": int(hit.get("offset", 0)),
                "order": "MSB first" if hit.get("msb") else "LSB first",
                "score": float(hit.get("score", 0.0)),
                "capacity": hit_capacity_bytes(self.doc, hit),
                "preview": hit.get("snippet", "")[:200],
                "truncated": was_truncated,
            }
            if hit.get("kind") == "FLAG":
                entry["match"] = hit.get("match", "")
            manifest.append(entry)
        payload_manifest = {
            "tool": {"name": APP_NAME, "short": APP_SHORT, "version": APP_VERSION},
            "generated": self._timestamp(),
            "source_image": self.path,
            "payloads": manifest,
        }
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(payload_manifest, handle, indent=2, default=str)
        with open(os.path.join(folder, "manifest.txt"), "w", encoding="utf-8") as handle:
            handle.write(self._payload_index_text(manifest))
        return {"written": written, "failed": failed, "truncated": truncated}

    def _payload_index_text(self, manifest: list[dict]) -> str:
        lines = [
            f"{APP_NAME} {APP_VERSION} - extracted payloads",
            f"Generated : {self._timestamp()}",
            f"Source    : {self.path or 'none'}",
            f"Payloads  : {len(manifest)}",
            "",
        ]
        for entry in manifest:
            if "error" in entry:
                lines.append(f"[{entry['index']:03d}] FAILED {entry.get('label', '')}: {entry['error']}")
                continue
            lines.append(
                f"[{entry['index']:03d}] {entry['file']}  ({entry['bytes']:,} bytes)  "
                f"{entry['kind']}  {entry['carrier']} bit {entry['bit']} "
                f"width {entry['width']} offset {entry['offset']} {entry['order']}"
            )
            if entry.get("match"):
                lines.append(f"      match: {entry['match']}")
            if entry.get("preview"):
                lines.append(f"      preview: {entry['preview']}")
        return "\n".join(lines)

    def export_scan_csv(self):
        if not self.scan_hits:
            QMessageBox.information(self, "Export", "Run a sweep first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results", "scan_results.csv", "CSV (*.csv)")
        if not path:
            return
        columns = ["method", "label", "bit", "width", "offset", "order", "kind", "score", "entropy", "printable", "snippet"]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for hit in self.scan_hits:
                writer.writerow([
                    hit.get("method", ""), hit.get("label", ""), hit.get("bit", 0), hit.get("width", 1),
                    hit.get("offset", 0), "MSB" if hit.get("msb") else "LSB", hit.get("kind", ""),
                    f"{hit.get('score', 0):.4f}", f"{hit.get('entropy', 0):.4f}",
                    f"{hit.get('printable', 0):.4f}", hit.get("snippet", ""),
                ])
        self.status(f"Exported {len(self.scan_hits)} results to {path}")

    def export_scan_json(self):
        if not self.scan_hits:
            QMessageBox.information(self, "Export", "Run a sweep first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results", "scan_results.json", "JSON (*.json)")
        if not path:
            return
        clean = [self._jsonable(hit) for hit in self.scan_hits]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, indent=2)
        self.status(f"Exported {len(self.scan_hits)} results to {path}")

    @staticmethod
    def _jsonable(value):
        if isinstance(value, dict):
            return {k: MainWindow._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [MainWindow._jsonable(v) for v in value]
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            return float(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
        return value

    # ---------------------------------------------------------------- carving
    def carve_embedded(self):
        if self.structure_report is None:
            QMessageBox.information(self, "Carve", "Parse the container structure first.")
            return
        candidates = [
            e for e in self.structure_report.embedded
            if e.offset > 0 and e.length > 0
        ]
        if not candidates:
            QMessageBox.information(
                self, "Carve",
                "No embedded object with a known length was found. Check the Structure tab for raw hits.",
            )
            return
        # Prefer the first object located after the logical end of the image.
        trailing = self.structure_report.trailing_offset
        candidates.sort(key=lambda e: (0 if trailing >= 0 and e.offset >= trailing else 1, e.offset))
        target = candidates[0]
        data = self._raw_bytes()
        if data is None:
            return
        blob = data[target.offset : target.offset + target.length]
        suggested = f"carved_{target.kind.lower()}.bin"
        path, _ = QFileDialog.getSaveFileName(self, "Save carved object", suggested, "All files (*.*)")
        if not path:
            return
        with open(path, "wb") as handle:
            handle.write(blob)
        self.status(f"Carved {len(blob)} bytes ({target.kind}) to {path}")

    def _raw_bytes(self) -> bytes | None:
        if self._file_bytes is not None:
            return self._file_bytes
        if not self.path:
            return None
        try:
            with open(self.path, "rb") as handle:
                self._file_bytes = handle.read()
            return self._file_bytes
        except OSError as exc:
            QMessageBox.critical(self, "Read file", str(exc))
            return None

    # ---------------------------------------------------------------- reports
    def _timestamp(self) -> str:
        return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def build_report_dict(self) -> dict:
        doc = self.doc
        data = {
            "tool": {"name": APP_NAME, "short": APP_SHORT, "version": APP_VERSION},
            "generated": self._timestamp(),
            "file": {
                "path": self.path,
                "name": doc.filename if doc else None,
                "size": doc.file_size if doc else None,
                "format": doc.format if doc else None,
                "mode": doc.original_mode if doc else None,
                "width": doc.width if doc else None,
                "height": doc.height if doc else None,
                "bit_depth": doc.bits if doc else None,
                "channels": doc.channels if doc else None,
            },
            "metadata": self.metadata_report.to_dict() if self.metadata_report is not None else None,
            "structure": self.structure_report.to_dict() if self.structure_report is not None else None,
            "steganalysis": self._jsonable(self.steg_summary) if self.steg_summary is not None else None,
            "dct": self._jsonable(self.dct_report) if self.dct_report is not None else None,
            "palette": self._jsonable(self.palette_report) if self.palette_report is not None else None,
            "payloads": [self._jsonable(hit) for hit in self.scan_hits],
        }
        return data

    def build_report(self, fmt: str = "txt") -> str:
        fmt = fmt.lower()
        if fmt == "json":
            return json.dumps(self.build_report_dict(), indent=2, default=str)
        sections = []
        doc = self.doc
        title = f"{APP_NAME} {APP_VERSION} - forensic report"
        sections.append((title, [
            f"Generated      : {self._timestamp()}",
            f"Operator       : {os.environ.get('USERNAME') or os.environ.get('USER') or 'unknown'}",
            f"Host           : {os.environ.get('COMPUTERNAME') or 'unknown'}",
            "",
            f"File           : {self.path or 'none'}",
        ]))
        if doc is not None:
            sections.append(("File properties", [
                f"Name           : {doc.filename}",
                f"Format         : {doc.format}",
                f"Size           : {doc.file_size:,} bytes",
                f"Colour mode    : {doc.original_mode}",
                f"Dimensions     : {doc.width} x {doc.height} pixels",
                f"Samples        : {doc.bits} bit",
                f"Channels       : {', '.join(doc.channels)}",
                f"Frames         : {doc.n_frames}",
                f"Indexed        : {'yes, ' + str(len(doc.palette)) + ' palette entries' if doc.is_indexed else 'no'}",
                f"LSB capacity   : {doc.capacity_bytes('B' if doc.is_color else 'Gray'):,} bytes at bit 0",
            ]))
        if self.metadata_report is not None:
            sections.append(("Metadata", self.metadata_report.to_text().splitlines()))
        if self.structure_report is not None:
            sections.append(("Container structure", self.structure_report.to_text().splitlines()))
        if self.steg_summary is not None:
            lines = []
            for channel, data in self.steg_summary.get("channels", {}).items():
                lines.append(
                    f"{channel:<5} entropy {data.get('entropy', 0):.4f}  "
                    f"LSB ones {data.get('lsb_ones_ratio', 0):.4f}  "
                    f"chi-square p {data.get('chi_square', {}).get('p', 0):.4f}  "
                    f"RS {data.get('rs', {}).get('estimated_rate', 0):.4f}  "
                    f"SPA {data.get('spa', {}).get('rate', 0):.4f}"
                )
                lines.append(f"      verdict: {data.get('verdict', '')}")
            lines.append("")
            lines.append("Findings:")
            for finding in self.steg_summary.get("findings", []):
                lines.append(f"  - {finding}")
            sections.append(("Steganalysis", lines))
        if self.dct_report is not None:
            lines = []
            quant = self.dct_report.get("quant", {})
            if isinstance(quant, dict) and "tables" in quant:
                lines.append(f"Quantization tables: {quant.get('count', 0)}")
                for tid, table in quant.get("tables", {}).items():
                    lines.append(f"  table {tid} sum {quant.get('sums', {}).get(tid, '')}")
            dct = self.dct_report.get("dct", {})
            if isinstance(dct, dict):
                lines.append(f"DCT coefficients supported: {dct.get('supported')}")
                if dct.get("reason"):
                    lines.append(f"  reason: {dct['reason']}")
            for finding in self.dct_report.get("hist", {}).get("findings", []) or []:
                lines.append(f"  - {finding}")
            sections.append(("JPEG DCT domain", lines))
        if self.palette_report is not None:
            lines = [f"{k}: {v}" for k, v in self.palette_report.items() if k not in ("palette", "findings")]
            lines += ["Findings:"] + [f"  - {f}" for f in self.palette_report.get("findings", [])]
            sections.append(("Palette analysis", lines))
        if self.scan_hits:
            lines = []
            for hit in self.scan_hits[:200]:
                lines.append(
                    f"[{hit.get('kind', ''):<7}] {hit.get('method', ''):<22} "
                    f"{hit.get('label', ''):<10} bit {hit.get('bit', 0)} width {hit.get('width', 1)} "
                    f"offset {hit.get('offset', 0)} {'MSB' if hit.get('msb') else 'LSB'} "
                    f"score {hit.get('score', 0):.3f} :: {hit.get('snippet', '')[:80]}"
                )
            sections.append((f"Payload sweep candidates ({len(self.scan_hits)})", lines))

        if fmt == "html":
            return self._render_html(sections)
        blocks = []
        for heading, lines in sections:
            blocks.append(heading)
            blocks.append("=" * len(heading))
            blocks.extend(lines)
            blocks.append("")
        return "\n".join(blocks)

    @staticmethod
    def _render_html(sections) -> str:
        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<style>body{background:#141519;color:#dcdee2;font-family:Consolas,monospace;font-size:12px;padding:24px;}",
            "h1{font-size:18px;border-bottom:1px solid #383b42;padding-bottom:6px;}",
            "h2{font-size:14px;color:#8fb4dd;margin-top:22px;border-bottom:1px solid #2a2d33;}",
            "pre{white-space:pre-wrap;margin:0;}</style></head><body>",
        ]
        for heading, lines in sections:
            parts.append(f"<h2>{html.escape(heading)}</h2><pre>{html.escape(chr(10).join(str(x) for x in lines))}</pre>")
        parts.append("</body></html>")
        return "".join(parts)

    def save_report_part(self, part: str, fmt: str):
        if self.doc is None:
            QMessageBox.information(self, "Report", "Open an image first.")
            return
        if part == "metadata" and self.metadata_report is not None:
            content = self.metadata_report.to_text() if fmt == "txt" else json.dumps(self.metadata_report.to_dict(), indent=2, default=str)
            default = "metadata." + fmt
        elif part == "structure" and self.structure_report is not None:
            content = self.structure_report.to_text() if fmt == "txt" else json.dumps(self.structure_report.to_dict(), indent=2, default=str)
            default = "structure." + fmt
        elif part == "steganalysis" and self.steg_summary is not None:
            content = json.dumps(self._jsonable(self.steg_summary), indent=2, default=str)
            default = "steganalysis." + fmt
        else:
            part = "report"
            content = self.build_report(fmt if fmt in ("html", "json") else "txt")
            default = "forensic_report." + fmt
        filters = {"txt": "Text (*.txt)", "json": "JSON (*.json)", "html": "HTML (*.html)"}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "Save " + part, default, filters)
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        self.status(f"Saved {part} to {path}")

    # ------------------------------------------------------------------ misc
    def show_docs(self):
        page = self.pages.get("report")
        text = (
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Workflow\n"
            "--------\n"
            "1. Open an image (Ctrl+O) or drop it onto the window. A full analysis runs automatically.\n"
            "2. Overview   - file properties, digests, entropy and a combined findings list.\n"
            "3. Bit Planes - every bit plane of every channel rendered as one zoomable montage.\n"
            "                Click any tile to inspect that plane at full resolution.\n"
            "4. Visual     - bit plane isolation plus derived views: error level analysis,\n"
            "                noise residual, channel combinations, autocorrelation, LSB density.\n"
            "5. Metadata   - full EXIF, GPS, XMP, ICC, IPTC and container metadata.\n"
            "6. Structure  - every chunk or segment, trailing data, embedded objects, hex viewer.\n"
            "7. Steganalysis - chi-square, RS and SPA detectors per channel, plus JPEG DCT analysis.\n"
            "8. Payloads   - systematic decoder sweep over spatial, palette and derived carriers.\n"
            "9. Extract    - manual extraction with bit width, offset and bit order control.\n"
            "10. Embed     - lossless LSB embedding for verification and counter-testing.\n"
            "11. Report    - consolidated, exportable forensic report (text, HTML, JSON).\n\n"
            "Shortcuts\n"
            "---------\n"
            "Ctrl+O open   F5 reload   F8 full analysis   Ctrl+R payload sweep   Esc stop\n"
            "Ctrl+B montage   Ctrl+1..9 switch page   Ctrl+S save report\n"
        )
        TextDialog("Documentation", text, self).exec()

    def about(self):
        QMessageBox.about(
            self,
            f"About {APP_SHORT}",
            f"<b>{APP_NAME}</b> {APP_VERSION}<br><br>"
            "An image forensics and steganalysis workbench: metadata extraction, container "
            "structure analysis, payload carving, statistical steganalysis, bit-plane and "
            "derived-carrier visualisation, and systematic payload discovery.<br><br>"
            f"Built with PySide6, NumPy and Pillow.<br>{APP_ORGANISATION}",
        )

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and os.path.splitext(url.toLocalFile())[1].lower() in SUPPORTED_EXTS:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):  # noqa: N802
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.splitext(path)[1].lower() in SUPPORTED_EXTS:
                    self.load_image(path)
                    event.acceptProposedAction()
                    return
        event.ignore()
