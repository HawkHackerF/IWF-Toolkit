"""User settings: a small, validated JSON document persisted per user.

Kept free of Qt so it can be imported and self-tested headlessly. The GUI reads
defaults from here at construction time and writes changes back through
``Settings.update``; every value is coerced to a sane type so a hand-edited
file cannot break the application.

Path:
    Windows  %APPDATA%\\ImageForensicsWorkbench\\settings.json
    other    ~/.config/image-forensics-workbench/settings.json

Public entry point:

    Settings()          # loads the user file, falling back to DEFAULTS
    Settings.load(path) # explicit path (used by the tests)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

#: name -> default. The type of the default value drives the coercion.
DEFAULTS: dict[str, object] = {
    # -- analysis -----------------------------------------------------------
    "scan_depth": "standard",
    "pixel_offsets": 8,
    "pixel_widths": "1,2,4",
    "pixel_max_bytes": 200000,
    # -- extraction ---------------------------------------------------------
    "extract_dir": "",
    "extract_max_mb": 64,
    "extract_confirm": True,
    # -- strings ------------------------------------------------------------
    "strings_min_len": 6,
    "strings_encoding": "ascii",
    "strings_limit": 5000,
    # -- hex ----------------------------------------------------------------
    "hex_rows": 256,
    # -- display ------------------------------------------------------------
    "theme_density": "comfortable",
    "montage_max_tiles": 64,
    # -- ocr ----------------------------------------------------------------
    "ocr_enabled": True,
    "ocr_language": "eng",
    "tesseract": "",
}

#: Allowed values for the enumerated keys, used for validation.
CHOICES: dict[str, tuple[str, ...]] = {
    "scan_depth": ("quick", "standard", "deep"),
    "strings_encoding": ("ascii", "utf16le", "both"),
    "theme_density": ("comfortable", "compact"),
}

#: Lower and upper bounds for the numeric keys.
RANGES: dict[str, tuple[float, float]] = {
    "pixel_offsets": (1, 64),
    "pixel_max_bytes": (4096, 16_000_000),
    "extract_max_mb": (1, 1024),
    "strings_min_len": (2, 64),
    "strings_limit": (50, 200_000),
    "hex_rows": (64, 1024),
    "montage_max_tiles": (8, 4096),
}


def config_dir() -> str:
    """Directory that holds the settings file."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ImageForensicsWorkbench")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "image-forensics-workbench")


def config_path() -> str:
    return os.path.join(config_dir(), "settings.json")


@dataclass
class Settings:
    """A typed view over the settings document."""

    data: dict
    path: str

    # -- construction -------------------------------------------------------
    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        target = path or config_path()
        instance = cls(data=dict(DEFAULTS), path=target)
        try:
            with open(target, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return instance
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in DEFAULTS:
                    instance.data[key] = value
        instance.coerce()
        return instance

    def coerce(self) -> None:
        """Force every stored value back into the declared type and range."""
        for key, default in DEFAULTS.items():
            value = self.data.get(key, default)
            if key in CHOICES:
                self.data[key] = value if value in CHOICES[key] else default
            elif isinstance(default, bool):
                self.data[key] = bool(value)
            elif isinstance(default, int):
                low, high = RANGES.get(key, (0, 1 << 62))
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    number = int(default)
                self.data[key] = max(int(low), min(int(high), number))
            else:
                self.data[key] = str(value) if value is not None else str(default)

    # -- access -------------------------------------------------------------
    def get(self, key: str, default=None):
        return self.data.get(key, default if default is not None else DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key in DEFAULTS:
            self.data[key] = value
            self.coerce()

    def update(self, values: dict) -> None:
        for key, value in values.items():
            if key in DEFAULTS:
                self.data[key] = value
        self.coerce()

    def reset(self) -> None:
        self.data = dict(DEFAULTS)
        self.coerce()

    # -- persistence --------------------------------------------------------
    def save(self) -> str:
        directory = os.path.dirname(self.path)
        if directory:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                pass
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2, sort_keys=True)
        return self.path

    # -- derived helpers ----------------------------------------------------
    def pixel_widths_tuple(self) -> tuple[int, ...]:
        """The configured bit widths as a tuple, ignoring invalid entries."""
        out = []
        for token in str(self.data.get("pixel_widths", "1,2,4")).replace(",", " ").split():
            try:
                value = int(token)
            except ValueError:
                continue
            if 1 <= value <= 8 and value not in out:
                out.append(value)
        return tuple(out) or (1,)

    def extract_limit_bytes(self) -> int:
        return int(self.data.get("extract_max_mb", 64)) * 1024 * 1024


def _selftest() -> int:
    import tempfile

    failures = []

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    settings = Settings.load(os.path.join(tempfile.mkdtemp(prefix="ifw_cfg_"), "settings.json"))
    check("defaults load", settings.get("scan_depth") == "standard")
    check("numeric default", settings.get("strings_min_len") == 6)

    settings.set("scan_depth", "deep")
    check("set accepts a valid choice", settings.get("scan_depth") == "deep")
    settings.set("scan_depth", "nonsense")
    check("invalid choice falls back", settings.get("scan_depth") == "standard")

    settings.set("strings_min_len", 9999)
    check("numeric value is clamped", settings.get("strings_min_len") == 64)
    settings.set("hex_rows", "not-a-number")
    check("non numeric value falls back", settings.get("hex_rows") == 256)

    settings.set("pixel_widths", "1, 2, 4, 2, 9")
    check("widths are parsed and deduplicated", settings.pixel_widths_tuple() == (1, 2, 4),
          str(settings.pixel_widths_tuple()))

    settings.update({"extract_max_mb": 8, "ocr_enabled": False, "tesseract": r"C:\tess.exe"})
    check("update applies several keys",
          settings.get("extract_max_mb") == 8 and not settings.get("ocr_enabled"))
    check("extract limit is in bytes", settings.extract_limit_bytes() == 8 * 1024 * 1024)

    target = settings.save()
    check("settings file written", os.path.isfile(target))
    reloaded = Settings.load(target)
    check("round trip preserves values", reloaded.get("tesseract") == r"C:\tess.exe")

    reloaded.reset()
    check("reset restores defaults", reloaded.get("scan_depth") == "standard")

    broken = os.path.join(os.path.dirname(target), "broken.json")
    with open(broken, "w", encoding="utf-8") as handle:
        handle.write("{ not json")
    check("corrupt file falls back to defaults",
          Settings.load(broken).get("scan_depth") == "standard")

    print()
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
