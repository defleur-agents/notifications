'use strict';
(() => {
  const el = id => document.getElementById(id);
  const R = window.NotifRender;
  let filter = 'unread', items = [], selected = null, generation = 0, busy = false, allReadArmed = false;
  const titleOf = it => String(it.title || String(it.message || '').split('\n')[0] || 'Untitled notification');
  const say = text => { el('status').textContent = text || ''; };
  const disarmAllRead = () => { allReadArmed = false; el('allread').textContent = 'Mark all read'; };
  function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; }
  // Static, trusted icon paths only (never notification text).
  const ICONS = {
    chat: 'M4 5h16v11H9l-5 4z', read: 'M4 12l5 5L20 6', unread: 'M3 6h18v12H3zM3 6l9 7 9-7',
    flag: 'M6 21V4m0 0h11l-2 4 2 4H6', archive: 'M4 5h16v4H4zM6 9v10h12V9M10 13h4', inbox: 'M4 13l3-8h10l3 8v6H4zM4 13h5l1 2h4l1-2h5',
  };
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', ICONS[name]); svg.append(p); return svg;
  }
  async function api(path, data) {
    const response = await fetch('api/' + path, data ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)} : {cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Notifications are not reachable. Try Refresh.');
    return result;
  }
  function visible() { return filter === 'unread' ? items.filter(it => !it.read) : items; }
  function paint() {
    el('list').replaceChildren(...visible().map(it => {
      const li = node('li'), button = node('button', null, 'subject-row' + (it.read ? '' : ' unread'));
      button.type = 'button'; button.dataset.id = it.id;
      button.setAttribute('aria-label', titleOf(it) + (it.read ? '' : ', unread'));
      const dot = node('span', '', 'dot'); dot.setAttribute('aria-hidden', 'true');
      button.append(dot, node('span', titleOf(it), 'title')); li.append(button); return li;
    }));
    el('empty').hidden = visible().length !== 0;
    el('empty').textContent = filter === 'unread' ? 'No unread notifications' : filter === 'flagged' ? 'No flagged notifications' : filter === 'archive' ? 'No archived notifications' : 'No notifications';
    document.querySelectorAll('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === filter)));
    el('overview').hidden = !!selected; el('detail').hidden = !selected;
    // The detail is one quiet page: no filter bar under it.
    el('toolbar').hidden = !!selected; document.documentElement.classList.toggle('nt-detail', !!selected);
    if (selected) paintDetail();
  }
  // ---- body: parsed blocks → DOM nodes (textContent and createElement only, no HTML parsing) ----
  function linkNode(href, text) {
    const a = node('a', text, 'body-link'); a.href = href; a.rel = 'noopener noreferrer';
    // Same-origin Wizard links stay in the shell (it intercepts them); other https links open outside.
    a.target = '_blank'; return a;
  }
  const mediaURL = path => 'media' + path.split('/').map(encodeURIComponent).join('/');
  function mediaNode(block) {
    const url = mediaURL(block.path), name = 'Picture ' + block.index;
    const a = node('a', null, 'thumb'); a.href = url; a.setAttribute('aria-label', 'Open ' + name.toLowerCase());
    const img = node('img'); img.alt = name; img.loading = 'lazy'; img.decoding = 'async'; img.src = url;
    img.onerror = () => { const miss = node('p', 'Picture unavailable', 'thumb-missing'); a.replaceWith(miss); };
    a.append(img);
    a.onclick = e => { if (e.defaultPrevented) return; e.preventDefault(); openViewer(url, name); };
    return a;
  }
  function paintBody(it) {
    const blocks = R.parse(it.message, Number(it.time)), out = [];
    for (const b of blocks) {
      if (b.type === 'media') { out.push(mediaNode(b)); continue; }
      const p = node('p');
      b.lines.forEach((runs, i) => {
        if (i) p.append(node('br'));
        for (const r of runs) {
          if (r.t === 'text') p.append(document.createTextNode(r.text));
          else if (r.t === 'link') p.append(linkNode(r.href, r.text));
          else if (r.t === 'code') p.append(node('code', r.text));
          else if (r.t === 'bold') p.append(node('strong', r.text));
        }
      });
      out.push(p);
    }
    el('body').replaceChildren(...out);
    return blocks;
  }
  function openViewer(url, name) {
    const v = el('viewer'), img = el('viewer-img'); img.src = url; img.alt = name; v.hidden = false; el('viewer-close').focus();
  }
  function closeViewer() { if (el('viewer').hidden) return false; el('viewer').hidden = true; el('viewer-img').removeAttribute('src'); return true; }
  // ---- actions ----
  function action(label, iconName, handler, cls) {
    const b = node('button', null, cls || 'act'); b.type = 'button'; b.disabled = busy; b.onclick = handler;
    if (iconName) b.append(icon(iconName)); b.append(node('span', label)); return b;
  }
  function startChat(it) {
    try {
      const receipt = window.WizardApp.nav.openContextDraft({id: String(it.id), title: titleOf(it), message: String(it.message || '')});
      if (receipt?.kind !== 'unsent-draft' || receipt.sent !== false) throw new Error('Could not confirm an unsent draft.');
      say('Draft opened in Chats. Nothing sent.');
    } catch (error) { say(error.message || 'Could not open Chats.'); }
  }
  function primaryLink(it, blocks) {
    const own = R.safeHref(it.link);
    if (own) return {href: own, label: R.firstLinkText(blocks, own) || 'Open link'};
    for (const b of blocks) if (b.type === 'text') for (const l of b.lines) for (const r of l) if (r.t === 'link' && r.text !== r.href) return {href: r.href, label: r.text};
    return null;
  }
  function paintDetail() {
    const it = selected;
    el('subject').textContent = titleOf(it);
    el('meta').textContent = [R.friendlyTime(it.time), it.flag ? 'Flagged' : '', it.archived ? 'Archived' : ''].filter(Boolean).join(' · ');
    const blocks = paintBody(it);
    const link = primaryLink(it, blocks), primary = el('primary'), row = el('actions');
    if (link) {
      const a = node('a', null, 'btn-primary'); a.href = link.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.append(node('span', /^open\b/i.test(link.label) ? link.label : 'Open ' + link.label)); primary.replaceChildren(a);
    } else primary.replaceChildren(action('Start chat', 'chat', () => startChat(it), 'btn-primary'));
    const acts = [];
    if (link) acts.push(action('Start chat', 'chat', () => startChat(it)));
    acts.push(action(it.read ? 'Mark unread' : 'Mark read', it.read ? 'unread' : 'read', () => mark([it.id], {read: !it.read})));
    acts.push(action(it.flag ? 'Unflag' : 'Flag', 'flag', () => mark([it.id], {flag: !it.flag})));
    acts.push(action(it.archived ? 'Move to inbox' : 'Archive', it.archived ? 'inbox' : 'archive', () => mark([it.id], {archived: !it.archived})));
    if (it.flag) acts.find(b => b.textContent === 'Unflag')?.setAttribute('aria-pressed', 'true');
    row.style.setProperty('--n', String(acts.length)); row.replaceChildren(...acts);
  }
  async function load() {
    const version = ++generation;
    const view = filter === 'unread' ? 'inbox' : filter;
    try {
      const result = await api('items?view=' + encodeURIComponent(view) + '&limit=500');
      if (version !== generation) return;
      if (!Array.isArray(result.items)) throw new Error('Notifications returned an invalid list.');
      items = result.items;
      if (selected) selected = items.find(it => it.id === selected.id) || selected;
      // A background refresh never rebuilds an open detail (pictures and scroll stay put).
      if (selected) paintList(); else paint();
      say('');
      const c = result.counts || {};
      if (parent !== window) parent.postMessage({type: 'wizard:notifications:count', unread: Number(c.unread) || 0, needs: Number(c.needs) || 0}, location.origin);
    } catch (error) { if (version === generation) say(error.message); }
  }
  function paintList() { const keep = selected; selected = null; const top = el('scroll').scrollTop; paint(); selected = keep; el('overview').hidden = true; el('detail').hidden = false; el('toolbar').hidden = true; document.documentElement.classList.add('nt-detail'); el('scroll').scrollTop = top; }
  async function mark(ids, marks) {
    if (busy || !ids.length) return;
    busy = true; if (selected) paintActionsOnly();
    try {
      await api('mark', {ids, ...marks});
      if (selected && ids.includes(selected.id)) selected = {...selected, ...marks, ...(marks.archived === true ? {read: true} : {})};
      await load();
    } catch (error) { say(error.message); }
    finally { busy = false; if (selected) paintActionsOnly(); }
  }
  function paintActionsOnly() { const top = el('scroll').scrollTop; paintDetail(); el('scroll').scrollTop = top; }
  function closeDetail() {
    if (!selected) return false;
    const id = selected.id; selected = null; paint();
    const row = [...document.querySelectorAll('.subject-row')].find(b => b.dataset.id === id); (row || el('scroll')).focus(); return true;
  }
  el('list').onclick = e => {
    const b = e.target.closest('[data-id]'); if (!b) return;
    selected = items.find(it => it.id === b.dataset.id); if (!selected) return;
    paint(); el('scroll').scrollTop = 0; el('subject').focus();
  };
  document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
    disarmAllRead();
    filter = b.dataset.filter; selected = null; el('options').hidden = true; el('more').setAttribute('aria-expanded', 'false'); el('scroll').scrollTop = 0;
    // Clear old rows while the new filter loads, never label stale data “All”.
    items = []; paint(); load();
  });
  el('back').onclick = closeDetail;
  el('viewer-close').onclick = closeViewer;
  el('viewer').onclick = e => { if (e.target === el('viewer')) closeViewer(); };
  el('more').onclick = () => { disarmAllRead(); el('options').hidden = !el('options').hidden; el('more').setAttribute('aria-expanded', String(!el('options').hidden)); };
  el('refresh').onclick = load;
  el('allread').onclick = () => {
    const ids = visible().filter(it => !it.read).map(it => it.id);
    if (!ids.length) return;
    if (!allReadArmed) { allReadArmed = true; el('allread').textContent = 'Confirm mark all read'; return; }
    allReadArmed = false; el('allread').textContent = 'Mark all read'; mark(ids, {read: true});
  };
  window.__wizardCloseOverlay = () => {
    if (closeViewer()) return true;
    if (!el('options').hidden) { disarmAllRead(); el('options').hidden = true; el('more').setAttribute('aria-expanded', 'false'); el('more').focus(); return true; }
    return closeDetail();
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') window.__wizardCloseOverlay(); });
  window.WizardApp?.ready({name: 'Notifications'});
  window.WizardApp?.back?.register?.({canGoBack: () => !!selected || !el('options').hidden || !el('viewer').hidden, back: () => window.__wizardCloseOverlay()});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden && !busy) load(); }, 60000);
  load();
})();
