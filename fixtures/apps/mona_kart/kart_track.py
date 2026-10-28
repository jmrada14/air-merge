# Mona Kart track: "Merge Bay Loop", a coastal tech-campus circuit at dusk.
#
# The track is built once at import from a compact list of sections. Each
# section eases its curve in, holds it, eases it out, and blends the road
# height from the previous section's end height to its own target height.
# Everything the game needs per segment (curve, height, scenery, pickups,
# boost pads, minimap path) is precomputed here so the per-frame code only
# indexes arrays.

import math
from array import array

if __name__ == "__main__":
    # desktop self-test run with `python3 -I`: make kart_config importable
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kart_config import SEGMENT_LENGTH

# (enter, hold, leave, curve, hill, tag)
#   enter/hold/leave: segment counts for ease-in, full curve, ease-out
#   curve: curvature at full strength (+ = right)
#   hill: road height (world units) reached at the END of the section
#   tag: scenery theme for the section
SECTIONS = (
    (0, 50, 0, 0.0, 0, "campus"),        # start straight past the server racks
    (18, 30, 18, 3.0, 0, "coast"),       # long sweeping right onto the bay road
    (0, 6, 0, 0.0, 0, "coast"),          # short breather
    (12, 8, 12, -3.5, 0, "park"),        # S-section: left ...
    (12, 8, 12, 3.5, 0, "park"),         # ... then right
    (0, 24, 0, 0.0, 1400, "hill"),       # climb to the crest
    (0, 22, 0, 0.0, -650, "hill"),       # over the top and down into the dip
    (16, 24, 16, 3.3, 0, "hill"),        # rising right out of the dip
    (0, 4, 0, 0.0, 0, "park"),
    (16, 22, 16, 3.3, 0, "park"),        # right, setting up the sweeper
    (22, 24, 22, -3.0, 0, "coast"),      # long fast left sweeper
    (18, 24, 18, 3.3, 0, "coast"),       # right back toward campus
    (0, 49, 0, 0.0, 0, "campus"),        # boost-pad straight
    (18, 18, 18, 4.0, 0, "campus"),      # final banked right
    (0, 30, 0, 0.0, 0, "campus"),        # run to the line
)

# Degrees of heading change per segment per unit of curve, used only to judge
# how close the raw section list is to one full turn (the minimap itself is
# normalised to exactly one turn).
DEG_PER_CURVE = 0.66

_EMPTY = ()


def _ease_in_out(t):
    return 0.5 - 0.5 * math.cos(t * math.pi)


class Pickup:
    def __init__(self, index, x, kind):
        self.index = index
        self.x = x
        self.kind = kind
        self.respawn_at = 0


class _Rng:
    # Tiny deterministic LCG so scenery is identical on badge and desktop.
    def __init__(self, seed):
        self.s = seed & 0x7FFFFFFF

    def next(self):
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s / 2147483648.0

    def uniform(self, a, b):
        return a + (b - a) * self.next()


