"""Stable host identifiers; no RealSense SDK or GPIO dependency."""

import os
import re
from pathlib import Path


def get_serial_number():
    override = os.environ.get("DEVICE_SERIAL_OVERRIDE", "").strip()
    if override:
        override = override.lower()
        if re.fullmatch(r"[0-9a-f]{1,16}", override) and int(override, 16):
            return override.zfill(16)
        if re.fullmatch(r"dev-[a-z0-9_-]{1,124}", override):
            return override
        raise ValueError("DEVICE_SERIAL_OVERRIDE must be a nonzero Pi serial or dev- identifier")
    candidates = []
    try:
        candidates.append(Path("/proc/device-tree/serial-number").read_text().strip("\x00 \r\n\t"))
    except OSError:
        pass
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() == "Serial":
                candidates.append(value.strip())
    except OSError:
        pass
    for value in candidates:
        if re.fullmatch(r"[0-9a-fA-F]{1,16}", value) and int(value, 16):
            return value.lower().zfill(16)
    raise ValueError("Raspberry Pi board serial unavailable; set DEVICE_SERIAL_OVERRIDE for Windows development")
