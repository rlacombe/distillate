/* ───── Integrations — connectors, agents, compute ───── */

/* ───── Connectors ───── */

let cachedConnectors = [];
let cachedAgents = [];
let cachedCompute = [];

async function runHealthCheck() {
  if (!serverPort) return;
  const listEl = document.getElementById("integrations-list");
  if (!listEl) return;

  // Show spinner on the re-check button while running
  const recheckBtn = document.getElementById("integrations-recheck-btn");
  const recheckLabel = recheckBtn ? recheckBtn.querySelector("span") : null;
  if (recheckBtn) {
    recheckBtn.disabled = true;
    recheckBtn.classList.add("checking");
    if (recheckLabel) recheckLabel.textContent = "Checking\u2026";
  }

  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/integrations/health`, { method: "POST" });
    const data = await r.json();
    if (!data.ok) return;

    const health = data.health || {};
    for (const [id, status] of Object.entries(health)) {
      const item = listEl.querySelector(`[data-id="${id}"]`);
      if (!item) continue;
      const dot = item.querySelector(".connector-dot");
      if (!dot || !dot.classList.contains("connected")) continue;

      if (status !== "ok") {
        dot.classList.remove("connected");
        dot.classList.add("warning");
        dot.title = status === "expired"
          ? "Token expired \u2014 click to reconnect"
          : "Connection error \u2014 click to check";
      }
    }
  } catch {}

  if (recheckBtn) {
    recheckBtn.disabled = false;
    recheckBtn.classList.remove("checking");
    if (recheckLabel) recheckLabel.textContent = "Re-check";
  }
}

function fetchIntegrations() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/integrations`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;
      _applyIntegrationsData(data);
    })
    .catch(() => {});
}

function renderIntegrations(data) {
  const listEl = document.getElementById("integrations-list");
  if (!listEl) return;
  listEl.innerHTML = "";

  const library = data.library || [];
  const compute = data.compute || [];
  const agents = data.agents || [];

  // --- MODELS ---
  // Extract model-type entries from agents list (LLM providers)
  const models = agents.filter((a) => a.category === "model" || a.id === "anthropic" || a.id === "distillate-sdk");
  const harnesses = agents.filter((a) => a.category === "harness" || (a.id !== "anthropic" && a.id !== "distillate-sdk" && !models.includes(a)));

  // Only render the MODELS header when there's at least one model; the
  // backend doesn't yet emit `category`, so in practice this is empty and
  // the label was rendering alone before.
  if (models.length > 0) {
    listEl.appendChild(_makeSectionHeader("Models"));
    for (const a of models) {
      listEl.appendChild(_makeAgentIntegrationItem(a));
    }
  }

  // --- HARNESSES ---
  // "+ Add custom harness" belongs under HARNESSES specifically — not at
  // the very bottom of the list, disconnected from its semantic section.
  if (harnesses.length > 0) {
    listEl.appendChild(_makeSectionHeader("Harnesses"));
    for (const a of harnesses) {
      listEl.appendChild(_makeAgentIntegrationItem(a));
    }
    listEl.appendChild(_makeAddHarnessRow());
  }

  // --- KNOWLEDGE ---
  if (library.length > 0) {
    listEl.appendChild(_makeSectionHeader("Knowledge"));
    for (const c of library) {
      listEl.appendChild(_makeConnectorItem(c));
    }
  }

  // --- COMPUTE ---
  if (compute.length > 0) {
    listEl.appendChild(_makeSectionHeader("Compute"));
    for (const c of compute) {
      const item = document.createElement("div");
      item.className = "connector-item" + (c.connected ? " connected" : "");
      item.dataset.id = c.id;

      const dot = document.createElement("span");
      dot.className = "connector-dot" + (c.connected ? " connected" : "");
      item.appendChild(dot);

      const nameGroup = document.createElement("div");
      nameGroup.className = "connector-name-group";
      const lbl = document.createElement("span");
      lbl.className = "connector-label";
      lbl.textContent = c.label;
      nameGroup.appendChild(lbl);
      if (c.detail) {
        const sub = document.createElement("span");
        sub.className = "connector-sub";
        sub.textContent = c.detail;
        nameGroup.appendChild(sub);
      }
      item.appendChild(nameGroup);

      if (!c.connected && c.setup) {
        const hint = document.createElement("span");
        hint.className = "connector-hint";
        hint.textContent = "Set up";
        item.appendChild(hint);
      }

      item.addEventListener("click", () => handleComputeClick(c));
      listEl.appendChild(item);
    }
  }

  // Fallback: if no agents have category tags, render them in one flat
  // HARNESSES section. The backend currently lands everything here
  // because it doesn't emit `category` yet — so this is actually the
  // hot path in production, not a rare fallback.
  if (models.length === 0 && harnesses.length === 0 && agents.length > 0) {
    listEl.appendChild(_makeSectionHeader("Harnesses"));
    for (const a of agents) {
      listEl.appendChild(_makeAgentIntegrationItem(a));
    }
    listEl.appendChild(_makeAddHarnessRow());
  }

  // Wire up the Re-check button (now in settings HTML)
  const recheckBtn = document.getElementById("integrations-recheck-btn");
  if (recheckBtn && !recheckBtn.dataset.integrated) {
    recheckBtn.dataset.integrated = "1";
    recheckBtn.addEventListener("click", () => runHealthCheck());
  }

  // Fire health check after render (non-blocking)
  setTimeout(() => runHealthCheck(), 500);
}

function _makeSectionHeader(label) {
  // Section header used to separate Models / Harnesses / Knowledge /
  // Compute. Mixed-case looks less shouty than the previous ALL-CAPS, which
  // read like a mainframe field label inside what is a consumer settings
  // pane.
  const el = document.createElement("div");
  el.className = "integration-subgroup-label";
  el.textContent = label;
  return el;
}

