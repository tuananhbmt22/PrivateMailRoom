"""Nexus Forge — Core pipeline for autonomous folder learning.

Processes sample events to generate skill scrolls and classification entries.
Uses Claude API for high-quality schema generation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .claude_client import ClaudeClient, ClaudeConfig
from .prompts import (
    JOB1_SYSTEM_PROMPT,
    JOB2_SYSTEM_PROMPT,
    build_job1_user_message,
    build_job2_user_message,
)

logger = logging.getLogger(__name__)


@dataclass
class ForgeEvent:
    """A sample event loaded for Forge processing."""

    event_id: str
    path: Path
    email_body: str
    attachments: list[str]
    file_count: int


@dataclass
class ForgeResult:
    """Result of the full Forge pipeline for one folder."""

    folder_key: str
    folder_name: str
    event_blueprints: list[dict[str, Any]]
    classification_entry: dict[str, Any] | None
    scroll_json: dict[str, Any] | None
    errors: list[str] = field(default_factory=list)
    total_tokens: int = 0
    total_latency_ms: float = 0.0


def parse_json_response(raw: str) -> dict[str, Any] | None:
    """Parse JSON from Claude response, handling markdown fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    # Extract first complete JSON object
    brace_count = 0
    start_idx = -1
    for i, ch in enumerate(cleaned):
        if ch == '{':
            if start_idx == -1:
                start_idx = i
            brace_count += 1
        elif ch == '}':
            brace_count -= 1
            if brace_count == 0 and start_idx >= 0:
                try:
                    return json.loads(cleaned[start_idx:i + 1])
                except json.JSONDecodeError:
                    pass
                break

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def load_forge_event(event_dir: Path) -> ForgeEvent:
    """Load an event directory into a ForgeEvent for processing."""
    email_body = ""
    body_path = event_dir / "email_body.txt"
    if body_path.is_file():
        email_body = body_path.read_text(encoding="utf-8", errors="replace")
        if len(email_body) > 10000:
            email_body = email_body[:10000] + "\n[... truncated ...]"

    attachments: list[str] = []
    for f in sorted(event_dir.iterdir()):
        if f.is_file() and not f.name.startswith("_") and not f.name.startswith("."):
            if f.name != "email_body.txt":
                attachments.append(f"{f.name} ({f.stat().st_size} bytes)")

    file_count = len(list(event_dir.iterdir()))

    return ForgeEvent(
        event_id=event_dir.name,
        path=event_dir,
        email_body=email_body,
        attachments=attachments,
        file_count=file_count,
    )


