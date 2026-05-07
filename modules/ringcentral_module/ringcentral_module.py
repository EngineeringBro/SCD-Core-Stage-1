from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any

from modules import general_module


MODULE_ID = "ringcentral"
DISPLAY_NAME = "ringcentral module"
VERSION = "v1.0"

RINGCENTRAL_REPORTER_EMAIL = "notify@ringcentral.com"
RINGCENTRAL_ALERT_TOPIC = "Ring Central Alert"
SPAM_TOPIC = "Spam"
SPAM_RESOLUTION = "Dismissed"
SPAM_ROOT_CAUSE = "Unknown"
PHONE_PATTERN = re.compile(r"(\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4})")
SPAM_SIGNAL_PATTERNS = {
    "suspected_robocall": re.compile(r"suspected robocall", re.IGNORECASE),
    "unknown_fax": re.compile(r"new fax message.*(unknown|<no callerid>)", re.IGNORECASE),
    "loan_or_interest": re.compile(r"interest rates|student loan", re.IGNORECASE),
    "delivery_or_warranty": re.compile(r"delivery attempt|auto[- ]warranty|warranty pitch", re.IGNORECASE),
    "parts_sales_pitch": re.compile(r"parts for sale|screen protector|back glass|replacement screen|lcd", re.IGNORECASE),
}


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


def get_fields(ticket_details: dict[str, Any]) -> dict[str, Any]:
    issue = ticket_details.get("issue")
    if not isinstance(issue, dict):
        return {}
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return {}
    return fields


def extract_reporter_email(ticket_details: dict[str, Any]) -> str:
    reporter = get_fields(ticket_details).get("reporter")
    if not isinstance(reporter, dict):
        return ""
    return normalize_whitespace(str(reporter.get("emailAddress") or reporter.get("email") or "")).lower()


def extract_summary(ticket_details: dict[str, Any]) -> str:
    return normalize_whitespace(str(get_fields(ticket_details).get("summary") or ""))


def extract_created_at(ticket_details: dict[str, Any]) -> str:
    return str(get_fields(ticket_details).get("created") or "").strip()


def extract_description_text(ticket_details: dict[str, Any]) -> str:
    return normalize_whitespace(extract_text(get_fields(ticket_details).get("description") or ""))


def extract_comment_text(ticket_details: dict[str, Any]) -> str:
    parts: list[str] = []
    comments = ticket_details.get("comments")
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict):
                parts.append(normalize_whitespace(extract_text(comment.get("body") or "")))
            else:
                parts.append(normalize_whitespace(str(comment)))
    return "\n".join(part for part in parts if part)


def extract_phone_number(summary: str, description_text: str, comment_text: str) -> str:
    for source in (summary, description_text, comment_text):
        match = PHONE_PATTERN.search(source)
        if match:
            return normalize_whitespace(match.group(1))
    return ""


