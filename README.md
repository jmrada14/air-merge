# Air Merge

Share apps badge to badge. Air Merge sends an installed app from one GitHub
Universe 2026 badge (MonaOS 4.0) to another over the badge's Bluetooth link,
and nothing moves without both people saying yes.

## How it works

1. Both people open **Air Merge** and hold their badges back to back until
   they pair (the same pairing as Pong and Contacts).
2. The sender picks an app from their list and confirms
   *"Send Mona Kart to Bo? They will be asked to accept."*
3. The receiver sees who is sending what — name, size, number of files,
   whether it replaces an app they already have — plus a warning that apps
   can run any code, and presses **Enter** to accept or **Back** to decline
   (it declines by itself after 30 s).
4. The app streams across with progress on both badges, every file is checked
   with SHA-256, and only then is it installed. Exit Air Merge and it's in the
   menu.

Air Merge can send itself, so a friend without it can get it from you.

## Safety

- Only the files inside the chosen app's folder are sent — never
  `secrets.py`, saved data in `/state`, or other apps.
- Offers are checked before the receiver is asked: no paths that escape the
  app folder, no hidden files, no reserved names (`menu`), at most 200 files,
  and the app must fit in free space. USB disk mode must be off.
- Incoming files land in a staging area on the badge's internal LittleFS and
  are verified first. `/system` (normally read-only) is remounted writable
  only for the short install, in small steps, and always locked again. When
  replacing an app, the old copy is kept until the new one is in place.
- Apps can run any code. Only accept apps from people you trust.

## Install

1. Open **Mass Storage** on the badge so it mounts as `BADGER`.
2. Run `./deploy.sh` (or `./deploy.sh /Volumes/OTHER`).
3. Eject the disk and reboot the badge. Air Merge appears at the end of the menu.

## Layout

| Path | What |
|---|---|
| `app/__init__.py` | Screen flow and state machine |
| `app/am_fs.py` | App listing, manifests, safety checks, staging, verify, install |
| `app/am_proto.py` | Transfer protocol over `badgeware.link` |
| `app/am_ui.py` | Screens |
| `tests/` | Unit tests (`python3 -I -m unittest discover -s tests`) |
| `sim/` | Two-badge desktop simulator, see `sim/README.md` |
| `fixtures/` | Sample apps used by the simulator |
| `DESIGN.md` | Measured badge facts, module contract and protocol |

## Simulator

```sh
python3 -I sim/share.py          # badge A sends Mona Kart to badge B
python3 -I sim/share.py --all    # send, replace, decline, walk away, low space, USB busy...
python3 -I sim/mpy_check.py app  # MicroPython compatibility check
```
