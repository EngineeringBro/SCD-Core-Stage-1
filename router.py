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

    routing_text = build_routing_text(ticket_details)
    module_name = select_module_name(routing_text)
    if module_name not in ALLOWED_MODULE_NAMES:
        raise ValueError(f"invalid module selected: {module_name}")

    return {
        "ticket_id": ticket_id.strip(),
        "module_name": module_name,
    }


def build_routing_text(ticket_details: dict[str, Any]) -> str:
    parts: list[str] = []

    issue = ticket_details.get("issue")
    if isinstance(issue, dict):
        fields = issue.get("fields")
        if isinstance(fields, dict):
            parts.extend(
                [
                    normalize_whitespace(str(fields.get("summary") or "")),
                    normalize_whitespace(str((fields.get("customfield_10170") or {}).get("value") or "")),
                    normalize_whitespace(str((fields.get("status") or {}).get("name") or "")),
                    normalize_whitespace(extract_text(fields.get("description") or "")),
                ]
            )

    comments = ticket_details.get("comments")
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict):
                parts.append(normalize_whitespace(extract_text(comment.get("body") or "")))
            else:
                parts.append(normalize_whitespace(str(comment)))

    return "\n".join(part for part in parts if part).lower()


def extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(extract_text(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), list):
            return " ".join(extract_text(item) for item in value["content"])
        return " ".join(extract_text(item) for item in value.values())
    return str(value)


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split()).strip()


def select_module_name(combined_text: str) -> str:
    if "orphaned transaction" in combined_text:
        return "orphaned_transaction"

    if any(keyword in combined_text for keyword in ["spam", "robocall", "junk"]):
        return "spam"

    if any(keyword in combined_text for keyword in ["notification", "alert", "ringcentral"]):
        return "notification"

    return "general"