"""P0 integrity: the guarantees that must hold before any real adjudication is collected.

Each test here fails on the build that preceded this phase.
"""
import json

import pytest

from adjudicator.conflicts import build_conflicts
from adjudicator.decisions import ResolvedSpan, decide, source_fingerprint, stale_decisions
from adjudicator.export import export_project, roundtrip_document
from adjudicator.project import Project
from adjudicator.store import DecisionStore
from conftest import TEXT, S, sha_of, write_doc


# ------------------------------------------------------- 0.1 independent adapters
def test_each_annotator_is_parsed_with_its_own_schema(tmp_path):
    """A and B may legitimately use different field names. One inferred mapping applied to both
    reads one side correctly and silently sees nothing in the other."""
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    (a / "d.json").write_text(json.dumps(
        {"id": "d", "text": TEXT, "spans": [{"begin": 0, "end": 5, "label": "X"}]}),
        encoding="utf-8")
    (b / "d.json").write_text(json.dumps(
        {"document_id": "d", "content": TEXT,
         "annotations": [{"start": 0, "stop": 5, "type": "Y"}]}), encoding="utf-8")

    project = Project(str(a), str(b), str(tmp_path / "out"))
    assert project.usable, [i.message for i in project.blocking]
    paired = project.docs["d"]
    assert len(paired.a.spans) == 1 and len(paired.b.spans) == 1
    assert {s.label for s in paired.b.spans} == {"Y"}
    assert project.mapping_a["spans"] == "spans"
    assert project.mapping_b["spans"] == "annotations"
    conflict = project.conflicts("d")[0]
    assert conflict.shape == "ROLE_CLASH"


def test_an_annotator_that_parses_to_nothing_is_a_blocking_error(tmp_path):
    """Never report 'no annotations' when the truth is 'the file was not understood'."""
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    (a / "d.json").write_text(json.dumps(
        {"id": "d", "text": TEXT, "spans": [{"begin": 0, "end": 5, "label": "X"}]}),
        encoding="utf-8")
    (b / "d.json").write_text(json.dumps(
        {"id": "d", "spans": [{"from_char": 0, "to_char": 5, "kind_of": "Y"}]}), encoding="utf-8")
    project = Project(str(a), str(b), str(tmp_path / "out"))
    assert not project.usable
    assert any(i.kind in ("format", "empty_side") for i in project.blocking)


# ------------------------------------------------------- 0.2 fingerprint validation
def test_a_decision_is_rejected_once_its_source_annotation_changes(corpus, tmp_path):
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")
    store = DecisionStore(corpus["out"])
    conflict = next(c for c in project.conflicts("D1") if not c.agreed)
    span = conflict.a_spans[0] if conflict.a_spans else conflict.b_spans[0]
    side = "A" if conflict.a_spans else "B"
    decision = decide(conflict, [ResolvedSpan(span.begin, span.end, (span.label,), side)])
    store.save("D1", "rhetorical", {conflict.conflict_id: decision})

    fresh = build_conflicts("D1", "rhetorical", [S(0, 40, "PREAMBLE")], [S(0, 41, "PREAMBLE")])
    stale = stale_decisions(fresh, {decision.conflict_id: decision})
    assert stale, "a decision made against different source spans was not detected"
    assert "changed" in stale[0]["reason"] or "no longer" in stale[0]["reason"]


def test_a_stale_decision_is_never_exported(corpus, tmp_path):
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")
    store = DecisionStore(corpus["out"])
    conflicts = project.conflicts("D1")
    saved = {}
    for c in conflicts:
        side, spans = ("A", c.a_spans) if c.a_spans else ("B", c.b_spans)
        saved[c.conflict_id] = decide(
            c, [ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans])
    store.save("D1", "rhetorical", saved, sha_of(corpus["text"]))
    clean = export_project(project, store, out_dir=str(tmp_path / "e1"))
    assert clean.written == ["D1"]

    # the source annotation is edited after the fact, under a decision the reviewer made
    forged = dict(saved)
    first = next(c for c in conflicts if not c.agreed)
    forged[first.conflict_id].fingerprint = "0000000000000000"
    store.save("D1", "rhetorical", forged, sha_of(corpus["text"]))
    after = export_project(project, store, out_dir=str(tmp_path / "e2"))
    assert after.written == [], "a decision whose source changed was still exported"
    assert any("source" in reason for _, reason in after.skipped)
    # the void decision is not exported even when incomplete documents are asked for
    forced = export_project(project, store, out_dir=str(tmp_path / "e3"), only_complete=False)
    assert forced.written == ["D1"]
    payload = json.loads((tmp_path / "e3" / "canonical" / "D1.json").read_text(encoding="utf-8"))
    assert first.conflict_id not in {s["provenance"]["conflict_id"] for s in payload["spans"]}
    assert payload["adjudication"]["stale"] == 1 and payload["adjudication"]["undecided"] == 1


