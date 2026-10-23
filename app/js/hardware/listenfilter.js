/**
 * listenfilter.js: a copy of a 16-bit PCM WAV for listening, through a band (the MCU window filter of
 * MAX78002/beam_power.c: fourth-order Butterworth high-pass and second-order Butterworth low-pass, as RBJ
 * biquads; 0.5-3.5 kHz by default) or through the bank's own filters (its anti-alias low-pass at 48 kHz, then
 * its band-pass, designed at 12 kHz and applied here with zeros between the taps).
 */

export const BAND_LOW_HZ = 500;
export const BAND_HIGH_HZ = 3500;
export const BANK_FACTOR = 4;
const HIGH_PASS_Q = [0.54119610, 1.30656296];
const BUTTERWORTH_Q = Math.SQRT1_2;

export function biquad(highPass, cornerHz, q, rateHz) {
  const w0 = 2 * Math.PI * cornerHz / rateHz;
  const c = Math.cos(w0);
  const alpha = Math.sin(w0) / (2 * q);
  const a0 = 1 + alpha;
  const edge = highPass ? 1 + c : 1 - c;
  return {b0: edge / 2 / a0, b1: (highPass ? -edge : edge) / a0, b2: edge / 2 / a0,
    a1: -2 * c / a0, a2: (1 - alpha) / a0};
}

export function bandSections(rateHz, lowHz = BAND_LOW_HZ, highHz = BAND_HIGH_HZ) {
  return [...HIGH_PASS_Q.map((q) => biquad(true, lowHz, q, rateHz)),
    biquad(false, highHz, BUTTERWORTH_Q, rateHz)];
}

/** Magnitude of the cascade at one frequency (linear). */
export function bandGain(frequencyHz, rateHz, lowHz = BAND_LOW_HZ, highHz = BAND_HIGH_HZ) {
  const w = 2 * Math.PI * frequencyHz / rateHz;
  let gain = 1;
  for (const s of bandSections(rateHz, lowHz, highHz)) {
    const num = [s.b0 + s.b1 * Math.cos(w) + s.b2 * Math.cos(2 * w), -(s.b1 * Math.sin(w) + s.b2 * Math.sin(2 * w))];
    const den = [1 + s.a1 * Math.cos(w) + s.a2 * Math.cos(2 * w), -(s.a1 * Math.sin(w) + s.a2 * Math.sin(2 * w))];
    gain *= Math.hypot(...num) / Math.hypot(...den);
  }
  return gain;
}

/** Filters one channel (Float64Array, full scale 1) in place. The first high-pass section starts settled on the
 * first sample, as in the firmware, so a DC offset does not ring. */
export function filterChannel(x, rateHz, lowHz = BAND_LOW_HZ, highHz = BAND_HIGH_HZ) {
  const sections = bandSections(rateHz, lowHz, highHz);
  sections.forEach((s, i) => {
    let z1 = i === 0 ? -s.b0 * x[0] : 0;
    let z2 = i === 0 ? s.b2 * x[0] : 0;
    for (let n = 0; n < x.length; n++) {
      const v = x[n];
      const y = s.b0 * v + z1;
      z1 = s.b1 * v - s.a1 * y + z2;
      z2 = s.b2 * v - s.a2 * y;
      x[n] = y;
    }
  });
  return x;
}

