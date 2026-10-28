# Air Merge: design and module contract

Air Merge shares an installed app from one GitHub Universe 2026 badge (MonaOS 4.0,
MicroPython 1.29, RP2350, 320x240) to another over the badge's Bluetooth link. Both
people must consent: the sender confirms what is sent to whom, the receiver sees who is
sending what and accepts or declines.

`app/` is copied to the badge as `/system/apps/air_merge/` (menu tile "Air Merge").
`sim/` is a desktop simulator that is never copied to the badge.

## Facts measured on a real badge (2026-10-27)

- Mounts: `/rom` (VfsRom, fonts only), `/system` (VfsFat on `rp2.Flash()`, 3328 x 4096 B
  blocks, about 9 MB free, mounted **read-only** at boot), `/` (VfsLfs2, 1 MB, about
  976 KB free; holds `/secrets.py`, `/state/`).
- `/system` can be made writable by an app while USB disk mode is off
  (`rp2.is_msc_busy()` is False):
  ```python
  vfs.umount("/system"); vfs.mount(vfs.VfsFat(rp2.Flash()), "/system")          # rw
  ...
  vfs.umount("/system"); vfs.mount(vfs.VfsFat(rp2.Flash()), "/system", readonly=True)
  ```
  Tested: writing 64 KB took about 1.1 s; read-only restored afterwards.
- `hashlib.sha256`, `binascii.crc32`, `deflate`, `json` are available. About 8 MB heap.
- `badgeware.link`: `Link(name)`, `link.capacity()` is the largest message `send()`
  accepts (mtu - 4: 19 bytes at MTU 23 before linking, up to 243 at the preferred MTU
  247). `link.send(msg)` returns False when the message can't be queued right now
  (too big, not linked, or backpressure); the caller retries later. Receive buffer 512 B,
  inbox limit 256 messages, pending limit 512. `link.name` is the owner's configured
  name; `link.partner.name`, `link.partner.id` (8 bytes), `link.central`.
- Pairing exactly like `/system/apps/contacts` and `pong`: `badgelink.Proximity(link)`,
  hold badges back to back, `proximity.update/syncing/progress/cooling/rest/clear/lights`,
  `from pairing import Pairing`. The Contacts app transfers JSON in chunks of
  `link.capacity() - 3` bytes the same way.

## Platform rules

Firmware globals used without import: `screen badge color font image shape vec2 rect
mat3 run reset file_exists HIRES LORES VSYNC BUTTON_UP BUTTON_DOWN BUTTON_LEFT
BUTTON_RIGHT BUTTON_SELECT BUTTON_BACK BUTTON_HOME BUTTON_MENU`. Allowed imports:
`math random array struct os sys gc json hashlib binascii vfs rp2 micropython time`
`from badgeware import State`, `from badgeware import link as badgelink`, `pairing`.
No dataclasses/typing/enum/functools/itertools/walrus/match/`__slots__`. Module files
start with `am_`. Drawing subset: `screen.pen/clear/rectangle/triangle/circle/line/
text/measure_text/font/clip`, fonts `font.ignore` (title), `font.more` (big digits),
`font.bacteria` (body), `font.smart` (small). Input `badge.pressed/held`, time
`badge.ticks`, lights `badge.caselights(level)`.

## Modules

### `am_fs.py` (filesystem; App agent)
All absolute paths go through `p(path)`, which prefixes `ROOT`:
```python
import builtins
ROOT = getattr(builtins, "AM_ROOT", "")   # "" on the badge; a sandbox dir in the simulator
APPS = "/system/apps"
STAGE = "/air_merge"                       # staging area on the writable LittleFS root
def p(path): return ROOT + path
```
- `list_apps()` -> list of dicts `{"slug", "name", "size", "files"}` for every folder in
  `/system/apps` with `__init__.py` or `__init__.mpy`, except `menu` and hidden folders.
  `name` like the menu ("mona_kart" -> "Mona Kart").
- `manifest(slug)` -> `{"v": 1, "slug", "name", "files": [[relpath, size, sha256hex]],
  "total"}`. Walks the app folder recursively, skipping hidden entries (`.*`, `._*`),
  `__pycache__`, `.ruff_cache`. Paths use `/`.
- `read_chunk(slug, relpath, offset, n)` -> bytes.
- `check_manifest(m, free_bytes)` -> None or a short reason string. Rejects: bad slug
  (must match `[a-z0-9_]{1,32}`, not `menu`), any path that is absolute, contains `..`,
  `\\`, empty components, components starting with `.`, more than 6 levels, names
  longer than 64, more than 200 files, sizes that don't add up to `total`, total larger
  than `free_bytes` minus a margin.
- `free_stage()` / `free_system()` -> bytes free on `/` and `/system`.
- `class Staging(manifest)`: `write(index, offset, data)` (writes into
  `STAGE/incoming/<slug>/<relpath>`, creating folders), `verify()` -> bool (sizes and
  sha256 for every file), `discard()`.
