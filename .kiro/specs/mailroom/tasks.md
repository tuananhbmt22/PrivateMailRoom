# Kajima Mailroom Appliance — Implementation Tasks

## Phase 1: Foundation & Core Pipeline

### Task 1: Project Scaffold & Configuration
- **Status**: not started
- **Requirements**: REQ-1, REQ-2, REQ-5
- **Description**: Create the `apps/mailroom/` directory structure, `pyproject.toml`, `requirements.txt`, and configuration files (`config/tree.yaml`, `config/map.md`, `config/settings.yaml`). Set up Python package structure with `__init__.py` files. Create `.env.example` with all configurable paths.
- **Files**:
  - `apps/mailroom/pyproject.toml`
  - `apps/mailroom/requirements.txt`
  - `apps/mailroom/config/tree.yaml`
  - `apps/mailroom/config/map.md`
  - `apps/mailroom/config/settings.yaml`
  - `apps/mailroom/engine/__init__.py`
  - `apps/mailroom/db/__init__.py`
  - `apps/mailroom/integrations/__init__.py`
  - `apps/mailroom/dashboard/__init__.py`
  - `apps/mailroom/.env.example`
  - `apps/mailroom/.gitignore`
  - `apps/mailroom/README.md`

### Task 2: Traceability Database
- **Status**: not started
- **Requirements**: REQ-6, REQ-7
- **Description**: Implement SQLite traceability store with WAL mode. Create schema, migration script, and Python interface for logging file actions, querying history, and performing reverts. This is the foundation — nothing moves without a DB record.
- **Files**:
  - `apps/mailroom/db/schema.sql`
  - `apps/mailroom/db/store.py`

### Task 3: File Watcher Service
- **Status**: not started
- **Requirements**: REQ-1
- **Description**: Implement the `receive_channel/` watcher using `watchdog`. FIFO queue with timestamp ordering. Debounce logic (2s after last write). SHA-256 checksum for dedup. Emits `FileEvent` dataclass to processing queue.
- **Files**:
  - `apps/mailroom/engine/watcher.py`
  - `apps/mailroom/engine/models.py` (shared dataclasses: FileEvent, ClassificationResult, DocumentMetadata, DispatchResult)

### Task 4: LLM Interface (Local Inference)
- **Status**: not started
- **Requirements**: REQ-2, REQ-3
- **Description**: Abstract LLM interface supporting local multimodal models. Must handle: text prompts, image inputs (for OCR), structured JSON output, timeouts, and graceful failure. Initially target llama.cpp / Ollama compatible API.
- **Files**:
  - `apps/mailroom/engine/llm.py`

### Task 5: Skill Loader & Registry
- **Status**: not started
- **Requirements**: REQ-5
- **Description**: Parse `.md` skill files into structured `Skill` dataclass. Watch `skills/` directory for hot-reload. Registry provides skill lookup by department key. Create example parking skill.
- **Files**:
  - `apps/mailroom/engine/skill_loader.py`
  - `apps/mailroom/skills/parking.md` (example skill)
  - `apps/mailroom/skills/README.md`

### Task 6: Document Classifier
- **Status**: not started
- **Requirements**: REQ-2
- **Description**: Implement classification engine. Loads tree schema, constructs LLM prompt with document content + relevant skill context, parses response into `ClassificationResult`. Handles confidence thresholds and fallback to `undetermined`.
- **Files**:
  - `apps/mailroom/engine/classifier.py`

### Task 7: Metadata Extractor
- **Status**: not started
- **Requirements**: REQ-3
- **Description**: Post-classification metadata extraction. Uses LLM to pull structured fields based on document type and department skill. Returns `DocumentMetadata` with both standard and custom fields.
- **Files**:
  - `apps/mailroom/engine/extractor.py`

### Task 8: Dispatcher (File Mover)
- **Status**: not started
- **Requirements**: REQ-4, REQ-6
- **Description**: Atomic file dispatch: copy → verify checksum → delete source. Three routes: destination folder, junk, undetermined. Every dispatch writes to traceability DB BEFORE moving. Handles permission preservation and path conflicts.
- **Files**:
  - `apps/mailroom/engine/dispatcher.py`