def test_a_stale_decision_does_not_count_as_resolved(corpus):
    from adjudicator.progress import document_progress
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")
    conflicts = project.conflicts("D1")
    c = next(x for x in conflicts if not x.agreed)
    side, spans = ("A", c.a_spans) if c.a_spans else ("B", c.b_spans)
    good = decide(c, [ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans])
    assert document_progress(conflicts, {c.conflict_id: good})["settled"] == 1
    good.fingerprint = "deadbeefdeadbeef"
    p = document_progress(conflicts, {c.conflict_id: good})
    assert p["settled"] == 0 and p["stale"] == 1 and not p["complete"]


# ------------------------------------------------------- 0.3 text hash validation
def test_a_decision_log_written_against_different_text_is_refused(corpus):
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")
    store = DecisionStore(corpus["out"])
    conflict = next(c for c in project.conflicts("D1") if not c.agreed)
    side, spans = ("A", conflict.a_spans) if conflict.a_spans else ("B", conflict.b_spans)
    decision = decide(conflict, [ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans])
    store.save("D1", "rhetorical", {conflict.conflict_id: decision}, "not-the-right-hash")

    from adjudicator.store import StaleTextError
    with pytest.raises(StaleTextError, match="text"):
        store.load("D1", "rhetorical", expect_text_sha256=sha_of(corpus["text"]))
    # ... and loading without a claim still works, for tooling that has no text
    assert store.load("D1", "rhetorical")


