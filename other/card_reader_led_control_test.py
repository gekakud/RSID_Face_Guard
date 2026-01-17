#!/usr/bin/env python3
import lgpio
import threading


CHIP = 0          # /dev/gpiochip0

class CardReaderLEDAPI():
    def __init__(self, red_led_pin=5, green_led_pin=6):
        self.chip = CHIP
        self.red_led_pin = red_led_pin
        self.green_led_pin = green_led_pin
        self.h = lgpio.gpiochip_open(self.chip)
        lgpio.gpio_claim_output(self.h, self.red_led_pin, 0)   # idle OFF
        lgpio.gpio_claim_output(self.h, self.green_led_pin, 0)  # idle OFF

    def led_green_on(self, duration = 2):
        lgpio.gpio_write(self.h, self.green_led_pin, 1)  # pull LED line low via opto
        self.timer = threading.Timer(duration, self.led_all_off)
        self.timer.start()

    def led_red_on(self, duration = 2):
        lgpio.gpio_write(self.h, self.red_led_pin, 1)
        self.timer = threading.Timer(duration, self.led_all_off)
        self.timer.start()

    def led_all_off(self):
        lgpio.gpio_write(self.h, self.green_led_pin, 0)
        lgpio.gpio_write(self.h, self.red_led_pin, 0)

    def close(self):
        lgpio.gpiochip_close(self.h)
