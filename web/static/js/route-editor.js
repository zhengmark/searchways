/** Route editor v3 — fixed route + recommended POIs + user-selectable stops.
 *
 *  Model:
 *  - System plans a base route (fixed stops) + recommends alternative POIs
 *  - User clicks recommended POIs to ADD them to route
 *  - User can REMOVE stops (except origin/dest) → they go back to recommendations
 *  - User can REORDER stops in sidebar (drag & drop)
 *  - Segments auto-rebuild when stops change
 *  - Confirm → generate narration
 */

import { authFetch, $, formatDuration, renderMarkdown, haversineKm } from './utils.js';
import {
  getMap, initMap, clearLayers, renderStops, renderSegments, renderRecommended, invalidateSize,
  setRecClickCallback,
} from './map.js';

let _sessionId = null;
let _routeData = null;  // { stops, segments, alternatives, summary }
let _dragSrcIdx = null;

// ═══════════════════════════════════════════════
// Mock data
// ═══════════════════════════════════════════════

function _mockData() {
  return {
    stops: [
      { id: 's0', name: '钟楼', lat: 34.2594, lng: 108.9470, isOrigin: true, address: '西安市中心' },
      { id: 's1', name: '回民街', lat: 34.2634, lng: 108.9450, category: '美食街', rating: 4.5, address: '莲湖区' },
      { id: 's2', name: '碑林博物馆', lat: 34.2550, lng: 108.9480, category: '博物馆', rating: 4.7, address: '碑林区' },
      { id: 's3', name: '西安城墙南门', lat: 34.2520, lng: 108.9500, category: '景点', rating: 4.8, address: '南门' },
      { id: 's4', name: '大唐不夜城', lat: 34.2140, lng: 108.9623, category: '景点', rating: 4.6, isDest: true, address: '雁塔区' },
    ],
    recomms: [
      { id: 'r1', name: '德发长饺子馆', lat: 34.2610, lng: 108.9430, category: '美食', rating: 4.4, match: 0.90, address: '莲湖区西大街' },
      { id: 'r2', name: '大雁塔·大慈恩寺', lat: 34.2195, lng: 108.9614, category: '景点', rating: 4.8, match: 0.85, address: '雁塔区' },
      { id: 'r3', name: '陕西历史博物馆', lat: 34.2240, lng: 108.9540, category: '博物馆', rating: 4.9, match: 0.80, address: '雁塔区小寨东路' },
      { id: 'r4', name: '老孙家泡馍', lat: 34.2620, lng: 108.9480, category: '美食', rating: 4.3, match: 0.78, address: '莲湖区' },
      { id: 'r5', name: '书院门文化街', lat: 34.2530, lng: 108.9450, category: '文化街区', rating: 4.5, match: 0.75, address: '碑林区' },
      { id: 'r6', name: '兴庆宫公园', lat: 34.2500, lng: 108.9850, category: '公园', rating: 4.2, match: 0.70, address: '碑林区' },
      { id: 'r7', name: '湘子庙', lat: 34.2580, lng: 108.9420, category: '古迹', rating: 4.6, match: 0.68, address: '碑林区' },
      { id: 'r8', name: '永兴坊美食街', lat: 34.2680, lng: 108.9580, category: '美食', rating: 4.4, match: 0.65, address: '新城区' },
      { id: 'r9', name: '大唐芙蓉园', lat: 34.2160, lng: 108.9700, category: '景点', rating: 4.7, match: 0.62, address: '雁塔区' },
      { id: 'r10', name: '赛格国际购物中心', lat: 34.2250, lng: 108.9500, category: '购物', rating: 4.3, match: 0.60, address: '雁塔区小寨' },
      { id: 'r11', name: '钟楼星巴克甄选', lat: 34.2580, lng: 108.9460, category: '咖啡', rating: 4.2, match: 0.58, address: '碑林区' },
      { id: 'r12', name: '长安大排档', lat: 34.2640, lng: 108.9520, category: '美食', rating: 4.5, match: 0.55, address: '莲湖区' },
      { id: 'r13', name: '西安音乐厅', lat: 34.2150, lng: 108.9650, category: '文化', rating: 4.6, match: 0.52, address: '雁塔区' },
      { id: 'r14', name: '半坡国际艺术区', lat: 34.2750, lng: 109.0500, category: '艺术区', rating: 4.3, match: 0.50, address: '灞桥区' },
      { id: 'r15', name: '曲江书城', lat: 34.2100, lng: 108.9700, category: '书店', rating: 4.4, match: 0.48, address: '雁塔区' },
      { id: 'r16', name: '小雁塔·荐福寺', lat: 34.2420, lng: 108.9380, category: '古迹', rating: 4.5, match: 0.45, address: '碑林区' },
    ],
  };
}

