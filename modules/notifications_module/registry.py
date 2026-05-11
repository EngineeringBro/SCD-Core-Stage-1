from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODULE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = MODULE_DIR / "output"
NOTIFICATIONS_LOG_PATH = OUTPUT_DIR / "Notifications log.md"


def _ensure_parent() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _is_data_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    if stripped.startswith("| # |") or stripped.startswith("| --- |"):
        return False
    if "No notifications logged yet" in stripped:
        return False
    return True


def _load_existing_rows() -> list[str]:
    if not NOTIFICATIONS_LOG_PATH.exists():
        return []

    lines = NOTIFICATIONS_LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if _is_data_row(line)]


def _write_notifications_log(rows: list[str], generated_at: str) -> None:
    _ensure_parent()
    lines = [
        "# Notifications log",
        "",
        f"Updated: {generated_at}",
        f"Total notifications: {len(rows)}",
        "",
        "| # | Ticket ID | Title | Created At |",
        "| --- | --- | --- | --- |",
    ]

    if rows:
        lines.extend(rows)
    else:
        lines.append("| - | - | No notifications logged yet | - |")

    NOTIFICATIONS_LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def register_notification(ticket_id: str, title: str, created_at: str) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    logged_at = datetime.now(timezone.utc).isoformat()
    rows = _load_existing_rows()
    next_number = len(rows) + 1
    rows.append(
        "| {number} | {ticket_id} | {title} | {created_at} |".format(
            number=_escape_markdown_cell(next_number),
            ticket_id=_escape_markdown_cell(normalized_ticket_id),
            title=_escape_markdown_cell(title),
            created_at=_escape_markdown_cell(created_at),
        )
    )
    _write_notifications_log(rows, logged_at)

    return {
        "ticket_id": normalized_ticket_id,
        "title": title,
        "created_at": created_at,
        "logged_at": logged_at,
    }