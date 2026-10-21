/**
 * build3d.js — the motion in the 3D views (Hardware and Present), on the canvas mockups' timelines (Main and Arasaka boards,
 * 2026-10-21), seconds from the start of the build:
 *
 * Panel, full (3.4 s): a pen draws the hub, then a petal every 0.11 s, and the paper lays in under each line; the mount joins
 * from the plate down to the rail from 1.15 s; the floor wakes at 1 s; the silkscreen at 1.3 s; tiles dither in column by
 * column from 1.5 s; each microphone flashes orange in DSP channel order from 1.6 s, 0.03 s apart; the target at 2.9 s.
 *
 * Arasaka, full (3.85 s): the same timeline, the petals sliding in to the hub instead of being drawn and the ghost outlines
 * drawn at 1.3 s; then the chromatic pulse: the hologram's outline swells and splits at 2.9 s, a spark runs down the mount,
 * the floor grid lights in a ring spreading from the base at 3.08 s, the sectors flash row by row from the bottom at 3.4 s.
 *
 * The app's own look (3.4 s) plays Arasaka's build without the chromatic pulse and without the emitter's ring: the user asked
 * for Arasaka's animations there, minus those two (2026-10-21).
 *
 * Quick (1 s, a tool switch: Map to 3D, or back from Present): the same steps overlapped, without the pulse.
 * Idle (nothing running): Panel's dotted sonar rings, a soft shade wave over the tiles and the microphones twinkling grey;
 * Arasaka's glow wave, the emitter's pulse ring and an occasional jitter; the app's own look, Arasaka's without the ring.
 * With prefers-reduced-motion nothing moves.
 */

import * as THREE from 'three';

const PANEL = {
  full: {pdt: 0.11, pd: 1.5, pfo: 0.7, pf: 0.35, dt0: 1.3, s4: 1.15, sdt: 0.08, fl: 1, m0: 1.6, mdt: 0.03, mf: 0.4, kd: 0.6, bm: 2.9,
    t0: 1.5, dc: 0.1, dr: 0.04, end: 3.4},
  quick: {pdt: 0.02, pd: 0.55, pfo: 0.25, pf: 0.25, dt0: 0.35, s4: 0.15, sdt: 0.03, fl: 0.1, m0: 0.35, mdt: 0.008, mf: 0.3, kd: 0.3,
    bm: 0.55, t0: 0.3, dc: 0.05, dr: 0.02, end: 1},
};
const ARASAKA = {
  full: {hub: [0, 0.5], pet0: 0.11, pdt: 0.11, pd: 1.05, floor: [1, 0.6], emit: [1.05, 0.5], pulse: [1.05, 0.8],
    ghost: [[1.3, 0.45], [1.38, 0.45]], m0: 1.6, mdt: 0.03, mf: 0.4, unused: [1.3, 0.4], tiles: [1.5, 0.8], beam: [2.9, 0.4],
    rays: [1.5, 0.45], tips: 1.7,
    mount: [1.15, 1.23, 1.31, 1.39], mountFade: 0.4, split: [2.92, 2.98, 3.05, 3.08], chroma: [2.9, 0.5], spark: [2.9, 0.18],
    wave: [3.08, 0.5], hits: [3.4, 0.16, 0.28], end: 3.85},
  quick: {hub: [0, 0.25], pet0: 0.05, pdt: 0.035, pd: 0.3, floor: [0.1, 0.3], emit: [0.05, 0.3], pulse: null,
    ghost: [[0.3, 0.35], [0.35, 0.35]], m0: 0.35, mdt: 0.005, mf: 0.25, unused: [0.35, 0.4], tiles: [0.3, 0.35], beam: [0.55, 0.35],
    rays: [0.3, 0.35], tips: 0.45,
    mount: [0.12, 0.12, 0.12, 0.12], mountFade: 0.3, split: null, chroma: null, spark: null, wave: null, hits: null, end: 1},
};
const DEFAULT = {
  full: {...ARASAKA.full, pulse: null, split: null, chroma: null, spark: null, wave: null, hits: null, end: 3.4},
  quick: ARASAKA.quick,
};
const TIMINGS = {panel: PANEL, arasaka: ARASAKA, default: DEFAULT};
const MOTION = {
  panel: {ringPeriod: 3.2, wavePeriod: 3.2, waveAlpha: 0.14, twinkle: 5, flash: '#ea5a00', settle: '#111111', grey: '#a3a3a0'},
  arasaka: {pulsePeriod: 2.4, wavePeriod: 2.4, waveAlpha: 0.19, flash: '#f2122a', settle: '#e9e3dc', jitter: 5.3, flicker: 5},
  default: {wavePeriod: 2.4, waveAlpha: 0.16, flash: '#7aa7ff', settle: '#eceef1', jitter: 5.3, flicker: 5},
};
const WHITE = new THREE.Color(0xffffff);
const DOCK = 0.148;        // how far out a petal starts, metres (1.5 × the mockup's 34 px at its scale)
const SPLIT = 0.017;       // the chromatic copies' sideways split, metres (6 px)

