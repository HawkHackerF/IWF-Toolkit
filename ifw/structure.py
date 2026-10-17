"""Container structure analysis: chunk walk, trailing data, embedded objects.

The functions here work on raw bytes only. They never decode pixels, so they stay
fast on large files and remain valid for containers that Pillow cannot open.

Public entry point:

    analyze_structure(path) -> StructureReport

The report is a flat, printable description of every top level region of the
file (chunks, segments, boxes, IFDs), the data that sits after the logical end of
the container, and any recognisable object embedded inside the file body.
"""

from __future__ import annotations

import math
import os
import re
import struct
import zlib
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Region / object tables
# ---------------------------------------------------------------------------

#: magic bytes -> (kind, mime). This is the template library used by the
#: signature scan. Two byte magics ("BM", "MZ") are accepted but only reported
#: when a cheap validity check passes, so random data does not flood the report.
SIGNATURES: list[tuple[bytes, str, str]] = [
    # -- raster and vector images -------------------------------------------
    (b"\x89PNG\r\n\x1a\n", "PNG image", "image/png"),
    (b"\xff\xd8\xff\xe0", "JPEG image (JFIF)", "image/jpeg"),
    (b"\xff\xd8\xff\xe1", "JPEG image (Exif)", "image/jpeg"),
    (b"\xff\xd8\xff\xdb", "JPEG image", "image/jpeg"),
    (b"\xff\xd8\xff\xee", "JPEG image (Adobe)", "image/jpeg"),
    (b"\xff\xd8\xff\xe2", "JPEG image (ICC)", "image/jpeg"),
    (b"GIF87a", "GIF image", "image/gif"),
    (b"GIF89a", "GIF image", "image/gif"),
    (b"II*\x00", "TIFF image (little endian)", "image/tiff"),
    (b"MM\x00*", "TIFF image (big endian)", "image/tiff"),
    (b"\x00\x00\x01\x00", "ICO icon", "image/x-icon"),
    (b"\x00\x00\x02\x00", "CUR cursor", "image/x-icon"),
    (b"icns", "Apple icon image", "image/icns"),
    (b"8BPS", "Photoshop document", "image/vnd.adobe.photoshop"),
    (b"gimp xcf", "GIMP project", "image/x-xcf"),
    (b"DDS ", "DirectDraw surface", "image/vnd-ms.dds"),
    (b"\x76\x2f\x31\x01", "OpenEXR image", "image/x-exr"),
    (b"\x00\x00\x00\x0cjP  \r\n\x87\n", "JPEG 2000 image", "image/jp2"),
    (b"BPG\xfb", "BPG image", "image/bpg"),
    (b"qoif", "QOI image", "image/qoi"),
    (b"BM", "BMP image", "image/bmp"),
    # -- audio and video ----------------------------------------------------
    (b"RIFF", "RIFF container", "application/riff"),
    (b"WEBP", "WEBP image payload", "image/webp"),
    (b"WAVE", "WAV audio payload", "audio/wav"),
    (b"AVI ", "AVI video payload", "video/x-msvideo"),
    (b"OggS", "Ogg stream", "application/ogg"),
    (b"\x49\x44\x33\x03", "MP3 tag", "audio/mpeg"),
    (b"\xff\xfb", "MPEG audio frame", "audio/mpeg"),
    (b"fLaC", "FLAC stream", "audio/flac"),
    (b"\x1a\x45\xdf\xa3", "Matroska or WebM container", "video/x-matroska"),
    (b"ftyp", "ISO base media (MP4, MOV, HEIF, AVIF)", "video/mp4"),
    (b"MThd", "MIDI file", "audio/midi"),
    (b"FORM", "IFF container (AIFF, ILBM)", "application/iff"),
    # -- archives and compression ------------------------------------------
    (b"PK\x03\x04", "ZIP archive", "application/zip"),
    (b"PK\x05\x06", "ZIP end of central directory", "application/zip"),
    (b"PK\x01\x02", "ZIP central directory", "application/zip"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive", "application/x-7z-compressed"),
    (b"Rar!\x1a\x07", "RAR archive", "application/vnd.rar"),
    (b"\x1f\x8b\x08\x00", "gzip stream", "application/gzip"),
    (b"BZh9", "bzip2 stream", "application/x-bzip2"),
    (b"\xfd7zXZ\x00", "xz stream", "application/x-xz"),
    (b"\x28\xb5\x2f\xfd", "Zstandard stream", "application/zstd"),
    (b"\x04\x22\x4d\x18", "LZ4 stream", "application/x-lz4"),
    (b"\x89LZO\x00\r\n\x1a\n", "lzop stream", "application/x-lzop"),
    (b"MSCF", "Microsoft cabinet", "application/vnd.ms-cab-compressed"),
    (b"!<arch>\n", "Unix ar archive", "application/x-archive"),
    (b"\x30\x37\x30\x37\x30\x31", "cpio archive", "application/x-cpio"),
    (b"CD001", "ISO 9660 image", "application/x-iso9660-image"),
    (b"\xed\xab\xee\xdb", "RPM package", "application/x-rpm"),
    (b"debian-binary", "Debian package", "application/vnd.debian.binary-package"),
    # -- documents ----------------------------------------------------------
    (b"%PDF-", "PDF document", "application/pdf"),
    (b"{\\rtf", "RTF document", "application/rtf"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE compound file (DOC, XLS, MSI)", "application/x-ole-storage"),
    (b"%!PS", "PostScript document", "application/postscript"),
    (b"<?xml", "XML document", "application/xml"),
    (b"<!DOCTYPE html", "HTML document", "text/html"),
    (b"SQLite format 3\x00", "SQLite database", "application/vnd.sqlite3"),
    # -- programs and code --------------------------------------------------
    (b"\x7fELF", "ELF executable", "application/x-executable"),
    (b"MZ", "DOS or Windows executable", "application/x-dosexec"),
    (b"\xfe\xed\xfa\xce", "Mach-O executable (32 bit)", "application/x-mach-binary"),
    (b"\xfe\xed\xfa\xcf", "Mach-O executable (64 bit)", "application/x-mach-binary"),
    (b"\xce\xfa\xed\xfe", "Mach-O executable (32 bit, LE)", "application/x-mach-binary"),
    (b"\xcf\xfa\xed\xfe", "Mach-O executable (64 bit, LE)", "application/x-mach-binary"),
    (b"\xca\xfe\xba\xbe", "Java class or Mach-O fat binary", "application/java-vm"),
    (b"\x00asm", "WebAssembly module", "application/wasm"),
    (b"dex\n", "Android DEX", "application/x-dex"),
    (b"\x00\x01\x00\x00\x00", "TrueType font", "font/ttf"),
    (b"OTTO", "OpenType font", "font/otf"),
    (b"wOFF", "WOFF font", "font/woff"),
    (b"wOF2", "WOFF2 font", "font/woff2"),
    (b"ttcf", "TrueType collection", "font/collection"),
    # -- media and misc -----------------------------------------------------
    (b"FWS", "Flash SWF (uncompressed)", "application/x-shockwave-flash"),
    (b"CWS", "Flash SWF (zlib)", "application/x-shockwave-flash"),
    (b"ZWS", "Flash SWF (LZMA)", "application/x-shockwave-flash"),
    (b"\xd4\xc3\xb2\xa1", "PCAP capture (LE)", "application/vnd.tcpdump.pcap"),
    (b"\xa1\xb2\xc3\xd4", "PCAP capture (BE)", "application/vnd.tcpdump.pcap"),
    (b"\x0a\x0d\x0d\x0a", "PCAPNG capture", "application/x-pcapng"),
    (b"regf", "Windows registry hive", "application/x-windows-registry"),
    (b"ElfFile\x00", "Windows event log (EVTX)", "application/x-evtx"),
    (b"-----BEGIN ", "PEM encoded key or certificate", "application/x-pem-file"),
    (b"QFI\xfb", "QEMU disk image", "application/octet-stream"),
    (b"KDM", "VMDK disk image", "application/octet-stream"),
    (b"conectix", "VHD disk image", "application/octet-stream"),
]

#: Byte patterns worth reporting wherever they occur inside a file.
PATTERNS: list[tuple[str, bytes, str]] = [
    ("JPEG SOI", b"\xff\xd8\xff\xe0", "start of an embedded JFIF stream"),
    ("PNG signature", b"\x89PNG\r\n\x1a\n", "start of an embedded PNG stream"),
    ("GIF89a header", b"GIF89a", "start of an embedded GIF stream"),
    ("ZIP local header", b"PK\x03\x04", "start of an embedded ZIP archive"),
    ("PDF header", b"%PDF-", "start of an embedded PDF document"),
    ("RAR header", b"Rar!\x1a\x07", "start of an embedded RAR archive"),
    ("7-Zip header", b"7z\xbc\xaf\x27\x1c", "start of an embedded 7-Zip archive"),
    ("gzip header", b"\x1f\x8b\x08\x00", "start of an embedded gzip stream"),
]

#: Container specific markers that indicate the logical end of the payload.
_CRC_TABLE = None


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def entropy(data: bytes) -> float:
    """Shannon entropy of a byte string in bits per byte (0..8)."""
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    n = len(data)
    total = 0.0
    for count in counts:
        if count:
            p = count / n
            total -= p * math.log2(p)
    return total


def _preview(data: bytes, limit: int = 96) -> str:
    """Short, printable, hex oriented preview of a byte string."""
    head = data[:limit]
    text = []
    for byte in head:
        if 32 <= byte < 127:
            text.append(chr(byte))
        elif byte == 10:
            text.append("/n")
        elif byte == 13:
            text.append("/r")
        elif byte == 9:
            text.append("/t")
        else:
            text.append(f"\\x{byte:02x}")
    out = "".join(text)
    if len(data) > limit:
        out += "..."
    return out


def _entropy_verdict(value: float) -> str:
    if value >= 7.6:
        return "high entropy (compressed or encrypted)"
    if value >= 6.0:
        return "moderately high entropy"
    if value >= 3.0:
        return "mixed content"
    return "low entropy (structured or text)"


def entropy_map(data: bytes, block: int = 1024, limit: int = 4096):
    """Per-block Shannon entropy, for a binwalk style entropy view.

    Returns a list of (offset, entropy) covering the whole file with at most
    `limit` samples; `block` is the minimum window size in bytes.
    """
    if not data or block <= 0:
        return []
    stride = max(block, len(data) // max(1, limit))
    scores = []
    for offset in range(0, len(data), stride):
        scores.append((offset, entropy(data[offset : offset + stride])))
    return scores


def collect_strings(data: bytes, min_len: int = 6, limit: int = 400, encoding: str = "ascii"):
    """Printable strings with their offsets, like the `strings` utility.

    `encoding` selects the encodings to search: "ascii" (the default, matching
    the classic tool), "utf16le" for the wide runs Windows embeds, or "both".
    Each result is (offset, text, encoding).
    """
    if min_len < 1:
        min_len = 1
    results: list[tuple[int, str, str]] = []
    if encoding in ("ascii", "both"):
        pattern = re.compile(rb"[\x20-\x7e]{%d,}" % min_len)
        for match in pattern.finditer(data):
            results.append((match.start(), match.group(0).decode("latin-1"), "ascii"))
            if len(results) >= limit:
                break
    if encoding in ("utf16le", "utf-16le", "utf16", "both") and len(results) < limit:
        pattern = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len)
        for match in pattern.finditer(data):
            results.append((match.start(), match.group(0).decode("utf-16le"), "utf16le"))
            if len(results) >= limit:
                break
    results.sort(key=lambda item: item[0])
    return results[:limit]


def find_strings(data: bytes, min_len: int = 6, limit: int = 400):
    """Printable ASCII runs of at least `min_len` characters.

    Kept as the two-tuple form used by the structural findings.
    """
    return [(offset, text) for offset, text, _enc in collect_strings(data, min_len, limit, "ascii")]


def find_patterns(data: bytes, limit: int = 200):
    """All signature occurrences in the body, with offsets and context."""
    hits = []
    seen = set()
    for name, needle, note in PATTERNS:
        start = 0
        while len(hits) < limit:
            pos = data.find(needle, start)
            if pos < 0:
                break
            start = pos + 1
            if (name, pos) in seen:
                continue
            seen.add((name, pos))
            hits.append((pos, name, note))
    hits.sort(key=lambda item: item[0])
    return hits


def _plausible_signature(magic: bytes, data: bytes, pos: int) -> bool:
    """Guard the few short magics against chance occurrences in random data."""
    if magic == b"BM":
        if pos + 6 > len(data):
            return False
        size = struct.unpack_from("<I", data, pos + 2)[0]
        return 14 <= size <= len(data) - pos
    if magic == b"MZ":
        if pos + 0x40 > len(data):
            return False
        lfanew = struct.unpack_from("<I", data, pos + 0x3C)[0]
        if lfanew == 0 or pos + lfanew + 2 > len(data):
            return False
        return data[pos + lfanew : pos + lfanew + 2] in (b"PE", b"NE", b"LE", b"LX")
    if magic == b"\xff\xfb":
        return pos + 1 < len(data) and (data[pos + 1] & 0xE0) == 0xE0
    return True


def find_signatures(data: bytes, min_offset: int = 1, limit: int = 256):
    """Embedded objects recognised by their container signature.

    The first signature at offset 0 is the file's own header, so it is skipped.
    """
    found = []
    for magic, kind, mime in SIGNATURES:
        start = 0
        while len(found) < limit:
            pos = data.find(magic, start)
            if pos < 0:
                break
            start = pos + 1
            if pos < min_offset or not _plausible_signature(magic, data, pos):
                continue
            found.append((pos, kind, mime))
    found.sort(key=lambda item: item[0])
    deduped = []
    last = -1
    for pos, kind, mime in found:
        if pos == last:
            continue
        deduped.append((pos, kind, mime))
        last = pos
    return deduped[:limit]


def carve(data: bytes, offset: int, length: int) -> bytes:
    """Slice `length` bytes out of `data` at `offset`, clamped to the buffer."""
    if offset < 0 or offset >= len(data):
        return b""
    return data[offset : offset + max(0, length)]


def _estimate_object_length(data: bytes, offset: int, magic: bytes, cap: int) -> int:
    """Best effort length for a signature found at `offset`."""
    chunk = data[offset : offset + 64]
    try:
        if magic.startswith(b"\x89PNG"):
            end = data.find(b"IEND\xaeB`\x82", offset)
            if end >= 0:
                return min(end + 8 - offset, cap)
        elif magic.startswith(b"\xff\xd8\xff"):
            end = data.find(b"\xff\xd9", offset + 2)
            if end >= 0:
                return min(end + 2 - offset, cap)
        elif magic.startswith(b"GIF"):
            return min(len(data) - offset, cap)
        elif magic.startswith(b"PK\x03\x04"):
            end = data.find(b"PK\x05\x06", offset)
            if end >= 0:
                return min(end + 22 - offset, cap)
        elif magic.startswith(b"%PDF-"):
            end = data.find(b"%%EOF", offset)
            if end >= 0:
                return min(end + 5 - offset, cap)
        elif magic.startswith(b"RIFF") and len(chunk) >= 8:
            size = struct.unpack_from("<I", data, offset + 4)[0]
            return min(size + 8, cap)
    except (struct.error, IndexError):
        pass
    return min(len(data) - offset, cap)


# ---------------------------------------------------------------------------
# Report structures
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    offset: int
    length: int
    ctype: str
    name: str
    detail: str = ""
    flag: str = ""


@dataclass
class Embedded:
    offset: int
    length: int
    kind: str
    mime: str = ""
    note: str = ""
    preview: str = ""


@dataclass
class StructureReport:
    path: str
    file_format: str
    file_size: int
    chunks: list[Chunk] = field(default_factory=list)
    trailing_offset: int = -1
    trailing_length: int = 0
    trailing_preview: str = ""
    trailing_entropy: float = 0.0
    embedded: list[Embedded] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    # -- serialisation -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "file_format": self.file_format,
            "file_size": self.file_size,
            "chunks": [vars(c) for c in self.chunks],
            "trailing_offset": self.trailing_offset,
            "trailing_length": self.trailing_length,
            "trailing_preview": self.trailing_preview,
            "trailing_entropy": self.trailing_entropy,
            "embedded": [vars(e) for e in self.embedded],
            "findings": list(self.findings),
            "warnings": list(self.warnings),
            "extra": self.extra,
        }

    def to_text(self) -> str:
        lines = [
            f"Container structure for {os.path.basename(self.path)}",
            f"  format      : {self.file_format}",
            f"  size        : {self.file_size:,} bytes",
            f"  segments    : {len(self.chunks)}",
            f"  embedded    : {len(self.embedded)}",
            f"  trailing    : {self.trailing_length} bytes at offset {self.trailing_offset}",
            "",
            "Segments",
            "--------",
        ]
        for chunk in self.chunks:
            detail = f"  {chunk.detail}" if chunk.detail else ""
            flag = f"  [{chunk.flag}]" if chunk.flag else ""
            lines.append(
                f"  0x{chunk.offset:08X}  {chunk.length:>10}  {chunk.ctype:<12}{flag}"
            )
            lines.append(f"              {chunk.name}{detail}")
        if self.embedded:
            lines += ["", "Embedded objects", "----------------"]
            for item in self.embedded:
                lines.append(
                    f"  0x{item.offset:08X}  {item.length:>10}  {item.kind}  {item.mime}"
                )
                if item.note:
                    lines.append(f"              {item.note}")
                if item.preview:
                    lines.append(f"              {item.preview}")
        if self.trailing_length:
            lines += [
                "",
                "Trailing data",
                "-------------",
                f"  offset   : 0x{self.trailing_offset:08X}",
                f"  length   : {self.trailing_length} bytes",
                f"  entropy  : {self.trailing_entropy:.4f} bit/byte - {_entropy_verdict(self.trailing_entropy)}",
                f"  preview  : {self.trailing_preview}",
            ]
        if self.findings:
            lines += ["", "Findings", "--------"] + [f"  - {f}" for f in self.findings]
        if self.warnings:
            lines += ["", "Warnings", "--------"] + [f"  - {w}" for w in self.warnings]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Format parsers - every parser returns (chunks, end_offset, extra)
# ---------------------------------------------------------------------------
def parse_png(data: bytes):
    chunks: list[Chunk] = []
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    chunks.append(Chunk(0, 8, "signature", "PNG file signature"))
    pos = 8
    end = len(data)
    counts: dict[str, int] = {}
    names = {
        b"IHDR": "image header",
        b"PLTE": "palette",
        b"IDAT": "image data",
        b"IEND": "end of image",
        b"tRNS": "transparency",
        b"gAMA": "gamma",
        b"cHRM": "chromaticity",
        b"sRGB": "sRGB rendering intent",
        b"iCCP": "embedded ICC profile",
        b"pHYs": "physical pixel dimensions",
        b"tEXt": "uncompressed text",
        b"zTXt": "compressed text",
        b"iTXt": "international text",
        b"bKGD": "background colour",
        b"tIME": "last modification time",
        b"eXIf": "Exif metadata",
        b"sBIT": "significant bits",
        b"hIST": "palette histogram",
        b"sPLT": "suggested palette",
        b"acTL": "APNG animation control",
        b"fcTL": "APNG frame control",
        b"fdAT": "APNG frame data",
    }
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos + 4 : pos + 8]
        if pos + 12 + length > len(data):
            chunks.append(
                Chunk(pos, len(data) - pos, ctype.decode("latin-1", "replace"),
                      "truncated chunk", flag="suspicious")
            )
            break
        body = data[pos + 8 : pos + 8 + length]
        name = names.get(ctype, "unknown chunk type")
        detail = ""
        flag = ""
        if ctype == b"IHDR" and length >= 13:
            w, h, depth, colour, comp, filt, interlace = struct.unpack_from(">IIBBBBB", body, 0)
            detail = (
                f"  {w}x{h} px, {depth} bit, colour type {colour}, "
                f"{'interlaced' if interlace else 'progressive'}"
            )
        elif ctype == b"PLTE":
            detail = f"  {length // 3} palette entries"
        elif ctype in (b"tEXt", b"zTXt", b"iTXt"):
            detail = "  " + _preview(body, 120)
        elif ctype == b"iCCP" and len(body) > 2:
            sep = body.find(b"\x00")
            detail = "  profile name " + body[:sep].decode("latin-1", "replace") if sep > 0 else ""
        elif ctype == b"pHYs" and length >= 9:
            px, py, unit = struct.unpack_from(">IIB", body, 0)
            detail = f"  {px}x{py} ppm, unit {unit}"
        elif ctype == b"tIME" and length >= 7:
            year, month, day, hour, minute, second = struct.unpack_from(">HBBBBB", body, 0)
            detail = f"  {year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
        elif ctype not in names:
            flag = "suspicious"
        # CRC check
        crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        if crc != _crc32(ctype + body):
            flag = flag or "suspicious"
            detail += "  CRC mismatch"
        ctype_name = ctype.decode("latin-1", "replace")
        counts[ctype_name] = counts.get(ctype_name, 0) + 1
        chunks.append(Chunk(pos, length + 12, ctype.decode("latin-1", "replace"), name, detail, flag))
        pos += 12 + length
        if ctype == b"IEND":
            end = pos
            break
    extra = {"chunk_counts": counts}
    return chunks, end, extra


