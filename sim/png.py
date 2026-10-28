"""Minimal PNG writer/reader (8-bit RGB) and contact-sheet helper. Stdlib only."""

import os
import struct
import zlib


def _chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _scale_rgb(width, height, rgb, scale):
    if scale == 1:
        return width, height, rgb
    out = bytearray()
    for y in range(height):
        row = rgb[y * width * 3:(y + 1) * width * 3]
        big = bytearray(width * 3 * scale)
        for k in range(3):
            ch = row[k::3]
            for s in range(scale):
                big[k + 3 * s::3 * scale] = ch
        out += bytes(big) * scale
    return width * scale, height * scale, out


def encode_png(width, height, rgb, scale=1):
    width, height, rgb = _scale_rgb(width, height, rgb, int(scale))
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _chunk(b"IEND", b""))


def write_png(path, width, height, rgb, scale=1):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(encode_png(width, height, rgb, scale))


def read_png(path):
    """Read an 8-bit RGB or RGBA non-interlaced PNG -> (width, height, rgb bytes)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG: %s" % path)
    pos = 8
    idat = bytearray()
    width = height = bpp = 0
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise ValueError("unsupported PNG format in %s" % path)
            bpp = 3 if ctype == 2 else 4
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    stride = width * bpp
    prev = bytearray(stride)
    out = bytearray()
    i = 0
    for _ in range(height):
        ft = raw[i]
        cur = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if ft == 1:
            for x in range(bpp, stride):
                cur[x] = (cur[x] + cur[x - bpp]) & 255
        elif ft == 2:
            for x in range(stride):
                cur[x] = (cur[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                left = cur[x - bpp] if x >= bpp else 0
                cur[x] = (cur[x] + ((left + prev[x]) >> 1)) & 255
        elif ft == 4:
            for x in range(stride):
                a = cur[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[x] = (cur[x] + pr) & 255
        if bpp == 4:
            rgb = bytearray(width * 3)
            rgb[0::3] = cur[0::4]
            rgb[1::3] = cur[1::4]
            rgb[2::3] = cur[2::4]
            out += rgb
        else:
            out += cur
        prev = cur
    return width, height, bytes(out)


def _as_frame(item, default_size=(320, 240)):
    """Accepts a PNG path, raw RGB bytes (default 320x240), or (w, h, rgb)."""
    if isinstance(item, (str, os.PathLike)):
        return read_png(item)
    if isinstance(item, tuple) and len(item) == 3:
        return item
    w, h = default_size
    return w, h, bytes(item)


def contact_sheet(paths_or_framebuffers, cols, out_path, scale=1, labels=None,
                  gap=4, background=(16, 16, 24), label_color=(230, 230, 230)):
    """Tile frames into one PNG, `cols` per row with a `gap` px border.

    labels: optional list of strings drawn in a strip under each frame.
    """
    frames = [_as_frame(it) for it in paths_or_framebuffers]
    if not frames:
        raise ValueError("contact_sheet: no frames")
    cols = max(1, min(int(cols), len(frames)))
    rows = (len(frames) + cols - 1) // cols
    fw = max(f[0] for f in frames)
    fh = max(f[1] for f in frames)
    strip = 12 if labels else 0
    cw, ch = fw + gap, fh + strip + gap
    W = gap + cols * cw
    H = gap + rows * ch
    sheet = bytearray(bytes(background) * (W * H))
    for idx, (w, h, rgb) in enumerate(frames):
        ox = gap + (idx % cols) * cw
        oy = gap + (idx // cols) * ch
        for y in range(h):
            o = ((oy + y) * W + ox) * 3
            sheet[o:o + w * 3] = rgb[y * w * 3:(y + 1) * w * 3]
        if labels and idx < len(labels) and labels[idx]:
            _draw_label(sheet, W, H, str(labels[idx]), ox + 1, oy + h + 2, label_color)
    write_png(out_path, W, H, sheet, scale=scale)
    return out_path


def _draw_label(buf, W, H, text, x, y, rgb):
    from badge_sim import GLYPHS
    px = bytes(rgb)
    unknown = GLYPHS["?"]
    for ch in text:
        for row, c0, c1 in GLYPHS.get(ch, unknown):
            yy = y + row
            if 0 <= yy < H:
                for xx in range(x + c0, x + c1):
                    if 0 <= xx < W:
                        o = (yy * W + xx) * 3
                        buf[o:o + 3] = px
        x += 6
