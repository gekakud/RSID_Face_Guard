"""
Display placement helper for the PySide6 GUI.

Self-contained: parses `xrandr --query` to find a preferred small
touchscreen's geometry so the kiosk window can be placed on it.
"""

from observability.logging_setup import get_logger

log = get_logger("gui")

import re
import subprocess
import sys
from typing import Optional, Tuple

def parse_xrandr_connected():
    """
    Parse xrandr and return list of connected displays:
    [(name, w, h, x, y), ...]
    """
    displays = []
    if not sys.platform.startswith("linux"):
        return displays
    try:
        out = subprocess.check_output(["xrandr", "--query"], text=True, stderr=subprocess.DEVNULL)
        # Example line:
        # HDMI-1 connected primary 1920x1080+0+0 ...
        # DSI-1 connected 800x480+1920+0 ...
        pat = re.compile(
            r"^(?P<name>\S+)\s+connected(?:\s+primary)?\s+"
            r"(?P<w>\d+)x(?P<h>\d+)\+(?P<x>\d+)\+(?P<y>\d+)"
        )
        for line in out.splitlines():
            m = pat.search(line.strip())
            if m:
                displays.append((
                    m.group("name"),
                    int(m.group("w")),
                    int(m.group("h")),
                    int(m.group("x")),
                    int(m.group("y")),
                ))
    except Exception as e:
        log.warning("xrandr parse failed: %s", e)
    return displays

def find_small_display_geometry(prefer_w=800, prefer_h=480) -> Optional[Tuple[int, int]]:
    """Return (x, y) for the preferred small display if found."""
    displays = parse_xrandr_connected()
    for name, w, h, x, y in displays:
        if w == prefer_w and h == prefer_h:
            return (x, y)
    return None