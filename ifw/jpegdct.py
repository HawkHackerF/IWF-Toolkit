"""Baseline JPEG decoding to quantized DCT coefficients.

steghide embeds data in the *quantized* DCT coefficients of a JPEG, exactly as
libjpeg's ``jpeg_read_coefficients`` exposes them: one virtual block array per
component, rows and columns in block raster order and, inside a block, the 64
coefficients in natural (row-major) order - not zigzag order.

This module is a dependency-free re-implementation of the parts of libjpeg that
are needed to recover that array:

  * marker parsing (SOF0/SOF1, DQT, DHT, DRI, SOS, EOI)
  * baseline Huffman entropy decoding, including restart markers
  * the exact MCU to block placement rules of ``jdcoefct.c`` / ``jdinput.c``

Progressive (SOF2) and arithmetic coded JPEGs raise :class:`UnsupportedJpeg`
because they do not expose one complete set of quantized coefficients in a
single pass.

The source of truth for every rule below is libjpeg (``val-verde/libjpeg``):
``jutils.c`` (``jpeg_natural_order``), ``jdhuff.c`` (``decode_mcu``),
``jdinput.c`` (``per_scan_setup``) and ``jdcoefct.c`` (``consume_data``).
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "JpegError",
    "UnsupportedJpeg",
    "JpegComponent",
    "JpegDoc",
    "read_jpeg",
    "self_verify",
    "ZIGZAG_TO_NATURAL",
    "NATURAL_TO_ZIGZAG",
]


class JpegError(Exception):
    """The byte stream could not be parsed as a JPEG."""


class UnsupportedJpeg(JpegError):
    """A coding method this decoder does not implement (progressive etc.)."""


#: ``jpeg_natural_order`` from libjpeg ``jutils.c``: the natural (row-major)
#: position of the i'th element of zigzag order.  The trailing sixteen
#: duplicates of 63 are part of libjpeg and stop a corrupt run length from
#: writing outside the block.
ZIGZAG_TO_NATURAL: tuple = (
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
) + (63,) * 16

#: Inverse mapping: natural position -> zigzag index.
NATURAL_TO_ZIGZAG: tuple = tuple(ZIGZAG_TO_NATURAL.index(i) for i in range(64))


class _EndOfData(Exception):
    """The entropy stream hit a marker or the end of the file."""


def _extend(value: int, size: int) -> int:
    """``HUFF_EXTEND`` from libjpeg: unsigned magnitude -> signed value."""
    if size <= 0:
        return 0
    if value < (1 << (size - 1)):
        return value - ((1 << size) - 1)
    return value


def _wrap16(value: int) -> int:
    """C cast of an int to ``JCOEF`` (a signed 16-bit type)."""
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


# ---------------------------------------------------------------------------
# Baseline Huffman decoding


class _HuffTable:
    """Canonical JPEG Huffman table (libjpeg's derived table, slow path)."""

    __slots__ = ("mincode", "maxcode", "valptr", "huffval")

    @classmethod
    def build(cls, bits, huffval) -> "_HuffTable":
        """``bits`` holds the symbol counts for lengths 1..16 at index 1..16."""
        if len(huffval) != sum(bits[1:17]):
            raise JpegError("DHT symbol count does not match the bit counts")
        # code sizes, then the canonical codes (Annex F.1.2.1.1 / F.1.2.1.2)
        sizes: list[int] = []
        for length in range(1, 17):
            sizes.extend([length] * bits[length])
        codes: list[int] = []
        code = 0
        si = sizes[0] if sizes else 0
        k = 0
        while k < len(sizes):
            while k < len(sizes) and sizes[k] == si:
                codes.append(code)
                code += 1
                k += 1
            code <<= 1
            si += 1

        # mincode / maxcode / valptr (Annex F.1.2.1.3)
        self = cls()
        self.mincode = [0] * 17
        self.maxcode = [-1] * 17
        self.valptr = [0] * 17
        p = 0
        for length in range(1, 17):
            if bits[length]:
                self.valptr[length] = p
                self.mincode[length] = codes[p]
                p += bits[length]
                self.maxcode[length] = codes[p - 1]
        self.huffval = tuple(huffval)
        return self

    def decode(self, br: "_BitReader") -> int:
        code = 0
        length = 1
        while length <= 16:
            code = (code << 1) | br.getbit()
            if code <= self.maxcode[length]:
                index = self.valptr[length] + (code - self.mincode[length])
                if 0 <= index < len(self.huffval):
                    return self.huffval[index]
                raise JpegError("Huffman symbol index out of range")
            length += 1
        raise JpegError("invalid Huffman code in entropy data")


class _BitReader:
    """MSB-first bit reader over entropy data, honouring 0xFF 0x00 stuffing.

    When a marker (0xFF followed by anything but 0x00) is reached the reader
    raises :class:`_EndOfData` and leaves ``pos`` on that marker, which is what
    restart handling and the following marker scan need.
    """

    __slots__ = ("data", "pos", "end", "_buf", "_n")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos
        self.end = len(data)
        self._buf = 0
        self._n = 0

    def discard_to_byte(self) -> None:
        """Drop a partially consumed byte (restart alignment)."""
        self._buf = 0
        self._n = 0

    def _fill(self, need: int) -> None:
        while self._n < need:
            if self.pos >= self.end:
                raise _EndOfData()
            byte = self.data[self.pos]
            if byte == 0xFF:
                if self.pos + 1 >= self.end:
                    raise _EndOfData()
                if self.data[self.pos + 1] == 0x00:
                    self.pos += 2
                    byte = 0xFF
                else:
                    raise _EndOfData()  # marker: leave pos on it
            else:
                self.pos += 1
            self._buf = (self._buf << 8) | byte
            self._n += 8

    def getbits(self, k: int) -> int:
        if k <= 0:
            return 0
        self._fill(k)
        value = (self._buf >> (self._n - k)) & ((1 << k) - 1)
        self._n -= k
        self._buf &= (1 << self._n) - 1
        return value

    def getbit(self) -> int:
        if self._n:
            value = (self._buf >> (self._n - 1)) & 1
            self._n -= 1
            self._buf &= (1 << self._n) - 1
            return value
        return self.getbits(1)


def _decode_block(br: _BitReader, dc_tbl: _HuffTable, ac_tbl: _HuffTable,
                  limit: int, pred: int):
    """Decode one 8x8 block into natural order. Returns the new DC predictor."""
    sym = dc_tbl.decode(br)
    if sym > 16:
        raise JpegError("DC magnitude category out of range")
    diff = _extend(br.getbits(sym), sym) if sym else 0
    dc = pred + diff

    block = [0] * 64
    block[0] = _wrap16(dc)
    k = 1
    while k < limit:
        sym = ac_tbl.decode(br)
        run = sym >> 4
        size = sym & 15
        if size:
            k += run
            block[ZIGZAG_TO_NATURAL[k]] = _wrap16(_extend(br.getbits(size), size))
        else:
            if run != 15:
                break  # end of block
            k += 15  # sixteen zeros (the loop's k += 1 completes the 16)
        k += 1
    return dc, block


def _next_marker(data: bytes, pos: int):
    """libjpeg's ``next_marker``: returns ``(marker, pos_after_marker)``."""
    end = len(data)
    while pos < end:
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < end and data[pos] == 0xFF:
            pos += 1
        if pos >= end:
            return 0xD9, pos
        marker = data[pos]
        pos += 1
        if marker == 0x00:  # stuffed byte outside entropy data
            continue
        return marker, pos
    return 0xD9, pos


def _find_restart(data: bytes, pos: int) -> int:
    """Skip forward to the byte after the next RSTn marker."""
    end = len(data)
    while pos < end:
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < end and data[pos] == 0xFF:
            pos += 1
        if pos >= end:
            raise _EndOfData()
        marker = data[pos]
        if 0xD0 <= marker <= 0xD7:
            return pos + 1
        if marker == 0x00:
            continue
        raise _EndOfData()
    raise _EndOfData()


class JpegComponent:
    """One frame component and its quantized DCT coefficients."""

    __slots__ = ("index", "id", "h_samp", "v_samp", "tq",
                 "height_in_blocks", "width_in_blocks", "coef")

    def __init__(self, index: int, cid: int, h_samp: int, v_samp: int, tq: int):
        self.index = index
        self.id = cid
        self.h_samp = h_samp
        self.v_samp = v_samp
        self.tq = tq
        self.height_in_blocks = 0
        self.width_in_blocks = 0
        #: shape ``(height_in_blocks, width_in_blocks, 64)``, natural order
        self.coef: np.ndarray | None = None

    @property
    def num_blocks(self) -> int:
        return self.height_in_blocks * self.width_in_blocks

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"<JpegComponent #{self.index} id={self.id} "
                f"{self.h_samp}x{self.v_samp} blocks="
                f"{self.height_in_blocks}x{self.width_in_blocks}>")


