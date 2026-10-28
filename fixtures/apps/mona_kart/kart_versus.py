# Mona Kart: 1v1 versus between two badges held back to back (like Pong).
#
# Owns the badge link, the pairing lobby, the message protocol and the remote
# racer. Each badge simulates only its own kart and streams it to the other; the
# rival kart is a Race racer with `.remote` set, moved here from 'K' messages.
#
# Protocol: one ASCII kind byte + struct body (little endian), <= 20 bytes.
# `rnd` is the round number (u8, +1 per rematch); messages for another round are
# ignored, so late packets from a previous race can't leak into the next one.
#   P <I t>                    ping (clock sync), every PING_MS while linked
#   Q <I t_echo> <I t_mine>    pong: reply to P
#   C <B rnd> <B char>         my driver pick (255 = still choosing); resent
#   G <B rnd> <I start> <B central_char> <B other_char>
#                              central -> other: both picked, countdown starts at
#                              `start` (central clock); resent until GO
#   K <B rnd> <I z*4> <h x*1000> <H speed> <B lap> <b steer*100> <B flags> <B tokens>
#                              kart state every K_MS while racing (14 bytes);
#                              flags: 1 boosting, 2 spinning, 4 finished, 8 armed
#   F <B rnd> <I finish_ms>    I finished (ms since my GO); resent
#   R <B rnd>                  rematch please (rnd = round just raced); resent
#   L                          leaving the match

import struct

from kart_config import WIDTH, HEIGHT, LAPS, PLAYER_Z, FONT_TITLE, FONT_BODY, FONT_HUD, use_font
import kart_config
import kart_hud as hud
import kart_render as render
from kart_racers import CHARACTERS, Controls, Race
from kart_track import TRACK

CHANNEL = "mona_kart"

K_MS = 70               # kart state stream interval
PING_MS = 700           # clock sync ping interval
RESEND_MS = 450         # resend interval for C, G, F, R
START_MARGIN = 1500     # central schedules the countdown this far ahead (covers latency)
COUNTDOWN_MS = 3000
GO_MS = 600
FINISH_HOLD_MS = 2500   # after you finish, before results (if the outcome is known)
WAIT_MS = 25000         # rival must finish within this after you, or you win
DRAW_MS = 50            # finish times this close are a draw
LEAVE_CONFIRM_MS = 2000
BANNER_MS = 1100
MSG_MS = 4000           # lobby message line lifetime
EXTRAPOLATE_MS = 400    # remote kart coasts on its last speed at most this long
SPLIT_DD = getattr(kart_config, "SPLIT_DRAW_DISTANCE", 32)
TOP_VIEW = (0, 120)
BOTTOM_VIEW = (120, 120)

LOBBY = 0
SELECT = 1
COUNTDOWN = 2
RACE = 3
RESULTS = 4
NOLINK = 5

_P = 80   # ord("P")
_Q = 81
_C = 67
_G = 71
_K = 75
_F = 70
_R = 82
_L = 76
_NONE = 255

_BANNERS = {
    "token": "+TOKEN",
    "bug": "BUG! SPUN OUT",
    "final_lap": "FINAL LAP",
    "finish": "FINISH!",
}

_K_COL = None


def _cols():
    global _K_COL
    if _K_COL is None:
        _K_COL = {
            "band": color.rgb(0, 0, 0, 130),
            "panel": color.rgb(10, 10, 24, 200),
            "text": color.rgb(236, 236, 248),
            "muted": color.rgb(160, 160, 190),
            "gold": color.rgb(255, 204, 51),
            "title_mid": color.rgb(214, 50, 96),
            "shadow": color.rgb(24, 10, 48),
            "green": color.rgb(80, 230, 120),
            "red": color.rgb(240, 90, 100),
            "slot": color.rgb(70, 70, 96),
        }
    return _K_COL


def _text(s, x, y, fname, pen):
    use_font(fname)
    screen.pen = pen
    screen.text(s, x, y)


def _ctext(s, cx, y, fname, pen):
    use_font(fname)
    w, h = screen.measure_text(s)
    screen.pen = pen
    screen.text(s, cx - w / 2, y)
    return w, h


