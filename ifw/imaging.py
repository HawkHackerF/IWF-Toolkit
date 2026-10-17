"""Image document model with bit-level and byte-level forensic access.

This module is deliberately free of any GUI dependency so that it can be reused
headlessly (batch processing, unit tests, scripting). It provides:

    * ImageDoc          - a uniform view over any Pillow-readable image, keeping
                          the original container facts (format, mode, palette,
                          info) alongside a numpy working array
    * channel access    - base channels (Gray/R/G/B/A), the palette index plane
                          and derived carriers (channel XOR, channel difference,
                          per-pixel maximum/minimum/average)
    * bit planes        - isolation, inversion, edge and keep/clear views
    * bit streams       - building bit sequences for single or interleaved
                          carriers, unpacking them into bytes with configurable
                          bit width, offset and bit order
    * payload embedding - lossless LSB substitution on a chosen carrier
    * scoring           - text-likeness scoring and pattern search used to rank
                          candidate payloads
    * montage           - a single zoomable image holding every bit plane
"""

from __future__ import annotations

import itertools
import math
import os
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Forensic tool: do not abort on large images, the operator decides what to load.
Image.MAX_IMAGE_PIXELS = None

IMAGE_FILTER = (
    "Images (*.png *.bmp *.jpg *.jpeg *.jpe *.gif *.tif *.tiff *.webp *.ppm *.pgm *.pbm *.ico);;"
    "All files (*.*)"
)

SUPPORTED_EXTS = (
    ".png", ".bmp", ".jpg", ".jpeg", ".jpe", ".gif", ".tif", ".tiff",
    ".webp", ".ppm", ".pgm", ".pbm", ".ico",
)

#: Patterns used to recognise challenge-style payloads. The first pattern is the
#: authoritative one (keyword prefix such as flag{...}); the second is a generic
#: "something{...}" shape which is reported separately because it produces noise.
FLAG_RES = [
    re.compile(r"(?i)\b(?:flag|ctf|picoctf|htb|key|secret|pass(?:word)?)\{[\x21-\x7e]{1,200}\}"),
    re.compile(r"(?<![\w])[A-Za-z0-9_\-]{2,25}\{[\x21-\x7e]{3,200}\}"),
]
REAL_FLAG_RE = re.compile(r"(?i)^(?:flag|ctf|picoctf|htb|key|secret|pass(?:word)?)\{")
_KEYWORDS = ("flag", "ctf", "picoctf", "htb", "key", "secret", "pass", "password")
#: Case sensitive pre-filter (fast path) plus a case-insensitive variant applied
#: only to the head of a stream, where an unusual capitalisation is most likely.
FLAG_QUICK_RE = re.compile(
    rb"(?:flag|Flag|FLAG|ctf|Ctf|CTF|picoctf|PicoCTF|htb|HTB|key|KEY|secret|pass|PASS)\{"
)
FLAG_QUICK_HEAD_RE = re.compile(rb"(?i)(?:flag|ctf|picoctf|htb|key|secret|pass)\{")
QUICK_HEAD_BYTES = 8192
STRING_RE = re.compile(r"[\x20-\x7e]{8,}")
PRINTABLE = (32, 127)

BASE_CHANNELS = ("Gray", "R", "G", "B", "A")
DERIVED_CHANNELS = ("R^G", "R^B", "G^B", "|R-G|", "|R-B|", "|G-B|", "Max", "Min", "Avg")


def is_real_flag(value: str) -> bool:
    """True when the match starts with a recognised flag keyword (flag{, ctf{...)."""
    return bool(REAL_FLAG_RE.match(value))


