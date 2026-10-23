import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bandGain, bandWav, readWav, writeWav } from '../js/hardware/listenfilter.js';

const RATE = 48000;
const db = (x) => 20 * Math.log10(x);

test('the band copy uses the MCU-window filter: fourth-order high-pass at 500 Hz, low-pass at 3.5 kHz', () => {
  assert.ok(Math.abs(db(bandGain(100, RATE)) + 55.9) < 0.5);
  assert.ok(Math.abs(db(bandGain(250, RATE)) + 24.1) < 0.5);
  assert.ok(Math.abs(db(bandGain(1000, RATE))) < 0.1);
  assert.ok(Math.abs(db(bandGain(3500, RATE)) + 3.0) < 0.3);
  assert.ok(db(bandGain(10000, RATE)) < -18);
});

test('WAV files round-trip', () => {
  const samples = Int16Array.from([0, 1, -1, 32767, -32768, 1234]);
  const wav = readWav(writeWav({channels: 2, rateHz: RATE, samples}));
  assert.equal(wav.channels, 2);
  assert.equal(wav.rateHz, RATE);
  assert.deepEqual([...wav.samples], [...samples]);
});

test('the band copy removes a strong 50 Hz rumble and keeps 1 kHz, on each channel', () => {
  const frames = RATE;
  const samples = new Int16Array(frames * 2);
  for (let n = 0; n < frames; n++) {
    const t = n / RATE;
    samples[2 * n] = Math.round(32767 * (0.5 * Math.sin(2 * Math.PI * 50 * t) + 0.05 * Math.sin(2 * Math.PI * 1000 * t)));
    samples[2 * n + 1] = Math.round(32767 * 0.1 * Math.sin(2 * Math.PI * 1000 * t));
  }
  const out = readWav(bandWav(writeWav({channels: 2, rateHz: RATE, samples})));
  const rms = (ch) => {
    let sum = 0;
    for (let n = RATE / 10; n < frames; n++) sum += (out.samples[2 * n + ch] / 32768) ** 2;
    return Math.sqrt(sum / (frames - RATE / 10));
  };
  assert.ok(Math.abs(db(rms(0) / (0.05 / Math.SQRT2))) < 0.2);
  assert.ok(Math.abs(db(rms(1) / (0.1 / Math.SQRT2))) < 0.2);
});
