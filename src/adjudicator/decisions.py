"""Reviewer decisions: what the resolved annotation is, and where every part of it came from.

The central rule of this tool: **a disagreement is never silently merged.** If A says a passage
is one role and B says it is another, the resolved annotation may carry A's role, or B's role, or
both roles *if the reviewer explicitly says the passage performs both* - but it must never end up
with both roles merely because two people disagreed.

That rule is enforced by making provenance explicit rather than guessed. Every resolved span
declares its `origin`, and the store VERIFIES the claim against the source annotation instead of
trying to infer intent from the shape of the result.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from .conflicts import Conflict
from .model import Span

ORIGIN_A, ORIGIN_B, ORIGIN_REVIEWER = "A", "B", "reviewer"
ORIGINS = (ORIGIN_A, ORIGIN_B, ORIGIN_REVIEWER)

DECISION_TYPES = (
    "AUTO_AGREE",   # both annotators already agreed; recorded, not asked
    "TAKE_A",       # every resolved span reproduces one of A's spans
    "TAKE_B",       # every resolved span reproduces one of B's spans
    "TAKE_BOTH",    # spans from both, on genuinely different extents
    "MULTI_ROLE",   # reviewer judged one extent to carry several roles at once
    "CUSTOM",       # reviewer authored or altered at least one span
    "DROP",         # nothing here should be annotated
    "DEFER",        # deliberately unresolved; blocks completion
)
OPEN_TYPES = ("DEFER",)

# Logs written before the rename used FLAG for the same thing.
LEGACY_TYPES = {"FLAG": "DEFER"}


class DecisionError(ValueError):
    """A decision that would corrupt the resolved layer. Always surfaced to the reviewer."""


@dataclass(frozen=True)
class ResolvedSpan:
    begin: int
    end: int
    labels: tuple[str, ...]
    origin: str

    def __post_init__(self) -> None:
        if self.origin not in ORIGINS:
            raise DecisionError(f"unknown origin {self.origin!r}; expected one of {ORIGINS}")
        if not self.labels:
            raise DecisionError(f"span [{self.begin},{self.end}) has no role")
        if not all(isinstance(label, str) and label for label in self.labels):
            raise DecisionError(f"span [{self.begin},{self.end}) has a role that is not a "
                                f"non-empty text label: {list(self.labels)!r}")
        if len(set(self.labels)) != len(self.labels):
            raise DecisionError(f"span [{self.begin},{self.end}) repeats a role")
        try:
            Span(self.begin, self.end, self.labels[0])      # reuse the range/type checks
        except (TypeError, ValueError) as exc:
            # Always a DecisionError, so the server answers with the reason (400) rather than
            # an anonymous internal error.
            raise DecisionError(str(exc)) from None

    @property
    def extent(self) -> tuple[int, int]:
        return (self.begin, self.end)

    def as_dict(self) -> dict:
        return {"begin": self.begin, "end": self.end,
                "labels": list(self.labels), "origin": self.origin}

    @staticmethod
    def from_dict(d: dict) -> "ResolvedSpan":
        # A malformed request should name the field it is missing. Letting KeyError escape
        # reached the client as the bare text "KeyError: 'labels'", which says nothing useful.
        missing = [k for k in ("begin", "end", "labels") if k not in d]
        if missing:
            raise DecisionError("span is missing " + ", ".join(missing))
        begin, end = _offset(d["begin"]), _offset(d["end"])
        labels = d["labels"]
        if isinstance(labels, str) or not isinstance(labels, (list, tuple)):
            raise DecisionError(f"span labels must be a list of roles, got {labels!r}")
        return ResolvedSpan(begin, end, tuple(labels), str(d.get("origin", ORIGIN_REVIEWER)))


def _offset(value) -> int:
    """A character offset from a request: an int, an integral float, or a numeric string.
    Booleans are refused (bool is a subclass of int, so int(True) would quietly become 1), and
    so is anything with a fractional part."""
    if isinstance(value, bool):
        raise DecisionError(f"span offsets must be whole numbers, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value.strip())
    raise DecisionError(f"span offsets must be whole numbers, got {value!r}")


def _side_spans(conflict: Conflict, origin: str) -> list[Span]:
    return conflict.a_spans if origin == ORIGIN_A else conflict.b_spans


def verify_origins(conflict: Conflict, spans: Sequence[ResolvedSpan]) -> None:
    """A span may only claim to come from an annotator if that annotator actually wrote it.

    Without this the origin field would be decoration; with it, provenance in the exported layer
    is a checked fact.
    """
    for sp in spans:
        if sp.origin == ORIGIN_REVIEWER:
            continue
        available = {(s.begin, s.end, s.label) for s in _side_spans(conflict, sp.origin)}
        missing = [l for l in sp.labels if (sp.begin, sp.end, l) not in available]
        if missing:
            raise DecisionError(
                f"span [{sp.begin},{sp.end}) claims to come from annotator {sp.origin} with "
                f"role(s) {missing}, but {sp.origin} did not annotate that. Mark it as a "
                f"reviewer edit instead.")


def check_stacking(spans: Sequence[ResolvedSpan]) -> None:
    """Several roles on ONE extent must be somebody's deliberate judgement.

    The precise thing to prevent is A's role and B's role ending up on the same text with
    NOBODY having decided that - the disagreement quietly becoming a union.

    Legal:   one annotator wrote all of those roles there; or the reviewer put them there, alone
             or alongside a role they kept from one annotator. Adding a role of your own to a
             span you took from A is an ordinary edit, not a merge of anyone's disagreement.
    Illegal: roles drawn from BOTH annotators on one extent. The reviewer who genuinely judges
             the passage to carry both writes it as a single span, which is recorded as their
             own explicit multi-role decision.
    """
    by_extent: dict[tuple[int, int], list[ResolvedSpan]] = {}
    for sp in spans:
        by_extent.setdefault(sp.extent, []).append(sp)
    for extent, group in by_extent.items():
        roles = {l for sp in group for l in sp.labels}
        if len(roles) <= 1:
            continue
        origins = {sp.origin for sp in group}
        if not {ORIGIN_A, ORIGIN_B} <= origins:
            continue
        from_a = sorted({l for sp in group if sp.origin == ORIGIN_A for l in sp.labels})
        from_b = sorted({l for sp in group if sp.origin == ORIGIN_B for l in sp.labels})
        raise DecisionError(
            f"extent [{extent[0]},{extent[1]}) would carry {from_a} from annotator A and "
            f"{from_b} from annotator B at once. Nobody has decided that the passage performs "
            f"both roles - it is the two annotators' disagreement being kept rather than "
            f"settled. Take one side, or put the roles you want on a single span, which records "
            f"the multi-role judgement as your own.")


def derive_type(spans: Sequence[ResolvedSpan]) -> str:
    """The decision type is READ OFF the declared provenance, never guessed from the shape."""
    if not spans:
        return "DROP"
    origins = {sp.origin for sp in spans}
    stacked = any(len(sp.labels) > 1 for sp in spans)
    if origins == {ORIGIN_REVIEWER}:
        return "MULTI_ROLE" if stacked else "CUSTOM"
    if ORIGIN_REVIEWER in origins:
        return "CUSTOM"
    if origins == {ORIGIN_A}:
        return "TAKE_A"
    if origins == {ORIGIN_B}:
        return "TAKE_B"
    return "TAKE_BOTH"


def source_fingerprint(conflict: Conflict) -> str:
    """Digest of the A/B spans a decision was made against, so later edits to the source
    annotation are detected rather than silently inherited."""
    payload = json.dumps(
        {"a": [[s.begin, s.end, s.label] for s in conflict.a_spans],
         "b": [[s.begin, s.end, s.label] for s in conflict.b_spans]},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def stale_decisions(conflicts, decisions: dict) -> list[dict]:
    """Decisions that can no longer be trusted against the source annotation as it stands now.

    A fingerprint that is merely STORED proves nothing. It has to be recomputed from the current
    conflict and compared, or an annotation edited after adjudication is exported as though the
    reviewer had seen it.
    """
    current = {c.conflict_id: c for c in conflicts}
    out: list[dict] = []
    for conflict_id, decision in sorted(decisions.items()):
        conflict = current.get(conflict_id)
        if conflict is None:
            out.append({"conflict_id": conflict_id,
                        "reason": "this region no longer exists in the source annotation"})
            continue
        if decision.fingerprint and decision.fingerprint != source_fingerprint(conflict):
            out.append({"conflict_id": conflict_id,
                        "reason": "the source annotation changed after this decision was recorded"})
    return out


def effective_decisions(conflicts, stored: dict) -> "tuple[dict[str, Decision], list[dict]]":
    """The decisions that apply to the conflicts AS THEY STAND NOW, plus the stale ones.

    Derived deterministically from the conflicts and the stored log, and used by every consumer
    (interface, export, span table, summary, progress), so none of them can disagree about what
    is decided:

    * every exact agreement carries an automatic decision, whether or not anything was ever
      written to disk for it. Reading a document must not be the act that creates data.
    * a stored decision whose source annotation has changed, or whose region no longer exists,
      is VOID. It is reported as stale and otherwise treated as if it had never been made: the
      conflict is queued again, and the void decision contributes to no statistic.

    The stored log itself is never modified here; a void entry stays on disk until the reviewer
    decides the conflict again.
    """
    stale = stale_decisions(conflicts, stored)
    void = {s["conflict_id"] for s in stale}
    current = {cid: d for cid, d in stored.items() if cid not in void}
    for conflict in conflicts:
        if conflict.agreed and conflict.conflict_id not in current:
            current[conflict.conflict_id] = auto_agree(conflict)
    return current, stale


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Decision:
    conflict_id: str
    doc_id: str
    layer: str
    decision_type: str
    spans: list[ResolvedSpan]
    shape: str = ""
    note: str = ""
    fingerprint: str = ""
    reviewed: bool = True
    # attempt_seconds  - active time in THIS attempt
    # cumulative_seconds - active time across every attempt at this conflict
    # visit_count      - times the conflict became the active target, not times it was saved
    # revision_count   - times an existing decision was replaced
    attempt_seconds: float = 0.0
    cumulative_seconds: float = 0.0
    visit_count: int = 1
    revision_count: int = 0
    decided_at: str = field(default_factory=now_iso)

    @property
    def is_open(self) -> bool:
        return self.decision_type in OPEN_TYPES

    def as_dict(self) -> dict:
        return {"conflict_id": self.conflict_id, "doc_id": self.doc_id, "layer": self.layer,
                "decision_type": self.decision_type, "shape": self.shape,
                "spans": [s.as_dict() for s in self.spans], "note": self.note,
                "fingerprint": self.fingerprint, "reviewed": self.reviewed,
                "attempt_seconds": round(self.attempt_seconds, 1),
                "cumulative_seconds": round(self.cumulative_seconds, 1),
                "visit_count": self.visit_count, "revision_count": self.revision_count,
                "decided_at": self.decided_at}

    @staticmethod
    def from_dict(d: dict) -> "Decision":
        return Decision(
            conflict_id=d["conflict_id"], doc_id=d["doc_id"], layer=d.get("layer", ""),
            decision_type=LEGACY_TYPES.get(d["decision_type"], d["decision_type"]),
            shape=d.get("shape", ""),
            spans=[ResolvedSpan.from_dict(x) for x in d.get("spans", [])],
            note=d.get("note", ""), fingerprint=d.get("fingerprint", ""),
            reviewed=bool(d.get("reviewed", True)),
            attempt_seconds=float(d.get("attempt_seconds", d.get("seconds", 0))),
            cumulative_seconds=float(d.get("cumulative_seconds", d.get("seconds", 0))),
            visit_count=int(d.get("visit_count", d.get("visits", 1))),
            revision_count=int(d.get("revision_count", 0)),
            decided_at=d.get("decided_at", ""))


def decide(conflict: Conflict, spans: Sequence[ResolvedSpan], *,
           intent: str | None = None, note: str = "", labels: Sequence[str] | None = None,
           text_length: int | None = None, attempt_seconds: float = 0.0,
           cumulative_seconds: float | None = None, visit_count: int = 1,
           revision_count: int = 0) -> Decision:
    """Validate a proposed resolution and record it. Raises DecisionError with a message written
    for the reviewer, not for a log file."""
    spans = list(spans)

    intent = LEGACY_TYPES.get(intent, intent)
    if intent in ("DROP", "DEFER"):
        if spans:
            raise DecisionError(f"{intent} resolves to no annotation, so it cannot carry spans")
        dtype = intent
    elif intent is not None:
        raise DecisionError(f"unknown intent {intent!r}; expected DROP or DEFER")
    else:
        if not spans:
            raise DecisionError("a resolution needs at least one span; use Drop to remove this "
                                "annotation entirely, or Defer to come back to it")
        dtype = derive_type(spans)

    check_spans(conflict, spans, labels=labels, text_length=text_length)

    return Decision(conflict_id=conflict.conflict_id, doc_id=conflict.doc_id,
                    layer=conflict.layer, decision_type=dtype, shape=conflict.shape,
                    spans=spans, note=note, fingerprint=source_fingerprint(conflict),
                    reviewed=True, attempt_seconds=attempt_seconds,
                    cumulative_seconds=(cumulative_seconds if cumulative_seconds is not None
                                        else attempt_seconds),
                    visit_count=visit_count, revision_count=revision_count)


def check_spans(conflict: Conflict, spans: Sequence[ResolvedSpan], *,
                labels: Sequence[str] | None = None, text_length: int | None = None) -> None:
    """Every rule a resolution has to satisfy, applied to a proposed span set. Raises
    DecisionError. Used when a decision is made, and again on every stored decision before it is
    exported or counted, so a log edited by hand is held to the same rules as a live one."""
    inventory = set(labels) if labels else None
    seen: set[tuple[int, int, str]] = set()
    for sp in spans:
        if text_length is not None and sp.end > text_length:
            raise DecisionError(f"span [{sp.begin},{sp.end}) runs past the end of the document "
                                f"({text_length} characters)")
        if inventory is not None:
            unknown = [l for l in sp.labels if l not in inventory]
            if unknown:
                raise DecisionError(f"role(s) {unknown} are not in this layer's inventory")
        for label in sp.labels:
            # The export refuses a document in which one (extent, role) appears twice, so a
            # decision that would produce that must be refused here, where it can be corrected.
            if (sp.begin, sp.end, label) in seen:
                raise DecisionError(f"{label} at [{sp.begin},{sp.end}) is given twice in one "
                                    f"resolution")
            seen.add((sp.begin, sp.end, label))
    verify_origins(conflict, spans)
    check_stacking(spans)


def revalidate_decisions(conflicts, decisions: dict, labels: Sequence[str] | None = None,
                         text_length: int | None = None) -> list[str]:
    """Hold a stored log to the rules it was written under. Returns one message per violation.

    Fingerprints prove the source annotation is unchanged; they do not prove the decision itself
    is well formed. A file edited by hand can claim an origin the annotator never wrote, stack A's
    and B's roles on one extent, carry a role outside the inventory, mislabel its own type, or
    hold negative timing. Nothing is exported or counted from a log that fails here.
    """
    by_id = {c.conflict_id: c for c in conflicts}
    errors: list[str] = []
    for cid in sorted(decisions):
        decision = decisions[cid]
        conflict = by_id.get(cid)
        if conflict is None:
            continue                         # reported as stale, not as malformed
        kind = decision.decision_type
        if kind not in DECISION_TYPES:
            errors.append(f"{cid}: unknown decision type {kind!r}")
            continue
        if (kind == "AUTO_AGREE") == decision.reviewed:
            errors.append(f"{cid}: {kind} is marked {'reviewed' if decision.reviewed else 'not reviewed'}")
        if any(not math.isfinite(v) or v < 0 for v in
               (decision.attempt_seconds, decision.cumulative_seconds,
                float(decision.visit_count), float(decision.revision_count))):
            errors.append(f"{cid}: timing or visit counts are negative or not finite")
        if kind in OPEN_TYPES or kind == "DROP":
            if decision.spans:
                errors.append(f"{cid}: {kind} carries spans")
            continue
        if not decision.spans:
            errors.append(f"{cid}: {kind} has no spans")
            continue
        if kind != "AUTO_AGREE" and derive_type(decision.spans) != kind:
            errors.append(f"{cid}: recorded as {kind} but its spans' provenance gives "
                          f"{derive_type(decision.spans)}")
        try:
            check_spans(conflict, decision.spans, labels=labels, text_length=text_length)
        except DecisionError as exc:
            errors.append(f"{cid}: {exc}")
    return errors


def auto_agree(conflict: Conflict) -> Decision:
    """Exact agreement is carried over without asking - the only decision made automatically."""
    if not conflict.agreed:
        raise DecisionError(f"{conflict.conflict_id} is not an agreement")
    by_extent: dict[tuple[int, int], list[str]] = {}
    for s in conflict.a_spans:
        by_extent.setdefault(s.extent, []).append(s.label)
    spans = [ResolvedSpan(b, e, tuple(sorted(labels)), ORIGIN_A)
             for (b, e), labels in sorted(by_extent.items())]
    # No timestamp: nobody decided this at any moment, and a derived decision must be the same
    # every time it is derived, so two exports of one log are identical.
    return Decision(conflict_id=conflict.conflict_id, doc_id=conflict.doc_id,
                    layer=conflict.layer, decision_type="AUTO_AGREE", shape=conflict.shape,
                    spans=spans, fingerprint=source_fingerprint(conflict),
                    reviewed=False, attempt_seconds=0.0, cumulative_seconds=0.0,
                    visit_count=0, revision_count=0, decided_at="")
