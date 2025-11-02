#!/usr/bin/env python3
# Wiegand reader for Raspberry Pi 5 using lgpio callbacks (RP1-friendly)

import lgpio, time

CHIP   = 0          # /dev/gpiochip0
D0_PIN = 17         # BCM for Wiegand D0  (adjust!)
D1_PIN = 27         # BCM for Wiegand D1  (adjust!)
GAP    = 0.030      # 30 ms inter-bit timeout -> end of frame

h = lgpio.gpiochip_open(CHIP)

# Claim inputs with pull-ups; Wiegand pulses are active-low
FLAGS = lgpio.SET_PULL_UP
lgpio.gpio_claim_alert(h, D0_PIN, lgpio.FALLING_EDGE, FLAGS)
lgpio.gpio_claim_alert(h, D1_PIN, lgpio.FALLING_EDGE, FLAGS)

bits = []
last = time.monotonic()

def flush():
    global bits
    if not bits:
        return
    # Convert bit list to int and print (with simple W26/W34 decode)
    value = 0
    for b in bits:
        value = (value << 1) | b
    n = len(bits)
    if n == 26:
        p0 = (value >> 25) & 1
        data = (value >> 1) & ((1 << 24) - 1)
        p1 = value & 1
        facility = (data >> 16) & 0xFF
        card = data & 0xFFFF
        print(f"Wiegand-26: raw=0x{value:07X} facility={facility} card={card} p0={p0} p1={p1}")
    elif n == 34:
        p0 = (value >> 33) & 1
        data = (value >> 1) & ((1 << 32) - 1)
        p1 = value & 1
        facility = (data >> 16) & 0xFFFF
        card = data & 0xFFFF
        print(f"Wiegand-34: raw=0x{value:09X} facility={facility} card={card} p0={p0} p1={p1}")
    else:
        print(f"Wiegand {n}-bit: 0x{value:X} ({value})")
    bits = []

def on_edge(chip, gpio, level, tick):
    # lgpio callback signature: (handle, gpio, level, tick)  ← docs term it as “callback(handle, gpio, edge, func)”
    # Falling edge means a bit arrived. 0-bit on D0, 1-bit on D1.
    global last
    if gpio == D0_PIN:
        bits.append(0)
        last = time.monotonic()
    elif gpio == D1_PIN:
        bits.append(1)
        last = time.monotonic()

# Register callbacks for both lines
cb0 = lgpio.callback(h, D0_PIN, lgpio.FALLING_EDGE, on_edge)
cb1 = lgpio.callback(h, D1_PIN, lgpio.FALLING_EDGE, on_edge)

print(f"Listening on D0={D0_PIN}, D1={D1_PIN} … Ctrl+C to quit")
try:
    while True:
        if time.monotonic() - last > GAP:
            flush()
        time.sleep(0.001)
except KeyboardInterrupt:
    pass
finally:
    # Callbacks auto-unregister when the chip is closed, but be explicit:
    try: cb0.cancel()
    except Exception: pass
    try: cb1.cancel()
    except Exception: pass
    lgpio.gpiochip_close(h)
