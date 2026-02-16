"""ChatGPT OAuth token management — load, refresh, cache, device code flow.

Uses the OpenAI Codex public OAuth client for ChatGPT Plus subscription auth.
Mirrors the pattern from outlook.py (persistent cache, silent acquire, graceful no-op).
"""

import base64
import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("conduit.chatgpt_auth")

# OpenAI OAuth endpoints
TOKEN_URL = "https://auth.openai.com/oauth/token"
DEVICE_CODE_URL = "https://auth.openai.com/oauth/authorize/device"

# Codex public OAuth client
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Token cache location (server-local, falls back to Codex CLI cache)
_CACHE_PATH = Path(__file__).parent / ".chatgpt_token_cache.json"
_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"

# In-memory caches
_cached_tokens: dict | None = None
_cached_api_token: str | None = None  # Exchanged API-scoped token
_api_token_exp: float = 0  # Expiry timestamp for the API token


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (just to read exp)."""
    try:
        payload_b64 = token.split(".")[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _is_token_expired(access_token: str, buffer_seconds: int = 300) -> bool:
    """Check if a JWT access token is expired (with buffer)."""
    payload = _decode_jwt_payload(access_token)
    exp = payload.get("exp", 0)
    return time.time() >= (exp - buffer_seconds)


def _load_cache() -> dict | None:
    """Load tokens from cache file, falling back to Codex auth."""
    global _cached_tokens

    if _cached_tokens is not None:
        return _cached_tokens

    # Try server-local cache first
    if _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text())
            if data.get("access_token") and data.get("refresh_token"):
                _cached_tokens = data
                log.info("Loaded ChatGPT tokens from local cache")
                return _cached_tokens
        except (json.JSONDecodeError, KeyError):
            pass

    # Fall back to Codex CLI auth
    if _CODEX_AUTH_PATH.exists():
        try:
            raw = json.loads(_CODEX_AUTH_PATH.read_text())
            tokens = raw.get("tokens", {})
            if tokens.get("access_token") and tokens.get("refresh_token"):
                _cached_tokens = {
                    "access_token": tokens["access_token"],
                    "refresh_token": tokens["refresh_token"],
                    "id_token": tokens.get("id_token", ""),
                    "account_id": tokens.get("account_id", ""),
                }
                # Persist to local cache so we don't depend on Codex
                _save_cache(_cached_tokens)
                log.info("Imported ChatGPT tokens from Codex CLI auth")
                return _cached_tokens
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def _save_cache(tokens: dict):
    """Persist tokens to the local cache file."""
    global _cached_tokens
    _cached_tokens = tokens
    try:
        _CACHE_PATH.write_text(json.dumps(tokens, indent=2))
    except OSError as e:
        log.warning("Failed to save ChatGPT token cache: %s", e)


def _refresh_token_sync(refresh_token: str) -> dict | None:
    """Refresh the access token using the refresh token (sync)."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "id_token": data.get("id_token", ""),
            "account_id": _cached_tokens.get("account_id", "") if _cached_tokens else "",
        }
    except Exception as e:
        log.error("ChatGPT token refresh failed: %s", e)
        return None


