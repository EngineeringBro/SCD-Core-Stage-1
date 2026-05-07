from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path


MAX_MP3_ATTACHMENTS = 3
MAX_MP3_ATTACHMENT_BYTES = 15_000_000
MP3_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/x-mp3",
    "audio/x-mpeg-3",
    "audio/mpg",
}


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

    def get_mp3_attachments(self, issue: dict, max_attachments: int = MAX_MP3_ATTACHMENTS) -> list[dict[str, object]]:
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        if not isinstance(fields, dict):
            return []

        raw_attachments = fields.get("attachment")
        if not isinstance(raw_attachments, list):
            return []

        hydrated_attachments: list[dict[str, object]] = []
        for raw_attachment in raw_attachments:
            if len(hydrated_attachments) >= max_attachments:
                break
            if not isinstance(raw_attachment, dict):
                continue

            hydrated_attachment = self._read_mp3_attachment(raw_attachment)
            if hydrated_attachment is not None:
                hydrated_attachments.append(hydrated_attachment)

        return hydrated_attachments

    def _read_mp3_attachment(self, attachment: dict) -> dict[str, object] | None:
        filename = str(attachment.get("filename") or "").strip()
        mime_type = str(attachment.get("mimeType") or "").strip().lower()
        content_url = str(attachment.get("content") or "").strip()

        try:
            size = int(attachment.get("size") or 0)
        except (TypeError, ValueError):
            size = 0

        if not content_url:
            return None
        if not self._is_mp3_attachment(filename, mime_type):
            return None
        if size and size > MAX_MP3_ATTACHMENT_BYTES:
            return None

        attachment_bytes = self._download_attachment_bytes(content_url)
        if not attachment_bytes:
            return None

        return {
            "filename": filename or "attachment",
            "mime_type": mime_type or "audio/mpeg",
            "size": size or len(attachment_bytes),
            "content_bytes": attachment_bytes,
        }

    def _is_mp3_attachment(self, filename: str, mime_type: str) -> bool:
        if Path(filename).suffix.lower() == ".mp3":
            return True
        return mime_type in MP3_MIME_TYPES

    def _download_attachment_bytes(self, content_url: str) -> bytes:
        headers = {
            "Authorization": self._headers["Authorization"],
            "Accept": "*/*",
        }
        request = urllib.request.Request(content_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = response.read(MAX_MP3_ATTACHMENT_BYTES + 1)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Jira attachment API {exc.code} on {content_url[:100]}: {body}") from exc

        if len(payload) > MAX_MP3_ATTACHMENT_BYTES:
            raise RuntimeError(f"Jira attachment exceeded {MAX_MP3_ATTACHMENT_BYTES} bytes: {content_url[:100]}")
        return payload

    def search(self, jql: str, fields: list[str] | None = None, max_results: int = 50) -> list:
        data = self._search_jql(
            {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields or ["*all"],
            }
        )
        return data.get("issues", [])
