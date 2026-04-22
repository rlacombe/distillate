"""Secret management for Distillate.

Stores API keys and tokens encrypted in the SQLite database using Fernet
symmetric encryption. Encryption key is derived from machine hardware info,
so credentials are decrypted automatically at startup without OS keychain
prompts or user passwords.

Credentials are loaded into memory once at server startup and kept there
for the session. This eliminates the poor UX of "Allow keychain access?"
dialogs while maintaining security through encryption at rest.
"""

import logging
import os

log = logging.getLogger(__name__)

SECRET_KEYS: frozenset[str] = frozenset({
    "ZOTERO_API_KEY",
    "ZOTERO_USER_ID",
    "REMARKABLE_DEVICE_TOKEN",
    "ANTHROPIC_API_KEY",
    "HF_TOKEN",
    "DISTILLATE_AUTH_TOKEN",
    "RESEND_API_KEY",
    "ZOTERO_WEBDAV_PASSWORD",
    # HF OAuth session (L1+L2)
    "DISTILLATE_SESSION_JWT",
    "DISTILLATE_USER_ID",
    "HF_OAUTH_ACCESS_TOKEN",
    "HF_OAUTH_REFRESH_TOKEN",
    "HF_OAUTH_EXPIRES_AT",
    "_SESSION_EMAIL",
    "_SESSION_DISPLAY_NAME",
    "_SESSION_AVATAR_URL",
    "_LEGACY_CLAIMED",
})

# In-memory cache — populated on first access per key, invalidated by set/delete.
_cache: dict[str, str | None] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(key: str) -> str:
    """Return a secret value, or empty string if not set.

    Reads from encrypted database (loaded once at startup), then env fallback.
    Caches in-memory after first read. Also updates ``os.environ`` so downstream
    code that reads env vars directly (e.g. third-party libraries) still works.
    """
    if key in _cache:
        return _cache[key] or ""

    value: str | None = None

    # Try encrypted credential store
    try:
        from distillate import credential_store, db
        encrypted = db.get_credential(key)
        if encrypted:
            try:
                value = credential_store.decrypt(encrypted)
            except credential_store.DecryptionError:
                log.error(f"Failed to decrypt credential {key}")
                value = None
    except Exception as e:
        log.debug(f"Credential store lookup failed for {key}: {e}")
        value = None

    # Fallback: try env var
    if not value:
        value = os.environ.get(key, "").strip() or None

    _cache[key] = value

    # Keep os.environ in sync so libraries that check it work.
    if value:
        os.environ[key] = value

    return value or ""


def set(key: str, value: str) -> None:  # noqa: A001
    """Store a secret. Updates encrypted database, os.environ, and the in-memory cache."""
    _cache[key] = value or None
    os.environ[key] = value

    # Save encrypted to database
    try:
        from distillate import credential_store, db
        if value:
            encrypted = credential_store.encrypt(value)
            db.set_credential(key, encrypted, source="app")
        else:
            # Clearing: delete from encrypted store
            db.delete_credential(key)
    except Exception as exc:
        log.warning(f"Failed to write {key} to encrypted credential store: {exc}")


def delete(key: str) -> None:
    """Remove a secret from database, cache, and os.environ."""
    _cache.pop(key, None)
    os.environ.pop(key, None)

    try:
        from distillate import db
        db.delete_credential(key)
    except Exception as e:
        log.debug(f"Failed to delete credential {key}: {e}")


# ---------------------------------------------------------------------------
# Backward compatibility stubs
# ---------------------------------------------------------------------------


def using_keyring() -> bool:
    """Stub for backward compat. Always False (we use encrypted DB instead)."""
    return False


def migrate_from_env(*args, **kwargs) -> int:
    """Stub for backward compat. No longer needed (credentials encrypted in DB)."""
    return 0
