/**
 * reclabel.js: the labels that Save WAV writes into the recording record (heimdall-recording-v2) and the
 * dataset index. The form keeps the text as typed; labelPayload() converts it for the backend.
 */

export const LABEL_KEY = 'heimdall.recordingLabel';
export const LABEL_CLASSES = [['', 'Not set'], ['drone', 'Drone'], ['not_drone', 'Not drone']];
export const LABEL_FIELDS = [
  {key: 'class', label: 'Class', kind: 'class'},
  {key: 'source', label: 'Source', placeholder: 'YouTube clip title, speech, fan'},
  {key: 'source_url', label: 'Source URL', wide: true},
  {key: 'licence', label: 'Licence'},
  {key: 'clip_offset_s', label: 'Clip offset (s)', kind: 'number'},
  {key: 'distance_m', label: 'Distance (m)', kind: 'number'},
  {key: 'azimuth_deg', label: 'Azimuth (°)', kind: 'number'},
  {key: 'elevation_deg', label: 'Elevation (°)', kind: 'number'},
  {key: 'level', label: 'Level', placeholder: 'dB(A) at the array, or volume'},
  {key: 'room', label: 'Room'},
  {key: 'session', label: 'Session'},
  {key: 'notes', label: 'Notes', kind: 'text', wide: true},
];

const EMPTY = Object.fromEntries(LABEL_FIELDS.map(({key}) => [key, '']));

/** A number typed with a decimal point or comma: null when empty, NaN when it is not a number. */
export function parseNumber(text) {
  const trimmed = String(text ?? '').trim();
  if (!trimmed) return null;
  const value = Number(trimmed.replace(',', '.'));
  return Number.isFinite(value) ? value : NaN;
}

export function loadLabel(storage) {
  try {
    const saved = JSON.parse(storage?.getItem(LABEL_KEY));
    if (!saved || typeof saved !== 'object') return {...EMPTY};
    const label = {...EMPTY};
    for (const key of Object.keys(EMPTY)) if (saved[key] !== undefined) label[key] = String(saved[key]);
    if (!LABEL_CLASSES.some(([value]) => value === label.class)) label.class = '';
    return label;
  } catch (_) {
    return {...EMPTY};
  }
}

/** {label, problems}: the label for the backend, with a field that is not a number left out (null). */
export function labelPayload(form) {
  const label = {};
  const problems = [];
  for (const {key, label: name, kind} of LABEL_FIELDS) {
    const text = String(form?.[key] ?? '').trim();
    if (kind === 'class') {
      label.class = text || null;
    } else if (kind === 'number') {
      const value = parseNumber(text);
      if (Number.isNaN(value)) problems.push(`${name} "${text}" is not a number`);
      label[key] = Number.isNaN(value) ? null : value;
    } else {
      label[key] = text;
    }
  }
  return {label, problems};
}

/** One line for the closed form: "Drone · YouTube clip · 1.5 m", or "not set". */
export function labelSummary(form) {
  const parts = [];
  const cls = LABEL_CLASSES.find(([value]) => value === form?.class);
  if (form?.class && cls) parts.push(cls[1]);
  if (String(form?.source ?? '').trim()) parts.push(String(form.source).trim());
  const distance = parseNumber(form?.distance_m);
  if (Number.isFinite(distance)) parts.push(`${distance} m`);
  return parts.length ? parts.join(' · ') : 'not set';
}
