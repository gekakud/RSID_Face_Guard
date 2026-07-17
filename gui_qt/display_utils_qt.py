"""
Display placement helper for the PySide6 GUI -- thin Qt-facing wrapper
around the same xrandr parsing logic used by the Tkinter GUI.
"""

from typing import Optional, Tuple

from gui.display_utils import parse_xrandr_connected

def find_small_display_geometry(prefer_w=800, prefer_h=480) -> Optional[Tuple[int, int]]:
    """Return (x, y) for the preferred small display if found."""
    displays = parse_xrandr_connected()
    for name, w, h, x, y in displays:
        if w == prefer_w and h == prefer_h:
            return (x, y)
    return None