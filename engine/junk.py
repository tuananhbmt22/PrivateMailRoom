"""Junk fingerprint system — pattern-based junk detection.

Captures junk email patterns as fingerprints. Each fingerprint defines
match rules (sender domain, subject keywords, body keywords) that can
identify similar junk emails without an LLM call.

Current: LLM classifies junk, staff confirms, fingerprint is captured.
Future: Pre-filter script checks fingerprints before LLM call, instant junk.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

JUNK_TYPES = {
    "marketing": "Marketing / Promotional",
    "automated": "Automated Notification",
    "spam": "Spam / Phishing",
    "internal": "Internal / Duplicate",
    "irrelevant": "Irrelevant / Wrong Address",
}


@dataclass
class JunkFingerprint:
    """A pattern that identifies a type of junk email."""

    id: str
    junk_type: str
    created_at: str
    created_by: str
    match_rules: dict[str, Any]
    sample: dict[str, str]
    auto_delete: bool = False
    match_count: int = 0
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "junk_type": self.junk_type,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "match_rules": self.match_rules,
            "sample": self.sample,
            "auto_delete": self.auto_delete,
            "match_count": self.match_count,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JunkFingerprint:
        return cls(
            id=data["id"],
            junk_type=data["junk_type"],
            created_at=data["created_at"],
            created_by=data.get("created_by", "unknown"),
            match_rules=data.get("match_rules", {}),
            sample=data.get("sample", {}),
            auto_delete=data.get("auto_delete", False),
            match_count=data.get("match_count", 0),
            active=data.get("active", True),
        )


def load_junk_patterns(council_dir: Path) -> list[JunkFingerprint]:
    """Load all junk fingerprints from the council's pattern file."""
    patterns_path = council_dir / "_junk_patterns.json"
    if not patterns_path.is_file():
        return []

    try:
        data = json.loads(patterns_path.read_text())
        return [JunkFingerprint.from_dict(p) for p in data.get("patterns", [])]
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load junk patterns: %s", exc)
        return []


def save_junk_patterns(council_dir: Path, patterns: list[JunkFingerprint]) -> None:
    """Save junk fingerprints to the council's pattern file."""
    patterns_path = council_dir / "_junk_patterns.json"
    data = {
        "version": "1.0",
        "updated_at": datetime.now().isoformat(),
        "count": len(patterns),
        "patterns": [p.to_dict() for p in patterns],
    }
    patterns_path.write_text(json.dumps(data, indent=2))


def create_fingerprint_from_event(
    event_dir: Path,
    junk_type: str,
    staff_name: str,
    never_show_again: bool,
) -> JunkFingerprint:
    """Create a junk fingerprint from an event's email content.

    Extracts sender domain, subject keywords, and body keywords
    to build match rules that can identify similar junk emails.
    """
    subject = ""
    sender = ""
    body_text = ""

    # Read email body
    body_path = event_dir / "email_body.txt"
    if body_path.is_file():
        content = body_path.read_text(encoding="utf-8", errors="replace")
        for line in content.split("\n"):
            if line.startswith("Subject:"):
                subject = line[8:].strip()
            elif line.startswith("From:"):
                sender = line[5:].strip()
            elif not line.startswith(("To:", "Date:", "Cc:")):
                body_text += line + " "

    # Read meta if available
    meta_path = event_dir / "_email_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text())
            if not subject:
                subject = meta.get("subject", "")
            if not sender:
                sender = meta.get("from", "")
        except (json.JSONDecodeError, OSError):
            pass

    # Extract sender domain
    sender_domain = ""
    domain_match = re.search(r'@([\w.-]+)', sender)
    if domain_match:
        sender_domain = domain_match.group(1).lower()

    # Extract sender local part patterns
    sender_contains = []
    local_match = re.search(r'([\w.+-]+)@', sender)
    if local_match:
        local = local_match.group(1).lower()
        for keyword in ["noreply", "no-reply", "newsletter", "promo", "marketing", "info", "sales", "support", "admin", "notification", "alert"]:
            if keyword in local:
                sender_contains.append(keyword)

    # Extract subject keywords (common junk indicators)
    subject_lower = subject.lower()
    subject_contains = []
    junk_subject_keywords = [
        "unsubscribe", "newsletter", "special offer", "limited time",
        "free", "discount", "webinar", "demo", "trial", "promotion",
        "update your", "verify your", "confirm your", "action required",
        "automated", "notification", "reminder", "digest",
    ]
    for kw in junk_subject_keywords:
        if kw in subject_lower:
            subject_contains.append(kw)

    # Extract body keywords
    body_lower = body_text.lower()[:2000]
    body_contains = []
    junk_body_keywords = [
        "unsubscribe", "view in browser", "click here", "opt out",
        "manage preferences", "email preferences", "privacy policy",
        "this email was sent to", "you are receiving this",
        "if you no longer wish", "to stop receiving",
    ]
    for kw in junk_body_keywords:
        if kw in body_lower:
            body_contains.append(kw)

    # Generate fingerprint ID
    fp_id = f"junk_f_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    match_rules: dict[str, Any] = {}
    if sender_domain:
        match_rules["sender_domain_exact"] = sender_domain
    if sender_contains:
        match_rules["sender_contains"] = sender_contains
    if subject_contains:
        match_rules["subject_contains"] = subject_contains
    if body_contains:
        match_rules["body_contains"] = body_contains

    return JunkFingerprint(
        id=fp_id,
        junk_type=junk_type,
        created_at=datetime.now().isoformat(),
        created_by=staff_name,
        match_rules=match_rules,
        sample={
            "event_id": event_dir.name,
            "subject": subject,
            "sender": sender,
        },
        auto_delete=never_show_again,
        match_count=0,
        active=True,
    )


def check_junk_patterns(
    sender: str,
    subject: str,
    body: str,
    patterns: list[JunkFingerprint],
) -> JunkFingerprint | None:
    """Check if an email matches any active junk fingerprint.

    This is the future pre-filter function. Returns the matching
    pattern if found, None otherwise.
    """
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    body_lower = body.lower()[:2000]

    for pattern in patterns:
        if not pattern.active:
            continue

        rules = pattern.match_rules
        matched = False

        # Check sender domain
        if "sender_domain_exact" in rules:
            if rules["sender_domain_exact"] in sender_lower:
                matched = True

        # Check sender keywords
        if "sender_contains" in rules:
            for kw in rules["sender_contains"]:
                if kw in sender_lower:
                    matched = True
                    break

        # Check subject keywords (need at least one match)
        if "subject_contains" in rules and not matched:
            for kw in rules["subject_contains"]:
                if kw in subject_lower:
                    matched = True
                    break

        # Check body keywords (need at least one match)
        if "body_contains" in rules and not matched:
            for kw in rules["body_contains"]:
                if kw in body_lower:
                    matched = True
                    break

        if matched:
            return pattern

    return None
