"""Integrity of the decision log across every path that reads it.

Each test goes through the path a user takes (the server's AppState, the export and summary
functions, a real socket) rather than only the lowest-level function, because a guarantee that
holds in one function and not in the path that calls it is no guarantee.
"""
import io
import json
import os
import contextlib
import threading
import urllib.error
import urllib.request

import pytest

from adjudicator.adapters import brat, conll, detect_format, load_any
from adjudicator.conflicts import build_conflicts
from adjudicator.decisions import (
    DecisionError, ResolvedSpan, decide, effective_decisions, revalidate_decisions,
)
from adjudicator.evaluation import compare_to_sources
from adjudicator.export import canonical_document, export_project
from adjudicator.progress import document_progress
from adjudicator.project import Project, resolve_project_layout, sha256_of
from adjudicator.server import AppState, make_server
from adjudicator.store import DecisionStore, StaleTextError, StoreError
from adjudicator.summary import corpus_summary
from conftest import S, TEXT, sha_of, write_doc


# ------------------------------------------------------------------ helpers
def json_project(root, text, a_spans, b_spans, doc="D1"):
    """A two-annotator JSON project where both sides carry the text."""
    for side, spans in (("annotator_A", a_spans), ("annotator_B", b_spans)):
        (root / side).mkdir(parents=True, exist_ok=True)
        record = {"doc_id": doc, "text": text,
                  "spans": [{"begin": b, "end": e, "label": l, "kind": "rhetorical"}
                            for b, e, l in spans]}
        (root / side / f"{doc}.json").write_text(json.dumps(record, ensure_ascii=False),
                                                 encoding="utf-8")
    return root


def opened(root, layer="rhetorical"):
    project = Project(str(root / "annotator_A"), str(root / "annotator_B"), str(root / "out"),
                      layer=layer)
    assert project.usable, project.preflight()["blocking"]
    store = DecisionStore(str(root / "out"))
    return project, store, AppState(project, store, None)


def take_a(state, doc_id, conflict, **extra):
    span = conflict.a_spans[0]
    return state.apply_decision({
        "doc_id": doc_id, "conflict_id": conflict.conflict_id,
        "spans": [{"begin": span.begin, "end": span.end, "labels": [span.label], "origin": "A"}],
        **extra})


# ================================================================== 1. stale text
def test_opening_a_document_cannot_relabel_a_log_as_belonging_to_new_text(tmp_path):
    """The log stores the hash of the text its offsets were measured against. A page load
    used to read the log WITHOUT checking that hash and write it back stamped with the current
    one, after which the stale-text protection could never fire again."""
    text_v1 = "The court held that the claim fails. The appeal is dismissed."
    root = json_project(tmp_path / "p", text_v1,
                        [(0, 36, "FACTS"), (37, 61, "OUTCOME")],
                        [(0, 36, "FACTS"), (37, 61, "ISSUE")])
    project, store, state = opened(root)
    clash = next(c for c in project.conflicts("D1") if not c.agreed)
    take_a(state, "D1", clash)
    path = store.path_for("D1", "rhetorical")
    assert json.load(open(path, encoding="utf-8"))["text_sha256"] == sha256_of(text_v1)

    # the annotators' text changes; every existing offset still lies inside it
    text_v2 = text_v1 + " Costs follow the event."
    json_project(tmp_path / "p", text_v2,
                 [(0, 36, "FACTS"), (37, 61, "OUTCOME")], [(0, 36, "FACTS"), (37, 61, "ISSUE")])
    project2, store2, state2 = opened(root)

    with pytest.raises(StaleTextError):
        state2.document_state("D1")                       # the page load is refused ...
    assert json.load(open(path, encoding="utf-8"))["text_sha256"] == sha256_of(text_v1), \
        "a refused page load must leave the log's text hash untouched"
    with pytest.raises(StaleTextError):                    # ... and so is a save
        take_a(state2, "D1", next(c for c in project2.conflicts("D1") if not c.agreed))
    result = export_project(project2, store2, out_dir=str(tmp_path / "e"))
    assert result.written == [] and "different document text" in result.skipped[0][1]
    # the document list says why instead of showing stale progress
    rows = state2.corpus_progress()
    assert rows[0]["error"] and "different document text" in rows[0]["error"]


