"""
Unified card reader / writer hardware API.

Selects a card-reader backend based on config.CARD_READER_BACKEND:
    "gwiot_hid"    -> real GWIOT USB HID keyboard-emulation reader (evdev),
                      the main reader hardware going forward.
    "wiegand_gpio" -> real Wiegand GPIO reader (lgpio), older hardware.
    "simulated"    -> card_backends_impl.card_read_write_simulator module.

The Wiegand *transmitter* (send_w32 to the external access-control panel)
is a separate physical concern from the reader -- it always comes from
wiegand_card_writer.py (or the simulator's writer when backend=="simulated"),
regardless of which reader hardware is in use, since GWIOT is read-only.

Callers (face_auth, GUI) never need to branch on config.CARD_READER_BACKEND
themselves -- they just import from here.

Public API:
    initialize_card_reader()
    get_card_id(timeout=None) -> int | str | None
    disconnect_card_reader()
    initialize_wiegand_writer()
    send_w32(value)
    close_wiegand_writer()
"""

from observability.logging_setup import get_logger

log = get_logger("card")

import sys

import config

if config.CARD_READER_BACKEND == "simulated":
    from card_backends_impl.card_read_write_simulator import (
        initialize_card_reader as _initialize_card_reader,
        get_card_id as _get_card_id,
        disconnect_card_reader as _disconnect_card_reader,
        initialize_wiegand_tx as _initialize_wiegand_tx,
        send_w32 as _send_w32,
        close_wiegand_tx as _close_wiegand_tx,
    )
elif config.CARD_READER_BACKEND == "gwiot_hid":
    try:
        from card_backends_impl.gwiot_hid_card_reader import (
            initialize_card_reader as _initialize_card_reader,
            get_card_id as _get_card_id,
            disconnect_card_reader as _disconnect_card_reader,
        )
        from card_backends_impl.wiegand_card_writer import (
            initialize_wiegand_tx as _initialize_wiegand_tx,
            send_w32 as _send_w32,
            close_wiegand_tx as _close_wiegand_tx,
        )
    except ImportError:
        log.critical("GWIOT HID card reader backend not available.")
        sys.exit(1)
elif config.CARD_READER_BACKEND == "wiegand_gpio":
    try:
        from card_backends_impl.wiegand_card_reader import (
            initialize_card_reader as _initialize_card_reader,
            get_card_id as _get_card_id,
            disconnect_card_reader as _disconnect_card_reader,
        )
        from card_backends_impl.wiegand_card_writer import (
            initialize_wiegand_tx as _initialize_wiegand_tx,
            send_w32 as _send_w32,
            close_wiegand_tx as _close_wiegand_tx,
        )
    except ImportError:
        log.critical("Wiegand GPIO card reader backend not available.")
        sys.exit(1)
else:
    log.critical("Unknown CARD_READER_BACKEND: %r", config.CARD_READER_BACKEND)
    sys.exit(1)

# card reader API
def initialize_card_reader():
    return _initialize_card_reader()

def get_card_id(timeout=None):
    return _get_card_id(timeout=timeout)

def disconnect_card_reader():
    return _disconnect_card_reader()

# Wiegand card writer API
def initialize_wiegand_writer():
    return _initialize_wiegand_tx()

def send_w32(value: int):
    return _send_w32(value)

def close_wiegand_writer():
    return _close_wiegand_tx()