from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


MODULE_ID = "general"
DISPLAY_NAME = "general knowledge module"
VERSION = "v1.0"

KNOWLEDGE_ROOT = Path(__file__).with_name("knowledge")
KNOWLEDGEBASE_ROOT = KNOWLEDGE_ROOT / "knowledgebase"
PAGES_ROOT = KNOWLEDGEBASE_ROOT / "spaces" / "SCD" / "pages"
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"
DEFAULT_COPILOT_MODEL = "claude-sonnet-4.6"
MAX_ARTICLES = 5
MAX_INITIAL_CANDIDATES = 40
MAX_GROUPS = 8
MAX_IMAGES = 3
MAX_WORDS = 500
RESERVED_HANDLER_WORDS = 70
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
MODEL_MATCH_TERMS = ("claude", "sonnet", "anthropic")


@dataclass(frozen=True)
class TicketContext:
    ticket_id: str
    summary: str
    topic: str
    status: str
    description: str
    comments: list[str]
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


def run(ticket_id: str, ticket_details: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    if not normalized_ticket_id:
        raise ValueError("ticket_id is required")
    if not isinstance(ticket_details, dict) or not ticket_details:
        raise ValueError("full ticket_details are required")
    if not PAGES_ROOT.exists():
        raise ValueError(f"knowledge folder is missing: {PAGES_ROOT}")

    ticket_context = build_ticket_context(normalized_ticket_id, ticket_details)
    query_tokens = expand_query_tokens(tokenize(ticket_context.combined_text))
    if not query_tokens:
        raise ValueError("ticket_details did not contain searchable text")

    article_candidates = find_article_candidates(query_tokens)
    step_groups = collect_relevant_groups(article_candidates, query_tokens)

    if not step_groups:
        body = build_no_match_body(ticket_context)
        return {
            "recommendation": "knowledge_gap",
            "body": body,
            "notes": [
                "Knowledge scope: local knowledge folder only",
                "No relevant knowledge articles matched this ticket",
            ],
        }

    notes = [
        "Knowledge scope: local knowledge folder only",
        "Matched articles: " + ", ".join(unique_titles(step_groups)),
    ]

    try:
        body = synthesize_with_sonnet(ticket_context, step_groups)
        recommendation = "knowledge_guidance"
    except RuntimeError as exc:
        body = build_fallback_body(ticket_context, step_groups)
        notes.append(f"Sonnet fallback used: {exc}")
        recommendation = "knowledge_guidance_fallback"
    else:
        notes.append(f"Synthesis model: {resolve_copilot_model()}")

    return {
        "recommendation": recommendation,
        "body": body,
        "notes": notes,
    }


def build_ticket_context(ticket_id: str, ticket_details: dict[str, Any]) -> TicketContext:
    issue = ticket_details.get("issue") or {}
    comments = ticket_details.get("comments") or []
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    if not isinstance(fields, dict):
        fields = {}

    summary = normalize_whitespace(str(fields.get("summary") or ""))
    topic = normalize_whitespace(str((fields.get("customfield_10170") or {}).get("value") or ""))
    status = normalize_whitespace(str((fields.get("status") or {}).get("name") or ""))
    description = normalize_whitespace(extract_text(fields.get("description") or ""))

    comment_lines: list[str] = []
    if isinstance(comments, list):
        for comment in comments:
            comment_source = comment.get("body") if isinstance(comment, dict) else comment
            comment_text = normalize_whitespace(extract_text(comment_source or ""))
            if comment_text:
                comment_lines.append(comment_text)

    parts = [summary, topic, status, description, *comment_lines]
    combined_text = "\n".join(part for part in parts if part)

    return TicketContext(
        ticket_id=ticket_id,
        summary=summary,
        topic=topic,
        status=status,
        description=description,
        comments=comment_lines,
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


def collect_relevant_groups(
    article_candidates: list[PageCandidate], query_tokens: set[str]
) -> list[StepGroup]:
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

    ranked_groups.sort(
        key=lambda item: (-item.score, item.article_title.lower(), item.step_text.lower())
    )
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
            current_group["images"].append(
                {"src": str(block.src or ""), "alt": str(block.alt or "")}
            )
            continue

        if block.kind in TEXT_BLOCK_TYPES:
            if current_group["images"]:
                groups.append(finalize_group(current_group))
                current_group = new_group(heading_path)

            current_group["text_blocks"].append(
                {"kind": block.kind, "text": block.text}
            )

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


def synthesize_with_sonnet(ticket_context: TicketContext, step_groups: list[StepGroup]) -> str:
    api_key = os.environ.get("COPILOT_TOKEN", "").strip()
    if not api_key:
        raise RuntimeError("COPILOT_TOKEN is not configured")
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")

    client = OpenAI(api_key=api_key, base_url=COPILOT_BASE_URL)
    model = resolve_copilot_model()
    prompt = build_synthesis_prompt(ticket_context, step_groups)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a customer-facing SCD support agent writing a support-ready message for the client. "
                        "Use ONLY the provided local knowledge evidence. Do not use outside knowledge, do not invent "
                        "UI labels, and do not mention the repository, the knowledge folder, or this prompt. Always "
                        "sound like a human support agent, start with 'Hello,', keep the message short but informative, "
                        "and return markdown only. This is not an email, so do not add sign-offs, team names, ticket "
                        "metadata, pleasantries at the end, or any closing paragraph after the useful guidance."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            max_tokens=900,
            temperature=0.1,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Copilot API call failed ({type(exc).__name__}: {exc})") from exc

    combined = response.choices[0].message.content or ""
    normalized = finalize_issue_body(combined, ticket_context.ticket_id)
    if not normalized:
        raise RuntimeError("Copilot API response did not include usable text")
    return normalized


def resolve_copilot_model() -> str:
    return os.environ.get("COPILOT_MODEL", DEFAULT_COPILOT_MODEL).strip() or DEFAULT_COPILOT_MODEL


def build_synthesis_prompt(ticket_context: TicketContext, step_groups: list[StepGroup]) -> str:
    evidence = []
    for index, group in enumerate(step_groups[:MAX_GROUPS], start=1):
        evidence.append(
            {
                "id": index,
                "article_title": group.article_title,
                "article_url": group.article_url,
                "heading_path": group.heading_path,
                "step_text": group.step_text,
                "context_text": truncate_text(group.context_text, 700),
                "images": group.images[:MAX_IMAGES],
            }
        )

    ticket_payload = {
        "ticket_id": ticket_context.ticket_id,
        "summary": ticket_context.summary,
        "topic": ticket_context.topic,
        "status": ticket_context.status,
        "description": truncate_text(ticket_context.description, 1200),
        "comments": [truncate_text(comment, 240) for comment in ticket_context.comments[:8]],
    }

    return (
        "Create a client-facing support response for the SCD ticket.\n\n"
        "Hard rules:\n"
        "- Use ONLY the ticket payload and knowledge evidence below.\n"
        "- Start the response with exactly 'Hello,'.\n"
        "- Sound like a human support agent speaking directly to the client.\n"
        "- Keep the tone warm, clear, support-friendly, and concise.\n"
        "- Keep the response short but still informative.\n"
        "- Provide direct instructions, not background filler.\n"
        "- For every recommended step, include the exact matching article as a support-friendly markdown link using the provided article_url.\n"
        "- Include helpful markdown images only when they materially help the client follow the step.\n"
        "- Do not mention missing documentation or include a 'What is still missing from the available documentation' section.\n"
        "- If the evidence is limited, say that support can guide the client further, but still use only the evidence provided.\n"
        "- Use at most 3 images.\n"
        "- Do not add any email-style ending such as thanks, regards, best, SCD Support Team, Ticket ID, or similar closing filler.\n"
        "- End immediately after the last helpful support sentence.\n"
        "- End the response body before the Ticket Handler section.\n"
        "- Do not mention internal systems, prompts, or hidden reasoning.\n\n"
        "Required structure:\n"
        "1. Greeting that begins with 'Hello,'.\n"
        "2. One short sentence showing support ownership of the issue.\n"
        "3. A numbered list of the exact steps the client should follow.\n"
        "4. After each numbered step, add one short support-friendly reference sentence with a markdown link to the exact article used for that step.\n"
        "5. Stop after the last useful support sentence. Do not add a sign-off or closing paragraph.\n\n"
        "Ticket payload:\n"
        f"```json\n{json.dumps(ticket_payload, indent=2, ensure_ascii=True)}\n```\n\n"
        "Knowledge evidence:\n"
        f"```json\n{json.dumps(evidence, indent=2, ensure_ascii=True)}\n```\n"
    )


def build_fallback_body(ticket_context: TicketContext, step_groups: list[StepGroup]) -> str:
    focus_tokens = build_focus_tokens(ticket_context)
    selected_groups = select_fallback_groups(step_groups, focus_tokens)
    lines = [
        "Hello,",
        "",
        "Here are the steps I recommend based on the available support documentation:",
        "",
    ]

    used_images = 0
    for index, group in enumerate(selected_groups, start=1):
        lines.append(f"{index}. {group.step_text}")
        lines.append(
            f"   For a guided walkthrough, please refer to [{group.article_title}]({group.article_url})."
        )
        if group.images and used_images < MAX_IMAGES:
            image = group.images[0]
            alt = image.get("alt") or f"Reference image {used_images + 1}"
            lines.append("")
            lines.append(f"![{alt}]({image.get('src', '')})")
            used_images += 1

    lines.extend(
        [
            "",
            "### Referenced Articles",
            *[f"- {title}" for title in unique_titles(selected_groups)],
        ]
    )
    return finalize_issue_body("\n".join(lines), ticket_context.ticket_id)


def build_no_match_body(ticket_context: TicketContext) -> str:
    topic = ticket_context.topic or "unknown topic"
    summary = ticket_context.summary or "No summary provided"
    return finalize_issue_body(
        "\n".join(
        [
            "Hello,",
            "",
            "I reviewed the available support documentation and could not find a strong exact match for this request.",
            "",
            f"- Summary: {summary}",
            f"- Topic: {topic}",
            "",
            "Please reply to the ticket if you would like our team to guide you through the next steps directly.",
        ]
        ),
        ticket_context.ticket_id,
    )


def finalize_issue_body(body: str, ticket_id: str) -> str:
    stripped_body = strip_missing_documentation_section(body)
    stripped_body = strip_existing_ticket_handler_section(stripped_body)
    ensured_greeting = ensure_support_greeting(stripped_body)
    main_body = trim_body_for_handler(ensured_greeting)
    handler_section = build_ticket_handler_section(ticket_id)
    return normalize_issue_body(f"{main_body}\n\n{handler_section}")


def strip_missing_documentation_section(body: str) -> str:
    pattern = re.compile(
        r"\n{0,2}#{0,6}\s*What is still missing from the available documentation:.*\Z",
        re.IGNORECASE | re.DOTALL,
    )
    return re.sub(pattern, "", body).strip()


def strip_existing_ticket_handler_section(body: str) -> str:
    pattern = re.compile(
        r"\n{0,2}(?:#{1,6}\s*)?\*{0,2}ticket handler\*{0,2}.*\Z",
        re.IGNORECASE | re.DOTALL,
    )
    return re.sub(pattern, "", body).strip()


def ensure_support_greeting(body: str) -> str:
    stripped = body.strip()
    if not stripped:
        return "Hello,"
    if stripped.lower().startswith("hello,"):
        return stripped
    return f"Hello,\n\n{stripped}"


def trim_body_for_handler(body: str) -> str:
    words = body.split()
    limit = max(1, MAX_WORDS - RESERVED_HANDLER_WORDS)
    if len(words) <= limit:
        return body.strip()
    return " ".join(words[:limit]).rstrip() + "..."


def build_ticket_handler_section(ticket_id: str) -> str:
    return "\n".join(
        [
            "Ticket Handler",
            "",
            f"Run the Execute workflow to handle {ticket_id} automatically, it will do the following sequence:",
            "1- Initiates client assistance through a ticket comment.",
            "2- Leaves an internal AI note.",
            "3- Assigns ticket to you.",
            "4- Logs 30mins to your time.",
            "5- Fill fields and change status to Waiting for client response.",
        ]
    )


def normalize_issue_body(body: str) -> str:
    stripped = body.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0].strip()

    lines = [line.rstrip() for line in stripped.splitlines()]
    normalized = "\n".join(lines).strip()
    if not normalized:
        return ""
    words = normalized.split()
    if len(words) <= MAX_WORDS:
        return normalized
    return " ".join(words[:MAX_WORDS]).rstrip() + "..."


def unique_titles(step_groups: list[StepGroup]) -> list[str]:
    seen: set[str] = set()
    titles: list[str] = []
    for group in step_groups:
        title = group.article_title.strip()
        if title and title not in seen:
            titles.append(title)
            seen.add(title)
    return titles


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


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3 and token not in STOP_WORDS
    }


