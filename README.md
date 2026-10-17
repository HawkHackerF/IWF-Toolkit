# Image Forensics Workbench

A desktop workbench for inspecting images the way a forensic analyst does:
metadata and container structure first, then bit-plane and least-significant-bit
(LSB) steganography, colour-channel statistics, palette and JPEG DCT analysis,
payload extraction, and re-embedding for verification.

The interface is built with Qt (PySide6). Drop a single image in and the
workbench shows every bit plane at once, decodes all common LSB carriers in the
background, and ranks the candidates, without blocking the UI.

> Typical task this tool was built to solve:
> "Isolate the least-significant bit of the **blue** channel (that bit becomes
> white, everything else black) and the flag appears as readable text."

## Installation

Python 3.10 or newer is recommended.

```powershell
pip install -r requirements.txt
```

The only dependencies are Qt (PySide6), NumPy and Pillow. There is no SciPy,
piexif, exifread, jpegio or scikit-image dependency: the incomplete-gamma
function used by the statistical tests and the baseline JPEG decoder used for
DCT analysis are implemented in the package.

## Running

```powershell
python ifw.py                       # empty window
python ifw.py image.png             # open an image immediately
python -m ifw                       # equivalent module entry point
run.bat                             # Windows launcher (drag a file onto it)
```

## Workflow

1. **Drag an image** onto the window, or use `Ctrl+O`. Any file the analysis
   layer understands can be opened (see _Supported formats_ below).
2. The **Overview** page summarises the file: format, geometry, hashes, entropy
   and a one-line verdict.
3. The **Bit Planes** page shows a montage of every bit plane (for an 8-bit RGB
   image that is one panel per channel and bit, plus per-bit colour composites).
   Panels that look like text are outlined. Click any panel to enlarge it.
4. The **Payloads** page sweeps every decode configuration in the background and
   ranks the results by text-likeness; `flag{...}`-style matches are highlighted.
5. Remaining pages drill into specifics: **Visual Analysis**, **Metadata**,
   **Structure**, **Strings**, **Hex**, **Steganalysis**, **Extract**, **Embed**,
   **Report**.

### Solving the blue-LSB task quickly

- Choose **View > Open blue channel, bit 0** to isolate channel **B**, bit **0**
  in bit-plane mode: the bit becomes white, the rest black.
- Or click the `B bit 0` panel in the montage.
- For a byte-encoded flag, open the **Payloads** page and run a sweep; the
  matching carrier, bit, width and offset are reported and can be replayed.

## Interface

The window follows the Adobe workstation idiom: a charcoal palette, hairline
separators and a single blue accent.

- **Menu bar** - `File`, `Edit`, `View`, `Analysis`, `Help`. Every entry carries
  the same glyph as its toolbar button.
- **Toolbar** - icon only, grouped by task (open/reload/close, run/sweep/stop,
  extract/carve, views, save, preferences/help) and divided by separators.
  Buttons are uniform squares; hover shows the label and shortcut as a tooltip.
- **Tab bar** - one icon per workspace page.
- **Status bar** - progress indicator on the right, plus the loaded file name.

Icons come from `ifw/gui/icons.py`: each glyph is a small SVG drawn on a 24x24
grid and tinted per state, so the whole set shares one look and one colour
scheme - `#c8c8c8` idle, `#ffffff` on hover, `#767676` disabled, and the accent
blue `#2d8ceb` for the primary actions (run analysis, sweep, extract). No binary
image assets are shipped.

### Preferences

`Edit > Preferences...` (`Ctrl+,`) edits the persisted settings and applies them
immediately:

| Section    | Options                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------- |
| General    | default sweep depth, pixel-search offsets, bit widths, byte budget, extraction confirmation |
| Extraction | default output folder, maximum payload size                                                 |
| Strings    | minimum length, encoding, maximum listed                                                    |
| Display    | interface density (comfortable/compact), montage tile count                                 |
| Hex        | rows per page                                                                               |
| OCR        | enable visible-text reading, Tesseract language, engine path, test button                   |

Settings live in `%APPDATA%\ImageForensicsWorkbench\settings.json` (or
`~/.config/image-forensics-workbench/settings.json`). Interface density applies
on the next start.

## Pages

### Overview

File facts, hashes (MD5/SHA-1/SHA-256), Shannon entropy, and a summary verdict
that combines the metadata, structure and steganalysis findings.

### Bit Planes

Every plane in one zoomable montage. Supported views include plain isolation,
inversion, edges, keep and clear. Export the montage as PNG, or export every
plane to a folder as separate PNG files.

### Visual Analysis

Single-plane view with per-channel, per-bit selection, inversion, and derived
carriers (channel XOR, channel difference, per-pixel max/min/average).

### Metadata

Full metadata extraction:

- EXIF (TIFF IFDs, including GPS and interoperability tags),
- XMP, ICC profiles, IPTC and Photoshop records where present,
- PNG textual chunks (`tEXt`, `zTXt`, `iTXt`), JFIF, GIF and BMP headers,
- file hashes, entropy, an entropy map, zero-width-character search, and
  embedded-thumbnail extraction.

