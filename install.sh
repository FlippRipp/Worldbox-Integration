#!/usr/bin/env sh
# Install wb_toy_link into a WorldboxAI checkout.
#   ./install.sh /path/to/WorldboxAI          # symlink (updates with git pull)
#   ./install.sh /path/to/WorldboxAI --copy   # plain copy
set -e

TARGET="$1"
MODE="$2"
SRC="$(cd "$(dirname "$0")" && pwd)/wb_toy_link"

if [ -z "$TARGET" ] || [ ! -d "$TARGET/modules" ]; then
    echo "Usage: $0 /path/to/WorldboxAI [--copy]" >&2
    echo "(the target must contain a modules/ directory)" >&2
    exit 1
fi

DEST="$TARGET/modules/wb_toy_link"
rm -rf "$DEST"
if [ "$MODE" = "--copy" ]; then
    cp -r "$SRC" "$DEST"
    echo "Copied wb_toy_link -> $DEST"
else
    ln -s "$SRC" "$DEST"
    echo "Symlinked wb_toy_link -> $DEST"
fi
echo "Restart the WorldboxAI backend; 'Toy Link' appears in the module list"
echo "and 'Toy Studio' in the main menu."
