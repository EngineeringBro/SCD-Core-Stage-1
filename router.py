from __future__ import annotations

from typing import Any


ALLOWED_MODULE_NAMES = {
    "notification",
    "general",
    "orphaned_transaction",
    "spam",
    "ringcentral",
}

NOTIFICATION_SENDER_EMAILS = {
    "mail@repairq.io",
    "noreply@repairq.io",
    "azure-noreply@microsoft.com",
}

RINGCENTRAL_SENDER_EMAILS = {
    "notify@ringcentral.com",
}

SPAM_SENDER_DOMAINS = {
    "elekworld.ltd",
    "elekworld.cn",
    "topyetlcd.com",
    "tendernews.com",
    "merchant-email.fiserv.com",
    "jacktelecom.com",
}


def route_ticket(ticket_id: str, ticket_details: dict[str, Any]) -> dict[str, Any]:
    if not ticket_id.strip():
        raise ValueError("ticket_id is required")
    if not ticket_details:
        raise ValueError("ticket_details are required")

    routing_text = build_routing_text(ticket_details)
    route_result = select_route(ticket_id, ticket_details, routing_text)
    module_name = str(route_result.get("module_name") or "").strip()
    if module_name not in ALLOWED_MODULE_NAMES:
        raise ValueError(f"invalid module selected: {module_name}")

    return {
        "ticket_id": ticket_id.strip(),
        **route_result,
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


def extract_reporter_email(ticket_details: dict[str, Any]) -> str:
    issue = ticket_details.get("issue")
    if not isinstance(issue, dict):
        return ""

    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return ""

    reporter = fields.get("reporter")
    if not isinstance(reporter, dict):
        return ""

    return normalize_whitespace(str(reporter.get("emailAddress") or reporter.get("email") or "")).lower()


def extract_reporter_domain(ticket_details: dict[str, Any]) -> str:
    reporter_email = extract_reporter_email(ticket_details)
    if "@" not in reporter_email:
        return ""
    return reporter_email.rsplit("@", 1)[1]


def notification_sender_has_profile_match(ticket_id: str, ticket_details: dict[str, Any]) -> bool:
    from modules.notifications_module.notification_matcher import classify_ticket

    classification = classify_ticket(ticket_id.strip(), ticket_details)
    return bool(classification.matched_case_id)


def select_route(ticket_id: str, ticket_details: dict[str, Any], combined_text: str) -> dict[str, Any]:
    if "orphaned transaction" in combined_text:
        return {"module_name": "orphaned_transaction"}

    if extract_reporter_email(ticket_details) in NOTIFICATION_SENDER_EMAILS:
        if notification_sender_has_profile_match(ticket_id, ticket_details):
            return {"module_name": "notification"}
        return {"module_name": "general"}

    if extract_reporter_email(ticket_details) in RINGCENTRAL_SENDER_EMAILS:
        return {"module_name": "ringcentral"}

    if extract_reporter_domain(ticket_details) in SPAM_SENDER_DOMAINS:
        return {"module_name": "spam"}

    return {"module_name": "general"}