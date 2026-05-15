from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MODULE_ID = "orphaned_transaction"
DISPLAY_NAME = "orphaned transaction module"
VERSION = "v1.1"


MODULE_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIRECTORY = MODULE_ROOT / "orphaned_transaction_tickets"
REQUIRED_DETAIL_KEYS = [
    "rq_ticket",
    "customer_id",
    "loc_id",
    "terminal_id",
    "pm_id",
    "amount",
    "card",
    "timestamp",
    "staff_user_id",
    "transaction_id",
]


ALLOWED_TICKET_IDS = {
    "SCD-142125",
    "SCD-142398",
    "SCD-142437",
}


def run(ticket_id: str, _ticket_details: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_ticket_id = ticket_id.strip().upper()
    if not normalized_ticket_id:
        raise ValueError("ticket_id is required")

    if normalized_ticket_id not in ALLOWED_TICKET_IDS:
        raise ValueError(f"{normalized_ticket_id} is not supported by {MODULE_ID}")

    template = load_ticket_template(normalized_ticket_id)
    detail_values, notes = get_detail_values(template)
    issue_body = build_issue_body(template, detail_values)

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


def get_detail_values(template: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    raw_details = template.get("transaction_details")
    if not isinstance(raw_details, dict):
        raise ValueError("transaction_details is required in the orphaned transaction ticket file")

    details = {str(key): str(value).strip() for key, value in raw_details.items()}
    missing_keys = [key for key in REQUIRED_DETAIL_KEYS if not details.get(key)]
    if missing_keys:
        raise ValueError(
            "transaction_details is missing required keys: " + ", ".join(missing_keys)
        )

    sql_query = str(template.get("sql_query") or "").strip()
    if not sql_query:
        raise ValueError("sql_query is required in the orphaned transaction ticket file")

    details["sql_query"] = sql_query
    return details, ["None"]


def build_issue_body(template: dict[str, Any], extracted_values: dict[str, str]) -> str:
    display_rows = get_display_rows(template, extracted_values)
    customer_name = str(template.get("customer_name") or "").strip()
    staff_name = str(template.get("staff_name") or "").strip()
    intro = str(template.get("intro") or "This is an orphaned transaction ticket.").strip()
    template_ticket_id = str(template.get("ticket_id") or "SCD-_____").strip() or "SCD-_____"
    step_2_text = str(
        template.get("step_2_text")
        or "Run the Execute workflow to close {ticket_id} automatically:\n1- Posts a comment to the client.\n2- Leaves an internal AI note.\n3- Assigns ticket to you.\n4- Logs 30mins to your time.\n5- Fill fields and resolve."
    ).strip()
    step_2_text = step_2_text.replace("{ticket_id}", template_ticket_id)

    customer_id = with_optional_label(extracted_values.get("customer_id", "Not found in ticket"), customer_name)
    staff_user_id = with_optional_label(extracted_values.get("staff_user_id", "Not found in ticket"), staff_name)
    sql_query = extracted_values.get("sql_query") or "Not found in ticket."

    if not display_rows:
        display_rows = [
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

    for label, value in display_rows:
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


def get_display_rows(template: dict[str, Any], extracted_values: dict[str, str]) -> list[tuple[str, str]]:
    raw_rows = template.get("display_rows")
    if not isinstance(raw_rows, list):
        return []

    display_rows: list[tuple[str, str]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            raise ValueError("display_rows entries must be JSON objects")

        label = str(raw_row.get("label") or "").strip()
        if not label:
            raise ValueError("display_rows entries require a label")

        if "value" in raw_row:
            value = str(raw_row.get("value") or "").strip()
        else:
            key = str(raw_row.get("detail_key") or "").strip()
            if not key:
                raise ValueError("display_rows entries require either value or detail_key")
            value = extracted_values.get(key, "Not found in ticket")

        display_rows.append((label, value or "Not found in ticket"))

    return display_rows


def with_optional_label(value: str, label: str) -> str:
    normalized_value = value.strip()
    normalized_label = label.strip()
    if normalized_value and normalized_label and normalized_value != "Not found in ticket":
        return f"{normalized_value} ({normalized_label})"
    return normalized_value or "Not found in ticket"


def escape_table_value(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", "<br>")