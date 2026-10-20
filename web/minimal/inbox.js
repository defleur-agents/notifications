'use strict';
(() => {
  const el = id => document.getElementById(id);
  let filter = 'unread', items = [], selected = null, generation = 0, busy = false, allReadArmed = false;
  const titleOf = it => String(it.title || String(it.message || '').split('\n')[0] || 'Untitled notification');
  const say = text => { el('status').textContent = text || ''; };
  const disarmAllRead = () => { allReadArmed = false; el('allread').textContent = 'Mark all read'; };
  function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; }
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
    if (selected) paintDetail();
  }
  function action(label, handler, disabled = false) {
    const b = node('button', label); b.type = 'button'; b.disabled = disabled || busy; b.onclick = handler; return b;
  }
  function paintDetail() {
    const it = selected;
    el('subject').textContent = titleOf(it);
    const date = new Date(Number(it.time) * 1000);
    el('meta').textContent = [Number.isNaN(date.getTime()) ? '' : date.toLocaleString(), it.read ? 'Read' : 'Unread', it.flag ? 'Flagged' : '', it.archived ? 'Archived' : ''].filter(Boolean).join(' · ');
    el('body').textContent = String(it.message || '');
    const a = el('actions'); a.replaceChildren();
    const reply = action('Start chat', () => {
      try {
        const receipt = window.WizardApp.nav.openContextDraft({id: String(it.id), title: titleOf(it), message: String(it.message || '')});
        if (receipt?.kind !== 'unsent-draft' || receipt.sent !== false) throw new Error('Could not confirm an unsent draft.');
        say('Draft opened in Chats. Nothing sent.');
      } catch (error) { say(error.message || 'Could not open Chats.'); }
    }); reply.setAttribute('aria-describedby', 'reply-note'); a.append(reply);
    a.append(action(it.read ? 'Mark unread' : 'Mark read', () => mark([it.id], {read: !it.read})));
    a.append(action(it.flag ? 'Unflag' : 'Flag', () => mark([it.id], {flag: !it.flag})));
    a.append(action(it.archived ? 'Move to inbox' : 'Archive', () => mark([it.id], {archived: !it.archived})));
    try {
      const url = new URL(it.link);
      if (url.protocol === 'https:') { const link = node('a', 'Open link'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; a.append(link); }
    } catch (_) {}
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
      paint(); say('');
      const c = result.counts || {};
      if (parent !== window) parent.postMessage({type: 'wizard:notifications:count', unread: Number(c.unread) || 0, needs: Number(c.needs) || 0}, location.origin);
    } catch (error) { if (version === generation) say(error.message); }
  }
  async function mark(ids, marks) {
    if (busy || !ids.length) return;
    busy = true; if (selected) paintDetail();
    try {
      await api('mark', {ids, ...marks});
      if (selected && ids.includes(selected.id)) selected = {...selected, ...marks, ...(marks.archived === true ? {read: true} : {})};
      await load();
    } catch (error) { say(error.message); }
    finally { busy = false; if (selected) paintDetail(); }
  }
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
  el('more').onclick = () => { disarmAllRead(); el('options').hidden = !el('options').hidden; el('more').setAttribute('aria-expanded', String(!el('options').hidden)); };
  el('refresh').onclick = load;
  el('allread').onclick = () => {
    const ids = visible().filter(it => !it.read).map(it => it.id);
    if (!ids.length) return;
    if (!allReadArmed) { allReadArmed = true; el('allread').textContent = 'Confirm mark all read'; return; }
    allReadArmed = false; el('allread').textContent = 'Mark all read'; mark(ids, {read: true});
  };
  window.__wizardCloseOverlay = () => {
    if (!el('options').hidden) { disarmAllRead(); el('options').hidden = true; el('more').setAttribute('aria-expanded', 'false'); el('more').focus(); return true; }
    return closeDetail();
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') window.__wizardCloseOverlay(); });
  window.WizardApp?.ready({name: 'Notifications'});
  window.WizardApp?.back?.register?.({canGoBack: () => !!selected || !el('options').hidden, back: () => window.__wizardCloseOverlay()});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden && !busy) load(); }, 60000);
  load();
})();
