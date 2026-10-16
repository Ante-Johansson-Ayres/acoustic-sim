import { test } from 'node:test';
import assert from 'node:assert/strict';

import { captureSettings, runCsv } from '../js/study/runcsv.js';

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

test('labels with commas or quotes are quoted, so every row keeps its columns', () => {
  const csv = runCsv({
    name: 'Survey', template: 'microphones', time: '2026-10-16T00:00:00.000Z', meta: {},
    captures: [{label: 'microphone 0, source on', value: 0, n: 20, mean_db: -59.652, std_db: 4.44, min_db: -68.22, max_db: -50.29},
      {label: 'the "quiet" one', value: null, n: 1, mean_db: -64, std_db: 0, min_db: -64, max_db: -64}],
  });
  assert.match(csv, /^"microphone 0, source on",0,20,-59\.65,4\.44,-68\.22,-50\.29$/m);
  assert.match(csv, /^"the ""quiet"" one",,1,-64\.00,0\.00,-64\.00,-64\.00$/m);
});

test('settings that differ between captures become columns; the rest stay in the header', () => {
  const beam = {label: 'steered beam', value: 24, n: 5, mean_db: -30, std_db: 1, min_db: -31, max_db: -29,
    settings: {measure: 1, channel: 0, band: 1}};
  const mic = {...beam, label: 'microphone 0', value: 0, settings: {measure: 1, channel: 1, band: 1}};
  const used = captureSettings([beam, mic]);
  assert.deepEqual(used, {common: {measure: 1, band: 1}, varying: ['channel']});
  assert.equal(captureSettings([{label: 'no settings'}]), null);
  const csv = runCsv({name: 'Array gain', template: 'arraygain', time: '2026-10-16T00:00:00.000Z',
    meta: {settings: used.common, varying_settings: used.varying}, captures: [beam, mic]});
  assert.match(csv, /^label,value,n,mean_dbfs,std_db,min_dbfs,max_dbfs,channel$/m);
  assert.match(csv, /^steered beam,24,5,-30\.00,1\.00,-31\.00,-29\.00,0$/m);
  assert.match(csv, /^microphone 0,0,5,-30\.00,1\.00,-31\.00,-29\.00,1$/m);
});
