"""Command line: check, serve, export, summary."""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser

from .export import export_project, write_span_table
from .project import Project, resolve_project_layout
from .server import AppState, make_server
from .store import DecisionStore
from .summary import corpus_summary

BULLET = "  - "


def _add_project_args(parser: argparse.ArgumentParser, guide: bool = False) -> None:
    parser.add_argument("--project", help="a directory holding annotator_A/ and annotator_B/; "
                                          "the other paths are derived from it")
    parser.add_argument("--a-dir", help="annotator A's directory")
    parser.add_argument("--b-dir", help="annotator B's directory")
    parser.add_argument("--out", help="where decisions and exports are written")
    parser.add_argument("--layer", default=None,
                        help="annotation layer to review (detected when the corpus has only one)")
    parser.add_argument("--adjudicator", default="",
                        help="identifier recorded with the exported results")
    if guide:
        parser.add_argument("--guide", default=None,
                            help="markdown file shown in the guidelines panel")


def _paths(args, parser: argparse.ArgumentParser) -> dict:
    """Explicit paths always win; --project fills in whatever was not given."""
    guide = getattr(args, "guide", None)
    if args.project:
        try:
            return resolve_project_layout(args.project, args.a_dir, args.b_dir, args.out, guide)
        except FileNotFoundError as exc:
            parser.error(str(exc))
    if not (args.a_dir and args.b_dir and args.out):
        parser.error("give --project, or all of --a-dir --b-dir --out")
    return {"a_dir": args.a_dir, "b_dir": args.b_dir, "out": args.out, "guide": guide,
            "config": {}}


def _text_origin(source) -> str:
    if not source:
        return "not found"
    if source.startswith("A and B"):
        return "in both annotators' files, identical"
    if source in ("annotator A", "annotator B"):
        other = "B" if source.endswith("A") else "A"
        return f"from {source}'s files (annotator {other}'s carry none)"
    return source


def _report(project: Project) -> None:
    pf = project.preflight()
    print("=" * 72)
    print("PROJECT CHECK")
    print("=" * 72)
    print(f"  paired documents : {pf['paired']}")
    if pf["only_in_a"] or pf["only_in_b"]:
        print(f"  unpaired         : {len(pf['only_in_a'])} only in A, "
              f"{len(pf['only_in_b'])} only in B")
    side = lambda name, kind: f"{name} ({kind})" if kind else name
    print(f"  annotations      : {side('A', pf['format_a'])} + {side('B', pf['format_b'])}")
    print(f"  document text    : {_text_origin(pf['text_source'])}")
    print(f"  layers           : {pf['layers']}")
    print(f"  reviewing layer  : {pf['layer']}")
    print(f"  roles detected   : {len(pf['labels'])}")
    if pf["labels"]:
        preview = ", ".join(pf["labels"][:10])
        print(f"                     {preview}{' ...' if len(pf['labels']) > 10 else ''}")
    for side in ("a", "b"):
        mapping = pf[f"mapping_{side}"]
        print(f"  fields ({side.upper()})       : "
              + ", ".join(f"{k}={v}" for k, v in mapping.items() if v))
    if pf["warnings"]:
        print(f"\n  warnings ({len(pf['warnings'])}):")
        for w in pf["warnings"][:12]:
            print(BULLET + w["message"])
        if len(pf["warnings"]) > 12:
            print(f"    ... and {len(pf['warnings']) - 12} more")
    if pf["blocking"]:
        print(f"\n  BLOCKING ({len(pf['blocking'])}):")
        for b in pf["blocking"][:12]:
            print(BULLET + (f"[{b['doc_id']}] " if b["doc_id"] else "") + b["message"])
        if len(pf["blocking"]) > 12:
            print(f"    ... and {len(pf['blocking']) - 12} more")


def _open(paths: dict, layer: str | None) -> Project:
    project = Project(paths["a_dir"], paths["b_dir"], paths["out"], layer=layer,
                      config=paths.get("config"))
    _report(project)
    if not project.usable:
        print("\nRefusing to continue: the problems above make character offsets untrustworthy.")
        sys.exit(2)
    return project


