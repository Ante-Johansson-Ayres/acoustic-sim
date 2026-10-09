import { test } from 'node:test';
import assert from 'node:assert/strict';

import { arrayGainDb, fitDetectorDecay, refinePeak, refinePeak2d, signalDb, snrDb } from '../js/study/analysis.js';

test('signal level subtracts the noise power', () => {
  assert.ok(Math.abs(signalDb(-20, -30) - 10 * Math.log10(0.01 - 0.001)) < 1e-9);
  assert.equal(signalDb(-40, -30), -Infinity);
});

test('SNR uses power above the floor', () => {
  assert.ok(Math.abs(snrDb(-20, -30) - 10 * Math.log10(9)) < 1e-9);
  assert.equal(snrDb(-30, -30), -Infinity);
});

test('array gain does not change with a path gain on either channel', () => {
  const base = {beamOn: -10, beamOff: -30, micOn: -25, micOff: -35};
  const shifted = {beamOn: -26.5, beamOff: -46.5, micOn: -19, micOff: -29};
  assert.ok(Math.abs(arrayGainDb(base) - (snrDb(-10, -30) - snrDb(-25, -35))) < 1e-9);
  assert.ok(Math.abs(arrayGainDb(base) - arrayGainDb(shifted)) < 1e-9);
});

test('the settle sweep fit recovers the detector time constant', () => {
  const tau = 8.665e-3;
  const quiet = 0.05;
  const loud = 0.8;
  const db = (raw) => 20 * Math.log10(raw);
  const points = [250, 500, 1000, 2000, 4000, 8000, 12000, 16000, 20000].map((settle_us) => ({
    settle_us, level_db: db(quiet + (loud - quiet) * Math.exp(-settle_us / 1e6 / tau)),
  }));
  const fit = fitDetectorDecay(points, db(quiet), db(loud));
  assert.ok(Math.abs(fit.tau_ms - 8.665) < 0.01);
  assert.ok(fit.points_used >= 4 && fit.r2 > 0.999);
  assert.equal(fitDetectorDecay(points.slice(0, 1), db(quiet), db(loud)), null);
});

test('peak interpolation finds the vertex between grid points', () => {
  const angles = [-6, -3, 0, 3, 6];
  const levels = angles.map((a) => -((a - 1.3) ** 2));
  assert.ok(Math.abs(refinePeak(levels, angles) - 1.3) < 1e-9);
  assert.equal(refinePeak([0, -1, -2], [0, 3, 6]), 0);
  const grid = [-3, 0, 3].map((e) => angles.map((a) => -((a - 1.3) ** 2) - (e + 0.6) ** 2));
  const peak = refinePeak2d(grid, angles, [-3, 0, 3]);
  assert.ok(Math.abs(peak.azimuth - 1.3) < 1e-9 && Math.abs(peak.elevation + 0.6) < 1e-9);
});
