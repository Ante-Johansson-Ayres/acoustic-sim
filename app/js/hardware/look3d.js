/**
 * look3d.js — how the hidden looks (look.js) draw the 3D field, as their canvas mockups: tiles in six dither steps with a
 * five-cell meter, a halo behind each tile for the target ring (Panel) or bracket corners and glow (Arasaka), and the floor
 * (Panel's dots, Arasaka's red grid) with its idle rings and Arasaka's build wave. Dithers are screen-aligned pixels, as in
 * the mockups' CSS. The app's own look does not use this file.
 */

import * as THREE from 'three';

/** Halo margin round a tile, in tile widths (28 of the mockups' 96 px tiles: room for the glow, ring and brackets). */
export const HALO_OUTSET = 28 / 96;

const box = (right, top, width) => new THREE.Vector4(1 - (right + width) / 96, top / 96, 1 - right / 96, 1 - top / 96);

const LOOKS = {
  panel: {
    tile: {off: '#dfdfdd', ink: '#111111', target: '#ea5a00', emptyFrom: 6, radius: 6 / 96, edge: ['#111111', 0],
      meterBox: box(6, 6, 14), meterBg: ['#f9f9f8', 1], meterOff: ['#111111', 0.08], meterOn: '#111111', meterOnTarget: '#ea5a00',
      wave: '#111111', waveAlpha: 0.14},
    halo: {ring: 1, ringColor: '#111111', glow: null},
    floor: {kind: 0, color: '#111111', alpha: 0.32, spacing: 0.33, dot: 0.0125, solid: 0.35, ring: '#111111', ringAlpha: 0.9, ringWidth: 0.03, dotted: 1},
  },
  arasaka: {
    tile: {off: '#0e0a0b', ink: '#f2122a', target: '#f2122a', emptyFrom: 4, halfStep: 3, radius: 0, edge: ['#f2122a', 0.24],
      meterBox: box(4, 4, 12), meterBg: ['#07080a', 0.8], meterOff: ['#f2122a', 0.12], meterOn: '#f2122a', meterOnTarget: '#e9e3dc',
      wave: '#f2122a', waveAlpha: 0.19},
    halo: {ring: 2, ringColor: '#e9e3dc', glow: ['#f2122a', 0.45]},
    floor: {kind: 1, color: '#f2122a', alpha: 0.24 * 0.55, spacing: 0.4, dot: 0, solid: 0.3, ring: '#f2122a', ringAlpha: 1, ringWidth: 0.05, dotted: 0},
  },
};

export function lookStyle(look) {
  return LOOKS[look] || null;
}

const VERTEX = /* glsl */`
varying vec2 vUv;
varying vec3 vWorld;
void main() {
  vUv = uv;
  vec4 world = modelMatrix * vec4(position, 1.0);
  vWorld = world.xyz;
  gl_Position = projectionMatrix * viewMatrix * world;
}`;

// Coverage of dither step s (0 solid, then 3/4, 1/2, 1/4, 1/8, 1/16) at screen pixel p, as the mockups' CSS patterns.
const PATTERN = /* glsl */`
float pattern(float s, vec2 p) {
  vec2 a = mod(p, 2.0);
  vec2 b = mod(p, 4.0);
  if (s < 0.5) return 1.0;
  if (s < 1.5) return (a.x < 0.5 && a.y > 0.5) ? 0.0 : 1.0;
  if (s < 2.5) return mod(p.x + p.y, 2.0);
  if (s < 3.5) return (a.x > 0.5 && a.y < 0.5) ? 1.0 : 0.0;
  if (s < 4.5) return ((b.x > 2.5 && b.y < 0.5) || (b.x < 0.5 && b.y > 2.5)) ? 1.0 : 0.0;
  return (b.x > 2.5 && b.y < 0.5) ? 1.0 : 0.0;
}`;

