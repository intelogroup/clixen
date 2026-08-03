function _hostLabel(url){
  try{ return new URL(url).hostname.replace(/^www\./,''); }catch{ return url.slice(0,40); }
}

// ── sessions ─────────────────────────────────────
function loadSessions() { try{return JSON.parse(localStorage.getItem(SESSIONS_KEY))||[];}catch{return[];} }
function saveSessions(s) { localStorage.setItem(SESSIONS_KEY, JSON.stringify(s)); }
function getActiveId()   { return localStorage.getItem(ACTIVE_KEY); }
function setActiveId(id) { localStorage.setItem(ACTIVE_KEY, id); }

function createSession(name) {
  const s={id:uuid(),name:name||'New chat',createdAt:Date.now()};
  const ss=loadSessions(); ss.unshift(s); saveSessions(ss); return s;
}
function renameSession(id, name) { saveSessions(loadSessions().map(s=>s.id===id?{...s,name}:s)); }
function deleteSession(id) {
  const ss=loadSessions().filter(s=>s.id!==id); saveSessions(ss);
  if(getActiveId()===id){
    if(ss.length>0){setActiveId(ss[0].id);}
    else{const s=createSession('New chat');setActiveId(s.id);}
  }
}
function relativeDate(ts) {
  const d=new Date(ts),now=new Date(),diff=now-d,day=86400000;
  if(diff<60000) return 'just now';
  if(diff<3600000) return Math.floor(diff/60000)+'m ago';
  if(diff<day) return Math.floor(diff/3600000)+'h ago';
  if(diff<2*day) return 'yesterday';
  return d.toLocaleDateString([],{month:'short',day:'numeric'});
}

function renderSidebar() {
  const sessions=loadSessions(), activeId=getActiveId();
  const container=document.getElementById('sessions');
  container.innerHTML='';
  sessions.forEach(s=>{
    const div=document.createElement('div');
    div.className='session-item'+(s.id===activeId?' active':'');
    div.dataset.id=s.id;
    const icon=`<svg class="sess-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`;
    div.innerHTML=`${icon}<div class="sess-body"><div class="sess-name" title="${escHtml(s.name)}">${escHtml(s.name)}</div><div class="sess-date">${relativeDate(s.createdAt)}</div></div>
      <div class="sess-actions">
        <button class="sess-action-btn rename" title="Rename"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4z"/></svg></button>
        <button class="sess-action-btn delete" title="Delete"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg></button>
      </div>`;
    div.querySelector('.sess-body').addEventListener('click',()=>switchSession(s.id));
    div.querySelector('.sess-icon').addEventListener('click',()=>switchSession(s.id));
    div.querySelector('.rename').addEventListener('click',e=>{e.stopPropagation();startRename(s.id,div);});
    div.querySelector('.delete').addEventListener('click',e=>{e.stopPropagation();confirmDelete(s.id);});
    container.appendChild(div);
  });
}

function startRename(id, div) {
  const nameEl=div.querySelector('.sess-name');
  const sess=loadSessions().find(s=>s.id===id);
  nameEl.contentEditable='true'; nameEl.focus();
  document.execCommand('selectAll',false,null);
  const onKey=e=>{
    if(e.key==='Enter'){e.preventDefault();nameEl.blur();}
    if(e.key==='Escape'){nameEl.textContent=escHtml(sess.name);nameEl.blur();}
  };
  nameEl.addEventListener('keydown',onKey);
  nameEl.addEventListener('blur',()=>{
    nameEl.removeEventListener('keydown',onKey);
    nameEl.contentEditable='false';
    const n=nameEl.textContent.trim()||sess.name;
    renameSession(id,n); renderSidebar();
  },{once:true});
}

async function confirmDelete(id) {
  try {
    await fetch(`/history/${encodeURIComponent(id)}`, {method:'DELETE'});
  } catch(_) {}
  deleteSession(id);
  document.getElementById('messages').innerHTML='';
  renderSidebar(); checkWelcome();
  loadHistory(getActiveId());
}

async function switchSession(id) {
  setActiveId(id);
  document.getElementById('messages').innerHTML='';
  renderSidebar(); checkWelcome();
  await loadHistory(id);
}

async function loadHistory(chatId) {
  if(!chatId) return;
  try {
    const res=await fetch(`/history/${encodeURIComponent(chatId)}`);
    const turns=await safeJsonResponse(res);
    document.getElementById('messages').innerHTML='';
    for(const t of turns) addMessage(t.role==='user'?'user':'bot', t.content, '', true);
    if(turns.length) checkWelcome();
    scrollBottom();
    refreshCtxUsage(chatId);
  } catch(_){}
}

function ensureActiveSession() {
  const ss=loadSessions(), id=getActiveId();
  if(ss.find(s=>s.id===id)) return id;
  if(ss.length>0){setActiveId(ss[0].id);return ss[0].id;}
  const s=createSession('New chat'); setActiveId(s.id); renderSidebar(); return s.id;
}

// ── welcome ───────────────────────────────────────
function checkWelcome() {
  const msgs=document.getElementById('messages');
  document.getElementById('welcome').classList.toggle('hidden', msgs.children.length>0);
}
function usePrompt(text) {
  const i=document.getElementById('input'); i.value=text; i.focus(); autoResize();
}

