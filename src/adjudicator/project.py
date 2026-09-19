"""A project: two annotator directories, one output directory, and everything derived from them.

Loading is strict on purpose. Anything that would make character offsets untrustworthy - a text
that differs between annotators, a stored checksum that disagrees with the text, an offset past
the end of the document - is a BLOCKING problem, reported by name, and the tool refuses to open
the project. Silently adjudicating against the wrong text produces a corrupt gold standard that
nobody discovers until much later.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field

from .adapters.base import Document
from .adapters import detect_format, load_any
from .adapters.json_dir import DEFAULT_LAYER, FormatError, detect_mapping, read_records
from .conflicts import Conflict, build_conflicts
from .model import Span


PROJECT_LAYOUT = {
    "a_dir": ("annotator_A", "annotator_a", "A", "a"),
    "b_dir": ("annotator_B", "annotator_b", "B", "b"),
}
GUIDE_NAMES = ("guidelines.md", "GUIDELINES.md", "guidelines.MD",
               os.path.join("docs", "GUIDELINES.md"))
CONFIG_NAME = "config.json"


def load_project_config(root: str) -> dict:
    """`<project>/config.json`, when there is one: `layer` and `field_mapping` overrides.

    Malformed JSON is an error rather than a silent fall-back to detection, because a mapping
    the reviewer wrote and believes is in force must not be quietly ignored.
    """
    path = os.path.join(root, CONFIG_NAME)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            config = json.load(fh)
        except ValueError as exc:
            raise FileNotFoundError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(config, dict):
        raise FileNotFoundError(f"{path} must hold a JSON object")
    return config


def resolve_project_layout(root: str, a_dir: str | None = None, b_dir: str | None = None,
                           out: str | None = None, guide: str | None = None) -> dict:
    """Work out the four paths from a project directory, so nobody has to type them.

    An explicitly given path always wins, so a conventional layout can still be overridden one
    piece at a time. A `config.json` beside the annotator directories is read too.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"{root} is not a directory")

    resolved = {"root": root, "config": load_project_config(root)}
    for role, names in PROJECT_LAYOUT.items():
        explicit = a_dir if role == "a_dir" else b_dir
        if explicit:
            resolved[role] = explicit
            continue
        found = next((os.path.join(root, n) for n in names
                      if os.path.isdir(os.path.join(root, n))), None)
        if not found:
            raise FileNotFoundError(
                f"{root} does not look like a project directory: no {names[0]}/ inside it. "
                f"Expected {PROJECT_LAYOUT['a_dir'][0]}/ and {PROJECT_LAYOUT['b_dir'][0]}/, "
                f"or pass --a-dir and --b-dir explicitly.")
        resolved[role] = found

    resolved["out"] = out or os.path.join(root, "adjudication")
    resolved["guide"] = guide or next(
        (os.path.join(root, n) for n in GUIDE_NAMES if os.path.isfile(os.path.join(root, n))),
        None)
    return resolved


@dataclass
class Issue:
    severity: str          # "blocking" | "warning"
    kind: str
    message: str
    doc_id: str = ""

    def as_dict(self) -> dict:
        return {"severity": self.severity, "kind": self.kind,
                "message": self.message, "doc_id": self.doc_id}


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class PairedDoc:
    doc_id: str
    a: Document
    b: Document
    text: str
    text_source: str
    sentences: list[tuple[int, int]]


def _shown(mapping: dict) -> dict:
    """The mapping as the reviewer should read it. The live mapping is left alone: it is what the
    round-trip export writes back, and it must keep the corpus's own field names."""
    shown = dict(mapping)
    if not shown.get("doc_id"):
        shown["doc_id"] = "(the file name)"
    return shown


def _covered(spans, length: int) -> float:
    """Share of the document covered by these spans, counting overlaps once."""
    if not spans or length <= 0:
        return 0.0
    total = 0
    reached = 0
    for span in sorted(spans, key=lambda s: s.begin):
        begin, end = max(0, span.begin), min(length, span.end)
        if end <= reached:
            continue
        total += end - max(begin, reached)
        reached = end
    return total / length


