from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

try:
    import openai
    from openai import OpenAI
except ImportError:
    openai = None  # type: ignore
    OpenAI = None  # type: ignore

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore


MODULE_ID = "ringcentral"
DISPLAY_NAME = "ringcentral module"
VERSION = "v1.0"

RINGCENTRAL_REPORTER_EMAIL = "notify@ringcentral.com"
SPAM_TOPIC = "Spam"
SPAM_RESOLUTION = "Dismissed"
SPAM_ROOT_CAUSE = "Unknown"
PHONE_PATTERN = re.compile(r"(\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4})")
SUMMARY_TIMESTAMP_PATTERN = re.compile(
    r"\bon\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\b",
    re.IGNORECASE,
)
SPAM_SIGNAL_PATTERNS = {
    "suspected_robocall": re.compile(r"suspected robocall", re.IGNORECASE),
    "unknown_fax": re.compile(r"new fax message.*(unknown|<no callerid>)", re.IGNORECASE),
    "loan_or_interest": re.compile(r"interest rates|student loan", re.IGNORECASE),
    "delivery_or_warranty": re.compile(r"delivery attempt|auto[- ]warranty|warranty pitch", re.IGNORECASE),
    "parts_sales_pitch": re.compile(r"parts for sale|screen protector|back glass|replacement screen|lcd", re.IGNORECASE),
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge"
KNOWLEDGEBASE_ROOT = KNOWLEDGE_ROOT / "knowledgebase"
PAGES_ROOT = KNOWLEDGEBASE_ROOT / "spaces" / "SCD" / "pages"
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"
DEFAULT_COPILOT_MODEL = "claude-sonnet-4.6"
TRANSCRIPTION_MODEL = os.environ.get("VOICEMAIL_TRANSCRIPTION_MODEL", "base.en").strip() or "base.en"
MAX_ARTICLES = 5
MAX_INITIAL_CANDIDATES = 40
MAX_GROUPS = 8
MAX_IMAGES = 3
DECORATIVE_IMAGE_MARKERS = (
    "instructional -",
    "technical -",
    "control -",
)
HEADING_TAGS = {f"h{level}" for level in range(1, 7)}
TEXT_BLOCK_TYPES = {"paragraph", "list_item"}
QUERY_EXPANSIONS = {
    "attach": {"assigned", "assign", "customer", "link", "linked"},
    "client": {"contact", "customer", "customers", "profile"},
    "clients": {"contact", "customer", "customers", "profiles"},
    "create": {"profile"},
    "created": {"profile"},
    "creating": {"profile"},
    "ticket": {"repair", "tickets"},
}
STOP_WORDS = {
    "add",
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "but",
    "can",
    "create",
    "created",
    "creating",
    "existing",
    "for",
    "from",
    "have",
    "into",
    "just",
    "need",
    "needs",
    "new",
    "not",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "ticket",
    "with",
    "your",
}
OPENAI_CLIENT_EXCEPTIONS = (openai.APIError,) if openai is not None else (RuntimeError,)


@dataclass(frozen=True)
class TicketContext:
    ticket_id: str
    summary: str
    topic: str
    status: str
    description: str
    comments: list[str]
    transcript: str
    combined_text: str


@dataclass(frozen=True)
class PageCandidate:
    page_id: str
    title: str
    page_dir: Path
    web_url: str
    initial_score: int


@dataclass(frozen=True)
class StepGroup:
    article_title: str
    article_url: str
    heading_path: list[str]
    step_text: str
    context_text: str
    images: list[dict[str, str]]
    score: int


@dataclass
class Block:
    kind: str
    text: str = ""
    level: int | None = None
    src: str | None = None
    alt: str | None = None


@dataclass
class CaptureState:
    tag: str
    kind: str
    level: int | None = None
    parts: list[str] | None = None

    def __post_init__(self) -> None:
        if self.parts is None:
            self.parts = []


class ArticleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.capture: CaptureState | None = None
        self.list_depth = 0
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)

        if tag in {"script", "style"}:
            self.skip_depth += 1
            return

        if self.skip_depth:
            return

        if tag in {"ul", "ol"}:
            self.list_depth += 1
            return

        if tag == "li" and self.capture is None:
            self.capture = CaptureState(tag="li", kind="list_item")
            return

        if tag in HEADING_TAGS and self.capture is None:
            self.capture = CaptureState(tag=tag, kind="heading", level=int(tag[1]))
            return

        if tag == "p" and self.capture is None and self.list_depth == 0:
            self.capture = CaptureState(tag="p", kind="paragraph")
            return

        if tag == "br" and self.capture is not None:
            self.capture.parts.append("\n")
            return

        if tag == "img":
            src = attributes.get("src")
            if not src:
                return
            alt = normalize_whitespace(attributes.get("alt") or "")
            if is_decorative_image(src, alt):
                return
            self.blocks.append(Block(kind="image", src=src, alt=alt))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return

        if self.skip_depth:
            return

        if tag in {"ul", "ol"}:
            self.list_depth = max(0, self.list_depth - 1)
            return

        if self.capture is None or self.capture.tag != tag:
            return

        text = normalize_whitespace("".join(self.capture.parts))
        if text:
            self.blocks.append(Block(kind=self.capture.kind, text=text, level=self.capture.level))
        self.capture = None

    def handle_data(self, data: str) -> None:
        if self.skip_depth or self.capture is None:
            return
        self.capture.parts.append(data)


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
    return re.sub(r"\s+", " ", value).strip()


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