def test_store_update_refuses_a_log_written_against_other_text(store):
    conflict = build_conflicts("D1", "r", [S(0, 10, "X")], [S(0, 10, "Y")])[0]
    decision = decide(conflict, [ResolvedSpan(0, 10, ("X",), "A")])
    store.save("D1", "r", {conflict.conflict_id: decision}, text_sha256="a" * 64)
    with pytest.raises(StaleTextError):
        store.update("D1", "r", lambda current: None, text_sha256="b" * 64)
    assert json.load(open(store.path_for("D1", "r"), encoding="utf-8"))["text_sha256"] == "a" * 64


# ================================================================== 2. file names
def test_different_document_ids_never_share_a_decision_file(store):
    ids = ["case/1", "case?1", "case:1", "case*1", "case_1", "Case_1", "a__b"]
    paths = {store.path_for(doc_id, "default") for doc_id in ids}
    assert len(paths) == len(ids), "two document ids mapped to one file"
    # doc "a__b" in layer "c" and doc "a" in layer "b__c" used to collide too
    assert store.path_for("a__b", "c") != store.path_for("a", "b__c")
    # the readable part is still there for a person browsing the directory
    assert os.path.basename(store.path_for("EN001", "rhetorical")).startswith("EN001__rhetorical")


def test_a_log_written_under_the_old_file_name_is_still_found(store):
    conflict = build_conflicts("D1", "rhetorical", [S(0, 10, "X")], [S(0, 10, "Y")])[0]
    decision = decide(conflict, [ResolvedSpan(0, 10, ("X",), "A")])
    store.save("D1", "rhetorical", {conflict.conflict_id: decision})
    new_path = store.path_for("D1", "rhetorical")
    legacy = os.path.join(store.dir, "D1__rhetorical.json")
    os.replace(new_path, legacy)                 # as an earlier build would have written it
    assert store.load("D1", "rhetorical")        # found, and ...
    assert os.path.exists(new_path) and not os.path.exists(legacy), "... moved to its new name"
    # a legacy file that belongs to a DIFFERENT document is never adopted
    os.replace(new_path, os.path.join(store.dir, "D1_2__rhetorical.json"))
    assert store.load("D1/2", "rhetorical") == {}
    assert store.load("D1:2", "rhetorical") == {}


# ================================================================== 3. agreements are derived
def test_export_and_progress_do_not_depend_on_the_document_having_been_opened(tmp_path):
    """Automatic agreements used to be written to the log only when the interface opened the
    document, so a document nobody had browsed exported as incomplete with zero spans."""
    text = "One two three four five six seven eight nine ten."
    root = json_project(tmp_path / "p", text, [(0, 7, "FACTS"), (8, 13, "ISSUE")],
                        [(0, 7, "FACTS"), (8, 13, "ISSUE")])
    project, store, _ = opened(root)
    assert not os.path.exists(store.path_for("D1", "rhetorical")), "nothing was ever written"
    conflicts = project.conflicts("D1")
    progress = document_progress(conflicts, store.load("D1", "rhetorical"))
    payload = canonical_document(project, "D1", conflicts, store.load("D1", "rhetorical"))
    assert progress["complete"] and payload["adjudication"]["complete"]
    assert len(payload["spans"]) == 2
    result = export_project(project, store, out_dir=str(tmp_path / "e"))
    assert result.written == ["D1"] and result.span_count == 2
    report = corpus_summary(project, store)
    assert report["documents_complete"] == 1 and report["auto_agreed"] == 2
    assert not os.path.exists(store.path_for("D1", "rhetorical")), "reading must not write"


