/* account.js — Activity-bar account button + settings sidebar wiring
 *
 * The account button opens the settings sidebar view (no overlay panel).
 * Signed-in state: renders avatar/initials in the activity bar button.
 * Signed-out state: renders person icon.
 * refreshAccountState() is called after auth changes.
 */

let _accountUser = null;

// ---- Helpers ---------------------------------------------------------------

function _getInitials(user) {
  const name = user?.display_name || user?.hf_username || user?.email || "";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.trim().slice(0, 2).toUpperCase() || "?";
}

function _avatarColor(user) {
  const str = user?.display_name || user?.email || "u";
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) & 0xffff;
  return `hsl(${h % 360}, 55%, 48%)`;
}

async function _fetchAccountUser() {
  if (!serverPort) return null;
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/auth/status`);
    const d = await r.json();
    return d.signed_in ? d.user : null;
  } catch { return null; }
}

// ---- Account button rendering ----------------------------------------------

function _renderAccountBtn(user) {
  const btn = document.getElementById("account-btn");
  if (!btn) return;
  if (user) {
    const initials = _getInitials(user);
    const color = _avatarColor(user);
    const label = user.display_name || user.hf_username || user.email || "Account";
    btn.title = label;
    btn.classList.add("has-user");
    if (user.avatar_url) {
      btn.innerHTML = `<img class="account-avatar account-avatar-img" src="${escapeHtml(user.avatar_url)}" alt="${escapeHtml(label)}">`;
      const img = btn.querySelector("img");
      img.onerror = () => {
        img.replaceWith(Object.assign(document.createElement("span"), {
          className: "account-avatar",
          style: `background:${color}`,
          textContent: initials,
        }));
      };
    } else {
      btn.innerHTML = `<span class="account-avatar" style="background:${color}" aria-label="${escapeHtml(label)}">${escapeHtml(initials)}</span>`;
    }
  } else {
    btn.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="8" r="3.5"/>
        <path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/>
      </svg>`;
    btn.title = "Account & Settings";
    btn.classList.remove("has-user");
  }
}

// ---- Shared sign-out -------------------------------------------------------

async function _doSignOut() {
  try {
    await fetch(`http://127.0.0.1:${serverPort}/auth/logout`, { method: "POST" });
  } catch {}
  _accountUser = null;
  _renderAccountBtn(null);
  if (typeof _refreshHfAuthBar === "function") _refreshHfAuthBar();
  if (typeof _refreshSettingsAccountSection === "function") _refreshSettingsAccountSection();
}

// ---- Public API ------------------------------------------------------------

function _wireAccountBtn() {
  const btn = document.getElementById("account-btn");
  if (!btn || btn.dataset.accountWired) return;
  btn.dataset.accountWired = "1";
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const overlay = document.getElementById("settings-overlay");
    if (overlay && !overlay.hidden) {
      if (typeof closeSettings === "function") closeSettings();
    } else {
      if (typeof openSettings === "function") openSettings("account");
    }
  });
}

async function mountAccount() {
  _wireAccountBtn();
  _accountUser = await _fetchAccountUser();
  _renderAccountBtn(_accountUser);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _wireAccountBtn);
} else {
  _wireAccountBtn();
}

async function refreshAccountState() {
  _accountUser = await _fetchAccountUser();
  _renderAccountBtn(_accountUser);
  if (typeof _refreshSettingsAccountSection === "function") _refreshSettingsAccountSection();
}
