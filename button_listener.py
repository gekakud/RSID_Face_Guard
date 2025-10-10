from gpiozero import Button
from signal import pause

class ButtonListener:
    def __init__(self, pin, callback):
        self.button = Button(pin, pull_up=True, bounce_time=0.2)
        self.button.when_pressed = callback

    def start(self):
        print("Button listener started. Press the button to trigger the callback.")
        pause()  # Keep the program running to listen for button presses

if __name__ == "__main__":
    def on_button_press():
        print("Button pressed!")
        # call your authenticate() or trigger function here

    listener = ButtonListener(pin=16, callback=on_button_press)  # GPIO pin 16
    listener.start()