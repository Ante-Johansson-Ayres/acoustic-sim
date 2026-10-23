/**
 * runlog.js — the pure parts of Study · Measurements: option values and the command preview (as
 * study_runner.py builds them), log lines, answers to the scripts' questions, the plan's inline
 * markup and result labels. No DOM, so the node tests cover them.
 */

const TEXT_VALUE = /^[A-Za-z0-9 _.,;:+-]{0,200}$/;
const NUMBER_LIMIT = 1e7;

/** "burst_at" → "Burst at". */
export function optionLabel(name) {
  const text = String(name).replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The text an input shows for a value from the catalogue. */
export function inputText(option, value) {
  if (option.kind === 'flag') return Boolean(value);
  return value === null || value === undefined ? '' : String(value);
}

/** Form values of an entry, as input texts: the plan's values. */
export function optionDefaults(entry) {
  return Object.fromEntries(entry.options.map((option) => [option.name, inputText(option, option.default)]));
}

/** {value} or {error} for an input text; the same rules as study_runner.option_value. */
export function optionValue(option, raw) {
  if (option.kind === 'flag') return {value: raw === true};
  const text = raw === null || raw === undefined ? '' : String(raw).trim();
  if (!text) return option.required ? {error: 'Needs a value'} : {value: null};
  if (option.kind === 'int' || option.kind === 'float') {
    const number = /^[+-]?(\d+([.,]\d*)?|[.,]\d+)$/.test(text) ? Number(text.replace(',', '.')) : NaN;
    if (!Number.isFinite(number) || Math.abs(number) > NUMBER_LIMIT) return {error: 'Type a number'};
    if (option.kind === 'int' && !Number.isInteger(number)) return {error: 'Type a whole number'};
    return {value: number};
  }
  if (option.kind === 'choice') {
    return option.choices.includes(text) ? {value: text} : {error: `One of: ${option.choices.join(', ')}`};
  }
  return TEXT_VALUE.test(text) ? {value: text} : {error: 'Letters, digits and , ; . : + - only'};
}

/** {name: error} for the inputs that the backend would refuse. */
export function formErrors(entry, values) {
  const errors = {};
  for (const option of entry.options) {
    const {error} = optionValue(option, values[option.name]);
    if (error) errors[option.name] = error;
  }
  return errors;
}

/** {name: value} for POST /study/run. */
export function requestOptions(entry, values) {
  return Object.fromEntries(entry.options.map((option) => [option.name, optionValue(option, values[option.name]).value ?? null]));
}

/** The script's arguments as study_runner.arguments builds them: options at the script's default are left out. */
export function commandArgs(entry, values) {
  const args = entry.test ? [entry.test] : [];
  for (const option of entry.options) {
    const {value, error} = optionValue(option, values[option.name]);
    if (error) continue;
    if (option.kind === 'flag') {
      if (value) args.push(option.flag);
      continue;
    }
    if (value === null) continue;
    if (!option.required && value === optionValue(option, inputText(option, option.script_default)).value) continue;
    const text = String(value);
    if (text.startsWith('-')) args.push(`${option.flag}=${text}`);
    else args.push(option.flag, text);
  }
  return args;
}

export function commandLine(entry, values, folder) {
  const quote = (arg) => (/[\s;"]/.test(arg) ? `"${arg}"` : arg);
  return ['python', `scripts/${entry.script}`, ...commandArgs(entry, values), '--out', folder].map(quote).join(' ');
}

/** How a log line shows: pass, fail, info, warn, ask, answer, system or plain. */
export function lineKind(line) {
  if (line.kind !== 'out') return line.kind;
  const text = line.text;
  if (/^PASS\s/.test(text)) return 'pass';
  if (/^FAIL\s/.test(text)) return 'fail';
  if (/^INFO\s/.test(text)) return 'info';
  if (/^(warning\b|Traceback\b|\w+Error\b)/i.test(text) || /^\s*(RuntimeError|ValueError|KeyboardInterrupt)\b/.test(text)) return 'warn';
  return 'plain';
}

/** Numbers as the scripts read them (characterise.parse_numbers): spaces, commas or semicolons between,
 * a decimal comma works ("72,5", "152,5 200 10"). */
export function parseNumbers(text, count) {
  const trimmed = String(text).trim();
  const parts = count === 1 ? [trimmed] : trimmed.includes(' ') ? trimmed.split(/\s+/) : trimmed.replace(/;/g, ',').split(',');
  const values = parts.map((part) => part.replace(/^[,;]+|[,;]+$/g, '')).filter(Boolean).map((part) => Number(part.replace(',', '.')));
  if (values.length !== count || !values.every(Number.isFinite)) return null;
  return values;
}

/** Why an answer would be asked again, or null. */
export function answerError(prompt, text) {
  if (!prompt || !prompt.numbers) return null;
  if (!String(text).trim()) return prompt.optional ? null : `Type ${prompt.numbers === 1 ? 'a number' : `${prompt.numbers} numbers`}`;
  return parseNumbers(text, prompt.numbers) ? null
    : `Type ${prompt.numbers === 1 ? 'one number' : `${prompt.numbers} numbers, with spaces between them`}`;
}

/** Plan text in parts: **bold** (which can hold `code`), `code` and [links](...) as their text. */
export function inlineParts(text) {
  const parts = [];
  const pattern = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\([^)]*\)/g;
  let last = 0;
  for (const match of String(text).matchAll(pattern)) {
    if (match.index > last) parts.push({kind: 'text', text: text.slice(last, match.index)});
    if (match[1] !== undefined) parts.push({kind: 'bold', parts: inlineParts(match[1])});
    else if (match[2] !== undefined) parts.push({kind: 'code', text: match[2]});
    else parts.push({kind: 'text', text: match[3]});
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({kind: 'text', text: text.slice(last)});
  return parts;
}

export const STATE_TEXT = {
  idle: 'No test running', running: 'Running', stopping: 'Stopping: the test runs its restore steps',
  finishing: 'Finishing: putting back the Study edits', done: 'Finished', stopped: 'Stopped',
  error: 'Ended without results',
};

export function isActive(state) {
  return ['running', 'stopping', 'finishing'].includes(state);
}

export function countsText(counts) {
  if (!counts) return '';
  const parts = [];
  if (counts.pass) parts.push(`${counts.pass} pass`);
  if (counts.fail) parts.push(`${counts.fail} fail`);
  if (counts.info) parts.push(`${counts.info} info`);
  return parts.join(' · ');
}

export function durationText(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  return `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, '0')} s`;
}

/** The newest result of an entry: its run record, or its folder name (health, health_2, ...). */
export function latestResult(results, entryId) {
  return results.find((result) => result.entry === entryId
    || (!result.entry && (result.folder === entryId || result.folder.startsWith(`${entryId}_`)))) || null;
}
