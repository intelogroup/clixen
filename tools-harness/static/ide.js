// ── IDE ──────────────────────────────────────────
(function(){
  const ideView     = document.getElementById('ide-view');
  const ideBtn      = document.getElementById('ide-btn');
  const ideClose    = document.getElementById('ide-close-btn');
  const ideFiles    = document.getElementById('ide-files');
  const ideTabs     = document.getElementById('ide-tabs');
  const ideOutput   = document.getElementById('ide-output');
  const ideRunBtn   = document.getElementById('ide-run-btn');
  const ideInput    = document.getElementById('ide-input');
  const ideSend     = document.getElementById('ide-send-btn');
  const ideMessages = document.getElementById('ide-chat-messages');
  const ideOutputWrap = document.getElementById('ide-output-wrap');
  const ideBreadcrumbs = document.getElementById('ide-breadcrumbs');
  const ideWelcome  = document.getElementById('ide-welcome-editor');
  const sbBranch    = document.getElementById('sb-branch-name');
  const sbLang      = document.getElementById('sb-lang-name');
  const sbPos       = document.getElementById('sb-pos-text');
  const ideTitleName = document.getElementById('ide-titlebar-name');
  const ideFolderModal = document.getElementById('ide-folder-modal');
  const ideFolderList  = document.getElementById('ide-folder-list');
  const ideFolderInput = document.getElementById('ide-folder-path-input');
  const ideCtxMenu  = document.getElementById('ide-ctx-menu');

  let _editor = null;
  let _monacoReady = false;
  let _currentRoot = '';
  let _openFiles = {};       // path → {content, editorModel, modified}
  let _activeFile = null;
  let _ideChatId = 'ide_' + (localStorage.getItem('g4l_active') || 'default');
  let _folderPickerSelected = '';
  let _ctxTarget = null;

  // ── Monaco init ──────────────────────────────────
  function initMonaco(cb) {
    if (_monacoReady) { cb(); return; }
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.50.0/min/vs/loader.js';
    script.onload = () => {
      require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.50.0/min/vs' }});
      require(['vs/editor/editor.main'], () => {
        // VS Code exact theme
        monaco.editor.defineTheme('vscode-dark', {
          base: 'vs-dark', inherit: true, rules: [],
          colors: {
            'editor.background': '#1e1e1e',
            'editor.foreground': '#d4d4d4',
            'editor.lineHighlightBackground': '#2a2d2e',
            'editor.selectionBackground': '#264f78',
            'editorLineNumber.foreground': '#858585',
            'editorLineNumber.activeForeground': '#c6c6c6',
            'editorCursor.foreground': '#aeafad',
            'editorWhitespace.foreground': '#3b3b3b',
            'editorIndentGuide.background': '#404040',
            'editorIndentGuide.activeBackground': '#707070',
            'editor.findMatchBackground': '#515c6a',
            'editor.findMatchHighlightBackground': '#ea5c0033',
            'editorBracketMatch.background': '#0d3a58',
            'editorBracketMatch.border': '#888888',
          }
        });
        _editor = monaco.editor.create(
          document.getElementById('ide-editor-container'), {
            theme: 'vscode-dark',
            fontSize: 14,
            fontFamily: "'Geist Mono','Cascadia Code','JetBrains Mono','Fira Code',monospace",
            fontLigatures: true,
            lineHeight: 22,
            minimap: { enabled: true, scale: 1 },
            scrollBeyondLastLine: false,
            renderLineHighlight: 'all',
            padding: { top: 8, bottom: 8 },
            automaticLayout: true,
            cursorBlinking: 'smooth',
            cursorSmoothCaretAnimation: 'on',
            smoothScrolling: true,
            bracketPairColorization: { enabled: true },
            guides: { bracketPairs: true, indentation: true },
            renderWhitespace: 'selection',
            suggest: { showFiles: true },
            quickSuggestions: true,
            wordWrap: 'off',
            tabSize: 4,
            insertSpaces: true,
          }
        );
        _editor.onDidChangeModelContent(() => {
          if (_activeFile && _openFiles[_activeFile]) {
            _openFiles[_activeFile].modified = true;
            renderTabs();
          }
        });
        _editor.onDidChangeCursorPosition(e => {
          sbPos.textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
        });
        _editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveActive);
        _monacoReady = true;
        cb();
      });
    };
    document.head.appendChild(script);
  }

  // ── open/close IDE ───────────────────────────────
  function openIDE() {
    ideView.classList.add('visible');
    ideBtn.classList.add('on');
    initMonaco(() => { if (!_currentRoot) showTreeWelcome(); });
  }
  function closeIDE() {
    ideView.classList.remove('visible');
    ideBtn.classList.remove('on');
  }
  window.openIDE = openIDE;
  window.closeIDE = closeIDE;

  function showTreeWelcome() {
    ideFiles.innerHTML = `<div style="padding:24px 16px;text-align:center">
      <p style="font-size:12px;color:#858585;margin-bottom:14px;line-height:1.6">
        Open a folder to start editing
      </p>
      <button id="tree-open-folder-btn"
        style="background:#007acc;border:none;color:#fff;font-size:12.5px;font-weight:600;
               padding:6px 18px;border-radius:3px;cursor:pointer;font-family:var(--font)">
        Open Folder…
      </button></div>`;
    document.getElementById('tree-open-folder-btn')?.addEventListener('click', () => openFolderModal('~'));
  }

  ideBtn.addEventListener('click', () => {
    if (activeMainView === 'ide') {
      showMainView('chat');
    } else {
      showMainView('ide');
    }
  });
  document.getElementById('ide-close-btn').addEventListener('click', () => showMainView('chat'));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      if (ideFolderModal.classList.contains('open')) { ideFolderModal.classList.remove('open'); return; }
      if (ideCtxMenu.style.display === 'block') { ideCtxMenu.style.display = 'none'; return; }
      if (activeMainView === 'ide') showMainView('chat');
    }
  });

  // ── activity bar ─────────────────────────────────
  document.querySelectorAll('.act-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.act-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const panel = btn.dataset.panel;
      const sidebar = document.getElementById('ide-sidebar');
      if (panel === 'explorer') {
        sidebar.classList.remove('hidden');
        document.getElementById('ide-tree-title').textContent = 'Explorer';
      } else if (panel === 'git') {
        sidebar.classList.remove('hidden');
        document.getElementById('ide-tree-title').textContent = 'Source Control';
        _loadGitStatus();
      } else {
        sidebar.classList.toggle('hidden');
      }
    });
  });

  async function _loadGitStatus() {
    ideFiles.innerHTML = '<div style="padding:8px 12px;font-size:12px;color:#858585">Loading git status…</div>';
    try {
      const r = await fetch('/fs/run', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ path: _currentRoot + '/.git', _cmd: 'git_status' })
      });
    } catch(e) {}
    // Show a hint since git_status is via tool, not direct
    ideFiles.innerHTML = `<div style="padding:12px 16px;font-size:12px;color:#858585">
      Ask Clixen chat: "git status in ${_currentRoot}"</div>`;
  }

  // ── folder picker modal ───────────────────────────
  function openFolderModal(startPath) {
    _folderPickerSelected = (typeof startPath === 'string' && startPath) ? startPath : (_currentRoot || '~');
    ideFolderInput.value = _folderPickerSelected;
    ideFolderModal.classList.add('open');
    loadFolderBrowser(_folderPickerSelected);
  }

  async function loadFolderBrowser(path) {
    ideFolderList.innerHTML = '<div style="padding:12px 16px;color:#858585;font-size:12px">Loading…</div>';
    ideFolderInput.value = path;
    _folderPickerSelected = path;
    let data;
    try {
      const r = await fetch('/fs/tree?path=' + encodeURIComponent(path) + '&depth=1');
      data = await r.json();
    } catch(e) {
      ideFolderList.innerHTML = `<div style="padding:12px 16px;color:#f48771;font-size:12px">Error: ${e}</div>`;
      return;
    }
    if (data.error) {
      ideFolderList.innerHTML = `<div style="padding:12px 16px;color:#f48771;font-size:12px">${data.error}</div>`;
      return;
    }
    ideFolderList.innerHTML = '';
    // Parent dir
    const parentPath = path.replace(/\/[^/]+$/, '') || '/';
    if (path !== parentPath && path !== '/') {
      const up = document.createElement('div');
      up.className = 'fdr-item up';
      up.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#858585" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg> ..`;
      up.addEventListener('click', () => loadFolderBrowser(parentPath));
      ideFolderList.appendChild(up);
    }
    const dirs = (data.items || []).filter(i => i.type === 'dir');
    dirs.forEach(item => {
      const div = document.createElement('div');
      div.className = 'fdr-item';
      div.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="#dcb67a" stroke="none"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>${item.name}`;
      div.addEventListener('click', () => loadFolderBrowser(item.path));
      ideFolderList.appendChild(div);
    });
    if (!dirs.length) {
      ideFolderList.innerHTML += '<div style="padding:10px 16px;color:#858585;font-size:12px">(no subfolders)</div>';
    }
  }

  document.getElementById('ide-folder-go-btn').addEventListener('click', () => {
    const p = ideFolderInput.value.trim();
    if (p) loadFolderBrowser(p);
  });
  ideFolderInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') loadFolderBrowser(ideFolderInput.value.trim());
  });
  document.getElementById('fdr-cancel').addEventListener('click', () => ideFolderModal.classList.remove('open'));
  document.getElementById('fdr-open').addEventListener('click', () => {
    ideFolderModal.classList.remove('open');
    setRoot(_folderPickerSelected);
  });
  ideFolderModal.addEventListener('click', e => {
    if (e.target === ideFolderModal) ideFolderModal.classList.remove('open');
  });

  // ── file tree ────────────────────────────────────
  async function setRoot(path) {
    _currentRoot = path;
    const name = path.split('/').pop() || path;
    ideTitleName.textContent = `Clixen IDE — ${name}`;
    const folderEl = document.getElementById('ide-tree-folder');
    folderEl.textContent = name.toUpperCase();
    folderEl.style.display = 'block';
    _loadBranch(path);
    await loadTree(path, ideFiles, 0);
    // Background semantic index so semantic_file_search works from the first question
    fetch('/ide/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    }).catch(() => {});
  }
  window._g4lSetRoot = setRoot;

  async function _loadBranch(path) {
    try {
      const r = await fetch('/fs/run', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ path: path + '/__git_branch__' })
      });
      // branch shown via separate endpoint below
    } catch(e) {}
    // Quick branch read via bash_exec through the run endpoint won't work directly.
    // Use a dedicated call.
    try {
      const r = await fetch('/fs/git-branch?path=' + encodeURIComponent(path));
      const d = await r.json();
      if (d.branch) sbBranch.textContent = d.branch;
    } catch(e) { sbBranch.textContent = '—'; }
  }

  async function loadTree(path, container, depth) {
    if (depth === 0) container.innerHTML = '<div style="padding:8px 16px;color:#858585;font-size:12px">Loading…</div>';
    let data;
    try {
      const r = await fetch('/fs/tree?path=' + encodeURIComponent(path) + '&depth=1');
      data = await r.json();
    } catch(e) {
      container.innerHTML = `<div style="padding:8px;color:#f48771;font-size:12px">Error loading</div>`;
      return;
    }
    if (depth === 0) container.innerHTML = '';
    renderTreeItems(data.items || [], container, depth);
  }

  function renderTreeItems(items, container, depth) {
    const indent = 8 + depth * 16;
    items.forEach(item => {
      const div = document.createElement('div');
      div.className = 'ide-tree-item' + (item.type === 'dir' ? ' dir' : '');
      div.style.paddingLeft = indent + 'px';
      const arrow = item.type === 'dir'
        ? `<span class="ti-arrow">▶</span>`
        : `<span class="ti-arrow"></span>`;
      const icon = `<span class="ti-icon">${_fileIcon(item.name, item.type)}</span>`;
      div.innerHTML = `${arrow}${icon}<span class="ti-name">${escHtml(item.name)}</span>`;

      if (item.type === 'file') {
        div.addEventListener('click', () => { _clearTreeActive(); div.classList.add('active'); openFile(item.path); });
      } else {
        let expanded = false, sub = null;
        div.addEventListener('click', async () => {
          expanded = !expanded;
          div.classList.toggle('expanded', expanded);
          if (expanded) {
            sub = document.createElement('div');
            await loadTree(item.path, sub, depth + 1);
            div.after(sub);
          } else { sub?.remove(); sub = null; }
        });
      }
      div.addEventListener('contextmenu', e => {
        e.preventDefault();
        _ctxTarget = item;
        showCtxMenu(e.clientX, e.clientY, item);
      });
      container.appendChild(div);
    });
  }

  function _clearTreeActive() {
    document.querySelectorAll('.ide-tree-item.active').forEach(el => el.classList.remove('active'));
  }

  function _fileIcon(name, type) {
    if (type === 'dir') return '<svg width="14" height="14" viewBox="0 0 24 24" fill="#dcb67a" stroke="none"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
    const ext = name.split('.').pop().toLowerCase();
    const colors = { py:'#3572A5', js:'#f7df1e', ts:'#3178c6', jsx:'#61dafb', tsx:'#61dafb',
      json:'#cbcb41', md:'#083fa1', html:'#e34f26', css:'#563d7c', rs:'#dea584',
      go:'#00add8', sh:'#89e051', yaml:'#cb171e', yml:'#cb171e', env:'#ecd53f',
      sql:'#e38c00', toml:'#9c4121' };
    const c = colors[ext] || '#cccccc';
    const letter = ext ? ext[0].toUpperCase() : '·';
    return `<svg width="14" height="14" viewBox="0 0 24 24"><rect x="3" y="2" width="18" height="20" rx="2" fill="${c}" opacity=".18"/><text x="12" y="16" text-anchor="middle" font-size="9" fill="${c}" font-family="monospace" font-weight="bold">${letter}</text></svg>`;
  }

  document.getElementById('ide-root-btn').addEventListener('click', e => { e.stopPropagation(); openFolderModal(_currentRoot || '~'); });
  document.getElementById('ide-newfile-btn')?.addEventListener('click', async () => {
    if (!_currentRoot) { openFolderModal(); return; }
    const name = prompt('New file name:');
    if (!name) return;
    const path = _currentRoot + '/' + name;
    await fetch('/fs/write', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ path, content: '' }) });
    await setRoot(_currentRoot);
    openFile(path);
  });

  // ── context menu ─────────────────────────────────
  function showCtxMenu(x, y, item) {
    ideCtxMenu.style.display = 'block';
    ideCtxMenu.style.left = Math.min(x, window.innerWidth - 180) + 'px';
    ideCtxMenu.style.top = Math.min(y, window.innerHeight - 150) + 'px';
  }
  document.addEventListener('click', () => { ideCtxMenu.style.display = 'none'; });
  ideCtxMenu.querySelectorAll('.ctx-item').forEach(el => {
    el.addEventListener('click', async () => {
      const cmd = el.dataset.cmd;
      if (!_ctxTarget) return;
      if (cmd === 'open' && _ctxTarget.type === 'file') openFile(_ctxTarget.path);
      if (cmd === 'newfile') {
        const name = prompt('New file name:');
        if (name) {
          const dir = _ctxTarget.type === 'dir' ? _ctxTarget.path : _ctxTarget.path.replace(/\/[^/]+$/, '');
          await fetch('/fs/write', { method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ path: dir + '/' + name, content: '' }) });
          setRoot(_currentRoot);
        }
      }
      if (cmd === 'copy-path') navigator.clipboard.writeText(_ctxTarget.path);
    });
  });

  // ── tabs + breadcrumbs ───────────────────────────
  function renderTabs() {
    ideTabs.innerHTML = '';
    Object.keys(_openFiles).forEach(path => {
      const f = _openFiles[path];
      const name = path.split('/').pop();
      const isActive = path === _activeFile;
      const tab = document.createElement('div');
      tab.className = 'ide-tab' + (isActive ? ' active' : '') + (f.modified ? ' modified' : '');
      tab.innerHTML = `
        <span class="ti-icon" style="margin-right:5px;font-size:13px">${_fileIcon(name,'file')}</span>
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(name)}</span>
        <span class="ide-tab-dot"></span>
        <button class="ide-tab-close" title="Close (middle-click)">×</button>`;
      tab.addEventListener('click', e => { if (!e.target.closest('.ide-tab-close')) switchTo(path); });
      tab.querySelector('.ide-tab-close').addEventListener('click', e => { e.stopPropagation(); closeTab(path); });
      tab.addEventListener('auxclick', e => { if (e.button === 1) closeTab(path); });
      ideTabs.appendChild(tab);
    });
    ideRunBtn.disabled = !_activeFile;
    _updateBreadcrumbs();
    // show/hide editor welcome
    if (ideWelcome) ideWelcome.style.display = _activeFile ? 'none' : 'flex';
  }

  function _updateBreadcrumbs() {
    ideBreadcrumbs.innerHTML = '';
    if (!_activeFile) return;
    const root = _currentRoot || '';
    let rel = _activeFile.startsWith(root) ? _activeFile.slice(root.length) : _activeFile;
    const parts = rel.replace(/^\//, '').split('/');
    parts.forEach((p, i) => {
      const span = document.createElement('span');
      span.className = 'bc-part' + (i === parts.length - 1 ? ' last' : '');
      span.textContent = p;
      ideBreadcrumbs.appendChild(span);
      if (i < parts.length - 1) {
        const sep = document.createElement('span');
        sep.className = 'bc-sep';
        sep.textContent = '›';
        ideBreadcrumbs.appendChild(sep);
      }
    });
    // status bar language
    const ext = _activeFile.split('.').pop().toLowerCase();
    const langNames = { py:'Python', js:'JavaScript', ts:'TypeScript', jsx:'JavaScript (JSX)',
      tsx:'TypeScript (TSX)', json:'JSON', md:'Markdown', sh:'Shell Script',
      html:'HTML', css:'CSS', sql:'SQL', yaml:'YAML', yml:'YAML',
      rs:'Rust', go:'Go', cpp:'C++', c:'C', java:'Java', toml:'TOML' };
    sbLang.textContent = langNames[ext] || 'Plain Text';
  }

  function switchTo(path) {
    if (_activeFile && _openFiles[_activeFile] && _editor) {
      _openFiles[_activeFile].content = _editor.getValue();
    }
    _activeFile = path;
    const f = _openFiles[path];
    if (_editor) {
      const lang = _detectLang(path);
      if (!f.editorModel) {
        f.editorModel = monaco.editor.createModel(f.content, lang);
      }
      _editor.setModel(f.editorModel);
      _editor.focus();
    }
    renderTabs();
  }

  function closeTab(path) {
    if (_openFiles[path]?.editorModel) _openFiles[path].editorModel.dispose();
    delete _openFiles[path];
    if (_activeFile === path) {
      const remaining = Object.keys(_openFiles);
      _activeFile = remaining.length ? remaining[remaining.length - 1] : null;
      if (_activeFile) switchTo(_activeFile);
      else { if (_editor) _editor.setModel(null); renderTabs(); }
    } else renderTabs();
  }

  function _detectLang(path) {
    const ext = path.split('.').pop().toLowerCase();
    const map = { py:'python', js:'javascript', ts:'typescript', jsx:'javascript',
                  tsx:'typescript', json:'json', md:'markdown', sh:'shell',
                  html:'html', css:'css', sql:'sql', yaml:'yaml', yml:'yaml',
                  toml:'ini', rs:'rust', go:'go', cpp:'cpp', c:'c', java:'java',
                  rb:'ruby', php:'php', swift:'swift', kt:'kotlin' };
    return map[ext] || 'plaintext';
  }

  // ── open file ────────────────────────────────────
  async function openFile(path) {
    if (_openFiles[path]) { switchTo(path); return; }
    try {
      const r = await fetch('/fs/read?path=' + encodeURIComponent(path));
      const d = await r.json();
      if (d.error) { ideOutput.textContent = '[error] ' + d.error; return; }
      _openFiles[path] = { content: d.content, modified: false, editorModel: null };
      switchTo(path);
    } catch(e) {
      ideOutput.textContent = '[error] ' + e;
    }
  }

  // ── save ─────────────────────────────────────────
  async function saveActive() {
    if (!_activeFile || !_editor) return;
    const content = _editor.getValue();
    _openFiles[_activeFile].content = content;
    _openFiles[_activeFile].modified = false;
    renderTabs();
    await fetch('/fs/write', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ path: _activeFile, content }),
    });
  }

  // ── run ──────────────────────────────────────────
  ideRunBtn.addEventListener('click', async () => {
    if (!_activeFile) return;
    await saveActive();
    ideRunBtn.disabled = true;
    ideRunBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg> Running…';
    ideOutput.innerHTML = `<span class="ide-out-meta">▶ Running ${_activeFile.split('/').pop()}…</span>\n`;
    ideOutputWrap.classList.remove('collapsed');
    try {
      const r = await fetch('/fs/run', { method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ path: _activeFile }) });
      const d = await r.json();
      const out = d.output || '';
      const exitMatch = out.match(/\[exit code:\s*(\d+)\]/);
      const exitCode = exitMatch ? parseInt(exitMatch[1]) : 0;
      const isErr = exitCode !== 0 || out.includes('[error]') || out.includes('Traceback') || out.includes('Error:');
      ideOutput.innerHTML =
        `<span class="ide-out-meta">▶ ${_activeFile.split('/').pop()} — ${new Date().toLocaleTimeString()}</span>\n` +
        `<span class="${isErr ? 'ide-out-err' : 'ide-out-ok'}">${escHtml(out)}</span>`;
      // Auto-feed failures to IDE agent so it can fix them
      if (isErr && ideInput) {
        const fname = _activeFile.split('/').pop();
        ideInput.value =
          `\`${fname}\` failed (exit ${exitCode}):\n\`\`\`\n${out.slice(0, 2000)}\n\`\`\`\nPlease identify and fix the error.`;
        ideSend_();
      }
    } catch(e) {
      ideOutput.innerHTML = `<span class="ide-out-err">${e}</span>`;
    } finally {
      ideRunBtn.disabled = false;
      ideRunBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run';
    }
  });

  // panel tabs
  document.querySelectorAll('.ide-panel-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.ide-panel-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      ideOutputWrap.classList.remove('collapsed');
    });
  });
  document.getElementById('ide-collapse-btn')?.addEventListener('click', () => {
    ideOutputWrap.classList.toggle('collapsed');
  });
  document.getElementById('ide-clear-btn')?.addEventListener('click', () => {
    ideOutput.innerHTML = '<span class="ide-out-meta">Output cleared.</span>';
  });

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── IDE chat ─────────────────────────────────────
  function _ideAppend(role, text) {
    const row = document.createElement('div');
    row.className = 'msg-row ' + (role === 'user' ? 'user' : 'bot');
    const sender = document.createElement('div');
    sender.className = 'msg-sender';
    sender.textContent = role === 'user' ? 'You' : 'Assistant';
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    // escHtml for user input; marked.parse (same as main chat) for assistant markdown
    bubble.innerHTML = role === 'user' ? escHtml(text) : marked.parse(text); // safe: user path escaped, assistant path is model output
    row.appendChild(sender);
    row.appendChild(bubble);
    ideMessages.appendChild(row);
    ideMessages.scrollTop = ideMessages.scrollHeight;
    return bubble;
  }

  async function ideSend_() {
    const msg = ideInput.value.trim();
    if (!msg) return;
    ideInput.value = '';
    _ideAppend('user', msg);

    // Inject project root + active file context
    let context = '';
    if (_currentRoot) {
      context += `[Project root: ${_currentRoot}]\n`;
    }
    if (_activeFile && _editor) {
      const content = _editor.getValue().slice(0, 3000);
      const name = _activeFile.split('/').pop();
      context += `[Active file: ${_activeFile}]\n\`\`\`\n${content}\n\`\`\`\n\n`;
    }

    const ideModel = window._ideModelOverride || '';
    const bubble = _ideAppend('assistant', '…');
    let full = '';
    const url = '/chat/stream?message=' + encodeURIComponent(context + msg)
      + '&chat_id=' + encodeURIComponent(_ideChatId)
      + (_currentRoot ? '&root=' + encodeURIComponent(_currentRoot) : '')
      + (ideModel ? '&model=' + encodeURIComponent(ideModel) : '');
    const es = new EventSource(url);
    es.onmessage = e => {
      const d = JSON.parse(e.data);
      if (d.token) { full += d.token; bubble.textContent = stripMd(full); ideMessages.scrollTop = ideMessages.scrollHeight; }
      if (d.done) {
        bubble.innerHTML = marked.parse(full);
        if (d.model) {
          const badge = document.createElement('span');
          badge.className = 'ide-model-badge';
          badge.textContent = d.model + (d.intent ? ' · ' + d.intent : '');
          bubble.appendChild(badge);
        }
        es.close();
      }
    };
    es.onerror = () => es.close();
  }

  ideSend.addEventListener('click', ideSend_);
  ideInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ideSend_(); }
  });
  ideInput.addEventListener('input', () => {
    ideInput.style.height = 'auto';
    ideInput.style.height = Math.min(ideInput.scrollHeight, 120) + 'px';
  });

  // ── sidebar toggle & persistence ──────────────────
  const sidebarEl = document.getElementById('sidebar');
  const sidebarOverlayEl = document.getElementById('sidebar-overlay');
  const sidebarToggleBtn = document.getElementById('sidebar-toggle');

  // Load saved state on desktop
  if (window.innerWidth >= 768 && localStorage.getItem('sidebar-collapsed') === 'true') {
    sidebarEl.classList.add('collapsed');
  }

  function openSidebar() {
    sidebarEl.classList.add('open');
    sidebarOverlayEl.classList.add('visible');
  }
  function closeSidebar() {
    sidebarEl.classList.remove('open');
    sidebarOverlayEl.classList.remove('visible');
  }

  sidebarToggleBtn.addEventListener('click', () => {
    if (window.innerWidth < 768) {
      sidebarEl.classList.contains('open') ? closeSidebar() : openSidebar();
    } else {
      sidebarEl.classList.toggle('collapsed');
      localStorage.setItem('sidebar-collapsed', sidebarEl.classList.contains('collapsed'));
    }
  });
  sidebarOverlayEl.addEventListener('click', closeSidebar);
  // close when a session is selected on mobile
  document.getElementById('sessions').addEventListener('click', () => {
    if (window.innerWidth < 768) closeSidebar();
  });

})();
