// CodeMirror 6 bootstrap for the canvas editor.
//
// Loads CM6 from esm.sh (allowed by our CSP). Exposes a single global
// factory ``window.createCanvasEditor(container, { type, doc, onChange,
// onSave, onInlineEdit })`` that canvas.js calls once it needs to mount
// an editor. The factory dispatches on ``type`` to pick the right CM6
// language mode (LaTeX, Markdown, plain text).

import { EditorView, keymap, lineNumbers, highlightActiveLine } from "https://esm.sh/@codemirror/view@6";
import { EditorState, Compartment } from "https://esm.sh/@codemirror/state@6";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "https://esm.sh/@codemirror/commands@6";
import { StreamLanguage, bracketMatching, indentOnInput, foldGutter, foldKeymap, defaultHighlightStyle, syntaxHighlighting } from "https://esm.sh/@codemirror/language@6";
import { highlightSelectionMatches, searchKeymap } from "https://esm.sh/@codemirror/search@6";
import { closeBrackets, closeBracketsKeymap, autocompletion, completionKeymap } from "https://esm.sh/@codemirror/autocomplete@6";
import { lintKeymap } from "https://esm.sh/@codemirror/lint@6";
import { oneDark } from "https://esm.sh/@codemirror/theme-one-dark@6";
import { stex } from "https://esm.sh/@codemirror/legacy-modes@6/mode/stex";
import { markdown } from "https://esm.sh/@codemirror/lang-markdown@6";

/** Dispatch on canvas type to pick the right CM6 language extension. */
function _languageFor(type) {
  if (type === "latex") return StreamLanguage.define(stex);
  if (type === "markdown") return markdown();
  return null; // plain text
}

window.createCanvasEditor = function createCanvasEditor(container, opts = {}) {
  const { type = "plain", doc = "", onChange, onSave, onInlineEdit } = opts;

  const themeCompartment = new Compartment();
  const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;

  const extensions = [
    lineNumbers(),
    foldGutter(),
    history(),
    indentOnInput(),
    bracketMatching(),
    closeBrackets(),
    autocompletion(),
    highlightActiveLine(),
    highlightSelectionMatches(),
    EditorView.lineWrapping,
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    keymap.of([
      ...closeBracketsKeymap,
      ...defaultKeymap,
      ...searchKeymap,
      ...historyKeymap,
      ...foldKeymap,
      ...completionKeymap,
      ...lintKeymap,
      indentWithTab,
      {
        key: "Mod-s",
        preventDefault: true,
        // ⌘S = save + compile / save + preview refresh (per canvas type).
        run: () => { onSave?.(); return true; },
      },
      {
        key: "Mod-Enter",
        preventDefault: true,
        run: () => { onSave?.(); return true; },
      },
      {
        key: "Mod-k",
        preventDefault: true,
        run: () => {
          if (typeof window.openInlineEditModal === "function") {
            window.openInlineEditModal();
          }
          return true;
        },
      },
    ]),
    themeCompartment.of(isDark ? oneDark : []),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        onChange?.(update.state.doc.toString());
      }
    }),
    EditorView.theme({
      "&": { height: "100%", fontSize: "13px" },
      ".cm-scroller": { fontFamily: "'MesloLGS Nerd Font Mono', 'Andale Mono', Menlo, monospace" },
    }),
  ];

  // Append the language extension AFTER the base set so its keymap (e.g.
  // markdown's smart-list handling) overrides only where needed.
  const language = _languageFor(type);
  if (language) extensions.push(language);

  const view = new EditorView({
    state: EditorState.create({ doc, extensions }),
    parent: container,
  });

  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", (e) => {
    view.dispatch({
      effects: themeCompartment.reconfigure(e.matches ? oneDark : []),
    });
  });

  return {
    view,
    getDoc: () => view.state.doc.toString(),
    setDoc: (text) => {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: text },
      });
    },
    focus: () => view.focus(),
    destroy: () => view.destroy(),
    gotoLine: (line) => {
      if (!line || line < 1) return;
      const lineInfo = view.state.doc.line(Math.min(line, view.state.doc.lines));
      view.dispatch({
        selection: { anchor: lineInfo.from },
        scrollIntoView: true,
      });
      view.focus();
    },
  };
};

// Signal readiness for canvas.js to check.
window.__canvasEditorReady = true;
window.dispatchEvent(new CustomEvent("canvas-editor-ready"));
