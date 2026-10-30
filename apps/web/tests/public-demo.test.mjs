import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTS } from './load-ts.mjs';
const { publicDemoAssetURL, isFixturePreview, demoStateLabel, findingAssets } = await loadTS('../lib/public-demo.ts');

const assetId = '123e4567-e89b-42d3-a456-426614174000';
test('public demo asset URLs accept only snapshot-local UUIDs', () => {
  assert.equal(publicDemoAssetURL(assetId), '/api/public/demo/assets/' + assetId + '/content');
  assert.equal(publicDemoAssetURL('../projects'), null);
  assert.equal(publicDemoAssetURL('not-a-uuid'), null);
  assert.equal(publicDemoAssetURL(assetId + '?x=1'), null);
});
test('fixture previews are labeled and never presented as recorded real snapshots', () => {
  assert.equal(isFixturePreview({ state: 'FIXTURE_PREVIEW', provenance: 'LOCAL_FIXTURE' }), true);
  assert.equal(demoStateLabel({ state: 'FIXTURE_PREVIEW', provenance: 'LOCAL_FIXTURE' }), 'Local fixture preview');
  assert.equal(demoStateLabel({ state: 'PUBLISHED', provenance: 'REAL_PROVIDER' }), 'Recorded demo snapshot');
  assert.equal(demoStateLabel({ state: 'EMPTY', provenance: null }), 'Demo pending publication');
});
test('finding assets resolve only through the manifest asset list', () => {
  const asset = { id: assetId, role: 'ORIGINAL' };
  const manifest = { assets: [asset], findings: [] };
  assert.deepEqual(findingAssets(manifest, { asset_ids: [assetId, '123e4567-e89b-42d3-a456-426614174999'] }), [asset]);
  assert.deepEqual(findingAssets(manifest, { asset_ids: [] }), []);
});