async def _refresh_token_async(refresh_token: str) -> dict | None:
    """Refresh the access token using the refresh token (async)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "id_token": data.get("id_token", ""),
                "account_id": _cached_tokens.get("account_id", "") if _cached_tokens else "",
            }
    except Exception as e:
        log.error("ChatGPT token refresh failed (async): %s", e)
        return None


def _exchange_token_sync(id_token: str) -> str | None:
    """Exchange id_token for an API-scoped access token (sync).

    Codex CLI uses RFC 8693 token exchange: the id_token is exchanged
    at the OpenAI token endpoint for a short-lived API bearer token.
    """
    global _cached_api_token, _api_token_exp
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": CLIENT_ID,
                "subject_token": id_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                "audience": "https://api.openai.com/v1",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        api_token = data.get("access_token", "")
        if api_token:
            _cached_api_token = api_token
            expires_in = data.get("expires_in", 3600)
            _api_token_exp = time.time() + expires_in - 60  # 60s buffer
            log.info("Exchanged id_token for API token (expires in %ds)", expires_in)
            return api_token
    except Exception as e:
        log.error("Token exchange failed: %s", e)
    return None


async def _exchange_token_async(id_token: str) -> str | None:
    """Exchange id_token for an API-scoped access token (async)."""
    global _cached_api_token, _api_token_exp
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": CLIENT_ID,
                    "subject_token": id_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                    "audience": "https://api.openai.com/v1",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            api_token = data.get("access_token", "")
            if api_token:
                _cached_api_token = api_token
                expires_in = data.get("expires_in", 3600)
                _api_token_exp = time.time() + expires_in - 60
                log.info("Exchanged id_token for API token (expires in %ds)", expires_in)
                return api_token
    except Exception as e:
        log.error("Token exchange failed (async): %s", e)
    return None


def _ensure_fresh_tokens_sync() -> dict | None:
    """Ensure we have fresh OAuth tokens (refresh if needed). Returns tokens dict.

    Refreshes if EITHER the access_token or id_token is expired,
    since the id_token is needed for API token exchange and typically
    expires sooner than the access_token.
    """
    tokens = _load_cache()
    if not tokens:
        return None

    id_token = tokens.get("id_token", "")
    access_expired = _is_token_expired(tokens["access_token"])
    id_expired = not id_token or _is_token_expired(id_token)

    if access_expired or id_expired:
        log.info("ChatGPT OAuth tokens need refresh (access_expired=%s, id_expired=%s)",
                 access_expired, id_expired)
        new_tokens = _refresh_token_sync(tokens["refresh_token"])
        if new_tokens:
            _save_cache(new_tokens)
            tokens = new_tokens
        elif access_expired:
            return None
        # If only id_token expired but refresh failed, still return tokens
        # (get_access_token will work, but get_api_token won't)

    return tokens


async def _ensure_fresh_tokens_async() -> dict | None:
    """Ensure we have fresh OAuth tokens (refresh if needed). Returns tokens dict."""
    tokens = _load_cache()
    if not tokens:
        return None

    id_token = tokens.get("id_token", "")
    access_expired = _is_token_expired(tokens["access_token"])
    id_expired = not id_token or _is_token_expired(id_token)

    if access_expired or id_expired:
        log.info("ChatGPT OAuth tokens need refresh (access_expired=%s, id_expired=%s)",
                 access_expired, id_expired)
        new_tokens = await _refresh_token_async(tokens["refresh_token"])
        if new_tokens:
            _save_cache(new_tokens)
            tokens = new_tokens
        elif access_expired:
            return None

    return tokens


def get_access_token() -> str | None:
    """Get a valid OAuth access token (sync). Refreshes if expired.

    Note: This returns the raw OAuth token, NOT an API-scoped token.
    For API calls, use get_api_token() instead.
    """
    tokens = _ensure_fresh_tokens_sync()
    if not tokens:
        return None
    return tokens["access_token"]


async def get_access_token_async() -> str | None:
    """Get a valid OAuth access token (async). Refreshes if expired."""
    tokens = await _ensure_fresh_tokens_async()
    if not tokens:
        return None
    return tokens["access_token"]


def get_api_token() -> str | None:
    """Get an API-scoped bearer token (sync).

    This performs the token exchange that Codex CLI does:
    id_token -> API-scoped access token via RFC 8693 exchange.
    """
    global _cached_api_token, _api_token_exp

    # Return cached API token if still valid
    if _cached_api_token and time.time() < _api_token_exp:
        return _cached_api_token

    # Need fresh OAuth tokens first
    tokens = _ensure_fresh_tokens_sync()
    if not tokens:
        return None

    id_token = tokens.get("id_token", "")
    if not id_token:
        log.warning("No id_token available for token exchange")
        return None

    return _exchange_token_sync(id_token)


async def get_api_token_async() -> str | None:
    """Get an API-scoped bearer token (async).

    This performs the token exchange that Codex CLI does:
    id_token -> API-scoped access token via RFC 8693 exchange.
    """
    global _cached_api_token, _api_token_exp

    # Return cached API token if still valid
    if _cached_api_token and time.time() < _api_token_exp:
        return _cached_api_token

    # Need fresh OAuth tokens first
    tokens = await _ensure_fresh_tokens_async()
    if not tokens:
        return None

    id_token = tokens.get("id_token", "")
    if not id_token:
        log.warning("No id_token available for token exchange")
        return None

    return await _exchange_token_async(id_token)


def is_authenticated() -> bool:
    """Check if we have valid (or refreshable) tokens."""
    tokens = _load_cache()
    return tokens is not None and bool(tokens.get("refresh_token"))


def get_auth_info() -> dict:
    """Get auth status info for the Settings UI."""
    tokens = _load_cache()
    if not tokens:
        return {"authenticated": False}

    info = {"authenticated": True}

    # Extract profile from id_token JWT
    id_token = tokens.get("id_token", "")
    if id_token:
        payload = _decode_jwt_payload(id_token)
        info["email"] = payload.get("email", "")
        auth_data = payload.get("https://api.openai.com/auth", {})
        info["plan"] = auth_data.get("chatgpt_plan_type", "")
        info["user_id"] = auth_data.get("chatgpt_user_id", "")

    # Check if access token is still valid
    access_token = tokens.get("access_token", "")
    if access_token:
        info["token_valid"] = not _is_token_expired(access_token)

    return info


def initiate_device_flow() -> dict | None:
    """Start the device code authorization flow. Returns flow data or None."""
    try:
        resp = httpx.post(
            DEVICE_CODE_URL,
            data={
                "client_id": CLIENT_ID,
                "scope": "openid profile email offline_access",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error("ChatGPT device flow initiation failed: %s", e)
        return None


def poll_device_flow(device_code: str) -> dict:
    """Poll for device flow completion. Returns result dict with status."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device_code,
            },
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            tokens = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "id_token": data.get("id_token", ""),
                "account_id": "",
            }
            _save_cache(tokens)
            return {"status": "complete", "auth_info": get_auth_info()}

        # Still waiting or error
        error_data = resp.json()
        error = error_data.get("error", "unknown")
        if error == "authorization_pending":
            return {"status": "pending"}
        elif error == "slow_down":
            return {"status": "slow_down"}
        elif error == "expired_token":
            return {"status": "expired"}
        else:
            return {"status": "error", "error": error_data.get("error_description", error)}

    except Exception as e:
        return {"status": "error", "error": str(e)}


def clear_cache():
    """Remove cached tokens (for re-authentication)."""
    global _cached_tokens
    _cached_tokens = None
    if _CACHE_PATH.exists():
        _CACHE_PATH.unlink()
