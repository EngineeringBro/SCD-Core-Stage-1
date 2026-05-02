from __future__ import annotations

from typing import Any


ALLOWED_MODULE_NAMES = {
    "notification",
    "general",
    "orphaned_transaction",
    "spam",
}


def route_ticket(ticket_id: str, ticket_details: dict[str, Any]) -> dict[str, str]:
    if not ticket_id.strip():
        raise ValueError("ticket_id is required")
    if not ticket_details:
        raise ValueError("ticket_details are required")

    combined_text = build_combined_text(ticket_details)
    module_name = select_module_name(combined_text)
    if module_name not in ALLOWED_MODULE_NAMES:
        raise ValueError(f"invalid module selected: {module_name}")

    return {
        "ticket_id": ticket_id.strip(),
        "module_name": module_name,
    }


def build_combined_text(ticket_details: dict[str, Any]) -> str:
    parts: list[str] = []

    issue = ticket_details.get("issue")
    if isinstance(issue, dict):
        parts.append(str(issue))

    comments = ticket_details.get("comments")
    if isinstance(comments, list):
        parts.extend(str(comment) for comment in comments)

    return "\n".join(parts).lower()


def select_module_name(combined_text: str) -> str:
    if "orphaned transaction" in combined_text:
        return "orphaned_transaction"

    if any(keyword in combined_text for keyword in ["spam", "robocall", "junk"]):
        return "spam"

    if any(keyword in combined_text for keyword in ["notification", "alert", "ringcentral"]):
        return "notification"

    return "general"