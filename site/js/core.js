// Lookup core: parse a query, index lookup.json and search it. Pure ES module (no DOM, no
// Node APIs), shared by the page and the Node tests. Addresses are BigInt in both families.

const BITS = { 4: 32, 6: 128 };
const MAX_QUERY = 100;
const LIST_CAP = 20;
const STALE_MS = 2 * 24 * 60 * 60 * 1000;
const REPORT_BASE = 'https://github.com/Psycomy/MikroBlockList/issues/new';

const OCTET = /^(0|[1-9][0-9]{0,2})$/;
const HEXTET = /^[0-9a-fA-F]{1,4}$/;
const PREFIX = /^(0|[1-9][0-9]{0,2})$/;

// Dotted IPv4 to BigInt, or null. Leading zeros are invalid, as in Python's ipaddress.
function parseV4(text) {
  const parts = text.split('.');
  if (parts.length !== 4) return null;
  let value = 0n;
  for (const part of parts) {
    if (!OCTET.test(part)) return null;
    const n = Number(part);
    if (n > 255) return null;
    value = (value << 8n) | BigInt(n);
  }
  return value;
}

// IPv6 (compressed, any case, optional trailing dotted IPv4) to BigInt, or null. No zones.
function parseV6(text) {
  const halves = text.split('::');
  if (halves.length > 2) return null;
  const last = halves[halves.length - 1];
  // the hextets of one side of "::"; a dotted IPv4 may only end the address
  const hextets = (side, isLast) => {
    if (side === '') return [];
    const parts = side.split(':');
    const out = [];
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      if (isLast && i === parts.length - 1 && part.includes('.')) {
        const v4 = parseV4(part);
        if (v4 === null) return null;
        out.push(Number(v4 >> 16n), Number(v4 & 0xFFFFn));
      } else if (HEXTET.test(part)) {
        out.push(parseInt(part, 16));
      } else {
        return null;
      }
    }
    return out;
  };
  const head = hextets(halves[0], halves.length === 1);
  const tail = halves.length === 2 ? hextets(last, true) : [];
  if (head === null || tail === null) return null;
  const count = head.length + tail.length;
  if (halves.length === 1 ? count !== 8 : count > 7) return null;
  const groups = head.concat(new Array(8 - count).fill(0), tail);
  let value = 0n;
  for (const g of groups) value = (value << 16n) | BigInt(g);
  return value;
}

function v4Text(value) {
  return [24n, 16n, 8n, 0n].map((s) => String((value >> s) & 0xFFn)).join('.');
}

// RFC 5952 text, as Python prints it: lowercase, no leading zeros, the longest run (2+) of
// zero groups compressed, the first one on a tie.
function v6Text(value) {
  const groups = [];
  for (let s = 112n; s >= 0n; s -= 16n) groups.push(Number((value >> s) & 0xFFFFn));
  let bestStart = -1;
  let bestLen = 1;
  for (let i = 0; i < 8;) {
    if (groups[i] !== 0) { i++; continue; }
    let j = i;
    while (j < 8 && groups[j] === 0) j++;
    if (j - i > bestLen) { bestStart = i; bestLen = j - i; }
    i = j;
  }
  const hex = groups.map((g) => g.toString(16));
  if (bestStart < 0) return hex.join(':');
  const left = hex.slice(0, bestStart).join(':');
  const right = hex.slice(bestStart + bestLen).join(':');
  return `${left}::${right}`;
}

function addrText(version, value) {
  return version === 4 ? v4Text(value) : v6Text(value);
}

// "addr" or "addr/prefix" of one family, as {version, start, end, prefix}; host bits cleared.
function parseNetwork(text) {
  const slash = text.indexOf('/');
  const addr = slash < 0 ? text : text.slice(0, slash);
  let version;
  let value;
  if (addr.includes(':')) {
    if (!/^[0-9a-fA-F:.]+$/.test(addr)) return null;
    version = 6;
    value = parseV6(addr);
  } else {
    version = 4;
    value = parseV4(addr);
  }
  if (value === null) return null;
  const bits = BITS[version];
  let prefix = bits;
  if (slash >= 0) {
    const p = text.slice(slash + 1);
    if (!PREFIX.test(p)) return null;
    prefix = Number(p);
    if (prefix > bits) return null;
  }
  const hostBits = BigInt(bits - prefix);
  const hostMask = (1n << hostBits) - 1n;
  const start = value & ~hostMask;
  return { version, start, end: start | hostMask, prefix };
}