# ------------------------------------------------------- 0.4 default-layer round-trip
def test_round_trip_replaces_rather_than_duplicates_without_a_layer_field(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    original = {"id": "d", "text": TEXT, "text_sha256": sha_of(TEXT),
                "spans": [{"begin": 0, "end": 5, "label": "X"}]}
    (a / "d.json").write_text(json.dumps(original), encoding="utf-8")
    (b / "d.json").write_text(json.dumps(
        {"id": "d", "text_sha256": sha_of(TEXT),
         "spans": [{"begin": 0, "end": 6, "label": "X"}]}), encoding="utf-8")
    project = Project(str(a), str(b), str(tmp_path / "out"))
    assert project.mapping_a.get("layer") is None

    mirrored = roundtrip_document(project, "d", [{"begin": 0, "end": 5, "label": "X",
                                                  "layer": project.layer}])
    assert len(mirrored["spans"]) == 1, mirrored["spans"]
    assert mirrored["spans"][0]["adjudicated"] is True


def test_round_trip_leaves_other_layers_untouched(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "d", [(0, 5, "X")], text=TEXT, sha=sha_of(TEXT), layer="rhetorical")
    write_doc(b, "d", [(0, 6, "X")], sha=sha_of(TEXT), layer="rhetorical")
    raw = json.loads((a / "d.json").read_text(encoding="utf-8"))
    raw["spans"].append({"kind": "entity", "label": "COURT", "begin": 0, "end": 3})
    (a / "d.json").write_text(json.dumps(raw), encoding="utf-8")

    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    mirrored = roundtrip_document(project, "d", [{"begin": 0, "end": 5, "label": "X",
                                                  "layer": "rhetorical"}])
    kinds = [s.get("kind") for s in mirrored["spans"]]
    assert kinds.count("entity") == 1 and kinds.count("rhetorical") == 1


# ------------------------------------------------------- 0.5 evaluation-derived fields
from adjudicator.evaluation import compare_to_sources, decision_metadata, structure_signature


def _conflict(a, b):
    return build_conflicts("D", "r", a, b)[0]


def test_flags_are_computed_from_structure_not_from_decision_type():
    """`TAKE_A` only means every kept span came from A. Keeping ONE of A's three spans is still
    TAKE_A, and the paper must not read that as 'the final annotation equals A'."""
    # B's single span covers all three of A's, so they form ONE conflict rather than three
    conflict = _conflict([S(0, 10, "X"), S(20, 30, "Y"), S(40, 50, "Z")], [S(0, 50, "W")])
    assert len(conflict.a_spans) == 3
    kept_one = [ResolvedSpan(0, 10, ("X",), "A")]
    decision = decide(conflict, kept_one)
    flags = compare_to_sources(kept_one, conflict.a_spans, conflict.b_spans)
    assert decision.decision_type == "TAKE_A"
    assert flags["final_exactly_equals_A"] is False
    assert flags["final_differs_from_both"] is True


def test_exact_equality_with_a_is_detected():
    conflict = _conflict([S(0, 10, "X")], [S(0, 10, "Y")])
    spans = [ResolvedSpan(0, 10, ("X",), "A")]
    flags = compare_to_sources(spans, conflict.a_spans, conflict.b_spans)
    assert flags["final_exactly_equals_A"] and not flags["final_exactly_equals_B"]
    assert flags["boundary_equals_A"] and flags["boundary_equals_B"]      # same extent
    assert flags["role_inventory_equals_A"] and not flags["role_inventory_equals_B"]
    assert not flags["final_differs_from_both"]


def test_equal_to_both_when_the_annotators_agreed():
    conflict = build_conflicts("D", "r", [S(0, 10, "X")], [S(0, 10, "X")])[0]
    spans = [ResolvedSpan(0, 10, ("X",), "A")]
    flags = compare_to_sources(spans, conflict.a_spans, conflict.b_spans)
    assert flags["final_exactly_equals_both"] and not flags["final_differs_from_both"]


def test_boundary_role_and_structure_vary_independently():
    conflict = _conflict([S(0, 10, "X")], [S(0, 10, "Y")])
    moved = [ResolvedSpan(0, 12, ("X",), "reviewer")]        # A's role, nobody's boundary
    flags = compare_to_sources(moved, conflict.a_spans, conflict.b_spans)
    assert flags["role_inventory_equals_A"] and flags["boundary_differs_from_both"]
    assert flags["final_differs_from_both"]


def test_structure_signature_ignores_offsets_but_not_shape():
    nested_small = [(0, 100), (10, 20)]
    nested_large = [(0, 900), (50, 80)]
    side_by_side = [(0, 40), (50, 90)]
    assert structure_signature(nested_small) == structure_signature(nested_large)
    assert structure_signature(nested_small) != structure_signature(side_by_side)


def test_decision_metadata_carries_the_evaluation_row(corpus):
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")
    conflict = next(c for c in project.conflicts("D1") if not c.agreed)
    side, spans = ("A", conflict.a_spans) if conflict.a_spans else ("B", conflict.b_spans)
    decision = decide(conflict, [ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans])
    row = decision_metadata(conflict, decision)
    for field in ("case_id", "region_id", "conflict_type", "n_A_spans", "n_B_spans",
                  "n_final_spans", "decision_type", "final_exactly_equals_A",
                  "final_differs_from_both", "structure_differs_from_both",
                  "attempt_seconds", "cumulative_seconds", "visit_count", "revision_count"):
        assert field in row, field
    assert "The court considered" not in json.dumps(row)     # no document text in the row
    assert all(not isinstance(v, str) or len(v) < 200 for v in row.values())


def test_canonical_export_includes_the_evaluation_block(corpus, tmp_path):
    from adjudicator.export import canonical_document
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")
    conflicts = project.conflicts("D1")
    decisions = {}
    for c in conflicts:
        side, spans = ("A", c.a_spans) if c.a_spans else ("B", c.b_spans)
        decisions[c.conflict_id] = decide(
            c, [ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans])
    payload = canonical_document(project, "D1", conflicts, decisions)
    assert "evaluation" in payload and len(payload["evaluation"]) == len(decisions)
    assert all("final_differs_from_both" in row for row in payload["evaluation"])


# ------------------------------------------------------- 0.6 timing and revisions
def test_revisions_accumulate_and_time_is_never_lost(corpus):
    from adjudicator.server import AppState
    from adjudicator.store import DecisionStore
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetorical")
    state = AppState(project, DecisionStore(corpus["out"]), None)
    conflict = next(c for c in project.conflicts("D1") if not c.agreed)
    span = (conflict.a_spans or conflict.b_spans)[0]
    side = "A" if conflict.a_spans else "B"
    body = {"doc_id": "D1", "conflict_id": conflict.conflict_id,
            "spans": [{"begin": span.begin, "end": span.end,
                       "labels": [span.label], "origin": side}],
            "seconds": 5.0, "visits_delta": 2}

    first = state.apply_decision(body)["decision"]
    assert first["attempt_seconds"] == 5.0 and first["cumulative_seconds"] == 5.0
    assert first["visit_count"] == 2 and first["revision_count"] == 0

    second = state.apply_decision({**body, "seconds": 3.0, "visits_delta": 1})["decision"]
    assert second["attempt_seconds"] == 3.0
    assert second["cumulative_seconds"] == 8.0, "time from the earlier attempt was lost"
    assert second["visit_count"] == 3, "visits must accumulate, not reset"
    assert second["revision_count"] == 1
