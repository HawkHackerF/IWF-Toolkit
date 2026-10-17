"""Statistical steganalysis, visual transforms and JPEG DCT domain analysis.

Grouped into four parts:

    * statistical detectors - entropy, histograms, chi-square (Westfeld),
      RS analysis (Fridrich) and sample pair analysis, all reported per channel
    * visual transforms    - error level analysis, median residual, difference
      map, LSB autocorrelation and LSB density
    * palette analysis     - anomalies in indexed (PNG/GIF) images
    * JPEG DCT domain      - quantisation tables, a baseline entropy decoder
      that exposes quantised coefficients, a JSteg extractor and the
      coefficient-histogram test

The detector rates are first order estimates: they are monotone in the amount of
LSB replacement, equal 0 for untouched data and approach 1 for a fully random
LSB plane. They are intended as triage evidence, not as a proof.
"""

from __future__ import annotations

import io
import math
import os
import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _select_plane(arr: np.ndarray, channel=None) -> np.ndarray:
    """Return a 2D plane. A 3D array defaults to channel 0 when channel is None."""
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3:
        if channel is None:
            channel = 0
        if channel < 0 or channel >= arr.shape[2]:
            channel = 0
        return arr[..., channel]
    raise ValueError("unsupported array shape")


def _channel_names(arr: np.ndarray):
    if arr.ndim == 2:
        return ["Gray"]
    n = arr.shape[2]
    return ["R", "G", "B", "A"][:n] if n == 4 else ["R", "G", "B"][:n]


def shannon_entropy(values) -> float:
    """Shannon entropy in bits per sample over the 0..255 range (or wider)."""
    if isinstance(values, (bytes, bytearray)):
        flat = np.frombuffer(bytes(values), dtype=np.uint8)
    else:
        flat = np.asarray(values).ravel()
    if flat.size == 0:
        return 0.0
    counts = np.bincount(flat.astype(np.int64))
    counts = counts[counts > 0]
    p = counts / flat.size
    return float(-(p * np.log2(p)).sum())


def channel_histogram(arr: np.ndarray, channel=None) -> np.ndarray:
    """256 bin histogram (wider for >8 bit data, capped at 256 bins)."""
    plane = _select_plane(arr, channel)
    flat = plane.ravel().astype(np.int64)
    counts = np.bincount(flat, minlength=256)
    if counts.size > 256:  # 16 bit data: fold down to a displayable histogram
        counts = counts[:256]
    return counts


# ---------------------------------------------------------------------------
# Distribution helpers for the chi-square test
# ---------------------------------------------------------------------------
def _lower_gamma_series(a: float, x: float) -> float:
    gln = math.lgamma(a)
    if x <= 0:
        return 0.0
    ap, total, delta = a, 1.0 / a, 1.0 / a
    for _ in range(1000):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-13:
            break
    return total * math.exp(-x + a * math.log(x) - gln)


def _upper_gamma_cf(a: float, x: float) -> float:
    gln = math.lgamma(a)
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-13:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def _chi2_sf(x: float, df: int) -> float:
    """Survival function P(X > x) of a chi-square variate with df degrees."""
    if df <= 0:
        return 1.0
    if x <= 0:
        return 1.0
    a = df / 2.0
    xx = x / 2.0
    if xx < a + 1.0:
        return max(0.0, min(1.0, 1.0 - _lower_gamma_series(a, xx)))
    return max(0.0, min(1.0, _upper_gamma_cf(a, xx)))


def _pair_chi_square(flat: np.ndarray):
    """Westfeld chi-square over the (2i, 2i+1) value pairs of one plane."""
    counts = np.bincount(flat.astype(np.int64), minlength=256)[:256].astype(np.float64)
    even = counts[0:256:2]
    odd = counts[1:256:2]
    pair_sum = even + odd
    used = pair_sum > 0
    df = int(used.sum())
    if df == 0:
        return {"stat": 0.0, "df": 0, "p": 1.0, "pairs": 0}
    denom = pair_sum[used]
    diff = (even - odd)[used]
    stat = float(((diff * diff) / denom).sum())
    return {"stat": stat, "df": df, "p": _chi2_sf(stat, df), "pairs": df}


