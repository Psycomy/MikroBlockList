// All UI strings, Spanish and English, and the helpers that apply them. Elements carry
// data-i18n="key" (textContent) or data-i18n-attr="attr:key;attr:key" (attributes).

const STRINGS = {
  es: {
    page_title: 'MikroBlockList · ¿Está esta IP en la lista?',
    tagline: 'threat blocklist · RouterOS v7',
    q_label: 'Dirección IP o rango CIDR',
    q_placeholder: 'Escribe una IP o un rango…',
    search: 'Buscar',
    help: 'Comprueba si una dirección IPv4 o IPv6, o un rango CIDR, está en la lista de bloqueo, en qué variantes (full, lite) y qué fuentes públicas la incluyen.',
    try: 'Prueba con',
    or: 'o',
    privacy: 'Tu búsqueda nunca sale de tu navegador.',
    loading: '> cargando lista…',
    ready: '> lista cargada · {n} entradas · {s} fuentes',
    ready_short: '> {n} entradas · {s} fuentes',
    unavailable: '> lista no disponible',
    retry: 'Reintentar',
    foot_counts: '{v4} IPv4 · {v6} IPv6 · actualizada {when} · {s} fuentes',
    foot_counts_no_v6: '{v4} IPv4 · IPv6 no publicada hoy · actualizada {when} · {s} fuentes',
    foot_loading: 'cargando datos de la lista…',
    foot_unavailable: 'datos de la lista no disponibles',
    dbip: 'IP Geolocation by DB-IP',
    dbip_month: '(datos de {month})',
    license: 'uso doméstico y no comercial',
    repo: 'código en GitHub',
    readme: 'README',
    ticker_label: 'En la lista ahora mismo',
    ticker_note: 'direcciones listadas, no tráfico en directo',
    lang_label: 'Idioma',
    several_countries: 'varios países',
    unknown_country: 'país desconocido',
    cat_attack: 'ataque',
    cat_c2: 'C2',
    scanning: '> escaneando {n} / {total} entradas · {s} fuentes',
    scanning_short: '> escaneando {n} / {total}',
    waiting: '> esperando a la lista…',
    invalid: '> eso no es una dirección IP ni un rango CIDR válido',
    st_blocked: '> coincidencia encontrada',
    st_partial: '> coincidencia parcial',
    st_clear: '> sin coincidencias',
    st_v6: '> lista IPv6 no publicada hoy',
    v_blocked: 'BLOQUEADA',
    v_partial: 'PARCIALMENTE EN LA LISTA',
    v_clear: 'NO ESTÁ EN LA LISTA',
    v_v6: 'IPv6 NO PUBLICADA HOY',
    v_unavailable: 'LISTA NO DISPONIBLE',
    inside: 'dentro de {cidr}',
    pill_yes: 'incluida',
    pill_no: 'no incluida',
    sources_label: 'Fuentes:',
    no_sources: 'sin detalle de fuentes (entrada solo de lite)',
    overlap_one: '1 entrada de la lista se solapa con este rango:',
    overlap: '{n} entradas de la lista se solapan con este rango:',
    and_more: '… y {n} más',
    clear_text: 'Ninguna de las fuentes la incluye en la lista publicada hoy.',
    clear_text_range: 'Ninguna entrada de la lista publicada hoy se solapa con este rango.',
    v6_text: 'La lista IPv6 no se ha publicado hoy, así que esta dirección no se puede comprobar.',
    unavailable_text: 'No se ha podido cargar la lista, así que no se puede comprobar nada. Prueba con Reintentar.',
    stale: '⚠ La lista es del {when}, de hace más de 2 días: el resultado puede estar desactualizado.',
    copy_ip: 'Copiar IP',
    copy_range: 'Copiar rango',
    copied: 'Copiado',
    copy_failed: 'No se pudo copiar',
    report_q: '¿Es un error?',
    report: 'Reportar falso positivo',
    new_tab: '(se abre en otra pestaña)',
    sources_link: 'fuentes',
    sources_title: 'Fuentes de la lista',
    sources_intro: 'Las listas públicas que forman MikroBlockList y sus condiciones. Las cifras son de la compilación de hoy.',
    th_source: 'Fuente',
    th_category: 'Tipo',
    th_entries: 'Entradas (IPv4 · IPv6)',
    th_terms: 'Condiciones',
    th_notice: 'Aviso',
    src_failed: 'falló hoy',
    sources_wait: 'cargando…',
    close: 'Cerrar',
  },
  en: {
    page_title: 'MikroBlockList · Is this IP listed?',
    tagline: 'threat blocklist · RouterOS v7',
    q_label: 'IP address or CIDR range',
    q_placeholder: 'Type an IP or a range…',
    search: 'Search',
    help: 'Check whether an IPv4 or IPv6 address, or a CIDR range, is on the blocklist, in which variants (full, lite) and which public sources list it.',
    try: 'Try',
    or: 'or',
    privacy: 'Your search never leaves your browser.',
    loading: '> loading list…',
    ready: '> list loaded · {n} entries · {s} sources',
    ready_short: '> {n} entries · {s} sources',
    unavailable: '> list unavailable',
    retry: 'Retry',
    foot_counts: '{v4} IPv4 · {v6} IPv6 · updated {when} · {s} sources',
    foot_counts_no_v6: '{v4} IPv4 · IPv6 not published today · updated {when} · {s} sources',
    foot_loading: 'loading list data…',
    foot_unavailable: 'list data unavailable',
    dbip: 'IP Geolocation by DB-IP',
    dbip_month: '({month} data)',
    license: 'home and non-commercial use',
    repo: 'code on GitHub',
    readme: 'README',
    ticker_label: 'On the list right now',
    ticker_note: 'listed addresses, not live traffic',
    lang_label: 'Language',
    several_countries: 'several countries',
    unknown_country: 'unknown country',
    cat_attack: 'attack',
    cat_c2: 'C2',
    scanning: '> scanning {n} / {total} entries · {s} sources',
    scanning_short: '> scanning {n} / {total}',
    waiting: '> waiting for the list…',
    invalid: '> that is not a valid IP address or CIDR range',
    st_blocked: '> match found',
    st_partial: '> partial match',
    st_clear: '> no match',
    st_v6: '> IPv6 list not published today',
    v_blocked: 'BLOCKED',
    v_partial: 'PARTIALLY LISTED',
    v_clear: 'NOT ON THE LIST',
    v_v6: 'IPv6 NOT PUBLISHED TODAY',
    v_unavailable: 'LIST UNAVAILABLE',
    inside: 'inside {cidr}',
    pill_yes: 'listed',
    pill_no: 'not listed',
    sources_label: 'Sources:',
    no_sources: 'no source detail (lite-only entry)',
    overlap_one: '1 listed entry overlaps this range:',
    overlap: '{n} listed entries overlap this range:',
    and_more: '… and {n} more',
    clear_text: "None of the sources lists it in today's published list.",
    clear_text_range: "No entry of today's published list overlaps this range.",
    v6_text: 'The IPv6 list was not published today, so this address cannot be checked.',
    unavailable_text: 'The list could not be loaded, so nothing can be checked. Try Retry.',
    stale: '⚠ The list is from {when}, more than 2 days old: the result may be out of date.',
    copy_ip: 'Copy IP',
    copy_range: 'Copy range',
    copied: 'Copied',
    copy_failed: 'Copy failed',
    report_q: 'Is this wrong?',
    report: 'Report a false positive',
    new_tab: '(opens in a new tab)',
    sources_link: 'sources',
    sources_title: 'List sources',
    sources_intro: "The public lists MikroBlockList is built from, and their terms. Counts are from today's build.",
    th_source: 'Source',
    th_category: 'Type',
    th_entries: 'Entries (IPv4 · IPv6)',
    th_terms: 'Terms',
    th_notice: 'Notice',
    src_failed: 'failed today',
    sources_wait: 'loading…',
    close: 'Close',
  },
};

