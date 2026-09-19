"""Local HTTP server. Python standard library only; binds the loopback interface.

Nothing leaves the machine: no external requests, no analytics, no CDN. Request paths are not
logged, because a path contains a document id and document ids are corpus metadata.
"""
from __future__ import annotations

import json
import math
import mimetypes
import os
import posixpath
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .adapters.json_dir import FormatError
from .conflicts import Conflict
from .decisions import (
    Decision, DecisionError, ResolvedSpan, decide, effective_decisions, source_fingerprint,
)
from .export import export_project
from .progress import document_progress
from .project import Project, resolve_project_layout, sha256_of
from .store import DecisionStore, StoreError

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "web")


def _finite_number(value, name: str, minimum: float = 0.0) -> float:
    """A timing or count from the client: a real, finite number, not below `minimum`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value) or value < minimum:
        raise DecisionError(f"{name} must be a finite number not below {minimum:g}, got {value!r}")
    return float(value)


class AppState:
    """Everything a request might need, behind one re-entrant lock.

    Reading a document never writes: automatic agreements are derived on the fly by
    `effective_decisions`, so the only writers are `apply_decision`, `set_layer` and
    `open_project`. The lock still serialises a save against a concurrent read of the same log,
    so a read never observes a half-applied change.
    """

    def __init__(self, project: Project, store: DecisionStore, guide_path: str | None) -> None:
        self.project = project
        self.store = store
        self.guide_path = guide_path
        self.lock = threading.RLock()

    def document_state(self, doc_id: str) -> dict:
        with self.lock:
            project, layer = self.project, self.project.layer or ""
            conflicts = project.conflicts(doc_id)
            paired = project.docs[doc_id]
            stored = self.store.load(doc_id, layer, expect_text_sha256=sha256_of(paired.text))
            decisions, stale = effective_decisions(conflicts, stored)
            return {
                "doc_id": doc_id, "layer": layer,
                "text": paired.text,
                "text_source": paired.text_source,
                "sentences": [list(s) for s in paired.sentences],
                "conflicts": [c.as_dict() for c in conflicts],
                "decisions": {k: v.as_dict() for k, v in decisions.items()},
                # conflicts whose stored decision no longer applies; they are queued again
                "stale": [s["conflict_id"] for s in stale],
                # from the stored log, so the stale count survives (the effective set has
                # already had the void decisions removed)
                "progress": document_progress(conflicts, stored),
            }

    def open_project(self, root: str, layer: str | None = None) -> dict:
        """Point the running server at another project directory.

        The replacement corpus is built first, outside the swap: if the directory is not a
        project, or the corpus has a blocking problem, the reviewer keeps working on what they
        already had open instead of landing in a half-loaded state.
        """
        layout = resolve_project_layout(root)          # FileNotFoundError -> 400
        candidate = Project(layout["a_dir"], layout["b_dir"], layout["out"], layer=layer,
                            config=layout.get("config"))
        if not candidate.usable:
            blocking = "; ".join(issue["message"] for issue in candidate.preflight()["blocking"][:3])
            raise DecisionError(f"that project cannot be reviewed safely: {blocking}")
        with self.lock:
            self.project = candidate
            self.store = DecisionStore(layout["out"])
            self.guide_path = layout.get("guide")      # never keep the old project's guidelines
            return {**candidate.preflight(), "documents": len(candidate.doc_ids),
                    "available_layers": sorted(candidate.layers)}

    def corpus_progress(self) -> list[dict]:
        with self.lock:
            layer = self.project.layer or ""
            out = []
            for doc_id in self.project.doc_ids:
                try:
                    conflicts = self.project.conflicts(doc_id)
                    stored = self.store.load(
                        doc_id, layer,
                        expect_text_sha256=sha256_of(self.project.docs[doc_id].text))
                    row = document_progress(conflicts, stored)
                    row.update(doc_id=doc_id, error=None)
                except StoreError as exc:
                    row = {"doc_id": doc_id, "error": str(exc), "conflicts": 0, "settled": 0,
                           "flagged": 0, "undecided": 0, "complete": False, "percent": 0.0,
                           "auto_agreed": 0, "dropped": 0, "stale": 0, "by_shape": {}}
                out.append(row)
            return out

    def apply_decision(self, body: dict) -> dict:
        with self.lock:
            doc_id = body.get("doc_id")
            if not isinstance(doc_id, str) or doc_id not in self.project.docs:
                raise DecisionError(f"unknown document {doc_id!r}")
            layer = self.project.layer or ""
            conflicts = {c.conflict_id: c for c in self.project.conflicts(doc_id)}
            conflict = conflicts.get(body.get("conflict_id"))
            if conflict is None:
                raise DecisionError(f"unknown conflict {body.get('conflict_id')!r}")
            if conflict.agreed:
                raise DecisionError("both annotators agree here; agreements are carried over "
                                    "automatically and are not adjudicated")
            paired = self.project.docs[doc_id]
            raw_spans = body.get("spans", [])
            if not isinstance(raw_spans, list):
                raise DecisionError("spans must be a list")
            spans = [ResolvedSpan.from_dict(s) if isinstance(s, dict)
                     else ResolvedSpan.from_dict({}) for s in raw_spans]
            intent = body.get("intent")
            if intent is not None and not isinstance(intent, str):
                raise DecisionError(f"intent must be DROP, DEFER or omitted, got {intent!r}")
            note = body.get("note", "")
            if not isinstance(note, str):
                raise DecisionError("note must be text")
            attempt = _finite_number(body.get("seconds", 0), "seconds")
            # The client counts visits, because only the client knows when a conflict became
            # the active target. The server only ever accumulates what it is told.
            visits_delta = int(_finite_number(body.get("visits_delta", 1), "visits_delta"))
            result: dict = {}

            def mutate(current: dict[str, Decision]) -> None:
                previous = current.get(conflict.conflict_id)
                if previous is not None and previous.fingerprint \
                        and previous.fingerprint != source_fingerprint(conflict):
                    previous = None          # void: a fresh attempt, not a revision of it
                current[conflict.conflict_id] = decide(
                    conflict, spans, intent=intent, note=note,
                    labels=self.project.labels, text_length=len(paired.text),
                    attempt_seconds=attempt,
                    cumulative_seconds=(previous.cumulative_seconds if previous else 0.0) + attempt,
                    visit_count=(previous.visit_count if previous else 0) + visits_delta,
                    revision_count=(previous.revision_count + 1) if previous else 0)
                result["decision"] = current[conflict.conflict_id].as_dict()

            stored = self.store.update(doc_id, layer, mutate, sha256_of(paired.text))
            result["progress"] = document_progress(list(conflicts.values()), stored)
            return result


class Handler(BaseHTTPRequestHandler):
    state: AppState = None            # injected by serve()
    server_version = "SpanArbiter"

    def log_message(self, *args) -> None:
        pass                          # never log request paths: they carry document ids

    # ------------------------------------------------------------- plumbing
    def _send(self, code: int, body: bytes | str, ctype: str = "application/json") -> None:
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False))

    def _internal_error(self, exc: Exception, ok_field: bool) -> None:
        """An exception nobody anticipated. The full traceback goes to the server's own console
        -- never logged to a file, since it can quote document text -- and the client gets only
        the exception's type name. The message text is withheld: unlike the type name, it can
        contain a file path, an offset, or a fragment of the corpus itself."""
        traceback.print_exc(file=sys.stderr)
        payload = {"error": f"internal error ({type(exc).__name__}); see the server's console",
                   "kind": "internal"}
        if ok_field:
            payload["ok"] = False
        self._json(500, payload)

    def _static(self, path: str) -> None:
        # Resolve symlinks on both sides and compare whole path components, so neither `..`
        # nor a sibling directory whose name merely starts with "web" can escape the web root.
        root = os.path.realpath(WEB_ROOT)
        rel = posixpath.normpath(path).lstrip("/")
        target = os.path.realpath(os.path.join(root, rel))
        if os.path.commonpath([root, target]) != root or not os.path.isfile(target):
            return self._json(404, {"error": "not found"})
        ctype, _ = mimetypes.guess_type(target)
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype or "application/octet-stream")

    # ------------------------------------------------------------- routes
    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                return self._static("index.html")
            if not url.path.startswith("/api/"):
                return self._static(url.path)

            if url.path == "/api/project":
                project = self.state.project
                return self._json(200, {
                    **project.preflight(),
                    "documents": len(project.doc_ids),
                    "available_layers": sorted(project.layers),
                })
            if url.path == "/api/documents":
                return self._json(200, {"documents": self.state.corpus_progress(),
                                        "layer": self.state.project.layer})
            if url.path == "/api/document":
                doc_id = (query.get("id") or [""])[0]
                if doc_id not in self.state.project.docs:
                    return self._json(404, {"error": f"unknown document {doc_id!r}"})
                return self._json(200, self.state.document_state(doc_id))
            if url.path == "/api/guide":
                path = self.state.guide_path
                text = None
                if path and os.path.isfile(path):
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()        # re-read every time, so edits appear on reload
                return self._json(200, {"path": path, "markdown": text})
            return self._json(404, {"error": "not found"})
        except StoreError as exc:
            self._json(409, {"error": str(exc), "kind": "corrupt_log"})
        except Exception as exc:
            self._internal_error(exc, ok_field=False)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) or b"{}"
            try:
                body = json.loads(raw)
            except ValueError:
                raise DecisionError("the request body is not valid JSON") from None
            if not isinstance(body, dict):
                raise DecisionError("the request body must be a JSON object")

            if url.path == "/api/decide":
                return self._json(200, {"ok": True, **self.state.apply_decision(body)})
            if url.path == "/api/project/open":
                root = str(body.get("root") or "").strip()
                if not root:
                    raise DecisionError("give the path of a project directory")
                return self._json(200, {"ok": True, **self.state.open_project(
                    root, body.get("layer"))})
            if url.path == "/api/layer":
                layer = body.get("layer")
                if not isinstance(layer, str) or not layer:
                    raise DecisionError("give the name of the layer to review")
                with self.state.lock:
                    self.state.project.set_layer(layer)
                    return self._json(200, {"ok": True, "layer": self.state.project.layer,
                                            "labels": self.state.project.labels})
            if url.path == "/api/export":
                with self.state.lock:
                    result = export_project(
                        self.state.project, self.state.store,
                        out_dir=os.path.join(self.state.project.out_dir, "export"),
                        roundtrip=bool(body.get("roundtrip", True)),
                        only_complete=bool(body.get("only_complete", True)))
                    return self._json(200, {"ok": True, **result.as_dict(),
                                            "directory": os.path.abspath(
                                                os.path.join(self.state.project.out_dir, "export"))})
            return self._json(404, {"error": "not found"})
        except (DecisionError, FormatError, FileNotFoundError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except StoreError as exc:
            self._json(409, {"ok": False, "error": str(exc), "kind": "corrupt_log"})
        except Exception as exc:
            self._internal_error(exc, ok_field=True)


def make_server(state: AppState, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
