#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("export_knowledgebase")

DEFAULT_SEED_QUERIES = list("abcdefghijklmnopqrstuvwxyz0123456789")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Jira Service Management portal structure and linked knowledge-base content.",
    )
    parser.add_argument(
        "--env-file",
        help="Optional .env path. Defaults to repo-local and sibling Cowork fallbacks.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path("knowledge") / "knowledgebase"),
        help="Output directory for the local mirror.",
    )
    parser.add_argument(
        "--desk-id",
        action="append",
        dest="desk_ids",
        help="Restrict export to one or more service desk IDs.",
    )
    parser.add_argument(
        "--seed-queries",
        default="".join(DEFAULT_SEED_QUERIES),
        help="Characters used to discover knowledge-base spaces before Confluence export.",
    )
    parser.add_argument(
        "--max-spaces",
        type=int,
        default=0,
        help="Limit the number of Confluence spaces exported for validation runs.",
    )
    parser.add_argument(
        "--max-pages-per-space",
        type=int,
        default=0,
        help="Limit pages exported per Confluence space for validation runs.",
    )
    parser.add_argument(
        "--skip-confluence",
        action="store_true",
        help="Only export portal/JSM data and KB article metadata, not full Confluence pages.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def load_env_file(env_file_path: str | None = None) -> tuple[dict[str, str], str]:
    repo_root = Path(__file__).resolve().parent
    candidate_paths: list[Path] = []

    if env_file_path:
        candidate_paths.append(Path(env_file_path))
    else:
        candidate_paths.extend(
            [
                repo_root / ".env",
                repo_root.parent / ".env",
                repo_root.parent / "Cowork" / ".env",
                repo_root.parent / "Cowork" / ".service-central-copilot" / "tools" / "jira_fetcher" / ".env",
            ]
        )

    env_vars: dict[str, str] = {}
    env_source = "shell environment"

    selected_path = next((path for path in candidate_paths if path.exists()), None)
    if selected_path is not None:
        env_source = str(selected_path)
        with selected_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip().strip('"\'')

    for key in ["JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_URL"]:
        if os.environ.get(key):
            env_vars[key] = os.environ[key].strip()

    if not env_vars.get("JIRA_BASE_URL") and env_vars.get("JIRA_URL"):
        env_vars["JIRA_BASE_URL"] = env_vars["JIRA_URL"]

    missing_keys = [
        key for key in ["JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_BASE_URL"] if not env_vars.get(key)
    ]
    if missing_keys:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing_keys))

    return env_vars, env_source


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "item"


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    ensure_directory(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def write_text(path: Path, payload: str) -> None:
    ensure_directory(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(payload)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_existing_page_dir(space_pages_dir: Path, page_id: str) -> Path | None:
    matches = sorted(space_pages_dir.glob(f"{page_id}-*"))
    return matches[0] if matches else None


class AtlassianReadClient:
    def __init__(self, env_vars: dict[str, str]) -> None:
        credentials = base64.b64encode(
            f"{env_vars['JIRA_EMAIL']}:{env_vars['JIRA_API_TOKEN']}".encode()
        ).decode()
        self.base_url = env_vars["JIRA_BASE_URL"].rstrip("/")
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.timeout = 30
        self.max_retries = 4

    def get_json(self, path_or_url: str) -> dict[str, Any]:
        url = self.resolve_url(path_or_url)
        for attempt in range(1, self.max_retries + 1):
            logger.debug("GET %s (attempt %s/%s)", url, attempt, self.max_retries)
            request = urllib.request.Request(url, headers=self.headers, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")[:1000]
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(min(2 ** (attempt - 1), 8))
                    continue
                raise RuntimeError(f"HTTP {exc.code} on {url}: {body}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                if attempt < self.max_retries:
                    time.sleep(min(2 ** (attempt - 1), 8))
                    continue
                raise RuntimeError(f"Network error on {url}: {exc}") from exc

        raise RuntimeError(f"Failed to GET {url} after {self.max_retries} attempts")

    def resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            return path_or_url
        if path_or_url.startswith("/wiki/"):
            return self.base_url + path_or_url
        if path_or_url.startswith("/rest/api/"):
            return self.base_url + "/wiki" + path_or_url
        return self.base_url + path_or_url

    def paginate_values(self, path: str, item_key: str = "values") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_target: str | None = path
        visited: set[str] = set()

        while next_target:
            if next_target in visited:
                raise RuntimeError(f"Pagination loop detected for {next_target}")
            visited.add(next_target)

            payload = self.get_json(next_target)
            batch = payload.get(item_key, [])
            if isinstance(batch, list):
                items.extend(batch)

            links = payload.get("_links", {})
            next_target = links.get("next")

        return items

    def iterate_confluence_space_pages(self, space_key: str) -> list[dict[str, Any]]:
        cql = f'space="{space_key}" AND type=page'
        query = urllib.parse.urlencode({"cql": cql, "limit": 100})
        path = f"/wiki/rest/api/content/search?{query}"
        pages: list[dict[str, Any]] = []
        next_target: str | None = path
        visited: set[str] = set()

        while next_target:
            if next_target in visited:
                raise RuntimeError(f"Confluence pagination loop detected for {next_target}")
            visited.add(next_target)

            payload = self.get_json(next_target)
            batch = payload.get("results", [])
            if isinstance(batch, list):
                pages.extend(batch)

            links = payload.get("_links", {})
            next_target = links.get("next")

        return pages

    def get_confluence_page(self, page_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"expand": "body.export_view,version,space"})
        return self.get_json(f"/wiki/rest/api/content/{page_id}?{query}")


def export_service_desks(
    client: AtlassianReadClient,
    output_dir: Path,
    desk_filter: set[str],
) -> list[dict[str, Any]]:
    desk_listing = client.get_json("/rest/servicedeskapi/servicedesk?limit=100")
    write_json(output_dir / "service_desks.json", desk_listing)

    service_desks = desk_listing.get("values", [])
    if not isinstance(service_desks, list):
        raise RuntimeError("Unexpected service desk response shape")

    selected_desks = [desk for desk in service_desks if not desk_filter or str(desk.get("id")) in desk_filter]

    for desk in selected_desks:
        desk_id = str(desk["id"])
        desk_name = str(desk.get("projectName") or desk.get("projectKey") or desk_id)
        desk_dir = output_dir / "service_desks" / f"{desk_id}-{slugify(desk_name)}"
        ensure_directory(desk_dir)

        write_json(desk_dir / "servicedesk.json", client.get_json(f"/rest/servicedeskapi/servicedesk/{desk_id}"))
        write_json(
            desk_dir / "requesttypegroup.json",
            client.get_json(f"/rest/servicedeskapi/servicedesk/{desk_id}/requesttypegroup?limit=100"),
        )
        request_type_payload = client.get_json(f"/rest/servicedeskapi/servicedesk/{desk_id}/requesttype?limit=100")
        write_json(desk_dir / "requesttype.json", request_type_payload)

        request_types = request_type_payload.get("values", [])
        if not isinstance(request_types, list):
            raise RuntimeError(f"Unexpected request type shape for desk {desk_id}")

        for request_type in request_types:
            request_type_id = str(request_type["id"])
            request_type_slug = slugify(str(request_type.get("name") or request_type_id))
            request_type_dir = desk_dir / "request_types" / f"{request_type_id}-{request_type_slug}"
            ensure_directory(request_type_dir)
            write_json(
                request_type_dir / "detail.json",
                client.get_json(
                    f"/rest/servicedeskapi/servicedesk/{desk_id}/requesttype/{request_type_id}"
                ),
            )
            write_json(
                request_type_dir / "fields.json",
                client.get_json(
                    f"/rest/servicedeskapi/servicedesk/{desk_id}/requesttype/{request_type_id}/field?limit=100"
                ),
            )

    return selected_desks


def discover_knowledgebase_articles(
    client: AtlassianReadClient,
    output_dir: Path,
    service_desks: list[dict[str, Any]],
    seed_queries: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    articles_by_page_id: dict[str, dict[str, Any]] = {}
    spaces_by_key: dict[str, dict[str, Any]] = {}
    discovery_dir = output_dir / "discovery"

    for desk in service_desks:
        desk_id = str(desk["id"])
        desk_name = str(desk.get("projectName") or desk.get("projectKey") or desk_id)
        desk_slug = slugify(desk_name)
        desk_discovery_dir = discovery_dir / f"{desk_id}-{desk_slug}"
        ensure_directory(desk_discovery_dir)

        for seed_query in seed_queries:
            encoded_query = urllib.parse.urlencode({"query": seed_query})
            path = f"/rest/servicedeskapi/servicedesk/{desk_id}/knowledgebase/article?{encoded_query}"
            articles = client.paginate_values(path)
            write_json(
                desk_discovery_dir / f"query-{slugify(seed_query)}.json",
                {"seedQuery": seed_query, "values": articles},
            )

            for article in articles:
                source = article.get("source") or {}
                page_id = str(source.get("pageId") or "").strip()
                space_key = str(source.get("spaceKey") or "").strip()
                if not page_id:
                    continue

                record = articles_by_page_id.setdefault(
                    page_id,
                    {
                        "pageId": page_id,
                        "title": article.get("title"),
                        "excerpt": article.get("excerpt"),
                        "source": source,
                        "content": article.get("content"),
                        "serviceDeskIds": [],
                        "seedQueries": [],
                    },
                )
                if desk_id not in record["serviceDeskIds"]:
                    record["serviceDeskIds"].append(desk_id)
                if seed_query not in record["seedQueries"]:
                    record["seedQueries"].append(seed_query)

                if space_key:
                    space_entry = spaces_by_key.setdefault(
                        space_key,
                        {
                            "spaceKey": space_key,
                            "pageIds": [],
                            "serviceDeskIds": [],
                        },
                    )
                    if page_id not in space_entry["pageIds"]:
                        space_entry["pageIds"].append(page_id)
                    if desk_id not in space_entry["serviceDeskIds"]:
                        space_entry["serviceDeskIds"].append(desk_id)

    write_json(output_dir / "knowledgebase_articles.json", sorted(articles_by_page_id.values(), key=lambda item: item["title"] or ""))
    write_json(output_dir / "knowledgebase_spaces.json", sorted(spaces_by_key.values(), key=lambda item: item["spaceKey"]))
    return articles_by_page_id, spaces_by_key


def export_confluence_spaces(
    client: AtlassianReadClient,
    output_dir: Path,
    spaces_by_key: dict[str, dict[str, Any]],
    max_spaces: int,
    max_pages_per_space: int,
) -> dict[str, Any]:
    export_summary = {"spaces": [], "pageCount": 0, "failedPages": []}
    selected_space_keys = sorted(spaces_by_key.keys())
    if max_spaces > 0:
        selected_space_keys = selected_space_keys[:max_spaces]

    for space_key in selected_space_keys:
        space_dir = output_dir / "spaces" / space_key
        ensure_directory(space_dir)
        space_pages_dir = space_dir / "pages"
        ensure_directory(space_pages_dir)
        pages = client.iterate_confluence_space_pages(space_key)
        if max_pages_per_space > 0:
            pages = pages[:max_pages_per_space]

        write_json(space_dir / "pages.index.json", pages)

        page_records: list[dict[str, Any]] = []
        for page_stub in pages:
            page_id = str(page_stub["id"])
            existing_page_dir = find_existing_page_dir(space_pages_dir, page_id)
            page_payload: dict[str, Any]

            if existing_page_dir is not None and (existing_page_dir / "page.json").exists():
                page_payload = read_json(existing_page_dir / "page.json")
            else:
                try:
                    page_payload = client.get_confluence_page(page_id)
                except RuntimeError as exc:
                    export_summary["failedPages"].append(
                        {"spaceKey": space_key, "pageId": page_id, "error": str(exc)}
                    )
                    logger.warning("Skipping page %s in %s after repeated failures: %s", page_id, space_key, exc)
                    continue

            page_title = str(page_payload.get("title") or page_id)
            page_slug = slugify(page_title)
            page_dir = space_pages_dir / f"{page_id}-{page_slug}"
            ensure_directory(page_dir)

            if not (page_dir / "page.json").exists():
                write_json(page_dir / "page.json", page_payload)
            export_view = (
                page_payload.get("body", {})
                .get("export_view", {})
                .get("value", "")
            )
            if not (page_dir / "body.export_view.html").exists():
                write_text(page_dir / "body.export_view.html", export_view)

            page_records.append(
                {
                    "id": page_id,
                    "title": page_title,
                    "spaceKey": space_key,
                    "version": page_payload.get("version", {}).get("number"),
                    "bodyExportViewPath": str((page_dir / "body.export_view.html").relative_to(output_dir)).replace("\\", "/"),
                }
            )

        write_json(space_dir / "pages.exported.json", page_records)
        export_summary["spaces"].append(
            {
                "spaceKey": space_key,
                "pageCount": len(page_records),
                "failedPageCount": len([item for item in export_summary["failedPages"] if item["spaceKey"] == space_key]),
            }
        )
        export_summary["pageCount"] += len(page_records)

    if export_summary["failedPages"]:
        write_json(output_dir / "confluence_failed_pages.json", export_summary["failedPages"])

    return export_summary


def main() -> int:
    args = parse_arguments()
    if args.debug:
        logger.setLevel(logging.DEBUG)

    env_vars, env_source = load_env_file(args.env_file)
    output_dir = Path(args.output_dir)
    ensure_directory(output_dir)

    client = AtlassianReadClient(env_vars)
    desk_filter = {desk_id.strip() for desk_id in args.desk_ids or [] if desk_id.strip()}
    seed_queries = [character for character in args.seed_queries if character.strip()]
    if not seed_queries:
        raise RuntimeError("At least one seed query character is required")

    logger.info("Exporting portal structure into %s", output_dir)
    service_desks = export_service_desks(client, output_dir, desk_filter)
    logger.info("Exported %s service desk records", len(service_desks))

    articles_by_page_id, spaces_by_key = discover_knowledgebase_articles(
        client,
        output_dir,
        service_desks,
        seed_queries,
    )
    logger.info(
        "Discovered %s knowledge-base articles across %s spaces",
        len(articles_by_page_id),
        len(spaces_by_key),
    )

    confluence_summary = {"spaces": [], "pageCount": 0}
    if not args.skip_confluence:
        confluence_summary = export_confluence_spaces(
            client,
            output_dir,
            spaces_by_key,
            args.max_spaces,
            args.max_pages_per_space,
        )
        logger.info(
            "Exported %s Confluence pages across %s spaces",
            confluence_summary["pageCount"],
            len(confluence_summary["spaces"]),
        )

    manifest = {
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "environmentSource": env_source,
        "baseUrl": env_vars["JIRA_BASE_URL"],
        "serviceDeskCount": len(service_desks),
        "knowledgebaseArticleCount": len(articles_by_page_id),
        "knowledgebaseSpaceCount": len(spaces_by_key),
        "confluencePageCount": confluence_summary["pageCount"],
        "confluenceFailedPageCount": len(confluence_summary.get("failedPages", [])),
        "seedQueries": seed_queries,
        "outputDir": str(output_dir),
    }
    write_json(output_dir / "manifest.json", manifest)
    logger.info("Knowledgebase export complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())