"""Smoke test: every Air Merge screen renders through the simulator's rasteriser without
raising, including the rarely reached ones (invalid offer, failures, partner gone)."""

import os
import sys
import unittest

import fakes

SIM = os.path.join(os.path.dirname(fakes.HERE), "sim")


def _load_sim():
    if SIM not in sys.path:
        sys.path.insert(0, SIM)
    import badge_sim
    import fake_link
    sim = badge_sim.install(fakes.APP)
    fake_link.install(sim.badge, name="Ada", id_=bytes.fromhex("a1a2a3a4a5a6a7a8"))
    return sim, fake_link


class FakeEngine:
    def __init__(self, state, m):
        self.state = state
        self.m = m
        self.done = 12345
        self.total = 54321
        self.orphan = False
        self.state_at = 0

    remote_phase = 0

    def can_cancel(self):
        return self.state != 9 and not (self.state == 4 and self.remote_phase >= 2)

    def speed(self):
        return 9876

    def current_file(self):
        return "assets/levels/a_rather_long_file_name_for_testing.json"

    def ask_left(self, now):
        return 12000


class FakeApp:
    pass


class TestScreens(unittest.TestCase):
    def test_every_screen(self):
        sim, fl = _load_sim()
        import am_proto as P
        import am_ui as ui
        from badgeware import link as bl
        from pairing import Pairing
        link = bl.Link("air_merge")
        m = {"v": 1, "slug": "mona_kart", "name": "Mona Kart", "total": 192565,
             "files": [["__init__.py", 1, "0" * 64]] * 8}
        app = {"slug": "mona_kart", "name": "Mona Kart", "size": 192565, "files": 8}
        a = FakeApp()
        a.badgelink = bl
        a.pairing = Pairing(74, 138, 6)
        a.proximity = bl.Proximity(link)
        a.link = link
        a.my_name, a.my_id = "Ada", b"\xa1" * 8
        a.partner_name, a.partner_id = "Bartholomew the Magnificent", b"\xb2" * 8
        a.apps = [dict(app, slug="app_%d" % i, name="App number %d with a long name" % i)
                  for i in range(9)] + [dict(app, slug="air_merge", name="Air Merge")]
        a.sel, a.top = 8, 3
        a.msg, a.msg_until, a.msg_bad = "Bartholomew walked away", 10 ** 9, True
        a.pick = app
        a.offer = m
        a.offer_local = {"slug": "mona_kart", "name": "Mona Kart", "size": 100, "files": 2}
        a.bad = (m, "too big: needs 900 KB, 300 KB free on this badge right now")
        a.error = "radio busy"
        cases = [
            (ui.NOLINK, P.IDLE), (ui.LOBBY, P.IDLE), (ui.PAIRED, P.IDLE),
            (ui.CONFIRM_SEND, P.IDLE), (ui.OFFERED, P.PREP), (ui.OFFERED, P.OFFER),
            (ui.SENDING, P.SEND), (ui.SENDING, P.AWAIT), (ui.OFFER_IN, P.ASK),
            (ui.OFFER_BAD, P.IDLE), (ui.RECEIVING, P.RECV), (ui.VERIFYING, P.VERIFY),
            (ui.INSTALLING, P.INSTALL),
        ]
        drawn = 0
        for state, est in cases:
            a.state = state
            a.engine = FakeEngine(est, m)
            sim.screen.reset_counts()
            ui.draw(a, 5000 + drawn * 77)
            self.assertGreater(sim.screen.counts["texts"], 0, state)
            drawn += 1
        for phase in (1, 2):
            a.state = ui.SENDING
            a.engine = FakeEngine(P.AWAIT, m)
            a.engine.remote_phase = phase
            ui.draw(a, 6000)
        a.bad = (m, "USB disk mode is on")
        a.state = ui.OFFER_BAD
        ui.draw(a, 6100)
        a.result = {"ok": None, "title": "Bo left early", "lines": ["They may still have it"],
                    "gone": False}
        a.state = ui.DONE
        ui.draw(a, 6200)
        a.engine.orphan = True
        a.state = ui.INSTALLING
        ui.draw(a, 7000)
        for ok in (True, False):
            for gone in (True, False):
                a.result = {"ok": ok, "title": "Bartholomew installed Mona Kart!",
                            "lines": ["Exit Air Merge to find it in the menu", "x " * 80],
                            "gone": gone}
                a.state = ui.DONE
                ui.draw(a, 9000)
        a.apps = []
        a.state = ui.PAIRED
        ui.draw(a, 9100)
        a.bad = (None, "invalid offer")
        a.offer_local = None
        for state in (ui.OFFER_BAD, ui.OFFER_IN):
            a.state = state
            ui.draw(a, 9200)

    def test_helpers(self):
        _load_sim()
        import am_ui as ui
        self.assertEqual(ui.fmt_size(512), "512 B")
        self.assertEqual(ui.fmt_size(2048), "2.0 KB")
        self.assertEqual(ui.fmt_size(192565), "188 KB")
        self.assertEqual(ui.fmt_size(3 * 1048576), "3.0 MB")
        self.assertEqual(ui.fmt_files(1), "1 file")
        self.assertTrue(ui.fit("x" * 200, ui.FONT_BODY, 100).endswith("..."))
        self.assertLessEqual(ui.measure(ui.fit("x" * 200, ui.FONT_BODY, 100), ui.FONT_BODY)[0], 100)
        self.assertLessEqual(len(ui.wrap("word " * 100, ui.FONT_SMALL, 200, 2)), 2)


if __name__ == "__main__":
    unittest.main()
