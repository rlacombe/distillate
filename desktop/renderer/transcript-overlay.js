// Transcript overlay — shows tmux pane buffer in a scrollable, selectable
// modal so the user can select long content natively. Opened via Cmd+Shift+T.

/** Strip ANSI escape sequences from text. */
function _stripAnsi(s) {
  return s.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, "")
          .replace(/\x1b\][^\x07]*\x07/g, "")  // OSC sequences
          .replace(/\x1b[PX^_][\s\S]*?\x1b\\/g, ""); // DCS/SOS/PM/APC
}

function _createOverlay() {
  const existing = document.getElementById("transcript-overlay");
  if (existing) return existing;

  const overlay = document.createElement("div");
  overlay.id = "transcript-overlay";
  overlay.className = "transcript-overlay hidden";
  overlay.innerHTML = `
    <div class="transcript-box" role="dialog" aria-label="Terminal transcript">
      <div class="transcript-header">
        <span class="transcript-title">Terminal transcript</span>
        <span class="transcript-hint">Select text · Cmd+C to copy · Esc to close</span>
        <button class="transcript-close" aria-label="Close">×</button>
      </div>
      <pre class="transcript-body" tabindex="0"></pre>
    </div>
  `;
  document.body.appendChild(overlay);

  // Close on backdrop click (but not when clicking inside the box)
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) closeTranscriptOverlay();
  });
  overlay.querySelector(".transcript-close").addEventListener("click", closeTranscriptOverlay);

  return overlay;
}

/** Open the transcript overlay with the current tmux pane content. */
async function openTranscriptOverlay() {
  if (!window.xtermBridge?.getTranscript) return;

  const overlay = _createOverlay();
  const body = overlay.querySelector(".transcript-body");
  body.textContent = "Loading…";
  overlay.classList.remove("hidden");

  try {
    const result = await window.xtermBridge.getTranscript();
    if (!result?.ok) {
      body.textContent = result?.error || "Failed to capture transcript";
      return;
    }
    body.textContent = _stripAnsi(result.content).replace(/\n+$/, "");
    // Scroll to bottom so latest content is visible
    body.scrollTop = body.scrollHeight;
    // Focus so Cmd+A works without extra click
    body.focus();
  } catch (err) {
    body.textContent = `Error: ${err.message || err}`;
  }
}

function closeTranscriptOverlay() {
  const overlay = document.getElementById("transcript-overlay");
  if (overlay) overlay.classList.add("hidden");
}

function isTranscriptOverlayOpen() {
  const overlay = document.getElementById("transcript-overlay");
  return overlay && !overlay.classList.contains("hidden");
}

window.openTranscriptOverlay = openTranscriptOverlay;
window.closeTranscriptOverlay = closeTranscriptOverlay;
window.isTranscriptOverlayOpen = isTranscriptOverlayOpen;
