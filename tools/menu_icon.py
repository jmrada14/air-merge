"""
Build the Air Merge menu icon for MonaOS.

The launcher (/system/apps/menu) draws each app tile from vector contours in
icons.mpy (compiled from menu/src/icons.py) keyed by the app's folder name, and
plays menu/anim/<slug>.bin once when the tile is selected, holding its last
frame. An app without its own entry in icons.py gets the "default" icon and no
animation, so both files are needed. Contours are flat (x, y, x, y, ...) tuples
in a unit box centred on 0 with y pointing down, filled NON_ZERO.

The icon is a beacon: a dot with three rings around it, cut open at the bottom
by a wedge. Selecting the tile pops the dot and sends the rings out one by one,
then a pulse runs through them.

Usage (desktop CPython):
  python3 -I tools/menu_icon.py --icons-src icons.py --icons-out icons_new.py \
      --anim-out air_merge.bin --preview preview.png
"""

import argparse
import math
import struct
import sys
import zlib

SLUG = "air_merge"
FPS = 30
ANIM_MAGIC = b"BWIC"
ANIM_VERSION = 3

CENTRE = (0.0, 0.06)
DOT = 0.075
RINGS = ((0.14, 0.21), (0.255, 0.325), (0.37, 0.44))  # inner and outer radius
WEDGE = math.radians(32)  # half-angle of the opening at the bottom
ARC_STEPS = 28


def circle(cx, cy, r, steps=20):
    return [(cx + math.cos(2 * math.pi * i / steps) * r, cy + math.sin(2 * math.pi * i / steps) * r)
            for i in range(steps)]


def ring(inner, outer):
    """One ring as a single band, from one side of the wedge round the top to the other."""
    cx, cy = CENTRE
    start = math.pi / 2 + WEDGE   # y points down, so pi/2 is straight below the centre
    end = math.pi / 2 - WEDGE + 2 * math.pi
    angles = [start + (end - start) * i / ARC_STEPS for i in range(ARC_STEPS + 1)]
    band = [(cx + math.cos(a) * outer, cy + math.sin(a) * outer) for a in angles]
    band += [(cx + math.cos(a) * inner, cy + math.sin(a) * inner) for a in reversed(angles)]
    return band


def oriented(contour):
    """Every contour clockwise on screen, so overlapping parts never cancel out."""
    area = 0.0
    for i in range(len(contour)):
        x1, y1 = contour[i]
        x2, y2 = contour[(i + 1) % len(contour)]
        area += x1 * y2 - x2 * y1
    return contour if area > 0 else contour[::-1]


def frame(dot_scale, rings):
    """rings: (grow, thickness) per ring; grow 0 sits just inside the ring's slot, 1 in place."""
    contours = [circle(CENTRE[0], CENTRE[1], DOT * dot_scale, 20)]
    for (inner, outer), (grow, thickness) in zip(RINGS, rings):
        start = inner - 0.02
        middle = start + ((inner + outer) / 2 - start) * grow
        half = (outer - inner) / 2 * thickness
        if half > 0.004:
            contours.append(ring(max(0.0, middle - half), middle + half))
    return [oriented(c) for c in contours]


def ease_out_back(t, overshoot=1.7):
    t -= 1
    return 1 + (overshoot + 1) * t * t * t + overshoot * t * t


def clamp(t):
    return 0.0 if t < 0 else 1.0 if t > 1 else t


def animation():
    frames = []
    count = 44
    for i in range(count):
        dot = 0.15 + 0.85 * ease_out_back(clamp(i / 8))
        rings = []
        for n in range(len(RINGS)):
            # each ring ripples out into place, staggered
            grow = clamp((i - 4 - 5 * n) / 11)
            thickness = min(1.0, grow * 1.6)
            # then a pulse of signal runs outward through the rings
            pulse = (i - 26 - 3 * n) / 6
            if 0 < pulse < 1:
                thickness *= 1 + 0.35 * math.sin(pulse * math.pi)
            rings.append((ease_out_back(grow, 1.2), thickness))
        frames.append(frame(dot, rings))
    frames[-1] = static_icon()
    return frames


def static_icon():
    return frame(1.0, [(1.0, 1.0)] * len(RINGS))