def parse_jpeg(data: bytes):
    chunks: list[Chunk] = []
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG file")
    chunks.append(Chunk(0, 2, "SOI", "start of image"))
    pos = 2
    end = len(data)
    marker_names = {
        0xC0: "SOF0 baseline frame",
        0xC1: "SOF1 extended sequential frame",
        0xC2: "SOF2 progressive frame",
        0xC3: "SOF3 lossless frame",
        0xC4: "DHT Huffman tables",
        0xC8: "JPG reserved",
        0xCC: "DAC arithmetic conditioning",
        0xD8: "SOI",
        0xD9: "EOI",
        0xDA: "SOS scan header",
        0xDB: "DQT quantisation tables",
        0xDC: "DNL number of lines",
        0xDD: "DRI restart interval",
        0xDE: "DHP",
        0xDF: "EXP",
        0xE0: "APP0 JFIF",
        0xE1: "APP1 Exif or XMP",
        0xE2: "APP2 ICC profile or FlashPix",
        0xE3: "APP3",
        0xE4: "APP4",
        0xE5: "APP5",
        0xE6: "APP6",
        0xE7: "APP7",
        0xE8: "APP8",
        0xE9: "APP9",
        0xEA: "APP10",
        0xEB: "APP11",
        0xEC: "APP12",
        0xED: "APP13 IPTC / Photoshop",
        0xEE: "APP14 Adobe",
        0xEF: "APP15",
        0xFE: "comment",
    }
    while pos + 1 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker == 0x00:
            continue
        if marker in (0xD8, 0xD9):
            name = marker_names.get(marker, f"marker FF{marker:02X}")
            chunks.append(Chunk(pos - 2, 2, f"FF{marker:02X}", name))
            if marker == 0xD9:
                end = pos
                break
            continue
        if 0xD0 <= marker <= 0xD7:
            chunks.append(Chunk(pos - 2, 2, f"FF{marker:02X}", "restart marker"))
            continue
        if pos + 2 > len(data):
            break
        seg_len = struct.unpack_from(">H", data, pos)[0]
        seg_start = pos - 2
        if seg_len < 2 or pos + seg_len > len(data):
            chunks.append(
                Chunk(seg_start, len(data) - seg_start, f"FF{marker:02X}",
                      marker_names.get(marker, "marker"), "  truncated segment", "suspicious")
            )
            break
        body = data[pos + 2 : pos + seg_len]
        name = marker_names.get(marker, f"marker FF{marker:02X}")
        detail = ""
        flag = ""
        if marker == 0xE0 and body[:5] == b"JFIF\x00":
            detail = f"  JFIF version {body[5]}.{body[6]:02d}"
        elif marker == 0xE1:
            if body[:6] == b"Exif\x00\x00":
                detail = f"  Exif payload, {len(body)} bytes"
            elif body[:28].lstrip().startswith(b"http://ns.adobe.com/xap"):
                detail = "  XMP packet"
            else:
                detail = "  " + _preview(body, 80)
        elif marker == 0xE2 and body[:11] == b"ICC_PROFILE":
            detail = f"  ICC profile part {body[12]}/{body[13]}"
        elif marker == 0xE4 and body[:5] == b"JUMBF":
            detail = "  JUMBF box (C2PA or JPEG Universal Metadata)"
            flag = "info"
        elif marker in (0xDB,) and body:
            precision = body[0] >> 4
            table_id = body[0] & 0x0F
            detail = f"  table {table_id}, {'16' if precision else '8'} bit precision"
        elif marker in (0xC0, 0xC1, 0xC2, 0xC3) and len(body) >= 6:
            precision, h, w, ncomp = struct.unpack_from(">BHHB", body, 0)
            detail = f"  {w}x{h} px, {ncomp} components, {precision} bit precision"
        elif marker == 0xC4:
            tables = []
            i = 0
            while i < len(body):
                tc = body[i] >> 4
                th = body[i] & 0x0F
                tables.append(("DC" if tc == 0 else "AC") + str(th))
                i += 17
                for _ in range(16):
                    if i < len(body):
                        i += body[i] + 1
            detail = "  tables: " + ", ".join(tables)
        elif marker == 0xDA:
            ncomp = body[0]
            comps = body[1 : 1 + 2 * ncomp]
            detail = f"  {ncomp} components in scan"
            chunks.append(Chunk(seg_start, seg_len + 2, f"FF{marker:02X}", name, detail, flag))
            pos += seg_len
            # Scan the entropy coded data up to the next real marker.
            scan_start = pos
            i = pos
            while i + 1 < len(data):
                if data[i] == 0xFF:
                    nxt = data[i + 1]
                    if nxt == 0x00 or nxt == 0xFF or 0xD0 <= nxt <= 0xD7:
                        i += 2
                        continue
                    break
                i += 1
            length = i - scan_start
            chunks.append(
                Chunk(scan_start, length, "SCAN", "entropy coded scan data",
                      f"  {length} bytes, entropy {entropy(data[scan_start:i]):.4f} bit/byte")
            )
            pos = i
            continue
        elif marker == 0xDD and len(body) >= 2:
            detail = f"  restart interval {struct.unpack_from('>H', body, 0)[0]}"
        elif marker == 0xFE:
            detail = "  " + _preview(body, 120)
        elif 0xE0 <= marker <= 0xEF and not detail:
            detail = "  " + _preview(body, 60)
        chunks.append(Chunk(seg_start, seg_len + 2, f"FF{marker:02X}", name, detail, flag))
        pos += seg_len
    return chunks, end, {}


