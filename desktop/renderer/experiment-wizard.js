/* ───── Experiment Wizard — Nicolas-driven flow + legacy form fallback ───── */

// DOM ref for new experiment button
const newExperimentBtn = document.getElementById("new-experiment-btn");
if (newExperimentBtn) {
  newExperimentBtn.addEventListener("click", () => showNewExperimentWizard());
}

// New experiment modal — compact overlay with harness/model/effort selectors
// and two CTAs: quick scaffold or hand off to Nicolas for planning.
function showNewExperimentWizard(preselectedWorkspaceId) {
  showNewExperimentModal(preselectedWorkspaceId);
}

function showNewExperimentModal(preselectedWorkspaceId) {
  document.querySelector(".new-xp-modal-overlay")?.remove();

  function modelsForHarness(harness) {
    if (typeof getModelsForHarness === "function") return getModelsForHarness(harness);
    const all = (typeof getSupportedModels === "function") ? getSupportedModels() : [];
    const isGemini = harness === "gemini";
    return all
      .filter(m => isGemini ? m.family === "gemini" : m.family !== "gemini")
      .map(m => ({ value: m.id, label: m.label }));
  }

  function renderModelOptions(harness) {
    return modelsForHarness(harness).map(m =>
      `<option value="${m.id}">${m.label}</option>`
    ).join("");
  }

  const modelOptions = renderModelOptions("claude");

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay workspace-modal new-xp-modal-overlay";
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h2>New Experiment</h2>
        <button class="modal-close">&times;</button>
      </div>
      <div class="modal-body" id="new-xp-modal-body">
        <div class="setting-group">
          <label for="new-xp-name">Name</label>
          <input type="text" id="new-xp-name" class="modal-input" placeholder="my-experiment" spellcheck="false" autofocus />
        </div>
        <div class="setting-group">
          <label for="new-xp-goal">Goal</label>
          <textarea id="new-xp-goal" class="modal-input" rows="3"
            placeholder="What are you trying to achieve? Describe the approach, constraints, and success criteria…"
            style="resize:vertical;font-family:inherit;"></textarea>
        </div>
        <div class="setting-group">
          <label for="new-xp-workspace">Project</label>
          <select id="new-xp-workspace" class="modal-input">
            <option value="">Select a project…</option>
          </select>
        </div>
        <div style="display:flex;gap:10px;margin-top:4px;">
          <div class="setting-group" style="flex:2;margin-bottom:0;">
            <label for="new-xp-metric">Goal Metric <span class="new-xp-opt">— optional</span></label>
            <input type="text" id="new-xp-metric" class="modal-input" placeholder="e.g. val_accuracy, loss, F1" spellcheck="false" />
          </div>
          <div class="setting-group" style="flex:1;margin-bottom:0;">
            <label for="new-xp-metric-dir">Direction</label>
            <select id="new-xp-metric-dir" class="modal-input">
              <option value="maximize">Maximize</option>
              <option value="minimize">Minimize</option>
            </select>
          </div>
        </div>
        <details class="new-xp-advanced">
          <summary>Advanced</summary>
          <div style="display:flex;gap:10px;margin-top:8px;">
            <div class="setting-group" style="flex:1;margin-bottom:0;">
              <label for="new-xp-harness">Harness</label>
              <select id="new-xp-harness" class="modal-input">
                <option value="claude">Claude Code</option>
                <option value="gemini">Gemini CLI</option>
              </select>
            </div>
            <div class="setting-group" style="flex:2;margin-bottom:0;">
              <label for="new-xp-model">Model</label>
              <select id="new-xp-model" class="modal-input">
                ${modelOptions}
              </select>
            </div>
            <div class="setting-group" style="flex:1;margin-bottom:0;">
              <label for="new-xp-effort">Effort</label>
              <select id="new-xp-effort" class="modal-input">
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>
          </div>
          <div style="display:flex;gap:10px;margin-top:8px;align-items:flex-end;">
            <div class="setting-group" style="flex:2;margin-bottom:0;">
              <label for="new-xp-compute">Compute</label>
              <select id="new-xp-compute" class="modal-input">
                <option value="local">Local — free</option>
              </select>
            </div>
            <div id="new-xp-budget-row" style="flex:1;display:none;">
              <div class="setting-group" style="margin-bottom:0;">
                <label for="new-xp-budget">Budget cap</label>
                <div style="display:flex;align-items:center;gap:4px;">
                  <span style="font-size:12px;color:var(--text-dim);">$</span>
                  <input type="number" id="new-xp-budget" class="modal-input" value="25" min="1" step="1" style="padding:10px 8px;" />
                </div>
              </div>
            </div>
          </div>
        </details>
        <div style="margin-top:16px;">
          <div class="new-xp-cta-row">
            <button class="modal-btn-submit" id="new-xp-create-btn" disabled>Create Experiment</button>
            <button id="new-xp-nicolas-btn" class="modal-btn-secondary">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H2a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h3l3 3 3-3h3a1 1 0 0 0 1-1V3a1 1 0 0 0-1-1z"/></svg>
              Plan with Nicolas
            </button>
          </div>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  const nameInput  = overlay.querySelector("#new-xp-name");
  const goalInput  = overlay.querySelector("#new-xp-goal");
  const createBtn  = overlay.querySelector("#new-xp-create-btn");
  const nicolasBtn = overlay.querySelector("#new-xp-nicolas-btn");

  // Async: populate workspace dropdown
  (async () => {
    try {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces`);
      if (!resp.ok) return;
      const data = await resp.json();
      const sel = overlay.querySelector("#new-xp-workspace");
      const workspaces = (data.workspaces || []).sort((a, b) => (a.default ? 1 : 0) - (b.default ? 1 : 0));
      for (const ws of workspaces) {
        const opt = document.createElement("option");
        opt.value = ws.id;
        opt.textContent = ws.name + (ws.default ? " (default)" : "");
        if (preselectedWorkspaceId && ws.id === preselectedWorkspaceId) opt.selected = true;
        sel.appendChild(opt);
      }
      // Default to the Workbench (first `default` project) if nothing preselected.
      if (!preselectedWorkspaceId) {
        const fallback = workspaces.find((w) => w.default);
        if (fallback) sel.value = fallback.id;
      }
      _updateCreateEnabled();
    } catch (_) {}
  })();

  const wsSelect = overlay.querySelector("#new-xp-workspace");
  function _updateCreateEnabled() {
    const hasName = !!nameInput.value.trim();
    const hasGoal = !!goalInput.value.trim();
    const hasProject = !!(wsSelect && wsSelect.value);
    createBtn.disabled = !(hasName && hasGoal && hasProject);
  }
  nameInput.addEventListener("input", _updateCreateEnabled);
  goalInput.addEventListener("input", _updateCreateEnabled);
  wsSelect?.addEventListener("change", _updateCreateEnabled);

  overlay.querySelector(".modal-close").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  document.addEventListener("keydown", function _esc(e) {
    if (e.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", _esc); }
  });
  setTimeout(() => nameInput.focus(), 50);

  // Update model list when harness changes
  const harnessSelect = overlay.querySelector("#new-xp-harness");
  const modelSelect   = overlay.querySelector("#new-xp-model");
  harnessSelect.addEventListener("change", () => {
    const opts = modelsForHarness(harnessSelect.value);
    modelSelect.innerHTML = opts.map(m =>
      `<option value="${m.id}">${m.label}</option>`
    ).join("");
    modelSelect.value = opts[0]?.value || "";
  });

  // Compute select — show budget cap row for GPU options
  const computeSelect = overlay.querySelector("#new-xp-compute");
  const budgetRow     = overlay.querySelector("#new-xp-budget-row");
  computeSelect.addEventListener("change", () => {
    budgetRow.style.display = computeSelect.value === "local" ? "none" : "";
  });

  // Async: populate HF Jobs GPU flavors if connected
  (async () => {
    try {
      const intResp = await fetch(`http://127.0.0.1:${serverPort}/integrations`);
      if (!intResp.ok) return;
      const intData = await intResp.json();
      const hfjobs = (intData.compute || []).find((c) => c.id === "hfjobs");
      if (!hfjobs?.connected) return;

      const flavorsResp = await fetch(`http://127.0.0.1:${serverPort}/compute/hfjobs/flavors`);
      if (!flavorsResp.ok) return;
      const flavorsData = await flavorsResp.json();
      if (!flavorsData.ok || !flavorsData.flavors?.length) return;

      const divider = document.createElement("option");
      divider.disabled = true;
      divider.textContent = "── Hugging Face Jobs ──";
      computeSelect.appendChild(divider);

      for (const f of flavorsData.flavors) {
        const opt = document.createElement("option");
        opt.value = `hfjobs:${f.id}`;
        opt.textContent = `${f.label}  ·  ${f.vram_gb}GB  ·  $${f.cost_per_hour.toFixed(2)}/hr`;
        computeSelect.appendChild(opt);
      }
    } catch (_) {}
  })();

  // ── Plan with Nicolas ──
  nicolasBtn.addEventListener("click", () => {
    const name    = nameInput.value.trim();
    const goal    = goalInput.value.trim();
    const metric  = overlay.querySelector("#new-xp-metric")?.value.trim()   || "";
    const metricDir = overlay.querySelector("#new-xp-metric-dir")?.value    || "maximize";
    const harness = overlay.querySelector("#new-xp-harness")?.value  || "claude";
    const model   = overlay.querySelector("#new-xp-model")?.value    || "claude-sonnet-4-6";
    const effort  = overlay.querySelector("#new-xp-effort")?.value   || "high";
    const compute = overlay.querySelector("#new-xp-compute")?.value  || "local";
    const wsId    = overlay.querySelector("#new-xp-workspace")?.value || "";
    overlay.remove();

    const ctx = wsId ? ` Link it to project "${wsId}".` : preselectedWorkspaceId ? ` Link it to project "${preselectedWorkspaceId}".` : "";
    const harnessLabel = harness === "gemini" ? "Gemini CLI" : "Claude Code";
    const modelLabel = (typeof _modelIdToLabel === "function") ? _modelIdToLabel(model) : model;
    const computeLabel = compute === "local" ? "local compute"
      : `HF Jobs GPU (${compute.split(":")[1] || compute})`;
    const parts = [];
    if (name) parts.push(`called "${name}"`);
    if (goal) parts.push(`intent: ${goal}`);
    if (metric) parts.push(`goal metric: ${metricDir === "minimize" ? "minimize" : "maximize"} ${metric}`);
    const preamble = parts.length
      ? `I want to create an experiment ${parts.join(", ")}.${ctx} I'm planning to use ${harnessLabel} with ${modelLabel} at ${effort} effort on ${computeLabel}. `
      : "";
    const seed = preamble +
      "Walk me through the setup: confirm or refine the name and intent, then confirm the goal metric and direction. " +
      "If no goal metric was specified, propose a concrete one with direction (minimize or maximize). " +
      "Confirm every choice before moving on. When ready, call init_experiment to scaffold it, " +
      "show me the generated PROMPT.md, and pause so I can review before we launch.";

    const detailEl = document.getElementById("experiment-detail");
    if (detailEl && !detailEl.classList.contains("hidden")) {
      detailEl.classList.add("hidden");
      detailEl.innerHTML = "";
      welcomeEl?.classList.remove("hidden");
    }
    if (typeof askNicolasFromPalette === "function") askNicolasFromPalette(seed);
  });

  // ── Create Experiment ──
  createBtn.addEventListener("click", async () => {
    const name    = nameInput.value.trim();
    const goal    = goalInput.value.trim();
    const metric  = overlay.querySelector("#new-xp-metric").value.trim();
    const metricDir = overlay.querySelector("#new-xp-metric-dir").value;
    const harness = overlay.querySelector("#new-xp-harness").value;
    const model   = overlay.querySelector("#new-xp-model").value;
    const effort  = overlay.querySelector("#new-xp-effort").value;
    const compute = overlay.querySelector("#new-xp-compute").value;
    const budget  = parseFloat(overlay.querySelector("#new-xp-budget")?.value || "25") || 25;
    const wsId    = overlay.querySelector("#new-xp-workspace")?.value || "";
    if (!name || !goal || !wsId) return;

    // Transition modal body to progress view
    const modalBody = overlay.querySelector("#new-xp-modal-body");
    modalBody.innerHTML = `
      <div style="padding:8px 0 4px;">
        <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:12px;">${name}</div>
        <div class="wizard-flow" id="new-xp-flow" style="gap:6px;">
          <div class="flow-step" data-step="1"><span class="flow-dot pending"></span><span class="flow-label">Scaffolding</span><span class="flow-detail"></span></div>
          <div class="flow-connector"></div>
          <div class="flow-step" data-step="2"><span class="flow-dot pending"></span><span class="flow-label">Creating GitHub repo</span><span class="flow-detail"></span></div>
          <div class="flow-connector"></div>
          <div class="flow-step" data-step="3"><span class="flow-dot pending"></span><span class="flow-label">Ready</span><span class="flow-detail"></span></div>
        </div>
      </div>
    `;

    const flowEl = modalBody.querySelector("#new-xp-flow");
    function setStep(id, status, detail) {
      const node = flowEl.querySelector(`.flow-step[data-step="${id}"]`);
      if (!node) return;
      node.querySelector(".flow-dot").className = `flow-dot ${status}`;
      if (detail) node.querySelector(".flow-detail").textContent = detail;
    }

    let projectId = null;

    // Step 1: scaffold
    setStep(1, "active");
    try {
      const body = { name, agent_type: harness, effort, model, launch: false };
      if (goal)    body.goal = goal;
      if (metric)  { body.primary_metric = metric; body.metric_direction = metricDir; }
      const effectiveWsId = wsId || preselectedWorkspaceId || "";
      if (effectiveWsId) body.workspace_id = effectiveWsId;
      if (compute.startsWith("hfjobs:")) {
        body.compute = "hfjobs";
        body.gpu_type = compute.split(":")[1];
        body.compute_budget_usd = budget;
      }

      const resp = await fetch(`http://127.0.0.1:${serverPort}/experiments/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        for (const line of buf.split("\n").slice(0, -1)) {
          if (!line.trim()) continue;
          try {
            const msg = JSON.parse(line);
            if (msg.project_id || msg.experiment_id) projectId = msg.project_id || msg.experiment_id;
            if (msg.status === "error") throw new Error(msg.detail || "Scaffold failed");
          } catch (e) { if (e.message !== "JSON parse") throw e; }
        }
        buf = buf.split("\n").pop();
      }
    } catch (err) {
      setStep(1, "error", err.message);
      return;
    }

    if (!projectId) { setStep(1, "error", "No project ID"); return; }
    setStep(1, "done");

    // Step 2: GitHub repo (non-fatal)
    setStep(2, "active");
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/github`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `distillate-xp-${projectId}`, private: false }),
      });
      const d = await r.json();
      setStep(2, d.ok ? "done" : "skipped", d.ok ? (d.url || "") : (d.reason || "skipped"));
    } catch {
      setStep(2, "skipped", "GitHub unavailable");
    }

    // Step 3: navigate
    setStep(3, "active");
    await fetchExperimentsList();
    selectProject(projectId);
    setStep(3, "done");
    setTimeout(() => overlay.remove(), 600);
  });
}

