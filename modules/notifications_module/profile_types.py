from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TicketContext:
    ticket_id: str
    summary: str
    topic: str
    status: str
    description: str
    reporter_email: str
    comments_count: int
    combined_text: str


@dataclass(frozen=True)
class MatchRule:
    reporter_emails: tuple[str, ...] = ()
    summary_contains: tuple[str, ...] = ()
    summary_patterns: tuple[str, ...] = ()
    description_contains: tuple[str, ...] = ()
    description_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotificationProfile:
    case_id: str
    display_name: str
    historical_total: int
    historical_zero_comment_closes: int
    dominant_resolutions: tuple[str, ...]
    rule: MatchRule
    reasoning: str
    output_topic: str | None = None


@dataclass(frozen=True)
class ProfileMatch:
    profile: NotificationProfile
    score: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ClassificationResult:
    ticket_id: str
    recommendation: str
    matched_case_id: str | None
    matched_case_name: str | None
    output_topic: str | None
    evidence: tuple[str, ...]
    notes: list[str]
    context: TicketContext


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
    return str(value or "")


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split()).strip()


def build_ticket_context(ticket_id: str, ticket_details: dict[str, Any]) -> TicketContext:
    issue = ticket_details.get("issue") or {}
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    if not isinstance(fields, dict):
        fields = {}
    comments = ticket_details.get("comments")
    comment_count = len(comments) if isinstance(comments, list) else 0
    summary = normalize_whitespace(str(fields.get("summary") or ""))
    topic = normalize_whitespace(str((fields.get("customfield_10170") or {}).get("value") or ""))
    status = normalize_whitespace(str((fields.get("status") or {}).get("name") or ""))
    description = normalize_whitespace(extract_text(fields.get("description") or ""))
    reporter = fields.get("reporter") or {}
    reporter_email = normalize_whitespace(str(reporter.get("emailAddress") or reporter.get("email") or "")).lower()
    parts = [summary, topic, status, description]
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict):
                parts.append(normalize_whitespace(extract_text(comment.get("body") or "")))
            else:
                parts.append(normalize_whitespace(str(comment)))
    combined_text = "\n".join(part for part in parts if part)
    return TicketContext(ticket_id=ticket_id, summary=summary, topic=topic, status=status, description=description, reporter_email=reporter_email, comments_count=comment_count, combined_text=combined_text)