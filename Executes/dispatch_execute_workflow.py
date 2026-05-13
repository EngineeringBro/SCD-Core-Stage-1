from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path


PERMISSION_LABEL = "permission to execute"
EVENT_TYPE = "authorized_execute"


def main() -> int:
    payload = load_event_payload()
    action = str(payload.get("action") or "").strip().lower()
    if action != "labeled":
        print(f"Skipped execute gate: unsupported issue action '{action or 'unknown'}'.")
        return 0

    issue = payload.get("issue") if isinstance(payload, dict) else None
    if not isinstance(issue, dict):
        print("Skipped execute gate: issue payload is missing.")
        return 0

    if issue.get("pull_request"):
        print("Skipped execute gate: pull request events are not supported.")
        return 0

    issue_number = str(issue.get("number") or "").strip()
    if not issue_number:
        print("Skipped execute gate: issue number is missing.")
        return 0

    label = payload.get("label") if isinstance(payload.get("label"), dict) else {}
    label_name = str(label.get("name") or "").strip().lower()
    if label_name != PERMISSION_LABEL:
        print(f"Skipped execute gate: label '{label_name or 'unknown'}' is not authorized for execute.")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GH_TOKEN", "").strip()
    if not repo or "/" not in repo or not token:
        raise RuntimeError("GITHUB_REPOSITORY and GH_TOKEN are required")

    verified_issue = fetch_issue(repo, issue_number, token)

    if str(verified_issue.get("state") or "").strip().lower() != "closed":
        print(f"Skipped execute gate: issue #{issue_number} is not closed.")
        return 0

    if str(verified_issue.get("state_reason") or "").strip().lower() != "completed":
        print(f"Skipped execute gate: issue #{issue_number} was not closed as completed.")
        return 0

    labels = verified_issue.get("labels") if isinstance(verified_issue, dict) else []
    label_names = {
        str(label.get("name") or "").strip().lower()
        for label in labels
        if isinstance(label, dict)
    }
    if PERMISSION_LABEL not in label_names:
        print(f"Skipped execute gate: issue #{issue_number} is missing 'Permission to Execute'.")
        return 0

    issue_title = str(verified_issue.get("title") or "")
    title_match = re.search(r"\[(SCD-\d+)\s*-\s*([^\]]+?)\]\s*$", issue_title, re.IGNORECASE)
    if not title_match:
        title_match = re.search(r"^\[[^\]]+\]\s*(SCD-\d+)\s*-\s*(.+?)\s*$", issue_title, re.IGNORECASE)
    if not title_match:
        print(f"Skipped execute gate: issue #{issue_number} title is not parseable.")
        return 0

    ticket_id = str(title_match.group(1) or "").strip().upper()
    module_name = str(title_match.group(2) or "").strip()
    if not ticket_id or not module_name:
        print(f"Skipped execute gate: issue #{issue_number} is missing ticket or module data.")
        return 0

    default_branch = "main"
    repository = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    if isinstance(repository, dict):
        default_branch = str(repository.get("default_branch") or "main").strip() or "main"

    dispatch_execute(
        repo=repo,
        token=token,
        ref=default_branch,
        ticket_id=ticket_id,
        issue_number=issue_number,
        status_message=f"Authorized execute for {ticket_id} ({module_name}) from issue #{issue_number}.",
    )
    print(f"Dispatched execute for {ticket_id} from issue #{issue_number}.")
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
        raise RuntimeError(f"issue lookup failed: HTTP {error.code}: {body}") from error


def dispatch_execute(repo: str, token: str, ref: str, ticket_id: str, issue_number: str, status_message: str) -> None:
    owner, name = repo.split("/", 1)
    payload = {
        "event_type": EVENT_TYPE,
        "client_payload": {
            "scd_ticket_id": ticket_id,
            "execute_issue_number": issue_number,
            "status_message": status_message,
            "ref": ref,
        },
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{name}/dispatches",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            if response.status != 204:
                raise RuntimeError(f"repository dispatch failed: expected 204, got {response.status}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"repository dispatch failed: HTTP {error.code}: {body}") from error


if __name__ == "__main__":
    raise SystemExit(main())