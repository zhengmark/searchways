/** Main application — state, event binding, initialization. */

import { $, renderMarkdown, authFetch } from './utils.js';
import { renderMermaid } from './mermaid-renderer.js';
import { initMap, getMap, clearLayers, renderStops, invalidateSize } from './map.js';
import { enableEditMode } from './route-editor.js';

// ── State ─────────────────────────────────────
export const AppState = {
  sessionId: null,
  username: null,
  lastResultData: null,
  ui: { mode: 'idle' },  // idle | loading | editing | confirmed
};

// ── History ───────────────────────────────────
const HISTORY_KEY = 'route_history_v1';

function histKey() {
  return AppState.username ? HISTORY_KEY + '_' + AppState.username : HISTORY_KEY;
}
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(histKey()) || '[]'); }
  catch { return []; }
}
function saveHistory(items) {
  localStorage.setItem(histKey(), JSON.stringify(items.slice(0, 20)));
}
function addHistory(input, data) {
  const items = loadHistory();
  items.unshift({
    time: new Date().toLocaleString(),
    input,
    data,
    narration: data.narration || '',
    mermaid: data.mermaid || '',
    stops: data.stops || [],
  });
  saveHistory(items);
  renderHistoryList();
}
function renderHistoryList() {
  const list = $('historyList');
  if (!list) return;
  const items = loadHistory();
  if (!items.length) { list.textContent = '暂无记录'; return; }
  list.innerHTML = '';
  items.forEach((h, i) => {
    const div = document.createElement('div');
    div.className = 'history-item';
    div.innerHTML = `<span>${h.input.slice(0, 40)}${h.input.length > 40 ? '...' : ''}</span>`;
    const right = document.createElement('span');
    right.style.cssText = 'display:flex;align-items:center;gap:8px';
    const time = document.createElement('span');
    time.className = 'hist-time'; time.textContent = h.time;
    right.appendChild(time);
    const del = document.createElement('button');
    del.className = 'hist-delete'; del.textContent = '×'; del.title = '删除';
    del.onclick = e => { e.stopPropagation(); deleteHistory(i); };
    right.appendChild(del);
    div.appendChild(right);
    div.onclick = () => {
      if (h.data) showResult(h.data);
    };
    list.appendChild(div);
  });
}
function deleteHistory(idx) {
  const items = loadHistory();
  items.splice(idx, 1);
  saveHistory(items);
  renderHistoryList();
}

// ── Progress ──────────────────────────────────
let progressCount = 0;
const PROGRESS_STAGES = { '📍': 15, '🧠': 30, '🤖': 45, '🔧': 65, '✅': 100 };
let lastPct = 0;

function addProgress(emoji, msg) {
  progressCount++;
  const status = $('status');
  const el = document.createElement('span');
  el.className = 'progress-step';
  el.textContent = emoji + ' ' + msg;
  status.appendChild(el);
  status.scrollTop = status.scrollHeight;

  const wrap = $('progressWrap');
  if (wrap) {
    wrap.style.display = 'block';
    const pct = PROGRESS_STAGES[emoji] || (lastPct + 5);
    lastPct = Math.min(pct, 98);
    $('progressFill').style.width = lastPct + '%';
    $('progressText').textContent = lastPct + '%';
  }

  const toggle = $('progressToggle');
  if (toggle) {
    toggle.textContent = progressCount + ' 步 · 点击收起 ▴';
    toggle.classList.add('show');
  }
}
function toggleProgress() {
  const status = $('status');
  const toggle = $('progressToggle');
  if (status.classList.contains('collapsed')) {
    status.classList.remove('collapsed');
    toggle.textContent = progressCount + ' 步 · 点击收起 ▴';
  } else {
    status.classList.add('collapsed');
    toggle.textContent = progressCount + ' 步 · 点击展开 ▾';
  }
}
function finishProgress() {
  $('progressFill').style.width = '100%';
  $('progressText').textContent = '100%';
  setTimeout(() => { $('progressWrap').style.display = 'none'; lastPct = 0; }, 1500);
}

// ── Skeleton ──────────────────────────────────
function showSkeleton() {
  $('emptyState').style.display = 'none';
  $('skeletonArea').classList.add('active');
  $('resultArea').classList.remove('active');
  $('editorArea').classList.remove('active');
  $('confirmedArea').classList.remove('active');
  $('errorCard').classList.remove('active');
}
function hideSkeleton() {
  $('skeletonArea').classList.remove('active');
}

// ── Error ─────────────────────────────────────
function showError(msg) {
  $('errorCard').classList.add('active');
  $('errorMsg').textContent = msg;
  $('resultArea').classList.remove('active');
  $('editorArea').classList.remove('active');
  $('statsBar').classList.remove('active');
  $('emptyState').style.display = 'none';
}