function _makeAddHarnessRow() {
  const item = document.createElement("div");
  item.className = "connector-item add-agent";
  const icon = document.createElement("span");
  icon.className = "add-agent-icon";
  icon.textContent = "+";
  item.appendChild(icon);
  const label = document.createElement("span");
  label.className = "add-agent-label";
  label.textContent = "Add custom harness";
  item.appendChild(label);
  item.addEventListener("click", () => showAddPiAgentForm());
  return item;
}

function _makeAgentIntegrationItem(a) {
  const item = document.createElement("div");
  item.className = "connector-item" + (a.available ? " connected" : "");
  item.dataset.id = a.id;

  const dot = document.createElement("span");
  dot.className = "connector-dot" + (a.available ? " connected" : "");
  item.appendChild(dot);

  const nameGroup = document.createElement("div");
  nameGroup.className = "connector-name-group";
  const label = document.createElement("span");
  label.className = "connector-label";
  label.textContent = a.label;
  nameGroup.appendChild(label);
  if (a.description) {
    const sub = document.createElement("span");
    sub.className = "connector-sub";
    sub.textContent = a.description;
    nameGroup.appendChild(sub);
  }
  item.appendChild(nameGroup);

  if (a.available) {
    const badge = document.createElement("span");
    badge.className = "connector-badge";
    badge.textContent = a.mcp ? "MCP" : "scan";
    item.appendChild(badge);
  } else if (a.install) {
    const hint = document.createElement("span");
    hint.className = "connector-hint";
    hint.textContent = "Install";
    item.appendChild(hint);
  }

  item.addEventListener("click", () => handleAgentClick(a));
  return item;
}

function _makeConnectorItem(c) {
  const item = document.createElement("div");
  item.className = "connector-item" + (c.connected ? " connected" : "");
  item.dataset.id = c.id;
  item.dataset.setup = c.setup || "";

  const dot = document.createElement("span");
  dot.className = "connector-dot" + (c.connected ? " connected" : "");
  item.appendChild(dot);

  const nameGroup = document.createElement("div");
  nameGroup.className = "connector-name-group";
  const label = document.createElement("span");
  label.className = "connector-label";
  label.textContent = c.label;
  nameGroup.appendChild(label);

  const subText = (c.connected && c.detail) ? c.detail : c.service;
  if (subText) {
    const sub = document.createElement("span");
    sub.className = "connector-sub";
    sub.textContent = subText;
    nameGroup.appendChild(sub);
  }
  item.appendChild(nameGroup);

  if (c.icon && c.connected) {
    const icon = document.createElement("img");
    icon.className = "connector-icon";
    icon.src = `/ui/icons/${c.icon}.svg`;
    icon.alt = "";
    item.appendChild(icon);
  } else if (!c.connected && c.setup) {
    const hint = document.createElement("span");
    hint.className = "connector-hint";
    hint.textContent = "Set up";
    item.appendChild(hint);
  }

  item.addEventListener("click", () => handleConnectorClick(c));
  return item;
}

function renderConnectors(connectors) {
  renderIntegrations({ library: connectors, compute: cachedCompute, agents: cachedAgents });
}

function handleConnectorClick(connector) {
  if (!connector.connected) {
    launchConnectorSetup(connector.setup);
  } else {
    showConnectorSettings(connector.id);
  }
}

function handleComputeClick(compute) {
  if (!compute.connected && compute.setup) {
    showComputeSetup(compute);
  } else {
    showComputeDetail(compute);
  }
}

async function showConnectorSettings(connectorId) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");

  // Show control panel view
  const cpView = document.getElementById("control-panel-view");
  if (cpView && cpView.classList.contains("hidden")) {
    for (const v of editorViews) {
      const viewEl = document.getElementById(`${v}-view`);
      if (viewEl) viewEl.classList.toggle("hidden", v !== "control-panel");
    }
  }

  detailEl.innerHTML = '<div class="exp-detail-loading">Loading...</div>';

  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/connectors/${connectorId}`);
    const data = await r.json();
    if (!data.ok) throw new Error(data.reason || "Failed to load");
    const c = data.connector;

    detailEl.innerHTML = "";

    const header = document.createElement("div");
    header.className = "exp-detail-header";

    const titleRow = document.createElement("div");
    titleRow.className = "exp-detail-title-row";
    const title = document.createElement("h2");
    title.className = "exp-detail-title";
    title.textContent = c.label;
    const badge = document.createElement("span");
    const emailPending = c.id === "email" && c.connected && !c.verified;
    badge.className = `exp-detail-badge ${!c.connected ? "paused" : emailPending ? "waiting" : "running"}`;
    badge.textContent = !c.connected ? "not connected" : emailPending ? "pending verification" : "connected";

    titleRow.appendChild(title);
    titleRow.appendChild(badge);
    header.appendChild(titleRow);

    const service = document.createElement("div");
    service.className = "exp-detail-meta";
    service.style.marginTop = "4px";
    service.textContent = c.service;
    header.appendChild(service);

    detailEl.appendChild(header);

    // Settings table
    if (c.settings && c.settings.length) {
      const section = document.createElement("div");
      section.className = "connector-settings";

      for (const s of c.settings) {
        const row = document.createElement("div");
        row.className = "connector-setting-row";

        const label = document.createElement("span");
        label.className = "connector-setting-label";
        label.textContent = s.label;
        row.appendChild(label);

        const value = document.createElement("span");
        value.className = "connector-setting-value";
        value.textContent = s.value || "—";
        if (s.sensitive && s.value) value.classList.add("sensitive");
        row.appendChild(value);

        section.appendChild(row);
      }

      detailEl.appendChild(section);
    }

    // Email: resend verification link if pending
    if (c.id === "email" && c.connected && !c.verified) {
      const verifyRow = document.createElement("div");
      verifyRow.style.cssText = "margin-top:12px;font-size:12px;color:var(--text-dim);";
      verifyRow.innerHTML = 'Check your inbox to verify · <a href="#" id="resend-verify-settings" style="color:var(--accent);">Resend</a>';
      detailEl.appendChild(verifyRow);
      document.getElementById("resend-verify-settings")?.addEventListener("click", async (e) => {
        e.preventDefault();
        const link = e.target;
        link.textContent = "Sending...";
        try {
          const r = await fetch(`http://127.0.0.1:${serverPort}/email/resend-verification`, { method: "POST" });
          const d = await r.json();
          link.textContent = d.ok ? "Sent!" : "Failed";
        } catch { link.textContent = "Failed"; }
      });
    }

    // Action buttons
    const actions = document.createElement("div");
    actions.className = "connector-actions";
    const reconfigBtn = document.createElement("button");
    reconfigBtn.className = "onboarding-btn";
    reconfigBtn.textContent = c.connected ? "Reconfigure" : "Set up";
    reconfigBtn.addEventListener("click", () => launchConnectorSetup(c.id === "zotero" ? "library" : c.id === "email" ? "email" : c.id));
    actions.appendChild(reconfigBtn);

    // Disconnect button for HuggingFace
    if (c.id === "huggingface" && c.connected) {
      const disconnectBtn = document.createElement("button");
      disconnectBtn.className = "onboarding-btn";
      disconnectBtn.style.cssText = "margin-left:8px; background:transparent; color:var(--text-dim); border:1px solid var(--border);";
      disconnectBtn.textContent = "Disconnect";
      disconnectBtn.addEventListener("click", async () => {
        try {
          await fetch(`http://127.0.0.1:${serverPort}/huggingface/setup`, { method: "DELETE" });
          fetchIntegrations();
          showConnectorSettings("huggingface");
        } catch {}
      });
      actions.appendChild(disconnectBtn);
    }

    detailEl.appendChild(actions);

  } catch (err) {
    detailEl.innerHTML = `<div class="exp-detail-loading">Failed: ${err.message}</div>`;
  }
}

