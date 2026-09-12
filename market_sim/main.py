"""Punkt wejścia aplikacji UI."""

from __future__ import annotations

import argparse
import sys

from PyQt5 import QtWidgets

from market_sim.ui.main_window import DEFAULT_INPUT, MainWindow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AMT x CAS — podgląd symulacji")
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=f"plik HDF5 z zapisem symulacji (domyślnie {DEFAULT_INPUT})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("AMT x CAS market simulator")
    window = MainWindow(data_path=args.path)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
