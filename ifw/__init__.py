"""Image Forensics Workbench (IFW).

A modular image-forensics and steganalysis toolkit:

    ifw.imaging       image document model, bit-level access, views, montage
    ifw.scan          systematic stego payload discovery (LSB and derived carriers)
    ifw.metadata      EXIF / XMP / ICC / IPTC / container metadata extraction
    ifw.structure     container parsing, trailing data, embedded file carving
    ifw.steganalysis  statistical steganalysis and forensic visual transforms
    ifw.gui           Qt (PySide6) desktop interface
"""

from __future__ import annotations

APP_NAME = "Image Forensics Workbench"
APP_SHORT = "IFW"
APP_VERSION = "3.0"
APP_ORGANISATION = "IFW Project"

__all__ = ["APP_NAME", "APP_SHORT", "APP_VERSION", "APP_ORGANISATION"]
