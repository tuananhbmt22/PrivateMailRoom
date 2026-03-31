# Kajima Mailroom Appliance — Design

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    IMMUTABLE LINUX APPLIANCE                     │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │   Receive     │───▶│   Engine     │───▶│   Destination    │  │
│  │   Channel     │    │  Pipeline    │    │   Folders (20+)  │  │
│  └──────────────┘    │              │    └──────────────────┘  │
│                      │  ┌────────┐  │    ┌──────────────────┐  │
│                      │  │Watcher │  │───▶│   Junk (temp)    │  │
│                      │  └───┬────┘  │    └──────────────────┘  │
│                      │      ▼       │    ┌──────────────────┐  │
│                      │  ┌────────┐  │───▶│  Undetermined    │  │
│                      │  │Classify│  │    └──────────────────┘  │
│                      │  └───┬────┘  │                          │
│                      │      ▼       │    ┌──────────────────┐  │
│                      │  ┌────────┐  │    │  Traceability    │  │
│                      │  │Extract │  │───▶│  DB (SQLite)     │  │
│                      │  └───┬────┘  │    └──────────────────┘  │
│                      │      ▼       │                          │
│                      │  ┌────────┐  │    ┌──────────────────┐  │
│                      │  │Dispatch│  │    │  Skills (.md)    │  │
│                      │  └────────┘  │◀───│  per department  │  │
│                      └──────────────┘    └──────────────────┘  │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │   Dashboard   │    │  Notifier    │    │  Integrations   │  │
│  │  (Flask/LAN)  │    │ (local SMTP) │    │  (CM9/TechOne)  │  │
│  └──────────────┘    └──────────────┘    └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Component Design

### 1. Watcher Service (`engine/watcher.py`)
- Uses `watchdog` library to monitor `receive_channel/`
- FIFO queue ordered by file creation timestamp
- Debounce: waits 2 seconds after last write event before processing (handles large file copies)
- Emits `FileReady` events to the pipeline

```python
# Core interface
class FileWatcher:
    def __init__(self, channel_path: Path, queue: Queue[FileEvent]) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...

@dataclass
class FileEvent:
    path: Path
    timestamp: datetime
    size_bytes: int
    checksum: str  # SHA-256 for dedup
```

### 2. Classifier (`engine/classifier.py`)
- Loads the Tree Schema from `config/tree.yaml`
- Sends document to local LLM with structured prompt
- Returns classification result with confidence score
- Applies skill-specific rules if a matching skill is loaded

```python
@dataclass
class ClassificationResult:
    department: str | None          # Target department key
    document_type: str | None       # Sub-type within department
    confidence: float               # 0.0 - 1.0
    reasoning: str                  # AI's explanation
    status: Literal["classified", "junk", "undetermined"]

class Classifier:
    def __init__(self, tree: DepartmentTree, llm: LocalLLM, skills: SkillRegistry) -> None: ...
    async def classify(self, file_event: FileEvent) -> ClassificationResult: ...
```

### 3. Metadata Extractor (`engine/extractor.py`)
- Runs after classification
- Uses LLM to extract structured fields based on document type
- Fields vary by department (defined in skill files)

```python
@dataclass
class DocumentMetadata:
    filename: str
    document_date: date | None
    reference_id: str | None
    person_name: str | None
    custom_fields: dict[str, Any]   # Department-specific (e.g., plate_number)

class Extractor:
    def __init__(self, llm: LocalLLM) -> None: ...
    async def extract(self, file_event: FileEvent, classification: ClassificationResult) -> DocumentMetadata: ...
```

### 4. Dispatcher (`engine/dispatcher.py`)
- Atomic file operations: copy to destination → verify checksum → delete source
- Logs every action to traceability DB before moving
- Handles three outcomes: destination folder, junk, undetermined

```python
class Dispatcher:
    def __init__(self, tree: DepartmentTree, db: TraceabilityStore, notifier: Notifier) -> None: ...
    async def dispatch(self, file_event: FileEvent, classification: ClassificationResult, metadata: DocumentMetadata) -> DispatchResult: ...
```