// ── messages ──────────────────────────────────────
function addMessage(role, content, metaText, isHistory) {
  const row=document.createElement('div'); row.className='msg-row '+role;
  if(content) row.dataset.msgText=content;
  // sender label
  const senderLabel=document.createElement('div'); senderLabel.className='msg-sender';
  senderLabel.textContent=role==='user'?'You':'Clixen';
  row.appendChild(senderLabel);
  // bubble
  const bubble=document.createElement('div'); bubble.className='msg-bubble';
  if(role==='user') bubble.textContent=content;
  else renderMarkdown(bubble,content);
  row.appendChild(bubble);
  // meta
  const metaRow=document.createElement('div'); metaRow.className='msg-meta';
  const timeSpan=document.createElement('span');
  timeSpan.textContent=metaText||(isHistory?'':'');
  metaRow.appendChild(timeSpan);
  let modelSpan=null;
  if(role==='bot'){
    modelSpan=document.createElement('span'); modelSpan.className='msg-model';
    metaRow.appendChild(modelSpan);
  }
  row.appendChild(metaRow);
  // action pill strip
  const actions=document.createElement('div'); actions.className='msg-actions';
  // copy — all messages
  const copyBtn=document.createElement('button'); copyBtn.className='msg-action-btn';
  copyBtn.innerHTML=`<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy`;
  copyBtn.addEventListener('click',()=>copyText(bubble.textContent||bubble.innerText,copyBtn));
  actions.appendChild(copyBtn);
  if(role==='bot'){
    // play/TTS
    const speakBtn=document.createElement('button'); speakBtn.className='msg-action-btn';
    speakBtn.title='Play audio';
    _setSpeakBtnState(speakBtn,'play');
    let _speakAbort=null;
    speakBtn.addEventListener('click',async()=>{
      if(speakBtn.dataset.playing){
        if(_speakAbort){_speakAbort.abort();_speakAbort=null;}
        stopAudioPlayback();
        speakBtn.dataset.playing='';
        _setSpeakBtnState(speakBtn,'play');
        speakBtn.disabled=false;
        return;
      }
      const text=(bubble.textContent||bubble.innerText).trim();
      if(!text) return;
      speakBtn.disabled=true;
      _setSpeakBtnState(speakBtn,'load');
      _speakAbort=new AbortController();
      try{
        const resp=await fetch('/tts/speak/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:_speakAbort.signal});
        if(!resp.ok) throw new Error('TTS failed');
        const reader=resp.body.getReader();
        const dec=new TextDecoder();
        let sseBuf='',firstChunk=true;
        speakBtn.dataset.playing='1';
        while(true){
          const {done,value}=await reader.read();
          if(done) break;
          sseBuf+=dec.decode(value,{stream:true});
          const parts=sseBuf.split('\n');
          sseBuf=parts.pop();
          for(const line of parts){
            if(!line.startsWith('data: ')) continue;
            const evt=JSON.parse(line.slice(6));
            if(evt.type==='audio'){
              if(firstChunk){firstChunk=false;_setSpeakBtnState(speakBtn,'stop');speakBtn.disabled=false;}
              await playAudioChunk(evt.data);
            } else if(evt.type==='done'){
              const remaining=Math.max(0,(_nextPlayAt||0)-((_playCtx&&_playCtx.currentTime)||0));
              setTimeout(()=>{if(speakBtn.dataset.playing){speakBtn.dataset.playing='';_setSpeakBtnState(speakBtn,'play');}},remaining*1000+300);
            }
          }
        }
      }catch(e){
        if(e.name!=='AbortError') console.warn('speak error',e);
        _setSpeakBtnState(speakBtn,'play');
        speakBtn.disabled=false;
        speakBtn.dataset.playing='';
      }
    });
    actions.appendChild(speakBtn);
    // regenerate
    const regenBtn=document.createElement('button'); regenBtn.className='msg-action-btn regen-btn';
    regenBtn.title='Regenerate response';
    regenBtn.innerHTML=`<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg> Regen`;
    regenBtn.addEventListener('click',()=>{
      if(streaming) return;
      const allRows=Array.from(document.getElementById('messages').querySelectorAll('.msg-row'));
      const myIdx=allRows.indexOf(row);
      let prevText=null;
      for(let i=myIdx-1;i>=0;i--){if(allRows[i].classList.contains('user')){prevText=allRows[i].dataset.msgText||'';break;}}
      if(!prevText) return;
      inputEl.value=prevText; autoResize(); send();
    });
    actions.appendChild(regenBtn);
  } else {
    // edit — user messages
    const editBtn=document.createElement('button'); editBtn.className='msg-action-btn edit-btn';
    editBtn.title='Edit and resend';
    editBtn.innerHTML=`<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4z"/></svg> Edit`;
    editBtn.addEventListener('click',()=>{
      const text=row.dataset.msgText||bubble.textContent||'';
      inputEl.value=text; autoResize(); inputEl.focus();
      inputEl.setSelectionRange(text.length,text.length);
    });
    actions.appendChild(editBtn);
  }
  row.appendChild(actions);
  document.getElementById('messages').appendChild(row);
  checkWelcome();
  return {row, bubble, meta:timeSpan, modelSpan};
}

const _typingWords = [
  'thinking','computing','hallucinating','plotting','scheming',
  'cooking','searching','calibrating','pondering','confabulating',
  'theorizing','manifesting','spellcasting','fumbling','cogitating',
  'deliberating','synthesizing','fistulating','vibing','processing',
];
let _typingWordTimer = null;

function showTyping() {
  if (_typingWordTimer) { clearInterval(_typingWordTimer); _typingWordTimer = null; }
  const w = document.createElement('div');
  w.className = 'typing-wrap';
  w.id = 'typing-indicator';
  const bubble = document.createElement('div');
  bubble.className = 'typing-bubble';
  for (let i = 0; i < 3; i++) {
    const d = document.createElement('div');
    d.className = 'dot';
    bubble.appendChild(d);
  }
  const wordEl = document.createElement('span');
  wordEl.className = 'typing-word';
  wordEl.id = 'typing-word';
  wordEl.textContent = 'thinking';
  bubble.appendChild(wordEl);
  w.appendChild(bubble);
  document.getElementById('messages').appendChild(w);
  scrollBottom();
  let _used = [];
  function _nextWord() {
    if (_used.length >= _typingWords.length) _used = [];
    const remaining = _typingWords.filter(v => !_used.includes(v));
    const word = remaining[Math.floor(Math.random() * remaining.length)];
    _used.push(word);
    const el = document.getElementById('typing-word');
    if (!el) return;
    el.style.animation = 'none';
    void el.offsetWidth;
    el.style.animation = '';
    el.textContent = word;
  }
  _typingWordTimer = setInterval(_nextWord, 2000);
}
function removeTyping() {
  if (_typingWordTimer) { clearInterval(_typingWordTimer); _typingWordTimer = null; }
  const e = document.getElementById('typing-indicator');
  if (e) e.remove();
}

// ── scroll ────────────────────────────────────────
const msgEl=document.getElementById('messages'), fab=document.getElementById('scroll-fab');
msgEl.addEventListener('scroll',()=>{
  const atBot=msgEl.scrollHeight-msgEl.scrollTop-msgEl.clientHeight<80;
  fab.classList.toggle('visible',!atBot);
});
function scrollBottom() { msgEl.scrollTop=msgEl.scrollHeight; }

// ── streaming ─────────────────────────────────────
const inputEl=document.getElementById('input');
const sendBtn=document.getElementById('send-btn');
const stopBtn=document.getElementById('stop-btn');
const statusEl=document.getElementById('status');
const modelBadge=document.getElementById('model-badge');
const workspaceBtn=document.getElementById('workspace-btn');
const workspaceModal=document.getElementById('workspace-modal');
let streaming=false, activeES=null, _streamTimeout=null, _activeStreamChatId='';

// ── model picker ──────────────────────────────────
(function initModeUI() {
  // main model picker
  window._selectedModel = '';
  const btn = document.getElementById('model-picker-btn');
  const list = document.getElementById('model-picker-list');
  if(btn && list) {
    btn.addEventListener('click', e => { e.stopPropagation(); list.classList.toggle('open'); });
    document.addEventListener('click', () => list.classList.remove('open'));
    list.querySelectorAll('.mpopt').forEach(opt => {
      opt.addEventListener('click', e => {
        e.stopPropagation();
        window._selectedModel = opt.dataset.value;
        const lbl = document.getElementById('mpbtn-label');
        if(lbl) lbl.textContent = opt.dataset.value || 'Auto (Router)';
        const dot = document.getElementById('mpbtn-dot');
        if(dot) {
          const isAuto = !opt.dataset.value;
          dot.textContent = isAuto ? '—' : (opt.querySelector('.mpdot')?.textContent || '○');
          dot.className = 'mpdot' + (opt.querySelector('.mpdot')?.classList.contains('hot') ? ' hot' : '');
        }
        list.querySelectorAll('.mpopt').forEach(o => o.classList.toggle('active', o === opt));
        list.classList.remove('open');
      });
    });
  }
  // ide model picker
  window._ideModelOverride = '';
  const ideBtn = document.getElementById('ide-model-picker-btn');
  const ideList = document.getElementById('ide-model-picker-list');
  if(ideBtn && ideList) {
    ideBtn.addEventListener('click', e => { e.stopPropagation(); ideList.classList.toggle('open'); });
    document.addEventListener('click', () => ideList.classList.remove('open'));
    ideList.querySelectorAll('.ide-mpopt').forEach(opt => {
      opt.addEventListener('click', e => {
        e.stopPropagation();
        window._ideModelOverride = opt.dataset.value;
        const lbl = document.getElementById('ide-mpbtn-label');
        if(lbl) lbl.textContent = opt.dataset.value || 'Auto';
        const dot = document.getElementById('ide-mpbtn-dot');
        if(dot) {
          const isAuto = !opt.dataset.value;
          dot.textContent = isAuto ? '—' : (opt.querySelector('.mpdot')?.textContent || '○');
          dot.className = 'mpdot' + (opt.querySelector('.mpdot')?.classList.contains('hot') ? ' hot' : '');
        }
        ideList.querySelectorAll('.ide-mpopt').forEach(o => o.classList.toggle('active', o === opt));
        ideList.classList.remove('open');
      });
    });
  }
})();

// ── hot-model indicators ──────────────────────────
function refreshHotModels() {
  fetchJson('/models/hot').then(({hot})=>{
    // custom model picker dots
    document.querySelectorAll('#model-picker-list .mpopt').forEach(opt => {
      if(!opt.dataset.value) return; // skip Auto
      const isHot = hot.includes(opt.dataset.value);
      const dot = opt.querySelector('.mpdot');
      if(dot) { dot.textContent = isHot ? '●' : '○'; dot.classList.toggle('hot', isHot); }
    });
    // picker button dot is updated per-option in the list above
    // ide model picker dots
    document.querySelectorAll('#ide-model-picker-list .ide-mpopt').forEach(opt => {
      if(!opt.dataset.value) return; // skip Auto
      const isHot = hot.includes(opt.dataset.value);
      const dot = opt.querySelector('.mpdot');
      if(dot) { dot.textContent = isHot ? '●' : '○'; dot.classList.toggle('hot', isHot); }
    });
    // ide picker button dot
    const ideBtnDot = document.getElementById('ide-mpbtn-dot');
    if(ideBtnDot && window._ideModelOverride) {
      const isHot = hot.includes(window._ideModelOverride);
      ideBtnDot.textContent = isHot ? '●' : '○';
      ideBtnDot.classList.toggle('hot', isHot);
    }
  }).catch(()=>{});
}
refreshHotModels();
setInterval(refreshHotModels, 12000);

// ── context-window usage meter ────────────────────
function refreshCtxUsage(chatId, model){
  if(!chatId) return;
  const params = new URLSearchParams({ chat_id: chatId });
  if(model) params.set('model', model);
  fetchJson(`/chat/context-usage?${params}`).then(({used, limit, pct})=>{
    const fill = document.getElementById('ctx-usage-fill');
    const label = document.getElementById('ctx-usage-label');
    if(!fill || !label) return;
    fill.style.width = pct + '%';
    fill.classList.toggle('warn', pct >= 70 && pct < 90);
    fill.classList.toggle('danger', pct >= 90);
    label.textContent = pct + '%';
  }).catch(()=>{});
}

// ── Ollama health ─────────────────────────────────────────────────────────
const ollamaDot   = document.getElementById("ollama-dot");
const ollamaLabel = document.getElementById("ollama-label");
const ollamaBtn   = document.getElementById("ollama-health");
let _ollamaDown = false;

function refreshOllamaHealth() {
  if(!ollamaDot || !ollamaLabel || !ollamaBtn) return;
  ollamaDot.className = "checking";
  fetchJson("/health/ollama").then(({status})=>{
    _ollamaDown = status !== "ok";
    ollamaDot.className = _ollamaDown ? "down" : "";
    ollamaLabel.textContent = _ollamaDown ? "Start Ollama" : "Ollama";
    ollamaBtn.title = _ollamaDown ? "Ollama is not running - click to start" : "Ollama is running";
  }).catch(()=>{
    _ollamaDown = true;
    ollamaDot.className = "down";
    ollamaLabel.textContent = "Start Ollama";
  });
}
if (ollamaBtn) ollamaBtn.addEventListener("click", ()=>{
  if(!_ollamaDown) return;
  ollamaLabel.textContent = "Starting...";
  ollamaDot.className = "checking";
  fetch("/health/ollama/restart",{method:"POST"}).then(()=>{
    setTimeout(refreshOllamaHealth, 5000);
  });
});
refreshOllamaHealth();
setInterval(refreshOllamaHealth, 15000);

// ── Notifications ────────────────────────────────────────────────────────
const notifBtn   = document.getElementById("notif-btn");
const notifCount = document.getElementById("notif-count");
const notifPanel = document.getElementById("notif-panel");
const notifList  = document.getElementById("notif-list");

function _timeAgo(ts){
  const diff = Math.floor(Date.now()/1000) - ts;
  if(diff < 60) return "just now";
  if(diff < 3600) return Math.floor(diff/60)+"m ago";
  if(diff < 86400) return Math.floor(diff/3600)+"h ago";
  return Math.floor(diff/86400)+"d ago";
}
function _buildNotifItem(n) {
  const item = document.createElement("div");
  item.className = "notif-item"+(n.read?"":" unread")+" notif-level-"+n.level;
  item.dataset.id = n.id;
  const msg = document.createElement("div");
  msg.className = "notif-item-msg";
  msg.textContent = n.message;
  const meta = document.createElement("div");
  meta.className = "notif-item-meta";
  const src = document.createElement("span"); src.textContent = n.source || "system";
  const ago = document.createElement("span"); ago.textContent = _timeAgo(n.ts);
  const dis = document.createElement("button"); dis.className="notif-dismiss"; dis.textContent="✕";
  dis.addEventListener("click", ()=>dismissNotif(n.id));
  meta.append(src, ago, dis);
  item.append(msg, meta);
  return item;
}
function renderNotifications(notifications, unread) {
  const c = unread > 0 ? (unread > 9 ? "9+" : String(unread)) : "";
  notifCount.textContent = c;
  notifCount.classList.toggle("visible", unread > 0);
  notifList.replaceChildren();
  if(!notifications.length) {
    const e = document.createElement("div"); e.id="notif-empty"; e.textContent="No notifications";
    notifList.appendChild(e); return;
  }
  notifications.forEach(n => notifList.appendChild(_buildNotifItem(n)));
  notifications.filter(n=>!n.read).forEach(n=>{
    fetch("/notifications/"+n.id+"/read",{method:"POST"}).catch(()=>{});
  });
}
function dismissNotif(id){fetch("/notifications/"+id,{method:"DELETE"}).then(()=>pollNotifications());}
function clearAllNotifs(){fetch("/notifications",{method:"DELETE"}).then(()=>pollNotifications());}
function pollNotifications(){
  fetchJson("/notifications").then(({notifications,unread})=>{
    renderNotifications(notifications,unread);
  }).catch(()=>{});
}
notifBtn.addEventListener("click", (e)=>{
  e.stopPropagation();
  const open = notifPanel.classList.toggle("open");
  if(open) pollNotifications();
});
document.addEventListener("click", (e)=>{
  if(!notifPanel.contains(e.target) && e.target!==notifBtn) notifPanel.classList.remove("open");
});
pollNotifications();
setInterval(pollNotifications, 10000);

function stopGeneration() {
  if(activeES){activeES.close();activeES=null;}
  if(_streamTimeout){clearTimeout(_streamTimeout);_streamTimeout=null;}
  if(_activeStreamChatId){
    fetch(`/chat/abort?chat_id=${encodeURIComponent(_activeStreamChatId)}`,{method:'POST'}).catch(()=>{});
    _activeStreamChatId='';
  }
  streaming=false; sendBtn.disabled=false;
  stopBtn.classList.remove('visible');
  statusEl.textContent='local model'; removeTyping();
}

function _searchStageLabel(stage) {
  const labels = {
    route: 'routing…',
    clarify: 'clarifying…',
    rewrite: 'rewriting query…',
    search: 'searching…',
    search_ok: 'sources found…',
    search_fail: 'fallback search…',
    rerank: 'reranking…',
    summarize: 'summarizing…',
    verify: 'verifying…',
    retry: 'retrying search…',
    finalize: 'finalizing…',
  };
  return labels[stage] || 'searching…';
}

async function send() {
  if(streaming){statusEl.textContent='Still responding…';return;}
  if(!workspaceState?.user?.authenticated){
    window.location.href='/login';
    return;
  }
  const text=inputEl.value.trim(); if(!text) return;
  inputEl.value=''; inputEl.style.height='auto';
  const baseId=ensureActiveSession();
  const chatId=baseId;
  _activeStreamChatId=chatId;
  const sess=loadSessions().find(s=>s.id===baseId);
  if(sess&&sess.name==='New chat'){
    renameSession(chatId,text.slice(0,38)+(text.length>38?'…':''));
    renderSidebar();
  }

  // Snapshot ready file attachments, then clear the pending queue
  const attachedFiles=pendingFiles.filter(f=>f.id).map(f=>({id:f.id,name:f.name,mime:f.mime,size:f.size}));
  const fileIds=attachedFiles.map(f=>f.id);
  pendingFiles.splice(0); renderAttachmentPreviews();

  const {row: userRow}=addMessage('user', text, ts());
  if(attachedFiles.length) addFileCards(userRow, attachedFiles);

  showTyping();
  sendBtn.disabled=true; stopBtn.classList.add('visible');
  streaming=true; statusEl.textContent='typing…';

  let bubble=null, meta=null, msgModelSpan=null, accumulated='', botRow=null;
  const fileIdsParam=fileIds.length?`&file_ids=${encodeURIComponent(fileIds.join(','))}`:'';
  const modelParam = window._selectedModel ? `&model=${encodeURIComponent(window._selectedModel)}` : '';
  const streamUrl = webSearchEnabled
    ? `/search/stream?message=${encodeURIComponent(text)}&thread_id=${encodeURIComponent(chatId)}${fileIdsParam}`
    : `/chat/stream?message=${encodeURIComponent(text)}&chat_id=${encodeURIComponent(chatId)}${fileIdsParam}${modelParam}`;
  const es=new EventSource(streamUrl);
  activeES=es;
  _streamTimeout=setTimeout(()=>{
    if(!streaming) return;
    es.close(); activeES=null;
    statusEl.textContent='Timed out — no response';
    if(!bubble){const c=addMessage('bot','');bubble=c.bubble;meta=c.meta;botRow=c.row;}
    if(!accumulated) bubble.textContent='(No response — agent timed out after 5m)';
    else { renderMarkdown(bubble,accumulated); if(botRow) botRow.dataset.msgText=accumulated; }
    if(meta&&!accumulated) meta.textContent=ts();
    sendBtn.disabled=false; stopBtn.classList.remove('visible');
    streaming=false; removeTyping();
  }, 300000);

  es.onmessage=e=>{
    if(!streaming) return;
    let data;
    try{data=JSON.parse(e.data);}catch{return;}
    if(data.done || data.type==='done'){
      es.close(); activeES=null;
      if(_streamTimeout){clearTimeout(_streamTimeout);_streamTimeout=null;}
      removeTyping();
      if(!bubble && data.reply){
        const c=addMessage('bot',''); bubble=c.bubble; meta=c.meta; msgModelSpan=c.modelSpan; botRow=c.row;
        const sepIdx = data.reply.indexOf('\n\nSources:\n');
        const answerPart = sepIdx !== -1 ? data.reply.slice(0, sepIdx) : data.reply;
        const sourcesPart = sepIdx !== -1 ? data.reply.slice(sepIdx + '\n\nSources:\n'.length) : '';
        const words = answerPart.split(' ');
        let i = 0;
        const iv = setInterval(() => {
          accumulated += (i > 0 ? ' ' : '') + words[i];
          renderMarkdown(bubble, accumulated);
          scrollBottom();
          i++;
          if(i >= words.length){
            clearInterval(iv);
            if(sourcesPart){
              const urls = sourcesPart.trim().split('\n')
                .filter(l => l.startsWith('- '))
                .map(l => l.slice(2).trim());
              const srcDiv = document.createElement('div');
              srcDiv.className = 'search-sources';
              urls.forEach(u => {
                const a = document.createElement('a');
                a.href = u;
                a.target = '_blank';
                a.rel = 'noopener noreferrer';
                a.textContent = _hostLabel(u);
                srcDiv.appendChild(a);
              });
              bubble.appendChild(srcDiv);
            }
            if(botRow) botRow.dataset.msgText = accumulated;
            scrollBottom();
          }
        }, 28);
      }
      if(!bubble){
        // Model returned empty response — show a visible fallback rather than silent nothing
        const c=addMessage('bot',''); bubble=c.bubble; meta=c.meta; botRow=c.row;
        bubble.textContent='(No response generated — the model may have timed out or encountered an internal error. Try rephrasing.)';
      }
      if(bubble){
        meta.textContent=ts();
        if(accumulated) { renderMarkdown(bubble,accumulated); if(botRow) botRow.dataset.msgText=accumulated; }
        if(data.model){
          if(modelBadge) modelBadge.textContent=data.model;
          if(msgModelSpan){ msgModelSpan.textContent=data.model; msgModelSpan.classList.add('visible'); }
        }
      }
      scrollBottom();
      streaming=false; sendBtn.disabled=false; stopBtn.classList.remove('visible');
      statusEl.textContent='local model';
      refreshCtxUsage(chatId, data.model);
      return;
    }
    if(data.type==='stage'){
      statusEl.textContent = _searchStageLabel(data.stage);
      const tw = document.getElementById('typing-word');
      if(tw) tw.textContent = _searchStageLabel(data.stage).replace('…','');
      return;
    }
    if(data.token){
      if(!bubble){
        removeTyping();
        const c=addMessage('bot',''); bubble=c.bubble; meta=c.meta; msgModelSpan=c.modelSpan; botRow=c.row;
      }
      accumulated+=data.token; renderMarkdown(bubble,accumulated); scrollBottom();
    }
    if(data.type==='token'){
      if(!bubble){
        removeTyping();
        const c=addMessage('bot',''); bubble=c.bubble; meta=c.meta; msgModelSpan=c.modelSpan; botRow=c.row;
      }
      accumulated+=data.text||''; renderMarkdown(bubble,accumulated); scrollBottom();
      return;
    }
    // Agent mode events
    if(data.type==='tool_call'){
      removeTyping();
      if(!bubble){const c=addMessage('bot',''); bubble=c.bubble; meta=c.meta; msgModelSpan=c.modelSpan; botRow=c.row;}
      const toolInfo=document.createElement('div');
      toolInfo.className='agent-tool-call';
      toolInfo.innerHTML=`<span class="agent-tool-name">${escHtml(data.data.name)}</span> <span class="agent-tool-args">${escHtml(JSON.stringify(data.data.args).slice(0,100))}</span>`;
      bubble.appendChild(toolInfo);
      scrollBottom();
      return;
    }
    if(data.type==='tool_result'){
      if(!bubble) return;
      const resultDiv=document.createElement('div');
      resultDiv.className='agent-tool-result';
      resultDiv.textContent=(data.data.content||'').slice(0,200);
      bubble.appendChild(resultDiv);
      scrollBottom();
      return;
    }
    if(data.type==='final'){
      es.close(); activeES=null;
      if(_streamTimeout){clearTimeout(_streamTimeout);_streamTimeout=null;}
      removeTyping();
      if(!bubble){
        const c=addMessage('bot',''); bubble=c.bubble; meta=c.meta; msgModelSpan=c.modelSpan; botRow=c.row;
      }
      accumulated=data.data.content||'';
      renderMarkdown(bubble,accumulated);
      if(botRow) botRow.dataset.msgText=accumulated;
      meta.textContent=ts();
      if(msgModelSpan){msgModelSpan.textContent='agent'; msgModelSpan.classList.add('visible');}
      scrollBottom();
      sendBtn.disabled=false; stopBtn.classList.remove('visible');
      streaming=false; statusEl.textContent='local model';
      return;
    }
    if(data.error){
      es.close(); activeES=null;
      if(_streamTimeout){clearTimeout(_streamTimeout);_streamTimeout=null;}
      removeTyping();
      if(!bubble){const c=addMessage('bot','');bubble=c.bubble;meta=c.meta;botRow=c.row;}
      bubble.textContent='⚠ '+data.error; meta.textContent=ts();
      sendBtn.disabled=false; stopBtn.classList.remove('visible');
      streaming=false; statusEl.textContent='local model';
    }
  };
  es.onerror=()=>{
    if(!streaming) return;
    es.close(); activeES=null;
    if(_streamTimeout){clearTimeout(_streamTimeout);_streamTimeout=null;}
    removeTyping();
    if(!bubble){const c=addMessage('bot','');bubble=c.bubble;meta=c.meta;botRow=c.row;}
    if(!accumulated) bubble.textContent='⚠ Connection error.';
    else { renderMarkdown(bubble,accumulated); if(botRow) botRow.dataset.msgText=accumulated; }
    if(meta) meta.textContent=ts();
    sendBtn.disabled=false; stopBtn.classList.remove('visible');
    streaming=false; statusEl.textContent='local model';
  };
}

function autoResize() {
  inputEl.style.height='auto';
  inputEl.style.height=Math.min(inputEl.scrollHeight,160)+'px';
}
sendBtn.addEventListener('click',send);
inputEl.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
inputEl.addEventListener('input',autoResize);
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='n'){
    e.preventDefault();
    const s=createSession('New chat'); setActiveId(s.id);
    document.getElementById('messages').innerHTML='';
    renderSidebar(); checkWelcome(); inputEl.focus();
  }
});
document.getElementById('new-chat-btn').addEventListener('click',()=>{
  const s=createSession('New chat'); setActiveId(s.id);
  document.getElementById('messages').innerHTML='';
  renderSidebar(); checkWelcome(); inputEl.focus();
});

