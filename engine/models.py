"""Shared data models for the Mailroom pipeline.

All data flowing through the pipeline uses these dataclasses.
No raw dicts — everything is typed and explicit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class DispatchStatus(str, Enum):
    """Outcome of the classification + dispatch pipeline."""

    CLASSIFIED = "classified"
    JUNK = "junk"
    UNDETERMINED = "undetermined"
    REVERTED = "reverted"
    FAILED = "failed"


@dataclass(frozen=True)
class FileEvent:
    """Represents a new file detected in the receive channel."""

    path: Path
    timestamp: datetime
    size_bytes: int
    checksum: str  # SHA-256

    @staticmethod
    def compute_checksum(file_path: Path) -> str:
        """Compute SHA-256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()


@dataclass
class ClassificationResult:
    """Output from the classifier — determines where a file goes."""

    department: str | None
    document_type: str | None
    confidence: float
    reasoning: str
    status: DispatchStatus

    @property
    def is_routable(self) -> bool:
        """Whether this result has a valid destination department."""
        return self.status == DispatchStatus.CLASSIFIED and self.department is not None


@dataclass
class DocumentMetadata:
    """Structured metadata extracted from a classified document."""

    filename: str
    document_date: date | None = None
    reference_id: str | None = None
    person_name: str | None = None
    custom_fields: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "filename": self.filename,
            "document_date": self.document_date.isoformat() if self.document_date else None,
            "reference_id": self.reference_id,
            "person_name": self.person_name,
            "custom_fields": self.custom_fields,
        }


@dataclass
class DispatchResult:
    """Outcome of the dispatcher's file move operation."""

    file_event: FileEvent
    classification: ClassificationResult
    metadata: DocumentMetadata
    destination_path: Path | None
    status: DispatchStatus
    failure_reason: str | None = None
    db_record_id: int | None = None


@dataclass
class Skill:
    """Parsed representation of a department skill file."""

    department: str
    name: str
    classification_hints: list[str]
    validation_rules: list[str]
    metadata_fields: list[str]
    output_format: str
    raw_content: str  # Full .md content for LLM context injection


@dataclass
class DepartmentNode:
    """A single department in the tree schema."""

    key: str
    name: str
    path: Path
    subtypes: list[str]


@dataclass
class LLMResponse:
    """Raw response from the local LLM."""

    content: str
    model: str
    tokens_used: int
    latency_ms: float
    success: bool
    error: str | None = None
