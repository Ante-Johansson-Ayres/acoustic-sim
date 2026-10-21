import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseLook, sceneColor } from '../js/shared/look.js';

test('the look comes from the query string, in any case', () => {
  assert.equal(parseLook('?look=panel'), 'panel');
  assert.equal(parseLook('?look=Arasaka'), 'arasaka');
  assert.equal(parseLook('?x=1&look=arasaka'), 'arasaka');
  assert.equal(parseLook('look=panel'), 'panel');
});

test('anything else keeps the app in its own look', () => {
  for (const search of ['', '?', '?look=', '?look=neon', '?looks=panel', '#look=panel', undefined]) {
    assert.equal(parseLook(search), null);
  }
});

test('3D scenes keep their own colours without a look', () => {
  assert.equal(sceneColor('background', '#1d2025'), '#1d2025');
  assert.equal(sceneColor('accent', 0x7aa7ff, null), 0x7aa7ff);
  assert.equal(sceneColor('background', '#1d2025', 'panel'), '#f2f2f0');
  assert.equal(sceneColor('marker', '#ffffff', 'arasaka'), '#e9e3dc');
  assert.equal(sceneColor('ok', '#4cc38a', 'panel'), '#4cc38a');
});
