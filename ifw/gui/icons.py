"""Vector icon set for the interface.

Every glyph is a small SVG drawn on a 24x24 grid with a single stroke colour,
so the whole interface stays visually consistent: hairline strokes, round caps
and no fills except where a shape should read solid (play, stop, dots).

The icons are rendered through ``QSvgRenderer`` and tinted per state, which
gives four distinct colours per icon without shipping any binary assets:

    icon("open")                # idle / active / disabled / selected
    icon("run", accent=True)    # primary action in the accent colour
    pixmap("search", 16, "#fff")# a one-off tinted pixmap

When Qt does not ship the SVG module the factory degrades to an empty icon and
``AVAILABLE`` reports False, letting callers fall back to platform icons.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

try:  # QtSvg is an optional Qt module; degrade gracefully when it is absent.
    from PySide6.QtSvg import QSvgRenderer

    AVAILABLE = True
except Exception:  # pragma: no cover - depends on the Qt distribution
    QSvgRenderer = None
    AVAILABLE = False

from .theme import ICON, ICON_ACCENT, ICON_ACTIVE, ICON_DISABLED

#: name -> SVG body drawn inside the shared 24x24 frame. ``{c}`` is replaced
#: with the current tint colour; shapes that should read solid set
#: ``fill="{c}" stroke="none"`` themselves.
ICONS: dict[str, str] = {
    # -- file -----------------------------------------------------------------
    "open": (
        '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2a1.5 1.5 0 0 1 1.2.6L11 7h8.5'
        'A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>'
    ),
    "reload": (
        '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M18 3.6V7.2h-3.6"/>'
    ),
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "quit": '<path d="M12 4.2v7.3"/><path d="M7.6 6.6a7 7 0 1 0 8.8 0"/>',
    # -- primary actions ------------------------------------------------------
    "run": (
        '<path d="M8.4 5.6a1 1 0 0 1 1.5-.87l9.1 6.4a1 1 0 0 1 0 1.74l-9.1 6.4'
        'a1 1 0 0 1-1.5-.87z" fill="{c}" stroke="none"/>'
    ),
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2.2" fill="{c}" stroke="none"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M19.8 19.8l-4.1-4.1"/>',
    "download": (
        '<path d="M12 4.2v9"/><path d="M8.4 10.4l3.6 3.6 3.6-3.6"/>'
        '<path d="M5 16.6v1.9A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-1.9"/>'
    ),
    "upload": (
        '<path d="M12 19.8v-9"/><path d="M8.4 13.6L12 10l3.6 3.6"/>'
        '<path d="M5 7.4V5.5A1.5 1.5 0 0 1 6.5 4h11A1.5 1.5 0 0 1 19 5.5v1.9"/>'
    ),
    "package": (
        '<path d="M12 3.2l8 4.4v8.8L12 20.8l-8-4.4V7.6z"/>'
        '<path d="M4 7.6l8 4.4 8-4.4"/><path d="M12 12v8.8"/>'
    ),
    "carve": (
        '<circle cx="7" cy="17.4" r="2.5"/><circle cx="17" cy="17.4" r="2.5"/>'
        '<path d="M8.7 15.6L17.6 4.6"/><path d="M15.3 15.6L6.4 4.6"/>'
    ),
    # -- views ----------------------------------------------------------------
    "layers": (
        '<path d="M12 3.6l8.2 4.1-8.2 4.1-8.2-4.1z"/>'
        '<path d="M4.3 12l7.7 3.9 7.7-3.9"/><path d="M4.3 15.9l7.7 3.9 7.7-3.9"/>'
    ),
    "fit": (
        '<path d="M4 9V5.6A1.6 1.6 0 0 1 5.6 4H9"/>'
        '<path d="M15 4h3.4A1.6 1.6 0 0 1 20 5.6V9"/>'
        '<path d="M20 15v3.4a1.6 1.6 0 0 1-1.6 1.6H15"/>'
        '<path d="M9 20H5.6A1.6 1.6 0 0 1 4 18.4V15"/>'
    ),
    "zoom_in": (
        '<circle cx="11" cy="11" r="6"/><path d="M19.8 19.8l-4.1-4.1"/>'
        '<path d="M11 8.4v5.2M8.4 11h5.2"/>'
    ),
    "zoom_out": (
        '<circle cx="11" cy="11" r="6"/><path d="M19.8 19.8l-4.1-4.1"/>'
        '<path d="M8.4 11h5.2"/>'
    ),
    "image": (
        '<rect x="3.5" y="5" width="17" height="14" rx="2"/>'
        '<circle cx="8.6" cy="9.8" r="1.5"/>'
        '<path d="M4 16.6l4.4-3.9 3.4 2.9 3.1-2.6 4.7 4.1"/>'
    ),
    "montage": (
        '<rect x="3.5" y="5" width="17" height="14" rx="2"/>'
        '<path d="M3.5 12h17M12 5v14"/>'
    ),
    "droplet": (
        '<path d="M12 3.4c0 0 6 6.4 6 10.3a6 6 0 0 1-12 0c0-3.9 6-10.3 6-10.3z"/>'
    ),
    # -- analysis -------------------------------------------------------------
    "tag": (
        '<path d="M11.4 3.5H4.6A1.1 1.1 0 0 0 3.5 4.6v6.8a1.1 1.1 0 0 0 .32.78'
        'l8.1 8.1a1.1 1.1 0 0 0 1.56 0l6.8-6.8a1.1 1.1 0 0 0 0-1.56l-8.1-8.1'
        'a1.1 1.1 0 0 0-.78-.32z"/>'
        '<circle cx="7.6" cy="7.6" r="1.3"/>'
    ),
    "grid": (
        '<rect x="4" y="4" width="7" height="7" rx="1.2"/>'
        '<rect x="13" y="4" width="7" height="7" rx="1.2"/>'
        '<rect x="4" y="13" width="7" height="7" rx="1.2"/>'
        '<rect x="13" y="13" width="7" height="7" rx="1.2"/>'
    ),
    "wave": '<path d="M3 12.5h3l2-5.5 3.2 10 2.6-7.4 1.9 4.4H21"/>',
    "bars": '<path d="M5 19V9.5M9.6 19V5M14.2 19v-7M18.8 19v-4.4"/>',
    "palette": (
        '<circle cx="9.2" cy="10" r="4.4"/>'
        '<circle cx="14.8" cy="10" r="4.4"/>'
        '<circle cx="12" cy="15" r="4.4"/>'
    ),
    # -- text and data --------------------------------------------------------
    "text": '<path d="M4.5 6.2h15M4.5 11.2h15M4.5 16.2h9.5"/>',
    "hash": '<path d="M9.4 4l-2.2 16M16.8 4l-2.2 16M4.5 9.2h15M4 14.8h15"/>',
    "info": (
        '<circle cx="12" cy="12" r="8.2"/><path d="M12 11.2v5"/>'
        '<circle cx="12" cy="8.2" r="0.7" fill="{c}" stroke="none"/>'
    ),
    "book": (
        '<path d="M5 4.6A1.6 1.6 0 0 1 6.6 3H19v16.4H6.6A1.6 1.6 0 0 0 5 21z"/>'
        '<path d="M5 19.4A1.6 1.6 0 0 1 6.6 17.8H19"/>'
    ),
    "sliders": (
        '<path d="M4 8.2h9.4M18.6 8.2H20"/><circle cx="16" cy="8.2" r="2.1"/>'
        '<path d="M4 15.8h2.4M11.4 15.8H20"/><circle cx="8" cy="15.8" r="2.1"/>'
    ),
    "report": (
        '<path d="M6 3.5h7.6l4.4 4.4v12.6H6z"/><path d="M13.6 3.5v4.4H18"/>'
        '<path d="M8.8 12.4h6.4M8.8 15.8h6.4"/>'
    ),
}


def _document(body: str, colour: str) -> str:
    """Wrap an icon body in the shared 24x24 SVG frame."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
        'fill="none" stroke="' + colour + '" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round">' + body.replace("{c}", colour) + "</svg>"
    )


