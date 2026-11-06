import { $, esc, fmtTime, fmtMin, badge, STATUS_TONE } from './lib.js';

export const IMPACT_TONE = { critical: 'red', high: 'red', watch: 'amber', none: 'green' };

function impactText(i) {
  if (!i) return '—';
  const short = i.shortfallHours > 0 ? ` ${i.shortfallHours} h short (${i.unitsShort} ${esc(i.unit)})` : '';
  return `${badge(i.level, IMPACT_TONE[i.level])}${short}<ul class="reasons">${(i.reasons ?? []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`;
}

export function renderOverview(o, profile) {
  const a = o.assessment;
  const sev = o.openExceptionsBySeverity;
  const sevText = Object.entries(sev).map(([k, n]) => badge(`${n} ${k}`, STATUS_TONE[k])).join('') || badge('none', 'green');
  const kpi = (label, value) => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${value}</div></div>`;
  const rel = o.reliability;
  $('overview').innerHTML = [
    kpi(profile.vocabulary.entity, `${esc(o.entity.trackedEntityId)}<div class="small muted">${esc(o.entity.product.name)}</div>`),
    kpi('Route', `${esc(o.entity.origin.name)} → ${esc(o.entity.destination.name)}`),
    kpi('Latest reliable milestone', o.latestReliableMilestone ? `${esc(o.latestReliableMilestone.label)}<div class="small muted">${fmtTime(o.latestReliableMilestone.actualTime)}</div>` : '—'),
    kpi('Next milestone', a.nextMilestone ? `${esc(a.nextMilestone.label)} ${badge(a.nextMilestone.status, STATUS_TONE[a.nextMilestone.status])}<div class="small muted">plan ${fmtTime(a.nextMilestone.plannedTime)}</div>` : 'all complete'),
    kpi('Milestone risk', `${badge(a.riskLevel, a.riskLevel === 'high' ? 'red' : a.riskLevel === 'medium' ? 'amber' : 'green')} projected final ${fmtMin(a.projectedFinalLateMin)}`),
    kpi(profile.impact?.label ?? 'Business impact', impactText(o.businessImpact)),
    kpi('Open exceptions', `${o.openExceptionCount} ${sevText}${o.exceptionSummary.slaBreached ? ` ${badge(`${o.exceptionSummary.slaBreached} SLA breached`, 'red')}` : ''}`),
    kpi('Event reliability', `${badge(`${rel.duplicatesSuppressed} duplicates suppressed`, 'blue')}${badge(`${rel.byStatus.late ?? 0} late`, 'amber')}${badge(`${rel.byStatus.out_of_order ?? 0} out of order`, 'amber')}${rel.unlinked ? badge(`${rel.unlinked} unlinked`, 'red') : ''}`),
  ].join('');
}

export function renderMilestones(o) {
  const rows = o.assessment.milestones.map(
    (m) => `<tr>
      <td>${m.sequence}. ${esc(m.label)}<div class="small muted">${esc(m.source)}${m.reports > 1 ? ` · ${m.reports} reports` : ''}</div></td>
      <td>${fmtTime(m.plannedTime)}</td>
      <td>${m.actualTime ? fmtTime(m.actualTime) : m.projectedTime ? `<span class="muted">proj ${fmtTime(m.projectedTime)}</span>` : '—'}</td>
      <td>${badge(m.status.replace('_', ' '), STATUS_TONE[m.status])} ${m.varianceMin != null ? fmtMin(m.varianceMin) : ''}
        <ul class="reasons">${m.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul></td></tr>`
  );
  $('milestones').innerHTML = `<table><tr><th>Milestone</th><th>Planned</th><th>Actual</th><th>Status and reason</th></tr>${rows.join('')}</table>
    <p class="small muted">Status is derived from milestone sequence and the earliest report per milestone, so late or re-sent events cannot move the timeline backward.</p>`;
}

const FLAG_LABEL = {
  late: 'late arrival',
  out_of_order: 'out of order',
  milestone_already_recorded: 'milestone already recorded',
  milestone_earlier_report: 'earlier report of recorded milestone',
  future_event_time: 'event time in future',
};

const CHANNEL_LABEL = { edi: 'EDI', api: 'API', wms_scan: 'WMS scan', gps: 'GPS', iot: 'IoT', manual: 'manual', batch_file: 'batch file' };

export function renderTimeline(events, rel, flashId) {
  $('relSummary').textContent = `${events.length} stored · ${rel.duplicatesSuppressed} duplicates suppressed`;
  const rows = events.map((e) => {
    const r = e.reliability;
    const tags = [];
    if (!e.schemaValid) tags.push(badge('fails schema', 'red', e.demo?.issue));
    if (!e.linked) tags.push(badge('no trackedEntityId', 'red'));
    if (e.eventTimeType === 'string') tags.push(badge('eventTime is a string', 'red'));
    if (r) {
      tags.push(badge(r.status.replace('_', ' '), STATUS_TONE[r.status]));
      for (const f of r.flags.filter((x) => x !== r.status)) tags.push(badge(FLAG_LABEL[f] ?? f, 'amber'));
      if (r.duplicateCount) tags.push(badge(`${r.duplicateCount} duplicate${r.duplicateCount > 1 ? 's' : ''} suppressed`, 'blue'));
    }
    if (e.demo?.repairedAt) tags.push(badge('repaired', 'green'));
    const lag = r ? `${r.lagMinutes} min lag` : '';
    const raw = e.details?.raw;
    const rawBlock = raw
      ? `<details class="raw"><summary>raw ${esc(CHANNEL_LABEL[e.channel] ?? e.channel ?? 'payload')}</summary><pre>${esc(typeof raw === 'string' ? raw.replaceAll('~', '~\n') : JSON.stringify(raw, null, 2))}</pre></details>`
      : '';
    return `<tr class="${e._id === flashId ? 'flash' : ''}">
      <td>${e.eventTimeType === 'date' ? fmtTime(e.eventTime) : esc(e.eventTime)}<div class="small muted">rcv ${fmtTime(e.receivedAt)} ${lag}</div></td>
      <td>${badge(e.eventType)}${e.channel ? badge(CHANNEL_LABEL[e.channel] ?? e.channel, 'blue') : ''}${e.milestoneId ? `<div class="small">${esc(e.milestoneId)}</div>` : ''}<div class="small muted">${esc(e.source)}</div></td>
      <td>${esc(e.description)}<div>${tags.join('')}</div>${rawBlock}</td></tr>`;
  });
  $('timeline').innerHTML = `<table><tr><th>Event time</th><th>Type / source</th><th>Event</th></tr>${rows.join('')}</table>`;
}

export function renderQueue(queue, selectedId, summaryText) {
  $('excSummary').textContent = summaryText;
  if (!queue.length) {
    $('exceptionQueue').innerHTML = '<p class="muted">No exceptions for this profile.</p>';
    return;
  }
  const rows = queue.map(
    (e, i) => `<tr class="clickable ${e.exceptionId === selectedId ? 'selected' : ''}" data-id="${esc(e.exceptionId)}">
      <td>${e.status === 'resolved' ? '' : i + 1}</td>
      <td>${esc(e.categoryLabel)}<div class="small muted">${esc(e.exceptionId)} · ${e.relatedEventIds.length} event${e.relatedEventIds.length > 1 ? 's' : ''}</div></td>
      <td>${badge(e.severity, STATUS_TONE[e.severity])}${badge(e.status, STATUS_TONE[e.status])}${e.status !== 'resolved' && e.businessImpact && e.businessImpact.level !== 'none' ? badge(`impact ${e.businessImpact.level}`, IMPACT_TONE[e.businessImpact.level]) : ''}</td>
      <td>${e.priority.score}<div class="small muted" title="${esc(e.priority.reasons.join('\n'))}">${esc(e.priority.reasons.slice(0, 2).join(', '))}</div></td>
      <td>${e.status === 'resolved' ? '—' : e.priority.minutesToSla < 0 ? badge(`breached ${-e.priority.minutesToSla} min`, 'red') : `${e.priority.minutesToSla} min`}</td>
      <td>${esc(e.owner ?? '—')}</td></tr>`
  );
  $('exceptionQueue').innerHTML = `<table><tr><th>#</th><th>Exception</th><th>Severity / status</th><th>Priority</th><th>SLA</th><th>Owner</th></tr>${rows.join('')}</table>
    <p class="small muted">Priority = severity + SLA urgency + category impact + business impact (stock cover vs. projected arrival); acknowledged work is lowered slightly. Hover a score for every rule applied.</p>`;
}

export function renderDetail(e, profile) {
  const el = $('exceptionDetail');
  if (!e) return el.classList.add('hidden');
  el.classList.remove('hidden');
  const can = (a) => ({ acknowledge: ['open'], assign: ['open', 'acknowledged'], resolve: ['open', 'acknowledged'], note: ['open', 'acknowledged', 'resolved'] })[a].includes(e.status);
  const recommendations = (e.recommendedActions ?? [])
    .map((action) => {
      const decision = e.actionDecisions?.[action.actionId];
      const decisionText = decision
        ? `<div class="small">${badge(decision.decision, decision.decision === 'accepted' ? 'green' : 'amber')} ${esc(decision.by)} · ${fmtTime(decision.at)}${decision.note ? `: ${esc(decision.note)}` : ''}</div>`
        : e.status === 'resolved'
          ? '<div class="small muted">No decision recorded before resolution.</div>'
          : `<div class="row"><button data-recommendation="${esc(action.actionId)}" data-decision="accepted">Accept action</button><button class="secondary" data-recommendation="${esc(action.actionId)}" data-decision="declined">Decline</button></div>`;
      return `<article class="similar"><h4>${badge(action.level, IMPACT_TONE[action.level])} ${esc(action.title)}</h4>
        <p class="small">${esc(action.rationale)}</p><p class="small"><b>Decision due:</b> ${fmtTime(action.decisionDueAt)} · ${esc(action.expectedOutcome)}</p>${decisionText}</article>`;
    })
    .join('');
  el.innerHTML = `<h3>${esc(e.exceptionId)}: ${esc(e.summary)}</h3>
    <div class="small">Triggered by ${esc(e.triggeringEvent.source)} event ${esc(e.triggeringEvent.sourceEventId)} at ${fmtTime(e.triggeringEvent.eventTime)} ·
      SLA due ${fmtTime(e.slaDueAt)} · ${e.relatedEventIds.length} related event(s)</div>
    ${e.resolution ? `<div class="small">${badge('resolution', 'green')} ${esc(e.resolution.note)} (${esc(e.resolution.by)})</div>` : ''}
    <div class="row">
      <input id="excOperator" placeholder="your name (operator)" value="${esc(localStorage.operator ?? '')}" />
      <button data-act="acknowledge" ${can('acknowledge') ? '' : 'disabled'}>Acknowledge</button>
      <input id="excOwner" placeholder="${esc(profile.vocabulary.operator)}" />
      <button data-act="assign" class="secondary" ${can('assign') ? '' : 'disabled'}>Assign</button>
    </div>
    <div class="row">
      <input id="excNote" class="grow" placeholder="note or resolution" />
      <button data-act="note" class="secondary">Add note</button>
      <button data-act="resolve" ${can('resolve') ? '' : 'disabled'}>Resolve</button>
    </div>
    <div id="excActionResult" class="small"></div>
    ${recommendations ? `<section class="recommendations"><h3>Recommended actions</h3><p class="small muted">Explainable recommendations; accepting one records an operator decision, not an automatic change.</p>${recommendations}</section>` : ''}
    <div id="similarCases" class="similar"></div>
    <ul class="audit">${[...e.audit].reverse().map((a) => `<li>${fmtTime(a.at)} <b>${esc(a.action)}</b> by ${esc(a.by)}${a.owner ? ` → ${esc(a.owner)}` : ''}${a.note ? `: ${esc(a.note)}` : ''}</li>`).join('')}</ul>`;
}

// Similar resolved cases from Atlas Vector Search with Automated Embedding.
export function renderSimilar(res, canResolve) {
  const el = $('similarCases');
  if (!el) return;
  if (!res) {
    el.innerHTML = '<h3>Similar resolved cases</h3><p class="small muted">Looking up similar cases…</p>';
    return;
  }
  if (res.error) {
    el.innerHTML = `<h3>Similar resolved cases</h3><p class="small muted">${esc(res.error)}</p>`;
    return;
  }
  if (!res.results.length) {
    el.innerHTML = '<h3>Similar resolved cases</h3><p class="small muted">No resolved cases in this profile yet.</p>';
    return;
  }
  el.innerHTML = `<h3>Similar resolved cases <span class="small muted">$vectorSearch, Automated Embedding (voyage-4)</span></h3>
    <ul class="audit">${res.results
      .map(
        (r, i) => `<li>${badge(Number(r.score).toFixed(2), 'blue', 'vectorSearchScore')} <b>${esc(r.categoryLabel)}</b> · ${esc(r.trackedEntityId)}
          <div class="small">${esc(r.summary)}</div>
          <div class="small">${badge('resolution', 'green')} ${esc(r.resolution?.note ?? '')} <span class="muted">(${esc(r.resolution?.by ?? '')})</span>
          ${canResolve && r.resolution?.note ? `<button class="secondary" data-use-resolution="${i}">Use this resolution</button>` : ''}</div></li>`
      )
      .join('')}</ul>`;
}
