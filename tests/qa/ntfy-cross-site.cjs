// Real Chromium + real ntfy v2.29.0 + real Notifications front (base vs candidate). A page on another origin tries
// to read and publish the owner's notifications through the front, the way any website in the owner's browser can.
// Usage: node ntfy-cross-site.cjs <front port> <evil port>   (no route interception: Chromium does its own CORS)
const { chromium } = require(process.env.PW || 'playwright-core');
const http = require('http');
const [frontPort, evilPort] = process.argv.slice(2).map(Number);
const front = `http://127.0.0.1:${frontPort}`;
const page = `<!doctype html><title>evil</title><script>
window.run = async () => {
  const out = {};
  try { const r = await fetch('${front}/wizard/json?poll=1&since=all'); out.read = { status: r.status, body: (await r.text()).slice(0, 200) }; }
  catch (e) { out.read = { error: String(e) }; }
  try { const r = await fetch('${front}/wizard', { method: 'POST', body: 'PHISH-' + Date.now() }); out.publishCors = r.status; }
  catch (e) { out.publishCors = String(e); }
  try { await fetch('${front}/wizard', { method: 'POST', mode: 'no-cors', body: 'NOCORS-PHISH' }); out.publishNoCors = 'sent'; }
  catch (e) { out.publishNoCors = String(e); }
  await new Promise(r => { const i = new Image(); i.onload = i.onerror = r; i.src = '${front}/wizard/publish?message=IMG-PHISH'; });
  out.img = 'sent';
  return out;
};</script>`;
const evil = http.createServer((q, s) => { s.writeHead(200, { 'content-type': 'text/html' }); s.end(page); }).listen(evilPort, '127.0.0.1');
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME || undefined });
  try {
    const p = await (await b.newContext()).newPage();
    await p.goto(`http://127.0.0.1:${evilPort}/`);
    const hostile = await p.evaluate(() => window.run());
    // The ntfy web app itself (same origin, opened by a top-level visit from another site's link).
    const web = await (await b.newContext()).newPage();
    const nav = await web.goto(`${front}/`, { referer: 'https://mail.example/' });
    const own = await web.evaluate(async () => {
      const w = await fetch('/wizard', { method: 'POST', body: 'WEBAPP-OK' });
      const r = await fetch('/wizard/json?poll=1&since=all');
      return { publish: w.status, read: r.status, sawOwn: (await r.text()).includes('WEBAPP-OK') };
    });
    console.log(JSON.stringify({ hostile, webApp: { page: nav.status(), title: await web.title(), ...own } }));
  } finally { await b.close(); evil.close(); }
})();
