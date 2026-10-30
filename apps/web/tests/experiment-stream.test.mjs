import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTS } from './load-ts.mjs';
const { observeExperiment, eventFrames } = await loadTS('../lib/experiment-stream.ts');
const response = text => new Response(text, { headers: { 'Content-Type': 'text/event-stream' } });
const frame = id => `id: ${id}\ndata: {"id":${id},"experiment_id":"run","event":"saved","payload":{}}\n\n`;
function options(overrides = {}) {
  return { experimentId: 'run', signal: new AbortController().signal,
    refresh: async () => ({ state: 'RUNNING' }), isTerminal: s => s.state === 'COMPLETED',
    history: async () => [], fetchStream: async () => response(''), onSnapshot() {}, onEvents() {}, onStatus() {}, onAuthority() {},
    sleep: async () => {}, ...overrides };
}
test('SSE frames support split UTF-8/CRLF, named events and keepalives', async () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\nid: 2\r\nevent: saved\r\ndata: {"label":"café"}\r\n\r\n');
  const stream = new ReadableStream({ start(c) { for (const byte of bytes) c.enqueue(Uint8Array.of(byte)); c.close(); } });
  const frames = [];
  for await (const f of eventFrames(stream, new AbortController().signal)) frames.push(f);
  assert.deepEqual(frames, [{ id: '2', data: '{"label":"café"}' }]);
});
test('every reconnect refreshes snapshot, forwards cursor, deduplicates and finishes with stored counts', async () => {
  const order = [], ids = [], cursors = [], authority = [];
  let reads = 0, connections = 0;
  await observeExperiment(options({
    refresh: async () => { order.push('snapshot'); return { state: ++reads >= 5 ? 'COMPLETED' : 'RUNNING', count: 7 }; },
    fetchStream: async cursor => { order.push('connect'); cursors.push(cursor); return response(++connections === 1 ? frame(2) + frame(2) + frame(1) : frame(2) + frame(3)); },
    onSnapshot: s => { assert.equal(s.count, 7); },
    onEvents: events => ids.push(...events.map(e => e.id)), onAuthority: value => authority.push(value),
    history: async after => after < 3 ? [{ id: 3, experiment_id: 'run', event: 'saved', payload: {} }] : [],
  }));
  assert.deepEqual(cursors, [0, 2]);
  assert.deepEqual(order, ['snapshot', 'connect', 'snapshot', 'snapshot', 'snapshot', 'connect', 'snapshot']);
  assert.deepEqual(ids, [2, 3]);
  assert.equal(authority.at(-1), true);
  assert.ok(authority.slice(1, -1).includes(false));
});
test('stream 401 stops retries and invalidates the previous snapshot', async () => {
  let calls = 0;
  const authority = [], status = [];
  await observeExperiment(options({ fetchStream: async () => { calls++; throw Object.assign(new Error('Session expired.'), { status: 401 }); }, onAuthority: a => authority.push(a), onStatus: s => status.push(s) }));
  assert.equal(calls, 1);
  assert.equal(authority.at(-1), false);
  assert.match(status.at(-1), /Sign in again/);
});
test('repeated dropped streams have bounded retry and never declare live success', async () => {
  let connections = 0;
  const authority = [], status = [];
  await observeExperiment(options({ fetchStream: async () => { connections++; return response(''); }, onAuthority: a => authority.push(a), onStatus: s => status.push(s) }));
  assert.equal(connections, 6);
  assert.equal(authority.at(-1), false);
  assert.match(status.at(-1), /Reload experiment/);
});
test('an aborted route never applies a late snapshot or opens a stream', async () => {
  const controller = new AbortController();
  let snapshots = 0, streams = 0;
  await observeExperiment(options({ signal: controller.signal, refresh: async () => { controller.abort(); return { state: 'COMPLETED' }; }, onSnapshot: () => snapshots++, fetchStream: async () => { streams++; return response(''); } }));
  assert.equal(snapshots, 0); assert.equal(streams, 0);
});
test('terminal initial snapshot reads history without opening another stream', async () => {
  let streams = 0;
  const ids = [];
  await observeExperiment(options({ refresh: async () => ({ state: 'COMPLETED' }), fetchStream: async () => { streams++; return response(''); }, history: async after => after ? [] : [{ id: 5, experiment_id: 'run', event: 'done', payload: {} }], onEvents: events => ids.push(...events.map(e => e.id)) }));
  assert.equal(streams, 0); assert.deepEqual(ids, [5]);
});
