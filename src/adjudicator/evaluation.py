"""How a resolved annotation actually compares with what A and B wrote.

These fields exist because `decision_type` cannot answer the question a paper needs to ask.
`TAKE_A` means "every span kept came from A" — a reviewer who keeps one of A's three spans still
produces `TAKE_A`. Reporting that as "the final annotation equals annotator A" would be false.

Everything here is derived from the span structures themselves, so a claim like "X% of
adjudications required an annotation different from both annotators" is exact and recomputable
from the archived export.

Four comparisons, each answering one question, and named for exactly what it compares:

* exact       — the set of (extent, role) pairs. This IS role assignment per extent.
* boundary    — the set of extents alone.
* role inventory — the set of roles alone, ignoring where they sit. Two annotations that swap
                the roles of two spans have the same inventory; that is what "inventory" means,
                and it is deliberately not called role equality.
* structure   — the pattern of containment, crossing and separation, ignoring exact offsets.

`role_reassigned_vs_X` is the case the inventory measure alone would hide: the same extents and
the same roles, but distributed differently over them.
"""
from __future__ import annotations

from typing import Iterable, Sequence

Extent = tuple[int, int]


def _triples(spans: Iterable) -> set[tuple[int, int, str]]:
    """Exact identity: which roles sit on which characters."""
    out: set[tuple[int, int, str]] = set()
    for span in spans:
        labels = getattr(span, "labels", None)
        if labels is None:
            out.add((span.begin, span.end, span.label))
        else:
            for label in labels:
                out.add((span.begin, span.end, label))
    return out


def _extents(spans: Iterable) -> set[Extent]:
    return {(s.begin, s.end) for s in spans}


def _role_inventory(spans: Iterable) -> set[str]:
    out: set[str] = set()
    for span in spans:
        labels = getattr(span, "labels", None)
        out.update(labels if labels is not None else [span.label])
    return out


def _relation(x: Extent, y: Extent) -> str:
    if x[0] <= y[0] and y[1] <= x[1]:
        return "contains"
    if y[0] <= x[0] and x[1] <= y[1]:
        return "inside"
    if x[0] < y[1] and y[0] < x[1]:
        return "crossing"
    return "disjoint"


def structure_signature(extents: Sequence[Extent]) -> tuple:
    """The SHAPE of an annotation, independent of exact offsets and of roles.

    Two annotations share a structure when they have the same number of distinct extents and the
    same pattern of containment, crossing and separation between them. This is deliberately not
    the same question as `boundary_equals_*`, which asks whether the exact character positions
    match: a reviewer can keep A's shape while moving every boundary.
    """
    ordered = sorted(set(extents))
    relations = tuple(_relation(ordered[i], ordered[j])
                      for i in range(len(ordered))
                      for j in range(i + 1, len(ordered)))
    return (len(ordered), relations)


def compare_to_sources(final_spans, a_spans, b_spans) -> dict:
    """Every comparison the evaluation needs, computed from the structures alone."""
    final_t, a_t, b_t = _triples(final_spans), _triples(a_spans), _triples(b_spans)
    final_e, a_e, b_e = _extents(final_spans), _extents(a_spans), _extents(b_spans)
    final_r = _role_inventory(final_spans)
    a_r, b_r = _role_inventory(a_spans), _role_inventory(b_spans)
    final_s = structure_signature(final_e)
    a_s, b_s = structure_signature(a_e), structure_signature(b_e)

    equals_a, equals_b = final_t == a_t, final_t == b_t
    boundary_a, boundary_b = final_e == a_e, final_e == b_e
    inventory_a, inventory_b = final_r == a_r, final_r == b_r
    structure_a, structure_b = final_s == a_s, final_s == b_s
    return {
        "final_exactly_equals_A": equals_a,
        "final_exactly_equals_B": equals_b,
        "final_exactly_equals_both": equals_a and equals_b,
        "final_differs_from_both": not equals_a and not equals_b,
        "boundary_equals_A": boundary_a,
        "boundary_equals_B": boundary_b,
        "boundary_differs_from_both": not boundary_a and not boundary_b,
        "role_inventory_equals_A": inventory_a,
        "role_inventory_equals_B": inventory_b,
        "role_inventory_differs_from_both": not inventory_a and not inventory_b,
        # same extents, same roles, but the roles sit on different extents than X put them
        "role_reassigned_vs_A": boundary_a and inventory_a and not equals_a,
        "role_reassigned_vs_B": boundary_b and inventory_b and not equals_b,
        "structure_equals_A": structure_a,
        "structure_equals_B": structure_b,
        "structure_differs_from_both": not structure_a and not structure_b,
    }


def decision_metadata(conflict, decision) -> dict:
    """One analysis row per adjudicated region. Contains no document text."""
    flags = compare_to_sources(decision.spans, conflict.a_spans, conflict.b_spans)
    return {
        "case_id": conflict.doc_id,
        "region_id": conflict.conflict_id,
        "layer": conflict.layer,
        "conflict_type": conflict.shape,
        "simple_or_structural": "structural" if (conflict.flags or conflict.span_count > 2)
                                else "simple",
        "flags": list(conflict.flags),
        "n_A_spans": len(conflict.a_spans),
        "n_B_spans": len(conflict.b_spans),
        "n_final_spans": len(decision.spans),
        "decision_type": decision.decision_type,
        **flags,
        "attempt_seconds": decision.attempt_seconds,
        "cumulative_seconds": decision.cumulative_seconds,
        "visit_count": decision.visit_count,
        "revision_count": decision.revision_count,
        "dropped": decision.decision_type == "DROP",
        "deferred": decision.is_open,
        "reviewed": decision.reviewed,
        "decided_at": decision.decided_at,
    }
