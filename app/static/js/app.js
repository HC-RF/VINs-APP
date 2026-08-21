/* Application controller: state, views, event wiring. */

import { ApiError, api } from './api.js';
import { summaryStrip, vehicleCard } from './components.js';
import { COLUMNS, applyFilters, renderFilterBar, renderTable, sortRecords } from './table.js';
import {
  $, $$, copyText, downloadBlob, esc, on, relativeTime, setHTML, setStatus,
  sourceLabel, toast, vehicleTitle,
} from './utils.js';

const SAMPLE_VINS = [
  'WA1ANAFY5J2213924',
  'WBXHT3C38J5K23394',
  '5UXKR0C56JL070851',
  'WBA2J3C53JVA52449',
  'WBA5R7C59KAE82587',
  'WBA4J1C58JBG77203',
];

const VIN_LENGTH = 17;
const VIN_CHARS = /^[A-HJ-NPR-Z0-9]+$/;

const state = {
  view: 'decode',
  records: new Map(),        // vin -> record, accumulated across decodes
  lastBatch: [],             // vins from the most recent decode
  lastSummary: null,
  expanded: new Set(),
  selected: new Set(),       // comparison selection
  sortKey: 'make',
  sortDir: 'asc',
  filters: {},
  search: '',
  busy: false,
  controller: null,
};

// ---------------------------------------------------------------- theming

function initTheme() {
  const stored = localStorage.getItem('vin-theme');
  const preferred = stored
    || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.documentElement.dataset.theme = preferred;
  $('#theme-btn').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('vin-theme', next);
  });
}

// ------------------------------------------------------------ input parsing

function parseInput(text) {
  const tokens = (text || '').split(/[\s,;]+/).map((t) => t.trim().toUpperCase()).filter(Boolean);
  const seen = new Set();
  const vins = [];
  const duplicates = [];
  const issues = [];

  for (const token of tokens) {
    const cleaned = token.replace(/[-_.]/g, '');
    if (seen.has(cleaned)) {
      if (!duplicates.includes(cleaned)) duplicates.push(cleaned);
      continue;
    }
    seen.add(cleaned);
    vins.push(cleaned);

    if (cleaned.length !== VIN_LENGTH) {
      issues.push({
        level: 'error', vin: cleaned,
        message: `must be ${VIN_LENGTH} characters (this one has ${cleaned.length})`,
      });
    } else if (/[IOQ]/.test(cleaned)) {
      issues.push({
        level: 'error', vin: cleaned,
        message: 'contains I, O or Q, which never appear in a VIN',
      });
    } else if (!VIN_CHARS.test(cleaned)) {
      issues.push({ level: 'error', vin: cleaned, message: 'contains unsupported characters' });
    }
  }
  return { vins, duplicates, issues };
}

