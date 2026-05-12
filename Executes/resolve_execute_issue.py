from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


EXECUTES_ROOT = Path(__file__).resolve().parent
if str(EXECUTES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTES_ROOT))


PERMISSION_LABEL = "permission to execute"


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    if event_name != "issues":
        skip_execution(f"Skipped execute: unsupported event '{event_name or 'unknown'}'.")
        return 0

    payload = load_event_payload()
    action = str(payload.get("action") or "").strip().lower()
    if action != "closed":
        skip_execution(f"Skipped execute: unsupported issue action '{action or 'unknown'}'.")
        return 0

    issue = payload.get("issue") if isinstance(payload, dict) else None
    if not isinstance(issue, dict):
        skip_execution("Skipped execute: issue payload is missing.")
        return 0

    if issue.get("pull_request"):
        skip_execution("Skipped execute: pull request events are not supported.")
        return 0

    issue_number = str(issue.get("number") or "").strip()
    if not issue_number:
        skip_execution("Skipped execute: issue number is missing.")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GH_TOKEN", "").strip()
    if not repo or "/" not in repo or not token:
        skip_execution("Skipped execute: missing GitHub repository context for authorization check.")
        return 0

    try:
        verified_issue = fetch_issue(repo, issue_number, token)
    except RuntimeError as error:
        skip_execution(f"Skipped execute: could not verify issue #{issue_number} ({error}).")
        return 0

    state = str(verified_issue.get("state") or "").strip().lower()
    if state != "closed":
        skip_execution("Skipped execute: issue is not currently closed.")
        return 0

    state_reason = str(verified_issue.get("state_reason") or "").strip().lower()
    if state_reason != "completed":
        skip_execution("Skipped execute: issue was not closed as completed.")
        return 0

    labels = verified_issue.get("labels") if isinstance(verified_issue, dict) else []
    label_names = {
        str(label.get("name") or "").strip().lower()
        for label in labels
        if isinstance(label, dict)
    }
    if PERMISSION_LABEL not in label_names:
        skip_execution("Skipped execute: missing required label 'Permission to Execute'.")
        return 0

    issue_title = str(verified_issue.get("title") or "")
    title_match = re.search(r"\[(SCD-\d+)\s*-\s*([^\]]+?)\]\s*$", issue_title, re.IGNORECASE)
    if not title_match:
        title_match = re.search(r"^\[[^\]]+\]\s*(SCD-\d+)\s*-\s*(.+?)\s*$", issue_title, re.IGNORECASE)
    if not title_match:
        skip_execution("Skipped execute: issue title does not include a parseable ticket id and module name.")
        return 0

    ticket_id = str(title_match.group(1) or "").strip().upper()
    module_name = str(title_match.group(2) or "").strip()
    if not ticket_id:
        skip_execution("Skipped execute: parsed SCD ticket id was empty.")
        return 0
    if not module_name:
        skip_execution("Skipped execute: parsed module name from title was empty.")
        return 0

    export_execution(
        ticket_id,
        issue_number,
        f"Auto execute approved for {ticket_id} ({module_name}) from closed issue #{issue_number}.",
    )
    return 0


def load_event_payload() -> dict[str, object]:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        raise RuntimeError("GITHUB_EVENT_PATH is required")
    try:
        return json.loads(Path(event_path).read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"unable to read {event_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON in {event_path}: {error}") from error


def fetch_issue(repo: str, issue_number: str, token: str) -> dict[str, object]:
    owner, name = repo.split("/", 1)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{name}/issues/{issue_number}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"HTTP {error.code}: {body}") from error


def export_execution(ticket_id: str, issue_number: str, status_message: str) -> None:
    write_output("should_execute", "true")
    write_output("scd_ticket_id", ticket_id)
    write_output("status_message", status_message)
    write_env("SCD_TICKET_ID", ticket_id)
    write_env("EXECUTE_ISSUE_NUMBER", issue_number)
    print(status_message)


def skip_execution(status_message: str) -> None:
    write_output("should_execute", "false")
    write_output("scd_ticket_id", "")
    write_output("status_message", status_message)
    print(status_message)


def write_output(name: str, value: str) -> None:
    append_github_file(os.environ.get("GITHUB_OUTPUT", ""), name, value)


def write_env(name: str, value: str) -> None:
    append_github_file(os.environ.get("GITHUB_ENV", ""), name, value)


def append_github_file(path_value: str, name: str, value: str) -> None:
    path = path_value.strip()
    if not path:
        return
    sanitized = value.replace("\r", " ").replace("\n", " ")
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={sanitized}\n")


if __name__ == "__main__":
    raise SystemExit(main())