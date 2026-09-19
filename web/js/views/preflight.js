/** The preflight screen: what was loaded, and how it was interpreted.
 *
 * Character offsets are only meaningful against the exact text the annotators saw, and roles are
 * discovered from the data rather than configured. Both are shown before any adjudication starts,
 * so a mis-detected field is caught here instead of surfacing as a corrupted export later.
 */
import { el, replace } from '../dom.js';

const FIELDS = [
  ['doc_id', 'document id'], ['text', 'text'], ['spans', 'spans'], ['sents', 'sentences'],
  ['begin', 'begin'], ['end', 'end'], ['label', 'role'], ['layer', 'layer'],
];

function row(term, ...detail) {
  return [el('dt', { text: term }), el('dd', {}, ...detail)];
}

function mapping(side, map) {
  const used = FIELDS.filter(([key]) => map && map[key]);
  return el('div', { class: 'pf-mapping' },
    el('span', { class: `pf-side pf-side-${side.toLowerCase()}`, text: side }),
    el('span', { class: 'offsets', text: used.length
      ? used.map(([key, name]) => `${name} = ${map[key]}`).join(' · ')
      : 'no fields detected' }));
}

function loaded(p) {
  const side = (name, kind) => (kind ? `${name} (${kind})` : name);
  return `${side('Annotator A', p.format_a)} + ${side('Annotator B', p.format_b)}`;
}

/** Where the document text came from. The core reports "A and B (identical)" when both files
 * carry it, or the one annotator whose files do (column files and some JSON exports carry no
 * text on one side); saying so here stops the row reading as "only A was loaded". */
function textOrigin(source) {
  if (!source) return 'not found';
  if (source.startsWith('A and B')) return "in both annotators' files, identical";
  if (source === 'annotator A' || source === 'annotator B') {
    const other = source.endsWith('A') ? 'B' : 'A';
    return `from ${source}'s files (annotator ${other}'s carry none)`;
  }
  return source;
}

function issues(title, list, kind) {
  if (!list || !list.length) return null;
  return el('section', { class: `pf-issues pf-${kind}` },
    el('h4', { class: 'pf-issues-title', text: `${title} (${list.length})` }),
    el('ul', { class: 'pf-issue-list' },
      list.slice(0, 12).map((issue) => el('li', {
        text: (issue.doc_id ? `${issue.doc_id}: ` : '') + issue.message,
      })),
      list.length > 12 ? el('li', { class: 'offsets',
        text: `… and ${list.length - 12} more` }) : null));
}

export function renderPreflight(node, project) {
  const p = project || {};
  const labels = p.labels || [];
  const unpaired = (p.only_in_a || []).length + (p.only_in_b || []).length;
  replace(node,
    el('dl', { class: 'pf-facts' },
      row('Documents', `${p.paired ?? 0} paired`,
        unpaired ? el('span', { class: 'pf-warnish', text: ` · ${unpaired} unpaired` }) : null),
      row('Annotations loaded', loaded(p)),
      row('Document text', textOrigin(p.text_source)),
      row('Reviewing layer', p.layer || '—',
        Object.keys(p.layers || {}).length > 1
          ? el('span', { class: 'offsets',
              text: ` · also present: ${Object.keys(p.layers)
                .filter((l) => l !== p.layer).join(', ')}` })
          : null),
      row('Roles found', labels.length
        ? el('span', { class: 'pf-roles' }, labels.map((l) => el('code', { text: l })))
        : 'none'),
      row('Fields read', mapping('A', p.mapping_a), mapping('B', p.mapping_b))),
    issues('Blocking', p.blocking, 'blocking'),
    issues('Worth knowing', p.warnings, 'warning'));
  return node;
}
