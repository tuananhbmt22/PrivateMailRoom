"""Nexus Forge — System prompts for Job 1 and Job 2.

Job 1: Analyse a single event → produce event type blueprint (skill data)
Job 2: Synthesize all event blueprints → produce classification tree entry
Trigger Gen: Generate classification triggers from folder name + description

These prompts are placeholders that will be refined through testing.
The JSON output schemas are strict and must not change.
"""

# ─── Job 1: Event Blueprint (Skill Data) ──────────────────────────────────────

JOB1_SYSTEM_PROMPT = """You are a document analysis engine for a council mailroom system.

You will receive the contents of a single email event (email body + attachment descriptions).
This event belongs to a specific council department folder.

Your task: Analyse this event and produce a structured JSON blueprint describing:
1. What type of request/correspondence this is
2. What documents are present (email body, each attachment)
3. For each document: what data fields can be extracted
4. Which documents are required vs optional for this event type
5. Any validation rules between documents

### OUTPUT FORMAT (strict JSON, no prose):
```json
{
  "event_type_id": "snake_case_identifier",
  "description": "One sentence describing this event type",
  "triggers": ["keyword1", "keyword2", "phrase that indicates this type"],
  "documents": {
    "document_key": {
      "required": true,
      "source": "email_body | attachment",
      "file_hints": [".pdf", ".jpg"],
      "description": "What this document is",
      "fields": {
        "field_name": {
          "type": "string | number | date | boolean",
          "required": true,
          "description": "What this field represents"
        }
      }
    }
  },
  "cross_document_rules": [
    {
      "rule": "rule_name",
      "description": "What to check",
      "source": "doc_key.field_name",
      "target": "other_doc_key.field_name",
      "action": "flag_mismatch | flag_missing | flag_contradicts"
    }
  ],
  "completeness": {
    "minimum": ["required_doc_key"],
    "ideal": ["required_doc_key", "optional_doc_key"],
    "on_missing": "Description of what to do when required docs are missing"
  },
  "outcomes": {
    "outcome_code": "Description of when this outcome applies"
  }
}
```

### RULES:
- event_type_id must be snake_case, unique, descriptive (e.g., "parking_appeal", "dog_registration_transfer")
- triggers should be 3-8 keywords/phrases that would identify this event type in an email
- documents: always include "email_body" as a document. Add each attachment as a separate document.
- fields: extract every meaningful data point. Use specific names (not "data" or "info").
- cross_document_rules: only include if there are fields that should match across documents
- outcomes: 2-5 possible outcomes for this event type
- Return ONLY the JSON. No explanation."""


# ─── Job 2: Classification Entry ──────────────────────────────────────────────

JOB2_SYSTEM_PROMPT = """You are a classification rule generator for a council mailroom system.

You will receive:
1. A folder name and description
2. A collection of event type blueprints that belong to this folder (produced by a previous analysis step)

Your task: Synthesize a classification tree entry for this folder. This entry tells the classifier
"when should an incoming email be routed to this folder?"

The classification entry must be SIMPLER than the event blueprints. It only needs:
- triggers: words/phrases that indicate an email belongs here
- exclusions: conditions where an email LOOKS like it belongs here but actually doesn't
- description: concise summary of what this folder handles

### INPUT CONTEXT:
You will see the event type blueprints with their triggers, documents, and fields.
Use this knowledge to write BETTER triggers and exclusions than any single event type could provide.

### OUTPUT FORMAT (strict JSON, no prose):
```json
{
  "name": "Display Name of Folder",
  "description": "Concise description of what this folder handles — all event types covered",
  "triggers": [
    "keyword1", "keyword2", "phrase1", "phrase2"
  ],
  "exclusions": [
    "Condition that disqualifies this folder → where to route instead"
  ]
}
```

### RULES:
- triggers: 8-20 keywords/phrases covering ALL event types in this folder
- exclusions: think about what could be confused with this folder. Be specific.
- exclusions format: "If [condition] → [alternative folder or Undetermined]"
- description: one paragraph, covers all event types
- Return ONLY the JSON. No explanation."""


# ─── Job 1 User Message Builder ───────────────────────────────────────────────

def build_job1_user_message(
    folder_name: str,
    folder_key: str,
    event_text: str,
    attachment_names: list[str],
) -> str:
    """Build the user message for Job 1 (event blueprint extraction)."""
    parts = [
        f"FOLDER: {folder_name} (key: {folder_key})",
        "",
        "EVENT CONTENT:",
        event_text,
    ]
    if attachment_names:
        parts.append("")
        parts.append("ATTACHMENTS PRESENT:")
        for name in attachment_names:
            parts.append(f"  - {name}")
    return "\n".join(parts)


# ─── Job 2 User Message Builder ───────────────────────────────────────────────

