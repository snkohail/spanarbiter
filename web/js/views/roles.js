/** Choosing the role(s) for a final span.
 *
 * The control adapts to the inventory. A handful of roles is fastest as visible options — no
 * searching, no clicks to reveal them. Forty roles rendered the same way is a wall of chips that
 * dominates the screen and is slower to scan than typing three letters, so past a threshold the
 * chosen roles stay visible and the rest move behind a search field.
 */
import { el } from '../dom.js';

/** Above this many roles, selection becomes a search rather than a list. */
export const INLINE_LIMIT = 12;

function inlineRoles(draft, index, roles, handlers) {
  return el('div', { class: 'roles-grid' },
    roles.map((role) => el('label', { class: 'role-toggle' },
      el('input', {
        type: 'checkbox', checked: draft.labels.includes(role), value: role,
        onchange: (e) => handlers.toggleRole(index, role, e.target.checked),
      }),
      role)));
}

function searchRoles(draft, index, roles, picker, handlers) {
  const chosen = draft.labels.map((role) => el('span', { class: 'role-chip' },
    el('span', { class: 'role', text: role }),
    el('button', {
      class: 'role-chip-remove', type: 'button',
      'aria-label': `Remove role ${role}`,
      onclick: () => handlers.toggleRole(index, role, false),
    }, '×')));

  const open = picker.index === index;
  let panel = null;

  if (open) {
    // The options list is repainted in place as the reviewer types. Re-rendering the whole
    // editor on every keystroke would destroy the input element mid-word, losing focus, the
    // caret, and any character typed while the node was being replaced.
    const list = el('ul', { class: 'role-options', role: 'listbox' });

    const matching = (query) => {
      const needle = query.trim().toUpperCase();
      return roles.filter((role) => !needle || role.toUpperCase().includes(needle));
    };

    const paint = (query) => {
      const matches = matching(query);
      list.replaceChildren(...(matches.length
        ? matches.slice(0, 60).map((role) => el('li', {
            role: 'option',
            'aria-selected': draft.labels.includes(role) ? 'true' : 'false',
          }, el('button', {
            class: `role-option${draft.labels.includes(role) ? ' is-chosen' : ''}`,
            type: 'button', dataset: { role },
            onclick: () => handlers.toggleRole(index, role, !draft.labels.includes(role)),
          }, role)))
        : [el('li', { class: 'none', text: `No role matches “${query}”` })]));
    };

    const input = el('input', {
      type: 'text', class: 'role-search', id: 'role-search',
      placeholder: 'Search roles…', value: picker.query,
      'aria-label': 'Search roles', 'aria-expanded': 'true', role: 'combobox',
      oninput: (e) => { handlers.setQuery(e.target.value); paint(e.target.value); },
      onkeydown: (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); handlers.closePicker(); }
        if (e.key === 'Enter') {
          e.preventDefault();
          const matches = matching(e.target.value);
          if (matches.length) handlers.toggleRole(index, matches[0], true);
        }
      },
    });
    paint(picker.query);
    panel = el('div', { class: 'role-picker' }, input, list);
  }

  return el('div', { class: 'roles-search' },
    el('div', { class: 'role-chips' },
      chosen.length ? chosen : el('span', { class: 'none', text: 'No role chosen yet' }),
      el('button', {
        class: 'btn btn-quiet role-add', type: 'button',
        'aria-expanded': open ? 'true' : 'false',
        onclick: () => (open ? handlers.closePicker() : handlers.openPicker(index)),
      }, open ? 'Done' : '+ Add role')),
    panel);
}


export function renderRoles(draft, index, roles, picker, handlers) {
  return roles.length <= INLINE_LIMIT
    ? inlineRoles(draft, index, roles, handlers)
    : searchRoles(draft, index, roles, picker, handlers);
}
