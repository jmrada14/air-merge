"""Per-badge sandbox filesystem and fake `vfs` / `rp2` / `hashlib` modules.

`install(root, badge, ...)` (called by run.py before the app is imported):

- sets `builtins.AM_ROOT = root`; the app's `am_fs.p(path)` prefixes every absolute
  path with it, so `/system/apps/x` becomes `<root>/system/apps/x`.
- registers fake `vfs` and `rp2` modules. `/system` starts mounted **read-only**,
  like on the badge. `vfs.umount("/system")` unmounts it, `vfs.mount(vfs.VfsFat(
  rp2.Flash()), "/system")` remounts it read-write, `readonly=True` read-only.
- wraps `builtins.open` and `os.mkdir/rename/replace/remove/unlink/rmdir/listdir/
  ilistdir/stat/statvfs` with path checks that only act on paths inside `<root>`.
  Everything else passes straight through (the simulator itself writes PNGs under
  sim/out). Under `<root>/system`:
    * mounted read-only: writes raise OSError(30) (EROFS)
    * unmounted: every access raises OSError(2) (ENOENT), and importing an app
      module that has not been imported yet fails (on the badge the app's own .py
      files live under /system)
- fakes `os.statvfs` for `<root>/system` (FAT, 4096 B blocks, 3328 blocks) and
  `<root>` (LittleFS "/", 4096 B blocks, 256 blocks). Free space starts at
  `system_free_kb` / `stage_free_kb` and shrinks as files are written (block
  rounded). Writes that would exceed it raise OSError(28) (ENOSPC).
- replaces `hashlib` for the app with the MicroPython subset (sha256/sha1 objects
  with update() and digest() only, no hexdigest()).
- adds the MicroPython-only `os.ilistdir`.

Everything that happens is recorded in `FS` (mount transitions with badge ticks and
wall time, the longest time /system stayed writable, rejected writes, writes left
open across a remount, bytes written while read-write); `report()` returns lines
for the end-of-run summary.
"""

import builtins
import errno
import hashlib as _real_hashlib
import os
import stat as _stat
import sys
import time
import types

BLOCK = 4096
SYSTEM_BLOCKS = 3328          # measured: VfsFat on rp2.Flash(), 3328 x 4096 B
STAGE_BLOCKS = 256            # measured: VfsLfs2 "/", 1 MB
DEFAULT_SYSTEM_FREE_KB = 9000  # measured: "about 9 MB free"
DEFAULT_STAGE_FREE_KB = 976    # measured: "about 976 KB free"
BADGE_WRITE_KBPS = 58.0        # measured: 64 KB written in about 1.1 s

# real functions, captured before anything is wrapped
_open = builtins.open
_mkdir = os.mkdir
_rename = os.rename
_replace = os.replace
_remove = os.remove
_unlink = os.unlink
_rmdir = os.rmdir
_listdir = os.listdir
_stat_fn = os.stat
_statvfs = getattr(os, "statvfs", None)
_scandir = os.scandir
_normpath = os.path.normpath
_join = os.path.join
_getcwd = os.getcwd


class _State:
    root = None            # realpath of the sandbox
    system = None          # root + "/system"
    app_dir = None         # repo app folder (modules the badge would load from /system)
    badge = None
    mount = "ro"           # "ro" | "rw" | "none"
    msc_busy = False
    system_free = DEFAULT_SYSTEM_FREE_KB * 1024
    stage_free = DEFAULT_STAGE_FREE_KB * 1024
    used0 = None           # {"system": bytes, "stage": bytes} at install
    log = None             # [(ticks, wall, event)]
    rw_since = None        # (ticks, wall) when /system became writable
    rw_periods = None      # [(ticks_ms, wall_ms, bytes_written)]
    rw_bytes = 0           # bytes written under /system during the current rw period
    rejected = None        # [(op, relpath, errno)]
    violations = None      # [str]
    open_writes = None     # {id(wrapper): relpath} files open for writing under /system
    statvfs_calls = 0
    enospc = 0
    frame_tick = None      # badge ticks of the frame being counted
    frame_bytes = None     # {"system": n, "stage": n} written during that frame
    worst = None           # {"system": (bytes, ticks), "stage": (bytes, ticks)}