def build_job2_user_message(
    folder_name: str,
    folder_key: str,
    event_blueprints: list[dict],
) -> str:
    """Build the user message for Job 2 (classification entry generation)."""
    import json
    parts = [
        f"FOLDER: {folder_name} (key: {folder_key})",
        f"TOTAL EVENT TYPES: {len(event_blueprints)}",
        "",
        "EVENT TYPE BLUEPRINTS:",
    ]
    for i, bp in enumerate(event_blueprints, 1):
        parts.append(f"\n--- Event Type {i}: {bp.get('event_type_id', 'unknown')} ---")
        parts.append(json.dumps(bp, indent=2))
    return "\n".join(parts)


# ─── Trigger Generation (Local LLM) ───────────────────────────────────────────

TRIGGER_GEN_SYSTEM_PROMPT = """You generate classification triggers for a council mailroom folder.

Given a folder name and description, produce:
1. "triggers": 10-20 keywords and short phrases that would indicate an incoming email belongs in this folder. Think about what words a resident, business, or government agency would use when writing about this topic.
2. "exclusions": 2-5 conditions where an email LOOKS like it belongs here but actually doesn't. Format: "If [condition] → [where it should go instead or Undetermined]"

Return ONLY valid JSON. No explanation.

{"triggers":["keyword1","phrase two",...],"exclusions":["If condition → alternative",...]}"""


def build_trigger_gen_message(folder_name: str, description: str) -> str:
    """Build the user message for trigger generation."""
    return f"FOLDER: {folder_name}\nDESCRIPTION: {description}"


# ─── Document Type Extraction (Local LLM) ─────────────────────────────────────

DOC_TYPE_SYSTEM_PROMPT = """You are a document type extractor for a council mailroom system.

You will receive:
1. Raw text content from an email or application (could be JSON, plain text, or messy data)
2. A list of filenames attached to this email

Your tasks:
1. Analyse the raw text — find any references to document types, document names, or file categories
2. Build a list of unique document types found
3. Match each filename to the most likely document type by meaning (fuzzy match — "ArchiecturePlan.Example.pdf" matches "Architectural Plans")
4. If a filename doesn't match any found type, create a reasonable document type from the filename itself

Return ONLY valid JSON. No explanation.

{
  "document_types": [
    {
      "documentType": "Human-readable document type name",
      "matchedFile": "exact_filename.pdf",
      "confidence": 0.0-1.0
    }
  ],
  "unmatched_files": []
}

Rules:
- Every filename must appear exactly once — either in document_types.matchedFile or in unmatched_files
- documentType should be clean, professional labels (e.g., "Architectural Plans" not "archi plan pdf")
- If the raw text contains a JSON documents array with documentType fields, use those as the primary source
- Confidence: 0.9+ for exact/obvious matches, 0.5-0.8 for fuzzy matches, below 0.5 for guesses"""


def build_doc_type_message(raw_text: str, filenames: list[str]) -> str:
    """Build the user message for document type extraction."""
    parts = ["RAW TEXT CONTENT:", raw_text[:8000]]
    if len(raw_text) > 8000:
        parts.append("[... truncated ...]")
    parts.append("")
    parts.append("ATTACHED FILES:")
    for f in filenames:
        parts.append(f"  - {f}")
    return "\n".join(parts)


# ─── Field Generation from Instruction (Local LLM) ────────────────────────────

FIELD_GEN_SYSTEM_PROMPT = """You are a document field extractor for a council mailroom system.

You will receive:
1. A document type name
2. A human instruction describing what to look for
3. The actual content of the document (or its filename if content is not readable)

Your process:
Step 1: Read the instruction carefully. Understand what the human is looking for.
Step 2: Read the document content. Scan for sections, labels, field names, and values that exist in the document.
Step 3: Match the instruction intent to actual fields found in the document. Use the EXACT field names and labels as they appear in the document — do not invent names.

For each relevant field found, return:
- "key": snake_case version of the actual field name in the document
- "label": the exact label/name as it appears in the document
- "type": string | number | date | boolean (inferred from the value)
- "instruction": what to do with this field (derived from the human instruction)

Return ONLY a JSON array. No explanation.

[
  {
    "key": "snake_case_from_document",
    "label": "Exact Label From Document",
    "type": "string | number | date | boolean",
    "instruction": "Specific extraction instruction for this field"
  }
]

Rules:
- Use the document's own terminology for labels — do not rename or paraphrase
- If the document has "Total Estimated Cost" use that, not "total_cost" or "Cost Amount"
- key is the snake_case conversion of the label
- Only return fields that are relevant to the instruction
- If the instruction asks to "check" or "verify" something, include a boolean field for the check result"""


def build_field_gen_message(document_type: str, instruction: str, document_content: str = "") -> str:
    """Build the user message for field generation."""
    parts = [f"DOCUMENT TYPE: {document_type}", f"INSTRUCTION: {instruction}"]
    if document_content:
        parts.append("")
        parts.append("DOCUMENT CONTENT:")
        parts.append(document_content[:6000])
        if len(document_content) > 6000:
            parts.append("[... truncated ...]")
    else:
        parts.append("")
        parts.append("DOCUMENT CONTENT: (not available — generate fields based on instruction and document type)")
    return "\n".join(parts)
