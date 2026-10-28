# Pixel Garden: Air Merge fixture app with nested binary assets (not run by the simulator).
import os

ROOT = "/system/apps/pixel_garden/assets"


def load(name):
    with open(ROOT + "/" + name, "rb") as fh:
        return fh.read()


TILES = [load("tiles/" + n) for n in ("grass.bin", "flowers.bin", "water.bin")]
PALETTE = load("palette.bin")


def update():
    screen.pen = color.rgb(10, 40, 20)
    screen.clear()
    for i, t in enumerate(TILES):
        screen.pen = color.rgb(PALETTE[i * 3], PALETTE[i * 3 + 1], PALETTE[i * 3 + 2])
        screen.rectangle(20 + i * 100, 80, 80, 80 + t[0] % 40)


run(update)
