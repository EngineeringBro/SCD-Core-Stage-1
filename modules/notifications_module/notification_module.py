from __future__ import annotations

from typing import Any

from modules.notifications_module.notification_matcher import (
    NOTIFICATION_OUTPUT_RESOLUTION,
    NOTIFICATION_OUTPUT_ROOT_CAUSE,
    classify_ticket,
)
from modules.notifications_module.registry import REGISTRY_PATH, register_notification


MODULE_ID = "notification"
DISPLAY_NAME = "notifications module"
VERSION = "v1.0"


def extract_ticket_title(ticket_details: dict[str, Any]) -> str:
    issue = ticket_details.get("issue")
    if not isinstance(issue, dict):
        return ""
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return ""
    return str(fields.get("summary") or "").strip()


def extract_created_at(ticket_details: dict[str, Any]) -> str:
    issue = ticket_details.get("issue")
    if not isinstance(issue, dict):
        return ""
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return ""
    return str(fields.get("created") or "").strip()


def build_issue_body(result: Any) -> str:
    output_topic = result.output_topic or "Notification"
    article = "an" if output_topic[:1].lower() in {"a", "e", "i", "o", "u"} else "a"

    lines = [
        "## Suggestion",
        "",
        f"This is {article} {output_topic} notification ticket, safe to close and document.",
        "",
        "## Resolution",
        "",
        f"Run the Execute workflow to close {result.ticket_id} automatically:",
        "1. Leaves an internal AI note.",
        "2. Assigns ticket to you.",
        "3. Logs 3 mins to your time.",
        "4. Fill fields and change status to Done.",
        "",
        "## Output Fields",
        "",
        f"- Topic: {output_topic}",
        f"- Resolution: {NOTIFICATION_OUTPUT_RESOLUTION}",
        f"- Root cause: {NOTIFICATION_OUTPUT_ROOT_CAUSE}",
        "",
        "## Detection",
        "",
        f"- Ticket ID: {result.ticket_id}",
        f"- Detected case: {result.matched_case_id or 'none'}",
        f"- Case name: {result.matched_case_name or 'No match'}",
        f"- Summary: {result.context.summary or 'None'}",
        f"- Description: {result.context.description or 'None'}",
        f"- Reporter: {result.context.reporter_email or '(blank)'}",
        f"- Comments count: {result.context.comments_count}",
        "",
        "## Evidence",
        "",
    ]
    for item in result.evidence:
        lines.append(f"- {item}")

    if not result.evidence:
        lines.append("- None")

    if result.matched_case_id is not None:
        lines.extend(
            [
                "",
                "## Historical Pattern",
                "",
                result.notes[-1].replace("Historical closed without comment: ", "- Historical closed without comment: "),
            ]
        )

    return "\n".join(lines)


def run(ticket_id: str, ticket_details: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    if not normalized_ticket_id:
        raise ValueError("ticket_id is required")
    if not isinstance(ticket_details, dict) or not ticket_details:
        raise ValueError("full ticket_details are required")

    result = classify_ticket(normalized_ticket_id, ticket_details)
    ticket_title = extract_ticket_title(ticket_details)
    created_at = extract_created_at(ticket_details)
    registry_entry = register_notification(normalized_ticket_id, ticket_title, created_at)

    return {
        "recommendation": result.recommendation,
        "body": build_issue_body(result),
        "notes": result.notes,
        "output_topic": result.output_topic,
        "output_resolution": NOTIFICATION_OUTPUT_RESOLUTION,
        "output_root_cause": NOTIFICATION_OUTPUT_ROOT_CAUSE,
        "registry_number": registry_entry.get("number"),
        "ticket_title": registry_entry.get("title"),
        "ticket_created_at": registry_entry.get("created_at"),
        "registry_path": str(REGISTRY_PATH),
    }