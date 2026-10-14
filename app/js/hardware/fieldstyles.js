/**
 * fieldstyles.js — the 3D field as a height field instead of tiles: a smooth
 * surface in colour bands, or a point cloud. Both bulge toward the array by
 * the relief of the level (how far it stands above the background), with the
 * rim held on the dome, and take their colour from the level itself. Both are
 * sampled at fractional grid positions (0 = left / bottom edge of the field).
 */

import * as THREE from 'three';
import { clearGroup } from '../shared/viewport3d.js';
import { rampColor } from '../shared/colormap.js';
import { gridDirection } from './field.js';

export const DEPTH = 0.65;          // a full relief pulls the field this fraction of the radius toward the array
const BANDS = 20;

const LUT = Array.from({length: 256}, (_, i) => {
  const rgb = rampColor(i / 255);
  return new THREE.Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, THREE.SRGBColorSpace);
});
const colorOf = (value) => LUT[Math.round(Math.max(0, Math.min(1, value)) * 255)];

/** The colour ramp in flat bands, sampled by height so the surface shades like a contour plot. */
function bandTexture() {
  const data = new Uint8Array(BANDS * 4);
  for (let i = 0; i < BANDS; i++) {
    const rgb = rampColor((i + 0.5) / BANDS);
    data.set([Math.round(rgb[0]), Math.round(rgb[1]), Math.round(rgb[2]), 255], 4 * i);
  }
  const texture = new THREE.DataTexture(data, BANDS, 1);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

/** 1 inside the field, easing to 0 over the outer cell so the rim stays on the dome without a steep wall. */
export function pinAt(field, column, row) {
  const t = Math.max(0, Math.min(1, Math.min(column, field.columns - column, row, field.rows - row)));
  return t * t * (3 - 2 * t);
}

/** Rim weights and scene directions (x = left) for fractional grid positions [column, row, ...]. */
function samplePoints(field, positions) {
  const n = positions.length / 2;
  const pins = new Float32Array(n);
  const directions = new Float32Array(n * 3);
  for (let k = 0; k < n; k++) {
    pins[k] = pinAt(field, positions[2 * k], positions[2 * k + 1]);
    const [right, up, forward] = gridDirection(field, positions[2 * k], positions[2 * k + 1]);
    directions[3 * k] = -right;
    directions[3 * k + 1] = up;
    directions[3 * k + 2] = forward;
  }
  return {n, positions, pins, directions, values: new Float32Array(n)};
}

/**
 * Moves each sample toward `focus` (a point far behind the array, on the source's axis) by its relief,
 * keeping the levels (for colour) in `samples.values`. Paths from one point never cross, so the sheet
 * cannot fold; the far focus keeps a hill's width as it rises, and the hilltop stays on the beam.
 */
function displace(samples, level, relief, focus, radius, out) {
  const {n, positions, pins, directions, values} = samples;
  for (let k = 0; k < n; k++) {
    values[k] = level(positions[2 * k], positions[2 * k + 1]);
    const x = directions[3 * k] * radius;
    const y = directions[3 * k + 1] * radius;
    const z = directions[3 * k + 2] * radius;
    const dx = focus[0] - x;
    const dy = focus[1] - y;
    const dz = focus[2] - z;
    const step = DEPTH * radius * pins[k] * relief(values[k]) / Math.hypot(dx, dy, dz);
    out[3 * k] = x + dx * step;
    out[3 * k + 1] = y + dy * step;
    out[3 * k + 2] = z + dz * step;
  }
}

/** One smooth sheet in colour bands with a thin outline, like a terrain or level plot. */
export class SurfaceStyle {
  constructor(radius) {
    this.radius = radius;
    this.group = new THREE.Group();
    this.key = '';
    this.bands = bandTexture();
  }

  build(field) {
    if (field.key === this.key) return;
    this.key = field.key;
    clearGroup(this.group);
    const steps = 16;
    const nx = field.columns * steps;
    const ny = field.rows * steps;
    const positions = new Float32Array((nx + 1) * (ny + 1) * 2);
    const faces = [];
    const edges = [];
    for (let j = 0; j <= ny; j++) {
      for (let i = 0; i <= nx; i++) {
        const k = j * (nx + 1) + i;
        positions[2 * k] = i / steps;
        positions[2 * k + 1] = j / steps;
        if (i < nx && j < ny) faces.push(k, k + 1, k + nx + 1, k + 1, k + nx + 2, k + nx + 1);
        if (i < nx && (j === 0 || j === ny)) edges.push(k, k + 1);
        if (j < ny && (i === 0 || i === nx)) edges.push(k, k + nx + 1);
      }
    }
    this.samples = samplePoints(field, positions);
    this.position = new THREE.BufferAttribute(new Float32Array(this.samples.n * 3), 3);
    this.uv = new THREE.BufferAttribute(new Float32Array(this.samples.n * 2).fill(0.5), 2);
    this.skin = new THREE.BufferGeometry();
    this.skin.setAttribute('position', this.position);
    this.skin.setAttribute('uv', this.uv);
    this.skin.setIndex(faces);
    this.outline = new THREE.BufferGeometry();
    this.outline.setAttribute('position', this.position);
    this.outline.setIndex(edges);
    // Faces sit slightly behind the outline so it does not flicker.
    const shade = new THREE.MeshLambertMaterial({map: this.bands, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1});
    this.group.add(new THREE.Mesh(this.skin, shade),
      new THREE.LineSegments(this.outline, new THREE.LineBasicMaterial({color: '#000000'})));
  }

  update(level, relief, focus) {
    displace(this.samples, level, relief, focus, this.radius, this.position.array);
    const {n, values} = this.samples;
    for (let k = 0; k < n; k++) this.uv.array[2 * k] = values[k];
    this.position.needsUpdate = true;
    this.uv.needsUpdate = true;
    this.skin.computeVertexNormals();
    this.skin.computeBoundingSphere();
    this.outline.computeBoundingSphere();
  }
}

/** Points on a fixed grid, each pushed toward the array by its relief. */
export class PointStyle {
  constructor(radius) {
    this.radius = radius;
    this.group = new THREE.Group();
    this.key = '';
    this.matrix = new THREE.Matrix4();
  }

  build(field) {
    if (field.key === this.key) return;
    this.key = field.key;
    clearGroup(this.group);
    const perCell = 5;
    const nx = field.columns * perCell;
    const ny = field.rows * perCell;
    const positions = new Float32Array(nx * ny * 2);
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        positions[2 * (j * nx + i)] = (i + 0.5) / perCell;
        positions[2 * (j * nx + i) + 1] = (j + 0.5) / perCell;
      }
    }
    this.samples = samplePoints(field, positions);
    this.placed = new Float32Array(this.samples.n * 3);
    this.points = new THREE.InstancedMesh(new THREE.SphereGeometry(0.05, 10, 8), new THREE.MeshLambertMaterial(), this.samples.n);
    for (let k = 0; k < this.samples.n; k++) this.points.setColorAt(k, LUT[0]);
    this.points.frustumCulled = false;
    this.group.add(this.points);
  }

  update(level, relief, focus) {
    displace(this.samples, level, relief, focus, this.radius, this.placed);
    const {n, values} = this.samples;
    const p = this.placed;
    for (let k = 0; k < n; k++) {
      this.points.setMatrixAt(k, this.matrix.makeTranslation(p[3 * k], p[3 * k + 1], p[3 * k + 2]));
      this.points.setColorAt(k, colorOf(values[k]));
    }
    this.points.instanceMatrix.needsUpdate = true;
    this.points.instanceColor.needsUpdate = true;
  }
}
