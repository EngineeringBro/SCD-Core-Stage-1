from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODULE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = MODULE_DIR / "output"
REGISTRY_PATH = OUTPUT_DIR / "notification_registry.json"
NOTIFICATIONS_LOG_PATH = OUTPUT_DIR / "Notifications log.md"


def _ensure_parent() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"entries": []}

    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {"entries": []}
    return {"entries": entries}


def _escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _build_notifications_log(payload: dict[str, Any]) -> str:
    entries = payload.get("entries") or []
    generated_at = datetime.now(timezone.utc).isoformat()

    lines = [
        "# Notifications log",
        "",
        f"Updated: {generated_at}",
        f"Total notifications: {len(entries)}",
        "",
        "| # | Ticket ID | Title | Created At |",
        "| --- | --- | --- | --- |",
    ]

    if not entries:
        lines.append("| - | - | No notifications logged yet | - |")
        return "\n".join(lines) + "\n"

    for entry in entries:
        lines.append(
            "| {number} | {ticket_id} | {title} | {created_at} |".format(
                number=_escape_markdown_cell(entry.get("number")),
                ticket_id=_escape_markdown_cell(entry.get("ticket_id")),
                title=_escape_markdown_cell(entry.get("title")),
                created_at=_escape_markdown_cell(entry.get("created_at")),
            )
        )

    return "\n".join(lines) + "\n"


def _write_notifications_log(payload: dict[str, Any]) -> None:
    _ensure_parent()
    NOTIFICATIONS_LOG_PATH.write_text(_build_notifications_log(payload), encoding="utf-8")


def _save_registry(payload: dict[str, Any]) -> None:
    _ensure_parent()
    REGISTRY_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_notifications_log(payload)


def register_notification(ticket_id: str, title: str, created_at: str) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    payload = _load_registry()
    entries = payload["entries"]

    for entry in entries:
        if str(entry.get("ticket_id") or "").strip().upper() == normalized_ticket_id:
            _write_notifications_log(payload)
            return entry

    next_number = 1
    if entries:
        next_number = max(int(entry.get("number") or 0) for entry in entries) + 1

    entry = {
        "number": next_number,
        "title": title,
        "ticket_id": normalized_ticket_id,
        "created_at": created_at,
    }
    entries.append(entry)
    _save_registry(payload)
    return entry