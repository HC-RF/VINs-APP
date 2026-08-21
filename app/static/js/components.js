/* Renderers for vehicle records: chips, spec grids, detail panels, alerts. */

import { NOT_AVAILABLE, duration, engineSummary, esc, sourceLabel, vehicleTitle } from './utils.js';

// --- Chips ----------------------------------------------------------------

export function confidenceChip(level, { large = false } = {}) {
  const key = (level || 'UNKNOWN').toLowerCase();
  return `<span class="chip chip-${esc(key)}${large ? ' lg' : ''}">${esc(level || 'UNKNOWN')}</span>`;
}

/** VIN-decoded vs database-enriched. This is the distinction the spec cares most about. */
export function originChip(origin) {
  if (origin === 'VIN_DECODED') {
    return `<span class="chip chip-vin" title="Read directly from the 17 VIN characters">VIN</span>`;
  }
  if (origin === 'ENRICHED') {
    return `<span class="chip chip-db" title="Looked up in an external specification source">DB</span>`;
  }
  return '';
}

export function sourceChip(source) {
  if (!source) return '';
  return `<span class="chip chip-source" title="Source: ${esc(sourceLabel(source))}">${esc(sourceLabel(source))}</span>`;
}

// --- Spec grid ------------------------------------------------------------

/** The headline table from the brief: value + confidence/source per field. */
const HEADLINE_FIELDS = [
  ['year', 'Year'],
  ['make', 'Make'],
  ['model', 'Model'],
  ['trim', 'Trim'],
  ['engine_displacement_l', 'Engine', (v) => `${Number(v).toFixed(1)} L`],
  ['engine_cylinders', 'Cylinders'],
  ['horsepower', 'Horsepower', (v) => `${v} hp`],
  ['fuel', 'Fuel'],
  ['drivetrain', 'Drivetrain'],
  ['transmission', 'Transmission'],
  ['body_type', 'Body Type'],
  ['plant_country', 'Country'],
];

function specCell(record, [key, label, formatter]) {
  const field = record.fields?.[key];
  const value = field?.value;
  const missing = value === null || value === undefined || value === '';
  const shown = missing ? NOT_AVAILABLE : (formatter ? formatter(value) : value);

  const meta = missing
    ? ''
    : `<div class="spec-meta">${confidenceChip(field.confidence)}${originChip(field.origin)}${
        field.disputed ? '<span class="chip chip-warn" title="Sources disagree on this field">Conflict</span>' : ''
      }</div>`;

  return `
    <div class="spec${field?.disputed ? ' is-disputed' : ''}">
      <span class="spec-label">${esc(label)}</span>
      <span class="spec-value${missing ? ' na' : ''}">${esc(shown)}</span>
      ${meta}
    </div>`;
}

export function specGrid(record) {
  return `<div class="spec-grid">${HEADLINE_FIELDS.map((f) => specCell(record, f)).join('')}</div>`;
}

// --- Alerts ---------------------------------------------------------------

const WARN_ICON = `<svg class="alert-icon" viewBox="0 0 18 18" width="15" height="15" aria-hidden="true"><path d="M9 2.5 16.5 15.5h-15L9 2.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 7v3.4M9 12.8v.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
const ERR_ICON = `<svg class="alert-icon" viewBox="0 0 18 18" width="15" height="15" aria-hidden="true"><circle cx="9" cy="9" r="6.9" stroke="currentColor" stroke-width="1.5"/><path d="M9 5.4v4.2M9 12v.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;

export function discrepancyAlert(record) {
  if (!record.discrepancies?.length) return '';
  const critical = record.discrepancies.some((d) => d.severity === 'critical');
  const items = record.discrepancies.map((d) => {
    const rivals = d.conflicting
      .map((c) => `<b>${esc(c.value)}</b> (${esc(sourceLabel(c.source))}, ${esc(c.confidence)})`)
      .join(' vs ');
    return `<li><b>${esc(d.label)}</b>: showing <b>${esc(d.selected_value)}</b> from
            ${esc(sourceLabel(d.selected_source))} &mdash; ${rivals}</li>`;
  }).join('');

  return `
    <div class="alert ${critical ? 'alert-danger' : 'alert-warn'}">
      ${critical ? ERR_ICON : WARN_ICON}
      <div>
        <strong>Data discrepancy detected</strong>
        ${esc(record.discrepancies.length)} field${record.discrepancies.length === 1 ? '' : 's'}
        ${critical ? 'including a critical identity field ' : ''}differ between sources.
        The higher-confidence value is shown; every value is retained.
        <ul class="alert-list">${items}</ul>
      </div>
    </div>`;
}

export function warningAlert(record) {
  // Discrepancies get their own detailed alert; do not repeat the summary line.
  const items = (record.warnings || []).filter((w) => !w.startsWith('Data discrepancy detected'));
  if (!items.length) return '';
  return `
    <div class="alert alert-warn">
      ${WARN_ICON}
      <div><strong>Check this vehicle</strong>
        <ul class="alert-list">${items.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>
      </div>
    </div>`;
}

