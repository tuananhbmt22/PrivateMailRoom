"""Dispatcher — moves classified events to their destination folders.

After the classifier determines where an event belongs, the dispatcher:
1. Writes a classification receipt (_classification.json) into the event folder
2. Moves the entire event folder to the destination department folder
3. Returns a DispatchOutcome with the result

All moves are atomic: copy → verify → delete source.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .classifier import ClassificationOutcome

logger = logging.getLogger(__name__)


@dataclass
class CouncilConfig:
    """Council-specific folder mapping loaded from council.yaml."""

    council_name: str
    council_dir: Path
    receive_channel: Path
    undetermined: Path
    folder_map: dict[str, Path]

    @classmethod
    def from_yaml(cls, council_dir: Path) -> CouncilConfig:
        """Load council configuration from council.yaml."""
        config_path = council_dir / "council.yaml"
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)

        folder_map: dict[str, Path] = {}
        for key, relative_path in raw.get("folder_map", {}).items():
            folder_map[key] = council_dir / relative_path

        return cls(
            council_name=raw["council"],
            council_dir=council_dir,
            receive_channel=council_dir / raw["paths"]["receive_channel"],
            undetermined=council_dir / raw["paths"]["undetermined"],
            folder_map=folder_map,
        )


@dataclass
class DispatchOutcome:
    """Result of a dispatch operation."""

    event_id: str
    outcome: str
    destination_path: Path | None
    receipt_path: Path | None
    moved: bool
    error: str | None = None


def build_receipt(result: ClassificationOutcome) -> dict[str, Any]:
    """Build the classification receipt that gets dropped into the event folder.

    This file explains WHY the event is in this folder — full traceability.
    """
    return {
        "event_id": result.event_id,
        "classified_at": datetime.now().isoformat(),
        "outcome": result.outcome,
        "confidence": round(result.confidence, 2),
        "reasoning": result.reasoning,
        "linked_files": result.linked_files,
        "file_count": result.file_count,
        "llm_latency_ms": round(result.llm_latency_ms, 0),
        "llm_tokens_used": result.llm_tokens_used,
        "inference_success": result.success,
        "inference_error": result.error,
    }


def resolve_destination(
    result: ClassificationOutcome,
    council: CouncilConfig,
    folder_tree: dict[str, Any],
) -> Path:
    """Determine the physical destination path for a classified event.

    Maps the LLM's outcome (folder display name) back to a folder key,
    then looks up the physical path from the council config.
    """
    if result.outcome == "Undetermined":
        return council.undetermined

    # Reverse lookup: find the folder key from the display name
    for key, folder_def in folder_tree.get("folders", {}).items():
        if folder_def.get("name") == result.outcome:
            destination = council.folder_map.get(key)
            if destination and destination.is_dir():
                return destination
            else:
                logger.warning(
                    "Folder key '%s' not mapped or directory missing, routing to undetermined",
                    key,
                )
                return council.undetermined

    logger.warning(
        "Outcome '%s' not found in folder tree, routing to undetermined",
        result.outcome,
    )
    return council.undetermined


class Dispatcher:
    """Moves classified events from receive_channel to department folders."""

    def __init__(self, council: CouncilConfig, folder_tree: dict[str, Any]) -> None:
        self._council = council
        self._folder_tree = folder_tree

    def dispatch(
        self,
        result: ClassificationOutcome,
        event_source: Path,
    ) -> DispatchOutcome:
        """Move an event folder to its classified destination.

        Steps:
        1. Write _classification.json receipt into the event folder
        2. Copy the event folder to the destination
        3. Verify the copy
        4. Delete the source

        Args:
            result: The classification outcome from the engine.
            event_source: Path to the event folder in receive_channel.

        Returns:
            DispatchOutcome with the result of the move.
        """
        destination_base = resolve_destination(result, self._council, self._folder_tree)
        destination = destination_base / event_source.name

        # Handle name collision — append timestamp
        if destination.exists():
            suffix = datetime.now().strftime("_%Y%m%d_%H%M%S")
            destination = destination_base / f"{event_source.name}{suffix}"

        # Step 1: Write receipt into the event folder BEFORE moving
        receipt = build_receipt(result)
        receipt["source_path"] = str(event_source)
        receipt["destination_path"] = str(destination)

        receipt_path = event_source / "_classification.json"
        try:
            receipt_path.write_text(json.dumps(receipt, indent=2))
            logger.info("Receipt written: %s", receipt_path)
        except OSError as exc:
            error_msg = f"Failed to write receipt: {exc}"
            logger.error(error_msg)
            return DispatchOutcome(
                event_id=result.event_id,
                outcome=result.outcome,
                destination_path=None,
                receipt_path=None,
                moved=False,
                error=error_msg,
            )

        # Step 2: Copy event folder to destination
        try:
            shutil.copytree(event_source, destination)
            logger.info("Copied %s → %s", event_source, destination)
        except OSError as exc:
            error_msg = f"Failed to copy event: {exc}"
            logger.error(error_msg)
            return DispatchOutcome(
                event_id=result.event_id,
                outcome=result.outcome,
                destination_path=None,
                receipt_path=receipt_path,
                moved=False,
                error=error_msg,
            )

        # Step 3: Verify copy — check file count matches
        source_files = set(f.name for f in event_source.iterdir() if f.is_file())
        dest_files = set(f.name for f in destination.iterdir() if f.is_file())

        if not source_files.issubset(dest_files):
            missing = source_files - dest_files
            error_msg = f"Copy verification failed, missing files: {missing}"
            logger.error(error_msg)
            # Clean up partial copy
            shutil.rmtree(destination, ignore_errors=True)
            return DispatchOutcome(
                event_id=result.event_id,
                outcome=result.outcome,
                destination_path=None,
                receipt_path=receipt_path,
                moved=False,
                error=error_msg,
            )

        # Step 4: Delete source
        try:
            shutil.rmtree(event_source)
            logger.info("Source removed: %s", event_source)
        except OSError as exc:
            # Non-fatal — files are already at destination
            logger.warning("Could not remove source %s: %s", event_source, exc)

        return DispatchOutcome(
            event_id=result.event_id,
            outcome=result.outcome,
            destination_path=destination,
            receipt_path=destination / "_classification.json",
            moved=True,
        )