function updateInputMeta() {
  const input = $('#vin-input');
  const { vins, duplicates, issues } = parseInput(input.value);

  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 260)}px`;
  $('.vin-field').classList.toggle('multiline', input.value.includes('\n'));

  const bad = issues.filter((i) => i.level === 'error').length;
  const ok = vins.length - bad;
  const meta = $('#vin-meta');
  if (!vins.length) {
    setHTML(meta, '');
  } else {
    const cls = bad ? (ok ? '' : 'bad') : 'ok';
    setHTML(meta, `<span class="count-pill ${cls}">${ok}/${vins.length} valid</span>`);
  }

  const box = $('#input-issues');
  const lines = [];
  for (const issue of issues.slice(0, 6)) {
    lines.push(`<div class="issue issue-error"><code>${esc(issue.vin)}</code> ${esc(issue.message)}</div>`);
  }
  if (issues.length > 6) {
    lines.push(`<div class="issue issue-error">&hellip; and ${issues.length - 6} more.</div>`);
  }
  if (duplicates.length) {
    lines.push(`<div class="issue issue-info">${duplicates.length}
      duplicate VIN${duplicates.length === 1 ? '' : 's'} will be decoded once:
      <code>${duplicates.slice(0, 3).map(esc).join(', ')}</code>${duplicates.length > 3 ? '&hellip;' : ''}</div>`);
  }
  setHTML(box, lines.join(''));
  box.hidden = !lines.length;

  $('#decode-btn').disabled = state.busy || !vins.length;
  const label = $('#decode-btn .btn-label');
  label.textContent = vins.length > 1 ? `Decode ${vins.length} VINs` : 'Decode VIN';
}

// ----------------------------------------------------------------- decoding

async function runDecode() {
  if (state.busy) return;
  const input = $('#vin-input');
  const { vins } = parseInput(input.value);
  if (!vins.length) return;

  state.busy = true;
  state.controller = new AbortController();
  const button = $('#decode-btn');
  button.classList.add('is-loading');
  button.disabled = true;
  $('#progress').hidden = false;
  $('#progress-text').textContent = vins.length > 1
    ? `Decoding ${vins.length} VINs — free sources first…`
    : 'Contacting providers…';
  setStatus('Decoding…');

  try {
    const payload = {
      vins,
      refresh: $('#opt-refresh').checked,
      verify: $('#opt-verify').checked,
    };
    const data = await api.decode(payload, state.controller.signal);

    state.lastBatch = data.results.map((r) => r.vin);
    state.lastSummary = data.summary;
    for (const record of data.results) state.records.set(record.vin, record);

    renderDecodeResults(data.results);
    renderSummary(data.summary);
    refreshDependentViews();
    loadRecent();

    const conflicts = data.summary.discrepancy_count;
    if (data.summary.invalid) {
      toast(`${data.summary.decoded} decoded, ${data.summary.invalid} rejected as invalid.`, 'warn');
    } else if (conflicts) {
      toast(`Decoded ${data.summary.decoded}. ${conflicts} field${conflicts === 1 ? '' : 's'} disagreed between sources.`, 'warn');
    } else {
      toast(`Decoded ${data.summary.decoded} vehicle${data.summary.decoded === 1 ? '' : 's'}.`, 'success');
    }
    setStatus(`Last decode: ${data.summary.decoded} of ${data.summary.requested} · $${(data.summary.total_cost || 0).toFixed(2)}`);
  } catch (error) {
    if (error?.name === 'AbortError') return;
    handleError(error);
  } finally {
    state.busy = false;
    state.controller = null;
    button.classList.remove('is-loading');
    $('#progress').hidden = true;
    updateInputMeta();
  }
}

function handleError(error) {
  if (error instanceof ApiError) {
    if (error.code === 'RATE_LIMITED') {
      toast(error.message, 'warn', 8000);
    } else if (error.code === 'TOO_MANY_ITEMS') {
      toast(error.message, 'error', 8000);
    } else if (error.code === 'VALIDATION_ERROR') {
      const first = error.details?.fields?.[0];
      toast(first ? `${first.field}: ${first.message}` : error.message, 'error');
    } else {
      toast(error.message, 'error', 7000);
    }
    setStatus(`Error: ${error.code}`);
  } else {
    toast(error?.message || 'Something went wrong.', 'error');
    setStatus('Error');
  }
}

// ------------------------------------------------------------------ render

function renderSummary(summary) {
  const strip = $('#decode-summary');
  setHTML(strip, summaryStrip(summary));
  strip.hidden = false;
}

function renderDecodeResults(records) {
  setHTML($('#decode-results'), records.map((r) => vehicleCard(r, {
    expanded: state.expanded.has(r.vin),
    selected: state.selected.has(r.vin),
  })).join(''));
}

function rerenderCard(vin) {
  const record = state.records.get(vin);
  if (!record) return;
  const node = $(`#decode-results .vehicle-card[data-vin="${CSS.escape(vin)}"]`);
  if (!node) return;
  node.outerHTML = vehicleCard(record, {
    expanded: state.expanded.has(vin),
    selected: state.selected.has(vin),
  });
}

function allRecords() {
  return Array.from(state.records.values());
}

function renderBulk() {
  const records = allRecords();
  setHTML($('#filter-bar'), renderFilterBar(records, state.filters, state.search));

  const filtered = applyFilters(records, state.filters, state.search);
  const sorted = sortRecords(filtered, state.sortKey, state.sortDir);
  setHTML($('#bulk-table'), renderTable(sorted, {
    sortKey: state.sortKey, direction: state.sortDir, selected: state.selected,
  }));

  const note = $('#filter-count');
  if (note) {
    note.textContent = filtered.length === records.length
      ? `${records.length} vehicle${records.length === 1 ? '' : 's'}`
      : `${filtered.length} of ${records.length} shown`;
  }
  $('#bulk-sub').textContent = records.length
    ? 'Decoded vehicles from this session. Sort, filter and export.'
    : 'No vehicles yet — decode some VINs to populate this table.';

  const disabled = !records.length;
  $('#export-csv').disabled = disabled;
  $('#export-xlsx').disabled = disabled;
}