workspaceBtn.addEventListener('click', openWorkspaceModal);
document.getElementById('workspace-close').addEventListener('click', closeWorkspaceModal);
workspaceModal.addEventListener('click', e => {
  if(e.target === workspaceModal) closeWorkspaceModal();
});
document.getElementById('sidebar-auth-btn').addEventListener('click', () => {
  if(workspaceState?.user?.authenticated) openWorkspaceModal();
  else window.location.href='/login';
});
document.getElementById('sidebar-logout-btn').addEventListener('click', async () => {
  try {
    await logoutWorkspace();
  } catch(err) {
    console.error('sidebar logout failed', err);
  }
});
document.getElementById('auth-banner-logout').addEventListener('click', async () => {
  try {
    await logoutWorkspace();
  } catch(err) {
    console.error('banner logout failed', err);
  }
});
document.getElementById('mock-login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const form = new FormData(e.currentTarget);
  const payload = Object.fromEntries(form.entries());
  const hasAccount = !!workspaceState?.auth?.has_account;
  const endpoint = hasAccount ? '/api/auth/login' : '/api/auth/register';
  const body = hasAccount
    ? {email: payload.email, password: payload.password}
    : payload;
  try {
    const data = await apiJson(endpoint, {method:'POST', body: JSON.stringify(body)});
    renderWorkspaceUI(data);
    if(data.user?.authenticated) closeWorkspaceModal();
  } catch(err) {
    console.error('auth failed', err);
    alert(err.message || 'Authentication failed');
  }
});
document.getElementById('mock-logout-btn').addEventListener('click', async () => {
  try {
    await logoutWorkspace();
  } catch(err) {
    console.error('logout failed', err);
  }
});
// ── init ─────────────────────────────────────────
ensureActiveSession();
renderSidebar(); checkWelcome();
refreshWorkspaceState();
setInterval(refreshAutomationRuns, 5000);
inputEl.focus();



