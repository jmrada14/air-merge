#!/bin/sh
# Install Air Merge on a GitHub Universe 2026 badge.
#
# 1. On the badge, open the Mass Storage app so it mounts as BADGER.
# 2. Run this script, either from a clone of the repo:
#        ./install.sh
#    or straight from GitHub, no clone needed:
#        curl -fsSL https://raw.githubusercontent.com/jmrada14/air-merge/main/install.sh | sh
#
# It also adds the Air Merge icon to the menu, which needs Python 3 (the
# first run downloads mpy-cross into ~/.cache/air-merge). Without Python the
# app still installs and shows the default icon.
#
# Options: --volume PATH (badge disk, found automatically by default),
#          --ref NAME (branch or tag to download, default main),
#          --no-icon (skip the menu icon),
#          --no-eject (leave the disk mounted when done).
set -eu

REPO="jmrada14/air-merge"
REF="main"
FILES="__init__.py am_fs.py am_proto.py am_ui.py"
VOLUME=""
EJECT=1
ICON=1
MPY_CROSS="mpy-cross==1.26.1.post2"  # the version that builds the badge's own icons.mpy
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/air-merge"

while [ $# -gt 0 ]; do
    case "$1" in
        --volume) VOLUME="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --no-eject) EJECT=0; shift ;;
        --no-icon) ICON=0; shift ;;
        -h|--help) sed -n '2,20p' "$0" 2>/dev/null || true; exit 0 ;;
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
cleanup() { if [ -n "$WORK" ]; then rm -rf $WORK; fi; }  # unquoted: may hold several dirs
trap cleanup EXIT
fetch() {  # fetch REPO_PATH DEST: download one file of the repo
    curl -fsSL "https://raw.githubusercontent.com/$REPO/$REF/$1" -o "$2" && [ -s "$2" ]
}

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/app/__init__.py" ]; then
    SOURCE="$SCRIPT_DIR/app"
    say "Installing Air Merge from $SOURCE"
else
    command -v curl >/dev/null 2>&1 || fail "curl is needed to download Air Merge."
    WORK="$(mktemp -d)"
    SOURCE="$WORK"
    say "Downloading Air Merge ($REF) from github.com/$REPO"
    for file in $FILES; do
        fetch "app/$file" "$WORK/$file" || fail "couldn't download app/$file"
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

# --- menu icon: patch the menu's icons.py, recompile icons.mpy, add the animation
# Each step is checked by hand: a failure here leaves the menu as it was and
# the app still works with the default icon.
install_icon() {
    menu="$VOLUME/apps/menu"
    [ -f "$menu/src/icons.py" ] && [ -f "$menu/icons.mpy" ] && [ -d "$menu/anim" ] \
        || { say "The menu's icon files weren't found."; return 1; }
    command -v python3 >/dev/null 2>&1 || { say "Python 3 isn't installed."; return 1; }
    build="$(mktemp -d)" || return 1
    WORK="$WORK $build"
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/tools/menu_icon.py" ]; then
        cp "$SCRIPT_DIR/tools/menu_icon.py" "$build/menu_icon.py" || return 1
    else
        fetch tools/menu_icon.py "$build/menu_icon.py" || { say "Couldn't download tools/menu_icon.py."; return 1; }
    fi
    if [ ! -x "$CACHE/venv/bin/mpy-cross" ]; then
        say "Setting up $MPY_CROSS (first run only)"
        python3 -m venv "$CACHE/venv" >/dev/null && "$CACHE/venv/bin/pip" install -q --disable-pip-version-check "$MPY_CROSS" \
            || { rm -rf "$CACHE/venv"; say "Couldn't install mpy-cross."; return 1; }
    fi
    python3 -I "$build/menu_icon.py" --icons-src "$menu/src/icons.py" --icons-out "$build/icons.py" \
        --anim-out "$build/air_merge.bin" >/dev/null || return 1
    # compile from inside the folder with a relative name, like the stock build
    (cd "$build" && "$CACHE/venv/bin/mpy-cross" icons.py -o icons.mpy) && [ -s "$build/icons.mpy" ] || return 1
    if [ ! -f "$CACHE/menu-backup/icons.mpy" ]; then
        mkdir -p "$CACHE/menu-backup" && cp "$menu/icons.mpy" "$menu/src/icons.py" "$CACHE/menu-backup/" || return 1
    fi
    # animation first, then the source, then the compiled icons the menu loads
    for pair in "air_merge.bin anim/air_merge.bin" "icons.py src/icons.py" "icons.mpy icons.mpy"; do
        from="${pair% *}"; to="${pair#* }"
        COPYFILE_DISABLE=1 cp "$build/$from" "$menu/$to" && cmp -s "$build/$from" "$menu/$to" || return 1
    done
    rm -f "$menu"/._* "$menu"/src/._* "$menu"/anim/._*
    sync
}
if [ "$ICON" -eq 1 ]; then
    if install_icon; then
        say "Added the Air Merge icon to the menu"
    else
        say "Skipped the menu icon; Air Merge will show the default icon."
    fi
fi

# --- eject so it's safe to unplug -------------------------------------------
if [ "$EJECT" -eq 1 ]; then
    if command -v diskutil >/dev/null 2>&1; then
        diskutil eject "$VOLUME" >/dev/null 2>&1 && say "Ejected $VOLUME" || say "Eject $VOLUME before unplugging."
    elif command -v umount >/dev/null 2>&1; then
        umount "$VOLUME" 2>/dev/null && say "Unmounted $VOLUME" || say "Eject $VOLUME before unplugging."
    fi
fi
say "Reboot the badge: Air Merge is at the end of the menu."
