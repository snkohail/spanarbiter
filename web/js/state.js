/** Application state and the rules for moving through the queue.
 *
 * Kept apart from rendering so the navigation rules - which conflicts the filter admits, what
 * "next" means, how the timer accumulates - can be tested without a browser.
 */

const SHAPE_FILTERS = new Set([
  'ROLE_CLASH', 'BOUNDARY_SHIFT', 'SEGMENTATION', 'NESTING_DIFF',
  'A_ONLY', 'B_ONLY', 'MIXED',
]);

export const state = {
  pinned: null,          // a decided conflict held on screen until the reviewer moves
  project: null,
  documents: [],
  docId: null,
  doc: null,            // { text, sentences, conflicts, decisions, progress }
  filter: 'open',
  index: 0,
  draft: [],            // ResolvedSpan-shaped objects being edited
  activeDraft: 0,
  editorOpen: false,
  rolePicker: { index: null, query: '' },
  contextOpen: false,
  contextRadius: 400,
  docQuery: '',
  busy: false,
};

const listeners = new Set();
export const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const emit = () => { for (const fn of listeners) fn(); };

/** Conflicts admitted by the current filter, in document order. Agreements are never queued. */
export function visibleConflicts() {
  if (!state.doc) return [];
  const decisions = state.doc.decisions || {};
  return state.doc.conflicts.filter((c) => {
    if (c.agreed) return false;
    // The conflict just resolved from the editor stays visible until the reviewer moves on,
    // whatever the filter says. Otherwise deciding it under "Needs a decision" makes it vanish
    // at the exact moment the reviewer wants to look at what they saved.
    if (c.conflict_id === state.pinned) return true;
    const decision = decisions[c.conflict_id];
    switch (state.filter) {
      case 'open':    return !decision || decision.decision_type === 'DEFER';
      case 'settled': return !!decision && decision.decision_type !== 'DEFER';
      case 'deferred': return decision?.decision_type === 'DEFER';
      case 'all':     return true;
      default:        return SHAPE_FILTERS.has(state.filter) ? c.shape === state.filter : true;
    }
  });
}

export function currentConflict() {
  const list = visibleConflicts();
  if (!list.length) return null;
  state.index = Math.min(Math.max(0, state.index), list.length - 1);
  return list[state.index];
}

export function currentDecision() {
  const conflict = currentConflict();
  return conflict ? (state.doc.decisions || {})[conflict.conflict_id] || null : null;
}

export function move(delta) {
  const list = visibleConflicts();
  const next = state.index + delta;
  if (next < 0 || next >= list.length) return false;
  state.index = next;
  return true;
}

/** Time on task and visits, kept PER CONFLICT.
 *
 * A visit is the conflict becoming the active target — not a save. Time is banked in a ledger
 * when the reviewer moves on, so looking at a conflict, navigating away to think, and coming
 * back to decide accumulates rather than restarting. Only a successful save clears an entry.
 * Re-rendering and opening the document context are not visits and do not touch the clock.
 */
export const timer = {
  ledger: new Map(),          // conflictId -> { active seconds, visits }
  current: null,
  startedAt: null,

  entry(conflictId) {
    if (!this.ledger.has(conflictId)) this.ledger.set(conflictId, { active: 0, visits: 0 });
    return this.ledger.get(conflictId);
  },
  bank() {
    if (this.current !== null && this.startedAt !== null) {
      this.entry(this.current).active += (Date.now() - this.startedAt) / 1000;
    }
    this.startedAt = null;
  },
  focus(conflictId) {
    if (this.current === conflictId) {
      if (this.startedAt === null) this.startedAt = Date.now();   // resumed, not a new visit
      return;
    }
    this.bank();
    this.current = conflictId;
    this.entry(conflictId).visits += 1;
    this.startedAt = Date.now();
  },
  read(conflictId) {
    const entry = this.entry(conflictId);
    const live = this.current === conflictId && this.startedAt !== null
      ? (Date.now() - this.startedAt) / 1000 : 0;
    return entry.active + live;
  },
  visits(conflictId) { return this.entry(conflictId).visits; },
  /** A saved conflict starts a fresh attempt; the server keeps the running totals. */
  settle(conflictId) {
    this.bank();
    this.ledger.delete(conflictId);
    if (this.current === conflictId) this.current = null;
  },
  clear() { this.ledger.clear(); this.current = null; this.startedAt = null; },
  pause() { this.bank(); },
  resume() { if (this.current !== null && this.startedAt === null) this.startedAt = Date.now(); },
};

document.addEventListener('visibilitychange', () => {
  if (document.hidden) timer.pause(); else timer.resume();
});