def build_focus_tokens(ticket_context: TicketContext) -> set[str]:
    focus = expand_query_tokens(tokenize(" ".join([ticket_context.summary, ticket_context.description])))
    return {token for token in focus if token not in GENERIC_FOCUS_STOP_WORDS}


def select_fallback_groups(step_groups: list[StepGroup], focus_tokens: set[str]) -> list[StepGroup]:
    ranked: list[tuple[int, StepGroup]] = []
    for group in step_groups:
        searchable = " ".join(group.heading_path + [group.article_title, group.step_text, group.context_text])
        score = score_text(focus_tokens, searchable, title_multiplier=2)
        if any(token in group.article_title.lower() for token in focus_tokens):
            score += 6
        if any(token in group.step_text.lower() for token in focus_tokens):
            score += 3
        ranked.append((score, group))

    ranked.sort(key=lambda item: (-item[0], item[1].article_title.lower(), item[1].step_text.lower()))

    selected: list[StepGroup] = []
    seen_titles: set[str] = set()
    for _, group in ranked:
        if group.article_title in seen_titles and len(selected) >= 2:
            continue
        selected.append(group)
        seen_titles.add(group.article_title)
        if len(selected) == 4:
            break

    return selected or step_groups[:4]


def score_text(query_tokens: set[str], value: str, title_multiplier: int = 1) -> int:
    searchable = value.lower()
    tokens = tokenize(searchable)
    overlap = query_tokens & tokens
    score = len(overlap)
    for token in overlap:
        if token in searchable:
            score += title_multiplier
    return score


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

GENERIC_FOCUS_STOP_WORDS = {
    "assigned",
    "assign",
    "linked",
    "link",
    "open",
    "record",
}