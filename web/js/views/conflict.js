/** Rendering one conflict.
 *
 * Each SHAPE gets the presentation it needs. Showing a role clash and a one-against-many
 * segmentation disagreement with the same generic list of spans is what makes an adjudication
 * interface unreadable on real data: the reviewer has to reconstruct what kind of disagreement
 * it even is before they can think about the answer.
 */
import { el, directionOf } from '../dom.js';
import { anchored } from '../text.js';
import { describeConflict } from './describe.js';
import { renderStructure, worthDrawing } from './structure.js';

export const SHAPE_COPY = {
  ROLE_CLASH: {
    name: 'Same text, different role',
    hint: 'Both annotators marked exactly this text. They disagree only about which role it plays.',
  },
  BOUNDARY_SHIFT: {
    name: 'Same role, different boundary',
    hint: 'Both agree on the role. They disagree about where the span starts or ends.',
  },
  SEGMENTATION: {
    name: 'One span against several',
    hint: 'One annotator treated this passage as a single unit; the other divided it.',
  },
  NESTING_DIFF: {
    name: 'Different internal structure',
    hint: 'One annotator marked structure inside this passage that the other did not.',
  },
  A_ONLY: {
    name: 'Only annotator A marked this',
    hint: 'Annotator B left this passage unannotated.',
  },
  B_ONLY: {
    name: 'Only annotator B marked this',
    hint: 'Annotator A left this passage unannotated.',
  },
  MIXED: {
    name: 'Roles and boundaries both differ',
    hint: 'The two readings of this passage do not line up in any simpler way.',
  },
  AGREEMENT: { name: 'Agreement', hint: 'Both annotators produced the same annotation.' },
};

const depthOf = (span, all) =>
  all.filter((o) => o !== span && o.begin <= span.begin && span.end <= o.end
                    && !(o.begin === span.begin && o.end === span.end)).length;

function spanRow(span, text, siblings, options = {}) {
  const body = text.slice(span.begin, span.end);
  const depth = Math.min(depthOf(span, siblings), 3);
  return el('div', {
    class: `span-row${depth ? ` nest-depth-${depth}` : ''}`,
    dataset: { begin: span.begin, end: span.end, label: span.label },
  },
    el('div', { class: 'span-meta' },
      el('span', { class: 'role', text: span.label }),
      options.tag ? el('span', { class: 'chip', text: options.tag }) : null,
      el('span', { class: 'offsets', text: `${span.begin}–${span.end} · ${span.end - span.begin} chars` }),
    ),
    // Not abbreviated: this text is selectable, and an ellipsis would shift every offset
    // after it. The box scrolls instead.
    el('div', { class: 'span-text', dir: directionOf(body) },
       anchored(text, span.begin, span.end)),
  );
}

function sidePanel(side, spans, text, label) {
  const cls = side === 'A' ? 'side side-a' : side === 'B' ? 'side side-b' : 'side side-agreed';
  const rows = spans.length
    ? spans.map((s, i) => spanRow(s, text, spans, { tag: `${side}${i + 1}` }))
    : [el('p', { class: 'empty-side', text: 'Nothing marked here.' })];
  return el('div', { class: cls },
    el('div', { class: 'side-head' },
      el('span', { class: 'who', text: side, 'aria-hidden': 'true' }),
      el('span', { class: 'side-title', text: label }),
      el('span', { class: 'side-count', text: `${spans.length} span${spans.length === 1 ? '' : 's'}` }),
    ),
    rows,
  );
}

function statusChip(decision, stale) {
  if (stale) {
    // The stored decision was made against source spans that have since changed. It is void
    // and the conflict is queued again; say so, or the reviewer sees an inexplicable repeat.
    return el('span', { class: 'chip chip-flagged', text: 'Source changed – decide again',
                        title: 'The annotation this conflict was decided against has changed '
                             + 'since; the earlier decision no longer applies.' });
  }
  if (!decision) return el('span', { class: 'chip chip-open', text: 'Undecided' });
  if (decision.decision_type === 'DEFER') return el('span', { class: 'chip chip-flagged', text: 'Deferred' });
  return el('span', { class: 'chip chip-decided', text: decision.decision_type.replace(/_/g, ' ') });
}

/** A role clash needs the text once, not twice: the only question is which role it carries.
 *  The passage still carries both identities — A's edge above, B's below, as in the document
 *  view — so it never reads as unowned, nor as the single colour that means agreement. */
