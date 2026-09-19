"""Shared fixtures. Synthetic corpora only - no real annotation data in the test suite."""
from __future__ import annotations

import hashlib
import json

import pytest

from adjudicator.model import Span
from adjudicator.project import Project
from adjudicator.store import DecisionStore

ROLES = ["PREAMBLE", "FACTS", "ISSUE", "ANALYSIS", "LAW_REFERENCE", "DECISION"]
TEXT = ("The court considered the matter. " * 12) + ("Judgment follows below. " * 12)


def write_doc(directory, doc_id, spans, *, text=None, sha=None, layer="rhetorical",
              string_offsets=False, extra=None):
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"canonical_case_id": doc_id, "spans": [
        {"kind": layer, "label": label,
         "begin": str(begin) if string_offsets else begin,
         "end": str(end) if string_offsets else end}
        for begin, end, label in spans]}
    if text is not None:
        payload["text"] = text
        payload["sentences_auto"] = [[0, len(text) // 2], [len(text) // 2, len(text)]]
    if sha is not None:
        payload["text_sha256"] = sha
    payload.update(extra or {})
    (directory / f"{doc_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def sha_of(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def corpus(tmp_path):
    """Annotator A carries the text; B carries only a checksum - the realistic blind setup."""
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 40, "PREAMBLE"), (60, 120, "FACTS"), (200, 260, "ANALYSIS")],
              text=TEXT, sha=sha_of(TEXT))
    write_doc(b, "D1", [(0, 40, "PREAMBLE"), (60, 120, "ISSUE"), (300, 340, "DECISION")],
              sha=sha_of(TEXT))
    return {"a": str(a), "b": str(b), "out": str(tmp_path / "out"), "text": TEXT}


@pytest.fixture
def project(corpus):
    return Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")


@pytest.fixture
def store(corpus):
    return DecisionStore(corpus["out"])


def S(begin, end, label, layer="rhetorical"):
    return Span(begin, end, label, layer)
