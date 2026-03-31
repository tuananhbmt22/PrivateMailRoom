# Kajima Mailroom — Session Memory (2026-03-31)

## Project Overview
AI-powered document classification system for NSW local councils. Automates email intake, classification into 20 departmental folders, and provides a real-time monitoring dashboard. Built by Kajima with Kiro (Claude Opus) + Cael (OpenClaw).

## Architecture
- **Python 3.11+** backend, **Flask** dashboard, **vanilla JS** frontend
- **Local LLM**: `medina-qwen3.5-27b-openclaw` at `http://192.168.222.1:1234` (LM Studio)
- **Air-gapped**: all inference local, no data leaves the network
- **Multi-tenant**: one generic prompt + council-specific folder tree JSON

## Key Files
- `config/classification_only_prompt.md` — generic classification system prompt
- `config/classification_only_tree.json` — council-specific folder rules (20 folders + junk + undetermined)
- `config/llm.yaml` — LLM endpoint config (timeout: 300s)
- `config/email.yaml` — IMAP email config (Gmail connected, since_date filtering)
- `config/dashboard_settings.json` — skills toggle, dev mode
- `engine/classifier.py` — classification engine (reads events, sends to LLM)
- `engine/dispatcher.py` — atomic file mover with receipts
- `engine/email_ingester.py` — IMAP poller with UID dedup
- `engine/graph_mail.py` — Microsoft 365 OAuth2 client (built, untested)
- `engine/history.py` — immutable movement chain (blockchain-style audit)
- `engine/junk.py` — junk fingerprint system
- `engine/llm.py` — local LLM client (supports json_schema toggle, max_tokens override)
- `engine/skill_runner.py` — 3-call skill pipeline
- `dashboard/app.py` — Flask routes (30+ endpoints)
- `dashboard/static/js/dashboard.js` — all frontend logic
- `dashboard/static/css/dashboard.css` — dark theme UI
- `dashboard/templates/index.html` — single page app
- `skills/skills.md` — master skill list
- `skills/parking_scroll.json` — parking skill (full)
- `skills/waste_management_scroll.json` — waste skill (full)
- `skills/companion_animals_scroll.json` — companion animals skill (full)
- `skills/SCROLL_DEVELOPMENT_GUIDE.md` — standard procedure for creating new scrolls
- `Test_Council/` — test council with departments, receive_channel, junk, undetermined

## Current State (What Works)
- Email polling (Gmail IMAP) with UID dedup and since_date filtering
- Classification engine: event-based, batch files, confidence threshold
- Dispatcher: atomic move, _classification.json receipt
- Dashboard: radial folder map, SVG route lines, real-time animations
- Folder drag-and-drop with layout templates (save/load/reset)
- Event management: reassign (wizard), requeue, history chain
- Junk folder with fingerprint system ("never show again")
- Skills pipeline: 3-call chain (match → scroll → classify)
- Pipeline display: progressive stage updates per event
- Dev mode toggle (raw JSON vs human-friendly views)
- Multi-provider email: Gmail IMAP, Microsoft 365 OAuth2, Custom IMAP
- Settings panel: onboard date, skills toggle, dev mode, layout templates

## Skills Pipeline (3-Call Chain)
1. **Call 1 — Skill Match**: minimal prompt, 64 max_tokens, returns `{"skill_id":"parking","confidence":0.95}`
2. **Call 2 — Scroll Execution**: loads `{skill}_scroll.json`, follows instructions, returns structured analysis
3. **Call 3 — Classification**: standard classifier, enhanced with skill result if available
- Each call is a separate API endpoint for progressive frontend updates
- `use_json_schema=False` for calls 1+2, `True` for call 3

## Known Bugs / In Progress
- Scroll execution can be slow (27B model, complex scroll) — timeout set to 300s
- Model sometimes outputs duplicate JSON + analysis text — parser extracts first JSON block only
- Template save bar snapshot comparison needs testing after drag-back-to-original
- Draft reply feature: scaffolded but needs rewiring to use skill results (not standalone)
- Requeue: wizard modal approach works but had intermittent first-click issues (fixed with full step reset)

## Parked Features (Not Built Yet)
- **Reply drafting**: `config/reply_prompt.md` exists, `draftReply()` JS function exists, needs to use `_skill_result.json` + scroll response templates
- **Dashboard authentication**: no login, anyone on LAN can access
- **Audit CSV export**: councils need downloadable compliance reports
- **Action routing layer**: A1-A32 codes from original system prompt
- **File watcher service**: watchdog for auto event detection
- **SQLite traceability DB**: currently using JSON files
- **CM9 / TechnologyOne integration**
- **System hardening**: systemd, logrotate, healthcheck

## GitHub
- Repo: `https://github.com/tuananhbmt22/PrivateMailRoom.git`
- Push method: rsync to `/tmp/mailroom-push/`, commit there, push
- Excludes: `config/email.yaml`, `config/oauth.yaml`, `.token_cache.json`, `_processed_uids.json`

## How to Resume
1. Read this file for context
2. Read `skills/SCROLL_DEVELOPMENT_GUIDE.md` for scroll creation procedure
3. Read `.kiro/specs/mailroom/tasks.md` for the full task list
4. Dashboard: `python run_dashboard.py --council Test_Council --port 5000`
5. LLM must be running at `192.168.222.1:1234`
