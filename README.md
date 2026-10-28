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

Both badges need Air Merge installed and open: the receiving badge runs Air
Merge's own code to pair, ask for consent, verify and install. The first
install on each badge therefore goes through a computer (see Install below).
After that, sending Air Merge itself is how you pass a newer version along.

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

Every badge that wants to send or receive needs Air Merge once.

1. Plug the badge into a computer and open **Mass Storage** on it so it mounts
   as `BADGER`.
2. Run the installer, no clone needed:
   ```sh
   curl -fsSL https://raw.githubusercontent.com/jmrada14/air-merge/main/install.sh | sh
   ```
   or, from a clone of this repo, `./install.sh`.
3. Reboot the badge. Air Merge appears at the end of the menu.

The installer finds the badge on macOS and Linux, copies the app into
`apps/air_merge/`, checks the copy and ejects the disk. It also gives Air
Merge its own menu icon, a beacon whose rings ripple out when the tile is
selected. The icon needs Python 3; the first run downloads `mpy-cross` 1.26.1
into `~/.cache/air-merge` to rebuild the menu's `icons.mpy`, and keeps the
menu's original icon files in `~/.cache/air-merge/menu-backup`. Other apps'
icons are left as they are. Without Python, Air Merge still installs and shows
the default icon. A badge that gets Air Merge over the air, from a friend,
also shows the default icon until it runs the installer.

Options: `--volume PATH` if the badge mounts somewhere else, `--ref NAME` to
install a branch or tag, `--no-icon` to leave the menu alone, `--no-eject` to
leave the disk mounted.

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
| `install.sh` | Installs Air Merge onto a badge in USB disk mode |
| `tools/menu_icon.py` | Builds the menu icon and its animation (`--preview` draws a PNG of the frames) |
| `DESIGN.md` | Measured badge facts, module contract and protocol |

## Simulator

```sh
python3 -I sim/share.py          # badge A sends Mona Kart to badge B
python3 -I sim/share.py --all    # send, replace, decline, walk away, low space, USB busy...
python3 -I sim/mpy_check.py app  # MicroPython compatibility check
```
