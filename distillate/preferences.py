"""User preferences — tiny JSON store at ``~/.config/distillate/preferences.json``.

Intentionally minimal: two keys today (``nicolas_model`` is the only one
that matters). No schema migrations. Corrupt file → quarantine and return
defaults so the user never sees a fatal.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from distillate import config, pricing

log = logging.getLogger(__name__)

PREFERENCES_PATH: Path = config.CONFIG_DIR / "preferences.json"


def _defaults() -> dict:
    return {"nicolas_model": pricing.DEFAULT_MODEL}


def _quarantine(path: Path) -> None:
    try:
        path.rename(path.with_suffix(path.suffix + ".bak"))
    except OSError:
        log.debug("Failed to quarantine corrupt preferences file", exc_info=True)


def load() -> dict:
    """Read preferences from disk, merging onto defaults.

    Missing file → defaults. Corrupt file → defaults + quarantine (so the
    user's bad file is preserved as ``preferences.json.bak`` for debugging).
    """
    data = _defaults()
    if not PREFERENCES_PATH.exists():
        return data
    try:
        on_disk = json.loads(PREFERENCES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        log.warning("preferences.json is unreadable — quarantining and using defaults")
        _quarantine(PREFERENCES_PATH)
        return data
    if isinstance(on_disk, dict):
        data.update(on_disk)
    return data


def save(prefs: dict) -> None:
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(json.dumps(prefs, indent=2))


def get(key: str, default: Any = None) -> Any:
    data = load()
    return data.get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001 — name mirrors dict API
    data = load()
    data[key] = value
    save(data)
