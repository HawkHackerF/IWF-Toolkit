"""Workspace pages: one self-contained widget per analysis discipline.

Each page receives the main window as `app` and reads its state (`app.doc`,
`app.metadata_report`, `app.structure_report`, `app.steg_summary`, `app.scan_hits`)
so the pages stay free of cross-page plumbing.
"""

from __future__ import annotations

import html
import json
import os

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..imaging import (
    ImageDoc,
    as_text,
    build_montage,
    compute_view,
    embed_bytes,
    extract_stream,
    find_strings,
    plane_stats,
    to_display,
)
from ..scan import SCAN_DEPTHS, extract_for_hit, search_pixels
from .theme import ACCENT, DANGER, FG_DIM, OK, WARN, mono_font
from .widgets import (
    FindingList,
    HexEditor,
    HexView,
    ImageView,
    NumericItem,
    TextDialog,
    fill_kv_table,
    make_table,
)

DIFF_KEYS = ["R^G", "R^B", "G^B", "|R-G|", "|R-B|", "|G-B|"]


def group(title: str) -> QGroupBox:
    box = QGroupBox(title)
    return box


# ---------------------------------------------------------------------------
class Page(QWidget):
    """Base page: knows the main window and reacts to document changes."""

    KEY = ""
    TITLE = ""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.doc: ImageDoc | None = None

    def on_document_changed(self):
        """Called after a new image has been loaded."""

    def on_analysis_ready(self, kind: str):
        """Called when a background analysis of `kind` has finished."""


# ---------------------------------------------------------------------------
class OverviewPage(Page):
    KEY = "overview"
    TITLE = "Overview"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        layout.addWidget(split, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = ImageView()
        self.canvas.set_tile_mode(False)
        left_layout.addWidget(self.canvas, 1)
        self.lbl_pixel = QLabel("")
        self.lbl_pixel.setFont(mono_font(9))
        left_layout.addWidget(self.lbl_pixel)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.table_file = make_table(["Property", "Value"], stretch_columns=[1])
        self.table_hashes = make_table(["Digest", "Value"], stretch_columns=[1])
        self.findings = FindingList()
        right_layout.addWidget(QLabel("File properties"))
        right_layout.addWidget(self.table_file, 2)
        right_layout.addWidget(QLabel("Integrity"))
        right_layout.addWidget(self.table_hashes, 1)
        right_layout.addWidget(QLabel("Findings"))
        right_layout.addWidget(self.findings, 3)
        split.addWidget(right)
        split.setSizes([760, 520])

        actions = QHBoxLayout()
        self.btn_analyze = QPushButton("Run full analysis")
        self.btn_analyze.setProperty("accent", True)
        self.btn_analyze.clicked.connect(app.run_full_analysis)
        self.btn_report = QPushButton("Open consolidated report")
        self.btn_report.clicked.connect(lambda: app.goto("report"))
        actions.addWidget(self.btn_analyze)
        actions.addWidget(self.btn_report)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.canvas.pixelHover.connect(self._on_hover)

    def on_document_changed(self):
        self.doc = self.app.doc
        if self.doc is None:
            return
        doc = self.doc
        self.canvas.set_image(to_display(doc.array))
        self.canvas.fit()
        rows = [
            ("File name", doc.filename, ""),
            ("Path", doc.path, ""),
            ("File size", f"{doc.file_size:,} bytes", ""),
            ("Container format", doc.format, ""),
            ("Colour mode", doc.original_mode, ""),
            ("Dimensions", f"{doc.width} x {doc.height} px ({doc.pixels:,} pixels)", ""),
            ("Working array", " x ".join(str(v) for v in doc.array.shape) + f" {doc.array.dtype}", ""),
            ("Bit depth", f"{doc.bits} bit per sample", ""),
            ("Channels", ", ".join(doc.channels), ""),
            ("Frames", str(doc.n_frames), "warn" if doc.n_frames > 1 else ""),
            ("Indexed palette", f"yes, {len(doc.palette)} entries" if doc.is_indexed else "no", ""),
            ("LSB capacity (B@bit0)", f"{doc.capacity_bytes('B' if doc.is_color else 'Gray'):,} bytes", ""),
        ]
        for note in doc.notes:
            rows.append(("Note", note, "info"))
        fill_kv_table(self.table_file, rows)

        report = self.app.metadata_report
        if report is not None:
            fill_kv_table(
                self.table_hashes,
                [("Format", report.file_format, ""),
                 ("MD5", report.hashes.get("md5", ""), ""),
                 ("SHA-1", report.hashes.get("sha1", ""), ""),
                 ("SHA-256", report.hashes.get("sha256", ""), ""),
                 ("Entropy", f"{report.entropy:.4f} bit/byte", "")],
            )
            findings = list(report.findings)
            if self.app.structure_report is not None:
                findings += self.app.structure_report.findings
            if self.app.steg_summary is not None:
                findings += self.app.steg_summary.get("findings", [])
            self.findings.set_findings(findings)
        else:
            fill_kv_table(self.table_hashes, [("Digests", "run full analysis", "info")])
            self.findings.set_findings([])

    def _on_hover(self, x: int, y: int):
        if self.doc is None:
            return
        arr = self.doc.array
        if 0 <= y < arr.shape[0] and 0 <= x < arr.shape[1]:
            values = arr[y, x]
            text = " ".join(str(int(v)) for v in np.atleast_1d(values))
            names = " ".join(self.doc.channels)
            self.lbl_pixel.setText(f"x={x:5d}  y={y:5d}   {names} = {text}")


# ---------------------------------------------------------------------------
class PlanesPage(Page):
    KEY = "planes"
    TITLE = "Bit Planes"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.chk_invert = QCheckBox("Invert planes")
        self.chk_invert.stateChanged.connect(lambda _: app.rebuild_montage())
        bar.addWidget(self.chk_invert)
        self.btn_save = QPushButton("Save montage")
        self.btn_save.clicked.connect(app.save_montage)
        self.btn_export = QPushButton("Export all planes")
        self.btn_export.clicked.connect(app.export_all_planes)
        self.btn_fit = QPushButton("Fit")
        self.btn_fit.clicked.connect(lambda: self.canvas.fit())
        self.btn_100 = QPushButton("100%")
        self.btn_100.clicked.connect(lambda: self.canvas.zoom_to(1.0))
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_export)
        bar.addWidget(self.btn_fit)
        bar.addWidget(self.btn_100)
        bar.addStretch(1)
        self.lbl_hint = QLabel("Click a tile to open that plane at full resolution.")
        self.lbl_hint.setStyleSheet(f"color: {FG_DIM.name()};")
        bar.addWidget(self.lbl_hint)
        layout.addLayout(bar)

        self.canvas = ImageView()
        self.canvas.tileClicked.connect(app.on_tile_clicked)
        layout.addWidget(self.canvas, 1)

    def on_document_changed(self):
        self.doc = self.app.doc

    def show_montage(self, montage: np.ndarray, tiles: list):
        self.canvas.set_image(montage, tiles=tiles)
        self.canvas.fit()


