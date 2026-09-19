/** Building the resolved annotation.
 *
 * Provenance is never asserted by the interface: `originOf` RECOMPUTES it from what the draft
 * actually contains every time. A draft that reproduces annotator A's span exactly is marked as
 * A's; change one character offset or one role and it becomes the reviewer's own. It is
 * therefore impossible for a resolved span to carry a source it did not come from, which is the
 * property the exported provenance depends on.
 */
import { el, directionOf } from '../dom.js';
import { renderRoles } from './roles.js';
import { renderStructure } from './structure.js';

const key = (s) => `${s.begin}:${s.end}:${s.label}`;

export function originOf(draft, conflict) {
  const wanted = draft.labels.map((label) => key({ begin: draft.begin, end: draft.end, label }));
  if (!wanted.length) return 'reviewer';
  for (const [side, spans] of [['A', conflict.a_spans], ['B', conflict.b_spans]]) {
    const have = new Set(spans.map(key));
    if (wanted.every((w) => have.has(w))) return side;
  }
  return 'reviewer';
}

export const toWire = (draft, conflict) => ({
  begin: draft.begin, end: draft.end,
  labels: [...draft.labels], origin: originOf(draft, conflict),
});

/** `via` records HOW a draft came to exist - picked from A, picked from B, or made by the
 *  reviewer - as distinct from `originOf`, which only says what the draft's content matches.
 *  The distinction matters in one place: two drafts picked from A and from B that land on one
 *  extent are the two annotators' disagreement, not a judgement, and must not be merged. */
const fromSpan = (span, via) => ({ begin: span.begin, end: span.end, labels: [span.label], via });

/** The quick actions, expressed as drafts so every route through the tool is the same route. */
export const quickDrafts = {
  takeA: (c) => c.a_spans.map((s) => fromSpan(s, 'A')),
  takeB: (c) => c.b_spans.map((s) => fromSpan(s, 'B')),
  keepBoth: (c) => {
    const seen = new Set();
    return [...c.a_spans.map((s) => [s, 'A']), ...c.b_spans.map((s) => [s, 'B'])]
      .filter(([s]) => {
        if (seen.has(key(s))) return false;
        seen.add(key(s));
        return true;
      }).map(([s, via]) => fromSpan(s, via));
  },
  bothRoles: (c) => {
    const extents = new Map();
    for (const s of [...c.a_spans, ...c.b_spans]) {
      const k = `${s.begin}:${s.end}`;
      if (!extents.has(k)) extents.set(k, { begin: s.begin, end: s.end, labels: [], via: 'reviewer' });
      const target = extents.get(k);
      if (!target.labels.includes(s.label)) target.labels.push(s.label);
    }
    return [...extents.values()].map((d) => ({ ...d, labels: d.labels.sort() }));
  },
};

/** Two drafts on the same characters become one span on save. That is fine when it is the
 *  reviewer's own judgement, and refused when it would quietly fuse A's reading with B's: one
 *  draft picked from A, one picked from B, and a result that matches neither annotator. Returns
 *  the offending extents as "begin:end" keys; empty means the drafts may be merged. */
