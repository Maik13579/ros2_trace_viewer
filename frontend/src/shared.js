/** Browser-facing state uses seconds; exact nanoseconds arrive as decimal strings. */
export const $ = id => document.getElementById(id);
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else if (key === 'checked' || key === 'disabled' || key === 'hidden') node[key] = value;
    else node.setAttribute(key, value);
  }
  children.flat().forEach(child => { if (child != null) node.append(child instanceof Node ? child : String(child)); });
  return node;
}
export const seconds = ns => Number(ns) / 1e9;
export function duration(ns) {
  const n = Number(ns);
  if (n < 1000) return `${n.toFixed(0)} ns`;
  if (n < 1e6) return `${(n / 1e3).toFixed(2)} µs`;
  if (n < 1e9) return `${(n / 1e6).toFixed(3)} ms`;
  return `${(n / 1e9).toFixed(4)} s`;
}
export function exactSeconds(ns) {
  const value = BigInt(ns), billion = 1000000000n;
  return `${value / billion}.${String(value % billion).padStart(9, '0')} s`;
}
export function color(value) {
  let hash = 0;
  for (const char of String(value)) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return `hsl(${Math.abs(hash) % 360} 58% 64%)`;
}
export function debounce(fn, delay = 150) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
}
let pending = 0;
export function showError(error) { $('error').textContent = String(error.message || error); $('error').hidden = false; }
export async function api(path, parameters = {}, signal) {
  const query = new URLSearchParams();
  Object.entries(parameters).forEach(([key, value]) => { if (value !== '' && value != null) query.set(key, value); });
  pending++; $('activity').textContent = 'Loading…';
  try {
    const response = await fetch(`/api/${path}?${query}`, {signal});
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
    return data;
  } finally { pending--; $('activity').textContent = pending ? 'Loading…' : 'Ready'; }
}
export const state = {
  info: null, view: 'timeline', grouping: 'node', selected: null,
  filters: {exclude_processes: [], exclude_nodes: [], exclude_callbacks: []},
  draft: {exclude_processes: [], exclude_nodes: [], exclude_callbacks: []},
  events: new EventTarget(),
  parameters(extra = {}) {
    const result = {};
    for (const [key, value] of Object.entries(this.filters)) result[key] = Array.isArray(value) ? [...value].sort().join(',') : value;
    return {...result, ...extra};
  },
  change() {
    $('error').hidden = true;
    const count = Object.values(this.filters).filter(v => Array.isArray(v) ? v.length : !!v).length;
    $('filter-count').textContent = count ? String(count) : '';
    this.events.dispatchEvent(new Event('filters'));
  },
  reset() { this.filters = {exclude_processes: [], exclude_nodes: [], exclude_callbacks: []}; this.change(); },
};
export function canvasSize(canvas, width, height) {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(width * ratio));
  canvas.height = Math.max(1, Math.round(height * ratio));
  canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return ctx;
}

export function callbackLabel(record) {
  const symbol = record.callback_symbol || record.symbol || record.name || '';
  const bound = symbol.match(/([A-Za-z_]\w*(?:::[A-Za-z_]\w*)+)::\*/);
  const method = symbol.match(/([A-Za-z_]\w*(?:::[A-Za-z_]\w*)+)\(/);
  const type = record.callback_type || record.type || 'callback';
  if(type==='subscription')return `${record.node_name||'Unknown node'} · ${record.callback_topic||'Unknown topic'}`;
  if(type==='service')return `${record.node_name||'Unknown node'} · ${record.callback_service||'Unknown service'}`;
  const owner = bound ? bound[1].split('::').slice(-1)[0] : method ? method[1].split('::').slice(-2).join('::') : '';
  return owner ? `${owner} · ${type}` : `${type[0].toUpperCase() + type.slice(1)} callback`;
}

export const publicationLabel = record => `${record.node_name || 'Unknown node'} · ${record.topic || 'Unknown topic'}`;