function launchConnectorSetup(setup) {
  if (setup === "library") {
    launchLibrarySetup();
  } else if (setup === "email") {
    launchEmailSetup();
  } else if (setup === "remarkable") {
    // reMarkable auth is interactive — guide Nicolas to walk through rmapi setup
    showToast("reMarkable setup requires terminal interaction — Nicolas will guide you.", "info");
    inputEl.value = "I'd like to connect my reMarkable tablet. Walk me through the rmapi CLI setup.";
    sendMessage();
  } else if (setup === "obsidian") {
    launchObsidianSetup();
  } else if (setup === "huggingface") {
    launchHuggingFaceSetup();
  }
}

function launchObsidianSetup() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");

  const cpView = document.getElementById("control-panel-view");
  if (cpView && cpView.classList.contains("hidden")) {
    for (const v of editorViews) {
      const viewEl = document.getElementById(`${v}-view`);
      if (viewEl) viewEl.classList.toggle("hidden", v !== "control-panel");
    }
  }

  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Connect Obsidian</h2>
      <p class="exp-detail-meta" style="margin-top:6px;">Point Distillate to your vault to write paper notes and lab entries.</p>
      <div class="library-setup-wizard" style="margin-top:20px;">
        <div class="library-step" id="obsidian-step-path">
          <div class="library-step-header">
            <span class="library-step-num">1</span>
            <span>Vault path</span>
          </div>
          <p class="library-step-help">
            The folder where your Obsidian vault lives. Usually <code>~/Documents/MyVault</code> or similar.
            The folder must already exist and contain a <code>.obsidian/</code> directory.
          </p>
          <div class="library-field">
            <label for="obsidian-vault-path">Vault path</label>
            <input type="text" id="obsidian-vault-path" placeholder="~/Documents/MyVault"
                   spellcheck="false" autocomplete="off">
          </div>
          <div class="library-error hidden" id="obsidian-error"></div>
          <button class="onboarding-btn" id="obsidian-connect-btn" style="margin-top:8px;">Connect</button>
        </div>

        <div class="library-step hidden" id="obsidian-step-success">
          <div class="library-step-header">
            <span class="library-step-num" style="background:var(--green); color:#fff;">✓</span>
            <span>Connected</span>
          </div>
          <div id="obsidian-success-info" style="font-size:12px; color:var(--text-dim); margin-top:8px;"></div>
        </div>
      </div>
    </div>
  `;

  const connectBtn = document.getElementById("obsidian-connect-btn");
  const errorEl = document.getElementById("obsidian-error");

  connectBtn?.addEventListener("click", async () => {
    const path = document.getElementById("obsidian-vault-path")?.value?.trim();

    if (!path) {
      errorEl.textContent = "Vault path is required";
      errorEl.classList.remove("hidden");
      return;
    }

    connectBtn.disabled = true;
    connectBtn.textContent = "Checking\u2026";
    errorEl.classList.add("hidden");

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/obsidian/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vault_path: path }),
      });
      const data = await r.json();

      if (data.ok) {
        document.getElementById("obsidian-step-path").classList.add("library-step-done");
        const successEl = document.getElementById("obsidian-step-success");
        successEl.classList.remove("hidden");
        document.getElementById("obsidian-success-info").innerHTML =
          `<div style="margin-bottom:4px;"><strong style="color:var(--text);">${escapeHtml(data.vault_name)}</strong></div>` +
          `<div>Paper notes will be saved to <code>${escapeHtml(data.papers_folder)}/</code> inside your vault.</div>`;
        fetchIntegrations();
      } else {
        errorEl.textContent = data.reason || "Connection failed";
        errorEl.classList.remove("hidden");
        connectBtn.disabled = false;
        connectBtn.textContent = "Connect";
      }
    } catch {
      errorEl.textContent = "Connection failed \u2014 is the server running?";
      errorEl.classList.remove("hidden");
      connectBtn.disabled = false;
      connectBtn.textContent = "Connect";
    }
  });

  document.getElementById("obsidian-vault-path")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") connectBtn?.click();
  });
}

async function launchHuggingFaceSetup() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");

  // Check OAuth status to decide which panels to show
  let authStatus = null;
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/auth/status`);
    if (r.ok) authStatus = await r.json();
  } catch {}
  const oauthActive = authStatus?.signed_in && authStatus?.user;
  const displayName = oauthActive ? (authStatus.user.display_name || authStatus.user.email || "") : "";

  const oauthPanel = oauthActive
    ? `<div class="library-step" id="hf-oauth-panel" style="margin-bottom:16px;">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
          <div>
            <span style="color:var(--green); font-weight:600; margin-right:6px;">✓</span>
            <span id="hf-oauth-status" style="color:var(--text); font-size:13px;">Signed in as @${escapeHtml(displayName)}</span>
          </div>
          <button id="hf-signout-btn" style="font-size:12px; padding:4px 10px; border-radius:6px; border:1px solid var(--border); background:var(--surface); color:var(--text-dim); cursor:pointer;">
            Sign out
          </button>
        </div>
      </div>`
    : `<div class="library-step" id="hf-signin-panel" style="margin-bottom:16px;">
        <button class="welcome-hf-signin-btn" id="hf-signin-btn"
                style="display:flex;align-items:center;gap:8px;padding:8px 14px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--text);font-size:13px;cursor:pointer;width:100%;">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0;">
            <path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2z" fill="#FFD21E"/>
            <path d="M8 11.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM16 11.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z" fill="#1a1a1a"/>
            <path d="M8.5 15.5c1 1 5 1 7 0" stroke="#1a1a1a" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
          <span>Sign in with Hugging Face</span>
        </button>
      </div>`;

  const tokenLabel = oauthActive
    ? "HF token override (advanced, optional)"
    : "Token";
  const tokenHelp = oauthActive
    ? `<p class="library-step-help" style="color:var(--text-dim);font-size:11px;margin-bottom:8px;">
        For fine-grained or bot tokens only. Normal inference, Hub, and Jobs use the OAuth session.
      </p>`
    : `<p class="library-step-help">
        Create a free account and get your token at
        <a href="#" class="library-link" onclick="window.nicolas?.openExternal?.('https://huggingface.co/settings/tokens'); return false;">
          huggingface.co/settings/tokens
        </a>
      </p>`;

  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Connect Hugging Face</h2>
      <p class="exp-detail-meta">
        Access open-weight models, cloud GPUs, and the Hub ecosystem.
      </p>

      <div class="library-setup-wizard" id="hf-wizard" style="margin-top:20px;">
        ${oauthPanel}

        <div class="library-step" id="hf-step-token">
          <div class="library-step-header">
            <span class="library-step-num">${oauthActive ? "●" : "1"}</span>
            <span>${oauthActive ? "HF token override (advanced, optional)" : "API token"}</span>
          </div>
          ${tokenHelp}
          <div class="library-field">
            <label for="hf-token-input">${escapeHtml(tokenLabel)}</label>
            <input type="password" id="hf-token-input" placeholder="hf_..." spellcheck="false" autocomplete="off">
          </div>
          <div class="library-error hidden" id="hf-token-error"></div>
          <button class="onboarding-btn" id="hf-token-submit" style="margin-top:8px;">
            Connect
          </button>
        </div>

        <div class="library-step hidden" id="hf-step-success">
          <div class="library-step-header">
            <span class="library-step-num" style="background:var(--green); color:#fff;">✓</span>
            <span>Connected</span>
          </div>
          <div id="hf-account-info" style="font-size:12px; color:var(--text-dim); margin-top:8px;"></div>
          <div id="hf-features" style="margin-top:12px; font-size:12px;"></div>
        </div>
      </div>
    </div>
  `;

  // Wire OAuth sign-in button
  document.getElementById("hf-signin-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("hf-signin-btn");
    if (btn) { btn.disabled = true; btn.style.opacity = "0.6"; }
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/auth/signin-hf-start`, { method: "POST" });
      const data = await r.json();
      if (data.ok && data.authorize_url && window.nicolas?.openExternal) {
        window.nicolas.openExternal(data.authorize_url);
      }
    } catch (err) {
      console.error("Failed to start HF sign-in:", err);
    } finally {
      if (btn) { btn.disabled = false; btn.style.opacity = ""; }
    }
  });

  // Wire OAuth sign-out button
  document.getElementById("hf-signout-btn")?.addEventListener("click", async () => {
    try {
      await fetch(`http://127.0.0.1:${serverPort}/auth/logout`, { method: "POST" });
    } catch {}
    fetchIntegrations();
    launchHuggingFaceSetup();
  });

  // Submit handler
  document.getElementById("hf-token-submit")?.addEventListener("click", async () => {
    const token = document.getElementById("hf-token-input")?.value?.trim();
    const errorEl = document.getElementById("hf-token-error");
    const submitBtn = document.getElementById("hf-token-submit");

    if (!token) {
      errorEl.classList.remove("hidden");
      errorEl.textContent = "Token is required";
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Validating...";
    errorEl.classList.add("hidden");

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/huggingface/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const data = await r.json();

      if (data.ok) {
        // Hide token step, show success
        document.getElementById("hf-step-token").classList.add("library-step-done");
        const successEl = document.getElementById("hf-step-success");
        successEl.classList.remove("hidden");

        // Account info
        const accountEl = document.getElementById("hf-account-info");
        const plan = data.plan || "free";
        accountEl.innerHTML = `
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
            <strong style="color:var(--text);">${escapeHtml(data.username)}</strong>
            <span style="background:var(--surface); padding:1px 6px; border-radius:4px; font-size:10px; text-transform:uppercase; letter-spacing:0.03em;">${escapeHtml(plan)}</span>
          </div>
        `;

        // Features unlocked
        const featuresEl = document.getElementById("hf-features");
        const features = [
          { icon: "✓", label: "Hub search", detail: "Models, datasets & papers", on: true },
          { icon: "✓", label: "Inference Providers", detail: "15+ LLM providers via Pi agent", on: true },
          { icon: "✓", label: "MCP tools", detail: "Agents can search HF Hub", on: true },
          { icon: data.can_pay ? "✓" : "–", label: "HF Jobs compute", detail: data.can_pay ? "A100, H200, L40S — ready" : "Add credits at huggingface.co/settings/billing", on: data.can_pay },
        ];
        featuresEl.innerHTML = features.map(f => `
          <div style="display:flex; align-items:baseline; gap:6px; margin-bottom:4px;">
            <span style="color:${f.on ? 'var(--green)' : 'var(--text-dim)'}; font-weight:600; width:14px;">${f.icon}</span>
            <span style="color:var(--text); font-weight:500;">${escapeHtml(f.label)}</span>
            <span style="color:var(--text-dim);">— ${escapeHtml(f.detail)}</span>
          </div>
        `).join("");

        fetchIntegrations();
      } else {
        errorEl.classList.remove("hidden");
        errorEl.textContent = data.reason || "Invalid token";
        submitBtn.disabled = false;
        submitBtn.textContent = "Connect";
      }
    } catch (e) {
      errorEl.classList.remove("hidden");
      errorEl.textContent = "Connection failed — is the server running?";
      submitBtn.disabled = false;
      submitBtn.textContent = "Connect";
    }
  });

  // Enter key submits
  document.getElementById("hf-token-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("hf-token-submit")?.click();
  });
}

