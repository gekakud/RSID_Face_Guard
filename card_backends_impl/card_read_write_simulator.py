"""
Card API Simulation Module
Stub functions for testing without physical card reader hardware
"""

from observability.logging_setup import get_logger

log = get_logger("card")

log.info("Card hardware simulation enabled")
import time

def initialize_card_reader():
    """Simulated card reader initialization"""
    log.info("Card reader initialized (simulated)")


# Fake card ID returned by get_card_id() when card-reader simulation is
# active, so the auth flow can be exercised without physical hardware.
SIMULATED_CARD_ID = 1230007405


def get_card_id(timeout=1.0):
    """Simulated card ID read - returns a fixed fake card ID."""
    time.sleep(0.1)
    return SIMULATED_CARD_ID


def disconnect_card_reader():
    """Simulated card reader disconnect"""
    log.info("Card reader disconnected (simulated)")


def initialize_wiegand_tx():
    """Simulated Wiegand transmitter initialization"""
    log.info("Wiegand TX initialized (simulated)")


def send_w32(card_id):
    """Simulated Wiegand W32 send and BAKAR"""
    log.info("Would send W32: %s", card_id)


def send_w32_parity_1_30_1(card_id):
    """Simulated Wiegand W32 send with parity"""
    log.info("Would send W32 (parity 1-30-1): %s", card_id)


def close_wiegand_tx():
    """Simulated Wiegand transmitter close"""
    log.info("Wiegand TX closed (simulated)")