function renderComparePicker() {
  const records = allRecords().filter((r) => r.valid);
  const picker = $('#compare-picker');
  if (!records.length) {
    setHTML(picker, '<p class="empty-note">Decode at least two vehicles to compare them.</p>');
    return;
  }
  setHTML(picker, records.map((r) => `
    <button class="pick-chip ${state.selected.has(r.vin) ? 'is-picked' : ''}"
            data-pick="${esc(r.vin)}" type="button"
            aria-pressed="${state.selected.has(r.vin)}">
      ${esc(vehicleTitle(r))} <small>${esc(r.vin.slice(-6))}</small>
    </button>`).join(''));
}

async function renderCompare() {
  renderComparePicker();
  const output = $('#compare-output');
  const vins = Array.from(state.selected);

  if (vins.length < 2) {
    setHTML(output, `
      <div class="empty-state">
        <svg viewBox="0 0 48 48" width="46" height="46" aria-hidden="true">
          <path d="M16 38V18M32 38V10" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <path d="M8 38h32" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <h3>Select vehicles to compare</h3>
        <p>Pick two to six decoded vehicles above, or tick <b>Compare</b> on any result card.</p>
      </div>`);
    return;
  }

  setHTML(output, '<p class="empty-note">Building comparison…</p>');
  try {
    const data = await api.compare(vins);
    setHTML(output, compareTable(data));
  } catch (error) {
    handleError(error);
    setHTML(output, '<p class="empty-note">Comparison failed.</p>');
  }
}

