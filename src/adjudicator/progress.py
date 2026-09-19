"""How far through a document, and through the corpus, the reviewer is.

One definition, used by the interface, the CLI and the export, so the three can never disagree
about whether something is finished. A DEFERRED item is deliberately *not* finished.
"""
from __future__ import annotations

from .conflicts import Conflict
from .decisions import Decision, effective_decisions


def document_progress(conflicts: list[Conflict], decisions: dict[str, Decision]) -> dict:
    # Stale decisions are void and automatic agreements are implied, whatever the log on disk
    # says. Applying that here means a raw log and an already-derived set give the same answer.
    decisions, stale = effective_decisions(conflicts, decisions)
    needing_review = [c for c in conflicts if not c.agreed]
    ids = {c.conflict_id for c in needing_review}
    relevant = {k: v for k, v in decisions.items() if k in ids}

    flagged = sum(1 for d in relevant.values() if d.is_open)
    dropped = sum(1 for d in relevant.values() if d.decision_type == "DROP")
    settled = sum(1 for d in relevant.values() if not d.is_open)
    undecided = len(ids - set(relevant))

    by_shape: dict[str, dict[str, int]] = {}
    for c in needing_review:
        bucket = by_shape.setdefault(c.shape, {"total": 0, "settled": 0})
        bucket["total"] += 1
        d = relevant.get(c.conflict_id)
        if d is not None and not d.is_open:
            bucket["settled"] += 1

    return {
        "conflicts": len(needing_review),
        "auto_agreed": len(conflicts) - len(needing_review),
        "settled": settled,
        "flagged": flagged,
        "dropped": dropped,
        "undecided": undecided,
        # decisions on disk that no longer apply; their conflicts are counted as undecided
        "stale": len(stale),
        "complete": undecided == 0 and flagged == 0,
        "percent": round(100 * settled / len(needing_review), 1) if needing_review else 100.0,
        "by_shape": by_shape,
    }
