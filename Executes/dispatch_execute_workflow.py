from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path


PERMISSION_LABEL = "permission to execute"
EXECUTE_WORKFLOW_ID = "Execute_workflow.yml"


def main() -> int:
    payload = load_event_payload()
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"closed", "labeled"}:
        print(f"Skipped execute dispatch: unsupported issue action '{action or 'unknown'}'.")
        return 0

    issue = payload.get("issue") if isinstance(payload, dict) else None
    if not isinstance(issue, dict):
        raise RuntimeError("Issue payload is missing")
    if issue.get("pull_request"):
        print("Skipped execute dispatch: pull request events are not supported.")
        return 0

    if action == "labeled":
        added_label = payload.get("label") if isinstance(payload, dict) else None
        added_label_name = str((added_label or {}).get("name") or "").strip().lower() if isinstance(added_label, dict) else ""
        if added_label_name != PERMISSION_LABEL:
            print("Skipped execute dispatch: labeled event did not add Permission to Execute.")
            return 0

    state = str(issue.get("state") or "").strip().lower()
    state_reason = str(issue.get("state_reason") or "").strip().lower()
    labels = issue.get("labels") if isinstance(issue.get("labels"), list) else []
    label_names = {
        str(label.get("name") or "").strip().lower()
        for label in labels
        if isinstance(label, dict)
    }
    if state != "closed" or state_reason != "completed" or PERMISSION_LABEL not in label_names:
        print("Skipped execute dispatch: issue does not meet both authorization conditions.")
        return 0

    issue_number = str(issue.get("number") or "").strip()
    issue_title = str(issue.get("title") or "")
    ticket_id = extract_ticket_id(issue_title)
    if not ticket_id:
        raise RuntimeError("Issue title does not include a parseable SCD ticket id")

    repo = str(os.environ.get("GITHUB_REPOSITORY") or "").strip()
    token = str(os.environ.get("GH_TOKEN") or "").strip()
    if not repo or "/" not in repo:
        raise RuntimeError("GITHUB_REPOSITORY is required")
    if not token:
        raise RuntimeError("GH_TOKEN is required")

    default_branch = extract_default_branch(payload)
    dispatch_execute_workflow(repo, token, default_branch, ticket_id, issue_number)
    print(f"Dispatched Execute for {ticket_id} from issue #{issue_number}.")
    return 0


def load_event_payload() -> dict[str, object]:
    event_path = str(os.environ.get("GITHUB_EVENT_PATH") or "").strip()
    if not event_path:
        raise RuntimeError("GITHUB_EVENT_PATH is required")
    try:
        return json.loads(Path(event_path).read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"unable to read event payload: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid event payload JSON: {error}") from error


def extract_ticket_id(issue_title: str) -> str:
    match = re.search(r"\[(SCD-\d+)\s*-\s*[^\]]+\]\s*$", issue_title, re.IGNORECASE)
    if not match:
        match = re.search(r"^\[[^\]]+\]\s*(SCD-\d+)\s*-\s*.+$", issue_title, re.IGNORECASE)
    return str(match.group(1) or "").strip().upper() if match else ""


def extract_default_branch(payload: dict[str, object]) -> str:
    repository = payload.get("repository") if isinstance(payload, dict) else None
    if isinstance(repository, dict):
        branch = str(repository.get("default_branch") or "").strip()
        if branch:
            return branch
    return "main"


def dispatch_execute_workflow(repo: str, token: str, ref: str, ticket_id: str, issue_number: str) -> None:
    owner, name = repo.split("/", 1)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{name}/actions/workflows/{EXECUTE_WORKFLOW_ID}/dispatches",
        data=json.dumps(
            {
                "ref": ref,
                "inputs": {
                    "scd_ticket_id": ticket_id,
                    "execute_issue_number": issue_number,
                },
            }
        ).encode("utf-8"),
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
                raise RuntimeError(f"dispatch failed: expected 204, got {response.status}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"dispatch failed: HTTP {error.code}: {body}") from error


if __name__ == "__main__":
    raise SystemExit(main())