function launchEmailSetup() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");

  const cpView = document.getElementById("control-panel-view");
  if (cpView && cpView.classList.contains("hidden")) {
    for (const v of editorViews) {
      const viewEl = document.getElementById(`${v}-view`);
      if (viewEl) viewEl.classList.toggle("hidden", v !== "control-panel");
    }
  }

  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Create your account</h2>
      <p class="exp-detail-meta" style="margin-top:6px;">Sync your library across devices and choose what lands in your inbox.</p>
      <div class="library-setup-wizard" style="margin-top:20px;">
        <div class="library-step" id="email-step">
          <div class="library-step-header">
            <span class="library-step-num">1</span>
            <span>Your email</span>
          </div>
          <div class="library-field">
            <label for="email-setup-input">Email address</label>
            <input type="email" id="email-setup-input" placeholder="you@example.com" spellcheck="false" autocomplete="email">
          </div>

          <div class="library-step-header" style="margin-top:16px;">
            <span class="library-step-num">2</span>
            <span>What to receive</span>
          </div>

          <div class="email-toggle-list">
            <label class="email-toggle">
              <input type="checkbox" name="email-experiment-reports" checked>
              <div class="email-toggle-text">
                <span class="email-toggle-name">Experiment reports</span>
                <span class="email-toggle-desc">When an experiment completes — results, best metric, key insight</span>
              </div>
            </label>
            <label class="email-toggle">
              <input type="checkbox" name="email-daily-papers" checked>
              <div class="email-toggle-text">
                <span class="email-toggle-name">Daily paper suggestions</span>
                <span class="email-toggle-desc">Every morning at 7am — three papers matching your interests</span>
              </div>
            </label>
            <label class="email-toggle">
              <input type="checkbox" name="email-weekly-digest" checked>
              <div class="email-toggle-text">
                <span class="email-toggle-name">Weekly digest</span>
                <span class="email-toggle-desc">Every Monday — papers read, experiments ran, highlights</span>
              </div>
            </label>
          </div>

          <p class="email-privacy-note">Emails are sent from distillate.dev via Supabase and Resend. Enabling updates stores your email and a summary of your reading and experiment activity. You can disable at any time.</p>
          <div class="library-error hidden" id="email-setup-error"></div>
          <button class="onboarding-btn" id="email-setup-submit" style="margin-top:12px;">Enable updates</button>
        </div>
      </div>
    </div>`;

  document.getElementById("email-setup-submit")?.addEventListener("click", async () => {
    const email = document.getElementById("email-setup-input")?.value?.trim();
    const errorEl = document.getElementById("email-setup-error");

    if (!email || !email.includes("@")) {
      if (errorEl) { errorEl.textContent = "Please enter a valid email."; errorEl.classList.remove("hidden"); }
      return;
    }

    const experimentReports = document.querySelector('[name="email-experiment-reports"]')?.checked ?? true;
    const dailyPapers = document.querySelector('[name="email-daily-papers"]')?.checked ?? true;
    const weeklyDigest = document.querySelector('[name="email-weekly-digest"]')?.checked ?? true;

    const btn = document.getElementById("email-setup-submit");
    if (btn) { btn.disabled = true; btn.textContent = "Setting up..."; }

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/email/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          experiment_reports: experimentReports,
          daily_papers: dailyPapers,
          weekly_digest: weeklyDigest,
        }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.reason || "Failed");

      localStorage.setItem("distillate-email-asked", "1");
      const enabled = [];
      if (experimentReports) enabled.push("experiment reports");
      if (dailyPapers) enabled.push("daily papers");
      if (weeklyDigest) enabled.push("weekly digest");
      const enabledText = enabled.join(", ").replace(/^./, c => c.toUpperCase());
      const verified = data.verified;
      const stepEl = document.getElementById("email-step");
      stepEl.innerHTML =
        '<div style="text-align:center;padding:24px 16px;">' +
        '<div style="font-size:28px;margin-bottom:6px;">✓</div>' +
        '<div style="font-size:15px;font-weight:600;color:var(--green);margin-bottom:6px;">You\'re in!</div>' +
        '<div style="font-size:13px;color:var(--text-dim);margin-bottom:12px;">' + enabledText + ' coming to ' + email + '</div>' +
        (verified ? '' :
          '<div style="font-size:12px;color:var(--warning);margin-bottom:6px;">Check your inbox to verify your email</div>' +
          '<a href="#" id="resend-verify-link" style="font-size:12px;color:var(--text-dim);">Resend verification email</a>') +
        '</div>';
      if (!verified) {
        document.getElementById("resend-verify-link")?.addEventListener("click", async (e) => {
          e.preventDefault();
          const link = e.target;
          link.textContent = "Sending...";
          link.style.pointerEvents = "none";
          try {
            const r = await fetch(`http://127.0.0.1:${serverPort}/email/resend-verification`, { method: "POST" });
            const d = await r.json();
            link.textContent = d.ok ? "Sent! Check your inbox" : "Failed — try again";
          } catch { link.textContent = "Failed — try again"; }
          link.style.pointerEvents = "";
        });
      }
      fetchIntegrations();
    } catch (err) {
      if (errorEl) { errorEl.textContent = err.message; errorEl.classList.remove("hidden"); }
      if (btn) { btn.disabled = false; btn.textContent = "Enable updates"; }
    }
  });
}

