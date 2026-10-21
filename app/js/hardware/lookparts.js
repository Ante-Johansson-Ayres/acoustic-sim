/**
 * lookparts.js — what the hidden looks (shared/look.js) add to the Hardware screen, as their
 * canvas mockups: the dot-matrix angle boards, the dither steps and five-cell meters of the map
 * tiles, and the command band's word and level scale. Pure functions, tested in Node; the
 * elements are built in status.js, map.js and view.js, and styled by styles/looks/.
 */

// 5 × 7 digits, and a 3-wide degree sign and dash, as the mockups' glyph board.
const GLYPH = {
  0: ['.###.', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
  1: ['..#..', '.##..', '..#..', '..#..', '..#..', '..#..', '.###.'],
  2: ['.###.', '#...#', '....#', '...#.', '..#..', '.#...', '#####'],
  3: ['####.', '....#', '....#', '.###.', '....#', '....#', '####.'],
  4: ['...#.', '..##.', '.#.#.', '#..#.', '#####', '...#.', '...#.'],
  5: ['#####', '#....', '####.', '....#', '....#', '#...#', '.###.'],
  6: ['..##.', '.#...', '#....', '####.', '#...#', '#...#', '.###.'],
  7: ['#####', '....#', '...#.', '..#..', '.#...', '.#...', '.#...'],
  8: ['.###.', '#...#', '#...#', '.###.', '#...#', '#...#', '.###.'],
  9: ['.###.', '#...#', '#...#', '.####', '....#', '...#.', '.##..'],
  d: ['.#.', '#.#', '.#.', '...', '...', '...', '...'],
  '-': ['...', '...', '...', '###', '...', '...', '...'],
};

export const LED_COLUMNS = 17;
export const LED_ROWS = 9;

const widthOf = (glyphs) => glyphs.reduce((sum, ch) => sum + GLYPH[ch][0].length, 0) + Math.max(0, glyphs.length - 1);

/** Lit cells, row by row, of a `columns` × 9 board (17 by default) showing `text` centred: digits, `d` for degrees, `-`. */
export function ledCells(text, columns = LED_COLUMNS) {
  let glyphs = [...String(text)].filter((ch) => GLYPH[ch]);
  if (widthOf(glyphs) > columns) glyphs = glyphs.filter((ch) => ch !== 'd');
  const cells = new Array(columns * LED_ROWS).fill(false);
  let x = Math.floor((columns - widthOf(glyphs)) / 2);
  for (const ch of glyphs) {
    GLYPH[ch].forEach((line, row) => [...line].forEach((v, k) => {
      if (v === '#' && x + k >= 0 && x + k < columns) cells[(row + 1) * columns + x + k] = true;
    }));
    x += GLYPH[ch][0].length + 1;
  }
  return cells;
}

/** Columns a board needs to show `text` whole (at least 17). */
export function ledColumns(text) {
  return Math.max(LED_COLUMNS, widthOf([...String(text)].filter((ch) => GLYPH[ch] && ch !== 'd')));
}

/** What a board shows for an angle: its size in whole degrees, or dashes without one. */
export function ledText(angle) {
  return Number.isFinite(angle) ? `${Math.round(Math.abs(angle))}d` : '--';
}

/** A signed angle for a board, as the Beam pattern and Simulator mockups: a minus leads; the degree sign only where it fits. */
export function boardText(angle) {
  if (!Number.isFinite(angle)) return '--';
  const v = Math.round(angle);
  const digits = String(Math.abs(v));
  if (v < 0) return `-${digits}${digits.length < 2 ? 'd' : ''}`;
  return `${digits}${digits.length < 3 ? 'd' : ''}`;
}

/** Which way an angle points, in words. */
export function sideWord(angle, positive, negative, zero) {
  if (!Number.isFinite(angle)) return '—';
  return Math.abs(angle) < 0.5 ? zero : angle > 0 ? positive : negative;
}

/** Dither step of a tile, 0 (solid, loudest) to 5, in six even steps from 0 dBFS down to the floor. */
export function ditherStep(level, floor) {
  if (!Number.isFinite(level)) return null;
  return Math.max(0, Math.min(5, Math.floor(level / (floor / 6))));
}

/** Lit cells of a tile's five-cell meter: one per fifth of the range from the floor to 0 dBFS. */
export function meterFill(level, floor) {
  if (!Number.isFinite(level)) return 0;
  return Math.max(0, Math.min(5, Math.round((level - floor) / (-floor / 5))));
}

/** Lit cells of the Arasaka command band's eight-cell scale: dB above the background, 0 to 30. */
export function scaleFill(aboveDb) {
  return Number.isFinite(aboveDb) ? Math.max(0, Math.min(8, Math.round(aboveDb / 30 * 8))) : 0;
}

/** The command band's big word for an operating mode (status.js `operatingMode`). */
export function modeWord(mode) {
  return {stare: 'LIVE', track: 'TRACK', continuous: 'SCAN', once: 'SWEEP', fixed: 'BEAM'}[mode] || 'IDLE';
}
