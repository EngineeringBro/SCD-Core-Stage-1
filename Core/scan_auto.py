from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jira_read import JiraReadClient


CORE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CORE_ROOT.parent
STATE_PATH = Path(os.getenv("SCAN_AUTO_STATE_PATH") or (REPO_ROOT / ".github" / "scan_auto_state.json"))
DEFAULT_LATEST_TICKET_JQL = os.getenv("SCAN_AUTO_LATEST_JQL") or "project = SCD ORDER BY created DESC"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "enabled": False,
            "last_scanned_ticket_id": "",
            "last_scanned_created_at": "",
            "updated_at": "",
        }

    payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "enabled": bool(payload.get("enabled", False)),
        "last_scanned_ticket_id": str(payload.get("last_scanned_ticket_id") or "").strip().upper(),
        "last_scanned_created_at": str(payload.get("last_scanned_created_at") or "").strip(),
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
    return str(ticket_id or "").strip().upper()


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


def set_enabled(enabled: bool) -> dict[str, Any]:
    state = load_state()
    state_changed = state["enabled"] != enabled
    if state_changed:
        state["enabled"] = enabled
        state["updated_at"] = utc_now_iso()
        save_state(state)

    return {
        "should_scan": False,
        "state_changed": state_changed,
        "ticket_id": "",
        "ticket_created_at": "",
        "status_message": "Auto scan enabled." if enabled else "Auto scan disabled.",
    }


def fetch_issue_created_at(ticket_id: str) -> str:
    client = JiraReadClient()
    issue = client.get_issue(ticket_id, fields=["created"])
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    created_at = str(fields.get("created") or "").strip() if isinstance(fields, dict) else ""
    if not created_at:
        raise RuntimeError(f"Ticket {ticket_id} did not return a created timestamp")
    return created_at


def resolve_latest_ticket() -> tuple[str, str]:
    client = JiraReadClient()
    issues = client.search(DEFAULT_LATEST_TICKET_JQL, fields=["created"], max_results=1)
    if not issues:
        return "", ""

    latest_issue = issues[0]
    ticket_id = normalize_ticket_id(latest_issue.get("key"))
    fields = latest_issue.get("fields") if isinstance(latest_issue, dict) else {}
    created_at = str(fields.get("created") or "").strip() if isinstance(fields, dict) else ""
    return ticket_id, created_at


def resolve_target(event_name: str, mode: str, ticket_id: str) -> dict[str, Any]:
    normalized_mode = str(mode or "Manual").strip() or "Manual"

    if event_name == "workflow_dispatch":
        if normalized_mode == "Auto":
            return set_enabled(True)
        if normalized_mode != "Manual":
            raise RuntimeError(f"Unsupported workflow_dispatch mode: {normalized_mode}")

        disable_result = set_enabled(False)

        normalized_ticket_id = normalize_ticket_id(ticket_id)
        if not normalized_ticket_id:
            return {
                "should_scan": False,
                "state_changed": disable_result["state_changed"],
                "ticket_id": "",
                "ticket_created_at": "",
                "status_message": "Manual mode selected. Auto scan is off.",
            }

        created_at = fetch_issue_created_at(normalized_ticket_id)
        return {
            "should_scan": True,
            "state_changed": disable_result["state_changed"],
            "ticket_id": normalized_ticket_id,
            "ticket_created_at": created_at,
            "status_message": f"Manual mode selected. Auto scan is off. Manual scan requested for {normalized_ticket_id}.",
        }

    if event_name == "schedule":
        state = load_state()
        if not state["enabled"]:
            return {
                "should_scan": False,
                "state_changed": False,
                "ticket_id": "",
                "ticket_created_at": "",
                "status_message": "Auto scan is disabled.",
            }

        latest_ticket_id, latest_created_at = resolve_latest_ticket()
        if not latest_ticket_id:
            return {
                "should_scan": False,
                "state_changed": False,
                "ticket_id": "",
                "ticket_created_at": "",
                "status_message": "Auto scan found no Jira tickets.",
            }

        if latest_ticket_id == state["last_scanned_ticket_id"]:
            return {
                "should_scan": False,
                "state_changed": False,
                "ticket_id": latest_ticket_id,
                "ticket_created_at": latest_created_at,
                "status_message": f"Latest ticket {latest_ticket_id} was already scanned. Waiting for a newer ticket.",
            }

        return {
            "should_scan": True,
            "state_changed": False,
            "ticket_id": latest_ticket_id,
            "ticket_created_at": latest_created_at,
            "status_message": f"Auto scan queued for newest ticket {latest_ticket_id}.",
        }

    return {
        "should_scan": False,
        "state_changed": False,
        "ticket_id": "",
        "ticket_created_at": "",
        "status_message": f"Unsupported event '{event_name}'.",
    }


def mark_scanned(ticket_id: str, created_at: str) -> dict[str, Any]:
    normalized_ticket_id = normalize_ticket_id(ticket_id)
    normalized_created_at = str(created_at or "").strip()
    if not normalized_ticket_id:
        raise RuntimeError("mark-scanned requires ticket_id")
    if not normalized_created_at:
        raise RuntimeError("mark-scanned requires created_at")

    state = load_state()
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
        save_state(state)

    return {
        "should_scan": False,
        "state_changed": should_update,
        "ticket_id": normalized_ticket_id,
        "ticket_created_at": normalized_created_at,
        "status_message": f"Marked {normalized_ticket_id} as the latest scanned ticket." if should_update else f"Skipped state update for {normalized_ticket_id} because it is older than the current watermark.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage auto-scan state and resolve scan targets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--event-name", required=True)
    resolve_parser.add_argument("--mode", default="manual_ticket")
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