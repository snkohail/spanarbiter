/** Saying, in a sentence, what the two annotators actually did.
 *
 * A fixed sentence per conflict type tells the reviewer what KIND of disagreement this is; it
 * does not tell them what the disagreement IS. "One annotator marked structure inside this
 * passage that the other did not" is true of every nesting conflict ever produced. These
 * descriptions are generated from the spans in front of the reviewer, so they carry the counts,
 * the roles and the distances that make this instance different from the last one.
 *
 * Descriptive only. Nothing here suggests an answer.
 */

/** Role names come from the corpus and may run the other way from the sentence around them.
 *  Without an isolate, an Arabic label inside an English sentence drags the punctuation with it
 *  and the list reads back to front. U+2068/U+2069 are exactly the characters for this. */
const isolate = (label) => `\u2068${label}\u2069`;

const list = (items) => {
  const unique = [...new Set(items)].map(isolate);
  if (unique.length <= 1) return unique[0] || '';
  if (unique.length === 2) return `${unique[0]} and ${unique[1]}`;
  return `${unique.slice(0, -1).join(', ')} and ${unique[unique.length - 1]}`;
};

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const roles = (spans) => list(spans.map((s) => s.label));
const extents = (spans) => new Set(spans.map((s) => `${s.begin}:${s.end}`));

function nested(spans) {
  return spans.filter((inner) => spans.some((outer) =>
    outer !== inner && outer.begin <= inner.begin && inner.end <= outer.end
    && !(outer.begin === inner.begin && outer.end === inner.end)));
}

function boundaryGap(a, b) {
  if (a.length !== 1 || b.length !== 1) return null;
  const start = Math.abs(a[0].begin - b[0].begin);
  const end = Math.abs(a[0].end - b[0].end);
  if (start && end) return `their start and end both differ, by ${start} and ${end} characters`;
  if (start) return `their start differs by ${plural(start, 'character')}`;
  if (end) return `their end differs by ${plural(end, 'character')}`;
  return 'their boundaries differ';
}

export function describeConflict(conflict) {
  const a = conflict.a_spans;
  const b = conflict.b_spans;

  switch (conflict.shape) {
    case 'A_ONLY':
      return `Annotator A marks ${plural(a.length, 'span')} here, as ${roles(a)}. `
           + 'Annotator B left this passage unannotated.';

    case 'B_ONLY':
      return `Annotator B marks ${plural(b.length, 'span')} here, as ${roles(b)}. `
           + 'Annotator A left this passage unannotated.';

    case 'ROLE_CLASH':
      return `Both annotate exactly the same text. A assigns ${roles(a)}; B assigns ${roles(b)}.`;

    case 'BOUNDARY_SHIFT': {
      const gap = boundaryGap(a, b);
      const role = roles(a);
      return gap
        ? `Both assign ${role}, but ${gap}.`
        : `Both assign ${role}. A marks ${plural(a.length, 'span')}, B marks ${b.length}, `
          + 'and the boundaries do not line up.';
    }

    case 'SEGMENTATION': {
      const oneIsA = extents(a).size === 1;
      const one = oneIsA ? 'A' : 'B';
      const many = oneIsA ? 'B' : 'A';
      const parts = oneIsA ? b : a;
      return `Annotator ${one} treats this passage as a single span. `
           + `Annotator ${many} divides it into ${plural(parts.length, 'span')} `
           + `(${roles(parts)}).`;
    }

    case 'NESTING_DIFF': {
      const aInner = nested(a);
      const bInner = nested(b);
      const deeper = aInner.length >= bInner.length ? 'A' : 'B';
      const inner = aInner.length >= bInner.length ? aInner : bInner;
      const flat = deeper === 'A' ? 'B' : 'A';
      const flatSpans = deeper === 'A' ? b : a;
      return `Annotator ${flat} marks ${plural(flatSpans.length, 'span')} here. `
           + `Annotator ${deeper} marks the same region and adds `
           + `${plural(inner.length, 'nested span')} inside it (${roles(inner)}).`;
    }

    default:
      return `Annotator A marks ${plural(a.length, 'span')} (${roles(a)}); `
           + `annotator B marks ${plural(b.length, 'span')} (${roles(b)}). `
           + 'Neither the roles nor the boundaries line up.';
  }
}
