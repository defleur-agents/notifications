# Notifications

Your Wizard's own notification server: unmodified [ntfy](https://ntfy.sh) on your box. The agent tells you when something is done or needs you.

A [Wizard app](https://www.aiwizards.com/wizard-apps): install it from the Wizard Apps screen, or with the Wizard App Manager on any box running stock [Runtipi](https://runtipi.io). It fills the `notifications` system spot.

- `wizard.md`: what the app is and what it gives the agent (spec: wizard.md v0.2).
- `runtipi/`: stock Runtipi `config.json` and `docker-compose.yml`. The engine is the official `binwiederhier/ntfy` image, pinned by digest, unchanged.
- `mcp/`: the app's MCP front (`front.py` + `tools.py`), inlined into the compose file by `python3 mcp/inline.py`. It serves `/mcp` (tools `notify` and `notifications_recent`, box-only) and passes everything else unchanged to ntfy.
- `SKILL.md`: the skill the agent loads when this app is installed.
- `.github/workflows/publish.yml`: every push to `main` re-publishes to the index at apps.aiwizards.com.

## On your phone

Install the free ntfy app, add this server's private address (for example `https://mybox.tail1234.ts.net:8790`), and subscribe to the topic `wizard`. On iPhone, set the optional relay (`https://ntfy.sh`) in the app's settings on the box if you want notifications instantly; it forwards only a wake-up ping, never the text.

## License

ntfy is Apache-2.0 (LICENSE). The packaging in this repo is Apache-2.0 as well.
