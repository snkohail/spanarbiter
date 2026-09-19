"""brat and CoNLL input, and the sniffing that picks between them.

The two formats a reader of a span-adjudication paper will arrive with. Neither carries a field
mapping to detect, so what matters here is that offsets survive the trip exactly, and that the
places where a format cannot express what the tool needs are reported rather than fudged.
"""
import pytest

from adjudicator.adapters import detect_format, load_any
from adjudicator.project import Project

TEXT = "The court considered the claim of the plaintiff against the defendant.\n"


def write(dirpath, name, body):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / name).write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- brat
def brat_pair(directory, doc="D1", ann=None):
    write(directory, f"{doc}.txt", TEXT)
    write(directory, f"{doc}.ann", ann if ann is not None else
          "T1\tCOURT 4 9\tcourt\nT2\tPARTY 38 47\tplaintiff\n")


def test_brat_offsets_survive_exactly(tmp_path):
    brat_pair(tmp_path / "a")
    docs, problems = load_any(str(tmp_path / "a"), {})
    assert not problems, problems
    doc = docs["D1"]
    assert doc.text == TEXT
    got = sorted((s.begin, s.end, s.label) for s in doc.spans)
    assert got == [(4, 9, "COURT"), (38, 47, "PARTY")]
    for span in doc.spans:
        assert doc.text[span.begin:span.end]


def test_brat_surface_text_is_checked_against_the_offsets(tmp_path):
    """brat repeats the covered text in the third column. When it disagrees with the offsets the
    file has been edited or retokenised, and every offset in it is suspect."""
    brat_pair(tmp_path / "a", ann="T1\tCOURT 4 9\tdefendant\n")
    docs, problems = load_any(str(tmp_path / "a"), {})
    trouble = problems + [i for d in docs.values() for i in d.issues]
    assert any("court" in str(t) or "defendant" in str(t) for t in trouble), trouble


def test_brat_discontinuous_annotation_is_reported_not_flattened(tmp_path):
    """A fragmented brat annotation is two pieces of text under one label. This tool's span is a
    single extent, so silently taking the enclosing range would invent an annotation nobody made."""
    brat_pair(tmp_path / "a", ann="T1\tCOURT 4 9;37 46\tcourt plaintiff\n")
    docs, problems = load_any(str(tmp_path / "a"), {})
    trouble = problems + [i for d in docs.values() for i in d.issues]
    assert any("discontinuous" in str(t).lower() for t in trouble), trouble


def test_brat_ignores_relations_attributes_and_notes(tmp_path):
    brat_pair(tmp_path / "a", ann=(
        "T1\tCOURT 4 9\tcourt\n"
        "R1\tHeldBy Arg1:T1 Arg2:T1\n"
        "A1\tNegation T1\n"
        "#1\tAnnotatorNotes T1\tlooks right\n"
        "*\tEquiv T1 T1\n"))
    docs, problems = load_any(str(tmp_path / "a"), {})
    assert not problems, problems
    assert len(docs["D1"].spans) == 1


def test_two_brat_directories_pair_and_produce_conflicts(tmp_path):
    brat_pair(tmp_path / "annotator_A")
    brat_pair(tmp_path / "annotator_B", ann="T1\tPARTY 4 9\tcourt\n")
    project = Project(str(tmp_path / "annotator_A"), str(tmp_path / "annotator_B"),
                      str(tmp_path / "out"))
    assert project.usable, project.preflight()["blocking"]
    assert project.doc_ids == ["D1"]
    shapes = {c.shape for c in project.conflicts("D1")}
    assert "ROLE_CLASH" in shapes, shapes


# --------------------------------------------------------------------------- CoNLL
BIO = (
    "The\tO\n"
    "court\tB-COURT\n"
    "considered\tO\n"
    "the\tO\n"
    "claim\tB-CLAIM\n"
    "of\tI-CLAIM\n"
    "the\tI-CLAIM\n"
    "plaintiff\tI-CLAIM\n"
    ".\tO\n"
)


