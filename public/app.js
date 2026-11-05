import { $, api, esc, fmtTime, debounce } from './lib.js';
import { renderOverview, renderMilestones, renderTimeline, renderQueue, renderDetail } from './panels.js';
import { renderSearchStatus, renderSearchResults, renderTelemetry, renderValidation, renderResult } from './panels2.js';
import { bindActions } from './actions.js';

export const state = { meta: null, profileId: null, profile: null, queue: [], selectedId: null, flashId: null, ready: false };
const q = (path) => `${path}${path.includes('?') ? '&' : '?'}profile=${state.profileId}`;
export { q };

export function showBanner(html, bad = false) {
  const el = $('banner');
  el.innerHTML = html;
  el.className = `banner${bad ? ' bad' : ''}${html ? '' : ' hidden'}`;
}

// While maintenance runs, buttons are disabled and status is polled in case a change event is missed.
let maintenancePoll = null;
function setMaintenance(active) {
  document.body.classList.toggle('maintenance', active);
  if (active && !maintenancePoll) maintenancePoll = setInterval(() => refreshAll(), 3000);
  if (!active && maintenancePoll) {
    clearInterval(maintenancePoll);
    maintenancePoll = null;
  }
}

async function loadStatus() {
  const s = await api('/api/status');
  $('dbName').textContent = s.database;
  const pill = $('connection');
  if (!s.connected) {
    pill.className = 'pill bad';
    pill.textContent = 'Atlas: not connected';
    showBanner(`<b>Cannot reach Atlas target ${esc(s.target)}.</b> ${esc(s.error ?? '')} <button id="retryConnect">Retry</button>`, true);
    return false;
  }
  pill.className = 'pill ok';
  pill.textContent = `Atlas ${s.server.version} · ${s.target} · db ${s.database}`;
  setMaintenance(!!s.maintenance?.active);
  if (s.maintenance?.active) {
    showBanner(`<b>Maintenance in progress (${esc(s.maintenance.op)}).</b> Views and buttons are paused and resume automatically when it finishes.`);
    return false;
  }
  if (s.error) {
    showBanner(esc(s.error), true);
    return false;
  }
  if (!s.init.initialized) {
    const problems = s.init.problems?.length ? s.init.problems : ['seed data'];
    showBanner(`<b>The demo database is not ready.</b> ${problems.map(esc).join('; ')}. <button id="initBtn">Initialize demo data</button>`);
    return false;
  }
  showBanner(
    s.clockAgeMin > 30
      ? `Demo data was anchored ${Math.round(s.clockAgeMin / 60 * 10) / 10} h ago, so milestones and SLAs have drifted toward missed and telemetry may be outside the 8 h window. <button id="reanchorBtn">Re-anchor to now</button>`
      : ''
  );
  if (s.search) renderSearchStatus(s.search);
  return true;
}

function renderStatic() {
  const p = state.profile;
  $('profileTitle').textContent = `${p.label}: ${p.title}`;
  $('sampleButtons').innerHTML = p.samples
    .map((s) => `<button data-sample="${esc(s.id)}" class="${s.duplicateOf ? 'secondary' : ''}">${esc(s.label)}</button>`)
    .join('');
  $('invalidButtons').innerHTML = p.invalidSamples.map((s) => `<button class="secondary" data-invalid="${esc(s.id)}">${esc(s.label)}</button>`).join(' ');
  $('searchSource').innerHTML = `<option value="">any source</option>${Object.entries(p.sources).map(([id, s]) => `<option value="${esc(id)}">${esc(s.label)}</option>`).join('')}`;
  $('searchExamples').innerHTML = `Try: ${p.searchExamples.map((x) => `<a href="#" data-example="${esc(x)}">${esc(x)}</a>`).join(' · ')}`;
  $('telMin').value = p.telemetry?.min ?? '';
  $('telMax').value = p.telemetry?.max ?? '';
  $('indexList').innerHTML = `<table><tr><th>Collection</th><th>Key</th><th>Why</th></tr>${state.meta.indexes
    .map((i) => `<tr><td>${esc(i.coll)}</td><td><code>${esc(JSON.stringify(i.key))}</code>${i.unique ? ' unique' : ''}</td><td>${esc(i.reason)}</td></tr>`)
    .join('')}</table><p>MongoDB Search: ${state.meta.searchIndexes.map((s) => `<code>${esc(s.name)}</code> on ${esc(s.collection)}`).join(', ')}</p>`;
  $('capabilities').innerHTML = [
    ['Flexible document model', 'one validated event envelope; EDI segments, WMS scans, GPS pings, IoT readings, and API webhooks keep their own raw shape in <code>details.raw</code>'],
    ['Multi-document transactions', 'each event and its exception work item commit together (look for <b>txn</b> in the activity log)'],
    ['Aggregation pipeline', '<code>$lookup</code> + <code>$group</code> derive planned-versus-actual; <code>$facet</code> summarizes reliability'],
    ['Computed pattern', 'business impact (stock cover vs. projected arrival) is derived on write via <code>$lookup</code> into inventory_positions and stored on the entity and open exceptions'],
    ['Unique indexes', 'idempotencyKey and exceptionKey make duplicate processing safe'],
    ['Change streams', 'database-level stream with resume tokens drives this page (SSE)'],
    ['MongoDB Search', '<code>$search</code> with highlights over event descriptions and exception notes'],
    ['Time-series collection', '<code>$setWindowFields</code> moving average and range checks over telemetry'],
    ['Schema validation', 'flexible → observe (warn) → enforce (error) → constraint (MongoDB 9.0+)'],
  ].map(([t, d]) => `<li><b>${t}</b>: ${d}</li>`).join('');
}

