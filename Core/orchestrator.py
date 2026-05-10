from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Callable


class StepFailure(RuntimeError):
    pass


CORE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CORE_ROOT.parent
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SCD_TICKET_KEY_PATTERN = re.compile(r"\b(SCD-\d+)\b", re.IGNORECASE)
SCD_TICKET_NUMBER_PATTERN = re.compile(r"^\d+$")


@dataclass(frozen=True)
class FetchResult:
    ticket_id: str
    ticket_details: dict[str, Any]


@dataclass(frozen=True)
class RouterResult:
    ticket_id: str
    module_name: str
    ticket_details: dict[str, Any]


@dataclass(frozen=True)
class ModuleResult:
    ticket_id: str
    module_name: str
    module_version: str
    module_display_name: str
    recommendation: str
    issue_body: str
    notes: list[str]
    module_payload: dict[str, Any] = field(default_factory=dict)


def run() -> str:
    fetch_result = run_step("fetcher", fetch_ticket_details)
    router_result = run_step("router", lambda: route_ticket(fetch_result))
    module_result = run_step("module", lambda: run_module(router_result))
    gatekeeper_result = run_step("gatekeeper", lambda: run_gatekeeper_step(module_result))
    issue_description = run_step(
        "issue_description",
        lambda: build_issue_description(module_result, gatekeeper_result),
    )
    return issue_description


def run_step(name: str, action: Callable[[], Any]) -> Any:
    try:
        result = action()
    except StepFailure:
        raise
    except Exception as exc:
        raise StepFailure(f"{name} step failed: {exc}") from exc

    if result is None:
        raise StepFailure(f"{name} step failed: no result returned")

    return result


def load_module(module_file_name: str, module_label: str) -> Any:
    module_path = REPO_ROOT / module_file_name
    spec = spec_from_file_location(module_label, module_path)
    if spec is None or spec.loader is None:
        raise StepFailure(f"could not load {module_file_name}")

    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fetch_ticket_details() -> FetchResult:
    scan_ticket_id = normalize_scan_ticket_id(os.getenv("SCAN_TICKET_ID", ""))
    if not scan_ticket_id:
        raise StepFailure("fetcher step failed: SCAN_TICKET_ID is required in Stage 1")

    try:
        from jira_read import JiraReadClient
    except ImportError as exc:
        raise StepFailure(f"fetcher step failed: could not import JiraReadClient from jira_read.py: {exc}") from exc

    client = JiraReadClient()
    issue = client.get_issue(scan_ticket_id)
    comments = client.get_comments(scan_ticket_id)
    mp3_attachments = client.get_mp3_attachments(issue)

    if not issue:
        raise StepFailure("fetcher step failed: jira_read.py returned no issue data")

    ticket_id = str(issue.get("key") or scan_ticket_id).strip()
    if not ticket_id:
        raise StepFailure("fetcher step failed: Jira issue key is missing")

    ticket_details = {
        "issue": issue,
        "comments": comments,
        "mp3_attachments": mp3_attachments,
    }
    return FetchResult(ticket_id=ticket_id, ticket_details=ticket_details)


def normalize_scan_ticket_id(raw_value: str) -> str:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return ""

    ticket_key_match = SCD_TICKET_KEY_PATTERN.search(normalized)
    if ticket_key_match:
        return ticket_key_match.group(1).upper()

    if SCD_TICKET_NUMBER_PATTERN.fullmatch(normalized):
        return f"SCD-{normalized}"

    return normalized.upper()


