"""
Embedded chat UI for Hermes — served at /chat
Lightweight, no JavaScript framework, single HTML file.
"""
from __future__ import annotations

CHAT_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hermes Chat</title>
<style>
:root {
  --bg: #0a0e14;
  --sidebar-bg: #0d1117;
  --msg-user: #1a2744;
  --msg-ai: #151b23;
  --border: #1f2937;
  --text: #e6edf3;
  --text-dim: #8b95a1;
  --accent: #2dd4bf;
  --blue: #2563eb;
  --red: #ef4444;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg); color: var(--text);
  display: flex; height: 100vh; overflow: hidden;
}
/* sidebar */
.sidebar {
  width: 260px; background: var(--sidebar-bg); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; flex-shrink: 0;
}
.sidebar-header {
  padding: 16px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
}
.sidebar-header h1 { font-size: 16px; font-weight: 600; }
.sidebar-header .ver { font-size: 11px; color: var(--text-dim); }
.model-badge {
  margin: 12px; padding: 10px 12px; background: #0e1e1c; border: 1px solid var(--accent);
  border-radius: 6px; font-size: 12px;
}
.model-badge .label { color: var(--text-dim); font-size: 10px; text-transform: uppercase; }
.model-badge .name { color: var(--accent); font-family: ui-monospace,monospace; font-size: 12px; word-break: break-all; }
.model-badge .mode { color: var(--text-dim); font-size: 10px; margin-top: 2px; }
.actions { padding: 12px; flex: 1; }
.actions a, .actions button {
  display: block; padding: 8px 12px; margin-bottom: 4px; border-radius: 4px;
  font-size: 12px; color: var(--text); text-decoration: none; cursor: pointer;
  background: none; border: none; text-align: left; width: 100%;
}
.actions a:hover, .actions button:hover { background: #1f2937; }
.actions .warn { color: var(--red); }
/* main */
.main {
  flex: 1; display: flex; flex-direction: column; min-width: 0;
}
/* messages */
.messages {
  flex: 1; overflow-y: auto; padding: 16px;
}
.msg {
  max-width: 720px; margin: 0 auto 16px; padding: 12px 16px; border-radius: 8px;
  line-height: 1.6; font-size: 14px;
}
.msg.user { background: var(--msg-user); margin-left: auto; margin-right: 16px; }
.msg.assistant { background: var(--msg-ai); border: 1px solid var(--border); margin-right: auto; margin-left: 16px; }
.msg.system { color: var(--text-dim); font-size: 12px; text-align: center; margin: 8px 0; }
.msg .role { font-size: 11px; color: var(--text-dim); margin-bottom: 4px; text-transform: uppercase; }
.msg pre {
  background: #0a0e14; color: #7ee787; padding: 10px 12px; border-radius: 4px;
  overflow-x: auto; font-size: 12px; margin: 8px 0; line-height: 1.4;
}
.msg code { background: #1f2937; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
.thinking { font-style: italic; color: var(--text-dim); }
/* input */
.input-area {
  padding: 16px; border-top: 1px solid var(--border); background: var(--sidebar-bg);
}
.input-row {
  max-width: 720px; margin: 0 auto; display: flex; gap: 8px;
}
.input-row textarea {
  flex: 1; background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 14px; font-size: 14px; resize: none;
  min-height: 44px; max-height: 160px; font-family: inherit;
}
.input-row textarea:focus { outline: none; border-color: var(--accent); }
.input-row button {
  background: var(--blue); color: white; border: none; padding: 0 20px;
  border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500;
  white-space: nowrap;
}
.input-row button:hover { background: #1d4ed8; }
.input-row button:disabled { background: #374151; cursor: not-allowed; }
.status { color: var(--text-dim); font-size: 11px; padding: 4px 16px; text-align: center; }
@keyframes spin { to { transform: rotate(360deg); } }
.spinner {
  display: inline-block; width: 12px; height: 12px; border: 2px solid var(--text-dim);
  border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite;
  margin-right: 4px;
}
</style>
</head>
<body>
<div class="sidebar">
  <div class="sidebar-header">
    <h1>Hermes Chat</h1><span class="ver">v2</span>
  </div>
  <div class="model-badge">
    <div class="label">Current Model</div>
    <div class="name" id="model-name">loading...</div>
    <div class="mode" id="model-mode"></div>
  </div>
  <div class="actions">
    <a href="/launcher" target="_blank">Switch Model</a>
    <a href="http://localhost:7870" target="_blank">Open WebUI (Full)</a>
    <a href="/api/status" target="_blank">API Status</a>
    <button class="warn" onclick="clearChat()">Clear Chat</button>
  </div>
</div>
<div class="main">
  <div class="messages" id="messages">
    <div class="msg system">Hermes Agent ready. Type a message to start.</div>
  </div>
  <div class="status" id="status"></div>
  <div class="input-area">
    <div class="input-row">
      <textarea id="user-input" placeholder="Ask me anything..." rows="1"
        onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send();}"></textarea>
      <button id="send-btn" onclick="send()">Send</button>
    </div>
  </div>
</div>
<script>
const API = '/api/chat';
const HERMES_PORT = '7860';
var msgIdx = 0;

async function loadStatus() {
  try {
    let r = await fetch('/api/status');
    let s = await r.json();
    document.getElementById('model-name').textContent =
      (s.providers||[]).filter(p=>p.name==='local')[0]?.url || 'local';
    let mode = s.cloud ? 'cloud' : (s.local ? 'local' : (s.mock ? 'mock' : 'offline'));
    document.getElementById('model-mode').textContent = mode.toUpperCase();
    setStatus(mode === 'mock' ? '(running in mock mode - no LLM loaded)' : 'Ready');
  } catch(e) {
    setStatus('Cannot reach Hermes API', true);
  }
}
loadStatus();

function setStatus(text, isErr) {
  var el = document.getElementById('status');
  el.innerHTML = text;
  el.style.color = isErr ? 'var(--red)' : 'var(--text-dim)';
}

function addMsg(role, text) {
  var div = document.createElement('div');
  div.className = 'msg ' + role;
  if (role === 'user') {
    div.textContent = text;
  } else {
    // Simple markdown: code blocks and bold
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    text = text.replace(/\n/g, '<br>');
    div.innerHTML = text;
  }
  document.getElementById('messages').appendChild(div);
  document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
  return div;
}

async function send() {
  var input = document.getElementById('user-input');
  var btn = document.getElementById('send-btn');
  var text = input.value.trim();
  if (!text) return;

  input.value = ''; input.style.height = 'auto';
  input.disabled = true; btn.disabled = true;
  setStatus('<span class="spinner"></span> Thinking...');

  addMsg('user', text);
  var aiDiv = addMsg('assistant', '<span class="thinking">Thinking...</span>');

  try {
    let r = await fetch(API, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, remember: true})
    });
    let j = await r.json();
    if (j.ok) {
      aiDiv.innerHTML = '';
      // Use same simple markdown
      var html = j.reply
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
        .replace(/\n/g, '<br>');
      aiDiv.innerHTML = html;
      setStatus('Ready');
    } else {
      aiDiv.innerHTML = '<span style="color:var(--red)">Error: ' + (j.error||'unknown') + '</span>';
      setStatus('Error', true);
    }
  } catch(e) {
    aiDiv.innerHTML = '<span style="color:var(--red)">Error: ' + e.message + '</span>';
    setStatus('Connection error', true);
  }

  input.disabled = false; btn.disabled = false;
  input.focus();
}

function clearChat() {
  document.getElementById('messages').innerHTML = '';
  document.getElementById('messages').innerHTML = '<div class="msg system">Chat cleared.</div>';
}
</script>
</body>
</html>"""
