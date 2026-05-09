/** Leaflet map — v3: fixed stops, segment hover tooltips, recommended POI click-to-add. */

let _map = null;
let _containerId = 'map';

// Layer state
let _stopMarkers = [];
let _segmentLines = [];
let _recMarkers = [];   // recommended POI markers

// Callbacks
let _onRecClick = null;
let _onSegmentHover = null;

export function setRecClickCallback(cb) { _onRecClick = cb; }
export function setSegmentHoverCallback(cb) { _onSegmentHover = cb; }

export function initMap(containerId = 'map') {
  _containerId = containerId;
  if (_map) {
    const el = document.getElementById(containerId);
    if (el && _map._container !== el) { _map.remove(); _map = null; }
  }
  return getMap();
}

export function getMap() {
  if (_map) return _map;
  const el = document.getElementById(_containerId);
  if (!el) return null;
  _map = L.map(_containerId, { attributionControl: false }).setView([34.26, 108.94], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap'
  }).addTo(_map);

  // Add color legend
  _addLegend();

  return _map;
}

function _addLegend() {
  if (!_map) return;
  // Remove existing legend if any
  const existing = document.querySelector('.map-legend');
  if (existing) existing.remove();

  const legend = L.control({ position: 'topleft' });
  legend.onAdd = function () {
    const div = L.DomUtil.create('div', 'map-legend');
    div.innerHTML = `
      <div class="legend-title">图例</div>
      <div class="legend-row"><span class="legend-dot" style="background:#ef4444"></span> 美食 / 餐饮</div>
      <div class="legend-row"><span class="legend-dot" style="background:#22c55e"></span> 景点 / 公园</div>
      <div class="legend-row"><span class="legend-dot" style="background:#f59e0b"></span> 购物 / 商场</div>
      <div class="legend-row"><span class="legend-dot" style="background:#8b5cf6"></span> 文化 / 艺术</div>
      <div class="legend-row"><span class="legend-dot" style="background:#3b82f6"></span> 地铁 / 交通</div>
      <div class="legend-row"><span class="legend-dot" style="background:#6366f1"></span> 其他</div>
      <div class="legend-divider"></div>
      <div class="legend-row"><span class="legend-dot" style="background:none;border:2px dashed #6366f1;width:10px;height:10px;"></span> 推荐备选POI</div>
      <div class="legend-row"><span class="legend-marker">起</span> 起点 / <span class="legend-marker" style="background:#f59e0b">终</span> 终点</div>
    `;
    return div;
  };
  legend.addTo(_map);
}

export function invalidateSize() { if (_map) _map.invalidateSize(); }

export function clearLayers() {
  if (!_map) return;
  _map.eachLayer(l => {
    if (l instanceof L.Marker || l instanceof L.Polyline || l instanceof L.Polygon || l instanceof L.CircleMarker) {
      _map.removeLayer(l);
    }
  });
  _stopMarkers = [];
  _segmentLines = [];
  _recMarkers = [];
}

// ── Transport styling ──────────────────────

const TRANSPORT = {
  '步行': { color: '#22c55e', icon: '🚶', dash: null },
  '骑行': { color: '#8b5cf6', icon: '🚲', dash: null },
  '驾车': { color: '#ef4444', icon: '🚗', dash: null },
  '地铁': { color: '#3b82f6', icon: '🚇', dash: '8,6' },
  '公交': { color: '#f59e0b', icon: '🚌', dash: '6,4' },
};
function _ts(mode) {
  if (!mode) return { color: '#94a3b8', icon: '📍', dash: '4,4' };
  for (const [k, v] of Object.entries(TRANSPORT)) {
    if (mode.includes(k)) return v;
  }
  return { color: '#94a3b8', icon: '📍', dash: '4,4' };
}

// ── Render route stops (NON-draggable) ─────

export function renderStops(stops) {
  const map = getMap();
  if (!map || !stops || !stops.length) return;
  _stopMarkers.forEach(m => map.removeLayer(m));
  _stopMarkers = [];

  const markers = [];
  stops.forEach((s, idx) => {
    if (s.lat == null || s.lng == null) return;
    const isFirst = s.isOrigin || (idx === 0);
    const isLast = s.isDest || (idx === stops.length - 1 && !isFirst);
    let cls = 'marker-mid', label = String(idx);
    if (isFirst) { cls = 'marker-start'; label = '起'; }
    else if (isLast) { cls = 'marker-end'; label = '终'; }

    const icon = L.divIcon({
      html: `<div class="custom-marker ${cls}">${label}</div>`,
      iconSize: [28, 28], iconAnchor: [14, 14], className: '',
    });
    const popup = _stopPopup(s, isFirst, isLast);
    const marker = L.marker([s.lat, s.lng], { icon, draggable: false }).addTo(map).bindPopup(popup);
    marker._stopData = s;
    marker._stopIndex = idx;
    markers.push(marker);
  });
  _stopMarkers = markers;

  if (markers.length > 1) {
    map.fitBounds(new L.featureGroup(markers).getBounds().pad(0.2));
  } else if (markers.length === 1) {
    map.setView([markers[0].getLatLng().lat, markers[0].getLatLng().lng], 14);
  }
}