def chi_square_attack(arr: np.ndarray, channel=None, window: int = 1024, step: int = 512):
    """Westfeld chi-square LSB replacement test.

    A high p (>= 0.5) means the even/odd value pairs are close to equally
    populated, which is what LSB replacement produces. The result is only
    meaningful when the value histogram is not already flat: a flat histogram is
    reported as inconclusive.
    """
    plane = _select_plane(arr, channel)
    flat = plane.ravel()
    overall = _pair_chi_square(flat)
    ps = []
    if flat.size >= window:
        limit = 0
        for start in range(0, flat.size - window + 1, step):
            ps.append(round(_pair_chi_square(flat[start : start + window])["p"], 4))
            limit += 1
            if limit >= 4096:
                break
    value_entropy = shannon_entropy(plane)
    inconclusive = value_entropy > 7.9
    p = overall["p"]
    if inconclusive:
        verdict = "chi-square inconclusive (value histogram is already flat)"
    elif p >= 0.95:
        verdict = "strong evidence of LSB replacement"
    elif p >= 0.5:
        verdict = "possible LSB replacement"
    else:
        verdict = "no evidence of LSB replacement"
    return {
        "p": p,
        "statistic": overall["stat"],
        "df": overall["df"],
        "pairs": overall["pairs"],
        "p_windows": ps,
        "value_entropy": value_entropy,
        "inconclusive": bool(inconclusive),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# RS analysis (Fridrich, Goljan, Du)
# ---------------------------------------------------------------------------
_RS_MASKS = ([0, 1, 1, 0], [1, 0, 0, 1])


def _rs_groups(plane: np.ndarray):
    h, w = plane.shape
    w4 = (w // 4) * 4
    if w4 < 4 or h < 1:
        return None
    return plane[:, :w4].reshape(h, w4 // 4, 4).astype(np.int32)


def _rs_variation(g: np.ndarray) -> np.ndarray:
    return (
        np.abs(g[..., 1] - g[..., 0])
        + np.abs(g[..., 2] - g[..., 1])
        + np.abs(g[..., 3] - g[..., 2])
    )


def _rs_flip(g: np.ndarray, mask, positive: bool) -> np.ndarray:
    out = g.copy()
    for pos in range(4):
        if not mask[pos]:
            continue
        v = out[..., pos]
        if positive:
            out[..., pos] = v ^ 1
        else:
            out[..., pos] = np.where(v == 0, 0, np.where((v & 1) == 1, v + 1, v - 1))
    return out


def _rs_difference(plane: np.ndarray, mask):
    groups = _rs_groups(plane)
    if groups is None:
        return None
    f = _rs_variation(groups)
    regular = singular = 0
    for positive in (True, False):
        flipped = _rs_variation(_rs_flip(groups, mask, positive))
        if positive:
            regular = int((flipped > f).sum())
            singular = int((flipped < f).sum())
        else:
            neg_regular = int((flipped > f).sum())
            neg_singular = int((flipped < f).sum())
    n = f.size
    if n == 0:
        return None
    return {
        "n": n,
        "R": regular,
        "S": singular,
        "R_neg": neg_regular,
        "S_neg": neg_singular,
        "B": (regular - singular) / n,
        "B_neg": (neg_regular - neg_singular) / n,
    }


def rs_analysis(arr: np.ndarray, channel=None, mask: int = 0):
    """RS analysis with a first order estimate of the LSB replacement rate.

    Two traces are measured on the same image: ``B`` for the positive mask and
    ``B_neg`` for the negative mask. For untouched data the two are almost equal;
    as LSB replacement grows they diverge, because replacement destroys the
    correlation between the LSB plane and the higher bits. The normalised
    asymmetry ``(B_neg - B) / (|B_neg| + |B|)`` is monotone in the replacement
    rate and is used as the estimate.
    """
    plane = _select_plane(arr, channel).astype(np.uint8)
    if min(plane.shape) < 1 or plane.size < 64:
        return _unsuitable_rs("image too small for RS analysis")
    chosen = _RS_MASKS[mask % len(_RS_MASKS)]
    base = _rs_difference(plane, chosen)
    if base is None:
        return _unsuitable_rs("flat or degenerate image: no discrimination signal")
    n = base["n"]
    if (base["R"] + base["S"]) < 0.01 * n or (base["R_neg"] + base["S_neg"]) < 0.01 * n:
        return _unsuitable_rs("synthetic or flat image: groups do not discriminate")
    b, b_neg = base["B"], base["B_neg"]
    scale = abs(b) + abs(b_neg)
    if scale < 0.004:
        rate = 1.0
    else:
        rate = (b_neg - b) / scale
    rate = float(min(1.0, max(0.0, rate)))
    if rate >= 0.6:
        verdict = "strong evidence of LSB replacement"
    elif rate >= 0.25:
        verdict = "possible LSB replacement"
    else:
        verdict = "no evidence of LSB replacement"
    return {
        "mask": mask,
        "groups": n,
        "R": base["R"],
        "S": base["S"],
        "R_neg": base["R_neg"],
        "S_neg": base["S_neg"],
        "rs": b,
        "rs_neg": b_neg,
        "estimated_rate": rate,
        "verdict": verdict,
    }


def _unsuitable_rs(reason: str):
    return {
        "mask": 0, "groups": 0, "R": 0, "S": 0, "R_neg": 0, "S_neg": 0,
        "rs": 0.0, "rs_neg": 0.0, "estimated_rate": 0.0,
        "verdict": f"input unsuitable for RS analysis ({reason})",
    }


# ---------------------------------------------------------------------------
# Sample pair analysis
# ---------------------------------------------------------------------------
def _spa_trace(plane: np.ndarray):
    u = plane[:, :-1].ravel().astype(np.int32)
    v = plane[:, 1:].ravel().astype(np.int32)
    if u.size == 0:
        return None
    u_even = (u & 1) == 0
    cm = int(np.count_nonzero((u_even & (v < u)) | (~u_even & (v > u))))
    cmp = int(np.count_nonzero((u_even & (v > u)) | (~u_even & (v < u))))
    n = u.size
    return {"n": n, "Cm": cm, "Cmp": cmp, "B": (cm - cmp) / n}


def spa_analysis(arr: np.ndarray, channel=None):
    """Sample pair analysis: trace counts and a first order rate estimate.

    The trace is measured on the image and on its LSB-flipped control. The
    flipped control sits at the far end of the parity trace, so the normalised
    position of the measured trace between the two points estimates the rate.
    """
    plane = _select_plane(arr, channel).astype(np.uint8)
    if min(plane.shape) < 1 or plane.size < 64:
        return _unsuitable_spa("image too small for sample pair analysis")
    base = _spa_trace(plane)
    flipped = _spa_trace(plane ^ 1)
    if base is None or flipped is None:
        return _unsuitable_spa("degenerate image")
    b, b_flip = base["B"], flipped["B"]
    scale = abs(b) + abs(b_flip)
    if scale < 1e-4:
        return _unsuitable_spa("flat image: the parity trace carries no signal")
    rate = 1.0 - (b - b_flip) / scale
    rate = float(min(1.0, max(0.0, rate)))
    if rate >= 0.6:
        verdict = "strong evidence of LSB replacement"
    elif rate >= 0.25:
        verdict = "possible LSB replacement"
    else:
        verdict = "no evidence of LSB replacement"
    return {
        "pairs": base["n"],
        "Cm": base["Cm"],
        "Cmp": base["Cmp"],
        "trace": b,
        "trace_flipped": b_flip,
        "rate": rate,
        "verdict": verdict,
    }


def _unsuitable_spa(reason: str):
    return {
        "pairs": 0, "Cm": 0, "Cmp": 0, "trace": 0.0, "trace_flipped": 0.0,
        "rate": 0.0, "verdict": f"input unsuitable for sample pair analysis ({reason})",
    }


def sample_pair_analysis(arr: np.ndarray, channel=None):
    """Alias with the full pair breakdown included."""
    result = spa_analysis(arr, channel)
    result["method"] = "sample pair analysis (horizontal neighbours)"
    return result


# ---------------------------------------------------------------------------
# Visual transforms
# ---------------------------------------------------------------------------
def ela_image(path: str, quality: int = 90, scale: int = 15) -> np.ndarray:
    """Error level analysis: difference against a JPEG recompression."""
    with Image.open(path) as handle:
        original = handle.convert("RGB")
    buffer = io.BytesIO()
    original.save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    with Image.open(buffer) as recompressed:
        recompressed = recompressed.convert("RGB")
        diff = np.abs(
            np.asarray(original, dtype=np.int16) - np.asarray(recompressed, dtype=np.int16)
        )
    return np.clip(diff * scale, 0, 255).astype(np.uint8)


def noise_residual(arr: np.ndarray, channel=None) -> np.ndarray:
    """Median filtered residual, amplified: highlights LSB noise structure."""
    plane = _select_plane(arr, channel)
    display = plane if plane.dtype == np.uint8 else (plane >> 8).astype(np.uint8)
    median = np.asarray(
        Image.fromarray(display, "L").filter(ImageFilter.MedianFilter(5)), dtype=np.int16
    )
    residual = np.abs(display.astype(np.int16) - median)
    return np.clip(residual * 6, 0, 255).astype(np.uint8)


def difference_map(arr: np.ndarray, channel=None) -> np.ndarray:
    """Horizontal and vertical neighbour differences, amplified."""
    plane = _select_plane(arr, channel).astype(np.int16)
    dx = np.zeros_like(plane)
    dy = np.zeros_like(plane)
    dx[:, 1:] = np.abs(np.diff(plane, axis=1))
    dy[1:, :] = np.abs(np.diff(plane, axis=0))
    return np.clip(np.maximum(dx, dy) * 4, 0, 255).astype(np.uint8)


def autocorrelation_map(arr: np.ndarray, channel=None) -> np.ndarray:
    """Where adjacent pixels share the same LSB (white = equal)."""
    plane = _select_plane(arr, channel)
    lsb = (plane & 1).astype(np.uint8)
    equal = np.zeros(plane.shape, dtype=np.uint8)
    if plane.shape[1] > 1:
        equal[:, :-1] |= (lsb[:, :-1] == lsb[:, 1:]).astype(np.uint8)
    if plane.shape[0] > 1:
        equal[:-1, :] |= (lsb[:-1, :] == lsb[1:, :]).astype(np.uint8)
    return (equal * 255).astype(np.uint8)


def lsb_density_map(arr: np.ndarray, channel=None, block: int = 16) -> np.ndarray:
    """Per 16x16 block fraction of set LSBs, upsampled to the full frame."""
    plane = _select_plane(arr, channel)
    lsb = (plane & 1).astype(np.float32)
    h, w = lsb.shape
    bh, bw = max(1, h // block), max(1, w // block)
    hh, ww = bh * block, bw * block
    density = lsb[:hh, :ww].reshape(bh, block, bw, block).mean(axis=(1, 3))
    image = Image.fromarray((density * 255).astype(np.uint8), "L").resize(
        (w, h), Image.NEAREST
    )
    return np.asarray(image, dtype=np.float32) / 255.0


def bit_plane_entropy(arr: np.ndarray, channel=None) -> dict:
    """Entropy of each bit plane of a channel, keyed by bit index."""
    plane = _select_plane(arr, channel)
    bits = 8 if plane.dtype == np.uint8 else 16
    out = {}
    for bit in range(bits):
        out[bit] = shannon_entropy((plane >> bit) & 1)
    return out


# ---------------------------------------------------------------------------
# Text based steganalysis helpers
# ---------------------------------------------------------------------------
ZERO_WIDTH = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE / BOM",
    "\u180e": "MONGOLIAN VOWEL SEPARATOR",
}


def zero_width_report(text: str) -> dict:
    """Locate zero width characters, a classic hidden message carrier."""
    hits = [(i, ZERO_WIDTH[c], c) for i, c in enumerate(text) if c in ZERO_WIDTH]
    findings = []
    if hits:
        names = sorted({name for _i, name, _c in hits})
        findings.append(
            f"{len(hits)} zero width character(s) found: " + ", ".join(names)
        )
    return {
        "count": len(hits),
        "positions": [i for i, _n, _c in hits],
        "characters": [n for _i, n, _c in hits],
        "findings": findings,
    }


def whitespace_report(text: str) -> dict:
    """Trailing whitespace and unusual whitespace usage, a simple payload channel."""
    lines = text.split("\n")
    trailing = [i for i, line in enumerate(lines) if line != line.rstrip() and line.strip()]
    unusual = sorted({c for c in text if c in "\u00a0\u2000\u2001\u2002\u2003\u2028\u2029\u3000"})
    findings = []
    if trailing:
        findings.append(f"{len(trailing)} line(s) end with trailing whitespace")
    if unusual:
        findings.append("unusual whitespace characters present: " + ", ".join(f"U+{ord(c):04X}" for c in unusual))
    return {
        "lines": len(lines),
        "trailing_whitespace_lines": len(trailing),
        "unusual_whitespace": [f"U+{ord(c):04X}" for c in unusual],
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Palette analysis
# ---------------------------------------------------------------------------
def palette_analysis(image) -> dict:
    """Anomalies in an indexed image: unused entries, duplicates, index patterns."""
    result = {"indexed": False, "findings": []}
    if getattr(image, "mode", None) != "P":
        result["findings"].append("image is not palette indexed: no palette channel")
        result["verdict"] = "not applicable"
        return result
    index = np.asarray(image, dtype=np.uint8)
    palette = image.getpalette() or []
    entries = [(palette[i], palette[i + 1], palette[i + 2]) for i in range(0, len(palette) - 2, 3)]
    used = np.unique(index)
    result.update(
        {
            "indexed": True,
            "palette_entries": len(entries),
            "used_entries": int(used.size),
            "index_entropy": shannon_entropy(index),
            "index_lsb_ones": float((index & 1).mean()),
        }
    )
    unused = [i for i in range(len(entries)) if i not in set(used.tolist())]
    result["unused_entries"] = len(unused)
    if unused:
        result["findings"].append(
            f"{len(unused)} palette entries are never referenced: possible hidden payload slot"
        )
    seen = {}
    duplicates = 0
    for index_pos, colour in enumerate(entries):
        if colour in seen:
            duplicates += 1
        else:
            seen[colour] = index_pos
    result["duplicate_colours"] = duplicates
    if duplicates:
        result["findings"].append(f"{duplicates} duplicate palette colours")
    if len(used) < len(entries):
        # data hidden in the index plane is common in GIF/PNG8 challenges
        result["findings"].append(
            f"index plane uses values 0..{int(used.max())}; entries above that are unused "
            "and can carry steganographic data"
        )
    result["findings"].append(
        f"index plane entropy {result['index_entropy']:.4f} bit/sample, "
        f"LSB ones ratio {result['index_lsb_ones']:.4f}"
    )
    if result["index_lsb_ones"] > 0.55 or result["index_lsb_ones"] < 0.45:
        result["verdict"] = "palette index plane is not LSB balanced: review the index channel"
    else:
        result["verdict"] = "palette index plane looks balanced"
    return result


# ---------------------------------------------------------------------------
# JPEG DCT domain
# ---------------------------------------------------------------------------
ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
]

#: JPEG Annex K standard quantisation tables (natural, row major order).
STD_LUMA_QUANT = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]
STD_CHROMA_QUANT = [
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
]

_COMPONENT_NAMES = {1: "Y", 2: "Cb", 3: "Cr"}


class _JPEGError(Exception):
    pass


class _HuffmanTable:
    __slots__ = ("max_len", "mask", "fast")

    def __init__(self, counts, symbols):
        codes = {}
        code = 0
        index = 0
        for length in range(1, 17):
            for _ in range(counts[length - 1]):
                if index >= len(symbols):
                    break
                codes[(length, code)] = symbols[index]
                index += 1
                code += 1
            code <<= 1
        self.max_len = max((l for l in range(1, 17) if counts[l - 1]), default=0)
        if self.max_len == 0:
            self.max_len = 1
        self.mask = (1 << self.max_len) - 1
        size = 1 << self.max_len
        # packed value: symbol << 5 | length, length 0 means "invalid prefix"
        self.fast = [0] * size
        for (length, code), symbol in codes.items():
            shift = self.max_len - length
            start = code << shift
            span = 1 << shift
            packed = (symbol << 5) | length
            for i in range(start, start + span):
                self.fast[i] = packed

    def decode(self, reader: "_EntropyReader") -> int:
        if reader.bit_cnt < self.max_len:
            reader.fill()
        if reader.bit_cnt >= self.max_len:
            index = (reader.bit_buf >> (reader.bit_cnt - self.max_len)) & self.mask
            packed = self.fast[index]
            length = packed & 31
            if length:
                reader.bit_cnt -= length
                return packed >> 5
        # fallback: consume one bit at a time (should not happen for valid tables)
        code = 0
        for length in range(1, self.max_len + 1):
            code = (code << 1) | reader.get_bits(1)
            index = (code << (self.max_len - length)) & self.mask
            packed = self.fast[index]
            if (packed & 31) == length:
                return packed >> 5
        raise _JPEGError("invalid Huffman code")


class _EntropyReader:
    """Bit reader over entropy coded JPEG data with 0xFF00 destuffing."""

    __slots__ = ("data", "pos", "bit_buf", "bit_cnt")

    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos
        self.bit_buf = 0
        self.bit_cnt = 0

    def _next_byte(self):
        data, n = self.data, len(self.data)
        while self.pos < n:
            byte = data[self.pos]
            self.pos += 1
            if byte != 0xFF:
                return byte
            if self.pos >= n:
                return None
            nxt = data[self.pos]
            if nxt == 0x00:
                self.pos += 1
                return 0xFF
            if nxt == 0xFF:
                continue
            self.pos -= 1  # leave the marker for the caller
            return None
        return None

    def fill(self) -> bool:
        while self.bit_cnt <= 24:
            byte = self._next_byte()
            if byte is None:
                return False
            self.bit_buf = ((self.bit_buf << 8) | byte) & 0xFFFFFFFF
            self.bit_cnt += 8
        return True

    def get_bits(self, n: int) -> int:
        if n == 0:
            return 0
        if self.bit_cnt < n:
            self.fill()
            if self.bit_cnt < n:
                raise _JPEGError("unexpected end of entropy coded data")
        self.bit_cnt -= n
        return (self.bit_buf >> self.bit_cnt) & ((1 << n) - 1)


def _extend(value: int, size: int) -> int:
    if size == 0:
        return 0
    if value < (1 << (size - 1)):
        return value - (1 << size) + 1
    return value


def parse_quant_tables(data: bytes):
    """All DQT tables in a JPEG file as {id: {"precision","zigzag","natural"}}."""
    tables = {}
    order = []
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            pos += 2
            if marker == 0xD9:
                break
            continue
        if pos + 4 > len(data):
            break
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if marker == 0xDB:
            p = pos + 4
            end = pos + 2 + length
            while p < end:
                pq = data[p] >> 4
                tq = data[p] & 0x0F
                p += 1
                if pq == 0:
                    values = list(data[p : p + 64])
                    p += 64
                else:
                    values = list(struct.unpack_from(">64H", data, p))
                    p += 128
                natural = [0] * 64
                for k, v in enumerate(values):
                    natural[ZIGZAG[k]] = v
                tables[tq] = {
                    "precision": 8 if pq == 0 else 16,
                    "zigzag": values,
                    "natural": natural,
                }
                order.append(tq)
        if marker == 0xDA:
            break
        pos += 2 + length
    return tables, order


def quantization_tables(path: str) -> dict:
    """Report the JPEG quantisation tables and flag non standard ones."""
    with open(path, "rb") as handle:
        data = handle.read()
    if not data.startswith(b"\xff\xd8"):
        return {"tables": {}, "count": 0, "sums": {}, "findings": ["not a JPEG file"]}
    tables, order = parse_quant_tables(data)
    findings = []
    sums = {}
    for tid, table in tables.items():
        sums[str(tid)] = int(sum(table["zigzag"]))
        zeros = sum(1 for v in table["zigzag"] if v == 0)
        reference = STD_LUMA_QUANT if tid == 0 else STD_CHROMA_QUANT if tid == 1 else None
        if zeros:
            findings.append(f"quantisation table {tid}: {zeros} zero coefficient(s)")
        if reference is not None and table["natural"] != reference:
            differences = sum(
                1 for a, b in zip(table["natural"], reference) if a != b
            )
            findings.append(
                f"quantisation table {tid} differs from the standard table in "
                f"{differences} of 64 positions (custom quality or edited quantisation)"
            )
    if len(order) > len(set(order)):
        findings.append("a quantisation table is defined more than once")
    if not tables:
        findings.append("no quantisation table found (not a baseline JPEG?)")
    return {
        "tables": {
            str(tid): {"precision": t["precision"], "natural": t["natural"], "zigzag": t["zigzag"]}
            for tid, t in tables.items()
        },
        "count": len(tables),
        "order": order,
        "sums": sums,
        "findings": findings,
    }


def _decode_baseline(data: bytes):
    """Decode a baseline sequential JPEG into quantised DCT coefficients."""
    if not data.startswith(b"\xff\xd8"):
        raise _JPEGError("not a JPEG file")
    quant = {}
    huff_dc = {}
    huff_ac = {}
    frame = None
    restart_interval = 0
    scans = []
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            pos += 2
            if marker == 0xD9:
                break
            continue
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if marker == 0xDB:
            p, end = pos + 4, pos + 2 + length
            while p < end:
                pq, tq = data[p] >> 4, data[p] & 0x0F
                p += 1
                if pq == 0:
                    raw = np.frombuffer(data, np.uint8, 64, p).astype(np.int32)
                    p += 64
                else:
                    raw = np.frombuffer(data, ">u2", 64, p).astype(np.int32)
                    p += 128
                natural = np.zeros(64, dtype=np.int32)
                for k in range(64):
                    natural[ZIGZAG[k]] = raw[k]
                quant[tq] = natural
        elif marker == 0xC4:
            p, end = pos + 4, pos + 2 + length
            while p < end:
                tc, th = data[p] >> 4, data[p] & 0x0F
                counts = list(data[p + 1 : p + 17])
                total = sum(counts)
                symbols = list(data[p + 17 : p + 17 + total])
                table = _HuffmanTable(counts, symbols)
                if tc == 0:
                    huff_dc[th] = table
                else:
                    huff_ac[th] = table
                p += 17 + total
        elif marker in (0xC0, 0xC1):
            precision = data[pos + 4]
            height = struct.unpack_from(">H", data, pos + 5)[0]
            width = struct.unpack_from(">H", data, pos + 7)[0]
            ncomp = data[pos + 9]
            comps = []
            p = pos + 10
            for _ in range(ncomp):
                cid = data[p]
                hv = data[p + 1]
                tq = data[p + 2]
                comps.append({"id": cid, "h": hv >> 4, "v": hv & 0x0F, "tq": tq})
                p += 3
            frame = {"precision": precision, "height": height, "width": width, "components": comps}
        elif marker == 0xC2:
            raise _JPEGError("progressive JPEG is not supported by the built in decoder")
        elif marker in (0xC9, 0xCA, 0xCB):
            raise _JPEGError("arithmetic coded JPEG is not supported")
        elif marker == 0xDD:
            restart_interval = struct.unpack_from(">H", data, pos + 4)[0]
        elif marker == 0xDA:
            ncomp = data[pos + 4]
            comp_sel = []
            p = pos + 5
            for _ in range(ncomp):
                cid = data[p]
                td = data[p + 1] >> 4
                ta = data[p + 1] & 0x0F
                comp_sel.append((cid, td, ta))
                p += 2
            scans.append({"components": comp_sel, "data_start": pos + 2 + length})
        pos += 2 + length
        if marker == 0xDA and frame is not None:
            break

    if frame is None:
        raise _JPEGError("no frame header found")
    if not scans:
        raise _JPEGError("no scan found")
    if frame["precision"] != 8:
        raise _JPEGError(f"{frame['precision']} bit samples are not supported")

    width, height = frame["width"], frame["height"]
    comps = frame["components"]
    h_max = max(c["h"] for c in comps)
    v_max = max(c["v"] for c in comps)
    mcu_w, mcu_h = 8 * h_max, 8 * v_max
    mcu_cols = (width + mcu_w - 1) // mcu_w
    mcu_rows = (height + mcu_h - 1) // mcu_h
    total_mcus = mcu_cols * mcu_rows

    by_id = {c["id"]: c for c in comps}
    for c in comps:
        c["blocks"] = mcu_cols * mcu_rows * c["h"] * c["v"]
        c["coeffs"] = np.zeros((c["blocks"], 64), dtype=np.int16)
        c["written"] = 0

    scan = scans[0]
    reader = _EntropyReader(data, scan["data_start"])
    scan_ids = [cid for cid, _td, _ta in scan["components"]]
    table_ids = {cid: (td, ta) for cid, td, ta in scan["components"]}
    pred = {cid: 0 for cid in scan_ids}

    def decode_block(cid):
        c = by_id[cid]
        td, ta = table_ids[cid]
        block = np.zeros(64, dtype=np.int32)
        t = huff_dc.get(td)
        if t is None:
            raise _JPEGError(f"missing DC Huffman table {td}")
        size = t.decode(reader)
        if size:
            diff = _extend(reader.get_bits(size), size)
        else:
            diff = 0
        pred[cid] += diff
        block[0] = pred[cid]
        ac_table = huff_ac.get(ta)
        if ac_table is None:
            raise _JPEGError(f"missing AC Huffman table {ta}")
        k = 1
        while k < 64:
            rs = ac_table.decode(reader)
            run, size = rs >> 4, rs & 15
            if size == 0:
                if run == 15:
                    k += 16
                    continue
                break
            k += run
            if k > 63:
                break
            block[ZIGZAG[k]] = _extend(reader.get_bits(size), size)
            k += 1
        c["coeffs"][c["written"]] = block.astype(np.int16)
        c["written"] += 1

    mcu_count = 0
    for _row in range(mcu_rows):
        for _col in range(mcu_cols):
            for cid in scan_ids:
                c = by_id[cid]
                for _v in range(c["v"]):
                    for _h in range(c["h"]):
                        decode_block(cid)
            mcu_count += 1
            if restart_interval and mcu_count % restart_interval == 0 and mcu_count < total_mcus:
                reader.bit_buf = 0
                reader.bit_cnt = 0
                for cid in scan_ids:
                    pred[cid] = 0
                while reader.pos + 1 < len(data):
                    if data[reader.pos] == 0xFF and 0xD0 <= data[reader.pos + 1] <= 0xD7:
                        reader.pos += 2
                        break
                    reader.pos += 1

    components = {}
    for c in comps:
        name = _COMPONENT_NAMES.get(c["id"], f"C{c['id']}")
        flat = c["coeffs"][: c["written"]]
        # coeffs are stored flat in natural (row major) order already
        natural = flat.reshape(c["written"], 8, 8)
        components[name] = {
            "id": c["id"],
            "h": c["h"],
            "v": c["v"],
            "quant_table": c["tq"],
            "blocks": c["written"],
            "coeffs": natural,
        }
    return {
        "width": width,
        "height": height,
        "n_components": len(comps),
        "mcu": (mcu_cols, mcu_rows),
        "quant": {str(k): v.tolist() for k, v in quant.items()},
        "components": components,
    }


def dct_coefficients(path: str) -> dict:
    """Quantised DCT coefficients of a baseline JPEG, with per component blocks."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return {"supported": False, "reason": str(exc), "components": {}, "quant": {}}
    if not data.startswith(b"\xff\xd8"):
        return {"supported": False, "reason": "not a JPEG file", "components": {}, "quant": {}}
    if len(data) > 64 * 1024 * 1024:
        return {"supported": False, "reason": "file larger than 64 MiB", "components": {}, "quant": {}}
    try:
        result = _decode_baseline(data)
    except _JPEGError as exc:
        return {
            "supported": False,
            "reason": str(exc),
            "components": {},
            "quant": {},
            "findings": [f"built in DCT decoder did not run: {exc}"],
        }
    except Exception as exc:  # defensive: a malformed stream must not raise
        return {
            "supported": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "components": {},
            "quant": {},
        }
    result["supported"] = True
    result["reason"] = ""
    return result


def _iter_ac_coefficients(result):
    """Yield every non DC quantised coefficient, in zigzag order per block."""
    for component in result["components"].values():
        coeffs = component["coeffs"]
        n = coeffs.shape[0]
        # reorder the 8x8 block into zigzag order; column 0 is then the DC term
        flat = coeffs.reshape(n, 64)[:, ZIGZAG]
        for k in range(1, 64):
            yield flat[:, k]


def jsteg_extract(path: str, max_bytes: int = 0) -> bytes:
    """JSteg extractor: LSB of every non zero, non one quantised AC coefficient."""
    result = dct_coefficients(path)
    if not result.get("supported"):
        return b""
    bits = []
    for column in _iter_ac_coefficients(result):
        column = column.astype(np.int32)
        mask = (column != 0) & (column != 1)
        if not mask.any():
            continue
        bits.append((column[mask] & 1).astype(np.uint8))
        if max_bytes and sum(b.size for b in bits) >= max_bytes * 8:
            break
    if not bits:
        return b""
    stream = np.concatenate(bits)
    if max_bytes:
        stream = stream[: max_bytes * 8]
    usable = (stream.size // 8) * 8
    return np.packbits(stream[:usable], bitorder="big").tobytes()


def dct_lsb_histogram(path: str) -> dict:
    """Coefficient histogram test for JSteg style LSB embedding in the DCT domain."""
    result = dct_coefficients(path)
    if not result.get("supported"):
        return {
            "supported": False,
            "findings": [f"DCT analysis unavailable: {result.get('reason', 'unknown')}"],
            "histogram": {},
        }
    counts = {-2: 0, -1: 0, 1: 0, 2: 0}
    others = 0
    for column in _iter_ac_coefficients(result):
        column = column.astype(np.int32)
        counts[-2] += int((column == -2).sum())
        counts[-1] += int((column == -1).sum())
        counts[1] += int((column == 1).sum())
        counts[2] += int((column == 2).sum())
        others += int(column.size) - int(
            ((column >= -2) & (column <= 2)).sum()
        )
    ones = counts[1] + counts[-1]
    twos = counts[2] + counts[-2]
    findings = []
    ratio = (ones / twos) if twos else 0.0
    if twos:
        findings.append(
            f"AC coefficient counts: +/-1 = {ones}, +/-2 = {twos}, ratio {ratio:.3f}"
        )
        if ratio < 0.5:
            findings.append(
                "coefficients of magnitude 1 are far rarer than magnitude 2: "
                "a strong JSteg style signature"
            )
        elif ratio < 0.85:
            findings.append(
                "coefficients of magnitude 1 are rarer than magnitude 2: "
                "consistent with JSteg style LSB embedding"
            )
        else:
            findings.append("coefficient magnitudes follow the expected decay: no JSteg signature")
    else:
        findings.append("too few small AC coefficients to run the JSteg histogram test")
    verdict = (
        "strong evidence of DCT LSB embedding"
        if twos and ratio < 0.5
        else "possible DCT LSB embedding"
        if twos and ratio < 0.85
        else "no DCT LSB signature"
    )
    return {
        "supported": True,
        "histogram": {str(k): v for k, v in counts.items()},
        "other": others,
        "ones": ones,
        "twos": twos,
        "ones_ratio": ratio,
        "verdict": verdict,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def _verdict_score(chi: dict, rs: dict, spa: dict) -> float:
    score = 0.0
    if not chi.get("inconclusive"):
        p = chi.get("p", 0.0)
        if p >= 0.95:
            score += 1.0
        elif p >= 0.5:
            score += 0.5
    for detector in (rs, spa):
        rate = detector.get("estimated_rate", detector.get("rate", 0.0))
        if rate >= 0.6:
            score += 1.0
        elif rate >= 0.25:
            score += 0.5
    return score


def steganalysis_summary(arr: np.ndarray) -> dict:
    """Per channel statistical summary plus an aggregated verdict."""
    names = _channel_names(arr)
    channels = {}
    findings = []
    strongest = "no evidence of LSB replacement"
    for index, name in enumerate(names):
        channel_arg = index if arr.ndim == 3 else None
        plane = _select_plane(arr, index)
        entropy = shannon_entropy(plane)
        lsb_ones = float((plane & 1).mean())
        chi = chi_square_attack(arr, channel_arg)
        rs = rs_analysis(arr, channel_arg)
        spa = spa_analysis(arr, channel_arg)
        score = _verdict_score(chi, rs, spa)
        if score >= 2.0:
            verdict = "strong evidence of LSB replacement"
        elif score >= 1.0:
            verdict = "possible LSB replacement"
        else:
            verdict = "no evidence of LSB replacement"
        if verdict == "strong evidence of LSB replacement":
            strongest = verdict
        elif verdict == "possible LSB replacement" and strongest == "no evidence of LSB replacement":
            strongest = verdict
        channels[name] = {
            "entropy": entropy,
            "lsb_ones_ratio": lsb_ones,
            "chi_square": chi,
            "rs": rs,
            "spa": spa,
            "score": score,
            "verdict": verdict,
        }
        if verdict != "no evidence of LSB replacement":
            findings.append(
                f"channel {name}: {verdict} "
                f"(chi-square p={chi.get('p', 0):.3f}, RS={rs.get('estimated_rate', 0):.3f}, "
                f"SPA={spa.get('rate', 0):.3f})"
            )
        elif rs.get("estimated_rate", 0) >= 0.1 or spa.get("rate", 0) >= 0.1:
            findings.append(
                f"channel {name}: weak LSB asymmetry "
                f"(RS={rs.get('estimated_rate', 0):.3f}, SPA={spa.get('rate', 0):.3f})"
            )
    if not findings:
        findings.append("no statistical LSB anomaly detected in any channel")
    return {"channels": channels, "findings": findings, "verdict": strongest}


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile

    failures = 0

    def check(condition, label):
        nonlocal failures
        if not condition:
            failures += 1
            print(f"FAIL: {label}")

    rng = np.random.default_rng(20260101)

    check(abs(shannon_entropy(np.zeros((32, 32), np.uint8))) < 1e-9, "entropy of constant is zero")
    hist = channel_histogram(np.full((8, 8), 7, np.uint8))
    check(int(hist.sum()) == 64 and hist[7] == 64, "channel histogram")

    # Chi-square: a deliberately asymmetric value pair must score low, a
    # balanced pair must score high.
    asymmetric = np.full((64, 64), 100, np.uint8)
    asymmetric.ravel()[:1024] = 101
    chi_low = chi_square_attack(asymmetric)
    check(chi_low["p"] < 0.05, "chi-square low p on asymmetric pairs")

    balanced = np.full((64, 64), 100, np.uint8)
    balanced.ravel()[:2048] = 101
    chi_high = chi_square_attack(balanced)
    check(chi_high["p"] > 0.95, "chi-square high p on balanced pairs")

    for detector in (
        rs_analysis(rng.integers(0, 256, (64, 64), dtype=np.uint8)),
        spa_analysis(rng.integers(0, 256, (64, 64), dtype=np.uint8)),
    ):
        rate = detector.get("estimated_rate", detector.get("rate"))
        check(0.0 <= rate <= 1.0, "detector rate in [0, 1]")
        check("verdict" in detector, "detector verdict present")

    density = lsb_density_map(np.full((32, 48), 5, np.uint8))
    check(density.shape == (32, 48), "lsb density map shape")
    check(bit_plane_entropy(np.zeros((16, 16), np.uint8))[0] == 0.0, "bit plane entropy zero")

    report = zero_width_report("hello\u200bworld")
    check(report["count"] == 1, "zero width detection")
    check(whitespace_report("a \nb")["trailing_whitespace_lines"] == 1, "whitespace report")

    # Palette analysis on an indexed image.
    palette_image = Image.new("P", (16, 16))
    palette_image.putpalette([i % 256 for i in range(768)])
    palette_image.putdata([i % 7 for i in range(256)])
    pal = palette_analysis(palette_image)
    check(pal["indexed"] is True and pal["used_entries"] == 7, "palette analysis")
    check("findings" in pal, "palette findings")

    # JPEG DCT path.
    jpeg_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
            jpeg_path = handle.name
        image = Image.fromarray(rng.integers(0, 256, (48, 48, 3), dtype=np.uint8), "RGB")
        image.save(jpeg_path, "JPEG", quality=85)
        quant = quantization_tables(jpeg_path)
        check(quant["count"] >= 1, "quantisation tables found")
        dct = dct_coefficients(jpeg_path)
        check(dct["supported"] is True, "baseline DCT decode supported")
        if dct["supported"]:
            total = sum(c["blocks"] for c in dct["components"].values())
            check(total > 0, "DCT blocks decoded")
            check(all(c["coeffs"].shape[1:] == (8, 8) for c in dct["components"].values()),
                  "DCT coefficient block shape")
        stream = jsteg_extract(jpeg_path, max_bytes=256)
        check(isinstance(stream, bytes), "jsteg extractor returns bytes")
        hist = dct_lsb_histogram(jpeg_path)
        check(hist["supported"] is True, "dct lsb histogram supported")
        summary = steganalysis_summary(np.asarray(image))
        check("channels" in summary and "verdict" in summary, "summary structure")
    finally:
        if jpeg_path and os.path.exists(jpeg_path):
            os.unlink(jpeg_path)

    print(f"steganalysis selftest: {failures} failure(s)")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if _selftest() else 0)
