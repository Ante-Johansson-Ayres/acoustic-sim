/**
 * led.js — the hidden looks' dot-matrix board, 9 rows of 17 columns or more (lit cells from hardware/lookparts.js),
 * redrawn only when its text changes. Styled by styles/looks/: `lk-led--ink` lights in ink instead of the look's accent.
 */

import { h } from './dom.js';
import { LED_COLUMNS, LED_ROWS, ledCells } from '../hardware/lookparts.js';

export function ledBoard(modifier = '', columns = LED_COLUMNS) {
  const cells = Array.from({length: columns * LED_ROWS}, () => h('i'));
  const el = h('span', {class: `lk-led ${modifier}`.trim(), role: 'img', style: `--lk-columns: ${columns}`}, cells);
  let shown = null;
  return {
    el,
    set(text, label) {
      el.setAttribute('aria-label', label);
      if (text === shown) return;
      shown = text;
      ledCells(text, columns).forEach((on, i) => cells[i].classList.toggle('on', on));
    },
  };
}
