"""The central invariant: a disagreement is never silently merged, and provenance never lies."""
import pytest

from adjudicator.conflicts import build_conflicts
from adjudicator.decisions import (
    Decision, DecisionError, ResolvedSpan, auto_agree, decide, derive_type, source_fingerprint,
)
from conftest import S

ROLES = ["FACTS", "ISSUE", "ANALYSIS", "LAW_REFERENCE"]


def one(a, b):
    return build_conflicts("D", "r", a, b)[0]


@pytest.fixture
def clash():
    return one([S(0, 10, "FACTS")], [S(0, 10, "ISSUE")])


def test_taking_a_side_records_that_side(clash):
    assert decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A")]).decision_type == "TAKE_A"
    assert decide(clash, [ResolvedSpan(0, 10, ("ISSUE",), "B")]).decision_type == "TAKE_B"


def test_the_reviewers_own_multi_role_judgement_is_allowed(clash):
    decision = decide(clash, [ResolvedSpan(0, 10, ("FACTS", "ISSUE"), "reviewer")])
    assert decision.decision_type == "MULTI_ROLE"


def test_one_role_from_each_annotator_on_one_extent_is_refused(clash):
    """This is the whole point of the tool: two people disagreeing must not become one span
    carrying both of their answers with nobody having decided it."""
    with pytest.raises(DecisionError, match="from annotator A"):
        decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A"),
                       ResolvedSpan(0, 10, ("ISSUE",), "B")])


def test_your_own_role_may_sit_beside_one_you_kept_from_an_annotator(clash):
    """Adding a role of your own to a span you took from A is an ordinary edit. It is nobody's
    disagreement being merged - you are the one deciding - so it must not be refused."""
    decision = decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A"),
                              ResolvedSpan(0, 10, ("ANALYSIS",), "reviewer")])
    assert decision.decision_type == "CUSTOM"
    other = decide(clash, [ResolvedSpan(0, 10, ("ISSUE",), "B"),
                           ResolvedSpan(0, 10, ("ANALYSIS",), "reviewer")])
    assert other.decision_type == "CUSTOM"


def test_only_roles_from_both_annotators_at_once_are_refused(clash):
    """The precise thing being prevented is A's answer and B's answer surviving together with
    nobody having decided it. Everything else is somebody's judgement."""
    with pytest.raises(DecisionError, match="from annotator A"):
        decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A"),
                       ResolvedSpan(0, 10, ("ISSUE",), "B")])
    # ... and the reviewer who genuinely means it writes one span, recorded as their own
    assert decide(clash, [ResolvedSpan(0, 10, ("FACTS", "ISSUE"), "reviewer")]
                  ).decision_type == "MULTI_ROLE"


def test_the_refusal_names_which_role_came_from_which_annotator(clash):
    with pytest.raises(DecisionError) as caught:
        decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A"),
                       ResolvedSpan(0, 10, ("ISSUE",), "B")])
    message = str(caught.value)
    assert "'FACTS'" in message and "'ISSUE'" in message
    assert "annotator A" in message and "annotator B" in message


def test_a_span_cannot_claim_a_source_it_did_not_come_from(clash):
    with pytest.raises(DecisionError, match="claims to come from"):
        decide(clash, [ResolvedSpan(0, 10, ("ISSUE",), "A")])
    with pytest.raises(DecisionError, match="claims to come from"):
        decide(clash, [ResolvedSpan(0, 10, ("ANALYSIS",), "A")])


def test_one_annotators_own_stacked_roles_may_be_taken_whole():
    conflict = one([S(0, 10, "FACTS"), S(0, 10, "ISSUE")], [S(0, 10, "FACTS")])
    decision = decide(conflict, [ResolvedSpan(0, 10, ("FACTS", "ISSUE"), "A")])
    assert decision.decision_type == "TAKE_A"


def test_spans_from_both_sides_on_different_extents_are_fine():
    conflict = one([S(0, 40, "FACTS")], [S(10, 20, "ANALYSIS")])
    decision = decide(conflict, [ResolvedSpan(0, 40, ("FACTS",), "A"),
                                 ResolvedSpan(10, 20, ("ANALYSIS",), "B")])
    assert decision.decision_type == "TAKE_BOTH"


def test_drop_and_flag_carry_no_spans(clash):
    assert decide(clash, [], intent="DROP").decision_type == "DROP"
    deferred = decide(clash, [], intent="DEFER")
    assert deferred.decision_type == "DEFER" and deferred.is_open
    # logs written before the rename still load
    assert decide(clash, [], intent="FLAG").decision_type == "DEFER"
    with pytest.raises(DecisionError, match="cannot carry spans"):
        decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A")], intent="DROP")


def test_an_empty_resolution_is_refused_with_advice(clash):
    with pytest.raises(DecisionError, match="use Drop"):
        decide(clash, [])


def test_roles_outside_the_inventory_are_refused(clash):
    with pytest.raises(DecisionError, match="not in this layer"):
        decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A")], labels=["ISSUE"])


def test_spans_past_the_end_of_the_document_are_refused(clash):
    with pytest.raises(DecisionError, match="past the end"):
        decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A")], text_length=5)


def test_a_span_may_not_repeat_a_role():
    with pytest.raises(DecisionError, match="repeats a role"):
        ResolvedSpan(0, 10, ("FACTS", "FACTS"), "reviewer")


def test_agreement_is_carried_over_without_a_human(clash):
    agreed = one([S(0, 10, "FACTS")], [S(0, 10, "FACTS")])
    decision = auto_agree(agreed)
    assert decision.decision_type == "AUTO_AGREE" and decision.reviewed is False
    with pytest.raises(DecisionError, match="not an agreement"):
        auto_agree(clash)


def test_agreement_preserves_stacked_roles():
    spans = [S(0, 10, "FACTS"), S(0, 10, "ISSUE")]
    decision = auto_agree(one(spans, spans))
    assert decision.spans[0].labels == ("FACTS", "ISSUE")


def test_fingerprint_changes_when_the_source_annotation_changes(clash):
    other = one([S(0, 10, "FACTS")], [S(0, 11, "ISSUE")])
    assert source_fingerprint(clash) != source_fingerprint(other)


def test_a_decision_survives_a_round_trip_through_json(clash):
    original = decide(clash, [ResolvedSpan(0, 10, ("FACTS",), "A")], note="clear enough")
    assert Decision.from_dict(original.as_dict()).as_dict() == original.as_dict()


def test_derive_type_reads_provenance_rather_than_guessing_from_shape():
    assert derive_type([]) == "DROP"
    assert derive_type([ResolvedSpan(0, 5, ("X",), "reviewer")]) == "CUSTOM"
    assert derive_type([ResolvedSpan(0, 5, ("X", "Y"), "reviewer")]) == "MULTI_ROLE"


def test_a_log_written_before_the_rename_still_loads_as_deferred():
    """DEFER was called FLAG in earlier builds. An existing decision log must not become
    unreadable, and a deferred item must not quietly turn into a resolved one."""
    legacy = {"conflict_id": "D::r::0000", "doc_id": "D", "layer": "r",
              "decision_type": "FLAG", "spans": [], "reviewed": True}
    restored = Decision.from_dict(legacy)
    assert restored.decision_type == "DEFER"
    assert restored.is_open, "a legacy FLAG must still block completion"
