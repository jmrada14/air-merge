# Mona Kart world renderer: classic segment-projection pseudo-3D.
#
#   draw_world(track, race_or_none, camera_z, camera_x, now, player,
#              view=None, draw_distance=None)
#
# `view = (top, height)` renders into a horizontal slice of the screen (split
# screen uses (0, 120) and (120, 120)); None means the whole screen (0, 240).
# Everything vertical and every projection factor is scaled by
# k = height / 240: the horizon sits at top + 104k, the road under the
# subject kart is 160k px half-wide, sprites are scaled by k and the subject
# kart is drawn at scale k with its wheels at top + 232k. Drawing is confined
# with screen.clip and the clip is restored to the full screen afterwards.
# The background keeps its x layout (a wider-looking panorama in a half view)
# and is only squashed vertically; half views use fewer sky bands and fewer
# skyline windows. Parallax scroll state is kept per view, keyed by `top`.
#
# `player` is the racer the view follows; every other racer in race.racers is
# drawn as a sprite (works for 4-racer solo races and 2-racer versus races).
#
# Frame layout (all with rectangles / triangles / one circle):
#   1. sky gradient bands, sun, far hills, campus skyline, near hills
#      (parallax layers; only their scroll offsets change per frame)
#   2. road, near -> far, one pass that projects each segment edge and draws
#      the segment immediately, clipped to `maxy` (the top of everything
#      already drawn below it). Bands never overlap, so there is no overdraw
#      between segments and segments hidden behind a crest cost nothing.
#   3. a ground fill for the gap between the hills and the far end of the road
#   4. sprites far -> near: scenery, pickups and other karts, skipped when
#      their base is hidden behind a nearer crest
#   5. the subject kart at the bottom centre (plus speed lines when boosting)
#
# Projection. The contract's formula (screen = 160 + scale * dx * 160) puts the
# road at the player's depth only ~79 px half-wide, while the contract wants a
# sprite scale of ~1.0 (road half-width ~160 px) at the player's depth and the
# player kart's wheels at y = 232. So the projection factors below are derived
# from kart_config so that, for a flat road, the road under the player is
# exactly 160 px half-wide and sits at y = 232, with the vanishing point at
# HORIZON. Same math, calibrated constants.
#
# Curves are integrated relative to the road's tangent at the player's
# position (not at the camera), so the road under the player kart never
# drifts sideways in long bends and the scroll is perfectly continuous.
#
# `stats["calls"]` counts the primitives this module issues (cumulative);
# `stats["frame"]` is the count for the last draw_world call. Sprite art drawn
# by kart_art is not included. Counting is done with batched local adds, so it
# costs next to nothing.
#
# Nothing here touches firmware globals at import time: colours are built on
# the first draw_world call and kart_art is imported lazily.

import math

from kart_config import (
    WIDTH, HEIGHT, SEGMENT_LENGTH, ROAD_WIDTH, RUMBLE_LENGTH, CAMERA_HEIGHT,
    CAMERA_DEPTH, PLAYER_Z, DRAW_DISTANCE, SPLIT_DRAW_DISTANCE, MAX_SPEED,
    SKY_TOP, SKY_BOTTOM, HILLS, HILLS_FAR, GRASS_LIGHT, GRASS_DARK,
    RUMBLE_LIGHT, RUMBLE_DARK, ROAD_LIGHT, ROAD_DARK, LANE, PAD, rgb,
)

stats = {"calls": 0, "frame": 0}

# --- projection constants -----------------------------------------------------
HORIZON = 104                  # vanishing point y (flat road converges here)
PLAYER_Y = 232                 # player kart bottom y
HALF_W = WIDTH * 0.5
_PROJ_X = HALF_W * PLAYER_Z / (CAMERA_DEPTH * ROAD_WIDTH)
_PROJ_Y = (PLAYER_Y - HORIZON) * PLAYER_Z / (CAMERA_DEPTH * CAMERA_HEIGHT)
KXD = CAMERA_DEPTH * _PROJ_X   # screen x px = 160 + dx_world * KXD / dz
KYD = CAMERA_DEPTH * _PROJ_Y   # screen y px = HORIZON + dy_world * KYD / dz
RUMBLE_W = 1.14                # rumble outer edge, in road half-widths
LANE_W = 0.035                 # lane stripe half-width, in road half-widths
INV_SPRITE = 1.0 / 160.0       # sprite scale = road half-width px / 160
MIN_SCALE = 0.04
MIN_SCALE_SPLIT = 0.15         # small views: scenery/pickups pop in at the size
                               # they do at the far end of a full view