### Structure

Container-level analysis: chunk/tile walk of PNG, JPEG, GIF, BMP, TIFF and
WEBP, trailing-data detection with entropy, embedded-file signatures, carved
data, string and pattern search. The signature scan runs against a full
template library (archives, images, audio/video, documents, executables, fonts
and disk images); two-byte magics such as `BM` and `MZ` are only reported when
a cheap validity check passes, so random data does not flood the report.

### Strings

The `strings` equivalent, working on the raw file bytes:

- configurable minimum length and encoding (ASCII, UTF-16LE or both),
- substring filter, a quick "only `flag`" toggle, and export to a text file,
- **Find in pixels**: type any text and the page searches every carrier, bit,
  width and offset for it in the pixel data. Results are listed with the exact
  decode configuration that reproduces the text; double-click one to extract it.

### Hex

An editable hex editor over the whole file:

- offset navigation, byte paging, find (text or hex bytes) and find next,
- edit bytes directly in the hex column, then apply changes with find/replace,
  insert bytes at an offset, or delete a byte range,
- revert to the file on disk, then save or save as to write the patched bytes.

### Steganalysis

First-order statistical triage, clearly labelled as an estimate rather than
proof:

- chi-square attack on pairs of values,
- RS analysis and sample-pair analysis (SPA),
- LSB density map, bit-plane entropy,
- error-level analysis (ELA), noise residual, difference and autocorrelation
  maps,
- palette analysis for indexed images,
- JPEG: quantization tables, entropy-table/histogram inspection, DCT AC
  coefficient LSB histogram, and JSteg extraction.

### Payloads

The sweep. It tries, in the background and with a stop button:

| Axis             | Coverage                                                    |
| ---------------- | ----------------------------------------------------------- |
| Single channels  | R, G, B, A, Gray, palette index plane                       |
| Derived carriers | channel XOR, channel difference, max/min/average            |
| Interleaved      | the six permutations of R, G, B (zsteg `b1,rgb` style)      |
| Bit width        | 1 bit, 2 bits and 4 bits per sample                         |
| Starting bit     | every bit position valid for the width                      |
| Byte grouping    | most-significant-first and least-significant-first          |
| Bit offset       | configurable (data that is not byte-aligned is still found) |

Results are ranked by a text-likeness score; real flag matches are listed
separately. Any candidate can be replayed exactly, carved, or exported.

Because a sweep only ever reads as many samples as its byte budget needs, cost
is set by the scan depth and not by the image size: a phone photo and a
thumbnail are swept in the same time, and a very small image simply yields fewer
candidates.

**Extract everything.** Detection is only half the job, so the page can also
recover the data:

- **Extract all payloads** decodes every candidate in full - not a fixed-size
  probe - and writes each one to a chosen folder as `payload_NNN_<type>_<carrier>_b<bit>_w<width>_o<offset>_<order>.txt` (or `.bin` for binary data).
- **Extract selected** does the same for the highlighted rows only. Select with
  `Ctrl`/`Shift`-click.
- A `manifest.json` and a readable `manifest.txt` are written alongside,
  recording the exact configuration, byte count, capacity and matched flag for
  every payload.
- Double-clicking any row opens the decoded payload with **Save as...** (text)
  and **Save bytes...** (raw) buttons.

### Extract

Manual bitstream extraction: choose carrier, starting bit, bit width, byte
order, bit offset and a byte limit. Shows ASCII, hex and a list of printable
strings, and can save the raw `.bin` or `.txt`.

### Embed

Least-significant-bit substitution into a chosen carrier, bit and offset, with a
capacity check, for building test material and verifying round-trips. Saves PNG
so the modification is lossless.

### Report

Build a consolidated report as HTML, JSON or plain text, and save individual
sections.

## Scan depths

| Mode               | Coverage                                              | Notes         |
| ------------------ | ----------------------------------------------------- | ------------- |
| Quick              | 1- and 2-bit widths, 4 offsets, 50 KB per stream      | fast triage   |
| Standard (default) | 1-, 2- and 4-bit widths, 8 offsets, 200 KB per stream | balanced      |
| Deep               | 1-, 2- and 4-bit widths, 8 offsets, 1 MB per stream   | most thorough |

All analysis runs on worker threads. The UI stays responsive and any long job
can be stopped; results from a previous image are discarded if the image changes
mid-scan.

## Resolution and scale

The workbench is built to behave the same from a 16x16 icon to a 100-megapixel
scan:

- the payload sweep reads only the samples its byte budget covers, so it never
  materialises a whole plane just to look at its start,
- bit-plane capacity is computed arithmetically rather than by flattening the
  image,
- the bit-plane montage, and the synthetic reference tile in it, are built from
  thumbnails, so a huge image does not overflow the grid,
- metadata, structure and steganalysis run off the UI thread and remain
  interruptible.

## Keyboard shortcuts

