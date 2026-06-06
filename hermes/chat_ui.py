"""
Hermes Chat — professional single-file UI inspired by hermes-workspace prompt-kit.
Zero JS dependencies, 100% standalone. Design: clean dark, avatars, sessions,
markdown, code highlighting, auto-scroll, keyboard shortcuts.
"""
from __future__ import annotations

CHAT_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Hermes Chat</title>
<style>
:root{--bg:#0b0f17;--sbg:#0d131f;--card:#111827;--brd:#1e293b;--hover:#1e3050;--tx:#e2e8f0;--dm:#64748b;--ac:#22d3ee;--bl:#3b82f6;--gn:#22c55e;--rd:#ef4444;--am:#f59e0b;--r:10px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--tx);display:flex;height:100vh;overflow:hidden}
/* sidebar */
.sidebar{width:280px;background:var(--sbg);border-right:1px solid var(--brd);display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
.side-header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--brd)}
.side-header h2{font-size:14px;font-weight:600;display:flex;align-items:center;gap:6px}
.side-header h2::before{content:'';display:inline-block;width:8px;height:8px;background:var(--ac);border-radius:50%;box-shadow:0 0 8px var(--ac)}
.new-btn{background:var(--bl);color:#fff;border:none;padding:6px 14px;border-radius:8px;font-size:11px;font-weight:500;cursor:pointer}
.new-btn:hover{background:#2563eb}
.sess-list{flex:1;overflow-y:auto;padding:8px}
.sess-item{padding:10px 14px;border-radius:8px;cursor:pointer;font-size:12px;color:var(--dm);margin-bottom:2px;display:flex;align-items:center;gap:8px;transition:background .15s}
.sess-item:hover{background:var(--hover);color:var(--tx)}
.sess-item.active{background:var(--hover);color:var(--tx);border-left:2px solid var(--ac)}
.sess-item .title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sess-item .del-btn{opacity:0;color:var(--rd);background:none;border:none;cursor:pointer;font-size:14px;padding:0 4px;transition:opacity .15s}
.sess-item:hover .del-btn{opacity:1}
/* main chat */
.main{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.chat-header{display:flex;align-items:center;gap:12px;padding:12px 20px;border-bottom:1px solid var(--brd);background:var(--sbg)}
.chat-header .model-badge{display:flex;align-items:center;gap:6px;background:var(--card);border:1px solid var(--brd);padding:5px 12px;border-radius:20px;font-size:11px;cursor:pointer}
.chat-header .model-badge .dot{width:6px;height:6px;border-radius:50%;background:var(--gn);box-shadow:0 0 6px var(--gn)}
.chat-header .count{font-size:11px;color:var(--dm);margin-left:auto}
.messages{flex:1;overflow-y:auto;padding:20px 0;scroll-behavior:smooth}
.msg-wrap{max-width:800px;margin:0 auto 20px;padding:0 24px;animation:slide .2s ease}
.msg{display:flex;gap:12px}
.avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0}
.msg.user .avatar{background:var(--bl);color:#fff}
.msg.assistant .avatar{background:var(--ac);color:#0b0f17}
.msg-body{flex:1;min-width:0;padding:14px 18px;border-radius:var(--r);border:1px solid var(--brd);line-height:1.7;font-size:14px;background:var(--card)}
.msg.user .msg-body{border-color:var(--bl)}
.msg-body pre{background:#0a0f1a;padding:14px 16px;border-radius:8px;overflow-x:auto;font-size:12px;margin:10px 0;line-height:1.5;border:1px solid var(--brd);position:relative}
.msg-body pre .lang{position:absolute;top:4px;right:8px;font-size:10px;color:var(--dm)}
.msg-body code{font-family:'Cascadia Code','Fira Code',Consolas,monospace;font-size:12px}
.msg-body :not(pre)>code{background:#1e293b;padding:2px 6px;border-radius:4px}
.msg-body table{border-collapse:collapse;width:100%;margin:10px 0}
.msg-body th,.msg-body td{border:1px solid var(--brd);padding:6px 12px;text-align:left;font-size:13px}
.msg-body th{background:var(--sbg)}
.msg-body blockquote{border-left:3px solid var(--ac);padding:8px 14px;color:var(--dm);margin:10px 0;background:#0e1520;border-radius:0 6px 6px 0}
.msg-body h1,.msg-body h2,.msg-body h3{margin:12px 0 6px}
.msg-body h1{font-size:18px}.msg-body h2{font-size:16px}.msg-body h3{font-size:14px}
.msg-body ul,.msg-body ol{padding-left:20px;margin:8px 0}
.msg-actions{display:flex;gap:8px;margin-top:4px;opacity:0;transition:opacity .15s}
.msg-wrap:hover .msg-actions{opacity:1}
.msg-actions button{background:none;border:none;color:var(--dm);cursor:pointer;font-size:11px;padding:2px 6px;border-radius:4px}
.msg-actions button:hover{color:var(--tx);background:var(--hover)}
/* input */
.input-area{padding:16px 24px 20px;background:var(--sbg);border-top:1px solid var(--brd)}
.input-row{max-width:800px;margin:0 auto;display:flex;gap:10px;align-items:flex-end}
.input-row textarea{flex:1;background:var(--bg);color:var(--tx);border:1px solid var(--brd);border-radius:var(--r);padding:12px 16px;font-size:14px;resize:none;min-height:48px;max-height:180px;font-family:inherit;line-height:1.5;transition:border-color .2s}
.input-row textarea:focus{outline:none;border-color:var(--ac)}
.input-row button{background:var(--bl);color:#fff;border:none;padding:12px 24px;border-radius:var(--r);cursor:pointer;font-size:14px;font-weight:500;white-space:nowrap;transition:background .2s}
.input-row button:hover{background:#2563eb}
.input-row button:disabled{background:#334155;cursor:not-allowed}
.input-row .stop-btn{background:var(--rd);display:none}
.input-row .stop-btn:hover{background:#dc2626}
.status{padding:0 24px 8px;font-size:11px;text-align:center;min-height:20px}
.spinner{display:inline-block;width:10px;height:10px;border:2px solid var(--dm);border-top-color:var(--ac);border-radius:50%;animation:spin .8s linear infinite;margin-right:4px;vertical-align:middle}
.tok{color:var(--gn)}
/* scroll-to-bottom */
.scroll-btn{position:fixed;bottom:100px;right:32px;width:36px;height:36px;border-radius:50%;background:var(--card);border:1px solid var(--brd);color:var(--tx);cursor:pointer;display:none;align-items:center;justify-content:center;font-size:18px;z-index:10;transition:transform .2s,opacity .2s}
.scroll-btn.visible{display:flex}
.scroll-btn:hover{transform:scale(1.1)}
/* shimmer */
.shimmer{background:linear-gradient(90deg,var(--card) 30%,var(--hover) 50%,var(--card) 70%);background-size:200% 100%;animation:shimmer 2s ease-in-out infinite;border-radius:var(--r);height:16px;margin:4px 0}
.shimmer.w2{width:60%}.shimmer.w3{width:30%}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes slide{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.thinking{color:var(--dm);font-style:italic;font-size:13px;animation:pulse 1.5s ease-in-out infinite}
/* empty state */
.empty{text-align:center;padding:80px 20px;color:var(--dm)}
.empty h1{font-size:26px;color:var(--tx);margin-bottom:8px}
.empty p{font-size:13px;line-height:1.6}
.empty .hints{font-size:11px;margin-top:16px}
.empty .hints span{display:inline-block;background:var(--card);border:1px solid var(--brd);padding:4px 10px;border-radius:12px;margin:2px;cursor:pointer;transition:background .15s}
.empty .hints span:hover{background:var(--hover)}
@media(max-width:720px){.sidebar{display:none}.msg-wrap{padding:0 10px}.scroll-btn{right:12px;bottom:80px}}
</style>
</head>
<body>
<div class="sidebar">
<div class="side-header"><h2>Hermes Chat</h2><button class="new-btn" onclick="newSession()">+ New</button></div>
<div class="sess-list" id="sess-list"></div>
</div>
<div class="main">
<div class="chat-header">
<div class="model-badge" onclick="switchModel()" title="Switch model via Model Manager"><span class="dot"></span><span id="model-display">local</span></div>
<span class="count" id="msg-count"></span>
</div>
<div class="messages" id="messages"></div>
<div class="status"><span id="status-text">Ready</span></div>
<div class="input-area"><div class="input-row">
<textarea id="user-input" placeholder="Message Hermes... (Ctrl+Enter to send, Shift+Enter new line)" rows="1"></textarea>
<button id="send-btn" onclick="send()">Send</button>
<button class="stop-btn" id="stop-btn" onclick="stop()">Stop</button>
</div></div>
</div>
<button class="scroll-btn" id="scroll-btn" onclick="scrollToBottom()" title="Scroll to bottom">↓</button>
<script>
var SESSIONS=[],ACTIVE=null,MSG_HISTORY=[],ABORT=null;

async function api(url,opts){
 try{var r=await fetch(url,opts);if(!r.ok)throw new Error(await r.text());return await r.json()}
 catch(e){s('Error: '+e.message,1);return null}
}
function s(t,err){var e=document.getElementById('status-text');e.innerHTML=t;e.style.color=err?'var(--rd)':''}
function showThinking(){var d=document.createElement('div');d.className='msg-wrap';d.innerHTML='<div class="msg assistant"><div class="avatar">H</div><div class="msg-body"><div class="shimmer" style="width:80%"></div><div class="shimmer w2"></div><div class="shimmer w3"></div></div></div>';document.getElementById('messages').appendChild(d);scrollToBottom();return d}
function thinkingDone(el,html){el.innerHTML='<div class="msg assistant"><div class="avatar">H</div><div class="msg-body">'+html+'</div></div>'}
function thinkingErr(el,msg){el.innerHTML='<div class="msg assistant"><div class="avatar">H</div><div class="msg-body" style="color:var(--rd)">'+msg+'</div></div>'}

// === sessions ===
async function loadSessions(){
 var j=await api('/api/chat/sessions');
 if(!j)return;
 SESSIONS=j.sessions||[];
 renderSessList();
 // Auto-select latest
 if(!ACTIVE&&SESSIONS.length){var last=SESSIONS[SESSIONS.length-1];ACTIVE=last.id}
 renderSessList();
 if(ACTIVE)await loadHistory();
}
function renderSessList(){
 var el=document.getElementById('sess-list');
 if(!SESSIONS.length){el.innerHTML='<div style="padding:16px;color:var(--dm);font-size:11px">No conversations yet</div>';return}
 var h=SESSIONS.map(function(s){
  var cls=s.id===ACTIVE?'active':'';
  return '<div class="sess-item '+cls+'" onclick="openSession(\''+s.id+'\')"><span class="title">'+esc(s.title||'Chat')+'</span><button class="del-btn" onclick="event.stopPropagation();delSession(\''+s.id+'\')">&times;</button></div>';
 }).join('');
 el.innerHTML=h;
}
function esc(t){var d=document.createElement('div');d.textContent=t||'';return d.innerHTML}

// === history ===
async function loadHistory(){
 if(!ACTIVE){renderMessages();return}
 var j=await api('/api/chat/history?session='+ACTIVE);
 MSG_HISTORY=j?j.messages||[]:[];
 renderMessages();
}
function openSession(id){ACTIVE=id;loadHistory();renderSessList()}
async function newSession(){ACTIVE=null;MSG_HISTORY=[];renderMessages();renderSessList();document.getElementById('user-input').focus()}
async function delSession(id){await api('/api/chat/sessions/'+id,{method:'DELETE'});SESSIONS=SESSIONS.filter(function(s){return s.id!==id});if(ACTIVE===id){ACTIVE=null;MSG_HISTORY=[]};renderSessList();renderMessages()}

// === messages ===
function renderMessages(){
 var el=document.getElementById('messages'),count=document.getElementById('msg-count');
 if(!MSG_HISTORY.length){
  el.innerHTML='<div class="empty"><h1>Hermes Chat</h1><p>Ask anything. Models run locally via llama.cpp.<br>Type a message to start a conversation.</p><div class="hints">'+['"Explain quantum computing"','"Write a Python script"','"Summarize this article"'].map(function(t){return'<span onclick="quickSend(\''+t+'\')">'+t+'</span>'}).join(' ')+'</div></div>';
  count.textContent='';return;
 }
 count.textContent=MSG_HISTORY.length+' messages';
 var h='';
 for(var i=0;i<MSG_HISTORY.length;i++){
  var m=MSG_HISTORY[i];
  if(!m.content)continue;
  var role=m.role==='user'?'user':'assistant';
  var avatar=role==='user'?'U':'H';
  var body=role==='user'?esc(m.content):md2html(m.content);
  h+='<div class="msg-wrap"><div class="msg '+role+'"><div class="avatar">'+avatar+'</div><div class="msg-body">'+body+'</div></div><div class="msg-actions"><button onclick="copyText(this,\''+i+'\')">Copy</button><button onclick="regen('+i+')">Regen</button></div></div>';
 }
 el.innerHTML=h;scrollToBottom();
}

// === markdown ===
function md2html(s){
 if(!s)return'';
 // code blocks
 s=s.replace(/```(\w*)\n([\s\S]*?)```/g,function(_,lang,code){
  var lt=lang?'<span class="lang">'+lang+'</span>':'';
  return '<pre>'+lt+'<code>'+code.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</code></pre>';
 });
 // inline code, bold, italic
 s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
 s=s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
 s=s.replace(/\*(.+?)\*/g,'<i>$1</i>');
 // headings
 s=s.replace(/^### (.+)$/gm,'<h3>$1</h3>');
 s=s.replace(/^## (.+)$/gm,'<h2>$1</h2>');
 s=s.replace(/^# (.+)$/gm,'<h1>$1</h1>');
 // blockquotes
 s=s.replace(/^>\s?(.+)$/gm,'<blockquote>$1</blockquote>');
 // lists
 s=s.replace(/^- (.+)$/gm,'<li>$1</li>');
 s=s.replace(/(<li>[\s\S]*?<\/li>)/g,'<ul>$1</ul>');
 // hr
 s=s.replace(/^---$/gm,'<hr>');
 s=s.replace(/\n/g,'<br>');
 return s;
}

// === send ===
async function send(){
 var inp=document.getElementById('user-input'),sendBtn=document.getElementById('send-btn'),stopBtn=document.getElementById('stop-btn');
 var txt=inp.value.trim();if(!txt)return;
 inp.value='';inp.style.height='auto';
 sendBtn.style.display='none';stopBtn.style.display='inline-block';
 s('<span class="spinner"></span>Thinking...');

 // Add user message
 MSG_HISTORY.push({role:'user',content:txt});
 renderMessages();
 var thinkingEl=showThinking();

 var t0=Date.now();
 try{
  ABORT=new AbortController();
  var r=await fetch('/api/chat/send',{
   method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({message:txt,session:ACTIVE||undefined}),
   signal:ABORT.signal
  });
  var j=await r.json();

  if(j.ok){
   if(!ACTIVE&&j.session_id){ACTIVE=j.session_id;renderSessList()}
   if(j.user_message)MSG_HISTORY.push(j.user_message);
   if(j.message){MSG_HISTORY.push(j.message)}
   renderMessages();loadSessions();
   var ms=Date.now()-t0;
   s('<span class="tok">'+Math.round((j.message.content.length/(ms/1000))||0)+' tok/s</span> &middot; '+ms+'ms');
  }else{
   thinkingErr(thinkingEl,'Error: '+(j.error||'unknown'));
   s(j.error||'Failed',1);
  }
 }catch(e){
  if(e.name!=='AbortError'){
   thinkingErr(thinkingEl,'Error: '+e.message);
   s(e.message,1);
  }
 }
 sendBtn.style.display='inline-block';stopBtn.style.display='none';
 inp.focus();ABORT=null;
}

function stop(){if(ABORT){ABORT.abort();ABORT=null;s('Stopped');document.getElementById('send-btn').style.display='inline-block';document.getElementById('stop-btn').style.display='none'}}

// === helpers ===
function scrollToBottom(){var m=document.getElementById('messages');m.scrollTop=m.scrollHeight}
function quickSend(t){document.getElementById('user-input').value=t;send()}
function copyText(btn,i){var m=MSG_HISTORY[i];if(m){navigator.clipboard.writeText(m.content).then(function(){btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy'},1500)})}}
function regen(i){MSG_HISTORY=MSG_HISTORY.slice(0,i);renderMessages();document.getElementById('user-input').focus()}
function switchModel(){window.open('/launcher','_blank')}
function showStatus(){window.open('/api/status','_blank')}

// === scroll-to-bottom button ===
var scrollBtn=document.getElementById('scroll-btn');
document.getElementById('messages').addEventListener('scroll',function(){
 var m=this,d=m.scrollHeight-m.scrollTop-m.clientHeight;
 scrollBtn.classList.toggle('visible',d>150);
});
scrollBtn.addEventListener('click',function(){scrollToBottom();scrollBtn.classList.remove('visible')});

// === keyboard ===
document.getElementById('user-input').addEventListener('keydown',function(e){
 if((e.key==='Enter')&&(e.ctrlKey||e.metaKey)){e.preventDefault();send()}
});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&ABORT)stop()});
document.getElementById('user-input').addEventListener('input',function(){this.style.height='auto';this.style.height=Math.min(this.scrollHeight,180)+'px'});

// === init ===
loadSessions();
setInterval(function(){
 fetch('/api/chat/sessions').then(function(r){return r.json()}).then(function(j){
  if(j&&j.sessions){
   var models=[];
   fetch('http://127.0.0.1:8080/v1/models').then(function(r){return r.json()}).then(function(j2){
    var m=j2.data||j2.models||[];
    if(m.length)document.getElementById('model-display').textContent=m[0].id||'local';
   }).catch(function(){});
  }
 }).catch(function(){});
},15000);
</script>
</body>
</html>"""