FS = _State()


def _ticks():
    return FS.badge.ticks if FS.badge is not None else 0


def _abs(path):
    if isinstance(path, bytes):
        path = path.decode()
    if not isinstance(path, str):
        return None   # file descriptor etc.
    if not path.startswith("/"):
        path = _join(_getcwd(), path)
    return _normpath(path)


def where(path):
    """'system', 'stage' (the LittleFS root) or None (outside the sandbox)."""
    if FS.root is None:
        return None
    ap = _abs(path)
    if ap is None:
        return None
    if ap == FS.system or ap.startswith(FS.system + "/"):
        return "system"
    if ap == FS.root or ap.startswith(FS.root + "/"):
        return "stage"
    return None


def _rel(path):
    ap = _abs(path) or str(path)
    if FS.root and ap.startswith(FS.root):
        return ap[len(FS.root):] or "/"
    return ap


def _deny(op, path, code):
    FS.rejected.append((op, _rel(path), code))
    raise OSError(code, "%s: %s (sim: /system is %s)" % (
        os.strerror(code), _rel(path), {"ro": "mounted read-only", "none": "not mounted",
                                        "rw": "mounted read-write"}[FS.mount]))


def _check(op, path, write):
    w = where(path)
    if w != "system":
        return w
    if FS.mount == "none":
        _deny(op, path, errno.ENOENT)
    if write and FS.mount != "rw":
        _deny(op, path, errno.EROFS)
    return w


# --------------------------------------------------------------------------
# free space
# --------------------------------------------------------------------------

def _used(top, skip=None):
    """Bytes used under `top`, every file rounded up to whole blocks, plus one
    block per directory (close enough for FAT and LittleFS)."""
    total = 0
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            it = _scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                if skip and e.path == skip:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        total += BLOCK
                        stack.append(e.path)
                    else:
                        n = e.stat(follow_symlinks=False).st_size
                        total += (n + BLOCK - 1) // BLOCK * BLOCK
                except OSError:
                    pass
    return total


def _used_now(mount):
    if mount == "system":
        return _used(FS.system)
    return _used(FS.root, skip=FS.system)


def free_bytes(mount):
    base = FS.system_free if mount == "system" else FS.stage_free
    return max(0, base - (_used_now(mount) - FS.used0[mount]))


def _fake_statvfs(mount):
    blocks = SYSTEM_BLOCKS if mount == "system" else STAGE_BLOCKS
    free = free_bytes(mount) // BLOCK
    free = min(free, blocks)
    return os.statvfs_result((BLOCK, BLOCK, blocks, free, free, 0, 0, 0,
                              1 if (mount == "system" and FS.mount == "ro") else 0, 255))


# --------------------------------------------------------------------------
# wrapped file object (space accounting + open-for-write tracking)
# --------------------------------------------------------------------------

class _WFile:
    """Proxy for a file opened for writing inside the sandbox."""

    def __init__(self, fh, path, mount):
        object.__setattr__(self, "_fh", fh)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_mount", mount)
        if mount == "system":
            FS.open_writes[id(self)] = _rel(path)

    def write(self, data):
        n = len(data)
        if self._mount == "system":
            if FS.mount != "rw":   # remounted under an open file: data would be lost
                _deny("write", self._path, errno.EROFS if FS.mount == "ro" else errno.EIO)
        if n:
            fh = self._fh
            fh.flush()
            if free_bytes(self._mount) < n:
                FS.enospc += 1
                raise OSError(errno.ENOSPC, "No space left on device: %s (sim: %s free)"
                              % (_rel(self._path), free_bytes(self._mount)))
            r = fh.write(data)
            fh.flush()
            if self._mount == "system":
                FS.rw_bytes += n
            _count_frame(self._mount, n)
            return r
        return self._fh.write(data)

    def close(self):
        FS.open_writes.pop(id(self), None)
        return self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __iter__(self):
        return iter(self._fh)

    def __getattr__(self, name):
        return getattr(self._fh, name)

    def __setattr__(self, name, value):
        setattr(self._fh, name, value)

    def __del__(self):
        FS.open_writes.pop(id(self), None)


