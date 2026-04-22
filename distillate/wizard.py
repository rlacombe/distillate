"""Interactive setup wizard for first-time users.

Contains the init wizard, scheduling helpers, and newsletter subscription.
"""

import logging
import os
import sys
from pathlib import Path

import requests

from distillate import secrets as _secrets

log = logging.getLogger("distillate")

_SUBSCRIBE_URL = "https://distillate-subscribe.distillate.workers.dev/"


def _mask_value(value: str) -> str:
    """Mask a config value for display, showing first/last 4 chars."""
    if len(value) > 12:
        return value[:4] + "..." + value[-4:]
    return value


def _prompt_with_default(prompt: str, env_key: str, sensitive: bool = False) -> str | None:
    """Prompt user, showing existing value as default. Returns None if skipped."""
    current = os.environ.get(env_key, "")
    if current:
        display = _mask_value(current) if sensitive else current
        user_input = input(f"{prompt} [{display}]: ").strip()
    else:
        user_input = input(f"{prompt}: ").strip()

    if not user_input and current:
        return current
    return user_input or None


def _init_step5_claude(save_to_env) -> None:
    """Step 5: Claude API key (optional — for sync pipeline only)."""
    print("  " + "-" * 48)
    print("  Step 5 of 7: Claude API (optional)")
    print("  " + "-" * 48)
    print()
    print("  An Anthropic API key enables AI-powered features")
    print("  in the sync pipeline:")
    print()
    print("    - AI summaries & key learnings for each paper")
    print("    - Daily reading suggestions")
    print("    - Experiment enrichment (hypothesis generation)")
    print()
    print("  The Nicolas agent uses Claude Code instead — no API")
    print("  key needed. Without a key here, papers use their")
    print("  abstract as a fallback summary.")
    print()
    print("  Note: your highlights and abstracts are sent to the Claude API")
    print("  for processing. No data is stored by Anthropic.")
    print()
    anthropic_key = _prompt_with_default(
        "  Anthropic API key (Enter to skip)", "ANTHROPIC_API_KEY", sensitive=True,
    )
    if anthropic_key:
        _secrets.set("ANTHROPIC_API_KEY", anthropic_key)
        print("  Claude API enabled for sync pipeline.")
    else:
        print("  Skipped — Nicolas will still work via Claude Code.")
    print()


def _init_step6_huggingface(save_to_env) -> None:
    """Step 6: HuggingFace integration (optional)."""
    print("  " + "-" * 48)
    print("  Step 6 of 7: HuggingFace (optional)")
    print("  " + "-" * 48)
    print()
    print("  A HuggingFace token unlocks:")
    print()
    print("    - Pi agent with open-weight models (Llama, Qwen, Mistral)")
    print("      via 15+ inference providers (Cerebras, Groq, Together...)")
    print("    - Cloud GPU compute (HF Jobs: A100 at $2.50/hr)")
    print("    - Model & dataset search in experiment agents")
    print()
    print("  Create a free account at: https://huggingface.co/join")
    print("  Then get a token at: https://huggingface.co/settings/tokens")
    print()

    hf_token = _prompt_with_default(
        "  HuggingFace token (Enter to skip)", "HF_TOKEN", sensitive=True,
    )
    if hf_token:
        # Validate the token
        try:
            from distillate.huggingface import validate_token
            result = validate_token(hf_token)
            if result.get("ok"):
                _secrets.set("HF_TOKEN", hf_token)
                username = result.get("username", "unknown")
                plan = result.get("plan", "free")
                can_pay = result.get("can_pay", False)
                print(f"  Connected as {username} ({plan} plan)")
                if can_pay:
                    print("  Billing enabled — HF Jobs compute available.")
                else:
                    print("  Add credits at huggingface.co/settings/billing for GPU compute.")
            else:
                print(f"  Token validation failed: {result.get('error', 'unknown error')}")
                print("  Saving anyway — you can update it later.")
                _secrets.set("HF_TOKEN", hf_token)
        except Exception:
            print("  Could not validate token (network error).")
            print("  Saving anyway — you can update it later.")
            _secrets.set("HF_TOKEN", hf_token)
    else:
        print("  Skipped — Hub search still works without a token.")
    print()


