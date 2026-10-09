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

/** The firmware's reason if it refused the direction set sent after `beforeSequence`, else null. */
export function directionGridFailure(frame, beforeSequence) {
  const record = frame?.last_record;
  if (Number(frame?.sequence || 0) <= beforeSequence || record?.type !== 'error' || record.reason !== 'DG') return null;
  return (record.details || []).join(' ') || 'refused';
}

/** The grid a frame shows, kept so it can be put back after a temporary direction set. */
export function activeGrid(frame) {
  const cfg = frame?.configuration;
  if (!cfg || !Array.isArray(frame.azimuth_deg) || !Array.isArray(frame.elevation_deg)) return null;
  return {rows: cfg.rows, columns: cfg.columns, azimuth_deg: [...frame.azimuth_deg], elevation_deg: [...frame.elevation_deg]};
}

/** True when `frame` shows `grid` (from activeGrid), angles included. */
export function gridMatches(frame, grid) {
  const cfg = frame?.configuration;
  return cfg?.rows === grid.rows && cfg?.columns === grid.columns &&
    sameAngles(frame.azimuth_deg, grid.azimuth_deg) && sameAngles(frame.elevation_deg, grid.elevation_deg);
}

/** True once `frame` shows the requested direction set loaded, angles included. */
export function directionGridReady(frame, spec, beforeSequence) {
  const set = frame?.direction_set;
  const cfg = frame?.configuration;
  return Boolean(set) && Number(frame.sequence || 0) > beforeSequence && set.code === 0 &&
    cfg?.rows === spec.rows && cfg?.columns === spec.columns &&
    sameAngles(frame.azimuth_deg, directionGridAngles(spec.azimuthMin, spec.azimuthMax, spec.columns)) &&
    sameAngles(frame.elevation_deg, directionGridAngles(spec.elevationMin, spec.elevationMax, spec.rows));
}
