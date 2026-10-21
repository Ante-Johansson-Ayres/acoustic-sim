/**
 * arasaka.js — loads the Arasaka look (black and red; Workbench for titles, Silkscreen for
 * labels, DotGothic16 for text and numbers). Only imported when the URL asks for
 * ?look=arasaka, so the app's own bundle is unchanged. arasaka.css loads Workbench itself,
 * without its digits.
 */

import '@fontsource/dotgothic16/latin-400.css';
import '@fontsource/silkscreen/latin-400.css';
import '../../styles/looks/parts.css';
import '../../styles/looks/arasaka.css';
import { currentZoom } from '../shared/scale.js';

// Silkscreen misreads under about 12 screen pixels (C fills in to O). The labels are 10 design
// pixels, so on a small window or a 100 % display they switch to DotGothic16 (see arasaka.css).
function fitLabels() {
  const px = 10 * currentZoom() * (window.devicePixelRatio || 1);
  document.documentElement.classList.toggle('look-arasaka--small', px < 11.9);
}

fitLabels();
window.addEventListener('resize', fitLabels);
