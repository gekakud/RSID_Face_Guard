"""
Display placement helpers for kiosk-mode deployment on the small
Waveshare touchscreen (xrandr rotation, positioning, wmctrl fallback).
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

def setup_display_rotation(window_width: int, window_height: int):
    """Ensure the small display is in portrait orientation via xrandr.

    When the Waveshare is the only display it reports its native landscape
    resolution (800x480). We need to rotate it left so it appears as
    480x800 to the window manager and Tkinter, matching window_width x
    window_height. Called once at startup, before the GUI is created.
    """
    if not sys.platform.startswith("linux"):
        return
    if not is_command_available("xrandr"):
        return

    # Already in portrait -- nothing to do.
    if find_small_display_xy(window_width, window_height) is not None:
        log.info("Small display already in portrait orientation (%dx%d).", window_width, window_height)
        return

    # Look for the display in landscape (native 800x480 when we want 480x800).
    displays = parse_xrandr_connected()
    for name, w, h, x, y in displays:
        if w == window_height and h == window_width:
            log.info(
                "Display %s found in landscape (%dx%d) -- applying portrait rotation.",
                name, w, h,
            )
            try:
                subprocess.run(
                    ["xrandr", "--output", name, "--rotate", "left"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("Portrait rotation applied to %s.", name)
                time.sleep(0.8)  # allow the rotation to take effect
            except Exception as e:
                log.warning("xrandr rotation failed for %s: %s", name, e)
            return

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