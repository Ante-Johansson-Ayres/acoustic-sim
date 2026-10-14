/**
 * fieldstyles.js — the 3D field as a height field instead of tiles: one
 * continuous surface, ridgelines, or pin art. All three bulge toward the
 * array by the same smooth height (0..1), sampled at fractional grid
 * positions (0 = left / bottom edge of the field).
 */

import * as THREE from 'three';
import { clearGroup } from '../shared/viewport3d.js';
import { rampColor } from '../shared/colormap.js';
import { gridDirection } from './field.js';

export const DEPTH = 0.65;          // a full-scale level pulls the field this fraction of the radius toward the array
const WELL = '#1d2025';

const LUT = Array.from({length: 256}, (_, i) => {
  const rgb = rampColor(i / 255);
  return new THREE.Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, THREE.SRGBColorSpace);
});
const colorOf = (value) => LUT[Math.round(Math.max(0, Math.min(1, value)) * 255)];

/** 1 inside the field, easing to 0 over the outer half cell so the rim stays on the dome. */
export function pinAt(field, column, row) {
  const t = Math.max(0, Math.min(1, Math.min(column, field.columns - column, row, field.rows - row) / 0.5));
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
  return {n, positions, pins, directions};
}

/** Displaced positions, and colours lifted by `tint` so low values stay visible on the dark well. */
function displace(samples, height, radius, out, colors = null, tint = 0) {
  const {n, positions, pins, directions} = samples;
  for (let k = 0; k < n; k++) {
    const value = height(positions[2 * k], positions[2 * k + 1]);
    const r = radius * (1 - DEPTH * pins[k] * value);
    out[3 * k] = directions[3 * k] * r;
    out[3 * k + 1] = directions[3 * k + 1] * r;
    out[3 * k + 2] = directions[3 * k + 2] * r;
    if (colors) {
      const color = colorOf(tint + (1 - tint) * value);
      colors[3 * k] = color.r;
      colors[3 * k + 1] = color.g;
      colors[3 * k + 2] = color.b;
    }
  }
}

/** One continuous shaded sheet with a fine mesh, like a surface plot. */
export class SurfaceStyle {
  constructor(radius) {
    this.radius = radius;
    this.group = new THREE.Group();
    this.key = '';
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
    const lines = [];
    for (let j = 0; j <= ny; j++) {
      for (let i = 0; i <= nx; i++) {
        const k = j * (nx + 1) + i;
        positions[2 * k] = i / steps;
        positions[2 * k + 1] = j / steps;
        if (i < nx && j < ny) faces.push(k, k + 1, k + nx + 1, k + 1, k + nx + 2, k + nx + 1);
        if (i < nx && j % 4 === 0) lines.push(k, k + 1);
        if (j < ny && i % 4 === 0) lines.push(k, k + nx + 1);
      }
    }
    this.samples = samplePoints(field, positions);
    this.position = new THREE.BufferAttribute(new Float32Array(this.samples.n * 3), 3);
    this.color = new THREE.BufferAttribute(new Float32Array(this.samples.n * 3), 3);
    this.skin = new THREE.BufferGeometry();
    this.skin.setAttribute('position', this.position);
    this.skin.setAttribute('color', this.color);
    this.skin.setIndex(faces);
    this.wire = new THREE.BufferGeometry();
    this.wire.setAttribute('position', this.position);
    this.wire.setIndex(lines);
    // Faces sit slightly behind the mesh lines so the lines do not flicker.
    const shade = new THREE.MeshLambertMaterial({vertexColors: true, side: THREE.DoubleSide,
      color: new THREE.Color().setRGB(0.8, 0.8, 0.8), polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1});
    this.group.add(new THREE.Mesh(this.skin, shade),
      new THREE.LineSegments(this.wire, new THREE.LineBasicMaterial({color: '#000000', transparent: true, opacity: 0.35})));
  }

  update(height) {
    displace(this.samples, height, this.radius, this.position.array, this.color.array);
    this.position.needsUpdate = true;
    this.color.needsUpdate = true;
    this.skin.computeVertexNormals();
    this.skin.computeBoundingSphere();
    this.wire.computeBoundingSphere();
  }
}

/** Lines across the field at fixed elevations, each hiding the ones behind it (Unknown Pleasures). */
export class RidgeStyle {
  constructor(radius) {
    this.radius = radius;
    this.group = new THREE.Group();
    this.key = '';
  }

