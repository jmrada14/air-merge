"""
Air Merge: share an app from your badge with a friend's badge.

Hold two badges back to back to pair them. Pick one of your apps and confirm who
it goes to; your friend sees who is sending what and accepts or declines. The app
is checked file by file before it is installed, then shows up in their menu.

Controls:
* Up / Down = Pick an app
* Enter = Send / Accept
* Back = Cancel, decline, leave the pairing, exit
"""

import math
import os
import sys
import time

APP_DIR = "/system/apps/air_merge"

try:
    os.chdir(APP_DIR)
    sys.path.insert(0, APP_DIR)
except OSError:
    pass  # running in the desktop simulator, which sets the path itself

import am_fs
import am_proto
import am_ui as ui

try:
    badge.mode(HIRES | VSYNC)
except Exception:
    pass
try:
    screen.antialias = image.OFF
except Exception:
    pass
previous_clear = badge.default_clear
badge.default_clear = None

CHANNEL = "air_merge"
MSG_MS = 4000
BAD_MS = 9000
REPEAT_MS = 330
REPEAT_STEP_MS = 90


FLOW = (ui.CONFIRM_SEND, ui.OFFERED, ui.SENDING, ui.OFFER_IN, ui.RECEIVING, ui.VERIFYING,
        ui.INSTALLING)
_FOLLOW = {
    am_proto.PREP: ui.OFFERED,
    am_proto.OFFER: ui.OFFERED,
    am_proto.SEND: ui.SENDING,
    am_proto.AWAIT: ui.SENDING,
    am_proto.ASK: ui.OFFER_IN,
    am_proto.RECV: ui.RECEIVING,
    am_proto.VERIFY: ui.VERIFYING,
    am_proto.INSTALL: ui.INSTALLING,
}


