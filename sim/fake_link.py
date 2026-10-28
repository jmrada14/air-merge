"""Fake `badgeware.link` and `pairing` firmware modules for the desktop simulator.

Two simulator processes talk over UDP on localhost (`--link-port` is this badge,
`--peer-port` the other one). The API mirrors what /system/apps/pong uses:

    link = badgelink.Link("pong"); proximity = badgelink.Proximity(link)
    link.pump(); link.linked; link.receive(); link.send(b"..."); link.update()
    link.partner (.id bytes, .name str); link.central; link.id; link.name
    link.leave(); link.close()
    proximity.update(now); proximity.syncing(); proximity.progress(now)
    proximity.cooling(now); proximity.lights(now); proximity.rest(id, now)
    proximity.clear()
    badgelink.draw_identicon(screen, id, x, y, size); badgelink.identicon(id)
    pairing.Pairing(x, y, scale).draw_together(now) / draw_apart() / draw_loop(now)

Linking: both sides say hello every HELLO_MS of badge time. While a peer on
the same channel is heard and we are not cooling down, the proximity "sync"
runs for SYNC_MS of badge time; then this side is ready. The link is up once
both sides are ready. No packet from the peer for TIMEOUT_MS drops the link,
like walking away. Without a peer port nothing ever links, so solo runs are
unaffected. `mute_at` (badge ticks) makes this badge go silent from then on
(sends nothing, ignores everything) to test a mid-race drop.

Messages are capped at MAX_MSG bytes so oversize packets fail loudly here
rather than mysteriously on the badge.
"""

import socket
import sys
import types
import zlib

SYNC_MS = 1500
COOL_MS = 3000
TIMEOUT_MS = 1500
HELLO_MS = 150
MAX_MSG = 20


class _Cfg:
    port = None
    peer = None
    name = "badge"
    id = b"\x00\x00\x00\x00\x00\x01"
    mute_at = None
    badge = None
    sent = 0
    received = 0
    max_len = 0


CFG = _Cfg()


def _now():
    return CFG.badge.ticks if CFG.badge is not None else 0


def _muted():
    return CFG.mute_at is not None and _now() >= CFG.mute_at


class _Peer:
    def __init__(self, id_, name):
        self.id = id_
        self.name = name

    def __repr__(self):
        return "<peer %s %s>" % (self.name, self.id.hex())