def _count_frame(mount, n):
    t = _ticks()
    if FS.frame_tick != t:
        FS.frame_tick = t
        FS.frame_bytes = {"system": 0, "stage": 0}
    FS.frame_bytes[mount] += n
    if FS.frame_bytes[mount] > FS.worst[mount][0]:
        FS.worst[mount] = (FS.frame_bytes[mount], t)


def _writes(mode):
    return any(c in mode for c in "wax+")


def _sb_open(file, mode="r", *a, **k):
    w = where(file)
    if w is None:
        return _open(file, mode, *a, **k)
    wr = _writes(str(mode))
    _check("open(%r)" % mode, file, wr)
    fh = _open(file, mode, *a, **k)
    if wr:
        return _WFile(fh, file, w)
    return fh


def _sb_mkdir(path, *a, **k):
    _check("mkdir", path, True)
    return _mkdir(path, *a, **k)


def _sb_rename(src, dst, *a, **k):
    _check("rename", src, True)
    _check("rename", dst, True)
    return _rename(src, dst, *a, **k)


def _sb_replace(src, dst, *a, **k):
    _check("replace", src, True)
    _check("replace", dst, True)
    return _replace(src, dst, *a, **k)


def _sb_remove(path, *a, **k):
    _check("remove", path, True)
    return _remove(path, *a, **k)


def _sb_unlink(path, *a, **k):
    _check("unlink", path, True)
    return _unlink(path, *a, **k)


def _sb_rmdir(path, *a, **k):
    _check("rmdir", path, True)
    return _rmdir(path, *a, **k)


def _sb_listdir(path="."):
    _check("listdir", path, False)
    return _listdir(path)


def _sb_stat(path, *a, **k):
    _check("stat", path, False)
    return _stat_fn(path, *a, **k)


def _sb_statvfs(path):
    w = where(path)
    if w is None:
        return _statvfs(path)
    _check("statvfs", path, False)
    FS.statvfs_calls += 1
    return _fake_statvfs(w)


def _ilistdir(path="."):
    """MicroPython os.ilistdir: (name, type, inode, size) with type 0x4000 dir, 0x8000 file."""
    _check("ilistdir", path, False)
    out = []
    with _scandir(path) as it:
        for e in it:
            st = e.stat(follow_symlinks=False)
            if _stat.S_ISDIR(st.st_mode):
                out.append((e.name, 0x4000, 0, 0))
            else:
                out.append((e.name, 0x8000, 0, st.st_size))
    return iter(out)


# --------------------------------------------------------------------------
# vfs / rp2
# --------------------------------------------------------------------------

class Flash:
    """rp2.Flash(): the block device behind /system."""

    def __repr__(self):
        return "<rp2.Flash>"

    def ioctl(self, op, arg):
        return {4: SYSTEM_BLOCKS, 5: BLOCK}.get(op, 0)


class VfsFat:
    def __init__(self, dev):
        if not isinstance(dev, Flash):
            raise OSError(errno.ENODEV, "sim: VfsFat wants rp2.Flash(), got %r" % (dev,))
        self.dev = dev

    def __repr__(self):
        return "<VfsFat %r>" % (self.dev,)


class _Rom:
    def __repr__(self):
        return "<VfsRom>"


class _Lfs:
    def __repr__(self):
        return "<VfsLfs2>"


_SYSTEM_FS = [VfsFat(Flash())]


def _event(name):
    FS.log.append((_ticks(), time.time(), name))


def _end_rw():
    if FS.rw_since is not None:
        t0, w0 = FS.rw_since
        FS.rw_periods.append((_ticks() - t0, (time.time() - w0) * 1000.0, FS.rw_bytes))
        FS.rw_since = None
        FS.rw_bytes = 0


