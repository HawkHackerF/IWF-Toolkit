"""Systematic payload discovery across spatial, palette and derived carriers.

The scanner enumerates a well defined space of cheap decoders - every bit plane
of every carrier, single and interleaved, at 1/2/4 bits per sample, at several
bit offsets and both packing orders - extracts the resulting byte streams and
ranks them by text likeness. Hits that match a known flag keyword are reported
immediately through a dedicated signal.

A candidate reported here is always reproducible with
`ifw.imaging.extract_stream` using the parameters stored on the hit, which is
what the interface uses to re-decode and display the payload.
"""

from __future__ import annotations

import heapq
import itertools
import traceback

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from .imaging import (
    FLAG_QUICK_HEAD_RE,
    FLAG_QUICK_RE,
    ImageDoc,
    QUICK_HEAD_BYTES,
    as_text,
    carrier_bitseq,
    find_flags,
    find_strings,
    is_real_flag,
    longest_printable_run,
    pack_bits,
    score_text,
)

#: Scan depths: (max_bytes per stream, bit offsets, bit widths).
SCAN_DEPTHS = {
    "quick": dict(max_bytes=50_000, offsets=range(4), widths=(1, 2)),
    "standard": dict(max_bytes=200_000, offsets=range(8), widths=(1, 2, 4)),
    "deep": dict(max_bytes=1_000_000, offsets=range(8), widths=(1, 2, 4)),
}

DERIVED_MARKERS = ("^", "|", "Max", "Min", "Avg")


def decode_specs(doc: ImageDoc) -> list[tuple]:
    """Carrier combinations to try: singles (incl. palette and derived) plus
    the six interleavings of the three colour channels."""
    specs: list[tuple] = [(c,) for c in doc.available_channels()]
    if doc.is_color and doc.array.shape[2] >= 3:
        for perm in itertools.permutations(doc.channels[:3]):
            specs.append(tuple(perm))
    return specs


def spec_label(spec) -> str:
    """Human readable carrier name."""
    if isinstance(spec, str):
        return spec
    return ",".join(spec)


def method_of(spec, bit: int, width: int, bits: int) -> str:
    """Classify a decode configuration into a named stego method."""
    label = spec_label(spec)
    if "," in label:
        return "Interleaved channels"
    if label == "P":
        return "Palette index"
    if any(marker in label for marker in DERIVED_MARKERS):
        return "Derived carrier"
    if width > 1:
        return f"Multi-bit LSB (b{width})"
    if bit == 0:
        return "LSB (spatial)"
    return f"Bit plane {bit}"


def bit_width_combo(nbits: int):
    """(start bit, width) pairs: every bit at b1, then b2 and b4 windows."""
    for b in range(nbits):
        yield b, 1
    for b in range(0, nbits - 1, 2):
        yield b, 2
    for b in range(0, nbits - 3, 4):
        yield b, 4