### 5. Skill Loader (`engine/skill_loader.py`)
- Parses `.md` skill files from `skills/` directory
- Extracts structured sections: Classification Rules, Validation Logic, Output Format
- Hot-reload: watches `skills/` for changes, reloads without restart

```python
@dataclass
class Skill:
    department: str
    classification_hints: list[str]
    validation_rules: list[str]
    output_format: str
    raw_content: str                # Full .md for LLM context injection

class SkillRegistry:
    def __init__(self, skills_dir: Path) -> None: ...
    def load_all(self) -> None: ...
    def get(self, department: str) -> Skill | None: ...
    def reload(self) -> None: ...
```

### 6. Notifier (`engine/notifier.py`)
- Pluggable backends: local SMTP, dashboard WebSocket, file-based alerts
- Templates for different notification types (undetermined, crash, integration failure)

### 7. Traceability Store (`db/store.py`)
- SQLite with WAL mode for concurrent safety
- Core table: `file_actions`

```sql
CREATE TABLE file_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    destination_path TEXT,
    department TEXT,
    document_type TEXT,
    confidence REAL,
    ai_reasoning TEXT,
    metadata_json TEXT,
    status TEXT NOT NULL,           -- classified | junk | undetermined | reverted
    failure_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMP,
    reverted_by TEXT
);

CREATE INDEX idx_file_actions_status ON file_actions(status);
CREATE INDEX idx_file_actions_department ON file_actions(department);
CREATE INDEX idx_file_actions_created ON file_actions(created_at);
```

### 8. Admin Dashboard (`dashboard/app.py`)
- Flask app, LAN-only binding (0.0.0.0 on internal network)
- Pages: Overview, Queue, Undetermined, History, Settings
- Manual revert: calls `TraceabilityStore.revert()` which restores file + logs action
- Real-time updates via Server-Sent Events (SSE) — no WebSocket complexity needed

### 9. LLM Interface (`engine/llm.py`)
- Abstract interface for local multimodal LLM
- Supports: image input (OCR), text input, structured output (JSON mode)
- Configurable model path and parameters
- Timeout handling for inference failures

```python
class LocalLLM:
    def __init__(self, model_path: str, gpu_layers: int, context_size: int) -> None: ...
    async def infer(self, prompt: str, images: list[Path] | None = None) -> LLMResponse: ...
```

## Configuration

### `config/tree.yaml` — Department Tree Schema
```yaml
departments:
  parking:
    name: "Parking & Traffic"
    path: "/data/departments/parking"
    subtypes:
      - fine_tickets
      - image_evidence
      - appeal_papers
      - permits
  hr:
    name: "Human Resources"
    path: "/data/departments/hr"
    subtypes:
      - leave_applications
      - recruitment
      - complaints
  waste:
    name: "Waste Management"
    path: "/data/departments/waste"
    subtypes:
      - collection_requests
      - complaints
      - contractor_reports
  # ... 17 more departments
```

### `config/map.md` — Orchestration Rules
Master configuration defining pipeline behavior, thresholds, retry logic, and notification preferences. Parsed at startup.

## Data Flow (Per Document)

1. File lands in `receive_channel/`
2. Watcher detects → creates `FileEvent` → pushes to queue
3. Classifier pulls from queue → sends to LLM with tree schema + relevant skill
4. Classification result determines route:
   - `classified` → Extractor runs → Dispatcher moves to department folder
   - `junk` → Dispatcher moves to `junk/`
   - `undetermined` → Dispatcher moves to `undetermined/` + Notifier fires
5. Traceability DB records the full action (step 4 outcome)
6. If integration enabled → push to CM9/TechnologyOne
7. Dashboard reflects updated state via SSE

## Error Handling Strategy

| Failure Point | Behavior |
|---|---|
| LLM inference timeout | Retry once → if fail, route to `undetermined` with reason "Inference timeout" |
| LLM crash | Route to `undetermined` + critical notification to support channels |
| File copy failure | Retry 3x with backoff → if fail, leave in `receive_channel` + alert |
| DB write failure | Block dispatch (never move without logging) → alert |
| Skill parse error | Log warning, skip skill, classify without it |
| Integration push failure | Retry with exponential backoff (max 5 attempts) → log failure in DB |
