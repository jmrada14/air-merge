"""Seed a per-badge sandbox filesystem for the simulator.

    python3 -I sim/seed.py sim/out/fs/a --apps hello_badge,pixel_garden,mona_kart
    python3 -I sim/seed.py sim/out/fs/b --apps hello_badge_old      # installed as hello_badge
    python3 -I sim/seed.py DIR --apps src:dest                      # explicit target slug

Layout created under DIR (a fresh copy every time unless --keep):

    DIR/system/apps/<slug>/   fixture apps from fixtures/apps/ (`<name>_old` installs as
                              `<name>`), plus `menu/` (the launcher, never offered) and
                              `air_merge/` (a copy of app/, like the real badge; --no-self
                              leaves it out)
    DIR/secrets.py            stub with a fake wifi password
    DIR/state/                one State json per badge (contacts.json)
    DIR/air_merge/            empty staging parent (the app stages into air_merge/incoming)

`snapshot(DIR)` returns {relpath: (size, sha256)} for every file, used by share.py
to prove what changed and what didn't.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIXTURES = os.path.join(REPO, "fixtures", "apps")
APP = os.path.join(REPO, "app")
SKIP_COPY = shutil.ignore_patterns("__pycache__", ".ruff_cache")

MENU_INIT = '# Stub of the MonaOS launcher (sim fixture). Air Merge must never offer "menu".\n'


def _copy_app(src, dst, keep_junk):
    if keep_junk:
        shutil.copytree(src, dst)   # fixtures may carry junk on purpose (pixel_garden)
    else:
        shutil.copytree(src, dst, ignore=SKIP_COPY)


def seed(root, apps, keep=False, self_copy=True, name="Sim Owner"):
    root = os.path.abspath(root)
    if not keep and os.path.isdir(root):
        shutil.rmtree(root)
    apps_dir = os.path.join(root, "system", "apps")
    os.makedirs(apps_dir, exist_ok=True)
    os.makedirs(os.path.join(root, "state"), exist_ok=True)
    os.makedirs(os.path.join(root, "air_merge"), exist_ok=True)

    menu = os.path.join(apps_dir, "menu")
    os.makedirs(menu, exist_ok=True)
    with open(os.path.join(menu, "__init__.py"), "w") as fh:
        fh.write(MENU_INIT)

    installed = []
    for spec in apps:
        spec = spec.strip()
        if not spec:
            continue
        if ":" in spec:
            src, dst = spec.split(":", 1)
        else:
            src = dst = spec
            if dst.endswith("_old"):
                dst = dst[:-4]
        sp = os.path.join(FIXTURES, src)
        if not os.path.isdir(sp):
            raise SystemExit("seed: no fixture app %r in %s (have: %s)"
                             % (src, FIXTURES, ", ".join(sorted(os.listdir(FIXTURES)))))
        dp = os.path.join(apps_dir, dst)
        if os.path.exists(dp):
            shutil.rmtree(dp)
        _copy_app(sp, dp, keep_junk=True)
        if src == "pixel_garden":
            _ensure_junk(dp)
        installed.append(dst)

    if self_copy and os.path.exists(os.path.join(APP, "__init__.py")):
        dp = os.path.join(apps_dir, "air_merge")
        if os.path.exists(dp):
            shutil.rmtree(dp)
        _copy_app(APP, dp, keep_junk=False)
        installed.append("air_merge")

    with open(os.path.join(root, "secrets.py"), "w") as fh:
        fh.write('# sim stub of /secrets.py\nWIFI_SSID = "universe"\nWIFI_PASSWORD = "hunter2"\n'
                 'GITHUB_USERNAME = "%s"\n' % name.lower().replace(" ", ""))
    with open(os.path.join(root, "state", "contacts.json"), "w") as fh:
        json.dump({"contacts": [{"name": "Octo Cat", "id": "0123456789abcdef"}]}, fh)
    return installed


def _ensure_junk(app_dir):
    """Hidden and cache files that Air Merge must skip (in case git dropped them)."""
    ds = os.path.join(app_dir, ".DS_Store")
    if not os.path.exists(ds):
        with open(ds, "wb") as fh:
            fh.write(b"\x00\x00\x00\x01Bud1" + bytes(range(256)) * 4)
    pc = os.path.join(app_dir, "__pycache__")
    os.makedirs(pc, exist_ok=True)
    x = os.path.join(pc, "x.pyc")
    if not os.path.exists(x):
        with open(x, "wb") as fh:
            fh.write(b"\xcb\x0d\x0d\x0a" + bytes(300))


def snapshot(root):
    """{relpath: (size, sha256hex)} for every file under root (dirs listed as (-1, ''))."""
    root = os.path.abspath(root)
    out = {}
    for d, dirs, files in os.walk(root):
        dirs.sort()
        rd = os.path.relpath(d, root).replace(os.sep, "/")
        if rd != ".":
            out[rd + "/"] = (-1, "")
        for f in sorted(files):
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                data = fh.read()
            rel = (f if rd == "." else rd + "/" + f)
            out[rel] = (len(data), hashlib.sha256(data).hexdigest())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Seed a simulator badge filesystem.")
    ap.add_argument("dir")
    ap.add_argument("--apps", default="hello_badge,pixel_garden,mona_kart",
                    help="comma list of fixture apps; name_old installs as name; src:dest")
    ap.add_argument("--keep", action="store_true", help="don't wipe DIR first")
    ap.add_argument("--no-self", action="store_true", help="don't copy app/ as air_merge")
    ap.add_argument("--name", default="Sim Owner", help="owner name for secrets.py")
    args = ap.parse_args(argv)
    got = seed(args.dir, args.apps.split(","), keep=args.keep, self_copy=not args.no_self,
               name=args.name)
    print("seeded %s: %s (+ menu)" % (args.dir, ", ".join(got) or "no apps"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
