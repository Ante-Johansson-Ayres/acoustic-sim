import { test } from 'node:test';
import assert from 'node:assert/strict';

import { newMeasurementDb, outputReplyCode } from '../js/hardware/replies.js';

test('a measurement is matched by count and sector, not by being the last record', () => {
  const frame = {configuration: {rows: 7, columns: 7}, last_record: {type: 'tone'},
    last_measurement: {count: 5, row: 3, column: 3, level_db: -12.5}};
  assert.equal(newMeasurementDb(frame, 24, 4), -12.5);
  assert.equal(newMeasurementDb(frame, 24, 5), null);
  assert.equal(newMeasurementDb(frame, 23, 4), null);
  assert.equal(newMeasurementDb({...frame, last_measurement: null}, 24, 4), null);
});

test('the CH1 reply is matched to the selection that was sent', () => {
  const frame = {sequence: 20, ch1: {source: 1, index: 7, code: 0}};
  assert.equal(outputReplyCode(frame, 1, 7, 19), 0);
  assert.equal(outputReplyCode(frame, 1, 8, 19), null);
  assert.equal(outputReplyCode(frame, 1, 7, 20), null);
  assert.equal(outputReplyCode({sequence: 21, ch1: {source: 1, index: 7, code: -1}}, 1, 7, 19), -1);
});
