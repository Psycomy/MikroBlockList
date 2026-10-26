// The "sources" dialog: every source in lookup.json with its link, category, today's counts,
// license_note and notice (the Spamhaus copyright line). Spamhaus appears here only as the
// source of data. It holds a table (wide screens) and the same data as stacked cards (narrow
// screens); CSS shows one of them.
import { formatCount } from './core.js';
import { index, dataStatus } from './app.js';
import { t, lang, categoryName } from './i18n.js';

const dialog = document.getElementById('sources');
const body = document.getElementById('src-body');
const cards = document.getElementById('src-cards');
const opener = document.getElementById('src-open');
let returnFocus = null;

function cell(row, content, className) {
  const td = document.createElement('td');
  if (className) td.className = className;
  if (content instanceof Node) td.append(content); else td.textContent = content;
  row.append(td);
  return td;
}

/** An external link (new tab) with a visible ↗ and a hidden "opens in a new tab". */
export function externalLink(href, text) {
  const a = document.createElement('a');
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.textContent = text;
  const arrow = document.createElement('span');
  arrow.setAttribute('aria-hidden', 'true');
  arrow.textContent = ' ↗';
  const hint = document.createElement('span');
  hint.className = 'sr-only';
  hint.textContent = ` ${t('new_tab')}`;
  a.append(arrow, hint);
  return a;
}

/** A category badge ("attack", "C2"). */
export function badge(category) {
  const b = document.createElement('span');
  b.className = `badge cat-${category === 'c2' ? 'c2' : 'attack'}`;
  b.textContent = categoryName(category);
  return b;
}

const safeUrl = (url) => (/^https:\/\//i.test(url || '') ? url : null);

const div = (className, content) => {
  const node = document.createElement('div');
  node.className = className;
  if (content instanceof Node) node.append(content); else node.textContent = content;
  return node;
};

function sourceName(src) {
  const url = safeUrl(src.url);
  return url ? externalLink(url, src.name) : document.createTextNode(src.name);
}

function failedMark() {
  const failed = document.createElement('span');
  failed.className = 'src-failed';
  failed.textContent = ` · ${t('src_failed')}`;
  return failed;
}

const entriesText = (src) => (
  `${formatCount(src.entries || 0, lang())} · ${formatCount(src.entries6 || 0, lang())}`);

// a labelled line of a card ("Entradas (IPv4 · IPv6)  40.320 · 633")
function cardLine(key, value, className) {
  const line = div(`sc-line${className ? ` ${className}` : ''}`, '');
  const label = document.createElement('span');
  label.className = 'sc-k';
  label.textContent = t(key);
  const text = document.createElement('span');
  text.textContent = value;
  line.append(label, text);
  return line;
}

function sourceCard(src) {
  const li = document.createElement('li');
  li.className = 'src-card';
  const head = div('sc-head', sourceName(src));
  head.append(badge(src.category));
  if (!src.ok) head.append(failedMark());
  li.append(head, cardLine('th_entries', entriesText(src), 'num'));
  if (src.license_note) li.append(cardLine('th_terms', src.license_note));
  if (src.notice) li.append(cardLine('th_notice', src.notice, 'notice'));
  return li;
}

function render() {
  if (!index) {
    const text = t(dataStatus() === 'error' ? 'foot_unavailable' : 'sources_wait');
    const row = document.createElement('tr');
    cell(row, text).colSpan = 5;
    body.replaceChildren(row);
    const li = document.createElement('li');
    li.className = 'src-card';
    li.textContent = text;
    cards.replaceChildren(li);
    return;
  }
  const rows = index.sources.map((src) => {
    const row = document.createElement('tr');
    const nameCell = cell(row, sourceName(src), 'src-name');
    if (!src.ok) nameCell.append(failedMark());
    cell(row, badge(src.category));
    cell(row, entriesText(src), 'num');
    cell(row, src.license_note || '—');
    cell(row, src.notice || '—', 'notice');
    return row;
  });
  body.replaceChildren(...rows);
  cards.replaceChildren(...index.sources.map(sourceCard));
}

function open() {
  returnFocus = document.activeElement;
  render();
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
  dialog.querySelector('.dlg-close').focus();
}

function close() {
  if (typeof dialog.close === 'function') dialog.close();
  else dialog.removeAttribute('open');
}

opener.addEventListener('click', open);
dialog.querySelector('.dlg-close').addEventListener('click', close);
// a click on the backdrop (the dialog box itself, outside its content) closes it
dialog.addEventListener('click', (event) => { if (event.target === dialog) close(); });
dialog.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && typeof dialog.showModal !== 'function') close();
});
dialog.addEventListener('close', () => {
  if (returnFocus && typeof returnFocus.focus === 'function') returnFocus.focus();
  returnFocus = null;
});
document.addEventListener('langchange', () => { if (dialog.open) render(); });
document.addEventListener('datastatus', () => { if (dialog.open) render(); });
