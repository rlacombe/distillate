"""CLI command handlers.

Individual commands dispatched from cli.py: status, list, remove, digest,
suggest, import, metadata refresh, experiment commands, etc.
"""

import json
import logging
import re
import sys
from pathlib import Path

import requests

from distillate.cli import _bold, _dim, _opt

log = logging.getLogger("distillate")


def _backfill_s2() -> None:
    """Backfill Semantic Scholar data for papers that don't have it yet."""
    from distillate import config
    from distillate import semantic_scholar
    from distillate import zotero_client
    from distillate.state import State

    config.setup_logging()

    state = State()
    count = 0

    for key, doc in state.documents.items():
        meta = doc.get("metadata", {})
        # Skip papers already enriched with sufficient tags
        has_s2 = bool(meta.get("s2_url"))
        has_enough_tags = len(meta.get("tags") or []) >= 3
        if has_s2 and has_enough_tags:
            continue

        # Fetch metadata from Zotero if missing DOI
        if not meta.get("doi"):
            items = zotero_client.get_items_by_keys([key])
            if items:
                meta = zotero_client.extract_metadata(items[0])
                doc["metadata"] = meta

        s2_data = semantic_scholar.lookup_paper(
            doi=meta.get("doi", ""), title=doc["title"],
            url=meta.get("url", ""),
        )
        if s2_data:
            had_unknown = "unknown" in meta.get("citekey", "")
            had_date = bool(meta.get("publication_date"))
            semantic_scholar.enrich_metadata(meta, s2_data)
            # Regenerate citekey if S2 filled missing author or date
            needs_regen = not had_date and meta.get("publication_date")
            if had_unknown and s2_data.get("authors"):
                needs_regen = True
                doc["authors"] = meta["authors"]
            if needs_regen:
                meta["citekey"] = zotero_client._generate_citekey(
                    meta["authors"], meta["title"], meta["publication_date"],
                )
            print(f"  S2 enriched '{doc['title']}': {s2_data['citation_count']} citations")
        else:
            print(f"  S2: no data found for '{doc['title']}'")

        doc["metadata"] = meta
        state.save()
        count += 1

    print(f"Backfilled S2 data for {count} paper(s).")


def _refresh_metadata(args: list[str] | None = None) -> None:
    """Re-extract metadata from Zotero for tracked papers.

    With no arguments, refreshes all papers. Pass a citekey, index number,
    or title substring to refresh a single paper.
    """
    from distillate import config, zotero_client, obsidian, semantic_scholar, huggingface
    from distillate.pipeline import _find_papers
    from distillate.state import State

    config.setup_logging()

    state = State()

    if args:
        query = " ".join(args)
        matches = _find_papers(query, state)
        if not matches:
            print(f"\n  No paper matching '{query}'.\n")
            return
        if len(matches) > 1:
            print(f"\n  Multiple papers match '{query}':")
            for key, doc in matches:
                idx = state.index_of(key)
                ck = doc.get("metadata", {}).get("citekey", "")
                print(f"    [{idx}] {doc['title']} ({ck})")
            print("  Be more specific.\n")
            return
        keys = [matches[0][0]]
    else:
        keys = list(state.documents.keys())

    if not keys:
        print("No tracked papers.")
        return

    print(f"  Fetching metadata for {len(keys)} paper(s) from Zotero...")
    items = zotero_client.get_items_by_keys(keys)
    items_by_key = {item["key"]: item for item in items}
    changed = 0
    total = len(keys)
    is_tty = sys.stdout.isatty()

    for i, key in enumerate(keys, 1):
        item = items_by_key.get(key)
        if not item:
            continue
        doc = state.get_document(key)
        if not doc:
            continue

        title = doc["title"]
        # Show progress for each paper
        short = title[:50]
        if is_tty:
            print(f"\r  [{i}/{total}] \"{short}\"" + " " * 20, end="\r", flush=True)

        old_meta = doc.get("metadata", {})
        new_meta = zotero_client.extract_metadata(item)
        any_change = False

        # Preserve S2-filled authors when Zotero has none
        new_authors = new_meta.get("authors") or []
        old_authors = old_meta.get("authors") or []
        zotero_has_no_authors = not new_authors or new_authors == ["Unknown"]
        if zotero_has_no_authors and old_authors and old_authors != ["Unknown"]:
            new_meta["authors"] = old_authors
            new_meta["citekey"] = zotero_client._generate_citekey(
                old_authors, new_meta["title"], new_meta.get("publication_date", ""),
            )

        # Re-query S2 for papers missing date, citation data, or authors
        had_unknown_author = "unknown" in new_meta.get("citekey", "")
        if not new_meta.get("publication_date") or not old_meta.get("s2_url") or had_unknown_author:
            try:
                s2_data = semantic_scholar.lookup_paper(
                    doi=new_meta.get("doi", ""), title=doc["title"],
                    url=new_meta.get("url", ""),
                )
                if s2_data:
                    had_date = bool(new_meta.get("publication_date"))
                    semantic_scholar.enrich_metadata(new_meta, s2_data)
                    needs_regen = not had_date and new_meta.get("publication_date")
                    if had_unknown_author and s2_data.get("authors"):
                        needs_regen = True
                        doc["authors"] = new_meta["authors"]
                    if needs_regen:
                        new_meta["citekey"] = zotero_client._generate_citekey(
                            new_meta["authors"], new_meta["title"],
                            new_meta["publication_date"],
                        )
                        if not any_change:
                            print(f"  [{i}/{total}] \"{title[:50]}\"")
                        print(f"    S2 enrichment -> citekey: {new_meta['citekey']}")
                        any_change = True
            except Exception:
                log.debug("S2 lookup failed for '%s'", doc["title"], exc_info=True)
        else:
            # Preserve existing S2 enrichment
            for field in ("citation_count", "influential_citation_count",
                          "s2_url"):
                if field in old_meta:
                    new_meta[field] = old_meta[field]

        # Preserve paper_type if present
        if "paper_type" in old_meta:
            new_meta["paper_type"] = old_meta["paper_type"]

        # HuggingFace enrichment (backfill GitHub repo/stars)
        if not new_meta.get("github_repo"):
            try:
                arxiv_id = semantic_scholar.extract_arxiv_id(
                    new_meta.get("doi", ""), new_meta.get("url", ""),
                )
                if arxiv_id:
                    hf_data = huggingface.lookup_paper(arxiv_id)
                    if hf_data:
                        if hf_data.get("ai_summary"):
                            new_meta["hf_summary"] = hf_data["ai_summary"]
                        if hf_data.get("github_repo"):
                            new_meta["github_repo"] = hf_data["github_repo"]
                            new_meta["github_stars"] = hf_data.get("github_stars")
                            if not any_change:
                                print(f"  [{i}/{total}] \"{title[:50]}\"")
                            print(f"    HF: GitHub {hf_data['github_repo']}")
                            any_change = True
            except Exception:
                log.debug("HF lookup failed for '%s'", doc["title"], exc_info=True)
        else:
            # Preserve existing HF data
            for field in ("github_repo", "github_stars"):
                if field in old_meta:
                    new_meta[field] = old_meta[field]

        # Detect what changed
        old_ck = old_meta.get("citekey", "")
        new_ck = new_meta.get("citekey", "")
        old_title = doc["title"]
        new_title = new_meta.get("title", old_title)

        # Check for citekey change → rename Saved files
        citekey_changed = old_ck != new_ck
        needs_rename = citekey_changed
        # Also rename if file on disk doesn't match expected citekey
        if not needs_rename and new_ck and doc.get("status") == "processed":
            rd = obsidian._read_dir()
            if rd and not (rd / f"{new_ck}.md").exists():
                needs_rename = True
        if citekey_changed and not any_change:
            print(f"  [{i}/{total}] \"{title[:50]}\"")
            print(f"    Citekey: {old_ck or '(title)'} -> {new_ck}")
            any_change = True
        if needs_rename and new_ck and doc.get("status") == "processed":
            if not any_change:
                print(f"  [{i}/{total}] \"{title[:50]}\"")
                print(f"    Citekey: {old_ck or '(title)'} -> {new_ck}")
                any_change = True
            obsidian.rename_paper(doc["title"], old_ck, new_ck)

            new_uri = obsidian.get_obsidian_uri(doc["title"], citekey=new_ck)
            if new_uri:
                print("    Updating Obsidian link in Zotero")
                zotero_client.update_obsidian_link(key, new_uri)

            pd = obsidian._pdf_dir()
            if pd:
                new_pdf = pd / f"{new_ck}.pdf"
                if new_pdf.exists():
                    print("    Updating linked PDF in Zotero")
                    zotero_client.update_linked_attachment_path(
                        key, new_pdf.name, str(new_pdf),
                    )
            any_change = True

        # Rename Inbox PDFs that don't match expected citekey
        if new_ck and doc.get("status") in ("on_remarkable", "awaiting_pdf"):
            inbox = obsidian._inbox_dir()
            if inbox:
                new_inbox = inbox / f"{new_ck}.pdf"
                if not new_inbox.exists():
                    # Search candidates: old citekey variants, title-based name,
                    # and glob for any PDF starting with the surname+word prefix
                    sanitized = obsidian._sanitize_note_name(doc["title"])
                    candidates = []
                    if old_ck and old_ck != new_ck:
                        candidates.append(old_ck)
                        base = old_ck.rsplit("_", 1)[0] if "_" in old_ck else old_ck
                        if base != old_ck:
                            candidates.append(base)
                    # Also try new citekey base without year
                    new_base = new_ck.rsplit("_", 1)[0] if "_" in new_ck else new_ck
                    if new_base != new_ck and new_base not in candidates:
                        candidates.append(new_base)
                    candidates.append(sanitized)

                    found = None
                    for candidate in candidates:
                        old_inbox = inbox / f"{candidate}.pdf"
                        if old_inbox.exists():
                            found = old_inbox
                            break

                    # Fallback: glob for PDFs starting with surname_word prefix
                    # (catches malformed citekeys like "lla_bagel_Dec .pdf")
                    if found is None and "_" in new_ck:
                        parts = new_ck.split("_")
                        if len(parts) >= 2:
                            # Try both old (no accent normalization) and new surname
                            prefixes = set()
                            prefixes.add(f"{parts[0]}_{parts[1]}")
                            # Old surname may differ (e.g. "lla" vs "lala")
                            raw_authors = new_meta.get("authors", [])
                            if raw_authors:
                                import re as _re
                                raw_s = raw_authors[0].split(",")[0].strip()
                                old_s = _re.sub(r"[^a-z]", "", raw_s.lower())
                                if old_s and old_s != parts[0]:
                                    prefixes.add(f"{old_s}_{parts[1]}")
                            for prefix in prefixes:
                                matches = list(inbox.glob(f"{prefix}*.pdf"))
                                # Exclude the target itself
                                matches = [m for m in matches if m.name != new_inbox.name]
                                if len(matches) == 1:
                                    found = matches[0]
                                    break

                    if found is not None:
                        if not any_change:
                            print(f"  [{i}/{total}] \"{title[:50]}\"")
                        found.rename(new_inbox)
                        print(f"    Inbox PDF: {found.name} -> {new_inbox.name}")
                        log.info("Renamed inbox PDF: %s -> %s", found.name, new_inbox.name)
                        print("    Updating linked PDF in Zotero")
                        zotero_client.update_linked_attachment_path(
                            key, new_inbox.name, str(new_inbox),
                        )
                        any_change = True

        # Update title in reading log if it changed
        if old_title != new_title and doc.get("status") == "processed":
            if not any_change:
                print(f"  [{i}/{total}] \"{title[:50]}\"")
            print(f"    Title: {old_title[:50]} -> {new_title[:50]}")
            obsidian.update_reading_log_title(old_title, new_title, citekey=new_ck)
            any_change = True

        doc["metadata"] = new_meta
        doc["title"] = new_title
        doc["authors"] = new_meta.get("authors", doc["authors"])

        if doc.get("status") == "processed":
            obsidian.update_note_frontmatter(doc["title"], new_meta, citekey=new_ck)

        state.save()
        if any_change:
            changed += 1
        elif is_tty:
            # Clear progress line for unchanged papers
            print(f"\r  [{i}/{total}] \"{short}\" \u2713" + " " * 20, end="\r", flush=True)

    # Clear any lingering progress line
    if is_tty:
        print(" " * 80, end="\r", flush=True)
    print(f"  {total} papers checked, {changed} updated.")


