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
    """List pending events in the receive channel."""
    channel = council_dir / "receive_channel"
    events = []
    if channel.is_dir():
        for event_dir in sorted(channel.iterdir()):
            if event_dir.is_dir() and not event_dir.name.startswith("."):
                files = [f.name for f in event_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
                events.append({
                    "event_id": event_dir.name,
                    "file_count": len(files),
                    "files": files,
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
            })

        # Add undetermined
        folders.append({
            "key": "undetermined",
            "name": "Undetermined",
            "description": tree["folders"].get("undetermined", {}).get("description", ""),
            "count": counts.get("undetermined", 0),
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

                return jsonify({
                    "success": True,
                    "events_created": len(events),
                    "event_ids": [e.name for e in events],
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

                return jsonify({
                    "success": True,
                    "events_created": len(created_events),
                    "event_ids": created_events,
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
        """Call 1: Match event against skills list."""
        from engine.skill_runner import load_skills_list, call1_match_skill
        from engine.classifier import read_event, build_user_message
        from engine.llm import LLMConfig, LocalLLM

        council_dir = app.config["COUNCIL_DIR"]
        event_dir = council_dir / "receive_channel" / event_id
        if not event_dir.is_dir():
            return jsonify({"error": "Event not found"}), 404

        skills_dir = BASE_DIR / "skills"
        skills_list = load_skills_list(skills_dir)
        if not skills_list:
            return jsonify({"skill_id": "none", "confidence": 0.0})

        event = read_event(event_dir)
        event_text = build_user_message(event)

        llm_config = LLMConfig.from_yaml(CONFIG_DIR / "llm.yaml")
        llm = LocalLLM(llm_config)
        result = call1_match_skill(llm, skills_list, event_text)
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
            "confidence": round(classification.confidence, 2),
            "reasoning": classification.reasoning,
            "moved": dispatch_result.moved if dispatch_result else False,
        })
