const addMinutes = (date, min) => new Date(new Date(date).getTime() + min * 60000);

export const idempotencyKey = (source, sourceEventId) => `${source}:${sourceEventId}`;

// The unique idempotencyKey index is partial on { $type: 'string' }; the planner only uses it
// when the filter repeats that condition, so every lookup by key goes through this helper.
export const byIdempotencyKey = (key) => ({ idempotencyKey: { $eq: key, $type: 'string' } });

export function buildTrackedEntity(profile, anchorAt) {
  const { trackedEntityId, entityKind, product, origin, destination, metadata } = profile.entity;
  return {
    trackedEntityId,
    profile: profile.id,
    entityKind,
    product,
    origin,
    destination,
    plannedMilestones: profile.milestones.map((m, i) => ({
      milestoneId: m.id,
      label: m.label,
      sequence: i + 1,
      source: m.source,
      plannedTime: addMinutes(anchorAt, m.plannedOffsetMin),
      maxDwellMin: m.maxDwellMin,
    })),
    metadata,
    anchorAt: new Date(anchorAt),
    demo: { seeded: true },
  };
}

// Shared event envelope. Source-specific fields live in `details`.
export function buildEvent(profile, spec, { now, seeded = false }) {
  const evt = {
    trackedEntityId: profile.entity.trackedEntityId,
    profile: profile.id,
    eventType: spec.eventType,
    eventTime: addMinutes(now, spec.eventTimeOffsetMin ?? 0),
    source: spec.source,
    sourceEventId: spec.sourceEventId,
    idempotencyKey: idempotencyKey(spec.source, spec.sourceEventId),
    description: spec.description,
    details: spec.details ?? {},
    demo: { seeded },
  };
  if (spec.milestoneId) evt.milestoneId = spec.milestoneId;
  if (spec.notes) evt.notes = spec.notes;
  if (spec.exception) evt.exception = { category: spec.exception.category };
  return evt;
}

export const legacyTimeString = (date) => `${new Date(date).toISOString().slice(0, 16).replace('T', ' ')} UTC`;

// Intentionally malformed legacy documents, clearly marked with demo.invalidSample.
export function buildInvalidEvent(profile, spec, now) {
  const d = spec.doc;
  const when = addMinutes(now, d.eventTimeOffsetMin ?? 0);
  const doc = {
    profile: profile.id,
    eventType: d.eventType,
    eventTime: d.eventTimeString ? legacyTimeString(when) : when,
    receivedAt: new Date(now),
    source: d.source,
    description: d.description,
    details: d.details ?? {},
    demo: { invalidSample: true, issue: spec.issue, sampleId: spec.id },
  };
  if (d.trackedEntityId) doc.trackedEntityId = d.trackedEntityId;
  if (d.milestoneId) doc.milestoneId = d.milestoneId;
  return doc;
}

// Returns the $set needed to make a legacy event valid, or an error if it cannot be correlated.
export function planRepair(doc, profile, now = new Date()) {
  const set = {};
  if (typeof doc.trackedEntityId !== 'string' || !doc.trackedEntityId) {
    const refs = Object.values(profile.entity.metadata ?? {});
    if (doc.details?.legacyRef && refs.includes(doc.details.legacyRef)) {
      set.trackedEntityId = profile.entity.trackedEntityId;
    } else {
      return { error: 'No reference maps this event to a tracked entity. Remove it instead.' };
    }
  }
  if (!(doc.eventTime instanceof Date)) {
    const m = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC$/.exec(String(doc.eventTime ?? ''));
    if (!m) return { error: `eventTime "${doc.eventTime}" cannot be parsed. Remove it instead.` };
    set.eventTime = new Date(`${m[1]}T${m[2]}:00Z`);
  }
  if (!(doc.receivedAt instanceof Date)) set.receivedAt = new Date(now);
  if (typeof doc.sourceEventId !== 'string') set.sourceEventId = `LEGACY-${doc._id}`;
  if (typeof doc.idempotencyKey !== 'string') {
    set.idempotencyKey = idempotencyKey(doc.source, set.sourceEventId ?? doc.sourceEventId);
  }
  set['demo.repairedAt'] = new Date(now);
  return { set };
}
