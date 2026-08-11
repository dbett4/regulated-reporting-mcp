"""
OAuth2 client-credentials authentication for the Workiva API.

Stdlib-only (urllib): exchanges a client id/secret for a bearer token and
caches it at module level, refreshing inside the token's lifetime window.
"""

import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from workiva_mcp.config import AUTH_PATH, TOKEN_LIFETIME, get_urls
from workiva_mcp.ssl_context import create_ssl_context

SSL_CTX = create_ssl_context()
CLIENT_ID_ENV = "WORKIVA_CLIENT_ID"
CLIENT_SECRET_ENV = "WORKIVA_CLIENT_SECRET"

from typing import Optional

# ── Module-level token cache ─────────────────────────────────────────────────
_token: Optional[str] = None
_token_time: float = 0
_token_expires_in: int = 0


class AuthError(Exception):
    pass


def mock_mode() -> bool:
    """True when WORKIVA_MCP_MOCK enables the credential-free fake transport."""
    return os.environ.get("WORKIVA_MCP_MOCK", "").strip().lower() in {"1", "true", "yes"}


def _candidate_env_files() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    if os.environ.get("WORKIVA_ENV_FILE"):
        paths.append(pathlib.Path(os.environ["WORKIVA_ENV_FILE"]).expanduser())

    package_root = pathlib.Path(__file__).resolve().parents[1]
    paths.extend([
        pathlib.Path.cwd() / ".env",
        package_root / ".env",
    ])
    return paths


def _load_local_credentials() -> None:
    missing = {
        key for key in (CLIENT_ID_ENV, CLIENT_SECRET_ENV)
        if not os.environ.get(key)
    }
    if not missing:
        return

    for env_file in _candidate_env_files():
        if not env_file.is_file():
            continue
        try:
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in missing:
                    continue
                value = value.strip().strip("\"'")
                if value:
                    os.environ[key] = value
                    missing.discard(key)
                if not missing:
                    return
        except OSError:
            continue


def _get_credentials():
    _load_local_credentials()
    client_id = os.environ.get(CLIENT_ID_ENV, "")
    client_secret_value = os.environ.get(CLIENT_SECRET_ENV, "")
    if not client_id or not client_secret_value:
        raise AuthError(
            f"Workiva client id and secret must be set ({CLIENT_ID_ENV} / "
            f"{CLIENT_SECRET_ENV} environment variables or a .env file), or "
            "run with WORKIVA_MCP_MOCK=1 for the credential-free mock mode."
        )
    return client_id, client_secret_value


def authenticate() -> str:
    """Exchange client credentials for an access token."""
    global _token, _token_time, _token_expires_in

    client_id, client_secret_value = _get_credentials()
    urls = get_urls()
    auth_url = urls["platform_base"] + AUTH_PATH

    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret_value,
    }).encode("utf-8")

    req = urllib.request.Request(
        auth_url,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=30)
        body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise AuthError(f"Auth failed ({e.code}): {raw}")
    except Exception as e:
        raise AuthError(f"Auth request failed: {e}")

    _token = body["access_token"]
    _token_time = time.time()
    _token_expires_in = body.get("expires_in", 600)
    return _token


def ensure_token() -> str:
    """Return a valid token, refreshing if needed."""
    global _token, _token_time
    if mock_mode():
        return "mock-token"
    if _token is None or (time.time() - _token_time) >= TOKEN_LIFETIME:
        return authenticate()
    return _token


def get_token_info() -> dict:
    """Return token status for diagnostics."""
    if _token is None:
        return {"status": "disconnected", "token": None, "age_seconds": 0, "expires_in": 0}

    age = time.time() - _token_time
    remaining = max(0, _token_expires_in - age)
    return {
        "status": "connected" if remaining > 0 else "expired",
        "token": _token[:12] + "..." if _token else None,
        "age_seconds": round(age),
        "expires_in": round(remaining),
    }


def clear_token():
    """Clear cached token (for logout/reconnect)."""
    global _token, _token_time, _token_expires_in
    _token = None
    _token_time = 0
    _token_expires_in = 0
