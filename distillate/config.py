import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# Config directory: respects XDG_CONFIG_HOME, overridable with DISTILLATE_CONFIG_DIR
CONFIG_DIR = Path(
    os.environ.get("DISTILLATE_CONFIG_DIR", "")
    or (
        Path(os.environ.get("XDG_CONFIG_HOME", "") or Path.home() / ".config")
        / "distillate"
    )
)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# .env file: prefer CWD (for dev installs), then config dir
ENV_PATH = CONFIG_DIR / ".env"
if not ENV_PATH.exists() and (Path.cwd() / ".env").exists():
    ENV_PATH = Path.cwd() / ".env"

load_dotenv(ENV_PATH)


def _require(var: str) -> str:
    value = os.environ.get(var, "").strip()
    if not value or value.startswith("your_"):
        if not ENV_PATH.exists():
            print("Error: No config found. Run 'distillate --init' to get started.")
        else:
            print(f"Error: {var} is not set. Fill it in {ENV_PATH}")
        sys.exit(1)
    return value


def save_to_env(key: str, value: str) -> None:
    """Update a single key in the .env file, preserving all other content."""
    if ENV_PATH.exists():
        text = ENV_PATH.read_text()
    else:
        text = ""

    pattern = rf"^{re.escape(key)}=.*$"
    replacement = f"{key}={value}"

    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{replacement}\n"

    ENV_PATH.write_text(text)
    os.environ[key] = value


# Required — loaded lazily via ensure_loaded(), called at start of main()
ZOTERO_API_KEY: str = ""
ZOTERO_USER_ID: str = ""

# Optional — reMarkable token is set later via --register
REMARKABLE_DEVICE_TOKEN: str = os.environ.get("REMARKABLE_DEVICE_TOKEN", "").strip()

# Configurable with defaults
RM_FOLDER_PAPERS: str = os.environ.get("RM_FOLDER_PAPERS", "Distillate").strip()
RM_FOLDER_INBOX: str = os.environ.get("RM_FOLDER_INBOX", "Distillate/Inbox").strip()
RM_FOLDER_READ: str = os.environ.get("RM_FOLDER_READ", "Distillate/Read").strip()
RM_FOLDER_SAVED: str = os.environ.get("RM_FOLDER_SAVED", "Distillate/Saved").strip()

ZOTERO_TAG_INBOX: str = os.environ.get("ZOTERO_TAG_INBOX", "inbox").strip()
ZOTERO_TAG_READ: str = os.environ.get("ZOTERO_TAG_READ", "read").strip()


OBSIDIAN_VAULT_PATH: str = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
OBSIDIAN_PAPERS_FOLDER: str = os.environ.get("OBSIDIAN_PAPERS_FOLDER", "Distillate").strip()
OBSIDIAN_VAULT_NAME: str = (
    os.environ.get("OBSIDIAN_VAULT_NAME", "").strip()
    or (Path(OBSIDIAN_VAULT_PATH).name if OBSIDIAN_VAULT_PATH else "")
)
OUTPUT_PATH: str = os.environ.get("OUTPUT_PATH", "").strip()

KEEP_ZOTERO_PDF: bool = os.environ.get("KEEP_ZOTERO_PDF", "true").strip().lower() in ("true", "1", "yes")

ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "").strip()

RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "").strip()
DIGEST_FROM: str = os.environ.get("DIGEST_FROM", "onboarding@resend.dev").strip()
DIGEST_TO: str = os.environ.get("DIGEST_TO", "").strip()

STATE_GIST_ID: str = os.environ.get("STATE_GIST_ID", "").strip()

HTTP_TIMEOUT: int = int(os.environ.get("HTTP_TIMEOUT", "30"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
CLAUDE_FAST_MODEL: str = os.environ.get("CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001").strip()
CLAUDE_SMART_MODEL: str = os.environ.get("CLAUDE_SMART_MODEL", "claude-sonnet-4-5-20250929").strip()


_loaded = False


def ensure_loaded() -> None:
    """Validate required config vars. Call at the start of main()."""
    global _loaded, ZOTERO_API_KEY, ZOTERO_USER_ID
    if _loaded:
        return
    _loaded = True
    ZOTERO_API_KEY = _require("ZOTERO_API_KEY")
    ZOTERO_USER_ID = _require("ZOTERO_USER_ID")


def setup_logging() -> None:
    """Configure logging for the workflow. Call once at each entry point."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
