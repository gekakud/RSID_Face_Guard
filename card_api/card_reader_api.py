# file: wiegand_reader.py
#!/usr/bin/env python3
"""
Raspberry Pi 5 Wiegand reader using lgpio (RP1-friendly)

Public API:
    initialize_card_reader(chip=0, d0_pin=17, d1_pin=27, gap=0.030)
    get_card_id(timeout=None) -> int | None          # returns 32-bit raw value
    disconnect_card_reader()
"""

import time
import threading
import queue
from typing import Optional
# evgeny 1110447364
# alon 1241789444
import lgpio

# --------- Defaults (BCM numbering) ----------
DEFAULT_CHIP   = 0        # /dev/gpiochip0
DEFAULT_D0_PIN = 17       # BCM pin for Wiegand D0
DEFAULT_D1_PIN = 27       # BCM pin for Wiegand D1
DEFAULT_GAP    = 0.030    # seconds: inter-bit timeout that ends a frame

# ====== Internal singleton to keep things simple ======
class _WiegandReader:
    def __init__(self, chip: int, d0_pin: int, d1_pin: int, gap: float):
        self.chip_index = chip
        self.d0 = d0_pin
        self.d1 = d1_pin
        self.gap = gap

        self._h = None
        self._cb0 = None
        self._cb1 = None

        self._bits = []
        self._last = time.monotonic()
        self._lock = threading.Lock()

        # completed frames (raw int) go here
        self._frames = queue.Queue()

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._frame_watcher, daemon=True)

    # ---- low-level: edge callback ----
    def _on_edge(self, _chip, gpio, level, tick):
        # Falling edges are the data pulses; D0 -> bit 0, D1 -> bit 1
        now = time.monotonic()
        with self._lock:
            if gpio == self.d0:
                self._bits.append(0)
                self._last = now
            elif gpio == self.d1:
                self._bits.append(1)
                self._last = now

    # ---- background: detect frame gap, emit integer ----
    def _frame_watcher(self):
        while not self._stop.is_set():
            time.sleep(0.001)
            with self._lock:
                if self._bits and (time.monotonic() - self._last) > self.gap:
                    # finalize current frame
                    value = 0
                    for b in self._bits:
                        value = (value << 1) | b
                    n = len(self._bits)
                    self._bits = []

                    # Only enqueue strict 32-bit frames as requested
                    if n == 32:
                        self._frames.put(value)
                    # (If you ever want W26/W34, you can push them here too.)

    # ---- public-ish lifecycle ----
    def start(self):
        if self._h is not None:
            return  # already started
        self._h = lgpio.gpiochip_open(self.chip_index)

        FLAGS = lgpio.SET_PULL_UP  # Wiegand lines are open-collector -> pull-ups
        lgpio.gpio_claim_alert(self._h, self.d0, lgpio.FALLING_EDGE, FLAGS)
        lgpio.gpio_claim_alert(self._h, self.d1, lgpio.FALLING_EDGE, FLAGS)

        # keep references so we can cancel later
        self._cb0 = lgpio.callback(self._h, self.d0, lgpio.FALLING_EDGE, self._on_edge)
        self._cb1 = lgpio.callback(self._h, self.d1, lgpio.FALLING_EDGE, self._on_edge)

        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._frame_watcher, daemon=True)
            self._thread.start()

    def get_32bit(self, timeout: Optional[float] = None) -> Optional[int]:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._stop.set()
        try:
            if self._cb0: self._cb0.cancel()
        except Exception:
            pass
        try:
            if self._cb1: self._cb1.cancel()
        except Exception:
            pass
        if self._h is not None:
            try:
                lgpio.gpiochip_close(self._h)
            finally:
                self._h = None

# Singleton instance holder
_instance: Optional[_WiegandReader] = None

# ====== Public API ======
def initialize_card_reader(
    chip: int = DEFAULT_CHIP,
    d0_pin: int = DEFAULT_D0_PIN,
    d1_pin: int = DEFAULT_D1_PIN,
    gap: float = DEFAULT_GAP,
):
    """Open gpiochip, configure D0/D1 alerts, and start the parser loop."""
    global _instance
    if _instance is None:
        _instance = _WiegandReader(chip, d0_pin, d1_pin, gap)
    _instance.start()

def get_card_id(timeout: Optional[float] = None) -> Optional[int]:
    """
    Return next 32-bit Wiegand raw value (integer), or None on timeout.
    """
    if _instance is None:
        raise RuntimeError("Card reader not initialized. Call initialize_card_reader() first.")
    return _instance.get_32bit(timeout=timeout)

def disconnect_card_reader():
    """Cancel callbacks, stop the loop, and close the chip."""
    global _instance
    if _instance is not None:
        _instance.stop()
        _instance = None

from card_writer_api import initialize_wiegand_tx, send_w32, close_wiegand_tx

# Optional CLI for quick testing
if __name__ == "__main__":
    initialize_card_reader()
   # initialize_wiegand_tx(d0_pin=22, d1_pin=23, t_low_us=80, t_space_us=2000, active_high=True)

    print("Listening… present a card (Ctrl+C to exit)")
    try:
        while True:
            val = get_card_id(timeout=2.0)
            if val is not None:
                print(f"Wiegand 32-bit value: 0x{val:08X} ({val})")
                print(f"Read 0x{val:08X} ({val}); echoing to controller...")
         #       send_w32(val)   # exact same 32 raw bits, no parity added/removed
    except KeyboardInterrupt:
        pass
    finally:
        disconnect_card_reader()
