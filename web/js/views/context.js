/** The document around the conflict, on demand.
 *
 * The queue stays the primary workflow: this opens underneath the current conflict and never
 * changes queue position. It renders a WINDOW around the conflict rather than the whole
 * document, because a 115,000-character judgment with 500 annotations is not a usable default -
 * the reviewer can widen the window, or open the whole document, when they actually need it.
 */
import { el, directionOf } from '../dom.js';
import { anchored } from '../text.js';

/** Split a character range into runs that are uniform in which annotators cover them. */
function runs(from, to, spans) {
  const cuts = new Set([from, to]);
  for (const s of spans) {
    if (s.end > from && s.begin < to) {
      cuts.add(Math.max(from, s.begin));
      cuts.add(Math.min(to, s.end));
    }
  }
  const points = [...cuts].filter((p) => p >= from && p <= to).sort((x, y) => x - y);
  const out = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const [start, stop] = [points[i], points[i + 1]];
    if (stop <= start) continue;
    const covering = spans.filter((s) => s.begin <= start && stop <= s.end);
    out.push({ start, stop, covering });
  }
  return out;
}

export function renderContext(conflict, doc, radius) {
  const text = doc.text;
  const from = Math.max(0, conflict.begin - radius);
  const to = Math.min(text.length, conflict.end + radius);

  // Only who wrote each span is carried here. Whether a stretch of text is agreed is decided
  // PER RUN from the labels actually covering it - never inherited from the conflict. A conflict
  // is "not an agreement" as soon as one annotator adds a single extra span, but the text they
  // both labelled identically is still agreed, and must read that way.
  const tagged = [];
  for (const c of doc.conflicts) {
    for (const s of c.a_spans) tagged.push({ ...s, side: 'a' });
    for (const s of c.b_spans) tagged.push({ ...s, side: 'b' });
  }

  // The conflict is picked out by DIMMING everything around it, not by drawing a box around it.
  // An outline on inline text is redrawn on every line it wraps to, and a conflict that covers
  // most of the window would be boxed almost end to end - which distinguishes nothing.
  const fragment = document.createDocumentFragment();
  let outside = null;
  for (const run of runs(from, to, tagged)) {
    const slice = text.slice(run.start, run.stop);
    const inFocus = run.start >= conflict.begin && run.stop <= conflict.end;
    if (!inFocus && !outside) {
      outside = el('span', { class: 'context-outside' });
      fragment.append(outside);
    } else if (inFocus) {
      outside = null;
    }
    const target = outside || fragment;

    if (!run.covering.length) { target.append(anchored(text, run.start, run.stop)); continue; }
    // What did each annotator say about *these characters*?
    const rolesA = [...new Set(run.covering.filter((s) => s.side === 'a').map((s) => s.label))].sort();
    const rolesB = [...new Set(run.covering.filter((s) => s.side === 'b').map((s) => s.label))].sort();
    const same = rolesA.length === rolesB.length && rolesA.every((r, i) => r === rolesB[i]);
    const cls = !rolesB.length ? 'm-a'           // annotator A alone
      : !rolesA.length ? 'm-b'                   // annotator B alone
      : same ? 'm-agreed'                        // both, and they said the same thing
      : 'm-contested';                           // both, and they differ
    const title = rolesA.length && rolesB.length && !same
      ? `A: ${rolesA.join(', ')}  |  B: ${rolesB.join(', ')}`
      : (rolesA.length ? rolesA : rolesB).join(', ');
    target.append(el('mark', { class: cls, title, dataset: { offset: String(run.start) } },
                     slice));
  }

  const legend = el('div', { class: 'legend' },
    [['m-a', 'Annotator A'], ['m-b', 'Annotator B'],
     ['m-contested', 'both, disagreeing'], ['m-agreed', 'both, agreed']]
      .map(([cls, label]) => el('span', { class: 'legend-item' },
        el('span', { class: `legend-swatch ${cls}`, 'aria-hidden': 'true' }), label)));

  return el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('span', { class: 'shape-name', text: 'Document context' }),
      legend,
      el('span', { class: 'offsets', text: `showing ${from}–${to} of ${text.length}` }),
      el('span', { class: 'side-count' },
        el('button', { class: 'btn btn-quiet', id: 'context-widen', text: 'Widen' }),
        ' ',
        el('button', { class: 'btn btn-quiet', id: 'context-full', text: 'Whole document' })),
    ),
    el('div', { class: 'context-text', dir: directionOf(text) }, fragment),
  );
}