# Road nearer than this is not drawn. On a flat road everything nearer than
# ~0.9 * PLAYER_Z is below the screen anyway; this only matters when the camera
# trails the player down a steep slope and would otherwise look "into" the
# hill it sits on, hiding the road under the player kart.
NEAR_Z = PLAYER_Z * 0.6
XLIM = 2000.0                  # clamp for off-screen trapezoid vertices


def _cl(v):
    if v < -XLIM:
        return -XLIM
    if v > XLIM:
        return XLIM
    return v


_OFF = []                      # lateral road offset per edge (world units)
_EX = []                       # projected road centre x per edge
_EY = []                       # projected road y per edge
_EW = []                       # projected road half-width px per edge
_CLIP = []                     # per segment: top of everything nearer (maxy)


def _ensure(n):
    # grow the per-edge scratch arrays in place (they are shared by reference)
    k = n + 2 - len(_OFF)
    if k > 0:
        pad = [0.0] * k
        _OFF.extend(pad)
        _EX.extend(pad)
        _EY.extend(pad)
        _EW.extend(pad)
        _CLIP.extend(pad)


_ensure(DRAW_DISTANCE if DRAW_DISTANCE > SPLIT_DRAW_DISTANCE else SPLIT_DRAW_DISTANCE)

# --- background geometry (built once, scaled per view on first use) -------------
HILL_BASE = HORIZON + 3        # hills/skyline stand on this line
TILE = 640                     # parallax layers repeat every TILE px


