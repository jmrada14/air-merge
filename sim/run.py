"""Mona Kart desktop runner.

    python3 -I sim/run.py --app app --frames 1800 --dt 33 \
        --input "30:SELECT 60:RIGHT 90:SELECT 120-900:LEFT 400:SELECT" \
        --shots 1,40,100,200,400 --sheet out/sheet.png --autopilot

Frames are numbered from 1. Input tokens: `F:BTN` taps BTN on frame F (held for
that one frame), `A-B:BTN` holds BTN from frame A through B inclusive. Edges
(pressed/released) are derived from the held set of consecutive frames.
"""

import argparse
import os
import random
import runpy
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:  # `python3 -I` drops the script dir from sys.path
    sys.path.insert(0, HERE)
sys.dont_write_bytecode = True  # never leave __pycache__ in app/ (it gets copied to the badge)

import badge_sim  # noqa: E402
import fake_link  # noqa: E402
import png  # noqa: E402

BUTTONS = badge_sim.BUTTONS


def parse_input(script):
    """-> dict frame -> held bitmask."""
    held = {}
    if not script:
        return held
    for tok in script.replace(",", " ").split():
        try:
            frames, name = tok.split(":")
            mask = BUTTONS[name.strip().upper()]
            if "-" in frames:
                a, b = frames.split("-")
                a, b = int(a), int(b)
            else:
                a = b = int(frames)
        except (ValueError, KeyError):
            raise SystemExit("bad input token %r (want F:BTN or A-B:BTN, BTN in %s)"
                             % (tok, " ".join(BUTTONS)))
        for f in range(a, b + 1):
            held[f] = held.get(f, 0) | mask
    return held


def parse_frames(spec):
    out = []
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


# ----------------------------------------------------------------------------
# autopilot
# ----------------------------------------------------------------------------

def _looks_like_race(obj):
    return obj is not None and hasattr(obj, "racers") and hasattr(obj, "player")


def find_race(ns):
    """Look for a Race reachable from the app namespace (race, game.race, state.race, ...)."""
    for key in ("race", "game", "state", "app", "GAME", "STATE"):
        obj = ns.get(key)
        if obj is None:
            continue
        if _looks_like_race(obj):
            return obj
        if isinstance(obj, dict):
            r = obj.get("race")
        else:
            r = getattr(obj, "race", None)
        if _looks_like_race(r):
            return r
    for obj in list(ns.values()):
        if _looks_like_race(obj):
            return obj
        r = getattr(obj, "race", None) if not isinstance(obj, type) else None
        if _looks_like_race(r):
            return r
    return None


def find_player(ns):
    if "kart_racers" not in sys.modules:
        return None, None
    race = find_race(ns)
    if race is None:
        return None, None
    p = getattr(race, "player", None)
    if p is None or not getattr(p, "is_player", False):
        for r in getattr(race, "racers", ()):
            if getattr(r, "is_player", False):
                p = r
                break
        else:
            return race, None
    return race, p


class Autopilot:
    def __init__(self, rng):
        self.rng = rng
        self.active_frames = 0

    def held(self, ns, frame_held):
        """Return a held mask that replaces LEFT/RIGHT/SELECT while racing."""
        race, p = find_player(ns)
        if p is None or not getattr(race, "started", True) or getattr(p, "finished", False):
            return frame_held
        game = ns.get("game")
        versus = getattr(game, "versus", None) if game is not None else None
        if versus is not None and getattr(versus, "phase", 3) != 3:
            return frame_held  # versus results etc.: the race object lingers, don't drive

        self.active_frames += 1
        mask = frame_held & ~(BUTTONS["LEFT"] | BUTTONS["RIGHT"])
        target = 0.0
        track = sys.modules.get("kart_track")
        T = getattr(track, "TRACK", None) if track else None
        if T is not None and hasattr(T, "curve_at"):
            try:
                ahead = T.curve_at(p.z + 1200)
                target = max(-0.4, min(0.4, 0.06 * ahead))  # hug the inside of curves
            except Exception:
                target = 0.0
        x = getattr(p, "x", 0.0)
        band = 0.3
        if T is not None:
            try:
                target, band = self._pickups(T, p, target, band)
            except Exception:
                pass
        if x > target + band:
            mask |= BUTTONS["LEFT"]
        elif x < target - band:
            mask |= BUTTONS["RIGHT"]
        if getattr(p, "tokens", 0) > 0 and self.rng.random() < 0.02:
            mask |= BUTTONS["SELECT"]
        return mask

    @staticmethod
    def _pickups(T, p, target, band):
        """Dodge active bugs and drift towards active tokens 2..14 segments ahead."""
        seg = T.segment_index(p.z)
        best = None
        for pk in getattr(T, "pickups", ()):
            if getattr(pk, "respawn_at", 0):
                continue
            ahead = (pk.index - seg) % T.count
            if 2 <= ahead <= 14 and (best is None or ahead < best[0]):
                best = (ahead, pk)
        if best is None:
            return target, band
        pk = best[1]
        if pk.kind == "bug":
            if abs(pk.x - target) < 0.4:
                target = pk.x - 0.45 if pk.x > 0 else pk.x + 0.45
            return target, 0.15
        return pk.x, 0.1