def test_a_derived_agreement_is_identical_every_time(tmp_path):
    root = json_project(tmp_path / "p", "Alpha beta gamma.", [(0, 5, "X")], [(0, 5, "X")])
    project, store, _ = opened(root)
    conflicts = project.conflicts("D1")
    one = canonical_document(project, "D1", conflicts, {})
    two = canonical_document(project, "D1", conflicts, {})
    assert one == two


# ================================================================== 4. stale decisions are void
def test_a_stale_decision_contributes_to_no_statistic(tmp_path):
    text = "Alpha beta gamma delta epsilon zeta."
    root = json_project(tmp_path / "p", text, [(0, 10, "FACTS")], [(0, 10, "ISSUE")])
    project, store, state = opened(root)
    clash = project.conflicts("D1")[0]
    take_a(state, "D1", clash, seconds=7.0, visits_delta=2)
    before = corpus_summary(project, store)
    assert before["adjudicated"] == 1 and before["took_A"] == 1

    stored = store.load("D1", "rhetorical")
    stored[clash.conflict_id].fingerprint = "0000000000000000"    # the source changed
    store.save("D1", "rhetorical", stored, sha256_of(text))

    after = corpus_summary(project, store)
    assert after["stale"] == 1
    assert after["adjudicated"] == 0 and after["took_A"] == 0
    assert "TAKE_A" not in after["by_decision_type"]
    assert after["seconds_median"] is None and after["visits_median"] is None
    assert after["revisions_total"] == 0 and after["documents_complete"] == 0
    progress = document_progress(project.conflicts("D1"), store.load("D1", "rhetorical"))
    assert progress["undecided"] == 1 and progress["settled"] == 0 and progress["stale"] == 1


def test_a_stale_conflict_is_queued_again_and_a_fresh_decision_replaces_it(tmp_path):
    text = "Alpha beta gamma delta epsilon zeta."
    root = json_project(tmp_path / "p", text, [(0, 10, "FACTS")], [(0, 10, "ISSUE")])
    project, store, state = opened(root)
    clash = project.conflicts("D1")[0]
    take_a(state, "D1", clash, seconds=30.0)
    stored = store.load("D1", "rhetorical")
    stored[clash.conflict_id].fingerprint = "0000000000000000"
    store.save("D1", "rhetorical", stored, sha256_of(text))

    doc = state.document_state("D1")
    assert clash.conflict_id in doc["stale"]
    assert clash.conflict_id not in doc["decisions"], "a void decision must not look decided"
    fresh = take_a(state, "D1", clash, seconds=4.0)["decision"]
    assert fresh["revision_count"] == 0 and fresh["cumulative_seconds"] == 4.0, \
        "time and revisions from a void decision must not be inherited"
    assert state.document_state("D1")["stale"] == []


def test_a_stale_or_orphaned_automatic_agreement_does_not_block_completion(tmp_path):
    text = "One two three four five six seven eight nine ten eleven twelve."
    root = json_project(tmp_path / "p", text, [(0, 7, "FACTS"), (30, 40, "ISSUE")],
                        [(0, 7, "FACTS"), (30, 40, "ISSUE")])
    project, store, state = opened(root)
    # a log holding automatic agreements, as earlier builds wrote on every page load
    conflicts = project.conflicts("D1")
    derived, _ = effective_decisions(conflicts, {})
    store.save("D1", "rhetorical", derived, sha256_of(text))

    # both annotators move the second agreed span identically -> still agreed, new fingerprint
    json_project(tmp_path / "p", text, [(0, 7, "FACTS"), (30, 44, "ISSUE")],
                 [(0, 7, "FACTS"), (30, 44, "ISSUE")])
    project, store, state = opened(root)
    doc = state.document_state("D1")
    assert doc["progress"]["complete"] and doc["progress"]["stale"] == 1
    assert export_project(project, store, out_dir=str(tmp_path / "e1")).written == ["D1"]

    # both annotators delete the second span -> the stored decision is orphaned
    json_project(tmp_path / "p", text, [(0, 7, "FACTS")], [(0, 7, "FACTS")])
    project, store, state = opened(root)
    doc = state.document_state("D1")
    assert doc["progress"]["complete"] and doc["progress"]["stale"] == 1
    assert export_project(project, store, out_dir=str(tmp_path / "e2")).written == ["D1"]


