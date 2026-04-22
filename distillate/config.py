import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from distillate import secrets as _secrets

# Config directory: respects XDG_CONFIG_HOME, overridable with DISTILLATE_CONFIG_DIR
CONFIG_DIR = Path(
    os.environ.get("DISTILLATE_CONFIG_DIR", "")
    or (
        Path(os.environ.get("XDG_CONFIG_HOME", "") or Path.home() / ".config")
        / "distillate"
    )
)
CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

# .env file: prefer CWD (for dev installs), then config dir
ENV_PATH = CONFIG_DIR / ".env"
if not ENV_PATH.exists() and (Path.cwd() / ".env").exists():
    ENV_PATH = Path.cwd() / ".env"

load_dotenv(ENV_PATH)


def _require(var: str) -> str:
    value = _secrets.get(var) if var in _secrets.SECRET_KEYS else os.environ.get(var, "").strip()
    if not value or value.startswith("your_"):
        if not ENV_PATH.exists():
            print("Error: No config found. Run 'distillate --init' to get started.")
        else:
            print(f"Error: {var} is not set. Fill it in {ENV_PATH}")
        sys.exit(1)
    return value


def save_to_env(key: str, value: str) -> None:
    """Update a single key in the .env file, preserving all other content.

    Raises ``ValueError`` if *key* is a secret — use ``secrets.set()`` instead.
    """
    if key in _secrets.SECRET_KEYS:
        raise ValueError(
            f"{key} is a secret — use distillate.secrets.set() instead of save_to_env()"
        )
    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8")
    else:
        text = ""

    pattern = rf"^{re.escape(key)}=.*$"
    replacement = f"{key}={value}"

    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{replacement}\n"

    ENV_PATH.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(ENV_PATH, 0o600)
    os.environ[key] = value


# Required — loaded lazily via ensure_loaded(), called at start of main()
ZOTERO_API_KEY: str = ""
ZOTERO_USER_ID: str = ""

# Optional — reMarkable token is set later via --register
REMARKABLE_DEVICE_TOKEN: str = _secrets.get("REMARKABLE_DEVICE_TOKEN")

# Configurable with defaults
RM_FOLDER_PAPERS: str = os.environ.get("RM_FOLDER_PAPERS", "Distillate").strip()
RM_FOLDER_INBOX: str = os.environ.get("RM_FOLDER_INBOX", "Distillate/Inbox").strip()
RM_FOLDER_READ: str = os.environ.get("RM_FOLDER_READ", "Distillate/Read").strip()
RM_FOLDER_SAVED: str = os.environ.get("RM_FOLDER_SAVED", "Distillate/Saved").strip()

ZOTERO_TAG_INBOX: str = os.environ.get("ZOTERO_TAG_INBOX", "inbox").strip()
ZOTERO_TAG_READ: str = os.environ.get("ZOTERO_TAG_READ", "read").strip()
ZOTERO_COLLECTION_KEY: str = os.environ.get("ZOTERO_COLLECTION_KEY", "").strip()


OBSIDIAN_VAULT_PATH: str = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
OBSIDIAN_PAPERS_FOLDER: str = os.environ.get("OBSIDIAN_PAPERS_FOLDER", "Distillate").strip()
OBSIDIAN_VAULT_NAME: str = (
    os.environ.get("OBSIDIAN_VAULT_NAME", "").strip()
    or (Path(OBSIDIAN_VAULT_PATH).name if OBSIDIAN_VAULT_PATH else "")
)
OUTPUT_PATH: str = os.environ.get("OUTPUT_PATH", "").strip()

PDF_SUBFOLDER: str = os.environ.get("PDF_SUBFOLDER", "pdf").strip()

# Distillate home folder — stores PDFs outside the Obsidian vault.
# Default is empty (falls back to vault-embedded PDFs); wizard suggests ~/Distillate.
DISTILLATE_HOME: str = os.environ.get("DISTILLATE_HOME", "").strip()

