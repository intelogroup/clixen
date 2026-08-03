marked.setOptions({ breaks: true, gfm: true });
const SESSIONS_KEY = 'g4l_sessions', ACTIVE_KEY = 'g4l_active';

// ── file uploads ──────────────────────────────────
const pendingFiles = [];   // [{id, name, mime, size}]

const MIME_ICONS = {
  'image':       '🖼',
  'audio':       '🎵',
  'video':       '🎬',
  'application/pdf': '📋',
  'text/csv':    '📊',
  'application/json': '{ }',
  'text':        '📄',
  'application': '📦',
};
function mimeIcon(mime) {
  if (MIME_ICONS[mime]) return MIME_ICONS[mime];
  const cat = mime.split('/')[0];
  return MIME_ICONS[cat] || '📎';
}
function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n/1024).toFixed(1) + ' KB';
  return (n/1048576).toFixed(1) + ' MB';
}

function renderAttachmentPreviews() {
  const bar = document.getElementById('attachments-preview');
  if (!bar) return;
  bar.innerHTML = '';
  pendingFiles.forEach((f, i) => {
    const chip = document.createElement('div');
    chip.className = 'attach-chip' + (f.uploading ? ' uploading' : '');
    chip.innerHTML = `<span class="chip-icon">${mimeIcon(f.mime||'')}</span>
      <span class="chip-name" title="${escHtml(f.name)}">${escHtml(f.name)}</span>
      ${f.uploading
        ? '<span class="chip-progress"></span>'
        : `<button class="chip-remove" data-i="${i}" title="Remove">×</button>`}`;
    if (!f.uploading) {
      chip.querySelector('.chip-remove').addEventListener('click', e => {
        e.stopPropagation();
        pendingFiles.splice(+e.target.dataset.i, 1);
        renderAttachmentPreviews();
      });
    }
    bar.appendChild(chip);
  });
}

async function uploadFile(file, chatId) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('chat_id', chatId);
  const res = await fetch('/upload', {method:'POST', body: fd});
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return await res.json(); // {id, name, mime, size}
}

const attachBtn = document.getElementById('attach-btn');
const fileInput = document.getElementById('file-input');
if (attachBtn && fileInput) attachBtn.addEventListener('click', () => {
  fileInput.click();
});

if (fileInput) fileInput.addEventListener('change', async e => {
  const files = Array.from(e.target.files);
  e.target.value = '';
  if (!files.length) return;
  const chatId = ensureActiveSession();
  for (const file of files) {
    const placeholder = {name: file.name, mime: file.type, size: file.size, uploading: true, id: null};
    pendingFiles.push(placeholder);
    renderAttachmentPreviews();
    try {
      const rec = await uploadFile(file, chatId);
      placeholder.id = rec.id; placeholder.mime = rec.mime;
      placeholder.uploading = false;
    } catch(err) {
      pendingFiles.splice(pendingFiles.indexOf(placeholder), 1);
      alert('Upload failed: ' + err.message);
    }
    renderAttachmentPreviews();
  }
});

// drag-and-drop onto the message area
const messagesContainer = document.getElementById('messages-container');
if (messagesContainer) messagesContainer.addEventListener('dragover', e => {
  e.preventDefault(); e.currentTarget.style.outline='2px dashed var(--accent)';
});
if (messagesContainer) messagesContainer.addEventListener('dragleave', e => {
  e.currentTarget.style.outline='';
});
if (messagesContainer) messagesContainer.addEventListener('drop', async e => {
  e.preventDefault(); e.currentTarget.style.outline='';
  const files = Array.from(e.dataTransfer.files);
  if (!files.length) return;
  const chatId = ensureActiveSession();
  for (const file of files) {
    const placeholder = {name: file.name, mime: file.type, size: file.size, uploading: true, id: null};
    pendingFiles.push(placeholder);
    renderAttachmentPreviews();
    try {
      const rec = await uploadFile(file, chatId);
      placeholder.id = rec.id; placeholder.mime = rec.mime; placeholder.uploading = false;
    } catch(err) {
      pendingFiles.splice(pendingFiles.indexOf(placeholder), 1);
    }
    renderAttachmentPreviews();
  }
});

// lightbox for images
function openLightbox(url) {
  document.getElementById('lightbox-img').src = url;
  document.getElementById('lightbox').style.display = 'flex';
}

