from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.notifications_module.notification_matcher import classify_ticket
from modules.notifications_module.profiles import PROFILES


MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge/raw"
OUTPUT_DIR = MODULE_DIR / "output"
DEFAULT_JSON_OUTPUT = OUTPUT_DIR / "profile-topic-resolution.json"
DEFAULT_MD_OUTPUT = OUTPUT_DIR / "profile-topic-resolution.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build per-profile topic and resolution output from the Stage 1 notification cache."
    )
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    return parser.parse_args()


def text(value: Any) -> str:
    return str(value or "").strip()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def build_ticket_details(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue": {
            "fields": {
                "summary": record.get("summary") or "",
                "customfield_10170": {"value": record.get("topic") or ""},
                "status": {"name": record.get("status") or ""},
                "description": record.get("description") or "",
                "reporter": {"emailAddress": record.get("reporter_email") or ""},
            }
        },
        "comments": record.get("comments") or [],
    }


def top_entries(counter: Counter[str]) -> list[dict[str, object]]:
    return [{"value": key, "count": count} for key, count in counter.most_common()]


def analyze(knowledge_dir: Path) -> dict[str, object]:
    cache_files = sorted(knowledge_dir.glob("tickets_cache*.jsonl.gz"))
    if not cache_files:
        raise FileNotFoundError(f"No cache files found under {knowledge_dir}")

    stats: dict[str, dict[str, object]] = {
        profile.case_id: {
            "display_name": profile.display_name,
            "matched": 0,
            "topics": Counter(),
            "resolutions": Counter(),
        }
        for profile in PROFILES
    }

    for cache_path in cache_files:
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                ticket_id = text(record.get("ticket_id") or record.get("key") or record.get("id") or "UNKNOWN")
                ticket_details = build_ticket_details(record)
                result = classify_ticket(ticket_id, ticket_details)
                case_id = result.matched_case_id
                if case_id is None:
                    continue

                topic = text(record.get("topic")) or "(blank)"
                resolution = text(record.get("resolution")) or "(blank)"
                profile_stats = stats[case_id]
                profile_stats["matched"] += 1
                profile_stats["topics"][topic] += 1
                profile_stats["resolutions"][resolution] += 1

    profile_rows: list[dict[str, object]] = []
    for profile in PROFILES:
        profile_stats = stats[profile.case_id]
        profile_rows.append(
            {
                "case_id": profile.case_id,
                "display_name": profile.display_name,
                "matched": profile_stats["matched"],
                "topics": top_entries(profile_stats["topics"]),
                "resolutions": top_entries(profile_stats["resolutions"]),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_dir": str(knowledge_dir),
        "cache_files": [str(path) for path in cache_files],
        "profiles": profile_rows,
    }


def render_markdown(data: dict[str, object]) -> str:
    lines = [
        "# Notification Profile Topic And Resolution Output",
        "",
        f"Generated: {data['generated_at']}",
        f"Knowledge Dir: {data['knowledge_dir']}",
        "",
    ]

    for profile in data["profiles"]:
        lines.extend(
            [
                f"## {profile['case_id']}",
                "",
                f"- Display name: {profile['display_name']}",
                f"- Matched tickets: {profile['matched']}",
                "",
                "### Topics",
                "",
            ]
        )
        topics = profile["topics"]
        if topics:
            for entry in topics:
                lines.append(f"- {entry['value']}: {entry['count']}")
        else:
            lines.append("- None")

        lines.extend(["", "### Resolutions", ""])
        resolutions = profile["resolutions"]
        if resolutions:
            for entry in resolutions:
                lines.append(f"- {entry['value']}: {entry['count']}")
        else:
            lines.append("- None")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    data = analyze(args.knowledge_dir)

    ensure_parent(args.json_output)
    args.json_output.write_text(json.dumps(data, indent=2), encoding="utf-8")

    ensure_parent(args.md_output)
    args.md_output.write_text(render_markdown(data), encoding="utf-8")

    print(f"Wrote {args.json_output}")
    print(f"Wrote {args.md_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())