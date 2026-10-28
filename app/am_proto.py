# Air Merge transfer protocol: offers, consent answers, file data with acks, results.
#
# Message = kind byte + body (little endian), never longer than link.capacity().
# `tid` is a 1-byte transfer id picked by the sender; anything for another tid is ignored.
#   H <B version>                          hello after linking
#   O <B tid><H index><H count> + piece    offer: the manifest JSON split into pieces
#   A <B tid>                              receiver accepts the offer
#   D <B tid><B reason>                    decline: 0 declined 1 too big 2 invalid 3 busy 4 timed out
#   F <B tid><H file><I offset> + data     file data
#   K <B tid><I bytes_received>            receiver progress ack (every 8 chunks, file ends)
#   V <B tid><B ok> + reason text          receiver verified and installed (1) or failed (0)
#   X <B tid>                              cancel by either side
#   S <B tid><B phase>                     receiver status: 1 checking files, 2 installing
#                                          (the sender can no longer cancel once installing)
# D may carry a UTF-8 reason after the code byte (e.g. "USB disk mode is on").
#
# Engine is link agnostic (anything with send/capacity) so it runs in tests on a fake
# loopback pair. The UI drives it: on_message() for every received message, tick()
# once per frame, and drains `events`.

import json
import random
import struct

import am_fs

VERSION = 1
WINDOW = 24             # unacknowledged F chunks in flight
ACK_EVERY = 8           # receiver acks every N chunks (and at file ends)
BUDGET_MS = 25          # per-frame time budget for sending / hashing / copying
MAX_SENDS = 32          # hard cap of messages per frame (in case the clock is per frame)
ASK_MS = 30000          # receiver auto-declines an unanswered offer
ANSWER_MS = 45000       # sender gives up waiting for an answer
LOAD_MS = 10000         # offer pieces stop arriving
STALL_MS = 2500         # no ack for this long: resend from the last acked byte
FAIL_MS = 20000         # no progress at all for this long: give up
RESULT_MS = 180000      # sender waits this long for V after the last ack
KEEPALIVE_MS = 400      # receiver re-acks unacked data this often
MAX_PIECES = 6000
MAX_OFFER = 64 * 1024
MIN_CAP = 19

_H = 72
_O = 79
_A = 65
_D = 68
_F = 70
_K = 75
_V = 86
_X = 88
_S = 83

CHECKING = 1            # S phases
INSTALLING = 2

DECLINED = 0
TOO_BIG = 1
INVALID = 2
BUSY = 3
TIMED_OUT = 4

# engine states
IDLE = 0
PREP = 1        # sender hashing the app
OFFER = 2       # sender streaming the offer / waiting for the answer
SEND = 3        # sender streaming file data
AWAIT = 4       # sender: all data acked, waiting for V
LOAD = 5        # receiver collecting offer pieces
ASK = 6         # receiver asking the user
RECV = 7        # receiver writing file data
VERIFY = 8      # receiver hashing staged files
INSTALL = 9     # receiver copying into /system

SENDER_STATES = (PREP, OFFER, SEND, AWAIT)
RECEIVER_STATES = (LOAD, ASK, RECV, VERIFY, INSTALL)


def _sub(a, b):
    return a - b


def decline_code(reason):
    return TOO_BIG if reason and reason.startswith("too big") else INVALID


