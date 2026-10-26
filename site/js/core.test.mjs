// Tests for the lookup core: node --test site/js/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  parseQuery, buildIndex, search, reportUrl, formatCount, maskCidr, resultTone, exampleIp, countryKind,
} from './core.js';

const NOW = new Date('2026-10-22T06:00:00Z');

function sample() {
  const many = [];
  for (let i = 0; i < 25; i++) many.push([`10.9.${i}.0/24`, [1], 'NL']);
  return {
    format: 1,
    generated_at: '2026-10-22T04:17:00Z',
    ipv6_published: true,
    dbip_month: '2026-10',
    sources: [
      { name: 'spamhaus-drop', category: 'attack', url: 'https://example.invalid/drop',
        license_note: 'x', notice: 'y', entries: 3, entries6: 0, ok: true },
      { name: 'cins', category: 'attack', url: 'https://example.invalid/cins',
        license_note: 'x', notice: 'y', entries: 27, entries6: 0, ok: true },
      { name: 'c2intel', category: 'c2', url: 'https://example.invalid/c2',
        license_note: 'x', notice: 'y', entries: 0, entries6: 1, ok: true },
      { name: 'broken', category: 'attack', url: 'https://example.invalid/b',
        license_note: 'x', notice: 'y', entries: 5, entries6: 0, ok: false },
      { name: 'empty', category: 'attack', url: 'https://example.invalid/e',
        license_note: 'x', notice: 'y', entries: 0, entries6: 0, ok: true },
    ],
    v4: {
      full: [
        ['1.2.3.0/24', [1], 'US'],
        ['1.2.5.0/24', [1], 'US'],
        ['8.8.8.8/32', [0, 1], ''],
        ...many,
        ['185.220.100.0/22', [0, 1], 'DE'],
      ],
      lite: ['5.5.5.5/32', '185.220.100.0/22'],
    },
    v6: {
      full: [['2a0e:1::/32', [2], 'NL']],
      lite: [],
    },
  };
}

const index = buildIndex(sample());

function q(text) {
  const parsed = parseQuery(text);
  assert.ok(parsed, `expected ${text} to parse`);
  return parsed;
}

// --- parseQuery ---

test('parse valid IPv4', () => {
  const p = q('185.220.101.4');
  assert.equal(p.version, 4);
  assert.equal(p.start, 0xB9DC6504n);
  assert.equal(p.end, 0xB9DC6504n);
  assert.equal(p.text, '185.220.101.4');
  assert.equal(p.cidr, false);
});

test('parse trims whitespace', () => {
  assert.equal(q('  1.2.3.4  ').text, '1.2.3.4');
  assert.equal(q('\t1.2.3.4\n').start, 0x01020304n);
});

test('parse IPv6 compressed and uppercase', () => {
  const p = q('2A0E:0001:0:0::5');
  assert.equal(p.version, 6);
  assert.equal(p.text, '2a0e:1::5');
  assert.equal(p.start, (0x2a0en << 112n) | (1n << 96n) | 5n);
  assert.equal(p.end, p.start);
  assert.equal(q('::').text, '::');
  assert.equal(q('::1').text, '::1');
  assert.equal(q('1:0:0:2:0:0:0:3').text, '1:0:0:2::3');
  assert.equal(q('1:0:0:2:0:0:3:4').text, '1::2:0:0:3:4');
  assert.equal(q('1:2:3:4:5:6:7::').text, '1:2:3:4:5:6:7:0');
  assert.equal(q('64:ff9b::1.2.3.4').text, '64:ff9b::102:304');
});

test('parse IPv4-mapped IPv6 becomes IPv4', () => {
  const p = q('::ffff:1.2.3.4');
  assert.equal(p.version, 4);
  assert.equal(p.text, '1.2.3.4');
  assert.equal(p.start, 0x01020304n);
  assert.equal(q('::FFFF:0102:0304').text, '1.2.3.4');
  const net = q('::ffff:1.2.3.0/120');
  assert.equal(net.version, 4);
  assert.equal(net.text, '1.2.3.0/24');
});

