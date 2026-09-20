/** Wiring: load, render, decide, navigate. */
import { api } from './api.js';
import { el, replace } from './dom.js';
import { renderMarkdown } from './markdown.js';
import {
  state, subscribe, emit, visibleConflicts, currentConflict, currentDecision, move, timer,
} from './state.js';
import { renderConflict } from './views/conflict.js';
import { renderContext } from './views/context.js';
import {
  renderEditor, quickDrafts, toWire, keepBothIsSafe, bothRolesIsAvailable, mergedDisagreements,
} from './views/editor.js';
import { renderPreflight } from './views/preflight.js';
import { renderSummary } from './views/summary.js';
import { renderProgress, renderShapes, renderDocList } from './views/sidebar.js';
import { selectionOffsets } from './text.js';

const $ = (id) => document.getElementById(id);
let boundConflictId = null;          // which conflict the current drafts belong to

// ------------------------------------------------------------------ messages
function say(kind, text, sticky = false) {
  const node = el('p', { class: `msg msg-${kind}`, text });
  replace($('messages'), node);
  if (!sticky && kind === 'ok') {
    setTimeout(() => { if (node.isConnected) node.remove(); }, 3200);
  }
}
const clearMessages = () => replace($('messages'));

// ------------------------------------------------------------------ drafts
function defaultDrafts(conflict, decision) {
  if (decision && decision.spans?.length) {
    return decision.spans.map((s) => ({
      begin: s.begin, end: s.end, labels: [...s.labels], via: s.origin || 'reviewer' }));
  }
  const seed = conflict.a_spans[0] || conflict.b_spans[0];
  return [{ begin: seed.begin, end: seed.end, labels: [seed.label],
            via: conflict.a_spans[0] ? 'A' : 'B' }];
}

/** Rebind drafts whenever the conflict changes, so the editor can never write another
 *  conflict's offsets. Edits in progress on the SAME conflict are preserved. */
function ensureDrafts() {
  const conflict = currentConflict();
  if (!conflict) { boundConflictId = null; state.draft = []; replace($('editor')); return; }
  if (boundConflictId !== conflict.conflict_id) {
    boundConflictId = conflict.conflict_id;
    state.draft = defaultDrafts(conflict, currentDecision());
    state.activeDraft = 0;
    // The editor's nodes - the note box above all - belong to the conflict they were built
    // for. Left in place, a note typed for one conflict is read back for the next.
    replace($('editor'));
  }
}

const editorHandlers = {
  setActive(index) { state.activeDraft = index; emit(); },
  setOffsets(index, begin, end) {
    Object.assign(state.draft[index], { begin, end });
    state.activeDraft = index;
    emit();
  },
  toggleRole(index, role, on) {
    const draft = state.draft[index];
    if (state.rolePicker.index === index) state.rolePicker = { index, query: '' };
    draft.labels = on
      ? [...new Set([...draft.labels, role])]
      : draft.labels.filter((r) => r !== role);
    state.activeDraft = index;
    emit();
  },
  /** Put a source span in the final set, or take it back out. The role comes with it. */
  toggleSource(span) {
    const at = state.draft.findIndex((d) => d.begin === span.begin && d.end === span.end
      && d.labels.length === 1 && d.labels[0] === span.label);
    if (at >= 0) {
      if (state.draft.length === 1) {
        return say('warn', 'That is the only span left. Use Drop if this passage should carry '
                         + 'no annotation at all.');
      }
      state.draft.splice(at, 1);
      state.activeDraft = Math.max(0, Math.min(state.activeDraft, state.draft.length - 1));
    } else {
      state.draft.push({ begin: span.begin, end: span.end, labels: [span.label],
                         via: span.side || 'reviewer' });
      state.activeDraft = state.draft.length - 1;
    }
    return emit();
  },
  remove(index) {
    state.draft.splice(index, 1);
    state.activeDraft = 0;
    emit();
  },
  openPicker(index) { state.rolePicker = { index, query: '' }; state.activeDraft = index; emit(); },
  closePicker() { state.rolePicker = { index: null, query: '' }; emit(); },
  // no emit: the picker repaints its own list, so the input survives the keystroke
  setQuery(query) { state.rolePicker.query = query; },
  addSpan() {
    const last = state.draft[state.draft.length - 1];
    state.draft.push({ begin: last.begin, end: last.end, labels: [], via: 'reviewer' });
    state.activeDraft = state.draft.length - 1;
    emit();
  },
  save() { commit(state.draft, null, { advance: false }); },
  discard() {
    boundConflictId = null;        // drafts rebuild from the stored decision
    state.editorOpen = false;
    clearMessages();
    emit();
  },
};

