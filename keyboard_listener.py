#!/usr/bin/env python3
from evdev import InputDevice, categorize, ecodes, list_devices

def find_keyboard():
    """Auto-detect the first keyboard input device."""
    for path in list_devices():
        dev = InputDevice(path)
        if 'Keyboard' in dev.name or 'kbd' in dev.name.lower():
            print(f"Using keyboard: {dev.name} ({path})")
            return dev
    raise RuntimeError("No keyboard device found")

def main():
    kbd = find_keyboard()
    print("Waiting for SPACE key... (Ctrl+C to exit)")
    for event in kbd.read_loop():
        if event.type == ecodes.EV_KEY and event.value == 1:  # key press
            key = categorize(event)
            if key.keycode == 'KEY_SPACE':
                print("Spacebar pressed!")
                # call your authenticate() or trigger function here

if __name__ == "__main__":
    main()
