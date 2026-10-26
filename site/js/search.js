// The search flow: scan animation, the map effects and the result cards. Entry module of the
// page (it loads app.js, which wires everything else).
import { parseQuery, search, countryKind, reportUrl, formatCount, resultTone } from './core.js';
import {
  els, map, index, centroids, onSearch, showIdleStatus, dataStatus, formatWhen, compact,
} from './app.js';
import { t, lang, countryText } from './i18n.js';
import { externalLink, badge } from './sources.js';

const SCAN_MS = 1000;
const COMET_FALLBACK_MS = 1600; // a big comet takes ~1.3 s; a hidden tab never lands it
const RGB = { cyan: '0,217,255', bad: '255,56,96', warn: '255,181,71', ok: '43,255,136' };
const STATUS_KEY = {
  blocked: 'st_blocked', partial: 'st_partial', clear: 'st_clear', 'v6-unpublished': 'st_v6',
};

let run = 0; // the current search; older ones stop at their next await
let last = null; // what the card shows, to redraw it on a language change
let flight = null; // the result whose effects (wave, comet) are still playing

const bar = els.bar.querySelector('i');
const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function boxCentre() {
  const b = els.q.getBoundingClientRect();
  return [b.left + b.width / 2, b.top + b.height / 2];
}

function wave(rgb) {
  const [x, y] = boxCentre();
  map.wave(x, y, rgb);
}

function setLine(text, tone) {
  const { st } = els;
  st.classList.remove('warn', 'bad', 'ok');
  if (tone) st.classList.add(tone);
  st.textContent = text;
}

// --- scan phase ---
function startScan() {
  els.form.classList.add('scanning');
  els.st.setAttribute('aria-busy', 'true');
  // the result card (#r) announces the verdict; keep #st quiet until the idle status returns
  els.st.setAttribute('aria-live', 'off');
  bar.style.width = '0';
  wave(RGB.cyan);
}

function stopScan() {
  els.form.classList.remove('scanning');
  els.bar.classList.remove('wait');
  els.st.removeAttribute('aria-busy');
  bar.style.width = '0';
}

// The ~1 s count-up. The timer ends it (animation frames stop in a hidden tab); frames only
// draw the bar and the counter.
function countUp(token) {
  const total = index.counts.v4 + index.counts.v6;
  const s = index.sourcesWithData;
  const t0 = performance.now();
  const paint = (k) => {
    bar.style.width = `${k * 100}%`;
    setLine(t(compact.matches ? 'scanning_short' : 'scanning', {
      n: formatCount(Math.floor(k * total), lang()), total: formatCount(total, lang()), s,
    }));
  };
  els.bar.classList.remove('wait');
  paint(0);
  const frame = (now) => {
    if (token !== run) return;
    const k = Math.min(1, (now - t0) / SCAN_MS);
    if (k < 1) { paint(k); requestAnimationFrame(frame); }
  };
  requestAnimationFrame(frame);
  return sleep(SCAN_MS).then(() => { if (token === run) paint(1); });
}

// Resolves with 'ready' or 'error' once lookup.json has settled.
function settled() {
  const now = dataStatus();
  if (now !== 'loading') return Promise.resolve(now);
  return new Promise((resolve) => {
    const on = (event) => {
      if (event.detail === 'loading') return;
      document.removeEventListener('datastatus', on);
      resolve(event.detail);
    };
    document.addEventListener('datastatus', on);
  });
}

function flash() {
  const layer = el('div', 'flash');
  layer.setAttribute('aria-hidden', 'true');
  document.body.append(layer);
  const remove = () => layer.remove();
  if (layer.animate) {
    layer.animate([{ opacity: 0 }, { opacity: 1 }, { opacity: 0 }], { duration: 360 })
      .finished.then(remove, remove);
  } else {
    setTimeout(remove, 360);
  }
}

function setUrl(ip) {
  try {
    const url = new URL(location.href);
    if (ip === null) url.searchParams.delete('ip'); else url.searchParams.set('ip', ip);
    history.replaceState(history.state, '', url);
  } catch { /* history unavailable (sandboxed frame): the search still works */ }
}

// --- cards ---
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = el('textarea', 'sr-only');
    area.value = text;
    area.setAttribute('readonly', '');
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* no clipboard at all */ }
    area.remove();
    return ok;
  }
}

