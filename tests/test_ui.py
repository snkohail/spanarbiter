"""Browser tests: the behaviour a Python test cannot see.

Run with:  python -m pytest tests/test_ui.py
Needs:     pip install -e ".[test]" && playwright install chromium
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="pytest-playwright is not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

from adjudicator.store import DecisionStore  # noqa: E402
from conftest import TEXT, sha_of, write_doc  # noqa: E402

pytestmark = pytest.mark.ui
ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui")
    a, b = tmp / "A", tmp / "B"
    # one of each shape the interface presents differently, plus a role containing a quote
    write_doc(a, "D1", [
        (0, 40, "PREAMBLE"),          # agreed
        (60, 120, "FACTS"),           # role clash with B
        (200, 260, "ANALYSIS"),       # A only
        (300, 400, "FACTS"), (310, 330, 'SAID "X"'),   # nesting difference
    ], text=TEXT, sha=sha_of(TEXT))
    write_doc(b, "D1", [
        (0, 40, "PREAMBLE"),
        (60, 120, "ISSUE"),
        (300, 400, "FACTS"),
    ], sha=sha_of(TEXT))
    out = tmp / "out"
    guide = tmp / "G.md"
    guide.write_text("# Guide\n\n- **Take A** — pick A. This line wraps\n  onto a second line.\n",
                     encoding="utf-8")
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "adjudicator", "serve", "--a-dir", str(a), "--b-dir", str(b),
         "--out", str(out), "--layer", "rhetorical", "--guide", str(guide),
         "--port", str(port), "--no-browser"],
        cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.15)
    else:
        raise RuntimeError(process.stdout.read().decode()[:800])
    yield {"url": f"http://127.0.0.1:{port}/", "out": out, "guide": guide}
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture(scope="module")
def page(app):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1400, "height": 900})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(app["url"])
        pg.wait_for_selector(".card-head", timeout=15000)
        yield pg
        browser.close()


def decisions_on_disk(app, doc_id="D1"):
    path = Path(DecisionStore(str(app["out"])).path_for(doc_id, "rhetorical"))
    return json.loads(path.read_text(encoding="utf-8"))["decisions"] if path.exists() else []


def test_the_queue_opens_on_a_conflict_not_the_whole_document(page):
    assert page.inner_text(".shape-name")
    assert page.is_visible("#conflict")
    assert page.is_hidden("#context"), "the full document must not be the default view"


def test_each_shape_is_named_in_plain_language(page):
    page.select_option("#filter-select", "ROLE_CLASH")
    page.wait_for_timeout(300)
    assert "Same text, different role" in page.inner_text(".shape-name")
    assert page.inner_text(".shape-hint")
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_a_role_clash_passage_carries_both_annotators(page):
    page.select_option("#filter-select", "ROLE_CLASH")
    page.wait_for_timeout(300)
    panel = page.locator("#conflict .side-contested")
    assert panel.count() == 1, "the shared passage is one panel owned by both annotators"
    assert panel.locator(".span-text").count() == 1, "the text is shown once, not twice"
    assert panel.locator(".who-a").count() == 1 and panel.locator(".who-b").count() == 1
    assert "Both annotators" in panel.inner_text()
    assert page.locator("#conflict .role-choice .role").count() >= 2
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_a_role_containing_a_quote_is_shown_verbatim(page):
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(300)
    roles = page.eval_on_selector_all(".role", "els => els.map(e => e.textContent)")
    for _ in range(10):
        if any('SAID "X"' in r for r in roles):
            break
        page.click("#act-next")
        page.wait_for_timeout(200)
        roles = page.eval_on_selector_all(".role", "els => els.map(e => e.textContent)")
    assert any('SAID "X"' in r for r in roles)
    assert page.errors == []
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_keyboard_take_a_saves_and_advances(page, app):
    before = page.inner_text("#position")
    page.keyboard.press("1")
    page.wait_for_timeout(800)
    assert "Saved" in page.inner_text(".msg")
    assert page.inner_text("#position") != before
    assert any(d["decision_type"] == "TAKE_A" for d in decisions_on_disk(app))


def test_context_opens_without_leaving_the_conflict(page):
    position = page.inner_text("#position")
    page.keyboard.press("c")
    page.wait_for_timeout(500)
    assert page.is_visible("#context")
    assert page.eval_on_selector_all("#context mark", "e => e.length") > 0
    assert page.inner_text("#position") == position, "opening context must not move the queue"
    page.keyboard.press("c")
    page.wait_for_timeout(300)


def test_agreement_is_decided_per_run_not_per_conflict(page):
    """The fixture's nesting conflict has A = FACTS[300,400] + SAID"X"[310,330] against
    B = FACTS[300,400]. The conflict is not an agreement, but the characters both annotators
    labelled FACTS identically ARE agreed, and only the nested stretch is contested.

    Inheriting one flag from the whole conflict paints the identical part as a disagreement,
    which tells the reviewer the two annotators differ over text they typed the same way.
    """
    page.select_option("#filter-select", "NESTING_DIFF")
    page.wait_for_timeout(400)
    if not page.eval_on_selector_all(".shape-name", "e => e.length"):
        pytest.skip("no nesting conflict in this fixture")
    page.keyboard.press("c")
    page.wait_for_timeout(600)

    classes = page.eval_on_selector_all(
        "#context mark", "els => els.map(e => e.className)")
    assert any("m-agreed" in c for c in classes), \
        f"the part both annotators labelled identically was not shown as agreed: {classes}"
    assert any("m-contested" in c for c in classes), \
        f"the nested part they differ over was not shown as contested: {classes}"

    # and the contested run says who said what
    title = page.get_attribute("#context mark.m-contested", "title")
    assert "A:" in title and "B:" in title, title

    page.keyboard.press("c")
    page.wait_for_timeout(300)
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_the_context_view_carries_a_legend(page):
    page.keyboard.press("c")
    page.wait_for_timeout(600)
    labels = page.eval_on_selector_all("#context .legend-item", "e => e.map(x => x.innerText.trim())")
    assert labels == ["Annotator A", "Annotator B", "both, disagreeing", "both, agreed"]
    painted = page.eval_on_selector_all(
        "#context .legend-swatch",
        "e => e.map(x => getComputedStyle(x).backgroundColor + getComputedStyle(x).backgroundImage)")
    assert all(p not in ("rgba(0, 0, 0, 0)none", "") for p in painted), painted
    page.keyboard.press("c")
    page.wait_for_timeout(300)


def test_context_dims_the_surroundings_rather_than_boxing_the_conflict(page):
    page.keyboard.press("c")
    page.wait_for_timeout(600)
    assert page.eval_on_selector_all("#context .focus-band", "e => e.length") == 0
    outside = page.eval_on_selector_all(
        "#context .context-outside", "e => e.map(x => Number(getComputedStyle(x).opacity))")
    assert outside and all(o < 1 for o in outside)
    page.keyboard.press("c")
    page.wait_for_timeout(300)


def test_context_can_widen_to_the_whole_document(page):
    page.keyboard.press("c")
    page.wait_for_timeout(400)
    before = page.inner_text(".context-text").strip()
    page.click("#context-full")
    page.wait_for_timeout(400)
    assert len(page.inner_text(".context-text").strip()) >= len(before)
    page.keyboard.press("c")
    page.wait_for_timeout(300)


def test_a_picked_span_always_arrives_with_its_own_role(page):
    """Moving a boundary while leaving behind a role that belonged to some other span is how a
    resolved annotation silently ends up mislabelled. Picking a source span carries its role by
    construction, and this is the test that says so."""
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(400)
    if page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.wait_for_selector("#editor .source-toggle", timeout=10000)

    want = None
    for _ in range(12):
        want = page.evaluate("""() => {
          const t = [...document.querySelectorAll('#editor .source-toggle')]
            .find((x) => x.getAttribute('aria-pressed') === 'false');
          return t && { source: t.dataset.source, begin: +t.dataset.begin,
                        end: +t.dataset.end, label: t.dataset.label };
        }""")
        if want:
            break
        if page.is_disabled("#act-next"):
            pytest.skip("no unpicked source span in this fixture")
        page.click("#act-next")
        page.wait_for_timeout(350)
    assert want

    page.locator(f'#editor .source-toggle[data-source="{want["source"]}"]').click()
    page.wait_for_timeout(350)
    # match on the role too: on a role clash A and B share the extent, and the point of the
    # test is that the picked span brought ITS role, not the one already sitting there
    got = page.evaluate(
        "(w) => window.__adjDrafts().find(d => d.begin === w.begin && d.end === w.end"
        " && d.labels.length === 1 && d.labels[0] === w.label)", want)
    assert got, ("picked span did not arrive with its own role",
                 want, page.evaluate("() => window.__adjDrafts()"))

    page.locator(f'#editor .source-toggle[data-source="{want["source"]}"]').click()
    page.wait_for_timeout(250)          # put the draft set back as it was found
    if not page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_adding_a_span_on_the_same_text_saves_as_one_multi_role_annotation(page, app):
    """"+ Add span" copies the current extent, so ticking a different role there is the reviewer
    saying "this passage carries both". Sent as two separate spans it looks like two unrelated
    claims and gets refused; it is one judgement and must save as one."""
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(400)
    if page.is_disabled("#act-edit"):
        pytest.skip("nothing left to decide")
    page.keyboard.press("e")
    page.wait_for_timeout(500)

    roles = page.locator("#editor .draft").first.locator(".role-toggle input")
    first_role = roles.nth(0).get_attribute("value")
    second_role = roles.nth(1).get_attribute("value")
    page.locator("#editor .draft").first.locator(
        f'.role-toggle input[value="{first_role}"]').check()
    page.wait_for_timeout(200)

    page.click("#editor button:has-text('Add span')")
    page.wait_for_timeout(400)
    page.locator("#editor .draft").nth(1).locator(
        f'.role-toggle input[value="{second_role}"]').check()
    page.wait_for_timeout(200)

    page.click("#save-decision")
    page.wait_for_timeout(900)

    message = page.inner_text(".msg")
    assert "Not saved" not in message, message
    saved = [d for d in decisions_on_disk(app) if d["decision_type"] in ("MULTI_ROLE", "CUSTOM")]
    assert saved, decisions_on_disk(app)
    stacked = [s for d in saved for s in d["spans"] if len(s["labels"]) > 1]
    assert stacked, f"the two drafts were not merged into one span: {saved}"


def test_keep_both_is_withheld_when_it_would_merge_a_disagreement(page):
    page.select_option("#filter-select", "ROLE_CLASH")
    page.wait_for_timeout(400)
    if page.eval_on_selector_all(".shape-name", "e => e.length"):
        assert page.is_disabled("#act-both")
        assert "merge" in (page.get_attribute("#act-both", "title") or "")
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_flagging_keeps_the_document_incomplete(page, app):
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)
    if page.is_disabled("#act-defer"):
        pytest.skip("nothing left to flag")
    page.keyboard.press("s")
    page.wait_for_timeout(700)
    assert any(d["decision_type"] == "DEFER" for d in decisions_on_disk(app))
    assert "deferred" in page.inner_text(".panel").lower()


def test_a_wrapped_guideline_bullet_stays_inside_its_item(page):
    page.click("#guide-toggle")
    page.wait_for_timeout(500)
    items = page.eval_on_selector_all("#guide-body li", "e => e.map(x => x.innerText)")
    assert any("second line" in i for i in items), items
    strays = page.eval_on_selector_all(
        "#guide-body > p", "e => e.map(x => x.innerText).filter(t => t.startsWith('onto'))")
    assert strays == []
    page.click("#guide-close")
    page.wait_for_timeout(300)


def test_the_theme_toggle_persists_the_choice(page):
    page.click("#theme-toggle")
    page.wait_for_timeout(300)
    theme = page.get_attribute("html", "data-theme")
    assert theme in ("dark", "light")
    assert page.evaluate("() => localStorage.getItem('spanarbiter-theme')") == theme
    page.click("#theme-toggle")
    page.wait_for_timeout(200)


def test_no_uncaught_errors_anywhere_in_the_session(page):
    assert page.errors == []


def test_time_and_visits_survive_navigating_away_without_saving(page, app):
    """Looking at a conflict, moving on to think, and coming back to decide must accumulate.
    Counting only saves loses the first visit entirely, and with it its time."""
    # Reload so the timing ledger starts clean regardless of what earlier tests did.
    page.reload()
    page.wait_for_selector(".card-head", timeout=15000)
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(600)

    start = page.evaluate("() => window.__timerProbe()")
    assert start and start["visits"] == 1, start
    conflict_id = start["conflict_id"]

    page.wait_for_timeout(700)                     # spend time on it
    page.click("#act-next")                        # leave without deciding
    page.wait_for_timeout(400)
    page.click("#act-prev")                        # come back
    page.wait_for_timeout(400)

    back = page.evaluate("() => window.__timerProbe()")
    assert back["conflict_id"] == conflict_id
    assert back["visits"] == 2, f"returning to a conflict was not counted as a visit: {back}"
    assert back["seconds"] > 0.5, f"time from the first visit was lost: {back}"

    before = [d for d in decisions_on_disk(app) if d["conflict_id"] == conflict_id]
    page.keyboard.press("1")
    page.wait_for_timeout(900)
    saved = [d for d in decisions_on_disk(app) if d["conflict_id"] == conflict_id]
    assert saved, "decision not written"
    assert saved[0]["visit_count"] >= 2, saved[0]
    assert saved[0]["cumulative_seconds"] > 0.5, saved[0]
    # revisiting an already-decided conflict is a revision; a first decision is not
    assert saved[0]["revision_count"] == (before[0]["revision_count"] + 1 if before else 0)

    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


# ============================ P1.1 direct boundary interaction ============================
def test_selecting_document_text_sets_the_active_span_boundary(page):
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(400)
    if not page.evaluate("() => document.querySelector('#editor').hidden === false"):
        page.keyboard.press("e")
    page.wait_for_timeout(500)
    page.keyboard.press("c")                      # open the document context to select from
    page.wait_for_timeout(600)

    picked = page.evaluate("""() => {
        const node = document.querySelector('#context [data-offset]');
        if (!node || !node.firstChild) return null;
        const base = Number(node.dataset.offset);
        const range = document.createRange();
        range.setStart(node.firstChild, 1);
        range.setEnd(node.firstChild, Math.min(9, node.firstChild.length));
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(range);
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
        return [base + 1, base + Math.min(9, node.firstChild.length)];
    }""")
    assert picked, "no anchored text in the context view"
    page.wait_for_timeout(400)
    draft = page.evaluate("() => window.__textProbe.activeDraft()")
    assert [draft["begin"], draft["end"]] == picked, (draft, picked)
    page.keyboard.press("c")
    page.wait_for_timeout(300)


def test_a_selection_moves_the_active_span_not_the_last_one(page):
    """The old prototype always edited finals[finals.length - 1]. Explicit active span or the
    reviewer silently rewrites a different span than the one they are looking at."""
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(400)
    if page.evaluate("() => document.querySelector('#editor').hidden"):
        page.keyboard.press("e")
    page.wait_for_timeout(500)

    page.click("#editor button:has-text('Add span')")     # span 2 becomes active
    page.wait_for_timeout(400)
    page.click("#editor .draft:first-child button[data-act], #editor .draft:first-child button")
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__adjActive?.() ?? null") in (0, None)

    before = page.evaluate("() => window.__textProbe.activeDraft()")
    page.keyboard.press("c")
    page.wait_for_timeout(500)
    moved = page.evaluate("""() => {
        const node = document.querySelector('#context [data-offset]');
        const base = Number(node.dataset.offset);
        const range = document.createRange();
        range.setStart(node.firstChild, 0);
        range.setEnd(node.firstChild, Math.min(7, node.firstChild.length));
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(range);
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
        return [base, base + Math.min(7, node.firstChild.length)];
    }""")
    page.wait_for_timeout(400)
    spans = page.evaluate("() => window.__adjDrafts()")
    assert [spans[0]["begin"], spans[0]["end"]] == moved, spans
    page.keyboard.press("c")
    page.wait_for_timeout(300)
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


# ============================ P1.2 role selection scales with the inventory ============================
@pytest.fixture(scope="module")
def app_many(tmp_path_factory):
    """A corpus with a large role inventory — the shape that made the old chip wall unusable."""
    tmp = tmp_path_factory.mktemp("many")
    a, b = tmp / "A", tmp / "B"
    a_spans, b_spans = [], []
    for i in range(20):
        begin = i * 30
        a_spans.append((begin, begin + 20, f"ROLE_{i:02d}"))
        b_spans.append((begin, begin + 24, f"ROLE_{(i + 1) % 20:02d}"))
    write_doc(a, "M1", a_spans, text=TEXT, sha=sha_of(TEXT))
    write_doc(b, "M1", b_spans, sha=sha_of(TEXT))
    out = tmp / "out"
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "adjudicator", "serve", "--a-dir", str(a), "--b-dir", str(b),
         "--out", str(out), "--layer", "rhetorical", "--port", str(port), "--no-browser"],
        cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.15)
    else:
        raise RuntimeError(process.stdout.read().decode()[:800])
    yield {"url": f"http://127.0.0.1:{port}/", "out": out}
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture(scope="module")
def page_many(app_many, page):
    ctx = page.context.browser.new_context()
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(app_many["url"])
    pg.wait_for_selector(".card-head", timeout=15000)
    pg.wait_for_timeout(800)
    if pg.evaluate("() => document.getElementById('editor').hidden"):
        pg.click("#act-edit")
    pg.wait_for_selector("#editor .role-add", timeout=10000)
    yield pg
    ctx.close()


def test_a_small_inventory_still_shows_roles_inline(page):
    """Searching is slower than looking when there are only a few roles."""
    if page.evaluate("() => document.querySelector('#editor').hidden"):
        page.keyboard.press("e")
        page.wait_for_timeout(500)
    inline = page.eval_on_selector_all("#editor .role-toggle", "e => e.length")
    assert inline > 0, "a handful of roles should be directly visible"
    assert page.eval_on_selector_all("#editor .role-add", "e => e.length") == 0


def test_a_large_inventory_is_searched_not_walled(page_many):
    assert page_many.eval_on_selector_all("#editor .role-toggle", "e => e.length") == 0, \
        "forty roles must not be rendered as a wall of checkboxes"
    assert page_many.eval_on_selector_all("#editor .role-add", "e => e.length") > 0
    assert page_many.eval_on_selector_all("#editor .role-picker", "e => e.length") == 0, \
        "the picker stays closed until asked for"


def test_typing_filters_the_roles_and_enter_selects_the_first(page_many):
    page_many.click("#editor .draft:first-child .role-add")
    page_many.wait_for_selector("#role-search", timeout=5000)

    total = page_many.eval_on_selector_all("#editor .role-option", "e => e.length")
    page_many.fill("#role-search", "ROLE_1")
    page_many.wait_for_timeout(400)
    filtered = page_many.eval_on_selector_all("#editor .role-option", "e => e.map(x => x.innerText)")
    assert 0 < len(filtered) < total
    assert all("ROLE_1" in r for r in filtered), filtered

    page_many.fill("#role-search", "ROLE_07")
    page_many.wait_for_timeout(300)
    page_many.press("#role-search", "Enter")
    page_many.wait_for_timeout(400)
    chosen = page_many.evaluate("() => window.__textProbe.activeDraft().labels")
    # the draft already carried the role from its source span, so this is a second role
    assert "ROLE_07" in chosen, chosen


def test_a_chosen_role_can_be_removed_from_its_chip(page_many):
    before = page_many.evaluate("() => window.__textProbe.activeDraft().labels")
    assert before, "expected a role from the previous step"
    page_many.click("#editor .draft:first-child .role-chip-remove")
    page_many.wait_for_timeout(400)
    after = page_many.evaluate("() => window.__textProbe.activeDraft().labels")
    assert len(after) == len(before) - 1, (before, after)


def test_shortcuts_do_not_fire_while_typing_a_role_name(page_many):
    """'1' and 'd' are Take A and Drop. Typing them into the search box must not adjudicate."""
    if not page_many.query_selector("#role-search"):
        page_many.click("#editor .draft:first-child .role-add")
        page_many.wait_for_selector("#role-search", timeout=5000)
    position = page_many.inner_text("#position")
    page_many.click("#role-search")
    page_many.type("#role-search", "ROLE_1d")
    page_many.wait_for_timeout(400)
    assert page_many.input_value("#role-search") == "ROLE_1d"
    assert page_many.inner_text("#position") == position, "a shortcut fired while typing"
    assert page_many.errors == []


def test_no_search_field_means_no_stray_focus_trap(page_many):
    page_many.press("#role-search", "Escape")
    page_many.wait_for_timeout(400)
    assert page_many.eval_on_selector_all("#editor .role-picker", "e => e.length") == 0
    page_many.keyboard.press("n")                 # shortcuts work again once the picker is closed
    page_many.wait_for_timeout(300)
    assert page_many.errors == []


# ============================ P1.3 hierarchy and navigation ============================
def test_the_decision_sits_directly_under_the_evidence(page):
    """Actions belong with the thing they are about, not only in a bar at the bottom."""
    order = page.evaluate("""() => {
        const ids = ['conflict', 'actions', 'context', 'editor'];
        return ids.map(id => {
            const node = document.getElementById(id);
            return node ? [...document.querySelector('.main').children].indexOf(node) : -1;
        });
    }""")
    assert order == sorted(order) and -1 not in order, order
    # `is_visible` only means "in the DOM and not display:none" - a tall conflict can push
    # the actions below the fold and still pass that. Check they are actually on screen.
    box = page.evaluate("() => { const r = document.getElementById('actions').getBoundingClientRect(); return { bottom: r.bottom, height: window.innerHeight }; }")
    assert 0 < box["bottom"] <= box["height"] + 1, box
    # the bottom bar keeps navigation only, so nothing is duplicated
    assert page.eval_on_selector_all(".actionbar #act-a", "e => e.length") == 0
    assert page.is_visible(".actionbar #act-next")


def test_the_sidebar_states_each_number_once(page):
    """The earlier sidebar rendered the same two numbers four times."""
    text = page.inner_text("#progress-summary")
    assert text.count("resolved") == 1, text
    assert "remaining" in text and "deferred" in text
    assert page.eval_on_selector_all("#progress-summary .meter", "e => e.length") == 1


def test_conflict_types_are_clickable_filters_with_counts(page):
    rows = page.eval_on_selector_all("#progress-shapes .shape-row",
                                     "e => e.map(x => x.innerText.replace(/\\n/g, ' '))")
    assert rows and rows[0].startswith("All"), rows
    assert all("/" in r for r in rows), rows

    target = page.eval_on_selector_all(
        "#progress-shapes .shape-row",
        "e => e.map(x => x.innerText.split('\\n')[0]).filter(t => t !== 'All')")
    assert target, "no shape rows to click"
    page.click(f"#progress-shapes .shape-row:nth-child(2)")
    page.wait_for_timeout(500)
    chosen = page.input_value("#filter-select")
    assert chosen not in ("open",), "clicking a type row did not change the filter"
    assert page.eval_on_selector_all("#progress-shapes .shape-row.is-active", "e => e.length") == 1

    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_the_corpus_list_can_be_searched(page):
    total = page.eval_on_selector_all("#doc-list .doc-item", "e => e.length")
    page.fill("#case-search", "M1")
    page.wait_for_timeout(400)
    filtered = page.eval_on_selector_all("#doc-list .doc-item", "e => e.length")
    assert filtered <= total
    page.fill("#case-search", "zzz-no-such-document")
    page.wait_for_timeout(400)
    assert page.eval_on_selector_all("#doc-list .doc-item", "e => e.length") == 0
    assert "No document matches" in page.inner_text("#doc-list")
    page.fill("#case-search", "")
    page.wait_for_timeout(400)
    assert page.eval_on_selector_all("#doc-list .doc-item", "e => e.length") == total


def test_typing_in_the_case_search_does_not_fire_shortcuts(page):
    position = page.inner_text("#position")
    page.click("#case-search")
    page.type("#case-search", "1d")
    page.wait_for_timeout(400)
    assert page.input_value("#case-search") == "1d"
    assert page.inner_text("#position") == position
    page.fill("#case-search", "")
    page.wait_for_timeout(300)


def test_next_incomplete_moves_to_a_document_that_needs_work(page):
    before = page.input_value("#doc-select")
    page.click("#next-incomplete")
    page.wait_for_timeout(900)
    after = page.input_value("#doc-select")
    message = page.inner_text("#messages") if page.query_selector("#messages .msg") else ""
    # either it moved, or it explained why it could not — never a silent no-op
    assert after != before or "complete" in message.lower(), (before, after, message)
    assert after != before or message, "next-incomplete did nothing and said nothing"


# ============================ P1.4 descriptions come from the spans ============================
def test_the_description_names_what_these_two_annotators_actually_did(page):
    """A fixed sentence per shape says what KIND of disagreement this is, not what it IS.
    'One annotator marked structure the other did not' is true of every nesting conflict ever."""
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(500)

    seen = []
    for _ in range(12):
        shape = page.inner_text(".shape-name")
        hint = page.inner_text(".shape-hint")
        seen.append((shape, hint))
        assert hint.strip(), shape
        assert "Annotator" in hint or "Both" in hint, hint
        if page.is_disabled("#act-next"):
            break
        page.click("#act-next")
        page.wait_for_timeout(250)

    # the roles the annotators actually used must appear in at least one description
    roles = page.evaluate("() => window.__adjProject().labels")
    assert any(any(r in hint for r in roles) for _, hint in seen), seen
    # and the descriptions must not all be identical boilerplate
    assert len({h for _, h in seen}) > 1, seen

    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_a_role_clash_description_names_both_roles(page):
    page.select_option("#filter-select", "ROLE_CLASH")
    page.wait_for_timeout(500)
    if not page.eval_on_selector_all(".shape-name", "e => e.length"):
        pytest.skip("no role clash in this fixture")
    hint = page.inner_text(".shape-hint")
    conflict = page.evaluate("() => window.__adjRegion()")
    a_roles = {s["label"] for s in conflict["a_spans"]}
    b_roles = {s["label"] for s in conflict["b_spans"]}
    assert all(r in hint for r in a_roles | b_roles), (hint, a_roles, b_roles)
    assert "same text" in hint.lower()
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_the_deferred_filter_and_chip_use_the_new_name(page):
    options = page.eval_on_selector_all("#filter-select option", "e => e.map(x => x.value)")
    assert "deferred" in options and "flagged" not in options, options
    assert "Defer" in page.inner_text("#act-defer")


# --------------------------------------------------------- preflight and project chooser (P2)
def test_the_preflight_is_offered_not_forced(page):
    """A corpus where only one annotator used a role is normal; it must not block the queue."""
    assert page.is_hidden("#project-dialog")
    assert page.inner_text("#project-btn .pf-badge") == "2"
    assert page.locator("#project-btn .pf-badge.is-blocking").count() == 0


def test_the_preflight_screen_shows_how_the_files_were_read(page):
    page.click("#project-btn")
    page.wait_for_selector("#project-dialog[open]")
    body = page.inner_text("#project-body")
    assert "PREAMBLE" in body, "roles are discovered from the data and must be shown"
    assert "begin = begin" in body, "the reviewer must see which fields were read as offsets"
    assert "Annotator A (json) + Annotator B (json)" in body, "both annotators must be named as loaded"
    assert "from annotator A's files (annotator B's carry none)" in body, \
        "the text row must say why one annotator is named, not read as 'only A loaded'"
    page.click("#project-close")
    page.wait_for_selector("#project-dialog", state="hidden")


def test_a_bad_project_path_is_refused_without_losing_the_open_project(page):
    page.click("#project-btn")
    page.wait_for_selector("#project-dialog[open]")
    page.fill("#project-path", "/definitely/not/a/project")
    page.click("#project-open")
    page.wait_for_selector(".pf-open-msg.is-bad")
    assert "still reviewing" in page.inner_text("#project-open-msg")
    page.click("#project-close")
    page.wait_for_selector("#project-dialog", state="hidden")
    assert page.input_value("#doc-select") == "D1"
    assert page.errors == []


# ------------------------------------------------- structure diagram and final preview (P3)
def test_a_structural_conflict_shows_the_arrangement_at_a_glance(page):
    """Reading four offsets and reconstructing "the second sits inside the first" in your head
    is the slow part of a nesting conflict. The diagram states it."""
    page.select_option("#filter-select", "NESTING_DIFF")
    page.wait_for_timeout(400)
    if not page.locator("#conflict .shape-name").count():
        pytest.skip("no nesting conflict in this fixture")

    diagram = page.locator("#conflict .structure")
    assert diagram.count() == 1
    assert diagram.get_attribute("role") == "img"
    assert "nested" in (diagram.get_attribute("aria-label") or "")
    # A marks the whole region and one span inside it; B marks the whole region
    assert page.locator("#conflict .st-bar").count() == 3
    # a span contained in another cannot share its line
    assert page.locator('#conflict .st-bar[data-lane="1"]').count() == 1

    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_an_undecided_role_clash_gets_no_diagram(page):
    """Two identical bars would be decoration, and decoration in a queue costs attention. Once
    there IS a decision the picture earns its place, which is covered separately."""
    page.select_option("#filter-select", "ROLE_CLASH")
    page.wait_for_timeout(400)
    if not page.locator("#conflict .chip-open").count():
        pytest.skip("this role clash already carries a decision")
    assert page.locator("#conflict .structure").count() == 0
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_the_editor_previews_the_structure_that_will_be_saved(page):
    page.select_option("#filter-select", "NESTING_DIFF")
    page.wait_for_timeout(400)
    if not page.locator("#conflict .shape-name").count():
        pytest.skip("no nesting conflict in this fixture")
    # Another test may have left the editor open; "e" toggles, so ask rather than assume.
    if page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.wait_for_selector("#editor .draft", timeout=10000)
    page.wait_for_timeout(300)

    preview = page.locator("#editor .structure")
    assert preview.count() == 1
    assert "Final" in preview.inner_text()

    bar = page.locator("#editor .st-row-final .st-bar").first
    before = bar.get_attribute("style")
    end_input = page.locator("#editor .draft").first.locator("input[type=number]").nth(1)
    end_input.fill("350")
    page.keyboard.press("Tab")
    page.wait_for_timeout(400)
    after = page.locator("#editor .st-row-final .st-bar").first.get_attribute("style")
    assert before != after, "the preview must follow the offsets, not show a stale picture"

    if not page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.wait_for_timeout(300)
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------- a right-to-left corpus (P3)
AR_TEXT = ("نظرت المحكمة في أوراق الدعوى وفي المستندات المقدمة من الطرفين. " * 8
           + "وحكمت المحكمة بما هو مبين في منطوق هذا الحكم. " * 8)


@pytest.fixture(scope="module")
def app_ar(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui_ar")
    corpus = tmp / "corpus"
    # two Arabic documents with Arabic role names: a role clash first, then an agreement, a
    # boundary shift, a segmentation, another clash and a final agreement
    text = AR_TEXT
    for doc_id in ("Q1", "Q2"):
        write_doc(corpus / "annotator_A", doc_id, [
            (0, 60, "الديباجة"), (65, 120, "الوقائع"), (130, 250, "الأسباب"),
            (260, 380, "الأسباب"), (400, 500, "الطلبات"), (520, 600, "المنطوق"),
        ], text=text, sha=sha_of(text))
        write_doc(corpus / "annotator_B", doc_id, [
            (0, 60, "الوقائع"), (65, 120, "الوقائع"), (130, 240, "الأسباب"),
            (260, 320, "الأسباب"), (321, 380, "الأسباب"), (400, 500, "الأسباب"), (520, 600, "المنطوق"),
        ], sha=sha_of(text))
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "adjudicator", "serve", "--project", str(corpus),
         "--layer", "rhetorical", "--port", str(port), "--no-browser"],
        cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.15)
    else:
        raise RuntimeError(process.stdout.read().decode()[:800])
    yield {"url": f"http://127.0.0.1:{port}/", "out": corpus / "adjudication"}
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture(scope="module")
def page_ar(app_ar, page):
    ctx = page.context.browser.new_context()
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(app_ar["url"])
    pg.wait_for_selector(".card-head", timeout=15000)
    pg.select_option("#filter-select", "all")
    pg.wait_for_timeout(500)
    yield pg
    ctx.close()


def is_arabic(text):
    return any(0x0600 <= ord(ch) <= 0x06FF for ch in text or "")


def test_an_arabic_document_reads_right_to_left(page_ar):
    """Direction comes from the text, not from a setting: one build serves both corpora."""
    body = page_ar.locator("#conflict .span-text").first
    assert is_arabic(body.inner_text())
    assert body.get_attribute("dir") == "rtl"
    assert page_ar.errors == []


def test_arabic_role_names_are_shown_as_written(page_ar):
    roles = page_ar.eval_on_selector_all("#conflict .role", "e => e.map(x => x.textContent)")
    assert roles and all(is_arabic(r) for r in roles), roles


def test_the_diagram_runs_the_same_way_as_the_text_it_describes(page_ar, page):
    """An offset axis left to right under a right-to-left passage reads backwards."""
    for _ in range(12):
        if page_ar.locator("#conflict .structure").count():
            break
        page_ar.click("#act-next")
        page_ar.wait_for_timeout(250)
    else:
        pytest.skip("no structural conflict in this corpus")
    assert page_ar.locator("#conflict .structure").get_attribute("dir") == "rtl"

    page.select_option("#filter-select", "NESTING_DIFF")
    page.wait_for_timeout(400)
    assert page.locator("#conflict .structure").get_attribute("dir") == "ltr"
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_role_names_inside_a_description_are_direction_isolated(page_ar):
    """An Arabic label dropped into an English sentence drags the punctuation with it and the
    list reads back to front. U+2068/U+2069 stop that."""
    text = page_ar.eval_on_selector("#conflict .shape-hint", "e => e.textContent")
    assert "⁨" in text and "⁩" in text, repr(text)


# ------------------------------------------------------------------ accessibility (P3)
def test_every_visible_control_has_an_accessible_name(page):
    unnamed = page.evaluate("""() => {
      const name = (el) => (el.getAttribute('aria-label') || el.getAttribute('title')
        || (el.labels && el.labels.length ? el.labels[0].textContent : '')
        || el.textContent || '').trim();
      return [...document.querySelectorAll('button, a[href], input, select, textarea')]
        .filter((el) => el.offsetParent !== null && !name(el))
        .map((el) => el.outerHTML.slice(0, 100));
    }""")
    assert unnamed == [], unnamed


def test_nothing_claims_a_positive_tabindex(page):
    """A positive tabindex reorders the whole page and is never what anyone wants."""
    assert page.evaluate(
        "() => [...document.querySelectorAll('[tabindex]')]"
        ".map(e => e.tabIndex).filter(t => t > 0)") == []


def test_the_page_has_exactly_one_top_level_heading(page):
    headings = page.eval_on_selector_all("h1", "e => e.map(x => x.textContent.trim())")
    assert len(headings) == 1, headings
    assert headings[0]


def test_keyboard_focus_is_always_visible(page):
    """Tabbing is the primary way through this tool. Focus that cannot be seen is focus lost."""
    page.evaluate("() => document.activeElement.blur()")
    invisible = []
    for _ in range(25):
        page.keyboard.press("Tab")
        row = page.evaluate("""() => {
          const el = document.activeElement;
          if (!el || el === document.body) return null;
          const s = getComputedStyle(el);
          const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0)
            || (s.boxShadow && s.boxShadow !== 'none');
          return { tag: el.tagName, id: el.id, ring };
        }""")
        if row and not row["ring"]:
            invisible.append(row)
    page.evaluate("() => document.activeElement.blur()")
    assert invisible == [], invisible


def test_the_skip_link_jumps_straight_to_the_conflict(page):
    # blur() alone leaves the sequential focus starting point where it was, so the next Tab
    # continues from mid-page. Focusing the body itself moves the starting point back.
    page.evaluate("""() => {
      document.body.setAttribute('tabindex', '-1');
      document.body.focus();
      document.body.removeAttribute('tabindex');
    }""")
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.classList.contains('skip-link')")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.activeElement.id") == "conflict"
    page.evaluate("() => document.activeElement.blur()")


def test_the_diagram_labels_use_the_validated_text_pair(page):
    """White on the light-theme B orange is 3.2:1. The annotator hues are CVD-validated and must
    not move, so the label sits on the ink/surface pair the rest of the interface uses."""
    page.select_option("#filter-select", "NESTING_DIFF")
    page.wait_for_timeout(400)
    got = page.evaluate("""() => {
      const label = document.querySelector('#conflict .st-bar-label');
      if (!label) return null;
      const s = getComputedStyle(label);
      return { fg: s.color, bg: s.backgroundColor, bodyFg: getComputedStyle(document.body).color };
    }""")
    if got is None:
        pytest.skip("no diagram in this fixture")
    assert got["fg"] == got["bodyFg"]
    assert got["bg"] not in ("rgba(0, 0, 0, 0)", "transparent")
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


# ------------------------------------------------------------ the decided row (P3.5)
def test_the_diagram_shows_what_was_decided(page, app):
    """The reviewer's own result belongs on the same timeline as A and B; otherwise the only
    place it is ever drawn is inside the editor."""
    page.select_option("#filter-select", "ROLE_CLASH")
    page.wait_for_timeout(400)
    if not page.locator("#conflict .shape-name").count():
        pytest.skip("no role clash in this fixture")
    page.click("#act-a")
    page.wait_for_timeout(600)
    page.select_option("#filter-select", "settled")
    page.wait_for_timeout(500)

    rows = page.eval_on_selector_all("#conflict .st-row", "e => e.map(x => x.className)")
    assert any("st-row-final" in r for r in rows), rows
    assert page.locator("#conflict .st-row-final .st-bar").count() >= 1
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_spans_from_a_and_b_are_picked_into_the_final_one_by_one(page):
    """The common judgement is "A's second span plus B's first". That has to be one click each,
    not a rebuild of the extent and role by hand."""
    page.select_option("#filter-select", "NESTING_DIFF")
    page.wait_for_timeout(400)
    if not page.locator("#conflict .shape-name").count():
        pytest.skip("no nesting conflict in this fixture")
    if page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.wait_for_selector("#editor .source-toggle", timeout=10000)

    toggles = page.locator("#editor .source-toggle")
    assert toggles.count() == 3, "one toggle per source span: A1, A2, B1"

    # a span that is NOT already in the final, so the toggle has somewhere to travel
    want = page.evaluate("""() => {
      const t = [...document.querySelectorAll('#editor .source-toggle')]
        .find((x) => x.getAttribute('aria-pressed') === 'false');
      return t && { source: t.dataset.source, begin: +t.dataset.begin,
                    end: +t.dataset.end, label: t.dataset.label };
    }""")
    assert want, "every source span was already chosen; nothing left to pick"
    sel = f'#editor .source-toggle[data-source="{want["source"]}"]'
    matching = ("() => window.__adjDrafts().filter(d => d.begin === %d && d.end === %d"
                " && d.labels.length === 1 && d.labels[0] === %s).length"
                % (want["begin"], want["end"], json.dumps(want["label"])))

    before = page.evaluate(matching)
    page.locator(sel).click()
    page.wait_for_timeout(300)
    assert page.evaluate(matching) == before + 1, "clicking a source must add exactly that span"
    assert page.get_attribute(sel, "aria-pressed") == "true"

    page.locator(sel).click()
    page.wait_for_timeout(300)
    assert page.evaluate(matching) == before, "clicking again must take it back out"
    assert page.get_attribute(sel, "aria-pressed") == "false"

    if not page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_the_project_dialog_can_simply_be_closed(page):
    """Opened to look something up, it needs a way out that is not "Start reviewing"."""
    page.click("#project-btn")
    page.wait_for_selector("#project-dialog[open]")
    assert page.is_visible("#project-dismiss")
    page.click("#project-dismiss")
    page.wait_for_selector("#project-dialog", state="hidden")

    # and clicking the backdrop outside the panel closes it too
    page.click("#project-btn")
    page.wait_for_selector("#project-dialog[open]")
    # a <dialog>'s own rect is the panel; the backdrop is its pseudo-element covering the
    # viewport, and a click there targets the dialog itself
    page.mouse.click(5, 5)
    page.wait_for_selector("#project-dialog", state="hidden")
    assert page.errors == []


def test_a_digit_typed_into_an_offset_box_stays_in_the_box(page):
    """The quick actions are 1-4, and those are also the characters an offset is made of.
    Letting shortcuts through a number box so that C would work there let digits through too."""
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(400)
    if page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.wait_for_selector("#editor .draft", timeout=10000)

    before = page.evaluate("() => window.__adjRegion().conflict_id")
    box = page.locator("#editor .draft").first.locator("input[type=number]").first
    box.click()
    box.fill("")
    page.keyboard.type("1")
    page.wait_for_timeout(300)

    assert box.input_value() == "1", "the digit must reach the field"
    assert page.evaluate("() => window.__adjRegion().conflict_id") == before, \
        "typing 1 fired Take A instead of entering an offset"

    # a letter shortcut must still reach the interface from the same box
    was = page.evaluate("() => document.getElementById('context').hidden")
    page.keyboard.press("c")
    page.wait_for_timeout(500)
    assert page.evaluate("() => document.getElementById('context').hidden") != was

    page.keyboard.press("c")
    page.wait_for_timeout(300)
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


def test_the_document_context_can_be_opened_from_the_conflict_itself(page):
    """A quiet button pinned to the bottom-left of the window is not where a reviewer working at
    the top of a 1100px card looks for "show me this in the document"."""
    page.select_option("#filter-select", "all")
    page.wait_for_timeout(400)
    if not page.evaluate("() => document.getElementById('context').hidden"):
        page.click("#act-context")
        page.wait_for_timeout(400)

    opener = page.locator("#conflict button#card-context")
    assert opener.count() == 1, "the conflict card must offer the document view"
    assert "context" in opener.inner_text().lower()
    opener.click()
    page.wait_for_timeout(700)
    assert not page.evaluate("() => document.getElementById('context').hidden")
    assert page.locator("#conflict button#card-context").inner_text().lower().startswith("hide")

    page.locator("#conflict button#card-context").click()
    page.wait_for_timeout(500)
    assert page.evaluate("() => document.getElementById('context').hidden")
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)


# ================================================== queue, notes and provenance integrity
def test_defer_advances_to_the_next_conflict_under_the_default_filter(page_ar):
    """A deferred conflict stays listed under "Needs a decision", so the queue used to sit on
    it after S was pressed, although the quick keys are documented as advancing."""
    page_ar.select_option("#filter-select", "open")
    page_ar.wait_for_timeout(400)
    before = page_ar.evaluate("() => window.__adjRegion().conflict_id")
    page_ar.keyboard.press("s")
    page_ar.wait_for_timeout(800)
    assert "Deferred" in page_ar.inner_text("#messages")
    after = page_ar.evaluate("() => window.__adjRegion().conflict_id")
    assert after != before, "Defer must move on to the next conflict"
    page_ar.select_option("#filter-select", "all")
    page_ar.wait_for_timeout(300)


def test_a_note_never_leaks_into_another_conflicts_decision(page_ar, app_ar):
    """The note box used to survive, hidden, after the editor closed, and every later quick-key
    decision - on any conflict, in any document - was saved with that note."""
    page_ar.select_option("#filter-select", "all")
    page_ar.wait_for_timeout(400)
    doc = page_ar.input_value("#doc-select")
    x = page_ar.evaluate("() => window.__adjRegion().conflict_id")
    if page_ar.evaluate("() => document.getElementById('editor').hidden"):
        page_ar.keyboard.press("e")
    page_ar.wait_for_selector("#decision-note", timeout=5000)
    page_ar.fill("#decision-note", "NOTE WRITTEN FOR X ONLY")
    page_ar.click("#save-decision")
    page_ar.wait_for_timeout(800)
    page_ar.keyboard.press("n")
    page_ar.wait_for_timeout(400)
    y = page_ar.evaluate("() => window.__adjRegion().conflict_id")
    assert y != x
    page_ar.keyboard.press("1")                    # quick key, editor closed
    page_ar.wait_for_timeout(800)
    on_disk = {d["conflict_id"]: d for d in decisions_on_disk(app_ar, doc)}
    assert on_disk[x]["note"] == "NOTE WRITTEN FOR X ONLY"
    assert on_disk[y]["note"] == "", f"the note leaked into {y}: {on_disk[y]['note']!r}"
    # and the editor opened on a third conflict starts empty
    page_ar.keyboard.press("n")
    page_ar.wait_for_timeout(400)
    page_ar.keyboard.press("e")
    page_ar.wait_for_selector("#decision-note", timeout=5000)
    assert page_ar.input_value("#decision-note") == ""
    page_ar.keyboard.press("e")
    page_ar.wait_for_timeout(300)


def test_a_quick_key_keeps_the_note_a_decision_already_carries(page_ar, app_ar):
    page_ar.select_option("#filter-select", "all")
    page_ar.wait_for_timeout(400)
    doc = page_ar.input_value("#doc-select")
    noted = [d for d in decisions_on_disk(app_ar, doc) if d["note"] == "NOTE WRITTEN FOR X ONLY"]
    assert noted, "expected the note saved by the previous test"
    target = noted[0]["conflict_id"]
    # walk to that conflict and re-decide it with a quick key
    for _ in range(40):
        if page_ar.evaluate("() => window.__adjRegion().conflict_id") == target:
            break
        page_ar.keyboard.press("n")
        page_ar.wait_for_timeout(150)
    assert page_ar.evaluate("() => window.__adjRegion().conflict_id") == target
    page_ar.keyboard.press("1")
    page_ar.wait_for_timeout(800)
    saved = {d["conflict_id"]: d for d in decisions_on_disk(app_ar, doc)}[target]
    assert saved["note"] == "NOTE WRITTEN FOR X ONLY", "a quick key must not wipe the note"


def test_picking_a_and_b_on_one_extent_cannot_be_saved_as_the_reviewers_own_judgement(page, app):
    """Toggling A's span and B's span on the same text produced two drafts that the save path
    merged into one multi-role span whose content matched neither annotator - so it went to
    the server as a reviewer-authored judgement and the "never silently merge" rule never saw
    it. The editor already promised this would be refused; now it is."""
    page.select_option("#filter-select", "ROLE_CLASH")
    page.wait_for_timeout(400)
    if not page.locator("#conflict .shape-name").count():
        pytest.skip("no role clash in this fixture")
    conflict_id = page.evaluate("() => window.__adjRegion().conflict_id")
    if page.evaluate("() => document.getElementById('editor').hidden"):
        page.keyboard.press("e")
    page.wait_for_selector("#editor .source-toggle", timeout=10000)
    for toggle in page.locator('#editor .source-toggle[aria-pressed="false"]').all():
        toggle.click()
        page.wait_for_timeout(250)
    assert page.locator('#editor .source-toggle[aria-pressed="true"]').count() == 2
    assert "refused" in page.inner_text("#editor").lower()      # the warning names the outcome

    before = [d for d in decisions_on_disk(app) if d["conflict_id"] == conflict_id]
    page.click("#save-decision")
    page.wait_for_timeout(800)
    message = page.inner_text("#messages")
    assert "Not saved" in message and "Both roles" in message, message
    after = [d for d in decisions_on_disk(app) if d["conflict_id"] == conflict_id]
    assert after == before, "the refused save must not have changed the log"
    assert not any(d["decision_type"] == "MULTI_ROLE" for d in after)

    page.click("#discard-edit")
    page.wait_for_timeout(300)
    page.select_option("#filter-select", "open")
    page.wait_for_timeout(300)