const STORE_KEY = 'mbl-lang';
let current = initialLang();

function initialLang() {
  try {
    const saved = localStorage.getItem(STORE_KEY);
    if (saved === 'es' || saved === 'en') return saved;
  } catch { /* storage blocked: use the browser language */ }
  const langs = (typeof navigator !== 'undefined' && navigator.languages) || [];
  const first = String(langs[0] || (typeof navigator !== 'undefined' && navigator.language) || 'en');
  return first.toLowerCase().startsWith('es') ? 'es' : 'en';
}

/** The current language, 'es' or 'en'. */
export function lang() {
  return current;
}

/** The string for `key`, with {name} placeholders filled from `vars`; the key if unknown. */
export function t(key, vars) {
  const table = STRINGS[current];
  let text = key in table ? table[key] : (STRINGS.en[key] ?? key);
  if (vars) text = text.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
  return text;
}

/** Fill every data-i18n / data-i18n-attr element under `root`. */
export function applyStrings(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll('[data-i18n-attr]')) {
    for (const pair of el.dataset.i18nAttr.split(';')) {
      const [attr, key] = pair.split(':');
      if (attr && key) el.setAttribute(attr.trim(), t(key.trim()));
    }
  }
  document.documentElement.lang = current;
  document.title = t('page_title');
}

/** Switch language, re-apply the strings and fire a 'langchange' event on document. */
export function setLang(next) {
  if (next !== 'es' && next !== 'en') return;
  current = next;
  try { localStorage.setItem(STORE_KEY, next); } catch { /* not persisted */ }
  applyStrings();
  document.dispatchEvent(new CustomEvent('langchange', { detail: next }));
}

/** The regional-indicator flag emoji of an ISO alpha-2 code, or '' for anything else. */
export function flag(cc) {
  if (!/^[A-Za-z]{2}$/.test(cc || '')) return '';
  return String.fromCodePoint(...[...cc.toUpperCase()].map((c) => 0x1F1A5 + c.charCodeAt(0)));
}

const regionNames = {};
/** The country name of an ISO alpha-2 code in the current language; the code if unknown. */
export function countryName(cc) {
  if (!cc) return '';
  try {
    regionNames[current] ||= new Intl.DisplayNames([current], { type: 'region' });
    return regionNames[current].of(cc.toUpperCase()) || cc;
  } catch {
    return cc;
  }
}

/** The text for a countryKind() result: flag and name, a label, or '' for 'none'. */
export function countryText(kind, cc) {
  if (kind === 'code') return `${flag(cc)} ${countryName(cc)}`;
  if (kind === 'several') return t('several_countries');
  if (kind === 'unknown') return t('unknown_country');
  return '';
}

/** A source category ('attack', 'c2') in the current language. */
export function categoryName(category) {
  const key = `cat_${category}`;
  return key in STRINGS[current] ? t(key) : String(category || '');
}