/**
 * Parse a user query: an IPv4/IPv6 address or CIDR. Returns
 * {version, start, end, text, cidr} or null. `text` is canonical (network address, no
 * prefix for a single host); `cidr` is true when the query covers more than one address.
 * `::ffff:a.b.c.d` (and mapped networks of /96 or longer) become IPv4.
 */
export function parseQuery(text) {
  if (typeof text !== 'string') return null;
  const trimmed = text.trim();
  if (trimmed === '' || trimmed.length > MAX_QUERY) return null;
  const net = parseNetwork(trimmed);
  if (!net) return null;
  let { version, start, end, prefix } = net;
  if (version === 6 && prefix >= 96 && (start >> 32n) === 0xFFFFn) {
    version = 4;
    start &= 0xFFFFFFFFn;
    end &= 0xFFFFFFFFn;
    prefix -= 96;
  }
  const cidr = prefix < BITS[version];
  const addr = addrText(version, start);
  return { version, start, end, text: cidr ? `${addr}/${prefix}` : addr, cidr };
}

// One list's ranges sorted by start, with the running maximum of the ends for the search.
function rangeSet(list, version, isFull) {
  const rows = list.map((item) => {
    const cidr = isFull ? item[0] : item;
    const net = typeof cidr === 'string' && cidr.includes('/') ? parseNetwork(cidr) : null;
    if (!net || net.version !== version) throw new Error(`lookup.json: bad entry ${cidr}`);
    return { start: net.start, end: net.end, item };
  });
  rows.sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0));
  const starts = [];
  const ends = [];
  const maxEnds = [];
  const items = [];
  let top = -1n;
  for (const row of rows) {
    starts.push(row.start);
    ends.push(row.end);
    top = row.end > top ? row.end : top;
    maxEnds.push(top);
    items.push(row.item);
  }
  return { starts, ends, maxEnds, items, isFull };
}

function family(data, version) {
  if (!data) return null;
  return {
    full: rangeSet(data.full || [], version, true),
    lite: rangeSet(data.lite || [], version, false),
  };
}

/** Index a parsed lookup.json (format 1) for search(). */
export function buildIndex(data) {
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const v4 = family(data.v4, 4);
  const v6 = family(data.v6, 6);
  return {
    sources,
    generatedAt: new Date(data.generated_at),
    ipv6Published: Boolean(data.ipv6_published) && v6 !== null,
    dbipMonth: data.dbip_month || null,
    counts: { v4: v4 ? v4.full.items.length : 0, v6: v6 ? v6.full.items.length : 0 },
    sourcesWithData: sources
      .filter((s) => s.ok && (s.entries || 0) + (s.entries6 || 0) > 0).length,
    v4,
    v6,
  };
}

// Indexes (ascending) of the entries of a set that overlap [start, end].
function overlapping(set, start, end) {
  const { starts, ends, maxEnds } = set;
  let lo = 0;
  let hi = starts.length;
  while (lo < hi) { // count of entries starting at or before `end`
    const mid = (lo + hi) >> 1;
    if (starts[mid] <= end) lo = mid + 1; else hi = mid;
  }
  const out = [];
  for (let i = lo - 1; i >= 0 && maxEnds[i] >= start; i--) {
    if (ends[i] >= start) out.push(i);
  }
  return out.reverse();
}

function entryOf(index, set, i) {
  if (!set.isFull) return { cidr: set.items[i], sources: [], cc: '' };
  const [cidr, indexes, cc] = set.items[i];
  const sources = (indexes || [])
    .map((n) => index.sources[n])
    .filter(Boolean)
    .map(({ name, category, url }) => ({ name, category, url }));
  return { cidr, sources, cc: cc || '' };
}

/**
 * Search a parsed query. `blocked`: a full or lite entry contains the whole query (its entry
 * is listed, the full one when both do). `partial`: entries overlap it but none contains it
 * (first 20 listed, the rest counted in `more`; full entries, or lite ones when no full entry
 * overlaps). inFull/inLite: an entry of that variant contains the whole query.
 */
