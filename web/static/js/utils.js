/** Shared utility functions. */

export const $ = id => document.getElementById(id);

export function renderMarkdown(text) {
  if (!text) return '';
  let html = text;
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
  html = html.replace(/^---$/gm, '<hr>');
  html = html.replace(/^▶ (.+)$/gm, '<em>$1</em>');
  html = html.replace(/\n\n+/g, '</p><p>');
  html = '<p>' + html + '</p>';
  html = html.replace(/<p>\s*<\/p>/g, '');
  return html;
}

export function formatDuration(seconds) {
  if (!seconds) return '--';
  let s = parseInt(seconds);
  if (s >= 3600) return (s / 3600).toFixed(1) + 'h';
  if (s >= 60) return Math.round(s / 60) + 'min';
  return s + 's';
}

export function formatDistance(meters) {
  if (!meters) return '--';
  const m = parseInt(meters);
  if (m >= 1000) return (m / 1000).toFixed(1) + 'km';
  return m + 'm';
}

export function authFetch(url, options = {}) {
  const token = localStorage.getItem('jwt_token');
  const headers = { ...(options.headers || {}) };
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  }
  return fetch(url, { ...options, headers });
}

/** POI category → color mapping. */
export const CATEGORY_COLORS = {
  '餐饮': '#ef4444', '咖啡': '#a0522d', '茶馆': '#a0522d',
  '景点': '#22c55e', '文化': '#22c55e', '公园': '#22c55e',
  '购物': '#3b82f6', '商场': '#3b82f6',
};

export function categoryColor(cat) {
  if (!cat) return '#6b7280';
  for (const [k, v] of Object.entries(CATEGORY_COLORS)) {
    if (cat.includes(k)) return v;
  }
  return '#6b7280';
}
