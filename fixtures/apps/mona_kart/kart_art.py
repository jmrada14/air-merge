# Mona Kart: sprite art built from screen primitives.
#
# Everything is drawn from rectangles, triangles, circles and the odd line, in a
# local coordinate frame that is scaled onto the screen:
#   karts    : x -32..32, y -46..0 (bottom_y is y = 0), 64 px wide at scale 1.0
#   scenery  : x centred on cx, y up from bottom_y (negative = up)
#   portrait : centred on (cx, cy), a 110 px wide disc at size 110
#
# The five drivers are stylised fan designs from simple shapes (colour + a motif),
# not official logos:
#   0 Claude Code    blocky orange critter, cream sparkle emblem
#   1 GitHub Copilot round robot pilot with aviator goggles
#   2 DeepSeek       cute whale with a flapping tail and a water spout
#   3 Grok           black helmet, dark visor with a white slash and a scan line
#   4 Mona           the Octocat (navy cat head, cream face, waving tentacles) in a
#                    green kart with a contribution-graph rear panel
#
# Kart levels of detail: < 0.25 tiny (4 prims), 0.25..0.6 medium, >= 0.6 full
# (<= 60 prims). Emblems are primitives, never text, so they always fit the body.
#
# Firmware globals (screen, color, vec2) are only touched inside functions, and
# all colours are built lazily on the first draw call.

import math

from kart_config import FONT_HUD, FONT_TITLE, use_font

_sin = math.sin
_cos = math.cos
_sqrt = math.sqrt

# --- local drawing frame ------------------------------------------------------
# _X, _Y: screen origin of the frame; _SX: x scale (negative mirrors); _S: y scale

_X = 0.0
_Y = 0.0
_SX = 1.0
_S = 1.0


def _frame(x, y, sx, sy):
    global _X, _Y, _SX, _S
    _X = x
    _Y = y
    _SX = sx
    _S = sy


def _r(x, y, w, h):
    sx = _SX
    if sx >= 0:
        screen.rectangle(_X + x * sx, _Y + y * _S, w * sx, h * _S)
    else:
        screen.rectangle(_X + (x + w) * sx, _Y + y * _S, -w * sx, h * _S)


def _t(x1, y1, x2, y2, x3, y3):
    X = _X
    Y = _Y
    sx = _SX
    s = _S
    screen.triangle(vec2(X + x1 * sx, Y + y1 * s), vec2(X + x2 * sx, Y + y2 * s),
                    vec2(X + x3 * sx, Y + y3 * s))


def _q(x1, y1, x2, y2, x3, y3, x4, y4):
    # convex quad, corners in order
    _t(x1, y1, x2, y2, x3, y3)
    _t(x1, y1, x3, y3, x4, y4)


def _tg(x1, y1, x2, y2, x3, y3, k):
    # triangle grown by factor k about its centroid (cheap outline)
    mx = (x1 + x2 + x3) / 3.0
    my = (y1 + y2 + y3) / 3.0
    _t(mx + (x1 - mx) * k, my + (y1 - my) * k, mx + (x2 - mx) * k, my + (y2 - my) * k,
       mx + (x3 - mx) * k, my + (y3 - my) * k)


def _c(x, y, r):
    screen.circle(vec2(_X + x * _SX, _Y + y * _S), r * _S)


def _l(x1, y1, x2, y2):
    screen.line(vec2(_X + x1 * _SX, _Y + y1 * _S), vec2(_X + x2 * _SX, _Y + y2 * _S))


# --- colours --------------------------------------------------------------------

_WHITE = (255, 255, 255)


def _shade(c, k):
    r = int(c[0] * k)
    g = int(c[1] * k)
    b = int(c[2] * k)
    return (r if r < 255 else 255, g if g < 255 else 255, b if b < 255 else 255)


def _mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def _rgb(c, a=255):
    return color.rgb(c[0], c[1], c[2], a)


# per style: (body half width, body top, top inset, head y)
_BODY = (
    (22, -21, 0, -36),     # 0 Claude: boxy, chunky
    (21, -20, 3, -34),     # 1 Copilot: rounded trapezoid
    (21, -19, 3, -35),     # 2 DeepSeek: low, rounded, tail instead of a wing
    (24, -17, 6, -31),     # 3 Grok: low wide wedge
    (21, -19, 2, -34),     # 4 Mona: rounded tub, tentacles instead of a wing
)
_STYLE_BY_NAME = {"Claude Code": 0, "GitHub Copilot": 1, "DeepSeek": 2, "Grok": 3,
                  "Mona": 4}

# Mona's contribution graph: resting shade per cell (5 x 2, row-major), 0..4
_CG_BASE = (1, 3, 0, 2, 1, 0, 2, 1, 3, 0)

_pals = None
_K = None


class _Pal:
    pass


def _make_pal(i, ch):
    p = _Pal()
    body = ch["body"]
    acc = ch["accent"]
    st = _STYLE_BY_NAME.get(ch["name"], i % len(_BODY))
    p.style = st
    p.body = _rgb(body)
    p.light = _rgb(_mix(body, _WHITE, 0.4))
    p.dark = _rgb(_shade(body, 0.68))
    p.deep = _rgb(_shade(body, 0.4))
    p.acc = _rgb(acc)
    p.acc_d = _rgb(_mix(acc, body, 0.4))
    p.trim = p.light
    p.ring = _rgb(_mix(body, _WHITE, 0.3))
    p.disc = _rgb(_shade(body, 0.3))
    p.glow = _rgb(body, 70)
    p.tyre_hi = _rgb((92, 94, 108))
    if st == 0:      # Claude: orange critter, cream sparkle
        p.drv = _rgb((236, 136, 96))
        p.drv_l = _rgb((252, 178, 140))
        p.drv_d = _rgb((184, 88, 58))
        p.out = _rgb((70, 32, 22))
        p.eye = _rgb((32, 20, 18))
        p.fx = p.acc
        p.trim = p.acc
    elif st == 1:    # Copilot: lilac robot head, purple goggles
        p.drv = p.acc
        p.drv_l = _rgb(_WHITE)
        p.drv_d = _rgb((182, 168, 240))
        p.out = _rgb((44, 22, 120))
        p.eye = _rgb((20, 16, 52))
        p.fx = _rgb((120, 236, 255))
    elif st == 2:    # DeepSeek: light-blue whale, white belly
        p.drv = _rgb((118, 150, 255))
        p.drv_l = _rgb((176, 198, 255))
        p.drv_d = _rgb((54, 76, 206))
        p.out = _rgb((18, 28, 104))
        p.eye = _rgb((14, 18, 50))
        p.fx = _rgb((176, 222, 255))
        p.fx2 = _rgb((236, 248, 255))
        p.disc = _rgb((12, 30, 74))
    elif st == 4:    # Mona: navy Octocat, cream face, GitHub-green kart
        p.drv = _rgb((4, 66, 137))
        p.drv_l = _rgb((40, 116, 214))
        p.drv_d = _rgb((5, 38, 76))
        p.out = _rgb((3, 22, 48))
        p.eye = _rgb((14, 22, 44))
        p.fx = _rgb((255, 211, 61))          # gold sparkles, as on the badge
        p.peach = _rgb((255, 209, 173))
        p.ear = _rgb((249, 179, 221))
        p.trim = _rgb((120, 220, 140))
        p.cg_bg = _rgb((13, 17, 23))
        p.whisk = _rgb((196, 208, 232))
        p.hole = _rgb(_mix(_shade(body, 0.3), body, 0.275))   # disc under the glow
        p.cg = [_rgb(c) for c in ((33, 38, 45), (14, 68, 41), (0, 109, 50),
                                   (38, 166, 65), (57, 211, 83))]
    else:            # Grok: black, white accents, pale cyan neon edge
        p.light = _rgb((74, 74, 84))
        p.dark = _rgb((14, 14, 17))
        p.deep = _rgb((6, 6, 8))
        p.acc_d = _rgb((150, 150, 158))
        p.drv = _rgb((40, 40, 48))
        p.drv_l = _rgb((92, 92, 104))
        p.drv_d = _rgb((20, 20, 24))
        p.out = _rgb((170, 238, 255))
        p.eye = _rgb((6, 6, 10))
        p.fx = _rgb((120, 232, 255))
        p.trim = p.out
        p.tyre_hi = _rgb((96, 170, 190))
        p.ring = _rgb((200, 244, 255))
        p.disc = _rgb((10, 10, 14))
        p.glow = _rgb((120, 232, 255), 40)
    return p


def _palette(character):
    global _pals
    if _pals is None:
        from kart_racers import CHARACTERS
        _pals = [_make_pal(i, ch) for i, ch in enumerate(CHARACTERS)]
    return _pals[character % len(_pals)]


