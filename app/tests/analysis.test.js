import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MIN_SNR_DB, arrayGainDb, detectorScale, dynamicRange, fitDetectorDecay, refinePeak, refinePeak2d, remainingFraction,
  signalDb, snrDb, surveyRows } from '../js/study/analysis.js';

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

test('array gain is not a number when either path is below its noise', () => {
  assert.ok(Number.isNaN(arrayGainDb({beamOn: -10, beamOff: -30, micOn: -64.1, micOff: -64})));
  assert.ok(Number.isNaN(arrayGainDb({beamOn: -37.25, beamOff: -37.2, micOn: -40, micOff: -60})));
});

const levels = (entries) => new Map(entries.map(([microphone, mean_db]) => [microphone, {value: microphone, mean_db}]));

test('survey corrections compare source power above each microphone noise', () => {
  const rows = surveyRows(levels([[0, -40], [1, -43], [2, -38]]), levels([[0, -70], [1, -70], [2, -70]]), 0);
  assert.deepEqual(rows.map((row) => row.microphone), [0, 1, 2]);
  assert.ok(Math.abs(rows[0].gain_db) < 1e-12);
  assert.ok(Math.abs(rows[1].gain_db - (signalDb(-40, -70) - signalDb(-43, -70))) < 1e-12);
  assert.ok(rows[2].gain_db < 0);
  assert.ok(Math.abs(rows[1].snr_db - snrDb(-43, -70)) < 1e-12);
  assert.equal(rows[1].noise_dbfs, -70);
});

test('survey gives no correction below the SNR limit, without the noise pass or with a weak reference', () => {
  // The 2026-10-16 bench survey: microphone 0 at 1.8 dB SNR, microphone 2 at -13.6 dB.
  const bench = surveyRows(levels([[0, -59.65], [2, -63.89], [38, -64.07]]), levels([[0, -63.63], [2, -64.08], [38, -63.95]]), 0);
  assert.ok(bench.every((row) => row.gain_db === null));
  assert.equal(bench[2].snr_db, null);
  const noNoisePass = surveyRows(levels([[0, -40], [1, -43]]), new Map(), 0);
  assert.ok(noNoisePass.every((row) => row.gain_db === null && row.noise_dbfs === null && row.snr_db === null));
  const weakMic = surveyRows(levels([[0, -40], [1, -61]]), levels([[0, -70], [1, -70]]), 0);
  assert.ok(weakMic[0].gain_db === 0 && weakMic[1].gain_db === null && weakMic[1].snr_db < MIN_SNR_DB);
  const weakReference = surveyRows(levels([[0, -65], [1, -40]]), levels([[0, -70], [1, -70]]), 0);
  assert.ok(weakReference.every((row) => row.gain_db === null));
});

test('detector scale is the least-squares slope of reading against source level', () => {
  const captures = [{value: null, mean_db: -60}, {value: 0, mean_db: -40}, {value: 6, mean_db: -28.1},
    {value: 12, mean_db: -16}, {value: 12, mean_db: -15.9}];
  const scale = detectorScale(captures, -60);
  assert.ok(Math.abs(scale.ratio - 2) < 0.02);
  assert.equal(scale.step_db, 12);
  assert.ok(Math.abs(scale.seen_db - 12 * scale.ratio) < 1e-12);
  assert.ok(Math.abs(scale.margin_db - 20) < 1e-12);
  assert.equal(detectorScale(captures).margin_db, null);
  assert.equal(detectorScale([{value: 18, mean_db: -36}, {value: 18, mean_db: -34}]), null);
});

test('dynamic range runs from the floor to the highest level the reading still follows', () => {
  const floor = {value: null, mean_db: -64};
  const steps = [[-40, -63], [-30, -55], [-20, -45], [-10, -35], [0, -33], [10, -32.5]].map(([value, mean_db]) => ({value, mean_db}));
  const range = dynamicRange([floor, ...steps]);
  assert.ok(Math.abs(range.ceiling_db + 35) < 1e-9);
  assert.ok(Math.abs(range.range_db - 29) < 1e-9);
  assert.equal(dynamicRange(steps), null);
  assert.equal(dynamicRange([floor, steps[0]]), null);
  assert.equal(dynamicRange([floor, {value: 0, mean_db: -63.9}, {value: 18, mean_db: -63.8}]).range_db, null);
  const repeated = dynamicRange([floor, {value: 0, mean_db: -50}, {value: 0, mean_db: -50}, {value: 10, mean_db: -40}]);
  assert.ok(Math.abs(repeated.range_db - 24) < 1e-9);
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

test('per-point references follow a source whose level drifts during the sweep', () => {
  const tau = 8.665e-3;
  const db = (raw) => 20 * Math.log10(raw);
  const points = [500, 1000, 2000, 4000, 8000, 12000, 16000].map((settle_us, i) => {
    const loud = 0.5 + 0.06 * i;
    const quiet = 0.05 + 0.005 * i;
    return {settle_us, loud_db: db(loud), quiet_db: db(quiet),
      level_db: db(quiet + (loud - quiet) * Math.exp(-settle_us / 1e6 / tau))};
  });
  const fit = fitDetectorDecay(points);
  assert.ok(Math.abs(fit.tau_ms - 8.665) < 0.01);
  assert.equal(fit.points_used, 7);
  assert.ok(Math.abs(remainingFraction(points[3]) - Math.exp(-4 / 8.665)) < 1e-9);
});

test('readings without enough loud-to-quiet contrast are left out', () => {
  const flat = {settle_us: 1000, level_db: -20, loud_db: -19, quiet_db: -21};
  assert.ok(Number.isNaN(remainingFraction(flat)));
  assert.equal(fitDetectorDecay([flat, flat, flat]), null);
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