def _init_step7_extras(save_to_env) -> None:
    """Step 7: Email digest + experiment tracking."""
    print("  " + "-" * 48)
    print("  Step 7 of 7: Extras")
    print("  " + "-" * 48)
    print()
    print("  These are all optional. Press Enter to skip any of them.")
    print("  You can come back anytime with 'distillate --init'.")
    print()

    # Email Digest
    print("  Email Digest")
    print()
    print("  Get a weekly email summarizing what you've read, plus")
    print("  daily suggestions for what to read next from your queue.")
    print()
    print("  Requires a free Resend account: https://resend.com")
    print()
    resend_key = _prompt_with_default(
        "  Resend API key (Enter to skip)", "RESEND_API_KEY", sensitive=True,
    )
    if resend_key:
        _secrets.set("RESEND_API_KEY", resend_key)
        email_to = _prompt_with_default("  Your email address", "DIGEST_TO")
        if email_to:
            save_to_env("DIGEST_TO", email_to)
        print()
        print("  Resend's free tier includes one custom domain (3,000 emails/month).")
        print("  Add your domain at resend.com/domains, then set DIGEST_FROM")
        print("  in your .env (e.g. digest@yourdomain.com).")
        print()
        print("  Email digest enabled.")
    else:
        print("  Skipped.")
    print()

    # Experiment tracking (merged from old step 6)
    print("  Experiment Tracking")
    print()
    print("  Track ML experiments alongside your papers.")
    print("  Distillate can auto-discover experiments in your project directories")
    print("  and generate rich lab notebooks with run timelines and diffs.")
    print()

    enable = input("  Enable experiment tracking? [y/N] ").strip().lower()
    if enable not in ("y", "yes"):
        print("  Skipped. You can enable later with EXPERIMENTS_ENABLED=true")
        print()
        return

    save_to_env("EXPERIMENTS_ENABLED", "true")

    root = input("  Research folder root (e.g. ~/Code/Research): ").strip()
    if root:
        root_path = Path(root).expanduser().resolve()
        if root_path.is_dir():
            save_to_env("EXPERIMENTS_ROOT", str(root_path))
            print(f"  Set EXPERIMENTS_ROOT={root_path}")

            # Auto-discover ML repos
            from distillate.experiments import detect_ml_repos
            repos = detect_ml_repos(root_path)
            if repos:
                print(f"\n  Found {len(repos)} ML project(s):")
                for r in repos[:10]:
                    print(f"    - {r.name} ({r})")
                print()
                scan_now = input("  Scan them now? [Y/n] ").strip().lower()
                if scan_now not in ("n", "no"):
                    from distillate.experiments import (
                        generate_html_notebook,
                        generate_notebook,
                        scan_experiment,
                        slugify,
                    )
                    from distillate.obsidian import (
                        write_experiment_html_notebook,
                        write_experiment_notebook,
                    )
                    from distillate.state import State
                    state = State()
                    for repo_path in repos:
                        print(f"    Scanning {repo_path.name}...")
                        result = scan_experiment(repo_path)
                        if "error" not in result:
                            pid = slugify(result["name"])
                            state.add_experiment(
                                experiment_id=pid,
                                name=result["name"],
                                path=str(repo_path),
                            )
                            for run_id, run_data in result.get("runs", {}).items():
                                state.add_run(pid, run_id, run_data)
                            state.update_experiment(
                                pid,
                                last_scanned_at=__import__("datetime").datetime.now(
                                    __import__("datetime").timezone.utc
                                ).isoformat(),
                                last_commit_hash=result.get("head_hash", ""),
                            )
                            runs = result.get("runs", {})
                            print(f"      {len(runs)} run(s) discovered")
                            # Generate notebooks (MD + HTML)
                            proj = state.get_experiment(pid)
                            if proj:
                                nb = generate_notebook(proj)
                                write_experiment_notebook(proj, nb)
                                nb_html = generate_html_notebook(proj)
                                write_experiment_html_notebook(proj, nb_html)
                    state.save()
                    print(f"\n  Tracking {len(repos)} project(s).")
            else:
                print("  No ML projects found in that folder.")
        else:
            print(f"  Directory not found: {root_path}")
    else:
        print("  Skipped root folder. You can set EXPERIMENTS_ROOT later.")

    print()


def _schedule() -> None:
    """Set up, check, or remove automatic syncing."""
    import platform

    if platform.system() == "Darwin":
        _schedule_macos()
    elif platform.system() == "Windows":
        print()
        print("  Automatic scheduling on Windows uses Task Scheduler.")
        print()
        print("  Open Task Scheduler and create a task that runs:")
        print("    distillate --sync")
        print()
    else:
        _schedule_linux()


def _schedule_macos() -> None:
    """macOS scheduling via launchd."""
    import plistlib
    import subprocess

    plist_path = Path.home() / "Library/LaunchAgents/com.distillate.sync.plist"
    log_path = "~/Library/Logs/distillate.log"

    if plist_path.exists():
        # Parse plist to show current config
        interval_mins = 15
        try:
            with open(plist_path, "rb") as f:
                plist = plistlib.load(f)
            interval_secs = plist.get("StartInterval", 900)
            interval_mins = interval_secs // 60
        except Exception:
            log.debug("Failed to parse plist at %s", plist_path, exc_info=True)

        print()
        print("  Distillate Scheduling")
        print("  " + "-" * 40)
        print("  Status:   Active (launchd)")
        print(f"  Interval: every {interval_mins} minutes")
        print(f"  Log:      {log_path}")
        print()
        print("    1. Run sync now")
        print("    2. Remove schedule")
        print("    3. Keep current")
        print()
        choice = input("  Your choice [3]: ").strip()

        if choice == "1":
            subprocess.run(["launchctl", "start", "com.distillate.sync"])
            print("  Sync started.")
        elif choice == "2":
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True,
            )
            plist_path.unlink(missing_ok=True)
            print("  Schedule removed.")
        else:
            print("  Keeping current schedule.")
        print()
    else:
        print()
        print("  Distillate Scheduling")
        print("  " + "-" * 40)
        print("  Status: Not scheduled")
        print()
        print("  Distillate can run automatically every 15 minutes")
        print("  so your papers stay in sync without running it manually.")
        print()
        setup = input("  Set up automatic syncing? [Y/n] ").strip().lower()
        if setup != "n":
            _install_launchd()
        else:
            print("  Skipped. Run 'distillate --schedule' later.")
        print()


