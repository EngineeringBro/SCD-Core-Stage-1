from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from execute_router import fetch_latest_module_issue


INTERNAL_COMMENT_TEXT = "This ticket was resolved using SCD Core AI Project."
WORKLOG_TIME_SPENT = "3m"
RESOLVE_TRANSITION_ID = "81"
SPAM_TOPIC_ID = "10438"
RESOLUTION_DISMISSED_ID = "10005"
ROOT_CAUSE_UNKNOWN_ID = "10501"
EXPECTED_RECOMMENDATION = "ringcentral_spam_safe_to_dismiss"
EXPECTED_SUBTYPE = "spam_robocall"


def main() -> int:
    scd_id = os.environ.get("SCD_TICKET_ID", "").strip().upper()
    if not scd_id:
        raise RuntimeError("SCD_TICKET_ID is required")

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo or "/" not in repo:
        raise RuntimeError("GITHUB_REPOSITORY is required")

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GH_TOKEN is required")

    issue = fetch_latest_module_issue(repo, scd_id, token)
    validate_ringcentral_spam_issue(issue, scd_id)

    env = load_env_from_environment()
    creds = build_credentials(env)
    headers = {
        "Authorization": "Basic " + creds,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    base = env["JIRA_BASE_URL"].rstrip("/")

    ticket_details = fetch_ticket_details(base, scd_id, headers)

    post_internal_comment(base, scd_id, headers, ticket_details)
    assign_to_current_user(base, scd_id, creds)
    log_work(base, scd_id, headers)
    transition_to_spam_resolution(base, scd_id, headers)
    return 0


def validate_ringcentral_spam_issue(issue: dict[str, object], scd_id: str) -> None:
    title = str(issue.get("title") or "").strip()
    body = str(issue.get("body") or "")

    module_match = re.search(r"<!--\s*module_id:\s*([a-z0-9_\-]+)\s*-->", body, re.IGNORECASE)
    module_name = module_match.group(1).strip().lower() if module_match else ""
    if module_name != "ringcentral":
        raise RuntimeError(
            f"RingCentral spam execute expected module_id 'ringcentral' for {scd_id}, got '{module_name or '(missing)'}'"
        )

    recommendation_match = re.match(r"\[([^\]]+)\]", title)
    recommendation = recommendation_match.group(1).strip() if recommendation_match else ""
    if recommendation != EXPECTED_RECOMMENDATION:
        raise RuntimeError(
            f"RingCentral spam execute only supports recommendation '{EXPECTED_RECOMMENDATION}' for {scd_id}, "
            f"got '{recommendation or '(missing)'}'"
        )

    subtype_match = re.search(r"^-\s*RingCentral subtype:\s*(.+)$", body, re.IGNORECASE | re.MULTILINE)
    subtype = subtype_match.group(1).strip().lower() if subtype_match else ""
    if subtype != EXPECTED_SUBTYPE:
        raise RuntimeError(
            f"RingCentral spam execute only supports subtype '{EXPECTED_SUBTYPE}' for {scd_id}, "
            f"got '{subtype or '(missing)'}'"
        )


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


def post_internal_comment(
    base: str,
    scd_id: str,
    headers: dict[str, str],
    ticket_details: dict[str, object],
) -> None:
    issue = ticket_details.get("issue") if isinstance(ticket_details, dict) else None
    issue_fields = issue.get("fields") if isinstance(issue, dict) else None
    request_type = issue_fields.get("customfield_10010") if isinstance(issue_fields, dict) else None

    if request_type:
        internal_payload = {
            "body": INTERNAL_COMMENT_TEXT,
            "public": False,
        }
        request = urllib.request.Request(
            f"{base}/rest/servicedeskapi/request/{scd_id}/comment",
            data=json.dumps(internal_payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
            raise RuntimeError(f"9a internal comment failed: HTTP {exc.code}: {error_body}") from exc

        if status != 201:
            raise RuntimeError(f"9a internal comment failed: expected 201, got {status}")
        print(f"9a internal comment: {status}")
        return

    internal_payload = {
        "body": build_plain_text_adf(INTERNAL_COMMENT_TEXT),
        "properties": [
            {
                "key": "sd.public.comment",
                "value": {"internal": True},
            }
        ],
    }
    status = api_request(
        base,
        f"/rest/api/3/issue/{scd_id}/comment",
        headers,
        method="POST",
        payload=internal_payload,
        expected_status=201,
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


def transition_to_spam_resolution(base: str, scd_id: str, headers: dict[str, str]) -> None:
    payload = {
        "transition": {"id": RESOLVE_TRANSITION_ID},
        "fields": {
            "resolution": {"id": RESOLUTION_DISMISSED_ID},
            "customfield_10170": {"id": SPAM_TOPIC_ID},
            "customfield_10201": {"id": ROOT_CAUSE_UNKNOWN_ID},
        },
    }
    response = api_request(
        base,
        f"/rest/api/3/issue/{scd_id}/transitions",
        headers,
        method="POST",
        payload=payload,
        expected_status=204,
        label="9d spam transition",
    )
    print(f"9d spam transition: {response}")


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


def api_get(base: str, path: str, headers: dict[str, str]) -> dict[str, object]:
    request = urllib.request.Request(base.rstrip("/") + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"GET {path} failed: HTTP {exc.code}: {error_body}") from exc


def build_plain_text_adf(text: str) -> dict[str, object]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                    }
                ],
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())