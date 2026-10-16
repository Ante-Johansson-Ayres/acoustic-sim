const text = (value) => (value !== null && typeof value === 'object' ? JSON.stringify(value) : value ?? '');

/** A CSV field, quoted when it holds a comma, a quote or a line break. */
const field = (value) => {
  const s = String(text(value));
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** Settings shared by every capture, and the keys whose values differ between captures; null if none carry settings. */
export function captureSettings(captures) {
  const all = captures.map((c) => c.settings).filter(Boolean);
  if (!all.length) return null;
  const keys = [...new Set(all.flatMap(Object.keys))];
  const varying = keys.filter((key) => all.some((settings) => settings[key] !== all[0][key]));
  return {common: Object.fromEntries(keys.filter((key) => !varying.includes(key)).map((key) => [key, all[0][key]])), varying};
}

/** One saved Study run as CSV: a commented metadata header, then one row per capture. */
export function runCsv(run) {
  const varying = run.meta?.varying_settings || [];
  const lines = [`# ${run.name}`, `# template: ${run.template}`, `# time: ${run.time}`,
    ...Object.entries(run.meta || {}).map(([k, v]) => `# ${k}: ${text(v)}`),
    ...(run.notes ? [`# notes: ${String(run.notes).replace(/\n/g, ' ')}`] : []),
    ...Object.entries(run.result || {}).map(([k, v]) => `# result.${k}: ${text(v)}`),
    ['label', 'value', 'n', 'mean_dbfs', 'std_db', 'min_dbfs', 'max_dbfs', ...varying].join(',')];
  for (const c of run.captures || []) {
    lines.push([c.label, c.value, c.n, c.mean_db?.toFixed(2), c.std_db?.toFixed(2), c.min_db?.toFixed(2), c.max_db?.toFixed(2),
      ...varying.map((key) => c.settings?.[key])].map(field).join(','));
  }
  return lines.join('\n') + '\n';
}