# ---------------------------------------------------------------------------
# Byte / bit helpers
# ---------------------------------------------------------------------------
def pack_bits(bits: np.ndarray, msb_first: bool = True) -> np.ndarray:
    """Pack a 0/1 bit array into bytes, dropping any trailing partial byte."""
    if bits.size < 8:
        return np.zeros(0, dtype=np.uint8)
    n8 = (bits.size // 8) * 8
    return np.packbits(
        np.ascontiguousarray(bits[:n8], dtype=np.uint8),
        bitorder="big" if msb_first else "little",
    )


def bytes_to_bits(data: bytes, msb_first: bool = True) -> np.ndarray:
    """Expand bytes into a 0/1 bit array."""
    return np.unpackbits(
        np.frombuffer(data, dtype=np.uint8),
        bitorder="big" if msb_first else "little",
    )


def longest_printable_run(vals: np.ndarray) -> int:
    """Length of the longest run of printable ASCII bytes in an array."""
    pr = (vals >= PRINTABLE[0]) & (vals < PRINTABLE[1])
    nz = np.flatnonzero(~pr)
    if nz.size == 0:
        return int(vals.size)
    return int(np.diff(np.concatenate(([-1], nz, [vals.size]))).max() - 1)


def as_text(data) -> str:
    """Interpret bytes or a numpy array as latin-1 text without raising."""
    if isinstance(data, str):
        return data
    if isinstance(data, np.ndarray):
        return data.astype(np.uint8).tobytes().decode("latin-1")
    return bytes(data).decode("latin-1")


def to_display(arr: np.ndarray) -> np.ndarray:
    """Reduce a working array to something displayable as 8 bit per channel."""
    if arr.dtype == np.uint8:
        return arr
    if arr.dtype == np.uint16:
        return (arr >> 8).astype(np.uint8)
    return np.clip(arr, 0, 255).astype(np.uint8)


def find_strings(data, limit: int = 200, min_len: int = 8):
    """Printable strings of at least `min_len` characters, with offsets."""
    out, seen = [], set()
    for match in STRING_RE.finditer(as_text(data)):
        text = match.group(0)
        if len(text) < min_len or text in seen:
            continue
        seen.add(text)
        if sum(c.isalnum() or c.isspace() for c in text) / len(text) < 0.6:
            continue
        out.append((match.start(), text))
        if len(out) >= limit:
            break
    return out


def find_flags(data, limit: int = 100):
    """Keyword flags and generic brace patterns, as (offset, match) pairs."""
    text = as_text(data)
    hits, seen = [], set()
    for rx in FLAG_RES:
        for match in rx.finditer(text):
            value = match.group(0)
            if value in seen:
                continue
            seen.add(value)
            hits.append((match.start(), value))
            if len(hits) >= limit:
                return hits
    return hits


def find_keywords(data, limit: int = 200):
    """Case-insensitive keyword hits, useful for payload triage."""
    text = as_text(data)
    lowered = text.lower()
    hits, seen = [], set()
    for kw in _KEYWORDS:
        start = 0
        while len(hits) < limit:
            pos = lowered.find(kw, start)
            if pos < 0:
                break
            start = pos + 1
            snippet = text[pos : pos + 80]
            if snippet in seen:
                continue
            seen.add(snippet)
            hits.append((pos, snippet))
    return hits


def score_text(text: str, sample: int = 200_000, min_run: int = 6):
    """Score 0..1 for how much a decoded stream looks like human text.

    Combines the printable ratio, the longest printable run and the number of
    substantial printable runs. Returns (score, longest_snippet).
    """
    text = text[:sample]
    if not text:
        return 0.0, ""
    printable = sum(1 for c in text if PRINTABLE[0] <= ord(c) < PRINTABLE[1]) / len(text)
    runs = STRING_RE.findall(text)
    longest = max((len(r) for r in runs), default=0)
    nlong = sum(1 for r in runs if len(r) >= min_run)
    score = printable * 0.4 + min(longest / 60.0, 1.0) * 0.4 + min(nlong / 5.0, 1.0) * 0.2
    snippet = max(runs, key=len) if runs else ""
    return score, snippet


def plane_stats(bits: np.ndarray, block: int = 16):
    """Heuristic text-likeness of a bit plane as (score, density).

    The score is the lowest transition rate among 16x16 blocks that look like
    rendered text: they must contain a busy row and an empty row and must not be
    solid. Noise yields a high score, uniform areas are rejected outright.
    """
    if bits.size < 4:
        return 0.0, 0.0
    density = float(bits.mean())
    if bits.ndim != 2:
        return density, density
    h, w = bits.shape
    if h < block or w < block:
        dh = np.count_nonzero(bits[:, 1:] != bits[:, :-1]) / max(h * (w - 1), 1)
        dv = np.count_nonzero(bits[1:, :] != bits[:-1, :]) / max((h - 1) * w, 1)
        return float((dh + dv) / 2), density

    dh = np.zeros_like(bits, dtype=np.float32)
    dh[:, 1:] = bits[:, 1:] != bits[:, :-1]
    dv = np.zeros_like(bits, dtype=np.float32)
    dv[1:, :] = bits[1:, :] != bits[:-1, :]
    bh, bw = h // block, w // block
    hh, ww = bh * block, bw * block
    dhb = dh[:hh, :ww].reshape(bh, block, bw, block)
    dt = ((dhb + dv[:hh, :ww].reshape(bh, block, bw, block)).mean(axis=(1, 3))) / 2.0
    dens = bits[:hh, :ww].reshape(bh, block, bw, block).mean(axis=(1, 3))
    per_row = dhb.sum(axis=3)
    rmax = per_row.max(axis=1)
    rmin = per_row.min(axis=1)
    textlike = (rmax >= 2) & (rmin == 0) & (dens > 0.02) & (dens < 0.95)
    score = float(dt[textlike].min()) if textlike.any() else 1.0
    return score, density


def pil_font(size: int):
    """A Pillow font at the requested size, with a fallback for old versions."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------
class ImageDoc:
    """A loaded image plus the facts needed for forensic analysis."""

    def __init__(self, path: str):
        self.path = path
        self.notes: list[str] = []
        self.filename = os.path.basename(path)
        self.file_size = os.path.getsize(path) if os.path.exists(path) else 0

        pil = Image.open(path)
        pil.load()
        self.pil = pil
        self.original_mode = pil.mode
        self.format = (pil.format or "UNKNOWN").upper()
        self.info = dict(pil.info)
        self.n_frames = getattr(pil, "n_frames", 1)

        # Palette images: keep the index plane, it is a carrier in its own right.
        self.index_array: np.ndarray | None = None
        self.palette: list[tuple[int, int, int]] = []
        if pil.mode == "P":
            self.index_array = np.array(pil, dtype=np.uint8)
            raw = pil.getpalette() or []
            self.palette = [
                (raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw) - 2, 3)
            ]

        work = pil
        if work.mode == "P":
            work = work.convert("RGBA" if "transparency" in work.info else "RGB")
        elif work.mode in ("CMYK", "YCbCr", "LAB", "HSV"):
            self.notes.append(f"colour mode {self.original_mode} converted to RGB for analysis")
            work = work.convert("RGB")
        elif work.mode == "LA":
            work = work.convert("RGBA")
        elif work.mode == "1":
            work = work.convert("L")
        self.work_mode = work.mode

        arr = np.array(work)
        if arr.dtype != np.uint8:
            if arr.max() > 255:
                self.notes.append(
                    f"{arr.dtype}: analysis at native bit depth, display reduced to 8 bit"
                )
                arr = arr.astype(np.uint16)
            else:
                arr = arr.astype(np.uint8)

        self.array = arr
        if arr.ndim == 2:
            self.channels = ["Gray"]
        elif arr.ndim == 3:
            n = arr.shape[2]
            self.channels = ["R", "G", "B", "A"][:n] if n == 4 else ["R", "G", "B"][:n]
        else:
            raise ValueError("unsupported image shape")
        self.bits = 8 if self.array.dtype == np.uint8 else 16
        self.bitdepth_native = self.bits

    # -- geometry -----------------------------------------------------------
    @property
    def width(self) -> int:
        return int(self.array.shape[1])

    @property
    def height(self) -> int:
        return int(self.array.shape[0])

    @property
    def is_color(self) -> bool:
        return self.array.ndim == 3 and self.array.shape[2] >= 3

    @property
    def is_indexed(self) -> bool:
        return self.index_array is not None

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def index_of(self, channel: str):
        if channel in ("Gray", "RGB", "P") or self.array.ndim == 2:
            return None
        return self.channels.index(channel)

    # -- channel access -----------------------------------------------------
    def channel_array(self, channel: str) -> np.ndarray:
        if channel == "P":
            if self.index_array is None:
                raise ValueError("image has no palette index plane")
            return self.index_array
        if channel in ("Gray", "Max", "Min", "Avg") or self.array.ndim == 2:
            return self.array
        if channel == "RGB":
            return self.array[..., :3]
        return self.array[..., self.channels.index(channel)]

    def available_channels(self) -> list[str]:
        """Base channels, palette plane and derived carriers that this image supports."""
        names = list(self.channels)
        if self.is_indexed:
            names.append("P")
        if self.is_color:
            names.extend(DERIVED_CHANNELS)
        return names

    def sample_count(self, carrier: str) -> int:
        """Number of bit samples a carrier exposes, without materialising it.

        Used for capacity reporting so that huge images do not have to be
        flattened just to learn how much data they can hold.
        """
        if carrier == "P":
            return 0 if self.index_array is None else int(self.index_array.size)
        if carrier == "RGB":
            channels = self.array.shape[2] if self.array.ndim == 3 else 1
            return self.pixels * int(channels)
        return self.pixels

    def _sample_rows(self, name: str, max_samples: int | None) -> int | None:
        """How many leading image rows cover `max_samples` samples of a carrier."""
        if max_samples is None or self.width <= 0:
            return None
        per_row = self.width * (3 if name == "RGB" else 1)
        return min(self.height, int(math.ceil(max_samples / per_row)))

    def carrier_values(self, name: str, max_samples: int | None = None) -> np.ndarray:
        """Flat uint16 values for a named carrier (base, palette or derived).

        `max_samples` limits how many leading samples are materialised. The
        scanner only ever needs as many samples as its byte budget requires, so
        this keeps a sweep proportional to the payload budget rather than to the
        image resolution - a multi-megapixel image is handled as cheaply as a
        thumbnail.
        """
        rows = self._sample_rows(name, max_samples)

        def head(source: np.ndarray) -> np.ndarray:
            return source if rows is None else source[:rows]

        if name == "P":
            source = self.index_array if self.index_array is not None else np.zeros((0, 0), np.uint8)
            return head(source).ravel().astype(np.uint16)
        if self.array.ndim == 2:
            return head(self.array).ravel().astype(np.uint16)
        if name in DERIVED_CHANNELS:
            return self._derived(name, head(self.array)).ravel()
        if name == "Gray":
            return head(self.array[..., 0]).ravel().astype(np.uint16)
        if name == "RGB":
            return head(self.array[..., :3]).ravel().astype(np.uint16)
        return head(self.array[..., self.channels.index(name)]).ravel().astype(np.uint16)

    def _derived(self, name: str, source: np.ndarray | None = None) -> np.ndarray:
        array = self.array if source is None else source
        r, g, b = (array[..., i].astype(np.int32) for i in range(3))
        table = {
            "R^G": r ^ g,
            "R^B": r ^ b,
            "G^B": g ^ b,
            "|R-G|": np.abs(r - g),
            "|R-B|": np.abs(r - b),
            "|G-B|": np.abs(g - b),
            "Max": np.maximum(np.maximum(r, g), b),
            "Min": np.minimum(np.minimum(r, g), b),
            "Avg": (r + g + b) // 3,
        }
        return table[name].astype(np.uint16)

    def derived_image(self, name: str) -> np.ndarray:
        """Derived carrier as a displayable 8 bit image."""
        if name in ("Max", "Min", "Avg") or name in ("|R-G|", "|R-B|", "|G-B|"):
            return to_display(np.clip(self._derived(name), 0, 255).astype(np.uint16))
        if name in ("R^G", "R^B", "G^B"):
            return to_display(np.clip(self._derived(name), 0, 255).astype(np.uint16))
        return to_display(self.channel_array(name) if name != "RGB" else self.array)

    def describe(self) -> str:
        shape = "x".join(str(d) for d in self.array.shape)
        return (
            f"{self.filename} | {self.width}x{self.height} | format {self.format} | "
            f"mode {self.original_mode} | array {shape} {self.array.dtype} | "
            f"channels {', '.join(self.channels)} | {self.bits} bit/channel"
        )

    def capacity_bytes(self, carrier, bit_width: int = 1) -> int:
        """Number of payload bytes a carrier can hold at the given bit width.

        `carrier` may be a single channel name, ``"RGB"`` or a tuple of channel
        names (an interleaved carrier).
        """
        names = carrier if isinstance(carrier, (list, tuple)) else (carrier,)
        total = sum(self.sample_count(name) for name in names)
        return (total * bit_width) // 8


# ---------------------------------------------------------------------------
# Bit planes and views
# ---------------------------------------------------------------------------
def compute_view(
    doc: ImageDoc, channel: str, bit: int, mode: str, invert: bool = False
) -> np.ndarray:
    """Render a channel/bit-plane view.

    mode: "plane" (bit set -> white), "plane_inv", "edge" (transitions),
    "keep0" (clear the bit), "keep1" (set the bit), "orig" (untouched).
    """
    arr = doc.array
    if bit >= doc.bits:
        bit = doc.bits - 1
    mask = np.uint16(1) << bit

    if mode == "orig":
        return to_display(arr)
    if mode in ("keep0", "keep1"):
        out = (arr & ~mask) if mode == "keep0" else (arr | mask)
        return to_display(out.astype(arr.dtype))
    if mode not in ("plane", "plane_inv", "edge"):
        raise ValueError(f"unknown view mode: {mode}")

    if channel == "RGB" and doc.is_color:
        planes = [
            np.where(((arr[..., i] >> bit) & 1).astype(bool), 255, 0).astype(np.uint8)
            for i in range(3)
        ]
        out = np.stack(planes, axis=-1)
        return 255 - out if invert else out

    source = doc.channel_array(channel)
    if source.ndim == 3:
        source = source[..., 0]
    bp = ((source >> bit) & 1).astype(np.uint8)
    if invert:
        bp = 1 - bp
    if mode == "plane":
        return np.where(bp, 255, 0).astype(np.uint8)
    if mode == "plane_inv":
        return np.where(bp, 0, 255).astype(np.uint8)
    edges = np.zeros_like(bp, dtype=np.uint8)
    edges[:, 1:] |= (bp[:, 1:] != bp[:, :-1]).astype(np.uint8)
    edges[1:, :] |= (bp[1:, :] != bp[:-1, :]).astype(np.uint8)
    return np.where(edges, 255, 0).astype(np.uint8)


def unravel_values(vals: np.ndarray, bit: int, width: int, msb_first: bool) -> np.ndarray:
    """Turn per-sample values into bits taken from `width` bits starting at `bit`."""
    v = ((vals >> bit) & np.uint16((1 << width) - 1)).astype(np.uint16)
    if width == 1:
        return v.astype(np.uint8)
    shift = (
        np.arange(width - 1, -1, -1, dtype=np.uint16)
        if msb_first
        else np.arange(width, dtype=np.uint16)
    )
    return ((v[:, None] >> shift[None, :]) & np.uint16(1)).astype(np.uint8)


def carrier_bitseq(
    doc: ImageDoc, spec, bit: int = 0, width: int = 1, msb_first: bool = True,
    max_bits: int | None = None,
) -> np.ndarray:
    """Bit sequence for a single carrier or for interleaved carriers.

    `spec` may be a single channel name ("B"), the string "RGB", or a tuple of
    channel names whose bits are interleaved per pixel in the given order.
    """
    if isinstance(spec, str):
        spec = tuple(doc.channels[:3]) if spec == "RGB" else (spec,)

    if len(spec) == 1:
        n_samples = int(math.ceil(max_bits / width)) if max_bits is not None else None
        vals = doc.carrier_values(spec[0], max_samples=n_samples)
        if n_samples is not None:
            vals = vals[:n_samples]
        return unravel_values(vals, bit, width, msb_first).reshape(-1)

    n_samples = (
        int(math.ceil(max_bits / (len(spec) * width))) if max_bits is not None else None
    )
    columns = [doc.carrier_values(c, max_samples=n_samples) for c in spec]
    if n_samples is not None:
        columns = [c[:n_samples] for c in columns]
    parts = [unravel_values(c, bit, width, msb_first) for c in columns]
    return np.stack(parts, axis=1).reshape(-1)


def extract_stream(
    doc: ImageDoc,
    carrier,
    bit: int,
    msb_first: bool,
    offset: int,
    max_bytes: int = 0,
    all_bits: bool = False,
    bit_width: int = 1,
) -> np.ndarray:
    """Decode a payload stream from a carrier into bytes.

    This is the canonical decode API: any candidate reported by the scanner must
    be reproducible by calling this function with the same parameters.

    `bit`/`bit_width` select which bits of each sample are read; the bits of one
    sample are always taken most significant first. `msb_first` selects how the
    resulting bit stream is grouped into bytes: True assembles the first bit of
    each group as the most significant bit of the byte, False assembles it as
    the least significant bit. `offset` is a bit offset into the stream.
    """
    need = (max_bytes * 8 + offset) if max_bytes else None
    if all_bits:
        bits = carrier_bitseq(doc, carrier, 0, doc.bits, True, need)
    else:
        bits = carrier_bitseq(doc, carrier, bit, bit_width, True, need)
    if offset:
        bits = bits[offset:]
    vals = pack_bits(bits, msb_first=msb_first)
    return vals[:max_bytes] if max_bytes else vals


def embed_bytes(
    doc: ImageDoc, carrier: str, bit: int, data: bytes, msb_first: bool, offset: int = 0
) -> np.ndarray:
    """Return a copy of the working array with `data` written into one bit plane.

    Every other bit of every sample is preserved, so the modification is exactly
    a least-significant-bit substitution on the selected carrier.
    """
    out = doc.array.copy()
    ci = doc.index_of(carrier)
    plane = out if ci is None else out[..., ci]
    flat = np.array(plane.ravel(), copy=True)
    bits = bytes_to_bits(data, msb_first=msb_first)
    if offset:
        bits = np.concatenate([np.zeros(offset, np.uint8), bits])
    n = min(bits.size, flat.size)
    mask = np.uint16(1) << bit
    segment = flat[:n].astype(np.uint16)
    flat[:n] = ((segment & ~mask) | (bits[:n].astype(np.uint16) << bit)).astype(flat.dtype)
    if ci is None:
        out[...] = flat.reshape(plane.shape)
    else:
        out[..., ci] = flat.reshape(plane.shape)
    return out


# ---------------------------------------------------------------------------
# Montage of every bit plane
# ---------------------------------------------------------------------------
def montage_tiles(doc: ImageDoc, invert: bool = False, sub_size=(234, 144)):
    """Ordered tile definitions: (label, image array, payload) for every plane."""
    tiles = []
    original = to_display(doc.array)
    orig_im = Image.fromarray(original, "RGB" if original.ndim == 3 else "L")
    orig_im.thumbnail(sub_size, Image.NEAREST)
    tiles.append((
        f"ORIGINAL\n{doc.width}x{doc.height} {doc.original_mode}",
        np.array(orig_im),
        dict(kind="orig", ch=None, bit=None),
    ))

    subs = {}
    for ch in doc.channels:
        a = doc.channel_array(ch)
        if a.ndim == 3:
            a = a[..., 0]
        im = Image.fromarray(a if a.dtype == np.uint8 else (a >> 8).astype(np.uint8), "L")
        im.thumbnail(sub_size, Image.NEAREST)
        subs[ch] = np.array(im)

    for ch in doc.channels:
        for bit in range(doc.bits):
            bp = ((subs[ch] >> bit) & 1).astype(np.uint8)
            if invert:
                bp = 1 - bp
            tr, dens = plane_stats(bp)
            tag = " (LSB)" if bit == 0 else (" (MSB)" if bit == doc.bits - 1 else "")
            tiles.append((
                f"{ch} bit {bit}{tag}\ntransition {tr:.3f}  density {dens:.2f}",
                np.where(bp, 255, 0).astype(np.uint8),
                dict(kind="plane", ch=ch, bit=bit, tr=tr, dens=dens),
            ))

    if doc.is_color:
        for bit in range(doc.bits):
            parts = []
            for i in range(3):
                bp = ((subs[doc.channels[i]] >> bit) & 1).astype(np.uint8)
                if invert:
                    bp = 1 - bp
                parts.append(np.where(bp, 255, 0).astype(np.uint8))
            tiles.append((
                f"RGB bit {bit}\n(three channels)",
                np.stack(parts, -1),
                dict(kind="rgbplane", ch="RGB", bit=bit),
            ))
    return tiles


def build_montage(
    doc: ImageDoc, invert: bool = False, tile_w: int = 250, tile_h: int = 160,
    cols: int = 6, max_tiles: int = 120, title: str | None = None,
):
    """Render every bit plane into one image; returns (rgb array, tile map).

    Tile map entries are (x0, y0, x1, y1, payload) where payload identifies the
    plane so the GUI can open it on click.
    """
    pad, gap, label_h, head_h = 10, 10, 34, 40
    data = montage_tiles(doc, invert, sub_size=(tile_w - 16, tile_h - 16))[:max_tiles]
    n = len(data)
    cols = max(1, min(cols, n))
    rows = int(math.ceil(n / cols))
    cell_w, cell_h = tile_w + gap, tile_h + label_h + gap
    width = pad * 2 + cols * cell_w
    height = head_h + pad * 2 + rows * cell_h

    canvas = Image.new("RGB", (width, height), (22, 23, 26))
    draw = ImageDraw.Draw(canvas)
    font_head = pil_font(22)
    font_label = pil_font(14)
    if title is None:
        title = f"Bit planes | {doc.filename} | {doc.width}x{doc.height} | {doc.format}"
    draw.text((pad, 10), title, fill=(235, 235, 240), font=font_head)

    tiles = []
    for i, (label, arr, payload) in enumerate(data):
        r, c = divmod(i, cols)
        x0 = pad + c * cell_w
        y0 = head_h + pad + r * cell_h
        draw.rectangle(
            [x0, y0, x0 + tile_w, y0 + tile_h + label_h],
            fill=(34, 35, 39), outline=(72, 74, 80),
        )
        im = Image.fromarray(arr, "RGB" if arr.ndim == 3 else "L")
        ow, oh = im.size
        sx = x0 + 8 + max(0, (tile_w - 16 - ow) // 2)
        sy = y0 + 8 + max(0, (tile_h - 16 - oh) // 2)
        canvas.paste(im, (sx, sy))
        hot = payload["kind"] == "plane" and payload.get("tr", 1.0) < 0.25
        draw.rectangle(
            [x0, y0, x0 + tile_w, y0 + tile_h + label_h],
            outline=(214, 168, 68) if hot else (72, 74, 80),
            width=2 if hot else 1,
        )
        for j, line in enumerate(label.split("\n")):
            draw.text((x0 + 6, y0 + tile_h + 4 + j * 15), line, fill=(226, 226, 230), font=font_label)
        tiles.append((x0, y0, x0 + tile_w, y0 + tile_h, payload))
    return np.array(canvas), tiles


# ---------------------------------------------------------------------------
# Headless self-test: run with  python -m ifw.imaging
def _selftest() -> int:
    import os as _os
    import tempfile as _tf

    failures = []

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, size=(64, 96, 3), dtype=np.uint8)
    tmp = _tf.mkdtemp(prefix="ifw_imaging_")
    path = _os.path.join(tmp, "doc.png")
    Image.fromarray(arr).save(path)

    doc = ImageDoc(path)
    check("width/height", doc.width == 96 and doc.height == 64)
    check("channels", doc.channels == ["R", "G", "B"])
    check("is_color", doc.is_color is True)
    check("bits", doc.bits == 8)
    check("channel_array shape", doc.channel_array("B").shape == (64, 96))
    check("derived carriers present", "R^G" in doc.available_channels() and "|R-B|" in doc.available_channels())
    check("describe non-empty", bool(doc.describe()))

    payload = b"flag{imaging_roundtrip_ok}\x00\x01"
    stego = embed_bytes(doc, "B", 0, payload, msb_first=True, offset=0)
    rt = _os.path.join(tmp, "stego.png")
    Image.fromarray(stego).save(rt)
    got = extract_stream(ImageDoc(rt), "B", 0, True, 0, len(payload), False, 1).tobytes()
    check("embed survives PNG round-trip", got == payload, f"{got[:24]!r}")

    got2 = extract_stream(ImageDoc(rt), "B", 0, True, 0, len(payload), False, 1).tobytes()
    check("extract_stream reads embedded bytes", got2 == payload, f"{got2[:24]!r}")

    montage, tiles = build_montage(doc)
    check("montage is 3-D", montage.ndim == 3)
    check("montage has tiles", len(tiles) > 0, f"{len(tiles)} tiles")

    bits = carrier_bitseq(doc, "B", 0, 1, True, 1024)
    check("carrier_bitseq length", bits.size >= 1024, f"{bits.size} bits")

    # resolution independence: capacity and carrier slicing must not depend on
    # materialising the whole plane.
    big_arr = rng.integers(0, 256, size=(600, 900, 3), dtype=np.uint8)
    big_path = _os.path.join(tmp, "big.png")
    Image.fromarray(big_arr).save(big_path)
    big = ImageDoc(big_path)
    check("capacity equals pixels/8", big.capacity_bytes("B") == (900 * 600) // 8,
          f"{big.capacity_bytes('B')}")
    check("interleaved capacity", big.capacity_bytes(("R", "G", "B")) == (3 * 900 * 600) // 8,
          f"{big.capacity_bytes(('R', 'G', 'B'))}")
    limited = carrier_bitseq(big, "B", 0, 1, True, 4000)
    check("bounded bit sequence is exact", limited.size == 4000, f"{limited.size}")
    prefix = big.carrier_values("B", max_samples=5000)
    check("bounded carrier prefix covers request", 5000 <= prefix.size <= 5000 + 900,
          f"{prefix.size}")
    full_prefix = big.carrier_values("B")[:5000]
    check("bounded prefix matches full plane", np.array_equal(prefix[:5000], full_prefix))

    vals = extract_stream(doc, "B", 0, True, 0, 16, False, 1)
    check("extract_stream returns bytes", vals.size == 16 and vals.dtype == np.uint8)

    score, snippet = score_text(as_text(vals))
    check("score_text in [0,1]", 0.0 <= score <= 1.0, f"score={score:.3f}")

    print()
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
