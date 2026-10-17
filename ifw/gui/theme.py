"""Application theme: a flat, dark interface in the Adobe workstation idiom.

Charcoal surfaces, square-ish corners, hairline separators and a single blue
accent. Control sizes are driven by a density knob so the same stylesheet can
be tuned from a comfortable default to a compact, information-dense layout.

    build_stylesheet("comfortable")   # or "compact"
    apply_theme(app, density=...)
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

# -- surfaces -----------------------------------------------------------------
BG_WINDOW = QColor(50, 50, 50)      # #323232 application background
BG_PANEL = QColor(43, 43, 43)       # #2b2b2b panels, menus, toolbars
BG_INPUT = QColor(38, 38, 38)       # #262626 fields and tables
BG_EDIT = QColor(30, 30, 30)        # #1e1e1e text and hex editors
BG_ALT = QColor(45, 45, 45)         # #2d2d2d alternate row
BG_RAISED = QColor(58, 58, 58)      # #3a3a3a buttons and headers
BG_HOVER = QColor(69, 69, 69)       # #454545 hover
# -- foreground ---------------------------------------------------------------
FG_TEXT = QColor(214, 214, 214)     # #d6d6d6
FG_DIM = QColor(160, 160, 160)      # #a0a0a0
FG_FAINT = QColor(118, 118, 118)    # #767676
FG_BRIGHT = QColor(240, 240, 240)   # #f0f0f0
# -- accent -------------------------------------------------------------------
ACCENT = QColor(20, 115, 230)       # #1473e6 Adobe blue
ACCENT_DIM = QColor(13, 102, 208)   # #0d66d0
ACCENT_SOFT = QColor(45, 140, 235)  # #2d8ceb
OK = QColor(55, 160, 95)            # #37a05f
WARN = QColor(220, 170, 60)         # #dcaa3c
DANGER = QColor(209, 90, 80)        # #d15a50
# -- icons --------------------------------------------------------------------
ICON = QColor(200, 200, 200)        # #c8c8c8 idle glyphs
ICON_ACTIVE = QColor(255, 255, 255) # hovered and pressed glyphs
ICON_DISABLED = QColor(118, 118, 118)  # #767676 unavailable actions
ICON_ACCENT = QColor(45, 140, 235)  # #2d8ceb primary actions
# -- lines --------------------------------------------------------------------
BORDER = QColor(30, 30, 30)         # #1e1e1e hairline separators
BORDER_SOFT = QColor(69, 69, 69)    # #454545 control outlines
SELECTION = QColor(20, 115, 230)

#: Density knobs: padding, control heights, tab padding and font point size.
DENSITY: dict[str, dict[str, int]] = {
    "comfortable": dict(ctrl_v=5, ctrl_h=18, tool_h=24, tab_v=7, tab_h=15, font=9),
    "compact": dict(ctrl_v=3, ctrl_h=14, tool_h=20, tab_v=5, tab_h=12, font=8),
}

_HEX = {
    "BG_WINDOW": "#323232", "BG_PANEL": "#2b2b2b", "BG_INPUT": "#262626",
    "BG_EDIT": "#1e1e1e", "BG_ALT": "#2d2d2d", "BG_RAISED": "#3a3a3a",
    "BG_HOVER": "#454545", "FG_TEXT": "#d6d6d6", "FG_DIM": "#a0a0a0",
    "FG_FAINT": "#767676", "FG_BRIGHT": "#f0f0f0", "ACCENT": "#1473e6",
    "ACCENT_DIM": "#0d66d0", "ACCENT_SOFT": "#2d8ceb", "OK": "#37a05f",
    "WARN": "#dcaa3c", "DANGER": "#d15a50", "BORDER": "#1e1e1e",
    "BORDER_SOFT": "#454545", "SELECTION": "#1473e6",
}


def build_stylesheet(density: str = "comfortable") -> str:
    """Render the stylesheet for a density setting."""
    k = DENSITY.get(density) or DENSITY["comfortable"]
    c = _HEX
    return f"""
QWidget {{ background: {c['BG_WINDOW']}; color: {c['FG_TEXT']}; }}
QMainWindow::separator {{ background: {c['BG_PANEL']}; width: 2px; }}