class ScanWorker(QObject):
    """Runs the decoder sweep. Call `run()` on a worker thread."""

    progressed = Signal(str)
    progress = Signal(int, str)
    flagFound = Signal(list)
    candidates = Signal(list)
    finished = Signal()

    def __init__(
        self,
        doc: ImageDoc,
        max_bytes: int = 200_000,
        offsets=range(8),
        keep_top: int = 80,
        widths=(1, 2, 4),
        score_threshold: float = 0.45,
    ):
        super().__init__()
        self.doc = doc
        self.max_bytes = max_bytes
        self.offsets = list(offsets)
        self.keep_top = keep_top
        self.widths = tuple(widths)
        self.score_threshold = score_threshold
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _combos(self):
        for bit, width in bit_width_combo(self.doc.bits):
            if width in self.widths:
                yield bit, width

    def run(self):
        doc = self.doc
        heap: list = []
        counter = itertools.count()
        max_off = max(self.offsets) if self.offsets else 0
        need = self.max_bytes * 8 + max_off
        specs = decode_specs(doc)
        combos = list(self._combos())
        total = max(1, len(specs) * len(combos))
        done = 0
        try:
            for spec in specs:
                label = spec_label(spec)
                for bit, width in combos:
                    done += 1
                    if self._cancel:
                        break
                    self.progress.emit(int(done * 100 / total), f"decoding carrier {label}")
                    self.progressed.emit(f"{label} bit {bit} width {width}")
                    # Build the bit sequence once per configuration and try both
                    # byte-grouping orders. Per-sample bits are always taken most
                    # significant first, matching imaging.extract_stream.
                    base = carrier_bitseq(doc, spec, bit, width, True, need)
                    variants = ((True, base), (False, base))
                    for msb, bits_seq in variants:
                        for off in self.offsets:
                            if self._cancel:
                                break
                            segment = bits_seq[off : off + self.max_bytes * 8]
                            if segment.size < 128:
                                continue
                            vals = pack_bits(segment, msb_first=msb)
                            if vals.size < 16:
                                continue
                            raw = vals.tobytes()
                            quick = (
                                FLAG_QUICK_RE.search(raw) is not None
                                or FLAG_QUICK_HEAD_RE.search(raw[:QUICK_HEAD_BYTES]) is not None
                            )
                            if not quick:
                                head = vals[:QUICK_HEAD_BYTES]
                                hratio = np.bincount(head, minlength=256)[32:127].sum() / head.size
                                total_cnt = np.bincount(vals, minlength=256)
                                ratio = total_cnt[32:127].sum() / vals.size
                                if ratio < 0.40 and hratio < 0.40:
                                    continue
                                if (
                                    ratio < 0.60
                                    and hratio < 0.60
                                    and longest_printable_run(head) < 12
                                ):
                                    continue
                            text = as_text(vals)
                            flags = find_flags(text)
                            real = [s for _, s in flags if is_real_flag(s)]
                            if real:
                                self.flagFound.emit([
                                    dict(
                                        method=method_of(spec, bit, width, doc.bits),
                                        spec=tuple(spec), label=label, bit=bit, width=width,
                                        offset=off, msb=msb, match=s, pos=p, kind="FLAG",
                                        score=1.0,
                                    )
                                    for p, s in flags
                                    if is_real_flag(s)
                                ])
                            score, snippet = score_text(text)
                            if snippet:
                                cnt = np.bincount(vals, minlength=256)
                                printable = float(cnt[32:127].sum() / vals.size)
                                ent = _entropy(cnt, vals.size)
                            else:
                                printable, ent = 0.0, 0.0
                            if not real and score >= self.score_threshold and snippet:
                                item = dict(
                                    method=method_of(spec, bit, width, doc.bits),
                                    spec=tuple(spec), label=label, bit=bit, width=width,
                                    offset=off, msb=msb, score=score, snippet=snippet, pos=0,
                                    printable=printable, entropy=ent,
                                    kind="FLAG" if real else ("pattern" if flags else "text"),
                                )
                                heapq.heappush(heap, (score, next(counter), item))
                                if len(heap) > self.keep_top:
                                    heapq.heappop(heap)
                    if self._cancel:
                        break
                if self._cancel:
                    break
        except Exception as exc:  # never let a malformed image kill the sweep
            traceback.print_exc()
            self.progressed.emit(f"scan stopped on error: {exc}")
        finally:
            top = [item for _, _, item in sorted(heap, key=lambda t: -t[0])]
            self.candidates.emit(top)
            self.progress.emit(100 if not self._cancel else 0, "done")
            self.finished.emit()


def _entropy(counts: np.ndarray, size: int) -> float:
    """Shannon entropy in bits/byte from a 256-bin histogram."""
    if size <= 0:
        return 0.0
    p = counts[counts > 0] / size
    return float(-(p * np.log2(p)).sum())


def open_candidate(doc: ImageDoc, hit: dict, max_bytes: int = 200_000) -> str:
    """Re-decode a scanner hit into text (round-trip verification helper)."""
    if hit.get("match"):
        # flag hit: the prescan window is enough, decode a generous slice
        pass
    vals = extract_for_hit(doc, hit, max_bytes=max_bytes)
    return as_text(vals)


def extract_for_hit(doc: ImageDoc, hit: dict, max_bytes: int = 200_000) -> np.ndarray:
    """Decode the byte stream identified by a scanner hit."""
    from .imaging import extract_stream

    return extract_stream(
        doc,
        hit["spec"],
        hit.get("bit", 0),
        hit.get("msb", True),
        hit.get("offset", 0),
        max_bytes,
        bool(hit.get("all_bits", False)),
        hit.get("width", 1),
    )