def _backfill_highlights(args: list[str]) -> None:
    """Back-propagate highlights to Zotero for already-processed papers.

    Usage: distillate --backfill-highlights [N]
    Processes the last N papers (default: all processed papers).
    """
    from datetime import datetime, timezone

    from distillate import config
    from distillate import renderer
    from distillate import zotero_client
    from distillate.state import State

    zotero_mode = config.is_zotero_reader()
    if not zotero_mode:
        from distillate import remarkable_client

    config.setup_logging()

    if zotero_mode:
        print("Backfill not needed — highlights already in Zotero.")
        return

    limit = int(args[0]) if args else 0
    state = State()
    processed = state.documents_with_status("processed")

    if not processed:
        print("No processed papers to backfill.")
        return

    # Sort by processed_at descending, take last N
    processed.sort(key=lambda d: d.get("processed_at", ""), reverse=True)
    if limit:
        processed = processed[:limit]

    print(f"Back-propagating highlights for {len(processed)} paper(s)...")

    count = 0
    for doc in processed:
        title = doc["title"]
        rm_name = doc["remarkable_doc_name"]
        item_key = doc["zotero_item_key"]

        # Skip if already synced
        if doc.get("highlights_synced_at"):
            print(f"  Skip (already synced): {title[:60]}")
            continue

        # Find any PDF attachment in Zotero (imported or linked)
        att = zotero_client.get_pdf_attachment(item_key)
        if not att:
            att = zotero_client.get_linked_attachment(item_key)
        if not att:
            print(f"  Skip (no PDF attachment): {title[:60]}")
            continue

        print(f"  Processing: {title[:60]}")

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / f"{rm_name}.zip"

            bundle_ok = remarkable_client.download_document_bundle_to(
                config.RM_FOLDER_SAVED, rm_name, zip_path,
            )

            if not bundle_ok or not zip_path.exists():
                log.warning("Could not download bundle for '%s'", title)
                print("    Could not download from reMarkable")
                continue

            positions = renderer.extract_zotero_highlights(zip_path)
            if not positions:
                print("    No highlight positions extracted")
                continue

            ann_keys = zotero_client.create_highlight_annotations(
                att["key"], positions,
            )
            doc["highlights_synced_at"] = datetime.now(timezone.utc).isoformat()
            doc["zotero_annotation_count"] = len(ann_keys)
            state.save()
            count += 1
            print(f"    Created {len(ann_keys)} annotation(s)")

    print(f"\nDone: back-propagated highlights for {count} paper(s).")