// ── Result (legacy mode, no corridor) ────────
function showResult(data) {
  // Always enter interactive edit mode (with mock fallback if needed)
  AppState.sessionId = data.session_id || null;
  AppState.lastResultData = data;
  AppState.ui.mode = 'editing';
  hideSkeleton();
  finishProgress();
  $('errorCard').classList.remove('active');
  $('emptyState').style.display = 'none';
  $('resultArea').classList.remove('active');
  $('confirmedArea').classList.remove('active');
  $('statsBar').classList.add('active');
  enableEditMode(data.session_id, data);
  return;
}

// ── Plan (SSE) ────────────────────────────────
async function planRoute() {
  const input = $('userInput').value.trim();
  if (!input) return;

  const btn = $('planBtn');
  btn.disabled = true;
  $('status').innerHTML = '';
  $('status').classList.remove('collapsed');
  $('progressToggle').classList.remove('show');
  progressCount = 0;
  showSkeleton();

  try {
    const resp = await authFetch('/api/plan/stream', {
      method: 'POST',
      body: JSON.stringify({ query: input }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.type === 'progress') {
            addProgress(evt.emoji, evt.msg);
          } else if (evt.type === 'result') {
            AppState.sessionId = evt.session_id;
            AppState.lastResultData = evt;
            btn.disabled = false;
            hideSkeleton();
            initMap('map');
            showResult(evt);
            addHistory(input, evt);
          } else if (evt.type === 'error') {
            btn.disabled = false;
            hideSkeleton();
            showError(evt.error);
          }
        } catch (e) { /* skip malformed events */ }
      }
    }
  } catch (err) {
    btn.disabled = false;
    hideSkeleton();
    showError(err.message);
  }
}

// ── Chat / Modify ─────────────────────────────
async function modifyRoute() {
  const input = $('chatInput').value.trim();
  if (!input || !AppState.sessionId) return;

  const status = $('chatStatus');
  const btn = $('chatBtn');
  status.textContent = '修改中...';
  btn.disabled = true;

  try {
    const resp = await authFetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ query: input, session_id: AppState.sessionId }),
    });
    const data = await resp.json();
    if (data.error) {
      status.textContent = '修改失败：' + data.error;
      btn.disabled = false;
      return;
    }
    status.textContent = '修改完成';
    btn.disabled = false;
    $('chatInput').value = '';
    AppState.lastResultData = data;
    initMap('map');
    showResult(data);
    addHistory(input, data);
  } catch (err) {
    status.textContent = '修改失败：' + err.message;
    btn.disabled = false;
  }
}

// ── Share ─────────────────────────────────────
function shareRoute() {
  if (!AppState.lastResultData) return;
  const data = AppState.lastResultData;
  const stops = data.stops || [];
  const shareText = '🗺️ 现在就出发\n' + stops.map((s, i) => (i + 1) + '. ' + s.name).join('\n') + '\n\n' + (data.narration || '').slice(0, 200);
  navigator.clipboard.writeText(shareText).then(() => {
    $('shareMsg').textContent = '已复制到剪贴板！';
    setTimeout(() => { $('shareMsg').textContent = ''; }, 3000);
  }).catch(() => {
    $('shareMsg').textContent = '复制失败，请手动复制';
  });
}

// ── Quick tags ─────────────────────────────────
function setTag(text) {
  $('userInput').value = text;
  $('userInput').focus();
}

// ── Init ──────────────────────────────────────
function init() {
  // Initialize the editor map on page load
  initMap('editorMap');
  getMap()?.setView([34.26, 108.94], 12);

  // Show initial sidebar state
  $('routeSummary').innerHTML = '<span>⏱ -- min</span><span>📏 -- km</span><span>📍 -- 站</span>';
  $('stopList').innerHTML = '<li style="color:#94a3b8;padding:12px 8px;font-size:0.85rem;">输入出行需求后，路线和推荐POI将显示在这里</li>';

  // Plan button
  $('planBtn').onclick = planRoute;
  $('userInput').onkeydown = e => {
    if (e.key === 'Enter') planRoute();
  };

  // Chat
  $('chatBtn').onclick = modifyRoute;
  $('chatInput').onkeydown = e => {
    if (e.key === 'Enter') modifyRoute();
  };

  // Quick tags
  document.querySelectorAll('.tag').forEach(el => {
    el.onclick = () => setTag(el.dataset.query || el.textContent);
  });

  // Progress toggle
  $('progressToggle').onclick = toggleProgress;

  // Share
  $('shareBar').querySelector('.share-btn').onclick = shareRoute;

  // Retry button in error card
  const retryBtn = $('errorCard').querySelector('.retry-btn');
  if (retryBtn) retryBtn.onclick = planRoute;

  // Render history
  renderHistoryList();

  // Update username from auth module
  if (typeof currentUsername !== 'undefined') {
    AppState.username = currentUsername;
  }
}

// ── Bootstrap ─────────────────────────────────
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

// Expose for inline onclick fallbacks
window.planRoute = planRoute;
window.modifyRoute = modifyRoute;
window.setTag = setTag;
window.toggleProgress = toggleProgress;
window.shareRoute = shareRoute;
