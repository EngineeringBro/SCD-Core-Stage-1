from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from execute_router import fetch_latest_module_issue, fetch_module_issue_by_number, issue_matches_ticket


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

    issue_number_override = os.environ.get("EXECUTE_ISSUE_NUMBER", "").strip()
    if issue_number_override:
        issue = fetch_module_issue_by_number(repo, issue_number_override, token)
        if not issue_matches_ticket(issue, scd_id):
            raise RuntimeError(f"close execute issue failed: issue #{issue_number_override} does not match {scd_id}")
    else:
        issue = fetch_latest_module_issue(repo, scd_id, token)

    issue_number = str(issue.get("number") or "").strip()
    if not issue_number:
        raise RuntimeError(f"close execute issue failed: latest module issue for {scd_id} has no number")

    if str(issue.get("state") or "").strip().lower() == "closed":
        print(f"Closed GitHub issue: already closed #{issue_number}")
        return 0

    owner, name = repo.split("/", 1)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{name}/issues/{issue_number}",
        data=json.dumps(
            {
                "state": "closed",
                "state_reason": "completed",
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(
            f"close execute issue failed for #{issue_number}: HTTP {exc.code}: {error_body}"
        ) from exc

    if status != 200:
        raise RuntimeError(f"close execute issue failed for #{issue_number}: expected 200, got {status}")

    print(f"Closed GitHub issue: #{issue_number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())