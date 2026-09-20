<img src="web/favicon.svg" width="64" alt="">

# SpanArbiter

A local workbench where a third reviewer resolves disagreements between two independent span annotators.

It reads two directories of annotation files — JSON records, brat standoff, or column files with BIO tags — works out where the annotators disagree, groups
those disagreements into independently decidable **conflicts**, classifies each one by *shape*,
and steps the reviewer through them with the keyboard. The result is a resolved layer in which
every span records where it came from.

Runs on the **Python standard library alone**. No `pip install`, no build step, no framework, no
network, no telemetry, no model anywhere in the loop.

```bash
git clone <repository URL> && cd spanarbiter
python3 adjudicate.py serve --project path/to/project
```

A **project** is one folder holding `annotator_A/` and `annotator_B/`. Decisions go to
`<project>/adjudication/`, and a `guidelines.md` beside them is picked up automatically. Any of
those paths can still be given individually, and an explicit flag always wins:

```bash
python3 adjudicate.py serve --a-dir path/to/annotator_A \
                            --b-dir path/to/annotator_B \
                            --out   path/to/output \
                            --guide path/to/guidelines.md
```

The **Project** button opens the preflight screen: how many documents paired, which field each
offset was read from, every role found in the data, and anything worth checking. You can point
the running server at a different project from there — if that folder is not a usable project,
the one you have open is left exactly as it was.

Requires Python 3.9 or newer — the version macOS ships, so most people already have it. The
test suite runs on 3.11; 3.9 is checked by compiling and importing every module and running the
demo, `check` and `serve` end to end. That is the whole installation — `adjudicate.py` runs the tool straight
from a clone. If you prefer it installed, `pip install -e .` also gives you `python3 -m
adjudicator ...` and an `adjudicator` console script.

---

## Why the shape of a disagreement matters

Two annotators can disagree in ways that need completely different things from a reviewer. A
tool that shows every disagreement as the same list of spans forces the reviewer to work out what
kind of problem they are even looking at before they can think about the answer.

Every conflict is classified, named in plain language, and presented accordingly:

| Shape | What happened | How it is shown |
|---|---|---|
| `ROLE_CLASH` | Same text, different role | The passage once, in both annotators' colours, with the two roles beside it |
| `BOUNDARY_SHIFT` | Same role, different extent | The two extents, side by side |
| `SEGMENTATION` | One span against several | Aligned columns; the reviewer sees the cut points |
| `NESTING_DIFF` | One marked internal structure, the other did not | Indented by nesting depth |
| `A_ONLY` / `B_ONLY` | One annotator marked nothing | One panel, and an explicit "nothing marked here" |
| `MIXED` | Roles and boundaries both differ | Full structural comparison |

Agreements are carried over automatically and never queued.

## The rule the tool exists to enforce

**A disagreement is never silently merged.** If A calls a passage `FACTS` and B calls it `ISSUE`,
the resolved layer may carry A's answer, or B's, or *both roles if the reviewer explicitly judges
the passage to perform both* — but it must never end up with both merely because two people
disagreed.

This is enforced by making provenance a checked fact rather than a guess. Every resolved span
declares its `origin` (`A`, `B`, or `reviewer`), and the server **verifies the claim against the
source annotation** before storing it. A span cannot say it came from annotator A unless A
actually wrote it. The decision type is then read off the verified provenance, not inferred from
the shape of the result.

The rule is deliberately narrow. What it forbids is **A's role and B's role on one extent with
nobody having decided it** — so `Keep both` is withheld, with a tooltip explaining why, on any
conflict where it would produce that, and the editor refuses to save a span picked from A
together with a span picked from B on the same characters when the combination matches neither
annotator. Everything else is somebody's judgement and goes through: adding a role of your own
to a span you kept from A is an ordinary edit, and a reviewer who genuinely reads a passage as
carrying two roles writes it as a single span (or presses `Both roles`), recorded as their own
explicit multi-role decision. Two spans you draw on the same characters are one annotation, so
the editor merges them on save rather than sending them as unrelated claims.