class Track:
    def __init__(self):
        self.name = "Merge Bay Loop"
        self.start_z = 0
        self._build_geometry()
        self.length = self.count * SEGMENT_LENGTH
        self._build_pads()
        self._build_sprites()
        self._build_pickups()
        self._build_map()
        self._tags = None  # build-time only, free the RAM

    # --- geometry ---------------------------------------------------------
    def _build_geometry(self):
        count = 0
        for sec in SECTIONS:
            count += sec[0] + sec[1] + sec[2]
        self.count = count
        curve = array("f", [0.0] * count)
        height = array("f", [0.0] * (count + 1))
        tags = []
        sec_start = []
        i = 0
        h0 = 0.0
        for enter, hold, leave, c, hill, tag in SECTIONS:
            n = enter + hold + leave
            sec_start.append(i)
            for k in range(n):
                if k < enter:
                    f = _ease_in_out((k + 0.5) / enter)
                elif k < enter + hold:
                    f = 1.0
                else:
                    f = _ease_in_out((n - k - 0.5) / leave)
                curve[i + k] = c * f
                height[i + k] = h0 + (hill - h0) * _ease_in_out(k / n)
                tags.append(tag)
            i += n
            h0 = float(hill)
        height[count] = height[0]
        self.curve = curve
        self.height = height
        self._tags = tags
        self._sec_start = sec_start

    # --- boost pads ---------------------------------------------------------
    def _build_pads(self):
        pads = bytearray(self.count)
        s = self._sec_start
        # start straight, crest approach, boost-pad straight
        for first in (s[0] + 34, s[5] + 8, s[12] + 14):
            for k in range(3):
                pads[first + k] = 1
        self.pads = pads

    # --- scenery ------------------------------------------------------------
    def _build_sprites(self):
        count = self.count
        curve = self.curve
        tags = self._tags
        rng = _Rng(2026)
        sprites = [_EMPTY] * count
        for i in range(count):
            if i == 0:
                sprites[0] = ((0.0, "banner"),)
                continue
            c = curve[i]
            items = None
            if c >= 1.5 and i % 5 == 0:
                # right curve: chevron on the left (outside) edge, pointing right
                items = ((-1.4, "sign_r"),)
            elif c <= -1.5 and i % 5 == 0:
                items = ((1.4, "sign_l"),)
            elif abs(c) < 0.2:
                tag = tags[i]
                if i % 10 == 0:
                    side = 1.35 if (i // 10) % 2 else -1.35
                    items = ((side, "lamp"),)
                elif tag == "campus" and i % 4 == 2:
                    items = ((-1.7, "rack"), (1.7, "rack"))
            if items is None and i % 3 == 1:
                tag = tags[i]
                side = -1.0 if rng.next() < 0.5 else 1.0
                off = side * rng.uniform(1.5, 3.0)
                r = rng.next()
                if tag == "campus":
                    kind = "bush"
                elif tag == "hill":
                    kind = "pine" if r < 0.8 else "bush"
                elif tag == "park":
                    kind = "pine" if r < 0.5 else "bush"
                else:  # coast
                    kind = "pine" if r < 0.35 else "bush"
                # keep the outside of curves clear for the chevrons
                if (c > 0.2 and off < 0) or (c < -0.2 and off > 0):
                    off = -off
                items = ((off, kind),)
            if items is not None:
                sprites[i] = items
        self.sprites = sprites

    # --- pickups --------------------------------------------------------------
    def _build_pickups(self):
        s = self._sec_start
        pickups = []
        token_rows = (
            s[0] + 44,      # end of the start straight
            s[2] + 5,       # breather after the first sweeper
            s[5] + 22,      # climbing to the crest
            s[8] + 4,       # between the two hill-side rights
            s[10] + 6,      # entry to the left sweeper
            s[12] + 26,     # after the boost pad
        )
        for idx in token_rows:
            for x in (-0.5, 0.0, 0.5):
                pickups.append(Pickup(idx, x, "token"))
        # bugs: on the racing line, gentle places with room to dodge
        bugs = (
            (s[1] + 8, 0.3),       # turn-in of the first sweeper (gentle)
            (s[6] + 18, -0.2),     # in the dip after the crest
            (s[10] + 60, 0.2),     # exit of the left sweeper
            (s[11] + 66, 0.1),     # exit toward campus
            (s[14] + 6, -0.3),     # run to the line
        )
        for idx, x in bugs:
            pickups.append(Pickup(idx, x, "bug"))
        self.pickups = pickups

    # --- minimap ----------------------------------------------------------------
    def _build_map(self):
        count = self.count
        curve = self.curve
        total = 0.0
        for i in range(count):
            total += curve[i]
        self.raw_turns = total * DEG_PER_CURVE / 360.0
        k = 2.0 * math.pi / total  # radians per curve unit per segment
        xs = [0.0] * (count + 1)
        ys = [0.0] * (count + 1)
        x = 0.0
        y = 0.0
        hd = 0.0
        for i in range(count):
            # half the segment's turn before stepping, half after
            hd += curve[i] * k * 0.5
            x += math.sin(hd)
            y -= math.cos(hd)
            hd += curve[i] * k * 0.5
            xs[i + 1] = x
            ys[i + 1] = y
        ex = xs[count]
        ey = ys[count]
        self.closure_error = math.sqrt(ex * ex + ey * ey)
        for i in range(count):
            t = i / count
            xs[i] -= ex * t
            ys[i] -= ey * t
        minx = min(xs[:count])
        maxx = max(xs[:count])
        miny = min(ys[:count])
        maxy = max(ys[:count])
        w = maxx - minx
        h = maxy - miny
        span = w if w > h else h
        ox = (span - w) * 0.5
        oy = (span - h) * 0.5
        pts = []
        for i in range(count):
            pts.append(((xs[i] - minx + ox) / span, (ys[i] - miny + oy) / span))
        self.map_points = pts

    # --- queries (hot path, no allocation) ----------------------------------------
    def segment_index(self, z):
        i = int((z % self.length) / SEGMENT_LENGTH)
        if i >= self.count:
            i = self.count - 1
        return i

    def height_at(self, z):
        p = (z % self.length) / SEGMENT_LENGTH
        i = int(p)
        if i >= self.count:
            i = self.count - 1
        h = self.height
        a = h[i]
        return a + (h[i + 1] - a) * (p - i)

    def curve_at(self, z):
        return self.curve[self.segment_index(z)]


TRACK = Track()


if __name__ == "__main__":
    from kart_config import MAX_SPEED

    t = TRACK
    n_sprites = 0
    kinds = {}
    for items in t.sprites:
        for off, kind in items:
            n_sprites += 1
            kinds[kind] = kinds.get(kind, 0) + 1
    tokens = len([p for p in t.pickups if p.kind == "token"])
    bugs = len([p for p in t.pickups if p.kind == "bug"])
    print("track      ", t.name)
    print("segments   ", t.count)
    print("length     ", t.length)
    print("lap @max s  %.1f" % (t.length / MAX_SPEED))
    print("sprites     %d (%.2f / segment) %s" % (n_sprites, n_sprites / t.count, kinds))
    print("pickups     tokens %d, bugs %d" % (tokens, bugs))
    print("pads        %d segments" % sum(t.pads))
    print("max |curve| %.2f" % max(abs(c) for c in t.curve))
    print("height      %.0f .. %.0f (first %.1f last %.1f)" % (
        min(t.height), max(t.height), t.height[0], t.height[t.count]))
    print("raw turns   %.3f (sum curve * %.2f deg)" % (t.raw_turns, DEG_PER_CURVE))
    print("closure err %.2f segment lengths (%.2f%% of lap)" % (
        t.closure_error, 100.0 * t.closure_error / t.count))

    # sanity checks
    assert t.height[0] == t.height[t.count]
    assert sum(1 for it in t.sprites for o, k in it if k == "banner") == 1
    for it in t.sprites:
        for o, k in it:
            assert k == "banner" or 1.3 <= abs(o) <= 3.0, (o, k)
    for p in t.pickups:
        assert -0.8 <= p.x <= 0.8
        if p.kind == "bug":
            assert p.index >= 30 and abs(t.curve[p.index]) < 1.5, p.index
            assert not t.pads[p.index]
    for i in range(t.count):
        if t.pads[i]:
            assert t.curve[i] == 0.0, i
    assert t.segment_index(-1) == t.count - 1
    assert abs(t.height_at(t.length) - t.height[0]) < 1e-3

    # self-intersection check on the minimap polyline
    pts = t.map_points
    n = len(pts)

    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    hits = 0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if (j + 1) % n == i:
                continue
            c, d = pts[j], pts[(j + 1) % n]
            if (_cross(a, b, c) * _cross(a, b, d) < 0) and (_cross(c, d, a) * _cross(c, d, b) < 0):
                hits += 1
    print("self-intersections", hits)

    W, H = 60, 30
    grid = [[" "] * W for _ in range(H)]
    for i, (u, v) in enumerate(pts):
        gx = int(u * (W - 1) + 0.5)
        gy = int(v * (H - 1) + 0.5)
        ch = "."
        if t.pads[i]:
            ch = "="
        grid[gy][gx] = ch
    for p in t.pickups:
        u, v = pts[p.index]
        grid[int(v * (H - 1) + 0.5)][int(u * (W - 1) + 0.5)] = "T" if p.kind == "token" else "B"
    u, v = pts[0]
    grid[int(v * (H - 1) + 0.5)][int(u * (W - 1) + 0.5)] = "S"
    print("+" + "-" * W + "+")
    for row in grid:
        print("|" + "".join(row) + "|")
    print("+" + "-" * W + "+")
    print("S start  = pad  T tokens  B bug   (start heads up/north, loop runs clockwise)")