const TILE_FRAGMENT = /* glsl */`
uniform vec3 uOff;
uniform vec3 uInk;
uniform float uInkAlpha;
uniform float uStep;
uniform float uEmptyFrom;
uniform float uFill;
uniform vec4 uMeterBox;
uniform vec4 uMeterBg;
uniform vec4 uMeterOff;
uniform vec3 uMeterOn;
uniform float uRadius;
uniform vec4 uEdge;
uniform float uOpacity;
uniform float uFadeIn;
uniform float uMask;
uniform vec4 uWave;
uniform float uFlash;
uniform float uPixel;
uniform float uWire;
uniform float uTarget;
varying vec2 vUv;
${PATTERN}
void main() {
  vec2 p = floor(gl_FragCoord.xy / uPixel);
  if (uMask > 0.5 && pattern(4.0 - uMask, p) > 0.5) discard;
  vec2 uv = vUv;
  if (uWire > 0.5) {
    // Arasaka's Beams view: the sector as an outline over a faint fill, the target's in ink; the glow wave fills it.
    vec2 px = min(uv, 1.0 - uv) / max(fwidth(uv), vec2(1e-6));
    float width = uTarget > 0.5 ? 2.0 : 1.0;
    float edge = 1.0 - smoothstep(width - 0.5, width + 0.5, min(px.x, px.y));
    vec3 c = mix(uInk, uMeterOn, edge * uTarget);
    float a = mix(0.04 + uWave.a, uTarget > 0.5 ? 1.0 : 0.5, edge);
    gl_FragColor = vec4(c, a * uFadeIn);
    #include <colorspace_fragment>
    return;
  }
  if (uRadius > 0.0) {
    vec2 q = abs(uv - 0.5) - (0.5 - uRadius);
    if (q.x > 0.0 && q.y > 0.0 && length(q) > uRadius) discard;
  }
  vec3 col = uOff;
  if (uStep > -0.5 && uStep < uEmptyFrom - 0.5 && pattern(uStep, p) > 0.5) col = mix(col, uInk, uInkAlpha);
  if (uFill > -0.5 && uv.x > uMeterBox.x && uv.x < uMeterBox.z && uv.y > uMeterBox.y && uv.y < uMeterBox.w) {
    col = mix(col, uMeterBg.rgb, uMeterBg.a);
    float pad = 2.0 / 96.0;
    float y0 = uMeterBox.y + pad;
    float h = (uMeterBox.w - uMeterBox.y - 6.0 * pad) / 5.0;
    float cell = (uv.y - y0) / (h + pad);
    float k = floor(cell);
    if (uv.x > uMeterBox.x + pad && uv.x < uMeterBox.z - pad && cell >= 0.0 && k < 5.0 && fract(cell) * (h + pad) < h) {
      col = k < uFill - 0.5 ? uMeterOn : mix(col, uMeterOff.rgb, uMeterOff.a);
    }
  }
  vec2 w = max(fwidth(uv), vec2(1e-6));
  vec2 px = min(uv, 1.0 - uv) / w;
  col = mix(col, uEdge.rgb, (1.0 - smoothstep(0.5, 1.5, min(px.x, px.y))) * uEdge.a);
  col = mix(col, uWave.rgb, uWave.a);
  if (uFlash > 0.0) {
    col = mix(col, vec3(1.0), 0.2 * uFlash);
    col = mix(col, vec3(0.1, 0.9, 1.0), (1.0 - smoothstep(0.0, 2.5, uv.x / w.x)) * 0.5 * uFlash);
    col = mix(col, vec3(1.0, 0.17, 0.84), (1.0 - smoothstep(0.0, 2.5, (1.0 - uv.x) / w.x)) * 0.5 * uFlash);
  }
  gl_FragColor = vec4(col, uOpacity * uFadeIn);
  #include <colorspace_fragment>
}`;

const HALO_FRAGMENT = /* glsl */`
uniform float uOutset;
uniform vec4 uGlow;
uniform float uRing;
uniform vec3 uRingColor;
uniform float uOpacity;
varying vec2 vUv;
void main() {
  vec2 P = (vUv * (1.0 + 2.0 * uOutset) - uOutset) * 96.0;
  vec2 r = abs(P - 48.0);
  float outside = length(max(r - 48.0, 0.0));
  vec3 col = uGlow.rgb;
  float a = outside > 0.0 ? uGlow.a * exp(-outside * outside / 98.0) : 0.0;
  if (uRing > 0.5 && uRing < 1.5) {
    vec2 q = r - 43.0;
    float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - 10.0;
    if (d < 0.0 && d > -2.0) { col = uRingColor; a = 1.0; }
  } else if (uRing > 1.5) {
    float e = 56.0;
    bool along = r.y > e - 2.0 && r.y < e && r.x > e - 14.0 && r.x < e;
    bool across = r.x > e - 2.0 && r.x < e && r.y > e - 14.0 && r.y < e;
    if (along || across) { col = uRingColor; a = 1.0; }
  }
  if (a < 0.004) discard;
  gl_FragColor = vec4(col, a * uOpacity);
  #include <colorspace_fragment>
}`;

