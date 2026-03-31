---
inclusion: fileMatch
fileMatchPattern: "apps/mailroom/**"
---

# Mailroom Appliance — Steering

## Project Context
Kajima Document Handler — air-gapped AI mailroom for local councils.
Spec: #[[file:.kiro/specs/mailroom/requirements.md]]
Design: #[[file:.kiro/specs/mailroom/design.md]]
Tasks: #[[file:.kiro/specs/mailroom/tasks.md]]

## Coding Standards (Python)
- Python 3.11+ with type hints on every function
- Docstrings on all public functions (Google style)
- `async/await` for the pipeline (inference is I/O-bound to GPU)
- `dataclasses` for all data models — no raw dicts flowing through the pipeline
- `pathlib.Path` everywhere — no string path manipulation
- Error handling is mandatory, not optional. Every function that can fail must handle it explicitly.
- Logging via `logging` module, structured format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`

## Architecture Rules
- Traceability DB write BEFORE file move — never move without logging
- Atomic file operations: copy → verify → delete source
- Skills are pure `.md` files — no executable code in skills
- All config in YAML — no hardcoded paths, thresholds, or department names
- LLM interface is abstract — must support swapping models without engine changes
- Dashboard is optional — core pipeline runs headless

## Security (Non-Negotiable)
- Zero outbound network calls in any module
- No `requests`, `urllib`, or `httpx` calls to external URLs
- All file operations stay within configured base paths
- SQLite only — no network databases
- No telemetry, no analytics, no phone-home

## File Naming
- Python: `snake_case.py`
- Config: `snake_case.yaml`
- Skills: `department_name.md`
- Templates: `snake_case.html`

## Dependencies (Approved)
- `watchdog` — file system monitoring
- `flask` — dashboard (LAN only)
- `sqlite3` — stdlib, traceability DB
- `pyyaml` — config parsing
- `aiofiles` — async file operations
- `httpx` — LOCAL ONLY for LLM API and integration endpoints
- `rich` — CLI output formatting
- `python-dotenv` — environment config
