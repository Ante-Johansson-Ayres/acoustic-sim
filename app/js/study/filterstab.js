/**
 * filterstab.js — Study · Parameters · Filters: every filter of the signal chain. The steered beam's FIR
 * stages, the bank's band-pass, anti-alias low-pass and Ch1 low-pass (DSP words), the MCU window band
 * (firmware settings) and the listening copy (this browser). Each shows its response on the device, as
 * flashed and as proposed. The FIR blocks have a designer. Edits only stage: Apply writes them.
 */

import { h, options, s, segmented, setText } from '../shared/dom.js';
import { num } from '../shared/format.js';
import { BAND_HIGH_HZ, BAND_LOW_HZ, bandGain, listenGain } from '../hardware/listenfilter.js';
import { LISTEN_PRESETS } from '../hardware/session.js';
import { ONE, dsp } from './dsp.js';
import { TYPES, WINDOWS, bypassTaps, design, designProblems, firGain, toWords, transitionHz } from './filters.js';

export const FILTERS = [
  {key: 'ch0', groups: ['fir1', 'fir2'], rate: 48000, title: 'Steered beam FIR (Ch0)',
    where: 'Two FIR stages after the beam sum, at 48 kHz.',
    affects: 'Ch0 audio and recordings, and scans that read the DSP detector (measure 0).',
    design: {type: 'lowpass', low: 500, high: 3500, window: 'hamming'}},
  {key: 'bank_band', groups: ['bank_band'], rate: 12000, title: 'Bank band-pass (Live detectors)',
    where: 'One for each bank beam, at 12 kHz, before its level detector.',
    affects: 'What Live detects, and the direction that it reports.',
    warning: 'This changes Live detection. Each boot check (I) loads the flashed band-pass again: the tests, a reconnect and a reset send one.',
    design: {type: 'bandpass', low: 500, high: 3500, window: 'hamming'}},
  {key: 'bank_alias', groups: ['bank_alias'], rate: 48000, title: 'Bank anti-alias low-pass',
    where: 'One for each microphone, at 48 kHz, before the bank down-sampler (48 to 12 kHz).',
    affects: 'Every bank beam: Live, and the bank beam on Ch1.',
    warning: 'The bank runs at 12 kHz. Sound above 6 kHz that this filter lets through folds back into the band as false sound. Keep the cutoff at approximately 5 kHz or lower.',
    design: {type: 'lowpass', low: 500, high: 5000, window: 'hamming'}},
  {key: 'bank_ch1', groups: ['bank_ch1'], rate: 48000, title: 'Bank Ch1 low-pass',
    where: 'After the up-sampler (12 to 48 kHz) of the bank beam that Ch1 carries.',
    affects: 'Ch1 audio and recordings when Ch1 listens to a bank beam. Not Live.',
    warning: 'It removes the copies of the band that the up-sampler makes above 6 kHz. Its taps cannot make an edge at a few hundred hertz: to hear a bank beam through its band-pass, use the listening copy.',
    design: {type: 'lowpass', low: 500, high: 5000, window: 'hamming'}},
  {key: 'mcu', rate: 48000, title: 'MCU window band',
    where: 'Firmware: a fourth-order high-pass and a second-order low-pass at 48 kHz (SET band_low_hz, band_high_hz).',
    affects: 'MCU windows (measure 1): scans, the fine level of Live, and the tests.'},
  {key: 'listen', rate: 48000, title: 'Listening copy',
    where: 'This computer: the copy that Save WAV adds to a recording (Hardware, Audio).',
    affects: 'Only the copy that you listen to. The board and the raw recording do not change.'},
];

const MCU_PRESETS = {detector: ['Detector band 0.5-3.5 kHz', 500, 3500], drone: ['Drone band 0.1-4 kHz', 100, 4000]};
const CH0_PRESETS = {flashed: 'As flashed', unity: 'Flashed shape, unity at DC', bypass: 'Bypass'};

/** Linear gain of the cascade of tap sets at one frequency. */
const cascade = (sets, f, rate) => sets.reduce((gain, taps) => gain * firGain(taps, f, rate), 1);
const db = (gain) => 20 * Math.log10(Math.max(gain, 1e-9));

