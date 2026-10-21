import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseLook } from '../js/shared/look.js';

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
