import { test } from 'node:test';
import assert from 'node:assert/strict';

import { directionGridCommand, directionGridReady } from '../js/hardware/dirgrid.js';

const spec = {azimuthMin: -30, azimuthMax: 30, columns: 3, elevationMin: 0, elevationMax: 0, rows: 1};
const loaded = (azimuth, elevation, sequence = 12) => ({
  sequence,
  direction_set: {rows: 1, columns: 3, code: 0},
  configuration: {rows: 1, columns: 3},
  azimuth_deg: azimuth,
  elevation_deg: elevation,
});

test('the DG command uses centidegrees', () => {
  assert.equal(directionGridCommand({...spec, azimuthMin: -30.004}), 'DG,-3000,3000,3,0,0,1');
});

test('not ready while the previous angles of a same-sized grid are still shown', () => {
  assert.equal(directionGridReady(loaded([-10, 0, 10], [0]), spec, 10), false);
});

test('not ready between READY and the new AZIMUTH line', () => {
  assert.equal(directionGridReady(loaded([], []), spec, 10), false);
});

test('ready once the requested angles arrive; one row sits at the midpoint', () => {
  assert.equal(directionGridReady(loaded([-30, 0, 30], [0]), spec, 10), true);
  assert.equal(directionGridReady(loaded([-30, 0, 30], [0], 10), spec, 10), false);
});
