# Sample guidelines

What the reviewer sees in the **Guidelines** panel. Drop a `guidelines.md` beside `annotator_A/`
as here and `--project` finds it; or point `--guide` at any Markdown file. It is re-read every
time the panel opens, so you can edit it mid-session.

## The four cases

Four synthetic judgments, two English and two Arabic, doubly annotated. Every disagreement is
one that two careful annotators could reasonably reach; the rules below say how to settle each.

| Case | What the two annotators disagree about |
|---|---|
| `EN001` | construction dispute: one factual narrative against three events · the citation with or without the rule it states · a sentence that turns from the defendant's argument into the court's finding · a passing remark only A marked |
| `EN002` | tenancy appeal: a procedural line only B marked · the judgment below as case history with the order nested as decision, or as decision throughout · what the appellant asks, read as fact or as argument · a sentence neither annotator labelled quite right · a citation nested in the reasoning or not · one reasoning span across two paragraphs against two |
| `AR001` | labour dispute: the relief sought, read as fact or as argument · one defence memorandum against its two defences · a nested citation · a passing remark only A marked · the citation with or without the rule it states |
| `AR002` | cassation: the heading as one unit or two · a nested citation · a sentence that turns from the respondent's argument into the court's finding · a dismissive line only B marked |

## The roles

| Role | Use it for |
|---|---|
| `PREAMBLE` | Court, parties, case number, dates, procedural recitals — the heading matter. |
| `FACTS` | What happened and what was claimed, as the court records it. |
| `ISSUE` | The question the court has to answer. |
| `ARGUMENT_PLAINTIFF` | What the claimant, appellant or applicant says. |
| `ARGUMENT_DEFENDANT` | What the respondent says. |
| `LAW_REFERENCE` | A cited article, statute or precedent. |
| `ANALYSIS` | The court's reasoning. |
| `DECISION` | What a court of first instance orders, or an earlier ruling as the court recites it. |
| `DECISION_APPEAL` | The ruling on an appeal or cassation: admitted, dismissed, set aside, remitted. |

## How to settle the recurring dilemmas

- **Units.** One narrative that reports several distinct events (`EN001`), or a memorandum
  that advances two distinct defences (`AR001`), is split into one span per event or defence:
  prefer the finer reading. Heading matter stays one span (`AR002`).
- **Citations.** `LAW_REFERENCE` covers the citation itself, not the rule it goes on to state
  (`EN001`, `AR001`). Inside the court's reasoning it is nested in the `ANALYSIS` span, not a
  replacement for it (`EN002`, `AR001`, `AR002`): keep the reading that has both.
- **The relief sought.** A sentence recording what a party asked for is at once part of the
  facts of the case and that party's argument (`EN002`, `AR001`). Record both roles rather than
  losing one: *Both roles*.
- **An earlier ruling recited** by the court. The order itself is `DECISION`; the sentence that
  recites it is case history, `FACTS`, with the `DECISION` nested inside. Keep the reading that
  has both (`EN002`).
- **Transitions.** A sentence that opens with a party's contention and turns into the court's
  finding (`EN001`, `AR002`) is two things in sequence. Edit the final annotation: the contention
  as `ARGUMENT_DEFENDANT`, the finding as `ANALYSIS`, split where the sentence turns. The same
  applies when a sentence states an argument and then the issue (`EN002`): neither annotator's
  single label is right, so split it.
- **Omissions.** A passing remark by the court is still `ANALYSIS`, and a procedural recital is
  still heading matter: take the side that marked it (`EN001`, `AR001`, `AR002`, `EN002`).
- If neither annotator is right, edit the final annotation. It is stored as yours, and the export
  says so.
- If you cannot settle it, **Defer**. That keeps the document incomplete, which is the honest
  state; it never counts as resolved.

*Replace this file with your own guidelines.*
