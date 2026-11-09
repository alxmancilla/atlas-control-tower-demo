import { EventEmitter } from 'node:events';
import { COLL } from '../db.js';
import { redact } from '../config.js';

// Collections watched by the live view. telemetry_readings is excluded: time-series
// collections do not support change streams. demo_state tells the page when maintenance starts and ends.
export const WATCHED = [COLL.events, COLL.exceptions, COLL.entities, COLL.telemetryRollups, COLL.state];

const NON_RESUMABLE = new Set([260, 280, 286]); // InvalidResumeToken, ChangeStreamFatalError, ChangeStreamHistoryLost

export function summarizeChange(change) {
  const doc = change.fullDocument ?? {};
  return {
    coll: change.ns?.coll,
    op: change.operationType,
    id: change.documentKey?._id != null ? String(change.documentKey._id) : null,
    profile: doc.profile ?? null,
    trackedEntityId: doc.trackedEntityId ?? null,
    eventType: doc.eventType ?? null,
    reliability: doc.reliability?.status ?? null,
    duplicateCount: doc.reliability?.duplicateCount ?? null,
    exceptionId: doc.exceptionId ?? null,
    exceptionStatus: change.ns?.coll === COLL.exceptions ? doc.status ?? null : null,
    description: doc.description ?? doc.summary ?? null,
    updatedFields: change.updateDescription ? Object.keys(change.updateDescription.updatedFields ?? {}) : [],
    // Change events written inside a multi-document transaction carry the session's txnNumber.
    txn: change.txnNumber != null ? String(change.txnNumber) : null,
    clusterTime: change.clusterTime?.t ? new Date(change.clusterTime.t * 1000) : null,
  };
}

/** One database-level change stream shared by all SSE clients, with resume and backoff. */
export class ChangeFeed extends EventEmitter {
  constructor(db, { maxBackoffMs = 30000 } = {}) {
    super();
    this.db = db;
    this.maxBackoffMs = maxBackoffMs;
    this.resumeToken = null;
    this.attempts = 0;
    this.stream = null;
    this.stopped = false;
    this.state = { state: 'stopped', error: null, since: new Date() };
  }

  setState(state, error = null) {
    this.state = { state, error, since: new Date(), attempts: this.attempts };
    this.emit('status', this.state);
  }

  start() {
    this.stopped = false;
    this.open();
    return this;
  }

  open() {
    const pipeline = [{ $match: { 'ns.coll': { $in: WATCHED }, operationType: { $in: ['insert', 'update', 'replace', 'delete'] } } }];
    const options = { fullDocument: 'updateLookup' };
    if (this.resumeToken) options.resumeAfter = this.resumeToken;
    this.setState(this.resumeToken ? 'resuming' : 'starting');
    const stream = this.db.watch(pipeline, options);
    this.stream = stream;
    const markLive = () => {
      if (this.state.state !== 'live') {
        this.attempts = 0;
        this.setState('live');
      }
    };
    stream.on('init', markLive);
    stream.on('resumeTokenChanged', (token) => {
      this.resumeToken = token;
      markLive();
    });
    stream.on('change', (change) => {
      this.resumeToken = change._id;
      markLive();
      this.emit('change', summarizeChange(change));
    });
    stream.on('error', (err) => this.handleError(stream, err));
  }

  async handleError(stream, err) {
    if (stream !== this.stream || this.stopped) return;
    this.stream = null;
    await stream.close().catch(() => {});
    if (NON_RESUMABLE.has(err?.code)) {
      this.resumeToken = null; // Cannot resume from the old position; restart and ask clients to resync.
      this.emit('resync', { reason: redact(err.message) });
    }
    this.attempts += 1;
    const delay = Math.min(this.maxBackoffMs, 1000 * 2 ** Math.min(this.attempts - 1, 5));
    this.setState('reconnecting', `${redact(err?.message)} (retry in ${Math.round(delay / 1000)}s)`);
    this.retryTimer = setTimeout(() => !this.stopped && this.open(), delay);
  }

  async stop() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    const s = this.stream;
    this.stream = null;
    if (s) await s.close().catch(() => {});
    this.setState('stopped');
  }
}
