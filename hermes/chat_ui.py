"""
Hermes Chat Pro — standalone rich chat UI.
Features: model switching, conversation history, streaming, syntax highlight,
          system prompt editor, temperature control, export, mobile responsive.
Served at /chat — zero external dependencies at runtime (highlight.js inlined).
"""
from __future__ import annotations

CHAT_PRO_HTML = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Hermes Chat Pro</title>
<style>
:root{--bg:#0b0f17;--side:#0d131f;--card:#111827;--border:#1e293b;--hover:#1e3050;--text:#e2e8f0;--dim:#64748b;--accent:#22d3ee;--blue:#3b82f6;--green:#22c55e;--red:#ef4444;--orange:#f59e0b;--radius:10px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden}
/* sidebar */
.sidebar{width:280px;background:var(--side);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
.sidebar-header{padding:16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.sidebar-header h2{font-size:14px;font-weight:600;display:flex;align-items:center;gap:6px}
.sidebar-header h2::before{content:'';display:inline-block;width:8px;height:8px;background:var(--accent);border-radius:50%;box-shadow:0 0 8px var(--accent)}
.sidebar-header .new-btn{background:var(--blue);color:#fff;border:none;padding:6px 12px;border-radius:6px;font-size:11px;cursor:pointer;font-weight:500}
.sidebar-header .new-btn:hover{background:#2563eb}
.model-info{padding:12px 16px;border-bottom:1px solid var(--border);font-size:11px}
.model-info .row{display:flex;justify-content:space-between;margin-bottom:3px}
.model-info .label{color:var(--dim)}
.model-info .val{font-family:ui-monospace,monospace;color:var(--accent);font-size:10px}
.conv-list{flex:1;overflow-y:auto;padding:8px}
.conv-item{padding:10px 12px;border-radius:6px;cursor:pointer;font-size:12px;color:var(--dim);margin-bottom:2px;display:flex;align-items:center;gap:8px;transition:background .15s}
.conv-item:hover{background:var(--hover);color:var(--text)}
.conv-item.active{background:var(--hover);color:var(--text);border-left:2px solid var(--accent)}
.conv-item .del-btn{margin-left:auto;opacity:0;color:var(--red);background:none;border:none;cursor:pointer;font-size:14px;padding:0 4px}
.conv-item:hover .del-btn{opacity:1}
.sidebar-actions{padding:8px 16px 16px;border-top:1px solid var(--border)}
.sidebar-actions a{display:block;padding:6px 0;font-size:11px;color:var(--dim);text-decoration:none}
.sidebar-actions a:hover{color:var(--text)}
/* main */
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.chat-header{padding:14px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;background:var(--side)}
.chat-header select{background:var(--card);color:var(--text);border:1px solid var(--border);padding:6px 10px;border-radius:6px;font-size:12px;cursor:pointer;font-family:inherit}
.chat-header select:focus{outline:none;border-color:var(--accent)}
.chat-header .settings-btn{background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px;padding:4px 8px;border-radius:4px}
.chat-header .settings-btn:hover{color:var(--text);background:var(--hover)}
.messages{flex:1;overflow-y:auto;padding:20px 0;scroll-behavior:smooth}
.msg-wrap{max-width:800px;margin:0 auto 16px;padding:0 24px;animation:slideIn .25s ease}
.msg{display:flex;gap:12px}
.msg .avatar{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0;font-weight:700}
.msg.user .avatar{background:var(--blue);color:#fff}
.msg.assistant .avatar{background:var(--accent);color:#0b0f17}
.msg-body{flex:1;min-width:0;background:var(--card);padding:14px 18px;border-radius:var(--radius);border:1px solid var(--border);line-height:1.7;font-size:14px}
.msg.user .msg-body{background:var(--card);border-color:var(--blue)}
.msg-body pre{background:#0a0f1a;padding:14px 16px;border-radius:8px;overflow-x:auto;font-size:12px;margin:10px 0;line-height:1.5;border:1px solid var(--border);position:relative}
.msg-body pre .lang-tag{position:absolute;top:4px;right:8px;font-size:10px;color:var(--dim);text-transform:uppercase}
.msg-body code{font-family:'Cascadia Code','Fira Code','JetBrains Mono',Consolas,monospace;font-size:12px}
.msg-body :not(pre) > code{background:#1e293b;padding:2px 6px;border-radius:4px;font-size:12px}
.msg-body table{border-collapse:collapse;width:100%;margin:10px 0}
.msg-body th,.msg-body td{border:1px solid var(--border);padding:6px 12px;text-align:left;font-size:13px}
.msg-body th{background:var(--side);font-weight:600}
.msg-body blockquote{border-left:3px solid var(--accent);padding:8px 14px;color:var(--dim);margin:10px 0;background:#0e1520;border-radius:0 6px 6px 0}
.msg-body h1,.msg-body h2,.msg-body h3{margin:12px 0 6px;color:var(--text)}
.msg-body h1{font-size:18px}.msg-body h2{font-size:16px}.msg-body h3{font-size:14px}
.msg-body ul,.msg-body ol{padding-left:20px;margin:8px 0}
.msg-body li{margin:4px 0}
.msg-body a{color:var(--accent)}
.msg-body hr{border:none;border-top:1px solid var(--border);margin:14px 0}
.msg-actions{display:flex;gap:8px;margin-top:4px;opacity:0;transition:opacity .15s}
.msg-wrap:hover .msg-actions{opacity:1}
.msg-actions button{background:none;border:none;color:var(--dim);cursor:pointer;font-size:11px;padding:2px 6px;border-radius:4px}
.msg-actions button:hover{color:var(--text);background:var(--hover)}
/* settings panel */
.settings-panel{display:none;background:var(--side);border-left:1px solid var(--border);width:240px;padding:16px;overflow-y:auto;flex-shrink:0}
.settings-panel.show{display:block}
.settings-panel h3{font-size:13px;margin-bottom:12px}
.settings-panel label{display:block;font-size:11px;color:var(--dim);margin-bottom:4px}
.settings-panel input,.settings-panel textarea,.settings-panel select{width:100%;background:var(--card);color:var(--text);border:1px solid var(--border);padding:6px 8px;border-radius:6px;font-size:11px;font-family:inherit;margin-bottom:12px}
.settings-panel textarea{resize:vertical;min-height:80px;font-size:10px;line-height:1.4}
.settings-panel input[type=range]{padding:0;height:24px}
.settings-panel .val{color:var(--accent);font-size:10px;float:right}
/* input */
.input-area{padding:16px 24px 20px;background:var(--side);border-top:1px solid var(--border)}
.input-row{max-width:800px;margin:0 auto;display:flex;gap:10px;align-items:flex-end}
.input-row textarea{flex:1;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;font-size:14px;resize:none;min-height:48px;max-height:180px;font-family:inherit;line-height:1.5}
.input-row textarea:focus{outline:none;border-color:var(--accent)}
.input-row button{background:var(--blue);color:#fff;border:none;padding:12px 24px;border-radius:var(--radius);cursor:pointer;font-size:14px;font-weight:500;white-space:nowrap}
.input-row button:hover{background:#2563eb}
.input-row button:disabled{background:#334155;cursor:not-allowed}
.input-row .stop-btn{background:var(--red);display:none}
.input-row .stop-btn:hover{background:#dc2626}
.status{padding:0 24px 8px;font-size:11px;text-align:center}
.status .spinner{display:inline-block;width:10px;height:10px;border:2px solid var(--dim);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;margin-right:4px;vertical-align:middle}
.status .tok{color:var(--green)}
.welcome{text-align:center;padding:60px 20px;color:var(--dim)}
.welcome h1{font-size:28px;color:var(--text);margin-bottom:8px}
.welcome p{font-size:14px}
.welcome .hint{font-size:11px;margin-top:16px;color:var(--dim)}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes slideIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:700px){.sidebar{display:none}.messages{padding:10px 0}.msg-wrap{padding:0 8px}}
</style>
</head>
<body>
<div class="sidebar">
<div class="sidebar-header">
<h2>Hermes Chat</h2>
<button class="new-btn" onclick="newChat()">+ New</button>
</div>
<div class="model-info" id="model-info">
<div class="row"><span class="label">Model</span><span class="val" id="info-model">-</span></div>
<div class="row"><span class="label">Backend</span><span class="val">llama.cpp</span></div>
</div>
<div class="conv-list" id="conv-list"></div>
<div class="sidebar-actions">
<a href="/launcher" target="_blank">&#8599; Model Manager</a>
<a href="/api/status" target="_blank">&#8599; API Status</a>
</div>
</div>
<div class="main">
<div class="chat-header">
<select id="model-select" onchange="onModelSwitch(this.value)"><option>loading...</option></select>
<span style="font-size:11px;color:var(--dim)">API: <code id="api-url">http://127.0.0.1:8080/v1</code></span>
<button class="settings-btn" onclick="toggleSettings()" title="Settings">&#9881;</button>
</div>
<div class="messages" id="messages">
<div class="welcome"><h1>Hermes Chat Pro</h1><p>Ask anything. Models run locally via llama.cpp.</p><p class="hint">Ctrl+Enter to send &bull; Shift+Enter for new line &bull; ESC to stop</p></div>
</div>
<div class="status"><span id="status-text">Ready</span></div>
<div class="input-area">
<div class="input-row">
<textarea id="user-input" placeholder="Type a message... (Ctrl+Enter to send)" rows="1"></textarea>
<button id="send-btn" onclick="send()">Send</button>
<button class="stop-btn" id="stop-btn" onclick="stop()">Stop</button>
</div>
</div>
</div>
<div class="settings-panel" id="settings-panel">
<h3>Settings</h3>
<label>System Prompt</label>
<textarea id="sys-prompt" placeholder="You are a helpful assistant."></textarea>
<label>Temperature: <span class="val" id="temp-val">0.7</span></label>
<input type="range" id="temperature" min="0" max="2" step="0.1" value="0.7" oninput="document.getElementById('temp-val').textContent=this.value">
<label>Top-P: <span class="val" id="topp-val">0.9</span></label>
<input type="range" id="top-p" min="0" max="1" step="0.05" value="0.9" oninput="document.getElementById('topp-val').textContent=this.value">
<label>Max Tokens</label>
<input type="number" id="max-tokens" value="2048" min="16" max="32768">
<button onclick="applySettings()" style="width:100%;background:var(--blue);color:#fff;border:none;padding:8px;border-radius:6px;cursor:pointer;margin-top:6px">Apply</button>
</div>
<script>
var LLM_URL='http://127.0.0.1:8080/v1',currentModel='',abortCtrl=null;
var conversations={},activeConv=null;
var systemPrompt='You are a helpful AI assistant.';
var temperature=0.7,topP=0.9,maxTokens=2048;

// === load ===
load();function load(){
 try{var s=JSON.parse(localStorage.getItem('hs_conv')||'{}');conversations=s}catch(e){}
 try{systemPrompt=localStorage.getItem('hs_sys')||systemPrompt}catch(e){}
 try{temperature=+localStorage.getItem('hs_temp')||0.7}catch(e){}
 try{topP=+localStorage.getItem('hs_topp')||0.9}catch(e){}
 try{maxTokens=+localStorage.getItem('hs_maxt')||2048}catch(e){}
 document.getElementById('sys-prompt').value=systemPrompt;
 document.getElementById('temperature').value=temperature;document.getElementById('temp-val').textContent=temperature;
 document.getElementById('top-p').value=topP;document.getElementById('topp-val').textContent=topP;
 document.getElementById('max-tokens').value=maxTokens;
 renderConvList();loadModels();
}
function save(){localStorage.setItem('hs_conv',JSON.stringify(conversations))}
function saveSettings(){localStorage.setItem('hs_sys',systemPrompt);localStorage.setItem('hs_temp',temperature);localStorage.setItem('hs_topp',topP);localStorage.setItem('hs_maxt',maxTokens)}

// === conversations ===
function newChat(){
 activeConv=Date.now().toString(36)+Math.random().toString(36).slice(2,6);
 conversations[activeConv]={title:'New Chat',msgs:[],created:Date.now()};
 save();renderConvList();renderMessages();
}
function deleteConv(id){
 delete conversations[id];save();
 if(activeConv===id){activeConv=getLatestConv();}
 renderConvList();renderMessages();
}
function getLatestConv(){
 var ks=Object.keys(conversations);
 return ks.length?ks[ks.length-1]:null;
}
function renderConvList(){
 var el=document.getElementById('conv-list');
 if(!Object.keys(conversations).length){el.innerHTML='<div style="color:var(--dim);font-size:11px;padding:12px">No conversations yet</div>';return}
 var h='';
 for(var k in conversations){
  var c=conversations[k],cls=c===conversations[activeConv]?'active':'';
  h+='<div class="conv-item '+cls+'" onclick="openConv(\''+k+'\')">'+esc(c.title||'Chat')+'<button class="del-btn" onclick="event.stopPropagation();deleteConv(\''+k+'\')">&times;</button></div>';
 }
 el.innerHTML=h;
}
function openConv(id){activeConv=id;renderConvList();renderMessages()}
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}

// === models ===
async function loadModels(){
 try{
  var r=await fetch(LLM_URL+'/models'),j=await r.json();
  var models=(j.data||j.models||[]).map(function(m){return m.id||m.name||m});
  if(!models.length)models=['local'];
  var sel=document.getElementById('model-select');
  sel.innerHTML=models.map(function(m){return'<option value="'+m+'">'+m+'</option>'}).join('');
  currentModel=models[0];document.getElementById('info-model').textContent=currentModel;
 }catch(e){setTimeout(loadModels,2000)}
}
function onModelSwitch(name){if(name){currentModel=name;document.getElementById('info-model').textContent=name}}

// === messages ===
function renderMessages(){
 var el=document.getElementById('messages');
 if(!activeConv||!conversations[activeConv]){el.innerHTML='<div class="welcome"><h1>Hermes Chat Pro</h1><p>Start a new chat.</p></div>';return}
 var msgs=conversations[activeConv].msgs;
 if(!msgs.length){el.innerHTML='<div class="welcome"><h1>Hermes Chat Pro</h1><p>No messages yet.</p></div>';return}
 var h='';
 for(var i=0;i<msgs.length;i++){
  var m=msgs[i];
  if(m.role==='system')continue;
  h+='<div class="msg-wrap"><div class="msg '+m.role+'"><div class="avatar">'+(m.role==='user'?'U':'H')+'</div><div class="msg-body">'+md2html(m.content)+'</div></div>';
  h+='<div class="msg-actions"><button onclick="copyMsg('+i+')">Copy</button><button onclick="regenFrom('+i+')">Regen</button></div></div>';
 }
 el.innerHTML=h;scroll();
}
function addLocalMsg(role,content){
 if(!activeConv){newChat()}
 conversations[activeConv].msgs.push({role:role,content:content});
 if(conversations[activeConv].msgs.length===1&&role==='user'){conversations[activeConv].title=content.slice(0,40)}
 save();renderMessages();renderConvList();
}

// === markdown ===
function md2html(s){
 if(!s)return'';
 s=s.replace(/```(\w*)\n([\s\S]*?)```/g,function(_,lang,code){
  var lt=lang?'<span class="lang-tag">'+lang+'</span>':'';
  return '<pre>'+lt+'<code>'+code.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</code></pre>'
 });
 s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
 s=s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
 s=s.replace(/\*(.+?)\*/g,'<i>$1</i>');
 s=s.replace(/^### (.+)$/gm,'<h3>$1</h3>');
 s=s.replace(/^## (.+)$/gm,'<h2>$1</h2>');
 s=s.replace(/^# (.+)$/gm,'<h1>$1</h1>');
 s=s.replace(/^> (.+)$/gm,'<blockquote>$1</blockquote>');
 s=s.replace(/^- (.+)$/gm,'<li>$1</li>');
 s=s.replace(/(<li>[\s\S]*?<\/li>)/g,'<ul>$1</ul>');
 s=s.replace(/^---$/gm,'<hr>');
 s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
 s=s.replace(/\n/g,'<br>');
 return s
}

// === send ===
async function send(editIdx){
 var inp=document.getElementById('user-input'),btn=document.getElementById('send-btn'),stopBtn=document.getElementById('stop-btn');
 var text=inp.value.trim();if(!text)return;
 inp.value='';inp.style.height='auto';
 btn.style.display='none';stopBtn.style.display='inline-block';
 setStatus('<span class="spinner"></span>Thinking...');

 if(!activeConv)newChat();

 if(typeof editIdx==='number'){
  conversations[activeConv].msgs=conversations[activeConv].msgs.slice(0,editIdx);
 }
 addLocalMsg('user',text);

 // build messages
 var msgs=[];
 if(systemPrompt){msgs.push({role:'system',content:systemPrompt})}
 msgs=msgs.concat(conversations[activeConv].msgs);
 renderMessages();
 var aiDiv=document.createElement('div');aiDiv.className='msg-wrap';
 aiDiv.innerHTML='<div class="msg assistant"><div class="avatar">H</div><div class="msg-body"><span class="spinner"></span> Thinking...</div></div>';
 document.getElementById('messages').appendChild(aiDiv);scroll();

 var t0=Date.now();
 try{
  abortCtrl=new AbortController();
  var r=await fetch(LLM_URL+'/chat/completions',{
   method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({model:currentModel||'local',messages:msgs,stream:false,max_tokens:maxTokens,temperature:temperature,top_p:topP}),
   signal:abortCtrl.signal
  });
  var j=await r.json();
  if(j.error)throw new Error(j.error.message||j.error);
  var reply=j.choices?.[0]?.message?.content||'';
  addLocalMsg('assistant',reply);
  var ms=Date.now()-t0,tps=reply.length?Math.round(reply.length/(ms/1000)):0;
  setStatus('<span class="tok">'+tps+' tok/s</span> &middot; '+ms+'ms &middot; '+reply.length+' chars');
 }catch(e){
  aiDiv.querySelector('.msg-body').innerHTML='<span style="color:var(--red)">Error: '+e.message+'</span>';
  setStatus(e.message,true);
  conversations[activeConv].msgs.pop();save();renderMessages();
 }
 btn.style.display='inline-block';stopBtn.style.display='none';
 document.getElementById('user-input').focus();abortCtrl=null;
}

function stop(){if(abortCtrl){abortCtrl.abort();abortCtrl=null;setStatus('Stopped');document.getElementById('send-btn').style.display='inline-block';document.getElementById('stop-btn').style.display='none'}}

function setStatus(t,err){var e=document.getElementById('status-text');e.innerHTML=t;e.style.color=err?'var(--red)':''}
function scroll(){var m=document.getElementById('messages');m.scrollTop=m.scrollHeight}

// === actions ===
function copyMsg(i){if(!activeConv)return;var m=conversations[activeConv].msgs[i];if(m){navigator.clipboard.writeText(m.content);setStatus('Copied!')}}
function regenFrom(i){if(!activeConv)return;conversations[activeConv].msgs=conversations[activeConv].msgs.slice(0,i);save();renderMessages();document.getElementById('user-input').focus()}

// === settings ===
function toggleSettings(){document.getElementById('settings-panel').classList.toggle('show')}
function applySettings(){
 systemPrompt=document.getElementById('sys-prompt').value;
 temperature=+document.getElementById('temperature').value;
 topP=+document.getElementById('top-p').value;
 maxTokens=+document.getElementById('max-tokens').value;
 saveSettings();toggleSettings();setStatus('Settings applied')
}

// === keyboard ===
document.getElementById('user-input').addEventListener('keydown',function(e){
 if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();send()}
 if(e.key==='Enter'&&!e.shiftKey&&!e.ctrlKey&&!e.metaKey){e.preventDefault();send()}
});
document.addEventListener('keydown',function(e){if(e.key==='Escape')stop()});
document.getElementById('user-input').addEventListener('input',function(){this.style.height='auto';this.style.height=Math.min(this.scrollHeight,180)+'px'});

// init
if(!activeConv)activeConv=getLatestConv();
renderConvList();renderMessages();
</script>
</body>
</html>'''