  build(field) {
    if (field.key === this.key) return;
    this.key = field.key;
    clearGroup(this.group);
    const ridges = field.rows * 4;
    const points = field.columns * 16 + 1;
    const positions = new Float32Array(ridges * points * 2);
    const segments = [];
    const quads = [];
    const n = ridges * points;
    for (let r = 0; r < ridges; r++) {
      for (let i = 0; i < points; i++) {
        const k = r * points + i;
        positions[2 * k] = field.columns * i / (points - 1);
        positions[2 * k + 1] = field.rows * (r + 0.5) / ridges;
        if (i < points - 1) {
          segments.push(k, k + 1);
          quads.push(k, k + 1, n + k, k + 1, n + k + 1, n + k);
        }
      }
    }
    this.samples = samplePoints(field, positions);
    this.tops = new THREE.BufferAttribute(new Float32Array(n * 3), 3);
    this.color = new THREE.BufferAttribute(new Float32Array(n * 3), 3);
    const skirtPoints = new Float32Array(n * 6);
    const {directions} = this.samples;
    for (let k = 0; k < n * 3; k++) skirtPoints[n * 3 + k] = directions[k] * this.radius;
    this.skirt = new THREE.BufferAttribute(skirtPoints, 3);
    const line = new THREE.BufferGeometry();
    line.setAttribute('position', this.tops);
    line.setAttribute('color', this.color);
    line.setIndex(segments);
    const skirt = new THREE.BufferGeometry();
    skirt.setAttribute('position', this.skirt);
    skirt.setIndex(quads);
    this.geometries = [line, skirt];
    this.group.add(
      new THREE.Mesh(skirt, new THREE.MeshBasicMaterial({color: WELL, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1})),
      new THREE.LineSegments(line, new THREE.LineBasicMaterial({vertexColors: true})));
  }

  update(height) {
    displace(this.samples, height, this.radius, this.tops.array, this.color.array, 0.35);
    this.skirt.array.set(this.tops.array, 0);
    this.tops.needsUpdate = true;
    this.color.needsUpdate = true;
    this.skirt.needsUpdate = true;
    for (const geometry of this.geometries) geometry.computeBoundingSphere();
  }
}

/** Pins on a fixed grid that push their heads toward the array (pin art). */
export class PinStyle {
  constructor(radius) {
    this.radius = radius;
    this.group = new THREE.Group();
    this.key = '';
    this.matrix = new THREE.Matrix4();
    this.place = new THREE.Vector3();
    this.size = new THREE.Vector3();
    this.still = new THREE.Quaternion();
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
    const {n, directions} = this.samples;
    const up = new THREE.Vector3(0, 1, 0);
    this.turns = Array.from({length: n}, (_, k) => new THREE.Quaternion().setFromUnitVectors(up,
      new THREE.Vector3(directions[3 * k], directions[3 * k + 1], directions[3 * k + 2])));
    this.rods = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.008, 0.008, 1, 6),
      new THREE.MeshBasicMaterial({color: '#5a5f68'}), n);
    this.heads = new THREE.InstancedMesh(new THREE.SphereGeometry(0.03, 10, 8), new THREE.MeshLambertMaterial(), n);
    for (let k = 0; k < n; k++) this.heads.setColorAt(k, LUT[0]);
    this.rods.frustumCulled = false;
    this.heads.frustumCulled = false;
    this.group.add(this.rods, this.heads);
  }

  update(height) {
    const {n, positions, pins, directions} = this.samples;
    const {matrix, place, size, still} = this;
    for (let k = 0; k < n; k++) {
      const value = height(positions[2 * k], positions[2 * k + 1]);
      const head = this.radius * (1 - DEPTH * pins[k] * value);
      const length = Math.max(0.002, this.radius - head);
      const middle = this.radius - length / 2;
      place.set(directions[3 * k] * middle, directions[3 * k + 1] * middle, directions[3 * k + 2] * middle);
      matrix.compose(place, this.turns[k], size.set(1, length, 1));
      this.rods.setMatrixAt(k, matrix);
      place.set(directions[3 * k] * head, directions[3 * k + 1] * head, directions[3 * k + 2] * head);
      matrix.compose(place, still, size.set(1, 1, 1));
      this.heads.setMatrixAt(k, matrix);
      this.heads.setColorAt(k, colorOf(value));
    }
    this.rods.instanceMatrix.needsUpdate = true;
    this.heads.instanceMatrix.needsUpdate = true;
    this.heads.instanceColor.needsUpdate = true;
  }
}
