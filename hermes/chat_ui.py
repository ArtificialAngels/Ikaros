"""Hermes Studio — multi-page AI workspace. Zero JS deps, single file."""
CHAT_HTML = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes Studio</title>
<style>
:root{--bg:#0b0e14;--sbg:#0e1219;--card:#131826;--brd:#1c2333;--hover:#1a2440;--tx:#dbe2ec;--dm:#64748b;--ac:#40e0d0;--bl:#4d87f0;--gn:#34d399;--rd:#f87171;--am:#fbbf24;--pi:#a78bfa;--r:12px;--rs:8px;--font:system-ui,'Segoe UI',sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--bg);color:var(--tx);display:flex;height:100vh;overflow:hidden;font-size:14px}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--brd);border-radius:10px}
/* sidebar */
.sidebar{width:220px;background:var(--sbg);border-right:1px solid var(--brd);display:flex;flex-direction:column;flex-shrink:0;overflow:hidden;z-index:10}
.side-logo{padding:18px 16px;font-size:14px;font-weight:700;letter-spacing:-.3px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--brd)}
.side-logo .dot{width:10px;height:10px;background:var(--ac);border-radius:50%;box-shadow:0 0 10px rgba(64,224,208,.4);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.side-nav{flex:1;overflow-y:auto;padding:8px}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:var(--rs);cursor:pointer;font-size:13px;color:var(--dm);margin-bottom:1px;transition:all .12s;position:relative}
.nav-item:hover{background:var(--hover);color:var(--tx)}
.nav-item.active{background:var(--hover);color:var(--ac)}
.nav-item.active::before{content:'';position:absolute;left:0;top:8px;bottom:8px;width:2px;background:var(--ac);border-radius:0 3px 3px 0}
.nav-item .ico{width:20px;height:20px;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.nav-divider{height:1px;background:var(--brd);margin:6px 14px}
.nav-label{padding:6px 14px;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--dm);font-weight:600}
/* main */
.main{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden}
.page-header{display:flex;align-items:center;gap:12px;padding:14px 24px;border-bottom:1px solid var(--brd);background:var(--sbg);min-height:50px}
.page-header h2{font-size:15px;font-weight:600;letter-spacing:-.2px}
.page-content{flex:1;overflow-y:auto;padding:24px}.chat-content{padding:0}
/* pages */
.page{display:none;flex:1;flex-direction:column;overflow:hidden;animation:pgIn .25s ease}
.page.show{display:flex}
@keyframes pgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
/* chat */
.chat-msg{flex:1;overflow-y:auto;padding:24px 0;scroll-behavior:smooth}
.msg-row{max-width:840px;margin:0 auto 18px;padding:0 28px;animation:msgIn .2s ease}
@keyframes msgIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.msg-bubble{display:flex;gap:12px}
.avatar{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0}
.msg-bubble.user .avatar{background:linear-gradient(135deg,var(--bl),#6d28d9);color:#fff}
.msg-bubble.agent .avatar{background:linear-gradient(135deg,var(--ac),#0ea5e9);color:#0b0e14}
.msg-body{flex:1;min-width:0;line-height:1.7}
.msg-body pre{background:#090d15;padding:14px 16px;border-radius:8px;overflow-x:auto;font-size:12px;margin:10px 0;line-height:1.6;border:1px solid var(--brd);position:relative}
.msg-body pre::after{content:attr(data-l);position:absolute;top:6px;right:10px;font-size:10px;color:var(--dm)}
.msg-body code{font-family:Consolas,monospace;font-size:12px}
.msg-body :not(pre)>code{background:#1a2440;padding:2px 6px;border-radius:4px;color:var(--ac)}
.msg-body blockquote{border-left:3px solid var(--ac);padding:10px 14px;color:var(--dm);margin:10px 0;background:rgba(64,224,208,.04);border-radius:0 6px 6px 0}
.msg-body table{border-collapse:collapse;width:100%;margin:10px 0}.msg-body td,.msg-body th{border:1px solid var(--brd);padding:6px 12px;text-align:left}.msg-body th{background:var(--sbg)}
.chat-input-area{padding:16px 28px 20px;background:var(--sbg);border-top:1px solid var(--brd)}
.chat-input-row{max-width:840px;margin:0 auto;display:flex;gap:10px;align-items:flex-end}
.chat-input-row textarea{flex:1;background:var(--bg);color:var(--tx);border:1px solid var(--brd);border-radius:var(--r);padding:13px 16px;font-size:14px;resize:none;min-height:50px;max-height:180px;font-family:var(--font);line-height:1.5;transition:border-color .2s,box-shadow .2s}
.chat-input-row textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px rgba(64,224,208,.1)}
.btn{background:var(--bl);color:#fff;border:none;padding:10px 20px;border-radius:var(--rs);cursor:pointer;font-size:13px;font-weight:500;transition:all .15s}.btn:hover{opacity:.9}.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-send{padding:12px 26px;font-weight:600;border-radius:var(--r)}.btn-sm{padding:6px 14px;font-size:11px}.btn-red{background:var(--rd)}.btn-ac{background:var(--ac);color:#0b0e14}
/* cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--brd);border-radius:var(--r);padding:18px 20px;transition:all .15s}
.card:hover{border-color:var(--dm);transform:translateY(-1px)}
.card .card-title{font-size:13px;font-weight:600;margin-bottom:6px}
.card .card-meta{font-size:11px;color:var(--dm)}.card .card-val{font-size:20px;font-weight:700;color:var(--ac)}
/* stat row */
.stat-row{display:flex;gap:14px;margin-bottom:20px;flex-wrap:wrap}
.stat-box{flex:1;min-width:140px;background:var(--card);border:1px solid var(--brd);border-radius:var(--r);padding:16px 20px}
.stat-box .label{font-size:11px;color:var(--dm);margin-bottom:4px}
.stat-box .val{font-size:22px;font-weight:700;color:var(--ac)}
/* list */
.list-item{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:var(--rs);cursor:pointer;transition:background .12s;border:1px solid transparent}
.list-item:hover{background:var(--hover)}.list-item .li-title{flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.list-item .li-meta{font-size:11px;color:var(--dm)}
/* status */
.status-bar{padding:0 28px 8px;font-size:11px;text-align:center;color:var(--dm);min-height:20px}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--brd);border-top-color:var(--ac);border-radius:50%;animation:spin .7s linear infinite;margin-right:6px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.tok{color:var(--gn)}
/* shimmer */
.shimmer-box{max-width:840px;margin:0 auto 18px;padding:0 28px}
.shimmer{background:linear-gradient(90deg,var(--card) 25%,var(--hover) 50%,var(--card) 75%);background-size:200% 100%;animation:shimmer 2s infinite;border-radius:6px;margin:6px 0}.shimmer.s1{height:14px;width:75%}.shimmer.s2{height:14px;width:55%}.shimmer.s3{height:14px;width:85%}.shimmer.s4{height:14px;width:40%}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
/* empty */
.empty{padding:60px 20px;text-align:center;color:var(--dm)}.empty h2{font-size:18px;color:var(--tx);margin-bottom:8px}.empty p{font-size:13px}
/* scroll btn */
.scroll-btn{position:fixed;bottom:110px;right:32px;width:36px;height:36px;border-radius:50%;background:var(--card);border:1px solid var(--brd);color:var(--tx);cursor:pointer;display:none;align-items:center;justify-content:center;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,.3)}.scroll-btn.on{display:flex}
/* modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center}.modal-bg.on{display:flex}
.modal{background:var(--card);border:1px solid var(--brd);border-radius:var(--r);padding:24px;min-width:340px;max-width:500px;animation:pgIn .2s ease}.modal h3{margin-bottom:12px;font-size:15px}.modal input,.modal textarea{width:100%;background:var(--bg);color:var(--tx);border:1px solid var(--brd);border-radius:var(--rs);padding:8px 12px;font-size:13px;font-family:var(--font);margin-bottom:10px}.modal .btn-row{display:flex;gap:8px;justify-content:flex-end}
/* sessions sidebar */
.sess-item{padding:10px 14px;border-radius:var(--rs);cursor:pointer;font-size:12px;margin:0 6px 1px;display:flex;align-items:center;gap:8px;transition:all .12s;color:var(--dm)}.sess-item:hover{background:var(--hover);color:var(--tx)}.sess-item.on{background:var(--hover);color:var(--ac)}.sess-item .si-title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sess-item .si-del{opacity:0;color:var(--rd);border:none;background:none;cursor:pointer;font-size:14px}.sess-item:hover .si-del{opacity:1}
/* mobile */
@media(max-width:750px){.sidebar{display:none}.msg-row{padding:0 12px}.stat-row{flex-wrap:wrap}.stat-box{min-width:100px}}
</style>
</head>
<body>
<div class="sidebar">
<div class="side-logo"><span class="dot"></span>Hermes Studio</div>
<div class="side-nav">
<div class="nav-label">Workspace</div>
<div class="nav-item active" data-page="chat" onclick="navTo('chat')"><span class="ico">💬</span> Chat</div>
<div class="nav-item" data-page="agent" onclick="navTo('agent')"><span class="ico">🤖</span> Agent</div>
<div class="nav-item" data-page="memory" onclick="navTo('memory')"><span class="ico">🧠</span> Memory</div>
<div class="nav-item" data-page="skills" onclick="navTo('skills')"><span class="ico">⚡</span> Skills</div>
<div class="nav-item" data-page="models" onclick="navTo('models')"><span class="ico">🔮</span> Models</div>
<div class="nav-divider"></div>
<div class="nav-label">Data</div>
<div class="nav-item" data-page="history" onclick="navTo('history')"><span class="ico">📋</span> History</div>
<div class="nav-item" data-page="archive" onclick="navTo('archive')"><span class="ico">📦</span> Archive</div>
<div class="nav-item" data-page="plugins" onclick="navTo('plugins')"><span class="ico">🔌</span> Plugins</div>
</div>
<div class="sess-list" id="sessions" style="flex:1;overflow-y:auto;padding:4px 0;border-top:1px solid var(--brd)"></div>
<button class="btn btn-sm btn-ac" style="margin:8px 12px;width:calc(100% - 24px)" onclick="newChat()">+ New Chat</button>
</div>
<div class="main">
<!-- CHAT -->
<div class="page show" id="page-chat">
<div class="chat-msg" id="messages"></div>
<div class="status-bar"><span id="status-text">Ready</span></div>
<div class="chat-input-area"><div class="chat-input-row">
<textarea id="user-input" placeholder="Message Hermes..." rows="1"></textarea>
<button class="btn btn-send" id="send-btn" onclick="sendMsg()">Send</button>
<button class="btn btn-send btn-red" id="stop-btn" onclick="stopMsg()" style="display:none">Stop</button>
</div></div>
</div>
<!-- AGENT -->
<div class="page" id="page-agent">
<div class="page-header"><h2>🤖 Agent</h2></div>
<div class="page-content"><div id="agent-content"></div></div>
</div>
<!-- MEMORY -->
<div class="page" id="page-memory">
<div class="page-header"><h2>🧠 Memory</h2></div>
<div class="page-content"><div id="memory-content"></div></div>
</div>
<!-- SKILLS -->
<div class="page" id="page-skills">
<div class="page-header"><h2>⚡ Skills</h2></div>
<div class="page-content"><div id="skills-content"></div></div>
</div>
<!-- MODELS -->
<div class="page" id="page-models">
<div class="page-header"><h2>🔮 Models</h2></div>
<div class="page-content"><div id="models-content"></div></div>
</div>
<!-- HISTORY -->
<div class="page" id="page-history">
<div class="page-header"><h2>📋 History</h2></div>
<div class="page-content"><div id="history-content"></div></div>
</div>
<!-- ARCHIVE -->
<div class="page" id="page-archive">
<div class="page-header"><h2>📦 Archive</h2></div>
<div class="page-content"><div id="archive-content"></div></div>
</div>
<!-- PLUGINS -->
<div class="page" id="page-plugins">
<div class="page-header"><h2>🔌 Plugins</h2></div>
<div class="page-content"><div id="plugins-content"></div></div>
</div>
</div>
<button class="scroll-btn" id="scroll-btn" onclick="document.getElementById('messages').scrollTop=document.getElementById('messages').scrollHeight">↓</button>

<script>
var cur='chat',MSGS=[],SID=null,SESS=[],LOAD=false,AB=null;
var E=function(s){return document.getElementById(s)};

//==== navigation ====
function navTo(p){
 document.querySelectorAll('.nav-item').forEach(function(n){n.classList.toggle('active',n.dataset.page===p)});
 document.querySelectorAll('.page').forEach(function(n){n.classList.toggle('show',n.id==='page-'+p)});
 cur=p;
 if(p==='agent')loadAgent();if(p==='memory')loadMemory();if(p==='skills')loadSkills();
 if(p==='models')loadModels();if(p==='history')loadHistory();if(p==='archive')loadArchive();if(p==='plugins')loadPlugins();
}

//==== api ====
async function api(url,opts){
 try{var r=await fetch(url,opts);if(!r.ok){var t=await r.text();throw new Error(t)}return await r.json()}
 catch(e){st(e.message,1);return null}
}
function st(t,err){E('status-text').innerHTML=t;E('status-text').style.color=err?'var(--rd)':''}
function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML}

//==== sessions ====
async function loadSessions(){
 var j=await api('/api/chat/sessions');if(!j)return;
 SESS=j.sessions||[];renderSessions();
 if(!SID&&SESS.length){SID=SESS[SESS.length-1].id;loadHistory()}
}
function renderSessions(){
 var h='',el=E('sessions');
 SESS.forEach(function(s){var cl=s.id===SID?'on':'';h+='<div class="sess-item '+cl+'" onclick="openSession(\''+s.id+'\')"><span class="si-title">'+esc(s.title||'Chat')+'</span><button class="si-del" onclick="event.stopPropagation();delSess(\''+s.id+'\')">×</button></div>'});
 el.innerHTML=h||'<div style="padding:12px;color:var(--dm);font-size:11px;text-align:center">No chats</div>';
}
function openSession(id){SID=id;loadHistory();navTo('chat');renderSessions()}
function newChat(){SID=null;MSGS=[];renderChat();renderSessions();E('user-input').focus();navTo('chat')}
async function delSess(id){await api('/api/chat/sessions/'+id,{method:'DELETE'});SESS=SESS.filter(function(s){return s.id!==id});if(SID===id){SID=SESS.length?SESS[SESS.length-1].id:null;MSGS=[]};renderSessions();renderChat()}

//==== history load ====
async function loadHistory(){
 if(!SID){renderChat();return}
 var j=await api('/api/chat/history?session='+SID);
 MSGS=j?j.messages||[]:[];renderChat();
}

//==== chat render ====
function md(s){
 if(!s)return'';s=s.replace(/```(\w*)\n([\s\S]*?)```/g,function(_,l,c){return'<pre data-l="'+(l||'text')+'"><code>'+c.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</code></pre>'});
 s=s.replace(/`([^`]+)`/g,'<code>$1</code>');s=s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');s=s.replace(/\*(.+?)\*/g,'<i>$1</i>');
 s=s.replace(/^### (.+)$/gm,'<h3>$1</h3>');s=s.replace(/^## (.+)$/gm,'<h2>$1</h2>');s=s.replace(/^# (.+)$/gm,'<h1>$1</h1>');
 s=s.replace(/^>\s?(.+)$/gm,'<blockquote>$1</blockquote>');s=s.replace(/^- (.+)$/gm,'<li>$1</li>');
 s=s.replace(/(<li>[\s\S]*?<\/li>)/g,'<ul>$1</ul>');s=s.replace(/^---$/gm,'<hr>');s=s.replace(/\n/g,'<br>');
 return s;
}
function renderChat(){
 var el=E('messages');if(!MSGS.length){el.innerHTML='<div class="empty"><h2>Hermes Studio</h2><p>Local AI workspace. Start a conversation or explore the panels.</p></div>';return}
 var h='';for(var i=0;i<MSGS.length;i++){var m=MSGS[i];if(!m.content)continue;var r=m.role==='user'?'user':'agent',av=r==='user'?'U':'H';h+='<div class="msg-row"><div class="msg-bubble '+r+'"><div class="avatar">'+av+'</div><div class="msg-body">'+(r==='user'?esc(m.content):md(m.content))+'</div></div></div>';}
 el.innerHTML=h;scrollDown();
}
function scrollDown(){var m=E('messages');m.scrollTop=m.scrollHeight}
E('messages').addEventListener('scroll',function(){var d=this.scrollHeight-this.scrollTop-this.clientHeight;E('scroll-btn').classList.toggle('on',d>200)});

//==== send ====
async function sendMsg(){
 if(LOAD)return;var inp=E('user-input'),sb=E('send-btn'),stb=E('stop-btn'),tx=inp.value.trim();if(!tx)return;
 inp.value='';inp.style.height='auto';LOAD=true;sb.style.display='none';stb.style.display='inline-block';st('<span class="spin"></span>Thinking...');
 MSGS.push({role:'user',content:tx});renderChat();
 var shimmer=document.createElement('div');shimmer.className='shimmer-box';shimmer.innerHTML='<div class="msg-bubble agent"><div class="avatar">H</div><div style="flex:1"><div class="shimmer s1"></div><div class="shimmer s2"></div><div class="shimmer s3"></div><div class="shimmer s4"></div></div></div>';
 E('messages').appendChild(shimmer);scrollDown();
 var t0=Date.now();
 try{AB=new AbortController();
  var r=await fetch('/api/chat/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:tx,session:SID||undefined}),signal:AB.signal});
  var j=await r.json();shimmer.remove();
  if(j.ok){if(!SID&&j.session_id){SID=j.session_id;renderSessions()}if(j.message)MSGS.push(j.message);renderChat();loadSessions();
   var tok=j.message.content.length,ms=Date.now()-t0;st('<span class="tok">'+Math.round(tok/(ms/1000)||0)+' tok/s</span> · '+ms+'ms');}
  else st('Error: '+(j.error||'?'),1);
 }catch(e){shimmer.remove();if(e.name!=='AbortError')st(e.message,1)}
 sb.style.display='inline-block';stb.style.display='none';LOAD=false;inp.focus();AB=null;
}
function stopMsg(){if(AB){AB.abort();AB=null;st('Stopped');E('send-btn').style.display='inline-block';E('stop-btn').style.display='none';LOAD=false}}

//==== AGENT page ====
async function loadAgent(){
 var j=await api('/api/status');if(!j)return;
 var h='<div class="stat-row">';
 h+='<div class="stat-box"><div class="label">Version</div><div class="val">'+(j.version||'?')+'</div></div>';
 h+='<div class="stat-box"><div class="label">Mode</div><div class="val">'+(j.mode||'?').toUpperCase()+'</div></div>';
 h+='<div class="stat-box"><div class="label">Session Turns</div><div class="val">'+(j.turn_count||0)+'</div></div>';
 h+='<div class="stat-box"><div class="label">LLM Available</div><div class="val" style="color:var(--gn)">'+(j.llm_available?'Yes':'No')+'</div></div>';
 h+='</div><h3 style="margin:20px 0 10px">Providers</h3><div class="cards">';
 (j.providers||[]).forEach(function(p){h+='<div class="card"><div class="card-title">'+p.name+'</div><div class="card-meta">'+p.url+'</div></div>'});
 h+='</div><h3 style="margin:20px 0 10px">Agent Info</h3><div class="cards"><div class="card"><div class="card-title">Data Directory</div><div class="card-meta">'+(j.data_dir||'?')+'</div></div>';
 h+='<div class="card"><div class="card-title">Skills Loaded</div><div class="card-val">'+(j.skills||[]).length+'</div></div></div>';
 E('agent-content').innerHTML=h;
}

//==== MEMORY page ====
async function loadMemory(){
 var j=await api('/api/status');if(!j)return;
 var mem=j.memory||{},kb=j.knowledge||{};
 var h='<div class="stat-row"><div class="stat-box"><div class="label">Memory Items</div><div class="val">'+(mem.total_items||0)+'</div></div>';
 h+='<div class="stat-box"><div class="label">Knowledge Chunks</div><div class="val">'+(kb.total_chunks||0)+'</div></div>';
 h+='<div class="stat-box"><div class="label">Skills</div><div class="val">'+(j.skills||[]).length+'</div></div>';
 h+='</div><h3 style="margin:20px 0 10px">Recent Memories</h3><div style="display:flex;flex-direction:column;gap:6px">';
 (mem.recent||[]).slice(0,20).forEach(function(m){h+='<div class="list-item"><div class="li-title">'+esc(m.text||'')+'</div><div class="li-meta">'+(m.tags||[]).join(', ')+'</div></div>'});
 h+='</div>';E('memory-content').innerHTML=h;
}

//==== SKILLS page ====
async function loadSkills(){
 var j=await api('/api/skills');if(!j)return;
 var h='<div class="cards">';
 (j||[]).forEach(function(s){h+='<div class="card"><div class="card-title">'+s.name+'</div><div class="card-meta">'+s.description+'</div><div class="card-meta" style="margin-top:4px;color:var(--ac)">'+s.category+'</div></div>'});
 h+='</div>';if(!(j||[]).length)h='<div class="empty"><h2>No Skills</h2><p>Create custom skills in data/skills/</p></div>';
 E('skills-content').innerHTML=h;
}

//==== MODELS page ==== (merges model launcher)
async function loadModels(){
 var h='<div style="display:flex;gap:14px;margin-bottom:20px"><button class="btn btn-ac" onclick="scanModels()">Scan Models</button><button class="btn" onclick="window.open(\'/launcher\',\'_blank\')">Model Manager</button></div><div id="models-list">Loading...</div>';
 E('models-content').innerHTML=h;
 scanModels();
}
async function scanModels(){
 try{
  var r=await fetch('http://127.0.0.1:8080/v1/models');
  var j=await r.json(),models=j.data||j.models||[],rows='<div class="cards">';
  models.forEach(function(m,i){
   var nm=m.id||m.name||m,isActive=i===0;
   rows+='<div class="card" style="border-color:'+(isActive?'var(--ac)':'var(--brd)')+'"><div class="card-title">'+(isActive?'● ':'')+nm+'</div><div class="card-meta">OpenAI-compatible · :8080</div></div>';
  });
  rows+='</div>';if(!models.length)rows='<div class="empty"><h2>No Models</h2><p>Local llama-server not running</p></div>';
  E('models-list').innerHTML=rows;
 }catch(e){E('models-list').innerHTML='<div class="empty"><h2>Connection Error</h2><p>'+e.message+'</p></div>'}
}

//==== HISTORY page ====
async function loadHistory(){
 var j=await api('/api/status');if(!j)return;
 var mem=j.memory||{},h='<h3 style="margin-bottom:14px">Recent Activity ('+(mem.total_items||0)+' items)</h3>';
 h+='<div style="display:flex;flex-direction:column;gap:4px">';
 (mem.recent||[]).slice(0,50).forEach(function(m){h+='<div class="list-item"><div class="li-title">'+esc(m.text||'')+'</div><div class="li-meta">'+(m.tags||[]).join(', ')+'</div></div>'});
 h+='</div>';E('history-content').innerHTML=h;
}

//==== ARCHIVE page ====
async function loadArchive(){
 var j=await api('/api/chat/sessions');if(!j)return;
 var s=(j.sessions||[]).slice(0).reverse(),h='<div style="display:flex;flex-direction:column;gap:4px">';
 s.forEach(function(s){h+='<div class="list-item" onclick="openSession(\''+s.id+'\')"><div class="li-title">📁 '+esc(s.title||'Chat')+'</div><div class="li-meta">'+s.message_count+' msgs</div></div>'});
 h+='</div>';if(!s.length)h='<div class="empty"><h2>No Archives</h2><p>Completed conversations appear here</p></div>';
 E('archive-content').innerHTML=h;
}

//==== PLUGINS page ====
async function loadPlugins(){
 var enable='✅',disable='⏸️',h='<div class="cards">';
 h+='<div class="card"><div class="card-title">'+enable+' Hermes Chat API</div><div class="card-meta">/api/chat/* endpoints</div></div>';
 h+='<div class="card"><div class="card-title">'+enable+' OpenAI Shim</div><div class="card-meta">/v1/* endpoints</div></div>';
 h+='<div class="card"><div class="card-title">'+enable+' Embedding Service</div><div class="card-meta">/v1/embeddings</div></div>';
 h+='<div class="card"><div class="card-title">'+enable+' Model Launcher</div><div class="card-meta">/launcher</div></div>';
 h+='<div class="card"><div class="card-title">'+disable+' RAG Pipeline</div><div class="card-meta">Install sentence-transformers</div></div>';
 h+='</div>';E('plugins-content').innerHTML=h;
}

//==== keyboard ====
E('user-input').addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg()}});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&AB)stopMsg()});
E('user-input').addEventListener('input',function(){this.style.height='auto';this.style.height=Math.min(this.scrollHeight,180)+'px'});

// init
loadSessions();
setInterval(function(){
 fetch('/api/status').then(function(r){return r.json()}).then(function(j){
  if(j&&j.mode){var n=document.querySelector('.side-logo .dot');if(n)n.style.background=j.mode==='cloud'?'var(--rd)':'var(--ac)'}
 }).catch(function(){});
},15000);
</script>
</body>
</html>'''
