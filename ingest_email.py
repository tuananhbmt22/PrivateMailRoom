#!/usr/bin/env python3
"""Kajima Mailroom — Email Ingestion Runner.

Polls an IMAP mailbox and converts incoming emails into events
in the council's receive_channel.

Usage:
    # Continuous polling (runs forever)
    python ingest_email.py --council Test_Council

    # Single poll (check once and exit)
    python ingest_email.py --council Test_Council --once

    # Override poll interval
    python ingest_email.py --council Test_Council --interval 10
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from engine.email_ingester import EmailConfig, EmailIngester

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = BASE_DIR / "config"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mailroom.ingest")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kajima Mailroom Email Ingestion")
    parser.add_argument("--council", required=True, help="Council directory name")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--interval", type=int, default=None, help="Override poll interval (seconds)")
    args = parser.parse_args()

    council_dir = BASE_DIR / args.council
    if not council_dir.is_dir():
        logger.error("Council directory not found: %s", council_dir)
        sys.exit(1)

    receive_channel = council_dir / "receive_channel"
    if not receive_channel.is_dir():
        logger.error("Receive channel not found: %s", receive_channel)
        sys.exit(1)

    # Load email config
    email_config_path = CONFIG_DIR / "email.yaml"
    if not email_config_path.is_file():
        logger.error("Email config not found: %s", email_config_path)
        sys.exit(1)

    config = EmailConfig.from_yaml(email_config_path)

    if not config.username or not config.password:
        logger.error("Email username and password must be set in %s", email_config_path)
        sys.exit(1)

    if args.interval:
        config = EmailConfig(
            imap_host=config.imap_host,
            imap_port=config.imap_port,
            use_ssl=config.use_ssl,
            username=config.username,
            password=config.password,
            poll_interval_seconds=args.interval,
            inbox_folder=config.inbox_folder,
            after_processing=config.after_processing,
            processed_folder=config.processed_folder,
            since_date=config.since_date,
            max_attachment_size_mb=config.max_attachment_size_mb,
            allowed_extensions=config.allowed_extensions,
        )

    ingester = EmailIngester(config, receive_channel)

    print(f"\n  Kajima Mailroom — Email Ingestion")
    print(f"  Council: {args.council}")
    print(f"  Server: {config.imap_host}:{config.imap_port}")
    print(f"  User: {config.username}")
    print(f"  Mode: {'single poll' if args.once else f'continuous (every {config.poll_interval_seconds}s)'}\n")

    if args.once:
        ingester.connect()
        events = ingester.poll_once()
        ingester.disconnect()
        logger.info("Ingested %d event(s)", len(events))
        for ev in events:
            logger.info("  → %s", ev.name)
    else:
        ingester.connect()
        ingester.run_forever()


if __name__ == "__main__":
    main()
