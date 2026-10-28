"""Two-badge Air Merge run: badge A sends an app to badge B over the fake link.

    python3 -I sim/share.py                      # A sends mona_kart to B, B accepts
    python3 -I sim/share.py --app pixel_garden   # nested folders, junk files skipped
    python3 -I sim/share.py --replace            # A sends hello_badge over B's older copy
    python3 -I sim/share.py --decline            # B presses BACK on the offer
    python3 -I sim/share.py --drop-at 400        # A walks away mid-transfer (frame 400)
    python3 -I sim/share.py --drop-at 680 --drop-who b   # B leaves during the install
    python3 -I sim/share.py --small-stage        # B's staging space is too small
    python3 -I sim/share.py --small-system       # B's /system is too full
    python3 -I sim/share.py --msc-busy           # B is in USB disk mode
    python3 -I sim/share.py --mtu 23             # link capacity stays at 19 bytes
    python3 -I sim/share.py --all                # every scenario, one after another

Both badges get a fresh sandbox (sim/out/share/a and b, seeded by seed.py: A has
hello_badge, pixel_garden and mona_kart; B has an older hello_badge only), then two
run.py processes are paced in real time from a shared epoch and linked over
localhost UDP. Buttons are driven by sim/pilot.py from each app's state (A picks
the app and confirms, B accepts or declines), so the run doesn't depend on screen
layout or exact frame numbers. Afterwards the sandboxes are checked:

- success: B's /system/apps/<slug> equals A's byte for byte (same tree, hidden and
  __pycache__ entries excluded on A's side and absent on B's), B's /system is
  read-only again, no staging leftovers (files under /air_merge, .am_new_/.am_old_
  folders), B's other apps, secrets.py and State files untouched, A's filesystem
  untouched and A never unlocked /system.
- decline / drop / small stage / small system / USB disk mode: B's filesystem is
  unchanged and has no leftovers, and the reason is shown where there is one.

A contact sheet of matching frames (A left, B right) goes to sim/out/share_sheet.png
(sim/out/share_<scenario>_sheet.png with --all). Exit code 0 only if every check passes.
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import png  # noqa: E402
import seed as seeder  # noqa: E402

RUN = os.path.join(HERE, "run.py")
PILOT = os.path.join(HERE, "pilot.py")
APP = os.path.join(os.path.dirname(HERE), "app")
OUT = os.path.join(HERE, "out")

APPS_A = ["hello_badge", "pixel_garden", "mona_kart"]
APPS_B = ["hello_badge_old"]          # installed on B as hello_badge (older, different)
NAME_A, ID_A = "Ada", "a1a2a3a4a5a6a7a8"
NAME_B, ID_B = "Bo", "b8b7b6b5b4b3b2b1"

# Extra pilot options (see sim/pilot.py); adjust here if the app's flow changes.
PILOT_A = {"role": "send"}
PILOT_B = {"role": "recv"}
# Optional fixed input scripts that are merged with the pilot's buttons
INPUT_A = ""
INPUT_B = ""


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def skip_name(name):
    return name.startswith(".") or name in ("__pycache__", ".ruff_cache")


def app_tree(root, filtered):
    """{relpath: bytes} of an app folder; filtered drops hidden/__pycache__ entries."""
    out = {}
    for d, dirs, files in os.walk(root):
        if filtered:
            dirs[:] = [x for x in dirs if not skip_name(x)]
        rd = os.path.relpath(d, root).replace(os.sep, "/")
        for f in files:
            if filtered and skip_name(f):
                continue
            rel = f if rd == "." else rd + "/" + f
            with open(os.path.join(d, f), "rb") as fh:
                out[rel] = fh.read()
    return out


def leftovers(root):
    """Staging files and half-installed folders left in a sandbox."""
    bad = []
    stage = os.path.join(root, "air_merge")
    for d, dirs, files in os.walk(stage):
        for f in files:
            bad.append(os.path.relpath(os.path.join(d, f), root))
    apps = os.path.join(root, "system", "apps")
    for name in sorted(os.listdir(apps)) if os.path.isdir(apps) else ():
        if name.startswith(".am_"):
            bad.append("system/apps/" + name)
    return bad


def parse_log(text):
    states = []   # (frame, state)
    for m in re.finditer(r"^frame\s+(\d+)\s+ticks\s+\d+\s+state (.*)$", text, re.M):
        states.append((int(m.group(1)), m.group(2).strip()))
    events = []
    for m in re.finditer(r"^frame\s+(\d+)\s+pilot: (.*)$", text, re.M):
        events.append((int(m.group(1)), m.group(2).strip()))
    fs = {}
    m = re.search(r"^fs: summary (.*)$", text, re.M)
    if m:
        for tok in m.group(1).split():
            k, _, v = tok.partition("=")
            fs[k] = v
    return states, events, fs


def ui_of(state):
    return state.split()[0] if state else ""


def first_frame(states, names, after=0):
    for f, s in states:
        if f >= after and ui_of(s) in names:
            return f
    return None


def last_frame(states, names):
    out = None
    for i, (f, s) in enumerate(states):
        if ui_of(s) in names:
            end = states[i + 1][0] if i + 1 < len(states) else f
            out = (f, end)
    return out


def visited(states, names):
    return any(ui_of(s) in names for _, s in states)


class Checks:
    def __init__(self):
        self.items = []

    def check(self, ok, what, detail=""):
        self.items.append((bool(ok), what, detail))
        return ok

    def ok(self):
        return all(o for o, _, _ in self.items)

    def report(self):
        for o, what, detail in self.items:
            print("  [%s] %s%s" % ("ok" if o else "FAIL", what, (": " + detail) if detail else ""))


def snap_diff(before, after, ignore_prefixes=()):
    """(changed, added, removed) paths of a seed.snapshot(), ignoring some prefixes."""
    def keep(p):
        return not any(p.startswith(x) for x in ignore_prefixes)
    changed = sorted(p for p in before if p in after and before[p] != after[p] and keep(p))
    added = sorted(p for p in after if p not in before and keep(p))
    removed = sorted(p for p in before if p not in after and keep(p))
    return changed, added, removed


def fmt_diff(d):
    changed, added, removed = d
    parts = []
    for name, lst in (("changed", changed), ("added", added), ("removed", removed)):
        if lst:
            parts.append("%s %s" % (name, ", ".join(lst[:6]) + (" ..." if len(lst) > 6 else "")))
    return "; ".join(parts)


def app_kb(slug):
    tree = app_tree(os.path.join(seeder.FIXTURES, slug), filtered=True)
    return sum(len(v) for v in tree.values()) // 1024 + 1


def run_scenario(sc, args):
    """Run one scenario; returns (ok, sheet path)."""
    slug = sc["app"]
    tag = sc["name"]
    base = os.path.join(args.out, "share")
    root_a = os.path.join(base, "a")
    root_b = os.path.join(base, "b")
    print("=== scenario %s: A (%s) sends %s to B (%s) ===" % (tag, NAME_A, slug, NAME_B))
    seeder.seed(root_a, APPS_A, name=NAME_A)
    seeder.seed(root_b, APPS_B, name=NAME_B)
    before_a = seeder.snapshot(root_a)
    before_b = seeder.snapshot(root_b)
    b_had_app = os.path.isdir(os.path.join(root_b, "system", "apps", slug))

    pa, pb = free_port(), free_port()
    while pb == pa:
        pb = free_port()
    if pb < pa:
        pa, pb = pb, pa   # the lower port is central: make that badge A
    est_s = 20 + app_kb(slug) / max(1.0, args.kbps) * 1.6
    frames = args.frames or int(est_s * 1000 / args.dt)
    epoch = time.time() + 2.0

    pilot_a = dict(PILOT_A, app=slug)
    pilot_b = dict(PILOT_B, answer="decline" if sc.get("decline") else "accept")
    if sc.get("expect_auto_decline"):
        pilot_b["expect"] = "auto_decline"
    extra_a, extra_b = [], []
    if sc.get("drop_at"):
        (extra_b if sc.get("drop_who") == "b" else extra_a).extend(
            ["--link-mute-frame", str(sc["drop_at"])])
    if sc.get("stage_free") is not None:
        extra_b += ["--stage-free", str(sc["stage_free"])]
    if sc.get("system_free") is not None:
        extra_b += ["--system-free", str(sc["system_free"])]
    if sc.get("msc_busy"):
        extra_b += ["--msc-busy"]

    badges = (
        ("A", root_a, pa, pb, NAME_A, ID_A, 1, 5000, INPUT_A, pilot_a, extra_a),
        ("B", root_b, pb, pa, NAME_B, ID_B, 2, 23417, INPUT_B, pilot_b, extra_b),
    )
    procs = []
    for btag, root, port, peer, name, bid, seed, ticks, inp, pilot, extra in badges:
        out = os.path.join(base, "frames_" + btag.lower())
        cmd = [sys.executable, "-I", RUN, "--app", APP, "--frames", str(frames),
               "--dt", str(args.dt), "--every", str(args.every), "--out", out,
               "--fs-root", root, "--seed", str(seed), "--start-ticks", str(ticks),
               "--realtime", "--pace", str(args.pace), "--epoch", repr(epoch),
               "--link-port", str(port), "--peer-port", str(peer),
               "--badge-name", name, "--badge-id", bid, "--link-kbps", str(args.kbps), "--link-mtu", str(args.mtu),
               "--pilot", PILOT, "--pilot-opt", ",".join("%s=%s" % kv for kv in pilot.items())]
        if inp:
            cmd += ["--input", inp]
        cmd += extra
        log = open(os.path.join(base, "%s.log" % btag.lower()), "w+")
        procs.append((btag, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log, out))
    print("badges A (port %d, central) and B (port %d) running, up to %.0f s ..."
          % (pa, pb, frames * args.dt / 1000.0 / args.pace + 2))

    logs = {}
    rcs = {}
    for btag, p, log, out in procs:
        rcs[btag] = p.wait()
        log.seek(0)
        logs[btag] = log.read()
        log.close()

    C = Checks()
    parsed = {}
    for btag in ("A", "B"):
        text = logs[btag]
        states, events, fs = parse_log(text)
        parsed[btag] = (states, events, fs)
        print("badge %s timeline:" % btag)
        for f, s in states:
            print("    frame %5d  %s" % (f, s))
        for f, e in events:
            if not e.startswith(("press", "tap")):
                print("    frame %5d  pilot: %s" % (f, e))
        for line in text.splitlines():
            if line.startswith(("link:", "fs: /system", "fs: final", "fs: VIOLATION",
                                "fs: free", "fs: ", "ran ", "python ms", "slow frames",
                                "realtime:", "pilot:", "ERROR")) and not line.startswith("fs:   "):
                print("    " + line)
        C.check(rcs[btag] == 0, "badge %s exited cleanly" % btag, "exit %d" % rcs[btag])
        C.check("Traceback" not in text, "badge %s raised no exception" % btag,
                _tail_of_traceback(text))
        viol = [ln[len("fs: VIOLATION "):] for ln in text.splitlines()
                if ln.startswith("fs: VIOLATION")]
        C.check(not viol, "badge %s: no filesystem rule violations" % btag, "; ".join(viol))
        C.check(fs.get("final") == "ro", "badge %s: /system read-only at the end" % btag,
                "final=%s" % fs.get("final"))

    states_a, events_a, fs_a = parsed["A"]
    states_b, events_b, fs_b = parsed["B"]
    after_a = seeder.snapshot(root_a)
    after_b = seeder.snapshot(root_b)

    # A is only ever a sender: nothing on it may change but State files
    da = snap_diff(before_a, after_a, ignore_prefixes=("state/",))
    C.check(not any(da), "A's filesystem untouched (State aside)", fmt_diff(da))
    C.check(fs_a.get("rw_count") == "0", "A never unlocked /system",
            "rw_count=%s" % fs_a.get("rw_count"))
    st_changed = [p for p in before_a if p.startswith("state/") and after_a.get(p) != before_a[p]]
    st_changed += [p for p in before_b if p.startswith("state/") and after_b.get(p) != before_b[p]]
    C.check(not st_changed, "existing State files untouched on both badges", ", ".join(st_changed))
    lo = leftovers(root_b)
    C.check(not lo, "B has no staging leftovers", ", ".join(lo))
    C.check(not leftovers(root_a), "A has no staging leftovers", ", ".join(leftovers(root_a)))

    kind = sc["expect"]
    if kind == "dropped" and visited(states_b, ("DONE",)):
        # the link went away after all data had arrived: B finishes on its own
        print("  (B finished the install after the drop: checking it like a send)")
        kind = "installed_orphan"
    dst = os.path.join(root_b, "system", "apps", slug)
    others = snap_diff(before_b, after_b,
                       ignore_prefixes=("state/", "air_merge/", "system/apps/%s/" % slug))
    C.check(not any(others), "B's other apps, secrets.py and State untouched", fmt_diff(others))
    if kind in ("installed", "installed_orphan"):
        want = app_tree(os.path.join(root_a, "system", "apps", slug), filtered=True)
        got = app_tree(dst, filtered=False) if os.path.isdir(dst) else {}
        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        differ = sorted(k for k in want if k in got and want[k] != got[k])
        C.check(os.path.isdir(dst), "B has /system/apps/%s" % slug)
        C.check(not missing and not extra and not differ,
                "B's %s matches A's byte for byte (%d files, %d bytes)"
                % (slug, len(want), sum(len(v) for v in want.values())),
                "missing %s extra %s differ %s" % (missing[:5], extra[:5], differ[:5]))
        junk = [k for k in got if any(skip_name(part) for part in k.split("/"))]
        C.check(not junk, "no hidden / __pycache__ files were transferred", ", ".join(junk))
        C.check(fs_b.get("rw_count", "0") != "0", "B unlocked /system to install",
                "rw_count=%s" % fs_b.get("rw_count"))
        C.check(visited(states_b, ("DONE",)), "B reached DONE")
        if kind == "installed":
            C.check(visited(states_a, ("DONE",)), "A reached DONE (got the receiver's result)")
        if b_had_app:
            C.check("old_notes.txt" not in got, "B's older %s was replaced, stale files gone"
                    % slug)
    else:
        db = snap_diff(before_b, after_b, ignore_prefixes=("state/",))
        db = (db[0], [p for p in db[1] if not (p.startswith("air_merge/") and p.endswith("/"))],
              db[2])
        C.check(not any(db), "B's filesystem unchanged (empty staging folders aside)",
                fmt_diff(db))
        C.check(fs_b.get("rw_count") == "0", "B never unlocked /system",
                "rw_count=%s" % fs_b.get("rw_count"))
        C.check(not visited(states_b, ("DONE",)), "B did not reach DONE")
        C.check(not visited(states_a, ("DONE",)), "A did not reach DONE")
        if kind == "declined":
            C.check(visited(states_b, ("OFFER_IN",)), "B was shown the offer")
            C.check(any("decline" in e.lower() or "back" in e.lower() for _, e in events_b),
                    "B's pilot declined with BACK")
            C.check(not visited(states_a, ("SENDING",)) and not visited(states_b, ("RECEIVING",)),
                    "no data was sent after the decline")
        elif kind == "refused":
            C.check(not visited(states_a, ("SENDING",)) or visited(states_a, ("FAILED",)),
                    "A was told it didn't work")
            texts = " | ".join(e for _, e in events_a + events_b if e.startswith("text"))
            C.check(re.search(sc.get("reason", "."), texts, re.I) is not None,
                    "a reason matching /%s/ was shown" % sc.get("reason", "."), texts[-200:])
        elif kind == "auto_declined":
            C.check(not visited(states_b, ("RECEIVING",)), "B never started receiving")
            texts = " | ".join(e for _, e in events_a + events_b if e.startswith("text"))
            C.check(re.search(r"too big|space|room|free|KB", texts, re.I) is not None,
                    "a reason (too big / no room) was shown", texts[-200:])
            C.check(not any(e.startswith(("press SELECT", "tap SELECT")) for _, e in events_b),
                    "B never had to answer (auto-declined)")
        elif kind == "dropped":
            rx = last_frame(states_b, ("RECEIVING",))
            C.check(rx is not None and rx[0] <= sc["drop_at"] <= rx[1] + 60,
                    "the drop happened while B was receiving",
                    "B RECEIVING span %r, drop at frame %d" % (rx, sc["drop_at"]))
            C.check(not b_had_app and not os.path.exists(dst), "B has no new %s" % slug)
            C.check(states_b and ui_of(states_b[-1][1]) == "LOBBY",
                    "B is back in the lobby after the drop",
                    states_b[-1][1] if states_b else "no states")

    sheet = sc.get("sheet") or args.sheet
    made = make_sheet(sheet, states_a, states_b, base, args.every, frames)
    C.report()
    ok = C.ok()
    print("scenario %s: %s%s" % (tag, "PASS" if ok else "FAIL",
                                 ("  sheet %s" % os.path.relpath(made)) if made else ""))
    return ok, made


def _tail_of_traceback(text):
    i = text.find("Traceback")
    if i < 0:
        return ""
    return " / ".join(text[i:].splitlines()[-3:])


def state_at(states, frame):
    cur = "?"
    for f, s in states:
        if f > frame:
            break
        cur = ui_of(s)
    return cur


def make_sheet(path, states_a, states_b, base, every, frames):
    """Side-by-side sheet: one row per moment, A left, B right."""
    e = max(1, every)
    moments = []
    seen_states = set()
    for tag, states in (("A", states_a), ("B", states_b)):
        for i, (f, s) in enumerate(states):
            ui = ui_of(s)
            key = (tag, ui)
            if key in seen_states and ui not in ("LOBBY",):
                continue
            seen_states.add(key)
            end = states[i + 1][0] if i + 1 < len(states) else frames
            moments.append((f + min(12, max(2, (end - f) // 2)), "%s %s" % (tag, ui)))
            if ui in ("SENDING", "RECEIVING") and end - f > 60:
                moments.append((f + (end - f) // 2, "%s %s 50%%" % (tag, ui)))
    moments.sort()
    rows = []
    for f, name in moments:
        f = max(e, int(round(f / float(e))) * e)
        if rows and abs(rows[-1][0] - f) < e * 2:
            continue
        rows.append((f, name))
    rows = rows[:16]
    paths, labels = [], []
    for f, name in rows:
        pair = []
        for tag in ("a", "b"):
            p = os.path.join(base, "frames_" + tag, "frame_%04d.png" % f)
            if not os.path.exists(p):
                cands = sorted(int(x[6:10]) for x in os.listdir(os.path.join(base, "frames_" + tag))
                               if x.startswith("frame_") and x.endswith(".png") and x[6:10].isdigit())
                cands = [c for c in cands if c <= f] or cands
                if not cands:
                    continue
                p = os.path.join(base, "frames_" + tag, "frame_%04d.png" % cands[-1])
            pair.append(p)
        if len(pair) != 2:
            continue
        paths += pair
        labels += ["A #%d %s" % (f, state_at(states_a, f)), "B #%d %s" % (f, state_at(states_b, f))]
    if not paths:
        return None
    png.contact_sheet(paths, 2, path, labels=labels)
    return path


def scenarios(args):
    app = args.app
    out = []
    if args.all:
        out.append({"name": "send", "app": app, "expect": "installed"})
        out.append({"name": "pixel_garden", "app": "pixel_garden", "expect": "installed"})
        out.append({"name": "replace", "app": "hello_badge", "expect": "installed"})
        out.append({"name": "decline", "app": app, "expect": "declined", "decline": True})
        out.append({"name": "drop", "app": app, "expect": "dropped",
                    "drop_at": args.drop_at or 0})
        out.append({"name": "small_stage", "app": app, "expect": "auto_declined",
                    "stage_free": 48 + app_kb(app) // 2, "expect_auto_decline": True})
        out.append({"name": "small_system", "app": app, "expect": "auto_declined",
                    "system_free": 48 + app_kb(app) // 2, "expect_auto_decline": True})
        out.append({"name": "msc_busy", "app": "pixel_garden", "expect": "refused",
                    "msc_busy": True, "reason": "USB|disk"})
        for sc in out:
            sc["sheet"] = os.path.join(args.out, "share_%s_sheet.png" % sc["name"])
        return out
    sc = {"name": "send", "app": app, "expect": "installed"}
    if args.replace:
        sc.update(name="replace", app="hello_badge")
    if args.decline:
        sc.update(name="decline", expect="declined", decline=True)
    if args.drop_at:
        sc.update(name="drop", expect="dropped", drop_at=args.drop_at, drop_who=args.drop_who)
    if args.small_system:
        sc.update(name="small_system", expect="auto_declined",
                  system_free=48 + app_kb(sc["app"]) // 2, expect_auto_decline=True)
    if args.msc_busy:
        sc.update(name="msc_busy", expect="refused", msc_busy=True, reason="USB|disk")
    if args.small_stage:
        sc.update(name="small_stage", expect="auto_declined",
                  stage_free=48 + app_kb(sc["app"]) // 2, expect_auto_decline=True)
    return [sc]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Two-badge Air Merge share run.")
    ap.add_argument("--app", default="mona_kart", help="slug A sends (default mona_kart)")
    ap.add_argument("--decline", action="store_true", help="B presses BACK on the offer")
    ap.add_argument("--drop-at", type=int, default=0,
                    help="A's link goes silent from this frame (walk away mid-transfer)")
    ap.add_argument("--drop-who", choices=("a", "b"), default="a",
                    help="with --drop-at: which badge walks away (b: test that B finishes "
                    "an install on its own after the link drops)")
    ap.add_argument("--replace", action="store_true",
                    help="send hello_badge, which B has in an older version")
    ap.add_argument("--small-stage", action="store_true",
                    help="B's staging free space is too small: offer must be auto-declined")
    ap.add_argument("--small-system", action="store_true",
                    help="B's /system free space is too small: offer must be auto-declined")
    ap.add_argument("--msc-busy", action="store_true",
                    help="B is in USB disk mode (rp2.is_msc_busy() True): nothing may be "
                    "installed and a reason must be shown")
    ap.add_argument("--all", action="store_true", help="run every scenario")
    ap.add_argument("--frames", type=int, default=0, help="frame limit (default: from app size)")
    ap.add_argument("--dt", type=int, default=33)
    ap.add_argument("--pace", type=float, default=2.0,
                    help="badge ms per wall ms for both badges (1 = real time)")
    ap.add_argument("--kbps", type=float, default=12.0, help="fake link throughput, KB/s")
    ap.add_argument("--mtu", type=int, default=247,
                    help="negotiated link MTU (capacity = mtu - 4; 23 = never upgraded)")
    ap.add_argument("--every", type=int, default=5, help="save every Nth frame on both badges")
    ap.add_argument("--sheet", default=os.path.join(OUT, "share_sheet.png"))
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    if not os.path.exists(os.path.join(APP, "__init__.py")):
        print("no app/__init__.py yet")
        return 2

    results = []
    for sc in scenarios(args):
        if sc.get("expect") == "dropped" and not sc.get("drop_at"):
            sc["drop_at"] = auto_drop_frame(sc, args)
        ok, sheet = run_scenario(sc, args)
        results.append((sc["name"], ok, sheet))
    if len(results) > 1:
        print("=== summary ===")
        for name, ok, sheet in results:
            print("  %-13s %s  %s" % (name, "PASS" if ok else "FAIL",
                                      os.path.relpath(sheet) if sheet else ""))
    return 0 if all(ok for _, ok, _ in results) else 1


def auto_drop_frame(sc, args):
    """A frame in the middle of the transfer: pairing + offer take ~8 s of badge time."""
    secs = 9.0 + app_kb(sc["app"]) / max(1.0, args.kbps) * 0.5
    return int(secs * 1000 / args.dt)


if __name__ == "__main__":
    sys.exit(main())
