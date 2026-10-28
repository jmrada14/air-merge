"""Fake MonaOS 4.0 firmware for running badge apps under desktop CPython.

`install(app_dir)` puts the firmware globals (screen, badge, color, font, ...)
into `builtins`, registers fake `badgeware` and `micropython` modules and adds
the app directory to sys.path. Pure standard library; no Pillow.

The rasteriser implements exactly the drawing subset in DESIGN.md:
clear, rectangle, triangle, circle, line, text, measure_text. Pixel centres are
sampled at (x + 0.5, y + 0.5), so shapes sharing an edge tile without gaps or
double-blending.
"""

import builtins
import gc
import json
import math
import os
import sys
import time
import types

WIDTH = 320
HEIGHT = 240

_ceil = math.ceil
_isfinite = math.isfinite


class AppExit(BaseException):
    """Raised by reset(). BaseException so an app's `except Exception` can't eat it."""


# --------------------------------------------------------------------------
# colour, vectors, misc value types
# --------------------------------------------------------------------------

def _c8(v):
    v = int(v)
    return 0 if v < 0 else (255 if v > 255 else v)


class Color(tuple):
    """(r, g, b, a) with .r .g .b .a accessors."""

    def __new__(cls, r, g, b, a=255):
        return tuple.__new__(cls, (_c8(r), _c8(g), _c8(b), _c8(a)))

    r = property(lambda s: s[0])
    g = property(lambda s: s[1])
    b = property(lambda s: s[2])
    a = property(lambda s: s[3])

    def __repr__(self):
        return "color.rgb(%d, %d, %d, %d)" % tuple(self)


class _ColorNS:
    white = Color(255, 255, 255)
    black = Color(0, 0, 0)

    @staticmethod
    def rgb(r, g, b, a=255):
        return Color(r, g, b, a)

    rgba = rgb

    @staticmethod
    def hsv(h, s, v, a=255):
        import colorsys
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        return Color(r * 255, g * 255, b * 255, a)


class vec2:
    __slots__ = ("x", "y")  # fine here: this file never runs on the badge

    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y

    def __iter__(self):
        yield self.x
        yield self.y

    def __getitem__(self, i):
        return (self.x, self.y)[i]

    def __len__(self):
        return 2

    def __add__(self, o):
        return vec2(self.x + o[0], self.y + o[1])

    def __sub__(self, o):
        return vec2(self.x - o[0], self.y - o[1])

    def __mul__(self, k):
        return vec2(self.x * k, self.y * k)

    __rmul__ = __mul__

    def __truediv__(self, k):
        return vec2(self.x / k, self.y / k)

    def __neg__(self):
        return vec2(-self.x, -self.y)

    def __eq__(self, o):
        try:
            return self.x == o[0] and self.y == o[1]
        except (TypeError, IndexError):
            return NotImplemented

    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y)

    def __repr__(self):
        return "vec2(%g, %g)" % (self.x, self.y)


class rect:
    def __init__(self, x=0, y=0, w=0, h=0):
        self.x, self.y, self.w, self.h = x, y, w, h

    def __iter__(self):
        return iter((self.x, self.y, self.w, self.h))

    def __repr__(self):
        return "rect(%g, %g, %g, %g)" % (self.x, self.y, self.w, self.h)


class _Stub:
    """Accepts any attribute access / call. Used for APIs outside the contract
    subset (image, shape, mat3) so imports don't crash; they draw nothing."""

    def __init__(self, name="stub", *a, **k):
        object.__setattr__(self, "_name", name)

    def __getattr__(self, name):
        return _Stub(self._name + "." + name)

    def __setattr__(self, name, value):
        pass

    def __call__(self, *a, **k):
        return _Stub(self._name + "()")

    def __repr__(self):
        return "<sim stub %s>" % self._name


class mat3(_Stub):
    def __init__(self, *a, **k):
        _Stub.__init__(self, "mat3")


# --------------------------------------------------------------------------
# 5x7 bitmap font, ASCII 32..126. Each glyph is 5 column bytes (bit 0 = top row),
# written as 10 hex digits per glyph. Descenders (g j p q y) use an 8th row (bit 7),
# which still fits the 8 * scale line height.
# --------------------------------------------------------------------------

