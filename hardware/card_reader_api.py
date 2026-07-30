"""
Unified card reader / writer hardware API.

Selects real GPIO-backed Wiegand hardware (card_api package) or the
simulated stand-in (card_api_sim module) based on config.SIMULATE_CARD_READER,
so callers (face_auth, GUI) never need to branch on that flag
themselves -- they just import from here.

Public API:
    initialize_card_reader()
    get_card_id(timeout=None) -> int | None
    disconnect_card_reader()
    initialize_wiegand_tx()
    send_w32(value)
    close_wiegand_tx()
"""

import sys

import config

if config.SIMULATE_CARD_READER:
    from card_api.card_read_write_simulator import (
        initialize_card_reader as _initialize_card_reader,
        get_card_id as _get_card_id,
        disconnect_card_reader as _disconnect_card_reader,
        initialize_wiegand_tx as _initialize_wiegand_tx,
        send_w32 as _send_w32,
        close_wiegand_tx as _close_wiegand_tx,
    )
else:
    try:
        from card_api.wiegand_card_reader import (
            initialize_card_reader as _initialize_card_reader,
            get_card_id as _get_card_id,
            disconnect_card_reader as _disconnect_card_reader,
        )
        from card_api.wiegand_card_writer import (
            initialize_wiegand_tx as _initialize_wiegand_tx,
            send_w32 as _send_w32,
            close_wiegand_tx as _close_wiegand_tx,
        )
    except ImportError:
        print("CRITICAL: Card API module not available.")
        sys.exit(1)

def initialize_card_reader():
    return _initialize_card_reader()

def get_card_id(timeout=None):
    return _get_card_id(timeout=timeout)

def disconnect_card_reader():
    return _disconnect_card_reader()

def initialize_wiegand_tx():
    return _initialize_wiegand_tx()

def send_w32(value: int):
    return _send_w32(value)

def close_wiegand_tx():
    return _close_wiegand_tx()