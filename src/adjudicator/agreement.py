"""Corpus-level inter-annotator agreement, computed from the raw span sets alone.

Deliberately independent of any adjudication decision, and of files, field names, or a project
object -- like `model.py` and `conflicts.py`, this module only ever sees spans. Two standard,
recognisable metrics, each scoped to the question it actually answers:

**Span-level F1** -- symmetric exact-match overlap between A's and B's spans, on
`(begin, end, label)`. This is "how often did the annotators produce the same annotation at all",
and it is sensitive to every kind of disagreement (boundary, role, segmentation) at once, the way
a NER-style F1 always is.

**Cohen's kappa on shared extents** -- restricted to extents where BOTH annotators drew exactly
one span (no stacked roles on either side), because kappa assumes a fixed, pre-aligned set of
items each classified into one category, and a stacked extent is not one categorical judgement.
That population is exactly "extents where A and B agree on the boundary" -- the AGREEMENT spans
sharing that extent, plus every ROLE_CLASH -- so this kappa answers "given they already agree on
the extent, do they agree on the role", and says nothing about boundary disagreement, which F1
already covers. A single kappa over ALL conflicts would blur those two questions together; that
blurring is the thing this module exists to avoid.
"""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from .model import Span

SpanPair = tuple[Sequence[Span], Sequence[Span]]


def span_f1(pairs: Sequence[SpanPair]) -> dict:
    """Symmetric exact-match F1 across a corpus of (a_spans, b_spans) pairs, one pair per
    document. F1 = 2|A∩B| / (|A|+|B|) is symmetric in A and B by construction, so there is no
    "reference" side to choose between two annotators."""
    total_a = total_b = total_shared = 0
    for a_spans, b_spans in pairs:
        a_set = {(s.begin, s.end, s.label) for s in a_spans}
        b_set = {(s.begin, s.end, s.label) for s in b_spans}
        total_a += len(a_set)
        total_b += len(b_set)
        total_shared += len(a_set & b_set)
    denom = total_a + total_b
    return {
        "a_spans": total_a, "b_spans": total_b, "shared": total_shared,
        "precision_a_vs_b": round(total_shared / total_a, 4) if total_a else None,
        "recall_a_vs_b": round(total_shared / total_b, 4) if total_b else None,
        "f1": round(2 * total_shared / denom, 4) if denom else None,
    }


def cohens_kappa_on_shared_extents(pairs: Sequence[SpanPair]) -> dict:
    """Cohen's kappa over extents where both annotators drew exactly one span."""
    labelled_pairs: list[tuple[str, str]] = []
    skipped_stacked = 0
    for a_spans, b_spans in pairs:
        by_extent_a: dict[tuple[int, int], list[str]] = {}
        by_extent_b: dict[tuple[int, int], list[str]] = {}
        for s in a_spans:
            by_extent_a.setdefault(s.extent, []).append(s.label)
        for s in b_spans:
            by_extent_b.setdefault(s.extent, []).append(s.label)
        for extent in set(by_extent_a) & set(by_extent_b):
            labels_a, labels_b = by_extent_a[extent], by_extent_b[extent]
            if len(labels_a) != 1 or len(labels_b) != 1:
                skipped_stacked += 1     # a stacked extent is not one categorical judgement
                continue
            labelled_pairs.append((labels_a[0], labels_b[0]))

    n = len(labelled_pairs)
    if n == 0:
        return {"n": 0, "skipped_stacked": skipped_stacked, "kappa": None,
                "observed_agreement": None, "expected_agreement": None, "labels": []}

    labels = sorted({l for pair in labelled_pairs for l in pair})
    confusion = Counter(labelled_pairs)
    row_marginal = Counter(a for a, _ in labelled_pairs)
    col_marginal = Counter(b for _, b in labelled_pairs)

    observed = sum(confusion[(l, l)] for l in labels) / n
    expected = sum((row_marginal[l] / n) * (col_marginal[l] / n) for l in labels)
    kappa = (observed - expected) / (1 - expected) if expected != 1 else None

    return {
        "n": n, "skipped_stacked": skipped_stacked, "labels": labels,
        "observed_agreement": round(observed, 4),
        "expected_agreement": round(expected, 4),
        "kappa": round(kappa, 4) if kappa is not None else None,
    }
