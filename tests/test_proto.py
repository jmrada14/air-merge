import hashlib
import json
import os
import random
import struct
import unittest

import fakes
from fakes import FS
import duo

import am_fs
import am_proto as P


def big_app(seed=7, total=200 * 1024):
    """A ~200 KB multi-file app with nested folders and an empty file."""
    rng = random.Random(seed)
    files = {"__init__.py": b"# main\n" + fakes.rand_bytes(rng, 3000), "empty.txt": b""}
    names = ["mod_%d.py" % i for i in range(6)] + ["assets/img_%d.bin" % i for i in range(5)] + \
        ["assets/levels/l%d.json" % i for i in range(4)]
    left = total - sum(len(v) for v in files.values())
    for i, n in enumerate(names):
        size = left // (len(names) - i) + rng.randint(-500, 500)
        if i == len(names) - 1:
            size = left
        files[n] = fakes.rand_bytes(rng, size)
        left -= size
    return files


class ProtoBase(unittest.TestCase):
    def setUp(self):
        fakes.reset()
        self.ra = fakes.new_root()
        self.rb = fakes.new_root()

    def tearDown(self):
        fakes.reset()

    def sys_apps(self, root):
        return os.path.join(root, "system", "apps")

    def offer(self, d, slug):
        d.a.use()
        self.assertTrue(d.a.engine.send_app(slug, d.now))
        d.run(lambda d: d.b.engine.state == P.ASK or "offer_bad" in d.b.kinds())

    def finish(self, d, **kw):
        d.run(lambda d: d.a.engine.state == P.IDLE and d.b.engine.state == P.IDLE, **kw)

    def assert_receiver_clean(self, root):
        self.assertFalse(os.path.exists(os.path.join(root, "air_merge", "incoming")))
        self.assertEqual(FS.mount, "ro")
        self.assertEqual(FS.violations, [])
        self.assertFalse([n for n in os.listdir(self.sys_apps(root)) if n.startswith(".am_")])


