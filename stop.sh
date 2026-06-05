#!/usr/bin/env bash
# Stop whoop-mcp server + its cloudflared tunnel (leaves other cloudflared tunnels alone).
pkill -f "python.*server.py" 2>/dev/null || true
ps aux | grep -E "cloudflared tunnel.*localhost:8080|cloudflared tunnel --url http://localhost:8080" | grep -v grep | awk '{print $2}' | xargs -I{} kill {} 2>/dev/null || true
echo "stopped"