def _install_launchd() -> None:
    """Generate and install a launchd plist for automatic syncing."""
    import plistlib
    import shutil
    import subprocess

    label = "com.distillate.sync"
    plist_path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    log_path = str(Path.home() / "Library/Logs/distillate.log")

    # Find distillate executable
    executable = shutil.which("distillate")
    if not executable:
        print("  Could not find 'distillate' in PATH.")
        print("  Make sure it's installed: pip install distillate")
        return

    # Find rmapi for PATH
    rmapi_path = shutil.which("rmapi")
    launch_path = "/usr/local/bin:/usr/bin:/bin"
    if rmapi_path:
        rmapi_dir = str(Path(rmapi_path).parent)
        if rmapi_dir not in launch_path:
            launch_path = f"{rmapi_dir}:{launch_path}"

    # Unload existing agent
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
    )

    # Ensure directory exists
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    # Write plist
    plist_data = {
        "Label": label,
        "ProgramArguments": [executable],
        "StartInterval": 900,
        "EnvironmentVariables": {"PATH": launch_path},
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
        "Nice": 10,
    }
    with open(plist_path, "wb") as f:
        plistlib.dump(plist_data, f)

    # Load the agent
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
    )

    if result.returncode == 0:
        print()
        print("  Automatic syncing enabled (every 15 minutes).")
        print(f"  Log: {log_path}")
    else:
        print()
        print("  Could not load launchd agent.")
        print(f"  Plist written to: {plist_path}")
        print("  Try: launchctl load " + str(plist_path))


def _schedule_linux() -> None:
    """Linux scheduling via cron."""
    import subprocess

    has_entry = False
    lines = []
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and "distillate" in result.stdout:
            has_entry = True
            lines = [ln for ln in result.stdout.splitlines() if "distillate" in ln]
    except Exception:
        log.debug("Failed to check crontab for existing entry", exc_info=True)

    print()
    print("  Distillate Scheduling")
    print("  " + "-" * 40)

    if has_entry:
        print("  Status: Active (cron)")
        for line in lines:
            print(f"    {line.strip()}")
        print()
        print("  To modify: crontab -e")
    else:
        print("  Status: Not scheduled")
        print()
        print("  Add this to your crontab (crontab -e):")
        print("    */15 * * * * distillate")
    print()


def _init_newsletter() -> None:
    """Offer to subscribe to product update emails."""
    print()
    print("  Product Updates")
    print("  " + "-" * 40)
    print("  Get notified about new features and releases.")
    print("  One email per release, unsubscribe anytime.")
    print()
    email = input("  Your email (Enter to skip): ").strip()
    if not email:
        print("  Skipped.")
        return
    try:
        resp = requests.post(
            _SUBSCRIBE_URL,
            json={"email": email},
            timeout=5,
        )
        if resp.ok:
            print("  You're in! We'll keep you posted.")
        else:
            print("  Couldn't subscribe right now, but no worries.")
    except Exception:
        print("  Couldn't reach the server, but no worries.")


def _connectors() -> None:
    """Show status of all connectors with colored indicators."""
    import shutil
    from distillate import config

    _GREEN = "\033[1;32m"
    _YELLOW = "\033[33m"
    _DIM = "\033[2m"
    _RESET = "\033[0m"

    def _check(ok: bool) -> str:
        return f"{_GREEN}\u2713{_RESET}" if ok else f"{_YELLOW}\u2013{_RESET}"

    connectors = []

    # Papers — Zotero
    zotero_ok = bool(config.ZOTERO_API_KEY and config.ZOTERO_USER_ID)
    detail = ""
    if zotero_ok:
        detail = f"user {config.ZOTERO_USER_ID}"
        if config.ZOTERO_COLLECTION_KEY:
            try:
                from distillate import zotero_client
                name = zotero_client.get_collection_name(config.ZOTERO_COLLECTION_KEY)
                detail += f" \u00b7 {name}"
            except Exception:
                detail += f" \u00b7 {config.ZOTERO_COLLECTION_KEY}"
    connectors.append((_check(zotero_ok), "Papers", "Zotero library", detail if zotero_ok else "not configured"))

    # Updates — Email
    email = os.environ.get("DISTILLATE_EMAIL", "").strip()
    email_ok = bool(email)
    if email_ok:
        verified = os.environ.get("DISTILLATE_EMAIL_VERIFIED", "").lower() in ("true", "1")
        v_tag = "verified" if verified else "unverified"
        email_detail = f"{email} ({v_tag})"
    else:
        email_detail = "not configured"
    connectors.append((_check(email_ok), "Updates", "Email", email_detail))

    # Notes — Obsidian
    obsidian_ok = bool(config.OBSIDIAN_VAULT_PATH)
    obs_detail = config.OBSIDIAN_VAULT_PATH.replace(str(Path.home()), "~") if obsidian_ok else "not configured"
    connectors.append((_check(obsidian_ok), "Notes", "Obsidian vault", obs_detail))

    # Tablet — reMarkable
    has_rmapi = shutil.which("rmapi") is not None
    has_token = bool(config.REMARKABLE_DEVICE_TOKEN)
    rm_ok = has_rmapi and has_token
    if config.READING_SOURCE == "remarkable" or has_rmapi:
        if rm_ok:
            rm_detail = "registered"
        elif has_rmapi:
            rm_detail = "rmapi found, not registered"
        else:
            rm_detail = "rmapi not installed"
        connectors.append((_check(rm_ok), "Tablet", "reMarkable", rm_detail))

    # HuggingFace
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    hf_ok = bool(hf_token)
    if hf_ok:
        try:
            from distillate.huggingface import validate_token
            info = validate_token(hf_token)
            hf_detail = info.get("username", "connected") if info.get("ok") else "token invalid"
        except Exception:
            hf_detail = "connected"
    else:
        hf_detail = "not configured"
    connectors.append((_check(hf_ok), "HuggingFace", "Models & compute", hf_detail))

    total = len(connectors)
    connected = sum(1 for c in connectors if "\u2713" in c[0])

    print()
    print("  Connectors")
    print("  " + "\u2500" * 48)
    print()
    for icon, label, service, detail in connectors:
        print(f"  {icon} {label:<14s}{service:<22s}{_DIM}{detail}{_RESET}")
    print()
    print(f"  {connected}/{total} connected")
    if connected < total:
        print(f"  {_DIM}Run 'distillate --setup <name>' to configure a connector{_RESET}")
    print()