def parse_gif(data: bytes):
    chunks: list[Chunk] = []
    if not data.startswith((b"GIF87a", b"GIF89a")):
        raise ValueError("not a GIF file")
    header = "GIF87a" if data.startswith(b"GIF87a") else "GIF89a"
    chunks.append(Chunk(0, 6, "header", header))
    if len(data) < 13:
        return chunks, len(data), {}
    w, h, flags, bg, aspect = struct.unpack_from("<HHBBB", data, 6)
    gct = (flags & 0x80) != 0
    gct_size = 2 ** ((flags & 0x07) + 1) if gct else 0
    detail = f"  {w}x{h} px, background {bg}, {'global colour table' if gct else 'no global table'}"
    if gct:
        detail += f", {gct_size} entries"
    chunks.append(Chunk(6, 7, "LSD", "logical screen descriptor", detail))
    pos = 13
    if gct:
        chunks.append(Chunk(pos, gct_size * 3, "GCT", "global colour table",
                            f"  {gct_size} entries"))
        pos += gct_size * 3
    end = len(data)
    while pos < len(data):
        block = data[pos]
        if block == 0x3B:
            chunks.append(Chunk(pos, 1, "0x3B", "trailer"))
            end = pos + 1
            break
        if block == 0x2C:
            if pos + 10 > len(data):
                break
            iw, ih, iflags = struct.unpack_from("<HHB", data, pos + 1)
            lct = (iflags & 0x80) != 0
            lct_size = 2 ** ((iflags & 0x07) + 1) if lct else 0
            detail = f"  {iw}x{ih} px" + (f", {lct_size} local entries" if lct else "")
            chunks.append(Chunk(pos, 10, "0x2C", "image descriptor", detail))
            pos += 10
            if lct:
                chunks.append(Chunk(pos, lct_size * 3, "LCT", "local colour table",
                                    f"  {lct_size} entries"))
                pos += lct_size * 3
            if pos < len(data):
                chunks.append(Chunk(pos, 1, "LZW", "LZW minimum code size",
                                    f"  {data[pos]}"))
                pos += 1
            while pos < len(data):
                size = data[pos]
                sub_end = min(len(data), pos + 1 + size)
                if size > 0:
                    pos = sub_end
                else:
                    pos += 1
                    break
        elif block == 0x21:
            if pos + 2 > len(data):
                break
            label = data[pos + 1]
            labels = {
                0xF9: "graphic control extension",
                0xFE: "comment extension",
                0x01: "plain text extension",
                0xFF: "application extension",
            }
            detail = ""
            p = pos + 2
            if label == 0xFF and p + 11 <= len(data):
                detail = "  " + data[p + 1 : p + 12].decode("latin-1", "replace")
            chunks.append(Chunk(pos, 2, f"0x21/{label:02X}", labels.get(label, "extension"), detail))
            pos = p
            while pos < len(data):
                size = data[pos]
                if size == 0:
                    pos += 1
                    break
                pos = min(len(data), pos + 1 + size)
        else:
            chunks.append(Chunk(pos, 1, f"0x{block:02X}", "unknown block", flag="suspicious"))
            break
    return chunks, end, {}


