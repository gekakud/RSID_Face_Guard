"""
Relay (door strike) hardware API.

Moved unchanged from the project root relay_api.py. Public API:
    initialize_relay(relay_pin, active_low, default_off)
    open_door(seconds)
    disconnect_relay()
"""

import time

from observability.logging_setup import get_logger

log = get_logger("relay")

try:
    import lgpio
except ImportError:
    lgpio = None
    log.warning("lgpio not available -- relay control disabled")

# GPIO chip number: RPi 5 uses chip 4, earlier RPi models use chip 0
GPIO_CHIP = 4


class RelayController:
    def __init__(self, relay_pin: int, active_low: bool = True, default_off: bool = True):
        self.relay_pin = relay_pin
        self.active_low = active_low
        self.default_off = default_off
        self._handle = None
        self._unavailable = False

    def initialize(self):
        if lgpio is None or self._unavailable:
            return

        if self._handle is not None:
            return

        try:
            self._handle = lgpio.gpiochip_open(GPIO_CHIP)
            off_level = 1 if self.active_low else 0
            on_level = 0 if self.active_low else 1
            initial = off_level if self.default_off else on_level
            lgpio.gpio_claim_output(self._handle, self.relay_pin, initial)
            log.info("Relay initialized on GPIO %s, chip %s (active_low=%s)", self.relay_pin, GPIO_CHIP, self.active_low)
        except Exception as e:
            log.error("Relay GPIO initialization failed: %s. Relay disabled.", e)
            self._unavailable = True
            if self._handle is not None:
                try:
                    lgpio.gpiochip_close(self._handle)
                except Exception:
                    pass
                self._handle = None

    def _set_on(self):
        if lgpio is None or self._handle is None:
            return
        lgpio.gpio_write(self._handle, self.relay_pin, 0 if self.active_low else 1)

    def _set_off(self):
        if lgpio is None or self._handle is None:
            return
        lgpio.gpio_write(self._handle, self.relay_pin, 1 if self.active_low else 0)

    def open_door(self, seconds: float = 3.0):
        if lgpio is None or self._unavailable:
            log.info("[SIM] open_door(%s)", seconds)
            return

        if self._handle is None:
            self.initialize()

        self._set_on()
        time.sleep(max(0.0, seconds))
        self._set_off()

    def disconnect(self):
        if lgpio is None or self._handle is None:
            return
        try:
            self._set_off()
        except Exception:
            pass
        try:
            lgpio.gpiochip_close(self._handle)
        except Exception:
            pass
        self._handle = None
        log.info("Relay GPIO cleaned up")


# =====================================================
# Simple module-level API
# =====================================================

_relay = None


def initialize_relay(relay_pin: int = 18, active_low: bool = True, default_off: bool = True):
    global _relay
    _relay = RelayController(relay_pin, active_low=active_low, default_off=default_off)
    _relay.initialize()


def open_door(seconds: float = 3.0):
    if _relay is None:
        initialize_relay()
    _relay.open_door(seconds)


def disconnect_relay():
    global _relay
    if _relay is not None:
        _relay.disconnect()
        _relay = None