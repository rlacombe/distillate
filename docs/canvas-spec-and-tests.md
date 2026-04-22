# Canvas — Spec & Test Plan

A working document. Two halves:
1. **Spec**: what Canvas should do to delight users (independent of current code)
2. **Test plan**: defensive test coverage derived from the spec, in red→green order

---

## Part 1 — Spec

### 1.1 What Canvas is

Canvas is **the synthesis surface** in Distillate. The keystone loop is "run experiment → see it improve on the chart"; Canvas is where you turn the chart into a story — a draft, a paper, a README, a shareable artifact — with an AI agent that lives alongside the document and knows exactly what's being edited.

It bridges three things that were never well connected before:
- **Structured artifacts** (papers in your library, runs from your experiments, notes)
- **Free-form documents** the researcher actually produces (LaTeX, Markdown, plain text)
- **An agent** primed with the file context and ready to edit it on request

The file on disk is the **source of truth**. No proprietary format. `git commit` it. Open it in vim. It just works.

### 1.2 Promises

These are the user-facing contracts. Every test should map to one or more of these. Every promise has a clear violation that constitutes a bug.

| # | Promise | Violation looks like |
|---|---------|----------------------|
| P1 | **Any document, edited where it lives.** Plain files on disk; no proprietary format. Open in another editor and changes flow back. | Editor shows stale content after external change; saving corrupts the file; format-locks the user out. |
| P2 | **Your agent knows what you're working on.** Launching an agent from a canvas cwd's it into the canvas directory and primes it with the entry filename. | Agent starts in `~`; primes with wrong file; needs manual context dump. |
| P3 | **Click to drill in.** Filenames in agent terminal are clickable: hover underlines, click opens as a canvas. | Filenames are inert text; click does nothing or opens the wrong project. |
| P4 | **Edit with words.** ⌘K on a selection sends a free-form instruction to the agent; the agent edits the file; the editor reloads automatically. | Selection lost; instruction sent to wrong session; no file watcher fires. |
| P5 | **Always rebuildable.** ⌘S compiles LaTeX with Tectonic; PDF appears next to source; errors map to source lines. | Stale PDF; cryptic errors; broken click-to-line; Tectonic auto-install fails silently. |
| P6 | **Live preview while you write.** Markdown shows rendered preview side-by-side, updates as you type. | Preview lags; doesn't update; needs save. |
| P7 | **Never lose work.** External edits to a clean buffer reload silently. External edits to a dirty buffer prompt explicitly (Reload / Keep mine). | Silent overwrite of unsaved changes; reload loop; banner doesn't appear; banner action does the wrong thing. |
| P8 | **Find what's already there.** New Canvas modal detects existing `.tex` (with `\documentclass`) and `.md` files in the project; offers Use this. | Suggests already-registered canvases; misses obvious files; suggests vendored junk. |
| P9 | **Citations from your library.** `\cite{key}` → resolve → `references.bib` populated from Papers. Missing keys are reported, not silently dropped. | Wrong bibtex entry; bib file truncated; missing keys not surfaced. |
| P10 | **One canvas → one persistent agent.** Each canvas can have a long-lived session. Closing the canvas doesn't kill the agent. Reopening reattaches. | Closing canvas kills tmux; reopening can't reattach; two sessions race for one canvas. |
| P11 | **Sandboxed I/O.** Canvas file ops (read, write, watch) are restricted to the canvas's directory. Path traversal is rejected. | `..`-prefixed path reads `/etc/passwd`; absolute path writes to `/tmp`. |
| P12 | **No silent failures.** Errors surface as toasts with actionable text; no operation completes the UI optimistically and fails behind the scenes. | "Canvas saved" toast but file unchanged; spinner spins forever; backend 500 swallowed. |

### 1.3 Anti-promises (out of scope by design)

Listing these so we don't accidentally test or build them:
- Multi-user collaboration / real-time co-editing
- Built-in version control (git is the answer)
- Rich-text / WYSIWYG editing (source editor only)
- Cloud sync of canvas content (only metadata syncs)
- Compile-on-keystroke (LaTeX is too heavy; ⌘S is the gesture)

### 1.4 User journeys

Each journey is a sequence of user actions and the system's promise that it'll behave a certain way. These map directly to behavior-level tests.

#### J1 — Start a paper
1. User opens project → sees "+ New canvas" in the project detail
2. Clicks → modal opens with title input + type radio (LaTeX / Markdown / Plain)
3. Types title, picks LaTeX, clicks Create
4. **System**: scaffolds `<root>/canvases/<slug>/main.tex` with template, `.gitignore`, `figures/` subdir; adds canvas record; sidebar updates; project detail card appears; editor mounts with the scaffolded content and CodeMirror in stex mode