# ---------------------------------------------------------------------------
class VisualPage(Page):
    KEY = "visual"
    TITLE = "Visual Analysis"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()

        self.cb_transform = QComboBox()
        self.cb_transform.addItem("Bit plane isolation", "plane")
        self.cb_transform.addItem("Error level analysis (ELA)", "ela")
        self.cb_transform.addItem("Noise residual (median)", "residual")
        self.cb_transform.addItem("Channel combination", "difference")
        self.cb_transform.addItem("Autocorrelation map", "autocorr")
        self.cb_transform.addItem("Local LSB density", "lsbdensity")
        self.cb_transform.currentIndexChanged.connect(lambda _: self.refresh_view())
        controls.addWidget(QLabel("Transform"))
        controls.addWidget(self.cb_transform)

        self.cb_channel = QComboBox()
        self.cb_channel.currentIndexChanged.connect(lambda _: self.refresh_view())
        controls.addWidget(QLabel("Carrier"))
        controls.addWidget(self.cb_channel)

        self.cb_bit = QComboBox()
        self.cb_bit.currentIndexChanged.connect(lambda _: self.refresh_view())
        controls.addWidget(QLabel("Bit"))
        controls.addWidget(self.cb_bit)

        self.cb_mode = QComboBox()
        for label, code in (
            ("Bit = 1 white", "plane"),
            ("Bit = 1 black", "plane_inv"),
            ("Transitions", "edge"),
            ("Clear bit plane", "keep0"),
            ("Set bit plane", "keep1"),
            ("Original", "orig"),
        ):
            self.cb_mode.addItem(label, code)
        self.cb_mode.currentIndexChanged.connect(lambda _: self.refresh_view())
        controls.addWidget(QLabel("Mode"))
        controls.addWidget(self.cb_mode)

        self.cb_diff = QComboBox()
        for key in DIFF_KEYS:
            self.cb_diff.addItem(key, key)
        self.cb_diff.currentIndexChanged.connect(lambda _: self.refresh_view())
        controls.addWidget(self.cb_diff)

        self.chk_invert = QCheckBox("Invert")
        self.chk_invert.stateChanged.connect(lambda _: self.refresh_view())
        controls.addWidget(self.chk_invert)
        controls.addStretch(1)

        self.btn_fit = QPushButton("Fit")
        self.btn_fit.clicked.connect(lambda: self.canvas.fit())
        self.btn_save = QPushButton("Save view")
        self.btn_save.clicked.connect(app.save_current_view)
        controls.addWidget(self.btn_fit)
        controls.addWidget(self.btn_save)
        layout.addLayout(controls)

        self.canvas = ImageView()
        self.canvas.set_tile_mode(False)
        self.canvas.pixelHover.connect(self._on_hover)
        layout.addWidget(self.canvas, 1)

        self.lbl_pixel = QLabel("")
        self.lbl_pixel.setFont(mono_font(9))
        layout.addWidget(self.lbl_pixel)
        self._current: np.ndarray | None = None

    def on_document_changed(self):
        self.doc = self.app.doc
        if self.doc is None:
            return
        doc = self.doc
        self.cb_channel.blockSignals(True)
        self.cb_channel.clear()
        for name in doc.available_channels():
            self.cb_channel.addItem(name, name)
        if doc.is_color:
            self.cb_channel.addItem("RGB (three planes)", "RGB")
        if doc.is_color:
            self.cb_channel.setCurrentIndex(self.cb_channel.findData("B"))
        self.cb_channel.blockSignals(False)

        self.cb_bit.blockSignals(True)
        self.cb_bit.clear()
        for bit in range(doc.bits):
            suffix = " (LSB)" if bit == 0 else (" (MSB)" if bit == doc.bits - 1 else "")
            self.cb_bit.addItem(f"{bit}{suffix}", bit)
        self.cb_bit.setCurrentIndex(0)
        self.cb_bit.blockSignals(False)
        self.cb_diff.setVisible(doc.is_color)
        self.refresh_view()

    def set_selection(self, carrier: str, bit: int, mode: str = "plane"):
        index = self.cb_channel.findData(carrier)
        if index >= 0:
            self.cb_channel.setCurrentIndex(index)
        index = self.cb_bit.findData(int(bit))
        if index >= 0:
            self.cb_bit.setCurrentIndex(index)
        index = self.cb_mode.findData(mode)
        if index >= 0:
            self.cb_mode.setCurrentIndex(index)
        self.cb_transform.setCurrentIndex(0)
        self.refresh_view()

    def on_analysis_ready(self, kind: str):
        if kind in ("metadata", "steganalysis"):
            self.refresh_view()

    def refresh_view(self):
        doc = self.doc
        if doc is None:
            return
        transform = self.cb_transform.currentData()
        carrier = self.cb_channel.currentData() or "B"
        bit = self.cb_bit.currentData()
        bit = 0 if bit is None else int(bit)
        mode = self.cb_mode.currentData()
        invert = self.chk_invert.isChecked()
        image = None
        note = ""

        try:
            if transform == "plane":
                image = compute_view(doc, carrier, bit, mode, invert)
                note = f"carrier {carrier}, bit {bit}, mode {mode}"
            elif transform == "difference":
                key = self.cb_diff.currentData()
                image = doc.derived_image(key)
                if invert:
                    image = 255 - image
                note = f"derived carrier {key}"
            else:
                module = self.app.steg_module()
                if module is None:
                    note = self.app.steg_module_error()
                    image = compute_view(doc, carrier, bit, "plane", invert)
                elif transform == "ela":
                    image = module.ela_image(doc.path)
                    note = "error level analysis, JPEG recompression quality 90"
                elif transform == "residual":
                    image = module.noise_residual(doc.array, doc.index_of(carrier) if carrier in doc.channels else None)
                    note = "median residual of " + carrier
                elif transform == "autocorr":
                    image = module.autocorrelation_map(doc.array, doc.index_of(carrier))
                    note = "adjacent pixels with equal LSB"
                elif transform == "lsbdensity":
                    density = module.lsb_density_map(doc.array, doc.index_of(carrier))
                    image = (np.clip(density, 0, 1) * 255).astype(np.uint8)
                    note = "local LSB density per 16x16 block"
        except Exception as exc:  # keep the interface usable on any failure
            note = f"transform failed: {exc}"
            image = compute_view(doc, carrier, bit, "plane", invert)

        if image is None:
            return
        self._current = image
        self.canvas.set_image(image, smooth=transform in ("ela",))
        if not self.canvas.has_image():
            return
        self.canvas.fit()
        self.lbl_pixel.setText(f"view: {note}   |   {image.shape[1]} x {image.shape[0]}")

    def _on_hover(self, x: int, y: int):
        if self.doc is None or self._current is None:
            return
        arr = self._current
        if 0 <= y < arr.shape[0] and 0 <= x < arr.shape[1]:
            value = arr[y, x]
            text = " ".join(str(int(v)) for v in np.atleast_1d(value))
            self.lbl_pixel.setText(f"x={x:5d}  y={y:5d}   value = {text}")

    def current_image(self) -> np.ndarray | None:
        return self._current