class Link:
    def __init__(self, channel):
        self.channel = str(channel)
        self.id = CFG.id
        self.name = CFG.name
        self.central = bool(CFG.port is not None and CFG.peer is not None and CFG.port < CFG.peer)
        self.linked = False
        self.partner = None
        self._peer = None          # last peer heard on our channel
        self._peer_ready = False
        self._last_rx = -1 << 30
        self._ready = False        # this side finished its proximity sync
        self._inbox = []
        self._hello_at = -1 << 30
        self._sock = None
        if CFG.port is not None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", CFG.port))
            s.setblocking(False)
            self._sock = s

    # -- transport --------------------------------------------------------
    def _tx(self, data):
        if self._sock is None or CFG.peer is None or _muted():
            return
        try:
            self._sock.sendto(data, ("127.0.0.1", CFG.peer))
        except OSError:
            pass

    def _hello(self):
        ch = self.channel.encode()[:32]
        nm = self.name.encode()[:32]
        self._tx(b"H" + bytes((len(ch),)) + ch + bytes((len(self.id),)) + self.id
                 + bytes((1 if self._ready else 0,)) + nm)

    def pump(self):
        now = _now()
        s = self._sock
        while s is not None:
            try:
                data, _ = s.recvfrom(256)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if _muted() or not data:
                continue
            kind = data[:1]
            if kind == b"H":
                try:
                    n = data[1]
                    ch = data[2:2 + n].decode()
                    p = 2 + n
                    m = data[p]
                    pid = bytes(data[p + 1:p + 1 + m])
                    p += 1 + m
                    ready = bool(data[p])
                    name = data[p + 1:].decode("utf-8", "replace")
                except (IndexError, UnicodeDecodeError):
                    continue
                if ch != self.channel:
                    continue
                if self._peer is None or self._peer.id != pid:
                    self._peer = _Peer(pid, name)
                self._peer_ready = ready
                self._last_rx = now
            elif kind == b"M":
                self._last_rx = now
                if self.linked:
                    self._inbox.append(bytes(data[1:]))
                    CFG.received += 1
            elif kind == b"B":
                self._peer_ready = False
                self._last_rx = now
                if self.linked:
                    self._drop()
        if self.linked and now - self._last_rx > TIMEOUT_MS:
            self._drop()
            self._peer = None
        if (not self.linked and self._ready and self._peer_ready and self._peer is not None
                and now - self._last_rx <= TIMEOUT_MS):
            self.linked = True
            self.partner = self._peer

    def _drop(self):
        self.linked = False
        self.partner = None
        self._ready = False
        self._inbox = []

    def _present(self, now):
        return self._peer is not None and now - self._last_rx <= TIMEOUT_MS

    def receive(self):
        out = self._inbox
        self._inbox = []
        return out

    def send(self, data):
        data = bytes(data)
        if len(data) > MAX_MSG:
            raise ValueError("link message too long (%d > %d bytes): %r" % (len(data), MAX_MSG, data))
        if not self.linked:
            return False
        if len(data) > CFG.max_len:
            CFG.max_len = len(data)
        CFG.sent += 1
        self._tx(b"M" + data)
        return True

    def update(self):
        now = _now()
        if now - self._hello_at >= HELLO_MS:
            self._hello_at = now
            self._hello()

    def leave(self):
        self._tx(b"B")
        self._drop()

    def close(self):
        self._tx(b"B")
        self._drop()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class Proximity:
    def __init__(self, link):
        self.link = link
        self._sync_at = None
        self._cool_until = None

    def update(self, now):
        link = self.link
        if link.linked:
            self._sync_at = None
            return
        if self.cooling(now) is not None:
            self._sync_at = None
            return
        if not link._present(now):
            self._sync_at = None
            link._ready = False
            return
        if self._sync_at is None:
            self._sync_at = now
        if now - self._sync_at >= SYNC_MS:
            link._ready = True

    def syncing(self):
        if self._sync_at is None or self.link.linked:
            return None
        return self.link._peer

    def progress(self, now):
        if self._sync_at is None:
            return 0.0
        return max(0.0, (now - self._sync_at) / SYNC_MS)

    def cooling(self, now):
        if self._cool_until is None:
            return None
        left = self._cool_until - now
        if left <= 0:
            self._cool_until = None
            return None
        return left

    def lights(self, now):
        if self._sync_at is None:
            return None
        p = min(1.0, self.progress(now))
        pulse = 0.5 + 0.5 * ((now // 120) % 2)
        return (p * pulse, p * pulse, p * pulse, p * pulse)

    def rest(self, partner_id, now):
        self._cool_until = now + COOL_MS
        self._sync_at = None

    def clear(self):
        self._sync_at = None
        self.link._ready = False


# --------------------------------------------------------------------------
# identicons
# --------------------------------------------------------------------------

def identicon(id_):
    h = zlib.crc32(bytes(id_)) & 0xFFFFFFFF
    bits = h & 0x7FFF  # 5 rows x 3 columns, mirrored to 5x5
    hue = ((h >> 16) & 0xFF) / 255.0
    ink = color.hsv(hue, 0.65, 1.0)  # noqa: F821 (firmware global installed by badge_sim)
    return bits, ink


def draw_identicon(scr, id_, x, y, size):
    bits, ink = identicon(id_)
    cell = size / 5.0
    scr.pen = color.rgb(20, 20, 32)  # noqa: F821
    scr.rectangle(x, y, size, size)
    scr.pen = ink
    for row in range(5):
        for col in range(3):
            if bits >> (row * 3 + col) & 1:
                scr.rectangle(x + col * cell, y + row * cell, cell, cell)
                scr.rectangle(x + (4 - col) * cell, y + row * cell, cell, cell)


def badge_id_hex():
    return CFG.id.hex()


# --------------------------------------------------------------------------
# pairing animation
# --------------------------------------------------------------------------

class Pairing:
    """Two little badges: drifting together (loop), touching (together) or apart."""

    def __init__(self, x, y, scale):
        self.x = x
        self.y = y
        self.s = scale

    def _badge(self, cx, flip):
        s = self.s
        w = 5 * s
        h = 8 * s
        x = cx - w / 2
        y = self.y
        screen.pen = color.rgb(230, 230, 240)  # noqa: F821
        screen.rectangle(x, y, w, h)
        screen.pen = color.rgb(30, 30, 46)  # noqa: F821
        screen.rectangle(x + s * 0.5, y + s * 0.5, w - s, h * 0.55)
        screen.pen = color.rgb(255, 204, 51)  # noqa: F821
        dot = x + (w - s * 1.5 if flip else s * 0.5)
        screen.rectangle(dot, y + h - s * 2, s, s)

    def _pair(self, gap):
        mid = self.x + 86
        w = 5 * self.s
        self._badge(mid - gap / 2 - w / 2, False)
        self._badge(mid + gap / 2 + w / 2, True)

    def draw_loop(self, now):
        t = (now % 2000) / 2000.0
        gap = 80 * (1 - t) if t < 0.7 else 0
        self._pair(gap)

    def draw_together(self, now):
        self._pair(0)
        if (now // 200) % 2:
            screen.pen = color.rgb(95, 237, 131)  # noqa: F821
            screen.rectangle(self.x + 86 - 1, self.y - 6, 2, 4)

    def draw_apart(self):
        self._pair(110)


# --------------------------------------------------------------------------

def install(badge, port=None, peer=None, name="badge", id_=None, mute_at=None):
    """Register fake `badgeware.link` and `pairing` modules (call after badge_sim.install)."""
    CFG.badge = badge
    CFG.port = port
    CFG.peer = peer
    CFG.name = name
    if id_ is not None:
        CFG.id = bytes(id_)
    CFG.mute_at = mute_at

    lm = types.ModuleType("badgeware.link")
    lm.Link = Link
    lm.Proximity = Proximity
    lm.identicon = identicon
    lm.draw_identicon = draw_identicon
    lm.badge_id_hex = badge_id_hex
    sys.modules["badgeware.link"] = lm
    bw = sys.modules.get("badgeware")
    if bw is not None:
        bw.link = lm

    pm = types.ModuleType("pairing")
    pm.Pairing = Pairing
    sys.modules["pairing"] = pm
    return CFG