const FLOOR_FRAGMENT = /* glsl */`
uniform float uKind;
uniform vec3 uColor;
uniform float uAlpha;
uniform float uSpacing;
uniform float uDot;
uniform vec2 uCentre;
uniform float uReach;
uniform float uSolid;
uniform vec4 uRings;
uniform vec3 uRingColor;
uniform float uRingWidth;
uniform float uDotted;
uniform vec2 uRingCentre;
uniform float uWave;
uniform float uWaveAlpha;
uniform vec2 uWaveCentre;
uniform float uFade;
varying vec3 vWorld;
void main() {
  vec2 xz = vWorld.xz;
  float fade = 1.0 - smoothstep(uSolid, 1.0, length(xz - uCentre) / uReach);
  float mark;
  if (uKind < 0.5) {
    vec2 c = (fract(xz / uSpacing) - 0.5) * uSpacing;
    float d = length(c);
    float aa = max(fwidth(d), 1e-5);
    mark = 1.0 - smoothstep(uDot - aa, uDot + aa, d);
  } else {
    vec2 g = abs(fract(xz / uSpacing - 0.5) - 0.5) * uSpacing / max(fwidth(xz), vec2(1e-5));
    mark = 1.0 - min(min(g.x, g.y), 1.0);
  }
  vec3 col = uColor;
  float a = mark * uAlpha * fade;
  // Idle rings spreading from under the array (Panel: dotted sonar rings; Arasaka: the emitter's pulse).
  float rd = length(xz - uRingCentre);
  for (int i = 0; i < 2; i++) {
    float radius = i == 0 ? uRings.x : uRings.z;
    float alpha = i == 0 ? uRings.y : uRings.w;
    if (alpha <= 0.0) continue;
    float aa = max(fwidth(rd), 1e-5);
    float band = 1.0 - smoothstep(uRingWidth * 0.5 - aa, uRingWidth * 0.5 + aa, abs(rd - radius));
    if (uDotted > 0.5) band *= step(0.5, fract(atan(xz.y - uRingCentre.y, xz.x - uRingCentre.x) * radius / 0.05));
    else band = max(band, exp(-pow((rd - radius) / (uRingWidth * 2.0), 2.0)) * 0.6);
    if (band * alpha > a) { col = uRingColor; a = band * alpha; }
  }
  // Arasaka's build wave: a band spreading from the mount's base, lit only on the grid lines.
  if (uWave > 0.0) {
    vec2 g = abs(fract(xz / uSpacing - 0.5) - 0.5) * uSpacing / max(fwidth(xz), vec2(1e-5));
    float line = clamp(1.6 - min(g.x, g.y) * 0.6, 0.0, 1.0);
    float f = length(xz - uWaveCentre) / uWave;
    vec3 w = vec3(0.0);
    float wa = 0.0;
    if (f > 0.78 && f < 1.0) {
      w = f < 0.88 ? mix(vec3(0.0), vec3(0.1, 0.9, 1.0), (f - 0.78) / 0.1)
        : f < 0.95 ? mix(vec3(0.1, 0.9, 1.0), vec3(1.0), (f - 0.88) / 0.07)
        : f < 0.985 ? mix(vec3(1.0), vec3(1.0, 0.17, 0.84), (f - 0.95) / 0.035)
        : mix(vec3(1.0, 0.17, 0.84), vec3(0.0), (f - 0.985) / 0.015);
      wa = (f < 0.88 ? 0.55 * (f - 0.78) / 0.1 : 1.0) * line * fade * uWaveAlpha;
    }
    if (wa > 0.0) { col = mix(col, w, wa / max(a + wa, 1e-4)); a = max(a, wa); }
  }
  a *= uFade;
  if (a < 0.003) discard;
  gl_FragColor = vec4(col, a);
  #include <colorspace_fragment>
}`;