function compareTable(data) {
  const head = data.vehicles.map((v) => `
    <th>${esc(v.title)}<small>${esc(v.vin)}</small></th>`).join('');

  // Displacement reads better with a fixed decimal: "2.0" not "2".
  const DECIMAL_ROWS = new Set(['engine_displacement_l', 'zero_to_sixty_s']);

  const rows = data.rows.map((row) => {
    const cells = row.values.map((value, i) => {
      const missing = value === null || value === undefined || value === '';
      const best = row.best_index === i;
      const shown = !missing && DECIMAL_ROWS.has(row.field)
        ? Number(value).toFixed(1) : value;
      return `<td class="${missing ? 'na' : ''} ${best ? 'best' : ''}">${esc(missing ? 'Not available' : shown)}</td>`;
    }).join('');
    return `<tr class="${row.differs ? 'differs' : ''}">
      <td class="rowlabel">${esc(row.label)}</td>${cells}</tr>`;
  }).join('');

  return `
    <p class="panel-sub" style="padding:14px 20px 0">
      ${data.difference_count} of ${data.rows.length} attributes differ between these
      ${data.vehicles.length} vehicles. Differing rows are highlighted;
      ▲ marks the leading value where higher or lower is objectively better.
    </p>
    <table class="compare-table">
      <thead><tr><th class="rowlabel">Attribute</th>${head}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function refreshDependentViews() {
  updateCompareCount();
  if (state.view === 'bulk') renderBulk();
  if (state.view === 'compare') renderCompare();
}

function updateCompareCount() {
  const badge = $('[data-compare-count]');
  badge.textContent = String(state.selected.size);
  badge.hidden = state.selected.size === 0;
}

// -------------------------------------------------------------- recent list

async function loadRecent() {
  try {
    const data = await api.recent(12);
    const list = $('#recent-list');
    if (!data.results.length) {
      setHTML(list, '<p class="empty-note">No lookups yet.</p>');
      return;
    }
    setHTML(list, data.results.map((r) => {
      const title = [r.year, r.make, r.model].filter(Boolean).join(' ') || r.vin;
      return `
        <button class="recent-item" data-recent="${esc(r.vin)}" type="button">
          <div class="recent-body">
            <div class="recent-title">${esc(title)}${r.trim ? ` <span style="opacity:.6">${esc(r.trim)}</span>` : ''}</div>
            <div class="recent-vin">${esc(r.vin)} · ${esc(relativeTime(r.last_decoded_at))}</div>
          </div>
          ${r.discrepancies ? '<span class="chip chip-warn">!</span>' : ''}
        </button>`;
    }).join(''));
  } catch {
    // A failing sidebar must not disrupt the main workflow.
  }
}

// ------------------------------------------------------------------ export

async function runExport(format) {
  const records = applyFilters(allRecords(), state.filters, state.search);
  if (!records.length) {
    toast('Nothing to export.', 'warn');
    return;
  }
  const button = format === 'csv' ? $('#export-csv') : $('#export-xlsx');
  button.disabled = true;
  try {
    const { blob, filename } = await api.export(records.map((r) => r.vin), format);
    downloadBlob(blob, filename);
    toast(`Exported ${records.length} vehicle${records.length === 1 ? '' : 's'} to ${format.toUpperCase()}.`, 'success');
  } catch (error) {
    handleError(error);
  } finally {
    button.disabled = false;
  }
}

// --------------------------------------------------------------- providers

async function showProviders() {
  const modal = $('#providers-modal');
  modal.hidden = false;
  setHTML($('#providers-body'), '<p class="empty-note">Loading…</p>');
  try {
    const [{ providers }, usage] = await Promise.all([api.providers(), api.usage()]);
    const usageByName = Object.fromEntries((usage.providers || []).map((p) => [p.provider, p]));

    const rows = providers.map((p) => {
      const stats = usageByName[p.name];
      return `
        <div class="provider-row">
          <div class="provider-top">
            <span class="provider-name">${esc(p.label)}</span>
            <span class="chip ${p.kind === 'COMMERCIAL' ? 'chip-warn' : 'chip-high'}">${esc(p.kind)}</span>
            <span class="chip ${p.available ? 'chip-high' : 'chip-unknown'}">${p.available ? 'Available' : 'Unavailable'}</span>
            ${p.cost_per_call > 0
              ? `<span class="chip chip-warn">$${p.cost_per_call.toFixed(3)}/call</span>`
              : '<span class="chip chip-high">Free</span>'}
          </div>
          <p class="provider-desc">${esc(p.description)}</p>
          ${p.unavailable_reason ? `<p class="provider-desc" style="color:var(--medium)">${esc(p.unavailable_reason)}</p>` : ''}
          ${stats ? `<p class="provider-fields">${stats.calls} calls · ${stats.failures} failed ·
            avg ${stats.avg_latency_ms}ms · $${stats.total_cost.toFixed(2)} spent (30d)</p>` : ''}
          <p class="provider-fields">Supplies: ${esc(p.provides.slice(0, 12).join(', '))}${p.provides.length > 12 ? `, +${p.provides.length - 12} more` : ''}</p>
        </div>`;
    }).join('');

    setHTML($('#providers-body'), `
      <div class="summary-strip" style="margin:14px 0 4px;box-shadow:none">
        <div class="stat"><span class="stat-value">${usage.total_lookups}</span><span class="stat-label">lookups (30d)</span></div>
        <div class="stat good"><span class="stat-value">${Math.round((usage.cache_hit_rate || 0) * 100)}%</span><span class="stat-label">cache hit rate</span></div>
        <div class="stat"><span class="stat-value">${usage.vehicles_cached}</span><span class="stat-label">vehicles cached</span></div>
        <div class="stat ${usage.total_cost > 0 ? 'warn' : 'good'}"><span class="stat-value">$${(usage.total_cost || 0).toFixed(2)}</span><span class="stat-label">total API spend</span></div>
      </div>
      ${rows}`);
  } catch (error) {
    handleError(error);
    setHTML($('#providers-body'), '<p class="empty-note">Could not load provider status.</p>');
  }
}

// ------------------------------------------------------------------ routing

function switchView(view) {
  state.view = view;
  $$('.tab').forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  $$('[data-view-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
  if (view === 'bulk') renderBulk();
  if (view === 'compare') renderCompare();
  if (location.hash.slice(1) !== view) history.replaceState(null, '', `#${view}`);
}

// ------------------------------------------------------------------- events

