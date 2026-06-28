// Hermes Desktop launcher — runs in webui SPA context
// Loaded by webui_proxy via <script defer> injected into the SPA HTML head.
// Click handler POSTs to /api/hermes/launch/desktop which spawns
// bin\hermes-desktop.bat detached (the .bat calls ikaros-desktop-pet.exe).
//
// Design notes (mirror of recovery.js / voice.js siblings):
//   - Pure vanilla JS, no framework deps (loaded before SPA hydrates)
//   - Single button at bottom-left (avoids recovery toast at top-right)
//   - Debounced via `busy` flag (prevents double-clicks spawning 2 windows)
//   - 127.0.0.1-only backend -> CSRF isn't a concern
//
// 2026-06-25: added as part of `webui hermes-desktop-launcher` feature.
// This file is git-tracked so webui npm updates cannot clobber it.

(function () {
  "use strict";

  const API = "/api/hermes/launch/desktop";
  const BTN_ID = "hermes-desktop-launcher";
  const TOAST_ID = "hermes-desktop-launcher-toast";
  let busy = false;

  function injectCss() {
    if (document.getElementById("hermes-desktop-launcher-css")) return;
    const s = document.createElement("style");
    s.id = "hermes-desktop-launcher-css";
    s.textContent = [
      "#" + BTN_ID + "{",
      "  position:fixed;left:18px;bottom:18px;z-index:999998;",
      "  display:inline-flex;align-items:center;gap:8px;",
      "  padding:9px 16px;border-radius:999px;",
      "  background:linear-gradient(135deg,#6366f1,#8b5cf6);",
      "  color:#fff;border:0;cursor:pointer;font-weight:500;",
      "  font:13px/1 system-ui,-apple-system,sans-serif;",
      "  box-shadow:0 6px 20px rgba(99,102,241,.45),0 2px 6px rgba(0,0,0,.18);",
      "  transition:transform .15s ease,box-shadow .15s ease;",
      "  user-select:none;",
      "}",
      "#" + BTN_ID + ":hover{transform:translateY(-1px);box-shadow:0 8px 26px rgba(99,102,241,.55),0 3px 8px rgba(0,0,0,.22);}",
      "#" + BTN_ID + ":active{transform:translateY(0);}",
      "#" + BTN_ID + "[disabled]{opacity:.6;cursor:wait;}",
      "#" + TOAST_ID + "{",
      "  position:fixed;left:18px;bottom:68px;z-index:999998;",
      "  max-width:340px;background:#1f2937;color:#f3f4f6;",
      "  border:1px solid #4b5563;border-radius:8px;",
      "  padding:10px 14px;font:12px/1.5 system-ui,-apple-system,sans-serif;",
      "  box-shadow:0 6px 24px rgba(0,0,0,.45);",
      "  animation:hermes-launcher-fade-in .25s ease-out;",
      "}",
      "@keyframes hermes-launcher-fade-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}",
    ].join("\n");
    (document.head || document.documentElement).appendChild(s);
  }

  function mountButton() {
    if (document.getElementById(BTN_ID)) return;
    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
    btn.setAttribute("aria-label", "Launch Hermes Desktop");
    btn.innerHTML = "\uD83D\uDC3E Hermes Desktop";
    btn.addEventListener("click", launch);
    document.body.appendChild(btn);
  }

  function showToast(msg, kind) {
    const old = document.getElementById(TOAST_ID);
    if (old) old.remove();
    const t = document.createElement("div");
    t.id = TOAST_ID;
    const color = kind === "error" ? "#ef4444" : kind === "ok" ? "#10b981" : "#9ca3af";
    t.style.cssText = "border-left:3px solid " + color + ";";
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.remove(); }, kind === "error" ? 6000 : 3000);
  }

  function escapeHtml(s) {
    return String(s).replace(/[<>&"\x27]/g, function (c) {
      return ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "\"": "&quot;", "\x27": "&#39;" })[c];
    });
  }

  async function launch() {
    if (busy) return;
    const btn = document.getElementById(BTN_ID);
    if (btn) { btn.disabled = true; busy = true; }
    try {
      const r = await fetch(API, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      let payload = {};
      try { payload = await r.json(); } catch (e) {}
      if (r.ok && payload.ok) {
        showToast("\u2705 " + (payload.message || "started"), "ok");
      } else {
        const err = payload.error || ("HTTP " + r.status);
        showToast("\u274C " + escapeHtml(err), "error");
      }
    } catch (e) {
      showToast("\u274C " + escapeHtml(String(e)), "error");
    } finally {
      if (btn) btn.disabled = false;
      busy = false;
    }
  }

  function init() {
    injectCss();
    if (document.body) mountButton();
    else setTimeout(init, 80);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();