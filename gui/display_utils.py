"""
Display placement helpers for kiosk-mode deployment on the small
touchscreen (positioning, wmctrl fallback).
"""

import logging
import re
import subprocess
import sys
import time
from shutil import which
from typing import Optional, Tuple

log = logging.getLogger("face_guard")

def is_command_available(cmd: str) -> bool:
    """Return True if the given shell command exists on PATH."""
    return which(cmd) is not None

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
        print("xrandr parse failed:", e)
    return displays

def find_small_display_xy(prefer_w=800, prefer_h=480) -> Optional[Tuple[int, int, str]]:
    """
    Return (x, y, output_name) for preferred small display if found.
    """
    displays = parse_xrandr_connected()
    for name, w, h, x, y in displays:
        if w == prefer_w and h == prefer_h:
            return (x, y, name)
    return None

def wmctrl_force_move_resize(win, x: int, y: int, w: int, h: int, window_name: str):
    """Fallback: force move/resize using wmctrl if available."""
    if not is_command_available("wmctrl"):
        return
    try:
        # Needs window to have a title and be mapped
        win.update_idletasks()
        time.sleep(0.05)
        title = win.title() or window_name
        # Remove maximized flags first (some WMs ignore move while maximized)
        subprocess.run(["wmctrl", "-r", title, "-b", "remove,maximized_vert,maximized_horz"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Move/resize: gravity=0,x,y,w,h
        subprocess.run(["wmctrl", "-r", title, "-e", f"0,{x},{y},{w},{h}"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.warning("wmctrl fallback failed: %s", e)