# ================================================================== hand-edited logs
def _rewrite_log(store, doc_id, layer, edit):
    path = store.path_for(doc_id, layer)
    payload = json.load(open(path, encoding="utf-8"))
    edit(payload["decisions"])
    json.dump(payload, open(path, "w", encoding="utf-8"))


def test_a_log_edited_by_hand_is_held_to_the_rules_it_was_written_under(tmp_path):
    text = "Alpha beta gamma delta epsilon zeta."
    root = json_project(tmp_path / "p", text, [(0, 10, "FACTS")], [(0, 10, "ISSUE")])
    project, store, state = opened(root)
    clash = project.conflicts("D1")[0]
    take_a(state, "D1", clash)
    assert export_project(project, store, out_dir=str(tmp_path / "e0")).written == ["D1"]

    # the span now claims A wrote ISSUE there; the fingerprint is untouched, so it is not stale
    _rewrite_log(store, "D1", "rhetorical",
                 lambda ds: ds[0]["spans"][0].__setitem__("labels", ["ISSUE"]))
    stored = store.load("D1", "rhetorical")
    problems = revalidate_decisions(project.conflicts("D1"), stored, project.labels, len(text))
    assert problems and "claims to come from" in problems[0]
    result = export_project(project, store, out_dir=str(tmp_path / "e1"))
    assert result.written == [] and "claims to come from" in result.skipped[0][1]
    report = corpus_summary(project, store)
    assert report["validation"]["error_count"] == 1 and report["adjudicated"] == 0

    # A's role and B's role stacked on one extent, written straight into the file
    take_a(state, "D1", clash)
    _rewrite_log(store, "D1", "rhetorical", lambda ds: ds[0]["spans"].append(
        {"begin": 0, "end": 10, "labels": ["ISSUE"], "origin": "B"}))
    problems = revalidate_decisions(project.conflicts("D1"), store.load("D1", "rhetorical"),
                                    project.labels, len(text))
    assert any("annotator A" in p and "annotator B" in p for p in problems), problems

    # a type that does not match the spans' provenance, and negative timing
    take_a(state, "D1", clash)
    _rewrite_log(store, "D1", "rhetorical", lambda ds: ds[0].update(
        decision_type="TAKE_B", attempt_seconds=-4))
    problems = revalidate_decisions(project.conflicts("D1"), store.load("D1", "rhetorical"),
                                    project.labels, len(text))
    assert any("recorded as TAKE_B" in p for p in problems), problems
    assert any("negative" in p for p in problems), problems


# ================================================================== 6. role metrics
def test_swapped_roles_are_not_reported_as_role_equality():
    a = [S(0, 5, "X"), S(5, 10, "Y")]
    swapped = [ResolvedSpan(0, 5, ("Y",), "reviewer"), ResolvedSpan(5, 10, ("X",), "reviewer")]
    flags = compare_to_sources(swapped, a, [])
    assert flags["final_exactly_equals_A"] is False
    assert flags["boundary_equals_A"] is True
    assert flags["role_inventory_equals_A"] is True, "same roles, so the INVENTORY is equal"
    assert flags["role_reassigned_vs_A"] is True, "... but they sit on different extents"
    assert "role_equals_A" not in flags, "the old name overstated what was measured"


