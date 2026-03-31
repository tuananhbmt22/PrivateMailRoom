# Scroll Development Guide

Standard procedure for creating new skills (scrolls) for the Kajima Mailroom system.

## What is a Scroll?

A scroll is a JSON file that contains the business logic for handling a specific type of council request. When an email arrives and matches a skill, the scroll tells the AI exactly how to analyse it, what to check, what metadata to extract, and what response templates to use.

Scrolls are the "IP" of the system — each one encodes a council procedure that would normally require a trained staff member to process.

## Architecture Overview

```
skills/
  skills.md                    ← Master list of all available skills
  parking_scroll.json          ← Parking & Traffic skill
  waste_scroll.json            ← Waste Management skill
  companion_animals_scroll.json ← Companion Animals skill
  ...
```

The pipeline:
1. Event arrives → compared against `skills.md` (Call 1: skill matching)
2. Best match found → load `{skill}_scroll.json` (Call 2: scroll execution)
3. Scroll output enriches the event → feeds into classification (Call 3)
4. Draft reply uses scroll output + response templates

## Step-by-Step: Creating a New Scroll

### Step 1: Identify the Council Procedure

Before writing any JSON, answer these questions:
- What department does this cover?
- What types of requests come into this department?
- For each request type, what does a staff member check?
- What are the possible outcomes?
- What does the reply look like for each outcome?

### Step 2: Add to skills.md

Add a new entry to `skills/skills.md`:

```markdown
- parking: Parking fines, infringements, appeals, permits, clearway violations, ranger enforcement
- NEW_SKILL: Brief semantic description of what this skill handles
```

The description should contain the key concepts that help the skill matcher identify relevant emails.

### Step 3: Create the Scroll JSON

Create `skills/{skill_name}_scroll.json` following this schema:

```json
{
  "skill_id": "parking",
  "skill_name": "Parking & Traffic",
  "version": "1.0",
  "department_key": "regulation_of_parking",
  "description": "Handles parking fines, appeals, permits, and enforcement",

  "matching": {
    "keywords": ["parking", "fine", "infringement", "clearway", "permit", "parked"],
    "concepts": ["vehicle violation", "parking appeal", "parking permit"]
  },

  "request_types": {
    "appeal": {
      "description": "Sender is disputing a parking fine",
      "checks": [
        "Is a fine/infringement number referenced?",
        "Is evidence provided (photos, witness statements)?",
        "What grounds are cited? (signage obscured, medical emergency, permit displayed)"
      ],
      "outcomes": {
        "valid_appeal": "Evidence supports the appeal, grounds are legitimate",
        "needs_evidence": "Appeal filed but missing required evidence",
        "needs_review": "Complex case requiring human review",
        "rejected": "No valid grounds for appeal"
      }
    },
    "payment": {
      "description": "Sender wants to pay a fine or asks about payment options",
      "checks": ["Is a fine number referenced?"],
      "outcomes": {
        "payment_info": "Provide payment instructions"
      }
    },
    "report": {
      "description": "Sender is reporting illegal parking by someone else",
      "checks": ["Is a location provided?", "Is the issue ongoing or one-time?"],
      "outcomes": {
        "ranger_dispatch": "Location provided, ranger can be sent",
        "noted": "Report logged but insufficient detail for dispatch"
      }
    }
  },

  "metadata_fields": {
    "plate_number": "Vehicle registration plate",
    "fine_number": "Infringement notice number",
    "violation_type": "Type of violation (clearway, no_stopping, expired_meter, no_permit)",
    "violation_date": "Date of the alleged violation",
    "location": "Street address or intersection",
    "request_type": "Determined request type"
  },

  "response_templates": {
    "valid_appeal": "Thank you for your correspondence regarding infringement notice {fine_number}. We have reviewed the evidence provided regarding the {violation_type} at {location}. Your appeal has been forwarded to our Parking Review Panel. You will receive a determination within 21 business days.",
    "needs_evidence": "Thank you for your correspondence regarding infringement notice {fine_number}. To process your appeal, we require: {missing_items}. Please provide within 14 business days.",
    "rejected": "After reviewing the details provided for infringement notice {fine_number}, we are unable to identify grounds for waiving this infringement. Payment of the fine is due within 28 days.",
    "payment_info": "Payment for infringement notice {fine_number} can be made online, by phone, or in person. If experiencing financial hardship, payment plans are available.",
    "ranger_dispatch": "Thank you for reporting the parking issue at {location}. A Council Ranger will attend to investigate.",
    "noted": "Thank you for your report. It has been logged for monitoring."
  }
}
```

### Step 4: Validate the Scroll

Checklist before deploying:
- [ ] `skill_id` matches the entry in `skills.md`
- [ ] `department_key` matches a folder key in the classification tree
- [ ] Every request type has at least one outcome
- [ ] Every outcome has a response template
- [ ] Metadata fields cover what the response templates need (no missing placeholders)
- [ ] Checks are specific enough for the LLM to follow
- [ ] Response templates are professional, factual, and council-appropriate

### Step 5: Test

1. Find or create a test email that should match this skill
2. Run with skills enabled
3. Verify: Call 1 matches the correct skill
4. Verify: Call 2 produces the correct request type and outcome
5. Verify: Call 3 classifies to the correct folder
6. Verify: Draft reply uses the correct template with filled metadata

## Scroll Schema Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| skill_id | string | yes | Unique identifier, matches skills.md entry |
| skill_name | string | yes | Human-readable name |
| version | string | yes | Scroll version for tracking changes |
| department_key | string | yes | Maps to classification tree folder key |
| description | string | yes | What this skill handles |
| matching.keywords | array | yes | Keywords for skill matching |
| matching.concepts | array | yes | Semantic concepts for matching |
| request_types | object | yes | Map of request type → checks + outcomes |
| metadata_fields | object | yes | Fields to extract from the document |
| response_templates | object | yes | Map of outcome → reply template text |

## Naming Convention

- Scroll files: `{skill_id}_scroll.json`
- Skill IDs: lowercase, underscores (e.g., `companion_animals`, `waste_management`)
- Request types: lowercase, underscores (e.g., `appeal`, `payment`, `report`)
- Outcomes: lowercase, underscores (e.g., `valid_appeal`, `needs_evidence`)

## Tips for Writing Good Scrolls

1. Start from the staff perspective — what does a human check when they see this email?
2. Keep checks concrete and verifiable — "Is a fine number present?" not "Does this seem valid?"
3. Response templates should be factual — no promises, no opinions, just process
4. Include timeframes that match actual council SLAs
5. Test with real emails from the department if possible
6. Version your scrolls — when procedures change, bump the version