// ── Deep Research Library ──────────────────────────
let activeReportContent = '';

async function loadResearchReports() {
  try {
    const list = document.getElementById('research-list');
    if (!list) return;
    list.innerHTML = 'Loading reports...';
    
    const data = await fetchJson('/api/research/reports');
    if (!data.reports.length) {
      list.innerHTML = '<div class="research-empty">No reports found. Generate one in chat with the deep_research tool.</div>';
      return;
    }
    
    list.innerHTML = data.reports.map(r => {
      const dateStr = new Date(r.modified * 1000).toLocaleDateString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
      const cleanName = r.filename.replace(/^research_/, '').replace(/_\d{8}_\d{6}\.md$/, '').replace(/_/g, ' ');
      return `
        <div class="research-item" data-filename="${escHtml(r.filename)}" onclick="readResearchReport('${escHtml(r.filename)}')">
          <div class="research-item-title">${escHtml(cleanName.charAt(0).toUpperCase() + cleanName.slice(1))}</div>
          <div class="research-item-meta">${escHtml(dateStr)} · ${escHtml((r.size/1024).toFixed(1))} KB</div>
        </div>
      `;
    }).join('');
  } catch(err) {
    console.error('loadResearchReports failed', err);
  }
}

async function readResearchReport(filename) {
  try {
    const docEl = document.getElementById('research-document');
    const placeholder = document.getElementById('research-placeholder');
    const headerEl = document.getElementById('research-content-header');
    if (!docEl || !placeholder || !headerEl) return;
    
    document.querySelectorAll('.research-item').forEach(el => {
      el.classList.toggle('active', el.getAttribute('data-filename') === filename);
    });
    
    placeholder.style.display = 'none';
    docEl.style.display = 'block';
    docEl.innerHTML = '<div class="research-loading">Loading report content...</div>';
    headerEl.style.display = 'none';
    
    const data = await fetchJson(`/api/research/reports/${encodeURIComponent(filename)}`);
    activeReportContent = data.content;
    
    // Set title and estimate reading time
    const titleEl = document.getElementById('research-doc-title');
    const readingTimeEl = document.getElementById('research-doc-reading-time');
    
    const cleanName = filename.replace(/^research_/, '').replace(/_\d{8}_\d{6}\.md$/, '').replace(/_/g, ' ');
    titleEl.textContent = cleanName.charAt(0).toUpperCase() + cleanName.slice(1);
    
    const wordCount = data.content.split(/\s+/).length;
    const readingTime = Math.max(1, Math.round(wordCount / 200));
    readingTimeEl.textContent = `${readingTime} min read`;
    
    headerEl.style.display = 'flex';
    renderMarkdown(docEl, data.content);
  } catch(err) {
    console.error('readResearchReport failed', err);
  }
}

