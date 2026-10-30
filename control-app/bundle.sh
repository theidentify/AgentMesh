#!/bin/bash
# Build AgentMesh Control.app and the agentmesh-presence helper, ad-hoc sign both, and install them:
#   the app    -> ~/Applications/AgentMesh Control.app   (refuses while the app is running)
#   the helper -> $AGENTMESH_HOME/bin/agentmesh-presence  (dir and file 0700, replaced atomically)
# Usage: bundle.sh [--no-install]   (--no-install: build into control-app/build/ only)
set -euo pipefail
cd "$(dirname "$0")"

INSTALL=1; [ "${1:-}" = "--no-install" ] && INSTALL=0
HOME_DIR="${AGENTMESH_HOME:-$HOME/.agentmesh}"
APP_NAME="AgentMesh Control.app"
OUT="build"
REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

swift build -c release --product AgentMeshControl
swift build -c release --product agentmesh-presence
BIN="$(swift build -c release --show-bin-path)"

rm -rf "$OUT/$APP_NAME"
mkdir -p "$OUT/$APP_NAME/Contents/MacOS"
cp "$BIN/AgentMeshControl" "$OUT/$APP_NAME/Contents/MacOS/AgentMeshControl"
cat > "$OUT/$APP_NAME/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>org.agentmesh.control-app</string>
  <key>CFBundleName</key><string>AgentMesh Control</string>
  <key>CFBundleExecutable</key><string>AgentMeshControl</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>$REV</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
EOF
codesign -s - --force "$OUT/$APP_NAME"
cp "$BIN/agentmesh-presence" "$OUT/agentmesh-presence"
codesign -s - --force "$OUT/agentmesh-presence"
echo "built $OUT/$APP_NAME and $OUT/agentmesh-presence ($REV)"

[ "$INSTALL" = 1 ] || exit 0

if pgrep -x AgentMeshControl >/dev/null; then
  echo "ABORT: AgentMesh Control is running; quit it from its menu first." >&2; exit 1
fi
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/$APP_NAME.new"
cp -R "$OUT/$APP_NAME" "$HOME/Applications/$APP_NAME.new"
rm -rf "$HOME/Applications/$APP_NAME"
mv "$HOME/Applications/$APP_NAME.new" "$HOME/Applications/$APP_NAME"
echo "installed ~/Applications/$APP_NAME"

# The supervisor refuses a helper that is a symlink, group/world-writable, or in such a directory.
mkdir -p "$HOME_DIR/bin"; chmod 700 "$HOME_DIR/bin"
TMP="$(mktemp "$HOME_DIR/bin/.agentmesh-presence.XXXXXX")"
cp "$OUT/agentmesh-presence" "$TMP"; chmod 700 "$TMP"
mv -f "$TMP" "$HOME_DIR/bin/agentmesh-presence"
echo "installed $HOME_DIR/bin/agentmesh-presence"
"$HOME_DIR/bin/agentmesh-presence" available || echo "note: Touch ID / password evaluation is not available here"