class TestTransfer(ProtoBase):
    def test_big_app_capacity_19_then_243_with_backpressure(self):
        files = big_app()
        fakes.write_app(self.ra, "big_game", files)
        fakes.write_app(self.rb, "other", {"__init__.py": b"untouched"})
        d = duo.Duo(self.ra, self.rb, seed=5, refuse=0.3)
        sizes = []
        d.la.tap = lambda m: (sizes.append((d.la.capacity(), len(m))), m)[1]
        self.offer(d, "big_game")
        self.assertEqual(d.b.kinds(), ["offer"])
        m = d.b.engine.m
        self.assertEqual(m["total"], sum(len(v) for v in files.values()))
        self.assertEqual(len(m["files"]), len(files))
        d.b.use()
        self.assertTrue(d.b.engine.accept(d.now))
        # stream a while at capacity 19, then the MTU upgrade happens mid-transfer
        d.run(lambda d: d.b.engine.done > 20000)
        self.assertTrue(all(n <= 19 for cap, n in sizes))
        d.upgrade()
        self.finish(d)
        self.assertTrue(all(n <= cap for cap, n in sizes))
        self.assertTrue(any(n > 200 for cap, n in sizes))
        self.assertEqual(d.la.over + d.lb.over, 0)
        self.assertIn(("result", True, "", "Big Game"), d.a.events)
        self.assertEqual(d.b.kinds()[-1], "installed")
        got = fakes.read_tree(os.path.join(self.sys_apps(self.rb), "big_game"), skip_hidden=False)
        self.assertEqual(got, files)
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "other")),
                         {"__init__.py": b"untouched"})
        self.assert_receiver_clean(self.rb)
        self.assertTrue(d.la.refused > 0)
        self.assertFalse(os.path.exists(os.path.join(self.ra, "air_merge", "incoming")))

    def test_window_never_exceeded(self):
        fakes.write_app(self.ra, "game", big_app(total=60 * 1024))
        d = duo.Duo(self.ra, self.rb, refuse=0.0)
        d.upgrade()
        self.offer(d, "game")
        d.b.use()
        d.b.engine.accept(d.now)
        worst = [0]

        def hook(d):
            worst[0] = max(worst[0], len(d.a.engine._ends) if d.a.engine.state == P.SEND else 0)
        # deliver slowly so the window fills up
        self.finish(d, per_frame=6, hook=hook)
        self.assertLessEqual(worst[0], P.WINDOW)
        self.assertEqual(worst[0], P.WINDOW)
        self.assertEqual(d.b.kinds()[-1], "installed")

    def test_replace_existing_app(self):
        new = {"__init__.py": b"v2" * 3000, "extra.py": b"e" * 100}
        fakes.write_app(self.ra, "snake", new)
        fakes.write_app(self.rb, "snake", {"__init__.py": b"v1", "gone.txt": b"x"})
        d = duo.Duo(self.ra, self.rb)
        d.upgrade()
        self.offer(d, "snake")
        d.b.use()
        d.b.engine.accept(d.now)
        self.finish(d)
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "snake")), new)
        self.assert_receiver_clean(self.rb)

    def test_lost_chunk_recovers(self):
        files = big_app(total=40 * 1024)
        fakes.write_app(self.ra, "game", files)
        d = duo.Duo(self.ra, self.rb, refuse=0.1)
        d.upgrade()
        count = [0]

        def tap(m):
            if m[0] == ord("F"):
                count[0] += 1
                if count[0] in (30, 31, 77):
                    return None     # lost on the air
            return m
        d.la.tap = tap
        self.offer(d, "game")
        d.b.use()
        d.b.engine.accept(d.now)
        self.finish(d)
        self.assertGreater(d.a.engine.rewinds, 0)
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "game")), files)

    def test_corrupted_chunk_is_caught(self):
        files = big_app(total=30 * 1024)
        fakes.write_app(self.ra, "game", files)
        d = duo.Duo(self.ra, self.rb, refuse=0.0)
        d.upgrade()
        hit = [False]

        def tap(m):
            if m[0] == ord("F") and not hit[0] and len(m) > 50:
                hit[0] = True
                m = m[:40] + bytes([m[40] ^ 0xFF]) + m[41:]
            return m
        d.la.tap = tap
        self.offer(d, "game")
        d.b.use()
        d.b.engine.accept(d.now)
        self.finish(d)
        self.assertEqual(d.a.events[-1][:2], ("result", False))
        self.assertEqual(d.b.kinds()[-1], "failed")
        self.assertFalse(os.path.exists(os.path.join(self.sys_apps(self.rb), "game")))
        self.assertEqual(FS.rw_count, 0)
        self.assert_receiver_clean(self.rb)