test('parse host-bit CIDR normalized to its network', () => {
  const p = q('1.2.3.77/24');
  assert.equal(p.text, '1.2.3.0/24');
  assert.equal(p.cidr, true);
  assert.equal(p.start, 0x01020300n);
  assert.equal(p.end, 0x010203FFn);
  const p6 = q('2a0e:1::ffff/32');
  assert.equal(p6.text, '2a0e:1::/32');
  assert.equal(p6.end, (0x2a0e0001n << 96n) | ((1n << 96n) - 1n));
  assert.equal(q('0.0.0.0/0').end, 0xFFFFFFFFn);
});

test('parse single-host prefix is a plain address', () => {
  const p = q('8.8.8.8/32');
  assert.equal(p.text, '8.8.8.8');
  assert.equal(p.cidr, false);
  assert.equal(q('::1/128').text, '::1');
});

test('parse rejects invalid input', () => {
  for (const bad of [
    '010.1.1.1', '1.2.3.04', '1.2.3.4/33', '1.2.3.4/', '1.2.3.4/-1', '1.2.3.4/024',
    '<script>', '', '   ', '1.2.3', '1.2.3.4.5', '256.1.1.1', '1.2.3.4 5',
    '::1/129', '1::2::3', '12345::', '1:2:3:4:5:6:7:8:9', '1:2:3:4:5:6:7:8::',
    'fe80::1%eth0', ':1:2', '1:2:', '::ffff:01.2.3.4', 'g::1', '1.2.3.4/3 2',
    '١.٢.٣.٤',
  ]) {
    assert.equal(parseQuery(bad), null, `expected ${JSON.stringify(bad)} to be rejected`);
  }
  assert.equal(parseQuery(null), null);
  assert.equal(parseQuery('1'.repeat(500)), null);
});

// --- buildIndex ---

test('index metadata', () => {
  assert.equal(index.generatedAt.toISOString(), '2026-10-22T04:17:00.000Z');
  assert.equal(index.ipv6Published, true);
  assert.equal(index.dbipMonth, '2026-10');
  assert.deepEqual(index.counts, { v4: 29, v6: 1 });
  assert.equal(index.sourcesWithData, 3);
  assert.equal(index.sources.length, 5);
});

test('index of withheld IPv6', () => {
  const data = sample();
  data.v6 = null;
  data.ipv6_published = false;
  const idx = buildIndex(data);
  assert.deepEqual(idx.counts, { v4: 29, v6: 0 });
  assert.equal(idx.ipv6Published, false);
});

// --- search ---

test('IP inside a full entry is blocked with its sources and cc', () => {
  const r = search(index, q('185.220.101.4'), NOW);
  assert.equal(r.state, 'blocked');
  assert.equal(r.family, 'IPv4');
  assert.equal(r.inFull, true);
  assert.equal(r.inLite, true);
  assert.equal(r.stale, false);
  assert.equal(r.more, 0);
  assert.deepEqual(r.entries, [{
    cidr: '185.220.100.0/22',
    sources: [
      { name: 'spamhaus-drop', category: 'attack', url: 'https://example.invalid/drop' },
      { name: 'cins', category: 'attack', url: 'https://example.invalid/cins' },
    ],
    cc: 'DE',
  }]);
});

test('exact /32 entry boundaries', () => {
  assert.equal(search(index, q('8.8.8.8'), NOW).state, 'blocked');
  assert.equal(search(index, q('8.8.8.7'), NOW).state, 'clear');
  assert.equal(search(index, q('8.8.8.9'), NOW).state, 'clear');
  assert.equal(search(index, q('1.2.3.255'), NOW).state, 'blocked');
  assert.equal(search(index, q('1.2.4.0'), NOW).state, 'clear');
});

