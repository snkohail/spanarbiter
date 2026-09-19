"""What the tool does with input that is not well formed.

A tool other people point at their own corpora is judged by this file more than by any feature.
The rule throughout: refuse with a message that names the file and the problem, or accept and say
exactly what was assumed. Never a traceback, and never silent acceptance of something wrong.
"""
import hashlib
import json
import os

import pytest

from adjudicator.project import Project

TEXT = "The court considered the submissions of both parties and found as follows."
SHA = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
SPAN = [{"begin": 0, "end": 9, "label": "COURT"}]


def corpus(tmp_path, a_records, b_records, raw_a=None):
    root = tmp_path / "corpus"
    for side, records in (("annotator_A", a_records), ("annotator_B", b_records)):
        directory = root / side
        directory.mkdir(parents=True, exist_ok=True)
        if raw_a is not None and side == "annotator_A":
            (directory / "raw.json").write_bytes(raw_a)
            continue
        for index, record in enumerate(records):
            (directory / f"D{index}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return Project(str(root / "annotator_A"), str(root / "annotator_B"), str(root / "out"))


def a_record(spans, **extra):
    return {"doc_id": "D1", "text": TEXT, "text_sha256": SHA, "spans": spans, **extra}


def b_record(spans, **extra):
    return {"doc_id": "D1", "text_sha256": SHA, "spans": spans, **extra}


def refusal(project):
    """The first blocking message, or None if the corpus was accepted."""
    blocking = project.preflight()["blocking"]
    return blocking[0]["message"] if blocking else None


BROKEN = [
    ("no annotation files", lambda: ([], [b_record(SPAN)]),                    "looked for"),
    ("bytes that are not json", None,                                          "no readable JSON"),
    ("no text anywhere",    lambda: ([{"doc_id": "D1", "spans": SPAN}],
                                     [b_record(SPAN)]),                        "document text"),
    ("checksum disagrees",  lambda: ([a_record(SPAN)],
                                     [b_record(SPAN, text_sha256="0" * 64)]),  "sha256"),
    ("begin after end",     lambda: ([a_record([{"begin": 20, "end": 5, "label": "X"}])],
                                     [b_record(SPAN)]),                        "unusable span"),
    ("end past the text",   lambda: ([a_record([{"begin": 0, "end": 9999, "label": "X"}])],
                                     [b_record(SPAN)]),                        "characters"),
    ("negative begin",      lambda: ([a_record([{"begin": -5, "end": 9, "label": "X"}])],
                                     [b_record(SPAN)]),                        "unusable span"),
    ("zero length span",    lambda: ([a_record([{"begin": 4, "end": 4, "label": "X"}])],
                                     [b_record(SPAN)]),                        "unusable span"),
    ("label is null",       lambda: ([a_record([{"begin": 0, "end": 9, "label": None}])],
                                     [b_record(SPAN)]),                        "unusable span"),
    ("fractional offsets",  lambda: ([a_record([{"begin": 0.0, "end": 9.5, "label": "X"}])],
                                     [b_record(SPAN)]),                        "unusable span"),
    ("spans is not a list", lambda: ([a_record({"a": 1})], [b_record(SPAN)]),   "begin"),
    ("one side is empty",   lambda: ([a_record(SPAN)], []),                     "looked for"),
    ("duplicate doc ids",   lambda: ([a_record(SPAN), a_record(SPAN)],
                                     [b_record(SPAN)]),                        "duplicate document id"),
]


@pytest.mark.parametrize("name,build,expected", BROKEN, ids=[b[0] for b in BROKEN])
def test_a_broken_corpus_is_refused_by_name(tmp_path, name, build, expected):
    if build is None:
        project = corpus(tmp_path, [], [b_record(SPAN)], raw_a=b"\x00\x01 not json {{{")
    else:
        a, b = build()
        project = corpus(tmp_path, a, b)
    message = refusal(project)
    assert message, f"{name} was accepted silently"
    assert expected in message, (name, message)
    assert not project.usable


def test_nothing_raises_a_traceback_at_the_reviewer(tmp_path):
    """Every refusal above must arrive as a preflight message, not an exception."""
    for name, build, _ in BROKEN:
        try:
            if build is None:
                corpus(tmp_path / name, [], [b_record(SPAN)], raw_a=b"\x00\x01")
            else:
                a, b = build()
                corpus(tmp_path / name, a, b)
        except Exception as exc:                                    # noqa: BLE001
            pytest.fail(f"{name} raised {type(exc).__name__}: {exc}")


def test_a_missing_document_id_field_is_declared_not_guessed_in_silence(tmp_path):
    """Falling back to the file name is reasonable — pairing then depends entirely on both
    annotators naming their files identically, which the reviewer has to be told."""
    project = corpus(tmp_path,
                     [{"text": TEXT, "text_sha256": SHA, "spans": SPAN}],
                     [{"text_sha256": SHA, "spans": SPAN}])
    pf = project.preflight()
    assert project.usable
    assert pf["mapping_a"]["doc_id"], "the mapping must say where document ids came from"
    assert "file name" in pf["mapping_a"]["doc_id"]
    assert any("file name" in w["message"] for w in pf["warnings"]), pf["warnings"]


def test_a_document_only_one_annotator_really_annotated_is_flagged(tmp_path):
    """The failure this catches: one annotator barely annotated a document, and every span the
    other wrote arrives as an ordinary B_ONLY disagreement. Adjudicating those records the
    reviewer as having overridden an annotator who was never there."""
    long_text = TEXT * 20
    sha = hashlib.sha256(long_text.encode("utf-8")).hexdigest()
    thorough = [{"begin": i, "end": i + 60, "label": "FACTS"}
                for i in range(0, len(long_text) - 60, 60)]
    project = corpus(tmp_path,
                     [{"doc_id": "D1", "text": long_text, "text_sha256": sha,
                       "spans": [{"begin": 0, "end": 40, "label": "FACTS"}]}],
                     [{"doc_id": "D1", "text_sha256": sha, "spans": thorough}])
    messages = " ".join(w["message"] for w in project.preflight()["warnings"])
    assert "annotated" in messages.lower(), project.preflight()["warnings"]
    assert "D1" in messages or any(
        "D1" in str(w.get("doc_id")) for w in project.preflight()["warnings"])


def test_a_balanced_corpus_raises_no_such_warning(tmp_path):
    project = corpus(tmp_path, [a_record(SPAN)], [b_record(SPAN)])
    messages = " ".join(w["message"] for w in project.preflight()["warnings"])
    assert "barely" not in messages and "only one annotator really" not in messages
