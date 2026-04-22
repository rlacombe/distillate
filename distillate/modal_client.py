"""Modal compute provider helpers — auth detection, App naming, lifecycle.

Modal (modal.com) is a serverless platform for running GPU workloads
billed by the second. This module is the *backend-side* glue: it answers
"is the user authed?", "what App name corresponds to this experiment?",
and "stop this App now".

The experimentalist agent itself invokes Modal via the ``modal`` CLI
from its own training script (which has ``@app.function`` decorators);
the agent does not import this module. Distillate's server uses these
helpers for the integrations panel (auth detection), per-experiment
budget watchers (spend polling — see ``get_spend_usd`` TODO), and
budget enforcement (App stop).

Modal CLI install: ``pip install modal && modal setup``.
Token storage: ``~/.modal.toml``.
"""

from __future__ import annotations

import logging
import subprocess
import tomllib
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_TOKEN_PATH = Path.home() / ".modal.toml"

# Per the experiment-repo naming convention (`distillate-xp-` prefix),
# Modal Apps share the same identifier so dashboard and repo line up.
_APP_PREFIX = "distillate-xp-"


def app_name_for(experiment_id: str) -> str:
    """Return the Modal App name for a given experiment ID.

    Experiment IDs are already slug-safe. If the ID is already prefixed
    with ``distillate-xp-`` (e.g. when reading back the App name), return
    it unchanged rather than double-prefixing.
    """
    if experiment_id.startswith(_APP_PREFIX):
        return experiment_id
    return f"{_APP_PREFIX}{experiment_id}"


def is_authed(token_path: Path | None = None) -> bool:
    """Return True if the user has a usable Modal CLI token on disk.

    Modal stores tokens in ``~/.modal.toml`` after ``modal setup``. We
    check that the file parses as TOML, has a ``[default]`` section, and
    that section has both ``token_id`` and ``token_secret`` set.
    """
    path = token_path if token_path is not None else _DEFAULT_TOKEN_PATH
    if not path.is_file():
        return False
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    default = data.get("default") if isinstance(data, dict) else None
    if not isinstance(default, dict):
        return False
    return bool(default.get("token_id")) and bool(default.get("token_secret"))


def stop_app(app_name: str) -> bool:
    """Stop a running Modal App by name. Returns True on success.

    Used by the per-experiment budget watcher when spend hits the cap.
    Tolerates the ``modal`` CLI being missing (returns False) so callers
    can surface the failure rather than crash.
    """
    try:
        result = subprocess.run(
            ["modal", "app", "stop", app_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        log.warning("modal CLI not on PATH — cannot stop App %s", app_name)
        return False
    except subprocess.TimeoutExpired:
        log.warning("modal app stop timed out for %s", app_name)
        return False
    if result.returncode != 0:
        log.warning(
            "modal app stop failed for %s: %s",
            app_name, result.stderr.strip(),
        )
        return False
    return True
