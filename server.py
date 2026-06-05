"""Whoop MCP server — Streamable HTTP transport for use as a Claude custom connector.

Auth model (single-user deployment):
- The MCP endpoint is mounted under a secret URL path (MCP_SECRET) so only the
  user with the URL can call tools. No bearer token negotiation needed.
- Whoop tokens are stored server-side and rotated automatically.
- Initial Whoop OAuth is done by visiting /auth from the user's phone or laptop.
"""
from __future__ import annotations

import contextlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from whoop_client import DEFAULT_SCOPES, TokenStore, WhoopAuthError, WhoopClient

load_dotenv()

CLIENT_ID = os.environ.get("WHOOP_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("WHOOP_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
MCP_SECRET = os.environ.get("MCP_SECRET", "")
TOKEN_STORE_PATH = os.environ.get("TOKEN_STORE_PATH", "./tokens.json")
BOOTSTRAP_REFRESH = os.environ.get("WHOOP_REFRESH_TOKEN") or None

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit("WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET are required.")
if not PUBLIC_URL:
    raise SystemExit("PUBLIC_URL is required (your deployed https URL, no trailing slash).")
if not MCP_SECRET or len(MCP_SECRET) < 24:
    raise SystemExit(
        "MCP_SECRET must be set to a long random string (>=24 chars). "
        'Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )

REDIRECT_URI = f"{PUBLIC_URL}/auth/callback"
_oauth_states: dict[str, float] = {}

token_store = TokenStore(TOKEN_STORE_PATH)
whoop = WhoopClient(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    token_store=token_store,
    bootstrap_refresh_token=BOOTSTRAP_REFRESH,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _window(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, days))
    return _iso(start), _iso(end)


# --- MCP server -------------------------------------------------------------

mcp = FastMCP(
    "whoop",
    instructions=(
        "Whoop biometric data: recovery, sleep, workouts, strain, HRV. "
        "All times are returned in UTC. Date params accept ISO 8601 or 'YYYY-MM-DD'."
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


@mcp.tool()
async def get_profile() -> dict[str, Any]:
    """Get the authenticated user's basic Whoop profile (name, email, user id)."""
    return await whoop.request("/developer/v2/user/profile/basic")


@mcp.tool()
async def get_body_measurements() -> dict[str, Any]:
    """Get the user's body measurements: height (m), weight (kg), max heart rate."""
    return await whoop.request("/developer/v2/user/measurement/body")


@mcp.tool()
async def get_recent_recovery(days: int = 7, limit: int = 25) -> dict[str, Any]:
    """Get recovery scores from the last N days.

    Each record includes recovery_score (0-100), resting_heart_rate, hrv_rmssd_milli,
    spo2_percentage, skin_temp_celsius, and the cycle it belongs to.

    Args:
        days: How many days back to fetch. Default 7.
        limit: Max records per page (Whoop max is 25). Default 25.
    """
    start, end = _window(days)
    return await whoop.request(
        "/developer/v2/recovery",
        params={"start": start, "end": end, "limit": min(limit, 25)},
    )


@mcp.tool()
async def get_recent_sleep(days: int = 7, limit: int = 25) -> dict[str, Any]:
    """Get sleep activities from the last N days.

    Includes sleep stages (light, deep/SWS, REM, awake), sleep_performance_percentage,
    sleep_efficiency, respiratory_rate, sleep_consistency_percentage.

    Args:
        days: How many days back. Default 7.
        limit: Max records per page (Whoop max 25). Default 25.
    """
    start, end = _window(days)
    return await whoop.request(
        "/developer/v2/activity/sleep",
        params={"start": start, "end": end, "limit": min(limit, 25)},
    )


@mcp.tool()
async def get_recent_workouts(days: int = 14, limit: int = 25) -> dict[str, Any]:
    """Get workouts from the last N days.

    Includes sport_name, strain (0-21), average_heart_rate, max_heart_rate,
    distance_meter, kilojoule, percent_recorded, zone_durations (HR zones).

    Args:
        days: How many days back. Default 14.
        limit: Max records per page (Whoop max 25). Default 25.
    """
    start, end = _window(days)
    return await whoop.request(
        "/developer/v2/activity/workout",
        params={"start": start, "end": end, "limit": min(limit, 25)},
    )


@mcp.tool()
async def get_recent_cycles(days: int = 7, limit: int = 25) -> dict[str, Any]:
    """Get physiological cycles (Whoop's ~24h day construct) from the last N days.

    Each cycle has strain (0-21), kilojoule, average_heart_rate, max_heart_rate.
    Use the cycle id to fetch the matching recovery via get_recovery_for_cycle.

    Args:
        days: How many days back. Default 7.
        limit: Max records per page (Whoop max 25). Default 25.
    """
    start, end = _window(days)
    return await whoop.request(
        "/developer/v2/cycle",
        params={"start": start, "end": end, "limit": min(limit, 25)},
    )


@mcp.tool()
async def get_recovery_for_cycle(cycle_id: int) -> dict[str, Any]:
    """Get the recovery record tied to a specific cycle id."""
    return await whoop.request(f"/developer/v2/cycle/{cycle_id}/recovery")


@mcp.tool()
async def get_workout(workout_id: str) -> dict[str, Any]:
    """Get full detail for a single workout by id (UUID)."""
    return await whoop.request(f"/developer/v2/activity/workout/{workout_id}")


@mcp.tool()
async def get_sleep(sleep_id: str) -> dict[str, Any]:
    """Get full detail for a single sleep activity by id (UUID)."""
    return await whoop.request(f"/developer/v2/activity/sleep/{sleep_id}")


@mcp.tool()
async def get_cycle(cycle_id: int) -> dict[str, Any]:
    """Get full detail for a single cycle by id."""
    return await whoop.request(f"/developer/v2/cycle/{cycle_id}")


@mcp.tool()
async def get_today() -> dict[str, Any]:
    """Aggregate snapshot of today: current cycle (strain so far), latest recovery, last night's sleep.

    Useful as a single 'how am I doing today' lookup.
    """
    start, end = _window(2)
    out: dict[str, Any] = {}
    try:
        cycles = await whoop.request(
            "/developer/v2/cycle",
            params={"start": start, "end": end, "limit": 5},
        )
        records = cycles.get("records", [])
        out["current_cycle"] = records[0] if records else None
    except WhoopAuthError as exc:
        out["current_cycle_error"] = str(exc)

    try:
        recovery = await whoop.request(
            "/developer/v2/recovery",
            params={"start": start, "end": end, "limit": 5},
        )
        records = recovery.get("records", [])
        out["latest_recovery"] = records[0] if records else None
    except WhoopAuthError as exc:
        out["latest_recovery_error"] = str(exc)

    try:
        sleep = await whoop.request(
            "/developer/v2/activity/sleep",
            params={"start": start, "end": end, "limit": 5},
        )
        records = sleep.get("records", [])
        out["last_sleep"] = records[0] if records else None
    except WhoopAuthError as exc:
        out["last_sleep_error"] = str(exc)

    return out


# --- OAuth + landing routes -------------------------------------------------

LANDING_HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Whoop MCP</title>
<style>
body{{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;color:#111}}
.b{{display:inline-block;background:#000;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:600}}
.ok{{color:#0a8}}.bad{{color:#c00}}code{{background:#f4f4f4;padding:2px 6px;border-radius:4px;font-size:13px}}
</style></head><body>
<h1>Whoop MCP</h1>
<p>Status: <span class="{cls}">{status}</span></p>
<p>{cta}</p>
<hr>
<p><strong>Custom connector URL:</strong></p>
<p><code>{mcp_url}</code></p>
<p style="color:#666;font-size:13px">Paste this into Claude → Settings → Connectors → Add custom connector.</p>
</body></html>
"""


async def landing(_request: Request) -> HTMLResponse:
    has_auth = await whoop.has_auth()
    if has_auth:
        cls, status = "ok", "connected to Whoop"
        cta = '<p><a class="b" href="/auth">Re-authorize</a></p>'
    else:
        cls, status = "bad", "not yet authorized"
        cta = '<p><a class="b" href="/auth">Connect Whoop</a></p>'
    return HTMLResponse(
        LANDING_HTML.format(
            cls=cls,
            status=status,
            cta=cta,
            mcp_url=f"{PUBLIC_URL}/{MCP_SECRET}/mcp",
        )
    )


async def auth_start(_request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = datetime.now(timezone.utc).timestamp()
    cutoff = datetime.now(timezone.utc).timestamp() - 600
    for k in list(_oauth_states):
        if _oauth_states[k] < cutoff:
            del _oauth_states[k]
    scope_param = quote_plus(" ".join(DEFAULT_SCOPES))
    url = (
        f"https://api.prod.whoop.com/oauth/oauth2/auth"
        f"?response_type=code"
        f"&client_id={quote_plus(CLIENT_ID)}"
        f"&redirect_uri={quote_plus(REDIRECT_URI)}"
        f"&scope={scope_param}"
        f"&state={state}"
    )
    return RedirectResponse(url, status_code=302)


async def auth_callback(request: Request) -> HTMLResponse:
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state or state not in _oauth_states:
        return HTMLResponse(
            "<h1>Auth failed</h1><p>Missing code or invalid state.</p>",
            status_code=400,
        )
    _oauth_states.pop(state, None)
    try:
        await whoop.exchange_code(code, REDIRECT_URI)
    except WhoopAuthError as exc:
        return HTMLResponse(f"<h1>Auth failed</h1><pre>{exc}</pre>", status_code=400)
    return HTMLResponse(
        '<h1>Connected.</h1><p>Whoop is now linked to this MCP server. '
        'You can close this tab and return to Claude.</p>'
    )


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "authed": await whoop.has_auth()})


# --- Mount everything -------------------------------------------------------

mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp_app.router.lifespan_context(app):
        try:
            yield
        finally:
            await whoop.aclose()


app = Starlette(
    debug=False,
    routes=[
        Route("/", landing),
        Route("/auth", auth_start),
        Route("/auth/callback", auth_callback),
        Route("/health", health),
        Mount(f"/{MCP_SECRET}", app=mcp_app),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
    )
