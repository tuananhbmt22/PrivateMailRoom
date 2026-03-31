#!/usr/bin/env python3
"""Kajima Mailroom — Classification Runner.

Processes events in a council's receive_channel, classifies them,
and optionally dispatches (moves) them to department folders.

Usage:
    # Dry run — classify only, no file moves
    python classify.py --council Test_Council

    # Live run — classify AND move files to departments
    python classify.py --council Test_Council --live

    # Single event
    python classify.py --council Test_Council --event event_001

    # Save results to file
    python classify.py --council Test_Council --output results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from engine.classifier import (
    ClassificationEngine,
    ClassificationOutcome,
    load_folder_tree,
)
from engine.dispatcher import CouncilConfig, Dispatcher, DispatchOutcome

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = BASE_DIR / "config"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mailroom.runner")


def find_council_dir(council_name: str) -> Path:
    """Locate the council directory under the mailroom app."""
    council_dir = BASE_DIR / council_name
    if not council_dir.is_dir():
        logger.error("Council directory not found: %s", council_dir)
        sys.exit(1)
    return council_dir


def find_events(receive_channel: Path, specific_event: str | None = None) -> list[Path]:
    """List event directories in the receive channel."""
    if specific_event:
        event_dir = receive_channel / specific_event
        if not event_dir.is_dir():
            logger.error("Event not found: %s", event_dir)
            sys.exit(1)
        return [event_dir]

    events = sorted(
        [d for d in receive_channel.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda p: p.name,
    )

    if not events:
        logger.info("No events found in receive channel: %s", receive_channel)

    return events


def format_classification(result: ClassificationOutcome) -> dict:
    """Convert a ClassificationOutcome to a JSON-serializable dict."""
    return {
        "event_id": result.event_id,
        "file_count": result.file_count,
        "outcome": result.outcome,
        "confidence": round(result.confidence, 2),
        "reasoning": result.reasoning,
        "linked_files": result.linked_files,
        "llm_latency_ms": round(result.llm_latency_ms, 0),
        "llm_tokens_used": result.llm_tokens_used,
        "success": result.success,
        "error": result.error,
    }


def format_dispatch(dispatch: DispatchOutcome) -> dict:
    """Convert a DispatchOutcome to a JSON-serializable dict."""
    return {
        "event_id": dispatch.event_id,
        "outcome": dispatch.outcome,
        "destination": str(dispatch.destination_path) if dispatch.destination_path else None,
        "receipt": str(dispatch.receipt_path) if dispatch.receipt_path else None,
        "moved": dispatch.moved,
        "error": dispatch.error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Kajima Mailroom Classification Runner")
    parser.add_argument(
        "--council", required=True,
        help="Council directory name (e.g., Test_Council)",
    )
    parser.add_argument(
        "--event", default=None,
        help="Specific event to classify (e.g., event_001). Omit to process all.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file for results JSON. Omit to print to stdout.",
    )
    parser.add_argument(
        "--live", action="store_true", default=False,
        help="Live mode: classify AND move files to department folders.",
    )
    args = parser.parse_args()

    council_dir = find_council_dir(args.council)
    receive_channel = council_dir / "receive_channel"

    if not receive_channel.is_dir():
        logger.error("Receive channel not found: %s", receive_channel)
        sys.exit(1)

    # Load folder tree (needed by both classifier and dispatcher)
    tree_path = CONFIG_DIR / "classification_only_tree.json"
    folder_tree = load_folder_tree(tree_path)

    # Initialize classifier
    engine = ClassificationEngine(
        llm_config_path=CONFIG_DIR / "llm.yaml",
        system_prompt_path=CONFIG_DIR / "classification_only_prompt.md",
        folder_tree_path=tree_path,
    )

    # Initialize dispatcher if live mode
    dispatcher: Dispatcher | None = None
    if args.live:
        council_config = CouncilConfig.from_yaml(council_dir)
        dispatcher = Dispatcher(council_config, folder_tree)
        logger.info("LIVE MODE — files will be moved to department folders")
    else:
        logger.info("DRY RUN — classify only, no files will be moved")

    events = find_events(receive_channel, args.event)
    results: list[dict] = []

    logger.info(
        "Processing %d event(s) for council '%s'",
        len(events),
        args.council,
    )

    for event_dir in events:
        # Classify
        classification = engine.classify_event_dir(event_dir)
        entry: dict = {
            "classification": format_classification(classification),
            "dispatch": None,
        }

        status_icon = "✓" if classification.outcome != "Undetermined" else "?"
        logger.info(
            "%s %s → %s (%.2f) — %s",
            status_icon,
            classification.event_id,
            classification.outcome,
            classification.confidence,
            classification.reasoning,
        )

        # Dispatch if live mode
        if dispatcher and classification.success:
            dispatch_result = dispatcher.dispatch(classification, event_dir)
            entry["dispatch"] = format_dispatch(dispatch_result)

            if dispatch_result.moved:
                logger.info(
                    "  📁 Moved to: %s", dispatch_result.destination_path
                )
            else:
                logger.error(
                    "  ❌ Dispatch failed: %s", dispatch_result.error
                )

        results.append(entry)

    engine.close()

    # Build output
    classified_count = sum(
        1 for r in results
        if r["classification"]["outcome"] != "Undetermined"
    )
    output_payload = {
        "council": args.council,
        "mode": "live" if args.live else "dry_run",
        "timestamp": datetime.now().isoformat(),
        "total_events": len(results),
        "classified": classified_count,
        "undetermined": len(results) - classified_count,
        "results": results,
    }

    output_json = json.dumps(output_payload, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(output_json)
        logger.info("Results written to %s", output_path)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
