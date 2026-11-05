import { minutesBetween } from './milestoneRisk.js';
import { BadRequestError, ConflictError } from '../errors.js';

// ---------- Exception prioritization (transparent, profile-configurable) ----------
export function prioritizeException(exc, now, rules) {
  if (exc.status === 'resolved') return { score: 0, reasons: ['Resolved'], minutesToSla: null };
  const reasons = [];
  let score = 0;
  const sev = rules.severityPoints[exc.severity] ?? 0;
  score += sev;
  reasons.push(`${exc.severity} severity +${sev}`);

  const minutesToSla = minutesBetween(exc.slaDueAt, now);
  if (minutesToSla < 0) {
    score += rules.slaBreachedPoints;
    reasons.push(`SLA breached ${-minutesToSla} min ago +${rules.slaBreachedPoints}`);
  } else if (minutesToSla <= rules.slaSoonMin) {
    score += rules.slaSoonPoints;
    reasons.push(`SLA due in ${minutesToSla} min +${rules.slaSoonPoints}`);
  }

  const impact = (exc.impact ?? 0) * rules.impactPointsPerLevel;
  score += impact;
  reasons.push(`impact level ${exc.impact ?? 0} +${impact}`);

  if (exc.status === 'acknowledged') {
    score += rules.acknowledgedPoints;
    reasons.push(`acknowledged ${rules.acknowledgedPoints}`);
  }
  return { score, reasons, minutesToSla };
}

const STATUS_ORDER = { open: 0, acknowledged: 1, resolved: 2 };

export function buildQueue(exceptions, now, rules) {
  return exceptions
    .map((e) => ({ ...e, priority: prioritizeException(e, now, rules) }))
    .sort(
      (a, b) =>
        (a.status === 'resolved') - (b.status === 'resolved') ||
        b.priority.score - a.priority.score ||
        new Date(a.slaDueAt) - new Date(b.slaDueAt) ||
        STATUS_ORDER[a.status] - STATUS_ORDER[b.status]
    );
}

// ---------- Exception workflow transitions ----------
const ALLOWED = {
  acknowledge: ['open'],
  assign: ['open', 'acknowledged'],
  note: ['open', 'acknowledged', 'resolved'],
  resolve: ['open', 'acknowledged'],
};

// The exception's current state does not allow the action (HTTP 409).
export class WorkflowError extends ConflictError {}

export function checkExceptionAction(currentStatus, action, payload = {}) {
  const allowedFrom = ALLOWED[action];
  if (!allowedFrom) throw new BadRequestError(`Unknown action "${action}"`);
  if (!allowedFrom.includes(currentStatus)) {
    throw new WorkflowError(`Cannot ${action} an exception that is ${currentStatus}`);
  }
  if (action === 'assign' && !payload.owner?.trim()) throw new BadRequestError('Assign requires an owner');
  if ((action === 'note' || action === 'resolve') && !payload.note?.trim()) {
    throw new BadRequestError(`${action === 'resolve' ? 'Resolve requires a resolution note' : 'Note text is required'}`);
  }
  const nextStatus = action === 'acknowledge' ? 'acknowledged' : action === 'resolve' ? 'resolved' : currentStatus;
  return { allowedFrom, nextStatus };
}

// ---------- Event reliability classification ----------
/**
 * latestEventTime: newest eventTime among events already stored for the tracked entity.
 * milestoneRecordedAt: current actual time for the same milestone, if one is recorded.
 */
export function classifyArrival({ eventTime, receivedAt, latestEventTime, milestoneRecordedAt }, risk) {
  const lagMinutes = minutesBetween(receivedAt, eventTime);
  const flags = [];
  if (lagMinutes > risk.lateArrivalMin) flags.push('late');
  if (lagMinutes < -5) flags.push('future_event_time');
  if (latestEventTime && new Date(eventTime) < new Date(latestEventTime)) flags.push('out_of_order');
  if (milestoneRecordedAt) {
    flags.push(new Date(eventTime) < new Date(milestoneRecordedAt) ? 'milestone_earlier_report' : 'milestone_already_recorded');
  }
  const status = flags.includes('out_of_order') ? 'out_of_order' : flags.includes('late') ? 'late' : 'accepted';
  return { status, flags, lagMinutes };
}

// ---------- MongoDB 9.0 constraint gate ----------
export function parseVersion(version) {
  const [major, minor] = String(version ?? '').split('.').map((n) => Number.parseInt(n, 10));
  return { major, minor: Number.isNaN(minor) ? 0 : minor };
}

export function constraintGate(version) {
  const { major } = parseVersion(version);
  if (Number.isNaN(major)) {
    return { supported: false, reason: 'Server version could not be determined, so the constraint stage stays disabled.' };
  }
  if (major < 9) {
    return {
      supported: false,
      reason:
        `Connected server reports MongoDB ${version}. validationLevel "constraint" was added in MongoDB 9.0, ` +
        'so this stage is unavailable. Observe and Enforce still work. Use a cluster running 9.0 or later to try it.',
    };
  }
  return { supported: true, reason: `MongoDB ${version} supports validationLevel "constraint".` };
}
