"""Grouping two annotators' spans into reviewable conflicts, and classifying their shape.

Two annotators' spans are grouped into connected components over character overlap. A component
is the smallest unit that can be decided independently: nothing outside it can change what the
right answer inside it is.

Components are then CLASSIFIED. Shape matters because a role clash on one identical extent and a
one-against-many segmentation disagreement need completely different things from the reviewer,
and presenting both as an undifferentiated list of spans is what makes an adjudication interface
unusable on real data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .model import Span, dedupe, sort_spans, structure_flags

A, B = "A", "B"

SHAPES = (
    "AGREEMENT",        # both annotators produced exactly the same spans
    "A_ONLY",           # only A annotated here
    "B_ONLY",           # only B annotated here
    "ROLE_CLASH",       # same extents, different roles
    "BOUNDARY_SHIFT",   # same roles, extents disagree
    "SEGMENTATION",     # one side has a single span where the other has several
    "NESTING_DIFF",     # one side expressed internal structure the other did not
    "MIXED",            # roles and boundaries both disagree, no cleaner description
)


def _components(items: list[tuple[Span, str]]) -> list[list[tuple[Span, str]]]:
    """Connected components over character overlap, by sweep line.

    Sorted by begin, so once a span ends before the current span starts it can never overlap
    anything later and is dropped from the active set.
    """
    order = sorted(range(len(items)), key=lambda i: (items[i][0].begin, -items[i][0].end))
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    active: list[int] = []
    for i in order:
        span = items[i][0]
        active = [j for j in active if items[j][0].end > span.begin]
        for j in active:
            if items[j][0].overlaps(span):
                union(i, j)
        active.append(i)

    groups: dict[int, list[tuple[Span, str]]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(items[i])
    return [g for _, g in sorted(groups.items(),
                                 key=lambda kv: (min(s.begin for s, _ in kv[1]),
                                                 max(s.end for s, _ in kv[1])))]


def classify(a_spans: Sequence[Span], b_spans: Sequence[Span]) -> str:
    if not a_spans:
        return "B_ONLY"
    if not b_spans:
        return "A_ONLY"
    if set(a_spans) == set(b_spans):
        return "AGREEMENT"

    a_ext = {s.extent for s in a_spans}
    b_ext = {s.extent for s in b_spans}
    a_lab = {s.label for s in a_spans}
    b_lab = {s.label for s in b_spans}

    if a_ext == b_ext:
        return "ROLE_CLASH"
    # Nesting is checked BEFORE segmentation. "One span where the other has three" describes both
    # shapes, but three spans nested inside one another is a disagreement about internal
    # structure, while three spans laid end to end is a disagreement about where to cut.
    a_nested = "NESTED" in structure_flags(a_spans)
    b_nested = "NESTED" in structure_flags(b_spans)
    if a_nested != b_nested:
        return "NESTING_DIFF"
    if (len(a_ext) == 1) != (len(b_ext) == 1):
        return "SEGMENTATION"
    if a_lab == b_lab:
        return "BOUNDARY_SHIFT"
    return "MIXED"


@dataclass
class Conflict:
    """One independently decidable unit of disagreement."""
    conflict_id: str
    doc_id: str
    index: int
    layer: str
    begin: int
    end: int
    shape: str
    agreed: bool
    a_spans: list[Span]
    b_spans: list[Span]
    flags: list[str] = field(default_factory=list)

    @property
    def extent(self) -> tuple[int, int]:
        return (self.begin, self.end)

    @property
    def span_count(self) -> int:
        return len(self.a_spans) + len(self.b_spans)

    def shared_extents(self) -> set[tuple[int, int]]:
        return {s.extent for s in self.a_spans} & {s.extent for s in self.b_spans}

    def labels_at(self, side: str, extent: tuple[int, int]) -> set[str]:
        spans = self.a_spans if side == A else self.b_spans
        return {s.label for s in spans if s.extent == extent}

    def as_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id, "doc_id": self.doc_id, "index": self.index,
            "layer": self.layer, "begin": self.begin, "end": self.end,
            "shape": self.shape, "agreed": self.agreed, "flags": self.flags,
            "span_count": self.span_count,
            "a_spans": [s.as_dict() for s in self.a_spans],
            "b_spans": [s.as_dict() for s in self.b_spans],
        }


def build_conflicts(doc_id: str, layer: str,
                    a_spans: Sequence[Span], b_spans: Sequence[Span]) -> list[Conflict]:
    """Group A and B into ordered, independently decidable conflicts. Deterministic."""
    items = [(s, A) for s in dedupe(a_spans)] + [(s, B) for s in dedupe(b_spans)]
    out: list[Conflict] = []
    for index, group in enumerate(_components(items)):
        a_side = sort_spans({s for s, side in group if side == A})
        b_side = sort_spans({s for s, side in group if side == B})
        shape = classify(a_side, b_side)
        flags = sorted(structure_flags(a_side) | structure_flags(b_side))
        out.append(Conflict(
            conflict_id=f"{doc_id}::{layer}::{index:04d}",
            doc_id=doc_id, index=index, layer=layer,
            begin=min(s.begin for s, _ in group), end=max(s.end for s, _ in group),
            shape=shape, agreed=(shape == "AGREEMENT"),
            a_spans=a_side, b_spans=b_side, flags=flags))
    return out