test('IP only in lite is blocked, not in full', () => {
  const r = search(index, q('5.5.5.5'), NOW);
  assert.equal(r.state, 'blocked');
  assert.equal(r.inFull, false);
  assert.equal(r.inLite, true);
  assert.deepEqual(r.entries, [{ cidr: '5.5.5.5/32', sources: [], cc: '' }]);
});

test('IP outside every entry is clear', () => {
  const r = search(index, q('9.9.9.9'), NOW);
  assert.equal(r.state, 'clear');
  assert.deepEqual(r.entries, []);
  assert.equal(r.more, 0);
  assert.equal(r.inFull, false);
  assert.equal(r.inLite, false);
  assert.equal(search(index, q('0.0.0.0'), NOW).state, 'clear');
  assert.equal(search(index, q('255.255.255.255'), NOW).state, 'clear');
});

test('wider CIDR containing two entries is partial', () => {
  const r = search(index, q('1.2.0.0/16'), NOW);
  assert.equal(r.state, 'partial');
  assert.deepEqual(r.entries.map((e) => e.cidr), ['1.2.3.0/24', '1.2.5.0/24']);
  assert.equal(r.more, 0);
  assert.equal(r.inFull, false);
  assert.equal(r.inLite, false);
});

test('CIDR inside an entry is blocked', () => {
  const r = search(index, q('185.220.101.0/24'), NOW);
  assert.equal(r.state, 'blocked');
  assert.deepEqual(r.entries.map((e) => e.cidr), ['185.220.100.0/22']);
});

test('CIDR overlapping 25 entries lists 20 and more 5', () => {
  const r = search(index, q('10.9.0.0/16'), NOW);
  assert.equal(r.state, 'partial');
  assert.equal(r.entries.length, 20);
  assert.equal(r.more, 5);
  assert.equal(r.entries[0].cidr, '10.9.0.0/24');
  assert.equal(r.entries[19].cidr, '10.9.19.0/24');
});

test('whole IPv4 space is partial over every entry', () => {
  const r = search(index, q('0.0.0.0/0'), NOW);
  assert.equal(r.state, 'partial');
  assert.equal(r.entries.length + r.more, 29);
});

test('IPv6 search', () => {
  const r = search(index, q('2a0e:1:ff::1'), NOW);
  assert.equal(r.state, 'blocked');
  assert.equal(r.family, 'IPv6');
  assert.equal(r.entries[0].sources[0].name, 'c2intel');
  assert.equal(search(index, q('2a0f::1'), NOW).state, 'clear');
});

test('IPv6 query without published IPv6 is v6-unpublished', () => {
  const data = sample();
  data.v6 = null;
  data.ipv6_published = false;
  const r = search(buildIndex(data), q('2a0e:1::1'), NOW);
  assert.equal(r.state, 'v6-unpublished');
  assert.equal(r.family, 'IPv6');
  assert.deepEqual(r.entries, []);
});

test('stale when data is older than 2 days', () => {
  const r1 = search(index, q('9.9.9.9'), new Date('2026-10-24T04:17:00Z'));
  assert.equal(r1.stale, false);
  const r2 = search(index, q('9.9.9.9'), new Date('2026-10-24T04:17:01Z'));
  assert.equal(r2.stale, true);
});

// --- reportUrl ---

test('report URL for a full entry', () => {
  const query = q('185.220.101.4');
  const url = reportUrl(query, search(index, query, NOW));
  assert.ok(url.startsWith('https://github.com/Psycomy/MikroBlockList/issues/new?template=false_positive.yml&'));
  assert.ok(url.includes('address=185.220.101.4'));
  assert.ok(url.includes('family=IPv4'));
  assert.ok(url.includes('variant=full'));
  assert.ok(url.includes('source=spamhaus-drop'));
});

test('report URL for a lite-only entry without sources', () => {
  const query = q('5.5.5.5');
  const url = reportUrl(query, search(index, query, NOW));
  assert.ok(url.includes('variant=lite'));
  assert.ok(url.includes('source=unknown'));
});

