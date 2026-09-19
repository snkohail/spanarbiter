"""P2: project layout, the span table, and the evaluation summary."""
import csv
import io as _io
import json
import os

import pytest

from adjudicator.decisions import ResolvedSpan, auto_agree, decide
from adjudicator.export import export_project, write_span_table
from adjudicator.project import Project, resolve_project_layout
from adjudicator.store import DecisionStore
from adjudicator.summary import corpus_summary
from conftest import TEXT, sha_of, write_doc


def settled_project(tmp_path, defer_one=False):
    root = tmp_path / "project"
    write_doc(root / "annotator_A", "D1",
              [(0, 40, "PREAMBLE"), (60, 120, "FACTS"), (200, 260, "ANALYSIS")],
              text=TEXT, sha=sha_of(TEXT))
    write_doc(root / "annotator_B", "D1",
              [(0, 40, "PREAMBLE"), (60, 120, "ISSUE"), (300, 340, "DECISION")],
              sha=sha_of(TEXT))
    (root / "guidelines.md").write_text("# Rules\n", encoding="utf-8")
    out = root / "adjudication"
    project = Project(str(root / "annotator_A"), str(root / "annotator_B"), str(out),
                      layer="rhetorical")
    store = DecisionStore(str(out))
    conflicts = project.conflicts("D1")
    decisions = {}
    for index, conflict in enumerate(conflicts):
        if conflict.agreed:
            decisions[conflict.conflict_id] = auto_agree(conflict)
            continue
        if defer_one and index == 1:
            decisions[conflict.conflict_id] = decide(conflict, [], intent="DEFER")
            continue
        side, spans = ("A", conflict.a_spans) if conflict.a_spans else ("B", conflict.b_spans)
        decisions[conflict.conflict_id] = decide(
            conflict, [ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans],
            attempt_seconds=4.0 + index, visit_count=1 + index)
    store.save("D1", "rhetorical", decisions, sha_of(TEXT))
    return root, project, store


# ------------------------------------------------------------------ 2.1 project layout
def test_a_project_directory_supplies_all_four_paths(tmp_path):
    root, _, _ = settled_project(tmp_path)
    layout = resolve_project_layout(str(root))
    assert layout["a_dir"].endswith("annotator_A")
    assert layout["b_dir"].endswith("annotator_B")
    assert layout["out"].endswith("adjudication")
    assert layout["guide"].endswith("guidelines.md")


def test_a_directory_without_the_expected_layout_says_what_is_missing(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="annotator_A"):
        resolve_project_layout(str(tmp_path / "empty"))