def _lcg(seed):
    s = [seed]

    def nxt():
        s[0] = (s[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return s[0] / 2147483648.0
    return nxt


def _make_hills(seed, n, min_h, max_h, min_w, max_w):
    # (left, peak_x, right, peak_y) triangles spread across one tile
    r = _lcg(seed)
    out = []
    step = TILE / n
    for i in range(n):
        cx = i * step + r() * step * 0.6
        half = min_w + r() * (max_w - min_w)
        peak = HILL_BASE - (min_h + r() * (max_h - min_h))
        skew = (r() - 0.5) * half * 0.6
        out.append((cx - half, cx + skew, cx + half, peak))
    return tuple(out)


FAR_HILLS = _make_hills(7, 9, 34, 58, 60, 95)
NEAR_HILLS = _make_hills(31, 11, 10, 24, 40, 75)
_HILL_MAXW = 200.0


def _make_skyline():
    # a cluster of campus towers (x, top_y, w) plus lit windows (x, y, row)
    r = _lcg(4242)
    towers = []
    windows = []
    x = 30.0
    end = 250.0
    while x < end:
        w = 7 + int(r() * 12)
        mid = 1.0 - abs((x - 140.0) / 120.0)     # taller in the middle
        h = 8 + int(r() * 14 + mid * 22)
        top = HILL_BASE - h
        towers.append((x, top, w))
        if h > 14:
            wy = top + 3
            row = 0
            while wy < HILL_BASE - 4:
                if r() < 0.45:
                    windows.append((x + 2 + int(r() * (w - 4)), wy, row))
                wy += 5
                row += 1
        x += w + int(r() * 3)
    # a slim bridge tower and deck out over the bay
    towers.append((330.0, HILL_BASE - 30, 3))
    towers.append((390.0, HILL_BASE - 30, 3))
    towers.append((262.0, HILL_BASE - 8, 190))
    return tuple(towers), tuple(windows)


SKY_TOWERS, SKY_WINDOWS = _make_skyline()
SKY_BANDS = 8
SKY_BANDS_SPLIT = 4
_SKY_H = HILL_BASE / SKY_BANDS

_pal = None
_art = None
_geo = {}                      # (top, height) -> scaled background geometry
_scroll = {}                   # top -> [sun/far, skyline, near offsets, last camera_z]


def _view_geo(top, height):
    # Background geometry for one view, in screen px. For the full screen
    # (0, 240) this is exactly the original layout.
    key = (top, height)
    g = _geo.get(key)
    if g is not None:
        return g
    k = height / HEIGHT
    full = height >= HEIGHT
    base = top + HILL_BASE * k

    def sy(y):
        return top + y * k

    far = tuple((xl, xp, xr, sy(py)) for xl, xp, xr, py in FAR_HILLS)
    near = tuple((xl, xp, xr, sy(py)) for xl, xp, xr, py in NEAR_HILLS)
    towers = tuple((tx, sy(ty), tw, base - sy(ty)) for tx, ty, tw in SKY_TOWERS)
    if full:
        wins = tuple((wx, wy) for wx, wy, row in SKY_WINDOWS)
        ws = 2
    else:
        # keep every other row (and every other window) so the squashed
        # towers don't turn into a solid block of lights
        wins = tuple((wx, int(sy(wy) + 0.5)) for wx, wy, row in SKY_WINDOWS[::2] if not row & 1)
        ws = 1
    bands = SKY_BANDS if full else SKY_BANDS_SPLIT
    bh = (HILL_BASE * k) / bands
    sky = tuple(top + i * bh for i in range(bands))
    g = {
        "k": k,
        "bottom": top + height,
        "hor": top + HORIZON * k,
        "player_y": top + PLAYER_Y * k,
        "kxd": KXD * k,
        "kyd": KYD * k,
        "base": base,
        "sky": sky,
        "sky_h": bh,
        "sky_pens": "sky" if full else "sky4",
        "sun_y": sy(58),
        "sun_r": (27 * k, 21 * k),
        "sun_cut": (sy(66), 2 * k, sy(72), 3 * k, 24 * k),
        "far": far,
        "near": near,
        "towers": towers,
        "wins": wins,
        "win_w": 2,
        "win_h": ws,
        "water": 3 if full else 2,
        "clip_margin": 60 * k,
        "min_scale": MIN_SCALE if full else MIN_SCALE_SPLIT,
    }
    _geo[key] = g
    return g


def _mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def _build_palette():
    global _pal
    p = {}
    sky = []
    for i in range(SKY_BANDS):
        sky.append(rgb(_mix(SKY_TOP, SKY_BOTTOM, i / (SKY_BANDS - 1))))
    p["sky"] = tuple(sky)
    sky = []
    for i in range(SKY_BANDS_SPLIT):
        sky.append(rgb(_mix(SKY_TOP, SKY_BOTTOM, 0.1 + 0.9 * i / (SKY_BANDS_SPLIT - 1))))
    p["sky4"] = tuple(sky)
    p["sun"] = rgb((255, 176, 120))
    p["sun_glow"] = rgb((255, 128, 150), 90)
    p["sun_cut"] = rgb(_mix(SKY_TOP, SKY_BOTTOM, 0.8))
    p["hills_far"] = rgb(HILLS_FAR)
    p["hills"] = rgb(HILLS)
    p["city"] = rgb((40, 30, 86))
    p["window"] = rgb((255, 214, 120))
    p["water"] = rgb((70, 92, 170))
    p["field"] = rgb(_mix(GRASS_DARK, HILLS_FAR, 0.2))
    # index 0 = dark band, 1 = light band
    p["grass"] = (rgb(GRASS_DARK), rgb(GRASS_LIGHT))
    p["rumble"] = (rgb(RUMBLE_DARK), rgb(RUMBLE_LIGHT))
    p["road"] = (rgb(ROAD_DARK), rgb(ROAD_LIGHT))
    p["lane"] = rgb(LANE)
    p["pad"] = rgb(PAD)
    p["speed"] = rgb((255, 255, 255), 200)
    _pal = p


def _set_clip(top, height):
    screen.clip = rect(0, top, WIDTH, height)


def _dz_key(item):
    return item[0]


def _draw_hill_layer(scr, tri, V, hills, ox, base):
    n = 0
    for xl, xp, xr, py in hills:
        x0 = xl - ox
        if x0 < -_HILL_MAXW:
            x0 += TILE
        if x0 < WIDTH:
            d = x0 - xl
            if xr + d > 0:
                tri(V(x0, base), V(xp + d, py), V(xr + d, base))
                n += 1
    return n


def _draw_background(scr, rect, tri, V, camera_x, g, bg):
    pal = _pal
    calls = 0
    base = g["base"]
    # sky bands
    sky = pal[g["sky_pens"]]
    h = g["sky_h"] + 1
    i = 0
    for y in g["sky"]:
        scr.pen = sky[i]
        rect(0, y, WIDTH, h)
        i += 1
    calls += i

    # sun (scrolls with heading, wraps over TILE)
    sx = 236.0 - bg[0] * 0.8
    sx -= int(sx / TILE) * TILE
    if sx < -40:
        sx += TILE
    if sx < WIDTH + 40:
        sy = g["sun_y"]
        r0, r1 = g["sun_r"]
        y0, h0, y1, h1, cw = g["sun_cut"]
        scr.pen = pal["sun_glow"]
        scr.circle(V(sx, sy), r0)
        scr.pen = pal["sun"]
        scr.circle(V(sx, sy), r1)
        scr.pen = pal["sun_cut"]
        rect(sx - cw, y0, cw + cw, h0)
        rect(sx - cw, y1, cw + cw, h1)
        calls += 4

    # far hills
    ox = bg[0] + camera_x * 4.0
    ox -= int(ox / TILE) * TILE
    if ox < 0:
        ox += TILE
    scr.pen = pal["hills_far"]
    calls += _draw_hill_layer(scr, tri, V, g["far"], ox, base)

    # campus skyline + bridge
    ox = bg[1] + camera_x * 7.0
    ox -= int(ox / TILE) * TILE
    if ox < 0:
        ox += TILE
    scr.pen = pal["city"]
    for tx, ty, tw, th in g["towers"]:
        x0 = tx - ox
        if x0 < -_HILL_MAXW:
            x0 += TILE
        if x0 < WIDTH and x0 + tw > 0:
            rect(x0, ty, tw, th)
            calls += 1
    scr.pen = pal["window"]
    ww = g["win_w"]
    wh = g["win_h"]
    for wx, wy in g["wins"]:
        x0 = wx - ox
        if x0 < -_HILL_MAXW:
            x0 += TILE
        if 0 <= x0 < WIDTH:
            rect(x0, wy, ww, wh)
            calls += 1

    # near hills
    ox = bg[2] + camera_x * 12.0
    ox -= int(ox / TILE) * TILE
    if ox < 0:
        ox += TILE
    scr.pen = pal["hills"]
    calls += _draw_hill_layer(scr, tri, V, g["near"], ox, base)
    return calls


def draw_world(track, race_or_none, camera_z, camera_x, now, player_racer_or_none,
               view=None, draw_distance=None):
    global _art
    if _pal is None:
        _build_palette()
    if _art is None:
        import kart_art
        _art = kart_art

    if view is None:
        top = 0
        vh = HEIGHT
    else:
        top = view[0]
        vh = view[1]
    g = _view_geo(top, vh)
    N = DRAW_DISTANCE if draw_distance is None else int(draw_distance)
    if N < 4:
        N = 4
    if N + 2 > len(_OFF):
        _ensure(N)
    clipped = top != 0 or vh != HEIGHT
    if clipped:
        _set_clip(top, vh)

    scr = screen
    rect = scr.rectangle
    tri = scr.triangle
    V = vec2
    pal = _pal
    SEG = SEGMENT_LENGTH
    count = track.count
    length = track.length
    curve = track.curve
    heights = track.height
    pads = track.pads
    OFF = _OFF
    EX = _EX
    EY = _EY
    EW = _EW
    CLIP = _CLIP
    hor = g["hor"]
    kxd = g["kxd"]
    kyd = g["kyd"]
    bottom = g["bottom"]

    # --- camera ---------------------------------------------------------------
    camera_z = camera_z % length
    base = int(camera_z // SEG)
    if base >= count:
        base = count - 1
    frac = camera_z - base * SEG
    pz = camera_z + PLAYER_Z
    if pz >= length:
        pz -= length
    cam_y = CAMERA_HEIGHT + track.height_at(pz)
    cam_x = camera_x * ROAD_WIDTH

    # --- parallax scroll from distance travelled through the current curve ----
    bg = _scroll.get(top)
    if bg is None:
        bg = _scroll[top] = [0.0, 0.0, 0.0, None]
    last = bg[3]
    if last is not None:
        d = camera_z - last
        if d > length * 0.5:
            d -= length
        elif d < -length * 0.5:
            d += length
        if -3000.0 < d < 3000.0:
            k = track.curve_at(pz) * d * (1.0 / SEG)
            bg[0] += k * 1.0
            bg[1] += k * 1.25
            bg[2] += k * 1.6
    bg[3] = camera_z

    calls = _draw_background(scr, rect, tri, V, camera_x, g, bg)

    # --- lateral road offsets, relative to the road tangent at the player ------
    tp = frac + PLAYER_Z
    n_a = int(tp // SEG)
    if n_a > N - 1:
        n_a = N - 1
    pp = (tp - n_a * SEG) * (1.0 / SEG)
    i = (base + n_a) % count
    c = curve[i]
    X = c * pp * pp * 0.5
    S = -c * pp
    OFF[n_a] = X
    e = n_a
    while e > 0:
        i -= 1
        if i < 0:
            i = count - 1
        cj = curve[i]
        X = X - S + cj * 0.5
        S -= cj
        e -= 1
        OFF[e] = X
    q = 1.0 - pp
    Xf = c * q * q * 0.5
    Sf = c * q
    na1 = n_a + 1
    OFF[na1] = Xf

    # --- road: project + draw near -> far, clipped to maxy ----------------------
    gr = pal["grass"]
    rb_pen = pal["rumble"]
    rd_pen = pal["road"]
    lane_pen = pal["lane"]
    pad_pen = pal["pad"]

    idx = base + 1
    if idx >= count:
        idx -= count
    dz = SEG - frac
    inv = 1.0 / dz
    s = kxd * inv
    x1 = HALF_W + (OFF[1] - cam_x) * s
    w1 = ROAD_WIDTH * s
    y1 = hor + (cam_y - heights[idx]) * kyd * inv
    EX[1] = x1
    EY[1] = y1
    EW[1] = w1
    maxy = bottom
    CLIP[0] = bottom

    for n in range(1, N):
        e = n + 1
        nidx = idx + 1
        if nidx == count:
            nidx = 0
        dz += SEG
        if e > na1:
            c = curve[idx]
            Xf += Sf + c * 0.5
            Sf += c
            OFF[e] = Xf
            off = Xf
        else:
            off = OFF[e]
        inv = 1.0 / dz
        s = kxd * inv
        x2 = HALF_W + (off - cam_x) * s
        w2 = ROAD_WIDTH * s
        y2 = hor + (cam_y - heights[nidx]) * kyd * inv
        EX[e] = x2
        EY[e] = y2
        EW[e] = w2
        CLIP[n] = maxy
        if dz < NEAR_Z:
            x1 = x2
            y1 = y2
            w1 = w2
            idx = nidx
            continue

        yt = int(y2 + 1000.5) - 1000          # round, safe for small negatives
        if yt < top:
            yt = top
        if yt < maxy:
            yb = maxy
            h = yb - yt
            maxy = yt
            light = (idx // RUMBLE_LENGTH) & 1
            scr.pen = gr[light]
            rect(0, yt, WIDTH, h)
            calls += 1
            dy = y1 - y2
            if h <= 2 or dy < 0.01:
                # thin band: plain rectangles at the band's middle
                if dy > 0.01:
                    t = ((yt + yb) * 0.5 - y2) / dy
                else:
                    t = 0.0
                xm = x2 + (x1 - x2) * t
                wm = w2 + (w1 - w2) * t
                rw = wm * RUMBLE_W
                if xm + rw > 0 and xm - rw < WIDTH:
                    scr.pen = rb_pen[light]
                    if rw > XLIM:
                        rect(_cl(xm - rw), yt, _cl(xm + rw) - _cl(xm - rw), h)
                        scr.pen = rd_pen[light]
                        rect(_cl(xm - wm), yt, _cl(xm + wm) - _cl(xm - wm), h)
                    else:
                        rect(xm - rw, yt, rw + rw, h)
                        scr.pen = rd_pen[light]
                        rect(xm - wm, yt, wm + wm, h)
                    calls += 2
                    if pads[idx]:
                        scr.pen = pad_pen
                        rect(xm - wm * 0.45, yt, wm * 0.9, h)
                        calls += 1
                    elif light:
                        lw = wm * LANE_W
                        if lw > 0.3:
                            scr.pen = lane_pen
                            rect(xm - lw, yt, lw + lw, h)
                            calls += 1
            else:
                inv_dy = 1.0 / dy
                ta = (yt - y2) * inv_dy
                tb = (yb - y2) * inv_dy
                ddx = x1 - x2
                ddw = w1 - w2
                xa = x2 + ddx * ta
                wa = w2 + ddw * ta
                xb = x2 + ddx * tb
                wb = w2 + ddw * tb
                ra = wa * RUMBLE_W
                rb = wb * RUMBLE_W
                lb = xb - rb
                rbx = xb + rb
                if (xa + ra > 0 or rbx > 0) and (xa - ra < WIDTH or lb < WIDTH):
                    scr.pen = rb_pen[light]
                    if lb < -XLIM or rbx > XLIM or xa - ra < -XLIM or xa + ra > XLIM:
                        # camera right on top of a steep slope: keep the
                        # off-screen vertices in a sane range
                        A = V(_cl(xa - ra), yt)
                        C = V(_cl(rbx), yb)
                        tri(A, V(_cl(xa + ra), yt), C)
                        tri(A, C, V(_cl(lb), yb))
                        scr.pen = rd_pen[light]
                        A = V(_cl(xa - wa), yt)
                        C = V(_cl(xb + wb), yb)
                        tri(A, V(_cl(xa + wa), yt), C)
                        tri(A, C, V(_cl(xb - wb), yb))
                        wide = True
                    else:
                        wide = False
                        A = V(xa - ra, yt)
                        C = V(rbx, yb)
                        tri(A, V(xa + ra, yt), C)
                        tri(A, C, V(lb, yb))
                        scr.pen = rd_pen[light]
                        A = V(xa - wa, yt)
                        C = V(xb + wb, yb)
                        tri(A, V(xa + wa, yt), C)
                        tri(A, C, V(xb - wb, yb))
                    calls += 4
                    if wide:
                        pass
                    elif pads[idx]:
                        # chevron pointing up the road: green arrow, notch cut
                        scr.pen = pad_pen
                        tri(V(xb - wb * 0.55, yb), V(xa, yt), V(xb + wb * 0.55, yb))
                        scr.pen = rd_pen[light]
                        ym = yb - (yb - yt) * 0.5
                        xm = (xa + xb) * 0.5
                        tri(V(xb - wb * 0.3, yb), V(xm, ym), V(xb + wb * 0.3, yb))
                        calls += 2
                    elif light:
                        la = wa * LANE_W
                        lb = wb * LANE_W
                        scr.pen = lane_pen
                        A = V(xa - la, yt)
                        C = V(xb + lb, yb)
                        tri(A, V(xa + la, yt), C)
                        tri(A, C, V(xb - lb, yb))
                        calls += 2
        x1 = x2
        y1 = y2
        w1 = w2
        idx = nidx

    # --- ground between the hills and the far end of the road ----------------
    hb = g["base"]
    if maxy > hb:
        hgt = maxy - hb
        wat = g["water"]
        scr.pen = pal["water"]
        rect(0, hb, WIDTH, wat if hgt > wat else hgt)
        calls += 1
        if hgt > wat:
            scr.pen = pal["field"]
            rect(0, hb + wat, WIDTH, hgt - wat)
            calls += 1

    # --- sprites, far -> near -------------------------------------------------------
    art = _art
    draw_scenery = art.draw_scenery
    sprites = track.sprites
    racing = race_or_none is not None
    player = player_racer_or_none
    min_scale = g["min_scale"]
    margin = g["clip_margin"]

    dyn = None
    if racing:
        dyn = []
        last_dz = (N - 1) * SEG - frac
        for p in track.pickups:
            ra = p.respawn_at
            if ra and ra > now:
                continue
            rel = p.index - base
            if rel < 0:
                rel += count
            if 1 <= rel < N:
                dyn.append((rel * SEG + SEG * 0.5 - frac, 0, p))
        for r in race_or_none.racers:
            if r is player:
                continue
            d = r.z - camera_z
            if d < 0:
                d += length
            if SEG - frac < d < last_dz:
                dyn.append((d, 1, r))
        if len(dyn) > 1:
            dyn.sort(key=_dz_key, reverse=True)
    nd = len(dyn) if dyn else 0
    di = 0
    vp = (N, base, frac, count, heights, cam_x, cam_y, hor, kxd, kyd, bottom, margin, min_scale)

    sidx = (base + N - 1) % count
    for n in range(N - 1, 0, -1):
        dzn = n * SEG - frac
        while di < nd and dyn[di][0] >= dzn:
            _draw_dynamic(dyn[di], art, vp, now)
            di += 1
        sp = sprites[sidx]
        sidx -= 1
        if sidx < 0:
            sidx = count - 1
        if sp:
            w = EW[n]
            sc = w * INV_SPRITE
            if sc < min_scale:
                continue
            y = EY[n]
            clip = CLIP[n]
            if y > clip + (margin if clip >= bottom else 2):
                continue
            x = EX[n]
            lim = 120.0 * sc
            for item in sp:
                cx = x + item[0] * w
                if -lim < cx < WIDTH + lim:
                    draw_scenery(item[1], cx, y, sc)

    # --- subject kart -----------------------------------------------------------------
    if racing and player is not None:
        k = g["k"]
        sp = player.speed * (1.0 / MAX_SPEED)
        boosting = now < player.boost_until
        spin = now < player.spin_until
        bounce = 0
        if sp > 0.05:
            period = 140 - int(90 * (sp if sp < 1.0 else 1.0))
            if (now // period) & 1:
                bounce = 2 if (player.x > 1.0 or player.x < -1.0) else 1
        bump = player.bump
        sx = 0.0
        sy = 0.0
        if bump > 0.01:
            sx = bump * 4.0 * math.sin(now * 0.09)
            sy = bump * 2.0 * math.sin(now * 0.13)
        if k == 1.0:
            cx = HALF_W + (player.x - camera_x) * HALF_W + sx + math.sin(now * 0.003) * 0.8 * sp
        else:
            cx = HALF_W + ((player.x - camera_x) * HALF_W + sx + math.sin(now * 0.003) * 0.8 * sp) * k
            bounce *= k
            sy *= k
        if boosting:
            scr.pen = pal["speed"]
            for j in range(5):
                yy = 120 + ((j * 53 + now // 3) % 110)
                ln = 14 + ((j * 29 + now // 7) % 26)
                y2 = ((j * 71 + now // 3 + 40) % 110) + 120
                if k != 1.0:
                    yy = top + yy * k
                    y2 = top + y2 * k
                    ln *= k
                rect(0, yy, ln, 1)
                rect(WIDTH - ln, y2, ln, 1)
            calls += 10
        art.draw_kart(player.character, cx, g["player_y"] - bounce + sy, k,
                      player.steer, now, spin, boosting)

    if clipped:
        _set_clip(0, HEIGHT)
    stats["frame"] = calls
    stats["calls"] += calls


def _draw_dynamic(item, art, vp, now):
    N, base, frac, count, heights, cam_x, cam_y, hor, kxd, kyd, bottom, margin, min_scale = vp
    dz = item[0]
    SEG = SEGMENT_LENGTH
    t = dz + frac
    n = int(t // SEG)
    if n < 1 or n >= N:
        return
    p = (t - n * SEG) * (1.0 / SEG)
    obj = item[2]
    off = _OFF[n] + (_OFF[n + 1] - _OFF[n]) * p
    hi = (base + n) % count
    h0 = heights[hi]
    hgt = h0 + (heights[hi + 1] - h0) * p
    inv = 1.0 / dz
    s = kxd * inv
    sc = ROAD_WIDTH * s * INV_SPRITE
    if sc < (min_scale if item[1] == 0 else MIN_SCALE):   # karts stay visible to the end
        return
    y = hor + (cam_y - hgt) * kyd * inv
    clip = _CLIP[n]
    if y > clip + (margin if clip >= bottom else 2):
        return
    cx = HALF_W + (off + obj.x * ROAD_WIDTH - cam_x) * s
    lim = 120.0 * sc
    if cx < -lim or cx > WIDTH + lim:
        return
    if item[1] == 0:
        art.draw_pickup(obj.kind, cx, y, sc, now)
    else:
        art.draw_kart(obj.character, cx, y, sc, obj.steer, now,
                      now < obj.spin_until, now < obj.boost_until)


def _reset_views():
    # forget parallax state (tests; also handy when a new race starts)
    _scroll.clear()
