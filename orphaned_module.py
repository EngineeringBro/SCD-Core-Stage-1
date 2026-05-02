from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MODULE_ID = "orphaned_transaction"
DISPLAY_NAME = "orphaned transaction module"
VERSION = "v1.1"


TEMPLATE_DIRECTORY = Path(__file__).with_name("orphaned_transaction_tickets")
FIELD_SPECS = [
    ("rq_ticket", "RQ ticket", r"RQ ticket"),
    ("customer_id", "customer_id", r"customer_id"),
    ("loc_id", "loc_id", r"loc_id"),
    ("terminal_id", "terminal_id", r"terminal_id"),
    ("pm_id", "pm_id", r"pm_id"),
    ("amount", "amount", r"amount"),
    ("card", "card", r"card"),
    ("timestamp", "timestamp", r"timestamp"),
    ("staff_user_id", "staff_user_id", r"staff_user_id"),
    ("transaction_id", "transaction_id", r"transaction_id"),
    ("sql_query", "SQL Query", r"SQL\s+Quer(?:y|ry)"),
]


ALLOWED_TICKET_IDS = {
    "SCD-142125",
}


def run(ticket_id: str, ticket_details: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    if not normalized_ticket_id:
        raise ValueError("ticket_id is required")

    if normalized_ticket_id not in ALLOWED_TICKET_IDS:
        raise ValueError(f"{normalized_ticket_id} is not supported by {MODULE_ID}")

    template = load_ticket_template(normalized_ticket_id)
    source_text = build_source_text(ticket_details)
    extracted_values, notes = extract_values(source_text)
    issue_body = build_issue_body(template, extracted_values)

    return {
        "recommendation": str(template.get("recommendation") or "SQL insert required"),
        "body": issue_body,
        "notes": notes or ["None"],
    }


def load_ticket_template(ticket_id: str) -> dict[str, Any]:
    template_path = TEMPLATE_DIRECTORY / f"{ticket_id}.json"
    if not template_path.exists():
        raise ValueError(f"no orphaned transaction template file exists for {ticket_id}")

    with template_path.open("r", encoding="utf-8") as handle:
        template = json.load(handle)

    if not isinstance(template, dict):
        raise ValueError(f"template file for {ticket_id} must contain a JSON object")

    return template


def build_source_text(ticket_details: dict[str, Any] | None) -> str:
    if not isinstance(ticket_details, dict):
        return ""

    issue = ticket_details.get("issue")
    issue_fields = issue.get("fields") if isinstance(issue, dict) else None
    comments = ticket_details.get("comments")

    parts: list[str] = []
    if isinstance(issue_fields, dict):
        summary = str(issue_fields.get("summary") or "").strip()
        if summary:
            parts.append(summary)

        description = render_adf_text(issue_fields.get("description"))
        if description:
            parts.append(description)

    if isinstance(comments, list):
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_text = render_adf_text(comment.get("body"))
            if comment_text:
                parts.append(comment_text)

    return "\n".join(part for part in parts if part).strip()


def render_adf_text(node: Any) -> str:
    if node is None:
        return ""

    if isinstance(node, list):
        return "".join(render_adf_text(child) for child in node)

    if not isinstance(node, dict):
        return str(node)

    node_type = str(node.get("type") or "")
    if node_type == "text":
        return str(node.get("text") or "")
    if node_type == "hardBreak":
        return "\n"

    content_text = "".join(render_adf_text(child) for child in node.get("content", []))
    if node_type in {"paragraph", "heading", "blockquote"}:
        return f"{content_text}\n"
    if node_type == "listItem":
        stripped_content = content_text.strip()
        return f"- {stripped_content}\n" if stripped_content else ""
    if node_type in {"bulletList", "orderedList", "doc"}:
        return content_text

    return content_text


def extract_values(source_text: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    notes: list[str] = []

    for index, (field_key, display_name, label_pattern) in enumerate(FIELD_SPECS):
        next_patterns = [pattern for _, _, pattern in FIELD_SPECS[index + 1 :]]
        value = extract_field_value(source_text, label_pattern, next_patterns)
        if value:
            values[field_key] = value
        else:
            notes.append(f"{display_name} was not found in the Jira ticket content.")

    return values, notes


def extract_field_value(source_text: str, label_pattern: str, next_patterns: list[str]) -> str:
    if not source_text:
        return ""

    lookahead_parts = [rf"(?:{pattern})\s*:" for pattern in next_patterns]
    lookahead_parts.append(r"$")
    lookahead = "|".join(lookahead_parts)
    pattern = re.compile(
        rf"(?:^|\n|\b){label_pattern}\s*:\s*(.*?)(?={lookahead})",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(source_text)
    if not match:
        return ""

    return clean_value(match.group(1))


def clean_value(value: str) -> str:
    cleaned = value.replace("\r", "\n")
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    cleaned = cleaned.strip()
    return cleaned


def build_issue_body(template: dict[str, Any], extracted_values: dict[str, str]) -> str:
    customer_name = str(template.get("customer_name") or "").strip()
    staff_name = str(template.get("staff_name") or "").strip()
    intro = str(template.get("intro") or "This is an orphaned transaction ticket.").strip()
    step_2_text = str(
        template.get("step_2_text")
        or "Execute the Jira ticket to temporarily regain write API access, then close it once the transaction has been restored."
    ).strip()

    customer_id = with_optional_label(extracted_values.get("customer_id", "Not found in ticket"), customer_name)
    staff_user_id = with_optional_label(extracted_values.get("staff_user_id", "Not found in ticket"), staff_name)
    sql_query = extracted_values.get("sql_query") or "Not found in ticket."

    detail_rows = [
        ("RQ ticket", extracted_values.get("rq_ticket", "Not found in ticket")),
        ("customer_id", customer_id),
        ("loc_id", extracted_values.get("loc_id", "Not found in ticket")),
        ("terminal_id", extracted_values.get("terminal_id", "Not found in ticket")),
        ("pm_id", extracted_values.get("pm_id", "Not found in ticket")),
        ("amount", extracted_values.get("amount", "Not found in ticket")),
        ("card", extracted_values.get("card", "Not found in ticket")),
        ("timestamp", extracted_values.get("timestamp", "Not found in ticket")),
        ("staff_user_id", staff_user_id),
        ("transaction_id", extracted_values.get("transaction_id", "Not found in ticket")),
    ]

    lines = [
        "## Orphaned Transaction Fix",
        "",
        intro,
        "",
        "## Transaction Details",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]

    for label, value in detail_rows:
        lines.append(f"| {label} | {escape_table_value(value)} |")

    lines.extend(
        [
            "",
            "## Step 1",
            "",
            "Run the following SQL query in the database:",
            "",
            "```sql",
            sql_query,
            "```",
            "",
            "## Step 2",
            "",
            step_2_text,
        ]
    )

    return "\n".join(lines)


def with_optional_label(value: str, label: str) -> str:
    normalized_value = value.strip()
    normalized_label = label.strip()
    if normalized_value and normalized_label and normalized_value != "Not found in ticket":
        return f"{normalized_value} ({normalized_label})"
    return normalized_value or "Not found in ticket"


def escape_table_value(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", "<br>")