class App:
    def __init__(self, now):
        self.state = ui.LOBBY
        self.msg = None
        self.msg_until = 0
        self.msg_bad = False
        self.sel = 0
        self.top = 0
        self.repeat_at = 0
        self.apps = []
        self.pick = None
        self.offer = None
        self.offer_local = None
        self.bad = None
        self.bad_at = 0
        self.result = None
        self.light = 0.0
        self.error = None
        self.partner_name = "badge"
        self.partner_id = None
        self.was_linked = False
        self.link = None
        self.proximity = None
        self.pairing = None
        self.badgelink = None
        self.my_name = "you"
        self.my_id = None
        try:
            am_fs.clean_stage()
        except Exception:
            pass
        self.refresh_apps()
        try:
            from pairing import Pairing
            from badgeware import link as badgelink
            self.badgelink = badgelink
            self.pairing = Pairing(74, 138, 6)
            self.link = badgelink.Link(CHANNEL)
            self.proximity = badgelink.Proximity(self.link)
        except Exception as e:  # no link on this firmware (or radio trouble)
            self.error = str(e) or "no link"
            self.state = ui.NOLINK
        try:
            self.my_name = self.link.name or "you"
            self.my_id = self.link.id
        except Exception:
            pass
        self.engine = am_proto.Engine(self.link, _clock, _diff)

    # --- helpers -------------------------------------------------------------------------
    def refresh_apps(self):
        try:
            self.apps = am_fs.list_apps()
        except Exception:
            self.apps = []
        if self.sel >= len(self.apps):
            self.sel = max(0, len(self.apps) - 1)
        self._scroll()

    def _scroll(self):
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + ui.ROWS:
            self.top = self.sel - ui.ROWS + 1
        if self.top > max(0, len(self.apps) - ui.ROWS):
            self.top = max(0, len(self.apps) - ui.ROWS)

    def note(self, text, now, bad=False):
        self.msg = text
        self.msg_until = now + MSG_MS
        self.msg_bad = bad

    def local_app(self, slug):
        for app in self.apps:
            if app["slug"] == slug:
                return app
        return None

    def show_result(self, ok, title, lines, now):
        self.result = {"ok": ok, "title": title, "lines": lines, "gone": not self.was_linked}
        self.state = ui.DONE
        if ok:
            self.light = 1.0
        else:
            self.light = 0.0

    def back_home(self, now):
        """Return to the app list (or the lobby when the partner is gone)."""
        self.state = ui.PAIRED if self.was_linked else ui.LOBBY
        self.pick = None
        self.offer = None

    # --- link lifecycle ------------------------------------------------------------------
    def _linked(self, now):
        self.was_linked = True
        name = "badge"
        pid = None
        try:
            p = self.link.partner
            if p is not None:
                name = p.name or "badge"
                pid = p.id
        except Exception:
            pass
        self.partner_name = name
        self.partner_id = pid
        central = False
        try:
            central = self.link.central
        except Exception:
            pass
        self.engine.linked(central, now)
        self.msg = None
        if self.state != ui.DONE:
            self.state = ui.PAIRED

    def _dropped(self, now):
        self.was_linked = False
        name = self.partner_name
        eng = self.engine
        # every byte sent (maybe not yet acked): B may finish verifying and installing alone
        waiting = eng.state == am_proto.AWAIT or (
            eng.state == am_proto.SEND and getattr(eng, "_pos", 0) >= eng.total)
        app_name = eng.m["name"] if eng.m else "the app"
        phase = eng.remote_phase
        still = eng.dropped(now)
        try:
            self.proximity.clear()
        except Exception:
            pass
        if still:
            return            # verify/install finishes locally; the result screen says they left
        if waiting:
            line = ("They were installing it, so they probably have it now" if
                    phase >= am_proto.INSTALLING else "They may still have installed %s" % app_name)
            self.show_result(None, "%s left early" % name,
                             [line, "Ask them to check their menu"], now)
            self.result["gone"] = False
            return
        if self.state == ui.DONE:
            self.result["gone"] = True
            return
        self.state = ui.LOBBY
        self.pick = None
        self.offer = None
        self.partner_id = None
        self.note("%s walked away" % name, now, True)

    def leave(self, now):
        """BACK on the list: part from the partner (like kart versus) and go to the lobby."""
        eng = self.engine
        eng.cancel(now)
        eng._flush()
        link = self.link
        try:
            partner = link.partner
            self.proximity.rest(partner.id if partner else None, now)
        except Exception:
            pass
        try:
            link.leave()
        except Exception:
            pass
        eng.dropped(now)
        self.was_linked = False
        self.partner_id = None
        self.state = ui.LOBBY

    def close(self):
        eng = self.engine
        try:
            if eng.state not in (am_proto.IDLE, am_proto.INSTALL):
                eng.cancel(0)
                eng._flush()
            eng.abort()
        except Exception:
            pass
        link = self.link
        self.link = None
        if link is not None:
            try:
                link.close()
            except Exception:
                pass

    def exit(self):
        self.close()
        try:
            badge.caselights(0)
        except Exception:
            pass
        badge.default_clear = previous_clear
        reset()

    # --- per frame -----------------------------------------------------------------------
    def update(self, now):
        if self.state == ui.NOLINK:
            if badge.pressed(BUTTON_BACK) or badge.pressed(BUTTON_SELECT):
                self.exit()
            return
        link = self.link
        eng = self.engine
        try:
            link.pump()
            linked = link.linked
        except Exception:
            linked = False
        if linked and not self.was_linked:
            self._linked(now)
        elif not linked and self.was_linked:
            self._dropped(now)
        if linked:
            try:
                messages = link.receive()
            except Exception:
                messages = ()
            for m in messages:
                try:
                    eng.on_message(bytes(m), now)
                except Exception:
                    pass
        eng.tick(now)
        self._events(now)
        self._input(now)
        try:
            if self.link is not None:
                self.link.update()
        except Exception:
            pass

    def _events(self, now):
        eng = self.engine
        events = eng.events
        if not events:
            # the engine went idle on its own (e.g. a half-received offer timed out)
            self._sync(now)
            return
        eng.events = []
        who = self.partner_name
        for e in events:
            kind = e[0]
            if kind == "offer":
                self.offer = e[1]
                self.offer_local = self.local_app(e[1]["slug"])
                self.pick = None
                self.state = ui.OFFER_IN
            elif kind == "offer_bad":
                self.bad = (e[1], e[2])
                self.bad_at = now
                self.state = ui.OFFER_BAD
            elif kind == "declined":
                name = self.pick["name"] if self.pick else "the app"
                code = e[1]
                why = e[2] if len(e) > 2 else ""
                if code == am_proto.BUSY and why.startswith("USB"):
                    title = "%s can't install now" % who
                    line = "Their badge is in USB disk mode. Ask them to eject it, then try again"
                elif code == am_proto.TOO_BIG:
                    title, line = "Not enough room", "%s is too big for %s's badge" % (name, who)
                elif code == am_proto.INVALID:
                    title, line = "Can't send that", "%s's badge refused %s: %s" % (
                        who, name, why or "invalid")
                elif code == am_proto.BUSY:
                    title, line = "%s is busy" % who, "Try again in a moment"
                elif code == am_proto.TIMED_OUT:
                    title, line = "No answer", "%s didn't answer in time" % who
                else:
                    title, line = "%s said no thanks" % who, "%s wasn't sent" % name
                self.show_result(False, title, [line], now)
            elif kind == "result":
                ok, why, name = e[1], e[2], e[3]
                if ok:
                    self.show_result(True, "%s installed %s!" % (who, name),
                                     ["It's in their menu once they exit Air Merge"], now)
                else:
                    self.show_result(False, "%s couldn't install %s" % (who, name),
                                     [why or "something went wrong"], now)
            elif kind == "installed":
                self.refresh_apps()
                name = e[1].get("name", "the app")
                self.show_result(True, "Installed %s!" % name,
                                 ["Exit Air Merge to find it in the menu"], now)
            elif kind == "failed":
                if self.state in FLOW:
                    self.show_result(False, "Transfer failed", [e[1]], now)
                else:
                    self.note(e[1], now, True)
            elif kind == "cancelled":
                if e[1] == "collision":
                    self.note("You both sent at once - %s goes first" % who, now)
                else:
                    self.note("%s cancelled" % who, now, True)
                if self.state in FLOW:
                    self.back_home(now)
            elif kind == "ask_timeout":
                self.note("Offer timed out - declined", now, True)
                if self.state == ui.OFFER_IN:
                    self.back_home(now)
        self._sync(now)

    def _sync(self, now):
        """Follow the engine through the sending / receiving sub-states."""
        s = self.state
        if s not in ui.SENDER and s not in ui.RECEIVER and s != ui.OFFER_IN:
            return
        st = self.engine.state
        nxt = _FOLLOW.get(st)
        if nxt is None:
            self.back_home(now)      # the engine went idle on its own
        elif s == ui.OFFER_IN:
            if st != am_proto.ASK:
                self.back_home(now)
        elif (s in ui.SENDER) == (nxt in ui.SENDER):
            self.state = nxt

    def _input(self, now):
        s = self.state
        eng = self.engine
        if s == ui.LOBBY:
            if not eng.busy():
                try:
                    self.proximity.update(now)
                except Exception:
                    pass
            if badge.pressed(BUTTON_BACK):
                self.exit()
        elif s == ui.PAIRED:
            n = len(self.apps)
            step = 0
            for btn, d in ((BUTTON_UP, -1), (BUTTON_DOWN, 1)):
                if badge.pressed(btn):
                    step = d
                    self.repeat_at = now + REPEAT_MS
                elif badge.held(btn) and now >= self.repeat_at:
                    step = d
                    self.repeat_at = now + REPEAT_STEP_MS
            if step and n:
                self.sel = (self.sel + step) % n
                self._scroll()
            if badge.pressed(BUTTON_SELECT) and n:
                self.pick = self.apps[self.sel]
                self.state = ui.CONFIRM_SEND
            elif badge.pressed(BUTTON_BACK):
                self.leave(now)
        elif s == ui.CONFIRM_SEND:
            if badge.pressed(BUTTON_SELECT):
                if eng.send_app(self.pick["slug"], now):
                    self.state = ui.OFFERED
            elif badge.pressed(BUTTON_BACK):
                self.state = ui.PAIRED
        elif s in ui.SENDER:
            if badge.pressed(BUTTON_BACK):
                late = eng.state == am_proto.AWAIT
                if eng.cancel(now):
                    self.back_home(now)
                    if late:
                        self.note("Cancelled (%s may still get it)" % self.partner_name, now)
                    else:
                        self.note("Cancelled", now)
        elif s == ui.OFFER_IN:
            if badge.pressed(BUTTON_SELECT):
                if eng.accept(now):
                    self.state = ui.RECEIVING
                    self._sync(now)
            elif badge.pressed(BUTTON_BACK):
                name = self.offer.get("name", "the app") if self.offer else "the app"
                eng.decline(now)
                self.back_home(now)
                self.note("You declined %s" % name, now)
        elif s == ui.OFFER_BAD:
            if badge.pressed(BUTTON_SELECT) or badge.pressed(BUTTON_BACK) or now - self.bad_at > BAD_MS:
                self.back_home(now)
        elif s in ui.RECEIVER:
            if badge.pressed(BUTTON_BACK) and eng.state in (am_proto.RECV, am_proto.VERIFY):
                if eng.cancel(now):
                    self.back_home(now)
                    self.note("Cancelled", now)
        elif s == ui.DONE:
            if badge.pressed(BUTTON_SELECT) or badge.pressed(BUTTON_BACK):
                self.back_home(now)

    def lights(self, now, dt):
        s = self.state
        level = 0.0
        if s == ui.LOBBY:
            lights = None
            try:
                lights = self.proximity.lights(now)
            except Exception:
                pass
            if lights:
                badge.caselights(*lights)
                return
        elif s in (ui.SENDING, ui.RECEIVING) and self.engine.state in (am_proto.SEND, am_proto.RECV):
            level = 0.2 + 0.3 * (0.5 + 0.5 * math.sin(now / 160))
        elif s == ui.OFFER_IN:
            level = 0.6 if (now // 500) % 2 else 0.15
        elif s == ui.DONE:
            self.light = max(0.0, self.light - dt * 0.8)
            level = self.light
        badge.caselights(level)


# badge.ticks is frozen for the whole frame (measured), so the engine's per-frame
# budget needs a clock that moves while we work.
_clock = getattr(time, "ticks_ms", None)
_diff = getattr(time, "ticks_diff", None)
if _clock is None:
    def _clock():
        return int(time.time() * 1000)
if _diff is None:
    def _diff(a, b):
        return a - b


app = App(badge.ticks)
_last = [badge.ticks]


def update():
    now = badge.ticks
    dt = (now - _last[0]) / 1000
    _last[0] = now
    if dt > 0.1:
        dt = 0.1
    app.update(now)
    ui.draw(app, now)
    app.lights(now, dt)


def on_exit():
    app.close()
    badge.caselights(0)
    badge.default_clear = previous_clear


run(update)
