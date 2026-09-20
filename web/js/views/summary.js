/** The adjudication summary: what the decision logs add up to, drawn rather than listed.
 *
 * Every figure comes from the server's corpus summary, which compares the stored resolved spans
 * with both annotators' spans; nothing here is derived from the button the reviewer pressed.
 * Colour keeps its one meaning: blue is annotator A, orange is annotator B, green is agreement.
 * The reviewer's own work is hatched rather than given a fourth hue (see tokens.css).
 */
import { el, replace } from '../dom.js';
import { SHAPE_COPY } from './conflict.js';

const DECISION_COPY = {
  TAKE_A: ['Take A', "A's annotation kept as it was"],
  TAKE_B: ['Take B', "B's annotation kept as it was"],
  TAKE_BOTH: ['Keep both', 'spans from both annotators, on different text'],
  MULTI_ROLE: ['Both roles', "one span carrying several roles, as the reviewer's judgement"],
  CUSTOM: ['Edited', 'the reviewer authored or altered at least one span'],
  DROP: ['Dropped', 'nothing here should be annotated'],
  DEFER: ['Deferred', 'left open on purpose; the document stays incomplete'],
};
const DECISION_ORDER = ['TAKE_A', 'TAKE_B', 'TAKE_BOTH', 'MULTI_ROLE', 'CUSTOM', 'DROP', 'DEFER'];
const DECISION_TONE = {
  TAKE_A: 'a', TAKE_B: 'b', TAKE_BOTH: 'both', MULTI_ROLE: 'neutral', CUSTOM: 'neither',
  DROP: 'neutral', DEFER: 'deferred',
};
/** Landis and Koch (1977), the customary reading of kappa; shown as a scale, not a verdict. */
const KAPPA_BANDS = [
  [0, 0.2, 'slight'], [0.2, 0.4, 'fair'], [0.4, 0.6, 'moderate'],
  [0.6, 0.8, 'substantial'], [0.8, 1, 'almost perfect'],
];

const num = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString('en-US'));
const pct = (part, whole) => (whole ? `${Math.round((1000 * part) / whole) / 10}%` : '0%');
const plural = (n, word) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

function seconds(v) {
  if (v === null || v === undefined) return '—';
  if (v < 90) return `${Math.round(v * 10) / 10} s`;
  const minutes = Math.floor(v / 60);
  return `${minutes} min ${Math.round(v - 60 * minutes)} s`;
}

function section(title, note, ...body) {
  return el('section', { class: 'sm-section' },
    el('h3', { class: 'sm-title', text: title }),
    note ? el('p', { class: 'sm-note', text: note }) : null,
    ...body);
}

function kpi(value, label, title) {
  return el('div', { class: 'sm-kpi', title: title || null },
    el('span', { class: 'sm-kpi-n', text: value }),
    el('span', { class: 'sm-kpi-l', text: label }));
}

/** One horizontal bar divided in proportion, with a legend that repeats every number in text. */
function stack(parts, total, label) {
  const bar = el('div', { class: 'sm-stack', role: 'img', 'aria-label': label });
  for (const part of parts) {
    if (!part.value) continue;
    const width = (100 * part.value) / total;
    bar.append(el('div', {
      class: `sm-seg sm-tone-${part.tone}`, style: { width: `${width}%` },
      title: `${part.name}: ${num(part.value)} (${pct(part.value, total)})`,
    }, width >= 8 ? el('span', { class: 'sm-seg-label', text: num(part.value) }) : null));
  }
  if (!total) bar.append(el('div', { class: 'sm-seg sm-tone-empty', style: { width: '100%' } }));
  const legend = el('div', { class: 'sm-legend' }, parts.map((part) => el('span', { class: 'sm-key' },
    el('i', { class: `sm-swatch sm-tone-${part.tone}` }),
    `${part.name} `, el('b', { text: num(part.value) }),
    total ? el('span', { class: 'offsets', text: ` ${pct(part.value, total)}` }) : null)));
  return [bar, legend];
}

