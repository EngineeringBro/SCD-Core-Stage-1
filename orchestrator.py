from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Callable


class StepFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    ticket_id: str
    ticket_details: dict[str, Any]


@dataclass(frozen=True)
class RouterResult:
    ticket_id: str
    module_name: str


@dataclass(frozen=True)
class ModuleResult:
    ticket_id: str
    module_name: str
    decision: str


def run() -> str:
    fetch_result = run_step("fetcher", fetch_ticket_details)
    router_result = run_step("router", lambda: route_ticket(fetch_result))
    module_result = run_step("module", lambda: run_module(router_result))
    issue_description = run_step(
        "issue_description",
        lambda: build_issue_description(module_result),
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


def create_jira_read_client() -> Any:
    module_path = Path(__file__).with_name("jira_read.py")
    spec = spec_from_file_location("scd_stage1_jira_read", module_path)
    if spec is None or spec.loader is None:
        raise StepFailure("fetcher step failed: could not load jira_read.py")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    client_class = getattr(module, "JiraReadClient", None)
    if client_class is None:
        raise StepFailure("fetcher step failed: JiraReadClient is missing from jira_read.py")
    if not isinstance(client_class, type):
        raise StepFailure("fetcher step failed: JiraReadClient in jira_read.py is not a class")

    return type.__call__(client_class)


def load_local_module(module_file_name: str, module_label: str) -> Any:
    module_path = Path(__file__).with_name(module_file_name)
    spec = spec_from_file_location(module_label, module_path)
    if spec is None or spec.loader is None:
        raise StepFailure(f"could not load {module_file_name}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_ticket_details() -> FetchResult:
    scan_ticket_id = os.getenv("SCAN_TICKET_ID", "").strip()
    if not scan_ticket_id:
        raise StepFailure("fetcher step failed: SCAN_TICKET_ID is required in Stage 1")

    client = create_jira_read_client()
    issue = client.get_issue(scan_ticket_id)
    comments = client.get_comments(scan_ticket_id)

    if not issue:
        raise StepFailure("fetcher step failed: jira_read.py returned no issue data")

    ticket_id = str(issue.get("key") or scan_ticket_id).strip()
    if not ticket_id:
        raise StepFailure("fetcher step failed: Jira issue key is missing")

    ticket_details = {
        "issue": issue,
        "comments": comments,
    }
    return FetchResult(ticket_id=ticket_id, ticket_details=ticket_details)


def route_ticket(fetch_result: FetchResult) -> RouterResult:
    if not fetch_result.ticket_id:
        raise StepFailure("router step failed: missing ticket id from fetcher")

    if not fetch_result.ticket_details:
        raise StepFailure("router step failed: missing ticket details from fetcher")

    router_module = load_local_module("router.py", "scd_stage1_router")
    route_ticket_fn = getattr(router_module, "route_ticket", None)
    if route_ticket_fn is None or not hasattr(route_ticket_fn, "__call__"):
        raise StepFailure("router step failed: route_ticket is missing from router.py")

    route_result = route_ticket_fn.__call__(fetch_result.ticket_id, fetch_result.ticket_details)
    if not isinstance(route_result, dict):
        raise StepFailure("router step failed: router.py must return a dict")

    ticket_id = str(route_result.get("ticket_id") or "").strip()
    module_name = str(route_result.get("module_name") or "").strip()
    if not ticket_id:
        raise StepFailure("router step failed: router.py returned an empty ticket_id")
    if not module_name:
        raise StepFailure("router step failed: router.py returned an empty module_name")

    return RouterResult(ticket_id=ticket_id, module_name=module_name)


def run_module(router_result: RouterResult) -> ModuleResult:
    if not router_result.ticket_id:
        raise StepFailure("module step failed: missing ticket id from router")

    if not router_result.module_name:
        raise StepFailure("module step failed: missing module name from router")

    module_file_names = {
        "notification": "notification_module.py",
        "spam": "spam_module.py",
        "general": "general_module.py",
        "orphaned": "orphaned_module.py",
    }

    module_file_name = module_file_names.get(router_result.module_name)
    if not module_file_name:
        raise StepFailure(f"module step failed: unsupported module '{router_result.module_name}'")

    module_path = Path(__file__).with_name(module_file_name)
    if not module_path.exists():
        raise StepFailure(
            f"module step failed: {module_file_name} does not exist yet for module '{router_result.module_name}'"
        )

    module = load_local_module(module_file_name, f"scd_stage1_{router_result.module_name}_module")
    run_fn = getattr(module, "run", None)
    if run_fn is None or not hasattr(run_fn, "__call__"):
        raise StepFailure(f"module step failed: run is missing from {module_file_name}")

    decision = run_fn.__call__(router_result.ticket_id)
    normalized_decision = str(decision).strip()
    if not normalized_decision:
        raise StepFailure(f"module step failed: {module_file_name} returned an empty answer")

    return ModuleResult(
        ticket_id=router_result.ticket_id,
        module_name=router_result.module_name,
        decision=normalized_decision,
    )


def build_issue_description(module_result: ModuleResult) -> str:
    module_answer = module_result.decision.strip()
    if not module_answer:
        raise StepFailure("issue_description step failed: module answer is empty")

    lines = [
        f"Issue Description: {module_answer}",
        "",
        f"Module Output: {module_answer}",
        f"Ticket ID: {module_result.ticket_id}",
        f"Selected Module: {module_result.module_name}",
    ]
    return "\n".join(lines)


def main() -> int:
    try:
        issue_description = run()
    except StepFailure as exc:
        print(str(exc))
        return 1

    print(issue_description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())