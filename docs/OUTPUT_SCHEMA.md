# Output schema

## Working log — `<out>/decisions/<doc>__<layer>~<digest>.json`

One file per (document, layer), written atomically after every decision. The readable part of
the name is for browsing; the 16-character digest of the exact (document id, layer) pair is what
keeps two different documents from ever sharing a file (`case/1` and `case:1` sanitise to the
same string, and `Case1` and `case1` are one file on a case-insensitive disk). Logs written by
earlier builds under the plain `<doc>__<layer>.json` name are adopted and renamed on first use,
provided the payload names that document and layer.

The log holds the reviewer's decisions only. Exact agreements are never written: they are derived
from the two annotators' spans every time they are needed, so reading a document never creates
data and an export does not depend on the document having been opened in the interface.

```json
{"schema_version": "1.0", "doc_id": "C0001", "layer": "rhetorical",
 "text_sha256": "e12b33fb…",
 "decisions": [
   {"conflict_id": "C0001::rhetorical::0003", "doc_id": "C0001", "layer": "rhetorical",
    "decision_type": "TAKE_A", "shape": "ROLE_CLASH",
    "spans": [{"begin": 347, "end": 699, "labels": ["FACTS"], "origin": "A"}],
    "note": "", "fingerprint": "9f2c…", "reviewed": true,
    "attempt_seconds": 12.4, "cumulative_seconds": 31.0,
    "visit_count": 2, "revision_count": 1,
    "decided_at": "2026-08-31T09:14:02+00:00"}]}
```

`decision_type` is one of `AUTO_AGREE`, `TAKE_A`, `TAKE_B`, `TAKE_BOTH`, `MULTI_ROLE`, `CUSTOM`,
`DROP`, `DEFER` (logs written before the rename say `FLAG`; they are read as `DEFER`). It is
**derived from the verified `origin` of each span**, not from which button was pressed.
`TAKE_A` / `TAKE_B` mean every kept span reproduces one of that annotator's spans (not
necessarily all of them). `TAKE_BOTH` means the reviewer deliberately retained spans from both
annotators' structures on different extents; it does not mean that A and B agreed, and the
resolved structure may be nested or overlapping if the annotation scheme allows that.
`MULTI_ROLE` is the reviewer's own judgement that one extent carries several roles.

`text_sha256` is the hash of the document text the offsets were measured against. A log whose
hash disagrees with the current text is refused, on every path that reads it: the interface
reports the document as unreadable, and `export` and `summary` skip it. Nothing ever re-stamps a
log with a different hash.

`fingerprint` digests the A/B spans the decision was made against. When the source annotation
later changes, or the region disappears altogether, the decision is **void**: it stays in the
file, but it is treated everywhere as if it had never been made. The conflict is queued again
(with a "source changed" marker), the document is incomplete until it is decided afresh, and the
void decision contributes to no export, table or statistic other than the `stale` count.

Timing: `attempt_seconds` is the active time in the attempt that produced this decision,
`cumulative_seconds` the active time across every attempt, `visit_count` how many times the
conflict became the active target, `revision_count` how many times an existing decision was
replaced. A decision that replaces a void one starts all four afresh.

## Canonical export — `<out>/export/canonical/<doc>.json`

Lossless. One entry per (extent, role), each carrying its full provenance, plus one evaluation
row per adjudicated region.

```json
{"schema_version": "1.0", "doc_id": "C0001", "layer": "rhetorical",
 "text": "…", "text_length": 19730, "text_sha256": "e12b33fb…",
 "text_source": "annotator A",
 "adjudication": {"complete": true, "conflicts_total": 29, "agreed_automatically": 7,
                  "reviewed_by_human": 22, "flagged": 0, "undecided": 0, "stale": 0,
                  "role_inventory": ["ANALYSIS", "…"]},
 "spans": [
   {"begin": 347, "end": 699, "label": "FACTS", "layer": "rhetorical",
    "provenance": {"conflict_id": "C0001::rhetorical::0003",
                   "conflict_shape": "ROLE_CLASH", "decision_type": "TAKE_A",
                   "origin": "A", "co_located_roles": null,
                   "annotator_a": [{"begin": 347, "end": 699, "label": "FACTS", "layer": "rhetorical"}],
                   "annotator_b": [{"begin": 347, "end": 699, "label": "ISSUE", "layer": "rhetorical"}],
                   "reviewed": true, "decided_at": "…", "note": ""}}],
 "evaluation": [{"case_id": "C0001", "region_id": "C0001::rhetorical::0003", "…": "…"}]}
```