/** A row per item: name, a bar scaled to the largest item, the number, and an extra note. */
function rows(items, label) {
  const max = Math.max(0, ...items.map((it) => it.value));
  return el('div', { class: 'sm-rows', role: 'img', 'aria-label': label },
    items.map((it) => el('div', { class: 'sm-row' },
      el('span', { class: 'sm-row-name', text: it.name, title: it.hint || null }),
      el('div', { class: 'sm-bar' },
        el('div', { class: `sm-fill sm-tone-${it.tone}`,
                    style: { width: `${max ? (100 * it.value) / max : 0}%` } })),
      el('span', { class: 'sm-row-value' },
        el('b', { text: num(it.value) }),
        it.extra ? el('span', { class: 'offsets', text: ` ${it.extra}` }) : null))));
}

/** A 0 to 1 scale with the value marked; optional named bands underneath the marker. */
function gauge(value, bands, label) {
  const track = el('div', { class: 'sm-gauge', role: 'img', 'aria-label': label });
  for (const [lo, hi, name] of bands || []) {
    track.append(el('div', { class: 'sm-band', title: `${name}: ${lo} to ${hi}`,
                             style: { left: `${100 * lo}%`, width: `${100 * (hi - lo)}%` } },
      el('span', { class: 'sm-band-name', text: name })));
  }
  if (value !== null && value !== undefined) {
    const clamped = Math.max(0, Math.min(1, value));
    track.append(el('div', { class: 'sm-marker', style: { left: `${100 * clamped}%` } },
      el('span', { class: 'sm-marker-value', text: value.toFixed(2) })));
  }
  return el('div', { class: 'sm-gauge-wrap' }, track,
    el('div', { class: 'sm-scale' },
      ['0', '0.2', '0.4', '0.6', '0.8', '1'].map((t) => el('span', { text: t }))));
}

function measure(title, value, gaugeNode, ...detail) {
  return el('div', { class: 'sm-measure' },
    el('div', { class: 'sm-measure-head' },
      el('span', { text: title }),
      el('b', { text: value === null || value === undefined ? 'n/a' : value.toFixed(2) })),
    gaugeNode,
    ...detail.map((line) => el('p', { class: 'sm-small', text: line })));
}