def _email_signup() -> None:
    """Interactive email signup with preference selection and cloud sync."""
    from distillate import config as _cfg
    from distillate.state import State

    email = os.environ.get("DISTILLATE_EMAIL", "").strip()
    verified = os.environ.get("DISTILLATE_EMAIL_VERIFIED", "").lower() in ("true", "1")

    print()
    print("  Email Notifications")
    print("  " + "\u2500" * 48)

    if email:
        print()
        print(f"  Current: {email}" + (" (verified)" if verified else " (unverified)"))
        # Show current preferences
        daily = os.environ.get("DISTILLATE_EMAIL_DAILY_PAPERS", "true").lower() in ("true", "1", "yes")
        weekly = os.environ.get("DISTILLATE_EMAIL_WEEKLY_DIGEST", "true").lower() in ("true", "1", "yes")
        reports = os.environ.get("DISTILLATE_EMAIL_EXPERIMENT_REPORTS", "true").lower() in ("true", "1", "yes")
        print()
        print(f"    [{'x' if reports else ' '}] Experiment reports")
        print(f"    [{'x' if daily else ' '}] Daily paper suggestions")
        print(f"    [{'x' if weekly else ' '}] Weekly reading digest")
        print()
        action = input("  Update preferences? [y/N] ").strip().lower()
        if action not in ("y", "yes"):
            print("  Keeping current settings.")
            print()
            return
        print()

    print()
    print("  Create an account to unlock cloud features:")
    print("    \u2022 Cross-device sync \u2014 same library on laptop and desktop")
    print("    \u2022 Experiment reports when a session finishes")
    print("    \u2022 Daily paper suggestions from your queue")
    print("    \u2022 Weekly reading digest summarizing what you read")
    print()

    if not email:
        email = input("  Email address: ").strip()
        if not email or "@" not in email:
            print("  Skipped.")
            print()
            return
    print()

    # Preferences
    print("  Which emails would you like? (y/n for each)")
    print()
    reports_in = input("  Experiment reports [Y/n]: ").strip().lower()
    reports = reports_in not in ("n", "no")
    daily_in = input("  Daily paper suggestions [Y/n]: ").strip().lower()
    daily = daily_in not in ("n", "no")
    weekly_in = input("  Weekly reading digest [Y/n]: ").strip().lower()
    weekly = weekly_in not in ("n", "no")

    # Save to .env
    _cfg.save_to_env("DISTILLATE_EMAIL", email)
    _cfg.save_to_env("DISTILLATE_EMAIL_EXPERIMENT_REPORTS", "true" if reports else "false")
    _cfg.save_to_env("DISTILLATE_EMAIL_DAILY_PAPERS", "true" if daily else "false")
    _cfg.save_to_env("DISTILLATE_EMAIL_WEEKLY_DIGEST", "true" if weekly else "false")

    print()
    print(f"  Saved! Registering {email} with cloud...")

    # Sync to cloud
    try:
        from distillate.cloud_email import sync_snapshot
        state = State()
        result = sync_snapshot(state)
        if result and result.get("ok"):
            verified = result.get("verified", False)
            _cfg.save_to_env("DISTILLATE_EMAIL_VERIFIED", "true" if verified else "false")
            if verified:
                print("  Email verified and synced.")
            else:
                print("  Check your inbox for a verification email.")
        else:
            print("  Saved locally. Cloud sync will retry on next run.")
    except Exception:
        log.debug("Cloud email sync failed during signup", exc_info=True)
        print("  Saved locally. Cloud sync will retry on next run.")

    print()


