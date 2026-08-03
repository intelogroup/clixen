// ── voice recording + VAD (Silero via @ricky0123/vad-web) ────────────────────
const ARMED_TIMEOUT_MS = 30000; // cancel if no speech within 30s

const micBtn    = document.getElementById('mic-btn');
const vadBar    = document.getElementById('vad-bar');
const vadBarFill = document.getElementById('vad-bar-fill');
const pendingAudioEl = document.getElementById('pending-audio');
const pendingLabelEl = document.getElementById('pa-label');
const pendingPlayBtn = document.getElementById('pa-play');
const pendingCancelBtn = document.getElementById('pa-cancel');
const pendingSendBtn = document.getElementById('pa-send');

let _micState      = 'idle';  // idle | armed | speaking | processing
let _mediaRecorder = null;
let _micStream     = null;
let _chunks        = [];
let _speechSeen    = false;
let _armedTimer    = null;
let _myvad         = null;   // vad.MicVAD instance (lazy-initialized)
let _vadInitializing = false;

function setMicState(s) {
  _micState = s;
  if(!micBtn || !vadBar || !vadBarFill) return;
  micBtn.className = 'composer-btn' + (s !== 'idle' ? ' mic-' + s : '');
  const labels = {
    idle:       'Voice input (click to record)',
    armed:      'Listening for voice… click to cancel',
    speaking:   'Recording… click to stop',
    processing: 'Transcribing…',
  };
  micBtn.title = labels[s] || '';
  micBtn.disabled = (s === 'processing');
  if(s === 'idle' || s === 'processing') {
    vadBar.classList.remove('active');
    vadBarFill.style.width = '0%';
  }
}

function _startMediaRecorder(stream) {
  _chunks = [];
  let mime = 'audio/webm;codecs=opus';
  if (!MediaRecorder.isTypeSupported(mime)) mime = 'audio/webm';
  if (!MediaRecorder.isTypeSupported(mime)) mime = 'audio/mp4';
  _mediaRecorder = new MediaRecorder(stream, {mimeType: mime});
  _mediaRecorder.ondataavailable = e => { if(e.data.size > 0) _chunks.push(e.data); };
  _mediaRecorder.start(100);
}

function _stopMediaRecorder(clearHandler = false) {
  if(_mediaRecorder && _mediaRecorder.state !== 'inactive') {
    if(clearHandler) _mediaRecorder.ondataavailable = null;
    _mediaRecorder.stop();
  }
}

async function _initVAD() {
  if(_myvad) return _myvad;
  if(_vadInitializing) return null;
  _vadInitializing = true;
  try {
    _myvad = await vad.MicVAD.new({
      positiveSpeechThreshold: 0.5,
      negativeSpeechThreshold: 0.35,
      redemptionFrames: 10,   // ~320ms hangover at 32ms/frame
      minSpeechFrames: 5,     // discard segments < ~160ms (keyboard clicks, pops)
      preSpeechPadFrames: 10,
      workletURL: 'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.22/dist/vad.worklet.bundle.min.js',
      modelURL:   'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.22/dist/silero_vad_v5.onnx',
      ortConfig: ort => {
        ort.env.wasm.wasmPaths = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.20.1/dist/';
        ort.env.wasm.numThreads = 1; // avoid SharedArrayBuffer / COOP-COEP requirement
      },
      onSpeechStart: () => {
        if(_micState !== 'armed') return;
        _speechSeen = true;
        clearTimeout(_armedTimer);
        setMicState('speaking');
        vadBar.classList.add('active');
        // Restart MediaRecorder fresh so the blob starts with a proper init segment.
        // The pre-speech chunks already emitted are headerless and would corrupt the blob.
        _stopMediaRecorder(true);
        _chunks = [];
        _startMediaRecorder(_micStream);
      },

      onSpeechEnd: (/*Float32Array*/) => {
        // VAD auto-send path — use MediaRecorder blob
        if(_micState !== 'speaking') return;
        const mime = _mediaRecorder ? _mediaRecorder.mimeType : 'audio/webm';
        _stopMediaRecorder();
        _mediaRecorder = null;
        // Wait 20ms for the final ondataavailable event to fire before sealing the blob
        setTimeout(() => {
          if(!_speechSeen || !_chunks.length) { setMicState('idle'); return; }
          const blob = new Blob(_chunks, {type: mime});
          _chunks = [];
          setMicState('processing');
          _submitBlob(blob, mime);
        }, 20);
      },
      onVADMisfire: () => {
        // Too short — not real speech (keyboard tap, pop, etc.)
        _chunks = [];
        _speechSeen = false;
        if(_micState === 'speaking') {
          setMicState('armed');
          vadBar.classList.remove('active');
          vadBarFill.style.width = '0%';
        }
      },
    });
  } catch(e) {
    console.error('VAD init failed:', e);
    if (e && e.name === 'NotAllowedError') {
      setVoiceStatus('microphone permission denied');
    }
    _vadInitializing = false;
    return null;
  }
  _vadInitializing = false;
  return _myvad;
}