def vfs_umount(mount_point):
    mp = str(mount_point).rstrip("/") or "/"
    if mp != "/system":
        raise OSError(errno.EPERM, "sim: only /system may be unmounted (got %r)" % mount_point)
    if FS.mount == "none":
        raise OSError(errno.EINVAL, "sim: /system is not mounted")
    if FS.open_writes:
        FS.violations.append("umount /system at ticks %d with files still open for writing: %s"
                             % (_ticks(), ", ".join(sorted(FS.open_writes.values()))))
    _end_rw()
    FS.mount = "none"
    _event("umount")


def vfs_mount(fs=None, mount_point=None, readonly=False, mkfs=False):
    if fs is None and mount_point is None:
        out = [(_Rom(), "/rom")]
        if FS.mount != "none":
            out.append((_SYSTEM_FS[0], "/system"))
        out.append((_Lfs(), "/"))
        return out
    mp = str(mount_point).rstrip("/") or "/"
    if mp != "/system":
        raise OSError(errno.EPERM, "sim: only /system may be mounted (got %r)" % mount_point)
    if not isinstance(fs, VfsFat):
        raise OSError(errno.EINVAL, "sim: mount wants vfs.VfsFat(rp2.Flash()), got %r" % (fs,))
    if FS.mount != "none":
        raise OSError(errno.EPERM, "sim: /system is already mounted (%s); umount first" % FS.mount)
    if mkfs:
        FS.violations.append("vfs.mount(..., mkfs=True) would format /system!")
    _SYSTEM_FS[0] = fs
    if readonly:
        FS.mount = "ro"
        _event("mount ro")
    else:
        if FS.msc_busy:
            FS.violations.append("mounted /system read-write at ticks %d while USB disk mode "
                                 "was busy (rp2.is_msc_busy() True)" % _ticks())
        FS.mount = "rw"
        FS.rw_since = (_ticks(), time.time())
        FS.rw_bytes = 0
        _event("mount rw")


def is_msc_busy():
    return bool(FS.msc_busy)


# --------------------------------------------------------------------------
# MicroPython hashlib subset
# --------------------------------------------------------------------------

class _Hash:
    def __init__(self, ctor, data=None):
        self._h = ctor()
        if data is not None:
            self._h.update(bytes(data))

    def update(self, data):
        self._h.update(bytes(data))

    def digest(self):
        return self._h.digest()


def _mp_hashlib():
    m = types.ModuleType("hashlib")
    m.sha256 = lambda data=None: _Hash(_real_hashlib.sha256, data)
    m.sha1 = lambda data=None: _Hash(_real_hashlib.sha1, data)
    m.__doc__ = "sim: MicroPython hashlib (sha256/sha1; update() and digest() only)"
    return m


# --------------------------------------------------------------------------
# import guard: app modules can't be loaded while /system is unmounted
# --------------------------------------------------------------------------

class _ImportGuard:
    def find_spec(self, name, path=None, target=None):
        if FS.mount != "none" or FS.app_dir is None or "." in name:
            return None
        if (os.path.exists(_join(FS.app_dir, name + ".py"))
                or os.path.exists(_join(FS.app_dir, name + ".mpy"))):
            FS.violations.append("import %s while /system was unmounted (ticks %d)"
                                 % (name, _ticks()))
            raise ImportError("no module named '%s' (sim: /system is unmounted)" % name)
        return None


# --------------------------------------------------------------------------

