/**
 * history.js — the last 30 seconds: the direction of the answer and how far
 * it stands above the background (the median of its map). Both charts fill
 * the box the layout gives them and redraw when it changes.
 */

import { h } from '../shared/dom.js';
import { LineChart } from '../shared/linechart.js';
import { deg, num } from '../shared/format.js';
import { HISTORY_WINDOW_S } from './session.js';

const X_TICKS = [
  {value: -30, label: '−30 s'},
  {value: -20, label: '−20 s'},
  {value: -10, label: '−10 s'},
  {value: 0, label: 'now'},
];
const PAD = {l: 40, r: 10, t: 8, b: 22};

function figure(title, legend, chart) {
  const box = h('div', {class: 'chart-box'}, chart.el);
  const el = h('figure', {class: 'hw-chart'},
    h('figcaption', {class: 'hw-chart__head'}, h('span', {class: 'label'}, title), legend || null), box);
  return {el, box};
}

export class HistoryCharts {
  constructor() {
    this.direction = new LineChart({pad: PAD, label: 'Azimuth and elevation of the source over the last 30 seconds'});
    this.margin = new LineChart({pad: PAD, label: 'How far the source stands above the background over the last 30 seconds'});
    const legend = h('span', {class: 'legend'},
      h('span', {style: {color: 'var(--accent)'}}, h('i'), 'Azimuth'),
      h('span', {style: {color: 'var(--muted)'}}, h('i'), 'Elevation'));
    const direction = figure('Direction', legend, this.direction);
    const margin = figure('Above background', null, this.margin);
    this.directionBox = direction.box;
    this.marginBox = margin.box;
    this.el = h('section', {class: 'panel timeline', 'aria-label': 'Last 30 seconds'},
      h('header', {class: 'panel__head'}, h('h2', {class: 'label'}, 'Last 30 s')),
      h('div', {class: 'hw-charts'}, direction.el, margin.el));
    this.lastRender = 0;
    this.pending = false;
    this.observer = new ResizeObserver(() => { this.pending = true; });
    this.observer.observe(this.directionBox);
    this.observer.observe(this.marginBox);
  }

  fit() {
    const a = this.direction.setSize(this.directionBox.clientWidth, this.directionBox.clientHeight);
    const b = this.margin.setSize(this.marginBox.clientWidth, this.marginBox.clientHeight);
    return a || b;
  }

  /** span: the field's half-width in degrees, so the direction chart covers the whole field. */
  render(session, {span = 40, force = false} = {}) {
    const resized = this.fit() || this.pending;
    this.pending = false;
    const now = performance.now();
    if (!force && !resized && now - this.lastRender < 200) return;
    this.lastRender = now;
    const frame = session.frame;
    const latest = Number.isFinite(frame?.timestamp_s) ? frame.timestamp_s : Date.now() / 1000;
    const recent = session.history.filter((item) => latest - item.t <= HISTORY_WINDOW_S);
    const points = (key) => recent.map((item) => [item.t - latest, item[key]]);

    const top = Math.ceil(span);
    this.direction.setAxes({
      x: {min: -HISTORY_WINDOW_S, max: 0, ticks: X_TICKS},
      y: {min: -top, max: top, ticks: [top, 0, -top].map((value) => ({value, label: deg(value, 0)}))},
    });
    this.direction.render([
      {points: points('elevation'), stroke: 'var(--muted)', width: 1.5, gap: 2},
      {points: points('azimuth'), stroke: 'var(--accent)', width: 2, gap: 2},
    ]);

    const highest = Math.max(30, ...recent.map((item) => item.margin).filter(Number.isFinite));
    const ceiling = Math.ceil(highest / 10) * 10;
    this.margin.setAxes({
      x: {min: -HISTORY_WINDOW_S, max: 0, ticks: X_TICKS},
      y: {min: 0, max: ceiling, ticks: [ceiling, ceiling / 2, 0].map((value) => ({value, label: `${num(value, 0)} dB`}))},
    });
    this.margin.render([{points: points('margin'), stroke: 'var(--ok)', width: 2, gap: 2}]);
  }
}
