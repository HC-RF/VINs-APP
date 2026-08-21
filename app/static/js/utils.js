/* Shared helpers: escaping, formatting, DOM, toasts. */

export const NOT_AVAILABLE = 'Not available';

/** Escape text for safe insertion into HTML. Every dynamic string goes through this. */
export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/** Render a value, or an explicit "Not available" - never a blank or a guess. */
export function fmt(value, suffix = '') {
  if (value === null || value === undefined || value === '') return null;
  return `${value}${suffix}`;
}

export function vehicleTitle(record) {
  const parts = [record.year, record.make, record.model].filter(Boolean);
  if (!parts.length) return record.vin || 'Unknown vehicle';
  return parts.join(' ');
}

export function engineSummary(record) {
  const e = record.engine || {};
  const bits = [];
  if (e.displacement_l) bits.push(`${e.displacement_l}L`);
  if (e.configuration && e.cylinders) bits.push(`${e.configuration}-${e.cylinders}`);
  else if (e.cylinders) bits.push(`${e.cylinders}-cyl`);
  if (e.type) bits.push(e.type);
  return bits.length ? bits.join(' ') : null;
}

export function relativeTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function duration(ms) {
  if (ms === null || ms === undefined) return '';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** Provider name -> readable label. */
const SOURCE_LABELS = {
  vin_structure: 'VIN structure',
  nhtsa_vpic: 'NHTSA vPIC',
  spec_catalog: 'Spec catalog',
  autodev: 'Auto.dev',
  cost_policy: 'Cost policy',
  validation: 'Validation',
  cache: 'Cache',
};
export function sourceLabel(name) {
  return SOURCE_LABELS[name] || name || 'Unknown';
}

// --- DOM ------------------------------------------------------------------

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function on(root, eventName, selector, handler) {
  root.addEventListener(eventName, (event) => {
    const target = event.target.closest(selector);
    if (target && root.contains(target)) handler(event, target);
  });
}

export function setHTML(node, html) {
  if (node) node.innerHTML = html;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API needs a secure context; fall back to a hidden textarea.
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    area.remove();
    return ok;
  }
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// --- Toasts ---------------------------------------------------------------

export function toast(message, kind = 'info', ttl = 5200) {
  const stack = $('#toast-stack');
  if (!stack) return;
  const el = document.createElement('div');
  el.className = `toast toast-${kind}`;
  el.innerHTML = `<span class="toast-dot"></span><span>${esc(message)}</span>`;
  stack.appendChild(el);
  setTimeout(() => {
    el.classList.add('is-out');
    setTimeout(() => el.remove(), 220);
  }, ttl);
}

export function setStatus(text) {
  const el = $('#footer-status');
  if (el) el.textContent = text;
}

// --- Sorting --------------------------------------------------------------

/** Comparator that keeps missing values at the bottom regardless of direction. */
export function compareValues(a, b, direction = 'asc') {
  const aMissing = a === null || a === undefined || a === '';
  const bMissing = b === null || b === undefined || b === '';
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;

  let result;
  if (typeof a === 'number' && typeof b === 'number') result = a - b;
  else result = String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
  return direction === 'desc' ? -result : result;
}