def install(root, badge, app_dir=None, system_free_kb=None, stage_free_kb=None, msc_busy=False):
    root = os.path.realpath(os.path.abspath(root))
    FS.root = root
    FS.system = _join(root, "system")
    FS.app_dir = os.path.abspath(app_dir) if app_dir else None
    FS.badge = badge
    FS.mount = "ro"
    FS.msc_busy = bool(msc_busy)
    if system_free_kb is not None:
        FS.system_free = int(system_free_kb * 1024)
    if stage_free_kb is not None:
        FS.stage_free = int(stage_free_kb * 1024)
    FS.log = [(_ticks(), time.time(), "boot: /system mounted ro")]
    FS.rw_periods = []
    FS.rejected = []
    FS.violations = []
    FS.open_writes = {}
    FS.worst = {"system": (0, 0), "stage": (0, 0)}
    FS.used0 = {"system": _used_now("system"), "stage": _used_now("stage")}
    builtins.AM_ROOT = root

    builtins.open = _sb_open
    os.mkdir = _sb_mkdir
    os.rename = _sb_rename
    os.replace = _sb_replace
    os.remove = _sb_remove
    os.unlink = _sb_unlink
    os.rmdir = _sb_rmdir
    os.listdir = _sb_listdir
    os.stat = _sb_stat
    if _statvfs is not None:
        os.statvfs = _sb_statvfs
    os.ilistdir = _ilistdir
    if not hasattr(os, "sync"):
        os.sync = lambda: None

    vm = types.ModuleType("vfs")
    vm.VfsFat = VfsFat
    vm.mount = vfs_mount
    vm.umount = vfs_umount
    sys.modules["vfs"] = vm
    os.mount = vfs_mount     # MicroPython keeps these aliases
    os.umount = vfs_umount

    rm = types.ModuleType("rp2")
    rm.Flash = Flash
    rm.is_msc_busy = is_msc_busy
    sys.modules["rp2"] = rm

    sys.modules["hashlib"] = _mp_hashlib()
    sys.meta_path.insert(0, _ImportGuard())
    return FS


def set_msc_busy(flag):
    FS.msc_busy = bool(flag)


def report():
    """End-of-run summary lines (prefixed 'fs:' so share.py can parse them)."""
    if FS.root is None:
        return []
    lines = ["fs: root %s" % FS.root]
    t0 = FS.log[0][1] if FS.log else time.time()
    for ticks, wall, ev in FS.log:
        lines.append("fs:   ticks %8d  +%7.3f s  %s" % (ticks, wall - t0, ev))
    periods = list(FS.rw_periods)
    if FS.rw_since is not None:
        t, w = FS.rw_since
        periods.append((_ticks() - t, (time.time() - w) * 1000.0, FS.rw_bytes))
        FS.violations.append("/system still mounted read-write at the end of the run")
    if periods:
        mt = max(p[0] for p in periods)
        mw = max(p[1] for p in periods)
        mb = max(p[2] for p in periods)
        lines.append("fs: /system writable %d time(s); longest %d ms badge time, %.0f ms wall; "
                     "most bytes in one period %d (about %.1f s of flash writes on a badge)"
                     % (len(periods), mt, mw, mb, mb / 1024.0 / BADGE_WRITE_KBPS))
    else:
        lines.append("fs: /system never writable")
    for mount, label in (("system", "/system"), ("stage", "/ (staging)")):
        b, t = FS.worst[mount]
        if b:
            est = (" = about %.0f ms of flash writes on a badge" % (b / 1024.0 / BADGE_WRITE_KBPS
                                                                     * 1000)
                   if mount == "system" else "")
            lines.append("fs: most written to %s in one frame: %d bytes (ticks %d)%s"
                         % (label, b, t, est))
    lines.append("fs: final /system %s" % FS.mount)
    lines.append("fs: free now: /system %d KB, / %d KB (statvfs called %d times)"
                 % (free_bytes("system") // 1024, free_bytes("stage") // 1024, FS.statvfs_calls))
    if FS.rejected:
        lines.append("fs: %d operation(s) rejected, first: %s" % (
            len(FS.rejected), "; ".join("%s %s -> %s" % (op, p, errno.errorcode.get(c, c))
                                         for op, p, c in FS.rejected[:4])))
    if FS.enospc:
        lines.append("fs: %d write(s) failed with ENOSPC" % FS.enospc)
    for v in FS.violations:
        lines.append("fs: VIOLATION %s" % v)
    lines.append("fs: summary final=%s rw_count=%d max_rw_ticks=%d max_rw_wall_ms=%.0f "
                 "rejected=%d violations=%d" % (
                     FS.mount, len(periods), max([p[0] for p in periods] or [0]),
                     max([p[1] for p in periods] or [0]), len(FS.rejected), len(FS.violations)))
    return lines
