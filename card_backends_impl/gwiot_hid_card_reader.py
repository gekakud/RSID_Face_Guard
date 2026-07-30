#!/usr/bin/env python3

from pathlib import Path
from typing import Optional

from evdev import InputDevice, categorize, ecodes, list_devices


class GwiotCardReader:
    KEY_MAP = {
        ecodes.KEY_0: "0",
        ecodes.KEY_1: "1",
        ecodes.KEY_2: "2",
        ecodes.KEY_3: "3",
        ecodes.KEY_4: "4",
        ecodes.KEY_5: "5",
        ecodes.KEY_6: "6",
        ecodes.KEY_7: "7",
        ecodes.KEY_8: "8",
        ecodes.KEY_9: "9",
        ecodes.KEY_KP0: "0",
        ecodes.KEY_KP1: "1",
        ecodes.KEY_KP2: "2",
        ecodes.KEY_KP3: "3",
        ecodes.KEY_KP4: "4",
        ecodes.KEY_KP5: "5",
        ecodes.KEY_KP6: "6",
        ecodes.KEY_KP7: "7",
        ecodes.KEY_KP8: "8",
        ecodes.KEY_KP9: "9",
    }

    def __init__(
        self,
        device_path: Optional[str] = None,
        device_name_contains: str = "GWIOT",
    ):
        self.device = self._open_device(
            device_path=device_path,
            device_name_contains=device_name_contains,
        )

        print(
            f"Card reader connected: {self.device.name} "
            f"({self.device.path})"
        )

    @staticmethod
    def _open_device(
        device_path: Optional[str],
        device_name_contains: str,
    ) -> InputDevice:
        if device_path:
            path = Path(device_path)

            if not path.exists():
                raise FileNotFoundError(
                    f"Card reader device does not exist: {device_path}"
                )

            return InputDevice(str(path))

        search_text = device_name_contains.upper()

        for path in list_devices():
            device = InputDevice(path)

            if search_text in device.name.upper():
                return device

            device.close()

        raise RuntimeError(
            f'Could not find an input device containing '
            f'"{device_name_contains}"'
        )

    def wait_for_card_number(self) -> str:
        """
        Wait until the reader sends a card number followed by Enter.

        Returns:
            The card number as a string, for example "2325780402".
        """
        card_buffer = ""

        for event in self.device.read_loop():
            if event.type != ecodes.EV_KEY:
                continue

            key_event = categorize(event)

            # Accept only key-down events.
            # Ignore key release and key repeat events.
            if key_event.keystate != key_event.key_down:
                continue

            key_code = key_event.scancode

            if key_code in self.KEY_MAP:
                card_buffer += self.KEY_MAP[key_code]
                continue

            if key_code in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
                if card_buffer:
                    card_number = card_buffer
                    card_buffer = ""
                    return card_number

            elif key_code == ecodes.KEY_BACKSPACE:
                card_buffer = card_buffer[:-1]

            elif key_code == ecodes.KEY_ESC:
                card_buffer = ""

    def close(self) -> None:
        self.device.close()


def main() -> None:
    reader = GwiotCardReader()

    try:
        while True:
            print("Waiting for card...")

            card_number = reader.wait_for_card_number()

            print(f"Card number: {card_number}")

    except KeyboardInterrupt:
        print("\nStopping card reader.")

    finally:
        reader.close()


if __name__ == "__main__":
    main()