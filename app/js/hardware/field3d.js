/**
 * field3d.js — the console's 3D view: the array on its stand, the field on a
 * dome in front of it, and a beam from the array to the source. The field is
 * drawn as one tile per beam (bank) or sector, or as a height field that
 * bulges toward the array where the sound is louder (fieldstyles.js). All of
 * it comes from field.js, so the bank's even sine grid and the sector grid
 * draw the same way. Drag to orbit; click the field to pick a beam or sector.
 *
 * Scene axes: x = LEFT, y = up, z = forward, so a direction to the right
 * lands on the right of the screen when the camera is behind the array.
 */

import * as THREE from 'three';
import { Viewport3D, clearGroup } from '../shared/viewport3d.js';
import { levelToUnit, rampColor } from '../shared/colormap.js';
import { h, setText } from '../shared/dom.js';
import { direction } from './geometry.js';
import { cellDirection, gridDirection, gridPosition, median, surfaceUnit } from './field.js';
import { DEPTH, PointStyle, SurfaceStyle, pinAt } from './fieldstyles.js';

const RADIUS = 3.5;                    // far-field beams: the dome's distance is arbitrary, only its angles are real
const FLOOR_Y = -2.45;
const TARGET = new THREE.Vector3(0, -0.2, 1.9);
const DISTANCE = 9.5;
const EASE = 0.15;
const PEAK_SPREAD = 2 * 0.8 * 0.8;     // 2σ² of the hill at the source, in cells
const REST = 0.1;                      // relief of the background, so quieter directions can still dip below it
const KNEE = 0.05;                     // width of the soft floor under the relief
const FOCUS = 2;                       // the field lifts toward a point this many radii behind the array

export const CAMERA_VIEWS = {
  behind: {yaw: -14, pitch: 15},
  above: {yaw: 0, pitch: 62},
  side: {yaw: 78, pitch: 8, distance: 11.5},
};
const COLORS = {
  background: '#1d2025', line: '#2c3036', strong: '#3a3f47', subtle: '#80868e', text: '#eceef1',
  accent: '#7aa7ff', ok: '#4cc38a', warn: '#e2b454',
};

const toScene = ([right, up, forward], radius = 1) => new THREE.Vector3(-right * radius, up * radius, forward * radius);
const flat = (color, extra = {}) => new THREE.MeshBasicMaterial({color, side: THREE.DoubleSide, ...extra});
const srgb = (rgb) => new THREE.Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, THREE.SRGBColorSpace);

function arrow(value, positive, negative) {
  return !Number.isFinite(value) || Math.abs(value) < 0.5 ? '' : ` ${value > 0 ? positive : negative}`;
}

