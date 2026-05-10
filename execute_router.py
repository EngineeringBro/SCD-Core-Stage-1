from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


MODERN_TITLE_PATTERN = re.compile(r"\[(SCD-\d+)\s*-\s*([^\]]+?)\]\s*$", re.IGNORECASE)
LEGACY_TITLE_PATTERN = re.compile(r"^\[[^\]]+\]\s*(SCD-\d+)\s*-\s*(.+?)\s*$", re.IGNORECASE)


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

    issue_number = os.environ.get("EXECUTE_ISSUE_NUMBER", "").strip()
    if issue_number:
        issue = fetch_module_issue_by_number(repo, issue_number, token)
        if not issue_matches_ticket(issue, scd_id):
            raise RuntimeError(f"execute router found issue #{issue_number}, but it does not match {scd_id}")
    else:
        issue = fetch_latest_module_issue(repo, scd_id, token)

    module_name = resolve_module_name_from_issue(issue, scd_id)
    print(module_name)
    return 0


def fetch_module_issue_by_number(repo: str, issue_number: str, token: str) -> dict[str, object]:
    owner, name = repo.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{name}/issues/{issue_number}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(
            f"execute router failed to read issue #{issue_number}: HTTP {exc.code}: {error_body}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"execute router expected a GitHub issue object for #{issue_number}")

    if "pull_request" in payload:
        raise RuntimeError(f"execute router issue #{issue_number} is a pull request, not a module issue")

    return payload


def fetch_latest_module_issue(repo: str, scd_id: str, token: str) -> dict[str, object]:
    owner, name = repo.split("/", 1)
    query = urllib.parse.urlencode(
        {
            "state": "all",
            "sort": "created",
            "direction": "desc",
            "per_page": "100",
        }
    )
    url = f"https://api.github.com/repos/{owner}/{name}/issues?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"execute router failed to read issues: HTTP {exc.code}: {error_body}") from exc

    if not isinstance(payload, list):
        raise RuntimeError("execute router expected a GitHub issues list")

    for item in payload:
        if not isinstance(item, dict):
            continue
        if "pull_request" in item:
            continue
        if issue_matches_ticket(item, scd_id):
            return item

    raise RuntimeError(f"execute router could not find a module output issue for {scd_id}")


def issue_matches_ticket(issue: dict[str, object], scd_id: str) -> bool:
    normalized_ticket_id = scd_id.strip().upper()
    if not normalized_ticket_id:
        return False

    title = str(issue.get("title") or "").strip()
    if title:
        title_metadata = parse_issue_title_metadata(title)
        if title_metadata and title_metadata[0] == normalized_ticket_id:
            return True

        # Backstop for older titles that still contain the ticket but do not
        # follow one of the recognized structured title formats.
        title_pattern = re.compile(rf"(?:\]\s*|\[){re.escape(normalized_ticket_id)}\s*-\s+", re.IGNORECASE)
        if title_pattern.search(title):
            return True

    body = str(issue.get("body") or "")
    if body:
        body_pattern = re.compile(rf"(^|\n)-\s*Ticket ID:\s*{re.escape(normalized_ticket_id)}\b", re.IGNORECASE)
        if body_pattern.search(body):
            return True

    return False


def resolve_module_name_from_issue(issue: dict[str, object], scd_id: str) -> str:
    title = str(issue.get("title") or "").strip()
    if title:
        title_metadata = parse_issue_title_metadata(title)
        if title_metadata and title_metadata[0] == scd_id.strip().upper() and title_metadata[1]:
            return title_metadata[1]

    body = str(issue.get("body") or "")
    match = re.search(r"<!--\s*module_id:\s*([a-z0-9_\-]+)\s*-->", body, re.IGNORECASE)
    if match:
        module_name = match.group(1).strip().lower()
        if module_name:
            return module_name
    issue_number = str(issue.get("number") or "unknown")
    raise RuntimeError(
        f"execute router could not resolve module from persisted module marker in issue #{issue_number} for {scd_id}: {title}"
    )


def parse_issue_title_metadata(title: str) -> tuple[str, str, str] | None:
    normalized_title = str(title or "").strip()
    if not normalized_title:
        return None

    match = MODERN_TITLE_PATTERN.search(normalized_title)
    if not match:
        match = LEGACY_TITLE_PATTERN.search(normalized_title)
    if not match:
        return None

    ticket_id = str(match.group(1) or "").strip().upper()
    module_display_name = str(match.group(2) or "").strip()
    if not ticket_id or not module_display_name:
        return None

    module_name = normalize_module_name_from_title(module_display_name)
    if not module_name:
        return None

    return ticket_id, module_name, module_display_name


def normalize_module_name_from_title(module_display_name: str) -> str:
    normalized_display = re.sub(r"[^a-z0-9]+", " ", str(module_display_name or "").lower()).strip()
    if not normalized_display:
        return ""

    if "general knowledge module" in normalized_display:
        return "general"
    if "spam module" in normalized_display:
        return "spam"
    if "notifications module" in normalized_display:
        return "notification"
    if "orphaned transaction module" in normalized_display:
        return "orphaned_transaction"
    if "ringcentral module" in normalized_display or "ring central module" in normalized_display:
        return "ringcentral"

    return ""


if __name__ == "__main__":
    raise SystemExit(main())