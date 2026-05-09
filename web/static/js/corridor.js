/** Corridor layer — renders shape, clusters, and individual POI markers on the map. */

import { getMap, clearLayers } from './map.js';
import { categoryColor, formatDuration, formatDistance } from './utils.js';

let _poiMarkers = [];       // { poiId, marker }
let _clusterMarkers = [];
let _shapePoly = null;
let _onPoiClick = null;
let _onPoiHover = null;

export function setCorridorCallbacks(onClick, onHover) {
  _onPoiClick = onClick;
  _onPoiHover = onHover;
}

export function renderCorridor(corridorPois, clusterMarkers, corridorShape) {
  const map = getMap();
  if (!map) return;

  clearCorridor();

  // 1. Corridor shape polygon
  if (corridorShape && corridorShape.length >= 3) {
    _shapePoly = L.polygon(corridorShape, {
      color: '#2563eb', weight: 1, opacity: 0.15,
      fillColor: '#2563eb', fillOpacity: 0.06,
    }).addTo(map);
  }

  // 2. Cluster markers (large circles)
  if (clusterMarkers && clusterMarkers.length) {
    clusterMarkers.forEach(cm => {
      if (!cm.lat || !cm.lng) return;
      const circle = L.circleMarker([cm.lat, cm.lng], {
        radius: 18,
        color: '#6366f1', weight: 2, opacity: 0.5,
        fillColor: '#6366f1', fillOpacity: 0.1,
      }).addTo(map);
      circle.bindTooltip(cm.name || `区域${cm.cluster_id}`, {
        direction: 'top', offset: [0, -20],
      });
      _clusterMarkers.push(circle);
    });
  }

  // 3. Individual POI markers
  if (corridorPois && corridorPois.length) {
    corridorPois.forEach(poi => {
      if (poi.lat == null || poi.lng == null) return;
      addPoiMarker(poi);
    });
  }
}

function addPoiMarker(poi) {
  const map = getMap();
  if (!map) return;

  const color = categoryColor(poi.category);
  const isSelected = poi.selected;
  const size = isSelected ? 11 : 7;

  const marker = L.circleMarker([poi.lat, poi.lng], {
    radius: size,
    color: isSelected ? '#000' : color,
    weight: isSelected ? 2 : 1,
    opacity: 0.9,
    fillColor: color,
    fillOpacity: isSelected ? 0.9 : 0.6,
  }).addTo(map);

  // Tooltip on hover
  marker.on('mouseover', () => {
    marker.setStyle({ radius: 11, weight: 2 });
    marker.bindTooltip(_buildTooltip(poi), {
      direction: 'top', offset: [0, -10],
      className: 'corridor-poi-tooltip',
    }).openTooltip();
    if (_onPoiHover) _onPoiHover(poi.id);
  });
  marker.on('mouseout', () => {
    const sel = poi.selected;
    marker.setStyle({ radius: sel ? 11 : 7, weight: sel ? 2 : 1 });
    marker.unbindTooltip();
    if (_onPoiHover) _onPoiHover(null);
  });

  marker.on('click', () => {
    if (_onPoiClick) _onPoiClick(poi.id, poi);
  });

  _poiMarkers.push({ poiId: poi.id, marker, poi });
}

export function updatePoiMarker(poiId, selected) {
  const entry = _poiMarkers.find(m => m.poiId === poiId);
  if (!entry) return;
  const { marker, poi } = entry;
  poi.selected = selected;
  const color = categoryColor(poi.category);
  marker.setStyle({
    radius: selected ? 11 : 7,
    color: selected ? '#000' : color,
    weight: selected ? 2 : 1,
    fillOpacity: selected ? 0.9 : 0.6,
  });
}

export function clearCorridor() {
  const map = getMap();
  if (!map) return;
  _poiMarkers.forEach(({ marker }) => map.removeLayer(marker));
  _clusterMarkers.forEach(m => map.removeLayer(m));
  if (_shapePoly) map.removeLayer(_shapePoly);
  _poiMarkers = [];
  _clusterMarkers = [];
  _shapePoly = null;
}

function _buildTooltip(poi) {
  const stars = poi.rating ? '⭐' + poi.rating + ' ' : '';
  const price = poi.price_per_person ? '💰¥' + poi.price_per_person : '';
  const reasons = poi.recommendation_reasons || {};
  let html = `<div class="poi-tip-name">${stars}${poi.name}</div>`;
  if (poi.category || price) {
    html += `<div class="poi-tip-meta">${poi.category || ''} ${price}</div>`;
  }
  if (reasons.structured) {
    html += `<div class="poi-tip-reason">${reasons.structured}</div>`;
  }
  if (reasons.user_need) {
    html += `<div class="poi-tip-match">${reasons.user_need}</div>`;
  }
  html += `<div class="poi-tip-action">点击切换选中</div>`;
  return html;
}
