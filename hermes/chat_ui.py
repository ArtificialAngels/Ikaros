"""
Hermes Chat — lightweight single‑file chat UI (no node_modules, no build step).
Supports: multi‑turn, markdown, code highlight, model switching, streaming.
"""
from __future__ import annotations

CHAT_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Hermes Chat</title>
<style>
:root{--bg:#0a0e14;--side:#0d1117;--user-msg:#1a2744;--ai-msg:#151b23;--border:#1f2937;--text:#e6edf3;--dim:#8b95a1;--accent:#2dd4bf;--blue:#2563eb;--red:#ef4444;--green:#3fb950}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden}
.sidebar{width:260px;background:var(--side);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;overflow-y:auto}
.side-header{padding:16px;border-bottom:1px solid var(--border)}
.side-header h2{font-size:15px;font-weight:600}
.side-header .ver{font-size:10px;color:var(--dim)}
.model-card{margin:12px;padding:10px 12px;background:#0e1e1c;border:1px solid var(--accent);border-radius:6px;font-size:11px}
.model-card .label{color:var(--dim);font-size:9px;text-transform:uppercase;margin-bottom:2px}
.model-card .name{color:var(--accent);font-family:ui-monospace,monospace;font-size:11px;word-break:break-all}
.actions{padding:12px;flex:1}
.actions a,.actions button,.actions select{display:block;width:100%;padding:8px 12px;margin-bottom:4px;border-radius:4px;font-size:11px;color:var(--text);text-decoration:none;cursor:pointer;background:none;border:1px solid transparent;text-align:left;font-family:inherit}
.actions a:hover,.actions button:hover,.actions select:hover{background:#1f2937}
.actions select{-webkit-appearance:none;appearance:none;background:var(--side);border:1px solid var(--border)}
.actions .warn{color:var(--red)}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.messages{flex:1;overflow-y:auto;padding:20px 0;scroll-behavior:smooth}
.msg{max-width:760px;margin:0 auto 20px;padding:12px 18px;border-radius:10px;line-height:1.65;font-size:14px;animation:slideIn .2s ease}
.msg.user{background:var(--user-msg);margin-right:24px}
.msg.assistant{background:var(--ai-msg);border:1px solid var(--border);margin-left:24px}
.msg.system{color:var(--dim);font-size:12px;text-align:center;margin:20px 0;background:none}
.msg pre{background:#0a0e14;padding:12px 14px;border-radius:6px;overflow-x:auto;font-size:12px;margin:8px 0;line-height:1.45;border:1px solid var(--border)}
.msg code{font-family:'Cascadia Code','Fira Code','JetBrains Mono',Consolas,monospace;font-size:12px}
.msg p code,.msg li code{background:#1f2937;padding:1px 5px;border-radius:3px}
.msg table{border-collapse:collapse;width:100%;margin:8px 0}
.msg th,.msg td{border:1px solid var(--border);padding:6px 10px;text-align:left;font-size:13px}
.msg th{background:var(--side)}
.msg blockquote{border-left:3px solid var(--accent);padding-left:12px;color:var(--dim);margin:8px 0}
.input-area{padding:16px 24px 20px;border-top:1px solid var(--border);background:var(--side)}
.input-row{max-width:760px;margin:0 auto;display:flex;gap:10px;align-items:flex-end}
.input-row textarea{flex:1;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:12px 16px;font-size:14px;resize:none;min-height:48px;max-height:180px;font-family:inherit;line-height:1.5}
.input-row textarea:focus{outline:none;border-color:var(--accent)}
.input-row button{background:var(--blue);color:#fff;border:none;padding:12px 24px;border-radius:10px;cursor:pointer;font-size:14px;font-weight:500;white-space:nowrap}
.input-row button:hover{background:#1d4ed8}
.input-row button:disabled{background:#374151;cursor:not-allowed}
.status{padding:0 24px 8px;font-size:11px;text-align:center;min-height:16px}
.status .spinner{display:inline-block;width:10px;height:10px;border:2px solid var(--dim);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;margin-right:4px;vertical-align:middle}
.status .tps{color:var(--green)}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes slideIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:640px){.sidebar{display:none}.msg{margin-left:8px;margin-right:8px}}
</style>
</head>
<body>
<div class="sidebar">
 <div class="side-header"><h2>Hermes Chat</h2><span class="ver">v2.1</span></div>
 <div class="model-card" id="model-card">
  <div class="label">CURRENT MODEL</div>
  <div class="name" id="model-name">...</div>
 </div>
 <div class="actions">
  <select id="model-switch" onchange="switchModel(this.value)" style="margin-bottom:8px">
   <option value="">Switch Model...</option>
  </select>
  <a href="/launcher" target="_blank">Model Manager</a>
  <a href="/api/status" target="_blank">Status JSON</a>
  <a href="/chat?history=1" onclick="window.location.reload();return false">New Chat</a>
  <button onclick="exportChat()">Export Chat</button>
  <button class="warn" onclick="clearAll()">Clear All</button>
 </div>
</div>
<div class="main">
 <div class="messages" id="messages"></div>
 <div class="status"><span id="status-text">Ready</span></div>
 <div class="input-area">
  <div class="input-row">
   <textarea id="user-input" placeholder="Ask anything... (Shift+Enter for new line)" rows="1"
    onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"></textarea>
   <button id="send-btn" onclick="send()">Send</button>
  </div>
 </div>
</div>
<script>
const LLM_PORT=8080, LLM_URL='http://127.0.0.1:'+LLM_PORT+'/v1';
let history=[], abortCtrl=null;

// === model list ===
async function loadModels(){
 try{
  let r=await fetch(LLM_URL+'/models');
  let j=await r.json();
  let sel=document.getElementById('model-switch');
  sel.innerHTML='<option value="">Switch Model...</option>';
  (j.data||j.models||[]).forEach(function(m){
   let id=m.id||m.name||m;
   let o=document.createElement('option');o.value=id;o.textContent=id;sel.appendChild(o);
  });
  let current = sel.options[1]?sel.options[1].value:'local';
  document.getElementById('model-name').textContent=current;
 }catch(e){document.getElementById('model-name').textContent='llama-server';}
}
loadModels();

// === markdown render (light) ===
function md2html(s){
 if(!s)return'';
 // code blocks with optional language
 s=s.replace(/```(\\w*)\\n([\\s\\S]*?)```/g,function(_,lang,code){
  return '<pre><code>'+code.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</code></pre>';
 });
 // inline code
 s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
 // bold / italic
 s=s.replace(/\\*\\*(.+?)\\*\\*/g,'<b>$1</b>');
 s=s.replace(/\\*(.+?)\\*/g,'<i>$1</i>');
 // headings
 s=s.replace(/^### (.+)$/gm,'<h3>$1</h3>');
 s=s.replace(/^## (.+)$/gm,'<h2>$1</h2>');
 s=s.replace(/^# (.+)$/gm,'<h1>$1</h1>');
 // tables
 s=s.replace(/\\|(.+)\\|/g,function(m){return m.startsWith('|')?'<br>'+m+'<br>':m});
 // blockquotes
 s=s.replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>');
 // lists
 s=s.replace(/^- (.+)$/gm,'<li>$1</li>');
 s=s.replace(/(<li>.*<\\/li>)/g,'<ul>$1</ul>');
 // horizontal rules
 s=s.replace(/^---$/gm,'<hr>');
 // line breaks
 s=s.replace(/\\n/g,'<br>');
 return s;
}

// === ui helpers ===
function addMsg(role,text){
 var d=document.createElement('div');d.className='msg '+role;
 d.innerHTML=role==='assistant'?md2html(text):text;
 document.getElementById('messages').appendChild(d);
 scroll();
 return d;
}
function scroll(){
 var m=document.getElementById('messages');
 m.scrollTop=m.scrollHeight;
}
function setStatus(t,err){
 var e=document.getElementById('status-text');
 e.innerHTML=t;
 e.style.color=err?varRed:'';
}
var varRed='var(--red)';

// === send message ===
async function send(){
 var inp=document.getElementById('user-input'),btn=document.getElementById('send-btn');
 var text=inp.value.trim();if(!text)return;
 inp.value='';inp.style.height='auto';
 inp.disabled=btn.disabled=true;
 setStatus('<span class="spinner"></span> Thinking...');

 // build messages array
 history.push({role:'user',content:text});
 addMsg('user',text);
 var aiDiv=addMsg('assistant','<span class="spinner"></span>');

 // pick model
 var sel=document.getElementById('model-switch');
 var model=sel.value||'local';

 var t0=Date.now();
 try{
  abortCtrl=new AbortController();
  var r=await fetch(LLM_URL+'/chat/completions',{
   method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({
    model:model,
    messages:history,
    stream:false,
    max_tokens:2048
   }),
   signal:abortCtrl.signal
  });
  var j=await r.json();
  if(j.error){throw new Error(j.error.message||j.error)}
  var reply=j.choices?.[0]?.message?.content||'';
  aiDiv.innerHTML=md2html(reply);
  history.push({role:'assistant',content:reply});

  var ms=Date.now()-t0;
  var tps=reply.length?Math.round(reply.length/(ms/1000)):0;
  setStatus('<span class="tps">'+tps+' tok/s</span> ('+ms+'ms)');
 }catch(e){
  aiDiv.innerHTML='<span style="color:var(--red)">Error: '+e.message+'</span>';
  setStatus(e.message,true);
  history.pop(); // remove failed user msg
 }
 inp.disabled=btn.disabled=false;inp.focus();abortCtrl=null;
}

// === switch model ===
function switchModel(name){
 if(!name)return;
 document.getElementById('model-name').textContent=name;
 setStatus('Switched to '+name);
}

// === clear / export ===
function clearAll(){
 history=[];
 document.getElementById('messages').innerHTML='<div class="msg system">Chat cleared. Start a new conversation.</div>';
}
function exportChat(){
 var txt='';
 for(var i=0;i<history.length;i++){
  txt+='\\n=== '+history[i].role.toUpperCase()+' ===\\n'+history[i].content+'\\n';
 }
 var b=new Blob([txt],{type:'text/plain'});
 var a=document.createElement('a');a.href=URL.createObjectURL(b);
 a.download='hermes-chat-'+new Date().toISOString().slice(0,10)+'.txt';
 a.click();
}

// ESC to stop generating
document.addEventListener('keydown',function(e){
 if(e.key==='Escape'&&abortCtrl){abortCtrl.abort();abortCtrl=null;setStatus('Interrupted');}
});
// Auto-resize textarea
document.getElementById('user-input').addEventListener('input',function(){
 this.style.height='auto';this.style.height=Math.min(this.scrollHeight,180)+'px';
});

// Welcome
addMsg('system','Hermes Chat ready. Type a message to start.');
</script>
</body>
</html>"""