function copyButton(text, isRange) {
  const button = el('button', 'act', t(isRange ? 'copy_range' : 'copy_ip'));
  button.type = 'button';
  let timer = 0;
  button.addEventListener('click', async () => {
    const ok = await copyText(text);
    button.focus();
    button.textContent = t(ok ? 'copied' : 'copy_failed');
    clearTimeout(timer);
    timer = setTimeout(() => { button.textContent = t(isRange ? 'copy_range' : 'copy_ip'); }, 1600);
  });
  return button;
}

function pill(text, on, sr) {
  const p = el('span', `pill ${on === undefined ? 'info' : on ? 'on' : 'off'}`, text);
  if (sr) p.append(el('span', 'sr-only', `: ${sr}`));
  return p;
}

/** The country text of an entry; '' when this lookup.json has no DB-IP data. */
function country(entry, hasCountry = true) {
  if (!index.dbipMonth) return '';
  if (!hasCountry) return t('unknown_country'); // a lite-only entry carries no country
  return countryText(countryKind(entry.cc, index.dbipMonth), entry.cc);
}

function sourceLinks(sources) {
  const line = el('div', 'src');
  line.append(el('span', 'src-label', t('sources_label')));
  if (!sources.length) {
    line.append(el('span', 'dim', ` ${t('no_sources')}`));
    return line;
  }
  for (const src of sources) {
    const item = el('span', 'src-item');
    if (/^https:\/\//i.test(src.url || '')) item.append(externalLink(src.url, src.name));
    else item.append(document.createTextNode(src.name));
    item.append(badge(src.category));
    line.append(item);
  }
  return line;
}

function actions(query, result) {
  const row = el('div', 'actions');
  row.append(copyButton(query.text, query.cidr));
  if (result) {
    const report = el('span', 'report', `${t('report_q')} `);
    report.append(externalLink(reportUrl(query, result), t('report')));
    row.append(report);
  }
  return row;
}

function verdict(icon, key, ipText) {
  const v = el('div', 'verdict');
  v.append(el('span', 'v-icon', icon), el('span', 'v-text', t(key)));
  v.firstChild.setAttribute('aria-hidden', 'true');
  if (ipText) v.append(el('span', 'ip', ipText));
  return v;
}

function staleLine() {
  return el('div', 'stale', t('stale', { when: formatWhen(index.generatedAt) }));
}

function resultCard(query, result) {
  const tone = resultTone(result);
  const card = el('div', `card ${tone}`);
  const { state } = result;
  if (state === 'blocked') {
    const entry = result.entries[0];
    const fromFull = result.inFull; // a lite-only entry carries no country or sources
    card.append(verdict('⛔', 'v_blocked', query.text));
    const where = country(entry, fromFull);
    if (where) card.append(el('div', 'where', where));
    const row = el('div', 'row');
    row.append(
      pill(t('inside', { cidr: entry.cidr }), true),
      pill('full', result.inFull, t(result.inFull ? 'pill_yes' : 'pill_no')),
      pill('lite', result.inLite, t(result.inLite ? 'pill_yes' : 'pill_no')),
      pill(result.family),
    );
    card.append(row, sourceLinks(entry.sources), actions(query, result));
  } else if (state === 'partial') {
    const n = result.entries.length + result.more;
    card.append(verdict('◐', 'v_partial', query.text));
    card.append(el('div', 'lead',
      n === 1 ? t('overlap_one') : t('overlap', { n: formatCount(n, lang()) })));
    const list = el('ul', 'hits');
    for (const entry of result.entries) {
      const li = el('li');
      li.append(el('span', 'hit-cidr', entry.cidr));
      const bits = [country(entry)].filter(Boolean);
      if (entry.sources.length) bits.push(entry.sources.map((s) => s.name).join(', '));
      li.append(el('span', 'hit-rest', ` · ${bits.join(' · ')}`));
      list.append(li);
    }
    card.append(list);
    if (result.more) card.append(el('div', 'more', t('and_more', { n: formatCount(result.more, lang()) })));
    const row = el('div', 'row');
    row.append(pill(result.family));
    card.append(row, actions(query, result));
  } else if (state === 'v6-unpublished') {
    card.append(verdict('?', 'v_v6', query.text), el('div', 'lead', t('v6_text')));
  } else {
    card.append(verdict('✓', 'v_clear', query.text),
      el('div', 'lead', t(query.cidr ? 'clear_text_range' : 'clear_text')));
  }
  if (result.stale) card.append(staleLine());
  return card;
}

function unavailableCard(text) {
  const card = el('div', 'card warn');
  card.append(verdict('!', 'v_unavailable', text), el('div', 'lead', t('unavailable_text')));
  return card;
}

// Narrow screens hide the help paragraph while a card is shown (CSS: body.searched), until the
// box is cleared or the query is invalid.
function setSearched(on) {
  document.body.classList.toggle('searched', on);
}

// A new card that is not fully on screen scrolls into view (instantly with reduced motion);
// one taller than the screen scrolls to its top, the verdict. Measured once its pop-in
// animation has ended (a timer too, in case no animationend comes), against the visual
// viewport (what is on screen with a phone's address bar or keyboard).
function reveal(card) {
  let done = false;
  const go = () => {
    if (done || !card.isConnected) return;
    done = true;
    const view = window.visualViewport;
    const top = view ? view.offsetTop : 0;
    const height = view ? view.height : window.innerHeight;
    const b = card.getBoundingClientRect();
    if (b.top >= top && b.bottom <= top + height) return;
    card.scrollIntoView({
      behavior: map.reduced ? 'auto' : 'smooth',
      block: b.height > height ? 'start' : 'nearest',
    });
  };
  card.addEventListener('animationend', go, { once: true });
  setTimeout(go, 450);
}

/** Draw `last`; `fresh` when it is a new search result (not a language redraw). */
function render(fresh = false) {
  if (!last) { els.r.replaceChildren(); return; }
  let card = null;
  if (last.kind === 'invalid') {
    els.r.replaceChildren();
    setSearched(false);
    setLine(t('invalid'), 'bad');
  } else if (last.kind === 'unavailable') {
    card = unavailableCard(last.text);
    els.r.replaceChildren(card);
    showIdleStatus();
  } else {
    const { query, result } = last;
    card = resultCard(query, result);
    els.r.replaceChildren(card);
    setLine(t(STATUS_KEY[result.state]), resultTone(result));
  }
  if (card && fresh) {
    setSearched(true);
    reveal(card);
  }
}

// --- the flow ---
/** Run a search for the query text: scan, effects, then the result card. */
export async function runSearch(text) {
  const token = ++run;
  last = null;
  flight = null;
  stopScan();
  els.r.replaceChildren();
  const query = parseQuery(text);
  if (!query) {
    last = { kind: 'invalid' };
    els.q.setAttribute('aria-invalid', 'true');
    setUrl(null);
    render();
    return;
  }
  els.q.removeAttribute('aria-invalid');
  setUrl(query.text);
  const animate = !map.reduced;
  if (animate) startScan();

  let data = dataStatus();
  if (data === 'loading') {
    if (animate) els.bar.classList.add('wait');
    setLine(t('waiting'));
    data = await settled();
    if (token !== run) return;
  }
  if (data !== 'ready') {
    stopScan();
    last = { kind: 'unavailable', text: query.text, raw: text };
    render(true);
    return;
  }
  if (animate) {
    await countUp(token);
    if (token !== run) return;
    stopScan();
  }

  const result = search(index, query, new Date());
  const tone = resultTone(result);
  if (!animate) { last = { kind: 'result', query, result }; render(true); return; }

  flight = result;
  setLine(t(STATUS_KEY[result.state]), tone);
  wave(RGB[tone]);
  if (result.state === 'blocked') {
    const points = await centroids;
    if (token !== run) return;
    const cc = result.inFull ? result.entries[0].cc : '';
    const from = (cc && points[cc]) || null;
    await Promise.race([map.comet(from, true), sleep(COMET_FALLBACK_MS)]);
    if (token !== run) return;
    flash();
  }
  flight = null;
  last = { kind: 'result', query, result };
  render(true);
}

onSearch(runSearch);

els.q.addEventListener('input', () => { if (!els.q.value.trim()) setSearched(false); });

// redraw the card in the new language (app.js has just reset the status line)
document.addEventListener('langchange', () => {
  if (els.form.classList.contains('scanning')) return;
  if (flight) {
    // the card comes on impact: only the status line changes language now
    setLine(t(STATUS_KEY[flight.state]), resultTone(flight));
  } else if (last) {
    render();
  }
});

// a search made while the list was unavailable runs again once a Retry loads it
// (app.js has just reset the status line: put an "invalid" message back)
document.addEventListener('datastatus', (event) => {
  if (!last) return;
  if (last.kind === 'unavailable') {
    if (event.detail === 'ready') runSearch(last.raw);
  } else if (last.kind === 'invalid') {
    render();
  }
});

// ?ip= runs the search on load
const initial = new URLSearchParams(location.search).get('ip');
if (initial !== null) {
  els.q.value = initial;
  runSearch(initial);
}
