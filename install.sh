#!/bin/sh
# Install Air Merge on a GitHub Universe 2026 badge.
#
# 1. On the badge, open the Mass Storage app so it mounts as BADGER.
# 2. Run this script, either from a clone of the repo:
#        ./install.sh
#    or straight from GitHub, no clone needed:
#        curl -fsSL https://raw.githubusercontent.com/jmrada14/air-merge/main/install.sh | sh
#
# Options: --volume PATH (badge disk, found automatically by default),
#          --ref NAME (branch or tag to download, default main),
#          --no-eject (leave the disk mounted when done).
set -eu

REPO="jmrada14/air-merge"
REF="main"
FILES="__init__.py am_fs.py am_proto.py am_ui.py"
VOLUME=""
EJECT=1

while [ $# -gt 0 ]; do
    case "$1" in
        --volume) VOLUME="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --no-eject) EJECT=0; shift ;;
        -h|--help) sed -n '2,15p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# --- find the badge ---------------------------------------------------------
if [ -z "$VOLUME" ]; then
    for candidate in /Volumes/BADGER "/media/${USER:-}/BADGER" "/run/media/${USER:-}/BADGER" /media/BADGER; do
        if [ -d "$candidate/apps" ]; then VOLUME="$candidate"; break; fi
    done
fi
[ -n "$VOLUME" ] && [ -d "$VOLUME/apps" ] || fail "no badge found. Open Mass Storage on the badge so it mounts as BADGER, or pass --volume PATH."
[ -d "$VOLUME/apps/menu" ] || fail "$VOLUME doesn't look like a badge (no apps/menu)."

# --- get the app files: local clone if we're in one, otherwise GitHub --------
SCRIPT_DIR=""
case "$0" in
    */*) SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)" ;;
esac
WORK=""
cleanup() { if [ -n "$WORK" ]; then rm -rf "$WORK"; fi; }
trap cleanup EXIT

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/app/__init__.py" ]; then
    SOURCE="$SCRIPT_DIR/app"
    say "Installing Air Merge from $SOURCE"
else
    command -v curl >/dev/null 2>&1 || fail "curl is needed to download Air Merge."
    WORK="$(mktemp -d)"
    SOURCE="$WORK"
    say "Downloading Air Merge ($REF) from github.com/$REPO"
    for file in $FILES; do
        curl -fsSL "https://raw.githubusercontent.com/$REPO/$REF/app/$file" -o "$WORK/$file" \
            || fail "couldn't download app/$file"
        [ -s "$WORK/$file" ] || fail "app/$file downloaded empty"
    done
fi

for file in $FILES; do
    [ -f "$SOURCE/$file" ] || fail "missing app/$file"
done

# --- copy onto the badge ----------------------------------------------------
TARGET="$VOLUME/apps/air_merge"
mkdir -p "$TARGET"
for file in $FILES; do
    COPYFILE_DISABLE=1 cp "$SOURCE/$file" "$TARGET/$file"
done
for file in $FILES; do
    cmp -s "$SOURCE/$file" "$TARGET/$file" || fail "copy of $file didn't verify"
done
rm -f "$TARGET"/._* "$VOLUME/apps/._air_merge"
sync
say "Installed Air Merge to $TARGET"

# --- eject so it's safe to unplug -------------------------------------------
if [ "$EJECT" -eq 1 ]; then
    if command -v diskutil >/dev/null 2>&1; then
        diskutil eject "$VOLUME" >/dev/null 2>&1 && say "Ejected $VOLUME" || say "Eject $VOLUME before unplugging."
    elif command -v umount >/dev/null 2>&1; then
        umount "$VOLUME" 2>/dev/null && say "Unmounted $VOLUME" || say "Eject $VOLUME before unplugging."
    fi
fi
say "Reboot the badge: Air Merge is at the end of the menu."
