"""DroidForenX Professional Desktop — Entry Point."""
import sys
import os
import traceback
import logging

# Ensure the desktop package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Global exception handler — prevents silent crashes
def _excepthook(exc_type, exc_value, exc_tb):
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.error(f"Unhandled exception:\n{tb}")
    print(f"FATAL ERROR:\n{tb}", file=sys.stderr)

sys.excepthook = _excepthook

# Also catch Qt internal errors
def _qt_message_handler(msg_type, context, message):
    if msg_type == 2:  # QtFatalMsg
        logging.error(f"Qt Fatal: {message}")
    elif msg_type == 3:  # QtCriticalMsg
        logging.warning(f"Qt Critical: {message}")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, qInstallMessageHandler
from PyQt6.QtGui import QFont

from desktop import __version__

from desktop.core.database import init_db, audit_log

qInstallMessageHandler(_qt_message_handler)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(os.path.expanduser("~"), ".droidforenx", "droidforenx.log"), mode="a"),
        ],
    )

    app = QApplication(sys.argv)
    app.setApplicationName("DroidForenX")
    app.setOrganizationName("DroidForenX")
    app.setApplicationVersion(__version__)

    # Set before the first window exists so the taskbar button is branded from
    # the moment the process starts, not after the main window maps.
    from desktop.ui import branding
    app.setWindowIcon(branding.app_icon())

    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    # Owns the light/dark/system preference. Installed after the application
    # name so QSettings has somewhere to persist it, and before the first
    # window is built so the chosen mode is what opens.
    from desktop.ui.theme import theme
    theme.install(app)

    init_db()
    audit_log("app_launch", details="Desktop application started")

    from desktop.ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
