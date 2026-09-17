#!/usr/bin/env python3
"""
Delegate to Interconnect's launcher entry so this works from the lyric-gen repo:

  cd D:\\claude projects\\lyric-gen
  py -3 tools/lyric_gen_bridge.py --profile dev --run

The canonical script lives at interconnect/tools/lyric_gen_bridge.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LYRIC_GEN_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ic = Path(os.environ.get("INTERCONNECT_ROOT", str(_LYRIC_GEN_ROOT.parent / "interconnect"))).resolve()
    bridge = ic / "tools" / "lyric_gen_bridge.py"
    if not bridge.is_file():
        print(
            "Cannot find Interconnect bridge at:\n  ",
            bridge,
            "\nClone `interconnect` next to `lyric-gen` or set INTERCONNECT_ROOT.",
            file=sys.stderr,
            sep="",
        )
        sys.exit(1)
    os.environ.setdefault("LYRIC_GEN_ROOT", str(_LYRIC_GEN_ROOT))
    os.environ.setdefault("INTERCONNECT_ROOT", str(ic))
    raise SystemExit(subprocess.call([sys.executable, str(bridge), *sys.argv[1:]]))


if __name__ == "__main__":
    main()
