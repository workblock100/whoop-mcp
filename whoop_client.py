"""Whoop API v2 client with OAuth refresh-token rotation.

Whoop rotates refresh tokens on every refresh — each call to /oauth/oauth2/token
returns a NEW refresh_token that invalidates the old one. We persist the latest
token to disk so restarts don't lose auth.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

WHOOP_API_BASE = "https://api.prod.whoop.com"
TOKEN_URL = f"{WHOOP_API_BASE}/oauth/oauth2/token"
AUTH_URL = f"{WHOOP_API_BASE}/oauth/oauth2/auth"

DEFAULT_SCOPES = [
    "read:profile",
    "read:body_measurement",
    "read:cycles",
    "read:sleep",
    "read:recovery",
    "read:workout",
    "offline",
]


class WhoopAuthError(Exception):
    pass


class TokenStore:
    """JSON-file-backed store for the rotating Whoop refresh token + cached access token."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._cache: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._cache = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                self._cache = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2))
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    async def get(self) -> dict[str, Any]:
        async with self._lock:
            return dict(self._cache)

    async def update(self, **fields: Any) -> None:
        async with self._lock:
            self._cache.update(fields)
            self._save()

    async def clear(self) -> None:
        async with self._lock:
            self._cache = {}
            if self.path.exists():
                self.path.unlink()


class WhoopClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_store: TokenStore,
        bootstrap_refresh_token: str | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.store = token_store
        self.bootstrap_refresh_token = bootstrap_refresh_token
        self._http = httpx.AsyncClient(timeout=30.0)
        self._refresh_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def has_auth(self) -> bool:
        tokens = await self.store.get()
        return bool(tokens.get("refresh_token") or self.bootstrap_refresh_token)

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange an authorization code for tokens (initial OAuth completion)."""
        resp = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise WhoopAuthError(f"Token exchange failed: {resp.status_code} {resp.text}")
        data = resp.json()
        await self._persist_tokens(data)
        return data

    async def _persist_tokens(self, data: dict[str, Any]) -> None:
        expires_at = int(time.time()) + int(data.get("expires_in", 3600)) - 60
        await self.store.update(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            scope=data.get("scope", ""),
        )

    async def _current_refresh_token(self) -> str | None:
        tokens = await self.store.get()
        return tokens.get("refresh_token") or self.bootstrap_refresh_token

    async def _refresh(self) -> str:
        """Use the stored refresh token to get a new access token. Returns new access_token."""
        async with self._refresh_lock:
            tokens = await self.store.get()
            now = int(time.time())
            access = tokens.get("access_token")
            expires_at = tokens.get("expires_at", 0)
            if access and now < expires_at:
                return access

            refresh_token = tokens.get("refresh_token") or self.bootstrap_refresh_token
            if not refresh_token:
                raise WhoopAuthError(
                    "No refresh token. Visit /auth to complete the Whoop OAuth flow first."
                )

            resp = await self._http.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "offline",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code >= 400:
                raise WhoopAuthError(
                    f"Refresh failed: {resp.status_code} {resp.text}. Re-run /auth."
                )
            data = resp.json()
            await self._persist_tokens(data)
            return data["access_token"]

    async def _access_token(self) -> str:
        tokens = await self.store.get()
        access = tokens.get("access_token")
        expires_at = tokens.get("expires_at", 0)
        if access and int(time.time()) < expires_at:
            return access
        return await self._refresh()

    async def request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Authenticated GET against the Whoop API. Retries once on 401 by forcing refresh."""
        url = f"{WHOOP_API_BASE}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        for attempt in range(2):
            access = await self._access_token()
            resp = await self._http.get(
                url,
                params=clean_params,
                headers={"Authorization": f"Bearer {access}"},
            )
            if resp.status_code == 401 and attempt == 0:
                await self.store.update(access_token=None, expires_at=0)
                continue
            if resp.status_code >= 400:
                raise WhoopAuthError(
                    f"Whoop API {resp.status_code} on {path}: {resp.text}"
                )
            return resp.json()
        raise WhoopAuthError(f"Whoop API failed twice on {path}")

    def authorize_url(self, redirect_uri: str, state: str, scopes: list[str] | None = None) -> str:
        scope_param = "%20".join(scopes or DEFAULT_SCOPES)
        return (
            f"{AUTH_URL}"
            f"?response_type=code"
            f"&client_id={self.client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope={scope_param}"
            f"&state={state}"
        )
