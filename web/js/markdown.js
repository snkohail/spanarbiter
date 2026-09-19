/** A deliberately small Markdown renderer for the guidelines panel.
 *
 * It builds nodes rather than HTML, so a guidelines file can never inject markup into the
 * interface. Supports what a guidelines document actually needs: headings, paragraphs, lists,
 * fenced and inline code, bold, italic, and blockquotes.
 */
import { el } from './dom.js';

function inline(text) {
  const nodes = [];
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(_[^_]+_)/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith('`')) nodes.push(el('code', { text: token.slice(1, -1) }));
    else if (token.startsWith('**')) nodes.push(el('strong', { text: token.slice(2, -2) }));
    else nodes.push(el('em', { text: token.slice(1, -1) }));
    last = pattern.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function renderMarkdown(source) {
  const out = [];
  const lines = (source || '').split('\n');
  let paragraph = [];
  let list = null;
  let fence = null;

  const flushParagraph = () => {
    if (paragraph.length) { out.push(el('p', {}, inline(paragraph.join(' ')))); paragraph = []; }
  };
  const flushList = () => { if (list) { out.push(list); list = null; } };

  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (fence === null) { flushParagraph(); flushList(); fence = []; }
      else { out.push(el('pre', {}, el('code', { text: fence.join('\n') }))); fence = null; }
      continue;
    }
    if (fence !== null) { fence.push(line); continue; }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph(); flushList();
      out.push(el(`h${Math.min(heading[1].length, 3)}`, {}, inline(heading[2])));
      continue;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flushParagraph();
      const wanted = bullet ? 'UL' : 'OL';
      if (!list || list.tagName !== wanted) { flushList(); list = el(bullet ? 'ul' : 'ol'); }
      list.append(el('li', {}, inline((bullet || numbered)[1])));
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      flushParagraph(); flushList();
      out.push(el('blockquote', {}, inline(quote[1])));
      continue;
    }
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    // Lazy continuation: a wrapped list item belongs to the item above it, not to a new
    // paragraph. Without this a hanging-indent bullet is emitted as prose BEFORE the list.
    if (list && list.lastElementChild) {
      list.lastElementChild.append(' ', ...inline(line.trim()));
      continue;
    }
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushList();
  if (fence !== null) out.push(el('pre', {}, el('code', { text: fence.join('\n') })));
  return out;
}