def _colours():
    global _K
    if _K is None:
        k = {}
        for name, c in (
            ("tyre", (26, 26, 32)), ("tread", (62, 62, 74)), ("hub", (150, 154, 168)),
            ("pipe", (160, 160, 172)), ("pipe_in", (20, 20, 24)), ("tail", (255, 60, 70)),
            ("flame_o", (255, 112, 28)), ("flame_y", (255, 222, 90)), ("white", (255, 255, 255)),
            ("black", (8, 8, 12)), ("star", (255, 230, 90)), ("star_d", (200, 150, 40)),
            ("trunk", (96, 62, 40)), ("pine_d", (16, 92, 64)),
            ("pine_l", (34, 132, 84)), ("rack_f", (58, 62, 78)), ("rack_d", (20, 22, 30)),
            ("unit", (40, 44, 58)), ("led_g", (80, 255, 140)), ("led_a", (255, 190, 60)),
            ("led_b", (90, 170, 255)), ("pole", (70, 74, 90)), ("lamp", (255, 236, 160)),
            ("bush_d", (22, 104, 56)), ("bush_l", (46, 150, 78)), ("flower", (255, 120, 170)),
            ("sign_r", (214, 36, 60)), ("post", (180, 184, 196)), ("arch", (52, 48, 92)),
            ("arch_l", (88, 80, 150)), ("gold", (255, 200, 40)), ("gold_d", (176, 112, 10)),
            ("gold_l", (255, 244, 170)), ("bug", (178, 34, 92)), ("bug_d", (62, 12, 44)),
            ("bug_l", (236, 90, 140)), ("bug_leg", (40, 10, 30)), ("blush", (255, 140, 170)),
        ):
            k[name] = _rgb(c)
        k["shadow"] = color.rgb(0, 0, 0, 90)
        k["fglow"] = color.rgb(255, 150, 40, 70)
        k["smoke"] = [color.rgb(205, 205, 215, a) for a in (150, 110, 75, 40)]
        k["glow"] = color.rgb(255, 210, 90, 60)
        k["glow2"] = color.rgb(255, 230, 140, 90)
        k["shine"] = color.rgb(255, 255, 255, 70)
        k["sheen"] = [color.rgb(255, 255, 255, a) for a in (200, 120, 60)]
        k["tglow"] = color.rgb(255, 214, 60, 80)
        k["bglow"] = color.rgb(255, 40, 120, 60)
        k["blush_a"] = color.rgb(255, 120, 160, 110)
        k["scan_a"] = color.rgb(120, 232, 255, 80)
        _K = k
    return _K


_glyph_px = {}


def _glyph_size(text):
    # measured once per string with the font the caller set
    wh = _glyph_px.get(text)
    if wh is None:
        wh = screen.measure_text(text)
        _glyph_px[text] = wh
    return wh


# --- karts ----------------------------------------------------------------------

_LO = False   # far kart (0.25..0.4): skip the small stuff
_BOOST = False  # the kart being drawn is boosting (Mona glances back, grinning)

# spin cycle: (view, direction) with views 0 rear, 1 rear 3/4, 2 side, 3 front 3/4, 4 front
_SPIN = ((0, 1), (1, 1), (2, 1), (3, 1), (4, 1), (3, -1), (2, -1), (1, -1))


def draw_kart(character, cx, bottom_y, scale, steer, now, spin=False, boosting=False):
    if scale <= 0:
        return
    P = _palette(character)
    K = _colours()
    if steer > 1:
        steer = 1
    elif steer < -1:
        steer = -1
    if scale < 0.25:
        _frame(cx, bottom_y, scale, scale)
        _kart_tiny(P, K, steer)
        return
    global _LO, _BOOST
    hi = scale >= 0.6
    _LO = scale < 0.4
    _BOOST = boosting and not spin
    if spin:
        _kart_spin(P, K, cx, bottom_y, scale, now, hi)
        return
    _kart_rear(P, K, cx, bottom_y, scale, steer, now, boosting, hi, True, True)


def _kart_tiny(P, K, steer):
    st = P.style
    hw = _BODY[st][0]
    hx = steer * 3
    scr = screen
    scr.pen = K["tyre"]
    _r(-32, -14, 64, 14)
    scr.pen = P.body
    _r(-hw, -21, hw * 2, 15)
    if st == 0:
        scr.pen = P.drv
        _r(hx - 9, -38, 18, 17)
        scr.pen = P.acc
        _r(-4, -17, 8, 7)
    elif st == 1:
        scr.pen = P.drv
        _c(hx, -32, 10)
        scr.pen = P.out
        _r(hx - 10, -34, 20, 4)
    elif st == 2:
        scr.pen = P.drv
        _c(hx, -31, 11)
        scr.pen = P.acc
        _t(-12, -30, 12, -30, 0, -21)
    elif st == 3:
        scr.pen = P.drv
        _c(hx, -30, 10)
        scr.pen = P.acc
        _q(-8, -8, -2, -8, 8, -20, 2, -20)
    else:
        # navy cat head with two pointed ears
        scr.pen = P.drv
        _c(hx, -32, 10)
        _t(hx - 10, -33, hx - 2, -40, hx - 9, -47)
        _t(hx + 10, -33, hx + 2, -40, hx + 9, -47)


def _bob(now):
    return _sin(now * 0.021) * 0.8 + 0.4


def _kart_rear(P, K, cx, by, s, steer, now, boosting, hi, driver, fx):
    scr = screen
    st = P.style
    hw, top, ins, hy = _BODY[st]
    a = steer if steer >= 0 else -steer

    # --- wheels: unsprung, drawn in the ground frame
    _frame(cx, by, s, s)
    scr.pen = K["shadow"]
    _r(-31, -2.5, 62, 4.5)
    fw = 7 + a * 3.5
    fxo = steer * 2.5
    lx = -19 + fxo - fw * 0.5
    rx = 19 + fxo - fw * 0.5
    scr.pen = K["tyre"]
    _r(lx, -28, fw, 11)
    _r(rx, -28, fw, 11)
    if hi and a > 0.25:
        # turned front wheels show a sidewall and hub on the side away from the turn
        scr.pen = K["hub"]
        ho = fw - 3 if steer < 0 else 0.5
        _r(lx + ho, -26.5, 2.5, 5)
        _r(rx + ho, -26.5, 2.5, 5)
    scr.pen = K["tyre"]
    _r(-32, -16, 13, 16)
    _r(19, -16, 13, 16)
    # rolling tread bars
    roll = (now * 0.11) % 16.0
    scr.pen = K["tread"]
    if hi:
        for k in (0.0, 8.0):
            y = -2.6 - ((roll + k) % 16.0) * 0.82
            _r(-30.5, y, 10, 1.7)
            _r(20.5, y, 10, 1.7)
        scr.pen = P.tyre_hi
        _r(-32, -14, 1.4, 12)
        _r(30.6, -14, 1.4, 12)
    elif not _LO:
        y = -2.6 - (roll % 8.0) * 1.6
        _r(-30.5, y, 10, 2)
        _r(20.5, y, 10, 2)

    # --- sprung body: bobs on the suspension, tilts into the steer
    bob = _bob(now) if fx else 0.0
    _frame(cx, by + bob * s, s, s)
    tr = steer * 1.6
    yl = top - tr
    yr = top + tr
    if not _LO:
        scr.pen = P.deep
        _r(-hw + 4, -7, hw * 2 - 8, 4)
    scr.pen = P.dark
    _r(-hw, -12.5, hw * 2, 6)
    scr.pen = P.body
    _q(-hw + ins, yl, hw - ins, yr, hw, -12, -hw, -12)
    if hi:
        scr.pen = P.trim
        _l(-hw + ins + 1, yl + 1, hw - ins - 1, yr + 1)
        if st == 3:
            _l(-hw, -12, -hw + ins, yl)
            _l(hw, -12, hw - ins, yr)
        elif st == 0:
            scr.pen = P.dark
            _r(-hw, -13.5, hw * 2, 1.5)

    # tail lights (y follows the tilted top edge)
    ly = yl + 3
    ry = yr + 3
    if st == 1:
        # round twin lamps: the goggles motif
        for x, y in ((-hw + 6.5, ly + 2.5), (hw - 6.5, ry + 2.5)):
            scr.pen = P.acc
            _c(x, y, 3.8)
            scr.pen = K["tail"]
            _c(x, y, 2.4)
    elif st == 3:
        scr.pen = K["tail"]
        _r(-hw + ins, ly, 9, 1.6)
        _r(hw - ins - 9, ry, 9, 1.6)
    elif st == 4:
        pass        # the contribution graph lights the back; tentacles hang at the corners
    else:
        scr.pen = K["tail"]
        _r(-hw + 1.5 + ins, ly, 5, 3)
        _r(hw - 6.5 - ins, ry, 5, 3)

    _emblem(P, K, 0, (yl + yr) * 0.5 + 6.5, now, hi)

    # exhaust pipes
    ex = 11
    ey = -8
    if not _LO:
        scr.pen = K["pipe"]
        _c(-ex, ey, 2.6)
        _c(ex, ey, 2.6)
    if hi:
        scr.pen = K["pipe_in"]
        _c(-ex, ey, 1.3)
        _c(ex, ey, 1.3)

    # --- driver: head bobs with a little lag and leans into the turn
    if driver:
        if not _LO:
            scr.pen = P.deep
            _r(-9 + steer * 1.5, top - 4.5, 18, 5)
        hb = (_sin((now - 90) * 0.021) * 1.2 + 0.4) if fx else 0.0
        _frame(cx, by + hb * s, s, s)
        _head(P, K, 0, steer * 3.2, hy, now, steer, hi)
        _frame(cx, by + bob * s, s, s)

    _wing(P, K, top, steer, now, hi)

    if fx:
        if boosting:
            _flames(K, ex, ey, now, hi)
        elif not _LO:
            _smoke(K, ex, ey, now, hi)


