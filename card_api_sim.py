"""
Card API Simulation Module
Stub functions for testing without physical card reader hardware
"""

print('[SIMULATE_HW] Card hardware simulation enabled')
import time

def initialize_card_reader():
    """Simulated card reader initialization"""
    print('[SIMULATE_HW] Card reader initialized (simulated)')


def get_card_id(timeout=1.0):
    """Simulated card ID read - returns None (no card present)"""
    time.sleep(0.1)
    return None


def disconnect_card_reader():
    """Simulated card reader disconnect"""
    print('[SIMULATE_HW] Card reader disconnected (simulated)')


def initialize_wiegand_tx():
    """Simulated Wiegand transmitter initialization"""
    print('[SIMULATE_HW] Wiegand TX initialized (simulated)')


def send_w32(card_id):
    """Simulated Wiegand W32 send"""
    print(f'[SIMULATE_HW] Would send W32: {card_id}')


def send_w32_parity_1_30_1(card_id):
    """Simulated Wiegand W32 send with parity"""
    print(f'[SIMULATE_HW] Would send W32 (parity 1-30-1): {card_id}')


def close_wiegand_tx():
    """Simulated Wiegand transmitter close"""
    print('[SIMULATE_HW] Wiegand TX closed (simulated)')