/** Two drafts on the same characters are one annotation carrying both roles. Sending them
 *  separately makes the reviewer's single judgement look like two unrelated claims. */
function mergeByExtent(drafts) {
  const out = [];
  const seen = new Map();
  for (const draft of drafts) {
    const key = `${draft.begin}:${draft.end}`;
    if (!seen.has(key)) {
      seen.set(key, out.length);
      out.push({ begin: draft.begin, end: draft.end, labels: [...draft.labels] });
      continue;
    }
    const target = out[seen.get(key)];
    for (const role of draft.labels) {
      if (!target.labels.includes(role)) target.labels.push(role);
    }
  }
  return out;
}

/** Move one draft's boundary and say exactly what it now covers. */
function applyBoundary(index, begin, end, how) {
  const draft = state.draft[index];
  if (!draft || !(begin < end)) return;
  const unchanged = draft.begin === begin && draft.end === end;
  Object.assign(draft, { begin, end });
  state.activeDraft = index;
  const shown = state.doc.text.slice(begin, end);
  say('ok', unchanged
    ? `Span ${index + 1} is already on ${how} boundaries.`
    : `Span ${index + 1} → ${begin}–${end}: “${shown.length > 60
        ? `${shown.slice(0, 57)}…` : shown}”`);
  emit();
}

/** Selecting document text sets the ACTIVE span's boundary — never some other span's. */
document.addEventListener('mouseup', () => {
  if (!state.editorOpen || !state.draft.length) return;
  const offsets = selectionOffsets();
  if (!offsets) return;
  applyBoundary(state.activeDraft, offsets[0], offsets[1], 'selection');
});

