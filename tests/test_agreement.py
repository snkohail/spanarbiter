"""Corpus-level agreement metrics: span F1 and Cohen's kappa on shared extents.

Pure functions over spans, so every case here is hand-computed rather than derived from a
running project - the point of the module is that the arithmetic itself is right.
"""
from adjudicator.agreement import cohens_kappa_on_shared_extents, span_f1
from conftest import S


def test_f1_is_symmetric_dice_overlap_on_exact_matches():
    a = [S(0, 10, "X"), S(20, 30, "Y")]
    b = [S(0, 10, "X"), S(20, 30, "Z")]     # one shared extent, but the label differs
    result = span_f1([(a, b)])
    assert result == {"a_spans": 2, "b_spans": 2, "shared": 1,
                      "precision_a_vs_b": 0.5, "recall_a_vs_b": 0.5, "f1": 0.5}


def test_f1_is_one_on_perfect_agreement():
    spans = [S(0, 10, "X"), S(20, 30, "Y")]
    assert span_f1([(spans, spans)])["f1"] == 1.0


def test_f1_is_zero_on_total_disagreement():
    a, b = [S(0, 10, "X")], [S(20, 30, "Y")]
    result = span_f1([(a, b)])
    assert result["shared"] == 0 and result["f1"] == 0.0


def test_f1_is_none_when_nobody_annotated_anything():
    assert span_f1([([], [])])["f1"] is None


def test_f1_aggregates_across_documents():
    doc1 = ([S(0, 10, "X")], [S(0, 10, "X")])           # 1/1 shared
    doc2 = ([S(0, 10, "X")], [S(0, 10, "Y")])           # 0/1 shared
    result = span_f1([doc1, doc2])
    assert result == {"a_spans": 2, "b_spans": 2, "shared": 1,
                      "precision_a_vs_b": 0.5, "recall_a_vs_b": 0.5, "f1": 0.5}


def test_kappa_matches_hand_computed_confusion_matrix():
    # Four shared extents: (X,X), (X,Y), (Y,Y), (Y,Y).
    # po = 3/4 = .75; pe = (2/4 * 1/4) + (2/4 * 3/4) = .5; kappa = (.75-.5)/(1-.5) = .5
    a = [S(0, 10, "X"), S(20, 30, "X"), S(40, 50, "Y"), S(60, 70, "Y")]
    b = [S(0, 10, "X"), S(20, 30, "Y"), S(40, 50, "Y"), S(60, 70, "Y")]
    result = cohens_kappa_on_shared_extents([(a, b)])
    assert result["n"] == 4
    assert result["observed_agreement"] == 0.75
    assert result["expected_agreement"] == 0.5
    assert result["kappa"] == 0.5


def test_kappa_is_one_on_perfect_role_agreement():
    spans = [S(0, 10, "X"), S(20, 30, "Y"), S(40, 50, "X")]
    result = cohens_kappa_on_shared_extents([(spans, spans)])
    assert result["kappa"] == 1.0


def test_stacked_extents_are_excluded_not_counted_as_disagreement():
    # Extent (0,10) is stacked on A's side (two roles on one extent): not one categorical
    # judgement, so it must not enter the confusion matrix at all.
    a = [S(0, 10, "X"), S(0, 10, "Y"), S(20, 30, "Z")]
    b = [S(0, 10, "X"), S(20, 30, "Z")]
    result = cohens_kappa_on_shared_extents([(a, b)])
    assert result["n"] == 1                  # only (20,30) qualifies
    assert result["skipped_stacked"] == 1
    assert result["observed_agreement"] == 1.0
    # A single item, single category: expected agreement is 1.0 by construction, so kappa's
    # (po - pe) / (1 - pe) is 0/0 - undefined, not "perfect". None is the honest answer.
    assert result["kappa"] is None


def test_kappa_is_none_when_no_shared_extent_exists():
    a, b = [S(0, 10, "X")], [S(20, 30, "Y")]
    result = cohens_kappa_on_shared_extents([(a, b)])
    assert result == {"n": 0, "skipped_stacked": 0, "kappa": None,
                      "observed_agreement": None, "expected_agreement": None, "labels": []}
