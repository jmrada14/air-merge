"""Test helpers: sandbox filesystem with a read-only /system, fake vfs/rp2, and a fake
loopback link pair. Import this before importing any am_* module."""

import builtins
import errno
import os
import random
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
if APP not in sys.path:
    sys.path.insert(0, APP)
sys.dont_write_bytecode = True

_real = {
    "open": builtins.open,
    "mkdir": os.mkdir,
    "rename": os.rename,
    "remove": os.remove,
    "rmdir": os.rmdir,
}


class FS:
    root = None
    roots = []
    mount = "ro"          # "ro" | "rw" | "none"
    msc_busy = False
    rw_count = 0
    open_writes = 0
    violations = []
    fail_write_after = None  # bytes written under /system before ENOSPC
    written = 0


def _system(path):
    if not isinstance(path, str):
        return False
    ap = os.path.normpath(os.path.abspath(path))
    for root in FS.roots:
        sysdir = os.path.join(root, "system")
        if ap == sysdir or ap.startswith(sysdir + "/"):
            return True
    return False


def _guard(path, write):
    if not _system(path):
        return
    if FS.mount == "none":
        raise OSError(errno.ENOENT, "unmounted")
    if write and FS.mount != "rw":
        raise OSError(errno.EROFS, "read-only")


class _WFile:
    def __init__(self, fh, sysfile):
        self._fh = fh
        self._sys = sysfile
        if sysfile:
            FS.open_writes += 1

    def write(self, b):
        if self._sys:
            if FS.mount != "rw":
                raise OSError(errno.EROFS, "read-only")
            if FS.fail_write_after is not None and FS.written + len(b) > FS.fail_write_after:
                raise OSError(errno.ENOSPC, "full")
            FS.written += len(b)
        return self._fh.write(b)

    def close(self):
        if self._sys and self._fh is not None and not self._fh.closed:
            FS.open_writes -= 1
        return self._fh.close()

    def __getattr__(self, k):
        return getattr(self._fh, k)


def _open(path, mode="r", *a, **k):
    w = any(c in mode for c in "wax+")
    _guard(path, w)
    fh = _real["open"](path, mode, *a, **k)
    if w and _system(path):
        return _WFile(fh, True)
    return fh


def _mkdir(path, *a, **k):
    _guard(path, True)
    return _real["mkdir"](path, *a, **k)


def _rename(a, b, **k):
    _guard(a, True)
    _guard(b, True)
    return _real["rename"](a, b, **k)


def _remove(path, **k):
    _guard(path, True)
    return _real["remove"](path, **k)


def _rmdir(path, **k):
    _guard(path, True)
    return _real["rmdir"](path, **k)


class Flash:
    pass


class VfsFat:
    def __init__(self, dev):
        self.dev = dev


def _umount(mp):
    assert mp == "/system"
    if FS.mount == "none":
        raise OSError(errno.EINVAL, "not mounted")
    if FS.open_writes:
        FS.violations.append("umount with open files")
    FS.mount = "none"


def _vmount(fs, mp, readonly=False):
    assert mp == "/system" and isinstance(fs, VfsFat)
    if FS.mount != "none":
        raise OSError(errno.EPERM, "already mounted")
    FS.mount = "ro" if readonly else "rw"
    if not readonly:
        FS.rw_count += 1


def install_fakes():
    """Patch builtins/os and register vfs, rp2 once per process."""
    if getattr(builtins, "_am_fakes", False):
        return
    builtins._am_fakes = True
    builtins.open = _open
    os.mkdir = _mkdir
    os.rename = _rename
    os.remove = _remove
    os.rmdir = _rmdir
    vm = types.ModuleType("vfs")
    vm.VfsFat = VfsFat
    vm.mount = _vmount
    vm.umount = _umount
    sys.modules["vfs"] = vm
    rm = types.ModuleType("rp2")
    rm.Flash = Flash
    rm.is_msc_busy = lambda: FS.msc_busy
    sys.modules["rp2"] = rm


def reset():
    """Forget all sandboxes (deleting them) and restore the default fake state."""
    install_fakes()
    FS.roots, roots = [], FS.roots
    for r in roots:
        shutil.rmtree(r, ignore_errors=True)
    FS.roots = []
    FS.root = None