// ------------------------------------------------------------------ committing
async function commit(drafts, intent = null, { advance = true } = {}) {
  const conflict = currentConflict();
  if (!conflict || state.busy) return;

  if (!intent) {
    if (!drafts.length) return say('error', 'Add a span, or use Drop to remove this annotation.');
    const bad = drafts.find((d) => !d.labels.length);
    if (bad) return say('error', 'Every span needs at least one role.');
    const range = drafts.find((d) => !(Number.isInteger(d.begin) && Number.isInteger(d.end))
      || d.begin < 0 || d.begin >= d.end || d.end > state.doc.text.length);
    if (range) return say('error', `Offsets must satisfy 0 ≤ start < end ≤ ${state.doc.text.length}.`);
    // A span picked from A and a span picked from B on the same characters, with roles that
    // match neither annotator once combined, are the disagreement itself. Merging them before
    // sending would present it to the server as the reviewer's own multi-role judgement.
    const fused = mergedDisagreements(drafts, conflict);
    if (fused.length) {
      return say('error', `Not saved. Spans picked from A and from B cover the same text `
        + `(${fused.map((k) => k.replace(':', '–')).join(', ')}) with different roles, so saving `
        + 'them together would merge the disagreement away. Use Both roles to record that the '
        + 'passage performs both, or choose one side.', true);
    }
  }

  // The note belongs to the editor session for THIS conflict. With the editor closed, a quick
  // key keeps whatever note the stored decision already carries rather than wiping it.
  const note = state.editorOpen
    ? ($('decision-note')?.value || '')
    : (currentDecision()?.note || '');

  state.busy = true;
  emit();
  try {
    const result = await api.decide({
      doc_id: state.docId,
      conflict_id: conflict.conflict_id,
      spans: intent ? [] : mergeByExtent(drafts).map((d) => toWire(d, conflict)),
      intent,
      note,
      seconds: timer.read(conflict.conflict_id),
      visits_delta: timer.visits(conflict.conflict_id),
    });
    state.doc.decisions[conflict.conflict_id] = result.decision;
    state.doc.progress = result.progress;
    const row = state.documents.find((d) => d.doc_id === state.docId);
    if (row) Object.assign(row, result.progress);

    say('ok', intent === 'DROP' ? 'Dropped — excluded from the resolved layer.'
      : intent === 'DEFER' ? 'Deferred — this document is not complete until it is resolved.'
      : `Saved as ${result.decision.decision_type.replace(/_/g, ' ')}.`);

    markSummaryButton(true);
    timer.settle(conflict.conflict_id);
    boundConflictId = null;
    if (advance) {
      state.pinned = null;
      const list = visibleConflicts();
      // Under "Needs a decision" a decided conflict leaves the list, so the index already
      // points at the next one; a DEFERRED conflict stays listed, so it has to be stepped past.
      if (state.index >= list.length) state.index = Math.max(0, list.length - 1);
      else if (state.filter !== 'open' || intent === 'DEFER') move(1);
      state.editorOpen = false;
    } else {
      // Deliberate resolution: hold this conflict so the reviewer can see what was saved on the
      // same timeline as A and B. Next / Prev moves on when they are ready.
      state.pinned = conflict.conflict_id;
      state.editorOpen = false;
      const list = visibleConflicts();
      const at = list.findIndex((c) => c.conflict_id === conflict.conflict_id);
      if (at >= 0) state.index = at;
    }
  } catch (error) {
    say('error', `Not saved. ${error.message}`, true);
  } finally {
    state.busy = false;
    emit();
  }
}

// ------------------------------------------------------------------ rendering
let rendering = false;
let renderAgain = false;
/** Never re-enter: an emit raised while the old nodes are torn down (a change handler on a
 *  focused offset box, for example) is honoured by one more pass once this one is done. */
function render() {
  if (rendering) { renderAgain = true; return; }
  rendering = true;
  try {
    renderOnce();
  } finally {
    rendering = false;
  }
  if (renderAgain) { renderAgain = false; render(); }
}