function addFileCards(container, files) {
  if (!files || !files.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'file-attachments';
  files.forEach(f => {
    if (f.mime && f.mime.startsWith('image/')) {
      const img = document.createElement('img');
      img.className = 'img-preview';
      img.src = `/file/${f.id}`;
      img.alt = f.name;
      img.addEventListener('click', () => openLightbox(`/file/${f.id}`));
      wrap.appendChild(img);
    } else {
      const card = document.createElement('a');
      card.className = 'file-card';
      card.href = `/file/${f.id}`;
      card.download = f.name;
      card.innerHTML = `<span class="file-card-icon">${mimeIcon(f.mime||'')}</span>
        <div class="file-card-info">
          <div class="file-card-name">${escHtml(f.name)}</div>
          <div class="file-card-meta">${escHtml((f.mime||'').split('/').pop().toUpperCase())} · ${fmtSize(f.size||0)}</div>
        </div>`;
      wrap.appendChild(card);
    }
  });
  container.insertBefore(wrap, container.firstChild);
}

// ── helpers ──────────────────────────────────────
function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function ts() {
  return new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
}
function uuid() {
  return crypto.randomUUID ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,
        c=>((c==='x'?Math.random()*16|0:(Math.random()*4|0)|8).toString(16)));
}
function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.innerHTML;
    btn.textContent = 'Copied!'; btn.classList.add('copied');
    setTimeout(()=>{btn.innerHTML=orig;btn.classList.remove('copied');},1500);
  }).catch(()=>{});
}

