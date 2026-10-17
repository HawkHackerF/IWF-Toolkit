"""Reusable widgets: image canvas, hex viewer, data tables and text dialogs."""

from __future__ import annotations

import re

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, DANGER, FG_DIM, OK, WARN, mono_font


def np_to_qimage(arr: np.ndarray) -> QImage:
    """Convert a numpy image array to a QImage (uint8 2D/3D or uint16)."""
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    if arr.ndim == 2:
        return QImage(arr.tobytes(), w, h, w, QImage.Format_Grayscale8).copy()
    if arr.shape[2] == 3:
        return QImage(arr.tobytes(), w, h, w * 3, QImage.Format_RGB888).copy()
    if arr.shape[2] == 4:
        return QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888).copy()
    raise ValueError(f"unsupported array shape {arr.shape}")


class NumericItem(QTableWidgetItem):
    """Table item that sorts by the numeric value stored in UserRole+1."""

    def __lt__(self, other):
        a, b = self.data(Qt.UserRole + 1), other.data(Qt.UserRole + 1)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return super().__lt__(other)


def make_table(headers: list[str], stretch_columns: list[int] | None = None) -> QTableWidget:
    """A read-only, row-selectable table with the workstation look."""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setSectionResizeMode(QHeaderView.Interactive)
    for col in stretch_columns or []:
        header.setSectionResizeMode(col, QHeaderView.Stretch)
    return table


def fill_kv_table(table: QTableWidget, pairs) -> None:
    """Populate a two column key/value table."""
    table.setRowCount(0)
    for key, value, *rest in pairs:
        row = table.rowCount()
        table.insertRow(row)
        key_item = QTableWidgetItem(str(key))
        value_item = QTableWidgetItem(str(value))
        flag = rest[0] if rest else ""
        if flag == "warn":
            value_item.setForeground(WARN)
        elif flag == "danger":
            value_item.setForeground(DANGER)
        elif flag == "ok":
            value_item.setForeground(OK)
        elif flag == "info":
            value_item.setForeground(ACCENT)
        value_item.setToolTip(str(value))
        table.setItem(row, 0, key_item)
        table.setItem(row, 1, value_item)
    table.resizeColumnToContents(0)


