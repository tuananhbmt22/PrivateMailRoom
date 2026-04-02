"""Kajima Mailroom Dashboard — Flask application.

LAN-only admin dashboard for monitoring document classification.
Reads folder state from the council directory and serves a real-time UI.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

import yaml
from flask import Flask, Response, jsonify, render_template, request

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.resolve()
CONFIG_DIR = BASE_DIR / "config"


def create_app(council_name: str = "Test_Council") -> Flask:
    """Create and configure the Flask dashboard application."""
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "dashboard" / "templates"),
        static_folder=str(BASE_DIR / "dashboard" / "static"),
    )
    app.config["COUNCIL_NAME"] = council_name
    app.config["COUNCIL_DIR"] = BASE_DIR / council_name

    # Load council config
    council_yaml = app.config["COUNCIL_DIR"] / "council.yaml"
    with open(council_yaml, "r") as f:
        app.config["COUNCIL_CONFIG"] = yaml.safe_load(f)

    # Load folder tree
    tree_path = CONFIG_DIR / "classification_only_tree.json"
    with open(tree_path, "r") as f:
        app.config["FOLDER_TREE"] = json.load(f)

    register_routes(app)
    return app


def load_email_config() -> dict[str, Any]:
    """Load current email config from YAML."""
    email_path = CONFIG_DIR / "email.yaml"
    if email_path.is_file():
        with open(email_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_email_config(config: dict[str, Any]) -> None:
    """Save email config to YAML."""
    email_path = CONFIG_DIR / "email.yaml"
    with open(email_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def scan_folder_counts(council_dir: Path, folder_map: dict[str, str]) -> dict[str, int]:
    """Count events in each department folder."""
    counts: dict[str, int] = {}
    for key, relative_path in folder_map.items():
        folder_path = council_dir / relative_path
        if folder_path.is_dir():
            event_dirs = [
                d for d in folder_path.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ]
            counts[key] = len(event_dirs)
        else:
            counts[key] = 0
    return counts


def scan_receive_channel(council_dir: Path) -> list[dict[str, Any]]:
    """List pending events in the receive channel with display metadata."""
    channel = council_dir / "receive_channel"
    events = []
    if channel.is_dir():
        for event_dir in sorted(channel.iterdir()):
            if event_dir.is_dir() and not event_dir.name.startswith("."):
                files = [f.name for f in event_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
                display = extract_event_display(event_dir)
                events.append({
                    "event_id": event_dir.name,
                    "file_count": len(files),
                    "files": files,
                    "_subject": display["subject"],
                    "_sender": display["sender"],
                })
    return events


def scan_event_log(council_dir: Path, folder_map: dict[str, str]) -> list[dict[str, Any]]:
    """Collect all classification receipts across all department folders."""
    log_entries: list[dict[str, Any]] = []

    for key, relative_path in folder_map.items():
        folder_path = council_dir / relative_path
        if not folder_path.is_dir():
            continue
        for event_dir in folder_path.iterdir():
            if not event_dir.is_dir() or event_dir.name.startswith("."):
                continue
            receipt_path = event_dir / "_classification.json"
            if receipt_path.is_file():
                try:
                    receipt = json.loads(receipt_path.read_text())
                    receipt["_folder_key"] = key
                    display = extract_event_display(event_dir)
                    receipt["_subject"] = display["subject"]
                    receipt["_sender"] = display["sender"]
                    log_entries.append(receipt)
                except (json.JSONDecodeError, OSError):
                    pass

    log_entries.sort(key=lambda e: e.get("classified_at", ""), reverse=True)
    return log_entries


def extract_event_display(event_dir: Path) -> dict[str, str]:
    """Extract human-friendly display info from an event folder."""
    subject = ""
    sender = ""
    received = ""

    # Try _email_meta.json first
    meta_json = event_dir / "_email_meta.json"
    if meta_json.is_file():
        try:
            meta = json.loads(meta_json.read_text())
            subject = meta.get("subject", "")
            sender = meta.get("from", "")
            received = meta.get("received", "")
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: parse email_body.txt
    if not subject:
        body_path = event_dir / "email_body.txt"
        if body_path.is_file():
            try:
                for line in body_path.read_text(errors="replace").split("\n")[:6]:
                    if line.startswith("Subject:"):
                        subject = line[8:].strip()
                    elif line.startswith("From:"):
                        sender = line[5:].strip()
                    elif line.startswith("Date:"):
                        received = line[5:].strip()
            except OSError:
                pass

    return {
        "subject": subject or "(No Subject)",
        "sender": sender,
        "received": received,
    }


def scan_folder_events(council_dir: Path, folder_path: str) -> list[dict[str, Any]]:
    """List all events inside a specific department folder."""
    full_path = council_dir / folder_path
    events = []
    if not full_path.is_dir():
        return events

    for event_dir in sorted(full_path.iterdir()):
        if not event_dir.is_dir() or event_dir.name.startswith("."):
            continue
        files = [f.name for f in event_dir.iterdir() if f.is_file() and not f.name.startswith("_") and not f.name.startswith(".")]
        receipt_path = event_dir / "_classification.json"
        receipt = None
        if receipt_path.is_file():
            try:
                receipt = json.loads(receipt_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        # Merge skill result into receipt if available
        skill_path = event_dir / "_skill_result.json"
        if receipt and skill_path.is_file():
            try:
                skill_data = json.loads(skill_path.read_text())
                receipt["skill_matched"] = skill_data.get("skill_id")
                receipt["skill_name"] = skill_data.get("skill_name")
                receipt["skill_outcome"] = skill_data.get("outcome")
                receipt["skill_analysis"] = skill_data.get("analysis")
                receipt["skill_metadata"] = skill_data.get("metadata")
            except (json.JSONDecodeError, OSError):
                pass

        display = extract_event_display(event_dir)

        events.append({
            "event_id": event_dir.name,
            "subject": display["subject"],
            "sender": display["sender"],
            "received": display["received"],
            "file_count": len(files),
            "files": files,
            "receipt": receipt,
        })
    return events


def _parse_llm_json(raw: str, expect_array: bool = False) -> Any:
    """Parse JSON from an LLM response, handling fences, trailing text, and reasoning_content.

    Args:
        raw: Raw LLM response content.
        expect_array: If True, look for [...] array instead of {...} object.

    Returns:
        Parsed dict/list, or None if parsing fails.
    """
    import re as _re

    content = raw.strip()
    if not content:
        return None

    # Strip markdown code fences
    if "```" in content:
        open_char = r"\[" if expect_array else r"\{"
        close_char = r"\]" if expect_array else r"\}"
        fence_match = _re.search(
            r"```(?:json)?\s*(" + open_char + r"[\s\S]*?" + close_char + r")\s*```",
            content,
        )
        if fence_match:
            content = fence_match.group(1)

    # Extract first complete JSON object/array by brace/bracket counting
    open_ch = '[' if expect_array else '{'
    close_ch = ']' if expect_array else '}'
    depth = 0
    start_idx = -1
    for i, ch in enumerate(content):
        if ch == open_ch:
            if start_idx == -1:
                start_idx = i
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0 and start_idx >= 0:
                try:
                    return json.loads(content[start_idx:i + 1])
                except json.JSONDecodeError:
                    pass
                break

    # Last resort: try parsing the whole thing
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _save_last_llm_response(council_dir: Path, folder_key: str, endpoint: str, content: str) -> None:
    """Save the last LLM response for debug and retry purposes."""
    try:
        folder_map_dir = council_dir / "departments" / folder_key
        if not folder_map_dir.is_dir():
            folder_map_dir = council_dir / "_forge_temp"
        folder_map_dir.mkdir(parents=True, exist_ok=True)
        response_path = folder_map_dir / "_last_llm_response.json"
        response_path.write_text(json.dumps({
            "endpoint": endpoint,
            "timestamp": datetime.now().isoformat(),
            "content": content,
        }, indent=2))
    except OSError:
        pass


def _parse_eml_dev_mode(eml_bytes: bytes, output_dir: Path) -> Path | None:
    """Parse .eml in dev mode: body = JSON schema, attachments = raw files.

    In dev mode, the email body is expected to be a raw JSON string
    (e.g., NSW Planning Portal DA data). It gets saved as _source_schema.json.
    Attachments are saved as-is alongside it.
    """
    import email as _email
    import email.policy as _policy
    import re as _re
    from datetime import datetime as _dt

    try:
        msg = _email.message_from_bytes(eml_bytes, policy=_policy.default)
    except Exception as exc:
        logger.error("Dev mode: failed to parse .eml: %s", exc)
        return None

    subject = msg.get("Subject", "event")
    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
    safe_subject = _re.sub(r'[^a-zA-Z0-9_-]', '_', subject.lower().strip())[:40]
    event_id = f"forge_{safe_subject}_{timestamp}"

    event_dir = output_dir / event_id
    if event_dir.exists():
        return None
    event_dir.mkdir(parents=True)

    # Extract body — try to parse as JSON
    body_part = msg.get_body(preferencelist=('plain', 'html'))
    body_text = ""
    if body_part:
        content = body_part.get_content()
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        # Strip HTML if needed
        if body_part.get_content_type() == 'text/html':
            content = _re.sub(r'<[^>]+>', ' ', content)
            content = _re.sub(r'\s+', ' ', content).strip()
        body_text = content.strip()

    # Try to parse body as JSON
    try:
        schema_data = json.loads(body_text)
        schema_path = event_dir / "_source_schema.json"
        schema_path.write_text(json.dumps(schema_data, indent=2, ensure_ascii=False))
        logger.info("Dev mode: saved source schema (%d keys) to %s", len(schema_data), schema_path)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — save as regular email body
        body_path = event_dir / "email_body.txt"
        body_path.write_text(f"Subject: {subject}\n\n{body_text}", encoding="utf-8")
        logger.warning("Dev mode: body is not valid JSON, saved as email_body.txt")

    # Save attachments
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if "attachment" not in cd and "inline" not in cd:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        filename = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename).strip('. ')
        payload = part.get_payload(decode=True)
        if not payload:
            continue

        att_path = event_dir / filename
        counter = 1
        while att_path.exists():
            stem = Path(filename).stem
            ext = Path(filename).suffix
            att_path = event_dir / f"{stem}_{counter}{ext}"
            counter += 1
        att_path.write_bytes(payload)

    # Save email headers
    meta_path = event_dir / "_email_meta.txt"
    headers = "\n".join(f"{k}: {v}" for k, v in msg.items())
    meta_path.write_text(headers, encoding="utf-8")

    logger.info("Dev mode: parsed event '%s' — %d files", event_id, len(list(event_dir.iterdir())))
    return event_dir


def register_routes(app: Flask) -> None:
    """Register all dashboard routes."""

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            council_name=app.config["COUNCIL_NAME"],
        )

    @app.route("/api/state")
    def api_state():
        """Full dashboard state — folder counts, pending, event log."""
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]
        tree = app.config["FOLDER_TREE"]

        counts = scan_folder_counts(council_dir, folder_map)
        pending = scan_receive_channel(council_dir)
        event_log = scan_event_log(council_dir, folder_map)

        folders = []
        for key in tree.get("evaluation_priority", []):
            folder_def = tree["folders"].get(key, {})
            folders.append({
                "key": key,
                "name": folder_def.get("name", key),
                "description": folder_def.get("description", ""),
                "count": counts.get(key, 0),
                "status": folder_def.get("status", "active"),
            })

        # Add undetermined
        folders.append({
            "key": "undetermined",
            "name": "Undetermined",
            "description": tree["folders"].get("undetermined", {}).get("description", ""),
            "count": counts.get("undetermined", 0),
            "status": "active",
        })

        # Add draft folders (not in evaluation_priority)
        active_keys = set(tree.get("evaluation_priority", []))
        active_keys.add("undetermined")
        active_keys.add("junk")
        for key, folder_def in tree.get("folders", {}).items():
            if key not in active_keys and folder_def.get("status") == "draft":
                folders.append({
                    "key": key,
                    "name": folder_def.get("name", key),
                    "description": folder_def.get("description", ""),
                    "count": counts.get(key, 0),
                    "status": "draft",
                })

        return jsonify({
            "council": app.config["COUNCIL_NAME"],
            "timestamp": datetime.now().isoformat(),
            "folders": folders,
            "pending": pending,
            "pending_count": len(pending),
            "event_log": event_log[:50],
            "total_classified": sum(counts.values()),
        })

    @app.route("/api/folder/<folder_key>")
    def api_folder(folder_key: str):
        """Events inside a specific folder."""
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]
        tree = app.config["FOLDER_TREE"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        folder_def = tree["folders"].get(folder_key, {})
        events = scan_folder_events(council_dir, relative_path)

        return jsonify({
            "key": folder_key,
            "name": folder_def.get("name", folder_key),
            "description": folder_def.get("description", ""),
            "events": events,
            "count": len(events),
        })

    @app.route("/api/event/<folder_key>/<event_id>")
    def api_event(folder_key: str, event_id: str):
        """Full details of a specific event including AI receipt."""
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        event_dir = council_dir / relative_path / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        files = []
        receipt = None
        for f in sorted(event_dir.iterdir()):
            if not f.is_file():
                continue
            if f.name == "_classification.json":
                try:
                    receipt = json.loads(f.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            else:
                files.append({
                    "filename": f.name,
                    "size_bytes": f.stat().st_size,
                })

        return jsonify({
            "event_id": event_id,
            "folder_key": folder_key,
            "files": files,
            "receipt": receipt,
        })

    @app.route("/api/stream")
    def api_stream():
        """SSE endpoint for real-time updates."""
        def generate() -> Generator[str, None, None]:
            last_state = ""
            while True:
                council_dir = app.config["COUNCIL_DIR"]
                folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]
                counts = scan_folder_counts(council_dir, folder_map)
                pending = scan_receive_channel(council_dir)
                state_hash = json.dumps({"counts": counts, "pending_count": len(pending)}, sort_keys=True)

                if state_hash != last_state:
                    last_state = state_hash
                    yield f"data: {json.dumps({'type': 'update', 'counts': counts, 'pending_count': len(pending)})}\n\n"

                time.sleep(3)

        return Response(generate(), mimetype="text/event-stream")

    @app.route("/api/email/status")
    def api_email_status():
        """Check if email is configured and connected (OAuth2 or IMAP)."""
        # Check IMAP first
        imap_config = load_email_config()
        if imap_config.get("provider") and imap_config.get("username") and imap_config.get("password"):
            return jsonify({
                "configured": True,
                "authenticated": True,
                "provider": imap_config["provider"],
                "username": imap_config["username"],
                "client_id": "",
                "tenant_id": "",
            })

        # Check OAuth2
        from engine.graph_mail import OAuthConfig, GraphMailClient

        oauth_path = CONFIG_DIR / "oauth.yaml"
        if not oauth_path.is_file():
            return jsonify({"configured": False, "authenticated": False, "provider": "", "username": "", "client_id": "", "tenant_id": ""})

        oauth_config = OAuthConfig.from_yaml(oauth_path)
        if not oauth_config.is_configured:
            return jsonify({
                "configured": False,
                "authenticated": False,
                "provider": "microsoft",
                "username": "",
                "client_id": oauth_config.client_id,
                "tenant_id": oauth_config.tenant_id,
            })

        client = GraphMailClient(oauth_config, CONFIG_DIR)
        authenticated = client.is_authenticated()
        username = ""

        if authenticated:
            user_info = client.get_user_info()
            if user_info:
                username = user_info.get("mail", user_info.get("userPrincipalName", ""))

        client.close()

        return jsonify({
            "configured": True,
            "authenticated": authenticated,
            "username": username,
            "client_id": oauth_config.client_id,
            "tenant_id": oauth_config.tenant_id,
        })

    @app.route("/api/email/save-config", methods=["POST"])
    def api_email_save_config():
        """Save OAuth2 Azure app credentials from the dashboard UI."""
        data = request.get_json()
        client_id = data.get("client_id", "").strip()
        tenant_id = data.get("tenant_id", "").strip()
        client_secret = data.get("client_secret", "").strip()

        if not client_id or not tenant_id or not client_secret:
            return jsonify({"success": False, "error": "All three fields are required"}), 400

        # Load existing config or create new
        oauth_path = CONFIG_DIR / "oauth.yaml"
        if oauth_path.is_file():
            with open(oauth_path, "r") as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}

        config["client_id"] = client_id
        config["tenant_id"] = tenant_id
        config["client_secret"] = client_secret

        # Ensure defaults exist
        config.setdefault("authority_base", "https://login.microsoftonline.com")
        config.setdefault("scopes", ["Mail.Read", "Mail.ReadWrite", "User.Read"])
        config.setdefault("redirect_uri", "http://localhost:5000/auth/callback")
        config.setdefault("token_cache_path", ".token_cache.json")
        config.setdefault("poll_interval_seconds", 30)
        config.setdefault("after_processing", "mark_read")
        config.setdefault("processed_folder", "Processed")
        config.setdefault("max_attachment_size_mb", 25)

        with open(oauth_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return jsonify({"success": True, "message": "OAuth configuration saved"})

    @app.route("/auth/login")
    def auth_login():
        """Redirect user to Microsoft login page."""
        from engine.graph_mail import OAuthConfig, GraphMailClient

        oauth_config = OAuthConfig.from_yaml(CONFIG_DIR / "oauth.yaml")
        if not oauth_config.is_configured:
            return "OAuth not configured. Fill in config/oauth.yaml first.", 400

        client = GraphMailClient(oauth_config, CONFIG_DIR)
        auth_url = client.get_auth_url()
        client.close()

        from flask import redirect
        return redirect(auth_url)

    @app.route("/auth/callback")
    def auth_callback():
        """Handle OAuth2 callback from Microsoft."""
        from engine.graph_mail import OAuthConfig, GraphMailClient

        auth_code = request.args.get("code")
        error = request.args.get("error")

        if error:
            return f"""
            <html><body style="background:#0f1117;color:#e1e4ed;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh">
            <div style="text-align:center">
                <h2 style="color:#ef4444">Authentication Failed</h2>
                <p>{request.args.get('error_description', error)}</p>
                <a href="/" style="color:#3b82f6">Back to Dashboard</a>
            </div></body></html>
            """

        if not auth_code:
            return "No authorization code received", 400

        oauth_config = OAuthConfig.from_yaml(CONFIG_DIR / "oauth.yaml")
        client = GraphMailClient(oauth_config, CONFIG_DIR)

        try:
            client.complete_auth(auth_code)
            user_info = client.get_user_info()
            username = user_info.get("mail", "Unknown") if user_info else "Unknown"
            client.close()

            return f"""
            <html><body style="background:#0f1117;color:#e1e4ed;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh">
            <div style="text-align:center">
                <h2 style="color:#22c55e">✓ Connected</h2>
                <p>Signed in as <strong>{username}</strong></p>
                <p style="color:#7a7f94">Mail service ready for inference.</p>
                <script>setTimeout(function(){{ window.location.href = '/'; }}, 2000);</script>
            </div></body></html>
            """
        except Exception as exc:
            client.close()
            return f"""
            <html><body style="background:#0f1117;color:#e1e4ed;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh">
            <div style="text-align:center">
                <h2 style="color:#ef4444">Token Error</h2>
                <p>{exc}</p>
                <a href="/" style="color:#3b82f6">Back to Dashboard</a>
            </div></body></html>
            """

    @app.route("/api/email/connect", methods=["POST"])
    def api_email_connect():
        """Initiate OAuth2 sign-in — returns the auth URL."""
        from engine.graph_mail import OAuthConfig, GraphMailClient

        oauth_path = CONFIG_DIR / "oauth.yaml"
        if not oauth_path.is_file():
            return jsonify({"success": False, "error": "OAuth config not found. Create config/oauth.yaml"}), 400

        oauth_config = OAuthConfig.from_yaml(oauth_path)
        if not oauth_config.is_configured:
            return jsonify({"success": False, "error": "Fill in client_id, tenant_id, and client_secret in config/oauth.yaml"}), 400

        client = GraphMailClient(oauth_config, CONFIG_DIR)
        auth_url = client.get_auth_url()
        client.close()

        return jsonify({"success": True, "auth_url": auth_url})

    @app.route("/api/email/disconnect", methods=["POST"])
    def api_email_disconnect():
        """Clear all email credentials (OAuth + IMAP)."""
        # Clear IMAP
        config = load_email_config()
        config["username"] = ""
        config["password"] = ""
        config.pop("provider", None)
        save_email_config(config)

        # Clear OAuth tokens
        from engine.graph_mail import OAuthConfig, GraphMailClient
        oauth_path = CONFIG_DIR / "oauth.yaml"
        if oauth_path.is_file():
            try:
                oauth_config = OAuthConfig.from_yaml(oauth_path)
                if oauth_config.is_configured:
                    client = GraphMailClient(oauth_config, CONFIG_DIR)
                    client.logout()
                    client.close()
            except Exception:
                pass

        return jsonify({"success": True, "message": "Signed out"})

    @app.route("/api/email/imap-connect", methods=["POST"])
    def api_email_imap_connect():
        """Test IMAP connection and save credentials if successful."""
        import imaplib

        data = request.get_json()
        imap_host = data.get("imap_host", "imap.gmail.com")
        imap_port = int(data.get("imap_port", 993))
        use_ssl = data.get("use_ssl", True)
        username = data.get("username", "")
        password = data.get("password", "")
        provider = data.get("provider", "gmail")

        if not username or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400

        try:
            if use_ssl:
                conn = imaplib.IMAP4_SSL(imap_host, imap_port)
            else:
                conn = imaplib.IMAP4(imap_host, imap_port)

            conn.login(username, password)

            status, _ = conn.select("INBOX")
            if status != "OK":
                conn.logout()
                return jsonify({"success": False, "error": "Could not access INBOX"}), 400

            _, msg_ids = conn.search(None, "(UNSEEN)")
            unread_count = len(msg_ids[0].split()) if msg_ids[0] else 0

            conn.logout()

        except imaplib.IMAP4.error as exc:
            return jsonify({"success": False, "error": f"Authentication failed: {exc}"}), 401
        except (OSError, ConnectionError) as exc:
            return jsonify({"success": False, "error": f"Connection failed: {exc}"}), 502
        except Exception as exc:
            return jsonify({"success": False, "error": f"Unexpected error: {exc}"}), 500

        # Save to email.yaml
        config = load_email_config()
        config["provider"] = provider
        config["imap_host"] = imap_host
        config["imap_port"] = imap_port
        config["use_ssl"] = use_ssl
        config["username"] = username
        config["password"] = password
        save_email_config(config)

        return jsonify({
            "success": True,
            "message": f"Connected to {imap_host} as {username}",
            "unread_count": unread_count,
        })

    @app.route("/api/email/imap-disconnect", methods=["POST"])
    def api_email_imap_disconnect():
        """Clear IMAP credentials."""
        config = load_email_config()
        config["username"] = ""
        config["password"] = ""
        config.pop("provider", None)
        save_email_config(config)
        return jsonify({"success": True, "message": "Disconnected"})


    @app.route("/api/email/poll", methods=["POST"])
    def api_email_poll():
        """Poll the inbox for new emails and convert them to events."""
        council_dir = app.config["COUNCIL_DIR"]
        receive_channel = council_dir / "receive_channel"

        config = load_email_config()
        provider = config.get("provider", "")

        if provider in ("gmail", "imap") and config.get("username") and config.get("password"):
            # IMAP-based ingestion
            from engine.email_ingester import EmailConfig, EmailIngester

            email_config = EmailConfig.from_yaml(CONFIG_DIR / "email.yaml")
            ingester = EmailIngester(email_config, receive_channel)

            try:
                ingester.connect()
                events = ingester.poll_once()
                ingester.disconnect()

                event_details = []
                for event_path in events:
                    display = extract_event_display(event_path)
                    event_details.append({
                        "event_id": event_path.name,
                        "subject": display["subject"],
                    })

                return jsonify({
                    "success": True,
                    "events_created": len(events),
                    "event_ids": [e.name for e in events],
                    "event_details": event_details,
                })
            except Exception as exc:
                return jsonify({"success": False, "error": str(exc)}), 500

        else:
            # Check OAuth2
            from engine.graph_mail import OAuthConfig, GraphMailClient

            oauth_path = CONFIG_DIR / "oauth.yaml"
            if not oauth_path.is_file():
                return jsonify({"success": False, "error": "No email provider configured"}), 400

            oauth_config = OAuthConfig.from_yaml(oauth_path)
            if not oauth_config.is_configured:
                return jsonify({"success": False, "error": "OAuth not configured"}), 400

            client = GraphMailClient(oauth_config, CONFIG_DIR)
            if not client.is_authenticated():
                client.close()
                return jsonify({"success": False, "error": "Not signed in to Microsoft"}), 401

            try:
                emails = client.get_unread_emails(top=20)
                created_events = []

                for email_data in emails:
                    event_path = client.email_to_event(email_data, receive_channel)
                    if event_path:
                        created_events.append(event_path.name)

                client.close()

                event_details = []
                for eid in created_events:
                    event_dir = receive_channel / eid
                    if event_dir.is_dir():
                        display = extract_event_display(event_dir)
                        event_details.append({
                            "event_id": eid,
                            "subject": display["subject"],
                        })

                return jsonify({
                    "success": True,
                    "events_created": len(created_events),
                    "event_ids": created_events,
                    "event_details": event_details,
                })
            except Exception as exc:
                client.close()
                return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/classify", methods=["POST"])
    def api_classify():
        """Run classification on all pending events in receive_channel.

        If skills are enabled, runs the 3-call pipeline:
        Call 1: Skill matching → Call 2: Scroll execution → Call 3: Classification
        If skills are off, runs Call 3 only.
        """
        council_dir = app.config["COUNCIL_DIR"]
        receive_channel = council_dir / "receive_channel"
        folder_tree_path = CONFIG_DIR / "classification_only_tree.json"

        from engine.classifier import ClassificationEngine, load_folder_tree, read_event, build_user_message
        from engine.dispatcher import CouncilConfig, Dispatcher

        # Check if skills are enabled
        settings_path = CONFIG_DIR / "dashboard_settings.json"
        skills_enabled = False
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text())
                skills_enabled = settings.get("skills_enabled", False)
            except (json.JSONDecodeError, OSError):
                pass

        events = [
            d for d in receive_channel.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        if not events:
            return jsonify({"success": True, "classified": 0, "results": []})

        from engine.llm import LLMConfig, LocalLLM
        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)

        engine = ClassificationEngine(
            llm_config_path=CONFIG_DIR / "llm.yaml",
            system_prompt_path=CONFIG_DIR / "classification_only_prompt.md",
            folder_tree_path=folder_tree_path,
        )

        folder_tree = load_folder_tree(folder_tree_path)
        council_config = CouncilConfig.from_yaml(council_dir)
        dispatcher = Dispatcher(council_config, folder_tree)

        results = []
        for event_dir in sorted(events):
            skill_result = None

            # Skills pipeline (Call 1 + Call 2)
            if skills_enabled:
                from engine.skill_runner import run_skill_pipeline, save_skill_result
                skills_dir = BASE_DIR / "skills"

                event = read_event(event_dir)
                event_text = build_user_message(event)

                skill_result = run_skill_pipeline(llm, skills_dir, event_text)
                if skill_result:
                    save_skill_result(event_dir, skill_result)
                    logger.info("Skill result saved for %s: %s/%s", event_dir.name, skill_result.get("skill_id"), skill_result.get("outcome"))

            # Call 3: Classification (with skill enrichment if available)
            classification = engine.classify_event_dir(event_dir)
            dispatch_result = None

            if classification.success:
                dispatch_result = dispatcher.dispatch(classification, event_dir)

            result_entry = {
                "event_id": classification.event_id,
                "outcome": classification.outcome,
                "confidence": round(classification.confidence, 2),
                "reasoning": classification.reasoning,
                "moved": dispatch_result.moved if dispatch_result else False,
                "skill_matched": skill_result.get("skill_id") if skill_result else None,
                "skill_request_type": skill_result.get("request_type") if skill_result else None,
                "skill_outcome": skill_result.get("outcome") if skill_result else None,
                "skill_analysis": skill_result.get("analysis") if skill_result else None,
                "skill_metadata": skill_result.get("metadata") if skill_result else None,
                "skill_confidence": skill_result.get("confidence") if skill_result else None,
                "skill_response_key": skill_result.get("response_template_key") if skill_result else None,
            }
            results.append(result_entry)

        engine.close()
        llm.close()

        classified_count = sum(1 for r in results if r["outcome"] != "Undetermined")
        return jsonify({
            "success": True,
            "classified": classified_count,
            "undetermined": len(results) - classified_count,
            "skills_enabled": skills_enabled,
            "results": results,
        })

    @app.route("/api/settings")
    def api_settings():
        """Get current settings."""
        config = load_email_config()
        since_date = config.get("since_date", "")

        # Convert IMAP date format (30-Mar-2026) to ISO (2026-03-30) for the date input
        iso_date = ""
        if since_date:
            try:
                from datetime import datetime as dt
                parsed = dt.strptime(since_date, "%d-%b-%Y")
                iso_date = parsed.strftime("%Y-%m-%d")
            except ValueError:
                iso_date = since_date

        settings_path = CONFIG_DIR / "dashboard_settings.json"
        skills_enabled = False
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text())
                skills_enabled = settings.get("skills_enabled", False)
            except (json.JSONDecodeError, OSError):
                pass

        return jsonify({"since_date": iso_date, "skills_enabled": skills_enabled})

    @app.route("/api/settings", methods=["POST"])
    def api_settings_save():
        """Save settings."""
        data = request.get_json()
        iso_date = data.get("since_date", "")
        skills_enabled = data.get("skills_enabled", False)

        # Convert ISO to IMAP date
        imap_date = ""
        if iso_date:
            try:
                from datetime import datetime as dt
                parsed = dt.strptime(iso_date, "%Y-%m-%d")
                imap_date = parsed.strftime("%d-%b-%Y")
            except ValueError:
                imap_date = iso_date

        config = load_email_config()
        config["since_date"] = imap_date
        save_email_config(config)

        # Save skills toggle
        settings_path = CONFIG_DIR / "dashboard_settings.json"
        settings = {}
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        settings["skills_enabled"] = skills_enabled
        settings_path.write_text(json.dumps(settings, indent=2))

        return jsonify({"success": True, "since_date": imap_date, "skills_enabled": skills_enabled})

    @app.route("/api/event/<folder_key>/<event_id>/history")
    def api_event_history(folder_key: str, event_id: str):
        """Get the full movement chain for an event."""
        from engine.history import load_history

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        event_dir = council_dir / relative_path / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        history = load_history(event_dir)
        return jsonify(history.to_dict())

    @app.route("/api/event/<folder_key>/<event_id>/reverse", methods=["POST"])
    def api_event_reverse(folder_key: str, event_id: str):
        """Reverse an event to its previous location."""
        from engine.history import load_history, move_event, append_training_log

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        event_dir = council_dir / relative_path / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        history = load_history(event_dir)

        if history.previous_location is None:
            return jsonify({"error": "No previous location to reverse to"}), 400

        # Get correction data from request
        data = request.get_json() or {}
        correction = data.get("correction")
        staff_name = data.get("staff_name", "anonymous")

        # Find the destination folder path
        prev_key = history.previous_location
        if prev_key == "receive_channel":
            destination_base = council_dir / "receive_channel"
        else:
            dest_path = folder_map.get(prev_key)
            if not dest_path:
                return jsonify({"error": f"Cannot find folder for key: {prev_key}"}), 400
            destination_base = council_dir / dest_path

        # Log training data if AI was wrong
        if correction and correction.get("correction_type") == "ai_wrong":
            append_training_log(council_dir, event_id, correction, history, event_dir)

        # Perform the move
        new_path = move_event(
            event_dir=event_dir,
            destination_base=destination_base,
            history=history,
            action="reversed",
            actor=f"staff:{staff_name}",
            reason=f"Reversed to {prev_key} (was {folder_key})",
            correction=correction,
        )

        if new_path:
            return jsonify({
                "success": True,
                "event_id": event_id,
                "from": folder_key,
                "to": prev_key,
                "new_path": str(new_path),
            })
        else:
            return jsonify({"error": "Failed to move event"}), 500

    @app.route("/api/event/<folder_key>/<event_id>/redirect", methods=["POST"])
    def api_event_redirect(folder_key: str, event_id: str):
        """Redirect an event to a different folder."""
        from engine.history import load_history, move_event, append_training_log

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        event_dir = council_dir / relative_path / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        data = request.get_json() or {}
        target_key = data.get("target_folder")
        correction = data.get("correction")
        staff_name = data.get("staff_name", "anonymous")

        if not target_key:
            return jsonify({"error": "target_folder is required"}), 400

        if target_key == folder_key:
            return jsonify({"error": "Cannot redirect to the same folder"}), 400

        dest_path = folder_map.get(target_key)
        if not dest_path:
            return jsonify({"error": f"Unknown target folder: {target_key}"}), 400

        destination_base = council_dir / dest_path
        history = load_history(event_dir)

        # Log training data if AI was wrong
        if correction and correction.get("correction_type") == "ai_wrong":
            append_training_log(council_dir, event_id, correction, history, event_dir)

        tree = app.config["FOLDER_TREE"]
        target_name = tree["folders"].get(target_key, {}).get("name", target_key)

        new_path = move_event(
            event_dir=event_dir,
            destination_base=destination_base,
            history=history,
            action="redirected",
            actor=f"staff:{staff_name}",
            reason=f"Manual redirect to {target_name}",
            correction=correction,
        )

        if new_path:
            return jsonify({
                "success": True,
                "event_id": event_id,
                "from": folder_key,
                "to": target_key,
                "new_path": str(new_path),
            })
        else:
            return jsonify({"error": "Failed to move event"}), 500

    @app.route("/api/junk/patterns")
    def api_junk_patterns():
        """List all junk fingerprints."""
        from engine.junk import load_junk_patterns
        council_dir = app.config["COUNCIL_DIR"]
        patterns = load_junk_patterns(council_dir)
        return jsonify({
            "count": len(patterns),
            "patterns": [p.to_dict() for p in patterns],
        })

    @app.route("/api/event/<folder_key>/<event_id>/confirm-junk", methods=["POST"])
    def api_confirm_junk(folder_key: str, event_id: str):
        """Confirm an event as junk and optionally create a fingerprint."""
        from engine.junk import (
            load_junk_patterns, save_junk_patterns,
            create_fingerprint_from_event,
        )
        from engine.history import load_history, move_event

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        event_dir = council_dir / relative_path / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        data = request.get_json() or {}
        junk_type = data.get("junk_type", "spam")
        staff_name = data.get("staff_name", "admin")
        never_show_again = data.get("never_show_again", False)

        # Move to junk folder
        junk_path = folder_map.get("junk")
        if not junk_path:
            return jsonify({"error": "Junk folder not configured"}), 400

        destination_base = council_dir / junk_path
        history = load_history(event_dir)

        # Create fingerprint before moving (need access to files)
        fingerprint = None
        if never_show_again:
            fingerprint = create_fingerprint_from_event(
                event_dir, junk_type, staff_name, never_show_again
            )
            patterns = load_junk_patterns(council_dir)
            patterns.append(fingerprint)
            save_junk_patterns(council_dir, patterns)

        new_path = move_event(
            event_dir=event_dir,
            destination_base=destination_base,
            history=history,
            action="junked",
            actor=f"staff:{staff_name}",
            reason=f"Confirmed as junk: {junk_type}" + (" (fingerprinted)" if never_show_again else ""),
            correction={
                "correction_type": "junk_confirmed",
                "junk_type": junk_type,
                "never_show_again": never_show_again,
                "fingerprint_id": fingerprint.id if fingerprint else None,
            },
        )

        if new_path:
            return jsonify({
                "success": True,
                "event_id": event_id,
                "to": "junk",
                "fingerprint_created": fingerprint is not None,
                "fingerprint_id": fingerprint.id if fingerprint else None,
            })
        else:
            return jsonify({"error": "Failed to move event to junk"}), 500

    @app.route("/api/event/<folder_key>/<event_id>/not-junk", methods=["POST"])
    def api_not_junk(folder_key: str, event_id: str):
        """Mark a junk event as not-junk and reassign it."""
        from engine.history import load_history, move_event

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        event_dir = council_dir / relative_path / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        data = request.get_json() or {}
        target_key = data.get("target_folder", "receive_channel")

        if target_key == "receive_channel":
            destination_base = council_dir / "receive_channel"
        else:
            dest_path = folder_map.get(target_key)
            if not dest_path:
                return jsonify({"error": f"Unknown folder: {target_key}"}), 400
            destination_base = council_dir / dest_path

        history = load_history(event_dir)

        new_path = move_event(
            event_dir=event_dir,
            destination_base=destination_base,
            history=history,
            action="unjunked",
            actor=f"staff:{data.get('staff_name', 'admin')}",
            reason=f"Marked as not junk, moved to {target_key}",
        )

        if new_path:
            return jsonify({
                "success": True,
                "event_id": event_id,
                "to": target_key,
            })
        else:
            return jsonify({"error": "Failed to move event"}), 500

    @app.route("/api/layout/templates")
    def api_layout_templates():
        """List all saved layout templates."""
        council_dir = app.config["COUNCIL_DIR"]
        templates_path = council_dir / "_layout_templates.json"
        if templates_path.is_file():
            try:
                data = json.loads(templates_path.read_text())
                return jsonify(data)
            except (json.JSONDecodeError, OSError):
                pass
        return jsonify({"active": "alphabetical", "templates": {}})

    @app.route("/api/layout/save", methods=["POST"])
    def api_layout_save():
        """Save a layout template."""
        council_dir = app.config["COUNCIL_DIR"]
        templates_path = council_dir / "_layout_templates.json"

        data = request.get_json() or {}
        template_name = data.get("name", "").strip()
        folder_order = data.get("folder_order", [])
        override = data.get("override", False)

        if not template_name:
            return jsonify({"error": "Template name is required"}), 400

        # Load existing
        existing = {"active": "alphabetical", "templates": {}}
        if templates_path.is_file():
            try:
                existing = json.loads(templates_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        if not override and template_name in existing.get("templates", {}):
            return jsonify({"error": f"Template '{template_name}' already exists"}), 409

        existing.setdefault("templates", {})[template_name] = {
            "folder_order": folder_order,
            "created_at": datetime.now().isoformat(),
        }
        existing["active"] = template_name

        templates_path.write_text(json.dumps(existing, indent=2))
        return jsonify({"success": True, "active": template_name})

    @app.route("/api/layout/apply", methods=["POST"])
    def api_layout_apply():
        """Apply a saved template or reset to alphabetical."""
        council_dir = app.config["COUNCIL_DIR"]
        templates_path = council_dir / "_layout_templates.json"

        data = request.get_json() or {}
        template_name = data.get("name", "alphabetical")

        existing = {"active": "alphabetical", "templates": {}}
        if templates_path.is_file():
            try:
                existing = json.loads(templates_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        if template_name != "alphabetical" and template_name not in existing.get("templates", {}):
            return jsonify({"error": f"Template '{template_name}' not found"}), 404

        existing["active"] = template_name
        templates_path.write_text(json.dumps(existing, indent=2))

        folder_order = []
        if template_name != "alphabetical":
            folder_order = existing["templates"][template_name].get("folder_order", [])

        return jsonify({"success": True, "active": template_name, "folder_order": folder_order})

    @app.route("/api/layout/delete", methods=["POST"])
    def api_layout_delete():
        """Delete a saved template."""
        council_dir = app.config["COUNCIL_DIR"]
        templates_path = council_dir / "_layout_templates.json"

        data = request.get_json() or {}
        template_name = data.get("name", "")

        if not template_name or template_name == "alphabetical":
            return jsonify({"error": "Cannot delete"}), 400

        existing = {"active": "alphabetical", "templates": {}}
        if templates_path.is_file():
            try:
                existing = json.loads(templates_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        existing.get("templates", {}).pop(template_name, None)
        if existing.get("active") == template_name:
            existing["active"] = "alphabetical"

        templates_path.write_text(json.dumps(existing, indent=2))
        return jsonify({"success": True})

    @app.route("/api/event/<folder_key>/<event_id>/draft-reply", methods=["POST"])
    def api_draft_reply(folder_key: str, event_id: str):
        """Generate a draft reply using the department skill."""
        from engine.skill_runner import load_skill, analyse_with_skill, draft_reply
        from engine.llm import LLMConfig, LocalLLM

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Unknown folder"}), 404

        event_dir = council_dir / relative_path / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        # Read email body
        email_text = ""
        body_path = event_dir / "email_body.txt"
        if body_path.is_file():
            email_text = body_path.read_text(encoding="utf-8", errors="replace")
            if len(email_text) > 8000:
                email_text = email_text[:8000]

        if not email_text:
            return jsonify({"error": "No email content found"}), 400

        # Load skill for this department
        skills_dir = BASE_DIR / "skills"
        skill_content = load_skill(skills_dir, folder_key)

        if not skill_content:
            return jsonify({"error": f"No skill file found for department: {folder_key}. Draft reply requires a skill."}), 404

        # Init LLM
        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)

        try:
            # Step 1: Analyse with skill
            analysis = analyse_with_skill(llm, skill_content, email_text)

            # Step 2: Draft reply
            reply_text = draft_reply(llm, skill_content, email_text, analysis)

            llm.close()

            return jsonify({
                "success": True,
                "analysis": analysis,
                "draft_reply": reply_text,
                "department": folder_key,
            })

        except Exception as exc:
            llm.close()
            return jsonify({"error": f"Draft generation failed: {exc}"}), 500

    @app.route("/api/pipeline/match", methods=["POST"])
    def api_pipeline_match():
        """Call 1: Skill matching for a single event."""
        from engine.skill_runner import call1_match_skill, load_skills_list
        from engine.llm import LLMConfig, LocalLLM
        from engine.classifier import read_event, build_user_message

        data = request.get_json() or {}
        event_id = data.get("event_id")
        council_dir = app.config["COUNCIL_DIR"]
        receive_channel = council_dir / "receive_channel"

        event_dir = receive_channel / event_id if event_id else None
        if not event_dir or not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        skills_list = load_skills_list(BASE_DIR / "skills")
        if not skills_list:
            return jsonify({"skill_id": "none", "confidence": 0.0})

        event = read_event(event_dir)
        event_text = build_user_message(event)

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)
        result = call1_match_skill(llm, skills_list, event_text)
        llm.close()

        return jsonify(result)

    @app.route("/api/pipeline/scroll", methods=["POST"])
    def api_pipeline_scroll():
        """Call 2: Execute scroll for a single event."""
        from engine.skill_runner import call2_execute_scroll, load_scroll, save_skill_result
        from engine.llm import LLMConfig, LocalLLM
        from engine.classifier import read_event, build_user_message

        data = request.get_json() or {}
        event_id = data.get("event_id")
        skill_id = data.get("skill_id")
        council_dir = app.config["COUNCIL_DIR"]
        receive_channel = council_dir / "receive_channel"

        event_dir = receive_channel / event_id if event_id else None
        if not event_dir or not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        scroll = load_scroll(BASE_DIR / "skills", skill_id)
        if not scroll:
            return jsonify({"error": f"Scroll not found: {skill_id}"}), 404

        event = read_event(event_dir)
        event_text = build_user_message(event)

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)
        result = call2_execute_scroll(llm, scroll, event_text)
        llm.close()

        # Save skill result to event
        save_skill_result(event_dir, result)

        return jsonify(result)

    @app.route("/api/pipeline/classify-single", methods=["POST"])
    def api_pipeline_classify_single():
        """Call 3: Classify and dispatch a single event."""
        from engine.classifier import ClassificationEngine, load_folder_tree
        from engine.dispatcher import CouncilConfig, Dispatcher

        data = request.get_json() or {}
        event_id = data.get("event_id")
        council_dir = app.config["COUNCIL_DIR"]
        receive_channel = council_dir / "receive_channel"
        folder_tree_path = CONFIG_DIR / "classification_only_tree.json"

        event_dir = receive_channel / event_id if event_id else None
        if not event_dir or not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        engine = ClassificationEngine(
            llm_config_path=CONFIG_DIR / "llm.yaml",
            system_prompt_path=CONFIG_DIR / "classification_only_prompt.md",
            folder_tree_path=folder_tree_path,
        )

        folder_tree = load_folder_tree(folder_tree_path)
        council_config = CouncilConfig.from_yaml(council_dir)
        dispatcher = Dispatcher(council_config, folder_tree)

        classification = engine.classify_event_dir(event_dir)
        dispatch_result = None
        if classification.success:
            dispatch_result = dispatcher.dispatch(classification, event_dir)

        engine.close()

        return jsonify({
            "event_id": classification.event_id,
            "outcome": classification.outcome,
            "confidence": round(classification.confidence, 2),
            "reasoning": classification.reasoning,
            "moved": dispatch_result.moved if dispatch_result else False,
        })

    @app.route("/api/skill-match/<event_id>", methods=["POST"])
    def api_skill_match(event_id: str):
        """Call 1: Match event against skills list, optionally generate title."""
        from engine.skill_runner import load_skills_list, call1_match_skill
        from engine.classifier import read_event, build_user_message
        from engine.llm import LLMConfig, LocalLLM

        council_dir = app.config["COUNCIL_DIR"]
        event_dir = council_dir / "receive_channel" / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        # Check if frontend wants title generation
        data = request.get_json(silent=True) or {}
        generate_title = data.get("generate_title", False)

        skills_dir = BASE_DIR / "skills"
        skills_list = load_skills_list(skills_dir)
        if not skills_list:
            return jsonify({"skill_id": "none", "confidence": 0.0})

        event = read_event(event_dir)
        event_text = build_user_message(event)

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)
        result = call1_match_skill(llm, skills_list, event_text, generate_title=generate_title)
        llm.close()

        return jsonify(result)

    @app.route("/api/skill-execute/<event_id>", methods=["POST"])
    def api_skill_execute(event_id: str):
        """Call 2: Execute matched scroll on event."""
        from engine.skill_runner import load_scroll, call2_execute_scroll, save_skill_result
        from engine.classifier import read_event, build_user_message
        from engine.llm import LLMConfig, LocalLLM

        council_dir = app.config["COUNCIL_DIR"]
        event_dir = council_dir / "receive_channel" / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        data = request.get_json() or {}
        skill_id = data.get("skill_id")
        if not skill_id:
            return jsonify({"error": "skill_id required"}), 400

        skills_dir = BASE_DIR / "skills"
        scroll = load_scroll(skills_dir, skill_id)
        if not scroll:
            return jsonify({"error": f"Scroll not found: {skill_id}"}), 404

        event = read_event(event_dir)
        event_text = build_user_message(event)

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)
        result = call2_execute_scroll(llm, scroll, event_text)
        llm.close()

        # Save to event
        save_skill_result(event_dir, result)

        return jsonify(result)

    @app.route("/api/classify-single/<event_id>", methods=["POST"])
    def api_classify_single(event_id: str):
        """Call 3: Classify a single event and dispatch."""
        from engine.classifier import ClassificationEngine, load_folder_tree
        from engine.dispatcher import CouncilConfig, Dispatcher

        council_dir = app.config["COUNCIL_DIR"]
        event_dir = council_dir / "receive_channel" / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        # Extract display info before dispatch moves the event
        display = extract_event_display(event_dir)

        folder_tree_path = CONFIG_DIR / "classification_only_tree.json"
        engine = ClassificationEngine(
            llm_config_path=CONFIG_DIR / "llm.yaml",
            system_prompt_path=CONFIG_DIR / "classification_only_prompt.md",
            folder_tree_path=folder_tree_path,
        )

        folder_tree = load_folder_tree(folder_tree_path)
        council_config = CouncilConfig.from_yaml(council_dir)
        dispatcher = Dispatcher(council_config, folder_tree)

        classification = engine.classify_event_dir(event_dir)
        dispatch_result = None
        if classification.success:
            dispatch_result = dispatcher.dispatch(classification, event_dir)

        engine.close()

        return jsonify({
            "event_id": classification.event_id,
            "outcome": classification.outcome,
            "sub_item_id": classification.sub_item_id,
            "sub_item_name": classification.sub_item_name,
            "confidence": round(classification.confidence, 2),
            "sub_item_confidence": round(classification.sub_item_confidence, 2),
            "reasoning": classification.reasoning,
            "display_title": classification.display_title,
            "display_title_redacted": classification.display_title_redacted,
            "moved": dispatch_result.moved if dispatch_result else False,
            "_subject": display["subject"],
            "_sender": display["sender"],
        })

    @app.route("/api/generate-title/<event_id>", methods=["POST"])
    def api_generate_title(event_id: str):
        """Call 4: Generate a display title + redacted version for an event.

        Reads the classification receipt to get context, then asks the LLM
        for a short human-friendly title. Saves both versions into the receipt.
        Works on events in any folder (post-dispatch).
        """
        from engine.title_generator import generate_event_title
        from engine.llm import LLMConfig, LocalLLM

        data = request.get_json() or {}
        subject = data.get("subject", "")
        outcome = data.get("outcome", "")
        skill_outcome = data.get("skill_outcome")
        reasoning = data.get("reasoning")
        folder_key = data.get("folder_key")

        if not subject or not outcome:
            return jsonify({"error": "subject and outcome required"}), 400

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)

        titles = generate_event_title(
            llm=llm,
            subject=subject,
            outcome=outcome,
            skill_outcome=skill_outcome,
            reasoning=reasoning,
        )
        llm.close()

        # Save titles into the classification receipt if we can find the event
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        receipt_updated = False
        if folder_key:
            relative_path = folder_map.get(folder_key)
            if relative_path:
                event_dir = council_dir / relative_path / event_id
                receipt_path = event_dir / "_classification.json"
                if receipt_path.is_file():
                    try:
                        receipt = json.loads(receipt_path.read_text())
                        receipt["display_title"] = titles["title"]
                        receipt["display_title_redacted"] = titles["title_redacted"]
                        receipt_path.write_text(json.dumps(receipt, indent=2))
                        receipt_updated = True
                    except (json.JSONDecodeError, OSError) as exc:
                        logger.warning("Failed to update receipt for %s: %s", event_id, exc)

        return jsonify({
            "event_id": event_id,
            "display_title": titles["title"],
            "display_title_redacted": titles["title_redacted"],
            "receipt_updated": receipt_updated,
        })

    @app.route("/api/save-titles/<event_id>", methods=["POST"])
    def api_save_titles(event_id: str):
        """Persist agent-generated titles into a classification receipt."""
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"]["folder_map"]

        data = request.get_json() or {}
        folder_key = data.get("folder_key")
        display_title = data.get("display_title", "")
        display_title_redacted = data.get("display_title_redacted", "")

        if not folder_key or not display_title:
            return jsonify({"success": False}), 400

        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"success": False, "error": "Unknown folder"}), 404

        # Search for the event in the folder (name may have timestamp suffix from collision)
        folder_dir = council_dir / relative_path
        event_dir = None
        if folder_dir.is_dir():
            for candidate in folder_dir.iterdir():
                if candidate.is_dir() and candidate.name.startswith(event_id):
                    event_dir = candidate
                    break

        if not event_dir:
            return jsonify({"success": False, "error": "Event not found"}), 404

        receipt_path = event_dir / "_classification.json"
        if not receipt_path.is_file():
            return jsonify({"success": False, "error": "No receipt"}), 404

        try:
            receipt = json.loads(receipt_path.read_text())
            receipt["display_title"] = display_title
            receipt["display_title_redacted"] = display_title_redacted
            receipt_path.write_text(json.dumps(receipt, indent=2))
            return jsonify({"success": True})
        except (json.JSONDecodeError, OSError) as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    # ── Forge Wizard Endpoints ──────────────────────────────────────────────

    @app.route("/api/forge/upload", methods=["POST"])
    def api_forge_upload():
        """Parse uploaded .eml files into temporary event folders.

        In dev mode: treats email body as raw JSON schema (_source_schema.json)
        and saves attachments as separate files. No email_body.txt created.

        In normal mode: standard .eml parsing (email_body.txt + attachments).
        """
        from engine.nexus.eml_parser import parse_eml_to_event

        folder_name = request.form.get("folder_name", "")
        folder_key = request.form.get("folder_key", "")
        is_dev_mode = request.form.get("dev_mode", "false") == "true"

        if not folder_name or not folder_key:
            return jsonify({"success": False, "error": "Folder name and key are required"}), 400

        files = request.files.getlist("files")
        if not files:
            return jsonify({"success": False, "error": "No files uploaded"}), 400

        # Create temp directory for forge processing
        forge_temp = app.config["COUNCIL_DIR"] / "_forge_temp" / folder_key
        forge_temp.mkdir(parents=True, exist_ok=True)

        parsed_events = []
        for f in files:
            eml_bytes = f.read()

            if is_dev_mode:
                # Dev mode: parse body as JSON schema + raw attachments
                event_dir = _parse_eml_dev_mode(eml_bytes, forge_temp)
            else:
                event_dir = parse_eml_to_event(eml_bytes, forge_temp)

            if event_dir:
                # Build display info
                schema_path = event_dir / "_source_schema.json"
                body_path = event_dir / "email_body.txt"

                subject = ""
                sender = ""
                source_schema = None

                if schema_path.is_file():
                    # Dev mode — read schema for display
                    try:
                        schema_data = json.loads(schema_path.read_text())
                        source_schema = schema_data
                        # Try to extract a meaningful title from the schema
                        subject = schema_data.get("developmentDescription", "")
                        if not subject:
                            subject = schema_data.get("applicationType", "")
                        sender = schema_data.get("applicant", {}).get("applicantPerson", {}).get("email", "")
                    except (json.JSONDecodeError, OSError):
                        pass

                if not subject:
                    display = extract_event_display(event_dir)
                    subject = display["subject"]
                    sender = display["sender"]

                attachments = [
                    af.name for af in sorted(event_dir.iterdir())
                    if af.is_file() and not af.name.startswith("_") and af.name != "email_body.txt"
                ]

                event_info = {
                    "event_id": event_dir.name,
                    "subject": subject or "(No Subject)",
                    "sender": sender,
                    "file_count": len(list(event_dir.iterdir())),
                    "attachments": attachments,
                    "dev_mode": is_dev_mode,
                    "has_source_schema": schema_path.is_file(),
                }

                # In dev mode, include schema field count for display
                if source_schema:
                    top_level_keys = [k for k in source_schema.keys() if not k.startswith("_")]
                    event_info["schema_fields"] = len(top_level_keys)
                    event_info["application_type"] = source_schema.get("applicationType", "")
                    event_info["council_ref"] = source_schema.get("councilDANumber", "")

                parsed_events.append(event_info)

        return jsonify({
            "success": True,
            "events": parsed_events,
            "folder_key": folder_key,
            "dev_mode": is_dev_mode,
        })

    @app.route("/api/forge/run", methods=["POST"])
    def api_forge_run():
        """Run the Forge pipeline (Job 1 + Job 2) on uploaded events."""
        from engine.nexus.claude_client import ClaudeConfig
        from engine.nexus.forge import run_forge

        data = request.get_json() or {}
        folder_name = data.get("folder_name", "")
        folder_key = data.get("folder_key", "")

        if not folder_name or not folder_key:
            return jsonify({"success": False, "error": "Folder name and key required"}), 400

        # Load Claude config from external.json
        config = ClaudeConfig.from_external(CONFIG_DIR)
        if not config or not config.is_configured:
            return jsonify({
                "success": False,
                "error": "Claude API key not configured. Set it in config/external.json",
            }), 400

        # Find parsed event directories
        forge_temp = app.config["COUNCIL_DIR"] / "_forge_temp" / folder_key
        if not forge_temp.is_dir():
            return jsonify({"success": False, "error": "No uploaded events found. Upload events first."}), 400

        event_dirs = sorted([
            d for d in forge_temp.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

        if not event_dirs:
            return jsonify({"success": False, "error": "No event directories found"}), 400

        # Run Forge
        result = run_forge(
            config=config,
            folder_name=folder_name,
            folder_key=folder_key,
            event_dirs=event_dirs,
        )

        return jsonify({
            "success": len(result.event_blueprints) > 0,
            "folder_key": result.folder_key,
            "folder_name": result.folder_name,
            "event_blueprints": result.event_blueprints,
            "classification_entry": result.classification_entry,
            "scroll_json": result.scroll_json,
            "errors": result.errors,
        })

    @app.route("/api/forge/save", methods=["POST"])
    def api_forge_save():
        """Create a draft folder skeleton from Forge wizard.

        Creates the physical folder, generates {folder_key}.json from source schema,
        adds to classification tree as status=draft, and moves sample files.
        """
        from engine.nexus.folder_schema import build_folder_schema, save_folder_schema

        data = request.get_json() or {}
        folder_key = data.get("folder_key", "")
        folder_name = data.get("folder_name", "")
        folder_desc = data.get("description", "")
        external_id = data.get("external_id", "")

        if not folder_key or not folder_name:
            return jsonify({"success": False, "error": "Folder name and key required"}), 400

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})

        if folder_key in folder_map:
            return jsonify({"success": False, "error": f"Folder '{folder_key}' already exists"}), 409

        # 1. Create physical folder
        dept_path = f"departments/{folder_key}"
        physical_dir = council_dir / dept_path
        physical_dir.mkdir(parents=True, exist_ok=True)

        # 2. Move temp events to _forge_samples
        samples_dir = physical_dir / "_forge_samples"
        samples_dir.mkdir(exist_ok=True)

        source_schema = None
        forge_temp = council_dir / "_forge_temp" / folder_key
        if forge_temp.is_dir():
            import shutil
            for event_dir in forge_temp.iterdir():
                if event_dir.is_dir():
                    # Look for source schema in the event
                    schema_path = event_dir / "_source_schema.json"
                    if schema_path.is_file() and source_schema is None:
                        try:
                            source_schema = json.loads(schema_path.read_text())
                        except (json.JSONDecodeError, OSError):
                            pass
                    dest = samples_dir / event_dir.name
                    shutil.copytree(event_dir, dest)
            shutil.rmtree(forge_temp, ignore_errors=True)

        # 3. Build and save {folder_key}.json
        folder_schema = build_folder_schema(
            folder_key=folder_key,
            folder_name=folder_name,
            description=folder_desc,
            external_id=external_id,
            source_schema=source_schema,
        )
        save_folder_schema(physical_dir, folder_key, folder_schema)

        # 4. Add to classification tree as DRAFT
        tree_path = CONFIG_DIR / "classification_only_tree.json"
        tree = json.loads(tree_path.read_text())
        tree["folders"][folder_key] = {
            "name": folder_name,
            "description": folder_desc or f"Draft folder — created by Forge",
            "triggers": [],
            "exclusions": [],
            "status": "draft",
        }
        if external_id:
            tree["folders"][folder_key]["external_id"] = external_id
        tree_path.write_text(json.dumps(tree, indent=2))

        # 5. Update council.yaml
        council_yaml_path = council_dir / "council.yaml"
        with open(council_yaml_path, "r") as f:
            council_config = yaml.safe_load(f)
        council_config.setdefault("folder_map", {})[folder_key] = dept_path
        with open(council_yaml_path, "w") as f:
            yaml.dump(council_config, f, default_flow_style=False, sort_keys=False)

        # 6. Update in-memory configs
        app.config["COUNCIL_CONFIG"]["folder_map"][folder_key] = dept_path
        with open(tree_path, "r") as f:
            app.config["FOLDER_TREE"] = json.load(f)

        doc_count = len(folder_schema.get("documents", []))
        logger.info("Forge: created draft folder '%s' with %d document types", folder_name, doc_count)

        return jsonify({
            "success": True,
            "folder_key": folder_key,
            "folder_name": folder_name,
            "status": "draft",
            "document_count": doc_count,
        })

    @app.route("/api/forge/status")
    def api_forge_status():
        """Check if Forge is available (Claude API configured)."""
        from engine.nexus.claude_client import ClaudeConfig
        config = ClaudeConfig.from_external(CONFIG_DIR)
        return jsonify({
            "available": config is not None and config.is_configured,
            "model": config.model if config else None,
        })

    # ── Schema Completion Wizard Endpoints ──────────────────────────────────

    @app.route("/api/forge/schema/<folder_key>")
    def api_forge_schema(folder_key: str):
        """Load the folder schema for the Schema Completion Wizard.

        Reads {folder_key}.json — the single source of truth.
        Source schema inside is already PII-sanitized from creation time.
        """
        from engine.nexus.folder_schema import load_folder_schema

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Folder not found"}), 404

        folder_dir = council_dir / relative_path
        schema = load_folder_schema(folder_dir, folder_key)

        if not schema:
            return jsonify({"error": f"No {folder_key}.json found in folder"}), 404

        # List physical sample files
        files_on_disk = []
        samples_dir = folder_dir / "_forge_samples"
        if samples_dir.is_dir():
            for event_dir in sorted(samples_dir.iterdir()):
                if not event_dir.is_dir():
                    continue
                for f in sorted(event_dir.iterdir()):
                    if f.is_file() and not f.name.startswith("_") and f.name != "email_body.txt":
                        files_on_disk.append({
                            "filename": f.name,
                            "size_bytes": f.stat().st_size,
                        })

        return jsonify({
            "folder_key": folder_key,
            "schema": schema,
            "documents": schema.get("documents", []),
            "files_on_disk": files_on_disk,
        })

    @app.route("/api/forge/schema/<folder_key>/save", methods=["POST"])
    def api_forge_schema_save(folder_key: str):
        """Save the edited folder schema JSON."""
        from engine.nexus.folder_schema import save_folder_schema

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Folder not found"}), 404

        data = request.get_json() or {}
        schema = data.get("schema")
        if not schema:
            return jsonify({"error": "No schema provided"}), 400

        folder_dir = council_dir / relative_path
        save_folder_schema(folder_dir, folder_key, schema)

        logger.info("Schema wizard: saved schema for '%s'", folder_key)
        return jsonify({"success": True})

    @app.route("/api/forge/documents/<folder_key>/save", methods=["POST"])
    def api_forge_documents_save(folder_key: str):
        """Save document requirement flags back into {folder_key}.json."""
        from engine.nexus.folder_schema import load_folder_schema, save_folder_schema

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Folder not found"}), 404

        data = request.get_json() or {}
        documents = data.get("documents")
        if not documents:
            return jsonify({"error": "No documents provided"}), 400

        folder_dir = council_dir / relative_path
        schema = load_folder_schema(folder_dir, folder_key)
        if not schema:
            return jsonify({"error": "Folder schema not found"}), 404

        # Update documents array in the schema
        schema["documents"] = documents
        save_folder_schema(folder_dir, folder_key, schema)

        logger.info("Schema wizard: saved %d document requirements for '%s'", len(documents), folder_key)
        return jsonify({"success": True, "count": len(documents)})

    @app.route("/api/forge/delete/<folder_key>", methods=["POST"])
    def api_forge_delete(folder_key: str):
        """Delete a draft folder completely — physical folder, tree entry, council.yaml."""
        import shutil

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        tree = app.config["FOLDER_TREE"]

        # Only allow deleting draft folders
        folder_def = tree.get("folders", {}).get(folder_key)
        if not folder_def:
            return jsonify({"success": False, "error": "Folder not found"}), 404
        if folder_def.get("status") != "draft":
            return jsonify({"success": False, "error": "Only draft folders can be deleted"}), 400

        relative_path = folder_map.get(folder_key)

        # 1. Remove physical folder
        if relative_path:
            physical_dir = council_dir / relative_path
            if physical_dir.is_dir():
                shutil.rmtree(physical_dir, ignore_errors=True)
                logger.info("Forge delete: removed %s", physical_dir)

        # 2. Remove from classification tree
        tree_path = CONFIG_DIR / "classification_only_tree.json"
        tree_data = json.loads(tree_path.read_text())
        tree_data["folders"].pop(folder_key, None)
        if folder_key in tree_data.get("evaluation_priority", []):
            tree_data["evaluation_priority"].remove(folder_key)
        tree_path.write_text(json.dumps(tree_data, indent=2))

        # 3. Remove from council.yaml
        council_yaml_path = council_dir / "council.yaml"
        with open(council_yaml_path, "r") as f:
            council_config = yaml.safe_load(f)
        council_config.get("folder_map", {}).pop(folder_key, None)
        with open(council_yaml_path, "w") as f:
            yaml.dump(council_config, f, default_flow_style=False, sort_keys=False)

        # 4. Update in-memory configs
        app.config["COUNCIL_CONFIG"]["folder_map"].pop(folder_key, None)
        with open(tree_path, "r") as f:
            app.config["FOLDER_TREE"] = json.load(f)

        # 5. Clean up any forge temp
        forge_temp = council_dir / "_forge_temp" / folder_key
        if forge_temp.is_dir():
            shutil.rmtree(forge_temp, ignore_errors=True)

        logger.info("Forge delete: removed draft folder '%s'", folder_key)
        return jsonify({"success": True, "folder_key": folder_key})

    @app.route("/api/forge/generate-triggers", methods=["POST"])
    def api_forge_generate_triggers():
        """Generate classification triggers using the local LLM.

        Takes folder name + description, returns suggested triggers and exclusions.
        """
        from engine.nexus.prompts import TRIGGER_GEN_SYSTEM_PROMPT, build_trigger_gen_message
        from engine.llm import LLMConfig, LocalLLM

        data = request.get_json() or {}
        folder_name = data.get("folder_name", "")
        description = data.get("description", "")

        if not folder_name or not description:
            return jsonify({"error": "Folder name and description required"}), 400

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)

        user_message = build_trigger_gen_message(folder_name, description)
        response = llm.infer(
            TRIGGER_GEN_SYSTEM_PROMPT,
            user_message,
            use_json_schema=False,
            max_tokens_override=512,
        )
        llm.close()

        if not response.success:
            return jsonify({"error": f"LLM failed: {response.error}"}), 500

        # Save last LLM response for debug/retry
        _save_last_llm_response(app.config["COUNCIL_DIR"], "forge", "generate_triggers", response.content)

        result = _parse_llm_json(response.content)
        if not result:
            return jsonify({
                "triggers": [],
                "exclusions": [],
                "raw": response.content[:500],
                "error": "Failed to parse LLM response as JSON",
            })

        return jsonify({
            "triggers": result.get("triggers", []),
            "exclusions": result.get("exclusions", []),
            "latency_ms": round(response.latency_ms),
            "tokens": response.tokens_used,
        })

    @app.route("/api/forge/save-classification/<folder_key>", methods=["POST"])
    def api_forge_save_classification(folder_key: str):
        """Save classification triggers and exclusions for a draft folder.

        Saves to both the classification tree AND _forge_progress.json for wizard resumption.
        """
        data = request.get_json() or {}
        triggers = data.get("triggers", [])
        exclusions = data.get("exclusions", [])
        description = data.get("description", "")
        step = data.get("step", 3)

        # Update classification tree
        tree_path = CONFIG_DIR / "classification_only_tree.json"
        tree = json.loads(tree_path.read_text())

        folder_def = tree.get("folders", {}).get(folder_key)
        if not folder_def:
            return jsonify({"error": "Folder not found in classification tree"}), 404

        folder_def["triggers"] = triggers
        folder_def["exclusions"] = exclusions
        folder_def["status"] = folder_def.get("status", "draft")
        if description:
            folder_def["description"] = description

        tree_path.write_text(json.dumps(tree, indent=2))
        with open(tree_path, "r") as f:
            app.config["FOLDER_TREE"] = json.load(f)

        # Save forge progress for wizard resumption
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        relative_path = folder_map.get(folder_key)
        if relative_path:
            folder_dir = council_dir / relative_path
            progress_path = folder_dir / "_forge_progress.json"

            progress = {}
            if progress_path.is_file():
                try:
                    progress = json.loads(progress_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass

            completed = set(progress.get("completed_steps", []))
            completed.add(step)
            progress["completed_steps"] = sorted(completed)
            progress["last_step"] = step
            progress["classification_draft"] = {
                "name": folder_def.get("name", ""),
                "description": description,
                "triggers": triggers,
                "exclusions": exclusions,
                "status": "draft",
            }
            progress["updated_at"] = datetime.now().isoformat()

            progress_path.write_text(json.dumps(progress, indent=2))

        logger.info("Forge: saved %d triggers + %d exclusions for '%s'", len(triggers), len(exclusions), folder_key)
        return jsonify({"success": True, "triggers": len(triggers), "exclusions": len(exclusions)})

    @app.route("/api/forge/progress/<folder_key>")
    def api_forge_progress(folder_key: str):
        """Load forge wizard progress for resumption."""
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Folder not found"}), 404

        folder_dir = council_dir / relative_path
        progress_path = folder_dir / "_forge_progress.json"

        if not progress_path.is_file():
            return jsonify({"completed_steps": [], "last_step": 0})

        try:
            progress = json.loads(progress_path.read_text())
            return jsonify(progress)
        except (json.JSONDecodeError, OSError):
            return jsonify({"completed_steps": [], "last_step": 0})

    @app.route("/api/forge/progress/<folder_key>/reset", methods=["POST"])
    def api_forge_progress_reset(folder_key: str):
        """Reset forge wizard progress — start as new."""
        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Folder not found"}), 404

        folder_dir = council_dir / relative_path
        progress_path = folder_dir / "_forge_progress.json"
        if progress_path.is_file():
            progress_path.unlink()

        return jsonify({"success": True})

    @app.route("/api/forge/extract-doc-types/<folder_key>", methods=["POST"])
    def api_forge_extract_doc_types(folder_key: str):
        """Extract document types from source data using local LLM.

        Reads the raw source text + filenames, sends to LLM,
        returns paired document types with matched files.
        """
        import re as _re
        from engine.nexus.prompts import DOC_TYPE_SYSTEM_PROMPT, build_doc_type_message
        from engine.llm import LLMConfig, LocalLLM

        council_dir = app.config["COUNCIL_DIR"]
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        relative_path = folder_map.get(folder_key)
        if not relative_path:
            return jsonify({"error": "Folder not found"}), 404

        folder_dir = council_dir / relative_path
        samples_dir = folder_dir / "_forge_samples"

        # Collect raw text and filenames from samples
        raw_text = ""
        filenames = []

        if samples_dir.is_dir():
            for event_dir in sorted(samples_dir.iterdir()):
                if not event_dir.is_dir():
                    continue
                # Try source schema first
                schema_path = event_dir / "_source_schema.json"
                if schema_path.is_file():
                    raw_text = schema_path.read_text(errors="replace")
                else:
                    body_path = event_dir / "email_body.txt"
                    if body_path.is_file():
                        raw_text = body_path.read_text(errors="replace")

                # Collect filenames
                for f in sorted(event_dir.iterdir()):
                    if f.is_file() and not f.name.startswith("_") and f.name != "email_body.txt":
                        filenames.append(f.name)
                break  # Only process first event

        if not raw_text and not filenames:
            return jsonify({"error": "No source data found"}), 404

        # Call local LLM
        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)

        user_message = build_doc_type_message(raw_text, filenames)
        response = llm.infer(
            DOC_TYPE_SYSTEM_PROMPT,
            user_message,
            use_json_schema=False,
            max_tokens_override=2048,
        )
        llm.close()

        if not response.success:
            return jsonify({"error": f"LLM failed: {response.error}"}), 500

        # Save last LLM response for debug/retry
        _save_last_llm_response(council_dir, folder_key, "extract_doc_types", response.content)

        result = _parse_llm_json(response.content)
        if not result:
            return jsonify({
                "document_types": [],
                "unmatched_files": filenames,
                "error": "Failed to parse LLM response",
                "raw": response.content[:500],
            })

        return jsonify({
            "document_types": result.get("document_types", []),
            "unmatched_files": result.get("unmatched_files", []),
            "latency_ms": round(response.latency_ms),
            "tokens": response.tokens_used,
        })

    @app.route("/api/forge/generate-fields", methods=["POST"])
    def api_forge_generate_fields():
        """Generate extraction fields from instruction + actual document content."""
        import re as _re
        from engine.nexus.prompts import FIELD_GEN_SYSTEM_PROMPT, build_field_gen_message
        from engine.llm import LLMConfig, LocalLLM

        data = request.get_json() or {}
        document_type = data.get("document_type", "")
        instruction = data.get("instruction", "")
        filename = data.get("filename", "")
        folder_key = data.get("folder_key", "")

        if not document_type or not instruction:
            return jsonify({"error": "Document type and instruction required"}), 400

        # Try to read the actual document content from _forge_samples
        document_content = ""
        if folder_key and filename:
            council_dir = app.config["COUNCIL_DIR"]
            folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
            relative_path = folder_map.get(folder_key)
            if relative_path:
                samples_dir = council_dir / relative_path / "_forge_samples"
                if samples_dir.is_dir():
                    for event_dir in samples_dir.iterdir():
                        if not event_dir.is_dir():
                            continue
                        file_path = event_dir / filename
                        if file_path.is_file():
                            ext = file_path.suffix.lower()
                            if ext in {".txt", ".csv", ".md", ".html", ".htm", ".json"}:
                                try:
                                    document_content = file_path.read_text(errors="replace")
                                except OSError:
                                    pass
                            elif ext == ".pdf":
                                # Try to extract text from PDF
                                try:
                                    import subprocess
                                    result = subprocess.run(
                                        ["pdftotext", "-layout", str(file_path), "-"],
                                        capture_output=True, text=True, timeout=10,
                                    )
                                    if result.returncode == 0:
                                        document_content = result.stdout
                                except (FileNotFoundError, subprocess.TimeoutExpired):
                                    document_content = f"[PDF file: {filename} — text extraction not available]"
                            break

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)

        user_message = build_field_gen_message(document_type, instruction, document_content)
        response = llm.infer(
            FIELD_GEN_SYSTEM_PROMPT,
            user_message,
            use_json_schema=False,
            max_tokens_override=1024,
        )
        llm.close()

        if not response.success:
            return jsonify({"error": f"LLM failed: {response.error}"}), 500

        # Save last LLM response
        folder_key_for_save = data.get("folder_key", "forge")
        _save_last_llm_response(app.config["COUNCIL_DIR"], folder_key_for_save, "generate_fields", response.content)

        fields = _parse_llm_json(response.content, expect_array=True)
        if not fields or not isinstance(fields, list):
            return jsonify({
                "fields": [],
                "error": "Failed to parse LLM response",
                "raw": response.content[:500],
            })

        return jsonify({
            "fields": fields,
            "latency_ms": round(response.latency_ms),
            "tokens": response.tokens_used,
        })

    @app.route("/api/skill-execute-sub/<event_id>", methods=["POST"])
    def api_skill_execute_sub(event_id: str):
        """Execute a sub-item micro-skill on an event.

        Looks up skills/{folder_key}/{sub_item_id}.json, falls back to _default.json.
        """
        from engine.classifier import read_event, build_user_message
        from engine.llm import LLMConfig, LocalLLM

        data = request.get_json() or {}
        folder_key = data.get("folder_key", "")
        sub_item_id = data.get("sub_item_id", "")

        if not folder_key or not sub_item_id:
            return jsonify({"error": "folder_key and sub_item_id required"}), 400

        council_dir = app.config["COUNCIL_DIR"]
        event_dir = council_dir / "receive_channel" / event_id

        # Event might already be dispatched — search in department folder
        if not event_dir.is_dir():
            folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
            relative_path = folder_map.get(folder_key)
            if relative_path:
                dept_dir = council_dir / relative_path
                if dept_dir.is_dir():
                    for candidate in dept_dir.iterdir():
                        if candidate.is_dir() and candidate.name.startswith(event_id):
                            event_dir = candidate
                            break

        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        # Load sub-item skill
        skill_path = BASE_DIR / "skills" / folder_key / f"{sub_item_id}.json"
        if not skill_path.is_file():
            skill_path = BASE_DIR / "skills" / folder_key / "_default.json"
        if not skill_path.is_file():
            return jsonify({"error": f"No skill found for {folder_key}/{sub_item_id}", "skill_matched": False})

        try:
            skill = json.loads(skill_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return jsonify({"error": f"Failed to load skill: {exc}"}), 500

        # Read event content
        event = read_event(event_dir)
        event_text = build_user_message(event)

        # Build skill prompt
        skill_prompt = f"""You are a council mailroom skill agent. Execute the skill on the event.