#### J2 — Pick up an existing draft
1. Project has `paper/main.tex` from prior work
2. User clicks "+ New canvas"
3. Modal shows "Existing documents" section with that file
4. Clicks "Use this"
5. **System**: registers existing path as canvas (no scaffold), opens editor on the actual file

#### J3 — Get help from an agent
1. In a canvas, user clicks "Launch agent"
2. **System**: status "Launching…", spawns Claude Code in a tmux session cwd'd to canvas dir, attaches the bottom xterm, status flips to "Running"
3. First line in terminal: agent's primed message naming the canvas file
4. User types instruction
5. Agent edits file → file watcher fires → editor reloads silently
6. User closes canvas (e.g., navigates away)
7. **System**: detaches xterm; tmux session keeps running
8. User reopens canvas → xterm reattaches to the same tmux session (no new agent)

#### J4 — Drill into mentioned files
1. Agent terminal output mentions `figures/results.py`
2. User hovers → underlined, pointer cursor
3. User clicks
4. **System**: resolves to `<canvas-dir>/figures/results.py`, POSTs to canvases endpoint with import_path, gets back a canvas record (new or deduped), drills into editor for it; old canvas state is preserved in cache for back-navigation

#### J5 — Inline edit with ⌘K
1. User selects passage in editor → presses ⌘K
2. Modal opens with selected text shown in context + free-form input
3. User types instruction, hits Send
4. **System**: injects a structured prompt into the agent terminal via tmux send-keys, including selection coordinates and the user's instruction
5. Agent edits file → watcher fires → buffer reloads (silently if clean)

#### J6 — Citations
1. LaTeX canvas with `\cite{kingma2014adam}` etc.
2. User clicks "Resolve citations" (or it runs automatically on save)
3. **System**: extracts cite keys, looks each up in Papers library (citekey/DOI/arxiv), writes `references.bib` with matched bibtex entries plus a banner comment, returns list of resolved + missing
4. UI shows missing keys as a warning so the user can add the papers

#### J7 — Hot-reload safety
1. User editing canvas, makes changes, hasn't saved
2. Agent (or external tool) modifies the file
3. **System**: file watcher fires; because buffer is dirty, banner appears: "External change detected. [Reload] [Keep mine]"
4. User picks; banner dismisses; chosen content wins; no surprise data loss

#### J8 — Switch views without losing context
1. In canvas editor with agent terminal at bottom
2. User clicks a session item in sidebar → switches to Session tab
3. User navigates back to the canvas (sidebar canvas item)
4. **System**: switches back to control-panel view, remounts canvas editor cleanly, reattaches to existing agent if one was running

#### J9 — Compile with errors
1. User has LaTeX with a typo (`\beggin{document}`)
2. User hits ⌘S
3. **System**: Tectonic compiles, fails; error panel appears below editor with parsed errors; clicking an error jumps to the source line

#### J10 — First-run Tectonic install
1. User has a fresh install, no Tectonic binary
2. Hits ⌘S on a LaTeX canvas
3. **System**: install modal appears, downloads Tectonic, shows progress, then proceeds to compile

#### J11 — Rename a canvas
1. User clicks ⋯ menu on canvas card → Rename
2. Modal asks for new title
3. **System**: PATCH endpoint updates title; sidebar + cards reflect immediately; underlying file is **not** renamed (just the title)