function roleClashBody(conflict, text) {
  const body = text.slice(conflict.begin, conflict.end);
  const roles = (side) => [...new Set(
    (side === 'A' ? conflict.a_spans : conflict.b_spans).map((s) => s.label))];
  return [
    el('div', { class: 'side side-contested' },
      el('div', { class: 'side-head' },
        el('span', { class: 'who who-a', text: 'A', 'aria-hidden': 'true' }),
        el('span', { class: 'who who-b', text: 'B', 'aria-hidden': 'true' }),
        el('span', { class: 'side-title', text: 'Both annotators marked exactly this text' }),
        el('span', { class: 'side-count', text: 'same extent, different role' }),
      ),
      el('div', { class: 'span-row' },
        el('div', { class: 'span-meta' },
          el('span', { class: 'offsets', text: `${conflict.begin}–${conflict.end} · ${conflict.end - conflict.begin} chars` })),
        el('div', { class: 'span-text', dir: directionOf(body) },
           anchored(text, conflict.begin, conflict.end)),
      ),
    ),
    el('div', { class: 'role-choice' },
      el('div', { class: 'role-option' },
        el('span', { class: 'who', style: { background: 'var(--a)' }, text: 'A' }),
        roles('A').map((r) => el('span', { class: 'role', text: r }))),
      el('div', { class: 'role-option' },
        el('span', { class: 'who', style: { background: 'var(--b)' }, text: 'B' }),
        roles('B').map((r) => el('span', { class: 'role', text: r }))),
    ),
  ];
}

/** A and B, and — once there is one — the resolution, on the same scale.
 *
 * Drawn whenever the extents differ, and ALSO whenever a decision exists: on a role clash the
 * two bars are identical and the diagram would be decoration, but the moment there is a result
 * the row that says what was actually saved is the point of the picture. */
function diagram(conflict, doc, decision) {
  const saved = (decision && decision.spans) ? decision.spans : [];
  if (!worthDrawing(conflict) && !saved.length) return null;
  const extent = {
    begin: Math.min(conflict.begin, ...saved.map((s) => s.begin)),
    end: Math.max(conflict.end, ...saved.map((s) => s.end)),
  };
  const rows = [
    { side: 'a', label: 'A', long: 'Annotator A',
      spans: conflict.a_spans.map((s) => ({ ...s, origin: 'A' })) },
    { side: 'b', label: 'B', long: 'Annotator B',
      spans: conflict.b_spans.map((s) => ({ ...s, origin: 'B' })) },
  ];
  if (saved.length) {
    rows.push({ side: 'final', label: 'Final', long: 'What was saved',
      spans: saved.map((s) => ({
        begin: s.begin, end: s.end,
        label: (s.labels || []).join(' + ') || 'no role',
        origin: s.origin })) });
  }
  return renderStructure(extent, rows,
                         directionOf(doc.text.slice(extent.begin, extent.end)));
}

export function renderConflict(conflict, doc, decision, contextOpen = false) {
  const copy = SHAPE_COPY[conflict.shape] || { name: conflict.shape, hint: '' };
  const head = el('div', { class: 'card-head' },
    el('span', { class: 'shape-name', text: copy.name }),
    statusChip(decision, (doc.stale || []).includes(conflict.conflict_id)),
    conflict.flags.map((f) => el('span', { class: 'chip', text: f.toLowerCase().replace(/_/g, ' ') })),
    el('span', { class: 'offsets', text: `${conflict.begin}–${conflict.end}` }),
    // The reviewer is reading the top of a card that can run past 1000px. Asking them to find a
    // control at the bottom-left of the window is asking them not to find it.
    el('button', {
      id: 'card-context', class: 'btn btn-quiet', type: 'button',
      style: { marginInlineStart: 'auto' },
      title: 'Show this conflict inside the document, with both annotators\u2019 spans (C)',
      onclick: () => document.getElementById('act-context')?.click(),
    }, contextOpen ? 'Hide document context' : 'Document context'),
  );

  const body = conflict.shape === 'ROLE_CLASH'
    ? roleClashBody(conflict, doc.text)
    // Stack only when ONE side alone is too tall for a column. Seeing A and B beside each other
    // is the whole point, so the threshold is per-side, not on the combined count.
    : [el('div', { class: `sides${Math.max(conflict.a_spans.length, conflict.b_spans.length) > 6 ? ' stacked' : ''}` },
        sidePanel('A', conflict.a_spans, doc.text, 'Annotator A'),
        sidePanel('B', conflict.b_spans, doc.text, 'Annotator B'))];

  return el('div', { class: 'card' },
    head,
    el('p', { class: 'shape-hint', style: { padding: '10px 16px 0' },
              text: describeConflict(conflict) }),
    diagram(conflict, doc, decision),
    body,
  );
}