function filterResearchReports() {
  const query = document.getElementById('research-search').value.toLowerCase();
  const items = document.querySelectorAll('.research-item');
  items.forEach(item => {
    const title = item.querySelector('.research-item-title').textContent.toLowerCase();
    item.style.display = title.includes(query) ? 'block' : 'none';
  });
}

function copyActiveResearchReport() {
  if (!activeReportContent) return;
  const btn = document.getElementById('research-copy-btn');
  navigator.clipboard.writeText(activeReportContent).then(() => {
    const originalText = btn.innerHTML;
    btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="margin-right:4px"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
    setTimeout(() => { btn.innerHTML = originalText; }, 2000);
  }).catch(err => {
    console.error('Copy failed', err);
  });
}

// ── System Cookbook serving management ──────────────
async function loadCookbookSystemProfile() {
  try {
    const data = await fetchJson('/api/system/profile');
    document.getElementById('cb-cpu').textContent = data.cpu || '—';
    document.getElementById('cb-ram').textContent = data.ram || '—';
    document.getElementById('cb-gpu').textContent = data.gpu || '—';
    
    const tierEl = document.getElementById('cb-tier');
    if (tierEl) {
      tierEl.textContent = data.tier || '—';
      tierEl.className = 'cb-spec-value badge';
      if (data.tier && data.tier.toLowerCase().includes('optimal')) {
        tierEl.classList.add('badge-success');
      } else if (data.tier && data.tier.toLowerCase().includes('minimum')) {
        tierEl.classList.add('badge-warning');
      } else {
        tierEl.classList.add('badge-danger');
      }
    }
    
    document.getElementById('cb-primary').textContent = data.primary || '—';
    
    const rows = document.querySelectorAll('.cb-model-row');
    const downloadedList = data.downloaded_models || [];
    rows.forEach(row => {
      const modelName = row.querySelector('.cb-model-info strong').textContent.trim();
      const downloadBtn = row.querySelector('.download-btn');
      const serveBtn = row.querySelector('.serve-btn');
      
      const isDownloaded = downloadedList.includes(modelName);
      const isRunning = data.active_sidecars && data.active_sidecars.includes(modelName);
      
      if (downloadBtn) {
        if (isDownloaded) {
          downloadBtn.textContent = 'Downloaded';
          downloadBtn.classList.add('downloaded');
          downloadBtn.disabled = true;
        } else {
          downloadBtn.textContent = 'Download';
          downloadBtn.classList.remove('downloaded');
          downloadBtn.disabled = false;
        }
      }
      
      if (serveBtn) {
        serveBtn.disabled = !isDownloaded;
        if (!isDownloaded) {
          serveBtn.title = 'Download model first to serve';
          serveBtn.classList.remove('active');
          serveBtn.textContent = 'Serve';
        } else {
          serveBtn.title = isRunning ? 'Stop llama.cpp sidecar' : 'Start llama.cpp sidecar';
          if (isRunning) {
            serveBtn.textContent = 'Stop Serving';
            serveBtn.classList.add('active');
          } else {
            serveBtn.textContent = 'Serve';
            serveBtn.classList.remove('active');
          }
        }
      }
    });
  } catch(err) {
    console.error('loadCookbookSystemProfile failed', err);
  }
}