function tileGeometry(bounds, steps = 6) {
  const positions = [];
  const index = [];
  const row = steps + 1;
  for (let i = 0; i <= steps; i++) {
    for (let j = 0; j <= steps; j++) {
      const p = toScene(cellDirection(bounds, j / steps, i / steps), RADIUS);
      positions.push(p.x, p.y, p.z);
    }
  }
  for (let i = 0; i < steps; i++) {
    for (let j = 0; j < steps; j++) {
      const k = i * row + j;
      index.push(k, k + 1, k + row, k + 1, k + row + 1, k + row);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(index);
  return geometry;
}

function outlinePoints(bounds, radius, steps = 10) {
  const points = [];
  const edge = (s0, t0, s1, t1) => {
    for (let i = 0; i < steps; i++) {
      const f = i / steps;
      points.push(toScene(cellDirection(bounds, s0 + (s1 - s0) * f, t0 + (t1 - t0) * f), radius));
    }
  };
  edge(0, 0, 1, 0);
  edge(1, 0, 1, 1);
  edge(1, 1, 0, 1);
  edge(0, 1, 0, 0);
  return points;
}

export class FieldScene {
  constructor(container, {onPick, onHover} = {}) {
    this.container = container;
    this.onPick = onPick;
    this.onHover = onHover;
    this.viewport = new Viewport3D(container, {background: COLORS.background, fov: 34});
    Object.assign(this.viewport.controls, {enablePan: false, minDistance: 3, maxDistance: 12});
    this.labels = h('div', {class: 'scene-labels', 'aria-hidden': 'true'});
    container.append(this.labels);

    this.room = new THREE.Group();
    this.array = new THREE.Group();
    this.tiles = new THREE.Group();
    this.marks = new THREE.Group();
    this.styles = {surface: new SurfaceStyle(RADIUS), points: new PointStyle(RADIUS)};
    this.viewport.scene.add(this.room, this.array, this.tiles, this.marks, ...Object.values(this.styles).map((s) => s.group));
    this.buildRoom();

    this.key = '';
    this.style = 'tiles';
    this.shown = null;                // eased per-cell levels, 0..1 above the floor
    this.wanted = null;               // the same from the latest frame
    this.peak = {column: 0, row: 0, level: 0};      // eased source peak at its interpolated direction
    this.peakWanted = {column: 0, row: 0, level: 0};
    this.base = 0;                    // eased background (median cell level)
    this.baseWanted = 0;
    this.levelAt = (column, row) => this.level(column, row);
    this.reliefOf = (value) => this.relief(value);
    this.arrayKey = '';
    this.tileMeshes = [];
    this.field = null;
    this.hover = null;
    this.labelNodes = [];
    this.axisLabels = [];
    this.markerLabel = h('span', {class: 'scene-label scene-label--marker', hidden: true});
    this.arrayLabel = h('span', {class: 'scene-label scene-label--name scene-label--start'}, 'ARRAY');
    this.labels.append(this.markerLabel, this.arrayLabel);
    this.markerPoint = null;
    this.arrayPoint = new THREE.Vector3(-0.32, -0.12, 0);

    this.viewport.onFrame = () => {
      this.placeLabels();
      this.easeField();
    };
    this.bindPointer();
    this.setView('behind');
  }

  start() { this.viewport.start(); }
  stop() { this.viewport.stop(); }

  setView(name) {
    this.viewName = CAMERA_VIEWS[name] ? name : 'behind';
    this.viewport.orbitTo(TARGET, {distance: DISTANCE, ...CAMERA_VIEWS[this.viewName]});
    this.labels.classList.toggle('no-axes', this.viewName !== 'behind');
  }

  buildRoom() {
    const points = [];
    for (let i = 0; i <= 16; i++) {
      const x = -3.2 + i * 0.4;
      const z = -0.8 + i * 0.3;
      points.push(new THREE.Vector3(x, FLOOR_Y, -0.8), new THREE.Vector3(x, FLOOR_Y, 4.0),
        new THREE.Vector3(-3.2, FLOOR_Y, z), new THREE.Vector3(3.2, FLOOR_Y, z));
    }
    this.room.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({color: COLORS.line})));
  }

  /** The array as a plate with its microphones, on a stand down to the floor. */
  setArray(layout) {
    const key = layout?.positions ? JSON.stringify(layout.positions) : '';
    if (key === this.arrayKey) return;
    this.arrayKey = key;
    clearGroup(this.array);
    if (!layout?.positions?.length) return;
    const n = layout.positions.length;
    const mean = [0, 0];
    for (const [x, y] of layout.positions) { mean[0] += x / n; mean[1] += y / n; }
    const metres = layout.positions.map(([x, y]) => [(x - mean[0]) / 1000, (y - mean[1]) / 1000]);
    const reach = Math.max(...metres.map(([x, y]) => Math.hypot(x, y))) + 0.03;
    const plate = new THREE.Mesh(new THREE.CircleGeometry(reach, 64),
      flat(COLORS.strong, {transparent: true, opacity: 0.6}));
    const rim = new THREE.Mesh(new THREE.RingGeometry(reach - 0.007, reach + 0.003, 64), flat(COLORS.subtle));
    const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, -FLOOR_Y - reach, 12), flat(COLORS.strong));
    stand.position.set(0, (FLOOR_Y - reach) / 2, 0.01);
    const mics = new THREE.InstancedMesh(new THREE.SphereGeometry(0.012, 10, 8), flat(COLORS.text), n);
    const matrix = new THREE.Matrix4();
    metres.forEach(([x, y], i) => {
      matrix.makeTranslation(-x, y, -0.006);
      mics.setMatrixAt(i, matrix);
    });
    this.array.add(plate, rim, stand, mics);
    this.arrayPoint = new THREE.Vector3(-reach - 0.05, -reach * 0.8, 0);
    this.viewport.requestRender();
  }

  build(field) {
    if (field.key === this.key) return;
    this.key = field.key;
    clearGroup(this.tiles);
    this.tileMeshes = [];
    const gap = 0.012;
    for (const cell of field.cells) {
      const b = cell.bounds;
      const inset = b.space === 'sine'
        ? {...b, u0: b.u0 + gap / 2, u1: b.u1 - gap / 2, v0: b.v0 + gap / 2, v1: b.v1 - gap / 2}
        : {...b, a0: b.a0 + 0.6, a1: b.a1 - 0.6, e0: b.e0 + 0.6, e1: b.e1 - 0.6};
      const mesh = new THREE.Mesh(tileGeometry(inset), flat(COLORS.line, {transparent: true, opacity: 0.95}));
      mesh.userData = {index: cell.index};
      this.tiles.add(mesh);
      this.tileMeshes.push(mesh);
    }
    this.buildAxisLabels(field);
  }

  /** Axis titles just outside the grid: azimuth under the middle column, elevation left of the middle row. */
  buildAxisLabels(field) {
    this.axisLabels.forEach(({el}) => el.remove());
    const bottom = field.cells[Math.floor(field.columns / 2)].bounds;
    const left = field.cells[Math.floor(field.rows / 2) * field.columns].bounds;
    const azimuth = h('span', {class: 'scene-label scene-label--axis'},
      h('span', {class: 'tri'}, '◀'), 'AZIMUTH', h('span', {class: 'tri'}, '▶'));
    const elevation = h('span', {class: 'scene-label scene-label--axis scene-label--vertical scene-label--end'},
      h('span', {class: 'tri'}, '▲'), h('span', {class: 'scene-label__text'}, 'ELEVATION'), h('span', {class: 'tri'}, '▼'));
    this.labels.append(azimuth, elevation);
    this.axisLabels = [
      {el: azimuth, point: toScene(cellDirection(bottom, 0.5, -0.45), RADIUS)},
      {el: elevation, point: toScene(cellDirection(left, -0.8, 0.5), RADIUS)},
    ];
  }

  /**
   * Level 0..1 at a fractional grid position: the levels interpolated between
   * the beams, blended into a smooth hill topped at the source's interpolated direction.
   */
  level(column, row) {
    const value = surfaceUnit(this.field, this.shown, column, row);
    const peak = this.peak;
    if (peak.level <= 0) return value;
    const dc = column - peak.column;
    const dr = row - peak.row;
    return value + (peak.level - value) * Math.exp(-(dc * dc + dr * dr) / PEAK_SPREAD);
  }

  /** How far a level lifts the field: measured from the background, so the sheet does not heave with it. */
  relief(value) {
    const x = value - this.base + REST;
    return Math.min(1, (x + Math.sqrt(x * x + KNEE * KNEE)) / 2);
  }

  updateStyle() {
    const style = this.styles[this.style];
    if (!style || !this.shown || !this.field) return;
    const [right, up, forward] = gridDirection(this.field, this.peak.column, this.peak.row);
    const behind = -FOCUS * RADIUS;
    style.update(this.levelAt, this.reliefOf, [-right * behind, up * behind, forward * behind]);
  }

  /** Glide the height field toward the latest levels, one step per rendered frame. */
  easeField() {
    if (!this.styles[this.style] || !this.shown || this.wanted?.length !== this.shown.length) return;
    let moving = false;
    const glide = (from, to) => {
      if (Math.abs(to - from) <= 0.002) return to;
      moving = true;
      return from + (to - from) * EASE;
    };
    for (let i = 0; i < this.shown.length; i++) this.shown[i] = glide(this.shown[i], this.wanted[i]);
    for (const key of ['column', 'row', 'level']) this.peak[key] = glide(this.peak[key], this.peakWanted[key]);
    this.base = glide(this.base, this.baseWanted);
    if (moving) {
      this.updateStyle();
      this.viewport.requestRender();
    }
  }

  placeLabels() {
    const place = (el, point, dx = 0, dy = 0) => {
      const p = this.viewport.project(point);
      el.style.left = `${(p.x + dx).toFixed(1)}px`;
      el.style.top = `${(p.y + dy).toFixed(1)}px`;
      return p.visible;
    };
    for (const {el, point} of this.axisLabels) el.hidden = !place(el, point);
    this.arrayLabel.hidden = !this.arrayKey || !place(this.arrayLabel, this.arrayPoint);
    if (this.markerPoint) place(this.markerLabel, this.markerPoint, 16, 0);
  }

  /** state: {floor, session, style: 'tiles' | 'surface' | 'points'} */
  render(field, {floor, session, style = 'tiles'}) {
    if (!field) return;
    const changed = this.field?.key !== field.key || this.style !== style;
    this.field = field;
    this.style = this.styles[style] ? style : 'tiles';
    this.build(field);
    this.setArray(session.arrayLayout);
    const tiles = this.style === 'tiles';
    this.tiles.visible = tiles;
    for (const [name, entry] of Object.entries(this.styles)) entry.group.visible = name === this.style;
    if (tiles) {
      for (const mesh of this.tileMeshes) {
        const cell = field.cells[mesh.userData.index];
        const measured = Number.isFinite(cell.level);
        mesh.material.color.copy(measured ? srgb(rampColor(levelToUnit(cell.level, floor))) : new THREE.Color(COLORS.line));
        mesh.material.opacity = measured ? (cell.fresh ? 0.96 : 0.38) : 0.35;
      }
    } else {
      this.styles[this.style].build(field);
      this.wanted = Float32Array.from(field.cells, (cell) => levelToUnit(cell.level, floor) ?? 0);
      this.baseWanted = median(Array.from(this.wanted)) ?? 0;
      const source = field.marker;
      const at = source ? gridPosition(field, source.azimuth, source.elevation) : null;
      const first = this.shown?.length !== this.wanted.length;
      const rest = first ? {column: field.columns / 2, row: field.rows / 2} : this.peak;
      this.peakWanted = at
        ? {column: at.column, row: at.row, level: levelToUnit(source.cell?.level, floor) ?? 0}
        : {column: rest.column, row: rest.row, level: 0};
      if (first) {
        this.shown = Float32Array.from(this.wanted);
        this.peak = {...this.peakWanted};
        this.base = this.baseWanted;
        this.updateStyle();
      } else if (changed) {
        this.updateStyle();
      }
    }

    clearGroup(this.marks);
    const outline = (cell, color, radius = 0.01) => {
      const curve = new THREE.CatmullRomCurve3(outlinePoints(cell.bounds, RADIUS * 0.99), true, 'centripetal');
      this.marks.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 160, radius, 6, true), flat(color)));
    };
    const marker = field.marker;
    const hovered = tiles && this.hover !== null ? field.cells[this.hover] : null;
    if (hovered) {
      this.marks.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(outlinePoints(hovered.bounds, RADIUS * 0.985)),
        new THREE.LineBasicMaterial({color: COLORS.text})));
    }
    if (tiles && marker?.cell) outline(marker.cell, marker.kind === 'beam' ? COLORS.accent : '#ffffff');
    else if (tiles && field.strongest) outline(field.strongest, '#ffffff', 0.007);

    if (marker && Number.isFinite(marker.azimuth) && Number.isFinite(marker.elevation)) {
      let reach = RADIUS * 0.985;
      if (!tiles && this.shown) {
        const at = gridPosition(field, marker.azimuth, marker.elevation);
        const lift = at ? pinAt(field, at.column, at.row) * this.relief(this.level(at.column, at.row)) : 0;
        reach = RADIUS * (1 - DEPTH * lift) - 0.04;
      }
      const tip = toScene(direction(marker.azimuth, marker.elevation), reach);
      const up = new THREE.Vector3(0, 1, 0);
      const along = tip.clone().normalize();
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.01, 0.01, tip.length(), 12), flat(COLORS.accent));
      const cone = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.03, tip.length(), 48, 1, true),
        flat(COLORS.accent, {transparent: true, opacity: 0.16, depthWrite: false}));
      for (const mesh of [beam, cone]) {
        mesh.position.copy(tip).multiplyScalar(0.5);
        mesh.quaternion.setFromUnitVectors(up, along);
      }
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.03, 16, 12), flat('#ffffff'));
      dot.position.copy(tip);
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.08, 0.1, 48), flat('#ffffff'));
      ring.position.copy(tip);
      ring.lookAt(0, 0, 0);
      this.marks.add(beam, cone, dot, ring);
      this.markerPoint = tip;
      this.markerLabel.hidden = false;
      setText(this.markerLabel, `${Math.round(Math.abs(marker.azimuth))}°${arrow(marker.azimuth, '▶', '◀')}  `
        + `${Math.round(Math.abs(marker.elevation))}°${arrow(marker.elevation, '▲', '▼')}`);
    } else {
      this.markerPoint = null;
      this.markerLabel.hidden = true;
    }

    const truth = field.truth;
    if (truth && Number.isFinite(truth.azimuth_deg) && Number.isFinite(truth.elevation_deg)) {
      const color = truth.source_active === false ? COLORS.warn : COLORS.ok;
      const point = toScene(direction(truth.azimuth_deg, truth.elevation_deg), RADIUS * 1.04);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), point]),
        new THREE.LineBasicMaterial({color, transparent: true, opacity: 0.7}));
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 12), flat(color));
      dot.position.copy(point);
      this.marks.add(line, dot);
    }
    this.viewport.requestRender();
  }

  bindPointer() {
    const canvas = this.viewport.canvas;
    let down = null;
    canvas.addEventListener('pointerdown', (event) => { down = {x: event.clientX, y: event.clientY}; });
    canvas.addEventListener('pointermove', (event) => {
      if (event.buttons) return;
      const hit = this.viewport.pick(event, this.tileMeshes)[0];
      const index = hit ? hit.object.userData.index : null;
      if (index !== this.hover) {
        this.hover = index;
        canvas.style.cursor = index === null ? 'grab' : 'pointer';
        this.onHover?.(index);
      }
    });
    canvas.addEventListener('pointerleave', () => {
      if (this.hover !== null) {
        this.hover = null;
        this.onHover?.(null);
      }
    });
    canvas.addEventListener('pointerup', (event) => {
      if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 4) { down = null; return; }
      down = null;
      const hit = this.viewport.pick(event, this.tileMeshes)[0];
      if (hit) this.onPick?.(hit.object.userData.index);
    });
  }
}