class ImageView(QGraphicsView):
    """Zoomable, pannable image canvas that can report pixels and tile clicks."""

    pixelHover = Signal(int, int)
    zoomChanged = Signal(float)
    tileClicked = Signal(object)
    rectDrawn = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self.setBackgroundBrush(QColor(18, 19, 22))
        self._has_image = False
        self._tiles: list = []
        self._tile_mode = True
        self._rect_anchor: QPointF | None = None

    # -- content ------------------------------------------------------------
    def set_image(self, arr: np.ndarray, smooth: bool = False, tiles=None):
        self._item.setPixmap(QPixmap.fromImage(np_to_qimage(arr)))
        self._item.setTransformationMode(
            Qt.SmoothTransformation if smooth else Qt.FastTransformation
        )
        self._scene.setSceneRect(self._item.boundingRect())
        self._tiles = list(tiles or [])
        self._has_image = True

    def has_image(self) -> bool:
        return self._has_image

    def set_tile_mode(self, enabled: bool):
        self._tile_mode = enabled

    # -- geometry -----------------------------------------------------------
    def fit(self):
        if self._has_image:
            self.fitInView(self._item, Qt.KeepAspectRatio)
            self.zoomChanged.emit(self.transform().m11())

    def zoom_to(self, factor: float):
        if not self._has_image:
            return
        self.resetTransform()
        self.scale(factor, factor)
        self.zoomChanged.emit(factor)

    def zoom_by(self, factor: float):
        if not self._has_image:
            return
        self.scale(factor, factor)
        self.zoomChanged.emit(self.transform().m11())

    # -- events -------------------------------------------------------------
    def wheelEvent(self, event):  # noqa: N802
        if not self._has_image:
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.25 if delta > 0 else 0.8)

    def _image_pos(self, event):
        return self._item.mapFromScene(self.mapToScene(event.position().toPoint()))

    def mouseMoveEvent(self, event):  # noqa: N802
        super().mouseMoveEvent(event)
        if not self._has_image:
            return
        p = self._image_pos(event)
        x, y = int(p.x()), int(p.y())
        if 0 <= x < self._item.pixmap().width() and 0 <= y < self._item.pixmap().height():
            self.pixelHover.emit(x, y)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self._has_image:
            p = self._image_pos(event)
            if self._tile_mode and self._tiles:
                for x0, y0, x1, y1, payload in self._tiles:
                    if x0 <= p.x() < x1 and y0 <= p.y() < y1:
                        self.tileClicked.emit(payload)
                        break
            else:
                self._rect_anchor = p
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and not self._tile_mode
            and self._rect_anchor is not None
            and self._has_image
        ):
            p = self._image_pos(event)
            x0, y0 = int(self._rect_anchor.x()), int(self._rect_anchor.y())
            x1, y1 = int(p.x()), int(p.y())
            self._rect_anchor = None
            if abs(x1 - x0) >= 4 and abs(y1 - y0) >= 4:
                self.rectDrawn.emit(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        super().mouseReleaseEvent(event)


class HexView(QWidget):
    """Paged hexadecimal viewer with offset navigation and byte search."""

    ROWS = 256

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = b""
        self._base = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("Offset"))
        self.spin = QSpinBox()
        self.spin.setRange(0, 0)
        self.spin.setDisplayIntegerBase(16)
        self.spin.setPrefix("0x")
        self.spin.setMinimumWidth(110)
        self.spin.editingFinished.connect(lambda: self.goto(self.spin.value()))
        bar.addWidget(self.spin)
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("search: text or hex bytes (e.g. 50 4B 03 04)")
        self.edit_search.returnPressed.connect(self.find_next)
        bar.addWidget(self.edit_search, 1)
        self.btn_find = QPushButton("Find")
        self.btn_find.clicked.connect(self.find_next)
        bar.addWidget(self.btn_find)
        self.btn_prev_page = QPushButton("Prev")
        self.btn_prev_page.clicked.connect(lambda: self.goto(self._base - self.ROWS * 16))
        bar.addWidget(self.btn_prev_page)
        self.btn_next_page = QPushButton("Next")
        self.btn_next_page.clicked.connect(lambda: self.goto(self._base + self.ROWS * 16))
        bar.addWidget(self.btn_next_page)
        layout.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(mono_font(9))
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.view, 1)

        self.lbl_range = QLabel("no data")
        self.lbl_range.setStyleSheet(f"color: {FG_DIM.name()};")
        layout.addWidget(self.lbl_range)
        self._match = -1

    def set_data(self, data: bytes):
        self._data = data or b""
        self.spin.setRange(0, max(0, len(self._data)))
        self.goto(0)

    def goto(self, offset: int):
        offset = max(0, min(int(offset), max(0, len(self._data) - 1)))
        self._base = offset
        self.refresh()

    def refresh(self):
        if not self._data:
            self.view.setPlainText("")
            self.lbl_range.setText("no data")
            return
        start = self._base
        end = min(len(self._data), start + self.ROWS * 16)
        lines = []
        for row in range(start, end, 16):
            chunk = self._data[row : row + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            hex_part = f"{hex_part:<47}"
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{row:08X}  {hex_part}  {ascii_part}")
        self.view.setPlainText("\n".join(lines))
        self.view.moveCursor(QTextCursor.Start)
        self.lbl_range.setText(
            f"0x{start:08X} - 0x{end:08X} of 0x{len(self._data):08X} "
            f"({len(self._data)} bytes)  |  page {start // (self.ROWS * 16) + 1} "
            f"of {max(1, (len(self._data) + self.ROWS * 16 - 1) // (self.ROWS * 16))}"
        )

    # -- search -------------------------------------------------------------
    def _needle(self) -> bytes:
        raw = self.edit_search.text().strip()
        if not raw:
            return b""
        tokens = raw.replace(",", " ").split()
        if tokens and all(len(t) == 2 and all(c in "0123456789abcdefABCDEF" for c in t) for t in tokens):
            try:
                return bytes(int(t, 16) for t in tokens)
            except ValueError:
                return b""
        return raw.encode("latin-1", errors="ignore")

    def find_next(self):
        needle = self._needle()
        if not needle or not self._data:
            return
        start = self._base + 1
        pos = self._data.find(needle, start)
        if pos < 0:
            pos = self._data.find(needle)
        if pos < 0:
            QMessageBox.information(self, "Search", "No match in this file.")
            return
        self.goto(max(0, pos - 32))
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, (pos - self._base) // 16)
        self.view.setTextCursor(cursor)
        self.view.centerCursor()


class HexEditor(QWidget):
    """Editable hexadecimal view of a byte buffer.

    The hex column is the editing surface: retype any pair of hex digits and the
    change is captured when the buffer is committed (navigating, saving or
    running an operation). Beyond direct editing it offers find, find and
    replace, byte insertion, range deletion, revert, save and save as, which is
    what makes it usable as a patch editor for a whole image file.
    """

    ROWS = 256
    COLS = 16

    modifiedChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = b""
        self._base = 0
        self._dirty = False
        self._modified = False
        self._rendering = False
        self._path: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        nav = QHBoxLayout()
        nav.setSpacing(6)
        nav.addWidget(QLabel("Offset"))
        self.spin = QSpinBox()
        self.spin.setRange(0, 0)
        self.spin.setDisplayIntegerBase(16)
        self.spin.setPrefix("0x")
        self.spin.setMinimumWidth(120)
        self.spin.editingFinished.connect(lambda: self.goto(self.spin.value()))
        nav.addWidget(self.spin)
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("find: text, or spaced hex bytes (e.g. 50 4B 03 04)")
        self.edit_search.returnPressed.connect(self.find_next)
        nav.addWidget(self.edit_search, 1)
        self.chk_hex = QCheckBox("hex")
        self.chk_hex.setToolTip("interpret the search box as hex bytes rather than text")
        nav.addWidget(self.chk_hex)
        btn_find = QPushButton("Find")
        btn_find.clicked.connect(self.find_next)
        nav.addWidget(btn_find)
        btn_repl = QPushButton("Replace all")
        btn_repl.clicked.connect(self.replace_all)
        nav.addWidget(btn_repl)
        layout.addLayout(nav)

        ops = QHBoxLayout()
        ops.setSpacing(6)
        for label, slot in (
            ("Prev page", lambda: self.goto(self._base - self.ROWS * self.COLS)),
            ("Next page", lambda: self.goto(self._base + self.ROWS * self.COLS)),
            ("Insert bytes...", self.insert_bytes),
            ("Delete range...", self.delete_range),
            ("Revert", self.revert),
            ("Save", self.save),
            ("Save as...", self.save_as),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            ops.addWidget(button)
        ops.addStretch(1)
        layout.addLayout(ops)

        self.view = QPlainTextEdit()
        self.view.setFont(mono_font(9))
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setTabChangesFocus(True)
        self.view.textChanged.connect(self._on_changed)
        layout.addWidget(self.view, 1)

        self.lbl_range = QLabel("no data")
        self.lbl_range.setStyleSheet(f"color: {FG_DIM.name()};")
        layout.addWidget(self.lbl_range)

    # -- data ---------------------------------------------------------------
    def set_data(self, data: bytes, path: str | None = None):
        self._data = bytes(data or b"")
        self._path = path
        self._base = 0
        self._dirty = False
        self._modified = False
        self.spin.setRange(0, max(0, len(self._data)))
        self.refresh()
        self.modifiedChanged.emit(False)

    def set_rows(self, rows: int):
        """Page length in rows; the class default is :attr:`ROWS`."""
        rows = max(16, int(rows))
        if rows != self.ROWS:
            self.ROWS = rows
            self.goto(min(self._base, max(0, len(self._data) - 1)))

    def data(self) -> bytes:
        """The current buffer, including any edits typed into the hex column."""
        self._commit()
        return self._data

    def is_modified(self) -> bool:
        self._commit()
        return self._modified

    def _on_changed(self):
        if not self._rendering:
            self._dirty = True
            if not self._modified:
                self._modified = True
                self.modifiedChanged.emit(True)

    # -- rendering and parsing ---------------------------------------------
    def _render_text(self) -> str:
        if not self._data:
            return ""
        start = self._base
        end = min(len(self._data), start + self.ROWS * self.COLS)
        header = "  offset   " + " ".join(f"{i:02X}" for i in range(self.COLS)) + "   ascii"
        lines = [header]
        for row in range(start, end, self.COLS):
            chunk = self._data[row : row + self.COLS]
            hex_part = " ".join(f"{b:02X}" for b in chunk).ljust(self.COLS * 3 - 1)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{row:08X}  {hex_part}  {ascii_part}")
        return "\n".join(lines)

    def _commit(self):
        """Fold any typed hex edits back into the byte buffer."""
        if not self._dirty:
            return
        out = bytearray()
        for line in self.view.toPlainText().splitlines():
            stripped = line.rstrip()
            if len(stripped) < 10:
                continue
            if not all(c in "0123456789abcdefABCDEF" for c in stripped[:8]):
                continue
            hex_region = stripped[10 : 10 + self.COLS * 3]
            for token in re.findall(r"[0-9a-fA-F]{1,2}", hex_region):
                out.append(int(token, 16) & 0xFF)
        self._data = bytes(out)
        self._dirty = False

    def refresh(self):
        self._rendering = True
        self.view.setPlainText(self._render_text())
        self._rendering = False
        self._update_range()

    def _update_range(self):
        if not self._data:
            self.lbl_range.setText("no data")
            return
        end = min(len(self._data), self._base + self.ROWS * self.COLS)
        span = self.ROWS * self.COLS
        pages = max(1, (len(self._data) + span - 1) // span)
        page = self._base // span + 1
        flag = "  [modified]" if self._modified else ""
        self.lbl_range.setText(
            f"0x{self._base:08X} - 0x{end:08X} of 0x{len(self._data):08X} "
            f"({len(self._data):,} bytes){flag}  |  page {page} of {pages}"
        )

    # -- navigation ---------------------------------------------------------
    def goto(self, offset: int):
        self._commit()
        offset = max(0, min(int(offset), max(0, len(self._data) - 1)))
        self._base = offset
        self.spin.blockSignals(True)
        self.spin.setValue(self._base)
        self.spin.blockSignals(False)
        self.refresh()

    # -- search -------------------------------------------------------------
    def _needle(self) -> bytes:
        raw = self.edit_search.text().strip()
        if not raw:
            return b""
        if self.chk_hex.isChecked():
            tokens = raw.replace(",", " ").split()
            try:
                return bytes(int(token, 16) & 0xFF for token in tokens)
            except ValueError:
                return b""
        tokens = raw.replace(",", " ").split()
        if tokens and all(len(t) == 2 and all(c in "0123456789abcdefABCDEF" for c in t) for t in tokens):
            try:
                return bytes(int(token, 16) for token in tokens)
            except ValueError:
                return b""
        return raw.encode("latin-1", errors="ignore")

    def find_next(self):
        self._commit()
        needle = self._needle()
        if not needle or not self._data:
            return
        pos = self._data.find(needle, self._base + 1)
        if pos < 0:
            pos = self._data.find(needle)
        if pos < 0:
            QMessageBox.information(self, "Find", "No match in this file.")
            return
        self.goto(max(0, pos - self.COLS))
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, (pos - self._base) // self.COLS + 1)
        self.view.setTextCursor(cursor)
        self.view.centerCursor()
        self.edit_search.setToolTip(f"found at 0x{pos:08X}")

    def replace_all(self):
        self._commit()
        needle = self._needle()
        if not needle or not self._data:
            return
        count = self._data.count(needle)
        if count == 0:
            QMessageBox.information(self, "Replace", "The search pattern does not occur in this file.")
            return
        replacement_text, ok = QInputDialog.getText(
            self, "Replace all", f"Replace {count} occurrence(s) with text or hex:",
            QLineEdit.Normal, "",
        )
        if not ok:
            return
        replacement = replacement_text.encode("latin-1", errors="ignore")
        if self.chk_hex.isChecked():
            try:
                replacement = bytes(int(t, 16) & 0xFF for t in replacement_text.replace(",", " ").split())
            except ValueError:
                QMessageBox.warning(self, "Replace", "The replacement is not valid hex.")
                return
        self._data = self._data.replace(needle, replacement)
        self._dirty = False
        self._modified = True
        self.modifiedChanged.emit(True)
        self.refresh()

    # -- editing ------------------------------------------------------------
    def insert_bytes(self):
        self._commit()
        offset_text, ok = QInputDialog.getText(
            self, "Insert bytes",
            f"Insert at offset (hex, 0 - 0x{len(self._data):X}):",
            QLineEdit.Normal, f"{self._base:X}",
        )
        if not ok:
            return
        payload, ok = QInputDialog.getText(
            self, "Insert bytes", "Bytes to insert (hex, e.g. 50 4B 03 04):", QLineEdit.Normal, "",
        )
        if not ok:
            return
        try:
            offset = int(offset_text.strip() or "0", 16)
            blob = bytes(int(t, 16) & 0xFF for t in payload.replace(",", " ").split())
        except ValueError:
            QMessageBox.warning(self, "Insert bytes", "Offset or bytes are not valid hex.")
            return
        if not blob:
            return
        offset = max(0, min(offset, len(self._data)))
        self._data = self._data[:offset] + blob + self._data[offset:]
        self._modified = True
        self.modifiedChanged.emit(True)
        self.goto(offset)

    def delete_range(self):
        self._commit()
        start_text, ok = QInputDialog.getText(
            self, "Delete range", f"Start offset (hex, 0 - 0x{len(self._data):X}):",
            QLineEdit.Normal, f"{self._base:X}",
        )
        if not ok:
            return
        length_text, ok = QInputDialog.getText(
            self, "Delete range", "Number of bytes to delete:", QLineEdit.Normal, "1",
        )
        if not ok:
            return
        try:
            start = int(start_text.strip() or "0", 16)
            length = int(length_text.strip() or "0")
        except ValueError:
            QMessageBox.warning(self, "Delete range", "Start must be hex; length must be decimal.")
            return
        if length <= 0:
            return
        start = max(0, min(start, len(self._data)))
        end = min(len(self._data), start + length)
        if end <= start:
            return
        self._data = self._data[:start] + self._data[end:]
        self._modified = True
        self.modifiedChanged.emit(True)
        self.goto(start)

    def revert(self):
        if self._path:
            try:
                with open(self._path, "rb") as handle:
                    blob = handle.read()
            except OSError as exc:
                QMessageBox.critical(self, "Revert", str(exc))
                return
            self.set_data(blob, self._path)
        else:
            self._dirty = False
            self._modified = False
            self.refresh()
            self.modifiedChanged.emit(False)

    # -- saving -------------------------------------------------------------
    def save(self):
        if not self._path:
            self.save_as()
            return
        self._write(self._path)

    def save_as(self):
        suggested = self._path or "edited.bin"
        path, _ = QFileDialog.getSaveFileName(self, "Save bytes as", suggested, "All files (*.*)")
        if not path:
            return
        self._path = path
        self._write(path)

    def _write(self, path: str):
        blob = self.data()
        try:
            with open(path, "wb") as handle:
                handle.write(blob)
        except OSError as exc:
            QMessageBox.critical(self, "Save bytes", str(exc))
            return
        self._modified = False
        self.modifiedChanged.emit(False)
        self._update_range()


class TextDialog(QDialog):
    """Read-only text viewer for decoded payloads and reports."""
    def __init__(self, title: str, text: str, parent=None, extra_buttons=None, monospace=True, raw=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 600)
        self._text = text
        self._raw = raw
        layout = QVBoxLayout(self)
        self.info = QLabel(f"{len(text)} characters")
        layout.addWidget(self.info)
        self.edit = QPlainTextEdit(text)
        self.edit.setReadOnly(True)
        if monospace:
            self.edit.setFont(mono_font(9))
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.edit, 1)

        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._text))
        save = QPushButton("Save as...")
        save.clicked.connect(self._save)
        row.addWidget(copy)
        row.addWidget(save)
        if raw is not None:
            save_raw = QPushButton("Save bytes...")
            save_raw.clicked.connect(self._save_bytes)
            row.addWidget(save_raw)
        for label, handler in (extra_buttons or []):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        QShortcut(QKeySequence("Escape"), self, self.accept)

    def set_text(self, text: str):
        self._text = text
        self.edit.setPlainText(text)
        self.info.setText(f"{len(text)} characters")

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save text", "payload.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8", errors="replace") as handle:
                handle.write(self._text)

    def _save_bytes(self):
        if not self._raw:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save bytes", "payload.bin", "All files (*.*)")
        if path:
            with open(path, "wb") as handle:
                handle.write(self._raw)


class FindingList(QWidget):
    """A list of analyst-facing findings with simple severity colouring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(mono_font(9))
        layout.addWidget(self.view)

    def set_findings(self, findings: list[str], empty: str = "No notable findings."):
        if not findings:
            self.view.setPlainText(empty)
            return
        self.view.setPlainText("\n".join(f"[{i + 1:02d}] {text}" for i, text in enumerate(findings)))
