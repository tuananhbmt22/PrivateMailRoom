"""Microsoft Graph API email reader with OAuth2 authentication.

Uses MSAL for OAuth2 authorization code flow and Microsoft Graph API
to read emails and download attachments. Replaces IMAP for Microsoft 365.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import msal
import yaml

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@dataclass
class OAuthConfig:
    """Microsoft OAuth2 configuration."""

    client_id: str
    tenant_id: str
    client_secret: str
    authority_base: str
    scopes: list[str]
    redirect_uri: str
    token_cache_path: str
    poll_interval_seconds: int
    after_processing: str
    processed_folder: str
    max_attachment_size_mb: int

    @classmethod
    def from_yaml(cls, config_path: Path) -> OAuthConfig:
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)
        return cls(
            client_id=raw.get("client_id", ""),
            tenant_id=raw.get("tenant_id", ""),
            client_secret=raw.get("client_secret", ""),
            authority_base=raw.get("authority_base", "https://login.microsoftonline.com"),
            scopes=raw.get("scopes", ["Mail.Read", "Mail.ReadWrite", "User.Read"]),
            redirect_uri=raw.get("redirect_uri", "http://localhost:5000/auth/callback"),
            token_cache_path=raw.get("token_cache_path", ".token_cache.json"),
            poll_interval_seconds=raw.get("poll_interval_seconds", 30),
            after_processing=raw.get("after_processing", "mark_read"),
            processed_folder=raw.get("processed_folder", "Processed"),
            max_attachment_size_mb=raw.get("max_attachment_size_mb", 25),
        )

    @property
    def authority(self) -> str:
        return f"{self.authority_base}/{self.tenant_id}"

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.tenant_id and self.client_secret)

    @property
    def max_attachment_bytes(self) -> int:
        return self.max_attachment_size_mb * 1024 * 1024


def sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    return name.strip('. ') or "unnamed_file"


class GraphMailClient:
    """Microsoft Graph API client for reading emails.

    Handles OAuth2 token management via MSAL and provides methods
    to list, read, and process emails from a Microsoft 365 mailbox.
    """

    def __init__(self, config: OAuthConfig, config_dir: Path) -> None:
        self._config = config
        self._config_dir = config_dir
        self._cache = msal.SerializableTokenCache()
        self._load_token_cache()
        self._app = msal.ConfidentialClientApplication(
            config.client_id,
            authority=config.authority,
            client_credential=config.client_secret,
            token_cache=self._cache,
        )
        self._http = httpx.Client(timeout=30)

    def _load_token_cache(self) -> None:
        """Load persisted token cache from disk."""
        cache_path = self._config_dir / self._config.token_cache_path
        if cache_path.is_file():
            self._cache.deserialize(cache_path.read_text())

    def _save_token_cache(self) -> None:
        """Persist token cache to disk."""
        if self._cache.has_state_changed:
            cache_path = self._config_dir / self._config.token_cache_path
            cache_path.write_text(self._cache.serialize())

    def get_auth_url(self) -> str:
        """Generate the OAuth2 authorization URL for user login.

        Returns the URL to redirect the user to for Microsoft login.
        """
        return self._app.get_authorization_request_url(
            scopes=self._config.scopes,
            redirect_uri=self._config.redirect_uri,
        )

    def complete_auth(self, auth_code: str) -> dict[str, Any]:
        """Exchange the authorization code for tokens.

        Called after the user completes the Microsoft login and is
        redirected back with an auth code.
        """
        result = self._app.acquire_token_by_authorization_code(
            code=auth_code,
            scopes=self._config.scopes,
            redirect_uri=self._config.redirect_uri,
        )
        self._save_token_cache()

        if "error" in result:
            raise RuntimeError(f"Token acquisition failed: {result.get('error_description', result['error'])}")

        return result

    def get_access_token(self) -> str | None:
        """Get a valid access token, refreshing if needed.

        Returns None if no cached tokens exist (user needs to auth).
        """
        accounts = self._app.get_accounts()
        if not accounts:
            return None

        result = self._app.acquire_token_silent(
            scopes=self._config.scopes,
            account=accounts[0],
        )
        self._save_token_cache()

        if result and "access_token" in result:
            return result["access_token"]
        return None

    def is_authenticated(self) -> bool:
        """Check if we have valid cached tokens."""
        return self.get_access_token() is not None

    def get_user_info(self) -> dict[str, Any] | None:
        """Get the authenticated user's profile."""
        token = self.get_access_token()
        if not token:
            return None

        resp = self._http.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            return resp.json()
        return None

    def get_unread_emails(self, top: int = 50) -> list[dict[str, Any]]:
        """Fetch unread emails from the inbox."""
        token = self.get_access_token()
        if not token:
            raise RuntimeError("Not authenticated — user must sign in first")

        resp = self._http.get(
            f"{GRAPH_BASE}/me/mailFolders/inbox/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "$filter": "isRead eq false",
                "$top": top,
                "$orderby": "receivedDateTime asc",
                "$select": "id,subject,from,receivedDateTime,body,hasAttachments,ccRecipients,toRecipients",
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Graph API error: {resp.status_code} {resp.text[:300]}")

        return resp.json().get("value", [])

    def get_attachments(self, message_id: str) -> list[dict[str, Any]]:
        """Fetch attachments for a specific email."""
        token = self.get_access_token()
        if not token:
            raise RuntimeError("Not authenticated")

        resp = self._http.get(
            f"{GRAPH_BASE}/me/messages/{message_id}/attachments",
            headers={"Authorization": f"Bearer {token}"},
        )

        if resp.status_code != 200:
            return []

        return resp.json().get("value", [])

    def mark_as_read(self, message_id: str) -> None:
        """Mark an email as read."""
        token = self.get_access_token()
        if not token:
            return

        self._http.patch(
            f"{GRAPH_BASE}/me/messages/{message_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"isRead": True},
        )

    def email_to_event(
        self,
        email_data: dict[str, Any],
        receive_channel: Path,
    ) -> Path | None:
        """Convert a Graph API email into an event folder.

        Creates:
            receive_channel/{event_id}/
                email_body.txt
                attachment1.pdf
                _email_meta.json
        """
        msg_id = email_data["id"]
        received = email_data.get("receivedDateTime", "")
        subject = email_data.get("subject", "(No Subject)")
        sender = email_data.get("from", {}).get("emailAddress", {}).get("address", "unknown")
        body_content = email_data.get("body", {}).get("content", "")
        body_type = email_data.get("body", {}).get("contentType", "text")

        # Clean HTML body
        if body_type == "html":
            body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL)
            body_content = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL)
            body_content = re.sub(r'<[^>]+>', ' ', body_content)
            body_content = re.sub(r'\s+', ' ', body_content).strip()

        # Generate event ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        msg_hash = abs(hash(msg_id)) % 100000
        event_id = f"email_{timestamp}_{msg_hash:05d}"

        event_dir = receive_channel / event_id
        if event_dir.exists():
            return None

        try:
            event_dir.mkdir(parents=True)
        except OSError as exc:
            logger.error("Failed to create event dir: %s", exc)
            return None

        # Save email body
        to_addrs = ", ".join(
            r.get("emailAddress", {}).get("address", "")
            for r in email_data.get("toRecipients", [])
        )
        cc_addrs = ", ".join(
            r.get("emailAddress", {}).get("address", "")
            for r in email_data.get("ccRecipients", [])
        )

        email_text = f"Subject: {subject}\nFrom: {sender}\nTo: {to_addrs}\nDate: {received}\n"
        if cc_addrs:
            email_text += f"Cc: {cc_addrs}\n"
        email_text += f"\n{body_content}\n"

        (event_dir / "email_body.txt").write_text(email_text, encoding="utf-8")

        # Save attachments
        if email_data.get("hasAttachments"):
            attachments = self.get_attachments(msg_id)
            for att in attachments:
                if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                    continue

                filename = sanitize_filename(att.get("name", "unnamed"))
                content_bytes = att.get("contentBytes", "")

                if not content_bytes:
                    continue

                import base64
                raw_bytes = base64.b64decode(content_bytes)

                if len(raw_bytes) > self._config.max_attachment_bytes:
                    logger.warning("Skipping oversized attachment: %s", filename)
                    continue

                att_path = event_dir / filename
                counter = 1
                while att_path.exists():
                    stem = Path(filename).stem
                    ext = Path(filename).suffix
                    att_path = event_dir / f"{stem}_{counter}{ext}"
                    counter += 1

                att_path.write_bytes(raw_bytes)
                logger.info("Saved attachment: %s (%d bytes)", filename, len(raw_bytes))

        # Save metadata
        meta = {
            "message_id": msg_id,
            "subject": subject,
            "from": sender,
            "to": to_addrs,
            "received": received,
            "has_attachments": email_data.get("hasAttachments", False),
        }
        (event_dir / "_email_meta.json").write_text(json.dumps(meta, indent=2))

        # Mark as read
        if self._config.after_processing == "mark_read":
            self.mark_as_read(msg_id)

        file_count = len(list(event_dir.iterdir()))
        logger.info("Created event '%s' from email: '%s' from %s (%d files)", event_id, subject, sender, file_count)

        return event_dir

    def logout(self) -> None:
        """Clear cached tokens."""
        cache_path = self._config_dir / self._config.token_cache_path
        if cache_path.is_file():
            cache_path.unlink()
        self._cache = msal.SerializableTokenCache()

    def close(self) -> None:
        """Release resources."""
        self._http.close()
