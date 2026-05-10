from __future__ import annotations

import json
import urllib.error
import urllib.request


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


def post_public_issue_comment(
    base: str,
    scd_id: str,
    headers: dict[str, str],
    *,
    adf_body: dict[str, object],
    label: str,
) -> int:
    payload = {"body": adf_body}
    request = urllib.request.Request(
        base.rstrip("/") + f"/rest/api/3/issue/{scd_id}/comment",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"{label} failed: HTTP {exc.code}: {error_body}") from exc

    if status != 201:
        raise RuntimeError(f"{label} failed: expected 201, got {status}")
    return status


def post_internal_note_issue_comment(
    base: str,
    scd_id: str,
    headers: dict[str, str],
    *,
    comment_text: str,
    label: str,
) -> int:
    payload = {
        "body": build_plain_text_adf(comment_text),
        "properties": [
            {
                "key": "sd.public.comment",
                "value": {"internal": True},
            }
        ],
    }
    request = urllib.request.Request(
        base.rstrip("/") + f"/rest/api/3/issue/{scd_id}/comment",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"{label} failed: HTTP {exc.code}: {error_body}") from exc

    if status != 201:
        raise RuntimeError(f"{label} failed: expected 201, got {status}")
    return status