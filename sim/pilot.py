"""State-driven button pilot for Air Merge simulator runs (run.py --pilot sim/pilot.py).

Instead of a fixed input script with frame numbers, the pilot looks at the running
app's state every frame and presses what a person would:

    role=send   app=<slug>   on the app list: move the cursor to <slug> (UP/DOWN) and
                             press SELECT; on the confirmation: SELECT
    role=recv   answer=accept|decline
                             on an incoming offer: SELECT (accept) or BACK (decline)

Every press waits until the current screen has been visible for `hold_ms` (default
1200 ms of badge time) so screenshots show each screen, and buttons are released
between presses so `badge.pressed()` edges fire.

The run ends (`finished()`) `linger` frames (default 60) after the flow is over:
DONE reached, or a send/receive attempt that ended back on the list or in the lobby,
or a link that went away after it had been up.

The probe finds the app object (module global `app` with a `screen` attribute),
maps its screen constant (am_ui) and its engine state (am_proto) to the DESIGN.md
state names (LOBBY, PAIRED, CONFIRM_SEND, OFFERED, OFFER_IN, SENDING, RECEIVING,
VERIFYING, INSTALLING, DONE, plus WAITING, OFFER_REFUSED, FAILED), and reads the app
list (dicts with "slug"), the cursor index and on-screen messages. If the app's
names change, adjust the tables at the top (SCREEN_MAP etc.).
"""

import sys

# --- how the app is read (adjust here if the app changes) -------------------------
# The app keeps an `App` instance in the module global `app`; its `screen` is one of
# the am_ui screen constants and `app.engine.state` one of the am_proto engine states.
# (screen constant name, engine state name or None, app.result ok or None) -> state
# name used in logs and by share.py (the DESIGN.md names, plus a few extras).
APP_GLOBALS = ("app", "APP", "ui_app", "game")
SCREEN_ATTRS = ("screen", "state", "mode", "page", "scene")
ENGINE_ATTRS = ("engine", "eng", "proto")
LIST_ATTRS = ("apps", "items", "app_list", "choices", "entries")
CURSOR_ATTRS = ("sel", "cursor", "index", "selected", "idx", "pos")
SCREEN_MAP = {
    # current app (am_ui uses the DESIGN.md names)
    "NOLINK": "NOLINK",
    "LOBBY": "LOBBY",
    "PAIRED": "PAIRED",
    "CONFIRM_SEND": "CONFIRM_SEND",
    "OFFERED": "OFFERED",
    "SENDING": {"SEND": "SENDING", "AWAIT": "WAITING", None: "SENDING"},
    "OFFER_IN": "OFFER_IN",
    "OFFER_BAD": "OFFER_REFUSED",
    "RECEIVING": "RECEIVING",
    "VERIFYING": "VERIFYING",
    "INSTALLING": "INSTALLING",
    "DONE": {True: "DONE", False: "FAILED"},     # by app.result["ok"]
    # older screen names
    "LIST": "PAIRED",
    "CONFIRM": "CONFIRM_SEND",
    "OUT": {"PREP": "OFFERED", "OFFER": "OFFERED", "SEND": "SENDING", "AWAIT": "WAITING",
            None: "OFFERED"},
    "IN_ASK": "OFFER_IN",
    "IN_BAD": "OFFER_REFUSED",
    "IN": {"RECV": "RECEIVING", "VERIFY": "VERIFYING", "INSTALL": "INSTALLING",
           None: "RECEIVING"},
    "RESULT": {True: "DONE", False: "FAILED"},
}
ENGINE_NAMES = ("IDLE", "PREP", "OFFER", "SEND", "AWAIT", "LOAD", "ASK", "RECV", "VERIFY",
                "INSTALL")
UI_MODULES = ("am_ui", "__main__")
ENGINE_MODULES = ("am_proto",)

STATE_NAMES = ("LOBBY", "PAIRED", "CONFIRM_SEND", "OFFERED", "OFFER_IN", "SENDING",
               "RECEIVING", "VERIFYING", "INSTALLING", "WAITING", "OFFER_REFUSED", "DONE",
               "FAILED", "NOLINK")
