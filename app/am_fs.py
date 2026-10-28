# Air Merge filesystem layer: list apps, build/check manifests, stage incoming files
# on the writable LittleFS root, and install a verified app into the read-only /system.
#
# Every absolute path goes through p(), which prefixes ROOT ("" on the badge, a
# sandbox folder in the simulator and the tests). Only install() ever writes under
# /system, and only between a rw remount and the read-only remount in its finally.

import builtins
import binascii
import hashlib
import os

ROOT = getattr(builtins, "AM_ROOT", "")   # "" on the badge; a sandbox dir in the simulator
APPS = "/system/apps"
STAGE = "/air_merge"                       # staging area on the writable LittleFS root
INCOMING = STAGE + "/incoming"

MAX_FILES = 200
MAX_DEPTH = 6
MAX_NAME = 64
MAX_FILE = 4 * 1024 * 1024
BLOCK = 4096
MARGIN = 48 * 1024        # keep this much free on / (State saves etc.) and /system
IO = 2048                 # bytes per read/write step
SKIP = ("__pycache__",)
HIDDEN_APPS = ("menu",)
BAD_CHARS = '\\:*?"<>|'


def p(path):
    return ROOT + path


# --- small os helpers (MicroPython has no os.path / makedirs / walk) ----------------------

def _stat(path):
    try:
        return os.stat(path)
    except OSError:
        return None


def _isdir(path):
    s = _stat(path)
    return s is not None and (s[0] & 0x4000) != 0


def _isfile(path):
    s = _stat(path)
    return s is not None and (s[0] & 0x4000) == 0


def _exists(path):
    return _stat(path) is not None


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        if not _isdir(path):
            raise


def _mkdirs(base, rel):
    """Create base/rel folder by folder; base must exist."""
    cur = base
    for part in rel.split("/"):
        if part:
            cur = cur + "/" + part
            _mkdir(cur)


def _rmtree(path):
    s = _stat(path)
    if s is None:
        return
    if s[0] & 0x4000:
        for name in os.listdir(path):
            _rmtree(path + "/" + name)
        os.rmdir(path)
    else:
        os.remove(path)


def _close(f):
    if f is not None:
        try:
            f.close()
        except Exception:
            pass


def _hex(digest):
    return binascii.hexlify(digest).decode()


def _skip(name):
    return not name or name[0] == "." or name in SKIP


def pretty(slug):
    """'mona_kart' -> 'Mona Kart' (MicroPython str has no title())."""
    words = []
    for w in str(slug).replace("-", "_").split("_"):
        if w:
            words.append(w[0].upper() + w[1:])
    return " ".join(words) or str(slug)


def _walk(base, rel, depth, out):
    """Append (relpath, size) for every file under base/rel, sorted, skipping hidden."""
    folder = base + "/" + rel if rel else base
    try:
        names = os.listdir(folder)
    except OSError:
        return
    names.sort()
    for name in names:
        if _skip(name):
            continue
        r = rel + "/" + name if rel else name
        s = _stat(base + "/" + r)
        if s is None:
            continue
        if s[0] & 0x4000:
            if depth < MAX_DEPTH + 1:
                _walk(base, r, depth + 1, out)
            else:
                out.append((r + "/", 0))   # too deep: check_manifest rejects it
        else:
            out.append((r, s[6]))


def files_of(slug):
    out = []
    _walk(p(APPS + "/" + slug), "", 1, out)
    return out


# --- queries ----------------------------------------------------------------------------

def is_app(slug):
    base = p(APPS + "/" + slug)
    return _isfile(base + "/__init__.py") or _isfile(base + "/__init__.mpy")


def exists(slug):
    return valid_slug(slug) and is_app(slug)


def list_apps():
    """[{"slug", "name", "size", "files"}] for every app folder except menu/hidden."""
    out = []
    try:
        names = os.listdir(p(APPS))
    except OSError:
        return out
    names.sort()
    for slug in names:
        if _skip(slug) or slug in HIDDEN_APPS or not _isdir(p(APPS + "/" + slug)):
            continue
        if not is_app(slug):
            continue
        files = files_of(slug)
        size = 0
        for _, n in files:
            size += n
        out.append({"slug": slug, "name": pretty(slug), "size": size, "files": len(files)})
    return out


