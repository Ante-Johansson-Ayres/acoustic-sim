/**
 * look.js — the hidden looks. `?look=panel` or `?look=arasaka` in the URL (before
 * the hash, e.g. `/?look=arasaka#hardware`) shows the whole app in that look for
 * this visit. Without the flag the app keeps its own look. Nothing is remembered:
 * drop the flag and the app is back to normal.
 */

export const LOOKS = ['panel', 'arasaka'];

/** The look a URL query string asks for, or null for the app's own look. */
export function parseLook(search) {
  const value = new URLSearchParams(search || '').get('look');
  const name = value ? value.trim().toLowerCase() : '';
  return LOOKS.includes(name) ? name : null;
}

export const look = typeof window === 'undefined' ? null : parseLook(window.location.search);

// Colours the 3D scenes draw with in each look, by role. `marker` is what the app draws white
// (the source dot, ring and outlines), `mesh` the grid over the beam pattern; a role a look
// leaves out keeps the scene's own colour.
const SCENE = {
  panel: {background: '#f2f2f0', line: '#c9c9c6', strong: '#9a9a97', subtle: '#6f6f6d', text: '#111111', accent: '#ea5a00', marker: '#111111', mesh: '#787876'},
  arasaka: {background: '#0b0a0c', line: '#3a1218', strong: '#4a1a20', subtle: '#a19d99', text: '#e9e3dc', accent: '#f2122a', marker: '#e9e3dc'},
};

/** A 3D scene's colour for a role in the given look (the current one by default), or its own colour without a look. */
export function sceneColor(role, own, name = look) {
  return (name && SCENE[name][role]) || own;
}
