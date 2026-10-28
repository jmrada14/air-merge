import hashlib
import os
import random
import unittest

import fakes
from fakes import FS

import am_fs


def sha(b):
    return hashlib.sha256(b).hexdigest()


def good_manifest():
    files = {"__init__.py": b"print('hi')\n", "lib/util.py": b"x = 1\n", "data/a.bin": b"\0" * 10}
    rows = [[k, len(v), sha(v)] for k, v in sorted(files.items())]
    return {"v": 1, "slug": "snake", "name": "Snake", "files": rows,
            "total": sum(len(v) for v in files.values())}


class FsBase(unittest.TestCase):
    def setUp(self):
        fakes.reset()
        self.root = fakes.new_root()

    def tearDown(self):
        fakes.reset()

    def path(self, *parts):
        return os.path.join(self.root, *parts)


class TestListAndManifest(FsBase):
    def test_list_apps(self):
        fakes.write_app(self.root, "mona_kart", {"__init__.py": b"a" * 100, "kart_hud.py": b"b" * 50})
        fakes.write_app(self.root, "menu", {"__init__.py": b"m"})
        fakes.write_app(self.root, ".hidden", {"__init__.py": b"h"})
        fakes.write_app(self.root, ".am_new_x", {"__init__.py": b"h"})
        fakes.write_app(self.root, "not_an_app", {"readme.txt": b"r"})
        fakes.write_app(self.root, "compiled", {"__init__.mpy": b"c" * 7})
        fakes.write_app(self.root, "air_merge", {"__init__.py": b"am"})
        apps = am_fs.list_apps()
        slugs = [a["slug"] for a in apps]
        self.assertEqual(slugs, ["air_merge", "compiled", "mona_kart"])
        kart = apps[2]
        self.assertEqual(kart["name"], "Mona Kart")
        self.assertEqual(kart["size"], 150)
        self.assertEqual(kart["files"], 2)
        self.assertEqual(apps[0]["name"], "Air Merge")

    def test_manifest_skips_hidden_and_cache(self):
        files = {
            "__init__.py": b"main",
            "assets/tile.bin": bytes(range(256)) * 20,
            "assets/deep/x.txt": b"x",
            ".DS_Store": b"junk",
            "._kart.py": b"junk",
            "__pycache__/a.cpython-312.pyc": b"junk",
            ".ruff_cache/x": b"junk",
            "assets/.hidden": b"junk",
            "empty.txt": b"",
        }
        fakes.write_app(self.root, "garden", files)
        m = am_fs.manifest("garden")
        rels = [f[0] for f in m["files"]]
        self.assertEqual(rels, ["__init__.py", "assets/deep/x.txt", "assets/tile.bin", "empty.txt"])
        for rel, size, digest in m["files"]:
            self.assertEqual(size, len(files[rel]))
            self.assertEqual(digest, sha(files[rel]))
        self.assertEqual(m["total"], sum(f[1] for f in m["files"]))
        self.assertEqual(m["slug"], "garden")
        self.assertIsNone(am_fs.check_manifest(m, 1 << 30))

    def test_read_chunk_and_reader(self):
        data = bytes(range(256)) * 4
        fakes.write_app(self.root, "app", {"__init__.py": data})
        self.assertEqual(am_fs.read_chunk("app", "__init__.py", 10, 5), data[10:15])
        r = am_fs.Reader("app")
        self.assertEqual(r.read(0, "__init__.py", 0, 100), data[:100])
        self.assertEqual(r.read(0, "__init__.py", 100, 100), data[100:200])
        self.assertEqual(r.read(0, "__init__.py", 50, 10), data[50:60])   # seek back
        r.close()

    def test_exists(self):
        fakes.write_app(self.root, "snake", {"__init__.py": b"s"})
        self.assertTrue(am_fs.exists("snake"))
        self.assertFalse(am_fs.exists("nope"))
        self.assertFalse(am_fs.exists("../secrets"))


