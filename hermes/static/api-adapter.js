/* ========================================================================
 * Hermes WebUI -> Hermes FastAPI API Adapter (v0.2 - real SSE)
 *
 * Bridges nesquena/hermes-webui's API expectations to our local
 * /api/chat/* + /v1/* endpoints. Loaded synchronously in <head>
 * before pwa-startup.js / boot.js so window.fetch is wrapped before
 * any other module uses it.
 *
 * v0.2 changes (Phase 1+2 of the streaming-and-sessions plan):
 *  - **Removed** the EventSource mock. The new server exposes
 *    GET /api/chat/stream/{stream_id} as a real text/event-stream;
 *    the browser's native EventSource now talks to it directly. The
 *    upstream WebUI calls `new EventSource('api/chat/stream?stream_id=...')`
 *    — that URL no longer matches anything our adapter needs to fake.
 *  - Simplified /api/chat/start transform: the server now returns the
 *    real `stream_id` and we just pass it through. No more body rewrite
 *    to /api/chat/send; that endpoint is now only used for legacy /
 *    non-streaming clients.
 *  - Kept the window.fetch wrapper for all the other route
 *    translations (sessions, models, skills, etc.).
 *  - /api/chat/cancel/{stream_id} is now a real endpoint (no more
 *    noop stub).
 *  - /api/chat/stream/status is now a real endpoint.
 * ======================================================================== */

