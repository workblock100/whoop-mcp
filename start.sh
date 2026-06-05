#!/usr/bin/env bash
# Start the Whoop MCP server + cloudflared tunnel.
# Run this after a Mac restart, or anytime the server/tunnel dies.
#
# If the cloudflared URL changes, you must update:
#   1. PUBLIC_URL in .env (this script does it for you)
#   2. The redirect URL on https://developer-dashboard.whoop.com (manual)
#      → must be: <new URL>/auth/callback
#   3. The custom connector URL in claude.ai → Customize → Connectors → Whoop
#      → must be: <new URL>/<MCP_SECRET>/mcp

set -e
cd "$(dirname "$0")"

echo "== killing any old whoop-mcp/cloudflared processes =="
pkill -f "python.*server.py" 2>/dev/null || true
# Only kill cloudflared tunnels pointing at port 8080 (not other tunnels you have running)
ps aux | grep -E "cloudflared tunnel.*localhost:8080|cloudflared tunnel --url http://localhost:8080" | grep -v grep | awk '{print $2}' | xargs -I{} kill {} 2>/dev/null || true
sleep 2

echo "== starting cloudflared =="
nohup cloudflared tunnel --url http://localhost:8080 > /tmp/cf-tunnel.log 2>&1 &
CF_PID=$!
echo "  cloudflared PID: $CF_PID"

# Wait for the URL to appear
URL=""
for i in $(seq 1 20); do
  sleep 1
  URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/cf-tunnel.log | head -1)
  if [ -n "$URL" ]; then
    break
  fi
done
if [ -z "$URL" ]; then
  echo "FAILED to get cloudflared URL. Check /tmp/cf-tunnel.log"
  exit 1
fi
echo "  tunnel URL: $URL"

echo "== updating PUBLIC_URL in .env =="
OLD_URL=$(grep -E "^PUBLIC_URL=" .env | cut -d= -f2-)
if [ "$OLD_URL" != "$URL" ]; then
  echo "  URL changed: $OLD_URL  ->  $URL"
  echo "  ⚠️  You must update Whoop redirect URL and the Claude connector URL — see top of this script."
fi
sed -i.bak -E "s|^PUBLIC_URL=.*|PUBLIC_URL=$URL|" .env
rm -f .env.bak

echo "== starting whoop-mcp server =="
nohup .venv/bin/python server.py > /tmp/whoop-mcp.log 2>&1 &
SERVER_PID=$!
echo "  server PID: $SERVER_PID"
sleep 3

# Health check
echo "== health =="
curl -s http://localhost:8080/health
echo
curl -s -m 8 "$URL/health"
echo

# Print the connector URL for convenience
MCP_SECRET=$(grep -E "^MCP_SECRET=" .env | cut -d= -f2-)
echo
echo "== ready =="
echo "Tunnel:    $URL"
echo "Auth flow: $URL/auth"
echo "Claude connector URL: $URL/$MCP_SECRET/mcp"
