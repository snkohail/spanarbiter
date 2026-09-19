"""The HTTP layer, exercised over a real socket."""
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from adjudicator.project import Project
from adjudicator.server import AppState, make_server
from adjudicator.store import DecisionStore
from conftest import TEXT, sha_of, write_doc


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write_doc(a, "D1", [(0, 40, "PREAMBLE"), (60, 120, "FACTS")], text=TEXT, sha=sha_of(TEXT))
    write_doc(b, "D1", [(0, 40, "PREAMBLE"), (60, 120, "ISSUE")], sha=sha_of(TEXT))
    project = Project(str(a), str(b), str(tmp_path / "out"), layer="rhetorical")
    store = DecisionStore(str(tmp_path / "out"))
    guide = tmp_path / "G.md"
    guide.write_text("# Rules\n\n- first\n- second\n", encoding="utf-8")
    state = AppState(project, store, str(guide))
    httpd = make_server(state, free_port())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield {"base": base, "state": state, "store": store, "project": project}
    httpd.shutdown()
    httpd.server_close()


def get(base, path):
    with urllib.request.urlopen(base + path) as response:
        return json.loads(response.read())


def post(base, path, payload):
    request = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def test_project_endpoint_describes_the_corpus(live):
    payload = get(live["base"], "/api/project")
    assert payload["usable"] and payload["paired"] == 1
    assert payload["layer"] == "rhetorical" and "rhetorical" in payload["available_layers"]


def test_the_interface_and_its_modules_are_served(live):
    for path, expected in [("/", "text/html"), ("/css/app.css", "text/css"),
                           ("/js/main.js", "text/javascript"),
                           ("/js/views/conflict.js", "text/javascript")]:
        with urllib.request.urlopen(live["base"] + path) as response:
            assert response.status == 200
            assert expected in response.headers["Content-Type"]


def test_a_path_outside_the_web_root_is_not_served(live):
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(live["base"] + "/../pyproject.toml")
    assert caught.value.code == 404


def test_loading_a_document_materialises_the_agreements(live):
    payload = get(live["base"], "/api/document?id=D1")
    assert payload["text"] == TEXT
    assert any(d["decision_type"] == "AUTO_AGREE" for d in payload["decisions"].values())
    assert payload["progress"]["auto_agreed"] == 1


def test_an_unknown_document_is_a_404(live):
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(live["base"] + "/api/document?id=nope")
    assert caught.value.code == 404


def test_a_decision_is_recorded_and_progress_updates(live):
    document = get(live["base"], "/api/document?id=D1")
    conflict = next(c for c in document["conflicts"] if not c["agreed"])
    result = post(live["base"], "/api/decide", {
        "doc_id": "D1", "conflict_id": conflict["conflict_id"],
        "spans": [{"begin": conflict["a_spans"][0]["begin"],
                   "end": conflict["a_spans"][0]["end"],
                   "labels": [conflict["a_spans"][0]["label"]], "origin": "A"}],
        "note": "A is right", "seconds": 4})
    assert result["decision"]["decision_type"] == "TAKE_A"
    assert result["progress"]["settled"] == 1
    stored = live["store"].load("D1", "rhetorical")
    assert stored[conflict["conflict_id"]].note == "A is right"


def test_an_invalid_decision_is_rejected_with_a_readable_reason(live):
    document = get(live["base"], "/api/document?id=D1")
    conflict = next(c for c in document["conflicts"] if not c["agreed"])
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(live["base"], "/api/decide", {
            "doc_id": "D1", "conflict_id": conflict["conflict_id"],
            "spans": [{"begin": conflict["a_spans"][0]["begin"],
                       "end": conflict["a_spans"][0]["end"],
                       "labels": [conflict["a_spans"][0]["label"]], "origin": "A"},
                      {"begin": conflict["b_spans"][0]["begin"],
                       "end": conflict["b_spans"][0]["end"],
                       "labels": [conflict["b_spans"][0]["label"]], "origin": "B"}]})
    assert caught.value.code == 400
    message = json.loads(caught.value.read())["error"]
    assert "annotator A" in message and "annotator B" in message