The exported `origin` is *content* provenance: a span is attributed to A when it reproduces
something A wrote, whatever sequence of clicks produced it. The interface additionally tracks
*interaction* provenance (which annotator a draft was picked from) and uses it only for the
refusal above. The rule does not cover spans on *different* extents: `TAKE_BOTH` means the
reviewer deliberately retained spans from both annotators' structures — A's `FACTS[0,100)` beside
B's `ISSUE[0,99)`, or a coarse span beside the finer spans that segment it. It never means that A
and B agreed, and the summary reports such a result as differing from both annotators.

## Working through a document

| Key | Action |
|---|---|
| `1` `2` | Take A / Take B |
| `3` | Keep both — when the annotators marked genuinely different passages |
| `4` | Both roles — your own judgement that the passage performs several at once |
| `E` | Edit: adjust boundaries, change roles, add or remove spans |
| `D` `S` | Drop / Defer |
| `C` | Show the document around this conflict |
| `N` `P` | Next / previous |

The conflict queue stays the primary workflow. Pressing `C` opens the surrounding document
underneath the current conflict, with this conflict highlighted, **without moving the queue**;
from there the window can be widened or the whole document opened. Hundreds of annotations are
never rendered over the whole document as a default view.

## What is detected, not assumed

On startup the tool reports the field mapping it inferred, the annotation layers present, the
role inventory it found in the data, roles used by only one annotator, unpaired documents, and
where the document text came from (both files, or one annotator's when the other's carry none). Roles are read from the corpus; there is no built-in inventory, so a role that
neither annotator used anywhere in the layer cannot be assigned by the reviewer.

It **refuses to start** when character offsets cannot be trusted:

- the two annotators contain different document text
- either annotator's stored `text_sha256` disagrees with that text
- a span runs past the end of the document
- a document id appears twice in one directory
- the requested `--layer` does not exist in the corpus
- a file cannot be parsed, or a span cannot be read

And it **refuses to reuse a decision** the source no longer supports: a decision log is bound to
the text it was recorded against and is never re-stamped for a different one, and a decision
whose A/B spans have since changed is void — the conflict is queued again, marked "source
changed", and the void decision reaches no export and no statistic other than a `stale` count.
Exact agreements are derived from the annotations whenever they are needed rather than written
when a document happens to be opened, so `export` and `summary` give the same answer whether or
not anyone browsed the corpus in the interface.

## Output

Two products, and the first is never sacrificed for the second.

- `<out>/export/canonical/<doc>.json` — **lossless**. Every resolved span with its full decision
  provenance: origin, decision type, conflict shape, what both annotators originally said, who
  decided, when, and any note. This is the archival record.
- `<out>/export/roundtrip/<doc>.json` — **optional mirror** of the input schema, so existing
  readers keep working. Other layers and all original fields are copied through untouched.
  Anything the input schema cannot represent stays in the canonical export rather than being
  dropped to make it fit.

- `<out>/export/spans.tsv` — **one row per resolved span**, for a spreadsheet or a statistics
  package. Offsets, role, origin, decision type and conflict shape; never any document text.

`<out>/decisions/` holds the working log, one file per document and layer, written atomically.

In the interface, **Summary** in the top bar shows the same report as charts: progress, where
the final annotation came from (equals A, equals B, both, neither), conflict types with the
median time each took, decision types, time and revisions, and the two agreement measures,
and it names the folders where the logs and exports are. It reads *Provisional* until every
conflict is decided and *Complete* from then on; the button lights up at that moment.

```bash
python3 adjudicate.py check   --project PROJ                  # validate, don't start
python3 adjudicate.py export  --project PROJ --adjudicator C  # canonical + roundtrip + spans.tsv
python3 adjudicate.py summary --project PROJ --json report.json
```

`summary` is the command that turns the decision logs into the numbers a paper reports: how many
conflicts of each shape, how often the final annotation equalled A, equalled B, or differed from
both, how many introduced a new boundary or a new structure, median time per conflict and per
shape, revisions, and how many documents still validate clean. Every proportion is computed by
comparing the stored result against both sources — never from the button the reviewer pressed —
and deferred conflicts are reported on their own rather than counted as outcomes.