def _emblem(P, K, x, y, now, hi):
    # rear panel motif centred on (x, y), at most ~16 x 8 local units
    scr = screen
    st = P.style
    if st == 0:
        # cream 8-point sparkle that turns and twinkles
        scr.pen = P.acc
        tw = 0.8 + 0.25 * _sin(now * 0.009)
        if _LO:
            _r(x - 4.5 * tw, y - 1, 9 * tw, 2)
            _r(x - 1, y - 4 * tw, 2, 8 * tw)
        else:
            # four 1 px strokes = 8 points; lines never outgrow the panel
            rot = now * 0.0016
            for k in range(4):
                ang = rot + k * 0.7854
                dx = _cos(ang) * 4.6 * tw
                dy = _sin(ang) * 4.6 * tw
                _l(x - dx, y - dy, x + dx, y + dy)
            if hi:
                _c(x, y, 1.3)
    elif st == 1:
        # lilac pilot's wings with a goggle-lens centre that glints
        scr.pen = P.acc
        _t(x - 1, y - 1.8, x - 9, y - 3.2, x - 2, y + 2)
        _t(x + 1, y - 1.8, x + 9, y - 3.2, x + 2, y + 2)
        scr.pen = P.fx if (now % 2200) < 300 else P.deep
        _c(x, y, 2.4)
    elif st == 2:
        # little white whale: round body and a tail
        scr.pen = P.acc
        _c(x - 2, y + 0.5, 3.2)
        _t(x, y, x + 6, y - 3.5 + _sin(now * 0.011), x + 5.5, y + 2.5)
        if hi:
            scr.pen = P.deep
            _r(x - 3.5, y - 0.5, 1, 1)
    elif st == 4:
        _contrib(P, x, y, now, hi)
    else:
        # bold white diagonal slash with a travelling glint
        scr.pen = P.acc
        _q(x - 5.5, y + 4.5, x - 1.5, y + 4.5, x + 5.5, y - 4.5, x + 1.5, y - 4.5)
        if hi:
            g = (now % 2400) / 400.0
            if g < 1:
                scr.pen = P.fx
                gx = x - 4 + g * 8
                gy = y + 3.5 - g * 7
                _l(gx - 1, gy, gx + 2, gy)


