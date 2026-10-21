import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LED_COLUMNS, LED_ROWS, ditherStep, ledCells, ledText, meterFill, modeWord, scaleFill, sideWord } from '../js/hardware/lookparts.js';

const rows = (cells) => Array.from({length: LED_ROWS}, (_, r) =>
  cells.slice(r * LED_COLUMNS, (r + 1) * LED_COLUMNS).map((on) => (on ? '#' : '.')).join(''));

test('the board centres the angle with its degree sign, a blank row above and below', () => {
  const board = rows(ledCells('14d'));
  assert.equal(board[0], '.................');
  assert.equal(board[1], '...#......#...#..');
  assert.equal(board[5], '...#...#####.....');
  assert.equal(board[7], '..###.....#......');
  assert.equal(board[8], '.................');
});

test('the board shows dashes without an angle, and drops the degree sign when three digits fill it', () => {
  assert.equal(ledText(null), '--');
  assert.equal(ledText(-13.6), '14d');
  assert.equal(rows(ledCells('--'))[4], '.....###.###.....');
  const wide = rows(ledCells('100d'));
  assert.equal(wide[1], '..#....###...###.');
  assert.equal(ledCells('x').filter(Boolean).length, 0);
});

test('directions read in words, level within half a degree', () => {
  assert.equal(sideWord(12, 'right', 'left', 'ahead'), 'right');
  assert.equal(sideWord(-0.4, 'right', 'left', 'ahead'), 'ahead');
  assert.equal(sideWord(-3, 'up', 'down', 'level'), 'down');
  assert.equal(sideWord(undefined, 'up', 'down', 'level'), '—');
});

test('tiles step in six even bands from 0 dBFS to the floor, meters in fifths', () => {
  assert.equal(ditherStep(-3, -40), 0);
  assert.equal(ditherStep(-7, -40), 1);
  assert.equal(ditherStep(-40, -40), 5);
  assert.equal(ditherStep(-55, -40), 5);
  assert.equal(ditherStep(2, -40), 0);
  assert.equal(ditherStep(null, -40), null);
  assert.equal(meterFill(-40, -40), 0);
  assert.equal(meterFill(-20, -40), 3);
  assert.equal(meterFill(0, -40), 5);
  assert.equal(meterFill(NaN, -40), 0);
});

test('the scale lights one cell per 3.75 dB above the background, and the word follows the mode', () => {
  assert.equal(scaleFill(15), 4);
  assert.equal(scaleFill(40), 8);
  assert.equal(scaleFill(-2), 0);
  assert.equal(scaleFill(null), 0);
  assert.equal(modeWord('stare'), 'LIVE');
  assert.equal(modeWord('idle'), 'IDLE');
  assert.equal(modeWord('track'), 'TRACK');
});
