from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from execute_comment_utils import build_plain_text_adf, post_internal_note_issue_comment, post_public_issue_comment


PUBLIC_COMMENT_SINGLE = "Hello,\n\nWe have added the transaction. Let us know if you need anything else!"
PUBLIC_COMMENT_PLURAL = "Hello,\n\nWe have added the transactions. Let us know if you need anything else!"
INTERNAL_COMMENT_TEXT = "This ticket was resolved using SCD Core AI Project."
RESOLVE_TRANSITION_ID = "81"
RESOLUTION_FIXED_ID = "10000"
TOPIC_TRANSACTION_ERRORS_ID = "10446"
ROOT_CAUSE_SOFTWARE_BUG_ID = "10500"
WORKLOG_TIME_SPENT = "30m"


def main() -> int:
    scd_id = os.environ.get("SCD_TICKET_ID", "").strip().upper()
    if not scd_id:
        raise RuntimeError("SCD_TICKET_ID is required")

    template = load_orphaned_ticket_template(scd_id)
    sql_count = count_sql_inserts(template)
    comment_text = PUBLIC_COMMENT_PLURAL if sql_count > 1 else PUBLIC_COMMENT_SINGLE

    env = load_env_from_environment()
    creds = base64.b64encode((env["JIRA_EMAIL"] + ":" + env["JIRA_WRITE_API_TOKEN"]).encode()).decode()
    headers = {
        "Authorization": "Basic " + creds,
        "Content-Type": "application/json",
    }
    base = env["JIRA_BASE_URL"].rstrip("/")

    post_public_comment(base, scd_id, headers, comment_text)
    post_internal_comment(base, scd_id, headers)
    assign_to_current_user(base, scd_id, creds)
    resolve_ticket(base, scd_id, headers)
    return 0


def load_env_from_environment() -> dict[str, str]:
    env = {
        "JIRA_EMAIL": os.environ.get("JIRA_EMAIL", "").strip(),
        "JIRA_WRITE_API_TOKEN": os.environ.get("JIRA_WRITE_API_TOKEN", "").strip(),
        "JIRA_BASE_URL": os.environ.get("JIRA_BASE_URL", "").strip(),
    }
    missing = [key for key, value in env.items() if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    return env


def load_orphaned_ticket_template(scd_id: str) -> dict[str, Any]:
    template_path = Path(__file__).with_name("orphaned_transaction_tickets") / f"{scd_id}.json"
    if not template_path.exists():
        raise RuntimeError(f"Execute is only allowed for orphaned transaction tickets with a local file: {scd_id}")

    with template_path.open("r", encoding="utf-8") as handle:
        template = json.load(handle)

    if not isinstance(template, dict):
        raise RuntimeError(f"Invalid orphaned transaction ticket file for {scd_id}")

    return template


def count_sql_inserts(template: dict[str, Any]) -> int:
    sql_queries = template.get("sql_queries")
    if isinstance(sql_queries, list):
        normalized_queries = [str(value).strip() for value in sql_queries if str(value).strip()]
        if normalized_queries:
            return len(normalized_queries)

    sql_query = str(template.get("sql_query") or "").strip()
    if sql_query:
        return 1

    raise RuntimeError("sql_query or sql_queries is required in the orphaned transaction ticket file")


def post_public_comment(base: str, scd_id: str, headers: dict[str, str], comment_text: str) -> None:
    status = post_public_issue_comment(
        base,
        scd_id,
        headers,
        adf_body=build_plain_text_adf(comment_text),
        label="9a comment",
    )
    print(f"9a comment: {status}")


def post_internal_comment(base: str, scd_id: str, headers: dict[str, str]) -> None:
    status = post_internal_note_issue_comment(
        base,
        scd_id,
        headers,
        comment_text=INTERNAL_COMMENT_TEXT,
        label="9b internal comment",
    )
    print(f"9b internal comment: {status}")


def assign_to_current_user(base: str, scd_id: str, creds: str) -> None:
    req = urllib.request.Request(
        f"{base}/rest/api/3/myself",
        headers={
            "Authorization": "Basic " + creds,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req) as response:
        account_id = json.loads(response.read())["accountId"]

    payload = {
        "fields": {
            "assignee": {
                "accountId": account_id,
            }
        }
    }
    assign_request = urllib.request.Request(
        f"{base}/rest/api/3/issue/{scd_id}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Basic " + creds,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(assign_request) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"9c assign failed: HTTP {exc.code}: {error_body}") from exc

    if status != 204:
        raise RuntimeError(f"9c assign failed: expected 204, got {status}")
    print("9c assign: OK")


def resolve_ticket(base: str, scd_id: str, headers: dict[str, str]) -> None:
    payload = {
        "transition": {"id": RESOLVE_TRANSITION_ID},
        "fields": {
            "resolution": {"id": RESOLUTION_FIXED_ID},
            "customfield_10170": {"id": TOPIC_TRANSACTION_ERRORS_ID},
            "customfield_10201": {"id": ROOT_CAUSE_SOFTWARE_BUG_ID},
        },
        "update": {
            "worklog": [
                {
                    "add": {
                        "timeSpent": WORKLOG_TIME_SPENT,
                        "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
                    }
                }
            ]
        },
    }
    req = urllib.request.Request(
        f"{base}/rest/api/3/issue/{scd_id}/transitions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as response:
        status = response.status
    if status != 204:
        raise RuntimeError(f"9d resolve failed: expected 204, got {status}")
    print(f"9d resolve: {status}")


if __name__ == "__main__":
    raise SystemExit(main())