// ═══════════════════════════════════════════════
// Entry
// ═══════════════════════════════════════════════

export function enableEditMode(sessionId, data) {
  _sessionId = sessionId;

  if (!data.stops || data.stops.length < 3) {
    _showEditorError('数据不足，无法进入编辑模式。请重新规划路线。');
    return;
  }
  _routeData = _realToModel(data);
  if (!_routeData.segments || _routeData.segments.length === 0) {
    _routeData.segments = _computeSegments();
    _routeData.summary = _computeSummary();
  }

  // Show editor layout
  $('emptyState').style.display = 'none';
  $('skeletonArea').classList.remove('active');
  $('resultArea').classList.remove('active');
  $('confirmedArea').classList.remove('active');
  $('editorArea').classList.add('active');
  $('statsBar').classList.add('active');

  // Initialize map on editorMap container
  initMap('editorMap');
  invalidateSize();

  renderAll();

  // Recommended POI click → add to route
  setRecClickCallback((rec) => _addRecToRoute(rec));

  // Buttons
  $('confirmBtn').onclick = () => _onConfirm();
  $('editorChatInput').onkeydown = e => { if (e.key === 'Enter') _onChatModify(); };
  $('editorChatBtn').onclick = () => _onChatModify();
}

// ═══════════════════════════════════════════════
// Convert backend data → internal model
// ═══════════════════════════════════════════════

function _realToModel(data) {
  const stops = (data.stops || []).map((s, i) => ({
    id: s.poi_id || `s${i}`,
    name: s.name, lat: s.lat, lng: s.lng,
    rating: s.rating, price: s.price,
    address: s.address || '', category: s.category || '',
    isOrigin: i === 0,
    isDest: i === (data.stops || []).length - 1 && i > 0,
  }));

  let segments = [];
  if (data.segments && data.segments.length > 0) {
    segments = data.segments.map((seg, i) => ({
      from: stops[i]?.id || `s${i}`,
      to: stops[i + 1]?.id || `s${i + 1}`,
      transport: seg.transport || '步行',
      duration_min: seg.duration_min || Math.round((seg.duration || 0) / 60),
      distance_m: seg.distance_m || seg.distance || 0,
    }));
  }

  // Filter corridor POIs: within 3km, not in stops, pick evenly from 5 route segments
  const stopNames = new Set(stops.map(s => s.name));
  let pool = (data.corridor_pois || [])
    .filter(p => {
      if (p.selected) return false;
      if (stopNames.has(p.name)) return false;
      if (p.perpendicular_km != null && p.perpendicular_km > 3.0) return false;
      return true;
    })
    .map(p => ({
      id: p.id, name: p.name, lat: p.lat, lng: p.lng,
      category: p.category || '', rating: p.rating,
      proj: p.projection_ratio != null ? p.projection_ratio : 0.5,
      perp: p.perpendicular_km || 0,
      match: p.perpendicular_km != null ? Math.max(0, 1 - p.perpendicular_km / 3) : 0.7,
      address: p.address || '',
      _dist_km: p._aoi_dist_m != null ? p._aoi_dist_m / 1000 : null,
    }));

  // Hard cap: no alternatives >5km from origin (frontend safeguard)
  const MAX_ALT_DIST_KM = 5.0;
  if (stops.length > 0) {
    const origin = stops[0];
    pool = pool.filter(p => {
      const d = p._dist_km != null ? p._dist_km : haversineKm(origin.lat, origin.lng, p.lat, p.lng);
      if (d > MAX_ALT_DIST_KM) return false;
      p._dist_km = d;
      return true;
    });
  }

  // Pick top 5 from each of 5 projection ranges for even spatial distribution
  const projRanges = [
    { lo: 0.0, hi: 0.2 }, { lo: 0.2, hi: 0.4 }, { lo: 0.4, hi: 0.6 },
    { lo: 0.6, hi: 0.8 }, { lo: 0.8, hi: 1.0 },
  ];
  const recomms = [];
  for (const seg of projRanges) {
    const inSeg = pool.filter(p => p.proj >= seg.lo && p.proj < seg.hi);
    inSeg.sort((a, b) => (b.rating || 0) - (a.rating || 0));
    recomms.push(...inSeg.slice(0, 5));
  }
  // If total less than 20, fill up with best remaining
  if (recomms.length < 20) {
    const picked = new Set(recomms.map(r => r.id));
    const rest = pool.filter(p => !picked.has(p.id))
      .sort((a, b) => (b.rating || 0) - (a.rating || 0));
    recomms.push(...rest.slice(0, 25 - recomms.length));
  }

  return {
    stops, segments, recomms,
    summary: _computeSummaryFrom(stops, segments),
  };
}

