/**
 * measure.js — Study · Measurements: guided procedures that hold a beam,
 * poll its level with M, and turn the captures into a figure (SNR, main-lobe
 * width, dynamic range, detector scale, range estimate). Runs are kept in this
 * browser and export as CSV with a metadata header.
 *
 * Captures use only commands that exist today (X, S,<n>, M). Procedures that
 * need parameter writes on the device are listed but marked as such.
 */

import { h, s, setText } from '../shared/dom.js';
import { clockTime, deg, fileStamp, num } from '../shared/format.js';
import { download } from '../shared/dom.js';
import { apiJson } from '../shared/api.js';
import { dsp } from './dsp.js';
import { captureSettings, runCsv } from './runcsv.js';
import { MIN_SNR_DB, SCALE_MARGIN_DB, arrayGainDb, detectorScale, dynamicRange, fitDetectorDecay, refinePeak,
  refinePeak2d, remainingFraction, snrDb, surveyRows } from './analysis.js';
import { newMeasurementDb } from '../hardware/replies.js';

const RUNS_KEY = 'heimdall.study.runs';
const put = (el, ...kids) => el.replaceChildren(...kids.flat().filter(Boolean));

// MCU windows on the Ch0 stream, band-passed when band = 1. The DSP detector has no high-pass, so
// low-frequency content, which the beam adds in phase from every microphone, sets its floor; only the
// procedures that study it use it.
const MCU_BEAM = {measure: 1, channel: 0};
const DSP_DETECTOR = {measure: 0};

const TEMPLATES = [
  {key: 'microphones', name: 'Microphone survey', ready: true,
    text: `Route each microphone to Ch1 under the same broadside source and compare it with a reference, with MCU windows. The pass with the source off gives each microphone's noise; a microphone gets a correction only when it and the reference are at least ${MIN_SNR_DB} dB above their noise. Needs a program with an output selector.`},
  {key: 'beamcut', name: 'Beam-pattern cut', ready: true,
    text: 'Load a fine direction set across a fixed speaker, measure every direction, and overlay the deployed-array theory at the chosen frequency.'},
  {key: 'detectorstep', name: 'Detector time constant', ready: true,
    text: 'Dwell on a loud direction, then steer to a quiet one and read once after a settle time set in the firmware. Sweeping the settle time traces the shared detector\'s decay with firmware timing.'},
  {key: 'steering', name: 'Steering accuracy', ready: true,
    text: 'Search a fine grid around a marked speaker angle and report the measured peak and angular error.'},
  {key: 'arraygain', name: 'Array gain', ready: true,
    text: `Beam SNR minus single-microphone SNR, each from MCU-window captures with the source on and off. Fixed gains on either path cancel. The microphone must be at least ${MIN_SNR_DB} dB above its noise. Needs a program with an output selector.`},
  {key: 'snr', name: 'Signal-to-noise ratio', ready: true,
    text: 'Hold one beam on the source and measure it with MCU windows. Capture the level with the source on, then off. SNR is the on/off power ratio above the floor.'},
  {key: 'width', name: 'Main-lobe width, move the source', ready: true,
    text: 'Hold one beam, measured with MCU windows, and move the speaker through a list of angles. Gives the measured −3 dB and −6 dB widths and the pointing error of a fixed beam.'},
  {key: 'scale', name: 'Detector scale check', ready: true,
    text: `Checks the DSP detector, which has no high-pass: low-frequency content, which the beam adds in phase from every microphone, sets its floor. Capture the floor with the source off, then the source at levels a known step apart (for example 0 and +12 dB), the quietest at least ${SCALE_MARGIN_DB} dB above the floor. A reading that rises by the step means amplitude dB; twice the step means power is counted twice.`},
  {key: 'dynamic', name: 'Dynamic range', ready: true,
    text: 'Measured with MCU windows. Capture the floor with the source off, then step the source level up. The range runs from the floor to where the reading stops following the source.'},
  {key: 'range', name: 'Detection range, estimate', ready: true,
    text: 'Extrapolates an SNR measured at a known distance with spherical spreading and air absorption to the distance where SNR falls to your detection threshold.'},
];

function powerMean(levels) {
  const p = levels.map((l) => 10 ** (l / 10));
  return 10 * Math.log10(p.reduce((a, b) => a + b, 0) / p.length);
}

function stdDb(levels) {
  const m = levels.reduce((a, b) => a + b, 0) / levels.length;
  return Math.sqrt(levels.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, levels.length - 1));
}

function loadRuns() {
  try { return JSON.parse(localStorage.getItem(RUNS_KEY)) || []; } catch (_) { return []; }
}

function saveRuns(runs) {
  try { localStorage.setItem(RUNS_KEY, JSON.stringify(runs.slice(0, 100))); } catch (_) { /* storage unavailable */ }
}

function requireLink(session) {
  if (!session.ready) throw new Error('Connect to the device or an emulator first (Hardware screen).');
  if (session.frozen) throw new Error('The display is frozen, so no readings arrive. Unfreeze it on the Hardware screen.');
}