async function downloadCookbookModel(modelName) {
  const btn = event.target;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Downloading...';
  try {
    await apiJson('/api/system/models/download', {
      method: 'POST',
      body: JSON.stringify({model_name: modelName})
    });
    btn.textContent = 'Download started';
    setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
  } catch(err) {
    console.error('downloadCookbookModel failed', err);
    btn.textContent = 'Download failed';
    setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
  }
}

async function toggleServeCookbookModel(modelName) {
  const btn = event.target;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Updating...';
  try {
    const res = await apiJson('/api/system/serve/toggle', {
      method: 'POST',
      body: JSON.stringify({model_name: modelName})
    });
    if (res.status === 'started') {
      btn.textContent = 'Stop Serving';
      btn.classList.add('active');
    } else {
      btn.textContent = 'Serve';
      btn.classList.remove('active');
    }
  } catch(err) {
    console.error('toggleServeCookbookModel failed', err);
    btn.textContent = 'Failed';
    setTimeout(() => { btn.textContent = original; }, 2000);
  } finally {
    btn.disabled = false;
    loadCookbookSystemProfile();
  }
}

const _openWorkspaceModalOrig = window.openWorkspaceModal;
window.openWorkspaceModal = function() {
  if (typeof _openWorkspaceModalOrig === 'function') _openWorkspaceModalOrig();
  loadCookbookSystemProfile();
};
