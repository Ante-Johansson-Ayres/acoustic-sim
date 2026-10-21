import { test } from 'node:test';
import assert from 'node:assert/strict';

import { BOARDS } from '../js/hardware/arraydata.js';
import { buildRig, channelOrder } from '../js/hardware/rig3d.js';

const boardMics = BOARDS.petals.flatMap((petal) => petal.mics.map(([x, y, used]) => ({x, y, used: Boolean(used)})));

test('the boards carry 48 microphone places, 44 of them in the beam table', () => {
  assert.equal(BOARDS.petals.length, 6);
  assert.equal(boardMics.length, 48);
  assert.equal(boardMics.filter((m) => m.used).length, 44);
  assert.deepEqual(BOARDS.petals.map((p) => p.angle), [90, 30, -30, -90, -150, 150]);
});

test('each contract position finds its own board microphone, in the contract order, within a millimetre', () => {
  const used = boardMics.map((m, i) => ({...m, i})).filter((m) => m.used);
  const shuffled = used.slice().reverse();
  const positions = shuffled.map((m) => [m.x + 0.6, m.y - 0.6]);
  assert.deepEqual(channelOrder(positions, boardMics), shuffled.map((m) => m.i));
  assert.equal(channelOrder(null, boardMics).length, 44);
});

test('a rig has its microphones in channel order, the 4 unused as rings, and the parts each look animates', () => {
  const positions = boardMics.filter((m) => m.used).map((m) => [m.x, m.y]);
  for (const look of ['default', 'panel', 'arasaka']) {
    const {parts, joints} = buildRig({look, layout: {positions}, floorY: -0.95});
    assert.equal(parts.mics.count, 44);
    assert.equal(parts.unused.children.length, 4);
    assert.equal(parts.petals.length, 6);
    assert.equal(parts.stages.length, 4);
    assert.equal(parts.pens.length, look === 'panel' ? 7 : 0);
    assert.equal(parts.ghosts.length, look === 'arasaka' ? 2 : 0);
    assert.equal(parts.chroma.length, look === 'arasaka' ? 3 : 0);
    assert.ok(joints.deskTop < joints.railTop && joints.railTop < joints.J3[0]);
    // The desk is a top only: nothing of it reaches more than its 25 mm thickness below the rail.
    parts.desk.updateMatrixWorld(true);
    const lowest = Math.min(...parts.desk.children.filter((o) => o.isMesh).map((o) => {
      o.geometry.computeBoundingBox();
      return o.geometry.boundingBox.min.y + o.position.y;
    }));
    assert.ok(lowest > joints.deskTop - 0.026, `${look}: desk reaches ${lowest.toFixed(3)} m`);
  }
});
