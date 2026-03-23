"""Cloud email integration — sync state and send events to Supabase.

Lightweight client that pushes snapshots and events to the Distillate cloud
for scheduled emails (daily suggestions, weekly digest) and event-driven
notifications (experiment reports).

No account creation needed — the first sync auto-registers the user.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request

from distillate import config

log = logging.getLogger(__name__)

CLOUD_URL = os.environ.get(
    "DISTILLATE_CLOUD_URL",
    "https://your-project.supabase.co/functions/v1",
)
CLOUD_ANON_KEY = os.environ.get("DISTILLATE_CLOUD_ANON_KEY", "")


def _cloud_configured() -> bool:
    """Check if cloud email is configured."""
    email = os.environ.get("DISTILLATE_EMAIL", "").strip()
    return bool(email and CLOUD_URL and "your-project" not in CLOUD_URL)


def _post(endpoint: str, data: dict, auth_token: str = "") -> dict | None:
    """POST JSON to a cloud endpoint."""
    url = f"{CLOUD_URL}/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "apikey": CLOUD_ANON_KEY,
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    body = json.dumps(data).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib_error.HTTPError as e:
        log.warning("Cloud POST %s failed: %s", endpoint, e.code)
        return None
    except Exception as e:
        log.debug("Cloud POST %s error: %s", endpoint, e)
        return None


def sync_snapshot(state, resend_verification: bool = False) -> dict | None:
    """Push current state summary to the cloud.

    Creates the user account on first call. Returns the response
    including auth_token (saved to .env for subsequent calls).
    If resend_verification=True, asks the server to re-send the verification email.
    """
    email = os.environ.get("DISTILLATE_EMAIL", "").strip()
    if not email:
        return None

    # Build snapshot from current state
    processed = state.documents_with_status("processed")
    q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
    queue = state.documents_with_status(q_status)

    # Reading tags from recent papers
    tags: dict[str, int] = {}
    for doc in processed:
        for tag in doc.get("metadata", {}).get("tags", []):
            tags[tag] = tags.get(tag, 0) + 1
    top_tags = sorted(tags, key=tags.get, reverse=True)[:10]

    # Recent highlights
    recent_highlights = []
    for doc in list(reversed(processed))[:5]:
        hl = doc.get("highlights", [])
        if isinstance(hl, list) and hl:
            recent_highlights.append(str(hl[0])[:150])

    # Experiments summary
    experiments = []
    for proj in state.projects.values():
        runs = proj.get("runs", {})
        kept = sum(1 for r in runs.values()
                   if (r.get("decision") or r.get("status", "")) == "keep")

        # Find best metric
        best = ""
        for r in runs.values():
            results = r.get("results", {})
            for k in ("accuracy", "test_accuracy", "loss", "f1"):
                if k in results:
                    best = f"{k}={results[k]}"
                    break
            if best:
                break

        sessions = proj.get("sessions", {})
        has_active = any(s.get("status") == "running" for s in sessions.values())

        experiments.append({
            "name": proj.get("name", proj.get("id", "")),
            "runs": len(runs),
            "kept": kept,
            "best_metric": best,
            "status": "running" if has_active else "paused",
        })

    # Get timezone
    try:
        tz = datetime.now().astimezone().tzinfo
        tz_name = str(tz) if tz else "UTC"
        # Try to get IANA timezone name
        import time as _time
        tz_name = _time.tzname[0] or "UTC"
        # On macOS, try to get the proper IANA name
        import subprocess
        result = subprocess.run(
            ["readlink", "/etc/localtime"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and "zoneinfo/" in result.stdout:
            tz_name = result.stdout.strip().split("zoneinfo/")[-1]
    except Exception:
        tz_name = "UTC"

    # User preferences
    preferred_hour = int(os.environ.get("DISTILLATE_EMAIL_HOUR", "7"))
    daily_papers = os.environ.get("DISTILLATE_EMAIL_DAILY_PAPERS", "true").strip().lower() in ("true", "1", "yes")
    weekly_digest = os.environ.get("DISTILLATE_EMAIL_WEEKLY_DIGEST", "true").strip().lower() in ("true", "1", "yes")
    experiment_reports = os.environ.get("DISTILLATE_EMAIL_EXPERIMENT_REPORTS", "true").strip().lower() in ("true", "1", "yes")

    data = {
        "email": email,
        "timezone": tz_name,
        "preferred_hour": preferred_hour,
        "daily_papers": daily_papers,
        "weekly_digest": weekly_digest,
        "experiment_reports": experiment_reports,
        "resend_verification": resend_verification,
        "snapshot": {
            "papers_read": len(processed),
            "papers_queued": len(queue),
            "reading_tags": top_tags,
            "recent_highlights": recent_highlights,
            "experiments": experiments,
        },
    }

    result = _post("sync-snapshot", data)
    if result and result.get("ok"):
        # Save auth token for event calls
        token = result.get("auth_token", "")
        if token:
            config.save_to_env("DISTILLATE_AUTH_TOKEN", token)
        log.info("Cloud snapshot synced for %s", email)
    return result


def send_experiment_event(
    state,
    project_name: str,
    project_id: str = "",
    runs: int = 0,
    kept: int = 0,
    best_metric: str = "",
    insight: str = "",
    github_url: str = "",
) -> bool:
    """Send an experiment completion event to the cloud for immediate email.

    Automatically pulls research insights and generates the frontier chart
    from the project's enrichment cache and run data.
    """
    import base64
    from pathlib import Path

    auth_token = os.environ.get("DISTILLATE_AUTH_TOKEN", "").strip()
    if not auth_token or not _cloud_configured():
        return False

    chart_b64 = ""
    proj = state.projects.get(project_id, {}) if project_id else {}

    # Pull research insights from enrichment cache
    if project_id and not insight:
        try:
            from distillate.experiments import load_enrichment_cache
            project_path = Path(proj.get("path", ""))
            cache = load_enrichment_cache(project_path)
            enr = cache.get("enrichment", cache)
            pi = enr.get("project", {})
            breakthrough = pi.get("key_breakthrough", "")
            lessons = pi.get("lessons_learned", [])
            if breakthrough or lessons:
                parts = []
                if breakthrough:
                    parts.append(f"**Key breakthrough:** {breakthrough}")
                if lessons:
                    parts.append("")
                    parts.append("**Lessons learned:**")
                    for i, lesson in enumerate(lessons, 1):
                        parts.append(f"{i}. {lesson}")
                insight = "\n".join(parts)
        except Exception:
            log.debug("Failed to load insights for %s", project_name, exc_info=True)

    # Generate chart PNG (no title — email header has it)
    if project_id:
        try:
            from distillate.experiments import generate_export_chart, infer_key_metric_name, _is_lower_better
            run_list = list(proj.get("runs", {}).values())
            metric = infer_key_metric_name(proj)
            if run_list and metric:
                # Auto-detect best metric from the key metric (same one shown in chart)
                if not best_metric:
                    lower = _is_lower_better(metric)
                    vals = [r.get("results", {}).get(metric) for r in run_list
                            if isinstance(r.get("results", {}).get(metric), (int, float))]
                    if vals:
                        best_val = min(vals) if lower else max(vals)
                        # Format nicely
                        if isinstance(best_val, float) and best_val < 0.001:
                            best_metric = f"{metric} = {best_val:.2e}"
                        elif isinstance(best_val, float) and best_val < 1:
                            best_metric = f"{metric} = {best_val:.4f}"
                        elif isinstance(best_val, int) or (isinstance(best_val, float) and best_val == int(best_val)):
                            best_metric = f"{metric} = {int(best_val):,}"
                        else:
                            best_metric = f"{metric} = {best_val:,.4f}"

                log_scale = _is_lower_better(metric)
                png = generate_export_chart(run_list, metric, title="", log_scale=log_scale)
                chart_b64 = base64.b64encode(png).decode()
        except Exception:
            log.debug("Chart generation failed for %s", project_name, exc_info=True)

    data = {
        "auth_token": auth_token,
        "event_type": "experiment_complete",
        "payload": {
            "project_name": project_name,
            "runs": runs,
            "kept": kept,
            "best_metric": best_metric,
            "insight": insight,
            "github_url": github_url,
            "chart_png_b64": chart_b64,
        },
    }

    result = _post("send-event", data, auth_token=auth_token)
    if result and result.get("ok"):
        log.info("Experiment event sent: %s", project_name)
        return result.get("emailed", False)
    return False


def prompt_for_email_cli(state) -> str | None:
    """Prompt the user for email in the CLI. Returns email or None."""
    email = os.environ.get("DISTILLATE_EMAIL", "").strip()
    if email:
        return email

    # Check if we've already asked
    asked = os.environ.get("DISTILLATE_EMAIL_ASKED", "").strip()
    if asked:
        return None

    print()
    print("  Want experiment reports and paper suggestions by email?")
    print("  You'll get a summary when experiments finish,")
    print("  plus a weekly digest of your reading.")
    print()
    email = input("  Email (Enter to skip): ").strip()

    if email and "@" in email:
        config.save_to_env("DISTILLATE_EMAIL", email)
        config.save_to_env("DISTILLATE_EMAIL_CADENCE", "weekly")
        print(f"  Saved! You'll get weekly digests at {email}.")
        print()
        # Sync immediately
        sync_snapshot(state)
        return email
    else:
        config.save_to_env("DISTILLATE_EMAIL_ASKED", "true")
        print("  No problem. You can enable this later in settings.")
        print()
        return None