export function errorAlert(record) {
  if (record.status !== 'INVALID_VIN' && !(record.errors || []).length) return '';
  const items = (record.errors || []).map(
    (e) => `<li>${esc(e.message)}${e.code ? ` <code>(${esc(e.code)})</code>` : ''}</li>`,
  ).join('');
  const heading = record.status === 'INVALID_VIN'
    ? 'Invalid VIN &mdash; no provider was queried'
    : 'A provider could not be reached';
  return `
    <div class="alert ${record.status === 'INVALID_VIN' ? 'alert-danger' : 'alert-warn'}">
      ${ERR_ICON}
      <div><strong>${heading}</strong>
        <ul class="alert-list">${items || '<li>No further detail available.</li>'}</ul>
      </div>
    </div>`;
}

// --- Detail ---------------------------------------------------------------

const DETAIL_GROUPS = [
  ['Identity', ['year', 'make', 'model', 'trim', 'series', 'body_type', 'vehicle_type', 'doors', 'steering_location']],
  ['Powertrain', ['engine_displacement_l', 'engine_configuration', 'engine_cylinders', 'engine_type',
                  'engine_model', 'engine_manufacturer', 'horsepower', 'torque_lb_ft', 'fuel',
                  'fuel_secondary', 'drivetrain', 'transmission', 'transmission_speeds']],
  ['Efficiency & performance', ['mpg_city', 'mpg_highway', 'mpg_combined', 'top_speed_mph',
                                'zero_to_sixty_s', 'curb_weight_lb', 'towing_capacity_lb', 'gvwr']],
  ['Manufacturing', ['manufacturer', 'plant_country', 'plant_state', 'plant_city', 'plant_company', 'wmi_country']],
  ['Safety & equipment', ['abs', 'esc', 'traction_control', 'tpms', 'airbag_front', 'airbag_side',
                          'airbag_curtain', 'backup_camera', 'forward_collision_warning',
                          'seats', 'seat_rows', 'wheels', 'axles', 'wheelbase_in', 'base_price_usd']],
];

function fieldRow(name, field) {
  const missing = field?.value === null || field?.value === undefined || field?.value === '';
  const alternatives = (field?.alternatives || []).filter((a) => a.value !== field.value);
  const altLine = alternatives.length
    ? `<div class="alt-line">Also reported: ${alternatives.map(
        (a) => `<b>${esc(a.value)}</b> &mdash; ${esc(sourceLabel(a.source))} (${esc(a.confidence)})`,
      ).join('; ')}</div>`
    : '';
  const note = field?.note ? `<div class="alt-line">${esc(field.note)}</div>` : '';

  return `
    <tr class="${field?.disputed ? 'is-disputed' : ''}">
      <td class="fname">${esc(field?.label || name)}</td>
      <td class="fvalue${missing ? ' na' : ''}">${esc(missing ? NOT_AVAILABLE : field.value)}${altLine}${note}</td>
      <td>${missing ? '' : sourceChip(field.source)}</td>
      <td>${missing ? '' : confidenceChip(field.confidence)}</td>
      <td>${missing ? '' : originChip(field.origin)}</td>
    </tr>`;
}