const clamp = (x) => Math.max(0, Math.min(1, x));

/** A CSS cubic-bezier timing function. */
function bezier(x1, y1, x2, y2) {
  const at = (a, b, t) => 3 * a * t * (1 - t) ** 2 + 3 * b * t * t * (1 - t) + t ** 3;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let lo = 0;
    let hi = 1;
    for (let i = 0; i < 24; i++) {
      const mid = (lo + hi) / 2;
      if (at(x1, x2, mid) < x) lo = mid; else hi = mid;
    }
    return at(y1, y2, (lo + hi) / 2);
  };
}
const easeOut = bezier(0, 0, 0.58, 1);
const easeInOut = bezier(0.42, 0, 0.58, 1);
const dock = bezier(0.25, 0.6, 0.25, 1);
const shoot = bezier(0.2, 0.75, 0.25, 1);
const swell = bezier(0.2, 0.7, 0.2, 1);
/** 0 → 1 over `duration` from `start`, eased out (the mockups' fade-ins). */
const fadeIn = (t, start, duration) => easeOut(clamp((t - start) / duration));

export function prefersReducedMotion() {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/** Fade an object: every material under it to `f` of its resting opacity (kept on the material the first time). */
function fade(object, f) {
  if (!object) return;
  object.traverse((o) => {
    for (const m of [o.material].flat()) {
      if (!m) continue;
      m.userData.rest ??= m.opacity;
      m.opacity = m.userData.rest * f;
    }
  });
  object.visible = f > 0.001;
}

/** The looks' floor is a shader that fades through its own uniform; the app's own is a grid of lines. */
function floorFade(floor, f) {
  if (floor.material.uniforms) floor.material.uniforms.uFade.value = f;
  else fade(floor, f);
  floor.visible = f > 0.001;
}

/**
 * A tile as the motion moves it: the looks' shader tiles (look3d.js) through their uniforms, the app's own plain tiles through
 * opacity and colour on top of what the scene last set for its data (mesh.userData.opacity and .color).
 */
export function tileHandle(mesh, halo = null) {
  const u = mesh.material.uniforms;
  const d = mesh.userData;
  const plain = () => {
    mesh.material.opacity = (d.opacity ?? 1) * (d.fadeIn ?? 1);
    if (d.color) mesh.material.color.copy(d.color).lerp(WHITE, d.wave ?? 0);
  };
  return {
    column: d.column,
    row: d.row,
    fade(f) {
      mesh.visible = f > 0.001;
      if (u) u.uFadeIn.value = f;
      else { d.fadeIn = f; plain(); }
    },
    wave(a) {
      if (u) u.uWave.value.w = a;
      else { d.wave = a; plain(); }
    },
    mask(m) { if (u) u.uMask.value = m; },
    flash(f) { if (u) u.uFlash.value = f; },
    haloFade(f) { if (halo) halo.material.uniforms.uOpacity.value = f; },
  };
}

/** Draw closed line strips on from their start: the pen (Panel) and the ghost outlines (Arasaka). */
function drawOn(object, f) {
  object.visible = f > 0;
  object.traverse((o) => { if (o.userData.count) o.geometry.setDrawRange(0, Math.ceil(o.userData.count * f)); });
}

export class Motion {
  /**
   * scene: {look ('panel', 'arasaka' or 'default'), viewport, parts: () => ({rig, floor, tiles: [tileHandle], columns, rows,
   * marks: [Object3D], labels: [HTMLElement]}), reach: {wave, ring, pulse, jitter}} — the reaches in metres for this scene's size.
   */
  constructor({look, viewport, parts, reach}) {
    this.look = look;
    this.viewport = viewport;
    this.parts = parts;
    this.reach = reach;
    this.build = null;
    this.idle = false;
    this.waving = false;
    this.clock0 = performance.now() / 1000;
    this.flash = new THREE.Color(MOTION[look].flash);
    this.settle = new THREE.Color(MOTION[look].settle);
    this.grey = MOTION[look].grey ? new THREE.Color(MOTION[look].grey) : null;
    this.color = new THREE.Color();
    viewport.animating = () => this.running;
  }

  /**
   * Start a build: 'full' or 'quick'. Every shader the build will use compiles first, off the main thread where the browser
   * allows (hidden parts included), with the view covered meanwhile, so the build starts on time instead of stalling.
   */
  play(kind) {
    if (prefersReducedMotion()) return;
    const {renderer, scene, camera, canvas} = this.viewport;
    const timing = TIMINGS[this.look][kind];
    const hidden = [];
    scene.traverse((o) => { if (!o.visible) { hidden.push(o); o.visible = true; } });
    const materials = renderer.compile(scene, camera);
    for (const o of hidden) o.visible = false;
    canvas.style.visibility = 'hidden';
    // Wait for the programs (KHR_parallel_shader_compile), at most 0.6 s. Not three's compileAsync: the marker meshes are
    // replaced with every data frame, and a material disposed while it waits leaves its promise unresolved. Only materials
    // still in the scene are asked (a disposed one's program is gone).
    const ready = () => {
      const live = new Set();
      scene.traverse((o) => { for (const m of [o.material].flat()) if (m) live.add(m); });
      return [...materials].every((m) => {
        const program = renderer.properties.get(m).currentProgram;
        return !live.has(m) || !program || program.isReady();
      });
    };
    const asked = performance.now();
    const begin = () => {
      canvas.style.visibility = '';
      this.build = {kind, start: performance.now() / 1000, timing};
      this.apply();
      this.viewport.requestRender();
    };
    const wait = () => (ready() || performance.now() - asked > 600 ? begin() : setTimeout(wait, 10));
    this.build = {kind, start: Infinity, timing};
    wait();
  }

  /** Idle motion while nothing runs; `wave` alone keeps just the tiles' glow wave going (Present's Beams view while searching). */
  setIdle(on, wave = on) {
    this.idle = Boolean(on) && !prefersReducedMotion();
    this.waving = Boolean(on || wave) && !prefersReducedMotion();
  }

  /** How many microphones the build has lit (1–44 as it counts them in), or null outside a build. */
  channel() {
    if (!this.build) return null;
    const T = this.build.timing;
    const count = this.parts().rig?.parts.mics.count ?? 44;
    const t = performance.now() / 1000 - this.build.start;
    return !(t >= T.m0) ? null : Math.min(count, Math.floor((t - T.m0) / T.mdt) + 1);
  }

  /** Whether the view must redraw every frame: during a build, while idle, and around the brief flickers (not Panel). */
  get running() {
    if (this.build || this.idle || this.waving) return true;
    const period = MOTION[this.look].flicker;
    if (!period || prefersReducedMotion()) return false;
    const phase = ((performance.now() / 1000 - this.clock0) / period) % 1;
    return (phase > 0.46 && phase < 0.5) || (phase > 0.7 && phase < 0.74);
  }

  /** Put every animated part where it is now. Called before each render and after the scene redraws its data. */
  apply() {
    const now = performance.now() / 1000;
    const p = this.parts();
    if (this.build && now - this.build.start > this.build.timing.end) {
      this.build = null;
      this.rest(p);
    }
    if (this.build) {
      const t = Math.max(-1, now - this.build.start);       // still compiling: everything held at its first frame
      if (this.look === 'panel') this.panelBuild(p, t, this.build.timing);
      else this.arasakaBuild(p, t, this.build.timing);
      return;
    }
    this.motionAt(p, now - this.clock0);
  }

  /** Everything at rest, as after a build. */
  rest(p) {
    const parts = p.rig?.parts;
    if (parts) {
      for (const o of [parts.hub, ...parts.petals, ...parts.silk, parts.unused, ...parts.stages, parts.desk, parts.emitter, ...parts.ghosts]) {
        if (o) fade(o, 1);
      }
      for (const petal of parts.petals) petal.position.set(0, 0, 0);
      for (const ghost of parts.ghosts) drawOn(ghost, 1);
      for (const pen of parts.pens) pen.visible = false;
      for (const copy of parts.chroma) copy.visible = false;
      if (parts.spark) parts.spark.visible = false;
      for (const stage of parts.stages) stage.traverse((o) => { if (o.material?.emissive) o.material.emissive.setRGB(0, 0, 0); });
      this.micsAt(parts, () => 1);
    }
    if (p.floor) {
      floorFade(p.floor, 1);
      if (p.floor.material.uniforms) p.floor.material.uniforms.uWave.value = 0;
    }
    for (const tile of p.tiles) {
      tile.mask(0);
      tile.fade(1);
      tile.flash(0);
      tile.haloFade(1);
    }
    for (const mark of p.marks) { mark.scale.setScalar(1); fade(mark, 1); }
    for (const label of p.labels) if (label) label.style.opacity = '';
    if (p.rays) p.rays.scale.setScalar(1);
    if (p.tips) p.tips.visible = true;
  }

  /**
   * Microphones: hidden until their turn, then the flash colour, fading to their own colour from halfway. `at(c)` is how far
   * channel c is through its flash (below 0 before it, 1 or more after).
   */
  micsAt(parts, at) {
    const mics = parts.mics;
    const places = mics.userData.places;
    const matrix = new THREE.Matrix4();
    places.forEach((place, c) => {
      const u = at(c);
      const shown = u >= 0 ? 1 : 0;
      matrix.makeScale(shown, shown, 1).setPosition(place);
      mics.setMatrixAt(c, matrix);
      this.color.copy(this.flash).lerp(this.settle, clamp((u - 0.5) / 0.5));
      mics.setColorAt(c, u >= 1 ? mics.userData.color : this.color);
    });
    mics.instanceMatrix.needsUpdate = true;
    if (mics.instanceColor) mics.instanceColor.needsUpdate = true;
  }

  marksAt(p, f, scale = 1) {
    for (const mark of p.marks) { fade(mark, f); mark.scale.setScalar(Math.max(0.01, scale)); }
    for (const label of p.labels) if (label) label.style.opacity = String(f);
  }

  panelBuild(p, t, T) {
    const parts = p.rig?.parts;
    if (parts) {
      const paper = (i) => fadeIn(t, i * T.pdt + T.pfo, T.pf);
      fade(parts.hub, paper(0));
      parts.petals.forEach((petal, k) => fade(petal, paper(k + 1)));
      for (const s of parts.silk) fade(s, fadeIn(t, T.dt0, 0.4));
      fade(parts.unused, fadeIn(t, T.dt0, 0.4));
      parts.pens.forEach((pen, i) => {
        const u = (t - i * T.pdt) / T.pd;
        drawOn(pen, u <= 0 || u >= 1 ? 0 : easeInOut(clamp(u / 0.55)));
        pen.material.opacity = u < 0.75 ? 1 : 1 - (u - 0.75) / 0.25;
      });
      parts.stages.forEach((stage, s) => fade(stage, fadeIn(t, T.s4 + s * T.sdt, 0.4)));
      fade(parts.desk, fadeIn(t, T.fl, 0.6));
      this.micsAt(parts, (c) => (t - (T.m0 + c * T.mdt)) / T.mf);
    }
    if (p.floor) floorFade(p.floor, fadeIn(t, T.fl, 0.6));
    for (const tile of p.tiles) {
      const md = T.t0 + tile.column * T.dc + (p.rows - 1 - tile.row) * T.dr;
      const u = (t - md) / T.kd;
      tile.mask(u < 0 ? 4 : u >= 1 ? 0 : 4 - Math.floor(u * 4));
      tile.haloFade(fadeIn(t, md + T.kd, 0.2));
    }
    this.marksAt(p, fadeIn(t, T.bm, 0.4));
  }

  arasakaBuild(p, t, T) {
    const parts = p.rig?.parts;
    if (parts) {
      fade(parts.hub, fadeIn(t, ...T.hub));
      parts.petals.forEach((petal, k) => {
        const u = clamp((t - (T.pet0 + k * T.pdt)) / T.pd);
        fade(petal, clamp(u / 0.4));
        petal.position.copy(petal.userData.direction).multiplyScalar(DOCK * (1 - dock(u)));
      });
      parts.ghosts.forEach((ghost, g) => drawOn(ghost, easeInOut(clamp((t - T.ghost[g][0]) / T.ghost[g][1]))));
      fade(parts.unused, fadeIn(t, ...T.unused));
      parts.stages.forEach((stage, s) => {
        fade(stage, fadeIn(t, T.mount[s], T.mountFade));
        const flash = T.split ? clamp((t - T.split[s]) / 0.3) : 0;
        const k = flash > 0 && flash < 1 ? (flash < 0.3 ? flash / 0.3 : 1 - (flash - 0.3) / 0.7) : 0;
        stage.traverse((o) => { if (o.material?.emissive) o.material.emissive.setRGB(k * 0.7, k * 0.7, k * 0.7); });
      });
      fade(parts.desk, fadeIn(t, ...T.floor));
      if (parts.emitter) fade(parts.emitter, fadeIn(t, ...T.emit));
      this.micsAt(parts, (c) => (t - (T.m0 + c * T.mdt)) / T.mf);
      // The chromatic pulse: a white copy of the outline swells and fades while cyan and magenta copies split left and right.
      if (T.chroma) {
        const u = clamp((t - T.chroma[0]) / T.chroma[1]);
        const live = u > 0 && u < 1;
        const e = swell(u);
        const peak = (v, top) => (v < 0.15 ? v / 0.15 : 1 - (v - 0.15) / 0.85) * top;
        [[0, 0.12, 1, 0], [1, 0.16, 0.9, 1], [2, 0.08, 0.9, -1]].forEach(([i, grow, top, side]) => {
          const copy = parts.chroma[i];
          copy.visible = live;
          if (!live) return;
          copy.scale.setScalar(1 + grow * e);
          copy.position.x = side * SPLIT * (u < 0.15 ? u / 0.15 : 1 - 0.67 * (u - 0.15) / 0.85);
          copy.traverse((o) => { if (o.material) o.material.opacity = peak(u, top); });
        });
      }
      // The spark runs down the mount, plate to rail.
      if (T.spark && parts.spark) {
        const u = (t - T.spark[0]) / T.spark[1];
        parts.spark.visible = u > 0 && u < 1;
        if (parts.spark.visible) {
          const path = p.rig.joints.sparkPath;
          const lengths = path.slice(1).map((q, i) => q.distanceTo(path[i]));
          let d = u * lengths.reduce((a, b) => a + b, 0);
          let i = 0;
          while (i < lengths.length - 1 && d > lengths[i]) d -= lengths[i++];
          parts.spark.position.lerpVectors(path[i], path[i + 1], d / lengths[i]);
          parts.spark.material.opacity = u < 0.12 ? u / 0.12 : u > 0.88 ? (1 - u) / 0.12 : 1;
        }
      }
    }
    if (p.floor) floorFade(p.floor, fadeIn(t, ...T.floor));
    if (p.floor?.material.uniforms) {
      const u = p.floor.material.uniforms;
      const wave = T.wave ? (t - T.wave[0]) / T.wave[1] : -1;
      u.uWave.value = wave > 0 && wave < 1 ? (0.02 + 0.98 * wave) * this.reach.wave : 0;
      u.uWaveAlpha.value = wave < 0.04 ? wave / 0.04 : wave > 0.8 ? (1 - wave) / 0.2 : 1;
      if (p.rig) u.uWaveCentre.value.copy(p.rig.joints.base);
      const pulse = T.pulse ? clamp((t - T.pulse[0]) / T.pulse[1]) : 0;
      const live = pulse > 0 && pulse < 1;
      u.uRings.value.set(live ? (0.08 + 0.92 * easeOut(pulse)) * this.reach.pulse : 0, live ? (pulse < 0.08 ? pulse / 0.08 : 1 - (pulse - 0.08) / 0.92) : 0, 0, 0);
    }
    const tiles = fadeIn(t, ...T.tiles);
    for (const tile of p.tiles) {
      tile.fade(tiles);
      tile.haloFade(tiles);
      let flash = 0;
      if (T.hits) {
        const u = (t - (T.hits[0] + tile.row * T.hits[1] / Math.max(1, p.rows - 1))) / T.hits[2];
        if (u > 0 && u < 1) flash = u < 0.25 ? u / 0.25 * 0.8 : u < 0.6 ? 0.8 - (u - 0.25) / 0.35 * 0.35 : 0.45 * (1 - (u - 0.6) / 0.4);
      }
      tile.flash(flash);
    }
    // Present's Beams display: the rays shoot out from the array, then their tips appear.
    if (p.rays) p.rays.scale.setScalar(Math.max(0.01, shoot(clamp((t - T.rays[0]) / T.rays[1]))));
    if (p.tips) p.tips.visible = t >= T.tips;
    const beam = clamp((t - T.beam[0]) / T.beam[1]);
    this.marksAt(p, beam > 0 ? 1 : 0, shoot(beam));
    for (const label of p.labels) if (label) label.style.opacity = String(fadeIn(t, ...T.beam));
  }

  /** Idle and ambient motion between builds. */
  motionAt(p, t) {
    const M = MOTION[this.look];
    const idle = this.idle;
    const parts = p.rig?.parts;
    const centre = [(p.columns - 1) / 2, (p.rows - 1) / 2];
    const wave = (tile) => {
      if (!this.waving) return 0;
      const phase = ((t - Math.hypot(tile.column - centre[0], tile.row - centre[1]) * 0.3) / M.wavePeriod) % 1;
      return M.waveAlpha * (0.5 - 0.5 * Math.cos(2 * Math.PI * (phase < 0 ? phase + 1 : phase)));
    };
    for (const tile of p.tiles) tile.wave(wave(tile));
    const rings = p.floor?.material.uniforms?.uRings.value;
    if (this.look === 'panel') {
      if (rings) {
        const ring = (k) => ((t / M.ringPeriod) + k * 0.5) % 1;
        const [a, b] = [ring(0), ring(1)];
        if (idle) rings.set((0.08 + 0.92 * a) * this.reach.ring, 0.9 * (1 - a), (0.08 + 0.92 * b) * this.reach.ring, 0.9 * (1 - b));
        else rings.set(0, 0, 0, 0);
      }
      if (parts) {
        // Microphones twinkle grey now and then, each on its own beat.
        const mics = parts.mics;
        mics.userData.places.forEach((_, c) => {
          let k = 0;
          if (idle) {
            const phase = ((t + ((c * 7919) % 97) / 97 * M.twinkle) / M.twinkle) % 1;
            k = phase < 0.04 ? phase / 0.04 : phase < 0.08 ? 1 : 1 - (phase - 0.08) / 0.92;
          }
          mics.setColorAt(c, this.color.copy(mics.userData.color).lerp(this.grey, easeInOut(k)));
        });
        if (mics.instanceColor) mics.instanceColor.needsUpdate = true;
      }
      return;
    }
    // Arasaka: the emitter breathes and sends a pulse ring across the floor; the feed jitters now and then; the hologram flickers.
    // The app's own look: the same without the emitter and its ring.
    if (M.pulsePeriod) {
      const pulse = (t / M.pulsePeriod) % 1;
      if (rings) rings.set(idle ? (0.08 + 0.92 * easeOut(pulse)) * this.reach.pulse : 0, idle ? (pulse < 0.08 ? pulse / 0.08 : 1 - (pulse - 0.08) / 0.92) : 0, 0, 0);
      if (parts?.emitter) fade(parts.emitter, idle ? 1 - 0.55 * (0.5 - 0.5 * Math.cos(2 * Math.PI * pulse)) : 1);
    }
    const jitter = (t / M.jitter) % 1;
    this.viewport.scene.position.x = !idle ? 0 : jitter >= 0.89 && jitter < 0.905 ? this.reach.jitter : jitter >= 0.905 && jitter < 0.92 ? -0.75 * this.reach.jitter : 0;
    const flicker = (t / M.flicker) % 1;
    const holo = flicker >= 0.47 && flicker < 0.49 ? 0.7 : flicker >= 0.71 && flicker < 0.73 ? 0.9 : 1;
    if (parts) {
      fade(parts.hub, holo);
      for (const petal of parts.petals) fade(petal, holo);
    }
  }
}
