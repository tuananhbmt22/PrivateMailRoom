"""Email ingestion service for the Mailroom pipeline.

Connects to an IMAP mailbox, polls for new unread emails,
and converts each email + attachments into an event folder
in the council's receive_channel.

Supports any IMAP-compatible server: Outlook, Exchange, Gmail, etc.
All connections stay on the local network in production.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailConfig:
    """IMAP email ingestion configuration."""

    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    password: str
    poll_interval_seconds: int
    inbox_folder: str
    after_processing: str
    processed_folder: str
    since_date: str
    max_attachment_size_mb: int
    allowed_extensions: list[str]

    @classmethod
    def from_yaml(cls, config_path: Path) -> EmailConfig:
        """Load email configuration from YAML file."""
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)
        return cls(
            imap_host=raw["imap_host"],
            imap_port=raw.get("imap_port", 993),
            use_ssl=raw.get("use_ssl", True),
            username=raw["username"],
            password=raw["password"],
            poll_interval_seconds=raw.get("poll_interval_seconds", 30),
            inbox_folder=raw.get("inbox_folder", "INBOX"),
            after_processing=raw.get("after_processing", "mark_read"),
            processed_folder=raw.get("processed_folder", "Processed"),
            since_date=raw.get("since_date", ""),
            max_attachment_size_mb=raw.get("max_attachment_size_mb", 25),
            allowed_extensions=raw.get("allowed_extensions", []),
        )

    @property
    def max_attachment_bytes(self) -> int:
        return self.max_attachment_size_mb * 1024 * 1024


def sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name if name else "unnamed_file"


def extract_text_body(msg: EmailMessage) -> str:
    """Extract the plain text body from an email message.

    Falls back to stripping HTML if no plain text part exists.
    """
    body = msg.get_body(preferencelist=('plain', 'html'))
    if body is None:
        return ""

    content = body.get_content()
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='replace')

    # If we got HTML, do a basic strip (good enough for classification)
    content_type = body.get_content_type()
    if content_type == 'text/html':
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = re.sub(r'\s+', ' ', content).strip()

    return content


def extract_attachments(msg: EmailMessage, config: EmailConfig) -> list[tuple[str, bytes]]:
    """Extract attachments from an email message.

    Returns list of (filename, content_bytes) tuples.
    Filters by allowed extensions and max size.
    """
    attachments: list[tuple[str, bytes]] = []

    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        if "attachment" not in content_disposition and "inline" not in content_disposition:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        filename = sanitize_filename(filename)

        # Check extension
        ext = Path(filename).suffix.lower()
        if config.allowed_extensions and ext not in config.allowed_extensions:
            logger.info("Skipping attachment '%s' — extension '%s' not allowed", filename, ext)
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        # Check size
        if len(payload) > config.max_attachment_bytes:
            logger.warning(
                "Skipping attachment '%s' — %d bytes exceeds %d MB limit",
                filename, len(payload), config.max_attachment_size_mb,
            )
            continue

        attachments.append((filename, payload))

    return attachments


def email_to_event(
    msg: EmailMessage,
    event_id: str,
    receive_channel: Path,
    config: EmailConfig,
) -> Path | None:
    """Convert an email message into an event folder in the receive channel.

    Creates:
        receive_channel/{event_id}/
            email_body.txt      — subject + from + date + body text
            attachment1.pdf     — each attachment as a separate file
            _email_meta.txt     — raw email headers for traceability

    Returns the event directory path, or None on failure.
    """
    event_dir = receive_channel / event_id
    if event_dir.exists():
        logger.warning("Event directory already exists: %s", event_dir)
        return None

    try:
        event_dir.mkdir(parents=True)
    except OSError as exc:
        logger.error("Failed to create event directory %s: %s", event_dir, exc)
        return None

    # Extract email metadata
    subject = msg.get("Subject", "(No Subject)")
    sender = msg.get("From", "(Unknown Sender)")
    date_str = msg.get("Date", "")
    to_addr = msg.get("To", "")
    cc_addr = msg.get("Cc", "")

    # Save email body
    body_text = extract_text_body(msg)
    email_content = f"Subject: {subject}\nFrom: {sender}\nTo: {to_addr}\nDate: {date_str}\n"
    if cc_addr:
        email_content += f"Cc: {cc_addr}\n"
    email_content += f"\n{body_text}\n"

    body_path = event_dir / "email_body.txt"
    body_path.write_text(email_content, encoding="utf-8")

    # Save attachments
    attachments = extract_attachments(msg, config)
    for filename, content in attachments:
        # Handle duplicate filenames
        att_path = event_dir / filename
        counter = 1
        while att_path.exists():
            stem = Path(filename).stem
            ext = Path(filename).suffix
            att_path = event_dir / f"{stem}_{counter}{ext}"
            counter += 1

        att_path.write_bytes(content)
        logger.info("Saved attachment: %s (%d bytes)", att_path.name, len(content))

    # Save email headers for traceability
    meta_path = event_dir / "_email_meta.txt"
    headers = "\n".join(f"{k}: {v}" for k, v in msg.items())
    meta_path.write_text(headers, encoding="utf-8")

    file_count = len(list(event_dir.iterdir()))
    logger.info(
        "Created event '%s' from email: subject='%s', from='%s', %d files",
        event_id, subject, sender, file_count,
    )

    return event_dir


class EmailIngester:
    """Polls an IMAP mailbox and converts emails to events.

    Tracks processed email UIDs in a local JSON file to avoid
    re-processing emails that have already been ingested.

    Usage:
        ingester = EmailIngester(config, receive_channel)
        ingester.poll_once()       # Single poll
        ingester.run_forever()     # Continuous polling loop
    """

    def __init__(self, config: EmailConfig, receive_channel: Path) -> None:
        self._config = config
        self._receive_channel = receive_channel
        self._connection: imaplib.IMAP4_SSL | imaplib.IMAP4 | None = None
        self._processed_uids_path = receive_channel.parent / "_processed_uids.json"
        self._processed_uids: set[str] = self._load_processed_uids()

    def _load_processed_uids(self) -> set[str]:
        """Load the set of already-processed email UIDs from disk."""
        if self._processed_uids_path.is_file():
            try:
                import json
                data = json.loads(self._processed_uids_path.read_text())
                return set(data.get("uids", []))
            except (json.JSONDecodeError, OSError):
                pass
        return set()

    def _save_processed_uids(self) -> None:
        """Persist processed UIDs to disk."""
        import json
        self._processed_uids_path.write_text(
            json.dumps({"uids": sorted(self._processed_uids), "count": len(self._processed_uids)}, indent=2)
        )

    def connect(self) -> None:
        """Establish IMAP connection and authenticate."""
        logger.info("Connecting to %s:%d", self._config.imap_host, self._config.imap_port)

        if self._config.use_ssl:
            self._connection = imaplib.IMAP4_SSL(
                self._config.imap_host,
                self._config.imap_port,
            )
        else:
            self._connection = imaplib.IMAP4(
                self._config.imap_host,
                self._config.imap_port,
            )

        self._connection.login(self._config.username, self._config.password)
        logger.info("Authenticated as %s", self._config.username)

    def disconnect(self) -> None:
        """Close the IMAP connection."""
        if self._connection:
            try:
                self._connection.logout()
            except Exception:
                pass
            self._connection = None

    def poll_once(self) -> list[Path]:
        """Poll the inbox once and convert new emails to events.

        Uses UID-based tracking to skip already-processed emails.
        Filters by since_date if configured.

        Returns list of created event directory paths.
        """
        if not self._connection:
            self.connect()

        conn = self._connection
        conn.select(self._config.inbox_folder)

        # Build search criteria — use SINCE date to limit scope
        if self._config.since_date:
            search_criteria = f'(SINCE "{self._config.since_date}")'
        else:
            search_criteria = "(UNSEEN)"

        status, message_ids = conn.search(None, search_criteria)
        if status != "OK" or not message_ids[0]:
            return []

        ids = message_ids[0].split()

        # Fetch UIDs for dedup
        new_ids = []
        for msg_id in ids:
            status, uid_data = conn.fetch(msg_id, "(UID)")
            if status == "OK" and uid_data[0]:
                uid_str = uid_data[0].decode() if isinstance(uid_data[0], bytes) else str(uid_data[0])
                # Extract UID number from response like '1 (UID 12345)'
                import re as _re
                uid_match = _re.search(r'UID\s+(\d+)', uid_str)
                if uid_match:
                    uid = uid_match.group(1)
                    if uid not in self._processed_uids:
                        new_ids.append((msg_id, uid))

        if not new_ids:
            logger.info("No new emails (all %d already processed)", len(ids))
            return []

        logger.info("Found %d new email(s) out of %d total", len(new_ids), len(ids))

        created_events: list[Path] = []

        for msg_id, uid in new_ids:
            try:
                event_path = self._process_email(conn, msg_id)
                if event_path:
                    created_events.append(event_path)
                    self._processed_uids.add(uid)
            except Exception as exc:
                logger.error("Failed to process email UID %s: %s", uid, exc)

        # Save updated UIDs
        if created_events:
            self._save_processed_uids()

        return created_events

    def _process_email(
        self,
        conn: imaplib.IMAP4_SSL | imaplib.IMAP4,
        msg_id: bytes,
    ) -> Path | None:
        """Fetch and process a single email by its IMAP ID."""
        status, msg_data = conn.fetch(msg_id, "(RFC822)")
        if status != "OK" or not msg_data[0]:
            return None

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email, policy=email.policy.default)

        # Generate event ID from timestamp + message ID hash
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        msg_hash = abs(hash(msg.get("Message-ID", str(msg_id)))) % 100000
        event_id = f"email_{timestamp}_{msg_hash:05d}"

        event_path = email_to_event(msg, event_id, self._receive_channel, self._config)

        if event_path:
            self._mark_processed(conn, msg_id)

        return event_path

    def _mark_processed(
        self,
        conn: imaplib.IMAP4_SSL | imaplib.IMAP4,
        msg_id: bytes,
    ) -> None:
        """Mark an email as processed based on config."""
        action = self._config.after_processing

        if action == "mark_read":
            conn.store(msg_id, "+FLAGS", "\\Seen")

        elif action == "move":
            # Create processed folder if it doesn't exist
            conn.create(self._config.processed_folder)
            conn.copy(msg_id, self._config.processed_folder)
            conn.store(msg_id, "+FLAGS", "\\Deleted")
            conn.expunge()

        elif action == "delete":
            conn.store(msg_id, "+FLAGS", "\\Deleted")
            conn.expunge()

    def run_forever(self) -> None:
        """Continuously poll the inbox at the configured interval.

        Reconnects automatically on connection failures.
        """
        logger.info(
            "Starting email ingestion loop — polling every %ds",
            self._config.poll_interval_seconds,
        )

        while True:
            try:
                events = self.poll_once()
                if events:
                    logger.info("Ingested %d new event(s)", len(events))
            except (imaplib.IMAP4.error, OSError, ConnectionError) as exc:
                logger.warning("IMAP connection error: %s — reconnecting", exc)
                self.disconnect()
                try:
                    self.connect()
                except Exception as reconnect_exc:
                    logger.error("Reconnect failed: %s", reconnect_exc)

            time.sleep(self._config.poll_interval_seconds)
