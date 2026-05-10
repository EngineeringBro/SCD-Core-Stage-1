from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from execute_comment_utils import post_internal_note_issue_comment
from modules.notifications_module import notification_module


INTERNAL_COMMENT_TEXT = "This ticket was resolved using SCD Core AI Project."
WORKLOG_TIME_SPENT = "3m"
RESOLVE_TRANSITION_ID = "81"
RESOLUTION_DONE_ID = "10006"
ROOT_CAUSE_UNKNOWN_ID = "10501"
TOPIC_OPTION_IDS = {
    "Azure Notification": "10495",
    "Revv Error Report": "10494",
    "Assurant": "10351",
    "Asurion": "10352",
    "Quickbooks": "10418",
    "Sales": "10429",
}
TOPIC_NO_CHANGE = "No change"


def main() -> int:
    scd_id = os.environ.get("SCD_TICKET_ID", "").strip().upper()
    if not scd_id:
        raise RuntimeError("SCD_TICKET_ID is required")

    env = load_env_from_environment()
    creds = build_credentials(env)
    headers = {
        "Authorization": "Basic " + creds,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    base = env["JIRA_BASE_URL"].rstrip("/")

    ticket_details = fetch_ticket_details(base, scd_id, headers)
    module_response = notification_module.run(scd_id, ticket_details)
    topic_name = str(module_response.get("output_topic") or "").strip()

    if not topic_name:
        raise RuntimeError(f"Notification execute requires a matched notification topic for {scd_id}")

    post_internal_comment(base, scd_id, headers)
    assign_to_current_user(base, scd_id, creds)
    log_work(base, scd_id, headers)
    transition_to_done(base, scd_id, headers, topic_name)
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


def build_credentials(env: dict[str, str]) -> str:
    return base64.b64encode((env["JIRA_EMAIL"] + ":" + env["JIRA_WRITE_API_TOKEN"]).encode()).decode()


def fetch_ticket_details(base: str, scd_id: str, headers: dict[str, str]) -> dict[str, object]:
    issue = api_get(base, f"/rest/api/3/issue/{scd_id}", headers)
    comments_payload = api_get(base, f"/rest/api/3/issue/{scd_id}/comment", headers)
    comments = comments_payload.get("comments", []) if isinstance(comments_payload, dict) else []
    return {
        "issue": issue,
        "comments": comments,
    }


def post_internal_comment(base: str, scd_id: str, headers: dict[str, str]) -> None:
    status = post_internal_note_issue_comment(
        base,
        scd_id,
        headers,
        comment_text=INTERNAL_COMMENT_TEXT,
        label="9a internal comment",
    )
    print(f"9a internal comment: {status}")


def assign_to_current_user(base: str, scd_id: str, creds: str) -> None:
    request = urllib.request.Request(
        f"{base}/rest/api/3/myself",
        headers={
            "Authorization": "Basic " + creds,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request) as response:
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
        raise RuntimeError(f"9b assign failed: HTTP {exc.code}: {error_body}") from exc

    if status != 204:
        raise RuntimeError(f"9b assign failed: expected 204, got {status}")
    print("9b assign: OK")


def log_work(base: str, scd_id: str, headers: dict[str, str]) -> None:
    payload = {
        "timeSpent": WORKLOG_TIME_SPENT,
        "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
    }
    response = api_request(
        base,
        f"/rest/api/3/issue/{scd_id}/worklog",
        headers,
        method="POST",
        payload=payload,
        expected_status=201,
        label="9c worklog",
    )
    print(f"9c worklog: {response}")


def transition_to_done(
    base: str,
    scd_id: str,
    headers: dict[str, str],
    topic_name: str,
) -> None:
    fields = {
        "resolution": {"id": RESOLUTION_DONE_ID},
        "customfield_10201": {"id": ROOT_CAUSE_UNKNOWN_ID},
    }

    if topic_name != TOPIC_NO_CHANGE:
        topic_option_id = TOPIC_OPTION_IDS.get(topic_name)
        if not topic_option_id:
            available_topics = ", ".join(sorted(TOPIC_OPTION_IDS))
            raise RuntimeError(
                f"9d done transition failed: unsupported notification topic '{topic_name}'. "
                f"Configured topics: {available_topics}, {TOPIC_NO_CHANGE}"
            )
        fields["customfield_10170"] = {"id": topic_option_id}

    payload = {
        "transition": {"id": RESOLVE_TRANSITION_ID},
        "fields": fields,
    }
    response = api_request(
        base,
        f"/rest/api/3/issue/{scd_id}/transitions",
        headers,
        method="POST",
        payload=payload,
        expected_status=204,
        label="9d done transition",
    )
    print(f"9d done transition: {response}")


def api_get(base: str, path: str, headers: dict[str, str]) -> dict[str, object]:
    request = urllib.request.Request(base.rstrip("/") + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"GET {path} failed: HTTP {exc.code}: {error_body}") from exc


def api_request(
    base: str,
    path: str,
    headers: dict[str, str],
    *,
    method: str,
    payload: dict[str, object],
    expected_status: int,
    label: str,
) -> int:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"{label} failed: HTTP {exc.code}: {error_body}") from exc

    if status != expected_status:
        raise RuntimeError(f"{label} failed: expected {expected_status}, got {status}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())