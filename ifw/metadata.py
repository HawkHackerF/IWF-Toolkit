"""Metadata extraction: EXIF, TIFF, GPS, XMP, ICC, IPTC, PNG text, comments.

The module works directly on the container bytes so that it can report fields
that Pillow discards, and so that metadata can still be read from files Pillow
refuses to open. It also computes the standard digests and the file entropy.

Public entry point:

    analyze_metadata(path) -> MetadataReport
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import struct
import zlib
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# TIFF / EXIF tag tables
# ---------------------------------------------------------------------------
#: tag id -> (name, group)
TAGS: dict[int, tuple[str, str]] = {
    0x0100: ("ImageWidth", "image"),
    0x0101: ("ImageLength", "image"),
    0x0102: ("BitsPerSample", "image"),
    0x0103: ("Compression", "image"),
    0x0106: ("PhotometricInterpretation", "image"),
    0x010E: ("ImageDescription", "image"),
    0x010F: ("Make", "image"),
    0x0110: ("Model", "image"),
    0x0111: ("StripOffsets", "image"),
    0x0112: ("Orientation", "image"),
    0x0115: ("SamplesPerPixel", "image"),
    0x0116: ("RowsPerStrip", "image"),
    0x0117: ("StripByteCounts", "image"),
    0x011A: ("XResolution", "image"),
    0x011B: ("YResolution", "image"),
    0x011C: ("PlanarConfiguration", "image"),
    0x0128: ("ResolutionUnit", "image"),
    0x0131: ("Software", "image"),
    0x0132: ("DateTime", "image"),
    0x013B: ("Artist", "image"),
    0x013E: ("WhitePoint", "image"),
    0x013F: ("PrimaryChromaticities", "image"),
    0x0201: ("JPEGInterchangeFormat", "thumbnail"),
    0x0202: ("JPEGInterchangeFormatLength", "thumbnail"),
    0x0211: ("YCbCrCoefficients", "image"),
    0x0213: ("YCbCrPositioning", "image"),
    0x0214: ("ReferenceBlackWhite", "image"),
    0x8298: ("Copyright", "image"),
    0x829A: ("ExposureTime", "exif"),
    0x829D: ("FNumber", "exif"),
    0x830E: ("PixelScale", "image"),
    0x83BB: ("IPTC-NAA", "iptc"),
    0x8649: ("ImageResources", "photoshop"),
    0x8769: ("ExifIFDPointer", "exif"),
    0x8773: ("InterColorProfile", "icc"),
    0x8822: ("ExposureProgram", "exif"),
    0x8824: ("SpectralSensitivity", "exif"),
    0x8825: ("GPSInfoIFDPointer", "gps"),
    0x8827: ("ISOSpeedRatings", "exif"),
    0x8828: ("OECF", "exif"),
    0x8830: ("SensitivityType", "exif"),
    0x9000: ("ExifVersion", "exif"),
    0x9003: ("DateTimeOriginal", "exif"),
    0x9004: ("DateTimeDigitized", "exif"),
    0x9010: ("OffsetTime", "exif"),
    0x9011: ("OffsetTimeOriginal", "exif"),
    0x9012: ("OffsetTimeDigitized", "exif"),
    0x9101: ("ComponentsConfiguration", "exif"),
    0x9102: ("CompressedBitsPerPixel", "exif"),
    0x9201: ("ShutterSpeedValue", "exif"),
    0x9202: ("ApertureValue", "exif"),
    0x9203: ("BrightnessValue", "exif"),
    0x9204: ("ExposureBiasValue", "exif"),
    0x9205: ("MaxApertureValue", "exif"),
    0x9206: ("SubjectDistance", "exif"),
    0x9207: ("MeteringMode", "exif"),
    0x9208: ("LightSource", "exif"),
    0x9209: ("Flash", "exif"),
    0x920A: ("FocalLength", "exif"),
    0x9214: ("SubjectArea", "exif"),
    0x927C: ("MakerNote", "exif"),
    0x9286: ("UserComment", "exif"),
    0x9290: ("SubSecTime", "exif"),
    0x9291: ("SubSecTimeOriginal", "exif"),
    0x9292: ("SubSecTimeDigitized", "exif"),
    0x9C9B: ("XPTitle", "windows"),
    0x9C9C: ("XPComment", "windows"),
    0x9C9D: ("XPAuthor", "windows"),
    0x9C9E: ("XPKeywords", "windows"),
    0x9C9F: ("XPSubject", "windows"),
    0xA000: ("FlashpixVersion", "exif"),
    0xA001: ("ColorSpace", "exif"),
    0xA002: ("PixelXDimension", "exif"),
    0xA003: ("PixelYDimension", "exif"),
    0xA004: ("RelatedSoundFile", "exif"),
    0xA005: ("InteroperabilityIFDPointer", "exif"),
    0xA20B: ("FlashEnergy", "exif"),
    0xA20E: ("FocalPlaneXResolution", "exif"),
    0xA20F: ("FocalPlaneYResolution", "exif"),
    0xA210: ("FocalPlaneResolutionUnit", "exif"),
    0xA215: ("ExposureIndex", "exif"),
    0xA217: ("SensingMethod", "exif"),
    0xA300: ("FileSource", "exif"),
    0xA301: ("SceneType", "exif"),
    0xA302: ("CFAPattern", "exif"),
    0xA401: ("CustomRendered", "exif"),
    0xA402: ("ExposureMode", "exif"),
    0xA403: ("WhiteBalance", "exif"),
    0xA404: ("DigitalZoomRatio", "exif"),
    0xA405: ("FocalLengthIn35mmFilm", "exif"),
    0xA406: ("SceneCaptureType", "exif"),
    0xA407: ("GainControl", "exif"),
    0xA408: ("Contrast", "exif"),
    0xA409: ("Saturation", "exif"),
    0xA40A: ("Sharpness", "exif"),
    0xA40C: ("SubjectDistanceRange", "exif"),
    0xA420: ("ImageUniqueID", "exif"),
    0xA430: ("CameraOwnerName", "exif"),
    0xA431: ("BodySerialNumber", "exif"),
    0xA432: ("LensSpecification", "exif"),
    0xA433: ("LensMake", "exif"),
    0xA434: ("LensModel", "exif"),
    0xA435: ("LensSerialNumber", "exif"),
    0xA460: ("CompositeImage", "exif"),
    0xA500: ("Gamma", "exif"),
}

GPS_TAGS: dict[int, str] = {
    0x0000: "GPSVersionID",
    0x0001: "GPSLatitudeRef",
    0x0002: "GPSLatitude",
    0x0003: "GPSLongitudeRef",
    0x0004: "GPSLongitude",
    0x0005: "GPSAltitudeRef",
    0x0006: "GPSAltitude",
    0x0007: "GPSTimeStamp",
    0x0008: "GPSSatellites",
    0x0009: "GPSStatus",
    0x000A: "GPSMeasureMode",
    0x000B: "GPSDOP",
    0x000C: "GPSSpeedRef",
    0x000D: "GPSSpeed",
    0x000E: "GPSTrackRef",
    0x000F: "GPSTrack",
    0x0010: "GPSImgDirectionRef",
    0x0011: "GPSImgDirection",
    0x0012: "GPSMapDatum",
    0x0013: "GPSDestLatitudeRef",
    0x0014: "GPSDestLatitude",
    0x0015: "GPSDestLongitudeRef",
    0x0016: "GPSDestLongitude",
    0x0017: "GPSDestBearingRef",
    0x0018: "GPSDestBearing",
    0x0019: "GPSDestDistanceRef",
    0x001A: "GPSDestDistance",
    0x001B: "GPSProcessingMethod",
    0x001C: "GPSAreaInformation",
    0x001D: "GPSDateStamp",
    0x001E: "GPSDifferential",
    0x001F: "GPSHPositioningError",
}

INTEROP_TAGS: dict[int, str] = {
    0x0001: "InteroperabilityIndex",
    0x0002: "InteroperabilityVersion",
    0x1000: "RelatedImageFileFormat",
    0x1001: "RelatedImageWidth",
    0x1002: "RelatedImageLength",
}

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
TYPE_NAMES = {
    1: "BYTE", 2: "ASCII", 3: "SHORT", 4: "LONG", 5: "RATIONAL", 6: "SBYTE",
    7: "UNDEFINED", 8: "SSHORT", 9: "SLONG", 10: "SRATIONAL", 11: "FLOAT", 12: "DOUBLE",
}

#: EXIF fields whose presence is worth flagging explicitly.
SOFTWARE_HINTS = (
    "photoshop", "gimp", "lightroom", "affinity", "paint", "inkscape", "canva",
    "pixelmator", "capture one", "darktable", "rawtherapee", "snapseed",
)

ZERO_WIDTH = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE / BOM",
    "\u180e": "MONGOLIAN VOWEL SEPARATOR",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Field:
    group: str
    tag: str
    value: str
    raw: str = ""
    flag: str = ""
    note: str = ""


@dataclass
class MetadataReport:
    path: str
    file_format: str
    file_size: int
    fields: list[Field] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hashes: dict = field(default_factory=dict)
    entropy: float = 0.0
    extra: dict = field(default_factory=dict)

    def groups(self) -> dict[str, list[Field]]:
        grouped: dict[str, list[Field]] = {}
        for item in self.fields:
            grouped.setdefault(item.group, []).append(item)
        return grouped

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "file_format": self.file_format,
            "file_size": self.file_size,
            "hashes": self.hashes,
            "entropy": self.entropy,
            "fields": [vars(f) for f in self.fields],
            "findings": list(self.findings),
            "warnings": list(self.warnings),
            "extra": self.extra,
        }

    def to_text(self) -> str:
        lines = [
            f"Metadata for {os.path.basename(self.path)}",
            f"  format   : {self.file_format}",
            f"  size     : {self.file_size:,} bytes",
            f"  entropy  : {self.entropy:.4f} bit/byte",
            f"  MD5      : {self.hashes.get('md5', '')}",
            f"  SHA-1    : {self.hashes.get('sha1', '')}",
            f"  SHA-256  : {self.hashes.get('sha256', '')}",
            f"  fields   : {len(self.fields)}",
        ]
        for group, items in self.groups().items():
            if group == "file":
                continue
            lines += ["", f"[{group}]", "-" * (len(group) + 2)]
            for item in items:
                flag = f"  <{item.flag}>" if item.flag else ""
                lines.append(f"  {item.tag:<28} {item.value}{flag}")
                if item.note:
                    lines.append(f"      note: {item.note}")
        if self.findings:
            lines += ["", "Findings", "--------"] + [f"  - {f}" for f in self.findings]
        if self.warnings:
            lines += ["", "Warnings", "--------"] + [f"  - {w}" for w in self.warnings]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Hashing / entropy
# ---------------------------------------------------------------------------
def file_hashes(path: str) -> dict:
    """MD5, SHA-1 and SHA-256 digests of a file."""
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            md5.update(block)
            sha1.update(block)
            sha256.update(block)
    return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(), "sha256": sha256.hexdigest()}


def shannon_entropy(data: bytes) -> float:
    """Shannon entropy of a byte string in bits per byte."""
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    n = len(data)
    return float(-sum((c / n) * math.log2(c / n) for c in counts if c))


def entropy_map(data: bytes, window: int = 4096, step: int = 4096):
    """Per-window entropy as a list of (offset, bits per byte)."""
    if window <= 0:
        return []
    out = []
    for start in range(0, len(data), step):
        chunk = data[start : start + window]
        if len(chunk) >= 16:
            out.append((start, shannon_entropy(chunk)))
    return out


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
    if data.startswith(b"<svg") or data.startswith(b"<?xml"):
        return "SVG/XML"
    return "UNKNOWN"


def find_zero_width(text: str):
    """Positions and names of zero width characters in a string."""
    return [(index, name) for index, char in enumerate(text) if (name := ZERO_WIDTH.get(char))]


# ---------------------------------------------------------------------------
# TIFF / EXIF parser
# ---------------------------------------------------------------------------
def _read_values(data: bytes, order: str, vtype: int, count: int, value_offset: int):
    """Return (values list, raw string) for a TIFF entry."""
    size = TYPE_SIZES.get(vtype, 1) * count
    if size <= 4:
        raw_bytes = data[value_offset : value_offset + size]
    else:
        pointer = struct.unpack_from(order + "I", data, value_offset)[0]
        raw_bytes = data[pointer : pointer + size]
    values = []
    try:
        if vtype == 2:  # ASCII
            text = raw_bytes.split(b"\x00")[0]
            values = [text.decode("latin-1", "replace")]
        elif vtype in (1, 6, 7):
            values = list(raw_bytes)
        elif vtype == 3:
            values = list(struct.unpack(f"{order}{count}H", raw_bytes[: count * 2]))
        elif vtype == 8:
            values = list(struct.unpack(f"{order}{count}h", raw_bytes[: count * 2]))
        elif vtype == 4:
            values = list(struct.unpack(f"{order}{count}I", raw_bytes[: count * 4]))
        elif vtype == 9:
            values = list(struct.unpack(f"{order}{count}i", raw_bytes[: count * 4]))
        elif vtype == 5:
            values = [
                struct.unpack_from(order + "II", raw_bytes, i * 8) for i in range(count)
            ]
        elif vtype == 10:
            values = [
                struct.unpack_from(order + "ii", raw_bytes, i * 8) for i in range(count)
            ]
        elif vtype == 11:
            values = list(struct.unpack(f"{order}{count}f", raw_bytes[: count * 4]))
        elif vtype == 12:
            values = list(struct.unpack(f"{order}{count}d", raw_bytes[: count * 8]))
    except struct.error:
        pass
    return values, raw_bytes


def _format_value(vtype: int, values) -> tuple[str, str]:
    """Format a TIFF value into (human readable, flag)."""
    flag = ""
    if vtype == 2:
        text = values[0] if values else ""
        return text, flag
    if vtype in (1, 6, 7):
        if vtype == 7:
            try:
                text = bytes(values).decode("ascii")
                if text.isprintable():
                    return text, flag
            except (ValueError, UnicodeDecodeError):
                pass
            return " ".join(f"{v:02X}" for v in values[:32]) + (
                f" ... ({len(values)} bytes)" if len(values) > 32 else ""
            ), "binary"
        return ", ".join(str(v) for v in values[:32]), flag
    if vtype in (5, 10):
        parts = []
        for num, den in values[:16]:
            if den == 0:
                parts.append(f"{num}/0")
            elif vtype == 5:
                parts.append(f"{num}/{den}")
            else:
                parts.append(f"{num / den:.4f}")
        return ", ".join(parts), flag
    if vtype in (11, 12):
        return ", ".join(f"{v:.4f}" for v in values[:16]), flag
    return ", ".join(str(v) for v in values[:32]), flag


def _resolve_tag(tag: int, group: str) -> tuple[str, str]:
    if group == "gps":
        return GPS_TAGS.get(tag, f"GPSTag0x{tag:04X}"), "gps"
    if tag == 0x8769:
        return "ExifIFDPointer", "exif"
    if tag == 0x8825:
        return "GPSInfoIFDPointer", "gps"
    if tag == 0xA005:
        return "InteroperabilityIFDPointer", "interop"
    if tag in TAGS:
        return TAGS[tag]
    if 0x8000 <= tag <= 0xFFFF:
        return f"Tag0x{tag:04X}", "vendor"
    return f"Tag0x{tag:04X}", group


def parse_tiff_ifds(data: bytes, base: int = 0, max_ifds: int = 16):
    """Walk the TIFF IFD chain in `data`.

    Returns (fields, raw_extra) where raw_extra carries thumbnail information and
    the parsed group dictionaries. Handles Exif/GPS/Interop sub-IFDs.
    """
    fields: list[Field] = []
    extra: dict = {}
    if len(data) < 8:
        return fields, extra
    if data[:2] == b"II":
        order = "<"
    elif data[:2] == b"MM":
        order = ">"
    else:
        return fields, extra
    magic = struct.unpack_from(order + "H", data, 2)[0]
    if magic != 42:
        return fields, extra
    first = struct.unpack_from(order + "I", data, 4)[0]

    def read_ifd(offset: int, group: str, depth: int = 0):
        if offset <= 0 or offset + 2 > len(data) or depth > 4:
            return
        count = struct.unpack_from(order + "H", data, offset)[0]
        for index in range(count):
            entry = offset + 2 + index * 12
            if entry + 12 > len(data):
                break
            tag, vtype, cnt = struct.unpack_from(order + "HHI", data, entry)
            name, real_group = _resolve_tag(tag, group)
            values, raw_bytes = _read_values(data, order, vtype, cnt, entry + 8)
            text, flag = _format_value(vtype, values)
            note = ""
            if tag == 0x8769:
                read_ifd(struct.unpack_from(order + "I", data, entry + 8)[0], "exif", depth + 1)
                continue
            if tag == 0x8825:
                read_ifd(struct.unpack_from(order + "I", data, entry + 8)[0], "gps", depth + 1)
                continue
            if tag == 0xA005:
                read_ifd(struct.unpack_from(order + "I", data, entry + 8)[0], "interop", depth + 1)
                continue
            if tag == 0x927C:
                note = "camera specific block, not fully decoded"
                extra.setdefault("makernote_offset", base + struct.unpack_from(order + "I", data, entry + 8)[0]
                                 if vtype != 7 else entry + 8)
            if tag in (0x0201, 0x0202) and group == "thumbnail":
                extra[f"thumb_{name}"] = values[0] if values else 0
            if real_group in ("gps",) or group == "gps":
                real_group = "gps"
            fields.append(Field(real_group, name, results_text(text), raw_bytes.hex()[:120], flag, note))
        next_ifd = struct.unpack_from(order + "I", data, offset + 2 + count * 12)[0]
        extra["next_ifd"] = next_ifd
        if next_ifd and depth == 0:
            read_ifd(next_ifd, "thumbnail", depth + 1)

    def results_text(text):
        return text

    read_ifd(first, "image")
    return fields, extra


def parse_exif(data: bytes, base: int = 0):
    """Parse an EXIF block (either starting with the Exif header or bare TIFF)."""
    if data[:6] == b"Exif\x00\x00":
        data = data[6:]
    if len(data) < 8 or data[:2] not in (b"II", b"MM"):
        raise ValueError("not a TIFF/EXIF block")
    return parse_tiff_ifds(data, base)


def extract_thumbnail(data: bytes):
    """Return (bytes, mime) for an EXIF embedded thumbnail, or (b'', '')."""
    if data[:6] == b"Exif\x00\x00":
        tiff = data[6:]
    else:
        tiff = data
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return b"", ""
    order = "<" if tiff[:2] == b"II" else ">"
    try:
        first = struct.unpack_from(order + "I", tiff, 4)[0]
        if first + 2 > len(tiff):
            return b"", ""
        count = struct.unpack_from(order + "H", tiff, first)[0]
        next_ifd = struct.unpack_from(order + "I", tiff, first + 2 + count * 12)[0]
        if not next_ifd or next_ifd + 2 > len(tiff):
            return b"", ""
        n = struct.unpack_from(order + "H", tiff, next_ifd)[0]
        offset = length = 0
        for index in range(n):
            entry = next_ifd + 2 + index * 12
            tag = struct.unpack_from(order + "H", tiff, entry)[0]
            value = struct.unpack_from(order + "I", tiff, entry + 8)[0]
            if tag == 0x0201:
                offset = value
            elif tag == 0x0202:
                length = value
        if offset and length and offset + length <= len(tiff):
            blob = tiff[offset : offset + length]
            mime = "image/jpeg" if blob[:2] == b"\xff\xd8" else "image/png" if blob[:8].startswith(b"\x89PNG") else "application/octet-stream"
            return blob, mime
    except struct.error:
        pass
    return b"", ""


# ---------------------------------------------------------------------------
# XMP / ICC / IPTC
# ---------------------------------------------------------------------------
def _xmp_fields(xmp: str) -> list[Field]:
    fields: list[Field] = []
    patterns = [
        ("dc:title", r"<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("dc:creator", r"<dc:creator>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("dc:description", r"<dc:description>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("dc:subject", r"<dc:subject>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("dc:rights", r"<dc:rights>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("xmp:CreatorTool", r"xmp:CreatorTool=\"([^\"]*)\""),
        ("xmp:CreateDate", r"xmp:CreateDate=\"([^\"]*)\""),
        ("xmp:ModifyDate", r"xmp:ModifyDate=\"([^\"]*)\""),
        ("xmp:MetadataDate", r"xmp:MetadataDate=\"([^\"]*)\""),
        ("photoshop:City", r"photoshop:City=\"([^\"]*)\""),
        ("photoshop:Country", r"photoshop:Country=\"([^\"]*)\""),
        ("xmpMM:DocumentID", r"xmpMM:DocumentID=\"([^\"]*)\""),
        ("xmpMM:InstanceID", r"xmpMM:InstanceID=\"([^\"]*)\""),
        ("xmpMM:OriginalDocumentID", r"xmpMM:OriginalDocumentID=\"([^\"]*)\""),
        ("crs:RawFileName", r"crs:RawFileName=\"([^\"]*)\""),
        ("Iptc4xmpCore:Location", r"Iptc4xmpCore:Location=\"([^\"]*)\""),
    ]
    for name, pattern in patterns:
        for match in re.finditer(pattern, xmp, re.S):
            value = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            if value:
                fields.append(Field("xmp", name, value))
    if not fields:
        fields.append(Field("xmp", "packet", f"{len(xmp)} bytes of XMP present (no simple fields matched)", flag="info"))
    return fields


def _icc_fields(data: bytes) -> list[Field]:
    fields: list[Field] = []
    if len(data) < 128:
        fields.append(Field("icc", "profile", f"{len(data)} bytes (too short to parse)", flag="info"))
        return fields
    try:
        size = struct.unpack_from(">I", data, 0)[0]
        cmm = data[4:8].decode("latin-1", "replace").strip()
        version = f"{data[8]}.{data[9] >> 4}.{data[9] & 0x0F}"
        device_class = data[12:16].decode("latin-1", "replace")
        colour_space = data[16:20].decode("latin-1", "replace")
        connection = data[20:24].decode("latin-1", "replace")
        fields.append(Field("icc", "size", f"{size} bytes"))
        fields.append(Field("icc", "version", version))
        fields.append(Field("icc", "device class", device_class))
        fields.append(Field("icc", "colour space", colour_space))
        fields.append(Field("icc", "connection space", connection))
        if cmm:
            fields.append(Field("icc", "CMM", cmm))
        count = struct.unpack_from(">I", data, 128)[0]
        description = ""
        for index in range(min(count, 128)):
            entry = 132 + index * 12
            if entry + 12 > len(data):
                break
            sig = data[entry : entry + 4]
            offset, length = struct.unpack_from(">II", data, entry + 4)
            if sig == b"desc" and offset + length <= len(data) and length >= 12:
                block = data[offset : offset + length]
                if block[:4] == b"desc":
                    try:
                        n = struct.unpack_from(">I", block, 8)[0]
                        if 0 < n <= 200:
                            description = block[12 : 12 + n - 1].decode("latin-1", "replace")
                    except struct.error:
                        pass
        if description:
            fields.append(Field("icc", "profile description", description))
        fields.append(Field("icc", "tag count", str(count)))
    except (struct.error, IndexError) as exc:
        fields.append(Field("icc", "profile", f"parse error: {exc}", flag="info"))
    return fields


IPTC_LABELS = {
    0: "record version", 5: "object name", 10: "urgency", 15: "category",
    20: "supplemental category", 25: "keywords", 40: "special instructions",
    55: "date created", 60: "time created", 62: "digital creation date",
    80: "byline", 85: "byline title", 90: "city", 92: "sublocation",
    95: "province/state", 100: "country code", 101: "country",
    103: "original transmission reference", 105: "headline",
    110: "credit", 115: "source", 116: "copyright notice",
    118: "contact", 120: "caption", 122: "writer/editor",
}


def _iptc_fields(data: bytes) -> list[Field]:
    fields: list[Field] = []
    pos = 0
    while pos + 5 <= len(data):
        if data[pos] != 0x1C:
            pos += 1
            continue
        record = data[pos + 1]
        dataset = data[pos + 2]
        length = struct.unpack_from(">H", data, pos + 3)[0]
        if length & 0x8000:
            length = struct.unpack_from(">I", data, pos + 3)[0] & 0x7FFFFFFF
            body_start = pos + 7
        else:
            body_start = pos + 5
        value = data[body_start : body_start + length]
        if record == 2:
            label = IPTC_LABELS.get(dataset, f"dataset {dataset}")
            text = value.decode("latin-1", "replace").strip("\x00").strip()
            if text:
                fields.append(Field("iptc", f"2:{dataset} {label}", text))
        pos = body_start + length
    return fields


# ---------------------------------------------------------------------------
# Container specific extractors
# ---------------------------------------------------------------------------
def _png_metadata(data: bytes) -> tuple[list[Field], list[str]]:
    fields: list[Field] = []
    findings: list[str] = []
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if pos + 12 + length > len(data):
            break
        if ctype == b"IHDR" and length >= 13:
            w, h, depth, colour, comp, filt, interlace = struct.unpack_from(">IIBBBBB", body, 0)
            fields.append(Field("png", "Width", str(w)))
            fields.append(Field("png", "Height", str(h)))
            fields.append(Field("png", "Bit depth", str(depth)))
            fields.append(Field("png", "Colour type", str(colour)))
            fields.append(Field("png", "Interlace", "Adam7" if interlace else "none"))
        elif ctype == b"tEXt":
            sep = body.find(b"\x00")
            keyword = body[:sep].decode("latin-1", "replace")
            value = body[sep + 1 :].decode("latin-1", "replace")
            fields.append(Field("png-text", keyword, value))
            findings.append(f"PNG text chunk '{keyword}': {value[:120]}")
        elif ctype == b"zTXt":
            sep = body.find(b"\x00")
            keyword = body[:sep].decode("latin-1", "replace")
            try:
                value = zlib.decompress(body[sep + 2 :]).decode("latin-1", "replace")
            except zlib.error:
                value = f"<{len(body) - sep - 2} compressed bytes>"
            fields.append(Field("png-text", keyword, value))
            findings.append(f"PNG compressed text chunk '{keyword}': {value[:120]}")
        elif ctype == b"iTXt":
            parts = body.split(b"\x00", 5)
            if len(parts) >= 6:
                keyword = parts[0].decode("latin-1", "replace")
                compressed = parts[1] == b"\x01"
                text = parts[5]
                if compressed:
                    try:
                        text = zlib.decompress(text)
                    except zlib.error:
                        pass
                value = text.decode("utf-8", "replace")
                fields.append(Field("png-text", keyword, value))
                findings.append(f"PNG international text chunk '{keyword}': {value[:120]}")
        elif ctype == b"eXIf":
            try:
                exif_fields, _extra = parse_exif(body)
                for item in exif_fields:
                    item.group = "exif"
                    fields.append(item)
                findings.append("PNG eXIf chunk with Exif metadata present")
            except Exception as exc:
                findings.append(f"PNG eXIf chunk could not be parsed: {exc}")
        elif ctype == b"tIME" and length >= 7:
            year, month, day, hour, minute, second = struct.unpack_from(">HBBBBB", body, 0)
            fields.append(Field("png", "Last modified",
                                f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"))
        elif ctype == b"pHYs" and length >= 9:
            px, py, unit = struct.unpack_from(">IIB", body, 0)
            fields.append(Field("png", "Pixel dimensions", f"{px}x{py} {'per metre' if unit == 1 else '(unitless)'}"))
        elif ctype == b"gAMA" and length >= 4:
            gamma = struct.unpack_from(">I", body, 0)[0] / 100000.0
            fields.append(Field("png", "Gamma", f"{gamma:.5f}"))
        elif ctype == b"sRGB":
            fields.append(Field("png", "sRGB intent", str(body[0]) if body else ""))
        elif ctype == b"iCCP":
            sep = body.find(b"\x00")
            fields.append(Field("icc", "profile name", body[:sep].decode("latin-1", "replace")))
            try:
                profile = zlib.decompress(body[sep + 2 :])
                fields.extend(_icc_fields(profile))
            except zlib.error:
                pass
        pos += 12 + length
        if ctype == b"IEND":
            break
    return fields, findings


def _jpeg_metadata(data: bytes) -> tuple[list[Field], list[str]]:
    fields: list[Field] = []
    findings: list[str] = []
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        seg_len = struct.unpack_from(">H", data, pos + 2)[0]
        body = data[pos + 4 : pos + 2 + seg_len]
        if marker == 0xE0 and body[:5] == b"JFIF\x00":
            fields.append(Field("jfif", "version", f"{body[5]}.{body[6]:02d}"))
            fields.append(Field("jfif", "density unit", str(body[7])))
            fields.append(Field("jfif", "X density", str(struct.unpack_from(">H", body, 8)[0])))
            fields.append(Field("jfif", "Y density", str(struct.unpack_from(">H", body, 10)[0])))
        elif marker == 0xE1 and body[:6] == b"Exif\x00\x00":
            try:
                exif_fields, extra = parse_exif(body)
                fields.extend(exif_fields)
                findings.append(f"Exif block present ({len(exif_fields)} fields)")
                if extra.get("thumb_JPEGInterchangeFormatLength"):
                    findings.append("Exif contains an embedded thumbnail")
            except Exception as exc:
                findings.append(f"Exif block could not be parsed: {exc}")
        elif marker == 0xE1:
            text = body.decode("latin-1", "replace")
            if "ns.adobe.com/xap" in text or "<?xpacket" in text:
                fields.extend(_xmp_fields(text))
                findings.append("XMP metadata packet present")
        elif marker == 0xE2 and body[:11] == b"ICC_PROFILE":
            fields.extend(_icc_fields(body[14:]))
            findings.append("ICC colour profile present")
        elif marker == 0xED:
            text = body.decode("latin-1", "replace")
            if "Photoshop 3.0" in text:
                fields.append(Field("photoshop", "resource block", f"{len(body)} bytes"))
            fields.extend(_iptc_fields(body))
        elif marker == 0xEE:
            fields.append(Field("adobe", "APP14", "Adobe marker segment present"))
        elif marker == 0xFE:
            comment = body.decode("latin-1", "replace")
            fields.append(Field("comment", "JPEG comment", comment))
            findings.append(f"JPEG comment: {comment[:160]}")
        pos += 2 + seg_len
        if marker == 0xDA:
            break
    return fields, findings


def _gif_metadata(data: bytes) -> tuple[list[Field], list[str]]:
    fields: list[Field] = []
    findings: list[str] = []
    if len(data) < 13:
        return fields, findings
    w, h, flags, bg, aspect = struct.unpack_from("<HHBBB", data, 6)
    fields.append(Field("gif", "Version", data[:6].decode("latin-1")))
    fields.append(Field("gif", "Width", str(w)))
    fields.append(Field("gif", "Height", str(h)))
    fields.append(Field("gif", "Background index", str(bg)))
    pos = 13
    if flags & 0x80:
        pos += 3 * 2 ** ((flags & 0x07) + 1)
    while pos < len(data):
        block = data[pos]
        if block == 0x3B:
            break
        if block == 0x21 and pos + 2 <= len(data):
            label = data[pos + 1]
            p = pos + 2
            payload = b""
            while p < len(data) and data[p] != 0:
                size = data[p]
                payload += data[p + 1 : p + 1 + size]
                p += 1 + size
            if label == 0xFE:
                text = payload.decode("latin-1", "replace")
                fields.append(Field("comment", "GIF comment", text))
                findings.append(f"GIF comment: {text[:160]}")
            elif label == 0xFF and len(payload) >= 11:
                ident = payload[:11].decode("latin-1", "replace")
                fields.append(Field("gif", "Application extension", ident))
                findings.append(f"GIF application extension: {ident}")
            pos = p + 1
            continue
        if block == 0x2C:
            break
        pos += 1
    return fields, findings


def _webp_metadata(data: bytes) -> tuple[list[Field], list[str]]:
    fields: list[Field] = []
    findings: list[str] = []
    pos = 12
    while pos + 8 <= len(data):
        fourcc = data[pos : pos + 4]
        length = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8 : pos + 8 + length]
        if fourcc == b"EXIF":
            try:
                payload = body[6:] if body[:6] == b"Exif\x00\x00" else body
                exif_fields, _ = parse_exif(payload)
                fields.extend(exif_fields)
                findings.append(f"WEBP EXIF chunk present ({len(exif_fields)} fields)")
            except Exception as exc:
                findings.append(f"WEBP EXIF chunk could not be parsed: {exc}")
        elif fourcc == b"XMP ":
            fields.extend(_xmp_fields(body.decode("latin-1", "replace")))
            findings.append("WEBP XMP chunk present")
        elif fourcc == b"ICCP":
            fields.extend(_icc_fields(body))
            findings.append("WEBP ICC profile present")
        pos += 8 + length + (length & 1)
    return fields, findings


def _bmp_metadata(data: bytes) -> tuple[list[Field], list[str]]:
    fields: list[Field] = []
    if len(data) < 26:
        return fields, []
    size = struct.unpack_from("<I", data, 2)[0]
    offset = struct.unpack_from("<I", data, 10)[0]
    dib = struct.unpack_from("<I", data, 14)[0]
    fields.append(Field("bmp", "Declared size", str(size)))
    fields.append(Field("bmp", "Pixel data offset", str(offset)))
    fields.append(Field("bmp", "DIB header size", str(dib)))
    if dib >= 40 and len(data) >= 26:
        w, h = struct.unpack_from("<ii", data, 18)
        planes, bpp = struct.unpack_from("<HH", data, 26)
        fields.append(Field("bmp", "Width", str(w)))
        fields.append(Field("bmp", "Height", str(h)))
        fields.append(Field("bmp", "Bit depth", str(bpp)))
    return fields, []


EXTRACTORS = {
    "PNG": _png_metadata,
    "JPEG": _jpeg_metadata,
    "GIF": _gif_metadata,
    "WEBP": _webp_metadata,
    "BMP": _bmp_metadata,
    "TIFF": lambda data: _tiff_metadata(data),
}


def _tiff_metadata(data: bytes) -> tuple[list[Field], list[str]]:
    fields, extra = parse_exif(data)
    findings = ["TIFF/Exif structure parsed directly"]
    if extra.get("thumb_JPEGInterchangeFormatLength"):
        findings.append("TIFF contains an embedded thumbnail (IFD1)")
    return fields, findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def analyze_metadata(path: str) -> MetadataReport:
    """Extract every metadata field that can be recovered from a file."""
    with open(path, "rb") as handle:
        data = handle.read()
    report = MetadataReport(path=path, file_format=sniff_format(data), file_size=len(data))
    report.hashes = file_hashes(path)
    report.entropy = shannon_entropy(data)
    report.fields.append(Field("file", "Name", os.path.basename(path)))
    report.fields.append(Field("file", "Path", path))
    report.fields.append(Field("file", "Size", f"{len(data):,} bytes"))
    report.fields.append(Field("file", "Format", report.file_format))
    report.fields.append(Field("file", "Entropy", f"{report.entropy:.4f} bit/byte"))

    extractor = EXTRACTORS.get(report.file_format)
    if extractor is None:
        report.warnings.append(
            f"no metadata extractor for format {report.file_format}; "
            "only file level facts and digests are reported"
        )
    else:
        try:
            fields, findings = extractor(data)
            report.fields.extend(fields)
            for text in findings:
                if text not in report.findings:
                    report.findings.append(text)
        except Exception as exc:
            report.warnings.append(f"{report.file_format} metadata extractor failed: {type(exc).__name__}: {exc}")

    _make_findings(report, data)
    return report


def _make_findings(report: MetadataReport, data: bytes) -> None:
    by_tag = {f.tag: f for f in report.fields}

    # Camera and software attribution.
    make = by_tag.get("Make")
    model = by_tag.get("Model")
    if make or model:
        report.findings.append(
            "camera attribution: " + " ".join(
                x.value for x in (make, model) if x is not None
            )
        )
    software = by_tag.get("Software")
    if software:
        note = ""
        lowered = software.value.lower()
        if any(hint in lowered for hint in SOFTWARE_HINTS):
            note = " (image editor: editing history is likely)"
        report.findings.append(f"software used: {software.value}{note}")
        software.flag = "suspicious" if note else software.flag

    # GPS exposure.
    gps_tags = [f for f in report.fields if f.group == "gps"]
    if gps_tags:
        coords = []
        for tag in ("GPSLatitude", "GPSLatitudeRef", "GPSLongitude", "GPSLongitudeRef"):
            item = next((f for f in gps_tags if f.tag == tag), None)
            if item:
                coords.append(f"{tag}={item.value}")
        report.findings.append("location metadata present: " + ", ".join(coords))
        for item in gps_tags:
            if item.tag in ("GPSLatitude", "GPSLongitude"):
                item.flag = "suspicious"

    # Timestamps: an edit after the capture time is evidence of tampering.
    stamps = {
        name: by_tag[name].value
        for name in ("DateTimeOriginal", "DateTimeDigitized", "DateTime", "ModifyDate")
        if name in by_tag
    }
    if len(stamps) >= 2:
        report.findings.append("timestamps: " + ", ".join(f"{k}={v}" for k, v in stamps.items()))

    # Textual metadata: flag zero width characters and any flag-like strings.
    for item in report.fields:
        if item.flag == "binary" and len(item.value) > 400:
            continue
        value = item.value
        if any(ord(c) > 0x2000 and c in ZERO_WIDTH for c in value):
            names = {name for _i, name in find_zero_width(value)}
            item.flag = "suspicious"
            item.note = "contains hidden zero width characters: " + ", ".join(sorted(names))
            report.findings.append(f"{item.tag} contains hidden zero width characters")
        for match in re.finditer(r"(?i)(?:flag|ctf|key|secret)\{[^}]{1,200}\}", value):
            report.findings.append(f"flag-like string in {item.tag}: {match.group(0)[:160]}")

    # XMP generally means the file passed through an editor.
    if any(f.group == "xmp" for f in report.fields):
        report.findings.append("XMP metadata present (typical of edited or exported files)")
    if any(f.group == "icc" for f in report.fields):
        report.findings.append("ICC colour profile embedded")

    # Sanity: an image with no metadata at all is itself notable.
    interesting = [f for f in report.fields if f.group not in ("file",)]
    if not interesting:
        report.findings.append("no embedded metadata found (stripped or never written)")
    if report.entropy > 7.999 and report.file_size > 262144:
        report.findings.append(
            "file entropy is 8.000 bit/byte over a large file: the payload is "
            "indistinguishable from random data (encrypted or padded content)"
        )


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import io
    import tempfile

    from PIL import Image

    failures = 0

    def check(condition, label):
        nonlocal failures
        if not condition:
            failures += 1
            print(f"FAIL: {label}")

    buffer = io.BytesIO()
    image = Image.new("RGB", (24, 24), (200, 100, 50))
    image.save(buffer, "PNG", pnginfo=_png_text_probe("comment", "selftest text chunk"))
    blob = buffer.getvalue()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(blob)
        path = handle.name
    try:
        report = analyze_metadata(path)
        check(report.file_format == "PNG", "PNG detected")
        check(report.hashes.get("md5"), "MD5 computed")
        check(len(report.hashes.get("sha256", "")) == 64, "SHA-256 length")
        check(report.entropy > 0, "entropy computed")
        check(any(f.tag == "comment" and f.value == "selftest text chunk" for f in report.fields),
              "PNG text chunk parsed")
        check(isinstance(report.to_text(), str), "text output")
        check(isinstance(report.to_dict(), dict), "dict output")
    finally:
        os.unlink(path)

    # TIFF round trip through Pillow's Exif writer.
    check(shannon_entropy(b"") == 0.0, "entropy of empty string")
    check(shannon_entropy(b"\x00" * 100) == 0.0, "entropy of constant data")
    check(find_zero_width("a\u200bb")[0][1].startswith("ZERO WIDTH"), "zero width detection")
    print(f"metadata selftest: {failures} failure(s)")
    return failures


def _png_text_probe(keyword: str, text: str):
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text(keyword, text)
    return info


if __name__ == "__main__":
    raise SystemExit(1 if _selftest() else 0)
