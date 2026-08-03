# UI: Green Hot Dot + Typing Words Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Show a dot for every model in the picker — green when hot, gray when cold. (2) Add cycling random words to the typing indicator while the model is running.

**Architecture:** Both changes are pure frontend inside `chat_ui.py` (single embedded HTML/CSS/JS file). No backend changes. The hot-dot change modifies `refreshHotModels()` (~line 2001). The typing-words change modifies `showTyping()` (~line 1945) and `removeTyping()` (~line 1950), using DOM methods (not innerHTML) to create the new word element.

**Tech Stack:** Vanilla JS, CSS, single-file FastAPI+HTML (`tools-harness/chat_ui.py`)

---

## File Map

| Action | Path |
|--------|------|
| Modify | `tools-harness/chat_ui.py` — CSS (~line 492), `refreshHotModels()` (~line 2001), `showTyping()` (~line 1945), `removeTyping()` (~line 1950) |
| Sync | `~/developer/clixen/tools-harness/chat_ui.py` |

---

## Task 1: Green hot dot + gray cold dot in model picker

**Files:**
- Modify: `tools-harness/chat_ui.py` — `refreshHotModels()` function (~line 1995)

- [ ] **Step 1: Read lines 1995–2012 to confirm exact current code**

```bash
sed -n '1995,2012p' /Users/kalinovdameus/Developer/clixen/tools-harness/chat_ui.py
```

- [ ] **Step 2: Replace `refreshHotModels` function body**

Find this exact block:

```javascript
function refreshHotModels() {
  fetch('/models/hot').then(r=>r.json()).then(({hot})=>{
    [document.getElementById('model-select'), document.getElementById('ide-model-select')]
      .filter(Boolean)
      .forEach(sel => {
        Array.from(sel.options).forEach(opt => {
          if (!opt.value) return; // skip "Auto" / empty option
          const isHot = hot.includes(opt.value);
          opt.text = opt.text.replace(/^● /, '');
          if(isHot) opt.text = '● ' + opt.text;
          opt.style.color = isHot ? '#22c55e' : '';
        });
      });
  }).catch(()=>{});
}
```

Replace with:

```javascript
function refreshHotModels() {
  fetch('/models/hot').then(r=>r.json()).then(({hot})=>{
    [document.getElementById('model-select'), document.getElementById('ide-model-select')]
      .filter(Boolean)
      .forEach(sel => {
        Array.from(sel.options).forEach(opt => {
          if (!opt.value) return; // skip "Auto" / empty option
          const isHot = hot.includes(opt.value);
          opt.text = opt.text.replace(/^● /, '');
          opt.text = '● ' + opt.text;
          opt.style.color = isHot ? '#22c55e' : '#555';
        });
      });
  }).catch(()=>{});
}
```

**What changed:** `●` is always prepended (not only for hot models). Color is `#22c55e` (green) for hot, `#555` (dark gray) for cold.

- [ ] **Step 3: Verify Python syntax**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
python3 -c "
import ast, sys
with open('chat_ui.py') as f: src = f.read()
try:
    ast.parse(src); print('OK')
except SyntaxError as e:
    print(f'SyntaxError: {e}'); sys.exit(1)
"
```

Expected: `OK`

- [ ] **Step 4: Sync to lowercase repo**

```bash
cp ~/Developer/clixen/tools-harness/chat_ui.py \
   ~/developer/clixen/tools-harness/chat_ui.py
```

---

## Task 2: Cycling words in typing indicator

**Files:**
- Modify: `tools-harness/chat_ui.py` — CSS (~line 492), `showTyping()` (~line 1945), `removeTyping()` (~line 1950)

- [ ] **Step 1: Add CSS for the typing word**

Find this exact CSS block:

```css
.typing-bubble{background:var(--panel);border:1px solid var(--border);
  border-radius:0 var(--radius-lg) var(--radius-lg) var(--radius-lg);
  padding:11px 16px;display:flex;gap:5px;align-items:center}
.dot{width:6px;height:6px;border-radius:50%;background:var(--accent);animation:blink 1.2s infinite}
.dot:nth-child(2){animation-delay:.2s}
.dot:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.2}40%{opacity:1}}
```

Replace with:

```css
.typing-bubble{background:var(--panel);border:1px solid var(--border);
  border-radius:0 var(--radius-lg) var(--radius-lg) var(--radius-lg);
  padding:11px 16px;display:flex;gap:5px;align-items:center}
.dot{width:6px;height:6px;border-radius:50%;background:var(--accent);animation:blink 1.2s infinite}
.dot:nth-child(2){animation-delay:.2s}
.dot:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.2}40%{opacity:1}}
.typing-word{font-size:11px;color:var(--text-muted);margin-left:4px;font-style:italic;
  animation:wordFade .35s ease-out}
@keyframes wordFade{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}
```

- [ ] **Step 2: Replace `showTyping()` and `removeTyping()`**

Find this exact block:

```javascript
function showTyping() {
  const w=document.createElement('div'); w.className='typing-wrap'; w.id='typing-indicator';
  w.innerHTML='<div class="typing-bubble"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';
  document.getElementById('messages').appendChild(w); scrollBottom();
}
function removeTyping() { const e=document.getElementById('typing-indicator'); if(e)e.remove(); }
```

Replace with:

```javascript
const _typingWords = [
  'thinking','computing','hallucinating','plotting','scheming',
  'cooking','searching','calibrating','pondering','confabulating',
  'theorizing','manifesting','spellcasting','fumbling','cogitating',
  'deliberating','synthesizing','fistulating','vibing','processing',
];
let _typingWordTimer = null;

function showTyping() {
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
    void el.offsetWidth; // force reflow to restart CSS animation
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
```

- [ ] **Step 3: Verify Python syntax**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
python3 -c "
import ast, sys
with open('chat_ui.py') as f: src = f.read()
try:
    ast.parse(src); print('OK')
except SyntaxError as e:
    print(f'SyntaxError: {e}'); sys.exit(1)
"
```

Expected: `OK`

- [ ] **Step 4: Sync to lowercase repo**

```bash
cp ~/Developer/clixen/tools-harness/chat_ui.py \
   ~/developer/clixen/tools-harness/chat_ui.py
```
