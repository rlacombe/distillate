# Covers: distillate/routes/auth.py
"""HF OAuth sign-in routes for the Distillate desktop server."""

import logging
import os
import secrets as _std_secrets
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from distillate import auth as _auth
from distillate import secrets as _secrets
from distillate.routes import _context

log = logging.getLogger(__name__)

router = APIRouter()

_TIMEOUT = 20  # seconds


def _cloud_url() -> str:
    return os.environ.get("DISTILLATE_CLOUD_URL", "https://api.distillate.dev").rstrip("/")


@router.get("/auth/status")
async def auth_status():
    """Return current sign-in state and user profile."""
    session = _auth.get_session()
    if session is None:
        return JSONResponse({"signed_in": False, "user": None})
    return JSONResponse({
        "signed_in": True,
        "user": {
            "user_id": session.get("user_id"),
            "email": session.get("email"),
            "display_name": session.get("display_name"),
            "avatar_url": session.get("avatar_url"),
        },
    })


@router.get("/account/usage")
async def account_usage():
    """Return token usage for the current calendar month."""
    now = datetime.now()
    try:
        from distillate.agent_runtime import usage_tracker
        snap = usage_tracker.get_tracker().snapshot()
        m = snap.get("month", {})
        return JSONResponse({
            "ok": True,
            "month": now.strftime("%B %Y").upper(),
            "tokens_input": m.get("input_tokens", 0),
            "tokens_output": m.get("output_tokens", 0),
            "tokens_cache_creation": m.get("cache_creation_tokens", 0),
            "cost_usd": m.get("cost_usd", 0.0),
        })
    except Exception:
        log.debug("account_usage: usage_tracker unavailable", exc_info=True)
        return JSONResponse({
            "ok": True,
            "month": now.strftime("%B %Y").upper(),
            "tokens_input": 0,
            "tokens_output": 0,
            "tokens_cache_creation": 0,
            "cost_usd": 0.0,
        })


@router.get("/account/stats")
async def account_stats(period: str = "30d"):
    """Detailed token usage breakdown for the stats page.

    period: "day" | "7d" | "30d" | "all"
    """
    import json
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "day":
        cutoff = today_start
        period_label = "TODAY · " + now.strftime("%B %d").upper().lstrip("0")
        daily_mode = "none"
    elif period == "7d":
        cutoff = today_start - timedelta(days=6)
        period_label = "LAST 7 DAYS"
        daily_mode = "days"
    elif period == "all":
        cutoff = datetime(1970, 1, 1, tzinfo=timezone.utc)
        period_label = "ALL TIME"
        daily_mode = "months"
    else:  # default: 30d
        cutoff = today_start - timedelta(days=29)
        period_label = "LAST 30 DAYS"
        daily_mode = "days"

    try:
        from distillate.agent_runtime.usage_tracker import USAGE_PATH
    except Exception:
        return JSONResponse({"ok": False, "reason": "tracker_unavailable"})

    _ROLE_LABELS = {
        "nicolas_turn":      "Nicolas",
        "lab_repl_subcall":  "Lab REPL",
        "experimentalist_run": "Experiments",
    }
    _MODEL_LABELS = {
        "claude-opus-4-7":            "Opus 4.7",
        "claude-opus-4-6":            "Opus 4.6",
        "claude-sonnet-4-6":          "Sonnet 4.6",
        "claude-sonnet-4-5-20250929": "Sonnet 4.5",
        "claude-haiku-4-5-20251001":  "Haiku 4.5",
        "gemini-3.1":                 "Gemini 3.1 Pro",
        "gemini-3.0":                 "Gemini 3.0 Flash",
    }

    def _empty():
        return {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0,
                "cost_usd": 0.0, "calls": 0}

    def _add(bucket, row_tokens, cost):
        bucket["input"]          += int(row_tokens.get("input_tokens") or 0)
        bucket["output"]         += int(row_tokens.get("output_tokens") or 0)
        bucket["cache_creation"] += int(row_tokens.get("cache_creation_input_tokens") or 0)
        bucket["cache_read"]     += int(row_tokens.get("cache_read_input_tokens") or 0)
        bucket["cost_usd"]       += float(cost or 0)
        bucket["calls"]          += 1

    totals = _empty()
    by_model: dict[str, dict] = {}
    by_role:  dict[str, dict] = {}
    by_day:   dict[str, dict] = {}   # "YYYY-MM-DD" → bucket

    if USAGE_PATH.exists():
        for line in USAGE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = r.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                continue

            tokens = r.get("tokens") or {}
            cost   = float(r.get("cost_usd") or 0)
            model  = r.get("model", "unknown")
            role   = r.get("role",  "unknown")
            day    = ts.strftime("%Y-%m-%d")

            _add(totals, tokens, cost)
            by_model.setdefault(model, _empty())
            _add(by_model[model], tokens, cost)
            by_role.setdefault(role, _empty())
            _add(by_role[role], tokens, cost)
            by_day.setdefault(day, _empty())
            _add(by_day[day], tokens, cost)

    # Build daily/monthly array depending on mode
    daily = []
    today_local = now.date()
    if daily_mode == "days":
        d = cutoff
        while d.date() <= today_local:
            key = d.strftime("%Y-%m-%d")
            b   = by_day.get(key, _empty())
            daily.append({
                "date":    key,
                "tokens":  b["input"] + b["cache_creation"] + b["output"],
                "cost_usd": round(b["cost_usd"], 4),
            })
            d += timedelta(days=1)
    elif daily_mode == "months":
        # Aggregate by month — emit one entry per month as "YYYY-MM-01"
        by_month: dict[str, dict] = {}
        for day_key, b in by_day.items():
            month_key = day_key[:7] + "-01"
            by_month.setdefault(month_key, _empty())
            bm = by_month[month_key]
            for k in ("input", "output", "cache_creation", "cache_read", "calls"):
                bm[k] += b[k]
            bm["cost_usd"] += b["cost_usd"]
        for mk in sorted(by_month):
            b = by_month[mk]
            daily.append({
                "date":    mk,
                "tokens":  b["input"] + b["cache_creation"] + b["output"],
                "cost_usd": round(b["cost_usd"], 4),
            })
    # daily_mode == "none": leave daily empty

    # Sort models / roles by cost desc
    models_out = sorted(
        [{"model": k, "label": _MODEL_LABELS.get(k, k), **v}
         for k, v in by_model.items()],
        key=lambda x: x["cost_usd"], reverse=True,
    )
    roles_out = [
        {"role": k, "label": _ROLE_LABELS.get(k, k), **v}
        for k, v in by_role.items()
    ]
    roles_out.sort(key=lambda x: x["cost_usd"], reverse=True)

    return JSONResponse({
        "ok":        True,
        "period":    period_label,
        "daily_mode": daily_mode,
        "totals":    {**totals, "cost_usd": round(totals["cost_usd"], 4)},
        "by_model":  models_out,
        "by_role":   roles_out,
        "daily":     daily,
    })


