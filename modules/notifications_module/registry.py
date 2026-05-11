from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODULE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = MODULE_DIR / "output"
EXECUTE_LOG_PATH = OUTPUT_DIR / "Notifications execute log.md"


def _ensure_parent() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _ensure_execute_log() -> None:
    _ensure_parent()
    if EXECUTE_LOG_PATH.exists():
        return

    EXECUTE_LOG_PATH.write_text(
        "# Notifications execute log\n\n"
        "| Logged At (UTC) | Ticket ID | Title | Ticket Created At |\n"
        "| --- | --- | --- | --- |\n",
        encoding="utf-8",
    )


def register_notification(ticket_id: str, title: str, created_at: str) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    logged_at = datetime.now(timezone.utc).isoformat()
    _ensure_execute_log()

    with EXECUTE_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(
            "| {logged_at} | {ticket_id} | {title} | {created_at} |\n".format(
                logged_at=_escape_markdown_cell(logged_at),
                ticket_id=_escape_markdown_cell(normalized_ticket_id),
                title=_escape_markdown_cell(title),
                created_at=_escape_markdown_cell(created_at),
            )
        )

    return {
        "ticket_id": normalized_ticket_id,
        "title": title,
        "created_at": created_at,
        "logged_at": logged_at,
    }