def _sync_state() -> None:
    """Upload state.json to a private GitHub Gist for GitHub Actions."""
    import subprocess

    from distillate import config

    config.setup_logging()

    gist_id = config.STATE_GIST_ID
    if not gist_id:
        log.error("STATE_GIST_ID not set — run: gh gist create state.json")
        return

    from distillate.state import STATE_PATH
    if not STATE_PATH.exists():
        log.info("No state.json to sync")
        return

    try:
        subprocess.run(
            ["gh", "gist", "edit", gist_id, "-f", "state.json", str(STATE_PATH)],
            check=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.error("Timed out syncing state to gist %s", gist_id)
        return
    log.info("Synced state.json to gist %s", gist_id)


def _scan_projects() -> None:
    """Scan all tracked ML projects for new experiments."""
    from distillate import config
    from distillate.state import State

    config.setup_logging()
    state = State()

    if not config.EXPERIMENTS_ENABLED:
        print("Experiments not enabled. Set EXPERIMENTS_ENABLED=true in your .env")
        return

    projects = state.projects
    if not projects:
        print("No projects tracked yet. Use the agent to scan a project:")
        print('  distillate "scan project at ~/Code/Research/my-project"')
        return

    from distillate.experiments import (
        generate_notebook,
        load_enrichment_cache,
        update_project,
    )
    from distillate.obsidian import write_experiment_notebook

    updated = 0
    for proj_id, proj in projects.items():
        print(f"  Scanning {proj.get('name', proj_id)}...")
        if update_project(proj, state):
            proj_path = proj.get("path", "")
            enrichment = load_enrichment_cache(Path(proj_path)) if proj_path else {}
            notebook_md = generate_notebook(proj, enrichment=enrichment)
            write_experiment_notebook(proj, notebook_md)
            updated += 1

    if updated:
        state.save()
        print(f"  Updated {updated} project(s).")
    else:
        print("  No changes detected.")


def _install_hooks(args: list[str]) -> None:
    """Install Claude Code hooks for experiment capture into a project."""
    import json as json_mod
    import shutil

    if not args:
        print("Usage: distillate --install-hooks <path>")
        return

    project_path = Path(args[0]).resolve()
    if not project_path.is_dir():
        print(f"Not a directory: {project_path}")
        return

    # 1. Create .distillate/ directory
    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    print(f"  Created {distillate_dir}/")

    # 2. Copy REPORTING.md
    reporting_src = Path(__file__).parent / "autoresearch" / "REPORTING.md"
    if reporting_src.exists():
        reporting_dst = distillate_dir / "REPORTING.md"
        shutil.copy2(reporting_src, reporting_dst)
        print(f"  Copied REPORTING.md to {reporting_dst}")

    # 3. Merge hook config into .claude/settings.json
    hooks_src = Path(__file__).parent / "autoresearch" / "hooks.json"
    if not hooks_src.exists():
        print("  Warning: hooks.json template not found")
        return

    hook_config = json_mod.loads(hooks_src.read_text(encoding="utf-8"))

    claude_dir = project_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_file = claude_dir / "settings.json"

    existing: dict = {}
    if settings_file.exists():
        try:
            existing = json_mod.loads(settings_file.read_text(encoding="utf-8"))
        except json_mod.JSONDecodeError:
            pass

    # Merge hooks (don't overwrite existing hooks)
    existing_hooks = existing.setdefault("hooks", {})
    for event_type, hook_list in hook_config.get("hooks", {}).items():
        existing_entries = existing_hooks.setdefault(event_type, [])
        existing_commands = {e.get("command", "") for e in existing_entries}
        for hook in hook_list:
            if hook.get("command", "") not in existing_commands:
                existing_entries.append(hook)

    settings_file.write_text(
        json_mod.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  Updated {settings_file}")
    print("  Done! Hooks will capture experiments in this directory.")


# ---------------------------------------------------------------------------
# Experiment launcher commands
# ---------------------------------------------------------------------------

def _new_experiment(args: list[str]) -> None:
    """Scaffold a new experiment from a template (interactive wizard)."""
    from distillate import config
    from distillate.experiments import slugify
    from distillate.launcher import (
        import_template,
        list_templates,
        scaffold_experiment,
    )
    from distillate.state import State

    templates = list_templates()

    # If template name given as argument, use it
    template_name = None
    if args and not args[0].startswith("-"):
        template_name = args[0]
        # Check if it exists
        if not any(t["name"] == template_name for t in templates):
            # Maybe it's a path to import as a template
            candidate = Path(args[0]).expanduser().resolve()
            if candidate.is_dir() and (candidate / "PROMPT.md").exists():
                print(f"  Importing {candidate.name} as a template...")
                template_name = import_template(candidate)
                templates = list_templates()
            else:
                print(f"  Template '{template_name}' not found.")
                if templates:
                    print("  Available templates:")
                    for t in templates:
                        data = " (has data/)" if t["has_data"] else ""
                        print(f"    {t['name']}{data} — {t['prompt_lines']} lines")
                else:
                    print("  No templates available. Import one:")
                    print("    distillate --new-experiment /path/to/experiment")
                return

    if not template_name:
        if not templates:
            print("  No templates available yet.")
            print("  Import an experiment directory as a template:")
            print("    distillate --new-experiment /path/to/experiment")
            return

        print("\n  Available templates:")
        for i, t in enumerate(templates, 1):
            data = " (has data/)" if t["has_data"] else ""
            print(f"    {i}. {t['name']}{data} — {t['prompt_lines']} lines")

        try:
            choice = input("\n  Select template (number): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(templates):
                template_name = templates[idx]["name"]
            else:
                print("  Invalid choice.")
                return
        except (ValueError, EOFError, KeyboardInterrupt):
            print()
            return

    # Name
    name = _opt("--name")
    if not name:
        try:
            default = template_name
            name = input(f"  Experiment name [{default}]: ").strip() or default
        except (EOFError, KeyboardInterrupt):
            print()
            return

    # Target directory
    target = _opt("--target")
    if not target:
        if config.EXPERIMENTS_ROOT:
            default_target = str(Path(config.EXPERIMENTS_ROOT) / slugify(name))
        else:
            default_target = str(Path.home() / "experiments" / slugify(name))
        try:
            target = input(f"  Target directory [{default_target}]: ").strip() or default_target
        except (EOFError, KeyboardInterrupt):
            print()
            return

    target_path = Path(target).expanduser().resolve()

    try:
        result = scaffold_experiment(template_name, target_path, name=name)
        print(f"\n  Scaffolded experiment at {result}")
        print(f"  - PROMPT.md copied from template")
        print(f"  - .distillate/ created with REPORTING.md")
        print(f"  - Claude Code hooks installed")
        print(f"  - git initialized")

        # Register in state
        state = State()
        project_id = slugify(name)
        if not state.has_project(project_id):
            state.add_project(
                project_id=project_id,
                name=name.replace("-", " ").title() if name == slugify(name) else name,
                path=str(result),
            )
            state.update_project(project_id, template=template_name)
            state.save()
            print(f"  - Registered as project '{project_id}'")

        print(f"\n  Launch it:")
        print(f"    distillate --launch {project_id}")

    except (FileNotFoundError, FileExistsError) as e:
        print(f"  Error: {e}")


def _launch_experiment(args: list[str]) -> None:
    """Launch an auto-research session for an experiment."""
    from distillate.launcher import launch_experiment
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --launch <name|path> [--host <ssh_host>] [--model <model>] [--turns <N>]")
        return

    query = args[0]
    host = _opt("--host")
    model = _opt("--model") or "claude-sonnet-4-5-20250929"
    turns = int(_opt("--turns") or "100")

    state = State()

    # Resolve: try project name/ID first, then path
    proj = state.find_project(query)
    if proj:
        project_path = Path(proj["path"])
    else:
        project_path = Path(query).expanduser().resolve()
        if not project_path.is_dir():
            print(f"  No project found matching '{query}' and path doesn't exist.")
            return

    try:
        session_data = launch_experiment(
            project_path,
            host=host,
            model=model,
            max_turns=turns,
            project=proj,
        )

        # Save session in state
        if proj:
            state.add_session(proj["id"], session_data["session_id"], session_data)
            state.save()

        tmux_name = session_data["tmux_session"]
        print(f"\n  Launched experiment session: {tmux_name}")
        print(f"  Model: {model} | Max turns: {turns}")
        if host:
            print(f"  Host: {host}")
        print(f"\n  Attach to session:")
        print(f"    distillate --attach {query}")
        print(f"\n  Stop session:")
        print(f"    distillate --stop {query}")

    except (FileNotFoundError, RuntimeError) as e:
        print(f"  Error: {e}")


def _list_experiments() -> None:
    """List all tracked experiments with status and key insights."""
    from distillate.experiments import load_enrichment_cache
    from distillate.launcher import refresh_session_statuses
    from distillate.state import State

    state = State()
    projects = state.projects

    if not projects:
        print("  No experiments tracked yet.")
        print("  Scaffold one: distillate --new-experiment")
        return

    # Refresh session statuses
    changed = refresh_session_statuses(state)
    if changed:
        state.save()

    # Print table header
    print()
    print(f"  {'#':>3}  {'Name':<22} {'Status':<12} {'Runs':>5}  {'Best Metric':<20} {'Sessions'}")
    print(f"  {'─' * 3}  {'─' * 22} {'─' * 12} {'─' * 5}  {'─' * 20} {'─' * 12}")

    insights_by_proj: list[tuple[str, dict]] = []

    for proj_id, proj in projects.items():
        idx = state.project_index_of(proj_id)
        name = proj.get("name", proj_id)[:22]
        status = proj.get("status", "tracking")
        runs = proj.get("runs", {})
        run_count = len(runs)

        # Find best metric
        best_metric = ""
        for run in runs.values():
            results = run.get("results", {})
            for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
                       "best_val_acc", "f1", "loss", "val_bpb", "rmse"):
                if k in results:
                    val = results[k]
                    if isinstance(val, float):
                        best_metric = f"{k}: {val:.4f}"
                    else:
                        best_metric = f"{k}: {val}"
                    break
            if best_metric:
                break

        # Count active sessions
        sessions = proj.get("sessions", {})
        active = sum(1 for s in sessions.values() if s.get("status") == "running")
        sess_str = f"{active} active" if active else "0 active"

        print(f"  {idx:>3}  {name:<22} {status:<12} {run_count:>5}  {best_metric:<20} {sess_str}")

        # Load enrichment for insights
        proj_path = proj.get("path", "")
        if proj_path:
            cache = load_enrichment_cache(Path(proj_path))
            enr = cache.get("enrichment", cache)
            project_insights = enr.get("project", {})
            if project_insights:
                insights_by_proj.append((proj.get("name", proj_id), project_insights))

    # Print research insights below the table
    if insights_by_proj:
        print()
        print(f"  {'─' * 60}")
        for proj_name, insights in insights_by_proj:
            breakthrough = insights.get("key_breakthrough", "")
            lessons = insights.get("lessons_learned", [])
            if breakthrough or lessons:
                print(f"\n  {_bold(proj_name)} — Research Insights")
                if breakthrough:
                    print(f"  {_dim('Breakthrough:')} {breakthrough}")
                if lessons:
                    print(f"  {_dim('Lessons:')}")
                    for i, lesson in enumerate(lessons, 1):
                        print(f"    {i}. {lesson}")

    print()


def _attach_experiment(args: list[str]) -> None:
    """Attach to a running experiment session."""
    from distillate.launcher import attach_session
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --attach <name>")
        return

    query = args[0]
    state = State()

    proj = state.find_project(query)
    if not proj:
        print(f"  No project found matching '{query}'.")
        return

    # Find running session
    sessions = proj.get("sessions", {})
    running = [(sid, s) for sid, s in sessions.items() if s.get("status") == "running"]

    if not running:
        print(f"  No running sessions for '{proj.get('name', query)}'.")
        return

    # Attach to the most recent running session
    sess_id, sess = running[-1]
    tmux_name = sess.get("tmux_session", "")
    host = sess.get("host")

    try:
        attach_session(tmux_name, host)
        print(f"  Opened terminal attached to {tmux_name}")
    except RuntimeError as e:
        print(f"  Error: {e}")


def _stop_experiment(args: list[str]) -> None:
    """Stop a running experiment session."""
    from datetime import datetime, timezone

    from distillate.launcher import stop_session
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --stop <name>")
        return

    query = args[0]
    state = State()

    proj = state.find_project(query)
    if not proj:
        print(f"  No project found matching '{query}'.")
        return

    # Find running sessions
    sessions = proj.get("sessions", {})
    running = [(sid, s) for sid, s in sessions.items() if s.get("status") == "running"]

    if not running:
        print(f"  No running sessions for '{proj.get('name', query)}'.")
        return

    for sess_id, sess in running:
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")
        ok = stop_session(tmux_name, host)
        if ok:
            state.update_session(
                proj["id"], sess_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            print(f"  Stopped session {tmux_name}")
        else:
            print(f"  Failed to stop session {tmux_name}")

    state.save()


def _campaign(args: list[str]) -> None:
    """Manage autonomous campaign loops: start, status, stop."""
    import signal
    import threading
    from datetime import datetime, timezone

    from distillate.cli import _bold, _dim
    from distillate.launcher import run_campaign, should_continue
    from distillate.state import State

    if not args:
        print("Usage: distillate --campaign start|status|stop <project>")
        return

    action = args[0]
    if action not in ("start", "status", "stop"):
        print(f"Unknown campaign action: {action}")
        print("Usage: distillate --campaign start|status|stop <project>")
        return

    if len(args) < 2:
        print(f"Usage: distillate --campaign {action} <project>")
        return

    query = args[1]
    state = State()
    proj = state.find_project(query)
    if not proj:
        print(f"  No project found matching '{query}'.")
        return

    proj_name = proj.get("name", query)

    # --- status ---
    if action == "status":
        campaign = proj.get("campaign", {})
        if not campaign or not campaign.get("status"):
            print(f"  No campaign running for '{proj_name}'.")
            return
        print()
        print(f"  Campaign: {_bold(proj_name)}")
        print(f"  Status:   {campaign.get('status', '?')}")
        print(f"  Sessions: {campaign.get('sessions_launched', 0)}"
              f" / {campaign.get('budget', {}).get('max_sessions', '?')}")
        if campaign.get("objective"):
            print(f"  Objective: {campaign['objective']}")
        stop_reason = campaign.get("stop_reason")
        if stop_reason:
            print(f"  Stopped:  {stop_reason}")
        # Show best metric from kept runs
        runs = proj.get("runs", {})
        best_val = None
        best_name = None
        for run in runs.values():
            if run.get("status") != "keep" and run.get("decision") != "keep":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    if best_val is None or v > best_val:
                        best_val = v
                        best_name = k
        if best_val is not None:
            print(f"  Best:     {best_name}={best_val}")
        print()
        return

    # --- stop ---
    if action == "stop":
        campaign = proj.get("campaign", {})
        if not campaign or campaign.get("status") not in ("running", "paused"):
            print(f"  No active campaign for '{proj_name}'.")
            return
        campaign["status"] = "stopped"
        campaign["stop_reason"] = "user_stopped"
        campaign["completed_at"] = datetime.now(timezone.utc).isoformat()
        state.update_project(proj["id"], campaign=campaign)
        state.save()
        print(f"  Campaign stopped for '{proj_name}'.")
        return

    # --- start ---
    if not proj.get("goals"):
        print(f"  Cannot start campaign: '{proj_name}' has no goals set.")
        print("  Set goals first with the agent REPL (update_goals tool).")
        return

    if not should_continue(proj):
        print(f"  All goals for '{proj_name}' appear to be met already.")
        return

    existing = proj.get("campaign", {})
    if existing.get("status") == "running":
        print(f"  Campaign already running for '{proj_name}'.")
        return

    max_sessions = 10
    model = "claude-sonnet-4-5-20250929"
    max_turns = 100

    # Parse optional flags
    for i, a in enumerate(args[2:], start=2):
        if a == "--model" and i + 1 < len(args):
            model = args[i + 1]
        elif a == "--turns" and i + 1 < len(args):
            max_turns = int(args[i + 1])
        elif a.isdigit():
            max_sessions = int(a)

    campaign = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "objective": "",
        "budget": {"max_sessions": max_sessions, "max_hours": 8},
        "model": model,
        "max_turns": max_turns,
        "sessions_launched": 0,
        "current_session_id": None,
        "completed_at": None,
        "stop_reason": None,
    }
    state.update_project(proj["id"], campaign=campaign, auto_continue=True)
    state.save()

    stop_flag = threading.Event()

    def _on_sigint(sig, frame):
        print("\n  Pausing campaign (finishing current session)...")
        stop_flag.set()

    old_handler = signal.signal(signal.SIGINT, _on_sigint)

    def _on_event(event):
        etype = event.get("type", "")
        ts = event.get("ts", "")[:19]
        if etype == "campaign_run_started":
            n = event.get("sessions_launched", 0)
            remaining = event.get("budget_remaining", "?")
            print(f"  [{ts}] Session #{n} started ({remaining} remaining)")
        elif etype == "goal_reached":
            print(f"  [{ts}] \033[1;32mGoal reached!\033[0m")
        elif etype == "campaign_completed":
            reason = event.get("stop_reason", "?")
            print(f"  [{ts}] Campaign completed: {reason}")

    print()
    print(f"  Starting campaign for {_bold(proj_name)}")
    print(f"  Budget: {max_sessions} sessions, model: {model}")
    print(f"  Press Ctrl+C to pause\n")

    try:
        result = run_campaign(
            proj["id"],
            state,
            max_sessions=max_sessions,
            model=model,
            max_turns=max_turns,
            on_event=_on_event,
            stop_flag=stop_flag,
        )
    finally:
        signal.signal(signal.SIGINT, old_handler)

    reason = result.get("stop_reason", "unknown")
    launched = result.get("sessions_launched", 0)
    print(f"\n  Campaign ended: {reason} ({launched} session(s) launched)")

    # Update campaign status in state
    state.reload()
    p = state.get_project(proj["id"])
    if p:
        c = dict(p.get("campaign", {}))
        c["status"] = "completed" if reason != "user_stopped" else "paused"
        c["stop_reason"] = reason
        c["completed_at"] = datetime.now(timezone.utc).isoformat()
        state.update_project(proj["id"], campaign=c)
        state.save()


def _steer(args: list[str]) -> None:
    """Write steering instructions for the next experiment session."""
    from distillate.launcher import write_steering
    from distillate.state import State

    if len(args) < 2:
        print("Usage: distillate --steer <project> \"text\"")
        return

    query = args[0]
    text = " ".join(args[1:])

    state = State()
    proj = state.find_project(query)
    if not proj:
        print(f"  No project found matching '{query}'.")
        return

    proj_path = proj.get("path", "")
    if not proj_path:
        print(f"  Project '{proj.get('name', query)}' has no path set.")
        return

    path = write_steering(Path(proj_path), text)
    print(f"  Steering written: {path}")
    preview = text[:120] + ("..." if len(text) > 120 else "")
    print(f"  → {preview}")


def _sparkline(values: list[float], width: int = 8) -> str:
    """Render a list of floats as a Unicode sparkline."""
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = hi - lo if hi != lo else 1.0
    recent = values[-width:]
    return "".join(bars[min(int((v - lo) / span * (len(bars) - 1)), len(bars) - 1)]
                   for v in recent)


def _tail_jsonl(path: Path, offset: int) -> tuple[list[dict], int]:
    """Read new lines from a JSONL file starting at byte *offset*.

    Returns (parsed_events, new_offset).
    """
    if not path.exists():
        return [], offset
    try:
        size = path.stat().st_size
        if size <= offset:
            return [], offset
        with open(path, "r", encoding="utf-8") as f:
            f.seek(offset)
            lines = f.readlines()
        new_offset = path.stat().st_size
    except OSError:
        return [], offset

    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, new_offset


def _format_watch_event(event: dict) -> str | None:
    """Format a JSONL event for terminal display. Returns colored string or None."""
    etype = event.get("type", "")
    ts = event.get("ts", "")[:19]

    if etype == "metric_update":
        metric = event.get("metric", "?")
        value = event.get("value", "?")
        history = event.get("history", [])
        spark = _sparkline(history) if history else ""
        return f"  \033[36m[{ts}]\033[0m {metric}={value} {spark}"

    if etype == "run_completed":
        run_id = event.get("run_id", "?")
        results = event.get("results", {})
        status = event.get("status", "?")
        metrics_str = ", ".join(f"{k}={v}" for k, v in results.items()
                                if isinstance(v, (int, float)))
        color = "\033[32m" if status == "keep" else "\033[33m"
        return f"  {color}[{ts}]\033[0m Run {run_id}: {metrics_str} [{status}]"

    if etype == "session_end":
        reason = event.get("stop_reason", event.get("reason", "?"))
        return f"  \033[2m[{ts}]\033[0m Session ended: {reason}"

    if etype == "goal_reached":
        return f"  \033[1;32m[{ts}] Goal reached!\033[0m"

    if etype == "campaign_run_started":
        n = event.get("sessions_launched", 0)
        remaining = event.get("budget_remaining", "?")
        return f"  \033[34m[{ts}]\033[0m Campaign session #{n} started ({remaining} remaining)"

    if etype == "campaign_completed":
        reason = event.get("stop_reason", "?")
        return f"  \033[1m[{ts}]\033[0m Campaign completed: {reason}"

    # Generic fallback for unknown event types
    return f"  \033[2m[{ts}]\033[0m {etype}"


def _watch(args: list[str]) -> None:
    """Watch an experiment repo and regenerate notebooks on changes.

    Also tails events.jsonl, runs.jsonl, and live_metrics.jsonl for
    live event display with sparklines.
    """
    import time
    import webbrowser

    from distillate import config
    from distillate.experiments import (
        generate_html_notebook,
        generate_notebook,
        load_enrichment_cache,
        scan_project,
        watch_project_artifacts,
    )

    config.setup_logging()

    if not args:
        print("Usage: distillate --watch <path>")
        return

    project_path = Path(args[0]).resolve()
    if not project_path.is_dir():
        print(f"Not a directory: {project_path}")
        return

    print(f"  Watching {project_path}...")

    # Initial scan
    project = scan_project(project_path)
    if "error" in project:
        print(f"  Error: {project['error']}")
        return

    runs_count = len(project.get("runs", {}))
    print(f"  Found {runs_count} experiment(s)")

    # Load LLM enrichment (insights, lessons learned)
    enrichment = load_enrichment_cache(project_path)

    # Generate initial notebook
    html = generate_html_notebook(project, enrichment=enrichment)
    html_path = project_path / ".distillate" / "notebook.html"
    html_path.parent.mkdir(exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    md = generate_notebook(project, enrichment=enrichment)
    md_path = project_path / ".distillate" / "notebook.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"  Generated notebook: {html_path}")
    webbrowser.open(f"file://{html_path}")

    # Initialize JSONL tail offsets
    distillate_dir = project_path / ".distillate"
    tail_files = {
        "events": distillate_dir / "events.jsonl",
        "runs": distillate_dir / "runs.jsonl",
        "metrics": distillate_dir / "live_metrics.jsonl",
    }
    offsets: dict[str, int] = {}
    for key, fpath in tail_files.items():
        offsets[key] = fpath.stat().st_size if fpath.exists() else 0

    # Watch loop
    print("  Watching for changes (Ctrl+C to stop)...")
    try:
        while True:
            time.sleep(5)

            # Tail JSONL files for live events
            for key, fpath in tail_files.items():
                new_events, new_offset = _tail_jsonl(fpath, offsets[key])
                offsets[key] = new_offset
                for evt in new_events:
                    line = _format_watch_event(evt)
                    if line:
                        print(line)

            # Check for artifact changes (notebook regen)
            new_data = watch_project_artifacts(project_path)
            if new_data:
                print(f"  Detected {len(new_data)} new event(s), regenerating...")
                project = scan_project(project_path)
                enrichment = load_enrichment_cache(project_path)
                if "error" not in project:
                    html = generate_html_notebook(project, enrichment=enrichment)
                    html_path.write_text(html, encoding="utf-8")
                    md = generate_notebook(project, enrichment=enrichment)
                    md_path.write_text(md, encoding="utf-8")
                    new_runs = len(project.get("runs", {}))
                    print(f"  Updated: {new_runs} experiment(s)")
    except KeyboardInterrupt:
        print("\n  Stopped watching.")


def _status() -> None:
    """Print a quick status overview to the terminal."""
    from datetime import datetime, timedelta, timezone

    from distillate import config
    from distillate.state import State

    config.setup_logging()

    from distillate.state import STATE_PATH
    if not STATE_PATH.exists() and not config.ENV_PATH.exists():
        print("\n  No experiments or papers tracked yet. Run 'distillate --init' to get started.\n")
        return

    state = State()
    now = datetime.now(timezone.utc)

    print()
    print("  Distillate")
    print("  " + "\u2500" * 40)

    # Experiments (shown first)
    projects = state.projects
    if projects:
        from distillate.launcher import refresh_session_statuses
        changed = refresh_session_statuses(state)
        if changed:
            state.save()

        n_proj = len(projects)
        total_runs = sum(len(p.get("runs", {})) for p in projects.values())
        active = sum(
            1 for p in projects.values()
            for s in p.get("sessions", {}).values()
            if s.get("status") == "running"
        )
        exp_line = f"{n_proj} experiment{'s' if n_proj != 1 else ''} \u00b7 {total_runs} runs"
        if active:
            exp_line += f" \u00b7 {active} running"
        print(f"  Lab:       {exp_line}")

        for proj in list(projects.values())[:5]:
            runs = proj.get("runs", {})
            sessions = proj.get("sessions", {})
            sess_active = sum(1 for s in sessions.values() if s.get("status") == "running")
            status = "\U0001F7E2 running" if sess_active else proj.get("status", "tracking")
            print(f"    {proj.get('name', '?')} \u2014 {len(runs)} runs, {status}")
        if len(projects) > 5:
            print(f"    {_dim(f'... and {len(projects) - 5} more')}")
        print()

    # Queue
    _q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
    queue = state.documents_with_status(_q_status)
    oldest_days = 0
    if queue:
        oldest_uploaded = min(d.get("uploaded_at", "") for d in queue)
        if oldest_uploaded:
            try:
                oldest_days = (now - datetime.fromisoformat(oldest_uploaded)).days
            except (ValueError, TypeError):
                pass
    queue_str = f"{len(queue)} paper{'s' if len(queue) != 1 else ''} waiting"
    if oldest_days:
        queue_str += f" (oldest: {oldest_days} days)"
    print(f"  Queue:     {queue_str}")

    # List queue papers (up to 10)
    if queue:
        sorted_queue = sorted(queue, key=lambda d: d.get("uploaded_at", ""), reverse=True)
        for doc in sorted_queue[:10]:
            idx = state.index_of(doc["zotero_item_key"])
            ck = doc.get("metadata", {}).get("citekey", "")
            # Date
            date_str = ""
            uploaded = doc.get("uploaded_at", "")
            if uploaded:
                try:
                    dt = datetime.fromisoformat(uploaded)
                    date_str = dt.strftime("%b %-d")
                except (ValueError, TypeError):
                    pass
            # Stats
            stats = []
            engagement = doc.get("engagement", 0)
            highlight_count = doc.get("highlight_count", 0)
            if engagement:
                stats.append(f"{engagement}% engaged")
            if highlight_count:
                stats.append(f"{highlight_count} highlights")
            stats_str = f" ({', '.join(stats)})" if stats else ""
            detail = f"{date_str}{stats_str}"
            if ck:
                detail = f"{detail} - {ck}" if detail else ck
            print(f"    {_dim(f'[{idx}]')} {_bold(doc['title'])}")
            if detail:
                print(f"        {_dim(detail)}")
        if len(queue) > 10:
            print(f"    {_dim(f'... and {len(queue) - 10} more')}")

    # Ready to process (in Read/ on reMarkable)
    if not config.is_zotero_reader():
        try:
            from distillate import remarkable_client
            read_docs = remarkable_client.list_folder(config.RM_FOLDER_READ)
            if read_docs:
                print(f"  Ready:     {len(read_docs)} paper{'s' if len(read_docs) != 1 else ''} in Read/")
                for name in read_docs[:5]:
                    print(f"    - {name}")
                if len(read_docs) > 5:
                    print(f"    ... and {len(read_docs) - 5} more")
        except Exception:
            pass  # rmapi unavailable — skip

    # Promoted (show last 3)
    promoted = state.promoted_papers
    if promoted:
        entries = []
        for key in promoted[-3:]:
            doc = state.get_document(key)
            if doc:
                idx = state.index_of(key)
                entries.append(f"{_dim(f'[{idx}]')} {_bold(doc['title'])}")
        if entries:
            print(f"  Promoted:  {entries[0]}")
            for e in entries[1:]:
                print(f"             {e}")

    # Last sync
    last_poll = state.last_poll_timestamp
    if last_poll:
        try:
            poll_dt = datetime.fromisoformat(last_poll)
            delta = now - poll_dt
            if delta.total_seconds() < 60:
                ago = "just now"
            elif delta.total_seconds() < 3600:
                mins = int(delta.total_seconds() / 60)
                ago = f"{mins} min{'s' if mins != 1 else ''} ago"
            elif delta.total_seconds() < 86400:
                hours = int(delta.total_seconds() / 3600)
                ago = f"{hours} hour{'s' if hours != 1 else ''} ago"
            else:
                days = delta.days
                ago = f"{days} day{'s' if days != 1 else ''} ago"
            print(f"  {_dim(f'Last sync: {ago}')}")
        except (ValueError, TypeError):
            pass
    else:
        print(f"  {_dim('Last sync: never')}")

    # Reading stats
    week_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    month_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    week_papers = state.documents_processed_since(week_ago.isoformat())
    month_papers = state.documents_processed_since(month_ago.isoformat())

    def _stats_line(papers, label):
        count = len(papers)
        pages = sum(d.get("page_count", 0) for d in papers)
        words = sum(d.get("highlight_word_count", 0) for d in papers)
        parts = [f"read {count} paper{'s' if count != 1 else ''}"]
        if pages:
            parts.append(f"{pages:,} pages")
        if words:
            parts.append(f"{words:,} words highlighted")
        sep = " \u00b7 "
        return f"{label}: {sep.join(parts)}"

    print()
    print(f"  {_dim(_stats_line(week_papers, 'This week'))}")
    print(f"  {_dim(_stats_line(month_papers, 'This month'))}")

    # Awaiting PDF (show titles with guidance)
    awaiting = state.documents_with_status("awaiting_pdf")
    if awaiting:
        print()
        print(f"  Awaiting PDF: {len(awaiting)} paper{'s' if len(awaiting) != 1 else ''}")
        for doc in awaiting:
            print(f"    - {doc['title']}")
        print("    Sync the PDF in Zotero, then re-run distillate.")

    # Pending promotions
    pending_promo = state.pending_promotions
    if pending_promo:
        titles = [state.get_document(k)["title"] for k in pending_promo if state.get_document(k)]
        if titles:
            print()
            print(f"  Pending promotions: {len(titles)}")
            for t in titles:
                print(f"    - {t}")

    # Total processed
    processed = state.documents_with_status("processed")
    print()
    print(f"  Total: {len(processed)} papers read, {len(queue)} in queue")
    if not queue and not awaiting:
        print("  Hint: run 'distillate --import' to add existing papers")

    # Config health
    import shutil
    problems = []
    optional = []
    if not config.OBSIDIAN_VAULT_PATH and not config.OUTPUT_PATH:
        problems.append("No output configured (set OBSIDIAN_VAULT_PATH or OUTPUT_PATH)")
    elif config.OBSIDIAN_VAULT_PATH and not Path(config.OBSIDIAN_VAULT_PATH).is_dir():
        problems.append(f"Vault path missing: {config.OBSIDIAN_VAULT_PATH}")
    if not config.is_zotero_reader() and not shutil.which("rmapi"):
        problems.append("rmapi not found (reMarkable sync will fail)")
    if not config.ANTHROPIC_API_KEY:
        optional.append("AI summaries (set ANTHROPIC_API_KEY)")
    if not config.RESEND_API_KEY:
        optional.append("Email digest (set RESEND_API_KEY)")

    if problems or optional:
        print()
        print("  Config:")
        for p in problems:
            print(f"    - {p}")
        for o in optional:
            print(f"    - Optional: {o}")
    print()


def _list() -> None:
    """List all tracked papers grouped by status."""
    from distillate import config
    from distillate.state import State

    config.setup_logging()
    state = State()

    if config.is_zotero_reader():
        groups = [
            ("Reading", "tracked"),
            ("Processing", "processing"),
            ("Awaiting PDF", "awaiting_pdf"),
            ("Processed", "processed"),
        ]
    else:
        groups = [
            ("On reMarkable", "on_remarkable"),
            ("Processing", "processing"),
            ("Awaiting PDF", "awaiting_pdf"),
            ("Processed", "processed"),
        ]

    total = 0
    print()
    for label, status in groups:
        docs = state.documents_with_status(status)
        if not docs:
            continue
        total += len(docs)
        print(f"  {label} ({len(docs)})")
        for doc in docs:
            idx = state.index_of(doc["zotero_item_key"])
            ck = doc.get("metadata", {}).get("citekey", "")
            date_str = ""
            if status == "processed" and doc.get("processed_at"):
                date_str = doc["processed_at"][:10]
            elif doc.get("uploaded_at"):
                date_str = doc["uploaded_at"][:10]
            detail = " \u00b7 ".join(p for p in [date_str, ck] if p)
            idx_str = f"{_dim(f'[{idx}]')} " if idx else ""
            print(f"    {idx_str}{doc['title']}")
            if detail:
                print(f"      {_dim(detail)}")
        if status == "awaiting_pdf":
            print("    Sync the PDF in Zotero, then re-run distillate.")
        print()

    if total == 0:
        print("  No papers tracked yet.")
        print("  Run 'distillate --import' to add existing papers.")
        print()


def _queue() -> None:
    """List all papers in the reading queue, paged with less."""
    import shutil
    import subprocess
    import tempfile

    from distillate import config
    from distillate.state import State

    config.setup_logging()
    state = State()

    promoted_set = set(state.promoted_papers)

    # Gather unread papers (everything not processed)
    if config.is_zotero_reader():
        unread_statuses = ("tracked", "processing", "awaiting_pdf")
    else:
        unread_statuses = ("on_remarkable", "processing", "awaiting_pdf")

    unread = []
    for key, doc in state.documents.items():
        if doc.get("status") in unread_statuses:
            unread.append((key, doc))

    # Also include processed papers (read) at the end
    read = []
    for key, doc in state.documents.items():
        if doc.get("status") == "processed":
            read.append((key, doc))

    total = len(unread) + len(read)
    if total == 0:
        print("\n  No papers tracked yet.")
        print("  Run 'distillate --import' to add papers.\n")
        return

    lines = []
    lines.append("")
    lines.append(f"  Papers queue \u2014 {len(unread)} unread \u00b7 {len(read)} read \u00b7 {total} total")
    lines.append("  " + "\u2500" * 52)

    # Promoted first, then unread, then read
    promoted_docs = [(k, d) for k, d in unread if k in promoted_set]
    other_unread = [(k, d) for k, d in unread if k not in promoted_set]

    if promoted_docs:
        lines.append("")
        lines.append(f"  \u2605 Promoted ({len(promoted_docs)})")
        for key, doc in promoted_docs:
            _format_queue_entry(lines, key, doc, state, promoted=True)

    if other_unread:
        lines.append("")
        lines.append(f"  Unread ({len(other_unread)})")
        for key, doc in other_unread:
            _format_queue_entry(lines, key, doc, state)

    if read:
        lines.append("")
        lines.append(f"  Read ({len(read)})")
        for key, doc in read:
            _format_queue_entry(lines, key, doc, state, is_read=True)

    lines.append("")

    output = "\n".join(lines)

    # Pipe through less if output is longer than terminal
    term_height = shutil.get_terminal_size().lines
    if sys.stdout.isatty() and output.count("\n") > term_height - 2:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write(output)
                f.flush()
                subprocess.run(["less", "-R", f.name])
        except (FileNotFoundError, OSError):
            print(output)
    else:
        print(output)


def _format_queue_entry(
    lines: list,
    key: str,
    doc: dict,
    state,
    *,
    promoted: bool = False,
    is_read: bool = False,
) -> None:
    """Format a single paper entry for the queue listing."""
    idx = state.index_of(key)
    meta = doc.get("metadata", {})
    ck = meta.get("citekey", "")
    title = doc.get("title", "Untitled")
    pages = meta.get("numPages") or meta.get("page_count", 0)

    idx_str = f"[{idx}]" if idx else ""
    star = "\u2605 " if promoted else ""

    # Date
    if is_read and doc.get("processed_at"):
        date_str = doc["processed_at"][:10]
    elif doc.get("uploaded_at"):
        date_str = doc["uploaded_at"][:10]
    else:
        date_str = ""

    # Detail line
    detail_parts = []
    if date_str:
        detail_parts.append(date_str)
    if ck:
        detail_parts.append(ck)
    if pages:
        detail_parts.append(f"{pages} pp.")
    citations = meta.get("citation_count", 0)
    if citations:
        detail_parts.append(f"{citations:,} cit.")
    detail = " \u00b7 ".join(detail_parts)

    if sys.stdout.isatty():
        lines.append(f"    {_dim(idx_str)} {star}{title}")
    else:
        lines.append(f"    {idx_str} {star}{title}")

    if detail:
        lines.append(f"      {_dim(detail) if sys.stdout.isatty() else detail}")

    # Summary (truncated)
    summary = doc.get("summary", "")
    if summary:
        trunc = summary[:120] + ("\u2026" if len(summary) > 120 else "")
        lines.append(f"      {_dim(trunc) if sys.stdout.isatty() else trunc}")


def _remove(args: list[str]) -> None:
    """Remove a paper from tracking by title substring match."""
    from distillate import config
    from distillate.pipeline import _find_papers
    from distillate.state import State

    config.setup_logging()

    if not args:
        print("Usage: distillate --remove <title|citekey|index>")
        return

    query = " ".join(args)
    state = State()
    matches = _find_papers(query, state)

    if not matches:
        print(f"\n  No papers matching '{query}'.\n")
        return

    if len(matches) == 1:
        key, doc = matches[0]
        print(f"\n  Found: {doc['title']} [{doc['status']}]")
        confirm = input("  Remove this paper from tracking? [y/N] ").strip().lower()
        if confirm == "y":
            state.remove_document(key)
            state.save()
            print("  Removed.\n")
        else:
            print("  Cancelled.\n")
        return

    print(f"\n  Found {len(matches)} papers matching '{query}':\n")
    for i, (key, doc) in enumerate(matches, 1):
        print(f"    {i}. {doc['title']} [{doc['status']}]")
    print()
    choice = input("  Remove which? (number, or Enter to cancel) ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(matches):
        key, doc = matches[int(choice) - 1]
        state.remove_document(key)
        state.save()
        print(f"  Removed: {doc['title']}\n")
    else:
        print("  Cancelled.\n")


def _print_digest() -> None:
    """Print a reading digest to the terminal."""
    from datetime import datetime, timedelta, timezone

    from distillate import config
    from distillate.state import State

    config.setup_logging()
    state = State()

    now = datetime.now(timezone.utc)
    since = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)).isoformat()
    papers = state.documents_processed_since(since)

    if not papers:
        print("  No papers read in the last 7 days.")
        return

    papers = sorted(papers, key=lambda d: d.get("processed_at", ""), reverse=True)

    print()
    print(f"  Reading digest — last 7 days ({len(papers)} paper{'s' if len(papers) != 1 else ''})")
    print("  " + "-" * 48)

    for p in papers:
        title = p.get("title", "Untitled")
        summary = p.get("summary", "")
        engagement = p.get("engagement", 0)
        highlight_count = p.get("highlight_count", 0)
        processed_at = p.get("processed_at", "")

        date_str = ""
        if processed_at:
            try:
                dt = datetime.fromisoformat(processed_at)
                date_str = dt.strftime("%b %-d")
            except (ValueError, TypeError):
                pass

        citation_count = p.get("metadata", {}).get("citation_count", 0)
        stats = []
        if engagement:
            stats.append(f"{engagement}% engaged")
        if highlight_count:
            stats.append(f"{highlight_count} highlights")
        if citation_count:
            stats.append(f"{citation_count:,} citations")
        stats_str = f" ({', '.join(stats)})" if stats else ""

        ck = p.get("metadata", {}).get("citekey", "")
        idx = state.index_of(p["zotero_item_key"])
        idx_str = f"{_dim(f'[{idx}]')} " if idx else ""

        print()
        print(f"  {idx_str}{_bold(title)}")
        detail = f"{date_str}{stats_str}"
        if ck:
            detail = f"{detail} \u00b7 {ck}" if detail else ck
        if detail:
            print(f"    {_dim(detail)}")
        if summary:
            print(f"    {summary}")

    # Reading stats footer (matches email format)
    month_since = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)).isoformat()
    month_papers = state.documents_processed_since(month_since)
    _q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
    unread = state.documents_with_status(_q_status)

    def _stats_line(docs, label):
        count = len(docs)
        pages = sum(d.get("page_count", 0) for d in docs)
        words = sum(d.get("highlight_word_count", 0) for d in docs)
        parts = [f"read {count} paper{'s' if count != 1 else ''}"]
        if pages:
            parts.append(f"{pages:,} pages")
        if words:
            parts.append(f"{words:,} words highlighted")
        sep = " \u00b7 "
        return f"{label}: {sep.join(parts)}"

    print()
    queue_s = "s" if len(unread) != 1 else ""
    print(f"  {_dim(_stats_line(papers, 'This week'))}")
    print(f"  {_dim(_stats_line(month_papers, 'This month'))}")
    print(f"  {_dim(f'Queue: {len(unread)} paper{queue_s} waiting')}")
    print()