function _computeSummaryFrom(stops, segments) {
  return {
    duration: segments.reduce((s, seg) => s + (seg.duration_min || 0), 0),
    distance: segments.reduce((s, seg) => s + (seg.distance_m || 0), 0),
    stops: Math.max(0, stops.length - 2),
  };
}

// ═══════════════════════════════════════════════
// Compute segments from stops
// ═══════════════════════════════════════════════

function _computeSegments() {
  const stops = _routeData.stops;
  const segs = [];
  for (let i = 1; i < stops.length; i++) {
    const from = stops[i - 1];
    const to = stops[i];
    const dx = (to.lng - from.lng) * 96000;
    const dy = (to.lat - from.lat) * 111000;
    const dist = Math.sqrt(dx * dx + dy * dy);
    let transport = '步行', duration = Math.round(dist / 80);
    if (dist > 4000) { transport = '地铁2号线'; duration = Math.round(dist / 300); }
    else if (dist > 1500) { transport = '公交'; duration = Math.round(dist / 200); }
    segs.push({
      from: from.id, to: to.id,
      transport, duration_min: Math.max(3, duration),
      distance_m: Math.round(dist),
      line_name: dist > 4000 ? '地铁2号线' : null,
      start_stop: dist > 4000 ? from.name : null,
      end_stop: dist > 4000 ? to.name : null,
    });
  }
  return segs;
}

function _computeSummary() {
  const segs = _routeData.segments || [];
  return {
    duration: segs.reduce((s, seg) => s + (seg.duration_min || 0), 0),
    distance: segs.reduce((s, seg) => s + (seg.distance_m || 0), 0),
    stops: Math.max(0, (_routeData.stops || []).length - 2),
  };
}

// ═══════════════════════════════════════════════
// Render
// ═══════════════════════════════════════════════

function renderAll() {
  clearLayers();
  if (!_routeData) return;

  renderRecommended(_routeData.recomms || []);
  renderSegments(_routeData.segments || [], _routeData.stops || []);
  renderStops(_routeData.stops || []);
  _renderSidebar();
  _updateStats();
  getMap()?.invalidateSize();
}

function _updateStats() {
  const s = _routeData.summary || _computeSummary();
  $('statDuration').textContent = s.duration || 0;
  $('statDistance').textContent = ((s.distance || 0) / 1000).toFixed(1);
  $('statStops').textContent = s.stops || 0;
  $('statScore').textContent = '--';
}

// ═══════════════════════════════════════════════
// Sidebar
// ═══════════════════════════════════════════════

