/* Sortable, filterable results table for bulk lookups. */

import { NOT_AVAILABLE, compareValues, esc, vehicleTitle } from './utils.js';
import { confidenceChip } from './components.js';

export const COLUMNS = [
  { key: 'vin', label: 'VIN', get: (r) => r.vin, cls: 'vin-cell' },
  { key: 'year', label: 'Year', get: (r) => r.year, num: true, filter: 'select' },
  { key: 'make', label: 'Make', get: (r) => r.make, filter: 'select' },
  { key: 'model', label: 'Model', get: (r) => r.model, filter: 'select' },
  { key: 'trim', label: 'Trim', get: (r) => r.trim },
  { key: 'engine', label: 'Engine', get: (r) => r.engine?.displacement_l, num: true,
    render: (v) => (v == null ? null : `${Number(v).toFixed(1)} L`), filter: 'select' },
  { key: 'cylinders', label: 'Cyl', get: (r) => r.engine?.cylinders, num: true, filter: 'select' },
  { key: 'horsepower', label: 'HP', get: (r) => r.horsepower, num: true },
  { key: 'fuel', label: 'Fuel', get: (r) => r.fuel, filter: 'select' },
  { key: 'drivetrain', label: 'Drive', get: (r) => r.drivetrain, filter: 'select' },
  { key: 'transmission', label: 'Transmission', get: (r) => r.transmission, filter: 'select' },
  { key: 'mpg_combined', label: 'MPG', get: (r) => r.mpg_combined, num: true },
  { key: 'body_type', label: 'Body', get: (r) => r.body_type, filter: 'select' },
  { key: 'confidence', label: 'Confidence', get: (r) => r.confidence?.overall,
    render: (v) => confidenceChip(v), raw: true },
];

const CONFIDENCE_ORDER = { HIGH: 3, MEDIUM: 2, LOW: 1, UNKNOWN: 0 };

function sortValue(column, record) {
  const value = column.get(record);
  if (column.key === 'confidence') return CONFIDENCE_ORDER[value] ?? -1;
  return value;
}

export function distinctValues(records, column) {
  const values = new Set();
  for (const record of records) {
    const value = column.get(record);
    if (value !== null && value !== undefined && value !== '') values.add(value);
  }
  return Array.from(values).sort((a, b) => compareValues(a, b, 'asc'));
}

export function applyFilters(records, filters, search) {
  let out = records;

  for (const [key, wanted] of Object.entries(filters)) {
    if (!wanted) continue;
    const column = COLUMNS.find((c) => c.key === key);
    if (!column) continue;
    out = out.filter((r) => String(column.get(r) ?? '') === String(wanted));
  }

  const term = (search || '').trim().toLowerCase();
  if (term) {
    out = out.filter((r) => {
      const haystack = [r.vin, r.year, r.make, r.model, r.trim, r.fuel,
                        r.drivetrain, r.transmission, r.body_type]
        .filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(term);
    });
  }
  return out;
}

export function sortRecords(records, sortKey, direction) {
  if (!sortKey) return records;
  const column = COLUMNS.find((c) => c.key === sortKey);
  if (!column) return records;
  return [...records].sort((a, b) =>
    compareValues(sortValue(column, a), sortValue(column, b), direction));
}

export function renderFilterBar(records, filters, search) {
  const selects = COLUMNS.filter((c) => c.filter === 'select').map((column) => {
    const options = distinctValues(records, column);
    if (options.length < 2) return '';
    const current = filters[column.key] || '';
    return `
      <select data-filter="${esc(column.key)}" aria-label="Filter by ${esc(column.label)}">
        <option value="">${esc(column.label)}: all</option>
        ${options.map((o) => `<option value="${esc(o)}" ${String(o) === String(current) ? 'selected' : ''}>${esc(o)}</option>`).join('')}
      </select>`;
  }).join('');

  return `
    <input type="search" id="table-search" placeholder="Search VIN, make, model&hellip;"
           value="${esc(search || '')}" aria-label="Search results">
    ${selects}
    <button class="ghost-btn subtle xs" id="reset-filters" type="button">Reset</button>
    <span class="filter-note" id="filter-count"></span>`;
}

export function renderTable(records, { sortKey, direction, selected }) {
  if (!records.length) {
    return `
      <div class="empty-state">
        <svg viewBox="0 0 48 48" width="46" height="46" aria-hidden="true">
          <rect x="6" y="10" width="36" height="28" rx="4" stroke="currentColor" stroke-width="2"/>
          <path d="M6 19h36M17 19v19" stroke="currentColor" stroke-width="2"/>
        </svg>
        <h3>Nothing to show</h3>
        <p>Decode some VINs first, or relax the filters above.</p>
      </div>`;
  }

  const head = COLUMNS.map((c) => {
    const isSorted = sortKey === c.key;
    const arrow = isSorted ? (direction === 'asc' ? '▲' : '▼') : '▴▾';
    return `<th data-sort="${esc(c.key)}" class="${isSorted ? 'sorted' : ''} ${c.num ? 'num' : ''}"
             aria-sort="${isSorted ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}">
             ${esc(c.label)}<span class="sort-arrow">${arrow}</span></th>`;
  }).join('');

  const body = records.map((record) => {
    const cells = COLUMNS.map((c) => {
      const value = c.get(record);
      const missing = value === null || value === undefined || value === '';
      if (c.raw) return `<td>${missing ? `<span class="na">${NOT_AVAILABLE}</span>` : c.render(value)}</td>`;
      const shown = missing ? NOT_AVAILABLE : (c.render ? c.render(value) : value);
      return `<td class="${c.cls || ''} ${c.num ? 'num' : ''} ${missing ? 'na' : ''}">${esc(shown)}</td>`;
    }).join('');

    const flags = [
      record.discrepancies?.length ? 'has-conflict' : '',
      !record.valid ? 'is-invalid' : '',
    ].filter(Boolean).join(' ');

    return `
      <tr class="${flags}" data-row-vin="${esc(record.vin)}" title="${esc(vehicleTitle(record))}">
        <td class="row-check">
          <input type="checkbox" data-compare-toggle="${esc(record.vin)}"
                 ${selected.has(record.vin) ? 'checked' : ''} ${record.valid ? '' : 'disabled'}
                 aria-label="Select ${esc(record.vin)} for comparison">
        </td>
        ${cells}
      </tr>`;
  }).join('');

  return `
    <table class="data-table">
      <thead><tr><th class="row-check no-sort"></th>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}
