'use strict';
/* Notification body → safe blocks. Pure data in, pure data out: no DOM, no HTML strings.
 * Supported subset: [text](https://…), bare https URLs, `code`, **bold**, MEDIA:/abs/path image lines.
 * A trailing "21 Oct 02:03 UTC" stamp is dropped when it only repeats the notification time.
 * (No dollar sign anywhere: the generated compose would interpolate it; (?![\s\S]) is the end anchor.) */
(function (root) {
  const MONTHS = {Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11};
  const STAMP = /^\s*(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{2}):(\d{2}) UTC\s*(?![\s\S])/;
  const MEDIA = /^\s*MEDIA:(\/\S{1,1000})\s*(?![\s\S])/;
  const INLINE = /\[([^\]\n]{1,300})\]\(([^\s)]{1,2000})\)|`([^`\n]{1,500})`|\*\*([^*\n]{1,500})\*\*|(https:\/\/[^\s<>()[\]`"']+)/g;
  const TRAIL = /[.,;:!?)\]'"]+(?![\s\S])/;

  function safeHref(raw) {
    try { const u = new URL(String(raw)); return u.protocol === 'https:' && !u.username && !u.password ? u.href : ''; }
    catch (_) { return ''; }
  }
  function inline(line) {
    const runs = []; let last = 0, m;
    const text = t => { if (t) { const prev = runs[runs.length - 1]; if (prev && prev.t === 'text') prev.text += t; else runs.push({t: 'text', text: t}); } };
    INLINE.lastIndex = 0;
    while ((m = INLINE.exec(line))) {
      text(line.slice(last, m.index)); last = INLINE.lastIndex;
      if (m[1] != null) { const href = safeHref(m[2]); if (href) runs.push({t: 'link', text: m[1], href}); else text(m[1]); }
      else if (m[3] != null) runs.push({t: 'code', text: m[3]});
      else if (m[4] != null) runs.push({t: 'bold', text: m[4]});
      else {
        let url = m[5], tail = '';
        const tm = TRAIL.exec(url); if (tm) { tail = tm[0]; url = url.slice(0, url.length - tail.length); }
        const href = safeHref(url); if (href) runs.push({t: 'link', text: url, href}); else text(url);
        text(tail);
      }
    }
    text(line.slice(last));
    return runs;
  }
  function stampTime(line, itemTime) {
    const m = STAMP.exec(line); if (!m || !itemTime) return null;
    const year = new Date(itemTime * 1000).getUTCFullYear();
    let best = null;
    for (const y of [year - 1, year, year + 1]) {
      const t = Date.UTC(y, MONTHS[m[2]], Number(m[1]), Number(m[3]), Number(m[4])) / 1000;
      if (best === null || Math.abs(t - itemTime) < Math.abs(best - itemTime)) best = t;
    }
    return best;
  }
  function parse(message, itemTime) {
    const lines = String(message || '').replace(/\r\n?/g, '\n').split('\n');
    // Drop trailing blanks and a trailing UTC stamp that repeats the notification time (within 30 minutes).
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    if (lines.length > 1) {
      const t = stampTime(lines[lines.length - 1], Number(itemTime));
      if (t !== null && Math.abs(t - Number(itemTime)) <= 1800) { lines.pop(); while (lines.length && !lines[lines.length - 1].trim()) lines.pop(); }
    }
    const blocks = []; let para = null, media = 0;
    for (const line of lines) {
      const mm = MEDIA.exec(line);
      if (mm) { para = null; blocks.push({type: 'media', path: mm[1], index: ++media}); continue; }
      if (!line.trim()) { para = null; continue; }
      if (!para) { para = {type: 'text', lines: []}; blocks.push(para); }
      para.lines.push(inline(line));
    }
    return blocks;
  }
  function firstLinkText(blocks, href) {
    for (const b of blocks) if (b.type === 'text') for (const l of b.lines) for (const r of l) if (r.t === 'link' && r.href === href && r.text !== href) return r.text;
    return '';
  }
  function friendlyTime(seconds, now) {
    const d = new Date(Number(seconds) * 1000); if (!seconds || Number.isNaN(d.getTime())) return '';
    const n = now ? new Date(now) : new Date();
    const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(n) - day(d)) / 86400000);
    const time = d.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
    if (diff === 0) return 'Today ' + time;
    if (diff === 1) return 'Yesterday ' + time;
    if (diff > 1 && diff < 7) return d.toLocaleDateString(undefined, {weekday: 'short'}) + ' ' + time;
    return d.toLocaleDateString(undefined, {day: 'numeric', month: 'short', ...(d.getFullYear() !== n.getFullYear() ? {year: 'numeric'} : {})}) + ' ' + time;
  }
  root.NotifRender = Object.freeze({parse, inline, safeHref, firstLinkText, friendlyTime});
})(typeof window !== 'undefined' ? window : globalThis);
