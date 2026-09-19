/** The arrangement of a set of spans over one region, drawn to scale.
 *
 * Every fact here is already on the screen as numbers. The point is that "300–400 and 310–330"
 * only becomes "the second sits inside the first" after the reviewer does the arithmetic, and
 * they do it once per conflict, hundreds of times a session. Two rows of bars state it.
 *
 * Colour still carries annotator identity and nothing else: A, B, and — for a span the reviewer
 * wrote themselves — a neutral tone. Roles are text on the bar, never a colour.
 */
import { el } from '../dom.js';

const ORIGIN_CLASS = { A: 'st-a', B: 'st-b', reviewer: 'st-own' };

/** Greedy lane packing: a span takes the first lane whose spans all end at or before it starts.
 *  Disjoint spans share a lane; nested and crossing spans are pushed down a line, which is the
 *  whole visual claim the diagram makes. */
export function packLanes(spans) {
  const ordered = [...spans].sort((x, y) => x.begin - y.begin || y.end - x.end);
  const ends = [];
  return ordered.map((span) => {
    let lane = ends.findIndex((end) => end <= span.begin);
    if (lane === -1) {
      lane = ends.length;
      ends.push(span.end);
    } else {
      ends[lane] = span.end;
    }
    return { ...span, lane };
  });
}

const containedCount = (spans) => spans.filter((inner) => spans.some((outer) =>
  outer !== inner && outer.begin <= inner.begin && inner.end <= outer.end
  && !(outer.begin === inner.begin && outer.end === inner.end))).length;

/** What the diagram says, in words, for anyone who is not looking at it. */
export function describeStructure(rows) {
  return rows.map(({ label, long, spans }) => {
    const name = long || label;
    if (!spans.length) return `${name}: nothing`;
    const nested = containedCount(spans);
    const count = `${spans.length} span${spans.length === 1 ? '' : 's'}`;
    return nested ? `${name}: ${count}, ${nested} nested inside another` : `${name}: ${count}`;
  }).join('. ') + '.';
}

function bar(span, begin, width) {
  const left = ((span.begin - begin) / width) * 100;
  const size = ((span.end - span.begin) / width) * 100;
  return el('div', {
    class: `st-bar ${ORIGIN_CLASS[span.origin] || 'st-own'}`,
    dataset: { lane: span.lane, begin: span.begin, end: span.end },
    // A bar can be a few pixels wide, so the numbers travel with it rather than only beside it.
    title: `${span.label} · ${span.begin}–${span.end}`,
    style: { insetInlineStart: `${left}%`, width: `max(3px, ${size}%)`,
             insetBlockStart: `calc(${span.lane} * (var(--st-bar) + 3px))` },
  }, el('span', { class: 'st-bar-label', text: span.label }));
}

function row({ side, label, spans }, begin, width) {
  const packed = packLanes(spans);
  const lanes = Math.max(1, ...packed.map((s) => s.lane + 1));
  return el('div', { class: `st-row st-row-${side}` },
    el('span', { class: `st-who ${ORIGIN_CLASS[side.toUpperCase()] || 'st-own'}`, text: label }),
    el('div', {
      class: 'st-track',
      style: { height: `calc(${lanes} * (var(--st-bar) + 3px) - 3px)` },
    },
      packed.length
        ? packed.map((s) => bar({ ...s, origin: s.origin || side }, begin, width))
        : el('span', { class: 'st-none', text: 'nothing here' })),
  );
}

/** `rows` is [{ side: 'A'|'B'|'final', label, spans: [{begin, end, label, origin?}] }].
 *
 * `dir` is the direction of the text being diagrammed, not of the interface. Bars are placed
 * with logical inset, so an Arabic document draws its offset axis right to left — the same way
 * the reviewer is reading the passage directly underneath. */
export function renderStructure(extent, rows, dir = 'ltr') {
  const width = Math.max(1, extent.end - extent.begin);
  return el('figure', {
    class: 'structure', role: 'img', dir, 'aria-label': describeStructure(rows),
  },
    el('div', { class: 'st-rows' }, rows.map((r) => row(r, extent.begin, width))),
    el('figcaption', { class: 'st-scale' },
      el('span', { class: 'offsets', text: String(extent.begin) }),
      // A Latin phrase inside a right-to-left figure resolves to RTL and renders as
      // "chars 214". The digits and the word belong together, so isolate them.
      el('span', { class: 'offsets', dir: 'ltr', text: `${width} chars` }),
      el('span', { class: 'offsets', text: String(extent.end) })),
  );
}

/** Two identical bars tell the reviewer nothing they cannot see in one glance at the text. */
export const worthDrawing = (conflict) =>
  new Set([...conflict.a_spans, ...conflict.b_spans]
    .map((s) => `${s.begin}:${s.end}`)).size > 1;