#: Upper bound on a single extracted payload, so a large carrier cannot make the
#: interface allocate unbounded memory when the user asks to extract everything.
EXTRACT_CAP = 64 * 1024 * 1024


def hit_capacity_bytes(doc: ImageDoc, hit: dict) -> int:
    """Payload capacity in bytes for the exact configuration of a hit."""
    spec = hit.get("spec", ())
    names = spec if isinstance(spec, (list, tuple)) else (spec,)
    samples = sum(doc.sample_count(name) for name in names)
    return (samples * int(hit.get("width", 1))) // 8


def decode_hit(doc: ImageDoc, hit: dict, max_bytes: int | None = None) -> np.ndarray:
    """Decode the full payload of a hit, bounded by the carrier and a safety cap.

    Unlike a fixed-size probe this reads as much as the carrier actually holds,
    so the complete embedded object is recovered, not just its beginning.
    """
    capacity = hit_capacity_bytes(doc, hit)
    limit = capacity if max_bytes is None else min(max_bytes, capacity or max_bytes)
    limit = max(1, min(limit, EXTRACT_CAP))
    return extract_for_hit(doc, hit, max_bytes=limit)


def _mostly_text(raw: bytes, threshold: float = 0.85) -> bool:
    if not raw:
        return False
    sample = raw[:64_000]
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(sample) >= threshold


def hit_filename(index: int, hit: dict, raw: bytes = b"") -> str:
    """A filesystem-safe, descriptive name for one extracted payload."""
    label = str(hit.get("label", "carrier"))
    label = "".join(c if c.isalnum() else "_" for c in label).strip("_") or "carrier"
    kind = "".join(c if c.isalnum() else "_" for c in str(hit.get("kind", "hit"))) or "hit"
    order = "msb" if hit.get("msb", True) else "lsb"
    ext = "txt" if _mostly_text(raw) else "bin"
    return (
        f"payload_{index:03d}_{kind}_{label}"
        f"_b{int(hit.get('bit', 0))}_w{int(hit.get('width', 1))}"
        f"_o{int(hit.get('offset', 0))}_{order}.{ext}"
    )


def search_pixels(
    doc: ImageDoc,
    needle,
    max_bytes: int = 200_000,
    offsets=(0,),
    widths=(1, 2),
    limit: int = 200,
):
    """Find where a literal string occurs inside pixel carried bitstreams.

    This is the "search text in the pixels" path: instead of ranking whole
    streams, it walks the same decoder space as the sweep and reports exactly
    which carrier/bit/width/offset reproduces the requested text. Hits use the
    sweep's dictionary shape so they can be replayed and extracted unchanged.
    """
    if isinstance(needle, str):
        needle = needle.encode("latin-1", "ignore")
    needle = bytes(needle)
    if not needle:
        return []

    offsets = list(offsets)
    widths = tuple(widths)
    max_shift = max(offsets) if offsets else 0
    need = max_bytes * 8 + max_shift
    hits: list[dict] = []
    for spec in decode_specs(doc):
        label = spec_label(spec)
        for bit, width in bit_width_combo(doc.bits):
            if width not in widths:
                continue
            base = carrier_bitseq(doc, spec, bit, width, True, need)
            for msb in (True, False):
                for off in offsets:
                    segment = base[off : off + max_bytes * 8]
                    if segment.size < 8:
                        continue
                    raw = pack_bits(segment, msb_first=msb).tobytes()
                    start = 0
                    while True:
                        pos = raw.find(needle, start)
                        if pos < 0:
                            break
                        start = pos + 1
                        ctx = raw[max(0, pos - 16) : pos + len(needle) + 16]
                        hits.append(dict(
                            method="pixel text search",
                            spec=tuple(spec),
                            label=label,
                            bit=bit,
                            width=width,
                            offset=off,
                            msb=msb,
                            pos=pos,
                            match=needle.decode("latin-1", "replace"),
                            kind="text",
                            snippet=as_text(ctx),
                            score=1.0,
                        ))
                        if len(hits) >= limit:
                            return hits
    return hits