async function startRecording() {
  if(_micState !== 'idle') return;
  const vd = await _initVAD();
  if(!vd) {
    if (_micState !== 'idle') setMicState('idle');
    if (!voiceStatusText || voiceStatusText.textContent !== 'microphone permission denied') {
      alert('Voice input unavailable. Check microphone permission and network access for VAD assets.');
    }
    return;
  }

  _speechSeen = false; _chunks = [];
  _micStream = vd.stream;

  // Parallel MediaRecorder on the same stream for audio capture
  _startMediaRecorder(_micStream);

  setMicState('armed');
  _armedTimer = setTimeout(() => {
    if(_micState === 'armed') cancelRecording();
  }, ARMED_TIMEOUT_MS);

  vd.start();
}

// User clicked mic while speaking → hold audio afloat, don't auto-send
function stopRecordingManual() {
  if(_micState !== 'speaking') return;
  if(_myvad) _myvad.pause();
  const mime = _mediaRecorder ? _mediaRecorder.mimeType : 'audio/webm';
  clearTimeout(_armedTimer);
  _stopMediaRecorder();
  _mediaRecorder = null;
  setMicState('idle');
  vadBar.classList.remove('active');
  vadBarFill.style.width = '0%';
  // Wait 20ms for the final ondataavailable event before sealing the blob
  setTimeout(() => _holdPending(mime), 20);
}

let _pendingBlob = null;
let _pendingMime = null;
let _previewAudio = null;

function _holdPending(mime) {
  if(!_speechSeen || !_chunks.length) { _chunks = []; return; }
  _pendingMime = mime;
  _pendingBlob = new Blob(_chunks, {type: mime});
  _chunks = [];
  if (pendingLabelEl) pendingLabelEl.textContent = 'recording ready - preview, send or cancel';
  if (pendingAudioEl) pendingAudioEl.classList.add('visible');
}

function _clearPending() {
  _pendingBlob = null; _pendingMime = null;
  if(_previewAudio) { try{_previewAudio.pause();}catch(e){} _previewAudio=null; }
  if (pendingAudioEl) pendingAudioEl.classList.remove('visible');
  if (pendingPlayBtn) pendingPlayBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview`;
}


if (pendingCancelBtn) pendingCancelBtn.addEventListener('click', () => {
  _clearPending();
});

if (pendingPlayBtn) pendingPlayBtn.addEventListener('click', async () => {
  if(!_pendingBlob) return;
  const paPlay = pendingPlayBtn;
  if(_previewAudio && !_previewAudio.paused) {
    _previewAudio.pause();
    return;
  }
  try {
    const url = URL.createObjectURL(_pendingBlob);
    _previewAudio = new Audio(url);
    _previewAudio.onended = () => {
      _previewAudio = null;
      paPlay.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview`;
    };
    _previewAudio.onpause = () => {
      paPlay.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview`;
    };
    _previewAudio.onplay = () => {
      paPlay.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Stop`;
    };
    await _previewAudio.play();
  } catch(e) { console.warn('preview error', e); }
});

if (pendingSendBtn) pendingSendBtn.addEventListener('click', () => {
  if(!_pendingBlob) return;
  const blob = _pendingBlob;
  const mime = _pendingMime;
  _clearPending();
  _submitBlob(blob, mime);
});

