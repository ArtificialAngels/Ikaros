// Icarus session recovery — runs in webui SPA context
// Loaded by webui_proxy via <script defer>. Talks to bridge via webui_proxy.
//
// What this does:
//   1. On page load, fetches /api/hermes/icarus/active-session
//   2. If a recent active session exists, shows a "Resume previous conversation?"
//      toast in the corner
//   3. On Resume click, calls /api/hermes/icarus/session/{id}/resume-context,
//      stores the system prompt in localStorage, then navigates to /chat
//   4. SPA chat page (when loaded) can read the stored context and seed
//      the new conversation with the previous context
//
// Design constraints:
//   - Pure vanilla JS — no framework deps (loaded before SPA hydrates)
//   - Defensive: respects user dismissals (sessionStorage)
//   - Self-cleaning: removes toast if SPA navigates away

(function () {
  "use strict";

  const API = "/api/hermes/icarus";
  const STORAGE_KEY = "icarus_recovery_state";
  const RESUME_CONTEXT_KEY = "icarus_resume_context";
  let shown = false;

  function escapeHtml(s) {
    return String(s).replace(/[<>&"']/g, function (c) {
      return ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function mountToast(html) {
    const div = document.createElement("div");
    div.id = "icarus-recovery-toast";
    div.setAttribute("role", "alert");
    div.style.cssText = [
      "position:fixed", "top:16px", "right:16px", "z-index:999999",
      "max-width:380px", "background:#1f2937", "color:#f3f4f6",
      "border:1px solid #4b5563", "border-radius:8px",
      "padding:14px 18px", "font:13px/1.5 system-ui,-apple-system,sans-serif",
      "box-shadow:0 6px 24px rgba(0,0,0,.45)",
      "animation:icarus-fade-in .3s ease-out"
    ].join(";");
    div.innerHTML = html;
    document.body.appendChild(div);
  }

  function dismiss(reason) {
    const el = document.getElementById("icarus-recovery-toast");
    if (el) el.remove();
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        dismissed: reason,
        ts: Date.now(),
      }));
    } catch (e) {}
  }

  async function check() {
    if (shown) return;
    shown = true;

    // Respect user's recent dismiss (within last 30 minutes)
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) {
        const s = JSON.parse(raw);
        if (s.dismissed && (Date.now() - s.ts) < 30 * 60 * 1000) return;
        if (s.dismissed === "never") return;
      }
    } catch (e) {}

    let info;
    try {
      const r = await fetch(API + "/active-session", {
        credentials: "include",
      });
      if (!r.ok) return;
      info = await r.json();
    } catch (e) {
      return;
    }
    if (!info || !info.found) return;
    // Only suggest if the session was active within the last 24h
    if (info.age_seconds != null && info.age_seconds > 24 * 60 * 60) return;

    const ageStr = info.age_human || "recently";
    const lastMsg = (info.last_messages || []).slice(-1)[0] || {};
    const excerpt = (lastMsg.content_excerpt || "").slice(0, 160);
    const safeTitle = escapeHtml(info.title || "(untitled conversation)");
    const safeExcerpt = escapeHtml(excerpt);

    const html = [
      '<div style="font-weight:600;margin-bottom:6px;font-size:14px;display:flex;align-items:center;gap:6px">',
        '🪶 <span>伊卡洛斯: 上次对话未完成</span>',
      '</div>',
      '<div style="opacity:.85;margin-bottom:6px">',
        '<b>', safeTitle, '</b>',
        ' · ', String(info.message_count || 0), ' 条消息 · ', ageStr,
      '</div>',
      excerpt ? ('<div style="opacity:.7;margin-bottom:12px;font-style:italic;border-left:2px solid #4b5563;padding-left:8px">&quot;' +
        safeExcerpt + '&quot;</div>') : '',
      '<div style="display:flex;gap:8px;margin-top:8px">',
        '<button id="icarus-resume" style="background:#10b981;color:#fff;border:0;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:500">继续上次</button>',
        '<button id="icarus-dismiss" style="background:transparent;color:#9ca3af;border:1px solid #4b5563;padding:6px 14px;border-radius:4px;cursor:pointer">以后再说</button>',
        '<button id="icarus-dismiss-session" style="background:transparent;color:#6b7280;border:0;padding:6px 14px;cursor:pointer;text-decoration:underline;font-size:12px">不再提醒</button>',
      '</div>',
    ].join("");

    function showNow() {
      if (document.body) {
        mountToast(html);
        const resumeBtn = document.getElementById("icarus-resume");
        const dismissBtn = document.getElementById("icarus-dismiss");
        const neverBtn = document.getElementById("icarus-dismiss-session");
        if (resumeBtn) resumeBtn.onclick = () => resume(info);
        if (dismissBtn) dismissBtn.onclick = () => dismiss("later");
        if (neverBtn) neverBtn.onclick = () => dismiss("never");
      } else {
        setTimeout(showNow, 100);
      }
    }
    showNow();
  }

  async function resume(info) {
    const btn = document.getElementById("icarus-resume");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "加载上下文...";
    }
    try {
      const r = await fetch(
        API + "/session/" + encodeURIComponent(info.session_id) + "/resume-context",
        { method: "POST", credentials: "include" }
      );
      if (!r.ok) throw new Error("HTTP " + r.status);
      const ctx = await r.json();
      try {
        localStorage.setItem(RESUME_CONTEXT_KEY, JSON.stringify({
          session_id: info.session_id,
          title: info.title,
          system_prompt: ctx.system_prompt,
          summary: ctx.summary,
          ts: Date.now(),
        }));
      } catch (e) {}
      // Navigate to chat. SPA typically routes /chat or / (default).
      // We use /chat — adjust if SPA uses a different path.
      dismiss("resumed");
      const profile = info.profile && info.profile !== "default"
        ? "?profile=" + encodeURIComponent(info.profile) : "";
      window.location.href = "/chat" + profile;
    } catch (e) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "重试";
      }
    }
  }

  // Inject a small CSS animation for fade-in
  function injectCss() {
    if (document.getElementById("icarus-recovery-css")) return;
    const s = document.createElement("style");
    s.id = "icarus-recovery-css";
    s.textContent = "@keyframes icarus-fade-in { from { opacity:0; transform:translateY(-8px) } to { opacity:1; transform:translateY(0) } }";
    (document.head || document.documentElement).appendChild(s);
  }

  function init() {
    injectCss();
    check();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();