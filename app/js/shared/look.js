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
