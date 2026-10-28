# Air Merge desktop simulator

Runs the Air Merge badge app (`app/`) under desktop CPython 3.13 with the standard
library only. Nothing in `sim/` or `fixtures/` is ever copied to the badge.

| File | What it is |
|---|---|
| `badge_sim.py` | fake MonaOS firmware: globals (`screen badge color font ...`), `badgeware.State`, `micropython`, a pure-Python rasteriser for the drawing subset |
| `fake_fs.py` | per-badge sandbox filesystem, fake `vfs` and `rp2`, read-only `/system` enforcement, fake `statvfs`, MicroPython `hashlib` and `os.ilistdir` |
| `fake_link.py` | fake `badgeware.link` (`Link`, `Proximity`, identicons) and `pairing` over localhost UDP, with the measured capacity and flow control |
| `run.py` | runs one badge: frames, input scripts or a pilot, screenshots, end-of-run report |
| `pilot.py` | presses buttons from the app's state (pick an app, confirm, accept or decline) |
| `seed.py` | builds a sandbox filesystem from `fixtures/apps/` |
| `share.py` | two-badge runner: A sends an app to B, then checks both filesystems |
| `mpy_check.py` | static check of `app/*.py` for things that break on MicroPython |
| `png.py` | PNG writer/reader and contact sheets |

Outputs go to `sim/out/` (git-ignored).

## Quick start

```sh
python3 -I sim/mpy_check.py                 # static MicroPython check of app/
python3 -I sim/share.py                     # A sends mona_kart to B, B accepts
python3 -I sim/share.py --all               # every scenario, summary at the end
python3 -I sim/run.py --frames 300 --shots 1,60,300 --sheet out/solo.png   # one badge alone
```

## Sandbox filesystem (`--fs-root`, `seed.py`)

Each simulated badge gets its own folder standing in for the badge's `/`.
`run.py --fs-root DIR` sets `builtins.AM_ROOT = DIR` before the app is imported, and
`am_fs.p(path)` prefixes every absolute path with it. Without `--fs-root`, run.py
uses `sim/out/fs/solo` and seeds it if it has no `system/apps` yet.

```sh
python3 -I sim/seed.py DIR --apps hello_badge,pixel_garden,mona_kart
python3 -I sim/seed.py DIR --apps hello_badge_old          # installs as hello_badge
python3 -I sim/seed.py DIR --apps src:dest --no-self --keep
```

Creates `DIR/system/apps/<slug>/` for each fixture, plus `menu/` (the launcher stub,
which Air Merge must never offer) and `air_merge/` (a copy of `app/`, like the real
badge; `--no-self` leaves it out), `DIR/secrets.py` (stub), `DIR/state/contacts.json`
and the empty staging parent `DIR/air_merge/`. `seed.snapshot(DIR)` hashes every file
for before/after comparisons.

The running app's own code is imported from the repo's `app/` folder, and
`file_exists("/system/apps/air_merge/...")` maps there too. Other absolute paths given
to `file_exists` map into the sandbox. `badgeware.State` saves to `DIR/state/`.

### Fixture apps (`fixtures/apps/`)

| Fixture | Contents | Used for |
|---|---|---|
| `hello_badge/` | `__init__.py` + `greeting.txt` (2 KB) | small app, `--replace` |
| `hello_badge_old/` | older `__init__.py` + `old_notes.txt` | seeded on B as `hello_badge`; `old_notes.txt` must vanish after a replace |
| `pixel_garden/` | `__init__.py`, `assets/` with nested `tiles/` and `sounds/` binaries (~28 KB), an empty file, plus `.DS_Store` and `__pycache__/x.pyc` | nested folders, a zero-byte file, junk that must **not** be transferred |
| `mona_kart/` | copy of the Mona Kart app (8 files, ~190 KB) | realistic big app (default for share.py) |

`fixtures/.gitignore` re-includes the junk files that the root `.gitignore` would
drop, and `seed.py` recreates them if they are missing.

## Read-only `/system`, `vfs` and `rp2` (`fake_fs.py`)

`/system` starts mounted **read-only**, like on the badge. The fake modules:

- `rp2.Flash()`, `rp2.is_msc_busy()` (False; `run.py --msc-busy` makes it True)
- `vfs.VfsFat(rp2.Flash())`, `vfs.mount(fs, "/system", readonly=False|True)`,
  `vfs.umount("/system")`, `vfs.mount()` lists `/rom`, `/system` (if mounted), `/`.
  Mounting over a mounted `/system` raises `EPERM`, unmounting twice `EINVAL`
  (`os.mount`/`os.umount` are aliases).