function renderOnce() {
  ensureDrafts();
  const conflict = currentConflict();
  const list = visibleConflicts();
  const decision = currentDecision();
  const roles = state.project?.labels || [];

  renderProgress($('progress-summary'), state.doc?.progress, state.docId);
  renderShapes($('progress-shapes'), state.doc?.progress, state.filter, setFilter);
  renderDocList($('doc-list'), $('corpus-summary'), state.documents, state.docId,
                openDocument, state.docQuery);

  if (!conflict) {
    replace($('conflict'), el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('span', { class: 'shape-name', text: 'Nothing to show' })),
      el('p', { style: { padding: '16px' },
                text: state.doc ? 'No conflicts match the current filter in this document.'
                                : 'Loading…' })));
    $('editor').hidden = true;
    $('context').hidden = true;
    $('position').textContent = '0 / 0';
    for (const id of ['act-a', 'act-b', 'act-both', 'act-roles', 'act-edit', 'act-drop',
                      'act-defer', 'act-prev', 'act-next', 'act-context']) $(id).disabled = true;
    return;
  }

  timer.focus(conflict.conflict_id);
  $('position').textContent = `${state.index + 1} / ${list.length}`;
  replace($('conflict'), renderConflict(conflict, state.doc, decision, state.contextOpen));

  $('context').hidden = !state.contextOpen;
  if (state.contextOpen) {
    replace($('context'), renderContext(conflict, state.doc, state.contextRadius));
    $('context-widen').onclick = () => { state.contextRadius *= 3; emit(); };
    $('context-full').onclick = () => { state.contextRadius = state.doc.text.length; emit(); };
  }

  $('editor').hidden = !state.editorOpen;
  if (!state.editorOpen) replace($('editor'));      // nothing lingers from a closed editor
  if (state.editorOpen) {
    const note = $('decision-note')?.value;
    const search = $('role-search');
    const caret = search ? search.selectionStart : null;
    replace($('editor'), renderEditor(conflict, state.doc, state.draft, state.activeDraft,
                                      roles, state.rolePicker, editorHandlers));
    // the editor is rebuilt on every change, so put the caret back where the reviewer left it
    const reopened = $('role-search');
    if (reopened) {
      reopened.focus();
      const at = caret === null ? reopened.value.length : caret;
      reopened.setSelectionRange(at, at);
    }
    if (note !== undefined) $('decision-note').value = note;
    else if (decision?.note) $('decision-note').value = decision.note;
  }

  const busy = state.busy;
  $('act-a').disabled = busy || !conflict.a_spans.length;
  $('act-b').disabled = busy || !conflict.b_spans.length;
  $('act-both').disabled = busy || !keepBothIsSafe(conflict);
  $('act-roles').disabled = busy || !bothRolesIsAvailable(conflict);
  $('act-edit').disabled = busy;
  $('act-drop').disabled = busy;
  $('act-defer').disabled = busy;
  $('act-context').disabled = busy;
  $('act-prev').disabled = busy || state.index <= 0;
  $('act-next').disabled = busy || state.index >= list.length - 1;

  $('act-both').title = keepBothIsSafe(conflict) ? 'Keep every span from both annotators'
    : 'Unavailable here: both annotators marked the same text with different roles, so keeping '
      + 'both would merge a disagreement. Choose a side, or use Both roles.';
}
subscribe(render);

/** Read-only view of the timing ledger, for tests and for anyone auditing what is measured. */
window.__adjProject = () => state.project;
window.__adjRegion = () => currentConflict();
window.__adjActive = () => state.activeDraft;
window.__adjDrafts = () => state.draft.map((d) => ({ ...d, labels: [...d.labels] }));
window.__textProbe = {
  selection: () => selectionOffsets(),
  activeDraft: () => (state.draft[state.activeDraft] || null),
};
window.__timerProbe = () => {
  const conflict = currentConflict();
  if (!conflict) return null;
  return {
    conflict_id: conflict.conflict_id,
    visits: timer.visits(conflict.conflict_id),
    seconds: timer.read(conflict.conflict_id),
  };
};

// ------------------------------------------------------------------ loading
async function openDocument(docId) {
  try {
    state.docId = docId;
    state.doc = await api.document(docId);
    state.index = 0;
    boundConflictId = null;
    state.editorOpen = false;
    state.contextOpen = false;
    state.contextRadius = 400;
    timer.clear();
    $('doc-select').value = docId;
    clearMessages();
    emit();
  } catch (error) {
    say('error', `Cannot open ${docId}: ${error.message}`, true);
  }
}

async function refreshDocuments() {
  const { documents } = await api.documents();
  state.documents = documents;
  markSummaryButton(false);
  replace($('doc-select'), documents.map((d) => el('option', {
    value: d.doc_id, text: `${d.doc_id}${d.complete ? ' ✓' : ` — ${d.settled}/${d.conflicts}`}`,
  })));
  const broken = documents.filter((d) => d.error);
  if (broken.length) {
    say('error', `${broken.length} document(s) have an unreadable decision log, starting with `
      + `${broken[0].doc_id}.`, true);
  }
}

