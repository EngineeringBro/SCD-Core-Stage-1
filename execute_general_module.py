from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

import general_module


INTERNAL_COMMENT_TEXT = "This ticket was resolved using my AI Agent"
WORKLOG_TIME_SPENT = "30m"
WAITING_TRANSITION_NAMES = (
    "Waiting for client",
    "Waiting for client response",
)
WAITING_STATUS_NAMES = (
    "Waiting for client",
    "Waiting for client response",
    "Waiting for customer",
)


def main() -> int:
    scd_id = os.environ.get("SCD_TICKET_ID", "").strip().upper()
    if not scd_id:
        raise RuntimeError("SCD_TICKET_ID is required")

    env = load_env_from_environment()
    creds = build_credentials(env)
    headers = {
        "Authorization": "Basic " + creds,
        "Content-Type": "application/json",
    }
    base = env["JIRA_BASE_URL"].rstrip("/")

    ticket_details = fetch_ticket_details(base, scd_id, headers)
    module_response = general_module.run(scd_id, ticket_details)
    public_comment_markdown = build_public_comment_markdown(module_response)

    post_public_comment(base, scd_id, headers, public_comment_markdown)
    post_internal_comment(base, scd_id, headers, ticket_details)
    assign_to_current_user(base, scd_id, creds)
    log_work(base, scd_id, headers)
    transition_to_waiting_for_client(base, scd_id, headers)
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


def build_public_comment_markdown(module_response: dict[str, object]) -> str:
    body = str(module_response.get("body") or "").strip()
    if not body:
        raise RuntimeError("General module returned an empty body")

    stripped = strip_ticket_handler_section(body)
    if not stripped:
        raise RuntimeError("General module body contained no customer-facing comment text")
    return stripped


def strip_ticket_handler_section(body: str) -> str:
    pattern = re.compile(
        r"\n{0,2}(?:#{1,6}\s*)?\*{0,2}ticket handler\*{0,2}.*\Z",
        re.IGNORECASE | re.DOTALL,
    )
    return re.sub(pattern, "", body).strip()