#### J12 — Delete a canvas
1. User clicks ⋯ menu → Delete
2. Confirm dialog
3. **System**: DELETE endpoint removes from state; sidebar + cards update; **files on disk are left in place** (the user can `rm -rf` the directory if they want; we don't make destructive decisions)

### 1.5 Open spec questions (decide before writing tests)

Tentative defaults are listed; flag any you'd change.

1. **What types are first-class?** Default: `latex`, `markdown`, `plain`. Code files (`.py`, `.json`, etc.) open as plain text — clickable in terminal but not "real" canvases.
2. **What about absolute paths in agent output?** Default: clickable, opens as canvas regardless of project (could create a canvas pointing outside the project root).
3. **Do we restrict canvas dir to project root?** Default: yes for scaffolded canvases, no for imported ones (since the user explicitly chose the path).
4. **How does the user remove an agent from a canvas?** Default: there's a Stop button (not yet built). Without it, agent keeps running indefinitely.
5. **Cite key auto-resolve on save?** Default: no — explicit button only. Auto-resolve makes save slow and surprising.
6. **What does ⌘K do without a selection?** Default: still opens modal, prompts the agent to "edit the file" with the user's instruction (no specific passage).
7. **What if Tectonic install fails?** Default: error toast, allow retry; ⌘S becomes a no-op until install succeeds.
8. **Markdown preview engine?** Default: existing `markedParse` global. Does not need MathJax or custom renderers in v1.

---

## Part 2 — Test Plan

### 2.1 Coverage philosophy

Defensive coverage (per your call) means: every promise has tests for both the happy path AND every observable error path. Every endpoint, every IPC method, every renderer click handler. Tests are **behavior-level**: assert what the user sees / what the file system shows / what the API returns. Avoid pinning implementation details that will change.

### 2.2 Tier structure

```
Tier 1 — Backend HTTP routes        ~25 tests   pytest + FastAPI TestClient
Tier 2 — State helpers (extend)     ~10 tests   pytest
Tier 3 — IPC sandbox (extend)       ~12 tests   node --test
Tier 4 — Renderer DOM (jsdom)       ~20 tests   node --test + jsdom
Tier 5 — Regression (TDD discipline) ~5 tests   one per known bug, red→green
                                   ────────────
                                    ~72 tests
```

Tiers 1–3 use existing infrastructure. Tier 4 introduces `jsdom` as a devDep on `desktop/package.json`. No Playwright / Spectron / real Electron in this plan — flag if you want it.

### 2.3 Tier 1 — Backend HTTP routes (`tests/test_canvas_routes.py` — new)

One file, one `TestClient` fixture per test (isolated state via the existing `isolate_state` autouse fixture). Each test names exactly what it asserts.

**`GET /workspaces/{ws}/canvases`**
- `test_list_returns_empty_array_for_workspace_with_no_canvases`
- `test_list_returns_canvases_in_creation_order`
- `test_list_unknown_workspace_returns_project_not_found`
- `test_list_migrates_legacy_singular_writeup_field`
- `test_list_migrates_legacy_plural_writeups_dict`

**`POST /workspaces/{ws}/canvases` (scaffold path)**
- `test_create_latex_scaffolds_main_tex_with_template`
- `test_create_markdown_scaffolds_md_file_with_h1`
- `test_create_plain_scaffolds_empty_txt_file`
- `test_create_with_no_root_path_returns_root_path_required`
- `test_create_with_repo_only_falls_back_to_repo_path`
- `test_create_dedups_slug_when_directory_exists`
- `test_create_unknown_workspace_returns_project_not_found`
- `test_create_invalid_type_returns_unknown_type_error`

**`POST /workspaces/{ws}/canvases` (import path)**
- `test_import_existing_file_registers_without_scaffolding`
- `test_import_nonexistent_path_returns_file_not_found`
- `test_import_directory_path_returns_not_a_regular_file`
- `test_import_dedups_when_path_already_registered`
- `test_import_infers_type_from_extension_when_unspecified`

**`PATCH /workspaces/{ws}/canvases/{cv}`**
- `test_patch_renames_canvas_title`
- `test_patch_ignores_disallowed_fields`
- `test_patch_unknown_canvas_returns_canvas_not_found`
- `test_patch_empty_body_returns_no_editable_fields`

**`DELETE /workspaces/{ws}/canvases/{cv}`**
- `test_delete_removes_from_state_but_leaves_files_on_disk`
- `test_delete_unknown_canvas_returns_canvas_not_found`

**`GET /workspaces/{ws}/canvases/{cv}/dir`**
- `test_get_dir_returns_dir_entry_type_exists`
- `test_get_dir_reports_exists_false_when_directory_missing`
- `test_get_dir_unknown_canvas_returns_canvas_not_found`

**`POST /workspaces/{ws}/canvases/{cv}/compile-status`**
- `test_compile_status_records_freshness_on_canvas`
- `test_compile_status_unknown_canvas_returns_canvas_not_found`

**`GET /workspaces/{ws}/canvases/detect`**
- `test_detect_finds_tex_with_documentclass`
- `test_detect_skips_tex_without_documentclass`
- `test_detect_finds_markdown_files`
- `test_detect_excludes_already_registered_canvases`
- `test_detect_skips_ignored_dirs_node_modules_venv_etc`
- `test_detect_returns_empty_when_no_root_path`

**`POST /workspaces/{ws}/canvases/{cv}/resolve-citations`**
- `test_resolve_writes_references_bib_with_resolved_entries`
- `test_resolve_reports_missing_keys`
- `test_resolve_handles_arxiv_prefix_keys`
- `test_resolve_handles_doi_keys`
- `test_resolve_skips_when_canvas_is_not_latex`

### 2.4 Tier 2 — State helper tests (extend `tests/test_canvas.py`)

Add to existing file (already 22 tests):
- `test_canvas_id_is_stable_across_save_and_reload`
- `test_canvas_session_link_persists_across_reload`
- `test_unlink_canvas_session_clears_session_id`
- `test_concurrent_canvas_creation_assigns_distinct_ids`
- `test_canvas_added_at_set_to_creation_time`
- `test_canvas_updated_at_changes_on_patch`
- `test_remove_canvas_does_not_affect_other_canvases`
- `test_find_by_path_returns_canvas_when_dir_entry_match`
- `test_find_by_path_returns_none_when_no_match`
- `test_legacy_writeup_with_no_dir_field_is_skipped_during_migration`

### 2.5 Tier 3 — IPC sandbox tests (`desktop/test/canvas-fs.test.js` — new)

Extract from the existing tectonic-manager.test.js since CanvasFs deserves its own file.

**Sandbox enforcement (P11)**
- `path traversal with ".." is rejected`
- `absolute path outside baseDir is rejected`
- `symlink that escapes baseDir is rejected`
- `path with null byte is rejected`

**File ops**
- `readFile returns content for valid path inside sandbox`
- `readFile returns error when file does not exist`
- `writeFile creates file and parents if needed`
- `writeFile fails cleanly when canvas dir was deleted`

**Watcher (P7)**
- `startWatch fires emitter on file change`
- `startWatch debounces multiple rapid changes within 150ms`
- `stopWatch removes listeners and prevents further fires`
- `start/stop is idempotent`

### 2.6 Tier 4 — Renderer DOM tests (`desktop/test/canvas-renderer.test.js` — new, jsdom)

Setup: a tiny test harness that:
1. Creates a jsdom `Window`
2. Stubs `window.fetch`, `window.xtermBridge`, `window.distillate.canvas`, `window.distillate.tectonic`, `window.nicolas`, `serverPort`
3. Loads the renderer scripts (`canvas.js`, `projects.js`, the canvas slice of `layout.js`) via `vm.runInContext` so they bind to the jsdom window
4. Provides helpers to seed the DOM (sidebar HTML, detail panel, etc.) and to fire DOM events

Tests follow the user journeys.

**J1 — Start a paper**
- `clicking + New canvas opens the modal`
- `submitting modal POSTs to canvases endpoint with title and type`
- `successful create switches to control-panel view, drills into editor`

**J2 — Pick up existing draft**
- `New Canvas modal renders detected files when API returns candidates`
- `clicking Use this on a candidate POSTs with import_path`

**J3 — Agent attach**
- `Launch agent disables button, shows Launching status`
- `successful launch attaches xterm to xterm-canvas-bottom and shows Running`
- `terminal data is forwarded to the canvas's wsId, not currentTerminalProject`

**J4 — Drill into mentioned files (the bug class we hit!)**
- `terminal file click in canvas agent uses _current.wsId for the POST URL`
- `terminal file click in main session uses _currentSessionContext.workspaceId, NOT currentTerminalProject` ← regression test
- `terminal file click resolves relative path against canvas dir`
- `terminal file click on absolute path uses path as-is`
- `successful import drills into the new canvas without losing back-navigation`

**J5 — ⌘K inline edit**
- `Cmd+K with selection opens modal with selection visible`
- `submit posts to inject-prompt endpoint with selection coords`
- `Escape dismisses modal without sending`

**J7 — Hot reload**
- `external change with clean buffer silently reloads editor`
- `external change with dirty buffer shows banner with Reload and Keep mine`
- `Reload action discards local changes and loads disk content`
- `Keep mine action dismisses banner and re-saves local content`

**J8 — View switching (the other bug we hit!)**
- `openCanvasInline switches to control-panel view when session view is active` ← regression test
- `openCanvasInline unhides experiment-detail and hides welcome`
- `openCanvasInline replaces previous editor cleanly (destroyCanvasEditor called first)`

**J11/J12 — Rename and delete**
- `rename PATCH updates sidebar label and card title in place`
- `delete DELETE removes card from project detail`

### 2.7 Tier 5 — Regression tests (TDD red→green for known bugs)

These already have fixes in place, but per your "pytest red/green" requirement I'll write each as a RED test against a reverted version of the fix, document the failure in the commit message, then re-apply the fix and watch it go GREEN. One commit per bug.

**B1 — Workspace ID extracted from terminal key**
- Test: `terminal file click in main session uses _currentSessionContext.workspaceId, NOT currentTerminalProject`
- Red: revert the `_currentSessionContext?.workspaceId || ...` change in `layout.js`
- Green: re-apply

**B2 — Canvas editor mounted into hidden container**
- Test: `openCanvasInline switches to control-panel view when session view is active`
- Red: revert the `switchEditorTab("control-panel")` call in `openCanvasInline`
- Green: re-apply

**B3 — `_term.dispose()` throwing leaves singleton in dead state**
- Test: `xtermBridge.dispose() leaves _term null even when underlying dispose throws`
- Red: revert the try/catch around `_term.dispose()` in `preload.js`
- Green: re-apply

**B4 — File link provider missing on main terminal**
- Test: `main session terminal init registers file link provider with workspace-id-aware callback`
- Red: revert the `registerFileLinkProvider` block I added in `layout.js`
- Green: re-apply

**B5 — Native link provider not invoked because of `forceSelectionEnabled`**
- (Investigate first — may not actually be a bug if my native provider implementation works. If it doesn't, write the test, find the cause, fix it.)

### 2.8 Implementation order

Phase order, with checkpoints between phases.

**Phase 0 — Spec sign-off**
- You review this doc
- We agree on open spec questions (§1.5)
- We agree on tier scope, especially Tier 4 (jsdom)

**Phase 1 — Tier 5 regression tests (B1, B2, B3, B4)**
- Highest value: proves the fixes I shipped actually fix the bugs
- Smallest scope: 4 tests
- Forces us to set up the jsdom harness for B1, B2, B4 (B3 is preload-only Node)
- One commit per bug, with red→green log in the message

**Phase 2 — Tier 1 backend route tests**
- Independent of renderer
- Fast, deterministic, no infra
- Catches backend regressions immediately
- Run the full pytest suite at the end of this phase

**Phase 3 — Tier 2 state extensions + Tier 3 IPC tests**
- Smaller add to existing files
- Wraps up the non-renderer side

**Phase 4 — Tier 4 renderer DOM tests (rest)**
- Largest infrastructure investment
- Build the jsdom harness once, reuse for all renderer tests
- Defensive coverage of all journeys

**Phase 5 — Manual end-to-end validation**
- Launch the actual app
- Walk through each user journey from §1.4
- Note any divergence from the spec
- Triage: fix or update spec

**Phase 6 — CI wiring**
- Ensure pytest covers `test_canvas*.py`
- Ensure `npm test` in `desktop/` covers `canvas-*.test.js`
- Add a CI gate so canvas tests must pass

### 2.9 What this plan deliberately doesn't include

- **Playwright-Electron / Spectron E2E** — too much infrastructure relative to value. Tier 4 jsdom should catch most renderer bugs. Revisit if Tier 4 misses real bugs.
- **Visual regression tests** — pixel-level testing is brittle and out of scope.
- **Performance tests** — no performance budget defined; skip until there's a complaint.
- **xterm.js / CodeMirror internals** — trust the libraries.
- **Real Tectonic compile in CI** — too flaky (network-dependent download). Mock the binary; integration-test the parser separately.
- **Cloud sync of canvas state** — explicitly anti-promise.

### 2.10 Expected costs

Rough estimates. Will adjust as I learn what jsdom needs.

| Phase | Tests | Effort |
|-------|-------|--------|
| Phase 1 (regressions) | 4 | 1 session — sets up jsdom harness |
| Phase 2 (backend routes) | ~25 | 1 session — mostly mechanical |
| Phase 3 (state + IPC) | ~22 | 1 session |
| Phase 4 (renderer DOM) | ~16 | 1–2 sessions — the harness pays off here |
| Phase 5 (manual) | — | 30 min walking journeys |
| Phase 6 (CI) | — | 15 min |

Total: ~67 tests across 4–5 working sessions.

---

## Spec questions for you to answer before Phase 1

1. **§1.5 open spec questions** — any defaults to override?
2. **Tier 4 jsdom approval** — OK to add `jsdom` to `desktop/package.json` devDeps?
3. **Phase 1 starting point** — start with B2 (view switching, simplest jsdom setup) or B1 (workspace ID, more code involved)?
4. **What's the right home for this doc?** Keep at `docs/canvas-spec-and-tests.md`? Move to `strategy/`? Split into spec.md + test-plan.md?
5. **Priorities if time runs out** — if we only get through Phase 1+2, is that acceptable? Or is Tier 4 (renderer) the critical one?
