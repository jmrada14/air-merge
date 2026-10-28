"""In-process two-badge driver for protocol tests (no UI): each Side owns a sandbox root,
one end of a FakeLink pair and an am_proto.Engine."""

import fakes  # noqa: F401  (patches os/open, registers vfs/rp2)
import am_fs
import am_proto


class Side:
    def __init__(self, root, link):
        self.root = root
        self.link = link
        self.clock_ms = 0
        self.engine = am_proto.Engine(link, lambda: self.clock_ms)
        self.events = []

    def use(self):
        am_fs.ROOT = self.root

    def step(self, now):
        self.use()
        self.clock_ms = now
        for m in self.link.receive():
            self.engine.on_message(m, now)
        self.engine.tick(now)
        self.events.extend(self.engine.events)
        self.engine.events = []

    def kinds(self):
        return [e[0] for e in self.events]


class Duo:
    def __init__(self, root_a, root_b, seed=1, refuse=0.25):
        self.la, self.lb = fakes.link_pair(seed, refuse)
        self.a = Side(root_a, self.la)
        self.b = Side(root_b, self.lb)
        self.now = 1000
        self.a.use()
        self.a.engine.linked(True, self.now)
        self.b.use()
        self.b.engine.linked(False, self.now)

    def upgrade(self):
        self.la.mtu_ok = True
        self.lb.mtu_ok = True

    def frame(self, dt=33, per_frame=None, hook=None):
        self.now += dt
        self.a.step(self.now)
        self.b.step(self.now)
        self.la.deliver(per_frame)
        self.lb.deliver(per_frame)
        if hook is not None:
            hook(self)

    def run(self, until, max_frames=20000, **kw):
        for i in range(max_frames):
            if until(self):
                return i
            self.frame(**kw)
        raise AssertionError("condition not reached in %d frames" % max_frames)