def test_conll_tags_become_character_offsets(tmp_path):
    write(tmp_path / "a", "D1.conll", BIO)
    docs, problems = load_any(str(tmp_path / "a"), {})
    assert not problems, problems
    doc = docs["D1"]
    spans = sorted((s.begin, s.end, s.label) for s in doc.spans)
    assert sorted(s[2] for s in spans) == ["CLAIM", "COURT"]
    for begin, end, label in spans:
        assert doc.text[begin:end].strip(), (begin, end, label)
    claim = [s for s in spans if s[2] == "CLAIM"][0]
    assert doc.text[claim[0]:claim[1]] == "claim of the plaintiff"


def test_conll_accepts_bioes_and_a_stray_inside_tag(tmp_path):
    write(tmp_path / "a", "D1.conll",
          "Alpha\tS-ONE\nBeta\tB-TWO\nGamma\tE-TWO\nDelta\tI-THREE\n")
    docs, problems = load_any(str(tmp_path / "a"), {})
    assert not problems, problems
    labels = sorted(s.label for s in docs["D1"].spans)
    assert labels == ["ONE", "THREE", "TWO"]


def test_conll_sentence_breaks_do_not_join_two_spans(tmp_path):
    write(tmp_path / "a", "D1.conll", "Alpha\tB-X\n\nBeta\tI-X\n")
    docs, problems = load_any(str(tmp_path / "a"), {})
    spans = sorted((s.begin, s.end) for s in docs["D1"].spans)
    assert len(spans) == 2, spans


def test_conll_disagreeing_tokenisation_is_caught_as_a_text_mismatch(tmp_path):
    """The text is reconstructed from the tokens, so two annotators who tokenised differently
    are measuring offsets against different documents. That must block, not drift."""
    write(tmp_path / "annotator_A", "D1.conll", "Alpha\tB-X\nBeta\tO\n")
    write(tmp_path / "annotator_B", "D1.conll", "Alpha\tB-X\nBe\tO\nta\tO\n")
    project = Project(str(tmp_path / "annotator_A"), str(tmp_path / "annotator_B"),
                      str(tmp_path / "out"))
    assert not project.usable
    assert any("different document text" in i["message"]
               for i in project.preflight()["blocking"]), project.preflight()["blocking"]


def test_conll_with_no_tags_loads_as_an_empty_side(tmp_path):
    write(tmp_path / "a", "D1.conll", "Alpha\tO\nBeta\tO\n")
    docs, problems = load_any(str(tmp_path / "a"), {})
    assert not problems
    assert docs["D1"].spans == []


# --------------------------------------------------------------------------- sniffing
def test_the_format_is_detected_from_what_is_in_the_directory(tmp_path):
    write(tmp_path / "j", "D1.json", '{"doc_id": "D1", "text": "x", "spans": []}')
    brat_pair(tmp_path / "b")
    write(tmp_path / "c", "D1.conll", BIO)
    assert detect_format(str(tmp_path / "j")) == "json"
    assert detect_format(str(tmp_path / "b")) == "brat"
    assert detect_format(str(tmp_path / "c")) == "conll"


def test_a_directory_of_two_formats_is_refused_rather_than_half_read(tmp_path):
    d = tmp_path / "mixed"
    brat_pair(d)
    write(d, "D2.json", '{"doc_id": "D2", "text": "x", "spans": []}')
    with pytest.raises(Exception) as caught:
        detect_format(str(d))
    assert "more than one" in str(caught.value).lower(), caught.value


def test_an_unrecognisable_directory_says_what_it_looked_for(tmp_path):
    write(tmp_path / "e", "notes.md", "# nothing to see")
    with pytest.raises(Exception) as caught:
        detect_format(str(tmp_path / "e"))
    for token in (".json", ".ann", ".conll"):
        assert token in str(caught.value), caught.value
