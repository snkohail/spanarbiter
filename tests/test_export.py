"""Export: lossless canonical record first, round-trip mirror second."""
import json

from adjudicator.decisions import ResolvedSpan, auto_agree, decide
from adjudicator.export import (
    canonical_document, export_project, resolved_spans, roundtrip_document, validate,
)


def settle(project, store, doc_id="D1", flag_one=False):
    conflicts = project.conflicts(doc_id)
    decisions = {}
    for index, conflict in enumerate(conflicts):
        if conflict.agreed:
            decisions[conflict.conflict_id] = auto_agree(conflict)
            continue
        if flag_one and index == 1:
            decisions[conflict.conflict_id] = decide(conflict, [], intent="DEFER")
            continue
        side, spans = ("A", conflict.a_spans) if conflict.a_spans else ("B", conflict.b_spans)
        decisions[conflict.conflict_id] = decide(
            conflict, [ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans])
    store.save(doc_id, project.layer, decisions)
    return conflicts, decisions


def test_every_resolved_span_carries_its_full_provenance(project, store):
    conflicts, decisions = settle(project, store)
    spans = resolved_spans(conflicts, decisions)
    assert spans
    for span in spans:
        provenance = span["provenance"]
        assert provenance["origin"] in ("A", "B", "reviewer")
        assert provenance["decision_type"] and provenance["conflict_shape"]
        assert "annotator_a" in provenance and "annotator_b" in provenance


def test_dropped_and_flagged_items_produce_no_spans(project, store):
    conflicts, decisions = settle(project, store, flag_one=True)
    flagged = [d for d in decisions.values() if d.decision_type == "DEFER"]
    assert flagged
    ids = {s["provenance"]["conflict_id"] for s in resolved_spans(conflicts, decisions)}
    assert flagged[0].conflict_id not in ids


def test_a_document_with_a_flag_is_not_complete(project, store):
    conflicts, decisions = settle(project, store, flag_one=True)
    payload = canonical_document(project, "D1", conflicts, decisions)
    assert payload["adjudication"]["complete"] is False
    assert payload["adjudication"]["flagged"] == 1


def test_a_fully_settled_document_is_complete(project, store):
    conflicts, decisions = settle(project, store)
    payload = canonical_document(project, "D1", conflicts, decisions)
    assert payload["adjudication"]["complete"] is True
    assert payload["text_sha256"] and payload["layer"] == "rhetorical"


def test_validate_allows_nesting_and_stacking_but_catches_corruption(project, store):
    conflicts, decisions = settle(project, store)
    payload = canonical_document(project, "D1", conflicts, decisions)
    assert validate(payload["spans"], payload["text_length"], project.labels) == []
    broken = [{**payload["spans"][0], "end": payload["text_length"] + 10}]
    assert validate(broken, payload["text_length"], project.labels)
    unknown = [{**payload["spans"][0], "label": "NOT_A_ROLE"}]
    assert validate(unknown, payload["text_length"], project.labels)


def test_incomplete_documents_are_skipped_by_default(project, store, tmp_path):
    settle(project, store, flag_one=True)
    result = export_project(project, store, out_dir=str(tmp_path / "e1"))
    assert result.written == [] and result.skipped
    forced = export_project(project, store, out_dir=str(tmp_path / "e2"), only_complete=False)
    assert forced.written == ["D1"]


def test_roundtrip_preserves_other_layers_and_original_fields(project, store, tmp_path):
    conflicts, decisions = settle(project, store)
    payload = canonical_document(project, "D1", conflicts, decisions)
    mirrored = roundtrip_document(project, "D1", payload["spans"])
    assert mirrored["canonical_case_id"] == "D1"
    assert "text" in mirrored and mirrored["adjudication_provenance"]["layer"] == "rhetorical"
    assert all(s.get("kind") == "rhetorical" for s in mirrored["spans"])


def test_export_writes_both_products(project, store, tmp_path):
    settle(project, store)
    out = tmp_path / "export"
    result = export_project(project, store, out_dir=str(out), roundtrip=True)
    assert result.written == ["D1"] and result.span_count > 0
    canonical = json.loads((out / "canonical" / "D1.json").read_text(encoding="utf-8"))
    assert canonical["spans"][0]["provenance"]["origin"]
    assert (out / "roundtrip" / "D1.json").exists()