# ---------------------------------------------------------------------------
class MetadataPage(Page):
    KEY = "metadata"
    TITLE = "Metadata"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_run = QPushButton("Extract metadata")
        self.btn_run.setProperty("accent", True)
        self.btn_run.clicked.connect(lambda: app.run_analysis("metadata"))
        self.btn_text = QPushButton("Save as text")
        self.btn_text.clicked.connect(lambda: app.save_report_part("metadata", "txt"))
        self.btn_json = QPushButton("Save as JSON")
        self.btn_json.clicked.connect(lambda: app.save_report_part("metadata", "json"))
        self.lbl = QLabel("No metadata extracted yet.")
        self.lbl.setStyleSheet(f"color: {FG_DIM.name()};")
        bar.addWidget(self.btn_run)
        bar.addWidget(self.btn_text)
        bar.addWidget(self.btn_json)
        bar.addWidget(self.lbl, 1)
        layout.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["Group", "Tag", "Value"], stretch_columns=[2])
        self.table.setFont(mono_font(9))
        self.findings = FindingList()
        split.addWidget(self.table)
        split.addWidget(self.findings)
        split.setSizes([560, 220])
        layout.addWidget(split, 1)

    def on_analysis_ready(self, kind: str):
        if kind != "metadata":
            return
        report = self.app.metadata_report
        if report is None:
            return
        self.table.setRowCount(0)
        for field in report.fields:
            row = self.table.rowCount()
            self.table.insertRow(row)
            items = [field.group, field.tag, field.value]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                if field.flag == "suspicious" and col == 2:
                    item.setForeground(WARN)
                if field.flag == "binary" and col == 2:
                    item.setForeground(FG_DIM)
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        findings = list(report.findings) + [f"warning: {w}" for w in report.warnings]
        self.findings.set_findings(findings, "No notable metadata findings.")
        self.lbl.setText(
            f"{len(report.fields)} fields, {len(report.findings)} findings, "
            f"format {report.file_format}, entropy {report.entropy:.4f}"
        )


# ---------------------------------------------------------------------------
class StructurePage(Page):
    KEY = "structure"
    TITLE = "Structure"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_run = QPushButton("Parse container")
        self.btn_run.setProperty("accent", True)
        self.btn_run.clicked.connect(lambda: app.run_analysis("structure"))
        self.btn_embedded = QPushButton("Carve first embedded object")
        self.btn_embedded.clicked.connect(app.carve_embedded)
        self.btn_json = QPushButton("Save as JSON")
        self.btn_json.clicked.connect(lambda: app.save_report_part("structure", "json"))
        self.lbl = QLabel("Container not parsed yet.")
        self.lbl.setStyleSheet(f"color: {FG_DIM.name()};")
        for widget in (self.btn_run, self.btn_embedded, self.btn_json, self.lbl):
            bar.addWidget(widget)
        bar.addStretch(1)
        layout.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(
            ["Offset", "Length", "Type", "Description"], stretch_columns=[3]
        )
        self.table.setFont(mono_font(9))
        self.embedded = make_table(
            ["Offset", "Length", "Kind", "Note", "Preview"], stretch_columns=[4]
        )
        self.embedded.setFont(mono_font(9))
        self.hex = HexView()
        self.findings = FindingList()

        top = QSplitter(Qt.Vertical)
        top.addWidget(self.table)
        top.addWidget(self.embedded)
        top.setSizes([320, 160])
        bottom = QSplitter(Qt.Vertical)
        bottom.addWidget(self.hex)
        bottom.addWidget(self.findings)
        bottom.setSizes([260, 140])
        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([420, 420])
        layout.addWidget(split, 1)

    def on_document_changed(self):
        self.doc = self.app.doc
        if self.app.path:
            try:
                with open(self.app.path, "rb") as handle:
                    self.hex.set_data(handle.read())
            except OSError:
                self.hex.set_data(b"")

    def on_analysis_ready(self, kind: str):
        if kind != "structure":
            return
        report = self.app.structure_report
        if report is None:
            return
        self.table.setRowCount(0)
        for chunk in report.chunks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                f"0x{chunk.offset:08X}",
                f"{chunk.length}",
                chunk.ctype,
                chunk.detail or chunk.name,
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.UserRole + 1, chunk.offset)
                if chunk.flag == "suspicious" and col == 2:
                    item.setForeground(WARN)
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(2)

        self.embedded.setRowCount(0)
        for item_data in report.embedded:
            row = self.embedded.rowCount()
            self.embedded.insertRow(row)
            values = [
                f"0x{item_data.offset:08X}",
                str(item_data.length),
                item_data.kind,
                item_data.note,
                item_data.preview,
            ]
            for col, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if col == 0:
                    cell.setData(Qt.UserRole + 1, item_data.offset)
                cell.setToolTip(text)
                self.embedded.setItem(row, col, cell)
        self.embedded.resizeColumnToContents(0)
        self.embedded.resizeColumnToContents(2)

        findings = list(report.findings) + [f"warning: {w}" for w in report.warnings]
        self.findings.set_findings(findings, "No notable structural findings.")
        self.lbl.setText(
            f"{len(report.chunks)} segments, {len(report.embedded)} embedded objects, "
            f"trailing {report.trailing_length} bytes"
        )