def versus_state(ns):
    """(mode, versus phase, outcome) of the app's Game, for logging; None if absent."""
    game = ns.get("game")
    if game is None:
        return None
    v = getattr(game, "versus", None)
    if v is None:
        return (getattr(game, "mode", None), None, None)
    return (getattr(game, "mode", None), getattr(v, "phase", None), getattr(v, "outcome", None))


def versus_summary(ns):
    game = ns.get("game")
    v = getattr(game, "versus", None) if game is not None else None
    if v is None:
        return None
    race = getattr(v, "race", None)
    mine = race.player.finish_ms if race is not None else None
    return "phase %s outcome %s my_finish %s rival_finish %s round %s offset %s rtt %s" % (
        v.phase, v.outcome, mine, v.rival_finish, v.round,
        None if v.offset is None else round(v.offset), v.best_rtt)


def player_summary(ns):
    race, p = find_player(ns)
    if p is None:
        return None
    return "lap %s place %s z %.0f x %+.2f speed %.0f tokens %s%s" % (
        getattr(p, "lap", "?"), getattr(p, "place", "?"), getattr(p, "z", 0.0),
        getattr(p, "x", 0.0), getattr(p, "speed", 0.0), getattr(p, "tokens", "?"),
        " FINISHED %sms" % p.finish_ms if getattr(p, "finished", False) else "")


# ----------------------------------------------------------------------------