# states that are part of one send / receive attempt
FLOW = ("CONFIRM_SEND", "OFFERED", "OFFER_IN", "SENDING", "RECEIVING", "VERIFYING",
        "INSTALLING", "WAITING", "OFFER_REFUSED")

BTN = {"UP": 1, "DOWN": 2, "LEFT": 4, "RIGHT": 8, "SELECT": 16, "BACK": 32,
       "HOME": 64, "MENU": 128}


def _get(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _const_names(modnames, wanted):
    """value -> NAME for module-level constants whose NAME is in `wanted`."""
    out = {}
    for mn in modnames:
        m = sys.modules.get(mn)
        if m is None:
            continue
        for k, v in list(vars(m).items()):
            if k in wanted and isinstance(v, (int, str)) and v not in out:
                out[v] = k
    return out


class Probe:
    """Finds the app object and reads its screen, engine state, list and messages."""

    def __init__(self):
        self.app = None
        self.screen_attr = None
        self.screens = None
        self.engines = None

    def find(self, ns):
        if self.app is not None:
            return self.app
        cands = [ns.get(k) for k in APP_GLOBALS if ns.get(k) is not None]
        cands += [v for k, v in list(ns.items()) if not k.startswith("__")
                  and hasattr(v, "__dict__") and not isinstance(v, type)
                  and type(v).__name__ != "module"]
        screens = _const_names(UI_MODULES, SCREEN_MAP)
        for obj in cands:
            for attr in SCREEN_ATTRS:
                v = _get(obj, attr)
                if isinstance(v, (int, str)) and not isinstance(v, bool) and (
                        v in screens or str(v).upper() in SCREEN_MAP):
                    self.app = obj
                    self.screen_attr = attr
                    self.screens = screens
                    self.engines = _const_names(ENGINE_MODULES, ENGINE_NAMES)
                    return obj
        return None

    def engine(self):
        for a in ENGINE_ATTRS:
            e = _get(self.app, a)
            if e is not None:
                return e
        return None

    def raw(self, ns):
        """(screen name, engine state name) or (None, None)."""
        if self.find(ns) is None:
            return None, None
        v = _get(self.app, self.screen_attr)
        scr = self.screens.get(v, str(v).upper())
        e = self.engine()
        est = None
        if e is not None:
            ev = _get(e, "state")
            est = self.engines.get(ev, None if ev is None else str(ev))
        return scr, est

    def state(self, ns):
        scr, est = self.raw(ns)
        if scr is None:
            return None
        m = SCREEN_MAP.get(scr, scr)
        if isinstance(m, dict):
            if True in m:
                r = _get(self.app, "result")
                ok = bool(_get(r, "ok")) if r is not None else False
                return m.get(ok, scr)
            return m.get(est, m.get(None, scr))
        return m

    def apps(self, ns):
        if self.find(ns) is None:
            return None
        for a in LIST_ATTRS:
            v = _get(self.app, a)
            if isinstance(v, list) and (not v or isinstance(v[0], dict) and "slug" in v[0]):
                return v
        return None

    def cursor(self, ns):
        if self.find(ns) is None:
            return None
        for a in CURSOR_ATTRS:
            v = _get(self.app, a)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
        return None

    def texts(self, ns):
        """Messages on screen: toast, result title/lines, auto-decline reason."""
        if self.find(ns) is None:
            return []
        out = []
        a = self.app
        for name in ("msg", "message", "note", "toast", "error"):
            v = _get(a, name)
            if isinstance(v, str) and v:
                out.append(v)
        r = _get(a, "result")
        if r is not None and _get(r, "title"):
            out.append("%s: %s" % (_get(r, "title"), " / ".join(_get(r, "lines") or ())))
        bad = _get(a, "bad")
        if isinstance(bad, (list, tuple)) and len(bad) == 2 and isinstance(bad[1], str):
            out.append("refused: " + bad[1])
        return out


class Pilot:
    def __init__(self, opts):
        self.role = opts.get("role", "recv")
        self.app = opts.get("app", "mona_kart")
        self.answer = opts.get("answer", "accept")
        self.expect = opts.get("expect", "")
        self.dt = int(opts.get("dt", "33"))
        self.hold = max(1, int(int(opts.get("hold_ms", "1200")) / self.dt))
        self.linger = int(opts.get("linger", "60"))
        self.probe = Probe()
        self.events = []
        self.cur = None
        self.since = 0
        self.seen = []
        self.last_press = -1000
        self.end_at = None
        self.flow_done = False
        self.texts = set()
        self.presses = 0
        self.pressed_select_on_offer = False

    # -- helpers -------------------------------------------------------------
    def drain_events(self):
        ev = self.events
        self.events = []
        return ev

    def _ev(self, msg):
        self.events.append(msg)

    def describe(self, ns):
        st = self.probe.state(ns)
        if st is None:
            return "?"
        scr, est = self.probe.raw(ns)
        return "%s (screen %s, engine %s)" % (st, scr, est)

    def _press(self, frame, name, why):
        if frame - self.last_press < 4:
            return 0
        self.last_press = frame
        self.presses += 1
        self._ev("press %s (%s)" % (name, why))
        return BTN[name]

    # -- per frame -----------------------------------------------------------
    def held(self, ns, frame, mask):
        st = self.probe.state(ns)
        if st != self.cur:
            prev = self.cur
            self.cur = st
            self.since = frame
            self.seen.append(st)
            self._transition(prev, st, frame)
        for t in self.probe.texts(ns):
            if t not in self.texts:
                self.texts.add(t)
                self._ev("text %s" % t.replace("\n", " / "))
        if st is None or self.flow_done:
            return mask
        if frame - self.since < self.hold or frame - self.last_press < 6:
            return mask
        if self.role == "send":
            return mask | self._send(ns, st, frame)
        return mask | self._recv(ns, st, frame)

    def _transition(self, prev, st, frame):
        if st == "DONE":
            self._finish(frame, "done")
        elif st == "FAILED":
            self._finish(frame, "failed / declined")
        elif prev in FLOW and st in ("PAIRED", "LOBBY"):
            self._finish(frame, "flow ended in %s" % st)
        elif st == "LOBBY" and "PAIRED" in self.seen[:-1]:
            self._finish(frame, "link went away")

    def _finish(self, frame, why):
        if self.end_at is None:
            self.flow_done = True
            self.end_at = frame + self.linger
            self._ev("flow over (%s); stopping at frame %d" % (why, self.end_at))

    def _send(self, ns, st, frame):
        if st == "PAIRED":
            if "OFFERED" in self.seen or "CONFIRM_SEND" in self.seen:
                return 0   # one attempt only
            apps = self.probe.apps(ns)
            cur = self.probe.cursor(ns)
            if apps is None or cur is None:
                return 0
            slugs = [a.get("slug") for a in apps]
            if self.app not in slugs:
                self._ev("app %s not in the list %s" % (self.app, slugs))
                self._finish(frame, "app missing")
                return 0
            want = slugs.index(self.app)
            if cur < want:
                return self._press(frame, "DOWN", "cursor %d -> %s at %d" % (cur, self.app, want))
            if cur > want:
                return self._press(frame, "UP", "cursor %d -> %s at %d" % (cur, self.app, want))
            return self._press(frame, "SELECT", "send %s" % self.app)
        if st == "CONFIRM_SEND":
            return self._press(frame, "SELECT", "confirm")
        return 0

    def _recv(self, ns, st, frame):
        if st == "OFFER_IN":
            if self.pressed_select_on_offer:
                return 0
            self.pressed_select_on_offer = True
            if self.answer == "decline":
                return self._press(frame, "BACK", "decline the offer")
            return self._press(frame, "SELECT", "accept the offer")
        return 0

    def finished(self, ns, frame):
        return self.end_at is not None and frame >= self.end_at

    def summary(self, ns):
        return ["role %s, %d presses, states seen: %s" % (
            self.role, self.presses, " ".join(s or "?" for s in self.seen))]