### Task 9: Pipeline Orchestrator & Entry Point
- **Status**: not started
- **Requirements**: REQ-1, REQ-2, REQ-3, REQ-4
- **Description**: Wire all components together. `main.py` boots: watcher → queue → classifier → extractor → dispatcher pipeline. Async event loop. Graceful shutdown handling. Runtime folder creation (`receive_channel/`, `junk/`, `undetermined/`).
- **Files**:
  - `apps/mailroom/main.py`
  - `apps/mailroom/engine/pipeline.py`

## Phase 2: Notifications & Dashboard

### Task 10: Notification System
- **Status**: not started
- **Requirements**: REQ-10
- **Description**: Pluggable notification backends. Initial: local SMTP + dashboard alert. Templates for: undetermined file, system crash, integration failure. Configurable per department.
- **Files**:
  - `apps/mailroom/engine/notifier.py`
  - `apps/mailroom/config/notifications.yaml`

### Task 11: Admin Dashboard — Core
- **Status**: not started
- **Requirements**: REQ-8, REQ-7
- **Description**: Flask app bound to LAN only. Pages: Overview (queue stats, recent activity), Undetermined queue (with AI failure reasons), History (searchable log). SSE for real-time updates.
- **Files**:
  - `apps/mailroom/dashboard/app.py`
  - `apps/mailroom/dashboard/routes.py`
  - `apps/mailroom/dashboard/templates/layout.html`
  - `apps/mailroom/dashboard/templates/overview.html`
  - `apps/mailroom/dashboard/templates/undetermined.html`
  - `apps/mailroom/dashboard/templates/history.html`
  - `apps/mailroom/dashboard/static/css/style.css`
  - `apps/mailroom/dashboard/static/js/main.js`

### Task 12: Manual Revert System
- **Status**: not started
- **Requirements**: REQ-7
- **Description**: Dashboard UI for file revert. Single and bulk revert. Calls `TraceabilityStore.revert()` which: restores file to original path, updates DB record with revert timestamp and operator. Revert action itself is logged.
- **Files**:
  - `apps/mailroom/dashboard/templates/revert.html`
  - (extends `db/store.py` revert methods from Task 2)

## Phase 3: Integrations & Hardening

### Task 13: CM9 Integration
- **Status**: not started
- **Requirements**: REQ-9
- **Description**: Push classified documents to Content Manager 9 via local REST API. Configurable endpoint, auth, and field mapping. Retry with exponential backoff. Status tracking in dashboard.
- **Files**:
  - `apps/mailroom/integrations/cm9.py`

### Task 14: TechnologyOne Integration
- **Status**: not started
- **Requirements**: REQ-9
- **Description**: Push classified documents to TechnologyOne via local REST API. Same pattern as CM9 — configurable, retry logic, status tracking.
- **Files**:
  - `apps/mailroom/integrations/techone.py`

### Task 15: Department Skill Pack (Initial Set)
- **Status**: not started
- **Requirements**: REQ-5
- **Description**: Write skill files for the initial department set. Each skill defines classification hints, validation rules, expected metadata fields, and output format. Start with 5 core departments.
- **Files**:
  - `apps/mailroom/skills/parking.md` (expand from Task 5)
  - `apps/mailroom/skills/hr.md`
  - `apps/mailroom/skills/waste.md`
  - `apps/mailroom/skills/planning.md`
  - `apps/mailroom/skills/rates.md`

### Task 16: System Hardening & Deployment Config
- **Status**: not started
- **Requirements**: NFR-1, NFR-2, NFR-3, NFR-4
- **Description**: Systemd service files, log rotation, health checks, watchdog auto-restart. Read-only root FS compatibility. Performance tuning for 40GB+ VRAM inference. Security lockdown (no outbound network, local auth).
- **Files**:
  - `apps/mailroom/deploy/mailroom.service`
  - `apps/mailroom/deploy/logrotate.conf`
  - `apps/mailroom/deploy/healthcheck.py`
  - `apps/mailroom/deploy/DEPLOYMENT.md`
