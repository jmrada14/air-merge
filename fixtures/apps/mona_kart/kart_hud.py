# Mona Kart: HUD and menu screens.
#
# Layouts are measured with screen.measure_text so they adapt to the badge
# fonts. Colours are built lazily (color.rgb is a firmware global).

import math

from kart_config import (
    WIDTH, HEIGHT, LAPS, MAX_TOKENS, BOOST_MS, FONT_TITLE, FONT_BIG, FONT_BODY, FONT_HUD, use_font,
)

_sin = math.sin

_K = None
_map = None          # cached minimap: (track id, points, ox, oy, k, umin, vmin, box y)
_mw = {}             # cached measurements of static strings: (font, text) -> (w, h)

PLACE_SUFFIX = ("st", "nd", "rd", "th")
MAP_X = WIDTH - 4 - 64
MAP_W = 64
MAP_H = 48


def fmt_time(ms):
    if ms is None or ms < 0:
        ms = 0
    ms = int(ms)
    return "%d:%02d.%d" % (ms // 60000, (ms // 1000) % 60, (ms // 100) % 10)


def _c(r, g, b, a=255):
    return color.rgb(r, g, b, a)


def _colours():
    global _K
    if _K is None:
        _K = {
            "panel": _c(10, 10, 24, 170),
            "panel2": _c(10, 10, 24, 210),
            "panel_hi": _c(60, 52, 120, 210),
            "white": _c(255, 255, 255),
            "text": _c(236, 236, 248),
            "muted": _c(160, 160, 190),
            "dim": _c(90, 90, 120),
            "black": _c(8, 8, 14),
            "gold": _c(255, 204, 51),
            "gold_d": _c(150, 100, 10),
            "gold_l": _c(255, 244, 170),
            "silver": _c(200, 210, 225),
            "bronze": _c(214, 132, 72),
            "red": _c(240, 60, 80),
            "amber": _c(255, 170, 40),
            "green": _c(80, 230, 120),
            "green_d": _c(20, 120, 50),
            "boost": _c(255, 130, 40),
            "track": _c(220, 220, 235),
            "slot": _c(70, 70, 96),
            "pill_llm": _c(255, 196, 92),
            "pill_har": _c(110, 214, 255),
            "pill_mascot": _c(249, 179, 221),
            "chip": _c(230, 230, 242),
            "band": _c(0, 0, 0, 130),
            "shadow": _c(24, 10, 48),
            "title_mid": _c(214, 50, 96),
            "title_top": _c(255, 228, 130),
            "glass": _c(255, 255, 255, 40),
            "bars": [_c(12 + i * 3, 12 + i * 2, 30 + i * 5) for i in range(10)],
        }
    return _K


def _chars():
    from kart_racers import CHARACTERS
    return CHARACTERS


def _measure(text, fname, cache=True):
    if cache:
        key = fname + text
        wh = _mw.get(key)
        if wh is None:
            if len(_mw) > 96:
                _mw.clear()
            use_font(fname)
            wh = screen.measure_text(text)
            _mw[key] = wh
        return wh
    use_font(fname)
    return screen.measure_text(text)


def _text(text, x, y, fname, pen):
    use_font(fname)
    screen.pen = pen
    screen.text(text, x, y)


def _ctext(text, cx, y, fname, pen, cache=True):
    w, h = _measure(text, fname, cache)
    _text(text, cx - w / 2, y, fname, pen)
    return w, h


def _panel(x, y, w, h, pen):
    # rounded-ish rectangle: two overlapping rects would double the alpha, so
    # draw the body and trim strips separately
    screen.pen = pen
    screen.rectangle(x + 1, y, w - 2, 1)
    screen.rectangle(x, y + 1, w, h - 2)
    screen.rectangle(x + 1, y + h - 1, w - 2, 1)


def _rgb(t, a=255):
    return color.rgb(t[0], t[1], t[2], a)


# --- race HUD -------------------------------------------------------------------

def _build_map(track, my):
    global _map
    pts = track.map_points
    n = len(pts)
    umin = vmin = 1e9
    umax = vmax = -1e9
    for u, v in pts:
        if u < umin:
            umin = u
        if u > umax:
            umax = u
        if v < vmin:
            vmin = v
        if v > vmax:
            vmax = v
    du = umax - umin
    dv = vmax - vmin
    if du <= 0:
        du = 1e-6
    if dv <= 0:
        dv = 1e-6
    iw = MAP_W - 8
    ih = MAP_H - 8
    k = iw / du
    if ih / dv < k:
        k = ih / dv
    ox = MAP_X + (MAP_W - du * k) / 2
    oy = my + (MAP_H - dv * k) / 2
    step = 6
    while n // step > 44:
        step += 1
    poly = []
    for i in range(0, n, step):
        u, v = pts[i]
        poly.append(vec2(ox + (u - umin) * k, oy + (v - vmin) * k))
    poly.append(poly[0])
    _map = (id(track), poly, ox, oy, k, umin, vmin, my)
    return _map


def draw_race_hud(race, track, now):
    K = _colours()
    scr = screen
    p = race.player

    # --- place, top-left ---
    place = p.place
    if place < 1:
        place = 1
    pl_pen = (K["gold"], K["silver"], K["bronze"], K["text"])[min(place, 4) - 1]
    digit = str(place)
    suffix = PLACE_SUFFIX[min(place, 4) - 1]
    dw, dh = _measure(digit, FONT_BIG)
    sw, sh = _measure(suffix, FONT_HUD)
    of = "/%d" % len(race.racers)
    ow, oh = _measure(of, FONT_HUD)
    ph = max(dh, sh + oh + 2) + 8
    pw = 10 + dw + 3 + max(sw, ow) + 7
    _panel(4, 4, pw, ph, K["panel"])
    scr.pen = pl_pen
    scr.rectangle(4, 6, 3, ph - 4)
    _text(digit, 11, 4 + (ph - dh) / 2, FONT_BIG, pl_pen)
    tx = 11 + dw + 3
    ty = 4 + (ph - (sh + oh + 2)) / 2
    _text(suffix, tx, ty, FONT_HUD, pl_pen)
    _text(of, tx, ty + sh + 2, FONT_HUD, K["muted"])

    # --- lap and time, top-right ---
    lap = p.lap + 1
    if lap > LAPS or p.finished:
        lap = LAPS
    final = lap == LAPS
    lw, lh = _measure("LAP ", FONT_HUD)
    ls = "%d/%d" % (lap, LAPS)
    nw, nh = _measure(ls, FONT_HUD)
    if p.finished and p.finish_ms:
        t = p.finish_ms
    elif race.started:
        t = race.elapsed
    else:
        t = 0
    ts = fmt_time(t)
    tw, th = _measure("0:00.0", FONT_HUD)
    bw = max(lw + nw, tw) + 12
    if bw < MAP_W:
        bw = MAP_W
    bh = lh + th + 10
    bx = WIDTH - 4 - bw
    _panel(bx, 4, bw, bh, K["panel"])
    x0 = bx + (bw - lw - nw) / 2
    _text("LAP ", x0, 8, FONT_HUD, K["muted"])
    _text(ls, x0 + lw, 8, FONT_HUD, K["amber"] if final else K["text"])
    _ctext(ts, bx + bw / 2, 8 + lh + 3, FONT_HUD, K["text"], False)

    # --- minimap ---
    my = 4 + bh + 4
    m = _map
    if m is None or m[0] != id(track) or m[7] != my:
        m = _build_map(track, my)
    _panel(MAP_X, my, MAP_W, MAP_H, K["panel"])
    poly = m[1]
    ox = m[2]
    oy = m[3]
    k = m[4]
    umin = m[5]
    vmin = m[6]
    scr.pen = K["track"]
    prev = poly[0]
    for i in range(1, len(poly)):
        cur = poly[i]
        scr.line(prev, cur)
        prev = cur
    pts = track.map_points
    u, v = pts[track.segment_index(track.start_z)]
    scr.pen = K["gold"]
    scr.rectangle(ox + (u - umin) * k - 2, oy + (v - vmin) * k - 1, 4, 2)
    chars = _chars()
    for r in race.racers:
        if r is p:
            continue
        u, v = pts[track.segment_index(r.z)]
        scr.pen = _rgb(chars[r.character]["body"])
        scr.circle(vec2(ox + (u - umin) * k, oy + (v - vmin) * k), 2.2)
    u, v = pts[track.segment_index(p.z)]
    c = vec2(ox + (u - umin) * k, oy + (v - vmin) * k)
    scr.pen = K["white"]
    scr.circle(c, 3.6)
    scr.pen = _rgb(chars[p.character]["body"])
    scr.circle(c, 2.4)

    # --- token pips, bottom-left ---
    boosting = now < p.boost_until
    pip = 7
    tpw = 8 + MAX_TOKENS * (pip * 2 + 4) + 4
    tph = pip * 2 + 8
    ty = HEIGHT - 4 - tph
    _panel(4, ty, tpw, tph, K["panel"])
    cy = ty + tph / 2
    for i in range(MAX_TOKENS):
        c = vec2(4 + 8 + pip + i * (pip * 2 + 4), cy)
        if i < p.tokens:
            scr.pen = K["gold_d"]
            scr.circle(c, pip)
            scr.pen = K["gold"]
            scr.circle(c, pip - 1.5)
            scr.pen = K["gold_l"]
            scr.rectangle(c.x - 3, c.y - 4, 2, 3)
        else:
            scr.pen = K["slot"]
            scr.circle(c, pip)
            scr.pen = K["panel2"]
            scr.circle(c, pip - 2)
    if boosting:
        scr.pen = K["boost"]
        scr.rectangle(6, ty + tph - 3, tpw - 4, 2)
    elif p.tokens > 0 and race.started:
        w, h = _measure("ENTER", FONT_HUD)
        # "ENTER" tag sits above the pips so it never collides with the hint row
        _panel(4, ty - h - 9, w + 8, h + 6, K["panel"])
        _text("ENTER", 8, ty - h - 6, FONT_HUD, K["gold"])


# --- countdown and banners ------------------------------------------------------

def draw_countdown(ms_left):
    K = _colours()
    scr = screen
    cx = WIDTH // 2
    cy = 84
    if ms_left > 0:
        n = (int(ms_left) + 999) // 1000
        if n > 3:
            n = 3
        frac = ((int(ms_left) - 1) % 1000) / 1000.0
        pop = (frac - 0.8) / 0.2 if frac > 0.8 else 0.0
        col = (K["green"], K["amber"], K["red"])[n - 1]
        dw, dh = _measure(str(n), FONT_BIG)
        size = max(dw, dh) + 34 + 18 * pop
        x = cx - size / 2
        y = cy - size / 2
        _panel(x - 2, y - 2, size + 4, size + 4, col)
        _panel(x, y, size, size, K["panel2"])
        _text(str(n), cx - dw / 2, cy - dh / 2 - 5, FONT_BIG, col)
        # three start lamps
        lit = 4 - n
        for i in range(3):
            scr.pen = col if i < lit else K["slot"]
            scr.circle(vec2(cx - 12 + i * 12, y + size - 8), 3.5)
    else:
        w, h = _measure("GO!", FONT_TITLE)
        pw = w + 36
        ph = h + 20
        _panel(cx - pw / 2 - 2, cy - ph / 2 - 2, pw + 4, ph + 4, K["white"])
        _panel(cx - pw / 2, cy - ph / 2, pw, ph, K["green_d"])
        _text("GO!", cx - w / 2 + 2, cy - h / 2 + 2, FONT_TITLE, K["black"])
        _text("GO!", cx - w / 2, cy - h / 2, FONT_TITLE, K["green"])


def draw_banner(text, now):
    K = _colours()
    fname = FONT_TITLE
    w, h = _measure(text, fname)
    if w > WIDTH - 40:
        fname = FONT_BODY
        w, h = _measure(text, fname)
    pw = w + 28
    ph = h + 14
    x = (WIDTH - pw) / 2
    y = 106 - ph / 2
    flash = K["gold"] if (now // 140) % 2 else K["white"]
    _panel(x, y, pw, ph, K["panel2"])
    screen.pen = flash
    screen.rectangle(x + 3, y + 2, pw - 6, 2)
    screen.rectangle(x + 3, y + ph - 4, pw - 6, 2)
    _text(text, x + 14, y + (ph - h) / 2, fname, K["white"])


# --- title ----------------------------------------------------------------------

TITLE_MODES = ("SOLO RACE", "VERSUS 1v1")


def draw_title(now, best_ms, choice=0):
    # choice: 0 = SOLO, 1 = VERSUS (highlighted entry of the mode menu)
    K = _colours()
    scr = screen
    chars = _chars()

    scr.pen = K["band"]
    scr.rectangle(0, 18, WIDTH, 92)

    title = "MONA KART"
    w, h = _measure(title, FONT_TITLE)
    if w <= WIDTH - 24:
        lines = (title,)
    else:
        lines = ("MONA", "KART")
    y = 26
    for line in lines:
        w, h = _measure(line, FONT_TITLE)
        x = (WIDTH - w) / 2
        _text(line, x + 3, y + 3, FONT_TITLE, K["shadow"])
        _text(line, x + 1.5, y + 1.5, FONT_TITLE, K["title_mid"])
        _text(line, x, y, FONT_TITLE, K["title_top"])
        y += h + 4

    # checkered stripe with a dot per driver colour in the middle
    y += 4
    q = 6
    n = len(chars)
    gap = 12                        # dot spacing
    side = int((WIDTH / 2 - 40 - (n - 1) * gap / 2 - 10) // q)
    if side > 16:
        side = 16
    for i in range(side):
        scr.pen = K["white"] if i % 2 == 0 else K["black"]
        scr.rectangle(40 + i * q, y, q, q / 2)
        scr.pen = K["black"] if i % 2 == 0 else K["white"]
        scr.rectangle(40 + i * q, y + q / 2, q, q / 2)
        scr.rectangle(WIDTH - 40 - (i + 1) * q, y, q, q / 2)
        scr.pen = K["white"] if i % 2 == 0 else K["black"]
        scr.rectangle(WIDTH - 40 - (i + 1) * q, y + q / 2, q, q / 2)
    for i in range(n):
        c = vec2(WIDTH / 2 + (i - (n - 1) / 2) * gap, y + q / 2)
        b = chars[i]["body"]
        if b[0] + b[1] + b[2] < 120:
            scr.pen = K["muted"]          # ring keeps a black livery visible
            scr.circle(c, 4.6)
        scr.pen = _rgb(b)
        scr.circle(c, 4)
    y += q + 8

    sub = "Merge Bay Loop"
    w, h = _measure(sub, FONT_BODY)
    _panel((WIDTH - w) / 2 - 10, y, w + 20, h + 8, K["panel2"])
    _text(sub, (WIDTH - w) / 2, y + 4, FONT_BODY, K["text"])

    # mode menu: two stacked pills, the chosen one framed in gold with arrows
    mw = 0
    mh = 0
    for s in TITLE_MODES:
        w, h = _measure(s, FONT_BODY)
        if w > mw:
            mw = w
        if h > mh:
            mh = h
    pw = mw + 48
    ph = mh + 10
    x = (WIDTH - pw) / 2
    choice = 1 if choice == 1 else 0
    nudge = _sin(now * 0.008) * 2
    for i in range(len(TITLE_MODES)):
        s = TITLE_MODES[i]
        w, h = _measure(s, FONT_BODY)
        y = 132 + i * (ph + 6)
        if i == choice:
            _panel(x - 2, y - 2, pw + 4, ph + 4, K["gold"])
            _panel(x, y, pw, ph, K["panel_hi"])
            _text(s, (WIDTH - w) / 2, y + (ph - h) / 2, FONT_BODY, K["white"])
            scr.pen = K["gold"]
            ay = y + ph / 2
            ax = x + 8 + nudge
            scr.triangle(vec2(ax, ay - 4), vec2(ax + 6, ay), vec2(ax, ay + 4))
            ax = x + pw - 8 - nudge
            scr.triangle(vec2(ax, ay - 4), vec2(ax - 6, ay), vec2(ax, ay + 4))
        else:
            _panel(x, y, pw, ph, K["panel2"])
            _text(s, (WIDTH - w) / 2, y + (ph - h) / 2, FONT_BODY, K["muted"])

    if best_ms:
        s = "BEST " + fmt_time(best_ms)
        w, h = _measure(s, FONT_HUD, False)
        by = 132 + 2 * (ph + 6) + 6
        if by < 194:
            by = 194
        _panel((WIDTH - w) / 2 - 8, by, w + 16, h + 6, K["panel"])
        _text(s, (WIDTH - w) / 2, by + 3, FONT_HUD, K["text"])

    draw_hints((("choose", "UP/DOWN"), ("go", "ENTER")))


# --- split screen (versus) ---------------------------------------------------------

def _ahead_of(o, r):
    # True when racer o is ahead of racer r (finish time first, then progress)
    if o.finished and o.finish_ms:
        if r.finished and r.finish_ms:
            return o.finish_ms < r.finish_ms
        return True
    if r.finished and r.finish_ms:
        return False
    return o.total > r.total


def _rival_of(race, r):
    for o in race.racers:
        if o is not r:
            return o
    return None


def _gap_text(gap_ms):
    if gap_ms is None:
        return "LEVEL"
    g = int(gap_ms)
    if -100 < g < 100:
        return "LEVEL"
    a = -g if g < 0 else g
    return "%s%d.%ds" % ("+" if g > 0 else "-", a // 1000, (a // 100) % 10)


def draw_view_hud(race, racer, view, now, label):
    # Compact overlay for one split-screen view (top, height); also fine for
    # a full-height view. About 25-35 primitives.
    K = _colours()
    scr = screen
    top = view[0]
    vh = view[1]
    chars = _chars()
    ch = chars[racer.character]

    # label + place pill, top-left
    lw, lh = _measure(label, FONT_HUD)
    if lw > 72:
        label = ch.get("short", ch["name"])
        lw, lh = _measure(label, FONT_HUD)
    place = 1
    for o in race.racers:
        if o is not racer and _ahead_of(o, racer):
            place += 1
    pi = min(place, 4) - 1
    ps = "%d%s" % (place, PLACE_SUFFIX[pi])
    pw, phh = _measure(ps, FONT_HUD)
    bh = (lh if lh > phh else phh) + 6
    y = top + 4
    _panel(4, y, 6 + lw + 8 + pw + 6, bh, K["panel"])
    scr.pen = _rgb(ch["body"])
    scr.rectangle(4, y + 1, 3, bh - 2)
    _text(label, 10, y + (bh - lh) / 2, FONT_HUD, K["white"])
    _text(ps, 10 + lw + 8, y + (bh - phh) / 2, FONT_HUD,
          (K["gold"], K["silver"], K["bronze"], K["text"])[pi])

    # lap (or finish time), top-right
    if racer.finished and racer.finish_ms:
        s = fmt_time(racer.finish_ms)
        sw, sh = _measure(s, FONT_HUD, False)
        bw = sw + 12
        _panel(WIDTH - 4 - bw, y, bw, sh + 6, K["panel"])
        _text(s, WIDTH - 4 - bw + 6, y + 3, FONT_HUD, K["gold"])
    else:
        lap = racer.lap + 1
        if lap > LAPS:
            lap = LAPS
        if lap < 1:
            lap = 1
        ls = "%d/%d" % (lap, LAPS)
        aw, ah = _measure("LAP ", FONT_HUD)
        nw, nh = _measure(ls, FONT_HUD)
        bw = aw + nw + 12
        bx = WIDTH - 4 - bw
        _panel(bx, y, bw, ah + 6, K["panel"])
        _text("LAP ", bx + 6, y + 3, FONT_HUD, K["muted"])
        _text(ls, bx + 6 + aw, y + 3, FONT_HUD, K["amber"] if lap == LAPS else K["text"])

    # token pips (and boost bar), bottom-left
    pip = 4 if vh < HEIGHT else 6
    step = pip * 2 + 3
    tpw = 6 + MAX_TOKENS * step + 3
    tph = pip * 2 + 6
    ty = top + vh - 6 - tph
    _panel(4, ty, tpw, tph, K["panel"])
    cy = ty + tph / 2
    for i in range(MAX_TOKENS):
        c = vec2(4 + 6 + pip + i * step, cy)
        if i < racer.tokens:
            scr.pen = K["gold_d"]
            scr.circle(c, pip)
            scr.pen = K["gold"]
            scr.circle(c, pip - 1.2)
        else:
            scr.pen = K["slot"]
            scr.circle(c, pip - 1)
    left = racer.boost_until - now
    if left > 0:
        f = left / float(BOOST_MS)
        if f > 1.0:
            f = 1.0
        bx = 4 + tpw + 4
        scr.pen = K["panel2"]
        scr.rectangle(bx, cy - 2, 44, 4)
        scr.pen = K["boost"]
        scr.rectangle(bx, cy - 2, 44 * f, 4)


def draw_split_divider(race, now, gap_ms):
    # Band between the two views: your colour on the upper edge line, the
    # rival's on the lower one, a progress dot per racer along it and the
    # time gap in a centred pill ("+1.2s" = you are behind by 1.2 s).
    K = _colours()
    scr = screen
    chars = _chars()
    me = race.player
    rival = _rival_of(race, me)
    scr.pen = K["black"]
    scr.rectangle(0, 118, WIDTH, 5)
    mine = _rgb(chars[me.character]["body"])
    theirs = _rgb(chars[rival.character]["body"]) if rival is not None else K["muted"]
    scr.pen = mine
    scr.rectangle(0, 119, WIDTH, 1)
    scr.pen = theirs
    scr.rectangle(0, 121, WIDTH, 1)

    # lap progress dots
    import kart_track
    total = LAPS * kart_track.TRACK.length
    for r, pen, dy in ((rival, theirs, 1.5), (me, mine, -1.5)):
        if r is None:
            continue
        f = 1.0 if r.finished else r.total / total
        if f < 0:
            f = 0.0
        elif f > 1:
            f = 1.0
        c = vec2(8 + f * (WIDTH - 16), 120 + dy)
        scr.pen = K["white"]
        scr.circle(c, 3.2)
        scr.pen = pen
        scr.circle(c, 2.2)

    # gap pill
    s = _gap_text(gap_ms)
    w, h = _measure(s, FONT_HUD, False)
    if s == "LEVEL":
        pen = K["white"]
    elif s[0] == "+":
        pen = K["red"]
    else:
        pen = K["green"]
    pw = w + 14
    ph = h + 6
    x = (WIDTH - pw) / 2
    # hangs from the divider into the rival view's sky, so it never covers
    # the wheels of your kart (bottom y 116)
    y = 118
    _panel(x - 1, y - 1, pw + 2, ph + 2, pen)
    _panel(x, y, pw, ph, K["black"])
    _text(s, x + 7, y + 3, FONT_HUD, pen)


_HEADLINES = {
    "win": ("YOU WIN!", "gold"),
    "lose": ("YOU LOSE", "red"),
    "draw": ("DRAW", "white"),
    "gone": ("RIVAL LEFT", "amber"),
}


def _vs_row(K, chars, r, name, me, y, rh, won):
    scr = screen
    if me:
        _panel(14, y - 1, WIDTH - 28, rh + 2, K["white"])
        _panel(16, y + 1, WIDTH - 32, rh - 2, K["panel_hi"])
    else:
        _panel(16, y + 1, WIDTH - 32, rh - 2, K["panel2"])
    cy = y + rh / 2
    ch = chars[r.character] if r is not None else None
    if ch is not None:
        gw, gh = _measure(ch["glyph"], FONT_HUD)
        cw = gw + 8 if gw + 8 > 22 else 22
        scr.pen = _rgb(ch["body"])
        scr.rectangle(26, cy - 11, cw, 22)
        _text(ch["glyph"], 26 + (cw - gw) / 2, cy - gh / 2, FONT_HUD, _rgb(ch["accent"]))
    else:
        cw = 22
    if not name or name == "YOU":       # the YOU tag already says it
        name = ch["name"] if ch is not None else "?"
    nx = 26 + cw + 10
    nw, nh = _measure(name, FONT_BODY, False)
    if nw > 150:
        name = ch.get("short", name) if ch is not None else name[:8]
        nw, nh = _measure(name, FONT_BODY, False)
    best = None
    if r is not None and r.lap_ms:
        best = min(r.lap_ms)
    ny = cy - nh / 2 if best is None else cy - nh - 1
    _text(name, nx, ny, FONT_BODY, K["white"] if me else K["text"])
    tag = "YOU" if me else "RIVAL"
    tw, th = _measure(tag, FONT_HUD)
    tx = nx + nw + 8
    _panel(tx, ny + (nh - th) / 2 - 2, tw + 8, th + 4, K["gold"] if me else K["pill_har"])
    _text(tag, tx + 4, ny + (nh - th) / 2, FONT_HUD, K["black"])
    if best is not None:
        _text("best lap " + fmt_time(best), nx, cy + 2, FONT_HUD, K["muted"])
    ts = fmt_time(r.finish_ms) if (r is not None and r.finished and r.finish_ms) else "--"
    tw, th = _measure(ts, FONT_BODY, False)
    _text(ts, WIDTH - 26 - tw, cy - th / 2, FONT_BODY, K["gold"] if won else K["text"])


def draw_versus_results(race, now, my_name, rival_name, outcome, status=None):
    # outcome: "win" | "lose" | "draw" | "gone". Leaves y ~150..205 free for
    # the rematch status: pass `status` (e.g. "RIVAL WANTS A REMATCH") to have
    # it drawn there as a flashing strip centred on y 170. (draw_banner sits
    # at y 106, on top of the rival row.)
    K = _colours()
    scr = screen
    chars = _chars()
    me = race.player if race is not None else None
    rival = _rival_of(race, me) if race is not None else None
    _background(K, chars[me.character]["body"] if me is not None else None, now)

    q = 10
    for i in range(WIDTH // q):
        scr.pen = K["white"] if i % 2 == 0 else K["black"]
        scr.rectangle(i * q, 0, q, 5)
        scr.pen = K["black"] if i % 2 == 0 else K["white"]
        scr.rectangle(i * q, 5, q, 5)

    head, key = _HEADLINES.get(outcome, ("FINISH", "white"))
    w, h = _measure(head, FONT_TITLE)
    hx = (WIDTH - w) / 2
    if outcome == "win" and (now % 600) < 300:
        key = "gold_l"
    _text(head, hx + 2, 20, FONT_TITLE, K["shadow"])
    _text(head, hx, 18, FONT_TITLE, K[key])

    y = 18 + h + 14
    rh = 34
    rows = ((me, my_name, True, outcome == "win"), (rival, rival_name, False, outcome == "lose"))
    for r, name, mine, won in rows:
        _vs_row(K, chars, r, name, mine, y, rh, won)
        y += rh + 6

    if (outcome == "win" or outcome == "lose") and me is not None and rival is not None \
            and me.finish_ms and rival.finish_ms:
        d = rival.finish_ms - me.finish_ms
        if d < 0:
            d = -d
        s = "by %d.%ds" % (d // 1000, (d // 100) % 10)
        _ctext(s, WIDTH / 2, y + 2, FONT_HUD, K["muted"], False)

    if status:
        fname = FONT_BODY
        w, h = _measure(status, fname, False)
        if w > WIDTH - 40:
            fname = FONT_HUD
            w, h = _measure(status, fname, False)
        pw = w + 28
        ph = h + 12
        x = (WIDTH - pw) / 2
        y = 170 - ph / 2
        _panel(x, y, pw, ph, K["panel2"])
        scr.pen = K["gold"] if (now // 140) % 2 else K["white"]
        scr.rectangle(x + 3, y + 2, pw - 6, 1)
        scr.rectangle(x + 3, y + ph - 3, pw - 6, 1)
        _text(status, x + 14, y + (ph - h) / 2, fname, K["white"])

    if outcome == "gone":
        draw_hints((("leave", "BACK"),))      # nobody left to rematch
    else:
        draw_hints((("rematch", "ENTER"), ("leave", "BACK")))


# --- character select -----------------------------------------------------------

def _background(K, stripe_rgb, now):
    scr = screen
    bars = K["bars"]
    bh = HEIGHT / len(bars)
    for i in range(len(bars)):
        scr.pen = bars[i]
        scr.rectangle(0, i * bh, WIDTH, bh + 1)
    if stripe_rgb is not None:
        scr.pen = _rgb(stripe_rgb, 34)
        off = (now // 40) % 64
        for i in range(-3, 6):
            x0 = i * 64 + off
            scr.triangle(vec2(x0, 0), vec2(x0 + 26, 0), vec2(x0 + 26 + 100, HEIGHT))
            scr.triangle(vec2(x0, 0), vec2(x0 + 126, HEIGHT), vec2(x0 + 100, HEIGHT))


_KIND_PILL = {"LLM": "pill_llm", "HARNESS": "pill_har", "MASCOT": "pill_mascot"}

_STATS = (("SPEED", "top", 0.85, 1.0), ("ACCEL", "accel", 0.5, 1.0),
          ("GRIP", "handling", 0.6, 1.0), ("WEIGHT", "weight", 0.5, 1.0))


def _fit_name(name, room):
    # -> (lines, font): one title line, two title lines split at a space, or body font
    w, h = _measure(name, FONT_TITLE)
    if w <= room:
        return (name,), FONT_TITLE
    sp = name.find(" ")
    if sp > 0:
        a = name[:sp]
        b = name[sp + 1:]
        if _measure(a, FONT_TITLE)[0] <= room and _measure(b, FONT_TITLE)[0] <= room:
            return (a, b), FONT_TITLE
    if _measure(name, FONT_BODY)[0] <= room:
        return (name,), FONT_BODY
    return (name,), FONT_HUD


_road_pens = None


def _select_kart(kart_art, index, cx, by, now):
    global _road_pens
    if _road_pens is None:
        _road_pens = (_c(84, 88, 100), _c(235, 235, 240), _c(200, 40, 70), _c(240, 240, 245))
    road, lane, rum_a, rum_b = _road_pens
    scr = screen
    # a little perspective road: narrow at the top, wide at the bottom
    top = by - 30
    dh = by + 3 - top
    scr.pen = road
    scr.triangle(vec2(cx - 26, top), vec2(cx + 26, top), vec2(cx + 62, by + 3))
    scr.triangle(vec2(cx - 26, top), vec2(cx + 62, by + 3), vec2(cx - 62, by + 3))
    # rumble bands and lane dashes scroll towards the viewer
    ph = ((now % 480) / 480.0) / 6.0
    for i in range(6):
        f0 = i / 6.0 + ph
        f1 = f0 + 1 / 6.0
        if f1 > 1:
            f1 = 1.0
        y0 = top + dh * f0 * f0
        y1 = top + dh * f1 * f1
        hm = 26 + 36 * ((y0 + y1) * 0.5 - top) / dh
        rw = 2 + f1 * 3
        scr.pen = rum_a if i & 1 else rum_b
        scr.rectangle(cx - hm - rw, y0, rw, y1 - y0 + 0.5)
        scr.rectangle(cx + hm, y0, rw, y1 - y0 + 0.5)
        if i & 1:
            scr.pen = lane
            scr.rectangle(cx - 0.5 - f1, y0, 1 + f1 * 2, y1 - y0)
    if ph < 1 / 6.0:
        # the band entering at the horizon
        scr.pen = rum_b
        y1 = top + dh * ph * ph
        scr.rectangle(cx - 28, top, 2, y1 - top + 0.5)
        scr.rectangle(cx + 26, top, 2, y1 - top + 0.5)
    steer = _sin(now * 0.0016) * 1.0
    boosting = (now % 4200) < 900
    kart_art.draw_kart(index, cx + steer * 10, by, 0.7, steer, now, False, boosting)


def draw_select(index, now):
    import kart_art
    K = _colours()
    scr = screen
    chars = _chars()
    n = len(chars)
    index %= n
    ch = chars[index]
    body = ch["body"]
    if body[0] * 0.3 + body[1] * 0.59 + body[2] * 0.11 < 60:
        # very dark livery (Grok): tint the UI with a pale version of the accent
        a = ch["accent"]
        body = (a[0] * 3 // 5, a[1] * 3 // 5 + 20, min(255, a[2] * 3 // 5 + 40))
    bpen = _rgb(body)
    light = _rgb((min(255, body[0] + 90), min(255, body[1] + 90), min(255, body[2] + 90)))

    _background(K, body, now)

    # header and page indicator
    _text("SELECT DRIVER", 10, 8, FONT_HUD, K["muted"])
    s = "%d/%d" % (index + 1, n)
    w, h = _measure(s, FONT_HUD, False)
    dots_w = n * 10
    _text(s, WIDTH - 10 - w - dots_w - 4, 8, FONT_HUD, K["text"])
    for i in range(n):
        scr.pen = bpen if i == index else K["slot"]
        scr.circle(vec2(WIDTH - 10 - dots_w + 5 + i * 10, 8 + h / 2), 3.2 if i == index else 2.2)

    # portrait and arrows
    pcx = 78
    pcy = 88
    kart_art.draw_portrait(index, pcx, pcy, 98, now)
    nudge = _sin(now * 0.008) * 2
    scr.pen = K["white"]
    ax = 8 - nudge
    scr.triangle(vec2(ax, pcy), vec2(ax + 10, pcy - 11), vec2(ax + 10, pcy + 11))
    ax = 148 + nudge
    scr.triangle(vec2(ax, pcy), vec2(ax - 10, pcy - 11), vec2(ax - 10, pcy + 11))

    # kart preview on a scrolling strip of road, weaving and boosting now and then
    _select_kart(kart_art, index, pcx, 176, now)

    # name (title font if it fits, else split over two lines, else body font), kind pill
    x0 = 160
    room = WIDTH - 8 - x0
    lines, nfont = _fit_name(ch["name"], room)
    ny = 28
    nw = 0
    nh = 0
    for ln in lines:
        lw, lh = _measure(ln, nfont)
        _text(ln, x0 + 2, ny + nh + 2, nfont, K["black"])
        _text(ln, x0, ny + nh, nfont, light)
        nh += lh + 2
        if lw > nw:
            nw = lw
    nh -= 2
    scr.pen = bpen
    scr.rectangle(x0, ny + nh + 3, nw if nw > 40 else 40, 2)
    kind = ch["kind"].upper()
    kw, kh = _measure(kind, FONT_HUD)
    py = ny + nh + 10
    _panel(x0, py, kw + 12, kh + 6, K[_KIND_PILL.get(kind, "pill_har")])
    _text(kind, x0 + 6, py + 3, FONT_HUD, K["black"])

    # stat bars
    y = py + kh + 16
    bx = x0 + 52
    bw = WIDTH - 10 - bx
    for label, key, lo, hi in _STATS:
        lw, lh = _measure(label, FONT_HUD)
        rh = lh if lh > 8 else 8
        _text(label, x0, y + (rh - lh) / 2, FONT_HUD, K["muted"])
        f = (ch[key] - lo) / (hi - lo)
        if f < 0:
            f = 0.0
        elif f > 1:
            f = 1.0
        f = 0.2 + 0.8 * f
        by = y + (rh - 8) / 2
        scr.pen = K["slot"]
        scr.rectangle(bx, by, bw, 8)
        scr.pen = bpen
        scr.rectangle(bx, by, bw * f, 8)
        scr.pen = K["glass"]
        scr.rectangle(bx, by, bw * f, 3)
        scr.pen = K["bars"][0]
        for j in range(1, 5):
            scr.rectangle(bx + bw * j / 5 - 0.5, by, 1, 8)
        y += rh + 9

    # blurb strip
    blurb = ch["blurb"]
    fname = FONT_BODY
    w, h = _measure(blurb, fname)
    if w > WIDTH - 24:
        fname = FONT_HUD
        w, h = _measure(blurb, fname)
    by = 181
    scr.pen = K["band"]
    scr.rectangle(0, by, WIDTH, h + 14)
    scr.pen = bpen
    scr.rectangle(0, by, WIDTH, 2)
    _text(blurb, (WIDTH - w) / 2, by + 7, fname, K["text"])

    draw_hints((("pick", "LEFT/RIGHT"), ("race", "ENTER")))


# --- results --------------------------------------------------------------------

def _order(race):
    out = list(race.racers)
    # finished racers by time, then the rest by race progress
    out.sort(key=lambda r: (0, r.finish_ms) if (r.finished and r.finish_ms) else (1, -r.total))
    return out


def _winner_sparkles(K, x, y, now):
    # three twinkling 4-point stars that pop in turn around the winner's chip
    scr = screen
    for i in range(3):
        t = ((now + i * 260) % 780) / 780.0
        r = 3.5 * _sin(t * 3.1416)
        if r < 0.6:
            continue
        sx = x + (-15, 14, 9)[i]
        sy = y + (-7, -9, 8)[i]
        scr.pen = K["gold_l"] if i != 1 else K["white"]
        scr.rectangle(sx - r, sy - 0.5, r * 2 + 1, 1)
        scr.rectangle(sx, sy - r, 1, r * 2 + 1)


def draw_results(race, now, new_best):
    import kart_art
    K = _colours()
    scr = screen
    chars = _chars()
    p = race.player
    _background(K, chars[p.character]["body"], now)

    # checkered top strip
    q = 10
    for i in range(WIDTH // q):
        scr.pen = K["white"] if i % 2 == 0 else K["black"]
        scr.rectangle(i * q, 0, q, 5)
        scr.pen = K["black"] if i % 2 == 0 else K["white"]
        scr.rectangle(i * q, 5, q, 5)

    w, h = _measure("RESULTS", FONT_TITLE)
    _text("RESULTS", (WIDTH - w) / 2 + 2, 18, FONT_TITLE, K["shadow"])
    _text("RESULTS", (WIDTH - w) / 2, 16, FONT_TITLE, K["title_top"])

    y = 18 + h + 10
    rh = 25
    for i, r in enumerate(_order(race)):
        ch = chars[r.character]
        me = r is p
        if me:
            _panel(14, y - 1, WIDTH - 28, rh + 2, K["white"])
            _panel(16, y + 1, WIDTH - 32, rh - 2, K["panel_hi"])
        else:
            _panel(16, y + 1, WIDTH - 32, rh - 2, K["panel2"])
        place = i + 1
        ppen = (K["gold"], K["silver"], K["bronze"], K["text"])[min(place, 4) - 1]
        ps = "%d%s" % (place, PLACE_SUFFIX[min(place, 4) - 1])
        pw, ph = _measure(ps, FONT_HUD)
        cy = y + rh / 2
        _text(ps, 24, cy - ph / 2, FONT_HUD, ppen)
        # driver chip: a tiny animated portrait; the winner's bounces with sparkles
        ccx = 68
        ccy = cy
        if place == 1:
            hop = _sin((now % 700) * 0.004488)    # 0..1..0 over 700 ms
            ccy = cy - 4 * hop
            _winner_sparkles(K, ccx, cy, now)
        kart_art.draw_portrait(r.character, ccx, ccy, 22, now)
        ts = fmt_time(r.finish_ms) if (r.finished and r.finish_ms) else "--"
        tw, th = _measure(ts, FONT_HUD, False)
        nx = ccx + 19
        room = WIDTH - 26 - tw - 8 - nx
        if me:
            yw, yh = _measure("YOU", FONT_HUD)
            room -= yw + 16
        name = ch["name"]
        nw, nh = _measure(name, FONT_BODY)
        if nw > room:
            name = ch.get("short", name)
            nw, nh = _measure(name, FONT_BODY)
        _text(name, nx, cy - nh / 2, FONT_BODY, K["white"] if me else K["text"])
        if me:
            tx = nx + nw + 8
            _panel(tx, cy - yh / 2 - 2, yw + 8, yh + 4, K["gold"])
            _text("YOU", tx + 4, cy - yh / 2, FONT_HUD, K["black"])
        _text(ts, WIDTH - 26 - tw, cy - th / 2, FONT_HUD, K["text"])
        y += rh + 4

    y += 4
    if p.lap_ms:
        s = "BEST LAP " + fmt_time(min(p.lap_ms))
        w, h = _measure(s, FONT_HUD, False)
        _text(s, (WIDTH - w) / 2, y, FONT_HUD, K["muted"])
        y += h + 6
    if new_best and (now % 600) < 420:
        w, h = _measure("NEW BEST!", FONT_BODY)
        _panel((WIDTH - w) / 2 - 10, y, w + 20, h + 8, K["gold"])
        _text("NEW BEST!", (WIDTH - w) / 2, y + 4, FONT_BODY, K["black"])

    draw_hints((("again", "ENTER"), ("menu", "BACK")))


# --- hints ----------------------------------------------------------------------

def draw_hints(pairs):
    K = _colours()
    total = 0
    bh = 0
    for label, btn in pairs:
        bw, h = _measure(btn, FONT_HUD)
        lw, lh = _measure(label, FONT_HUD)
        total += bw + 8 + 4 + lw + 12
        if h > bh:
            bh = h
        if lh > bh:
            bh = lh
    total -= 12
    ch = bh + 4
    cy = 229
    x = (WIDTH - total) / 2
    _panel(x - 6, cy - ch / 2 - 3, total + 12, ch + 6, K["panel"])
    for label, btn in pairs:
        bw, h = _measure(btn, FONT_HUD)
        lw, lh = _measure(label, FONT_HUD)
        _panel(x, cy - ch / 2, bw + 8, ch, K["chip"])
        _text(btn, x + 4, cy - h / 2, FONT_HUD, K["black"])
        x += bw + 8 + 4
        _text(label, x, cy - lh / 2, FONT_HUD, K["text"])
        x += lw + 12
