# file: card_writer_api.py
#!/usr/bin/env python3
"""
Raspberry Pi 5 Wiegand *transmitter* using lgpio (RP1-friendly).

Public API:
    initialize_wiegand_tx(chip=0, d0_pin=22, d1_pin=23,
                          t_low_us=80, t_space_us=2000, active_high=True)
    send_w32(value: int)                 # 32 data bits, no parity
    send_w32_parity_1_30_1(value: int)   # 1 parity + 30 data + 1 parity (32 total)
    close_wiegand_tx()
"""

import time
import lgpio

# Defaults (BCM numbering)
DEFAULT_CHIP     = 0       # /dev/gpiochip0
DEFAULT_D0_TX    = 22      # drives IN1 of opto -> D0 line at controller
DEFAULT_D1_TX    = 23      # drives IN2 of opto -> D1 line at controller
DEFAULT_T_LOW_US = 80      # active LOW pulse width (µs) at controller
DEFAULT_T_SP_US  = 2000    # inter-bit spacing (µs)
DEFAULT_ACTIVE_HIGH = True # HIGH on GPIO turns opto ON -> controller line LOW

class _WiegandTx:
    def __init__(self, chip, d0_pin, d1_pin, t_low_us, t_space_us, active_high):
        self.chip = chip
        self.d0 = d0_pin
        self.d1 = d1_pin
        self.t_low_us = max(20, int(t_low_us))
        self.t_space_us = max(200, int(t_space_us))
        self.active_high = bool(active_high)
        self._h = None

    def start(self):
        if self._h is not None:
            return
        self._h = lgpio.gpiochip_open(self.chip)
        # Outputs, idle = opto LED OFF (controller lines HIGH)
        lgpio.gpio_claim_output(self._h, self.d0, 0 if self.active_high else 1)
        lgpio.gpio_claim_output(self._h, self.d1, 0 if self.active_high else 1)

    def _drive(self, pin, on: bool):
        # on=True means "emit active pulse" (controller side goes LOW)
        # If active_high -> ON=1; else ON=0
        val = 1 if (on == self.active_high) else 0
        lgpio.gpio_write(self._h, pin, val)

    def _idle(self, pin):
        # opposite of _drive(on=True)
        val = 0 if self.active_high else 1
        lgpio.gpio_write(self._h, pin, val)

    def _pulse_bit(self, bit1: bool):
        # Wiegand bit: '0' = pulse on D0, '1' = pulse on D1
        pin = self.d1 if bit1 else self.d0
        self._drive(pin, True)                            # active (LOW at controller)
        time.sleep(self.t_low_us / 1_000_000.0)
        self._idle(pin)                                   # back to idle (HIGH at controller)
        time.sleep(self.t_space_us / 1_000_000.0)

    def send_bits_msb_first(self, bits: str):
        # bits like "0101..." MSB first (matches your reader’s shift-left parsing)
        for b in bits:
            self._pulse_bit(b == '1')

    def send_w32(self, value: int):
        # Send exactly 32 data bits, MSB first (no parity)
        bits = f"{value & 0xFFFFFFFF:032b}"
        self.send_bits_msb_first(bits)

    def send_w32_parity_1_30_1(self, value: int):
        """
        Optional: Build a common 32-bit frame with parity:
        P_even over first 15 of 30 data bits, then 30 data bits, then P_odd over last 15.
        Total = 32. Adjust if your controller uses a different 32-bit flavor.
        """
        data30 = f"{value & ((1<<30)-1):030b}"
        first15, last15 = data30[:15], data30[15:]
        p1 = '0' if (first15.count('1') % 2) else '1'  # even parity bit (1 if even)
        p2 = '1' if (last15.count('1') % 2) else '0'   # odd parity bit (1 if odd)
        frame = p1 + data30 + p2
        assert len(frame) == 32
        self.send_bits_msb_first(frame)

    def close(self):
        if self._h is not None:
            try:
                lgpio.gpiochip_close(self._h)
            finally:
                self._h = None

# Singleton-ish helpers
_instance = None

def initialize_wiegand_tx(chip=DEFAULT_CHIP, d0_pin=DEFAULT_D0_TX, d1_pin=DEFAULT_D1_TX,
                          t_low_us=DEFAULT_T_LOW_US, t_space_us=DEFAULT_T_SP_US,
                          active_high=DEFAULT_ACTIVE_HIGH):
    global _instance
    if _instance is None:
        _instance = _WiegandTx(chip, d0_pin, d1_pin, t_low_us, t_space_us, active_high)
    _instance.start()

def send_w32(value: int):
    if _instance is None:
        raise RuntimeError("initialize_wiegand_tx() first.")
    _instance.send_w32(value)

def send_w32_parity_1_30_1(value: int):
    if _instance is None:
        raise RuntimeError("initialize_wiegand_tx() first.")
    _instance.send_w32_parity_1_30_1(value)

def close_wiegand_tx():
    global _instance
    if _instance is not None:
        _instance.close()
        _instance = None

if __name__ == "__main__":
    # quick test: send 0xDEADBEEF once
    initialize_wiegand_tx()
    print("Sending 0xDEADBEEF as 32 data bits...")
    send_w32(0xDEADBEEF)
    close_wiegand_tx()
