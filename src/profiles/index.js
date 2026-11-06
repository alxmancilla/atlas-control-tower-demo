import retail from './retail.js';
import healthcare from './healthcare.js';
import manufacturing from './manufacturing.js';
import { CHANNELS } from '../domain/documents.js';

export const PROFILES = { retail, healthcare, manufacturing };
export const PROFILE_IDS = Object.keys(PROFILES);

export function getProfile(id) {
  const profile = PROFILES[id];
  if (!profile) throw new Error(`Unknown profile "${id}". Expected one of ${PROFILE_IDS.join(', ')}.`);
  return profile;
}

// Structural checks so a profile edit cannot silently break the shared workflow.
export function validateProfile(p) {
  const errors = [];
  const sources = Object.keys(p.sources || {});
  const milestoneIds = new Set((p.milestones || []).map((m) => m.id));
  const categories = Object.keys(p.exceptionCategories || {});
  if (!p.entity?.trackedEntityId) errors.push('entity.trackedEntityId is required');
  if (sources.length < 2) errors.push('at least two event sources are required');
  if (milestoneIds.size < 3) errors.push('at least three milestones are required');
  let previous = -Infinity;
  for (const m of p.milestones || []) {
    if (!sources.includes(m.source)) errors.push(`milestone ${m.id} uses unknown source ${m.source}`);
    if (!(m.plannedOffsetMin > previous)) errors.push(`milestone ${m.id} must be planned after the previous milestone`);
    previous = m.plannedOffsetMin;
  }
  for (const key of ['onTimeToleranceMin', 'missedGraceMin', 'atRiskWindowMin', 'lateArrivalMin']) {
    if (typeof p.risk?.[key] !== 'number') errors.push(`risk.${key} must be a number`);
  }
  const events = [...(p.seedEvents || []), ...(p.samples || []).filter((s) => !s.duplicateOf)];
  for (const [id, s] of Object.entries(p.sources || {})) {
    if (s.channel && !CHANNELS.includes(s.channel)) errors.push(`source ${id} uses unknown channel ${s.channel}`);
  }
  for (const e of events) {
    if (!sources.includes(e.source)) errors.push(`event ${e.sourceEventId} uses unknown source ${e.source}`);
    if (e.channel && !CHANNELS.includes(e.channel)) errors.push(`event ${e.sourceEventId} uses unknown channel ${e.channel}`);
    if (e.milestoneId && !milestoneIds.has(e.milestoneId)) errors.push(`event ${e.sourceEventId} uses unknown milestone`);
    if (e.exception && !categories.includes(e.exception.category)) errors.push(`event ${e.sourceEventId} uses unknown category`);
  }
  for (const s of (p.samples || []).filter((x) => x.duplicateOf)) {
    if (!p.samples.some((x) => x.id === s.duplicateOf)) errors.push(`sample ${s.id} duplicates unknown sample`);
  }
  if (p.telemetry?.enabled && !(p.telemetry.min < p.telemetry.max)) errors.push('telemetry.min must be below telemetry.max');
  for (const h of p.history || []) {
    if (!categories.includes(h.category)) errors.push(`history ${h.entityId} uses unknown category ${h.category}`);
    if (h.entityId === p.entity?.trackedEntityId) errors.push(`history ${h.entityId} must use a past entity, not the current one`);
    if (!h.summary || !h.resolution) errors.push(`history ${h.entityId} needs summary and resolution`);
  }
  const lines = p.entity?.orderLines ?? [];
  if (lines.length < 2) errors.push('entity.orderLines needs at least two lines');
  if (lines[0] && lines[0].destination?.id !== p.entity?.destination?.id) errors.push('the first order line must go to entity.destination');
  const qty = lines.reduce((n, l) => n + (l.quantity ?? 0), 0);
  if (lines.length && qty !== p.entity?.product?.quantity) errors.push(`order line quantities (${qty}) must add up to entity.product.quantity`);
  const keys = new Set();
  for (const l of lines) {
    const key = `${l.destination?.id}|${l.itemRef}`;
    if (keys.has(key)) errors.push(`order line ${l.orderRef} repeats destination and item ${key}`);
    keys.add(key);
    if (!(l.stopOffsetMin >= 0)) errors.push(`order line ${l.orderRef} needs stopOffsetMin >= 0`);
    if (!(l.position?.onHand >= 0 && l.position?.consumptionPerHour > 0)) errors.push(`order line ${l.orderRef} needs position.onHand >= 0 and consumptionPerHour > 0`);
  }
  if (!p.impact?.consequence) errors.push('impact.consequence is required');
  for (const level of ['critical', 'high', 'watch', 'none']) {
    if (typeof p.priority?.businessImpactPoints?.[level] !== 'number') errors.push(`priority.businessImpactPoints.${level} must be a number`);
  }
  return errors;
}

// Public view of a profile for the browser; omits nothing sensitive because profiles are synthetic.
export function profileSummary(p) {
  return {
    id: p.id,
    label: p.label,
    title: p.title,
    vocabulary: p.vocabulary,
    entity: p.entity,
    sources: p.sources,
    milestones: p.milestones.map(({ id, label, source }) => ({ id, label, source })),
    risk: p.risk,
    exceptionCategories: p.exceptionCategories,
    priority: p.priority,
    telemetry: p.telemetry,
    impact: p.impact,
    searchExamples: p.searchExamples,
    samples: p.samples.map(({ id, label, duplicateOf, eventType }) => ({ id, label, duplicateOf, eventType })),
    invalidSamples: p.invalidSamples.map(({ id, label, issue }) => ({ id, label, issue })),
  };
}