async function loadGuide() {
  try {
    const { markdown, path } = await api.guide();
    replace($('guide-body'), markdown
      ? renderMarkdown(markdown)
      : el('p', { text: 'No guidelines file is configured. Start the server with --guide '
                        + 'path/to/GUIDELINES.md to show them here.' }));
    if (path) $('guide-body').append(el('p', { class: 'offsets',
      style: { marginBlockStart: '16px' }, text: `source: ${path} (re-read on every open)` }));
  } catch {
    replace($('guide-body'), el('p', { text: 'Guidelines could not be loaded.' }));
  }
}

// ------------------------------------------------------------------ controls
$('doc-select').onchange = (e) => { e.target.blur(); openDocument(e.target.value); };
/** One place that changes the filter, whether it came from the dropdown or a sidebar row. */
function setFilter(value) {
  state.pinned = null;
  state.filter = value;
  state.index = 0;
  boundConflictId = null;
  $('filter-select').value = value;
  emit();
}
$('filter-select').onchange = (e) => { e.target.blur(); setFilter(e.target.value); };
$('case-search').oninput = (e) => {
  // repaint only the document list: re-rendering everything would drop focus mid-word
  state.docQuery = e.target.value;
  renderDocList($('doc-list'), $('corpus-summary'), state.documents, state.docId,
                openDocument, state.docQuery);
};
$('next-incomplete').onclick = () => {
  const elsewhere = state.documents.filter(
    (d) => !d.complete && !d.error && d.doc_id !== state.docId);
  if (elsewhere.length) return openDocument(elsewhere[0].doc_id);
  // Say why nothing happened rather than silently reloading the document already open.
  const here = state.documents.find((d) => d.doc_id === state.docId);
  say('ok', here && !here.complete
    ? 'This is the only document still incomplete.'
    : 'Every document in this corpus is complete.');
};
$('layer-select').onchange = async (e) => {
  e.target.blur();
  try {
    const result = await api.setLayer(e.target.value);
    state.project.layer = result.layer;
    state.project.labels = result.labels;
    await refreshDocuments();
    await openDocument(state.docId && state.documents.some((d) => d.doc_id === state.docId)
      ? state.docId : state.documents[0].doc_id);
    say('ok', `Now reviewing the ${result.layer} layer (${result.labels.length} roles).`);
  } catch (error) { say('error', error.message, true); }
};

$('act-a').onclick = () => commit(quickDrafts.takeA(currentConflict()));
$('act-b').onclick = () => commit(quickDrafts.takeB(currentConflict()));
$('act-both').onclick = () => commit(quickDrafts.keepBoth(currentConflict()));
$('act-roles').onclick = () => commit(quickDrafts.bothRoles(currentConflict()));
$('act-drop').onclick = () => commit([], 'DROP');
$('act-defer').onclick = () => commit([], 'DEFER');
/** Bring a panel the reviewer just asked for into view.
 *
 * On real legal spans the conflict card runs to about 1100px, so a panel that opens underneath
 * it lands several hundred pixels below the fold and reads as "nothing happened". */
function reveal(id) {
  const node = $(id);
  if (!node || node.hidden) return;
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  node.scrollIntoView({ block: 'start', behavior: still ? 'auto' : 'smooth' });
}

$('act-edit').onclick = () => {
  state.editorOpen = !state.editorOpen;
  emit();
  if (state.editorOpen) reveal('editor');
};
$('act-context').onclick = () => {
  state.contextOpen = !state.contextOpen;
  emit();
  if (state.contextOpen) reveal('context');
};
/** Moving on releases the conflict held on screen after a deliberate save, keeping the
 *  reviewer on whichever conflict they actually moved to. */
function step(delta) {
  if (!move(delta)) return;
  if (state.pinned) {
    const landed = visibleConflicts()[state.index];
    state.pinned = null;
    const list = visibleConflicts();
    const at = landed ? list.findIndex((c) => c.conflict_id === landed.conflict_id) : -1;
    state.index = at >= 0 ? at : Math.min(state.index, Math.max(0, list.length - 1));
  }
  emit();
}

$('act-next').onclick = () => step(1);
$('act-prev').onclick = () => step(-1);

