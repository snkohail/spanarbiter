"""Decisions on disk: never half-written, never silently lost."""
import json
import threading

import pytest

from adjudicator.conflicts import build_conflicts
from adjudicator.decisions import ResolvedSpan, decide
from adjudicator.store import DecisionStore, StoreError
from conftest import S


def a_decision():
    conflict = build_conflicts("D1", "rhetorical", [S(0, 10, "FACTS")], [S(0, 10, "ISSUE")])[0]
    return conflict, decide(conflict, [ResolvedSpan(0, 10, ("FACTS",), "A")])


def test_a_decision_survives_being_written_and_read(store):
    conflict, decision = a_decision()
    store.save("D1", "rhetorical", {conflict.conflict_id: decision})
    reloaded = store.load("D1", "rhetorical")
    assert reloaded[conflict.conflict_id].as_dict() == decision.as_dict()


def test_no_log_yet_is_not_an_error(store):
    assert store.load("never-seen", "rhetorical") == {}


def test_a_damaged_log_is_an_error_not_an_empty_result(store):
    """Reporting a corrupt log as 'no decisions' invites the reviewer to redo finished work,
    or to overwrite it."""
    conflict, decision = a_decision()
    store.save("D1", "rhetorical", {conflict.conflict_id: decision})
    with open(store.path_for("D1", "rhetorical"), "w", encoding="utf-8") as fh:
        fh.write("{ truncated")
    with pytest.raises(StoreError, match="must never be reported as zero"):
        store.load("D1", "rhetorical")


def test_update_holds_the_lock_across_read_modify_write(store):
    """Two threads each adding a decision must both survive. A read-modify-write that releases
    the lock in between loses whichever write lands first."""
    conflicts = build_conflicts("D1", "rhetorical",
                                [S(0, 10, "FACTS"), S(50, 60, "FACTS")],
                                [S(0, 10, "ISSUE"), S(50, 60, "ISSUE")])
    barrier = threading.Barrier(2)

    def writer(conflict):
        def run():
            decision = decide(conflict, [ResolvedSpan(conflict.begin, conflict.end,
                                                      ("FACTS",), "A")])
            barrier.wait()
            for _ in range(25):
                store.update("D1", "rhetorical",
                             lambda current: current.__setitem__(conflict.conflict_id, decision))
        return run

    threads = [threading.Thread(target=writer(c)) for c in conflicts]
    for t in threads: t.start()
    for t in threads: t.join()
    assert set(store.load("D1", "rhetorical")) == {c.conflict_id for c in conflicts}


def test_the_written_file_is_valid_json_with_a_schema_version(store):
    conflict, decision = a_decision()
    store.save("D1", "rhetorical", {conflict.conflict_id: decision}, text_sha256="abc")
    payload = json.loads(open(store.path_for("D1", "rhetorical"), encoding="utf-8").read())
    assert payload["schema_version"] and payload["text_sha256"] == "abc"
    assert payload["doc_id"] == "D1" and payload["layer"] == "rhetorical"


def test_no_temporary_file_is_left_behind(store, tmp_path):
    conflict, decision = a_decision()
    store.save("D1", "rhetorical", {conflict.conflict_id: decision})
    assert not any(p.name.endswith(".tmp") for p in (tmp_path / "out" / "decisions").iterdir())


def test_layers_are_stored_separately(store):
    conflict, decision = a_decision()
    store.save("D1", "rhetorical", {conflict.conflict_id: decision})
    assert store.load("D1", "entity") == {}
