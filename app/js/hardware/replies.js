/** The code of the CH1 reply to `O,<source>,<index>` sent after `beforeSequence`, else null. */
export function outputReplyCode(frame, source, index, beforeSequence) {
  const ch1 = frame?.ch1;
  if (Number(frame?.sequence || 0) <= beforeSequence || !ch1 || ch1.source !== source || ch1.index !== index) return null;
  return ch1.code;
}