def test_a_page_load_racing_a_save_does_not_erase_it(live):
    """Loading a document WRITES, because agreements are materialised on read. If that
    read-modify-write does not share the commit lock, a page load that began before a save
    writes back its older snapshot and the decision disappears."""
    document = get(live["base"], "/api/document?id=D1")
    conflict = next(c for c in document["conflicts"] if not c["agreed"])
    body = {"doc_id": "D1", "conflict_id": conflict["conflict_id"],
            "spans": [{"begin": conflict["a_spans"][0]["begin"],
                       "end": conflict["a_spans"][0]["end"],
                       "labels": [conflict["a_spans"][0]["label"]], "origin": "A"}]}
    for _ in range(12):
        path = live["store"].path_for("D1", "rhetorical")
        import os
        if os.path.exists(path):
            os.remove(path)
        outcome = []
        threads = [
            threading.Thread(target=lambda: get(live["base"], "/api/document?id=D1")),
            threading.Thread(target=lambda: outcome.append(
                post(live["base"], "/api/decide", body)["decision"])),
            threading.Thread(target=lambda: get(live["base"], "/api/document?id=D1")),
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        assert outcome, "the decision was never accepted"
        assert conflict["conflict_id"] in live["store"].load("D1", "rhetorical")


def test_switching_layer_changes_the_detected_roles(live):
    payload = post(live["base"], "/api/layer", {"layer": "rhetorical"})
    assert payload["ok"] and payload["labels"]


def test_the_guide_is_read_from_disk_every_time(live, tmp_path):
    assert "# Rules" in get(live["base"], "/api/guide")["markdown"]
    (tmp_path / "G.md").write_text("# Revised\n", encoding="utf-8")
    assert "# Revised" in get(live["base"], "/api/guide")["markdown"]


def test_export_reports_what_it_wrote_and_what_it_skipped(live):
    payload = post(live["base"], "/api/export", {"roundtrip": True, "only_complete": True})
    assert payload["ok"] and "directory" in payload
    assert isinstance(payload["written"], list) and isinstance(payload["skipped"], list)


def test_request_paths_are_never_logged(live, capsys):
    get(live["base"], "/api/document?id=D1")
    captured = capsys.readouterr()
    assert "D1" not in captured.err


# ------------------------------------------------------- opening another project (P2)
def _second_project(tmp_path):
    root = tmp_path / "second"
    write_doc(root / "annotator_A", "D9", [(0, 40, "PREAMBLE")], text=TEXT, sha=sha_of(TEXT))
    write_doc(root / "annotator_B", "D9", [(0, 40, "DECISION")], sha=sha_of(TEXT))
    return root


def test_the_project_endpoint_describes_what_was_loaded(live):
    body = get(live["base"], "/api/project")
    assert body["paired"] == 1
    assert body["layer"] == "rhetorical"
    assert "PREAMBLE" in body["labels"]
    assert body["mapping_a"]["begin"] == "begin"


def test_opening_another_project_switches_the_corpus(live, tmp_path):
    root = _second_project(tmp_path)
    body = post(live["base"], "/api/project/open", {"root": str(root)})
    assert body["ok"] is True
    assert [d["doc_id"] for d in get(live["base"], "/api/documents")["documents"]] == ["D9"]
    # decisions must land in the new project's own output directory, not the old one
    assert live["state"].store.out_dir == str(root / "adjudication")


def test_opening_a_directory_without_the_layout_leaves_the_project_alone(live, tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(live["base"], "/api/project/open", {"root": str(empty)})
    assert caught.value.code == 400
    assert "annotator_A" in json.loads(caught.value.read())["error"]
    assert [d["doc_id"] for d in get(live["base"], "/api/documents")["documents"]] == ["D1"]
