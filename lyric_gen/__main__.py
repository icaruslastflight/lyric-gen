"""Default entry: `py -3 -m lyric_gen` opens the standalone GUI (public / non-developer)."""

from __future__ import annotations

import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()
    from lyric_gen.standalone.gui import run_gui

    run_gui()


if __name__ == "__main__":
    main()