def test_the_summary_counts_reassigned_roles(tmp_path):
    # A: X on [0,10) and Y on [5,15); B: X on [0,10) and Z on [5,15). One overlapping component.
    text = "Alpha beta gamma delta epsilon zeta eta theta."
    root = json_project(tmp_path / "p", text, [(0, 10, "X"), (5, 15, "Y")],
                        [(0, 10, "X"), (5, 15, "Z")])
    project, store, state = opened(root)
    conflict = project.conflicts("D1")[0]
    assert len(project.conflicts("D1")) == 1 and conflict.shape == "ROLE_CLASH"
    state.apply_decision({
        "doc_id": "D1", "conflict_id": conflict.conflict_id,
        "spans": [{"begin": 0, "end": 10, "labels": ["X"], "origin": "A"},
                  {"begin": 5, "end": 15, "labels": ["Y"], "origin": "A"}]})
    assert corpus_summary(project, store)["role_reassigned"] == 0
    # A's extents and A's roles, swapped across the two extents
    state.apply_decision({
        "doc_id": "D1", "conflict_id": conflict.conflict_id,
        "spans": [{"begin": 0, "end": 10, "labels": ["Y"], "origin": "reviewer"},
                  {"begin": 5, "end": 15, "labels": ["X"], "origin": "reviewer"}]})
    report = corpus_summary(project, store)
    assert report["role_reassigned"] == 1
    assert report["new_role_inventory"] == 0, "no role was introduced, only moved"
    assert report["differs_from_both"] == 1