class TestCheckManifest(unittest.TestCase):
    def bad(self, m, free=1 << 30):
        why = am_fs.check_manifest(m, free)
        self.assertIsNotNone(why, "accepted %r" % (m,))
        return why

    def with_path(self, rel):
        m = good_manifest()
        m["files"].append([rel, 1, sha(b"x")])
        m["total"] += 1
        return m

    def test_good(self):
        self.assertIsNone(am_fs.check_manifest(good_manifest(), 1 << 30))

    def test_path_traversal(self):
        for rel in ("../secrets.py", "lib/../../secrets.py", "..", "a/..", "/secrets.py",
                    "/system/apps/menu/__init__.py", "a\\..\\b", "lib//x.py", "lib/", "",
                    ".hidden", "lib/.git/config", "._x.py", "a/b/c/d/e/f/g.py", "x" * 65,
                    "a:b", "tab\there", "café.py", "trailing ", "a/./b"):
            self.bad(self.with_path(rel))

    def test_depth_limit_ok_at_six(self):
        self.assertIsNone(am_fs.check_manifest(self.with_path("a/b/c/d/e/f.py"), 1 << 30))

    def test_non_string_path(self):
        m = good_manifest()
        m["files"].append([None, 1, sha(b"x")])
        self.bad(m)

    def test_too_many_files(self):
        m = good_manifest()
        for i in range(am_fs.MAX_FILES):
            m["files"].append(["f%d.py" % i, 0, sha(b"")])
        why = self.bad(m)
        self.assertIn("too many files", why)

    def test_sizes(self):
        m = good_manifest()
        m["total"] += 1
        self.assertIn("add up", self.bad(m))
        m = good_manifest()
        m["files"][0][1] = -1
        self.bad(m)
        m = good_manifest()
        m["files"][0][1] = True
        self.bad(m)
        m = good_manifest()
        m["files"][0][1] = 1.5
        self.bad(m)
        m = good_manifest()
        m["total"] = str(m["total"])
        self.bad(m)

    def test_checksum(self):
        m = good_manifest()
        m["files"][0][2] = "XYZ"
        self.bad(m)
        m = good_manifest()
        m["files"][0][2] = m["files"][0][2].upper()
        self.bad(m)

    def test_slugs(self):
        for slug in ("menu", "", "Snake", "../x", "a/b", ".hidden", "x" * 33, None, 5, "sn ake"):
            m = good_manifest()
            m["slug"] = slug
            self.bad(m)
        m = good_manifest()
        m["slug"] = "air_merge"   # sharing Air Merge itself is allowed
        self.assertIsNone(am_fs.check_manifest(m, 1 << 30))

    def test_structure(self):
        for m in (None, [], "x", {"v": 2}, {"v": 1, "slug": "a", "files": "x", "total": 0},
                  {"v": 1, "slug": "a", "files": [], "total": 0}):
            self.bad(m)
        m = good_manifest()
        m["files"][0] = ["__init__.py", 1]
        self.bad(m)

    def test_not_an_app(self):
        m = good_manifest()
        m["files"] = [f for f in m["files"] if f[0] != "__init__.py"]
        m["total"] = sum(f[1] for f in m["files"])
        self.assertIn("__init__", self.bad(m))

    def test_duplicates_and_clash(self):
        m = good_manifest()
        m["files"].append(["LIB/UTIL.py", 0, sha(b"")])
        self.assertIn("duplicate", self.bad(m))
        m = good_manifest()
        m["files"].append(["lib", 0, sha(b"")])
        self.assertIn("clash", self.bad(m))

    def test_too_big(self):
        m = good_manifest()
        m["files"].append(["big.bin", 500 * 1024, sha(b"")])
        m["total"] += 500 * 1024
        why = self.bad(m, free=400 * 1024)
        self.assertTrue(why.startswith("too big"), why)
        self.assertIsNone(am_fs.check_manifest(m, 2 * 1024 * 1024))


