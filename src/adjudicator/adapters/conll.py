"""Column files with BIO-style tags: one token per line, blank line between sentences.

The document text is RECONSTRUCTED from the tokens, because a column file does not carry it.
That has a consequence worth stating plainly: two annotators who tokenised differently produce
different documents, and their character offsets are then not comparable. The tool does not paper
over that — the reconstructed texts differ, and the project refuses to pair them.

The tag is read from the LAST column and the token from the first. That is the shape of
CoNLL-2003-style files, `.iob` files and plain two-column exports. It is NOT the shape of
CoNLL-U (`.conllu`: an id column first, the form second, and `MISC` last), which is therefore not
accepted: reading it with this parser would silently take the wrong columns.
"""
from __future__ import annotations

import glob
import os

from ..model import Span
from .base import Document

DEFAULT_LAYER = "default"
EXTENSIONS = (".conll", ".iob", ".iob2", ".bio", ".tsv", ".col")
OPENERS = ("B", "S", "U")          # begin, singleton
CLOSERS = ("E", "L", "S", "U")     # end, last, singleton


def _split_tag(tag: str) -> tuple[str, str | None]:
    """('B-COURT') -> ('B', 'COURT');  ('O') -> ('O', None);  a bare label -> ('B', label)."""
    if not tag or tag == "O" or tag == "_":
        return "O", None
    if len(tag) > 1 and tag[1] in "-." and tag[0] in "BIOESLU":
        return tag[0], tag[2:] or None
    return "B", tag                                   # a column of plain labels, no BIO prefix


def _tokens(body: str) -> list[tuple[str, str, int, int]]:
    """(token, tag, sentence index, line number). Comments and document markers are skipped."""
    out: list[tuple[str, str, int, int]] = []
    sentence = 0
    for lineno, line in enumerate(body.splitlines(), 1):
        if not line.strip():
            sentence += 1
            continue
        if line.startswith("#") or line.startswith("-DOCSTART-"):
            continue
        columns = line.split("\t") if "\t" in line else line.split()
        if len(columns) < 2:
            continue
        out.append((columns[0], columns[-1], sentence, lineno))
    return out


def parse(body: str) -> tuple[str, list[Span], list[tuple[int, int]], list[str], list[str]]:
    """Returns (text, spans, sentences, issues, notes). `notes` records every place the tags
    were not well-formed BIO and the parser made a choice, so that choice is visible to the
    reviewer instead of silently shaping the annotation."""
    tokens = _tokens(body)
    text_parts: list[str] = []
    placed: list[tuple[int, int, str, int, int]] = []      # begin, end, tag, sentence, line
    sentences: list[tuple[int, int]] = []
    cursor = 0
    current_sentence = None
    sentence_begin = 0

    for token, tag, sentence, lineno in tokens:
        if current_sentence is None:
            current_sentence, sentence_begin = sentence, cursor
        elif sentence != current_sentence:
            sentences.append((sentence_begin, cursor))
            text_parts.append("\n")
            cursor += 1
            current_sentence, sentence_begin = sentence, cursor
        elif text_parts:
            text_parts.append(" ")
            cursor += 1
        text_parts.append(token)
        placed.append((cursor, cursor + len(token), tag, sentence, lineno))
        cursor += len(token)
    if current_sentence is not None:
        sentences.append((sentence_begin, cursor))

    spans: list[Span] = []
    issues: list[str] = []
    notes: list[str] = []
    open_span: dict | None = None

    def close():
        nonlocal open_span
        if open_span:
            try:
                spans.append(Span(open_span["begin"], open_span["end"],
                                  open_span["label"], DEFAULT_LAYER))
            except (TypeError, ValueError) as exc:
                issues.append(str(exc))
        open_span = None

    for begin, end, tag, sentence, lineno in placed:
        prefix, label = _split_tag(tag)
        if label is None:
            close()
            continue
        continues = (open_span is not None and open_span["label"] == label
                     and open_span["sentence"] == sentence)
        fresh = prefix in OPENERS or not continues
        if fresh and prefix not in OPENERS:
            # An inside/end tag with nothing to continue. It is read as the start of a span,
            # and said so: the alternative, dropping it, loses an annotation silently.
            notes.append(f"line {lineno}: {tag} continues no open {label} span "
                         f"({'a different role was open' if open_span else 'nothing was open'}); "
                         f"read as the start of a new {label} span")
        if fresh:
            close()
            open_span = {"begin": begin, "end": end, "label": label, "sentence": sentence}
        else:
            open_span["end"] = end
        if prefix in CLOSERS:
            close()
    close()
    return "".join(text_parts), spans, sentences, issues, notes


def read_directory(directory: str) -> tuple[dict[str, Document], list[dict]]:
    docs: dict[str, Document] = {}
    problems: list[dict] = []
    paths: list[str] = []
    for extension in EXTENSIONS:
        paths += glob.glob(os.path.join(directory, f"*{extension}"))
    for path in sorted(set(paths)):
        name = os.path.basename(path)
        doc_id = name.rsplit(".", 1)[0]
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            problems.append({"file": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        text, spans, sentences, issues, notes = parse(body)
        if len(notes) > 5:
            notes = notes[:5] + [f"... and {len(notes) - 5} more malformed BIO transitions "
                                 f"read the same way"]
        docs[doc_id] = Document(doc_id=doc_id, spans=spans, text=text, sentences=sentences,
                                source_file=name, raw={"format": "conll"}, issues=issues,
                                notes=notes)
    return docs, problems
