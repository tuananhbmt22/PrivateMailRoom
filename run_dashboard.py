#!/usr/bin/env python3
"""Kajima Mailroom — Dashboard Runner.

Starts the admin dashboard on the local network.

Usage:
    python run_dashboard.py --council Test_Council
    python run_dashboard.py --council Test_Council --port 5000
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dashboard.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Kajima Mailroom Dashboard")
    parser.add_argument("--council", required=True, help="Council directory name")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    args = parser.parse_args()

    app = create_app(council_name=args.council)
    print(f"\n  Kajima Mailroom Dashboard")
    print(f"  Council: {args.council}")
    print(f"  URL: http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
