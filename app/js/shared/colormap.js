/**
 * colormap.js — perceptual colour ramps shared by the map, the 3D tiles and
 * the beam-pattern views. Inputs are normalised 0..1.
 */

import { look } from './look.js';

export const RAMPS = {
  inferno: [[0, '#1b0f36'], [0.25, '#57106e'], [0.5, '#bc3754'], [0.75, '#f98c0a'], [1, '#fcffa4']],
  cividis: [[0, '#0b2350'], [0.25, '#3b496c'], [0.5, '#707173'], [0.75, '#aea06f'], [1, '#fde737']],
  blue: [[0, '#18202c'], [0.5, '#2f65b0'], [1, '#d6e7ff']],
  // Exhibition (style D): dark to ember to warm white.
  ember: [[0, '#16171b'], [0.35, '#4a1f1a'], [0.6, '#b8351f'], [0.82, '#ff7a45'], [1, '#ffe3c2']],
  // Panel look, as its mockup: six flat greys from light to ink, and orange for the top tenth
  // (within 3 dB on the beam pattern's 30 dB scale).
  panel: [
    [0, '#dcdcda'], [1 / 6, '#dcdcda'], [1 / 6, '#b5b5b2'], [2 / 6, '#b5b5b2'], [2 / 6, '#8c8c8a'], [3 / 6, '#8c8c8a'],
    [3 / 6, '#636362'], [4 / 6, '#636362'], [4 / 6, '#3a3a39'], [5 / 6, '#3a3a39'], [5 / 6, '#111111'], [0.9, '#111111'],
    [0.9, '#ea5a00'], [1, '#ea5a00'],
  ],
  // Arasaka look: near black, through dark red, to red and a pale red top.
  arasaka: [[0, '#120b0c'], [0.35, '#3a0a10'], [0.65, '#9e0e1d'], [0.9, '#f2122a'], [1, '#ffc2ca']],
};

// A hidden look (look.js) draws with its own ramp wherever the app uses its two.
const LOOK_RAMPS = {
  panel: {inferno: 'panel', ember: 'panel'},
  arasaka: {inferno: 'arasaka', ember: 'arasaka'},
};

function resolve(name) {
  return (look && LOOK_RAMPS[look][name]) || name;
}

const parsed = {};

function stopsOf(name) {
  if (!parsed[name]) {
    parsed[name] = (RAMPS[name] || RAMPS.inferno).map(([t, hex]) => [t, [
      parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16),
    ]]);
  }
  return parsed[name];
}

/** Colour at t in [0, 1] as [r, g, b] (0..255). */
export function rampColor(t, name = 'inferno') {
  const stops = stopsOf(resolve(name));
  const x = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [t0, a] = stops[i - 1];
      const [t1, b] = stops[i];
      const f = (x - t0) / (t1 - t0);
      return a.map((v, k) => Math.round(v + (b[k] - v) * f));
    }
  }
  return stops[stops.length - 1][1].slice();
}

export function rgbCss(rgb) {
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}

export function luminance(rgb) {
  const lin = rgb.map((v) => {
    const u = v / 255;
    return u <= 0.03928 ? u / 12.92 : Math.pow((u + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

/** Readable text colour on top of a ramp colour. */
export function inkOn(rgb) {
  return luminance(rgb) > 0.3 ? '#141414' : '#f4f4f4';
}

export function rampGradient(direction = 'to top', name = 'inferno') {
  return `linear-gradient(${direction}, ${(RAMPS[resolve(name)] || RAMPS.inferno)
    .map(([t, hex]) => `${hex} ${Math.round(t * 100)}%`).join(', ')})`;
}

/** Diagonal hatch used to mark stale measurements. */
export const HATCH = 'repeating-linear-gradient(135deg, rgba(0,0,0,0.32) 0 2px, rgba(0,0,0,0) 2px 7px)';

/** Normalise a dBFS level against a floor (e.g. −40) to 0..1. */
export function levelToUnit(level, floorDb) {
  if (!Number.isFinite(level)) return null;
  const floor = Number.isFinite(floorDb) ? floorDb : -40;
  return Math.max(0, Math.min(1, (level - floor) / Math.max(1, -floor)));
}
