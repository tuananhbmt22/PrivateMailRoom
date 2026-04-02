"""MSG file parser — converts Outlook .MSG files into event folders.

Uses extract-msg library to parse Microsoft Outlook message format.
Creates the same event folder structure as the email ingester.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name if name else "unnamed_file"


def parse_msg_to_event(msg_path: Path, output_dir: Path) -> Path | None:
    """Parse a .MSG file and create an event folder.

    Args:
        msg_path: Path to the .MSG file.
        output_dir: Parent directory to create the event folder in.

    Returns:
        Path to the created event directory, or None on failure.
    """
    try:
        import extract_msg
    except ImportError:
        logger.error("extract-msg not installed: pip install extract-msg")
        return None

    try:
        msg = extract_msg.Message(str(msg_path))
    except Exception as exc:
        logger.error("Failed to parse .MSG file %s: %s", msg_path.name, exc)
        return None

    subject = msg.subject or "(No Subject)"
    sender = msg.sender or "(Unknown Sender)"
    date_str = str(msg.date) if msg.date else ""
    to_addr = msg.to or ""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', subject.lower().strip())[:30]
    event_id = f"demo_{safe_name}_{timestamp}"

    event_dir = output_dir / event_id
    if event_dir.exists():
        return None

    try:
        event_dir.mkdir(parents=True)
    except OSError as exc:
        logger.error("Failed to create event dir: %s", exc)
        return None

    # Save email body
    body_text = msg.body or ""
    email_content = f"Subject: {subject}\nFrom: {sender}\nTo: {to_addr}\nDate: {date_str}\n\n{body_text}\n"
    body_path = event_dir / "email_body.txt"
    body_path.write_text(email_content, encoding="utf-8")

    # Save attachments
    for att in msg.attachments:
        try:
            filename = sanitize_filename(att.longFilename or att.shortFilename or "attachment")
            att_path = event_dir / filename
            counter = 1
            while att_path.exists():
                stem = Path(filename).stem
                ext = Path(filename).suffix
                att_path = event_dir / f"{stem}_{counter}{ext}"
                counter += 1
            att_path.write_bytes(att.data)
        except Exception as exc:
            logger.warning("Failed to save attachment: %s", exc)

    # Save headers
    meta_path = event_dir / "_email_meta.txt"
    meta_path.write_text(
        f"Subject: {subject}\nFrom: {sender}\nTo: {to_addr}\nDate: {date_str}",
        encoding="utf-8",
    )

    msg.close()

    file_count = len([f for f in event_dir.iterdir() if f.is_file()])
    logger.info("Parsed .MSG → event '%s': subject='%s', %d files", event_id, subject, file_count)
    return event_dir
