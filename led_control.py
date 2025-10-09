#!/usr/bin/env python3

"""
LED Control Module for RealSense ID Authentication
Provides methods to control LED indicators based on authentication status
"""

import time
import threading

try:
    import board
    import neopixel
    LED_AVAILABLE = True
except ImportError:
    print('NeoPixel library not available. LED control disabled.')
    LED_AVAILABLE = False

# Configuration
NUM_LEDS = 19
LED_BRIGHTNESS = 0.6

class LEDController:
    """Control NeoPixel LEDs for authentication feedback"""
    
    def __init__(self):
        """Initialize the LED controller"""
        self.pixels = None
        self.timer = None
        
        if LED_AVAILABLE:
            try:
                self.pixels = neopixel.NeoPixel(
                    board.D21, 
                    NUM_LEDS,
                    brightness=LED_BRIGHTNESS,
                    auto_write=False,
                )
                # Start with LEDs off
                self.all_off()
                print("LED Controller initialized successfully")
            except Exception as e:
                print(f"Failed to initialize LEDs: {e}")
                self.pixels = None
        else:
            print("LED control not available - missing neopixel library")
    
    def all_off(self):
        """Turn off all LEDs"""
        if self.pixels:
            try:
                self.pixels.fill((0, 0, 0))
                self.pixels.show()
            except Exception as e:
                print(f"Error turning off LEDs: {e}")
    
    def all_green(self):
        """Turn all LEDs green (success indicator)"""
        if self.pixels:
            try:
                # Set first 6 and last 6 LEDs to green
                for i in range(0, 6):
                    self.pixels[i] = (0, 255, 0)
                for i in range(13, 19):
                    self.pixels[i] = (0, 255, 0)
                self.pixels.show()
            except Exception as e:
                print(f"Error setting LEDs to green: {e}")
    
    def all_red(self):
        """Turn all LEDs red (failure indicator)"""
        if self.pixels:
            try:
                # Set first 6 and last 6 LEDs to red
                for i in range(0, 6):
                    self.pixels[i] = (255, 0, 0)
                for i in range(13, 19):
                    self.pixels[i] = (255, 0, 0)
                self.pixels.show()
            except Exception as e:
                print(f"Error setting LEDs to red: {e}")
    
    def flash_green(self, duration=3):
        """Flash green LEDs for specified duration then turn off"""
        self._cancel_timer()
        self.all_green()
        self.timer = threading.Timer(duration, self.all_off)
        self.timer.start()
    
    def flash_red(self, duration=3):
        """Flash red LEDs for specified duration then turn off"""
        self._cancel_timer()
        self.all_red()
        self.timer = threading.Timer(duration, self.all_off)
        self.timer.start()
    
    def _cancel_timer(self):
        """Cancel any existing timer"""
        if self.timer and self.timer.is_alive():
            self.timer.cancel()
    
    def cleanup(self):
        """Clean up resources"""
        self._cancel_timer()
        self.all_off()


# Standalone functions for backward compatibility
_controller = None

def get_controller():
    """Get or create the global LED controller instance"""
    global _controller
    if _controller is None:
        _controller = LEDController()
    return _controller

def all_off():
    """Turn off all LEDs"""
    get_controller().all_off()

def all_green():
    """Turn all LEDs green"""
    get_controller().all_green()

def all_red():
    """Turn all LEDs red"""
    get_controller().all_red()


# Test code when run directly
if __name__ == "__main__":
    print("Testing LED Control...")
    
    controller = LEDController()
    
    print("Testing green LEDs for 2 seconds...")
    controller.all_green()
    time.sleep(2)
    
    print("Testing red LEDs for 2 seconds...")
    controller.all_red()
    time.sleep(2)
    
    print("Turning off LEDs...")
    controller.all_off()
    
    print("Testing flash_green (3 seconds)...")
    controller.flash_green(3)
    time.sleep(4)
    
    print("Testing flash_red (3 seconds)...")
    controller.flash_red(3)
    time.sleep(4)
    
    print("Test complete. LEDs should be off.")
    controller.cleanup()