export async function refreshOverview() {
  const [o, t] = await Promise.all([api(q('/api/overview')), api(q('/api/timeline'))]);
  renderOverview(o, state.profile);
  renderMilestones(o);
  renderTimeline(t.events, o.reliability, state.flashId);
}

export async function refreshExceptions() {
  const { queue } = await api(q('/api/exceptions'));
  state.queue = queue;
  const open = queue.filter((e) => e.status !== 'resolved').length;
  renderQueue(queue, state.selectedId, `${open} active · ${queue.length - open} resolved`);
  renderDetail(queue.find((e) => e.exceptionId === state.selectedId), state.profile);
}

export const refreshValidation = async () => renderValidation(await api(q('/api/validation')));

export async function refreshTelemetry() {
  const params = new URLSearchParams({ min: $('telMin').value, max: $('telMax').value });
  try {
    renderTelemetry(await api(q(`/api/telemetry?${params}`)));
  } catch (err) {
    $('telemetry').innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
}

export async function refreshAll() {
  state.ready = await loadStatus().catch((err) => (showBanner(esc(err.message), true), false));
  if (!state.ready) return;
  const tasks = [refreshOverview(), refreshExceptions(), refreshValidation(), refreshTelemetry()];
  const results = await Promise.allSettled(tasks);
  const failed = results.find((r) => r.status === 'rejected');
  if (failed) showBanner(esc(failed.reason.message), true);
}

const liveRefresh = debounce(async (colls) => {
  if (!state.ready) return refreshAll();
  const jobs = [];
  if (colls.has('operational_events') || colls.has('tracked_entities')) jobs.push(refreshOverview(), refreshValidation());
  if (colls.has('exceptions')) jobs.push(refreshExceptions(), refreshOverview());
  if (colls.has('demo_state')) jobs.push(refreshValidation());
  await Promise.allSettled(jobs);
  colls.clear();
}, 250);
const pending = new Set();

function startStream() {
  const es = new EventSource('/api/stream');
  const pill = $('streamStatus');
  es.addEventListener('stream-status', (e) => {
    const s = JSON.parse(e.data);
    pill.className = `pill ${s.state === 'live' ? 'ok' : s.state === 'reconnecting' || s.state === 'resuming' ? 'warn' : 'bad'}`;
    pill.textContent = `change stream: ${s.state}`;
    pill.title = s.error ?? '';
  });
  es.addEventListener('change', (e) => {
    const c = JSON.parse(e.data);
    const li = document.createElement('li');
    li.innerHTML = `${fmtTime(c.clusterTime ?? Date.now())} ${c.txn ? `<span class="badge b-green" title="committed in one multi-document transaction">txn ${esc(c.txn)}</span>` : ''}<b>${esc(c.op)}</b> ${esc(c.coll)} ${esc(c.profile ?? '')} ${esc(c.exceptionId ?? c.eventType ?? '')} ${c.updatedFields.length ? `<span class="muted">[${esc(c.updatedFields.slice(0, 4).join(', '))}]</span>` : ''}`;
    $('activity').prepend(li);
    while ($('activity').children.length > 60) $('activity').lastChild.remove();
    if (c.coll === 'operational_events' && c.op === 'insert') state.flashId = c.id;
    if (c.coll === 'demo_state' && c.id === 'maintenance') return refreshAll();
    if (document.body.classList.contains('maintenance')) return; // refresh once when maintenance ends
    if (!c.profile || c.profile === state.profileId) {
      pending.add(c.coll);
      liveRefresh(pending);
    }
  });
  es.addEventListener('resync', () => refreshAll());
  es.onerror = () => {
    pill.className = 'pill warn';
    pill.textContent = 'change stream: browser reconnecting';
  };
}

export function selectProfile(id) {
  state.profileId = id;
  state.profile = state.meta.profiles.find((p) => p.id === id);
  state.selectedId = null;
  $('searchResults').innerHTML = '';
  renderResult('valResult', '');
  renderStatic();
  return refreshAll();
}

async function main() {
  state.meta = await api('/api/profiles');
  $('profileSelect').innerHTML = state.meta.profiles.map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join('');
  $('profileSelect').value = state.meta.defaultProfile;
  bindActions();
  startStream();
  await selectProfile(state.meta.defaultProfile);
}

main().catch((err) => showBanner(`Failed to load: ${esc(err.message)}`, true));
