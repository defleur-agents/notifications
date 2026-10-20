# Notifications inbox

The source front adds `/notifications/` and the existing inbox API/marks without changing ntfy. `INBOX_TOPICS=wizard,cron`, `INBOX_STATE=/data/inbox.json` and a writable persistent `/data` are required for inbox marks. The MCP front remains box-only for agent tools.

Default HTML/CSS/JS are preserved. Only verified host appearance skin IDs `obsidian`, `obsidian-light`, `landscapes` select the compact title-list/detail screen. Switching within those skins preserves detail and scroll. Crossing to another skin replaces the app document with its original screen (no top-level history entry).

Start chat uses the additive `WizardApp.nav.openContextDraft({id,title,message})` capability from wizard-ux #221. The verified installed Notifications document can create/focus a canonical unsent draft; no model run, send, server session mutation, URL context, or notification mark accompanies it. Notification text is explicitly quoted untrusted reference DATA. Repeated activation preserves edits and focuses the same draft; sent/discarded handoffs fail closed rather than creating another.

Generate assets with `python mcp/embed_minimal.py`, then `python mcp/inline.py`; check with `python mcp/inline.py --check` and `python -m unittest discover -s tests -v`. `web/minimal/` contains the source; `mcp/minimal_assets.py` is generated for the stock Python front. Existing source-app private command overrides can deploy this front while preserving their environment and volume settings; no legacy registry spoof or custom-view activation is involved.
