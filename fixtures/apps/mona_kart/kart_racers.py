# Mona Kart: characters, kart physics, AI drivers and race bookkeeping.
# Pure logic, no drawing and no badge globals: time comes in via `now` (ms ticks).

from array import array
import random

from kart_config import (
    SEGMENT_LENGTH, LAPS, MAX_SPEED, OFFROAD_LIMIT, CENTRIFUGAL, BOOST_MS,
    PAD_BOOST_MS, BOOST_FACTOR, SPIN_MS, MAX_TOKENS, KART_LENGTH, KART_HALF_WIDTH,
)

try:
    _rand = random.random
except AttributeError:  # minimal MicroPython ports only have getrandbits
    def _rand():
        return random.getrandbits(24) / 16777216.0


# Stylised fan drivers (simple shapes, not official logos). The first four stat
# lines are the v1 balanced sets, so race balance is unchanged:
#   Claude Code = grip all-rounder, Copilot = balanced, DeepSeek = light and quick
#   off the line, Grok = heavy with the highest top speed, Mona (the Octocat,
#   appended last so old saves keep their driver) = light all-rounder.
# "short" (<= 8 chars) is for tight HUD spots: c.get("short", c["name"]).
CHARACTERS = (
    {"name": "Claude Code", "short": "CLAUDE", "kind": "Harness",
     "blurb": "Agentic apex hunter",
     "body": (217, 119, 87), "accent": (250, 240, 230), "glyph": "*",
     "top": 0.985, "accel": 0.7, "handling": 1.0, "weight": 0.7},
    {"name": "GitHub Copilot", "short": "COPILOT", "kind": "Harness",
     "blurb": "Pair-programs the apex",
     "body": (110, 64, 255), "accent": (230, 225, 255), "glyph": "oo",
     "top": 0.98, "accel": 0.75, "handling": 0.8, "weight": 0.75},
    {"name": "DeepSeek", "short": "DEEPSEEK", "kind": "LLM",
     "blurb": "Dives deep, rises fast",
     "body": (77, 107, 254), "accent": (225, 235, 255), "glyph": "~",
     "top": 0.965, "accel": 1.0, "handling": 0.9, "weight": 0.5},
    {"name": "Grok", "short": "GROK", "kind": "LLM",
     "blurb": "Fast, blunt, unfiltered",
     "body": (24, 24, 28), "accent": (240, 240, 240), "glyph": "/",
     "top": 1.0, "accel": 0.5, "handling": 0.65, "weight": 1.0},
    {"name": "Mona", "short": "MONA", "kind": "Mascot",
     "blurb": "Home-turf hero",
     "body": (45, 164, 78), "accent": (255, 248, 242), "glyph": ":3",
     "top": 0.975, "accel": 0.85, "handling": 0.85, "weight": 0.6},
)
MONA = 4  # index of the mascot: always a rival in solo unless you drive her

# --- tuning (local to this module) --------------------------------------------
_STEER_RATE = 2.0           # x per second at full lock, full speed, handling 1.0
_CURVE_PUSH = 0.42          # scales CENTRIFUGAL into x per second
_CORNER_SCRUB = 0.025       # top-speed loss per unit |curve| per missing handling
_BRAKE = 1.8 * MAX_SPEED    # deceleration while braking (units/s^2)
_SPIN_DECAY = 1.6           # speed fraction lost per second while spinning
_BUG_SLOW = 0.45
_RESPAWN_MS = 4000
_BUMP_DECAY = 3.0
_X_LIMIT = 2.2
_HALF_SEG = SEGMENT_LENGTH * 0.5
_BAND_GAP = 30 * SEGMENT_LENGTH  # rubber-band ramps in over this gap
_AI_SKILLS = (0.95, 0.92, 0.94)  # per AI grid slot (front-left, front-right, back-right)


def _clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _pick_rivals(player_character, count):
    # Solo grid: `count` AI drivers from everyone but the player, in random order.
    # Mona always lines up when the player picked someone else; the other seats
    # are drawn at random, so each race mixes up the rivals.
    pool = [c for c in range(len(CHARACTERS)) if c != player_character]
    out = []
    if MONA in pool and count > 0:
        pool.remove(MONA)
        out.append(MONA)
    while len(out) < count and pool:
        out.append(pool.pop(int(_rand() * len(pool)) % len(pool)))
    # shuffle the grid slots (Fisher-Yates)
    for i in range(len(out) - 1, 0, -1):
        j = int(_rand() * (i + 1)) % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out


