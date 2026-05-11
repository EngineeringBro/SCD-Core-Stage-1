from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jira_read import JiraReadClient


CORE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CORE_ROOT.parent
STATE_PATH = Path(os.getenv("SCAN_AUTO_STATE_PATH") or (REPO_ROOT / ".github" / "scan_auto_state.json"))
DEFAULT_LATEST_TICKET_JQL = os.getenv("SCAN_AUTO_LATEST_JQL") or "project = SCD ORDER BY created DESC"
REQUIRED_AUTO_ASSIGNEE_DISPLAY_NAME = os.getenv("SCAN_AUTO_REQUIRED_ASSIGNEE") or "Hussein Chaib"
AUTO_SCAN_QUEUE_SIZE = int(os.getenv("SCAN_AUTO_QUEUE_SIZE") or "5")
AUTO_SCAN_SEARCH_WINDOW = int(os.getenv("SCAN_AUTO_SEARCH_WINDOW") or "100")
SCANNED_TICKET_ID_HISTORY_LIMIT = int(os.getenv("SCAN_AUTO_SCANNED_HISTORY_LIMIT") or "200")
SCD_TICKET_KEY_PATTERN = re.compile(r"\b(SCD-\d+)\b", re.IGNORECASE)
SCD_TICKET_NUMBER_PATTERN = re.compile(r"^\d+$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "enabled": False,
            "last_scanned_ticket_id": "",
            "last_scanned_created_at": "",
            "scanned_ticket_ids": [],
            "updated_at": "",
        }

    payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    raw_scanned_ticket_ids = payload.get("scanned_ticket_ids")
    scanned_ticket_ids = []
    if isinstance(raw_scanned_ticket_ids, list):
        for ticket_id in raw_scanned_ticket_ids:
            normalized_ticket_id = normalize_ticket_id(str(ticket_id or ""))
            if normalized_ticket_id and normalized_ticket_id not in scanned_ticket_ids:
                scanned_ticket_ids.append(normalized_ticket_id)

    return {
        "enabled": bool(payload.get("enabled", False)),
        "last_scanned_ticket_id": str(payload.get("last_scanned_ticket_id") or "").strip().upper(),
        "last_scanned_created_at": str(payload.get("last_scanned_created_at") or "").strip(),
        "scanned_ticket_ids": scanned_ticket_ids,
        "updated_at": str(payload.get("updated_at") or "").strip(),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def parse_timestamp(value: str) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw_value, fmt)
        except ValueError:
            continue

    normalized = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


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


def build_scan_result(ticket_queue: list[dict[str, str]], state_changed: bool, status_message: str) -> dict[str, Any]:
    primary_ticket = ticket_queue[0] if ticket_queue else {"ticket_id": "", "ticket_created_at": ""}
    return {
        "should_scan": bool(ticket_queue),
        "state_changed": state_changed,
        "ticket_id": primary_ticket["ticket_id"],
        "ticket_created_at": primary_ticket["ticket_created_at"],
        "ticket_count": len(ticket_queue),
        "ticket_queue_json": json.dumps(ticket_queue),
        "status_message": status_message,
    }


def set_enabled(enabled: bool) -> dict[str, Any]:
    state = load_state()
    state_changed = state["enabled"] != enabled
    if state_changed:
        state["enabled"] = enabled
        state["updated_at"] = utc_now_iso()
        save_state(state)

    return build_scan_result([], state_changed, "Auto scan enabled." if enabled else "Auto scan disabled.")


def fetch_issue_created_at(ticket_id: str) -> str:
    client = JiraReadClient()
    issue = client.get_issue(ticket_id, fields=["created"])
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    created_at = str(fields.get("created") or "").strip() if isinstance(fields, dict) else ""
    if not created_at:
        raise RuntimeError(f"Ticket {ticket_id} did not return a created timestamp")
    return created_at