def post_public_comment(base: str, scd_id: str, headers: dict[str, str], comment_markdown: str) -> None:
    payload = {"body": convert_markdown_comment_to_adf(comment_markdown)}
    response = api_request(
        base,
        f"/rest/api/3/issue/{scd_id}/comment",
        headers,
        method="POST",
        payload=payload,
        expected_status=201,
        label="9a comment",
    )
    print(f"9a comment: {response}")


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
        req_internal = urllib.request.Request(
            f"{base}/rest/servicedeskapi/request/{scd_id}/comment",
            data=json.dumps(internal_payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req_internal) as response:
            print(f"9b internal comment: {response.status}")
        return

    internal_payload = {
        "body": convert_markdown_comment_to_adf(INTERNAL_COMMENT_TEXT),
        "properties": [
            {
                "key": "sd.public.comment",
                "value": {"internal": True},
            }
        ],
    }
    response = api_request(
        base,
        f"/rest/api/3/issue/{scd_id}/comment",
        headers,
        method="POST",
        payload=internal_payload,
        expected_status=201,
        label="9b internal comment",
    )
    print(f"9b internal comment: {response}")


def assign_to_current_user(base: str, scd_id: str, creds: str) -> None:
    myself = api_get(
        base,
        "/rest/api/3/myself",
        {
            "Authorization": "Basic " + creds,
            "Accept": "application/json",
        },
    )
    account_id = str(myself.get("accountId") or "").strip()
    if not account_id:
        raise RuntimeError("9c assign failed: /myself did not return accountId")

    payload = {
        "fields": {
            "assignee": {
                "accountId": account_id,
            }
        }
    }
    api_request(
        base,
        f"/rest/api/3/issue/{scd_id}",
        {
            "Authorization": "Basic " + creds,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="PUT",
        payload=payload,
        expected_status=204,
        label="9c assign",
    )
    print("9c assign: OK")


def transition_to_waiting_for_client(base: str, scd_id: str, headers: dict[str, str]) -> None:
    transitions_payload = api_get(base, f"/rest/api/3/issue/{scd_id}/transitions", headers)
    transitions = transitions_payload.get("transitions", []) if isinstance(transitions_payload, dict) else []
    transition_id = find_transition_id(transitions)
    if not transition_id:
        raise RuntimeError(
            "9d transition failed: could not find a Waiting for client transition. "
            f"Available transitions: {', '.join(sorted(extract_transition_names(transitions)))}"
        )

    payload = {
        "transition": {"id": transition_id},
    }
    response = api_request(
        base,
        f"/rest/api/3/issue/{scd_id}/transitions",
        headers,
        method="POST",
        payload=payload,
        expected_status=204,
        label="9d transition",
    )
    print(f"9d transition: {response}")


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
        label="9d worklog",
    )
    print(f"9d worklog: {response}")


def find_transition_id(transitions: object) -> str:
    if not isinstance(transitions, list):
        return ""

    normalized_transition_names = {name.lower() for name in WAITING_TRANSITION_NAMES}
    normalized_status_names = {name.lower() for name in WAITING_STATUS_NAMES}
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        name = str(transition.get("name") or "").strip()
        to_status = transition.get("to") if isinstance(transition.get("to"), dict) else {}
        to_name = str(to_status.get("name") or "").strip()
        if name.lower() in normalized_transition_names or to_name.lower() in normalized_status_names:
            return str(transition.get("id") or "").strip()
    return ""


def extract_transition_names(transitions: object) -> list[str]:
    names: list[str] = []
    if not isinstance(transitions, list):
        return names
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        name = str(transition.get("name") or "").strip()
        if name:
            names.append(name)
    return names


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


def convert_markdown_comment_to_adf(markdown: str) -> dict[str, object]:
    lines = markdown.splitlines()
    content: list[dict[str, object]] = []
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped:
            index += 1
            continue

        ordered_match = re.match(r"^(\d+)[.-]\s+(.+)$", stripped)
        if ordered_match:
            items: list[dict[str, object]] = []
            while index < len(lines):
                candidate = lines[index].strip()
                match = re.match(r"^(\d+)[.-]\s+(.+)$", candidate)
                if not match:
                    break
                items.append(
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": parse_inline_content(match.group(2)),
                            }
                        ],
                    }
                )
                index += 1
            content.append({"type": "orderedList", "content": items})
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet_match:
            items = []
            while index < len(lines):
                candidate = lines[index].strip()
                match = re.match(r"^[-*]\s+(.+)$", candidate)
                if not match:
                    break
                items.append(
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": parse_inline_content(match.group(1)),
                            }
                        ],
                    }
                )
                index += 1
            content.append({"type": "bulletList", "content": items})
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line:
                break
            if re.match(r"^(\d+)[.-]\s+(.+)$", next_line) or re.match(r"^[-*]\s+(.+)$", next_line):
                break
            paragraph_lines.append(next_line)
            index += 1
        paragraph_text = " ".join(paragraph_lines)
        content.extend(convert_paragraph_with_images(paragraph_text))

    return {
        "type": "doc",
        "version": 1,
        "content": content or [{"type": "paragraph", "content": [{"type": "text", "text": markdown.strip()}]}],
    }


def convert_paragraph_with_images(paragraph_text: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    cursor = 0
    inline_parts: list[str] = []
    for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", paragraph_text):
        before = paragraph_text[cursor:match.start()].strip()
        if before:
            inline_parts.append(before)
        if inline_parts:
            blocks.append(
                {
                    "type": "paragraph",
                    "content": parse_inline_content(" ".join(inline_parts)),
                }
            )
            inline_parts = []
        alt = (match.group(1) or "Reference image").strip() or "Reference image"
        url = match.group(2).strip()
        blocks.append(
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": f"Reference image: {alt} - "},
                    {
                        "type": "text",
                        "text": url,
                        "marks": [{"type": "link", "attrs": {"href": url}}],
                    },
                ],
            }
        )
        cursor = match.end()

    tail = paragraph_text[cursor:].strip()
    if tail:
        inline_parts.append(tail)
    if inline_parts:
        blocks.append(
            {
                "type": "paragraph",
                "content": parse_inline_content(" ".join(inline_parts)),
            }
        )
    return blocks


def parse_inline_content(text: str) -> list[dict[str, object]]:
    content: list[dict[str, object]] = []
    position = 0
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    for match in link_pattern.finditer(text):
        if match.start() > position:
            content.append({"type": "text", "text": text[position:match.start()]})
        label = match.group(1)
        url = match.group(2)
        content.append(
            {
                "type": "text",
                "text": label,
                "marks": [{"type": "link", "attrs": {"href": url}}],
            }
        )
        position = match.end()
    if position < len(text):
        content.append({"type": "text", "text": text[position:]})
    return content or [{"type": "text", "text": ""}]


if __name__ == "__main__":
    raise SystemExit(main())