/** Hold `sector`, discard five detector time constants, then collect for `seconds`. */
async function capture(session, sector, seconds, rate, onProgress) {
  requireLink(session);
  if (!session.validSector(sector)) throw new Error('Sector out of range.');
  if (session.firmwareMode !== 'IDLE') {
    session.command('X');
    const start = performance.now();
    while (session.firmwareMode !== 'IDLE' && performance.now() - start < 3000) await new Promise((r) => setTimeout(r, 50));
  }
  session.setMonitorRate(rate);
  const levels = [];
  let discarded = 0;
  let lastCount = Number(session.latest?.last_measurement?.count || 0);
  const tauMs = Number(session.latest?.level_time_constant_ms || 8.665);
  const acceptAt = performance.now() + 5 * tauMs;
  const onFrame = () => {
    const level = newMeasurementDb(session.frame, sector, lastCount);
    lastCount = Math.max(lastCount, Number(session.frame?.last_measurement?.count || 0));
    if (!Number.isFinite(level)) return;
    if (performance.now() < acceptAt) discarded += 1;
    else levels.push(level);
  };
  session.addEventListener('frame', onFrame);
  session.startMonitor(sector);
  await new Promise((resolve) => {
    const tick = () => {
      const elapsed = Math.max(0, performance.now() - acceptAt) / 1000;
      onProgress?.(Math.min(1, elapsed / seconds), levels.length);
      if (elapsed >= seconds) resolve(); else setTimeout(tick, 100);
    };
    tick();
  });
  session.removeEventListener('frame', onFrame);
  session.stopMonitor();
  if (!levels.length) throw new Error('No level readings arrived. Check that the device answers M.');
  return {n: levels.length, discarded, settle_tau_ms: tauMs, mean_db: powerMean(levels),
    std_db: stdDb(levels), min_db: Math.min(...levels), max_db: Math.max(...levels),
    sector, settings: {...(session.latest?.settings || {})}};
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function ensureIdle(session) {
  if (session.firmwareMode === 'IDLE') return;
  session.command('X');
  const deadline = performance.now() + 3000;
  while (session.firmwareMode !== 'IDLE' && performance.now() < deadline) await sleep(25);
}

/** Send S,<sector> or M and return the one level the firmware reads (it settles after a steer). */
async function readOnce(session, command, sector) {
  requireLink(session);
  const before = Number(session.latest?.last_measurement?.count || 0);
  if (!session.command(command)) throw new Error('No active hardware link.');
  const deadline = performance.now() + 2000;
  while (performance.now() < deadline) {
    const level = newMeasurementDb(session.latest, sector, before);
    if (Number.isFinite(level)) return level;
    await sleep(5);
  }
  throw new Error(`No level reading arrived for ${command}.`);
}

/** Stop any scan (the firmware takes settings only while idle), apply settings, put the user's values back afterwards. */
async function withSettings(session, changes, action) {
  requireLink(session);
  await ensureIdle(session);
  const before = session.latest?.settings || {};
  const restore = Object.fromEntries(Object.keys(changes)
    .filter((key) => Number.isFinite(before[key])).map((key) => [key, before[key]]));
  try {
    await session.setMeasurementSettings(changes);
    return await action({...before, ...changes});
  } finally {
    await session.setMeasurementSettings(restore).catch(() => {});
  }
}

/** Run an action that reroutes Ch1, then put back the routing it found (the firmware boots on the steered beam). */
async function withCh1(session, action) {
  const ch1 = session.latest?.ch1;
  const previous = ch1 && ch1.code === 0 ? ch1 : {source: 0, index: 0};
  try {
    return await action();
  } finally {
    await session.selectOutputChecked(previous.source, previous.index).catch(() => {});
  }
}

/** Load a direction set for one procedure, then put the deployment grid back so sector numbers mean what they did. */
async function withDirectionGrid(session, spec, action) {
  await session.setDirectionGrid(spec);
  let result;
  try {
    result = await action([...session.latest.azimuth_deg], [...session.latest.elevation_deg]);
  } catch (error) {
    await session.restoreGrid().catch(() => {});
    throw error;
  }
  await session.restoreGrid();
  return result;
}

const snrText = (db) => Number.isFinite(db) ? `${num(db, 1)} dB` : 'below the noise';

/** Mean of detector readings on the raw state word, back in dB. */
function rawMeanDb(levels) {
  return 20 * Math.log10(levels.reduce((sum, db) => sum + 10 ** (db / 20), 0) / levels.length);
}

function widthAt(points, drop) {
  // points: [{angle, level}] sorted by angle. Interpolated width where the level is `drop` below the peak.
  if (points.length < 3) return null;
  const peak = points.reduce((a, b) => (b.level > a.level ? b : a));
  const i0 = points.indexOf(peak);
  const edge = (dir) => {
    for (let i = i0; i + dir >= 0 && i + dir < points.length; i += dir) {
      const a = points[i]; const b = points[i + dir];
      if (b.level <= peak.level - drop) return a.angle + (b.angle - a.angle) * (a.level - (peak.level - drop)) / (a.level - b.level);
    }
    return null;
  };
  const left = edge(-1); const right = edge(1);
  return left === null || right === null ? null : {width: Math.abs(right - left), left, right, peak: peak.angle};
}

function rangeFor(snr0, d0, threshold, alphaDbKm) {
  const snrAt = (d) => snr0 - 20 * Math.log10(d / d0) - alphaDbKm * (d - d0) / 1000;
  if (snrAt(d0) < threshold) return {range: null, snrAt};
  let lo = d0; let hi = d0;
  while (snrAt(hi) >= threshold && hi < 1e5) hi *= 2;
  for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (snrAt(mid) >= threshold) lo = mid; else hi = mid; }
  return {range: lo, snrAt};
}

