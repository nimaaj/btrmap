"""Application entry point — creates :class:`~PyQt6.QtWidgets.QApplication` and shows :class:`~btrmap.ui.main_window.MainWindow`."""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from btrmap.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("btrmap")
    app.setOrganizationName("btrmap")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
