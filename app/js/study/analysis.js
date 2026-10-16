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

/** Below this SNR a level is too close to its noise for a gain correction or an array gain. */
export const MIN_SNR_DB = 10;

/** Beam SNR minus single-microphone SNR; a fixed gain on either path cancels out. NaN if either is below its noise. */
export function arrayGainDb({beamOn, beamOff, micOn, micOff}) {
  const beam = snrDb(beamOn, beamOff);
  const microphone = snrDb(micOn, micOff);
  return Number.isFinite(beam) && Number.isFinite(microphone) ? beam - microphone : NaN;
}

/**
 * Microphone survey rows from captures keyed by microphone ({mean_db}). A correction needs the
 * noise pass and at least minSnrDb on both the microphone and the reference; otherwise it is null.
 */
export function surveyRows(on, off, reference, minSnrDb = MIN_SNR_DB) {
  const measure = (microphone) => {
    const sourceOn = on.get(microphone);
    const sourceOff = off.get(microphone);
    if (!sourceOn || !sourceOff) return {snr: null, level: null};
    const snr = snrDb(sourceOn.mean_db, sourceOff.mean_db);
    return {snr: Number.isFinite(snr) ? snr : null,
      level: snr >= minSnrDb ? signalDb(sourceOn.mean_db, sourceOff.mean_db) : null};
  };
  const ref = measure(reference);
  return [...on.keys()].sort((a, b) => a - b).map((microphone) => {
    const mic = measure(microphone);
    return {
      microphone,
      gain_db: ref.level !== null && mic.level !== null ? ref.level - mic.level : null,
      delay_us: null,
      polarity: null,
      level_dbfs: on.get(microphone).mean_db,
      noise_dbfs: off.get(microphone)?.mean_db ?? null,
      snr_db: mic.snr,
    };
  });
}

/** Reading dB the quietest scale-check capture needs above the floor, so noise moves it < 0.15 dB. */
export const SCALE_MARGIN_DB = 15;

/**
 * Detector scale from captures at known source levels ({value: source dB, mean_db}): the
 * least-squares slope of reading against source level. 1 means amplitude dB, 2 power counted twice.
 */
export function detectorScale(captures, floorDb = null) {
  const points = captures.filter((c) => Number.isFinite(c.value) && Number.isFinite(c.mean_db));
  if (new Set(points.map((p) => p.value)).size < 2) return null;
  const meanX = points.reduce((sum, p) => sum + p.value, 0) / points.length;
  const meanY = points.reduce((sum, p) => sum + p.mean_db, 0) / points.length;
  const sxx = points.reduce((sum, p) => sum + (p.value - meanX) ** 2, 0);
  const sxy = points.reduce((sum, p) => sum + (p.value - meanX) * (p.mean_db - meanY), 0);
  const ratio = sxy / sxx;
  const step = Math.max(...points.map((p) => p.value)) - Math.min(...points.map((p) => p.value));
  const quietest = Math.min(...points.map((p) => p.mean_db));
  return {ratio, step_db: step, seen_db: ratio * step,
    margin_db: Number.isFinite(floorDb) ? quietest - floorDb : null};
}

const powerMeanDb = (levels) => 10 * Math.log10(levels.reduce((sum, db) => sum + power(db), 0) / levels.length);

/**
 * Dynamic range from floor captures (value null) and captures at source levels (value in dB):
 * from the floor to the highest level whose step from the level below still moves the reading by
 * at least half the step. Captures at the same level are power-averaged. Null without a floor and
 * two levels; range_db is null if the reading never follows the source.
 */
export function dynamicRange(captures) {
  const floors = captures.filter((c) => c.value === null).map((c) => c.mean_db);
  const values = [...new Set(captures.filter((c) => Number.isFinite(c.value)).map((c) => c.value))].sort((a, b) => a - b);
  if (!floors.length || values.length < 2) return null;
  const levels = values.map((value) => ({value,
    mean_db: powerMeanDb(captures.filter((c) => c.value === value).map((c) => c.mean_db))}));
  const floor = powerMeanDb(floors);
  for (let i = levels.length - 1; i >= 1; i--) {
    if ((levels[i].mean_db - levels[i - 1].mean_db) / (levels[i].value - levels[i - 1].value) >= 0.5) {
      return {floor_db: floor, ceiling_db: levels[i].mean_db, range_db: levels[i].mean_db - floor};
    }
  }
  return {floor_db: floor, ceiling_db: null, range_db: null};
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
