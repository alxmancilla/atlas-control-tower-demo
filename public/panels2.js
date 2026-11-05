import { $, esc, fmtTime, badge, json, STATUS_TONE } from './lib.js';

export function renderSearchStatus(s) {
  const ready = s.ready;
  $('searchStatus').innerHTML = ready
    ? badge('Search indexes queryable', 'green')
    : `${badge('Search unavailable', 'amber')} ${esc(s.message)}<div class="small">${s.indexes.map((i) => `${esc(i.name)}: ${esc(i.status)}${i.error ? ` (${esc(i.error)})` : ''}`).join(' · ')}</div>`;
  $('searchBtn').disabled = !ready;
  $('searchInput').disabled = !ready;
  $('searchCreate').classList.toggle('hidden', !s.indexes.some((i) => i.status === 'MISSING'));
}

const hl = (h) =>
  (h ?? [])
    .slice(0, 2)
    .map((x) => x.texts.map((t) => (t.type === 'hit' ? `<mark>${esc(t.value)}</mark>` : esc(t.value))).join(''))
    .join(' … ');

export function renderSearchResults(res) {
  if (!res.results.length) {
    $('searchResults').innerHTML = `<p class="muted">No matches for "${esc(res.q)}".</p>`;
    return;
  }
  $('searchResults').innerHTML = `<table><tr><th>Kind</th><th>Match</th><th>Score</th></tr>${res.results
    .map(
      (r) => `<tr><td>${badge(r.kind, r.kind === 'exception' ? 'red' : 'blue')}${r.status ? badge(r.status, STATUS_TONE[r.status]) : ''}</td>
      <td>${esc(r.title)}<div class="small">${hl(r.highlights) || esc(r.text ?? '')}</div>
        <div class="small muted">${esc(r.trackedEntityId)} ${r.source ? `· ${esc(r.source)}` : ''} ${r.exceptionId ? `· ${esc(r.exceptionId)}` : ''} ${r.eventTime ? `· ${fmtTime(r.eventTime)}` : ''}</div></td>
      <td>${Number(r.score).toFixed(2)}</td></tr>`
    )
    .join('')}</table>`;
}

function spark(points, range) {
  if (!points.length) return '';
  const vals = points.map((p) => p.value);
  const lo = Math.min(range.min, ...vals) - 1;
  const hi = Math.max(range.max, ...vals) + 1;
  const W = 600;
  const H = 90;
  const x = (i) => (i / Math.max(1, points.length - 1)) * W;
  const y = (v) => H - ((v - lo) / (hi - lo)) * H;
  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const dots = points
    .map((p, i) => (p.outOfRange ? `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="4" fill="#970606" />` : ''))
    .join('');
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <rect x="0" y="${y(range.max)}" width="${W}" height="${y(range.min) - y(range.max)}" fill="#e3fcf0" />
    <polyline points="${line}" fill="none" stroke="#016bf8" stroke-width="2" />${dots}</svg>`;
}

export function renderTelemetry(t) {
  if (!t.enabled) {
    $('telemetry').innerHTML = `<p class="muted">${esc(t.note)}</p>`;
    return;
  }
  const s = t.stats;
  const series = [...t.recent].reverse();
  $('telemetry').innerHTML = `<div class="small"><b>${esc(t.label)}</b> · sensor ${esc(t.sensorId)} · range ${t.range.min}–${t.range.max} ${esc(t.unit)}
      (configured ${t.configuredRange.min}–${t.configuredRange.max}) · last 8h · queried ${fmtTime(t.queriedAt)}</div>
    ${spark(series, t.range)}
    <div class="row small">${badge(`${s.count ?? 0} readings`)}${badge(`last ${s.last ?? '—'} ${t.unit}`, 'blue')}${badge(`avg ${s.avg ?? '—'}`)}${badge(`min ${s.min ?? '—'} / max ${s.max ?? '—'}`)}${badge(`${s.outOfRange ?? 0} out of range`, s.outOfRange ? 'red' : 'green')}</div>
    ${t.excursions.length ? `<table><tr><th>Excursion time</th><th>Value</th><th>Deviation</th></tr>${t.excursions.map((e) => `<tr><td>${fmtTime(e.ts)}</td><td>${e.value} ${esc(t.unit)}</td><td>${e.deviation > 0 ? '+' : ''}${e.deviation}</td></tr>`).join('')}</table>` : '<p class="small muted">No readings outside the selected range.</p>'}
    <p class="small muted">Range is a configurable demo threshold, not a regulatory or compliance limit.</p>`;
}

const STAGE_INFO = {
  flexible: ['1. Flexible', 'No validator. Any document shape is accepted.'],
  observe: ['2. Observe', 'validationAction "warn": invalid writes succeed; the server logs a warning.'],
  enforce: ['3. Enforce', 'validationAction "error": invalid inserts and updates are rejected. Existing invalid documents can remain.'],
  constraint: ['4. Constraint (9.0+)', 'validationLevel "constraint": MongoDB checks every existing document and guarantees all documents satisfy the rules.'],
};

export function renderValidation(v) {
  $('valStage').innerHTML = `current stage: ${badge(v.stage, 'blue')} · validationLevel ${esc(v.collectionOptions.validationLevel ?? 'default')} · validationAction ${esc(v.collectionOptions.validationAction ?? 'default')}`;
  $('stageButtons').innerHTML = Object.entries(STAGE_INFO)
    .map(([id, [label, help]]) => {
      const disabled = id === 'constraint' && !v.constraintGate.supported;
      return `<button data-stage="${id}" class="${id === v.stage ? 'current' : 'secondary'}" title="${esc(help)}" ${disabled ? 'disabled' : ''}>${esc(label)}</button>`;
    })
    .join('');
  $('gate').innerHTML = `${esc(STAGE_INFO[v.stage]?.[1] ?? '')} ${v.constraintGate.supported ? badge('constraint available', 'green') : badge('constraint unavailable', 'amber')} ${esc(v.constraintGate.reason)}`;
  const docs = v.invalid.docs;
  $('invalidDocs').innerHTML = docs.length
    ? `<p class="small">${v.invalid.count} document(s) fail the proposed schema.${v.stage === 'enforce' ? ' Enforce rejects new invalid writes but these existing records remain.' : ''}</p>
      <table><tr><th>Document</th><th>Issue</th><th>Action</th></tr>${docs
        .map(
          (d) => `<tr><td><code>${esc(d._id)}</code><div class="small">${esc(d.description)}</div></td>
          <td>${badge(d.demo?.issue ?? 'unknown', 'red')}<div class="small muted">trackedEntityId: ${esc(d.trackedEntityId ?? 'missing')} · eventTime: ${esc(typeof d.eventTime === 'string' && !/T.*Z$/.test(d.eventTime) ? `"${d.eventTime}"` : 'date')}</div></td>
          <td>${d.demo?.invalidSample ? `<button class="secondary" data-repair="${esc(d._id)}">Repair</button> <button class="secondary" data-remove="${esc(d._id)}">Remove</button>` : '<span class="muted small">not a demo sample</span>'}</td></tr>`
        )
        .join('')}</table>`
    : `<p class="small">${badge('0 invalid documents', 'green')} Every document satisfies the proposed schema.${v.constraintGate.supported && v.stage !== 'constraint' ? ' The collection is ready for the constraint stage.' : ''}</p>`;
}

export const renderResult = (id, obj) => {
  $(id).textContent = typeof obj === 'string' ? obj : json(obj);
};