class Controls:
    def __init__(self):
        self.steer = 0       # -1, 0, +1
        self.brake = False
        self.boost = False   # edge-triggered: use a token this frame

    def clear(self):
        self.steer = 0
        self.brake = False
        self.boost = False


class Racer:
    def __init__(self, character, is_player):
        ch = CHARACTERS[character]
        self.character = character
        self.is_player = is_player
        self.z = 0.0
        self.x = 0.0
        self.speed = 0.0
        self.lap = 0
        self.total = 0.0
        self.place = 1
        self.tokens = 0
        self.boost_until = 0
        self.spin_until = 0
        self.steer = 0.0
        self.finished = False
        self.finish_ms = 0
        self.lap_ms = []
        self.bump = 0.0
        # cached stats
        self._top = ch["top"]
        self._accel_time = 2.5 + (1.0 - ch["accel"]) * 3.0  # seconds 0 -> top
        self._handling = ch["handling"]
        self._weight = ch["weight"]
        self._grip = 1.15 - 0.3 * ch["handling"]             # centrifugal multiplier
        self._scrub = _CORNER_SCRUB * (1.05 - ch["handling"])
        # race bookkeeping
        self._rel = 0.0          # distance past the start line, 0..length
        self._armed = False      # crossed the start line once (lap counting live)
        self._lap_start = 0      # elapsed ms when the current lap began
        self._on_pad = False
        self._top_mul = 1.0      # AI skill * rubber band
        # versus: a remote racer is driven by link messages (kart_versus), never
        # by physics or AI here
        self.remote = False
        # AI
        self.skill = 1.0
        self._look = 12 * SEGMENT_LENGTH
        self._greedy = False
        self._greedy_at = 0