def _parse_suggestions(text: str) -> list[dict]:
    """Parse Claude's suggestion response into structured entries.

    Expects lines like: '1. Title — Reason'
    Returns list of {'title': ..., 'reason': ...}.
    """
    entries = []
    for line in text.strip().split("\n"):
        clean = line.strip().replace("**", "")
        if not clean:
            continue
        # Match "N. Title — Reason" or "N. Title - Reason"
        m = re.match(r"^\d+\.\s*(.+?)\s*[—–\-]\s*(.+)$", clean)
        if m:
            entries.append({"title": m.group(1).strip(), "reason": m.group(2).strip()})
    return entries


def _print_suggestions(entries: list[dict], unread: list[dict], now, state=None) -> None:
    """Print formatted suggestion output matching --digest style."""
    from datetime import datetime

    print()
    print(f"  Paper suggestions ({len(unread)} in queue)")
    print("  " + "-" * 48)

    # Build lookup: lowercase title -> doc for metadata enrichment
    title_to_doc = {doc["title"].lower(): doc for doc in unread}

    for entry in entries:
        title = entry["title"]
        reason = entry["reason"]

        # Try to find the matching doc for metadata
        doc = title_to_doc.get(title.lower())
        if not doc:
            # Fuzzy match: check if suggestion title is a substring
            for t_lower, d in title_to_doc.items():
                if title.lower() in t_lower or t_lower in title.lower():
                    doc = d
                    break

        # Build index prefix
        idx_str = ""
        if doc and state:
            idx = state.index_of(doc["zotero_item_key"])
            if idx:
                idx_str = f"[{idx}] "

        # Build stats line
        stats = []
        if doc:
            uploaded = doc.get("uploaded_at", "")
            if uploaded:
                try:
                    dt = datetime.fromisoformat(uploaded)
                    days = (now - dt).days
                    stats.append(f"{days} days in queue")
                except (ValueError, TypeError):
                    pass
            citations = doc.get("metadata", {}).get("citation_count", 0)
            if citations:
                stats.append(f"{citations:,} citations")

        stats_str = f" ({', '.join(stats)})" if stats else ""

        print()
        idx_dim = _dim(idx_str) if idx_str else ""
        print(f"  {idx_dim}{_bold(title)}")
        if stats_str:
            print(f"    {_dim(stats_str)}")
        print(f"    {reason}")

    print()


