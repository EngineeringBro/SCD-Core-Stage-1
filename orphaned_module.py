from __future__ import annotations


MODULE_ID = "orphaned_transaction"
DISPLAY_NAME = "orphaned transaction module"
VERSION = "v1.1"


ALLOWED_TICKET_IDS = {
    "SCD-142125",
}


def run(ticket_id: str) -> str:
    normalized_ticket_id = ticket_id.strip().upper()
    if not normalized_ticket_id:
        raise ValueError("ticket_id is required")

    if normalized_ticket_id in ALLOWED_TICKET_IDS:
        return "yes"

    return "no"