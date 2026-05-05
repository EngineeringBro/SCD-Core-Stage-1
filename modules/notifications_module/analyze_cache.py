from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


KNOWLEDGE_DIR = Path("knowledge/raw")
OUTPUT_DIR = Path("knowledge/analysis")
DEFAULT_JSON_OUTPUT = OUTPUT_DIR / "notification-analysis.json"
DEFAULT_MD_OUTPUT = OUTPUT_DIR / "notification-analysis.md"
CLOSED_STATUSES = {"closed", "resolved", "done"}
SYSTEM_REPORTERS = {"noreply@repairq.io", "mail@repairq.io", "azure-noreply@microsoft.com"}
SYSTEM_SUMMARY_PREFIXES = {
    "Asurion: Error updating inventory quantities": "asurion_inventory_error",
    "Assurant: Error updating inventory quantities": "assurant_inventory_error",
    "Revv Error Report": "revv_error_report_blank_topic",
    "Notify of the tasks completed": "notify_tasks_completed",
    "Welcome to RepairQ": "welcome_to_repairq",
    "GSX Permission Violation Alert": "gsx_permission_violation",
    "Error: RepairQ Journal Entries to QuickBooks Online": "quickbooks_journal_entry_error",
    "RepairQ task completed": "repairq_task_completed",
}


@dataclass(frozen=True)
class Sample:
    key: str
    summary: str
    topic: str
    status: str
    resolution: str
    comments: int
    reporter_email: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze the Stage 1 historical SCD cache for notification-like ticket families "
            "and closed-without-comment patterns."
        )
    )
    parser.add_argument("--knowledge-dir", type=Path, default=KNOWLEDGE_DIR)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--sample-size", type=int, default=12)
    return parser.parse_args()


def text(value: object) -> str:
    return str(value or "").strip()


def is_closed_like(record: dict) -> bool:
    return text(record.get("status")).lower() in CLOSED_STATUSES


def comment_count(record: dict) -> int:
    return len(record.get("comments") or [])


def sample_from_record(record: dict) -> Sample:
    return Sample(
        key=text(record.get("key")),
        summary=text(record.get("summary")),
        topic=text(record.get("topic")),
        status=text(record.get("status")),
        resolution=text(record.get("resolution")),
        comments=comment_count(record),
        reporter_email=text(record.get("reporter_email")),
    )


def top_entries(counter: Counter[str], limit: int) -> list[dict[str, object]]:
    return [{"value": key, "count": count} for key, count in counter.most_common(limit)]


def explicit_family(record: dict) -> str | None:
    topic = text(record.get("topic"))
    summary = text(record.get("summary"))
    reporter = text(record.get("reporter_email")).lower()
    if topic == "Revv Error Report" or summary.startswith("Revv Error Report"):
        return "revv_error_report"
    if topic == "Azure Notification" or reporter == "azure-noreply@microsoft.com" or summary.startswith("Azure:"):
        return "azure_notification"
    return None


def blank_topic_system_family(record: dict) -> str | None:
    topic = text(record.get("topic"))
    reporter = text(record.get("reporter_email")).lower()
    summary = text(record.get("summary"))
    if topic:
        return None
    if reporter not in SYSTEM_REPORTERS:
        return None
    for prefix, family in SYSTEM_SUMMARY_PREFIXES.items():
        if summary.startswith(prefix):
            return family
    return None


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def analyze(knowledge_dir: Path, sample_size: int) -> dict[str, object]:
    cache_files = sorted(knowledge_dir.glob("tickets_cache*.jsonl.gz"))
    if not cache_files:
        raise FileNotFoundError(f"No cache files found under {knowledge_dir}")

    totals = {
        "tickets": 0,
        "closed": 0,
        "closed_without_comments": 0,
        "notification_like": 0,
        "notification_like_closed_without_comments": 0,
    }
    explicit_stats: dict[str, dict[str, object]] = {}
    blank_topic_stats: dict[str, dict[str, object]] = {}
    silent_close = {
        "top_topics": Counter(),
        "top_resolutions": Counter(),
        "top_reporters": Counter(),
        "top_blank_summary_prefixes": Counter(),
        "samples_by_topic": defaultdict(list),
    }

    def family_stats(container: dict[str, dict[str, object]], family: str) -> dict[str, object]:
        return container.setdefault(
            family,
            {
                "total": 0,
                "closed": 0,
                "closed_without_comments": 0,
                "resolutions": Counter(),
                "reporters": Counter(),
                "topics": Counter(),
                "samples": [],
            },
        )

    for cache_path in cache_files:
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                totals["tickets"] += 1
                closed_like = is_closed_like(record)
                zero_comments = comment_count(record) == 0
                topic = text(record.get("topic")) or "(blank)"
                resolution = text(record.get("resolution")) or "(blank)"
                reporter = text(record.get("reporter_email")) or "(blank)"
                summary = text(record.get("summary"))

                if closed_like:
                    totals["closed"] += 1
                if closed_like and zero_comments:
                    totals["closed_without_comments"] += 1
                    silent_close["top_topics"][topic] += 1
                    silent_close["top_resolutions"][resolution] += 1
                    silent_close["top_reporters"][reporter] += 1
                    if topic == "(blank)":
                        prefix = summary.split(" - ")[0] if summary else "(blank)"
                        silent_close["top_blank_summary_prefixes"][prefix] += 1
                    topic_samples = silent_close["samples_by_topic"][topic]
                    if len(topic_samples) < sample_size:
                        topic_samples.append(sample_from_record(record).__dict__)

                family = explicit_family(record)
                if family:
                    stats = family_stats(explicit_stats, family)
                    totals["notification_like"] += 1
                    stats["total"] += 1
                    stats["resolutions"][resolution] += 1
                    stats["reporters"][reporter] += 1
                    stats["topics"][topic] += 1
                    if len(stats["samples"]) < sample_size:
                        stats["samples"].append(sample_from_record(record).__dict__)
                    if closed_like:
                        stats["closed"] += 1
                    if closed_like and zero_comments:
                        stats["closed_without_comments"] += 1
                        totals["notification_like_closed_without_comments"] += 1

                blank_family = blank_topic_system_family(record)
                if blank_family:
                    stats = family_stats(blank_topic_stats, blank_family)
                    totals["notification_like"] += 1
                    stats["total"] += 1
                    stats["resolutions"][resolution] += 1
                    stats["reporters"][reporter] += 1
                    stats["topics"][topic] += 1
                    if len(stats["samples"]) < sample_size:
                        stats["samples"].append(sample_from_record(record).__dict__)
                    if closed_like:
                        stats["closed"] += 1
                    if closed_like and zero_comments:
                        stats["closed_without_comments"] += 1
                        totals["notification_like_closed_without_comments"] += 1

    def finalize(container: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
        result = {}
        for family, stats in sorted(container.items()):
            result[family] = {
                "total": stats["total"],
                "closed": stats["closed"],
                "closed_without_comments": stats["closed_without_comments"],
                "top_resolutions": top_entries(stats["resolutions"], 8),
                "top_reporters": top_entries(stats["reporters"], 5),
                "top_topics": top_entries(stats["topics"], 5),
                "samples": stats["samples"],
            }
        return result

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_dir": str(knowledge_dir),
        "cache_files": [str(path) for path in cache_files],
        "totals": totals,
        "notification_families": finalize(explicit_stats),
        "blank_topic_system_families": finalize(blank_topic_stats),
        "silent_close": {
            "top_topics": top_entries(silent_close["top_topics"], 15),
            "top_resolutions": top_entries(silent_close["top_resolutions"], 15),
            "top_reporters": top_entries(silent_close["top_reporters"], 15),
            "top_blank_summary_prefixes": top_entries(silent_close["top_blank_summary_prefixes"], 20),
            "samples_by_topic": dict(silent_close["samples_by_topic"]),
        },
    }