const color = (hex) => new THREE.Color(hex);
const rgba = ([hex, a]) => new THREE.Vector4(...color(hex).toArray(), a);

/** A tile's material in a look; its uniforms are set per frame by the scene. */
export function tileMaterial(look) {
  const t = LOOKS[look].tile;
  return new THREE.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: TILE_FRAGMENT,
    transparent: true,
    side: THREE.DoubleSide,
    uniforms: {
      uOff: {value: color(t.off)}, uInk: {value: color(t.ink)}, uInkAlpha: {value: 1}, uStep: {value: -1}, uEmptyFrom: {value: t.emptyFrom},
      uFill: {value: -1}, uMeterBox: {value: t.meterBox.clone()}, uMeterBg: {value: rgba(t.meterBg)}, uMeterOff: {value: rgba(t.meterOff)},
      uMeterOn: {value: color(t.meterOn)}, uRadius: {value: t.radius}, uEdge: {value: rgba(t.edge)}, uOpacity: {value: 1}, uFadeIn: {value: 1}, uMask: {value: 0},
      uWave: {value: new THREE.Vector4(...color(t.wave).toArray(), 0)}, uFlash: {value: 0}, uPixel: {value: 1},
      uWire: {value: 0}, uTarget: {value: 0},
    },
  });
}

/** Set a tile's state: dither step (null unmeasured), meter cells, whether it is the target, and its opacity. */
export function setTile(material, look, {step, fill, target, opacity}) {
  const t = LOOKS[look].tile;
  const u = material.uniforms;
  u.uStep.value = step ?? -1;
  u.uFill.value = step === null ? -1 : fill;
  u.uInk.value.set(target ? t.target : t.ink);
  u.uInkAlpha.value = t.halfStep !== undefined && step === t.halfStep ? 0.55 : 1;
  u.uMeterOn.value.set(target ? t.meterOnTarget : t.meterOn);
  u.uOpacity.value = opacity;
  u.uTarget.value = target ? 1 : 0;
}

export function haloMaterial(look) {
  const h = LOOKS[look].halo;
  return new THREE.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: HALO_FRAGMENT,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    uniforms: {
      uOutset: {value: HALO_OUTSET}, uGlow: {value: h.glow ? rgba(h.glow) : new THREE.Vector4(0, 0, 0, 0)},
      uRing: {value: 0}, uRingColor: {value: color(h.ringColor)}, uOpacity: {value: 1},
    },
  });
}

/** Show a tile's halo: the target's ring or brackets, and Arasaka's glow on the loudest two steps. */
export function setHalo(mesh, look, {step, target}) {
  const h = LOOKS[look].halo;
  const u = mesh.material.uniforms;
  u.uRing.value = target ? h.ring : 0;
  u.uGlow.value.w = h.glow && step !== null && step <= 1 ? h.glow[1] : 0;
  mesh.visible = u.uRing.value > 0 || u.uGlow.value.w > 0;
}

/** The look's floor: a plane at y, its dots or grid fading out round `centre` ([x, z]) within `reach` metres. */
export function floorMesh(look, {y, centre, reach, size}) {
  const f = LOOKS[look].floor;
  const material = new THREE.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FLOOR_FRAGMENT,
    transparent: true,
    depthWrite: false,
    uniforms: {
      uKind: {value: f.kind}, uColor: {value: color(f.color)}, uAlpha: {value: f.alpha}, uSpacing: {value: f.spacing}, uDot: {value: f.dot},
      uCentre: {value: new THREE.Vector2(...centre)}, uReach: {value: reach}, uSolid: {value: f.solid},
      uRings: {value: new THREE.Vector4(0, 0, 0, 0)}, uRingColor: {value: color(f.ring)}, uRingWidth: {value: f.ringWidth},
      uDotted: {value: f.dotted}, uRingCentre: {value: new THREE.Vector2(0, 0)},
      uWave: {value: 0}, uWaveAlpha: {value: 1}, uWaveCentre: {value: new THREE.Vector2(0, 0)}, uFade: {value: 1},
    },
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size, size), material);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.set(centre[0], y, centre[1]);
  mesh.renderOrder = -1;
  mesh.userData.ringAlpha = f.ringAlpha;
  return mesh;
}