export function renderSummary(node, report) {
  const r = report || {};
  const conflicts = r.conflicts || 0;
  const decided = r.adjudicated || 0;
  const deferred = r.deferred || 0;
  const open = Math.max(0, conflicts - decided - deferred);
  const docsLeft = (r.documents || 0) - (r.documents_complete || 0);
  const complete = (r.documents || 0) > 0 && docsLeft === 0 && open === 0 && !(r.stale > 0);

  // ---------------------------------------------------------------- status
  const detail = [];
  if (open) detail.push(`${plural(open, 'conflict')} still need${open === 1 ? 's' : ''} a decision`);
  if (deferred) detail.push(`${num(deferred)} deferred`);
  if (r.stale) detail.push(`${num(r.stale)} void after a source change`);
  const status = el('div', { class: `sm-status ${complete ? 'is-complete' : 'is-partial'}` },
    el('b', { text: complete ? 'Complete' : 'Provisional' }),
    el('span', { text: complete
      ? ` — every conflict in ${plural(r.documents, 'document')} is decided; these figures are final.`
      : ` — ${detail.join(', ') || 'nothing decided yet'}, in ${plural(docsLeft, 'document')} `
        + 'not yet complete. The figures describe the work so far.' }));

  // ---------------------------------------------------------------- progress
  const progress = section('Progress', null,
    el('div', { class: 'sm-kpis' },
      kpi(`${num(r.documents_complete)} / ${num(r.documents)}`, 'documents complete'),
      kpi(num(conflicts), 'conflicts to decide',
          'units where the two annotations differ; exact agreements are not counted here'),
      kpi(`${num(decided)} (${pct(decided, conflicts)})`, 'decided'),
      kpi(num(r.auto_agreed), 'exact agreements carried over',
          'identical on both sides, so never queued and never counted as adjudications')),
    ...stack([
      { name: 'decided', value: decided, tone: 'decided' },
      { name: 'deferred', value: deferred, tone: 'deferred' },
      { name: 'open', value: open, tone: 'open' },
    ], conflicts, `Progress: ${decided} decided, ${deferred} deferred, ${open} open of ${conflicts}`));

  // ---------------------------------------------------------------- outcomes
  const outcomes = section('Where the final annotation came from',
    'For each decided conflict the stored spans are compared with what A and B wrote. '
    + 'A conflict counts as "equals A" only when the final annotation reproduces A\'s spans '
    + 'exactly, whatever button produced it.',
    ...stack([
      { name: 'equals A', value: r.took_A || 0, tone: 'a' },
      { name: 'equals B', value: r.took_B || 0, tone: 'b' },
      { name: 'equals both', value: r.took_both || 0, tone: 'both' },
      { name: 'differs from both', value: r.differs_from_both || 0, tone: 'neither' },
    ], decided, `Outcomes of ${decided} decided conflicts: equals A ${r.took_A || 0}, `
       + `equals B ${r.took_B || 0}, equals both ${r.took_both || 0}, `
       + `differs from both ${r.differs_from_both || 0}`),
    decided ? el('div', { class: 'sm-kpis sm-kpis-small' },
      kpi(num(r.new_boundary), 'new boundary',
          'the final extents match neither annotator'),
      kpi(num(r.new_structure), 'new structure',
          'the final nesting or segmentation matches neither annotator'),
      kpi(num(r.new_role_inventory), 'different role set',
          "the roles on the passage match neither annotator's set: a role neither used, "
          + 'or both annotators\' roles combined'),
      kpi(num(r.role_reassigned), 'roles reassigned',
          "one annotator's extents and roles, but placed differently"))
      : el('p', { class: 'sm-small', text: 'No conflict has been decided yet.' }));

  // ---------------------------------------------------------------- conflict types
  const shapeCounts = Object.entries(r.by_conflict_type || {})
    .sort((x, y) => y[1] - x[1]);
  const medians = r.seconds_by_conflict_type || {};
  const shapes = section('Conflict types',
    'Which kinds of disagreement the two annotators produced, and the median active time a '
    + 'decision of each kind took.',
    shapeCounts.length
      ? rows(shapeCounts.map(([shape, count]) => ({
          name: SHAPE_COPY[shape]?.name || shape, hint: SHAPE_COPY[shape]?.hint,
          value: count, tone: shape === 'A_ONLY' ? 'a' : shape === 'B_ONLY' ? 'b' : 'neutral',
          extra: `${pct(count, conflicts)}${medians[shape] != null
            ? ` · median ${seconds(medians[shape])}` : ''}`,
        })), 'Conflicts by type')
      : el('p', { class: 'sm-small', text: 'No conflicts in this corpus.' }));

  // ---------------------------------------------------------------- decisions
  const byType = r.by_decision_type || {};
  const chosen = DECISION_ORDER.filter((k) => byType[k]);
  const decisions = section('What the reviewer chose',
    'The decision types as recorded. They are the actions taken; the outcomes above are what '
    + 'those actions amounted to once the spans were compared.',
    chosen.length
      ? rows(chosen.map((k) => ({
          name: DECISION_COPY[k][0], hint: DECISION_COPY[k][1], value: byType[k],
          tone: DECISION_TONE[k], extra: DECISION_COPY[k][1],
        })), 'Decisions by type')
      : el('p', { class: 'sm-small', text: 'No decision recorded yet.' }),
    byType.AUTO_AGREE ? el('p', { class: 'sm-small',
      text: `${num(byType.AUTO_AGREE)} exact agreements were carried over automatically; `
        + 'they are not adjudications and appear in no outcome above.' }) : null);

  // ---------------------------------------------------------------- effort
  const kinds = r.simple_vs_structural || {};
  const effort = section('Time and revisions',
    'Active seconds while a conflict was on screen, paused when the tab was hidden. Decisions '
    + 'without a recorded time are counted separately rather than skewing the medians.',
    el('div', { class: 'sm-kpis' },
      kpi(seconds(r.seconds_median), 'median per conflict',
          r.seconds_iqr && r.seconds_iqr[0] != null
            ? `interquartile range ${seconds(r.seconds_iqr[0])} to ${seconds(r.seconds_iqr[1])}`
            : null),
      kpi(`${num(r.timed_decisions)} / ${num(r.untimed_decisions)}`, 'timed / untimed decisions'),
      kpi(num(r.visits_median), 'median visits per conflict'),
      kpi(num(r.revisions_total), 'decisions revised')),
    (kinds.simple?.n || kinds.structural?.n)
      ? rows([
          { name: 'simple conflicts (role, one-sided)', value: kinds.simple?.seconds_median || 0,
            tone: 'neutral', extra: `s median · n=${num(kinds.simple?.n || 0)}` },
          { name: 'structural conflicts (boundary, segmentation, nesting, mixed)',
            value: kinds.structural?.seconds_median || 0, tone: 'neutral',
            extra: `s median · n=${num(kinds.structural?.n || 0)}` },
        ], 'Median seconds, simple against structural conflicts')
      : null);

  // ---------------------------------------------------------------- stale
  const stale = r.stale ? el('div', { class: 'sm-stale' },
    el('b', { text: `${plural(r.stale, 'decision')} need${r.stale === 1 ? 's' : ''} re-adjudication. ` }),
    'They were made against annotations that have since changed, so they are void: they reach '
    + 'no export and no figure on this page, and their conflicts are back in the queue marked '
    + '"source changed".') : null;

  // ---------------------------------------------------------------- agreement
  const f1 = r.agreement?.span_f1 || {};
  const kappa = r.agreement?.role_kappa_on_shared_extents || {};
  const kappaValue = kappa.n ? kappa.kappa : null;
  const agreement = section('Agreement between the annotators, before adjudication',
    'How far apart A and B were to begin with. These two numbers describe the input and do not '
    + 'change as conflicts are decided.',
    el('div', { class: 'sm-two' },
      measure('Exact-match span F1', f1.f1 ?? null, gauge(f1.f1 ?? null, null,
        `Span F1 ${f1.f1 ?? 'n/a'}`),
        `A span counts as shared only when its extent and role match exactly: ${num(f1.shared)} `
        + `shared of ${num(f1.a_spans)} spans by A and ${num(f1.b_spans)} by B`,
        `precision of A against B ${f1.precision_a_vs_b ?? '—'}, recall ${f1.recall_a_vs_b ?? '—'}`),
      measure("Cohen's kappa on shared extents", kappaValue, gauge(kappaValue, KAPPA_BANDS,
        `Cohen's kappa ${kappaValue ?? 'n/a'}`),
        kappa.n
          ? `computed on the ${plural(kappa.n, 'extent')} both annotators drew with one role `
            + `each; ${num(kappa.skipped_stacked || 0)} multi-role extents excluded`
          : 'not computable: no extent was marked exactly once by both annotators',
        kappa.n
          ? `observed agreement ${kappa.observed_agreement}, expected by chance `
            + `${kappa.expected_agreement}; the bands are the customary reading (Landis and Koch)`
          : null)));

  // ---------------------------------------------------------------- validation + where
  const errors = r.validation?.errors || [];
  const where = section('Where this lives',
    `${num(r.validation?.documents_validated)} decision log(s) re-validated against the rules `
    + 'they were written under before anything was counted.',
    errors.length ? el('div', { class: 'sm-stale' },
      el('b', { text: `${plural(r.validation.error_count, 'log')} failed validation and `
        + 'counted for nothing: ' }), errors.join('; ')) : null,
    el('div', { class: 'sm-paths' },
      el('div', {}, 'Decision logs, one file per document and layer: ',
        el('code', { text: r.paths?.decisions || '—' })),
      el('div', {}, 'Exports (canonical, round-trip, spans.tsv): ',
        el('code', { text: r.paths?.export || '—' })),
      el('div', {}, 'The same report as JSON for a paper: ',
        el('code', { text: 'python3 adjudicate.py summary --project <project> --json report.json' }))));

  replace(node, status, progress, outcomes, shapes, decisions, effort, stale, agreement, where);
  return node;
}