def parse_bmp(data: bytes):
    chunks: list[Chunk] = []
    if not data.startswith(b"BM") or len(data) < 14:
        raise ValueError("not a BMP file")
    size, reserved, offset = struct.unpack_from("<IHHI", data, 2)
    chunks.append(Chunk(0, 2, "signature", "BM signature"))
    chunks.append(Chunk(2, 12, "file header", "BMP file header",
                        f"  declared size {size} bytes, pixel data at {offset}"))
    dib = 0
    if len(data) >= 18:
        dib = struct.unpack_from("<I", data, 14)[0]
    chunks.append(Chunk(14, min(dib or 40, len(data) - 14), "DIB header",
                        "device independent bitmap header", f"  {dib} byte header"))
    end = size if 14 < size <= len(data) else len(data)
    if offset < end:
        chunks.append(Chunk(offset, end - offset, "PIXELS", "pixel array",
                            f"  {end - offset} bytes"))
    return chunks, end, {"declared_size": size}


def parse_tiff(data: bytes):
    chunks: list[Chunk] = []
    order = "<" if data.startswith(b"II") else ">"
    if data[:2] not in (b"II", b"MM"):
        raise ValueError("not a TIFF file")
    magic = struct.unpack_from(order + "H", data, 2)[0]
    chunks.append(Chunk(0, 2, "byte order", "II little endian" if order == "<" else "MM big endian"))
    chunks.append(Chunk(2, 2, "magic", f"magic {magic}"))
    if magic != 42:
        chunks.append(Chunk(4, 4, "offset", "first IFD offset", "  not a TIFF (magic != 42)",
                            "suspicious"))
        return chunks, len(data), {}
    ifd = struct.unpack_from(order + "I", data, 4)[0]
    chunks.append(Chunk(4, 4, "IFD0", "offset of first image file directory",
                        f"  0x{ifd:08X}"))
    seen = set()
    end = len(data)
    count = 0
    while ifd and ifd not in seen and ifd + 2 <= len(data) and count < 32:
        seen.add(ifd)
        count += 1
        n = struct.unpack_from(order + "H", data, ifd)[0]
        length = 2 + n * 12 + 4
        if ifd + length > len(data):
            length = len(data) - ifd
        chunks.append(Chunk(ifd, length, f"IFD{count - 1}", "image file directory",
                            f"  {n} entries"))
        if ifd + 2 + n * 12 + 4 > len(data):
            break
        ifd = struct.unpack_from(order + "I", data, ifd + 2 + n * 12)[0]
        if ifd:
            chunks.append(Chunk(ifd - 4, 4, "next", "pointer to next IFD",
                                f"  0x{ifd:08X}"))
    return chunks, end, {"ifd_count": count}