class TestConsent(ProtoBase):
    def setUp(self):
        ProtoBase.setUp(self)
        self.files = {"__init__.py": b"snake" * 500}
        fakes.write_app(self.ra, "snake", self.files)
        self.d = duo.Duo(self.ra, self.rb)
        self.d.upgrade()

    def test_nothing_written_before_accept(self):
        d = self.d
        self.offer(d, "snake")
        for _ in range(100):
            d.frame()
        self.assertEqual(d.b.engine.state, P.ASK)
        self.assertFalse(os.path.exists(os.path.join(self.rb, "air_merge", "incoming")))
        self.assertEqual(d.a.engine.state, P.OFFER)

    def test_decline(self):
        d = self.d
        self.offer(d, "snake")
        d.b.use()
        self.assertTrue(d.b.engine.decline(d.now))
        self.finish(d)
        self.assertEqual(d.a.events[-1][:2], ("declined", P.DECLINED))
        self.assertFalse(os.path.exists(os.path.join(self.sys_apps(self.rb), "snake")))
        self.assert_receiver_clean(self.rb)

    def test_ask_times_out(self):
        d = self.d
        self.offer(d, "snake")
        d.run(lambda d: d.a.engine.state == P.IDLE, dt=500)
        self.assertEqual(d.a.events[-1][:2], ("declined", P.TIMED_OUT))
        self.assertIn("ask_timeout", d.b.kinds())
        self.assertGreaterEqual(d.now, 30000)
        self.assert_receiver_clean(self.rb)

    def test_too_big_auto_declined(self):
        d = self.d
        real = am_fs.free_stage
        am_fs.free_stage = lambda: 40 * 1024
        try:
            fakes.write_app(self.ra, "huge", {"__init__.py": b"h" * (100 * 1024)})
            self.offer(d, "huge")
        finally:
            am_fs.free_stage = real
        self.finish(d)
        bad = [e for e in d.b.events if e[0] == "offer_bad"]
        self.assertEqual(len(bad), 1)
        self.assertTrue(bad[0][2].startswith("too big"))
        self.assertNotIn("offer", d.b.kinds())
        self.assertEqual(d.a.events[-1][:2], ("declined", P.TOO_BIG))

    def test_usb_disk_mode_declined_at_offer(self):
        d = self.d
        FS.msc_busy = True
        try:
            self.offer(d, "snake")
            self.finish(d)
        finally:
            FS.msc_busy = False
        bad = [e for e in d.b.events if e[0] == "offer_bad"]
        self.assertEqual(len(bad), 1)
        self.assertIn("USB", bad[0][2])
        self.assertNotIn("offer", d.b.kinds())
        self.assertEqual(d.a.events[-1], ("declined", P.BUSY, "USB disk mode is on"))
        self.assertFalse(os.path.exists(os.path.join(self.rb, "air_merge", "incoming")))
        self.assertEqual(FS.rw_count, 0)

    def test_sender_cancels_while_asking(self):
        d = self.d
        self.offer(d, "snake")
        d.a.use()
        d.a.engine.cancel(d.now)
        self.finish(d)
        self.assertEqual(d.b.events[-1], ("cancelled", "partner"))

    def test_collision_central_wins(self):
        fakes.write_app(self.rb, "frog", {"__init__.py": b"frog"})
        d = self.d
        d.a.use()
        d.a.engine.send_app("snake", d.now)
        d.b.use()
        d.b.engine.send_app("frog", d.now)
        d.run(lambda d: d.b.engine.state == P.ASK)
        self.assertIn(("cancelled", "collision"), d.b.events)
        self.assertEqual(d.b.engine.m["slug"], "snake")
        d.b.use()
        d.b.engine.accept(d.now)
        self.finish(d)
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "snake")), self.files)


class Raw:
    """Hand-made sender for malicious / malformed offers."""

    def __init__(self, d):
        self.d = d

    def offer(self, m, tid=9, piece=230):
        data = json.dumps(m).encode()
        n = (len(data) + piece - 1) // piece
        for i in range(n):
            self.d.lb.inbox.append(struct.pack("<BBHH", ord("O"), tid, i, n) + data[i * piece:(i + 1) * piece])