def _panel(x, y, w, h, pen):
    screen.pen = pen
    screen.rectangle(x + 1, y, w - 2, 1)
    screen.rectangle(x, y + 1, w, h - 2)
    screen.rectangle(x + 1, y + h - 1, w - 2, 1)


def _label(name):
    name = (name or "RIVAL").upper()
    if len(name) > 10:
        name = name[:10]
    return name


def draw_world_view(race, racer, view, now):
    """Draw the road following `racer` inside `view` (top, height)."""
    cz = (racer.z - PLAYER_Z) % TRACK.length
    render.draw_world(TRACK, race, cz, racer.x, now, racer, view=view, draw_distance=SPLIT_DD)


class _Remote:
    """Turns the rival's 'K' snapshots into a smoothly moving Racer."""

    def __init__(self, racer):
        self.r = racer
        self.has = False
        self.base_total = 0.0
        self.base_at = 0
        self.err = 0.0
        self.speed = 0.0
        self.tx = racer.x
        self.tsteer = 0.0
        self.flags = 0
        self.finish_ms = 0
        length = TRACK.length
        self.r.total = (racer.z - TRACK.start_z) % length - length

    def _shown(self, now):
        ahead = now - self.base_at
        if ahead > EXTRAPOLATE_MS:
            ahead = EXTRAPOLATE_MS
        elif ahead < 0:
            ahead = 0
        return self.base_total + self.speed * ahead * 0.001

    def on_state(self, now, z, x, speed, lap, steer, flags, tokens):
        length = TRACK.length
        rel = (z - TRACK.start_z) % length
        if flags & 8:
            total = lap * length + rel
        else:
            total = rel - length
        if self.has:
            err = self._shown(now) + self.err - total
            self.err = err if -3000.0 < err < 3000.0 else 0.0
        self.has = True
        self.base_total = total
        self.base_at = now
        self.speed = speed
        self.tx = x
        self.tsteer = steer
        self.flags = flags
        r = self.r
        r.tokens = tokens

    def finish(self, finish_ms):
        self.finish_ms = finish_ms if finish_ms > 0 else 1

    def update(self, now, dt):
        r = self.r
        if dt > 0:
            f = 12.0 * dt
            if f > 1.0:
                f = 1.0
            self.err -= self.err * (8.0 * dt if 8.0 * dt < 1.0 else 1.0)
            r.x += (self.tx - r.x) * f
            r.steer += (self.tsteer - r.steer) * f
            if r.bump > 0:
                r.bump -= 3.0 * dt
                if r.bump < 0:
                    r.bump = 0.0
        if not self.has:
            return
        length = TRACK.length
        goal = LAPS * length
        total = self._shown(now) + self.err
        r.z = (TRACK.start_z + total) % length
        r.speed = self.speed
        flags = self.flags
        r.boost_until = now + 120 if flags & 1 else 0
        r.spin_until = now + 120 if flags & 2 else 0
        if self.finish_ms:
            r.finished = True
            r.finish_ms = self.finish_ms
            r.lap = LAPS
            r.total = goal
        else:
            if total >= goal:
                total = goal - 1.0
            r.total = total
            lap = int(total // length) if total > 0 else 0
            r.lap = lap if lap < LAPS else LAPS


class Versus:
    """The whole versus mode. `update` returns "title" when the player leaves."""

    def __init__(self, game, now):
        self.game = game
        self.character = game.character
        self.race = None
        self.remote = None
        self.controls = Controls()
        self.link = None
        self.proximity = None
        self.pairing = None
        self.badgelink = None
        self.error = None
        self.phase = LOBBY
        self.phase_at = now
        self.msg = None
        self.msg_until = 0
        self.attract_z = 0.0
        self.light = 0.0
        self.round = 0
        self._reset_link_state()
        self._reset_round()
        try:
            from pairing import Pairing
            from badgeware import link as badgelink
            self.badgelink = badgelink
            self.pairing = Pairing(74, 138, 6)
            self.link = badgelink.Link(CHANNEL)
            self.proximity = badgelink.Proximity(self.link)
        except Exception as e:  # no link on this firmware (or radio trouble)
            self.error = str(e) or "no link"
            self.phase = NOLINK

    # --- state helpers -----------------------------------------------------------
    def _reset_link_state(self):
        self.offset = None        # rival clock - my clock (ms), from ping/pong
        self.best_rtt = 0
        self.ping_at = -100000
        self.rival_name = "RIVAL"
        self.rival_id = None
        self.was_linked = False

    def _reset_round(self):
        self.pick = None          # my locked driver, or None while choosing
        self.rival_pick = None
        self.rival_shown = None   # rival pick the banner last announced
        self.start_at = 0         # local ticks when the 3 s countdown starts
        self.go_at = 0
        self.sent_g = False
        self.k_at = 0
        self.resend_at = 0
        self.finished_at = 0
        self.rival_finish = None
        self.outcome = None
        self.outcome_at = 0
        self.rematch = False
        self.rival_rematch = False
        self.rival_gone = False
        self.leave_until = 0
        self.banner = None
        self.banner_until = 0

    def set_phase(self, phase, now):
        self.phase = phase
        self.phase_at = now

    def flash(self, text, now):
        self.banner = text
        self.banner_until = now + BANNER_MS

    def note(self, text, now):
        self.msg = text
        self.msg_until = now + MSG_MS

    def send(self, data):
        link = self.link
        if link is None:
            return
        try:
            if link.linked:
                link.send(data)
        except Exception:
            pass

    def close(self):
        link = self.link
        self.link = None
        if link is not None:
            try:
                if link.linked:
                    link.send(b"L")
            except Exception:
                pass
            try:
                link.close()
            except Exception:
                pass

    def leave(self, now, why=None):
        """Leave the current match on purpose and go back to the lobby."""
        self.send(b"L")
        self._part(now)
        self.race = None
        self.remote = None
        self._reset_round()
        self.set_phase(LOBBY, now)
        if why:
            self.note(why, now)

    def _part(self, now):
        link = self.link
        if link is None:
            return
        try:
            partner = link.partner
            self.proximity.rest(partner.id if partner else None, now)
        except Exception:
            pass
        try:
            link.leave()
        except Exception:
            pass

    def _rival_left(self, now, dropped):
        """The rival walked away (link dropped) or sent 'L'."""
        if self.phase in (COUNTDOWN, RACE):
            self.rival_gone = True
            if self.outcome is None:
                if self.rival_finish is not None:
                    self._decide(now)
                    if self.outcome is None:
                        self._set_outcome("lose", now)
                else:
                    self._set_outcome("gone", now)
            self.set_phase(RESULTS, now)
        elif self.phase == RESULTS:
            self.rival_gone = True
            if self.outcome is None:
                self._set_outcome("gone", now)
        else:
            self.race = None
            self.remote = None
            self._reset_round()
            self.set_phase(LOBBY, now)
            self.note("rival walked away", now)
        if not dropped:
            self._part(now)
        else:
            try:
                self.proximity.clear()
            except Exception:
                pass
        self.was_linked = False

    # --- per frame -----------------------------------------------------------------
    def update(self, now, dt):
        if self.phase == NOLINK:
            if badge.pressed(BUTTON_BACK) or badge.pressed(BUTTON_SELECT):
                return "title"
            return None
        link = self.link
        try:
            link.pump()
            linked = link.linked
        except Exception:
            linked = False

        if linked and not self.was_linked:
            self._linked(now)
        elif not linked and self.was_linked:
            self._rival_left(now, True)

        if linked:
            try:
                messages = link.receive()
            except Exception:
                messages = ()
            for m in messages:
                try:
                    self._handle(bytes(m), now)
                except Exception:
                    pass  # malformed message: ignore
            if now - self.ping_at >= PING_MS:
                self.ping_at = now
                self.best_rtt += 4  # let fresher samples win slowly (clock drift)
                self.send(b"P" + struct.pack("<I", now & 0xFFFFFFFF))

        result = None
        phase = self.phase
        if phase == LOBBY:
            result = self._lobby(now, dt)
        elif phase == SELECT:
            self._select(now)
        elif phase == COUNTDOWN:
            self._countdown(now, dt)
        elif phase == RACE:
            self._race(now, dt)
        elif phase == RESULTS:
            self._results(now, dt)

        try:
            if self.link is not None:
                self.link.update()
        except Exception:
            pass
        return result

    def _linked(self, now):
        self._reset_link_state()
        self.was_linked = True
        try:
            p = self.link.partner
            if p is not None:
                self.rival_name = p.name or "RIVAL"
                self.rival_id = p.id
        except Exception:
            pass
        self.round = 0
        self._reset_round()
        self.race = None
        self.remote = None
        self.msg = None
        self.set_phase(SELECT, now)

    # --- messages ------------------------------------------------------------------
    def _handle(self, m, now):
        n = len(m)
        if n < 1:
            return
        kind = m[0]
        rnd = self.round & 0xFF
        if kind == _P:
            if n >= 5:
                t = struct.unpack_from("<I", m, 1)[0]
                self.send(b"Q" + struct.pack("<II", t, now & 0xFFFFFFFF))
        elif kind == _Q:
            if n >= 9:
                sent, theirs = struct.unpack_from("<II", m, 1)
                mine = now & 0xFFFFFFFF
                rtt = (mine - sent) & 0xFFFFFFFF
                if rtt < 5000 and (self.offset is None or rtt <= self.best_rtt):
                    self.best_rtt = rtt
                    self.offset = theirs - (sent + mine) / 2
        elif kind == _L:
            self._rival_left(now, False)
        elif n < 2:
            return
        elif kind == _C:
            if n >= 3:
                if m[1] == rnd and self.phase == SELECT:
                    self.rival_pick = None if m[2] >= len(CHARACTERS) else m[2]
                elif m[1] == ((rnd + 1) & 0xFF) and self.phase == RESULTS:
                    self.rival_rematch = True  # they already moved on to the rematch
        elif kind == _G:
            if n >= 8 and m[1] == rnd and self.phase == SELECT and not self.link.central:
                start, cc, oc = struct.unpack_from("<IBB", m, 2)
                if cc >= len(CHARACTERS) or oc >= len(CHARACTERS):
                    return
                self.pick = oc
                self.character = oc
                self.rival_pick = cc
                if self.offset is not None:
                    local = int(start - self.offset)
                    if local < now - 1000 or local > now + 4000:
                        local = now + START_MARGIN - 40
                else:
                    local = now + START_MARGIN - 40  # no clock sample yet: start on receipt
                self._begin(local, now)
        elif kind == _K:
            if n >= 14 and m[1] == rnd and self.remote is not None and self.phase in (COUNTDOWN, RACE, RESULTS):
                z4, x, speed, lap, steer, flags, tokens = struct.unpack_from("<IhHBbBB", m, 2)
                self.remote.on_state(now, z4 * 0.25, x * 0.001, speed, lap, steer * 0.01,
                                     flags, tokens)
        elif kind == _F:
            if n >= 6 and m[1] == rnd and self.remote is not None and self.rival_finish is None:
                ms = struct.unpack_from("<I", m, 2)[0]
                self.rival_finish = ms
                self.remote.finish(ms)
                if self.phase == RACE and not self.race.finished:
                    self.flash("RIVAL FINISHED", now)
        elif kind == _R:
            if m[1] == rnd and self.phase in (RACE, RESULTS):
                self.rival_rematch = True

    # --- lobby -------------------------------------------------------------------------
    def _lobby(self, now, dt):
        self.attract_z = (self.attract_z + 2600 * dt) % TRACK.length
        try:
            self.proximity.update(now)
        except Exception:
            pass
        if badge.pressed(BUTTON_BACK):
            return "title"
        return None

    def _draw_lobby(self, now):
        K = _cols()
        render.draw_world(TRACK, None, self.attract_z, 0.0, now, None)
        screen.pen = K["band"]
        screen.rectangle(0, 0, WIDTH, HEIGHT)

        # title
        title = "VERSUS"
        use_font(FONT_TITLE)
        w, h = screen.measure_text(title)
        x = (WIDTH - w) / 2
        _text(title, x + 3, 13, FONT_TITLE, K["shadow"])
        _text(title, x + 1.5, 11.5, FONT_TITLE, K["title_mid"])
        _text(title, x, 10, FONT_TITLE, K["gold"])
        y = 14 + h
        _ctext("1v1 over the badge link", WIDTH / 2, y, FONT_HUD, K["muted"])

        # you: identicon + name
        link = self.link
        _panel(16, 48, WIDTH - 32, 82, K["panel"])
        name = "you"
        try:
            name = link.name or "you"
            self.badgelink.draw_identicon(screen, link.id, 26, 56, 24)
        except Exception:
            pass
        _text(name, 58, 58, FONT_BODY, K["text"])
        _text("your badge", 58, 72, FONT_HUD, K["muted"])

        # status
        prox = self.proximity
        status = "hold badges back to back"
        pen = K["text"]
        mode = 0
        progress = 0.0
        try:
            if prox.cooling(now) is not None:
                status = "pull apart"
                pen = K["gold"]
                mode = 1
            else:
                peer = prox.syncing()
                if peer is not None:
                    progress = prox.progress(now)
                    mode = 2
                    if progress >= 1.0:
                        status = "connecting..."
                    else:
                        status = "syncing with %s" % (peer.name or "badge")
                    pen = K["green"]
        except Exception:
            pass
        _ctext(status, WIDTH / 2, 92, FONT_BODY, pen)
        if mode == 2:
            bw = WIDTH - 80
            screen.pen = K["slot"]
            screen.rectangle(40, 110, bw, 6)
            screen.pen = K["green"]
            screen.rectangle(40, 110, bw * (progress if progress < 1.0 else 1.0), 6)
        try:
            if mode == 1:
                self.pairing.draw_apart()
            elif mode == 2:
                self.pairing.draw_together(now)
            else:
                self.pairing.draw_loop(now)
        except Exception:
            pass

        if self.msg is not None and now < self.msg_until:
            use_font(FONT_BODY)
            w, h = screen.measure_text(self.msg)
            _panel((WIDTH - w) / 2 - 10, 194, w + 20, h + 8, K["panel"])
            _text(self.msg, (WIDTH - w) / 2, 198, FONT_BODY, K["red"])
        hud.draw_hints((("title", "BACK"),))

    # --- driver select ------------------------------------------------------------------
    def _select(self, now):
        n = len(CHARACTERS)
        if self.pick is None:
            if badge.pressed(BUTTON_LEFT):
                self.character = (self.character - 1) % n
            if badge.pressed(BUTTON_RIGHT):
                self.character = (self.character + 1) % n
            if badge.pressed(BUTTON_SELECT):
                self.pick = self.character
                self.game.remember_character(self.character)
                self.resend_at = 0
            elif badge.pressed(BUTTON_BACK):
                self.leave(now, "you left the match")
                return
        elif badge.pressed(BUTTON_BACK):
            self.pick = None
            self.resend_at = 0
        if self.rival_pick is not None and self.rival_pick != self.rival_shown:
            self.rival_shown = self.rival_pick
        if now >= self.resend_at:
            self.resend_at = now + RESEND_MS
            self.send(b"C" + struct.pack("<BB", self.round & 0xFF,
                                  _NONE if self.pick is None else self.pick))
        if self.link.central and self.pick is not None and self.rival_pick is not None:
            self._begin(now + START_MARGIN, now)

    def _begin(self, start_at, now):
        """Both drivers are locked: build the 2-kart race and schedule the countdown."""
        central = self.link.central
        self.start_at = start_at
        self.go_at = start_at + COUNTDOWN_MS
        self.race = Race(TRACK, self.pick, self.rival_pick, False, -1 if central else 1)
        self.remote = _Remote(self.race.rival)
        self.controls.clear()
        self.banner = None
        self.resend_at = 0
        self.set_phase(COUNTDOWN, now)

    def _send_go(self, now):
        # central keeps telling the other badge when the countdown starts (until GO)
        if self.link.central and now >= self.resend_at and now < self.go_at:
            self.resend_at = now + RESEND_MS
            off = self.start_at
            self.send(b"G" + struct.pack("<BIBB", self.round & 0xFF, off & 0xFFFFFFFF,
                                  self.pick, self.rival_pick))

    # --- countdown / race ---------------------------------------------------------------
    def _countdown(self, now, dt):
        self._send_go(now)
        if badge.pressed(BUTTON_BACK):
            self._ask_leave(now)
            if self.phase != COUNTDOWN:
                return
        self.race.update(now, dt, self.controls)
        self.remote.update(now, dt)
        if now >= self.go_at:
            self.race.start(self.go_at)
            self.resend_at = 0
            self.set_phase(RACE, now)
            self.phase_at = self.go_at

    def _ask_leave(self, now):
        if now < self.leave_until:
            self.leave(now, "you left the race")
        else:
            self.leave_until = now + LEAVE_CONFIRM_MS

    def _race(self, now, dt):
        if badge.pressed(BUTTON_BACK):
            self._ask_leave(now)
            if self.phase != RACE:
                return
        race = self.race
        controls = self.controls
        controls.steer = (1 if badge.held(BUTTON_RIGHT) else 0) - (1 if badge.held(BUTTON_LEFT) else 0)
        controls.brake = badge.held(BUTTON_DOWN)
        controls.boost = badge.pressed(BUTTON_SELECT)
        self.remote.update(now, dt)
        race.update(now, dt, controls)
        for event in race.events:
            if event == "lap" and "final_lap" not in race.events:
                self.flash("LAP %d/%d" % (min(race.player.lap + 1, LAPS), LAPS), now)
            elif event in _BANNERS:
                self.flash(_BANNERS[event], now)
            if event in ("token", "boost", "pad", "finish"):
                self.light = 1.0

        p = race.player
        if now - self.k_at >= K_MS:
            self.k_at = now
            self._send_kart(now)
        if race.finished and not self.finished_at:
            self.finished_at = now
            self.resend_at = 0
        if self.finished_at and now >= self.resend_at:
            self.resend_at = now + RESEND_MS
            self.send(b"F" + struct.pack("<BI", self.round & 0xFF, p.finish_ms))
        if self.outcome is None:
            self._decide(now)
        if self.outcome is not None and self.finished_at and \
                now - self.finished_at >= FINISH_HOLD_MS and now - self.outcome_at >= 800:
            self.set_phase(RESULTS, now)

    def _send_kart(self, now):
        p = self.race.player
        flags = 0
        if now < p.boost_until:
            flags |= 1
        if now < p.spin_until:
            flags |= 2
        if p.finished:
            flags |= 4
        if p._armed:
            flags |= 8
        x = int(p.x * 1000)
        x = -32000 if x < -32000 else (32000 if x > 32000 else x)
        st = int(p.steer * 100)
        st = -100 if st < -100 else (100 if st > 100 else st)
        sp = int(p.speed)
        sp = 0 if sp < 0 else (65535 if sp > 65535 else sp)
        lap = p.lap if p.lap < 255 else 255
        self.send(b"K" + struct.pack("<BIhHBbBB", self.round & 0xFF, int(p.z * 4) & 0xFFFFFFFF,
                                     x, sp, lap, st, flags, p.tokens & 0xFF))

    def _set_outcome(self, outcome, now):
        self.outcome = outcome
        self.outcome_at = now

    def _decide(self, now):
        p = self.race.player
        if not p.finished:
            return
        if self.rival_finish is not None:
            d = p.finish_ms - self.rival_finish
            if -DRAW_MS <= d <= DRAW_MS:
                self._set_outcome("draw", now)
            else:
                self._set_outcome("win" if d < 0 else "lose", now)
        elif self.finished_at and now - self.finished_at >= WAIT_MS:
            self._set_outcome("win", now)

    def gap_ms(self):
        race = self.race
        me = race.player
        rv = race.rival
        goal = LAPS * TRACK.length
        if me.finished and rv.finished:
            return me.finish_ms - rv.finish_ms
        if me.finished:
            s = rv.speed if rv.speed > 1000 else 1000
            return -(goal - rv.total) / s * 1000
        if rv.finished:
            s = me.speed if me.speed > 1000 else 1000
            return (goal - me.total) / s * 1000
        s = me.speed if me.speed > 1000 else 1000
        return (rv.total - me.total) / s * 1000

    # --- results ---------------------------------------------------------------------
    def _results(self, now, dt):
        if self.rival_gone:
            if badge.pressed(BUTTON_BACK):
                self.leave(now)
            return
        race = self.race
        if race is not None and race.finished and self.rival_finish is None:
            # the rival is still racing: my kart keeps cruising (AI) on their screen
            self.controls.clear()
            self.remote.update(now, dt)
            race.update(now, dt, self.controls)
            if now - self.k_at >= K_MS:
                self.k_at = now
                self._send_kart(now)
        if badge.pressed(BUTTON_SELECT) and not self.rematch:
            self.rematch = True
            self.resend_at = 0
        elif badge.pressed(BUTTON_BACK):
            self.leave(now)
            return
        if now >= self.resend_at:
            self.resend_at = now + RESEND_MS
            if self.race is not None and self.race.finished:
                self.send(b"F" + struct.pack("<BI", self.round & 0xFF, self.race.player.finish_ms))
            if self.rematch:
                self.send(b"R" + struct.pack("<B", self.round & 0xFF))
        if self.rematch and self.rival_rematch:
            self.send(b"R" + struct.pack("<B", self.round & 0xFF))
            last = self.pick
            self.round += 1
            self._reset_round()
            if last is not None:
                self.character = last
            self.race = None
            self.remote = None
            self.set_phase(SELECT, now)

    # --- drawing -------------------------------------------------------------------------
    def draw(self, now):
        phase = self.phase
        K = _cols()
        if phase == NOLINK:
            screen.pen = color.rgb(12, 12, 30)
            screen.clear()
            _ctext("VERSUS", WIDTH / 2, 60, FONT_TITLE, K["gold"])
            _ctext("badge link not available", WIDTH / 2, 110, FONT_BODY, K["text"])
            _ctext(self.error[:40] if self.error else "", WIDTH / 2, 130, FONT_HUD, K["muted"])
            hud.draw_hints((("title", "BACK"),))
        elif phase == LOBBY:
            self._draw_lobby(now)
        elif phase == SELECT:
            index = self.character if self.pick is None else self.pick
            hud.draw_select(index, now)
            if self.pick is not None:
                hud.draw_banner("waiting for rival...", now)
            elif self.rival_pick is not None:
                hud.draw_banner("rival picked %s" % CHARACTERS[self.rival_pick]["name"], now)
        elif phase == RESULTS:
            self._draw_results(now)
        else:
            self._draw_split(now)

    def _draw_split(self, now):
        race = self.race
        me = race.player
        rv = race.rival
        draw_world_view(race, me, TOP_VIEW, now)
        draw_world_view(race, rv, BOTTOM_VIEW, now)
        hud.draw_view_hud(race, me, TOP_VIEW, now, "YOU")
        hud.draw_view_hud(race, rv, BOTTOM_VIEW, now, _label(self.rival_name))
        hud.draw_split_divider(race, now, self.gap_ms() if race.started else 0)
        if self.phase == COUNTDOWN:
            left = self.go_at - now
            if left > COUNTDOWN_MS:
                hud.draw_banner("GET READY", now)
            else:
                hud.draw_countdown(left)
        elif now - self.go_at < GO_MS:
            hud.draw_countdown(0)
        if now < self.leave_until:
            hud.draw_banner("leave race? BACK again", now)
        elif self.finished_at and self.outcome is None:
            left = (WAIT_MS - (now - self.finished_at)) // 1000
            hud.draw_banner("waiting for rival... %ds" % (left if left > 0 else 0), now)
        elif self.banner is not None and now < self.banner_until:
            hud.draw_banner(self.banner, now)

    def _draw_results(self, now):
        race = self.race
        outcome = self.outcome or "gone"
        my_name = "YOU"
        try:
            my_name = self.link.name or "YOU"
        except Exception:
            pass
        status = None
        if self.rival_gone:
            status = None if outcome == "gone" else "RIVAL LEFT"
        elif self.rematch:
            status = "WAITING FOR RIVAL..."
        elif self.rival_rematch:
            status = "RIVAL WANTS A REMATCH"
        elif self.outcome is not None and race is not None and \
                not race.rival.finished and self.outcome == "win":
            status = "RIVAL DID NOT FINISH"
        hud.draw_versus_results(race, now, my_name, self.rival_name, outcome, status)

    def lights(self, now, dt):
        if self.phase == LOBBY:
            lights = None
            try:
                lights = self.proximity.lights(now)
            except Exception:
                pass
            if lights:
                badge.caselights(*lights)
            else:
                badge.caselights(0)
            return
        race = self.race
        boosting = race is not None and self.phase == RACE and now < race.player.boost_until
        target = 0.6 if boosting else 0.0
        self.light = max(target, self.light - dt * 2.5)
        badge.caselights(self.light)
