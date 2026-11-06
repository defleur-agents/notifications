# Mail-only private notification ingress

REUSE the installed Notifications readable editor, durable update inbox and delivery policy, and existing WAM Mail grant. EXTEND the same front process with an optional AF_UNIX count-only ingress. NEW services, relays, HTTP proxies and TCP fallbacks: none.

The listener is off unless `MAIL_SOCKET_ENABLED=on`. It binds only `/run/mail-notifications/mail.sock`, inside a dedicated same-owner directory, mode0700 uid/gid1000, with socket0600. Both actual containers must use identity uid mapping, uid1000, ALL capabilities dropped and no-new-privileges. Mount only this directory into Mail and Notifications; never mount parent data, credentials or home directories. Mail's readonly mount is defence in depth, not authentication. Every request must carry Mail's existing WAM outgoing Notifications grant; Notifications hashes it and matches only incoming `from=wizard-mail`, independently of the general enforcement flag. Revocation closes new requests and pending Mail-only retries. Grant rotation preserves already-accepted counts.

One newline-delimited JSON object, max1024 bytes. Exact allowed key sets:
- `method=ready, grant`: content-free storage/authorization readiness, no models or provider.
- `method=submit, grant, event_id, new_items, scan_changes`: event id `physical:` plus64 lowercase hex, integer counts0..100000, nonzero sum. No text/URL/tool/history/provider fields.
- `method=receipt, grant, event_id`: receipt for this Mail event only; no general Notifications history.

Notifications constructs fixed generic text and the pre-authorized thread link in `mail_socket.py`. The editor receives counts only, never a letter. Existing policy is intentional: accepted/fyi means inbox-only, suppressed/noise means no push, queued means editor retry, published means actual push. None of these are conflated. No bill/deadline summarization is offered. Stable ids prevent re-admission on retries/restarts; the pre-existing ntfy post-before-persist crash window is not claimed solved.

## Source and safe cutover
This branch adopts the ACTUAL serving front's generated minimal assets, not the stale registry recipe. Its unextended source reconstructs live SHA256 `7e5aff6a803eba420a8d7d0d13e50010732029193ff5065a24ba3d056840263e` from maintained `/home/telos/notifications-readable` base4ce9e73. Existing cron relay, readable retry/editing, TCP/MCP/UI/inbox routes remain. A failed optional socket startup leaves the existing front running; Mail readiness fails closed.

Merge only the two dedicated mounts and Notifications opt-in env into the CURRENT runtime overlay; do not replace it with this generic recipe. Precreate/chown/chmod the host directory before restart. Back up actual current front, runtime recipes and app data first; rollback restores them and disables the optional ingress. Leave Mail provider setup and owner credentials untouched. No live change before the one focused synthetic gate passes. No human notification is used for validation.

The owning fork `defleur-agents/notifications` was discovered PUBLIC. Do not push this owner-scoped branch there. The approved existing PRIVATE artifact repository is `defleur-agents/wizard-mail`, branch `james/notifications-mail-socket`, retaining this repository's actual maintained ancestry.