/* ───── Agents ───── */

function handleAgentClick(agent) {
  if (!agent.available) {
    showAgentInstall(agent);
  } else {
    showAgentDetail(agent);
  }
}

function showAgentInstall(agent) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  const welcomeEl = document.getElementById("welcome-screen");
  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");

  detailEl.innerHTML = "";

  const header = document.createElement("div");
  header.className = "exp-detail-header";

  const titleRow = document.createElement("div");
  titleRow.className = "exp-detail-title-row";
  const title = document.createElement("h2");
  title.className = "exp-detail-title";
  title.textContent = agent.label;
  const badge = document.createElement("span");
  badge.className = "exp-detail-badge paused";
  badge.textContent = "not installed";
  titleRow.appendChild(title);
  titleRow.appendChild(badge);
  header.appendChild(titleRow);

  const desc = document.createElement("div");
  desc.className = "exp-detail-meta";
  desc.textContent = agent.description;
  header.appendChild(desc);

  detailEl.appendChild(header);

  // Install instructions
  const section = document.createElement("div");
  section.style.cssText = "padding: 16px 20px;";

  section.innerHTML = `
    <div style="margin-bottom:16px;">
      <h3 style="font-size:13px; margin:0 0 8px; color:var(--text);">Install</h3>
      <div style="background:var(--bg-dark, #1a1a2e); border-radius:8px; padding:10px 14px; font-family:monospace; font-size:12px; color:var(--text); user-select:all; cursor:text;">
        ${escapeHtml(agent.install)}
      </div>
    </div>
    <div style="margin-bottom:16px;">
      <h3 style="font-size:13px; margin:0 0 8px; color:var(--text);">Auth</h3>
      <p style="font-size:12px; color:var(--text-dim); margin:0;">${escapeHtml(agent.auth || "No auth required")}</p>
    </div>
    <div style="margin-bottom:16px;">
      <h3 style="font-size:13px; margin:0 0 8px; color:var(--text);">MCP support</h3>
      <p style="font-size:12px; color:var(--text-dim); margin:0;">${agent.mcp ? "Yes \u2014 full tool integration with Distillate" : "No \u2014 writes directly to runs.jsonl (scanner picks up results)"}</p>
    </div>
    <div style="display:flex; gap:8px; margin-top:20px;">
      <button id="agent-install-btn" class="wizard-btn-create" style="font-size:12px; padding:8px 16px;">Install now</button>
      ${agent.url ? `<button id="agent-docs-btn" class="wizard-btn-cancel" style="font-size:12px; padding:8px 16px;">Documentation</button>` : ""}
    </div>
    <pre id="agent-install-output" style="display:none; margin-top:12px; background:var(--bg-dark, #1a1a2e); border-radius:8px; padding:10px 14px; font-size:11px; color:var(--text-dim); max-height:200px; overflow:auto; white-space:pre-wrap;"></pre>
  `;
  detailEl.appendChild(section);

  // Install button handler
  section.querySelector("#agent-install-btn")?.addEventListener("click", async () => {
    const btn = section.querySelector("#agent-install-btn");
    const output = section.querySelector("#agent-install-output");
    btn.disabled = true;
    btn.textContent = "Installing\u2026";
    output.style.display = "block";
    output.textContent = "$ " + agent.install + "\\n";

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/agents/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: agent.id }),
      });
      const data = await r.json();
      if (data.ok) {
        output.textContent += data.output || "Installed successfully!";
        btn.textContent = "Installed";
        badge.className = "exp-detail-badge running";
        badge.textContent = "installed";
        // Refresh integrations list
        fetchIntegrations();
      } else {
        output.textContent += data.reason || "Install failed";
        btn.disabled = false;
        btn.textContent = "Retry";
      }
    } catch (err) {
      output.textContent += "Error: " + err.message;
      btn.disabled = false;
      btn.textContent = "Retry";
    }
  });

  // Docs button
  section.querySelector("#agent-docs-btn")?.addEventListener("click", () => {
    if (agent.url) window.nicolas?.openExternal?.(agent.url) || window.open(agent.url, "_blank");
  });
}