async function showNewExperimentForm(preselectedWorkspaceId) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  // Deselect any project
  currentProjectId = null;
  const _tabLabel = document.getElementById("editor-tabs-project-name");
  if (_tabLabel) _tabLabel.textContent = "";
  experimentsSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.remove("active");
  });

  welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = "";
  resetResultsTab();
  resetSetupTab();
  switchEditorTab("control-panel");

  // Check if experiments root is configured
  const settings = await window.nicolas.getSettings();
  let experimentsRoot = settings.experimentsRoot || "";

  const wizard = document.createElement("div");
  wizard.className = "new-experiment-wizard";

  wizard.innerHTML = `
    <h3>\uD83E\uDDEA New Experiment</h3>
    <div class="wizard-field">
      <label>Name</label>
      <input id="wizard-name" type="text" placeholder="my-experiment" spellcheck="false" />
    </div>
    <div class="wizard-field">
      <label>Goal</label>
      <textarea id="wizard-goal" placeholder="Describe what you want to optimize, the success criteria, and target metrics\u2026" rows="4"></textarea>
    </div>
    <div class="wizard-field">
      <label>Primary metric</label>
      <div style="display:flex; gap:8px; align-items:center;">
        <input id="wizard-metric" type="text" placeholder="e.g. param_count, val_loss, test_accuracy" spellcheck="false" style="flex:1;" />
        <select id="wizard-metric-dir" style="width:auto; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:4px 8px;">
          <option value="minimize">minimize</option>
          <option value="maximize">maximize</option>
        </select>
      </div>
      <span style="font-size:10px; color:var(--text-dim);">The metric the agent optimizes. Getting this wrong means it pushes in the wrong direction.</span>
    </div>
    <div class="wizard-field">
      <label>Metric constraint (optional)</label>
      <input id="wizard-metric-constraint" type="text" placeholder="e.g. test_accuracy >= 0.99" spellcheck="false" />
    </div>
    <div class="wizard-field">
      <label>Constraints (optional)</label>
      <input id="wizard-constraints" type="text" placeholder="e.g. MacBook M3, no GPU, must use PyTorch" spellcheck="false" />
    </div>
    <div class="wizard-field" style="display:flex; gap:12px;">
      <div style="flex:0 0 120px;">
        <label>Iteration time</label>
        <div style="display:flex; align-items:center; gap:4px;">
          <input id="wizard-duration" type="number" value="5" min="1" max="120" style="width:60px;" />
          <span style="font-size:11px; color:var(--text-dim);">min</span>
        </div>
      </div>
      <div style="flex:0 0 160px;">
        <label>Session budget (optional)</label>
        <div style="display:flex; align-items:center; gap:4px;">
          <input id="wizard-session-budget" type="number" placeholder="\u221E" min="0" style="width:60px;" />
          <span style="font-size:11px; color:var(--text-dim);">hours</span>
        </div>
      </div>
      <div style="flex:0 0 140px;">
        <label>Agent</label>
        <select id="wizard-agent" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:6px 8px;">
          <option value="claude">Claude Code</option>
        </select>
      </div>
      <div style="flex:0 0 100px;">
        <label>Effort</label>
        <select id="wizard-effort" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:6px 8px;">
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>
    </div>
    <div class="wizard-field">
      <label>Experiments folder</label>
      <div style="display:flex; gap:6px;">
        <input id="wizard-root" type="text" value="${experimentsRoot}" placeholder="~/experiments" spellcheck="false" style="flex:1;" />
        <button id="wizard-browse" class="wizard-btn-cancel" style="flex:0; padding:6px 10px; white-space:nowrap;">Browse</button>
      </div>
    </div>
    <div class="wizard-field" style="display:flex; gap:12px; align-items:flex-end;">
      <div style="flex:1;">
        <label>Compute</label>
        <select id="wizard-compute" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:6px 8px;">
          <option value="local">Local (free)</option>
        </select>
      </div>
      <div id="wizard-modal-budget-row" style="flex:0 0 140px; display:none;">
        <label>Budget cap</label>
        <div style="display:flex; align-items:center; gap:4px;">
          <span style="font-size:11px; color:var(--text-dim);">$</span>
          <input id="wizard-modal-budget" type="number" value="25" min="1" step="1" style="width:72px;" />
          <span style="font-size:11px; color:var(--text-dim);">USD</span>
        </div>
      </div>
    </div>
    <div class="wizard-field">
      <label>Project (optional)</label>
      <select id="wizard-workspace" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:6px 8px;">
        <option value="">None</option>
      </select>
    </div>
    <div class="wizard-actions">
      <button class="wizard-btn-cancel" id="wizard-cancel">Cancel</button>
      <button class="wizard-btn-create" id="wizard-create" disabled>Create</button>
    </div>
    <div id="wizard-flow" class="wizard-flow hidden"></div>
  `;
  detailEl.appendChild(wizard);

  // Populate agent dropdown from server
  (async () => {
    try {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/agents`);
      if (resp.ok) {
        const data = await resp.json();
        const sel = wizard.querySelector("#wizard-agent");
        sel.innerHTML = "";
        for (const ag of (data.agents || [])) {
          const opt = document.createElement("option");
          opt.value = ag.id;
          opt.textContent = ag.label + (ag.available ? "" : " (not installed)");
          opt.disabled = !ag.available;
          sel.appendChild(opt);
        }
      }
    } catch (_) {}
  })();

  // Populate compute dropdown from /integrations — show Modal and HF Jobs when authed.
  // Toggle the $ budget input based on the selected compute.
  (async () => {
    try {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/integrations`);
      if (!resp.ok) return;
      const data = await resp.json();
      const sel = wizard.querySelector("#wizard-compute");

      // HF Jobs — fetch GPU flavors and add each as an option
      const hfjobs = (data.compute || []).find((c) => c.id === "hfjobs");
      if (hfjobs?.connected) {
        try {
          const flavorsResp = await fetch(`http://127.0.0.1:${serverPort}/compute/hfjobs/flavors`);
          const flavorsData = await flavorsResp.json();
          if (flavorsData.ok) {
            for (const f of flavorsData.flavors) {
              const opt = document.createElement("option");
              opt.value = `hfjobs:${f.id}`;
              opt.textContent = `${f.label} ${f.vram_gb}GB ($${f.cost_per_hour.toFixed(2)}/hr)`;
              sel.appendChild(opt);
            }
          }
        } catch (_) {}
      }

      // Modal
      const modal = (data.compute || []).find((c) => c.id === "modal");
      if (modal?.connected) {
        const opt = document.createElement("option");
        opt.value = "modal";
        opt.textContent = "Modal A100 80GB ($2-3/hr)";
        sel.appendChild(opt);
      }
    } catch (_) {}
  })();

  const computeSel = wizard.querySelector("#wizard-compute");
  const budgetRow = wizard.querySelector("#wizard-modal-budget-row");
  computeSel?.addEventListener("change", () => {
    const val = computeSel.value;
    budgetRow.style.display = (val === "modal" || val.startsWith("hfjobs:")) ? "" : "none";
  });

  // Populate workspace/project dropdown — default to Workbench when no project is preselected
  (async () => {
    try {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces`);
      if (resp.ok) {
        const data = await resp.json();
        const sel = wizard.querySelector("#wizard-workspace");
        const workspaces = data.workspaces || [];
        // Sort: non-default first, Workbench last
        workspaces.sort((a, b) => (a.default ? 1 : 0) - (b.default ? 1 : 0));
        for (const ws of workspaces) {
          const opt = document.createElement("option");
          opt.value = ws.id;
          opt.textContent = ws.name + (ws.default ? " (default)" : "");
          if (preselectedWorkspaceId && ws.id === preselectedWorkspaceId) {
            opt.selected = true;
          } else if (!preselectedWorkspaceId && ws.default) {
            opt.selected = true;
          }
          sel.appendChild(opt);
        }
      }
    } catch (_) {}
  })();

  const STEPS = [
    { id: 1, label: "Create project directory" },
    { id: 2, label: "Draft PROMPT.md with Claude" },
    { id: 3, label: "Install hooks & reporting" },
    { id: 4, label: "Register experiment" },
    { id: 5, label: "Create GitHub repository" },
    { id: 6, label: "Launch Claude Code session" },
  ];

  // Browse button
  wizard.querySelector("#wizard-browse").addEventListener("click", async () => {
    const dir = await window.nicolas.selectDirectory("Select experiments folder");
    if (dir) {
      wizard.querySelector("#wizard-root").value = dir;
      experimentsRoot = dir;
    }
  });

  // Cancel
  wizard.querySelector("#wizard-cancel").addEventListener("click", () => {
    detailEl.classList.add("hidden");
    detailEl.innerHTML = "";
    welcomeEl.classList.remove("hidden");
  });

  const nameInput = wizard.querySelector("#wizard-name");
  const goalInput = wizard.querySelector("#wizard-goal");
  const createBtn = wizard.querySelector("#wizard-create");

  function updateCreateBtn() {
    createBtn.disabled = !nameInput.value.trim();
  }
  nameInput.addEventListener("input", updateCreateBtn);

  // Build flowchart
  function renderFlow(flowEl) {
    flowEl.innerHTML = "";
    for (let i = 0; i < STEPS.length; i++) {
      const step = STEPS[i];
      const node = document.createElement("div");
      node.className = "flow-step";
      node.dataset.step = step.id;
      node.innerHTML = `
        <span class="flow-dot pending"></span>
        <span class="flow-label">${step.label}</span>
        <span class="flow-detail"></span>
      `;
      flowEl.appendChild(node);
      if (i < STEPS.length - 1) {
        const connector = document.createElement("div");
        connector.className = "flow-connector";
        flowEl.appendChild(connector);
      }
    }
  }

  function updateStep(flowEl, stepId, status, detail) {
    const node = flowEl.querySelector(`.flow-step[data-step="${stepId}"]`);
    if (!node) return;
    const dot = node.querySelector(".flow-dot");
    const detailSpan = node.querySelector(".flow-detail");
    dot.className = `flow-dot ${status}`;
    if (detail) detailSpan.textContent = detail;
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // Create (scaffold, GitHub repo, then pause for PROMPT.md review)
  createBtn.addEventListener("click", async () => {
    const name = nameInput.value.trim();
    const goal = goalInput.value.trim();
    const constraints = wizard.querySelector("#wizard-constraints").value.trim();
    const primaryMetric = wizard.querySelector("#wizard-metric").value.trim();
    const metricDirection = wizard.querySelector("#wizard-metric-dir").value;
    const metricConstraint = wizard.querySelector("#wizard-metric-constraint").value.trim();
    const root = wizard.querySelector("#wizard-root").value.trim();
    const durationMinutes = parseInt(wizard.querySelector("#wizard-duration").value) || 5;
    const sessionBudgetHours = parseFloat(wizard.querySelector("#wizard-session-budget").value) || 0;
    const agentType = wizard.querySelector("#wizard-agent").value || "claude";
    const effort = wizard.querySelector("#wizard-effort")?.value || "high";
    if (!name) return;

    // Save experiments root for next time
    if (root && root !== settings.experimentsRoot) {
      window.nicolas.saveSettings({ ...settings, experimentsRoot: root });
    }

    // Disable inputs, show flowchart
    createBtn.disabled = true;
    createBtn.textContent = "Running\u2026";
    nameInput.disabled = true;
    goalInput.disabled = true;
    wizard.querySelector("#wizard-constraints").disabled = true;
    wizard.querySelector("#wizard-duration").disabled = true;
    wizard.querySelector("#wizard-root").disabled = true;
    wizard.querySelector("#wizard-browse").disabled = true;

    const flowEl = wizard.querySelector("#wizard-flow");
    flowEl.classList.remove("hidden");
    renderFlow(flowEl);
    flowEl.scrollIntoView({ behavior: "smooth", block: "nearest" });

    let projectId = null;
    let hasError = false;
    let errorDetail = "";

    // Steps 1-4: scaffold via streaming endpoint
    try {
      const workspaceId = wizard.querySelector("#wizard-workspace")?.value || "";
      const compute = wizard.querySelector("#wizard-compute")?.value || "local";
      const modalBudgetUsd = parseFloat(
        wizard.querySelector("#wizard-modal-budget")?.value || "0",
      );
      const body = { name, goal, constraints, duration_minutes: durationMinutes, launch: false, agent_type: agentType, effort };
      if (workspaceId) body.workspace_id = workspaceId;
      if (sessionBudgetHours > 0) body.session_budget_seconds = Math.round(sessionBudgetHours * 3600);
      if (primaryMetric) body.primary_metric = primaryMetric;
      if (metricDirection) body.metric_direction = metricDirection;
      if (metricConstraint) body.metric_constraint = metricConstraint;
      if (root) body.target = root + "/" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      if (compute === "modal") {
        body.compute = "modal";
        body.modal_gpu = "A100-80GB";
        body.modal_budget_usd = modalBudgetUsd > 0 ? modalBudgetUsd : 25.0;
      } else if (compute.startsWith("hfjobs:")) {
        const gpuId = compute.split(":")[1];
        body.compute = "hfjobs";
        body.gpu_type = gpuId;
        body.compute_budget_usd = modalBudgetUsd > 0 ? modalBudgetUsd : 25.0;
      }

      const resp = await fetch(`http://127.0.0.1:${serverPort}/experiments/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const msg = JSON.parse(line);
            if (msg.step) updateStep(flowEl, msg.step, msg.status, msg.detail || "");
            if (msg.project_id || msg.experiment_id) projectId = msg.project_id || msg.experiment_id;
            if (msg.status === "error") { hasError = true; errorDetail = msg.detail || ""; }
          } catch (e) { /* ignore */ }
        }
      }
    } catch (err) {
      hasError = true;
    }

    if (hasError || !projectId) {
      createBtn.textContent = errorDetail ? `Failed: ${errorDetail}` : "Failed";
      createBtn.style.background = "var(--error)";
      createBtn.style.borderColor = "var(--error)";
      createBtn.style.whiteSpace = "normal";
      createBtn.style.height = "auto";
      nameInput.disabled = false;
      goalInput.disabled = false;
      wizard.querySelector("#wizard-constraints").disabled = false;
      wizard.querySelector("#wizard-root").disabled = false;
      wizard.querySelector("#wizard-browse").disabled = false;
      return;
    }

    // Step 5: Create GitHub repo
    updateStep(flowEl, 5, "active", "");
    const privateRepo = settingPrivateRepos ? settingPrivateRepos.checked : false;
    try {
      const ghResp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/github`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `distillate-xp-${projectId}`, private: privateRepo }),
      });
      const ghData = await ghResp.json();
      if (ghData.ok) {
        const repoUrl = ghData.url || "";
        updateStep(flowEl, 5, "done", "");
        // Show copyable repo URL
        if (repoUrl) {
          const visibility = privateRepo ? "private" : "public";
          const visColor = privateRepo ? "var(--yellow)" : "var(--emerald)";
          const urlEl = document.createElement("div");
          urlEl.style.cssText = "margin: 8px 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap;";
          urlEl.innerHTML = `<span style="font-size:10px; font-weight:600; color:${visColor}; background:${privateRepo ? 'rgba(245,158,11,0.1)' : 'rgba(52,211,153,0.1)'}; padding:1px 6px; border-radius:8px; text-transform:uppercase; letter-spacing:0.04em;">${visibility}</span><a href="#" class="paper-external-link" style="font-size:12px;">${repoUrl}</a><button class="paper-action-btn" style="font-size:10px; padding:2px 6px;">Copy</button>`;
          urlEl.querySelector("a").addEventListener("click", (e) => { e.preventDefault(); window.nicolas?.openExternal(repoUrl); });
          urlEl.querySelector("button").addEventListener("click", () => { navigator.clipboard.writeText(repoUrl); urlEl.querySelector("button").textContent = "Copied!"; setTimeout(() => urlEl.querySelector("button").textContent = "Copy", 1500); });
          flowEl.appendChild(urlEl);
        }
      } else {
        updateStep(flowEl, 5, "error", ghData.reason || "");
        // Non-fatal — continue without GitHub
      }
    } catch (e) {
      updateStep(flowEl, 5, "error", "Network error");
    }

    // Show PROMPT.md review
    createBtn.style.display = "none";
    wizard.querySelector("#wizard-cancel").style.display = "none";

    const reviewSection = document.createElement("div");
    reviewSection.className = "wizard-field";
    reviewSection.innerHTML = `
      <label style="margin-top:12px;">Review PROMPT.md <span style="font-weight:400; text-transform:none; color:var(--text-dim);">\u2014 edit before launching</span></label>
      <textarea id="wizard-prompt-review" rows="16" style="font-family:var(--mono); font-size:11px; line-height:1.5;">Loading\u2026</textarea>
      <div class="wizard-actions" style="margin-top:8px;">
        <button class="wizard-btn-cancel" id="wizard-back">Back</button>
        <button class="wizard-btn-create" id="wizard-launch">\u25B6 Launch Experiment</button>
      </div>
    `;
    wizard.appendChild(reviewSection);
    reviewSection.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Fetch PROMPT.md content
    try {
      const promptResp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/prompt`);
      const promptData = await promptResp.json();
      wizard.querySelector("#wizard-prompt-review").value = promptData.ok ? promptData.content : "Failed to load PROMPT.md";
    } catch (e) {
      wizard.querySelector("#wizard-prompt-review").value = "Failed to load PROMPT.md";
    }

    // Back — go to experiment list
    wizard.querySelector("#wizard-back").addEventListener("click", () => {
      refreshExperiments(projectId);
    });

    // Launch — save edits, then launch
    wizard.querySelector("#wizard-launch").addEventListener("click", async () => {
      const launchBtn = wizard.querySelector("#wizard-launch");
      launchBtn.disabled = true;
      launchBtn.textContent = "Saving & launching\u2026";

      // Save edited PROMPT.md
      const editedContent = wizard.querySelector("#wizard-prompt-review").value;
      try {
        await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/prompt`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: editedContent }),
        });
      } catch (e) { /* continue */ }

      // Launch
      updateStep(flowEl, 6, "active", "");
      try {
        const launchResp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/launch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: "claude-sonnet-4-6", agent_type: agentType, effort }),
        });
        const launchData = await launchResp.json();
        if (launchData.ok) {
          updateStep(flowEl, 6, "done", launchData.tmux_session || "");
          launchBtn.textContent = "Launched!";
          launchBtn.style.background = "var(--green)";
          launchBtn.style.borderColor = "var(--green)";
          // Attach xterm to the new tmux session
          // Switch to session tab and attach directly (don't wait for refresh)
          setTimeout(async () => {
            switchEditorTab("session", { skipSessionAttach: true });
            if (launchData.tmux_session) {
              const xtermEl = document.getElementById("xterm-container");
              const emptyEl = document.getElementById("session-empty");
              if (emptyEl) emptyEl.classList.add("hidden");
              if (xtermEl) xtermEl.classList.remove("hidden");
              await attachToTerminalSession(projectId, launchData.tmux_session);
            }
            await refreshExperiments(projectId);
          }, 500);
        } else {
          updateStep(flowEl, 6, "error", launchData.reason || "");
          launchBtn.textContent = "Failed";
          launchBtn.style.background = "var(--error)";
          launchBtn.disabled = false;
        }
      } catch (e) {
        updateStep(flowEl, 6, "error", e.message);
        launchBtn.textContent = "Failed";
        launchBtn.disabled = false;
      }
    });
  });
}

async function launchDemoExperiment() {
  const detailEl = document.getElementById("experiment-detail");
  const welcomeEl = document.getElementById("welcome");
  if (!detailEl || !serverPort) return;

  // Show progress in the detail area
  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  switchEditorTab("control-panel");

  // Build 3-step flowchart (reuse wizard-flow CSS)
  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Setting up your first experiment</h2>
      <p class="exp-detail-meta">A tiny transformer will learn matrix multiplication while you watch.</p>
      <div class="wizard-flow" id="onboarding-flow">
        <div class="flow-step" data-step="1"><span class="flow-dot pending"></span><span class="flow-label">Scaffolding experiment</span><span class="flow-detail"></span></div>
        <div class="flow-step" data-step="2"><span class="flow-dot pending"></span><span class="flow-label">Creating GitHub repository</span><span class="flow-detail"></span></div>
        <div class="flow-step" data-step="3"><span class="flow-dot pending"></span><span class="flow-label">Launching Claude Code agent</span><span class="flow-detail"></span></div>
      </div>
    </div>`;

  const flowEl = document.getElementById("onboarding-flow");

  function updateOnboardingStep(stepId, status, detail) {
    const node = flowEl.querySelector(`.flow-step[data-step="${stepId}"]`);
    if (!node) return;
    const dot = node.querySelector(".flow-dot");
    const detailSpan = node.querySelector(".flow-detail");
    dot.className = `flow-dot ${status}`;
    if (detail) detailSpan.textContent = detail;
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  let projectId = null;

  // Step 1: Scaffold from demo template
  updateOnboardingStep(1, "active", "");
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/scaffold`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: "demo", name: "Addition Grokking" }),
    });
    const data = await r.json();
    if (!data.ok) throw new Error(data.reason || "Scaffold failed");
    projectId = data.project_id;
    updateOnboardingStep(1, "done", data.path);
    if (data.already_exists) {
      // Already scaffolded — skip to launch
      updateOnboardingStep(2, "done", "already created");
    }
  } catch (err) {
    updateOnboardingStep(1, "error", err.message);
    return;
  }

  // Step 2: Create GitHub repo (non-fatal)
  if (!flowEl.querySelector('[data-step="2"] .flow-dot.done')) {
    updateOnboardingStep(2, "active", "");
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/github`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `distillate-xp-${projectId}`, private: false }),
      });
      const data = await r.json();
      if (data.ok) {
        updateOnboardingStep(2, "done", data.url || "");
      } else {
        updateOnboardingStep(2, "skipped", data.reason || "gh CLI not found");
      }
    } catch {
      updateOnboardingStep(2, "skipped", "GitHub unavailable");
    }
  }

  // Step 3: Check harness, then launch Claude Code session
  updateOnboardingStep(3, "active", "");
  try {
    const harnessCheck = await fetch(`http://127.0.0.1:${serverPort}/health/harness`);
    const harnessData = await harnessCheck.json();
    if (!harnessData.ok) {
      const hint = harnessData.install_hint || "npm install -g @anthropic-ai/claude-code";
      const node = flowEl.querySelector('[data-step="3"]');
      if (node) {
        node.querySelector(".flow-dot").className = "flow-dot error";
        node.querySelector(".flow-detail").innerHTML =
          `Claude Code is not installed. Run: <code>${hint}</code> then <button id="harness-recheck-btn" style="margin-left:6px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--surface);cursor:pointer;font-size:11px;">Re-check</button>`;
        document.getElementById("harness-recheck-btn")?.addEventListener("click", launchDemoExperiment);
      }
      return;
    }
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/launch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: "claude-sonnet-4-6" }),
    });
    const data = await r.json();
    if (!data.ok) {
      if (data.reason === "claude_not_found") {
        const hint = data.install_hint || "npm install -g @anthropic-ai/claude-code";
        throw new Error(`Claude Code not found. Install with: ${hint}`);
      }
      throw new Error(data.reason || "Launch failed");
    }
    updateOnboardingStep(3, "done", "Agent started");
  } catch (err) {
    updateOnboardingStep(3, "error", err.message);
    return;
  }

  // Success: refresh sidebar, select project, switch to Session tab
  window._onboardingProjectId = projectId;
  localStorage.setItem("distillate-onboarded", "1");
  await fetchExperimentsList();
  selectProject(projectId);
  switchEditorTab("session");

  // Nudge: if HF Jobs is connected, suggest trying a GPU experiment next
  try {
    const intResp = await fetch(`http://127.0.0.1:${serverPort}/integrations`);
    if (intResp.ok) {
      const intData = await intResp.json();
      const hfjobs = (intData.compute || []).find((c) => c.id === "hfjobs");
      if (hfjobs?.connected) {
        const hint = document.createElement("div");
        hint.className = "onboarding-gpu-hint";
        hint.textContent = "Want to try this on a real GPU? Launch a new experiment and select HF Jobs compute.";
        const flowEl = document.getElementById("onboarding-flow");
        if (flowEl) flowEl.parentElement?.appendChild(hint);
      }
    }
  } catch (_) {}
}