class TaskWorker(QThread):
    """Generic worker: run a callable on a thread and deliver its result."""

    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            value = self._fn(*self._args, **self._kwargs)
        except Exception:
            self.failed.emit(traceback.format_exc())
        else:
            self.result.emit(value)


# ---------------------------------------------------------------------------
# Headless self-test: run with  python -m ifw.scan
def _selftest() -> int:
    import os as _os
    import tempfile as _tf

    from .imaging import ImageDoc
    from PIL import Image

    failures = []

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    payload = b"flag{scan_selftest_ok}\x00\x01\x02"
    rng = np.random.default_rng(5)
    base = rng.integers(40, 220, size=(48, 72, 3), dtype=np.uint8)

    def set_bit(plane, bit, bits):
        flat = plane.ravel().astype(np.uint16)
        n = min(bits.size, flat.size)
        mask = np.uint16(1) << bit
        flat[:n] = (flat[:n] & ~mask) | (bits[:n].astype(np.uint16) << bit)
        return flat.reshape(plane.shape).astype(np.uint8)

    tmp = _tf.mkdtemp(prefix="ifw_scan_")
    path = _os.path.join(tmp, "stego.png")
    arr = base.copy()
    arr[..., 2] = set_bit(arr[..., 2], 0, np.unpackbits(np.frombuffer(payload, np.uint8)))
    Image.fromarray(arr).save(path)

    check("SCAN_DEPTHS has quick/standard/deep", {"quick", "standard", "deep"} <= set(SCAN_DEPTHS))
    cfg = SCAN_DEPTHS["standard"]
    check("depth config keys", {"max_bytes", "offsets", "widths"} <= set(cfg))

    doc = ImageDoc(path)
    specs = decode_specs(doc)
    check("decode_specs returns specs", len(specs) > 0, f"{len(specs)} specs")
    check("spec_label returns text", all(isinstance(spec_label(s), str) and spec_label(s) for s in specs))

    # run the worker synchronously
    worker = ScanWorker(doc, max_bytes=cfg["max_bytes"], offsets=cfg["offsets"], widths=cfg["widths"])
    hits, flags = [], []
    worker.candidates.connect(lambda h: hits.extend(h if isinstance(h, list) else []))
    worker.flagFound.connect(lambda h: flags.extend(h if isinstance(h, list) else []))
    worker.run()
    check("ScanWorker emits candidates", isinstance(hits, list))
    check("ScanWorker finds the embedded flag",
          any((h.get("match") or "").startswith("flag{scan_selftest_ok}") for h in flags),
          str([h.get("match") for h in flags][:1]))

    # extract_for_hit must replay the decode exactly
    if flags:
        from .imaging import extract_stream
        hit = flags[0]
        got = extract_stream(doc, hit.get("spec"), int(hit.get("bit", 0)), bool(hit.get("msb")),
                             int(hit.get("offset", 0)), len(payload), False, int(hit.get("width", 1))).tobytes()
        check("hit replay matches payload", got == payload, f"{got[:20]!r}")

        # full extraction: decode_hit recovers the whole payload, not a probe
        full = decode_hit(doc, hit).tobytes()
        check("decode_hit recovers the full payload", full.startswith(payload), f"{full[:24]!r}")
        check("hit_capacity_bytes is plausible",
              0 < hit_capacity_bytes(doc, hit) <= doc.capacity_bytes("B") + 1,
              f"{hit_capacity_bytes(doc, hit)}")
        name = hit_filename(0, hit, full)
        check("hit_filename is safe and descriptive",
              name.startswith("payload_000_") and name.endswith((".txt", ".bin")) and " " not in name,
              name)

    # search_pixels must reproduce a literal string from the carrier stream
    pixel_hits = search_pixels(doc, "flag{scan_selftest_ok}", offsets=(0,), widths=(1,))
    check("search_pixels finds the literal string", bool(pixel_hits),
          f"{len(pixel_hits)} configuration(s)")
    check("search_pixels reports the match offset",
          bool(pixel_hits) and all("pos" in hit for hit in pixel_hits))
    check("search_pixels ignores an absent string",
          search_pixels(doc, "no-such-token-here", offsets=(0,), widths=(1,)) == [])

    print()
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
