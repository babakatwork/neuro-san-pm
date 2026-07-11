"""Inspect or resolve body-free Slack event dead letters."""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from coded_tools.colleague.slack_event_queue import dead_letters
from coded_tools.colleague.slack_event_queue import drop_dead_letter
from coded_tools.colleague.slack_event_queue import requeue_dead_letter


def main() -> int:
    """Run an explicit dead-letter operator action."""
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List body-free quarantined event metadata")
    for action in ("requeue", "drop"):
        command = subparsers.add_parser(action)
        command.add_argument("event_id")
    args = parser.parse_args()
    if args.action == "list":
        print(json.dumps(dead_letters(), indent=2, sort_keys=True))
        return 0
    changed = requeue_dead_letter(args.event_id) if args.action == "requeue" else drop_dead_letter(args.event_id)
    if not changed:
        print(f"Event {args.event_id!r} was not changed.", file=sys.stderr)
        return 1
    verb = "requeued" if args.action == "requeue" else "dropped"
    print(f"Event {args.event_id!r} {verb}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
