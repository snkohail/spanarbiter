"""Opening a project: everything that could make character offsets untrustworthy must block."""
import pytest

from adjudicator.project import Project
from conftest import TEXT, sha_of, write_doc


def kinds(project):
    return {issue.kind for issue in project.blocking}


def test_a_clean_project_opens(project):
    assert project.usable and project.layer == "rhetorical"
    assert project.doc_ids == ["D1"]
    assert project.docs["D1"].text_source == "annotator A"


def test_roles_are_detected_from_the_data(project):
    assert project.labels == ["ANALYSIS", "DECISION", "FACTS", "ISSUE", "PREAMBLE"]
    assert project.label_counts["A"]["ANALYSIS"] == 1
    assert any("only by annotator A" in w.message for w in project.warnings)


def test_a_checksum_that_disagrees_with_the_text_blocks(tmp_path):
    """B annotated a different revision of the document. Its offsets mean something else."""
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 10, "FACTS")], text=TEXT, sha=sha_of(TEXT))
    write_doc(b, "D1", [(0, 10, "ISSUE")], sha="de" * 32)
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    assert not project.usable and "checksum" in kinds(project)


def test_differing_document_text_blocks(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 10, "FACTS")], text=TEXT)
    write_doc(b, "D1", [(0, 10, "ISSUE")], text=TEXT + " and more")
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    assert not project.usable and "text_mismatch" in kinds(project)


def test_no_text_anywhere_blocks(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 10, "FACTS")])
    write_doc(b, "D1", [(0, 10, "ISSUE")])
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    assert not project.usable and "no_text" in kinds(project)


def test_an_offset_past_the_end_of_the_document_blocks(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, len(TEXT) + 50, "FACTS")], text=TEXT)
    write_doc(b, "D1", [(0, 10, "ISSUE")])
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    assert not project.usable and "offset" in kinds(project)


def test_a_layer_that_does_not_exist_blocks_instead_of_finding_nothing(corpus):
    """A mistyped layer selects no spans, which would make every document vacuously complete
    and export an empty corpus that looks finished."""
    project = Project(corpus["a"], corpus["b"], corpus["out"], layer="rhetoricol")
    assert not project.usable
    assert "unknown_layer" in kinds(project)
    assert "rhetorical" in project.blocking[0].message


def test_no_annotations_at_all_blocks(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [], text=TEXT)
    write_doc(b, "D1", [])
    project = Project(str(a), str(b), str(tmp_path / "out"))
    assert not project.usable


def test_documents_missing_from_one_side_are_warned_not_blocked(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 10, "FACTS")], text=TEXT)
    write_doc(a, "D2", [(0, 10, "FACTS")], text=TEXT)
    write_doc(b, "D1", [(0, 10, "ISSUE")])
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    assert project.usable and project.only_in_a == ["D2"]
    assert any("only in annotator A" in w.message for w in project.warnings)


def test_several_layers_pick_one_and_say_so(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 10, "FACTS")], text=TEXT, layer="rhetorical")
    write_doc(b, "D1", [(0, 10, "COURT")], layer="entity")
    project = Project(str(a), str(b), str(tmp_path / "out"))
    assert project.usable
    assert any("several layers" in w.message for w in project.warnings)


def test_switching_layer_re_detects_the_roles(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 10, "FACTS")], text=TEXT, layer="rhetorical")
    write_doc(b, "D1", [(20, 30, "COURT")], layer="entity")
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    assert project.labels == ["FACTS"]
    project.set_layer("entity")
    assert project.labels == ["COURT"]
    assert all(c.layer == "entity" for c in project.conflicts("D1"))
