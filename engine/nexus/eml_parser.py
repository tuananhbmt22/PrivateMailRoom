"""EML file parser for Nexus Forge.

Converts .eml files into event folders with the same structure
as the email ingester produces (email_body.txt + attachments).
"""

from __future__ import annotations

import email
import email.policy
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name if name else "unnamed_file"


def extract_text_body(msg: email.message.EmailMessage) -> str:
    """Extract plain text body from an email message."""
    body = msg.get_body(preferencelist=('plain', 'html'))
    if body is None:
        return ""

    content = body.get_content()
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='replace')

    content_type = body.get_content_type()
    if content_type == 'text/html':
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = re.sub(r'\s+', ' ', content).strip()

    return content


def parse_eml_to_event(
    eml_content: bytes,
    output_dir: Path,
    event_id: str | None = None,
) -> Path | None:
    """Parse a .eml file and create an event folder.

    Args:
        eml_content: Raw bytes of the .eml file.
        output_dir: Parent directory to create the event folder in.
        event_id: Optional event ID. Auto-generated if not provided.

    Returns:
        Path to the created event directory, or None on failure.
    """
    try:
        msg = email.message_from_bytes(eml_content, policy=email.policy.default)
    except Exception as exc:
        logger.error("Failed to parse .eml: %s", exc)
        return None

    subject = msg.get("Subject", "(No Subject)")
    sender = msg.get("From", "(Unknown Sender)")
    date_str = msg.get("Date", "")
    to_addr = msg.get("To", "")
    cc_addr = msg.get("Cc", "")

    if not event_id:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        msg_hash = abs(hash(msg.get("Message-ID", str(timestamp)))) % 100000
        event_id = f"forge_{timestamp}_{msg_hash:05d}"

    event_dir = output_dir / event_id
    if event_dir.exists():
        logger.warning("Event directory already exists: %s", event_dir)
        return None

    try:
        event_dir.mkdir(parents=True)
    except OSError as exc:
        logger.error("Failed to create event directory: %s", exc)
        return None

    # Save email body
    body_text = extract_text_body(msg)
    email_content = f"Subject: {subject}\nFrom: {sender}\nTo: {to_addr}\nDate: {date_str}\n"
    if cc_addr:
        email_content += f"Cc: {cc_addr}\n"
    email_content += f"\n{body_text}\n"

    body_path = event_dir / "email_body.txt"
    body_path.write_text(email_content, encoding="utf-8")

    # Save attachments
    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        if "attachment" not in content_disposition and "inline" not in content_disposition:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        filename = sanitize_filename(filename)
        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        att_path = event_dir / filename
        counter = 1
        while att_path.exists():
            stem = Path(filename).stem
            ext = Path(filename).suffix
            att_path = event_dir / f"{stem}_{counter}{ext}"
            counter += 1

        att_path.write_bytes(payload)
        logger.info("Saved attachment: %s (%d bytes)", att_path.name, len(payload))

    # Save email headers for traceability
    meta_path = event_dir / "_email_meta.txt"
    headers = "\n".join(f"{k}: {v}" for k, v in msg.items())
    meta_path.write_text(headers, encoding="utf-8")

    file_count = len([f for f in event_dir.iterdir() if f.is_file()])
    logger.info(
        "Parsed .eml → event '%s': subject='%s', %d files",
        event_id, subject, file_count,
    )

    return event_dir
