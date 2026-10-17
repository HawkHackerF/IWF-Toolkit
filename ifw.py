#!/usr/bin/env python3
"""Launcher for the Image Forensics Workbench.

Usage:
    python ifw.py [image]

Equivalent to ``python -m ifw``. Kept as a single, double-clickable entry point.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ifw.gui import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
