/**
 * rig3d.js — the Heimdall array as its real boards (arraydata.js: the hub and six petals with their silkscreen, standoffs and
 * microphones) on the CAD mount (FreeCAD Heimdall_GoPro_Mounts_V06 at true scale: the V plate on the hub's back, the 115 mm and
 * 130 mm extensions on GoPro knuckles, the V-slot block on a 2020 rail) standing on a desk, for the Hardware and Present 3D views.
 * The app's own look draws it lit in its palette; the hidden looks (look.js) draw Panel's paper cards and Arasaka's dark hologram.
 * The parts come back grouped so build3d.js can animate them: hub, petals, microphones (in DSP channel order), the mount from
 * the plate down to the rail, the desk.
 *
 * Scene axes as field3d.js: x = LEFT, y = up, z = forward toward the field; the array centre at the origin, seen from behind.
 */

import * as THREE from 'three';
import { BOARDS } from './arraydata.js';

export const MM = 0.001;
const PET_Z = 0;                 // petals from z 0 to 1.6 mm (field side), 8.4 mm nylon standoffs,
const HUB_Z = -11.6 * MM;        // then the hub from -11.6 to -10 mm, and the mount plate on its back
const DEG = Math.PI / 180;

const PALETTES = {
  // The original-app preview the user approved (2026-10-21): the app's palette, lit.
  default: {
    lit: true, petal: '#30343b', hub: '#262a30', edge: '#80868e', edgeAlpha: 0.9, silk: '#9aa0a8', silkAlpha: 0.35,
    mic: '#eceef1', unused: '#80868e', standoff: '#9fa3a8', orange: '#e8742a', black: '#3c4048', alu: '#a3a8ae',
    steel: '#d0d3d8', plate: '#3c4048', desk: '#3a3f47', leg: '#2c3036',
  },
  // Panel: paper cut-outs. White petals over a grey hub, a soft ink shadow behind each, faint silkscreen, ink microphones.
  panel: {
    lit: false, petal: '#ffffff', petalSide: '#c9c9c6', hub: '#d6d6d3', hubSide: '#b5b5b2', shadow: '#111111', shadowAlpha: 0.14,
    silk: '#111111', silkAlpha: 0.12, mic: '#111111', unused: '#111111', unusedAlpha: 0.35, standoff: '#a3a3a0', pen: '#111111',
    orange: '#ea5a00', black: '#1c1c1c', alu: '#c9c9c6', steel: '#a3a3a0', plate: '#1c1c1c', desk: '#dcdcda', leg: '#b5b5b2',
  },
  // Arasaka: a dark hologram. Near-black boards with red edges, red silkscreen, pale microphones, two ghost outlines.
  arasaka: {
    lit: false, petal: '#240a0e', hub: '#16070a', edge: '#f2122a', edgeAlpha: 1, hubEdgeAlpha: 0.55, silk: '#f2122a', silkAlpha: 0.45,
    mic: '#e9e3dc', unused: '#f2122a', unusedAlpha: 0.5, standoff: '#2a0e12', ghost: '#f2122a', ghostAlpha: 0.35,
    orange: '#f2122a', black: '#1a0a0d', alu: '#141416', steel: '#e9e3dc', plate: '#1a0a0d', desk: '#141416', leg: '#0c0c0e',
    partEdge: '#f2122a', partEdgeAlpha: 0.5,
  },
};

const pointsOf = (flat) => {
  const points = [];
  for (let i = 0; i < flat.length; i += 2) points.push([flat[i], flat[i + 1]]);
  return points;
};
// Array frame (mm, x right, y up, seen from behind) to the scene (x = left).
const v2 = ([x, y]) => new THREE.Vector2(-x * MM, y * MM);
const centreOf = (flat) => {
  const p = pointsOf(flat);
  return p.reduce((s, [x, y]) => [s[0] + x / p.length, s[1] + y / p.length], [0, 0]);
};

/** Which board microphone each contract position is (nearest, all within 0.9 mm), so channel c draws at its real place. */
export function channelOrder(positions, boardMics) {
  if (!positions?.length) return boardMics.map((m, i) => (m.used ? i : -1)).filter((i) => i >= 0);
  return positions.map(([x, y]) => {
    let best = 0;
    let gap = Infinity;
    boardMics.forEach((m, i) => {
      const d = Math.hypot(m.x - x, m.y - y);
      if (d < gap) { gap = d; best = i; }
    });
    return best;
  });
}