function cancelRecording() {
  clearTimeout(_armedTimer);
  if(_myvad) _myvad.pause();
  _stopMediaRecorder(true);
  _mediaRecorder = null;
  _chunks = [];
  _speechSeen = false;
  vadBar.classList.remove('active');
  vadBarFill.style.width = '0%';
  setMicState('idle');
}

// ── audio playback (TTS chunks) ──────────────────
let _playCtx    = null;
let _nextPlayAt = 0;

function _getPlayCtx() {
  if(!_playCtx || _playCtx.state === 'closed')
    _playCtx = new AudioContext();
  return _playCtx;
}

async function playAudioChunk(b64wav) {
  const ctx = _getPlayCtx();
  try {
    const binary = atob(b64wav);
    const bytes  = new Uint8Array(binary.length);
    for(let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const buf   = await ctx.decodeAudioData(bytes.buffer.slice(0));
    const src   = ctx.createBufferSource();
    src.buffer  = buf;
    src.connect(ctx.destination);
    const start = Math.max(ctx.currentTime + 0.02, _nextPlayAt);
    src.start(start);
    _nextPlayAt = start + buf.duration;
  } catch(e) {
    console.warn('Audio chunk decode error:', e);
  }
}

function stopAudioPlayback() {
  if(_playCtx) { _playCtx.close().catch(()=>{}); _playCtx = null; }
  _nextPlayAt = 0;
}

const _SPEAK_ICONS = {
  play: ['M5 3 19 12 5 21 5 3', 'polygon', ' Play'],
  stop: null,
  load: ['M12 12 m-9 0 a9 9 0 1 0 18 0 a9 9 0 1 0-18 0', 'path', ' ...']
};
function _setSpeakBtnState(btn, state) {
  if (state === 'play') {
    btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/><\/svg> Play';
  } else if (state === 'stop') {
    btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/><\/svg> Stop';
  } else {
    btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><\/svg> ...';
  }
}

// ── TTS toggle ───────────────────────────────────
const ttsBtn = document.getElementById('tts-btn');
let ttsEnabled = false;
function setTtsEnabled(next) {
  ttsEnabled = !!next;
  if (!ttsBtn) return;
  ttsBtn.classList.toggle('on', ttsEnabled);
  ttsBtn.title = ttsEnabled ? 'TTS on - click to disable' : 'Toggle voice response (TTS)';
}
if (ttsBtn) ttsBtn.addEventListener('click', () => {
  setTtsEnabled(!ttsEnabled);
});

// ── Web search toggle ────────────────────────────
const webSearchBtn = document.getElementById('web-search-btn');
let webSearchEnabled = false;
if (webSearchBtn) webSearchBtn.addEventListener('click', () => {
  webSearchEnabled = !webSearchEnabled;
  webSearchBtn.classList.toggle('on', webSearchEnabled);
  webSearchBtn.title = webSearchEnabled
    ? 'Research mode on - every message searches the web (click to disable)'
    : 'Research mode - every message searches the web (stateless, no chat history)';
});

// ── voice status pill ────────────────────────────
const voiceStatusEl   = document.getElementById('voice-status');
const voiceStatusText = document.getElementById('voice-status-text');
function setVoiceStatus(msg) {
  if(!voiceStatusEl || !voiceStatusText) return;
  if(msg) { voiceStatusText.textContent = msg; voiceStatusEl.classList.add('visible'); }
  else     { voiceStatusEl.classList.remove('visible'); }
}

// ── SSE stream parser (async generator over fetch body) ──
async function* _parseSSE(response) {
  const reader  = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while(true) {
    const {done, value} = await reader.read();
    if(done) break;
    buf += decoder.decode(value, {stream: true});
    let i;
    while((i = buf.indexOf('\n\n')) !== -1) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 2);
      if(line.startsWith('data: ')) {
        try { yield JSON.parse(line.slice(6)); } catch(_) {}
      }
    }
  }
}