KEEP_ZOTERO_PDF: bool = os.environ.get("KEEP_ZOTERO_PDF", "true").strip().lower() in ("true", "1", "yes")

# Reading surface: "zotero" (default, any device via Zotero app) or "remarkable".
# reMarkable is now an optional integration — install via:
#   pip install "distillate[remarkable]"
READING_SOURCE: str = os.environ.get("READING_SOURCE", "zotero").strip().lower()

# When using Zotero reader, default SYNC_HIGHLIGHTS to false (highlights already in Zotero)
_sync_default = "false" if READING_SOURCE == "zotero" else "true"
SYNC_HIGHLIGHTS: bool = os.environ.get("SYNC_HIGHLIGHTS", _sync_default).strip().lower() in ("true", "1", "yes")


def is_zotero_reader() -> bool:
    """True when the user reads on any device via Zotero app (no reMarkable)."""
    return READING_SOURCE == "zotero"

ANTHROPIC_API_KEY: str = _secrets.get("ANTHROPIC_API_KEY")

# HuggingFace — inference providers, jobs compute, Hub storage
HF_TOKEN: str = _secrets.get("HF_TOKEN")
HF_INFERENCE_ROUTING: str = os.environ.get("HF_INFERENCE_ROUTING", ":fastest").strip()
HF_COMPUTE_ENABLED: bool = os.environ.get("HF_COMPUTE_ENABLED", "false").strip().lower() in ("true", "1", "yes")
HF_DEFAULT_GPU_FLAVOR: str = os.environ.get("HF_DEFAULT_GPU_FLAVOR", "a100-large").strip()
HF_STORAGE_BUCKET: str = os.environ.get("HF_STORAGE_BUCKET", "").strip()
HF_NAMESPACE: str = os.environ.get("HF_NAMESPACE", "").strip()

# Cloud backend (optional — used by desktop app for managed AI)
DISTILLATE_AUTH_TOKEN: str = _secrets.get("DISTILLATE_AUTH_TOKEN")
DISTILLATE_API_URL: str = os.environ.get("DISTILLATE_API_URL", "").strip()

RESEND_API_KEY: str = _secrets.get("RESEND_API_KEY")
DIGEST_FROM: str = os.environ.get("DIGEST_FROM", "onboarding@resend.dev").strip()
DIGEST_TO: str = os.environ.get("DIGEST_TO", "").strip()

ZOTERO_WEBDAV_URL: str = os.environ.get("ZOTERO_WEBDAV_URL", "").strip().rstrip("/")
ZOTERO_WEBDAV_USERNAME: str = os.environ.get("ZOTERO_WEBDAV_USERNAME", "").strip()
ZOTERO_WEBDAV_PASSWORD: str = _secrets.get("ZOTERO_WEBDAV_PASSWORD")

STATE_GIST_ID: str = os.environ.get("STATE_GIST_ID", "").strip()

# Experiments
EXPERIMENTS_ROOT: str = os.environ.get("EXPERIMENTS_ROOT", "").strip()
EXPERIMENTS_ENABLED: bool = os.environ.get("EXPERIMENTS_ENABLED", "false").strip().lower() in ("true", "1", "yes")

