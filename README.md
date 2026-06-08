# Whoop MCP

Personal Whoop data as a remote MCP server you can add to Claude (web, mobile, desktop) as a **custom connector**. Exposes recovery, sleep, workouts, strain, HRV, and physiological cycles via the Whoop v2 API.

## Tools

| Tool | What it returns |
|---|---|
| `get_profile` | name, email, user id |
| `get_body_measurements` | height, weight, max HR |
| `get_today` | today's cycle (strain so far) + latest recovery + last sleep |
| `get_recent_recovery(days, limit)` | recovery score, HRV, RHR, SpO2, skin temp |
| `get_recent_sleep(days, limit)` | stages, performance %, efficiency, respiratory rate |
| `get_recent_workouts(days, limit)` | sport, strain, HR, distance, zones |
| `get_recent_cycles(days, limit)` | physiological cycles with strain |
| `get_recovery_for_cycle(cycle_id)` | recovery for one cycle |
| `get_workout(workout_id)`, `get_sleep(sleep_id)`, `get_cycle(cycle_id)` | single record by id |

## How auth works

Two layers:

1. **Whoop ↔ this server** — OAuth 2.0. You go through Whoop's auth flow once at `/auth`. The server stores a refresh token and rotates it automatically (Whoop rotates refresh tokens on every refresh).
2. **Claude ↔ this server** — the MCP endpoint is mounted under a secret URL path (`MCP_SECRET`). Anyone who has the URL can call tools. Anyone who doesn't can't reach `/mcp` at all. Single-user model. Don't share the URL.

## Setup

### 1. Register a Whoop developer app

1. Go to <https://developer.whoop.com> → create an app
2. Save **Client ID** and **Client Secret**
3. Leave **Redirect URL** blank for now — you'll fill it in after deploy

### 2. Pick a deploy target

Easiest: **fly.io** (free tier covers this, has persistent volume so rotated refresh tokens survive restarts).

```bash
brew install flyctl
fly auth login
cd ~/Documents/whoop-mcp
```

Edit `fly.toml` → change `app = "whoop-mcp-yourname"` to something unique.

```bash
fly launch --no-deploy --copy-config --name whoop-mcp-yourname
fly volumes create whoop_data --size 1 --region iad
```

### 3. Generate your MCP secret + set fly secrets

```bash
MCP_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Your MCP_SECRET: $MCP_SECRET"

fly secrets set \
  WHOOP_CLIENT_ID=xxx \
  WHOOP_CLIENT_SECRET=xxx \
  MCP_SECRET=$MCP_SECRET \
  PUBLIC_URL=https://whoop-mcp-yourname.fly.dev
```

### 4. Deploy

```bash
fly deploy
```

Once deployed, your URL is `https://whoop-mcp-yourname.fly.dev`.

### 5. Set the redirect URL in Whoop developer portal

Back at <https://developer.whoop.com>, set the **Redirect URL** for your app to:

```
https://whoop-mcp-yourname.fly.dev/auth/callback
```

### 6. Authorize Whoop (do this on your phone if you want)

Open `https://whoop-mcp-yourname.fly.dev/` in any browser. Tap **Connect Whoop**, sign in, approve. You're done — refresh tokens are now persisted server-side.

### 7. Add as a Claude custom connector

In Claude (web or mobile): Settings → Connectors → **Add custom connector**. Paste:

```
https://whoop-mcp-yourname.fly.dev/<MCP_SECRET>/mcp
```

(Replace `<MCP_SECRET>` with the value you generated. The secret is in the path — no headers needed.)

Claude will discover the tools. Try: *"Pull my recovery from the last week"* or *"How did I sleep?"*

## Local dev

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, MCP_SECRET, PUBLIC_URL
python server.py
```

For local OAuth testing, use a tunnel (cloudflared, ngrok) so Whoop can redirect back to you, and set `PUBLIC_URL` and the Whoop redirect URL to the tunnel URL.

## Troubleshooting

- **`/auth` redirects to Whoop and comes back with "Auth failed"** — your `PUBLIC_URL` and the Whoop redirect URL must match exactly, including https and no trailing slash.
- **Tools fail with "No refresh token"** — visit `/auth` once to complete the OAuth flow.
- **Tools fail with `Refresh failed: 400`** — the rotated refresh token in `tokens.json` got stale (e.g., volume not mounted, or you re-deployed without persistence). Visit `/auth` again to re-authorize.
- **fly.io app sleeps** — it auto-stops to save resources. First request after sleep takes a few seconds to wake.

## Security notes

- The MCP secret in the URL path is the only thing protecting your Whoop data from the public internet. Treat it like a password. If leaked, rotate `MCP_SECRET` (`fly secrets set MCP_SECRET=...; fly deploy`) and update the URL in Claude.
- `tokens.json` (with mode 0600) holds your live refresh token. The fly.io persistent volume keeps it across restarts.
- This is a **single-tenant** server — one Whoop account per deployment. Don't share the URL.


---

## 💼 Hire the author
Built by **Elijah** — I build custom MCP servers, Python automations, web scrapers, and AI chatbots. Fixed-price from $85, working sample before you pay.
- **Upwork:** https://www.upwork.com/freelancers/~01818ac5bd67ef7935
- **Email:** workblock100@gmail.com