function _stopPopup(s, isFirst, isLast) {
  let tag = '';
  if (isFirst) tag = '<span class="popup-tag tag-start">起点</span> ';
  else if (isLast) tag = '<span class="popup-tag tag-end">终点</span> ';
  const stars = s.rating ? ` ⭐${s.rating}` : '';
  return `<b>${tag}${s.name}</b>${stars}` +
    (s.address ? `<br><small>📍 ${s.address}</small>` : '') +
    (s.category ? `<br><small>🏷 ${s.category}</small>` : '');
}

// ── Render route segments (hover → tooltip) ──

export function renderSegments(segments, stops) {
  const map = getMap();
  if (!map) return;
  _segmentLines.forEach(l => map.removeLayer(l));
  _segmentLines = [];

  if (!segments || !segments.length) return;

  // Build stop lookup
  const stopById = {};
  (stops || []).forEach(s => { stopById[s.id] = s; });

  segments.forEach((seg, i) => {
    const from = stopById[seg.from];
    const to = stopById[seg.to];
    if (!from || !to || from.lat == null || to.lat == null) return;

    const st = _ts(seg.transport);
    const line = L.polyline([[from.lat, from.lng], [to.lat, to.lng]], {
      color: st.color, weight: 4, opacity: 0.75,
      dashArray: st.dash || null,
    }).addTo(map);

    // Hover → tooltip at cursor
    let _tooltip = null;
    line.on('mouseover', (e) => {
      const dur = seg.duration_min || seg.duration || 0;
      const dist = ((seg.distance_m || seg.distance || 0) / 1000).toFixed(1);
      const mode = seg.transport || '步行';
      const content = `<div class="seg-hover-tip">
        <span class="seg-hover-icon">${st.icon}</span>
        <span class="seg-hover-mode">${mode}</span>
        <span class="seg-hover-time">${dur}min</span>
        <span class="seg-hover-dist">${dist}km</span>
      </div>`;
      _tooltip = L.tooltip({ direction: 'top', offset: [0, -12], className: 'seg-tooltip', sticky: true })
        .setLatLng(e.latlng)
        .setContent(content)
        .openOn(map);
    });
    line.on('mouseout', () => {
      if (_tooltip) { map.closeTooltip(_tooltip); _tooltip = null; }
    });
    // Click to show details popup
    line.on('click', (e) => {
      L.DomEvent.stopPropagation(e);
      const dur = seg.duration_min || seg.duration || 0;
      const dist = ((seg.distance_m || seg.distance || 0) / 1000).toFixed(1);
      const mode = seg.transport || '步行';
      let html = `<div class="segment-popup">
        <div class="seg-popup-header">${st.icon} ${mode} — ${dur}分钟 / ${dist}km</div>`;
      if (seg.line_name) html += `<div class="seg-popup-body"><div>🚌 ${seg.line_name}</div></div>`;
      if (seg.start_stop) html += `<div class="seg-popup-body"><div>上车：${seg.start_stop}</div></div>`;
      if (seg.end_stop) html += `<div class="seg-popup-body"><div>下车：${seg.end_stop}</div></div>`;
      html += '</div>';
      L.popup().setLatLng(e.latlng).setContent(html).openOn(map);
    });

    line._segData = seg;
    _segmentLines.push(line);
  });
}

// ── Render recommended POI markers (clickable) ──

export function renderRecommended(pois) {
  const map = getMap();
  if (!map) return;
  _recMarkers.forEach(m => map.removeLayer(m));
  _recMarkers = [];

  if (!pois || !pois.length) return;

  pois.forEach(p => {
    if (p.lat == null || p.lng == null) return;
    const color = _catColor(p.category);

    const marker = L.circleMarker([p.lat, p.lng], {
      radius: 9,
      color: color, weight: 3, opacity: 0.85,
      fillColor: color, fillOpacity: 0.35,
      dashArray: '3,2',
    }).addTo(map);

    // Hover: show info tooltip with "click to add"
    marker.on('mouseover', () => {
      marker.setStyle({ radius: 14, weight: 4, opacity: 1, fillOpacity: 0.55 });
      const stars = p.rating ? `⭐${p.rating} ` : '';
      const match = p.match != null ? ` 匹配${Math.round(p.match * 100)}%` : '';
      marker.bindTooltip(
        `<div class="rec-tip"><b>${stars}${p.name}</b><br>
        <span class="rec-tip-meta">${p.category || ''}${match}</span><br>
        <span class="rec-tip-action">🖱 点击加入路线</span></div>`,
        { direction: 'top', offset: [0, -14], className: 'rec-tooltip' }
      ).openTooltip();
    });
    marker.on('mouseout', () => {
      marker.setStyle({ radius: 9, weight: 3, opacity: 0.85, fillOpacity: 0.35 });
      marker.unbindTooltip();
    });

    // Click → add to route
    marker.on('click', () => {
      if (_onRecClick) _onRecClick(p);
    });

    marker._recData = p;
    _recMarkers.push(marker);
  });
}

function _catColor(cat) {
  if (!cat) return '#94a3b8';
  if (/美食|餐饮|火锅|小吃|咖啡/.test(cat)) return '#ef4444';
  if (/景点|公园|博物|古迹|风景/.test(cat)) return '#22c55e';
  if (/购物|商场/.test(cat)) return '#f59e0b';
  if (/文化|书店|艺术/.test(cat)) return '#8b5cf6';
  return '#6366f1';
}

export function getStopMarkers() { return _stopMarkers; }
export function getSegmentLines() { return _segmentLines; }