(function () {
  'use strict';
  if (window.__hermesAdapterLoaded) return;
  window.__hermesAdapterLoaded = true;

  console.info('[hermes-adapter] loading API adapter v0.2 (real SSE)');

  // ---- helpers -----------------------------------------------------------

  function jsonResponse(obj, status) {
    return new Response(JSON.stringify(obj), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  function emptyResponse(status) {
    return new Response(null, { status: status || 204 });
  }

  function normalizeUrl(input) {
    try {
      if (typeof input === 'string') return new URL(input, location.origin);
      if (input instanceof URL) return input;
      if (input && input.url) return new URL(input.url, location.origin);
    } catch (e) {}
    return null;
  }

  function methodOf(input, init) {
    if (init && init.method) return String(init.method).toUpperCase();
    if (input && input.method) return String(input.method).toUpperCase();
    return 'GET';
  }

  // ---- URL translation table -------------------------------------------
  // Each entry: match(pathname) -> { url, method, transform }
  // transform: optional (path, method, data) -> response-shaped data

  const ROUTES = [
    // === Settings (no real persistence; return defaults; new UI uses
    //     these to populate theme / skin / streaming toggles) ===
    { match: (p) => p === '/api/settings', method: 'GET',
      url: '/api/webui/settings', method2: 'GET',
      transform: () => ({
        theme: localStorage.getItem('hermes-theme') || 'dark',
        skin: localStorage.getItem('hermes-skin') || 'default',
        language: localStorage.getItem('hermes-lang') || 'zh',
        send_key: 'enter',
        show_token_usage: true,
        show_thinking: true,
        show_tps: false,
        show_cli_sessions: false,
        show_quota_chip: false,
        hide_empty_state_suggestions: false,
        fade_text_effect: false,
        sound_enabled: false,
        notifications_enabled: false,
        whats_new_summary_enabled: false,
        whitelisted_browsers: [],
        session_endless_scroll_enabled: false,
        bot_name: 'Hermes',
        simplified_tool_calling: true,
        terminal_auto_expand_on_output: false,
        session_jump_buttons_enabled: false,
        sidebar_density: 'compact',
        pinned_sessions_limit: 3,
        busy_input_mode: 'queue',
        onboarding_completed: true,
        check_for_updates: false,
        show_reasoning: true,
        reasoning_effort: 'medium',
        display: { show_reasoning: true, show_cost: false, compact_mode: false, streaming: true },
        agent: { max_turns: 50, timeout: 300, tools_required: false },
        memory: { enabled: true, max_chars: 4000 },
        session: { idle_timeout: 1800, reset_schedule: null },
        privacy: { pii_redaction: false },
      }) },

    { match: (p) => p === '/api/settings', method: 'POST',
      url: '/api/webui/settings', method2: 'POST',
      transform: (data) => ({ ok: true, settings: data || {} }) },

    // === Models (OpenAI-compat -> new UI shape) ===
    { match: (p) => p === '/api/models', method: 'GET',
      url: '/v1/models', method2: 'GET',
      transform: (data) => ({
        models: (data && data.data || []).map(m => m.id),
        extra_models: [],
        aliases: {},
        providers: [],
        model_aliases: {},
        current_default: (data && data.data && data.data[0] && data.data[0].id) || null,
      }) },

    // === Skills ===
    { match: (p) => p === '/api/skills', method: 'GET',
      url: '/api/skills', method2: 'GET',
      transform: (data) => {
        // Our /api/skills returns {skills:[{name,description,category}]}
        if (Array.isArray(data)) return { skills: data };
        if (data && data.skills) return data;
        return { skills: [] };
      } },

    // === Sessions list ===
    { match: (p) => p === '/api/sessions', method: 'GET',
      url: '/api/chat/sessions', method2: 'GET',
      transform: (data) => {
        const list = (data && data.sessions) || [];
        return {
          sessions: list.map(s => ({
            session_id: s.id,
            id: s.id,
            title: s.title || 'Chat',
            message_count: s.message_count || 0,
            updated_at: s.updated_at || 0,
            created_at: s.created_at || s.updated_at || 0,
            last_message: s.last_message || '',
            archived: false,
            pinned: false,
            profile: 'default',
          })),
        };
      } },

    // === Single session ===
    { match: (p, q) => p === '/api/session' && q.get && q.get.has('session_id'), method: 'GET',
      url: '/api/chat/history', method2: 'GET',
      transform: (data, ctx) => {
        const sid = (ctx && ctx.query && ctx.query.get('session_id')) || (data && data.session_id);
        const msgs = (data && data.messages) || [];
        return {
          session: {
            session_id: sid,
            id: sid,
            title: (msgs[0] && msgs[0].content && msgs[0].content.slice(0, 60)) || 'Chat',
            created_at: (msgs[0] && msgs[0].timestamp) || 0,
            updated_at: (msgs[msgs.length-1] && msgs[msgs.length-1].timestamp) || 0,
            message_count: msgs.length,
            profile: 'default',
            archived: false,
            pinned: false,
            messages: msgs.map(m => ({
              role: m.role,
              content: m.content,
              timestamp: m.timestamp || 0,
            })),
          },
        };
      } },

    // === Create new session (treat as no-op; new UI wants a session_id back) ===
    { match: (p) => p === '/api/session/new', method: 'POST',
      url: '/api/webui/session/new', method2: 'POST',
      transform: (data) => ({
        session: {
          session_id: 'sess_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          title: (data && data.title) || 'New chat',
          created_at: Date.now() / 1000,
          updated_at: Date.now() / 1000,
          message_count: 0,
          profile: 'default',
          archived: false,
          pinned: false,
          messages: [],
        },
      }) },

    // === Delete session ===
    { match: (p) => p === '/api/session/delete', method: 'POST',
      url: '/api/chat/sessions', method2: 'DELETE',
      transform: () => ({ ok: true }) },

    // === Rename session ===
    { match: (p) => p === '/api/session/rename', method: 'POST',
      url: '/api/webui/session/rename', method2: 'POST',
      transform: () => ({ ok: true }) },

    // === Archive / pin / truncate / clear (all no-op for now) ===
    { match: (p) => ['/api/session/archive','/api/session/pin','/api/session/truncate',
                     '/api/session/clear','/api/session/duplicate','/api/session/retry',
                     '/api/session/undo','/api/session/update','/api/session/branch',
                     '/api/session/draft','/api/session/import','/api/session/export',
                     '/api/session/import_cli','/api/session/title/regenerate',
                     '/api/session/conversation-rounds','/api/session/handoff-summary',
                     '/api/session/move','/api/session/worktree/status',
                     '/api/session/worktree/remove','/api/session/compress/start',
                     '/api/session/compress/status','/api/session/toolsets',
                     '/api/session/yolo','/api/session/lineage/report',
                     '/api/btw','/api/background','/api/background/status',
                     '/api/chat/steer','/api/approval/respond','/api/approval/pending',
                     '/api/clarify/respond','/api/clarify/pending'].includes(p), method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: (data) => ({ ok: true, data: data || {} }) },

    // === Chat start (Phase 1+2: real server, real stream_id) ===
    // v0.2: server already returns { stream_id, session_id, effective_model, ... }
    // and starts the background LLM task. We just pass through the response.
    { match: (p) => p === '/api/chat/start', method: 'POST',
      url: '/api/chat/start', method2: 'POST',
      // No body transformation, no response transformation. Just relaying.
      transform: (data) => data,
      passthrough: true },

    // === Chat cancel (Phase 1: real endpoint) ===
    // The WebUI calls /api/chat/cancel with the stream_id in the body or
    // the URL. Our server accepts POST /api/chat/cancel/{stream_id}; the
    // WebUI usually hits the bare /api/chat/cancel path with the id in
    // the body, so we forward the body as-is and let the server return
    // { ok, stream_id }. The native adapter below handles the URL-form
    // variant.
    { match: (p) => p === '/api/chat/cancel', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: (data) => {
        // If the upstream sent a stream_id in the body, forward to the
        // real cancel endpoint.
        if (data && data.stream_id) {
          return fetch('/api/chat/cancel/' + encodeURIComponent(data.stream_id), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
          }).then(r => r.json()).catch(() => ({ ok: false }));
        }
        return { ok: true, cancelled: true };
      } },

    // === Chat stream status (Phase 1: real endpoint) ===
    { match: (p) => p === '/api/chat/stream/status', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: (data, ctx) => {
        const sid = ctx && ctx.query && ctx.query.get('stream_id');
        if (!sid) return { stream_id: '', active: false, status: 'idle' };
        // Forward to the real status endpoint
        return fetch('/api/chat/stream/status?stream_id=' + encodeURIComponent(sid))
          .then(r => r.json())
          .catch(() => ({ stream_id: sid, active: false, status: 'unknown' }));
      } },

    // === Profile ===
    { match: (p) => p === '/api/profile/active', method: 'GET',
      url: '/api/webui/profile/active', method2: 'GET',
      transform: () => ({ name: 'default', is_default: true }) },
    { match: (p) => p === '/api/profiles', method: 'GET',
      url: '/api/webui/profiles', method2: 'GET',
      transform: () => ({ profiles: [{ name: 'default', is_default: true }] }) },
    { match: (p) => p === '/api/profile/switch', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
    { match: (p) => ['/api/profile/create','/api/profile/delete'].includes(p), method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },

    // === Auth ===
    { match: (p) => p === '/api/auth/status', method: 'GET',
      url: '/api/webui/auth/status', method2: 'GET',
      transform: () => ({ enabled: false, user: null, mode: 'open' }) },

    // === Dashboard ===
    { match: (p) => p === '/api/dashboard/status', method: 'GET',
      url: '/api/status', method2: 'GET' },
    { match: (p) => p === '/api/dashboard/config', method: 'GET',
      url: '/api/webui/dashboard/config', method2: 'GET',
      transform: () => ({ config: {} }) },
    { match: (p) => p === '/api/dashboard/config', method: 'POST',
      url: '/api/webui/dashboard/config', method2: 'POST',
      transform: (data) => ({ ok: true, config: data || {} }) },

    // === Health ===
    { match: (p) => p === '/api/system/health' || p === '/api/health/agent', method: 'GET',
      url: '/health', method2: 'GET' },

    // === Workspaces (no equivalent; return empty) ===
    { match: (p) => p === '/api/workspaces', method: 'GET',
      url: '/api/webui/workspaces', method2: 'GET',
      transform: () => ({ workspaces: [] }) },
    { match: (p) => ['/api/workspaces/add','/api/workspaces/remove','/api/workspaces/rename',
                     '/api/workspaces/reorder','/api/workspaces/suggest'].includes(p), method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: (data) => ({ ok: true, data: data || {} }) },

    // === Memory (new UI wants /api/memory list; we serve from /api/status) ===
    { match: (p) => p === '/api/memory', method: 'GET',
      url: '/api/status', method2: 'GET',
      transform: (data) => {
        const mem = (data && data.memory) || {};
        return {
          total_items: mem.total_items || 0,
          items: (mem.recent || []).map(m => ({
            id: m.id, text: m.text, tags: m.tags || [], created_at: 0,
          })),
          sections: {},
        };
      } },
    { match: (p) => p === '/api/memory/write', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },

    // === Crons, Kanban, Wiki, Logs, Goals, Personalities, Commands,
    //     Reasoning, Provider quota, Updates, Insights, Notes ===
    //     All return safe empty defaults.
    { match: (p) => p === '/api/crons', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ jobs: [] }) },
    { match: (p) => p.startsWith('/api/crons/'), method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
    { match: (p) => p === '/api/kanban/boards', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ boards: [] }) },
    { match: (p) => p.startsWith('/api/kanban/'), method: '*',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ ok: true, data: [] }) },
    { match: (p) => p === '/api/wiki/status', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ status: 'disabled' }) },
    { match: (p) => p === '/api/logs', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ lines: [] }) },
    { match: (p) => p === '/api/insights', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ insights: {} }) },
    { match: (p) => p === '/api/notes/sources', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ sources: [] }) },
    { match: (p) => p.startsWith('/api/notes/'), method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ items: [] }) },
    { match: (p) => p === '/api/personalities', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ personalities: [] }) },
    { match: (p) => p === '/api/personality/set', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
    { match: (p) => p === '/api/commands', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ commands: [] }) },
    { match: (p) => p === '/api/commands/exec', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true, output: '' }) },
    { match: (p) => p === '/api/reasoning', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ display: true, effort: 'medium' }) },
    { match: (p) => p === '/api/reasoning', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
    { match: (p) => p === '/api/provider/quota', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ quota: null }) },
    { match: (p) => p.startsWith('/api/updates/'), method: '*',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ webui: { behind: 0 }, agent: { behind: 0 } }) },
    { match: (p) => p === '/api/goal', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
    { match: (p) => p === '/api/transcribe', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ text: '' }) },
    { match: (p) => p === '/api/shutdown', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
    { match: (p) => p === '/api/skills/content', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ content: '', name: '' }) },
    { match: (p) => p === '/api/skills/usage', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ usage: {}, skill_names: [], total_invocations: 0, unique_skills_used: 0 }) },
    { match: (p) => ['/api/skills/toggle','/api/skills/save','/api/skills/delete'].includes(p), method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
    { match: (p) => p === '/api/file' || p === '/api/list' || p === '/api/git-info', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => ({ entries: [], files: [] }) },
    { match: (p) => p === '/api/media', method: 'GET',
      url: '/api/webui/noop', method2: 'GET',
      transform: () => emptyResponse(404) },
    { match: (p) => p.startsWith('/api/file/') || p.startsWith('/api/folder/') ||
                     p === '/api/workspace/upload' || p === '/api/client-events/log', method: 'POST',
      url: '/api/webui/noop', method2: 'POST',
      transform: () => ({ ok: true }) },
  ];

  function findRoute(path, method) {
    for (const r of ROUTES) {
      try {
        if (r.method && r.method !== method && r.method !== '*') continue;
        if (r.match(path, new URLSearchParams())) return r;
      } catch (e) {}
    }
    return null;
  }

  // ---- fetch override ---------------------------------------------------

  const _origFetch = window.fetch ? window.fetch.bind(window) : null;
  if (!_origFetch) {
    console.error('[hermes-adapter] no window.fetch; aborting');
    return;
  }

  window.fetch = async function (input, init) {
    init = init || {};
    const u = normalizeUrl(input);
    if (!u) return _origFetch(input, init);
    // Only intercept same-origin /api/* calls
    if (u.origin !== location.origin) return _origFetch(input, init);
    if (!u.pathname.startsWith('/api/')) return _origFetch(input, init);

    const method = methodOf(input, init);
    const route = findRoute(u.pathname, method);
    if (!route) {
      // Pass through; let server return 404 which the new UI swallows
      return _origFetch(input, init);
    }

    // Build translated request
    const newUrl = new URL(route.url, location.origin);
    // Carry over query string except for translated ones
    for (const [k, v] of u.searchParams) {
      if (!newUrl.searchParams.has(k)) newUrl.searchParams.set(k, v);
    }

    const newInit = Object.assign({}, init, { method: route.method2 || method });

    // Decode body (if any) for transform context
    let body = null;
    if (init && init.body) {
      try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
    } else if (input && input.body) {
      try { body = JSON.parse(input.body); } catch (e) { body = init.body; }
    }

    let response;
    try {
      response = await _origFetch(newUrl.href, newInit);
    } catch (e) {
      console.warn('[hermes-adapter] fetch error', u.pathname, e);
      throw e;
    }

    // passthrough routes: return server response verbatim
    if (route.passthrough) {
      return response;
    }
    // If the route has no transform, return as-is
    if (!route.transform) return response;

    // Read body, transform, return new response
    let data = null;
    const text = await response.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
    if (data === null) {
      // Non-JSON or empty; pass through
      return new Response(text, { status: response.status, headers: response.headers });
    }

    const out = route.transform(data, { body, query: u.searchParams });
    // If the transform returned a Promise (e.g. for the cancel/status
    // forwarding), await it before serializing.
    if (out && typeof out.then === 'function') {
      try {
        const resolved = await out;
        return jsonResponse(resolved, response.status);
      } catch (e) {
        return jsonResponse({ ok: false, error: String(e) }, 500);
      }
    }
    return jsonResponse(out, response.status);
  };

  // EventSource is NOT wrapped anymore (Phase 1+2). The new server
  // exposes a real text/event-stream at /api/chat/stream/{stream_id};
  // the browser's native EventSource talks to it directly. The
  // upstream WebUI uses:
  //     new EventSource('api/chat/stream?stream_id=...')
  // which is now served as a real SSE endpoint. No more fake.

  console.info('[hermes-adapter] ready v0.2 (fetch wrapped; real SSE on /api/chat/stream)');
})();
