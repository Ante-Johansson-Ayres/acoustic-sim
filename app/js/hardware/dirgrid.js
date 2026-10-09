/** DG command for a grid in degrees; the firmware takes centidegrees. */
export function directionGridCommand({azimuthMin, azimuthMax, columns, elevationMin, elevationMax, rows}) {
  const cdeg = (deg) => Math.round(deg * 100);
  return `DG,${cdeg(azimuthMin)},${cdeg(azimuthMax)},${columns},${cdeg(elevationMin)},${cdeg(elevationMax)},${rows}`;
}

/** The angles the firmware reports for one axis (main.c grid_angle). */
export function directionGridAngles(minimum, maximum, count) {
  const lo = Math.round(minimum * 100);
  const hi = Math.round(maximum * 100);
  if (count < 2) return [(lo + hi) / 200];
  return Array.from({length: count}, (_, i) => (lo + (hi - lo) * i / (count - 1)) / 100);
}

const sameAngles = (actual, expected) => Array.isArray(actual) && actual.length === expected.length &&
  expected.every((angle, i) => Math.abs(actual[i] - angle) < 1e-3);

/** True once `frame` shows the requested direction set loaded, angles included. */
export function directionGridReady(frame, spec, beforeSequence) {
  const set = frame?.direction_set;
  const cfg = frame?.configuration;
  return Boolean(set) && Number(frame.sequence || 0) > beforeSequence && set.code === 0 &&
    cfg?.rows === spec.rows && cfg?.columns === spec.columns &&
    sameAngles(frame.azimuth_deg, directionGridAngles(spec.azimuthMin, spec.azimuthMax, spec.columns)) &&
    sameAngles(frame.elevation_deg, directionGridAngles(spec.elevationMin, spec.elevationMax, spec.rows));
}