def pixmap(name: str, size: int = 18, colour: str | None = None, ratio: int = 2) -> QPixmap:
    """A single tinted pixmap, or a null pixmap for an unknown name."""
    body = ICONS.get(name)
    if body is None or not AVAILABLE:
        return QPixmap()
    tint = colour or ICON.name()
    target = QPixmap(size * ratio, size * ratio)
    target.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_document(body, tint).encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    target.setDevicePixelRatio(ratio)
    return target


_CACHE: dict[tuple[str, int, bool], QIcon] = {}


def icon(name: str, size: int = 18, accent: bool = False) -> QIcon:
    """A four state icon: idle, active (hover), disabled and selected."""
    key = (name, size, accent)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    result = QIcon()
    if AVAILABLE and name in ICONS:
        idle = ICON_ACCENT.name() if accent else ICON.name()
        result.addPixmap(pixmap(name, size, idle), QIcon.Mode.Normal, QIcon.State.Off)
        result.addPixmap(
            pixmap(name, size, ICON_DISABLED.name()), QIcon.Mode.Disabled, QIcon.State.Off
        )
        result.addPixmap(
            pixmap(name, size, ICON_ACTIVE.name()), QIcon.Mode.Active, QIcon.State.Off
        )
        result.addPixmap(
            pixmap(name, size, ICON_ACTIVE.name()), QIcon.Mode.Selected, QIcon.State.Off
        )
        result.addPixmap(
            pixmap(name, size, ICON_ACTIVE.name()), QIcon.Mode.Normal, QIcon.State.On
        )
    _CACHE[key] = result
    return result


def available() -> bool:
    """True when icons can actually be rendered."""
    return AVAILABLE


def _selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    check("icon table is populated", len(ICONS) >= 20, f"{len(ICONS)} glyphs")
    check(
        "every body is non empty",
        all(isinstance(body, str) and body.strip() for body in ICONS.values()),
    )
    check("no stray format braces", all("{" not in b.replace("{c}", "") for b in ICONS.values()))
    check("document frames the body", _document('<path d="M0 0"/>', "#fff").startswith("<svg"))

    app = None
    try:
        from PySide6.QtGui import QGuiApplication

        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QGuiApplication.instance() or QGuiApplication([])
    except Exception as exc:  # pragma: no cover - headless without a platform plugin
        print(f"note: no Qt platform available for rendering checks ({exc})")

    if app is not None:
        if AVAILABLE:
            rendered = pixmap("open", 18, "#ffffff")
            check("svg renderer produces a pixmap", not rendered.isNull() and rendered.width() > 0)
            sample = icon("run", 18)
            check("icon has the idle and disabled states", not sample.pixmap(18, QIcon.Mode.Disabled).isNull())
            check("accent variant renders", not icon("run", 18, accent=True).pixmap(18).isNull())
        else:
            check("degrades without QtSvg", icon("open") is not None)
        check("unknown name yields a null pixmap", pixmap("does-not-exist").isNull())

    print()
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