class Project:
    """Loads a pair of annotator directories and answers everything the server needs."""

    def __init__(self, a_dir: str, b_dir: str, out_dir: str,
                 layer: str | None = None, config: dict | None = None) -> None:
        self.a_dir, self.b_dir, self.out_dir = a_dir, b_dir, out_dir
        self.config = config or {}
        self.requested_layer = layer or self.config.get("layer")
        self.issues: list[Issue] = []
        self.mapping: dict = {}          # annotator A's, used for round-trip export
        self.mapping_a: dict = {}
        self.mapping_b: dict = {}
        self.mapping_notes: list[str] = []
        self.format_a = ""
        self.format_b = ""
        self.docs: dict[str, PairedDoc] = {}
        self.only_in_a: list[str] = []
        self.only_in_b: list[str] = []
        self.layers: Counter = Counter()
        self.layer: str | None = None
        self.labels: list[str] = []
        self.label_counts: dict[str, dict[str, int]] = {"A": {}, "B": {}}
        self._conflicts: dict[str, list[Conflict]] = {}
        self.load()

    # ------------------------------------------------------------------ loading

    def _blocking(self, kind: str, message: str, doc_id: str = "") -> None:
        self.issues.append(Issue("blocking", kind, message, doc_id))

    def _warn(self, kind: str, message: str, doc_id: str = "") -> None:
        self.issues.append(Issue("warning", kind, message, doc_id))

    def load(self) -> None:
        try:
            self.format_a = detect_format(self.a_dir)
            self.format_b = detect_format(self.b_dir)
            if self.format_a != self.format_b:
                self._warn("format",
                           f"annotator A is {self.format_a} and annotator B is {self.format_b}. "
                           f"That is allowed, but both sides must describe the same document text "
                           f"for their offsets to be comparable.")
            # Only record formats carry field names to detect. brat and column files have a
            # fixed shape, and their document id is the file name by construction.
            recs_a = read_records(self.a_dir)[0] if self.format_a == "json" else []
            recs_b = read_records(self.b_dir)[0] if self.format_b == "json" else []
            # Each annotator is inferred SEPARATELY. One mapping guessed from both and applied to
            # both reads whichever side matched and sees nothing in the other - which then looks
            # exactly like an annotator who marked nothing.
            override = self.config.get("field_mapping") or {}
            override_a = override.get("A", override.get("a", override))
            override_b = override.get("B", override.get("b", override))
            def infer(kind, records, override, side):
                if kind != "json":
                    # Nothing to infer: brat and column files have one shape, and the document
                    # id is the file name. Say so where the reviewer reads the field mapping.
                    return {}, [f"read as {kind}; document id is the file name"]
                return detect_mapping([r for _, r in records[:60]], override)

            self.mapping_a, notes_a = infer(self.format_a, recs_a, override_a, "A")
            self.mapping_b, notes_b = infer(self.format_b, recs_b, override_b, "B")
            self.mapping = self.mapping_a
            self.mapping_notes = ([f"A · {n}" for n in notes_a]
                                  + [f"B · {n}" for n in notes_b])
            a_docs, a_problems = load_any(self.a_dir, self.mapping_a)
            b_docs, b_problems = load_any(self.b_dir, self.mapping_b)
        except FormatError as exc:
            self._blocking("format", str(exc))
            return

        for side, problems in (("A", a_problems), ("B", b_problems)):
            for p in problems:
                self._blocking("unreadable", f"annotator {side}, {p['file']}: {p['error']}")
        for side, docs in (("A", a_docs), ("B", b_docs)):
            for doc in docs.values():
                for issue in doc.issues:
                    self._blocking("bad_span", f"annotator {side}, {doc.source_file}: {issue}",
                                   doc.doc_id)
                for note in doc.notes:
                    # something the adapter accepted but interpreted; the reviewer must see it
                    self._warn("input_repaired", f"annotator {side}, {doc.source_file}: {note}",
                               doc.doc_id)

        # An annotator whose files parsed but yielded nothing is almost always a mapping that
        # did not fit, not a person who annotated nothing. Say so instead of showing an empty side.
        for side, docs, mapping in (("A", a_docs, self.mapping_a), ("B", b_docs, self.mapping_b)):
            other = b_docs if side == "A" else a_docs
            if docs and not any(d.spans for d in docs.values()) \
                    and any(d.spans for d in other.values()):
                self._blocking(
                    "empty_side",
                    f"annotator {side}: {len(docs)} file(s) were read but not one annotation was "
                    f"recognised, while the other annotator has plenty. The field mapping chosen "
                    f"for {side} ({ {k: v for k, v in mapping.items() if v} }) probably does not "
                    f"match these files. Set field_mapping.{side} in config.json.")
        if self.blocking:
            return

        shared = sorted(set(a_docs) & set(b_docs))
        self.only_in_a = sorted(set(a_docs) - set(b_docs))
        self.only_in_b = sorted(set(b_docs) - set(a_docs))
        if not shared:
            self._blocking("pairing", "no document id appears in both annotator directories")
            return
        for doc_id in self.only_in_a:
            self._warn("unpaired", f"{doc_id} exists only in annotator A", doc_id)
        for doc_id in self.only_in_b:
            self._warn("unpaired", f"{doc_id} exists only in annotator B", doc_id)

        for doc_id in shared:
            a, b = a_docs[doc_id], b_docs[doc_id]
            text, source = self._resolve_text(doc_id, a, b)
            if text is None:
                continue
            digest = sha256_of(text)
            for side, doc in (("A", a), ("B", b)):
                if doc.text_sha256 and doc.text_sha256 != digest:
                    self._blocking(
                        "checksum",
                        f"annotator {side}'s stored text_sha256 disagrees with the document text "
                        f"the offsets are measured against "
                        f"({doc.text_sha256[:12]}... vs {digest[:12]}...)", doc_id)
            self.docs[doc_id] = PairedDoc(doc_id, a, b, text, source,
                                          a.sentences or b.sentences)

        for side, mapping, kind in (("A", self.mapping_a, self.format_a),
                                    ("B", self.mapping_b, self.format_b)):
            if kind != "json":
                continue                      # the file name IS the document id in these formats
            if not mapping.get("doc_id"):
                self._warn("doc_id_fallback",
                           f"annotator {side} has no document id field, so the file name is used "
                           f"instead. Documents pair only if both annotators name their files "
                           f"identically; set field_mapping.{side}.doc_id in config.json if they "
                           f"carry an id under a name this tool did not recognise.")

        self._detect_layers()
        self._check_offsets()
        self._check_double_annotation()

    def _resolve_text(self, doc_id: str, a: Document, b: Document) -> tuple[str | None, str]:
        if a.text is not None and b.text is not None:
            if a.text != b.text:
                self._blocking("text_mismatch",
                               "annotators A and B contain different document text, so character "
                               "offsets cannot be compared", doc_id)
                return None, ""
            return a.text, "A and B (identical)"
        if a.text is not None:
            return a.text, "annotator A"
        if b.text is not None:
            return b.text, "annotator B"
        self._blocking("no_text", "neither annotator contains the document text", doc_id)
        return None, ""

    def _check_double_annotation(self) -> None:
        """Catch a document that is not really doubly annotated.

        When one annotator barely touched a document, every span the other wrote arrives as an
        ordinary A_ONLY / B_ONLY conflict. Adjudicating those means the reviewer quietly doing the
        missing annotator's work, while the log records each one as the reviewer overriding an
        annotator who was never there — which then flows into the agreement figures. The tool
        cannot fix the corpus, but it must not present the gap as a set of disagreements.
        """
        lonely: list[tuple[str, float, float]] = []
        for doc_id, paired in self.docs.items():
            length = len(paired.text)
            if not length:
                continue
            a = _covered(paired.a.spans, length)
            b = _covered(paired.b.spans, length)
            low, high = min(a, b), max(a, b)
            if high - low > 0.25 and low < 0.5 * high:
                lonely.append((doc_id, a, b))

        for doc_id, a, b in lonely[:10]:
            thin = "A" if a < b else "B"
            self._warn("one_sided",
                       f"{doc_id} looks annotated by only one person: A covers {100 * a:.0f}% of "
                       f"the text, B covers {100 * b:.0f}%. Annotator {thin}'s side may be "
                       f"unfinished, and every span the other wrote will arrive here as an "
                       f"ordinary disagreement.", doc_id)
        if len(lonely) > 10:
            self._warn("one_sided",
                       f"... and {len(lonely) - 10} further document(s) where one annotator covers "
                       f"far less of the text than the other. Consider settling those before "
                       f"adjudicating: they are gaps, not disagreements.")

    def _detect_layers(self) -> None:
        for paired in self.docs.values():
            for doc in (paired.a, paired.b):
                for span in doc.spans:
                    self.layers[span.layer] += 1
        if not self.layers:
            self._blocking("no_spans", "neither annotator contains a single annotation")
            return
        if self.requested_layer:
            if self.requested_layer not in self.layers:
                self._blocking("unknown_layer",
                               f"layer {self.requested_layer!r} does not exist in this corpus; "
                               f"available layers: {sorted(self.layers)}")
                return
            self.layer = self.requested_layer
        elif len(self.layers) == 1:
            self.layer = next(iter(self.layers))
        else:
            self.layer = self.layers.most_common(1)[0][0]
            self._warn("layer_choice",
                       f"this corpus has several layers {sorted(self.layers)}; showing "
                       f"{self.layer!r}. Switch layers in the interface or pass --layer.")
        self._detect_labels()

    def _detect_labels(self) -> None:
        """Roles are read off the data, per layer. There is no built-in inventory."""
        counts = {"A": Counter(), "B": Counter()}
        for paired in self.docs.values():
            for side, doc in (("A", paired.a), ("B", paired.b)):
                for span in doc.spans_in(self.layer or ""):
                    counts[side][span.label] += 1
        self.label_counts = {k: dict(v) for k, v in counts.items()}
        self.labels = sorted(set(counts["A"]) | set(counts["B"]))
        only_a = sorted(set(counts["A"]) - set(counts["B"]))
        only_b = sorted(set(counts["B"]) - set(counts["A"]))
        if only_a:
            self._warn("label_coverage", f"roles used only by annotator A: {only_a}")
        if only_b:
            self._warn("label_coverage", f"roles used only by annotator B: {only_b}")

    def _check_offsets(self) -> None:
        for paired in self.docs.values():
            limit = len(paired.text)
            for side, doc in (("A", paired.a), ("B", paired.b)):
                for span in doc.spans:
                    if span.end > limit:
                        self._blocking(
                            "offset", f"annotator {side} has {span.label} at "
                                      f"[{span.begin},{span.end}) but the document is only "
                                      f"{limit} characters", paired.doc_id)

    # ------------------------------------------------------------------ queries

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "blocking"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def usable(self) -> bool:
        return not self.blocking and bool(self.docs) and self.layer is not None

    @property
    def doc_ids(self) -> list[str]:
        return sorted(self.docs)

    def set_layer(self, layer: str) -> None:
        if layer not in self.layers:
            raise FormatError(f"unknown layer {layer!r}; available: {sorted(self.layers)}")
        self.layer = layer
        self._conflicts.clear()
        self._detect_labels()

    def conflicts(self, doc_id: str) -> list[Conflict]:
        key = f"{doc_id}::{self.layer}"
        if key not in self._conflicts:
            paired = self.docs[doc_id]
            self._conflicts[key] = build_conflicts(
                doc_id, self.layer or DEFAULT_LAYER,
                paired.a.spans_in(self.layer or ""), paired.b.spans_in(self.layer or ""))
        return self._conflicts[key]

    def preflight(self) -> dict:
        return {
            "a_dir": self.a_dir, "b_dir": self.b_dir, "out_dir": self.out_dir,
            "documents_a": len(self.docs) + len(self.only_in_a),
            "documents_b": len(self.docs) + len(self.only_in_b),
            "paired": len(self.docs),
            "only_in_a": self.only_in_a, "only_in_b": self.only_in_b,
            "format_a": self.format_a, "format_b": self.format_b,
            "mapping": _shown(self.mapping), "mapping_a": _shown(self.mapping_a),
            "mapping_b": _shown(self.mapping_b), "mapping_notes": self.mapping_notes,
            "layers": dict(self.layers), "layer": self.layer,
            "labels": self.labels, "label_counts": self.label_counts,
            "text_source": next(iter(self.docs.values())).text_source if self.docs else None,
            "blocking": [i.as_dict() for i in self.blocking],
            "warnings": [i.as_dict() for i in self.warnings],
            "usable": self.usable,
        }
