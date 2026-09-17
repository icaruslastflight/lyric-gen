#!/usr/bin/env python3
"""Lyric Gen — Interconnect ToolBase entry (headless bridge).

For normal (public) use, run the GUI: ``py -3 run.py`` or ``Lyric Gen.bat``.

This script imports ``tool_template.ToolBase`` from the **interconnect** repo.
It is **not** registered in Interconnect's ``launcher_service`` / manifest in
this workspace layout — start it manually when you want headless Lyric Gen
alongside other bridges.

Environment:
  INTERCONNECT_ROOT — absolute path to the ``interconnect`` checkout (default:
    sibling ``../interconnect`` from this file).
  INTERCONNECT_PROFILE — optional; merged into Lyric Gen Resolume OSC targets.
  LYRICGEN_LISTEN_PORT — optional; default 7020 when INTERCONNECT_ROOT is set
    (avoids clashing with port 7001).
"""

import os
import sys

_INTERCONNECT_DEFAULT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "interconnect")
)
INTERCONNECT_ROOT = os.environ.get("INTERCONNECT_ROOT", _INTERCONNECT_DEFAULT)
if os.path.isdir(INTERCONNECT_ROOT):
    INTERCONNECT_ROOT = os.path.normpath(os.path.abspath(INTERCONNECT_ROOT))
    # Required by lyric_gen.interconnect.tool: profile merge, hub forward, and 7020 cmd port.
    os.environ["INTERCONNECT_ROOT"] = INTERCONNECT_ROOT
    sys.path.insert(0, INTERCONNECT_ROOT)
else:
    print(
        "[run_interconnect] INTERCONNECT_ROOT is not a directory:",
        INTERCONNECT_ROOT,
        "\nSet INTERCONNECT_ROOT to your interconnect checkout.",
        file=sys.stderr,
    )
    sys.exit(1)

from lyric_gen.interconnect.tool import LyricGenTool

if __name__ == "__main__":
    LyricGenTool().run()