/**
 * The rig for a look ('default', 'panel' or 'arasaka'), its desk standing on the floor at `floorY`. `layout` is the session's
 * array layout (contract positions in DSP order), used to number the microphones. Returns the group and its animatable parts.
 */
export function buildRig({look = 'default', layout = null, floorY = -2.45} = {}) {
  const C = PALETTES[look] || PALETTES.default;
  const group = new THREE.Group();
  const own = look !== 'default';          // the hidden looks fade parts in during the build, so each part gets its own materials
  const solid = (color, extra = {}) => (C.lit
    ? new THREE.MeshLambertMaterial({color, ...extra})
    : new THREE.MeshBasicMaterial({color, ...extra}));
  const lambert = (color) => new THREE.MeshLambertMaterial({color, transparent: own});
  const lineMat = (color, opacity = 1, extra = {}) => new THREE.LineBasicMaterial({color, transparent: own || opacity < 1, opacity, ...extra});
  const lines = (geometry, material) => new THREE.LineSegments(geometry, material);
  const edgesOf = (geometry, color, opacity, threshold = 30) => lines(new THREE.EdgesGeometry(geometry, threshold), lineMat(color, opacity));

  if (C.lit || look === 'panel' || look === 'arasaka') {
    group.add(new THREE.HemisphereLight(0xffffff, look === 'panel' ? 0xb5b5b2 : 0x3a3f47, look === 'panel' ? 1.6 : 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, look === 'panel' ? 1.6 : 2.4);
    sun.position.set(1.5, 3, -3);
    group.add(sun);
  }

  const shapeOf = (outline, holes = []) => {
    const shape = new THREE.Shape(pointsOf(outline).map(v2));
    for (const hole of holes) shape.holes.push(new THREE.Path(pointsOf(hole).map(v2)));
    return shape;
  };
  /** A 1.6 mm board from z to z + 1.6 mm: caps and side walls (paper), or lit, or a flat hologram card. */
  const board = (parent, outline, holes, z, {cap, side, edge, edgeAlpha, dashed = false}) => {
    const geometry = new THREE.ExtrudeGeometry(shapeOf(outline, holes), {depth: 1.6 * MM, bevelEnabled: false, curveSegments: 4});
    const material = side
      ? [new THREE.MeshBasicMaterial({color: cap, transparent: own}), new THREE.MeshBasicMaterial({color: side, transparent: own})]
      : solid(cap, {transparent: own});
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.z = z;
    parent.add(mesh);
    if (edge) {
      const outlineOnly = new THREE.EdgesGeometry(geometry, 30);
      const line = new THREE.LineSegments(outlineOnly, dashed
        ? new THREE.LineDashedMaterial({color: edge, transparent: true, opacity: edgeAlpha, dashSize: 4 * MM, gapSize: 4 * MM})
        : lineMat(edge, edgeAlpha));
      if (dashed) line.computeLineDistances();
      line.position.z = z;
      parent.add(line);
    }
    return mesh;
  };
  /** Thin closed polylines on a board face (silkscreen hexagons, outlines). */
  const polylines = (polys, z, color, opacity) => {
    const pts = [];
    for (const flat of polys) {
      const p = pointsOf(flat);
      for (let i = 0; i < p.length; i++) {
        const a = p[i];
        const b = p[(i + 1) % p.length];
        pts.push(new THREE.Vector3(-a[0] * MM, a[1] * MM, z), new THREE.Vector3(-b[0] * MM, b[1] * MM, z));
      }
    }
    return lines(new THREE.BufferGeometry().setFromPoints(pts), lineMat(color, opacity));
  };
  /** One closed outline as a line strip in drawing order, so the build can draw it on (setDrawRange). */
  const strip = (flat, z, color, opacity = 1) => {
    const pts = pointsOf(flat).map(([x, y]) => new THREE.Vector3(-x * MM, y * MM, z));
    pts.push(pts[0].clone());
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(color, opacity));
    line.userData.count = pts.length;
    return line;
  };

  // ── Boards ────────────────────────────────────────────────────────────
  const silk = [];
  const hub = new THREE.Group();
  board(hub, BOARDS.hub.outline, BOARDS.hub.holes, HUB_Z, {
    cap: C.hub, side: C.hubSide, edge: look === 'panel' ? null : C.edge, edgeAlpha: C.hubEdgeAlpha ?? C.edgeAlpha, dashed: look === 'arasaka',
  });
  silk.push(polylines(BOARDS.hub.silk, HUB_Z - 0.2 * MM, C.silk, C.silkAlpha));
  hub.add(silk.at(-1));
  const petals = BOARDS.petals.map((petal) => {
    const g = new THREE.Group();
    board(g, petal.outline, petal.holes, PET_Z, {cap: C.petal, side: C.petalSide, edge: look === 'panel' ? null : C.edge, edgeAlpha: C.edgeAlpha});
    silk.push(polylines(petal.silk, -0.2 * MM, C.silk, C.silkAlpha));
    g.add(silk.at(-1));
    const standoff = new THREE.CylinderGeometry(2.9 * MM, 2.9 * MM, 10 * MM, 6);
    for (const hole of petal.holes) {
      const [cx, cy] = centreOf(hole);
      const s = new THREE.Mesh(standoff, look === 'default' ? solid(C.standoff) : lambert(C.standoff));
      s.rotation.x = Math.PI / 2;
      s.position.set(-cx * MM, cy * MM, -5 * MM);
      g.add(s);
    }
    g.userData.direction = new THREE.Vector3(-Math.cos(petal.angle * DEG), Math.sin(petal.angle * DEG), 0);
    return g;
  });
  group.add(hub, ...petals);

  // Panel: a soft ink shadow behind each card (as the mockup's offset copies) and the pen outlines the build draws.
  const shadows = [];
  const pens = [];
  if (look === 'panel') {
    const shadow = (outline, holes, dx, dy, z, alpha) => {
      const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shapeOf(outline, holes), 4),
        new THREE.MeshBasicMaterial({color: C.shadow, transparent: true, opacity: alpha, depthWrite: false}));
      mesh.position.set(-dx * MM, dy * MM, z);
      return mesh;
    };
    const hubShadow = shadow(BOARDS.hub.outline, BOARDS.hub.holes, 5, -8, HUB_Z + 4 * MM, C.shadowAlpha * 0.85);
    hub.add(hubShadow);
    shadows.push(hubShadow);
    petals.forEach((g, i) => {
      const s = shadow(BOARDS.petals[i].outline, BOARDS.petals[i].holes, 6, -10, PET_Z + 6 * MM, C.shadowAlpha);
      g.add(s);
      shadows.push(s);
    });
    pens.push(strip(BOARDS.hub.outline, HUB_Z - 0.4 * MM, C.pen));
    BOARDS.petals.forEach((petal) => pens.push(strip(petal.outline, -0.6 * MM, C.pen)));
    for (const pen of pens) { pen.visible = false; group.add(pen); }
  }

  // Arasaka: two ghost outlines of the boards, 23 mm in front of and behind the hologram, and for the build the chromatic
  // copies of the outlines (white, cyan, magenta) that swell out and split.
  const ghosts = [];
  const chroma = [];
  const outlineGroup = (z, color, opacity, extra) => {
    const g = new THREE.Group();
    g.add(strip(BOARDS.hub.outline, HUB_Z + z, color, opacity));
    for (const petal of BOARDS.petals) g.add(strip(petal.outline, z, color, opacity));
    if (extra) g.traverse((o) => { if (o.material) Object.assign(o.material, extra); });
    return g;
  };
  if (look === 'arasaka') {
    for (const dz of [23 * MM, -23 * MM]) {         // the far one (toward the field) first, as the mockup draws them
      const ghost = outlineGroup(dz, C.ghost, C.ghostAlpha);
      ghosts.push(ghost);
      group.add(ghost);
    }
    for (const hex of ['#ffffff', '#19e6ff', '#ff2bd6']) {
      const copy = outlineGroup(-0.8 * MM, hex, 1, {blending: THREE.AdditiveBlending, depthTest: false});
      copy.visible = false;
      chroma.push(copy);
      group.add(copy);
    }
  }

  // ── Microphones: the used ones as dots in DSP channel order, the 4 not in the beam table as rings ──
  const boardMics = BOARDS.petals.flatMap((petal) => petal.mics.map(([x, y, used]) => ({x, y, used: Boolean(used)})));
  const order = channelOrder(layout?.positions, boardMics);
  const dot = new THREE.CircleGeometry(look === 'default' ? 5 * MM : 4 * MM, 20);
  const mics = new THREE.InstancedMesh(dot, new THREE.MeshBasicMaterial({color: 0xffffff, side: THREE.DoubleSide}), order.length);
  const micColor = new THREE.Color(C.mic);
  mics.userData.places = order.map((index, channel) => {
    const m = boardMics[index];
    const place = new THREE.Vector3(-m.x * MM, m.y * MM, -0.4 * MM);
    mics.setMatrixAt(channel, new THREE.Matrix4().makeTranslation(place.x, place.y, place.z));
    mics.setColorAt(channel, micColor);
    return place;
  });
  mics.userData.color = micColor;
  const taken = new Set(order);
  const ring = new THREE.RingGeometry(3.6 * MM, 5 * MM, 20);
  const unused = new THREE.Group();
  boardMics.forEach((m, i) => {
    if (taken.has(i)) return;
    const r = new THREE.Mesh(ring, new THREE.MeshBasicMaterial({color: C.unused, side: THREE.DoubleSide, transparent: own, opacity: C.unusedAlpha ?? 1}));
    r.position.set(-m.x * MM, m.y * MM, -0.4 * MM);
    unused.add(r);
  });
  group.add(mics, unused);

  // ── Mount, at true scale: the V plate on the hub's back, the 115 mm extension 31° off vertical, the 130 mm one at 22°, ──
  // ── the V-slot block on a 2020 rail on the desk. Knuckles: 3 fingers of one part between 2 of the next, bolt through. ──
  const stages = [new THREE.Group(), new THREE.Group(), new THREE.Group(), new THREE.Group()];   // plate first, rail last
  const part =(geometry, color, parent, setup) => {
    const mesh = new THREE.Mesh(geometry, look === 'default' ? solid(color) : lambert(color));
    setup(mesh);
    parent.add(mesh);
    if (C.partEdge) {
      const edge = edgesOf(geometry, C.partEdge, C.partEdgeAlpha, 40);
      edge.position.copy(mesh.position);
      edge.rotation.copy(mesh.rotation);
      parent.add(edge);
    }
    return mesh;
  };
  const F3 = [[-7.6, -4.6], [-1.5, 1.5], [4.7, 12.7]];
  const F2 = [[-4.5, -1.7], [1.8, 4.5]];
  const cyl = (parent, x0, x1, y, z, r, color, sides = 32) => part(new THREE.CylinderGeometry(r * MM, r * MM, (x1 - x0) * MM, sides), color, parent, (m) => {
    m.rotation.z = Math.PI / 2;
    m.position.set(-(x0 + x1) / 2 * MM, y, z);
  });
  const knuckle = (parent, [y, z], c3, c2) => {
    F3.forEach(([a, b]) => cyl(parent, a, b, y, z, 9, c3));
    F2.forEach(([a, b]) => cyl(parent, a, b, y, z, 9, c2));
    cyl(parent, -10.4, -7.6, y, z, 5, C.steel);
    cyl(parent, 12.7, 15.7, y, z, 4.6, C.steel, 6);
  };
  const bar = (parent, a, b, color) => {
    const dy = b[0] - a[0];
    const dz = b[1] - a[1];
    part(new THREE.BoxGeometry(15.3 * MM, Math.hypot(dy, dz) - 18 * MM, 12.8 * MM), color, parent, (m) => {
      m.position.set(0, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      m.rotation.x = Math.atan2(dz, dy);
    });
  };
  const box = (parent, sx, sy, sz, x, y, z, color) => part(new THREE.BoxGeometry(sx, sy, sz), color, parent, (m) => m.position.set(x, y, z));
  const J1 = [-80.1 * MM, HUB_Z - 9 * MM];
  const J2 = [J1[0] - 115 * MM * Math.cos(31 * DEG), J1[1] - 115 * MM * Math.sin(31 * DEG)];
  const J3 = [J2[0] - 130 * MM * Math.cos(22 * DEG), J2[1] - 130 * MM * Math.sin(22 * DEG)];
  const railTop = J3[0] - 16.4 * MM;
  const deskTop = railTop - 20 * MM;
  const plate = new THREE.Mesh(new THREE.ExtrudeGeometry(shapeOf(BOARDS.plate.body, BOARDS.plate.holes), {depth: 12.8 * MM, bevelEnabled: false}),
    look === 'default' ? solid(C.plate) : lambert(C.plate));
  plate.position.z = HUB_Z - 12.8 * MM;
  stages[0].add(plate);
  if (C.partEdge) {
    const edge = edgesOf(plate.geometry, C.partEdge, C.partEdgeAlpha, 40);
    edge.position.copy(plate.position);
    stages[0].add(edge);
  }
  for (const hole of BOARDS.plate.holes) {
    const [cx, cy] = centreOf(hole);
    part(new THREE.CylinderGeometry(2.8 * MM, 2.8 * MM, 2 * MM, 16), C.steel, stages[0], (m) => {
      m.rotation.x = Math.PI / 2;
      m.position.set(-cx * MM, cy * MM, HUB_Z - 13.8 * MM);
    });
  }
  knuckle(stages[0], J1, C.orange, C.black);
  bar(stages[0], J1, J2, C.orange);
  knuckle(stages[1], J2, C.black, C.orange);
  bar(stages[1], J2, J3, C.black);
  knuckle(stages[2], J3, C.orange, C.black);
  const rz = J3[1] - 1 * MM;
  box(stages[3], 60 * MM, 10 * MM, 20 * MM, 0, railTop + 5 * MM, rz, C.orange);                  // V-slot block
  box(stages[3], 0.6, 20 * MM, 20 * MM, 0, deskTop + 10 * MM, rz, C.alu);                           // rail under it, along the array
  const zFront = 0.12;
  box(stages[3], 20 * MM, 20 * MM, zFront - (rz + 10 * MM), 0, deskTop + 10 * MM, (zFront + rz + 10 * MM) / 2, C.alu);   // rail forward
  group.add(...stages);

  // Desk: a 0.8 × 0.5 m top, 25 mm thick, on legs down to the floor.
  const desk = new THREE.Group();
  const dz0 = -0.36;
  const dz1 = 0.16;
  const dx = 0.4;
  const top = 25 * MM;
  box(desk, 2 * dx, top, dz1 - dz0, 0, deskTop - top / 2, (dz0 + dz1) / 2, C.desk);
  const legH = deskTop - top - floorY;
  if (legH > 0) for (const x of [-dx + 0.03, dx - 0.03]) for (const z of [dz0 + 0.03, dz1 - 0.03]) box(desk, 0.03, legH, 0.03, x, floorY + legH / 2, z, C.leg);
  group.add(desk);

  // Arasaka: the emitter (the hub's octagon glowing on the floor under the desk) and the build's spark, which runs down the
  // mount from the plate to the rail.
  let emitter = null;
  let spark = null;
  const sparkPath = [new THREE.Vector3(0, J1[0], J1[1]), new THREE.Vector3(0, J2[0], J2[1]), new THREE.Vector3(0, J3[0], J3[1]),
    new THREE.Vector3(0, railTop, rz)];
  if (look === 'arasaka') {
    const octagon = shapeOf(BOARDS.hub.outline);
    emitter = new THREE.Group();
    const fill = new THREE.Mesh(new THREE.ShapeGeometry(octagon), new THREE.MeshBasicMaterial({color: C.edge, transparent: true, opacity: 0.12, depthWrite: false}));
    const rim = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pointsOf(BOARDS.hub.outline).map(v2)), lineMat(C.edge, 1));
    emitter.add(fill, rim);
    emitter.scale.setScalar(2.2);
    emitter.rotation.x = -Math.PI / 2;
    emitter.position.set(0, floorY + 2 * MM, 0);
    group.add(emitter);
    spark = new THREE.Mesh(new THREE.BoxGeometry(6 * MM, 26 * MM, 6 * MM),
      new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, blending: THREE.AdditiveBlending, depthTest: false}));
    spark.visible = false;
    group.add(spark);
  }

  return {
    group,
    palette: C,
    parts: {hub, petals, silk, shadows, pens, ghosts, chroma, mics, unused, stages, desk, emitter, spark},
    joints: {J1, J2, J3, railTop, deskTop, sparkPath, base: new THREE.Vector2(0, rz)},
    labelPoint: new THREE.Vector3(-0.27, -0.22, 0),
  };
}