def _contrib(P, x, y, now, hi):
    # contribution graph: a dark panel of green squares; a bright wave sweeps
    # left to right over resting shades. Boosting = a full green streak.
    scr = screen
    pens = P.cg
    scr.pen = P.cg_bg
    _r(x - 9.8, y - 4.2, 19.6, 8.4)
    if _LO:
        lit = int(now // 300) % 3
        scr.pen = pens[4]
        _r(x - 8.5 + lit * 6, y - 3, 5, 6)
        return
    if hi:
        cols = 5
        cell = 3.0
        gap = 0.8
    else:
        cols = 3
        cell = 4.8
        gap = 1.2
    gw = cols * cell + (cols - 1) * gap
    x0 = x - gw * 0.5
    y0 = y - cell - gap * 0.5
    if _BOOST:
        # on a streak: every column lit, flickering bright
        scr.pen = pens[4 if (now // 90) % 2 else 3]
        _r(x0, y0, gw, cell * 2 + gap)
        scr.pen = P.cg_bg
        for col in range(1, cols):
            gx = x0 + col * (cell + gap) - gap * 0.5
            _l(gx, y0, gx, y0 + cell * 2 + gap)
        return
    t = now * 0.0055
    for row in range(2):
        for col in range(cols):
            w = _sin(t - col * 0.85 - row * 0.45)
            lvl = int(w * 4.9) if w > 0 else 0
            b = _CG_BASE[row * 5 + col]
            scr.pen = pens[lvl if lvl > b else b]
            _r(x0 + col * (cell + gap), y0 + row * (cell + gap), cell, cell)


def _tent(x0, y0, x1, y1, x2, y2, w0, w1, full):
    # tapered tentacle: a thick quad from the base (half width w0) to the bend
    # (half width w1), then a pointed tip that curls off towards (x2, y2).
    # Not full: one tapering triangle from the base straight to the tip.
    dx = x1 - x0
    dy = y1 - y0
    d = _sqrt(dx * dx + dy * dy)
    if d < 0.01:
        d = 0.01
    nx = -dy / d
    ny = dx / d
    if full:
        ax = nx * w1
        ay = ny * w1
        _q(x0 + nx * w0, y0 + ny * w0, x1 + ax, y1 + ay, x1 - ax, y1 - ay,
           x0 - nx * w0, y0 - ny * w0)
        _t(x1 + ax, y1 + ay, x2, y2, x1 - ax, y1 - ay)
    else:
        _t(x0 + nx * w0, y0 + ny * w0, x2, y2, x0 - nx * w0, y0 - ny * w0)


def _tent3(x0, y0, x1, y1, x2, y2, x3, y3, w0, w1, w2):
    # S-curved tentacle: two tapering segments with mitred joints, then a tip
    # towards (x3, y3) (x3 None: no tip, the caller adds a curl)
    dx = x1 - x0
    dy = y1 - y0
    d = _sqrt(dx * dx + dy * dy) + 0.01
    ax = -dy / d
    ay = dx / d
    ex = x2 - x1
    ey = y2 - y1
    e = _sqrt(ex * ex + ey * ey) + 0.01
    bx = -ey / e
    by = ex / e
    mx = ax + bx
    my = ay + by
    m = _sqrt(mx * mx + my * my) + 0.01
    mx = mx / m * w1
    my = my / m * w1
    bx *= w2
    by *= w2
    _q(x0 + ax * w0, y0 + ay * w0, x1 + mx, y1 + my, x1 - mx, y1 - my,
       x0 - ax * w0, y0 - ay * w0)
    _q(x1 + mx, y1 + my, x2 + bx, y2 + by, x2 - bx, y2 - by, x1 - mx, y1 - my)
    if x3 is not None:
        _t(x2 + bx, y2 + by, x3, y3, x2 - bx, y2 - by)


def _mona_tentacles(P, top, steer, now, hi):
    # two tentacles spill over the rear deck, hang down the corners and end in
    # a curl; two more rise from her shoulders and wave like arms (tucked in
    # while boosting: she grips the wheel and glances back instead)
    scr = screen
    full = not _LO
    t = now * 0.0065
    wl = steer * 1.2
    hx = steer * 3.2
    for side in (-1, 1):
        w1 = _sin(t + side * 1.1)
        scr.pen = P.drv
        if hi:
            x2 = side * (18.2 + w1 * 0.5) + wl
            y2 = top + 5
            _tent3(side * 9.5 + wl, top - 2.5, side * 16 + wl, top - 0.5, x2, y2,
                   None, None, 2.5, 2.1, 1.8)
            # the curl: a ring hooking back inward under the tip
            cx = x2 - side * (1.4 - w1 * 0.4)
            cy = y2 + 2.2
            _c(cx, cy, 2.7)
            scr.pen = P.body
            _c(cx - side * 0.3, cy - 0.3, 1.1)
        else:
            # cheaper: one bend, the tip curling outward, no ring
            _tent(side * 9.5 + wl, top - 2.5, side * (17.5 + w1 * 0.5) + wl, top + 2,
                  side * (20 + w1) + wl, top + 7.5, 2.6, 2.0, full)
        if not _BOOST:
            w2 = _sin(t * 1.35 + 2.0 + side * 0.8)
            scr.pen = P.drv
            _tent(side * 7 + hx, top - 6, side * 15 + hx, top - 9 - w2 * 1.6,
                  side * (13 + w2 * 0.8) + hx, top - 15.5 - w2 * 2.2, 2.5, 1.9, hi)


def _wing(P, K, top, steer, now, hi):
    scr = screen
    st = P.style
    wl = steer * 1.2
    if st == 0:
        # low blocky cream ducktail with tall terracotta end plates
        scr.pen = P.acc
        _r(-24 + wl, top - 5.5, 48, 3.5)
        scr.pen = P.dark
        _r(-27 + wl, top - 10, 3.5, 10)
        _r(23.5 + wl, top - 10, 3.5, 10)
        if hi:
            scr.pen = P.deep
            _r(-24 + wl, top - 2, 48, 1.2)
    elif st == 1:
        if hi:
            scr.pen = P.deep
            _r(-14 + wl, top - 3, 2.5, 3)
            _r(11.5 + wl, top - 3, 2.5, 3)
        scr.pen = P.body
        _r(-22 + wl, top - 6.5, 44, 3.5)
        _c(-22 + wl, top - 4.75, 1.75)
        _c(22 + wl, top - 4.75, 1.75)
        if hi:
            scr.pen = P.acc
            _r(-21 + wl, top - 6.5, 42, 1.2)
    elif st == 2:
        # the whale's tail flukes rise from behind the seat and flap
        # (curls up beside the head like a "Y")
        _ytail(P, 17 + wl, top, _sin(now * 0.011) * 2.5)
    elif st == 4:
        _mona_tentacles(P, top, steer, now, hi)
    else:
        # thin blade wing with tall angular end plates and a neon edge
        if hi:
            scr.pen = P.deep
            _r(-11 + wl, top - 6, 2.5, 6)
            _r(8.5 + wl, top - 6, 2.5, 6)
        scr.pen = P.body
        _r(-26 + wl, top - 9, 52, 3)
        scr.pen = P.acc
        _t(-28 + wl, top - 3, -24 + wl, top - 3, -27 + wl, top - 15)
        _t(28 + wl, top - 3, 24 + wl, top - 3, 27 + wl, top - 15)
        if hi:
            scr.pen = P.trim
            _l(-24 + wl, top - 9, 24 + wl, top - 9)


def _ytail(P, x, y0, flap):
    # whale tail: stalk rising 11 units from (x, y0), two flukes in a "Y"
    y = y0 - 11
    scr = screen
    scr.pen = P.out
    _q(x - 5, y0 + 1, x + 4.5, y0 + 1, x + 3.5, y, x - 2.5, y)
    _t(x - 3, y + 2.5, x - 11, y - 8 + flap, x + 2.5, y - 2)
    _t(x + 4, y + 2.5, x + 11, y - 9 - flap, x - 0.5, y - 2)
    scr.pen = P.drv
    _q(x - 3.5, y0, x + 3, y0, x + 2.2, y, x - 1.2, y)
    _t(x - 1.6, y + 1, x - 8.6, y - 6 + flap, x + 1.2, y - 1.6)
    _t(x + 2.6, y + 1, x + 8.6, y - 7 - flap, x + 0.2, y - 1.6)


def _smoke(K, ex, ey, now, hi):
    # puffs drift up and outward from alternating pipes, growing and fading
    scr = screen
    sm = K["smoke"]
    n = 3 if hi else 2
    per = 660
    for i in range(n):
        f = ((now + i * per // n) % per) / float(per)
        side = 1 if i & 1 else -1
        scr.pen = sm[int(f * 3.99)]
        _c(side * (ex + 2 + f * 9), ey + 2 - f * 9, 1.6 + f * 3.4)


def _flames(K, ex, ey, now, hi):
    scr = screen
    fl = (now // 40) % 3
    ln = 11 + 3 * _sin(now * 0.045) + fl * 2.5
    if hi:
        scr.pen = K["fglow"]
        _c(-ex, ey + 2, 5 + fl)
        _c(ex, ey + 2, 5 + fl)
    for side in (-1, 1):
        x = side * ex
        scr.pen = K["flame_o"]
        _t(x - 4.2, ey - 1, x + 4.2, ey - 1, x + side * ln * 0.3, ey + ln)
        scr.pen = K["flame_y"]
        l2 = ln * (0.6 + 0.1 * fl)
        _t(x - 2.6, ey - 0.5, x + 2.6, ey - 0.5, x + side * l2 * 0.25, ey + l2)
        if hi:
            scr.pen = K["white"]
            _c(x, ey + 1, 1.5 + (fl & 1) * 0.5)


# --- heads ----------------------------------------------------------------------
# face: 0 seen from behind, 1 side (facing +x in the frame), 2 from the front


def _head(P, K, face, hx, hy, now, steer, hi):
    st = P.style
    if st == 0:
        _head_claude(P, K, face, hx, hy, now, hi)
    elif st == 1:
        _head_copilot(P, K, face, hx, hy, now, hi)
    elif st == 2:
        _head_whale(P, K, face, hx, hy, now, hi)
    elif st == 3:
        _head_grok(P, K, face, hx, hy, now, hi)
    else:
        _head_mona(P, K, face, hx, hy, now, hi)


def _head_claude(P, K, face, hx, hy, now, hi):
    scr = screen
    w = now % 2600
    wave = w < 1000
    up = (2.5 + 2.5 * _sin(now * 0.03)) if wave else 0.0
    # outline
    scr.pen = P.out
    _r(hx - 10, hy - 8.5, 20, 16)
    # stubby arms with claws: one waves now and then, otherwise both grip the wheel
    scr.pen = P.drv_d
    if face == 1:
        _r(hx + 6, hy + 1, 8, 3)
        _r(hx + 12, hy - 1 - up * 0.5, 3, 5)
    else:
        _r(hx - 14, hy + 1.5, 5, 3)
        _r(hx - 15.5, hy, 3, 5)
        _r(hx + 9, hy + 1.5 - up, 5, 3)
        _r(hx + 12.5, hy - 1.5 - up * 1.3, 3.5, 5)
    scr.pen = P.drv
    _r(hx - 9, hy - 7.5, 18, 14)
    if hi:
        scr.pen = P.drv_l
        _r(hx - 9, hy - 7.5, 18, 2.5)
    # eyes: two dark blocks; it glances back at you every few seconds
    blink = (now % 3100) < 120
    scr.pen = P.eye
    if face == 2 or (face == 0 and (now % 4200) < 1500):
        eh = 1.2 if blink else 5
        _r(hx - 5.5, hy - 3 + (5 - eh) * 0.5, 3, eh)
        _r(hx + 2.5, hy - 3 + (5 - eh) * 0.5, 3, eh)
    elif face == 1:
        _r(hx + 4, hy - 3, 3, 1.2 if blink else 5)


def _head_copilot(P, K, face, hx, hy, now, hi):
    scr = screen
    scr.pen = P.out
    _c(hx, hy, 9.8)
    scr.pen = P.drv
    _c(hx, hy, 8.6)
    # antenna with a blinking tip
    scr.pen = P.out
    _r(hx - 0.6, hy - 13.5, 1.2, 5)
    scr.pen = P.fx if (now // 400) % 2 else P.body
    _c(hx, hy - 13.5, 1.7)
    glint = (now % 2200) < 300
    blink = (now % 3300) < 140
    if face == 0:
        # goggle strap round the back, lens rims poking out at the sides
        scr.pen = P.body
        _r(hx - 9.4, hy - 1.8, 18.8, 3.4)
        _c(hx - 9.2, hy - 0.2, 3.2)
        _c(hx + 9.2, hy - 0.2, 3.2)
        if hi:
            scr.pen = P.fx if glint else P.eye
            _c(hx - 9.8, hy - 0.2, 1.8)
            _c(hx + 9.8, hy - 0.2, 1.8)
    elif face == 1:
        scr.pen = P.body
        _r(hx - 9, hy - 2, 12, 3.4)
        _t(hx + 2, hy - 6.5, hx + 12, hy - 5, hx + 2, hy - 4)
        _c(hx + 6.5, hy - 0.5, 4.2)
        scr.pen = P.eye
        _c(hx + 7.2, hy - 0.5, 2.8)
        if not blink:
            scr.pen = P.fx
            _r(hx + 7, hy - 1.8, 1.6, 2.6)
    else:
        # aviator goggles with a visor brim above
        scr.pen = P.body
        _r(hx - 9.5, hy - 2, 19, 3.4)
        _r(hx - 8, hy - 7.5, 16, 2.2)
        for gx in (-4.3, 4.3):
            scr.pen = P.body
            _c(hx + gx, hy - 0.3, 4.1)
            scr.pen = P.eye
            _c(hx + gx, hy - 0.3, 2.8)
            if hi or not blink:
                scr.pen = P.fx
                if blink:
                    _r(hx + gx - 2, hy - 0.2, 4, 0.9)
                else:
                    _r(hx + gx - 0.6, hy - 1.6, 1.4, 2.6)
        scr.pen = P.out
        _r(hx - 2.5, hy + 5, 5, 1.2)


def _head_whale(P, K, face, hx, hy, now, hi):
    scr = screen
    # spout first so the head overlaps its base
    ph = now % 2000
    if ph < 700:
        f = ph / 700.0
        h = 9 * _sin(f * 3.1416)
        scr.pen = P.fx
        _r(hx - 1.2, hy - 10 - h, 2.4, h)
        if f > 0.3:
            scr.pen = P.fx2
            d = 2 + f * 5
            _c(hx - d, hy - 10 - h + f * 3, 1.7)
            _c(hx + d, hy - 10 - h + f * 3, 1.7)
            if hi:
                _c(hx, hy - 11.5 - h, 1.8)
    if face == 1:
        scr.pen = P.out
        _c(hx, hy + 1, 11)
        _r(hx, hy - 6, 13, 13)
        scr.pen = P.drv
        _c(hx, hy + 1, 10)
        _r(hx, hy - 5, 12, 11)
        scr.pen = P.acc
        _r(hx - 6, hy + 4, 18, 4)
        scr.pen = P.eye
        _c(hx + 6, hy, 1.5)
        return
    scr.pen = P.out
    _c(hx, hy + 1, 11.2)
    _r(hx - 11.2, hy + 1, 22.4, 10)
    scr.pen = P.drv
    _c(hx, hy + 1, 10)
    _r(hx - 10, hy + 1, 20, 10)
    if face == 2:
        scr.pen = P.acc
        _c(hx, hy + 6, 7)
        scr.pen = P.drv
        _r(hx - 8, hy - 1.5, 16, 4)
        scr.pen = P.eye
        blink = (now % 2900) < 130
        _r(hx - 6, hy - 1, 2.6, 0.9 if blink else 2.6)
        _r(hx + 3.4, hy - 1, 2.6, 0.9 if blink else 2.6)
        if hi:
            scr.pen = K["blush_a"]
            _c(hx - 6.5, hy + 3.5, 1.8)
            _c(hx + 6.5, hy + 3.5, 1.8)
    else:
        if hi:
            scr.pen = P.drv_l
            _c(hx - 3.5, hy - 3, 3.2)
        # whale eyes sit on the sides, so they peek out from behind too
        scr.pen = P.eye
        _c(hx - 9.4, hy + 3, 1.5)
        _c(hx + 9.4, hy + 3, 1.5)


def _head_grok(P, K, face, hx, hy, now, hi):
    scr = screen
    scr.pen = P.out
    _c(hx, hy, 10)
    scr.pen = P.drv
    _c(hx, hy, 9)
    sp = (now % 1600) / 1600.0     # visor scan phase
    if face == 0:
        # wrap-around visor at the sides and a white slash decal on the back
        scr.pen = P.eye
        _r(hx - 9.4, hy - 2.5, 3, 4.5)
        _r(hx + 6.4, hy - 2.5, 3, 4.5)
        scr.pen = P.acc
        _q(hx - 6.5, hy + 6, hx - 2.5, hy + 7.2, hx + 6.5, hy - 6, hx + 2.5, hy - 7.2)
        if hi:
            scr.pen = P.fx
            if sp < 0.5:
                _r(hx - 9.4, hy - 0.8, 3, 1)
            else:
                _r(hx + 6.4, hy - 0.8, 3, 1)
            scr.pen = P.drv_l
            _r(hx - 5, hy - 8, 4, 1.4)
    elif face == 1:
        scr.pen = P.eye
        _r(hx - 1, hy - 4, 10.5, 6.5)
        scr.pen = P.acc
        _q(hx + 1, hy + 2.5, hx + 3.5, hy + 2.5, hx + 7, hy - 4, hx + 4.5, hy - 4)
        scr.pen = P.fx
        _r(hx - 1 + sp * 9.5, hy - 4, 1, 6.5)
    else:
        scr.pen = P.eye
        _r(hx - 7.5, hy - 4.5, 15, 7.5)
        scr.pen = P.acc
        _q(hx - 4, hy + 3, hx - 0.5, hy + 3, hx + 4, hy - 4.5, hx + 0.5, hy - 4.5)
        scr.pen = P.fx
        _r(hx - 7.5 + sp * 14, hy - 4.5, 1, 7.5)
        if hi:
            scr.pen = P.drv_l
            _r(hx - 4, hy + 4.5, 8, 1.5)


def _head_mona(P, K, face, hx, hy, now, hi):
    scr = screen
    # an ear twitches now and then (left, then right a beat later)
    ph = now % 2900
    tl = 1.0 if ph < 170 else 0.0
    tr = 1.0 if 420 < ph < 560 else 0.0
    if face == 1:
        # side view, facing +x: far ear behind, near ear, face mask on the right
        scr.pen = P.drv_d
        _t(hx - 9, hy - 3, hx - 3, hy - 9, hx - 8 - tl, hy - 13 + tl * 2)
        if hi:
            scr.pen = P.out
            _c(hx, hy, 10.9)
        scr.pen = P.drv
        _t(hx - 2.5, hy - 9, hx + 6.5, hy - 6.5, hx + 1 + tr, hy - 13.5 + tr * 2)
        _c(hx, hy, 10)
        scr.pen = P.acc
        _c(hx + 5, hy + 2.2, 5.2)
        blink = (now % 3000) < 130
        scr.pen = P.eye
        _r(hx + 5.8, hy - 1, 1.8, 0.8 if blink else 3.4)
        _r(hx + 9, hy + 2.8, 1.2, 1.1)
        if hi:
            scr.pen = P.out
            _l(hx + 7.5, hy + 5, hx + 14, hy + 4.5)
        return
    # broad, short ears: (outer base, inner base, tip)
    lx1 = hx - 10.8
    lx2 = hx - 2.5
    ltx = hx - 9.6 - tl * 1.6
    lty = hy - 13 + tl * 2.6
    rx1 = hx + 10.8
    rx2 = hx + 2.5
    rtx = hx + 9.6 + tr * 1.6
    rty = hy - 13 + tr * 2.6
    if not _LO:
        scr.pen = P.out
        _c(hx, hy, 11)
    scr.pen = P.drv
    _t(lx1, hy - 1, lx2, hy - 9.5, ltx, lty)
    _t(rx1, hy - 1, rx2, hy - 9.5, rtx, rty)
    _c(hx, hy, 10)
    if face == 0 and not _BOOST:
        return
    # front, or looking back over her shoulder while boosting
    if face == 2 and hi:
        scr.pen = P.ear
        _t(lx1 + 2.8, hy - 3.5, lx2 - 1.5, hy - 8.5, ltx + 0.6, lty + 3.2)
        _t(rx1 - 2.8, hy - 3.5, rx2 + 1.5, hy - 8.5, rtx - 0.6, rty + 3.2)
    # wide cream face with round cheeks; the navy brow stays above it
    scr.pen = P.acc
    _r(hx - 6, hy - 3, 12, 9)
    _c(hx - 5.5, hy + 1.6, 4.5)
    _c(hx + 5.5, hy + 1.6, 4.5)
    scr.pen = P.eye
    if _BOOST and face == 0:
        # happy squint: ^ ^
        for ex in (-3.8, 3.8):
            _l(hx + ex - 2.2, hy + 1.4, hx + ex, hy - 1)
            _l(hx + ex, hy - 1, hx + ex + 2.2, hy + 1.4)
        return
    blink = (now % 3000) < 130
    _r(hx - 5, hy - 1.8, 2.4, 0.8 if blink else 3.8)
    _r(hx + 2.6, hy - 1.8, 2.4, 0.8 if blink else 3.8)
    _r(hx - 0.8, hy + 2.6, 1.6, 1.1)
    if hi:
        scr.pen = K["blush_a"]
        _c(hx - 6.6, hy + 3.6, 1.7)
        _c(hx + 6.6, hy + 3.6, 1.7)
        scr.pen = P.out
        _l(hx - 9.5, hy + 2.5, hx - 15.5, hy + 1.5)
        _l(hx + 9.5, hy + 2.5, hx + 15.5, hy + 1.5)


# --- spin views -----------------------------------------------------------------

def _kart_spin(P, K, cx, by, s, now, hi):
    view, d = _SPIN[(now // 110) % 8]
    if _LO and (view & 1):
        view = 2      # far away: rear, side, front, side is enough
    st = P.style
    hy = _BODY[st][3]
    if view == 0:
        _kart_rear(P, K, cx, by, s, 0, now, False, hi, True, False)
    elif view == 2:
        _frame(cx, by, s * d, s)
        _kart_side(P, K, now, hi, True)
    elif view == 4:
        _frame(cx, by, s, s)
        _kart_front(P, K, now, hi, True)
    elif view == 1:
        # rear 3/4: the side recedes, the rear face is nearest
        _frame(cx + d * 9 * s, by - s, s * d * 0.6, s)
        _kart_side(P, K, now, False, False)
        _frame(cx, by, s, s)
        _head(P, K, 0, -d * 3, hy, now, 0, hi)
        _kart_rear(P, K, cx - d * 12 * s, by, s * 0.62, 0, now, False, False, False, False)
    else:
        # front 3/4: the nose is nearest
        _frame(cx - d * 9 * s, by - s, s * d * 0.6, s)
        _kart_side(P, K, now, False, False)
        _frame(cx, by, s, s)
        _head(P, K, 2, d * 3, hy, now, 0, hi)
        _frame(cx + d * 12 * s, by, s * 0.62, s)
        _kart_front(P, K, now, False, False)
    # dizzy stars circling over the driver
    _frame(cx, by, s, s)
    scr = screen
    for i in range(3):
        ang = now * 0.012 + i * 2.094
        x = _cos(ang) * 12
        y = hy - 15 + _sin(ang) * 3
        scr.pen = K["star"] if _sin(ang) > -0.3 else K["star_d"]
        _r(x - 3.5, y - 0.8, 7, 1.6)
        _r(x - 0.8, y - 3.5, 1.6, 7)


def _wheel_side(K, x, y, r, now, hi):
    scr = screen
    scr.pen = K["tyre"]
    _c(x, y, r)
    scr.pen = K["hub"]
    _c(x, y, r * 0.45)
    if hi:
        ang = now * 0.03
        dx = _cos(ang) * (r - 1)
        dy = _sin(ang) * (r - 1)
        scr.pen = K["tread"]
        _l(x - dx, y - dy, x + dx, y + dy)
        _l(x + dy, y - dx, x - dy, y + dx)
        scr.pen = K["pipe_in"]
        _c(x, y, r * 0.18)


def _kart_side(P, K, now, hi, driver):
    # facing +x in the current frame (mirror the frame to face left)
    scr = screen
    st = P.style
    if driver:
        scr.pen = K["shadow"]
        _r(-30, -2.5, 62, 4.5)
    # wing or tail at the back
    if st == 2:
        flap = _sin(now * 0.011) * 3
        scr.pen = P.drv_d
        _t(-18, -20, -30, -30 + flap, -24, -18)
        _t(-30, -30 + flap, -34, -26 + flap, -26, -25 + flap)
    elif st == 4:
        # tentacles streaming out behind, curling up at the tips
        w = _sin(now * 0.012)
        scr.pen = P.drv_d
        _tent(-12, -21, -24, -26 + w * 2, -27, -33 + w * 3, 2.6, 1.9, hi)
        scr.pen = P.drv
        _tent(-10, -19, -23, -18 - w * 1.5, -30, -22 - w * 2.5, 2.8, 2.0, True)
    else:
        scr.pen = P.deep
        _r(-27, -27, 3, 10)
        scr.pen = P.acc if st != 1 else P.body
        _r(-33, -30, 13, 4)
    scr.pen = P.body
    _r(-25, -18, 42, 11)
    _t(16, -18, 33, -8, 16, -7)
    scr.pen = P.dark
    _r(-25, -10, 56, 3)
    if hi:
        scr.pen = P.trim
        _r(-20, -16, 30, 1.6)
    scr.pen = K["pipe"]
    _r(-31, -10, 6, 2.5)
    if driver:
        hy = _BODY[st][3]
        scr.pen = P.deep
        _r(-11, -22, 14, 5)
        _head(P, K, 1, -4, hy + 1, now, 0, hi)
        scr.pen = K["tyre"]
        _r(4, -23, 2, 6)
    _wheel_side(K, -17, -7.5, 7.5, now, hi)
    _wheel_side(K, 19, -6.5, 6.5, now, hi)


def _kart_front(P, K, now, hi, driver):
    scr = screen
    st = P.style
    top = _BODY[st][1]
    if driver:
        scr.pen = K["shadow"]
        _r(-31, -2.5, 62, 4.5)
    scr.pen = K["tyre"]
    _r(-30, -24, 9, 11)
    _r(21, -24, 9, 11)
    if driver:
        if st != 2:
            scr.pen = P.dark
            _r(-24, top - 10, 48, 4)
        _head(P, K, 2, 0, _BODY[st][3] + 1, now, 0, hi)
    scr.pen = P.body
    _r(-16, -21, 32, 13)
    _t(-16, -21, -21, -9, -16, -9)
    _t(16, -21, 21, -9, 16, -9)
    scr.pen = P.light
    _r(-15, -21, 30, 2)
    scr.pen = K["lamp"]
    _c(-10, -15, 2.2)
    _c(10, -15, 2.2)
    scr.pen = P.acc
    _r(-20, -9, 40, 3)
    scr.pen = P.dark
    _r(-29, -6, 58, 3)
    if st == 4 and driver:
        # front tentacles curl over the cowl and grip the nose
        w = _sin(now * 0.0065)
        scr.pen = P.drv
        _tent(-6, top - 5, -12, top + 1, -13 - w, top + 6, 2.6, 1.9, hi)
        _tent(6, top - 5, 12, top + 1, 13 + w, top + 6, 2.6, 1.9, hi)
    scr.pen = K["tyre"]
    _r(-31, -14, 10, 14)
    _r(21, -14, 10, 14)
    if hi:
        scr.pen = K["tread"]
        y = -2.5 - ((now * 0.11) % 11.0)
        _r(-30, y, 8, 1.6)
        _r(22, y, 8, 1.6)


# --- scenery --------------------------------------------------------------------

def draw_scenery(kind, cx, bottom_y, scale):
    if scale <= 0:
        return
    K = _colours()
    scr = screen
    small = scale < 0.3
    detail = scale >= 0.55
    _frame(cx, bottom_y, scale, scale)

    if kind == "pine":
        scr.pen = K["trunk"]
        _r(-5, -16, 10, 16)
        scr.pen = K["pine_d"]
        if small:
            _t(-30, -12, 30, -12, 0, -120)
            return
        _t(-36, -12, 36, -12, 0, -66)
        _t(-30, -42, 30, -42, 0, -94)
        _t(-22, -70, 22, -70, 0, -120)
        scr.pen = K["pine_l"]
        _t(0, -66, -36, -12, -2, -12)
        _t(0, -94, -30, -42, -2, -42)
        _t(0, -120, -22, -70, -2, -70)

    elif kind == "rack":
        scr.pen = K["rack_f"]
        _r(-24, -92, 48, 89)
        _r(-22, -3, 6, 3)
        _r(16, -3, 6, 3)
        scr.pen = K["rack_d"]
        _r(-21, -88, 42, 82)
        if small:
            scr.pen = K["led_g"]
            _r(-16, -80, 10, 4)
            _r(-16, -50, 10, 4)
            _r(-16, -24, 10, 4)
            return
        leds = (K["led_g"], K["led_a"], K["led_b"], K["led_g"])
        for i in range(6):
            y = -86 + i * 13.5
            scr.pen = K["unit"]
            _r(-19, y, 38, 11)
            scr.pen = leds[i % 4]
            _r(-16, y + 4, 3, 3)
            if detail:
                scr.pen = leds[(i + 1 + (i >> 1)) % 4]
                _r(-11, y + 4, 3, 3)
                scr.pen = K["rack_d"]
                _r(-4, y + 3, 19, 1.2)
                _r(-4, y + 6.5, 19, 1.2)

    elif kind == "lamp":
        if not small:
            scr.pen = K["glow"]
            _c(-16, -116, 22)
            _c(16, -116, 22)
            scr.pen = K["glow2"]
            _c(-16, -118, 11)
            _c(16, -118, 11)
        scr.pen = K["pole"]
        _r(-2.5, -128, 5, 128)
        _r(-7, -10, 14, 10)
        _r(-20, -129, 40, 3.5)
        scr.pen = K["lamp"]
        _r(-23, -126, 10, 4)
        _r(13, -126, 10, 4)

    elif kind == "bush":
        scr.pen = K["bush_d"]
        if small:
            _r(-22, -14, 44, 14)
            _c(0, -16, 13)
            return
        _r(-22, -11, 44, 11)
        _c(-13, -11, 11)
        _c(13, -11, 11)
        _c(0, -18, 13)
        scr.pen = K["bush_l"]
        _c(-5, -22, 6)
        _c(-15, -14, 5)
        if detail:
            scr.pen = K["flower"]
            _c(8, -20, 2)
            _c(16, -11, 2)
            _c(-9, -8, 2)

    elif kind == "sign_l" or kind == "sign_r":
        if kind == "sign_l":
            _frame(cx, bottom_y, -scale, scale)
        scr.pen = K["post"]
        _r(-15, -26, 3, 26)
        _r(12, -26, 3, 26)
        if small:
            scr.pen = K["sign_r"]
            _r(-22, -50, 44, 26)
            scr.pen = K["white"]
            _t(-12, -47, 13, -37, -12, -27)
            return
        scr.pen = K["white"]
        _r(-23, -51, 46, 28)
        scr.pen = K["sign_r"]
        _r(-21, -49, 42, 24)
        for x0 in (-15, 1):
            scr.pen = K["white"]
            _t(x0, -46, x0 + 12, -37, x0, -28)
            scr.pen = K["sign_r"]
            _t(x0, -40.5, x0 + 5, -37, x0, -33.5)

    elif kind == "banner":
        _banner(K, cx, bottom_y, scale)


def _banner(K, cx, by, s):
    scr = screen
    # posts sit just outside the road edge (road half width = 160 * s)
    scr.pen = K["arch"]
    _r(-182, -156, 13, 156)
    _r(169, -156, 13, 156)
    scr.pen = K["arch_l"]
    _r(-182, -156, 4, 156)
    _r(169, -156, 4, 156)
    # band: checker rows top and bottom, dark title strip in the middle
    scr.pen = K["white"]
    _r(-182, -156, 364, 36)
    scr.pen = K["arch"]
    _r(-182, -148, 364, 20)
    if s >= 0.3:
        scr.pen = K["black"]
        n = 26
        q = 364.0 / n
        for i in range(n):
            x = -182 + i * q
            if i & 1:
                _r(x, -156, q, 4)
            else:
                _r(x, -124, q, 4)
            if s >= 0.7:
                if i & 1:
                    _r(x, -128, q, 4)
                else:
                    _r(x, -152, q, 4)
    if s >= 0.45:
        use_font(FONT_TITLE)
        w, h = screen.measure_text("MERGE BAY")
        if h > 22 * s or w > 300 * s:
            use_font(FONT_HUD)
            w, h = screen.measure_text("MERGE BAY")
        ty = by - 138 * s - h / 2
        scr.pen = K["gold"]
        scr.text("MERGE BAY", cx - w / 2, ty)


# --- pickups --------------------------------------------------------------------

def draw_pickup(kind, cx, bottom_y, scale, now):
    if scale <= 0:
        return
    K = _colours()
    scr = screen
    s = scale
    if kind == "token":
        bob = _sin(now * 0.005) * 3
        cy = -19 - bob
        if s < 0.3:
            _frame(cx, bottom_y, s, s)
            scr.pen = K["gold"]
            _c(0, cy, 11)
            return
        _frame(cx, bottom_y, s, s)
        scr.pen = K["shadow"]
        _r(-8 + bob * 0.5, -2, 16 - bob, 3)
        scr.pen = K["tglow"]
        _c(0, cy, 16)
        # coin spin: squash the hexagon horizontally
        sq = _cos(now * 0.004)
        if sq < 0:
            sq = -sq
        sq = 0.35 + 0.65 * sq
        _frame(cx, bottom_y, s * sq, s)
        scr.pen = K["gold_d"]
        _hex(cy, 12)
        scr.pen = K["gold"]
        _hex(cy, 9.5)
        scr.pen = K["gold_l"]
        _r(-4.5, cy - 7.5, 3, 4)
        if s >= 0.65 and sq > 0.6:
            use_font(FONT_HUD)
            w, h = _glyph_size("{}")
            scr.pen = K["gold_d"]
            scr.text("{}", cx - w / 2, bottom_y + cy * s - h / 2)
        else:
            scr.pen = K["gold_d"]
            _r(-4, cy - 5, 8, 2.2)
            _r(-1.1, cy - 5, 2.2, 10)

    elif kind == "bug":
        _frame(cx, bottom_y, s, s)
        wig = _sin(now * 0.02)
        if s < 0.3:
            scr.pen = K["bug"]
            _c(0, -10, 10)
            scr.pen = K["bug_d"]
            _c(0, -19, 5)
            return
        scr.pen = K["bglow"]
        _c(0, -11, 15)
        scr.pen = K["bug_leg"]
        w = wig * 2
        for i in range(3):
            y = -15 + i * 5
            d = w if i != 1 else -w
            _r(-14, y + d, 7, 1.8)
            _r(7, y - d, 7, 1.8)
            _r(-15, y + d, 2, 4)
            _r(13, y - d, 2, 4)
        scr.pen = K["bug"]
        _c(0, -10, 10)
        scr.pen = K["bug_d"]
        _r(-0.8, -19, 1.6, 18)
        _c(0, -20, 5.5)
        scr.pen = K["bug_l"]
        _c(-4.5, -9, 2.4)
        _c(5, -6, 1.8)
        _c(4, -13, 1.6)
        scr.pen = K["white"]
        _c(-2.4, -22, 1.3)
        _c(2.4, -22, 1.3)
        scr.pen = K["bug_leg"]
        _l(-2, -25, -6 + wig * 2, -30)
        _l(2, -25, 6 + wig * 2, -30)


def _hex(cy, r):
    h = r * 0.866
    hr = r * 0.5
    _r(-hr, cy - h, r, h * 2)
    _t(-hr, cy - h, -r, cy, -hr, cy + h)
    _t(hr, cy - h, r, cy, hr, cy + h)


# --- portraits ------------------------------------------------------------------
# Local units: a 110 wide disc (radius 55) centred on (cx, cy); everything the
# character draws stays inside radius ~52. Below size 48 a cheap chip is drawn.


def draw_portrait(character, cx, cy, size, now):
    if size <= 0:
        return
    P = _palette(character)
    K = _colours()
    scr = screen
    st = P.style
    k = size / 110.0
    _frame(cx, cy, k, k)

    scr.pen = P.ring
    _c(0, 0, 55)
    scr.pen = P.disc
    _c(0, 0, 51)
    if size < 48:
        _chip(P, K, now)
        return
    scr.pen = P.glow
    _c(0, 6, 42)
    # highlight sweep: a little comet of light running round the rim
    ang = now * 0.0021
    sh = K["sheen"]
    for i in range(3):
        a = ang - i * 0.13
        scr.pen = sh[i]
        _c(_cos(a) * 53, _sin(a) * 53, 2.6 - i * 0.6)

    bob = _sin(now * 0.003) * 3
    _frame(cx, cy + bob * k, k, k)
    if st == 0:
        _portrait_claude(P, K, now)
    elif st == 1:
        _portrait_copilot(P, K, now)
    elif st == 2:
        _portrait_whale(P, K, now)
    elif st == 3:
        _portrait_grok(P, K, now)
    else:
        _portrait_mona(P, K, now)


def _chip(P, K, now):
    # tiny face for list rows (results); 4-7 prims, inside radius 50
    scr = screen
    st = P.style
    blink = (now % 3100) < 130
    if st == 0:
        scr.pen = P.drv
        _r(-26, -20, 52, 38)
        _r(-38, -4, 12, 10)
        _r(26, -4, 12, 10)
        scr.pen = P.eye
        _r(-14, -10, 8, 3 if blink else 14)
        _r(6, -10, 8, 3 if blink else 14)
    elif st == 1:
        scr.pen = P.drv
        _c(0, 2, 36)
        scr.pen = P.body
        _r(-36, -8, 72, 10)
        _c(-14, -3, 14)
        _c(14, -3, 14)
        scr.pen = P.eye
        _c(-14, -3, 9)
        _c(14, -3, 9)
    elif st == 2:
        scr.pen = P.acc
        _c(0, 8, 36)
        scr.pen = P.drv
        _c(0, 0, 36)
        scr.pen = P.eye
        _c(-14, 2, 5)
        _c(14, 2, 5)
    elif st == 3:
        scr.pen = P.drv
        _c(0, 0, 38)
        scr.pen = P.eye
        _r(-30, -14, 60, 24)
        scr.pen = P.acc
        _q(-18, 18, -6, 18, 18, -22, 6, -22)
    else:
        # Octocat: navy head with ears, wide cream face, two dark eyes
        scr.pen = P.drv
        _c(0, 2, 34)
        _t(-36, -4, -12, -28, -34, -46)
        _t(36, -4, 12, -28, 34, -46)
        scr.pen = P.acc
        _r(-28, -6, 56, 28)
        scr.pen = P.eye
        _r(-15, -2, 7, 3 if blink else 13)
        _r(8, -2, 7, 3 if blink else 13)


def _sparkle(x, y, r, rot, w):
    # 8-point burst from thin triangles (each spike = one triangle)
    for i in range(8):
        a = rot + i * 0.7854
        ln = r if i & 1 == 0 else r * 0.62
        ca = _cos(a)
        sa = _sin(a)
        _t(x + ca * ln, y + sa * ln, x - sa * w, y + ca * w, x + sa * w, y - ca * w)


def _portrait_claude(P, K, now):
    scr = screen
    # cream sparkle turning and twinkling behind the critter
    tw = 1.0 + 0.18 * _sin(now * 0.007)
    scr.pen = P.acc
    _sparkle(27, -30, 14 * tw, now * 0.0012, 2.4)
    _c(27, -30, 3)
    # small sparkle twin
    scr.pen = P.acc_d
    _sparkle(-33, -24, 6 * (2.2 - tw), -now * 0.002, 1.3)

    # legs (a little trot)
    tr = 2 if (now // 260) % 2 else 0
    for i, lx in enumerate((-22, -10, 4, 16)):
        ln = 11 + (tr if i & 1 else 2 - tr)
        scr.pen = P.out
        _r(lx - 1, 20, 9, ln + 1)
        scr.pen = P.drv_d
        _r(lx, 20, 7, ln)
    # arms: left grips, right waves
    wave = _sin(now * 0.008)
    scr.pen = P.out
    _r(-41, -3, 14, 12)
    _r(27, -6 - wave * 7, 14, 12)
    scr.pen = P.drv_d
    _r(-39, -1, 11, 8)
    _r(29, -4 - wave * 7, 11, 8)
    # body: outline, fill, top light, lower shade
    scr.pen = P.out
    _r(-30, -24, 60, 47)
    scr.pen = P.drv
    _r(-28, -22, 56, 43)
    scr.pen = P.drv_l
    _r(-28, -22, 56, 5)
    _r(-28, -17, 4, 30)
    scr.pen = P.drv_d
    _r(-24, 15, 52, 6)
    # eyes: dark blocks with a pixel of shine; blink and glance
    blink = (now % 3200) < 140
    look = 2 if (now % 5000) > 3600 else 0
    scr.pen = P.eye
    if blink:
        _r(-15 + look, -2, 9, 3)
        _r(6 + look, -2, 9, 3)
    else:
        _r(-14 + look, -11, 8, 15)
        _r(6 + look, -11, 8, 15)
        scr.pen = K["white"]
        _r(-13 + look, -10, 2.5, 3)
        _r(7 + look, -10, 2.5, 3)


def _portrait_copilot(P, K, now):
    scr = screen
    # suit
    scr.pen = P.out
    _r(-23, 30, 46, 16)
    scr.pen = P.body
    _r(-20, 32, 40, 14)
    scr.pen = P.acc
    _r(-6, 32, 12, 5)
    # ear pods
    scr.pen = P.body
    _c(-36, 2, 9)
    _c(36, 2, 9)
    scr.pen = P.drv_d
    _c(-37, 2, 4.5)
    _c(37, 2, 4.5)
    # antenna with a blinking tip
    scr.pen = P.out
    _r(-1.5, -46, 3, 12)
    scr.pen = P.fx if (now // 500) % 2 else P.acc
    _c(0, -46, 4)
    # head
    scr.pen = P.out
    _c(0, 0, 36)
    scr.pen = P.drv
    _c(0, 0, 33.5)
    scr.pen = K["shine"]
    _c(-15, -18, 6)
    # visor brim over the goggles
    scr.pen = P.body
    _r(-25, -24, 50, 6)
    _t(-25, -24, -31, -16, -25, -18)
    _t(25, -24, 31, -16, 25, -18)
    # strap and aviator goggles
    scr.pen = P.out
    _r(-34, -8, 68, 7)
    blink = (now % 3300) < 160
    gs = (now % 2600) / 2600.0
    for gx in (-14.5, 14.5):
        scr.pen = P.body
        _c(gx, -4, 14)
        scr.pen = P.eye
        _c(gx, -4, 10.5)
        scr.pen = P.fx
        if blink:
            _r(gx - 5, -4, 10, 2)
        else:
            _r(gx - 2.5, -10, 5, 10)
        if gs < 0.35:
            # glint sweeping across the lens
            g = gx - 9 + gs / 0.35 * 15
            scr.pen = K["white"]
            _t(g, -12, g + 3, -12, g - 2, 3)
    scr.pen = P.body
    _r(-2.5, -6, 5, 4)
    # smile
    scr.pen = P.out
    _r(-6, 14, 12, 2.5)
    _r(-8, 12, 3, 2.5)
    _r(5, 12, 3, 2.5)


def _portrait_whale(P, K, now):
    scr = screen
    # bubbles rising
    for i in range(2):
        f = ((now + i * 900) % 1800) / 1800.0
        scr.pen = K["sheen"][2]
        _c(-34 + i * 12 + _sin(f * 9) * 2, 36 - f * 28, 2 + i)
    # tail: the same "Y" as on the kart, drawn bigger and flapping
    k = _S
    X = _X
    Y = _Y
    _frame(X + 15 * k, Y + 2 * k, 1.8 * k, 1.8 * k)
    _ytail(P, 0, 0, _sin(now * 0.006) * 2.2)
    _frame(X, Y, k, k)
    # body: outline, white belly crescent, blue body on top
    scr.pen = P.out
    _c(-10, 7, 30)
    scr.pen = P.acc
    _c(-10, 8, 28)
    scr.pen = P.drv
    _c(-10, 2, 27)
    scr.pen = P.drv_l
    _c(-19, -10, 6.5)
    # belly grooves
    scr.pen = P.drv_d
    _r(-24, 26, 2, 4)
    _r(-14, 28, 2, 5)
    _r(-4, 27, 2, 4)
    # eye, smile and blush
    blink = (now % 2900) < 150
    scr.pen = P.eye
    if blink:
        _r(-27, 4, 9, 2)
    else:
        _c(-23, 4, 4.5)
        scr.pen = K["white"]
        _c(-24.5, 2.5, 1.6)
    scr.pen = P.eye
    _r(-35, 15, 11, 2)
    _r(-37, 13, 3, 2)
    scr.pen = K["blush_a"]
    _c(-13, 12, 4)
    # spout puffing every ~2 s from the blowhole
    ph = now % 2000
    if ph < 900:
        f = ph / 900.0
        h = 16 * _sin(f * 3.1416)
        scr.pen = P.fx
        _r(-12.5, -24 - h, 5, h + 1)
        scr.pen = P.fx2
        d = 4 + f * 10
        yy = -26 - h + f * 8
        _c(-10 - d, yy, 3)
        _c(-10 + d, yy, 3)
        _c(-10, -27 - h, 3.5)


def _portrait_grok(P, K, now):
    scr = screen
    # shoulders with a white collar edge
    scr.pen = P.out
    _r(-24, 30, 48, 16)
    scr.pen = P.body
    _r(-22, 32, 44, 14)
    scr.pen = P.acc
    _t(-14, 32, -4, 32, -9, 40)
    # helmet: neon rim, shell, sheen
    scr.pen = P.out
    _c(0, -2, 37)
    scr.pen = P.drv
    _c(0, -2, 35)
    scr.pen = P.drv_l
    _c(-16, -22, 6)
    scr.pen = P.drv_d
    _r(-24, 18, 48, 10)
    # visor
    scr.pen = P.eye
    _r(-24, -14, 48, 22)
    _c(-24, -3, 11)
    _c(24, -3, 11)
    # bold diagonal slash across the visor
    scr.pen = P.acc
    _q(-15, 14, -5, 14, 15, -20, 5, -20)
    # scanning light line sweeping across the visor
    sp = (now % 1800) / 1800.0
    sx = -32 + sp * 64
    scr.pen = K["scan_a"]
    _r(sx - 3, -13, 7, 20)
    scr.pen = P.fx
    _r(sx, -13, 1.5, 20)
    # slash glint
    g = (now % 2600) / 500.0
    if g < 1:
        scr.pen = P.fx
        gx = -11 + g * 22
        gy = 12 - g * 30
        _t(gx - 2, gy + 2, gx + 3, gy + 2, gx + 4, gy - 4)
    # chin vents
    scr.pen = P.light
    _r(-8, 21, 4, 4)
    _r(-2, 21, 4, 4)
    _r(4, 21, 4, 4)


def _portrait_mona(P, K, now):
    scr = screen
    # gold sparkles twinkling around her, like the badge's own Mona
    for i in range(2):
        f = ((now + i * 700) % 1400) / 1400.0
        r = 5.5 * _sin(f * 3.1416)
        if r > 0.8:
            x = (-41, 40)[i]
            y = (-30, -12)[i]
            scr.pen = P.fx
            _r(x - r, y - 0.9, r * 2, 1.8)
            _r(x - 0.9, y - r, 1.8, r * 2)
    # four tentacles under the head, each waving on its own phase and ending
    # in a curl: the outer pair sweeps out and curls up, the inner pair hangs
    t = now * 0.005
    for side in (-1, 1):
        for inner in (0, 1):
            w = _sin(t + side * 1.3 + inner * 2.2)
            if inner:
                x0, y0 = side * 6, 14
                x1, y1 = side * 9 + w * 2, 29
                x2, y2 = side * 14 + w * 3, 37
                cx, cy, cr = side * 17.5 + w * 3, 34.5, 3.9
            else:
                x0, y0 = side * 13, 10
                x1, y1 = side * 25, 21 + w * 1.5
                x2, y2 = side * 34, 22 + w * 2.5
                cx, cy, cr = side * 37, 18.5 + w * 2.5, 4.2
            scr.pen = P.drv
            _tent3(x0, y0, x1, y1, x2, y2, None, None, 6, 4.8, 3.6)
            _c(cx, cy, cr)
            scr.pen = P.hole
            _c(cx, cy, cr * 0.4)
    scr.pen = P.drv
    _c(0, 10, 15)
    # the head is drawn a little smaller and higher so the tentacles show
    X = _X
    Y = _Y
    k = _S
    _frame(X, Y - 5 * k, 0.9 * k, 0.9 * k)
    # ears (outline, fill, pink inside); the left one twitches now and then
    ph = now % 3100
    tl = 1.0 if ph < 200 else 0.0
    tr = 1.0 if 520 < ph < 700 else 0.0
    ears = ((-34, -10, -11, -33, -31 - tl * 4, -47 + tl * 6),
            (34, -10, 11, -33, 31 + tr * 4, -47 + tr * 6))
    scr.pen = P.out
    _c(0, -6, 35)
    for e in ears:
        _tg(e[0], e[1], e[2], e[3], e[4], e[5], 1.12)
    scr.pen = P.drv
    for e in ears:
        _t(e[0], e[1], e[2], e[3], e[4], e[5])
    _c(0, -6, 32.5)
    scr.pen = P.ear
    for e in ears:
        _t(e[0] * 0.82, e[1] - 6, e[2] * 1.2, e[3] + 2, e[4] * 0.92, e[5] + 8)
    # wide cream face with a little blush
    scr.pen = P.acc
    _r(-19, -17, 38, 30)
    _c(-17, -1, 14)
    _c(17, -1, 14)
    scr.pen = K["blush_a"]
    _c(-21, 6, 5)
    _c(21, 6, 5)
    # whiskers
    scr.pen = P.whisk
    _l(-30, 1, -46, -2)
    _l(-30, 6, -45, 8)
    _l(30, 1, 46, -2)
    _l(30, 6, 45, 8)
    # eyes: tall dark ovals with a shine; blink, and glance now and then
    blink = (now % 3300) < 150
    look = 2.5 if (now % 5200) > 3900 else 0.0
    scr.pen = P.eye
    for ex in (-10, 10):
        x = ex + look
        if blink:
            _r(x - 4.5, -2, 9, 2.5)
        else:
            _r(x - 4, -8, 8, 10)
            _c(x, -8, 4)
            _c(x, 2, 4)
    if not blink:
        scr.pen = K["white"]
        _c(-11.5 + look, -8, 1.6)
        _c(8.5 + look, -8, 1.6)
    # small nose and smile
    scr.pen = P.drv_d
    _r(-2, 5, 4, 2.5)
    _r(-4, 9, 3, 1.5)
    _r(1, 9, 3, 1.5)
    _frame(X, Y, k, k)