class Engine:
    def __init__(self, link, clock=None, diff=None):
        """clock() -> ms that advances within a frame (time.ticks_ms on the badge);
        diff(a, b) -> a - b in ms (time.ticks_diff, wrap safe)."""
        self.link = link
        self.clock = clock
        self.diff = diff if diff is not None else _sub
        self.events = []
        self.tid = random.getrandbits(8)
        self.partner_version = None
        self.central = False
        self.orphan = False      # link dropped while verifying/installing: finish locally
        self.rewinds = 0         # go-back resends so far (diagnostics)
        self._busy_tid = -1
        self._clear()

    # --- plumbing ---------------------------------------------------------------------
    def _clear(self):
        self.state = IDLE
        self.state_at = 0
        self.m = None
        self.slug = None
        self.total = 0
        self.done = 0
        self.ctl = []
        self._gen = None
        self._res = None
        self._reader = None
        self._staging = None
        self._pieces = None
        self._starts = None
        self._samples = []
        self.remote_phase = 0    # sender: last S phase from the receiver

    def _elapsed(self, start, now):
        try:
            return self.diff(self._now(now), start)
        except Exception:
            return 0

    def _now(self, fallback):
        c = self.clock
        if c is None:
            return fallback
        try:
            return c()
        except Exception:
            return fallback

    def cap(self):
        try:
            c = int(self.link.capacity())
        except Exception:
            c = MIN_CAP
        return c if c >= MIN_CAP else MIN_CAP

    def _send(self, msg):
        try:
            return bool(self.link.send(msg))
        except Exception:
            return False

    def _q(self, msg):
        self.ctl.append(msg)

    def _flush(self):
        """Send queued control messages in order; False if some are still waiting."""
        ctl = self.ctl
        while ctl:
            if not self._send(ctl[0]):
                return False
            ctl.pop(0)
        return True

    def _set(self, state, now):
        self.state = state
        self.state_at = now

    def _event(self, *e):
        self.events.append(e)

    def _close_gen(self):
        g = self._gen
        self._gen = None
        if g is not None:
            try:
                g.close()
            except Exception:
                pass

    def _drive(self, now, max_steps):
        """Advance self._gen within the frame budget; True when it has finished."""
        start = self._now(now)
        n = 0
        g = self._gen
        while True:
            try:
                next(g)
            except StopIteration:
                self._gen = None
                return True
            n += 1
            if n >= max_steps or self._elapsed(start, now) >= BUDGET_MS:
                return False

    def _cleanup(self, now):
        self._close_gen()
        if self._reader is not None:
            self._reader.close()
        if self._staging is not None:
            try:
                self._staging.discard()
            except Exception:
                pass
        ctl = self.ctl
        self._clear()
        self.ctl = ctl
        self.state_at = now

    def _starts_of(self, m):
        starts = []
        t = 0
        for e in m["files"]:
            starts.append(t)
            t += e[1]
        return starts

    def _locate(self, pos):
        """Global byte position -> (file index, offset), skipping empty files."""
        files = self.m["files"]
        starts = self._starts
        i = 0
        n = len(files)
        while i < n and (files[i][1] == 0 or pos >= starts[i] + files[i][1]):
            i += 1
        if i >= n:
            return n, 0
        return i, pos - starts[i]

    def _sample(self, now):
        s = self._samples
        if not s or now - s[-1][0] >= 250:
            s.append((now, self.done))
            while len(s) > 2 and now - s[0][0] > 3000:
                s.pop(0)

    def speed(self):
        """Bytes per second over the last ~3 s."""
        s = self._samples
        if len(s) < 2:
            return 0
        dt = s[-1][0] - s[0][0]
        if dt <= 0:
            return 0
        return (s[-1][1] - s[0][1]) * 1000 // dt

    def current_file(self):
        m = self.m
        if m is None:
            return ""
        i, _ = self._locate(self.done if self.done < self.total else self.total - 1)
        files = m["files"]
        if 0 <= i < len(files):
            return files[i][0]
        return files[-1][0] if files else ""

    def busy(self):
        return self.state != IDLE

    def role(self):
        if self.state in SENDER_STATES:
            return "send"
        if self.state in RECEIVER_STATES:
            return "recv"
        return None

    # --- link lifecycle ---------------------------------------------------------------
    def linked(self, central, now):
        """A new link is up: forget the old session and say hello."""
        if self.state not in (VERIFY, INSTALL):
            self._cleanup(now)
        self.ctl = []
        self._busy_tid = -1
        self.orphan = False
        self.central = bool(central)
        self.partner_version = None
        self._q(struct.pack("<BB", _H, VERSION))

    def dropped(self, now):
        """The link went away. True if a verify/install keeps going locally."""
        self.ctl = []
        if self.state in (VERIFY, INSTALL):
            self.orphan = True
            return True
        if self.state != IDLE:
            self._cleanup(now)
        return False

    def abort(self):
        """App exit: stop everything now (closing an install generator relocks /system)."""
        self._cleanup(0)
        self.ctl = []

    # --- user actions ------------------------------------------------------------------
    def send_app(self, slug, now):
        """Sender consent given: hash the app, then offer it."""
        if self.state != IDLE:
            return False
        self._cleanup(now)
        self.tid = (self.tid + 1) & 0xFF
        self.slug = slug
        self._res = []
        self._gen = am_fs.manifest_steps(slug, self._res)
        self._set(PREP, now)
        return True

    def accept(self, now):
        if self.state != ASK:
            return False
        try:
            self._staging = am_fs.Staging(self.m)
        except Exception:
            self._q(struct.pack("<BBB", _D, self.tid, INVALID))
            self._staging = None
            self._cleanup(now)
            self._event("failed", "couldn't make room to receive")
            return False
        self._q(struct.pack("<BB", _A, self.tid))
        self._rfi, self._rfo = self._locate(0)
        self._unacked = 0
        self._k_at = now
        self._rx_at = now
        self._samples = []
        self._set(RECV, now)
        if self.total == 0:
            self._start_verify(now)
        return True

    def _decline_msg(self, tid, code, why=""):
        body = why.encode()[:self.cap() - 3] if why else b""
        return struct.pack("<BBB", _D, tid, code) + body

    def decline(self, now, code=DECLINED):
        if self.state != ASK:
            return False
        self._q(self._decline_msg(self.tid, code))
        self._cleanup(now)
        return True

    def can_cancel(self):
        st = self.state
        return st != INSTALL and not (st == AWAIT and self.remote_phase >= INSTALLING)

    def cancel(self, now):
        """BACK during an offer or transfer. False once installing (too late to stop),
        on either side."""
        st = self.state
        if st == IDLE:
            return True
        if not self.can_cancel():
            return False
        if st == ASK:
            return self.decline(now)
        if st != PREP:
            self._q(struct.pack("<BB", _X, self.tid))
        self._cleanup(now)
        return True

    # --- incoming ----------------------------------------------------------------------
    def on_message(self, m, now):
        try:
            self._handle(m, now)
        except Exception:
            pass   # malformed: ignore

    def _handle(self, m, now):
        n = len(m)
        if n < 2:
            return
        kind = m[0]
        if kind == _H:
            self.partner_version = m[1]
            return
        tid = m[1]
        st = self.state
        if kind == _O:
            if n >= 6:
                self._on_offer(tid, m, now)
            return
        if tid != self.tid:
            return
        if kind == _F:
            if st == RECV and n > 8:
                fi, off = struct.unpack_from("<HI", m, 2)
                self._on_data(fi, off, m[8:], now)
        elif kind == _K:
            if st in (SEND, AWAIT) and n >= 6:
                self._on_ack(struct.unpack_from("<I", m, 2)[0], now)
        elif kind == _A:
            if st == OFFER and self._piece >= self._count:
                self._start_send(now)
        elif kind == _D:
            if st == OFFER and n >= 3:
                code = m[2]
                why = ""
                try:
                    why = bytes(m[3:]).decode()
                except Exception:
                    pass
                self._cleanup(now)
                self._event("declined", code, why)
        elif kind == _S:
            if st in (SEND, AWAIT) and n >= 3 and m[2] > self.remote_phase:
                self.remote_phase = m[2]
                self._event("phase", m[2])
        elif kind == _V:
            if st in (SEND, AWAIT) and n >= 3:
                ok = m[2] == 1
                why = ""
                try:
                    why = bytes(m[3:]).decode()
                except Exception:
                    pass
                name = self.m["name"] if self.m else ""
                self._cleanup(now)
                self._event("result", ok, why, name)
        elif kind == _X:
            if st in (OFFER, SEND, AWAIT, LOAD, ASK, RECV, VERIFY):
                self._cleanup(now)
                self._event("cancelled", "partner")

    # --- sender --------------------------------------------------------------------------
    def _prep(self, now):
        if not self._drive(now, 6):
            return
        m = self._res[0] if self._res else None
        self._res = None
        if m is None:
            self._cleanup(now)
            self._event("failed", "couldn't read the app")
            return
        why = am_fs.check_manifest(m, 1 << 40)
        if why:
            self._cleanup(now)
            self._event("failed", "can't send: " + why)
            return
        try:
            js = json.dumps(m, separators=(",", ":"))
        except TypeError:
            js = json.dumps(m)
        data = js.encode()
        if len(data) > MAX_OFFER:
            self._cleanup(now)
            self._event("failed", "too many files to offer")
            return
        self.m = m
        self.total = m["total"]
        self._starts = self._starts_of(m)
        self._offer_data = data
        self._split(now)
        if self._count > MAX_PIECES:
            self._cleanup(now)
            self._event("failed", "too many files to offer")
            return
        self._set(OFFER, now)

    def _split(self, now):
        size = self.cap() - 6
        self._piece_size = size
        self._count = (len(self._offer_data) + size - 1) // size
        self._piece = 0
        self._sent_at = now

    def _offer(self, now):
        if self._piece >= self._count:
            if now - self._sent_at > ANSWER_MS:
                self._q(struct.pack("<BB", _X, self.tid))
                self._cleanup(now)
                self._event("failed", "no answer")
            return
        sends = 0
        while self._piece < self._count and sends < MAX_SENDS:
            size = self._piece_size
            if size + 6 > self.cap():   # capacity shrank: start the offer again
                self._split(now)
                size = self._piece_size
            i = self._piece
            body = self._offer_data[i * size:(i + 1) * size]
            msg = struct.pack("<BBHH", _O, self.tid, i, self._count) + body
            if not self._send(msg):
                break
            self._piece += 1
            sends += 1
            self._sent_at = now

    def _start_send(self, now):
        self._offer_data = None
        self._reader = am_fs.Reader(self.m["slug"])
        self._pos = 0
        self.done = 0
        self._ends = []
        self._ack_at = now
        self._prog_at = now
        self._samples = []
        self._set(SEND, now)
        self._event("accepted")
        if self.total == 0:
            self._set(AWAIT, now)

    def _on_ack(self, b, now):
        if b > self.total:
            b = self.total
        if b <= self.done:
            # duplicate ack: the receiver is stuck at b; resend from there soon
            if self.state == SEND and self._pos > b and now - self._ack_at > 300:
                self._rewind(b, now)
            return
        self.done = b
        self._ack_at = now
        self._prog_at = now
        if self._pos < b:
            self._pos = b
        ends = self._ends
        while ends and ends[0] <= b:
            ends.pop(0)
        if b >= self.total and self.state == SEND:
            self._reader.close()
            self._set(AWAIT, now)

    def _rewind(self, b, now):
        self._pos = b
        self._ends = []
        self._ack_at = now
        self.rewinds += 1

    def _send_data(self, now):
        if now - self._prog_at > FAIL_MS:
            self._q(struct.pack("<BB", _X, self.tid))
            self._cleanup(now)
            self._event("failed", "transfer stalled")
            return
        if self._ends and now - self._ack_at > STALL_MS:
            self._rewind(self.done, now)
        files = self.m["files"]
        start = self._now(now)
        sends = 0
        while len(self._ends) < WINDOW and self._pos < self.total and sends < MAX_SENDS:
            fi, fo = self._locate(self._pos)
            rel, size, _ = files[fi]
            n = self.cap() - 8
            if n > size - fo:
                n = size - fo
            try:
                data = self._reader.read(fi, rel, fo, n)
            except OSError:
                data = b""
            if len(data) != n:
                self._q(struct.pack("<BB", _X, self.tid))
                self._cleanup(now)
                self._event("failed", "couldn't read " + rel)
                return
            if not self._send(struct.pack("<BBHI", _F, self.tid, fi, fo) + data):
                break
            if not self._ends:
                self._ack_at = now
            self._pos += n
            self._ends.append(self._pos)
            sends += 1
            if self._elapsed(start, now) >= BUDGET_MS:
                break

    # --- receiver ------------------------------------------------------------------------
    def _on_offer(self, tid, m, now):
        idx, cnt = struct.unpack_from("<HH", m, 2)
        piece = bytes(m[6:])
        st = self.state
        if st == LOAD and tid == self.tid:
            if idx == 0 and cnt != len(self._pieces):
                self._pieces = None
            elif cnt != len(self._pieces) or idx >= cnt:
                return
        elif idx != 0:
            return
        elif st == IDLE or (st in (PREP, OFFER) and not self.central):
            if st != IDLE:
                # both offered at once: the central badge's offer wins
                self.cancel(now)
                self._event("cancelled", "collision")
            self.tid = tid
            self._set(LOAD, now)
            self._pieces = None
        else:
            if tid != self._busy_tid and tid != self.tid:
                self._busy_tid = tid
                self._q(struct.pack("<BBB", _D, tid, BUSY))
            return
        if self._pieces is None:
            if cnt == 0 or cnt > MAX_PIECES:
                self._q(struct.pack("<BBB", _D, tid, INVALID))
                self._cleanup(now)
                self._event("offer_bad", None, "offer too large")
                return
            self._pieces = [None] * cnt
            self._have = 0
            self._bytes = 0
        if self._pieces[idx] is None:
            self._have += 1
            self._bytes += len(piece)
            if self._bytes > MAX_OFFER:
                self._q(struct.pack("<BBB", _D, tid, INVALID))
                self._cleanup(now)
                self._event("offer_bad", None, "offer too large")
                return
        self._pieces[idx] = piece
        self.state_at = now
        if self._have < cnt:
            return
        data = b"".join(self._pieces)
        self._pieces = None
        try:
            offer = json.loads(data.decode())
        except Exception:
            offer = None
        why = "invalid offer" if offer is None else None
        if why is None:
            free = am_fs.free_stage()
            fs = am_fs.free_system()
            why = am_fs.check_manifest(offer, free if free < fs else fs)
        code = decline_code(why)
        if why is None and am_fs.msc_busy():
            why = "USB disk mode is on"     # /system can't be unlocked: don't waste a transfer
            code = BUSY
        if why:
            self._q(self._decline_msg(tid, code, why))
            self._cleanup(now)
            self._event("offer_bad", offer if isinstance(offer, dict) else None, why)
            return
        self.m = offer
        self.total = offer["total"]
        self._starts = self._starts_of(offer)
        self.done = 0
        self._set(ASK, now)
        self._event("offer", offer)

    def _ack(self, now):
        msg = struct.pack("<BBI", _K, self.tid, self.done)
        ctl = self.ctl
        if ctl and ctl[-1][0] == _K:
            ctl[-1] = msg       # newer ack supersedes the queued one
        else:
            ctl.append(msg)
        self._unacked = 0
        self._k_at = now

    def _on_data(self, fi, off, data, now):
        if fi != self._rfi or off != self._rfo:
            if now - self._k_at > 300:
                self._ack(now)        # duplicate / out of order: tell the sender where we are
            return
        try:
            ok = self._staging.write(fi, off, data)
        except OSError:
            ok = False
        if not ok:
            self._q(struct.pack("<BB", _X, self.tid))
            self._cleanup(now)
            self._event("failed", "couldn't save the data")
            return
        n = len(data)
        self.done += n
        self._rx_at = now
        self._unacked += 1
        size = self.m["files"][fi][1]
        self._rfo += n
        end = self._rfo >= size
        if end:
            self._rfi, self._rfo = self._locate(self.done)
        if end or self._unacked >= ACK_EVERY:
            self._ack(now)
        if self.done >= self.total:
            self._start_verify(now)

    def _status(self, phase):
        if not self.orphan:
            self._q(struct.pack("<BBB", _S, self.tid, phase))

    def _start_verify(self, now):
        self._status(CHECKING)
        self._res = []
        self._gen = self._staging.verify_steps(self._res)
        self._set(VERIFY, now)

    def _recv(self, now):
        if now - self._rx_at > FAIL_MS:
            self._q(struct.pack("<BB", _X, self.tid))
            self._cleanup(now)
            self._event("failed", "transfer stalled")
            return
        if self._unacked and now - self._k_at > KEEPALIVE_MS:
            self._ack(now)

    def _result(self, ok, why, now):
        if not self.orphan:
            body = why.encode()[:self.cap() - 3]
            self._q(struct.pack("<BBB", _V, self.tid, 1 if ok else 0) + body)

    def _verify(self, now):
        if not self._drive(now, 6):
            return
        ok = bool(self._res and self._res[0])
        if not ok:
            self._result(False, "files didn't match", now)
            self._cleanup(now)
            self._event("failed", "files didn't match")
            return
        self._status(INSTALLING)
        self._res = []
        self._staging = None          # install_steps owns the staged copy now
        self._gen = am_fs.install_steps(self.m, self._res)
        self._set(INSTALL, now)

    def _install(self, now):
        if not self._drive(now, 4):
            return
        err = self._res[0] if self._res else "install failed"
        m = self.m
        self._result(err is None, err or "", now)
        if err is not None:
            am_fs.clean_stage()
        self._cleanup(now)
        if err is None:
            self._event("installed", m)
        else:
            self._event("failed", err)

    # --- per frame --------------------------------------------------------------------
    def tick(self, now):
        ready = self._flush()
        st = self.state
        if st == PREP:
            self._prep(now)
        elif st == OFFER:
            if ready:
                self._offer(now)
        elif st == SEND:
            if ready:
                self._send_data(now)
        elif st == AWAIT:
            if now - self.state_at > RESULT_MS:
                self._q(struct.pack("<BB", _X, self.tid))
                self._cleanup(now)
                self._event("failed", "no answer")
        elif st == LOAD:
            if now - self.state_at > LOAD_MS:
                self._cleanup(now)
        elif st == ASK:
            if now - self.state_at > ASK_MS:
                self.decline(now, TIMED_OUT)
                self._event("ask_timeout")
        elif st == RECV:
            self._recv(now)
        elif st == VERIFY:
            self._verify(now)
        elif st == INSTALL:
            self._install(now)
        if self.state in (SEND, AWAIT, RECV):
            self._sample(now)
        if self.ctl:
            self._flush()

    def ask_left(self, now):
        """ms until the receiver auto-declines (ASK state)."""
        left = ASK_MS - (now - self.state_at)
        return left if left > 0 else 0
