/* Parent-admitted forum only: synthetic hosted proxy, real shared host/client and palette sources. */
'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {chromium, devices} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {PNG} = require('pngjs');
const OUT = path.resolve(process.argv[2]);
const SHELL = path.resolve(process.argv[3]);
fs.mkdirSync(OUT, {recursive: true});
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const receipt = {scope: 'Synthetic hosted proxy, not canonical shell or physical devices', profiles: [], pass: false};
async function fixture() {
  const proc = spawn(process.env.PYTHON || 'python3', [path.join(__dirname, 'preservation_fixture.py'), '--shell-web', SHELL], {stdio: ['ignore', 'pipe', 'pipe']});
  let stdout = '', stderr = '';
  proc.stderr.on('data', b => stderr += b);
  const origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Fixture startup timeout: ' + stderr)), 15000);
    proc.stdout.on('data', b => { stdout += b; for (const line of stdout.split('\n')) { try { const v = JSON.parse(line); if (v.origin) { clearTimeout(timer); resolve(v.origin); return; } } catch {} } });
    proc.on('exit', code => {clearTimeout(timer); reject(new Error('Fixture exited ' + code + ': ' + stderr));});
  }).catch(error => {proc.kill('SIGTERM'); throw error;});
  return {proc, origin, stop: async () => {if (proc.exitCode === null) {proc.kill('SIGTERM'); await new Promise(r => proc.once('exit', r));}}};
}
async function settle(frame) {
  await frame.evaluate(async () => { await document.fonts.ready; await Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))); });
}
function changed(a, b) {
  a = PNG.sync.read(a); b = PNG.sync.read(b); assert.equal(a.width, b.width); assert.equal(a.height, b.height);
  let n = 0; for (let i=0;i<a.data.length;i+=4) if (!a.data.subarray(i,i+4).equals(b.data.subarray(i,i+4))) ++n; return n;
}
async function opaque(page, frame, label) {
  const menu = frame.locator('#options');
  const box = await menu.boundingBox(); assert(box && box.width > 40);
  const clip = {x: Math.ceil(box.x)+2, y: Math.ceil(box.y)+2, width: Math.floor(box.width)-4, height: Math.floor(box.height)-4};
  await frame.evaluate(() => {
    const e = document.createElement('div'); e.id='pixel-canary';
    Object.assign(e.style,{position:'absolute',right:'18px',bottom:'calc(100% + 4px)',width:'280px',height:'240px',fontSize:'28px',zIndex:'2',background:'magenta',color:'lime',pointerEvents:'none'});
    e.textContent='LOUD SYNTHETIC TEXT '.repeat(30); document.querySelector('#toolbar').append(e);
  });
  await settle(frame);
  const loud = await page.screenshot({clip});
  await frame.locator('#pixel-canary').evaluate(e => e.style.visibility='hidden');
  const blank = await page.screenshot({clip});
  assert.equal(changed(loud,blank),0,'text panel pixels depend on background: '+label);
  await menu.evaluate(e => e.style.background='transparent');
  await frame.locator('#pixel-canary').evaluate(e => e.style.visibility='visible');
  const badLoud = await page.screenshot({clip});
  await frame.locator('#pixel-canary').evaluate(e => e.style.visibility='hidden');
  const badBlank = await page.screenshot({clip});
  assert(changed(badLoud,badBlank)>0,'negative control did not exercise opacity oracle');
  await menu.evaluate(e => e.style.removeProperty('background'));
  await frame.locator('#pixel-canary').evaluate(e => e.remove());
  fs.writeFileSync(path.join(OUT,label+'-opaque.png'),blank);
}
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    for (const [profile, config] of [['phone',{...devices['Pixel 7'],viewport:{width:390,height:844}}], ['desktop',{viewport:{width:1280,height:900}}]]) {
      const srv = await fixture(); const context = await browser.newContext(config); const page = await context.newPage();
      const errors = [], external = [], delivered = []; const pending = [];
      const row = {profile, screenshots:[], delivered, errors, external}; receipt.profiles.push(row);
      page.on('pageerror', e=>errors.push(String(e)));
      await context.route('**/*', route => {const u = new URL(route.request().url()); if (u.origin !== srv.origin && u.protocol !== 'data:') {external.push(u.origin+u.pathname); return route.abort();} return route.continue();});
      page.on('response', response => { if (response.status()===200 && /\.(js|css)(\?|$)/.test(response.url())) pending.push(response.body().then(b => {if (b.length) delivered.push({url:response.url().replace(srv.origin,''),sha256:hash(b)});})); });
      try {
        await page.goto(srv.origin+'/fixture.html');
        const frame = await page.locator('iframe').elementHandle().then(h=>h.contentFrame());
        await frame.locator('.subject-row').first().waitFor();
        assert.equal(await frame.evaluate(()=>innerWidth), config.viewport.width);
        await page.waitForFunction(()=>WizAppearance.inspect().some(s=>s.acked));
        const identity = await frame.evaluate(()=>window.__identity = crypto.randomUUID());
        for (const skin of ['obsidian','obsidian-light']) {
          await page.locator('[data-skinpick="'+skin+'"]').click();
          await frame.waitForFunction(s => document.documentElement.dataset.skin===s,skin);
          await page.waitForFunction(()=>WizAppearance.inspect().every(s=>s.acked));
          assert.equal(await frame.evaluate(()=>window.__identity),identity,'skin change remounted app');
          await frame.locator('.subject-row').filter({hasText:/Backup check/i}).click();
          await frame.locator('#runs details').first().waitFor();
          assert.equal(await frame.locator('.run').count(),2);
          await frame.locator('#runs details').first().locator('summary').first().click();
          await settle(frame);
          const shot = path.join(OUT,profile+'-'+skin+'-runs.png'); await page.screenshot({path:shot}); row.screenshots.push({file:path.basename(shot),sha256:hash(fs.readFileSync(shot))});
          // Shared app Back closes the detail, never exits the fixture.
          await frame.locator('#subject').press('Escape');
          await frame.locator('#overview').waitFor({state:'visible'});
          await frame.locator('#more').click(); await opaque(page,frame,profile+'-'+skin);
          await frame.locator('#filters').click({position:{x:1,y:1}});
          await frame.locator('#options').waitFor({state:'hidden'});
          assert(await frame.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
        }
        await frame.locator('.subject-row').filter({hasText:'Fixture needs you'}).click();
        let release; const held = new Promise(r=>release=r);
        let intercepted; const admitted = new Promise(r=>intercepted=r);
        await context.route('**/notifications/api/mark',async route=>{intercepted();await held;await route.fulfill({status:500,contentType:'application/json',body:'{"error":"Synthetic rejected save"}'});});
        await frame.getByRole('button',{name:'Mark read',exact:true}).click(); await admitted;
        assert.equal(await frame.locator('#overview').isVisible(),true,'action waited for backend before paint');
        assert.equal(await frame.locator('.subject-row').filter({hasText:'Fixture needs you'}).count(),0,'optimistic read not painted');
        release(); await frame.getByText('Synthetic rejected save',{exact:true}).waitFor();
        await frame.locator('.subject-row').filter({hasText:'Fixture needs you'}).waitFor();
        await context.unroute('**/notifications/api/mark');
        await frame.locator('.subject-row').filter({hasText:'Fixture needs you'}).click();
        await frame.getByRole('button',{name:'Mark read',exact:true}).click();
        await frame.waitForFunction(()=>!document.querySelector('#status').textContent);
        assert.equal((await (await context.request.get(srv.origin+'/fixture/marks')).json())['fixture-needs-you'].read,true);
        await page.goto(srv.origin+'/notifications/?layout=minimal');
        await page.emulateMedia({colorScheme:'dark'}); await page.locator('.subject-row').first().waitFor(); await settle(page);
        assert.equal(await page.evaluate(()=>parent===window),true);
        const background = await page.locator('body').evaluate(e=>getComputedStyle(e).backgroundColor); row.standalone_background=background;
        assert.notEqual(background,'rgba(0, 0, 0, 0)');
        const shot = path.join(OUT,profile+'-standalone-dark.png'); await page.screenshot({path:shot}); row.screenshots.push({file:path.basename(shot),sha256:hash(fs.readFileSync(shot))});
        await Promise.all(pending); assert.equal(errors.length,0,errors.join('\n')); assert.equal(external.length,0,external.join('\n'));
        assert(delivered.some(v=>v.url==='/wizard-os-home/core/app-client.js'));
        assert(delivered.some(v=>v.url==='/wizard-os-home/core/appearance-host.js'));
      } finally {await context.close(); await srv.stop();}
    }
    receipt.pass=true;
  } finally {await browser.close(); fs.writeFileSync(path.join(OUT,'browser-receipt.json'),JSON.stringify(receipt,null,2)+'\n');}
})().catch(error=>{receipt.error=String(error.stack||error); fs.writeFileSync(path.join(OUT,'browser-receipt.json'),JSON.stringify(receipt,null,2)+'\n');console.error(error);process.exitCode=1;});