# ---------------------------------------------------------------------------
class StringsPage(Page):
    KEY = "strings"
    TITLE = "Strings"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self._settings = getattr(app, "settings", None)
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("Min length"))
        self.sp_min = QSpinBox()
        self.sp_min.setRange(2, 64)
        self.sp_min.setValue(self._setting("strings_min_len", 6))
        self.sp_min.valueChanged.connect(self._on_min_changed)
        bar.addWidget(self.sp_min)
        bar.addWidget(QLabel("Encoding"))
        self.cb_enc = QComboBox()
        self.cb_enc.addItem("ASCII", "ascii")
        self.cb_enc.addItem("UTF-16LE", "utf16le")
        self.cb_enc.addItem("Both", "both")
        index = self.cb_enc.findData(self._setting("strings_encoding", "ascii"))
        self.cb_enc.setCurrentIndex(index if index >= 0 else 0)
        self.cb_enc.currentIndexChanged.connect(self._on_enc_changed)
        bar.addWidget(self.cb_enc)
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("filter substrings (flag, key, http, password...)")
        self.edit_filter.textChanged.connect(self.apply_filter)
        bar.addWidget(self.edit_filter, 1)
        self.chk_flag = QCheckBox("only 'flag'")
        self.chk_flag.toggled.connect(self.apply_filter)
        bar.addWidget(self.chk_flag)
        self.btn_pixels = QPushButton("Find in pixels")
        self.btn_pixels.setToolTip("Search the pixel carried bitstreams for the text in the filter box")
        self.btn_pixels.clicked.connect(self.find_in_pixels)
        bar.addWidget(self.btn_pixels)
        self.btn_ocr = QPushButton("Read visible text")
        self.btn_ocr.setToolTip("Run OCR on the picture to read text that is printed in it")
        self.btn_ocr.clicked.connect(self.read_visible_text)
        bar.addWidget(self.btn_ocr)
        self.btn_save = QPushButton("Save strings")
        self.btn_save.clicked.connect(self.save_strings)
        bar.addWidget(self.btn_save)
        layout.addLayout(bar)

        self.lbl = QLabel("Load an image to list its strings.")
        self.lbl.setStyleSheet(f"color: {FG_DIM.name()};")
        layout.addWidget(self.lbl)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["Offset", "Encoding", "String"], stretch_columns=[2])
        self.table.setFont(mono_font(9))
        self.table.doubleClicked.connect(self._use_as_filter)
        split.addWidget(self.table)

        wrap = QWidget()
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.addWidget(QLabel("Pixel text search"))
        self.pixels = make_table(
            ["Carrier", "Bit", "Width", "Offset", "Order", "Position", "Context"],
            stretch_columns=[6],
        )
        self.pixels.setFont(mono_font(9))
        self.pixels.doubleClicked.connect(self._open_pixel_hit)
        wrap_layout.addWidget(self.pixels)
        split.addWidget(wrap)
        split.setSizes([520, 260])
        layout.addWidget(split, 1)

        self.raw = b""
        self.all_strings: list[tuple[int, str, str]] = []
        self._limit = int(self._setting("strings_limit", 5000))
        self._needle = ""

    # -- settings -----------------------------------------------------------
    def _setting(self, key: str, default):
        if self._settings is None:
            return default
        return self._settings.get(key)

    def _remember(self, key: str, value) -> None:
        if self._settings is None:
            return
        self._settings.set(key, value)
        try:
            self._settings.save()
        except OSError:
            pass

    def _on_min_changed(self, value: int):
        self._remember("strings_min_len", int(value))
        self.reload()

    def _on_enc_changed(self, _index: int):
        self._remember("strings_encoding", self.cb_enc.currentData())
        self.reload()

    def apply_settings(self):
        """Re-read the settings after the preferences dialog was used."""
        if self._settings is None:
            return
        self.sp_min.blockSignals(True)
        self.sp_min.setValue(int(self._setting("strings_min_len", 6)))
        self.sp_min.blockSignals(False)
        index = self.cb_enc.findData(self._setting("strings_encoding", "ascii"))
        self.cb_enc.blockSignals(True)
        self.cb_enc.setCurrentIndex(index if index >= 0 else 0)
        self.cb_enc.blockSignals(False)
        self._limit = int(self._setting("strings_limit", 5000))
        self.reload()

    def on_document_changed(self):
        self.doc = self.app.doc
        self.raw = self.app._raw_bytes() or b""
        self.pixels.setRowCount(0)
        self.reload()

    def reload(self):
        if not self.raw:
            self.all_strings = []
        else:
            from ..structure import collect_strings

            self.all_strings = collect_strings(
                self.raw, int(self.sp_min.value()), int(self._limit), self.cb_enc.currentData()
            )
        self.apply_filter()

    def apply_filter(self):
        needle = self.edit_filter.text().strip().lower()
        only_flag = self.chk_flag.isChecked()
        self.table.setRowCount(0)
        shown = 0
        for offset, text, enc in self.all_strings:
            if only_flag and "flag" not in text.lower():
                continue
            if needle and needle not in text.lower():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(f"0x{offset:08X}")
            item.setData(Qt.UserRole + 1, offset)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(enc))
            self.table.setItem(row, 2, QTableWidgetItem(text))
            shown += 1
            if shown >= self._limit:
                break
        self.table.resizeColumnToContents(0)
        self.lbl.setText(
            f"{shown} string(s) shown of {len(self.all_strings)} extracted from "
            f"{len(self.raw):,} bytes"
        )

    def _use_as_filter(self, index):
        item = self.table.item(index.row(), 2)
        if item is not None:
            self.edit_filter.setText(item.text())

    def _open_pixel_hit(self, index):
        item = self.pixels.item(index.row(), 0)
        if item is None:
            return
        hit = item.data(Qt.UserRole)
        if isinstance(hit, dict):
            self.app.open_candidate(hit)

    # -- pixel text search --------------------------------------------------
    def find_in_pixels(self):
        if self.doc is None:
            QMessageBox.information(self, "Pixel text search", "Load an image first.")
            return
        needle = self.edit_filter.text().strip()
        if not needle:
            QMessageBox.information(
                self, "Pixel text search",
                "Type the text to look for in the filter box first, then run the search.",
            )
            return
        self._needle = needle
        doc = self.doc
        offsets = range(max(1, int(self._setting("pixel_offsets", 8))))
        widths = self._pixel_widths()
        self.lbl.setText(f"searching the pixels for '{needle}'...")
        self.app.run_task(
            f"pixel text search: {needle}",
            lambda: search_pixels(
                doc, needle,
                offsets=offsets,
                widths=widths,
                max_bytes=int(self._setting("pixel_max_bytes", 200000)),
            ),
            self._on_pixel_hits,
        )

    def _pixel_widths(self):
        if self._settings is not None and hasattr(self._settings, "pixel_widths_tuple"):
            return self._settings.pixel_widths_tuple()
        return (1, 2, 4)

    def _on_pixel_hits(self, hits):
        hits = hits or []
        self.pixels.setRowCount(0)
        for hit in hits:
            row = self.pixels.rowCount()
            self.pixels.insertRow(row)
            values = [
                hit.get("label", ""),
                hit.get("bit", 0),
                hit.get("width", 1),
                hit.get("offset", 0),
                "MSB" if hit.get("msb") else "LSB",
                hit.get("pos", 0),
                hit.get("snippet", ""),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value))
                if col == 0:
                    cell.setData(Qt.UserRole, hit)
                self.pixels.setItem(row, col, cell)
        self.pixels.resizeColumnToContents(0)
        if hits:
            self.lbl.setText(
                f"pixel text search: '{self._needle}' reproduced in {len(hits)} "
                f"carrier configuration(s) - double-click one to extract it"
            )
        else:
            self.lbl.setText(f"pixel text search: '{self._needle}' was not found in the pixels")

    # -- visible text (OCR) --------------------------------------------------
    def read_visible_text(self):
        """Read the text that is printed inside the picture, using OCR."""
        if self.doc is None:
            QMessageBox.information(self, "Read visible text", "Load an image first.")
            return
        if not self._setting("ocr_enabled", True):
            QMessageBox.information(
                self, "Read visible text",
                "Reading visible text is switched off in Preferences > OCR.",
            )
            return
        try:
            from .. import ocr as ocr_mod
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(self, "Read visible text", str(exc))
            return
        engine = str(self._setting("tesseract", "") or "").strip()
        status = ocr_mod.probe(engine)
        if not status.available:
            QMessageBox.warning(
                self, "Read visible text",
                "No usable OCR engine was found:\n"
                f"{status.reason}\n\n"
                "Install the 'pytesseract' package together with a Tesseract build, "
                "then point Preferences > OCR at the engine.",
            )
            return
        image = self._ocr_image()
        language = str(self._setting("ocr_language", "eng") or "eng")
        self.lbl.setText(f"reading visible text with Tesseract {status.version}...")
        self.app.run_task(
            f"reading visible text ({language})",
            lambda: ocr_mod.recognize(image, language, engine),
            self._on_ocr_done,
        )

    def _ocr_image(self):
        """The picture handed to OCR, scaled down when it is very large."""
        from PIL import Image

        source = getattr(self.doc, "pil", None)
        if source is None:
            source = Image.fromarray(self.doc.array)
        else:
            source = source.convert("RGB")
        longest = max(source.size)
        if longest > 4000:
            factor = 4000.0 / longest
            resample = getattr(Image, "Resampling", Image).LANCZOS
            source = source.resize(
                (max(1, int(source.width * factor)), max(1, int(source.height * factor))),
                resample,
            )
        return source

    def _on_ocr_done(self, text):
        text = text or ""
        stripped = text.strip()
        if not stripped:
            self.lbl.setText("OCR finished: no visible text was recognised in this image.")
            return
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.lbl.setText(
            f"OCR finished: {len(lines)} line(s) of visible text, {len(stripped)} characters"
        )
        TextDialog(
            "Visible text (OCR)", text, parent=self, raw=text.encode("utf-8")
        ).exec()

    def save_strings(self):
        if not self.all_strings:
            QMessageBox.information(self, "Save strings", "There are no strings to save.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save strings", "strings.txt", "Text (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8", errors="replace") as handle:
            for offset, text, enc in self.all_strings:
                handle.write(f"{offset:08X}  {enc:<8}  {text}\n")
        self.lbl.setText(f"saved {len(self.all_strings)} string(s) to {path}")