def _stat(values):
    if not values:
        return "n/a"
    s = sorted(values)
    p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
    return "avg %.2f  p95 %.2f  max %.2f" % (sum(s) / len(s), p95, s[-1])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a badge app in the desktop simulator.")
    ap.add_argument("--app", default=os.path.join(os.path.dirname(HERE), "app"),
                    help="app folder containing __init__.py (default: app)")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--dt", type=int, default=33, help="ms per frame")
    ap.add_argument("--input", default="", help='e.g. "30:SELECT 120-900:LEFT"')
    ap.add_argument("--shots", default="", help="frames to save, e.g. 1,40,100-105")
    ap.add_argument("--every", type=int, default=0, help="also save every Nth frame")
    ap.add_argument("--out", default=os.path.join(HERE, "out"), help="screenshot folder")
    ap.add_argument("--sheet", default="",
                    help="contact sheet path (relative paths resolve against sim/)")
    ap.add_argument("--cols", type=int, default=3, help="contact sheet columns")
    ap.add_argument("--scale", type=int, default=1, help="integer upscale for saved PNGs")
    ap.add_argument("--autopilot", action="store_true", help="steer the player during races")
    ap.add_argument("--start-ticks", type=int, default=5000,
                    help="badge.ticks before frame 1 (non-zero like a real boot)")
    ap.add_argument("--seed", type=int, default=1, help="random seed (deterministic runs)")
    ap.add_argument("--state-dir", default=None, help="State json folder (default sim/out/state)")
    ap.add_argument("--fresh-state", action="store_true", help="delete saved State before running")
    ap.add_argument("--log-every", type=int, default=300,
                    help="print player status every N frames when a race is found (0 = off)")
    ap.add_argument("--realtime", action="store_true",
                    help="pace frames in wall-clock time (frame N runs at epoch + N*dt/pace)")
    ap.add_argument("--pace", type=float, default=1.0,
                    help="with --realtime: badge ms per wall ms (2 = twice as fast)")
    ap.add_argument("--epoch", type=float, default=0.0,
                    help="with --realtime: wall time.time() of frame 1 (shared by versus runs)")
    ap.add_argument("--link-port", type=int, default=None,
                    help="fake badge link: this badge's UDP port on 127.0.0.1")
    ap.add_argument("--peer-port", type=int, default=None,
                    help="fake badge link: the other badge's UDP port (omit: never links)")
    ap.add_argument("--badge-name", default="sim badge", help="fake badge link: owner name")
    ap.add_argument("--badge-id", default="5ba0000000a1",
                    help="fake badge link: badge id as hex")
    ap.add_argument("--link-mute-at", type=int, default=None,
                    help="fake badge link: go silent from this badge.ticks on (walk away)")
    args = ap.parse_args(argv)

    app_dir = os.path.abspath(args.app)
    init = os.path.join(app_dir, "__init__.py")
    if not os.path.exists(init):
        print("no __init__.py in %s" % app_dir)
        return 2
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.startswith("frame_") and name.endswith(".png"):
            os.remove(os.path.join(out_dir, name))

    random.seed(args.seed)
    sim = badge_sim.install(app_dir, script=args.input, state_dir=args.state_dir)
    if args.fresh_state and os.path.isdir(badge_sim.State.dir):
        for name in os.listdir(badge_sim.State.dir):
            if name.endswith(".json"):
                os.remove(os.path.join(badge_sim.State.dir, name))
    screen, badge = sim.screen, sim.badge
    fake_link.install(badge, port=args.link_port, peer=args.peer_port, name=args.badge_name,
                      id_=bytes.fromhex(args.badge_id), mute_at=args.link_mute_at)
    badge.ticks = args.start_ticks
    held_script = parse_input(args.input)
    shots = set(parse_frames(args.shots))
    autopilot = Autopilot(random.Random(args.seed + 1)) if args.autopilot else None

    saved = []
    times = []
    last_state = None
    counts = {k: [] for k in ("rects", "tris", "circles", "lines", "texts", "pixels")}
    ns = {}
    events = {}
    status = 0
    frame = 0
    stop_reason = "frame limit"

    def save(frame_no, tag=None):
        path = os.path.join(out_dir, "frame_%04d%s.png" % (frame_no, tag or ""))
        png.write_png(path, screen.width, screen.height, screen.fb, scale=args.scale)
        saved.append((frame_no, path))
        return path

    t0 = time.perf_counter()
    try:
        ns = runpy.run_path(init, run_name="__main__")
    except badge_sim.AppExit:
        print("app called reset() during import")
        return 0
    except BaseException:
        sys.stdout.flush()
        traceback.print_exc()
        print("ERROR while importing app")
        return 1
    import_ms = (time.perf_counter() - t0) * 1000
    update = sim.update or ns.get("update")
    if update is None:
        print("app never called run(update) and defines no update()")
        return 1
    print("app imported in %.0f ms (run() called %d time(s))" % (import_ms, sim.run_calls))

    epoch = args.epoch or time.time()
    pace = args.pace if args.pace > 0 else 1.0
    try:
        for frame in range(1, args.frames + 1):
            if args.realtime:
                wait = epoch + (frame - 1) * args.dt / 1000.0 / pace - time.time()
                if wait > 0:
                    time.sleep(wait)
            mask = held_script.get(frame, 0)
            if autopilot is not None:
                mask = autopilot.held(ns, mask)
            badge.set_held(mask)
            badge.ticks_delta = args.dt if frame > 1 else 0
            badge.ticks += badge.ticks_delta
            screen.reset_counts()
            t = time.perf_counter()
            result = update()
            times.append((time.perf_counter() - t) * 1000)
            for k in counts:
                counts[k].append(screen.counts[k])
            race = find_race(ns) if "kart_racers" in sys.modules else None
            for ev in getattr(race, "events", None) or ():
                events[ev] = events.get(ev, 0) + 1
            st = versus_state(ns)
            if st is not None and st != last_state and st[1] is not None:
                print("frame %5d  ticks %d  mode %s phase %s outcome %s" % ((frame, badge.ticks) + st))
            last_state = st
            if frame in shots or (args.every and frame % args.every == 0):
                save(frame)
            if args.log_every and frame % args.log_every == 0:
                s = player_summary(ns)
                if s:
                    print("frame %5d  %s" % (frame, s))
            if result is not None:
                stop_reason = "update() returned %r" % (result,)
                break
    except badge_sim.AppExit:
        stop_reason = "reset() called"
    except BaseException:
        sys.stdout.flush()
        traceback.print_exc()
        print("ERROR on frame %d (ticks %d); partial frame saved" % (frame, badge.ticks))
        try:
            save(frame, "_error")
        except Exception:
            pass
        status = 1
        stop_reason = "exception"

    vs = versus_summary(ns)  # before on_exit(), which closes the link
    on_exit = ns.get("on_exit")
    if callable(on_exit) and status == 0:
        try:
            on_exit()
        except BaseException:
            sys.stdout.flush()
            traceback.print_exc()
            print("ERROR in on_exit()")
            status = 1

    n = len(times)
    print("ran %d frames (%s), %d ms of badge time" % (n, stop_reason, badge.ticks))
    print("python ms/frame: %s" % _stat(times))
    for k, v in counts.items():
        print("  %-8s per frame: %s" % (k, _stat(v).replace(".00", "")))
    print("caselights last: %r (%d calls)" % (badge.last_caselights, badge.caselight_calls))
    if events:
        print("race events: %s" % ", ".join("%s x%d" % kv for kv in sorted(events.items())))
    if autopilot is not None:
        print("autopilot active on %d frames" % autopilot.active_frames)
    if fake_link.CFG.sent or fake_link.CFG.received:
        print("link: %d messages sent (largest %d bytes), %d received"
              % (fake_link.CFG.sent, fake_link.CFG.max_len, fake_link.CFG.received))
    s = player_summary(ns)
    if s:
        print("player: %s" % s)
    if vs:
        print("versus: %s" % vs)
    for f, p in saved:
        print("saved %s" % os.path.relpath(p))

    if args.sheet and saved:
        sheet = args.sheet if os.path.isabs(args.sheet) else os.path.join(HERE, args.sheet)
        png.contact_sheet([p for _, p in saved], args.cols, sheet,
                          labels=["#%d" % f for f, _ in saved])
        print("sheet %s" % os.path.relpath(sheet))
    return status


if __name__ == "__main__":
    sys.exit(main())
