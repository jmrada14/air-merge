"""Air Merge desktop runner: one simulated badge.

    python3 -I sim/run.py --frames 600 --fs-root sim/out/fs/solo \
        --input "30:DOWN 40:SELECT" --shots 1,40,100 --sheet out/sheet.png

    # one of two linked badges (see share.py, which launches both):
    python3 -I sim/run.py --fs-root sim/out/share/a --realtime --epoch T \
        --link-port 40001 --peer-port 40002 --badge-name Ada \
        --pilot sim/pilot.py --pilot-opt role=send --pilot-opt app=mona_kart

Frames are numbered from 1. Input tokens: `F:BTN` taps BTN on frame F (held for
that one frame), `A-B:BTN` holds BTN from frame A through B inclusive. Edges
(pressed/released) are derived from the held set of consecutive frames.

The app sees a sandbox filesystem (`--fs-root`, seeded with fixture apps by
sim/seed.py when it has no system/apps yet), fake `vfs`/`rp2` modules that keep
/system read-only unless remounted (fake_fs.py) and a fake Bluetooth link with
measured capacity and throughput (fake_link.py). A pilot (`--pilot FILE`) can
drive the buttons from the app's state instead of a fixed input script.
"""

import argparse
import importlib.util
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
import fake_fs  # noqa: E402
import fake_link  # noqa: E402
import png  # noqa: E402
import seed as seeder  # noqa: E402

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

def _stat(values):
    if not values:
        return "n/a"
    s = sorted(values)
    p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
    return "avg %.2f  p95 %.2f  max %.2f" % (sum(s) / len(s), p95, s[-1])


