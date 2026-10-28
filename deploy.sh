#!/bin/sh
# Copy Air Merge onto the badge while it is in USB disk mode (Mass Storage app).
set -eu

VOLUME="${1:-/Volumes/BADGER}"
SOURCE="$(cd "$(dirname "$0")" && pwd)/app"
TARGET="$VOLUME/apps/air_merge"

if [ ! -d "$VOLUME/apps" ]; then
    echo "Badge not found at $VOLUME (open Mass Storage on the badge first)" >&2
    exit 1
fi

mkdir -p "$TARGET"
for file in "$SOURCE"/*.py; do
    COPYFILE_DISABLE=1 cp -X "$file" "$TARGET/"
done
rm -f "$TARGET"/._* "$VOLUME/apps/._air_merge"
sync
echo "Copied $(ls "$SOURCE"/*.py | wc -l | tr -d ' ') files to $TARGET. Eject the disk, then reboot the badge."