def _setup_zotero(save_to_env) -> bool | None:
    """Configure Zotero connector.

    Returns True if user chose Zotero reader, False if reMarkable,
    None if setup was aborted (missing required input).
    """
    print()
    print("  Zotero Setup")
    print("  " + "\u2500" * 48)
    print()
    print("  Distillate watches your Zotero library for new papers.")
    print()
    print("  You need a Zotero API key with read/write library access.")
    print("  Create one here: https://www.zotero.org/settings/keys/new")
    print()
    api_key = _prompt_with_default("  API key", "ZOTERO_API_KEY", sensitive=True)
    if not api_key:
        print("\n  Error: A Zotero API key is required.")
        return None

    print()
    print("  Your user ID is the number shown on the same page.")
    print()
    user_id = _prompt_with_default("  User ID", "ZOTERO_USER_ID")
    if not user_id:
        print("\n  Error: A Zotero user ID is required.")
        return None

    print()
    print("  Verifying...")
    _secrets.set("ZOTERO_API_KEY", api_key)
    _secrets.set("ZOTERO_USER_ID", user_id)
    try:
        resp = requests.get(
            f"https://api.zotero.org/users/{user_id}/items?limit=1",
            headers={"Zotero-API-Version": "3", "Zotero-API-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        print("  Connected! Found your Zotero library.")
    except Exception as e:
        print(f"  Warning: could not verify credentials ({e})")
        print("  Saved anyway \u2014 you can fix them later in .env")
    print()

    # Collection scoping (optional)
    try:
        from distillate import zotero_client as _zc
        collections = _zc.list_collections()
        if collections:
            colls = sorted(collections, key=lambda c: c["data"]["name"])
            print("  You can scope Distillate to a specific collection.")
            print("  Only papers you add to that collection will be synced.")
            print()
            for i, c in enumerate(colls, 1):
                print(f"    {i}. {c['data']['name']}")
            print()
            existing_key = os.environ.get("ZOTERO_COLLECTION_KEY", "").strip()
            if existing_key:
                try:
                    existing_name = _zc.get_collection_name(existing_key)
                except Exception:
                    existing_name = existing_key
                hint = f" [current: {existing_name}]"
            else:
                hint = ""
            choice = input(
                f"  Collection number (Enter for whole library){hint}: "
            ).strip()
            if choice:
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(colls):
                        coll_key = colls[idx]["key"]
                        coll_name = colls[idx]["data"]["name"]
                        save_to_env("ZOTERO_COLLECTION_KEY", coll_key)
                        print(f"  Scoped to '{coll_name}'.")
                    else:
                        print("  Invalid number, using whole library.")
                        save_to_env("ZOTERO_COLLECTION_KEY", "")
                except ValueError:
                    print("  Invalid input, using whole library.")
                    save_to_env("ZOTERO_COLLECTION_KEY", "")
            else:
                save_to_env("ZOTERO_COLLECTION_KEY", "")
                print("  Using whole library.")
            print()
    except Exception:
        log.debug("Collection picker failed, skipping", exc_info=True)

    # WebDAV storage (optional)
    existing_webdav = os.environ.get("ZOTERO_WEBDAV_URL", "").strip()
    print("  Do you use WebDAV for Zotero file storage?")
    print("  (Most people use Zotero's built-in cloud \u2014 press Enter to skip.)")
    if existing_webdav:
        print(f"  Current: {existing_webdav}")
    print()
    webdav_url = input("  WebDAV URL (Enter to skip): ").strip()
    if webdav_url:
        save_to_env("ZOTERO_WEBDAV_URL", webdav_url.rstrip("/"))
        webdav_user = _prompt_with_default("  WebDAV username", "ZOTERO_WEBDAV_USERNAME")
        webdav_pass = _prompt_with_default("  WebDAV password", "ZOTERO_WEBDAV_PASSWORD", sensitive=True)
        if webdav_user:
            save_to_env("ZOTERO_WEBDAV_USERNAME", webdav_user)
        if webdav_pass:
            _secrets.set("ZOTERO_WEBDAV_PASSWORD", webdav_pass)
        print("  WebDAV configured.")
    elif existing_webdav:
        print("  Keeping existing WebDAV config.")
    print()

    # Reading surface choice
    existing_source = os.environ.get("READING_SOURCE", "").strip().lower()
    print("  How do you read your papers?")
    print()
    print("    1. reMarkable tablet")
    print("    2. Any device (iPad, desktop, tablet \u2014 via Zotero app)")
    print()
    default_choice = "2" if existing_source == "zotero" else "1"
    reading_choice = input(f"  Your choice [{default_choice}]: ").strip() or default_choice
    use_zotero_reader = reading_choice == "2"

    if use_zotero_reader:
        save_to_env("READING_SOURCE", "zotero")
        save_to_env("SYNC_HIGHLIGHTS", "false")
        print("  Read and highlight papers in the Zotero app (desktop, iPad,")
        print("  or Android), then add the 'read' tag when done.")
    else:
        save_to_env("READING_SOURCE", "remarkable")
    print()

    return use_zotero_reader


def _setup_remarkable(save_to_env) -> None:
    """Configure reMarkable connector: rmapi install, device registration."""
    import shutil

    print()
    print("  reMarkable Setup")
    print("  " + "\u2500" * 48)
    print()
    print("  Distillate uses rmapi to sync PDFs with your reMarkable")
    print("  via the reMarkable Cloud.")
    print()
    print("  Important: enable 'Text recognition' in your reMarkable")
    print("  settings for highlight extraction to work.")
    print()

    already_registered = bool(os.environ.get("REMARKABLE_DEVICE_TOKEN", ""))

    if already_registered:
        print("  reMarkable already registered.")
        print()
        register = input("  Re-register? [y/N] ").strip().lower()
        if register == "y":
            from distillate.integrations.remarkable.auth import register_interactive
            register_interactive()
        else:
            print("  Keeping existing registration.")
    elif shutil.which("rmapi"):
        print("  rmapi found.")
        print()
        print("  You need to authorize this device once.")
        print()
        register = input("  Register your reMarkable now? [Y/n] ").strip().lower()
        if register != "n":
            from distillate.integrations.remarkable.auth import register_interactive
            register_interactive()
        else:
            print("  Skipped. Run 'distillate --register' later.")
    else:
        print("  Distillate requires rmapi to sync files with your")
        print("  reMarkable via the cloud.")
        print()
        import platform
        if platform.system() == "Darwin":
            print("  Install it with Homebrew:")
            print("    brew install rmapi")
        else:
            print("  Download the latest binary from:")
            print("    https://github.com/ddvk/rmapi/releases")
        print()
        install_now = input("  Install rmapi now? [Y/n] ").strip().lower()
        if install_now != "n":
            if platform.system() == "Darwin":
                print()
                print("  Running: brew install rmapi")
                print()
                import subprocess
                result = subprocess.run(
                    ["brew", "install", "rmapi"],
                    capture_output=False,
                )
                print()
                if result.returncode == 0 and shutil.which("rmapi"):
                    print("  rmapi installed successfully!")
                    print()
                    register = input("  Register your reMarkable now? [Y/n] ").strip().lower()
                    if register != "n":
                        from distillate.integrations.remarkable.auth import register_interactive
                        register_interactive()
                    else:
                        print("  Skipped. Run 'distillate --register' later.")
                else:
                    print("  Installation failed. You can install manually later.")
                    print("  Run 'distillate --register' when ready.")
            else:
                print()
                print("  Please install rmapi manually from the link above,")
                print("  then run 'distillate --register' to connect.")
        else:
            print("  Skipped. Install rmapi and run 'distillate --register'")
            print("  when you're ready.")
    print()


def _setup_obsidian(save_to_env) -> None:
    """Configure Obsidian/notes connector: vault path, PDF subfolder, PDF storage."""
    print()
    print("  Notes & PDF Setup")
    print("  " + "\u2500" * 48)
    print()
    print("  When you finish reading, Distillate creates:")
    print("    \u2022 An annotated PDF with your highlights overlaid")
    print("    \u2022 A markdown note with metadata, highlights, and summaries")
    print()

    # Default to Obsidian if vault path already set
    existing_vault = os.environ.get("OBSIDIAN_VAULT_PATH", "")
    existing_output = os.environ.get("OUTPUT_PATH", "")
    if existing_vault:
        obsidian_default = "Y"
    elif existing_output:
        obsidian_default = "n"
    else:
        obsidian_default = "Y"

    use_obsidian = input(f"  Use an Obsidian vault? [{obsidian_default}/{'n' if obsidian_default == 'Y' else 'Y'}] ").strip().lower()
    if not use_obsidian:
        use_obsidian = obsidian_default.lower()

    if use_obsidian != "n":
        print()
        print("  To find your vault path in Obsidian:")
        print("    Open Obsidian > Settings > General (bottom of page)")
        print()
        vault_path = _prompt_with_default("  Vault path", "OBSIDIAN_VAULT_PATH")
        if vault_path:
            vault_path = str(Path(vault_path).expanduser().resolve())
            save_to_env("OBSIDIAN_VAULT_PATH", vault_path)
            print()
            print(f"  Obsidian mode enabled: {vault_path}/Distillate/")
        else:
            print("  No path provided \u2014 skipping.")
    else:
        print()
        folder = _prompt_with_default("  Output folder path (Enter to skip)", "OUTPUT_PATH")
        if folder:
            folder = str(Path(folder).expanduser().resolve())
            save_to_env("OUTPUT_PATH", folder)
            Path(folder).mkdir(parents=True, exist_ok=True)
            print(f"  Notes and PDFs will go to: {folder}")
        else:
            print("  Skipped. Notes will only be stored in Zotero.")
    print()

    # Distillate home folder (stores PDFs outside the Obsidian vault)
    existing_home = os.environ.get("DISTILLATE_HOME", "")
    default_home = existing_home or str(Path.home() / "Distillate")
    print("  Distillate stores PDFs outside your Obsidian vault so it stays")
    print("  lightweight. Notes will link to them with a file:// link.")
    print()
    home_input = input(f"  Distillate home folder [{default_home}]: ").strip()
    if not home_input:
        home_input = default_home
    home_path = str(Path(home_input).expanduser().resolve())
    save_to_env("DISTILLATE_HOME", home_path)
    papers_path = Path(home_path) / "Papers"
    papers_path.mkdir(parents=True, exist_ok=True)
    (papers_path / "To Read").mkdir(parents=True, exist_ok=True)
    print(f"  PDFs will go to: {papers_path}/")
    print()

    # PDF storage policy
    existing_source = os.environ.get("READING_SOURCE", "remarkable").lower()
    if existing_source == "zotero":
        print("  After syncing a paper, where should the PDF be kept?")
    else:
        print("  After syncing a paper to your reMarkable, where should")
        print("  the PDF be kept?")
    print()
    print("    1. Keep in Zotero (uses Zotero storage)")
    print("    2. Remove from Zotero after sync (saves space)")
    print()
    existing_keep = os.environ.get("KEEP_ZOTERO_PDF", "true")
    default_storage = "2" if existing_keep.lower() == "false" else "1"
    storage = input(f"  Your choice [{default_storage}]: ").strip()
    if not storage:
        storage = default_storage
    if storage == "2":
        save_to_env("KEEP_ZOTERO_PDF", "false")
        print("  PDFs will be removed from Zotero after upload.")
    else:
        save_to_env("KEEP_ZOTERO_PDF", "true")
        print("  PDFs will stay in Zotero.")
    print()


def _setup_single(connector: str) -> None:
    """Route --setup <connector> to the appropriate setup function."""
    from distillate.config import save_to_env

    valid = {"zotero", "email", "remarkable", "obsidian", "huggingface"}
    if connector not in valid:
        print(f"  Unknown connector: {connector}")
        print(f"  Available: {', '.join(sorted(valid))}")
        sys.exit(1)

    if connector == "zotero":
        _setup_zotero(save_to_env)
    elif connector == "remarkable":
        _setup_remarkable(save_to_env)
    elif connector == "obsidian":
        _setup_obsidian(save_to_env)
    elif connector == "email":
        _email_signup()
    elif connector == "huggingface":
        _init_step6_huggingface(save_to_env)


def _init_done(env_path) -> None:
    """Print post-setup instructions, offer import of existing papers, and automated syncing."""
    print()
    print("  " + "=" * 48)
    print("  Setup complete!")
    print("  " + "=" * 48)
    print()
    print(f"  Config saved to: {env_path}")

    # -- Seed queue: offer to import existing papers --
    _init_seed()

    print()
    print("  " + "-" * 48)
    print("  How it works")
    print("  " + "-" * 48)
    print()
    print("  There are seven commands:")
    print()
    print("    distillate --import")
    print("      Import existing papers from your Zotero library.")
    print()
    print("    distillate")
    print("      Syncs everything in both directions:")
    print("      Zotero -> reMarkable (new papers)")
    print("      reMarkable -> notes (papers you finished reading)")
    print()
    print("    distillate --status")
    print("      Shows queue health and reading stats at a glance.")
    print()
    print("    distillate --list")
    print("      List all tracked papers grouped by status.")
    print()
    print("    distillate --suggest")
    print("      Picks 3 papers from your queue and moves them")
    print("      to the front of your Distillate folder. Unread")
    print("      suggestions are moved back to Inbox automatically.")
    print()
    print("    distillate --digest")
    print("      Shows a summary of what you read this week.")
    print()
    print("    distillate --schedule")
    print("      Set up or manage automatic syncing.")
    print()
    print("  Your workflow:")
    print("    1. Save a paper to Zotero (browser connector)")
    print("    2. distillate (PDF lands on your reMarkable)")
    print("    3. Read and highlight on your reMarkable")
    print("    4. Move the document to Distillate/Read")
    print("    5. distillate (annotated PDF + notes are ready)")
    print()

    # Offer automated sync via _schedule()
    _schedule()

    # Newsletter opt-in
    _init_newsletter()

    print()
    print("  " + "=" * 48)
    print("  Run 'distillate' now to sync your first papers!")
    print("  " + "=" * 48)
    print()


def _init_seed() -> None:
    """Offer to import existing papers during init wizard."""
    from distillate import config
    from distillate import zotero_client
    from distillate import pipeline as _pipeline
    from distillate.state import State

    config.ensure_loaded()

    try:
        state = State()
        _coll_key = config.ZOTERO_COLLECTION_KEY
        papers = zotero_client.get_recent_papers(
            limit=100, collection_key=_coll_key,
        )
        papers = [p for p in papers if not state.has_document(p["key"])]

        if not papers:
            return

        if _coll_key:
            try:
                _coll_name = zotero_client.get_collection_name(_coll_key)
            except Exception:
                _coll_name = _coll_key
            scope = f" in '{_coll_name}'"
        else:
            scope = " in your library"

        print()
        print("  " + "-" * 48)
        print("  Import existing papers")
        print("  " + "-" * 48)
        print()
        print(f"  Found {len(papers)} untracked paper{'s' if len(papers) != 1 else ''}{scope}.")
        print()
        for p in papers[:5]:
            meta = zotero_client.extract_metadata(p)
            print(f"    - {meta['title']}")
        if len(papers) > 5:
            print(f"    ... and {len(papers) - 5} more")
        print()
        answer = input("  How many to import? [all/N/none] ").strip().lower()
        if not answer or answer == "none" or answer == "n":
            print("  Skipped. You can run 'distillate --import' later.")
            # Still set watermark so first sync doesn't process everything
            current_version = zotero_client.get_library_version()
            state.zotero_library_version = current_version
            state.save()
            return

        if answer != "all":
            try:
                count = int(answer)
                papers = papers[:count]
            except ValueError:
                print(f"  Invalid input: {answer}")
                return

        # Check if RM is available
        import shutil
        has_rm = bool(
            shutil.which("rmapi")
            and os.environ.get("REMARKABLE_DEVICE_TOKEN", "")
        )
        skip_remarkable = not has_rm

        if skip_remarkable:
            print("  reMarkable not registered — papers will upload on first sync.")
        else:
            from distillate.integrations.remarkable import client as remarkable_client
            remarkable_client.ensure_folders()

        existing_on_rm = set()
        if not skip_remarkable:
            from distillate.integrations.remarkable import client as remarkable_client
            existing_on_rm = set(
                remarkable_client.list_folder(config.RM_FOLDER_INBOX)
            )

        imported = 0
        for paper in papers:
            try:
                if _pipeline._upload_paper(paper, state, existing_on_rm, skip_remarkable=skip_remarkable):
                    imported += 1
            except Exception:
                log.debug(
                    "Failed to import '%s', skipping",
                    paper.get("data", {}).get("title", paper.get("key")),
                    exc_info=True,
                )

        # Update watermark
        current_version = zotero_client.get_library_version()
        state.zotero_library_version = current_version
        state.save()

        print(f"\n  Imported {imported} paper{'s' if imported != 1 else ''}.")

    except Exception:
        log.debug("Seed import failed, continuing", exc_info=True)
        print("  Could not fetch papers. You can run 'distillate --import' later.")


def _init_wizard() -> None:
    """Interactive setup wizard for first-time users."""
    from distillate.config import save_to_env, ENV_PATH

    # Detect existing config for re-run shortcut
    has_existing = ENV_PATH.exists() and os.environ.get("ZOTERO_API_KEY", "")

    print()
    if has_existing:
        print("  Distillate Setup")
        print("  " + "=" * 48)
        print()
        print(f"  Existing config found at: {ENV_PATH}")
        print()
        print("    1. Re-run full setup")
        print("    2. Configure AI & extras")
        print()
        choice = input("  Your choice [2]: ").strip()
        if choice != "1":
            print()
            _init_step5_claude(save_to_env)
            _init_step6_huggingface(save_to_env)
            _init_step7_extras(save_to_env)
            _init_done(ENV_PATH)
            return
        # Warn about existing state
        try:
            from distillate.state import STATE_PATH
            if STATE_PATH.exists():
                import json as _json
                state_data = _json.loads(STATE_PATH.read_text(encoding="utf-8"))
                n_papers = len(state_data.get("documents", {}))
                if n_papers > 0:
                    print()
                    print(f"  Warning: You have {n_papers} tracked paper(s) in state.json.")
                    print("  A full re-setup will NOT erase your papers, but if you")
                    print("  want to back them up first, run:")
                    print("    distillate --export-state ~/distillate-backup.json")
                    print()
                    proceed = input("  Continue with full re-setup? [Y/n]: ").strip().lower()
                    if proceed and proceed != "y":
                        print("  Aborted.")
                        return
        except Exception:
            log.debug("Failed to check existing state during setup", exc_info=True)
        print()
    else:
        print("  Welcome to Distillate")
        print("  " + "=" * 48)
        print()
        print("  Distillate automates your research paper workflow:")
        print()
        print("    1. You save a paper to Zotero (browser connector)")
        print("    2. You read and highlight the paper")
        print("    3. Distillate extracts your highlights, creates an")
        print("       annotated PDF, writes a note, and archives it")
        print()
        print("  Power-user features (optional, with Anthropic API key):")
        print("    - Nicolas, an interactive research agent in your terminal")
        print("    - AI summaries & key learnings for each paper")
        print("    - Daily reading suggestions & weekly digest emails")
        print()
        print("  Let's get you set up. This takes about 2 minutes.")
        print()
        print(f"  Config will be saved to: {ENV_PATH}")
        print()

    # -- Steps 1-4: Connectors --

    use_zotero_reader = _setup_zotero(save_to_env)
    if use_zotero_reader is None:
        return  # aborted — missing required input

    if not use_zotero_reader:
        _setup_remarkable(save_to_env)

    _setup_obsidian(save_to_env)

    # -- Step 5: Claude API --

    _init_step5_claude(save_to_env)

    # -- Step 6: HuggingFace --

    _init_step6_huggingface(save_to_env)

    # -- Step 7: Extras (email + experiments) --

    _init_step7_extras(save_to_env)

    # -- Done --

    _init_done(ENV_PATH)
