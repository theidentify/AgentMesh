#!/bin/bash
# swift test, plus the swift-testing macro plugin path that a CommandLineTools-only toolchain does not search.
set -euo pipefail
cd "$(dirname "$0")"
PLUGINS="$(xcode-select -p)/usr/lib/swift/host/plugins/testing"
if [ -d "$PLUGINS" ]; then exec swift test -Xswiftc -plugin-path -Xswiftc "$PLUGINS" "$@"; fi
exec swift test "$@"
