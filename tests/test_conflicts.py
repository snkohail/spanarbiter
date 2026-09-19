"""Grouping and classification: does the reviewer get told what kind of disagreement this is?"""
import pytest

from adjudicator.conflicts import build_conflicts, classify
from conftest import S


def test_non_overlapping_disagreements_are_separate_decisions():
    conflicts = build_conflicts("D", "r", [S(0, 10, "X"), S(90, 99, "Y")], [S(5, 12, "X")])
    assert len(conflicts) == 2
    assert [c.shape for c in conflicts] == ["BOUNDARY_SHIFT", "A_ONLY"]


def test_a_long_span_pulls_everything_it_covers_into_one_decision():
    """Anything the long span overlaps must be decided together: accepting it would otherwise
    silently contradict a decision made separately about the spans inside it."""
    conflicts = build_conflicts("D", "r",
                                [S(0, 20, "X"), S(30, 50, "Y"), S(60, 80, "Z")],
                                [S(10, 70, "W")])
    assert len(conflicts) == 1
    assert conflicts[0].extent == (0, 80)


@pytest.mark.parametrize("a,b,expected", [
    ([S(0, 10, "X")], [S(0, 10, "X")], "AGREEMENT"),
    ([S(0, 10, "X")], [], "A_ONLY"),
    ([], [S(0, 10, "X")], "B_ONLY"),
    ([S(0, 10, "X")], [S(0, 10, "Y")], "ROLE_CLASH"),
    ([S(0, 10, "X")], [S(3, 10, "X")], "BOUNDARY_SHIFT"),
    ([S(0, 99, "X")], [S(0, 30, "X"), S(31, 60, "Y"), S(61, 99, "X")], "SEGMENTATION"),
    ([S(0, 99, "X"), S(10, 20, "Y")], [S(0, 99, "X")], "NESTING_DIFF"),
    ([S(0, 10, "X"), S(20, 30, "Y")], [S(5, 15, "Z"), S(22, 33, "W")], "MIXED"),
])
def test_every_shape_is_recognised(a, b, expected):
    assert classify(a, b) == expected


def test_nesting_is_not_mistaken_for_segmentation():
    """Both are 'one span against several'. Spans laid end to end are a disagreement about where
    to cut; spans inside one another are a disagreement about internal structure."""
    nested = classify([S(0, 99, "X"), S(10, 20, "Y")], [S(0, 99, "X")])
    tiled = classify([S(0, 99, "X")], [S(0, 30, "X"), S(31, 60, "Y"), S(61, 99, "X")])
    assert (nested, tiled) == ("NESTING_DIFF", "SEGMENTATION")


def test_grouping_is_deterministic_regardless_of_input_order():
    a = [S(30, 50, "Y"), S(0, 20, "X")]
    b = [S(10, 40, "Z")]
    first = build_conflicts("D", "r", a, b)
    second = build_conflicts("D", "r", list(reversed(a)), b)
    assert [c.as_dict() for c in first] == [c.as_dict() for c in second]


def test_conflict_ids_are_stable_and_ordered():
    conflicts = build_conflicts("DOC", "layer", [S(0, 5, "X"), S(50, 60, "Y")], [])
    assert [c.conflict_id for c in conflicts] == ["DOC::layer::0000", "DOC::layer::0001"]
