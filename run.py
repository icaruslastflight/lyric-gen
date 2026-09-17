#!/usr/bin/env python3
"""Lyric Gen — default public entry: standalone GUI.

Run:
  py -3 run.py
  py -3 -m lyric_gen        (same GUI)
Double-click: Lyric Gen.bat

Interconnect / developer bridge (no GUI): run_interconnect.py
"""

import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()  # PyInstaller-friendly
    from lyric_gen.standalone.gui import run_gui

    run_gui()


if __name__ == "__main__":
    main()
