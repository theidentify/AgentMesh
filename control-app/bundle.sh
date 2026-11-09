#!/bin/bash
# Build AgentMesh Control.app and the agentmesh-presence helper, ad-hoc sign both, and install them:
#   the app    -> ~/Applications/AgentMesh Control.app   (refuses while the app is running)
#   the helper -> $AGENTMESH_HOME/bin/agentmesh-presence  (dir and file 0700, replaced atomically)
# The app also carries the supervisor's launchd job (Contents/Library/LaunchAgents/<label>.plist), which "Run supervisor
# at login" registers through SMAppService; see `agentmesh-log control handover`.
# Usage: bundle.sh [--no-install] [--label LABEL --home DIR]
#   --no-install: build into control-app/build/ only
#   --label/--home: a test build (never installed) whose job runs LABEL with AGENTMESH_HOME=DIR, beside the real one
set -euo pipefail
cd "$(dirname "$0")"

INSTALL=1; LABEL="org.agentmesh.control"; TEST_HOME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-install) INSTALL=0; shift ;;
    --label) LABEL="$2"; INSTALL=0; shift 2 ;;
    --home) TEST_HOME="$2"; INSTALL=0; shift 2 ;;
    *) echo "usage: bundle.sh [--no-install] [--label LABEL --home DIR]" >&2; exit 2 ;;
  esac
done
if [ "$LABEL" != "org.agentmesh.control" ] && [ -z "$TEST_HOME" ]; then echo "--label needs --home" >&2; exit 2; fi
case "$TEST_HOME" in ""|/*) ;; *) echo "--home must be absolute" >&2; exit 2 ;; esac
HOME_DIR="${AGENTMESH_HOME:-$HOME/.agentmesh}"
APP_NAME="AgentMesh Control.app"
OUT="build"
REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
# A build from uncommitted changes says so in its version, so the menu never claims a commit it does not match.
[ -z "$(git status --porcelain -- . 2>/dev/null)" ] || REV="$REV-dirty"

swift build -c release --product AgentMeshControl
swift build -c release --product agentmesh-presence
BIN="$(swift build -c release --show-bin-path)"

rm -rf "$OUT/$APP_NAME" "$OUT/AppIcon.iconset"
mkdir -p "$OUT/$APP_NAME/Contents/MacOS" "$OUT/$APP_NAME/Contents/Resources" "$OUT/$APP_NAME/Contents/Library/LaunchAgents"
cp "$BIN/AgentMeshControl" "$OUT/$APP_NAME/Contents/MacOS/AgentMeshControl"
# The app icon (notifications, System Settings, Finder) is drawn from code, so the repo holds no binary image.
swift Scripts/make-icon.swift "$OUT/AppIcon.iconset"
iconutil -c icns "$OUT/AppIcon.iconset" -o "$OUT/$APP_NAME/Contents/Resources/AppIcon.icns"
cat > "$OUT/$APP_NAME/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>org.agentmesh.control-app</string>
  <key>CFBundleName</key><string>AgentMesh Control</string>
  <key>CFBundleExecutable</key><string>AgentMeshControl</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>$REV</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>AgentMeshSupervisorLabel</key><string>$LABEL</string>
</dict></plist>
EOF
# The supervisor's job. launchd cannot expand $HOME in a plist, so the launcher picks the home and the log files.
LAUNCHER="$OUT/$APP_NAME/Contents/Resources/agentmesh-control-launcher"
cat > "$LAUNCHER" <<'LAUNCH'
#!/bin/sh
# AgentMesh supervisor, started by launchd from AgentMesh Control's login item.
umask 077
AGENTMESH_HOME="${AGENTMESH_HOME:-$HOME/.agentmesh}"; export AGENTMESH_HOME
mkdir -p "$AGENTMESH_HOME/logs"
exec "$AGENTMESH_HOME/venv/bin/AgentMesh" -m agentmesh_log.cli control serve \
  >>"$AGENTMESH_HOME/logs/control.out.log" 2>>"$AGENTMESH_HOME/logs/control.err.log"
LAUNCH
chmod 755 "$LAUNCHER"
ENV_XML=""
[ -z "$TEST_HOME" ] || ENV_XML="<key>EnvironmentVariables</key><dict><key>AGENTMESH_HOME</key><string>$TEST_HOME</string></dict>"
JOB="$OUT/$APP_NAME/Contents/Library/LaunchAgents/$LABEL.plist"
cat > "$JOB" <<JOBEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>BundleProgram</key><string>Contents/Resources/agentmesh-control-launcher</string>
  $ENV_XML
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>Umask</key><integer>63</integer>
  <key>AssociatedBundleIdentifiers</key><array><string>org.agentmesh.control-app</string></array>
</dict></plist>
JOBEOF
plutil -lint -s "$JOB"
# Sign last: SMAppService refuses to register a job from a bundle whose signature does not cover it.
codesign -s - --force "$OUT/$APP_NAME"
codesign --verify --strict "$OUT/$APP_NAME"
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
