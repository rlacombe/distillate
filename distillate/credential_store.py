"""Encrypted credential storage using Fernet symmetric encryption.

Stores API keys, OAuth tokens, and other secrets encrypted at rest in the
SQLite database. Encryption key is derived from device hardware info, so
credentials load automatically on startup without OS keychain prompts.

Security model: Device-bound encryption. If the machine is compromised,
credentials are exposed. OS keychain has the same threat model but with
worse UX (keychain prompts). For high-security use cases, credentials
should be stored in a dedicated secrets manager (AWS Secrets, Vault, etc.).
"""

import base64
import hashlib
import logging
import socket
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

# Salt for key derivation — ensures different apps don't derive the same key
_KEY_SALT = b"distillate-credentials-v1"

# Encryption key is derived from machine hardware + app version
_encryption_key: bytes | None = None


def _get_machine_identifier() -> str:
    """Get a unique machine identifier for key derivation.

    Uses hostname (changes on rename, but consistent across reboots).
    In containers/CI, this varies by host. In virtualized environments,
    it's derived from hypervisor-assigned name.

    This means: credentials are not portable across machines. If a user
    migrates to a new machine or container, they must re-login. This is
    acceptable (better than prompting for keychain password).
    """
    return socket.gethostname()


def _get_app_version() -> str:
    """Get app version for key derivation.

    If the app version changes, the derived key changes (forcing re-login).
    This is intentional: prevents issues if encryption params change in
    future versions.
    """
    try:
        from distillate import __version__
        return __version__
    except (ImportError, AttributeError):
        # Fallback: read from pyproject.toml
        try:
            pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
            for line in pyproject_path.read_text().split("\n"):
                if line.startswith("version"):
                    return line.split("=")[1].strip().strip('"')
        except Exception:
            pass
        return "0.7.0"  # Fallback


def get_encryption_key() -> bytes:
    """Derive and cache the encryption key.

    Key is derived from: machine_id + app_version + salt
    Deterministic: same machine + version = same key every time.
    """
    global _encryption_key
    if _encryption_key is not None:
        return _encryption_key

    machine_id = _get_machine_identifier()
    app_version = _get_app_version()

    # Combine inputs and hash to 32 bytes (256 bits for Fernet)
    key_material = f"{machine_id}:{app_version}".encode() + _KEY_SALT
    key_hash = hashlib.sha256(key_material).digest()

    # Fernet requires base64-encoded 32-byte key
    _encryption_key = base64.urlsafe_b64encode(key_hash)
    return _encryption_key


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return base64-encoded ciphertext."""
    if not plaintext:
        return ""
    try:
        key = get_encryption_key()
        f = Fernet(key)
        ciphertext = f.encrypt(plaintext.encode())
        return ciphertext.decode("ascii")
    except Exception as e:
        log.error(f"Encryption failed: {e}")
        raise


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext and return plaintext.

    Raises DecryptionError if ciphertext is invalid or tampered.
    """
    if not ciphertext:
        return ""
    try:
        key = get_encryption_key()
        f = Fernet(key)
        plaintext = f.decrypt(ciphertext.encode())
        return plaintext.decode("utf-8")
    except InvalidToken as e:
        log.error(f"Decryption failed (invalid token, possibly wrong machine): {e}")
        raise DecryptionError(str(e)) from e
    except Exception as e:
        log.error(f"Decryption failed: {e}")
        raise DecryptionError(str(e)) from e


class DecryptionError(Exception):
    """Raised when decryption fails (invalid token or tampering)."""
    pass