function showAgentDetail(agent) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  const welcomeEl = document.getElementById("welcome-screen");
  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");

  detailEl.innerHTML = "";

  const header = document.createElement("div");
  header.className = "exp-detail-header";
  const titleRow = document.createElement("div");
  titleRow.className = "exp-detail-title-row";
  const title = document.createElement("h2");
  title.className = "exp-detail-title";
  title.textContent = agent.label;
  const badge = document.createElement("span");
  badge.className = "exp-detail-badge running";
  badge.textContent = "installed";
  titleRow.appendChild(title);
  titleRow.appendChild(badge);
  header.appendChild(titleRow);

  const desc = document.createElement("div");
  desc.className = "exp-detail-meta";
  desc.textContent = agent.description;
  header.appendChild(desc);

  detailEl.appendChild(header);

  const section = document.createElement("div");
  section.style.cssText = "padding: 16px 20px;";
  section.innerHTML = `
    <div style="margin-bottom:12px;">
      <span style="font-size:12px; color:var(--text-dim);">MCP: </span>
      <span style="font-size:12px; color:var(--text);">${agent.mcp ? "Yes" : "No (scan-only)"}</span>
    </div>
    <div style="margin-bottom:12px;">
      <span style="font-size:12px; color:var(--text-dim);">Auth: </span>
      <span style="font-size:12px; color:var(--text);">${escapeHtml(agent.auth || "None")}</span>
    </div>
    <div style="margin-bottom:12px;">
      <span style="font-size:12px; color:var(--text-dim);">Protocol: </span>
      <span style="font-size:12px; color:var(--text);">${escapeHtml(agent.context_file)}</span>
    </div>
  `;
  if (agent.model) {
    section.innerHTML += `
    <div style="margin-bottom:12px;">
      <span style="font-size:12px; color:var(--text-dim);">Model: </span>
      <span style="font-size:12px; color:var(--text); font-family:var(--mono);">${escapeHtml(agent.model)}</span>
    </div>`;
  }
  detailEl.appendChild(section);

  if (agent.variant) {
    const removeBtn = document.createElement("button");
    removeBtn.className = "wizard-btn-cancel";
    removeBtn.style.cssText = "font-size:12px; padding:8px 16px; margin:0 20px 16px; color:var(--error);";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", async () => {
      removeBtn.disabled = true;
      removeBtn.textContent = "Removing\u2026";
      try {
        await fetch(`http://127.0.0.1:${serverPort}/agents/pi/${encodeURIComponent(agent.id)}`, { method: "DELETE" });
        fetchIntegrations();
        detailEl.innerHTML = "";
        detailEl.classList.add("hidden");
        welcomeEl?.classList.remove("hidden");
      } catch {
        removeBtn.disabled = false;
        removeBtn.textContent = "Remove";
      }
    });
    detailEl.appendChild(removeBtn);
  }
}

