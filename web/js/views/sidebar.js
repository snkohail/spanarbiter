/** Navigation and progress.
 *
 * One progress figure, stated once. The earlier sidebar showed "Conflicts 194 / Decided 4 /
 * Left 190 / 190 left" — four renderings of the same two numbers, competing with the conflict
 * the reviewer is supposed to be reading.
 */
import { el, replace } from '../dom.js';
import { SHAPE_COPY } from './conflict.js';

export function renderProgress(container, progress, docId) {
  if (!progress) {
    return replace(container, el('p', { class: 'none', text: 'No document open.' }));
  }
  const { settled, conflicts, undecided, flagged, auto_agreed: agreed, stale = 0 } = progress;
  const secondary = [
    `${undecided} remaining`,
    `${flagged} deferred`,
    `${agreed} agreed automatically`,
  ];
  if (stale) secondary.push(`${stale} need re-adjudication`);

  return replace(container,
    el('p', { class: 'doc-id', text: docId || '—' }),
    el('p', { class: 'progress-headline', text: `${settled} / ${conflicts} resolved` }),
    el('div', {
      class: 'meter', role: 'progressbar', 'aria-valuenow': String(progress.percent),
      'aria-valuemin': '0', 'aria-valuemax': '100',
      'aria-label': 'Conflicts resolved in this document',
    }, el('i', { style: { inlineSize: `${progress.percent}%` } })),
    el('p', { class: 'progress-detail', text: secondary.join(' · ') }),
    progress.complete
      ? el('p', { class: 'badge-done', text: '✓ Document complete' })
      : null,
  );
}

/** Conflict types, as clickable filters with their own counts. */
export function renderShapes(container, progress, active, onPick) {
  if (!progress || !progress.by_shape) return replace(container);
  const rows = [['all', 'All', progress.settled, progress.conflicts]];
  for (const [shape, counts] of Object.entries(progress.by_shape)
    .sort((a, b) => b[1].total - a[1].total)) {
    rows.push([shape, (SHAPE_COPY[shape] || { name: shape }).name,
               counts.settled, counts.total]);
  }
  return replace(container, rows.map(([value, label, settled, total]) => el('button', {
    class: `shape-row${active === value ? ' is-active' : ''}`,
    type: 'button',
    'aria-pressed': active === value ? 'true' : 'false',
    title: `Show only: ${label}`,
    onclick: () => onPick(value),
  },
    el('span', { class: 'shape-row-label', text: label }),
    el('span', { class: 'shape-row-count', text: `${settled}/${total}` }))));
}

export function renderDocList(container, summary, documents, currentId, onPick, query = '') {
  const done = documents.filter((d) => d.complete).length;
  replace(summary, el('span', {
    text: `${done} / ${documents.length} documents complete`,
  }));

  const needle = query.trim().toLowerCase();
  const shown = needle
    ? documents.filter((d) => d.doc_id.toLowerCase().includes(needle))
    : documents;

  if (!shown.length) {
    return replace(container, el('li', {},
      el('p', { class: 'none', text: `No document matches “${query}”` })));
  }
  return replace(container, shown.map((doc) => el('li', {},
    el('button', {
      class: `doc-item${doc.complete ? ' is-done' : ''}`,
      'aria-current': doc.doc_id === currentId ? 'true' : 'false',
      onclick: () => onPick(doc.doc_id),
    },
      el('span', { text: doc.doc_id }),
      el('span', {
        class: 'count',
        text: doc.error ? 'error' : doc.complete ? '✓' : `${doc.settled}/${doc.conflicts}`,
      })))));
}