function _renderSidebar() {
  const stops = _routeData.stops || [];
  const segs = _routeData.segments || [];
  const recomms = _routeData.recomms || [];
  const s = _routeData.summary || _computeSummary();

  // Summary
  $('routeSummary').innerHTML = `
    <span>⏱ ${s.duration || 0} min</span>
    <span>📏 ${((s.distance || 0) / 1000).toFixed(1)} km</span>
    <span>📍 ${s.stops || 0} 站</span>
  `;

  // Route stops
  const list = $('stopList');
  list.innerHTML = '';

  stops.forEach((stop, i) => {
    // Segment info before each stop (except first)
    if (i > 0 && segs[i - 1]) {
      const seg = segs[i - 1];
      const st = _transportStyle(seg.transport);
      const segEl = document.createElement('li');
      segEl.className = 'segment-info-item';
      segEl.innerHTML = `<span class="seg-icon">${st.icon}</span>
        <span class="seg-mode">${seg.transport}</span>
        <span class="seg-dur">${seg.duration_min || 0}min</span>`;
      segEl.title = `${seg.transport} ${seg.duration_min || 0}分钟 / ${((seg.distance_m || 0) / 1000).toFixed(1)}km`;
      segEl.onclick = () => _zoomToSegment(seg, i - 1);
      list.appendChild(segEl);
    }

    // Stop item
    const li = document.createElement('li');
    li.className = 'stop-item';
    li.dataset.index = i;
    li.dataset.stopId = stop.id;

    // Drag handle (only for non-origin, non-dest stops)
    const canDrag = !stop.isOrigin && !stop.isDest;
    if (canDrag) {
      li.draggable = true;
      const handle = document.createElement('span');
      handle.className = 'stop-drag-handle';
      handle.innerHTML = '⋮⋮';
      handle.title = '拖动排序';
      li.appendChild(handle);
    } else {
      const sp = document.createElement('span');
      sp.className = 'stop-drag-handle';
      sp.style.visibility = 'hidden';
      sp.innerHTML = '⋮⋮';
      li.appendChild(sp);
    }

    // Badge
    const badge = document.createElement('span');
    badge.className = 'stop-num';
    if (stop.isOrigin) { badge.textContent = '起'; badge.style.background = '#22c55e'; }
    else if (stop.isDest) { badge.textContent = '终'; badge.style.background = '#f59e0b'; }
    else { badge.textContent = i; }
    li.appendChild(badge);

    // Name + rating
    const nameEl = document.createElement('span');
    nameEl.className = 'stop-name';
    nameEl.textContent = stop.name;
    if (stop.rating) {
      const sr = document.createElement('span');
      sr.className = 'stop-rating';
      sr.textContent = ` ⭐${stop.rating}`;
      nameEl.appendChild(sr);
    }
    li.appendChild(nameEl);

    // Remove button (only for non-origin, non-dest)
    if (canDrag) {
      const rm = document.createElement('button');
      rm.className = 'stop-remove';
      rm.textContent = '×';
      rm.title = '从路线中移除';
      rm.onclick = (e) => { e.stopPropagation(); _removeStop(i); };
      li.appendChild(rm);
    }

    // Drag events
    if (canDrag) {
      li.addEventListener('dragstart', _onDragStart);
      li.addEventListener('dragover', _onDragOver);
      li.addEventListener('dragenter', _onDragEnter);
      li.addEventListener('dragleave', _onDragLeave);
      li.addEventListener('drop', _onDrop);
      li.addEventListener('dragend', _onDragEnd);
    }

    // Click stop → pan map to it
    li.onclick = () => {
      if (stop.lat != null && stop.lng != null) {
        getMap()?.setView([stop.lat, stop.lng], 15);
      }
    };

    list.appendChild(li);
  });

  // ── Recommended POIs section ──
  if (recomms.length > 0) {
    const hdr = document.createElement('li');
    hdr.className = 'alt-header';
    hdr.textContent = `推荐备选 (${recomms.length}个) — 点击加入路线`;
    list.appendChild(hdr);

    recomms.forEach(rec => {
      const rli = document.createElement('li');
      rli.className = 'rec-item';
      const stars = rec.rating ? `⭐${rec.rating} ` : '';
      const distStr = rec._dist_km != null ? `${rec._dist_km.toFixed(1)}km` : (rec.match != null ? `${Math.round(rec.match * 100)}%` : '');
      rli.innerHTML = `
        <span class="rec-dot" style="background:${_catColor(rec.category)}"></span>
        <span class="rec-name">${stars}${rec.name}</span>
        <span class="rec-match">${distStr}</span>
        <button class="rec-add-btn">+ 加入</button>
      `;
      rli.querySelector('.rec-add-btn').onclick = (e) => {
        e.stopPropagation();
        _addRecToRoute(rec);
      };
      rli.onclick = () => _addRecToRoute(rec);
      rli.style.cursor = 'pointer';
      list.appendChild(rli);
    });
  }
}

