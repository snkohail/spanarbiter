"""Export the resolved layer.

Two products, and the first is never sacrificed for the second:

* CANONICAL - lossless. Every resolved span with its full decision provenance: which annotator it
  came from, what the two annotators originally said, who decided, when, and why. This is the
  archival record and the thing a paper's reproducibility claim rests on.
* ROUND-TRIP - optional, and only produced when the input adapter can rebuild the source schema
  (JSON records). It mirrors the input file so existing readers keep working. Information that
  the input schema cannot represent is NOT discarded to make it fit; it stays in the canonical
  export.

What is exported is the EFFECTIVE decision set (see `decisions.effective_decisions`): exact
agreements are implied whether or not the interface ever wrote them to disk, and a decision whose
source annotation has since changed is void. Reading a document in the interface is therefore
never a precondition for a correct export.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field

from .conflicts import Conflict
from .decisions import Decision, effective_decisions, revalidate_decisions
from .evaluation import decision_metadata
from .project import Project, sha256_of


@dataclass
class ExportResult:
    written: list[str]
    skipped: list[tuple[str, str]]
    span_count: int
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"written": self.written, "skipped": [{"doc_id": d, "reason": r}
                                                     for d, r in self.skipped],
                "span_count": self.span_count, "notes": self.notes}


def resolved_spans(conflicts: list[Conflict], decisions: dict[str, Decision]) -> list[dict]:
    """Flatten decisions into one record per (extent, role), each carrying its provenance."""
    by_id = {c.conflict_id: c for c in conflicts}
    out: list[dict] = []
    for cid in sorted(decisions):
        decision = decisions[cid]
        if decision.decision_type in ("DROP", "DEFER"):
            continue
        conflict = by_id.get(cid)
        for span in decision.spans:
            stacked = len(span.labels) > 1
            for label in span.labels:
                out.append({
                    "begin": span.begin, "end": span.end, "label": label,
                    "layer": decision.layer,
                    "provenance": {
                        "conflict_id": cid,
                        "conflict_shape": decision.shape,
                        "decision_type": decision.decision_type,
                        "origin": span.origin,
                        "co_located_roles": sorted(span.labels) if stacked else None,
                        "annotator_a": [s.as_dict() for s in conflict.a_spans] if conflict else [],
                        "annotator_b": [s.as_dict() for s in conflict.b_spans] if conflict else [],
                        "reviewed": decision.reviewed,
                        "decided_at": decision.decided_at,
                        "note": decision.note or "",
                    },
                })
    out.sort(key=lambda s: (s["begin"], -s["end"], s["label"]))
    return out


def validate(spans: list[dict], text_length: int, inventory: list[str]) -> list[str]:
    """Nesting, crossing and stacked roles are all LEGAL. Only real corruption is reported."""
    errors: list[str] = []
    known = set(inventory)
    seen: set[tuple[int, int, str]] = set()
    for span in spans:
        if not (0 <= span["begin"] < span["end"] <= text_length):
            errors.append(f"{span['label']} at [{span['begin']},{span['end']}) is outside the "
                          f"document ({text_length} characters)")
        if known and span["label"] not in known:
            errors.append(f"role {span['label']!r} is not in the layer inventory")
        key = (span["begin"], span["end"], span["label"])
        if key in seen:
            errors.append(f"{span['label']} at [{span['begin']},{span['end']}) appears twice")
        seen.add(key)
    return errors


def canonical_document(project: Project, doc_id: str, conflicts: list[Conflict],
                       decisions: dict[str, Decision]) -> dict:
    paired = project.docs[doc_id]
    decisions, stale = effective_decisions(conflicts, decisions)
    spans = resolved_spans(conflicts, decisions)
    open_items = [d for d in decisions.values() if d.is_open]
    undecided = [c for c in conflicts if c.conflict_id not in decisions]
    return {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "layer": project.layer,
        "text": paired.text,
        "text_length": len(paired.text),
        "text_sha256": sha256_of(paired.text),
        "text_source": paired.text_source,
        "adjudication": {
            "complete": not open_items and not undecided,
            "conflicts_total": len(conflicts),
            "agreed_automatically": sum(1 for c in conflicts if c.agreed),
            "reviewed_by_human": sum(1 for d in decisions.values() if d.reviewed),
            "flagged": len(open_items),
            "undecided": len(undecided),
            "stale": len(stale),
            "role_inventory": project.labels,
        },
        "spans": spans,
        # One analysis row per adjudicated region. Derived from the structures, never from
        # decision_type, and carrying no document text.
        "evaluation": [decision_metadata(c, decisions[c.conflict_id])
                       for c in conflicts if c.conflict_id in decisions],
    }


def roundtrip_document(project: Project, doc_id: str, spans: list[dict]) -> dict:
    """Rebuild the *input* schema, replacing only this layer's spans. Everything else is copied
    through untouched, so nothing in the source file is lost. JSON records only."""
    paired = project.docs[doc_id]
    mapping = project.mapping_a          # the mirror rebuilds annotator A's own record
    if not mapping.get("spans"):
        raise ValueError(f"annotator A's files are {project.format_a or 'not JSON records'}; "
                         f"only JSON records can be mirrored")
    out = copy.deepcopy(paired.a.raw)
    layer_field = mapping.get("layer")

    def belongs_to_selected_layer(span: dict) -> bool:
        # With no layer field every original span lives in the single default layer, so every
        # one of them is being replaced. Treating "no layer field" as "matches nothing" keeps
        # the originals and appends the resolved spans beside them - a duplicated annotation.
        if not layer_field:
            return True
        return str(span.get(layer_field)) == project.layer

    kept = [s for s in (out.get(mapping["spans"]) or [])
            if not belongs_to_selected_layer(s)]
    for span in spans:
        record = {mapping["begin"]: span["begin"], mapping["end"]: span["end"],
                  mapping["label"]: span["label"]}
        if mapping.get("layer"):
            record[mapping["layer"]] = span["layer"]
        record["adjudicated"] = True
        kept.append(record)
    out[mapping["spans"]] = kept
    out["adjudication_provenance"] = {
        "layer": project.layer, "produced_by": "SpanArbiter",
        "canonical_export_is_authoritative": True,
    }
    return out


def export_project(project: Project, store, *, out_dir: str, roundtrip: bool = False,
                   only_complete: bool = True) -> ExportResult:
    canonical_dir = os.path.join(out_dir, "canonical")
    os.makedirs(canonical_dir, exist_ok=True)
    notes: list[str] = []
    if roundtrip and project.format_a != "json":
        # brat and column files are not rewritten: the tool has no writer for them, and a
        # half-mirror would be worse than none. The canonical export carries everything.
        notes.append(f"round-trip mirror not written: annotator A's files are "
                     f"{project.format_a}, and only JSON records can be mirrored")
        roundtrip = False
    rt_dir = os.path.join(out_dir, "roundtrip")
    if roundtrip:
        os.makedirs(rt_dir, exist_ok=True)

    written: list[str] = []
    skipped: list[tuple[str, str]] = []
    total = 0
    for doc_id in project.doc_ids:
        conflicts = project.conflicts(doc_id)
        expected = sha256_of(project.docs[doc_id].text)
        try:
            stored = store.load(doc_id, project.layer or "", expect_text_sha256=expected)
        except Exception as exc:                      # StaleTextError and unreadable logs alike
            skipped.append((doc_id, str(exc).split(". ")[0]))
            continue
        decisions, outdated = effective_decisions(conflicts, stored)
        # A void decision (source annotation changed since) never contributes a span. Where it
        # leaves a conflict undecided the document is incomplete and skipped by default; a void
        # automatic agreement or an orphaned region is simply superseded and blocks nothing.
        payload = canonical_document(project, doc_id, conflicts, stored)
        if only_complete and not payload["adjudication"]["complete"]:
            reason = (f"{payload['adjudication']['undecided']} undecided, "
                      f"{payload['adjudication']['flagged']} deferred")
            if outdated:
                reason += (f"; {len(outdated)} earlier decision(s) are void because the source "
                           f"annotation changed: {outdated[0]['reason']}")
            skipped.append((doc_id, reason))
            continue
        errors = (revalidate_decisions(conflicts, decisions, project.labels, payload["text_length"])
                  + validate(payload["spans"], payload["text_length"], project.labels))
        if errors:
            skipped.append((doc_id, "; ".join(errors[:3])))
            continue
        with open(os.path.join(canonical_dir, f"{doc_id}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        if roundtrip:
            rt = roundtrip_document(project, doc_id, payload["spans"])
            with open(os.path.join(rt_dir, f"{doc_id}.json"), "w", encoding="utf-8") as fh:
                json.dump(rt, fh, ensure_ascii=False, indent=1)
        written.append(doc_id)
        total += len(payload["spans"])
    return ExportResult(written, skipped, total, notes)


# --------------------------------------------------------------------------- span table

SPAN_TABLE_COLUMNS = [
    "document_id", "layer", "begin", "end", "length", "label", "origin",
    "decision_type", "conflict_shape", "conflict_id", "co_located_roles",
    "reviewed", "decided_at", "adjudicator",
]


def write_span_table(project: Project, store, path: str,
                     adjudicator: str = "", only_complete: bool = False) -> int:
    """One row per resolved span, for analysis and for the paper's tables.

    Carries offsets and roles but NEVER the document text: this file is meant to be shareable
    alongside results, and the corpus may not be.
    """
    import csv

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    written = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        # QUOTE_MINIMAL with a tab delimiter quotes any field containing a tab or newline, so a
        # role like `ODD\tROLE` cannot split a row.
        writer = csv.DictWriter(fh, fieldnames=SPAN_TABLE_COLUMNS, delimiter="\t",
                                quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writeheader()
        for doc_id in project.doc_ids:
            conflicts = project.conflicts(doc_id)
            try:
                stored = store.load(doc_id, project.layer or "",
                                    expect_text_sha256=sha256_of(project.docs[doc_id].text))
            except Exception:
                continue
            payload = canonical_document(project, doc_id, conflicts, stored)
            if only_complete and not payload["adjudication"]["complete"]:
                continue
            decisions, _ = effective_decisions(conflicts, stored)
            if revalidate_decisions(conflicts, decisions, project.labels, payload["text_length"]) \
                    or validate(payload["spans"], payload["text_length"], project.labels):
                continue                     # the export reports why; the table stays clean
            for span in payload["spans"]:
                provenance = span["provenance"]
                writer.writerow({
                    "document_id": doc_id, "layer": span["layer"],
                    "begin": span["begin"], "end": span["end"],
                    "length": span["end"] - span["begin"], "label": span["label"],
                    "origin": provenance["origin"],
                    "decision_type": provenance["decision_type"],
                    "conflict_shape": provenance["conflict_shape"],
                    "conflict_id": provenance["conflict_id"],
                    "co_located_roles": "|".join(provenance["co_located_roles"] or []),
                    "reviewed": provenance["reviewed"],
                    "decided_at": provenance["decided_at"],
                    "adjudicator": adjudicator,
                })
                written += 1
    return written
