from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from jira_read import JiraReadClient


SCD_TICKET_KEY_PATTERN = re.compile(r"\b(SCD-\d+)\b", re.IGNORECASE)
SCD_TICKET_NUMBER_PATTERN = re.compile(r"^\d+$")


def normalize_ticket_id(ticket_id: str) -> str:
    normalized = str(ticket_id or "").strip()
    if not normalized:
        return ""

    ticket_key_match = SCD_TICKET_KEY_PATTERN.search(normalized)
    if ticket_key_match:
        return ticket_key_match.group(1).upper()

    if SCD_TICKET_NUMBER_PATTERN.fullmatch(normalized):
        return f"SCD-{normalized}"

    return normalized.upper()


def fetch_issue_created_at(ticket_id: str) -> str:
    client = JiraReadClient()
    issue = client.get_issue(ticket_id, fields=["created"])
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    created_at = str(fields.get("created") or "").strip() if isinstance(fields, dict) else ""
    if not created_at:
        raise RuntimeError(f"Ticket {ticket_id} did not return a created timestamp")
    return created_at


def build_scan_result(ticket_queue: list[dict[str, str]], status_message: str) -> dict[str, Any]:
    primary_ticket = ticket_queue[0] if ticket_queue else {"ticket_id": "", "ticket_created_at": ""}
    return {
        "should_scan": bool(ticket_queue),
        "state_changed": False,
        "ticket_id": primary_ticket["ticket_id"],
        "ticket_created_at": primary_ticket["ticket_created_at"],
        "ticket_count": len(ticket_queue),
        "ticket_queue_json": json.dumps(ticket_queue),
        "status_message": status_message,
    }


def write_github_output(path: str | None, result: dict[str, Any]) -> None:
    if not path:
        return

    lines = []
    for key, value in result.items():
        if isinstance(value, bool):
            serialized = str(value).lower()
        else:
            serialized = str(value or "")
        lines.append(f"{key}={serialized}")

    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def resolve_target(ticket_id: str) -> dict[str, Any]:
    normalized_ticket_id = normalize_ticket_id(ticket_id)
    if not normalized_ticket_id:
        return build_scan_result([], "Manual mode selected. Provide a Jira ticket id to scan.")

    created_at = fetch_issue_created_at(normalized_ticket_id)
    return build_scan_result(
        [{"ticket_id": normalized_ticket_id, "ticket_created_at": created_at}],
        f"Manual mode selected. Manual scan requested for {normalized_ticket_id}.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve manual scan targets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--ticket-id", default="")
    resolve_parser.add_argument("--github-output", default="")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "resolve":
        result = resolve_target(args.ticket_id)
        write_github_output(args.github_output, result)
        print(json.dumps(result))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())