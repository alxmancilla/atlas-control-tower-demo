import { $, api, esc, json } from './lib.js';
import { renderSearchResults, renderSearchStatus, renderResult } from './panels2.js';
import { renderQueue } from './panels.js';
import { state, q, refreshAll, refreshValidation, refreshTelemetry, refreshExceptions, refreshKpis, selectProfile, showBanner, showDetail } from './app.js';

const errText = (err) => json({ error: err.message, ...(err.data?.codeName ? { codeName: err.data.codeName } : {}) });

async function guard(btn, fn) {
  if (btn) btn.disabled = true;
  try {
    return await fn();
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runSearch() {
  const params = new URLSearchParams({
    q: $('searchInput').value,
    kind: $('searchKind').value,
    source: $('searchSource').value,
    status: $('searchExcStatus').value,
  });
  try {
    renderSearchResults(await api(q(`/api/search?${params}`)));
  } catch (err) {
    $('searchResults').innerHTML = `<p class="muted">${esc(err.message)}</p>`;
    if (err.status === 503) renderSearchStatus(await api('/api/search/status'));
  }
}

async function exceptionAction(act) {
  const operator = $('excOperator').value.trim();
  if (operator) localStorage.operator = operator;
  const body = { action: act, operator, owner: $('excOwner').value, note: $('excNote').value };
  try {
    await api(`/api/exceptions/${encodeURIComponent(state.selectedId)}/action`, { method: 'POST', body });
    await refreshExceptions();
  } catch (err) {
    $('excActionResult').textContent = err.message;
  }
}

async function recommendationDecision(actionId, decision) {
  const operator = $('excOperator').value.trim();
  if (operator) localStorage.operator = operator;
  try {
    await api(`/api/exceptions/${encodeURIComponent(state.selectedId)}/recommendation`, {
      method: 'POST',
      body: { actionId, decision, operator, note: $('excNote').value },
    });
    await refreshExceptions();
  } catch (err) {
    $('excActionResult').textContent = err.message;
  }
}

export function bindActions() {
  $('profileSelect').addEventListener('change', (e) => selectProfile(e.target.value));

  $('banner').addEventListener('click', async (e) => {
    if (e.target.id === 'retryConnect') await guard(e.target, () => api('/api/connect', { method: 'POST' }).catch(() => {})).then(refreshAll);
    if (e.target.id === 'reanchorBtn') {
      await guard(e.target, () => api('/api/reanchor', { method: 'POST' }).catch((err) => showBanner(esc(err.message), true)));
      await refreshAll();
    }
    if (e.target.id === 'initBtn') {
      showBanner('Initializing demo data in Atlas…');
      await api('/api/setup', { method: 'POST' }).catch((err) => showBanner(esc(err.message), true));
      await refreshAll();
    }
  });

  $('sampleButtons').addEventListener('click', (e) => {
    const id = e.target.dataset.sample;
    if (!id) return;
    guard(e.target, async () => {
      try {
        const r = await api('/api/events/sample', { method: 'POST', body: { profile: state.profileId, sampleId: id } });
        const exc = r.exception ? `; exception ${r.exception.outcome}` : '';
        $('sampleResult').textContent = `${e.target.textContent}: event ${r.outcome}${exc}. Waiting for the change stream to refresh views…`;
      } catch (err) {
        $('sampleResult').textContent = err.message;
      }
    });
  });

  $('exceptionQueue').addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-id]');
    if (!row) return;
    state.selectedId = row.dataset.id;
    renderQueue(state.queue, state.selectedId, $('excSummary').textContent);
    showDetail();
  });
  $('exceptionDetail').addEventListener('click', (e) => {
    if (e.target.dataset.act) return guard(e.target, () => exceptionAction(e.target.dataset.act));
    if (e.target.dataset.recommendation) return guard(e.target, () => recommendationDecision(e.target.dataset.recommendation, e.target.dataset.decision));
    const idx = e.target.dataset.useResolution;
    if (idx !== undefined) {
      const note = state.similar[state.selectedId]?.results?.[Number(idx)]?.resolution?.note;
      if (note) {
        $('excNote').value = note;
        $('excNote').focus();
      }
    }
  });

  $('searchForm').addEventListener('submit', (e) => {
    e.preventDefault();
    runSearch();
  });
  $('searchExamples').addEventListener('click', (e) => {
    if (!e.target.dataset.example) return;
    e.preventDefault();
    $('searchInput').value = e.target.dataset.example;
    if (!$('searchBtn').disabled) runSearch();
  });
  $('searchCreate').addEventListener('click', (e) =>
    guard(e.target, async () => renderSearchStatus((await api('/api/search/indexes', { method: 'POST' })).status))
  );

  $('kpiRefresh').addEventListener('click', (e) => guard(e.target, () => refreshKpis(true)));
  $('telRefresh').addEventListener('click', (e) => guard(e.target, refreshTelemetry));
  for (const [id, kind] of [['telNormal', 'normal'], ['telExcursion', 'excursion']]) {
    $(id).addEventListener('click', (e) =>
      guard(e.target, async () => {
        await api('/api/telemetry/reading', { method: 'POST', body: { profile: state.profileId, kind } }).catch((err) => alert(err.message));
        await refreshTelemetry();
      })
    );
  }

  $('stageButtons').addEventListener('click', (e) => {
    const stage = e.target.dataset.stage;
    if (!stage) return;
    guard(e.target, async () => {
      renderResult('valResult', `Running collMod for stage "${stage}"…`);
      try {
        renderResult('valResult', await api('/api/validation/stage', { method: 'POST', body: { stage } }));
      } catch (err) {
        renderResult('valResult', errText(err));
      }
      await refreshValidation();
    });
  });
  const submit = (path, body) => async (e) =>
    guard(e.target, async () => {
      try {
        renderResult('valResult', await api(path, { method: 'POST', body: { profile: state.profileId, ...body(e) } }));
      } catch (err) {
        renderResult('valResult', errText(err));
      }
      await refreshValidation();
    });
  $('validProbe').addEventListener('click', submit('/api/validation/valid', () => ({})));
  $('invalidButtons').addEventListener('click', (e) => e.target.dataset.invalid && submit('/api/validation/invalid', (ev) => ({ sampleId: ev.target.dataset.invalid }))(e));
  $('invalidDocs').addEventListener('click', (e) => {
    if (e.target.dataset.repair) submit('/api/validation/repair', (ev) => ({ id: ev.target.dataset.repair }))(e);
    if (e.target.dataset.remove) submit('/api/validation/remove', (ev) => ({ id: ev.target.dataset.remove }))(e);
  });

  $('resetBtn').addEventListener('click', (e) =>
    guard(e.target, async () => {
      renderResult('resetResult', 'Resetting…');
      try {
        const r = await api('/api/reset', { method: 'POST', body: { confirm: $('resetConfirm').value } });
        renderResult('resetResult', { dropped: r.dropped, validationStage: r.validationStage, search: r.search });
        $('resetConfirm').value = '';
      } catch (err) {
        renderResult('resetResult', errText(err));
      }
      await refreshAll();
    })
  );
}
