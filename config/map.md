# Mailroom Orchestration Rules

## Pipeline Behaviour

### Watcher
- Monitor: `RECEIVE_CHANNEL` path from environment
- Mode: FIFO (first in, first out) by file creation timestamp
- Debounce: Wait `WATCHER_DEBOUNCE_SECONDS` after last write event before processing
- Supported formats: PDF, DOCX, DOC, JPG, JPEG, PNG, TIFF, TIF, EML, MSG

### Classification
- Load department tree from `config/tree.yaml`
- If a matching skill exists in `skills/`, inject its content as additional LLM context
- Confidence threshold: `CONFIDENCE_THRESHOLD` from environment (default 0.7)
- Below threshold → route to `undetermined` with reasoning

### Dispatch Rules
1. `classified` + confidence >= threshold → move to department destination folder
2. Detected as junk/spam → move to `junk/` temp folder
3. `undetermined` (low confidence, mixed info, unreadable) → move to `undetermined/` + notify

### File Operations
- All moves are atomic: copy → verify SHA-256 → delete source
- Never move without writing traceability record first
- Preserve original file permissions and timestamps
- Filename conflicts: append `_YYYYMMDD_HHMMSS` before extension

### Retry Logic
- LLM inference timeout: retry once, then route to `undetermined`
- File copy failure: retry 3x with 2s backoff, then alert + leave in receive channel
- Integration push failure: retry 5x with exponential backoff (2s, 4s, 8s, 16s, 32s)

### Notifications
- Undetermined file: send to configured notification channel with filename + failure reason
- System crash during inference: immediate critical alert
- Integration failure after max retries: alert with details

### Maintenance
- Junk folder: auto-purge files older than 30 days
- Traceability DB: retain for 7 years (configurable)
- Logs: rotate daily, retain 90 days
