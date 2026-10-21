// node tests/render.test.js — pure markdown-subset parser checks (no DOM).
'use strict';
const assert = require('node:assert');
require('../web/minimal/render.js');
const R = globalThis.NotifRender;

// Owner's real morning briefing shape.
const t1 = Date.UTC(2026, 9, 21, 2, 6, 17) / 1000;
let b = R.parse('Your morning report is ready: [Open Morning Report](https://oscarvps.tail6adf1a.ts.net:8444/wizard-os-home/?open=morning-report)\n\n21 Oct 02:03 UTC\n', t1);
assert.strictEqual(b.length, 1, 'trailing UTC stamp repeating the time is dropped');
const runs = b[0].lines[0];
assert.deepStrictEqual(runs[0], {t: 'text', text: 'Your morning report is ready: '});
assert.strictEqual(runs[1].t, 'link'); assert.strictEqual(runs[1].text, 'Open Morning Report');
assert.strictEqual(runs[1].href, 'https://oscarvps.tail6adf1a.ts.net:8444/wizard-os-home/?open=morning-report');
assert.strictEqual(R.firstLinkText(b, runs[1].href), 'Open Morning Report');

// A stamp far from the notification time is kept (it is information, not a duplicate).
b = R.parse('Backup done\n\n18 Oct 20:00 UTC', t1);
assert.strictEqual(b[0].lines.length + (b[1] ? 1 : 0), 2);

// Sniper shape: code, bare link, MEDIA, non-https scheme stays text.
const sniper = 'Reply `whitelist` / `whitelist all` now.\n\n1. Instagram · @x\nLink: https://www.instagram.com/fries4980.\nMEDIA:/home/oscar/.hermes/sniper_reports/2026-10-21/a_01.jpg\n\nLink: kakao-signature://Open Profile/x\n[bad](javascript:alert(1)) [http](http://example.com) **Bold**';
b = R.parse(sniper, 0);
assert.deepStrictEqual(b[0].lines[0].map(r => r.t), ['text', 'code', 'text', 'code', 'text']);
assert.strictEqual(b[0].lines[0][1].text, 'whitelist');
const link = b[1].lines[1].find(r => r.t === 'link');
assert.strictEqual(link.href, 'https://www.instagram.com/fries4980'); assert.strictEqual(b[1].lines[1].at(-1).text, '.');
assert.deepStrictEqual(b[2], {type: 'media', path: '/home/oscar/.hermes/sniper_reports/2026-10-21/a_01.jpg', index: 1});
const last = b[3].lines.flat();
assert.ok(!last.some(r => r.t === 'link'), 'kakao/javascript/http never become links');
assert.ok(last.some(r => r.t === 'bold' && r.text === 'Bold'));
assert.ok(last.some(r => r.t === 'text' && r.text.includes('bad')));
// Hostile markup is plain text data.
b = R.parse('<img src=x onerror=bad()> [x](https://user:pw@evil.example/)', 0);
assert.ok(b[0].lines[0].every(r => r.t === 'text'));
assert.strictEqual(R.safeHref('https://a.example/x'), 'https://a.example/x');
assert.strictEqual(R.safeHref('javascript:alert(1)'), '');
// MEDIA must be absolute and alone on its line.
assert.strictEqual(R.parse('see MEDIA:/a.jpg', 0)[0].type, 'text');
assert.strictEqual(R.parse('MEDIA:relative.jpg', 0)[0].type, 'text');
// Friendly time.
const now = new Date(2026, 9, 21, 12, 0).getTime();
assert.match(R.friendlyTime(new Date(2026, 9, 21, 9, 6).getTime() / 1000, now), /^Today 9:06/);
assert.match(R.friendlyTime(new Date(2026, 9, 20, 9, 6).getTime() / 1000, now), /^Yesterday /);
// dropLink: the primary button's link never shows twice.
const flat = bs => bs.filter(x => x.type === 'text').map(x => x.lines.map(l => l.map(r => r.text).join('')).join('\n')).join('\n\n');
const mb = R.parse('Your morning report is ready: [Open Morning Report](https://h.example/?open=morning-report)', 0);
let d = R.dropLink(mb, 'https://h.example/?open=morning-report');
assert.strictEqual(flat(d), 'Your morning report is ready', 'sole link removed, trailing colon tidied');
assert.ok(!d.flatMap(x => x.lines.flat()).some(r => r.t === 'link'));
d = R.dropLink(R.parse('Done.\nLink: https://a.example/x.', 0), 'https://a.example/x');
assert.strictEqual(flat(d), 'Done.', 'a bare "Link:" line goes with its link');
d = R.dropLink(R.parse('See https://a.example/x for details', 0), 'https://a.example/x');
assert.strictEqual(flat(d), 'See https://a.example/x for details', 'mid-sentence link stays (quieted by the page)');
const two = R.parse('[A](https://a.example/) and [B](https://b.example/)', 0);
assert.strictEqual(R.dropLink(two, 'https://a.example/'), two, 'several links: kept (the duplicate is only quieted)');
const other = R.parse('[A](https://a.example/)', 0);
assert.strictEqual(R.dropLink(other, 'https://z.example/'), other, 'a different link is kept');
console.log('render tests: ok');