def parse_webp(data: bytes):
    chunks: list[Chunk] = []
    if not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        raise ValueError("not a WEBP file")
    size = struct.unpack_from("<I", data, 4)[0]
    chunks.append(Chunk(0, 4, "RIFF", "RIFF container"))
    chunks.append(Chunk(4, 4, "size", "declared payload size", f"  {size} bytes"))
    chunks.append(Chunk(8, 4, "WEBP", "WEBP fourcc"))
    pos = 12
    end = min(len(data), size + 8)
    names = {
        b"VP8 ": "lossy VP8 bitstream",
        b"VP8L": "lossless VP8L bitstream",
        b"VP8X": "extended format header",
        b"ALPH": "alpha plane",
        b"ANIM": "animation parameters",
        b"ANMF": "animation frame",
        b"ICCP": "ICC colour profile",
        b"EXIF": "Exif metadata",
        b"XMP ": "XMP metadata",
    }
    while pos + 8 <= end:
        fourcc = data[pos : pos + 4]
        length = struct.unpack_from("<I", data, pos + 4)[0]
        chunks.append(Chunk(pos, 8 + length + (length & 1),
                            fourcc.decode("latin-1", "replace"),
                            names.get(fourcc, "RIFF chunk"),
                            f"  {length} bytes"))
        pos += 8 + length + (length & 1)
    return chunks, end, {"declared_size": size}


