"""Domain model: spans, conflicts and decisions.

This module is deliberately ignorant of files, field names, HTTP and labels. It receives
already-normalised spans and a label inventory that a project supplied. Keeping it that way is
what makes the adjudication logic testable in isolation and reusable for other corpora.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- spans

@dataclass(frozen=True, order=True)
class Span:
    """A labelled character range. Half-open: [begin, end)."""
    begin: int
    end: int
    label: str
    layer: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.begin, int) or isinstance(self.begin, bool):
            raise TypeError(f"begin must be an int, got {self.begin!r}")
        if not isinstance(self.end, int) or isinstance(self.end, bool):
            raise TypeError(f"end must be an int, got {self.end!r}")
        if self.begin < 0:
            raise ValueError(f"begin must not be negative, got {self.begin}")
        if self.begin >= self.end:
            raise ValueError(f"empty or inverted span [{self.begin},{self.end})")
        if not self.label:
            raise ValueError("a span must carry a label")

    @property
    def extent(self) -> tuple[int, int]:
        return (self.begin, self.end)

    @property
    def length(self) -> int:
        return self.end - self.begin

    def overlaps(self, other: "Span") -> bool:
        """True when the two ranges share at least one character.

        Adjacency is not overlap: [0,5) and [5,9) merely touch.
        """
        return self.begin < other.end and other.begin < self.end

    def contains(self, other: "Span") -> bool:
        return self.begin <= other.begin and other.end <= self.end

    def crosses(self, other: "Span") -> bool:
        """Partial overlap where neither contains the other - the shape BIO cannot express."""
        return self.overlaps(other) and not self.contains(other) and not other.contains(self)

    def as_dict(self) -> dict:
        return {"begin": self.begin, "end": self.end, "label": self.label, "layer": self.layer}

    @staticmethod
    def from_dict(d: dict) -> "Span":
        return Span(int(d["begin"]), int(d["end"]), str(d["label"]), str(d.get("layer", "")))


def sort_spans(spans: Iterable[Span]) -> list[Span]:
    """Reading order: earliest start first, longest first on a tie, then label.

    Longest-first matters for display: a container is drawn before the spans nested inside it.
    """
    return sorted(spans, key=lambda s: (s.begin, -s.end, s.label))


def dedupe(spans: Iterable[Span]) -> list[Span]:
    return sort_spans(set(spans))


# --------------------------------------------------------------------------- structure

STRUCTURE_FLAGS = ("NESTED", "SAME_ROLE_NESTED", "CROSSING", "STACKED", "MULTI_EXTENT")


def structure_flags(spans: Sequence[Span]) -> set[str]:
    """Structural properties *within one annotator's own* spans."""
    flags: set[str] = set()
    extents: dict[tuple[int, int], int] = {}
    for s in spans:
        extents[s.extent] = extents.get(s.extent, 0) + 1
    if any(n > 1 for n in extents.values()):
        flags.add("STACKED")          # two roles on one identical extent
    if len(extents) > 1:
        flags.add("MULTI_EXTENT")
    for a in spans:
        for b in spans:
            if a is b or a == b:
                continue
            if a.extent == b.extent:
                continue
            if b.contains(a):
                flags.add("NESTED")
                if a.label == b.label:
                    flags.add("SAME_ROLE_NESTED")
            elif a.crosses(b):
                flags.add("CROSSING")
    return flags
