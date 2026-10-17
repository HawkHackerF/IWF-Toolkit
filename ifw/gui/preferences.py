"""Preferences dialog: a sectioned editor for the user settings.

Laid out like the Adobe preference windows: a list of sections on the left and
the matching page on the right, with Restore defaults, Cancel and OK along the
bottom. The dialog only collects values; the caller decides whether to persist
them, so it stays free of policy.

    dialog = PreferencesDialog(settings, parent)
    if dialog.exec() is QDialog.DialogCode.Accepted:
        settings.update(dialog.collect())
        settings.save()
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import ocr as ocr_mod
from .theme import FG_DIM, mono_font, ui_font


class PreferencesDialog(QDialog):
    """Sectioned editor for the values in :class:`ifw.config.Settings`."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumSize(760, 560)
        self._settings = settings
        self._fields: dict[str, tuple[QWidget, str]] = {}
        self._choice: dict[str, QComboBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(14)

        self.sections = QListWidget()
        self.sections.setFixedWidth(190)
        self.sections.setFont(ui_font(10))
        body.addWidget(self.sections)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 0, 12, 12)
        self.btn_defaults = QPushButton("Restore defaults")
        self.btn_defaults.clicked.connect(self.restore_defaults)
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(f"color: {FG_DIM.name()};")
        self.lbl_status.setFont(mono_font(8))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(self.btn_defaults)
        footer.addWidget(self.lbl_status, 1)
        footer.addWidget(buttons)
        root.addLayout(footer)

        self._build_pages()
        self.sections.setCurrentRow(0)
        self.sections.currentRowChanged.connect(self.stack.setCurrentIndex)

    # -- construction -------------------------------------------------------
    def _build_pages(self) -> None:
        data = self._settings.data

        general = self._page("General")
        self._choice_field(general, "Scan depth", "scan_depth",
                           [("Quick", "quick"), ("Standard", "standard"), ("Deep", "deep")],
                           data.get("scan_depth", "standard"))
        self._int_field(general, "Pixel search offsets", "pixel_offsets",
                        data.get("pixel_offsets", 8), 1, 64)
        self._str_field(general, "Pixel search bit widths", "pixel_widths",
                        str(data.get("pixel_widths", "1,2,4")), "comma separated, e.g. 1,2,4")
        self._int_field(general, "Pixel search byte budget", "pixel_max_bytes",
                        data.get("pixel_max_bytes", 200000), 4096, 16_000_000, 1000)
        self._bool_field(general, "Confirm before extracting payloads", "extract_confirm",
                         bool(data.get("extract_confirm", True)))

        extraction = self._page("Extraction")
        row = QHBoxLayout()
        edit = QLineEdit(str(data.get("extract_dir", "")))
        edit.setPlaceholderText("leave empty to choose a folder each time")
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: self._browse_dir(edit))
        row.addWidget(edit, 1)
        row.addWidget(browse)
        self._register("extract_dir", edit, "str")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("Default output folder", row)
        extraction.layout().addLayout(form)
        self._int_field(extraction, "Maximum payload size (MB)", "extract_max_mb",
                        data.get("extract_max_mb", 64), 1, 1024)

        strings = self._page("Strings")
        self._int_field(strings, "Minimum string length", "strings_min_len",
                        data.get("strings_min_len", 6), 2, 64)
        self._choice_field(strings, "Encoding", "strings_encoding",
                           [("ASCII", "ascii"), ("UTF-16LE", "utf16le"), ("Both", "both")],
                           data.get("strings_encoding", "ascii"))
        self._int_field(strings, "Maximum strings listed", "strings_limit",
                        data.get("strings_limit", 5000), 50, 200000, 500)

        display = self._page("Display")
        self._choice_field(display, "Interface density", "theme_density",
                           [("Comfortable", "comfortable"), ("Compact", "compact")],
                           data.get("theme_density", "comfortable"))
        self._int_field(display, "Bit-plane montage tiles", "montage_max_tiles",
                        data.get("montage_max_tiles", 64), 8, 4096, 8)
        note = QLabel("Interface density applies the next time the workbench starts.")
        note.setStyleSheet(f"color: {FG_DIM.name()};")
        note.setWordWrap(True)
        display.layout().addWidget(note)

        hex_page = self._page("Hex")
        self._int_field(hex_page, "Rows per page", "hex_rows",
                        data.get("hex_rows", 256), 64, 1024, 64)
        note = QLabel("Bytes are always shown as 16 per row. A larger page shows more "
                      "of a large file at once.")
        note.setStyleSheet(f"color: {FG_DIM.name()};")
        note.setWordWrap(True)
        hex_page.layout().addWidget(note)

        ocr_page = self._page("OCR")
        self._bool_field(ocr_page, "Enable reading of visible text", "ocr_enabled",
                         bool(data.get("ocr_enabled", True)))
        self._str_field(ocr_page, "Tesseract language", "ocr_language",
                        str(data.get("ocr_language", "eng")), "for example eng, ind, eng+ind")
        row = QHBoxLayout()
        edit = QLineEdit(str(data.get("tesseract", "")))
        edit.setPlaceholderText("path to tesseract.exe (leave empty to use PATH)")
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: self._browse_file(edit))
        row.addWidget(edit, 1)
        row.addWidget(browse)
        self._register("tesseract", edit, "str")
        self.lbl_ocr = QLabel("")
        self.lbl_ocr.setWordWrap(True)
        self.lbl_ocr.setStyleSheet(f"color: {FG_DIM.name()};")
        self.lbl_ocr.setFont(mono_font(8))
        test = QPushButton("Test engine")
        test.clicked.connect(lambda: self._update_ocr_status(edit.text().strip()))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("Tesseract executable", row)
        form.addRow("", test)
        form.addRow("", self.lbl_ocr)
        ocr_page.layout().addLayout(form)
        self._update_ocr_status(str(data.get("tesseract", "")).strip())

    def _page(self, title: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        heading = QLabel(title.upper())
        heading.setStyleSheet("font-weight: 600;")
        heading.setFont(ui_font(10))
        layout.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignRight)
        layout.addLayout(form)
        layout.addStretch(1)
        self.stack.addWidget(page)
        self.sections.addItem(title)
        page.form = form
        return page

    # -- field helpers ------------------------------------------------------
    def _register(self, key: str, widget: QWidget, kind: str) -> None:
        self._fields[key] = (widget, kind)

    def _str_field(self, page, label, key, value, placeholder=""):
        edit = QLineEdit(str(value))
        if placeholder:
            edit.setPlaceholderText(placeholder)
        page.form.addRow(label, edit)
        self._register(key, edit, "str")

    def _int_field(self, page, label, key, value, low, high, step=1):
        spin = QSpinBox()
        spin.setRange(int(low), int(high))
        spin.setSingleStep(int(step))
        spin.setValue(int(value))
        page.form.addRow(label, spin)
        self._register(key, spin, "int")

    def _bool_field(self, page, label, key, value):
        box = QCheckBox(label)
        box.setChecked(bool(value))
        page.form.addRow("", box)
        self._register(key, box, "bool")

    def _choice_field(self, page, label, key, options, current):
        combo = QComboBox()
        for text, data in options:
            combo.addItem(text, data)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        page.form.addRow(label, combo)
        self._fields[key] = (combo, "choice")
        self._choice[key] = combo

    # -- browsing -----------------------------------------------------------
    def _browse_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", edit.text().strip())
        if path:
            edit.setText(path)

    def _browse_file(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate the Tesseract executable", edit.text().strip(), "All files (*.*)"
        )
        if path:
            edit.setText(path)
            self._update_ocr_status(path)

    def _update_ocr_status(self, path: str) -> None:
        if not ocr_mod.engine_present():
            self.lbl_ocr.setText(
                "OCR engine not installed. Install both the 'pytesseract' package and a "
                "Tesseract build, then set the executable above."
            )
            return
        status = ocr_mod.probe(path)
        if status.available:
            langs = ", ".join(ocr_mod.languages(path)[:12])
            self.lbl_ocr.setText(
                f"Engine ready - version {status.version}" + (f", languages: {langs}" if langs else "")
            )
        else:
            self.lbl_ocr.setText(f"Engine not usable: {status.reason}")

    # -- values -------------------------------------------------------------
    def collect(self) -> dict:
        """The edited values, keyed by configuration name."""
        values: dict = {}
        for key, (widget, kind) in self._fields.items():
            if kind == "int":
                values[key] = int(widget.value())
            elif kind == "bool":
                values[key] = bool(widget.isChecked())
            elif kind == "choice":
                values[key] = widget.currentData()
            else:
                values[key] = widget.text().strip()
        return values

    def restore_defaults(self) -> None:
        from ..config import DEFAULTS

        defaults = dict(DEFAULTS)
        for key, (widget, kind) in self._fields.items():
            value = defaults.get(key)
            if kind == "int":
                widget.setValue(int(value))
            elif kind == "bool":
                widget.setChecked(bool(value))
            elif kind == "choice":
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else 0)
            else:
                widget.setText(str(value))
        self._update_ocr_status(str(defaults.get("tesseract", "")).strip())
