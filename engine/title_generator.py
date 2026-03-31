"""Event title generator with PII redaction.

After classification, generates a human-friendly 1-sentence title
for each event, plus a redacted version with PII replaced by placeholders.
Uses the local LLM with a minimal prompt and low max_tokens for speed.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .llm import LocalLLM

logger = logging.getLogger(__name__)

TITLE_SYSTEM_PROMPT = """You generate short event titles for a council mailroom system.

Rules:
- Output valid JSON only: {"title": "...", "title_redacted": "..."}
- "title": A single sentence, max 15 words, describing what this event is about. Be specific.
- "title_redacted": Same sentence but replace:
  - Personal names → [Name]
  - Street addresses / property descriptions → [Address]
  - Phone numbers → [Phone]
  - Email addresses → [Email]
  - Registration / permit / account / reference numbers → [Ref]
  - Pet names → [Pet]
  - ABN / ACN → [ABN]
- Do NOT redact department names, council names, document types, or generic terms.
- If no PII is present, title and title_redacted should be identical."""


def build_title_user_message(
    subject: str,
    outcome: str,
    skill_outcome: str | None = None,
    reasoning: str | None = None,
) -> str:
    """Build the user message for title generation."""
    parts = [f"Subject: {subject}", f"Classification: {outcome}"]
    if skill_outcome:
        parts.append(f"Skill result: {skill_outcome}")
    if reasoning:
        parts.append(f"Reasoning: {reasoning}")
    return "\n".join(parts)


def parse_title_response(raw: str) -> dict[str, str]:
    """Parse the LLM title response, handling malformed JSON gracefully."""
    cleaned = raw.strip()

    # Strip markdown fences
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        title = parsed.get("title", "")
        title_redacted = parsed.get("title_redacted", title)
        return {"title": title, "title_redacted": title_redacted}
    except json.JSONDecodeError:
        # Try to extract from malformed response
        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', cleaned)
        if title_match:
            title = title_match.group(1)
            redacted_match = re.search(r'"title_redacted"\s*:\s*"([^"]+)"', cleaned)
            title_redacted = redacted_match.group(1) if redacted_match else title
            return {"title": title, "title_redacted": title_redacted}

    return {"title": "", "title_redacted": ""}


def generate_event_title(
    llm: LocalLLM,
    subject: str,
    outcome: str,
    skill_outcome: str | None = None,
    reasoning: str | None = None,
) -> dict[str, str]:
    """Generate a display title and redacted version for an event.

    Args:
        llm: Local LLM instance.
        subject: Email subject or event description.
        outcome: Classification outcome (folder name).
        skill_outcome: Optional skill analysis result.
        reasoning: Optional classification reasoning.

    Returns:
        Dict with 'title' and 'title_redacted' keys.
    """
    user_message = build_title_user_message(subject, outcome, skill_outcome, reasoning)

    response = llm.infer(
        system_prompt=TITLE_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens_override=80,
        use_json_schema=False,
    )

    if not response.success:
        logger.warning("Title generation failed: %s", response.error)
        return {"title": subject, "title_redacted": subject}

    result = parse_title_response(response.content)

    if not result["title"]:
        logger.warning("Empty title from LLM, falling back to subject")
        return {"title": subject, "title_redacted": subject}

    logger.info("Generated title: %s", result["title"])
    return result
