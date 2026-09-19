# Input formats

A project is two directories, one per annotator. Three formats are read, and which one a
directory is in is **detected from its contents and reported**, never assumed. A directory
holding two formats is refused rather than half-read.

| Format | Recognised by | Document id | Text comes from |
|---|---|---|---|
| JSON records | `.json`, `.jsonl` | a detected field, else the file name | a field in the record |
| brat standoff | `.ann` beside a matching `.txt` | the file name | the `.txt` |
| column / BIO | `.conll`, `.iob`, `.iob2`, `.bio`, `.tsv`, `.col` | the file name | rebuilt from the tokens |

Field names inside JSON records are detected and reported too.

## brat

Only text-bound annotations (`T` lines) are spans. Relations, events, attributes, equivalences
and notes are read past without complaint.

```
T1	COURT 4 9	court
T2	PARTY 38 47	plaintiff
```

Two checks are worth knowing about, because both catch real mistakes:

- The third column is brat's own copy of the covered text, with line breaks written as spaces.
  If it disagrees with the offsets, the document is refused — one of the two files has been
  edited since, and every offset in it is suspect. (Both times this document's own examples were
  written by hand, this check caught the miscount.)
- A **discontinuous** annotation (`4 9;38 47`) is refused rather than flattened. It is two pieces
  of text under one label; taking the enclosing range would invent an annotation covering text
  nobody marked.

## Column files with BIO tags

One token per line, blank line between sentences; the token is the first column and the tag is
the last. `B-`/`I-`/`O`, `BIOES` and a bare label column are all read. An `I-` with no open span
of that role before it starts a span rather than being dropped, and every such repair is listed
in the preflight as a warning, so the reviewer can see where the source tags were not well
formed. CoNLL-U (`.conllu`) is **not** accepted: its columns are id, form, …, misc, and reading
it as "first column token, last column tag" would silently take the wrong ones.

```
The	O
court	B-COURT
claim	B-CLAIM
of	I-CLAIM
```

**The document text is reconstructed from the tokens**, because a column file does not carry it.
That has a consequence: two annotators who tokenised differently have produced different
documents, and their character offsets are not comparable. The tool does not paper over it — the
reconstructed texts differ and the project refuses to pair them.

## Minimum

Each record needs a span list; each span needs a start, an end and a label. The document text
must be available from at least one of the two annotators.

```json
{"canonical_case_id": "C0001",
 "text": "The court considered the matter…",
 "text_sha256": "e12b33fb…",
 "spans": [{"kind": "rhetorical", "label": "FACTS", "begin": 347, "end": 699}]}
```

## Field detection

| Role | Names tried, in order |
|---|---|
| document id | `canonical_case_id`, `document_id`, `doc_id`, `case_id`, `id`, `name` |
| text | `text`, `content`, `body`, `document`, `raw_text`, `full_text` |
| span list | `spans`, `annotations`, `entities`, `mentions`, `markables`, `labels` |
| start | `begin`, `start`, `start_offset`, `char_start`, `offset_start`, `from`, `b` |
| end | `end`, `stop`, `end_offset`, `char_end`, `offset_end`, `to`, `e` |
| label | `label`, `tag`, `role`, `category`, `class`, `value`, `type` |
| layer | `kind`, `layer`, `annotation_type`, `level`, `namespace` |
| sentences | `sentences_auto`, `sentences`, `sentence_offsets` |

Override any of these with a `config.json` beside `annotator_A/` and `annotator_B/` (read when
`--project` is used, or when a project is opened from the interface):

```json
{"layer": "rhetorical",
 "field_mapping": {"A": {"begin": "from_char", "end": "to_char", "label": "kind_of"},
                   "B": {"doc_id": "case"}}}
```

`field_mapping` may also be one flat mapping applied to both annotators. The same dictionary can
be passed directly as `Project(config=...)`.

## Accepted variations

- **Offsets may be integers or numeric strings.** `"begin": "347"` and `"begin": 347` both load.
  Anything else — a float with a fraction, a boolean, a word — is reported, not coerced.
- **Only one annotator needs the text.** A blind second annotator that carries only
  `text_sha256` and `spans` is the expected setup; the checksum is verified against the text the
  offsets are measured against.
- **No layer field** — every span is placed in one layer called `default`.
- **Several layers** — the tool reports them and lets the reviewer switch; `--layer` picks one
  up front.

## Offsets

Character offsets into the document text, half-open: `[begin, end)`. `begin` must be less than
`end`. Nesting, crossing overlap, and several roles on one identical extent are all permitted.

## What blocks loading

Both annotators containing different text; a stored `text_sha256` that disagrees with the text;
an offset past the end of the document; a duplicate document id within one directory; a file
that cannot be parsed; a span that cannot be read; a `--layer` that does not exist.