export function search(index, query, now) {
  const familyName = query.version === 4 ? 'IPv4' : 'IPv6';
  const age = now.getTime() - index.generatedAt.getTime();
  const stale = !(age <= STALE_MS); // an unreadable date counts as stale
  const result = {
    state: 'clear', stale, entries: [], more: 0, inFull: false, inLite: false,
    family: familyName,
  };
  const fam = query.version === 4 ? index.v4 : index.v6;
  if (!fam) {
    if (query.version === 6) result.state = 'v6-unpublished';
    return result;
  }
  const { start, end } = query;
  const contains = (set) => (i) => set.starts[i] <= start && set.ends[i] >= end;
  const fullHits = overlapping(fam.full, start, end);
  const liteHits = overlapping(fam.lite, start, end);
  const fullIn = fullHits.find(contains(fam.full));
  const liteIn = liteHits.find(contains(fam.lite));
  result.inFull = fullIn !== undefined;
  result.inLite = liteIn !== undefined;
  let set;
  let hits;
  if (result.inFull || result.inLite) {
    result.state = 'blocked';
    set = result.inFull ? fam.full : fam.lite;
    hits = [result.inFull ? fullIn : liteIn];
  } else if (fullHits.length || liteHits.length) {
    result.state = 'partial';
    set = fullHits.length ? fam.full : fam.lite;
    hits = fullHits.length ? fullHits : liteHits;
  } else {
    return result;
  }
  result.entries = hits.slice(0, LIST_CAP).map((i) => entryOf(index, set, i));
  result.more = Math.max(0, hits.length - LIST_CAP);
  return result;
}

/** The pre-filled false_positive.yml issue form URL for a query and its result. */
export function reportUrl(query, result) {
  const first = result.entries.find((e) => e.sources.length > 0);
  const params = [
    ['template', 'false_positive.yml'],
    ['address', query.text],
    ['family', result.family],
    ['variant', result.inLite && !result.inFull ? 'lite' : 'full'],
    ['source', first ? first.sources[0].name : 'unknown'],
  ];
  return `${REPORT_BASE}?${params
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')}`;
}

/**
 * A CIDR with its host part hidden, for the ticker: "203.0.x.x" (first two octets) or
 * "2001:xxxx::" (first hextet only; "::" for ::/0). '?' if unparsable.
 */
export function maskCidr(cidr) {
  const net = typeof cidr === 'string' ? parseNetwork(cidr.trim()) : null;
  if (!net) return '?';
  if (net.version === 4) {
    return `${(net.start >> 24n) & 0xFFn}.${(net.start >> 16n) & 0xFFn}.x.x`;
  }
  if (net.start === 0n) return '::';
  return `${((net.start >> 112n) & 0xFFFFn).toString(16)}:xxxx::`;
}

/**
 * A listed IPv4 address to offer as the "blocked" example: inside the first full entry that
 * has a country (its first host address when the entry is wider than /31). null if none.
 */
export function exampleIp(index) {
  const items = (index && index.v4 && index.v4.full.items) || [];
  const item = items.find((it) => it[2] && it[2] !== 'ZZ') || items[0];
  const net = item ? parseNetwork(item[0]) : null;
  if (!net || net.version !== 4) return null;
  return v4Text(net.end - net.start > 1n ? net.start + 1n : net.start);
}

/**
 * The colour of a result: 'bad' (blocked, red), 'warn' (partial, IPv6 unpublished or a stale
 * "not listed", amber) or 'ok' (not listed with fresh data, green). Unknowns are never green.
 */
export function resultTone(result) {
  if (result.state === 'blocked') return 'bad';
  if (result.state === 'clear' && !result.stale) return 'ok';
  return 'warn';
}

/** A whole number with thousands separators: "39.480" (es) or "39,480" (en). */
/**
 * What to show for an entry's country code. `dbipMonth` is null when lookup.json has no
 * DB-IP data at all (every cc is ''): then there is nothing to say ('none'). With data,
 * '' means the entry spans several countries and 'ZZ' that DB-IP has no country for it.
 */
export function countryKind(cc, dbipMonth) {
  if (!dbipMonth) return 'none';
  if (!cc) return 'several';
  if (cc.toUpperCase() === 'ZZ') return 'unknown';
  return 'code';
}

export function formatCount(n, lang) {
  const sep = lang === 'es' ? '.' : ',';
  return String(Math.trunc(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, sep);
}
