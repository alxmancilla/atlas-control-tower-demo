import retail from './retail.js';
import healthcare from './healthcare.js';
import manufacturing from './manufacturing.js';

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
  for (const e of events) {
    if (!sources.includes(e.source)) errors.push(`event ${e.sourceEventId} uses unknown source ${e.source}`);
    if (e.milestoneId && !milestoneIds.has(e.milestoneId)) errors.push(`event ${e.sourceEventId} uses unknown milestone`);
    if (e.exception && !categories.includes(e.exception.category)) errors.push(`event ${e.sourceEventId} uses unknown category`);
  }
  for (const s of (p.samples || []).filter((x) => x.duplicateOf)) {
    if (!p.samples.some((x) => x.id === s.duplicateOf)) errors.push(`sample ${s.id} duplicates unknown sample`);
  }
  if (p.telemetry?.enabled && !(p.telemetry.min < p.telemetry.max)) errors.push('telemetry.min must be below telemetry.max');
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
    searchExamples: p.searchExamples,
    samples: p.samples.map(({ id, label, duplicateOf, eventType }) => ({ id, label, duplicateOf, eventType })),
    invalidSamples: p.invalidSamples.map(({ id, label, issue }) => ({ id, label, issue })),
  };
}
