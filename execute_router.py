from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


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
    module_name = resolve_module_name_from_issue(issue, scd_id)
    print(module_name)
    return 0


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
        # Support both the legacy pattern `[recommendation] SCD-123 - Module`
        # and the new aligned pattern `Label [SCD-123 - Module]`.
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


if __name__ == "__main__":
    raise SystemExit(main())