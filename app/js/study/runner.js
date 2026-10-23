/**
 * runner.js — Study · Measurements: the tests of the characterisation plan, run by the backend
 * (study_runner.py) one at a time. Pick a test, read the plan's text, change its options, choose
 * Flashed or Current edits, then Run. The scripts' questions come up here; Stop runs their restore
 * steps. Each result folder shows its checks, figures and log.
 */

import { h, segmented, setText } from '../shared/dom.js';
import { API_BASE, apiJson } from '../shared/api.js';
import { dsp } from './dsp.js';
import { STATE_TEXT, answerError, commandLine, countsText, durationText, formErrors, inlineParts, isActive,
  latestResult, lineKind, optionDefaults, optionLabel, requestOptions } from './runlog.js';

const POLL_ACTIVE_MS = 700;
const POLL_IDLE_MS = 3000;
const POLL_TIMEOUT_MS = 10000;
const PREFS_KEY = 'heimdall.study.tests';

const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((kid) => kid !== null && kid !== undefined && kid !== false));
const fileUrl = (path) => `${API_BASE}/study/results/file?path=${encodeURIComponent(path)}`;
const timed = (promise) => Promise.race([promise,
  new Promise((_, reject) => setTimeout(() => reject(new Error('no answer from the backend')), POLL_TIMEOUT_MS))]);

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch (_) { return {}; }
}

function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* storage unavailable */ }
}

function renderParts(parts) {
  return parts.map((part) => (part.kind === 'bold' ? h('strong', {}, renderParts(part.parts))
    : part.kind === 'code' ? h('code', {}, part.text) : part.text));
}

const inline = (text) => renderParts(inlineParts(text));

/** The plan's paragraphs, lists and tables (catalogue.py blocks). */
function planBlocks(blocks) {
  return blocks.map((block) => {
    if (block.type === 'p') return h('p', {}, inline(block.text));
    if (block.type === 'ol' || block.type === 'ul') return h(block.type, {}, block.items.map((item) => h('li', {}, inline(item))));
    if (block.type === 'table' && block.rows.length) {
      const [head, ...rows] = block.rows;
      return h('table', {class: 'rt-table'},
        h('thead', {}, h('tr', {}, head.map((cell) => h('th', {}, inline(cell))))),
        h('tbody', {}, rows.map((row) => h('tr', {}, row.map((cell) => h('td', {}, inline(cell)))))));
    }
    return null;
  });
}

function checksTable(checks) {
  if (!checks?.length) return h('p', {class: 'note'}, 'No checks in summary.json.');
  return h('table', {class: 'rt-table rt-checks'},
    h('thead', {}, h('tr', {}, h('th', {}, 'Result'), h('th', {}, 'Check'), h('th', {}, 'Detail'))),
    h('tbody', {}, checks.map((check) => h('tr', {class: `is-${check.result}`},
      h('td', {}, h('span', {class: `chip ${{pass: 'chip--accent', fail: 'chip--danger', info: ''}[check.result] ?? ''}`},
        String(check.result || '').toUpperCase())),
      h('td', {}, check.name), h('td', {class: 'rt-checks__detail'}, check.detail)))));
}

function figures(path, names) {
  if (!names?.length) return null;
  return h('div', {class: 'rt-figures'}, names.map((name) => h('a', {href: fileUrl(`${path}/${name}`), target: '_blank', rel: 'noopener'},
    h('img', {src: fileUrl(`${path}/${name}`), alt: name, loading: 'lazy'}), h('span', {class: 'note'}, name))));
}

