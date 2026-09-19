"""What a loaded document looks like, whatever format it arrived in."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Span


@dataclass
class Document:
    doc_id: str
    spans: list[Span]
    text: str | None = None
    text_sha256: str | None = None
    sentences: list[tuple[int, int]] = field(default_factory=list)
    source_file: str = ""
    raw: dict = field(default_factory=dict)      # kept verbatim, so export can round-trip
    issues: list[str] = field(default_factory=list)      # block loading
    notes: list[str] = field(default_factory=list)       # accepted, but interpreted; warned

    def layers(self) -> set[str]:
        return {s.layer for s in self.spans}

    def spans_in(self, layer: str) -> list[Span]:
        return [s for s in self.spans if s.layer == layer]
