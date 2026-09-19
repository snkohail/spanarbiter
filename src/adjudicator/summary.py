"""Corpus-level statistics, computed from the decision logs.

Everything a system-demonstration paper needs to report, derived deterministically so the same
logs always give the same numbers, and carrying no document text so the report can be shared
even when the corpus cannot.

The proportions here are computed from the SPAN STRUCTURES, never from `decision_type`.
`TAKE_A` means every span kept came from A — a reviewer who keeps one of A's three spans still
produces `TAKE_A`, and reporting that as "the final annotation equals annotator A" would be
false. See adjudicator.evaluation.

Only EFFECTIVE decisions are counted (see `decisions.effective_decisions`): a decision whose
source annotation changed since it was made is void, and appears in this report under `stale`
and nowhere else — not in the outcomes, not in the decision types, not in the timing.
"""
from __future__ import annotations

import statistics
from collections import Counter

from .agreement import cohens_kappa_on_shared_extents, span_f1
from .decisions import OPEN_TYPES, effective_decisions, revalidate_decisions
from .evaluation import decision_metadata
from .export import canonical_document, validate
from .progress import document_progress
from .project import Project, sha256_of


def _quantiles(values: list[float]) -> list[float | None]:
    if len(values) < 2:
        return [None, None]
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return [round(q1, 1), round(q3, 1)]


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def corpus_summary(project: Project, store, adjudicator: str = "") -> dict:
    documents = complete = 0
    conflicts_total = auto_agreed = 0
    by_shape: Counter = Counter()
    by_type: Counter = Counter()
    took_a = took_b = took_both = differs = 0
    new_boundary = new_structure = new_role_inventory = role_reassigned = 0
    deferred = dropped = stale_total = 0
    seconds: list[float] = []
    untimed = 0
    seconds_by_shape: dict[str, list[float]] = {}
    seconds_by_kind: dict[str, list[float]] = {"simple": [], "structural": []}
    visits: list[int] = []
    revisions: list[int] = []
    validation_errors: list[str] = []
    validated = 0

    for doc_id in project.doc_ids:
        documents += 1
        conflict_list = project.conflicts(doc_id)
        try:
            stored = store.load(doc_id, project.layer or "",
                                expect_text_sha256=sha256_of(project.docs[doc_id].text))
        except Exception as exc:
            validation_errors.append(f"{doc_id}: {type(exc).__name__}")
            continue

        decisions, outdated = effective_decisions(conflict_list, stored)
        stale_total += len(outdated)

        payload = canonical_document(project, doc_id, conflict_list, stored)
        errors = (revalidate_decisions(conflict_list, decisions, project.labels,
                                       payload["text_length"])
                  + validate(payload["spans"], payload["text_length"], project.labels))
        validated += 1
        if errors:
            # nothing is counted from a log that breaks the rules it was written under
            validation_errors.extend(f"{doc_id}: {e}" for e in errors[:3])
            continue

        progress = document_progress(conflict_list, stored)
        conflicts_total += progress["conflicts"]
        auto_agreed += progress["auto_agreed"]
        if progress["complete"]:
            complete += 1

        for conflict in conflict_list:
            if not conflict.agreed:
                by_shape[conflict.shape] += 1
            decision = decisions.get(conflict.conflict_id)
            if decision is None:
                continue
            by_type[decision.decision_type] += 1
            if decision.decision_type == "DEFER":
                deferred += 1
            if decision.decision_type == "DROP":
                dropped += 1
            if not decision.reviewed:
                continue                     # automatic agreements are not adjudications
            if decision.decision_type in OPEN_TYPES:
                # Deliberately unresolved. It is reported under "deferred"; counting it as an
                # outcome would put it in "differs from both" (it has no spans, so it equals
                # neither annotator) and overstate how often the reviewer overrode both.
                continue

            row = decision_metadata(conflict, decision)
            equals_a, equals_b = row["final_exactly_equals_A"], row["final_exactly_equals_B"]
            if equals_a and equals_b:
                took_both += 1
            elif equals_a:
                took_a += 1
            elif equals_b:
                took_b += 1
            else:
                differs += 1
            new_boundary += int(row["boundary_differs_from_both"])
            new_structure += int(row["structure_differs_from_both"])
            new_role_inventory += int(row["role_inventory_differs_from_both"])
            role_reassigned += int(row["role_reassigned_vs_A"] or row["role_reassigned_vs_B"])

            if row["cumulative_seconds"]:
                seconds.append(row["cumulative_seconds"])
                seconds_by_shape.setdefault(conflict.shape, []).append(row["cumulative_seconds"])
                seconds_by_kind[row["simple_or_structural"]].append(row["cumulative_seconds"])
            else:
                untimed += 1                 # a log without timing (imported or hand-written)
            visits.append(row["visit_count"])
            revisions.append(row["revision_count"])

    reviewed = took_a + took_b + took_both + differs
    share = lambda n: round(100 * n / reviewed, 1) if reviewed else 0.0

    layer = project.layer or ""
    span_pairs = [(project.docs[d].a.spans_in(layer), project.docs[d].b.spans_in(layer))
                  for d in project.doc_ids]

    return {
        "layer": project.layer,
        "adjudicator": adjudicator,
        "documents": documents,
        "documents_complete": complete,
        "conflicts": conflicts_total,
        "auto_agreed": auto_agreed,
        "adjudicated": reviewed,
        "percent_adjudicated": round(100 * reviewed / conflicts_total, 1) if conflicts_total else 0.0,
        "by_conflict_type": dict(by_shape),
        "by_decision_type": dict(by_type),
        "took_A": took_a, "took_B": took_b, "took_both": took_both,
        "differs_from_both": differs,
        "percent_took_A": share(took_a), "percent_took_B": share(took_b),
        "percent_differs_from_both": share(differs),
        "new_boundary": new_boundary, "new_structure": new_structure,
        # the final uses a role neither annotator used in that region
        "new_role_inventory": new_role_inventory,
        # same extents and same roles as one annotator, but the roles sit on different extents
        "role_reassigned": role_reassigned,
        "percent_new_boundary": share(new_boundary),
        "percent_new_structure": share(new_structure),
        "deferred": deferred, "dropped": dropped,
        # decisions on disk whose source annotation has changed: void, counted here only
        "stale": stale_total,
        "seconds_median": _median(seconds),
        "seconds_iqr": _quantiles(seconds),
        "seconds_by_conflict_type": {k: _median(v) for k, v in sorted(seconds_by_shape.items())},
        "simple_vs_structural": {
            kind: {"n": len(v), "seconds_median": _median(v)}
            for kind, v in seconds_by_kind.items()
        },
        "timed_decisions": len(seconds),
        "untimed_decisions": untimed,
        "visits_median": _median([float(v) for v in visits]),
        "revisions_total": sum(revisions),
        "validation": {"documents_validated": validated,
                       "errors": validation_errors[:20],
                       "error_count": len(validation_errors)},
        "agreement": {
            "span_f1": span_f1(span_pairs),
            "role_kappa_on_shared_extents": cohens_kappa_on_shared_extents(span_pairs),
        },
        "timing_note": ("active seconds while the conflict was the visible target, paused when "
                        "the tab was hidden; not a claim of millisecond accuracy. Decisions "
                        "with no recorded time are excluded from the timing figures and "
                        "counted under untimed_decisions"),
    }
