/**
 * view.js — the Hardware screen. Shows the connection panel until a link is
 * open, then the console: Live / Stop and the diagnostic scans on top, the
 * log rail on the left, the Field (3D or map) with its drawer in the middle,
 * and Source, Audio and the last 30 s on the right.
 */

import { h, options, segmented, setText } from '../shared/dom.js';
import { rampGradient } from '../shared/colormap.js';
import { deg, num } from '../shared/format.js';
import { FieldMap } from './map.js';
import { HistoryCharts } from './history.js';
import { AudioPanel, DiagnosticsPanel, EventLog, ManualBeamPanel, MeasurementPanel, SourcePanel, TonePanel, operatingMode, sourceAnswer } from './status.js';
import { AcousticScene, AuditionPanel, ConnectionPanel } from './connection.js';
import { buildField, fieldKind } from './field.js';
import { STALE_MS } from './session.js';
import { dsp } from '../study/dsp.js';

const PREFS_KEY = 'heimdall.hardware.view';
const FLOORS = [['-60', '−60 dBFS'], ['-50', '−50 dBFS'], ['-40', '−40 dBFS'], ['-30', '−30 dBFS'], ['-20', '−20 dBFS']];
const SCANS = [['F', 'Sweep once'], ['C', 'Continuous'], ['G', 'Track']];
const TABS = [['beam', 'Manual beam'], ['measure', 'Measurement'], ['tone', 'Tonal detector'], ['diag', 'Diagnostics']];
const CAPTIONS = {
  bank: 'Each tile is a direction the array listens in. Brighter is louder.',
  sectors: 'Each tile is a sector of the diagnostic scan. Faded tiles are older than 5 s.',
  surface: 'The surface bulges toward the array where the sound stands above the background; colour shows the level.',
  points: 'Each point moves toward the array where the sound stands above the background; colour shows the level.',
};
const STYLES = [['tiles', 'Tiles'], ['surface', 'Surface'], ['points', 'Point cloud']];
const MOUNTS = {upright: 'upright', cw90: 'turned 90° clockwise', 180: 'turned 180°', ccw90: 'turned 90° anticlockwise'};

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch (_) { return {}; }
}

function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* storage unavailable */ }
}

/** Half-width of the field in degrees, from the grid edges. */
function fieldSpan(field) {
  if (!field) return 40;
  const edge = Math.max(...field.axes.x.map(Math.abs), ...field.axes.y.map(Math.abs));
  return field.space === 'sine' ? Math.asin(Math.min(1, edge)) * 180 / Math.PI : edge;
}