def _suggest() -> None:
    """Suggest papers to read next, promote them on reMarkable.

    Checks Gist for pending picks from GH Actions first. If none,
    calls Claude directly. For users without GH Actions, this is
    the primary way to get suggestions.
    """
    from datetime import datetime, timedelta, timezone

    from distillate import config
    from distillate import summarizer
    from distillate.digest import fetch_pending_from_gist
    from distillate.pipeline import _demote_and_promote
    from distillate.state import State, acquire_lock, release_lock

    if not config.is_zotero_reader():
        from distillate import remarkable_client

    config.setup_logging()

    if not acquire_lock():
        log.warning("Another instance is running (lock held), exiting")
        return

    try:
        state = State()
        now = datetime.now(timezone.utc)

        # Check Gist for pending picks from GH Actions
        pick_keys = None
        suggestions_ok = False
        if config.STATE_GIST_ID:
            pending = fetch_pending_from_gist()
            if pending:
                timestamp = pending.get("timestamp", "")
                last_processed = state._data.get("last_pending_timestamp", "")
                if timestamp and timestamp > last_processed:
                    pick_keys = pending.get("picks", [])
                    suggestion_text = pending.get("suggestion_text", "")
                    if pick_keys and suggestion_text:
                        _q_st = "tracked" if config.is_zotero_reader() else "on_remarkable"
                        unread = state.documents_with_status(_q_st)
                        entries = _parse_suggestions(suggestion_text)
                        if entries:
                            _print_suggestions(entries, unread, now, state=state)
                        else:
                            # Fall back to raw output if parsing fails
                            print()
                            for line in suggestion_text.strip().split("\n"):
                                if line.strip():
                                    print(f"  {line.strip()}")
                            print()
                        state._data["last_pending_timestamp"] = timestamp
                        suggestions_ok = True

        # Fall back to Claude if no pending picks
        if not pick_keys:
            _q_st = "tracked" if config.is_zotero_reader() else "on_remarkable"
            unread = state.documents_with_status(_q_st)
            if not unread:
                print("  No papers in your reading queue.")
                return

            if not config.ANTHROPIC_API_KEY:
                print()
                print("  Paper suggestions require an Anthropic API key.")
                print("  Run 'distillate --init' to configure AI features.")
                print()
                return

            unread_enriched = []
            for doc in unread:
                meta = doc.get("metadata", {})
                unread_enriched.append({
                    "title": doc["title"],
                    "tags": meta.get("tags", []),
                    "paper_type": meta.get("paper_type", ""),
                    "uploaded_at": doc.get("uploaded_at", ""),
                    "citation_count": meta.get("citation_count", 0),
                })

            since = (now - timedelta(days=30)).isoformat()
            recent = state.documents_processed_since(since)
            recent_enriched = []
            for doc in recent:
                meta = doc.get("metadata", {})
                recent_enriched.append({
                    "title": doc["title"],
                    "tags": meta.get("tags", []),
                    "summary": doc.get("summary", ""),
                    "engagement": doc.get("engagement", 0),
                    "citation_count": meta.get("citation_count", 0),
                })

            result = summarizer.suggest_papers(unread_enriched, recent_enriched)
            if not result:
                log.warning("Could not generate suggestions")
                pick_keys = []
            else:
                suggestions_ok = True

                # Parse and print structured suggestions
                entries = _parse_suggestions(result)
                if entries:
                    _print_suggestions(entries, unread, now, state=state)
                else:
                    # Fall back to raw output if parsing fails
                    print()
                    for line in result.strip().split("\n"):
                        if line.strip():
                            print(f"  {line.strip()}")
                    print()

                # Parse picks from Claude's response
                from distillate.digest import match_suggestion_to_title
                title_to_key = {doc["title"].lower(): doc["zotero_item_key"] for doc in unread}
                known_titles = [doc["title"] for doc in unread]
                pick_keys = []
                for line in result.strip().split("\n"):
                    matched = match_suggestion_to_title(line, known_titles)
                    if matched:
                        key = title_to_key.get(matched.lower())
                        if key and key not in pick_keys:
                            pick_keys.append(key)

        # Only demote/promote if suggestions succeeded (issue #9)
        if suggestions_ok:
            _demote_and_promote(state, pick_keys, verbose=True)

    except requests.exceptions.ConnectionError:
        print(
            "\n  Could not connect to the internet."
            "\n  Check your network connection and try again.\n"
        )
        return
    except Exception as e:
        if type(e).__name__ == "RmapiAuthError":
            print(f"\n  {e}\n")
            return
        log.exception("Unexpected error in suggest")
        raise
    finally:
        release_lock()