function chart(svg, rate, curves) {
  const top = rate / 2;
  const fx = (f) => 56 + Math.log10(f / 20) / Math.log10(top / 20) * 444;
  const fy = (d) => 16 + (6 - Math.max(-80, Math.min(6, d))) / 86 * 200;
  const path = (gain) => Array.from({length: 161}, (_, i) => {
    const f = 20 * Math.pow(top / 20, i / 160);
    return `${i ? 'L' : 'M'}${fx(f).toFixed(1)} ${fy(db(gain(f))).toFixed(1)}`;
  }).join(' ');
  svg.replaceChildren(
    ...[0, -20, -40, -60, -80].flatMap((d) => [s('line', {class: 'grid', x1: 56, x2: 500, y1: fy(d), y2: fy(d)}),
      s('text', {class: 'tick', x: 48, y: fy(d) + 4, 'text-anchor': 'end'}, num(d, 0))]),
    ...[[50, '50'], [100, '100'], [300, '300'], [1000, '1k'], [3000, '3k'], [10000, '10k'], [20000, '20k']]
      .filter(([f]) => f < top).map(([f, t]) => s('text', {class: 'tick', x: fx(f), y: 236, 'text-anchor': 'middle'}, t)),
    s('text', {class: 'tick', x: 500, y: 250, 'text-anchor': 'end'}, 'Hz'),
    ...curves.filter(Boolean).map((c) => s('path', {d: path(c.gain), fill: 'none', stroke: c.stroke,
      'stroke-width': c.width ?? 1.5, 'stroke-dasharray': c.dash ?? null})),
  );
}

const legend = (items) => h('div', {class: 'chart-legend'}, ...items.flatMap(([cls, text]) => [h('span', {class: `lg-line ${cls}`}), text]));