def extract_mp3_attachments(ticket_details: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = ticket_details.get("mp3_attachments")
    if not isinstance(attachments, list):
        return []
    return [attachment for attachment in attachments if isinstance(attachment, dict)]


def extract_phone_number(summary: str, *sources: str) -> str:
    for source in (summary, *sources):
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


def detect_spam_signals(summary: str, *sources: str) -> list[str]:
    combined_text = "\n".join(part for part in (summary, *sources) if part)
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


def parse_summary_datetime(summary: str, fallback: datetime | None = None) -> datetime | None:
    match = SUMMARY_TIMESTAMP_PATTERN.search(summary)
    if match is None:
        return None

    date_text, time_text = match.groups()
    try:
        parsed = datetime.strptime(f"{date_text} {time_text.upper()}", "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None

    if fallback is not None and fallback.tzinfo is not None:
        return parsed.replace(tzinfo=fallback.tzinfo)
    return parsed


def build_callback_window_from_datetime(value: datetime | None) -> tuple[str, str, str]:
    if value is None:
        return "Unknown", "Unknown", "Unknown"

    start = value - timedelta(hours=1)
    end = value + timedelta(hours=1)
    return format_datetime(start), format_datetime(end), f"{format_callback_hours(start)} to {format_callback_hours(end)}"


def format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M %z")


def format_callback_hours(value: datetime) -> str:
    formatted = value.strftime("%I:%M %p")
    return formatted.lstrip("0")


def build_transcript_preview(*sources: str, empty_value: str = "None") -> str:
    combined = next((source for source in sources if source.strip()), "")
    if not combined:
        return empty_value
    if len(combined) <= 280:
        return combined
    return combined[:277].rstrip() + "..."


def build_missing_transcript_message(transcription_notes: list[str], attachment_count: int) -> str:
    if transcription_notes:
        return transcription_notes[0]
    if attachment_count > 0:
        return "No transcript available. Audio attachment was fetched, but the transcription step did not return usable text."
    return "No transcript available. No supported audio attachment was fetched from the Jira ticket."


def summary_rejects_helpful_articles(refined_summary: str) -> bool:
    normalized = normalize_whitespace(refined_summary).lower()
    rejection_patterns = (
        r"no\s+relevant\s+helpful\s+articles?\s+apply(?:\s+here|\s+to\s+this\s+voicemail)?",
        r"no\s+relevant\s+articles?\s+apply(?:\s+here|\s+to\s+this\s+voicemail)?",
        r"no\s+helpful\s+articles?\s+apply(?:\s+here|\s+to\s+this\s+voicemail)?",
    )
    return any(re.search(pattern, normalized) for pattern in rejection_patterns)


def summary_supports_helpful_articles(refined_summary: str, helpful_articles: list[tuple[str, str]]) -> bool:
    normalized = normalize_whitespace(refined_summary).lower()
    if summary_rejects_helpful_articles(normalized):
        return False
    return any(title.lower() in normalized for title, _ in helpful_articles)


def strip_irrelevant_article_text(refined_summary: str) -> str:
    cleaned = refined_summary.strip()
    if not cleaned:
        return ""

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    kept_lines = [line for line in lines if not summary_rejects_helpful_articles(line)]
    if kept_lines:
        return "\n".join(kept_lines).strip()

    sentence_parts = re.split(r"(?<=[.!?])\s+", cleaned)
    kept_parts = [part.strip() for part in sentence_parts if part.strip() and not summary_rejects_helpful_articles(part)]
    return " ".join(kept_parts).strip()


def has_transcript(*sources: str) -> bool:
    return any(source.strip() for source in sources)


def is_voice_message_summary(summary: str) -> bool:
    return summary.lower().startswith("new voice message from ")


def select_voicemail_text(
    *,
    is_voice_message: bool,
    transcription_text: str,
    description_text: str,
    comment_text: str,
) -> str:
    if not is_voice_message:
        return next((source for source in (description_text, comment_text) if source.strip()), "")
    if transcription_text.strip():
        return transcription_text
    return ""


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
    phone_number: str,
    callback_window_display: str,
    *,
    is_voicemail: bool,
    refined_summary: str = "",
    helpful_articles: list[tuple[str, str]] | None = None,
) -> str:
    resolution_text = (
        "This appears to be a RingCentral voice message ticket and does not look like obvious spam. "
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
        f"- Ticket ID: {ticket_id}",
        f"- Caller number: {phone_number or 'Unknown'}",
        f"- Optimal callback hours: {callback_window_display}",
    ]

    if refined_summary:
        lines.extend(
            [
                "",
                refined_summary,
            ]
        )

    if is_voicemail and helpful_articles and summary_supports_helpful_articles(refined_summary, helpful_articles):
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


def build_voicemail_transcription_prompt(summary: str) -> str:
    return (
        "Transcribe this RingCentral voicemail accurately in plain text. "
        "Preserve phone numbers, names, store locations, invoice numbers, repair details, and callback requests. "
        "Do not summarize.\n\n"
        f"Ticket summary: {summary or 'Unknown'}"
    )


def normalize_transcription_result(result: Any) -> str:
    if isinstance(result, str):
        return normalize_whitespace(result)
    text_value = getattr(result, "text", None)
    if isinstance(text_value, str):
        return normalize_whitespace(text_value)
    return normalize_whitespace(str(result))


def guess_audio_suffix(filename: str, mime_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix:
        return suffix

    mime_map = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/x-mp3": ".mp3",
        "audio/x-mpeg-3": ".mp3",
        "audio/mpg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/3gpp": ".3gp",
        "audio/3gpp2": ".3g2",
    }
    return mime_map.get(mime_type.lower(), ".audio")


def build_offline_transcript_text(segments: list[Any]) -> str:
    return normalize_whitespace(" ".join(str(segment.text or "").strip() for segment in segments if str(segment.text or "").strip()))


def transcribe_voicemail(summary: str, mp3_attachments: list[dict[str, Any]]) -> tuple[str, list[str]]:
    if not mp3_attachments:
        return "", ["Voicemail transcription skipped: no supported audio attachment found"]
    if WhisperModel is None:
        return "", ["Voicemail transcription skipped: faster-whisper package is not installed"]

    attachment = mp3_attachments[0]
    filename = str(attachment.get("filename") or "voicemail.mp3")
    mime_type = str(attachment.get("mime_type") or "audio/mpeg")
    content_bytes = attachment.get("content_bytes")
    if not isinstance(content_bytes, (bytes, bytearray)) or not content_bytes:
        return "", ["Voicemail transcription skipped: attachment bytes were missing"]

    try:
        with NamedTemporaryFile(suffix=guess_audio_suffix(filename, mime_type), delete=True) as handle:
            handle.write(bytes(content_bytes))
            handle.flush()

            model = WhisperModel(TRANSCRIPTION_MODEL, device="cpu", compute_type="int8")
            segments_iter, _ = model.transcribe(
                handle.name,
                language="en",
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=build_voicemail_transcription_prompt(summary),
            )
            segments = list(segments_iter)
    except (OSError, RuntimeError, ValueError) as exc:
        return "", [f"Voicemail transcription failed: {type(exc).__name__}: {exc}"]

    transcript_text = build_offline_transcript_text(segments)
    if not transcript_text:
        return "", ["Voicemail transcription failed: model returned no usable transcript"]

    return transcript_text, [f"Voicemail transcription model: {TRANSCRIPTION_MODEL}"]


def build_ticket_context(
    ticket_id: str,
    ticket_details: dict[str, Any],
    transcript_text: str,
) -> TicketContext:
    issue = ticket_details.get("issue") or {}
    comments = ticket_details.get("comments") or []
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    if not isinstance(fields, dict):
        fields = {}

    summary = normalize_whitespace(str(fields.get("summary") or ""))
    topic = normalize_whitespace(str((fields.get("customfield_10170") or {}).get("value") or ""))
    status = normalize_whitespace(str((fields.get("status") or {}).get("name") or ""))
    use_transcript_only = bool(transcript_text.strip())
    description = "" if use_transcript_only else normalize_whitespace(extract_text(fields.get("description") or ""))

    comment_lines: list[str] = []
    if not use_transcript_only and isinstance(comments, list):
        for comment in comments:
            comment_source = comment.get("body") if isinstance(comment, dict) else comment
            comment_text = normalize_whitespace(extract_text(comment_source or ""))
            if comment_text:
                comment_lines.append(comment_text)

    parts = [summary, topic, status, description, transcript_text, *comment_lines]
    combined_text = "\n".join(part for part in parts if part)

    return TicketContext(
        ticket_id=ticket_id,
        summary=summary,
        topic=topic,
        status=status,
        description=description,
        comments=comment_lines,
        transcript=transcript_text,
        combined_text=combined_text,
    )


def find_article_candidates(query_tokens: set[str]) -> list[PageCandidate]:
    candidates: list[PageCandidate] = []

    for page_dir in sorted(path for path in PAGES_ROOT.iterdir() if path.is_dir()):
        page_json_path = page_dir / "page.json"
        if not page_json_path.exists():
            continue

        page_data = read_json(page_json_path)
        title = normalize_whitespace(str(page_data.get("title") or page_dir.name))
        slug_text = page_dir.name.replace("-", " ")
        title_score = score_text(query_tokens, f"{title} {slug_text}", title_multiplier=4)
        body_score = score_text(query_tokens, build_candidate_search_text(page_dir), title_multiplier=1)
        initial_score = (title_score * 3) + min(body_score, 24)
        if initial_score <= 0:
            continue

        web_url = build_web_url(page_data)
        candidates.append(
            PageCandidate(
                page_id=str(page_data.get("id") or page_dir.name),
                title=title,
                page_dir=page_dir,
                web_url=web_url,
                initial_score=initial_score,
            )
        )

    candidates.sort(key=lambda item: (-item.initial_score, item.title.lower()))
    return candidates[:MAX_INITIAL_CANDIDATES]


def collect_relevant_groups(article_candidates: list[PageCandidate], query_tokens: set[str]) -> list[StepGroup]:
    ranked_groups: list[StepGroup] = []

    for candidate in article_candidates[:MAX_ARTICLES]:
        article_groups = parse_page_groups(candidate)
        for group in article_groups:
            searchable_text = " ".join(group.heading_path + [group.step_text, group.context_text])
            group_score = score_text(query_tokens, searchable_text, title_multiplier=2)
            if group_score <= 0:
                continue
            ranked_groups.append(
                StepGroup(
                    article_title=candidate.title,
                    article_url=candidate.web_url,
                    heading_path=group.heading_path,
                    step_text=group.step_text,
                    context_text=group.context_text,
                    images=group.images[:MAX_IMAGES],
                    score=group_score + (2 if group.images else 0),
                )
            )

    ranked_groups.sort(key=lambda item: (-item.score, item.article_title.lower(), item.step_text.lower()))
    if ranked_groups:
        return ranked_groups[:MAX_GROUPS]

    if article_candidates:
        fallback_groups = parse_page_groups(article_candidates[0])
        return [
            StepGroup(
                article_title=article_candidates[0].title,
                article_url=article_candidates[0].web_url,
                heading_path=group.heading_path,
                step_text=group.step_text,
                context_text=group.context_text,
                images=group.images[:MAX_IMAGES],
                score=0,
            )
            for group in fallback_groups[:3]
        ]

    return []


def parse_page_groups(candidate: PageCandidate) -> list[StepGroup]:
    body_path = candidate.page_dir / "body.export_view.html"
    if not body_path.exists():
        return []

    html_text = body_path.read_text(encoding="utf-8")
    parser = ArticleHtmlParser()
    parser.feed(html_text)
    raw_groups = build_groups(parser.blocks)

    return [
        StepGroup(
            article_title=candidate.title,
            article_url=candidate.web_url,
            heading_path=group["heading_path"],
            step_text=group["step_text"],
            context_text=group["context_text"],
            images=group["images"],
            score=0,
        )
        for group in raw_groups
        if group["step_text"]
    ]


def build_groups(blocks: list[Block]) -> list[dict[str, Any]]:
    heading_path: list[str] = []
    current_group: dict[str, Any] | None = None
    groups: list[dict[str, Any]] = []

    for block in blocks:
        if block.kind == "heading":
            level = block.level or 1
            heading_path = heading_path[: level - 1]
            heading_path.append(block.text)
            if group_has_content(current_group):
                groups.append(finalize_group(current_group))
            current_group = None
            continue

        if current_group is None:
            current_group = new_group(heading_path)

        if block.kind == "image":
            current_group["images"].append({"src": str(block.src or ""), "alt": str(block.alt or "")})
            continue

        if block.kind in TEXT_BLOCK_TYPES:
            if current_group["images"]:
                groups.append(finalize_group(current_group))
                current_group = new_group(heading_path)

            current_group["text_blocks"].append({"kind": block.kind, "text": block.text})

    if group_has_content(current_group):
        groups.append(finalize_group(current_group))

    return groups


def new_group(heading_path: list[str]) -> dict[str, Any]:
    return {
        "heading_path": heading_path.copy(),
        "text_blocks": [],
        "images": [],
    }


def group_has_content(group: dict[str, Any] | None) -> bool:
    return bool(group and (group["text_blocks"] or group["images"]))


def finalize_group(group: dict[str, Any]) -> dict[str, Any]:
    anchor_text = choose_anchor_text(group["text_blocks"])
    return {
        "heading_path": group["heading_path"],
        "step_text": anchor_text,
        "context_text": "\n".join(block["text"] for block in group["text_blocks"]),
        "images": group["images"],
    }


def choose_anchor_text(text_blocks: list[dict[str, str]]) -> str:
    for block in reversed(text_blocks):
        candidate = block["text"]
        if not is_note_text(candidate) and not is_boilerplate_text(candidate):
            return candidate
    return text_blocks[-1]["text"] if text_blocks else ""


def enrich_voicemail(
    ticket_id: str,
    ticket_details: dict[str, Any],
    transcript_text: str,
) -> tuple[str, list[tuple[str, str]], list[str]]:
    if not transcript_text.strip():
        return "", [], ["Voicemail enrichment skipped: transcript did not contain searchable text"]
    if not os.environ.get("COPILOT_TOKEN", "").strip():
        return "", [], ["Voicemail enrichment skipped: COPILOT_TOKEN is not configured"]
    if OpenAI is None:
        return "", [], ["Voicemail enrichment skipped: openai package is not installed"]
    if not PAGES_ROOT.exists():
        return "", [], [f"Voicemail enrichment skipped: knowledge folder is missing: {PAGES_ROOT}"]

    ticket_context = build_ticket_context(ticket_id, ticket_details, transcript_text)
    query_tokens = expand_query_tokens(tokenize(ticket_context.combined_text))
    if not query_tokens:
        return "", [], ["Voicemail enrichment skipped: transcript did not contain searchable text"]

    article_candidates = find_article_candidates(query_tokens)
    step_groups = collect_relevant_groups(article_candidates, query_tokens)
    helpful_articles = dedupe_articles([(group.article_title, group.article_url) for group in step_groups[:3]])
    prompt = build_voicemail_summary_prompt(ticket_context, helpful_articles)

    client = OpenAI(
        api_key=os.environ.get("COPILOT_TOKEN", "").strip(),
        base_url=COPILOT_BASE_URL,
    )

    try:
        response = client.chat.completions.create(
            model=resolve_copilot_model(),
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
    except OPENAI_CLIENT_EXCEPTIONS as exc:
        return "", helpful_articles, [f"Voicemail enrichment fallback used: {type(exc).__name__}: {exc}"]

    refined_summary = str(response.choices[0].message.content or "").strip()
    if refined_summary.startswith("```"):
        refined_summary = refined_summary.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    rejected_helpful_articles = summary_rejects_helpful_articles(refined_summary)
    refined_summary = strip_irrelevant_article_text(refined_summary)
    if not refined_summary:
        return "", helpful_articles, ["Voicemail enrichment fallback used: model returned no usable summary"]

    if rejected_helpful_articles:
        helpful_articles = []

    return refined_summary, helpful_articles, [f"Voicemail enrichment model: {resolve_copilot_model()}"]


def build_voicemail_summary_prompt(ticket_context: TicketContext, helpful_articles: list[tuple[str, str]]) -> str:
    payload = {
        "ticket_id": ticket_context.ticket_id,
        "summary": ticket_context.summary,
        "description": ticket_context.description,
        "comments": ticket_context.comments[:5],
        "transcript": ticket_context.transcript,
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
    return json.dumps(value, indent=2, ensure_ascii=True)


def dedupe_articles(articles: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for article in articles:
        if article in seen:
            continue
        seen.add(article)
        deduped.append(article)
    return deduped


def build_web_url(page_data: dict[str, Any]) -> str:
    links = page_data.get("_links") or {}
    base = str(links.get("base") or "https://servicecentral.atlassian.net/wiki").rstrip("/")
    webui = str(links.get("webui") or "").strip()
    if webui.startswith("http"):
        return webui
    if webui.startswith("/"):
        return f"{base}{webui}"
    return base


def build_candidate_search_text(page_dir: Path) -> str:
    body_path = page_dir / "body.export_view.html"
    if not body_path.exists():
        return ""

    html_text = body_path.read_text(encoding="utf-8")
    return truncate_text(extract_plain_html_text(html_text), 5000)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def truncate_text(value: str, limit: int) -> str:
    normalized = normalize_whitespace(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def extract_plain_html_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return normalize_whitespace(unescape(without_tags))


def expand_query_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        expanded.update(QUERY_EXPANSIONS.get(token, set()))
    return expanded


def tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) >= 3 and token not in STOP_WORDS}


def score_text(query_tokens: set[str], value: str, title_multiplier: int = 1) -> int:
    searchable = value.lower()
    tokens = tokenize(searchable)
    overlap = query_tokens & tokens
    score = len(overlap)
    for token in overlap:
        if token in searchable:
            score += title_multiplier
    return score


def is_note_text(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith("note:") or normalized.startswith("note ")


def is_boilerplate_text(value: str) -> bool:
    normalized = normalize_whitespace(value).lower()
    return bool(re.fullmatch(r"start\s+\d+(?:\.\d+)*(?:\.x)?", normalized))


def is_decorative_image(src: str, alt: str) -> bool:
    normalized_src = src.lower().replace("%20", " ")
    normalized_alt = alt.lower()
    return any(marker in normalized_src or marker in normalized_alt for marker in DECORATIVE_IMAGE_MARKERS)


def resolve_copilot_model() -> str:
    return os.environ.get("COPILOT_MODEL", DEFAULT_COPILOT_MODEL).strip() or DEFAULT_COPILOT_MODEL


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
    mp3_attachments = extract_mp3_attachments(ticket_details)
    is_voice_message = is_voice_message_summary(summary)
    transcription_text = ""
    transcription_notes: list[str] = []
    if is_voice_message:
        transcription_text, transcription_notes = transcribe_voicemail(summary, mp3_attachments)
    voicemail_text = select_voicemail_text(
        is_voice_message=is_voice_message,
        transcription_text=transcription_text,
        description_text=description_text,
        comment_text=comment_text,
    )

    phone_sources = (voicemail_text,) if is_voice_message else (voicemail_text, description_text, comment_text)
    phone_number = extract_phone_number(summary, *phone_sources)
    caller_label = extract_caller_label(summary, phone_number)
    spam_sources = (voicemail_text,) if is_voice_message else (description_text, comment_text)
    spam_signals = detect_spam_signals(summary, *spam_sources)
    transcript_preview = build_transcript_preview(
        voicemail_text,
        empty_value=build_missing_transcript_message(transcription_notes, len(mp3_attachments)),
    )
    is_voicemail = is_voice_message or has_transcript(description_text, comment_text)

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
                *transcription_notes,
            ],
            "ringcentral_subtype": "spam_robocall",
            "matched_spam_signals": spam_signals,
            "caller_number": phone_number,
        }

    created_dt = parse_created_datetime(created_at)
    callback_anchor_dt = parse_summary_datetime(summary, created_dt) or created_dt
    callback_window_start, callback_window_end, callback_window_display = build_callback_window_from_datetime(callback_anchor_dt)
    if created_dt is None:
        created_at_display = created_at or "Unknown"
    else:
        created_at_display = str(created_dt)
    recommendation = "ringcentral_voicemail_callback_needed" if is_voicemail else "ringcentral_missed_call_callback_needed"
    ringcentral_subtype = "voice_message" if is_voicemail else "missed_call"
    refined_summary = ""
    helpful_articles: list[tuple[str, str]] = []
    enrichment_notes: list[str] = []
    if is_voicemail:
        refined_summary, helpful_articles, enrichment_notes = enrich_voicemail(
            normalized_ticket_id,
            ticket_details,
            voicemail_text,
        )
    return {
        "recommendation": recommendation,
        "body": build_callback_issue_body(
            normalized_ticket_id,
            phone_number,
            callback_window_display,
            is_voicemail=is_voicemail,
            refined_summary=refined_summary,
            helpful_articles=helpful_articles,
        ),
        "notes": [
            f"Reporter email: {reporter_email}",
            f"RingCentral subtype: {ringcentral_subtype}",
            f"Summary: {summary or 'None'}",
            f"Caller label: {caller_label or 'Unknown'}",
            f"Ticket created at: {created_at_display}",
            f"Caller number: {phone_number or 'Unknown'}",
            f"Suggested callback window: {callback_window_start} to {callback_window_end}",
            f"Fetched audio attachments: {len(mp3_attachments)}",
            *transcription_notes,
            *enrichment_notes,
        ],
        "ringcentral_subtype": ringcentral_subtype,
        "caller_number": phone_number,
        "callback_window_start": callback_window_start,
        "callback_window_end": callback_window_end,
        "has_transcript": is_voicemail,
        "refined_summary": refined_summary,
        "helpful_articles": helpful_articles,
        "voicemail_transcript": voicemail_text,
    }