- `install(manifest)` -> None or error string: requires `not rp2.is_msc_busy()`; mounts
  `/system` rw; copies staged files to `/system/apps/.am_new_<slug>/`; if
  `/system/apps/<slug>` exists renames it to `.am_old_<slug>`; renames the new folder
  to `<slug>`; deletes the old copy; always remounts read-only in `finally`; deletes the
  staging copy. On failure it puts the old copy back if possible.
- `exists(slug)`.
- Incremental forms used by the UI so no frame stalls: `manifest_steps(slug, res)`,
  `Staging.verify_steps(res)` and `install_steps(m, res)` are generators that yield
  between 2-4 KB pieces and append their result to `res` (closing `install_steps`
  early still remounts read-only). `Reader(slug)` keeps the current file open for the
  sender; `clean_stage()` drops a half-received app (run at app start).

### `am_proto.py` (transfer protocol; App agent)
Message = kind byte + body (little endian), never longer than `link.capacity()`.
`tid` is a 1-byte transfer id chosen by the sender for each offer; the receiver adopts
it, and every message for another `tid` is ignored (late packets from a cancelled
transfer can't leak into the next one).

| Kind | Body | Meaning |
|---|---|---|
| `H` | `<B version>` | hello after linking (version 1) |
| `O` | `<B tid><H index><H count>` + manifest JSON piece | offer (manifest split into pieces of `capacity - 6` bytes; at most 64 KB of JSON) |
| `A` | `<B tid>` | receiver accepts the offer |
| `D` | `<B tid><B reason>` + optional UTF-8 text | decline (0 declined, 1 too big, 2 invalid, 3 busy, 4 timed out); the text says why (e.g. `USB disk mode is on`) |
| `F` | `<B tid><H file><I offset>` + data | file data (`capacity - 8` bytes per chunk) |
| `K` | `<B tid><I bytes_received>` | receiver progress ack (every 8 chunks, at file ends, and every 400 ms while data is unacked) |
| `V` | `<B tid><B ok>` + UTF-8 reason | receiver verified and installed (1) or failed (0, reason shown to the sender) |
| `X` | `<B tid>` | cancel by either side |
| `S` | `<B tid><B phase>` | receiver status after the last byte: 1 checking files, 2 installing (the sender stops offering cancel; X is ignored once installing) |

Sender keeps at most 24 unacknowledged `F` chunks in flight, retries `send()` that
returned False on the next frame (control messages queue in order ahead of data), and
never blocks a frame for more than ~25 ms. `badge.ticks` is frozen for the whole frame
(measured), so the budget uses `time.ticks_ms()`/`time.ticks_diff()`, with hard caps as a
backstop: 32 messages, 6 hash steps, 4 install steps per frame; an install step is one
2 KB flash write or one 2 KB read-back piece (~35 ms at the measured 58 KB/s). The receiver accepts only the next expected `(file, offset)` and writes it
immediately; anything else (duplicates, gaps) is dropped and answered with a `K` of
where it really is. If no ack arrives for 2.5 s the sender goes back to the last acked
byte and resends (go-back-N); no progress for 20 s fails the transfer.

Receiver checks the complete offer with `check_manifest(m, min(free_stage(),
free_system()))` before asking; a failing offer is declined automatically (1 for "too
big", otherwise 2) and the person only sees why. An unanswered offer is declined after
30 s (4). An offer that arrives while busy, or while the receiver is in USB disk mode
(`rp2.is_msc_busy()`, checked when the offer arrives so no transfer is wasted), gets 3; if both badges offer at the same
moment the `link.central` badge's offer wins and the other side withdraws its own.
After the last byte: VERIFY (sha256 of every staged file) -> INSTALL (`install_steps`,
copies are hashed again after landing on /system) -> `V`. A link drop during VERIFY or
INSTALL lets the receiver finish locally; anywhere else both sides discard everything.

### `am_ui.py` + `__init__.py` (screens and state machine; App agent)
States: LOBBY (hold back to back; BACK exits) -> PAIRED (pick an app to send from the
list, or wait to receive) -> CONFIRM_SEND ("Send Snake to Bo? 12 KB, 3 files" ENTER/BACK)
-> OFFERED (waiting for Bo to accept) | OFFER_IN (receiver: who, what, size, files,
"replaces your Snake", trust warning, ENTER accept / BACK decline) -> SENDING /
RECEIVING (progress, speed, cancel with BACK) -> VERIFYING -> INSTALLING -> DONE
("Installed Snake. Exit Air Merge to see it in the menu."). Link drop anywhere ->
LOBBY with a message. On exit: close link, caselights 0. Exiting returns to the menu,
which rescans apps (the badge resets back to the launcher after every app).

### `sim/` (Sim agent)
Based on the Mona Kart simulator (copied in): `badge_sim.py`, `png.py`, `fake_link.py`,
`run.py`, `mpy_check.py`. Adds a per-badge sandbox filesystem (`--fs-root`, sets
`builtins.AM_ROOT`), fake `vfs`/`rp2` modules that enforce the read-only `/system`
mount (writes under `<root>/system` raise `OSError(30)` unless remounted rw), a fake
link that matches the measured capacity behaviour, and a two-badge runner that seeds
both sandboxes with fixture apps, sends one app across, and checks the receiver's
`/system/apps/<slug>` matches the sender's byte for byte.
