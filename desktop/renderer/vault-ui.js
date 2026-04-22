/**
 * Wiki UI — browse the Obsidian LLM-wiki directly from Distillate.
 *
 * Sidebar: folder tree with expandable sections.
 * Center:  rendered markdown of the selected file (index.md as landing).
 * Header:  "Open in Obsidian" button + refresh.
 */

/* global serverPort, escapeHtml, obsidianConfigured, openObsidianVaultRoot, openObsidianWikiFile, marked */

// DOM refs
const vaultSidebarEl = document.getElementById("vault-sidebar");
const vaultDetailEl = document.getElementById("vault-detail");
const vaultCountEl = document.getElementById("vault-count");
const vaultRefreshBtn = document.getElementById("vault-refresh-btn");
const vaultObsidianBtn = document.getElementById("vault-obsidian-btn");

// State
let _vaultTree = [];
let _vaultOpenPath = "";
let _vaultExpanded = new Set(["Lab Notebook", "Papers", "Experiments"]);

// ── Data fetch ────────────────────────────────────────────────────────────

async function fetchVaultTree() {
  if (!serverPort) return;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/vault/tree`);
    if (!resp.ok) {
      _renderVaultUnconfigured();
      return;
    }
    const data = await resp.json();
    if (!data.ok) {
      _renderVaultUnconfigured();
      return;
    }
    _vaultTree = data.tree || [];
    _renderVaultSidebar();

    // Auto-show index.md on first open
    if (!_vaultOpenPath) {
      const indexNode = _vaultTree.find((n) => n.name === "index.md");
      if (indexNode) {
        _vaultSelectFile(indexNode.path);
      } else {
        _renderVaultLanding();
      }
    }
  } catch {
    _renderVaultUnconfigured();
  }
}

async function _vaultRefresh() {
  if (!serverPort) return;
  if (vaultRefreshBtn) vaultRefreshBtn.classList.add("spinning");
  try {
    await fetch(`http://127.0.0.1:${serverPort}/vault/refresh`, { method: "POST" });
    await fetchVaultTree();
    // Re-render current file to pick up changes
    if (_vaultOpenPath) _vaultSelectFile(_vaultOpenPath);
  } finally {
    if (vaultRefreshBtn) vaultRefreshBtn.classList.remove("spinning");
  }
}

// ── Sidebar tree ──────────────────────────────────────────────────────────

function _renderVaultSidebar() {
  if (!vaultSidebarEl) return;

  // Count files
  const fileCount = _countFiles(_vaultTree);
  if (vaultCountEl) vaultCountEl.textContent = fileCount || "";

  if (!_vaultTree.length) {
    _renderVaultUnconfigured();
    return;
  }

  vaultSidebarEl.innerHTML = _renderTreeNodes(_vaultTree, 0);
  _wireTreeClicks();
}

function _countFiles(nodes) {
  let c = 0;
  for (const n of nodes) {
    if (n.type === "file") c++;
    if (n.children) c += _countFiles(n.children);
  }
  return c;
}

function _formatFileLabel(filename) {
  // Strip extension, humanize lab notebook dates, keep others as-is
  const base = filename.replace(/\.(md|base)$/, "");
  const dateMatch = base.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateMatch) {
    const [, y, m, d] = dateMatch;
    const dt = new Date(Number(y), Number(m) - 1, Number(d));
    return dt.toLocaleDateString("en-US", {
      weekday: "short", month: "short", day: "numeric",
    });
  }
  return base;
}

function _renderTreeNodes(nodes, depth) {
  let html = "";
  for (const node of nodes) {
    if (node.type === "dir") {
      const expanded = _vaultExpanded.has(node.path);
      const arrow = expanded ? "&#x25BE;" : "&#x25B8;";
      const indent = 14 + depth * 12;
      html += `<div class="vault-tree-dir" data-path="${escapeHtml(node.path)}" style="padding-left:${indent}px">
        <span class="vault-tree-arrow">${arrow}</span>
        <span class="vault-tree-dir-name">${escapeHtml(node.name)}</span>
        <span class="vault-tree-dir-count">${_countFiles(node.children || [])}</span>
      </div>`;
      if (expanded && node.children) {
        html += _renderTreeNodes(node.children, depth + 1);
      }
    } else {
      const active = node.path === _vaultOpenPath ? " active" : "";
      const label = _formatFileLabel(node.name);
      const indent = 14 + (depth + 1) * 12;
      html += `<div class="vault-tree-file${active}" data-path="${escapeHtml(node.path)}" style="padding-left:${indent}px">
        <span class="vault-tree-file-name">${escapeHtml(label)}</span>
      </div>`;
    }
  }
  return html;
}

function _wireTreeClicks() {
  if (!vaultSidebarEl) return;
  vaultSidebarEl.querySelectorAll(".vault-tree-dir").forEach((el) => {
    el.addEventListener("click", () => {
      const path = el.dataset.path;
      if (_vaultExpanded.has(path)) {
        _vaultExpanded.delete(path);
      } else {
        _vaultExpanded.add(path);
      }
      _renderVaultSidebar();
    });
  });
  vaultSidebarEl.querySelectorAll(".vault-tree-file").forEach((el) => {
    el.addEventListener("click", () => _vaultSelectFile(el.dataset.path));
  });
}