export function mergedDisagreements(drafts, conflict) {
  const groups = new Map();
  for (const draft of drafts) {
    const k = `${draft.begin}:${draft.end}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(draft);
  }
  const out = [];
  for (const [k, group] of groups) {
    if (group.length < 2) continue;
    const vias = new Set(group.map((d) => d.via || 'reviewer'));
    if (!vias.has('A') || !vias.has('B')) continue;
    const merged = { begin: group[0].begin, end: group[0].end,
                     labels: [...new Set(group.flatMap((d) => d.labels))] };
    if (originOf(merged, conflict) === 'reviewer') out.push(k);
  }
  return out;
}

/** "Keep both" merges A and B as they are. On an extent both annotators marked with different
 *  roles that would silently merge a disagreement, so the action is withheld there. */
export function keepBothIsSafe(conflict) {
  if (!conflict.a_spans.length || !conflict.b_spans.length) return false;
  const rolesAt = (spans, b, e) => spans.filter((s) => s.begin === b && s.end === e).map((s) => s.label);
  return conflict.a_spans.every((sa) => {
    const theirs = rolesAt(conflict.b_spans, sa.begin, sa.end);
    if (!theirs.length) return true;
    const mine = rolesAt(conflict.a_spans, sa.begin, sa.end);
    const union = [...new Set([...mine, ...theirs])];
    return union.length <= 1
      || union.every((r) => mine.includes(r)) || union.every((r) => theirs.includes(r));
  });
}

export function bothRolesIsAvailable(conflict) {
  const extents = new Set([...conflict.a_spans, ...conflict.b_spans].map((s) => `${s.begin}:${s.end}`));
  const roles = new Set([...conflict.a_spans, ...conflict.b_spans].map((s) => s.label));
  return extents.size === 1 && roles.size > 1;
}

/** Every span either annotator wrote, as a toggle: press it to put that span in the final,
 *  press it again to take it out.
 *
 * This replaced per-draft "load A1" buttons, which retargeted whichever span was being edited.
 * That answered "make this draft look like A1"; the question a reviewer actually has is "which
 * of these six spans belong in the result", and the answer is usually some of A's and some of
 * B's. The role always travels with the span, so a boundary can never end up carrying a role
 * from somewhere else. */
function sourceStrip(conflict, drafts, handlers) {
  const chosen = (span) => drafts.some((d) => d.begin === span.begin && d.end === span.end
    && d.labels.length === 1 && d.labels[0] === span.label);
  const chips = [];
  const add = (side, spans) => spans.forEach((span, i) => {
    const on = chosen(span);
    chips.push(el('button', {
      type: 'button',
      class: `source-toggle src-${side.toLowerCase()}${on ? ' is-on' : ''}`,
      'aria-pressed': on ? 'true' : 'false',
      dataset: { source: `${side}${i + 1}`, begin: span.begin, end: span.end, label: span.label },
      title: `${span.label} at ${span.begin}–${span.end}`,
      onclick: () => handlers.toggleSource({
        begin: span.begin, end: span.end, label: span.label, side }),
    },
      el('span', { class: 'src-tag', text: `${side}${i + 1}` }),
      el('span', { class: 'src-role', text: span.label }),
      el('span', { class: 'offsets', text: `${span.begin}–${span.end}` })));
  });
  add('A', conflict.a_spans);
  add('B', conflict.b_spans);
  if (!chips.length) return null;
  return el('div', { class: 'sources' },
    el('span', { class: 'sources-label', text: 'Include in the final' }),
    el('div', { class: 'sources-list' }, chips));
}

/** What is about to be saved, against what the two annotators wrote. A nesting or a merge the
 *  reviewer did not intend is visible here before it is committed, not afterwards in the export. */
function finalPreview(conflict, drafts, doc) {
  const valid = drafts.filter((d) => Number.isInteger(d.begin) && Number.isInteger(d.end)
    && d.begin >= 0 && d.begin < d.end && d.end <= doc.text.length);
  const extent = {
    begin: Math.min(conflict.begin, ...valid.map((d) => d.begin)),
    end: Math.max(conflict.end, ...valid.map((d) => d.end)),
  };
  return renderStructure(extent, [
    { side: 'a', label: 'A', long: 'Annotator A',
      spans: conflict.a_spans.map((s) => ({ ...s, origin: 'A' })) },
    { side: 'b', label: 'B', long: 'Annotator B',
      spans: conflict.b_spans.map((s) => ({ ...s, origin: 'B' })) },
    { side: 'final', label: 'Final', long: 'What will be saved',
      spans: valid.map((d) => ({
        begin: d.begin, end: d.end,
        label: d.labels.length ? d.labels.join(' + ') : 'no role yet',
        origin: originOf(d, conflict),
      })) },
  ], directionOf(doc.text.slice(extent.begin, extent.end)));
}

/** Two drafts on one extent merge into a single span on save. That is fine when the reviewer
 *  means "this passage is both things" — and refused when the two roles come from A and B,
 *  because merging them would erase a disagreement rather than resolve it. Picking both sides
 *  from the strip above makes that easy to do by accident, so say which case this is. */
function sameExtentNote(conflict, drafts, draft, index) {
  const twins = drafts.filter((o, k) => k !== index
    && o.begin === draft.begin && o.end === draft.end);
  if (!twins.length) return null;
  // the same test the save uses, so what this note promises is what actually happens
  if (mergedDisagreements(drafts, conflict).includes(`${draft.begin}:${draft.end}`)) {
    return el('p', { class: 'msg msg-error',
      text: `Another span covers exactly ${draft.begin}–${draft.end}, and between them they `
          + 'carry roles from both annotators. Saving that would merge the disagreement away, '
          + 'so it will be refused. Use Both roles to record that the passage performs both, '
          + 'change one boundary, or choose one side.' });
  }
  return el('p', { class: 'msg',
    text: `Another span covers exactly ${draft.begin}–${draft.end}. On save they become one `
        + 'span carrying every role you ticked.' });
}

export function renderEditor(conflict, doc, drafts, activeIndex, roles, picker, handlers) {
  const body = el('div', { class: 'side' });

  drafts.forEach((draft, index) => {
    const text = doc.text.slice(draft.begin, draft.end);
    const invalid = !Number.isInteger(draft.begin) || !Number.isInteger(draft.end)
      || draft.begin < 0 || draft.begin >= draft.end || draft.end > doc.text.length;
    const origin = originOf(draft, conflict);

    const box = el('div', {
      class: 'draft', dataset: { index, active: index === activeIndex },
      'data-active': index === activeIndex ? 'true' : 'false',
    },
      el('div', { class: 'draft-controls' },
        el('button', {
          class: `btn${index === activeIndex ? ' btn-primary' : ''}`, type: 'button',
          onclick: () => handlers.setActive(index),
        }, index === activeIndex ? `▸ Editing span ${index + 1}` : `Span ${index + 1}`),
        el('span', { class: 'chip', text: origin === 'reviewer' ? 'your own' : `from ${origin}` }),
        'from',
        el('input', {
          type: 'number', value: draft.begin, min: 0, max: doc.text.length,
          'aria-label': `Span ${index + 1} start offset`,
          onchange: (e) => handlers.setOffsets(index, Number(e.target.value), draft.end),
        }),
        'to',
        el('input', {
          type: 'number', value: draft.end, min: 0, max: doc.text.length,
          'aria-label': `Span ${index + 1} end offset`,
          onchange: (e) => handlers.setOffsets(index, draft.begin, Number(e.target.value)),
        }),
        el('span', { class: 'offsets', text: `${Math.max(0, draft.end - draft.begin)} chars` }),
        drafts.length > 1
          ? el('button', { class: 'btn btn-quiet', type: 'button',
                           onclick: () => handlers.remove(index) }, 'Remove')
          : null,
      ),
      invalid
        ? el('p', { class: 'msg msg-error',
                    text: `Offsets must satisfy 0 ≤ start < end ≤ ${doc.text.length}.` })
        : el('div', { class: 'selected' },
             el('span', { class: 'selected-label', text: 'Selected text' }),
             // exact and unabbreviated: this is the semantic confirmation of the offsets
             el('div', { class: 'span-text', dir: directionOf(text), text })),
      renderRoles(draft, index, roles, picker, handlers),
      draft.labels.length > 1
        ? el('p', { class: 'msg msg-warn',
                    text: 'Two roles on one span records that this passage performs both at once.' })
        : null,
      sameExtentNote(conflict, drafts, draft, index),
    );
    body.append(box);
  });

  return el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('span', { class: 'shape-name', text: 'Resolved annotation' }),
      el('span', { class: 'shape-hint',
                   text: 'Select text anywhere above to set the editing span\u2019s boundary.' }),
      el('span', { class: 'offsets', text: conflict.conflict_id }),
      el('button', { class: 'btn btn-quiet', type: 'button', style: { marginInlineStart: 'auto' },
                     onclick: handlers.addSpan }, '+ Add span'),
    ),
    sourceStrip(conflict, drafts, handlers),
    finalPreview(conflict, drafts, doc),
    body,
    el('div', { class: 'editor-foot' },
      el('textarea', {
        id: 'decision-note', placeholder: 'Note (optional) — why this resolution?',
        'aria-label': 'Note about this decision',
      }),
      el('button', { class: 'btn btn-quiet', type: 'button', id: 'discard-edit',
                     title: 'Close the editor and forget these edits',
                     onclick: handlers.discard }, 'Discard'),
      el('button', { class: 'btn btn-primary', type: 'button', id: 'save-decision',
                     onclick: handlers.save }, 'Save resolution'),
    ),
  );
}