function _transportStyle(mode) {
  if (!mode) return { icon: '🚶' };
  if (mode.includes('地铁')) return { icon: '🚇' };
  if (mode.includes('公交')) return { icon: '🚌' };
  if (mode.includes('骑行')) return { icon: '🚲' };
  if (mode.includes('驾车')) return { icon: '🚗' };
  return { icon: '🚶' };
}

function _catColor(cat) {
  if (!cat) return '#94a3b8';
  if (/美食|餐饮|火锅|小吃|咖啡/.test(cat)) return '#ef4444';
  if (/景点|公园|博物|古迹|风景/.test(cat)) return '#22c55e';
  if (/购物|商场/.test(cat)) return '#f59e0b';
  return '#6366f1';
}

function _zoomToSegment(seg, idx) {
  const from = _routeData.stops.find(s => s.id === seg.from);
  const to = _routeData.stops.find(s => s.id === seg.to);
  if (from && to) {
    const midLat = (from.lat + to.lat) / 2;
    const midLng = (from.lng + to.lng) / 2;
    getMap()?.setView([midLat, midLng], 15);
  }
}

// ═══════════════════════════════════════════════
// Actions
// ═══════════════════════════════════════════════

function _addRecToRoute(rec) {
  // Find best insertion point (closest segment midpoint)
  const stops = _routeData.stops;
  let bestIdx = stops.length - 1; // default: before dest
  let bestDist = Infinity;

  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1], b = stops[i];
    const mx = (a.lat + b.lat) / 2, my = (a.lng + b.lng) / 2;
    const d = Math.hypot(rec.lat - mx, rec.lng - my);
    if (d < bestDist) { bestDist = d; bestIdx = i; }
  }

  // Insert stop
  const newStop = {
    id: rec.id, name: rec.name, lat: rec.lat, lng: rec.lng,
    category: rec.category || '', rating: rec.rating,
    address: rec.address || '',
  };
  stops.splice(bestIdx, 0, newStop);

  // Remove from recommendations
  _routeData.recomms = _routeData.recomms.filter(r => r.id !== rec.id);

  _rebuildAndRender();
}

function _removeStop(idx) {
  const stops = _routeData.stops;
  if (idx <= 0 || idx >= stops.length - 1) return; // keep origin/dest
  const [removed] = stops.splice(idx, 1);

  // Add back to recommendations
  _routeData.recomms.push({
    id: removed.id, name: removed.name,
    lat: removed.lat, lng: removed.lng,
    category: removed.category || '', rating: removed.rating,
    match: 0.6, address: removed.address || '',
  });

  _rebuildAndRender();
}

function _rebuildAndRender() {
  _routeData.segments = _computeSegments();
  _routeData.summary = _computeSummary();
  renderAll();
}

// ═══════════════════════════════════════════════
// Drag-to-reorder
// ═══════════════════════════════════════════════

