import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTS } from './load-ts.mjs';
const { api, eventResponse, ApiError } = await loadTS('../lib/client-api.ts');
test('safe server correlation IDs accompany actionable errors without arbitrary details', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: 'PROVIDER_RATE_LIMITED', message: 'Provider quota exceeded. Try later.', correlation_id: 'safe-123', details: { token: 'NEVER_DISPLAY' } } }), { status: 503 });
  await assert.rejects(api('setup'), e => e instanceof ApiError && e.correlationId === 'safe-123' && e.message.includes('Reference: safe-123') && !e.message.includes('NEVER_DISPLAY'));
});
test('malformed correlation identifiers are discarded', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { message: 'Unavailable.', correlation_id: '<private unsafe>' } }), { status: 500 });
  await assert.rejects(api('setup'), e => e.correlationId === undefined && e.message === 'Unavailable.');
});
test('event fetch uses header cursors and same origin credentials and announces auth expiry', async () => {
  const events = [], calls = [];
  globalThis.window = { dispatchEvent: e => events.push(e.type) };
  globalThis.fetch = async (url, init) => { calls.push([url, init]); return new Response('{}', { status: 401 }); };
  await assert.rejects(eventResponse('experiments/run/events', 14, new AbortController().signal), e => e.status === 401);
  assert.equal(calls[0][0], '/api/backend/experiments/run/events');
  assert.equal(calls[0][1].headers['Last-Event-ID'], '14');
  assert.equal(calls[0][1].credentials, 'same-origin');
  assert.deepEqual(events, ['counterforge:unauthorized']);
  delete globalThis.window;
});
