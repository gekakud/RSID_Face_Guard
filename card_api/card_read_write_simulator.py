"""
Card API Simulation Module
Stub functions for testing without physical card reader hardware
"""

print('[SIMULATE_CARD_READER] Card hardware simulation enabled')
import time

def initialize_card_reader():
    """Simulated card reader initialization"""
    print('[SIMULATE_CARD_READER] Card reader initialized (simulated)')


# Fake card ID returned by get_card_id() when card-reader simulation is
# active, so the auth flow can be exercised without physical hardware.
SIMULATED_CARD_ID = 1230007405


def get_card_id(timeout=1.0):
    """Simulated card ID read - returns a fixed fake card ID."""
    time.sleep(0.1)
    return SIMULATED_CARD_ID


def disconnect_card_reader():
    """Simulated card reader disconnect"""
    print('[SIMULATE_CARD_READER] Card reader disconnected (simulated)')


def initialize_wiegand_tx():
    """Simulated Wiegand transmitter initialization"""
    print('[SIMULATE_CARD_READER] Wiegand TX initialized (simulated)')


def send_w32(card_id):
    """Simulated Wiegand W32 send and BAKAR"""
    print(f'[SIMULATE_CARD_READER] Would send W32: {card_id}')


def send_w32_parity_1_30_1(card_id):
    """Simulated Wiegand W32 send with parity"""
    print(f'[SIMULATE_CARD_READER] Would send W32 (parity 1-30-1): {card_id}')


def close_wiegand_tx():
    """Simulated Wiegand transmitter close"""
    print('[SIMULATE_CARD_READER] Wiegand TX closed (simulated)')
