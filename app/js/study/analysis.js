/** Pure Study analysis: SNR, array gain, detector decay fit and peak interpolation. */

const power = (db) => 10 ** (db / 10);

/** SNR (dB) from mean levels with the source on and off, both in dBFS. */
export function snrDb(onDb, offDb) {
  const signal = power(onDb) - power(offDb);
  return signal > 0 ? 10 * Math.log10(signal / power(offDb)) : -Infinity;
}

/** Level (dBFS) of the source alone: power with it on minus power with it off. */
export function signalDb(onDb, offDb) {
  const signal = power(onDb) - power(offDb);
  return signal > 0 ? 10 * Math.log10(signal) : -Infinity;
}

/** Beam SNR minus single-microphone SNR; a fixed gain on either path cancels out. */
export function arrayGainDb({beamOn, beamOff, micOn, micOff}) {
  return snrDb(beamOn, beamOff) - snrDb(micOn, micOff);
}

/** Below this loud-to-quiet contrast a settle-sweep reading carries no usable decay. */
export const MIN_CONTRAST_DB = 6;

/** How much of the loud level a settle-sweep point still holds, on the raw state word. */
export function remainingFraction(point, quietDb = point.quiet_db, loudDb = point.loud_db) {
  if (!(loudDb - quietDb >= MIN_CONTRAST_DB)) return NaN;
  const raw = (db) => 10 ** (db / 20);
  const quiet = raw(quietDb);
  const span = raw(loudDb) - quiet;
  return span > 0 ? (raw(point.level_db) - quiet) / span : NaN;
}

/**
 * Detector time constant from a settle sweep: dwell on a loud beam, steer to a quiet one and read
 * once after settle_us. The one-pole decay acts on the raw state word, 10^(dB/20). Points may carry
 * their own loud_db and quiet_db, measured next to them, so a drifting source cancels out.
 */
export function fitDetectorDecay(points, quietDb, loudDb) {
  const fit = [];
  for (const point of points) {
    const fraction = remainingFraction(point, point.quiet_db ?? quietDb, point.loud_db ?? loudDb);
    if (fraction > 0.03 && fraction < 0.97) fit.push({x: point.settle_us / 1e6, y: Math.log(fraction)});
  }
  if (fit.length < 2) return null;
  const meanX = fit.reduce((sum, p) => sum + p.x, 0) / fit.length;
  const meanY = fit.reduce((sum, p) => sum + p.y, 0) / fit.length;
  const sxx = fit.reduce((sum, p) => sum + (p.x - meanX) ** 2, 0);
  const sxy = fit.reduce((sum, p) => sum + (p.x - meanX) * (p.y - meanY), 0);
  const syy = fit.reduce((sum, p) => sum + (p.y - meanY) ** 2, 0);
  if (!(sxx > 0) || sxy >= 0) return null;
  const slope = sxy / sxx;
  return {tau_ms: -1000 / slope, points_used: fit.length, r2: syy > 0 ? (sxy * sxy) / (sxx * syy) : 1};
}

/** Angle of the strongest level, refined by a parabola through it and its two neighbours. */
export function refinePeak(levels, angles) {
  let i = 0;
  for (let k = 1; k < levels.length; k++) if (levels[k] > levels[i]) i = k;
  if (i === 0 || i === levels.length - 1) return angles[i];
  const [x0, x1, x2] = [angles[i - 1], angles[i], angles[i + 1]];
  const [y0, y1, y2] = [levels[i - 1], levels[i], levels[i + 1]];
  const denominator = (x1 - x0) * (y1 - y2) - (x1 - x2) * (y1 - y0);
  if (denominator === 0) return x1;
  return x1 - 0.5 * ((x1 - x0) ** 2 * (y1 - y2) - (x1 - x2) ** 2 * (y1 - y0)) / denominator;
}

/** Refined peak of a grid of levels[row][column] (rows are elevations, columns azimuths). */
export function refinePeak2d(levels, azimuths, elevations) {
  let row = 0;
  let column = 0;
  levels.forEach((values, r) => values.forEach((value, c) => {
    if (value > levels[row][column]) { row = r; column = c; }
  }));
  return {
    azimuth: refinePeak(levels[row], azimuths),
    elevation: refinePeak(levels.map((values) => values[column]), elevations),
  };
}