def test_explicit_paths_still_win_over_the_project_layout(tmp_path):
    root, _, _ = settled_project(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    layout = resolve_project_layout(str(root), out=str(other))
    assert layout["out"] == str(other)


# ------------------------------------------------------------------ 2.4 span table
def test_the_span_table_has_one_row_per_resolved_span(tmp_path):
    root, project, store = settled_project(tmp_path)
    target = tmp_path / "spans.tsv"
    count = write_span_table(project, store, str(target), adjudicator="C")
    rows = list(csv.DictReader(_io.StringIO(target.read_text(encoding="utf-8")), delimiter="\t"))
    assert len(rows) == count > 0
    for field in ("document_id", "layer", "begin", "end", "label", "origin",
                  "decision_type", "conflict_shape", "adjudicator"):
        assert field in rows[0], rows[0]
    assert rows[0]["adjudicator"] == "C"
    assert all(int(r["begin"]) < int(r["end"]) for r in rows)


def test_the_span_table_carries_no_document_text(tmp_path):
    root, project, store = settled_project(tmp_path)
    target = tmp_path / "spans.tsv"
    write_span_table(project, store, str(target))
    body = target.read_text(encoding="utf-8")
    assert "The court considered" not in body, "document text leaked into the analysis table"


def test_a_label_containing_a_tab_or_newline_cannot_break_the_table(tmp_path):
    root = tmp_path / "p"
    write_doc(root / "annotator_A", "D1", [(0, 40, "ODD\tROLE")], text=TEXT, sha=sha_of(TEXT))
    write_doc(root / "annotator_B", "D1", [(0, 40, "OTHER")], sha=sha_of(TEXT))
    out = root / "adjudication"
    project = Project(str(root / "annotator_A"), str(root / "annotator_B"), str(out),
                      layer="rhetorical")
    store = DecisionStore(str(out))
    conflict = project.conflicts("D1")[0]
    store.save("D1", "rhetorical",
               {conflict.conflict_id: decide(
                   conflict, [ResolvedSpan(0, 40, ("ODD\tROLE",), "A")])}, sha_of(TEXT))
    target = tmp_path / "spans.tsv"
    write_span_table(project, store, str(target))
    rows = list(csv.DictReader(_io.StringIO(target.read_text(encoding="utf-8")), delimiter="\t"))
    assert len(rows) == 1 and rows[0]["label"] == "ODD\tROLE"


# ------------------------------------------------------------------ 2.5 evaluation summary
def test_the_summary_reports_what_the_paper_needs(tmp_path):
    root, project, store = settled_project(tmp_path)
    report = corpus_summary(project, store)
    for field in ("documents", "documents_complete", "conflicts", "auto_agreed",
                  "by_conflict_type", "by_decision_type", "took_A", "took_B",
                  "differs_from_both", "new_boundary", "new_structure",
                  "deferred", "dropped", "seconds_median", "seconds_iqr",
                  "seconds_by_conflict_type", "simple_vs_structural", "validation"):
        assert field in report, field
    assert report["documents"] == 1
    assert report["conflicts"] == len([c for c in project.conflicts("D1") if not c.agreed])
    assert sum(report["by_decision_type"].values()) > 0


def test_the_summary_never_emits_document_text(tmp_path):
    root, project, store = settled_project(tmp_path)
    body = json.dumps(corpus_summary(project, store))
    assert "The court considered" not in body
    assert "text" not in json.loads(body)


def test_proportions_are_computed_from_structure_not_decision_type(tmp_path):
    """`took_A` must mean the final annotation equals A, not merely that the spans came from A."""
    root = tmp_path / "p"
    write_doc(root / "annotator_A", "D1", [(0, 20, "FACTS"), (30, 50, "ISSUE")],
              text=TEXT, sha=sha_of(TEXT))
    write_doc(root / "annotator_B", "D1", [(0, 50, "FACTS")], sha=sha_of(TEXT))
    out = root / "adjudication"
    project = Project(str(root / "annotator_A"), str(root / "annotator_B"), str(out),
                      layer="rhetorical")
    store = DecisionStore(str(out))
    conflict = project.conflicts("D1")[0]
    assert len(conflict.a_spans) == 2
    partial = decide(conflict, [ResolvedSpan(0, 20, ("FACTS",), "A")])   # only ONE of A's two
    store.save("D1", "rhetorical", {conflict.conflict_id: partial}, sha_of(TEXT))

    report = corpus_summary(project, store)
    assert report["by_decision_type"].get("TAKE_A") == 1
    assert report["took_A"] == 0, "keeping part of A is not 'the final annotation equals A'"
    assert report["differs_from_both"] == 1


def test_incomplete_documents_are_counted_but_not_called_complete(tmp_path):
    root, project, store = settled_project(tmp_path, defer_one=True)
    report = corpus_summary(project, store)
    assert report["documents"] == 1 and report["documents_complete"] == 0
    assert report["deferred"] == 1


def test_a_deferred_conflict_is_not_counted_as_an_outcome(tmp_path):
    """DEFER means "come back to this", not "the reviewer wrote something new"."""
    _, project, store = settled_project(tmp_path, defer_one=True)
    report = corpus_summary(project, store)
    assert report["deferred"] == 1
    # Every settled decision here took one annotator's spans verbatim, so nothing differs from
    # both. Letting the deferred conflict fall into that bucket would overstate, in the paper,
    # how often the reviewer overrode both annotators.
    assert report["differs_from_both"] == 0
    outcomes = (report["took_A"] + report["took_B"]
                + report["took_both"] + report["differs_from_both"])
    assert report["adjudicated"] == outcomes
    assert sum(report[k] for k in ("percent_took_A", "percent_took_B",
                                   "percent_differs_from_both")) == pytest.approx(100.0)


def test_deferring_lowers_the_adjudicated_count(tmp_path):
    _, settled, store_settled = settled_project(tmp_path / "all")
    _, deferring, store_deferring = settled_project(tmp_path / "one", defer_one=True)
    assert (corpus_summary(deferring, store_deferring)["adjudicated"]
            == corpus_summary(settled, store_settled)["adjudicated"] - 1)