def write_animation(path, frames):
    blobs = []
    for contours in frames:
        blob = bytearray(struct.pack("<B", len(contours)))
        for contour in contours:
            blob += struct.pack("<H", len(contour))
            for x, y in contour:
                blob += struct.pack("<ff", x, y)
        blobs.append(bytes(blob))
    header = ANIM_MAGIC + struct.pack("<HBH", ANIM_VERSION, FPS, len(blobs))
    offset = len(header) + 4 * len(blobs)
    offsets = []
    for blob in blobs:
        offsets.append(offset)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<%dI" % len(offsets), *offsets))
        for blob in blobs:
            f.write(blob)


def icon_source(contours):
    lines = ['    "%s": (' % SLUG]
    for contour in contours:
        values = ", ".join("%g" % round(v, 3) for point in contour for v in point)
        lines.append("        (%s)," % values)
    lines.append("    ),")
    return "\n".join(lines) + "\n"


def remove_icon(source, slug):
    start = source.find('    "%s": (' % slug)
    if start < 0:
        return source
    end = source.index("\n    ),\n", start) + len("\n    ),\n")
    return source[:start] + source[end:]


def patch_icons(source, contours):
    """Replace only this app's entry; other apps' icons (stock or custom) are kept."""
    source = remove_icon(source, SLUG)
    end = source.rstrip().rfind("}")
    return source[:end] + icon_source(contours) + source[end:]


def rasterise(contours, size, scale):
    """NON_ZERO scanline fill of contours into a size x size greyscale bitmap."""
    pixels = bytearray(size * size)
    edges = []
    for contour in contours:
        pts = [(size / 2 + x * scale, size / 2 + y * scale) for x, y in contour]
        for i in range(len(pts)):
            (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
            if y1 != y2:
                edges.append((x1, y1, x2, y2, 1 if y2 > y1 else -1))
    for row in range(size):
        y = row + 0.5
        hits = []
        for x1, y1, x2, y2, d in edges:
            if min(y1, y2) <= y < max(y1, y2):
                hits.append((x1 + (y - y1) * (x2 - x1) / (y2 - y1), d))
        hits.sort()
        winding = 0
        for i in range(len(hits) - 1):
            winding += hits[i][1]
            if winding:
                for col in range(max(0, int(hits[i][0] + 0.5)), min(size, int(hits[i + 1][0] + 0.5))):
                    pixels[row * size + col] = 255
    return pixels


def write_preview(path, frames, size=96):
    cols = min(8, len(frames))
    rows = (len(frames) + cols - 1) // cols
    width, height = cols * (size + 4), rows * (size + 4)
    image = bytearray(width * height * 3)
    for n, contours in enumerate(frames):
        bitmap = rasterise(contours, size, size * 0.62)
        ox, oy = (n % cols) * (size + 4), (n // cols) * (size + 4)
        for row in range(size):
            for col in range(size):
                lit = bitmap[row * size + col]
                at = ((oy + row) * width + ox + col) * 3
                image[at:at + 3] = bytes((255, 255, 255)) if lit else bytes((30, 90, 170))
    raw = b"".join(b"\x00" + bytes(image[r * width * 3:(r + 1) * width * 3]) for r in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--icons-src", help="menu/src/icons.py to patch")
    parser.add_argument("--icons-out", help="where to write the patched icons.py")
    parser.add_argument("--anim-out", help="where to write anim/air_merge.bin")
    parser.add_argument("--preview", help="PNG contact sheet of the animation frames")
    args = parser.parse_args()

    frames = animation()
    if args.icons_src and args.icons_out:
        with open(args.icons_src) as f:
            source = f.read()
        if "ICONS = {" not in source:
            sys.exit("%s doesn't look like the menu's icons.py" % args.icons_src)
        with open(args.icons_out, "w") as f:
            f.write(patch_icons(source, static_icon()))
    if args.anim_out:
        write_animation(args.anim_out, frames)
    if args.preview:
        write_preview(args.preview, frames)
    print("frames %d, contours per frame %d..%d" % (
        len(frames), min(len(f) for f in frames), max(len(f) for f in frames)))


if __name__ == "__main__":
    sys.exit(main())
