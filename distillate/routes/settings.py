"""Settings — configuration, integrations, sync, state export/import."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from distillate.routes import _context

log = logging.getLogger(__name__)

router = APIRouter()

from distillate import secrets as _secrets

# Keys that can be saved via the generic POST /settings/env endpoint.
# Secret keys are routed through the keyring; non-secret config through .env.
_ENV_ALLOWLIST = {
    "RUNPOD_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN",
    "OPENAI_API_KEY", "GOOGLE_API_KEY",
}

_version_cache: dict = {}


@router.post("/sync")
async def sync_to_cloud():
    _state = _context._state
    from distillate.cloud_sync import cloud_sync_available, sync_state
    if not cloud_sync_available():
        return JSONResponse({"ok": False, "reason": "no_credentials"})
    _state.reload()
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(_context._executor, sync_state, _state)
    return JSONResponse({"ok": ok})


@router.post("/library/setup")
async def library_setup(body: dict):
    """Validate Zotero credentials, save config, and optionally set reading surface."""
    from distillate import config
    from distillate.config import save_to_env

    api_key = body.get("zotero_api_key", "").strip()
    user_id = body.get("zotero_user_id", "").strip()
    reading_source = body.get("reading_source", "").strip().lower()

    if not api_key or not user_id:
        return JSONResponse(
            {"ok": False, "reason": "Both zotero_api_key and zotero_user_id are required"},
            status_code=400,
        )

    # Validate credentials against Zotero API
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            f"https://api.zotero.org/users/{user_id}/items?limit=1",
            headers={
                "Zotero-API-Version": "3",
                "Zotero-API-Key": api_key,
            },
        )
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            _context._executor,
            lambda: urllib.request.urlopen(req, timeout=10),
        )
        if resp.status != 200:
            return JSONResponse(
                {"ok": False, "reason": f"Zotero API returned {resp.status}"},
                status_code=422,
            )
    except urllib.error.HTTPError as e:
        reason = "Invalid API key" if e.code == 403 else f"Zotero API error ({e.code})"
        return JSONResponse({"ok": False, "reason": reason}, status_code=422)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "reason": f"Could not reach Zotero: {e}"},
            status_code=502,
        )

    # Save credentials to keyring
    _secrets.set("ZOTERO_API_KEY", api_key)
    _secrets.set("ZOTERO_USER_ID", user_id)

    # Update in-memory config
    config.ZOTERO_API_KEY = api_key
    config.ZOTERO_USER_ID = user_id

    if reading_source in ("remarkable", "zotero"):
        save_to_env("READING_SOURCE", reading_source)
        config.READING_SOURCE = reading_source
        if reading_source == "zotero":
            save_to_env("SYNC_HIGHLIGHTS", "false")
            config.SYNC_HIGHLIGHTS = False
        else:
            save_to_env("SYNC_HIGHLIGHTS", "true")
            config.SYNC_HIGHLIGHTS = True

    return JSONResponse({"ok": True, "message": "Library configured successfully"})


@router.post("/email/register")
async def register_email(body: dict):
    """Register email for notifications (experiment reports, digests)."""
    _state = _context._state
    from distillate.config import save_to_env

    email = body.get("email", "").strip()

    if not email or "@" not in email:
        return JSONResponse({"ok": False, "reason": "Valid email required"}, status_code=400)

    save_to_env("DISTILLATE_EMAIL", email)

    # Save independent preferences
    if "experiment_reports" in body:
        save_to_env("DISTILLATE_EMAIL_EXPERIMENT_REPORTS", "true" if body["experiment_reports"] else "false")
    if "daily_papers" in body:
        save_to_env("DISTILLATE_EMAIL_DAILY_PAPERS", "true" if body["daily_papers"] else "false")
    if "weekly_digest" in body:
        save_to_env("DISTILLATE_EMAIL_WEEKLY_DIGEST", "true" if body["weekly_digest"] else "false")

    # Sync snapshot to cloud
    try:
        from distillate.cloud_email import sync_snapshot
        _state.reload()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_context._executor, lambda: sync_snapshot(_state))
        if result and result.get("ok"):
            verified = result.get("verified", False)
            save_to_env("DISTILLATE_EMAIL_VERIFIED", "true" if verified else "false")
            return JSONResponse({"ok": True, "verified": verified, "message": "Email registered and synced"})
    except Exception as e:
        log.debug("Cloud sync failed: %s", e)

    return JSONResponse({"ok": True, "verified": False, "message": "Email saved locally"})


@router.post("/email/resend-verification")
async def resend_verification():
    """Re-trigger verification email via cloud."""
    _state = _context._state
    import os as _os
    from distillate.cloud_email import sync_snapshot
    email = _os.environ.get("DISTILLATE_EMAIL", "").strip()
    if not email:
        return JSONResponse({"ok": False, "reason": "No email configured"}, status_code=400)
    try:
        _state.reload()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_context._executor, lambda: sync_snapshot(_state, resend_verification=True))
        if result and result.get("ok"):
            return JSONResponse({"ok": True, "message": "Verification email sent"})
    except Exception as e:
        log.debug("Resend verification failed: %s", e)
    return JSONResponse({"ok": False, "reason": "Failed to send verification email"}, status_code=500)


@router.get("/experiments/templates")
async def list_experiment_templates():
    """List available experiment templates."""
    from distillate.launcher import list_templates

    templates = list_templates()
    return JSONResponse({
        "ok": True,
        "templates": [
            {
                "name": t["name"],
                "has_data": t["has_data"],
                "prompt_lines": t["prompt_lines"],
            }
            for t in templates
        ],
    })


@router.post("/settings/env")
async def save_env_var(body: dict):
    """Save an environment variable. Body: {"key": "...", "value": "..."}

    Secret keys go to the OS keychain; non-secret config goes to .env.
    """
    import os
    from distillate import config
    key = body.get("key", "").strip()
    value = body.get("value", "").strip()
    if not key or key not in _ENV_ALLOWLIST:
        return JSONResponse(
            {"ok": False, "reason": f"Key '{key}' not in allowlist"},
            status_code=400,
        )
    if key in _secrets.SECRET_KEYS:
        _secrets.set(key, value)
    else:
        config.save_to_env(key, value)
        os.environ[key] = value
    return JSONResponse({"ok": True})


@router.post("/huggingface/setup")
async def setup_huggingface(body: dict):
    """Validate and save a HuggingFace token.

    Body: {"token": "hf_..."}
    Returns account info on success (username, plan, orgs).
    """
    import os
    from distillate import config
    from distillate.huggingface import validate_token

    token = body.get("token", "").strip()
    if not token:
        return JSONResponse(
            {"ok": False, "reason": "Token is required"},
            status_code=400,
        )

    # Validate against HF API
    result = validate_token(token)
    if not result["ok"]:
        return JSONResponse(
            {"ok": False, "reason": result.get("error", "Invalid token")},
            status_code=401,
        )

    # Save to keyring and update in-memory config
    _secrets.set("HF_TOKEN", token)
    config.HF_TOKEN = token

    return JSONResponse({
        "ok": True,
        "username": result.get("username", ""),
        "fullname": result.get("fullname", ""),
        "plan": result.get("plan", "free"),
        "orgs": result.get("orgs", []),
        "can_pay": result.get("can_pay", False),
        "token_name": result.get("token_name", ""),
        "message": f"Connected as {result.get('username', 'unknown')}",
    })


@router.delete("/huggingface/setup")
async def disconnect_huggingface():
    """Remove HuggingFace token."""
    import os
    from distillate import config

    _secrets.delete("HF_TOKEN")
    config.HF_TOKEN = ""
    return JSONResponse({"ok": True, "message": "HuggingFace disconnected"})


@router.get("/integrations")
async def list_integrations():
    """Return all integrations grouped: library, compute, agents."""
    _state = _context._state
    import json as _json
    import os as _os
    import shutil

    from distillate import config

    # Build connector list (inlined from list_connectors)
    connectors = []

    connectors.append({
        "id": "zotero",
        "label": "Papers",
        "service": "Zotero library",
        "connected": bool(config.ZOTERO_API_KEY and config.ZOTERO_USER_ID),
        "setup": "library",
    })

    email = _os.environ.get("DISTILLATE_EMAIL", "").strip()
    connectors.append({
        "id": "email",
        "label": "Updates",
        "service": "Email",
        "connected": bool(email),
        "detail": None,
        "setup": "email",
    })

    has_obsidian = bool(config.OBSIDIAN_VAULT_PATH)
    connectors.append({
        "id": "obsidian",
        "label": "Notes",
        "service": "Obsidian vault",
        "connected": has_obsidian,
        "detail": None,
        "setup": "obsidian",
        "icon": "obsidian",
        # Vault context for building obsidian:// URIs from the renderer.
        # Empty when no vault is configured — all "Open in Obsidian" UI
        # degrades silently to nothing.
        "vault_name": config.OBSIDIAN_VAULT_NAME if has_obsidian else "",
        "papers_folder": config.OBSIDIAN_PAPERS_FOLDER if has_obsidian else "",
    })

    has_rmapi = shutil.which("rmapi") is not None
    has_token = bool(config.REMARKABLE_DEVICE_TOKEN)
    if config.READING_SOURCE == "remarkable" or has_rmapi:
        connectors.append({
            "id": "remarkable",
            "label": "Tablet",
            "service": "reMarkable",
            "connected": has_rmapi and has_token,
            "setup": "remarkable",
        })

    from distillate import auth as _auth
    hf_token = _auth.hf_token_for("jobs")
    connectors.append({
        "id": "huggingface",
        "label": "Hugging Face",
        "service": "Models, compute & inference",
        "connected": bool(hf_token),
        "detail": "Also powers HF Jobs compute" if hf_token else None,
        "setup": "huggingface",
    })

    library = connectors

    from distillate.agents import available_agents, detect_local_compute
    agents = available_agents(_state)

    compute = [detect_local_compute()]
    compute.append({
        "id": "hfjobs",
        "label": "Hugging Face Jobs",
        "detail": "A100, H200, L40S \u00b7 connected via HF" if hf_token else "",
        "connected": bool(hf_token),
        "provider": "hfjobs",
        "setup": "huggingface",
    })

    return JSONResponse({
        "ok": True,
        "library": library,
        "compute": compute,
        "agents": agents,
    })


@router.get("/compute/hfjobs/flavors")
async def hfjobs_flavors():
    """Return GPU pricing table for HuggingFace Jobs compute."""
    from distillate.compute_hfjobs import GPU_COST_PER_HOUR

    _VRAM_GB = {
        "t4-small": 16, "t4-medium": 16,
        "l4x1": 24, "l4x4": 96,
        "l40sx1": 48, "l40sx4": 192, "l40sx8": 384,
        "a10g-small": 24, "a10g-large": 24, "a10g-largex2": 48, "a10g-largex4": 96,
        "a100-large": 80, "a100x4": 320, "a100x8": 640,
        "h200": 141, "h200x2": 282, "h200x4": 564, "h200x8": 1128,
    }

    _KEY_FLAVORS = [
        {"id": "t4-small", "label": "T4"},
        {"id": "l4x1", "label": "L4"},
        {"id": "l40sx1", "label": "L40S"},
        {"id": "a100-large", "label": "A100"},
        {"id": "h200", "label": "H200"},
    ]

    flavors = []
    for f in _KEY_FLAVORS:
        fid = f["id"]
        flavors.append({
            "id": fid,
            "label": f["label"],
            "vram_gb": _VRAM_GB.get(fid, 0),
            "cost_per_hour": GPU_COST_PER_HOUR.get(fid, 0.0),
        })
    return JSONResponse({"ok": True, "flavors": flavors})


@router.post("/obsidian/setup")
async def obsidian_setup(body: dict):
    """Validate and save Obsidian vault path."""
    from pathlib import Path as _Path
    from distillate import config
    from distillate.config import save_to_env

    vault_path = body.get("vault_path", "").strip()
    vault_name = body.get("vault_name", "").strip()

    if not vault_path:
        return JSONResponse({"ok": False, "reason": "vault_path is required"}, status_code=400)

    expanded = _Path(vault_path).expanduser().resolve()

    if not expanded.is_dir():
        return JSONResponse(
            {"ok": False, "reason": "Path does not exist or is not a directory"},
            status_code=422,
        )

    if not (expanded / ".obsidian").is_dir():
        return JSONResponse(
            {"ok": False, "reason": "Not an Obsidian vault — no .obsidian/ directory found"},
            status_code=422,
        )

    if not vault_name:
        vault_name = expanded.name

    vault_path_str = str(expanded)
    save_to_env("OBSIDIAN_VAULT_PATH", vault_path_str)
    save_to_env("OBSIDIAN_VAULT_NAME", vault_name)
    config.OBSIDIAN_VAULT_PATH = vault_path_str
    config.OBSIDIAN_VAULT_NAME = vault_name

    return JSONResponse({
        "ok": True,
        "vault_path": vault_path_str,
        "vault_name": vault_name,
        "papers_folder": config.OBSIDIAN_PAPERS_FOLDER,
    })


@router.post("/integrations/health")
async def integrations_health():
    """Run lightweight health checks on connected integrations."""
    import urllib.request
    import urllib.error
    from pathlib import Path as _Path
    from distillate import config

    results: dict = {}

    async def _http_check(url: str, headers: dict) -> str:
        def _req():
            req = urllib.request.Request(url, headers=headers)
            try:
                resp = urllib.request.urlopen(req, timeout=8)
                return "ok" if resp.status == 200 else "error"
            except urllib.error.HTTPError as e:
                return "expired" if e.code in (401, 403) else "error"
            except Exception:
                return "error"
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_context._executor, _req)

    # Zotero
    if config.ZOTERO_API_KEY and config.ZOTERO_USER_ID:
        results["zotero"] = await _http_check(
            f"https://api.zotero.org/users/{config.ZOTERO_USER_ID}/items?limit=1",
            {"Zotero-API-Version": "3", "Zotero-API-Key": config.ZOTERO_API_KEY},
        )

    # HuggingFace (covers both library + jobs tiles)
    from distillate import auth as _auth
    hf_token = _auth.hf_token_for("jobs")
    if hf_token:
        hf_status = await _http_check(
            "https://huggingface.co/api/whoami-v2",
            {"Authorization": f"Bearer {hf_token}"},
        )
        results["huggingface"] = hf_status
        results["hfjobs"] = hf_status

    # Obsidian — check vault path still on disk
    if config.OBSIDIAN_VAULT_PATH:
        results["obsidian"] = "ok" if _Path(config.OBSIDIAN_VAULT_PATH).is_dir() else "error"

    # Modal — verify ~/.modal.toml exists and contains a token
    modal_toml = _Path.home() / ".modal.toml"
    if modal_toml.exists():
        try:
            content = modal_toml.read_text()
            results["modal"] = "ok" if "token" in content.lower() else "error"
        except Exception:
            results["modal"] = "error"

    return JSONResponse({"ok": True, "health": results})


@router.get("/agents/pi/models")
async def list_pi_models():
    """Return available models for Pi variants."""
    from distillate.agents import PI_MODELS
    return JSONResponse({"ok": True, "models": PI_MODELS})


@router.post("/agents/pi")
async def create_pi_agent(body: dict):
    """Create a new Pi variant. Body: {"label": "...", "model": "..."}"""
    _state = _context._state
    from distillate.agents import create_pi_variant

    label = body.get("label", "").strip()
    model = body.get("model", "").strip()
    if not label or not model:
        return JSONResponse(
            {"ok": False, "reason": "label and model are required"},
            status_code=400,
        )
    variant = create_pi_variant(_state, label, model)
    return JSONResponse({"ok": True, "agent": variant})


@router.delete("/agents/pi/{variant_id}")
async def remove_pi_agent(variant_id: str):
    """Delete a Pi variant by ID."""
    _state = _context._state
    from distillate.agents import delete_pi_variant

    deleted = delete_pi_variant(_state, variant_id)
    if not deleted:
        return JSONResponse(
            {"ok": False, "reason": f"Variant '{variant_id}' not found"},
            status_code=404,
        )
    return JSONResponse({"ok": True})


@router.get("/agents")
async def list_agents():
    """Return available experiment agents (checks which CLIs are on PATH)."""
    _state = _context._state
    from distillate.agents import available_agents
    return JSONResponse({"ok": True, "agents": available_agents(_state)})


@router.post("/agents/install")
async def install_agent(body: dict):
    """Install an agent CLI via npm. Body: {"agent_id": "codex"}"""
    import subprocess

    from distillate.agents import AGENTS

    agent_id = body.get("agent_id", "")
    agent = AGENTS.get(agent_id)
    if not agent:
        return JSONResponse({"ok": False, "reason": f"Unknown agent: {agent_id}"}, status_code=400)

    install_cmd = agent.get("install", "")
    if not install_cmd:
        return JSONResponse({"ok": False, "reason": "No install command defined"}, status_code=400)

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _context._executor,
            lambda: subprocess.run(
                install_cmd.split(),
                capture_output=True, text=True, timeout=120,
            ),
        )
        if result.returncode == 0:
            return JSONResponse({
                "ok": True,
                "output": result.stdout[-2000:] if result.stdout else "Installed successfully!",
            })
        else:
            return JSONResponse({
                "ok": False,
                "reason": result.stderr[-1000:] or "Install failed",
            }, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@router.get("/version/check")
async def check_version():
    """Check PyPI for a newer version of distillate."""
    import time
    from importlib.metadata import version as get_version

    current = get_version("distillate")

    # Cache for 1 hour
    if (_version_cache.get("ts", 0) + 3600) > time.time():
        return JSONResponse({
            "ok": True,
            "current": current,
            "latest": _version_cache.get("latest", current),
            "update_available": _version_cache.get("latest", current) != current,
        })

    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request(
            "https://pypi.org/pypi/distillate/json",
            headers={"User-Agent": f"distillate/{current}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            latest = data.get("info", {}).get("version", current)
            _version_cache["latest"] = latest
            _version_cache["ts"] = time.time()
            return JSONResponse({
                "ok": True,
                "current": current,
                "latest": latest,
                "update_available": latest != current,
            })
    except Exception:
        return JSONResponse({
            "ok": True,
            "current": current,
            "latest": current,
            "update_available": False,
        })


@router.get("/state/export")
async def export_state(request: Request):
    """Return current state as JSON for backup."""
    _context._require_local_auth(request)
    from distillate.state import STATE_PATH
    if not STATE_PATH.exists():
        return JSONResponse({"ok": False, "reason": "no_state"}, status_code=404)
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return JSONResponse({"ok": True, "state": data})


@router.post("/state/import")
async def import_state(request: Request, body: dict):
    """Validate and import a state backup."""
    _state = _context._state
    _context._require_local_auth(request)
    import shutil
    from distillate.state import STATE_PATH

    state_data = body.get("state")
    if not state_data or not isinstance(state_data, dict):
        return JSONResponse({"ok": False, "reason": "invalid_body"}, status_code=400)
    if "documents" not in state_data:
        return JSONResponse({"ok": False, "reason": "missing_documents"}, status_code=400)

    # Backup existing
    if STATE_PATH.exists():
        backup = STATE_PATH.with_suffix(".json.bak")
        shutil.copy2(STATE_PATH, backup)

    STATE_PATH.write_text(
        json.dumps(state_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _state.reload()
    n_papers = len(state_data.get("documents", {}))
    return JSONResponse({"ok": True, "papers": n_papers})


@router.get("/settings")
async def get_settings():
    """Read app settings."""
    import os as _os
    from distillate import config
    return JSONResponse({
        "authToken": _secrets.get("DISTILLATE_AUTH_TOKEN"),
        "experimentsRoot": config.EXPERIMENTS_ROOT,
        "privateRepos": _os.environ.get("PRIVATE_REPOS", "").lower() in ("true", "1"),
    })


@router.post("/settings")
async def save_settings(request: Request):
    """Save app settings to .env."""
    from distillate import config
    body = await request.json()
    if "authToken" in body:
        _secrets.set("DISTILLATE_AUTH_TOKEN", body["authToken"] or "")
    if "experimentsRoot" in body:
        config.save_to_env("EXPERIMENTS_ROOT", body["experimentsRoot"] or "")
    if "privateRepos" in body:
        config.save_to_env("PRIVATE_REPOS", "true" if body["privateRepos"] else "false")
    return JSONResponse({"ok": True})


@router.post("/vault/refresh")
async def vault_refresh():
    """Regenerate vault wiki structural files, clean up legacy notebooks, and lint."""
    from distillate.lab_notebook import cleanup_legacy_notebook
    from distillate.vault_wiki import generate_schema, regenerate_index, vault_lint

    cleanup = cleanup_legacy_notebook()
    schema = generate_schema()
    index = regenerate_index()
    lint = vault_lint()

    return JSONResponse({
        "ok": True,
        "schema": str(schema) if schema else None,
        "index": str(index) if index else None,
        "cleanup": cleanup,
        "lint": lint,
    })


@router.get("/vault/lint")
async def vault_lint_endpoint():
    """Return vault health diagnostics."""
    from distillate.vault_wiki import vault_lint
    return JSONResponse(vault_lint())


@router.get("/vault/tree")
async def vault_tree():
    """Return the vault folder structure as a nested tree.

    Each node: ``{"name", "path" (relative), "type": "dir"|"file", "children"?}``.
    Only includes ``.md`` and ``.base`` files; skips PDFs, HTML, hidden files.
    """
    from distillate import config as _cfg
    if not _cfg.OBSIDIAN_VAULT_PATH:
        return JSONResponse({"ok": False, "reason": "no_vault"}, status_code=404)

    root = Path(_cfg.OBSIDIAN_VAULT_PATH) / _cfg.OBSIDIAN_PAPERS_FOLDER
    if not root.is_dir():
        return JSONResponse({"ok": False, "reason": "vault_dir_missing"}, status_code=404)

    def _walk(directory: Path, rel: str = "") -> list:
        nodes = []
        try:
            children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return nodes
        for child in children:
            if child.name.startswith("."):
                continue
            child_rel = f"{rel}/{child.name}" if rel else child.name
            if child.is_dir():
                if child.name in ("html", "pdf"):
                    continue  # skip binary asset dirs
                sub = _walk(child, child_rel)
                if sub:  # skip empty dirs
                    nodes.append({"name": child.name, "path": child_rel, "type": "dir", "children": sub})
            elif child.suffix in (".md", ".base"):
                nodes.append({"name": child.name, "path": child_rel, "type": "file"})
        return nodes

    return JSONResponse({"ok": True, "root": _cfg.OBSIDIAN_PAPERS_FOLDER, "tree": _walk(root)})


@router.get("/vault/file")
async def vault_file(path: str = ""):
    """Read a vault file and return its content.

    ``path`` is relative to ``{vault}/{papers_folder}/``.  Returns the
    raw markdown text plus minimal frontmatter fields for the header.
    """
    from distillate import config as _cfg
    if not _cfg.OBSIDIAN_VAULT_PATH or not path:
        return JSONResponse({"ok": False}, status_code=404)

    root = Path(_cfg.OBSIDIAN_VAULT_PATH) / _cfg.OBSIDIAN_PAPERS_FOLDER
    target = (root / path).resolve()

    # Path-traversal guard
    if not str(target).startswith(str(root.resolve())):
        return JSONResponse({"ok": False, "reason": "outside_vault"}, status_code=403)
    if not target.is_file():
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return JSONResponse({"ok": False, "reason": "read_error"}, status_code=500)

    return JSONResponse({"ok": True, "path": path, "content": text})