def new_root():
    """Fresh sandbox: <root>/system/apps (read-only), secrets.py, state/. Becomes am_fs.ROOT."""
    install_fakes()
    root = os.path.realpath(tempfile.mkdtemp(prefix="am_test_"))
    _real["mkdir"](os.path.join(root, "system"))
    _real["mkdir"](os.path.join(root, "system", "apps"))
    _real["mkdir"](os.path.join(root, "state"))
    with _real["open"](os.path.join(root, "secrets.py"), "w") as f:
        f.write("WIFI_PASSWORD = 'hunter2'\n")
    with _real["open"](os.path.join(root, "state", "contacts.json"), "w") as f:
        f.write('{"people": []}')
    FS.roots.append(root)
    FS.root = root
    FS.mount = "ro"
    FS.msc_busy = False
    FS.rw_count = 0
    FS.open_writes = 0
    FS.violations = []
    FS.fail_write_after = None
    FS.written = 0
    builtins.AM_ROOT = root
    import am_fs
    am_fs.ROOT = root
    return root


def write_app(root, slug, files):
    """Create an app under <root>/system/apps/<slug> bypassing the read-only guard."""
    base = os.path.join(root, "system", "apps", slug)
    for rel, data in files.items():
        path = os.path.join(base, rel)
        d = os.path.dirname(path)
        parts = []
        while not os.path.isdir(d):
            parts.append(d)
            d = os.path.dirname(d)
        for d in reversed(parts):
            _real["mkdir"](d)
        with _real["open"](path, "wb") as f:
            f.write(data)
    return base


def read_tree(base, skip_hidden=True):
    out = {}
    for d, dirs, files in os.walk(base):
        if skip_hidden:
            dirs[:] = [x for x in dirs if not x.startswith(".") and x != "__pycache__"]
        for name in files:
            if skip_hidden and name.startswith("."):
                continue
            path = os.path.join(d, name)
            with _real["open"](path, "rb") as f:
                out[os.path.relpath(path, base).replace(os.sep, "/")] = f.read()
    return out


def rand_bytes(rng, n):
    return bytes(rng.getrandbits(8) for _ in range(n))


# --- fake link pair ----------------------------------------------------------------------

class Peer:
    def __init__(self, id_, name):
        self.id = id_
        self.name = name


class FakeLink:
    """One end of an in-order, lossless pipe. capacity() is 19 until upgrade(), 243 after.
    send() refuses messages over capacity, when unlinked, when the outbox is full, and at
    random (backpressure)."""

    def __init__(self, name, id_, rng, refuse=0.25, outbox=16):
        self.name = name
        self.id = id_
        self.rng = rng
        self.refuse = refuse
        self.outbox_max = outbox
        self.linked = True
        self.central = False
        self.other = None
        self.inbox = []
        self.outbox = []
        self.mtu_ok = False
        self.sent = 0
        self.refused = 0
        self.over = 0
        self.max_len = 0
        self.tap = None   # optional callable(msg) -> msg or None (drop), for tampering tests

    def capacity(self):
        return 243 if self.mtu_ok else 19

    def send(self, msg):
        msg = bytes(msg)
        if not self.linked:
            return False
        if len(msg) > self.capacity():
            self.over += 1
            return False
        if len(self.outbox) >= self.outbox_max or self.rng.random() < self.refuse:
            self.refused += 1
            return False
        self.outbox.append(msg)
        self.sent += 1
        if len(msg) > self.max_len:
            self.max_len = len(msg)
        return True

    def receive(self):
        out = self.inbox
        self.inbox = []
        return out

    def deliver(self, n=None):
        """Move up to n queued messages to the other side (in order)."""
        k = len(self.outbox) if n is None else min(n, len(self.outbox))
        for m in self.outbox[:k]:
            if self.tap is not None:
                m = self.tap(m)
                if m is None:
                    continue
            if self.other.linked:
                self.other.inbox.append(m)
        del self.outbox[:k]

    def drop(self):
        self.linked = False
        self.outbox = []
        self.inbox = []

    @property
    def partner(self):
        return Peer(self.other.id, self.other.name) if self.linked else None


def link_pair(seed=1, refuse=0.25):
    rng = random.Random(seed)
    a = FakeLink("Ada", b"\xa1" * 8, rng, refuse)
    b = FakeLink("Bo", b"\xb2" * 8, rng, refuse)
    a.other, b.other = b, a
    a.central = True
    return a, b