def run_job1(
    client: ClaudeClient,
    event: ForgeEvent,
    folder_name: str,
    folder_key: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Job 1: Analyse a single event → produce event type blueprint.

    Returns:
        Tuple of (blueprint_dict, error_message).
    """
    # Build event text from email body + text attachments
    event_text = event.email_body

    # Read text content from attachments
    for f in sorted(event.path.iterdir()):
        if f.is_file() and not f.name.startswith("_") and f.name != "email_body.txt":
            ext = f.suffix.lower()
            if ext in {".txt", ".csv", ".md", ".html", ".htm"}:
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    if len(content) > 5000:
                        content = content[:5000] + "\n[... truncated ...]"
                    event_text += f"\n\n--- ATTACHMENT: {f.name} ---\n{content}"
                except OSError:
                    pass

    user_message = build_job1_user_message(
        folder_name=folder_name,
        folder_key=folder_key,
        event_text=event_text,
        attachment_names=event.attachments,
    )

    response = client.infer(JOB1_SYSTEM_PROMPT, user_message)

    if not response.success:
        return None, f"Job 1 failed for {event.event_id}: {response.error}"

    blueprint = parse_json_response(response.content)
    if not blueprint:
        return None, f"Job 1 JSON parse failed for {event.event_id}"

    logger.info(
        "Job 1 complete for '%s': event_type=%s (%d tokens, %.0fms)",
        event.event_id,
        blueprint.get("event_type_id", "unknown"),
        response.input_tokens + response.output_tokens,
        response.latency_ms,
    )

    return blueprint, None


def run_job2(
    client: ClaudeClient,
    folder_name: str,
    folder_key: str,
    event_blueprints: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Job 2: Synthesize all event blueprints → classification tree entry.

    Returns:
        Tuple of (classification_entry, error_message).
    """
    user_message = build_job2_user_message(
        folder_name=folder_name,
        folder_key=folder_key,
        event_blueprints=event_blueprints,
    )

    response = client.infer(JOB2_SYSTEM_PROMPT, user_message)

    if not response.success:
        return None, f"Job 2 failed: {response.error}"

    entry = parse_json_response(response.content)
    if not entry:
        return None, "Job 2 JSON parse failed"

    logger.info(
        "Job 2 complete: %d triggers, %d exclusions (%d tokens, %.0fms)",
        len(entry.get("triggers", [])),
        len(entry.get("exclusions", [])),
        response.input_tokens + response.output_tokens,
        response.latency_ms,
    )

    return entry, None


def build_scroll_json(
    folder_key: str,
    folder_name: str,
    event_blueprints: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the full scroll JSON from event blueprints."""
    # Collect all keywords from event type triggers
    all_keywords: list[str] = []
    all_concepts: list[str] = []
    event_types: dict[str, Any] = {}
    metadata_fields: dict[str, str] = {}

    for bp in event_blueprints:
        etype_id = bp.get("event_type_id", "unknown")
        triggers = bp.get("triggers", [])
        all_keywords.extend(triggers[:3])
        all_concepts.append(bp.get("description", ""))

        event_types[etype_id] = {
            "description": bp.get("description", ""),
            "triggers": triggers,
            "documents": bp.get("documents", {}),
            "cross_document_rules": bp.get("cross_document_rules", []),
            "completeness": bp.get("completeness", {}),
            "outcomes": bp.get("outcomes", {}),
        }

        # Collect metadata fields from all documents
        for doc_key, doc in bp.get("documents", {}).items():
            for field_key, field_def in doc.get("fields", {}).items():
                if field_key not in metadata_fields:
                    desc = field_def.get("description", field_key)
                    metadata_fields[field_key] = desc

    # Deduplicate keywords
    seen: set[str] = set()
    unique_keywords: list[str] = []
    for kw in all_keywords:
        kw_lower = kw.lower()
        if kw_lower not in seen:
            seen.add(kw_lower)
            unique_keywords.append(kw)

    return {
        "_schema": "folder_scroll",
        "_version": "2.0",
        "_generated_by": "nexus_forge",
        "_generated_at": datetime.now().isoformat(),
        "_verified": False,
        "skill_id": folder_key,
        "skill_name": folder_name,
        "department_key": folder_key,
        "description": f"Auto-generated scroll for {folder_name}",
        "matching": {
            "keywords": unique_keywords,
            "concepts": [c for c in all_concepts if c],
        },
        "event_types": event_types,
        "metadata_fields": metadata_fields,
        "response_templates": {},
    }


def run_forge(
    config: ClaudeConfig,
    folder_name: str,
    folder_key: str,
    event_dirs: list[Path],
    on_progress: Any = None,
) -> ForgeResult:
    """Run the full Forge pipeline for a folder.

    Args:
        config: Claude API configuration (from external.json).
        folder_name: Display name of the folder.
        folder_key: Machine key for the folder.
        event_dirs: List of event directory paths to process.
        on_progress: Optional callback(stage, event_id, status).

    Returns:
        ForgeResult with all generated artifacts.
    """
    if not config.is_configured:
        return ForgeResult(
            folder_key=folder_key,
            folder_name=folder_name,
            event_blueprints=[],
            classification_entry=None,
            scroll_json=None,
            errors=["Claude API key not configured in config/external.json"],
        )
    result = ForgeResult(
        folder_key=folder_key,
        folder_name=folder_name,
        event_blueprints=[],
        classification_entry=None,
        scroll_json=None,
    )

    client = ClaudeClient(config)

    try:
        # Job 1: Process each event
        for event_dir in event_dirs:
            event = load_forge_event(event_dir)

            if on_progress:
                on_progress("job1", event.event_id, "processing")

            blueprint, error = run_job1(client, event, folder_name, folder_key)

            if error:
                result.errors.append(error)
                if on_progress:
                    on_progress("job1", event.event_id, "error")
                continue

            result.event_blueprints.append(blueprint)
            if on_progress:
                on_progress("job1", event.event_id, "complete")

        if not result.event_blueprints:
            result.errors.append("No event blueprints generated — all Job 1 calls failed")
            return result

        # Job 2: Generate classification entry
        if on_progress:
            on_progress("job2", folder_key, "processing")

        classification_entry, error = run_job2(
            client, folder_name, folder_key, result.event_blueprints,
        )

        if error:
            result.errors.append(error)
            if on_progress:
                on_progress("job2", folder_key, "error")
        else:
            result.classification_entry = classification_entry
            if on_progress:
                on_progress("job2", folder_key, "complete")

        # Build scroll JSON
        result.scroll_json = build_scroll_json(
            folder_key, folder_name, result.event_blueprints,
        )

    finally:
        client.close()

    return result
