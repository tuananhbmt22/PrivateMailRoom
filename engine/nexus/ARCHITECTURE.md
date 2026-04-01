# Nexus — Autonomous Folder Learning Engine

## Purpose
Nexus takes sample events from a council folder and auto-generates two JSON artifacts:
1. A skill scroll (blueprint) for the skill runner
2. A classification tree entry for the classifier

Both outputs are AI-generated, unverified, and human-modifiable. The JSON schema is strict and must be 100% compatible with the existing Mailroom pipeline — no translation layer.

## Pipeline

```
Sample Events (from council)
    │
    ▼
┌─────────────────────────────┐
│  Job 1: Skill Blueprint     │  (per event, separate LLM call)
│  Input: raw event files     │
│  Output: event_type entry   │
│  with documents, fields,    │
│  cross-doc rules            │
└─────────────────────────────┘
    │ (collect all Job 1 outputs)
    ▼
┌─────────────────────────────┐
│  Job 2: Classification      │  (one call, consumes all Job 1 outputs)
│  Input: all event_type      │
│  summaries from Job 1       │
│  Output: classification     │
│  tree entry (triggers,      │
│  exclusions, description)   │
└─────────────────────────────┘
    │
    ▼
Two JSON files ready for Mailroom
```

## Job 1: Skill Blueprint Generation

**Runs once per sample event.**

Input:
- Event directory (email_body.txt + attachments)
- Folder key and name

LLM task:
- Identify the event type (e.g., "parking_appeal")
- List all documents present (email body, each attachment)
- For each document: extract field schema (name, type, required/optional)
- Identify cross-document validation rules
- Determine completeness requirements (which docs are required vs optional)

Output per event:
```json
{
  "event_type_id": "parking_appeal",
  "description": "Disputing a parking fine or infringement notice",
  "triggers": ["appeal", "dispute", "contest"],
  "documents": {
    "appeal_letter": {
      "required": true,
      "source": "email_body",
      "fields": {
        "fine_number": { "type": "string", "required": true },
        "appellant_name": { "type": "string", "required": true }
      }
    },
    "evidence_photo": {
      "required": false,
      "source": "attachment",
      "file_hints": [".jpg", ".png"],
      "fields": { ... }
    }
  },
  "cross_document_rules": [ ... ],
  "completeness": {
    "minimum": ["appeal_letter"],
    "ideal": ["appeal_letter", "infringement_notice"]
  }
}
```

## Job 2: Classification Entry Generation

**Runs once per folder, after all Job 1s complete.**

Input:
- All Job 1 outputs for this folder
- Folder key and name

LLM task:
- Synthesize triggers from all event types (what words/concepts route here)
- Identify exclusions (what looks like this folder but isn't)
- Write a concise description

Output:
```json
{
  "name": "Regulation of Parking",
  "description": "Parking fines, illegal/improper parking, infringement notices...",
  "triggers": ["parking", "fine", "infringement", "clearway", ...],
  "exclusions": [
    "Payment confirmations only → Finance folders"
  ]
}
```

This drops directly into `classification_only_tree.json` under the folder key.

## Final Outputs

### Output 1: `skills/{folder_key}_scroll.json`

Full scroll format compatible with the existing skill runner:
```json
{
  "_generated_by": "nexus",
  "_generated_at": "ISO timestamp",
  "_verified": false,
  "skill_id": "parking",
  "department_key": "regulation_of_parking",
  "matching": { "keywords": [...], "concepts": [...] },
  "event_types": { ... },
  "metadata_fields": { ... },
  "response_templates": {}
}
```

- `_verified: false` — flags this as AI-generated, needs human review
- `response_templates` is empty — council fills these in
- `event_types` contains the full blueprint from Job 1
- `matching` is derived from Job 1 triggers

### Output 2: Classification tree entry

Merges into `classification_only_tree.json`:
```json
{
  "regulation_of_parking": {
    "name": "Regulation of Parking",
    "description": "...",
    "triggers": [...],
    "exclusions": [...]
  }
}
```

## Key Principles

1. **Schema is sacred.** The JSON format never changes between AI-generated and human-edited versions. Same keys, same structure, same consumption path.

2. **Classification stays simple.** Triggers + exclusions + description. No document-level detail. Classification only needs to know "does this email belong in Parking?" — not "what kind of parking event is it?"

3. **Skills go deep.** Event types, documents, fields, cross-doc rules, completeness. This is where the structural knowledge lives.

4. **Job 2 consumes Job 1.** Classification triggers are synthesized from the event type triggers. Job 2 sees the full picture and can write better exclusions because it knows what's inside the folder.

5. **Both outputs are incomplete by design.** AI generates the structure, humans verify and refine. The system works with unverified scrolls (lower accuracy) and improves as humans tune them.

## LLM Strategy

- Job 1: Can use local LLM (27B) or Claude API for higher quality
- Job 2: Recommend Claude API — needs to synthesize across multiple event types, benefits from stronger reasoning
- Both jobs use `use_json_schema=False` — freeform JSON output with parser fallback

## Onboarding Flow

1. Council provides sample events per folder (1 event per type, covering all types)
2. Admin places samples in `onboarding/{folder_key}/` directory
3. Admin triggers Nexus via dashboard or CLI
4. Nexus runs Job 1 on each event, then Job 2 on the folder
5. Outputs saved to `skills/` and classification tree updated
6. Admin reviews and tweaks in the dashboard
7. System is ready for live classification with skill support