$('guide-toggle').onclick = async () => {
  const panel = $('guide-panel');
  panel.hidden = !panel.hidden;
  $('guide-toggle').setAttribute('aria-expanded', String(!panel.hidden));
  // Re-read on every open. The server already re-reads the file per request; fetching once at
  // startup meant an edit made during a session never appeared, contradicting the README.
  if (!panel.hidden) await loadGuide();
};
$('guide-close').onclick = () => {
  $('guide-panel').hidden = true;
  $('guide-toggle').setAttribute('aria-expanded', 'false');
  $('guide-toggle').focus();
};

$('theme-toggle').onclick = () => {
  const root = document.documentElement;
  const dark = root.getAttribute('data-theme') === 'dark';
  root.setAttribute('data-theme', dark ? 'light' : 'dark');
  try { localStorage.setItem('spanarbiter-theme', dark ? 'light' : 'dark'); } catch { /* private mode */ }
};

$('export-btn').onclick = async () => {
  $('export-btn').disabled = true;
  try {
    const result = await api.exportAll({ roundtrip: true, only_complete: true });
    say('ok', `Exported ${result.written.length} document(s), ${result.span_count} spans to `
      + `${result.directory}. ${result.skipped.length} still incomplete.`, true);
  } catch (error) {
    say('error', `Export failed: ${error.message}`, true);
  } finally { $('export-btn').disabled = false; }
};

// ------------------------------------------------------------------ keyboard
const KEYS = {
  1: 'act-a', 2: 'act-b', 3: 'act-both', 4: 'act-roles',
  e: 'act-edit', d: 'act-drop', s: 'act-defer', c: 'act-context',
  n: 'act-next', p: 'act-prev', ArrowRight: 'act-next', ArrowLeft: 'act-prev',
};
document.addEventListener('keydown', (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  // A shortcut pressed while a panel is open must not act on the queue behind it.
  if (document.querySelector('dialog[open]')) return;
  const target = event.target;
  if (target instanceof HTMLElement
      && (target.tagName === 'TEXTAREA' || target.isContentEditable
          || (target.tagName === 'INPUT'
              && !['checkbox', 'number'].includes(target.type))
          || target.tagName === 'SELECT')) return;
  // In an offset box the letter shortcuts (C, E, N…) should still reach the interface, but a
  // digit is the thing being typed — and the quick actions are 1-4.
  if (target instanceof HTMLInputElement && target.type === 'number'
      && /^[-+.,0-9eE]$/.test(event.key)) return;
  const id = KEYS[event.key] || KEYS[event.key.toLowerCase?.()];
  if (!id) return;
  const button = $(id);
  if (!button || button.disabled) return;
  event.preventDefault();
  button.click();
});

// ------------------------------------------------------------------ project
/** Load (or reload) everything that depends on which project is open. */
async function applyProject(payload) {
  state.project = payload;
  replace($('layer-select'), (payload.available_layers || []).map((name) => el('option', {
    value: name, text: `${name} (${payload.layers?.[name] ?? 0})`,
    selected: name === payload.layer,
  })));
  markProjectButton(payload);
  await loadGuide();
  await refreshDocuments();
  if (state.documents.length) await openDocument(state.documents[0].doc_id);
  emit();
}

/** Keep the count of things worth checking visible without stealing the reviewer's attention. */
function markProjectButton(project) {
  const button = $('project-btn');
  const blocking = project.blocking?.length || 0;
  const notes = blocking + (project.warnings?.length || 0);
  replace(button, 'Project',
    notes ? el('span', { class: `pf-badge${blocking ? ' is-blocking' : ''}`,
                         text: String(notes) }) : null);
  button.setAttribute('aria-label', notes
    ? `Project — ${notes} thing${notes === 1 ? '' : 's'} to check`
    : 'Project details');
}

