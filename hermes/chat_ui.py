"""Hermes Chat — professional chat interface. Zero JS deps, single file."""
CHAT_HTML = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>Hermes Chat</title>
<style>
:root{--bg:#0c0e14;--sbg:#0f131b;--card:#131826;--brd:#1c2333;--hover:#1a2340;--tx:#dbe2ec;--dm:#64748b;--ac:#40e0d0;--bl:#4d87f0;--gn:#34d399;--rd:#f87171;--am:#fbbf24;--r:12px;--r-sm:8px;--font:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif}
@font-face{font-family:'Inter';font-style:normal;font-weight:400 600;font-display:swap;src:local('Segoe UI'),local('system-ui')}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--bg);color:var(--tx);display:flex;height:100vh;overflow:hidden;-webkit-font-smoothing:antialiased}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--brd);border-radius:10px}
.sidebar{width:292px;background:var(--sbg);border-right:1px solid var(--brd);display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
.side-header{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid var(--brd)}
.side-header .logo{font-size:14px;font-weight:600;letter-spacing:-.3px;display:flex;align-items:center;gap:8px}
.side-header .logo .dot{width:9px;height:9px;background:var(--ac);border-radius:50%;box-shadow:0 0 10px rgba(64,224,208,.4)}
.side-header .dot.live{animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 10px rgba(64,224,208,.4)}50%{opacity:.6;box-shadow:0 0 20px rgba(64,224,208,.7)}}
.btn-new{background:var(--bl);color:#fff;border:none;padding:6px 14px;border-radius:var(--r-sm);font-size:11px;font-weight:500;cursor:pointer;letter-spacing:.3px;transition:background .15s}
.btn-new:hover{background:#3b74e0}
.sess-list{flex:1;overflow-y:auto;padding:6px 10px}
.sess-item{padding:10px 14px;border-radius:var(--r-sm);cursor:pointer;font-size:12px;margin-bottom:1px;display:flex;align-items:center;gap:10px;transition:all .12s;color:var(--dm);position:relative}
.sess-item:hover{background:var(--hover);color:var(--tx)}
.sess-item.active{background:var(--hover);color:var(--tx)}
.sess-item.active::before{content:'';position:absolute;left:0;top:8px;bottom:8px;width:2px;background:var(--ac);border-radius:0 2px 2px 0}
.sess-item .icon{width:20px;height:20px;border-radius:6px;background:var(--card);display:flex;align-items:center;justify-content:center;font-size:10px;flex-shrink:0}
.sess-item .title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.4}
.sess-item .del-btn{opacity:0;color:var(--rd);background:none;border:none;cursor:pointer;font-size:14px;padding:2px 4px;border-radius:4px;transition:all .15s}
.sess-item:hover .del-btn{opacity:.7}.sess-item .del-btn:hover{opacity:1;background:rgba(248,113,113,.1)}
.side-footer{padding:10px 18px 16px;border-top:1px solid var(--brd)}
.side-footer .info{font-size:10px;color:var(--dm);line-height:1.6}
.side-footer .info b{color:var(--ac);font-weight:500}
.main{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.chat-header{display:flex;align-items:center;gap:12px;padding:13px 24px;border-bottom:1px solid var(--brd);background:var(--sbg);min-height:50px}
.chat-header .badge{display:flex;align-items:center;gap:6px;background:var(--card);border:1px solid var(--brd);padding:5px 14px;border-radius:20px;font-size:11px;cursor:pointer;transition:border-color .15s}
.chat-header .badge:hover{border-color:var(--ac)}
.chat-header .badge .dot{width:7px;height:7px;border-radius:50%;background:var(--gn);box-shadow:0 0 6px rgba(52,211,153,.4)}
.chat-header .meta{font-size:11px;color:var(--dm);margin-left:auto}
.messages{flex:1;overflow-y:auto;padding:24px 0;scroll-behavior:smooth}
.msg-wrap{max-width:840px;margin:0 auto 20px;padding:0 28px;animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.msg{display:flex;gap:14px}
.avatar{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0;letter-spacing:-.3px}
.msg.user .avatar{background:linear-gradient(135deg,var(--bl),#6d28d9);color:#fff;box-shadow:0 2px 8px rgba(77,135,240,.3)}
.msg.assistant .avatar{background:linear-gradient(135deg,var(--ac),#0ea5e9);color:#0c0e14;box-shadow:0 2px 8px rgba(64,224,208,.3)}
.msg-body{flex:1;min-width:0;line-height:1.75;font-size:14px}
.msg-body p{margin:6px 0}
.msg-body p:first-child{margin-top:0}
.msg-body pre{background:#090d15;padding:16px 18px;border-radius:var(--r-sm);overflow-x:auto;font-size:12px;margin:12px 0;line-height:1.6;border:1px solid var(--brd);position:relative}
.msg-body pre::before{content:attr(data-lang);position:absolute;top:6px;right:10px;font-size:10px;color:var(--dm);text-transform:uppercase;letter-spacing:.8px}
.msg-body code{font-family:'Cascadia Code','Fira Code','JetBrains Mono',Consolas,monospace;font-size:12px}
.msg-body :not(pre)>code{background:#1a2340;padding:2px 7px;border-radius:4px;font-size:12px;color:var(--ac)}
.msg-body table{border-collapse:collapse;width:100%;margin:12px 0}
.msg-body th,.msg-body td{border:1px solid var(--brd);padding:8px 14px;text-align:left;font-size:13px}
.msg-body th{background:var(--sbg);font-weight:600}
.msg-body blockquote{border-left:3px solid var(--ac);padding:10px 16px;color:var(--dm);margin:12px 0;background:rgba(64,224,208,.04);border-radius:0 6px 6px 0}
.msg-body h1,.msg-body h2,.msg-body h3{margin:16px 0 8px;font-weight:600}
.msg-body h1{font-size:20px}.msg-body h2{font-size:17px}.msg-body h3{font-size:15px}
.msg-body ul,.msg-body ol{padding-left:22px;margin:8px 0}.msg-body li{margin:4px 0}
.msg-body a{color:var(--ac);text-decoration:none}.msg-body a:hover{text-decoration:underline}
.msg-body hr{border:none;border-top:1px solid var(--brd);margin:16px 0}
.msg-body img{max-width:100%;border-radius:var(--r-sm);margin:8px 0}
.msg-actions{display:flex;gap:6px;margin-top:8px;margin-left:48px;opacity:0;transition:opacity .15s}
.msg-wrap:hover .msg-actions{opacity:1}
.msg-actions button{background:var(--card);border:1px solid var(--brd);color:var(--dm);cursor:pointer;font-size:10px;padding:3px 10px;border-radius:4px;transition:all .15s;font-family:var(--font)}
.msg-actions button:hover{color:var(--tx);border-color:var(--dm);background:var(--hover)}
.empty{text-align:center;padding:80px 20px;color:var(--dm)}
.empty h1{font-size:26px;font-weight:600;color:var(--tx);margin-bottom:12px;letter-spacing:-.5px}
.empty p{font-size:13px;line-height:1.6;max-width:420px;margin:0 auto 20px}
.empty .hints{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;max-width:500px;margin:0 auto}
.empty .hints span{background:var(--card);border:1px solid var(--brd);padding:8px 16px;border-radius:20px;font-size:12px;cursor:pointer;transition:all .15s;color:var(--tx)}
.empty .hints span:hover{background:var(--hover);border-color:var(--ac)}
.input-area{padding:18px 28px 22px;background:var(--sbg);border-top:1px solid var(--brd)}
.input-row{max-width:840px;margin:0 auto;display:flex;gap:12px;align-items:flex-end}
.input-row textarea{flex:1;background:var(--bg);color:var(--tx);border:1px solid var(--brd);border-radius:var(--r);padding:14px 18px;font-size:14px;resize:none;min-height:52px;max-height:200px;font-family:var(--font);line-height:1.55;transition:border-color .2s,box-shadow .2s}
.input-row textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px rgba(64,224,208,.1)}
.input-row textarea::placeholder{color:var(--dm)}
.btn-send{background:var(--bl);color:#fff;border:none;padding:12px 28px;border-radius:var(--r);cursor:pointer;font-size:13px;font-weight:600;white-space:nowrap;letter-spacing:.3px;transition:all .15s}
.btn-send:hover{background:#3b74e0;transform:translateY(-1px);box-shadow:0 4px 12px rgba(77,135,240,.3)}
.btn-send:disabled{background:var(--card);color:var(--dm);cursor:not-allowed;transform:none;box-shadow:none}
.btn-stop{background:var(--rd);display:none}.btn-stop:hover{background:#e05a5a}
.status{padding:0 28px 8px;font-size:11px;text-align:center;min-height:20px;color:var(--dm)}
.status .spinner{display:inline-block;width:12px;height:12px;border:2px solid var(--brd);border-top-color:var(--ac);border-radius:50%;animation:spin .7s linear infinite;margin-right:6px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.status .tok{color:var(--gn)}
/* shimmer loading */
.shimmer-wrap{padding:0 28px;max-width:840px;margin:0 auto 20px}
.shimmer{background:linear-gradient(90deg,var(--card) 25%,var(--hover) 50%,var(--card) 75%);background-size:200% 100%;animation:shimmer 2s ease-in-out infinite;border-radius:6px;margin:8px 0}
.shimmer.h1{height:14px;width:75%}.shimmer.h2{height:14px;width:55%}.shimmer.h3{height:14px;width:85%}.shimmer.h4{height:14px;width:40%;margin-bottom:16px}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
/* scroll */
.scroll-btn{position:fixed;bottom:110px;right:36px;width:38px;height:38px;border-radius:50%;background:var(--card);border:1px solid var(--brd);color:var(--tx);cursor:pointer;display:none;align-items:center;justify-content:center;font-size:17px;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,.3);transition:all .2s}
.scroll-btn.visible{display:flex}.scroll-btn:hover{transform:translateY(-2px);border-color:var(--ac)}
/* mobile */
@media(max-width:750px){.sidebar{display:none}.msg-wrap{padding:0 12px}.input-area{padding:14px 14px 18px}.empty h1{font-size:20px}.chat-header{padding:12px 16px}.scroll-btn{right:14px;bottom:100px}}
</style>
</head>
<body>
<div class="sidebar">
<div class="side-header"><span class="logo"><span class="dot live"></span>Hermes Chat</span><button class="btn-new" onclick="newSession()">+ New</button></div>
<div class="sess-list" id="sess-list"></div>
<div class="side-footer"><div class="info"><b id="info-model">loading</b><br><span id="info-backend">llama.cpp</span></div></div>
</div>
<div class="main">
<div class="chat-header">
<div class="badge" onclick="window.open('/launcher','_blank')" title="Switch model"><span class="dot"></span><span id="header-model">local</span><span style="color:var(--dm);font-size:10px;margin-left:2px">&#9662;</span></div>
<span class="meta" id="msg-count"></span>
</div>
<div class="messages" id="messages"></div>
<div class="status"><span id="status-text">Ready</span></div>
<div class="input-area"><div class="input-row">
<textarea id="user-input" placeholder="Message Hermes... (Enter to send)" rows="1"></textarea>
<button class="btn-send" id="send-btn" onclick="send()">Send</button>
<button class="btn-send btn-stop" id="stop-btn" onclick="stop()">Stop</button>
</div></div>
</div>
<button class="scroll-btn" id="scroll-btn" title="Scroll to bottom">↓</button>
<script>
var SESS=[],ACT=null,MSGS=[],AB=null,LOADING=false;

async function api(url,opts){
 try{var r=await fetch(url,opts);if(!r.ok)throw new Error((await r.json()).detail||await r.text());return await r.json()}
 catch(e){st(e.message,1);return null}
}
function st(t,err){var e=document.getElementById('status-text');e.innerHTML=t;e.style.color=err?'var(--rd)':''}
var scrollBtn=document.getElementById('scroll-btn');
document.getElementById('messages').addEventListener('scroll',function(){var m=this,d=m.scrollHeight-m.scrollTop-m.clientHeight;scrollBtn.classList.toggle('visible',d>200)});

function scrollToBottom(){var m=document.getElementById('messages');m.scrollTop=m.scrollHeight;scrollBtn.classList.remove('visible')}

// sessions
async function loadSessions(){
 var j=await api('/api/chat/sessions');if(!j)return;
 SESS=j.sessions||[];renderSessList();
 if(!ACT&&SESS.length){var s=SESS[SESS.length-1];ACT=s.id;loadHistory()}
}
function renderSessList(){
 var el=document.getElementById('sess-list');
 if(!SESS.length){el.innerHTML='<div style="padding:20px 14px;color:var(--dm);font-size:11px;text-align:center">No conversations yet</div>';return}
 var h=SESS.map(function(s){
  var cls=s.id===ACT?'active':'',title=s.title||'Chat',preview=s.last_message||'';
  return'<div class="sess-item '+cls+'" onclick="openSession(\''+s.id+'\')"><div class="icon">'+title.charAt(0).toUpperCase()+'</div><div class="title">'+esc(title)+'</div><button class="del-btn" onclick="event.stopPropagation();delSession(\''+s.id+'\')">×</button></div>';
 }).join('');
 el.innerHTML=h;
}
function esc(t){var d=document.createElement('div');d.textContent=t||'';return d.innerHTML}
async function loadHistory(){
 if(!ACT){renderMessages();return}
 var j=await api('/api/chat/history?session='+ACT);
 MSGS=j?j.messages||[]:[];renderMessages();
}
function openSession(id){ACT=id;loadHistory();renderSessList()}
function newSession(){ACT=null;MSGS=[];renderMessages();renderSessList();document.getElementById('user-input').focus()}
async function delSession(id){await api('/api/chat/sessions/'+id,{method:'DELETE'});SESS=SESS.filter(function(s){return s.id!==id});if(ACT===id){ACT=SESS.length?SESS[SESS.length-1].id:null;MSGS=[]};renderSessList();loadHistory()}

// messages
function renderMessages(){
 var el=document.getElementById('messages'),count=document.getElementById('msg-count');
 if(!MSGS.length){el.innerHTML='<div class="empty"><h1>Hermes Chat</h1><p>Powered by local AI. Ask anything — answers run entirely on your machine.</p><div class="hints"><span onclick="quickSend(\'Explain quantum computing in simple terms\')">Explain quantum computing</span><span onclick="quickSend(\'Write a Python function to sort a list\')">Write Python code</span><span onclick="quickSend(\'What are the best practices for git?\')">Git best practices</span></div></div>';count.textContent='';return}
 count.textContent=MSGS.length+' messages';
 var h='';
 for(var i=0;i<MSGS.length;i++){
  var m=MSGS[i];if(!m.content)continue;
  var role=m.role==='user'?'user':'assistant',avatar=role==='user'?'U':'H';
  var body=role==='user'?esc(m.content):md2html(m.content);
  h+='<div class="msg-wrap"><div class="msg '+role+'"><div class="avatar">'+avatar+'</div><div class="msg-body">'+body+'</div></div><div class="msg-actions"><button onclick="copyMsg('+i+',this)">Copy</button><button onclick="regen('+i+')">Regen</button></div></div>';
 }
 el.innerHTML=h;scrollToBottom();
}

// markdown
function md2html(s){
 if(!s)return'';
 s=s.replace(/```(\w*)\n([\s\S]*?)```/g,function(_,lang,code){
  var lt=lang||'text';
  return'<pre data-lang="'+lt+'"><code>'+code.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</code></pre>';
 });
 s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
 s=s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');s=s.replace(/\*(.+?)\*/g,'<i>$1</i>');
 s=s.replace(/^### (.+)$/gm,'<h3>$1</h3>');s=s.replace(/^## (.+)$/gm,'<h2>$1</h2>');s=s.replace(/^# (.+)$/gm,'<h1>$1</h1>');
 s=s.replace(/^>\s?(.+)$/gm,'<blockquote>$1</blockquote>');
 s=s.replace(/^- (.+)$/gm,'<li>$1</li>');s=s.replace(/(<li>[\s\S]*?<\/li>)/g,'<ul>$1</ul>');
 s=s.replace(/^---$/gm,'<hr>');s=s.replace(/\n/g,'<br>');
 return s;
}

// send
async function send(){
 if(LOADING)return;
 var inp=document.getElementById('user-input'),sendBtn=document.getElementById('send-btn'),stopBtn=document.getElementById('stop-btn');
 var txt=inp.value.trim();if(!txt)return;
 inp.value='';inp.style.height='auto';LOADING=true;
 sendBtn.style.display='none';stopBtn.style.display='inline-block';
 st('<span class="spinner"></span>Thinking...');
 
 MSGS.push({role:'user',content:txt});renderMessages();
 var shimmerDiv=document.createElement('div');shimmerDiv.className='shimmer-wrap';
 shimmerDiv.innerHTML='<div class="msg"><div class="avatar">H</div><div style="flex:1"><div class="shimmer h1"></div><div class="shimmer h2"></div><div class="shimmer h3"></div><div class="shimmer h4"></div></div></div>';
 document.getElementById('messages').appendChild(shimmerDiv);scrollToBottom();
 
 var t0=Date.now();
 try{AB=new AbortController();
  var r=await fetch('/api/chat/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:txt,session:ACT||undefined}),signal:AB.signal});
  var j=await r.json();
  shimmerDiv.remove();
  if(j.ok){if(!ACT&&j.session_id){ACT=j.session_id;renderSessList()}if(j.message){MSGS.push(j.message)}renderMessages();loadSessions();
   var tok=j.message.content.length,ms=Date.now()-t0;
   st('<span class="tok">'+Math.round(tok/(ms/1000)||0)+' tok/s</span> · '+ms+'ms');
  }else{st('Error: '+(j.error||'unknown'),1)}
 }catch(e){shimmerDiv.remove();if(e.name!=='AbortError'){st(e.message,1)}}
 sendBtn.style.display='inline-block';stopBtn.style.display='none';LOADING=false;inp.focus();AB=null;
}

function stop(){if(AB){AB.abort();AB=null;st('Stopped');document.getElementById('send-btn').style.display='inline-block';document.getElementById('stop-btn').style.display='none';LOADING=false}}
function quickSend(t){document.getElementById('user-input').value=t;send()}
function copyMsg(i,btn){var m=MSGS[i];if(m){navigator.clipboard.writeText(m.content).then(function(){btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy'},1500)})}}
function regen(i){MSGS=MSGS.slice(0,i);renderMessages();document.getElementById('user-input').focus()}

// keyboard
document.getElementById('user-input').addEventListener('keydown',function(e){
 if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}
 if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();send()}
});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&AB)stop()});
document.getElementById('user-input').addEventListener('input',function(){this.style.height='auto';this.style.height=Math.min(this.scrollHeight,200)+'px'});

// model info
function updateModelInfo(){
 document.getElementById('info-backend').textContent='llama.cpp · :8080';
 fetch('http://127.0.0.1:8080/v1/models').then(function(r){return r.json()}).then(function(j){
  var m=j.data||j.models||[];
  if(m.length){var name=m[0].id||'local';document.getElementById('info-model').textContent=name;document.getElementById('header-model').textContent=name}
 }).catch(function(){document.getElementById('info-model').textContent='offline'});
}

// init
loadSessions();updateModelInfo();setInterval(updateModelInfo,20000);
</script>
</body>
</html>'''
