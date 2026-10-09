const text = (value) => (value !== null && typeof value === 'object' ? JSON.stringify(value) : value ?? '');

/** One saved Study run as CSV: a commented metadata header, then one row per capture. */
export function runCsv(run) {
  const lines = [`# ${run.name}`, `# template: ${run.template}`, `# time: ${run.time}`,
    ...Object.entries(run.meta || {}).map(([k, v]) => `# ${k}: ${text(v)}`),
    ...(run.notes ? [`# notes: ${String(run.notes).replace(/\n/g, ' ')}`] : []),
    ...Object.entries(run.result || {}).map(([k, v]) => `# result.${k}: ${text(v)}`),
    'label,value,n,mean_dbfs,std_db,min_dbfs,max_dbfs'];
  for (const c of run.captures || []) lines.push([c.label, c.value ?? '', c.n, c.mean_db?.toFixed(2), c.std_db?.toFixed(2), c.min_db?.toFixed(2), c.max_db?.toFixed(2)].join(','));
  return lines.join('\n') + '\n';
}