_FONT_HEX = (
    "0000000000" "00005f0000" "0007000700" "147f147f14" "242a7f2a12"  # space ! " # $
    "2313086462" "3649552250" "0005030000" "001c224100" "0041221c00"  # % & ' ( )
    "082a1c2a08" "08083e0808" "0050300000" "0808080808" "0060600000"  # * + , - .
    "2010080402" "3e5149453e" "00427f4000" "4261514946" "2141454b31"  # / 0 1 2 3
    "1814127f10" "2745454539" "3c4a494930" "0171090503" "3649494936"  # 4 5 6 7 8
    "064949291e" "0036360000" "0056360000" "0814224100" "1414141414"  # 9 : ; < =
    "0041221408" "0201510906" "324979413e" "7e1111117e" "7f49494936"  # > ? @ A B
    "3e41414122" "7f4141221c" "7f49494941" "7f09090101" "3e41415132"  # C D E F G
    "7f0808087f" "00417f4100" "2040413f01" "7f08142241" "7f40404040"  # H I J K L
    "7f0204027f" "7f0408107f" "3e4141413e" "7f09090906" "3e4151215e"  # M N O P Q
    "7f09192946" "4649494931" "01017f0101" "3f4040403f" "1f2040201f"  # R S T U V
    "7f2018207f" "6314081463" "0304780403" "6151494543" "007f414100"  # W X Y Z [
    "0204081020" "0041417f00" "0402010204" "4040404040" "0001020400"  # \ ] ^ _ `
    "2054545478" "7f48444438" "3844444420" "384444487f" "3854545418"  # a b c d e
    "087e090102" "18a4a4a47c" "7f08040478" "00447d4000" "4080847d00"  # f g h i j
    "007f102844" "00417f4000" "7c04180478" "7c08040478" "3844444438"  # k l m n o
    "fc24242418" "18242418fc" "7c08040408" "4854545420" "043f444020"  # p q r s t
    "3c4040207c" "1c2040201c" "3c4030403c" "4428102844" "1ca0a0a07c"  # u v w x y
    "4464544c44" "0008364100" "00007f0000" "0041360800" "0201020402"  # z { | } ~
)


def _build_glyph_runs():
    """glyph -> tuple of (row, col_start, col_end) horizontal runs in the 5x7 cell."""
    glyphs = {}
    for i in range(95):
        cols = bytes.fromhex(_FONT_HEX[i * 10:i * 10 + 10])
        runs = []
        for row in range(8):
            col = 0
            while col < 5:
                if cols[col] >> row & 1:
                    start = col
                    while col < 5 and cols[col] >> row & 1:
                        col += 1
                    runs.append((row, start, col))
                else:
                    col += 1
        glyphs[chr(32 + i)] = tuple(runs)
    return glyphs


GLYPHS = _build_glyph_runs()

FONT_SCALES = {"ignore": 2, "more": 3, "bacteria": 1, "smart": 1}


class Font:
    def __init__(self, name):
        self.name = name
        self.scale = FONT_SCALES.get(name, 1)
        self.advance = 6 * self.scale
        self.height = 8 * self.scale

    def __repr__(self):
        return "<font %s x%d>" % (self.name, self.scale)


class _FontNS:
    def __init__(self):
        self._cache = {}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        f = self._cache.get(name)
        if f is None:
            f = self._cache[name] = Font(name)
        return f


def text_size(text, scale):
    lines = str(text).split("\n")
    w = max(len(line) for line in lines) * 6 * scale
    if w:
        w -= scale  # no trailing inter-glyph gap
    return w, len(lines) * 8 * scale


# --------------------------------------------------------------------------
# Screen / rasteriser
# --------------------------------------------------------------------------

def _xy(p):
    try:
        return float(p.x), float(p.y)
    except AttributeError:
        return float(p[0]), float(p[1])