function showPreflight() {
  renderPreflight($('project-body'), state.project);
  replace($('project-open-msg'));
  const dialog = $('project-dialog');
  if (!dialog.open) {
    timer.pause();          // the reviewer is reading the preflight, not judging a conflict
    dialog.showModal();
  }
}

$('project-btn').onclick = showPreflight;
$('project-dialog').addEventListener('close', () => timer.resume());
$('project-dismiss').onclick = () => $('project-dialog').close();
// Clicking the dimmed area outside the panel closes it, as a modal is expected to. The target
// is the <dialog> itself only when the click landed on the backdrop, never inside the form.
$('project-dialog').addEventListener('click', (event) => {
  if (event.target === $('project-dialog')) $('project-dialog').close();
});

// ------------------------------------------------------------------ summary
/** The button stays available throughout: the report says "Provisional" until every conflict
 *  is decided, and lights up at the moment the last one is. */
function markSummaryButton(announce) {
  const docs = state.documents || [];
  const done = docs.length > 0 && docs.every((d) => d.complete && !d.error);
  const button = $('summary-btn');
  const was = button.classList.contains('is-ready');
  button.classList.toggle('is-ready', done);
  button.title = done ? 'Every conflict is decided: the summary is final'
    : 'Progress and outcomes so far; final once every conflict is decided';
  if (done && !was && announce) {
    say('ok', 'Every conflict is decided. Export and Summary are ready.', true);
  }
}

async function showSummary() {
  const dialog = $('summary-dialog');
  replace($('summary-body'), el('p', { class: 'sm-small', text: 'Computing from the stored decisions…' }));
  if (!dialog.open) {
    timer.pause();          // reading a report is not judging a conflict
    dialog.showModal();
  }
  try {
    renderSummary($('summary-body'), await api.summary());
  } catch (error) {
    replace($('summary-body'), el('p', { class: 'msg msg-error',
      text: `The summary could not be computed: ${error.message}` }));
  }
}

$('summary-btn').onclick = showSummary;
$('summary-dialog').addEventListener('close', () => timer.resume());
$('summary-dismiss').onclick = () => $('summary-dialog').close();
$('summary-dialog').addEventListener('click', (event) => {
  if (event.target === $('summary-dialog')) $('summary-dialog').close();
});

function openMessage(text, tone) {
  const node = $('project-open-msg');
  node.className = `pf-open-msg${tone ? ` is-${tone}` : ''}`;
  replace(node, text);
}

async function openProjectFromInput() {
  const root = $('project-path').value.trim();
  if (!root) return openMessage('Type the path of a project folder.', 'bad');
  openMessage('Loading…');
  try {
    const loaded = await api.openProject(root);
    await applyProject(loaded);
    renderPreflight($('project-body'), loaded);
    openMessage(`Opened ${loaded.paired} paired document(s) from ${root}.`, 'good');
    $('project-path').value = '';
    clearMessages();
  } catch (error) {
    // The previous project is still loaded and untouched; say so rather than leaving the
    // reviewer guessing whether their session just went away.
    openMessage(`${error.message} — still reviewing the project you had open.`, 'bad');
  }
}

$('project-open').onclick = openProjectFromInput;
$('project-path').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();          // Enter here means "open this", never "close the dialog"
  openProjectFromInput();
});

// ------------------------------------------------------------------ boot
(async function boot() {
  try { 
    const saved = localStorage.getItem('spanarbiter-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  } catch { /* private mode */ }

  try {
    await applyProject(await api.project());
    if (state.project.blocking?.length) {
      say('error', `This project has ${state.project.blocking.length} blocking problem(s): `
        + state.project.blocking[0].message, true);
    }
    // Interrupt only for a blocking problem, where reviewing would produce untrustworthy
    // offsets. Warnings ("roles used only by A") are normal in real corpora; a modal that
    // appears every time is a modal nobody reads, so those go on the Project button instead.
    if (state.project.blocking?.length) showPreflight();
  } catch (error) {
    say('error', `Could not start: ${error.message}`, true);
  }
}());
