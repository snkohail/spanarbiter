"""The sample project is what a reader of the paper runs first. These tests pin down what it must
contain: four paired synthetic cases in two scripts whose disagreements are ones two careful
annotators could reasonably reach, every shape, citations nested inside reasoning on both sides,
a span running across a paragraph break, no boundary that differs by a handful of characters,
and nothing for the preflight to warn about.
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from adjudicator.decisions import ResolvedSpan, auto_agree, decide
from adjudicator.export import export_project, validate
from adjudicator.project import Project, resolve_project_layout
from adjudicator.store import DecisionStore
from adjudicator.summary import corpus_summary

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample-project"
SHAPES = {"AGREEMENT", "ROLE_CLASH", "BOUNDARY_SHIFT", "SEGMENTATION", "NESTING_DIFF",
          "A_ONLY", "B_ONLY", "MIXED"}
ROLES = ["ANALYSIS", "ARGUMENT_DEFENDANT", "ARGUMENT_PLAINTIFF", "DECISION", "DECISION_APPEAL",
         "FACTS", "ISSUE", "LAW_REFERENCE", "PREAMBLE"]


def is_arabic(text):
    return any(0x0600 <= ord(ch) <= 0x06FF for ch in text)


def open_project(root, out):
    layout = resolve_project_layout(str(root))
    return Project(layout["a_dir"], layout["b_dir"], str(out), layer="rhetorical",
                   config=layout.get("config"))


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    return open_project(SAMPLE, tmp_path_factory.mktemp("sample_out"))


def sides(project):
    for doc_id in project.doc_ids:
        doc = project.docs[doc_id]
        yield doc_id, "A", doc.a.spans, doc.text
        yield doc_id, "B", doc.b.spans, doc.text


def labels(spans):
    return sorted({s.label for s in spans})


def conflicts_of(project, doc_id, shape, a=None, b=None):
    """The conflicts of one document with this shape and, optionally, these role sets."""
    return [c for c in project.conflicts(doc_id) if c.shape == shape
            and (a is None or labels(c.a_spans) == sorted(a))
            and (b is None or labels(c.b_spans) == sorted(b))]


# ------------------------------------------------------------------------ the sample project

def test_the_sample_loads_with_nothing_blocking_and_nothing_to_warn_about(sample):
    assert sample.usable, sample.preflight()["blocking"]
    assert sample.preflight()["blocking"] == []
    assert sample.preflight()["warnings"] == [], "the sample is about adjudication, not preflight problems"


def test_four_paired_cases_in_two_scripts(sample):
    assert sample.doc_ids == ["AR001", "AR002", "EN001", "EN002"]
    for doc_id in sample.doc_ids:
        assert is_arabic(sample.docs[doc_id].text) == doc_id.startswith("AR"), doc_id


def test_every_shape_appears_and_every_case_has_several_things_to_decide(sample):
    shapes = Counter(c.shape for d in sample.doc_ids for c in sample.conflicts(d))
    assert set(shapes) == SHAPES, sorted(shapes)
    assert shapes["AGREEMENT"] >= 15, "agreements must be common enough to be carried over visibly"
    for doc_id in sample.doc_ids:
        assert sum(c.shape != "AGREEMENT" for c in sample.conflicts(doc_id)) >= 4, doc_id


def test_the_nine_roles_of_a_legal_project_are_all_used(sample):
    assert sorted(sample.labels) == ROLES


def test_citations_are_nested_inside_reasoning_on_both_sides(sample):
    def nests(spans):
        return any(s is not t and s.begin <= t.begin and t.end <= s.end
                   and (s.begin, s.end) != (t.begin, t.end) for s in spans for t in spans)
    assert {side for _, side, spans, _ in sides(sample) if nests(spans)} == {"A", "B"}


def test_a_span_runs_across_a_paragraph_break(sample):
    assert any("\n\n" in text[s.begin:s.end] for _, _, spans, text in sides(sample) for s in spans)


def test_the_designed_dilemmas_are_present(sample):
    # one factual narrative against three events
    [seg] = conflicts_of(sample, "EN001", "SEGMENTATION", a=["FACTS"], b=["FACTS"])
    assert len(seg.a_spans) == 1 and len(seg.b_spans) == 3
    # the citation with or without the rule it states, nested in the reasoning on both sides
    for doc_id in ("EN001", "AR001"):
        [shift] = conflicts_of(sample, doc_id, "BOUNDARY_SHIFT",
                               a=["ANALYSIS", "LAW_REFERENCE"], b=["ANALYSIS", "LAW_REFERENCE"])
        cite_a = next(s for s in shift.a_spans if s.label == "LAW_REFERENCE")
        cite_b = next(s for s in shift.b_spans if s.label == "LAW_REFERENCE")
        assert cite_a.begin == cite_b.begin and cite_b.end > cite_a.end, doc_id
    # the citation nested in the reasoning by A only
    for doc_id in ("EN002", "AR001", "AR002"):
        assert conflicts_of(sample, doc_id, "NESTING_DIFF", a=["ANALYSIS", "LAW_REFERENCE"], b=["ANALYSIS"]), doc_id
    # the judgment below: case history with the order nested as decision, or decision throughout
    nests = [c for c in sample.conflicts("EN002") if c.shape == "NESTING_DIFF"
             and {s.label for s in c.a_spans} == {"FACTS", "DECISION"}]
    [nest] = nests
    [outer], [inner] = ([s for s in nest.a_spans if s.label == "FACTS"],
                        [s for s in nest.a_spans if s.label == "DECISION"])
    [b] = nest.b_spans
    assert b.label == "DECISION" and (b.begin, b.end) == (outer.begin, outer.end)
    assert outer.begin < inner.begin and inner.end <= outer.end
    # a sentence that turns from a party's argument into the court's finding
    for doc_id in ("EN001", "AR002"):
        [mixed] = conflicts_of(sample, doc_id, "MIXED", a=["ARGUMENT_DEFENDANT"], b=["ANALYSIS"])
        [a], [b] = mixed.a_spans, mixed.b_spans
        assert b.begin <= a.begin and a.end < b.end, doc_id
    # the relief sought, read as fact or as argument, on exactly the same text
    for doc_id in ("EN002", "AR001"):
        [clash] = conflicts_of(sample, doc_id, "ROLE_CLASH", a=["FACTS"], b=["ARGUMENT_PLAINTIFF"])
        assert (clash.a_spans[0].begin, clash.a_spans[0].end) == (clash.b_spans[0].begin, clash.b_spans[0].end)
    # the sentence neither annotator labelled quite right
    assert conflicts_of(sample, "EN002", "ROLE_CLASH", a=["ARGUMENT_PLAINTIFF"], b=["ISSUE"])
    # a passing remark or a procedural line that one annotator skipped
    assert conflicts_of(sample, "EN001", "A_ONLY", a=["ANALYSIS"])
    assert conflicts_of(sample, "AR001", "A_ONLY", a=["ANALYSIS"])
    assert conflicts_of(sample, "EN002", "B_ONLY", b=["PREAMBLE"])
    assert conflicts_of(sample, "AR002", "B_ONLY", b=["ANALYSIS"])


def test_no_disagreement_is_a_matter_of_a_few_characters(sample):
    """A boundary that differs by a punctuation mark would be a manufactured disagreement.
    Wherever the covered text differs, it differs by at least a clause."""
    for doc_id in sample.doc_ids:
        for conflict in sample.conflicts(doc_id):
            if conflict.shape not in ("BOUNDARY_SHIFT", "MIXED"):
                continue
            # per role, because a nested citation sits inside an identical reasoning span
            widest = 0
            for role in {s.label for s in conflict.a_spans + conflict.b_spans}:
                covered_a = {i for s in conflict.a_spans if s.label == role for i in range(s.begin, s.end)}
                covered_b = {i for s in conflict.b_spans if s.label == role for i in range(s.begin, s.end)}
                widest = max(widest, len(covered_a ^ covered_b))
            assert widest >= 40, (doc_id, conflict.shape, conflict.begin)


def test_the_sample_carries_its_own_guidelines():
    layout = resolve_project_layout(str(SAMPLE))
    assert layout["guide"] and layout["guide"].endswith("guidelines.md")
    text = (SAMPLE / "guidelines.md").read_text(encoding="utf-8")
    for role in ROLES:
        assert f"`{role}`" in text, role


def test_the_whole_sample_can_be_adjudicated_and_exported_clean(tmp_path):
    """Every conflict is settled by taking one annotator's reading (A where A wrote something,
    B otherwise), every document exports as complete, and every export validates against the
    text and the role inventory: nothing in the sample needs a reviewer-authored span to reach a
    clean archive, and the corpus summary agrees with the conflicts it was computed from."""
    project = open_project(SAMPLE, tmp_path / "out")
    store = DecisionStore(str(tmp_path / "out"))
    expected_conflicts = 0
    for doc_id in project.doc_ids:
        decisions = {}
        for conflict in project.conflicts(doc_id):
            if conflict.agreed:
                decisions[conflict.conflict_id] = auto_agree(conflict)
                continue
            expected_conflicts += 1
            side, spans = ("A", conflict.a_spans) if conflict.a_spans else ("B", conflict.b_spans)
            decisions[conflict.conflict_id] = decide(conflict, [
                ResolvedSpan(s.begin, s.end, (s.label,), side) for s in spans])
        store.save(doc_id, project.layer, decisions)
    result = export_project(project, store, out_dir=str(tmp_path / "export"), roundtrip=True)
    assert sorted(result.written) == project.doc_ids and not result.skipped
    for doc_id in project.doc_ids:
        payload = json.loads((tmp_path / "export" / "canonical" / f"{doc_id}.json")
                             .read_text(encoding="utf-8"))
        assert payload["adjudication"]["complete"] is True, doc_id
        assert validate(payload["spans"], payload["text_length"], project.labels) == [], doc_id
    report = corpus_summary(project, store)
    assert report["documents"] == 4
    assert report["conflicts"] == expected_conflicts
    assert report["stale"] == 0 and report["deferred"] == 0


# ------------------------------------------------------------------------------------ the files

def test_checksums_and_offsets_are_consistent():
    """The tool refuses to export against a text whose fingerprint does not match, so an example
    that got this wrong would look like a bug in the tool."""
    for path in sorted((SAMPLE / "annotator_A").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["text_len"] == len(payload["text"])
        assert hashlib.sha256(payload["text"].encode("utf-8")).hexdigest() == payload["text_sha256"]
        for span in payload["spans"]:
            assert 0 <= span["begin"] < span["end"] <= len(payload["text"]), (path.name, span)


def test_annotator_b_is_blind():
    """B carries a checksum and spans only, which is how the annotation was produced."""
    for path in sorted((SAMPLE / "annotator_B").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "text" not in payload and payload["text_sha256"]


def test_every_case_is_synthetic_and_says_what_it_shows():
    """Every file says what it demonstrates; a real corpus file dropped in here would not."""
    for path in sorted(SAMPLE.glob("annotator_*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert re.fullmatch(r"(AR|EN)\d{3}", payload["canonical_case_id"]), path.name
        assert payload["demonstrates"], path.name