def load_pilot(path, opts):
    """Load a pilot module (a .py file defining Pilot(opts))."""
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location("sim_pilot_%d" % abs(hash(path)), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Pilot(opts)


def parse_opts(items):
    out = {}
    for it in items or ():
        for part in it.split(","):
            if not part.strip():
                continue
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip() if _ else "1"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a badge app in the desktop simulator.")
    ap.add_argument("--app", default=os.path.join(os.path.dirname(HERE), "app"),
                    help="app folder containing __init__.py (default: app)")
    ap.add_argument("--slug", default="air_merge",
                    help="folder name of the app on the badge (/system/apps/<slug>)")
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
    ap.add_argument("--start-ticks", type=int, default=5000,
                    help="badge.ticks before frame 1 (non-zero like a real boot)")
    ap.add_argument("--seed", type=int, default=1, help="random seed (deterministic runs)")
    ap.add_argument("--state-dir", default=None,
                    help="State json folder (default <fs-root>/state)")
    ap.add_argument("--fresh-state", action="store_true", help="delete saved State before running")
    ap.add_argument("--realtime", action="store_true",
                    help="pace frames in wall-clock time (frame N runs at epoch + N*dt/pace)")
    ap.add_argument("--pace", type=float, default=1.0,
                    help="with --realtime: badge ms per wall ms (2 = twice as fast)")
    ap.add_argument("--epoch", type=float, default=0.0,
                    help="with --realtime: wall time.time() of frame 1 (shared by two-badge runs)")
    # filesystem
    ap.add_argument("--fs-root", default=os.path.join(HERE, "out", "fs", "solo"),
                    help="sandbox folder standing in for the badge's / (seeded if empty)")
    ap.add_argument("--system-free", type=float, default=None, metavar="KB",
                    help="free space statvfs reports for /system at start (default %d)"
                    % fake_fs.DEFAULT_SYSTEM_FREE_KB)
    ap.add_argument("--stage-free", type=float, default=None, metavar="KB",
                    help="free space statvfs reports for / (staging) at start (default %d)"
                    % fake_fs.DEFAULT_STAGE_FREE_KB)
    ap.add_argument("--msc-busy", action="store_true",
                    help="rp2.is_msc_busy() returns True (USB disk mode in use)")
    # link
    ap.add_argument("--link-port", type=int, default=None,
                    help="fake badge link: this badge's UDP port on 127.0.0.1")
    ap.add_argument("--peer-port", type=int, default=None,
                    help="fake badge link: the other badge's UDP port (omit: never links)")
    ap.add_argument("--badge-name", default="sim badge", help="fake badge link: owner name")
    ap.add_argument("--badge-id", default="5ba00000000000a1",
                    help="fake badge link: badge id as hex (8 bytes on the badge)")
    ap.add_argument("--link-mute-at", type=int, default=None,
                    help="fake badge link: go silent from this badge.ticks on (walk away)")
    ap.add_argument("--link-mute-frame", type=int, default=None,
                    help="fake badge link: go silent from this frame on (walk away)")
    ap.add_argument("--link-kbps", type=float, default=fake_link.KBPS_DEFAULT,
                    help="fake badge link: throughput in KB/s of badge time")
    ap.add_argument("--link-mtu", type=int, default=fake_link.MTU_DEFAULT,
                    help="fake badge link: negotiated MTU (capacity = mtu - 4)")
    # pilot
    ap.add_argument("--pilot", default=None,
                    help="python file with a Pilot class that drives buttons from app state")
    ap.add_argument("--autopilot", action="store_true", help="shorthand for --pilot sim/pilot.py")
    ap.add_argument("--pilot-opt", action="append", default=[],
                    help="k=v options for the pilot (repeatable or comma separated)")
    ap.add_argument("--log-state", action="store_true", default=True,
                    help="print the app state whenever it changes (default on)")
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

    fs_root = os.path.abspath(args.fs_root)
    if not os.path.isdir(os.path.join(fs_root, "system", "apps")):
        got = seeder.seed(fs_root, ["hello_badge", "pixel_garden", "mona_kart"])
        print("seeded %s with %s" % (os.path.relpath(fs_root), ", ".join(got)))

    random.seed(args.seed)
    state_dir = args.state_dir or os.path.join(fs_root, "state")
    sim = badge_sim.install(app_dir, script=args.input, state_dir=state_dir, slug=args.slug)
    if args.fresh_state and os.path.isdir(badge_sim.State.dir):
        for name in os.listdir(badge_sim.State.dir):
            if name.endswith(".json"):
                os.remove(os.path.join(badge_sim.State.dir, name))
    screen, badge = sim.screen, sim.badge
    mute = args.link_mute_at
    if args.link_mute_frame is not None:
        mute = args.start_ticks + args.link_mute_frame * args.dt
    fake_link.install(badge, port=args.link_port, peer=args.peer_port, name=args.badge_name,
                      id_=bytes.fromhex(args.badge_id), mute_at=mute,
                      kbps=args.link_kbps, mtu=args.link_mtu)
    badge.ticks = args.start_ticks
    held_script = parse_input(args.input)
    shots = set(parse_frames(args.shots))
    pilot_path = args.pilot or (os.path.join(HERE, "pilot.py") if args.autopilot else None)
    pilot = None
    if pilot_path:
        opts = parse_opts(args.pilot_opt)
        opts.setdefault("dt", str(args.dt))
        pilot = load_pilot(pilot_path, opts)

    saved = []
    times = []
    counts = {k: [] for k in ("rects", "tris", "circles", "lines", "texts", "pixels")}
    ns = {}
    status = 0
    frame = 0
    stop_reason = "frame limit"
    last_state = None

    def save(frame_no, tag=None):
        path = os.path.join(out_dir, "frame_%04d%s.png" % (frame_no, tag or ""))
        png.write_png(path, screen.width, screen.height, screen.fb, scale=args.scale)
        saved.append((frame_no, path))
        return path

    # the filesystem goes last: from here on writes under <fs-root>/system are checked
    fake_fs.install(fs_root, badge, app_dir=app_dir, system_free_kb=args.system_free,
                    stage_free_kb=args.stage_free, msc_busy=args.msc_busy)

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
    late = 0
    try:
        for frame in range(1, args.frames + 1):
            if args.realtime:
                wait = epoch + (frame - 1) * args.dt / 1000.0 / pace - time.time()
                if wait > 0:
                    time.sleep(wait)
                elif wait < -0.25:
                    late += 1
            mask = held_script.get(frame, 0)
            if pilot is not None:
                mask = pilot.held(ns, frame, mask)
                for msg in pilot.drain_events():
                    print("frame %5d  pilot: %s" % (frame, msg))
            badge.set_held(mask)
            badge.ticks_delta = args.dt if frame > 1 else 0
            badge.ticks += badge.ticks_delta
            screen.reset_counts()
            t = time.perf_counter()
            result = update()
            times.append((time.perf_counter() - t) * 1000)
            for k in counts:
                counts[k].append(screen.counts[k])
            if pilot is not None and args.log_state:
                st = pilot.describe(ns)
                if st != last_state:
                    print("frame %5d  ticks %d  state %s" % (frame, badge.ticks, st))
                    last_state = st
            if frame in shots or (args.every and frame % args.every == 0):
                save(frame)
            if result is not None:
                stop_reason = "update() returned %r" % (result,)
                break
            if pilot is not None and pilot.finished(ns, frame):
                stop_reason = "pilot finished"
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

    if pilot is not None:
        for line in pilot.summary(ns):
            print("pilot: %s" % line)
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
    if late:
        print("realtime: %d frame(s) started more than 250 ms late" % late)
    slow = [(i + 1, t) for i, t in enumerate(times) if t > 100]
    if slow:
        print("slow frames (>100 ms of CPython time): %s" % ", ".join(
            "#%d %.0f ms" % ft for ft in sorted(slow, key=lambda ft: -ft[1])[:8]))
    for k, v in counts.items():
        print("  %-8s per frame: %s" % (k, _stat(v).replace(".00", "")))
    print("caselights last: %r (%d calls)" % (badge.last_caselights, badge.caselight_calls))
    for line in fake_link.report():
        print(line)
    for line in fake_fs.report():
        print(line)
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