`Ctrl+O` open · `F5` reload · `Ctrl+W` close · `Ctrl+S` save report ·
`Ctrl+E` extract all payloads · `Ctrl+Q` quit · `F8` run full analysis ·
`Ctrl+R` payload sweep · `Esc` stop sweep · `Ctrl+B` show bit-plane montage ·
`Ctrl+1`..`Ctrl+9` go to the first nine pages (the remaining pages are in the
**View** menu) · `Ctrl+0` fit · `Ctrl+=` / `Ctrl+-` zoom ·
`F1` documentation.

## Supported formats

PNG, BMP, JPEG, GIF, TIFF, WEBP and PNM. Grayscale, RGB, RGBA, palette (`P`) and
16-bit-per-channel images are understood; 16-bit data is analysed at full depth
and reduced to 8-bit only for display.

## Verifying the build

```powershell
python selftest.py
```

The suite checks every layer headlessly:

1. each analysis module's own self-test (including `config`, `ocr`, `gui.theme`
   and `gui.icons`),
2. the core document model,
3. the canonical decode API round-trip (including bit offset and both byte
   orders),
4. the payload sweep on single-channel, interleaved and multi-bit carriers,
   plus a negative control,
5. the metadata, structure and steganalysis report builders,
6. detector calibration,
7. the signature templates, entropy map, ASCII/UTF-16 strings and pixel text
   search,
8. construction and shutdown of the Qt interface, including the vector icons
   and uniform toolbar, the strings and hex pages, byte-level hex editing, full
   extraction of every candidate payload and its manifest.

Individual modules can be tested on their own, for example
`python -m ifw.steganalysis`.

## Sample material

```powershell
python make_demo.py
```

| File                          | What it demonstrates                          |
| ----------------------------- | --------------------------------------------- |
| `demo_blue_lsb.png`           | flag drawn as a picture in the blue LSB plane |
| `demo_blue_lsb_bytes.png`     | flag stored as bytes in the blue LSB plane    |
| `demo_interleaved_rgb.png`    | flag interleaved across R, G, B at bit 1      |
| `demo_width2_blue.png`        | flag stored as two bits per blue sample       |
| `demo_metadata.jpg`           | ordinary JPEG with EXIF metadata              |
| `demo_indexed.png`            | palette image with a duplicate entry          |
| `demo_blue_lsb_extracted.png` | reference extraction of the visual carrier    |

## Project layout

| Path                        | Contents                                                                     |
| --------------------------- | ---------------------------------------------------------------------------- |
| `ifw/__init__.py`           | package metadata (name, version, organisation)                               |
| `ifw/imaging.py`            | document model, bit/byte access, montage, scoring                            |
| `ifw/scan.py`               | payload sweep worker, pixel text search and generic task worker              |
| `ifw/metadata.py`           | EXIF/TIFF/XMP/ICC/IPTC/PNG-text parsing, hashes, entropy                     |
| `ifw/structure.py`          | container/chunk/trailing analysis, signature templates, entropy map, strings |
| `ifw/steganalysis.py`       | statistics, transforms, palette and JPEG DCT analysis                        |
| `ifw/config.py`             | persisted user settings (JSON), validation and derived helpers               |
| `ifw/ocr.py`                | optional Tesseract adapter for reading visible text                          |
| `ifw/gui/theme.py`          | dark palette, stylesheet, icon tints and fonts                               |
| `ifw/gui/icons.py`          | vector icon set (SVG glyphs rendered and tinted per state)                   |
| `ifw/gui/preferences.py`    | sectioned preferences dialog                                                 |
| `ifw/gui/widgets.py`        | image view, hex viewer and editor, text dialog, tables                       |
| `ifw/gui/pages.py`          | the twelve workspace pages                                                   |
| `ifw/gui/main.py`           | main window, menus, toolbar, threading, report builders                      |
| `ifw/gui/__init__.py`       | application entry point (`run`)                                              |
| `ifw.py`, `ifw/__main__.py` | launchers                                                                    |
| `selftest.py`               | end-to-end test suite                                                        |
| `make_demo.py`              | sample-image generator                                                       |

## Technical notes

- **Canonical decode API.** `imaging.extract_stream(doc, carrier, bit,
msb_first, offset, max_bytes, all_bits, bit_width)` is the single source of
  truth for decoding. Every candidate the sweep reports can be reproduced by
  calling this function with the reported parameters. The bits of one sample are
  always read most significant first; `msb_first` selects how the resulting bit
  stream is grouped into bytes.
- **Bit-plane isolation** is `((channel >> bit) & 1) * 255`.
- **LSB substitution** is `(sample & ~mask) | (bit << n)`, which leaves every
  other bit of every sample untouched.
- **Detectability estimates** (chi-square, RS, sample-pair analysis) are
  first-order triage. They can miss payloads that are short or concentrated, and
  a "no evidence" result is not proof of a clean image; conversely, high-entropy
  content can look indistinguishable from a fully embedded image.
- **Performance.** Bit sequences are built once per configuration and reused for
  every offset and both byte orders, `numpy.packbits` does the byte packing in C,
  and a cheap prefilter (keyword byte search plus printable ratio) gates the
  heavier pattern matching.

test
