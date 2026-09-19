/** Tiny DOM helpers.
 *
 * Everything in this interface is built with createElement and textContent, never by
 * concatenating HTML strings. Annotation labels and document text are arbitrary corpus data;
 * building nodes directly means a role called `HE SAID "X"` or a document containing `<b>`
 * simply cannot break the markup or inject anything. There is no escaping function to forget.
 */

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style') Object.assign(node.style, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function replace(node, ...children) {
  clear(node);
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Arabic, Hebrew, Syriac, Thaana, Arabic Supplement/Extended, and the presentation forms. */
const RTL = /[֐-׿؀-ۿ܀-ݏހ-޿ࢠ-ࣿיִ-﷿ﹰ-﻿]/;

/** Direction is decided by the text itself, so one build serves LTR and RTL corpora alike. */
export function directionOf(text) {
  if (!text) return 'ltr';
  const sample = text.slice(0, 400);
  const rtl = (sample.match(new RegExp(RTL, 'g')) || []).length;
  const latin = (sample.match(/[A-Za-z]/g) || []).length;
  return rtl > latin ? 'rtl' : 'ltr';
}

export function truncate(text, limit = 320) {
  if (text.length <= limit) return text;
  const half = Math.floor((limit - 5) / 2);
  return `${text.slice(0, half)} […] ${text.slice(-half)}`;
}
