"""Input adapters. Each turns some on-disk format into Document objects.

The format is decided by what is in the directory, and the decision is reported to the reviewer
rather than assumed. A directory holding two formats is refused: half-reading a corpus is how a
tool ends up comparing one annotator's spans against nothing.
"""
from __future__ import annotations

import glob
import os

from . import brat, conll, json_dir
from .base import Document
from .json_dir import FormatError

JSON_EXTENSIONS = (".json", ".jsonl")
FORMATS = ("json", "brat", "conll")


def _present(directory: str) -> set[str]:
    found = set()
    for extension in JSON_EXTENSIONS:
        if glob.glob(os.path.join(directory, f"*{extension}")):
            found.add("json")
    if glob.glob(os.path.join(directory, "*.ann")):
        found.add("brat")
    for extension in conll.EXTENSIONS:
        if glob.glob(os.path.join(directory, f"*{extension}")):
            found.add("conll")
    return found


def detect_format(directory: str) -> str:
    """Which adapter this directory needs. Raises FormatError rather than guessing."""
    if not os.path.isdir(directory):
        raise FormatError(f"not a directory: {directory}")
    found = _present(directory)
    if len(found) > 1:
        raise FormatError(
            f"{directory} holds more than one annotation format ({', '.join(sorted(found))}). "
            f"Keep one format per annotator directory, so it is never ambiguous which files "
            f"carry the annotations.")
    if not found:
        raise FormatError(
            f"no annotation files in {directory}: looked for .json / .jsonl (records), "
            f".ann with a matching .txt (brat), and "
            f"{' / '.join(conll.EXTENSIONS)} (column files with BIO tags)")
    return found.pop()


def load_any(directory: str, mapping: dict) -> tuple[dict[str, Document], list[dict]]:
    """Load a directory in whatever format it is. `mapping` applies to JSON records only."""
    kind = detect_format(directory)
    if kind == "json":
        return json_dir.load_directory(directory, mapping)
    if kind == "brat":
        return brat.read_directory(directory)
    return conll.read_directory(directory)