def render_markdown(data: dict[str, object]) -> str:
    totals = data["totals"]
    explicit = data["notification_families"]
    blank_topic = data["blank_topic_system_families"]
    silent_close = data["silent_close"]
    lines = [
        "# Notification Cache Analysis",
        "",
        f"Generated: {data['generated_at']}",
        f"Knowledge Dir: {data['knowledge_dir']}",
        "",
        "## Summary",
        "",
        f"- Tickets scanned: {totals['tickets']}",
        f"- Closed-like tickets: {totals['closed']}",
        f"- Closed with zero comments: {totals['closed_without_comments']}",
        f"- Notification-like family matches: {totals['notification_like']}",
        f"- Notification-like family matches closed with zero comments: {totals['notification_like_closed_without_comments']}",
        "",
        "## Explicit Notification Families",
        "",
    ]
    for family, stats in explicit.items():
        lines.append(f"### {family}")
        lines.append("")
        lines.append(f"- Total: {stats['total']}")
        lines.append(f"- Closed: {stats['closed']}")
        lines.append(f"- Closed with zero comments: {stats['closed_without_comments']}")
        lines.append("- Top resolutions: " + ", ".join(f"{item['value']} ({item['count']})" for item in stats["top_resolutions"]))
        lines.append("- Top reporters: " + ", ".join(f"{item['value']} ({item['count']})" for item in stats["top_reporters"]))
        lines.append("- Sample tickets:")
        for sample in stats["samples"][:5]:
            lines.append(f"  - {sample['key']} | {sample['summary']} | topic={sample['topic'] or '(blank)'} | resolution={sample['resolution'] or '(blank)'} | comments={sample['comments']}")
        lines.append("")
    lines.append("## Blank Topic System Families")
    lines.append("")
    for family, stats in blank_topic.items():
        lines.append(f"### {family}")
        lines.append("")
        lines.append(f"- Total: {stats['total']}")
        lines.append(f"- Closed: {stats['closed']}")
        lines.append(f"- Closed with zero comments: {stats['closed_without_comments']}")
        lines.append("- Top resolutions: " + ", ".join(f"{item['value']} ({item['count']})" for item in stats["top_resolutions"]))
        lines.append("- Sample tickets:")
        for sample in stats["samples"][:5]:
            lines.append(f"  - {sample['key']} | {sample['summary']} | resolution={sample['resolution'] or '(blank)'} | reporter={sample['reporter_email'] or '(blank)'}")
        lines.append("")
    lines.extend(["## Silent Close Distribution", "", "### Top Topics", ""])
    for item in silent_close["top_topics"]:
        lines.append(f"- {item['value']}: {item['count']}")
    lines.extend(["", "### Top Reporters", ""])
    for item in silent_close["top_reporters"]:
        lines.append(f"- {item['value']}: {item['count']}")
    lines.extend(["", "### Top Blank Summary Prefixes", ""])
    for item in silent_close["top_blank_summary_prefixes"]:
        lines.append(f"- {item['value']}: {item['count']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    analysis = analyze(args.knowledge_dir, args.sample_size)
    ensure_parent(args.json_output)
    ensure_parent(args.md_output)
    args.json_output.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    args.md_output.write_text(render_markdown(analysis), encoding="utf-8")
    print(f"Wrote {args.json_output}")
    print(f"Wrote {args.md_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())