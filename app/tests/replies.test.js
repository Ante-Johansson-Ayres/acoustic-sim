import { test } from 'node:test';
import assert from 'node:assert/strict';

import { outputReplyCode } from '../js/hardware/replies.js';

test('the CH1 reply is matched to the selection that was sent', () => {
  const frame = {sequence: 20, ch1: {source: 1, index: 7, code: 0}};
  assert.equal(outputReplyCode(frame, 1, 7, 19), 0);
  assert.equal(outputReplyCode(frame, 1, 8, 19), null);
  assert.equal(outputReplyCode(frame, 1, 7, 20), null);
  assert.equal(outputReplyCode({sequence: 21, ch1: {source: 1, index: 7, code: -1}}, 1, 7, 19), -1);
});
