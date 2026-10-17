"""Optional optical character recognition for text rendered inside images.

Tesseract is an external engine, so it is never a hard dependency. Every entry
point here degrades gracefully: when the engine is absent the callers receive a
clear reason instead of an import error at module load time.

    probe(path)                  -> OcrStatus
    recognize(image, lang, path) -> str
    languages(path)              -> tuple[str, ...]

Enable it by installing both ``pytesseract`` and a Tesseract build, then point
Settings > OCR at the ``tesseract`` executable (or leave it blank when the
engine is already on PATH).
"""

from __future__ import annotations

from dataclasses import dataclass

try:  # pragma: no cover - exercised only when the optional engine is present
    import pytesseract

    _IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - the common case
    pytesseract = None
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


@dataclass
class OcrStatus:
    """What the environment can currently do."""

    available: bool
    reason: str = ""
    version: str = ""
    path: str = ""


def engine_present() -> bool:
    """True when the python side of the binding imports cleanly."""
    return pytesseract is not None


def probe(tesseract_path: str = "") -> OcrStatus:
    """Report whether OCR can run. Never raises."""
    if pytesseract is None:
        return OcrStatus(False, _IMPORT_ERROR or "pytesseract is not installed")
    if tesseract_path:
        try:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        except Exception as exc:  # pragma: no cover - defensive
            return OcrStatus(False, f"{type(exc).__name__}: {exc}", "", tesseract_path)
    try:
        version = str(pytesseract.get_tesseract_version()).strip()
    except Exception as exc:
        return OcrStatus(False, f"{type(exc).__name__}: {exc}", "", tesseract_path)
    return OcrStatus(True, "", version, tesseract_path)


def recognize(image, language: str = "eng", tesseract_path: str = "") -> str:
    """Run OCR on a PIL image and return the recognised text.

    Raises RuntimeError with a human readable reason when the engine is not
    usable, so a caller can show the message instead of crashing.
    """
    status = probe(tesseract_path)
    if not status.available:
        raise RuntimeError(status.reason)
    text = pytesseract.image_to_string(image, lang=(language or "eng").strip() or "eng")
    return text or ""


def languages(tesseract_path: str = "") -> tuple[str, ...]:
    """Installed Tesseract languages, or an empty tuple when unavailable."""
    if probe(tesseract_path).available:
        try:
            found = sorted(str(code).strip() for code in pytesseract.get_languages(config=""))
            return tuple(code for code in found if code)
        except Exception:  # pragma: no cover - defensive
            return ()
    return ()


def _selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    status = probe("")
    check("probe returns a status", isinstance(status, OcrStatus))
    check("probe is non-destructive", not status.available or status.version != "")

    from PIL import Image

    blank = Image.new("RGB", (16, 16), (255, 255, 255))
    if status.available:
        text = recognize(blank)
        check("recognize returns text", isinstance(text, str))
    else:
        try:
            recognize(blank)
        except RuntimeError as exc:
            check("unavailable engine reports a reason", bool(str(exc).strip()), str(exc)[:60])
        else:  # pragma: no cover - only when OCR silently succeeds
            check("unavailable engine reports a reason", False)

    check("languages degrades safely", isinstance(languages(""), tuple))

    print()
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
