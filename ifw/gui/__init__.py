"""Qt desktop interface for the Image Forensics Workbench.

Importing this package is safe without a display; ``run`` builds the
``QApplication``, applies the dark theme and opens the main window. The GUI on
its own needs no optional dependency beyond PySide6, NumPy and Pillow.
"""

from __future__ import annotations

import sys


def run(argv=None) -> int:
    """Launch the workbench and return the Qt event-loop exit code.

    ``argv`` defaults to ``sys.argv``. A path supplied as the first argument is
    opened straight away, so the tool can be registered as a file handler.
    """
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME, APP_ORGANISATION, APP_VERSION
    from ..config import Settings
    from .main import MainWindow
    from .theme import apply_theme

    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORGANISATION)

    settings = Settings.load()
    density = "comfortable"
    value = settings.get("theme_density")
    if isinstance(value, str) and value in ("comfortable", "compact"):
        density = value
    apply_theme(app, density=density)

    path = argv[1] if len(argv) > 1 else None
    window = MainWindow(path)
    window.show()
    try:
        return app.exec()
    finally:
        # Tear the window down explicitly while the application object is still
        # alive. Letting both be collected during interpreter finalisation is a
        # known source of shutdown aborts with PySide6.
        window.close()
        window.deleteLater()
        app.processEvents()


__all__ = ["run"]
