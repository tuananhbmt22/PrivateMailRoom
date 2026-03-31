# Kajima Mailroom Appliance

Air-gapped AI document classification system for local councils.

## What It Does
Automates document ingestion, classification, and routing for council departments. All processing happens locally — zero internet, zero data exfiltration.

## Architecture
```
receive_channel/ → Watcher → Classifier → Extractor → Dispatcher → Department Folders
                                 ↕              ↕            ↕
                              Skills          LLM DB      Traceability DB
```

## Quick Start (Development)
```bash
cp .env.example .env
# Edit .env with your local paths and LLM endpoint
pip install -r requirements.txt
python main.py
```

## Project Structure
```
apps/mailroom/
├── config/           # tree.yaml, map.md, settings.yaml
├── engine/           # Core pipeline: watcher, classifier, extractor, dispatcher
├── skills/           # Department skill files (.md)
├── db/               # SQLite schema and store
├── integrations/     # CM9, TechnologyOne connectors
├── dashboard/        # Admin web UI (Flask, LAN-only)
├── deploy/           # Systemd, logrotate, healthcheck
└── main.py           # Entry point
```

## Specs
Full requirements, design, and task breakdown:
- `.kiro/specs/mailroom/requirements.md`
- `.kiro/specs/mailroom/design.md`
- `.kiro/specs/mailroom/tasks.md`

## Security
- Air-gapped: no outbound network calls
- Immutable root filesystem in production
- Local SQLite only — no network databases
- LAN-only dashboard access
