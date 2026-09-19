"""Durable storage for reviewer decisions.

Three properties matter more than speed here:

1. A write is atomic. An interrupted save can never leave a half-written decision log.
2. A log that exists but cannot be parsed is an ERROR, never an empty result. Reporting a corrupt
   log as "no decisions yet" invites a reviewer to redo work that is actually still on disk, or
   to overwrite it.
3. A log written against one version of the document text is never silently re-stamped as
   belonging to another. Every read-modify-write states which text it is working on, and a log
   that disagrees is refused before anything is changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading

from .decisions import Decision

SCHEMA_VERSION = "1.0"


class StoreError(Exception):
    """A decision log exists but cannot be read. Never silently treated as empty."""


class StaleTextError(StoreError):
    """The log was written against different document text, so its offsets mean something else."""


def _readable(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value)


class DecisionStore:
    def __init__(self, out_dir: str) -> None:
        self.out_dir = out_dir
        self.dir = os.path.join(out_dir, "decisions")
        self._lock = threading.RLock()
        os.makedirs(self.dir, exist_ok=True)

    def path_for(self, doc_id: str, layer: str) -> str:
        """One file per (document, layer), and never the same file for two different pairs.

        The readable part is for a person browsing the directory. The digest is what makes the
        name unique: sanitising alone maps `case/1`, `case?1` and `case:1` to one file, and a
        case-insensitive file system maps `Case1` and `case1` to one file, so one document's
        decisions could overwrite another's.
        """
        digest = hashlib.sha256(f"{doc_id}\x00{layer}".encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.dir, f"{_readable(f'{doc_id}__{layer}')[:80]}~{digest}.json")

    def _legacy_path(self, doc_id: str, layer: str) -> str:
        """Where logs written before the digest was added would be."""
        return os.path.join(self.dir, _readable(f"{doc_id}__{layer}") + ".json")

    def _adopt_legacy(self, doc_id: str, layer: str, path: str) -> None:
        """Move a pre-digest log to its new name, but only if it really is this document's:
        the old naming let two documents share a file, so the payload has to say whose it is."""
        legacy = self._legacy_path(doc_id, layer)
        if not os.path.exists(legacy):
            return
        try:
            with open(legacy, encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            raise StoreError(
                f"{legacy} cannot be read ({type(exc).__name__}: {exc}). Refusing to continue: "
                f"a damaged decision log must never be reported as zero decisions.") from exc
        if isinstance(payload, dict) and payload.get("doc_id") == doc_id \
                and payload.get("layer") == layer:
            os.replace(legacy, path)

    def load(self, doc_id: str, layer: str,
             expect_text_sha256: str | None = None) -> dict[str, Decision]:
        """`expect_text_sha256` is the hash of the text the caller is about to apply these
        decisions to. When it disagrees with the hash the log was written against, the stored
        offsets refer to a different document and the log must not be used."""
        path = self.path_for(doc_id, layer)
        with self._lock:
            if not os.path.exists(path):
                self._adopt_legacy(doc_id, layer, path)
            if not os.path.exists(path):
                return {}
            try:
                with open(path, encoding="utf-8") as fh:
                    payload = json.load(fh)
                decisions = {d["conflict_id"]: Decision.from_dict(d) for d in payload["decisions"]}
            except Exception as exc:
                raise StoreError(
                    f"{path} cannot be read ({type(exc).__name__}: {exc}). Refusing to continue: "
                    f"a damaged decision log must never be reported as zero decisions.") from exc
            written_against = payload.get("text_sha256")
            if expect_text_sha256 and written_against and written_against != expect_text_sha256:
                raise StaleTextError(
                    f"{path} was recorded against different document text "
                    f"({written_against[:12]}... vs {expect_text_sha256[:12]}...). Its character "
                    f"offsets refer to another version of this document, so the decisions cannot "
                    f"be applied. Re-adjudication is required.")
            return decisions

    def save(self, doc_id: str, layer: str, decisions: dict[str, Decision],
             text_sha256: str = "") -> None:
        path = self.path_for(doc_id, layer)
        payload = {
            "schema_version": SCHEMA_VERSION, "doc_id": doc_id, "layer": layer,
            "text_sha256": text_sha256,
            "decisions": [decisions[k].as_dict() for k in sorted(decisions)],
        }
        with self._lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)

    def update(self, doc_id: str, layer: str, mutate, text_sha256: str = "") -> dict[str, Decision]:
        """Read, mutate and write back while holding the lock the whole time.

        Every caller that changes the log must go through here. A read-modify-write that releases
        the lock in between can silently drop a decision saved by a concurrent request.

        The read is checked against `text_sha256`, the text the caller is working on. Without
        that check a log recorded against an older version of the document would be written back
        stamped with the new hash, and the protection against applying old offsets to new text
        would be gone after a single read-modify-write.
        """
        with self._lock:
            current = self.load(doc_id, layer, expect_text_sha256=text_sha256 or None)
            mutate(current)
            self.save(doc_id, layer, current, text_sha256)
            return current