/** Format and samples of a 16-bit PCM WAV: {channels, rateHz, samples: Int16Array (interleaved)}. */
export function readWav(buffer) {
  const view = new DataView(buffer);
  const tag = (offset) => String.fromCharCode(...new Uint8Array(buffer, offset, 4));
  if (tag(0) !== 'RIFF' || tag(8) !== 'WAVE') throw new Error('not a WAV file');
  let format = null;
  for (let offset = 12; offset + 8 <= buffer.byteLength;) {
    const id = tag(offset);
    const size = view.getUint32(offset + 4, true);
    if (id === 'fmt ') {
      format = {code: view.getUint16(offset + 8, true), channels: view.getUint16(offset + 10, true),
        rateHz: view.getUint32(offset + 12, true), bits: view.getUint16(offset + 22, true)};
    } else if (id === 'data') {
      if (!format || format.code !== 1 || format.bits !== 16) throw new Error('only 16-bit PCM WAV files');
      const samples = new Int16Array(buffer.slice(offset + 8, offset + 8 + size - (size % 2)));
      return {channels: format.channels, rateHz: format.rateHz, samples};
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error('WAV file without data');
}

export function writeWav({channels, rateHz, samples}) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, rateHz, true);
  view.setUint32(28, rateHz * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  new Int16Array(buffer, 44).set(samples);
  return buffer;
}

/** Taps with factor - 1 zeros between them: the same filter at factor times the rate (with images). */
export function stuffTaps(taps, factor) {
  const out = new Float64Array((taps.length - 1) * factor + 1);
  taps.forEach((v, k) => { out[k * factor] = v; });
  return out;
}

/** FIR filter of one channel (zero initial state), in place. */
export function firChannel(x, taps) {
  const input = Float64Array.from(x);
  for (let n = 0; n < x.length; n++) {
    let y = 0;
    for (let k = 0; k < taps.length && k <= n; k++) if (taps[k]) y += taps[k] * input[n - k];
    x[n] = y;
  }
  return x;
}

export function firGain(taps, frequencyHz, rateHz) {
  const w = 2 * Math.PI * frequencyHz / rateHz;
  let re = 0;
  let im = 0;
  taps.forEach((v, k) => { re += v * Math.cos(w * k); im -= v * Math.sin(w * k); });
  return Math.hypot(re, im);
}

/** Linear gain of a listening filter: {kind: 'band', low, high} or {kind: 'bank', antiAlias, bandPass}. */
export function listenGain(filter, frequencyHz, rateHz) {
  if (filter.kind === 'bank') {
    return firGain(filter.antiAlias, frequencyHz, rateHz) * firGain(stuffTaps(filter.bandPass, BANK_FACTOR), frequencyHz, rateHz);
  }
  return bandGain(frequencyHz, rateHz, filter.low, filter.high);
}

function applyListenFilter(x, rateHz, filter) {
  if (filter.kind === 'bank') {
    if (rateHz !== 12000 * BANK_FACTOR) throw new Error(`the bank filter copy needs ${12000 * BANK_FACTOR} Hz audio`);
    return firChannel(firChannel(x, filter.antiAlias), stuffTaps(filter.bandPass, BANK_FACTOR));
  }
  return filterChannel(x, rateHz, filter.low, filter.high);
}

/** The file name part of a copy: band-0.5-3.5kHz, or bank-filter. */
export function listenSuffix(filter) {
  if (filter.kind === 'bank') return 'bank-filter';
  const khz = (hz) => String(Math.round(hz) / 1000);
  return `band-${khz(filter.low)}-${khz(filter.high)}kHz`;
}

/** A copy of a WAV file through a listening filter (ArrayBuffer in, ArrayBuffer out). */
export function listenWav(buffer, filter) {
  const wav = readWav(buffer);
  const frames = Math.floor(wav.samples.length / wav.channels);
  const out = new Int16Array(frames * wav.channels);
  for (let ch = 0; ch < wav.channels; ch++) {
    const x = new Float64Array(frames);
    for (let n = 0; n < frames; n++) x[n] = wav.samples[n * wav.channels + ch] / 32768;
    applyListenFilter(x, wav.rateHz, filter);
    for (let n = 0; n < frames; n++) {
      out[n * wav.channels + ch] = Math.max(-32768, Math.min(32767, Math.round(x[n] * 32768)));
    }
  }
  return writeWav({channels: wav.channels, rateHz: wav.rateHz, samples: out});
}

/** The detector-band copy of a WAV file (ArrayBuffer in, ArrayBuffer out). */
export function bandWav(buffer) {
  return listenWav(buffer, {kind: 'band', low: BAND_LOW_HZ, high: BAND_HIGH_HZ});
}