function wireEvents() {
  const input = $('#vin-input');
  input.addEventListener('input', updateInputMeta);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      runDecode();
    } else if (event.key === 'Enter' && !event.shiftKey && !input.value.includes('\n')) {
      event.preventDefault();
      runDecode();
    }
  });

  $('#decode-btn').addEventListener('click', runDecode);
  $('#clear-btn').addEventListener('click', () => {
    input.value = '';
    updateInputMeta();
    setHTML($('#decode-results'), '');
    $('#decode-summary').hidden = true;
    input.focus();
  });
  $('#sample-btn').addEventListener('click', () => {
    input.value = SAMPLE_VINS.join('\n');
    updateInputMeta();
    input.focus();
  });

  $$('.tab').forEach((tab) => tab.addEventListener('click', () => switchView(tab.dataset.view)));
  window.addEventListener('hashchange', () => {
    const view = location.hash.slice(1);
    if (['decode', 'bulk', 'compare'].includes(view)) switchView(view);
  });

  // Expand / collapse a vehicle's full specification list.
  on(document.body, 'click', '[data-toggle-detail]', (_event, target) => {
    const vin = target.dataset.toggleDetail;
    if (state.expanded.has(vin)) state.expanded.delete(vin);
    else state.expanded.add(vin);
    rerenderCard(vin);
  });

  on(document.body, 'click', '[data-copy]', async (_event, target) => {
    const ok = await copyText(target.dataset.copy);
    toast(ok ? 'VIN copied.' : 'Could not copy to clipboard.', ok ? 'success' : 'error', 2400);
  });

  on(document.body, 'change', '[data-compare-toggle]', (_event, target) => {
    const vin = target.dataset.compareToggle;
    if (target.checked) {
      if (state.selected.size >= 6) {
        target.checked = false;
        toast('Compare at most six vehicles at a time.', 'warn');
        return;
      }
      state.selected.add(vin);
    } else {
      state.selected.delete(vin);
    }
    updateCompareCount();
    if (state.view === 'compare') renderCompare();
    if (state.view === 'decode') rerenderCard(vin);
  });

  on(document.body, 'click', '[data-pick]', (_event, target) => {
    const vin = target.dataset.pick;
    if (state.selected.has(vin)) state.selected.delete(vin);
    else if (state.selected.size >= 6) {
      toast('Compare at most six vehicles at a time.', 'warn');
      return;
    } else state.selected.add(vin);
    updateCompareCount();
    renderCompare();
  });

  $('#clear-compare').addEventListener('click', () => {
    state.selected.clear();
    updateCompareCount();
    renderCompare();
    if (state.view === 'decode') renderDecodeResults(state.lastBatch.map((v) => state.records.get(v)).filter(Boolean));
  });

  on(document.body, 'click', '[data-recent]', (_event, target) => {
    $('#vin-input').value = target.dataset.recent;
    updateInputMeta();
    switchView('decode');
    runDecode();
  });

  // Table sorting and filtering.
  on(document.body, 'click', '.data-table th[data-sort]', (_event, target) => {
    const key = target.dataset.sort;
    if (state.sortKey === key) state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    else { state.sortKey = key; state.sortDir = COLUMNS.find((c) => c.key === key)?.num ? 'desc' : 'asc'; }
    renderBulk();
  });
  on(document.body, 'change', '[data-filter]', (_event, target) => {
    state.filters[target.dataset.filter] = target.value;
    renderBulk();
  });
  on(document.body, 'input', '#table-search', (_event, target) => {
    state.search = target.value;
    renderBulk();
  });
  on(document.body, 'click', '#reset-filters', () => {
    state.filters = {};
    state.search = '';
    renderBulk();
  });

  $('#export-csv').addEventListener('click', () => runExport('csv'));
  $('#export-xlsx').addEventListener('click', () => runExport('xlsx'));
  $('#refresh-recent').addEventListener('click', loadRecent);

  $('#providers-btn').addEventListener('click', showProviders);
  on(document.body, 'click', '[data-close-modal]', () => { $('#providers-modal').hidden = true; });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') $('#providers-modal').hidden = true;
  });
}

// -------------------------------------------------------------------- boot

async function boot() {
  initTheme();
  wireEvents();
  updateInputMeta();
  loadRecent();

  const view = location.hash.slice(1);
  if (['decode', 'bulk', 'compare'].includes(view)) switchView(view);

  try {
    const health = await api.health();
    const commercial = health.providers.unavailable.filter((p) => p.name === 'autodev');
    setStatus(
      `${health.providers.available.length} provider${health.providers.available.length === 1 ? '' : 's'} online · `
      + `${health.database.engine} · `
      + (commercial.length ? 'free sources only ($0.00)' : 'commercial provider active'),
    );
  } catch {
    setStatus('API unreachable — is the server running?');
    toast('Cannot reach the backend. Start it with: python -m app.main', 'error', 9000);
  }
}

boot();
