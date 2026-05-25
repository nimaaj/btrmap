# src/snapdiff/main.py
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from snapdiff.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("btrfs-snapdiff")
    app.setOrganizationName("btrfs-snapdiff")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