def parse_pnm(data: bytes):
    chunks: list[Chunk] = []
    magic = data[:2]
    if magic not in (b"P1", b"P2", b"P3", b"P4", b"P5", b"P6"):
        raise ValueError("not a PNM file")
    names = {
        b"P1": "ASCII bitmap", b"P2": "ASCII graymap", b"P3": "ASCII pixmap",
        b"P4": "binary bitmap", b"P5": "binary graymap", b"P6": "binary pixmap",
    }
    chunks.append(Chunk(0, 2, "magic", names[magic]))
    pos = 2
    # skip header tokens (tolerating comments)
    for _ in range(3):
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        if pos < len(data) and data[pos] == ord("#"):
            while pos < len(data) and data[pos] not in b"\r\n":
                pos += 1
        start = pos
        while pos < len(data) and data[pos] not in b" \t\r\n#":
            pos += 1
        chunks.append(Chunk(start, pos - start, "header", "PNM header field",
                            "  " + data[start:pos].decode("latin-1", "replace")))
    while pos < len(data) and data[pos] in b" \t\r\n":
        pos += 1
    chunks.append(Chunk(pos, len(data) - pos, "PIXELS", "pixel data",
                        f"  {len(data) - pos} bytes"))
    return chunks, len(data), {}


def parse_ico(data: bytes):
    chunks: list[Chunk] = []
    if len(data) < 6:
        raise ValueError("not an ICO file")
    reserved, itype, count = struct.unpack_from("<HHH", data, 0)
    chunks.append(Chunk(0, 6, "header", "icon directory header",
                        f"  type {itype}, {count} images"))
    pos = 6
    entries = []
    for _ in range(count):
        if pos + 16 > len(data):
            break
        w, h, colours, res, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, pos)
        entries.append((offset, size, w or 256, h or 256))
        chunks.append(Chunk(pos, 16, "entry", "icon directory entry",
                            f"  {w or 256}x{h or 256}, {bpp} bpp, {size} bytes at 0x{offset:08X}"))
        pos += 16
    for offset, size, w, h in entries:
        if offset + size <= len(data):
            chunks.append(Chunk(offset, size, "IMAGE", f"icon bitmap {w}x{h}",
                                f"  {size} bytes"))
    return chunks, len(data), {"count": count}