function detailGroup(record, [title, names]) {
  const rows = names
    .filter((n) => record.fields?.[n] && record.fields[n].value !== null && record.fields[n].value !== undefined)
    .map((n) => fieldRow(n, record.fields[n]))
    .join('');
  if (!rows) return '';
  return `
    <section class="detail-section">
      <h4 class="detail-title">${esc(title)}</h4>
      <table class="field-table">
        <thead><tr><th>Field</th><th>Value</th><th>Source</th><th>Confidence</th><th>Origin</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function callLog(record) {
  if (!record.provider_calls?.length) return '';
  const rows = record.provider_calls.map((c) => `
    <div class="call-row">
      <span class="call-dot ${c.success ? '' : 'fail'}"></span>
      <span class="call-name">${esc(sourceLabel(c.provider))}</span>
      <span class="call-meta">${c.success ? `${c.fields_returned} fields` : 'no data'}
        &middot; ${esc(duration(c.latency_ms))}
        &middot; ${c.cost > 0 ? `$${c.cost.toFixed(3)}` : 'free'}</span>
      ${c.error ? `<span class="call-err">${esc(c.error_code || 'ERROR')}: ${esc(c.error)}</span>` : ''}
    </div>`).join('');

  const uncovered = DETAIL_GROUPS.flatMap(([, names]) => names)
    .filter((n) => !record.fields?.[n] || record.fields[n].value === null);

  return `
    <section class="detail-section">
      <h4 class="detail-title">Provider calls</h4>
      <div class="call-log">${rows}</div>
      ${uncovered.length ? `<p class="alt-line" style="margin-top:10px">
        ${uncovered.length} field${uncovered.length === 1 ? '' : 's'} not supplied by any source;
        shown as &ldquo;${NOT_AVAILABLE}&rdquo; rather than estimated.</p>` : ''}
      ${record.cached ? `<p class="alt-line" style="margin-top:8px">
        Served from cache${record.cache_age_seconds != null
          ? ` (${Math.round(record.cache_age_seconds / 60)} min old)` : ''};
        no provider was contacted. Use <b>Force refresh</b> to re-query.</p>` : ''}
    </section>`;
}

export function detailPanel(record) {
  return `<div class="card-detail">
    ${DETAIL_GROUPS.map((g) => detailGroup(record, g)).join('')}
    ${callLog(record)}
  </div>`;
}

// --- Card -----------------------------------------------------------------

const COPY_ICON = `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.6" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 5.5v-1a1.5 1.5 0 0 0-1.5-1.5H4a1.5 1.5 0 0 0-1.5 1.5V9A1.5 1.5 0 0 0 4 10.5h1" stroke="currentColor" stroke-width="1.4"/></svg>`;

export function vehicleCard(record, { expanded = false, selected = false } = {}) {
  const invalid = !record.valid;
  const conflict = (record.discrepancies || []).length > 0;
  const overall = record.confidence?.overall || 'UNKNOWN';

  const subtitle = [record.trim, engineSummary(record), record.drivetrain]
    .filter(Boolean).join(' · ');

  return `
  <article class="vehicle-card ${invalid ? 'is-invalid' : ''} ${conflict ? 'has-conflict' : ''}"
           data-vin="${esc(record.vin)}">
    <header class="card-head">
      <div class="card-title-block">
        <h2 class="card-title">
          ${esc(vehicleTitle(record))}
          ${invalid ? '<span class="chip chip-danger lg">Invalid</span>'
                    : confidenceChip(overall, { large: true })}
          ${record.cached ? '<span class="chip chip-accent lg" title="Served from cache; no provider was contacted">Cached</span>' : ''}
          ${record.status === 'PARTIAL' ? '<span class="chip chip-warn lg">Partial</span>' : ''}
        </h2>
        <div class="card-vin">
          <span>${esc(record.vin || record.input || '')}</span>
          <button class="copy-btn" data-copy="${esc(record.vin)}" title="Copy VIN" aria-label="Copy VIN">${COPY_ICON}</button>
          ${record.check_digit_valid === true ? '<span class="chip chip-high">Check digit OK</span>' : ''}
          ${record.check_digit_valid === false ? '<span class="chip chip-warn">Check digit failed</span>' : ''}
        </div>
        ${subtitle ? `<p class="panel-sub">${esc(subtitle)}</p>` : ''}
      </div>
      <div class="card-actions">
        <label class="switch" title="Add to comparison">
          <input type="checkbox" data-compare-toggle="${esc(record.vin)}" ${selected ? 'checked' : ''}
                 ${invalid ? 'disabled' : ''}>
          <span class="switch-track"></span><span class="switch-label">Compare</span>
        </label>
        ${invalid ? '' : `<button class="ghost-btn" data-toggle-detail="${esc(record.vin)}">
          ${expanded ? 'Hide details' : 'All specifications'}</button>`}
      </div>
    </header>

    ${errorAlert(record)}
    ${discrepancyAlert(record)}
    ${warningAlert(record)}
    ${invalid ? '' : specGrid(record)}
    ${expanded && !invalid ? detailPanel(record) : ''}
  </article>`;
}

// --- Summary strip --------------------------------------------------------

export function summaryStrip(summary) {
  const stats = [
    ['', summary.requested, 'requested'],
    ['good', summary.decoded, 'decoded'],
    summary.invalid ? ['bad', summary.invalid, 'invalid'] : null,
    summary.failed ? ['bad', summary.failed, 'failed'] : null,
    summary.from_cache ? ['', summary.from_cache, 'from cache'] : null,
    summary.discrepancy_count ? ['warn', summary.discrepancy_count, 'discrepancies'] : null,
    ['', summary.provider_calls, 'provider calls'],
    ['', `$${(summary.total_cost || 0).toFixed(2)}`, 'API cost'],
    ['', duration(summary.elapsed_ms), 'elapsed'],
  ].filter(Boolean);

  const dupes = summary.duplicates_removed?.length
    ? `<div class="stat"><span class="stat-value warn">${summary.duplicates_removed.length}</span>
       <span class="stat-label">duplicate${summary.duplicates_removed.length === 1 ? '' : 's'} removed</span></div>`
    : '';

  return stats.map(([kind, value, label]) => `
    <div class="stat ${kind}">
      <span class="stat-value">${esc(value)}</span>
      <span class="stat-label">${esc(label)}</span>
    </div>`).join('') + dupes;
}
