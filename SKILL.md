---
name: notifications
description: Use when the owner asks to be told when something happens or finishes, when a long job you started is done, or when something needs their attention. Send them a short notification with the Notifications app.
---

# Notifications

The Notifications app is the owner's own notification server on this box. What you send shows up on their phone (ntfy app) and in the Notifications web app.

## Tools

- `notify` — send one notification. `message` is required; add a short `title`. Use `priority: high` only when it needs them soon and `urgent` only for real problems. Add a `link` (https) when there is something to open.
- `notifications_recent` — what was sent lately (default: last 24 hours). Check it before repeating yourself.

## When to notify

- They asked: "tell me when…", "let me know if…", "ping me when it's done".
- A long job you started for them finished or failed, and they are probably not watching the chat.
- Something needs their decision or action.

## When not to

- Don't notify for every small step, or for things you are already telling them in the chat they are looking at.
- Never put passwords, keys, codes or other secrets in a notification.
- One notification per event. If nothing changed, don't send another.

## Style

Plain and short: what happened and what (if anything) they need to do. For example: title "Video ready", message "Your edit of beach.mp4 is in Files → Videos/Edited."
