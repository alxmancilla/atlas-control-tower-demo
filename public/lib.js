export const $ = (id) => document.getElementById(id);

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: res.statusText }));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

export const fmtTime = (d) =>
  d ? new Date(d).toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
export const fmtMin = (m) => (m == null ? '—' : `${m > 0 ? '+' : ''}${m} min`);
export const badge = (text, tone = '', title = '') =>
  `<span class="badge ${tone ? `b-${tone}` : ''}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
export const json = (obj) => JSON.stringify(obj, null, 2);

export const STATUS_TONE = {
  completed: 'green',
  completed_late: 'amber',
  at_risk: 'amber',
  missed: 'red',
  pending: '',
  open: 'red',
  acknowledged: 'amber',
  resolved: 'green',
  critical: 'red',
  high: 'red',
  medium: 'amber',
  low: '',
  accepted: 'green',
  late: 'amber',
  out_of_order: 'amber',
};

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
