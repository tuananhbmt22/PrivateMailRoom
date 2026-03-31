# Kajima Mailroom Appliance — Requirements

## Overview
A high-security, air-gapped Linux appliance that automates document ingestion, classification, and departmental reasoning ("Skills") for local councils. 100% of data stays on-site. Zero internet connectivity.

## Stakeholders
- **Council IT Staff**: Deploy and maintain the appliance
- **Council Admin Staff**: Review undetermined documents, trigger manual reverts
- **Department Heads**: Consume classified documents in their target folders
- **Kajima (Owner)**: Builds, licenses, and supports the system

## Functional Requirements

### REQ-1: Centralized Document Intake
- **Description**: Single `receive_channel/` folder acts as the only entry point for all documents (scans, emails, uploads)
- **Acceptance Criteria**:
  - [ ] Watcher service monitors `receive_channel/` using FIFO logic with timestamps
  - [ ] Supports PDF, DOCX, images (JPG, PNG, TIFF), and EML files
  - [ ] New files are picked up within 5 seconds of arrival
  - [ ] Duplicate filenames are handled gracefully (append timestamp suffix)

### REQ-2: Document Classification via Local LLM
- **Description**: AI inference engine identifies document type and matches against the Department Tree Schema
- **Acceptance Criteria**:
  - [ ] Multimodal LLM performs OCR on scanned images
  - [ ] Classifies documents against 20+ departmental categories defined in `tree.yaml`
  - [ ] Classification confidence score is logged per document
  - [ ] Documents below confidence threshold route to `undetermined/`

### REQ-3: Metadata Extraction
- **Description**: Extract structured metadata from each document during classification
- **Acceptance Criteria**:
  - [ ] Extracts: Name, ID/Reference Number, Date, and domain-specific fields (e.g., Plate Number for Parking)
  - [ ] Metadata stored in traceability DB alongside file record
  - [ ] Extraction failures are logged with specific reason

### REQ-4: File Dispatch (The Dispatcher)
- **Description**: Move classified files to their correct destination based on Tree Schema
- **Acceptance Criteria**:
  - [ ] Correctly classified files → destination department folder
  - [ ] Junk/spam emails → `junk/` temp folder
  - [ ] Unclassifiable or mixed-info files → `undetermined/` with failure reason
  - [ ] Every move is atomic (copy-then-delete, not move) to prevent data loss
  - [ ] File permissions preserved during dispatch

### REQ-5: Skill System (Modular Business Logic)
- **Description**: Each department has a `.md` Skill file containing the AI's "playbook" for reasoning about that department's documents
- **Acceptance Criteria**:
  - [ ] Skills are loaded dynamically from `skills/` directory
  - [ ] Adding a new `.md` skill file activates that department without restart
  - [ ] Each skill defines: classification rules, validation logic, output format
  - [ ] Base Box ships without skills; skills are licensed add-ons
  - [ ] Skill hot-reload: updated `.md` files take effect on next document

### REQ-6: Traceability Database
- **Description**: Every action logged in local SQLite for full audit trail
- **Acceptance Criteria**:
  - [ ] Logs: original_path, destination_path, ai_reasoning, confidence_score, metadata_extracted, timestamp, status
  - [ ] Supports query by date range, department, status, filename
  - [ ] Retention policy configurable (default: 7 years for government compliance)
  - [ ] DB file stored locally, never transmitted

### REQ-7: Manual Revert
- **Description**: Admin staff can undo any AI-driven file move
- **Acceptance Criteria**:
  - [ ] Dashboard provides "Undo" button per file action
  - [ ] Revert restores file to original location using DB metadata
  - [ ] Revert action itself is logged in traceability DB
  - [ ] Bulk revert supported (select multiple files)

### REQ-8: Admin Dashboard
- **Description**: Local web dashboard for monitoring and manual intervention
- **Acceptance Criteria**:
  - [ ] Shows real-time processing queue status
  - [ ] Lists recent classifications with confidence scores
  - [ ] Undetermined queue with AI failure reasons
  - [ ] Manual revert controls
  - [ ] Department folder statistics
  - [ ] Accessible only on local LAN (no external access)

### REQ-9: Integration Layer (CM9 / TechnologyOne)
- **Description**: Push final classified documents to council records management systems via local APIs
- **Acceptance Criteria**:
  - [ ] CM9 integration via local REST API
  - [ ] TechnologyOne integration via local REST API
  - [ ] Integration is optional and configurable per deployment
  - [ ] Failed pushes retry with exponential backoff
  - [ ] Integration status visible in dashboard

### REQ-10: Notification System
- **Description**: Alert staff when documents land in `undetermined/` or system errors occur
- **Acceptance Criteria**:
  - [ ] Notifications sent via configurable channel (local SMTP, dashboard alert, or network share)
  - [ ] Notification includes: filename, failure reason, suggested action
  - [ ] Critical errors (system crash during inference) trigger immediate alert
  - [ ] Notification preferences configurable per department

## Non-Functional Requirements

### NFR-1: Security (Air-Gapped)
- Zero internet connectivity — all inference runs locally
- Read-only root filesystem (immutable Linux image)
- No data leaves the appliance
- All local API calls authenticated
- Dashboard requires local LAN authentication

### NFR-2: Performance
- Process a single document in < 30 seconds (including OCR + classification + dispatch)
- Handle burst of 100 documents without queue stall
- Watcher latency < 5 seconds for new file detection

### NFR-3: Reliability
- Atomic file operations (no partial moves)
- Graceful recovery from LLM inference failures
- Watchdog auto-restart on service crash
- SQLite WAL mode for concurrent read/write safety

### NFR-4: Hardware Requirements
- 40GB+ VRAM (for local multimodal LLM)
- SSD storage for receive channel and DB
- Minimum 32GB RAM
- Dedicated GPU (NVIDIA recommended for CUDA inference)

### NFR-5: Compliance
- 7-year document retention (configurable)
- Full audit trail for every file operation
- No PII leaves the appliance boundary
