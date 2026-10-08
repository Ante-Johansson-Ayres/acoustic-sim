/**
 * bank.js — the fixed beam bank (stare mode): every bank direction is measured
 * at once, so this map refreshes as a whole about 20 times a second. The ring
 * marks the strongest beam, the dashed outline the beam Ch1 listens to.
 * Clicking a cell routes that bank beam to Ch1.
 */

import { h, setText } from '../shared/dom.js';
import { inkOn, levelToUnit, rampColor, rgbCss } from '../shared/colormap.js';
import { deg, num } from '../shared/format.js';

const SOURCES = ['Steered beam', 'Microphone', 'Bank beam'];

export class BankPanel {
  constructor(session) {
    this.session = session;
    this.key = '';
    this.cells = [];
    this.grid = h('div', {class: 'bank__cells'});
    this.caption = h('p', {class: 'note note--body', 'aria-live': 'polite'});
    this.output = h('select', {class: 'select select--sm', 'aria-label': 'Ch1 source',
      onChange: () => {
        const [source, index] = this.output.value.split(':').map(Number);
        session.selectOutput(source, index);
      }});
    this.outputRow = h('span', {class: 'inline-field', hidden: true}, h('span', {}, 'Ch1 listens to'), this.output);
    this.el = h('section', {class: 'section', 'aria-labelledby': 'hw-bank-title', hidden: true},
      h('div', {class: 'section__row'}, h('h2', {class: 'section__title', id: 'hw-bank-title'}, 'Beam bank')),
      this.grid, this.caption, this.outputRow);
  }

  build(bank, outputs) {
    const key = `${bank.rows}x${bank.columns}|${outputs?.microphones}|${outputs?.bank_beams}`;
    if (key === this.key) return;
    this.key = key;
    this.grid.style.gridTemplateColumns = `repeat(${bank.columns}, 1fr)`;
    this.cells = [];
    const nodes = [];
    for (let displayRow = 0; displayRow < bank.rows; displayRow++) {
      const row = bank.rows - 1 - displayRow;
      for (let column = 0; column < bank.columns; column++) {
        const beam = row * bank.columns + column;
        const text = h('span', {class: 'bank__value'});
        const cell = h('button', {type: 'button', class: 'bank__cell',
          onClick: () => this.session.selectOutput(2, beam)}, text);
        this.cells[beam] = {cell, text, row, column};
        nodes.push(cell);
      }
    }
    this.grid.replaceChildren(...nodes);
    const choices = [['0:0', SOURCES[0]]];
    for (let mic = 0; mic < (outputs?.microphones || 0); mic++) choices.push([`1:${mic}`, `${SOURCES[1]} ${mic}`]);
    for (let beam = 0; beam < (outputs?.bank_beams || 0); beam++) choices.push([`2:${beam}`, `${SOURCES[2]} ${beam}`]);
    this.output.replaceChildren(...choices.map(([value, label]) => h('option', {value}, label)));
  }

  render(frame, {floor}) {
    const bank = frame?.bank;
    this.el.hidden = !bank;
    if (!bank) return;
    const outputs = frame.outputs;
    this.build(bank, outputs);
    const target = bank.target;
    const ch1 = frame.ch1;
    const listening = ch1 && ch1.code === 0 && ch1.source === 2 ? ch1.index : null;
    for (const entry of this.cells) {
      const {cell, text, row, column} = entry;
      const beam = row * bank.columns + column;
      const level = bank.levels_db?.[row]?.[column];
      const rgb = rampColor(levelToUnit(level, floor) ?? 0);
      cell.style.background = Number.isFinite(level) ? rgbCss(rgb) : 'var(--raised)';
      cell.classList.toggle('is-target', target?.beam === beam);
      cell.classList.toggle('is-listening', listening === beam);
      setText(text, Number.isFinite(level) ? num(level, 0) : '');
      text.style.color = inkOn(rgb);
      cell.setAttribute('aria-label', `Bank beam ${beam}${Number.isFinite(level) ? `, ${num(level, 1)} dBFS` : ''}. Listen to it on Ch1.`);
    }
    if (target) {
      setText(this.caption, `Strongest near ${deg(target.azimuth_deg)} azimuth, ${deg(target.elevation_deg)} elevation; `
        + `the steered beam is on sector ${target.sector}.`);
    } else {
      setText(this.caption, frame.firmware_mode === 'B'
        ? `${bank.maps} maps measured. No direction stands out from its background yet.`
        : 'Start Stare to measure every bank direction at once.');
    }
    this.outputRow.hidden = !outputs;
    if (outputs && ch1 && ch1.code === 0) this.output.value = `${ch1.source}:${ch1.source === 0 ? 0 : ch1.index}`;
  }
}