def _free(path):
    try:
        s = os.statvfs(path)
    except (OSError, AttributeError):
        return 0
    frsize = s[1] or s[0]
    return frsize * s[4]


def free_stage():
    return _free(p("/"))


def free_system():
    return _free(p("/system"))


# --- manifests --------------------------------------------------------------------------

def manifest_steps(slug, res):
    """Generator: hashes the app's files 4 KB at a time (yields between pieces), then
    appends the manifest dict to `res` (or None if the folder vanished)."""
    base = p(APPS + "/" + slug)
    files = files_of(slug)
    if not files:
        res.append(None)
        return
    out = []
    total = 0
    for rel, size in files:
        h = hashlib.sha256()
        n = 0
        f = None
        if not rel.endswith("/"):
            try:
                f = open(base + "/" + rel, "rb")
                while True:
                    b = f.read(BLOCK)
                    if not b:
                        break
                    h.update(b)
                    n += len(b)
                    yield
            except OSError:
                _close(f)
                res.append(None)
                return
            _close(f)
        out.append([rel, n, _hex(h.digest())])
        total += n
    res.append({"v": 1, "slug": slug, "name": pretty(slug), "files": out, "total": total})


def manifest(slug):
    res = []
    for _ in manifest_steps(slug, res):
        pass
    return res[0] if res else None


def read_chunk(slug, relpath, offset, n):
    f = open(p(APPS + "/" + slug + "/" + relpath), "rb")
    try:
        f.seek(offset)
        return f.read(n)
    finally:
        f.close()


class Reader:
    """Sequential reader for the sender: keeps the current file open between chunks."""

    def __init__(self, slug):
        self.base = p(APPS + "/" + slug + "/")
        self._f = None
        self._i = -1
        self._pos = 0

    def read(self, index, relpath, offset, n):
        if index != self._i or self._f is None:
            self.close()
            self._f = open(self.base + relpath, "rb")
            self._i = index
            self._pos = 0
        if offset != self._pos:
            self._f.seek(offset)
            self._pos = offset
        b = self._f.read(n)
        self._pos += len(b)
        return b

    def close(self):
        _close(self._f)
        self._f = None
        self._i = -1


# --- validation -------------------------------------------------------------------------

def valid_slug(slug):
    if not isinstance(slug, str) or not 1 <= len(slug) <= 32:
        return False
    for c in slug:
        if not ("a" <= c <= "z" or "0" <= c <= "9" or c == "_"):
            return False
    return True


def check_path(rel):
    """None if `rel` is a safe relative file path inside an app folder, else a reason."""
    if not isinstance(rel, str) or not rel:
        return "bad file name"
    if rel[0] == "/" or "\\" in rel or ".." in rel:
        return "unsafe path " + rel[:24]
    parts = rel.split("/")
    if len(parts) > MAX_DEPTH:
        return "folders nested too deep"
    for part in parts:
        if not part:
            return "unsafe path " + rel[:24]
        if part[0] == ".":
            return "hidden file " + rel[:24]
        if len(part) > MAX_NAME:
            return "file name too long"
        if part[-1] == " ":
            return "bad file name"
        for c in part:
            o = ord(c)
            if o < 32 or o > 126 or c in BAD_CHARS:
                return "bad file name"
    return None


