import { test } from 'node:test';
import assert from 'node:assert/strict';

import { runCsv } from '../js/study/runcsv.js';

test('nested metadata and results are written as JSON, not [object Object]', () => {
  const csv = runCsv({
    name: 'Beam cut', template: 'beamcut', time: '2026-10-09T00:00:00.000Z',
    meta: {device: 'emulator', settings: {settle_us: 1250, measure: 1}},
    captures: [{label: 'azimuth 0', value: 0, n: 10, mean_db: -20, std_db: 0.5, min_db: -21, max_db: -19}],
    result: {theory: [{angle_deg: 0, gain_db: 0}], microphones: [{microphone: 3, gain_db: -1.5}], summary: '20.0°'},
  });
  assert.ok(!csv.includes('[object Object]'));
  assert.match(csv, /^# settings: \{"settle_us":1250,"measure":1\}$/m);
  assert.match(csv, /^# result\.theory: \[\{"angle_deg":0,"gain_db":0\}\]$/m);
  assert.match(csv, /^# result\.summary: 20\.0°$/m);
  assert.match(csv, /^azimuth 0,0,10,-20\.00,0\.50,-21\.00,-19\.00$/m);
});