class TestHostileInput(ProtoBase):
    def setUp(self):
        ProtoBase.setUp(self)
        self.d = duo.Duo(self.ra, self.rb)
        self.d.upgrade()

    def evil(self, rel):
        body = b"pwned"
        return {"v": 1, "slug": "evil", "name": "Evil",
                "files": [["__init__.py", 1, hashlib.sha256(b"x").hexdigest()],
                          [rel, len(body), hashlib.sha256(body).hexdigest()]],
                "total": 1 + len(body)}

    def test_traversal_offer_never_asked(self):
        for rel in ("../secrets.py", "/secrets.py", "../../state/x.json", "a/../../x"):
            Raw(self.d).offer(self.evil(rel))
            self.d.frame()
            self.d.frame()
            kinds = self.d.b.kinds()
            self.assertEqual(kinds[-1], "offer_bad", rel)
            self.assertNotIn("offer", kinds)
            self.assertEqual(self.d.b.engine.state, P.IDLE)
            self.d.b.events = []
        with fakes._real["open"](os.path.join(self.rb, "secrets.py")) as f:
            self.assertIn("hunter2", f.read())

    def test_decline_code_for_invalid(self):
        Raw(self.d).offer(self.evil("../x"), tid=42)
        self.d.b.step(self.d.now)
        sent = self.d.lb.outbox + self.d.b.engine.ctl
        self.assertTrue([x for x in sent if x[:3] == struct.pack("<BBB", ord("D"), 42, P.INVALID)], sent)

    def test_menu_offer_refused(self):
        m = self.evil("ok.py")
        m["slug"] = "menu"
        Raw(self.d).offer(m)
        self.d.frame()
        self.assertEqual(self.d.b.kinds()[-1], "offer_bad")

    def test_garbage_ignored_everywhere(self):
        rng = random.Random(11)
        files = {"__init__.py": fakes.rand_bytes(rng, 20000)}
        fakes.write_app(self.ra, "game", files)
        d = self.d

        def noise(d):
            for side in (d.a, d.b):
                side.use()
                for _ in range(3):
                    kind = rng.choice(b"HOADFKVXZ\x00\xff")
                    tid = (side.engine.tid + rng.randint(1, 255)) & 0xFF
                    body = fakes.rand_bytes(rng, rng.randint(0, 30))
                    if kind == ord("O"):
                        body = struct.pack("<HH", rng.randint(1, 100), 100) + body
                    msg = bytes([kind, tid]) + body
                    if rng.random() < 0.2:
                        msg = msg[:rng.randint(0, 2)]
                    side.engine.on_message(msg, d.now)
        d.a.use()
        d.a.engine.send_app("game", d.now)
        d.run(lambda d: d.b.engine.state == P.ASK, hook=noise)
        d.b.use()
        d.b.engine.accept(d.now)
        self.finish(d, hook=noise)
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "game")), files)

    def test_wrong_tid_data_ignored(self):
        files = {"__init__.py": b"a" * 3000}
        fakes.write_app(self.ra, "game", files)
        d = self.d
        self.offer(d, "game")
        d.b.use()
        d.b.engine.accept(d.now)
        tid = d.b.engine.tid
        d.b.engine.on_message(struct.pack("<BBHI", ord("F"), (tid + 1) & 0xFF, 0, 0) + b"EVIL", d.now)
        d.b.engine.on_message(struct.pack("<BBHI", ord("F"), tid, 0, 100) + b"EVIL", d.now)
        d.b.engine.on_message(struct.pack("<BBHI", ord("F"), tid, 5, 0) + b"EVIL", d.now)
        self.assertEqual(d.b.engine.done, 0)
        self.finish(d)
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "game")), files)

    def test_busy_while_receiving(self):
        fakes.write_app(self.ra, "game", {"__init__.py": b"a" * 30000})
        d = self.d
        self.offer(d, "game")
        d.b.use()
        d.b.engine.accept(d.now)
        d.frame()
        t = (d.b.engine.tid + 50) & 0xFF
        Raw(d).offer(self.evil("ok.py"), tid=t)
        d.b.step(d.now)
        self.assertIn(struct.pack("<BBB", ord("D"), t, P.BUSY), d.lb.outbox + d.b.engine.ctl)
        self.assertEqual(d.b.engine.state, P.RECV)
        self.finish(d)
        self.assertEqual(d.b.kinds()[-1], "installed")