`builtins.open` and `os.mkdir/rename/replace/remove/unlink/rmdir/listdir/stat/
statvfs/ilistdir` are wrapped. The wrappers only act on paths inside the sandbox and
pass everything else straight through. Under `<fs-root>/system`:

- mounted read-only: opening for write/append/create, `mkdir`, `rename`, `remove`,
  `rmdir` raise `OSError(30)` (EROFS)
- unmounted: every access raises `OSError(2)` (ENOENT), and importing an app module
  that hasn't been imported yet fails (on the badge the app's own files live in /system)
- writing to a file that was opened before a remount raises EROFS

`os.statvfs` is faked for `<fs-root>/system` (4096 B blocks, 3328 blocks) and the rest
of the sandbox (`/`, 4096 B blocks, 256 blocks), with `f_bsize == f_frsize` like
MicroPython (macOS reports a 1 MB `f_bsize`). Free space starts at
`--system-free KB` (default 9000) and `--stage-free KB` (default 976) and shrinks as
files are written, rounded to whole blocks. A write that doesn't fit raises
`OSError(28)` (ENOSPC).

The app also gets MicroPython's `hashlib` (`sha256`/`sha1` with `update()` and
`digest()` only, no `hexdigest()`) and `os.ilistdir`.

The end-of-run report (`fs:` lines) lists every mount transition with badge ticks and
wall time, how often and for how long `/system` stayed writable (badge ms, wall ms,
bytes written and the estimated flash time on a real badge at the measured 58 KB/s),
the most bytes written to `/system` and to `/` within a single frame (with the
estimated flash time for `/system`), the final mount state, rejected operations and
violations: `/system` still writable at
the end, unmounted with files open for writing, mounted rw while USB disk mode was
busy, `mkfs=True`, imports while unmounted. A machine-readable line
`fs: summary final=ro rw_count=N max_rw_ticks=... rejected=N violations=N` is what
`share.py` checks.

## Fake link (`fake_link.py`)

Two run.py processes talk over UDP on 127.0.0.1 (`--link-port` is this badge,
`--peer-port` the other; the lower port is `link.central`). Both say hello every
150 ms of badge time; while the peer is heard, `Proximity` "syncs" for 1.5 s, then
both sides link. The behaviour follows the measured badge:

- `link.capacity()` is 19 until linked, and becomes `mtu - 4` (243; `--link-mtu`)
  300 ms after linking (MTU exchange). Longer sends return False until then.
- `send()` returns False when not linked, when the message is over capacity, or when
  16 messages are already queued (backpressure). The queue drains at `--link-kbps`
  KB/s of badge time (default 12), so a 190 KB app takes about 16 s.
- At most 256 unread messages are kept; more are dropped and counted.
- `--link-mute-frame F` (or `--link-mute-at TICKS`) makes this badge go silent: the
  other side drops the link 1.5 s later, like walking away.
- `link.name`, `link.partner.name`, `link.partner.id` (8 bytes, `--badge-id`),
  `link.leave()`, `link.close()`; `pairing.Pairing` draws the pairing animation.

The `link:` report lines show messages and bytes each way, the measured throughput,
and how many sends were refused and why.

## One badge: `run.py`

```sh
python3 -I sim/run.py [--frames N] [--dt 33] [--input "30:DOWN 40:SELECT"]
    [--shots 1,40,100-105] [--every N] [--out DIR] [--sheet out/sheet.png] [--scale 2]
    [--fs-root DIR] [--system-free KB] [--stage-free KB] [--msc-busy]
    [--link-port P --peer-port Q] [--badge-name Ada] [--badge-id HEX16]
    [--link-kbps 12] [--link-mtu 247] [--link-mute-frame F]
    [--realtime --pace 2 --epoch T] [--pilot sim/pilot.py --pilot-opt role=send,app=mona_kart]
```

Input tokens: `F:BTN` taps BTN on frame F, `A-B:BTN` holds it from A through B
(buttons UP DOWN LEFT RIGHT SELECT BACK HOME MENU). `--realtime` paces frames in
wall-clock time from `--epoch` so two processes stay in step. With a pilot, every
UI state change is logged as `frame N ticks T state NAME` and the pilot's button
presses as `frame N pilot: ...`.

## Pilot (`pilot.py`)

Drives the buttons from the app's state, so runs don't depend on frame numbers or
screen layout. It finds the UI object by duck typing (an attribute `state`/`mode`/...
`app.screen` (am_ui constants) plus `app.engine.state` (am_proto) mapped to the
DESIGN.md names `LOBBY PAIRED CONFIRM_SEND OFFERED OFFER_IN SENDING RECEIVING
VERIFYING INSTALLING DONE`, plus `WAITING` (sender, all data acked, waiting for the
result), `OFFER_REFUSED` (receiver auto-declined) and `FAILED` (result screen, not
ok)), the app list (`app.apps`, dicts with `slug`), the cursor (`app.sel`) and the
on-screen messages (`app.msg`, `app.result`, `app.bad`, logged as `pilot: text ...`). Options (`--pilot-opt k=v`):

