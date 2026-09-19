"""Adapter for a directory of JSON / JSONL annotation files.

Field names are DETECTED and reported, never assumed. Detection looks at both annotators at once,
because a corpus where only one side carries the document text would otherwise be undetectable
from the other side alone.
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter

from ..model import Span
from .base import Document

# Candidate field names in priority order. What was chosen is always reported back to the user.
CANDIDATES = {
    "doc_id": ["canonical_case_id", "document_id", "doc_id", "case_id", "id", "name"],
    "text":   ["text", "content", "body", "document", "raw_text", "full_text"],
    "spans":  ["spans", "annotations", "entities", "mentions", "markables", "labels"],
    "begin":  ["begin", "start", "start_offset", "char_start", "offset_start", "from", "b"],
    "end":    ["end", "stop", "end_offset", "char_end", "offset_end", "to", "e"],
    "label":  ["label", "tag", "role", "category", "class", "value", "type"],
    "layer":  ["kind", "layer", "annotation_type", "level", "namespace"],
    "sents":  ["sentences_auto", "sentences", "sentence_offsets"],
}
DEFAULT_LAYER = "default"


class FormatError(Exception):
    """The directory cannot be read as an annotation corpus. Never swallowed."""


def _as_offset(value) -> int:
    """Offsets arrive as ints or as strings ('347'). Both are accepted; nothing else is."""
    if isinstance(value, bool):
        raise ValueError(f"boolean is not an offset: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value.strip())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError(f"not a character offset: {value!r}")


def read_records(directory: str) -> tuple[list[tuple[str, dict]], list[dict]]:
    """Every .json / .jsonl record in a directory, plus a report of what could not be read."""
    if not os.path.isdir(directory):
        raise FormatError(f"not a directory: {directory}")
    paths = sorted(glob.glob(os.path.join(directory, "*.json"))
                   + glob.glob(os.path.join(directory, "*.jsonl")))
    if not paths:
        raise FormatError(f"no .json or .jsonl files in {directory}")
    records: list[tuple[str, dict]] = []
    unreadable: list[dict] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                if path.endswith(".jsonl"):
                    for lineno, line in enumerate(fh, 1):
                        if line.strip():
                            obj = json.loads(line)
                            if not isinstance(obj, dict):
                                raise TypeError(f"line {lineno} is not a JSON object")
                            records.append((path, obj))
                else:
                    obj = json.load(fh)
                    if not isinstance(obj, dict):
                        raise TypeError("file is not a JSON object")
                    records.append((path, obj))
        except Exception as exc:
            unreadable.append({"file": os.path.basename(path),
                               "error": f"{type(exc).__name__}: {exc}"})
    if not records:
        detail = "; ".join(f"{u['file']}: {u['error']}" for u in unreadable[:3])
        raise FormatError(f"no readable JSON object in {directory} ({detail})")
    return records, unreadable


def detect_mapping(records: list[dict], override: dict | None = None) -> tuple[dict, list[str]]:
    """Choose a field name for each role. Returns (mapping, human-readable notes)."""
    override = override or {}
    notes: list[str] = []
    mapping: dict[str, str | None] = {}

    top = Counter()
    for rec in records:
        top.update(rec.keys())

    for role in ("doc_id", "text", "spans", "sents"):
        if role in override:
            mapping[role] = override[role]
            notes.append(f'{role}: "{override[role]}" (set in config)')
            continue
        found = [c for c in CANDIDATES[role] if c in top]
        mapping[role] = found[0] if found else None
        if found:
            notes.append(f'{role}: "{found[0]}"'
                         + (f" (also present: {found[1:]})" if len(found) > 1 else ""))
    if not mapping["spans"]:
        raise FormatError(f"no span list found; looked for {CANDIDATES['spans']}")

    span_keys = Counter()
    for rec in records:
        for s in rec.get(mapping["spans"]) or []:
            if isinstance(s, dict):
                span_keys.update(s.keys())
    for role in ("begin", "end", "label", "layer"):
        if role in override:
            mapping[role] = override[role]
            notes.append(f'span.{role}: "{override[role]}" (set in config)')
            continue
        found = [c for c in CANDIDATES[role] if c in span_keys]
        mapping[role] = found[0] if found else None
        if found:
            notes.append(f'span.{role}: "{found[0]}"'
                         + (f" (also present: {found[1:]})" if len(found) > 1 else ""))
    for role in ("begin", "end", "label"):
        if not mapping[role]:
            raise FormatError(f"no span.{role} field found; looked for {CANDIDATES[role]}")
    if not mapping["layer"]:
        notes.append(f'span.layer: no layer field; every span treated as "{DEFAULT_LAYER}"')
    return mapping, notes


def build_document(path: str, rec: dict, mapping: dict) -> Document:
    fallback = os.path.basename(path).rsplit(".", 1)[0]
    raw_id = rec.get(mapping["doc_id"]) if mapping.get("doc_id") else None
    doc = Document(doc_id=str(raw_id) if raw_id is not None else fallback,
                   spans=[], source_file=os.path.basename(path), raw=rec)

    if mapping.get("text"):
        value = rec.get(mapping["text"])
        doc.text = value if isinstance(value, str) else None
    sha = rec.get("text_sha256")
    doc.text_sha256 = sha if isinstance(sha, str) else None

    if mapping.get("sents"):
        for pair in rec.get(mapping["sents"]) or []:
            try:
                doc.sentences.append((_as_offset(pair[0]), _as_offset(pair[1])))
            except Exception:
                continue

    for raw_span in rec.get(mapping["spans"]) or []:
        if not isinstance(raw_span, dict):
            doc.issues.append(f"span is not an object: {raw_span!r}")
            continue
        try:
            begin = _as_offset(raw_span[mapping["begin"]])
            end = _as_offset(raw_span[mapping["end"]])
            label = raw_span[mapping["label"]]
            if label is None or str(label) == "":
                raise ValueError("span has no label")
            layer = (str(raw_span.get(mapping["layer"]))
                     if mapping.get("layer") and raw_span.get(mapping["layer"]) is not None
                     else DEFAULT_LAYER)
            doc.spans.append(Span(begin, end, str(label), layer))
        except Exception as exc:
            doc.issues.append(f"unusable span {raw_span!r}: {exc}")
    return doc


def load_directory(directory: str, mapping: dict) -> tuple[dict[str, Document], list[dict]]:
    """Build documents keyed by id. A repeated id is reported, never silently overwritten."""
    records, unreadable = read_records(directory)
    docs: dict[str, Document] = {}
    problems = list(unreadable)
    for path, rec in records:
        doc = build_document(path, rec, mapping)
        if doc.doc_id in docs:
            problems.append({"file": doc.source_file,
                             "error": f"duplicate document id {doc.doc_id!r}, already loaded "
                                      f"from {docs[doc.doc_id].source_file}"})
            continue
        docs[doc.doc_id] = doc
    return docs, problems
