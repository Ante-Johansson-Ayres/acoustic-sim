import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { alphaFor, bypassTaps, design, designProblems, firDb, firGain, tauMs, toWords, transitionHz } from '../js/study/filters.js';
import { bandGain, listenGain, listenSuffix, listenWav, readWav, stuffTaps, writeWav } from '../js/hardware/listenfilter.js';

const contract = JSON.parse(readFileSync(new URL('../../../MAX78002/SigmaStudioBank7x7/beam_table_2d.json', import.meta.url), 'utf8'));

test('the designer reproduces the flashed bank band-pass (0.5-3.5 kHz, Hamming, 21 taps at 12 kHz)', () => {
  const taps = design({type: 'bandpass', low: 500, high: 3500, taps: 21, rate: 12000, window: 'hamming'});
  contract.bank.band_pass.forEach((v, k) => assert.ok(Math.abs(taps[k] - v) < 1e-12, `tap ${k}`));
  assert.ok(Math.abs(firGain(taps, 0, 12000)) < 1e-12);
  assert.ok(Math.abs(firGain(taps, 2000, 12000) - 1) < 1e-12);
});

test('low-pass and high-pass designs: unity in the pass band, attenuation in the stop band', () => {
  const low = design({type: 'lowpass', high: 5000, taps: 31, rate: 48000});
  assert.ok(Math.abs(firGain(low, 0, 48000) - 1) < 1e-12);
  assert.ok(firDb(low, 15000, 48000) < -40);
  const high = design({type: 'highpass', low: 2000, taps: 21, rate: 12000});
  assert.ok(Math.abs(firGain(high, 6000, 12000) - 1) < 1e-12);
  assert.ok(Math.abs(firGain(high, 0, 12000)) < 1e-12);
  assert.ok(firDb(high, 300, 12000) < -20);
  [low, high].forEach((taps) => taps.forEach((v, k) => assert.ok(Math.abs(v - taps[taps.length - 1 - k]) < 1e-15)));
  assert.deepEqual(bypassTaps(5), [0, 0, 1, 0, 0]);
});

test('design problems: errors stop a design, warnings say what the taps cannot do', () => {
  assert.equal(Math.round(transitionHz(31, 48000)), 5110);
  assert.ok(designProblems({type: 'bandpass', low: 3000, high: 1000, taps: 21, rate: 12000}).errors.length);
  assert.ok(designProblems({type: 'lowpass', high: 7000, taps: 21, rate: 12000}).errors.length);
  const ch1 = designProblems({type: 'highpass', low: 500, taps: 31, rate: 48000});
  assert.equal(ch1.errors.length, 0);
  assert.match(ch1.warnings[0], /does not stop the sound below it/);
  assert.equal(designProblems({type: 'bandpass', low: 500, high: 3500, taps: 21, rate: 12000}).warnings.length, 1);
});

test('8.24 words and the detector time constant', () => {
  assert.deepEqual(toWords([1, -0.5, 2 ** -24]), [2 ** 24, -(2 ** 23), 1]);
  assert.throws(() => toWords([200]));
  assert.ok(Math.abs(tauMs(alphaFor(34.66, 12000), 12000) - 34.66) < 1e-9);
  const word = contract.bank.level_time_constant_ms;
  assert.ok(Math.abs(word - 34.66) < 0.01);
});

test('the listening copy: any band corners, or the bank filters at 48 kHz', () => {
  assert.ok(Math.abs(20 * Math.log10(bandGain(1000, 48000, 100, 4000))) < 0.1);
  assert.ok(20 * Math.log10(bandGain(50, 48000, 100, 4000)) < -20);
  const bank = {kind: 'bank', antiAlias: design({type: 'lowpass', high: 5000, taps: 31, rate: 48000}), bandPass: contract.bank.band_pass};
  assert.ok(Math.abs(20 * Math.log10(listenGain(bank, 2000, 48000))) < 0.5);
  assert.ok(20 * Math.log10(listenGain(bank, 100, 48000)) < -30);
  assert.ok(20 * Math.log10(listenGain(bank, 10000, 48000)) < -30);
  assert.equal(stuffTaps([1, 2], 4).length, 5);
  assert.equal(listenSuffix({kind: 'band', low: 100, high: 4000}), 'band-0.1-4kHz');
  assert.equal(listenSuffix(bank), 'bank-filter');

  const rate = 48000;
  const samples = new Int16Array(rate * 2);
  for (let n = 0; n < rate; n++) {
    const v = 0.3 * Math.sin(2 * Math.PI * 2000 * n / rate) + 0.3 * Math.sin(2 * Math.PI * 60 * n / rate);
    samples[2 * n] = samples[2 * n + 1] = Math.round(v * 32767);
  }
  const copy = readWav(listenWav(writeWav({channels: 2, rateHz: rate, samples}), bank));
  const tail = Array.from(copy.samples.filter((_, i) => i % 2 === 0).slice(rate / 2, rate), (v) => v / 32768);
  const power = (hz) => {
    let re = 0; let im = 0;
    tail.forEach((v, n) => { re += v * Math.cos(2 * Math.PI * hz * n / rate); im += v * Math.sin(2 * Math.PI * hz * n / rate); });
    return Math.hypot(re, im) / tail.length * 2;
  };
  assert.ok(Math.abs(power(2000) - 0.3) < 0.03);
  assert.ok(power(60) < 0.3 * 0.03);
});