def stage_need(m):
    """Bytes of flash the files take, rounded up to whole blocks, plus folders."""
    need = 2 * BLOCK
    dirs = {}
    for rel, size, _ in m["files"]:
        need += ((size + BLOCK - 1) // BLOCK) * BLOCK
        i = rel.rfind("/")
        while i > 0:
            dirs[rel[:i]] = 1
            i = rel.rfind("/", 0, i)
    return need + BLOCK * len(dirs)


def _ishex(s):
    if not isinstance(s, str) or len(s) != 64:
        return False
    for c in s:
        if not ("0" <= c <= "9" or "a" <= c <= "f"):
            return False
    return True


def kb(n):
    return (n + 1023) // 1024


def check_manifest(m, free_bytes):
    """None if the offer is acceptable, else a short reason. A reason starting with
    'too big' means there is not enough room (decline code 1); anything else is invalid."""
    try:
        if not isinstance(m, dict) or m.get("v") != 1:
            return "unknown offer format"
        slug = m.get("slug")
        if not valid_slug(slug):
            return "bad app name"
        if slug in HIDDEN_APPS:
            return "can't replace the menu"
        files = m.get("files")
        if not isinstance(files, list) or not files:
            return "no files"
        if len(files) > MAX_FILES:
            return "too many files (%d)" % len(files)
        seen = {}
        dirs = {}
        total = 0
        for e in files:
            if not isinstance(e, list) or len(e) != 3:
                return "bad file list"
            rel, size, h = e
            why = check_path(rel)
            if why:
                return why
            if type(size) is not int or size < 0 or size > MAX_FILE:
                return "bad file size"
            if not _ishex(h):
                return "bad checksum"
            key = rel.lower()          # FAT is case-insensitive
            if key in seen:
                return "duplicate file " + rel[:24]
            seen[key] = 1
            i = key.rfind("/")
            while i > 0:
                dirs[key[:i]] = 1
                i = key.rfind("/", 0, i)
            total += size
        for d in dirs:
            if d in seen:
                return "file and folder clash"
        if "__init__.py" not in seen and "__init__.mpy" not in seen:
            return "not an app (no __init__)"
        t = m.get("total")
        if type(t) is not int or t != total:
            return "sizes don't add up"
        need = stage_need(m)
        if need > free_bytes - MARGIN:
            avail = free_bytes - MARGIN
            return "too big: needs %d KB, only %d KB free" % (kb(need), kb(avail if avail > 0 else 0))
    except Exception:
        return "invalid offer"
    return None


# --- staging ----------------------------------------------------------------------------

def clean_stage():
    """Forget any half-received app (e.g. after a crash or the badge being reset)."""
    try:
        _rmtree(p(INCOMING))
    except OSError:
        pass


class Staging:
    """Incoming files for one offer, under STAGE/incoming/<slug>/ on the LittleFS root."""

    def __init__(self, m):
        self.m = m
        self.files = m["files"]
        self.base = p(INCOMING + "/" + m["slug"])
        self._f = None
        self._fi = -1
        self._pos = 0
        self._buf = bytearray()
        clean_stage()
        _mkdir(p(STAGE))
        _mkdir(p(INCOMING))
        _mkdir(self.base)
        for rel, size, _ in self.files:
            i = rel.rfind("/")
            if i > 0:
                _mkdirs(self.base, rel[:i])
            if size == 0:
                open(self.base + "/" + rel, "wb").close()

    def path(self, rel):
        return self.base + "/" + rel

    def _flush(self):
        if self._buf and self._f is not None:
            self._f.write(self._buf)
            self._buf = bytearray()

    def _close(self):
        try:
            self._flush()
        finally:
            _close(self._f)
            self._f = None
            self._fi = -1
            self._buf = bytearray()

    def write(self, index, offset, data):
        """Write `data` at `offset` of file `index`. False if it would overflow the file."""
        if index < 0 or index >= len(self.files) or offset < 0:
            return False
        size = self.files[index][1]
        n = len(data)
        if offset + n > size:
            return False
        if index != self._fi or self._f is None:
            self._close()
            self._f = open(self.path(self.files[index][0]), "wb" if offset == 0 else "ab")
            self._fi = index
            self._pos = offset
        if offset != self._pos:
            self._flush()
            self._f.seek(offset)
            self._pos = offset
        self._buf += data
        self._pos += n
        if self._pos >= size:
            self._close()
        elif len(self._buf) >= IO:      # small flash writes keep each frame short
            self._flush()
        return True

    def verify_steps(self, res):
        """Generator: re-reads every staged file (yielding per 4 KB), appends True/False."""
        self._close()
        for rel, size, digest in self.files:
            path = self.path(rel)
            s = _stat(path)
            if s is None or s[6] != size:
                res.append(False)
                return
            h = hashlib.sha256()
            f = None
            try:
                f = open(path, "rb")
                while True:
                    b = f.read(BLOCK)
                    if not b:
                        break
                    h.update(b)
                    yield
            except OSError:
                _close(f)
                res.append(False)
                return
            _close(f)
            if _hex(h.digest()) != digest:
                res.append(False)
                return
        res.append(True)

    def verify(self):
        res = []
        for _ in self.verify_steps(res):
            pass
        return bool(res and res[0])

    def discard(self):
        try:
            self._close()
        except OSError:
            pass
        clean_stage()


# --- install ----------------------------------------------------------------------------

def _remount(rw):
    import rp2
    import vfs
    try:
        vfs.umount("/system")
    except OSError:
        pass
    if rw:
        vfs.mount(vfs.VfsFat(rp2.Flash()), "/system")
    else:
        vfs.mount(vfs.VfsFat(rp2.Flash()), "/system", readonly=True)


def msc_busy():
    """True while the badge is mounted as a USB disk (then /system can't be unlocked)."""
    try:
        import rp2
        return bool(rp2.is_msc_busy())
    except (ImportError, AttributeError):
        return False


def install_steps(m, res):
    """Generator that installs the verified staged app; appends None or an error string
    to `res`. /system is writable only inside the try; the finally always puts it back
    read-only (also when the generator is closed early)."""
    slug = m["slug"]
    if not valid_slug(slug) or slug in HIDDEN_APPS:
        res.append("bad app name")
        return
    if msc_busy():
        res.append("USB disk mode is on")
        return
    src = p(INCOMING + "/" + slug)
    apps = p(APPS)
    new = apps + "/.am_new_" + slug
    old = apps + "/.am_old_" + slug
    dst = apps + "/" + slug
    err = None
    step = "mount"
    remounted = False
    fi = fo = None
    try:
        remounted = True
        _remount(True)
        step = "tidy"
        _rmtree(new)
        if _exists(old):          # left over from an interrupted install
            if _exists(dst):
                _rmtree(old)
            else:
                os.rename(old, dst)
        os.mkdir(new)
        step = "copy"
        for rel, size, digest in m["files"]:
            i = rel.rfind("/")
            if i > 0:
                _mkdirs(new, rel[:i])
            # one IO-sized flash write per step (~35 ms at the measured 58 KB/s)
            fi = open(src + "/" + rel, "rb")
            fo = open(new + "/" + rel, "wb")
            while True:
                b = fi.read(IO)
                if not b:
                    break
                fo.write(b)
                yield
            fi.close()
            fi = None
            fo.close()
            fo = None
            # read back what landed on /system, hashing one piece per step
            h = hashlib.sha256()
            fi = open(new + "/" + rel, "rb")
            while True:
                b = fi.read(IO)
                if not b:
                    break
                h.update(b)
                yield
            fi.close()
            fi = None
            if _hex(h.digest()) != digest:
                raise OSError(5)
        step = "swap"
        moved = False
        if _exists(dst):
            os.rename(dst, old)
            moved = True
        try:
            os.rename(new, dst)
        except OSError:
            if moved:
                os.rename(old, dst)
            raise
        if moved:
            try:
                _rmtree(old)
            except OSError:
                pass
    except OSError as e:
        _close(fi)
        _close(fo)
        fi = fo = None
        code = e.args[0] if e.args else 0
        if code == 28:
            err = "no space left on /system"
        elif code == 30 or step == "mount":
            err = "couldn't unlock /system"
        else:
            err = "install failed (%s %s)" % (step, code)
        try:
            _rmtree(new)
        except OSError:
            pass
        try:
            if not _exists(dst) and _exists(old):
                os.rename(old, dst)
        except OSError:
            pass
    finally:
        _close(fi)             # never leave a file open across the remount
        _close(fo)
        if remounted:
            try:
                _remount(False)
            except OSError:
                if err is None:
                    err = "couldn't lock /system again"
    if err is None:
        clean_stage()
    res.append(err)


def install(m):
    res = []
    gen = install_steps(m, res)
    try:
        for _ in gen:
            pass
    finally:
        gen.close()
    return res[0] if res else "install failed"