class Race:
    def __init__(self, track, player_character, rival_character=None, ai=True, grid_side=-1):
        # ai=True: solo race, the player plus three AI rivals (Mona plus two
        # random others, or three random others when the player is Mona).
        # ai=False: versus, exactly two racers on the front row: the player on
        # `grid_side` (-1 left, +1 right) and a remote racer (`racers[1]`,
        # `.remote` True) on the other side, driven by kart_versus.
        self.track = track
        self.length = track.length
        self.elapsed = 0
        self.started = False
        self.finished = False
        self.events = []
        self.ai = ai
        self.rival = None
        self._side = -1.0 if grid_side < 0 else 1.0
        self._t0 = 0

        for p in track.pickups:
            p.respawn_at = 0

        length = self.length
        start = track.start_z
        front = (start - KART_LENGTH * 1.5) % length
        back = (start - KART_LENGTH * 3.5) % length

        self.player = Racer(player_character, True)
        self.racers = [self.player]
        if ai:
            # grid: player on row 2 left; AI on row 1 left/right and row 2 right
            slots = ((front, -0.35), (front, 0.35), (back, 0.35))
            slot = 0
            for c in _pick_rivals(player_character, len(slots)):
                r = Racer(c, False)
                r.z, r.x = slots[slot]
                r.skill = _AI_SKILLS[slot] + (_rand() - 0.5) * 0.02
                r._look = (6.0 + (r.skill - 0.9) * 75.0) * SEGMENT_LENGTH
                self.racers.append(r)
                slot += 1
            self.player.z = back
            self.player.x = -0.35
        else:
            side = -1.0 if grid_side < 0 else 1.0
            if rival_character is None:
                rival_character = player_character
            r = Racer(rival_character % len(CHARACTERS), False)
            r.remote = True
            r.z = front
            r.x = -0.35 * side
            self.rival = r
            self.racers.append(r)
            self.player.z = front
            self.player.x = 0.35 * side
        for r in self.racers:
            r._rel = (r.z - start) % length
            r.total = r._rel - length

        self._order = list(self.racers)
        self._build_line()
        self._place()

    # --- precomputed AI helpers ----------------------------------------------
    def _build_line(self):
        tr = self.track
        n = tr.count
        curve = tr.curve
        # racing line: inside of the upcoming curve, centre on straights
        raw = array("f", [0.0] * n)
        w0, w1 = 3, 22
        win = w1 - w0 + 1
        s = 0.0
        for k in range(w0, w1 + 1):
            s += curve[k % n]
        for i in range(n):
            raw[i] = _clamp(s / win * 0.14, -0.7, 0.7)
            s += curve[(i + w1 + 1) % n] - curve[(i + w0) % n]
        line = array("f", [0.0] * n)
        for i in range(n):
            a = 0.0
            for k in range(-3, 4):
                a += raw[(i + k) % n]
            line[i] = a / 7.0
        self._line = line
        # calm: no sharp curve in the next 15 segments (good place to boost)
        calm = bytearray(n)
        for i in range(n):
            ok = 1
            for k in range(15):
                c = curve[(i + k) % n]
                if c > 1.5 or c < -1.5:
                    ok = 0
                    break
            calm[i] = ok
        self._calm = calm

    # --- public API ------------------------------------------------------------
    def start(self, now):
        self.started = True
        self._t0 = now
        self.elapsed = 0

    def update(self, now, dt, controls):
        self.events.clear()
        if not self.started:
            return
        if not self.finished:
            self.elapsed = now - self._t0
        if dt <= 0:
            return
        player = self.player
        for r in self.racers:
            if r.remote:
                continue  # moved by kart_versus from link messages
            seg = self._seg(r.z)
            if r.is_player and not r.finished:
                self._physics(r, now, dt, controls.steer, controls.brake,
                              controls.boost, seg)
            else:
                if not r.is_player:
                    gap = r.total - player.total
                    if gap > 0:
                        band = 1.0 - 0.06 * _clamp(gap / _BAND_GAP, 0.0, 1.0)
                    else:
                        band = 1.0 + 0.04 * _clamp(-gap / _BAND_GAP, 0.0, 1.0)
                    r._top_mul = r.skill * band
                self._ai(r, now, dt, seg)
            self._advance(r, dt)
        self._collide()
        self._place()

    # --- internals ---------------------------------------------------------------
    def _seg(self, z):
        n = self.track.count
        i = int(z / SEGMENT_LENGTH)
        if i >= n:
            i -= n
        elif i < 0:
            i += n
        return i

    def _physics(self, r, now, dt, steer_in, brake, boost_in, seg):
        spinning = now < r.spin_until
        if boost_in and r.tokens > 0 and not spinning:
            r.tokens -= 1
            if r.boost_until < now + BOOST_MS:
                r.boost_until = now + BOOST_MS
            if r.is_player:
                self.events.append("boost")
        boosting = now < r.boost_until

        curve = self.track.curve_at(r.z)
        acurve = curve if curve >= 0 else -curve
        top = MAX_SPEED * r._top * r._top_mul
        target = top * (1.0 - r._scrub * acurve)
        if boosting:
            target = top * BOOST_FACTOR
        offroad = r.x > 1.0 or r.x < -1.0
        if offroad:
            target *= OFFROAD_LIMIT

        sp = r.speed
        if spinning:
            steer_in = 0
            sp -= sp * _SPIN_DECAY * dt
        elif brake:
            sp -= _BRAKE * dt
            if sp < 0:
                sp = 0.0
        elif sp < target:
            rate = top / r._accel_time
            if boosting:
                rate *= 3.0
            sp += rate * dt
            if sp > target:
                sp = target
        else:
            k = 2.5 if offroad else 1.5
            f = k * dt
            if f > 1.0:
                f = 1.0
            sp -= (sp - target) * f
        r.speed = sp

        ratio = sp / MAX_SPEED
        if sp > 1.0:
            r.x += _STEER_RATE * r._handling * steer_in * (0.3 + 0.7 * ratio) * dt
        r.x -= CENTRIFUGAL * _CURVE_PUSH * r._grip * curve * ratio * dt
        if r.x > _X_LIMIT:
            r.x = _X_LIMIT
        elif r.x < -_X_LIMIT:
            r.x = -_X_LIMIT

        f = 8.0 * dt
        if f > 1.0:
            f = 1.0
        r.steer += (steer_in - r.steer) * f
        if r.bump > 0:
            r.bump -= _BUMP_DECAY * dt
            if r.bump < 0:
                r.bump = 0.0

        # boost pads: once per pass, only on the tarmac
        on = self.track.pads[seg] and -1.05 <= r.x <= 1.05
        if on and not r._on_pad:
            if r.boost_until < now + PAD_BOOST_MS:
                r.boost_until = now + PAD_BOOST_MS
            if r.is_player:
                self.events.append("pad")
        r._on_pad = on

        # pickups
        length = self.length
        half = length * 0.5
        lim = KART_HALF_WIDTH + 0.12
        for p in self.track.pickups:
            if p.respawn_at and p.respawn_at > now:
                continue
            dz = p.index * SEGMENT_LENGTH + _HALF_SEG - r.z
            if dz > half:
                dz -= length
            elif dz < -half:
                dz += length
            if dz >= KART_LENGTH or dz <= -KART_LENGTH:
                continue
            dx = p.x - r.x
            if dx >= lim or dx <= -lim:
                continue
            if p.kind == "token":
                p.respawn_at = now + _RESPAWN_MS
                if r.tokens < MAX_TOKENS:
                    r.tokens += 1
                if r.is_player:
                    self.events.append("token")
            else:  # bug
                if now < r.spin_until:
                    continue
                p.respawn_at = now + _RESPAWN_MS
                if now < r.boost_until:
                    continue  # boosting smashes straight through
                r.spin_until = now + SPIN_MS
                r.speed *= _BUG_SLOW
                if r.is_player:
                    self.events.append("bug")

    def _advance(self, r, dt):
        length = self.length
        z = r.z + r.speed * dt
        if z >= length:
            z -= length
        r.z = z
        old = r._rel
        rel = (r.z - self.track.start_z) % length
        if rel < old - length * 0.5:
            self._crossed(r)
        r._rel = rel
        if r.finished:
            r.total = LAPS * length
        elif r._armed:
            r.total = r.lap * length + rel
        else:
            r.total = rel - length

    def _crossed(self, r):
        if r.finished:
            return
        if not r._armed:
            r._armed = True   # leaving the grid: lap 0 begins, nothing completed
            return
        t = self.elapsed
        r.lap_ms.append(t - r._lap_start)
        r._lap_start = t
        r.lap += 1
        if r.lap >= LAPS:
            r.lap = LAPS
            r.finished = True
            r.finish_ms = t if t > 0 else 1
            if r.is_player:
                self.events.append("finish")
                self.finished = True
                if self.ai:
                    self._estimate()  # versus: the rival reports its own time
        elif r.is_player:
            self.events.append("lap")
            if r.lap == LAPS - 1:
                self.events.append("final_lap")

    def _estimate(self):
        # player finished: give unfinished AI an estimated time, keeping current order
        t = self.elapsed
        length = self.length
        goal = LAPS * length
        rest = []
        for r in self.racers:
            if not r.finished:
                rest.append(r)
        # sort by progress, furthest first
        for i in range(1, len(rest)):
            j = i
            while j > 0 and rest[j].total > rest[j - 1].total:
                rest[j], rest[j - 1] = rest[j - 1], rest[j]
                j -= 1
        last = t
        for r in rest:
            rate = r.total / t if t > 0 and r.total > 0 else MAX_SPEED * 0.0009
            if rate < MAX_SPEED * 0.0003:
                rate = MAX_SPEED * 0.0003
            est = t + int((goal - r.total) / rate)
            if est <= last:
                est = last + 1
            last = est
            r.finished = True
            r.finish_ms = est
            r.lap = LAPS
            r.total = goal

    def _ai(self, r, now, dt, seg):
        tr = self.track
        length = self.length
        half = length * 0.5
        target = self._line[seg]

        if now >= r._greedy_at:
            r._greedy_at = now + 2000
            r._greedy = _rand() < 0.35 + (r.skill - 0.9) * 4.0

        # swerve to a token ahead
        if r._greedy and r.tokens < MAX_TOKENS:
            best = 18 * SEGMENT_LENGTH
            for p in tr.pickups:
                if p.kind != "token" or (p.respawn_at and p.respawn_at > now):
                    continue
                dz = p.index * SEGMENT_LENGTH + _HALF_SEG - r.z
                if dz < 0:
                    dz += length
                if 3 * SEGMENT_LENGTH < dz < best:
                    dx = p.x - r.x
                    if -0.7 < dx < 0.7:
                        best = dz
                        target = p.x

        # pass slower karts ahead, and give room to karts alongside
        for o in self.racers:
            if o is r:
                continue
            dz = o.z - r.z
            if dz > half:
                dz -= length
            elif dz < -half:
                dz += length
            if (-KART_LENGTH < dz <= KART_LENGTH) or \
                    (0 < dz < 5 * SEGMENT_LENGTH and o.speed < r.speed):
                d = o.x - target
                if -0.36 < d < 0.36:
                    target = o.x + 0.4 if d <= 0 else o.x - 0.4

        # dodge bugs (overrides everything)
        look = r._look
        for p in tr.pickups:
            if p.kind != "bug" or (p.respawn_at and p.respawn_at > now):
                continue
            dz = p.index * SEGMENT_LENGTH + _HALF_SEG - r.z
            if dz < -KART_LENGTH:
                dz += length
            if dz < look:
                d = p.x - target
                if -0.4 < d < 0.4:
                    target = p.x + 0.45 if d <= 0 else p.x - 0.45

        target = _clamp(target, -0.85, 0.85)

        # proportional steering with curve feed-forward
        ratio = r.speed / MAX_SPEED
        rate = _STEER_RATE * r._handling * (0.3 + 0.7 * ratio)
        push = CENTRIFUGAL * _CURVE_PUSH * r._grip * tr.curve_at(r.z) * ratio
        steer = push / rate + 3.0 * (target - r.x)
        steer = _clamp(steer, -1.0, 1.0)

        boost = False
        if r.tokens > 0 and now >= r.boost_until and now >= r.spin_until \
                and self._calm[seg]:
            chance = 0.9 if r.tokens >= MAX_TOKENS else 0.45
            boost = _rand() < chance * dt
        self._physics(r, now, dt, steer, False, boost, seg)

    def _collide(self):
        racers = self.racers
        n = len(racers)
        length = self.length
        half = length * 0.5
        wide = 2.0 * KART_HALF_WIDTH
        for i in range(n):
            a = racers[i]
            for j in range(i + 1, n):
                b = racers[j]
                dz = b.z - a.z
                if dz > half:
                    dz -= length
                elif dz < -half:
                    dz += length
                if dz >= KART_LENGTH or dz <= -KART_LENGTH:
                    continue
                dx = a.x - b.x
                adx = dx if dx >= 0 else -dx
                if adx >= wide:
                    continue
                if a.remote or b.remote:
                    # versus: each badge only nudges its own kart sideways (the
                    # rival's badge does the same), no speed loss, so it stays fair
                    me = b if a.remote else a
                    other = a if a.remote else b
                    d = me.x - other.x
                    if d == 0:
                        d = self._side
                    me.x += (0.5 if d > 0 else -0.5) * (wide - adx + 0.02)
                    if me.bump < 0.6 and me.is_player:
                        self.events.append("bump")
                    me.bump = 1.0
                    continue
                side = 1.0 if dx > 0 or (dx == 0 and i & 1) else -1.0
                push = wide - adx + 0.02
                wsum = a._weight + b._weight
                a.x += side * push * b._weight / wsum
                b.x -= side * push * a._weight / wsum
                fresh = a.bump < 0.6 and b.bump < 0.6
                if fresh:
                    if dz > 0:   # a is behind b
                        a.speed *= 1.0 - 0.1 * (2.0 * b._weight / wsum)
                    else:
                        b.speed *= 1.0 - 0.1 * (2.0 * a._weight / wsum)
                    if a.is_player or b.is_player:
                        self.events.append("bump")
                a.bump = 1.0
                b.bump = 1.0

    def _place(self):
        order = self._order
        n = len(order)
        for i in range(1, n):
            j = i
            while j > 0 and _ahead(order[j], order[j - 1]):
                order[j], order[j - 1] = order[j - 1], order[j]
                j -= 1
        for i in range(n):
            order[i].place = i + 1


def _ahead(a, b):
    if a.finished:
        if not b.finished:
            return True
        return a.finish_ms < b.finish_ms
    if b.finished:
        return False
    return a.total > b.total
