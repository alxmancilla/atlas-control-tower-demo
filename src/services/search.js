import { COLL, serverError } from '../db.js';
import { memo, invalidate } from './cache.js';
import { BadRequestError } from '../errors.js';

// MongoDB Search (Atlas Search) index definitions. The same JSON can be pasted into the Atlas UI
// JSON editor (see README). Static mappings keep the index small and explicit.
export const SEARCH_INDEXES = [
  {
    collection: COLL.events,
    name: 'ops_events_search',
    definition: {
      mappings: {
        dynamic: false,
        fields: {
          description: { type: 'string' },
          notes: { type: 'string' },
          profile: { type: 'token' },
          source: { type: 'token' },
          eventType: { type: 'token' },
          trackedEntityId: { type: 'token' },
        },
      },
    },
  },
  {
    collection: COLL.exceptions,
    name: 'ops_exceptions_search',
    definition: {
      mappings: {
        dynamic: false,
        fields: {
          summary: { type: 'string' },
          categoryLabel: { type: 'string' },
          resolution: { type: 'document', fields: { note: { type: 'string' } } },
          profile: { type: 'token' },
          status: { type: 'token' },
          category: { type: 'token' },
        },
      },
    },
  },
];

export class SearchUnavailableError extends Error {}

// Reads index status from Atlas. Search is enabled only when every index reports queryable: true.
export async function searchStatus(db) {
  const indexes = [];
  for (const idx of SEARCH_INDEXES) {
    try {
      const [found] = await db.collection(idx.collection).listSearchIndexes(idx.name).toArray();
      indexes.push(
        found
          ? { name: idx.name, collection: idx.collection, status: found.status, queryable: found.queryable === true }
          : { name: idx.name, collection: idx.collection, status: 'MISSING', queryable: false }
      );
    } catch (err) {
      indexes.push({ name: idx.name, collection: idx.collection, status: 'UNAVAILABLE', queryable: false, error: serverError(err).message });
    }
  }
  const ready = indexes.every((i) => i.queryable);
  let message = 'MongoDB Search indexes are ready.';
  if (!ready) {
    if (indexes.some((i) => i.status === 'UNAVAILABLE')) message = 'MongoDB Search is unavailable on this connection. See the error per index.';
    else if (indexes.some((i) => i.status === 'MISSING')) message = 'Search indexes are missing. Run "npm run search:create" or create them in the Atlas UI.';
    else message = 'Search indexes are building. Search is disabled until Atlas reports them queryable.';
  }
  return { ready, message, indexes, checkedAt: new Date() };
}

// Search index state changes slowly (builds take seconds to minutes), so per-request checks share a 5 s result.
export const cachedSearchStatus = (db) => memo(`${db.databaseName}:searchStatus`, 5000, () => searchStatus(db));

export async function createSearchIndexes(db) {
  invalidate(`${db.databaseName}:searchStatus`);
  const results = [];
  const status = await searchStatus(db);
  for (const idx of SEARCH_INDEXES) {
    const current = status.indexes.find((i) => i.name === idx.name);
    if (current.status !== 'MISSING') {
      results.push({ name: idx.name, action: 'skipped', status: current.status, error: current.error });
      continue;
    }
    try {
      await db.collection(idx.collection).createSearchIndex({ name: idx.name, definition: idx.definition });
      results.push({ name: idx.name, action: 'requested' });
    } catch (err) {
      results.push({ name: idx.name, action: 'failed', error: serverError(err).message });
    }
  }
  return results;
}

const branchProject = (kind, title, text) => ({
  $project: {
    _id: 0,
    kind: { $literal: kind },
    id: { $toString: '$_id' },
    trackedEntityId: 1,
    title,
    text,
    source: 1,
    eventType: 1,
    status: 1,
    severity: 1,
    exceptionId: 1,
    eventTime: 1,
    score: { $meta: 'searchScore' },
    highlights: { $meta: 'searchHighlights' },
  },
});

export function searchPipelines(profile, { q, kind = 'all', source, eventType, status, limit = 10 }) {
  const eventFilter = [{ equals: { path: 'profile', value: profile.id } }];
  if (source) eventFilter.push({ equals: { path: 'source', value: source } });
  if (eventType) eventFilter.push({ equals: { path: 'eventType', value: eventType } });
  const excFilter = [{ equals: { path: 'profile', value: profile.id } }];
  if (status) excFilter.push({ equals: { path: 'status', value: status } });

  const eventPaths = ['description', 'notes'];
  const excPaths = ['summary', 'categoryLabel', 'resolution.note'];
  const events = [
    { $search: { index: 'ops_events_search', compound: { must: [{ text: { query: q, path: eventPaths, fuzzy: { maxEdits: 1 } } }], filter: eventFilter }, highlight: { path: eventPaths } } },
    { $limit: limit },
    branchProject('event', '$description', '$notes'),
  ];
  const exceptions = [
    { $search: { index: 'ops_exceptions_search', compound: { must: [{ text: { query: q, path: excPaths, fuzzy: { maxEdits: 1 } } }], filter: excFilter }, highlight: { path: excPaths } } },
    { $limit: limit },
    branchProject('exception', '$summary', '$resolution.note'),
  ];
  if (kind === 'events') return { collection: COLL.events, pipeline: events };
  if (kind === 'exceptions') return { collection: COLL.exceptions, pipeline: exceptions };
  return {
    collection: COLL.events,
    pipeline: [...events, { $unionWith: { coll: COLL.exceptions, pipeline: exceptions } }, { $sort: { score: -1 } }, { $limit: limit * 2 }],
  };
}

export async function runSearch(db, profile, params) {
  const q = String(params.q ?? '').trim().slice(0, 200);
  if (!q) throw new BadRequestError('Enter search text');
  const status = await cachedSearchStatus(db);
  if (!status.ready) throw new SearchUnavailableError(status.message);
  const { collection, pipeline } = searchPipelines(profile, { ...params, q });
  const results = await db.collection(collection).aggregate(pipeline).toArray();
  return { q, results, status };
}
