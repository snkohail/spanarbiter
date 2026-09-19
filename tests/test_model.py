"""Spans: the range arithmetic every other layer depends on."""
import pytest

from adjudicator.model import Span, dedupe, sort_spans, structure_flags
from conftest import S


def test_a_span_is_a_half_open_range():
    assert S(0, 10, "X").length == 10


@pytest.mark.parametrize("begin,end,message", [
    (5, 5, "empty or inverted"), (9, 4, "empty or inverted"), (-1, 4, "not be negative"),
])
def test_impossible_ranges_are_refused(begin, end, message):
    with pytest.raises(ValueError, match=message):
        Span(begin, end, "X")


def test_booleans_are_not_offsets():
    """bool subclasses int, so a plain isinstance check lets True through as offset 1."""
    with pytest.raises(TypeError):
        Span(True, 5, "X")


def test_a_span_needs_a_label():
    with pytest.raises(ValueError, match="label"):
        Span(0, 5, "")


def test_touching_spans_do_not_overlap():
    assert not S(0, 5, "X").overlaps(S(5, 9, "Y"))
    assert S(0, 5, "X").overlaps(S(4, 9, "Y"))


def test_containment_and_crossing_are_distinguished():
    outer, inner, crossing = S(0, 100, "X"), S(10, 20, "Y"), S(50, 150, "Z")
    assert outer.contains(inner) and not inner.contains(outer)
    assert outer.crosses(crossing) and not outer.crosses(inner)


def test_reading_order_puts_containers_before_their_contents():
    ordered = sort_spans([S(10, 20, "inner"), S(0, 100, "outer"), S(0, 50, "middle")])
    assert [s.label for s in ordered] == ["outer", "middle", "inner"]


def test_dedupe_removes_exact_repeats_only():
    assert len(dedupe([S(0, 5, "X"), S(0, 5, "X"), S(0, 5, "Y")])) == 2


def test_structure_flags_name_what_is_actually_there():
    assert structure_flags([S(0, 100, "A"), S(10, 20, "B")]) >= {"NESTED"}
    assert structure_flags([S(0, 100, "A"), S(10, 20, "A")]) >= {"NESTED", "SAME_ROLE_NESTED"}
    assert "CROSSING" in structure_flags([S(0, 100, "A"), S(50, 150, "B")])
    assert "STACKED" in structure_flags([S(0, 10, "A"), S(0, 10, "B")])
    assert structure_flags([S(0, 10, "A")]) == set()