- `role=send app=<slug>`: on PAIRED move the cursor to `<slug>` and press SELECT; on
  CONFIRM_SEND press SELECT.
- `role=recv answer=accept|decline`: on OFFER_IN press SELECT or BACK.
- `hold_ms=1200`: how long each screen stays up before a press.
- `linger=60`: frames to keep running after the flow is over (DONE, back on the list
  after an attempt, or the link went away after pairing).

If the app's attribute or constant names change, adjust `SCREEN_MAP` and the name
lists at the top of `pilot.py`.

## Two badges: `share.py`

```sh
python3 -I sim/share.py                     # A sends mona_kart, B accepts
python3 -I sim/share.py --app pixel_garden  # nested folders, junk must be skipped
python3 -I sim/share.py --replace           # hello_badge over B's older copy
python3 -I sim/share.py --decline           # B presses BACK on the offer
python3 -I sim/share.py --drop-at 450       # A walks away at frame 450 (mid-transfer)
python3 -I sim/share.py --drop-at 680 --drop-who b   # B walks away after all data arrived:
                                                     # B must finish the install on its own
python3 -I sim/share.py --small-stage       # B's staging space too small: auto-decline
python3 -I sim/share.py --small-system      # B's /system too full: auto-decline
python3 -I sim/share.py --msc-busy --app pixel_garden   # B in USB disk mode: nothing installed
python3 -I sim/share.py --app pixel_garden --mtu 23     # capacity stays 19 (never upgraded)
python3 -I sim/share.py --all               # send, pixel_garden, replace, decline, drop,
                                            # small_stage, small_system, msc_busy (~2 min)
```

Options: `--pace 2` (badge ms per wall ms), `--kbps 12`, `--mtu 247`, `--every 5`
(screenshot interval), `--frames N` (default from the app size), `--sheet PATH`.
With `--drop-at` and no frame given under `--all`, a frame in the middle of the
transfer is picked from the app size.

Sandboxes: `sim/out/share/a` (Ada: hello_badge, pixel_garden, mona_kart) and
`sim/out/share/b` (Bo: an older hello_badge), freshly seeded each run. Logs:
`sim/out/share/a.log`, `b.log`; frames: `sim/out/share/frames_a/`, `frames_b/`.
Contact sheet: `sim/out/share_sheet.png` (with `--all`:
`sim/out/share_<scenario>_sheet.png`), A on the left, B on the right, one row per
moment, taken from the same frame on both badges.

Checks (exit code 0 only if all pass):

- both badges exit cleanly, no tracebacks, no filesystem violations, `/system`
  read-only at the end on both
- A's filesystem untouched (State aside) and A never unlocked `/system`
- no staging leftovers on B (files under `/air_merge`, `.am_new_*`/`.am_old_*`)
- B's other apps, `secrets.py` and existing State files untouched
- send / replace: B's `/system/apps/<slug>` equals A's byte for byte (A's hidden and
  `__pycache__` entries excluded, none present on B), B unlocked `/system` to
  install, both reached DONE; with `--replace` the old `old_notes.txt` is gone
- decline: B was shown the offer, pressed BACK, no data was sent, B unchanged
- drop: the drop happened while B was receiving, B has no new app, B back in LOBBY
  (if B had already received everything and finished the install on its own, the
  send checks apply instead, minus "A reached DONE")
- small stage / small system: B never started receiving, never had to answer, and
  a reason (too big / no room) was shown
- USB disk mode: nothing installed, B never unlocked /system, a reason mentioning
  USB / disk mode was shown

To change the scripted behaviour, edit `PILOT_A`/`PILOT_B` (pilot options) or
`INPUT_A`/`INPUT_B` (fixed input scripts merged with the pilot) at the top of share.py.

## MicroPython check: `mpy_check.py`

```sh
python3 -I sim/mpy_check.py [app_dir]
```

Flags imports outside the allowlist (`math random array struct os sys gc json hashlib
binascii vfs rp2 micropython builtins badgeware pairing` and `am_*` modules), module
names that collide with other badge apps (`ui game app ...`), local modules not named
`am_*`, walrus, `match`, `__slots__`, dataclass-style decorators, keyword-only and
positional-only arguments, async, f-string `=`, `eval/exec`, and CPython APIs that
MicroPython lacks: `os.path`, `os.walk`, `os.makedirs` and friends, `hexdigest()`,
str methods like `ljust`/`zfill`/`title`, `json.dumps(indent=...)`, `print(flush=...)`.
