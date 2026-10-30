import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTS } from './load-ts.mjs';
const { filterCandidates } = await loadTS('../lib/candidate-filters.ts');
const candidates = [
 { id: 'invalid', family: 'brightness_shift', state: 'INVALID', validation: { verdict: 'INVALID', checks: [{ code: 'PIXELS_CHANGED', status: 'FAIL' }] } },
 { id: 'review', family: 'brightness_shift', state: 'VALIDATING', validation: { verdict: 'REVIEW_REQUIRED', checks: [{ code: 'SEMANTICS', status: 'UNKNOWN' }] } },
 { id: 'unsealed', family: 'background_clutter', state: 'DRAFT', validation: null },
 { id: 'valid', family: 'background_clutter', state: 'VALID', validation: { verdict: 'VALID', checks: [{ code: 'SEMANTICS', status: 'PASS' }] } },
];
test('rejection, review and unknown filters preserve all attempts and uncertainty', () => {
 assert.equal(filterCandidates(candidates, 'ALL', '', '').length, 4);
 assert.deepEqual(filterCandidates(candidates, 'INVALID', '', '').map(c => c.id), ['invalid']);
 assert.deepEqual(filterCandidates(candidates, 'REVIEW_REQUIRED', '', '').map(c => c.id), ['review', 'unsealed']);
 assert.deepEqual(filterCandidates(candidates, 'UNKNOWN', '', '').map(c => c.id), ['review', 'unsealed']);
 assert.deepEqual(filterCandidates(candidates, 'ALL', 'background_clutter', 'DRAFT').map(c => c.id), ['unsealed']);
});