PARSERS = {
    "PNG": parse_png,
    "JPEG": parse_jpeg,
    "GIF": parse_gif,
    "BMP": parse_bmp,
    "TIFF": parse_tiff,
    "WEBP": parse_webp,
    "PNM": parse_pnm,
    "ICO": parse_ico,
}


def sniff_format(data: bytes) -> str:
    """Container format guessed from the leading bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if data.startswith(b"BM"):
        return "BMP"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:2] in (b"P1", b"P2", b"P3", b"P4", b"P5", b"P6"):
        return "PNM"
    if data[:4] == b"\x00\x00\x01\x00":
        return "ICO"
    return "UNKNOWN"


def trailing_data(data: bytes, end: int):
    """Return (offset, bytes) for anything after the logical end of the file."""
    end = max(0, min(end, len(data)))
    return end, data[end:]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def analyze_structure(path: str) -> StructureReport:
    """Full structural analysis of a file on disk."""
    with open(path, "rb") as handle:
        data = handle.read()
    fmt = sniff_format(data)
    if fmt == "UNKNOWN" and path.lower().endswith(".ico"):
        fmt = "ICO"
    report = StructureReport(path=path, file_format=fmt, file_size=len(data))

    parser = PARSERS.get(fmt)
    end = len(data)
    try:
        if parser is not None:
            chunks, end, extra = parser(data)
            report.chunks = chunks
            report.extra.update(extra)
        else:
            report.warnings.append(
                "container format not recognised; only generic carving was performed"
            )
            report.chunks.append(
                Chunk(0, len(data), "RAW", "unrecognised container",
                      f"  {len(data)} bytes, entropy {entropy(data):.4f} bit/byte")
            )
    except Exception as exc:  # a parser bug must not break the report
        report.warnings.append(f"{fmt} parser failed: {type(exc).__name__}: {exc}")

    # -- trailing data -------------------------------------------------------
    offset, tail = trailing_data(data, end)
    report.trailing_offset = offset if tail else -1
    report.trailing_length = len(tail)
    if tail:
        report.trailing_preview = _preview(tail, 160)
        report.trailing_entropy = entropy(tail)
        report.findings.append(
            f"{len(tail)} bytes of trailing data after the end of the {fmt} stream "
            f"at offset 0x{offset:08X} (entropy {report.trailing_entropy:.4f} bit/byte)"
        )
        recall = sniff_format(tail)
        if recall != "UNKNOWN":
            report.findings.append(
                f"trailing data begins with a {recall} signature - likely an appended file"
            )

    # -- embedded objects ----------------------------------------------------
    seen_offsets: set[int] = set()
    for pos, kind, mime in find_signatures(data, min_offset=1):
        if pos in seen_offsets:
            continue
        seen_offsets.add(pos)
        length = _estimate_object_length(data, pos, _magic_for(kind, data, pos), len(data) - pos)
        blob = data[pos : pos + min(length, 64)]
        report.embedded.append(
            Embedded(
                offset=pos,
                length=length,
                kind=kind,
                mime=mime,
                note=(
                    "after the logical end of the container"
                    if tail and pos >= offset
                    else "inside the container body"
                ),
                preview=_preview(blob, 60),
            )
        )
    for pos, name, note in find_patterns(data, limit=200):
        if pos in seen_offsets or sniff_format(data[pos:]) != "UNKNOWN":
            continue
        seen_offsets.add(pos)
        report.embedded.append(
            Embedded(offset=pos, length=len(data) - pos, kind=name, note=note,
                     preview=_preview(data[pos : pos + 48], 48))
        )

    _add_structural_findings(report, data, fmt)
    return report


def _magic_for(kind: str, data: bytes, pos: int) -> bytes:
    for magic, k, _mime in SIGNATURES:
        if k == kind and data.startswith(magic, pos):
            return magic
    return data[pos : pos + 8]


def _add_structural_findings(report: StructureReport, data: bytes, fmt: str) -> None:
    """Derive human readable findings from the parsed segments."""
    if fmt == "PNG":
        counts = report.extra.get("chunk_counts", {})
        if counts.get("IDAT", 0) > 1:
            report.findings.append(
                f"PNG image data is split across {counts['IDAT']} IDAT chunks"
            )
        for chunk in report.chunks:
            if chunk.flag == "suspicious":
                report.findings.append(
                    f"unexpected PNG chunk '{chunk.ctype}' at offset 0x{chunk.offset:08X}"
                )
            if chunk.ctype in ("tEXt", "zTXt", "iTXt") and len(chunk.detail) > 6:
                report.findings.append(
                    f"PNG text chunk at offset 0x{chunk.offset:08X}: {chunk.detail.strip()}"
                )
            if chunk.ctype == "iCCP":
                report.findings.append(
                    f"embedded ICC profile at offset 0x{chunk.offset:08X}"
                )
    elif fmt == "JPEG":
        apps = [c for c in report.chunks if c.ctype.startswith("FFE")]
        if len(apps) > 2:
            report.findings.append(f"{len(apps)} APPn marker segments present")
        for chunk in report.chunks:
            if chunk.ctype == "FFFE":
                report.findings.append(f"JPEG comment: {chunk.detail.strip()}")
            if chunk.ctype == "FFE1" and "Exif" in chunk.name:
                report.findings.append("Exif metadata present (APP1)")
            if chunk.ctype == "FFE1" and "XMP" in chunk.detail:
                report.findings.append("XMP metadata present (APP1)")
            if chunk.ctype == "FFE2" and "ICC" in chunk.detail:
                report.findings.append("ICC profile present (APP2)")
    elif fmt == "GIF":
        for chunk in report.chunks:
            if chunk.ctype.startswith("0x21/FE"):
                report.findings.append(
                    f"GIF comment extension at offset 0x{chunk.offset:08X}"
                )
            if chunk.ctype.startswith("0x21/FF"):
                report.findings.append(
                    f"GIF application extension '{chunk.detail.strip()}'"
                )
    elif fmt == "TIFF":
        count = report.extra.get("ifd_count", 0)
        if count > 1:
            report.findings.append(f"{count} image file directories (multi-page TIFF)")
    if report.trailing_length and report.trailing_entropy < 1.0:
        report.findings.append(
            "trailing data is low entropy and may carry plain text"
        )
    strings = [s for _o, s in find_strings(data, min_len=12, limit=40) if "flag" in s.lower()]
    for text in strings[:5]:
        report.findings.append(f"string containing 'flag' in the file body: {text[:120]}")


# ---------------------------------------------------------------------------
# Optional self test
# ---------------------------------------------------------------------------
def _selftest() -> int:
    """Round trip on a generated PNG with an appended object."""
    import io
    import tempfile

    from PIL import Image

    failures = 0

    def check(condition, label):
        nonlocal failures
        if not condition:
            failures += 1
            print(f"FAIL: {label}")
        return condition

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buffer, "PNG")
    blob = buffer.getvalue() + b"PK\x03\x04" + b"A" * 40
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(blob)
        path = handle.name
    try:
        report = analyze_structure(path)
        check(report.file_format == "PNG", "format is PNG")
        check(any(c.ctype == "IHDR" for c in report.chunks), "IHDR present")
        check(report.trailing_length == 44, "trailing length detected")
        check(any(e.kind == "ZIP archive" for e in report.embedded), "appended ZIP found")
        text = report.to_text()
        check("Trailing data" in text, "text report has trailing section")
        check(isinstance(report.to_dict(), dict), "dict report")

        # template scan sees objects past a chance two byte magic
        combo = blob + b"\x7fELF\x02\x01\x01\x00" + b"B" * 32
        check(any(kind.endswith("executable") for kind in
                  {kind for _o, kind, _m in find_signatures(combo)}), "ELF signature found")

        # a short magic is reported only when a cheap validity check passes
        plausible = b"\x00" * 16 + b"BM" + b"\x20\x00\x00\x00" + b"\x00" * 40
        bogus = b"\x00" * 16 + b"BM" + b"\x00\x00\x00\x00" + b"\x00" * 40
        check(any(kind == "BMP image" for _o, kind, _m in find_signatures(plausible)),
              "BMP with plausible size reported")
        check(not any(kind == "BMP image" for _o, kind, _m in find_signatures(bogus)),
              "BMP with implausible size guarded")

        # entropy map and strings extraction
        scores = entropy_map(blob, block=32)
        check(bool(scores) and all(0.0 <= value <= 8.0 for _o, value in scores),
              "entropy map bounded")
        wide = "flag{wide}".encode("utf-16-le")
        check(any("flag{wide}" in t for _o, t, _e in collect_strings(wide, 3, 8, "utf16le")),
              "utf-16 strings found")
        check(find_strings(b"\x00\x00\x00\x00hello world\x00\x00") == [(4, "hello world")],
              "find_strings keeps its two tuple form")
    finally:
        os.unlink(path)

    print(f"structure selftest: {failures} failure(s)")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if _selftest() else 0)
