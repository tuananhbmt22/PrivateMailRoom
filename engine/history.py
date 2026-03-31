"""Event history chain — immutable movement ledger.

Every event has a _history.json file that records every location change
as an ordered chain of steps. Like a blockchain: append-only, never edit.

Supports: ingestion, classification, redirect, reverse.
Each step records: action, from, to, timestamp, actor, reason, and optional correction data.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChainStep:
    """A single step in the event's movement chain."""

    step: int
    action: str  # ingested | classified | redirected | reversed
    from_location: str | None
    to_location: str
    timestamp: str
    actor: str  # email_poller | classifier | staff:<name>
    reason: str
    ai_result: dict[str, Any] | None = None
    correction: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "step": self.step,
            "action": self.action,
            "from": self.from_location,
            "to": self.to_location,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "reason": self.reason,
        }
        if self.ai_result:
            d["ai_result"] = self.ai_result
        if self.correction:
            d["correction"] = self.correction
        return d


@dataclass
class EventHistory:
    """The full movement chain for an event."""

    event_id: str
    chain: list[ChainStep] = field(default_factory=list)

    @property
    def current_location(self) -> str:
        if not self.chain:
            return "unknown"
        return self.chain[-1].to_location

    @property
    def current_step(self) -> int:
        return len(self.chain) - 1

    @property
    def previous_location(self) -> str | None:
        if len(self.chain) < 2:
            return None
        return self.chain[-1].from_location

    def append_step(
        self,
        action: str,
        from_location: str | None,
        to_location: str,
        actor: str,
        reason: str,
        ai_result: dict[str, Any] | None = None,
        correction: dict[str, Any] | None = None,
    ) -> ChainStep:
        """Append a new step to the chain."""
        step = ChainStep(
            step=len(self.chain),
            action=action,
            from_location=from_location,
            to_location=to_location,
            timestamp=datetime.now().isoformat(),
            actor=actor,
            reason=reason,
            ai_result=ai_result,
            correction=correction,
        )
        self.chain.append(step)
        return step

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "current_location": self.current_location,
            "current_step": self.current_step,
            "chain": [s.to_dict() for s in self.chain],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventHistory:
        history = cls(event_id=data["event_id"])
        for step_data in data.get("chain", []):
            history.chain.append(ChainStep(
                step=step_data["step"],
                action=step_data["action"],
                from_location=step_data.get("from"),
                to_location=step_data["to"],
                timestamp=step_data["timestamp"],
                actor=step_data["actor"],
                reason=step_data["reason"],
                ai_result=step_data.get("ai_result"),
                correction=step_data.get("correction"),
            ))
        return history


def load_history(event_dir: Path) -> EventHistory:
    """Load event history from _history.json, or create from _classification.json."""
    history_path = event_dir / "_history.json"

    if history_path.is_file():
        data = json.loads(history_path.read_text())
        return EventHistory.from_dict(data)

    # Migrate from old _classification.json format
    classification_path = event_dir / "_classification.json"
    if classification_path.is_file():
        receipt = json.loads(classification_path.read_text())
        history = EventHistory(event_id=receipt.get("event_id", event_dir.name))

        # Step 0: ingestion
        history.append_step(
            action="ingested",
            from_location=None,
            to_location="receive_channel",
            actor="email_poller",
            reason="Email ingested into receive channel",
        )

        # Step 1: classification — use folder key from parent directory name
        folder_key = event_dir.parent.name
        history.append_step(
            action="classified",
            from_location="receive_channel",
            to_location=folder_key,
            actor="classifier",
            reason=f"AI classification: {receipt.get('outcome', 'unknown')} ({receipt.get('confidence', 0)})",
            ai_result={
                "outcome": receipt.get("outcome"),
                "confidence": receipt.get("confidence"),
                "reasoning": receipt.get("reasoning"),
            },
        )

        save_history(event_dir, history)
        return history

    # No history at all — create minimal
    history = EventHistory(event_id=event_dir.name)
    return history