def _import(args: list[str]) -> None:
    """Import existing papers from Zotero into the Distillate workflow.

    Interactive:  distillate --import       (shows count, asks how many)
    Non-interactive: distillate --import N  (imports N most recent)
    """
    from distillate import config
    from distillate import pipeline as _pipeline
    from distillate import zotero_client
    from distillate.state import State, acquire_lock, release_lock

    if not config.is_zotero_reader():
        from distillate import remarkable_client

    config.setup_logging()

    if not acquire_lock():
        log.warning("Another instance is running (lock held), exiting")
        return

    try:
        state = State()

        # Fetch recent papers
        _coll_key = config.ZOTERO_COLLECTION_KEY
        papers = zotero_client.get_recent_papers(
            limit=100, collection_key=_coll_key,
        )

        # Exclude already-tracked keys
        papers = [p for p in papers if not state.has_document(p["key"])]

        if _coll_key:
            try:
                _coll_name = zotero_client.get_collection_name(_coll_key)
            except Exception:
                _coll_name = _coll_key
        else:
            _coll_name = ""

        if not papers:
            scope = f" in '{_coll_name}'" if _coll_name else " in your library"
            print(f"\n  No untracked papers found{scope}.\n")
            return

        # Determine how many to import
        if args:
            # Non-interactive: --import N
            try:
                count = int(args[0])
            except ValueError:
                print(f"\n  Invalid number: {args[0]}\n")
                return
            papers = papers[:count]
        else:
            # Interactive mode
            scope = f" in '{_coll_name}'" if _coll_name else " in your library"
            print(f"\n  Found {len(papers)} untracked paper{'s' if len(papers) != 1 else ''}{scope}.")
            print()
            for p in papers[:5]:
                meta = zotero_client.extract_metadata(p)
                print(f"    - {meta['title']}")
            if len(papers) > 5:
                print(f"    ... and {len(papers) - 5} more")
            print()
            answer = input(f"  How many to import? [all/{len(papers)}/none] ").strip().lower()
            if not answer or answer == "none" or answer == "n":
                print("  Skipped.\n")
                return
            if answer != "all":
                try:
                    count = int(answer)
                    papers = papers[:count]
                except ValueError:
                    print(f"  Invalid input: {answer}\n")
                    return

        # Ensure RM folders exist and get existing docs
        if not config.is_zotero_reader():
            remarkable_client.ensure_folders()
            existing_on_rm = set(
                remarkable_client.list_folder(config.RM_FOLDER_INBOX)
            )
        else:
            existing_on_rm = set()

        imported = 0
        awaiting_pdf = 0
        total = len(papers)
        for i, paper in enumerate(papers, 1):
            meta = zotero_client.extract_metadata(paper)
            print(f"  [{i}/{total}] Uploading: {meta['title']}")
            try:
                if _pipeline._upload_paper(paper, state, existing_on_rm):
                    # Check if it ended up as awaiting_pdf
                    doc = state.get_document(paper["key"])
                    if doc and doc.get("status") == "awaiting_pdf":
                        awaiting_pdf += 1
                    else:
                        imported += 1
            except Exception:
                log.exception(
                    "Failed to import '%s', skipping",
                    paper.get("data", {}).get("title", paper.get("key")),
                )

        # Update watermark to current library version
        current_version = zotero_client.get_library_version()
        state.zotero_library_version = current_version
        state.save()

        if awaiting_pdf:
            print(f"\n  Imported {imported} paper{'s' if imported != 1 else ''} ({awaiting_pdf} awaiting PDF).\n")
        else:
            print(f"\n  Imported {imported} paper{'s' if imported != 1 else ''}.\n")

    except requests.exceptions.ConnectionError:
        print(
            "\n  Could not connect to the internet."
            "\n  Check your network connection and try again.\n"
        )
        return
    except Exception as e:
        if type(e).__name__ == "RmapiAuthError":
            print(f"\n  {e}\n")
            return
        log.exception("Unexpected error in import")
        raise
    finally:
        release_lock()