function _renderVaultUnconfigured() {
  if (vaultSidebarEl) {
    vaultSidebarEl.innerHTML = `
      <div class="sidebar-empty">
        <p>No Obsidian vault configured.</p>
        <p class="sidebar-empty-hint">Set <code>OBSIDIAN_VAULT_PATH</code> in your .env to enable the wiki.</p>
      </div>`;
  }
  if (vaultCountEl) vaultCountEl.textContent = "";
}

// ── Center column ─────────────────────────────────────────────────────────

function _activateVaultCenterColumn() {
  if (!vaultDetailEl) return;
  const welcomeEl = document.getElementById("welcome");
  const expDetailEl = document.getElementById("experiment-detail");
  const nbDetailEl = document.getElementById("notebook-detail");
  if (welcomeEl) welcomeEl.classList.add("hidden");
  if (expDetailEl) expDetailEl.classList.add("hidden");
  if (nbDetailEl) nbDetailEl.classList.add("hidden");
  vaultDetailEl.classList.remove("hidden");

  const editorViews = ["control-panel", "session", "results", "prompt-editor"];
  for (const v of editorViews) {
    const el = document.getElementById(`${v}-view`);
    if (el) el.classList.toggle("hidden", v !== "control-panel");
  }
}

async function _vaultSelectFile(path) {
  if (!serverPort || !path) return;
  _vaultOpenPath = path;

  // Update sidebar active state
  if (vaultSidebarEl) {
    vaultSidebarEl.querySelectorAll(".vault-tree-file").forEach((el) => {
      el.classList.toggle("active", el.dataset.path === path);
    });
  }

  _activateVaultCenterColumn();
  if (!vaultDetailEl) return;

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/vault/file?path=${encodeURIComponent(path)}`);
    if (!resp.ok) {
      vaultDetailEl.innerHTML = `<div class="vault-page"><div class="vault-error">File not found: ${escapeHtml(path)}</div></div>`;
      return;
    }
    const data = await resp.json();
    _renderVaultFile(path, data.content || "");
  } catch {
    vaultDetailEl.innerHTML = `<div class="vault-page"><div class="vault-error">Failed to load file.</div></div>`;
  }
}

function _renderVaultFile(path, content) {
  if (!vaultDetailEl) return;

  const fileName = path.split("/").pop().replace(/\.md$/, "");

  // Strip YAML frontmatter for rendering
  let body = content;
  if (body.startsWith("---\n")) {
    const endIdx = body.indexOf("\n---\n", 4);
    if (endIdx > 0) body = body.slice(endIdx + 5);
  }

  // Render markdown
  let rendered = "";
  if (typeof marked !== "undefined" && marked.parse) {
    rendered = marked.parse(body);
  } else {
    rendered = `<pre>${escapeHtml(body)}</pre>`;
  }

  // Rewrite wikilinks: [[Target|Label]] → clickable vault links
  rendered = rendered.replace(
    /\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g,
    (_, target, label) => {
      const display = label || target;
      const href = target.endsWith(".md") ? target : target + ".md";
      return `<a class="vault-wikilink" data-path="${escapeHtml(href)}">${escapeHtml(display)}</a>`;
    },
  );

  // Build "Open in Obsidian" button
  const obsidianBtn = (typeof obsidianConfigured === "function" && obsidianConfigured())
    ? `<button class="obsidian-btn" id="vault-open-obsidian" title="Open in Obsidian">
         <img src="/ui/icons/obsidian.svg" alt="" width="14" height="14"><span>Open in Obsidian</span>
       </button>`
    : "";

  vaultDetailEl.innerHTML = `
    <div class="vault-page">
      <header class="vault-page-header">
        <span class="vault-page-breadcrumb">${escapeHtml(path.replace(/\.md$/, ""))}</span>
        ${obsidianBtn}
      </header>
      <div class="vault-page-body">${rendered}</div>
    </div>`;

  // Wire wikilink clicks
  vaultDetailEl.querySelectorAll(".vault-wikilink").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const target = a.dataset.path;
      if (target) _vaultSelectFile(target);
    });
  });

  // Wire "Open in Obsidian" button
  const obsBtn = document.getElementById("vault-open-obsidian");
  if (obsBtn) {
    obsBtn.addEventListener("click", () => {
      if (typeof openObsidianWikiFile === "function") {
        openObsidianWikiFile(path);
      } else if (typeof openObsidianVaultRoot === "function") {
        openObsidianVaultRoot();
      }
    });
  }
}

function _renderVaultLanding() {
  _activateVaultCenterColumn();
  if (!vaultDetailEl) return;

  vaultDetailEl.innerHTML = `
    <div class="vault-page">
      <header class="vault-page-header">
        <p class="vault-page-eyebrow">Research knowledge base</p>
        <h1 class="vault-page-title">Wiki</h1>
        <p class="vault-page-subtitle">Your research compounding over time, browsable from Distillate.</p>
      </header>
      <div class="vault-page-body">
        <p>Select a file from the sidebar to view it, or click <strong>Refresh</strong> to generate the index.</p>
      </div>
    </div>`;
}

// ── Wiring ────────────────────────────────────────────────────────────────

if (vaultRefreshBtn) {
  vaultRefreshBtn.addEventListener("click", _vaultRefresh);
}

function _vaultOpenObsidian() {
  const path = _vaultOpenPath || "index.md";
  if (typeof openObsidianWikiFile === "function") {
    openObsidianWikiFile(path);
  }
}