class Screen:
    def __init__(self, width=WIDTH, height=HEIGHT):
        self.width = width
        self.height = height
        self.fb = bytearray(width * height * 3)
        self.antialias = 0
        self.clip = None          # None or rect(x, y, w, h); see the clip setter
        self._font = Font("default")
        self._tables = {}
        self.counts = {}
        self.reset_counts()
        self.pen = Color(255, 255, 255)

    # -- state ----------------------------------------------------------
    def reset_counts(self):
        c = self.counts
        c["rects"] = c["tris"] = c["circles"] = c["lines"] = c["texts"] = c["pixels"] = 0

    @property
    def pen(self):
        return self._pen

    @pen.setter
    def pen(self, c):
        if not isinstance(c, Color):
            try:
                c = Color(*c)
            except TypeError:
                raise TypeError("screen.pen must be color.rgb(...), got %r" % (c,))
        self._pen = c
        self._pen3 = bytes(c[:3])
        a = c[3]
        self._alpha = a
        if 0 < a < 255:
            self._ctab = (self._table(c[0], a), self._table(c[1], a), self._table(c[2], a))

    def _table(self, c, a):
        key = (c << 8) | a
        t = self._tables.get(key)
        if t is None:
            if len(self._tables) > 8192:
                self._tables.clear()
            ia = 255 - a
            ca = c * a + 127
            t = self._tables[key] = bytes((v * ia + ca) // 255 for v in range(256))
        return t

    @property
    def font(self):
        return self._font

    @font.setter
    def font(self, f):
        if isinstance(f, str):
            f = Font(f)
        if not isinstance(f, Font):
            raise TypeError("screen.font must be font.<name>, got %r" % (f,))
        self._font = f

    @property
    def clip(self):
        return self._clip

    @clip.setter
    def clip(self, r):
        # `screen.clip = rect(x, y, w, h)` confines every primitive to that
        # rectangle (pixel edges rounded like rectangle()); None or a rect
        # covering the screen turns clipping off.
        W, H = self.width, self.height
        x0, y0, x1, y1 = 0, 0, W, H
        if r is not None:
            try:
                x, y, w, h = float(r.x), float(r.y), float(r.w), float(r.h)
            except AttributeError:
                x, y, w, h = (float(v) for v in r)
            x0 = max(0, min(W, _ceil(x - 0.5)))
            x1 = max(0, min(W, _ceil(x + w - 0.5)))
            y0 = max(0, min(H, _ceil(y - 0.5)))
            y1 = max(0, min(H, _ceil(y + h - 0.5)))
        self._clip = r
        self._cx0, self._cy0, self._cx1, self._cy1 = x0, y0, x1, y1
        self._clipping = (x0, y0, x1, y1) != (0, 0, W, H)

    # -- span primitive (caller clamps: 0 <= xs < xe <= width, 0 <= y < height;
    #    the clip rectangle is applied here so every primitive honours it)
    def _span(self, y, xs, xe):
        if self._clipping:
            if y < self._cy0 or y >= self._cy1:
                return
            if xs < self._cx0:
                xs = self._cx0
            if xe > self._cx1:
                xe = self._cx1
            if xe <= xs:
                return
        n = xe - xs
        o = (y * self.width + xs) * 3
        e = o + 3 * n
        fb = self.fb
        self.counts["pixels"] += n
        if self._alpha == 255:
            fb[o:e] = self._pen3 * n
        else:
            t = self._ctab
            fb[o:e:3] = fb[o:e:3].translate(t[0])
            fb[o + 1:e:3] = fb[o + 1:e:3].translate(t[1])
            fb[o + 2:e:3] = fb[o + 2:e:3].translate(t[2])

    def _fill_rect_px(self, x0, y0, x1, y1):
        W = self.width
        if x0 < self._cx0:
            x0 = self._cx0
        if y0 < self._cy0:
            y0 = self._cy0
        if x1 > self._cx1:
            x1 = self._cx1
        if y1 > self._cy1:
            y1 = self._cy1
        if x1 <= x0 or y1 <= y0 or self._alpha == 0:
            return
        if self._alpha == 255 and x0 == 0 and x1 == W:
            n = (y1 - y0) * W
            self.fb[y0 * W * 3:y1 * W * 3] = self._pen3 * n
            self.counts["pixels"] += n
            return
        span = self._span
        for y in range(y0, y1):
            span(y, x0, x1)

    # -- public API -----------------------------------------------------
    def clear(self):
        self._fill_rect_px(0, 0, self.width, self.height)

    def rectangle(self, x, y, w=None, h=None):
        self.counts["rects"] += 1
        if w is None:  # rectangle(rect(...))
            x, y, w, h = x
        x, y, w, h = float(x), float(y), float(w), float(h)
        if not (_isfinite(x) and _isfinite(y) and _isfinite(w) and _isfinite(h)):
            return
        if w < 0:
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        x0 = max(-1, min(self.width + 1, _ceil(x - 0.5)))
        x1 = max(-1, min(self.width + 1, _ceil(x + w - 0.5)))
        y0 = max(-1, min(self.height + 1, _ceil(y - 0.5)))
        y1 = max(-1, min(self.height + 1, _ceil(y + h - 0.5)))
        self._fill_rect_px(x0, y0, x1, y1)

    def triangle(self, a, b, c):
        self.counts["tris"] += 1
        if self._alpha == 0:
            return
        ax, ay = _xy(a)
        bx, by = _xy(b)
        cx, cy = _xy(c)
        if not (_isfinite(ax) and _isfinite(ay) and _isfinite(bx) and _isfinite(by)
                and _isfinite(cx) and _isfinite(cy)):
            return
        # sort by y
        if ay > by:
            ax, ay, bx, by = bx, by, ax, ay
        if by > cy:
            bx, by, cx, cy = cx, cy, bx, by
            if ay > by:
                ax, ay, bx, by = bx, by, ax, ay
        W, H = self.width, self.height
        ys = _ceil(ay - 0.5) if ay > -1 else 0
        ye = _ceil(cy - 0.5) if cy < H + 1 else H
        if ys < 0:
            ys = 0
        if ye > H:
            ye = H
        if ye <= ys or cy == ay:
            return
        long_k = (cx - ax) / (cy - ay)
        top_k = (bx - ax) / (by - ay) if by != ay else 0.0
        bot_k = (cx - bx) / (cy - by) if cy != by else 0.0
        span = self._span
        for y in range(ys, ye):
            yc = y + 0.5
            xl = ax + (yc - ay) * long_k
            if yc < by:
                xr = ax + (yc - ay) * top_k
            else:
                xr = bx + (yc - by) * bot_k
            if xr < xl:
                xl, xr = xr, xl
            xs = _ceil(xl - 0.5) if xl > -1 else 0
            xe = _ceil(xr - 0.5) if xr < W + 1 else W
            if xs < 0:
                xs = 0
            if xe > W:
                xe = W
            if xe > xs:
                span(y, xs, xe)

    def circle(self, center, r):
        self.counts["circles"] += 1
        cx, cy = _xy(center)
        r = float(r)
        if self._alpha == 0 or r <= 0 or not (_isfinite(cx) and _isfinite(cy) and _isfinite(r)):
            return
        W, H = self.width, self.height
        ys = max(0, _ceil(max(-2.0, cy - r) - 0.5))
        ye = min(H, _ceil(min(H + 2.0, cy + r) - 0.5))
        r2 = r * r
        span = self._span
        sqrt = math.sqrt
        for y in range(ys, ye):
            dy = y + 0.5 - cy
            d2 = r2 - dy * dy
            if d2 <= 0:
                continue
            dx = sqrt(d2)
            xl = cx - dx
            xr = cx + dx
            xs = _ceil(xl - 0.5) if xl > -1 else 0
            xe = _ceil(xr - 0.5) if xr < W + 1 else W
            if xs < 0:
                xs = 0
            if xe > W:
                xe = W
            if xe > xs:
                span(y, xs, xe)

    def line(self, a, b, *extra):
        self.counts["lines"] += 1
        if self._alpha == 0:
            return
        x0, y0 = _xy(a)
        x1, y1 = _xy(b)
        if not (_isfinite(x0) and _isfinite(y0) and _isfinite(x1) and _isfinite(y1)):
            return
        # Liang-Barsky clip to the screen so huge coordinates stay cheap.
        W, H = self.width, self.height
        dx, dy = x1 - x0, y1 - y0
        t0, t1 = 0.0, 1.0
        for p, q in ((-dx, x0), (dx, W - 0.001 - x0), (-dy, y0), (dy, H - 0.001 - y0)):
            if p == 0:
                if q < 0:
                    return
            else:
                t = q / p
                if p < 0:
                    if t > t1:
                        return
                    if t > t0:
                        t0 = t
                else:
                    if t < t0:
                        return
                    if t < t1:
                        t1 = t
        ix0 = int(math.floor(x0 + t0 * dx))
        iy0 = int(math.floor(y0 + t0 * dy))
        ix1 = int(math.floor(x0 + t1 * dx))
        iy1 = int(math.floor(y0 + t1 * dy))
        # Bresenham
        sx = 1 if ix1 >= ix0 else -1
        sy = 1 if iy1 >= iy0 else -1
        ddx = abs(ix1 - ix0)
        ddy = -abs(iy1 - iy0)
        err = ddx + ddy
        x, y = ix0, iy0
        span = self._span
        while True:
            if 0 <= x < W and 0 <= y < H:
                span(y, x, x + 1)
            if x == ix1 and y == iy1:
                break
            e2 = 2 * err
            if e2 >= ddy:
                err += ddy
                x += sx
            if e2 <= ddx:
                err += ddx
                y += sy

    def text(self, text, x, y, *extra):
        self.counts["texts"] += 1
        if self._alpha == 0:
            return
        f = self._font
        s = f.scale
        ox = _ceil(float(x) - 0.5)
        py = _ceil(float(y) - 0.5)
        W, H = self.width, self.height
        span = self._span
        unknown = GLYPHS["?"]
        for line in str(text).split("\n"):
            px = ox
            if -8 * s < py < H:
                for ch in line:
                    if px >= W:
                        break
                    if px > -6 * s:
                        for row, c0, c1 in GLYPHS.get(ch, unknown):
                            xs = px + c0 * s
                            xe = px + c1 * s
                            if xs < 0:
                                xs = 0
                            if xe > W:
                                xe = W
                            if xe <= xs:
                                continue
                            yy = py + row * s
                            for sy in range(s):
                                if 0 <= yy + sy < H:
                                    span(yy + sy, xs, xe)
                    px += 6 * s
            py += 8 * s

    def measure_text(self, text):
        return text_size(text, self._font.scale)

    # -- sim helpers ----------------------------------------------------
    def snapshot(self):
        return bytes(self.fb)

    def save_png(self, path, scale=1):
        import png
        png.write_png(path, self.width, self.height, self.fb, scale=scale)


# --------------------------------------------------------------------------
# badge (input, time, lights)
# --------------------------------------------------------------------------

BUTTONS = {
    "UP": 1, "DOWN": 2, "LEFT": 4, "RIGHT": 8,
    "SELECT": 16, "BACK": 32, "HOME": 64, "MENU": 128,
}


class _Buttons:
    def __init__(self, badge):
        self._b = badge

    def pressed(self, btn):
        return self._b.pressed(btn)

    def held(self, btn):
        return self._b.held(btn)

    def released(self, btn):
        return self._b.released(btn)


class Badge:
    def __init__(self):
        self.ticks = 0
        self.ticks_delta = 0
        self.default_clear = None
        self.last_caselights = None
        self.caselight_calls = 0
        self.mode_value = None
        self._held = 0
        self._pressed = 0
        self._released = 0
        self.buttons = _Buttons(self)

    # input -------------------------------------------------------------
    def set_held(self, mask):
        """Sim: set the held mask for the coming frame; edges derive from the last mask."""
        prev = self._held
        self._pressed = mask & ~prev
        self._released = prev & ~mask
        self._held = mask

    def pressed(self, btn):
        return bool(self._pressed & btn)

    def held(self, btn):
        return bool(self._held & btn)

    def released(self, btn):
        return bool(self._released & btn)

    # misc firmware calls ---------------------------------------------------
    def mode(self, *a):
        if a:
            self.mode_value = a[0]
        return self.mode_value

    def caselights(self, *levels):
        if levels:
            self.last_caselights = levels if len(levels) > 1 else levels[0]
            self.caselight_calls += 1
        return self.last_caselights

    def imu(self):
        return (0, 0, 16384, 0, 0, 0)

    def poll(self, *a):
        pass

    def autorotate(self, *a):
        pass

    def update(self, *a):
        pass


# --------------------------------------------------------------------------
# State (badgeware.State) backed by JSON files
# --------------------------------------------------------------------------

class State:
    dir = None  # set by install()

    @classmethod
    def _path(cls, key):
        return os.path.join(cls.dir, "%s.json" % key)

    @classmethod
    def load(cls, key, data):
        p = cls._path(key)
        if not os.path.exists(p):
            return False
        try:
            with open(p) as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return False
        if isinstance(saved, dict):
            data.update(saved)
        return True

    @classmethod
    def save(cls, key, data):
        os.makedirs(cls.dir, exist_ok=True)
        with open(cls._path(key), "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)

    @classmethod
    def delete(cls, key):
        p = cls._path(key)
        if os.path.exists(p):
            os.remove(p)


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

class Sim:
    """Handle returned by install(); the runner drives frames through it."""

    def __init__(self, app_dir, slug="air_merge"):
        self.app_dir = os.path.abspath(app_dir)
        self.slug = slug
        self.screen = Screen()
        self.badge = Badge()
        self.update = None
        self.run_calls = 0

    def run(self, update, *a, **k):
        self.update = update
        self.run_calls += 1
        return types.SimpleNamespace(result=None)

    def map_path(self, path):
        """Badge path -> host path. The running app's own folder
        (/system/apps/<slug>/) maps to the repo's app/ folder; other absolute
        paths go into the sandbox (builtins.AM_ROOT) when one is set; paths that
        already carry the sandbox prefix are left alone."""
        p = str(path)
        root = getattr(builtins, "AM_ROOT", "")
        if root and (p == root or p.startswith(root + "/")):
            return p
        prefix = "/system/apps/%s/" % self.slug
        if p.startswith(prefix):
            return os.path.join(self.app_dir, p[len(prefix):])
        if not os.path.isabs(p):
            return os.path.join(self.app_dir, p)
        if root:
            return root + p
        return p

    def file_exists(self, path):
        return os.path.exists(self.map_path(path))


def _reset(*a):
    raise AppExit("reset() called")


def install(app_dir, script=None, state_dir=None, slug="air_merge"):
    """Install fake firmware globals. `script` is accepted for API symmetry
    (the runner parses input scripts itself). Returns a Sim handle."""
    sim = Sim(app_dir, slug)
    here = os.path.dirname(os.path.abspath(__file__))
    State.dir = state_dir or os.path.join(here, "out", "state")
    sim.script = script

    g = {
        "screen": sim.screen,
        "badge": sim.badge,
        "color": _ColorNS(),
        "font": _FontNS(),
        "image": _Stub("image"),
        "shape": _Stub("shape"),
        "vec2": vec2,
        "rect": rect,
        "mat3": mat3,
        "run": sim.run,
        "reset": _reset,
        "file_exists": sim.file_exists,
        "HIRES": 1,
        "LORES": 0,
        "VSYNC": 2,
    }
    for name, bit in BUTTONS.items():
        g["BUTTON_" + name] = bit
    for k, v in g.items():
        setattr(builtins, k, v)

    bw = types.ModuleType("badgeware")
    bw.State = State
    for k, v in g.items():
        setattr(bw, k, v)
    sys.modules["badgeware"] = bw

    mp = types.ModuleType("micropython")
    mp.const = lambda x: x
    mp.native = lambda f: f
    mp.viper = lambda f: f
    mp.mem_info = lambda *a: None
    mp.opt_level = lambda *a: 0
    sys.modules["micropython"] = mp

    # MicroPython-only helpers some code reaches for; harmless on CPython.
    if not hasattr(gc, "mem_free"):
        gc.mem_free = lambda: 200000
        gc.mem_alloc = lambda: 0
    badge = sim.badge
    if not hasattr(time, "ticks_ms"):
        time.ticks_ms = lambda: badge.ticks
        time.ticks_diff = lambda a, b: a - b
        time.ticks_add = lambda a, b: a + b
        time.sleep_ms = lambda ms: None

    ad = os.path.abspath(app_dir)
    if ad not in sys.path:
        sys.path.insert(0, ad)
    return sim