// ── markdown ─────────────────────────────────────
function stripMd(text) {
  // Remove markdown tokens during streaming so partial **bold renders as plain text
  return text.replace(/(\*{1,3}|_{1,3}|`{1,3}|~~|#{1,6} ?)/g, '');
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const ALLOWED_SVG_TAGS = new Set([
  'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
  'text', 'tspan', 'defs', 'lineargradient', 'radialgradient', 'stop', 'clippath',
  'mask', 'pattern', 'use', 'symbol', 'title', 'desc'
]);
const ALLOWED_SVG_ATTRS = new Set([
  'xmlns', 'xmlns:xlink', 'viewbox', 'width', 'height', 'x', 'y', 'x1', 'x2', 'y1',
  'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'fill', 'fill-opacity', 'stroke',
  'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-opacity', 'opacity',
  'transform', 'preserveaspectratio', 'font-size', 'font-family', 'font-weight',
  'text-anchor', 'dominant-baseline', 'offset', 'stop-color', 'stop-opacity',
  'gradientunits', 'gradienttransform', 'mask', 'maskunits', 'maskcontentunits',
  'patternunits', 'patterncontentunits', 'patterntransform', 'clip-path', 'cliprule',
  'fill-rule', 'href', 'xlink:href', 'id', 'class', 'style'
]);

function sanitizeSvg(svgText) {
  const raw = (svgText || '').trim();
  if (!raw) return null;

  const doc = new DOMParser().parseFromString(raw, 'image/svg+xml');
  const root = doc.documentElement;
  if (!root || root.tagName.toLowerCase() !== 'svg' || doc.querySelector('parsererror')) {
    return null;
  }

  const clean = node => {
    if (node.nodeType === 3) return;
    if (node.nodeType !== 1) {
      node.remove();
      return;
    }

    const tag = node.tagName.toLowerCase();
    if (!ALLOWED_SVG_TAGS.has(tag)) {
      node.remove();
      return;
    }

    Array.from(node.attributes).forEach(attr => {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim();
      const isDataAttr = name.startsWith('data-');
      const isAriaAttr = name.startsWith('aria-');
      const isEventAttr = name.startsWith('on');
      const isRefAttr = name === 'href' || name === 'xlink:href';
      const badRef = isRefAttr && value && !value.startsWith('#');
      const badValue = /javascript:|data:text\/html|<|>/i.test(value);
      const badStyle = name === 'style' && /javascript:|expression\s*\(/i.test(value);

      if (
        isEventAttr ||
        badRef ||
        badValue ||
        badStyle ||
        (!ALLOWED_SVG_ATTRS.has(name) && !isDataAttr && !isAriaAttr)
      ) {
        node.removeAttribute(attr.name);
      }
    });

    Array.from(node.childNodes).forEach(clean);
  };

  clean(root);
  root.setAttribute('xmlns', SVG_NS);
  return root.outerHTML;
}

function unwrapNestedSvgFence(text) {
  const raw = (text || '').trim();
  if (!raw) return null;
  const match = raw.match(/^```svg\s*\n([\s\S]*?)\n```$/i);
  return match ? match[1].trim() : null;
}

function isIncompleteSvg(text) {
  const raw = (text || '').trim();
  if (!raw || !/<svg\b/i.test(raw)) return false;
  return !/<\/svg>\s*$/i.test(raw);
}

function renderMarkdown(el, text) {
  el.innerHTML = marked.parse(text);
  
  // Post-process GitHub Alerts
  el.querySelectorAll('blockquote').forEach(bq => {
    const html = bq.innerHTML.trim();
    if (html.includes('[!NOTE]') || html.includes('[!WARNING]') || html.includes('[!TIP]') || html.includes('[!IMPORTANT]') || html.includes('[!CAUTION]')) {
      let type = 'note';
      if (html.includes('[!WARNING]')) type = 'warning';
      else if (html.includes('[!TIP]')) type = 'tip';
      else if (html.includes('[!IMPORTANT]')) type = 'important';
      else if (html.includes('[!CAUTION]')) type = 'caution';
      
      bq.className = `gh-alert ${type}`;
      
      let cleanHtml = bq.innerHTML.replace(/\[!(NOTE|WARNING|TIP|IMPORTANT|CAUTION)\]\s*(<br>)?/gi, '').trim();
      bq.innerHTML = `
        <div class="gh-alert-title">
          <span class="gh-alert-icon"></span>
          ${type.toUpperCase()}
        </div>
        <div class="gh-alert-content">${cleanHtml}</div>
      `;
    }
  });

  el.querySelectorAll('pre').forEach(pre => {
    const code = pre.querySelector('code');
    if (!code) return;
    const lang = (code.className.match(/language-([A-Za-z0-9_-]+)/)||[])[1] || 'text';
    const rawSource = code.textContent || '';
    const nestedSvg = unwrapNestedSvgFence(rawSource);
    const source = nestedSvg || rawSource;
    const svgLike = lang.toLowerCase() === 'svg' || nestedSvg || /<svg\b/i.test(rawSource);
    if (svgLike) {
      const sanitized = sanitizeSvg(source);
      if (sanitized) {
        const wrap = document.createElement('div');
        wrap.className = 'svg-artifact';
        wrap.innerHTML = `<div class="svg-artifact-head">
            <span class="svg-artifact-label">SVG preview</span>
            <button class="svg-artifact-copy">Copy SVG</button>
          </div>
          <div class="svg-artifact-canvas">${sanitized}</div>`;
        wrap.querySelector('.svg-artifact-copy').addEventListener('click', e => {
          copyText(source, e.currentTarget);
        });
        pre.replaceWith(wrap);
        return;
      }
      if (isIncompleteSvg(source)) {
        return;
      }
    }
    hljs.highlightElement(code);
    const hdr = document.createElement('div');
    hdr.className = 'code-header';
    hdr.innerHTML = `<span class="code-lang">${escHtml(lang)}</span>
      <button class="code-copy" onclick="copyText(this.closest('pre').querySelector('code').textContent,this)">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        Copy</button>`;
    pre.insertBefore(hdr, code);
  });
}

// ── workspace lifecycle placeholders ─────────────
let workspaceState = null;
let automationCatalog = [];
let automationRuns = [];
let workflowItems = [];
let activeMainView = 'chat';

async function logoutWorkspace() {
  const data = await apiJson('/api/auth/logout', {method:'POST', body: JSON.stringify({})});
  renderWorkspaceUI(data);
  openWorkspaceModal();
  return data;
}

async function safeJsonResponse(res) {
  const contentType = (res.headers.get('content-type') || '').toLowerCase();
  const body = await res.text();
  if (!res.ok) throw new Error(body || `Request failed: ${res.status}`);
  if (!contentType.includes('application/json')) {
    throw new Error(`Expected JSON from ${res.url || 'request'}, got ${contentType || 'unknown content type'}`);
  }
  try {
    return JSON.parse(body);
  } catch (err) {
    throw new Error(`Invalid JSON from ${res.url || 'request'}: ${err.message}`);
  }
}

async function fetchJson(url, options={}) {
  const res = await fetch(url, options);
  return safeJsonResponse(res);
}

async function apiJson(url, options={}) {
  return fetchJson(url, {
    ...options,
    headers: {'Content-Type':'application/json', ...(options.headers||{})},
  });
}

function showMainView(view) {
  activeMainView = view;
  const panel = document.getElementById('workspace-panel');
  const navWorkflows = document.getElementById('nav-workflows-btn');
  const navResearch = document.getElementById('nav-research-btn');
  const ideBtn = document.getElementById('ide-btn');
  const status = document.getElementById('status');
  
  if (view === 'chat') {
    if (panel) panel.classList.add('collapsed');
    if (window.closeIDE) window.closeIDE();
  } else {
    if (panel) panel.classList.remove('collapsed');
    
    // Switch active tab pane
    document.querySelectorAll('.workspace-pane').forEach(pane => {
      pane.classList.toggle('active', pane.dataset.pane === view);
    });
    
    // Switch active tab button in workspace header
    document.querySelectorAll('.ws-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.pane === view);
    });

    if (view === 'ide') {
      if (window.openIDE) window.openIDE();
    } else {
      if (window.closeIDE) window.closeIDE();
    }

    if (view === 'workflows') {
      refreshWorkflows();
    } else if (view === 'research') {
      loadResearchReports();
    }
  }

  // Update nav highlights
  if (navWorkflows) navWorkflows.classList.toggle('active', view === 'workflows');
  if (navResearch) navResearch.classList.toggle('active', view === 'research');
  if (ideBtn) ideBtn.classList.toggle('on', view === 'ide');
  
  if (status) {
    if (view === 'workflows') status.textContent = 'workflows active';
    else if (view === 'research') status.textContent = 'research reports';
    else if (view === 'ide') status.textContent = 'editor active';
    else status.textContent = workspaceState?.user?.authenticated ? 'local model' : 'locked';
  }
  
  if (location.hash !== `#${view}`) {
    history.replaceState(null, '', view === 'chat' ? location.pathname : `#${view}`);
  }
}

function initMainView() {
  const requested = location.hash === '#workflows' ? 'workflows' : 'chat';
  showMainView(requested);
  const filter = document.getElementById('workflow-status-filter');
  if (filter) filter.addEventListener('change', () => refreshWorkflows(true));
  window.addEventListener('hashchange', () => {
    showMainView(location.hash === '#workflows' ? 'workflows' : 'chat');
  });
}

function formatWorkflowDate(raw) {
  if (!raw) return 'Not scheduled';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function describeWorkflowSchedule(schedule) {
  if (!schedule || !Object.keys(schedule).length) return 'Manual';
  if (schedule.interval_seconds) {
    const seconds = Number(schedule.interval_seconds);
    if (seconds % 3600 === 0) return `Every ${seconds / 3600}h`;
    if (seconds % 60 === 0) return `Every ${seconds / 60}m`;
    return `Every ${seconds}s`;
  }
  if (schedule.cron) return `Cron ${schedule.cron}`;
  if (schedule.run_at) return `Once ${formatWorkflowDate(schedule.run_at)}`;
  return 'Custom';
}

function formatWorkflowJson(value) {
  const empty = !value || (typeof value === 'object' && Object.keys(value).length === 0);
  if (empty) return '{}';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function isWorkflowDue(item) {
  if (String(item.status).toLowerCase() !== 'active' || !item.next_run_at) return false;
  const d = new Date(item.next_run_at);
  return !Number.isNaN(d.getTime()) && d.getTime() <= Date.now();
}

function renderWorkflowCard(item) {
  const status = String(item.status || 'unknown').toLowerCase();
  const canPause = status === 'active';
  const action = canPause
    ? `<button class="workflow-action-btn warning" type="button" data-workflow-action="pause" data-workflow-id="${escHtml(item.id)}">Pause</button>`
    : `<button class="workflow-action-btn primary" type="button" data-workflow-action="resume" data-workflow-id="${escHtml(item.id)}">Resume</button>`;
  return `
    <article class="workflow-card">
      <div class="workflow-card-head">
        <div>
          <div class="workflow-title-row">
            <span class="workflow-badge ${escHtml(status)}">${escHtml(status)}</span>
            <h2 class="workflow-title" title="${escHtml(item.task_name)}">${escHtml(item.task_name || item.automation_id)}</h2>
          </div>
          <div class="workflow-id">${escHtml(item.automation_id || 'manual')} · ${escHtml((item.id || '').slice(0, 12))}</div>
        </div>
        <div class="workflow-card-actions">
          <button class="workflow-action-btn primary" type="button" data-workflow-action="run-now" data-workflow-id="${escHtml(item.id)}">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            Run now
          </button>
          ${action}
        </div>
      </div>
      <div class="workflow-meta-grid">
        <div class="workflow-meta"><label>Trigger</label><div>${escHtml(item.trigger_type || 'schedule')}</div></div>
        <div class="workflow-meta"><label>Action</label><div>${escHtml(item.action_type || 'notification')}</div></div>
        <div class="workflow-meta"><label>Schedule</label><div title="${escHtml(formatWorkflowJson(item.schedule))}">${escHtml(describeWorkflowSchedule(item.schedule))}</div></div>
        <div class="workflow-meta"><label>Next run</label><div>${escHtml(formatWorkflowDate(item.next_run_at))}</div></div>
      </div>
      <div class="workflow-details">
        <div class="workflow-json-block">
          <label>Config</label>
          <pre>${escHtml(formatWorkflowJson(item.config))}</pre>
        </div>
        <div class="workflow-json-block">
          <label>Last result</label>
          <pre>${escHtml(formatWorkflowJson(item.last_result))}</pre>
        </div>
      </div>
    </article>
  `;
}

function renderWorkflows() {
  const list = document.getElementById('workflow-list');
  if (!list) return;
  const all = workflowItems || [];
  const activeCount = all.filter(item => item.status === 'active').length;
  const pausedCount = all.filter(item => item.status === 'paused').length;
  const dueCount = all.filter(isWorkflowDue).length;
  const totalEl = document.getElementById('workflow-total-count');
  const activeEl = document.getElementById('workflow-active-count');
  const pausedEl = document.getElementById('workflow-paused-count');
  const dueEl = document.getElementById('workflow-due-count');
  if (totalEl) totalEl.textContent = all.length;
  if (activeEl) activeEl.textContent = activeCount;
  if (pausedEl) pausedEl.textContent = pausedCount;
  if (dueEl) dueEl.textContent = dueCount;
  if (!all.length) {
    list.innerHTML = '<div class="workflow-empty">No workflows found.</div>';
    return;
  }
  list.innerHTML = all.map(renderWorkflowCard).join('');
  list.querySelectorAll('[data-workflow-action]').forEach(btn => {
    btn.addEventListener('click', () => runWorkflowAction(btn.dataset.workflowId, btn.dataset.workflowAction, btn));
  });
}

async function refreshWorkflows(force=false) {
  const list = document.getElementById('workflow-list');
  if (!list) return;
  if (activeMainView !== 'workflows' && !force) return;
  const status = document.getElementById('workflow-status-filter')?.value || '';
  if (!workflowItems.length) {
    list.innerHTML = '<div class="workflow-empty">Loading workflows...</div>';
  }
  try {
    const qs = new URLSearchParams({limit: '100'});
    if (status) qs.set('status', status);
    const data = await fetchJson(`/workflows?${qs.toString()}`);
    workflowItems = data.workflows || [];
    renderWorkflows();
  } catch (err) {
    console.error('workflows failed', err);
    list.innerHTML = `<div class="workflow-empty workflow-error">Could not load workflows: ${escHtml(err.message)}</div>`;
  }
}

async function runWorkflowAction(workflowId, action, btn) {
  if (!workflowId || !action) return;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Working...';
  try {
    await apiJson(`/workflows/${encodeURIComponent(workflowId)}/${action}`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    await refreshWorkflows(true);
  } catch (err) {
    console.error('workflow action failed', err);
    btn.textContent = 'Failed';
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 1200);
    return;
  }
  btn.disabled = false;
}

function renderLifecycleSteps(steps) {
  return steps.map(step => `
    <div class="lifecycle-item ${step.done ? 'done' : ''}">
      <div class="lifecycle-dot"></div>
      <div class="lifecycle-copy">
        <div class="lifecycle-label">${escHtml(step.label)}</div>
        <div class="lifecycle-detail">${escHtml(step.detail)}</div>
      </div>
    </div>
  `).join('');
}

function automationIcon(icon) {
  const map = {
    mail: '✉',
    calendar: '◷',
    clipboard: '▣',
    radar: '◉',
    browser: '⌘',
    home: '⌂',
    briefcase: '▤',
    spark: '✦',
    plane: '→',
    loop: '↻',
  };
  return map[icon] || '•';
}

function renderAutomationCards(items) {
  return items.map(item => `
    <div class="automation-card">
      <div class="automation-head">
        <div>
          <div class="automation-title">${escHtml(item.title)}</div>
          <div class="workspace-summary-meta" style="margin-top:6px;justify-content:flex-start">
            <span class="workspace-summary-pill">${escHtml(item.category || 'Automation')}</span>
            <span class="workspace-summary-pill">${escHtml(item.approval || 'manual')}</span>
          </div>
          <div class="automation-summary">${escHtml(item.summary)}</div>
        </div>
        <div class="automation-icon">${automationIcon(item.icon)}</div>
      </div>
      ${item.primary_field ? `
        <div class="workspace-field">
          <label for="automation-input-${item.id}">${escHtml(item.primary_field.label)}</label>
          <input id="automation-input-${item.id}" data-automation-key="${escHtml(item.primary_field.key)}"
            placeholder="${escHtml(item.primary_field.placeholder || '')}"
            value="${escHtml((item.params && item.params[item.primary_field.key]) || '')}"/>
        </div>
      ` : ''}
      <div class="workspace-summary-meta" style="justify-content:flex-start">
        ${(item.outputs || []).map(output => `<span class="workspace-summary-pill">${escHtml(output)}</span>`).join('')}
      </div>
      <div class="automation-actions">
        <button class="automation-run" type="button" data-automation-id="${item.id}">Run</button>
        <button class="automation-save" type="button" data-save-automation-id="${item.id}">Save preset</button>
      </div>
      <div class="automation-status" id="automation-status-${item.id}">Ready</div>
    </div>
  `).join('');
}

function renderAutomationRuns(items) {
  if(!items.length) {
    return `<div class="run-item"><div><div class="run-title">No runs yet</div><div class="run-meta">Run an automation to see queue progress and results here.</div></div></div>`;
  }
  return items.slice(0,8).map(run => {
    const automation = automationCatalog.find(item => item.id === run.automation_id);
    const title = automation ? automation.title : run.task_name;
    const statusClass = String(run.status || '').toLowerCase();
    const step = run.current_step ? ` · ${run.current_step}` : '';
    const error = run.last_error ? ` · ${String(run.last_error).slice(0,120)}` : '';
    return `
      <div class="run-item">
        <div>
          <div class="run-title">${escHtml(title)}</div>
          <div class="run-meta">${escHtml((run.updated_at || run.created_at || '').replace('T',' ').slice(0,19))}${step}${error}</div>
        </div>
        <div class="run-badge ${escHtml(statusClass)}">${escHtml(run.status || 'queued')}</div>
      </div>
    `;
  }).join('');
}

function latestRunFor(automationId) {
  return automationRuns.find(run => run.automation_id === automationId) || null;
}

function renderAutomationStatuses() {
  automationCatalog.forEach(item => {
    const el = document.getElementById(`automation-status-${item.id}`);
    if(!el) return;
    const run = latestRunFor(item.id);
    if(!run) {
      el.textContent = 'Ready';
      return;
    }
    const step = run.current_step ? ` · ${run.current_step}` : '';
    el.textContent = `${run.status}${step}`;
  });
  const runsEl = document.getElementById('automation-runs-list');
  if(runsEl) runsEl.innerHTML = renderAutomationRuns(automationRuns);
}

async function refreshAutomationRuns() {
  try {
    const data = await fetchJson('/automations/runs');
    automationRuns = data.runs || [];
    renderAutomationStatuses();
  } catch(err) {
    console.error('automation runs failed', err);
  }
}

async function pollAutomationJob(automationId, jobId) {
  for(let i = 0; i < 8; i++) {
    try {
      const run = await fetchJson(`/tasks/${jobId}`);
      automationRuns = [{...run, automation_id: automationId}, ...automationRuns.filter(item => item.id !== run.id)];
      renderAutomationStatuses();
      if(['done','failed'].includes(run.status)) return;
    } catch(err) {
      console.error('poll job failed', err);
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
}

function renderWorkspaceUI(data) {
  workspaceState = data;

  // Guard: skip workspace modal updates if modal doesn't exist (e.g., on login page)
  const workspaceModal = document.getElementById('workspace-modal-overlay');
  if (!workspaceModal && !document.querySelector('.workspace-panel')) {
    console.log('renderWorkspaceUI: no modal, skipping modal updates');
    return;
  }

  const user = data.user;
  const auth = data.auth || {has_account: false, authenticated: false};
  const lifecycle = data.lifecycle;
  const gmail = data.integrations.gmail || {};
  const nextStep = lifecycle.steps.find(step => !step.done) || lifecycle.steps[lifecycle.steps.length - 1];

  const profileChipAvatar = document.getElementById('profile-chip-avatar');
  if (profileChipAvatar) profileChipAvatar.textContent = user.avatar || 'G4';
  const profileChipName = document.getElementById('profile-chip-name');
  if (profileChipName) profileChipName.textContent = user.display_name || 'Guest';
  const profileChipSub = document.getElementById('profile-chip-sub');
  if (profileChipSub) profileChipSub.textContent = user.authenticated ? (user.role || 'workspace') : (auth.has_account ? 'Locked' : 'No account');
  const headerAvatar = document.querySelector('header .avatar');
  if (headerAvatar) headerAvatar.textContent = user.avatar || 'G4';
  const authPill = document.getElementById('auth-pill');
  const authPillText = document.getElementById('auth-pill-text');
  if (authPill) {
    authPill.classList.toggle('authenticated', !!user.authenticated);
    authPill.classList.toggle('locked', !user.authenticated);
  }
  if (authPillText) authPillText.textContent = user.authenticated ? `Signed in as ${user.display_name}` : (auth.has_account ? 'Locked' : 'No account');

  const workspaceSidebarCard = document.getElementById('workspace-sidebar-card');
  const cardAction = user.authenticated ? 'openWorkspaceModal()' : "window.location.href='/login'";
  const cardBtnLabel = user.authenticated ? 'Settings' : (auth.has_account ? 'Sign in' : 'Create account');
  const cardSub = user.authenticated
    ? `Signed in · ${escHtml(user.email || '')}`
    : (auth.has_account ? 'Locked · sign in required' : 'No account configured');
  if (workspaceSidebarCard) workspaceSidebarCard.innerHTML = `
    <div class="workspace-card-head" onclick="${cardAction}" style="cursor:pointer">
      <div>
        <div class="workspace-card-title">${escHtml(user.authenticated ? user.display_name : 'Local Workspace')}</div>
        <div class="workspace-card-sub">${cardSub}</div>
      </div>
      <button class="workspace-mini-btn" type="button" onclick="event.stopPropagation(); ${cardAction}">${escHtml(cardBtnLabel)}</button>
    </div>
  `;

  const workspaceLifecyclePanel = document.getElementById('workspace-lifecycle-panel');
  if (workspaceLifecyclePanel) workspaceLifecyclePanel.innerHTML = renderLifecycleSteps(lifecycle.steps);
  const workspaceStatusLine = document.getElementById('workspace-status-line');
  if (workspaceStatusLine) workspaceStatusLine.textContent = `${lifecycle.completion}% lifecycle complete`;
  const gmailDetail = document.getElementById('gmail-detail');
  const gmailPaths = document.getElementById('gmail-paths');
  if (gmailDetail) gmailDetail.textContent = gmail.detail;
  if (gmailPaths) gmailPaths.textContent = gmail.connected
    ? `${gmail.token_path} · ${gmail.credentials_path}`
    : 'No Gmail token files detected.';

  const gmailBadge = document.getElementById('gmail-badge');
  if (gmailBadge) {
    gmailBadge.textContent = gmail.connected ? 'Detected' : 'Missing';
    gmailBadge.className = `integration-badge ${gmail.connected ? 'connected' : ''}`;
  }

  const whatsapp = data.integrations.whatsapp || {};
  const whatsappDetail = document.getElementById('whatsapp-detail');
  const whatsappPaths = document.getElementById('whatsapp-paths');
  if (whatsappDetail) whatsappDetail.textContent = whatsapp.detail || 'Checking...';
  if (whatsappPaths) whatsappPaths.textContent = whatsapp.connected
    ? `Status: ${whatsapp.status}`
    : 'Scan QR code to connect';
  const whatsappBadge = document.getElementById('whatsapp-badge');
  if (whatsappBadge) {
    whatsappBadge.textContent = whatsapp.connected ? 'Connected' : 'Pending';
    whatsappBadge.className = `integration-badge ${whatsapp.connected ? 'connected' : ''}`;
  }

  if (!whatsapp.connected) {
    fetch('/api/whatsapp-qr')
      .then(r => r.json())
      .then(qr => {
        if (qr.qr) {
          const qrImg = document.getElementById('whatsapp-qr-image');
          const qrContainer = document.getElementById('whatsapp-qr-container');
          if (qrImg && qrContainer) {
            qrImg.src = qr.qr;
            qrContainer.style.display = 'block';
          }
        }
      })
      .catch(() => {});
  }

  const mockDisplayName = document.getElementById('mock-display-name');
  const mockEmail = document.getElementById('mock-email');
  const mockPassword = document.getElementById('mock-password');
  const mockWorkspaceName = document.getElementById('mock-workspace-name');
  const mockRole = document.getElementById('mock-role');
  if (mockDisplayName) mockDisplayName.value = user.authenticated ? user.display_name : '';
  if (mockEmail) mockEmail.value = user.email || '';
  if (mockPassword) mockPassword.value = '';
  if (mockWorkspaceName) mockWorkspaceName.value = user.workspace_name || 'Local Workspace';
  if (mockRole) mockRole.value = user.role || 'Owner';
  if (mockDisplayName) mockDisplayName.disabled = auth.has_account;
  if (mockWorkspaceName) mockWorkspaceName.disabled = auth.has_account;
  if (mockRole) mockRole.disabled = auth.has_account;
  const authSubmitBtn = document.getElementById('auth-submit-btn');
  if (authSubmitBtn) {
    authSubmitBtn.textContent = auth.has_account ? (user.authenticated ? 'Signed in' : 'Sign in') : 'Create local account';
    authSubmitBtn.disabled = !!user.authenticated;
  }
  const mockLogoutBtn = document.getElementById('mock-logout-btn');
  if (mockLogoutBtn) mockLogoutBtn.style.display = 'none';
  const authBanner = document.getElementById('auth-state-banner');
  if (authBanner) {
    authBanner.classList.toggle('authenticated', !!user.authenticated);
    authBanner.classList.toggle('locked', !user.authenticated);
  }
  const authStateTitle = document.getElementById('auth-state-title');
  const authStateDetail = document.getElementById('auth-state-detail');
  if (authStateTitle) authStateTitle.textContent = user.authenticated ? 'Workspace unlocked' : (auth.has_account ? 'Workspace locked' : 'Create local owner account');
  if (authStateDetail) authStateDetail.textContent = user.authenticated
    ? `Signed in as ${user.email}. Chat, uploads, and local integrations are available on this browser session.`
    : (auth.has_account
      ? 'Enter the configured email and password to unlock the workspace.'
      : 'Create the first local account to unlock chat, uploads, and integrations.');
  const authBannerLogout = document.getElementById('auth-banner-logout');
  if (authBannerLogout) authBannerLogout.style.display = 'none';
  const authFormHint = document.getElementById('auth-form-hint');
  if (authFormHint) authFormHint.textContent = auth.has_account
    ? (user.authenticated
      ? 'This browser session is authenticated.'
      : 'Enter the configured email and password to unlock the local workspace.')
    : 'Create the first local account to unlock chat, uploads, and integrations.';
  const workspaceSummaryTitle = document.getElementById('workspace-summary-title');
  if (workspaceSummaryTitle) workspaceSummaryTitle.textContent =
    lifecycle.completion >= 100
      ? `${user.workspace_name || 'Workspace'} is fully staged`
      : `${user.workspace_name || 'Local workspace'} is ${lifecycle.completion}% staged`;
  const workspaceSummarySub = document.getElementById('workspace-summary-sub');
  if (workspaceSummarySub) workspaceSummarySub.textContent = nextStep
    ? `Next up: ${nextStep.label}. ${nextStep.detail}`
    : 'All local workspace checkpoints are completed.';
  const workspaceSummaryMeta = document.getElementById('workspace-summary-meta');
  if (workspaceSummaryMeta) workspaceSummaryMeta.innerHTML = `
    <span class="workspace-summary-pill strong">${lifecycle.completion}% complete</span>
    <span class="workspace-summary-pill">${escHtml(gmail.connected ? 'Gmail detected' : 'Gmail pending')}</span>
    <span class="workspace-summary-pill">${escHtml(whatsapp.connected ? 'WhatsApp connected' : 'WhatsApp pending')}</span>
  `;
  const composerLocked = !user.authenticated;
  const input = document.getElementById('input');
  if(input){
    input.disabled = composerLocked;
    input.placeholder = composerLocked ? 'Sign in to unlock chat\u2026' : 'Message Clixen\u2026 (\u23ce send \u00b7 \u21e7\u23ce newline \u00b7 \u2318N new chat)';
  }
  ['attach-btn', 'web-search-btn', 'mic-btn'].forEach(id => {
    const el = document.getElementById(id);
    if(el) el.disabled = composerLocked;
  });
  const sendBtn = document.getElementById('send-btn');
  if(sendBtn) sendBtn.disabled = composerLocked;
  const status = document.getElementById('status');
  if(status && activeMainView !== 'workflows') status.textContent = composerLocked ? 'locked' : 'local model';
  document.querySelectorAll('.workspace-panel button[type="submit"]').forEach(el => {
    if(el.id === 'auth-submit-btn') return;
    if(el.id === 'mock-logout-btn') {
      el.disabled = !user.authenticated;
      return;
    }
    el.disabled = composerLocked;
  });
  const automationGrid = document.getElementById('automation-grid');
  if (automationGrid) automationGrid.innerHTML = renderAutomationCards(automationCatalog);
  document.querySelectorAll('.automation-run, .automation-save, #automation-grid input').forEach(el => {
    if(el) el.disabled = composerLocked;
  });
  renderAutomationStatuses();
  document.querySelectorAll('.automation-run').forEach(btn => {
    btn.addEventListener('click', async () => {
      const automationId = btn.dataset.automationId;
      const status = document.getElementById(`automation-status-${automationId}`);
      const automation = automationCatalog.find(item => item.id === automationId);
      btn.disabled = true;
      if(status) status.textContent = 'Queueing…';
      try {
        const overrides = {};
        if(automation?.primary_field) {
          const input = document.getElementById(`automation-input-${automationId}`);
          const value = input?.value.trim();
          if(value) overrides[automation.primary_field.key] = value;
        }
        const data = await apiJson(`/automations/${automationId}/run`, {method:'POST', body: JSON.stringify({overrides})});
        status.textContent = `Queued ${data.job_id.slice(0,8)}`;
        automationRuns = [{id: data.job_id, status: 'queued', current_step: '', automation_id: automationId}, ...automationRuns];
        renderAutomationStatuses();
        pollAutomationJob(automationId, data.job_id);
      } catch(err) {
        console.error(err);
        status.textContent = 'Failed to queue';
      } finally {
        btn.disabled = false;
      }
    });
  });
  document.querySelectorAll('.automation-save').forEach(btn => {
    btn.addEventListener('click', async () => {
      const automationId = btn.dataset.saveAutomationId;
      const automation = automationCatalog.find(item => item.id === automationId);
      if(!automation?.primary_field) return;
      const input = document.getElementById(`automation-input-${automationId}`);
      const value = input?.value.trim();
      const params = {...(automation.params || {})};
      if(value) params[automation.primary_field.key] = value;
      btn.disabled = true;
      try {
        const saved = await apiJson(`/automations/${automationId}/preset`, {method:'POST', body: JSON.stringify({params})});
        const idx = automationCatalog.findIndex(item => item.id === automationId);
        if(idx >= 0) automationCatalog[idx].params = saved.params;
        const status = document.getElementById(`automation-status-${automationId}`);
        if(status) status.textContent = 'Preset saved';
      } catch(err) {
        console.error(err);
      } finally {
        btn.disabled = false;
      }
    });
  });

}

async function refreshWorkspaceState() {
  try {
    const [data, automations, runs] = await Promise.all([
      fetchJson('/api/workspace'),
      fetchJson('/automations'),
      fetchJson('/automations/runs'),
    ]);
    automationCatalog = automations.automations || [];
    automationRuns = runs.runs || [];
    renderWorkspaceUI(data);
    if(data.user?.authenticated){
      const activeId = getActiveId();
      if(activeId) loadHistory(activeId);
    }
    if(!data.user?.authenticated) openWorkspaceModal();
  } catch(err) {
    console.error('workspace state failed', err);
  }
}

function switchSettingsTab(tabName) {
  document.querySelectorAll('.settings-tab-pane').forEach(el => {
    el.style.display = 'none';
  });
  document.querySelectorAll('.settings-tab-btn').forEach(btn => {
    btn.classList.remove('active');
  });
  
  const targetPane = document.getElementById(`settings-tab-pane-${tabName}`);
  if (targetPane) targetPane.style.display = 'flex';
  
  const targetBtn = document.getElementById(`settings-tab-btn-${tabName}`);
  if (targetBtn) targetBtn.classList.add('active');
  
  if (tabName === 'cookbook' && typeof loadCookbookSystemProfile === 'function') {
    loadCookbookSystemProfile();
  }
}
window.switchSettingsTab = switchSettingsTab;

function openWorkspaceModal() {
  document.getElementById('workspace-modal').classList.add('open');
  if(workspaceState?.user?.authenticated) {
    fetchJson('/api/workspace')
      .then(renderWorkspaceUI)
      .catch(err => console.error('workspace modal refresh failed', err));
  }
  refreshAutomationRuns();
  if(typeof loadCookbookSystemProfile === 'function') loadCookbookSystemProfile();
  
  const defaultTab = workspaceState?.user?.authenticated ? 'integrations' : 'account';
  switchSettingsTab(defaultTab);
}

function closeWorkspaceModal() {
  document.getElementById('workspace-modal').classList.remove('open');
}
