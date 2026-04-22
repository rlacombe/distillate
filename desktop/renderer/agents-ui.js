/**
 * Agents UI — unified agent roster.
 *
 * Three sections:
 *   1. Experimentalists — autonomous experiment runners (Tier 3a)
 *   2. Sessions — coding sessions from projects
 *   3. Personal agents — Lux, Switchback, custom (Tier 3b)
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _agents = [];
let _selectedAgentId = null;
let _agentTransition = false;
let _cachedTemplates = [];

const agentsSidebarEl = document.getElementById("agents-sidebar");
const agentsCountEl = document.getElementById("agents-count");
const newAgentBtn = document.getElementById("new-agent-btn");

// ---------------------------------------------------------------------------
// Fetch agents (all non-nicolas agents)
// ---------------------------------------------------------------------------

async function fetchAgents() {
  try {
    const [agentResp, tmplResp] = await Promise.all([
      fetch(`http://127.0.0.1:${serverPort}/agents/live`),
      _cachedTemplates.length ? Promise.resolve(null) : fetch(`http://127.0.0.1:${serverPort}/agents/templates`),
    ]);
    if (agentResp.ok) {
      const data = await agentResp.json();
      const allAgents = data.agents || [];
      // Agents = everything except Nicolas
      _agents = allAgents.filter((a) => a.agent_type !== "nicolas");
      // Also store all agents for backward compat with dashboard
      if (typeof window !== "undefined") window._allAgents = allAgents;
    }
    if (tmplResp && tmplResp.ok) {
      const data = await tmplResp.json();
      _cachedTemplates = data.templates || [];
    }
    renderAgentsList();
    if (typeof populateWelcomeDashboard === "function") populateWelcomeDashboard();
  } catch (e) {
    // Server not ready yet
  }
}

// Backward compat: fetchAgents is called from various places
window.fetchAgents = fetchAgents;

// ---------------------------------------------------------------------------
// Render agents list
// ---------------------------------------------------------------------------

function renderAgentsList() {
  if (!agentsSidebarEl) return;

  const wsArr = (typeof _workspaces !== "undefined" ? _workspaces : []);
  const expsArr = (typeof cachedProjects !== "undefined" ? cachedProjects : []);

  // ── Build the roster: named identities (Agent primitive) ──

  // Nicolas (Tier 1 — the shell, always present)
  const nicolas = {
    id: "nicolas", name: "Nicolas", emoji: "\u2697\uFE0F",
    tier: "shell", harness: "Distillate Agent SDK",
    status: "active",
  };

  // Experimentalist harnesses (Tier 3a — from integrations agents cache)
  const harnesses = [];
  const agentsList = (typeof cachedAgents !== "undefined" ? cachedAgents : []);
  for (const a of agentsList) {
    if (a.available) {
      const hId = a.id;
      harnesses.push({
        id: hId, name: a.label, emoji: "",
        tier: "experimentalist", harness: a.label,
        status: "configured",
        activeSessions: expsArr.filter((e) =>
          (e.active_sessions || 0) > 0 &&
          (e.harness_id === hId || e.agent_type === hId || (!e.harness_id && hId === "claude"))
        ).length,
      });
    }
  }
  // Fallback: if no integrations data yet, show Claude Code as default
  if (harnesses.length === 0) {
    const ccSessions = expsArr.filter((e) => (e.active_sessions || 0) > 0).length;
    harnesses.push({
      id: "claude-code", name: "Claude Code", emoji: "",
      tier: "experimentalist", harness: "Claude Code",
      status: "configured", activeSessions: ccSessions,
    });
  }

  // Personal agents (Tier 3b — Lux, Switchback, custom; exclude Nicolas)
  const personal = [..._agents]
    .filter((a) => a.agent_type !== "nicolas")
    .sort((a, b) => {
      const aR = a.session_status === "running" ? 0 : 1;
      const bR = b.session_status === "running" ? 0 : 1;
      if (aR !== bR) return aR - bR;
      return (a.name || "").localeCompare(b.name || "");
    });

  // ── Badge count: identities with active work ──
  const activeHarnesses = harnesses.filter((h) => (h.activeSessions || 0) > 0).length;
  const activePersonal = personal.filter((a) => a.session_status === "running").length;
  const totalRunning = activeHarnesses + activePersonal;
  if (agentsCountEl) agentsCountEl.textContent = totalRunning || "";

  // ── Render ──
  let html = "";

  // Nicolas (always first)
  html += `
    <div class="sidebar-item agent-item nicolas-item"
         onclick="switchSidebarView('nicolas')">
      <span class="agent-nicolas-dot"></span>
      <span class="sidebar-item-name">${nicolas.emoji} Nicolas</span>
      <span class="agent-badge builtin">shell</span>
    </div>`;

  // Experimentalist harnesses
  html += `<div class="agents-section-divider"></div>`;
  html += `<div class="agents-section-label">Experimentalists</div>`;
  for (const h of harnesses) {
    const activeLabel = h.activeSessions > 0
      ? `<span class="agent-badge running">${h.activeSessions} running</span>`
      : `<span class="agent-badge">\u2713 ready</span>`;
    html += `
      <div class="sidebar-item agent-item">
        <span class="sidebar-item-name">${escapeHtml(h.name)}</span>
        ${activeLabel}
      </div>`;
  }

  // Personal agents
  if (personal.length > 0) {
    html += `<div class="agents-section-divider"></div>`;
    html += `<div class="agents-section-label">Personal</div>`;
    for (const agent of personal) {
      const isSelected = _selectedAgentId === agent.id;
      const isRunning = agent.session_status === "running";
      const dotHtml = isRunning
        ? `<span class="sidebar-status-icon status-unknown">\u25CF</span>`
        : "";

      let actionBtn = "";
      if (isRunning) {
        actionBtn = `<button class="agent-stop-btn" onclick="event.stopPropagation(); stopAgent('${escapeHtml(agent.id)}')" title="Stop">&times;</button>`;
      } else {
        actionBtn = `<button class="agent-play-btn" onclick="event.stopPropagation(); selectAgent('${escapeHtml(agent.id)}')" title="Start">&#x25B6;</button>`
          + `<button class="agent-delete-btn" onclick="event.stopPropagation(); deleteAgent('${escapeHtml(agent.id)}', '${escapeHtml(agent.name)}')" title="Delete">&times;</button>`;
      }

      html += `
        <div class="sidebar-item agent-item${isSelected ? " active" : ""}"
             data-agent-id="${escapeHtml(agent.id)}"
             onclick="selectAgent('${escapeHtml(agent.id)}')">
          ${dotHtml}
          <span class="sidebar-item-name">${escapeHtml(agent.name)}</span>
          ${isRunning ? '<span class="agent-badge running">live</span>' : ""}
          ${actionBtn}
        </div>`;
    }
  }

  agentsSidebarEl.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Select agent -> open terminal
// ---------------------------------------------------------------------------

async function selectAgent(agentId) {
  if (_agentTransition) return;
  _agentTransition = true;

  try {
    _selectedAgentId = agentId;
    renderAgentsList();

    const agent = _agents.find((a) => a.id === agentId);
    if (!agent) return;

    // Hide experiment tabs — agents don't have Control Panel/Session/Results tabs
    const editorTabs = document.getElementById("editor-tabs");
    if (editorTabs) editorTabs.classList.add("hidden");

    // Clear currentProjectId so experiment-specific logic doesn't run
    if (typeof currentProjectId !== "undefined") currentProjectId = null;

    // Start session if not running, then attach terminal
    let tmuxName = agent.tmux_name;

    if (agent.session_status !== "running" || !tmuxName) {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/agents/live/${agent.id}/start`, {
        method: "POST",
      });
      const data = await resp.json();
      if (!data.success) {
        if (typeof showToast === "function") showToast(data.error || "Failed to start agent", "error");
        return;
        }
        tmuxName = data.tmux_name;
        agent.tmux_name = tmuxName;
        agent.session_status = "running";
        renderAgentsList();
        }

        const terminalKey = `agent-${agent.id}`;
        if (typeof showTerminalForSession === "function") {
        showTerminalForSession(terminalKey, tmuxName, agent.name);
        }
        } catch (e) {
        if (typeof showToast === "function") showToast("Failed to select agent", "error");
        } finally {
        _agentTransition = false;
        }
        }

        // ---------------------------------------------------------------------------
        // Stop agent
        // ---------------------------------------------------------------------------

        async function stopAgent(agentId) {
        try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/agents/live/${agentId}/stop`, {
        method: "POST",
        });
        const data = await resp.json();
        if (data.success) {
        if (_selectedAgentId === agentId && typeof detachTerminal === "function") {
        detachTerminal();
        }
        if (typeof showToast === "function") showToast("Agent stopped", "info");
        }
        } catch (e) {
        if (typeof showToast === "function") showToast("Failed to stop agent", "error");
        }
        fetchAgents();
        }

        // ---------------------------------------------------------------------------
        // Delete agent
        // ---------------------------------------------------------------------------

        function deleteAgent(agentId, agentName) {
        _showConfirm({
        title: "Delete Agent",
        message: `Delete <strong>${escapeHtml(agentName)}</strong>? This removes its config directory and cannot be undone.`,
        confirmLabel: "Delete",
        danger: true,
        onConfirm: async () => {
        try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/agents/live/${agentId}`, {
          method: "DELETE",
        });
        const data = await resp.json();
        if (data.success) {
          if (_selectedAgentId === agentId) {
            _selectedAgentId = null;
            if (typeof detachTerminal === "function") detachTerminal();
          }
          if (typeof showToast === "function") showToast(`"${agentName}" deleted`, "info");
        } else {
          if (typeof showToast === "function") showToast(data.error || "Failed to delete agent", "error");
        }
        } catch (e) {
        if (typeof showToast === "function") showToast("Failed to delete agent", "error");
        }
        fetchAgents();
        },
        });
        }

        // ---------------------------------------------------------------------------
        // Edit agent
        // ---------------------------------------------------------------------------

        async function editAgent(agentId) {
        const agent = _agents.find((a) => a.id === agentId);
        if (!agent) return;

        const detail = document.getElementById("experiment-detail");
        const welcome = document.getElementById("welcome");
        if (welcome) welcome.classList.add("hidden");
        if (!detail) return;
        detail.classList.remove("hidden");

        detail.innerHTML = `
        <div class="agent-create-form">
        <h2>Edit Agent</h2>

        <div class="form-group">
        <label for="agent-name-input">Name</label>
        <input type="text" id="agent-name-input" placeholder="e.g. Lux, Switchback" autocomplete="off" value="${escapeHtml(agent.name || "")}">
        </div>

        <div class="form-group">
        <label for="agent-personality-input">Personality / System Prompt</label>
        <textarea id="agent-personality-input" rows="6" placeholder="Describe who this agent is and what it does...">${escapeHtml(agent.personality || "")}</textarea>
        </div>

        <div class="form-group">
        <label for="agent-dir-input">Working Directory (optional)</label>
        <input type="text" id="agent-dir-input" placeholder="e.g. ~/projects/my-repo" autocomplete="off" value="${escapeHtml(agent.working_dir || "")}">
        <p class="form-hint">Folder the agent runs in. Leave blank to use the agent's config directory.</p>
        </div>

        <div class="form-group">
        <label for="agent-command-input">Command (optional)</label>
        <input type="text" id="agent-command-input" placeholder="Default: claude --permission-mode auto" autocomplete="off" value="${escapeHtml(agent.command || "")}">
        </div>

        <div class="form-group">
        <label for="agent-model-input">Model (optional)</label>
        <input type="text" id="agent-model-input" placeholder="Default: use Claude Code default" autocomplete="off" value="${escapeHtml(agent.model || "")}">
        </div>

        <div class="form-actions">
        <button class="btn-primary" onclick="submitEditAgent('${escapeHtml(agent.id)}')">Save Changes</button>
        <button class="btn-secondary" onclick="cancelEditAgent()">Cancel</button>
        </div>
        </div>`;

        const nameInput = document.getElementById("agent-name-input");
        if (nameInput) nameInput.focus();
        switchEditorTab("control-panel", { skipSessionAttach: true });
        }

        async function submitEditAgent(agentId) {
        const name = document.getElementById("agent-name-input")?.value.trim();
        const personality = document.getElementById("agent-personality-input")?.value.trim();
        const model = document.getElementById("agent-model-input")?.value.trim();
        const workingDir = document.getElementById("agent-dir-input")?.value.trim();
        const command = document.getElementById("agent-command-input")?.value.trim();

        if (!name) {
        if (typeof showToast === "function") showToast("Agent name is required", "error");
        return;
        }

        try {
        const body = { name };
        if (personality) body.personality = personality;
        if (model) body.model = model;
        if (workingDir) body.working_dir = workingDir;
        if (command) body.command = command;

        const resp = await fetch(`http://127.0.0.1:${serverPort}/agents/live/${agentId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (!data.success) {
        if (typeof showToast === "function") showToast(data.error || "Failed to update agent", "error");
        return;
        }

        if (typeof showToast === "function") showToast(`"${name}" updated`, "success");
        await fetchAgents();
        cancelEditAgent();
        } catch (e) {
        if (typeof showToast === "function") showToast("Failed to update agent", "error");
        }
        }

        function cancelEditAgent() {
        const detail = document.getElementById("experiment-detail");
        const welcome = document.getElementById("welcome");
        if (detail) detail.classList.add("hidden");
        if (welcome) welcome.classList.remove("hidden");
        }

        // ---------------------------------------------------------------------------
        // Create agent (reuse existing form logic from agents-ui.js)
        // ---------------------------------------------------------------------------

        let _selectedTemplateId = "";

        async function showCreateAgentForm() {
        _selectedAgentId = null;
        _selectedTemplateId = "";

        const detail = document.getElementById("experiment-detail");
        const welcome = document.getElementById("welcome");
        if (welcome) welcome.classList.add("hidden");
        if (!detail) return;
        detail.classList.remove("hidden");

        try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/agents/templates`);
        if (resp.ok) {
        const data = await resp.json();
        _cachedTemplates = data.templates || [];
        }
        } catch (e) {}

        const templateCards = _cachedTemplates.map((t) => `
        <div class="template-card" data-template="${escapeHtml(t.id)}" onclick="selectTemplate('${escapeHtml(t.id)}')">
        <span class="template-icon">${t.icon}</span>
        <span class="template-name">${escapeHtml(t.name)}</span>
        </div>`).join("");

        detail.innerHTML = `
        <div class="agent-create-form">
        <h2>New Agent</h2>
        <p class="agent-create-hint">Create a personal agent with its own personality and domain.</p>

        ${templateCards ? `
        <div class="agent-template-picker">
        <label>Start from template</label>
        <div class="template-grid">
          <div class="template-card selected" data-template="" onclick="selectTemplate('')">
            <span class="template-icon">+</span>
            <span class="template-name">Blank</span>
          </div>
          ${templateCards}
        </div>
        </div>` : ""}

        <div class="form-group">
        <label for="agent-name-input">Name</label>
        <input type="text" id="agent-name-input" placeholder="e.g. Lux, Switchback" autocomplete="off">
        </div>

        <div class="form-group">
        <label for="agent-personality-input">Personality / System Prompt</label>
        <textarea id="agent-personality-input" rows="6" placeholder="Describe who this agent is and what it does..."></textarea>
        </div>

        <div class="form-group">
        <label for="agent-dir-input">Working Directory (optional)</label>
        <input type="text" id="agent-dir-input" placeholder="e.g. ~/projects/my-repo" autocomplete="off">
        <p class="form-hint">Folder the agent runs in. Leave blank to use the agent's config directory.</p>
        </div>

        <div class="form-group">
        <label for="agent-command-input">Command (optional)</label>
        <input type="text" id="agent-command-input" placeholder="Default: claude --permission-mode auto" autocomplete="off">
        </div>

        <div class="form-group">
        <label for="agent-model-input">Model (optional)</label>
        <input type="text" id="agent-model-input" placeholder="Default: use Claude Code default" autocomplete="off">
        </div>

        <div class="form-actions">
        <button class="btn-primary" onclick="submitCreateAgent()">Create Agent</button>
        <button class="btn-secondary" onclick="cancelCreateAgent()">Cancel</button>
        </div>
        </div>`;

        const nameInput = document.getElementById("agent-name-input");
        if (nameInput) nameInput.focus();
        switchEditorTab("control-panel", { skipSessionAttach: true });
        }

        function selectTemplate(templateId) {
        _selectedTemplateId = templateId;
        document.querySelectorAll(".template-card").forEach((c) => {
        c.classList.toggle("selected", c.dataset.template === templateId);
        });
        if (!templateId) return;
        const tmpl = _cachedTemplates.find((t) => t.id === templateId);
        if (!tmpl) return;
        const nameInput = document.getElementById("agent-name-input");
        const personalityInput = document.getElementById("agent-personality-input");
        if (nameInput && !nameInput.value.trim()) nameInput.value = tmpl.name;
        if (personalityInput) personalityInput.value = tmpl.personality || "";
        }

        async function submitCreateAgent() {
        const name = document.getElementById("agent-name-input")?.value.trim();
        const personality = document.getElementById("agent-personality-input")?.value.trim();
        const model = document.getElementById("agent-model-input")?.value.trim();
        const workingDir = document.getElementById("agent-dir-input")?.value.trim();
        const command = document.getElementById("agent-command-input")?.value.trim();

        if (!name) {
        if (typeof showToast === "function") showToast("Agent name is required", "error");
        return;
        }

        try {
        const body = { name };
        if (_selectedTemplateId) body.template = _selectedTemplateId;
        if (personality) body.personality = personality;
        if (model) body.model = model;
        if (workingDir) body.working_dir = workingDir;
        if (command) body.command = command;

        const resp = await fetch(`http://127.0.0.1:${serverPort}/agents/live`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (!data.success) {
        if (typeof showToast === "function") showToast(data.error || "Failed to create agent", "error");
        return;
        }

        if (typeof showToast === "function") showToast(`"${name}" created`, "success");
        await fetchAgents();
        selectAgent(data.agent.id);
        } catch (e) {
        if (typeof showToast === "function") showToast("Failed to create agent", "error");
        }
        }

function cancelCreateAgent() {
  const detail = document.getElementById("experiment-detail");
  const welcome = document.getElementById("welcome");
  if (detail) detail.classList.add("hidden");
  if (welcome) welcome.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// New agent button
// ---------------------------------------------------------------------------

if (newAgentBtn) {
  newAgentBtn.addEventListener("click", showCreateAgentForm);
}

// ---------------------------------------------------------------------------
// Focus a coding session — navigate to the project and attach the terminal
// ---------------------------------------------------------------------------

function focusCodingSession(workspaceId, sessionId, tmuxName) {
  if (typeof showTerminalForSession === "function" && tmuxName) {
    showTerminalForSession(`session-${sessionId}`, tmuxName, sessionId);
  }
}

// ---------------------------------------------------------------------------
// Auto-refresh: poll agent status every 10s when agents view is active
// ---------------------------------------------------------------------------

setInterval(async () => {
  if (typeof _activeSidebarView === "undefined" || _activeSidebarView !== "agents") return;
  if (typeof currentTerminalProject !== "undefined" && currentTerminalProject) return;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/agents/live`);
    if (!resp.ok) return;
    const data = await resp.json();
    const allAgents = data.agents || [];
    const newAgents = allAgents.filter((a) => a.agent_type !== "nicolas");
    const changed = newAgents.length !== _agents.length ||
      newAgents.some((a, i) => !_agents[i] || a.session_status !== _agents[i].session_status);
    if (changed) {
      _agents = newAgents;
      renderAgentsList();
    }
  } catch (e) {}
}, 10000);