def extract_caller_label(summary: str, phone_number: str) -> str:
    cleaned = summary
    for prefix in ("New Voice Message from ", "New Call from ", "New Fax Message from "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break

    if phone_number:
        cleaned = cleaned.replace(phone_number, "")
    if " on " in cleaned:
        cleaned = cleaned.split(" on ", 1)[0]
    return normalize_whitespace(cleaned)


def detect_spam_signals(summary: str, description_text: str, comment_text: str) -> list[str]:
    combined_text = "\n".join(part for part in [summary, description_text, comment_text] if part)
    matched_signals: list[str] = []
    for label, pattern in SPAM_SIGNAL_PATTERNS.items():
        if pattern.search(combined_text):
            matched_signals.append(label)
    return matched_signals


def parse_created_datetime(created_at: str) -> datetime | None:
    if not created_at:
        return None
    normalized = created_at.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def build_callback_window(created_at: str) -> tuple[str, str]:
    created_dt = parse_created_datetime(created_at)
    if created_dt is None:
        return "Unknown", "Unknown"
    start = created_dt - timedelta(hours=1)
    end = created_dt + timedelta(hours=1)
    return format_datetime(start), format_datetime(end)


def format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M %z")


def format_callback_hours(value: datetime) -> str:
    formatted = value.strftime("%I:%M %p")
    return formatted.lstrip("0")


def build_transcript_preview(description_text: str, comment_text: str) -> str:
    combined = description_text or comment_text
    if not combined:
        return "None"
    if len(combined) <= 280:
        return combined
    return combined[:277].rstrip() + "..."


def has_transcript(description_text: str, comment_text: str) -> bool:
    return bool((description_text or "").strip() or (comment_text or "").strip())


def build_spam_issue_body(
    ticket_id: str,
    summary: str,
    created_at: str,
    phone_number: str,
    caller_label: str,
    spam_signals: list[str],
    transcript_preview: str,
) -> str:
    lines = [
        "## Resolution",
        "",
        f"This appears to be a RingCentral Spam ticket, it is safe to dismiss.\n\nRun the Execute workflow to close {ticket_id} automatically:",
        "1. Leaves an internal AI note.",
        "2. Assigns ticket to you.",
        "3. Logs 3 mins to your time.",
        "4. Fill fields and change status to Dismissed.",
        "",
        "## Detection",
        "",
        f"- Ticket ID: {ticket_id}",
        f"- Summary: {summary or 'None'}",
        f"- Caller label: {caller_label or 'Unknown'}",
        f"- Caller number: {phone_number or 'Unknown'}",
        f"- Ticket created at: {created_at or 'Unknown'}",
        "- RingCentral subtype: spam_robocall",
        f"- Matched signals: {', '.join(spam_signals) if spam_signals else 'None'}",
        "",
        "## Transcript Preview",
        "",
        transcript_preview,
        "",
        "## Suggested Fields",
        "",
        f"- Topic: {SPAM_TOPIC}",
        f"- Resolution: {SPAM_RESOLUTION}",
        f"- Root cause: {SPAM_ROOT_CAUSE}",
    ]
    return "\n".join(lines)


def build_callback_issue_body(
    ticket_id: str,
    summary: str,
    created_at: str,
    phone_number: str,
    caller_label: str,
    callback_window_start: str,
    callback_window_end: str,
    transcript_preview: str,
    *,
    is_voicemail: bool,
    refined_summary: str = "",
    helpful_articles: list[tuple[str, str]] | None = None,
) -> str:
    created_dt = parse_created_datetime(created_at)
    if created_dt is None:
        created_at_display = created_at or "Unknown"
        callback_window_display = f"{callback_window_start} to {callback_window_end}"
    else:
        created_at_display = str(created_dt)
        start_dt = parse_created_datetime(created_at)
        if start_dt is None:
            callback_window_display = f"{callback_window_start} to {callback_window_end}"
        else:
            callback_window_display = f"{format_callback_hours(start_dt - timedelta(hours=1))} to {format_callback_hours(start_dt + timedelta(hours=1))}"

    resolution_text = (
        "This appears to be a RingCentral voice mail ticket and does not look like obvious spam. "
        "Please call the number back within the suggested window and update the Jira ticket with the outcome."
        if is_voicemail
        else "This appears to be a RingCentral missed call ticket and does not look like obvious spam. "
        "Please call the number back within the suggested window and update the Jira ticket with the outcome."
    )

    lines = [
        "## Resolution",
        "",
        resolution_text,
        "",
        "## Useful Details",
        "",
        f"- Ticket ID: {ticket_id}",
        f"- Caller number: {phone_number or 'Unknown'}",
        f"- Optimal callback hours: {callback_window_display}",
        f"- Summary: {summary or 'None'}",
        f"- Caller label: {caller_label or 'Unknown'}",
        f"- Ticket created at: {created_at_display}",
    ]

    if is_voicemail:
        lines.extend(
            [
                "",
                "## Transcript Preview",
                "",
                transcript_preview,
            ]
        )

        if refined_summary:
            lines.extend(
                [
                    "",
                    "## Refined Summary",
                    "",
                    refined_summary,
                ]
            )

        if helpful_articles:
            lines.extend(
                [
                    "",
                    "## Helpful Articles",
                    "",
                ]
            )
            for title, url in helpful_articles[:3]:
                lines.append(f"- [{title}]({url})")

    return "\n".join(lines)


def enrich_voicemail(ticket_id: str, ticket_details: dict[str, Any]) -> tuple[str, list[tuple[str, str]], list[str]]:
    if not os.environ.get("COPILOT_TOKEN", "").strip():
        return "", [], ["Voicemail enrichment skipped: COPILOT_TOKEN is not configured"]
    if general_module.OpenAI is None:
        return "", [], ["Voicemail enrichment skipped: openai package is not installed"]
    if not general_module.PAGES_ROOT.exists():
        return "", [], [f"Voicemail enrichment skipped: knowledge folder is missing: {general_module.PAGES_ROOT}"]

    ticket_context = general_module.build_ticket_context(ticket_id, ticket_details)
    query_tokens = general_module.expand_query_tokens(general_module.tokenize(ticket_context.combined_text))
    if not query_tokens:
        return "", [], ["Voicemail enrichment skipped: transcript did not contain searchable text"]

    article_candidates = general_module.find_article_candidates(query_tokens)
    step_groups = general_module.collect_relevant_groups(article_candidates, query_tokens)
    helpful_articles = [(group.article_title, group.article_url) for group in step_groups[:3]]
    helpful_articles = dedupe_articles(helpful_articles)

    prompt = build_voicemail_summary_prompt(ticket_context, helpful_articles)
    client = general_module.OpenAI(
        api_key=os.environ.get("COPILOT_TOKEN", "").strip(),
        base_url=general_module.COPILOT_BASE_URL,
    )

    # pylint: disable=broad-exception-caught
    try:
        response = client.chat.completions.create(
            model=general_module.resolve_copilot_model(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You refine voicemail transcripts for internal support agents. "
                        "Use only the supplied transcript and ticket context. Do not invent facts. "
                        "Return a short markdown paragraph or bullets only."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            max_tokens=220,
            temperature=0.1,
        )
    except Exception as exc:
        return "", helpful_articles, [f"Voicemail enrichment fallback used: {type(exc).__name__}: {exc}"]

    refined_summary = str(response.choices[0].message.content or "").strip()
    if refined_summary.startswith("```"):
        refined_summary = refined_summary.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not refined_summary:
        return "", helpful_articles, ["Voicemail enrichment fallback used: model returned no usable summary"]

    return refined_summary, helpful_articles, [f"Voicemail enrichment model: {general_module.resolve_copilot_model()}"]


def build_voicemail_summary_prompt(
    ticket_context: general_module.TicketContext,
    helpful_articles: list[tuple[str, str]],
) -> str:
    payload = {
        "ticket_id": ticket_context.ticket_id,
        "summary": ticket_context.summary,
        "description": ticket_context.description,
        "comments": ticket_context.comments[:5],
    }
    articles = [{"title": title, "url": url} for title, url in helpful_articles]
    return (
        "Refine this RingCentral voicemail transcript into a short internal summary for a human support agent.\n\n"
        "Requirements:\n"
        "- Keep it concise.\n"
        "- State the likely caller intent if it is present.\n"
        "- Mention any ticket number, customer name, location, or issue keywords if present.\n"
        "- If the transcript is unclear, say exactly that instead of guessing.\n"
        "- If helpful articles are provided, end with one short sentence naming which article(s) may help.\n\n"
        f"Ticket context:\n```json\n{ticket_context_json(payload)}\n```\n\n"
        f"Helpful articles:\n```json\n{ticket_context_json(articles)}\n```"
    )


def ticket_context_json(value: Any) -> str:
    return general_module.json.dumps(value, indent=2, ensure_ascii=True)


def dedupe_articles(articles: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for article in articles:
        if article in seen:
            continue
        seen.add(article)
        deduped.append(article)
    return deduped


def run(ticket_id: str, ticket_details: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    if not normalized_ticket_id:
        raise ValueError("ticket_id is required")
    if not isinstance(ticket_details, dict) or not ticket_details:
        raise ValueError("full ticket_details are required")

    reporter_email = extract_reporter_email(ticket_details)
    if reporter_email != RINGCENTRAL_REPORTER_EMAIL:
        raise ValueError(
            f"{normalized_ticket_id} reporter '{reporter_email or '(blank)'}' is not supported by {MODULE_ID}"
        )

    summary = extract_summary(ticket_details)
    created_at = extract_created_at(ticket_details)
    description_text = extract_description_text(ticket_details)
    comment_text = extract_comment_text(ticket_details)
    phone_number = extract_phone_number(summary, description_text, comment_text)
    caller_label = extract_caller_label(summary, phone_number)
    spam_signals = detect_spam_signals(summary, description_text, comment_text)
    transcript_preview = build_transcript_preview(description_text, comment_text)
    is_voicemail = has_transcript(description_text, comment_text)

    if spam_signals:
        return {
            "recommendation": "ringcentral_spam_safe_to_dismiss",
            "body": build_spam_issue_body(
                normalized_ticket_id,
                summary,
                created_at,
                phone_number,
                caller_label,
                spam_signals,
                transcript_preview,
            ),
            "notes": [
                f"Reporter email: {reporter_email}",
                "RingCentral subtype: spam_robocall",
                f"Matched spam signals: {', '.join(spam_signals)}",
            ],
            "ringcentral_subtype": "spam_robocall",
            "matched_spam_signals": spam_signals,
            "caller_number": phone_number,
        }

    callback_window_start, callback_window_end = build_callback_window(created_at)
    recommendation = "ringcentral_voicemail_callback_needed" if is_voicemail else "ringcentral_missed_call_callback_needed"
    ringcentral_subtype = "voicemail_callback_needed" if is_voicemail else "missed_call_callback_needed"
    refined_summary = ""
    helpful_articles: list[tuple[str, str]] = []
    enrichment_notes: list[str] = []
    if is_voicemail:
        refined_summary, helpful_articles, enrichment_notes = enrich_voicemail(normalized_ticket_id, ticket_details)
    return {
        "recommendation": recommendation,
        "body": build_callback_issue_body(
            normalized_ticket_id,
            summary,
            created_at,
            phone_number,
            caller_label,
            callback_window_start,
            callback_window_end,
            transcript_preview,
            is_voicemail=is_voicemail,
            refined_summary=refined_summary,
            helpful_articles=helpful_articles,
        ),
        "notes": [
            f"Reporter email: {reporter_email}",
            f"RingCentral subtype: {ringcentral_subtype}",
            f"Caller number: {phone_number or 'Unknown'}",
            f"Suggested callback window: {callback_window_start} to {callback_window_end}",
            *enrichment_notes,
        ],
        "ringcentral_subtype": ringcentral_subtype,
        "caller_number": phone_number,
        "callback_window_start": callback_window_start,
        "callback_window_end": callback_window_end,
        "has_transcript": is_voicemail,
        "refined_summary": refined_summary,
        "helpful_articles": helpful_articles,
    }