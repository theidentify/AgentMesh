#!/bin/sh
# Keep credentials out of tracing, argv and diagnostics, including Keychain errors.
set +x
if ! command -v cloudflared >/dev/null 2>&1; then
    echo 'Insight tunnel: cloudflared is missing from PATH.' >&2
    exit 1
fi
if ! command -v security >/dev/null 2>&1; then
    echo 'Insight tunnel: macOS security command is missing.' >&2
    exit 1
fi
TUNNEL_TOKEN=$(security find-generic-password -s org.agentmesh.insight.tunnel -a agentmesh -w 2>/dev/null) || {
    echo 'Insight tunnel: could not read the Keychain token.' >&2
    exit 1
}
if [ -z "$TUNNEL_TOKEN" ]; then
    echo 'Insight tunnel: Keychain token is empty.' >&2
    exit 1
fi
export TUNNEL_TOKEN
exec cloudflared tunnel --no-autoupdate run