def extract_assignee_display_name(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    if not isinstance(fields, dict):
        return ""

    assignee = fields.get("assignee")
    if not isinstance(assignee, dict):
        return ""

    return str(assignee.get("displayName") or "").strip()


def resolve_recent_assigned_tickets() -> list[dict[str, str]]:
    client = JiraReadClient()
    issues = client.search(DEFAULT_LATEST_TICKET_JQL, fields=["created", "assignee"], max_results=AUTO_SCAN_SEARCH_WINDOW)

    assigned_tickets: list[dict[str, str]] = []
    for issue in issues:
        assignee_display_name = extract_assignee_display_name(issue)
        if assignee_display_name != REQUIRED_AUTO_ASSIGNEE_DISPLAY_NAME:
            continue

        ticket_id = normalize_ticket_id(issue.get("key"))
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        created_at = str(fields.get("created") or "").strip() if isinstance(fields, dict) else ""
        if not ticket_id or not created_at:
            continue

        assigned_tickets.append(
            {
                "ticket_id": ticket_id,
                "ticket_created_at": created_at,
                "assignee_display_name": assignee_display_name,
            }
        )
        if len(assigned_tickets) >= AUTO_SCAN_QUEUE_SIZE:
            break

    return assigned_tickets


def resolve_auto_scan_queue(state: dict[str, Any]) -> dict[str, Any]:
    recent_assigned_tickets = resolve_recent_assigned_tickets()
    if not recent_assigned_tickets:
        return build_scan_result([], False, f"Auto scan found no recently created tickets assigned to {REQUIRED_AUTO_ASSIGNEE_DISPLAY_NAME}.")

    scanned_ticket_ids = set(state.get("scanned_ticket_ids") or [])
    eligible_ticket_queue: list[dict[str, str]] = []
    for ticket in recent_assigned_tickets:
        ticket_id = ticket["ticket_id"]
        if ticket_id in scanned_ticket_ids:
            continue
        eligible_ticket_queue.append(
            {
                "ticket_id": ticket_id,
                "ticket_created_at": ticket["ticket_created_at"],
            }
        )

    if eligible_ticket_queue:
        return build_scan_result(
            eligible_ticket_queue,
            False,
            f"Auto scan queued {len(eligible_ticket_queue)} assigned ticket(s) from the recent assigned queue.",
        )

    return build_scan_result(
        [],
        False,
        f"The {len(recent_assigned_tickets)} most recent tickets assigned to {REQUIRED_AUTO_ASSIGNEE_DISPLAY_NAME} were already scanned. Waiting for a newly created assigned ticket.",
    )


def resolve_target(event_name: str, mode: str, ticket_id: str) -> dict[str, Any]:
    normalized_mode = str(mode or "Manual").strip() or "Manual"

    if event_name == "workflow_dispatch":
        if normalized_mode == "Auto Scan mode":
            enable_result = set_enabled(True)
            return build_scan_result(
                [],
                enable_result["state_changed"],
                "Auto scan enabled. Waiting for the scheduled cron run.",
            )
        if normalized_mode == "Auto":
            enable_result = set_enabled(True)
            return build_scan_result(
                [],
                enable_result["state_changed"],
                "Auto scan enabled. Waiting for the scheduled cron run.",
            )
        if normalized_mode != "Manual":
            raise RuntimeError(f"Unsupported workflow_dispatch mode: {normalized_mode}")

        disable_result = set_enabled(False)

        normalized_ticket_id = normalize_ticket_id(ticket_id)
        if not normalized_ticket_id:
            return build_scan_result([], disable_result["state_changed"], "Manual mode selected. Auto scan is off.")

        created_at = fetch_issue_created_at(normalized_ticket_id)
        return build_scan_result(
            [{"ticket_id": normalized_ticket_id, "ticket_created_at": created_at}],
            disable_result["state_changed"],
            f"Manual mode selected. Auto scan is off. Manual scan requested for {normalized_ticket_id}.",
        )

    if event_name == "schedule":
        state = load_state()
        if not state["enabled"]:
            return build_scan_result([], False, "Auto scan is disabled.")
        return resolve_auto_scan_queue(state)

    return build_scan_result([], False, f"Unsupported event '{event_name}'.")


def mark_scanned(ticket_id: str, created_at: str) -> dict[str, Any]:
    normalized_ticket_id = normalize_ticket_id(ticket_id)
    normalized_created_at = str(created_at or "").strip()
    if not normalized_ticket_id:
        raise RuntimeError("mark-scanned requires ticket_id")
    if not normalized_created_at:
        raise RuntimeError("mark-scanned requires created_at")

    state = load_state()
    scanned_ticket_ids = list(state.get("scanned_ticket_ids") or [])
    scanned_ticket_ids_changed = False
    if normalized_ticket_id not in scanned_ticket_ids:
        scanned_ticket_ids.insert(0, normalized_ticket_id)
        scanned_ticket_ids_changed = True
    state["scanned_ticket_ids"] = scanned_ticket_ids[:SCANNED_TICKET_ID_HISTORY_LIMIT]

    current_created_at = parse_timestamp(state.get("last_scanned_created_at", ""))
    incoming_created_at = parse_timestamp(normalized_created_at)

    should_update = False
    if state.get("last_scanned_ticket_id") == normalized_ticket_id:
        should_update = True
    elif current_created_at is None:
        should_update = True
    elif incoming_created_at is not None and incoming_created_at >= current_created_at:
        should_update = True

    if should_update:
        state["last_scanned_ticket_id"] = normalized_ticket_id
        state["last_scanned_created_at"] = normalized_created_at
        state["updated_at"] = utc_now_iso()

    if should_update or scanned_ticket_ids_changed:
        if not state.get("updated_at"):
            state["updated_at"] = utc_now_iso()
        save_state(state)

    return {
        "should_scan": False,
        "state_changed": should_update or scanned_ticket_ids_changed,
        "ticket_id": normalized_ticket_id,
        "ticket_created_at": normalized_created_at,
        "status_message": f"Marked {normalized_ticket_id} as the latest scanned ticket." if should_update else f"Skipped state update for {normalized_ticket_id} because it is older than the current watermark.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage auto-scan state and resolve scan targets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--event-name", required=True)
    resolve_parser.add_argument("--mode", default="Manual")
    resolve_parser.add_argument("--ticket-id", default="")
    resolve_parser.add_argument("--github-output", default="")

    mark_parser = subparsers.add_parser("mark-scanned")
    mark_parser.add_argument("--ticket-id", required=True)
    mark_parser.add_argument("--created-at", required=True)
    mark_parser.add_argument("--github-output", default="")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "resolve":
        result = resolve_target(args.event_name, args.mode, args.ticket_id)
        write_github_output(args.github_output, result)
        print(json.dumps(result))
        return 0

    if args.command == "mark-scanned":
        result = mark_scanned(args.ticket_id, args.created_at)
        write_github_output(args.github_output, result)
        print(json.dumps(result))
        return 0

    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())