class TestStaging(FsBase):
    def make(self, files):
        rows = [[k, len(v), sha(v)] for k, v in sorted(files.items())]
        return {"v": 1, "slug": "snake", "name": "Snake", "files": rows,
                "total": sum(len(v) for v in files.values())}

    def fill(self, st, m, files, step=19):
        for i, (rel, size, _) in enumerate(m["files"]):
            data = files[rel]
            for off in range(0, size, step):
                self.assertTrue(st.write(i, off, data[off:off + step]))

    def test_write_verify(self):
        rng = random.Random(3)
        files = {"__init__.py": fakes.rand_bytes(rng, 9000), "a/b/c.bin": fakes.rand_bytes(rng, 5000),
                 "empty.txt": b""}
        m = self.make(files)
        st = am_fs.Staging(m)
        self.fill(st, m, files)
        self.assertTrue(st.verify())
        for rel, data in files.items():
            with fakes._real["open"](st.path(rel), "rb") as f:
                self.assertEqual(f.read(), data)
        self.assertTrue(st.path("x").startswith(self.path("air_merge", "incoming", "snake")))
        st.discard()
        self.assertFalse(os.path.exists(self.path("air_merge", "incoming")))

    def test_corrupt_byte_fails_verify(self):
        files = {"__init__.py": bytes(range(256)) * 10, "b.py": b"hello world" * 30}
        m = self.make(files)
        st = am_fs.Staging(m)
        self.fill(st, m, files)
        st._close()
        p = st.path("b.py")
        with fakes._real["open"](p, "r+b") as f:
            f.seek(17)
            f.write(b"X")
        self.assertFalse(st.verify())

    def test_short_file_fails_verify(self):
        files = {"__init__.py": b"a" * 100}
        m = self.make(files)
        st = am_fs.Staging(m)
        st.write(0, 0, b"a" * 50)
        self.assertFalse(st.verify())

    def test_write_past_size_refused(self):
        files = {"__init__.py": b"a" * 10}
        m = self.make(files)
        st = am_fs.Staging(m)
        self.assertFalse(st.write(0, 5, b"b" * 6))
        self.assertFalse(st.write(1, 0, b"b"))
        self.assertFalse(st.write(-1, 0, b"b"))
        st.discard()

    def test_stage_never_touches_system(self):
        files = {"__init__.py": b"a" * 10}
        st = am_fs.Staging(self.make(files))
        self.fill(st, self.make(files), files)
        self.assertEqual(FS.rw_count, 0)
        self.assertFalse(os.path.exists(self.path("system", "apps", "snake")))