## Guidelines panel

Point `--guide` at any Markdown file. It is re-read from disk every time the panel opens, so the
guidelines can be edited during a review session and the change appears on the next open.

## Repository layout

```
src/adjudicator/
  model.py         spans and their geometry            (no I/O, no formats)
  conflicts.py     grouping and shape classification   (no I/O, no formats)
  decisions.py     resolutions, provenance, validation (no I/O, no formats)
  progress.py      one definition of "complete"
  adapters/        everything format-specific lives here
  project.py       loading, pairing, checks, detection
  store.py         atomic, lock-protected decision logs, bound to the document text
  export.py        canonical and round-trip output
  evaluation.py    per-decision comparison with both annotators
  agreement.py     span F1 and role kappa, from the raw annotations
  summary.py       corpus statistics
  server.py        stdlib HTTP server
  cli.py           check / serve / export / summary
adjudicate.py      launcher, so a fresh clone runs with nothing installed
web/               interface: ES modules, no build step
tests/             280 tests (60 of them drive a real browser)
docs/              input formats and output schema
examples/          the sample project: four synthetic cases, doubly annotated
```

The three core modules know nothing about files, field names or label inventories, so the
adjudication logic can be tested and reused independently of any corpus.

## Handing it to someone else

Zip the directory and send it. There is nothing to install and nothing to configure:

```bash
unzip SpanArbiter-0.2.1.zip && cd spanarbiter
python3 adjudicate.py serve --project examples/sample-project
```

Four sample cases travel inside the release — **two English and two Arabic** — doubly annotated,
with their own `guidelines.md`. So the first command someone runs shows the tool working on
disagreements worth thinking about, with no corpus of their own.

They are synthetic and written by hand, and every disagreement in them is one that two careful
annotators could reasonably reach: one factual narrative against three events, a citation with or
without the rule it states, a citation nested inside the reasoning or not, the relief sought read
as fact or as argument, a sentence that turns from a party's argument into the court's finding, a
passing remark one annotator skipped, and a sentence neither annotator labelled quite right. The
guidelines say how to settle each, and the answers need different actions: take a side, keep
both, both roles, or edit. Every file says in a `demonstrates` field what it is there to show.

That command prints a `http://127.0.0.1:8000/` address and opens it. Ctrl+C stops it. The zip is
about 170 KB; it carries no real case data and no dependencies, and the server binds the loopback
interface only.

To review their own data instead of the demo, they point `--project` at a folder holding
`annotator_A/` and `annotator_B/` — see [docs/INPUT_FORMATS.md](docs/INPUT_FORMATS.md) for the
three formats that are read.

## Tests

```bash
pip install -e ".[test]" && playwright install chromium
python3 -m pytest                      # 280 tests
python3 -m pytest -m "not ui"          # skip the 60 browser tests
```

## Accessibility and interface notes

Colour encodes **annotator identity only** (A / B / agreed) — never the role, because a corpus
can have dozens of roles and no palette distinguishes dozens of hues accessibly. Roles are always
text. The three identity colours were checked with an all-pairs colour-vision validator in both
light and dark; the reviewer's own draft is separated by layout rather than a fourth hue, because
no fourth hue cleared the separation floors in both modes.

Structural conflicts carry a mini-diagram of the two readings drawn to scale, and the editor
shows the structure about to be saved against both of them. Both are labelled for a screen
reader, and every fact they show is also present as text.

Text direction is detected per document, so right-to-left corpora render correctly without
configuration — including the diagram, which runs the same way as the passage it describes; the
two Arabic cases in the sample project show it. The interface is keyboard-operable throughout, honours `prefers-reduced-motion`
and `prefers-contrast`, and the DOM is built with `createElement`/`textContent` rather than HTML
strings — a role called `HE SAID "X"` cannot break the markup.

## Privacy

Everything is local. The server binds `127.0.0.1`. Request paths are never logged, because they
contain document ids. Source annotation files are opened read-only. No document text is written
to any log.

MIT licensed — see `LICENSE`.