@router.post("/auth/signin-hf-start")
async def signin_hf_start():
    """Return the HF OAuth authorization URL for the renderer to open in the browser."""
    nonce = _std_secrets.token_urlsafe(32)
    url = f"{_cloud_url()}/oauth/hf/start?desktop_nonce={nonce}"
    return JSONResponse({"ok": True, "authorize_url": url})


@router.post("/auth/signin-hf-complete")
async def signin_hf_complete(body: dict):
    """Exchange the bootstrap nonce for a session JWT, write to keychain, claim legacy data."""
    bootstrap = body.get("bootstrap", "").strip()
    if not bootstrap:
        return JSONResponse({"ok": False, "reason": "missing bootstrap"}, status_code=400)

    try:
        resp = requests.post(
            f"{_cloud_url()}/auth/exchange",
            json={"bootstrap": bootstrap},
            timeout=_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        log.warning("Bootstrap exchange request failed: %s", exc)
        return JSONResponse({"ok": False, "reason": "network_error"}, status_code=502)

    if not resp.ok:
        log.warning("Bootstrap exchange failed: %d %s", resp.status_code, resp.text[:200])
        return JSONResponse({"ok": False, "reason": "exchange_failed"}, status_code=resp.status_code)

    data = resp.json()
    if not data.get("ok"):
        return JSONResponse({"ok": False, "reason": "exchange_rejected"}, status_code=400)

    _auth.set_session(
        user_id=data["user_id"],
        session_jwt=data["session_jwt"],
        hf_access_token=data.get("hf_access_token", ""),
        hf_refresh_token=data.get("hf_refresh_token"),
        expires_at=data.get("expires_at"),
        email=data.get("email"),
        display_name=data.get("display_name"),
        avatar_url=data.get("avatar_url"),
    )

    # Legacy-claim: migrate old opaque-token cloud data to the new OAuth user
    toast: str | None = None
    legacy_token = _secrets.get("DISTILLATE_AUTH_TOKEN")
    if legacy_token and not _auth.legacy_claimed():
        toast = _claim_legacy(data["session_jwt"], legacy_token)

    return JSONResponse({
        "ok": True,
        "user": {
            "user_id": data["user_id"],
            "email": data.get("email"),
            "display_name": data.get("display_name"),
            "avatar_url": data.get("avatar_url"),
        },
        "toast": toast,
    })


def _claim_legacy(session_jwt: str, legacy_token: str) -> str | None:
    """POST /auth/claim-legacy. Returns a toast message or None."""
    try:
        resp = requests.post(
            f"{_cloud_url()}/auth/claim-legacy",
            json={"legacy_token": legacy_token},
            headers={"Authorization": f"Bearer {session_jwt}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 409:
            log.info("Legacy token already claimed by another account")
            _auth.mark_legacy_claimed()
            return None
        if not resp.ok:
            log.warning("Legacy claim failed: %d", resp.status_code)
            return None
        data = resp.json()
        _auth.mark_legacy_claimed()
        papers = data.get("papers_migrated", 0)
        projs = data.get("projects_migrated", 0)
        if papers or projs:
            parts = []
            if papers:
                parts.append(f"{papers} paper{'s' if papers != 1 else ''}")
            if projs:
                parts.append(f"{projs} project{'s' if projs != 1 else ''}")
            return f"Synced {' and '.join(parts)} from your previous install."
        return None
    except Exception:
        log.debug("Legacy claim request failed (non-critical)", exc_info=True)
        return None


@router.post("/auth/logout")
async def auth_logout():
    """Clear session from keychain. Local files and legacy token are untouched."""
    _auth.clear_session()
    return JSONResponse({"ok": True})