class TestCancelAndDrop(ProtoBase):
    def setUp(self):
        ProtoBase.setUp(self)
        self.files = big_app(total=120 * 1024)
        fakes.write_app(self.ra, "game", self.files)
        self.old = {"__init__.py": b"old game"}
        fakes.write_app(self.rb, "game", self.old)
        self.d = duo.Duo(self.ra, self.rb)
        self.d.upgrade()
        self.offer(self.d, "game")
        self.d.b.use()
        self.d.b.engine.accept(self.d.now)
        self.d.run(lambda d: d.b.engine.done > 50000)

    def assert_untouched(self):
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "game")), self.old)
        self.assertEqual(FS.rw_count, 0)
        self.assert_receiver_clean(self.rb)

    def test_sender_cancels_mid_transfer(self):
        d = self.d
        d.a.use()
        d.a.engine.cancel(d.now)
        self.finish(d)
        self.assertEqual(d.b.events[-1], ("cancelled", "partner"))
        self.assert_untouched()

    def test_receiver_cancels_mid_transfer(self):
        d = self.d
        d.b.use()
        d.b.engine.cancel(d.now)
        self.finish(d)
        self.assertEqual(d.a.events[-1], ("cancelled", "partner"))
        self.assert_untouched()

    def test_link_drop_mid_transfer(self):
        d = self.d
        d.la.drop()
        d.lb.drop()
        d.a.use()
        self.assertFalse(d.a.engine.dropped(d.now))
        d.b.use()
        self.assertFalse(d.b.engine.dropped(d.now))
        for _ in range(10):
            d.frame()
        self.assertEqual(d.a.engine.state, P.IDLE)
        self.assertEqual(d.b.engine.state, P.IDLE)
        self.assert_untouched()

    def test_silent_partner_times_out(self):
        d = self.d
        d.la.outbox = []
        d.la.linked = False      # sender goes silent but the link never reports a drop
        d.run(lambda d: d.b.engine.state == P.IDLE, dt=200)
        self.assertEqual(d.b.kinds()[-1], "failed")
        self.assert_untouched()

    def test_drop_while_installing_finishes(self):
        d = self.d
        d.run(lambda d: d.b.engine.state == P.INSTALL)
        d.la.drop()
        d.lb.drop()
        d.b.use()
        self.assertTrue(d.b.engine.dropped(d.now))
        d.a.use()
        d.a.engine.dropped(d.now)
        d.run(lambda d: d.b.engine.state == P.IDLE)
        self.assertEqual(d.b.kinds()[-1], "installed")
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "game")), self.files)
        self.assert_receiver_clean(self.rb)

    def test_receiver_status_blocks_late_cancel(self):
        d = self.d
        d.run(lambda d: d.a.engine.remote_phase >= P.CHECKING)
        self.assertEqual(d.a.engine.state, P.AWAIT)
        d.run(lambda d: d.a.engine.remote_phase >= P.INSTALLING or d.a.engine.state == P.IDLE)
        if d.a.engine.state == P.AWAIT:
            self.assertFalse(d.a.engine.can_cancel())
            d.a.use()
            self.assertFalse(d.a.engine.cancel(d.now))
            self.assertEqual(d.a.engine.state, P.AWAIT)
        self.finish(d)
        self.assertIn(("phase", P.INSTALLING), d.a.events)
        self.assertEqual(d.a.events[-1][:2], ("result", True))
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "game")), self.files)

    def test_cancel_while_checking_is_honoured(self):
        d = self.d
        d.run(lambda d: d.b.engine.state == P.VERIFY)

        def stall():
            while True:
                yield
        d.b.engine._gen.close()
        d.b.engine._gen = stall()      # keep B checking while A decides
        d.run(lambda d: d.a.engine.state == P.AWAIT and d.a.engine.remote_phase == P.CHECKING)
        self.assertTrue(d.a.engine.can_cancel())
        d.a.use()
        self.assertTrue(d.a.engine.cancel(d.now))
        self.finish(d)
        self.assertEqual(d.b.events[-1], ("cancelled", "partner"))
        self.assert_untouched()

    def test_abort_during_install_relocks(self):
        d = self.d
        d.run(lambda d: d.b.engine.state == P.INSTALL)
        d.frame()
        d.b.use()
        d.b.engine.abort()
        self.assertEqual(FS.mount, "ro")
        self.assertEqual(FS.violations, [])
        self.assertEqual(FS.open_writes, 0)
        self.assertEqual(fakes.read_tree(os.path.join(self.sys_apps(self.rb), "game")), self.old)


if __name__ == "__main__":
    unittest.main()


class TestBudget(ProtoBase):
    def test_budget_uses_live_clock(self):
        """The frame budget must stop work even though `now` (badge.ticks) is frozen."""
        fakes.write_app(self.ra, "game", big_app(total=64 * 1024))
        am_fs.ROOT = self.ra
        t = [0]

        def clock():
            t[0] += 10          # every clock read is 10 ms later
            return t[0]
        eng = P.Engine(fakes.link_pair()[0], clock)
        eng.send_app("game", 1000)
        steps = []
        for _ in range(200):
            g = eng._gen
            if g is None:
                break
            n0 = t[0]
            eng.tick(1000)
            steps.append(t[0] - n0)
        self.assertTrue(steps)
        self.assertTrue(all(s <= P.BUDGET_MS + 30 for s in steps), steps)

    def test_wrapping_clock_diff(self):
        calls = []

        def diff(a, b):
            calls.append((a, b))
            return (a - b) & 0x3FFFFFFF
        fakes.write_app(self.ra, "game", {"__init__.py": b"x" * 20000})
        am_fs.ROOT = self.ra
        t = [0x3FFFFFF0]

        def clock():
            t[0] = (t[0] + 7) & 0x3FFFFFFF
            return t[0]
        eng = P.Engine(fakes.link_pair()[0], clock, diff)
        eng.send_app("game", 0)
        for _ in range(50):
            eng.tick(0)
        self.assertTrue(calls)
        self.assertIsNotNone(eng.m)