export function filtersPanel({session}) {
  const state = {key: 'bank_band', designs: new Map(), ch0Target: 'stage1', built: null};
  const list = h('ul', {class: 'ft-list'});
  const editor = h('div', {class: 'ft-editor'});
  const el = h('div', {class: 'st-panel', role: 'tabpanel'},
    h('div', {class: 'st-panel__head'}, h('h2', {class: 'page-title'}, 'Filters'),
      h('p', {class: 'lede'}, 'Every filter in the signal chain. Select one to see its response on the board, as flashed and as proposed. Changes wait in the pending list until you apply them.')),
    list, editor);
  let view = null;

  const available = () => FILTERS.filter((f) => !f.groups || f.groups.every((g) => (dsp.byGroup.get(g) || []).some((p) => p.writable)));
  const blocks = (group) => (dsp.byGroup.get(group) || []).filter((p) => p.writable);
  const flashedTaps = (param) => param.default_words.map((w) => w / ONE);
  const proposedTaps = (param) => Array.from({length: param.words}, (_, k) => dsp.proposedWord(param.address + k) / ONE);
  const label = (def) => `Filters: ${def.title}`;

  /** The tap sets of a filter (two stages for Ch0, else the first copy): {device, flashed, proposed}. */
  function tapSets(def) {
    const firsts = def.groups.map((g) => blocks(g)[0]);
    return {device: firsts.map((p) => dsp.blockTaps(p)), flashed: firsts.map(flashedTaps), proposed: firsts.map(proposedTaps)};
  }

  /** flashed, edited, pending or mixed (the copies differ on the board). */
  function stateOf(def) {
    if (def.key === 'mcu') {
      const pending = dsp.settings.has('band_low_hz') || dsp.settings.has('band_high_hz');
      const settings = dsp.status?.settings || {};
      const edited = (settings.band_low_hz ?? BAND_LOW_HZ) !== BAND_LOW_HZ || (settings.band_high_hz ?? BAND_HIGH_HZ) !== BAND_HIGH_HZ;
      return pending ? 'pending' : edited ? 'edited' : 'flashed';
    }
    if (def.key === 'listen') return session.listenCopy.enabled ? 'on' : 'off';
    const params = def.groups.flatMap(blocks);
    const words = params.flatMap((p) => Array.from({length: p.words}, (_, k) => p.address + k));
    if (words.some((a) => dsp.pending.has(a))) return 'pending';
    if (words.every((a) => dsp.deviceWord(a) === dsp.defaultWord(a))) return 'flashed';
    for (const group of def.groups) {
      const [first, ...rest] = blocks(group);
      const reference = dsp.blockTaps(first).join(',');
      if (rest.some((p) => dsp.blockTaps(p).join(',') !== reference)) return 'mixed';
    }
    return 'edited';
  }

  function stage(def, setsFor) {
    dsp.unstage((address, entry) => entry.label === label(def));
    const writes = [];
    def.groups.forEach((group, stageIndex) => {
      for (const param of blocks(group)) {
        toWords(setsFor(stageIndex, param)).forEach((word, k) => writes.push({address: param.address + k, word}));
      }
    });
    dsp.stage(writes, label(def));
  }

  // ── FIR blocks ─────────────────────────────────────────────────────────
  function firEditor(def) {
    const values = state.designs.get(def.key) || {...def.design};
    state.designs.set(def.key, values);
    const taps = blocks(def.groups[0])[0].words;
    const copies = def.groups.reduce((n, g) => n + blocks(g).length, 0);
    const svg = s('svg', {viewBox: '0 0 520 250', class: 'chart', role: 'img', 'aria-label': `Response of ${def.title}`});
    const readouts = h('div', {class: 'readouts'});
    const problems = h('ul', {class: 'ft-problems'});
    const status = h('p', {class: 'note'});
    const typeSeg = segmented(Object.entries(TYPES), {label: 'Filter type', onPick: (key) => { values.type = key; typeSeg.set(key); update(); }});
    const number = (key, labelText) => {
      const input = h('input', {class: 'input input--sm input--mono', value: String(values[key]), inputmode: 'decimal', style: {width: '110px'},
        onInput: () => { values[key] = Number(input.value); update(); }});
      return {input, field: h('div', {class: 'field'}, h('label', {}, labelText), input)};
    };
    const low = number('low', 'Low corner (Hz)');
    const high = number('high', 'High corner (Hz)');
    const windowSelect = h('select', {class: 'select select--sm', 'aria-label': 'Window',
      onChange: () => { values.window = windowSelect.value; update(); }},
    options(Object.entries(WINDOWS).map(([key, w]) => [key, w.label]), values.window));
    const edge = h('span', {class: 'note'});
    const stageButton = h('button', {type: 'button', class: 'btn', onClick: () => stageDesign()}, 'Stage this design');
    const flashedButton = h('button', {type: 'button', class: 'btn btn--ghost',
      onClick: () => stage(def, (_, param) => flashedTaps(param))}, 'Stage flashed');
    const ch0Target = def.key === 'ch0' ? segmented([['stage1', 'Stage 1; stage 2 passes all'], ['both', 'Both stages (steeper)']],
      {label: 'Where the design goes', onPick: (key) => { state.ch0Target = key; ch0Target.set(key); update(); }}) : null;
    ch0Target?.set(state.ch0Target);
    const ch0Presets = def.key === 'ch0' ? segmented(Object.entries(CH0_PRESETS), {label: 'Ch0 presets', onPick: (key) => stagePreset(key)}) : null;
    const ch0Gain = def.key === 'ch0' ? h('p', {class: 'note'}) : null;
    typeSeg.set(values.type);

    function designed(stageIndex) {
      const set = design({...values, taps, rate: def.rate});
      if (def.key !== 'ch0' || state.ch0Target === 'both' || stageIndex === 0) return set;
      return bypassTaps(taps);
    }

    function stageDesign() {
      if (designProblems({...values, taps, rate: def.rate}).errors.length) return;
      stage(def, (stageIndex) => designed(stageIndex));
    }

    function stagePreset(key) {
      stage(def, (_, param) => {
        const flashed = flashedTaps(param);
        if (key === 'bypass') return bypassTaps(param.words);
        if (key === 'unity') { const total = flashed.reduce((a, b) => a + b, 0); return flashed.map((v) => (total ? v / total : v)); }
        return flashed;
      });
    }

    function update() {
      low.field.hidden = values.type === 'lowpass';
      high.field.hidden = values.type === 'highpass';
      const {errors, warnings} = designProblems({...values, taps, rate: def.rate});
      problems.replaceChildren(...errors.map((t) => h('li', {class: 'field__error'}, t)), ...warnings.map((t) => h('li', {class: 'is-warn'}, t)));
      stageButton.disabled = errors.length > 0;
      setText(edge, `${taps} taps at ${def.rate / 1000} kHz: the narrowest edge is about ${Math.round(transitionHz(taps, def.rate, values.window))} Hz (${WINDOWS[values.window].label}, sidelobes ${WINDOWS[values.window].sidelobe} dB).`);
      render();
    }

    function render() {
      const sets = tapSets(def);
      const {errors} = designProblems({...values, taps, rate: def.rate});
      const preview = errors.length ? null : def.groups.map((_, i) => designed(i));
      chart(svg, def.rate, [
        {gain: (f) => cascade(sets.flashed, f, def.rate), stroke: 'var(--subtle)', width: 1},
        {gain: (f) => cascade(sets.device, f, def.rate), stroke: 'var(--muted)', dash: '5 4'},
        preview ? {gain: (f) => cascade(preview, f, def.rate), stroke: 'var(--warn)', width: 1.2, dash: '2 3'} : null,
        {gain: (f) => cascade(sets.proposed, f, def.rate), stroke: 'var(--accent)', width: 2},
      ]);
      const points = def.rate === 12000 ? [100, 300, 500, 1000, 3500, 5000] : [100, 500, 1000, 3500, 6000, 10000];
      readouts.replaceChildren(...points.map((f) => h('div', {class: 'readout-item'},
        h('span', {class: 'figure-label'}, f >= 1000 ? `${f / 1000} kHz` : `${f} Hz`),
        h('span', {class: 'mono'}, `${num(db(cascade(sets.device, f, def.rate)), 1)} → ${num(db(cascade(sets.proposed, f, def.rate)), 1)} dB`))));
      const now = stateOf(def);
      setText(status, `${copies} ${copies === 1 ? 'block' : 'blocks'} of ${taps} taps. On the board: ${{flashed: 'as flashed', edited: 'edited',
        pending: 'edited, with pending changes', mixed: 'the copies differ from each other'}[now]}.`);
      if (ch0Gain) {
        const at = (sets) => db(cascade(sets, 1000, def.rate));
        const change = at(sets.proposed) - at(sets.device);
        setText(ch0Gain, `Ch0 level at 1 kHz: ${num(at(sets.device), 1)} dB on the board, ${num(at(sets.proposed), 1)} dB proposed${Math.abs(change) >= 3 ? ` (${change > 0 ? '+' : ''}${num(change, 1)} dB: ${change > 0 ? 'loud sounds can clip' : 'quieter recordings'})` : ''}.`);
      }
    }

    const el = h('section', {class: 'ft-section'},
      h('h3', {class: 'st-mic__title'}, def.title),
      h('p', {class: 'note note--body'}, `${def.where} It affects: ${def.affects}`),
      def.warning ? h('p', {class: 'ft-warning'}, def.warning) : null,
      h('figure', {class: 'st-figure'}, svg,
        legend([['lg-line--flashed', 'As flashed'], ['lg-line--device', 'On the board now'], ['lg-line--design', 'This design'], ['lg-line--proposed', 'Proposed (after Apply)']])),
      readouts,
      ch0Presets ? h('div', {class: 'toolbar'}, h('span', {class: 'note'}, 'Presets'), ch0Presets.el) : null,
      ch0Gain,
      h('div', {class: 'section'},
        h('h4', {class: 'section__title'}, 'Design'),
        h('div', {class: 'toolbar'}, typeSeg.el, ch0Target?.el),
        h('div', {class: 'form-row ft-form'}, low.field, high.field,
          h('div', {class: 'field'}, h('label', {}, 'Window'), windowSelect)),
        edge, problems,
        h('div', {class: 'toolbar toolbar--tight'}, stageButton, flashedButton),
        h('p', {class: 'note'}, `Stage this design writes it to all ${copies} ${copies === 1 ? 'block' : 'blocks'}. The taps are symmetric, so their order in the block does not matter.`)),
      status);
    update();
    return {el, render};
  }

  // ── MCU window band (firmware settings) ────────────────────────────────
  function mcuEditor(def) {
    const svg = s('svg', {viewBox: '0 0 520 250', class: 'chart', role: 'img', 'aria-label': 'Response of the MCU window band'});
    const lowInput = h('input', {class: 'input input--sm input--mono', inputmode: 'numeric', style: {width: '110px'}, onInput: () => validate()});
    const highInput = h('input', {class: 'input input--sm input--mono', inputmode: 'numeric', style: {width: '110px'}, onInput: () => validate()});
    const error = h('span', {class: 'field__error', role: 'alert'});
    const stageButton = h('button', {type: 'button', class: 'btn', onClick: () => stageBand(Number(lowInput.value), Number(highInput.value))}, 'Stage this band');
    const presets = segmented(Object.entries(MCU_PRESETS).map(([key, [text]]) => [key, text]), {label: 'Band presets',
      onPick: (key) => { const [, lo, hi] = MCU_PRESETS[key]; lowInput.value = lo; highInput.value = hi; stageBand(lo, hi); }});
    const readouts = h('div', {class: 'readouts'});
    const current = () => ({low: dsp.status?.settings?.band_low_hz ?? BAND_LOW_HZ, high: dsp.status?.settings?.band_high_hz ?? BAND_HIGH_HZ});
    const proposed = () => ({low: dsp.proposedSetting('band_low_hz', BAND_LOW_HZ), high: dsp.proposedSetting('band_high_hz', BAND_HIGH_HZ)});

    function problem(lo, hi) {
      if (!(Number.isInteger(lo) && lo >= 100 && lo <= 5000)) return 'The low corner is 100 to 5000 Hz.';
      if (!(Number.isInteger(hi) && hi >= 500 && hi <= 12000)) return 'The high corner is 500 to 12000 Hz.';
      if (!(lo < hi)) return 'The low corner must be below the high corner.';
      return '';
    }

    function validate() {
      const text = problem(Number(lowInput.value), Number(highInput.value));
      setText(error, text);
      stageButton.disabled = Boolean(text);
    }

    function stageBand(lo, hi) {
      if (problem(lo, hi)) return;
      dsp.stageSetting('band_low_hz', lo, 'MCU window band');
      dsp.stageSetting('band_high_hz', hi, 'MCU window band');
    }

    function render() {
      const now = current();
      const next = proposed();
      if (document.activeElement !== lowInput && document.activeElement !== highInput) {
        lowInput.value = next.low;
        highInput.value = next.high;
        validate();
      }
      chart(svg, def.rate, [
        {gain: (f) => bandGain(f, def.rate, BAND_LOW_HZ, BAND_HIGH_HZ), stroke: 'var(--subtle)', width: 1},
        {gain: (f) => bandGain(f, def.rate, now.low, now.high), stroke: 'var(--muted)', dash: '5 4'},
        {gain: (f) => bandGain(f, def.rate, next.low, next.high), stroke: 'var(--accent)', width: 2},
      ]);
      readouts.replaceChildren(...[100, 300, 1000, 3500, 6000].map((f) => h('div', {class: 'readout-item'},
        h('span', {class: 'figure-label'}, f >= 1000 ? `${f / 1000} kHz` : `${f} Hz`),
        h('span', {class: 'mono'}, `${num(db(bandGain(f, def.rate, now.low, now.high)), 1)} → ${num(db(bandGain(f, def.rate, next.low, next.high)), 1)} dB`))));
    }

    const el = h('section', {class: 'ft-section'},
      h('h3', {class: 'st-mic__title'}, def.title),
      h('p', {class: 'note note--body'}, `${def.where} It affects: ${def.affects}`),
      h('p', {class: 'ft-warning'}, 'The band sets what a scan and the tests measure. A test run with Flashed uses 0.5-3.5 kHz; a run with Current edits uses this band. A reset of the board puts back 0.5-3.5 kHz.'),
      h('figure', {class: 'st-figure'}, svg, legend([['lg-line--flashed', 'As flashed (0.5-3.5 kHz)'], ['lg-line--device', 'On the board now'], ['lg-line--proposed', 'Proposed']])),
      readouts,
      h('div', {class: 'toolbar'}, h('span', {class: 'note'}, 'Presets'), presets.el),
      h('div', {class: 'form-row ft-form'},
        h('div', {class: 'field'}, h('label', {}, 'High-pass corner (Hz)'), lowInput),
        h('div', {class: 'field'}, h('label', {}, 'Low-pass corner (Hz)'), highInput), stageButton),
      error);
    return {el, render};
  }

  // ── Listening copy (this browser) ──────────────────────────────────────
  function listenEditor(def) {
    const svg = s('svg', {viewBox: '0 0 520 250', class: 'chart', role: 'img', 'aria-label': 'Response of the listening copy'});
    const box = h('input', {type: 'checkbox', onChange: () => session.setListenCopy({enabled: box.checked})});
    const presets = segmented(Object.entries(LISTEN_PRESETS), {label: 'Filter of the copy', onPick: (key) => session.setListenCopy({preset: key})});
    const lowInput = h('input', {class: 'input input--sm input--mono', inputmode: 'numeric', style: {width: '110px'}, onChange: () => setCorners()});
    const highInput = h('input', {class: 'input input--sm input--mono', inputmode: 'numeric', style: {width: '110px'}, onChange: () => setCorners()});
    const custom = h('div', {class: 'form-row ft-form'},
      h('div', {class: 'field'}, h('label', {}, 'High-pass corner (Hz)'), lowInput),
      h('div', {class: 'field'}, h('label', {}, 'Low-pass corner (Hz)'), highInput));
    const error = h('span', {class: 'field__error', role: 'alert'});
    const note = h('p', {class: 'note'});

    function setCorners() {
      const lo = Number(lowInput.value);
      const hi = Number(highInput.value);
      const bad = !(lo >= 20 && hi <= 20000 && lo < hi);
      setText(error, bad ? 'The corners are 20 Hz to 20 kHz, the low one below the high one.' : '');
      if (!bad) session.setListenCopy({low: lo, high: hi});
    }

    function filter() {
      const {preset, low, high} = session.listenCopy;
      if (preset === 'bank') { const taps = dsp.bankListenTaps(); return taps ? {kind: 'bank', ...taps} : null; }
      if (preset === 'drone') return {kind: 'band', low: 100, high: 4000};
      if (preset === 'custom') return {kind: 'band', low, high};
      return {kind: 'band', low: dsp.status?.settings?.band_low_hz ?? BAND_LOW_HZ, high: dsp.status?.settings?.band_high_hz ?? BAND_HIGH_HZ};
    }

    function render() {
      const copy = session.listenCopy;
      box.checked = copy.enabled;
      presets.set(copy.preset);
      custom.hidden = copy.preset !== 'custom';
      if (document.activeElement !== lowInput && document.activeElement !== highInput) {
        lowInput.value = copy.low;
        highInput.value = copy.high;
      }
      const chosen = filter();
      const bank = dsp.bankListenTaps();
      chart(svg, def.rate, [
        {gain: (f) => bandGain(f, def.rate), stroke: 'var(--subtle)', width: 1},
        bank ? {gain: (f) => listenGain({kind: 'bank', ...bank}, f, def.rate), stroke: 'var(--muted)', dash: '5 4'} : null,
        chosen ? {gain: (f) => listenGain(chosen, f, def.rate), stroke: 'var(--accent)', width: 2} : null,
      ]);
      setText(note, copy.preset === 'bank'
        ? 'Through the bank\'s anti-alias low-pass and its 12 kHz band-pass, as on the board now: this is what the Live detectors hear. Compared with the MCU window band, it lets through more sound below 300 Hz and much less above 4 kHz.'
        : copy.preset === 'mcu' ? 'The band of the MCU window, as on the board now: what scans and the tests measure.'
          : 'A fourth-order high-pass and a second-order low-pass, the same kind of filter as the MCU window.');
    }

    const el = h('section', {class: 'ft-section'},
      h('h3', {class: 'st-mic__title'}, def.title),
      h('p', {class: 'note note--body'}, `${def.where} It affects: ${def.affects}`),
      h('label', {class: 'check'}, box, 'Save the copy with each recording'),
      presets.el, custom, error,
      h('figure', {class: 'st-figure'}, svg, legend([['lg-line--flashed', 'MCU window band, 0.5-3.5 kHz'], ['lg-line--device', 'Live detectors (bank filters)'], ['lg-line--proposed', 'The copy']])),
      note);
    return {el, render};
  }

  function select(key) {
    state.key = key;
    state.built = null;
    render();
  }

  function render() {
    if (!dsp.registry) return;
    const defs = available();
    if (!defs.some((def) => def.key === state.key)) state.key = defs[0]?.key;
    list.replaceChildren(...defs.map((def) => {
      const now = stateOf(def);
      return h('li', {}, h('button', {type: 'button', class: `ft-item${def.key === state.key ? ' is-open' : ''}`, 'aria-current': def.key === state.key ? 'true' : null,
        onClick: () => select(def.key)},
      h('span', {class: 'ft-item__title'}, def.title),
      h('span', {class: `chip ${{pending: 'chip--accent', edited: 'chip--warn', mixed: 'chip--danger', on: 'chip--accent'}[now] || ''}`},
        {flashed: 'as flashed', edited: 'edited', pending: 'pending', mixed: 'copies differ', on: 'saved with recordings', off: 'off'}[now])));
    }));
    const def = defs.find((d) => d.key === state.key);
    if (!def) { editor.replaceChildren(h('p', {class: 'note'}, 'This DSP program has no filters that Study can change.')); return; }
    if (state.built !== def.key) {
      view = def.key === 'mcu' ? mcuEditor(def) : def.key === 'listen' ? listenEditor(def) : firEditor(def);
      editor.replaceChildren(view.el);
      state.built = def.key;
    }
    view.render();
  }

  session.addEventListener('listen', () => { if (!el.hidden) render(); });
  return {el, render, select};
}
