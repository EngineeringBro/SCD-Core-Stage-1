from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MODULE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = MODULE_DIR / "output"
REGISTRY_PATH = OUTPUT_DIR / "notification_registry.json"


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


def _save_registry(payload: dict[str, Any]) -> None:
    _ensure_parent()
    REGISTRY_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def register_notification(ticket_id: str, title: str, created_at: str) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    payload = _load_registry()
    entries = payload["entries"]

    for entry in entries:
        if str(entry.get("ticket_id") or "").strip().upper() == normalized_ticket_id:
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