/* ───── Add Pi agent form ───── */
async function showAddPiAgentForm() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;
  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = "";

  const header = document.createElement("div");
  header.className = "exp-detail-header";
  const titleRow = document.createElement("div");
  titleRow.className = "exp-detail-title-row";
  const title = document.createElement("h2");
  title.className = "exp-detail-title";
  title.textContent = "New Pi Agent";
  titleRow.appendChild(title);
  header.appendChild(titleRow);
  const desc = document.createElement("div");
  desc.className = "exp-detail-meta";
  desc.textContent = "Create a Pi agent variant with a specific LLM backend.";
  header.appendChild(desc);
  detailEl.appendChild(header);

  let models = [];
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/agents/pi/models`);
    const data = await r.json();
    if (data.ok) models = data.models || [];
  } catch { /* use empty */ }

  const section = document.createElement("div");
  section.style.cssText = "padding: 16px 20px;";
  const modelOptions = models.map((m) =>
    `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`
  ).join("");

  section.innerHTML = `
    <div class="wizard-field" style="margin-bottom:12px;">
      <label style="display:block; font-size:11px; font-weight:600; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px;">Model</label>
      <select id="pi-model" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:6px 8px;">
        ${modelOptions}
      </select>
    </div>
    <div class="wizard-field" style="margin-bottom:12px;">
      <label style="display:block; font-size:11px; font-weight:600; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px;">Name</label>
      <input id="pi-label" type="text" placeholder="Pi \u00b7 Opus 4.6" spellcheck="false" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:6px 8px;" />
    </div>
    <div style="display:flex; gap:8px; margin-top:16px;">
      <button id="pi-cancel" class="wizard-btn-cancel" style="font-size:12px; padding:8px 16px;">Cancel</button>
      <button id="pi-create" class="wizard-btn-create" style="font-size:12px; padding:8px 16px;">Create</button>
    </div>
    <div id="pi-error" class="hidden" style="margin-top:8px; font-size:12px; color:var(--error);"></div>
  `;
  detailEl.appendChild(section);

  const modelSelect = section.querySelector("#pi-model");
  const labelInput = section.querySelector("#pi-label");
  if (modelSelect && labelInput && models.length) {
    labelInput.value = `Pi \u00b7 ${models[0].label}`;
    modelSelect.addEventListener("change", () => {
      const m = models.find((x) => x.id === modelSelect.value);
      if (m) labelInput.value = `Pi \u00b7 ${m.label}`;
    });
  }

  section.querySelector("#pi-cancel")?.addEventListener("click", () => {
    detailEl.innerHTML = "";
    detailEl.classList.add("hidden");
    welcomeEl?.classList.remove("hidden");
  });

  section.querySelector("#pi-create")?.addEventListener("click", async () => {
    const btn = section.querySelector("#pi-create");
    const errorEl = section.querySelector("#pi-error");
    const label = labelInput?.value.trim();
    const model = modelSelect?.value;
    if (!label || !model) {
      errorEl.textContent = "Name and model are required.";
      errorEl.classList.remove("hidden");
      return;
    }
    btn.disabled = true;
    btn.textContent = "Creating\u2026";
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/agents/pi`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label, model }),
      });
      const data = await r.json();
      if (data.ok) {
        fetchIntegrations();
        showAgentDetail({
          ...data.agent, binary: "pi", context_file: "PI.md", mcp: true, variant: true,
          available: true, auth: "LLM API key (configurable provider)",
          description: `Pi agent with ${model} backend`,
        });
      } else {
        errorEl.textContent = data.reason || "Creation failed.";
        errorEl.classList.remove("hidden");
        btn.disabled = false;
        btn.textContent = "Create";
      }
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.classList.remove("hidden");
      btn.disabled = false;
      btn.textContent = "Create";
    }
  });
}

