/** Mapping between what is on screen and character offsets in the source document.
 *
 * The adjudicator must be able to set a boundary by selecting the text itself. That only works
 * if every rendered character can be traced back to its exact offset, so text is emitted inside
 * elements that carry their own start offset, and nothing selectable is ever abbreviated — an
 * ellipsis in the middle of a passage would silently shift every offset after it.
 */
import { el } from './dom.js';

/** A run of document text that knows where it starts. */
export function anchored(text, begin, end, props = {}) {
  return el('span', { ...props, dataset: { ...(props.dataset || {}), offset: String(begin) } },
            text.slice(begin, end));
}

function offsetOf(node, within) {
  if (node.nodeType === Node.TEXT_NODE) {
    const parent = node.parentElement;
    if (!parent || parent.dataset.offset === undefined) return null;
    return Number(parent.dataset.offset) + within;
  }
  if (node.dataset && node.dataset.offset !== undefined) {
    let consumed = 0;
    for (let i = 0; i < within && i < node.childNodes.length; i += 1) {
      consumed += node.childNodes[i].textContent.length;
    }
    return Number(node.dataset.offset) + consumed;
  }
  return null;
}

/** The document offsets the user has selected, or null when the selection is not in the text. */
export function selectionOffsets() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const start = offsetOf(range.startContainer, range.startOffset);
  const end = offsetOf(range.endContainer, range.endOffset);
  if (start === null || end === null || start === end) return null;
  return start < end ? [start, end] : [end, start];
}