# ---------------------------------------------------------------------------
class HexPage(Page):
    KEY = "hex"
    TITLE = "Hex"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.lbl = QLabel("No file loaded.")
        self.lbl.setFont(mono_font(9))
        self.btn_reload = QPushButton("Reload from disk")
        self.btn_reload.clicked.connect(self.reload)
        bar.addWidget(self.lbl, 1)
        bar.addWidget(self.btn_reload)
        layout.addLayout(bar)

        self._settings = getattr(app, "settings", None)
        self.hex = HexEditor()
        # Any edit invalidates the cached raw bytes the other pages read.
        self.hex.modifiedChanged.connect(lambda _m: setattr(self.app, "_file_bytes", None))
        layout.addWidget(self.hex, 1)
        self.apply_settings()

    def apply_settings(self):
        """Page length in rows follows the Display/Hex preferences."""
        if self._settings is None:
            return
        self.hex.set_rows(int(self._settings.get("hex_rows", 256)))

    def on_document_changed(self):
        self.doc = self.app.doc
        self.reload()

    def reload(self):
        if not self.app.path:
            self.hex.set_data(b"")
            self.lbl.setText("No file loaded.")
            return
        try:
            with open(self.app.path, "rb") as handle:
                blob = handle.read()
        except OSError as exc:
            QMessageBox.critical(self, "Hex", str(exc))
            return
        self.hex.set_data(blob, self.app.path)
        name = os.path.basename(self.app.path)
        self.lbl.setText(
            f"{name} - {len(blob):,} bytes - edit the hex column, then Save or Save as"
        )