# ================================================================== 5/7. API validation
@pytest.fixture
def live(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 40, "PREAMBLE"), (60, 120, "FACTS")], text=TEXT, sha=sha_of(TEXT))
    write_doc(b, "D1", [(0, 40, "PREAMBLE"), (60, 120, "ISSUE")], sha=sha_of(TEXT))
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    store = DecisionStore(str(tmp_path / "out"))
    state = AppState(project, store, None)
    httpd = make_server(state, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    clash = next(c for c in project.conflicts("D1") if not c.agreed)
    agreed = next(c for c in project.conflicts("D1") if c.agreed)
    yield {"base": base, "clash": clash, "agreed": agreed, "store": store}
    httpd.shutdown()
    httpd.server_close()


def post(base, path, payload, raw=None):
    request = urllib.request.Request(
        base + path, data=raw if raw is not None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


BAD_SPANS = [
    ("inverted", {"begin": 80, "end": 60, "labels": ["FACTS"], "origin": "reviewer"}, "inverted"),
    ("negative", {"begin": -1, "end": 60, "labels": ["FACTS"], "origin": "reviewer"}, "negative"),
    ("boolean offset", {"begin": True, "end": 60, "labels": ["FACTS"], "origin": "reviewer"},
     "whole numbers"),
    ("fractional offset", {"begin": 60.5, "end": 120, "labels": ["FACTS"], "origin": "reviewer"},
     "whole numbers"),
    ("empty role", {"begin": 60, "end": 120, "labels": [""], "origin": "reviewer"}, "role"),
    ("non-text role", {"begin": 60, "end": 120, "labels": [5], "origin": "reviewer"}, "role"),
    ("role given twice", {"begin": 60, "end": 120, "labels": ["FACTS", "FACTS"],
                          "origin": "reviewer"}, "repeats"),
]


@pytest.mark.parametrize("name,span,expected", BAD_SPANS, ids=[b[0] for b in BAD_SPANS])
def test_a_malformed_span_is_refused_with_a_reason_not_an_internal_error(live, name, span, expected):
    with contextlib.redirect_stderr(io.StringIO()):
        code, body = post(live["base"], "/api/decide", {
            "doc_id": "D1", "conflict_id": live["clash"].conflict_id, "spans": [span]})
    assert code == 400, (name, code, body)
    assert expected in body["error"], (name, body["error"])


BAD_REQUESTS = [
    ("missing doc_id", "/api/decide", {"conflict_id": "x", "spans": []}, "document"),
    ("unknown doc_id", "/api/decide", {"doc_id": "NOPE", "conflict_id": "x", "intent": "DROP"},
     "document"),
    ("unknown conflict", "/api/decide", {"doc_id": "D1", "conflict_id": "D1::rhetorical::9999",
                                          "intent": "DROP"}, "conflict"),
    ("unknown intent", "/api/decide", {"doc_id": "D1", "conflict_id": "SET", "intent": "MERGE"},
     "intent"),
    ("negative seconds", "/api/decide", {"doc_id": "D1", "conflict_id": "SET", "intent": "DROP",
                                          "seconds": -5}, "seconds"),
    ("negative visits", "/api/decide", {"doc_id": "D1", "conflict_id": "SET", "intent": "DROP",
                                         "visits_delta": -1}, "visits"),
    ("note not text", "/api/decide", {"doc_id": "D1", "conflict_id": "SET", "intent": "DROP",
                                       "note": ["x"]}, "note"),
    ("unknown layer", "/api/layer", {"layer": "nope"}, "layer"),
    ("layer missing", "/api/layer", {}, "layer"),
]


@pytest.mark.parametrize("name,path,payload,expected", BAD_REQUESTS,
                         ids=[b[0] for b in BAD_REQUESTS])
def test_a_malformed_request_is_a_400_with_a_reason(live, name, path, payload, expected):
    if payload.get("conflict_id") == "SET":
        payload = {**payload, "conflict_id": live["clash"].conflict_id}
    with contextlib.redirect_stderr(io.StringIO()):
        code, body = post(live["base"], path, payload)
    assert code == 400, (name, code, body)
    assert expected in body["error"].lower(), (name, body["error"])


def test_a_body_that_is_not_json_is_a_400(live):
    with contextlib.redirect_stderr(io.StringIO()):
        code, body = post(live["base"], "/api/decide", None, raw=b"{not json")
        code2, body2 = post(live["base"], "/api/decide", None, raw=b"[1, 2]")
    assert code == 400 and "JSON" in body["error"]
    assert code2 == 400 and "object" in body2["error"]


def test_nan_timing_is_refused(live):
    # Python's json module accepts NaN, so it has to be checked explicitly
    with contextlib.redirect_stderr(io.StringIO()):
        code, body = post(live["base"], "/api/decide", None, raw=(
            '{"doc_id": "D1", "conflict_id": "%s", "intent": "DROP", "seconds": NaN}'
            % live["clash"].conflict_id).encode())
    assert code == 400 and "seconds" in body["error"]


def test_an_agreement_cannot_be_adjudicated_through_the_api(live):
    agreed = live["agreed"]
    span = agreed.a_spans[0]
    code, body = post(live["base"], "/api/decide", {
        "doc_id": "D1", "conflict_id": agreed.conflict_id,
        "spans": [{"begin": span.begin, "end": span.end, "labels": ["ISSUE"],
                   "origin": "reviewer"}]})
    assert code == 400 and "agree" in body["error"]


def test_the_same_span_twice_in_one_resolution_is_refused_when_saved():
    conflict = build_conflicts("D", "r", [S(0, 10, "FACTS")], [S(0, 10, "ISSUE")])[0]
    span = ResolvedSpan(0, 10, ("FACTS",), "A")
    with pytest.raises(DecisionError, match="twice"):
        decide(conflict, [span, span])


def test_static_files_stay_inside_the_web_root(live):
    for path in ("/../pyproject.toml", "/css/../../pyproject.toml", "/%2e%2e/pyproject.toml",
                 "/js/../../src/adjudicator/cli.py"):
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(live["base"] + path)
        assert caught.value.code == 404, path
    with urllib.request.urlopen(live["base"] + "/js/main.js") as response:
        assert response.status == 200


# ================================================================== 8. formats
def test_round_trip_export_on_a_brat_corpus_does_not_crash(tmp_path):
    for side in ("annotator_A", "annotator_B"):
        (tmp_path / side).mkdir()
        (tmp_path / side / "doc1.txt").write_text("The court dismissed the claim.", encoding="utf-8")
        (tmp_path / side / "doc1.ann").write_text("T1\tCOURT 4 9\tcourt\n", encoding="utf-8")
    project = Project(str(tmp_path / "annotator_A"), str(tmp_path / "annotator_B"),
                      str(tmp_path / "out"))
    assert project.usable
    result = export_project(project, DecisionStore(str(tmp_path / "out")),
                            out_dir=str(tmp_path / "e"), roundtrip=True)
    assert result.written == ["doc1"]
    assert (tmp_path / "e" / "canonical" / "doc1.json").exists()
    assert not (tmp_path / "e" / "roundtrip").exists()
    assert result.notes and "brat" in result.notes[0]


def test_a_brat_annotation_across_a_line_break_is_accepted():
    """brat writes the covered text with line breaks as spaces."""
    text = "The court\ndismissed it."
    spans, issues = brat._parse_ann(text, "T1\tX 4 19\tcourt dismissed\n")
    assert not issues, issues
    assert [(s.begin, s.end) for s in spans] == [(4, 19)]
    # a real disagreement is still caught
    spans, issues = brat._parse_ann(text, "T1\tX 4 19\tcourt dismisses\n")
    assert issues and not spans


def test_conll_u_is_not_read_as_a_two_column_file(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "D1.conllu").write_text(
        "1\tThe\tthe\tDET\t_\t_\t2\tdet\t_\t_\n2\tcourt\tcourt\tNOUN\t_\t_\t0\troot\t_\t_\n",
        encoding="utf-8")
    with pytest.raises(Exception) as caught:
        detect_format(str(tmp_path / "a"))
    assert "looked for" in str(caught.value)
    assert ".conllu" not in conll.EXTENSIONS


def test_a_stray_inside_tag_is_read_as_a_span_start_and_reported(tmp_path):
    (tmp_path / "annotator_A").mkdir()
    (tmp_path / "annotator_B").mkdir()
    (tmp_path / "annotator_A" / "D1.conll").write_text("Alpha\tI-X\nBeta\tI-X\n", encoding="utf-8")
    (tmp_path / "annotator_B" / "D1.conll").write_text("Alpha\tB-X\nBeta\tI-X\n", encoding="utf-8")
    docs, problems = load_any(str(tmp_path / "annotator_A"), {})
    assert not problems and len(docs["D1"].spans) == 1
    assert docs["D1"].notes and "I-X" in docs["D1"].notes[0]
    project = Project(str(tmp_path / "annotator_A"), str(tmp_path / "annotator_B"),
                      str(tmp_path / "out"))
    assert project.usable
    assert any(w.kind == "input_repaired" and "I-X" in w.message for w in project.warnings)


# ================================================================== config.json
def test_a_config_file_beside_the_annotators_is_read(tmp_path):
    root = tmp_path / "project"
    for side in ("annotator_A", "annotator_B"):
        (root / side).mkdir(parents=True)
        (root / side / "d.json").write_text(json.dumps(
            {"id": "d", "text": TEXT, "spans": [{"from_char": 0, "to_char": 5, "kind_of": "X"}]}),
            encoding="utf-8")
    (root / "config.json").write_text(json.dumps(
        {"field_mapping": {"begin": "from_char", "end": "to_char", "label": "kind_of"}}),
        encoding="utf-8")
    layout = resolve_project_layout(str(root))
    assert layout["config"]["field_mapping"]["begin"] == "from_char"
    project = Project(layout["a_dir"], layout["b_dir"], layout["out"], config=layout["config"])
    assert project.usable, project.preflight()["blocking"]
    assert project.mapping_a["begin"] == "from_char"
    (root / "config.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="config.json"):
        resolve_project_layout(str(root))