async function _finishRecording() {
  if(!_speechSeen || !_chunks.length) { setMicState('idle'); return; }
  const mime = _mediaRecorder ? _mediaRecorder.mimeType : 'audio/webm';
  const blob = new Blob(_chunks, {type: mime});
  _chunks = [];
  await _submitBlob(blob, mime);
}

async function _submitBlob(blob, mime) {
  setVoiceStatus('transcribing…');
  if(streaming) { setMicState('idle'); setVoiceStatus(null); return; }
  streaming = true;
  sendBtn.disabled = true;
  stopBtn.classList.add('visible');
  statusEl.textContent = 'voice…';

  const fd = new FormData();
  const ext = (mime || '').includes('mp4') ? '.mp4' : '.webm';
  fd.append('file', blob, 'voice' + ext);
  fd.append('chat_id', ensureActiveSession());
  fd.append('tts',        ttsEnabled ? '1' : '0');
  fd.append('web_search', webSearchEnabled ? '1' : '0');

  let bubble = null, meta = null, voiceModelSpan = null, accumulated = '';
  stopAudioPlayback();

  try {
    const res = await fetch('/voice/process', {method: 'POST', body: fd});
    if(!res.ok) throw new Error('HTTP ' + res.status);

    for await (const evt of _parseSSE(res)) {
      if(evt.type === 'transcript') {
        setVoiceStatus('thinking…');
        const sess = loadSessions().find(s => s.id === getActiveId());
        if(sess && sess.name === 'New chat') {
          renameSession(sess.id, evt.text.slice(0, 38) + (evt.text.length > 38 ? '…' : ''));
          renderSidebar();
        }
        addMessage('user', evt.text, ts());
        showTyping();

      } else if(evt.type === 'token') {
        if(!bubble) {
          removeTyping();
          const c = addMessage('bot', '');
          bubble = c.bubble; meta = c.meta; voiceModelSpan = c.modelSpan;
          setVoiceStatus(ttsEnabled ? 'speaking…' : 'streaming…');
        }
        accumulated += evt.text;
        renderMarkdown(bubble, accumulated);
        scrollBottom();

      } else if(evt.type === 'audio') {
        await playAudioChunk(evt.data);

      } else if(evt.type === 'done') {
        if(bubble) { renderMarkdown(bubble, accumulated); meta.textContent = ts(); }
        if(evt.model){
          modelBadge.textContent = evt.model;
          if(voiceModelSpan){ voiceModelSpan.textContent = evt.model; voiceModelSpan.classList.add('visible'); }
        }
        scrollBottom();

      } else if(evt.type === 'error') {
        removeTyping();
        if(!bubble) { const c = addMessage('bot',''); bubble=c.bubble; meta=c.meta; }
        bubble.textContent = '\u26a0 ' + evt.text;
        if(meta) meta.textContent = ts();
      }
    }
  } catch(err) {
    console.error('voice/process error:', err);
    removeTyping();
    if(bubble) renderMarkdown(bubble, accumulated || '\u26a0 Voice pipeline error.');
  }

  setVoiceStatus(null);
  setMicState('idle');
  sendBtn.disabled = false;
  stopBtn.classList.remove('visible');
  streaming = false;
  statusEl.textContent = 'local model';

  // Auto-restart mic after reply finishes (single-button hands-free flow)
  if(!ttsEnabled) setTtsEnabled(true);
  startRecording();
}

if (micBtn) micBtn.addEventListener('click', () => {
  if(_micState === 'idle')           startRecording();
  else if(_micState === 'armed')     cancelRecording();
  else if(_micState === 'speaking')  stopRecordingManual();
  // processing: ignore
});