/* ---- menus (Adobe style: flat rows, accent hover) ----------------------- */
QMenuBar {{ background: {c['BG_PANEL']}; border-bottom: 1px solid {c['BORDER']}; padding: 2px; }}
QMenuBar::item {{ padding: 4px 11px; background: transparent; border-radius: 3px; }}
QMenuBar::item:selected {{ background: {c['BG_HOVER']}; color: {c['FG_BRIGHT']}; }}
QMenuBar::item:pressed {{ background: {c['ACCENT']}; color: #ffffff; }}
QMenu {{ background: {c['BG_PANEL']}; border: 1px solid {c['BORDER']}; padding: 5px 4px; }}
QMenu::item {{ padding: 5px 26px 5px 22px; border-radius: 3px; margin: 0 3px; }}
QMenu::item:selected {{ background: {c['ACCENT']}; color: #ffffff; }}
QMenu::item:disabled {{ color: {c['FG_FAINT']}; }}
QMenu::separator {{ height: 1px; background: {c['BORDER_SOFT']}; margin: 5px 8px; }}
QMenu::indicator {{ width: 12px; height: 12px; }}

/* ---- toolbar (Adobe style: square icon buttons, hairline groups) -------- */
QToolBar {{
    background: {c['BG_PANEL']}; border: none; border-bottom: 1px solid {c['BORDER']};
    spacing: 2px; padding: 3px 5px; margin: 0;
}}
QToolButton#ToolBarButton {{
    background: transparent; border: 1px solid transparent; border-radius: 4px;
}}
QToolButton#ToolBarButton:hover {{ background: {c['BG_HOVER']}; border-color: {c['BORDER_SOFT']}; }}
QToolButton#ToolBarButton:pressed, QToolButton#ToolBarButton:checked {{
    background: {c['ACCENT']}; border-color: {c['ACCENT_SOFT']};
}}
QToolButton#ToolBarButton:disabled {{ background: transparent; border-color: transparent; }}
QToolButton#ToolBarButton::menu-indicator {{
    subcontrol-origin: padding; subcontrol-position: bottom right; width: 9px;
}}
QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 3px;
    padding: {k['ctrl_v'] - 1}px 9px; min-height: {k['ctrl_h']}px; color: {c['FG_TEXT']};
}}
QToolButton:hover {{ background: {c['BG_HOVER']}; border-color: {c['BORDER']}; }}
QToolButton:pressed {{ background: {c['ACCENT_DIM']}; color: #ffffff; }}
QToolButton:checked {{ background: {c['ACCENT']}; border-color: {c['ACCENT_SOFT']}; color: #ffffff; }}
QToolButton:disabled {{ color: {c['FG_FAINT']}; }}
QToolBar::separator {{
    background: {c['BORDER_SOFT']}; width: 1px; margin: 5px 6px; padding: 0;
}}

/* ---- status bar --------------------------------------------------------- */
QStatusBar {{ background: {c['BG_INPUT']}; border-top: 1px solid {c['BORDER']}; color: {c['FG_DIM']}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ padding: 0 8px; }}

/* ---- page tabs (Photoshop style) ---------------------------------------- */
QTabWidget::pane {{ border: none; border-top: 1px solid {c['BORDER']}; background: {c['BG_WINDOW']}; }}
QTabBar {{ background: {c['BG_PANEL']}; }}
QTabBar::tab {{
    background: {c['BG_PANEL']}; color: {c['FG_DIM']}; padding: {k['tab_v']}px {k['tab_h']}px;
    border: 1px solid {c['BORDER']}; border-bottom: none; margin-right: 1px;
    border-top: 2px solid transparent; min-width: 62px;
}}
QTabBar::tab:selected {{
    background: {c['BG_WINDOW']}; color: {c['FG_BRIGHT']}; border-top: 2px solid {c['ACCENT']};
}}
QTabBar::tab:hover:!selected {{ background: {c['BG_ALT']}; color: {c['FG_TEXT']}; }}

/* ---- dock titles and section panels ------------------------------------- */
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {c['BG_PANEL']}; padding: 6px 9px; border-bottom: 1px solid {c['BORDER']};
    color: {c['FG_BRIGHT']}; font-weight: 600;
}}
QGroupBox {{
    border: 1px solid {c['BORDER']}; border-radius: 3px; margin-top: 15px;
    padding: 12px 10px 8px 10px; background: {c['BG_PANEL']};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 9px; padding: 0 5px; color: {c['FG_BRIGHT']};
    font-weight: 600;
}}

/* ---- buttons ------------------------------------------------------------ */
QPushButton {{
    background: {c['BG_RAISED']}; border: 1px solid {c['BORDER']}; border-radius: 3px;
    padding: {k['ctrl_v']}px {k['ctrl_h']}px; min-height: {k['ctrl_h']}px; min-width: {k['ctrl_h']}px;
}}
QPushButton:hover {{ background: {c['BG_HOVER']}; border-color: {c['BORDER_SOFT']}; }}
QPushButton:pressed {{ background: {c['ACCENT_DIM']}; color: #ffffff; }}
QPushButton:disabled {{ color: {c['FG_FAINT']}; background: {c['BG_INPUT']}; border-color: {c['BORDER']}; }}
QPushButton[accent="true"] {{ background: {c['ACCENT']}; border-color: {c['ACCENT_SOFT']}; color: #ffffff; }}
QPushButton[accent="true"]:hover {{ background: {c['ACCENT_SOFT']}; }}
QPushButton[accent="true"]:pressed {{ background: {c['ACCENT_DIM']}; }}
QPushButton[danger="true"] {{ background: {c['BG_RAISED']}; border-color: {c['DANGER']}; color: {c['DANGER']}; }}

/* ---- fields and editors -------------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListView, QTreeView {{
    background: {c['BG_INPUT']}; border: 1px solid {c['BORDER']}; border-radius: 2px;
    padding: 3px 6px; selection-background-color: {c['SELECTION']}; selection-color: #ffffff;
}}
QPlainTextEdit, QTextEdit {{ background: {c['BG_EDIT']}; padding: 6px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {c['ACCENT']};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {c['FG_FAINT']}; background: {c['BG_EDIT']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {c['BG_PANEL']}; border: 1px solid {c['BORDER_SOFT']};
    selection-background-color: {c['ACCENT']}; selection-color: #ffffff; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {c['BG_RAISED']}; border: none; width: 14px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {c['BG_HOVER']}; }}

/* ---- tables and lists ---------------------------------------------------- */
QHeaderView::section {{
    background: {c['BG_PANEL']}; color: {c['FG_DIM']}; border: none;
    border-right: 1px solid {c['BORDER']}; border-bottom: 1px solid {c['BORDER']};
    padding: 5px 7px; font-weight: 600;
}}
QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget {{
    background: {c['BG_INPUT']}; alternate-background-color: {c['BG_ALT']};
    border: 1px solid {c['BORDER']}; gridline-color: #383838;
    selection-background-color: {c['SELECTION']}; selection-color: #ffffff;
}}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{ padding: 3px 5px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {c['SELECTION']}; }}

/* ---- scrollbars ---------------------------------------------------------- */
QScrollBar:vertical {{ background: {c['BG_WINDOW']}; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #4a4a4a; min-height: 24px; border-radius: 5px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #5a5a5a; }}
QScrollBar:horizontal {{ background: {c['BG_WINDOW']}; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #4a4a4a; min-width: 24px; border-radius: 5px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: #5a5a5a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- misc ----------------------------------------------------------------- */
QSplitter::handle {{ background: {c['BORDER']}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}
QProgressBar {{
    background: {c['BG_INPUT']}; border: 1px solid {c['BORDER']}; border-radius: 2px;
    text-align: center; color: {c['FG_BRIGHT']}; min-height: {k['ctrl_h'] - 4}px;
}}
QProgressBar::chunk {{ background: {c['ACCENT']}; }}
QCheckBox, QRadioButton, QLabel {{ background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
QCheckBox::indicator:unchecked {{ background: {c['BG_INPUT']}; border: 1px solid {c['BORDER_SOFT']}; border-radius: 2px; }}
QCheckBox::indicator:checked {{ background: {c['ACCENT']}; border: 1px solid {c['ACCENT']}; border-radius: 2px; }}
QCheckBox::indicator:disabled {{ background: {c['BG_EDIT']}; border: 1px solid {c['BORDER']}; }}
QRadioButton::indicator:unchecked {{ background: {c['BG_INPUT']}; border: 1px solid {c['BORDER_SOFT']}; border-radius: 7px; }}
QRadioButton::indicator:checked {{ background: {c['ACCENT']}; border: 1px solid {c['ACCENT']}; border-radius: 7px; }}
QToolTip {{
    background: {c['BG_PANEL']}; color: {c['FG_TEXT']}; border: 1px solid {c['BORDER_SOFT']};
    padding: 4px 6px;
}}
QScrollBar:vertical:hover, QScrollBar:horizontal:hover {{ background: {c['BG_WINDOW']}; }}
QToolButton#AccentButton {{ background: {c['ACCENT']}; color: #ffffff; }}
"""


#: Backwards compatible default stylesheet.
STYLESHEET = build_stylesheet("comfortable")


def mono_font(size: int = 10) -> QFont:
    """A monospaced font suitable for data and hex dumps."""
    font = QFont("Cascadia Mono")
    if not QFontDatabase.hasFamily("Cascadia Mono"):
        font = QFont("Consolas")
    if not QFontDatabase.hasFamily("Consolas"):
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSize(size)
    font.setStyleHint(QFont.Monospace)
    return font


def ui_font(size: int = 9) -> QFont:
    font = QFont("Segoe UI")
    if not QFontDatabase.hasFamily("Segoe UI"):
        font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    font.setPointSize(size)
    return font


def control_height(density: str = "comfortable") -> int:
    """Pixel height of a standard control, used by the pages to align rows."""
    return (DENSITY.get(density) or DENSITY["comfortable"])["ctrl_h"]


def apply_theme(app, density: str = "comfortable") -> None:
    """Apply the dark palette and stylesheet to an application."""
    app.setStyle("Fusion")
    knobs = DENSITY.get(density) or DENSITY["comfortable"]
    role = QPalette.ColorRole
    palette = QPalette()
    palette.setColor(role.Window, BG_WINDOW)
    palette.setColor(role.WindowText, FG_TEXT)
    palette.setColor(role.Base, BG_INPUT)
    palette.setColor(role.AlternateBase, BG_ALT)
    palette.setColor(role.Text, FG_TEXT)
    palette.setColor(role.Button, BG_PANEL)
    palette.setColor(role.ButtonText, FG_TEXT)
    palette.setColor(role.ToolTipBase, BG_PANEL)
    palette.setColor(role.ToolTipText, FG_TEXT)
    palette.setColor(role.Highlight, SELECTION)
    palette.setColor(role.HighlightedText, QColor(255, 255, 255))
    palette.setColor(role.PlaceholderText, FG_DIM)
    palette.setColor(role.Link, ACCENT_SOFT)
    palette.setColor(QPalette.ColorGroup.Disabled, role.Text, FG_FAINT)
    palette.setColor(QPalette.ColorGroup.Disabled, role.WindowText, FG_FAINT)
    palette.setColor(QPalette.ColorGroup.Disabled, role.ButtonText, FG_FAINT)
    app.setPalette(palette)
    app.setFont(ui_font(knobs["font"]))
    app.setStyleSheet(build_stylesheet(density))


def _selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    sheet = build_stylesheet("comfortable")
    compact = build_stylesheet("compact")
    check("stylesheet builds", isinstance(sheet, str) and "QTabBar::tab" in sheet)
    check("accent colour present", "#1473e6" in sheet)
    check("compact density differs", compact != sheet)
    check("unknown density falls back", build_stylesheet("nope") == sheet)
    check("control height by density",
          control_height("compact") < control_height("comfortable"),
          f"{control_height('compact')} vs {control_height('comfortable')}")
    check("public colours exported",
          all(isinstance(col, QColor) for col in (ACCENT, OK, WARN, DANGER, FG_DIM, ICON,
                                                  ICON_ACTIVE, ICON_DISABLED, ICON_ACCENT)))

    print()
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
