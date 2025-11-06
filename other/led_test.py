import time, board, neopixel

NUM_LEDS = 19
pixels = neopixel.NeoPixel(
    board.D26, NUM_LEDS,
    brightness=0.6,
    auto_write=False,  
)

def all_off():
    pixels.fill((0,0,0))
    pixels.show()

all_off()



for i in range(0,6):
    pixels[i] = (0, 255, 0)

for i in range(13,19):
    pixels[i] = (0, 255, 0)

pixels.show()

time.sleep(2)
for i in range(0,6):
    pixels[i] = (255, 0, 0)

for i in range(13,19):
    pixels[i] = (255, 0, 0)

pixels.show()
time.sleep(2)

all_off()

