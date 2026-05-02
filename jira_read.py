from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request


class JiraReadClient:
    """Read-only Jira client for Stage 1 fetching."""

    def __init__(self) -> None:
        email = os.environ["JIRA_EMAIL"]
        token = os.environ["JIRA_API_TOKEN"]
        self.base = os.environ["JIRA_BASE_URL"].rstrip("/")
        creds = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> dict:
        url = self.base + path
        print(f"[jira] GET {url[:120]}")
        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Jira API {exc.code} on {url[:100]}: {body}") from exc

    def _post(self, *args, **kwargs):
        raise PermissionError("JiraReadClient cannot make POST requests.")

    def _put(self, *args, **kwargs):
        raise PermissionError("JiraReadClient cannot make PUT requests.")

    def _search_jql(self, payload: dict) -> dict:
        params: dict = {
            "jql": payload["jql"],
            "maxResults": payload.get("maxResults", 50),
            "fields": ",".join(payload["fields"])
            if isinstance(payload.get("fields"), list)
            else (payload.get("fields") or "*all"),
        }
        if payload.get("nextPageToken"):
            params["nextPageToken"] = payload["nextPageToken"]
        return self._get(f"/rest/api/3/search/jql?{urllib.parse.urlencode(params)}")

    def get_issue(self, ticket_id: str, fields: list[str] | None = None) -> dict:
        if fields:
            params = urllib.parse.urlencode({"fields": ",".join(fields)})
            return self._get(f"/rest/api/3/issue/{ticket_id}?{params}")
        return self._get(f"/rest/api/3/issue/{ticket_id}")

    def get_comments(self, ticket_id: str) -> list:
        data = self._get(f"/rest/api/3/issue/{ticket_id}/comment")
        return data.get("comments", [])

    def search(self, jql: str, fields: list[str] | None = None, max_results: int = 50) -> list:
        data = self._search_jql(
            {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields or ["*all"],
            }
        )
        return data.get("issues", [])