// ── Voice SSE: display Brabble wake-word interactions in chat ──────────────
(function connectVoiceSSE() {
  const es = new EventSource('/voice/stream');
  es.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.type !== 'voice') return;
    const msgs = document.getElementById('messages');
    if (!msgs) return;
    const ts = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    // User bubble
    const userDiv = document.createElement('div');
    userDiv.className = 'msg-row user';
    userDiv.innerHTML = `<div class="msg-bubble"><div class="msg-text">${escapeHtml(data.query)}</div><div class="msg-meta">${ts}</div></div>`;
    msgs.appendChild(userDiv);
    // Bot bubble
    const botDiv = document.createElement('div');
    botDiv.className = 'msg-row bot';
    botDiv.innerHTML = `<div class="msg-bubble"><div class="msg-text">${escapeHtml(data.reply)}</div><div class="msg-meta">${ts} · voice</div></div>`;
    msgs.appendChild(botDiv);
    msgs.scrollTop = msgs.scrollHeight;
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.classList.add('hidden');
  };
})();

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── Voiceprint enrollment ──────────────────────────────────────────────────
const voiceBadge = document.getElementById('voice-badge');
const voiceModal = document.getElementById('voice-modal');
let _voiceRecorder = null, _voiceChunks = [];

function openVoiceModal() {
  voiceModal.style.display = 'flex';
  checkVoiceStatus();
}
function closeVoiceModal() {
  voiceModal.style.display = 'none';
}
if (voiceBadge) voiceBadge.addEventListener('click', openVoiceModal);

function checkVoiceStatus() {
  fetch('/voice/status').then(r => r.json()).then(data => {
    const statusEl = document.getElementById('voice-status-text');
    const enrollBtn = document.getElementById('voice-enroll-btn');
    if (data.enrolled) {
      voiceBadge.classList.add('enrolled');
      voiceBadge.title = 'Voiceprint enrolled — click to manage';
      if (statusEl) statusEl.textContent = 'Voiceprint enrolled';
      if (enrollBtn) enrollBtn.textContent = 'Re-enroll (record 5s)';
    } else {
      voiceBadge.classList.remove('enrolled');
      voiceBadge.title = 'Enroll your voice for speaker verification';
      if (statusEl) statusEl.textContent = 'No voiceprint — enroll to enable speaker verification';
      if (enrollBtn) enrollBtn.textContent = 'Enroll (record 5s)';
    }
  }).catch(() => {});
}

async function startVoiceEnroll() {
  const recDiv = document.getElementById('voice-recording');
  const resultDiv = document.getElementById('voice-result');
  const btn = document.getElementById('voice-enroll-btn');
  if (!navigator.mediaDevices) { alert('Mic not available'); return; }
  btn.disabled = true; recDiv.style.display = 'block'; resultDiv.style.display = 'none';
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    _voiceRecorder = new MediaRecorder(stream, {mimeType: 'audio/webm'});
    _voiceChunks = [];
    _voiceRecorder.ondataavailable = e => { if (e.data.size) _voiceChunks.push(e.data); };
    _voiceRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      recDiv.style.display = 'none';
      recDiv.textContent = 'Uploading...'; recDiv.style.display = 'block';
      const blob = new Blob(_voiceChunks, {type: 'audio/webm'});
      const form = new FormData(); form.append('file', blob, 'enroll.webm');
      try {
        const res = await fetch('/voice/enroll', {method:'POST', body:form});
        const data = await res.json();
        recDiv.style.display = 'none';
        if (data.ok) {
          voiceBadge.classList.add('enrolled');
          resultDiv.style.display = 'block';
          resultDiv.textContent = `Enrolled — ${data.dim}-dim voiceprint saved`;
          resultDiv.style.color = '#22c55e';
          checkVoiceStatus();
        } else {
          resultDiv.style.display = 'block';
          resultDiv.textContent = 'Enrollment failed: ' + (data.error || 'unknown');
          resultDiv.style.color = '#f87171';
        }
      } catch(e) {
        recDiv.style.display = 'none';
        resultDiv.style.display = 'block';
        resultDiv.textContent = 'Upload failed: ' + e.message;
        resultDiv.style.color = '#f87171';
      }
      btn.disabled = false;
    };
    _voiceRecorder.start();
    setTimeout(() => { if (_voiceRecorder && _voiceRecorder.state === 'recording') _voiceRecorder.stop(); }, 5500);
  } catch(e) {
    recDiv.style.display = 'none';
    resultDiv.style.display = 'block';
    resultDiv.textContent = 'Mic access denied: ' + e.message;
    resultDiv.style.color = '#f87171';
    btn.disabled = false;
  }
}

// Init voice status on load
document.addEventListener('DOMContentLoaded', checkVoiceStatus);
