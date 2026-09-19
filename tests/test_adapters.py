"""Reading real-world annotation files without guessing."""
import json

import pytest

from adjudicator.adapters.json_dir import (
    DEFAULT_LAYER, FormatError, _as_offset, build_document, detect_mapping, load_directory,
    read_records,
)
from conftest import TEXT, sha_of, write_doc


def test_offsets_may_be_integers_or_numeric_strings():
    assert _as_offset(12) == 12
    assert _as_offset("347") == 347
    assert _as_offset(" 8 ") == 8


@pytest.mark.parametrize("value", [True, False, "twelve", None, "12.5", [12]])
def test_anything_else_is_not_an_offset(value):
    with pytest.raises(ValueError):
        _as_offset(value)


def test_field_names_are_detected_from_both_annotators_together(tmp_path):
    """Only A carries the text. Inference that looked at B alone would find no text field."""
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 5, "X")], text=TEXT, sha=sha_of(TEXT))
    write_doc(b, "D1", [(0, 5, "X")], sha=sha_of(TEXT))
    records = [r for _, r in read_records(str(a))[0]] + [r for _, r in read_records(str(b))[0]]
    mapping, notes = detect_mapping(records)
    assert mapping["text"] == "text" and mapping["doc_id"] == "canonical_case_id"
    assert mapping["layer"] == "kind" and mapping["begin"] == "begin"
    assert any("text" in note for note in notes)


def test_a_corpus_with_no_layer_field_gets_one_default_layer(tmp_path):
    directory = tmp_path / "A"
    directory.mkdir()
    (directory / "d.json").write_text(json.dumps(
        {"id": "d", "text": TEXT, "spans": [{"begin": 0, "end": 4, "label": "X"}]}), encoding="utf-8")
    records, _ = read_records(str(directory))
    mapping, notes = detect_mapping([r for _, r in records])
    assert mapping["layer"] is None
    document = build_document("d.json", records[0][1], mapping)
    assert document.spans[0].layer == DEFAULT_LAYER
    assert any("no layer field" in n for n in notes)


def test_string_offsets_load_as_integers(tmp_path):
    write_doc(tmp_path / "A", "D1", [(3, 9, "X")], text=TEXT, string_offsets=True)
    records, _ = read_records(str(tmp_path / "A"))
    mapping, _ = detect_mapping([r for _, r in records])
    document = build_document("D1.json", records[0][1], mapping)
    assert document.spans[0].begin == 3 and isinstance(document.spans[0].begin, int)


def test_a_repeated_document_id_is_reported_not_overwritten(tmp_path):
    directory = tmp_path / "A"
    write_doc(directory, "one", [(0, 5, "X")], text=TEXT)
    write_doc(directory, "two", [(0, 5, "Y")], text=TEXT)
    for name in ("one", "two"):
        payload = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        payload["canonical_case_id"] = "SAME"
        (directory / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    records, _ = read_records(str(directory))
    mapping, _ = detect_mapping([r for _, r in records])
    docs, problems = load_directory(str(directory), mapping)
    assert len(docs) == 1
    assert any("duplicate document id" in p["error"] for p in problems)


def test_an_unusable_span_is_recorded_against_its_document(tmp_path):
    directory = tmp_path / "A"
    directory.mkdir()
    (directory / "d.json").write_text(json.dumps({
        "canonical_case_id": "d", "text": TEXT,
        "spans": [{"kind": "r", "label": "X", "begin": 0, "end": 5},
                  {"kind": "r", "label": "Y", "begin": "oops", "end": 9}]}), encoding="utf-8")
    records, _ = read_records(str(directory))
    mapping, _ = detect_mapping([r for _, r in records])
    document = build_document("d.json", records[0][1], mapping)
    assert len(document.spans) == 1 and len(document.issues) == 1


def test_an_empty_or_missing_directory_is_an_error(tmp_path):
    with pytest.raises(FormatError, match="not a directory"):
        read_records(str(tmp_path / "nope"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(FormatError, match="no .json"):
        read_records(str(tmp_path / "empty"))


def test_unparsable_json_is_reported_rather_than_skipped(tmp_path):
    directory = tmp_path / "A"
    write_doc(directory, "good", [(0, 5, "X")], text=TEXT)
    (directory / "bad.json").write_text("{ not json", encoding="utf-8")
    records, unreadable = read_records(str(directory))
    assert len(records) == 1 and unreadable[0]["file"] == "bad.json"