# -- State export / import --


def _export_state(path: str) -> None:
    """Copy state.json to the specified path."""
    import shutil
    from distillate.state import STATE_PATH

    if not STATE_PATH.exists():
        print("  No state file found. Nothing to export.")
        return

    dest = Path(path).expanduser().resolve()
    shutil.copy2(STATE_PATH, dest)
    print(f"  State exported to {dest}")


def _import_state(path: str) -> None:
    """Validate and import a state.json from the specified path.

    Backs up existing state before replacing.
    """
    import shutil
    from distillate.state import STATE_PATH, _run_migrations

    src = Path(path).expanduser().resolve()
    if not src.exists():
        print(f"  File not found: {src}")
        sys.exit(1)

    # Validate JSON
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"  Invalid JSON: {exc}")
        sys.exit(1)

    # Must have at minimum the documents dict
    if not isinstance(data, dict) or "documents" not in data:
        print("  Invalid state file: missing 'documents' key.")
        sys.exit(1)

    # Run migrations on the imported data
    _run_migrations(data)

    # Backup existing state
    if STATE_PATH.exists():
        backup = STATE_PATH.with_suffix(".json.bak")
        shutil.copy2(STATE_PATH, backup)
        print(f"  Backed up existing state to {backup.name}")

    shutil.copy2(src, STATE_PATH)
    n_papers = len(data.get("documents", {}))
    print(f"  State imported from {src} ({n_papers} papers)")


