"""brat standoff annotation: a `.txt` holding the document, a `.ann` holding the annotations.

Only text-bound annotations (`T` lines) describe spans. Relations, events, attributes, equivalence
and notes are read past without complaint — they are legitimate brat, they simply are not spans,
and refusing a corpus because it also records relations would be useless behaviour.
"""
from __future__ import annotations

import glob
import os

from ..model import Span
from .base import Document

DEFAULT_LAYER = "default"


def _parse_ann(text: str, body: str) -> tuple[list[Span], list[str]]:
    spans: list[Span] = []
    issues: list[str] = []
    for line in body.splitlines():
        if not line.startswith("T"):
            continue                                  # R, E, A, M, N, #, * — not spans
        parts = line.split("\t")
        identifier = parts[0]
        if len(parts) < 2 or " " not in parts[1]:
            issues.append(f"{identifier}: not a text-bound annotation line: {line[:60]!r}")
            continue
        label, _, offsets = parts[1].partition(" ")
        surface = parts[2] if len(parts) > 2 else None

        if ";" in offsets:
            # brat writes "4 9;37 46" for an annotation covering two separate pieces of text.
            # A span here is one extent, so the enclosing range would invent an annotation that
            # covers text nobody labelled.
            issues.append(f"{identifier}: discontinuous annotation ({offsets}) cannot be "
                          f"represented as a single span; split it in brat first")
            continue
        try:
            begin, end = (int(part) for part in offsets.split())
        except ValueError:
            issues.append(f"{identifier}: offsets are not two integers: {offsets!r}")
            continue
        if not 0 <= begin < end <= len(text):
            issues.append(f"{identifier}: {label} at [{begin},{end}) but the document is "
                          f"{len(text)} characters")
            continue
        # The third column is brat's own copy of the covered text, with line breaks written as
        # spaces. When it disagrees with the offsets, one of the two files has been edited since
        # and every offset is suspect.
        covered = text[begin:end].replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        if surface is not None and covered != surface:
            issues.append(f"{identifier}: the .ann says {surface!r} at [{begin},{end}) but the "
                          f".txt has {text[begin:end]!r}; offsets and text disagree")
            continue
        try:
            spans.append(Span(begin, end, label, DEFAULT_LAYER))
        except (TypeError, ValueError) as exc:
            issues.append(f"{identifier}: {exc}")
    return spans, issues


def read_directory(directory: str) -> tuple[dict[str, Document], list[dict]]:
    docs: dict[str, Document] = {}
    problems: list[dict] = []
    for ann_path in sorted(glob.glob(os.path.join(directory, "*.ann"))):
        name = os.path.basename(ann_path)
        doc_id = name[:-4]
        txt_path = os.path.join(directory, f"{doc_id}.txt")
        if not os.path.isfile(txt_path):
            problems.append({"file": name,
                             "error": f"no matching {doc_id}.txt; brat keeps the text beside "
                                      f"the annotations and the offsets are measured against it"})
            continue
        try:
            with open(txt_path, encoding="utf-8") as fh:
                text = fh.read()
            with open(ann_path, encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            problems.append({"file": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        spans, issues = _parse_ann(text, body)
        docs[doc_id] = Document(doc_id=doc_id, spans=spans, text=text, source_file=name,
                                raw={"format": "brat"}, issues=issues)
    return docs, problems