test('report URL encodes CIDR and IPv6', () => {
  const query = q('2a0e:1::/48');
  const url = new URL(reportUrl(query, search(index, query, NOW)));
  assert.equal(url.searchParams.get('address'), '2a0e:1::/48');
  assert.equal(url.searchParams.get('family'), 'IPv6');
  assert.ok(!url.search.includes('/48'));
});

// --- formatCount ---

test('formatCount groups thousands per language', () => {
  assert.equal(formatCount(39480, 'es'), '39.480');
  assert.equal(formatCount(39480, 'en'), '39,480');
  assert.equal(formatCount(1234567, 'es'), '1.234.567');
  assert.equal(formatCount(999, 'en'), '999');
  assert.equal(formatCount(0, 'es'), '0');
});

test('maskCidr hides the host part of both families', () => {
  assert.equal(maskCidr('203.0.113.0/24'), '203.0.x.x');
  assert.equal(maskCidr('1.2.3.4/32'), '1.2.x.x');
  assert.equal(maskCidr('2a0e::/32'), '2a0e:xxxx::');
  assert.equal(maskCidr('2001:470:1:332::8/128'), '2001:xxxx::');
  assert.equal(maskCidr('::/0'), '::');
  assert.equal(maskCidr('2001:0:5::/48'), '2001:xxxx::');
  assert.equal(maskCidr('2001:0:0:5::/64'), '2001:xxxx::');
  assert.equal(maskCidr('2001::/16'), '2001:xxxx::');
  assert.equal(maskCidr('0:0:5::/48'), '0:xxxx::');
  assert.equal(maskCidr('2001:DB8::1/128'), '2001:xxxx::');
  assert.equal(maskCidr('nonsense'), '?');
  assert.equal(maskCidr(undefined), '?');
});

test('resultTone: unknown states are amber, never green', () => {
  const r = (state, stale = false) => ({ state, stale });
  assert.equal(resultTone(r('blocked')), 'bad');
  assert.equal(resultTone(r('blocked', true)), 'bad');
  assert.equal(resultTone(r('clear')), 'ok');
  assert.equal(resultTone(r('clear', true)), 'warn');
  assert.equal(resultTone(r('partial')), 'warn');
  assert.equal(resultTone(r('v6-unpublished')), 'warn');
});

test('exampleIp: a listed address from the first full entry with a country', () => {
  const index = buildIndex(sample());
  const ip = exampleIp(index);
  assert.ok(ip);
  assert.equal(search(index, parseQuery(ip), NOW).state, 'blocked');
  assert.equal(exampleIp(buildIndex({
    generated_at: NOW.toISOString(), sources: [],
    v4: { full: [['10.0.0.0/24', [0], ''], ['192.0.2.7/32', [0], 'NL']], lite: [] },
  })), '192.0.2.7');
  assert.equal(exampleIp(buildIndex({
    generated_at: NOW.toISOString(), sources: [],
    v4: { full: [['10.0.0.0/24', [0], 'NL']], lite: [] },
  })), '10.0.0.1');
  assert.equal(exampleIp(buildIndex({ generated_at: NOW.toISOString(), sources: [],
    v4: { full: [], lite: [] } })), null);
});

test('countryKind: no DB-IP data shows nothing, never "several countries"', () => {
  assert.equal(countryKind('', null), 'none');
  assert.equal(countryKind('', ''), 'none');
  assert.equal(countryKind('DE', null), 'none');
});

test('countryKind: with DB-IP data the code distinguishes several, unknown and a country', () => {
  assert.equal(countryKind('', '2026-10'), 'several');
  assert.equal(countryKind('ZZ', '2026-10'), 'unknown');
  assert.equal(countryKind('zz', '2026-10'), 'unknown');
  assert.equal(countryKind('DE', '2026-10'), 'code');
});