def save_history(event_dir: Path, history: EventHistory) -> None:
    """Save event history to _history.json."""
    history_path = event_dir / "_history.json"
    history_path.write_text(json.dumps(history.to_dict(), indent=2))


def move_event(
    event_dir: Path,
    destination_base: Path,
    history: EventHistory,
    action: str,
    actor: str,
    reason: str,
    correction: dict[str, Any] | None = None,
    ai_result: dict[str, Any] | None = None,
) -> Path | None:
    """Move an event folder to a new destination and update the history chain.

    Args:
        event_dir: Current location of the event folder.
        destination_base: The department folder to move into.
        history: The event's history chain.
        action: The action type (classified, redirected, reversed).
        actor: Who triggered this (classifier, staff:<name>).
        reason: Why this move happened.
        correction: Optional correction form data.
        ai_result: Optional AI classification result.

    Returns:
        New event directory path, or None on failure.
    """
    from_key = history.current_location
    to_key = destination_base.name

    destination = destination_base / event_dir.name

    # Handle name collision
    if destination.exists():
        suffix = datetime.now().strftime("_%Y%m%d_%H%M%S")
        destination = destination_base / f"{event_dir.name}{suffix}"

    # Append step to history BEFORE moving
    history.append_step(
        action=action,
        from_location=from_key,
        to_location=to_key,
        actor=actor,
        reason=reason,
        ai_result=ai_result,
        correction=correction,
    )

    # Save updated history into the event folder
    save_history(event_dir, history)

    # Atomic move: copy → verify → delete source
    try:
        shutil.copytree(event_dir, destination)
    except OSError as exc:
        logger.error("Failed to copy event: %s", exc)
        return None

    # Verify
    source_files = {f.name for f in event_dir.iterdir() if f.is_file()}
    dest_files = {f.name for f in destination.iterdir() if f.is_file()}
    if not source_files.issubset(dest_files):
        logger.error("Copy verification failed")
        shutil.rmtree(destination, ignore_errors=True)
        return None

    # Delete source
    try:
        shutil.rmtree(event_dir)
    except OSError as exc:
        logger.warning("Could not remove source: %s", exc)

    logger.info("Moved event '%s': %s → %s (%s)", history.event_id, from_key, to_key, action)
    return destination


def append_training_log(
    council_dir: Path,
    event_id: str,
    correction: dict[str, Any],
    history: EventHistory,
    event_dir: Path,
) -> None:
    """Append a correction record to the central training log (JSONL).

    This file is the dataset for improving the classification system.
    """
    log_path = council_dir / "_training_log.jsonl"

    # Read email body preview if available
    email_body_preview = ""
    email_subject = ""
    body_path = event_dir / "email_body.txt"
    if body_path.is_file():
        content = body_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        for line in lines:
            if line.startswith("Subject:"):
                email_subject = line[8:].strip()
                break
        email_body_preview = content[:500]

    # Find the original AI classification step
    ai_step = None
    for step in history.chain:
        if step.action == "classified" and step.ai_result:
            ai_step = step
            break

    # List event files
    event_files = [
        f.name for f in event_dir.iterdir()
        if f.is_file() and not f.name.startswith("_")
    ]

    record = {
        "timestamp": datetime.now().isoformat(),
        "event_id": event_id,
        "correction_type": correction.get("correction_type", "unknown"),
        "ai_failure_reason": correction.get("ai_failure_reason"),
        "ai_original_outcome": ai_step.ai_result.get("outcome") if ai_step and ai_step.ai_result else None,
        "ai_original_confidence": ai_step.ai_result.get("confidence") if ai_step and ai_step.ai_result else None,
        "ai_original_reasoning": ai_step.ai_result.get("reasoning") if ai_step and ai_step.ai_result else None,
        "correct_folder": correction.get("correct_folder"),
        "explanation": correction.get("explanation", ""),
        "staff_name": correction.get("staff_name", "anonymous"),
        "event_files": event_files,
        "email_subject": email_subject,
        "email_body_preview": email_body_preview,
        "full_chain_length": len(history.chain),
    }

    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")

    logger.info("Training log entry appended for event '%s': %s", event_id, correction.get("correction_type"))