function _onDragStart(e) {
  _dragSrcIdx = parseInt(this.dataset.index);
  this.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', this.dataset.index);
}
function _onDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; this.classList.add('drag-over'); }
function _onDragEnter(e) { e.preventDefault(); this.classList.add('drag-enter'); }
function _onDragLeave(e) { this.classList.remove('drag-over', 'drag-enter'); }
function _onDragEnd() {
  this.classList.remove('dragging');
  document.querySelectorAll('.stop-item').forEach(el => el.classList.remove('drag-over', 'drag-enter'));
}
function _onDrop(e) {
  e.preventDefault();
  this.classList.remove('drag-over', 'drag-enter');
  const dstIdx = parseInt(this.dataset.index);
  if (_dragSrcIdx == null || _dragSrcIdx === dstIdx) return;
  _reorder(_dragSrcIdx, dstIdx);
}

function _reorder(fromIdx, toIdx) {
  const stops = _routeData.stops;
  if (fromIdx < 1 || toIdx < 1 || fromIdx >= stops.length - 1 || toIdx >= stops.length - 1) return;
  const [moved] = stops.splice(fromIdx, 1);
  stops.splice(toIdx, 0, moved);
  _rebuildAndRender();
}

// ═══════════════════════════════════════════════
// Confirm
// ═══════════════════════════════════════════════

async function _onConfirm() {
  if (!_sessionId) {
    _showEditorError('会话已过期，请重新规划路线');
    return;
  }
  try {
    const resp = await authFetch(`/api/route/${_sessionId}/confirm`, { method: 'POST' });
    const data = await resp.json();
    if (data.error) {
      _showEditorError('确认失败: ' + data.error);
      return;
    }
    _showConfirmed(data);
  } catch (e) {
    _showEditorError('确认失败: ' + e.message);
  }
}

function _showEditorError(msg) {
  $('editorError').textContent = msg;
  $('editorError').style.display = 'block';
  setTimeout(() => { $('editorError').style.display = 'none'; }, 5000);
}

function _showConfirmed(data) {
  $('editorArea').classList.remove('active');
  $('confirmedArea').classList.add('active');

  const { renderMarkdown } = window._utils || {};
  $('confirmedNarration').innerHTML = renderMarkdown(data.narration || '');

  // Render confirmed map
  if (data.stops && data.stops.length) {
    initMap('confirmedMap');
    clearLayers();
    const stops = data.stops.map((s, i) => ({
      ...s, isOrigin: i === 0, isDest: i === data.stops.length - 1 && i > 0,
    }));
    renderStops(stops);
    if (data.segments) renderSegments(data.segments, stops);
    invalidateSize();
  }
}

// ═══════════════════════════════════════════════
// Chat modify (placeholder)
// ═══════════════════════════════════════════════

async function _onChatModify() {
  const input = $('editorChatInput').value.trim();
  if (!input || !_sessionId) return;

  $('editorChatStatus').textContent = '修改中...';
  $('editorChatBtn').disabled = true;

  try {
    const resp = await authFetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ query: input, session_id: _sessionId }),
    });
    const data = await resp.json();
    if (data.error) {
      _showEditorError('修改失败: ' + data.error);
      return;
    }
    $('editorChatStatus').textContent = '完成';
    $('editorChatInput').value = '';
    // Reload route data from response
    _routeData = _realToModel(data);
    if (!_routeData.segments || _routeData.segments.length === 0) {
      _routeData.segments = _computeSegments();
      _routeData.summary = _computeSummary();
    }
    renderAll();
  } catch (err) {
    _showEditorError('修改失败: ' + err.message);
  } finally {
    $('editorChatBtn').disabled = false;
    setTimeout(() => { $('editorChatStatus').textContent = ''; }, 3000);
  }
}

// ═══════════════════════════════════════════════
// Backward-compat exports
// ═══════════════════════════════════════════════

export async function onSelectPoi(poiId) {
  // Not used in v3 — kept for compatibility
}
export async function onRemovePoi(poiId) {
  // Not used in v3 — kept for compatibility
}
export async function onConfirmRoute() {
  await _onConfirm();
}