export function measurementsScreen({session}) {
  const state = {template: 'snr', runs: loadRuns(), busy: false, draft: null};

  const list = h('ul', {class: 'templates'});
  const left = h('aside', {class: 'st-browser', 'aria-labelledby': 'ms-templates-h'},
    h('h2', {class: 'section__title', id: 'ms-templates-h'}, 'Measurements'), list,
    h('p', {class: 'note'}, 'Levels are dBFS from the level detector until you calibrate with a sound level meter at the array. Record the reference SPL in the notes.'));

  const body = h('div', {class: 'st-panel'});
  const centre = h('section', {class: 'st-editor', 'aria-label': 'Procedure'}, body);

  const runList = h('ul', {class: 'runs'});
  const right = h('aside', {class: 'st-changes', 'aria-label': 'Runs'},
    h('section', {class: 'section'},
      h('div', {class: 'section__row'}, h('h2', {class: 'section__title'}, 'Saved runs'),
        h('button', {type: 'button', class: 'btn btn--sm btn--quiet', onClick: () => exportAll()}, 'Export all')),
      runList,
      h('p', {class: 'note'}, 'Kept in this browser. Each export carries the device, the register-map hash, the firmware settings and the time.')));

  const el = h('div', {class: 'st-params'}, left, centre, right);

  function meta() {
    return {
      device: session.open ? session.transportLabel : 'not connected',
      grid: session.configuration ? `${session.configuration.rows}x${session.configuration.columns}` : '',
      map_hash: session.latest?.map_hash || dsp.registry?.map_hash || '',
      modified_words: dsp.status?.modified_words ?? '',
      detector_tau_ms: session.latest?.level_time_constant_ms ?? '',
      settings: {...(session.latest?.settings || dsp.status?.settings || {})},
    };
  }

  function saveRun(template, name, captures, result, notes) {
    const copies = captures.map((c) => ({...c}));
    const info = meta();
    const used = captureSettings(copies);
    if (used) {
      info.settings = used.common;
      if (used.varying.length) info.varying_settings = used.varying;
    }
    state.runs.unshift({id: Date.now(), template, name, time: new Date().toISOString(), meta: info, captures: copies, result, notes});
    saveRuns(state.runs);
    renderRuns();
  }

  function exportRun(run) {
    download(new Blob([runCsv(run)], {type: 'text/csv'}), `heimdall-${run.template}-${fileStamp(new Date(run.time))}.csv`);
  }

  function exportRunJson(run) {
    download(new Blob([JSON.stringify(run, null, 2) + '\n'], {type: 'application/json'}),
      `heimdall-${run.template}-${fileStamp(new Date(run.time))}.json`);
  }

  function exportAll() {
    if (!state.runs.length) return;
    download(new Blob([state.runs.map(runCsv).join('\n')], {type: 'text/csv'}), `heimdall-runs-${fileStamp()}.csv`);
  }

  function renderRuns() {
    runList.replaceChildren(...(state.runs.length ? state.runs.map((run) => h('li', {class: 'runs__item'},
      h('span', {class: 'runs__title'}, run.name),
      h('span', {class: 'note'}, `${new Date(run.time).toLocaleString()} · ${run.meta?.device || ''}`),
      h('span', {class: 'mono'}, run.result?.summary || ''),
      h('div', {class: 'toolbar toolbar--tight'},
        h('button', {type: 'button', class: 'btn btn--sm', onClick: () => exportRun(run)}, 'Export CSV'),
        h('button', {type: 'button', class: 'btn btn--sm', onClick: () => exportRunJson(run)}, 'Export JSON'),
        h('button', {type: 'button', class: 'btn btn--sm btn--quiet', 'aria-label': `Delete ${run.name}`, onClick: () => {
          state.runs = state.runs.filter((r) => r !== run); saveRuns(state.runs); renderRuns();
        }}, '×')),
    )) : [h('li', {class: 'note'}, 'No runs yet.')]));
  }

  function renderTemplates() {
    list.replaceChildren(...TEMPLATES.map((t) => h('li', {},
      h('button', {type: 'button', class: `template ${state.template === t.key ? 'is-open' : ''}`, onClick: () => { if (!state.busy) { state.template = t.key; state.draft = null; renderBody(); renderTemplates(); } }},
        h('span', {class: 'template__name'}, t.name),
        h('span', {class: `chip ${t.ready ? 'chip--plain' : 'chip--warn'}`}, t.ready ? 'Works now' : 'Needs protocol v3')))));
  }

  // Shared inputs
  const numberInput = (label, value, attrs = {}) => {
    const input = h('input', {class: 'input input--mono', type: 'number', value, style: {width: attrs.width || '110px'}, step: attrs.step || 'any'});
    return {input, el: h('div', {class: 'field'}, h('label', {}, label), input), get value() { return Number(input.value); }};
  };
  const notesInput = () => h('textarea', {class: 'input textarea', rows: 2, placeholder: 'Source, distance, level, room, reference SPL…'});

  function captureControls({sectorDefault}) {
    const sector = numberInput('Sector', sectorDefault, {width: '90px', step: 1});
    const seconds = numberInput('Capture (s)', 5, {width: '90px'});
    const rate = h('select', {class: 'select', 'aria-label': 'Read rate'}, h('option', {value: 10}, '10 Hz'), h('option', {value: 20, selected: true}, '20 Hz'), h('option', {value: 50}, '50 Hz'));
    return {sector, seconds, rate, el: h('div', {class: 'form-row'}, sector.el, seconds.el, h('div', {class: 'field'}, h('label', {}, 'Read rate'), rate))};
  }

  function defaultSector() {
    const cfg = session.configuration;
    const answer = session.solution();
    if (answer) return answer.sector;
    return cfg ? Math.floor(cfg.rows / 2) * cfg.columns + Math.floor(cfg.columns / 2) : 0;
  }

  function header(t) {
    return h('div', {class: 'st-panel__head'}, h('h2', {class: 'page-title'}, t.name), h('p', {class: 'lede'}, t.text));
  }

  function renderBody() {
    const t = TEMPLATES.find((x) => x.key === state.template);
    const status = h('p', {class: 'note', role: 'status'});
    const progress = h('progress', {max: 1, value: 0, hidden: true});
    const results = h('div', {class: 'ms-results'});
    const connected = session.ready;
    const needConnection = connected ? null : h('p', {class: 'note is-warn'}, 'Connect to the device or an emulator on the Hardware screen first.');

    const doCapture = async (controls, label, value, settings) => {
      state.busy = true;
      progress.hidden = false;
      setText(status, `Capturing ${label}…`);
      try {
        const c = await withSettings(session, settings, () => capture(session, controls.sector.value, controls.seconds.value,
          Number(controls.rate.value), (f, n) => { progress.value = f; setText(status, `Capturing ${label}… ${n} readings`); }));
        setText(status, `${label}: ${num(c.mean_db, 1)} dBFS ± ${num(c.std_db, 1)} dB from ${c.n} readings.`);
        return {label, value, ...c};
      } catch (error) {
        setText(status, error.message);
        return null;
      } finally {
        state.busy = false;
        progress.hidden = true;
      }
    };

    if (!t.ready) {
      put(body, header(t), h('p', {class: 'note is-warn'}, 'Not available yet: this procedure needs commands the firmware does not have. See MAX78002/docs/REFERENCE.md, section 4.5.'));
      return;
    }

    const selectorWarning = connected && !session.hasSelectors
      ? h('p', {class: 'note is-warn'}, 'This DSP program has no output selector, so Ch1 cannot carry one microphone. Use the Characterisation or Bank program.')
      : null;
    const parseMicList = (text) => {
      const values = [];
      for (const item of text.split(',')) {
        const match = item.trim().match(/^(\d+)(?:-(\d+))?$/);
        if (!match) continue;
        const first = Number(match[1]); const last = Number(match[2] ?? match[1]);
        for (let mic = first; mic <= last && mic < 44; mic++) if (!values.includes(mic)) values.push(mic);
      }
      return values;
    };

    if (t.key === 'microphones') {
      const controls = captureControls({sectorDefault: defaultSector()});
      controls.seconds.input.value = 1;
      const reference = numberInput('Reference microphone', 0, {width: '110px', step: 1});
      const microphones = h('input', {class: 'input input--mono', value: '0-43', style: {width: '180px'}});
      const notes = notesInput();
      const draft = state.draft ||= {on: new Map(), off: new Map()};
      const snrNote = (row) => row.noise_dbfs === null ? 'no noise pass' : row.snr_db === null ? 'below its noise' : `SNR ${num(row.snr_db, 1)} dB`;
      const show = () => {
        const rows = surveyRows(draft.on, draft.off, reference.value);
        const ref = rows.find((row) => row.microphone === reference.value);
        const corrected = rows.filter((row) => row.gain_db !== null).length;
        const referenceUsable = Boolean(ref && ref.snr_db !== null && ref.snr_db >= MIN_SNR_DB);
        const advice = !rows.length ? null
          : !draft.off.size ? `Run the noise pass with the source off: a correction needs each microphone at least ${MIN_SNR_DB} dB above its noise.`
            : !ref ? 'Survey the reference microphone as well.'
              : !referenceUsable ? `The reference microphone's SNR is ${ref.snr_db === null ? 'below its noise' : `${num(ref.snr_db, 1)} dB`}; it needs ${MIN_SNR_DB} dB. Raise the source or bring it closer, then run both passes again.`
                : `${corrected} of ${rows.length} microphones are at least ${MIN_SNR_DB} dB above their noise and have a correction. Delay and polarity are not measured yet.`;
        put(results,
          ...rows.map((row) => h('div', {class: 'ms-row'}, h('span', {}, `Mic ${row.microphone}`),
            h('span', {class: 'mono'}, `${row.gain_db === null ? 'no correction' : `correction ${num(row.gain_db, 2)} dB`} · ${snrNote(row)}`))),
          advice ? h('p', {class: referenceUsable ? 'note' : 'note is-warn'}, advice) : null,
          rows.length ? h('div', {class: 'toolbar'},
            h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('microphones',
              `Microphone survey, reference ${reference.value}`, [...draft.on.values(), ...draft.off.values()],
              {reference_microphone: reference.value, minimum_snr_db: MIN_SNR_DB, corrected_microphones: corrected, microphones: rows,
                summary: `${corrected} of ${rows.length} corrected, ref ${reference.value}`}, notes.value)}, 'Save run'),
            referenceUsable && corrected ? h('button', {type: 'button', class: 'btn', onClick: () => download(new Blob([
              JSON.stringify({schema: 'heimdall-microphone-calibration-v1',
                generated_at: new Date().toISOString(), reference_microphone: reference.value,
                microphones: rows.map(({microphone, gain_db, delay_us, polarity, noise_dbfs}) =>
                  ({microphone, gain_db, delay_us, polarity, noise_dbfs}))}, null, 2) + '\n'],
            {type: 'application/json'}), `heimdall-microphone-calibration-${fileStamp()}.json`)}, 'Export calibration JSON') : null) : null);
      };
      const pass = async (key) => {
        const mics = parseMicList(microphones.value);
        if (!mics.length || !mics.includes(reference.value)) { setText(status, 'Include the reference microphone in the survey list.'); return; }
        state.busy = true; progress.hidden = false;
        try {
          await withSettings(session, {measure: 1, channel: 1}, () => withCh1(session, async () => {
            for (let index = 0; index < mics.length; index++) {
              const mic = mics[index];
              await session.selectOutputChecked(1, mic);
              setText(status, `Microphone ${mic} (${index + 1}/${mics.length}), source ${key}…`);
              const measured = await capture(session, controls.sector.value, controls.seconds.value,
                Number(controls.rate.value), (fraction) => { progress.value = (index + fraction) / mics.length; });
              draft[key].set(mic, {label: `microphone ${mic}, source ${key}`, value: mic, ...measured});
            }
          }));
          setText(status, `Measured ${mics.length} microphones with the source ${key}.`);
        } catch (error) { setText(status, error.message); }
        finally { state.busy = false; progress.hidden = true; show(); }
      };
      const usable = connected && session.hasSelectors;
      put(body, header(t), needConnection, selectorWarning, controls.el,
        h('div', {class: 'form-row'}, reference.el,
          h('div', {class: 'field'}, h('label', {}, 'Microphones (list or range)'), microphones)),
        h('div', {class: 'toolbar'},
          h('button', {type: 'button', class: 'btn btn--primary', disabled: !usable, onClick: () => pass('on')}, 'Survey with the source on'),
          h('button', {type: 'button', class: 'btn', disabled: !usable, onClick: () => pass('off')}, 'Noise pass with the source off'),
          h('button', {type: 'button', class: 'btn btn--ghost', onClick: () => { draft.on.clear(); draft.off.clear(); show(); }}, 'Start over')),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes),
        h('p', {class: 'note'}, 'Keep the source level and position constant for both passes; microphones are measured one after another.'));
      show();
      return;
    }

    if (t.key === 'arraygain') {
      const controls = captureControls({sectorDefault: defaultSector()});
      const microphone = numberInput('Comparison microphone', 0, {width: '120px', step: 1});
      const notes = notesInput();
      const draft = state.draft ||= {on: null, off: null, theory: null};
      const show = () => {
        const rows = ['on', 'off'].map((key) => h('div', {class: 'ms-row'}, h('span', {}, `Source ${key}`),
          h('span', {class: 'mono'}, draft[key]
            ? `beam ${num(draft[key].beam.mean_db, 1)} · microphone ${num(draft[key].mic.mean_db, 1)} dBFS` : '—')));
        let figure = null;
        if (draft.on && draft.off) {
          const levels = {beamOn: draft.on.beam.mean_db, beamOff: draft.off.beam.mean_db,
            micOn: draft.on.mic.mean_db, micOff: draft.off.mic.mean_db};
          const beamSnr = snrDb(levels.beamOn, levels.beamOff);
          const micSnr = snrDb(levels.micOn, levels.micOff);
          const same = draft.on.beam.value === draft.off.beam.value && draft.on.mic.value === draft.off.mic.value;
          const gain = same && micSnr >= MIN_SNR_DB ? arrayGainDb(levels) : NaN;
          const expected = draft.theory?.white_noise_gain_db;
          const finite = (value) => (Number.isFinite(value) ? value : null);
          const problem = !same ? 'The two passes used different sectors or microphones; capture both again.'
            : !(micSnr >= MIN_SNR_DB) ? `The microphone's SNR is ${Number.isFinite(micSnr) ? `${num(micSnr, 1)} dB` : 'below its noise'}; it needs ${MIN_SNR_DB} dB. Raise the source or bring it closer.`
              : null;
          figure = [
            h('div', {class: 'ms-figure'}, h('span', {class: 'figure-label'}, 'Array gain'),
              h('span', {class: 'ms-figure__value'}, Number.isFinite(gain) ? `${num(gain, 1)} dB` : '—')),
            h('p', {class: problem ? 'note is-warn' : 'note'}, `Beam SNR ${snrText(beamSnr)}, microphone SNR ${snrText(micSnr)}.` +
              (problem ? ` ${problem}` : Number.isFinite(expected) ? ` Theory against noise uncorrelated between microphones: ${num(expected, 1)} dB. Room noise is partly correlated at low frequencies, so expect less.` : '')),
            same ? h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('arraygain',
              `Array gain, sector ${draft.on.beam.value}, microphone ${draft.on.mic.value}`,
              [draft.on.beam, draft.on.mic, draft.off.beam, draft.off.mic],
              {array_gain_db: finite(gain), beam_snr_db: finite(beamSnr), microphone_snr_db: finite(micSnr),
                minimum_snr_db: MIN_SNR_DB, theory_white_noise_gain_db: expected ?? null,
                summary: Number.isFinite(gain) ? `${num(gain, 1)} dB` : 'not measurable'}, notes.value)}, 'Save run') : null,
          ];
        }
        put(results, ...rows, figure);
      };
      const pass = async (key) => {
        state.busy = true; progress.hidden = false;
        try {
          draft[key] = await withSettings(session, MCU_BEAM, () => withCh1(session, async () => {
            const beam = await capture(session, controls.sector.value, controls.seconds.value,
              Number(controls.rate.value), (fraction) => { progress.value = fraction / 2; });
            await session.selectOutputChecked(1, microphone.value);
            await session.setSetting('channel', 1, 'channel', false);
            const mic = await capture(session, controls.sector.value, controls.seconds.value,
              Number(controls.rate.value), (fraction) => { progress.value = 0.5 + fraction / 2; });
            return {beam: {label: `steered beam, source ${key}`, value: controls.sector.value, ...beam},
              mic: {label: `microphone ${microphone.value}, source ${key}`, value: microphone.value, ...mic}};
          }));
          draft.theory ||= await apiJson('/study/theory/cut?step_deg=10').catch(() => null);
          setText(status, `Captured the beam and the microphone with the source ${key}.`);
        } catch (error) { setText(status, error.message); }
        finally { state.busy = false; progress.hidden = true; show(); }
      };
      const usable = connected && session.hasSelectors;
      put(body, header(t), needConnection, selectorWarning, controls.el, microphone.el,
        h('div', {class: 'toolbar'},
          h('button', {type: 'button', class: 'btn btn--primary', disabled: !usable, onClick: () => pass('on')}, 'Capture with the source on'),
          h('button', {type: 'button', class: 'btn', disabled: !usable, onClick: () => pass('off')}, 'Capture with the source off')),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes));
      show();
      return;
    }

    if (t.key === 'detectorstep') {
      const loud = numberInput('Loud sector', defaultSector(), {width: '100px', step: 1});
      const quiet = numberInput('Quiet sector', 0, {width: '100px', step: 1});
      const settles = h('input', {class: 'input input--mono', style: {width: '100%'},
        value: '250,500,750,1000,1500,2000,3000,4000,6000,8000,12000,16000,20000'});
      const repeats = numberInput('Repeats', 3, {width: '90px', step: 1});
      const notes = notesInput();
      const run = async () => {
        const list = settles.value.split(',').map((v) => Math.round(Number(v.trim())))
          .filter((v) => v >= 50 && v <= 20000);
        const count = Math.max(1, Math.min(10, Math.round(repeats.value)));
        if (list.length < 3) { setText(status, 'Give at least three settle times between 50 and 20000 µs.'); return; }
        state.busy = true; progress.hidden = false;
        try {
          const original = session.latest?.settings?.settle_us ?? 1250;
          let applied = {};
          const readings = await withSettings(session, {...DSP_DETECTOR, settle_us: original}, async (settings) => {
            applied = settings;
            const taken = [];
            let done = 0;
            for (let pass = 0; pass < count; pass++) {
              for (const settle of list) {
                // References are taken next to each reading, so a source that drifts cancels out.
                await readOnce(session, `S,${loud.value}`, loud.value);
                await sleep(150);
                const loudDb = await readOnce(session, 'M', loud.value);
                await session.setSetting('settle_us', settle, 'settle', false);
                const levelDb = await readOnce(session, `S,${quiet.value}`, quiet.value);
                await sleep(150);
                const quietDb = await readOnce(session, 'M', quiet.value);
                taken.push({settle_us: settle, level_db: levelDb, loud_db: loudDb, quiet_db: quietDb});
                done += 1;
                progress.value = done / (count * list.length);
                setText(status, `Settle ${settle} µs, pass ${pass + 1} of ${count}…`);
              }
            }
            return taken;
          });
          const fit = fitDetectorDecay(readings);
          const contractTau = Number(session.latest?.level_time_constant_ms || 8.665);
          const contrast = readings.map((r) => r.loud_db - r.quiet_db).sort((a, b) => a - b)[Math.floor(readings.length / 2)];
          const rows = list.map((settle) => {
            const group = readings.filter((r) => r.settle_us === settle);
            const fractions = group.map((r) => remainingFraction(r)).filter(Number.isFinite);
            return {settle, group, fraction: fractions.length ? fractions.reduce((sum, f) => sum + f, 0) / fractions.length : NaN};
          });
          const captures = rows.map(({settle, group}) => {
            const levels = group.map((r) => r.level_db);
            return {label: `settle ${settle} us`, value: settle, n: levels.length, mean_db: rawMeanDb(levels),
              std_db: stdDb(levels), min_db: Math.min(...levels), max_db: Math.max(...levels),
              settings: {...applied, settle_us: settle}};
          });
          put(results,
            h('div', {class: 'ms-figure'}, h('span', {class: 'figure-label'}, 'Detector time constant'),
              h('span', {class: 'ms-figure__value'}, fit ? `${num(fit.tau_ms, 2)} ms` : 'no fit')),
            h('p', {class: 'note'}, `${fit ? `${fit.points_used} of ${readings.length} readings used, r² ${num(fit.r2, 3)}. ` : ''}` +
              `Contract ${num(contractTau, 3)} ms. Loud and quiet directions differ by ${num(contrast, 1)} dB.` +
              (contrast < 10 ? ' Less than 10 dB: move the source or pick directions further apart.' : '')),
            ...rows.map(({settle, fraction}) => h('div', {class: 'ms-row'}, h('span', {}, `${settle} µs`),
              h('span', {class: 'mono'}, `${num(100 * fraction, 1)} % left · ${num(100 * Math.exp(-settle / 1000 / contractTau), 1)} % at the contract time constant`))),
            h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('detectorstep',
              `Detector time constant, sectors ${loud.value} to ${quiet.value}`, captures,
              {tau_ms: fit?.tau_ms ?? null, r2: fit?.r2 ?? null, points_used: fit?.points_used ?? 0,
                contract_tau_ms: contractTau, contrast_db: contrast, readings,
                summary: fit ? `${num(fit.tau_ms, 2)} ms` : 'no fit'}, notes.value)}, 'Save run'));
          setText(status, fit ? `Fitted ${num(fit.tau_ms, 2)} ms.` : 'Could not fit a decay; check the two directions differ clearly.');
        } catch (error) { setText(status, error.message); }
        finally { state.busy = false; progress.hidden = true; }
      };
      put(body, header(t), needConnection,
        h('div', {class: 'form-row'}, loud.el, quiet.el, repeats.el),
        h('div', {class: 'field'}, h('label', {}, 'Settle times (µs)'), settles),
        h('button', {type: 'button', class: 'btn btn--primary', disabled: !connected, onClick: run}, 'Measure time constant'),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes),
        h('p', {class: 'note'}, 'Uses the DSP detector, which also reads low-frequency content, about the same in every direction. Pick a loud direction on a loud, close source and a quiet one well away from it.'));
      return;
    }

    if (t.key === 'beamcut') {
      const axis = h('select', {class: 'select'}, h('option', {value: 'azimuth'}, 'Azimuth'),
        h('option', {value: 'elevation'}, 'Elevation'));
      const minimum = numberInput('Minimum (°)', -30, {width: '100px'});
      const maximum = numberInput('Maximum (°)', 30, {width: '100px'});
      const count = numberInput('Directions', 13, {width: '90px', step: 1});
      const fixed = numberInput('Other axis (°)', 0, {width: '100px'});
      const speaker = numberInput('Speaker on the cut axis (°)', 0, {width: '120px'});
      const frequency = numberInput('Theory frequency (Hz)', 2000, {width: '120px', step: 100});
      const seconds = numberInput('Per direction (s)', 0.5, {width: '110px', step: 0.1});
      const notes = notesInput();
      const chart = s('svg', {viewBox: '0 0 520 220', class: 'chart', role: 'img',
        'aria-label': 'Measured and theoretical beam-pattern cut'});
      const run = async () => {
        state.busy = true; progress.hidden = false;
        try {
          const directions = Math.max(2, Math.min(49, Math.round(count.value)));
          const isAzimuth = axis.value === 'azimuth';
          const captures = await withSettings(session, MCU_BEAM, () => withDirectionGrid(session, {
            azimuthMin: isAzimuth ? minimum.value : fixed.value,
            azimuthMax: isAzimuth ? maximum.value : fixed.value,
            columns: isAzimuth ? directions : 1,
            elevationMin: isAzimuth ? fixed.value : minimum.value,
            elevationMax: isAzimuth ? fixed.value : maximum.value,
            rows: isAzimuth ? 1 : directions,
          }, async (azimuths, elevations) => {
            const measured = [];
            for (let sector = 0; sector < directions; sector++) {
              const angle = isAzimuth ? azimuths[sector] : elevations[sector];
              setText(status, `Measuring ${axis.value} ${deg(angle, 1)} (${sector + 1}/${directions})…`);
              const levels = await capture(session, sector, seconds.value, 20,
                (fraction) => { progress.value = (sector + fraction) / directions; });
              measured.push({label: `${axis.value} ${angle}`, value: angle, ...levels});
            }
            return measured;
          }));
          // Steering across a fixed source traces the same pattern as a source moving past a fixed beam.
          // The theory is drawn finer than the measured spacing so its nulls show.
          const query = new URLSearchParams({axis: axis.value,
            steer_azimuth_deg: String(isAzimuth ? speaker.value : fixed.value),
            steer_elevation_deg: String(isAzimuth ? fixed.value : speaker.value),
            frequency_hz: String(frequency.value), minimum_deg: String(minimum.value),
            maximum_deg: String(maximum.value), step_deg: String((maximum.value - minimum.value) / 240)});
          const theory = await apiJson(`/study/theory/cut?${query}`);
          const peak = Math.max(...captures.map((item) => item.mean_db));
          const measuredPoints = captures.map((item) => ({angle: item.value, level: item.mean_db - peak}));
          const measuredWidth = widthAt(measuredPoints, 3);
          const measuredPeak = refinePeak(captures.map((item) => item.mean_db), captures.map((item) => item.value));
          const x = (angle) => 48 + (angle - minimum.value) / (maximum.value - minimum.value) * 440;
          const y = (level) => 16 + Math.min(30, Math.max(0, -level)) / 30 * 180;
          const span = maximum.value - minimum.value;
          const tickStep = [1, 2, 5, 10, 15, 20, 30, 45, 90].find((step) => span / step <= 8) || 90;
          const angleTicks = [];
          for (let angle = Math.ceil(minimum.value / tickStep) * tickStep; angle <= maximum.value + 1e-9; angle += tickStep) {
            angleTicks.push(angle);
          }
          chart.replaceChildren(
            ...[0, -6, -12, -18, -24, -30].flatMap((level) => [
              s('line', {class: 'grid', x1: 48, x2: 488, y1: y(level), y2: y(level)}),
              s('text', {class: 'tick', x: 40, y: y(level) + 4, 'text-anchor': 'end'}, level),
            ]),
            ...angleTicks.map((angle) => s('text', {class: 'tick', x: x(angle), y: 212, 'text-anchor': 'middle'}, `${angle}°`)),
            s('text', {class: 'tick', x: 4, y: 10}, 'dB'),
            s('path', {d: theory.points.map((point, index) => `${index ? 'L' : 'M'}${x(point.angle_deg)} ${y(point.gain_db)}`).join(' '),
              fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.5, 'stroke-dasharray': '5 4'}),
            s('path', {d: measuredPoints.map((point, index) => `${index ? 'L' : 'M'}${x(point.angle)} ${y(point.level)}`).join(' '),
              fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2.5}),
            ...measuredPoints.map((point) => s('circle', {cx: x(point.angle), cy: y(point.level), r: 3, fill: 'var(--accent)'})),
          );
          put(results, chart,
            h('div', {class: 'chart-legend'}, h('span', {class: 'lg-line lg-line--proposed'}), 'Measured, normalised to its peak',
              h('span', {class: 'lg-line lg-line--device'}), `Theory at ${frequency.value} Hz`),
            h('p', {class: 'ms-prompt'}, `Measured −3 dB width ${measuredWidth ? `${num(measuredWidth.width, 2)}°` : 'not bracketed'} · theory ${num(theory.beamwidth_3db?.width_deg, 2)}°. Peak at ${deg(measuredPeak, 2)}, ${num(measuredPeak - speaker.value, 2)}° from the speaker.`),
            h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('beamcut',
              `${axis.value} beam cut, ${frequency.value} Hz`, captures,
              {axis: axis.value, frequency_hz: frequency.value, speaker_deg: speaker.value,
                measured_peak_deg: measuredPeak,
                measured_width_3db_deg: measuredWidth?.width ?? null,
                theory_width_3db_deg: theory.beamwidth_3db?.width_deg ?? null,
                theory: theory.points, summary: measuredWidth ? `${num(measuredWidth.width, 1)}°` : 'edge not bracketed'}, notes.value)}, 'Save run'));
          setText(status, `Measured ${directions} directions.`);
        } catch (error) { setText(status, error.message); }
        finally { state.busy = false; progress.hidden = true; }
      };
      put(body, header(t), needConnection,
        h('div', {class: 'form-row'}, h('div', {class: 'field'}, h('label', {}, 'Cut axis'), axis),
          minimum.el, maximum.el, count.el, fixed.el, speaker.el, frequency.el, seconds.el),
        h('div', {class: 'toolbar'},
          h('button', {type: 'button', class: 'btn btn--primary', disabled: !connected, onClick: run}, 'Measure cut'),
          h('button', {type: 'button', class: 'btn btn--ghost', onClick: () => session.send({type: 'request_init'})}, 'Restore deployment grid')),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes));
      return;
    }

    if (t.key === 'steering') {
      const trueAzimuth = numberInput('Speaker azimuth (°)', 0, {width: '120px'});
      const trueElevation = numberInput('Speaker elevation (°)', 0, {width: '120px'});
      const span = numberInput('Search half-span (°)', 6, {width: '120px'});
      const count = numberInput('Directions per axis', 5, {width: '120px', step: 1});
      const seconds = numberInput('Per direction (s)', 0.4, {width: '110px', step: 0.1});
      const notes = notesInput();
      const run = async () => {
        state.busy = true; progress.hidden = false;
        try {
          const size = Math.max(2, Math.min(7, Math.round(count.value)));
          let azimuths = []; let elevations = [];
          const captures = await withSettings(session, MCU_BEAM, () => withDirectionGrid(session, {
            azimuthMin: trueAzimuth.value - span.value, azimuthMax: trueAzimuth.value + span.value, columns: size,
            elevationMin: trueElevation.value - span.value, elevationMax: trueElevation.value + span.value, rows: size,
          }, async (gridAzimuths, gridElevations) => {
            azimuths = gridAzimuths; elevations = gridElevations;
            const measured = [];
            for (let sector = 0; sector < size * size; sector++) {
              const row = Math.floor(sector / size); const column = sector % size;
              const azimuth = azimuths[column]; const elevation = elevations[row];
              setText(status, `Measuring ${sector + 1}/${size * size}: ${deg(azimuth, 1)}, ${deg(elevation, 1)}…`);
              const levels = await capture(session, sector, seconds.value, 20,
                (fraction) => { progress.value = (sector + fraction) / (size * size); });
              measured.push({label: `${azimuth},${elevation}`, value: sector, azimuth_deg: azimuth,
                elevation_deg: elevation, ...levels});
            }
            return measured;
          }));
          const peak = captures.reduce((best, item) => item.mean_db > best.mean_db ? item : best);
          const peakRow = Math.floor(peak.value / size); const peakColumn = peak.value % size;
          const onEdge = peakRow === 0 || peakRow === size - 1 || peakColumn === 0 || peakColumn === size - 1;
          const grid = Array.from({length: size}, (_, row) =>
            captures.slice(row * size, (row + 1) * size).map((item) => item.mean_db));
          const refined = refinePeak2d(grid, azimuths, elevations);
          const azimuthError = refined.azimuth - trueAzimuth.value;
          const elevationError = refined.elevation - trueElevation.value;
          const error = Math.hypot(azimuthError, elevationError);
          put(results,
            h('div', {class: 'ms-figure'}, h('span', {class: 'figure-label'}, 'Steering error'),
              h('span', {class: 'ms-figure__value'}, `${num(error, 2)}°`)),
            h('p', {class: 'note'}, `Interpolated peak ${deg(refined.azimuth, 2)}, ${deg(refined.elevation, 2)}; strongest grid point ${deg(peak.azimuth_deg, 2)}, ${deg(peak.elevation_deg, 2)} at ${num(peak.mean_db, 1)} dBFS.`),
            onEdge ? h('p', {class: 'note'}, 'The strongest point is on the edge of the search grid, so the true peak may lie outside it. Widen the half-span or move the search centre and measure again.') : null,
            h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('steering',
              `Steering accuracy at ${trueAzimuth.value},${trueElevation.value}`, captures,
              {true_azimuth_deg: trueAzimuth.value, true_elevation_deg: trueElevation.value,
                peak_azimuth_deg: refined.azimuth, peak_elevation_deg: refined.elevation,
                grid_peak_azimuth_deg: peak.azimuth_deg, grid_peak_elevation_deg: peak.elevation_deg,
                peak_on_edge: onEdge,
                azimuth_error_deg: azimuthError, elevation_error_deg: elevationError,
                angular_error_deg: error, summary: `${num(error, 2)}°${onEdge ? ' (edge)' : ''}`}, notes.value)}, 'Save run'));
          setText(status, `Peak angular error ${num(error, 2)}°${onEdge ? ', peak on the grid edge' : ''}.`);
        } catch (error) { setText(status, error.message); }
        finally { state.busy = false; progress.hidden = true; }
      };
      put(body, header(t), needConnection,
        h('div', {class: 'form-row'}, trueAzimuth.el, trueElevation.el, span.el, count.el, seconds.el),
        h('button', {type: 'button', class: 'btn btn--primary', disabled: !connected, onClick: run}, 'Measure steering accuracy'),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes));
      return;
    }

    if (t.key === 'snr') {
      const controls = captureControls({sectorDefault: defaultSector()});
      const notes = notesInput();
      const draft = state.draft ||= {on: null, off: null};
      const show = () => {
        const rows = [['Source on', draft.on], ['Source off', draft.off]].map(([k, c]) => h('div', {class: 'ms-row'}, h('span', {}, k), h('span', {class: 'mono'}, c ? `${num(c.mean_db, 1)} dBFS ± ${num(c.std_db, 1)} (${c.n})` : '—')));
        let figure = null;
        if (draft.on && draft.off && draft.on.sector !== draft.off.sector) {
          figure = h('p', {class: 'note is-warn'}, 'The two captures held different sectors; capture both again on one sector.');
        } else if (draft.on && draft.off) {
          const snr = snrDb(draft.on.mean_db, draft.off.mean_db);
          figure = h('div', {class: 'ms-figure'}, h('span', {class: 'figure-label'}, 'SNR'), h('span', {class: 'ms-figure__value'}, snrText(snr)),
            h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('snr', `SNR, sector ${draft.on.sector}`, [draft.on, draft.off], {snr_db: Number.isFinite(snr) ? snr : null, summary: `SNR ${snrText(snr)}`}, notes.value)}, 'Save run'));
        }
        put(results, ...rows, figure);
      };
      put(body, header(t), needConnection, controls.el,
        h('div', {class: 'toolbar'},
          h('button', {type: 'button', class: 'btn', disabled: !connected, onClick: async () => { draft.on = await doCapture(controls, 'source on', undefined, MCU_BEAM) || draft.on; show(); }}, 'Capture with the source on'),
          h('button', {type: 'button', class: 'btn', disabled: !connected, onClick: async () => { draft.off = await doCapture(controls, 'source off', undefined, MCU_BEAM) || draft.off; show(); }}, 'Capture with the source off')),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes),
        h('p', {class: 'note'}, 'SNR = 10·log10((P_on − P_off) / P_off) with P = 10^(L/10). MCU windows report RMS, so the dB figures need no detector-scale correction.'));
      show();
      return;
    }

    if (t.key === 'width') {
      const controls = captureControls({sectorDefault: defaultSector()});
      const angles = h('input', {class: 'input input--mono', value: '-30,-20,-15,-10,-5,0,5,10,15,20,30', style: {width: '100%'}});
      const notes = notesInput();
      const draft = state.draft ||= {points: [], index: 0};
      const list = () => angles.value.split(',').map((v) => Number(v.trim())).filter(Number.isFinite);
      const chart = s('svg', {viewBox: '0 0 520 220', class: 'chart', role: 'img', 'aria-label': 'Measured level against source angle'});
      const show = () => {
        const all = list();
        const next = all[draft.index];
        setText(prompt, next === undefined ? 'All angles recorded.' : `Put the source at ${deg(next, 0)} (angle ${draft.index + 1} of ${all.length}), then record.`);
        recordButton.disabled = !connected || next === undefined;
        const pts = [...draft.points].sort((a, b) => a.angle - b.angle);
        const w3 = widthAt(pts, 3); const w6 = widthAt(pts, 6);
        if (pts.length) {
          const lo = Math.min(...all, ...pts.map((p) => p.angle)); const hi = Math.max(...all, ...pts.map((p) => p.angle));
          const top = Math.max(...pts.map((p) => p.level));
          const x = (a) => 48 + (a - lo) / Math.max(1, hi - lo) * 440;
          const y = (l) => 16 + Math.min(24, top - l) / 24 * 180;
          chart.replaceChildren(
            ...[0, -6, -12, -18, -24].flatMap((d) => [s('line', {class: 'grid', x1: 48, x2: 488, y1: y(top + d), y2: y(top + d)}), s('text', {class: 'tick', x: 40, y: y(top + d) + 4, 'text-anchor': 'end'}, num(d, 0))]),
            s('line', {class: 'guide', x1: 48, x2: 488, y1: y(top - 3), y2: y(top - 3)}),
            s('path', {d: pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.angle)} ${y(p.level)}`).join(' '), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2}),
            ...pts.map((p) => s('circle', {cx: x(p.angle), cy: y(p.level), r: 3.5, fill: 'var(--accent)'})),
            ...[lo, (lo + hi) / 2, hi].map((a) => s('text', {class: 'tick', x: x(a), y: 214, 'text-anchor': 'middle'}, deg(a, 0))),
          );
        } else chart.replaceChildren();
        put(results, chart,
          h('div', {class: 'readouts'},
            h('div', {class: 'readout-item'}, h('span', {class: 'figure-label'}, '−3 dB width'), h('span', {class: 'mono'}, w3 ? `${num(w3.width, 1)}°` : '—')),
            h('div', {class: 'readout-item'}, h('span', {class: 'figure-label'}, '−6 dB width'), h('span', {class: 'mono'}, w6 ? `${num(w6.width, 1)}°` : '—')),
            h('div', {class: 'readout-item'}, h('span', {class: 'figure-label'}, 'Peak at'), h('span', {class: 'mono'}, w3 ? deg(w3.peak, 1) : '—'))),
          pts.length >= 3 ? h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('width', `Main-lobe width, sector ${pts[0].capture.sector}`,
            pts.map((p) => ({...p.capture, label: `source ${p.angle}`, value: p.angle})),
            {width_3db_deg: w3 ? Number(w3.width.toFixed(2)) : null, width_6db_deg: w6 ? Number(w6.width.toFixed(2)) : null, peak_deg: w3?.peak ?? null, summary: w3 ? `−3 dB ${num(w3.width, 1)}°` : 'no −3 dB edge'}, notes.value)}, 'Save run') : null);
      };
      const prompt = h('p', {class: 'ms-prompt'});
      const recordButton = h('button', {type: 'button', class: 'btn btn--primary', onClick: async () => {
        const angle = list()[draft.index];
        const c = await doCapture(controls, `source at ${angle}°`, angle, MCU_BEAM);
        const held = draft.points[0]?.capture.sector;
        if (c && held !== undefined && c.sector !== held) {
          setText(status, `The earlier angles held sector ${held}, this capture held ${c.sector}. Start over to change the beam.`);
          return;
        }
        if (c) { draft.points = draft.points.filter((p) => p.angle !== angle).concat([{angle, level: c.mean_db, capture: c}]); draft.index += 1; }
        show();
      }}, 'Record this angle');
      put(body, header(t), needConnection, controls.el,
        h('div', {class: 'field'}, h('label', {}, 'Source angles (degrees, relative to the held beam)'), angles),
        prompt, h('div', {class: 'toolbar'}, recordButton,
          h('button', {type: 'button', class: 'btn btn--ghost', onClick: () => { draft.index = Math.max(0, draft.index - 1); show(); }}, 'Back one'),
          h('button', {type: 'button', class: 'btn btn--ghost', onClick: () => { draft.points = []; draft.index = 0; show(); }}, 'Start over')),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes),
        h('p', {class: 'note'}, 'Keep the distance and the source level constant, and measure the angle from the array centre. A turntable under the array works as well as moving the speaker.'));
      angles.addEventListener('change', show);
      show();
      return;
    }

    if (t.key === 'scale' || t.key === 'dynamic') {
      const isScale = t.key === 'scale';
      const settings = isScale ? DSP_DETECTOR : MCU_BEAM;
      const controls = captureControls({sectorDefault: defaultSector()});
      const setLevel = numberInput(isScale ? 'Source level change (dB)' : 'Source level (dB, relative)', isScale ? 0 : -30, {width: '120px'});
      const notes = notesInput();
      const draft = state.draft ||= {captures: []};
      const round = (value, digits) => Number(value.toFixed(digits));
      const show = () => {
        const caps = draft.captures;
        const floors = caps.filter((c) => c.value === null);
        const rows = caps.map((c) => h('div', {class: 'ms-row'}, h('span', {}, c.label), h('span', {class: 'mono'}, `${num(c.mean_db, 1)} dBFS ± ${num(c.std_db, 1)}`)));
        let summary = null;
        let result = null;
        if (new Set(caps.map((c) => c.sector)).size > 1) {
          summary = h('p', {class: 'note is-warn'}, 'The captures held different sectors. Start over and keep one sector.');
        } else if (isScale) {
          const floorDb = floors.length ? powerMean(floors.map((c) => c.mean_db)) : null;
          const scale = detectorScale(caps, floorDb);
          if (scale) {
            const {ratio, margin_db: margin} = scale;
            const valid = margin !== null && margin >= SCALE_MARGIN_DB;
            const verdict = margin === null ? 'Capture the floor with the source off: the result counts only when the quietest level is well above it.'
              : !valid ? `The quietest level is ${num(margin, 1)} dB above the floor; it needs ${SCALE_MARGIN_DB} dB. Bring the source closer or raise every level.`
                : Math.abs(ratio - 1) < 0.2 ? 'The reading follows the source 1:1. The detector reports amplitude dB.'
                  : Math.abs(ratio - 2) < 0.3 ? 'The reading moves twice as far as the source. Power is counted twice; halve dB differences.'
                    : `The reading moves ${ratio.toFixed(2)}× the source step. Check for clipping.`;
            summary = [h('p', {class: 'ms-prompt'}, `Source ${num(scale.step_db, 1)} dB, reading ${num(scale.seen_db, 1)} dB (least-squares slope ${ratio.toFixed(2)}).`),
              h('p', {class: valid ? 'note' : 'note is-warn'}, verdict)];
            result = {step_db: scale.step_db, seen_db: round(scale.seen_db, 2), ratio: round(ratio, 3),
              floor_dbfs: floorDb === null ? null : round(floorDb, 2), margin_db: margin === null ? null : round(margin, 2),
              minimum_margin_db: SCALE_MARGIN_DB, valid,
              summary: valid ? `ratio ${ratio.toFixed(2)}` : margin === null ? 'no floor captured' : 'not valid, source near the floor'};
          } else if (caps.length) {
            summary = h('p', {class: 'note'}, 'Capture the floor with the source off and the source at two or more levels.');
          }
        } else if (caps.length) {
          const range = dynamicRange(caps);
          const message = !floors.length ? 'Capture the floor with the source off as well.'
            : !range ? 'Capture the source at two or more levels.'
              : range.range_db === null ? 'The reading does not follow the source between any two levels. Raise the levels above the floor.'
                : `Floor ${num(range.floor_db, 1)} dBFS, highest level that still follows the source ${num(range.ceiling_db, 1)} dBFS: dynamic range ${num(range.range_db, 1)} dB.`;
          summary = h('p', {class: 'ms-prompt'}, message);
          result = range?.range_db != null ? {floor_dbfs: round(range.floor_db, 2), ceiling_dbfs: round(range.ceiling_db, 2),
            range_db: round(range.range_db, 2), summary: `range ${num(range.range_db, 1)} dB`} : null;
        }
        put(results, ...rows, summary, result ? h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun(t.key, t.name, caps, result, notes.value)}, 'Save run') : null);
      };
      put(body, header(t), needConnection, controls.el, setLevel.el,
        h('div', {class: 'toolbar'},
          h('button', {type: 'button', class: 'btn', disabled: !connected, onClick: async () => {
            const c = await doCapture(controls, 'floor, source off', null, settings);
            if (c) draft.captures.unshift(c); show();
          }}, 'Capture the floor (source off)'),
          h('button', {type: 'button', class: 'btn', disabled: !connected, onClick: async () => {
            const c = await doCapture(controls, `source ${num(setLevel.value, 1)} dB`, setLevel.value, settings);
            if (c) draft.captures.push(c); show();
          }}, 'Capture at this level'),
          h('button', {type: 'button', class: 'btn btn--ghost', onClick: () => { draft.captures = []; show(); }}, 'Start over')),
        progress, status, results, h('div', {class: 'field'}, h('label', {}, 'Notes'), notes));
      show();
      return;
    }

    if (t.key === 'range') {
      const lastSnr = state.runs.find((r) => r.template === 'snr' && Number.isFinite(r.result?.snr_db));
      const snr0 = numberInput('Measured SNR (dB)', lastSnr ? lastSnr.result.snr_db.toFixed(1) : 20, {width: '120px'});
      const d0 = numberInput('at distance (m)', 10, {width: '110px'});
      const threshold = numberInput('Detection threshold (dB)', 6, {width: '120px'});
      const alpha = numberInput('Air absorption (dB/km)', 5, {width: '120px'});
      const chart = s('svg', {viewBox: '0 0 520 220', class: 'chart', role: 'img', 'aria-label': 'Predicted SNR against distance'});
      const answer = h('p', {class: 'ms-prompt'});
      const show = () => {
        const {range, snrAt} = rangeFor(snr0.value, Math.max(0.1, d0.value), threshold.value, Math.max(0, alpha.value));
        setText(answer, range ? `Estimated detection range: ${range < 1000 ? `${Math.round(range)} m` : `${(range / 1000).toFixed(2)} km`}. This is an extrapolation, not a measurement.` : 'The measured SNR is already below the threshold at that distance.');
        const dMin = Math.max(0.1, d0.value) / 2; const dMax = Math.max((range || d0.value) * 3, d0.value * 4);
        const x = (d) => 48 + Math.log10(d / dMin) / Math.log10(dMax / dMin) * 440;
        const top = Math.ceil(snr0.value + 10); const bottom = Math.min(threshold.value - 10, 0);
        const y = (v) => 16 + (top - Math.max(bottom, Math.min(top, v))) / (top - bottom) * 180;
        const pts = Array.from({length: 101}, (_, i) => dMin * (dMax / dMin) ** (i / 100));
        chart.replaceChildren(
          s('line', {class: 'guide', x1: 48, x2: 488, y1: y(threshold.value), y2: y(threshold.value)}),
          s('text', {class: 'tick', x: 492, y: y(threshold.value) + 4}, 'threshold'),
          s('path', {d: pts.map((d, i) => `${i ? 'L' : 'M'}${x(d).toFixed(1)} ${y(snrAt(d)).toFixed(1)}`).join(' '), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2}),
          s('circle', {cx: x(Math.max(0.1, d0.value)), cy: y(snr0.value), r: 4, fill: 'var(--text)'}),
          ...[dMin, Math.sqrt(dMin * dMax), dMax].map((d) => s('text', {class: 'tick', x: x(d), y: 214, 'text-anchor': 'middle'}, d < 1000 ? `${Math.round(d)} m` : `${(d / 1000).toFixed(1)} km`)),
          ...[top, threshold.value, bottom].map((v) => s('text', {class: 'tick', x: 40, y: y(v) + 4, 'text-anchor': 'end'}, num(v, 0))),
        );
        put(results, answer, chart, range ? h('button', {type: 'button', class: 'btn btn--primary', onClick: () => saveRun('range', 'Detection range estimate', [],
          {snr_db: snr0.value, distance_m: d0.value, threshold_db: threshold.value, absorption_db_per_km: alpha.value, range_m: Math.round(range), summary: `≈ ${Math.round(range)} m`}, '')}, 'Save estimate') : null);
      };
      for (const f of [snr0, d0, threshold, alpha]) f.input.addEventListener('input', show);
      put(body, header(t), h('div', {class: 'form-row'}, snr0.el, d0.el, threshold.el, alpha.el), results,
        h('p', {class: 'note'}, 'SNR(d) = SNR₀ − 20·log10(d/d₀) − α·(d − d₀). Assumes a steady source and ambient noise that does not change with distance. The real answer is a field log with a drone at known distances.'));
      show();
    }
  }

  renderTemplates();
  renderBody();
  renderRuns();
  return {el, render() { if (!state.busy) { renderTemplates(); renderBody(); } }};
}