DO NOT explain your reasoning. DO NOT think step by step. Return ONLY the JSON object immediately.

SKILL: {skill.get('name', '')}
DESCRIPTION: {skill.get('description', '')}

CHECKS:
{chr(10).join('- ' + c for c in skill.get('checks', []))}

OUTCOMES:
{chr(10).join(f'- {k}: {v}' for k, v in skill.get('outcomes', {}).items())}

EXTRACT:
{chr(10).join(f'- {k}: {v}' for k, v in skill.get('metadata_fields', {}).items())}

Return ONLY this JSON. Nothing else:
{{"outcome": "<code>", "metadata": {{"field": "value"}}, "analysis": "<1 sentence>", "missing_info": []}}"""

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)
        response = llm.infer(skill_prompt, event_text, use_json_schema=False, max_tokens_override=2048)
        llm.close()

        if not response.success:
            return jsonify({"error": f"Skill execution failed: {response.error}", "skill_matched": True})

        # Parse response
        result = _parse_llm_json(response.content)
        if not result:
            # Try to extract partial data from reasoning if available
            import re as _re
            content = response.content
            # Look for outcome mentions in reasoning text
            outcome_match = _re.search(r'`?(\w+_\w+)`?\s*[:.]', content)
            location_match = _re.search(r'[Ll]ocation[:\s]*["\']?([^"\'}\n]+)', content)
            safety_match = _re.search(r'safety.*(true|yes|urgent)', content, _re.IGNORECASE)

            result = {
                "outcome": outcome_match.group(1) if outcome_match else "needs_review",
                "analysis": "Response was incomplete — extracted partial data from reasoning",
                "metadata": {},
                "missing_info": ["Complete skill analysis (model response truncated)"],
            }
            if location_match:
                result["metadata"]["location"] = location_match.group(1).strip()
            if safety_match:
                result["metadata"]["safety_concern"] = True

        result["skill_matched"] = True
        result["skill_id"] = sub_item_id
        result["skill_name"] = skill.get("name", sub_item_id)
        result["folder_key"] = folder_key
        result["latency_ms"] = round(response.latency_ms)
        result["tokens"] = response.tokens_used

        # Save skill result to event folder
        skill_result_path = event_dir / "_skill_result.json"
        skill_result_path.write_text(json.dumps(result, indent=2))

        return jsonify(result)

    # ── Demo Mode Endpoints ─────────────────────────────────────────────────

    @app.route("/api/demo/snapshot", methods=["POST"])
    def api_demo_snapshot():
        """Take a snapshot of the current council state before demo."""
        import shutil

        council_dir = app.config["COUNCIL_DIR"]
        snapshot_dir = council_dir / "_demo_snapshot"

        # Remove old snapshot
        if snapshot_dir.is_dir():
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        snapshot_dir.mkdir()

        # Snapshot receive_channel
        rc = council_dir / "receive_channel"
        if rc.is_dir():
            shutil.copytree(rc, snapshot_dir / "receive_channel")

        # Snapshot all department folders
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        for key, rel_path in folder_map.items():
            src = council_dir / rel_path
            if src.is_dir():
                shutil.copytree(src, snapshot_dir / rel_path)

        # Snapshot processed UIDs
        uids_path = council_dir / "_processed_uids.json"
        if uids_path.is_file():
            shutil.copy2(uids_path, snapshot_dir / "_processed_uids.json")

        logger.info("Demo: snapshot created at %s", snapshot_dir)
        return jsonify({"success": True, "message": "Snapshot created"})

    @app.route("/api/demo/restore", methods=["POST"])
    def api_demo_restore():
        """Restore council state from snapshot — undo demo."""
        import shutil

        council_dir = app.config["COUNCIL_DIR"]
        snapshot_dir = council_dir / "_demo_snapshot"

        if not snapshot_dir.is_dir():
            return jsonify({"success": False, "error": "No snapshot found"}), 404

        # Restore receive_channel
        rc = council_dir / "receive_channel"
        if rc.is_dir():
            shutil.rmtree(rc, ignore_errors=True)
        snap_rc = snapshot_dir / "receive_channel"
        if snap_rc.is_dir():
            shutil.copytree(snap_rc, rc)
        else:
            rc.mkdir(parents=True, exist_ok=True)

        # Restore department folders
        folder_map = app.config["COUNCIL_CONFIG"].get("folder_map", {})
        for key, rel_path in folder_map.items():
            dest = council_dir / rel_path
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            snap_src = snapshot_dir / rel_path
            if snap_src.is_dir():
                shutil.copytree(snap_src, dest)
            else:
                dest.mkdir(parents=True, exist_ok=True)

        # Restore processed UIDs
        snap_uids = snapshot_dir / "_processed_uids.json"
        uids_path = council_dir / "_processed_uids.json"
        if snap_uids.is_file():
            shutil.copy2(snap_uids, uids_path)
        elif uids_path.is_file():
            uids_path.unlink()

        logger.info("Demo: state restored from snapshot")
        return jsonify({"success": True, "message": "State restored from snapshot"})

    # Shuffled demo queue — cycles through all files without repeats
    _demo_queue: list[Path] = []

    @app.route("/api/demo/push-one", methods=["POST"])
    def api_demo_push_one():
        """Push one shuffled .MSG file from Demo/ to receive_channel."""
        import random
        from engine.msg_parser import parse_msg_to_event

        council_dir = app.config["COUNCIL_DIR"]
        demo_dir = council_dir / "Demo"
        receive_channel = council_dir / "receive_channel"
        receive_channel.mkdir(parents=True, exist_ok=True)

        if not demo_dir.is_dir():
            return jsonify({"success": False, "error": "Demo folder not found"}), 404

        # Refill and shuffle queue when empty
        if not _demo_queue:
            msg_files = [f for f in demo_dir.iterdir() if f.is_file() and f.suffix.upper() == ".MSG"]
            if not msg_files:
                return jsonify({"success": False, "error": "No .MSG files in Demo folder"}), 404
            random.shuffle(msg_files)
            _demo_queue.extend(msg_files)

        chosen = _demo_queue.pop(0)
        event_dir = parse_msg_to_event(chosen, receive_channel)

        if event_dir:
            display = extract_event_display(event_dir)
            return jsonify({
                "success": True,
                "event_id": event_dir.name,
                "subject": display["subject"],
                "source_file": chosen.name,
                "remaining": len(_demo_queue),
            })
        else:
            return jsonify({"success": False, "error": f"Failed to parse {chosen.name}"}), 500

    @app.route("/api/demo/status")
    def api_demo_status():
        """Check demo state — is snapshot available, how many demo files."""
        council_dir = app.config["COUNCIL_DIR"]
        demo_dir = council_dir / "Demo"
        snapshot_dir = council_dir / "_demo_snapshot"

        msg_count = 0
        if demo_dir.is_dir():
            msg_count = len([f for f in demo_dir.iterdir() if f.suffix.upper() == ".MSG"])

        return jsonify({
            "demo_available": msg_count > 0,
            "demo_files": msg_count,
            "snapshot_exists": snapshot_dir.is_dir(),
        })

    # ── Model Selection Endpoints ───────────────────────────────────────────

    @app.route("/api/models")
    def api_models():
        """List available models and the currently active one."""
        llm_path = CONFIG_DIR / "llm.yaml"
        with open(llm_path, "r") as f:
            llm_config = yaml.safe_load(f)
        return jsonify({
            "active": llm_config.get("model", ""),
            "models": llm_config.get("models", []),
        })

    @app.route("/api/models/switch", methods=["POST"])
    def api_models_switch():
        """Switch the active LLM model."""
        data = request.get_json() or {}
        model_id = data.get("model_id", "")
        if not model_id:
            return jsonify({"error": "model_id required"}), 400

        llm_path = CONFIG_DIR / "llm.yaml"
        with open(llm_path, "r") as f:
            llm_config = yaml.safe_load(f)

        # Verify model exists in the list
        valid_ids = [m["id"] for m in llm_config.get("models", [])]
        if model_id not in valid_ids:
            return jsonify({"error": f"Unknown model: {model_id}"}), 400

        llm_config["model"] = model_id
        with open(llm_path, "w") as f:
            yaml.dump(llm_config, f, default_flow_style=False, sort_keys=False)

        logger.info("Model switched to: %s", model_id)
        return jsonify({"success": True, "model": model_id})
