/** The code of the CH1 reply to `O,<source>,<index>` sent after `beforeSequence`, else null. */
export function outputReplyCode(frame, source, index, beforeSequence) {
  const ch1 = frame?.ch1;
  if (Number(frame?.sequence || 0) <= beforeSequence || !ch1 || ch1.source !== source || ch1.index !== index) return null;
  return ch1.code;
}

/** Level (dBFS) of the newest measurement if it is newer than `beforeCount` and of `sector`, else null. */
export function newMeasurementDb(frame, sector, beforeCount) {
  const measurement = frame?.last_measurement;
  const columns = frame?.configuration?.columns;
  if (!measurement || !columns || measurement.count <= beforeCount) return null;
  return measurement.row * columns + measurement.column === sector ? measurement.level_db : null;
}
