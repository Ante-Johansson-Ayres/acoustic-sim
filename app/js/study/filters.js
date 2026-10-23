/**
 * filters.js — FIR design for Study · Filters: windowed-sinc low-pass, high-pass and band-pass with the tap
 * count of the DSP block, the narrowest edge those taps can make, and 8.24 words. The band-pass is the
 * generator's design (beamforming_mathematics/beam_bank.py band_pass), so 0.5-3.5 kHz, Hamming, 21 taps at
 * 12 kHz gives the flashed bank taps. No DOM, so the node tests cover it.
 */

/** Transition width in units of rate / taps, and the first sidelobe level (dB), of each window. */
export const WINDOWS = {
  hamming: {label: 'Hamming', width: 3.3, sidelobe: -53},
  blackman: {label: 'Blackman', width: 5.5, sidelobe: -74},
  rectangular: {label: 'Rectangular', width: 0.9, sidelobe: -21},
};

export const TYPES = {lowpass: 'Low-pass', highpass: 'High-pass', bandpass: 'Band-pass'};

const sinc = (x) => (x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x));

function windowValues(kind, taps) {
  return Array.from({length: taps}, (_, k) => {
    const phase = taps > 1 ? 2 * Math.PI * k / (taps - 1) : 0;
    if (kind === 'blackman') return 0.42 - 0.5 * Math.cos(phase) + 0.08 * Math.cos(2 * phase);
    if (kind === 'rectangular') return 1;
    return 0.54 - 0.46 * Math.cos(phase);
  });
}

/** Linear gain of taps at one frequency. */
export function firGain(taps, frequencyHz, rateHz) {
  const w = 2 * Math.PI * frequencyHz / rateHz;
  let re = 0;
  let im = 0;
  taps.forEach((v, k) => { re += v * Math.cos(w * k); im -= v * Math.sin(w * k); });
  return Math.hypot(re, im);
}

export const firDb = (taps, frequencyHz, rateHz) => 20 * Math.log10(Math.max(firGain(taps, frequencyHz, rateHz), 1e-9));

/** Taps of a design {type, low, high, taps, rate, window}. Low-pass: unity at DC. High-pass: zero at DC,
 * unity at half the rate. Band-pass: zero at DC, unity at the band centre. */
export function design({type, low, high, taps, rate, window = 'hamming'}) {
  const w = windowValues(window, taps);
  const n = Array.from({length: taps}, (_, k) => k - (taps - 1) / 2);
  const lowPass = (corner) => n.map((v) => 2 * corner / rate * sinc(2 * corner / rate * v));
  let h;
  if (type === 'lowpass') {
    h = lowPass(high).map((v, k) => v * w[k]);
    const total = h.reduce((a, b) => a + b, 0);
    return h.map((v) => v / total);
  }
  if (type === 'highpass') h = lowPass(low).map((v, k) => (sinc(n[k]) - v) * w[k]);
  else h = n.map((v, k) => (lowPass(high)[k] - lowPass(low)[k]) * w[k]);
  const offset = h.reduce((a, b) => a + b, 0) / w.reduce((a, b) => a + b, 0);
  h = h.map((v, k) => v - offset * w[k]);
  const reference = type === 'highpass' ? rate / 2 : (low + high) / 2;
  const gain = firGain(h, reference, rate);
  return h.map((v) => v / gain);
}

/** Width of the edge between pass band and stop band that the taps can make (Hz). */
export function transitionHz(taps, rate, window = 'hamming') {
  return WINDOWS[window].width * rate / taps;
}

/** {errors, warnings} of a design: errors stop it, warnings tell what the taps cannot do. */
export function designProblems({type, low, high, taps, rate, window = 'hamming'}) {
  const errors = [];
  const warnings = [];
  const nyquist = rate / 2;
  const edge = transitionHz(taps, rate, window);
  const corners = type === 'lowpass' ? [high] : type === 'highpass' ? [low] : [low, high];
  if (corners.some((c) => !Number.isFinite(c) || c <= 0 || c >= nyquist)) {
    errors.push(`Corners must be between 0 and ${nyquist} Hz (half the ${rate / 1000} kHz rate).`);
  }
  if (type === 'bandpass' && !(low < high)) errors.push('The low corner must be below the high corner.');
  if (errors.length) return {errors, warnings};
  const width = Math.round(edge);
  if (type !== 'lowpass' && low < edge / 2) {
    warnings.push(`${taps} taps at ${rate / 1000} kHz make an edge about ${width} Hz wide: a high-pass corner at ${low} Hz does not stop the sound below it.`);
  }
  if (type !== 'highpass' && high > nyquist - edge / 2) {
    warnings.push(`The low-pass edge (about ${width} Hz wide) reaches half the rate (${nyquist} Hz).`);
  }
  if (type === 'bandpass' && high - low < edge) {
    warnings.push(`The band is narrower than one edge (about ${width} Hz): the pass band does not reach full gain.`);
  }
  if (type === 'lowpass' && high < edge / 2) {
    warnings.push(`The pass band is narrower than half an edge (about ${width} Hz): most of it is the edge.`);
  }
  return {errors, warnings};
}

/** 8.24 words (signed 32-bit) of taps, as the generator rounds them. */
export function toWords(taps) {
  return taps.map((v) => {
    const word = Math.round(v * 2 ** 24);
    if (word > 2 ** 31 - 1 || word < -(2 ** 31)) throw new Error('a tap does not fit in 8.24');
    return word;
  });
}

export const fromWords = (words) => words.map((w) => w / 2 ** 24);

/** A unit impulse in the middle tap: the filter passes everything (with its delay). */
export const bypassTaps = (taps) => Array.from({length: taps}, (_, k) => (k === (taps - 1) / 2 ? 1 : 0));

/** Time constant (ms) of a one-pole level detector from its coefficient, and back. */
export const tauMs = (alpha, rate) => (alpha > 0 && alpha < 1 ? -1000 / (rate * Math.log(1 - alpha)) : NaN);
export const alphaFor = (ms, rate) => 1 - Math.exp(-1 / (ms * rate / 1000));