HTTP_TIMEOUT: int = int(os.environ.get("HTTP_TIMEOUT", "30"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
VERBOSE: bool = False
CLAUDE_FAST_MODEL: str = os.environ.get("CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001").strip()
CLAUDE_SMART_MODEL: str = os.environ.get("CLAUDE_SMART_MODEL", "claude-sonnet-4-5-20250929").strip()
CLAUDE_AGENT_MODEL: str = os.environ.get("CLAUDE_AGENT_MODEL", "claude-haiku-4-5-20251001").strip()

# When true, strip ANTHROPIC_API_KEY from the Claude Code subprocess env
# so Nicolas's main loop bills to the user's Claude Code subscription
# (Pro/Max) instead of the Anthropic Console. The parent process keeps
# the key for lab_repl sub-calls. Requires `claude login` on the host.
NICOLAS_USE_SUBSCRIPTION: bool = os.environ.get(
    "DISTILLATE_NICOLAS_USE_SUBSCRIPTION", "true"
).strip().lower() in ("true", "1", "yes")


_loaded = False


def ensure_loaded(*, required: bool = True) -> None:
    """Validate config vars. Call at the start of main().

    When ``required=False`` (used by the desktop server), missing Zotero
    credentials are logged as warnings instead of calling ``sys.exit(1)``.
    This lets the app start without a paper library configured.
    """
    global _loaded, ZOTERO_API_KEY, ZOTERO_USER_ID
    if _loaded:
        return
    _loaded = True

    # Migrate secrets from .env to OS keychain on first run after upgrade.
    _secrets.migrate_from_env()

    if required:
        ZOTERO_API_KEY = _require("ZOTERO_API_KEY")
        ZOTERO_USER_ID = _require("ZOTERO_USER_ID")
    else:
        log = logging.getLogger(__name__)
        ZOTERO_API_KEY = _secrets.get("ZOTERO_API_KEY")
        ZOTERO_USER_ID = _secrets.get("ZOTERO_USER_ID")
        if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
            log.warning(
                "Zotero credentials not configured — paper library disabled. "
                "Run 'distillate --init' or set ZOTERO_API_KEY and ZOTERO_USER_ID."
            )

    _validate_optional()


def _validate_optional() -> None:
    """Print warnings for common misconfigurations. Never exits."""
    log = logging.getLogger(__name__)

    if OBSIDIAN_VAULT_PATH and not Path(OBSIDIAN_VAULT_PATH).is_dir():
        log.warning("OBSIDIAN_VAULT_PATH does not exist: %s", OBSIDIAN_VAULT_PATH)

    if OUTPUT_PATH and not Path(OUTPUT_PATH).is_dir():
        log.warning("OUTPUT_PATH does not exist: %s", OUTPUT_PATH)

    # ANTHROPIC_API_KEY is optional — used only by the sync pipeline
    # (summaries, renderer, experiment enrichment). No prefix validation.

    if RESEND_API_KEY and not RESEND_API_KEY.startswith("re_"):
        log.warning(
            "RESEND_API_KEY doesn't look like a valid key (expected 're_' prefix)"
        )

    if EXPERIMENTS_ROOT and not Path(EXPERIMENTS_ROOT).is_dir():
        log.warning("EXPERIMENTS_ROOT does not exist: %s", EXPERIMENTS_ROOT)

    if DISTILLATE_HOME and not Path(DISTILLATE_HOME).is_dir():
        log.warning("DISTILLATE_HOME does not exist: %s", DISTILLATE_HOME)


DB_PATH = CONFIG_DIR / "state.db"
LOG_FILE = CONFIG_DIR / "distillate.log"
NICOLAS_SESSIONS_FILE = CONFIG_DIR / "nicolas_sessions.json"


_logging_configured = False


def setup_logging() -> None:
    """Configure logging for the workflow. Call once at each entry point.

    When stdout is a TTY and LOG_LEVEL != DEBUG, console shows only warnings
    while full INFO logging goes to a file. In non-TTY (cron/launchd) or
    DEBUG mode, everything goes to console as before.
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    level = getattr(logging, LOG_LEVEL, logging.INFO)

    if sys.stdout.isatty() and LOG_LEVEL != "DEBUG":
        # TTY: console gets warnings only (INFO with --verbose), file gets everything
        root = logging.getLogger()
        root.setLevel(level)

        console = logging.StreamHandler()
        console.setLevel(logging.INFO if VERBOSE else logging.WARNING)
        console.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console)

        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(file_handler)
    else:
        # Non-TTY or DEBUG: everything to console
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