class TestInstall(FsBase):
    def stage(self, files, slug="snake"):
        rows = [[k, len(v), sha(v)] for k, v in sorted(files.items())]
        m = {"v": 1, "slug": slug, "name": am_fs.pretty(slug), "files": rows,
             "total": sum(len(v) for v in files.values())}
        st = am_fs.Staging(m)
        for i, (rel, size, _) in enumerate(rows):
            if size:
                st.write(i, 0, files[rel])
        self.assertTrue(st.verify())
        return m

    def snapshot_outside(self):
        out = {}
        for top in ("secrets.py", "state"):
            p = self.path(top)
            if os.path.isdir(p):
                out.update({top + "/" + k: v for k, v in fakes.read_tree(p).items()})
            else:
                with fakes._real["open"](p, "rb") as f:
                    out[top] = f.read()
        return out

    def assert_clean(self, slug="snake"):
        self.assertEqual(FS.mount, "ro")
        self.assertEqual(FS.violations, [])
        self.assertEqual(FS.open_writes, 0)
        apps = os.listdir(self.path("system", "apps"))
        self.assertFalse([a for a in apps if a.startswith(".am_")], apps)

    def test_system_is_read_only(self):
        with self.assertRaises(OSError):
            open(am_fs.p("/system/apps/evil.py"), "wb")
        with self.assertRaises(OSError):
            os.mkdir(am_fs.p("/system/apps/evil"))

    def test_install_new(self):
        before = self.snapshot_outside()
        files = {"__init__.py": b"snake!" * 100, "lib/x.py": b"x" * 5000, "e.txt": b""}
        m = self.stage(files)
        self.assertIsNone(am_fs.install(m))
        self.assertEqual(fakes.read_tree(self.path("system", "apps", "snake")), files)
        self.assertEqual(FS.rw_count, 1)
        self.assert_clean()
        self.assertFalse(os.path.exists(self.path("air_merge", "incoming")))
        self.assertEqual(self.snapshot_outside(), before)

    def test_replace_existing(self):
        fakes.write_app(self.root, "snake", {"__init__.py": b"old", "old_only.txt": b"bye"})
        files = {"__init__.py": b"new version"}
        m = self.stage(files)
        self.assertIsNone(am_fs.install(m))
        self.assertEqual(fakes.read_tree(self.path("system", "apps", "snake")), files)
        self.assert_clean()

    def test_failed_swap_keeps_old(self):
        old = {"__init__.py": b"old", "keep.txt": b"keep me"}
        fakes.write_app(self.root, "snake", old)
        m = self.stage({"__init__.py": b"new"})
        orig = os.rename
        calls = []

        def rename(a, b):
            calls.append((a, b))
            if b.endswith("/apps/snake") and ".am_new_" in a:
                raise OSError(5, "boom")
            return fakes._rename(a, b)
        os.rename = rename
        try:
            err = am_fs.install(m)
        finally:
            os.rename = orig
        self.assertIsNotNone(err)
        self.assertEqual(fakes.read_tree(self.path("system", "apps", "snake")), old)
        self.assert_clean()

    def test_disk_full_keeps_old(self):
        old = {"__init__.py": b"old"}
        fakes.write_app(self.root, "snake", old)
        m = self.stage({"__init__.py": b"n" * 10000, "b.py": b"b" * 10000})
        FS.fail_write_after = 12000
        err = am_fs.install(m)
        self.assertIn("space", err)
        self.assertEqual(fakes.read_tree(self.path("system", "apps", "snake")), old)
        self.assert_clean()

    def test_usb_busy(self):
        m = self.stage({"__init__.py": b"n"})
        FS.msc_busy = True
        err = am_fs.install(m)
        self.assertIn("USB", err)
        self.assertEqual(FS.rw_count, 0)
        self.assertFalse(os.path.exists(self.path("system", "apps", "snake")))

    def test_abandoned_install_relocks(self):
        m = self.stage({"__init__.py": b"n" * 20000})
        res = []
        gen = am_fs.install_steps(m, res)
        next(gen)
        next(gen)
        self.assertEqual(FS.mount, "rw")
        gen.close()
        self.assertEqual(FS.mount, "ro")
        self.assertEqual(FS.violations, [])
        self.assertEqual(FS.open_writes, 0)

    def test_install_steps_are_small(self):
        """Each install step writes at most one IO piece (frame budget on real flash)."""
        m = self.stage({"__init__.py": b"n" * 55590, "b.py": b"b" * 5000})
        res = []
        worst = 0
        for _ in am_fs.install_steps(m, res):
            worst = max(worst, FS.written)
            FS.written = 0
        worst = max(worst, FS.written)
        self.assertEqual(res, [None])
        self.assertLessEqual(worst, am_fs.IO)

    def test_leftover_old_copy_recovered(self):
        # an earlier install died after moving the old app aside
        fakes.write_app(self.root, ".am_old_snake", {"__init__.py": b"old"})
        m = self.stage({"__init__.py": b"new"})
        self.assertIsNone(am_fs.install(m))
        self.assertEqual(fakes.read_tree(self.path("system", "apps", "snake")), {"__init__.py": b"new"})
        self.assert_clean()

    def test_menu_never_installed(self):
        m = {"v": 1, "slug": "menu", "name": "Menu", "files": [], "total": 0}
        self.assertIsNotNone(am_fs.install(m))
        self.assertEqual(FS.rw_count, 0)


if __name__ == "__main__":
    unittest.main()