class JpegDoc:
    """A parsed baseline JPEG: dimensions, components, quantized coefficients."""

    def __init__(self):
        self.height = 0
        self.width = 0
        self.components: list[JpegComponent] = []
        self.quant: dict[int, np.ndarray] = {}
        self.restart_interval = 0
        self.num_scans = 0
        self.truncated = False

    @property
    def max_h_samp(self) -> int:
        return max((c.h_samp for c in self.components), default=1)

    @property
    def max_v_samp(self) -> int:
        return max((c.v_samp for c in self.components), default=1)

    @property
    def total_iMCU_rows(self) -> int:
        return -(-self.height // (self.max_v_samp * 8))

    def steghide_block_order(self):
        """Yield ``(component_index, block_row, block_col)`` in steghide order.

        ``JpegFile::read`` walks components, then block rows, then block
        columns; this reproduces that traversal exactly.
        """
        for comp in self.components:
            for row in range(comp.height_in_blocks):
                for col in range(comp.width_in_blocks):
                    yield comp.index, row, col

    def coeff_count(self) -> int:
        """Total coefficients - steghide's ``LinDctCoeffs`` length."""
        return sum(int(c.coef.size) for c in self.components)

    def nonzero_count(self) -> int:
        """Non-zero coefficients - steghide's sample count."""
        return int(sum(np.count_nonzero(c.coef) for c in self.components))


def read_jpeg(data: bytes) -> JpegDoc:
    """Parse a JPEG and decode every baseline scan to quantized coefficients."""
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        raise JpegError("not a JPEG (missing SOI marker)")

    doc = JpegDoc()
    dc_tables: dict[int, _HuffTable] = {}
    ac_tables: dict[int, _HuffTable] = {}
    pos = 2
    end = len(data)
    seen_frame = False

    while True:
        marker, pos = _next_marker(data, pos)
        if marker == 0xD9:  # EOI
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7 or marker == 0xD8:
            continue  # TEM / stray RST / repeated SOI: no length field
        if pos + 2 > end:
            raise JpegError("truncated segment header")
        seglen = int.from_bytes(data[pos:pos + 2], "big")
        body = pos + 2
        segend = body + seglen - 2
        if seglen < 2 or segend > end:
            raise JpegError("segment runs past the end of the file")

        if marker == 0xDB:  # DQT
            _read_dqt(doc, data, body, segend)

        elif marker == 0xC4:  # DHT
            p = body
            while p < segend:
                tc_th = data[p]
                p += 1
                tc, th = tc_th >> 4, tc_th & 15
                if p + 16 > segend:
                    raise JpegError("truncated Huffman table")
                bits = [0] + list(data[p:p + 16])
                p += 16
                total = sum(bits[1:])
                if p + total > segend:
                    raise JpegError("truncated Huffman table symbols")
                table = _HuffTable.build(bits, list(data[p:p + total]))
                p += total
                (ac_tables if tc else dc_tables)[th] = table

        elif marker == 0xDD:  # DRI
            if segend - body >= 2:
                doc.restart_interval = int.from_bytes(data[body:body + 2], "big")

        elif marker in (0xC0, 0xC1, 0xC2, 0xC3) or 0xC5 <= marker <= 0xCF:
            if marker == 0xC2:
                raise UnsupportedJpeg("progressive JPEG is not supported")
            if marker != 0xC0 and marker != 0xC1:
                raise UnsupportedJpeg(f"JPEG frame type 0x{marker:02X} unsupported")
            if seen_frame:
                raise UnsupportedJpeg("multiple frames are not supported")
            _read_frame(doc, data, body, segend)
            seen_frame = True

        elif marker == 0xDA:  # SOS
            if not seen_frame:
                raise JpegError("scan before the frame header")
            scancomps, se, entropy_pos = _read_sos(doc, data, body, segend,
                                                   dc_tables, ac_tables)
            doc.num_scans += 1
            br = _BitReader(data, entropy_pos)
            try:
                _decode_scan(doc, br, scancomps, se)
            except _EndOfData:
                doc.truncated = True
            pos = br.pos
            continue

        # APPn, COM, JPG*, DNL, EXP, reserved: skip by length
        pos = segend

    if not seen_frame:
        raise JpegError("no frame header (SOF) found")
    if doc.num_scans == 0:
        raise JpegError("no scan data (SOS) found")
    for comp in doc.components:
        comp.coef = comp.coef[:comp.height_in_blocks, :comp.width_in_blocks]
    return doc


def _read_dqt(doc: JpegDoc, data: bytes, body: int, segend: int) -> None:
    p = body
    while p < segend:
        pq_tq = data[p]
        p += 1
        precision, tq = pq_tq >> 4, pq_tq & 15
        count = 128 if precision else 64
        if p + count > segend:
            raise JpegError("truncated quantization table")
        raw = data[p:p + count]
        p += count
        if precision:
            values = np.frombuffer(raw, ">u2").astype(np.int64)
        else:
            values = np.frombuffer(raw, np.uint8).astype(np.int64)
        # DQT stores the values in zigzag order; convert to natural order so
        # they index like the coefficient array does.
        natural = np.zeros(64, dtype=np.int64)
        natural[list(ZIGZAG_TO_NATURAL[:64])] = values
        doc.quant[tq] = natural


def _read_frame(doc: JpegDoc, data: bytes, body: int, segend: int) -> None:
    if segend - body < 6:
        raise JpegError("truncated frame header")
    precision = data[body]
    if precision != 8:
        raise UnsupportedJpeg(f"{precision}-bit JPEG is not supported")
    doc.height = int.from_bytes(data[body + 1:body + 3], "big")
    doc.width = int.from_bytes(data[body + 3:body + 5], "big")
    ncomp = data[body + 5]
    if not 1 <= ncomp <= 4:
        raise JpegError("invalid component count")
    if body + 6 + 3 * ncomp > segend:
        raise JpegError("truncated frame header")
    doc.components = []
    p = body + 6
    for index in range(ncomp):
        cid = data[p]
        hv = data[p + 1]
        doc.components.append(
            JpegComponent(index, cid, (hv >> 4) & 15, hv & 15, data[p + 2]))
        p += 3
    if doc.width <= 0 or doc.height <= 0:
        raise JpegError("invalid frame dimensions")
    max_h, max_v = doc.max_h_samp, doc.max_v_samp
    if not (1 <= max_h <= 4 and 1 <= max_v <= 4):
        raise JpegError("invalid sampling factors")
    for comp in doc.components:
        if not (1 <= comp.h_samp <= 4 and 1 <= comp.v_samp <= 4):
            raise JpegError("invalid sampling factors")
        comp.width_in_blocks = -(-doc.width * comp.h_samp // (max_h * 8))
        comp.height_in_blocks = -(-doc.height * comp.v_samp // (max_v * 8))
        # libjpeg pads the virtual array to a whole number of sampling units
        # (jdcoefct.c: request_virt_barray) because the entropy decoder may
        # write a little past the valid area; allocate the same way, then trim.
        rows = -(-comp.height_in_blocks // comp.v_samp) * comp.v_samp
        cols = -(-comp.width_in_blocks // comp.h_samp) * comp.h_samp
        comp.coef = np.zeros((rows, cols, 64), dtype=np.int16)


def _read_sos(doc: JpegDoc, data: bytes, body: int, segend: int,
              dc_tables, ac_tables):
    """Parse an SOS header. Returns ``(scancomps, se, entropy_data_pos)``.

    Each entry of ``scancomps`` is ``(component, dc_table, ac_table)``.
    """
    nscan = data[body]
    p = body + 1
    scancomps = []
    for _ in range(nscan):
        if p + 2 > segend:
            raise JpegError("truncated scan header")
        cid = data[p]
        td_ta = data[p + 1]
        p += 2
        comp = next((c for c in doc.components if c.id == cid), None)
        if comp is None:
            raise JpegError(f"scan references unknown component {cid}")
        dc_id, ac_id = (td_ta >> 4) & 15, td_ta & 15
        if dc_id not in dc_tables or ac_id not in ac_tables:
            raise JpegError("scan references an undefined Huffman table")
        scancomps.append((comp, dc_tables[dc_id], ac_tables[ac_id]))
    if p + 3 > segend:
        raise JpegError("truncated scan header")
    ss, se, ah_al = data[p], data[p + 1], data[p + 2]
    if p + 3 != segend:
        raise JpegError("malformed scan header")
    if ss != 0 or (ah_al >> 4) != 0 or (ah_al & 15) != 0:
        raise UnsupportedJpeg("non-sequential scan (progressive) not supported")
    if not scancomps:
        raise JpegError("scan with no components")
    return scancomps, se, segend


def _decode_scan(doc: JpegDoc, br: _BitReader, scancomps, se: int) -> None:
    """Decode one sequential scan into the component coefficient arrays.

    ``scancomps`` holds ``(component, dc_table, ac_table)`` triples.
    """
    limit = min(int(se) + 1, 64)
    restart_interval = doc.restart_interval
    restarts_to_go = restart_interval
    dc_pred = [0] * len(doc.components)

    def restart_if_needed():
        nonlocal restarts_to_go, dc_pred, br
        if restart_interval and restarts_to_go == 0:
            br.discard_to_byte()
            br.pos = _find_restart(br.data, br.pos)
            restarts_to_go = restart_interval
            dc_pred = [0] * len(doc.components)

    if len(scancomps) > 1:
        # interleaved: one MCU holds h*v blocks per scan component
        # (jdinput.c per_scan_setup + jdcoefct.c consume_data)
        mcus_per_row = -(-doc.width // (doc.max_h_samp * 8))
        for irow in range(doc.total_iMCU_rows):
            for mcol in range(mcus_per_row):
                restart_if_needed()
                for comp, dc_tbl, ac_tbl in scancomps:
                    base_row, base_col = irow * comp.v_samp, mcol * comp.h_samp
                    for y in range(comp.v_samp):
                        row = base_row + y
                        in_row = row < comp.height_in_blocks
                        for x in range(comp.h_samp):
                            col = base_col + x
                            pred, block = _decode_block(
                                br, dc_tbl, ac_tbl, limit, dc_pred[comp.index])
                            dc_pred[comp.index] = pred
                            if in_row and col < comp.width_in_blocks:
                                comp.coef[row, col] = block
                if restart_interval:
                    restarts_to_go -= 1
    else:
        comp, dc_tbl, ac_tbl = scancomps[0]
        # non-interleaved: one block per MCU, raster order over the component
        # grid (per_scan_setup sets MCUs_per_row == width_in_blocks)
        for row in range(comp.height_in_blocks):
            for col in range(comp.width_in_blocks):
                restart_if_needed()
                pred, block = _decode_block(br, dc_tbl, ac_tbl, limit,
                                            dc_pred[comp.index])
                dc_pred[comp.index] = pred
                comp.coef[row, col] = block
                if restart_interval:
                    restarts_to_go -= 1


# ---------------------------------------------------------------------------
# Verification helper: compare against Pillow's decoder


def self_verify(data: bytes, tolerance: int | None = None):
    """Decode ``data`` twice and compare the two decoders.

    Returns ``(ok, detail)``.  The coefficients found by :func:`read_jpeg` are
    dequantized and inverse-DCTed, then compared with Pillow's own decoder.  A
    wrong coefficient order or value differs by tens of levels, so a small
    maximum difference proves both the values and their order are correct.

    Grayscale images are compared directly (no colour transform); 4:4:4 colour
    images are compared in YCbCr and subsampled images on their luminance
    plane, which allows for the rounding Pillow performs on the way out of
    libjpeg.  ``ok`` is ``None`` when the check could not be run at all.
    """
    try:
        import io as _io

        import numpy as _np
        from PIL import Image
    except Exception as exc:  # pragma: no cover - Pillow is a dependency
        return None, f"Pillow unavailable: {exc}"

    doc = read_jpeg(data)
    ref = Image.open(_io.BytesIO(data))
    ref.load()

    if len(doc.components) == 1:
        tolerance = tolerance if tolerance is not None else 2
        if ref.mode != "L":
            ref = ref.convert("L")
        planes = _reconstruct(doc)
        ref_arr = _np.asarray(ref, dtype=_np.int64)[:doc.height, :doc.width]
        scope = "luminance"
    elif len(doc.components) == 3:
        tolerance = tolerance if tolerance is not None else 4
        if any((c.h_samp, c.v_samp) != (1, 1) for c in doc.components):
            # chroma is subsampled, so the three components no longer share a
            # block grid and cannot be stacked; only the luminance plane can be
            # compared, which is unaffected by the upsampler Pillow used.
            if ref.mode != "YCbCr":
                ref = ref.convert("YCbCr")
            luma = max(doc.components,
                       key=lambda c: (c.h_samp * c.v_samp, -c.index))
            planes = _reconstruct_component(doc, luma)
            ref_arr = _np.asarray(ref, dtype=_np.int64)[:doc.height, :doc.width]
            planes = planes[:doc.height, :doc.width]
            ref_arr = ref_arr[..., 0]
            scope = "luminance only (chroma subsampled)"
        else:
            planes = _reconstruct(doc)
            if ref.mode != "YCbCr":
                ref = ref.convert("YCbCr")
            ref_arr = _np.asarray(ref, dtype=_np.int64)[:doc.height, :doc.width]
            scope = "all channels"
    else:
        return True, (f"{len(doc.components)} components; pixel comparison "
                      f"skipped ({doc.width}x{doc.height}, "
                      f"{doc.nonzero_count()} non-zero coefficients)")

    planes = planes[:doc.height, :doc.width]
    err = int(_np.max(_np.abs(planes - ref_arr)))
    return err <= tolerance, (f"max difference {err} (tolerance {tolerance}) "
                              f"in {scope} on {doc.width}x{doc.height}, "
                              f"{doc.nonzero_count()} non-zero coefficients")


def _reconstruct(doc: JpegDoc) -> np.ndarray:
    """Dequantize + inverse DCT every component, return an int image array.

    All components must share the same block grid (grayscale or 4:4:4); for
    subsampled images use :func:`_reconstruct_component` on the component of
    interest instead.
    """
    import numpy as _np

    planes = [_reconstruct_component(doc, comp) for comp in doc.components]
    stacked = planes[0] if len(planes) == 1 else _np.stack(planes, axis=-1)
    return _np.clip(_np.rint(stacked), 0, 255).astype(_np.int64)


def _reconstruct_component(doc: JpegDoc, comp: JpegComponent) -> np.ndarray:
    """Dequantize + inverse DCT a single component to an int level plane."""
    import numpy as _np

    if comp.tq not in doc.quant:
        raise JpegError(f"undefined quantization table {comp.tq}")
    q = doc.quant[comp.tq]
    if not _np.all(q):
        raise JpegError("quantization table contains a zero entry")
    block = comp.coef.astype(_np.float64) * q.reshape(1, 1, 64)
    # (blocks, blocks, 8, 8) -> (blocks * 8, blocks * 8)
    spat = _idct_blocks(block).transpose(0, 2, 1, 3)
    rows, cols = comp.height_in_blocks * 8, comp.width_in_blocks * 8
    return _np.clip(_np.rint(spat.reshape(rows, cols) + 128.0), 0, 255).astype(_np.int64)


_COS: np.ndarray | None = None


def _idct_matrix() -> np.ndarray:
    """``mat[k, x] = alpha[k] * cos((2x+1) k pi / 16)`` - the orthonormal DCT-II basis."""
    global _COS
    if _COS is None:
        import math

        n = 8
        mat = np.zeros((n, n), dtype=np.float64)
        for k in range(n):
            alpha = math.sqrt(1.0 / n) if k == 0 else math.sqrt(2.0 / n)
            for x in range(n):
                mat[k, x] = alpha * math.cos((2 * x + 1) * k * math.pi / (2 * n))
        _COS = mat
    return _COS


def _idct_blocks(blocks: np.ndarray) -> np.ndarray:
    """Separable orthonormal inverse DCT-II over the trailing 64-coefficient axis.

    The coefficient array is stored in natural (row-major) order, so reshaping
    it to ``(..., 8, 8)`` yields ``[frequency_row, frequency_col]`` and the
    transform is applied once per axis.
    """
    mat = _idct_matrix()
    grid = np.asarray(blocks, dtype=np.float64).reshape(*blocks.shape[:-1], 8, 8)
    return (mat.T @ grid) @ mat