export function createView(container, {session}) {
  const prefs = {view: '3d', camera: 'behind', style: 'tiles', floor: -40, values: 'strongest', log: 'open', ...loadPrefs()};
  if (!['3d', 'map'].includes(prefs.view)) prefs.view = '3d';
  if (!STYLES.some(([key]) => key === prefs.style)) prefs.style = 'tiles';
  const state = {visible: false, kind: null, field: null, hover: null, tab: null};

  const acoustic = new AcousticScene();
  const connection = new ConnectionPanel(session, acoustic);
  const audition = new AuditionPanel(session, acoustic);
  const source = new SourcePanel();
  const audio = new AudioPanel(session);
  const history = new HistoryCharts();
  const log = new EventLog(session, {onFold: (folded) => { prefs.log = folded ? 'folded' : 'open'; savePrefs(prefs); fold(); }});
  const pages = {
    beam: new ManualBeamPanel(session),
    measure: new MeasurementPanel(session),
    tone: new TonePanel(session),
    diag: new DiagnosticsPanel(session, {extra: audition.el}),
  };
  const map = new FieldMap({onPick: pick, onHover: setHover});
  let scene3d = null;

  // ── Command bar: Live and Stop first; the sector scans are diagnostics ─
  const liveButton = h('button', {type: 'button', class: 'cmd cmd--live', 'aria-pressed': 'false',
    title: 'Listen in every bank direction at once and follow the strongest', onClick: () => session.stare()},
  h('i', {'aria-hidden': 'true'}), 'LIVE');
  const stopButton = h('button', {type: 'button', class: 'cmd cmd--stop', onClick: () => session.stop()},
    h('i', {'aria-hidden': 'true'}), 'STOP');
  const stateTitle = h('span', {class: 'state__title'});
  const stateDetail = h('span');
  const stateBox = h('div', {class: 'state', role: 'status'}, h('b', {}, h('i', {'aria-hidden': 'true'}), stateTitle), stateDetail);
  const scanLabel = h('span', {}, 'Diagnostic scans');
  const scanSummary = h('summary', {class: 'btn btn--sm'}, scanLabel, h('span', {class: 'menu__caret', 'aria-hidden': 'true'}, '▾'));
  const scanItems = SCANS.map(([key, label]) => h('button', {type: 'button', class: 'menu__item', 'aria-pressed': 'false',
    onClick: () => {
      scanMenu.open = false;
      ({F: () => session.sweepOnce(), C: () => session.continuous(), G: () => session.track()})[key]();
    }}, label));
  const scanMenu = h('details', {class: 'menu'}, scanSummary, h('div', {class: 'menu__list'}, ...scanItems));
  document.addEventListener('click', (event) => { if (!scanMenu.contains(event.target)) scanMenu.open = false; });
  const command = h('section', {class: 'command', 'aria-label': 'Commands'},
    liveButton, stopButton, stateBox, h('div', {class: 'command__right'}, scanMenu));

  // ── Field: 3D or map, with its tools in the header ────────────────────
  const viewSeg = segmented([['3d', '3D'], ['map', 'Map']], {
    label: 'Field view', onPick: (key) => { prefs.view = key; savePrefs(prefs); layout(); render(true); },
  });
  const cameraSeg = segmented([['behind', 'Behind'], ['above', 'Above'], ['side', 'Side']], {
    label: 'Camera', onPick: (key) => { prefs.camera = key; savePrefs(prefs); cameraSeg.set(key); scene3d?.setView(key); },
  });
  const floorSelect = h('select', {class: 'select select--sm', 'aria-label': 'Colour floor',
    onChange: () => { prefs.floor = parseInt(floorSelect.value, 10); savePrefs(prefs); render(true); }},
  options(FLOORS, String(prefs.floor)));
  const valuesSelect = h('select', {class: 'select select--sm', 'aria-label': 'Values on the map',
    onChange: () => { prefs.values = valuesSelect.value; savePrefs(prefs); render(true); }},
  options([['strongest', 'Strongest'], ['all', 'All']], prefs.values));
  const valuesPick = h('label', {class: 'pick'}, 'Values', valuesSelect);
  const styleSelect = h('select', {class: 'select select--sm', 'aria-label': 'How the 3D view draws the field',
    onChange: () => { prefs.style = styleSelect.value; savePrefs(prefs); render(true); }},
  options(STYLES, prefs.style));
  const stylePick = h('label', {class: 'pick'}, 'Show', styleSelect);
  const fieldHead = h('header', {class: 'panel__head'}, h('h2', {class: 'label'}, 'Field'),
    h('div', {class: 'tools'}, viewSeg.el, cameraSeg.el, h('label', {class: 'pick'}, 'Floor', floorSelect), stylePick, valuesPick));

  const sceneBox = h('div', {class: 'well__scene'});
  const mapBox = h('div', {class: 'well__map'}, map.el);
  const caption = h('p', {class: 'well__caption', 'aria-live': 'polite'});
  const barTicks = h('ul', {class: 'fmap__ticks'});
  const bar = h('div', {class: 'well__bar', 'aria-hidden': 'true'},
    h('div', {class: 'fmap__ramp', style: {background: rampGradient('to top')}}), barTicks);
  const waiting = h('p', {class: 'well__waiting', hidden: true});
  const well = h('div', {class: 'well'}, sceneBox, mapBox, caption, h('p', {class: 'well__hint'}, 'Drag to rotate'), bar, waiting);
  const fieldSection = h('section', {class: 'hw-field', 'aria-label': 'Field'}, fieldHead, well);

  // ── Drawer: the less frequent tools open under the Field ──────────────
  const drawerTitle = h('h2', {class: 'label'});
  const drawerBody = h('div', {class: 'drawer-panel__body'});
  const drawerPanel = h('section', {class: 'drawer-panel', hidden: true, 'aria-label': 'Drawer'},
    h('header', {class: 'panel__head'}, drawerTitle,
      h('div', {class: 'tools'}, h('button', {type: 'button', class: 'btn btn--sm btn--ghost', onClick: () => setTab(null)}, 'Close ▾'))),
    drawerBody);
  const tabButtons = TABS.map(([key, label]) => h('button', {type: 'button', class: 'tab', 'aria-pressed': 'false',
    onClick: () => setTab(state.tab === key ? null : key)}, label));
  const meta = h('div', {class: 'drawer__meta'});
  const foot = h('footer', {class: 'drawer'}, ...tabButtons, meta);

  const centre = h('div', {class: 'centre'}, fieldSection, drawerPanel);
  const side = h('div', {class: 'side'}, source.el, audio.el, history.el);
  const consoleEl = h('div', {class: 'hw-console', hidden: true},
    command, h('div', {class: 'work'}, log.el, centre, side), foot);

  const bannerText = h('span', {class: 'note note--body'});
  const banner = h('div', {class: 'banner', role: 'status', hidden: true},
    h('div', {class: 'banner__text'}, h('span', {class: 'banner__title'}, 'DSP parameters differ from the flashed program'), bannerText),
    h('div', {class: 'toolbar toolbar--tight'},
      h('a', {class: 'btn', href: '#study/parameters'}, 'Review in Study'),
      h('button', {type: 'button', class: 'btn btn--ghost', onClick: () => { dsp.stageFlashed(); window.location.hash = 'study/parameters'; }}, 'Stage a return to flashed')),
  );

  const connectWrap = h('div', {class: 'hw-connect'}, connection.el);
  container.append(banner, connectWrap, consoleEl);

  function renderBanner() {
    const modified = session.open ? dsp.status?.modified_words || 0 : 0;
    banner.hidden = modified === 0;
    const last = dsp.history.find((entry) => !entry.reverted);
    setText(bannerText, `${modified} ${modified === 1 ? 'word differs' : 'words differ'}${last ? `. Last change: ${last.label}` : ''}. Levels measured now reflect the changed parameters.`);
  }
  dsp.addEventListener('change', renderBanner);
  let dspPoll = null;

  // ── Behaviour ──────────────────────────────────────────────────────────
  /** A bank tile routes that beam to Ch1; a sector tile points the beam there once. */
  function pick(index) {
    const field = state.field;
    if (!field || !Number.isInteger(index)) return;
    if (field.kind === 'bank') {
      if (session.frame?.outputs) session.selectOutput(2, index);
      return;
    }
    pages.beam.setSector(index);
    session.steer(index, ' from the field');
  }

  function setHover(index) {
    state.hover = index;
    renderCaption();
  }

  function fold() {
    consoleEl.classList.toggle('log-folded', prefs.log === 'folded');
  }

  function setTab(tab) {
    state.tab = tab;
    drawerPanel.hidden = !tab;
    TABS.forEach(([key, label], i) => {
      tabButtons[i].setAttribute('aria-pressed', String(key === tab));
      if (key === tab) setText(drawerTitle, label);
    });
    if (tab) drawerBody.replaceChildren(pages[tab].el);
    render(true);
  }

  function ensureScene() {
    if (scene3d) return;
    import('./field3d.js').then(({FieldScene}) => {
      if (scene3d) return;
      scene3d = new FieldScene(sceneBox, {onPick: pick, onHover: setHover});
      scene3d.setView(prefs.camera);
      if (state.visible) scene3d.start();
      render(true);
    });
  }

  function layout() {
    const view = prefs.view;
    viewSeg.set(view);
    cameraSeg.set(prefs.camera);
    well.dataset.view = view;
    sceneBox.hidden = view !== '3d';
    mapBox.hidden = view !== 'map';
    cameraSeg.el.hidden = view !== '3d';
    stylePick.hidden = view !== '3d';
    valuesPick.hidden = view !== 'map';
    if (view === '3d') ensureScene();
  }

  function renderCaption() {
    const field = state.field;
    const cell = field && state.hover !== null ? field.cells[state.hover] : null;
    if (!cell) {
      setText(caption, !field ? '' : prefs.view === '3d' && CAPTIONS[prefs.style] ? CAPTIONS[prefs.style] : CAPTIONS[field.kind]);
      return;
    }
    const bank = field.kind === 'bank';
    const action = bank ? (session.frame?.outputs ? ' · click to listen on Ch1' : '') : ' · click to point the beam here';
    setText(caption, `${bank ? 'Beam' : 'Sector'} ${cell.index} · ${deg(cell.azimuth, 0)}, ${deg(cell.elevation, 0)}`
      + (Number.isFinite(cell.level) ? ` · ${num(cell.level, 1)} dBFS` : ' · not measured yet') + action);
  }

  function renderCommand(mode) {
    const ready = session.ready;
    liveButton.disabled = !ready;
    liveButton.setAttribute('aria-pressed', String(mode === 'stare'));
    stopButton.disabled = !session.open;
    const active = {once: 'F', continuous: 'C', track: 'G'}[mode];
    SCANS.forEach(([key], i) => {
      scanItems[i].disabled = !ready;
      scanItems[i].setAttribute('aria-pressed', String(key === active));
    });
    scanSummary.classList.toggle('is-active', Boolean(active));
    setText(scanLabel, active ? `Diagnostic: ${SCANS.find(([key]) => key === active)[1]}` : 'Diagnostic scans');

    const frame = session.frame;
    const track = frame?.target_update_rate_hz;
    const updates = Number.isFinite(track) && Number.isFinite(frame?.target_update_age_s) && frame.target_update_age_s < 2
      ? `${Math.round(track)} updates per second` : '';
    let title = 'Stopped';
    let detail = ready ? 'Press Live to listen in every direction' : 'Waiting for the device';
    let tone = 'stopped';
    if (mode === 'stare') {
      title = 'Running'; tone = 'running';
      detail = updates || 'Listening in every direction';
    } else if (active) {
      title = 'Scanning'; tone = 'scanning';
      detail = `${SCANS.find(([key]) => key === active)[1]}${updates ? ` · ${updates}` : ''}`;
    } else if (mode === 'fixed') {
      title = 'Holding the beam'; tone = 'scanning';
      detail = session.monitoring ? `Re-read at ${session.monitorRate} Hz` : 'Pointed once';
    }
    stateBox.dataset.state = tone;
    setText(stateTitle, title);
    setText(stateDetail, detail);
  }

  function renderFooter(field) {
    const frame = session.frame;
    const parts = [];
    const program = frame?.dsp;
    if (program) {
      const load = program.load;
      if (program.matches) {
        parts.push(h('span', {}, h('b', {}, 'DSP '), program.program,
          load ? ` · loaded by the MCU in ${(load.elapsed_us / 1e6).toFixed(2)} s` : ''));
      } else {
        parts.push(h('span', {class: 'is-fault'}, `DSP program mismatch (${program.matched}/${program.read} signature words): steering is locked`));
      }
    }
    const tables = frame?.tables;
    if (tables && tables.words > 0 && !tables.loaded) {
      parts.push(h('span', {class: 'is-fault'}, `Lookup tables not loaded (${tables.verified}/${tables.words})`));
    }
    const taper = frame?.taper;
    if (taper && !taper.applied) parts.push(h('span', {class: 'is-fault'}, `Taper not in place (${taper.verified}/${taper.written})`));
    const mount = frame?.array_mount;
    const tableMount = session.arrayLayout?.mount;
    if (mount && tableMount && mount !== tableMount) {
      parts.push(h('span', {class: 'is-fault'},
        `Array ${MOUNTS[mount] ?? mount} in the firmware but ${MOUNTS[tableMount] ?? tableMount} in the beam table: rebuild and flash`));
    } else if (mount) {
      parts.push(h('span', {}, h('b', {}, 'Array '), MOUNTS[mount] ?? mount));
    }
    if (field) {
      const span = `±${Math.round(fieldSpan(field))}° field of view`;
      parts.push(h('span', {}, h('b', {}, field.kind === 'bank' ? 'Bank ' : 'Sector grid '),
        `${field.rows} × ${field.columns}${field.kind === 'bank' ? ' beams' : ''} · ${span}`));
    }
    if (session.frozen) parts.push(h('span', {class: 'is-fault'}, 'Display frozen'));
    meta.replaceChildren(...parts);
  }

  /** Largest square cell that leaves room for the axes, titles and colour bar in the well. */
  function cellSize(field) {
    const byWidth = ((well.clientWidth || 900) - 250) / field.columns - 3;
    const byHeight = ((well.clientHeight || 520) - 150) / field.rows - 3;
    return Math.max(14, Math.min(140, Math.floor(Math.min(byWidth, byHeight))));
  }

  new ResizeObserver(() => render(true)).observe(well);

  let queued = false;
  let forceNext = false;
  function render(force = false) {
    forceNext = forceNext || force === true;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const forced = forceNext;
      forceNext = false;
      draw(forced);
    });
  }

  function draw(force) {
    if (!state.visible) return;
    const open = session.open;
    connectWrap.hidden = open;
    consoleEl.hidden = !open;
    if (!open) return;
    const frame = session.frame;
    const mode = operatingMode(session);
    state.kind = fieldKind(frame, {previous: state.kind, monitoring: session.monitoring});
    const field = buildField(frame, state.kind, {cellAge: (row, column) => session.cellAge(row, column), staleMs: STALE_MS});
    state.field = field;

    renderCommand(mode);
    waiting.hidden = Boolean(field);
    caption.hidden = !field;
    if (!field) {
      setText(waiting, `Connected to ${session.transportLabel}. Waiting for the firmware to report its beams…`);
    } else if (prefs.view === 'map') {
      map.render(field, {floor: prefs.floor, values: prefs.values, cellPx: cellSize(field)});
    } else {
      barTicks.replaceChildren(...[0, 0.25, 0.5, 0.75, 1].map((f) => h('li', {}, f === 0 ? '0 dBFS' : num(prefs.floor * f, 0))));
      scene3d?.render(field, {floor: prefs.floor, session, style: prefs.style});
    }
    source.render(sourceAnswer(session, field, mode));
    audio.render(frame);
    history.render(session, {span: fieldSpan(field), force});
    if (state.tab === 'diag') pages.diag.render(mode);
    else if (state.tab) pages[state.tab].render();
    audition.update();
    renderCaption();
    renderFooter(field);
  }

  session.addEventListener('frame', () => render());
  session.addEventListener('init', () => render(true));
  session.addEventListener('link', () => render());

  let ticker = null;
  layout();
  fold();
  return {
    show() {
      state.visible = true;
      scene3d?.start();
      ticker = setInterval(() => render(), 500);
      const refreshDsp = () => { if (session.open) dsp.load().then(() => dsp.refresh()).catch(() => {}); };
      refreshDsp();
      dspPoll = setInterval(refreshDsp, 5000);
      render(true);
    },
    hide() {
      state.visible = false;
      scene3d?.stop();
      clearInterval(ticker);
      clearInterval(dspPoll);
    },
  };
}