def route_ticket(fetch_result: FetchResult) -> RouterResult:
    if not fetch_result.ticket_id:
        raise StepFailure("router step failed: missing ticket id from fetcher")

    if not fetch_result.ticket_details:
        raise StepFailure("router step failed: missing ticket details from fetcher")

    router_module = load_module("Core/router.py", "scd_stage1_router")
    route_ticket_fn = getattr(router_module, "route_ticket", None)
    if route_ticket_fn is None or not hasattr(route_ticket_fn, "__call__"):
        raise StepFailure("router step failed: route_ticket is missing from Core/router.py")

    route_result = route_ticket_fn.__call__(fetch_result.ticket_id, fetch_result.ticket_details)
    if not isinstance(route_result, dict):
        raise StepFailure("router step failed: Core/router.py must return a dict")

    ticket_id = str(route_result.get("ticket_id") or "").strip()
    module_name = str(route_result.get("module_name") or "").strip()
    if not ticket_id:
        raise StepFailure("router step failed: Core/router.py returned an empty ticket_id")
    if not module_name:
        raise StepFailure("router step failed: Core/router.py returned an empty module_name")

    return RouterResult(
        ticket_id=ticket_id,
        module_name=module_name,
        ticket_details=fetch_result.ticket_details,
    )


def normalize_module_response(response: Any, module_file_name: str) -> tuple[str, str, list[str], dict[str, Any]]:
    if isinstance(response, str):
        normalized_response = response.strip()
        if not normalized_response:
            raise StepFailure(f"module step failed: {module_file_name} returned an empty answer")
        return normalized_response, normalized_response, ["None"], {}

    if not isinstance(response, dict):
        raise StepFailure(
            f"module step failed: {module_file_name} must return a string or a dict with recommendation/body/notes"
        )

    recommendation = str(response.get("recommendation") or "").strip()
    issue_body = str(response.get("body") or "").strip()
    notes_value = response.get("notes")

    if not recommendation:
        raise StepFailure(f"module step failed: {module_file_name} returned an empty recommendation")
    if not issue_body:
        raise StepFailure(f"module step failed: {module_file_name} returned an empty body")

    notes: list[str] = []
    if isinstance(notes_value, list):
        notes = [str(note).strip() for note in notes_value if str(note).strip()]
    elif isinstance(notes_value, str):
        stripped_note = notes_value.strip()
        if stripped_note:
            notes = [stripped_note]
    elif notes_value is not None:
        raise StepFailure(f"module step failed: {module_file_name} returned invalid notes")

    if not notes:
        notes = ["None"]

    module_payload = {
        key: value
        for key, value in response.items()
        if key not in {"recommendation", "body", "notes"}
    }

    return recommendation, issue_body, notes, module_payload


def run_module(router_result: RouterResult) -> ModuleResult:
    if not router_result.ticket_id:
        raise StepFailure("module step failed: missing ticket id from router")

    if not router_result.module_name:
        raise StepFailure("module step failed: missing module name from router")

    module_file_names = {
        "notification": "modules/notifications_module/notification_module.py",
        "general": "modules/general_module.py",
        "orphaned_transaction": "modules/orphaned_module.py",
        "spam": "modules/spam_module/spam_module.py",
        "ringcentral": "modules/ringcentral_module/ringcentral_module.py",
    }

    module_file_name = module_file_names.get(router_result.module_name)
    if not module_file_name:
        raise StepFailure(f"module step failed: unsupported module '{router_result.module_name}'")

    module_path = REPO_ROOT / module_file_name
    if not module_path.exists():
        raise StepFailure(
            f"module step failed: {module_file_name} does not exist yet for module '{router_result.module_name}'"
        )

    module = load_module(module_file_name, f"scd_stage1_{router_result.module_name}_module")
    module_id = str(getattr(module, "MODULE_ID", "")).strip()
    module_version = str(getattr(module, "VERSION", "")).strip()
    if module_id and module_id != router_result.module_name:
        raise StepFailure(
            f"module step failed: {module_file_name} MODULE_ID '{module_id}' does not match '{router_result.module_name}'"
        )

    display_name = build_module_display_name(
        router_result.module_name,
        str(getattr(module, "DISPLAY_NAME", "")).strip(),
        module_version,
    )

    run_fn = getattr(module, "run", None)
    if run_fn is None or not hasattr(run_fn, "__call__"):
        raise StepFailure(f"module step failed: run is missing from {module_file_name}")

    module_response = run_fn.__call__(router_result.ticket_id, router_result.ticket_details)
    recommendation, issue_body, notes, module_payload = normalize_module_response(module_response, module_file_name)

    return ModuleResult(
        ticket_id=router_result.ticket_id,
        module_name=router_result.module_name,
        module_version=module_version,
        module_display_name=display_name,
        recommendation=recommendation,
        issue_body=issue_body,
        notes=notes,
        module_payload=module_payload,
    )


