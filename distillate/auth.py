# Covers: distillate/auth.py
"""Session management and HF token resolution for Distillate.

Manages the HF OAuth session (JWT + tokens stored in keychain) and implements
the two-token resolution order: manual HF_TOKEN override > OAuth access token.
"""

import logging
import os

log = logging.getLogger(__name__)

_SESSION_KEYS = (
    "DISTILLATE_SESSION_JWT",
    "DISTILLATE_USER_ID",
    "HF_OAUTH_ACCESS_TOKEN",
    "HF_OAUTH_REFRESH_TOKEN",
    "HF_OAUTH_EXPIRES_AT",
)

# Non-secret local marker to avoid re-claiming the legacy token.
_LEGACY_CLAIMED_KEY = "_LEGACY_CLAIMED"


def get_session() -> dict | None:
    """Return current session info or None if not signed in.

    Returns: {user_id, session_jwt, email, display_name, avatar_url, expires_at}
    """
    from distillate import secrets as _secrets
    jwt = _secrets.get("DISTILLATE_SESSION_JWT")
    if not jwt:
        return None
    return {
        "user_id": _secrets.get("DISTILLATE_USER_ID"),
        "session_jwt": jwt,
        "email": _secrets.get("_SESSION_EMAIL"),
        "display_name": _secrets.get("_SESSION_DISPLAY_NAME"),
        "avatar_url": _secrets.get("_SESSION_AVATAR_URL"),
        "expires_at": _secrets.get("HF_OAUTH_EXPIRES_AT"),
    }


def set_session(
    *,
    user_id: str,
    session_jwt: str,
    hf_access_token: str,
    hf_refresh_token: str | None,
    expires_at: str | None,
    email: str | None = None,
    display_name: str | None = None,
    avatar_url: str | None = None,
) -> None:
    """Persist OAuth session to keychain."""
    from distillate import secrets as _secrets
    _secrets.set("DISTILLATE_SESSION_JWT", session_jwt)
    _secrets.set("DISTILLATE_USER_ID", user_id)
    _secrets.set("HF_OAUTH_ACCESS_TOKEN", hf_access_token)
    _secrets.set("HF_OAUTH_REFRESH_TOKEN", hf_refresh_token or "")
    _secrets.set("HF_OAUTH_EXPIRES_AT", expires_at or "")
    _secrets.set("_SESSION_EMAIL", email or "")
    _secrets.set("_SESSION_DISPLAY_NAME", display_name or "")
    _secrets.set("_SESSION_AVATAR_URL", avatar_url or "")
    log.info("Session established for user %s", user_id)


def clear_session() -> None:
    """Remove session and OAuth tokens from keychain.

    Deliberately does NOT touch HF_TOKEN (manual override) or
    DISTILLATE_AUTH_TOKEN (legacy cloud sync token).
    """
    from distillate import secrets as _secrets
    for key in _SESSION_KEYS:
        _secrets.delete(key)
    for key in ("_SESSION_EMAIL", "_SESSION_DISPLAY_NAME", "_SESSION_AVATAR_URL"):
        _secrets.delete(key)
    log.info("Session cleared")


def is_signed_in() -> bool:
    """True when a valid session JWT is present in keychain."""
    from distillate import secrets as _secrets
    return bool(_secrets.get("DISTILLATE_SESSION_JWT"))


def current_user_id() -> str | None:
    """Return the current user's UUID or None if not signed in."""
    from distillate import secrets as _secrets
    return _secrets.get("DISTILLATE_USER_ID") or None


def hf_token_for(purpose: str) -> str:
    """Return the best HF token for a given purpose.

    Resolution order:
    1. Manual HF_TOKEN (advanced override — user explicitly set this)
    2. HF_OAUTH_ACCESS_TOKEN (from OAuth session)
    3. Empty string (no token available)

    purpose ∈ {"jobs", "hub", "inference"} — same logic for all, parameter
    reserved for future per-scope routing.
    """
    from distillate import secrets as _secrets
    manual = _secrets.get("HF_TOKEN")
    if manual:
        return manual
    return _secrets.get("HF_OAUTH_ACCESS_TOKEN")


def legacy_claimed() -> bool:
    """True if the legacy DISTILLATE_AUTH_TOKEN has already been claimed."""
    from distillate import secrets as _secrets
    return bool(_secrets.get(_LEGACY_CLAIMED_KEY))


def mark_legacy_claimed() -> None:
    from distillate import secrets as _secrets
    _secrets.set(_LEGACY_CLAIMED_KEY, "1")


def refresh_hf_token_if_expired() -> None:
    """If HF_OAUTH_EXPIRES_AT is in the past, request a token refresh from the Worker."""
    from distillate import secrets as _secrets
    expires_at_str = _secrets.get("HF_OAUTH_EXPIRES_AT")
    if not expires_at_str:
        return

    import datetime
    try:
        expires_at = datetime.datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        if expires_at > datetime.datetime.now(datetime.timezone.utc):
            return  # Not expired yet
    except ValueError:
        return

    jwt = _secrets.get("DISTILLATE_SESSION_JWT")
    cloud_url = os.environ.get("DISTILLATE_CLOUD_URL", "https://api.distillate.dev").rstrip("/")
    if not jwt:
        return

    try:
        import requests
        resp = requests.post(
            f"{cloud_url}/auth/refresh-hf",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=15,
        )
        if resp.ok:
            data = resp.json()
            _secrets.set("HF_OAUTH_ACCESS_TOKEN", data.get("hf_access_token", ""))
            if data.get("hf_refresh_token"):
                _secrets.set("HF_OAUTH_REFRESH_TOKEN", data["hf_refresh_token"])
            if data.get("expires_at"):
                _secrets.set("HF_OAUTH_EXPIRES_AT", data["expires_at"])
            log.info("HF OAuth token refreshed")
        else:
            log.warning("HF token refresh failed: %d", resp.status_code)
    except Exception:
        log.debug("HF token refresh request failed (non-critical)", exc_info=True)