def _print_summary(report: dict) -> None:
    print("=" * 72)
    print(f"ADJUDICATION SUMMARY — layer {report['layer']!r}")
    print("=" * 72)
    print(f"  documents            : {report['documents']} "
          f"({report['documents_complete']} complete)")
    print(f"  conflicts            : {report['conflicts']} "
          f"(+{report['auto_agreed']} agreed automatically)")
    print(f"  adjudicated          : {report['adjudicated']} "
          f"({report['percent_adjudicated']}%)")
    print(f"  deferred / dropped   : {report['deferred']} / {report['dropped']}")
    if report["stale"]:
        print(f"  NEEDS RE-ADJUDICATION: {report['stale']} decision(s) made against source "
              f"annotation that has since changed; void, and counted nowhere else")
    print("\n  outcome of each adjudicated conflict")
    print(f"    final equals A     : {report['took_A']} ({report['percent_took_A']}%)")
    print(f"    final equals B     : {report['took_B']} ({report['percent_took_B']}%)")
    print(f"    equals both        : {report['took_both']}")
    print(f"    differs from both  : {report['differs_from_both']} "
          f"({report['percent_differs_from_both']}%)")
    print(f"    new boundary       : {report['new_boundary']} ({report['percent_new_boundary']}%)")
    print(f"    new structure      : {report['new_structure']} "
          f"({report['percent_new_structure']}%)")
    print(f"    new role           : {report['new_role_inventory']} "
          f"(a role neither annotator used there)")
    print(f"    roles reassigned   : {report['role_reassigned']} "
          f"(same extents and roles as one annotator, placed differently)")
    print("\n  conflict types")
    for shape, n in sorted(report["by_conflict_type"].items(), key=lambda kv: -kv[1]):
        median = report["seconds_by_conflict_type"].get(shape)
        print(f"    {shape:<18} {n:>6}" + (f"   median {median}s" if median else ""))
    print("\n  decisions")
    for kind, n in sorted(report["by_decision_type"].items(), key=lambda kv: -kv[1]):
        print(f"    {kind:<18} {n:>6}")
    print(f"\n  time per conflict    : median {report['seconds_median']}s "
          f"IQR {report['seconds_iqr']} (n={report['timed_decisions']} timed"
          + (f", {report['untimed_decisions']} without timing" if report["untimed_decisions"]
             else "") + ")")
    for kind, stats in report["simple_vs_structural"].items():
        print(f"    {kind:<18} n={stats['n']:<5} median {stats['seconds_median']}s")
    print(f"  revisions            : {report['revisions_total']}")
    validation = report["validation"]
    print(f"  validation           : {validation['documents_validated']} document(s), "
          f"{validation['error_count']} error(s)")
    for error in validation["errors"][:5]:
        print(BULLET + error)

    agreement = report["agreement"]
    f1 = agreement["span_f1"]
    kappa = agreement["role_kappa_on_shared_extents"]
    print("\n  inter-annotator agreement (A vs B, before adjudication)")
    print(f"    span F1            : {f1['f1']}  "
          f"(A {f1['a_spans']} spans, B {f1['b_spans']} spans, {f1['shared']} exact matches)")
    if kappa["kappa"] is not None:
        print(f"    role kappa         : {kappa['kappa']}  "
              f"(n={kappa['n']} extents both annotators marked once"
              + (f", {kappa['skipped_stacked']} stacked extent(s) excluded"
                 if kappa["skipped_stacked"] else "") + ")")
    else:
        print("    role kappa         : n/a (no extent marked exactly once by both annotators)")

    print(f"\n  {report['timing_note']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="adjudicator",
        description="SpanArbiter: resolve disagreements between two span annotators.")
    subs = parser.add_subparsers(dest="command", required=True)

    check = subs.add_parser("check", help="validate a project without starting the interface")
    _add_project_args(check)

    serve = subs.add_parser("serve", help="start the review interface")
    _add_project_args(serve, guide=True)
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-browser", action="store_true")

    export = subs.add_parser("export", help="write the resolved layer")
    _add_project_args(export)
    export.add_argument("--export-dir", default=None, help="defaults to <out>/export")
    export.add_argument("--include-incomplete", action="store_true",
                        help="also export documents with deferred or undecided items")
    export.add_argument("--no-roundtrip", action="store_true",
                        help="write only the canonical export, not the input-shaped mirror")

    summary = subs.add_parser("summary", help="aggregate statistics from the decision logs")
    _add_project_args(summary)
    summary.add_argument("--json", dest="json_path", default=None,
                         help="also write the report as JSON")

    args = parser.parse_args(argv)
    paths = _paths(args, parser)

    if args.command == "check":
        project = Project(paths["a_dir"], paths["b_dir"], paths["out"], layer=args.layer,
                          config=paths.get("config"))
        _report(project)
        print("\nProject is ready." if project.usable else "\nProject is NOT usable.")
        return 0 if project.usable else 2

    project = _open(paths, args.layer)
    store = DecisionStore(paths["out"])

    if args.command == "export":
        target = args.export_dir or os.path.join(paths["out"], "export")
        result = export_project(project, store, out_dir=target,
                                roundtrip=not args.no_roundtrip,
                                only_complete=not args.include_incomplete)
        table = os.path.join(target, "spans.tsv")
        rows = write_span_table(project, store, table, adjudicator=args.adjudicator,
                                only_complete=not args.include_incomplete)
        print(f"\nwrote {len(result.written)} document(s), {result.span_count} spans -> {target}")
        print(f"span table: {rows} row(s) -> {table}")
        for note in result.notes:
            print(f"note: {note}")
        if result.skipped:
            print(f"skipped {len(result.skipped)}:")
            for doc_id, reason in result.skipped[:15]:
                print(f"{BULLET}{doc_id}: {reason}")
            if len(result.skipped) > 15:
                print(f"    ... and {len(result.skipped) - 15} more")
        return 0

    if args.command == "summary":
        report = corpus_summary(project, store, adjudicator=args.adjudicator)
        print()
        _print_summary(report)
        if args.json_path:
            os.makedirs(os.path.dirname(os.path.abspath(args.json_path)) or ".", exist_ok=True)
            with open(args.json_path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=1)
            print(f"\nwrote {args.json_path}")
        return 0

    guide = paths.get("guide")
    if guide is None:
        for candidate in ("GUIDELINES.md", "guidelines.md", os.path.join("docs", "GUIDELINES.md")):
            if os.path.isfile(candidate):
                guide = candidate
                break
    state = AppState(project, store, guide)
    httpd = make_server(state, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  guidelines       : {guide or 'none (pass --guide to show them in the sidebar)'}")
    print(f"\nReview interface at {url}")
    print("Local only. Nothing is sent anywhere. Ctrl+C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