export function measurementsScreen({session}) {
  const prefs = {configuration: 'flashed', selected: null, ...loadPrefs()};
  const state = {
    catalogue: null, error: '', results: [], status: {state: 'idle', run: null, prompt: null},
    runId: null, next: 0, follow: true, detail: null, viewing: null, message: '', visible: false,
    timer: null, polling: false, promptKey: '', loading: false,
  };
  const forms = new Map();

  // ── Left: the tests ───────────────────────────────────────────────────
  const list = h('ul', {class: 'templates'});
  const left = h('aside', {class: 'st-browser', 'aria-labelledby': 'rt-tests-h'},
    h('h2', {class: 'section__title', id: 'rt-tests-h'}, 'Tests'), list,
    h('p', {class: 'note'}, 'In the order of the characterisation plan (MAX78002/docs/CHARACTERISATION_PLAN.md). '
      + 'Each run writes a folder in MAX78002/docs/data/bench_<date>/.'));

  // ── Centre: the selected test, or a result ────────────────────────────
  const body = h('div', {class: 'st-panel'});
  const centre = h('section', {class: 'st-editor', 'aria-label': 'Test'}, body);

  const runTitle = h('span', {class: 'rt-run__title'});
  const runMeta = h('span', {class: 'note'});
  const stopButton = h('button', {type: 'button', class: 'btn btn--sm', onClick: () => stop(false)},
    h('span', {class: 'btn__stop', 'aria-hidden': 'true'}), 'Stop');
  const forceButton = h('button', {type: 'button', class: 'btn btn--sm btn--ghost', hidden: true, onClick: () => stop(true),
    title: 'End the test now. Its restore steps do not run; press RESET on the board if the settings look wrong.'}, 'Force stop');
  const promptBox = h('div', {class: 'rt-prompt', hidden: true, 'aria-live': 'polite'});
  const logEl = h('pre', {class: 'rt-log', tabindex: '0', 'aria-label': 'Test output',
    onScroll: () => { state.follow = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 12; }});
  const resultBox = h('div', {class: 'rt-result'});
  const runPanel = h('section', {class: 'rt-run', 'aria-label': 'Test run'},
    h('div', {class: 'rt-run__head'}, h('div', {class: 'rt-run__text'}, runTitle, runMeta),
      h('div', {class: 'toolbar toolbar--tight'}, stopButton, forceButton)),
    promptBox, logEl, resultBox);

  // ── Right: result folders ─────────────────────────────────────────────
  const resultList = h('ul', {class: 'runs'});
  const right = h('aside', {class: 'st-changes', 'aria-label': 'Results'},
    h('section', {class: 'section'},
      h('div', {class: 'section__row'}, h('h2', {class: 'section__title'}, 'Results'),
        h('button', {type: 'button', class: 'btn btn--sm btn--quiet', onClick: () => loadResults()}, 'Refresh')),
      resultList,
      h('p', {class: 'note'}, 'Newest first. Each folder holds summary.json, captures.csv, the figures, run.json and run_log.txt.')));

  const el = h('div', {class: 'st-params st-tests'}, left, centre, right);

  // Elements of the test view that change without a rebuild.
  const view = {preview: null, runButton: null, why: null, errors: new Map(), configNote: null, configSeg: null};

  // ── Data ──────────────────────────────────────────────────────────────
  const entries = () => state.catalogue?.entries || [];
  const entryById = (id) => entries().find((entry) => entry.id === id) || null;
  const selected = () => entryById(prefs.selected) || entries()[0] || null;

  function formFor(entry) {
    if (!forms.has(entry.id)) forms.set(entry.id, optionDefaults(entry));
    return forms.get(entry.id);
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      state.catalogue = await apiJson('/study/tests');
      state.error = '';
    } catch (error) {
      state.error = error.message;
    } finally {
      state.loading = false;
    }
    renderList();
    renderCentre();
  }

  async function loadResults() {
    try {
      state.results = (await apiJson('/study/results')).results;
    } catch (_) { /* the list stays as it was */ }
    renderList();
    renderResults();
  }

  // ── Polling the run ───────────────────────────────────────────────────
  function schedule(delay) {
    clearTimeout(state.timer);
    if (state.visible) state.timer = setTimeout(poll, delay);
  }

  async function poll() {
    if (state.polling) return;
    state.polling = true;
    const before = state.status;
    try {
      let status = await timed(apiJson(`/study/run?since=${state.next}`));
      const changedRun = (status.run?.id ?? null) !== state.runId;
      if (changedRun) {
        state.runId = status.run?.id ?? null;
        state.next = 0;
        state.follow = true;
        logEl.replaceChildren();
        resultBox.replaceChildren();
        delete resultBox.dataset.key;
        state.detail = null;
        if (status.run) status = await timed(apiJson('/study/run?since=0'));
      }
      state.status = status;
      appendLines(status.lines);
      state.next = status.next;
      const wasActive = isActive(before.state);
      const nowActive = isActive(status.state);
      if (wasActive !== nowActive || changedRun) {
        if (!nowActive && status.run) await loadDetail(status.run.folder);
        renderCentre();
        renderList();
        if (wasActive !== nowActive) loadResults();
      }
      updateRun();
      updateForm();
      if (!state.catalogue) load();
    } catch (error) {
      // The backend is away: keep the last state; the next poll tries again.
      console.warn('Study runner poll:', error);
    } finally {
      state.polling = false;
      schedule(isActive(state.status.state) ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    }
  }

  function appendLines(lines) {
    if (!lines?.length) return;
    logEl.append(...lines.map((line) => h('span', {class: `rt-line is-${lineKind(line)}`},
      {ask: '? ', answer: '> ', system: ''}[line.kind] ?? '', line.text, '\n')));
    if (state.follow) logEl.scrollTop = logEl.scrollHeight;
  }

  async function loadDetail(path) {
    try {
      state.detail = {path, data: await apiJson(`/study/results/item?path=${encodeURIComponent(path)}`)};
    } catch (_) {
      state.detail = null;
    }
  }

  // ── Actions ───────────────────────────────────────────────────────────
  function select(id) {
    prefs.selected = id;
    savePrefs(prefs);
    state.viewing = null;
    renderList();
    renderCentre();
  }

  async function run() {
    const entry = selected();
    if (!entry) return;
    state.message = '';
    view.runButton.disabled = true;
    try {
      const status = await apiJson('/study/run', {method: 'POST', body: {
        entry: entry.id, options: requestOptions(entry, formFor(entry)), configuration: prefs.configuration}});
      state.status = status;
      state.runId = null;
      state.next = 0;
    } catch (error) {
      state.message = error.message;
    }
    await poll();
    renderCentre();
  }

  async function stop(force) {
    try {
      state.status = await apiJson('/study/run/stop', {method: 'POST', body: {force}});
    } catch (error) {
      state.message = error.message;
    }
    updateRun();
    schedule(0);
  }

  async function answer(text) {
    try {
      await apiJson('/study/run/answer', {method: 'POST', body: {text}});
      state.status = {...state.status, prompt: null};
      state.promptKey = '';
    } catch (error) {
      state.message = error.message;
    }
    updateRun();
    schedule(0);
  }

  async function speakerCheck(button) {
    button.disabled = true;
    try {
      await apiJson('/study/speaker_check', {method: 'POST'});
      state.message = 'Sound A plays for 2 s on the Windows default output. You must hear it from the test speaker.';
    } catch (error) {
      state.message = error.message;
    }
    setTimeout(() => { button.disabled = false; }, 2000);
    updateForm();
  }

  async function showResult(path) {
    state.viewing = path;
    renderResults();
    await loadDetail(path);
    renderCentre();
  }

  // ── Rendering ─────────────────────────────────────────────────────────
  function renderList() {
    if (state.error) { put(list, h('li', {class: 'note'}, `The test list is not available: ${state.error}`)); return; }
    if (!state.catalogue) { put(list, h('li', {class: 'note'}, 'Loading the test list…')); return; }
    const current = selected();
    const running = isActive(state.status.state) ? state.status.run?.entry : null;
    put(list, entries().map((entry) => {
      const last = latestResult(state.results, entry.id);
      const counts = last ? countsText(last.counts) : '';
      return h('li', {}, h('button', {type: 'button', class: `template${entry.id === current?.id && !state.viewing ? ' is-open' : ''}`,
        'aria-current': entry.id === current?.id ? 'true' : null, onClick: () => select(entry.id)},
      h('span', {class: 'template__name'}, entry.section ? h('span', {class: 'rt-section'}, entry.section) : null, entry.title),
      h('span', {class: 'template__meta note'}, [
        entry.minutes ? `${entry.minutes} min` : null,
        entry.speaker ? 'speaker' : (entry.note || 'no sound'),
      ].filter(Boolean).join(' · ')),
      running === entry.id ? h('span', {class: 'chip chip--accent'}, 'Running')
        : last ? h('span', {class: `chip ${last.counts?.fail ? 'chip--danger' : last.state === 'done' ? '' : 'chip--warn'}`},
          `${last.day} · ${counts || last.state || 'result'}`) : null));
    }));
  }

  function renderResults() {
    if (!state.results.length) { put(resultList, h('li', {class: 'note'}, 'No result folders yet.')); return; }
    put(resultList, state.results.slice(0, 80).map((result) => h('li', {class: `runs__item${state.viewing === result.path ? ' is-open' : ''}`},
      h('button', {type: 'button', class: 'rt-result-link', onClick: () => showResult(result.path)},
        h('span', {class: 'runs__title'}, result.path),
        h('span', {class: 'note'}, [result.title || result.test, result.configuration === 'edits' ? 'with Study edits' : null,
          result.state && result.state !== 'done' ? result.state : null, countsText(result.counts)].filter(Boolean).join(' · '))))));
  }

  function leadText(entry) {
    const parts = [];
    if (entry.minutes) parts.push(`About ${entry.minutes} minutes.`);
    parts.push(entry.speaker ? 'The test plays sounds on the Windows default output.' : 'The speaker stays silent.');
    return parts.join(' ');
  }

  function field(entry, option, values, disabled) {
    const id = `rt-${entry.id}-${option.name}`;
    const error = h('span', {class: 'field__error'});
    view.errors.set(option.name, error);
    const changed = (value) => { values[option.name] = value; updateForm(); };
    if (option.kind === 'flag') {
      return h('div', {class: 'field'},
        h('label', {class: 'check'}, h('input', {type: 'checkbox', id, checked: values[option.name] === true, disabled,
          onChange: (event) => changed(event.target.checked)}), optionLabel(option.name)),
        option.help ? h('span', {class: 'field__hint'}, option.help) : null, error);
    }
    const input = option.kind === 'choice'
      ? h('select', {class: 'select select--sm', id, disabled, onChange: (event) => changed(event.target.value)},
        option.choices.map((choice) => h('option', {value: choice, selected: choice === values[option.name]}, choice)))
      : h('input', {class: `input input--sm${option.kind === 'text' ? ' input--mono' : ''}`, id, disabled,
        value: values[option.name], inputmode: option.kind === 'text' ? null : 'decimal', spellcheck: 'false',
        placeholder: option.required ? 'required' : (option.script_default === null ? 'script default' : ''),
        onInput: (event) => changed(event.target.value)});
    return h('div', {class: 'field'}, h('label', {for: id}, optionLabel(option.name), h('span', {class: 'note'}, `  ${option.flag}`)),
      input, option.help ? h('span', {class: 'field__hint'}, option.help) : null, error);
  }

  function configurationNote() {
    const words = dsp.status?.modified_words ?? 0;
    if (prefs.configuration === 'edits') {
      return words
        ? `The test uses your ${words} edited DSP ${words === 1 ? 'word' : 'words'} and the current firmware settings. It writes the edits again after each boot check (I), which puts back the flashed taper and tables.`
        : 'No DSP word differs from the flashed program now, so this is the same as Flashed, with the current firmware settings.';
    }
    return `The test uses the flashed DSP program and the default firmware settings.${words ? ` Your ${words} edited ${words === 1 ? 'word comes' : 'words come'} back after the test.` : ''}`;
  }

  function renderTest(entry) {
    view.errors = new Map();
    const status = state.status;
    const active = isActive(status.state);
    const mine = Boolean(status.run && status.run.entry === entry.id);
    const values = formFor(entry);
    const locked = active;

    view.configSeg = segmented([['flashed', 'Flashed'], ['edits', 'Current edits']], {label: 'DSP configuration for the run',
      onPick: (key) => { prefs.configuration = key; savePrefs(prefs); view.configSeg.set(key); updateForm(); }});
    view.configSeg.set(prefs.configuration);
    view.configSeg.setDisabled(locked);
    view.configNote = h('p', {class: 'note'});
    view.preview = h('code', {class: 'rt-command'});
    view.why = h('span', {class: 'note', role: 'status'});
    view.runButton = h('button', {type: 'button', class: 'btn btn--primary', onClick: () => run()}, 'Run');
    const checkButton = entry.speaker ? h('button', {type: 'button', class: 'btn', disabled: locked,
      title: 'Plays 2 s of sound A on the Windows default output', onClick: (event) => speakerCheck(event.currentTarget)}, 'Play check sound') : null;

    const otherRun = active && !mine ? h('div', {class: 'rt-notice'},
      h('span', {}, `Running now: ${status.run.section ? status.run.section + ' ' : ''}${status.run.title}.`),
      h('button', {type: 'button', class: 'btn btn--sm', onClick: () => select(status.run.entry)}, 'Show it')) : null;

    put(body,
      h('header', {class: 'st-panel__head'},
        h('h1', {class: 'page-title'}, entry.section ? h('span', {class: 'rt-section'}, entry.section) : null, entry.title),
        h('p', {class: 'lede'}, leadText(entry))),
      otherRun,
      mine ? runPanel : null,
      h('details', {class: 'disclosure rt-plan', open: !(mine && active)},
        h('summary', {}, entry.section ? `Characterisation plan, section ${entry.section}` : 'About this test'),
        h('div', {class: 'rt-plan__body'}, planBlocks(entry.text))),
      h('section', {class: 'section', 'aria-labelledby': 'rt-options-h'},
        h('div', {class: 'section__row'}, h('h2', {class: 'section__title', id: 'rt-options-h'}, 'Options'),
          h('button', {type: 'button', class: 'btn btn--sm btn--quiet', disabled: locked,
            onClick: () => { forms.set(entry.id, optionDefaults(entry)); renderCentre(); }}, 'Plan values')),
        entry.options.length ? h('div', {class: 'field-grid'}, entry.options.map((option) => field(entry, option, values, locked)))
          : h('p', {class: 'note'}, 'This test has no options.')),
      h('section', {class: 'section', 'aria-labelledby': 'rt-config-h'},
        h('h2', {class: 'section__title', id: 'rt-config-h'}, 'DSP configuration'), view.configSeg.el, view.configNote),
      h('section', {class: 'section'},
        h('div', {class: 'toolbar'}, view.runButton, checkButton, view.why),
        h('p', {class: 'rt-command__line'}, view.preview)));
    updateForm();
    if (mine) updateRun();
  }

  function renderResultView() {
    const detail = state.detail?.path === state.viewing ? state.detail.data : null;
    if (!detail) { put(body, h('p', {class: 'note'}, 'Loading the result…')); return; }
    const run = detail.run || {};
    const summary = detail.summary || {};
    const settings = summary.settings ? Object.entries(summary.settings).map(([key, value]) => `${key} ${value}`).join(', ') : '';
    const edits = Object.keys(summary.dsp_edits || {}).length;
    put(body,
      h('header', {class: 'st-panel__head'},
        h('div', {class: 'toolbar toolbar--tight'}, h('button', {type: 'button', class: 'btn btn--sm btn--quiet',
          onClick: () => { state.viewing = null; renderResults(); renderList(); renderCentre(); }}, '← Back to the test')),
        h('h1', {class: 'page-title'}, detail.path),
        h('p', {class: 'lede'}, [detail.title || summary.test, detail.started ? `started ${detail.started}` : null,
          run.finished ? `finished ${run.finished}` : null, detail.state].filter(Boolean).join(' · '))),
      h('dl', {class: 'raw-info'},
        run.command ? [h('dt', {}, 'Command'), h('dd', {class: 'mono'}, run.command)] : null,
        [h('dt', {}, 'DSP'), h('dd', {}, run.configuration === 'edits' ? `Current edits (${edits} words)`
          : run.configuration === 'flashed' ? 'Flashed' : (edits ? `${edits} edited words` : 'Not recorded'))],
        settings ? [h('dt', {}, 'Settings'), h('dd', {class: 'mono'}, settings)] : null,
        summary.map_hash ? [h('dt', {}, 'Map hash'), h('dd', {class: 'mono'}, summary.map_hash)] : null),
      h('section', {class: 'section'}, h('h2', {class: 'section__title'}, `Checks${detail.counts ? ` · ${countsText(detail.counts)}` : ''}`),
        checksTable(summary.checks)),
      detail.figures?.length ? h('section', {class: 'section'}, h('h2', {class: 'section__title'}, 'Figures'), figures(detail.path, detail.figures)) : null,
      detail.log ? h('details', {class: 'disclosure'}, h('summary', {}, 'Log'), h('pre', {class: 'rt-log rt-log--static'}, detail.log)) : null,
      h('p', {class: 'note'}, `Files: ${(detail.files || []).join(', ')}`));
  }

  function renderCentre() {
    if (state.viewing) { renderResultView(); return; }
    if (state.error) {
      put(body, h('div', {class: 'placeholder'}, h('h1', {}, 'The test list could not be loaded'),
        h('p', {}, `${state.error}. The backend reads it from scripts/catalogue.py; restart sim_server.py after an update.`)));
      return;
    }
    const entry = selected();
    if (!entry) { put(body, h('p', {class: 'note'}, 'Loading…')); return; }
    renderTest(entry);
  }

  /** The run panel: state, question, log and, at the end, the checks and figures. */
  function updateRun() {
    const status = state.status;
    const run = status.run;
    if (!run) return;
    const active = isActive(status.state);
    const started = run.started ? new Date(run.started) : null;
    const end = run.finished ? new Date(run.finished) : new Date();
    setText(runTitle, `${STATE_TEXT[status.state] || status.state}${started ? ` · ${durationText(end - started)}` : ''}`);
    setText(runMeta, `${run.folder} · ${run.configuration === 'edits' ? `current edits (${run.edits} words)` : 'flashed'}`);
    runPanel.dataset.state = status.state;
    stopButton.hidden = !active;
    stopButton.disabled = status.state !== 'running';
    forceButton.hidden = status.state !== 'stopping';
    renderPrompt(status.prompt, active);
    const key = !active && state.detail?.path === run.folder ? `${run.id}:${run.folder}` : '';
    if (key === (resultBox.dataset.key || '')) return;
    resultBox.dataset.key = key;
    if (key) {
      const detail = state.detail.data;
      put(resultBox,
        h('div', {class: 'section__row'}, h('h2', {class: 'section__title'}, `Checks${detail.counts ? ` · ${countsText(detail.counts)}` : ''}`),
          h('button', {type: 'button', class: 'btn btn--sm btn--quiet', onClick: () => showResult(run.folder)}, 'Open the result')),
        checksTable(detail.summary?.checks), figures(detail.path, detail.figures));
    } else {
      resultBox.replaceChildren();
    }
  }

  function renderPrompt(prompt, active) {
    const key = prompt ? `${state.runId}:${prompt.text}` : '';
    if (!prompt || !active) {
      promptBox.hidden = true;
      promptBox.replaceChildren();
      state.promptKey = '';
      return;
    }
    if (key === state.promptKey) return;
    state.promptKey = key;
    promptBox.hidden = false;
    const error = h('span', {class: 'field__error'});
    const input = prompt.numbers ? h('input', {class: 'input input--mono', spellcheck: 'false', inputmode: 'decimal',
      'aria-label': 'Answer', placeholder: prompt.numbers === 1 ? 'one number' : `${prompt.numbers} numbers, for example 72,5 150 0`,
      onKeydown: (event) => { if (event.key === 'Enter') submit(); }}) : null;
    const submit = (text = input ? input.value : '') => {
      const problem = answerError(prompt, text);
      setText(error, problem || '');
      if (!problem) answer(text.trim());
    };
    const go = h('button', {type: 'button', class: 'btn btn--primary', onClick: () => submit()}, prompt.numbers ? 'Send' : 'Continue');
    put(promptBox,
      h('p', {class: 'rt-prompt__text'}, prompt.text),
      h('div', {class: 'toolbar toolbar--tight'}, input, go,
        prompt.optional ? h('button', {type: 'button', class: 'btn', onClick: () => submit('')}, 'As given (no numbers)') : null),
      error);
    (input || go).focus({preventScroll: true});
  }

  /** Command preview, field errors and why Run is off. */
  function updateForm() {
    const entry = selected();
    if (!entry || !view.runButton || state.viewing) return;
    const values = formFor(entry);
    const errors = formErrors(entry, values);
    for (const [name, element] of view.errors) setText(element, errors[name] || '');
    const day = new Date().toLocaleDateString('sv-SE');
    setText(view.preview, commandLine(entry, values, `MAX78002/docs/data/bench_${day}/${entry.id}`));
    setText(view.configNote, configurationNote());
    const active = isActive(state.status.state);
    let why = state.message;
    if (active) why = 'A test is running. Wait for it to end, or stop it.';
    else if (!session.open) why = 'Connect the board on the Hardware screen first.';
    else if (Object.keys(errors).length) why = 'Correct the marked options.';
    view.runButton.disabled = active || !session.open || Object.keys(errors).length > 0;
    setText(view.why, why || '');
  }

  function render() {
    if (!state.catalogue) load();
    renderList();
    renderResults();
    if (!state.catalogue) renderCentre();
    updateForm();
  }

  dsp.addEventListener('change', () => { if (state.visible) updateForm(); });
  session.addEventListener('link', () => { if (state.visible) updateForm(); });

  return {
    el,
    render,
    setVisible(visible) {
      if (visible === state.visible) return;
      state.visible = visible;
      if (visible) {
        render();
        loadResults();
        schedule(0);
      } else {
        clearTimeout(state.timer);
      }
    },
  };
}