/* ───── Compute detail panels ───── */
async function showComputeDetail(compute) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;
  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = "";

  const header = document.createElement("div");
  header.className = "exp-detail-header";
  const titleRow = document.createElement("div");
  titleRow.className = "exp-detail-title-row";
  const title = document.createElement("h2");
  title.className = "exp-detail-title";
  title.textContent = compute.label;
  const badge = document.createElement("span");
  badge.className = `exp-detail-badge ${compute.connected ? "running" : "paused"}`;
  badge.textContent = compute.connected ? "available" : "not configured";
  titleRow.appendChild(title);
  titleRow.appendChild(badge);
  header.appendChild(titleRow);
  if (compute.detail) {
    const desc = document.createElement("div");
    desc.className = "exp-detail-meta";
    desc.textContent = compute.detail;
    header.appendChild(desc);
  }
  detailEl.appendChild(header);

  const section = document.createElement("div");
  section.style.cssText = "padding: 16px 20px;";

  if (compute.id === "hfjobs") {
    // Fetch GPU pricing
    let flavors = [];
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/compute/hfjobs/flavors`);
      const data = await r.json();
      if (data.ok) flavors = data.flavors || [];
    } catch {}

    // Get auth username
    let username = "";
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/auth/status`);
      if (r.ok) {
        const s = await r.json();
        if (s.signed_in && s.user) username = s.user.display_name || s.user.email || "";
      }
    } catch {}

    const tableRows = flavors.map((f) => `
      <tr>
        <td style="padding:4px 12px 4px 0; color:var(--text); font-weight:500;">${escapeHtml(f.label)}</td>
        <td style="padding:4px 12px 4px 0; color:var(--text-dim);">$${f.cost_per_hour.toFixed(2)}/hr</td>
        <td style="padding:4px 0; color:var(--text-dim);">${f.vram_gb} GB</td>
      </tr>
    `).join("");

    section.innerHTML = `
      <div style="margin-bottom:16px; font-size:12px; color:var(--text-dim);">
        ${username ? `Connected as <strong style="color:var(--text);">@${escapeHtml(username)}</strong> via Hugging Face OAuth` : "Connected via Hugging Face token"}
      </div>
      ${flavors.length ? `
      <div style="margin-bottom:16px;">
        <div style="font-size:11px; font-weight:600; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:8px;">Available GPUs</div>
        <table style="font-size:12px; border-collapse:collapse;">${tableRows}</table>
      </div>` : ""}
      <button id="hfjobs-launch-btn" class="onboarding-btn" style="margin-top:4px;">Launch experiment with GPU</button>
    `;
    detailEl.appendChild(section);

    section.querySelector("#hfjobs-launch-btn")?.addEventListener("click", () => {
      // Navigate to experiment creation — switch to Experiments rail and open new-experiment form
      document.querySelector('[data-nav="experiments"]')?.click();
      setTimeout(() => document.getElementById("new-experiment-btn")?.click(), 100);
    });
  } else {
    section.innerHTML = `
      <div style="margin-bottom:12px;">
        <span style="font-size:12px; color:var(--text-dim);">Provider: </span>
        <span style="font-size:12px; color:var(--text);">${escapeHtml(compute.provider || compute.id)}</span>
      </div>
    `;
    detailEl.appendChild(section);
  }
}

function showComputeSetup(compute) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;
  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = "";

  const header = document.createElement("div");
  header.className = "exp-detail-header";
  const titleRow = document.createElement("div");
  titleRow.className = "exp-detail-title-row";
  const title = document.createElement("h2");
  title.className = "exp-detail-title";
  title.textContent = `Set up ${compute.label}`;
  titleRow.appendChild(title);
  header.appendChild(titleRow);
  detailEl.appendChild(header);

  const section = document.createElement("div");
  section.style.cssText = "padding: 16px 20px;";

  if (compute.id === "hfjobs") {
    // HF Jobs uses the same setup flow as the HuggingFace connector
    launchHuggingFaceSetup();
    return;
  } else if (compute.id === "runpod") {
    section.innerHTML = `
      <div class="wizard-field" style="margin-bottom:12px;">
        <label style="display:block; font-size:11px; font-weight:600; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px;">RunPod API Key</label>
        <input id="runpod-key" type="password" placeholder="rp_xxxxxxxx" spellcheck="false" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:6px 8px;" />
        <p style="font-size:11px; color:var(--text-dim); margin:4px 0 0;">Get your key at <a href="#" style="color:var(--accent);" onclick="window.nicolas?.openExternal?.('https://www.runpod.io/console/user/settings'); return false;">runpod.io/console</a></p>
      </div>
      <button id="runpod-save" class="wizard-btn-create" style="font-size:12px; padding:8px 16px;">Save</button>
      <span id="runpod-status" style="font-size:12px; color:var(--text-dim); margin-left:8px;"></span>
    `;
    detailEl.appendChild(section);

    section.querySelector("#runpod-save")?.addEventListener("click", async () => {
      const key = section.querySelector("#runpod-key")?.value.trim();
      const statusEl = section.querySelector("#runpod-status");
      if (!key) { statusEl.textContent = "API key required"; return; }
      try {
        await fetch(`http://127.0.0.1:${serverPort}/settings/env`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: "RUNPOD_API_KEY", value: key }),
        });
        statusEl.textContent = "Saved!";
        statusEl.style.color = "var(--green)";
        fetchIntegrations();
      } catch {
        statusEl.textContent = "Failed to save";
      }
    });
  } else {
    section.innerHTML = `<p style="font-size:12px; color:var(--text-dim);">Setup for ${escapeHtml(compute.label)} is not yet available.</p>`;
    detailEl.appendChild(section);
  }
}
