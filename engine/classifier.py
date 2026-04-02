"""Classification engine for the Mailroom pipeline.

Reads event folders, constructs LLM prompts from the generic system prompt
and council-specific folder tree, sends to local LLM, parses results.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .llm import LLMConfig, LLMResponse, LocalLLM

logger = logging.getLogger(__name__)


@dataclass
class EventFile:
    """A single file within an event."""

    filename: str
    content: str
    size_bytes: int


@dataclass
class Event:
    """An event is a batch of related files from the receive channel."""

    event_id: str
    path: Path
    files: list[EventFile]
    timestamp: datetime

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def filenames(self) -> list[str]:
        return [f.filename for f in self.files]


@dataclass
class ClassificationOutcome:
    """Parsed result from the LLM classification response."""

    event_id: str
    file_count: int
    outcome: str
    sub_item_id: str | None
    sub_item_name: str | None
    confidence: float
    sub_item_confidence: float
    reasoning: str
    display_title: str
    display_title_redacted: str
    linked_files: list[str]
    raw_response: str
    llm_latency_ms: float
    llm_tokens_used: int
    success: bool
    error: str | None = None


def load_system_prompt(prompt_path: Path) -> str:
    """Load the generic classification system prompt."""
    with open(prompt_path, "r") as f:
        return f.read()


def load_folder_tree(tree_path: Path) -> dict[str, Any]:
    """Load the council-specific folder tree JSON."""
    with open(tree_path, "r") as f:
        return json.load(f)


def read_event(event_dir: Path) -> Event:
    """Read all files in an event directory into an Event object.

    Only reads text-based files for classification. Binary files (PDFs, images)
    are noted as attachments but their content is not sent to the LLM.
    Skips internal metadata files (_email_meta.txt, _email_meta.json).
    """
    TEXT_EXTENSIONS = {".txt", ".eml", ".csv", ".md", ".html", ".htm", ".xml", ".json"}
    SKIP_PREFIXES = ("_email_meta", "_classification")

    files: list[EventFile] = []
    attachment_names: list[str] = []

    for file_path in sorted(event_dir.iterdir()):
        if not file_path.is_file() or file_path.name.startswith("."):
            continue

        # Skip internal metadata files
        if any(file_path.name.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue

        ext = file_path.suffix.lower()

        if ext in TEXT_EXTENSIONS:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                # Truncate very long text files to prevent context overflow
                if len(content) > 8000:
                    content = content[:8000] + "\n\n[... truncated, file too long for classification ...]"
                files.append(EventFile(
                    filename=file_path.name,
                    content=content,
                    size_bytes=file_path.stat().st_size,
                ))
            except OSError as exc:
                logger.warning("Cannot read file %s: %s", file_path, exc)
        else:
            # Binary file — note as attachment without reading content
            attachment_names.append(f"{file_path.name} ({file_path.stat().st_size} bytes)")

    # Add a summary of binary attachments so the LLM knows they exist
    if attachment_names:
        att_summary = "Attachments present (not readable as text):\n" + "\n".join(f"  - {a}" for a in attachment_names)
        files.append(EventFile(
            filename="_attachments_summary",
            content=att_summary,
            size_bytes=0,
        ))

    return Event(
        event_id=event_dir.name,
        path=event_dir,
        files=files,
        timestamp=datetime.now(),
    )


def build_user_message(event: Event) -> str:
    """Construct the user message from all files in the event.

    The LLM receives this as the content to classify.
    """
    parts = [f"EVENT ID: {event.event_id}", f"FILE COUNT: {event.file_count}", ""]

    for i, ef in enumerate(event.files, 1):
        parts.append(f"--- FILE {i}: {ef.filename} ---")
        parts.append(ef.content.strip())
        parts.append("")

    return "\n".join(parts)


def build_system_message(system_prompt: str, folder_tree: dict[str, Any]) -> str:
    """Combine the generic system prompt with the council's folder tree.

    The system prompt defines the task; the tree JSON provides the rules.
    """
    tree_json = json.dumps(folder_tree, indent=2)
    return f"{system_prompt}\n\n---\n\n## Folder Tree (Council-Specific Rules)\n\n```json\n{tree_json}\n```"


def parse_llm_response(raw_content: str, event: Event) -> ClassificationOutcome:
    """Parse the LLM's JSON response into a ClassificationOutcome.

    Handles malformed JSON gracefully — returns Undetermined on parse failure.
    """
    cleaned = raw_content.strip()

    # Strip markdown code fences if the LLM wrapped the JSON
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM response as JSON: %s", exc)
        return ClassificationOutcome(
            event_id=event.event_id,
            file_count=event.file_count,
            outcome="Undetermined",
            sub_item_id=None,
            sub_item_name=None,
            confidence=0.0,
            sub_item_confidence=0.0,
            reasoning=f"LLM response was not valid JSON: {exc}",
            display_title="",
            display_title_redacted="",
            linked_files=event.filenames,
            raw_response=raw_content,
            llm_latency_ms=0.0,
            llm_tokens_used=0,
            success=False,
            error=f"JSON parse error: {exc}",
        )

    return ClassificationOutcome(
        event_id=parsed.get("event_id", event.event_id),
        file_count=parsed.get("file_count", event.file_count),
        outcome=parsed.get("outcome", "Undetermined"),
        sub_item_id=parsed.get("sub_item_id"),
        sub_item_name=parsed.get("sub_item_name"),
        confidence=float(parsed.get("confidence", 0.0)),
        sub_item_confidence=float(parsed.get("sub_item_confidence", 0.0)),
        reasoning=parsed.get("reasoning", "No reasoning provided"),
        display_title=parsed.get("display_title", ""),
        display_title_redacted=parsed.get("display_title_redacted", ""),
        linked_files=parsed.get("linked_files", event.filenames),
        raw_response=raw_content,
        llm_latency_ms=0.0,
        llm_tokens_used=0,
        success=True,
    )


class ClassificationEngine:
    """Orchestrates event classification using the local LLM.

    Loads the generic prompt and council tree once, then classifies
    events by sending them to the LLM and parsing responses.
    """

    def __init__(
        self,
        llm_config_path: Path,
        system_prompt_path: Path,
        folder_tree_path: Path,
    ) -> None:
        self._llm_config = LLMConfig.from_yaml(llm_config_path)
        self._llm = LocalLLM(self._llm_config)
        self._system_prompt = load_system_prompt(system_prompt_path)
        self._folder_tree = load_folder_tree(folder_tree_path)
        self._system_message = build_system_message(
            self._system_prompt, self._folder_tree
        )

        council_name = self._folder_tree.get("council", "Unknown")
        logger.info(
            "Classification engine initialized for '%s' using model '%s' at %s",
            council_name,
            self._llm_config.model,
            self._llm_config.endpoint,
        )

    def classify_event(self, event: Event) -> ClassificationOutcome:
        """Classify a single event by sending it to the LLM.

        Args:
            event: The event to classify (contains all files).

        Returns:
            ClassificationOutcome with the folder assignment or Undetermined.
        """
        logger.info(
            "Classifying event '%s' (%d files: %s)",
            event.event_id,
            event.file_count,
            ", ".join(event.filenames),
        )

        user_message = build_user_message(event)
        llm_response: LLMResponse = self._llm.infer(self._system_message, user_message)

        if not llm_response.success:
            logger.error(
                "LLM inference failed for event '%s': %s",
                event.event_id,
                llm_response.error,
            )
            return ClassificationOutcome(
                event_id=event.event_id,
                file_count=event.file_count,
                outcome="Undetermined",
                sub_item_id=None,
                sub_item_name=None,
                confidence=0.0,
                sub_item_confidence=0.0,
                reasoning=f"Inference failure: {llm_response.error}",
                display_title="",
                display_title_redacted="",
                linked_files=event.filenames,
                raw_response=llm_response.content,
                llm_latency_ms=llm_response.latency_ms,
                llm_tokens_used=llm_response.tokens_used,
                success=False,
                error=llm_response.error,
            )

        result = parse_llm_response(llm_response.content, event)
        result.llm_latency_ms = llm_response.latency_ms
        result.llm_tokens_used = llm_response.tokens_used

        # Validate outcome against folder tree
        valid_names = {
            folder["name"]
            for folder in self._folder_tree["folders"].values()
        }
        valid_names.add("Undetermined")

        if result.outcome not in valid_names:
            logger.warning(
                "LLM returned invalid folder '%s' for event '%s', forcing Undetermined",
                result.outcome,
                event.event_id,
            )
            result.outcome = "Undetermined"
            result.reasoning = f"LLM returned invalid folder name: {result.outcome}"

        # Enforce confidence threshold
        threshold = self._folder_tree.get("confidence_threshold", 0.70)
        if result.outcome != "Undetermined" and result.confidence < threshold:
            logger.warning(
                "Confidence %.2f below threshold %.2f for event '%s', forcing Undetermined",
                result.confidence,
                threshold,
                event.event_id,
            )
            original_outcome = result.outcome
            result.outcome = "Undetermined"
            result.reasoning = (
                f"Below confidence threshold: {result.confidence:.2f} < {threshold:.2f} "
                f"(was: {original_outcome})"
            )

        logger.info(
            "Event '%s' classified as '%s' (confidence: %.2f, latency: %.0fms)",
            event.event_id,
            result.outcome,
            result.confidence,
            result.llm_latency_ms,
        )

        return result

    def classify_event_dir(self, event_dir: Path) -> ClassificationOutcome:
        """Convenience method: read an event directory and classify it.

        Args:
            event_dir: Path to the event folder.

        Returns:
            ClassificationOutcome.
        """
        event = read_event(event_dir)
        if not event.files:
            logger.warning("Event '%s' has no readable files", event.event_id)
            return ClassificationOutcome(
                event_id=event.event_id,
                file_count=0,
                outcome="Undetermined",
                sub_item_id=None,
                sub_item_name=None,
                confidence=0.0,
                sub_item_confidence=0.0,
                reasoning="Event contains no readable files",
                display_title="",
                display_title_redacted="",
                linked_files=[],
                raw_response="",
                llm_latency_ms=0.0,
                llm_tokens_used=0,
                success=False,
                error="No readable files in event",
            )
        return self.classify_event(event)

    def close(self) -> None:
        """Release resources."""
        self._llm.close()