def run_gatekeeper_step(module_result: ModuleResult) -> Any:
    gatekeeper_module = load_module("Core/Gatekeeper.py", "scd_stage1_gatekeeper")
    run_gatekeeper_fn = getattr(gatekeeper_module, "run_gatekeeper", None)
    if run_gatekeeper_fn is None or not hasattr(run_gatekeeper_fn, "__call__"):
        raise StepFailure("gatekeeper step failed: run_gatekeeper is missing from Gatekeeper.py")

    return run_gatekeeper_fn.__call__(
        ticket_id=module_result.ticket_id,
        module_name=module_result.module_name,
        module_version=module_result.module_version,
        recommendation=module_result.recommendation,
        issue_body=module_result.issue_body,
        notes=module_result.notes,
        module_payload=module_result.module_payload,
    )


def build_issue_description(module_result: ModuleResult, gatekeeper_result: Any) -> str:
    module_recommendation = module_result.recommendation.strip()
    if not module_recommendation:
        raise StepFailure("issue_description step failed: module recommendation is empty")

    issue_body = module_result.issue_body.strip()
    if not issue_body:
        raise StepFailure("issue_description step failed: module body is empty")

    gatekeeper_module = load_module("Core/Gatekeeper.py", "scd_stage1_gatekeeper")
    build_gatekeeper_table_fn = getattr(gatekeeper_module, "build_gatekeeper_table", None)
    if build_gatekeeper_table_fn is None or not hasattr(build_gatekeeper_table_fn, "__call__"):
        raise StepFailure("issue_description step failed: build_gatekeeper_table is missing from Gatekeeper.py")

    gatekeeper_table = build_gatekeeper_table_fn.__call__(
        ticket_id=module_result.ticket_id,
        module_name=module_result.module_name,
        module_version=module_result.module_version,
        gatekeeper_result=gatekeeper_result,
    )
    issue_body_with_gatekeeper = f"{gatekeeper_table}\n\n{issue_body}"

    formatted_notes = []
    for note in module_result.notes:
        normalized_note = note.strip()
        if not normalized_note:
            continue
        if normalized_note.startswith("-"):
            formatted_notes.append(normalized_note)
        else:
            formatted_notes.append(f"- {normalized_note}")

    if not formatted_notes:
        formatted_notes = ["- None"]

    transcript_value = str(module_result.module_payload.get("voicemail_transcript") or "").strip()

    lines = [
        f"Recommendation: {module_recommendation}",
        f"Ticket ID: {module_result.ticket_id}",
        f"Module ID: {module_result.module_name}",
        f"Module: {module_result.module_display_name}",
        "Issue Body:",
        issue_body_with_gatekeeper,
        "Notes:",
        *formatted_notes,
    ]

    if transcript_value:
        lines.extend(
            [
                "Word for word transcript:",
                transcript_value,
            ]
        )
    return "\n".join(lines)


def build_module_display_name(module_name: str, display_name: str, version: str) -> str:
    if display_name:
        if version:
            return f"{display_name} {version}"
        return display_name

    fallback_name = module_name.strip().replace("_", " ") or "unknown"
    if version:
        return f"{fallback_name} {version}"
    return fallback_name


def build_failure_issue_description(error_message: str) -> str:
    ticket_id = normalize_scan_ticket_id(os.getenv("SCAN_TICKET_ID", "unknown")) or "unknown"

    lines = [
        "Recommendation: error",
        f"Ticket ID: {ticket_id}",
        "Module ID: unknown",
        "Module: unknown",
        "Issue Body:",
        "No issue body available.",
        "Notes:",
        f"- {error_message}",
    ]
    return "\n".join(lines)


def main() -> int:
    try:
        issue_description = run()
    except StepFailure as exc:
        print(build_failure_issue_description(str(exc)))
        return 1

    print(issue_description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())