"""Folder schema builder for Nexus Forge.

Creates the standardized {folder_key}.json from source data.
This is the single source of truth for a folder's configuration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .sanitizer import sanitize_json

logger = logging.getLogger(__name__)


def build_folder_schema(
    folder_key: str,
    folder_name: str,
    description: str = "",
    external_id: str = "",
    source_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the standardized folder JSON schema.

    Args:
        folder_key: Machine key for the folder.
        folder_name: Display name.
        description: Optional description.
        external_id: Optional external system reference.
        source_schema: Raw source JSON (e.g., NSW Planning Portal data).

    Returns:
        Complete folder schema dict.
    """
    documents = extract_documents_from_source(source_schema) if source_schema else []

    return {
        "_schema_version": "1.0",
        "_generated_by": "forge",
        "_generated_at": datetime.now().isoformat(),
        "_verified": False,
        "folder_key": folder_key,
        "folder_name": folder_name,
        "description": description,
        "external_id": external_id,
        "status": "draft",
        "source_schema": sanitize_json(source_schema) if source_schema else None,
        "documents": documents,
    }


def extract_documents_from_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the documents array from a source schema.

    Handles NSW Planning Portal format (documents + deltaDocuments arrays).
    Falls back to empty list if no documents found.
    """
    docs: list[dict[str, Any]] = []
    seen_types: set[str] = set()

    for doc_list_key in ("documents", "deltaDocuments"):
        for raw_doc in source.get(doc_list_key, []):
            doc_name = raw_doc.get("documentName", "")
            doc_type = raw_doc.get("documentType", "") or doc_name

            # Deduplicate by document type
            if doc_type in seen_types:
                continue
            seen_types.add(doc_type)

            docs.append({
                "documentType": doc_type,
                "originalFileName": doc_name,
                "required": False,
                "mode": "verify",
                "extractFields": [],
            })

    return docs


def save_folder_schema(folder_dir: Path, folder_key: str, schema: dict[str, Any]) -> Path:
    """Save the folder schema JSON to the folder directory.

    Args:
        folder_dir: Physical folder path.
        folder_key: Machine key (used for filename).
        schema: The folder schema dict.

    Returns:
        Path to the saved JSON file.
    """
    schema_path = folder_dir / f"{folder_key}.json"
    schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False))
    logger.info("Folder schema saved: %s (%d documents)", schema_path, len(schema.get("documents", [])))
    return schema_path


def load_folder_schema(folder_dir: Path, folder_key: str) -> dict[str, Any] | None:
    """Load the folder schema JSON.

    Args:
        folder_dir: Physical folder path.
        folder_key: Machine key (used for filename).

    Returns:
        Schema dict or None if not found.
    """
    schema_path = folder_dir / f"{folder_key}.json"
    if not schema_path.is_file():
        return None
    try:
        return json.loads(schema_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load folder schema %s: %s", schema_path, exc)
        return None
