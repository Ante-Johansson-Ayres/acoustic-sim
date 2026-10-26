import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LABEL_KEY, labelPayload, labelSummary, loadLabel, parseNumber } from '../js/hardware/reclabel.js';
import { listenRecord } from '../js/hardware/listenfilter.js';

const storage = (value) => ({getItem: (key) => (key === LABEL_KEY ? value : null)});

test('numbers take a decimal point or comma; empty is null, other text is NaN', () => {
  assert.equal(parseNumber('1,5'), 1.5);
  assert.equal(parseNumber(' -12.25 '), -12.25);
  assert.equal(parseNumber(''), null);
  assert.ok(Number.isNaN(parseNumber('about 2')));
});

test('the payload converts numbers, leaves out a bad one and names it', () => {
  const {label, problems} = labelPayload({class: 'drone', source: ' YouTube clip ', distance_m: '1,5',
    azimuth_deg: 'left', elevation_deg: '', notes: 'speaker on a stand'});
  assert.equal(label.class, 'drone');
  assert.equal(label.source, 'YouTube clip');
  assert.equal(label.distance_m, 1.5);
  assert.equal(label.azimuth_deg, null);
  assert.equal(label.elevation_deg, null);
  assert.equal(label.notes, 'speaker on a stand');
  assert.deepEqual(problems, ['Azimuth (°) "left" is not a number']);
  assert.equal(labelPayload({class: ''}).label.class, null);
});

test('saved labels load as text; an unknown class or a broken store gives empty labels', () => {
  assert.equal(loadLabel(storage(JSON.stringify({class: 'not_drone', distance_m: 2}))).distance_m, '2');
  assert.equal(loadLabel(storage(JSON.stringify({class: 'bird'}))).class, '');
  assert.equal(loadLabel(storage('{broken')).class, '');
  assert.equal(loadLabel(undefined).source, '');
});

test('the summary shows class, source and distance', () => {
  assert.equal(labelSummary({class: 'drone', source: 'YouTube clip', distance_m: '1,5'}), 'Drone · YouTube clip · 1.5 m');
  assert.equal(labelSummary({}), 'not set');
});

test('the listening copy record describes its filter', () => {
  const band = listenRecord({kind: 'band', low: 500, high: 3500}, 'mcu');
  assert.equal(band.suffix, 'band-0.5-3.5kHz');
  assert.equal(band.low_hz, 500);
  assert.equal(band.high_hz, 3500);
  const bank = listenRecord({kind: 'bank', antiAlias: Float64Array.of(0.5, 0.5), bandPass: Float64Array.of(1, -1)}, 'bank');
  assert.equal(bank.suffix, 'bank-filter');
  assert.deepEqual(bank.anti_alias_taps, [0.5, 0.5]);
  assert.equal(bank.band_pass_factor, 4);
});