# ---------------------------------------------------------------------------
class HistogramWidget(QWidget):
    """Compact 256-bin histogram painter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts = np.zeros(256, dtype=np.int64)
        self._title = ""
        self.setMinimumHeight(120)

    def set_counts(self, counts, title: str = ""):
        self._counts = np.asarray(counts, dtype=np.int64)
        self._title = title
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 26, 30))
        painter.setPen(QPen(QColor(56, 59, 66)))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if self._counts.size == 0 or self._counts.max() <= 0:
            return
        width = self.width()
        height = self.height()
        peak = int(self._counts.max())
        bar_w = max(1.0, width / 256.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(ACCENT)
        for i, value in enumerate(self._counts):
            if value <= 0:
                continue
            bar_h = (value / peak) * (height - 18)
            x = i * bar_w
            painter.drawRect(
                int(x), int(height - 4 - bar_h), max(1, int(bar_w)), int(bar_h)
            )
        painter.setPen(QColor(150, 154, 162))
        if self._title:
            painter.drawText(6, 14, self._title)


class SteganalysisPage(Page):
    KEY = "steganalysis"
    TITLE = "Steganalysis"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_run = QPushButton("Run steganalysis")
        self.btn_run.setProperty("accent", True)
        self.btn_run.clicked.connect(lambda: app.run_analysis("steganalysis"))
        self.btn_palette = QPushButton("Palette analysis")
        self.btn_palette.clicked.connect(app.run_palette_analysis)
        self.btn_dct = QPushButton("JPEG DCT analysis")
        self.btn_dct.clicked.connect(lambda: app.run_analysis("dct"))
        self.btn_json = QPushButton("Save as JSON")
        self.btn_json.clicked.connect(lambda: app.save_report_part("steganalysis", "json"))
        self.lbl = QLabel("Not run yet.")
        self.lbl.setStyleSheet(f"color: {FG_DIM.name()};")
        for widget in (self.btn_run, self.btn_palette, self.btn_dct, self.btn_json, self.lbl):
            bar.addWidget(widget)
        bar.addStretch(1)
        layout.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(
            ["Channel", "Entropy", "LSB ones", "Chi-square p", "RS rate", "SPA rate", "Verdict"],
            stretch_columns=[6],
        )
        self.table.setFont(mono_font(9))
        self.hist = HistogramWidget()
        self.findings = FindingList()
        lower = QSplitter(Qt.Horizontal)
        lower.addWidget(self.hist)
        lower.addWidget(self.findings)
        lower.setSizes([420, 520])
        split.addWidget(self.table)
        split.addWidget(lower)
        split.setSizes([300, 360])
        layout.addWidget(split, 1)

    def on_analysis_ready(self, kind: str):
        if kind == "steganalysis":
            self._show_summary()
        elif kind == "dct":
            self.lbl.setText("JPEG domain analysis complete, see findings.")

    def _show_summary(self):
        summary = self.app.steg_summary
        if summary is None:
            return
        self.table.setRowCount(0)
        for name, data in summary.get("channels", {}).items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            verdict = data.get("verdict", "")
            values = [
                (name, None),
                (f"{data.get('entropy', 0):.4f}", None),
                (f"{data.get('lsb_ones_ratio', 0):.4f}", data.get("lsb_ones_ratio")),
                (f"{data.get('chi_square', {}).get('p', 0):.4f}", data.get("chi_square", {}).get("p")),
                (f"{data.get('rs', {}).get('estimated_rate', 0):.4f}", data.get("rs", {}).get("estimated_rate")),
                (f"{data.get('spa', {}).get('rate', 0):.4f}", data.get("spa", {}).get("rate")),
                (verdict, None),
            ]
            for col, (text, sort_value) in enumerate(values):
                item = NumericItem(text) if sort_value is not None else QTableWidgetItem(text)
                if sort_value is not None:
                    item.setData(Qt.UserRole + 1, sort_value)
                if col == 6:
                    lowered = verdict.lower()
                    if "strong" in lowered:
                        item.setForeground(DANGER)
                    elif "possible" in lowered:
                        item.setForeground(WARN)
                    else:
                        item.setForeground(OK)
                self.table.setItem(row, col, item)
        self.table.resizeColumnToContents(0)
        self.findings.set_findings(summary.get("findings", []), "No statistical anomaly detected.")
        self.lbl.setText("verdict: " + summary.get("verdict", "unknown"))
        module = self.app.steg_module()
        if module is not None and self.doc is not None:
            try:
                counts = module.channel_histogram(self.doc.array, 0 if self.doc.is_color else None)
                self.hist.set_counts(counts, "channel histogram")
            except Exception:
                self.hist.set_counts(np.zeros(256, dtype=np.int64))


# ---------------------------------------------------------------------------
class PayloadsPage(Page):
    KEY = "payloads"
    TITLE = "Payloads"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)

        scan_row = QHBoxLayout()
        scan_row.addWidget(QLabel("Depth"))
        self._settings = getattr(app, "settings", None)
        self.cb_depth = QComboBox()
        self.cb_depth.addItem("Quick (b1,b2 - 4 offsets - 50 KB)", "quick")
        self.cb_depth.addItem("Standard (b1,b2,b4 - 8 offsets - 200 KB)", "standard")
        self.cb_depth.addItem("Deep (b1,b2,b4 - 8 offsets - 1 MB)", "deep")
        index = self.cb_depth.findData(self._setting("scan_depth", "standard"))
        self.cb_depth.setCurrentIndex(index if index >= 0 else 1)
        self.cb_depth.currentIndexChanged.connect(self._on_depth_changed)
        scan_row.addWidget(self.cb_depth)
        self.btn_start = QPushButton("Start sweep")
        self.btn_start.setProperty("accent", True)
        self.btn_start.clicked.connect(app.start_scan)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(app.stop_scan)
        scan_row.addWidget(self.btn_start)
        scan_row.addWidget(self.btn_stop)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(220)
        scan_row.addWidget(self.progress)
        scan_row.addStretch(1)
        layout.addLayout(scan_row)

        out_row = QHBoxLayout()
        self.btn_extract_all = QPushButton("Extract all payloads")
        self.btn_extract_all.setProperty("accent", True)
        self.btn_extract_all.setToolTip("Decode every candidate in full and save the bytes to a folder")
        self.btn_extract_all.clicked.connect(lambda: app.extract_all_payloads(False))
        self.btn_extract_sel = QPushButton("Extract selected")
        self.btn_extract_sel.setToolTip("Decode the selected rows and save their payloads")
        self.btn_extract_sel.clicked.connect(lambda: app.extract_all_payloads(True))
        self.btn_csv = QPushButton("Export results (CSV)")
        self.btn_csv.clicked.connect(app.export_scan_csv)
        self.btn_json = QPushButton("Export results (JSON)")
        self.btn_json.clicked.connect(app.export_scan_json)
        out_row.addWidget(self.btn_extract_all)
        out_row.addWidget(self.btn_extract_sel)
        out_row.addWidget(self.btn_csv)
        out_row.addWidget(self.btn_json)
        out_row.addStretch(1)
        layout.addLayout(out_row)

        self.lbl = QLabel("No sweep has been run.")
        self.lbl.setStyleSheet(f"color: {FG_DIM.name()};")
        layout.addWidget(self.lbl)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Keyword flag hits"))
        self.flags = QListWidget()
        self.flags.setFont(mono_font(9))
        self.flags.itemDoubleClicked.connect(self._open_flag)
        left_layout.addWidget(self.flags)
        left.setMaximumWidth(420)
        split.addWidget(left)

        self.table = make_table(
            ["Method", "Carrier", "Bit", "Width", "Offset", "Order", "Type", "Score",
             "Entropy", "Printable", "Preview"],
            stretch_columns=[10],
        )
        self.table.setFont(mono_font(9))
        self.table.doubleClicked.connect(lambda idx: self._open_row(idx.row()))
        split.addWidget(self.table)
        split.setSizes([400, 880])
        layout.addWidget(split, 1)

    def _setting(self, key: str, default):
        if self._settings is None:
            return default
        return self._settings.get(key)

    def _on_depth_changed(self, _index: int):
        if self._settings is None:
            return
        self._settings.set("scan_depth", self.cb_depth.currentData())
        try:
            self._settings.save()
        except OSError:
            pass

    def apply_settings(self):
        """Adopt the configured default sweep depth."""
        if self._settings is None:
            return
        index = self.cb_depth.findData(self._setting("scan_depth", "standard"))
        self.cb_depth.blockSignals(True)
        self.cb_depth.setCurrentIndex(index if index >= 0 else 1)
        self.cb_depth.blockSignals(False)

    def on_document_changed(self):
        self.doc = self.app.doc
        self.table.setRowCount(0)
        self.flags.clear()
        self.lbl.setText("No sweep has been run for this image.")
        self.progress.setValue(0)

    def set_progress(self, value: int, message: str = ""):
        self.progress.setValue(int(value))
        if message:
            self.lbl.setText(message)

    def set_status(self, text: str):
        self.lbl.setText(text)

    def show_results(self, hits: list[dict]):
        self.table.setRowCount(0)
        self.flags.clear()
        for index, hit in enumerate(hits):
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                hit.get("method", ""),
                hit.get("label", ""),
                hit.get("bit", 0),
                hit.get("width", 1),
                hit.get("offset", 0),
                "MSB first" if hit.get("msb") else "LSB first",
                hit.get("kind", ""),
                hit.get("score", 0.0),
                hit.get("entropy", 0.0),
                hit.get("printable", 0.0),
                hit.get("snippet", "")[:120],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value) if col in (0, 1, 5, 6) else NumericItem(str(value))
                if col in (2, 3, 4):
                    item.setData(Qt.UserRole + 1, int(value))
                elif col in (7, 8, 9):
                    item.setData(Qt.UserRole + 1, float(value))
                if col == 0:
                    item.setData(Qt.UserRole, hit)
                if col == 6:
                    kind = str(value)
                    if kind == "FLAG":
                        item.setForeground(OK)
                    elif kind == "pattern":
                        item.setForeground(WARN)
                    else:
                        item.setForeground(FG_DIM)
                item.setToolTip(str(item.text()))
                self.table.setItem(row, col, item)
            if hit.get("kind") == "FLAG":
                entry = QListWidgetItem(f"{hit.get('match', '')}   [{hit.get('label', '')} @{hit.get('offset', 0)}]")
                entry.setData(Qt.UserRole, hit)
                entry.setForeground(OK)
                self.flags.addItem(entry)
        for col in (1, 10):
            self.table.resizeColumnToContents(col)

    def _open_flag(self, item: QListWidgetItem):
        hit = item.data(Qt.UserRole)
        if isinstance(hit, dict):
            self.app.open_candidate(hit)

    def _open_row(self, row: int):
        item = self.table.item(row, 0)
        if item is None:
            return
        hit = item.data(Qt.UserRole)
        if isinstance(hit, dict):
            self.app.open_candidate(hit)

    def selected_hits(self) -> list[dict]:
        """Hits for the currently selected rows, in table order, without duplicates."""
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        hits = []
        for row in rows:
            item = self.table.item(row, 0)
            if item is None:
                continue
            hit = item.data(Qt.UserRole)
            if isinstance(hit, dict):
                hits.append(hit)
        return hits


# ---------------------------------------------------------------------------
class ExtractPage(Page):
    KEY = "extract"
    TITLE = "Extract"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        self.cb_channel = QComboBox()
        self.sp_bit = QSpinBox()
        self.sp_bit.setRange(0, 7)
        self.cb_width = QComboBox()
        for width in (1, 2, 4, 8):
            self.cb_width.addItem(f"b{width}", width)
        self.cb_order = QComboBox()
        self.cb_order.addItem("MSB first", True)
        self.cb_order.addItem("LSB first", False)
        self.sp_offset = QSpinBox()
        self.sp_offset.setRange(0, 64)
        self.sp_max = QSpinBox()
        self.sp_max.setRange(0, 20_000_000)
        self.sp_max.setValue(4096)
        self.chk_all = QCheckBox("All bits per sample")
        self.btn_run = QPushButton("Extract")
        self.btn_run.setProperty("accent", True)
        self.btn_run.clicked.connect(self.run_extract)
        self.btn_save = QPushButton("Save bytes")
        self.btn_save.clicked.connect(self.save_bytes)

        for label, widget in (
            ("Carrier", self.cb_channel), ("Bit", self.sp_bit), ("Width", self.cb_width),
            ("Order", self.cb_order), ("Offset", self.sp_offset), ("Max bytes", self.sp_max),
        ):
            form.addWidget(QLabel(label))
            form.addWidget(widget)
        form.addWidget(self.chk_all)
        form.addWidget(self.btn_run)
        form.addWidget(self.btn_save)
        form.addStretch(1)
        layout.addLayout(form)

        split = QSplitter(Qt.Horizontal)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font(9))
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.strings = QListWidget()
        self.strings.setFont(mono_font(9))
        self.strings.itemDoubleClicked.connect(self._show_string)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Extracted strings"))
        right_layout.addWidget(self.strings)
        split.addWidget(self.preview)
        split.addWidget(right)
        split.setSizes([760, 420])
        layout.addWidget(split, 1)

        self.lbl = QLabel("")
        self.lbl.setFont(mono_font(9))
        layout.addWidget(self.lbl)
        self._bytes = b""

    def on_document_changed(self):
        self.doc = self.app.doc
        if self.doc is None:
            return
        self.cb_channel.clear()
        for name in self.doc.available_channels():
            self.cb_channel.addItem(name, name)
        if self.doc.is_color:
            self.cb_channel.addItem("RGB (interleaved)", "RGB")
            self.cb_channel.setCurrentIndex(self.cb_channel.findData("B"))
        self.sp_bit.setRange(0, self.doc.bits - 1)
        self.preview.setPlainText("")
        self.strings.clear()
        self.lbl.setText("")

    def _carrier(self):
        return self.cb_channel.currentData()

    def run_extract(self):
        doc = self.doc
        if doc is None:
            QMessageBox.information(self, "Extract", "Load an image first.")
            return
        try:
            vals = extract_stream(
                doc,
                self._carrier(),
                self.sp_bit.value(),
                bool(self.cb_order.currentData()),
                self.sp_offset.value(),
                self.sp_max.value(),
                self.chk_all.isChecked(),
                int(self.cb_width.currentData()),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Extract", f"Extraction failed:\n{exc}")
            return
        self._bytes = vals.tobytes()
        text = as_text(vals)
        display = "".join(c if 32 <= ord(c) < 127 or c == "\n" else "." for c in text)
        self.preview.setPlainText(display)
        self.strings.clear()
        for offset, value in find_strings(vals, limit=400):
            entry = QListWidgetItem(f"0x{offset:06X}  {value}")
            entry.setData(Qt.UserRole, value)
            self.strings.addItem(entry)
        printable = sum(1 for c in text if 32 <= ord(c) < 127) / max(1, len(text))
        self.lbl.setText(
            f"{len(self._bytes)} bytes extracted from {self._carrier()} "
            f"bit {self.sp_bit.value()} width {self.cb_width.currentData()} "
            f"offset {self.sp_offset.value()} | printable {printable * 100:.1f}% | "
            f"{self.strings.count()} strings"
        )
        self.app.last_extraction = dict(
            carrier=self._carrier(), bit=self.sp_bit.value(), width=int(self.cb_width.currentData()),
            msb=bool(self.cb_order.currentData()), offset=self.sp_offset.value(),
            max_bytes=self.sp_max.value(), all_bits=self.chk_all.isChecked(),
        )

    def _show_string(self, item: QListWidgetItem):
        TextDialog("Extracted string", str(item.data(Qt.UserRole)), self).exec()

    def save_bytes(self):
        if not self._bytes:
            QMessageBox.information(self, "Save", "Extract something first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save extracted bytes", "payload.bin", "Binary (*.bin);;All files (*.*)"
        )
        if path:
            with open(path, "wb") as handle:
                handle.write(self._bytes)
            self.app.status(f"Saved {len(self._bytes)} bytes to {path}")


# ---------------------------------------------------------------------------
class EmbedPage(Page):
    KEY = "embed"
    TITLE = "Embed"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        self.cb_channel = QComboBox()
        self.sp_bit = QSpinBox()
        self.sp_bit.setRange(0, 7)
        self.cb_order = QComboBox()
        self.cb_order.addItem("MSB first", True)
        self.cb_order.addItem("LSB first", False)
        self.sp_offset = QSpinBox()
        self.sp_offset.setRange(0, 64)
        for label, widget in (
            ("Carrier", self.cb_channel), ("Bit", self.sp_bit), ("Order", self.cb_order),
            ("Offset", self.sp_offset),
        ):
            form.addWidget(QLabel(label))
            form.addWidget(widget)
        form.addStretch(1)
        layout.addLayout(form)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Payload text to embed, or load a file with the button below.")
        self.text.setFont(mono_font(9))
        layout.addWidget(self.text, 1)

        bar = QHBoxLayout()
        self.btn_load = QPushButton("Load payload from file")
        self.btn_load.clicked.connect(self.load_payload)
        self.btn_preview = QPushButton("Check capacity")
        self.btn_preview.clicked.connect(self.check_capacity)
        self.btn_embed = QPushButton("Embed and save PNG")
        self.btn_embed.setProperty("accent", True)
        self.btn_embed.clicked.connect(self.do_embed)
        bar.addWidget(self.btn_load)
        bar.addWidget(self.btn_preview)
        bar.addWidget(self.btn_embed)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.lbl = QLabel("")
        self.lbl.setFont(mono_font(9))
        layout.addWidget(self.lbl)
        self._payload: bytes = b""

    def on_document_changed(self):
        self.doc = self.app.doc
        if self.doc is None:
            return
        self.cb_channel.clear()
        for name in self.doc.available_channels():
            if name in ("Gray", "R", "G", "B", "A", "P"):
                self.cb_channel.addItem(name, name)
        if self.doc.is_color:
            self.cb_channel.setCurrentIndex(self.cb_channel.findData("B"))
        self.sp_bit.setRange(0, self.doc.bits - 1)

    def payload(self) -> bytes:
        if self._payload:
            return self._payload
        return self.text.toPlainText().encode("utf-8", errors="replace")

    def load_payload(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load payload", "", "All files (*.*)")
        if not path:
            return
        with open(path, "rb") as handle:
            self._payload = handle.read()
        self.lbl.setText(f"payload loaded: {len(self._payload)} bytes from {os.path.basename(path)}")

    def check_capacity(self):
        if self.doc is None:
            return
        capacity = self.doc.capacity_bytes(self.cb_channel.currentData(), 1)
        need = len(self.payload())
        state = "fits" if need <= capacity else "DOES NOT FIT"
        self.lbl.setText(f"payload {need} bytes, capacity {capacity} bytes at bit "
                         f"{self.sp_bit.value()} - {state}")

    def do_embed(self):
        doc = self.doc
        if doc is None:
            QMessageBox.information(self, "Embed", "Load an image first.")
            return
        payload = self.payload()
        if not payload:
            QMessageBox.information(self, "Embed", "Provide a payload first.")
            return
        carrier = self.cb_channel.currentData()
        capacity = doc.capacity_bytes(carrier, 1)
        if len(payload) > capacity:
            QMessageBox.warning(
                self, "Embed",
                f"Payload is {len(payload)} bytes but the carrier holds {capacity} bytes.",
            )
            return
        array = embed_bytes(
            doc, carrier, self.sp_bit.value(), payload,
            bool(self.cb_order.currentData()), self.sp_offset.value(),
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save stego image", "stego.png", "PNG (*.png);;All files (*.*)"
        )
        if not path:
            return
        from PIL import Image as PilImage

        PilImage.fromarray(array).save(path)
        self.lbl.setText(f"saved {len(payload)} bytes into {carrier} bit {self.sp_bit.value()} -> {path}")
        self.app.status(f"Stego image written to {path}")


# ---------------------------------------------------------------------------
class ReportPage(Page):
    KEY = "report"
    TITLE = "Report"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_build = QPushButton("Build report")
        self.btn_build.setProperty("accent", True)
        self.btn_build.clicked.connect(self.build)
        self.btn_txt = QPushButton("Save as text")
        self.btn_txt.clicked.connect(lambda: self.app.save_report_part("report", "txt"))
        self.btn_html = QPushButton("Save as HTML")
        self.btn_html.clicked.connect(lambda: self.app.save_report_part("report", "html"))
        self.btn_json = QPushButton("Save as JSON")
        self.btn_json.clicked.connect(lambda: self.app.save_report_part("report", "json"))
        self.btn_copy = QPushButton("Copy to clipboard")
        self.btn_copy.clicked.connect(self.copy)
        for widget in (self.btn_build, self.btn_txt, self.btn_html, self.btn_json, self.btn_copy):
            bar.addWidget(widget)
        bar.addStretch(1)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet(f"color: {FG_DIM.name()};")
        bar.addWidget(self.lbl)
        layout.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(mono_font(9))
        layout.addWidget(self.view, 1)

    def build(self):
        text = self.app.build_report("txt")
        self.view.setPlainText(text)
        self.lbl.setText(f"{len(text)} characters")

    def copy(self):
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.view.toPlainText())
        self.app.status("Report copied to clipboard.")
