from __future__ import annotations

from typing import Any


MODULE_ID = "spam"
DISPLAY_NAME = "spam module"
VERSION = "v1.0"

SPAM_SENDER_DOMAINS = {
    "elekworld.ltd",
    "elekworld.cn",
    "topyetlcd.com",
    "tendernews.com",
    "merchant-email.fiserv.com",
    "jacktelecom.com",
}
SPAM_OUTPUT_TOPIC = "Spam"
SPAM_OUTPUT_RESOLUTION = "Dismissed"
SPAM_OUTPUT_ROOT_CAUSE = "Unknown"


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

    return str(reporter.get("emailAddress") or reporter.get("email") or "").strip().lower()


def extract_reporter_domain(ticket_details: dict[str, Any]) -> str:
    reporter_email = extract_reporter_email(ticket_details)
    if "@" not in reporter_email:
        return ""
    return reporter_email.rsplit("@", 1)[1]


def extract_summary(ticket_details: dict[str, Any]) -> str:
    issue = ticket_details.get("issue")
    if not isinstance(issue, dict):
        return ""

    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return ""

    return str(fields.get("summary") or "").strip()


def build_issue_body(ticket_id: str, reporter_email: str, reporter_domain: str, summary: str) -> str:
    lines = [
        "## Suggestion",
        "",
        "This ticket came from a red-flag sender domain that we treat as spam.",
        "",
        "## Output Fields",
        "",
        f"- Topic: {SPAM_OUTPUT_TOPIC}",
        f"- Resolution: {SPAM_OUTPUT_RESOLUTION}",
        f"- Root cause: {SPAM_OUTPUT_ROOT_CAUSE}",
        "",
        "## Detection",
        "",
        f"- Ticket ID: {ticket_id}",
        f"- Reporter email: {reporter_email or '(blank)'}",
        f"- Reporter domain: {reporter_domain or '(blank)'}",
        f"- Summary: {summary or 'None'}",
        "",
        "## Red-Flag Domains",
        "",
    ]

    for domain in sorted(SPAM_SENDER_DOMAINS):
        marker = " (matched)" if domain == reporter_domain else ""
        lines.append(f"- {domain}{marker}")

    lines.extend(
        [
            "",
            "## Handling",
            "",
            "All tickets from these sender domains are routed to the spam module and handled the same way.",
        ]
    )
    return "\n".join(lines)


def run(ticket_id: str, ticket_details: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    if not normalized_ticket_id:
        raise ValueError("ticket_id is required")
    if not isinstance(ticket_details, dict) or not ticket_details:
        raise ValueError("full ticket_details are required")

    reporter_email = extract_reporter_email(ticket_details)
    reporter_domain = extract_reporter_domain(ticket_details)
    if reporter_domain not in SPAM_SENDER_DOMAINS:
        allowed_domains = ", ".join(sorted(SPAM_SENDER_DOMAINS))
        raise ValueError(
            f"{normalized_ticket_id} reporter domain '{reporter_domain or '(blank)'}' is not supported by {MODULE_ID}. "
            f"Allowed domains: {allowed_domains}"
        )

    summary = extract_summary(ticket_details)

    return {
        "recommendation": "spam_sender_match",
        "body": build_issue_body(normalized_ticket_id, reporter_email, reporter_domain, summary),
        "notes": [
            f"Reporter email: {reporter_email or '(blank)'}",
            f"Matched spam sender domain: {reporter_domain}",
            "Routing rule: sender-domain exact match",
        ],
        "output_topic": SPAM_OUTPUT_TOPIC,
        "output_resolution": SPAM_OUTPUT_RESOLUTION,
        "output_root_cause": SPAM_OUTPUT_ROOT_CAUSE,
        "matched_sender_email": reporter_email,
        "matched_sender_domain": reporter_domain,
    }