`co_located_roles` is non-null only where the reviewer deliberately placed several roles on one
extent, which records that as an explicit judgement rather than an accident of merging.
`decided_at` is empty for an automatic agreement: nobody decided it at any moment, and a derived
decision is identical every time it is derived.

`complete` is true only when every conflict has an effective decision and none is deferred.

### Evaluation rows

Every comparison is computed from the span structures, never from `decision_type`:

| field | question it answers |
|---|---|
| `final_exactly_equals_A` / `_B` / `_both`, `final_differs_from_both` | the set of (extent, role) pairs, i.e. the role assignment per extent |
| `boundary_equals_A` / `_B`, `boundary_differs_from_both` | the set of extents alone |
| `role_inventory_equals_A` / `_B`, `role_inventory_differs_from_both` | the set of roles alone, ignoring where they sit. Called *inventory* deliberately: two annotations that swap the roles of two spans have the same inventory |
| `role_reassigned_vs_A` / `_B` | same extents and same roles as that annotator, distributed differently over the extents — the case the inventory measure alone would hide |
| `structure_equals_A` / `_B`, `structure_differs_from_both` | the pattern of containment, crossing and separation, ignoring exact offsets |

## Round-trip export — `<out>/export/roundtrip/<doc>.json`

Optional, and written only for JSON-record input: the input record, copied field for field, with
this layer's spans replaced by the resolved ones and every other layer left untouched. brat and
column-file corpora get the canonical export only; asking for the mirror then produces a note,
not an error. The canonical export remains authoritative, and nothing is discarded to fit this
shape.

## Validation

Applied before a document is written. Nesting, crossing overlap and stacked roles are **legal**
and never flagged. Reported as errors: an offset outside the document, a role outside the layer
inventory, and the exact same (extent, role) appearing twice (which is also refused at the moment
such a decision is saved).

## `spans.tsv` — the span table

Written next to `canonical/` and `roundtrip/` by `export`. One row per resolved span, for a
spreadsheet or a statistics package. Tab-separated, UTF-8, with a header row.

| column | meaning |
|---|---|
| `document_id` | the document the span belongs to |
| `layer` | the annotation layer that was reviewed |
| `begin`, `end` | half-open character offsets `[begin, end)` into the document text |
| `length` | `end - begin`, so a spreadsheet needs no formula |
| `label` | the role. One row per role: a co-extensive multi-role span produces several rows |
| `origin` | `A`, `B`, or `reviewer` — verified against the source, not asserted by the client |
| `decision_type` | `AUTO_AGREE`, `TAKE_A`, `TAKE_B`, `TAKE_BOTH`, `MULTI_ROLE`, `CUSTOM` (`DROP` and `DEFER` produce no spans, so never appear) |
| `conflict_shape` | `ROLE_CLASH`, `BOUNDARY_SHIFT`, `SEGMENTATION`, `NESTING_DIFF`, `A_ONLY`, `B_ONLY`, `MIXED`, `AGREEMENT` |
| `conflict_id` | joins back to the canonical export and the decision log |
| `co_located_roles` | other roles carried by the same extent, `\|`-separated |
| `reviewed` | `False` for an automatic agreement, `True` when a person decided |
| `decided_at` | ISO-8601 UTC; empty for an automatic agreement |
| `adjudicator` | whatever was passed to `--adjudicator` |

Two things it deliberately does not contain:

- **No document text.** The table can be shared or attached to a paper without carrying the
  corpus. Use `begin`/`end` against your own copy of the text.
- **No void rows.** A decision whose source annotation changed since it was made produces no
  row, and by default the document it belongs to is skipped as incomplete.

A tab or a newline inside a role is quoted rather than allowed to split a row, so a corpus with
an awkward label cannot silently produce a malformed table.

## `summary` — the corpus report

`adjudicated`, the outcome counts (`took_A`, `took_B`, `took_both`, `differs_from_both`),
`new_boundary`, `new_structure`, `new_role_inventory`, `role_reassigned`, the timing figures and
`revisions_total` are computed over effective, human-reviewed, non-deferred decisions only.
`stale` counts void decisions and is the only place they appear. Decisions with no recorded time
are excluded from the timing figures and counted under `untimed_decisions`. The `agreement`
block (span F1, role kappa on shared extents) is computed from the two annotators' spans alone
and does not depend on any decision.

Two counting conventions worth knowing when reading the outcome figures:

- a `DROP` on a conflict that only one annotator marked counts as *final equals* the annotator
  who marked nothing there;
- exact agreements are not adjudications and are outside every outcome denominator.
