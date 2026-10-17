#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the sample images used to demonstrate the workbench.

The samples deliberately cover several different carriers so that every code
path can be exercised without hunting for material:

    demo_blue_lsb.png           flag rendered as a picture inside the blue
                                least-significant bit plane (visual carrier)
    demo_blue_lsb_bytes.png     flag stored as bytes in the blue LSB plane
    demo_interleaved_rgb.png    flag interleaved across R, G and B at bit 1
    demo_width2_blue.png        flag stored as two bits per blue sample
    demo_metadata.jpg           ordinary JPEG carrying EXIF metadata
    demo_indexed.png            palette (P mode) PNG with a duplicate entry

Nothing here is malware: the embedded strings are plain-text markers used to
verify that the extraction and sweep functions behave correctly.
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FLAG = "flag{lsb_plane_is_not_encryption}"
WIDTH, HEIGHT = 720, 130


def write_bit_plane(plane: np.ndarray, bit: int, bits: np.ndarray) -> np.ndarray:
    """Copy `plane` with `bits` written, in order, into bit position `bit`."""
    flat = plane.ravel().astype(np.uint16)
    n = min(bits.size, flat.size)
    mask = np.uint16(1) << bit
    flat[:n] = (flat[: n] & ~mask) | (bits[:n].astype(np.uint16) << bit)
    return flat.reshape(plane.shape).astype(np.uint8)


def visual_carrier(rng: np.random.Generator) -> None:
    """The flag drawn as a bitmap and hidden in the blue LSB plane."""
    text = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(text)
    try:
        font = ImageFont.truetype("consola.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    draw.text((12, 40), FLAG, fill=255, font=font)
    plane = (np.array(text) > 127).astype(np.uint8)

    rgb = rng.integers(0, 256, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)
    rgb[..., 2] = write_bit_plane(rgb[..., 2], 0, plane.ravel())
    Image.fromarray(rgb, "RGB").save(os.path.join(HERE, "demo_blue_lsb.png"))
    Image.fromarray(plane * 255, "L").save(
        os.path.join(HERE, "demo_blue_lsb_extracted.png")
    )


def byte_carrier(rng: np.random.Generator) -> None:
    """The flag stored as a byte stream in the blue LSB plane."""
    rgb = rng.integers(0, 256, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)
    bits = np.unpackbits(np.frombuffer(FLAG.encode(), np.uint8), bitorder="big")
    rgb[..., 2] = write_bit_plane(rgb[..., 2], 0, bits)
    Image.fromarray(rgb, "RGB").save(os.path.join(HERE, "demo_blue_lsb_bytes.png"))


def interleaved_carrier(rng: np.random.Generator) -> None:
    """The flag interleaved across R, G and B at bit 1 (zsteg b1,rgb style)."""
    payload = (FLAG + "{interleaved}").encode()
    rgb = rng.integers(0, 256, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)
    bits = np.unpackbits(np.frombuffer(payload, np.uint8), bitorder="big")
    pad = (-bits.size) % 3
    if pad:
        bits = np.concatenate([bits, np.zeros(pad, np.uint8)])
    groups = bits.reshape(-1, 3)  # per pixel: R, G, B
    for channel in range(3):
        rgb[..., channel] = write_bit_plane(rgb[..., channel], 1, groups[:, channel])
    Image.fromarray(rgb, "RGB").save(os.path.join(HERE, "demo_interleaved_rgb.png"))


def multibit_carrier(rng: np.random.Generator) -> None:
    """The flag stored as two bits per blue sample, most significant bit first."""
    rgb = rng.integers(0, 256, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)
    bits = np.unpackbits(np.frombuffer(FLAG.encode(), np.uint8), bitorder="big")
    rgb[..., 2] = write_bit_plane(rgb[..., 2], 0, bits[1::2])  # second of each pair
    rgb[..., 2] = write_bit_plane(rgb[..., 2], 1, bits[0::2])  # first of each pair
    Image.fromarray(rgb, "RGB").save(os.path.join(HERE, "demo_width2_blue.png"))


def metadata_sample(rng: np.random.Generator) -> None:
    """An ordinary JPEG carrying EXIF metadata, for the metadata page."""
    arr = rng.integers(0, 256, size=(180, 240, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[271] = "Forensics Lab"          # Make
    exif[272] = "Reference Camera"       # Model
    exif[305] = "Image Forensics Workbench"  # Software
    exif[306] = "2026:01:01 00:00:00"    # DateTime
    exif[315] = "Sample Artist"          # Artist
    Image.fromarray(arr, "RGB").save(
        os.path.join(HERE, "demo_metadata.jpg"), "JPEG", quality=90, exif=exif
    )


def palette_sample(rng: np.random.Generator) -> None:
    """A palette image whose index plane and palette are worth inspecting."""
    palette = []
    for i in range(256):
        palette.extend([i, (i * 3) % 256, (i * 7) % 256])
    palette[3 * 200 : 3 * 200 + 3] = palette[3 * 201 : 3 * 201 + 3]  # duplicate
    indices = rng.integers(0, 256, size=(96, 96), dtype=np.uint8)
    image = Image.fromarray(indices, "P")
    image.putpalette(palette)
    image.save(os.path.join(HERE, "demo_indexed.png"))


def main() -> None:
    rng = np.random.default_rng(7)
    visual_carrier(rng)
    byte_carrier(rng)
    interleaved_carrier(rng)
    multibit_carrier(rng)
    metadata_sample(rng)
    palette_sample(rng)
    for name in (
        "demo_blue_lsb.png",
        "demo_blue_lsb_bytes.png",
        "demo_interleaved_rgb.png",
        "demo_width2_blue.png",
        "demo_metadata.jpg",
        "demo_indexed.png",
        "demo_blue_lsb_extracted.png",
    ):
        print("wrote", os.path.join(HERE, name))


if __name__ == "__main__":
    main()