# -- Reading insights dashboard --


def _report() -> None:
    """Display reading insights dashboard in the terminal."""
    from collections import Counter
    from datetime import datetime, timezone, timedelta

    from distillate.state import State
    from distillate.cli import _bold

    state = State()
    processed = state.documents_with_status("processed")
    if not processed:
        print("\n  No processed papers yet. Read some papers first!\n")
        return

    # ── Lifetime stats ────────────────────────────────────────────
    total_papers = len(processed)
    total_pages = sum(d.get("page_count", 0) for d in processed)
    total_words = sum(d.get("highlight_word_count", 0) for d in processed)
    engagements = [d.get("engagement", 0) for d in processed if d.get("engagement")]
    avg_engagement = round(sum(engagements) / len(engagements)) if engagements else 0

    print()
    print(f"  {_bold('Reading Report')}")
    print(f"  {'─' * 35}")
    print()
    print(f"  {_bold('Lifetime')}")
    print(f"    {total_papers} papers · {total_pages:,} pages · {total_words:,} words highlighted")
    print(f"    Avg engagement: {avg_engagement}%")
    print()

    # ── Reading velocity (last 8 weeks) ───────────────────────────
    now = datetime.now(timezone.utc)
    week_counts: Counter = Counter()
    for doc in processed:
        ts = doc.get("processed_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            weeks_ago = (now - dt).days // 7
            if weeks_ago < 8:
                # Use Monday of that week as label
                monday = dt - timedelta(days=dt.weekday())
                label = monday.strftime("%b %d")
                week_counts[label] = week_counts.get(label, 0) + 1
        except (ValueError, TypeError):
            pass

    if week_counts:
        print(f"  {_bold('Reading Velocity')} (last 8 weeks)")
        max_count = max(week_counts.values())
        # Reverse chronological
        for label in list(week_counts.keys())[::-1][:8]:
            count = week_counts[label]
            bar_len = round(count / max(max_count, 1) * 20)
            bar = "\u2588" * bar_len
            print(f"    {label}  {bar} {count}")
        print()

    # ── Top topics ────────────────────────────────────────────────
    topic_counter: Counter = Counter()
    for doc in processed:
        tags = doc.get("metadata", {}).get("tags") or []
        for tag in tags:
            topic_counter[tag] += 1

    if topic_counter:
        print(f"  {_bold('Top Topics')}")
        for i, (topic, count) in enumerate(topic_counter.most_common(5), 1):
            # Truncate long topic names
            display = topic[:30] if len(topic) > 30 else topic
            print(f"    {i}. {display:<32} {count} papers")
        print()

    # ── Engagement distribution ───────────────────────────────────
    buckets = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-100%": 0}
    for doc in processed:
        eng = doc.get("engagement", 0)
        if eng <= 25:
            buckets["0-25%"] += 1
        elif eng <= 50:
            buckets["25-50%"] += 1
        elif eng <= 75:
            buckets["50-75%"] += 1
        else:
            buckets["75-100%"] += 1

    max_bucket = max(buckets.values()) if buckets else 1
    print(f"  {_bold('Engagement Distribution')}")
    for label, count in buckets.items():
        bar_len = round(count / max(max_bucket, 1) * 20)
        bar = "\u2588" * bar_len
        print(f"    {label:<8} {bar} {count}")
    print()

    # ── Most-cited papers read ────────────────────────────────────
    cited = sorted(
        [d for d in processed if d.get("metadata", {}).get("citation_count", 0) > 0],
        key=lambda d: d.get("metadata", {}).get("citation_count", 0),
        reverse=True,
    )
    if cited:
        print(f"  {_bold('Most-Cited Papers Read')}")
        for doc in cited[:5]:
            idx = state.index_of(doc["zotero_item_key"])
            cites = doc["metadata"]["citation_count"]
            short = doc["title"][:50]
            print(f"    [{idx}] {short} ({cites:,} citations)")
        print()

    # ── Most-read authors ─────────────────────────────────────────
    author_counter: Counter = Counter()
    for doc in processed:
        for author in doc.get("authors", []):
            if author and author.lower() != "unknown":
                author_counter[author] += 1

    top_authors = [(a, c) for a, c in author_counter.most_common(10) if c >= 2]
    if top_authors:
        print(f"  {_bold('Most-Read Authors')}")
        for i, (author, count) in enumerate(top_authors[:5], 1):
            short = author[:30] if len(author) > 30 else author
            print(f"    {i}. {short:<32} {count} papers")
        print()
